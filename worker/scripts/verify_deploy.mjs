#!/usr/bin/env node
// Post-deploy verification for the Cloudflare Worker (read-only).
//
// Checks the live deployment answers, the safety flags match the intended
// deployment mode, the resume pointer exists, and every named source yields
// (or reports inconclusive, never silently).
//
// Usage:
//   node worker/scripts/verify_deploy.mjs --extra-sources a,b
//   node worker/scripts/verify_deploy.mjs --new-only
//   node worker/scripts/verify_deploy.mjs --expect-live          # autonomous apply deploys
//   node worker/scripts/verify_deploy.mjs --base-url http://localhost:8787
//
// Exit 0: deployed pipeline healthy (quiet/inconclusive boards warn).
// Exit 1: health/sources/coverage unreachable, flags wrong, or a probe
//         transport error. Those are deploy failures, not empty boards.

import { execFileSync } from "node:child_process";
import https from "node:https";

const DEFAULT_BASE = "https://jobhunt.habdulsamed777.workers.dev";


function parseArgs(argv) {
  const out = { baseUrl: DEFAULT_BASE, sources: "", newOnly: false, expectLive: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--base-url") out.baseUrl = argv[++i] || DEFAULT_BASE;
    else if (arg === "--sources" || arg === "--extra-sources") out.sources = argv[++i] || "";
    else if (arg === "--new-only") out.newOnly = true;
    else if (arg === "--expect-live") out.expectLive = true;
    else if (arg === "--help" || arg === "-h") out.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  out.baseUrl = out.baseUrl.replace(/\/+$/, "");
  return out;
}

/** One request, retried once on a transport failure: a stalled connect to the
 *  Worker says nothing about the deploy, and a false "unreachable" is worse
 *  than a two-second wait. */
async function reqRetry(baseUrl, method, path, payload, timeoutMs = 60_000) {
  let out = await req(baseUrl, method, path, payload, timeoutMs);
  if (out.status === -1) {
    await new Promise((r) => setTimeout(r, 1500));
    out = await req(baseUrl, method, path, payload, timeoutMs);
  }
  return out;
}

function req(baseUrl, method, path, payload, timeoutMs = 60_000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => { if (!settled) { settled = true; resolve(value); } };
    const u = new URL(baseUrl + path);
    const data = payload === undefined ? null : JSON.stringify(payload);
    const r = https.request({
      hostname: u.hostname,
      port: u.port || 443,
      path: u.pathname + u.search,
      method,
      family: 4,
      headers: {
        ...(data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {}),
        "User-Agent": "jobhunt-verify/2.0",
      },
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        let body = null;
        try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { body = { error: "unreadable body" }; }
        done({ status: res.statusCode || 0, body });
      });
    });
    r.setTimeout(timeoutMs, () => r.destroy(new Error("timeout")));
    r.on("error", (e) => done({ status: -1, body: { error: `transport: ${e.message}` } }));
    if (data) r.write(data);
    r.end();
  });
}

export function newSourcesSinceMain() {
  try {
    const diff = execFileSync("git", ["diff", "origin/main...HEAD", "--", "config/sources.yaml"], {
      encoding: "utf8",
    });
    const names = [];
    let pendingAdapter = null;
    for (const line of diff.split("\n")) {
      if (!line.startsWith("+") || line.startsWith("+++")) continue;
      const adapter = line.match(/^\+- adapter:\s*(\S+)/);
      if (adapter) { pendingAdapter = adapter[1]; continue; }
      const name = line.match(/^\+  name:\s*(\S+)/);
      if (name && pendingAdapter) { names.push(name[1]); pendingAdapter = null; }
    }
    return names;
  } catch {
    return [];
  }
}

/** Decide a probe outcome. Pure function — unit-tested, no network. */
export function classifyProbe(name, status, probe) {
  if (status === 404) return ["fail", `${name}: unknown source (not in deployed shard?)`];
  if (status === -1 || status >= 500) {
    return ["fail", `${name}: transport/server error (HTTP ${status}): ${JSON.stringify(probe)}`];
  }
  if (probe.inconclusive) return ["warn", `${name}: inconclusive (${String(probe.error || "").slice(0, 80)})`];
  // Empty only when the board yields nothing the product can use. The shipped
  // geography gate (remote anywhere, placed in Egypt, unknown location) is the
  // yardstick; remote_fresh is the fallback for an older deployment.
  const usable = probe.usable_fresh ?? probe.remote_fresh ?? 0;
  if (usable === 0) {
    return ["warn", `${name}: zero fresh-usable jobs (board quiet or stale, not a deploy failure)`];
  }
  return ["ok", ""];
}

/** Safety/schema checks over /health. Pure function — unit-tested, no network. */
export function checkHealth(health, expectLive = false) {
  const failures = [];
  if (expectLive && health.live_apply !== true) {
    failures.push("SAFETY: expected autonomous apply (live_apply=true) but the deployment reports it off");
  }
  if (!expectLive && health.live_apply === true) {
    failures.push("SAFETY: live_apply is true but this deploy was verified in dry-run mode — rerun with --expect-live");
  }
  if (health.notification_events === null || health.notification_events === undefined) {
    failures.push(
      "notification_events missing from /health — deployed schema predates the ledger migration " +
      "(redeploy with worker/src/state.ts ensureNotificationEvents)",
    );
  }
  const sha = health.master_resume_sha;
  if (typeof sha !== "string" || !/^[0-9a-f]{64}$/.test(sha)) {
    failures.push(
      "master_resume_sha missing/malformed in /health — the resume pointer is unset " +
      "or the Worker predates the settings migration",
    );
  }
  return failures;
}

export async function verify(opts) {
  let names = [];
  if (opts.sources && opts.sources !== "all") {
    names = opts.sources.split(",").map((n) => n.trim()).filter(Boolean);
  } else if (opts.sources === "all") {
    console.log("note: --sources all probes nothing directly; shard health comes from /coverage");
  } else if (opts.newOnly) {
    names = newSourcesSinceMain();
    console.log(`sources added vs origin/main: ${names.length ? names.join(", ") : "none"}`);
  }

  const failures = [];
  const warnings = [];
  const rows = [];

  const health = await reqRetry(opts.baseUrl, "GET", "/health");
  if (health.status !== 200 || !health.body?.ok) {
    failures.push(`/health unreachable: ${health.status} ${JSON.stringify(health.body)}`);
  } else {
    const h = health.body;
    console.log(
      `health: jobs=${h.jobs} skills=${h.profile_skills} live_apply=${h.live_apply} ` +
      `notification_events=${h.notification_events} master=${String(h.master_resume_sha).slice(0, 8)}`,
    );
    failures.push(...checkHealth(h, opts.expectLive));
  }

  const sources = await reqRetry(opts.baseUrl, "GET", "/sources");
  if (sources.status !== 200) failures.push(`/sources unreachable: ${sources.status}`);
  else console.log(`shard: ${sources.body.worker_sources} worker sources`);

  const coverage = await reqRetry(opts.baseUrl, "GET", "/coverage");
  if (coverage.status !== 200) failures.push(`/coverage unreachable: ${coverage.status}`);
  else console.log(`coverage: ${coverage.body.sources_yielding}/${coverage.body.sources_attempted} yielding`);

  for (const name of names) {
    const probe = await reqRetry(opts.baseUrl, "POST", "/probe", { name }, 90_000);
    const [kind, msg] = classifyProbe(name, probe.status, probe.body || {});
    if (kind === "fail") failures.push(msg);
    else if (kind === "warn") {
      warnings.push(msg);
      rows.push([name, probe.body?.inconclusive ? "inconclusive" : "empty", String(probe.body?.error || "").slice(0, 60)]);
    } else {
      const p = probe.body;
      rows.push([name, `raw=${p.raw} fresh=${p.fresh_24h} usable=${p.usable_fresh} remote=${p.remote_fresh}`, ""]);
    }
  }

  console.log("\nprobe results:");
  for (const [name, res, extra] of rows) console.log(`  ${name.padEnd(32)} ${res} ${extra}`);
  for (const w of warnings) console.log(`WARN: ${w}`);
  for (const f of failures) console.log(`FAIL: ${f}`);
  console.log(`\n${rows.length} probed, ${warnings.length} warnings, ${failures.length} failures`);
  return failures.length === 0 ? 0 : 1;
}

const invokedDirectly = process.argv[1]?.endsWith("verify_deploy.mjs");
if (invokedDirectly) {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    console.log("usage: node worker/scripts/verify_deploy.mjs [--base-url URL] [--new-only] [--sources a,b] [--expect-live]");
    process.exit(0);
  }
  process.exit(await verify(opts));
}
