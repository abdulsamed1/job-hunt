/**
 * jobhunt full pipeline on Cloudflare Workers (free tier).
 *
 * Limits respected (verified against docs 2026-10-01):
 * - ≤50 subrequests/invocation → one source / one job per queue message
 * - 10ms CPU/invocation → fetch (I/O is free) + small parses + few D1 ops only
 * - 5 cron triggers/account → exactly 2 used (hourly scout, daily deep sweep)
 * - D1 ≤50 queries/invocation → single statements, indexed lookups
 * - No browser anywhere: ATS applies are direct HTTP POSTs (greenhouse/lever);
 *   LinkedIn submits + CAPTCHA jobs stay human-queued.
 */

import { fetchRssFeed } from "./adapters/rss.js";
import { fetchArbeitnow, fetchBdJobs, fetchJobicy, fetchJsonSearchBoard, fetchLinkedInGuest, fetchRemotive, fetchRemoteOK } from "./adapters/boards.js";
import { fetchPersonio, fetchRecruitee, fetchTeamtailor } from "./adapters/tenants.js";
import { enrichDescriptions, fetchAshbyBoard, fetchAshbyIndex, fetchGreenhouseBoard, fetchIndeed, fetchLeverBoard, fetchSmartRecruitersBoard } from "./adapters/ats.js";
import { GENERATED_SOURCES, PYTHON_ONLY_SOURCES } from "./sources.generated.js";
import type { SourceDef } from "./sources.js";
import type { RawJob } from "./adapters/http.js";
import { canonicalHash, normalizeUrl, roleFingerprint } from "./lib/hash.js";
import { boardUrlForDef, classifyHost } from "./lib/hosts.js";
import { detectDegraded, isInconclusiveProbeError } from "./lib/health.js";
import { filterRecentList, filterRemoteList, isFresh, isRemoteish, parsePostedAt } from "./lib/freshness.js";
import { applyToAts, evaluateJob } from "./stages/pipeline.js";
import { completeJson } from "./lib/llm.js";
import type { Profile } from "./lib/evaluate.js";
import { ensureSourceHealthColumns, getActionableJobs, getJobsByState, recordSourceHealth, saveApplication, saveEvaluation, setJobState, upsertJob, type Db, type JobState } from "./state.js";

export interface Env {
  DB: D1Database;
  CV_BUCKET: R2Bucket;
  DISCOVER_Q: Queue;
  EVALUATE_Q: Queue;
  APPLY_Q: Queue;
  QUERIES?: string;
  TPR_SECONDS?: string;
  THRESHOLD?: string;
  REQUIRE_LINKEDIN_APPROVAL?: string;
  LINKEDIN_ENABLED?: string;
  LIVE_APPLY?: string;
  APPROVE_GREENHOUSE?: string;
  APPROVE_LEVER?: string;
  MASTER_RESUME_SHA?: string;
  LLM_RATIONALE?: string;
  PROFILE_JSON?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  REZI_MCP_TOKEN?: string;
}

const SOURCES: SourceDef[] = GENERATED_SOURCES;

export function linkedinEnabled(env: Env): boolean {
  return (env.LINKEDIN_ENABLED || "false").toLowerCase() === "true";
}

export function activeSources(env: Env): SourceDef[] {
  return linkedinEnabled(env) ? SOURCES : SOURCES.filter((s) => s.kind !== "linkedin");
}

function profileFromEnv(env: Env): Profile & { fullName: string; email: string; phone: string; summary?: string } {
  try {
    const p = JSON.parse(env.PROFILE_JSON || "{}");
    return {
      verifiedSkills: p.verified_skills || [],
      yearsOfExperience: p.years_of_experience ?? 5,
      openToRemote: p.open_to_remote ?? true,
      sponsorshipRequired: p.sponsorship_required ?? false,
      citizenship: p.citizenship ?? null,
      authorizedCountries: p.authorized_countries || [],
      blockedCompanies: p.blocked_companies || [],
      blockedKeywords: p.blocked_keywords || [],
      preferredKeywords: p.preferred_keywords || [],
      location: p.location || "",
      languages: p.languages || [],
      fullName: p.full_name || "",
      email: p.email || "",
      phone: p.phone || "",
      summary: p.summary,
    };
  } catch {
    return {
      verifiedSkills: [], yearsOfExperience: 5, openToRemote: true,
      sponsorshipRequired: false, citizenship: null, authorizedCountries: [],
      blockedCompanies: [], blockedKeywords: [], preferredKeywords: [],
      location: "", languages: [], fullName: "", email: "", phone: "",
    };
  }
}

async function alert(env: Env, text: string): Promise<void> {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: text.slice(0, 3000) }),
  });
}

async function discoverSource(def: SourceDef, env: Env): Promise<RawJob[]> {
  switch (def.kind) {
    case "rss": return fetchRssFeed(def.name, def.url || "");
    case "remoteok": return fetchRemoteOK();
    case "remotive": return fetchRemotive();
    case "arbeitnow": return fetchArbeitnow();
    case "jobicy": return fetchJobicy();
    case "greenhouse": return fetchGreenhouseBoard(def.org || "");
    case "lever": return fetchLeverBoard(def.org || "");
    case "ashby": return fetchAshbyBoard(def.org || "");
    case "smartrecruiters": return fetchSmartRecruitersBoard(def.org || "");
    case "ashby-index": return fetchAshbyIndex(def.url || "", def.maxOrgs || 8);
    case "indeed": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        out.push(...(await fetchIndeed({ query, location: (def.locations || ["Cairo, Egypt"])[0], country: def.country })));
      }
      return out;
    }
    case "freehire": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        for (const location of def.locations || ["Remote"]) {
          out.push(...(await fetchJsonSearchBoard({
            searchUrl: def.url || "", source: def.kind, query, location,
          })));
        }
      }
      return out;
    }
    case "bdjobs": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        for (const location of def.locations || ["Bangladesh"]) {
          out.push(...(await fetchBdJobs({
            searchUrl: def.url || "", query, location,
            hoursOld: def.hoursOld ?? 24,
          })));
        }
      }
      return out;
    }
    case "teamtailor": return fetchTeamtailor(def.url || "", def.name);
    case "recruitee": return fetchRecruitee(def.url || "", def.name);
    case "personio": return fetchPersonio(def.url || "", def.name);
    case "linkedin": {
      if (!linkedinEnabled(env)) return [];
      const out: RawJob[] = [];
      for (const q of def.queries || ["backend"]) {
        out.push(...(await fetchLinkedInGuest(q, (def.locations || ["Remote"])[0], 1, def.tprSeconds || parseInt(env.TPR_SECONDS || "43200", 10))));
      }
      return out;
    }
  }
}

export default {
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Hourly scout vs daily deep sweep selected by cron expression.
    const deep = event.cron === "0 2 * * *";
    const defs = activeSources(env).filter((s) => deep || s.cadence === "hourly" || s.kind === "linkedin");
    ctx.waitUntil(
      (async () => {
        const batch: Record<string, unknown>[] = defs.map((d) => ({ stage: "discover", def: d }));
        // Queue batch cap is 100; shard defensively.
        for (let i = 0; i < batch.length; i += 50) {
          await env.DISCOVER_Q.sendBatch(batch.slice(i, i + 50).map((body) => ({ body })));
        }
      })(),
    );
  },

  async queue(batch: MessageBatch<Record<string, unknown>>, env: Env): Promise<void> {
    const db = env.DB as unknown as Db;
    const profile = profileFromEnv(env);
    const threshold = parseFloat(env.THRESHOLD || "70");
    // Migrated once per batch, not once per health write (recordSourceHealth
    // no longer ensures columns itself).
    await ensureSourceHealthColumns(db);
    for (const msg of batch.messages) {
      try {
        const m = msg.body as any;
        if (m.stage === "discover" && m.def) {
          const def = m.def as SourceDef;
          // Fail closed on spoofed ATS hosts AND smuggled orgs: the board URL
          // is rebuilt from def.org exactly as discoverSource() builds it, so
          // a queue message carrying a hostile org or URL never reaches fetch.
          // (No workday/workable/bamboohr board kinds exist on the Worker —
          // see sources.ts — so the kind list is intentionally unchanged.)
          if (
            def.kind === "greenhouse" || def.kind === "lever" ||
            def.kind === "ashby" || def.kind === "smartrecruiters"
          ) {
            const boardUrl = boardUrlForDef(def);
            if (!boardUrl || classifyHost(boardUrl) === "unverified") {
              console.log(`skipping source with unverified board URL: ${def.name}`);
              continue;
            }
          }
          let jobs: RawJob[] = [];
          let healthRecorded = false;
          try {
            jobs = await discoverSource(m.def, env);
          } catch (e) {
            await recordSourceHealth(db, m.def.name, m.def.kind || "", -1, -1, String(e).slice(0, 180));
            healthRecorded = true;
            throw e;
          }
          const fresh = filterRecentList(
            jobs.map((j) => ({ ...j, posted_at: j.posted_at })),
            12,
          );
          const remote = filterRemoteList(fresh.map((j) => ({ ...j, location: j.location, description: j.desc, remote_flag: undefined })));
          const now = new Date().toISOString();
          // Board APIs omit descriptions; enrich the few survivors so scoring
          // has real evidence instead of a title-only string.
          const enriched = await enrichDescriptions(remote.slice(0, 40));
          const degraded = detectDegraded(remote, m.def.url || "", m.def.kind || "");
          if (!healthRecorded) {
            await recordSourceHealth(db, m.def.name, m.def.kind || "", jobs.length, remote.length, "", degraded);
            healthRecorded = true;
          }
          for (const j of enriched) {
            const canon = normalizeUrl(j.url);
            const isNew = await upsertJob(db, {
              canonical_hash: canonicalHash(canon), title: j.title.slice(0, 200),
              company: j.company.slice(0, 120), location: j.location.slice(0, 120),
              url: j.url.slice(0, 500), description: (j.desc || "").slice(0, 4000),
              source: j.source, posted_at: (j.posted_at || "").slice(0, 40),
              discovered_at: now, score: 0, remote: isRemoteish(j.location, j.desc),
            });
            if (isNew) {
              await env.EVALUATE_Q.send({ stage: "evaluate", hash: canonicalHash(canon) });
            }
          }
        } else if (m.stage === "evaluate" && m.hash) {
          const row: any = await db.prepare(
            `SELECT canonical_hash, title, company, location, url, description, source FROM jobs WHERE canonical_hash = ?`,
          ).bind(m.hash).first();
          if (!row) continue;
          const r = evaluateJob(
            { title: row.title, description: row.description || "", location: row.location },
            profile, threshold,
          );
          await saveEvaluation(db, m.hash, r.score, r.eligible, r.reasoning);
          if (env.LLM_RATIONALE === "true" && r.eligible) {
            try {
              const out = await completeJson(env, db, {
                messages: [
                  { role: "system", content: "Summarize in one sentence why this candidate fits. JSON only: {\"rationale\": \"...\"}. Never invent facts." },
                  { role: "user", content: `${row.title} @ ${row.company}. Matched: ${(r.matched || []).join(", ")}` },
                ],
                maxTokens: 120, purpose: "rationale",
              });
              const rationale = ((out.obj.rationale || "") as string).slice(0, 200).trim();
              if (rationale) {
                await db.prepare(`UPDATE evaluations SET reasoning = reasoning || ? WHERE job_hash = ?`).bind(` | llm: ${rationale}`, m.hash).run();
              }
            } catch { /* rationale is optional; deterministic score stands */ }
          }
          if (r.eligible) {
            await env.APPLY_Q.send({ stage: "tailor-apply", hash: m.hash });
          }
        } else if (m.stage === "tailor-apply" && m.hash) {
          const row: any = await db.prepare(
            `SELECT canonical_hash, title, company, location, url, source, description FROM jobs WHERE canonical_hash = ?`,
          ).bind(m.hash).first();
          if (!row || !row.url) continue;
          await setJobState(db, m.hash, "TAILORED", "worker tailor");
          const sha = (env.MASTER_RESUME_SHA || "").trim();
          if (!sha) {
            await saveApplication(db, m.hash, "FAILED", "resume bytes missing: MASTER_RESUME_SHA secret not set (cvs/master/<sha>.pdf unreachable)", null);
            continue;
          }
          // Master-only until per-job Rezi sync lands (see follow-up): no D1 column or artifact sync exists yet
          const key = `cvs/master/${sha}.pdf`;
          const obj = await env.CV_BUCKET.get(key);
          if (!obj) {
            await saveApplication(db, m.hash, "FAILED", `resume bytes missing: ${key}`, null);
            continue;
          }
          const resumeBytes = new Uint8Array(await obj.arrayBuffer());
          // Route ATS boards to direct HTTP apply; everything else waits human.
          const src: string = row.source || "";
          const live = env.LIVE_APPLY === "true";
          let board: { kind: "greenhouse" | "lever" | "ashby"; org: string; jobId: string } | null = null;
          let gh = src.match(/^greenhouse:(.+)$/);
          let lv = src.match(/^lever:(.+)$/);
          let ab = src.match(/^ashby:(.+)$/);
          const idFromUrl = (row.url.match(/jobs\/(\d+)/) || [])[1] || row.url.split("/").filter(Boolean).pop() || "";
          if (gh) board = { kind: "greenhouse", org: gh[1], jobId: idFromUrl };
          else if (lv) board = { kind: "lever", org: lv[1], jobId: idFromUrl };
          else if (ab) board = { kind: "ashby", org: ab[1], jobId: idFromUrl };
          const approved =
            (board?.kind === "greenhouse" && env.APPROVE_GREENHOUSE === "true") ||
            (board?.kind === "lever" && env.APPROVE_LEVER === "true");
          if (!board) {
            await saveApplication(db, m.hash, "APPLICATION_STARTED", "non-ATS board: queued for human review", key);
            continue;
          }
          const names = profile.fullName.split(/\s+/);
          const result = await applyToAts(
            board,
            {
              firstName: names[0] || "", lastName: names.slice(1).join(" ") || "",
              email: profile.email, phone: profile.phone,
              resumeBytes: resumeBytes, resumeFilename: "cv.pdf",
            },
            { dryRun: !live, approved },
          );
          await saveApplication(
            db, m.hash, result.ok && live && approved ? "SUBMITTED" : live ? "FAILED" : "APPLICATION_STARTED",
            result.detail, key,
          );
        }
      } catch (e) {
        console.log(`pipeline message failed: ${String(e).slice(0, 200)}`);
      }
    }
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const db = env.DB as unknown as Db;
    if (url.pathname === "/health") {
      const row: any = await db.prepare(`SELECT COUNT(*) AS n FROM jobs`).first();
      // Profile sanity: an empty verified_skills list silently rejects every job.
      const prof = profileFromEnv(env);
      return Response.json({
        ok: true,
        jobs: row?.n ?? 0,
        profile_skills: (prof.verifiedSkills || []).length,
        profile_languages: (prof.languages || []).length,
        profile_has_name: !!prof.fullName,
        threshold: parseFloat(env.THRESHOLD || "70"),
        live_apply: env.LIVE_APPLY === "true",
      });
    }
    if (url.pathname === "/recent") {
      // `actionable` is the morning list: qualifying jobs are moved out of the
      // ELIGIBLE state once tailored, so querying ELIGIBLE alone always looked empty.
      const actionable = await getActionableJobs(db, 50);
      const rows = actionable.filter((r: any) => r.state === "ELIGIBLE");
      const latest: any = await db.prepare(
        `SELECT title, company, location, url, source, score, state, discovered_at FROM jobs ORDER BY discovered_at DESC LIMIT 50`,
      ).bind().all();
      return Response.json({ actionable, eligible: rows, latest: latest.results });
    }
    if (url.pathname === "/queue" && request.method === "POST") {
      const body: any = await request.json().catch(() => ({}));
      const count = Math.min(50, Math.max(1, body.count || 10));
      const rows = await getJobsByState(db, body.state || "ELIGIBLE", count);
      return Response.json({ jobs: rows });
    }
    if (url.pathname === "/coverage") {
      // Measured coverage: what actually answered, vs. what is configured.
      const rows: any = await db.prepare(
        `SELECT COUNT(*) AS configured, SUM(CASE WHEN yields > 0 THEN 1 ELSE 0 END) AS yielding,
                SUM(attempts) AS attempts, MAX(last_seen) AS last_seen
         FROM source_health`,
      ).bind().first();
      const byKind: any = await db.prepare(
        `SELECT kind, COUNT(*) AS sources, SUM(CASE WHEN yields > 0 THEN 1 ELSE 0 END) AS yielding
         FROM source_health GROUP BY kind ORDER BY sources DESC`,
      ).bind().all();
      return Response.json({
        configured_worker_sources: SOURCES.length,
        python_only_sources: PYTHON_ONLY_SOURCES.length,
        sources_attempted: rows?.configured ?? 0,
        sources_yielding: rows?.yielding ?? 0,
        total_attempts: rows?.attempts ?? 0,
        last_seen: rows?.last_seen ?? null,
        by_kind: byKind.results,
      });
    }
    if (url.pathname === "/sources") {
      const byKind: Record<string, number> = {};
      for (const s of SOURCES) byKind[s.kind] = (byKind[s.kind] || 0) + 1;
      return Response.json({
        worker_sources: SOURCES.length,
        python_only_sources: PYTHON_ONLY_SOURCES.length,
        hourly_shard: SOURCES.filter((s) => s.cadence === "hourly").length,
        by_kind: byKind,
      });
    }
    if (url.pathname === "/probe" && request.method === "POST") {
      // Read-only yield probe: runs one source's fetch + filters, writes nothing.
      const body: any = await request.json().catch(() => ({}));
      const def = SOURCES.find((s) => s.name === body.name);
      if (!def) return Response.json({ error: "unknown source" }, { status: 404 });
      try {
        const jobs = await discoverSource(def, env);
        const fresh = filterRecentList(jobs.map((j) => ({ ...j, posted_at: j.posted_at })), 12);
        const remote = filterRemoteList(
          fresh.map((j) => ({ ...j, location: j.location, description: j.desc, remote_flag: undefined })),
        );
        return Response.json({
          name: def.name, kind: def.kind, raw: jobs.length, fresh_12h: fresh.length, remote_fresh: remote.length,
          degraded: detectDegraded(remote, def.url || "", def.kind || ""),
          sample: remote.slice(0, 3).map((j) => ({ title: j.title, company: j.company, url: j.url })),
        });
      } catch (e) {
        // Transport/rate-limit failures are inconclusive (HTTP 200): the board
        // may be throttling or unreachable, which says nothing about rot.
        // 404 stays reserved for unknown source names above.
        const error = String(e).slice(0, 200);
        if (isInconclusiveProbeError(e)) {
          return Response.json({ name: def.name, kind: def.kind, inconclusive: true, error });
        }
        return Response.json({ name: def.name, kind: def.kind, error }, { status: 502 });
      }
    }
    if (url.pathname === "/run" && request.method === "POST") {
      // Fan out the WHOLE shard (hourly subset unless deep=1), one source per message.
      const deep = url.searchParams.get("deep") === "1";
      const defs = activeSources(env).filter((s) => deep || s.cadence === "hourly" || s.kind === "linkedin");
      const batch: Record<string, unknown>[] = defs.map((d) => ({ stage: "discover", def: d }));
      for (let i = 0; i < batch.length; i += 50) {
        await env.DISCOVER_Q.sendBatch(batch.slice(i, i + 50).map((body) => ({ body })));
      }
      return Response.json({ queued: true, sources: defs.length, deep });
    }
    return new Response("jobhunt worker: /health /recent /sources /coverage /queue /run /probe", { status: 200 });
  },
};
