"""One-off backfill: demote pre-existing rows that violate the feed-quality rules.

Dry-run by default. Pass --apply to perform the writes.

Exit codes: 0 ok, 1 nothing to do, 2 error.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "jobs.db"

TERMINAL_STATES = {"DUPLICATE", "PRE_FILTERED_OUT", "SUBMITTED"}


def _load_storage():
    from job_hunt.storage import Storage
    return Storage


def _load_models():
    from job_hunt.models import JobState
    return JobState


# ---------------------------------------------------------------------------
# Role classification.
#
# The rules live in job_hunt.evaluation.engine (ROLE_REJECT_RULES), the same
# table the runtime applies and the Worker mirrors. This script must not carry
# a third copy: a stale copy demotes the wrong rows.
# ---------------------------------------------------------------------------

def classify_role(title: Optional[str], reject_crm: bool = True) -> Optional[str]:
    """Return the rule slug if the title is a reject, else None."""
    if not title or not isinstance(title, str):
        return None
    from job_hunt.evaluation.engine import classify_role_reject
    return classify_role_reject(title, reject_crm=reject_crm)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_rows(db_path: Path) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def _hours_ago(posted_at: Any) -> Optional[int]:
    from job_hunt.discovery.freshness import parse_posted_at
    parsed = parse_posted_at(posted_at)
    if parsed is None:
        return None
    now = datetime.now(timezone.utc)
    secs = (now - parsed).total_seconds()
    if secs < 0:
        secs = 0
    return int(round(secs / 3600.0))


def plan_backfill(db_path: Path, reject_crm: bool = True) -> Dict[str, Any]:
    JobState = _load_models()
    from job_hunt.discovery.freshness import is_fresh
    from job_hunt.dedup import compute_role_fingerprint

    rows = _load_rows(db_path)
    non_terminal = [r for r in rows if not _is_terminal(r["state"])]

    # role + freshness demotions
    demotions: List[Dict[str, Any]] = []  # {id, to_state, reason}
    demoted_ids: set = set()

    # role
    role_count = 0
    fresh_count = 0
    for r in non_terminal:
        rule = classify_role(r.get("title"), reject_crm=reject_crm)
        if rule is not None:
            reason = f"role gate: {rule}"
            demotions.append({"id": r["id"], "to_state": "PRE_FILTERED_OUT", "reason": reason})
            demoted_ids.add(r["id"])
            role_count += 1
            continue
        # freshness
        try:
            job = None
            from job_hunt.models import JobPosting
            # build minimal posting for is_fresh
            job = JobPosting(
                source=r["source"], title=r["title"], company=r["company"],
                raw_url=r["raw_url"], canonical_url=r["canonical_url"],
                canonical_url_hash=r["canonical_url_hash"],
                role_fingerprint=r.get("role_fingerprint") or "", content_hash=r["content_hash"],
                location=r.get("location"), posted_at=r.get("posted_at"),
            )
        except Exception:
            job = None
        stale = False
        if job is not None:
            if is_fresh(job, 24) is False:
                stale = True
        if stale:
            n = _hours_ago(r.get("posted_at"))
            reason = f"stale: posted {n}h ago"
            demotions.append({"id": r["id"], "to_state": "PRE_FILTERED_OUT", "reason": reason})
            demoted_ids.add(r["id"])
            fresh_count += 1

    # fingerprints: non-terminal rows where computed differs
    fp_updates: List[Dict[str, Any]] = []
    fp_count = 0
    for r in non_terminal:
        try:
            new_fp = compute_role_fingerprint(r.get("company"), r.get("title"), r.get("location"))
        except Exception:
            new_fp = r.get("role_fingerprint")
        if new_fp and new_fp != r.get("role_fingerprint"):
            fp_updates.append({"id": r["id"], "fingerprint": new_fp})
            fp_count += 1

    return {
        "role": role_count,
        "freshness": fresh_count,
        "fingerprints": fp_count,
        "demotions": demotions,
        "fp_updates": fp_updates,
        "non_terminal": len(non_terminal),
    }


def _print_summary(plan: Dict[str, Any], apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    print(f"Backfill feed quality ({mode})")
    print("-" * 40)
    print(f"{'demoted (role)':<28} {plan['role']}")
    print(f"{'demoted (freshness)':<28} {plan['freshness']}")
    print(f"{'fingerprints backfilled':<28} {plan['fingerprints']}")
    print("-" * 40)
    total = plan["role"] + plan["freshness"] + plan["fingerprints"]
    print(f"{'total changes':<28} {total}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill pre-existing feed-quality violations.")
    ap.add_argument("--apply", action="store_true", help="perform writes (default dry run)")
    ap.add_argument("--db", dest="db_path", default=None)
    ap.add_argument("--db-path", dest="db_path", default=None)
    ap.add_argument("--database", dest="db_path", default=None)
    ap.add_argument("--no-reject-crm", action="store_true", help="disable CRM role rejection (parity with REJECT_CRM_ROLES=false)")
    args = ap.parse_args(argv)

    db_path = args.db_path
    if db_path is None:
        db_path = str(DEFAULT_DB)
    db_path = Path(db_path)

    try:
        if not db_path.exists():
            print(f"error: db not found: {db_path}")
            return 2
        plan = plan_backfill(db_path, reject_crm=not args.no_reject_crm)
    except Exception as e:
        print(f"error: {e}")
        return 2

    _print_summary(plan, args.apply)

    total_changes = (plan["role"] + plan["freshness"] + plan["fingerprints"])
    if not args.apply:
        return 0 if total_changes > 0 else 1

    # apply
    try:
        JobState = _load_models()
        storage_mod = _load_storage()
        storage = storage_mod(db_path)
        # state transitions
        state_map = {
            "PRE_FILTERED_OUT": JobState.PRE_FILTERED_OUT,
        }
        for d in plan["demotions"]:
            try:
                storage.update_job_state(
                    d["id"], state_map[d["to_state"]],
                    details=d["reason"],
                )
            except Exception as e:
                # skip invalid transitions
                print(f"skip job {d['id']}: {e}")
        # fingerprints
        conn = _connect(db_path)
        try:
            now = datetime.now(timezone.utc).isoformat()
            for u in plan["fp_updates"]:
                conn.execute(
                    "UPDATE jobs SET role_fingerprint = ?, updated_at = ? WHERE id = ?",
                    (u["fingerprint"], now, u["id"]),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"error: {e}")
        return 2

    return 0 if total_changes > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
