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

// Settings store for the master-resume pointer: D1 first, env as fallback.
function settingsDb(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  return {
    store,
    db: {
      prepare(query: string) {
        const norm = query.trimStart().slice(0, 40);
        return {
          bind: (...params: any[]) => ({
            run: async () => {
              if (norm.startsWith("INSERT INTO settings")) store.set(String(params[0]), String(params[1]));
              return { meta: { changes: 1 } };
            },
            first: async () => {
              if (norm.startsWith("SELECT value FROM settings")) {
                const v = store.get(String(params[0]));
                return v === undefined ? null : { value: v };
              }
              return null;
            },
          }),
        };
      },
      batch: async () => [],
    } as any,
  };
}

describe("settings store", () => {
  it("round-trips a value and upserts on rewrite", async () => {
    const { db, store } = settingsDb();
    const { getSetting, setSetting } = await import("../src/state.js");
    expect(await getSetting(db, "master_resume_sha")).toBeNull();
    await setSetting(db, "master_resume_sha", "a".repeat(64));
    expect(await getSetting(db, "master_resume_sha")).toBe("a".repeat(64));
    await setSetting(db, "master_resume_sha", "b".repeat(64));
    expect(store.get("master_resume_sha")).toBe("b".repeat(64));
  });

  it("getSetting resolves null when the table is missing", async () => {
    const db = { prepare: () => { throw new Error("no such table: settings"); } } as any;
    const { getSetting } = await import("../src/state.js");
    expect(await getSetting(db, "master_resume_sha")).toBeNull();
  });

  it("ensureSettings creates the table idempotently", async () => {
    const seen: string[] = [];
    const db = { prepare: (q: string) => ({ run: async () => { seen.push(q); return {}; } }) } as any;
    const { ensureSettings } = await import("../src/state.js");
    await ensureSettings(db);
    expect(seen.some((q) => q.includes("CREATE TABLE IF NOT EXISTS settings"))).toBe(true);
  });
});

// Named running totals (role_rejected, duplicate_suppressed, dateless_dropped):
// in-memory D1 double applying the ON CONFLICT accumulation.
function countersDb(queries: string[][]) {
  const store = new Map<string, number>();
  const db = {
    prepare(query: string) {
      const norm = query.trimStart().slice(0, 40);
      return {
        bind: (...params: any[]) => ({
          run: async () => {
            queries.push([norm, ...params.map(String)]);
            if (norm.startsWith("INSERT INTO counters")) {
              const key = String(params[0]);
              store.set(key, (store.get(key) ?? 0) + Number(params[1]));
            }
            return { meta: { changes: 1 } };
          },
        }),
        run: async () => {
          queries.push([norm]);
          return {};
        },
      };
    },
    batch: async () => [],
  } as any;
  return { db, store };
}

describe("counters", () => {
  it("ensureCounters creates the table, safe to re-run", async () => {
    const queries: string[][] = [];
    const { db } = countersDb(queries);
    const { ensureCounters } = await import("../src/state.js");
    await ensureCounters(db);
    await ensureCounters(db);
    expect(queries.filter((q) => q[0].includes("CREATE TABLE IF NOT EXISTS counters"))).toHaveLength(2);
    expect(queries.filter((q) => q[0].startsWith("ALTER TABLE"))).toHaveLength(0);
  });

  it("ensureCounters survives a lost creation race", async () => {
    const db = { prepare: () => ({ run: async () => { throw new Error("table already exists"); } }) } as any;
    const { ensureCounters } = await import("../src/state.js");
    await expect(ensureCounters(db)).resolves.toBeUndefined();
  });

  it("bumpCounter accumulates one posting per bump", async () => {
    const queries: string[][] = [];
    const { db, store } = countersDb(queries);
    const { bumpCounter } = await import("../src/state.js");
    await bumpCounter(db, "role_rejected");
    await bumpCounter(db, "role_rejected");
    await bumpCounter(db, "role_rejected");
    expect(store.get("role_rejected")).toBe(3);
    // One statement per bump, no read-back (the upsert holds the running total).
    expect(queries.filter((q) => q[0].startsWith("INSERT INTO counters"))).toHaveLength(3);
    expect(queries.some((q) => q[0].startsWith("SELECT"))).toBe(false);
  });

  it("bumpCounter adds the requested step", async () => {
    const { db, store } = countersDb([]);
    const { bumpCounter } = await import("../src/state.js");
    await bumpCounter(db, "duplicate_suppressed", 5);
    expect(store.get("duplicate_suppressed")).toBe(5);
  });
});
