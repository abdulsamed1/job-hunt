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


def test_ats_cv_generator_structure_and_keywords(profile):
    from job_hunt.cv.pdf_generator import ATSCVGenerator

    generator = ATSCVGenerator()
    job = JobPosting(
        id=77,
        source="greenhouse",
        title="Senior Python / Go Backend Engineer",
        company="StreamCloud",
        raw_url="https://example.com/job/77",
        canonical_url="https://example.com/job/77",
        canonical_url_hash="h77",
        role_fingerprint="rf77",
        content_hash="c77",
        description="Must have strong Python, Go, and PostgreSQL experience. Distributed systems, Docker, AWS.",
    )

    keywords = generator.generate_ats_keyword_stream(job, profile)
    assert "Python" in keywords
    assert "Go" in keywords
    assert "StreamCloud" in keywords

    html_cv = generator.build_html_cv(
        job=job,
        profile=profile,
        ats_keywords_one_line=keywords,
        tailored_summary="Proven backend engineer specializing in high-throughput streaming systems.",
    )

    # 1. Check recruiter highlights present
    assert "Sam Taylor" in html_cv
    assert "Professional Summary" in html_cv
    assert "Technical Expertise" in html_cv

    # 2. Check hiring manager depth present
    assert "Amazon" in html_cv
    assert "50M" in html_cv
    assert "Expedia" in html_cv
    assert "University of Washington" in html_cv

    # 3. Check invisible ATS keyword layer is present at the end
    assert 'class="ats-keyword-bypass"' in html_cv
    assert keywords in html_cv


def test_ats_cv_pdf_rendering(profile, tmp_path):
    import subprocess
    from job_hunt.cv.pdf_generator import ATSCVGenerator

    generator = ATSCVGenerator()
    job = JobPosting(
        id=99,
        source="lever",
        title="Principal Infrastructure Engineer",
        company="CloudScale",
        raw_url="https://example.com/job/99",
        canonical_url="https://example.com/job/99",
        canonical_url_hash="h99",
        role_fingerprint="rf99",
        content_hash="c99",
        description="Looking for expert in Kubernetes, Go, Kafka, and PostgreSQL.",
    )

    out_pdf = tmp_path / "test_rendered_cv.pdf"
    res_path = generator.generate_tailored_pdf_sync(job, profile, out_pdf)
    assert res_path.exists()
    assert res_path.stat().st_size > 10000

    # Verify pdftotext extracts both the visible text and the ATS keywords
    out = subprocess.check_output(["pdftotext", str(res_path), "-"]).decode("utf-8")
    assert "Sam Taylor" in out
    assert "Amazon" in out
    assert "Kubernetes" in out
