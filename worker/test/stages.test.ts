import { describe, expect, it } from "vitest";
import { applyToAts, buildTailoredText, renderPdfBytes } from "../src/stages/pipeline.js";
import { canonicalHash, normalizeUrl, roleFingerprint } from "../src/lib/hash.js";
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
  // notification_events inserts D1-style (UNIQUE conflict on repeat keys),
  // accumulates counters rows and stores evaluations rows under the upsert /
  // evaluate conflict rules. jobs rows are kept too, so the batched duplicate
  // lookup sees what earlier queue messages stored.
  function memQueueDb(opts: {
    jobRow?: any; upsertChanges?: number; failJobsSelect?: boolean;
    failCounters?: boolean; settings?: Record<string, string>;
    seedJobs?: Array<Record<string, any>>;
  } = {}) {
    const statements: Array<{ sql: string; params: any[] }> = [];
    const seenEvents = new Set<string>();
    const counters = new Map<string, number>();
    // Rows that predate this queue invocation: what an earlier sweep, or a
    // deploy before the gate, already stored.
    const jobs = new Map<string, Record<string, any>>(
      (opts.seedJobs || []).map((r) => [String(r.canonical_hash), { ...r }]),
    );
    const evaluations = new Map<string, { score: number; eligible: number; reasoning: string; evaluated_at: string }>();
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
              // Honour the stage's own state guard: a row the stage excludes
              // must read as absent, exactly as it would in D1.
              if (sql.includes("state <> 'DUPLICATE'") && opts.jobRow?.state === "DUPLICATE") return null;
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
            if (sql.includes("INSERT INTO jobs")) {
              // Read binds through the statement's own column list, so the
              // double follows the SQL rather than a positional guess.
              const cols = sql.slice(sql.indexOf("(") + 1, sql.indexOf(")")).split(",").map((c) => c.trim());
              const rec: Record<string, any> = Object.fromEntries(cols.map((c, i) => [c, params[i]]));
              const hash = String(rec.canonical_hash);
              const prev = jobs.get(hash);
              // The real ON CONFLICT clause refreshes the text columns and the
              // fingerprint but never the state: a row's state only moves
              // through setJobState.
              jobs.set(hash, {
                ...rec,
                score: Number(rec.score),
                remote: Number(rec.remote),
                state: prev?.state ?? rec.state,
                role_fingerprint: rec.role_fingerprint ?? prev?.role_fingerprint ?? "",
              });
              return { meta: { changes: opts.upsertChanges ?? 1 } };
            }
            if (sql.includes("UPDATE jobs SET state")) {
              const row = jobs.get(String(params[1]));
              if (row) row.state = params[0];
              return {};
            }
            if (sql.includes("INSERT INTO evaluations")) {
              const key = String(params[0]);
              const next = {
                score: Number(params[1]), eligible: Number(params[2]),
                reasoning: String(params[3]), evaluated_at: String(params[4]),
              };
              const prev = evaluations.get(key);
              // saveEvaluation replaces reasoning; upsertJob's gate write
              // appends, but only when that reason is not already recorded
              // (its instr(...) guard).
              const gate = sql.includes("instr(");
              if (!gate) {
                evaluations.set(key, next);
                return { meta: { changes: 1 } };
              }
              if (prev && prev.reasoning.includes(next.reasoning)) return { meta: { changes: 0 } };
              evaluations.set(key, {
                ...next,
                reasoning: prev && prev.reasoning ? `${prev.reasoning} | ${next.reasoning}` : next.reasoning,
              });
              return { meta: { changes: 1 } };
            }
            if (sql.includes("INSERT INTO counters")) {
              if (opts.failCounters) throw new Error("no such table: counters");
              const key = String(params[0]);
              counters.set(key, (counters.get(key) ?? 0) + Number(params[1]));
              return {};
            }
            if (sql.includes("INTO settings")) { settings.set(String(params[0]), String(params[1])); return {}; }
            return {};
          },
          all: async () => {
            statements.push({ sql, params });
            // findKnownJobs: answer the `col IN (...)` groups from the stored
            // jobs, one param slice per group, in the order they are bound.
            if (sql.includes("FROM jobs") && sql.includes(" IN (")) {
              const groups: Array<[string, string[]]> = [];
              let cursor = 0;
              for (const col of ["canonical_hash", "role_fingerprint"]) {
                const at = sql.indexOf(`${col} IN (`);
                if (at < 0) continue;
                const width = (sql.slice(at, sql.indexOf(")", at)).match(/\?/g) || []).length;
                groups.push([col, params.slice(cursor, cursor + width).map(String)]);
                cursor += width;
              }
              return {
                results: [...jobs.values()].filter((r) =>
                  groups.some(([col, list]) => list.includes(String(r[col] ?? ""))),
                ),
              };
            }
            return { results: [] };
          },
        }),
        first: async () => null,
        run: async () => ({}),
        all: async () => ({ results: [] }),
      }),
    } as any;
    return { db, statements, counters, jobs, evaluations };
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
  // resolve ok; every other URL resolves with the given body — RSS feed XML
  // for discovery, "{}" for the ATS submit POST which only needs .text().
  // A function body serves two different boards out of one batch.
  function stubQueueFetch(texts: string[], body: string | ((url: string) => string), urls: string[] = []) {
    const prev = globalThis.fetch;
    (globalThis as any).fetch = async (url: string, init: any) => {
      if (String(url).includes("api.telegram.org")) {
        texts.push(JSON.parse(init.body).text);
        return { ok: true, status: 200 };
      }
      urls.push(String(url));
      const bodyText = typeof body === "function" ? body(String(url)) : body;
      return {
        ok: true,
        status: 200,
        text: async () => bodyText,
        json: async () => JSON.parse(bodyText || "{}"),
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

  // RSS feed bodies, driven through the discover message the scheduled sweep
  // sends (see queue lifecycle events above).
  const rssItem = (title: string, link: string, description: string) => [
    "<item>",
    `<title>${title}</title>`,
    `<link>${link}</link>`,
    `<description>${description}</description>`,
    `<pubDate>${new Date().toUTCString()}</pubDate>`,
    "</item>",
  ].join("");
  const rssWith = (...items: string[]) =>
    ['<?xml version="1.0"?><rss><channel>', ...items, "</channel></rss>"].join("");
  const discoverMsg = { stage: "discover", def: { name: "test-rss", kind: "rss", url: "https://example.com/feed", cadence: "hourly" } };

  // One requisition on two boards: the same employer slug on a Greenhouse and
  // a Lever board, same title, same remote work spelled differently, two URLs.
  const ghMsg = { stage: "discover", def: { name: "gh-acme", kind: "greenhouse", org: "acme", cadence: "hourly" } };
  const lvMsg = { stage: "discover", def: { name: "lv-acme", kind: "lever", org: "acme", cadence: "hourly" } };
  const GH_JOB = 1;
  const LV_JOB = 9;
  const GH_URL = "https://boards.greenhouse.io/acme/jobs/1";
  const LV_URL = "https://jobs.lever.co/acme/9";
  type BoardJob = { id: number; title: string; url: string; location: string };
  const ghBody = (jobs: BoardJob[]) => JSON.stringify({
    jobs: jobs.map((j) => ({
      id: j.id, title: j.title, absolute_url: j.url,
      location: { name: j.location }, updated_at: new Date().toISOString(),
    })),
  });
  const lvBody = (jobs: BoardJob[]) => JSON.stringify(jobs.map((j) => ({
    id: j.id, text: j.title, hostedUrl: j.url,
    categories: { location: j.location }, createdAt: new Date().toISOString(),
  })));
  const twoBoards = (greenhouse: string, lever: string) =>
    (url: string) => (String(url).includes("greenhouse") ? greenhouse : lever);
  const GH_POSTING = { id: GH_JOB, title: "Backend Engineer", url: GH_URL, location: "Remote, US" };
  const LV_TWIN = { id: LV_JOB, title: "Backend Engineer", url: LV_URL, location: "Worldwide" };

  it("collapses one requisition carried by two boards into one application", async () => {
    const { default: worker } = await import("../src/index.js");
    // Same requisition, same place ("Remote, US" vs "Worldwide"), two boards.
    const bodies = twoBoards(ghBody([GH_POSTING]), lvBody([LV_TWIN]));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, bodies);
    try {
      const { db, jobs, counters, evaluations } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: ghMsg }, { body: lvMsg }] } as any, queueEnv(db, q));
      const winner = canonicalHash(normalizeUrl(GH_URL));
      const twin = canonicalHash(normalizeUrl(LV_URL));
      expect(jobs.get(winner)?.state).toBe("DISCOVERED");
      expect(jobs.get(twin)?.state).toBe("DUPLICATE");
      // The twin says which posting it is standing in for.
      expect(evaluations.get(twin)?.reasoning).toBe(`duplicate of ${winner}`);
      // One requisition, one application: one evaluate message, one SEEKING.
      expect(q.EVALUATE_Q.sent.map((m: any) => m.hash)).toEqual([winner]);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(1);
      expect(counters.get("duplicate_suppressed")).toBe(1);
    } finally {
      restore();
    }
  });

  it("counts a collapsed duplicate once: a re-discovery never re-counts it", async () => {
    const { default: worker } = await import("../src/index.js");
    const bodies = twoBoards(ghBody([GH_POSTING]), lvBody([LV_TWIN]));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, bodies);
    try {
      const { db, jobs, counters, evaluations } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      const env = queueEnv(db, q);
      const winner = canonicalHash(normalizeUrl(GH_URL));
      const twin = canonicalHash(normalizeUrl(LV_URL));
      await worker.queue({ messages: [{ body: ghMsg }, { body: lvMsg }] } as any, env);
      await worker.queue({ messages: [{ body: ghMsg }, { body: lvMsg }] } as any, env);
      // The twin is written again every sweep (the double keeps reporting a
      // changed jobs row) but counted once, and its reason does not grow.
      expect(counters.get("duplicate_suppressed")).toBe(1);
      expect(evaluations.get(twin)?.reasoning).toBe(`duplicate of ${winner}`);
      expect(jobs.get(twin)?.state).toBe("DUPLICATE");
      // The posting that won the requisition never suppresses itself through
      // its own twin, and the owner still hears about it exactly once.
      expect(jobs.get(winner)?.state).toBe("DISCOVERED");
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(1);
    } finally {
      restore();
    }
  });

  it("collapses two copies of one requisition inside a single board batch", async () => {
    const { default: worker } = await import("../src/index.js");
    // One board listing the same opening twice under two jids: the first copy
    // seeds the fingerprint, the second lands in DUPLICATE.
    const GH_TWIN_URL = "https://boards.greenhouse.io/acme/jobs/2";
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, ghBody([
      { id: GH_JOB, title: "Backend Engineer", url: GH_URL, location: "Remote" },
      { id: 2, title: "Backend Engineer", url: GH_TWIN_URL, location: "Remote" },
    ]));
    try {
      const { db, jobs, counters, evaluations } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: ghMsg }] } as any, queueEnv(db, q));
      const winner = canonicalHash(normalizeUrl(GH_URL));
      const twin = canonicalHash(normalizeUrl(GH_TWIN_URL));
      expect(jobs.get(winner)?.state).toBe("DISCOVERED");
      expect(jobs.get(twin)?.state).toBe("DUPLICATE");
      expect(evaluations.get(twin)?.reasoning).toBe(`duplicate of ${winner}`);
      expect(counters.get("duplicate_suppressed")).toBe(1);
      expect(q.EVALUATE_Q.sent.map((m: any) => m.hash)).toEqual([winner]);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(1);
    } finally {
      restore();
    }
  });

  // A row the store already holds, as an earlier sweep or a pre-gate deploy
  // left it.
  const storedJob = (over: Record<string, any>) => ({
    canonical_hash: "", title: "Backend Engineer", company: "acme", location: "Remote",
    url: "", description: "Python FastAPI backend role building reliable systems.",
    source: "lever:acme", posted_at: new Date().toISOString(),
    discovered_at: new Date().toISOString(),
    score: 0, remote: 1, state: "DISCOVERED", role_fingerprint: "", notified: 0,
    ...over,
  });
  const REQ_FP = roleFingerprint("acme", "Backend Engineer", "Remote, US");
  const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

  it("suppresses a posting already in the feed when its requisition is owned elsewhere", async () => {
    const { default: worker } = await import("../src/index.js");
    // The pre-gate shape: the twin's row exists and sits in the feed, with no
    // fingerprint at all, while another board already owns the requisition.
    const winner = canonicalHash(normalizeUrl(GH_URL));
    const twin = canonicalHash(normalizeUrl(LV_URL));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, lvBody([LV_TWIN]));
    try {
      const { db, jobs, statements, counters, evaluations } = memQueueDb({
        upsertChanges: 1,
        seedJobs: [
          storedJob({ canonical_hash: winner, url: GH_URL, state: "DISCOVERED", role_fingerprint: REQ_FP }),
          storedJob({ canonical_hash: twin, url: LV_URL, state: "DISCOVERED" }),
        ],
      });
      const q = memQueues();
      const env = queueEnv(db, q);
      await worker.queue({ messages: [{ body: lvMsg }] } as any, env);
      // The suppression is real: the row leaves the feed, it is not merely
      // annotated while still sitting in it.
      expect(jobs.get(twin)?.state).toBe("DUPLICATE");
      const audit = statements.filter((s) => s.sql.includes("INSERT INTO audit_log"));
      // bind order: job_hash, to_state, timestamp, details (from_state is NULL)
      expect(audit.map((s) => s.params[1])).toContain("DUPLICATE");
      expect(evaluations.get(twin)?.reasoning).toBe(`duplicate of ${winner}`);
      expect(counters.get("duplicate_suppressed")).toBe(1);
      // And it is never applied: no evaluate message, no SEEKING.
      expect(q.EVALUATE_Q.sent).toHaveLength(0);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(0);

      await worker.queue({ messages: [{ body: lvMsg }] } as any, env);
      // Once: the transition happened, so the second sweep re-counts nothing,
      // re-audits nothing and re-explains nothing.
      expect(counters.get("duplicate_suppressed")).toBe(1);
      expect(statements.filter((s) => s.sql.includes("INSERT INTO audit_log"))).toHaveLength(audit.length);
      expect(statements.filter((s) => s.sql.includes("INSERT INTO evaluations"))).toHaveLength(1);
    } finally {
      restore();
    }
  });

  it("keeps a suppressed twin suppressed when its own fingerprint drifts", async () => {
    const { default: worker } = await import("../src/index.js");
    // The board edited the title, so the twin's fingerprint no longer matches
    // anything: no owner is found and the normal path would otherwise reopen a
    // posting the store already parked as a duplicate.
    const twin = canonicalHash(normalizeUrl(LV_URL));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, lvBody([{ ...LV_TWIN, title: "Backend Engineer II" }]));
    try {
      const { db, jobs } = memQueueDb({
        upsertChanges: 1,
        seedJobs: [storedJob({ canonical_hash: twin, url: LV_URL, state: "DUPLICATE", role_fingerprint: "fp-from-2025" })],
      });
      const q = memQueues();
      await worker.queue({ messages: [{ body: lvMsg }] } as any, queueEnv(db, q));
      expect(jobs.get(twin)?.state).toBe("DUPLICATE");
      expect(q.EVALUATE_Q.sent).toHaveLength(0);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(0);
    } finally {
      restore();
    }
  });

  it("never promotes a suppressed twin that was queued before the gate landed", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, "");
    try {
      const { db, statements } = memQueueDb({
        jobRow: {
          canonical_hash: "h-twin", title: "Backend Engineer", company: "acme",
          location: "Remote", url: LV_URL, description: "Python FastAPI backend role.",
          source: "lever:acme", state: "DUPLICATE",
        },
      });
      const q = memQueues();
      await worker.queue({ messages: [{ body: { stage: "evaluate", hash: "h-twin" } }] } as any, queueEnv(db, q));
      // The row reads as absent, so nothing is scored, nothing is applied and
      // nothing is alerted: a twin cannot be promoted by a stale message.
      expect(statements.filter((s) => s.sql.includes("INSERT INTO evaluations"))).toHaveLength(0);
      expect(statements.filter((s) => s.sql.includes("INSERT INTO audit_log"))).toHaveLength(0);
      expect(q.APPLY_Q.sent).toHaveLength(0);
      expect(texts.some((t) => t.includes("[worker] ELIGIBLE:"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("lets a twin through when the owner cannot be applied from", async () => {
    const { default: worker } = await import("../src/index.js");
    // The same requisition, but the owning row was rejected on its merits: it
    // will never be applied, so suppressing the twin would lose the opening.
    const twin = canonicalHash(normalizeUrl(LV_URL));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, lvBody([LV_TWIN]));
    try {
      const { db, jobs, counters } = memQueueDb({
        upsertChanges: 1,
        seedJobs: [
          storedJob({ canonical_hash: canonicalHash(normalizeUrl(GH_URL)), url: GH_URL, state: "REJECTED", role_fingerprint: REQ_FP }),
        ],
      });
      const q = memQueues();
      await worker.queue({ messages: [{ body: lvMsg }] } as any, queueEnv(db, q));
      expect(jobs.get(twin)?.state).toBe("DISCOVERED");
      expect(counters.get("duplicate_suppressed")).toBeUndefined();
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(1);
    } finally {
      restore();
    }
  });

  it("lets a re-posted requisition through once its owner is stale", async () => {
    const { default: worker } = await import("../src/index.js");
    // Ownership is bounded in time: a company re-posting the same remote role
    // next quarter is a new requisition, not a twin of last quarter's row.
    const twin = canonicalHash(normalizeUrl(LV_URL));
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, lvBody([LV_TWIN]));
    try {
      const { db, jobs, counters } = memQueueDb({
        upsertChanges: 1,
        seedJobs: [
          storedJob({
            canonical_hash: canonicalHash(normalizeUrl(GH_URL)), url: GH_URL,
            state: "DISCOVERED", role_fingerprint: REQ_FP, discovered_at: daysAgo(31),
          }),
        ],
      });
      const q = memQueues();
      await worker.queue({ messages: [{ body: lvMsg }] } as any, queueEnv(db, q));
      expect(jobs.get(twin)?.state).toBe("DISCOVERED");
      expect(counters.get("duplicate_suppressed")).toBeUndefined();
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
    } finally {
      restore();
    }
  });

  it("does not collapse the same title at different locations", async () => {
    const { default: worker } = await import("../src/index.js");
    // Same company and role, two places: two postings, two applications.
    const bodies = twoBoards(
      ghBody([GH_POSTING]),
      lvBody([{ ...LV_TWIN, location: "Remote - Europe" }]),
    );
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, bodies);
    try {
      const { db, jobs, counters } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: ghMsg }, { body: lvMsg }] } as any, queueEnv(db, q));
      expect(jobs.get(canonicalHash(normalizeUrl(GH_URL)))?.state).toBe("DISCOVERED");
      expect(jobs.get(canonicalHash(normalizeUrl(LV_URL)))?.state).toBe("DISCOVERED");
      expect(counters.get("duplicate_suppressed")).toBeUndefined();
      expect(q.EVALUATE_Q.sent).toHaveLength(2);
      expect(texts.filter((t) => t.includes("[worker] SEEKING:"))).toHaveLength(2);
    } finally {
      restore();
    }
  });

  it("stores a manager posting as PRE_FILTERED_OUT and never sends SEEKING or an evaluate message", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith(rssItem("Senior Manager, Application Security", "https://example.com/j/mgr", "Python FastAPI backend role building reliable systems.")));
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
    const restore = stubQueueFetch(texts, rssWith(rssItem("Senior Software Engineer, Query", "https://example.com/j/1", "Python FastAPI backend role building reliable systems.")));
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
    const restore = stubQueueFetch(texts, rssWith(rssItem("Data Scientist", "https://example.com/j/ds", strongSkills)));
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
      // Capped, not zeroed: the skills were scored, the verdict just cannot
      // clear the threshold. Reasoning names the cap so FILTERED can say why.
      expect(Number(evalInserts[0].params[1])).toBeGreaterThan(0);
      expect(Number(evalInserts[0].params[1])).toBeLessThanOrEqual(69);
      expect(evalInserts[0].params[2]).toBe(0);
      const reasoning = String(evalInserts[0].params[3]);
      expect(reasoning).toMatch(/serverless score/);
      expect(reasoning).toContain("unknown");
      expect(reasoning).toContain("capped at 69");
      expect(q.APPLY_Q.sent).toHaveLength(0);
    } finally {
      restore();
    }
  });

  it("explains a rejected posting once: a re-discovery never re-counts or re-alerts it", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith(rssItem("Senior Manager, Application Security", "https://example.com/j/mgr", "Python FastAPI backend role building reliable systems.")));
    try {
      // upsertChanges stays 1 for every call: a dateless row counts as
      // "materially improved" forever, so the jobs write reports a change long
      // after the posting was first seen.
      const { db, statements, counters, evaluations } = memQueueDb({ upsertChanges: 1 });
      const q = memQueues();
      const env = queueEnv(db, q);
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, env);
      const hash = canonicalHash(normalizeUrl("https://example.com/j/mgr"));
      const reason = "role gate: people-management:manager";
      expect(counters.get("role_rejected")).toBe(1);
      expect(evaluations.get(hash)?.reasoning).toBe(reason);
      const alerts = texts.filter((t) => t.includes("[worker] FILTERED:")).length;
      expect(alerts).toBe(1);

      await worker.queue({ messages: [{ body: discoverMsg }] } as any, env);
      // Second sweep rewrites the row but records nothing new: no second
      // reason (reasoning cannot grow), no second count, no second alert.
      expect(statements.filter((s) => s.sql.includes("INSERT INTO jobs"))).toHaveLength(2);
      expect(counters.get("role_rejected")).toBe(1);
      expect(evaluations.get(hash)?.reasoning).toBe(reason);
      expect(texts.filter((t) => t.includes("[worker] FILTERED:"))).toHaveLength(alerts);
      expect(q.EVALUATE_Q.sent).toHaveLength(0);
    } finally {
      restore();
    }
  });

  it("explains a posting stored before the gate even when its jobs upsert is a no-op", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const restore = stubQueueFetch(texts, rssWith(rssItem("Senior Manager, Application Security", "https://example.com/j/mgr", "Python FastAPI backend role building reliable systems.")));
    try {
      // upsertChanges 0: the row is already in the table with a full location,
      // date and description, so nothing about it is "materially improved" — the
      // shape of a posting stored before this gate existed.
      const { db, statements, counters, evaluations } = memQueueDb({ upsertChanges: 0 });
      const q = memQueues();
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, queueEnv(db, q));
      const hash = canonicalHash(normalizeUrl("https://example.com/j/mgr"));
      // Never explained before, so it is explained, counted and alerted now.
      expect(evaluations.get(hash)?.reasoning).toBe("role gate: people-management:manager");
      expect(counters.get("role_rejected")).toBe(1);
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
      expect(q.EVALUATE_Q.sent).toHaveLength(0);
      // The jobs row was left alone: no queue message, no SEEKING.
      expect(statements.filter((s) => s.sql.includes("UPDATE jobs SET state"))).toHaveLength(0);
      expect(texts.some((t) => t.includes("[worker] SEEKING:"))).toBe(false);
    } finally {
      restore();
    }
  });

  it("stores every posting even when the role_rejected counter cannot be written", async () => {
    const { default: worker } = await import("../src/index.js");
    const texts: string[] = [];
    const feed = rssWith(
      rssItem("Senior Manager, Application Security", "https://example.com/j/mgr", "Python FastAPI backend role building reliable systems."),
      rssItem("Senior Software Engineer, Query", "https://example.com/j/1", "Python FastAPI backend role building reliable systems."),
    );
    const restore = stubQueueFetch(texts, feed);
    try {
      const { db, statements } = memQueueDb({ upsertChanges: 1, failCounters: true });
      const q = memQueues();
      // Counters are observability: a failed bump must not abandon the rest of
      // the source's postings.
      await worker.queue({ messages: [{ body: discoverMsg }] } as any, queueEnv(db, q));
      expect(statements.filter((s) => s.sql.includes("INSERT INTO jobs"))).toHaveLength(2);
      expect(q.EVALUATE_Q.sent).toHaveLength(1);
      expect(texts.some((t) => t.includes("[worker] FILTERED:"))).toBe(true);
      expect(texts.some((t) => t.includes("STAGE_FAILED"))).toBe(false);
    } finally {
      restore();
    }
  });
});
