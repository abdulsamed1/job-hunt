import { describe, it, expect } from "vitest";
import { classifyHost } from "../src/lib/hosts.js";
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
