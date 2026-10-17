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
  // statements index.ts issues, captures binds for assertions, dedups
  // notification_events inserts D1-style (UNIQUE conflict on repeat keys) and
  // accumulates counters rows.
  function memQueueDb(opts: { jobRow?: any; upsertChanges?: number; failJobsSelect?: boolean; settings?: Record<string, string> } = {}) {
    const statements: Array<{ sql: string; params: any[] }> = [];
    const seenEvents = new Set<string>();
    const counters = new Map<string, number>();
    const settings = new Map(Object.entries(opts.settings ?? {}));
    const db = {
      prepare: (sql: string) => ({
        bind: (...params: any[]) => ({
          first: async () => {
            statements.push({ sql, params });
            if (sql.includes("FROM settings")) {
              const v = settings.get(String(params[0]));
              return v === undefined ? null : { value: v };
            }
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
            if (sql.includes("INSERT INTO counters")) {
              const key = String(params[0]);
              counters.set(key, (counters.get(key) ?? 0) + Number(params[1]));
              return {};
            }
            if (sql.includes("INTO settings")) { settings.set(String(params[0]), String(params[1])); return {}; }
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
    return { db, statements, counters };
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
  function stubQueueFetch(texts: string[], bodyText: string, urls: string[] = []) {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async (url: string, init: any) => {
      if (String(url).includes("api.telegram.org")) {
        texts.push(JSON.parse(init.body).text);
        return { ok: true, status: 200 };
      }
      urls.push(String(url));
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
      // Job lands in the pre-filter state via a single audit row (no
      // ELIGIBLE/REJECTED round-trip), never in the apply queue.
      const states = statements
        .filter((s) => s.sql.includes("UPDATE jobs SET state"))
        .map((s) => s.params[0]);
      expect(states).toContain("PRE_FILTERED_OUT");
      expect(statements.filter((s) => s.sql.includes("INSERT INTO audit_log"))).toHaveLength(1);
      expect(q.APPLY_Q.sent).toHaveLength(0);
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
    } finally {
      restore();
    }
  });

  it("emits SEEKING, ELIGIBLE, TAILORED, APPLICATION_ATTEMPT and SUBMITTED across discover/evaluate/tailor-apply", async () => {
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
      for (const ev of ["SEEKING", "ELIGIBLE", "TAILORED", "APPLICATION_ATTEMPT", "SUBMITTED"]) {
        expect(texts.some((t) => t.includes(`[worker] ${ev}:`)), ev).toBe(true);
      }
      // The terminal event is distinct from the attempt so it never dedups away.
      expect(texts.some((t) => t.includes("DRY_RUN"))).toBe(false);
      // Shared evaluate vocabulary: no Worker-only EVALUATED event.
      expect(texts.some((t) => t.includes("EVALUATED"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("emits APPLICATION_ATTEMPT then DRY_RUN on a dry-run tailor-apply, never SUBMITTED", async () => {
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
      const { db, statements } = memQueueDb({
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
      const env = queueEnv(db, q, { MASTER_RESUME_SHA: "deadbeef" }); // LIVE_APPLY unset
      await worker.queue(
        { messages: [{ body: { stage: "discover", def: { name: "test-rss", kind: "rss", url: "https://example.com/feed", cadence: "hourly" } } }] } as any,
        env,
      );
      const hash = q.EVALUATE_Q.sent[0].hash;
      await worker.queue({ messages: [{ body: { stage: "evaluate", hash } }] } as any, env);
      await worker.queue({ messages: [{ body: { stage: "tailor-apply", hash } }] } as any, env);
      expect(texts.some((t) => t.includes("[worker] APPLICATION_ATTEMPT:"))).toBe(true);
      expect(texts.some((t) => t.includes("[worker] DRY_RUN:"))).toBe(true);
      expect(texts.some((t) => t.includes("[worker] SUBMITTED:"))).toBe(false);
      const appInserts = statements.filter((s) => s.sql.includes("INSERT INTO applications"));
      expect(appInserts[0].params[1]).toBe("APPLICATION_STARTED");
    } finally {
      restore();
    }
  });

  it("re-runs the disability gate at apply time and never attempts the application", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const urls: string[] = [];
    const restore = stubQueueFetch(texts, "", urls);
    try {
      const { db, statements } = memQueueDb({
        jobRow: {
          canonical_hash: "h-regate",
          title: "Backend Engineer",
          company: "Acme",
          location: "Remote",
          url: "https://boards.greenhouse.io/acme/jobs/123",
          description: "Join our Ignite Program for people with disabilities. Python FastAPI backend role.",
          source: "greenhouse:acme",
          score: 100,
        },
      });
      const q = memQueues();
      const env = queueEnv(db, q, { MASTER_RESUME_SHA: "deadbeef", LIVE_APPLY: "true", APPROVE_GREENHOUSE: "true" });
      await worker.queue(
        { messages: [{ body: { stage: "tailor-apply", hash: "h-regate" } }] } as any,
        env,
      );
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
      expect(texts.some((t) => t.includes("[worker] TAILORED:"))).toBe(false);
      expect(texts.some((t) => t.includes("[worker] APPLICATION_ATTEMPT:"))).toBe(false);
      const states = statements
        .filter((s) => s.sql.includes("UPDATE jobs SET state"))
        .map((s) => s.params[0]);
      expect(states).toContain("PRE_FILTERED_OUT");
      expect(urls.some((u) => u.includes("boards-api.greenhouse.io"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("emits STAGE_FAILED when a queued stage fails", async () => {
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
      expect(texts.some((t) => t.includes("[worker] STAGE_FAILED:"))).toBe(true);
      // Fallback branch (job row unreadable): fixed reason + short hash, and
      // the raw error never leaks into the alert.
      expect(texts.some((t) => t.includes("queued stage failed; see worker logs"))).toBe(true);
      expect(texts.some((t) => t.includes("h-retry"))).toBe(true);
      expect(texts.some((t) => t.includes("d1 down"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("emits STAGE_FAILED with job context when the row lookup succeeds", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db } = memQueueDb({
        jobRow: {
          canonical_hash: "h-retry2",
          title: "Backend Engineer",
          company: "Acme",
          location: "Remote",
          url: "https://example.com/j/2",
          description: "Python FastAPI backend role building reliable systems.",
          source: "greenhouse:acme",
          score: 100,
        },
      });
      const q = memQueues();
      const env = queueEnv(db, q, { MASTER_RESUME_SHA: "deadbeef" });
      env.CV_BUCKET = { get: async () => { throw new Error("r2 exploded"); } };
      await worker.queue(
        { messages: [{ body: { stage: "tailor-apply", hash: "h-retry2" } }] } as any,
        env,
      );
      // Found branch: title/company name the role, fixed reason only.
      expect(texts.some((t) => t.includes("[worker] STAGE_FAILED: Backend Engineer @ Acme"))).toBe(true);
      expect(texts.some((t) => t.includes("queued stage failed; see worker logs"))).toBe(true);
      expect(texts.some((t) => t.includes("r2 exploded"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("apply path prefers the D1 pointer over MASTER_RESUME_SHA", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db } = memQueueDb({
        jobRow: {
          canonical_hash: "h",
          title: "Backend Engineer",
          company: "Acme",
          location: "Remote",
          url: "https://boards.greenhouse.io/acme/jobs/123",
          description: "Python FastAPI backend role.",
          source: "greenhouse:acme",
          score: 90,
        },
        settings: { master_resume_sha: "cafe" },
      });
      const q = memQueues();
      const keys: string[] = [];
      const env = queueEnv(db, q, {
        MASTER_RESUME_SHA: "deadbeef",
        CV_BUCKET: { get: async (key: string) => { keys.push(key); return { arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }; } },
      });
      await worker.queue({ messages: [{ body: { stage: "tailor-apply", hash: "h" } }] } as any, env);
      expect(keys).toEqual(["cvs/master/cafe.pdf"]);
    } finally {
      restore();
    }
  });

  // One posting on one RSS feed, driven through the discover message the
  // scheduled sweep sends (see queue lifecycle events above).
  const rssWith = (title: string, link: string, description: string) => [
    '<?xml version="1.0"?><rss><channel><item>',
    `<title>${title}</title>`,
    `<link>${link}</link>`,
    `<description>${description}</description>`,
    `<pubDate>${new Date().toUTCString()}</pubDate>`,
    "</item></channel></rss>",
  ].join("");
  const discoverMsg = { stage: "discover", def: { name: "test-rss", kind: "rss", url: "https://example.com/feed", cadence: "hourly" } };

  it("stores a manager posting as PRE_FILTERED_OUT and never sends SEEKING or an evaluate message", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith("Senior Manager, Application Security", "https://example.com/j/mgr", "Python FastAPI backend role building reliable systems."));
    try {
      const { db, statements, counters } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, queueEnv(db, q));
      // The row is born pre-filtered: management titles never enter the feed.
      const jobInserts = statements.filter((s) => s.sql.includes("INSERT INTO jobs"));
      expect(jobInserts).toHaveLength(1);
      expect(jobInserts[0].params).toContain("PRE_FILTERED_OUT");
      // The matched rule is stored as the row's reasoning (score 0, ineligible).
      const evalInserts = statements.filter((s) => s.sql.includes("INSERT INTO evaluations"));
      expect(evalInserts).toHaveLength(1);
      expect(evalInserts[0].params[1]).toBe(0);
      expect(evalInserts[0].params[2]).toBe(0);
      expect(evalInserts[0].params[3]).toBe("role gate: people-management:manager");
      expect(counters.get("role_rejected")).toBe(1);
      // FILTERED replaces SEEKING, and no evaluate message is queued.
      const events = statements
        .filter((s) => s.sql.includes("INSERT INTO notification_events"))
        .map((s) => s.params[2]);
      expect(events).toEqual(["FILTERED"]);
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
      expect(texts.some((t) => t.includes("[worker] SEEKING:"))).toBe(false);
      expect(q.EVALUATE_Q.sent).toHaveLength(0);
    } finally {
      restore();
    }
  });

  it("keeps SEEKING for a software posting and queues its evaluation", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith("Senior Software Engineer, Query", "https://example.com/j/1", "Python FastAPI backend role building reliable systems."));
    try {
      const { db, statements } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, queueEnv(db, q));
      const jobInserts = statements.filter((s) => s.sql.includes("INSERT INTO jobs"));
      expect(jobInserts).toHaveLength(1);
      expect(jobInserts[0].params).toContain("DISCOVERED");
      // No gate verdict to record, so the evaluate stage owns the scoring.
      expect(statements.filter((s) => s.sql.includes("INSERT INTO evaluations"))).toHaveLength(0);
      const events = statements
        .filter((s) => s.sql.includes("INSERT INTO notification_events"))
        .map((s) => s.params[2]);
      expect(events).toEqual(["SEEKING"]);
      expect(texts.some((t) => t.includes("[worker] SEEKING:"))).toBe(true);
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
      expect(q.EVALUATE_Q.sent[0].stage).toBe("evaluate");
    } finally {
      restore();
    }
  });

  it("gates evaluation of a manager posting with score 0", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db, statements } = memQueueDb({
        jobRow: {
          canonical_hash: "h-mgr",
          title: "Senior Manager, Application Security",
          company: "Acme",
          location: "Remote",
          url: "https://example.com/j/mgr",
          description: "Python FastAPI backend role building reliable systems.",
          source: "test-rss",
        },
      });
      const q = memQueues();
      await worker.queue({ messages: [{ body: { stage: "evaluate", hash: "h-mgr" } }] } as any, queueEnv(db, q));
      const evalInserts = statements.filter((s) => s.sql.includes("INSERT INTO evaluations"));
      expect(evalInserts).toHaveLength(1);
      expect(evalInserts[0].params[1]).toBe(0);
      expect(evalInserts[0].params[2]).toBe(0);
      expect(String(evalInserts[0].params[3])).toBe("role gate: people-management:manager");
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

  it("caps an unknown-title posting below the eligibility threshold", async () => {
    const { default: worker } = await import("../src/index.js");
    // Unclassified title with perfect skill evidence: scored, capped, never applied.
    const strongSkills = "Build and run resilient payment services. Python and FastAPI are the daily tools, alongside thorough testing, code review, on-call rotations, observability, incident response, mentoring and clear documentation.";
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith("Data Scientist", "https://example.com/j/ds", strongSkills));
    try {
      const { db, statements } = memQueueDb({
        jobRow: {
          canonical_hash: "h-ds",
          title: "Data Scientist",
          company: "Acme",
          location: "Remote",
          url: "https://example.com/j/ds",
          description: strongSkills,
          source: "test-rss",
        },
        upsertChanges: 1,
      });
      const q = memQueues();
      const env = queueEnv(db, q);
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, env);
      // Unknown titles stay visible: stored, notified, queued for evaluation.
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
      await worker.queue({ messages: [{ body: { stage: "evaluate", hash: q.EVALUATE_Q.sent[0].hash } }] } as any, env);
      const evalInserts = statements.filter((s) => s.sql.includes("INSERT INTO evaluations"));
      expect(evalInserts).toHaveLength(1);
      expect(Number(evalInserts[0].params[1])).toBeLessThanOrEqual(69);
      expect(evalInserts[0].params[2]).toBe(0);
      expect(String(evalInserts[0].params[3])).toContain("unknown");
      expect(q.APPLY_Q.sent).toHaveLength(0);
    } finally {
      restore();
    }
  });
});
