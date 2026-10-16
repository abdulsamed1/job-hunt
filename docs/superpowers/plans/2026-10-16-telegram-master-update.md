# Telegram master-resume update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner sends a PDF to the Telegram bot anytime; the Worker validates it, stores it content-addressed in R2, and repoints `settings.master_resume_sha` so every future application uses it. This machine can pull the same master.

**Architecture:** One new Worker route `POST /telegram/webhook` (secret-header auth + chat allowlist) processes document uploads inline and idempotently (same sha = zero writes). The resume pointer lives in a new D1 `settings` table first, with `MASTER_RESUME_SHA` as bootstrap/fallback. R2 keys stay content-addressed and immutable. `scripts/pull_master.py` syncs the local file from the pointer.

**Tech Stack:** Cloudflare Workers (fetch handler, D1, R2, Queues), `pdf-lib` (already a dependency), vitest, Python 3 stdlib (`subprocess`, `hashlib`, `json`) + pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-telegram-master-update-design.md`

## Global Constraints

- No new npm/Python dependencies; `pdf-lib` is already in `worker/package.json`.
- Worker rules: ≤50 subrequests/invocation — the webhook uses at most 5 (getFile, file download, R2 put, 1–2 D1 statements, sendMessage).
- Never log tokens, document bytes, or message bodies; log at most `file_id`, sha, byte counts.
- R2 master objects are content-addressed (`cvs/master/<sha256>.pdf`) and never deleted.
- Pointer precedence everywhere: `settings.master_resume_sha` → `MASTER_RESUME_SHA` → unset.
- Reply strings must start with the exact prefixes in the spec (`✅ Master resume updated:`, `Already current:`, `Master resume sha: `, `❌ Not a valid PDF:`, `❌ File too large (`, `❌ Upload failed:`).
- Upload cap: `15 * 1024 * 1024` bytes (`MAX_MASTER_BYTES`).
- Suites must be green before each commit: `uv run pytest tests/ -q -p no:cacheprovider`, `npm test` (in `worker/`), `npx tsc --noEmit` (in `worker/`).
- Commit style: conventional (`feat:`, `test:`, `docs:`, `chore:`).

---

### Task 1: D1 settings table + state helpers

**Files:**
- Modify: `worker/src/state.ts` (append after `ensureNotificationEvents`, ~line 110)
- Modify: `worker/schema.sql` (append after `notification_events`, ~line 100)
- Test: `worker/test/state.test.ts` (append)

**Interfaces:**
- Consumes: existing `Db` type from `worker/src/state.ts`.
- Produces: `ensureSettings(db)`, `getSetting(db, key): Promise<string | null>`, `setSetting(db, key, value)` — Tasks 2–3 import these.

- [ ] **Step 1: Write the failing tests**

Append to `worker/test/state.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run test/state.test.ts` (in `worker/`)
Expected: FAIL — `getSetting`/`setSetting`/`ensureSettings` are not exported.

- [ ] **Step 3: Implement the helpers**

Append to `worker/src/state.ts`:

```ts
/** Create the settings table on databases that predate it (fresh databases
 * already have it — see schema.sql). Safe to re-run. */
export async function ensureSettings(db: Db): Promise<void> {
  try {
    await db.prepare(
      `CREATE TABLE IF NOT EXISTS settings (
         key TEXT PRIMARY KEY,
         value TEXT NOT NULL,
         updated_at TEXT NOT NULL
       )`,
    ).run();
  } catch { /* raced with another invocation (or table exists) */ }
}

/** Read one settings value. Missing key or missing table resolves null so
 * callers can fall back to env-based defaults. */
export async function getSetting(db: Db, key: string): Promise<string | null> {
  try {
    const row: any = await db.prepare(`SELECT value FROM settings WHERE key = ? LIMIT 1`).bind(key).first();
    const v = row?.value;
    return typeof v === "string" && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

/** Upsert one settings value. */
export async function setSetting(db: Db, key: string, value: string): Promise<void> {
  await db.prepare(
    `INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
  ).bind(key, value, new Date().toISOString()).run();
}
```

Append to `worker/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run test/state.test.ts` (in `worker/`)
Expected: PASS (all three new tests).

- [ ] **Step 5: Commit**

```bash
git add worker/src/state.ts worker/schema.sql worker/test/state.test.ts
git commit -m "feat(worker): D1 settings store for the master-resume pointer"
```

---

### Task 2: Webhook module (validation + swap logic)

**Files:**
- Create: `worker/src/lib/webhook.ts`
- Test: `worker/test/webhook.test.ts`

**Interfaces:**
- Consumes: `ensureSettings`, `getSetting`, `setSetting`, `Db` (Task 1); `alert`, `AlertEnv` from `worker/src/lib/notify.ts`; `PDFDocument` from `pdf-lib`.
- Produces (Task 3 imports these):
  - `MAX_MASTER_BYTES: number` (15 MB)
  - `WebhookEnv extends AlertEnv { MASTER_RESUME_SHA?: string }`
  - `BucketLike { put(key: string, value: Uint8Array): Promise<unknown> }`
  - `sha256Hex(bytes: Uint8Array): Promise<string>`
  - `currentPointer(db, env): Promise<{ sha: string; source: "d1" | "secret" | "none" }>`
  - `handleTelegramUpdate(update, db, bucket, env): Promise<void>`

- [ ] **Step 1: Write the failing tests**

Create `worker/test/webhook.test.ts`:

```ts
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run test/webhook.test.ts` (in `worker/`)
Expected: FAIL — `../src/lib/webhook.js` does not exist.

- [ ] **Step 3: Implement the module**

Create `worker/src/lib/webhook.ts`:

```ts
// Inbound Telegram webhook: the only inbound surface. The owner sends a PDF
// document (or /master) and the Worker validates it, stores it
// content-addressed in R2, and repoints settings.master_resume_sha.
// Processing is inline and idempotent (same sha == zero writes), so Telegram
// retries are safe. Tokens, document bytes, and message bodies are never
// logged; only file ids, shas, and byte counts.

import { PDFDocument } from "pdf-lib";
import { alert, type AlertEnv } from "./notify.js";
import { ensureSettings, getSetting, setSetting, type Db } from "../state.js";

export const MAX_MASTER_BYTES = 15 * 1024 * 1024;

export interface WebhookEnv extends AlertEnv {
  MASTER_RESUME_SHA?: string;
}

export interface BucketLike {
  put(key: string, value: Uint8Array): Promise<unknown>;
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Pointer precedence: D1 settings first, MASTER_RESUME_SHA secret as the
 * bootstrap/fallback. /master reports the source so drift is visible. */
export async function currentPointer(
  db: Db, env: { MASTER_RESUME_SHA?: string },
): Promise<{ sha: string; source: "d1" | "secret" | "none" }> {
  const fromDb = await getSetting(db, "master_resume_sha");
  if (fromDb) return { sha: fromDb.trim(), source: "d1" };
  const fromEnv = (env.MASTER_RESUME_SHA || "").trim();
  if (fromEnv) return { sha: fromEnv, source: "secret" };
  return { sha: "", source: "none" };
}

function hasPdfMagic(bytes: Uint8Array): boolean {
  return bytes.length >= 4 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46;
}

/** Handle one Telegram update. All replies go through alert() (allowlisted
 * chat only); foreign chats and non-message updates are ignored silently. */
export async function handleTelegramUpdate(
  update: any,
  db: Db,
  bucket: BucketLike,
  env: WebhookEnv,
): Promise<void> {
  const msg = update?.message;
  if (!msg || !env.TELEGRAM_CHAT_ID || String(msg.chat?.id ?? "") !== String(env.TELEGRAM_CHAT_ID)) return;
  if (!env.TELEGRAM_BOT_TOKEN) return;
  await ensureSettings(db);
  const text = typeof msg.text === "string" ? msg.text.trim() : "";
  if (text === "/master") {
    const cur = await currentPointer(db, env);
    await alert(env, cur.sha ? `Master resume sha: ${cur.sha.slice(0, 8)} (source: ${cur.source}).` : "Master resume sha: unset.");
    return;
  }
  const doc = msg.document;
  if (!doc || !doc.file_id) return;
  const fail = async (reason: string): Promise<void> => {
    await alert(env, `❌ Upload failed: ${reason}. Master unchanged.`);
  };
  const mime = String(doc.mime_type || "");
  const fname = String(doc.file_name || "").toLowerCase();
  if (mime !== "application/pdf" && !fname.endsWith(".pdf")) {
    await alert(env, "❌ Not a valid PDF: file type. Master unchanged.");
    return;
  }
  const declared = Number(doc.file_size || 0);
  if (declared > MAX_MASTER_BYTES) {
    await alert(env, `❌ File too large (${(declared / 1024 / 1024).toFixed(1)} MB > 15 MB). Master unchanged.`);
    return;
  }
  let bytes: Uint8Array;
  try {
    const fileRes = await fetch(
      `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getFile?file_id=${encodeURIComponent(doc.file_id)}`,
    );
    const fileJson: any = await fileRes.json();
    const filePath = fileJson?.result?.file_path;
    if (!filePath) { await fail("telegram getFile rejected"); return; }
    const dl = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${filePath}`);
    if (!dl.ok) { await fail(`download HTTP ${dl.status}`); return; }
    bytes = new Uint8Array(await dl.arrayBuffer());
  } catch {
    await fail("telegram download error");
    return;
  }
  if (bytes.byteLength > MAX_MASTER_BYTES) {
    await alert(env, `❌ File too large (${(bytes.byteLength / 1024 / 1024).toFixed(1)} MB > 15 MB). Master unchanged.`);
    return;
  }
  if (!hasPdfMagic(bytes)) { await fail("missing %PDF header"); return; }
  let pages = 0;
  try {
    pages = (await PDFDocument.load(bytes)).getPageCount();
  } catch {
    await fail("could not parse PDF");
    return;
  }
  const sha = await sha256Hex(bytes);
  const cur = await currentPointer(db, env);
  if (cur.sha && sha === cur.sha) {
    await alert(env, `Already current: ${sha.slice(0, 8)} (${pages} pages).`);
    return;
  }
  try {
    await bucket.put(`cvs/master/${sha}.pdf`, bytes);
    await setSetting(db, "master_resume_sha", sha);
  } catch {
    await fail("storage error");
    return;
  }
  await alert(
    env,
    `✅ Master resume updated: ${sha.slice(0, 8)} (${pages} pages, ${bytes.byteLength} bytes). Previous: ${cur.sha ? cur.sha.slice(0, 8) : "none"}.`,
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run test/webhook.test.ts` (in `worker/`)
Expected: PASS (all 10 tests).

- [ ] **Step 5: Commit**

```bash
git add worker/src/lib/webhook.ts worker/test/webhook.test.ts
git commit -m "feat(worker): telegram webhook module for master-resume uploads"
```

---

### Task 3: Route wiring + apply-path pointer + /health

**Files:**
- Modify: `worker/src/index.ts` (Env ~line 31-51, imports ~29, queue ensure block ~208, apply path ~322, `/health` ~409-430, new route before ~515)
- Test: `worker/test/webhook.test.ts` (append route tests), `worker/test/health.test.ts` (append), `worker/test/stages.test.ts` (memQueueDb + one test)

**Interfaces:**
- Consumes: Task 2 exports.
- Produces: `POST /telegram/webhook` behavior; `/health.master_resume_sha`; apply path resolves pointer.

- [ ] **Step 1: Write the failing tests**

Append to `worker/test/webhook.test.ts`:

```ts
describe("POST /telegram/webhook route", () => {
  it("rejects missing or wrong secret tokens with 401", async () => {
    const { default: worker } = await import("../src/index.js");
    const env = { DB: memDb().db, CV_BUCKET: fakeBucket().b } as any;
    for (const headers of [{}, { "X-Telegram-Bot-Api-Secret-Token": "nope" }]) {
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
```

Append to `worker/test/health.test.ts`:

```ts
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
```

In `worker/test/stages.test.ts`, extend `memQueueDb` opts and `first`/`run` (exact edits):

```ts
  function memQueueDb(opts: { jobRow?: any; upsertChanges?: number; failJobsSelect?: boolean; settings?: Record<string, string> } = {}) {
    const statements: Array<{ sql: string; params: any[] }> = [];
    const seenEvents = new Set<string>();
    const settings = new Map(Object.entries(opts.settings ?? {}));
```

In the `bind` `first` body, before the `FROM jobs` check:

```ts
            if (sql.includes("FROM settings")) {
              const v = settings.get(String(params[0]));
              return v === undefined ? null : { value: v };
            }
```

In the `bind` `run` body, before `return {};`:

```ts
            if (sql.includes("INTO settings")) { settings.set(String(params[0]), String(params[1])); return {}; }
```

Append this test inside `describe("queue lifecycle events")`:

```ts
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run test/webhook.test.ts test/health.test.ts test/stages.test.ts` (in `worker/`)
Expected: FAIL — route 404, `master_resume_sha` undefined, apply path reads `deadbeef`.

- [ ] **Step 3: Implement the wiring**

In `worker/src/index.ts`:

1. Add to the imports (line ~29):

```ts
import { currentPointer, handleTelegramUpdate } from "./lib/webhook.js";
```

2. Extend the import from `./state.js` with `ensureSettings`:

```ts
import { ensureNotificationEvents, ensureSettings, ensureSourceHealthColumns, getActionableJobs, getJobsByState, recordSourceHealth, saveApplication, saveEvaluation, setJobState, upsertJob, type Db, type JobState } from "./state.js";
```

3. Add to `Env` (after `TELEGRAM_CHAT_ID`):

```ts
  TELEGRAM_WEBHOOK_SECRET?: string;
```

4. In `queue()`, after `await ensureNotificationEvents(db);` (line ~209):

```ts
    await ensureSettings(db);
```

5. Replace the apply-path sha resolution (line ~322-328) with:

```ts
          const ptr = await currentPointer(db, env);
          const sha = ptr.sha;
          const failDetailSha = "resume bytes missing: master resume unset (settings.master_resume_sha / MASTER_RESUME_SHA)";
          if (!sha) {
            await saveApplication(db, m.hash, "FAILED", failDetailSha, null);
            if (await notifyEventOnce(db, env, "worker", m.hash, "FAILED", row, scored(failDetailSha)) !== "sent") console.log(`telegram notify failed for ${m.hash} (FAILED)`);
            continue;
          }
```

6. In `/health` (before `return Response.json({`), add:

```ts
      const ptr = await currentPointer(db, env);
```

and inside the returned object (after `notification_events: notificationEvents,`):

```ts
        master_resume_sha: ptr.sha || null,
```

7. Add the route before the final fallback `return new Response(...)` (line ~515):

```ts
    if (url.pathname === "/telegram/webhook" && request.method === "POST") {
      // Restricted inbound surface: owner-only document uploads and /master.
      // Telegram authenticates with the secret-token header configured at
      // setWebhook time; anything else is rejected before any side effect.
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      const update: any = await request.json().catch(() => ({}));
      try {
        await handleTelegramUpdate(update, db, env.CV_BUCKET as any, env);
      } catch (e) {
        console.log(`telegram webhook failed: ${String(e).slice(0, 200)}`);
      }
      return Response.json({ ok: true });
    }
```

Also update the fallback route list string to include `/telegram/webhook`:

```ts
    return new Response("jobhunt worker: /health /recent /sources /coverage /queue /run /probe /telegram/webhook", { status: 200 });
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run` (in `worker/`)
Expected: PASS — all worker tests, including the pre-existing queue tests (`MASTER_RESUME_SHA: "deadbeef"` now falls through the settings lookup to the secret).

- [ ] **Step 5: Typecheck and commit**

Run: `npx tsc --noEmit` (in `worker/`)
Expected: no errors.

```bash
git add worker/src/index.ts worker/test/webhook.test.ts worker/test/health.test.ts worker/test/stages.test.ts
git commit -m "feat(worker): /telegram/webhook route, pointer-aware apply path, /health field"
```

---

### Task 4: verify_deploy pointer check

**Files:**
- Modify: `scripts/verify_deploy.py` (`check_health` ~line 88, health print ~line 138)
- Test: `tests/test_verify_deploy.py`

**Interfaces:**
- Consumes: `/health.master_resume_sha` (Task 3).
- Produces: `check_health` fails on missing/malformed pointer; header print includes `master=<sha8>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_deploy.py` (and update the existing `check_health` test baselines to include a valid pointer — search for `check_health` in that file):

```python
VALID_POINTER = "a" * 64


def _health(**over):
    base = {
        "ok": True,
        "live_apply": False,
        "notification_events": 0,
        "master_resume_sha": VALID_POINTER,
    }
    base.update(over)
    return base


def test_check_health_accepts_valid_pointer():
    mod = _load()
    assert mod.check_health(_health()) == []


def test_check_health_flags_missing_pointer():
    mod = _load()
    failures = mod.check_health(_health(master_resume_sha=None))
    assert any("master_resume_sha" in f for f in failures)


def test_check_health_flags_malformed_pointer():
    mod = _load()
    failures = mod.check_health(_health(master_resume_sha="deadbeef"))
    assert any("master_resume_sha" in f for f in failures)
```

Existing `check_health` tests that build a health dict without `master_resume_sha` must be updated to include `"master_resume_sha": VALID_POINTER` (otherwise they now fail on the new rule — that is expected and correct).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_verify_deploy.py -q -p no:cacheprovider`
Expected: FAIL — missing/malformed pointer is not flagged.

- [ ] **Step 3: Implement**

In `scripts/verify_deploy.py`, add `import re` at the top if absent, then extend `check_health` before `return failures`:

```python
    sha = health.get("master_resume_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        failures.append(
            "master_resume_sha missing/malformed in /health — the resume pointer is "
            "unset or the Worker predates the settings migration"
        )
```

Update the health print line to include the pointer prefix:

```python
        print(
            f"health: jobs={health.get('jobs')} skills={health.get('profile_skills')} "
            f"live_apply={health.get('live_apply')} notification_events={health.get('notification_events')} "
            f"master={str(health.get('master_resume_sha'))[:8]}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_verify_deploy.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_deploy.py tests/test_verify_deploy.py
git commit -m "feat(verify): require a well-formed master_resume_sha in /health"
```

---

### Task 5: Local master sync script

**Files:**
- Create: `scripts/pull_master.py`
- Test: `tests/test_pull_master.py`

**Interfaces:**
- Consumes: D1 `settings.master_resume_sha` (Task 1) and R2 `cvs/master/<sha>.pdf` (already in prod).
- Produces: `parse_pointer_output(stdout)`, `file_sha256(path)`, `fetch_pointer(worker_dir)`, `download_master(worker_dir, sha, target)`, `main()`; CLI `--check`, `--master-path`, `--worker-dir`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pull_master.py`:

```python
"""Unit tests for scripts/pull_master.py (no network, wrangler is mocked)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pull_master.py"
    spec = importlib.util.spec_from_file_location("pull_master", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_pointer_output_reads_value():
    mod = _load()
    out = json.dumps([{"results": [{"value": "abc123"}], "success": True}])
    assert mod.parse_pointer_output(out) == "abc123"


def test_parse_pointer_output_empty_and_garbage():
    mod = _load()
    assert mod.parse_pointer_output("") == ""
    assert mod.parse_pointer_output("not json") == ""
    assert mod.parse_pointer_output(json.dumps([{"results": [], "success": True}])) == ""
    assert mod.parse_pointer_output(json.dumps({"results": [{"value": "x"}]})) == ""


def test_file_sha256_missing_and_content(tmp_path):
    mod = _load()
    assert mod.file_sha256(tmp_path / "nope.pdf") == ""
    p = tmp_path / "a.bin"
    p.write_bytes(b"abc")
    assert mod.file_sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_main_check_mode_reports_drift_without_writing(tmp_path, monkeypatch, capsys):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    target.write_bytes(b"old")
    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: "b" * 64)
    monkeypatch.setattr(mod, "download_master", lambda *a, **k: pytest.fail("must not download in --check"))
    rc = mod.main(["--check", "--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 1
    assert target.read_bytes() == b"old"
    assert "DRIFT" in capsys.readouterr().out


def test_main_downloads_on_drift(tmp_path, monkeypatch):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    calls = []

    def fake_download(worker_dir, sha, tgt):
        calls.append(sha)
        Path(tgt).write_bytes(b"new")

    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: "c" * 64)
    monkeypatch.setattr(mod, "download_master", fake_download)
    rc = mod.main(["--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 0
    assert calls == ["c" * 64]
    assert target.read_bytes() == b"new"


def test_main_up_to_date_is_noop(tmp_path, monkeypatch, capsys):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    import hashlib
    target.write_bytes(b"same")
    sha = hashlib.sha256(b"same").hexdigest()
    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: sha)
    monkeypatch.setattr(mod, "download_master", lambda *a, **k: pytest.fail("up-to-date must not download"))
    rc = mod.main(["--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_main_errors_when_pointer_missing(tmp_path, monkeypatch, capsys):
    mod = _load()

    def boom(worker_dir):
        raise RuntimeError("no master_resume_sha in D1 settings (upload a PDF via Telegram first)")

    monkeypatch.setattr(mod, "fetch_pointer", boom)
    rc = mod.main(["--master-path", str(tmp_path / "x.pdf"), "--worker-dir", str(tmp_path)])
    assert rc == 2
    assert "no master_resume_sha" in capsys.readouterr().out


def test_download_master_verifies_sha(tmp_path, monkeypatch):
    mod = _load()

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd):
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"wrong")
        return R()

    monkeypatch.setattr(mod, "_run", fake_run)
    with pytest.raises(RuntimeError):
        mod.download_master(tmp_path, "d" * 64, tmp_path / "out.pdf")
```

Note: `main()` must accept an optional `argv: list[str] | None = None` so tests can call it directly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pull_master.py -q -p no:cacheprovider`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `scripts/pull_master.py`:

```python
"""Pull the current master resume (R2 pointer via D1) onto this machine.

The Worker is the single source of truth: settings.master_resume_sha in D1
and cvs/master/<sha>.pdf in R2. This script reads the pointer with the
authenticated wrangler CLI, downloads the object, verifies the sha, and
atomically replaces data/cvs/Abdulsamed_Hamdy.pdf.

Usage:
  uv run python scripts/pull_master.py            # sync local file
  uv run python scripts/pull_master.py --check    # exit 1 on drift, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER_DIR = REPO_ROOT / "worker"
DEFAULT_MASTER = REPO_ROOT / "data" / "cvs" / "Abdulsamed_Hamdy.pdf"
BUCKET = "jobhunt-cvs"


def parse_pointer_output(stdout: str) -> str:
    """Extract settings.master_resume_sha from `wrangler d1 execute --json` output."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    for entry in payload:
        for row in entry.get("results") or []:
            value = row.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def file_sha256(path: Path) -> str:
    if not Path(path).is_file():
        return ""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def fetch_pointer(worker_dir: Path) -> str:
    res = _run(
        ["npx", "wrangler", "d1", "execute", "jobhunt", "--remote", "--json",
         "--command", "SELECT value FROM settings WHERE key='master_resume_sha'"],
        worker_dir,
    )
    if res.returncode != 0:
        raise RuntimeError(f"wrangler d1 failed: {(res.stderr or res.stdout).strip()[:200]}")
    sha = parse_pointer_output(res.stdout)
    if not sha:
        raise RuntimeError("no master_resume_sha in D1 settings (upload a PDF via Telegram first)")
    return sha


def download_master(worker_dir: Path, sha: str, target: Path) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        res = _run(
            ["npx", "wrangler", "r2", "object", "get", f"{BUCKET}/cvs/master/{sha}.pdf",
             "--remote", "--file", str(tmp_path)],
            worker_dir,
        )
        if res.returncode != 0 or file_sha256(tmp_path) != sha:
            raise RuntimeError(f"download or sha verify failed for cvs/master/{sha}.pdf")
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pull the master resume from R2/D1.")
    ap.add_argument("--check", action="store_true", help="report drift only; exit 1, write nothing")
    ap.add_argument("--master-path", default=str(DEFAULT_MASTER))
    ap.add_argument("--worker-dir", default=str(DEFAULT_WORKER_DIR))
    args = ap.parse_args(argv)
    target = Path(args.master_path)
    worker_dir = Path(args.worker_dir)
    local = file_sha256(target)
    try:
        remote = fetch_pointer(worker_dir)
    except RuntimeError as e:
        print(f"error: {e}")
        return 2
    if local == remote:
        print(f"up to date: {remote[:12]}...")
        return 0
    if args.check:
        print(f"DRIFT: local={local[:12] or 'missing'}... remote={remote[:12]}...")
        return 1
    try:
        download_master(worker_dir, remote, target)
    except RuntimeError as e:
        print(f"error: {e}")
        return 2
    print(f"updated {target} -> {remote[:12]}... ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pull_master.py -q -p no:cacheprovider`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_master.py tests/test_pull_master.py
git commit -m "feat(scripts): pull_master syncs the local resume from the R2/D1 pointer"
```

---

### Task 6: Docs (no longer outbound-only)

**Files:**
- Modify: `README.md` (Telegram section, scripts/commands list)
- Modify: `worker/README.md` (routes table, settings table, gotchas)
- Modify: `AGENTS.md` (Notifications section bullet)
- Modify: `worker/src/lib/notify.ts` (header comment only)

**Interfaces:**
- Consumes: everything above; no code changes beyond the comment.

- [ ] **Step 1: Update `worker/src/lib/notify.ts` header**

Replace the header comment's second sentence (`Outbound only — the bot takes no inbound commands.`) with:

```ts
// Outbound lifecycle events ... Inbound is limited to the owner's chat:
// PDF master-resume uploads and the /master status command (see lib/webhook.ts).
```

Keep the rest of the header intact.

- [ ] **Step 2: Update `README.md`**

In the Telegram section, replace any "outbound only / takes no inbound" wording with a short subsection:

```markdown
### Inbound: updating the master resume from Telegram

Send a PDF to the bot from the owner's chat and it becomes the master resume:
the Worker validates it (`%PDF-` header, `pdf-lib` parse, ≤15 MB), stores it
as `cvs/master/<sha256>.pdf` in R2, and repoints `settings.master_resume_sha`
in D1. All future applications use it; old versions stay in R2. Text `/master`
reports the current sha and its source (`d1` or the `MASTER_RESUME_SHA`
secret fallback).

Everything else inbound is ignored; the webhook rejects requests without the
Telegram secret-token header.

Local sync (this machine is NOT the source of truth):

```bash
uv run python scripts/pull_master.py         # replace data/cvs/Abdulsamed_Hamdy.pdf with the current master
uv run python scripts/pull_master.py --check # exit 1 when the local file drifts
```
```

Also add `scripts/pull_master.py` next to the other script commands in the commands section.

- [ ] **Step 3: Update `worker/README.md`**

- Add to the routes/endpoints table: `POST /telegram/webhook` — owner-only Telegram inbound (secret header `X-Telegram-Bot-Api-Secret-Token`; PDF upload updates `settings.master_resume_sha`; `/master` reports it; everything else ignored).
- Add the `settings` table to the schema notes (key/value/updated_at; `master_resume_sha` pointer; runtime `ensureSettings`).
- Add a gotcha bullet: "Webhook processing is inline and idempotent (same sha = zero writes); Telegram retries are safe. Modest subrequests; never log bodies."
- Note pointer precedence: `settings.master_resume_sha` → `MASTER_RESUME_SHA` secret.
- Setup note: secrets `TELEGRAM_WEBHOOK_SECRET` + fresh `TELEGRAM_BOT_TOKEN`, and the one-time `setWebhook` call with `secret_token` + `drop_pending_updates`.

- [ ] **Step 4: Update `AGENTS.md`**

In the Notifications bullet, replace "Bot takes no inbound commands / outbound only" phrasing (search for `outbound`) with:

```markdown
Telegram inbound is owner-restricted: PDF uploads via `POST /telegram/webhook`
(secret token header) update the master resume pointer
(`settings.master_resume_sha` in D1; `MASTER_RESUME_SHA` secret is the
bootstrap fallback) and `/master` reports it. Everything else is ignored. Keep
the local `data/cvs/Abdulsamed_Hamdy.pdf` in sync with
`uv run python scripts/pull_master.py --check` before local runs.
```

- [ ] **Step 5: Run docs consistency check and commit**

Run: `grep -rn "outbound only\|takes no inbound" README.md worker/README.md AGENTS.md worker/src/lib/notify.ts`
Expected: no stale matches (or only the ones you intentionally rewrote).

```bash
git add README.md worker/README.md AGENTS.md worker/src/lib/notify.ts
git commit -m "docs: telegram inbound master-resume upload + local sync"
```

---

### Task 7: Deploy + webhook registration + e2e (ops; needs the owner)

**Files:** none (runtime state only).

- [ ] **Step 1: Full local verification (before deploy)**

```bash
uv run pytest tests/ -q -p no:cacheprovider     # repo root
npx vitest run && npx tsc --noEmit              # worker/
python worker/scripts/gen_sources.py --check
```

Expected: all green.

- [ ] **Step 2: Owner action — rotate the bot token**

Owner revokes the leaked token in BotFather and reissues; deliver the fresh token to the operator (or run `printf '%s' '<TOKEN>' | npx wrangler secret put TELEGRAM_BOT_TOKEN` themselves in `worker/`).

- [ ] **Step 3: Set secrets and deploy**

```bash
printf '%s' '<FRESH_TOKEN>' | npx wrangler secret put TELEGRAM_BOT_TOKEN
openssl rand -hex 32 | npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
npx wrangler deploy
```

- [ ] **Step 4: Register the webhook** (copy the same webhook secret used above)

```bash
curl -s "https://api.telegram.org/bot<FRESH_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://jobhunt.habdulsamed777.workers.dev/telegram/webhook","secret_token":"<WEBHOOK_SECRET>","allowed_updates":["message"],"drop_pending_updates":true}'
curl -s "https://api.telegram.org/bot<FRESH_TOKEN>/getWebhookInfo"
```

Expected: `setWebhook` returns `{"ok":true,...}`; `getWebhookInfo` shows the URL, no last error, `pending_update_count: 0`.

- [ ] **Step 5: Verify deploy**

```bash
python scripts/verify_deploy.py --new-only
```

Expected: PASS; health line now shows `master=<8 hex>` and no pointer failure.

- [ ] **Step 6: Live e2e**

1. Send `/master` in the owner chat → expect `Master resume sha: 19c8b110 (source: secret).` (until the first D1 write).
2. Send the current PDF file → expect `Already current: …` (same bytes as the deployed master) with zero D1/R2 writes.
3. Optional swap test with any newer PDF → expect `✅ Master resume updated: …`; then `/master` shows `(source: d1)`; then locally `uv run python scripts/pull_master.py` syncs the file (and `--check` exits 0).
4. Confirm no other chat can reach the bot logic: send from a different account → no reply.

- [ ] **Step 7: Rollback drill (documented, not executed unless needed)**

```bash
curl -s "https://api.telegram.org/bot<FRESH_TOKEN>/deleteWebhook"
# then in D1: DELETE FROM settings WHERE key='master_resume_sha';  (reverts to the secret's sha)
```

---

## Self-Review

**Spec coverage:** route+secret (T3) · allowlist (T2) · `/master` (T2) · MIME/size/magic/pdf-lib (T2) · sha dedupe (T2) · R2 content-addressed put (T2) · settings table + ensureSettings (T1) · apply-path precedence (T3) · `/health` field (T3) · verify_deploy (T4) · pull_master (T5) · docs incl. notify.ts header (T6) · ops + rollback (T7). Out-of-scope items (profile edits, multi-variant, deletion, local daemon) are untouched.

**Placeholder scan:** all tasks contain exact code, commands, and expected results. Task 7 uses `<FRESH_TOKEN>`/`<WEBHOOK_SECRET>` deliberately — these are operator-supplied secrets, never committed.

**Type consistency:** `ensureSettings/getSetting/setSetting` (T1) are exactly the names imported in `webhook.ts` (T2) and `index.ts` (T3). `currentPointer` returns `{sha, source}` in both webhook and apply-path usages. `MAX_MASTER_BYTES`, `WebhookEnv`, `BucketLike`, `handleTelegramUpdate(update, db, bucket, env)` signatures match across T2/T3. Reply prefixes match the spec verbatim.
