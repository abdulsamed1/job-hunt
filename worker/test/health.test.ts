import { describe, expect, it } from "vitest";

// /health must surface the notification ledger so verify_deploy can fail a
// deploy whose schema predates the ledger migration. `null` = unreadable.
function healthEnv(notificationFirst: () => Promise<any>) {
  return {
    DB: {
      prepare: (sql: string) => ({
        first: sql.includes("notification_events")
          ? notificationFirst
          : async () => ({ n: 7 }),
        bind: () => ({ first: async () => ({ n: 7 }) }),
      }),
    },
    THRESHOLD: "70",
  } as any;
}

describe("/health notification ledger", () => {
  it("reports the ledger count when the table exists", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/health"), healthEnv(async () => ({ n: 12 })),
    );
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
    expect(body.jobs).toBe(7);
    expect(body.notification_events).toBe(12);
  });

  it("reports null when the table is missing (pre-migration deploy)", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/health"),
      healthEnv(async () => { throw new Error('no such table: notification_events'); }),
    );
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
    expect(body.notification_events).toBeNull();
  });
});

function healthEnvWithPointer(settingsRow: any, extra: any = {}) {
  return {
    DB: {
      prepare: (sql: string) => ({
        first: async () => (sql.includes("notification_events") ? { n: 0 } : { n: 7 }),
        bind: () => ({ first: async () => (sql.includes("FROM settings") ? settingsRow : { n: 7 }) }),
      }),
    },
    THRESHOLD: "70",
    ...extra,
  } as any;
}

describe("/health master resume pointer", () => {
  it("reports the D1 pointer when set", async () => {
    const sha = "c".repeat(64);
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(new Request("https://worker/health"), healthEnvWithPointer({ value: sha }));
    const body: any = await res.json();
    expect(body.master_resume_sha).toBe(sha);
  });

  it("falls back to the MASTER_RESUME_SHA secret", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/health"),
      healthEnvWithPointer(null, { MASTER_RESUME_SHA: "d".repeat(64) }),
    );
    const body: any = await res.json();
    expect(body.master_resume_sha).toBe("d".repeat(64));
  });

  it("reports null when neither pointer nor secret is set", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(new Request("https://worker/health"), healthEnvWithPointer(null));
    const body: any = await res.json();
    expect(body.master_resume_sha).toBeNull();
  });
});

describe("/health feed-quality counters", () => {
  it("exposes role_rejected, duplicate_suppressed and dateless_dropped", async () => {
    const db = {
      prepare: (sql: string) => ({
        first: async () => (sql.includes("notification_events") ? { n: 0 } : { n: 7 }),
        all: async () => (sql.includes("counters")
          ? { results: [
              { key: "role_rejected", value: 2 },
              { key: "duplicate_suppressed", value: 3 },
              { key: "dateless_dropped", value: 4 },
              { key: "intake_capped", value: 5 },
            ] }
          : { results: [] }),
        bind: () => ({ first: async () => ({ n: 7 }), all: async () => ({ results: [] }) }),
      }),
    };
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(new Request("https://worker/health"), { DB: db, THRESHOLD: "70" } as any);
    const body: any = await res.json();
    expect(body.role_rejected).toBe(2);
    expect(body.duplicate_suppressed).toBe(3);
    expect(body.dateless_dropped).toBe(4);
    expect(body.intake_capped).toBe(5);
  });
});

describe("/coverage feed-quality counters", () => {
  it("reports the feed-quality counters and dated vs proxy freshness", async () => {
    const now = new Date().toISOString();
    const jobs = [
      { title: "Backend Engineer", company: "Acme", source: "lever:acme", posted_at: now, state: "DISCOVERED" },
      { title: "Frontend Engineer", company: "Beta", source: "greenhouse:beta", posted_at: now, state: "DISCOVERED" },
      { title: "Manager", company: "Gamma", source: "rss:x", posted_at: "", state: "PRE_FILTERED_OUT" },
      { title: "Dup", company: "Delta", source: "rss:y", posted_at: now, state: "DUPLICATE" },
      { title: "Rejected", company: "Epsilon", source: "rss:z", posted_at: now, state: "PRE_FILTERED_OUT" },
    ];
    const counters = [
      { key: "role_rejected", value: 1 },
      { key: "duplicate_suppressed", value: 1 },
      { key: "dateless_dropped", value: 1 },
    ];
    const queries: string[] = [];
    const rows = (sql: string) => {
      if (sql.includes("source_health")) return { results: [] };
      if (sql.includes("FROM jobs")) return { results: jobs };
      if (sql.includes("counters")) return { results: counters };
      return { results: [] };
    };
    const db = {
      prepare: (sql: string) => {
        queries.push(sql);
        return {
          first: async () => (sql.includes("source_health") ? { configured: 5, yielding: 3, attempts: 10, last_seen: "x" } : { n: 0 }),
          all: async () => rows(sql),
          bind: () => ({
            first: async () => ({ n: 0 }),
            all: async () => rows(sql),
          }),
        };
      },
    };
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(new Request("https://worker/coverage"), { DB: db, THRESHOLD: "70" } as any);
    const body: any = await res.json();
    expect(body.dated_fresh).toBe(1);
    expect(body.proxy_fresh).toBe(1);
    expect(body.dateless_dropped).toBe(1);
    expect(body.duplicate_suppressed).toBe(1);
    expect(body.role_rejected).toBe(1);
    expect(body.live_software).toBe(2);
  });

  it("reads the jobs table through a window and a limit, never in full", async () => {
    // The jobs table is cumulative history: an unbounded SELECT would grow
    // with the store instead of with the feed, and would report every company
    // ever stored as live software.
    const queries: string[] = [];
    const db = {
      prepare: (sql: string) => {
        queries.push(sql);
        return {
          first: async () => (sql.includes("source_health") ? { configured: 0, yielding: 0, attempts: 0, last_seen: null } : { n: 0 }),
          all: async () => ({ results: [] }),
          bind: () => ({
            first: async () => ({ n: 0 }),
            all: async () => ({ results: [] }),
          }),
        };
      },
    };
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(new Request("https://worker/coverage"), { DB: db, THRESHOLD: "70" } as any);
    expect(res.status).toBe(200);
    const jobsQuery = queries.find((q) => q.includes("FROM jobs"));
    expect(jobsQuery).toBeTruthy();
    expect(jobsQuery).toContain("discovered_at >=");
    expect(jobsQuery).toContain("LIMIT");
    // No aggregate fallback: the endpoint must not answer from a view of
    // columns the real D1 table does not have.
    expect(queries.some((q) => q.includes("dated_fresh"))).toBe(false);
    expect(queries.some((q) => q.includes("COUNT(DISTINCT company)"))).toBe(false);
  });
});
