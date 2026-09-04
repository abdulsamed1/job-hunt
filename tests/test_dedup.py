"""Unit tests for multi-signal deduplication and canonicalization."""

import pytest
from job_hunt.dedup import (
    canonical_url_hash,
    clean_content_text,
    compute_role_fingerprint,
    content_hash,
    normalize_company,
    normalize_location,
    normalize_role,
    normalize_url,
)


def test_canonical_url_normalization():
    # Tracking parameters stripped
    url1 = "https://boards.greenhouse.io/stripe/jobs/123?utm_source=linkedin&gh_src=campaign&trk=feed"
    url2 = "https://boards.greenhouse.io/stripe/jobs/123"
    assert normalize_url(url1) == url2

    # Preserves functional query params (e.g. gh_jid) and sorts them
    url3 = "https://boards.greenhouse.io/stripe/jobs/123?gh_jid=999&utm_medium=email"
    assert normalize_url(url3) == "https://boards.greenhouse.io/stripe/jobs/123?gh_jid=999"

    # Scheme normalized to https and host lowercased
    url4 = "HTTP://Boards.Greenhouse.IO/stripe/jobs/123/"
    assert normalize_url(url4) == "https://boards.greenhouse.io/stripe/jobs/123"

    # Fragment stripped
    url5 = "https://boards.greenhouse.io/stripe/jobs/123#apply-now"
    assert normalize_url(url5) == "https://boards.greenhouse.io/stripe/jobs/123"

    # Invalid URLs return empty string
    assert normalize_url("") == ""
    assert normalize_url("N/A") == ""
    assert normalize_url("not-a-valid-url") == ""


def test_canonical_url_hash():
    url = "https://jobs.lever.co/company/abc-123"
    h1 = canonical_url_hash(url)
    h2 = canonical_url_hash(url + "?utm_campaign=winter")
    assert h1 == h2
    assert len(h1) == 64


def test_company_normalization():
    # Legal suffixes stripped
    assert normalize_company("Stripe, Inc.") == "stripe"
    assert normalize_company("Acme Technologies LLC") == "acme"
    assert normalize_company("Google Corporation") == "google"
    assert normalize_company("GitLab Ltd.") == "gitlab"
    assert normalize_company("Siemens AG") == "siemens"
    assert normalize_company("Bayerische Motoren Werke GmbH") == "bayerische motoren werke"


def test_role_normalization():
    # Abbreviations expanded and case normalized
    r1 = normalize_role("Sr. SWE - Backend")
    r2 = normalize_role("Senior Software Engineer - Backend")
    assert r1 == r2

    r3 = normalize_role("Lead ML Engr")
    r4 = normalize_role("Lead Machine Learning Engineer")
    assert r3 == r4


def test_location_normalization():
    assert normalize_location("Remote, US") == "remote us"
    assert normalize_location("Remote - Worldwide") == "remote global"
    assert normalize_location("San Francisco, CA") == "san francisco ca"
    assert normalize_location("") == "unknown"


def test_role_fingerprint():
    # Same company and role across different formats produce identical fingerprint
    fp1 = compute_role_fingerprint("Stripe Inc.", "Sr. Software Engineer", "Remote, US")
    fp2 = compute_role_fingerprint("Stripe", "Senior Software Engineer", "Remote (US)")
    assert fp1 == fp2


def test_content_hashing():
    html_desc = "<div><p>We are looking for a <b>Software Engineer</b>.</p></div>"
    text_desc = "We are looking for a Software Engineer."
    assert clean_content_text(html_desc) == clean_content_text(text_desc)
    assert content_hash(html_desc) == content_hash(text_desc)
