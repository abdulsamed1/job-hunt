import { describe, it, expect } from "vitest";
import { classifyHost } from "../src/lib/hosts.js";
import { detectDegraded } from "../src/lib/health.js";

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
});
