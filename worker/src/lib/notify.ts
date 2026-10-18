// Telegram outbound lifecycle events: seeking, filter, evaluation, tailor,
// application attempt, submission, dry-run result, failure, and stage-failure
// notices. Inbound is limited to the owner's chat: PDF master-resume uploads
// and the /master status command (see lib/webhook.ts). Sends are
// best-effort with at-most-once semantics per (runtime, job_hash, event): the
// event key is inserted into `notification_events` before sending, so queue
// retries see the conflicting key and skip. With Telegram credentials unset
// nothing is claimed or written, so events can still flow once the bot is
// configured. Alert/network/DB errors must never break the pipeline or
// retry-storm Telegram.

/** Lifecycle events either runtime may report for a job. Both runtimes share
 * the evaluate vocabulary: FILTERED (gated or pre-filtered out) and ELIGIBLE
 * (passed evaluation). Python-only events (RETRY, RECOVERY, BLOCKED_CAPTCHA)
 * stay off this union; the Worker reports failed queue stages as
 * STAGE_FAILED and acknowledges the message instead of retrying it. */
export type WorkerEvent =
  | "SEEKING"
  | "FILTERED"
  | "ELIGIBLE"
  | "TAILORED"
  | "APPLICATION_ATTEMPT"
  | "SUBMITTED"
  | "DRY_RUN"
  | "FAILED"
  | "STAGE_FAILED";

import { parsePostedAt } from "./freshness.js";

export interface AlertEnv {
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
}

export interface AlertJobRow {
  title?: string | null;
  company?: string | null;
  url?: string | null;
  score?: number | null;
  /** Board the posting came from, e.g. "greenhouse:acme". */
  source?: string | null;
  /** The source's own date; drives the rendered age. */
  posted_at?: string | null;
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
 * How old a posting is, as the owner reads it: `3h ago`, `20m ago`, `2d ago`.
 * A board clock running ahead clamps to `0m ago` rather than rendering a
 * negative age, and a date that cannot be parsed says so — the whole point of
 * the rendered age is that an unverifiable posting is visible as unverifiable.
 */
export function formatPostedAge(postedAt: unknown, now: number = Date.now()): string {
  const ts = parsePostedAt(postedAt);
  if (ts === null) return "date unknown";
  const minutes = Math.floor(Math.max(0, now - ts) / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * Render the source-tagged per-event message. Pure function — fully
 * unit-tested. Shape mirrors the Python `format_event` contract:
 * `[runtime] EVENT: title @ company` plus detail/url lines, bounded to
 * 3000 chars, and the head line carries the board and the posting's own age
 * so a feed that quietly drifts older than a day is visible at a glance.
 * Never includes tokens or credentials (callers pass only job
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
  // Only the segments the row actually carries: a STAGE_FAILED alert that
  // found no row must not render "· date unknown · date unknown".
  const head = [`[${tag}] ${name}: ${title} @ ${company}`];
  if (row.source) head.push(row.source.slice(0, 120));
  head.push(formatPostedAge(row.posted_at));
  const lines = [head.join(" · ")];
  if (detail) lines.push(detail.slice(0, 2000));
  if (row.url) lines.push(row.url.slice(0, 500));
  return lines.join("\n").slice(0, MAX_ALERT_LEN);
}

/**
 * Alert at most once per (runtime, job_hash, event). Claims the event key in
 * `notification_events` first: a conflicting key means this event already
 * sent (or is being sent) and resolves "skipped" without a second send.
 * Only a successful insert sends. Alert failures resolve "failed" but still
 * record the claim, so a Telegram outage cannot retry-storm. Missing
 * credentials resolve "skipped" without claiming anything. Non-conflict DB
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
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return "skipped";
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
  return outcome;
}
