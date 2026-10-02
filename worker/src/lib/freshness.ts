// Recency helpers (mirrors Python discovery/freshness.py).

export function parsePostedAt(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? value : value * 1000; // ms vs seconds
  }
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d{10}(\.\d+)?$/.test(text)) return parseFloat(text) * 1000;
  if (/^\d{13}$/.test(text)) return parseInt(text, 10);
  const rel = text.toLowerCase().match(/^(\d+)\s+(hour|day|week|month)s?\s+ago$/);
  if (rel) {
    const n = parseInt(rel[1], 10);
    const mult = { hour: 36e5, day: 864e5, week: 6048e5, month: 2592e6 } as const;
    return Date.now() - n * mult[rel[2] as keyof typeof mult];
  }
  const iso = text.replace(/Z$/, "+00:00");
  const t = Date.parse(text.includes("T") || text.includes("GMT") ? text : iso);
  if (!Number.isNaN(t)) return t;
  return null;
}

/** true/false when dated, null when unknown (unknown always passes). */
export function isFresh(postedAt: unknown, hoursOld: number): boolean | null {
  if (!hoursOld || hoursOld <= 0) return true;
  const ts = parsePostedAt(postedAt);
  if (ts === null) return null;
  return ts >= Date.now() - hoursOld * 3600_000;
}

const REMOTE_SIGNS = ["remote", "worldwide", "anywhere", "wfh", "work from home", "work from anywhere", "distributed", "telecommute", "virtual"];

export function isRemoteish(location: string, description: string, remoteFlag?: boolean): boolean {
  if (remoteFlag === true) return true;
  const hay = `${location || ""} ${description || ""}`.toLowerCase();
  return REMOTE_SIGNS.some((s) => hay.includes(s));
}

/** Drop only placed on-site postings; unknown locations pass. */
export function isPlacedOnsite(location: string, description: string, remoteFlag?: boolean): boolean {
  const loc = (location || "").trim().toLowerCase();
  if (!loc) return false;
  if (isRemoteish(location, description, remoteFlag)) return false;
  return /[a-z]{3,}/.test(loc);
}

export interface LitePosting {
  posted_at?: unknown;
  location?: string;
  description?: string;
  remote_flag?: boolean;
}

/** Drop only provably-stale postings; dateless ones pass. */
export function filterRecentList<T extends LitePosting>(postings: T[], hoursOld: number = 12): T[] {
  if (!hoursOld || hoursOld <= 0) return postings;
  return postings.filter((p) => isFresh(p.posted_at ?? null, hoursOld) !== false);
}

/** Keep remote-signalled + unknown-location postings. */
export function filterRemoteList<T extends LitePosting>(postings: T[]): T[] {
  return postings.filter(
    (p) => isRemoteish(p.location ?? "", p.description ?? "", p.remote_flag) || !isPlacedOnsite(p.location ?? "", p.description ?? "", p.remote_flag),
  );
}
