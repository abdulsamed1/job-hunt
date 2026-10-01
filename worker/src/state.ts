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
  const res = await db.prepare(
    `INSERT INTO jobs (canonical_hash, title, company, location, url, description, source, posted_at, discovered_at, score, remote, state, notified)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', 0)
     ON CONFLICT (canonical_hash) DO NOTHING`,
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
