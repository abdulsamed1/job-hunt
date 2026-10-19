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

// D1 jobs-table migration: PRAGMA answers the live column list and an
// ALTER really adds its column, so a second run sees the migrated table.
function jobsTableDb(cols: string[], queries: string[][]) {
  const db = {
    prepare(query: string) {
      const norm = query.trimStart().slice(0, 60);
      return {
        bind: (...params: any[]) => {
          queries.push([norm, ...params.map(String)]);
          return { run: async () => ({}) };
        },
        all: async () => {
          queries.push([norm]);
          return { results: cols.map((name) => ({ name })) };
        },
        run: async () => {
          queries.push([norm]);
          const added = (query.match(/ADD COLUMN (\w+)/i) || [])[1];
          if (added && !cols.includes(added)) cols.push(added);
          return {};
        },
      };
    },
    batch: async () => [],
  } as any;
  return db;
}

const PRE_FINGERPRINT_COLS = [
  "canonical_hash", "title", "company", "location", "url", "description",
  "source", "posted_at", "discovered_at", "score", "remote", "state", "notified",
];

describe("role fingerprint column", () => {
  it("adds the column once, then only re-asserts the index", async () => {
    const cols = [...PRE_FINGERPRINT_COLS];
    const queries: string[][] = [];
    const db = jobsTableDb(cols, queries);
    const { ensureRoleFingerprint } = await import("../src/state.js");
    await ensureRoleFingerprint(db);
    expect(cols).toContain("role_fingerprint");
    expect(queries.filter((q) => q[0].startsWith("ALTER TABLE jobs"))).toHaveLength(1);
    expect(queries.filter((q) => q[0].startsWith("PRAGMA table_info(jobs)"))).toHaveLength(1);
    // The index is what the duplicate lookup rides on.
    expect(queries.filter((q) => q[0].startsWith("CREATE INDEX IF NOT EXISTS idx_jobs_role_fp"))).toHaveLength(1);
    // Idempotent: second run finds the column, so no second ALTER.
    await ensureRoleFingerprint(db);
    expect(queries.filter((q) => q[0].startsWith("ALTER TABLE jobs"))).toHaveLength(1);
    expect(queries.filter((q) => q[0].startsWith("CREATE INDEX IF NOT EXISTS idx_jobs_role_fp"))).toHaveLength(2);
  });

  it("tolerates a lost migration race", async () => {
    // The PRAGMA answers the pre-migration table, so the ALTER really is
    // attempted -- and another invocation won it, so it throws.
    const cols = [...PRE_FINGERPRINT_COLS];
    const queries: string[][] = [];
    let attempted = false;
    const db = {
      prepare(query: string) {
        const norm = query.trimStart().slice(0, 60);
        return {
          all: async () => ({ results: cols.map((name) => ({ name })) }),
          run: async () => {
            queries.push([norm]);
            if (/ADD COLUMN/i.test(query)) {
              attempted = true;
              throw new Error("duplicate column name: role_fingerprint");
            }
            return {};
          },
        };
      },
      batch: async () => [],
    } as any;
    const { ensureRoleFingerprint } = await import("../src/state.js");
    await expect(ensureRoleFingerprint(db)).resolves.toBeUndefined();
    expect(attempted).toBe(true);
    // The index still lands after the lost race.
    expect(queries.filter((q) => q[0].startsWith("CREATE INDEX IF NOT EXISTS idx_jobs_role_fp"))).toHaveLength(1);
  });

  it("survives a table it cannot inspect", async () => {
    const queries: string[] = [];
    const db = {
      prepare: (query: string) => ({
        all: async () => { throw new Error("no such table: jobs"); },
        run: async () => { queries.push(query.trimStart().slice(0, 60)); return {}; },
      }),
    } as any;
    const { ensureRoleFingerprint } = await import("../src/state.js");
    await expect(ensureRoleFingerprint(db)).resolves.toBeUndefined();
    expect(queries.some((q) => q.startsWith("CREATE INDEX IF NOT EXISTS idx_jobs_role_fp"))).toBe(true);
  });
});

// The dedupe lookup: one SELECT answering two IN lists, keyed by both
// identifiers so the caller can tell "mine again" from "another board".
function knownJobsDb(
  rows: Array<{ canonical_hash: string; role_fingerprint: string; state: string; discovered_at?: string }>,
  queries: string[][],
) {
  const db = {
    prepare(query: string) {
      return {
        bind: (...params: any[]) => ({
          all: async () => {
            queries.push([query, ...params.map(String)]);
            // Walk the WHERE clause in bind order: one slice of params per
            // `col IN (...)` group, exactly as the real statement binds them.
            const clauses: Array<[string, string[]]> = [];
            let cursor = 0;
            for (const col of ["canonical_hash", "role_fingerprint"]) {
              const at = query.indexOf(`${col} IN (`);
              if (at < 0) continue;
              const group = query.slice(at, query.indexOf(")", at));
              const width = (group.match(/\?/g) || []).length;
              clauses.push([col, params.slice(cursor, cursor + width).map(String)]);
              cursor += width;
            }
            const matched = rows.filter((r) =>
              clauses.some(([col, list]) => list.includes(String((r as any)[col] ?? ""))),
            );
            return { results: matched };
          },
        }),
      };
    },
    batch: async () => [],
  } as any;
  return db;
}

describe("findKnownJobs", () => {
  const SEEN_AT = "2026-10-01T00:00:00.000Z";
  const STORED = [
    { canonical_hash: "h1", role_fingerprint: "fp1", state: "DISCOVERED", discovered_at: SEEN_AT },
    { canonical_hash: "h2", role_fingerprint: "fp2", state: "DUPLICATE", discovered_at: SEEN_AT },
    { canonical_hash: "h3", role_fingerprint: "fp3", state: "ELIGIBLE", discovered_at: SEEN_AT },
  ];

  it("returns only identifiers already stored, each with its identity", async () => {
    const queries: string[][] = [];
    const { findKnownJobs } = await import("../src/state.js");
    const known = await findKnownJobs(knownJobsDb(STORED, queries), ["h1", "h-nope"], ["fp2", "fp-nope"]);
    // Both halves: a posting's own fingerprint is stored the moment it is, so
    // the caller needs the row's canonical_hash to answer "me again". State and
    // discovered_at ride along: the caller decides which rows own a
    // requisition (still applicable, still recent).
    // The keys are namespaced: a canonical hash and a role fingerprint are
    // both bare digests, so one kind must never resolve to the other.
    expect(known.get("h:h1")).toEqual({
      canonical_hash: "h1", role_fingerprint: "fp1", state: "DISCOVERED", discovered_at: SEEN_AT,
    });
    expect(known.get("f:fp2")).toEqual({
      canonical_hash: "h2", role_fingerprint: "fp2", state: "DUPLICATE", discovered_at: SEEN_AT,
    });
    // Nothing for identifiers the store does not carry.
    expect(known.get("h:h-nope")).toBeUndefined();
    expect(known.get("f:fp-nope")).toBeUndefined();
    expect(known.get("f:fp3")).toBeUndefined();
    // One batched read per source invocation, not one per posting.
    expect(queries).toHaveLength(1);
    expect(queries[0][0]).toContain("SELECT canonical_hash, role_fingerprint");
    expect(queries[0][0]).toContain("discovered_at");
    expect(queries[0][0]).toContain("canonical_hash IN (");
    expect(queries[0][0]).toContain("OR role_fingerprint IN (");
  });

  it("keeps the two identifier kinds in separate key spaces", async () => {
    // A row whose canonical_hash is "fp1" and whose fingerprint is "fp1" is
    // the collision case: without a namespace the fingerprint lookup would
    // answer with the hash's identity and the other way round.
    const queries: string[][] = [];
    const { findKnownJobs, hashKey, fingerprintKey } = await import("../src/state.js");
    const known = await findKnownJobs(
      knownJobsDb([{ canonical_hash: "fp1", role_fingerprint: "fp1", state: "DISCOVERED", discovered_at: SEEN_AT }], queries),
      ["fp1"],
      ["fp1"],
    );
    expect(known.get(hashKey("fp1"))?.canonical_hash).toBe("fp1");
    expect(known.get(fingerprintKey("fp1"))?.role_fingerprint).toBe("fp1");
    // The two keys are different keys.
    expect(hashKey("fp1")).not.toBe(fingerprintKey("fp1"));
  });

  it("still reads a fingerprint-only call", async () => {
    // A caller with no hashes to offer must not get a silent empty answer.
    const queries: string[][] = [];
    const { findKnownJobs } = await import("../src/state.js");
    const known = await findKnownJobs(knownJobsDb(STORED, queries), [], ["fp1"]);
    expect(queries).toHaveLength(1);
    expect(queries[0][0]).toContain("role_fingerprint IN (");
    expect(queries[0][0]).not.toContain("canonical_hash IN (");
    expect(known.get("f:fp1")?.canonical_hash).toBe("h1");
  });

  it("chunks the two lists and de-duplicates their bindings", async () => {
    const queries: string[][] = [];
    const { findKnownJobs } = await import("../src/state.js");
    const hashes = Array.from({ length: 60 }, (_, i) => `h${i}`);
    const fps = Array.from({ length: 51 }, (_, i) => `fp${i}`);
    // Not a measured production budget: a source is capped at 40 postings by
    // enrichDescriptions(remote.slice(0, 40)), so in production this is always
    // one statement. The chunking is the guard for that cap moving, and the
    // guard is what keeps the statement legal if it ever does.
    await findKnownJobs(knownJobsDb([], queries), hashes, fps);
    // 2 hash chunks x 2 fingerprint chunks, and no IN group ever carries more
    // than 50 bindings (D1's per-query parameter budget).
    expect(queries).toHaveLength(4);
    for (const q of queries) {
      const groups = [...q[0].matchAll(/IN \(([^)]*)\)/g)].map((m) => (m[1].match(/\?/g) || []).length);
      expect(groups.length).toBeGreaterThan(0);
      expect(Math.max(...groups)).toBeLessThanOrEqual(50);
      expect(q.length - 1).toBeLessThanOrEqual(100);
    }

    const dupes: string[][] = [];
    await findKnownJobs(knownJobsDb([], dupes), ["h1", "h1"], ["fp1", "fp1"]);
    expect(dupes).toHaveLength(1);
    expect(dupes[0].slice(1)).toEqual(["h1", "fp1"]);
  });

  it("costs no query when a source yields nothing", async () => {
    const queries: string[][] = [];
    const { findKnownJobs } = await import("../src/state.js");
    expect(await findKnownJobs(knownJobsDb([], queries), [], [])).toEqual(new Map());
    expect(queries).toHaveLength(0);
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
              // Real saveEvaluation replaces reasoning; this double routes
              // upsertJob's gate write, which appends only when the reason is
              // not already recorded (its instr(...) guard).
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

  it("explains a rejected posting whose jobs upsert was a no-op", async () => {
    const { db, evaluations } = gateDb(0);
    const { upsertJob } = await import("../src/state.js");
    const outcome = { recorded: false };
    // Nothing about the row changed (pre-gate shape), yet the posting has still
    // never been explained, so the reason is written and reported as first-time.
    expect(await upsertJob(db, REJECTED, { state: "PRE_FILTERED_OUT", detail: "role gate: people-management:manager", outcome })).toBe(false);
    expect(outcome.recorded).toBe(true);
    expect(evaluations.get("h-mgr").reasoning).toBe("role gate: people-management:manager");
  });

  it("resets a reused outcome object instead of leaking a stale recorded", async () => {
    const { db } = gateDb(1);
    const { upsertJob } = await import("../src/state.js");
    const outcome = { recorded: false };
    const reject = { state: "PRE_FILTERED_OUT" as const, detail: "role gate: people-management:manager", outcome };
    expect(await upsertJob(db, REJECTED, reject)).toBe(true);
    expect(outcome.recorded).toBe(true);
    // A posting that takes the plain path never records a reason, so a reused
    // object must come back false rather than keep the previous posting's true.
    expect(await upsertJob(db, { ...REJECTED, canonical_hash: "h-sw", title: "Backend Engineer" }, { outcome })).toBe(true);
    expect(outcome.recorded).toBe(false);
    // And the same rejected posting again: its reason is recorded already.
    expect(await upsertJob(db, REJECTED, reject)).toBe(true);
    expect(outcome.recorded).toBe(false);
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
    expect(jobInsert).toContain("DISCOVERED");
    expect(evaluations.size).toBe(0);
    expect(outcome.recorded).toBe(false);
  });

  it("stores the role fingerprint the caller offers, blank when it offers none", async () => {
    const { db, queries } = gateDb(1);
    const { upsertJob } = await import("../src/state.js");
    await upsertJob(db, REJECTED, { state: "PRE_FILTERED_OUT", role_fingerprint: "fp-mgr" });
    const withFp = queries.find((q) => q[0].startsWith("INSERT INTO jobs"))!;
    // Last bound column is role_fingerprint; the state sits just before it.
    expect(withFp[withFp.length - 1]).toBe("fp-mgr");
    expect(withFp[withFp.length - 2]).toBe("PRE_FILTERED_OUT");

    const { db: plain, queries: plainQueries } = gateDb(1);
    await upsertJob(plain, { ...REJECTED, canonical_hash: "h-sw" });
    const blank = plainQueries.find((q) => q[0].startsWith("INSERT INTO jobs"))!;
    expect(blank[blank.length - 1]).toBe("");
  });

  it("explains a cross-board duplicate once, like a role reject", async () => {
    const { db, queries, evaluations } = gateDb(1);
    const { upsertJob } = await import("../src/state.js");
    const opts = { state: "DUPLICATE" as const, detail: "duplicate of h1", role_fingerprint: "fp1" };
    expect(await upsertJob(db, { ...REJECTED, canonical_hash: "h2" }, opts)).toBe(true);
    expect(evaluations.get("h2").reasoning).toBe("duplicate of h1");
    // A re-discovery never grows the reason, so the caller can read `recorded`
    // as "this twin is new to the store".
    const outcome = { recorded: false };
    expect(await upsertJob(db, { ...REJECTED, canonical_hash: "h2" }, { ...opts, outcome })).toBe(true);
    expect(outcome.recorded).toBe(false);
    expect(evaluations.get("h2").reasoning).toBe("duplicate of h1");
    expect(queries.filter((q) => q[0].startsWith("INSERT INTO evaluations"))).toHaveLength(2);
  });
});
