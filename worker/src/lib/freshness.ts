// Recency helpers (mirrors Python discovery/freshness.py).

/**
 * The Worker's single recency window: a posting must be inside 24h to reach
 * the feed or to be applied to. Not 12h — the deep sweep runs daily, so a 12h
 * window silently drops everything that landed 13-24h before it, and the
 * owner's ceiling is a day. Every caller reads this constant; none re-states
 * the number. `0` (no window at all) is for a deliberate manual run only and
 * never for a scheduled one — and even then a dateless posting stays in the
 * `dateless` bucket, because a disabled window cannot prove a date the source
 * never gave.
 */
export const HOURS_OLD = 24;

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

/** The candidate is based in Egypt: an on-site or hybrid role there is in
 *  scope alongside remote roles anywhere. Country first, then the cities and
 *  compounds boards actually print; matched case-insensitively. */
const EGYPT_PLACES = [
  "egypt", "cairo", "alexandria", "giza", "6th of october", "6th october",
  "new cairo", "nasr city", "smart village", "maadi", "zamalek", "dokki",
  "mohandessin", "heliopolis", "new capital", "administrative capital",
  "new administrative capital", "port said", "suez", "ismailia", "mansoura",
  "tanta", "luxor", "aswan", "fayoum", "beni suef", "minya", "hurghada",
  "sharm el sheikh", "10th of ramadan", "sheikh zayed", "sadat city",
  "october city",
];

export function isRemoteish(location: string, description: string, remoteFlag?: boolean): boolean {
  if (remoteFlag === true) return true;
  const hay = `${location || ""} ${description || ""}`.toLowerCase();
  return REMOTE_SIGNS.some((s) => hay.includes(s));
}

/** True when the location names Egypt (country or a known Egyptian place). */
export function isEgyptLocation(location: string): boolean {
  const loc = (location || "").trim().toLowerCase();
  if (!loc) return false;
  return EGYPT_PLACES.some((place) => new RegExp(`\\b${place.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(loc));
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
export function filterRecentList<T extends LitePosting>(postings: T[], hoursOld: number = HOURS_OLD): T[] {
  if (!hoursOld || hoursOld <= 0) return postings;
  return postings.filter((p) => isFresh(p.posted_at ?? null, hoursOld) !== false);
}

/** A posting split three ways by the window. `dateless` is the honest answer
 *  when the source gave no parseable date: nothing can vouch for it, so it is
 *  never `fresh` — not even under a disabled window. */
export interface FreshPartition<T> {
  fresh: T[];
  stale: T[];
  dateless: T[];
}

/**
 * Split postings by the window instead of filtering them, because the caller
 * has to *store* the dateless ones: a posting with no date is kept for the
 * record and counted, while a provably-stale one is simply never stored.
 * Order is preserved inside every bucket.
 */
export function partitionFresh<T extends LitePosting>(
  postings: T[],
  hoursOld: number,
): FreshPartition<T> {
  const fresh: T[] = [];
  const stale: T[] = [];
  const dateless: T[] = [];
  const openWindow = !hoursOld || hoursOld <= 0;
  for (const p of postings) {
    // isFresh() short-circuits to true under a disabled window without ever
    // looking at the date, so the date is read first: an undated posting stays
    // dateless either way.
    const ts = parsePostedAt(p.posted_at ?? null);
    if (ts === null) dateless.push(p);
    else if (!openWindow && ts < Date.now() - hoursOld * 3600_000) stale.push(p);
    else fresh.push(p);
  }
  return { fresh, stale, dateless };
}

/** Keep remote-signalled + unknown-location postings. */
export function filterRemoteList<T extends LitePosting>(postings: T[]): T[] {
  return postings.filter(
    (p) => isRemoteish(p.location ?? "", p.description ?? "", p.remote_flag) || !isPlacedOnsite(p.location ?? "", p.description ?? "", p.remote_flag),
  );
}

/**
 * The owner's standing target: remote anywhere + placed in Egypt.
 *
 * Keeps remote-signalled postings wherever they are, postings whose location
 * names Egypt (on-site, hybrid or remote there), and postings with no usable
 * location signal — a board that gives no location never loses a posting.
 * Drops only a posting proven to be placed on-site outside Egypt.
 */
export function filterTargetGeoList<T extends LitePosting>(postings: T[]): T[] {
  return postings.filter(
    (p) =>
      isRemoteish(p.location ?? "", p.description ?? "", p.remote_flag) ||
      isEgyptLocation(p.location ?? "") ||
      !isPlacedOnsite(p.location ?? "", p.description ?? "", p.remote_flag),
  );
}
