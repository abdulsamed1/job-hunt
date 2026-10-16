// Telegram outbound lifecycle events: seeking, filter, evaluation, tailor,
// application start, submission, failure, retry, and recovery notices.
// Outbound only — the bot takes no inbound commands. Sends are best-effort
// with at-most-once semantics per (runtime, job_hash, event): the event key
// is inserted into `notification_events` before sending, so queue retries see
// the conflicting key and skip. `jobs.notified` is still updated for
// compatibility with older readers. Alert/network/DB errors must never break
// the pipeline or retry-storm Telegram.

export type JobOutcome = "SUBMITTED" | "APPLICATION_STARTED" | "FAILED";

/** Lifecycle events either runtime may report for a job. */
export type WorkerEvent =
  | "SEEKING"
  | "FILTERED"
  | "ELIGIBLE"
  | "EVALUATED"
  | "TAILORED"
  | "APPLICATION_STARTED"
  | "SUBMITTED"
  | "FAILED"
  | "RETRY"
  | "RECOVERY";

export interface AlertEnv {
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
}

export interface AlertJobRow {
  title?: string | null;
  company?: string | null;
  url?: string | null;
  score?: number | null;
}

const MAX_ALERT_LEN = 3000;

/** Send one Telegram message. Returns true only on HTTP 200. Never throws. */
export async function alert(env: AlertEnv, text: string): Promise<boolean> {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  try {
    const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: text.slice(0, 3000) }),
    });
    if (!res.ok) {
      console.log(`telegram alert failed: HTTP ${res.status}`);
      return false;
    }
    return true;
  } catch (e) {
    console.log(`telegram alert failed: ${String(e).slice(0, 120)}`);
    return false;
  }
}

/**
 * Unique dedup key for one lifecycle event. Pure function — the same
 * (runtime, job_hash, event) always maps to the same key, and any change in
 * one component maps to a different key so a job can report seeking, submit,
 * and failure independently.
 */
export function eventKey(runtime: string, jobHash: string, event: WorkerEvent): string {
  return `${runtime}:${jobHash}:${event}`;
}

/**
 * Render the source-tagged per-event message. Pure function — fully
 * unit-tested. Shape mirrors the Python `format_event` contract:
 * `[runtime] EVENT: title @ company` plus detail/url lines, bounded to
 * 3000 chars. Never includes tokens or credentials (callers pass only job
 * fields and state reasons).
 */
export function formatJobAlert(
  runtime: string,
  row: AlertJobRow,
  event: WorkerEvent,
  detail: string,
): string {
  const tag = (runtime || "worker").slice(0, 40);
  const name = (event || "UNKNOWN").slice(0, 40);
  const title = (row.title || "Untitled role").slice(0, 120);
  const company = (row.company || "Unknown company").slice(0, 80);
  const lines = [`[${tag}] ${name}: ${title} @ ${company}`];
  if (detail) lines.push(detail.slice(0, 2000));
  if (row.url) lines.push(row.url.slice(0, 500));
  return lines.join("\n").slice(0, MAX_ALERT_LEN);
}

/**
 * Alert at most once per (runtime, job_hash, event). Claims the event key in
 * `notification_events` first: a conflicting key means this event already
 * sent (or is being sent) and resolves "skipped" without a second send.
 * Only a successful insert sends. Alert failures resolve "failed" but still
 * record the claim, so a Telegram outage cannot retry-storm. Non-conflict DB
 * errors resolve "failed" without sending. Never throws.
 * Returns "sent" | "skipped" | "failed".
 */
export async function notifyEventOnce(
  db: any,
  env: AlertEnv,
  runtime: string,
  hash: string,
  event: WorkerEvent,
  row: AlertJobRow,
  detail: string,
): Promise<"sent" | "skipped" | "failed"> {
  const rt = runtime || "worker";
  try {
    await db
      .prepare(
        `INSERT INTO notification_events (runtime, job_hash, event, sent_at) VALUES (?, ?, ?, ?)`,
      )
      .bind(rt, hash, event, new Date().toISOString())
      .run();
  } catch (e) {
    // D1 raises on PRIMARY KEY conflict — that is the dedup signal.
    if (/unique|primary key|constraint failed|already exists/i.test(String(e))) return "skipped";
    return "failed";
  }
  let outcome: "sent" | "failed" = "failed";
  try {
    if (await alert(env, formatJobAlert(rt, row, event, detail))) outcome = "sent";
  } catch {
    outcome = "failed";
  }
  try {
    // Compatibility: older readers still check the per-job flag.
    await db.prepare(`UPDATE jobs SET notified = 1 WHERE canonical_hash = ?`).bind(hash).run();
  } catch {
    // Notification bookkeeping must never break the pipeline either.
  }
  return outcome;
}

/**
 * Compatibility wrapper for the pre-event callers: one alert per job for the
 * three original terminal outcomes. Keeps the legacy `jobs.notified` read
 * (jobs notified under the old flag never resend) and delegates the send to
 * the event ledger under runtime "worker".
 * Returns "sent" | "skipped" | "failed". Never throws.
 */
export async function notifyJobOnce(
  db: any,
  env: AlertEnv,
  hash: string,
  row: AlertJobRow,
  state: JobOutcome,
  detail: string,
): Promise<"sent" | "skipped" | "failed"> {
  let cur: any = null;
  try {
    cur = await db.prepare(`SELECT notified FROM jobs WHERE canonical_hash = ?`).bind(hash).first();
  } catch {
    cur = null;
  }
  if (cur?.notified) return "skipped";
  const scoredDetail =
    typeof row.score === "number"
      ? `(score ${row.score})${detail ? ` ${detail}` : ""}`
      : detail;
  return notifyEventOnce(db, env, "worker", hash, state, row, scoredDetail);
}
