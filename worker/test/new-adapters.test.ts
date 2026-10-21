import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";

import { baytSearchUrl, parseBaytCards } from "../src/adapters/bayt.js";
import { generateNkparam, parseNaukriJob } from "../src/adapters/naukri.js";
import { parseFeedText } from "../src/adapters/rss.js";
import { parseWebHtml } from "../src/adapters/web.js";
import { isInconclusiveProbeError } from "../src/lib/health.js";
import { applyDisposition, manualApplyDetail } from "../src/stages/pipeline.js";

describe("web board adapter", () => {
  it("reads schema.org JobPosting blocks", () => {
    const html = `<script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"Backend Engineer",
       "hiringOrganization":{"name":"Acme"},"url":"https://jobs.example.com/role/1",
       "jobLocation":{"address":{"addressLocality":"Cairo","addressCountry":"EG"}},
       "datePosted":"2026-10-20","description":"<p>Build APIs</p>"}
    </script>`;
    const jobs = parseWebHtml(html, "Example", "https://jobs.example.com/");
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "Backend Engineer",
      company: "Acme",
      location: "Cairo, EG",
      url: "https://jobs.example.com/role/1",
      posted_at: "2026-10-20T00:00:00.000Z",
      source: "Example",
    });
    expect(jobs[0].desc).toContain("Build APIs");
  });

  it("falls back to server-rendered job links and skips navigation", () => {
    const html = `<nav><a href="/careers/">Careers</a></nav>
      <a href="/jobs/backend-engineer">Backend Engineer</a>
      <a href="/jobs/backend-engineer">Backend Engineer (duplicate)</a>
      <a href="/about">About us</a>`;
    const jobs = parseWebHtml(html, "Example", "https://jobs.example.com/");
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "Backend Engineer",
      url: "https://jobs.example.com/jobs/backend-engineer",
    });
  });

  it("rejects search and category links that live under /jobs/", () => {
    const html = `<a href="/job-search/cloud-it-infrastructure.html">Cloud + IT Infrastructure</a>
      <a href="/jobs/q-data-entry">Data entry</a>
      <a href="/job-profile/1230506.html">Senior Software Engineer</a>
      <a href="/job-detail/abc-123">Backend Engineer</a>
      <a href="/search?q=engineer">Search results</a>`;
    const jobs = parseWebHtml(html, "Example", "https://jobs.example.com/");
    expect(jobs.map((j) => j.title).sort()).toEqual(["Backend Engineer", "Senior Software Engineer"]);
  });

  it("reads __NEXT_DATA__ postings", () => {
    const payload = {
      props: {
        pageProps: {
          jobs: [
            { title: "Full Stack Engineer", url: "/jobs/full-stack-engineer", company: "Beta", location: "Remote" },
          ],
        },
      },
    };
    const html = `<script id="__NEXT_DATA__" type="application/json">${JSON.stringify(payload)}</script>`;
    const jobs = parseWebHtml(html, "Example", "https://jobs.example.com/");
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ title: "Full Stack Engineer", company: "Beta", location: "Remote" });
    expect(jobs[0].remote_flag).toBe(true);
  });
});

describe("bayt adapter", () => {
  it("builds the Cairo search URL with the recency bucket", () => {
    const url = baytSearchUrl("backend", "Cairo, Egypt", 1, 24, false);
    expect(url).toContain("https://www.bayt.com/en/egypt/jobs/backend-jobs-in-cairo/");
    expect(url).toContain("jb_last_modification_date_interval");
    expect(url).toContain("page=1");
  });

  it("parses job cards and drops stale postings", () => {
    const fresh = Math.floor(Date.now() / 1000) - 3600;
    const stale = Math.floor(Date.now() / 1000) - 10 * 86400;
    const card = (id: string, title: string, stamp: number) => `<li data-js-job data-job-id="${id}">
      <h2><a href="/en/egypt/jobs/${id}/">${title}</a></h2>
      <div class="job-company-location-wrapper"><a href="/en/company/acme/">Acme</a></div>
      <dl><dt class="jb-label-location"><span>Cairo</span><span>Egypt</span></dt>
      <dt class="jb-label-remote">Remote</dt></dl>
      <div data-automation-jobactivedate="${stamp}"></div>
    </li>`;
    const jobs = parseBaytCards(`<ul>${card("fresh-1", "Backend Engineer", fresh)}${card("stale-1", "Old Job", stale)}</ul>`, 24);
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "Backend Engineer",
      company: "Acme",
      source: "bayt",
      external_id: "bayt-fresh-1",
      remote_flag: true,
    });
    expect(jobs[0].location).toContain("Cairo");
  });
});

describe("naukri adapter", () => {
  it("generates the RSA request token", () => {
    const token = generateNkparam("srp", 1_700_000_000_000);
    expect(Buffer.from(token, "base64")).toHaveLength(64);
  });

  it("maps a public API job, including remote placement", () => {
    const job = {
      jobId: 42,
      title: "Backend Engineer",
      jdURL: "/job-listings-backend-engineer-42",
      companyName: "Acme",
      createdDate: Date.now() - 3600_000,
      placeholders: [{ type: "location", label: "Remote - Remote" }],
      tagsAndSkills: "Node.js, TypeScript",
      experienceText: "2-4 years",
      salaryDetail: { hideSalary: true },
    };
    const parsed = parseNaukriJob(job, 24);
    expect(parsed).toMatchObject({
      title: "Backend Engineer",
      company: "Acme",
      location: "Remote",
      source: "naukri",
      external_id: "nk-42",
      remote_flag: true,
    });
    expect(parsed?.url).toBe("https://www.naukri.com/job-listings-backend-engineer-42");
    expect(parsed?.desc).toContain("TypeScript");
  });

  it("drops postings older than the window", () => {
    const job = {
      jobId: 1,
      title: "Old Job",
      jdURL: "/old",
      createdDate: Date.now() - 10 * 86400_000,
    };
    expect(parseNaukriJob(job, 24)).toBeNull();
  });
});

describe("feed adapter", () => {
  it("parses a RemoteOK JSON list and skips the legal metadata row", () => {
    const body = JSON.stringify([
      { last_updated: 1792592961, legal: "API Terms" },
      {
        id: "1", epoch: 1792592961, position: "Backend Engineer", company: "Acme",
        url: "https://remoteok.com/remote-jobs/1", location: "Worldwide",
        description: "<p>Python and TypeScript</p>",
      },
    ]);
    const jobs = parseFeedText("remoteok", body);
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "Backend Engineer", company: "Acme", source: "remoteok",
      location: "Worldwide", remote_flag: true,
    });
    expect(jobs[0].desc).toContain("Python and TypeScript");
    expect(jobs[0].posted_at).toBe("1792592961");
  });

  it("parses a Remotive JSON object and honors candidate_required_location", () => {
    const body = JSON.stringify({
      "0-warning": "moved",
      jobs: [{
        id: 2, title: "Full Stack Engineer", company_name: "Beta",
        url: "https://remotive.com/jobs/2", candidate_required_location: "Worldwide",
        publication_date: "2026-10-21T10:00:00Z", description: "Node and React",
      }],
    });
    const jobs = parseFeedText("remotive", body);
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      title: "Full Stack Engineer", company: "Beta", location: "Worldwide",
      posted_at: "2026-10-21T10:00:00Z",
    });
  });

  it("falls back to RSS/Atom XML", () => {
    const xml = `<?xml version="1.0"?><rss><channel><item>
      <title>Platform Engineer</title><link>https://example.com/j/9</link>
      <description>Go and Kubernetes</description><pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>`;
    const jobs = parseFeedText("example", xml);
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ title: "Platform Engineer", url: "https://example.com/j/9" });
  });

  it("returns nothing for a non-feed page", () => {
    expect(parseFeedText("example", "<html><body>No jobs</body></html>")).toEqual([]);
  });
});

describe("probe error classification", () => {
  it("treats an edge 403 as inconclusive, not a deploy failure", () => {
    expect(isInconclusiveProbeError(new Error("Bayt: HTTP 403"))).toBe(true);
    expect(isInconclusiveProbeError(new Error("web board Dice: HTTP 503"))).toBe(true);
    expect(isInconclusiveProbeError(new Error("web board Dice: HTTP 404"))).toBe(false);
  });
});

describe("apply disposition", () => {
  it("keeps a dry run queued", () => {
    expect(applyDisposition({ ok: true, detail: "DRY RUN" }, false)).toEqual({
      state: "APPLICATION_STARTED", manual: false,
    });
  });

  it("queues a captcha or credential refusal for the human", () => {
    expect(applyDisposition({ ok: false, detail: "submit rejected HTTP 401: access denied" }, true)).toEqual({
      state: "APPLICATION_STARTED", manual: true,
    });
    expect(applyDisposition({ ok: false, detail: "captcha required" }, true).manual).toBe(true);
  });

  it("reports a genuine submit failure", () => {
    expect(applyDisposition({ ok: false, detail: "submit transport error: timeout" }, true)).toEqual({
      state: "FAILED", manual: false,
    });
  });

  it("records a successful submission", () => {
    expect(applyDisposition({ ok: true, detail: "submitted (HTTP 200)" }, true)).toEqual({
      state: "SUBMITTED", manual: false,
    });
  });
});

describe("manual apply instruction", () => {
  it("says what to do and names the master CV by its short sha", () => {
    const detail = manualApplyDetail("non-ATS board", "19c8b110cabcc0237fdebdb198f7b9d7d6e294784907930efab00712fa396ae6");
    expect(detail).toBe("non-ATS board — apply manually: tap the link, attach your master CV (master CV 19c8b110)");
    expect(detail).toContain("apply manually");
  });

  it("stays readable when no master CV is set", () => {
    expect(manualApplyDetail("linkedin: discovery only", null)).toBe(
      "linkedin: discovery only — apply manually: tap the link, attach your master CV",
    );
  });
});

describe("source shard generator", () => {
  it("is in sync with config/sources.yaml", () => {
    const out = execFileSync(process.execPath, ["scripts/gen_sources.mjs", "--check"], {
      cwd: process.cwd(),
      encoding: "utf8",
    });
    expect(out).toContain("OK:");
  });
});
