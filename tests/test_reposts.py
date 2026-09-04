"""Unit tests for repost and ghost job detection."""

from job_hunt.models import JobPosting
from job_hunt.reposts import RepostDetector, title_identity_key


def test_title_identity_key_normalization():
    # Minor word order or stop word changes should resolve to the same key
    k1 = title_identity_key("Senior Software Engineer - Backend")
    k2 = title_identity_key("Senior Backend Software Engineer")
    assert k1 == k2

    # Different roles or levels should produce different keys
    k3 = title_identity_key("Staff Software Engineer - Backend")
    assert k1 != k3

    k4 = title_identity_key("Frontend Engineer")
    assert k1 != k4


def test_repost_detector_identifies_reposted_openings():
    detector = RepostDetector(min_span_days=1)

    hist_job = JobPosting(
        id=101,
        source="greenhouse",
        title="Senior Python Engineer",
        company="Stripe",
        raw_url="https://stripe.com/jobs/101",
        canonical_url="https://stripe.com/jobs/101",
        canonical_url_hash="hash101",
        role_fingerprint="fp101",
        content_hash="ch101",
        created_at="2026-07-01T10:00:00Z",
    )

    new_job = JobPosting(
        id=202,
        source="greenhouse",
        title="Senior Python Engineer",
        company="Stripe",
        raw_url="https://stripe.com/jobs/202",
        canonical_url="https://stripe.com/jobs/202",
        canonical_url_hash="hash202",
        role_fingerprint="fp202",
        content_hash="ch202",
        created_at="2026-09-01T10:00:00Z",
    )

    is_repost, orig, msg = detector.check_is_repost(new_job, [hist_job])
    assert is_repost is True
    assert orig is not None
    assert orig.id == 101
    assert "Job #101" in msg


def test_repost_detector_distinct_company_or_role():
    detector = RepostDetector()

    hist_job = JobPosting(
        id=101,
        source="greenhouse",
        title="Senior Backend Engineer",
        company="Datadog",
        raw_url="https://datadog.com/jobs/101",
        canonical_url="https://datadog.com/jobs/101",
        canonical_url_hash="hash101",
        role_fingerprint="fp101",
        content_hash="ch101",
    )

    new_job = JobPosting(
        id=202,
        source="greenhouse",
        title="Senior Backend Engineer",
        company="Stripe",  # Different company
        raw_url="https://stripe.com/jobs/202",
        canonical_url="https://stripe.com/jobs/202",
        canonical_url_hash="hash202",
        role_fingerprint="fp202",
        content_hash="ch202",
    )

    is_repost, orig, _ = detector.check_is_repost(new_job, [hist_job])
    assert is_repost is False
    assert orig is None
