import { describe, expect, it } from "vitest";
import { BD_LOCATION_CODES, bdLocationCode, bdPostedWithinDays } from "../src/adapters/boards.js";

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
