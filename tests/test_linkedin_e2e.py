"""LinkedIn is discovery-only: the browser engine refuses to drive the account.

The Easy Apply / External Apply flows still exist in the engine for a future
explicit manual mode, but no automated path may reach them: the application
stage filters LinkedIn rows out before the engine is called, and the engine
itself refuses a LinkedIn posting unconditionally (owner decision 2026-10-21 —
the account is never used by automation).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.models import JobPosting, CandidateProfile, JobState, ApplicationRecord


def _make_job(
    title="Senior Python Developer",
    company="TechCorp",
    raw_url="https://www.linkedin.com/jobs/view/test-job-123",
    application_type=None,
    external_id=None,
    canonical_url_hash="htest123",
    role_fingerprint="techcorp:senior_python_developer:remote",
    content_hash="ctest123",
):
    return JobPosting(
        source="linkedin",
        title=title,
        company=company,
        raw_url=raw_url,
        canonical_url=raw_url,
        canonical_url_hash=canonical_url_hash,
        role_fingerprint=role_fingerprint,
        content_hash=content_hash,
        application_type=application_type,
        external_id=external_id,
        location="Remote",
    )


def _make_profile():
    return CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdul@example.com",
        phone="+20-100-000-0000",
        location="Cairo, Egypt",
        work_authorization="Authorized to work in Egypt, Remote Worldwide",
        open_to_remote=True,
        verified_skills=["Python", "FastAPI", "PostgreSQL"],
        years_of_experience=5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("application_type", ["easy_apply", "external_url", None])
async def test_engine_refuses_linkedin_in_any_mode(application_type):
    """No browser session is launched for a LinkedIn posting — even with a
    login session present and every approval flag set."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        engine = BrowserApplicationEngine(headless=True)
        record = await engine.fill_and_submit(
            _make_job(application_type=application_type),
            _make_profile(),
            dry_run=True,
            linkedin_approved=True,
        )
        mock_pw.assert_not_called()

    assert record.state == JobState.FAILED
    assert "discovery-only" in record.error_message


@pytest.mark.asyncio
async def test_engine_refuses_a_linkedin_url_with_no_source():
    """The URL alone is enough: a spoofed source must not get the account used."""
    engine = BrowserApplicationEngine(headless=True)
    job = _make_job(application_type="easy_apply")
    job.source = "greenhouse:acme"
    record = await engine.fill_and_submit(job, _make_profile(), dry_run=True)
    assert record.state == JobState.FAILED
    assert "discovery-only" in record.error_message


def test_application_type_passed_through_browser():
    """application_type stays readable on the posting for future manual modes."""
    job_easy = _make_job(application_type="easy_apply")
    job_external = _make_job(application_type="external_url")
    job_none = _make_job(application_type=None)

    assert job_easy.application_type == "easy_apply"
    assert job_external.application_type == "external_url"
    assert job_none.application_type is None


@pytest.mark.asyncio
async def test_easy_apply_panel_detection():
    engine = BrowserApplicationEngine(headless=True)

    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    result = await engine._detect_linkedin_easy_apply_panel(mock_page)
    assert result is False


def test_easy_apply_selectors_defined():
    from job_hunt.automation.browser import LINKEDIN_EASY_APPLY_SELECTORS
    from job_hunt.automation.browser import LINKEDIN_SKILL_TAG_SELECTORS
    from job_hunt.automation.browser import CLOUDFLARE_TURNSTILE_SELECTORS

    assert "button:has-text('Easy Apply')" in LINKEDIN_EASY_APPLY_SELECTORS
    assert len(LINKEDIN_SKILL_TAG_SELECTORS) > 0
    assert len(CLOUDFLARE_TURNSTILE_SELECTORS) > 0


def test_a_non_linkedin_job_is_not_blocked_by_the_linkedin_rule():
    """Guard against over-blocking: an ATS posting is untouched by the rule."""
    from job_hunt import settings

    ats_job = JobPosting(
        source="greenhouse:acme",
        title="Backend Engineer",
        company="Acme",
        raw_url="https://boards.greenhouse.io/acme/jobs/1",
        canonical_url="https://boards.greenhouse.io/acme/jobs/1",
        canonical_url_hash="hats1",
        role_fingerprint="rf-ats1",
        content_hash="cats1",
        location="Remote",
    )
    assert settings.is_linkedin_job(ats_job) is False
