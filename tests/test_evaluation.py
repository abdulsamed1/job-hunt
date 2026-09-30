"""Unit tests for deterministic pre-filtering and structured evaluation scoring."""

import pytest
from job_hunt.evaluation.engine import EvaluationEngine
from job_hunt.models import CandidateProfile, JobPosting


@pytest.fixture
def candidate():
    return CandidateProfile(
        full_name="Morgan Reed",
        first_name="Morgan",
        last_name="Reed",
        email="morgan@example.com",
        phone="+1234567890",
        location="San Francisco, CA, USA",
        years_of_experience=5,
        verified_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Redis"],
    )


@pytest.fixture
def engine():
    return EvaluationEngine(min_score_threshold=70.0)


def test_pre_filter_negative_titles(engine, candidate):
    negative_titles = [
        "Product Marketing Manager",
        "Senior Recruiter - Technical",
        "Account Executive, Mid-Market",
        "Legal Counsel, Commercial",
        "Registered Nurse - ICU",
        "Software Engineering Intern (Summer 2026)",
    ]

    for title in negative_titles:
        job = JobPosting(
            source="test",
            title=title,
            company="Co",
            raw_url="https://example.com",
            canonical_url="https://example.com",
            canonical_url_hash="h",
            role_fingerprint="rf",
            content_hash="c",
        )
        passed, reason = engine.pre_filter(job, candidate)
        assert passed is False
        eval_res = engine.evaluate(job, candidate)
        assert eval_res.pre_filtered is True
        assert eval_res.eligible is False


def test_pre_filter_security_clearance(engine, candidate):
    job = JobPosting(
        source="test",
        title="Software Engineer, Defense Systems",
        company="DefenseCo",
        raw_url="https://example.com",
        canonical_url="https://example.com",
        canonical_url_hash="h",
        role_fingerprint="rf",
        content_hash="c",
        description="Must hold active Top Secret / TS/SCI clearance with Polygraph.",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is False
    assert "clearance" in reason.lower()


def test_structured_evaluation_scoring(engine, candidate):
    job = JobPosting(
        source="greenhouse",
        title="Senior Software Engineer, Core Services",
        company="FintechCo",
        raw_url="https://example.com/job/1",
        canonical_url="https://example.com/job/1",
        canonical_url_hash="h1",
        role_fingerprint="rf1",
        content_hash="c1",
        location="Remote (US)",
        description="Looking for a Senior Software Engineer with Python, FastAPI, Docker, and PostgreSQL expertise. 4+ years of experience required.",
    )

    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is False
    assert res.eligible is True
    assert res.score >= 70.0
    assert "Python" in res.matched_skills
    assert "FastAPI" in res.matched_skills
    assert "Docker" in res.matched_skills
    assert "PostgreSQL" in res.matched_skills
    assert len(res.reasoning) > 0


def test_low_fit_evaluation_rejected(engine, candidate):
    job = JobPosting(
        source="lever",
        title="Senior Embedded Systems Engineer",
        company="HardwareCo",
        raw_url="https://example.com/job/2",
        canonical_url="https://example.com/job/2",
        canonical_url_hash="h2",
        role_fingerprint="rf2",
        content_hash="c2",
        location="In-Office, Tokyo, Japan",
        description="Requires 10+ years of C, C++, Assembly, and RTOS experience.",
    )

    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is False
    assert res.eligible is False
    assert res.score < 70.0


def _swe_job(description: str, title: str = "Senior Backend Engineer") -> JobPosting:
    return JobPosting(
        source="test",
        title=title,
        company="Co",
        raw_url="https://example.com",
        canonical_url="https://example.com",
        canonical_url_hash="h",
        role_fingerprint="rf",
        content_hash="c",
        location="Remote",
        description=description,
    )


def test_eligibility_gate_blocks_citizenship_requirement(engine, candidate):
    job = _swe_job("Applicants must be citizens of Norway to apply. Python, FastAPI.")
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is True
    assert res.eligible is False
    assert res.score == 0.0
    assert "citizens of norway" in res.reasoning.lower()


def test_eligibility_gate_passes_sponsorship(engine, candidate):
    job = _swe_job("We sponsor visa applications. International applicants welcome. Python, FastAPI.")
    verdict, _ = engine.check_eligibility_gate(job)
    assert verdict == "PASS"


def test_language_gate_blocks_undeclared_language(engine, candidate):
    candidate.languages = ["English", "Arabic"]
    job = _swe_job("You must communicate with the Warsaw team in Polish. Fluent Polish required. Python.")
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is True
    assert res.eligible is False
    assert "polish" in res.reasoning.lower()


def test_language_gate_passes_declared_language(engine, candidate):
    candidate.languages = ["English", "Danish"]
    job = _swe_job("Danish required as working language. Python, FastAPI, Docker, AWS.")
    verdict, _ = engine.check_language_gate(job, candidate)
    assert verdict == "PASS"


def test_language_gate_unverified_without_profile_languages(engine, candidate):
    candidate.languages = []
    job = _swe_job("Fluent German required. Python, FastAPI.")
    verdict, _ = engine.check_language_gate(job, candidate)
    assert verdict == "UNVERIFIED"
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is False


def test_gates_do_not_block_ordinary_posting(engine, candidate):
    job = _swe_job("Build Python backends with FastAPI, Docker, AWS, PostgreSQL, Redis.")
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is False


@pytest.mark.asyncio
async def test_async_gates_block_before_scoring(engine, candidate):
    gated = _swe_job("Applicants must be citizens of Norway to apply. Python, FastAPI.")
    res = await engine.evaluate_async(gated, candidate)
    assert res.pre_filtered is True
    assert res.eligible is False
    assert res.score == 0.0
    assert "citizens of norway" in res.reasoning.lower()

    candidate.languages = ["English"]
    lang_gated = _swe_job("Fluent Polish required. Python, FastAPI.")
    res2 = await engine.evaluate_async(lang_gated, candidate)
    assert res2.pre_filtered is True
    assert res2.eligible is False
    assert "polish" in res2.reasoning.lower()
