// Bayt.com (MENA) adapter: public search HTML, per-card metadata. Port of the
// Python adapter. The Python side used TLS impersonation because Bayt's edge
// blocks plain clients; a Worker fetch cannot impersonate TLS, so this is a
// best-effort plain fetch. A block reads as a failed source (flaky), never as
// an empty board.

import { htmlToText } from "./ats.js";
import { fetchTextStatus, type RawJob } from "./http.js";

const BASE_URL = "https://www.bayt.com";
const JOBS_PER_PAGE = 20;

const COUNTRY_ALIASES: Record<string, string> = {
  uae: "uae",
  "united arab emirates": "uae",
  dubai: "uae",
  egypt: "egypt",
  cairo: "egypt",
  "saudi arabia": "saudi-arabia",
  saudi: "saudi-arabia",
  riyadh: "saudi-arabia",
  qatar: "qatar",
  doha: "qatar",
  kuwait: "kuwait",
  oman: "oman",
  bahrain: "bahrain",
  jordan: "jordan",
  amman: "jordan",
  lebanon: "lebanon",
  morocco: "morocco",
  remote: "remote",
};

// Server-side buckets, coarsest first; the client re-filters to exact hoursOld.
const DATE_INTERVALS: Array<[number, number]> = [[24, 3], [24 * 7, 2], [24 * 30, 1]];

function slugify(text: string): string {
  return encodeURIComponent(text.toLowerCase().split(/\s+/).filter(Boolean).join("-"));
}

export function parseBaytLocation(location: string): { country: string; city: string | null } {
  const parts = String(location || "").split(",").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return { country: "international", city: null };
  const country = COUNTRY_ALIASES[parts[parts.length - 1].toLowerCase()] || slugify(parts[parts.length - 1]);
  let city: string | null = parts.length > 1 ? slugify(parts[0]) : null;
  if (city && city === country) city = null;
  return { country, city };
}

export function baytSearchUrl(
  query: string, location: string, page: number, hoursOld: number | null, remoteOnly: boolean,
): string {
  const { country, city } = parseBaytLocation(location);
  const slug = slugify(query || "");
  const path = !slug ? "" : city ? `${slug}-jobs-in-${city}/` : `${slug}-jobs/`;
  const params = new URLSearchParams();
  if (hoursOld) {
    const interval = DATE_INTERVALS.find(([hours]) => hoursOld <= hours)?.[1];
    if (interval) params.set("filters[jb_last_modification_date_interval][]", String(interval));
  }
  if (remoteOnly) params.set("filters[remote_working_type][]", "1");
  params.set("page", String(page));
  return `${BASE_URL}/en/${country}/jobs/${path}?${params.toString()}`;
}

function stripTags(html: string): string {
  return htmlToText(html, 400);
}

function parseSalary(text: string): { min: number | null; max: number | null; currency: string | null } {
  const m = text.match(/([A-Z]{3}|\$)?\s*([\d,]+(?:\.\d+)?)\s*-\s*(?:[A-Z]{3}|\$)?\s*([\d,]+(?:\.\d+)?)/);
  if (!m) return { min: null, max: null, currency: null };
  const currency = m[1] ? (m[1] === "$" ? "USD" : m[1]) : null;
  return {
    min: Number(m[2].replace(/,/g, "")),
    max: Number(m[3].replace(/,/g, "")),
    currency,
  };
}

export function parseBaytCards(html: string, hoursOld: number | null, nowMs = Date.now()): RawJob[] {
  const starts: Array<{ index: number; tag: string }> = [];
  const re = /<li\b[^>]*\bdata-js-job\b[^>]*>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) starts.push({ index: m.index, tag: m[0] });
  const out: RawJob[] = [];
  for (let i = 0; i < starts.length; i++) {
    const chunk = html.slice(starts[i].index, i + 1 < starts.length ? starts[i + 1].index : html.length);
    const link = chunk.match(/<h2\b[^>]*>[\s\S]*?<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i);
    if (!link) continue;
    const href = link[1].trim().split("?")[0];
    const title = stripTags(link[2]);
    if (!title || !href) continue;
    let url: string;
    try { url = new URL(href, BASE_URL).toString(); } catch { continue; }

    const jobId = (starts[i].tag.match(/\bdata-job-id\s*=\s*["']?([^"'\s>]+)/i) || [])[1] || "";
    const stamp = (chunk.match(/data-automation-jobactivedate\s*=\s*["']?(\d+)/i) || [])[1];
    const postedMs = stamp ? Number(stamp) * 1000 : 0;
    if (hoursOld && postedMs && postedMs < nowMs - hoursOld * 3600_000) continue;

    const companyBlock = chunk.match(/<div\b[^>]*job-company-location-wrapper[^>]*>([\s\S]*?)<\/div>/i);
    const companyLink = chunk.match(/<a\b[^>]*href\s*=\s*["'][^"']*\/company\/[^"']*["'][^>]*>([\s\S]*?)<\/a>/i);
    const company = (companyLink ? stripTags(companyLink[1]) : companyBlock ? stripTags(companyBlock[1]) : "")
      || "Bayt Employer";

    const locTag = chunk.match(/<dt\b[^>]*jb-label-location[^>]*>([\s\S]*?)<\/dt>/i);
    const location = locTag ? stripTags(locTag[1]) : "Middle East";
    const salaryTag = chunk.match(/<dt\b[^>]*jb-label-salary[^>]*>([\s\S]*?)<\/dt>/i);
    const salary = parseSalary(salaryTag ? stripTags(salaryTag[1]) : "");
    const isRemote = /jb-label-remote/i.test(chunk);

    out.push({
      title,
      company,
      location,
      url,
      source: "bayt",
      posted_at: postedMs ? new Date(postedMs).toISOString() : "",
      desc: `${title} position at ${company}. Location: ${location}. Discovered via Bayt.`,
      salary_min: salary.min,
      salary_max: salary.max,
      salary_currency: salary.currency,
      remote_flag: isRemote,
      external_id: jobId ? `bayt-${jobId}` : null,
    });
    if (out.length >= JOBS_PER_PAGE) break;
  }
  return out;
}

export async function fetchBayt(opts: {
  query: string;
  location: string;
  hoursOld: number | null;
  remoteOnly?: boolean;
  page?: number;
}): Promise<RawJob[]> {
  const url = baytSearchUrl(
    opts.query, opts.location, opts.page || 1, opts.hoursOld, !!opts.remoteOnly,
  );
  const res = await fetchTextStatus(url, {
    headers: {
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
    },
  });
  if (res.status === 0) throw new Error("Bayt: network error");
  // 404/410 is a clean "page is gone" answer (dead board / moved location);
  // every other non-200 is a failed attempt.
  if (res.status === 404 || res.status === 410) throw new Error(`Bayt: HTTP ${res.status}`);
  if (res.status === 429) throw new Error("Bayt: HTTP 429");
  if (res.status !== 200) throw new Error(`Bayt: HTTP ${res.status}`);
  if (res.text === null) throw new Error("Bayt: response too large");
  return parseBaytCards(res.text, opts.hoursOld);
}
