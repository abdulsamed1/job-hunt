#!/usr/bin/env python3
"""Generate the Worker source shard from config/sources.yaml.

Python/Actions is the single source of truth for source coverage. The Worker
cannot run every Python adapter (TLS impersonation, RSA handshakes, Playwright),
so unsupported kinds are emitted as an explicit `pythonOnly` list instead of
being silently dropped -- coverage accounting stays honest.

Usage:
    python worker/scripts/gen_sources.py          # write generated file
    python worker/scripts/gen_sources.py --check  # fail on drift (CI/test)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCES_YAML = ROOT / "config" / "sources.yaml"
OUT_TS = ROOT / "worker" / "src" / "sources.generated.ts"

# Adapters that cannot run on Workers: TLS impersonation, RSA handshake,
# or browser/HTML scraping. These stay on the Python/Actions side.
PYTHON_ONLY_ADAPTERS = {"bayt", "naukri", "web"}

# Curated high-signal boards polled hourly; the rest run on the daily deep sweep.
HOURLY_ORGS = {
    "coinbase", "ripple", "consensys", "stellar", "uniswap", "opensea", "alchemy",
}
HOURLY_FEEDS = {
    "remoteok", "remotive", "arbeitnow", "jobicy", "cryptojobslist", "crypto.jobs",
    "weworkremotely", "remote3", "bitcoinjobs",
}


def org_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def ts_str(value) -> str:
    return json_dumps(value)


def json_dumps(value) -> str:
    import json

    return json.dumps(value)


def build() -> tuple[list[dict], list[dict], dict]:
    data = yaml.safe_load(SOURCES_YAML.read_text()) or {}
    entries = data.get("sources") or []
    worker: list[dict] = []
    python_only: list[dict] = []

    for entry in entries:
        adapter = entry.get("adapter")
        name = entry.get("name")
        if not adapter or not name:
            continue

        base = {"name": name, "url": entry.get("url") or ""}

        if adapter == "greenhouse":
            kind, org, cadence = "greenhouse", org_from_url(base["url"]), "hourly" if org_from_url(base["url"]) in HOURLY_ORGS else "6h"
        elif adapter == "lever":
            org = org_from_url(base["url"])
            kind, cadence = "lever", "hourly" if org in HOURLY_ORGS else "6h"
        elif adapter == "ashby":
            org = org_from_url(base["url"])
            kind, cadence = "ashby", "hourly" if org in HOURLY_ORGS else "6h"
        elif adapter == "smartrecruiters":
            org = org_from_url(base["url"])
            kind, cadence = "smartrecruiters", "6h"
        elif adapter == "feed":
            kind, org = "rss", ""
            cadence = "hourly" if name in HOURLY_FEEDS else "6h"
        elif adapter == "ashby-index":
            kind, org = "ashby-index", ""
            cadence = "6h"
        elif adapter == "linkedin":
            kind, org = "linkedin", ""
            cadence = "6h"
        elif adapter == "indeed":
            kind, org = "indeed", ""
            cadence = "6h"
        elif adapter in PYTHON_ONLY_ADAPTERS:
            python_only.append({"name": name, "adapter": adapter, "url": base["url"]})
            continue
        else:
            python_only.append({"name": name, "adapter": str(adapter), "url": base["url"]})
            continue

        record = {"kind": kind, "name": name, "cadence": cadence}
        if org:
            record["org"] = org
        if base["url"] and kind in {"rss", "linkedin", "indeed", "ashby-index"}:
            record["url"] = base["url"]
        if kind == "linkedin":
            record["queries"] = entry.get("queries") or ["backend", "fullstack", "software"]
            record["locations"] = entry.get("locations") or ["Remote"]
            record["tprSeconds"] = entry.get("tpr_seconds") or 43200
        if kind == "indeed":
            record["queries"] = entry.get("queries") or ["backend", "fullstack", "software"]
            record["locations"] = entry.get("locations") or ["Cairo, Egypt"]
            record["country"] = entry.get("country") or "Egypt"
        if kind == "ashby-index":
            record["maxOrgs"] = entry.get("max_orgs") or 8
        worker.append(record)

    stats = {
        "yaml_total": len(entries),
        "worker_supported": len(worker),
        "python_only": len(python_only),
    }
    return worker, python_only, stats


def render(worker: list[dict], python_only: list[dict], stats: dict) -> str:
    hourly = sum(1 for s in worker if s["cadence"] == "hourly")
    lines = [
        "// GENERATED FILE -- do not edit by hand.",
        "// Source: config/sources.yaml via worker/scripts/gen_sources.py",
        "// Regenerate: python worker/scripts/gen_sources.py",
        "",
        "import type { SourceDef } from \"./sources.js\";",
        "",
        f"// yaml_total={stats['yaml_total']} worker_supported={stats['worker_supported']}"
        f" python_only={stats['python_only']} hourly={hourly}",
        "",
        "export const GENERATED_SOURCES: SourceDef[] = [",
    ]
    for record in worker:
        lines.append("  " + ts_str(record) + ",")
    lines += [
        "];",
        "",
        "export interface PythonOnlySource {",
        "  name: string;",
        "  adapter: string;",
        "  url: string;",
        "}",
        "",
        "// Cannot run on Workers (TLS impersonation / RSA / browser). Stays on Python/Actions.",
        "export const PYTHON_ONLY_SOURCES: PythonOnlySource[] = [",
    ]
    for record in python_only:
        lines.append("  " + ts_str(record) + ",")
    lines += ["];", ""]
    return "\n".join(lines)


def main() -> int:
    worker, python_only, stats = build()
    text = render(worker, python_only, stats)
    if "--check" in sys.argv:
        current = OUT_TS.read_text() if OUT_TS.exists() else ""
        if current != text:
            print("DRIFT: worker/src/sources.generated.ts is stale; run python worker/scripts/gen_sources.py")
            return 1
        print(f"OK: {stats['worker_supported']} worker sources, {stats['python_only']} python-only")
        return 0
    OUT_TS.write_text(text)
    print(
        f"wrote {OUT_TS.relative_to(ROOT)}: {stats['worker_supported']} worker sources, "
        f"{stats['python_only']} python-only, {stats['yaml_total']} total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
