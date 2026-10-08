import { describe, expect, it } from "vitest";
import { alert, formatJobAlert, notifyJobOnce } from "../src/lib/notify.js";

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

function memDb(notified: number | null) {
  const writes: any[][] = [];
  return {
    writes,
    db: {
      prepare: (sql: string) => ({
        bind: (...a: any[]) => ({
          first: async () => (sql.includes("notified") && !sql.startsWith("UPDATE") ? { notified } : null),
          run: async () => { writes.push([sql, a]); return {}; },
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
  it("happy: submitted variant", () => {
    const t = formatJobAlert(ROW, "SUBMITTED", "ok");
    expect(t).toContain("Backend Engineer");
    expect(t).toContain("Acme");
    expect(t).toContain("95");
    expect(t).toContain("https://x/1");
  });

  it("happy: review variant carries reason", () => {
    expect(formatJobAlert(ROW, "APPLICATION_STARTED", "queued for human")).toContain("queued for human");
  });

  it("happy: failure variant carries detail", () => {
    expect(formatJobAlert(ROW, "FAILED", "resume bytes missing")).toContain("resume bytes missing");
  });

  it("edge: missing fields fall back, never crash", () => {
    const t = formatJobAlert({}, "APPLICATION_STARTED", "");
    expect(t).toContain("Untitled role");
    expect(t).toContain("Unknown company");
  });

  it("edge: long title/company/detail truncated", () => {
    const t = formatJobAlert({ title: "t".repeat(500), company: "c".repeat(500), url: "u" }, "FAILED", "d".repeat(500));
    expect(t.length).toBeLessThan(120 + 80 + 200 + 60);
  });
});

describe("notifyJobOnce", () => {
  it("happy: sends once and marks notified", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb(0);
    try {
      expect(await notifyJobOnce(m.db, ENV, "h", ROW, "APPLICATION_STARTED", "queued")).toBe("sent");
      expect(s.calls).toHaveLength(1);
      expect(JSON.parse(s.calls[0][1].body).text).toContain("Backend Engineer");
      expect(m.writes.some((w) => String(w[0]).includes("notified"))).toBe(true);
    } finally { s.restore(); }
  });

  it("edge: already notified never sends", async () => {
    const s = stubFetch(async () => okResp());
    const m = memDb(1);
    try {
      expect(await notifyJobOnce(m.db, ENV, "h", ROW, "APPLICATION_STARTED", "q")).toBe("skipped");
      expect(s.calls).toHaveLength(0);
    } finally { s.restore(); }
  });

  it("bad: telegram down still marks notified (no retry storm)", async () => {
    const s = stubFetch(async () => { throw new Error("down"); });
    const m = memDb(0);
    try {
      expect(await notifyJobOnce(m.db, ENV, "h", ROW, "FAILED", "x")).toBe("failed");
      expect(m.writes.some((w) => String(w[0]).includes("notified"))).toBe(true);
    } finally { s.restore(); }
  });

  it("bad: db read error fails open to alerting, never throws", async () => {
    const s = stubFetch(async () => okResp());
    const db = { prepare: () => ({ bind: () => ({ first: async () => { throw new Error("d1"); }, run: async () => ({}) }) }) };
    try {
      await expect(notifyJobOnce(db, ENV, "h", ROW, "SUBMITTED", "")).resolves.toBe("sent");
      expect(s.calls).toHaveLength(1);
    } finally { s.restore(); }
  });

  it("bad: db write error never propagates", async () => {
    const s = stubFetch(async () => okResp());
    const db = {
      prepare: (sql: string) => ({
        bind: () => ({
          first: async () => ({ notified: 0 }),
          run: async () => { if (sql.startsWith("UPDATE")) throw new Error("d1"); return {}; },
        }),
      }),
    };
    try {
      await expect(notifyJobOnce(db, ENV, "h", ROW, "SUBMITTED", "")).resolves.toBe("sent");
    } finally { s.restore(); }
  });
});
