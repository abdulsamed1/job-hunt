#!/usr/bin/env node
// Measure source liveness against the deployed Worker (read-only /probe).
//
// Liveness rule (same as the old Python probe): a source is live when it
// yields at least one *usable* posting at any age — remote anywhere, placed in
// Egypt, or unknown location. The 24h window gates the feed, not the source.
// A failed fetch is flaky/unknown, never dead; only a posting-less board that
// answered cleanly is dead, and an answered 404/410 is a clean "gone".
//
// Usage:
//   node worker/scripts/probe_sources.mjs                 # probe all, write ledger
//   node worker/scripts/probe_sources.mjs --apply         # promote live / demote hard-dead, regenerate shard
//   node worker/scripts/probe_sources.mjs --only stripe,figma
//   node worker/scripts/probe_sources.mjs --skip-dead     # skip boards already marked dead
//   node worker/scripts/probe_sources.mjs --include-linkedin
//
// Status policy: a quiet board is NOT removed. Only a fatal answer (404/410)
// demotes a board to `dead`; a board that answers but posts nothing right now
// stays configured and is re-measured, because it may post a remote/Egypt role
// tomorrow. Every dead board is re-probed by default so it can revive.
//
// LinkedIn verdicts are never applied to config: the guest endpoint throttles
// bursts, so a probe 429 says nothing about the board (and config keeps
// status: unknown deliberately). They are recorded in the ledger as flaky.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import https from "node:https";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const CONFIG = resolve(ROOT, "config/sources.yaml");
const LEDGER = resolve(ROOT, "data/source_probe.json");
const GEN = resolve(ROOT, "worker/scripts/gen_sources.mjs");
const DEFAULT_BASE = "https://jobhunt.habdulsamed777.workers.dev";

function parseArgs(argv) {
  const out = {
    baseUrl: DEFAULT_BASE, apply: false, only: "", includeLinkedin: false,
    skipDead: false, concurrency: 4, timeoutMs: 90_000,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--base-url") out.baseUrl = argv[++i] || DEFAULT_BASE;
    else if (arg === "--apply") out.apply = true;
    else if (arg === "--only") out.only = argv[++i] || "";
    else if (arg === "--include-linkedin") out.includeLinkedin = true;
    else if (arg === "--skip-dead") out.skipDead = true;
    else if (arg === "--concurrency") out.concurrency = Math.max(1, parseInt(argv[++i] || "4", 10));
    else if (arg === "--timeout-ms") out.timeoutMs = Math.max(1000, parseInt(argv[++i] || "90000", 10));
    else if (arg === "--help" || arg === "-h") out.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  out.baseUrl = out.baseUrl.replace(/\/+$/, "");
  return out;
}

/** POST JSON over an explicit IPv4 socket: sandbox hosts often advertise a
 *  broken IPv6 route, and a transport failure must mean the Worker failed, not
 *  this machine's DNS or connection pool. */
function postJson(url, payload, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => { if (!settled) { settled = true; resolve(value); } };
    let u;
    try { u = new URL(url); } catch (e) { done({ status: -1, error: `bad url: ${e.message}` }); return; }
    const data = JSON.stringify(payload);
    const req = https.request({
      hostname: u.hostname,
      port: u.port || 443,
      path: u.pathname + u.search,
      method: "POST",
      family: 4,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(data),
        "User-Agent": "jobhunt-probe/2.0",
      },
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        let body = {};
        try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { body = { error: "unreadable body" }; }
        done({ status: res.statusCode || 0, body });
      });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
    req.on("error", (e) => done({ status: -1, error: `transport: ${e.message}` }));
    req.end(data);
  });
}

async function probeOnce(baseUrl, name, timeoutMs) {
  const started = Date.now();
  const out = await postJson(baseUrl + "/probe", { name }, timeoutMs);
  if (out.status === -1) {
    return { status: -1, body: { error: out.error }, tookMs: Date.now() - started };
  }
  return { status: out.status, body: out.body, tookMs: Date.now() - started };
}

/** Two attempts: a reset/timeout is cheap to retry, a board answer is not. */
async function probe(baseUrl, name, timeoutMs) {
  let result = await probeOnce(baseUrl, name, timeoutMs);
  if (result.status === -1) {
    await new Promise((r) => setTimeout(r, 800));
    result = await probeOnce(baseUrl, name, timeoutMs);
  }
  return result;
}

/** Verdict for one probe, mirroring classify_verdict (live/dead/flaky). */
export function verdictFor(result) {
  const { status, body } = result;
  const error = String(body?.error || "");
  if (status === -1 || status >= 500) {
    const fatal = /\bHTTP (404|410)\b/i.test(error);
    return { verdict: fatal ? "dead" : "flaky", fatal, error };
  }
  if (status !== 200) return { verdict: "flaky", fatal: false, error: error || `HTTP ${status}` };
  if (body?.inconclusive) return { verdict: "flaky", fatal: false, error };
  const yielded = Number(body?.usable_any_age ?? body?.usable_fresh ?? 0);
  return { verdict: yielded > 0 ? "live" : "dead", fatal: false, error: "", yielded };
}

export function statusForVerdict(verdict) {
  return verdict === "live" ? "live" : verdict === "dead" ? "dead" : "unknown";
}

/** Only a promotion or a fatal demotion changes config. Quiet/flaky stays put. */
export function nextStatus(info) {
  if (!info) return null;
  if (info.verdict === "live") return "live";
  if (info.verdict === "dead" && info.fatal) return "dead";
  return null;
}

/** Rewrite `status:` inside config text, preserving formatting and comments. */
export function applyStatuses(configText, verdicts) {
  const lines = configText.split("\n");
  let changed = 0;
  let start = -1;
  const processBlock = (from, to) => {
    let name = "";
    let statusLine = -1;
    for (let i = from; i <= to; i++) {
      const nameMatch = lines[i].match(/^\s*(?:-\s*)?name:\s*(.+?)\s*$/);
      if (nameMatch && !name) name = nameMatch[1].replace(/^["']|["']$/g, "");
      if (/^\s*(?:-\s*)?status:\s*/.test(lines[i])) statusLine = i;
    }
    if (!name || statusLine < 0) return;
    const info = verdicts.get(name);
    if (!info) return;
    // Config blocks come in both shapes: `- adapter:` first (ATS entries) and
    // `- name:` first (web entries). Scan the block, don't assume line one.
    let adapter = "";
    for (let i = from; i <= to; i++) {
      const m = lines[i].match(/^\s*(?:-\s*)?adapter:\s*(\S+)/);
      if (m) { adapter = m[1]; break; }
    }
    if (adapter === "linkedin") return;
    const next = nextStatus(info);
    if (!next) return;
    const replaced = lines[statusLine].replace(/^(\s*status:\s*).*$/, `$1${next}`);
    if (replaced !== lines[statusLine]) {
      lines[statusLine] = replaced;
      changed += 1;
    }
  };
  for (let i = 0; i <= lines.length; i++) {
    if (i === lines.length || /^- /.test(lines[i])) {
      if (start >= 0) processBlock(start, i - 1);
      start = i;
    }
  }
  return { text: lines.join("\n"), changed };
}

async function main(argv) {
  const opts = parseArgs(argv);
  if (opts.help) {
    console.log("usage: node worker/scripts/probe_sources.mjs [--base-url URL] [--apply] [--only a,b] [--skip-dead] [--include-linkedin] [--concurrency N]");
    return 0;
  }
  const doc = YAML.parse(readFileSync(CONFIG, "utf8")) || {};
  const entries = (doc.sources || []).filter((e) => e && e.name && (!opts.skipDead || (e.status || "unknown") !== "dead"));
  const only = new Set(opts.only.split(",").map((s) => s.trim()).filter(Boolean));
  const targets = entries.filter((e) => {
    if (only.size && !only.has(e.name)) return false;
    if (e.adapter === "linkedin" && !opts.includeLinkedin) return false;
    return true;
  });

  console.log(`probing ${targets.length} sources at ${opts.baseUrl} (concurrency ${opts.concurrency})`);
  const queue = [...targets];
  const results = new Map();
  let done = 0;
  const worker = async () => {
    while (queue.length) {
      const entry = queue.shift();
      const result = await probe(opts.baseUrl, entry.name, opts.timeoutMs);
      const info = verdictFor(result);
      results.set(entry.name, { entry, result, info });
      done += 1;
      if (done % 10 === 0 || done === targets.length) {
        process.stdout.write(`  ${done}/${targets.length}\n`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(opts.concurrency, targets.length || 1) }, worker));

  // LinkedIn is never auto-applied: record flaky, keep config unknown.
  for (const entry of entries) {
    if (entry.adapter === "linkedin" && !results.has(entry.name)) {
      results.set(entry.name, {
        entry,
        result: { status: 0, body: {}, tookMs: 0 },
        info: { verdict: "flaky", fatal: false, error: "not probed (guest endpoint; config keeps unknown)" },
      });
    }
  }

  // Merge into the existing ledger: a --only run (or a source that is not in
  // the deployed shard) must not erase measurements it did not take.
  let previous = {};
  try { previous = JSON.parse(readFileSync(LEDGER, "utf8")) || {}; } catch { /* first run */ }
  const ledger = { ...previous };
  for (const name of [...results.keys()].sort()) {
    const { entry, result, info } = results.get(name);
    const body = result.body || {};
    if (result.status === 404 && String(body.error || "") === "unknown source") continue;
    ledger[name] = {
      attempts: result.status === 0 ? 0 : 1,
      error: String(info.error || "").slice(0, 500),
      fatal: !!info.fatal,
      industry: entry.industry || "general",
      kind: body.kind || entry.adapter || "",
      last_yield_at: null,
      probed_at: new Date().toISOString(),
      verdict: info.verdict,
      yielded: Number(info.yielded ?? body.usable_any_age ?? body.usable_fresh ?? 0),
    };
  }
  writeFileSync(LEDGER, JSON.stringify(ledger, null, 2) + "\n");

  const counts = { live: 0, dead: 0, flaky: 0 };
  for (const info of results.values()) counts[info.info.verdict] = (counts[info.info.verdict] || 0) + 1;
  console.log(`verdicts: ${counts.live} live, ${counts.dead} dead, ${counts.flaky} flaky/unknown`);
  console.log(`ledger: ${LEDGER}`);

  const failing = [...results.values()]
    .filter((r) => r.info.verdict !== "live")
    .slice(0, 15)
    .map((r) => `  ${r.entry.name}: ${r.info.verdict}${r.info.error ? ` (${r.info.error.slice(0, 90)})` : ""}`);
  if (failing.length) console.log("not live:\n" + failing.join("\n"));

  if (opts.apply) {
    const verdicts = new Map([...results.entries()].map(([name, r]) => [name, r.info]));
    const { text, changed } = applyStatuses(readFileSync(CONFIG, "utf8"), verdicts);
    if (changed) writeFileSync(CONFIG, text);
    console.log(`config: ${changed} status line(s) rewritten`);
    if (changed) {
      const { execFileSync } = await import("node:child_process");
      execFileSync(process.execPath, [GEN], { stdio: "inherit" });
    }
  }
  return 0;
}

const invokedDirectly = process.argv[1]?.endsWith("probe_sources.mjs");
if (invokedDirectly) {
  process.exit(await main(process.argv.slice(2)));
}
