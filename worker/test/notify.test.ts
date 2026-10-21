import { describe, expect, it } from "vitest";
import {
  alert,
  DEFAULT_TELEGRAM_EVENTS,
  formatJobAlert,
  formatPostedAge,
  notifyEventOnce,
  parseTelegramEvents,
} from "../src/lib/notify.js";

const ENV = { TELEGRAM_BOT_TOKEN: "tok", TELEGRAM_CHAT_ID: "123" };
/** Every event the Worker can report, explicitly allowed. */
const ENV_ALL = {
  ...ENV,
  TELEGRAM_EVENTS:
    "SEEKING,FILTERED,ELIGIBLE,TAILORED,APPLICATION_ATTEMPT,SUBMITTED,DRY_RUN,FAILED,STAGE_FAILED",
};

function stubFetch(handler: (url: string, init: any) => any) {
  const prev = globalThis.fetch;
  const calls: any[] = [];
  (globalThis as any).fetch = async (url: string, init: any) => {
    calls.push([url, init]);
    return handler(url, init);
  };
  return { calls, restore: () => { globalThis.fetch = prev; } };
}

const okResp = () => ({ ok: true, status: 200 });

// In-memory D1 double: `seen` holds (runtime, hash, event) ledger keys and
// throws a D1-style UNIQUE error on duplicate inserts.
function memDb(seeded: string[] = []) {
  const seen = new Set<string>(seeded);
  const writes: any[][] = [];
  return {
    writes,
    seen,
    db: {
      prepare: (sql: string) => ({
        bind: (...a: any[]) => ({
          run: async () => {
            writes.push([sql, a]);
            if (sql.includes("notification_events") && sql.startsWith("INSERT")) {
              const key = `${a[0]}:${a[1]}:${a[2]}`;
              if (seen.has(key)) {
                throw new Error(
                  "UNIQUE constraint failed: notification_events.runtime, notification_events.job_hash, notification_events.event",
                );
              }
              seen.add(key);
              return { success: true };
            }
            return {};
          },
        }),
      }),
    },
  };
}

const ROW = { title: "Backend Engineer", company: "Acme", url: "https://x/1", score: 95 };

describe("alert", () => {
  it("happy: sends and returns true", async () => {
    const s = stubFetch(async () => okResp());
    try {
      expect(await alert(ENV, "hello")).toBe(true);
      expect(s.calls).toHaveLength(1);
      expect(s.calls[0][0]).toContain("/bottok/sendMessage");
    } finally { s.restore(); }
  });

  it("bad: no credentials sends nothing", async () => {
    const s = stubFetch(async () => okResp());
    try {
      expect(await alert({}, "hello")).toBe(false);
      expect(await alert({ TELEGRAM_BOT_TOKEN: "t" }, "hello")).toBe(false);
      expect(s.calls).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("bad: network throw resolves false, never rejects", async () => {
    const s = stubFetch(async () => { throw new Error("boom"); });
    try {
      await expect(alert(ENV, "hello")).resolves.toBe(false);
    } finally { s.restore(); }
  });

  it("bad: telegram 403/400 resolves false", async () => {
    const s = stubFetch(async () => ({ ok: false, status: 403 }));
    try {
      expect(await alert(ENV, "hello")).toBe(false);
    } finally { s.restore(); }
  });

  it("edge: truncates overlong text at 3000 chars", async () => {
    const s = stubFetch(async () => okResp());
    try {
      await alert(ENV, "x".repeat(5000));
      expect(JSON.parse(s.calls[0][1].body).text).toHaveLength(3000);
    } finally { s.restore(); }
  });

  it("edge: empty text still sends (caller formats)", async () => {
    const s = stubFetch(async () => okResp());
    try {
      expect(await alert(ENV, "")).toBe(true);
    } finally { s.restore(); }
  });
});

describe("formatJobAlert", () => {
  it("happy: source-tagged seeking event", () => {
    const t = formatJobAlert("worker", ROW, "SEEKING", "new posting");
    expect(t.startsWith("[worker] SEEKING: Backend Engineer @ Acme")).toBe(true);
    expect(t).toContain("new posting");
    expect(t).toContain("https://x/1");
  });

  it("happy: seeking, submit, and failure each carry their own event tag", () => {
    expect(formatJobAlert("worker", ROW, "SEEKING", "found")).toContain("[worker] SEEKING:");
    expect(formatJobAlert("worker", ROW, "SUBMITTED", "ok")).toContain("[worker] SUBMITTED:");
    expect(formatJobAlert("worker", ROW, "FAILED", "resume bytes missing")).toContain("resume bytes missing");
  });

  it("happy: local runtime tags its own source", () => {
    expect(formatJobAlert("local", ROW, "TAILORED", "cv saved").startsWith("[local] TAILORED:")).toBe(true);
  });

  it("edge: missing fields fall back, never crash", () => {
    const t = formatJobAlert("worker", {}, "FAILED", "");
    expect(t).toContain("Untitled role");
    expect(t).toContain("Unknown company");
  });

  it("edge: long title/company/detail/url stay bounded at 3000 chars", () => {
    const t = formatJobAlert(
      "worker",
      { title: "t".repeat(500), company: "c".repeat(500), url: "https://x/" + "u".repeat(2000) },
      "FAILED",
      "d".repeat(5000),
    );
    expect(t.length).toBeLessThanOrEqual(3000);
  });
});

describe("notifyEventOnce", () => {
  it("bad: credentials unset resolves skipped without claiming or sending", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      await expect(notifyEventOnce(m.db, {}, "worker", "h", "SUBMITTED", ROW, "x")).resolves.toBe("skipped");
      expect(s.calls).toHaveLength(0);
      expect(m.writes).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("happy: same (runtime, hash, event) sends once", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "ok")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "ok")).toBe("skipped");
      expect(s.calls).toHaveLength(1);
      expect(JSON.parse(s.calls[0][1].body).text).toContain("[worker] SUBMITTED:");
    } finally { s.restore(); }
  });

  it("happy: different events on the same job each send", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "APPLICATION_ATTEMPT", ROW, "go")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "ok")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "FAILED", ROW, "late failure")).toBe("sent");
      expect(s.calls).toHaveLength(3);
    } finally { s.restore(); }
  });

  it("happy: same hash+event under another runtime sends again", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "x")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "local", "h", "SUBMITTED", ROW, "x")).toBe("sent");
      expect(s.calls).toHaveLength(2);
    } finally { s.restore(); }
  });

  it("bad: telegram down resolves failed without throwing (no retry storm)", async () => {
    const s = stubFetch(async () => { throw new Error("down"); });
    const m = memDb();
    try {
      await expect(notifyEventOnce(m.db, ENV, "worker", "h", "FAILED", ROW, "x")).resolves.toBe("failed");
      expect(s.calls).toHaveLength(1);
      // The ledger row was claimed before sending, so a retry skips quietly.
      await expect(notifyEventOnce(m.db, ENV, "worker", "h", "FAILED", ROW, "x")).resolves.toBe("skipped");
      expect(s.calls).toHaveLength(1);
    } finally { s.restore(); }
  });

  it("bad: pre-seeded event key skips without sending", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb(["worker:h:SUBMITTED"]);
    try {
      await expect(notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "x")).resolves.toBe("skipped");
      expect(s.calls).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("bad: db insert error resolves failed, never throws, never sends", async () => {
    const s = stubFetch(async () => okResp());
    const db = { prepare: () => { throw new Error("d1"); } };
    try {
      await expect(notifyEventOnce(db, ENV, "worker", "h", "SUBMITTED", ROW, "")).resolves.toBe("failed");
      expect(s.calls).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("happy: a sent event writes exactly one ledger claim and no legacy jobs flag", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      await expect(notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "")).resolves.toBe("sent");
      expect(m.writes).toHaveLength(1);
      expect(String(m.writes[0][0])).toContain("INSERT INTO notification_events");
      expect(m.writes.some((w) => String(w[0]).includes("UPDATE jobs"))).toBe(false);
    } finally { s.restore(); }
  });

  it("bad: bare 'exists' in another context resolves failed, not skipped", async () => {
    const s = stubFetch(async () => okResp());
    const db = {
      prepare: () => ({
        bind: () => ({
          first: async () => null,
          run: async () => { throw new Error("table jobs exists-but-broken"); },
        }),
      }),
    };
    try {
      await expect(notifyEventOnce(db, ENV, "worker", "h", "SUBMITTED", ROW, "")).resolves.toBe("failed");
      expect(s.calls).toHaveLength(0);
    } finally { s.restore(); }
  });
});

describe("telegram event allowlist", () => {
  const STAGE_EVENTS = ["SEEKING", "FILTERED", "ELIGIBLE", "TAILORED"] as const;
  const OUTCOME_EVENTS = ["APPLICATION_ATTEMPT", "SUBMITTED", "DRY_RUN", "FAILED", "STAGE_FAILED"] as const;

  it("default: the stage events are muted before the ledger — no claim, no send", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      for (const ev of STAGE_EVENTS) {
        expect(await notifyEventOnce(m.db, ENV, "worker", `h-${ev}`, ev, ROW, "stage chatter")).toBe("skipped");
      }
      expect(s.calls).toHaveLength(0);
      // Nothing is sent, so nothing is claimed: emission is already
      // first-time-only, and a chatty board must not pay a D1 write per
      // posting for a message nobody receives.
      expect(m.writes).toHaveLength(0);
      expect(m.seen.size).toBe(0);
    } finally { s.restore(); }
  });

  it("default: the outcome events still reach Telegram", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      for (const ev of OUTCOME_EVENTS) {
        expect(await notifyEventOnce(m.db, ENV, "worker", `h-${ev}`, ev, ROW, "outcome")).toBe("sent");
      }
      expect(s.calls).toHaveLength(OUTCOME_EVENTS.length);
      for (const ev of OUTCOME_EVENTS) {
        expect(JSON.parse(s.calls[OUTCOME_EVENTS.indexOf(ev)][1].body).text).toContain(`[worker] ${ev}:`);
      }
    } finally { s.restore(); }
  });

  it("TELEGRAM_EVENTS narrows the set: an event left out is muted", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    const env = { ...ENV, TELEGRAM_EVENTS: "SUBMITTED" };
    try {
      expect(await notifyEventOnce(m.db, env, "worker", "h1", "SUBMITTED", ROW, "ok")).toBe("sent");
      expect(await notifyEventOnce(m.db, env, "worker", "h2", "FAILED", ROW, "boom")).toBe("skipped");
      expect(s.calls).toHaveLength(1);
      expect(JSON.parse(s.calls[0][1].body).text).toContain("[worker] SUBMITTED:");
    } finally { s.restore(); }
  });

  it("TELEGRAM_EVENTS widens the set: a stage event can be switched back on", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    const env = { ...ENV, TELEGRAM_EVENTS: "SEEKING,SUBMITTED" };
    try {
      expect(await notifyEventOnce(m.db, env, "worker", "h1", "SEEKING", ROW, "found")).toBe("sent");
      expect(await notifyEventOnce(m.db, env, "worker", "h2", "SUBMITTED", ROW, "ok")).toBe("sent");
      expect(await notifyEventOnce(m.db, env, "worker", "h3", "ELIGIBLE", ROW, "ok")).toBe("skipped");
      expect(s.calls).toHaveLength(2);
    } finally { s.restore(); }
  });

  it("an empty or absent TELEGRAM_EVENTS uses the default set", async () => {
    for (const env of [ENV, { ...ENV, TELEGRAM_EVENTS: "" }, { ...ENV, TELEGRAM_EVENTS: "  ,  " }]) {
      const s = stubFetch(async () => okResp());
      const m = memDb();
      try {
        expect(await notifyEventOnce(m.db, env, "worker", "h1", "SEEKING", ROW, "found")).toBe("skipped");
        expect(await notifyEventOnce(m.db, env, "worker", "h2", "SUBMITTED", ROW, "ok")).toBe("sent");
        expect(s.calls).toHaveLength(1);
      } finally { s.restore(); }
    }
  });

  it("a muted event writes no ledger row at all — a retry stays silent", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "found")).toBe("skipped");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "found")).toBe("skipped");
      expect(s.calls).toHaveLength(0);
      expect(m.writes).toHaveLength(0);
      expect(m.seen.size).toBe(0);
    } finally { s.restore(); }
  });

  it("parseTelegramEvents: comma separated, trimmed, empty means the default set", () => {
    expect([...parseTelegramEvents(undefined)].sort()).toEqual([...DEFAULT_TELEGRAM_EVENTS].sort());
    expect([...parseTelegramEvents("")].sort()).toEqual([...DEFAULT_TELEGRAM_EVENTS].sort());
    expect([...parseTelegramEvents("   ")].sort()).toEqual([...DEFAULT_TELEGRAM_EVENTS].sort());
    expect([...parseTelegramEvents("SEEKING, SUBMITTED")].sort()).toEqual(["SEEKING", "SUBMITTED"]);
    // Event names are matched case-insensitively.
    expect([...parseTelegramEvents("seeking")]).toEqual(["SEEKING"]);
  });
});

describe("formatJobAlert source and posted age", () => {
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

  it("renders the source and posted age in the alert line", () => {
    const t = formatJobAlert("worker", { ...ROW, source: "greenhouse:acme", posted_at: ago(3 * 3600e3) }, "SEEKING", "new posting");
    expect(t.split("\n")[0]).toBe("[worker] SEEKING: Backend Engineer @ Acme · greenhouse:acme · 3h ago");
    expect(t).toContain("https://x/1");
  });

  it("renders minutes inside the hour and days past a day", () => {
    expect(formatPostedAge(ago(20 * 60e3))).toBe("20m ago");
    expect(formatPostedAge(ago(3 * 3600e3))).toBe("3h ago");
    expect(formatPostedAge(ago(50 * 3600e3))).toBe("2d ago");
    // A board clock that runs ahead never renders a negative age.
    expect(formatPostedAge(new Date(Date.now() + 3600e3).toISOString())).toBe("0m ago");
  });

  it("says date unknown rather than guessing an age", () => {
    expect(formatPostedAge("")).toBe("date unknown");
    expect(formatPostedAge(undefined)).toBe("date unknown");
    expect(formatPostedAge("sometime last week")).toBe("date unknown");
    // A date that is there but unreadable stays visible as unreadable.
    expect(formatJobAlert("worker", { ...ROW, source: "rss:x", posted_at: "sometime last week" }, "ELIGIBLE", "ok"))
      .toContain("date unknown");
  });

  it("renders no age at all for a row the board never dated", () => {
    // Nothing to render is not the same as "unknown": the row carries no date
    // at all, so the alert says nothing about one.
    const t = formatJobAlert("worker", { ...ROW, source: "rss:x" }, "ELIGIBLE", "ok");
    expect(t).not.toContain("date unknown");
    expect(t.split("\n")[0]).toBe("[worker] ELIGIBLE: Backend Engineer @ Acme · rss:x");
  });

  it("never leaves a dangling separator when the row carries no source", () => {
    expect(formatJobAlert("worker", { title: "T", company: "C" }, "FILTERED", "why").split("\n")[0])
      .toBe("[worker] FILTERED: T @ C");
  });

  it("keeps the whole alert bounded with a source and an age", () => {
    const t = formatJobAlert(
      "worker",
      { title: "t".repeat(500), company: "c".repeat(500), source: "s".repeat(300), posted_at: ago(3600e3) },
      "FAILED",
      "d".repeat(5000),
    );
    expect(t.length).toBeLessThanOrEqual(3000);
  });
});

describe("formatJobAlert events", () => {
  it("happy: every worker event renders its own source-tagged line", () => {
    const events = [
      "SEEKING", "FILTERED", "ELIGIBLE", "TAILORED", "APPLICATION_ATTEMPT",
      "SUBMITTED", "DRY_RUN", "FAILED", "STAGE_FAILED",
    ] as const;
    for (const ev of events) {
      const t = formatJobAlert("worker", ROW, ev, "why");
      expect(t.startsWith(`[worker] ${ev}: Backend Engineer @ Acme`)).toBe(true);
    }
  });
});
