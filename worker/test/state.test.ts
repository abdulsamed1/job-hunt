import { describe, it, expect } from "vitest";
import { ensureSourceHealthColumns, recordSourceHealth } from "../src/state.js";

// Minimal D1 stand-in: captures bound statements, answers PRAGMA from a
// fixed column list.
function fakeDb(existingCols: string[], queries: string[][]) {
  return {
    prepare(query: string) {
      const norm = query.trimStart().slice(0, 60);
      return {
        bind: (...params: any[]) => {
          queries.push([norm, ...params.map(String)]);
          return { run: async () => ({ meta: { changes: 1 } }) };
        },
        all: async () => ({ results: existingCols.map((name) => ({ name })) }),
        run: async () => {
          queries.push([norm]);
          return {};
        },
      };
    },
    batch: async () => [],
  } as any;
}

const FULL_COLS = [
  "source", "kind", "attempts", "yields", "last_raw", "last_kept",
  "last_error", "last_seen", "last_degraded", "degraded_hits",
];

describe("recordSourceHealth degraded persistence", () => {
  it("persists comma-joined signals and bumps degraded_hits", async () => {
    const queries: string[][] = [];
    await recordSourceHealth(
      fakeDb(FULL_COLS, queries), "rss-x", "rss", 10, 4, "", ["null-company", "off-domain-url"],
    );
    const insert = queries.find((q) => q[0].startsWith("INSERT INTO source_health"));
    expect(insert).toBeTruthy();
    // bind order: source, kind, yields-flag, raw, kept, error, seen, degraded, hit
    expect(insert![1]).toBe("rss-x");
    expect(insert![8]).toBe("null-company,off-domain-url");
    expect(insert![9]).toBe("1");
    // no migration ALTER needed when the columns already exist
    expect(queries.some((q) => q[0].startsWith("ALTER TABLE"))).toBe(false);
  });

  it("defaults to no degraded signals", async () => {
    const queries: string[][] = [];
    await recordSourceHealth(fakeDb(FULL_COLS, queries), "rss-y", "rss", 5, 0, "boom");
    const insert = queries.find((q) => q[0].startsWith("INSERT INTO source_health"));
    expect(insert![8]).toBe("");
    expect(insert![9]).toBe("0");
  });

  it("migrates pre-existing tables missing the columns", async () => {
    const queries: string[][] = [];
    await ensureSourceHealthColumns(fakeDb(["source", "kind", "attempts"], queries));
    expect(queries.filter((q) => q[0].startsWith("ALTER TABLE"))).toHaveLength(2);
  });
});
