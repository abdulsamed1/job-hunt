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
