import { describe, expect, it } from "vitest";
import { PDFDocument } from "pdf-lib";
import { currentPointer, handleTelegramUpdate, sha256Hex } from "../src/lib/webhook.js";

const ENV = { TELEGRAM_BOT_TOKEN: "tok", TELEGRAM_CHAT_ID: "123", TELEGRAM_WEBHOOK_SECRET: "sec" };

function memDb(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  const writes: any[][] = [];
  return {
    store, writes,
    db: {
      prepare: (sql: string) => ({
        bind: (...a: any[]) => ({
          run: async () => {
            writes.push([sql, a]);
            if (sql.includes("INTO settings")) store.set(String(a[0]), String(a[1]));
            return {};
          },
          first: async () => {
            if (sql.includes("FROM settings")) {
              const v = store.get(String(a[0]));
              return v === undefined ? null : { value: v };
            }
            return null;
          },
        }),
        run: async () => ({}),
      }),
    } as any,
  };
}

function fakeBucket() {
  const puts: Array<{ key: string; bytes: Uint8Array }> = [];
  return {
    puts,
    b: { put: async (key: string, bytes: Uint8Array) => { puts.push({ key, bytes }); return {}; } },
  };
}

async function makePdf(text = "hello world"): Promise<Uint8Array> {
  const doc = await PDFDocument.create();
  const page = doc.addPage([300, 300]);
  page.drawText(text, { x: 20, y: 150, size: 12 });
  return new Uint8Array(await doc.save());
}

function stubFetch(opts: {
  sent: string[];
  fileBytes?: Uint8Array;
  noFilePath?: boolean;
  downloadStatus?: number;
  counter?: { n: number };
}) {
  const prev = globalThis.fetch;
  (globalThis as any).fetch = async (url: string, init: any) => {
    if (opts.counter) opts.counter.n += 1;
    const u = String(url);
    if (u.includes("/sendMessage")) {
      opts.sent.push(JSON.parse(init.body).text);
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    if (u.includes("/getFile")) {
      return {
        ok: true, status: 200,
        json: async () => (opts.noFilePath ? { ok: true, result: {} } : { ok: true, result: { file_path: "documents/f.pdf" } }),
      };
    }
    if (u.includes("/file/bot")) {
      return {
        ok: (opts.downloadStatus ?? 200) === 200,
        status: opts.downloadStatus ?? 200,
        arrayBuffer: async () => (opts.fileBytes ?? new Uint8Array()).buffer,
      };
    }
    return { ok: false, status: 404, json: async () => ({}), text: async () => "", arrayBuffer: async () => new ArrayBuffer(0) };
  };
  return () => { (globalThis as any).fetch = prev; };
}

const doc = (over: any = {}) => ({ file_id: "f1", mime_type: "application/pdf", file_name: "cv.pdf", file_size: 1000, ...over });
const update = (msg: any) => ({ message: { chat: { id: 123 }, ...msg } });

describe("handleTelegramUpdate", () => {
  it("ignores foreign chats with zero side effects", async () => {
    const sent: string[] = [];
    const counter = { n: 0 };
    const restore = stubFetch({ sent, counter });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate({ message: { chat: { id: 999 }, document: doc() } }, m.db, bk.b, ENV);
      expect(sent).toHaveLength(0);
      expect(counter.n).toBe(0);
      expect(bk.puts).toHaveLength(0);
      expect(m.store.size).toBe(0);
    } finally { restore(); }
  });

  it("stores a valid PDF, repoints settings, and replies with the swap", async () => {
    const bytes = await makePdf();
    const sha = await sha256Hex(bytes);
    const sent: string[] = [];
    const restore = stubFetch({ sent, fileBytes: bytes });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc() }), m.db, bk.b, ENV);
      expect(bk.puts).toHaveLength(1);
      expect(bk.puts[0].key).toBe(`cvs/master/${sha}.pdf`);
      expect(m.store.get("master_resume_sha")).toBe(sha);
      expect(sent).toHaveLength(1);
      expect(sent[0]).toMatch(/^✅ Master resume updated: [0-9a-f]{8} \(1 pages, \d+ bytes\)\. Previous: none\.$/);
    } finally { restore(); }
  });

  it("treats the same sha as already current (idempotent double delivery)", async () => {
    const bytes = await makePdf();
    const sha = await sha256Hex(bytes);
    const sent: string[] = [];
    const restore = stubFetch({ sent, fileBytes: bytes });
    const m = memDb({ master_resume_sha: sha });
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc() }), m.db, bk.b, ENV);
      await handleTelegramUpdate(update({ document: doc() }), m.db, bk.b, ENV);
      expect(bk.puts).toHaveLength(0);
      expect(sent).toHaveLength(2);
      expect(sent.every((t) => t.startsWith("Already current:"))).toBe(true);
      expect(m.store.get("master_resume_sha")).toBe(sha);
    } finally { restore(); }
  });

  it("rejects non-PDF documents without writes", async () => {
    const sent: string[] = [];
    const restore = stubFetch({ sent });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc({ mime_type: "application/zip", file_name: "cv.zip" }) }), m.db, bk.b, ENV);
      expect(sent[0]).toMatch(/^❌ Not a valid PDF:/);
      expect(m.store.size).toBe(0);
      expect(bk.puts).toHaveLength(0);
    } finally { restore(); }
  });

  it("rejects oversized uploads before any telegram call", async () => {
    const sent: string[] = [];
    const counter = { n: 0 };
    const restore = stubFetch({ sent, counter });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc({ file_size: 16 * 1024 * 1024 }) }), m.db, bk.b, ENV);
      expect(sent[0]).toMatch(/^❌ File too large \(16\.0 MB > 15 MB\)/);
      expect(counter.n).toBe(1); // only the reply sendMessage
      expect(m.store.size).toBe(0);
    } finally { restore(); }
  });

  it("rejects bytes without the %PDF header", async () => {
    const sent: string[] = [];
    const restore = stubFetch({ sent, fileBytes: new TextEncoder().encode("definitely not a pdf") });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc() }), m.db, bk.b, ENV);
      expect(sent[0]).toMatch(/^❌ Upload failed: missing %PDF header/);
      expect(m.store.size).toBe(0);
      expect(bk.puts).toHaveLength(0);
    } finally { restore(); }
  });

  it("replies upload failed when getFile returns no path", async () => {
    const sent: string[] = [];
    const restore = stubFetch({ sent, noFilePath: true });
    const m = memDb();
    const bk = fakeBucket();
    try {
      await handleTelegramUpdate(update({ document: doc() }), m.db, bk.b, ENV);
      expect(sent[0]).toMatch(/^❌ Upload failed:/);
      expect(bk.puts).toHaveLength(0);
    } finally { restore(); }
  });

  it("/master reports the D1 pointer, the secret fallback, and unset", async () => {
    const sent: string[] = [];
    const restore = stubFetch({ sent });
    const sha = "a".repeat(64);
    try {
      await handleTelegramUpdate(update({ text: "/master" }), memDb({ master_resume_sha: sha }).db, fakeBucket().b, ENV);
      expect(sent[0]).toBe(`Master resume sha: ${sha.slice(0, 8)} (source: d1).`);
      await handleTelegramUpdate(update({ text: "/master" }), memDb().db, fakeBucket().b, { ...ENV, MASTER_RESUME_SHA: "deadbeef" });
      expect(sent[1]).toBe("Master resume sha: deadbeef (source: secret).");
      await handleTelegramUpdate(update({ text: "/master" }), memDb().db, fakeBucket().b, ENV);
      expect(sent[2]).toBe("Master resume sha: unset.");
    } finally { restore(); }
  });

  it("currentPointer prefers d1 over the secret", async () => {
    const m = memDb({ master_resume_sha: "b".repeat(64) });
    const ptr = await currentPointer(m.db, { MASTER_RESUME_SHA: "deadbeef" });
    expect(ptr).toEqual({ sha: "b".repeat(64), source: "d1" });
    const none = await currentPointer(memDb().db, {});
    expect(none).toEqual({ sha: "", source: "none" });
  });

  it("sha256Hex matches the known digest of 'abc'", async () => {
    expect(await sha256Hex(new TextEncoder().encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});

describe("POST /telegram/webhook route", () => {
  it("rejects missing or wrong secret tokens with 401", async () => {
    const { default: worker } = await import("../src/index.js");
    const env = { DB: memDb().db, CV_BUCKET: fakeBucket().b } as any;
    for (const headers of [{}, { "X-Telegram-Bot-Api-Secret-Token": "nope" }] as Record<string, string>[]) {
      const res = await worker.fetch(
        new Request("https://worker/telegram/webhook", { method: "POST", headers, body: "{}" }), { ...env, TELEGRAM_WEBHOOK_SECRET: "sec" },
      );
      expect(res.status).toBe(401);
    }
  });

  it("accepts the secret header and acknowledges non-message updates", async () => {
    const { default: worker } = await import("../src/index.js");
    const res = await worker.fetch(
      new Request("https://worker/telegram/webhook", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "sec" },
        body: JSON.stringify({ update_id: 1 }),
      }),
      { DB: memDb().db, CV_BUCKET: fakeBucket().b, TELEGRAM_WEBHOOK_SECRET: "sec" } as any,
    );
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
  });
});
