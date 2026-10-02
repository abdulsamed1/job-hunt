import { describe, it, expect } from "vitest";
import { classifyHost } from "../src/lib/hosts.js";

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
