// Generic feed discovery: JSON job APIs (RemoteOK, Remotive, Jobicy,
// Arbeitnow, WeWorkRemotely, Working Nomads, …) and RSS/Atom feeds. Mirrors the
// Python FeedAdapter: a JSON body is used when it parses as one, otherwise the
// text is treated as RSS/Atom. A failed fetch throws (recorded as a source
// failure); a successful non-feed page returns [] (the board is quiet).

import { htmlToText } from "./ats.js";
import { fetchTextStatus, tagText, type RawJob } from "./http.js";

const MAX_JOBS = 120;

function text(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

function pick(item: Record<string, any>, keys: string[]): string {
  for (const key of keys) {
    const value = text(item[key]);
    if (value) return value;
  }
  return "";
}

function isRemoteText(value: string): boolean {
  return /\b(remote|anywhere|worldwide|wfh|work from home|distributed|telecommute|virtual)\b/i.test(value);
}

function jsonItems(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  for (const key of ["jobs", "data", "postings", "results", "offers"]) {
    if (Array.isArray(data[key])) return data[key];
  }
  return [];
}

function fromJsonItem(item: any, name: string): RawJob | null {
  if (!item || typeof item !== "object" || "legal" in item) return null;
  const title = pick(item, ["position", "title", "jobTitle", "name"]);
  const url = pick(item, ["url", "apply_url", "jobUrl", "link", "absolute_url", "slug"]);
  const company = pick(item, ["company", "company_name", "companyName", "organization"]) || name;
  if (!title || !url) return null;
  // `slug` is only a URL when it is already absolute/relative-looking; a bare
  // slug would produce a broken row, so the guard above is paired with this.
  if (!/^https?:\/\//i.test(url) && !url.startsWith("/")) return null;

  const rawLocation = typeof item.location === "string"
    ? item.location
    : text(item.location?.name);
  const location = rawLocation
    || pick(item, ["candidate_required_location", "jobGeo", "candidateLocation", "workplace"]);
  const remote = item.remote === true || isRemoteText(location)
    || /\b(remoteok|remotive|jobicy|arbeitnow|weworkremotely|workingnomads)\b/i.test(name);
  const description = item.description || item.jobDescription || item.description_html || title;
  const posted = item.date ?? item.pubDate ?? item.publication_date ?? item.published
    ?? item.epoch ?? item.created_at ?? item.createdAt ?? "";

  return {
    title,
    company,
    location: location || (remote ? "Remote" : ""),
    url,
    source: name,
    posted_at: String(posted),
    desc: htmlToText(String(description), 4000) || title,
    remote_flag: remote,
    external_id: item.id !== undefined ? String(item.id) : null,
  };
}

function parseJsonFeed(data: any, name: string): RawJob[] {
  const out: RawJob[] = [];
  for (const item of jsonItems(data)) {
    const job = fromJsonItem(item, name);
    if (job) out.push(job);
    if (out.length >= MAX_JOBS) break;
  }
  return out;
}

function parseXmlFeed(text0: string, name: string): RawJob[] {
  const start = text0.search(/<\?xml|<rss|<feed|<rdf/i);
  const xml = start > 0 ? text0.slice(start) : text0;
  const out: RawJob[] = [];
  for (const chunk of xml.match(/<item[\s\S]*?<\/item>|<entry[\s\S]*?<\/entry>/gi) || []) {
    const title = tagText(chunk, "title");
    const url2 = tagText(chunk, "link") || tagText(chunk, "guid") || tagText(chunk, "id");
    if (!title || !url2) continue;
    const company = tagText(chunk, "dc:creator") || tagText(chunk, "author")
      || tagText(chunk, "company") || name;
    out.push({
      title,
      company,
      location: "Remote",
      url: url2,
      source: name,
      posted_at: tagText(chunk, "pubDate") || tagText(chunk, "published") || tagText(chunk, "updated"),
      desc: (tagText(chunk, "description") || tagText(chunk, "summary") || tagText(chunk, "content") || title).slice(0, 2000),
    });
    if (out.length >= MAX_JOBS) break;
  }
  return out;
}

/** Parse a feed body (JSON first, then RSS/Atom). Pure — used by tests. */
export function parseFeedText(name: string, body: string): RawJob[] {
  const trimmed = body.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      return parseJsonFeed(JSON.parse(trimmed), name);
    } catch {
      // Not JSON after all: fall through to the XML parser.
    }
  }
  return parseXmlFeed(trimmed, name);
}

export async function fetchRssFeed(name: string, url: string): Promise<RawJob[]> {
  // Arbeitnow returns a 2.3 MB / 300-item JSON page; the feed cap is larger
  // than the default 1.5 MB page cap. Parsing stops at MAX_JOBS items.
  const res = await fetchTextStatus(url, {
    headers: { Accept: "application/json, application/xml, text/xml, text/plain, */*" },
  }, 3_000_000);
  if (res.status === 0) throw new Error(`feed ${name}: network error`);
  if (res.status !== 200) throw new Error(`feed ${name}: HTTP ${res.status}`);
  if (res.text === null) throw new Error(`feed ${name}: response too large`);
  return parseFeedText(name, res.text);
}
