"""Unit tests for SQLite storage persistence, state transitions, and audit trails."""

import os
import pytest
from job_hunt.dedup import canonical_url_hash, compute_role_fingerprint, content_hash, normalize_url
from job_hunt.models import (
    ApplicationRecord,
    CandidateProfile,
    EvaluationResult,
    JobPosting,
    JobState,
    TailoredCV,
)
from job_hunt.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db_file = tmp_path / "test_jobs.db"
    return Storage(db_file)


def test_add_job_and_provenance(storage):
    job = JobPosting(
        source="greenhouse",
        title="Backend Software Engineer",
        company="Stripe",
        raw_url="https://boards.greenhouse.io/stripe/jobs/101?utm_source=twitter",
        canonical_url=normalize_url("https://boards.greenhouse.io/stripe/jobs/101?utm_source=twitter"),
        canonical_url_hash=canonical_url_hash("https://boards.greenhouse.io/stripe/jobs/101"),
        role_fingerprint=compute_role_fingerprint("Stripe", "Backend Software Engineer", "Remote"),
        content_hash=content_hash("Backend role"),
        location="Remote",
        description="Backend role",
    )

    saved, is_new = storage.add_job(job)
    assert is_new is True
    assert saved.id is not None
    assert saved.state == JobState.DISCOVERED

    # Re-adding same job with different tracking URL
    job_dup = JobPosting(
        source="jobboard_aggregator",
        title="Backend Software Engineer",
        company="Stripe",
        raw_url="https://boards.greenhouse.io/stripe/jobs/101?utm_source=aggregator",
        canonical_url=normalize_url("https://boards.greenhouse.io/stripe/jobs/101?utm_source=aggregator"),
        canonical_url_hash=canonical_url_hash("https://boards.greenhouse.io/stripe/jobs/101"),
        role_fingerprint=compute_role_fingerprint("Stripe", "Backend Software Engineer", "Remote"),
        content_hash=content_hash("Backend role"),
        location="Remote",
        description="Backend role",
    )

    saved2, is_new2 = storage.add_job(job_dup)
    assert is_new2 is False
    assert saved2.id == saved.id

    # Check stats and provenance
    stats = storage.get_summary_stats()
    assert stats["DISCOVERED"] == 1
    assert stats["total_discovery_events"] == 2


def test_state_transitions_and_audit_log(storage):
    job = JobPosting(
        source="lever",
        title="Staff Engineer",
        company="Figma",
        raw_url="https://jobs.lever.co/figma/123",
        canonical_url="https://jobs.lever.co/figma/123",
        canonical_url_hash="h123",
        role_fingerprint="rf123",
        content_hash="c123",
    )
    saved, _ = storage.add_job(job)

    # Valid transition: DISCOVERED -> ELIGIBLE
    assert storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Score 85%") is True
    updated = storage.get_job(saved.id)
    assert updated.state == JobState.ELIGIBLE

    # Invalid transition: ELIGIBLE -> DUPLICATE should raise ValueError
    with pytest.raises(ValueError):
        storage.update_job_state(saved.id, JobState.DUPLICATE)

    # Check audit log
    audit_entries = storage.get_audit_log(saved.id)
    assert len(audit_entries) >= 2
    assert audit_entries[-1].to_state == "ELIGIBLE"
    assert audit_entries[-1].details == "Score 85%"


def test_evaluation_and_cv_persistence(storage):
    job = JobPosting(
        source="ashby",
        title="Full Stack Engineer",
        company="Vercel",
        raw_url="https://jobs.ashbyhq.com/vercel/999",
        canonical_url="https://jobs.ashbyhq.com/vercel/999",
        canonical_url_hash="hash999",
        role_fingerprint="rf999",
        content_hash="ch999",
    )
    saved, _ = storage.add_job(job)

    eval_res = EvaluationResult(
        job_id=saved.id,
        score=92.0,
        eligible=True,
        matched_skills=["TypeScript", "React", "Next.js"],
        missing_skills=[],
        reasoning="High match for frontend stack",
    )
    storage.save_evaluation(eval_res)

    saved_eval = storage.get_evaluation(saved.id)
    assert saved_eval is not None
    assert saved_eval.score == 92.0
    assert saved_eval.matched_skills == ["TypeScript", "React", "Next.js"]

    # Job state should now be ELIGIBLE
    assert storage.get_job(saved.id).state == JobState.ELIGIBLE

    # Save tailored CV
    cv = TailoredCV(
        job_id=saved.id,
        content_markdown="# Tailored CV Content",
        verification_passed=True,
        verification_log=["All facts verified"],
    )
    storage.save_tailored_cv(cv)

    saved_cv = storage.get_tailored_cv(saved.id)
    assert saved_cv is not None
    assert saved_cv.verification_passed is True
    assert storage.get_job(saved.id).state == JobState.TAILORED


def test_rezi_resume_id_roundtrip(tmp_path):
    from job_hunt.models import JobPosting
    from job_hunt.storage import Storage

    storage = Storage(tmp_path / "rezi.db")
    job = JobPosting(
        source="test", title="Backend Engineer", company="Co",
        raw_url="https://example.com/1", canonical_url="https://example.com/1",
        canonical_url_hash="h-rezi-1", role_fingerprint="rf-rezi-1", content_hash="c1",
    )
    saved, is_new = storage.add_job(job)
    assert is_new is True
    assert storage.get_rezi_resume_id(saved.id) is None

    storage.save_rezi_resume_id(saved.id, "rezi-abc-123")
    assert storage.get_rezi_resume_id(saved.id) == "rezi-abc-123"
    assert storage.get_job(saved.id).rezi_resume_id == "rezi-abc-123"


def test_application_answers_roundtrip(tmp_path):
    from job_hunt.models import JobPosting
    from job_hunt.storage import Storage

    storage = Storage(tmp_path / "ans.db")
    job = JobPosting(
        source="test", title="Backend Engineer", company="Co",
        raw_url="https://example.com/9", canonical_url="https://example.com/9",
        canonical_url_hash="h-ans", role_fingerprint="rf-ans", content_hash="c9",
    )
    saved, _ = storage.add_job(job)
    storage.save_answers(
        saved.id,
        {"Are you authorized?": "Yes"},
        confirmation_needed=["Passport number"],
        submitted=False,
    )
    rows = storage.get_answers(saved.id)
    assert len(rows) == 2
    by_q = {r["question"]: r for r in rows}
    assert by_q["Are you authorized?"]["answer"] == "Yes"
    assert by_q["Are you authorized?"]["submitted"] is False
    assert by_q["Passport number"]["needs_confirmation"] is True

    storage.mark_answers_submitted(saved.id)
    assert all(r["submitted"] for r in storage.get_answers(saved.id))


def test_salary_source_and_direct_url_round_trip(storage):
    job = JobPosting(
        source="indeed",
        title="Backend Engineer",
        company="Acme",
        raw_url="https://eg.indeed.com/viewjob?jk=abc123",
        canonical_url="https://eg.indeed.com/viewjob?jk=abc123",
        canonical_url_hash="h-abc123",
        role_fingerprint="rf-abc123",
        content_hash="c-abc123",
        salary_min=100000,
        salary_max=140000,
        salary_currency="EGP",
        salary_source="stated",
        job_url_direct="https://acme.com/jobs/1",
    )
    saved, is_new = storage.add_job(job)
    assert is_new is True
    fetched = storage.get_job(saved.id)
    assert fetched.salary_source == "stated"
    assert fetched.job_url_direct == "https://acme.com/jobs/1"
    assert fetched.salary_min == 100000
    assert fetched.salary_max == 140000
    assert fetched.salary_currency == "EGP"
