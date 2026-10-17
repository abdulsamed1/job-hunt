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

export interface UpsertJobOptions {
  /** Birth state of the row. Defaults to DISCOVERED; a gate reject stores
   *  PRE_FILTERED_OUT so the posting exists but is never scored or applied. */
  state?: JobState;
  /** Why a PRE_FILTERED_OUT row was stored, e.g. "role gate: revenue:sales".
   *  Recorded in the row's evaluation reasoning, once per distinct reason. */
  detail?: string;
  /** Optional out-parameter: what this call actually recorded. The return value
   *  stays the new-or-improved boolean every existing caller reads. */
  outcome?: UpsertOutcome;
}

export interface UpsertOutcome {
  /** True only when this call wrote the gate reason for the first time: the
   *  posting was new, or it predates the gate and had never been explained.
   *  False when the reason is already recorded, which is the case for every
   *  re-discovery — including a dateless posting, whose jobs row counts as
   *  "improved" on every sweep. Callers gate side effects (counters, alerts) on
   *  this, never on the jobs upsert's own return value. */
  recorded: boolean;
}

export async function upsertJob(
  db: Db, job: {
    canonical_hash: string; title: string; company: string; location: string;
    url: string; description: string; source: string; posted_at: string; discovered_at: string;
    score: number; remote: boolean;
  },
  opts: UpsertJobOptions = {},
): Promise<boolean> {
  // Returns true when the row is new OR was materially improved (richer
  // description / backfilled location or date). That is exactly the set of
  // jobs whose stored score may now be stale, so the caller re-evaluates them
  // and nothing else. A no-op upsert returns false and costs no queue message.

  const res = await db.prepare(
    `INSERT INTO jobs (canonical_hash, title, company, location, url, description, source, posted_at, discovered_at, score, remote, state, notified)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
    opts.state ?? "DISCOVERED",
  ).run();
  const changed = (res.meta.changes ?? 0) > 0;
  const outcome: UpsertOutcome = opts.outcome ?? { recorded: false };
  // A gate reject records why beside the row, in the same reasoning column the
  // evaluate stage writes. `instr` makes the write happen once per distinct
  // reason: a posting re-discovered on a later sweep reports changes = 1 (its
  // dateless row counts as improved forever) but the reason is already stored,
  // so this statement is a no-op and `recorded` stays false. Reasoning therefore
  // cannot grow per sweep and callers get a first-time signal to gate counters
  // and alerts on.
  if (changed && opts.state === "PRE_FILTERED_OUT" && opts.detail) {
    const detail = await db.prepare(
      `INSERT INTO evaluations (job_hash, score, eligible, reasoning, evaluated_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT (job_hash) DO UPDATE SET
         score = 0,
         eligible = 0,
         reasoning = CASE
           WHEN length(evaluations.reasoning) > 0 THEN evaluations.reasoning || ' | ' || excluded.reasoning
           ELSE excluded.reasoning END,
         evaluated_at = excluded.evaluated_at
       WHERE instr(evaluations.reasoning, excluded.reasoning) = 0`,
    ).bind(job.canonical_hash, 0, 0, opts.detail.slice(0, 1000), new Date().toISOString()).run();
    outcome.recorded = (detail.meta.changes ?? 0) > 0;
  }
  return changed;
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
  state?: JobState,
): Promise<void> {
  await db.prepare(
    `INSERT INTO evaluations (job_hash, score, eligible, reasoning, evaluated_at)
     VALUES (?, ?, ?, ?, ?) ON CONFLICT (job_hash) DO UPDATE SET score=excluded.score, eligible=excluded.eligible, reasoning=excluded.reasoning`,
  ).bind(canonicalHash, score, eligible ? 1 : 0, reasoning.slice(0, 1000), new Date().toISOString()).run();
  await db.prepare(`UPDATE jobs SET score = ? WHERE canonical_hash = ?`).bind(score, canonicalHash).run();
  // Callers may pin the final state (e.g. gated rows land directly in
  // PRE_FILTERED_OUT) so a job never gets two audit rows in one evaluation.
  await setJobState(db, canonicalHash, state ?? (eligible ? "ELIGIBLE" : "REJECTED"), `score ${score}`);
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

/** Create the notification ledger on databases that predate it. Fresh
 * databases already have it (see schema.sql); this is the runtime migration
 * so the first alerted event never fails on a missing table. One statement:
 * the caller invokes it once per queue batch. Safe to re-run. */
export async function ensureNotificationEvents(db: Db): Promise<void> {
  try {
    await db.prepare(
      `CREATE TABLE IF NOT EXISTS notification_events (
         runtime TEXT NOT NULL,
         job_hash TEXT NOT NULL,
         event TEXT NOT NULL,
         sent_at TEXT NOT NULL,
         PRIMARY KEY (runtime, job_hash, event)
       )`,
    ).run();
  } catch { /* raced with another invocation (or table exists) */ }
}

/** Create the settings table on databases that predate it (fresh databases
 * already have it — see schema.sql). Safe to re-run. */
export async function ensureSettings(db: Db): Promise<void> {
  try {
    await db.prepare(
      `CREATE TABLE IF NOT EXISTS settings (
         key TEXT PRIMARY KEY,
         value TEXT NOT NULL,
         updated_at TEXT NOT NULL
       )`,
    ).run();
  } catch { /* raced with another invocation (or table exists) */ }
}

/** Read one settings value. Missing key or missing table resolves null so
 * callers can fall back to env-based defaults. */
export async function getSetting(db: Db, key: string): Promise<string | null> {
  try {
    const row: any = await db.prepare(`SELECT value FROM settings WHERE key = ? LIMIT 1`).bind(key).first();
    const v = row?.value;
    return typeof v === "string" && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

/** Upsert one settings value. */
export async function setSetting(db: Db, key: string, value: string): Promise<void> {
  await db.prepare(
    `INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
  ).bind(key, value, new Date().toISOString()).run();
}

/** Create the counters table on databases that predate it. Fresh databases get
 * it from schema.sql; this is the migration for existing ones, exactly like
 * ensureNotificationEvents and ensureSettings. Runs once per queue batch (see
 * queue() in index.ts) before any bump. Mirrors ensureSourceHealthColumns in
 * style: one statement, safe to re-run, tolerant of a lost race. */
export async function ensureCounters(db: Db): Promise<void> {
  try {
    await db.prepare(
      `CREATE TABLE IF NOT EXISTS counters (
         key TEXT PRIMARY KEY,
         value INTEGER NOT NULL DEFAULT 0
       )`,
    ).run();
  } catch { /* raced with another invocation (or table exists) */ }
}

/** Add n to one named counter (role_rejected, duplicate_suppressed,
 * dateless_dropped). One statement, no read-back: the running total lives in
 * the row, so N postings cost N writes and no extra query. Counters are
 * observability, never a gate — callers must tolerate a failure here rather
 * than let it abandon the postings they are counting. */
export async function bumpCounter(db: Db, key: string, n = 1): Promise<void> {
  await db.prepare(
    `INSERT INTO counters (key, value) VALUES (?, ?)
     ON CONFLICT(key) DO UPDATE SET value = counters.value + ?`,
  ).bind(key, n, n).run();
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

/** Record one discovery attempt so source coverage is measured, not assumed.
 * Exactly one write per call; the caller hoists ensureSourceHealthColumns to
 * once per batch (see queue() in index.ts). */
export async function recordSourceHealth(
  db: Db, source: string, kind: string, raw: number, kept: number, error = "", degraded: string[] = [],
): Promise<void> {
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
