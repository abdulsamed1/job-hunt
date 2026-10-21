#!/usr/bin/env node
// Generate the Worker source shard from config/sources.yaml.
//
// The Worker is the only runtime now: every non-dead source in the config must
// map to a Worker adapter, and an adapter the Worker cannot run is a build
// failure rather than a silently dropped source. Coverage accounting stays
// honest because the generated file is the whole non-dead set.
//
// Usage:
//   node worker/scripts/gen_sources.mjs          # write generated file
//   node worker/scripts/gen_sources.mjs --check  # fail on drift (test/CI)

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SOURCES_YAML = resolve(ROOT, "config/sources.yaml");
const OUT_TS = resolve(ROOT, "worker/src/sources.generated.ts");

// Curated high-signal boards polled hourly; the rest run on the 6h deep sweep.
// Rebuilt from the measured probe ledger, ranked by remote yield at any age,
// with a hard cap of 2 crypto orgs and 3 crypto feeds so the 24x/day scout is
// industry-balanced instead of crypto-dominated.
const HOURLY_ORGS = new Set([
  "andurilindustries", "mongodb", "okta", "elastic", "cloudflare", "stripe",
  "samsara", "brex", "gitlab", "anthropic", "affirm", "fivetran", "datadog",
  "elevenlabs",
  "coinbase", "ripple", // crypto, capped at 2
]);
const HOURLY_FEEDS = new Set([
  "cryptojobslist-feed", "cryptocurrencyjobs", "crypto-jobs-feed", // crypto, capped at 3
  "remoteok", "weworkremotely-feed", "arbeitnow", "jobicy", "jobspresso", "remotive",
]);

/** Last path segment of a board URL (the tenant/org slug). */
export function orgFromUrl(url) {
  return String(url || "").replace(/\/+$/, "").split("/").pop();
}

const DEFAULT_QUERIES = ["backend", "fullstack", "software"];

function hoursOld(entry) {
  return Object.prototype.hasOwnProperty.call(entry, "hours_old") ? entry.hours_old : 24;
}

function base(entry, status) {
  return {
    industry: entry.industry || "general",
    status,
    hoursOld: hoursOld(entry),
  };
}

/** Map one config entry to a Worker SourceDef, or throw when it cannot run. */
export function toRecord(entry) {
  const adapter = entry.adapter;
  const name = entry.name;
  const status = entry.status || "unknown";
  const url = entry.url || "";
  const org = orgFromUrl(url);
  const b = base(entry, status);

  const record = (kind, cadence, extra = {}) => ({
    kind, name, cadence, industry: b.industry, status: b.status, ...extra,
  });

  switch (adapter) {
    case "greenhouse":
      return record("greenhouse", HOURLY_ORGS.has(org) ? "hourly" : "6h", { org, hoursOld: b.hoursOld });
    case "lever":
      return record("lever", HOURLY_ORGS.has(org) ? "hourly" : "6h", { org, hoursOld: b.hoursOld });
    case "ashby":
      return record("ashby", HOURLY_ORGS.has(org) ? "hourly" : "6h", { org, hoursOld: b.hoursOld });
    case "smartrecruiters":
      return record("smartrecruiters", "6h", { org, hoursOld: b.hoursOld });
    case "feed":
      return record("rss", HOURLY_FEEDS.has(name) ? "hourly" : "6h", { url, hoursOld: b.hoursOld });
    case "ashby-index":
      return record("ashby-index", "6h", { url, maxOrgs: entry.max_orgs || 8, hoursOld: b.hoursOld });
    case "linkedin":
      return record("linkedin", "6h", {
        url,
        queries: entry.queries || DEFAULT_QUERIES,
        locations: entry.locations || ["Remote"],
        tprSeconds: entry.tpr_seconds || 43200,
        hoursOld: b.hoursOld,
      });
    case "indeed":
      return record("indeed", "6h", {
        url,
        queries: entry.queries || DEFAULT_QUERIES,
        locations: entry.locations || ["Cairo, Egypt"],
        country: entry.country || "Egypt",
        hoursOld: b.hoursOld,
      });
    case "freehire":
    case "bdjobs":
      return record(adapter, "6h", {
        url,
        queries: entry.queries || DEFAULT_QUERIES,
        locations: entry.locations || ["Remote"],
        hoursOld: b.hoursOld,
      });
    case "teamtailor":
    case "recruitee":
    case "personio":
    case "bamboohr":
    case "workable":
      // Per-tenant boards: plain RSS/XML/JSON GETs, no TLS impersonation or
      // RSA — Worker-runnable; tenant derived from url.
      return record(adapter, "6h", { url, hoursOld: b.hoursOld });
    case "web":
      // Generic HTML/JSON-LD board: one page fetch per run.
      return record("web", "6h", { url, hoursOld: b.hoursOld });
    case "bayt":
    case "naukri":
      // HTML search (Bayt) and the public JSON API (Naukri); queries/locations
      // are expanded per source by the Worker adapter.
      return record(adapter, "6h", {
        url,
        queries: entry.queries || DEFAULT_QUERIES,
        locations: entry.locations || ["Remote"],
        hoursOld: b.hoursOld,
      });
    default:
      throw new Error(
        `no Worker adapter for source "${name}" (adapter: ${adapter}); ` +
        "add one in worker/src or mark the source dead in config/sources.yaml",
      );
  }
}

/** Build the shard from raw YAML text.
 *
 * Every supported source is emitted, including `status: dead` ones, with its
 * status attached. The Worker runs only non-dead entries but can /probe a dead
 * one, so a board that comes back to life can be promoted without a redeploy
 * of a hand-edited special case. Dead entries with no Worker adapter are
 * omitted (they could not be probed anyway). */
export function buildSources(configText) {
  const data = YAML.parse(configText) || {};
  const entries = data.sources || [];
  const records = [];
  let dead = 0;
  let deadSupported = 0;
  let unsupportedDead = 0;
  const skipped = [];
  for (const entry of entries) {
    if (!entry || !entry.adapter || !entry.name) {
      skipped.push(entry);
      continue;
    }
    const isDead = (entry.status || "unknown") === "dead";
    if (isDead) dead += 1;
    try {
      records.push(toRecord(entry));
      if (isDead) deadSupported += 1;
    } catch (e) {
      if (!isDead) throw e;
      unsupportedDead += 1;
    }
  }
  return {
    worker: records,
    skipped,
    stats: {
      yaml_total: entries.length,
      worker_supported: records.length - deadSupported,
      dead: deadSupported,
      dead_unsupported: unsupportedDead,
      emitted: records.length,
    },
  };
}

/** Render the generated TypeScript module. */
export function render(worker, stats) {
  const hourly = worker.filter((s) => s.cadence === "hourly").length;
  const lines = [
    "// GENERATED FILE -- do not edit by hand.",
    "// Source: config/sources.yaml via worker/scripts/gen_sources.mjs",
    "// Regenerate: node worker/scripts/gen_sources.mjs",
    "",
    'import type { SourceDef } from "./sources.js";',
    "",
    `// yaml_total=${stats.yaml_total} active=${stats.worker_supported} dead=${stats.dead} hourly=${hourly}`,
    "",
    "export const GENERATED_SOURCES: SourceDef[] = [",
  ];
  for (const record of worker) {
    lines.push("  " + JSON.stringify(record) + ",");
  }
  lines.push("];", "");
  return lines.join("\n");
}

export function generateCheck(current) {
  const { worker, stats } = buildSources(readFileSync(SOURCES_YAML, "utf8"));
  const text = render(worker, stats);
  return { ok: current === text, text, stats };
}

function main(argv) {
  const { worker, stats, skipped } = buildSources(readFileSync(SOURCES_YAML, "utf8"));
  if (skipped.length) {
    throw new Error(`${skipped.length} config entries are missing adapter/name`);
  }
  const text = render(worker, stats);
  if (argv.includes("--check")) {
    const current = existsSync(OUT_TS) ? readFileSync(OUT_TS, "utf8") : "";
    if (current !== text) {
      console.error("DRIFT: worker/src/sources.generated.ts is stale; run node worker/scripts/gen_sources.mjs");
      return 1;
    }
    console.log(`OK: ${stats.worker_supported} active + ${stats.dead} dead Worker sources, ${stats.yaml_total} configured`);
    return 0;
  }
  writeFileSync(OUT_TS, text);
  console.log(
    `wrote worker/src/sources.generated.ts: ${stats.worker_supported} worker sources, ` +
    `${stats.yaml_total} configured (${stats.dead} dead excluded)`,
  );
  return 0;
}

const invokedDirectly = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  process.exit(main(process.argv.slice(2)));
}
