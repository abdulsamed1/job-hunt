// Per-tenant board discovery (Slice 5): Teamtailor RSS, Recruitee offers API,
// Personio XML feed. Mirrors the Python teamtailor/recruitee/personio adapters'
// field mapping. Plain GET + small-parse fetches: no TLS impersonation, no RSA,
// no browser — Worker-runnable; the tenant host is derived from the source URL.

import { fetchJson, fetchText, tagText, type RawJob } from "./http.js";
import { htmlToText } from "./ats.js";

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function schemeOf(url: string): string {
  try {
    return new URL(url).protocol || "https:";
  } catch {
    return "https:";
  }
}

function validScheme(url: string): boolean {
  const scheme = schemeOf(url);
  return scheme === "http:" || scheme === "https:";
}

function isTeamtailorHost(host: string): boolean {
  return !!host && (host === "teamtailor.com" || host.endsWith(".teamtailor.com"));
}

function isRecruiteeHost(host: string): boolean {
  return !!host && (host === "recruitee.com" || host.endsWith(".recruitee.com"));
}

function isPersonioHost(host: string): boolean {
  return !!host && (host === "jobs.personio.de" || host.endsWith(".jobs.personio.de"));
}

/** Uniform description pipeline (mirrors Python clean_description): unescape,
 * strip HTML tags, collapse whitespace, truncate to 4000 chars. Hash/store
 * the result of this. */
function cleanDesc(raw: string, fallback: string): string {
  return htmlToText(raw || "", 4000) || fallback;
}

/** Teamtailor tenant feed: <host>/jobs.rss with <item> blocks (mirrors Python _feed_url). */
export function teamtailorFeedUrl(sourceUrl: string): string {
  if (!sourceUrl || !validScheme(sourceUrl)) return "";
  // Host-suffix validation first: only *.teamtailor.com (multi-level allowed).
  // The .rss passthrough is validated too — an evil host with a /jobs.rss
  // path must never become a fetch URL.
  if (!isTeamtailorHost(hostOf(sourceUrl))) return "";
  if (sourceUrl.toLowerCase().endsWith(".rss")) return sourceUrl;
  const host = hostOf(sourceUrl);
  if (!host) return "";
  return `${schemeOf(sourceUrl)}//${host}/jobs.rss`;
}

export async function fetchTeamtailor(sourceUrl: string, name: string, limit = 40): Promise<RawJob[]> {
  const feedUrl = teamtailorFeedUrl(sourceUrl);
  if (!feedUrl) return [];
  const text = await fetchText(feedUrl);
  if (!text) return [];
  const out: RawJob[] = [];
  for (const chunk of text.match(/<item[\s\S]*?<\/item>/gi) || []) {
    if (out.length >= limit) break;
    const title = tagText(chunk, "title");
    const url = tagText(chunk, "link") || tagText(chunk, "guid");
    if (!title || !url) continue;
    const city = tagText(chunk, "tt:city") || tagText(chunk, "city");
    const country = tagText(chunk, "tt:country") || tagText(chunk, "country");
    const locName = tagText(chunk, "tt:name");
    const parts = [city, country].filter(Boolean);
    let location = parts.join(", ") || locName;
    const remoteStatus = tagText(chunk, "remoteStatus");
    if (/remote/i.test(remoteStatus)) location = location ? `${location} (Remote)` : "Remote";
    out.push({
      title,
      company: name,
      location,
      url,
      source: name,
      posted_at: tagText(chunk, "pubDate"),
      desc: cleanDesc(tagText(chunk, "description"), title),
      external_id: tagText(chunk, "guid") || null,
    });
  }
  return out;
}

/** Recruitee tenant API: <slug>.recruitee.com/api/offers/ -> {"offers": [...]} (mirrors Python _api_url). */
export function recruiteeApiUrl(sourceUrl: string): string {
  if (!sourceUrl || !validScheme(sourceUrl)) return "";
  const host = hostOf(sourceUrl);
  if (!isRecruiteeHost(host)) return "";
  if (sourceUrl.includes("/api/offers")) return sourceUrl;
  const suffix = ".recruitee.com";
  if (!host.endsWith(suffix)) return "";
  const slug = host.slice(0, -suffix.length).split(".")[0];
  if (!slug) return "";
  return `https://${slug}.recruitee.com/api/offers/`;
}

export async function fetchRecruitee(sourceUrl: string, name: string, limit = 40): Promise<RawJob[]> {
  const apiUrl = recruiteeApiUrl(sourceUrl);
  if (!apiUrl) return [];
  const data: any = await fetchJson(apiUrl);
  const offers = data && Array.isArray(data.offers) ? data.offers : null;
  if (!offers) return [];
  const out: RawJob[] = [];
  for (const o of offers) {
    if (out.length >= limit) break;
    if (!o || typeof o !== "object") continue;
    const title = String(o.title || o?.translations?.en?.title || "");
    const url = String(o.careers_url || "");
    if (!title || !url) continue;
    const parts: string[] = [];
    if (o.remote) parts.push("Remote");
    for (const key of ["city", "country"]) {
      const v = String(o[key] || "").trim();
      if (v && v.toLowerCase() !== "remote" && !parts.includes(v)) parts.push(v);
    }
    if (parts.length === 0 && Array.isArray(o.locations) && o.locations[0]?.name) {
      parts.push(String(o.locations[0].name).trim());
    }
    if (parts.length === 0 && o.location) parts.push(String(o.location).trim());
    const desc = cleanDesc(
      [o.description, o.requirements].filter(Boolean).map((d) => String(d)).join("\n"),
      title,
    );
    out.push({
      title,
      company: String(o.company_name || name),
      location: parts.join(", "),
      url,
      source: name,
      posted_at: String(o.published_at || ""),
      desc,
      external_id: o.id != null ? String(o.id) : null,
    });
  }
  return out;
}

/** Personio tenant feed: <tenant>.jobs.personio.de/xml with <position> blocks (mirrors Python adapter). */
export function personioFeedUrl(sourceUrl: string): string {
  if (!sourceUrl || !validScheme(sourceUrl)) return "";
  // Host-suffix validation: only *.jobs.personio.de (the /xml passthrough
  // is validated too — an evil host must never qualify).
  if (!isPersonioHost(hostOf(sourceUrl))) return "";
  if (sourceUrl.toLowerCase().replace(/\/$/, "").endsWith("/xml")) return sourceUrl;
  const host = hostOf(sourceUrl);
  if (!host) return "";
  return `${schemeOf(sourceUrl)}//${host}/xml`;
}

export async function fetchPersonio(sourceUrl: string, name: string, limit = 40): Promise<RawJob[]> {
  const feedUrl = personioFeedUrl(sourceUrl);
  if (!feedUrl) return [];
  const host = hostOf(feedUrl);
  if (!host) return [];
  const text = await fetchText(feedUrl);
  if (!text) return [];
  if (!/<position[\s>]/i.test(text)) return [];
  const out: RawJob[] = [];
  for (const block of text.match(/<position[\s\S]*?<\/position>/gi) || []) {
    if (out.length >= limit) break;
    // Strip the <jobDescriptions> subtree first: embedded raw markup breaks
    // naive XML parsing (unclosed CDATA) — same reason as the Python adapter.
    const cleaned = block.replace(/<jobDescriptions[\s\S]*?<\/jobDescriptions>/gi, "");
    const id = tagText(cleaned, "id");
    const title = tagText(cleaned, "name");
    if (!id || !title) continue;
    const values: string[] = [];
    for (const m of block.match(/<value>([\s\S]*?)<\/value>/gi) || []) {
      const t = htmlToText(m, 4000);
      if (t) values.push(t);
    }
    const office = tagText(cleaned, "office");
    const department = tagText(cleaned, "department");
    const company = tagText(cleaned, "subcompany") || name;
    out.push({
      title,
      company,
      location: office,
      url: `https://${host}/job/${id}`,
      source: name,
      posted_at: tagText(cleaned, "createdAt"),
      desc: cleanDesc(
        values.join("\n"),
        `${title} — ${department} (${office})`.replace(/^[ —()]+|[ —()]+$/g, "") || title,
      ),
      external_id: id,
    });
  }
  return out;
}
