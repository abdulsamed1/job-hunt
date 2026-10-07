"""Slice 5 world-coverage adapters: Teamtailor RSS, Recruitee offers, Personio XML.

Fixtures below are trimmed copies of live probe output (recon Task 1):
- Teamtailor: <item> blocks from https://career.teamtailor.com/jobs.rss
- Recruitee: offers[] entries from https://make.recruitee.com/api/offers/
- Personio: <position> blocks from https://vivid.jobs.personio.de/xml
No live network in these tests (httpx.MockTransport only).
"""

import httpx
import pytest

from job_hunt.discovery.adapters.teamtailor import TeamtailorAdapter

TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <title>Teamtailor</title>
    <link>https://career.teamtailor.com/jobs</link>
    <item>
      <title>Backend Engineer</title>
      <link>https://career.teamtailor.com/jobs/8118064-backend-engineer</link>
      <guid>4ff07fd5-3ac0-4333-8f4c-955380396321</guid>
      <pubDate>Fri, 24 Jul 2026 09:05:16 +0200</pubDate>
      <description>&lt;p&gt;Build our ATS platform with Python and Postgres.&lt;/p&gt;</description>
      <remoteStatus>fully-remote</remoteStatus>
      <tt:locations>
        <tt:location>
          <tt:name>Remote</tt:name>
          <tt:city></tt:city>
          <tt:country>Sweden</tt:country>
        </tt:location>
      </tt:locations>
      <tt:department>Engineering</tt:department>
    </item>
  </channel>
</rss>
"""


def test_teamtailor_matches_url():
    adapter = TeamtailorAdapter()
    assert adapter.matches_url("https://career.teamtailor.com/jobs.rss") is True
    # Multi-level subdomains exist (softwarefinder.na.teamtailor.com) — must match.
    assert adapter.matches_url("https://softwarefinder.na.teamtailor.com/jobs.rss") is True
    assert adapter.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_teamtailor_parses_real_shape():
    async def mock_handler(request):
        return httpx.Response(200, text=TEAMTAILOR_RSS)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TeamtailorAdapter()
        postings = await adapter.fetch(
            {"name": "Teamtailor", "url": "https://career.teamtailor.com/jobs.rss"}, client
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.raw_url == "https://career.teamtailor.com/jobs/8118064-backend-engineer"
    assert "Remote" in (p.location or "")
    assert p.source == "teamtailor"


RECRUITEE_PAYLOAD = {
    "offers": [
        {
            "id": 2695896,
            "title": "Delivery Lead",
            "slug": "delivery-lead-3",
            "careers_url": "https://make.recruitee.com/o/delivery-lead-3",
            "careers_apply_url": "https://make.recruitee.com/o/delivery-lead-3/c/new",
            "description": "<p>Lead delivery of large-scale custom software.</p>",
            "requirements": "<ul><li>5+ years of experience</li></ul>",
            "remote": True,
            "hybrid": False,
            "on_site": False,
            "location": "Remote job",
            "country": "United States",
            "city": "Remote",
            "department": "Delivery",
            "employment_type_code": "fulltime_permanent",
            "published_at": "2026-07-30 19:46:15 UTC",
            "translations": {"en": {"title": "Delivery Lead"}},
        }
    ]
}


def test_recruitee_matches_url():
    from job_hunt.discovery.adapters.recruitee import RecruiteeAdapter
    adapter = RecruiteeAdapter()
    assert adapter.matches_url("https://make.recruitee.com/api/offers/") is True
    assert adapter.matches_url("https://happeo.recruitee.com/o/some-role") is True
    assert adapter.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_recruitee_parses_real_shape():
    from job_hunt.discovery.adapters.recruitee import RecruiteeAdapter

    async def mock_handler(request):
        return httpx.Response(200, json=RECRUITEE_PAYLOAD)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = RecruiteeAdapter()
        postings = await adapter.fetch(
            {"name": "Make", "url": "https://make.recruitee.com/api/offers/"}, client
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Delivery Lead"
    assert p.external_id == "2695896"
    assert p.raw_url == "https://make.recruitee.com/o/delivery-lead-3"
    assert "Remote" in (p.location or "")
    assert p.source == "recruitee"


@pytest.mark.asyncio
async def test_recruitee_empty_and_wrong_shape():
    from job_hunt.discovery.adapters.recruitee import RecruiteeAdapter

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"offers": []}))
    ) as client:
        assert await RecruiteeAdapter().fetch({"url": "https://make.recruitee.com/api/offers/"}, client) == []

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"nope": 1}))
    ) as client:
        with pytest.raises(ValueError, match="Recruitee"):
            await RecruiteeAdapter().fetch({"url": "https://make.recruitee.com/api/offers/"}, client)
