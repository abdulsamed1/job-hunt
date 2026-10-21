import { describe, expect, it } from "vitest";
import { BD_LOCATION_CODES, bdLocationCode, bdPostedWithinDays, fetchLinkedInGuest } from "../src/adapters/boards.js";

describe("fetchLinkedInGuest", () => {
  const card = '<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:123">' +
    '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/x-123?trk=1"></a>' +
    '<h3 class="base-search-card__title">Backend Engineer</h3>' +
    '<h4 class="base-search-card__subtitle">Acme</h4>' +
    '<span class="job-search-card__location">Cairo, Egypt</span>' +
    '<time datetime="2026-10-21"></time></div></li>';

  it("parses guest cards into postings", async () => {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async () => ({
      ok: true, status: 200,
      arrayBuffer: async () => new TextEncoder().encode(`<ul>${card}</ul>`).buffer,
    });
    try {
      const jobs = await fetchLinkedInGuest("Backend", "Egypt", 1, 43200);
      expect(jobs).toHaveLength(1);
      expect(jobs[0].title).toBe("Backend Engineer");
      expect(jobs[0].company).toBe("Acme");
      expect(jobs[0].location).toBe("Cairo, Egypt");
      expect(jobs[0].url).toBe("https://www.linkedin.com/jobs/view/x-123");
      expect(jobs[0].external_id).toBe("123");
    } finally {
      (globalThis as any).fetch = prev;
    }
  });

  it("unescapes HTML entities in card text", async () => {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async () => ({
      ok: true, status: 200,
      arrayBuffer: async () => new TextEncoder().encode(
        '<ul><li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:7">' +
        '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/z-7"></a>' +
        '<h3 class="base-search-card__title">Backend &amp; Platform Engineer</h3>' +
        '<h4 class="base-search-card__subtitle">Acme &amp; Co</h4>' +
        '<span class="job-search-card__location">Cairo, Egypt</span>' +
        '</div></li></ul>',
      ).buffer,
    });
    try {
      const jobs = await fetchLinkedInGuest("Backend", "Egypt", 1, 43200);
      expect(jobs[0].title).toBe("Backend & Platform Engineer");
      expect(jobs[0].company).toBe("Acme & Co");
    } finally {
      (globalThis as any).fetch = prev;
    }
  });

  it("tags city locations as remote when the search itself was remote-filtered", async () => {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async () => ({
      ok: true, status: 200,
      arrayBuffer: async () => new TextEncoder().encode(
        '<ul><li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:42">' +
        '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/y-42"></a>' +
        '<h3 class="base-search-card__title">Backend Engineer</h3>' +
        '<span class="job-search-card__location">Melbourne, Victoria, Australia</span>' +
        '</div></li></ul>',
      ).buffer,
    });
    try {
      const remote = await fetchLinkedInGuest("Backend", "Remote", 1, 43200);
      expect(remote[0].location).toBe("Melbourne, Victoria, Australia (Remote)");
      // A geo search keeps the printed location: Egypt rows stay recognisable.
      const egypt = await fetchLinkedInGuest("Backend", "Egypt", 1, 43200);
      expect(egypt[0].location).toBe("Melbourne, Victoria, Australia");
    } finally {
      (globalThis as any).fetch = prev;
    }
  });

  it("raises on a failed fetch instead of reporting an empty board", async () => {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async () => ({ ok: false, status: 429 });
    try {
      // The status rides on the error: "all failed" with no reason is not a
      // diagnosable production report.
      await expect(fetchLinkedInGuest("Backend", "Egypt", 1, 43200))
        .rejects.toThrow(/linkedin guest search failed \(Backend @ Egypt\): HTTP 429/);
    } finally {
      (globalThis as any).fetch = prev;
    }
  });

  it("names a network failure instead of an HTTP status when the request throws", async () => {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async () => {
      throw new Error("connect timeout");
    };
    try {
      await expect(fetchLinkedInGuest("Backend", "Egypt", 1, 43200))
        .rejects.toThrow(/HTTP network error/);
    } finally {
      (globalThis as any).fetch = prev;
    }
  });
});

describe("bdjobs helpers", () => {
  it("carries the full location table (parity pinned from Python, see tests/)", () => {
    // Full table ported from src/job_hunt/discovery/adapters/bdjobs.py;
    // key-set parity with the Python dict is asserted in
    // tests/test_slice4_coverage.py::test_bdjobs_location_table_parity.
    expect(Object.keys(BD_LOCATION_CODES).length).toBe(72);
    expect(BD_LOCATION_CODES["cox's bazar"]).toBe(13);
    expect(BD_LOCATION_CODES["sunamganj"]).toBe(61);
  });

  it("resolves city names and widens unknowns", () => {
    expect(bdLocationCode("Dhaka, Bangladesh")).toBe(14);
    expect(bdLocationCode("cox's bazar")).toBe(13);
    expect(bdLocationCode("Bangladesh")).toBeNull();
    expect(bdLocationCode("Nowhereville")).toBeNull();
  });

  it("maps hoursOld to postedWithin days like the Python adapter", () => {
    expect(bdPostedWithinDays(72)).toBe(4);
    expect(bdPostedWithinDays(24)).toBe(2);
    expect(bdPostedWithinDays(0)).toBeNull();
    expect(bdPostedWithinDays(null)).toBeNull();
    expect(bdPostedWithinDays(200)).toBeNull(); // >5 days unsupported by the site
  });
});
