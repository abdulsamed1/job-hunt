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

// upsertJob's gate write, modelled with its own conflict rule: append the
// reason only when it is not already recorded (the instr(...) guard).
function gateDb(changes = 1, seed?: Record<string, any>) {
  const queries: string[][] = [];
  const evaluations = new Map<string, any>(Object.entries(seed ?? {}));
  const db = {
    prepare(query: string) {
      const norm = query.trimStart().slice(0, 40);
      return {
        bind: (...params: any[]) => ({
          run: async () => {
            queries.push([norm, ...params.map(String)]);
            if (norm.startsWith("INSERT INTO jobs")) return { meta: { changes } };
            if (norm.startsWith("INSERT INTO evaluations")) {
              const key = String(params[0]);
              const next = {
                score: Number(params[1]), eligible: Number(params[2]),
                reasoning: String(params[3]), evaluated_at: String(params[4]),
              };
              const prev = evaluations.get(key);
              if (prev && prev.reasoning.includes(next.reasoning)) return { meta: { changes: 0 } };
              evaluations.set(key, {
                ...next,
                reasoning: prev && prev.reasoning ? `${prev.reasoning} | ${next.reasoning}` : next.reasoning,
              });
              return { meta: { changes: 1 } };
            }
            return { meta: { changes: 1 } };
          },
        }),
      };
    },
    batch: async () => [],
  } as any;
  return { db, queries, evaluations };
}

const REJECTED = {
  canonical_hash: "h-mgr", title: "Senior Manager, Application Security", company: "Acme",
  location: "Remote", url: "https://example.com/j/mgr", description: "Python",
  source: "test-rss", posted_at: "", discovered_at: "now", score: 0, remote: true,
};

describe("upsertJob gate detail", () => {
  it("records the reason once and reports it as first-time only", async () => {
    const { db, queries, evaluations } = gateDb(1);
    const { upsertJob } = await import("../src/state.js");
    const opts = { state: "PRE_FILTERED_OUT" as const, detail: "role gate: people-management:manager", outcome: { recorded: false } };
    expect(await upsertJob(db, REJECTED, opts)).toBe(true);
    expect(opts.outcome.recorded).toBe(true);
    expect(evaluations.get("h-mgr").reasoning).toBe("role gate: people-management:manager");
    // Dateless re-discovery: the jobs row still counts as improved (changes 1),
    // but the reason is already there, so nothing new is recorded.
    expect(await upsertJob(db, REJECTED, opts)).toBe(true);
    expect(opts.outcome.recorded).toBe(false);
    expect(evaluations.get("h-mgr").reasoning).toBe("role gate: people-management:manager");
    expect(evaluations.get("h-mgr").evaluated_at).toBeTruthy();
    expect(queries.filter((q) => q[0].startsWith("INSERT INTO evaluations"))).toHaveLength(2);
  });

  it("explains a posting stored before the gate and refreshes evaluated_at", async () => {
    const stale = "2000-01-01T00:00:00.000Z";
    const { db, evaluations } = gateDb(1, {
      "h-mgr": {
        score: 100, eligible: 1,
        reasoning: "serverless score 100 (skills 55.0, seniority 25, location 20)",
        evaluated_at: stale,
      },
    });
    const { upsertJob } = await import("../src/state.js");
    const opts = { state: "PRE_FILTERED_OUT" as const, detail: "role gate: people-management:manager", outcome: { recorded: false } };
    expect(await upsertJob(db, REJECTED, opts)).toBe(true);
    expect(opts.outcome.recorded).toBe(true);
    const ev = evaluations.get("h-mgr");
    expect(ev.score).toBe(0);
    expect(ev.eligible).toBe(0);
    expect(ev.reasoning).toContain("role gate: people-management:manager");
    expect(ev.evaluated_at).not.toBe(stale);
  });

  it("stores a plain posting as DISCOVERED with no evaluation row", async () => {
    const { db, queries, evaluations } = gateDb(1);
    const { upsertJob } = await import("../src/state.js");
    const outcome = { recorded: false };
    expect(await upsertJob(db, { ...REJECTED, canonical_hash: "h-sw", title: "Backend Engineer" }, { outcome })).toBe(true);
    const jobInsert = queries.find((q) => q[0].startsWith("INSERT INTO jobs"))!;
    expect(jobInsert[jobInsert.length - 1]).toBe("DISCOVERED");
    expect(evaluations.size).toBe(0);
    expect(outcome.recorded).toBe(false);
  });
});
