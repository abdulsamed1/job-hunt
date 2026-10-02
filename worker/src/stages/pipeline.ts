// Pipeline stages: evaluate (deterministic + gates), tailor (text CV + PDF),
// apply (direct ATS HTTP posts, approval-gated). No browser anywhere here.

import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import { scoreJob, type Profile } from "../lib/evaluate.js";
import { isFresh } from "../lib/freshness.js";

export interface EvalInput {
  title: string;
  description: string;
  location: string;
}

export function evaluateJob(input: EvalInput, profile: Profile, threshold: number) {
  return scoreJob(input.title, input.description, input.location, profile, threshold);
}

/** @deprecated — apply path must attach stored bytes */
export function buildTailoredText(
  job: { title: string; company: string },
  profile: Profile & { fullName: string; email: string; phone: string; summary?: string },
): string {
  const keywords = new Set(`${job.title}`.toLowerCase().match(/[a-z0-9+#/.]+/g) || []);
  const matched = (profile.verifiedSkills || []).filter((s) => keywords.has(s.toLowerCase()));
  const rest = (profile.verifiedSkills || []).filter((s) => !keywords.has(s.toLowerCase()));
  const lines = [
    profile.fullName,
    `${profile.email} | ${profile.phone} | ${profile.location}`,
    "",
    "SUMMARY",
    profile.summary || `Software Engineer with ${profile.yearsOfExperience}+ years building reliable backend systems.`,
    "",
    `TECHNICAL SKILLS (matched first for ${job.title} @ ${job.company})`,
    [...matched, ...rest].join(", "),
    "",
    `TARGET ROLE: ${job.title} @ ${job.company}`,
  ];
  return lines.join("\n");
}

/** @deprecated — apply path must attach stored bytes */
export async function renderPdfBytes(textCv: string, fullName: string): Promise<Uint8Array> {
  // Minimal single-font PDF (Helvetica, no embedding): tiny CPU + bundle cost.
  const doc = await PDFDocument.create();
  const font = await doc.embedFont(StandardFonts.Helvetica);
  const bold = await doc.embedFont(StandardFonts.HelveticaBold);
  let page = doc.addPage([595, 842]);
  const margin = 48;
  let y = 800;
  const draw = (text: string, size: number, isBold: boolean) => {
    const f = isBold ? bold : font;
    const words = text.split(/\s+/);
    let line = "";
    const flush = () => {
      if (!line) return;
      if (y < 60) {
        page = doc.addPage([595, 842]);
        y = 800;
      }
      page.drawText(line, { x: margin, y, size, font: f, color: rgb(0.1, 0.1, 0.1) });
      y -= size + 4;
      line = "";
    };
    for (const w of words) {
      const trial = line ? `${line} ${w}` : w;
      if (f.widthOfTextAtSize(trial, size) > 595 - margin * 2) flush();
      line = trial;
    }
    flush();
  };
  let first = true;
  for (const raw of textCv.split("\n")) {
    const line = raw.trim();
    if (!line) {
      y -= 6;
      continue;
    }
    const heading = /^[A-Z][A-Z /()&-]{3,}$/.test(line) || first;
    draw(line.slice(0, 220), heading ? 12 : 9.5, heading);
    first = false;
  }
  void fullName;
  return doc.save();
}

export interface ApplyPayload {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  resumeBytes: Uint8Array;
  resumeFilename: string;
}

function multipart(parts: Record<string, string>, file: { field: string; filename: string; bytes: Uint8Array; mime: string }): { body: FormData; headers: Record<string, string> } {
  const body = new FormData();
  for (const [k, v] of Object.entries(parts)) body.append(k, v);
  body.append(file.field, new Blob([file.bytes as unknown as ArrayBuffer], { type: file.mime }), file.filename);
  return { body, headers: {} };
}

export interface ApplyResult {
  ok: boolean;
  detail: string;
}

/**
 * Direct ATS application POSTs. DRY-RUN BY DEFAULT: returns the would-be
 * payload without sending. Live sends require per-board approval flags.
 */
export async function applyToAts(
  board: { kind: "greenhouse" | "lever" | "ashby"; org: string; jobId: string },
  candidate: ApplyPayload,
  opts: { dryRun: boolean; approved: boolean },
): Promise<ApplyResult> {
  if (board.kind === "ashby") {
    // Ashby's submit contract varies per board; only dry-run is supported
    // until a supervised live run pins the exact endpoint for an org.
    return { ok: false, detail: "ashby live submit not pinned: dry-run only (verify endpoint per org first)" };
  }
  let url: string;
  let parts: Record<string, string>;
  if (board.kind === "greenhouse") {
    url = `https://boards-api.greenhouse.io/v1/boards/${board.org}/jobs/${board.jobId}`;
    parts = {
      first_name: candidate.firstName,
      last_name: candidate.lastName,
      email: candidate.email,
      phone: candidate.phone,
    };
  } else {
    url = `https://api.lever.co/v0/postings/${board.org}/${board.jobId}`;
    parts = {
      name: `${candidate.firstName} ${candidate.lastName}`,
      email: candidate.email,
      phone: candidate.phone,
    };
  }
  if (opts.dryRun || !opts.approved) {
    return { ok: true, detail: `DRY RUN: would POST multipart application to ${url}` };
  }
  const { body } = multipart(parts, {
    field: "resume",
    filename: candidate.resumeFilename,
    bytes: candidate.resumeBytes,
    mime: "application/pdf",
  });
  try {
    const res = await fetch(url, { method: "POST", body });
    const text = await res.text();
    if (res.ok) return { ok: true, detail: `submitted (HTTP ${res.status})` };
    return { ok: false, detail: `submit rejected HTTP ${res.status}: ${text.slice(0, 200)}` };
  } catch (e) {
    return { ok: false, detail: `submit transport error: ${String(e).slice(0, 200)}` };
  }
}

export function freshWithin(postedAt: unknown, hoursOld: number): boolean {
  return isFresh(postedAt, hoursOld) !== false;
}
