"""Tests for scripts/backfill_feed_quality.py."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_hunt.dedup import compute_role_fingerprint
from job_hunt.models import JobPosting, JobState
from job_hunt.storage import Storage


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_feed_quality.py"
    spec = importlib.util.spec_from_file_location("backfill_feed_quality", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _add_job(db, *, title, company="Co", state=JobState.DISCOVERED, posted_at=None,
             role_fingerprint="rf", location="Remote"):
    storage = Storage(db)
    job = JobPosting(
        source="test", title=title, company=company,
        raw_url="https://example.com/x", canonical_url="https://example.com/x",
        canonical_url_hash="h", role_fingerprint=role_fingerprint, content_hash="c",
        location=location, posted_at=posted_at,
    )
    saved, _ = storage.add_job(job)
    if state != JobState.DISCOVERED:
        storage.update_job_state(saved.id, state, force=True)
    return saved.id


def test_dry_run_changes_nothing(tmp_path):
    db = tmp_path / "jobs.db"
    jid = _add_job(db, title="Engineering Manager")
    mod = _load()
    rc = mod.main(["--db", str(db)])
    storage = Storage(db)
    job = storage.get_job(jid)
    assert job.state == JobState.DISCOVERED
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n == 1
    # dry run reports the planned change
    assert rc in (0, 1)


def test_apply_demotes_manager_rows_with_a_reason(tmp_path):
    db = tmp_path / "jobs.db"
    jid = _add_job(db, title="Engineering Manager")
    mod = _load()
    rc = mod.main(["--db", str(db), "--apply"])
    assert rc == 0
    storage = Storage(db)
    job = storage.get_job(jid)
    assert job.state == JobState.PRE_FILTERED_OUT
    with sqlite3.connect(db) as conn:
        details = conn.execute("SELECT details FROM audit_log WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()[0]
    assert "role gate" in (details or "").lower()


def test_apply_leaves_submitted_rows_alone(tmp_path):
    db = tmp_path / "jobs.db"
    jid = _add_job(db, title="Engineering Manager", state=JobState.SUBMITTED)
    mod = _load()
    mod.main(["--db", str(db), "--apply"])
    storage = Storage(db)
    job = storage.get_job(jid)
    assert job.state == JobState.SUBMITTED


def test_apply_demotes_rows_older_than_24h(tmp_path):
    db = tmp_path / "jobs.db"
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    jid = _add_job(db, title="Backend Engineer", posted_at=old)
    mod = _load()
    rc = mod.main(["--db", str(db), "--apply"])
    assert rc == 0
    storage = Storage(db)
    job = storage.get_job(jid)
    assert job.state == JobState.PRE_FILTERED_OUT
    with sqlite3.connect(db) as conn:
        details = conn.execute("SELECT details FROM audit_log WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,)).fetchone()[0]
    assert "stale: posted" in (details or "").lower()


def test_apply_backfills_role_fingerprint_for_non_terminal_rows(tmp_path):
    db = tmp_path / "jobs.db"
    jid = _add_job(db, title="Backend Engineer", role_fingerprint="old")
    mod = _load()
    mod.main(["--db", str(db), "--apply"])
    storage = Storage(db)
    job = storage.get_job(jid)
    assert job.role_fingerprint == compute_role_fingerprint("Co", "Backend Engineer", "Remote")
