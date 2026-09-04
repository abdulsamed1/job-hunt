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


def test_has_already_applied_detects_all_dimensions(orchestrator):
    storage = orchestrator.storage
    from job_hunt.models import ApplicationRecord

    job = JobPosting(
        source="greenhouse",
        title="Backend Engineer",
        company="Stripe",
        raw_url="https://stripe.com/jobs/1",
        canonical_url="https://stripe.com/jobs/1",
        canonical_url_hash="hstripe1",
        role_fingerprint="stripe:backend_engineer:remote",
        content_hash="cstripe1",
    )
    saved, _ = storage.add_job(job)
    storage.update_job_state(saved.id, JobState.ELIGIBLE)
    storage.update_job_state(saved.id, JobState.TAILORED)
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED)

    record = ApplicationRecord(
        job_id=saved.id,
        state=JobState.SUBMITTED,
        confirmation_text="Thank you for applying to Stripe!",
    )
    storage.record_application(record)

    # 1. Check direct job_id
    is_dup, reason = storage.has_already_applied(job_id=saved.id)
    assert is_dup is True
    assert "Job ID" in reason

    # 2. Check by canonical URL hash
    is_dup, reason = storage.has_already_applied(canonical_url_hash="hstripe1")
    assert is_dup is True
    assert "canonical URL" in reason

    # 3. Check by role fingerprint
    is_dup, reason = storage.has_already_applied(role_fingerprint="stripe:backend_engineer:remote")
    assert is_dup is True
    assert "role fingerprint" in reason

    # 4. Check by company + title
    is_dup, reason = storage.has_already_applied(company="Stripe", title="Backend Engineer")
    assert is_dup is True
    assert "Stripe" in reason


@pytest.mark.asyncio
async def test_run_application_stage_skips_duplicates(orchestrator):
    storage = orchestrator.storage
    from unittest.mock import AsyncMock
    from job_hunt.models import ApplicationRecord

    # 1. Mark existing job as SUBMITTED
    job1 = JobPosting(
        source="lever",
        title="Senior Python Developer",
        company="GitLab",
        raw_url="https://gitlab.com/jobs/1",
        canonical_url="https://gitlab.com/jobs/1",
        canonical_url_hash="hgit1",
        role_fingerprint="gitlab:senior_python_developer:remote",
        content_hash="cgit1",
    )
    saved1, _ = storage.add_job(job1)
    storage.update_job_state(saved1.id, JobState.ELIGIBLE)
    storage.update_job_state(saved1.id, JobState.TAILORED)
    storage.update_job_state(saved1.id, JobState.APPLICATION_STARTED)
    storage.record_application(
        ApplicationRecord(job_id=saved1.id, state=JobState.SUBMITTED, confirmation_text="Applied")
    )

    # 2. Create another job for same company and title in TAILORED state
    job2 = JobPosting(
        source="ashby",
        title="Senior Python Developer",
        company="GitLab",
        raw_url="https://gitlab.com/jobs/2",
        canonical_url="https://gitlab.com/jobs/2",
        canonical_url_hash="hgit2",
        role_fingerprint="gitlab:senior_python_developer:remote2",
        content_hash="cgit2",
    )
    saved2, _ = storage.add_job(job2)
    storage.update_job_state(saved2.id, JobState.ELIGIBLE)
    storage.update_job_state(saved2.id, JobState.TAILORED)

    # Mock browser engine so fill_and_submit should NEVER be called for job2
    orchestrator.browser_engine.fill_and_submit = AsyncMock()

    processed = await orchestrator.run_application_stage(limit=10, dry_run=True)
    # job2 should have been marked DUPLICATE and skipped
    assert processed == 0
    assert orchestrator.browser_engine.fill_and_submit.await_count == 0

    reloaded2 = storage.get_job(saved2.id)
    assert reloaded2.state == JobState.DUPLICATE


def test_remote_outside_egypt_evaluation(orchestrator):
    from job_hunt.models import CandidateProfile
    from job_hunt.evaluation.engine import EvaluationEngine

    candidate = CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdulsamed@example.com",
        phone="+201094393750",
        location="Cairo, Egypt",
        open_to_remote=True,
        years_of_experience=4,
        verified_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Distributed Systems"],
    )

    engine = EvaluationEngine(min_score_threshold=70.0, use_llm=False)

    # Job 1: Remote (US / Worldwide)
    remote_job = JobPosting(
        source="greenhouse",
        title="Software Engineer, Backend",
        company="ScaleAI",
        raw_url="https://example.com/job/scale1",
        canonical_url="https://example.com/job/scale1",
        canonical_url_hash="hscale1",
        role_fingerprint="scale:backend:remote",
        content_hash="cscale1",
        location="Remote (Worldwide)",
        description="We are seeking a Backend Software Engineer with Python, FastAPI, and PostgreSQL. 3+ years experience.",
    )

    res = engine.evaluate(remote_job, candidate)
    assert res.pre_filtered is False
    assert res.eligible is True
    assert res.score >= 70.0

    # Job 2: Strict on-site outside Egypt (no remote)
    onsite_job = JobPosting(
        source="lever",
        title="Software Engineer, Infrastructure",
        company="TokyoRobotics",
        raw_url="https://example.com/job/tokyo",
        canonical_url="https://example.com/job/tokyo",
        canonical_url_hash="htokyo",
        role_fingerprint="tokyo:infra:onsite",
        content_hash="ctokyo",
        location="Tokyo, Japan",
        description="Must work strictly on-site from our Tokyo headquarters 5 days a week. No remote allowed.",
    )

    passed, reason = engine.pre_filter(onsite_job, candidate)
    assert passed is False
    assert "on-site" in reason.lower()
