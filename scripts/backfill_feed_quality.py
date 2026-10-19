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
from typing import Any, Dict, List, Optional, Tuple

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
# Role classification (mirrors worker/src/lib/roles.ts reject families).
# Only "reject" verdicts are actionable for the backfill; unknowns are left alone.
# ---------------------------------------------------------------------------

def _has_word(text: str, word: str) -> bool:
    return f" {text} ".find(f" {word} ") >= 0


def classify_role(title: Optional[str], reject_crm: bool = True) -> Optional[str]:
    """Return a rule slug if the title is a reject, else None."""
    if not title or not isinstance(title, str):
        return None
    text = title.strip().lower()
    # flatten punctuation to spaces, keep dots inside words
    import re
    norm = re.sub(r"[^\w.\s]", " ", text)
    norm = re.sub(r"\s+", " ", norm).strip()
    words = norm.split()
    word_set = set(words)
    norm_spaced = " ".join(words)

    # people-management words override IC allow
    mgmt_words = {"manager", "team lead", "group lead", "head of", "director", "vp", "chief", "supervisor"}
    has_mgmt = any(_has_word(norm_spaced, w) for w in mgmt_words)

    # IC allow: lead engineer / seniority + engineering noun, only if no mgmt word
    ic_phrases = ["lead engineer", "lead software engineer"]
    seniority = {"staff", "principal", "senior", "sr"}
    eng_nouns = {"engineer", "engineers", "engineering"}
    is_ic = False
    if not has_mgmt:
        if any(_has_word(norm_spaced, p) for p in ic_phrases):
            is_ic = True
        elif (word_set & seniority) and (word_set & eng_nouns):
            is_ic = True
    if is_ic:
        return None

    # reject families: (family prefix, slug, list of words)
    families: List[Tuple[str, List[Tuple[str, str]]]] = []
    families.append(("people-management", [
        ("manager", "manager"),
        ("team lead", "team lead"),
        ("group lead", "group lead"),
        ("head of", "head of"),
        ("director", "director"),
        ("vp", "vp"),
        ("chief", "chief"),
        ("supervisor", "supervisor"),
    ]))
    families.append(("revenue", [
        ("sales", "sales"),
        ("sales development", "sales development"),
        ("business development", "business development"),
        ("account executive", "account executive"),
        ("account manager", "account manager"),
        ("marketing", "marketing"),
        ("partner", "partner"),
        ("customer success", "customer success"),
        ("customer support", "customer support"),
    ]))
    families.append(("non-engineering", [
        ("recruiter", "recruiter"),
        ("talent acquisition", "talent acquisition"),
        ("human resources", "human resources"),
        ("accountant", "accountant"),
        ("finance", "finance"),
        ("legal", "legal"),
        ("nurse", "nurse"),
        ("medical", "medical"),
        ("driver", "driver"),
        ("content writer", "content writer"),
        ("graphic designer", "graphic designer"),
        ("intern", "intern"),
        ("product manager", "product manager"),
    ]))

    matches: List[Tuple[int, str]] = []  # (word length, slug)
    for family, rules in families:
        for slug, word in rules:
            if _has_word(norm_spaced, word):
                matches.append((len(word), f"{family}:{slug.replace(' ', '-')}"))

    # CRM
    if reject_crm and _has_word(norm_spaced, "salesforce"):
        companions = ("consultant", "architect", "administrator", "analyst", "developer")
        if any(_has_word(norm_spaced, c) for c in companions):
            matches.append((len("salesforce"), "crm:salesforce"))

    if not matches:
        return None
    # most specific = longest matched word
    matches.sort(key=lambda m: m[0])
    return matches[-1][1]


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


def plan_backfill(db_path: Path) -> Dict[str, Any]:
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
        rule = classify_role(r.get("title"))
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
                role_fingerprint=r["role_fingerprint"], content_hash=r["content_hash"],
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

    # duplicates: group non-terminal, non-demoted rows by (company, title)
    dup_count = 0
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in non_terminal:
        if r["id"] in demoted_ids:
            continue
        key = (str(r.get("company") or "").strip().lower(),
               str(r.get("title") or "").strip().lower())
        groups.setdefault(key, []).append(r)
    for key, grp in groups.items():
        if len(grp) <= 1:
            continue
        grp_sorted = sorted(grp, key=lambda x: x["id"])
        owner = grp_sorted[0]
        for r in grp_sorted[1:]:
            reason = f"duplicate of {owner['id']}"
            demotions.append({"id": r["id"], "to_state": "DUPLICATE", "reason": reason})
            demoted_ids.add(r["id"])
            dup_count += 1

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
        "duplicate": dup_count,
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
    print(f"{'demoted (duplicate)':<28} {plan['duplicate']}")
    print(f"{'fingerprints backfilled':<28} {plan['fingerprints']}")
    print("-" * 40)
    total = plan["role"] + plan["freshness"] + plan["duplicate"] + plan["fingerprints"]
    print(f"{'total changes':<28} {total}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill pre-existing feed-quality violations.")
    ap.add_argument("--apply", action="store_true", help="perform writes (default dry run)")
    ap.add_argument("--db", dest="db_path", default=None)
    ap.add_argument("--db-path", dest="db_path", default=None)
    ap.add_argument("--database", dest="db_path", default=None)
    args = ap.parse_args(argv)

    db_path = args.db_path
    if db_path is None:
        db_path = str(DEFAULT_DB)
    db_path = Path(db_path)

    try:
        if not db_path.exists():
            print(f"error: db not found: {db_path}")
            return 2
        plan = plan_backfill(db_path)
    except Exception as e:
        print(f"error: {e}")
        return 2

    _print_summary(plan, args.apply)

    total_changes = (plan["role"] + plan["freshness"] + plan["duplicate"] + plan["fingerprints"])
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
            "DUPLICATE": JobState.DUPLICATE,
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
