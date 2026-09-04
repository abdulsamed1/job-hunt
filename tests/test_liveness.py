"""Unit tests for zero-token liveness detection and expired posting filters."""

import pytest
import httpx
from job_hunt.liveness import LivenessDetector


def test_liveness_detector_live_page():
    detector = LivenessDetector()
    live_html = """
    <html>
      <body>
        <h1>Senior Backend Engineer</h1>
        <p>We are seeking an experienced distributed systems engineer.</p>
        <button type="submit">Apply for this Job</button>
      </body>
    </html>
    """
    is_live, reason = detector.check_html_content(live_html, "https://example.com/jobs/123")
    assert is_live is True
    assert "live" in reason.lower()


def test_liveness_detector_hard_expired():
    detector = LivenessDetector()
    expired_html = """
    <html>
      <body>
        <h1>Role Update</h1>
        <div class="banner">This job is no longer available.</div>
        <p>Browse our other open opportunities below.</p>
      </body>
    </html>
    """
    is_live, reason = detector.check_html_content(expired_html, "https://example.com/jobs/123")
    assert is_live is False
    assert "expired" in reason.lower() or "no longer available" in reason.lower()


def test_liveness_detector_position_filled():
    detector = LivenessDetector()
    filled_html = """
    <html>
      <body>
        <div>Thank you for your interest. This position has been filled.</div>
      </body>
    </html>
    """
    is_live, reason = detector.check_html_content(filled_html, "https://example.com/jobs/123")
    assert is_live is False
    assert "expired" in reason.lower() or "filled" in reason.lower()


def test_liveness_detector_cloudflare_challenge_guard():
    detector = LivenessDetector()
    cf_html = """
    <html>
      <head><title>Just a moment...</title></head>
      <body>
        <span>Checking your browser before accessing the site.</span>
        <div class="cf-ray">Ray ID: 12345abcdef</div>
      </body>
    </html>
    """
    is_live, reason = detector.check_html_content(cf_html, "https://example.com/jobs/123")
    # Must NOT be marked as expired because it is a security wall, not a dead job
    assert is_live is True
    assert "challenge" in reason.lower() or "cloudflare" in reason.lower()


@pytest.mark.asyncio
async def test_liveness_async_http_404():
    detector = LivenessDetector()

    async def mock_handler(request):
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        is_live, reason = await detector.check_url_async("https://example.com/jobs/999", client=client)

    assert is_live is False
    assert "404" in reason
