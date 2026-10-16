import { describe, expect, it } from "vitest";
import { alert, formatJobAlert, notifyEventOnce } from "../src/lib/notify.js";

const ENV = { TELEGRAM_BOT_TOKEN: "tok", TELEGRAM_CHAT_ID: "123" };

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
      await expect(notifyEventOnce(m.db, {}, "worker", "h", "SEEKING", ROW, "x")).resolves.toBe("skipped");
      expect(s.calls).toHaveLength(0);
      expect(m.writes).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("happy: same (runtime, hash, event) sends once", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "found")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "found")).toBe("skipped");
      expect(s.calls).toHaveLength(1);
      expect(JSON.parse(s.calls[0][1].body).text).toContain("[worker] SEEKING:");
    } finally { s.restore(); }
  });

  it("happy: different events on the same job each send", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "found")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SUBMITTED", ROW, "ok")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "FAILED", ROW, "late failure")).toBe("sent");
      expect(s.calls).toHaveLength(3);
    } finally { s.restore(); }
  });

  it("happy: same hash+event under another runtime sends again", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb();
    try {
      expect(await notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "x")).toBe("sent");
      expect(await notifyEventOnce(m.db, ENV, "local", "h", "SEEKING", ROW, "x")).toBe("sent");
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
    const m = memDb(["worker:h:SEEKING"]);
    try {
      await expect(notifyEventOnce(m.db, ENV, "worker", "h", "SEEKING", ROW, "x")).resolves.toBe("skipped");
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
