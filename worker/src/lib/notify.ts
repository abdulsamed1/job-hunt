// Telegram outbound alerts: match-ready, submitted, and failure notices.
// Outbound only — the bot takes no inbound commands. Every send is
// best-effort and idempotent per job (see notifyJobOnce): alert failures
// must never break the pipeline or retry-storm Telegram.

export type JobOutcome = "SUBMITTED" | "APPLICATION_STARTED" | "FAILED";

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

/** Render the per-outcome message. Pure function — fully unit-tested. */
export function formatJobAlert(
  row: AlertJobRow,
  state: JobOutcome,
  detail: string,
): string {
  const title = (row.title || "Untitled role").slice(0, 120);
  const company = (row.company || "Unknown company").slice(0, 80);
  const score = row.score ?? "?";
  const url = row.url || "";
  if (state === "SUBMITTED") {
    return `Applied: ${title} @ ${company} (score ${score})\n${url}`;
  }
  if (state === "FAILED") {
    return `Needs attention: ${title} @ ${company}\n${(detail || "").slice(0, 200)}\n${url}`;
  }
  return `Ready for review: ${title} @ ${company} (score ${score})\n${(detail || "").slice(0, 200)}\n${url}`;
}

/**
 * Alert at most once per job. Reads the `notified` flag, sends on 0,
 * then marks notified=1 whether sending succeeded or not (a Telegram
 * outage must not retry-storm; queue retries are already bounded).
 * Returns "sent" | "skipped" | "failed".
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
  let outcome: "sent" | "failed" = "failed";
  try {
    if (await alert(env, formatJobAlert(row, state, detail))) outcome = "sent";
  } catch {
    outcome = "failed";
  }
  try {
    await db.prepare(`UPDATE jobs SET notified = 1 WHERE canonical_hash = ?`).bind(hash).run();
  } catch {
    // Notification bookkeeping must never break the pipeline either.
  }
  return outcome;
}
