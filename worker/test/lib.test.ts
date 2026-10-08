import { describe, expect, it } from "vitest";
import { canonicalHash, fnv1a, normalizeUrl, roleFingerprint } from "../src/lib/hash.js";
import { filterRemoteList, filterRecentList, isFresh, isPlacedOnsite, isRemoteish, parsePostedAt } from "../src/lib/freshness.js";
import { checkEligibilityGate, checkLanguageGate, classifyTier, scoreJob, type Profile } from "../src/lib/evaluate.js";
import { checkboxAction, matchAnswerToOption } from "../src/lib/match.js";

const profile = (): Profile => ({
  verifiedSkills: ["Python", "FastAPI", "PostgreSQL"],
  yearsOfExperience: 5,
  openToRemote: true,
  sponsorshipRequired: false,
  citizenship: "Egypt",
  authorizedCountries: ["Egypt"],
  blockedCompanies: [],
  blockedKeywords: [],
  preferredKeywords: [],
  location: "Cairo, Egypt",
  languages: ["English", "Arabic"],
});

describe("hash", () => {
  it("strips tracking params", () => {
    expect(normalizeUrl("https://x.com/j/1?utm_source=a&x=1")).toBe("https://x.com/j/1?x=1");
  });
  it("stable hashes", () => {
    expect(fnv1a("abc")).toBe(fnv1a("abc"));
    expect(canonicalHash("https://x.com/j/1?utm_source=a")).toBe(canonicalHash("https://x.com/j/1"));
    expect(roleFingerprint("Acme", "Dev", "Remote")).toBe(roleFingerprint("ACME ", " dev ", "remote"));
  });
});

describe("freshness", () => {
  it("parses formats", () => {
    expect(parsePostedAt(new Date().toISOString())).not.toBeNull();
    expect(parsePostedAt(Date.now())).not.toBeNull();
    expect(parsePostedAt(Date.now() * 1)).not.toBeNull();
    expect(parsePostedAt("5 hours ago")).not.toBeNull();
    expect(parsePostedAt("")).toBeNull();
    expect(parsePostedAt("soon")).toBeNull();
  });
  it("fresh/stale/unknown", () => {
    expect(isFresh(new Date().toISOString(), 24)).toBe(true);
    expect(isFresh("2020-01-01", 24)).toBe(false);
    expect(isFresh(null, 24)).toBeNull();
  });
  it("remote signals", () => {
    expect(isRemoteish("Remote", "", undefined)).toBe(true);
    expect(isRemoteish("Cairo, Egypt", "", undefined)).toBe(false);
    expect(isRemoteish("", "remote friendly", undefined)).toBe(true);
    expect(isPlacedOnsite("Berlin, Germany", "", undefined)).toBe(true);
    expect(isPlacedOnsite("", "", undefined)).toBe(false);
  });
  it("list filters drop only provable cases", () => {
    expect(filterRecentList([{ posted_at: null }], 24)).toHaveLength(1);
    expect(filterRemoteList([{ location: "", description: "" }])).toHaveLength(1);
  });
});

describe("gates", () => {
  it("eligibility FAIL quotes wording", () => {
    const v = checkEligibilityGate("Applicants must be citizens of Norway.");
    expect(v.verdict).toBe("FAIL");
    expect(v.quote?.toLowerCase()).toContain("norway");
  });
  it("language FAIL on undeclared, all requirements collected", () => {
    const v = checkLanguageGate("Dev", "Fluent English and fluent Polish required.", profile());
    expect(v.verdict).toBe("FAIL");
    expect(v.quote).toBe("polish");
  });
  it("language UNVERIFIED without declared languages", () => {
    const p = profile();
    p.languages = [];
    expect(checkLanguageGate("Dev", "Fluent German required.", p).verdict).toBe("UNVERIFIED");
  });
});

describe("scoreJob", () => {
  it("gates before scoring", () => {
    const r = scoreJob("Dev", "Must be citizens of Norway. Python.", "Remote", profile());
    expect(r.eligible).toBe(false);
    expect(r.score).toBe(0);
    expect(r.gated).toBe(true);
  });
  it("scores a fit", () => {
    const r = scoreJob("Backend Engineer", "Python and FastAPI backend.", "Remote", profile());
    expect(r.eligible).toBe(true);
    expect(r.matched).toContain("Python");
  });
});

describe("classifyTier", () => {
  it("maps titles", () => {
    expect(classifyTier("Software Engineering Intern", "")).toBe("intern");
    expect(classifyTier("Junior Backend Engineer", "")).toBe("entry");
    expect(classifyTier("Backend Engineer", "")).toBe("mid");
    expect(classifyTier("Senior Backend Engineer", "")).toBe("senior");
    expect(classifyTier("Staff Backend Engineer", "")).toBe("lead");
  });
});

describe("scoreJob matched-skill requirement", () => {
  it("rejects empty-signal titles", () => {
    const r = scoreJob("Senior Accountant", "Finance role.", "Remote", profile());
    expect(r.eligible).toBe(false);
  });
});

describe("matchAnswerToOption", () => {
  it("exact + whole-word, no substring traps", () => {
    expect(matchAnswerToOption("Yes", ["Yes", "No"])).toBe("Yes");
    expect(matchAnswerToOption("No", ["Knowledgeable", "No"])).toBe("No");
    expect(matchAnswerToOption("No", ["Knowledgeable"])).toBeNull();
    expect(matchAnswerToOption("No", ["Nope", "Yes"])).toBe("Nope");
    expect(matchAnswerToOption("Yes", ["No"])).toBeNull();
    expect(matchAnswerToOption("Yes", ["Maybe"])).toBeNull();
    expect(
      matchAnswerToOption("I choose not to disclose", ["Male", "Female", "Decline to self-identify"]),
    ).toBe("Decline to self-identify");
  });
  it("checkbox split", () => {
    expect(checkboxAction("I certify the above statements are true")).toBe("skip");
    expect(checkboxAction("I agree to the privacy policy")).toBe("check");
    expect(checkboxAction("Follow us on LinkedIn")).toBe("skip");
  });
});

describe("ashby index fan-out", () => {
  it("extracts unique org slugs in first-seen order", async () => {
    const { extractAshbyOrgs } = await import("../src/adapters/ats.js");
    const html = `
      <a href="https://jobs.ashbyhq.com/Solana">x</a>
      <a href="https://jobs.ashbyhq.com/coinbase">x</a>
      <a href="https://jobs.ashbyhq.com/Solana/jobs/1">dup</a>
      <a href="https://example.com/nope">x</a>
      <a href="https://jobs.ashbyhq.com/coinbase/x">dup2</a>`;
    expect(extractAshbyOrgs(html)).toEqual(["solana", "coinbase"]);
  });

  it("returns [] for an index page with no ashby links", async () => {
    const { extractAshbyOrgs } = await import("../src/adapters/ats.js");
    expect(extractAshbyOrgs("<html><body>nothing</body></html>")).toEqual([]);
  });
});

describe("generated source shard", () => {
  it("ships the full source set with hourly shard under the batch cap", async () => {
    const { GENERATED_SOURCES, PYTHON_ONLY_SOURCES } = await import("../src/sources.generated.js");
    expect(GENERATED_SOURCES.length + PYTHON_ONLY_SOURCES.length).toBe(372);
    expect(GENERATED_SOURCES.length).toBeGreaterThanOrEqual(200);
    const hourly = GENERATED_SOURCES.filter((s) => s.cadence === "hourly");
    expect(hourly.length).toBeGreaterThan(0);
    expect(hourly.length).toBeLessThanOrEqual(40);
  });

  it("keeps the LinkedIn standing target at 12h / 3 queries", async () => {
    const { GENERATED_SOURCES } = await import("../src/sources.generated.js");
    const li = GENERATED_SOURCES.filter((s) => s.kind === "linkedin");
    expect(li).toHaveLength(1);
    expect(li[0].tprSeconds).toBe(43200);
    expect([...(li[0].queries || [])].sort()).toEqual(["backend", "fullstack", "software"]);
  });

  it("gives every ATS source an org slug for board routing", async () => {
    const { GENERATED_SOURCES } = await import("../src/sources.generated.js");
    for (const s of GENERATED_SOURCES) {
      if (["greenhouse", "lever", "ashby", "smartrecruiters"].includes(s.kind)) {
        expect(s.org, s.name).toBeTruthy();
      }
    }
  });
});

describe("fetchBdJobs", () => {
  it("queries GetJobSearch with keyword params and parses data + premiumData", async () => {
    const { fetchBdJobs, bdLocationCode, bdPostedWithinDays } = await import("../src/adapters/boards.js");
    expect(bdLocationCode("Dhaka, Bangladesh")).toBe(14);
    expect(bdLocationCode("Bangladesh")).toBeNull();
    expect(bdPostedWithinDays(72)).toBe(4);
    const seen: string[] = [];
    const stub = async (url: string) => {
      seen.push(url);
      const payload = {
        message: "Success",
        data: [{ Jobid: "111", jobTitle: "Backend Engineer", companyName: "Acme Ltd", location: "Dhaka", publishDate: "2026-10-06T00:00:00Z" }],
        premiumData: [{ Jobid: "222", jobTitle: "Fullstack Developer", companyName: "Beta Ltd", location: "Dhaka", publishDate: "2026-10-06T00:00:00Z" }],
      };
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    const prev = globalThis.fetch;
    (globalThis as any).fetch = stub;
    try {
      const out = await fetchBdJobs({ searchUrl: "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch", query: "backend", location: "Dhaka, Bangladesh", hoursOld: 72 }, 40);
      expect(out).toHaveLength(2);
      expect(seen[0]).toContain("keyword=backend");
      expect(seen[0]).toContain("location=14");
      expect(seen[0]).toContain("postedWithin=4");
      expect(out[0].title).toBe("Backend Engineer");
      expect(out[0].url).toBe("https://bdjobs.com/h/details/111");
      expect(out[1].external_id).toBe("bdjobs-222");
      for (const j of out) {
        expect(j.title && j.company && j.url && j.desc).toBeTruthy();
      }
    } finally {
      globalThis.fetch = prev;
    }
  });
});

describe("htmlToText", () => {
  it("strips tags and unescapes double-encoded entities", async () => {
    const { htmlToText } = await import("../src/adapters/ats.js");
    const html = "&lt;h2&gt;Who we are&lt;/h2&gt;&lt;p&gt;We build &amp; ship &quot;infra&quot;&lt;/p&gt;";
    expect(htmlToText(html)).toBe('Who we are We build & ship "infra"');
  });

  it("collapses whitespace and respects max length", async () => {
    const { htmlToText } = await import("../src/adapters/ats.js");
    expect(htmlToText("a\n\n   b   c")).toBe("a b c");
    expect(htmlToText("x".repeat(100), 10)).toHaveLength(10);
  });
});

describe("enrichDescriptions", () => {
  it("leaves jobs with real descriptions untouched and within budget", async () => {
    const { enrichDescriptions } = await import("../src/adapters/ats.js");
    const jobs = [
      { title: "a", company: "c", location: "Remote", url: "u", source: "lever:x", desc: "y".repeat(500), posted_at: "", external_id: "1" },
      { title: "b", company: "c", location: "Remote", url: "u2", source: "lever:y", desc: "short", posted_at: "", external_id: "2" },
    ];
    const out = await enrichDescriptions(jobs);
    expect(out[0].desc).toHaveLength(500);
    expect(out[1].desc).toBe("short"); // lever already had text upstream; no fetch attempted
  });

  it("never drops a job when enrichment fails", async () => {
    const { enrichDescriptions } = await import("../src/adapters/ats.js");
    const jobs = [{ title: "t", company: "c", location: "Remote", url: "u", source: "greenhouse:nope", desc: "x", posted_at: "", external_id: "999999" }];
    const out = await enrichDescriptions(jobs);
    expect(out).toHaveLength(1);
  });
});

describe("linkedin runtime disable", () => {
  it("filters linkedin sources unless LINKEDIN_ENABLED=true", async () => {
    const { activeSources, linkedinEnabled } = await import("../src/index.js");
    expect(linkedinEnabled({} as any)).toBe(false);
    expect(linkedinEnabled({ LINKEDIN_ENABLED: "false" } as any)).toBe(false);
    expect(linkedinEnabled({ LINKEDIN_ENABLED: "true" } as any)).toBe(true);
    const off = activeSources({} as any);
    expect(off.some((s) => s.kind === "linkedin")).toBe(false);
    const on = activeSources({ LINKEDIN_ENABLED: "true" } as any);
    expect(on.some((s) => s.kind === "linkedin")).toBe(true);
  });
});
