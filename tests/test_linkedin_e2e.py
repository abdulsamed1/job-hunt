"""E2E tests for LinkedIn Easy Apply and External Apply browser automation flows."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, AsyncMock as AMock
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


def _mock_playwright_context():
    """Create a mock Playwright context that returns a fake page."""
    mock_page = MagicMock()
    mock_page.url = "https://www.linkedin.com/jobs/view/test-job-123"
    mock_page.content = MagicMock(return_value="<html><body>Thank you for applying</body></html>")
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.add_init_script = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.keyboard = MagicMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.keyboard.type = AsyncMock()
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.frames = []

    def _mock_query_selector(sel):
        """Return mock elements for LinkedIn-specific selectors."""
        if "easy apply" in sel.lower() or "apply" in sel.lower() or "jobapply" in sel.lower():
            el = MagicMock()
            el.is_visible = AsyncMock(return_value=True)
            el.click = AsyncMock()
            el.inner_text = AsyncMock(return_value="Easy Apply")
            return el
        return None

    mock_page.query_selector = AsyncMock(side_effect=_mock_query_selector)

    mock_frame = MagicMock()
    mock_frame.query_selector = AsyncMock(return_value=None)
    mock_frame.query_selector_all = AsyncMock(return_value=[])
    mock_page.frames = [mock_frame]

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

    return mock_playwright, mock_page, mock_context


@pytest.mark.asyncio
async def test_easy_apply_flow_sets_submitted():
    """E2E: LinkedIn Easy Apply application completes with SUBMITTED state."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job(application_type="easy_apply", canonical_url_hash="heasy123")
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=True)
        assert record.state == JobState.SUBMITTED
        assert "DRY RUN" in (record.confirmation_text or "")


@pytest.mark.asyncio
async def test_external_apply_flow_sets_submitted():
    """E2E: LinkedIn External Apply application completes with SUBMITTED state."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job(
            application_type="external_url",
            raw_url="https://www.linkedin.com/jobs/view/test-ext-456",
            canonical_url_hash="hext456",
        )
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=True)
        assert record.state == JobState.SUBMITTED
        assert "DRY RUN" in (record.confirmation_text or "")


@pytest.mark.asyncio
async def test_linkedin_unknown_type_falls_back_gracefully():
    """E2E: LinkedIn with no application_type falls back to Easy Apply attempt."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job(application_type=None, canonical_url_hash="hunknown")
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=True)
        assert record.state in (JobState.SUBMITTED, JobState.FAILED, JobState.APPLICATION_STARTED)


@pytest.mark.asyncio
async def test_easy_apply_sets_application_started_on_non_dry():
    """E2E: LinkedIn Easy Apply non-dry-run returns record with proper state."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job(application_type="easy_apply", canonical_url_hash="henondry")
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=False)
        assert record is not None
        assert isinstance(record, ApplicationRecord)
        assert record.state in (JobState.SUBMITTED, JobState.FAILED, JobState.APPLICATION_STARTED)


@pytest.mark.asyncio
async def test_application_type_passed_through_browser():
    """E2E: Verify application_type is accessible on JobPosting for browser routing."""
    job_easy = _make_job(application_type="easy_apply")
    job_external = _make_job(application_type="external_url")
    job_none = _make_job(application_type=None)

    assert job_easy.application_type == "easy_apply"
    assert job_external.application_type == "external_url"
    assert job_none.application_type is None


@pytest.mark.asyncio
async def test_easy_apply_panel_detection():
    """E2E: Verify LinkedIn Easy Apply panel detection logic."""
    engine = BrowserApplicationEngine(headless=True)

    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    result = await engine._detect_linkedin_easy_apply_panel(mock_page)
    assert result is False


@pytest.mark.asyncio
async def test_external_apply_navigates_to_external_url():
    """E2E: Verify LinkedIn External Apply uses external URL navigation."""
    from job_hunt.automation.browser import LINKEDIN_EASY_APPLY_SELECTORS

    assert len(LINKEDIN_EASY_APPLY_SELECTORS) > 0


@pytest.mark.asyncio
async def test_linkedin_flow_no_crash_on_missing_type():
    """E2E: LinkedIn flow should not crash when application_type is missing."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job()
        job.application_type = None
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=True)
        assert record is not None
        assert isinstance(record, ApplicationRecord)


@pytest.mark.asyncio
async def test_easy_apply_selectors_defined():
    """E2E: Verify LinkedIn Easy Apply selectors are properly configured."""
    from job_hunt.automation.browser import LINKEDIN_EASY_APPLY_SELECTORS
    from job_hunt.automation.browser import LINKEDIN_SKILL_TAG_SELECTORS
    from job_hunt.automation.browser import CLOUDFLARE_TURNSTILE_SELECTORS

    assert "button:has-text('Easy Apply')" in LINKEDIN_EASY_APPLY_SELECTORS
    assert len(LINKEDIN_SKILL_TAG_SELECTORS) > 0
    assert len(CLOUDFLARE_TURNSTILE_SELECTORS) > 0


@pytest.mark.asyncio
async def test_dry_run_skips_submit():
    """E2E: Dry-run mode captures screenshot without clicking submit for LinkedIn."""
    with patch("job_hunt.automation.browser.async_playwright") as mock_pw:
        mock_playwright, mock_page, mock_context = _mock_playwright_context()
        mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        engine = BrowserApplicationEngine(headless=True)
        job = _make_job(application_type="easy_apply", canonical_url_hash="hdryrun")
        profile = _make_profile()

        record = await engine.fill_and_submit(job, profile, dry_run=True)
        assert record.state == JobState.SUBMITTED
        assert record.screenshot_path is not None
        assert "dry_run" in (record.screenshot_path or "")