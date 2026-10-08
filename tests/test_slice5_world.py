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


# --- Final fix wave: I1 host-suffix validation ---

def test_teamtailor_feed_url_validates_host():
    adapter = TeamtailorAdapter()
    # Multi-level subdomains accepted.
    assert adapter._feed_url({"url": "https://softwarefinder.na.teamtailor.com"}) == (
        "https://softwarefinder.na.teamtailor.com/jobs.rss"
    )
    assert adapter._feed_url({"url": "https://career.teamtailor.com/jobs.rss"}) == (
        "https://career.teamtailor.com/jobs.rss"
    )
    # Evil hosts rejected — including the .rss passthrough.
    assert adapter._feed_url({"url": "https://teamtailor.com.evil.com"}) == ""
    assert adapter._feed_url({"url": "https://evil-teamtailor.com/jobs.rss"}) == ""
    assert adapter._feed_url({"url": "https://evil.com/jobs.rss"}) == ""
    assert adapter._feed_url({"url": "not a url"}) == ""


def test_personio_feed_url_validates_host():
    adapter = PersonioAdapter()
    assert adapter._feed_url({"url": "https://vivid.jobs.personio.de"}) == (
        "https://vivid.jobs.personio.de/xml"
    )
    assert adapter._feed_url({"url": "https://vivid.jobs.personio.de/xml"}) == (
        "https://vivid.jobs.personio.de/xml"
    )
    # Evil hosts rejected — including the /xml passthrough.
    assert adapter._feed_url({"url": "https://jobs.personio.de.evil.com/xml"}) == ""
    assert adapter._feed_url({"url": "https://evil.com/xml"}) == ""
    assert adapter._feed_url({"url": "https://vivid.personio.de"}) == ""


def test_recruitee_api_url_validates_host():
    adapter = RecruiteeAdapter()
    assert adapter._api_url({"url": "https://make.recruitee.com"}) == (
        "https://make.recruitee.com/api/offers/"
    )
    # Evil hosts rejected — including the /api/offers passthrough.
    assert adapter._api_url({"url": "https://make.recruitee.com.evil.com/api/offers/"}) == ""
    assert adapter._api_url({"url": "https://evil.com/api/offers/"}) == ""
    assert adapter._api_url({"url": "https://evil.com"}) == ""


@pytest.mark.asyncio
async def test_tenant_adapters_fetch_nothing_for_evil_hosts():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="x"))
    ) as client:
        assert await TeamtailorAdapter().fetch({"url": "https://evil.com/jobs.rss"}, client) == []
        assert await PersonioAdapter().fetch({"url": "https://evil.com/xml"}, client) == []
        assert await RecruiteeAdapter().fetch({"url": "https://evil.com/api/offers/"}, client) == []


# --- Final fix wave: I3 personio salary types ---

def _personio_xml_with_salary(salary_block: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position>
    <id>1</id>
    <name>Engineer</name>
    <office>Remote</office>
    <jobDescriptions><jobDescription><name>Role</name><value><![CDATA[<p>Work.</p>]]></value></jobDescription></jobDescriptions>
    <createdAt>2026-10-01T00:00:00Z</createdAt>
    {salary_block}
</position>
</workzag-jobs>
"""


async def _fetch_personio_salary(xml: str):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))
    ) as client:
        postings = await PersonioAdapter().fetch(
            {"name": "V", "url": "https://vivid.jobs.personio.de/xml"}, client
        )
    assert len(postings) == 1
    return postings[0]


@pytest.mark.asyncio
async def test_personio_salary_yearly_kept():
    p = await _fetch_personio_salary(
        _personio_xml_with_salary(
            "<salaryInformation><min>30000</min><max>40000</max>"
            "<currencyCode>EUR</currencyCode><type>yearly</type></salaryInformation>"
        )
    )
    assert (p.salary_min, p.salary_max, p.salary_currency, p.salary_source) == (
        30000.0, 40000.0, "EUR", "stated",
    )


@pytest.mark.asyncio
async def test_personio_salary_monthly_annualized():
    p = await _fetch_personio_salary(
        _personio_xml_with_salary(
            "<salaryInformation><min>3000</min><max>4000</max>"
            "<currencyCode>EUR</currencyCode><type>monthly</type></salaryInformation>"
        )
    )
    assert (p.salary_min, p.salary_max, p.salary_source) == (36000.0, 48000.0, "stated")


@pytest.mark.asyncio
async def test_personio_salary_hourly_annualized():
    p = await _fetch_personio_salary(
        _personio_xml_with_salary(
            "<salaryInformation><min>50</min>"
            "<currencyCode>USD</currencyCode><type>hourly</type></salaryInformation>"
        )
    )
    assert (p.salary_min, p.salary_max, p.salary_source) == (104000.0, None, "stated")


@pytest.mark.asyncio
async def test_personio_salary_unknown_type_dropped():
    p = await _fetch_personio_salary(
        _personio_xml_with_salary(
            "<salaryInformation><min>500</min><max>700</max>"
            "<currencyCode>EUR</currencyCode><type>weekly</type></salaryInformation>"
        )
    )
    assert (p.salary_min, p.salary_max, p.salary_source) == (None, None, None)


@pytest.mark.asyncio
async def test_personio_salary_source_only_with_amounts():
    p = await _fetch_personio_salary(_personio_xml_with_salary(""))
    assert (p.salary_min, p.salary_max, p.salary_source) == (None, None, None)


# --- Final fix wave: I4 uniform description pipeline ---

TEAMTAILOR_LONG_RSS = (
    """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item>"""
    """<title>Backend Engineer</title>"""
    """<link>https://career.teamtailor.com/jobs/1-backend-engineer</link>"""
    """<guid>4ff07fd5-3ac0-4333-8f4c-955380396321</guid>"""
    """<description>&lt;p&gt;Build things with Python. &lt;b&gt;Great team.&lt;/b&gt; """
    + "detail. " * 1500
    + """&lt;/p&gt;</description></item></channel></rss>"""
)


@pytest.mark.asyncio
async def test_description_pipeline_stripped_and_capped():
    async def tt_handler(request):
        return httpx.Response(200, text=TEAMTAILOR_LONG_RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(tt_handler)) as client:
        postings = await TeamtailorAdapter().fetch(
            {"name": "Teamtailor", "url": "https://career.teamtailor.com/jobs.rss"}, client
        )
    assert len(postings) == 1
    p = postings[0]
    assert "<" not in p.description and ">" not in p.description
    assert len(p.description) == 4000
    assert p.external_id == "4ff07fd5-3ac0-4333-8f4c-955380396321"  # M3: guid, not URL
    from job_hunt.dedup import content_hash

    assert p.content_hash == content_hash(p.description)  # hash of stored text

    async def rec_handler(request):
        return httpx.Response(200, json={
            "offers": [{
                "id": 7, "title": "Dev", "careers_url": "https://make.recruitee.com/o/dev",
                "description": "<p>Hello  <b>world</b></p>",
                "requirements": "<ul><li>x</li></ul>",
            }]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(rec_handler)) as client:
        postings = await RecruiteeAdapter().fetch(
            {"name": "Make", "url": "https://make.recruitee.com/api/offers/"}, client
        )
    assert postings[0].description == "Hello world x"


@pytest.mark.asyncio
async def test_shared_ua_across_tenant_adapters():
    from job_hunt.discovery.base import BROWSER_UA

    seen = {}

    def handler(name):
        async def _h(request):
            seen[name] = request.headers.get("user-agent")
            if name == "recruitee":
                return httpx.Response(200, json={"offers": []})
            if name == "personio":
                return httpx.Response(200, text="<workzag-jobs></workzag-jobs>")
            return httpx.Response(200, text="<rss/>")

        return _h

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler("teamtailor"))) as client:
        await TeamtailorAdapter().fetch({"url": "https://career.teamtailor.com"}, client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler("recruitee"))) as client:
        await RecruiteeAdapter().fetch({"url": "https://make.recruitee.com"}, client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler("personio"))) as client:
        await PersonioAdapter().fetch({"url": "https://vivid.jobs.personio.de"}, client)
    assert set(seen.values()) == {BROWSER_UA}


# --- Final fix wave: M4 robots gate on the adapter fetch URL ---

@pytest.mark.asyncio
async def test_robots_gate_evaluates_adapter_feed_url():
    from job_hunt.discovery.registry import SourceRegistry

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nDisallow: /jobs.rss\n"
            )
        return httpx.Response(200, text="<rss/>")

    import asyncio

    reg = SourceRegistry()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Entry bare host "/" is allowed, but the adapter's feed URL
        # /jobs.rss is disallowed → the source must be skipped.
        out = await reg.scan_source(
            {"name": "t", "adapter": "teamtailor", "url": "https://career.teamtailor.com"},
            client,
            asyncio.Semaphore(1),
        )
    assert out == []


# --- Final fix wave: I6 hydrated SPA ---

def test_stash_nested_braces_parse():
    from job_hunt.discovery.adapters.web import extract_hydrated_jobs

    html = (
        '<html><script>var Stash = {"jobsearch": {"result_app": {"searchResponse": '
        '{"results": [{"title": "Backend Engineer", "company": "Acme", '
        '"url": "https://x/jobs/1", "meta": {"tags": ["a}b", "c"]}}]}}}};</script></html>'
    )
    jobs = extract_hydrated_jobs(html)
    assert len(jobs) == 1 and jobs[0]["title"] == "Backend Engineer"


def test_hydrated_posting_skips_urlless_and_unknown_location():
    from job_hunt.discovery.adapters.web import UniversalWebAdapter

    adapter = UniversalWebAdapter()
    assert adapter._hydrated_dict_to_posting({"title": "No URL"}, "https://x/", "Portal") is None
    p = adapter._hydrated_dict_to_posting(
        {"title": "Dev", "company": "Acme", "url": "https://x/jobs/9"},
        "https://x/",
        "Portal",
    )
    assert p is not None
    assert p.location == "Unknown"  # never defaulted to "Remote"
    assert p.external_id == "https://x/jobs/9"
    assert p.source == "web" and p.source_name == "web"  # fixed adapter id


# --- Final fix wave: I8 resolver fallback + dead-board 200-clears ---

def test_resolver_unstripped_slug_fallback():
    mod = _load_resolver()
    assert mod.slugify("Acme Inc.", strip_suffixes=False) == "acme-inc"

    def fake_fetch(url):
        if url.startswith("https://api.lever.co/v0/postings/acme-inc"):
            return (200, [{"id": "abc123"}])
        return (404, [])

    assert mod.resolve_company("Acme Inc.", fetch=fake_fetch) == ("lever", "acme-inc", 1)


def test_resolver_records_404s_for_both_variants_only_after_both_miss(tmp_path):
    mod = _load_resolver()
    mem = str(tmp_path / "dead.json")

    def fake_fetch(url):
        return (404, [])

    assert mod.resolve_company("Acme Inc.", fetch=fake_fetch, mem_path=mem) is None
    from job_hunt.discovery.dead_boards import _load

    data = _load(mem)
    assert data["greenhouse:acme"]["misses"] == 1
    assert data["greenhouse:acme-inc"]["misses"] == 1


def test_dead_boards_200_clears_misses(tmp_path):
    from job_hunt.discovery.dead_boards import _load, record_result, should_skip

    mem = tmp_path / "dead.json"
    for _ in range(3):
        record_result(str(mem), "greenhouse:acme", 404)
    assert should_skip(str(mem), "greenhouse:acme")
    record_result(str(mem), "greenhouse:acme", 200)
    assert _load(str(mem)).get("greenhouse:acme") is None
    assert not should_skip(str(mem), "greenhouse:acme")
