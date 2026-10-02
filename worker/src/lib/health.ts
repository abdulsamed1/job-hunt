import type { RawJob } from "../adapters/http.js";

type DegradedRow = Pick<RawJob, "title" | "company" | "url">;

export function detectDegraded(jobs: DegradedRow[], sourceUrl: string): string[] {
  const signals = new Set<string>();
  let host = "";
  try { host = new URL(sourceUrl).hostname; } catch { /* ignore */ }
  for (const j of jobs) {
    if (!j.company) signals.add("null-company");
    if (!j.title) signals.add("empty-title");
    if (/&[a-z]+;|<[^>]+>/.test(j.title)) signals.add("html-in-title");
    try {
      if (host && new URL(j.url).hostname !== host
        && !new URL(j.url).hostname.endsWith("." + host)) signals.add("off-domain-url");
    } catch { signals.add("off-domain-url"); }
  }
  return [...signals];
}
