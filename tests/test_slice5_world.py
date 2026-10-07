"""Slice 5 world-coverage adapters: Teamtailor RSS, Recruitee offers, Personio XML.

Fixtures below are trimmed copies of live probe output (recon Task 1):
- Teamtailor: <item> blocks from https://career.teamtailor.com/jobs.rss
- Recruitee: offers[] entries from https://make.recruitee.com/api/offers/
- Personio: <position> blocks from https://vivid.jobs.personio.de/xml
No live network in these tests (httpx.MockTransport only).
"""

import httpx
import pytest

from job_hunt.discovery.adapters.personio import PersonioAdapter
from job_hunt.discovery.adapters.recruitee import RecruiteeAdapter
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
    adapter = RecruiteeAdapter()
    assert adapter.matches_url("https://make.recruitee.com/api/offers/") is True
    assert adapter.matches_url("https://happeo.recruitee.com/o/some-role") is True
    assert adapter.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_recruitee_parses_real_shape():
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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"offers": []}))
    ) as client:
        assert await RecruiteeAdapter().fetch({"url": "https://make.recruitee.com/api/offers/"}, client) == []

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"nope": 1}))
    ) as client:
        with pytest.raises(ValueError, match="Recruitee"):
            await RecruiteeAdapter().fetch({"url": "https://make.recruitee.com/api/offers/"}, client)


PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position>
    <id>2452785</id>
    <subcompany>VividTech Limited</subcompany>
    <office>Limassol</office>
    <department>Engineering</department>
    <name>Backend Engineer</name>
    <jobDescriptions>
        <jobDescription>
            <name>About The Role</name>
            <value>
                <![CDATA[We are looking for an experienced <b>Backend Engineer</b> to build fintech systems. Unclosed <span>markup here]]>
            </value>
        </jobDescription>
    </jobDescriptions>
    <employmentType>permanent</employmentType>
    <schedule>full-time</schedule>
    <createdAt>2025-12-05T21:17:25+00:00</createdAt>
    <salaryInformation>
        <min>30000.00</min>
        <max>40000.00</max>
        <currencySymbol>EUR</currencySymbol>
        <currencyCode>EUR</currencyCode>
        <type>yearly</type>
    </salaryInformation>
</position>
</workzag-jobs>
"""


def test_personio_matches_url():
    adapter = PersonioAdapter()
    assert adapter.matches_url("https://vivid.jobs.personio.de/xml") is True
    assert adapter.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_personio_parses_real_shape():
    async def mock_handler(request):
        return httpx.Response(200, text=PERSONIO_XML)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = PersonioAdapter()
        postings = await adapter.fetch(
            {"name": "Vivid", "url": "https://vivid.jobs.personio.de/xml"}, client
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.external_id == "2452785"
    assert p.raw_url == "https://vivid.jobs.personio.de/job/2452785"
    assert p.salary_min == 30000.0
    assert p.salary_max == 40000.0
    assert p.salary_currency == "EUR"
    assert p.source == "personio"


@pytest.mark.asyncio
async def test_personio_empty_board_yields_empty():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<workzag-jobs/>")
        )
    ) as client:
        assert (
            await PersonioAdapter().fetch(
                {"url": "https://finn.jobs.personio.de/xml"}, client
            )
            == []
        )


def test_web_falls_back_to_next_data():
    html = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"jobs":[{"title":"Backend Engineer","company":"Acme","url":"https://x/1"}]}}}</script></html>'
    from job_hunt.discovery.adapters.web import extract_hydrated_jobs
    jobs = extract_hydrated_jobs(html)
    assert jobs and jobs[0]["title"] == "Backend Engineer"


def test_dead_boards_counts_only_404(tmp_path):
    from job_hunt.discovery.dead_boards import record_result, should_skip
    mem = tmp_path / "dead.json"
    record_result(str(mem), "greenhouse:acme", 429)
    record_result(str(mem), "greenhouse:acme", 500)
    assert not should_skip(str(mem), "greenhouse:acme")  # throttles never kill
    for _ in range(3):
        record_result(str(mem), "greenhouse:acme", 404)
    assert should_skip(str(mem), "greenhouse:acme")


def _load_resolver():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "scripts" / "resolve_boards.py"
    spec = importlib.util.spec_from_file_location("resolve_boards", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolver_slug_strips_suffixes():
    mod = _load_resolver()
    assert mod.slugify("Acme Inc.") == "acme"
    assert mod.slugify("Globex LLC") == "globex"
    assert mod.slugify("Initech Corporation") == "initech"
    assert mod.slugify("Acme Co") == "acme"
    assert mod.slugify("Umbrella Health") == "umbrella-health"


def test_resolver_first_board_with_jobs_wins():
    mod = _load_resolver()

    def fake_fetch(url):
        if url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
            return (404, [])
        if url.startswith("https://api.lever.co/v0/postings/acme"):
            return (200, [{"id": "abc123"}])
        raise AssertionError(f"should not probe ashby once lever hits: {url}")

    board, slug, count = mod.resolve_company("Acme Inc.", fetch=fake_fetch)
    assert (board, slug, count) == ("lever", "acme", 1)


def test_resolver_unresolving_company_is_manual():
    mod = _load_resolver()

    def fake_fetch(url):
        return (404, [])

    assert mod.resolve_company("Nonexistent Corp", fetch=fake_fetch) is None
    assert mod.format_result("Nonexistent Corp", None) == "MANUAL: Nonexistent Corp"
