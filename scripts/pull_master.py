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
