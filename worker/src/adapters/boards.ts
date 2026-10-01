// JSON-board discovery: RemoteOK, Remotive, Arbeitnow, Jobicy + LinkedIn guest.
// Mirrors the Python feed/LinkedIn adapters' field mapping.

import { fetchJson, fetchText, type RawJob } from "./http.js";

const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0 Safari/537.36";

export async function fetchRemoteOK(limit = 30): Promise<RawJob[]> {
  const data: any = await fetchJson("https://remoteok.com/api");
  if (!Array.isArray(data)) return [];
  const out: RawJob[] = [];
  for (const j of data) {
    if (!j || typeof j !== "object" || !j.position) continue;
    if (out.length >= limit) break;
    out.push({
      title: String(j.position),
      company: String(j.company || ""),
      location: String(j.location || "Remote"),
      url: String(j.url || `https://remoteok.com/l/${j.id ?? ""}`),
      source: "remoteok",
      posted_at: String(j.date || ""),
      desc: `${j.position} at ${j.company || ""}. ${(j.description || "").slice(0, 500)}`,
    });
  }
  return out;
}

export async function fetchRemotive(limit = 30): Promise<RawJob[]> {
  const data: any = await fetchJson(
    "https://remotive.com/api/remote-jobs?category=software-dev&limit=50",
  );
  const out: RawJob[] = [];
  for (const j of (data && data.jobs) || []) {
    if (out.length >= limit) break;
    out.push({
      title: String(j.title || ""),
      company: String(j.company_name || ""),
      location: String(j.candidate_required_location || "Remote"),
      url: String(j.url || ""),
      source: "remotive",
      posted_at: String(j.publication_date || ""),
      desc: `${j.title} at ${j.company_name}. ${(j.description || "").replace(/<[^>]+>/g, " ").slice(0, 500)}`,
    });
  }
  return out.filter((j) => j.title && j.url);
}

export async function fetchArbeitnow(limit = 30): Promise<RawJob[]> {
  const data: any = await fetchJson("https://www.arbeitnow.com/api/job-board-api");
  const arr = Array.isArray(data) ? data : data?.data || [];
  const out: RawJob[] = [];
  for (const j of arr) {
    if (out.length >= limit) break;
    if (!j?.title || !j?.url) continue;
    out.push({
      title: String(j.title),
      company: String(j.company_name || ""),
      location: String(j.location || "Remote"),
      url: String(j.url),
      source: "arbeitnow",
      posted_at: String(j.created_at || ""),
      desc: `${j.title} at ${j.company_name || ""}. ${(j.description || "").slice(0, 500)}`,
    });
  }
  return out;
}

export async function fetchJobicy(limit = 30): Promise<RawJob[]> {
  const data: any = await fetchJson("https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa&industry=dev");
  const arr = Array.isArray(data) ? data : data?.jobs || [];
  const out: RawJob[] = [];
  for (const j of arr) {
    if (out.length >= limit) break;
    if (!j?.jobTitle || !j?.url) continue;
    out.push({
      title: String(j.jobTitle),
      company: String(j.companyName || ""),
      location: String(j.jobLocation || "Remote"),
      url: String(j.url),
      source: "jobicy",
      posted_at: String(j.pubDate || ""),
      desc: `${j.jobTitle} at ${j.companyName || ""}. ${(j.jobDescription || "").replace(/<[^>]+>/g, " ").slice(0, 500)}`,
    });
  }
  return out;
}

export async function fetchLinkedInGuest(
  keywords: string, location: string, pages: number, tprSeconds: number | null,
): Promise<RawJob[]> {
  // Standing target mirror: backend/fullstack/software, past-12h (f_TPR=r43200).
  const out: RawJob[] = [];
  for (let page = 0; page < pages; page++) {
    const params = new URLSearchParams({
      keywords, location, start: String(page * 10), count: "10",
    });
    if (tprSeconds) params.set("f_TPR", `r${tprSeconds}`);
    const html = await fetchText(
      `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?${params}`,
      {
        headers: {
          "User-Agent": UA,
          Accept: "text/html,application/xhtml+xml",
          "Accept-Language": "en-US,en;q=0.9",
          Referer: "https://www.linkedin.com/jobs/",
        },
      },
    );
    if (!html) break;
    const cards = html.match(/<li[\s\S]*?<\/li>/gi) || [];
    if (cards.length === 0) break;
    for (const card of cards) {
      const pick = (re: RegExp) => {
        const m = card.match(re);
        return m ? m[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : "";
      };
      const href =
        pick(/<a[^>]+class="[^"]*base-card__full-link[^"]*"[^>]+href="([^"]+)"/i) ||
        pick(/<a[^>]+href="([^"]+)"[^>]*class="[^"]*base-card__full-link/i);
      const title = pick(/<h3[^>]*>([\s\S]*?)<\/h3>/i);
      if (!title || !href) continue;
      const company = pick(/<h4[^>]*>([\s\S]*?)<\/h4>/i);
      const urn = card.match(/data-entity-urn="urn:li:jobPosting:(\d+)"/);
      out.push({
        title,
        company: company || "LinkedIn Employer",
        location: pick(/job-search-card__location[^>]*>([\s\S]*?)<\/span>/i) || "Remote",
        url: href.split("?")[0],
        source: "linkedin",
        posted_at: pick(/<time[^>]+datetime="([^"]+)"/i),
        desc: `${title} at ${company}. Discovered via LinkedIn (worker shard).`,
        external_id: urn ? urn[1] : null,
      });
    }
  }
  return out;
}
