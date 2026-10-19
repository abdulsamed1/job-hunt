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

/** Company legal and generic suffixes (mirrors Python dedup.COMPANY_SUFFIXES).
 *  "Acme, Inc." and "Acme" are the same employer, so the fingerprint must not
 *  depend on which of the two the board wrote. */
const COMPANY_SUFFIXES: RegExp[] = [
  /\binc\.?\b/g,
  /\bincorporated\b/g,
  /\bllc\.?\b/g,
  /\bltd\.?\b/g,
  /\blimited\b/g,
  /\bcorp\.?\b/g,
  /\bcorporation\b/g,
  /\bco\.?\b/g,
  /\bcompany\b/g,
  /\btechnologies\b/g,
  /\btechnology\b/g,
  /\bholdings\b/g,
  /\bgmbh\b/g,
  /\bag\b/g,
  /\bsa\b/g,
  /\bsas\b/g,
  /\bplc\b/g,
  /\bbv\b/g,
];

/** One employer's canonical name: lowercase, legal suffixes removed, and the
 *  rest reduced to words. Mirrors Python's dedup.normalize_company. */
export function normalizeCompany(raw: string): string {
  let text = (raw || "").trim().toLowerCase();
  for (const suffix of COMPANY_SUFFIXES) text = text.replace(suffix, "");
  // Strip everything that is not a letter, a digit or a space (Python keeps
  // underscores, so this does too).
  return text.replace(/[^\p{L}\p{N}_\s]/gu, " ").replace(/\s+/g, " ").trim();
}

/** Role abbreviations and their expansions (mirrors Python
 *  dedup.ROLE_TOKEN_EXPANSIONS). "Sr. Backend Engineer" and "Senior Backend
 *  Engineer" are the same role. */
const ROLE_TOKEN_EXPANSIONS: Record<string, string> = {
  sr: "senior",
  "sr.": "senior",
  jr: "junior",
  "jr.": "junior",
  swe: "software engineer",
  sw: "software",
  eng: "engineer",
  engr: "engineer",
  dev: "developer",
  mgr: "manager",
  lead: "lead",
  fde: "forward deployed engineer",
  sre: "site reliability engineer",
  infra: "infrastructure",
  qa: "quality assurance",
  ml: "machine learning",
  ai: "artificial intelligence",
  genai: "generative ai",
};

/** One posting's canonical role: lowercase, abbreviations expanded, noise
 *  removed. Mirrors Python's dedup.normalize_role. */
export function normalizeRole(raw: string): string {
  let text = (raw || "").trim().toLowerCase();
  // Keep the two technology names that are not plain words.
  text = text.replace(/c\+\+/g, "__cpp__").replace(/c#/g, "__csharp__");
  text = text.replace(/[^\p{L}\p{N}_\s]/gu, " ");
  text = text.replace(/__cpp__/g, "c++").replace(/__csharp__/g, "c#");
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((token) => ROLE_TOKEN_EXPANSIONS[token] ?? token)
    .join(" ");
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
 *  The company and the title are normalized the way Python normalizes them
 *  (legal suffixes dropped, role abbreviations expanded), so the Worker — the
 *  runtime that actually applies — collapses the same surface forms Python
 *  does: "Acme, Inc." and "Acme" are one employer, "Sr. Backend Engineer" and
 *  "Senior Backend Engineer" are one role.
 *
 *  Keeps the Worker's own fnv1a hash: only the normalization rules are shared
 *  with Python, because each runtime dedupes its own store and cross-runtime
 *  hash equality buys nothing. */
export function roleFingerprint(company: string, title: string, location: string): string {
  return fnv1a(`${normalizeCompany(company)}|${normalizeRole(title)}|${normalizeLocation(location)}`);
}
