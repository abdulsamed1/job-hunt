import { describe, expect, it } from "vitest";

import { applyStatuses, verdictFor } from "../scripts/probe_sources.mjs";

const CONFIG = `linkedin_enabled: true
sources:
- adapter: greenhouse
  name: revived
  status: dead
- adapter: greenhouse
  name: gone
  status: live
- adapter: greenhouse
  name: quiet
  status: live
- adapter: lever
  name: flaky
  status: unknown
- adapter: linkedin
  name: linkedin_geo_recent
  status: unknown
- name: web-first
  url: https://example.com/jobs
  adapter: web
  status: dead
`;

describe("probe tooling", () => {
  it("classifies probes", () => {
    expect(verdictFor({ status: 200, body: { usable_any_age: 3 } })).toMatchObject({ verdict: "live" });
    expect(verdictFor({ status: 200, body: { usable_any_age: 0 } })).toMatchObject({ verdict: "dead", fatal: false });
    expect(verdictFor({ status: 200, body: { inconclusive: true, error: "HTTP 403" } })).toMatchObject({ verdict: "flaky" });
    expect(verdictFor({ status: 502, body: { error: "HTTP 404" } })).toMatchObject({ verdict: "dead", fatal: true });
    expect(verdictFor({ status: 502, body: { error: "timeout" } })).toMatchObject({ verdict: "flaky", fatal: false });
  });

  it("promotes live, demotes only fatal dead, and leaves quiet boards alone", () => {
    const verdicts = new Map([
      ["revived", { verdict: "live", fatal: false }],
      ["gone", { verdict: "dead", fatal: true }],
      ["quiet", { verdict: "dead", fatal: false }],
      ["flaky", { verdict: "flaky", fatal: false }],
      ["linkedin_geo_recent", { verdict: "dead", fatal: true }],
      ["web-first", { verdict: "live", fatal: false }],
    ]);
    const { text, changed } = applyStatuses(CONFIG, verdicts);
    expect(changed).toBe(3);
    expect(text).toMatch(/name: revived\n\s+status: live/);
    expect(text).toMatch(/name: gone\n\s+status: dead/);
    expect(text).toMatch(/name: quiet\n\s+status: live/);
    expect(text).toMatch(/name: flaky\n\s+status: unknown/);
    // LinkedIn is discovery-only: its status is never machine-applied.
    expect(text).toMatch(/name: linkedin_geo_recent\n\s+status: unknown/);
    // `- name:` first blocks are rewritten too (web sources).
    expect(text).toMatch(/name: web-first\n\s+url: https:\/\/example\.com\/jobs\n\s+adapter: web\n\s+status: live/);
  });
});
