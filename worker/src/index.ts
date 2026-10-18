/**
 * jobhunt full pipeline on Cloudflare Workers (free tier).
 *
 * Limits respected (verified against docs 2026-10-01):
 * - ≤50 subrequests/invocation → one source / one job per queue message
 * - 10ms CPU/invocation → fetch (I/O is free) + small parses + few D1 ops only
 * - 5 cron triggers/account → exactly 2 used (hourly scout, daily deep sweep)
 * - D1 ≤50 queries/invocation → single statements, indexed lookups; the
 *   notification ledger costs exactly one INSERT per event (no extra
 *   per-job flag write), and migrates once per queue batch
 * - No browser anywhere: ATS applies are direct HTTP POSTs (greenhouse/lever);
 *   LinkedIn submits + CAPTCHA jobs stay human-queued.
 */

import { GENERATED_SOURCES, PYTHON_ONLY_SOURCES } from "./sources.generated.js";
import type { SourceDef } from "./sources.js";
import { detectDegraded, isInconclusiveProbeError } from "./lib/health.js";
import { filterRecentList, filterRemoteList, HOURS_OLD, parsePostedAt } from "./lib/freshness.js";
import { applyToAts, evaluateJob } from "./stages/pipeline.js";
import { discoverSource, linkedinEnabled, NO_DATE_DETAIL, runDiscoverStage } from "./stages/discover.js";
import { completeJson } from "./lib/llm.js";
import { checkDisabilityTargetingGate, type Profile } from "./lib/evaluate.js";
import { ensureCounters, ensureNotificationEvents, ensureRoleFingerprint, ensureSettings, ensureSourceHealthColumns, getActionableJobs, getJobsByState, saveApplication, saveEvaluation, setJobState, type Db, type JobState } from "./state.js";
import { currentPointer, handleTelegramUpdate } from "./lib/webhook.js";
import { classifyRole } from "./lib/roles.js";

// Stage behaviour, still exported from here: index is the module the source
// selectors and the tests import from, and the LinkedIn kill switch and the ATS
// host guard belong to the stage that uses them.
export { linkedinEnabled, queueGuardAllows } from "./stages/discover.js";

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
  REJECT_CRM_ROLES?: string;
  LIVE_APPLY?: string;
  APPROVE_GREENHOUSE?: string;
  APPROVE_LEVER?: string;
  MASTER_RESUME_SHA?: string;
  LLM_RATIONALE?: string;
  PROFILE_JSON?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  TELEGRAM_WEBHOOK_SECRET?: string;
  REZI_MCP_TOKEN?: string;
}

const SOURCES: SourceDef[] = GENERATED_SOURCES;

export function activeSources(env: Env): SourceDef[] {
  const base = SOURCES.filter((s) => s.status !== "dead");
  return linkedinEnabled(env) ? base : base.filter((s) => s.kind !== "linkedin");
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

import { notifyEventOnce, type WorkerEvent } from "./lib/notify.js";

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
    // CRM implementation roles are a Worker var, not a profile field (Decision
    // 10). Absent means on, so a deploy missing the var still filters them.
    const rejectCrm = env.REJECT_CRM_ROLES !== "false";
    // Migrated once per batch, not once per write (recordSourceHealth no
    // longer ensures columns itself; the notification ledger is created on
    // databases that predate it).
    await ensureSourceHealthColumns(db);
    await ensureNotificationEvents(db);
    await ensureSettings(db);
    await ensureCounters(db);
    await ensureRoleFingerprint(db);
    for (const msg of batch.messages) {
      let body: any = null;
      try {
        const m = msg.body as any;
        body = m;
        if (m.stage === "discover" && m.def) {
          await runDiscoverStage(m, db, env);
        } else if (m.stage === "evaluate" && m.hash) {
          // A suppressed twin reads as absent: a message queued before the
          // dedupe gate landed can never be promoted into an application.
          const row: any = await db.prepare(
            `SELECT canonical_hash, title, company, location, url, description, source FROM jobs
             WHERE canonical_hash = ? AND state <> 'DUPLICATE'`,
          ).bind(m.hash).first();
          if (!row) continue;
          const r = evaluateJob(
            { title: row.title, description: row.description || "", location: row.location },
            profile, threshold, { rejectCrm },
          );
          if (r.gated) {
            // Disability/eligibility/language/role pre-filter: evaluated and
            // recorded straight into PRE_FILTERED_OUT (one state write, one
            // audit row), never queued for apply.
            await saveEvaluation(db, m.hash, r.score, r.eligible, r.reasoning, "PRE_FILTERED_OUT");
            await notifyEventOnce(db, env, "worker", m.hash, "FILTERED", row, r.reasoning);
            continue;
          }
          await saveEvaluation(db, m.hash, r.score, r.eligible, r.reasoning);
          if (r.eligible) {
            await notifyEventOnce(db, env, "worker", m.hash, "ELIGIBLE", row, `Score ${r.score}: ${r.reasoning}`);
            if (env.LLM_RATIONALE === "true") {
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
            await env.APPLY_Q.send({ stage: "tailor-apply", hash: m.hash });
          } else {
            await notifyEventOnce(db, env, "worker", m.hash, "FILTERED", row, `Score ${r.score}: ${r.reasoning}`);
          }
        } else if (m.stage === "tailor-apply" && m.hash) {
          const row: any = await db.prepare(
            `SELECT canonical_hash, title, company, location, url, source, description, score, posted_at FROM jobs WHERE canonical_hash = ?`,
          ).bind(m.hash).first();
          if (!row || !row.url) continue;
          // Application-time disability re-check: rows can predate the gate
          // or gain richer descriptions after evaluation.
          const regate = checkDisabilityTargetingGate(row.title || "", row.description || "");
          if (regate.verdict === "FAIL") {
            const regateDetail = `disability gate re-check: ${regate.quote || "targeting phrase"}`.slice(0, 200);
            await setJobState(db, m.hash, "PRE_FILTERED_OUT", regateDetail);
            await notifyEventOnce(db, env, "worker", m.hash, "FILTERED", row, regateDetail);
            continue;
          }
          // Apply-time window + role re-check. The window is re-read here, not
          // trusted from discovery: a posting can sit in the queue across the
          // window's edge (the deep sweep is daily, so an ELIGIBLE row can be
          // hours old by the time its tailor-apply message is read), and a row
          // stored before the window existed may never have had a date at all.
          // The role is re-read for the same reason the disability gate is:
          // the board can retitle a posting after it was stored. Mirrors the
          // re-check above — set state, alert FILTERED, continue — so a posting
          // that aged or drifted out never reaches an employer.
          const postedAt = parsePostedAt(row.posted_at);
          const roleRegate = classifyRole(row.title || "", { rejectCrm });
          let staleDetail = "";
          if (roleRegate.verdict === "reject") staleDetail = `role gate: ${roleRegate.rule}`;
          else if (postedAt === null) staleDetail = NO_DATE_DETAIL;
          else if (postedAt < Date.now() - HOURS_OLD * 3_600_000) staleDetail = `older than ${HOURS_OLD}h`;
          if (staleDetail) {
            const applyDetail = staleDetail.slice(0, 200);
            await setJobState(db, m.hash, "PRE_FILTERED_OUT", applyDetail);
            await notifyEventOnce(db, env, "worker", m.hash, "FILTERED", row, applyDetail);
            continue;
          }
          await setJobState(db, m.hash, "TAILORED", "worker tailor");
          await notifyEventOnce(db, env, "worker", m.hash, "TAILORED", row, "worker tailor");
          // Event-level alerts carry the score in the detail (the compat
          // wrapper's backfill lives here now that callers use notifyEventOnce).
          const scored = (detail: string) =>
            typeof row.score === "number" ? `(score ${row.score})${detail ? ` ${detail}` : ""}` : detail;
          const ptr = await currentPointer(db, env);
          const sha = ptr.sha;
          const failDetailSha = "resume bytes missing: master resume unset (settings.master_resume_sha / MASTER_RESUME_SHA)";
          if (!sha) {
            await saveApplication(db, m.hash, "FAILED", failDetailSha, null);
            if (await notifyEventOnce(db, env, "worker", m.hash, "FAILED", row, scored(failDetailSha)) !== "sent") console.log(`telegram notify failed for ${m.hash} (FAILED)`);
            continue;
          }
          // Decided (2026-10-08): Worker is master-only BY DESIGN, not pending.
          // Rezi's API has no download/export endpoint, so no sync could move
          // bytes; per-job Rezi variants live on the Python/browser side only.
          const key = `cvs/master/${sha}.pdf`;
          const obj = await env.CV_BUCKET.get(key);
          if (!obj) {
            const failDetail = `resume bytes missing: ${key}`;
            await saveApplication(db, m.hash, "FAILED", failDetail, null);
            if (await notifyEventOnce(db, env, "worker", m.hash, "FAILED", row, scored(failDetail)) !== "sent") console.log(`telegram notify failed for ${m.hash} (FAILED)`);
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
            const queuedDetail = "non-ATS board: queued for human review";
            await saveApplication(db, m.hash, "APPLICATION_STARTED", queuedDetail, key);
            if (await notifyEventOnce(db, env, "worker", m.hash, "APPLICATION_ATTEMPT", row, scored(queuedDetail)) !== "sent") console.log(`telegram notify skipped/failed for ${m.hash} (APPLICATION_ATTEMPT)`);
            continue;
          }
          await notifyEventOnce(db, env, "worker", m.hash, "APPLICATION_ATTEMPT", row, scored("application attempt started"));
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
          const submitted = result.ok && live && approved;
          const finalState: JobState = submitted ? "SUBMITTED" : live ? "FAILED" : "APPLICATION_STARTED";
          // The terminal event is distinct from the attempt event so it is
          // never deduped away, and dry runs report their real outcome.
          const terminalEvent: WorkerEvent = submitted ? "SUBMITTED" : live ? "FAILED" : "DRY_RUN";
          await saveApplication(db, m.hash, finalState, result.detail, key);
          const notified = await notifyEventOnce(db, env, "worker", m.hash, terminalEvent, row, scored(result.detail));
          if (notified !== "sent") console.log(`telegram notify ${notified} for ${m.hash} (${terminalEvent})`);
        }
      } catch (e) {
        console.log(`pipeline message failed: ${String(e).slice(0, 200)}`);
        if (body?.hash) {
          // Fixed reason: raw errors may carry secrets or oversized text.
          // Best-effort job context so the alert still names the role. The
          // message is acknowledged (not retried), so the event is
          // STAGE_FAILED rather than any kind of retry notice.
          const STAGE_FAILED_DETAIL = "queued stage failed; see worker logs";
          let retryRow: Record<string, string | null> = {};
          let retryDetail = `${STAGE_FAILED_DETAIL} (job ${String(body.hash).slice(0, 8)})`;
          try {
            const found: any = await db.prepare(
              `SELECT title, company, url FROM jobs WHERE canonical_hash = ?`,
            ).bind(body.hash).first();
            if (found && (found.title || found.company || found.url)) {
              retryRow = { title: found.title ?? null, company: found.company ?? null, url: found.url ?? null };
              retryDetail = STAGE_FAILED_DETAIL;
            }
          } catch { /* lookup is best-effort; fall back to hash-only detail */ }
          await notifyEventOnce(db, env, "worker", body.hash, "STAGE_FAILED", retryRow, retryDetail);
        }
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
      // Notification ledger probe: null means the deployed schema predates
      // the ledger (verify_deploy flags it). 0 is a valid quiet state.
      let notificationEvents: number | null = null;
      try {
        const n: any = await db.prepare(`SELECT COUNT(*) AS n FROM notification_events`).first();
        notificationEvents = n?.n ?? 0;
      } catch { /* table missing on pre-ledger deploys */ }
      const ptr = await currentPointer(db, env);
      return Response.json({
        ok: true,
        jobs: row?.n ?? 0,
        profile_skills: (prof.verifiedSkills || []).length,
        profile_languages: (prof.languages || []).length,
        profile_has_name: !!prof.fullName,
        threshold: parseFloat(env.THRESHOLD || "70"),
        live_apply: env.LIVE_APPLY === "true",
        notification_events: notificationEvents,
        master_resume_sha: ptr.sha || null,
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
        // The probe measures the same window the pipeline applies, so a board
        // that only carries 13-24h-old postings reads as yielding here exactly
        // as it does in production.
        const fresh = filterRecentList(jobs.map((j) => ({ ...j, posted_at: j.posted_at })), HOURS_OLD);
        const remote = filterRemoteList(
          fresh.map((j) => ({ ...j, location: j.location, description: j.desc, remote_flag: undefined })),
        );
        return Response.json({
          name: def.name, kind: def.kind, raw: jobs.length, fresh_24h: fresh.length, remote_fresh: remote.length,
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
    if (url.pathname === "/telegram/webhook" && request.method === "POST") {
      // Restricted inbound surface: owner-only document uploads and /master.
      // Telegram authenticates with the secret-token header configured at
      // setWebhook time; anything else is rejected before any side effect.
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      const update: any = await request.json().catch(() => ({}));
      try {
        await handleTelegramUpdate(update, db, env.CV_BUCKET as any, env);
      } catch (e) {
        console.log(`telegram webhook failed: ${String(e).slice(0, 200)}`);
      }
      return Response.json({ ok: true });
    }
    return new Response("jobhunt worker: /health /recent /sources /coverage /queue /run /probe /telegram/webhook", { status: 200 });
  },
};
