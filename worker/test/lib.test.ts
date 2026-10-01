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
