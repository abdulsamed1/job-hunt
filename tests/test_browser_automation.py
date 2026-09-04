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
