import { describe, it, expect } from "vitest";
import { boardUrlForDef, classifyHost, isValidOrg } from "../src/lib/hosts.js";
import { detectDegraded, isInconclusiveProbeError } from "../src/lib/health.js";

describe("classifyHost", () => {
  it("passes exact apexes, fails spoofs closed", () => {
    expect(classifyHost("https://boards-api.greenhouse.io/v1/boards/x/jobs")).toBe("ats");
    expect(classifyHost("https://evil-greenhouse.io/x")).toBe("unverified");
    expect(classifyHost("https://greenhouse.io@evil.com/x")).toBe("unverified");
    expect(classifyHost("ftp://boards.greenhouse.io/x")).toBe("unverified");
    expect(classifyHost("https://acme.myworkdayjobs.com/jobs")).toBe("ats");
    expect(classifyHost("https://evil-myworkdayjobs.com/x")).toBe("unverified");
  });
});

describe("boardUrlForDef", () => {
  it("builds the same board URLs discoverSource fetches", () => {
    expect(boardUrlForDef({ kind: "greenhouse", name: "x", org: "stripe", cadence: "hourly" }))
      .toBe("https://boards-api.greenhouse.io/v1/boards/stripe/jobs");
    expect(boardUrlForDef({ kind: "lever", name: "x", org: "ashby", cadence: "hourly" }))
      .toBe("https://api.lever.co/v0/postings/ashby?mode=json");
    expect(boardUrlForDef({ kind: "ashby", name: "x", org: "stellar", cadence: "hourly" }))
      .toBe("https://api.ashbyhq.com/posting-api/job-board/stellar?includeCompensation=true");
    expect(boardUrlForDef({ kind: "smartrecruiters", name: "x", org: "acme", cadence: "hourly" }))
      .toBe("https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100");
  });
  it("rejects orgs outside [A-Za-z0-9_-] (fail closed)", () => {
    expect(isValidOrg("evil org")).toBe(false);
    expect(isValidOrg("a;b")).toBe(false);
    expect(isValidOrg("x/y")).toBe(false);
    expect(isValidOrg("stripe")).toBe(true);
    expect(boardUrlForDef({ kind: "greenhouse", name: "evil", org: "evil org", cadence: "hourly" })).toBeNull();
    expect(boardUrlForDef({ kind: "lever", name: "evil", org: "a;b", cadence: "hourly" })).toBeNull();
  });
  it("falls back to def.url for non-org kinds", () => {
    expect(boardUrlForDef({ kind: "rss", name: "f", url: "https://example.com/feed", cadence: "hourly" }))
      .toBe("https://example.com/feed");
  });
});
describe("detectDegraded", () => {
  it("flags garbage rows but not clean ones", () => {
    const bad = [{ title: "<b>Dev &amp; Ops", company: "", url: "https://evil.com/x", location: "Remote", desc: "x" }];
    expect(detectDegraded(bad, "https://example.com/feed")).toContain("null-company");
    const good = [{ title: "Backend Engineer", company: "Acme", url: "https://example.com/j/1", location: "Remote", desc: "Python" }];
    expect(detectDegraded(good, "https://example.com/feed")).toEqual([]);
  });
  it("returns [] for an empty job list", () => {
    expect(detectDegraded([], "https://example.com/feed")).toEqual([]);
  });
});

describe("isInconclusiveProbeError", () => {
  it("maps rate-limit and transport failures to inconclusive", () => {
    expect(isInconclusiveProbeError(new Error("HTTP 429 Too Many Requests"))).toBe(true);
    expect(isInconclusiveProbeError(new Error("Rate limit exceeded, retry later"))).toBe(true);
    expect(isInconclusiveProbeError(new TypeError("fetch failed"))).toBe(true);
    expect(isInconclusiveProbeError(new Error("Connect timeout"))).toBe(true);
    expect(isInconclusiveProbeError(new Error("getaddrinfo ENOTFOUND boards-api.greenhouse.io"))).toBe(true);
  });
  it("leaves real bugs as conclusive failures", () => {
    expect(isInconclusiveProbeError(new Error("Cannot read properties of undefined"))).toBe(false);
    expect(isInconclusiveProbeError("some weird string")).toBe(false);
  });
});

describe("/probe unknown source", () => {
  it("stays 404 for unknown source names", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/probe", {
        method: "POST",
        body: JSON.stringify({ name: "no-such-source" }),
      }),
      {} as any,
    );
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "unknown source" });
  });
});
