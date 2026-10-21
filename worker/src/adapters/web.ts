// Generic web-board adapter: JSON-LD first, then server-rendered job links,
// then hydrated state (__NEXT_DATA__ / `var Stash =`). Port of the Python
// `web` adapter, minus the Python-only hashing/dedup (the discover stage owns
// that) and without a browser: a board whose postings only exist after a
// client-side API call is out of scope and reads as an empty source.
//
// Failure semantics match the other adapters: a failed fetch throws (with the
// HTTP status), a page that parses but lists nothing returns [] so the board
// is recorded as yielded-nothing rather than failed.

import { htmlToText } from "./ats.js";
import { fetchTextStatus, type RawJob } from "./http.js";

const MAX_JOBS = 120;
const MAX_JSONLD_DEPTH = 6;
const MAX_OBJECT_DEPTH = 8;

function decodeEntities(s: string): string {
  return s
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => {
      try { return String.fromCodePoint(parseInt(h, 16)); } catch { return ""; }
    })
    .replace(/&#(\d+);/g, (_, d) => {
      try { return String.fromCodePoint(parseInt(d, 10)); } catch { return ""; }
    })
    .replace(/&apos;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&");
}

function absoluteUrl(href: string, base: string): string {
  if (!href) return "";
  try {
    const u = new URL(decodeEntities(href).trim(), base);
    return /^https?:$/i.test(u.protocol) ? u.toString() : "";
  } catch {
    return "";
  }
}

function isoDate(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  const d = new Date(value as any);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString();
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function isRemoteText(text: string): boolean {
  return /\b(remote|anywhere|worldwide|work from home|wfh|distributed)\b/i.test(text);
}

const NOT_A_JOB_PATH = /\/(job-search|jobs\/q|search|category|categories|tags?|blog|articles?|career-advice|salary)(\/|$|[-_?.])/i;

function looksLikeJobUrl(url: string): boolean {
  try {
    const u = new URL(url);
    const path = u.pathname + "/";
    // Listing/navigation pages often sit under /jobs/ too (Dice's /jobs/q-…,
    // CryptoJobSearch's /job-search/…). Only a detail-looking path counts.
    if (NOT_A_JOB_PATH.test(path)) return false;
    return /\/(job|jobs|career|careers|position|positions|opening|openings|vacanc|role|roles)(\/|$|[-_])/i.test(path);
  } catch {
    return false;
  }
}

const SKIP_TITLE = /^(view|see|browse|all|apply|learn|read|more|back|home|search|filter|job board|careers?|jobs?|open positions?|latest jobs?)\b/i;

function jsonLdLocation(value: any): string {
  const parts: string[] = [];
  const locations = Array.isArray(value) ? value : value ? [value] : [];
  for (const loc of locations) {
    if (typeof loc === "string") {
      parts.push(loc);
      continue;
    }
    if (!loc || typeof loc !== "object") continue;
    const address = loc.address;
    if (typeof address === "string") {
      parts.push(address);
    } else if (address && typeof address === "object") {
      const country = typeof address.addressCountry === "string"
        ? address.addressCountry
        : str(address.addressCountry?.name);
      const bits = [address.addressLocality, address.addressRegion, country].map(str).filter(Boolean);
      if (bits.length) parts.push(bits.join(", "));
    }
    if (loc.name) parts.push(str(loc.name));
  }
  const seen = new Set<string>();
  return parts
    .map((p) => p.trim())
    .filter((p) => p && !seen.has(p.toLowerCase()) && seen.add(p.toLowerCase()))
    .join(", ");
}

function fromJsonLd(item: any, sourceName: string, base: string): RawJob | null {
  if (!item || typeof item !== "object") return null;
  const title = str(item.title || item.name).trim();
  if (!title) return null;
  const org = item.hiringOrganization;
  const company = typeof org === "string" ? org : str(org?.name) || sourceName;
  const url = absoluteUrl(str(item.url) || base, base) || base;
  const location = jsonLdLocation(item.jobLocation);
  const remote = /TELECOMMUTE/i.test(str(item.jobLocationType)) || isRemoteText(location);
  const posted = isoDate(item.datePosted || item.dateCreated);
  const desc = htmlToText(str(item.description), 4000);
  const identifier = typeof item.identifier === "object"
    ? str(item.identifier?.value)
    : str(item.identifier);
  return {
    title,
    company,
    location: location || (remote ? "Remote" : ""),
    url,
    source: sourceName,
    posted_at: posted,
    desc,
    remote_flag: remote,
    external_id: identifier || null,
  };
}

function walk(value: any, depth: number, visit: (obj: any) => void): void {
  if (!value || depth > MAX_JSONLD_DEPTH) return;
  if (Array.isArray(value)) {
    for (const item of value) walk(item, depth + 1, visit);
    return;
  }
  if (typeof value !== "object") return;
  visit(value);
  for (const child of Object.values(value)) {
    if (child && typeof child === "object") walk(child, depth + 1, visit);
  }
}

function jsonLdJobs(html: string, sourceName: string, base: string): RawJob[] {
  const out: RawJob[] = [];
  const re = /<script[^>]*type\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    let data: any;
    try {
      data = JSON.parse(decodeEntities(m[1]).trim());
    } catch {
      continue;
    }
    walk(data, 0, (obj) => {
      const type = obj["@type"];
      const types = Array.isArray(type) ? type : [type];
      if (!types.some((t) => String(t).toLowerCase() === "jobposting")) return;
      const job = fromJsonLd(obj, sourceName, base);
      if (job) out.push(job);
    });
    if (out.length) break;
  }
  return out;
}

function anchorJobs(html: string, sourceName: string, base: string): RawJob[] {
  const out: RawJob[] = [];
  const seen = new Set<string>();
  const re = /<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    const url = absoluteUrl(m[1], base);
    if (!url || !looksLikeJobUrl(url)) continue;
    const title = decodeEntities(htmlToText(m[2], 200)).trim();
    if (title.length < 4 || title.length > 140 || SKIP_TITLE.test(title)) continue;
    const key = url.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const window = html.slice(m.index, Math.min(html.length, m.index + 1500));
    const stamp = window.match(/datetime\s*=\s*["']([^"']+)["']/i)?.[1]
      || window.match(/\b(20\d{2}-\d{2}-\d{2}(?:T[\d:.+-]+Z?)?)\b/)?.[1]
      || "";
    const remote = isRemoteText(window.slice(0, 600));
    out.push({
      title,
      company: sourceName,
      location: remote ? "Remote" : "",
      url,
      source: sourceName,
      posted_at: isoDate(stamp),
      desc: title,
      remote_flag: remote,
      external_id: null,
    });
    if (out.length >= MAX_JOBS) break;
  }
  return out;
}

/** Extract the first balanced JSON object that starts at/after `from`. */
function balancedObject(text: string, from: number): string {
  const start = text.indexOf("{", from);
  if (start < 0) return "";
  let depth = 0;
  let quote = "";
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === quote) quote = "";
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; continue; }
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return "";
}

function objectCandidates(root: any, sourceName: string, base: string): RawJob[] {
  const out: RawJob[] = [];
  const seen = new Set<string>();
  walk(root, 0, (obj) => {
    if (out.length >= MAX_JOBS) return;
    const title = str(obj.title || obj.jobTitle || obj.position || obj.name).trim();
    const link = str(obj.url || obj.jobUrl || obj.applyUrl || obj.link || obj.href || obj.absolute_url);
    if (!title || title.length > 160 || !link) return;
    const url = absoluteUrl(link, base);
    if (!url || !looksLikeJobUrl(url)) return;
    const key = url.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    const company = str(obj.company || obj.companyName || obj.organization || obj.hiringOrganization?.name) || sourceName;
    const location = typeof obj.location === "string"
      ? obj.location
      : str(obj.location?.name || obj.location?.city || "");
    const posted = isoDate(obj.datePosted || obj.postedAt || obj.publishedAt || obj.createdAt || obj.created_at);
    out.push({
      title,
      company,
      location,
      url,
      source: sourceName,
      posted_at: posted,
      desc: htmlToText(str(obj.description || obj.descriptionHtml || title), 4000),
      remote_flag: isRemoteText(location) || obj.remote === true,
      external_id: obj.id !== undefined ? str(obj.id) : null,
    });
  });
  return out;
}

function hydratedJobs(html: string, sourceName: string, base: string): RawJob[] {
  const next = html.match(/<script[^>]+id\s*=\s*["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i);
  if (next) {
    try {
      const jobs = objectCandidates(JSON.parse(decodeEntities(next[1]).trim()), sourceName, base);
      if (jobs.length) return jobs;
    } catch { /* malformed hydrated state */ }
  }
  const stash = html.match(/var\s+Stash\s*=/);
  if (stash && stash.index !== undefined) {
    const raw = balancedObject(html, stash.index);
    if (raw) {
      try {
        const jobs = objectCandidates(JSON.parse(raw), sourceName, base);
        if (jobs.length) return jobs;
      } catch { /* malformed Stash payload */ }
    }
  }
  return [];
}

/** Parse one board page: JSON-LD, else server-rendered links, else hydrated state. */
export function parseWebHtml(html: string, sourceName: string, baseUrl: string): RawJob[] {
  const byLd = jsonLdJobs(html, sourceName, baseUrl);
  const jobs = byLd.length
    ? byLd
    : anchorJobs(html, sourceName, baseUrl);
  const finalJobs = jobs.length ? jobs : hydratedJobs(html, sourceName, baseUrl);

  // One posting per canonical URL, first occurrence wins.
  const seen = new Set<string>();
  return finalJobs.filter((j) => {
    const key = j.url.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, MAX_JOBS);
}

export async function fetchWebBoard(sourceName: string, url: string): Promise<RawJob[]> {
  if (!/^https?:\/\//i.test(url || "")) throw new Error(`web board ${sourceName}: no url`);
  const res = await fetchTextStatus(url, {
    headers: {
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });
  if (res.status === 0) throw new Error(`web board ${sourceName}: network error`);
  if (res.status !== 200) throw new Error(`web board ${sourceName}: HTTP ${res.status}`);
  if (res.text === null) throw new Error(`web board ${sourceName}: response too large`);
  return parseWebHtml(res.text, sourceName, url);
}
