"""Tests for doctor diagnostics, outcome archiving, calibration, tier classifier."""

import pytest


def test_doctor_structure_and_failures(tmp_path):
    from job_hunt.doctor import run_diagnostics

    report = run_diagnostics(
        db_path=str(tmp_path / "d.db"),
        sources_path=str(tmp_path / "missing.yaml"),
        profile_path=str(tmp_path / "missing.json"),
    )
    assert isinstance(report["checks"], list) and report["checks"]
    for check in report["checks"]:
        assert {"name", "ok", "detail"} <= set(check.keys())
    assert report["ok"] is False  # missing sources + profile must fail
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["sources_config"]["ok"] is False
    assert by_name["candidate_profile"]["ok"] is False


def test_outcome_roundtrip(tmp_path):
    from job_hunt.models import JobPosting
    from job_hunt.storage import Storage

    storage = Storage(tmp_path / "o.db")
    job = JobPosting(
        source="test", title="Backend Engineer", company="Co",
        raw_url="https://example.com/o", canonical_url="https://example.com/o",
        canonical_url_hash="h-o", role_fingerprint="rf-o", content_hash="c-o",
    )
    saved, _ = storage.add_job(job)
    storage.record_outcome(saved.id, "interview", notes="screening call booked")
    rows = storage.get_outcomes(saved.id)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "interview"
    assert "screening" in rows[0]["notes"]


def test_calibration_bands(tmp_path):
    from job_hunt.models import EvaluationResult, JobPosting, JobState
    from job_hunt.storage import Storage

    storage = Storage(tmp_path / "c.db")
    for i, score in enumerate((80.0, 82.0, 50.0, 30.0)):
        job = JobPosting(
            source="test", title=f"R{i}", company="Co",
            raw_url=f"https://example.com/c{i}", canonical_url=f"https://example.com/c{i}",
            canonical_url_hash=f"h-c{i}", role_fingerprint=f"rf-c{i}", content_hash="c",
        )
        saved, _ = storage.add_job(job)
        storage.save_evaluation(EvaluationResult(
            job_id=saved.id, score=score, eligible=score >= 70.0,
            reasoning="t", pre_filtered=False,
        ))
        # Eligible ones got interviews; weak ones rejected
        if score >= 70:
            storage.update_job_state(saved.id, JobState.ELIGIBLE)
            storage.record_outcome(saved.id, "interview")
        else:
            storage.update_job_state(saved.id, JobState.REJECTED, force=True)

    report = storage.calibration_report()
    band75 = next(b for b in report["bands"] if b["band"] == "75-100")
    assert band75["evaluated"] == 2
    assert band75["interviews"] == 2
    assert report["total_evaluated"] == 4


def test_tier_classifier():
    from job_hunt.evaluation.engine import classify_tier

    assert classify_tier("Software Engineering Intern", "") == "intern"
    assert classify_tier("Junior Backend Engineer", "") == "entry"
    assert classify_tier("Backend Engineer", "") == "mid"
    assert classify_tier("Senior Backend Engineer", "") == "senior"
    assert classify_tier("Staff Backend Engineer", "") == "lead"
    assert classify_tier("Engineering Manager", "") == "lead"
    assert classify_tier("Backend Engineer", "Requires 8+ years of experience") == "senior"
