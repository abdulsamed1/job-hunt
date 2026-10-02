import { describe, expect, it } from "vitest";
import { applyToAts, buildTailoredText, renderPdfBytes } from "../src/stages/pipeline.js";
import type { Profile } from "../src/lib/evaluate.js";

const profile = (): Profile & { fullName: string; email: string; phone: string } => ({
  verifiedSkills: ["Python", "FastAPI"],
  yearsOfExperience: 5,
  openToRemote: true,
  sponsorshipRequired: false,
  citizenship: "Egypt",
  authorizedCountries: ["Egypt"],
  blockedCompanies: [],
  blockedKeywords: [],
  preferredKeywords: [],
  location: "Cairo, Egypt",
  languages: ["English"],
  fullName: "Test User",
  email: "t@example.com",
  phone: "+201000000000",
});

describe("tailor", () => {
  it("orders matched skills first", () => {
    const text = buildTailoredText({ title: "Backend Engineer", company: "Acme" }, profile());
    const skillsLine = text.split("\n").find((l) => l.includes("Python")) || "";
    expect(skillsLine.indexOf("Python")).toBeLessThan(skillsLine.length - 1);
    expect(text).toContain("Acme");
  });

  it("renders a parseable PDF", async () => {
    const bytes = await renderPdfBytes("Test User\nBACKEND SKILLS\nPython, FastAPI", "Test User");
    expect(bytes.length).toBeGreaterThan(500);
    const header = new TextDecoder().decode(bytes.slice(0, 8));
    expect(header).toContain("%PDF");
  });
});

describe("applyToAts", () => {
  const payload = {
    firstName: "Test",
    lastName: "User",
    email: "t@example.com",
    phone: "+201000000000",
    resumeBytes: new Uint8Array([1, 2, 3]),
    resumeFilename: "cv.pdf",
  };

  it("dry-runs greenhouse without sending", async () => {
    const r = await applyToAts(
      { kind: "greenhouse", org: "acme", jobId: "123" }, payload, { dryRun: true, approved: false },
    );
    expect(r.ok).toBe(true);
    expect(r.detail).toContain("DRY RUN");
  });

  it("requires approval for live sends", async () => {
    const r = await applyToAts(
      { kind: "lever", org: "acme", jobId: "123" }, payload, { dryRun: false, approved: false },
    );
    expect(r.ok).toBe(true);
    expect(r.detail).toContain("DRY RUN");
  });

  it("ashby live stays dry-run until pinned", async () => {
    const r = await applyToAts(
      { kind: "ashby", org: "acme", jobId: "123" }, payload, { dryRun: false, approved: true },
    );
    expect(r.ok).toBe(false);
    expect(r.detail).toMatch(/dry-run/i);
  });
});

describe("upsertJob backfill semantics", () => {
  const row = (description: string, location = "Remote", posted = "2026-10-01") => ({
    canonical_hash: "h1", title: "t", company: "c", location, url: "u",
    description, source: "greenhouse:x", posted_at: posted, discovered_at: "now",
    score: 0, remote: true,
  });

  const fakeDb = (changes: number) => ({
    prepare: () => ({ bind: () => ({ run: async () => ({ meta: { changes } }) }) }),
  }) as any;

  it("reports true for a brand-new job", async () => {
    const { upsertJob } = await import("../src/state.js");
    expect(await upsertJob(fakeDb(1), row("short"))).toBe(true);
  });

  it("reports true when a stale description gets richer (re-score needed)", async () => {
    const { upsertJob } = await import("../src/state.js");
    expect(await upsertJob(fakeDb(1), row("x".repeat(900)))).toBe(true);
  });

  it("reports false on a no-op upsert so sweeps do not flood the queue", async () => {
    const { upsertJob } = await import("../src/state.js");
    expect(await upsertJob(fakeDb(0), row("x".repeat(900)))).toBe(false);
  });
});
