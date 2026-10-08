#!/usr/bin/env python3
"""Post-deploy verification for the Cloudflare Worker (read-only).

Checks the live deployment answers, the safety flags are off, and every
given source yields (or reports inconclusive, never silently).

Usage:
    python scripts/verify_deploy.py --sources freehire-remote-tech,bdjobs-bangladesh-tech
    python scripts/verify_deploy.py --new-only        # sources added vs origin/main
    python scripts/verify_deploy.py --sources all     # full shard (slow: 241 probes)

Exit 0: deployed pipeline healthy (warnings allowed for inconclusive/empty).
Exit 1: health/sources/coverage unreachable, safety flag wrong, or a probe
        transport error (502). Those are deploy failures, not empty boards.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://jobhunt.habdulsamed777.workers.dev"


def req(method: str, path: str, payload=None, timeout: int = 60):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", "User-Agent": "jobhunt-verify/1.0"},
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": "unreadable body"}
    except Exception as e:
        return -1, {"error": f"transport: {e}"[:200]}


def new_sources_since_main() -> list:
    try:
        out = subprocess.run(
            ["git", "diff", "origin/main...HEAD", "--", "config/sources.yaml"],
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception:
        return []
    names, pending_adapter = [], None
    for line in out.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = re.match(r"\+- adapter:\s*(\S+)", line)
        if m:
            pending_adapter = m.group(1)
            continue
        m = re.match(r"\+  name:\s*(\S+)", line)
        if m and pending_adapter:
            names.append(m.group(1))
            pending_adapter = None
    return names


def classify_probe(name: str, status: int, probe: dict):
    """Decide a probe outcome. Pure function — unit-tested, no network."""
    if status == 404:
        return ("fail", f"{name}: unknown source (not in deployed shard?)")
    if status == 502 or status == -1:
        return ("fail", f"{name}: transport error: {probe}")
    if probe.get("inconclusive"):
        return ("warn", f"{name}: inconclusive ({probe.get('error', '')[:80]})")
    if (probe.get("remote_fresh") or 0) == 0:
        return ("warn", f"{name}: zero fresh-remote jobs (board quiet or stale, not a deploy failure)")
    return ("ok", "")


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE)
    ap.add_argument("--sources", default="")
    ap.add_argument("--new-only", action="store_true")
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")

    names: list = []
    if args.sources and args.sources != "all":
        names = [n.strip() for n in args.sources.split(",") if n.strip()]
    elif args.sources == "all":
        print("note: --sources all probes nothing directly; shard health comes from /coverage")
    elif args.new_only:
        names = new_sources_since_main()
        print(f"sources added vs origin/main: {names or 'none'}")

    failures, warnings, rows = [], [], []

    status, health = req("GET", "/health")
    if status != 200 or not health.get("ok"):
        failures.append(f"/health unreachable: {status} {health}")
    else:
        print(f"health: jobs={health.get('jobs')} skills={health.get('profile_skills')} live_apply={health.get('live_apply')}")
        if health.get("live_apply") is True:
            failures.append("SAFETY: live_apply is true on production — investigate before any run")

    status, sources = req("GET", "/sources")
    if status != 200:
        failures.append(f"/sources unreachable: {status}")
    else:
        print(f"shard: {sources.get('worker_sources')} worker + {sources.get('python_only_sources')} python-only")

    status, coverage = req("GET", "/coverage")
    if status != 200:
        failures.append(f"/coverage unreachable: {status}")
    else:
        print(f"coverage: {coverage.get('sources_yielding')}/{coverage.get('sources_attempted')} yielding")

    for name in names:
        status, probe = req("POST", "/probe", {"name": name}, timeout=90)
        kind, msg = classify_probe(name, status, probe)
        if kind == "fail":
            failures.append(msg)
        elif kind == "warn":
            warnings.append(msg)
            rows.append((name, "inconclusive" if probe.get("inconclusive") else "empty", (probe.get("error") or "")[:60]))
        else:
            rows.append((name, f"raw={probe.get('raw')} fresh={probe.get('fresh_12h')} remote={probe.get('remote_fresh')}", ""))

    print("\nprobe results:")
    for name, res, extra in rows:
        print(f"  {name:<32} {res} {extra}")
    for w in warnings:
        print(f"WARN: {w}")
    for f in failures:
        print(f"FAIL: {f}")
    print(f"\n{len(rows)} probed, {len(warnings)} warnings, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
