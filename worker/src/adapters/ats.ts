// ATS-board discovery: Greenhouse, Lever, Ashby, SmartRecruiters public APIs.
// Mirrors the Python ATS adapters. Small JSON responses fit the CPU budget.

import { fetchJson, fetchText, type RawJob } from "./http.js";

export async function fetchGreenhouseBoard(org: string, limit = 40): Promise<RawJob[]> {
  const data: any = await fetchJson(`https://boards-api.greenhouse.io/v1/boards/${org}/jobs`);
  const out: RawJob[] = [];
  for (const j of (data && data.jobs) || []) {
    if (out.length >= limit) break;
    if (!j?.title || !j?.absolute_url) continue;
    const loc = j.location?.name || "Remote";
    out.push({
      title: String(j.title),
      company: org,
      location: loc,
      url: String(j.absolute_url),
      source: `greenhouse:${org}`,
      posted_at: String(j.updated_at || ""),
      desc: `${j.title} at ${org}. Location: ${loc}.`,
      external_id: String(j.id ?? ""),
    });
  }
  return out;
}

export async function fetchLeverBoard(org: string, limit = 40): Promise<RawJob[]> {
  const data: any = await fetchJson(`https://api.lever.co/v0/postings/${org}?mode=json`);
  const out: RawJob[] = [];
  for (const j of Array.isArray(data) ? data : []) {
    if (out.length >= limit) break;
    if (!j?.text || !j?.hostedUrl) continue;
    const loc = j.categories?.location || "Remote";
    out.push({
      title: String(j.text),
      company: org,
      location: loc,
      url: String(j.hostedUrl),
      source: `lever:${org}`,
      posted_at: j.createdAt ? new Date(j.createdAt).toISOString() : "",
      desc: `${j.text} at ${org}. ${(j.descriptionPlain || "").slice(0, 800)}`,
      external_id: String(j.id ?? ""),
    });
  }
  return out;
}

export async function fetchAshbyBoard(org: string, limit = 40): Promise<RawJob[]> {
  const data: any = await fetchJson(
    `https://api.ashbyhq.com/posting-api/job-board/${org}?includeCompensation=true`,
  );
  const out: RawJob[] = [];
  for (const j of (data && data.jobs) || []) {
    if (out.length >= limit) break;
    if (!j?.title) continue;
    const url = j.jobUrl || `https://jobs.ashbyhq.com/${org}/${j.id ?? ""}`;
    const loc = j.location || (j.isRemote ? "Remote" : "Unknown");
    out.push({
      title: String(j.title),
      company: j.companyName || org,
      location: loc,
      url: String(url),
      source: `ashby:${org}`,
      posted_at: String(j.publishedAt || ""),
      desc: `${j.title}. ${(j.descriptionHtml || "").replace(/<[^>]+>/g, " ").slice(0, 800)}`,
      external_id: String(j.id ?? ""),
      remote_flag: j.isRemote === true,
    });
  }
  return out;
}

export async function fetchSmartRecruitersBoard(org: string, limit = 40): Promise<RawJob[]> {
  const data: any = await fetchJson(
    `https://api.smartrecruiters.com/v1/companies/${org}/postings?limit=100`,
  );
  const out: RawJob[] = [];
  for (const j of (data && data.content) || []) {
    if (out.length >= limit) break;
    if (!j?.name) continue;
    const loc = j.location?.city
      ? `${j.location.city}, ${j.location.country || ""}`.replace(/,\s*$/, "")
      : "Remote";
    out.push({
      title: String(j.name),
      company: org,
      location: loc,
      url: String(j.ref || `https://jobs.smartrecruiters.com/${org}/${j.id ?? ""}`),
      source: `smartrecruiters:${org}`,
      posted_at: String(j.releasedDate || ""),
      desc: `${j.name} at ${org}. Location: ${loc}.`,
      external_id: String(j.id ?? ""),
    });
  }
  return out;
}

/** Extract unique Ashby org slugs from an aggregator index page, first-seen order. */
export function extractAshbyOrgs(html: string): string[] {
  const seen: string[] = [];
  const re = /https?:\/\/jobs\.ashbyhq\.com\/([A-Za-z0-9_-]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const slug = m[1].toLowerCase();
    if (!seen.includes(slug)) seen.push(slug);
  }
  return seen;
}

/** Fan out an Ashby-powered aggregator index (e.g. jobs.solana.com) to per-org boards. */
export async function fetchAshbyIndex(indexUrl: string, maxOrgs = 8, perOrgLimit = 20): Promise<RawJob[]> {
  const html = (await fetchText(indexUrl, { headers: { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)" } })) || "";
  const orgs = extractAshbyOrgs(html).slice(0, Math.max(1, maxOrgs));
  const out: RawJob[] = [];
  for (const org of orgs) {
    try {
      out.push(...(await fetchAshbyBoard(org, perOrgLimit)));
    } catch {
      // one dead org must not sink the whole index
    }
  }
  return out;
}

export interface IndeedQuery {
  query: string;
  location: string;
  country?: string;
}

/** Indeed GraphQL search (guest, no login). One POST per query. */
export async function fetchIndeed(q: IndeedQuery, limit = 40): Promise<RawJob[]> {
  const where = q.country ? `${q.location};${q.country}` : q.location;
  const body = {
    operationName: "JobSearch",
    variables: { where: { query: q.query, location: where }, size: limit },
    query:
      "query JobSearch($where: JobSearchCriteriaInput!, $size: Int!) { jobSearch(where: $where, size: $size) { jobs { id title companyName { text } locations { countryName { text } city } publicationDate { date } absoluteUrl } } }",
  };
  const data = await fetchJson("https://eg.indeed.com/graphql", {
    method: "POST",
    headers: { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)" },
    body: JSON.stringify(body),
  });
  const out: RawJob[] = [];
  for (const j of data?.data?.jobSearch?.jobs || []) {
    if (!j?.title) continue;
    const city = j?.locations?.[0]?.city || "";
    const countryName = j?.locations?.[0]?.countryName?.text || q.country || "";
    const loc = [city, countryName].filter(Boolean).join(", ") || "Unknown";
    out.push({
      title: String(j.title),
      company: j?.companyName?.text || "Unknown",
      location: loc,
      url: String(j.absoluteUrl || ""),
      source: "indeed",
      posted_at: j?.publicationDate?.date ? new Date(j.publicationDate.date).toISOString() : "",
      desc: String(j.title),
      external_id: String(j.id ?? ""),
    });
  }
  return out;
}

/** Strip HTML (including double-escaped entities) down to plain text. */
export function htmlToText(raw: string, max = 4000): string {
  return raw
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/&nbsp;/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

/** Greenhouse omits `content` from the board list; fetch it per job. */
export async function fetchGreenhouseJobText(org: string, jobId: string, max = 3000): Promise<string> {
  const data: any = await fetchJson(
    `https://boards-api.greenhouse.io/v1/boards/${org}/jobs/${jobId}?content=true`,
  );
  return htmlToText(String(data?.content || ""), max);
}

/**
 * Board list endpoints omit full descriptions (greenhouse/smartrecruiters), so
 * scoring would see a title-only string and reject everything for lack of
 * evidence. Enrich the handful of jobs that survived the freshness/remote
 * filters: typically 0-3 per source, which keeps us far under the 50-subrequest
 * ceiling. Lever/Ashby already ship full text in the board response.
 */
export async function enrichDescriptions(jobs: RawJob[], maxEnrich = 12): Promise<RawJob[]> {
  let budget = maxEnrich;
  const out: RawJob[] = [];
  for (const j of jobs) {
    if (budget <= 0 || (j.desc || "").length >= 400) {
      out.push(j);
      continue;
    }
    const gh = j.source.match(/^greenhouse:(.+)$/);
    const sr = j.source.match(/^smartrecruiters:(.+)$/);
    try {
      if (gh && j.external_id) {
        const text = await fetchGreenhouseJobText(gh[1], j.external_id);
        if (text.length > (j.desc || "").length) j.desc = `${j.desc || ""} ${text}`.trim();
        budget -= 1;
      } else if (sr && j.external_id) {
        const data: any = await fetchJson(
          `https://api.smartrecruiters.com/v1/companies/${sr[1]}/postings/${j.external_id}`,
        );
        const sections = data?.jobAd?.sections || {};
        const text = htmlToText(
          Object.values(sections).map((s: any) => s?.text || s?.description || "").join(" "),
        );
        if (text.length > (j.desc || "").length) j.desc = `${j.desc || ""} ${text}`.trim();
        budget -= 1;
      }
    } catch {
      // enrichment is best-effort; never lose the job over it
    }
    out.push(j);
  }
  return out;
}
