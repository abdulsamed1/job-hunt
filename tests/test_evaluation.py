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


def _swe_job2(description: str, company: str = "Co") -> JobPosting:
    return JobPosting(
        source="test", title="Backend Engineer", company=company,
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote", description=description,
    )


def test_sponsorship_unavailable_rejects_when_needed(engine, candidate):
    candidate.sponsorship_required = True
    job = _swe_job2("Python backend role. We cannot provide sponsorship for this position.")
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is True
    assert res.eligible is False
    assert "sponsor" in (res.pre_filter_reason or "").lower()


def test_sponsorship_offer_passes_gate_when_needed(engine, candidate):
    candidate.sponsorship_required = True
    job = _swe_job2("Python backend role. We sponsor visas for exceptional candidates.")
    verdict, quote = engine.check_sponsorship(job, candidate)
    assert verdict == "PASS"


def test_sponsorship_irrelevant_when_not_needed(engine, candidate):
    candidate.sponsorship_required = False
    job = _swe_job2("Python backend role. No sponsorship available.")
    res = engine.evaluate(job, candidate)
    assert res.pre_filtered is False


def test_blocked_company_and_keywords(engine, candidate):
    candidate.blocked_companies = ["Evilterra"]
    candidate.blocked_keywords = ["crypto casino", "adult"]
    candidate.preferred_keywords = ["Egypt"]

    job = _swe_job2("Python backend role.", company="Evilterra")
    assert engine.evaluate(job, candidate).pre_filtered is True

    job2 = _swe_job2("Python backend role at our crypto casino platform.")
    assert engine.evaluate(job2, candidate).pre_filtered is True

    # Preferred exception wins over bad word
    job3 = _swe_job2("Python backend role in Egypt, not a crypto casino.")
    res3 = engine.evaluate(job3, candidate)
    assert res3.pre_filtered is False


def test_disability_targeted_program_is_pre_filtered(engine, candidate):
    job = _swe_job(
        "Technical support engineer intern for people with disabilities. "
        "Ignite Program. Python and customer support."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    assert result.eligible is False
    assert "disabilit" in (result.pre_filter_reason or result.reasoning).lower()


def test_generic_accessibility_language_is_not_disability_targeting(engine, candidate):
    job = _swe_job(
        "Build Python services. We are an equal opportunity employer and provide "
        "reasonable accommodation for applicants with disabilities."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is False


def test_disability_encouragement_into_program_fails(engine, candidate):
    job = _swe_job(
        "We encourage people with disabilities to join our hiring program. "
        "Python backend role."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    assert "disabilit" in (result.pre_filter_reason or result.reasoning).lower()


def test_disability_encouragement_only_sentence_passes(engine, candidate):
    job = _swe_job(
        "Individuals with disabilities are encouraged to apply. "
        "Python and FastAPI backend role."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is False


def test_disability_title_mention_fails(engine, candidate):
    job = _swe_job("Build Python services.", title="Disability Support Engineer")
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    assert "disabilit" in (result.pre_filter_reason or result.reasoning).lower()


def test_disability_nondiscrimination_in_hiring_passes(engine, candidate):
    job = _swe_job(
        "We do not discriminate in hiring on the basis of disability. "
        "Python backend role."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is False


def test_disability_decimal_does_not_split_sentence(engine, candidate):
    # A bare "." splitter would break at "3.5", hide the program noun from the
    # encouragement sentence and wrongly suppress the fail.
    job = _swe_job(
        "Job pays 3.5x above market and we encourage adults with disabilities "
        "to apply for our internship program. Python backend role."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    assert "disabilit" in (result.pre_filter_reason or result.reasoning).lower()


def test_disability_late_mention_quote_keeps_phrase(engine, candidate):
    filler = "We build reliable systems for teams worldwide " * 8
    job = _swe_job(
        filler + "We support engineers with disabilities through our certified program."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    reason = (result.pre_filter_reason or result.reasoning).lower()
    assert "disabilit" in reason


def test_disability_gate_shared_fixtures(engine, candidate):
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "disability_gate_cases.json"
    cases = json.loads(fixture.read_text())
    assert cases, "shared disability gate fixtures must not be empty"
    for case in cases:
        job = _swe_job(case["description"], title=case["title"])
        verdict, quote = engine.check_disability_targeting_gate(job)
        assert verdict == case["verdict"], f"{case['name']}: expected {case['verdict']}, got {verdict}"
        if case["verdict"] == "FAIL":
            assert quote and "disabilit" in quote.lower(), f"{case['name']}: quote must carry the matched phrase"


def test_disability_distant_eeo_passes(engine, candidate):
    gap = "Backend systems work. " * 20  # ~440 chars between mention and EEO
    job = _swe_job(
        "Engineering role for people with disabilities. " + gap +
        "We are an equal opportunity employer."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is False


def test_disability_bare_program_fails(engine, candidate):
    job = _swe_job(
        "Our disability inclusion program supports engineers. Python backend role."
    )
    result = engine.evaluate(job, candidate)
    assert result.pre_filtered is True
    assert result.eligible is False
    assert "disabilit" in (result.pre_filter_reason or result.reasoning).lower()


@pytest.mark.parametrize("title", [
    "Senior Manager, Application Security",
    "Engineering Team Lead, Platform",
    "Director of Software Engineering - Distributed Systems",
    "Head of Engineering",
    "Account Executive, EMEA",
    "Marketing Manager",
    # A seniority marker never makes a revenue title a software role: these
    # are sales and marketing jobs, whatever "Senior" says.
    "Senior Sales Engineer",
    "Staff Sales Engineer",
    "Senior Marketing Engineer",
    "Senior Customer Success Engineer",
])
def test_pre_filter_rejects_non_ic_roles(engine, candidate, title):
    job = JobPosting(
        source="test", title=title, company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is False


@pytest.mark.parametrize("title", ["Lead Software Engineer", "Staff Engineer, Platform",
                                   "Staff Engineers, Platform", "Forward Deployed Engineer",
                                   "Senior Software Engineer"])
def test_pre_filter_keeps_ic_titles(engine, candidate, title):
    job = JobPosting(
        source="test", title=title, company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is True


def test_pre_filter_keeps_an_ic_title_that_matches_no_reject_family(engine, candidate):
    # The IC allow-list stands in for the positive SWE patterns: an individual
    # contributor title needs no technology in it. It never stands in for the
    # reject families (see the sales/marketing titles above).
    job = JobPosting(
        source="test", title="Staff Engineer, Payments Tools", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is True


@pytest.mark.parametrize("title", ["Software Engineering Internship",
                                   "Backend Engineering Internship"])
def test_pre_filter_rejects_internship_titles(engine, candidate, title):
    # "Internship" is the same out-of-scope thing as "Intern": the Worker
    # rejects both spellings too (worker/src/lib/roles.ts).
    job = JobPosting(
        source="test", title=title, company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is False


def test_pre_filter_names_the_reject_rule_the_worker_names(engine, candidate):
    # The reason carries the family and the slug, so both runtimes call the
    # same rule by the same name (the Worker stores "role gate: revenue:sales").
    job = JobPosting(
        source="test", title="Senior Sales Engineer", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is False
    assert "revenue:sales" in (reason or "")


def test_pre_filter_accepts_fullstack_titles(engine, candidate):
    # "FullStack" and "Full-Stack" are "full stack" written as one word or with
    # a hyphen: the Worker accepts every spelling, so this runtime must too.
    for title in ("FullStack Engineer", "Full-Stack Engineer", "Full Stack Engineer"):
        job = JobPosting(
            source="test", title=title, company="Co",
            raw_url="https://example.com", canonical_url="https://example.com",
            canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
            location="Remote",
        )
        passed, reason = engine.pre_filter(job, candidate)
        assert passed is True, title


@pytest.mark.parametrize("title", ["Site Reliability", "Machine Learning"])
def test_pre_filter_accepts_standalone_swe_phrases(engine, candidate, title):
    job = JobPosting(
        source="test", title=title, company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed, reason = engine.pre_filter(job, candidate)
    assert passed is True


def test_pre_filter_crm_toggle(engine, candidate):
    # Default (on): CRM role rejected as CRM.
    job = JobPosting(
        source="test", title="Senior Salesforce Developer", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    passed_on, reason_on = engine.pre_filter(job, candidate)
    assert passed_on is False
    assert "crm" in (reason_on or "").lower() or "salesforce" in (reason_on or "").lower()

    # Toggle off: no longer rejected for CRM. The title names the build role
    # ("developer"), so the role gate now admits it — it is a software role,
    # not a CRM reject (the Worker agrees, see worker/test/roles.test.ts).
    profile_off = candidate.model_copy(update={"reject_crm_roles": False})
    passed_off, reason_off = engine.pre_filter(job, profile_off)
    assert passed_off is True
    assert "crm" not in (reason_off or "").lower()
    assert "salesforce" not in (reason_off or "").lower()

    # A CRM role that is otherwise SWE-shaped is admitted when the toggle is off.
    job2 = JobPosting(
        source="test", title="Salesforce Developer, Python", company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )
    assert engine.pre_filter(job2, candidate)[0] is False
    assert engine.pre_filter(job2, profile_off)[0] is True


# A domain word (accountant, finance, legal, medical) names a field of work, not
# a build role: it never disqualifies a title that names an engineering build
# role. These are software jobs.
DOMAIN_WORD_BUILD_ROLE_TITLES = [
    "Full Stack Engineer - Finance",
    "Staff Engineer, Revenue & Finance Automation",
    "Forward Deployed Engineer, Finance [Office of the CTO]",
    "AI Engineer, Legal & Compliance",
    "Senior Data Engineer II, Finance",
    "Finance Systems Engineer, Finance and Strategy",
    "Medical Engineer",
]

DOMAIN_WORD_ALONE_TITLES = [
    ("Senior Accountant", "non-engineering:accountant"),
    ("Legal Counsel", "non-engineering:legal"),
    ("Finance Analyst", "non-engineering:finance"),
    ("Registered Nurse - ICU", "non-engineering:medical"),
]


def _role_job(title: str) -> JobPosting:
    return JobPosting(
        source="test", title=title, company="Co",
        raw_url="https://example.com", canonical_url="https://example.com",
        canonical_url_hash="h", role_fingerprint="rf", content_hash="c",
        location="Remote",
    )


def test_pre_filter_keeps_a_domain_word_with_an_engineering_role(engine, candidate):
    # "Full Stack Engineer - Finance" is a software job: the domain word names
    # the team, not the role. The Worker classifies the same titles as
    # software (see worker/test/roles.test.ts and the shared fixtures).
    for title in DOMAIN_WORD_BUILD_ROLE_TITLES:
        passed, reason = engine.pre_filter(_role_job(title), candidate)
        assert passed is True, f"{title}: {reason}"


def test_pre_filter_rejects_a_domain_word_without_an_engineering_role(engine, candidate):
    # Without a build role the domain words still reject, and they are named by
    # the same rule the Worker names.
    for title, rule in DOMAIN_WORD_ALONE_TITLES:
        passed, reason = engine.pre_filter(_role_job(title), candidate)
        assert passed is False, title
        assert rule in (reason or ""), f"{title}: {reason}"


def test_pre_filter_leaves_management_and_revenue_titles_alone(engine, candidate):
    # A build role does not rescue a people-management or a revenue title.
    for title, rule in (
        ("Compliance Manager, BSA/AML Program", "people-management:manager"),
        ("Engineering Manager, Data Feeds", "people-management:manager"),
        ("Sales Engineer", "revenue:sales"),
    ):
        passed, reason = engine.pre_filter(_role_job(title), candidate)
        assert passed is False, title
        assert rule in (reason or ""), f"{title}: {reason}"


def test_role_gate_shared_fixtures(engine, candidate):
    """Both runtimes read the same fixture list, so the role gate cannot drift.

    "software" means the title is admitted by the role gate; "reject" means it
    is rejected, by the rule both runtimes name.
    """
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "role_gate_cases.json"
    cases = json.loads(fixture.read_text())
    assert cases, "shared role gate fixtures must not be empty"
    for case in cases:
        passed, reason = engine.pre_filter(_role_job(case["title"]), candidate)
        if case["verdict"] == "software":
            assert passed is True, f"{case['name']}: {reason}"
        else:
            assert passed is False, case["name"]
            assert case["rule"] in (reason or ""), f"{case['name']}: {reason}"
