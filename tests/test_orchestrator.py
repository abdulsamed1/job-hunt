"""Unit tests for orchestrator pipeline, stuck-job recovery, and retry behavior."""

from datetime import datetime, timezone, timedelta
import pytest
from job_hunt.models import JobPosting, JobState
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage


@pytest.fixture
def orchestrator(tmp_path):
    db_file = tmp_path / "orch_test.db"
    storage = Storage(db_file)
    return PipelineOrchestrator(storage=storage, db_path=str(db_file))


def test_stuck_job_recovery(orchestrator):
    storage = orchestrator.storage
    job = JobPosting(
        source="test",
        title="Software Engineer",
        company="CrashCorp",
        raw_url="https://example.com/job/crash",
        canonical_url="https://example.com/job/crash",
        canonical_url_hash="hcrash",
        role_fingerprint="rfcrash",
        content_hash="ccrash",
    )
    saved, _ = storage.add_job(job)

    # Transition through valid lifecycle: DISCOVERED -> ELIGIBLE -> TAILORED -> APPLICATION_STARTED
    storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
    storage.update_job_state(saved.id, JobState.TAILORED, details="Tailored")
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED, details="Started application")

    # Manually backdate updated_at to simulate a crash 30 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with storage._get_connection() as conn:
        conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_time, saved.id))
        conn.commit()

    # Run recovery
    recovered_count = orchestrator.recover_stuck_jobs(timeout_minutes=15)
    assert recovered_count == 1

    updated_job = storage.get_job(saved.id)
    assert updated_job.state == JobState.RETRY_PENDING

    # Verify audit entry recorded recovery
    audit = storage.get_audit_log(saved.id)
    assert any("Stuck application recovered" in (a.details or "") for a in audit)


def test_prevent_duplicate_submissions(orchestrator):
    storage = orchestrator.storage
    job = JobPosting(
        source="test",
        title="Principal Engineer",
        company="BigTech",
        raw_url="https://example.com/job/submitted",
        canonical_url="https://example.com/job/submitted",
        canonical_url_hash="hsub",
        role_fingerprint="rfsub",
        content_hash="csub",
    )
    saved, _ = storage.add_job(job)
    storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
    storage.update_job_state(saved.id, JobState.TAILORED, details="Tailored")
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED, details="Started")
    storage.update_job_state(saved.id, JobState.SUBMITTED, details="Application submitted successfully")

    # Re-discovering the same job must not reset or queue it for application
    job2 = JobPosting(
        source="another_feed",
        title="Principal Engineer",
        company="BigTech",
        raw_url="https://example.com/job/submitted?tracker=rss",
        canonical_url="https://example.com/job/submitted",
        canonical_url_hash="hsub",
        role_fingerprint="rfsub",
        content_hash="csub",
    )
    saved2, is_new = storage.add_job(job2)
    assert is_new is False
    assert saved2.id == saved.id
    assert saved2.state == JobState.SUBMITTED
