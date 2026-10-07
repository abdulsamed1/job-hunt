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

export interface JsonSearchQuery {
  searchUrl: string;
  source: string;
  query: string;
  location: string;
}

/** Generic JSON job-search fetch (freehire mirror of the Python adapter). */
export async function fetchJsonSearchBoard(q: JsonSearchQuery, limit = 40): Promise<RawJob[]> {
  const params = new URLSearchParams({ q: q.query, location: q.location, page: "1" });
  const data: any = await fetchJson(`${q.searchUrl}?${params}`);
  const arr = Array.isArray(data) ? data : data?.jobs || data?.data || data?.results || [];
  const out: RawJob[] = [];
  for (const j of arr) {
    if (out.length >= limit) break;
    const title = String(j?.title || j?.jobTitle || j?.position || "");
    const url = String(j?.url || j?.apply_url || j?.applyUrl || j?.link || "");
    if (!title || !url) continue;
    const company = String(j?.company || j?.companyName || j?.company_name || j?.employer || "Unknown");
    const desc = String(j?.description || j?.jobDescription || j?.content || title);
    out.push({
      title,
      company,
      location: String(j?.location || j?.jobLocation || q.location || "Remote"),
      url,
      source: q.source,
      posted_at: String(j?.posted_at || j?.publishedDate || j?.date || j?.created_at || ""),
      desc,
      external_id: j?.id != null ? `${q.source}-${j.id}` : null,
    });
  }
  return out;
}

export interface BdJobsQuery {
  searchUrl: string;
  query: string;
  location: string;
  hoursOld?: number | null;
}

// Full BDJobs location-code table, kept in parity with
// src/job_hunt/discovery/adapters/bdjobs.py BD_LOCATION_CODES
// (worker/test/boards.test.ts asserts equal key sets).
export const BD_LOCATION_CODES: Record<string, number> = {
  dhaka: 14,
  "dhaka division": 1003,
  faridpur: 16,
  gazipur: 19,
  gopalganj: 20,
  kishoreganj: 29,
  madaripur: 34,
  manikganj: 36,
  munshiganj: 39,
  narayanganj: 43,
  narsingdi: 44,
  rajbari: 53,
  shariatpur: 58,
  tangail: 63,
  chattogram: 10,
  "chattogram division": 1002,
  bandarban: 3,
  brahmanbaria: 1,
  chandpur: 8,
  "cox's bazar": 13,
  cumilla: 12,
  feni: 17,
  khagrachhari: 27,
  lakshmipur: 33,
  noakhali: 48,
  rangamati: 55,
  barishal: 4,
  "barishal division": 1001,
  barguna: 7,
  bhola: 5,
  jhalakathi: 24,
  patuakhali: 51,
  pirojpur: 52,
  khulna: 28,
  "khulna division": 1004,
  bagerhat: 2,
  chuadanga: 11,
  jashore: 23,
  jhenaidah: 25,
  kushtia: 31,
  magura: 35,
  meherpur: 37,
  narail: 42,
  satkhira: 57,
  mymensingh: 40,
  "mymensingh division": 1005,
  jamalpur: 22,
  netrokona: 46,
  sherpur: 59,
  rajshahi: 54,
  "rajshahi division": 1006,
  bogura: 6,
  chapainawabganj: 9,
  joypurhat: 26,
  naogaon: 41,
  natore: 45,
  pabna: 49,
  sirajganj: 60,
  rangpur: 56,
  "rangpur division": 1007,
  dinajpur: 15,
  gaibandha: 18,
  kurigram: 30,
  lalmonirhat: 32,
  nilphamari: 47,
  panchagarh: 50,
  thakurgaon: 64,
  sylhet: 62,
  "sylhet division": 1008,
  habiganj: 21,
  moulvibazar: 38,
  sunamganj: 61,
};

export function bdLocationCode(location: string): number | null {
  const place = (location || "").split(",")[0].trim().toLowerCase();
  if (!place || place === "bangladesh") return null;
  return BD_LOCATION_CODES[place] ?? null;
}

export function bdPostedWithinDays(hoursOld: number | null | undefined): number | null {
  if (!hoursOld || hoursOld <= 0) return null;
  const days = Math.ceil(hoursOld / 24) + 1;
  return days <= 5 ? days : null;
}

/** BDJobs GetJobSearch fetch (mirrors the Python bdjobs adapter params + parsing). */
export async function fetchBdJobs(q: BdJobsQuery, limit = 40): Promise<RawJob[]> {
  const params = new URLSearchParams({
    rpp: "50",
    isPro: "0",
    ToggleJobs: "true",
    isFresher: "false",
    keyword: q.query,
    pg: "1",
  });
  const code = bdLocationCode(q.location);
  if (code !== null) params.set("location", String(code));
  const within = bdPostedWithinDays(q.hoursOld);
  if (within !== null) params.set("postedWithin", String(within));
  const data: any = await fetchJson(`${q.searchUrl}?${params}`);
  if (!data || typeof data !== "object") return [];
  const chunks: any[] = [];
  for (const key of ["data", "premiumData"]) {
    const arr = (data as any)[key];
    if (arr === undefined || arr === null) continue;
    if (!Array.isArray(arr)) throw new Error(`BDJobs: unexpected jobs payload shape: ${key} is not a list`);
    chunks.push(...arr);
  }
  const out: RawJob[] = [];
  for (const j of chunks) {
    if (out.length >= limit) break;
    if (!j || typeof j !== "object") continue;
    const title = String(j.jobTitle || j.title || "");
    const jobId = j.Jobid ?? j.id ?? null;
    const rawUrl = String(j.url || j.apply_url || j.link || "");
    const url = rawUrl || (jobId !== null && String(jobId).trim() ? `https://bdjobs.com/h/details/${String(jobId).trim()}` : "");
    if (!title || !url) continue;
    const company = String(j.companyName || j.company || "Unknown");
    const location = String(j.location || j.jobLocation || q.location || "Bangladesh");
    const desc = String(j.description || j.jobDescription || `${title} at ${company} (${location}).`);
    out.push({
      title,
      company,
      location,
      url,
      source: "bdjobs",
      posted_at: String(j.publishDate || j.pub || j.posted_at || j.deadline || j.date || ""),
      desc,
      external_id: jobId !== null ? `bdjobs-${jobId}` : null,
    });
  }
  return out;
}
