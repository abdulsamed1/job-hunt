"""Tests for Rezi resume mapping and orchestrator mirroring (mocked client)."""

import uuid

import pytest
from unittest.mock import AsyncMock

from job_hunt.models import CandidateProfile, Education, Experience, JobPosting, JobState
from job_hunt.rezi import build_rezi_resume_data


@pytest.fixture
def profile():
    return CandidateProfile(
        full_name="Sam Taylor",
        first_name="Sam",
        last_name="Taylor",
        email="sam@example.com",
        phone="+1-555-0144",
        location="Seattle, WA",
        years_of_experience=5,
        verified_skills=["Python", "FastAPI", "PostgreSQL"],
        verified_experiences=[
            Experience(
                company="Amazon",
                title="SDE II",
                start_date="2021",
                end_date="Present",
                location="Seattle, WA",
                bullets=["Built Python APIs.", "Tuned PostgreSQL queries."],
            ),
        ],
        verified_education=[
            Education(institution="UW", degree="B.S.", field_of_study="CS", graduation_year=2019)
        ],
    )


@pytest.fixture
def job():
    return JobPosting(
        id=1,
        source="test",
        title="Backend Engineer",
        company="Acme",
        raw_url="https://example.com/1",
        canonical_url="https://example.com/1",
        canonical_url_hash="h1",
        role_fingerprint="rf1",
        content_hash="c1",
        location="Remote",
        description="Python and FastAPI backend role.",
    )


def test_mapping_shape(profile, job):
    data = build_rezi_resume_data(job, profile)
    assert data["jobTitle"] == "Backend Engineer"
    assert data["jobCompany"] == "Acme"
    assert "Python" in data["jobDescription"]
    assert isinstance(data["data"]["contact"], dict)
    assert data["data"]["contact"]["email"] == "sam@example.com"

    exp = data["data"]["experience"]
    assert len(exp) == 1
    item = next(iter(exp.values()))
    assert item["company"] == "Amazon"
    assert item["hide"] is False
    # UUID keys unique, index sequential
    assert len(exp.keys()) == len({str(k) for k in exp.keys()})
    for k in exp.keys():
        uuid.UUID(str(k))


def test_mapping_contains_only_profile_facts(profile, job):
    data = build_rezi_resume_data(job, profile)
    verified_skills = {s.lower() for s in profile.verified_skills}
    skill_names = []
    skills = data["data"]["skills"]
    items = skills.values() if isinstance(skills, dict) else skills
    for s in items:
        name = s["name"] if isinstance(s, dict) else s
        skill_names.append(name)
    assert skill_names, "expected skills in mapping"
    assert all(n.lower() in verified_skills for n in skill_names)

    companies = {e["company"] for e in data["data"]["experience"].values()}
    assert companies <= {e.company for e in profile.verified_experiences}


def test_mapping_orders_bullets_by_relevance(profile, job):
    data = build_rezi_resume_data(job, profile)
    item = next(iter(data["data"]["experience"].values()))
    assert item["bullets"][0] == "Built Python APIs."


def test_mirror_success_stores_rezi_id(tmp_path, profile, job):
    from job_hunt.orchestrator import PipelineOrchestrator
    from job_hunt.storage import Storage

    orch = PipelineOrchestrator(
        storage=Storage(tmp_path / "m.db"), db_path=str(tmp_path / "m.db"), use_llm=False
    )
    saved, _ = orch.storage.add_job(job)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE)

    mock_client = AsyncMock()
    mock_client.write_resume.return_value = {"id": "rezi-1", "name": "Acme - Backend Engineer"}
    mock_client.read_resume.return_value = {"id": "rezi-1", "name": "Acme - Backend Engineer"}

    count = orch.run_cv_stage(limit=5, use_rezi=True, rezi_client=mock_client, generate_pdf=False)
    assert count >= 1
    mock_client.write_resume.assert_awaited_once()
    # create, never update: no resume_id passed
    _, kwargs = mock_client.write_resume.call_args
    assert "resume_id" not in kwargs
    assert orch.storage.get_rezi_resume_id(saved.id) == "rezi-1"


def test_mirror_failure_falls_back_to_local(tmp_path, profile, job):
    from job_hunt.orchestrator import PipelineOrchestrator
    from job_hunt.rezi import ReziError
    from job_hunt.storage import Storage

    orch = PipelineOrchestrator(
        storage=Storage(tmp_path / "m2.db"), db_path=str(tmp_path / "m2.db"), use_llm=False
    )
    saved, _ = orch.storage.add_job(job)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE)

    mock_client = AsyncMock()
    mock_client.write_resume.side_effect = ReziError("boom")

    count = orch.run_cv_stage(limit=5, use_rezi=True, rezi_client=mock_client, generate_pdf=False)
    assert count >= 1  # local tailor still done
    assert orch.storage.get_rezi_resume_id(saved.id) is None


def test_mirror_skips_when_no_token(tmp_path, job, monkeypatch):
    from job_hunt.orchestrator import PipelineOrchestrator
    from job_hunt.storage import Storage

    monkeypatch.delenv("REZI_MCP_TOKEN", raising=False)
    orch = PipelineOrchestrator(
        storage=Storage(tmp_path / "m3.db"), db_path=str(tmp_path / "m3.db"), use_llm=False
    )
    saved, _ = orch.storage.add_job(job)
    assert orch._mirror_to_rezi(saved, rezi_client=None) == 0
