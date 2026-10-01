"""Unit tests for recency and remote post-filters."""

from datetime import datetime, timedelta, timezone

import pytest

from job_hunt.discovery.freshness import (
    filter_recent,
    filter_remote,
    is_fresh,
    is_remoteish,
    parse_posted_at,
)
from job_hunt.models import JobPosting


def _job(**kw):
    base = dict(
        source="test", title="Backend Engineer", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
    )
    base.update(kw)
    return JobPosting(**base)


def test_parse_posted_at_formats():
    now = datetime.now(timezone.utc)
    assert parse_posted_at(now.isoformat()) is not None
    assert parse_posted_at("2026-09-30") is not None
    assert parse_posted_at(int(now.timestamp())) is not None  # epoch seconds
    assert parse_posted_at(int(now.timestamp() * 1000)) is not None  # epoch ms
    assert parse_posted_at(str(int(now.timestamp() * 1000))) is not None
    assert parse_posted_at("Tue, 30 Sep 2026 10:00:00 GMT") is not None  # RFC822
    assert parse_posted_at("5 hours ago") is not None
    assert parse_posted_at("3 days ago") is not None
    assert parse_posted_at("") is None
    assert parse_posted_at(None) is None
    assert parse_posted_at("soon") is None


def test_is_fresh_keeps_dateless():
    fresh = _job(posted_at=datetime.now(timezone.utc).isoformat())
    assert is_fresh(fresh, 24) is True
    stale = _job(posted_at="2020-01-01")
    assert is_fresh(stale, 24) is False
    dateless = _job(posted_at=None)
    assert is_fresh(dateless, 24) is None


def test_filter_recent_drops_only_provably_stale():
    jobs = [
        _job(posted_at=datetime.now(timezone.utc).isoformat()),
        _job(posted_at="2020-01-01"),
        _job(posted_at=None),
    ]
    kept = filter_recent(jobs, 24)
    assert len(kept) == 2


def test_is_remoteish_signals():
    assert is_remoteish(_job(location="Remote")) is True
    assert is_remoteish(_job(location="Cairo, Egypt")) is False
    assert is_remoteish(_job(location="Cairo, Egypt (Remote)")) is True
    assert is_remoteish(_job(location="", description="Remote friendly role")) is True
    assert is_remoteish(_job(location=None, metadata={"remote_flag": True})) is True
    assert is_remoteish(_job(location=None, description="")) is False


def test_filter_remote_keeps_unknown_drops_onsite():
    jobs = [
        _job(location="Remote"),
        _job(location="Cairo, Egypt"),
        _job(location=None, description=""),
    ]
    kept = filter_remote(jobs)
    assert len(kept) == 2
