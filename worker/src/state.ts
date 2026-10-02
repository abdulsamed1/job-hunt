// D1 state helpers. Mirrors the SQLite state machine (subset tuned for the
// 50-queries-per-invocation cap: single statements, indexed lookups, batches).

export type JobState =
  | "DISCOVERED" | "DUPLICATE" | "PRE_FILTERED_OUT" | "EVALUATED" | "ELIGIBLE"
  | "REJECTED" | "TAILORED" | "APPLICATION_STARTED" | "SUBMITTED"
  | "BLOCKED_CAPTCHA" | "FAILED" | "RETRY_PENDING";

export interface Db {
  // Minimal D1 surface (kept loose: workers-types evolve faster than this file).
  prepare(query: string): any;
  batch(statements: any[]): Promise<any>;
}

export async function upsertJob(
  db: Db, job: {
    canonical_hash: string; title: string; company: string; location: string;
    url: string; description: string; source: string; posted_at: string; discovered_at: string;
    score: number; remote: boolean;
  },
): Promise<boolean> {
  // Returns true when the row is new OR was materially improved (richer
  // description / backfilled location or date). That is exactly the set of
  // jobs whose stored score may now be stale, so the caller re-evaluates them
  // and nothing else. A no-op upsert returns false and costs no queue message.

  const res = await db.prepare(
    `INSERT INTO jobs (canonical_hash, title, company, location, url, description, source, posted_at, discovered_at, score, remote, state, notified)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', 0)
     ON CONFLICT (canonical_hash) DO UPDATE SET
       -- Backfill richer text: board APIs can start returning descriptions
       -- (or we add enrichment later), and a stale title-only row would keep
       -- scoring as REJECTED forever. Never shrink an existing description.
       description = CASE
         WHEN length(excluded.description) > length(jobs.description) THEN excluded.description
         ELSE jobs.description END,
       location = CASE
         WHEN length(jobs.location) = 0 THEN excluded.location
         ELSE jobs.location END,
       posted_at = CASE
         WHEN length(jobs.posted_at) = 0 THEN excluded.posted_at
         ELSE jobs.posted_at END
     WHERE length(excluded.description) > length(jobs.description)
        OR length(jobs.location) = 0
        OR length(jobs.posted_at) = 0`,
  ).bind(
    job.canonical_hash, job.title.slice(0, 200), job.company.slice(0, 120),
    job.location.slice(0, 120), job.url.slice(0, 500), job.description.slice(0, 4000),
    job.source, job.posted_at.slice(0, 40), job.discovered_at, job.score, job.remote ? 1 : 0,
  ).run();
  return (res.meta.changes ?? 0) > 0;
}

export async function setJobState(
  db: Db, canonicalHash: string, state: JobState, details = "",
): Promise<void> {
  await db.prepare(`UPDATE jobs SET state = ? WHERE canonical_hash = ?`).bind(state, canonicalHash).run();
  await db.prepare(
    `INSERT INTO audit_log (job_hash, from_state, to_state, timestamp, details) VALUES (?, NULL, ?, ?, ?)`,
  ).bind(canonicalHash, state, new Date().toISOString(), details.slice(0, 500)).run();
}

export async function getJobsByState(db: Db, state: JobState, limit: number): Promise<Array<Record<string, any>>> {
  const { results } = await db.prepare(
    `SELECT canonical_hash, title, company, location, url, source, posted_at, score, remote FROM jobs WHERE state = ? ORDER BY score DESC, discovered_at DESC LIMIT ?`,
  ).bind(state, limit).all();
  return results;
}

export async function saveEvaluation(
  db: Db, canonicalHash: string, score: number, eligible: boolean, reasoning: string,
): Promise<void> {
  await db.prepare(
    `INSERT INTO evaluations (job_hash, score, eligible, reasoning, evaluated_at)
     VALUES (?, ?, ?, ?, ?) ON CONFLICT (job_hash) DO UPDATE SET score=excluded.score, eligible=excluded.eligible, reasoning=excluded.reasoning`,
  ).bind(canonicalHash, score, eligible ? 1 : 0, reasoning.slice(0, 1000), new Date().toISOString()).run();
  await db.prepare(`UPDATE jobs SET score = ? WHERE canonical_hash = ?`).bind(score, canonicalHash).run();
  await setJobState(db, canonicalHash, eligible ? "ELIGIBLE" : "REJECTED", `score ${score}`);
}

export async function saveApplication(
  db: Db, jobHash: string, state: JobState, detail: string, screenshotR2Key: string | null,
): Promise<void> {
  await db.prepare(
    `INSERT INTO applications (job_hash, state, detail, screenshot_r2_key, applied_at)
     VALUES (?, ?, ?, ?, ?) ON CONFLICT (job_hash) DO UPDATE SET state=excluded.state, detail=excluded.detail`,
  ).bind(jobHash, state, detail.slice(0, 500), screenshotR2Key, new Date().toISOString()).run();
  await setJobState(db, jobHash, state, detail.slice(0, 200));
}

/** Add the degraded-signal columns to pre-existing source_health tables.
 * Fresh databases already carry them (see schema.sql); this is the
 * migration path for tables created before the columns existed. Mirrors the
 * PRAGMA-check style in src/job_hunt/storage.py. Safe to re-run. */
export async function ensureSourceHealthColumns(db: Db): Promise<void> {
  try {
    const res = await db.prepare(`PRAGMA table_info(source_health)`).all();
    const cols = new Set((res.results || []).map((r: any) => r.name));
    if (!cols.has("last_degraded")) {
      try {
        await db.prepare(`ALTER TABLE source_health ADD COLUMN last_degraded TEXT NOT NULL DEFAULT ''`).run();
      } catch { /* raced with another invocation */ }
    }
    if (!cols.has("degraded_hits")) {
      try {
        await db.prepare(`ALTER TABLE source_health ADD COLUMN degraded_hits INTEGER NOT NULL DEFAULT 0`).run();
      } catch { /* raced with another invocation */ }
    }
  } catch {
    // PRAGMA unsupported here (or table missing): fresh databases get the
    // columns from schema.sql, so there is nothing to migrate.
  }
}

/** Record one discovery attempt so source coverage is measured, not assumed. */
export async function recordSourceHealth(
  db: Db, source: string, kind: string, raw: number, kept: number, error = "", degraded: string[] = [],
): Promise<void> {
  await ensureSourceHealthColumns(db);
  const degradedSignals = degraded.join(",").slice(0, 200);
  const degradedHit = degraded.length > 0 ? 1 : 0;
  await db.prepare(
    `INSERT INTO source_health (source, kind, attempts, yields, last_raw, last_kept, last_error, last_seen, last_degraded, degraded_hits)
     VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(source) DO UPDATE SET
       attempts = attempts + 1,
       yields = yields + excluded.yields,
       last_raw = excluded.last_raw,
       last_kept = excluded.last_kept,
       last_error = excluded.last_error,
       last_seen = excluded.last_seen,
       last_degraded = excluded.last_degraded,
       degraded_hits = degraded_hits + excluded.degraded_hits`,
  ).bind(source, kind, kept > 0 ? 1 : 0, raw, kept, error.slice(0, 200), new Date().toISOString(), degradedSignals, degradedHit).run();
}

/** Jobs that need a human or are already submitted -- the actionable morning list. */
export async function getActionableJobs(db: Db, limit = 50): Promise<Array<Record<string, any>>> {
  const { results } = await db.prepare(
    `SELECT canonical_hash, title, company, location, url, source, posted_at, score, state
     FROM jobs
     WHERE state IN ('APPLICATION_STARTED', 'TAILORED', 'SUBMITTED', 'ELIGIBLE')
     ORDER BY score DESC, discovered_at DESC LIMIT ?`,
  ).bind(limit).all();
  return results;
}
