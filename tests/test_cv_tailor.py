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


def test_stamp_job_description_on_master_pdf(tmp_path):
    from pathlib import Path
    import subprocess
    from job_hunt.cv.pdf_generator import ATSCVGenerator
    from job_hunt.models import JobPosting

    master_pdf = Path("Abdulsamed_Hamdy.pdf")
    if not master_pdf.exists():
        pytest.skip("Abdulsamed_Hamdy.pdf not present")

    generator = ATSCVGenerator(master_pdf_path=master_pdf)
    job = JobPosting(
        id=80,
        source="greenhouse",
        title="Staff Backend Engineer",
        company="GlobalScale Inc",
        raw_url="https://example.com/job/80",
        canonical_url="https://example.com/job/80",
        canonical_url_hash="h80",
        role_fingerprint="rf80",
        content_hash="c80",
        location="Remote Worldwide",
        description="Looking for distributed systems expert in Rust, Python, SQLite, PostgreSQL, and high-throughput microservices.",
    )

    out_pdf = tmp_path / "abdulsamed_tailored.pdf"
    res_path = generator.stamp_job_description_to_pdf(master_pdf, job, out_pdf)
    assert res_path.exists()
    assert res_path.stat().st_size > 50000

    out_text = subprocess.check_output(["pdftotext", str(res_path), "-"]).decode("utf-8")
    # Original resume text must be 100% preserved
    assert "Abdulsamed Hamdy" in out_text
    assert "Eagles" in out_text
    assert "app.eagles-eg.online" in out_text
    assert "Visa Appointment Automation" in out_text
    assert "AI Skills Aggregator" in out_text
    # Stamped 1pt job description must be extracted by ATS
    assert "GlobalScale Inc" in out_text
    assert "Staff Backend Engineer" in out_text
    assert "distributed systems expert in Rust" in out_text


def test_fact_verification_catches_unverified_project(profile):
    tailor = CVTailor()
    bad_cv = tailor.build_master_cv(profile) + "\n### SecretMoonshot\n*Stealth project*\n- Did secret things."
    passed, violations = tailor.verify_cv_facts(bad_cv, profile)
    assert passed is False
    assert any("SecretMoonshot" in v for v in violations)


def test_fact_verification_catches_date_mismatch(profile):
    tailor = CVTailor()
    bad_cv = tailor.build_master_cv(profile).replace("2021 - Present", "2021 - 2023")
    passed, violations = tailor.verify_cv_facts(bad_cv, profile)
    assert passed is False
    assert any("2023" in v for v in violations)


def test_keyword_coverage_table_statuses(profile):
    tailor = CVTailor()
    job = JobPosting(
        source="test", title="Kubernetes Engineer", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        description="Requires Kubernetes and Terraform. Go experience preferred.",
    )
    cv = tailor.build_master_cv(profile)
    table = tailor.keyword_coverage_table(job, cv, profile)
    by_kw = {row["keyword"]: row for row in table}
    assert by_kw["kubernetes"]["status"] == "covered"
    assert by_kw["kubernetes"]["priority"] == "required"
    assert by_kw["terraform"]["status"] == "missing (gap)"
    assert by_kw["go"]["status"] in ("covered", "missing (have it)")
    # No stuffed gaps: every covered keyword is verbatim in the CV text
    for row in table:
        if row["status"] == "covered":
            assert row["keyword"].lower() in cv.lower()


def test_trim_bullets_keeps_keyword_rich_lines():
    tailor = CVTailor()
    bullets = [
        "Attended weekly team standup meetings.",
        "Built Kubernetes operator in Go serving production traffic.",
        "Updated internal wiki documentation pages.",
    ]
    kept, cut = tailor.trim_bullets(bullets, {"kubernetes", "go"}, keep=1)
    assert kept == ["Built Kubernetes operator in Go serving production traffic."]
    assert len(cut) == 2


def _write_pdf(path, text: str):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_verify_pdf_text_layer_passes_clean_pdf(profile, tmp_path):
    from job_hunt.cv.pdf_generator import verify_pdf_text_layer

    pdf = tmp_path / "clean.pdf"
    _write_pdf(pdf, f"Sam Taylor\nsam@example.com\n+1-555-0144\nAmazon 2021 - Present\nPython, Go")
    passed, checks = verify_pdf_text_layer(str(pdf), profile)
    assert passed is True, checks


def test_verify_pdf_text_layer_catches_missing_email(profile, tmp_path):
    from job_hunt.cv.pdf_generator import verify_pdf_text_layer

    pdf = tmp_path / "noemail.pdf"
    _write_pdf(pdf, "Sam Taylor\n+1-555-0144\nAmazon 2021 - Present")
    passed, checks = verify_pdf_text_layer(str(pdf), profile)
    assert passed is False
    assert any("email" in c.lower() for c in checks)


def test_verify_pdf_text_layer_catches_unicode_dash_dates(profile, tmp_path):
    from job_hunt.cv.pdf_generator import verify_pdf_text_layer

    pdf = tmp_path / "dash.pdf"
    _write_pdf(pdf, "Sam Taylor\nsam@example.com\n+1-555-0144\nAmazon 2016–2024")
    passed, checks = verify_pdf_text_layer(str(pdf), profile)
    assert passed is False
    assert any("dash" in c.lower() for c in checks)


def test_generate_tailored_cv_applies_bullet_budget(profile):
    tailor = CVTailor(use_llm=False)
    job = JobPosting(
        source="test", title="Kafka Engineer", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        description="Deep Kafka and Go experience required.",
    )
    tailored = tailor.generate_tailored_cv(job, profile, generate_pdf=False, max_bullets_per_role=1)
    assert "processing 50M events" in tailored.content_markdown
    assert "Improved query latency by 35%" not in tailored.content_markdown


def test_verify_rendered_pdf_records_failures_in_log(profile, tmp_path):
    tailor = CVTailor(use_llm=False)
    bad_pdf = tmp_path / "bad.pdf"
    _write_pdf(bad_pdf, "Sam Taylor\n+1-555-0144\nAmazon 2021 - Present")
    log: list = []
    tailor._verify_rendered_pdf(str(bad_pdf), profile, log)
    assert any("ATS text-layer warnings" in entry for entry in log)

    good_pdf = tmp_path / "good.pdf"
    _write_pdf(good_pdf, "Sam Taylor\nsam@example.com\n+1-555-0144\nAmazon 2021 - Present")
    log2: list = []
    tailor._verify_rendered_pdf(str(good_pdf), profile, log2)
    assert log2 == []
