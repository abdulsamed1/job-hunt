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

describe("queue lifecycle events", () => {
  // In-memory D1 double for the queue consumer: routes the handful of
  // statements index.ts issues, captures binds for assertions, and dedups
  // notification_events inserts D1-style (UNIQUE conflict on repeat keys).
  function memQueueDb(opts: { jobRow?: any; upsertChanges?: number; failJobsSelect?: boolean } = {}) {
    const statements: Array<{ sql: string; params: any[] }> = [];
    const seenEvents = new Set<string>();
    const db = {
      prepare: (sql: string) => ({
        bind: (...params: any[]) => ({
          first: async () => {
            statements.push({ sql, params });
            if (sql.includes("FROM jobs")) {
              if (opts.failJobsSelect) throw new Error("d1 down");
              return opts.jobRow ?? null;
            }
            return null;
          },
          run: async () => {
            statements.push({ sql, params });
            if (sql.includes("INSERT INTO notification_events")) {
              const key = `${params[0]}:${params[1]}:${params[2]}`;
              if (seenEvents.has(key)) {
                throw new Error(
                  "UNIQUE constraint failed: notification_events.runtime, notification_events.job_hash, notification_events.event",
                );
              }
              seenEvents.add(key);
              return { success: true };
            }
            if (sql.includes("INSERT INTO jobs")) return { meta: { changes: opts.upsertChanges ?? 1 } };
            return {};
          },
          all: async () => {
            statements.push({ sql, params });
            return { results: [] };
          },
        }),
        first: async () => null,
        run: async () => ({}),
        all: async () => ({ results: [] }),
      }),
    } as any;
    return { db, statements };
  }

  function memQueues() {
    const mk = () => {
      const sent: any[] = [];
      return {
        sent,
        send: async (body: any) => { sent.push(body); },
        sendBatch: async (msgs: any[]) => { for (const m of msgs) sent.push(m.body); },
      };
    };
    return { DISCOVER_Q: mk(), EVALUATE_Q: mk(), APPLY_Q: mk() };
  }

  function queueEnv(db: any, q: any, extra: any = {}) {
    return {
      DB: db,
      CV_BUCKET: { get: async () => ({ arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }) },
      DISCOVER_Q: q.DISCOVER_Q,
      EVALUATE_Q: q.EVALUATE_Q,
      APPLY_Q: q.APPLY_Q,
      THRESHOLD: "70",
      TELEGRAM_BOT_TOKEN: "tok",
      TELEGRAM_CHAT_ID: "123",
      PROFILE_JSON: JSON.stringify({
        verified_skills: ["Python", "FastAPI"],
        years_of_experience: 5,
        open_to_remote: true,
        sponsorship_required: false,
        citizenship: "Egypt",
        authorized_countries: ["Egypt"],
        blocked_companies: [],
        blocked_keywords: [],
        preferred_keywords: [],
        location: "Cairo, Egypt",
        languages: ["English"],
        full_name: "Test User",
        email: "t@example.com",
        phone: "+201000000000",
      }),
      ...extra,
    } as any;
  }

  // Telegram-capturing fetch stub: telegram URLs record message text and
  // resolve ok; every other URL resolves with the given body text (RSS feed
  // XML for discovery, "{}" for the ATS submit POST which only needs .text()).
  function stubQueueFetch(texts: string[], bodyText: string) {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async (url: string, init: any) => {
      if (String(url).includes("api.telegram.org")) {
        texts.push(JSON.parse(init.body).text);
        return { ok: true, status: 200 };
      }
      return {
        ok: true,
        status: 200,
        text: async () => bodyText,
        arrayBuffer: async () => new TextEncoder().encode(bodyText).buffer,
      };
    };
    return () => { (globalThis as any).fetch = prev; };
  }

  it("gates a disability-targeted posting: records evaluation, emits FILTERED, never queues apply", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db, statements } = memQueueDb({
        jobRow: {
          canonical_hash: "h-gated",
          title: "Technical Support Engineer Intern for People with Disabilities",
          company: "Acme",
          location: "Cairo",
          url: "https://example.com/j/gated",
          description: "Ignite Program. Python and customer support.",
          source: "test-rss",
        },
      });
      const q = memQueues();
      await worker.queue(
        { messages: [{ body: { stage: "evaluate", hash: "h-gated" } }] } as any,
        queueEnv(db, q),
      );
      // D1 records the gated evaluation (ineligible, disability reason).
      const evalInserts = statements.filter((s) => s.sql.includes("INSERT INTO evaluations"));
      expect(evalInserts).toHaveLength(1);
      expect(evalInserts[0].params[2]).toBe(0);
      expect(String(evalInserts[0].params[3])).toMatch(/disabilit/i);
      // Job lands in the pre-filter state, never in the apply queue.
      const states = statements
        .filter((s) => s.sql.includes("UPDATE jobs SET state"))
        .map((s) => s.params[0]);
      expect(states).toContain("PRE_FILTERED_OUT");
      expect(q.APPLY_Q.sent).toHaveLength(0);
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
    } finally {
      restore();
    }
  });

  it("emits SEEKING, TAILORED, APPLICATION_STARTED and SUBMITTED across discover/evaluate/tailor-apply", async () => {
    const { default: worker } = await import("../src/index.js");
    const rss = [
      '<?xml version="1.0"?><rss><channel><item>',
      "<title>Backend Engineer</title>",
      "<link>https://example.com/j/1</link>",
      `<description>${"Python FastAPI backend role building reliable systems. ".repeat(20)}</description>`,
      `<pubDate>${new Date().toUTCString()}</pubDate>`,
      "</item></channel></rss>",
    ].join("");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rss);
    try {
      const { db } = memQueueDb({
        jobRow: {
          canonical_hash: "h",
          title: "Backend Engineer",
          company: "Acme",
          location: "Remote",
          url: "https://boards.greenhouse.io/acme/jobs/123",
          description: "Python FastAPI backend role building reliable systems.",
          source: "greenhouse:acme",
          score: 100,
        },
        upsertChanges: 1,
      });
      const q = memQueues();
      const env = queueEnv(db, q, {
        MASTER_RESUME_SHA: "deadbeef",
        LIVE_APPLY: "true",
        APPROVE_GREENHOUSE: "true",
      });
      await worker.queue(
        { messages: [{ body: { stage: "discover", def: { name: "test-rss", kind: "rss", url: "https://example.com/feed", cadence: "hourly" } } }] } as any,
        env,
      );
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
      const hash = q.EVALUATE_Q.sent[0].hash;
      await worker.queue({ messages: [{ body: { stage: "evaluate", hash } }] } as any, env);
      expect(q.APPLY_Q.sent).toHaveLength(1);
      await worker.queue({ messages: [{ body: { stage: "tailor-apply", hash } }] } as any, env);
      for (const ev of ["SEEKING", "TAILORED", "APPLICATION_STARTED", "SUBMITTED"]) {
        expect(texts.some((t) => t.includes(`[worker] ${ev}:`)), ev).toBe(true);
      }
    } finally {
      restore();
    }
  });

  it("emits RETRY when a queued stage fails", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db } = memQueueDb({ failJobsSelect: true });
      const q = memQueues();
      await worker.queue(
        { messages: [{ body: { stage: "evaluate", hash: "h-retry" } }] } as any,
        queueEnv(db, q),
      );
      expect(texts.some((t) => t.includes("[worker] RETRY:"))).toBe(true);
    } finally {
      restore();
    }
  });
});
