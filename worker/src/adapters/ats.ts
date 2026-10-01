// ATS-board discovery: Greenhouse, Lever, Ashby, SmartRecruiters public APIs.
// Mirrors the Python ATS adapters. Small JSON responses fit the CPU budget.

import { fetchJson, type RawJob } from "./http.js";

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
