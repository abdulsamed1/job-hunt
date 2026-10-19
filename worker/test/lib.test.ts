import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { canonicalHash, fnv1a, normalizeLocation, normalizeUrl, roleFingerprint } from "../src/lib/hash.js";
import { filterRemoteList, filterRecentList, HOURS_OLD, isFresh, isPlacedOnsite, isRemoteish, parsePostedAt, partitionFresh } from "../src/lib/freshness.js";
import { checkDisabilityTargetingGate, checkEligibilityGate, checkLanguageGate, classifyTier, scoreJob, type Profile } from "../src/lib/evaluate.js";
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
  it("strips every utm_* variant plus the tracker params Python strips", () => {
    // Prefix rule covers utm params nobody has listed yet; the exact names
    // mirror Python's dedup.TRACKING_PARAM_PATTERNS so both runtimes collapse
    // the same aggregator mirrors.
    expect(normalizeUrl("https://x.com/j/1?utm_campaign=b&x=1")).toBe("https://x.com/j/1?x=1");
    expect(normalizeUrl("https://x.com/j/1?utm_whatever=c&x=1")).toBe("https://x.com/j/1?x=1");
    expect(
      normalizeUrl("https://x.com/j/1?igshid=a&_hsenc=b&_hsmi=c&trk=d&trackingid=e&ref_code=f&gh_src=g&x=1"),
    ).toBe("https://x.com/j/1?x=1");
  });
  it("stable hashes", () => {
    expect(fnv1a("abc")).toBe(fnv1a("abc"));
    expect(canonicalHash("https://x.com/j/1?utm_source=a")).toBe(canonicalHash("https://x.com/j/1"));
    expect(roleFingerprint("Acme", "Dev", "Remote")).toBe(roleFingerprint("ACME ", " dev ", "remote"));
  });
});

describe("normalizeLocation", () => {
  it("maps every remote synonym to one token, empty/unknown to ?", () => {
    expect(normalizeLocation("Remote")).toBe("remote");
    expect(normalizeLocation("  REMOTE ")).toBe("remote");
    expect(normalizeLocation("Anywhere")).toBe("remote");
    expect(normalizeLocation("Worldwide")).toBe("remote");
    expect(normalizeLocation("WFH")).toBe("remote");
    expect(normalizeLocation("Work From Home")).toBe("remote");
    expect(normalizeLocation("Distributed")).toBe("remote");
    expect(normalizeLocation("Telecommute")).toBe("remote");
    expect(normalizeLocation("United States")).toBe("remote");
    expect(normalizeLocation("US")).toBe("remote");
    expect(normalizeLocation("USA")).toBe("remote");
    expect(normalizeLocation("Global")).toBe("remote");
    // A mixed location keeps its place: remote-anything collapses, "remote in
    // Berlin" does not.
    expect(normalizeLocation("Remote, US")).toBe("remote");
    expect(normalizeLocation("Remote - Europe")).toBe("remote europe");
    expect(normalizeLocation("Berlin, Germany")).toBe("berlin germany");
    expect(normalizeLocation("")).toBe("?");
    expect(normalizeLocation("   ")).toBe("?");
  });
});

describe("roleFingerprint", () => {
  it("collapses remote location synonyms to one token", () => {
    expect(roleFingerprint("Acme", "Backend Engineer", "Remote, US")).toBe(
      roleFingerprint("Acme", "Backend Engineer", "United States"),
    );
  });

  it("keeps an unknown location distinct from remote", () => {
    expect(roleFingerprint("Acme", "Backend Engineer", "")).not.toBe(
      roleFingerprint("Acme", "Backend Engineer", "Remote"),
    );
  });

  it("ignores punctuation and case in title and company", () => {
    expect(roleFingerprint("Acme, Inc.", "Sr. Backend Engineer (Payments)", "Remote")).toBe(
      roleFingerprint("acme inc", "sr backend engineer payments", "remote"),
    );
  });

  it("separates a different place, company or title", () => {
    const base = roleFingerprint("Acme", "Backend Engineer", "Remote");
    expect(roleFingerprint("Acme", "Backend Engineer", "Remote - Europe")).not.toBe(base);
    expect(roleFingerprint("Beta", "Backend Engineer", "Remote")).not.toBe(base);
    expect(roleFingerprint("Acme", "Frontend Engineer", "Remote")).not.toBe(base);
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

describe("partitionFresh", () => {
  it("splits postings into fresh, stale and dateless", () => {
    const { fresh, stale, dateless } = partitionFresh(
      [{ posted_at: new Date(Date.now() - 3600e3).toISOString() },
        { posted_at: new Date(Date.now() - 48 * 3600e3).toISOString() },
        { posted_at: "" }], 24,
    );
    expect(fresh).toHaveLength(1); expect(stale).toHaveLength(1); expect(dateless).toHaveLength(1);
  });

  it("never calls an undated posting fresh, whatever the window", () => {
    // A dateless posting is its own bucket: the window cannot vouch for it, and
    // a disabled window (hours_old: 0, manual runs only) must not either.
    const now = new Date().toISOString();
    for (const hoursOld of [24, 0]) {
      const { fresh, stale, dateless } = partitionFresh(
        [{ posted_at: "" }, { posted_at: now }, { posted_at: "2020-01-01" }], hoursOld,
      );
      expect(dateless).toHaveLength(1);
      expect(fresh.every((p) => p.posted_at !== "")).toBe(true);
      expect(fresh.some((p) => p.posted_at === now)).toBe(true);
      // A disabled window keeps everything dated, stale included.
      expect(stale).toHaveLength(hoursOld > 0 ? 1 : 0);
    }
  });

  it("keeps the input order inside every bucket", () => {
    const postings = [
      { posted_at: new Date(Date.now() - 3 * 3600e3).toISOString(), url: "a" },
      { posted_at: new Date(Date.now() - 30 * 3600e3).toISOString(), url: "b" },
      { posted_at: new Date(Date.now() - 1 * 3600e3).toISOString(), url: "c" },
    ];
    const { fresh, stale } = partitionFresh(postings, HOURS_OLD);
    expect(fresh.map((p) => p.url)).toEqual(["a", "c"]);
    expect(stale.map((p) => p.url)).toEqual(["b"]);
  });

  it("reads the window from one named constant", () => {
    // The Worker's window is 24h everywhere: a 12h window would silently drop
    // every posting that lands 13-24h before the daily deep sweep.
    expect(HOURS_OLD).toBe(24);
    expect(partitionFresh([{ posted_at: new Date(Date.now() - 20 * 3600e3).toISOString() }], HOURS_OLD).fresh).toHaveLength(1);
    expect(partitionFresh([{ posted_at: new Date(Date.now() - 20 * 3600e3).toISOString() }], 12).stale).toHaveLength(1);
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

describe("scoreJob disability-targeting gate", () => {
  it("rejects a disability-targeted program before scoring", () => {
    const r = scoreJob(
      "Technical Support Engineer Intern for People with Disabilities",
      "Ignite Program. Python and customer support.",
      "Cairo",
      profile(),
      70,
    );
    expect(r.gated).toBe(true);
    expect(r.eligible).toBe(false);
    expect(r.reasoning).toMatch(/disabilit/i);
  });

  it("allows generic accessibility language", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "Equal opportunity employer; reasonable accommodation is available.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(false);
  });

  it("rejects encouragement into a hiring program (noun beats suppression)", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "We encourage people with disabilities to join our hiring program. Python role.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(true);
    expect(r.eligible).toBe(false);
    expect(r.reasoning).toMatch(/disabilit/i);
  });

  it("passes an encouragement-only sentence without program nouns", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "Individuals with disabilities are encouraged to apply. Python role.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(false);
  });

  it("passes a distant EEO statement (300-char gap)", () => {
    const gap = "Backend systems work. ".repeat(20);
    const r = scoreJob(
      "Senior Backend Engineer",
      `Engineering role for people with disabilities. ${gap}We are an equal opportunity employer.`,
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(false);
  });

  it("passes non-discrimination boilerplate that mentions hiring", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "We do not discriminate in hiring on the basis of disability. Python backend role.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(false);
  });

  it("rejects a bare disability program without hiring language", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "Our disability inclusion program supports engineers. Python backend role.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(true);
    expect(r.eligible).toBe(false);
    expect(r.reasoning).toMatch(/disabilit/i);
  });

  it("does not let a decimal point hide the program noun from the sentence", () => {
    const r = scoreJob(
      "Senior Backend Engineer",
      "Job pays 3.5x above market and we encourage adults with disabilities to apply for our internship program. Python backend role.",
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(true);
    expect(r.eligible).toBe(false);
    expect(r.reasoning).toMatch(/disabilit/i);
  });

  it("keeps the matched phrase in the quote at late positions", () => {
    const filler = "We build reliable systems for teams worldwide ".repeat(8);
    const r = scoreJob(
      "Senior Backend Engineer",
      `${filler}We support engineers with disabilities through our certified program.`,
      "Remote",
      profile(),
      70,
    );
    expect(r.gated).toBe(true);
    expect(r.reasoning).toMatch(/disabilit/i);
  });

  it("matches the shared Python/Worker parity fixtures", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const fixture = join(here, "..", "..", "tests", "fixtures", "disability_gate_cases.json");
    const cases = JSON.parse(readFileSync(fixture, "utf8")) as Array<{
      name: string;
      title: string;
      description: string;
      verdict: "PASS" | "FAIL";
    }>;
    expect(cases.length).toBeGreaterThan(0);
    for (const c of cases) {
      const r = checkDisabilityTargetingGate(c.title, c.description);
      expect(r.verdict, c.name).toBe(c.verdict);
      if (c.verdict === "FAIL") {
        expect((r.quote || "").toLowerCase(), c.name).toMatch(/disabilit/);
      }
    }
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
    // Dead boards (status: dead) are excluded from the shard, so the live set is
    // smaller than the configured 382. The floor is the measured live count minus
    // margin; a drop below it means a mass regression or an over-aggressive probe.
    expect(GENERATED_SOURCES.length + PYTHON_ONLY_SOURCES.length).toBeGreaterThanOrEqual(150);
    expect(GENERATED_SOURCES.length).toBeGreaterThanOrEqual(100);
    const hourly = GENERATED_SOURCES.filter((s) => s.cadence === "hourly");
    expect(hourly.length).toBeGreaterThan(0);
    expect(hourly.length).toBeLessThanOrEqual(40);
  });

  it("keeps the LinkedIn standing target at 12h / 3 queries", async () => {
    const { GENERATED_SOURCES } = await import("../src/sources.generated.js");
    const li = GENERATED_SOURCES.filter((s) => s.kind === "linkedin");
    expect(li).toHaveLength(1);
    expect(li[0].tprSeconds).toBe(43200);
    expect([...(li[0].queries || [])].sort()).toEqual(["Backend", "Forward Deployed Engineer", "Frontier Deployed Engineer", "Full stack", "Software Engineer"]);
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
