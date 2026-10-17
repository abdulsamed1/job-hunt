// Stable identity + URL normalization (mirrors Python dedup.py).

export function fnv1a(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

// Query params that name the click or the campaign, never the posting. Same
// names Python strips (dedup.TRACKING_PARAM_PATTERNS), matched by prefix for
// `utm_*` so a variant nobody has listed yet is covered without a code change.
// `ref` and `source` stay stripped on top of Python's list: aggregator mirrors
// carry them, and dropping them is what collapses the mirror onto the board's
// own URL. Stripping is a superset of Python's set, never a subset.
const TRACKING = new Set([
  "gh_src", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid",
  "_hsenc", "_hsmi", "trk", "trackingid", "ref_code", "ref", "source",
]);
const TRACKING_PREFIX = "utm_";

function isTrackingParam(key: string): boolean {
  const k = key.toLowerCase();
  return k.startsWith(TRACKING_PREFIX) || TRACKING.has(k);
}

export function normalizeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    u.hash = "";
    for (const k of [...u.searchParams.keys()]) {
      if (isTrackingParam(k)) u.searchParams.delete(k);
    }
    let s = u.toString();
    if (s.endsWith("/") && u.pathname !== "/") s = s.slice(0, -1);
    return s;
  } catch {
    return raw;
  }
}

export function canonicalHash(rawUrl: string): string {
  return fnv1a(normalizeUrl(rawUrl));
}

/** Lowercase, then collapse every punctuation/whitespace run to one space. */
function words(raw: string): string[] {
  return raw.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
}

/** Locations that all name the same thing: work not tied to a place. One
 *  requisition reaches us spelled half a dozen ways ("Remote", "Remote, US",
 *  "Anywhere", "Work From Home"), so every all-remote location has to
 *  normalize to the same token or two boards of the same opening look like two
 *  openings. Matched word by word, so "Work From Home" matches as a phrase
 *  while a location that keeps a place ("Remote - Europe", "Home Office")
 *  stays its own thing. */
const REMOTE_LOCATION_PHRASES = [
  "remote", "anywhere", "worldwide", "wfh", "work from home",
  "distributed", "telecommute", "united states", "us", "usa", "global",
];
const REMOTE_LOCATION_WORDS = new Set(REMOTE_LOCATION_PHRASES.flatMap((p) => p.split(" ")));

/** One posting's place, canonical. Empty/unknown becomes `?`, never "remote":
 *  a posting with no location must not share a fingerprint with one that says
 *  remote, or the two would collapse into a single application. */
export function normalizeLocation(raw: string): string {
  const tokens = words(raw);
  if (!tokens.length) return "?";
  return tokens.every((t) => REMOTE_LOCATION_WORDS.has(t)) ? "remote" : tokens.join(" ");
}

/** A posting's second identity: the same company, role and place on any board,
 *  whatever the URL. This is what collapses the owner's x4 (company, title)
 *  groups, where identity today is the canonical URL hash alone and the same
 *  requisition arrives under two jids or on two boards.
 *
 *  Keeps the Worker's own fnv1a hash: only the normalization rules are shared
 *  with Python, because each runtime dedupes its own store and cross-runtime
 *  hash equality buys nothing. */
export function roleFingerprint(company: string, title: string, location: string): string {
  return fnv1a(`${words(company).join(" ")}|${words(title).join(" ")}|${normalizeLocation(location)}`);
}
