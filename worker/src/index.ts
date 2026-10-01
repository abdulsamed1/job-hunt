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
import { fetchArbeitnow, fetchJobicy, fetchLinkedInGuest, fetchRemotive, fetchRemoteOK } from "./adapters/boards.js";
import { fetchAshbyBoard, fetchGreenhouseBoard, fetchLeverBoard, fetchSmartRecruitersBoard } from "./adapters/ats.js";
import type { RawJob } from "./adapters/http.js";
import { canonicalHash, normalizeUrl, roleFingerprint } from "./lib/hash.js";
import { filterRecentList, filterRemoteList, isFresh, isRemoteish, parsePostedAt } from "./lib/freshness.js";
import { applyToAts, buildTailoredText, evaluateJob, renderPdfBytes } from "./stages/pipeline.js";
import type { Profile } from "./lib/evaluate.js";
import { getJobsByState, saveApplication, saveEvaluation, setJobState, upsertJob, type Db, type JobState } from "./state.js";

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
  LIVE_APPLY?: string;
  APPROVE_GREENHOUSE?: string;
  APPROVE_LEVER?: string;
  PROFILE_JSON?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  REZI_MCP_TOKEN?: string;
}

interface SourceDef {
  kind: "rss" | "remoteok" | "remotive" | "arbeitnow" | "jobicy" | "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "linkedin";
  name: string;
  url?: string;
  org?: string;
  queries?: string[];
  locations?: string[];
  cadence: "hourly" | "6h";
}

// Cheap + high-signal subset runs hourly; the deep set runs on the daily sweep.
const SOURCES: SourceDef[] = [
  { kind: "rss", name: "cryptojobslist", url: "https://api.cryptojobslist.com/jobs.rss", cadence: "hourly" },
  { kind: "rss", name: "crypto.jobs", url: "https://crypto.jobs/feed/rss", cadence: "hourly" },
  { kind: "rss", name: "weworkremotely", url: "https://weworkremotely.com/remote-jobs.rss", cadence: "hourly" },
  { kind: "rss", name: "remote3", url: "https://remote3.co/api/rss", cadence: "hourly" },
  { kind: "rss", name: "bitcoinjobs", url: "https://bitbo.io/jobs/feed.xml", cadence: "hourly" },
  { kind: "remoteok", name: "remoteok", cadence: "hourly" },
  { kind: "remotive", name: "remotive", cadence: "hourly" },
  { kind: "arbeitnow", name: "arbeitnow", cadence: "hourly" },
  { kind: "jobicy", name: "jobicy", cadence: "hourly" },
  { kind: "greenhouse", name: "coinbase-board", org: "coinbase", cadence: "hourly" },
  { kind: "greenhouse", name: "ripple-board", org: "ripple", cadence: "hourly" },
  { kind: "greenhouse", name: "stripe-board", org: "stripe", cadence: "6h" },
  { kind: "greenhouse", name: "consensys-board", org: "consensys", cadence: "6h" },
  { kind: "greenhouse", name: "elastic-board", org: "elastic", cadence: "6h" },
  { kind: "ashby", name: "stellar-board", org: "stellar", cadence: "hourly" },
  { kind: "ashby", name: "uniswap-board", org: "uniswap", cadence: "6h" },
  { kind: "ashby", name: "opensea-board", org: "opensea", cadence: "6h" },
  { kind: "ashby", name: "alchemy-board", org: "alchemy", cadence: "6h" },
  { kind: "linkedin", name: "linkedin_geo_recent", queries: ["backend", "fullstack", "software"], locations: ["Remote"], cadence: "6h" },
];

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
    case "linkedin": {
      const out: RawJob[] = [];
      for (const q of def.queries || ["backend"]) {
        out.push(...(await fetchLinkedInGuest(q, (def.locations || ["Remote"])[0], 1, parseInt(env.TPR_SECONDS || "43200", 10))));
      }
      return out;
    }
  }
}

export default {
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Hourly scout vs daily deep sweep selected by cron expression.
    const deep = event.cron === "0 2 * * *";
    const defs = SOURCES.filter((s) => deep || s.cadence === "hourly" || s.kind === "linkedin");
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
    for (const msg of batch.messages) {
      try {
        const m = msg.body as any;
        if (m.stage === "discover" && m.def) {
          const jobs = await discoverSource(m.def, env);
          const fresh = filterRecentList(
            jobs.map((j) => ({ ...j, posted_at: j.posted_at })),
            24,
          );
          const remote = filterRemoteList(fresh.map((j) => ({ ...j, location: j.location, description: j.desc, remote_flag: undefined })));
          const now = new Date().toISOString();
          for (const j of remote.slice(0, 40)) {
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
          if (r.eligible) {
            await env.APPLY_Q.send({ stage: "tailor-apply", hash: m.hash });
          }
        } else if (m.stage === "tailor-apply" && m.hash) {
          const row: any = await db.prepare(
            `SELECT canonical_hash, title, company, location, url, source, description FROM jobs WHERE canonical_hash = ?`,
          ).bind(m.hash).first();
          if (!row || !row.url) continue;
          await setJobState(db, m.hash, "TAILORED", "worker tailor");
          const text = buildTailoredText({ title: row.title, company: row.company }, profile);
          const pdf = await renderPdfBytes(text, profile.fullName);
          const key = `cvs/${m.hash}.pdf`;
          await env.CV_BUCKET.put(key, pdf, {
            httpMetadata: { contentType: "application/pdf" },
          });
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
              resumeBytes: pdf, resumeFilename: "cv.pdf",
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
      return Response.json({ ok: true, jobs: row?.n ?? 0 });
    }
    if (url.pathname === "/recent") {
      const rows = await getJobsByState(db, "ELIGIBLE", 50);
      const latest: any = await db.prepare(
        `SELECT title, company, location, url, source, score, state, discovered_at FROM jobs ORDER BY discovered_at DESC LIMIT 50`,
      ).bind().all();
      return Response.json({ eligible: rows, latest: latest.results });
    }
    if (url.pathname === "/queue" && request.method === "POST") {
      const body: any = await request.json().catch(() => ({}));
      const count = Math.min(50, Math.max(1, body.count || 10));
      const rows = await getJobsByState(db, body.state || "ELIGIBLE", count);
      return Response.json({ jobs: rows });
    }
    if (url.pathname === "/run" && request.method === "POST") {
      await env.DISCOVER_Q.send({ stage: "discover", def: SOURCES[0] });
      return Response.json({ queued: true });
    }
    return new Response("jobhunt worker: /health /recent /queue /run", { status: 200 });
  },
};
