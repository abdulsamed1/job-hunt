// Stable identity + URL normalization (mirrors Python dedup.py).

export function fnv1a(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

const TRACKING = new Set(["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src", "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid"]);

export function normalizeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    u.hash = "";
    for (const k of [...u.searchParams.keys()]) {
      if (TRACKING.has(k.toLowerCase())) u.searchParams.delete(k);
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

export function roleFingerprint(company: string, title: string, location: string): string {
  const norm = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
  return fnv1a(`${norm(company)}|${norm(title)}|${norm(location)}`);
}
