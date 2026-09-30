"""Integration tests for Playwright browser form filling and CAPTCHA detection."""

import base64
import pytest
from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.models import CandidateProfile, JobPosting, JobState


@pytest.fixture
def candidate():
    return CandidateProfile(
        full_name="Robin Diaz",
        first_name="Robin",
        last_name="Diaz",
        email="robin@example.com",
        phone="+1-555-0188",
        location="Austin, TX",
        linkedin_url="https://linkedin.com/in/robindiaz",
        github_url="https://github.com/robindiaz",
        verified_skills=["Python", "FastAPI"],
    )


@pytest.mark.asyncio
async def test_browser_dry_run_form_filling(candidate, tmp_path):
    html = """
    <!DOCTYPE html>
    <html>
      <head><title>Job Application</title></head>
      <body>
        <h1>Apply for Senior Software Engineer</h1>
        <form id="application_form">
          <input type="text" name="first_name" id="first_name" />
          <input type="text" name="last_name" id="last_name" />
          <input type="email" name="email" id="email" />
          <input type="tel" name="phone" id="phone" />
          <input type="text" name="linkedin" id="linkedin" />
          <input type="file" name="resume" id="resume" />
          <button type="submit" id="submit_app">Submit Application</button>
        </form>
      </body>
    </html>
    """
    b64 = base64.b64encode(html.encode()).decode()
    data_url = f"data:text/html;base64,{b64}"

    job = JobPosting(
        id=77,
        source="test",
        title="Senior Software Engineer",
        company="LocalCorp",
        raw_url=data_url,
        canonical_url=data_url,
        canonical_url_hash="h77",
        role_fingerprint="rf77",
        content_hash="c77",
    )

    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Robin Diaz Resume", encoding="utf-8")

    engine = BrowserApplicationEngine(headless=True, screenshots_dir=str(tmp_path))
    record = await engine.fill_and_submit(job, candidate, resume_file_path=str(resume_file), dry_run=True)

    assert record.job_id == 77
    assert record.state == JobState.SUBMITTED
    assert "DRY RUN" in record.confirmation_text
    assert record.screenshot_path is not None


@pytest.mark.asyncio
async def test_browser_captcha_detection(candidate, tmp_path):
    html_with_captcha = """
    <!DOCTYPE html>
    <html>
      <head><title>Security Check</title></head>
      <body>
        <h1>Please confirm you are human</h1>
        <div class="g-recaptcha" data-sitekey="sample"></div>
        <iframe src="https://challenges.cloudflare.com/turnstile/v0/api.js"></iframe>
      </body>
    </html>
    """
    b64 = base64.b64encode(html_with_captcha.encode()).decode()
    data_url = f"data:text/html;base64,{b64}"

    job = JobPosting(
        id=88,
        source="test",
        title="Staff Engineer",
        company="ProtectedCorp",
        raw_url=data_url,
        canonical_url=data_url,
        canonical_url_hash="h88",
        role_fingerprint="rf88",
        content_hash="c88",
    )

    engine = BrowserApplicationEngine(headless=True, screenshots_dir=str(tmp_path))
    record = await engine.fill_and_submit(job, candidate, dry_run=False)

    assert record.state == JobState.BLOCKED_CAPTCHA
    assert "CAPTCHA" in (record.error_message or "")
    assert record.screenshot_path is not None


def test_captcha_transcription_parser():
    from job_hunt.automation.captcha_solver import parse_audio_transcription, is_captcha_error

    # Test Whisper-style separated audio tokens with stutter
    raw = "T, D, R, 5, 5, 8."
    code = parse_audio_transcription(raw)
    assert code == "TDR58"

    # Test error detection
    error_html = "<div class='error'>Incorrect captcha code entered. Please try again.</div>"
    assert is_captcha_error(error_html) is True
    ok_html = "<div>Application submitted successfully. Thank you!</div>"
    assert is_captcha_error(ok_html) is False


def test_classify_apply_host():
    from job_hunt.automation.browser import classify_apply_host

    assert classify_apply_host("https://boards.greenhouse.io/stripe/jobs/1") == "ats"
    assert classify_apply_host("https://jobs.lever.co/spotify/abc") == "ats"
    assert classify_apply_host("https://jobs.ashbyhq.com/linear/1") == "ats"
    assert classify_apply_host("https://www.linkedin.com/jobs/view/123") == "portal"
    assert classify_apply_host("https://techcorp.com/careers/apply") == "unverified"
    # Spoofing attempts must fail closed
    assert classify_apply_host("https://evil-greenhouse.io/apply") == "unverified"
    assert classify_apply_host("https://job-boards.greenhouse.io.evil.com/apply") == "unverified"
    assert classify_apply_host("https://greenhouse.io@evil.com/apply") == "unverified"
    assert classify_apply_host("not-a-url") == "unverified"


@pytest.mark.asyncio
async def test_linkedin_submit_requires_human_approval():
    from unittest.mock import AsyncMock, MagicMock, patch
    from job_hunt.automation.browser import BrowserApplicationEngine

    mock_page = MagicMock()
    mock_page.url = "https://www.linkedin.com/jobs/view/123"
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.add_init_script = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.frames = []
    mock_page.content = AsyncMock(return_value="<html></html>")

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()
    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    mock_pw = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    def make_job():
        return JobPosting(
            source="linkedin",
            title="Backend Engineer",
            company="Co",
            raw_url="https://www.linkedin.com/jobs/view/123",
            canonical_url="https://www.linkedin.com/jobs/view/123",
            canonical_url_hash="h-approval",
            role_fingerprint="rf-approval",
            content_hash="c-approval",
            application_type="easy_apply",
        )

    profile = CandidateProfile(
        full_name="A B", first_name="A", last_name="B",
        email="a@b.com", phone="1", location="Remote",
    )

    with patch("job_hunt.automation.browser.async_playwright") as mock_pw_fn:
        mock_pw_fn.return_value.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_fn.return_value.__aexit__ = AsyncMock(return_value=None)
        engine = BrowserApplicationEngine(headless=True, require_linkedin_approval=True)
        record = await engine.fill_and_submit(make_job(), profile, dry_run=False, linkedin_approved=False)

    assert record.state == JobState.FAILED
    assert "human approval" in (record.error_message or "").lower()


@pytest.mark.asyncio
async def test_external_apply_refuses_unverified_host_without_approval():
    from unittest.mock import AsyncMock, MagicMock
    from job_hunt.automation.browser import BrowserApplicationEngine

    engine = BrowserApplicationEngine(headless=True)

    evil_link = MagicMock()
    evil_link.is_visible = AsyncMock(return_value=True)
    evil_link.get_attribute = AsyncMock(return_value="https://job-boards.greenhouse.io.evil.com/apply")

    mock_page = MagicMock()
    mock_page.frames = []

    async def fake_query_selector(sel):
        if "linkedin" in sel.lower() or "apply" in sel.lower():
            return evil_link
        return None

    mock_page.query_selector = fake_query_selector

    # Unverified host, no approval -> refused
    assert await engine._try_linkedin_external_apply(mock_page, allow_unverified=False) is False

    good_link = MagicMock()
    good_link.is_visible = AsyncMock(return_value=True)
    good_link.get_attribute = AsyncMock(return_value="https://boards.greenhouse.io/co/jobs/1")
    good_link.click = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=good_link)
    mock_page.wait_for_timeout = AsyncMock()

    # Known ATS host -> allowed even without approval flag
    assert await engine._try_linkedin_external_apply(mock_page, allow_unverified=False) is True


@pytest.mark.asyncio
async def test_try_apply_trigger_skips_unverified_hosts():
    from unittest.mock import AsyncMock, MagicMock
    from job_hunt.automation.browser import BrowserApplicationEngine

    engine = BrowserApplicationEngine(headless=True)

    def make_page(link_href):
        link = MagicMock()
        link.is_visible = AsyncMock(return_value=True)
        link.get_attribute = AsyncMock(return_value=link_href)
        page = MagicMock()
        page.frames = []

        async def fake_qsa(sel):
            # Return the link only when the selector pattern matches its href
            import re
            m = re.search(r"a\[href\*='([^']+)'\]", sel)
            if m and m.group(1).lower() in link_href.lower():
                return [link]
            return []

        page.query_selector_all = fake_qsa
        page.query_selector = AsyncMock(return_value=None)
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        return page

    evil_page = make_page("https://evil.com/apply/123")
    assert await engine._try_apply_trigger(evil_page) is False
    evil_page.goto.assert_not_called()

    ats_page = make_page("https://boards.greenhouse.io/co/jobs/1")
    assert await engine._try_apply_trigger(ats_page) is True
    ats_page.goto.assert_called_once()
    assert ats_page.goto.call_args[0][0] == "https://boards.greenhouse.io/co/jobs/1"


def test_has_linkedin_session_checks_li_at_cookie(tmp_path):
    import json
    from job_hunt.automation.browser import BrowserApplicationEngine

    state = tmp_path / "state.json"
    engine = BrowserApplicationEngine(headless=True, linkedin_storage_state=str(state))
    assert engine.has_linkedin_session() is False

    state.write_text(json.dumps({"cookies": [{"name": "JSESSIONID", "value": "x"}]}))
    assert engine.has_linkedin_session() is False

    state.write_text(json.dumps({"cookies": [{"name": "li_at", "value": "secret"}]}))
    assert engine.has_linkedin_session() is True


@pytest.mark.asyncio
async def test_detect_linkedin_login_wall():
    from unittest.mock import AsyncMock, MagicMock
    from job_hunt.automation.browser import BrowserApplicationEngine

    engine = BrowserApplicationEngine(headless=True)

    walled = MagicMock()
    walled.url = "https://www.linkedin.com/login?trk=guest_homepage"
    walled.query_selector = AsyncMock(return_value=None)
    assert await engine._detect_linkedin_login_wall(walled) is True

    form_page = MagicMock()
    form_page.url = "https://www.linkedin.com/jobs/view/123"
    form_el = MagicMock()
    form_el.is_visible = AsyncMock(return_value=True)
    form_page.query_selector = AsyncMock(return_value=form_el)
    assert await engine._detect_linkedin_login_wall(form_page) is True

    clean = MagicMock()
    clean.url = "https://www.linkedin.com/jobs/view/123"
    clean.query_selector = AsyncMock(return_value=None)
    assert await engine._detect_linkedin_login_wall(clean) is False


@pytest.mark.asyncio
async def test_fill_and_submit_uses_saved_linkedin_session(tmp_path):
    import json
    from unittest.mock import AsyncMock, MagicMock, patch
    from job_hunt.automation.browser import BrowserApplicationEngine

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cookies": [{"name": "li_at", "value": "s"}]}))

    mock_page = MagicMock()
    mock_page.url = "https://www.linkedin.com/jobs/view/123"
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.add_init_script = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.frames = []
    mock_page.content = AsyncMock(return_value="<html></html>")

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()
    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    mock_pw = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    with patch("job_hunt.automation.browser.async_playwright") as mock_pw_fn:
        mock_pw_fn.return_value.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_fn.return_value.__aexit__ = AsyncMock(return_value=None)
        engine = BrowserApplicationEngine(headless=True, linkedin_storage_state=str(state))
        job = JobPosting(
            source="linkedin", title="T", company="C",
            raw_url="https://www.linkedin.com/jobs/view/123",
            canonical_url="https://www.linkedin.com/jobs/view/123",
            canonical_url_hash="h-ss", role_fingerprint="rf-ss", content_hash="c-ss",
            application_type="easy_apply",
        )
        profile = CandidateProfile(
            full_name="A B", first_name="A", last_name="B",
            email="a@b.com", phone="1", location="Remote",
        )
        await engine.fill_and_submit(job, profile, dry_run=True)

    _, kwargs = mock_browser.new_context.call_args
    assert kwargs.get("storage_state") == str(state)
