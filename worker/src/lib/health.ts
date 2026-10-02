import type { RawJob } from "../adapters/http.js";

type DegradedRow = Pick<RawJob, "title" | "company" | "url">;

export function detectDegraded(jobs: DegradedRow[], sourceUrl: string, kind = ""): string[] {
  const signals = new Set<string>();
  let host = "";
  try { host = new URL(sourceUrl).hostname; } catch { /* ignore */ }
  // RSS feeds aggregate off-domain job URLs by design; the signal is noise there.
  const skipOffDomain = kind === "rss";
  for (const j of jobs) {
    if (!j.company) signals.add("null-company");
    if (!j.title) signals.add("empty-title");
    if (/&[a-z]+;|<[^>]+>/.test(j.title)) signals.add("html-in-title");
    if (skipOffDomain) continue;
    try {
      if (host && new URL(j.url).hostname !== host
        && !new URL(j.url).hostname.endsWith("." + host)) signals.add("off-domain-url");
    } catch { signals.add("off-domain-url"); }
  }
  return [...signals];
}

/**
 * Transport/rate-limit failures carry no signal about the source itself (a
 * throttled or unreachable board is not a rotted one), so the /probe caller
 * should report them as inconclusive rather than as a dead source.
 */
const INCONCLUSIVE_PROBE_ERROR =
  /(?:\b429\b|e429|rate[\s_-]*limit|too many requests|fetch failed|\benotfound\b|\beai_again\b|timed?\s*out|aborterror|service unavailable|bad gateway|gateway timeout|\b5\d\d\b)/i;

export function isInconclusiveProbeError(e: unknown): boolean {
  const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
  return INCONCLUSIVE_PROBE_ERROR.test(msg.slice(0, 500));
}
