/**
 * jobhunt-scout: hourly 24/7 discovery shard on Cloudflare Workers (free tier).
 *
 * Fetches only cheap stateless sources: a few JSON/RSS job APIs plus one page
 * per query of LinkedIn's public guest endpoint (same standing target as the
 * Python pipeline: backend/fullstack/software, past-12h window). Normalizes,
 * keyword-scores, and upserts into D1 (idempotent on canonical hash).
 * Optionally Telegram-alerts on fresh high-score remote matches.
 *
 * Budget: ~30 requests/hour ≈ 720/day (free quota: 100k/day). D1 writes only
 * for genuinely new jobs. No browser, no PDF, no LLM here — heavy stages run
 * in GitHub Actions / locally and read the same logical pipeline.
 */

export interface Env {
  DB: D1Database;
  QUERIES?: string;
  ALERT_MIN_SCORE?: string;
  REZI_MCP_TOKEN?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
}

const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0 Safari/537.36";

const KEYWORDS = ["backend", "fullstack", "full-stack", "full stack", "software", "python", "rust", "devops"];
const REMOTE_SIGNS = ["remote", "worldwide", "anywhere", "wfh", "distributed"];

const FEEDS: Array<{ name: string; url: string; kind: "rss" | "json-remoteok" | "json-remotive" }> = [
  { name: "cryptojobslist", url: "https://api.cryptojobslist.com/jobs.rss", kind: "rss" },
  { name: "crypto.jobs", url: "https://crypto.jobs/feed/rss", kind: "rss" },
  { name: "weworkremotely", url: "https://weworkremotely.com/remote-jobs.rss", kind: "rss" },
  { name: "tokyodev", url: "https://www.tokyodev.com/atom.xml", kind: "rss" },
  { name: "remoteok", url: "https://remoteok.com/api", kind: "json-remoteok" },
  { name: "remotive", url: "https://remotive.com/api/remote-jobs?category=software-dev&limit=50", kind: "json-remotive" },
];

function hashCanon(s: string): string {
  // FNV-1a 32-bit: dependency-free stable identity for dedup.
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

function normUrl(u: string): string {
  try {
    const x = new URL(u);
    x.search = "";
    x.hash = "";
    return x.toString();
  } catch {
    return u;
  }
}

function score(title: string, desc: string): { score: number; remote: boolean } {
  const hay = `${title} ${desc}`.toLowerCase();
  let score = 0;
  for (const k of KEYWORDS) if (hay.includes(k)) score++;
  const remote = REMOTE_SIGNS.some((s) => hay.includes(s));
  if (remote) score++;
  return { score, remote };
}

function tagText(xml: string, tag: string): string {
  const m = xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "i"));
  if (!m) return "";
  return m[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

interface RawJob {
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
  posted_at: string;
  desc: string;
}

async function fetchFeed(f: (typeof FEEDS)[number]): Promise<RawJob[]> {
  const res = await fetch(f.url, { headers: { "User-Agent": UA, Accept: "application/json, text/xml, */*" } });
  if (!res.ok) return [];
  const out: RawJob[] = [];
  if (f.kind === "rss") {
    const text = await res.text();
    for (const chunk of text.match(/<item[\s\S]*?<\/item>|<entry[\s\S]*?<\/entry>/gi) || []) {
      const title = tagText(chunk, "title");
      const url = tagText(chunk, "link") || tagText(chunk, "guid") || tagText(chunk, "id");
      if (!title || !url) continue;
      out.push({
        title,
        company: tagText(chunk, "dc:creator") || tagText(chunk, "author") || f.name,
        location: "Remote",
        url,
        source: f.name,
        posted_at: tagText(chunk, "pubDate") || tagText(chunk, "published") || tagText(chunk, "updated"),
        desc: (tagText(chunk, "description") || tagText(chunk, "summary") || title).slice(0, 2000),
      });
    }
  } else if (f.kind === "json-remoteok") {
    const data: any = await res.json();
    for (const j of Array.isArray(data) ? data : []) {
      if (!j || typeof j !== "object" || !j.position) continue;
      out.push({
        title: String(j.position),
        company: String(j.company || ""),
        location: String(j.location || "Remote"),
        url: String(j.url || `https://remoteok.com/l/${j.id}`),
        source: f.name,
        posted_at: String(j.date || ""),
        desc: `${j.position} at ${j.company || ""}. ${(j.description || "").slice(0, 500)}`,
      });
    }
  } else {
    const data: any = await res.json();
    for (const j of (data && data.jobs) || []) {
      out.push({
        title: String(j.title || ""),
        company: String(j.company_name || ""),
        location: String(j.candidate_required_location || "Remote"),
        url: String(j.url || ""),
        source: f.name,
        posted_at: String(j.publication_date || ""),
        desc: `${j.title} at ${j.company_name}. ${(j.description || "").replace(/<[^>]+>/g, " ").slice(0, 500)}`,
      });
    }
  }
  return out;
}

async function fetchLinkedIn(queries: string[]): Promise<RawJob[]> {
  // Standing target mirror: 1 page per query, past-12h window (f_TPR=r43200).
  const out: RawJob[] = [];
  for (const kw of queries) {
    const url =
      `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` +
      `?keywords=${encodeURIComponent(kw)}&location=Remote&start=0&count=10&f_TPR=r43200`;
    const res = await fetch(url, {
      headers: {
        "User-Agent": UA,
        Accept: "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        Referer: "https://www.linkedin.com/jobs/",
      },
    });
    if (!res.ok) continue;
    const html = await res.text();
    for (const card of html.match(/<li[\s\S]*?<\/li>/gi) || []) {
      const pick = (re: RegExp) => {
        const m = card.match(re);
        return m ? m[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : "";
      };
      const href = pick(/<a[^>]+class="[^"]*base-card__full-link[^"]*"[^>]+href="([^"]+)"/i)
        || pick(/<a[^>]+href="([^"]+)"[^>]*class="[^"]*base-card__full-link/i);
      const title = pick(/<h3[^>]*>([\s\S]*?)<\/h3>/i);
      const company = pick(/<h4[^>]*>([\s\S]*?)<\/h4>/i);
      const location = pick(/job-search-card__location[^>]*>([\s\S]*?)<\/span>/i);
      const urn = card.match(/data-entity-urn="urn:li:jobPosting:(\d+)"/);
      if (!title || !href) continue;
      out.push({
        title, company: company || "LinkedIn Employer", location: location || "Remote",
        url: href.split("?")[0], source: "linkedin",
        posted_at: pick(/<time[^>]+datetime="([^"]+)"/i),
        desc: `${title} at ${company}. Discovered via LinkedIn (worker shard).`,
      });
      void urn;
    }
  }
  return out;
}

async function alert(env: Env, text: string): Promise<void> {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: text.slice(0, 3000) }),
  });
}

async function runShard(env: Env): Promise<{ fetched: number; fresh: number; alerts: number }> {
  const queries = (env.QUERIES || "backend,fullstack,software").split(",").map((s) => s.trim()).filter(Boolean);
  const minScore = parseInt(env.ALERT_MIN_SCORE || "3", 10);
  const now = new Date().toISOString();
  let fetched = 0;
  let fresh = 0;
  let alerts = 0;

  const batches: RawJob[][] = [];
  for (const f of FEEDS) {
    try {
      batches.push(await fetchFeed(f));
    } catch {
      batches.push([]);
    }
  }
  try {
    batches.push(await fetchLinkedIn(queries));
  } catch {
    batches.push([]);
  }

  const alertLines: string[] = [];
  for (const job of batches.flat()) {
    fetched++;
    const canon = normUrl(job.url);
    const { score, remote } = score(job.title, `${job.company} ${job.location} ${job.desc}`);
    const res = await env.DB.prepare(
      `INSERT INTO jobs (canonical_hash, title, company, location, url, source, posted_at, discovered_at, score, remote, notified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
       ON CONFLICT (canonical_hash) DO NOTHING`
    ).bind(
      hashCanon(canon), job.title.slice(0, 200), job.company.slice(0, 120),
      job.location.slice(0, 120), job.url.slice(0, 500), job.source,
      job.posted_at.slice(0, 40), now, score, remote ? 1 : 0,
    ).run();
    if ((res.meta.changes ?? 0) > 0) {
      fresh++;
      if (remote && score >= minScore) {
        alertLines.push(`⭐ ${job.title} @ ${job.company} (${job.location})\n${job.url}`);
        await env.DB.prepare(`UPDATE jobs SET notified = 1 WHERE canonical_hash = ?`).bind(hashCanon(canon)).run();
        alerts++;
      }
    }
  }
  if (alertLines.length > 0) {
    await alert(env, `🎯 ${alertLines.length} fresh remote match(es):\n\n${alertLines.slice(0, 8).join("\n\n")}`);
  }
  return { fetched, fresh, alerts };
}

export default {
  async scheduled(_event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
    const r = await runShard(env);
    console.log(`scout shard done: fetched=${r.fetched} fresh=${r.fresh} alerts=${r.alerts}`);
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      const row: any = await env.DB.prepare(`SELECT COUNT(*) AS n FROM jobs`).first();
      return Response.json({ ok: true, jobs: row?.n ?? 0 });
    }
    if (url.pathname === "/recent") {
      const { results } = await env.DB.prepare(
        `SELECT title, company, location, url, source, score, discovered_at FROM jobs ORDER BY discovered_at DESC LIMIT 50`
      ).all();
      return Response.json({ jobs: results });
    }
    if (url.pathname === "/run" && request.method === "POST") {
      const r = await runShard(env);
      return Response.json(r);
    }
    return new Response("jobhunt-scout: /health /recent /run", { status: 200 });
  },
};
