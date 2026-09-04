"""Unit tests for fact-preserving CV tailoring and anti-hallucination verification."""

import pytest
from job_hunt.cv.tailor import CVTailor
from job_hunt.models import CandidateProfile, Education, Experience, JobPosting, Project


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
        verified_skills=["Python", "Go", "PostgreSQL", "Kafka", "Kubernetes", "AWS"],
        allowed_metrics=["35%", "50M", "99.95%"],
        verified_experiences=[
            Experience(
                company="Amazon",
                title="Software Development Engineer II",
                start_date="2021",
                end_date="Present",
                location="Seattle, WA",
                bullets=[
                    "Built streaming ingestion service with Go and Kafka processing 50M events daily.",
                    "Improved query latency by 35% through PostgreSQL partitioning.",
                ],
                technologies=["Go", "Kafka", "PostgreSQL"],
            ),
            Experience(
                company="Expedia",
                title="Software Engineer",
                start_date="2019",
                end_date="2021",
                location="Seattle, WA",
                bullets=[
                    "Maintained Python microservices deployed on Kubernetes with 99.95% reliability.",
                ],
                technologies=["Python", "Kubernetes", "AWS"],
            ),
        ],
        verified_education=[
            Education(
                institution="University of Washington",
                degree="B.S.",
                field_of_study="Computer Science",
                graduation_year=2019,
            )
        ],
    )


def test_fact_verification_gate_passes_verified_content(profile):
    tailor = CVTailor()
    master_cv = tailor.build_master_cv(profile)
    passed, violations = tailor.verify_cv_facts(master_cv, profile)
    assert passed is True
    assert len(violations) == 0


def test_fact_verification_gate_catches_hallucinated_metrics(profile):
    tailor = CVTailor()
    # Injected unverified claim "reduced latency by 85%"
    bad_cv = tailor.build_master_cv(profile) + "\n- Reduced latency by 85% and increased profits by 400%"
    passed, violations = tailor.verify_cv_facts(bad_cv, profile)
    assert passed is False
    assert any("85" in v or "400" in v for v in violations)


def test_generate_tailored_cv_reorders_relevant_bullets(profile):
    tailor = CVTailor()
    job = JobPosting(
        source="lever",
        title="Senior Go / Kafka Engineer",
        company="StreamingCo",
        raw_url="https://example.com/job/streaming",
        canonical_url="https://example.com/job/streaming",
        canonical_url_hash="h1",
        role_fingerprint="rf1",
        content_hash="c1",
        description="We need an expert in Go and Kafka to scale our distributed streaming infrastructure.",
    )

    cv = tailor.generate_tailored_cv(job, profile)
    assert cv.verification_passed is True
    assert "Amazon" in cv.content_markdown
    assert "Kafka" in cv.content_markdown
    assert "University of Washington" in cv.content_markdown


def test_tailored_cv_fallback_on_unverified_content(profile):
    tailor = CVTailor()
    job = JobPosting(
        source="test",
        title="SWE",
        company="Co",
        raw_url="https://example.com",
        canonical_url="https://example.com",
        canonical_url_hash="h",
        role_fingerprint="rf",
        content_hash="c",
    )
    # Even in edge cases, generator guarantees verification passed (falling back to master if needed)
    cv = tailor.generate_tailored_cv(job, profile)
    assert cv.verification_passed is True
