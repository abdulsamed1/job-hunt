"""Unit tests for discovery adapters and source isolation."""

import pytest
import httpx
from job_hunt.discovery.adapters.ashby import AshbyAdapter
from job_hunt.discovery.adapters.feed import FeedAdapter
from job_hunt.discovery.adapters.greenhouse import GreenhouseAdapter
from job_hunt.discovery.adapters.lever import LeverAdapter
from job_hunt.discovery.adapters.smartrecruiters import SmartRecruitersAdapter
from job_hunt.discovery.adapters.workday import WorkdayAdapter
from job_hunt.discovery.registry import SourceRegistry


def test_adapter_url_matching():
    gh = GreenhouseAdapter()
    assert gh.matches_url("https://boards.greenhouse.io/stripe") is True
    assert gh.matches_url("https://job-boards.eu.greenhouse.io/datadog") is True
    assert gh.matches_url("https://jobs.lever.co/canva") is False

    lever = LeverAdapter()
    assert lever.matches_url("https://jobs.lever.co/spotify") is True
    assert lever.matches_url("https://jobs.eu.lever.co/company") is True

    ashby = AshbyAdapter()
    assert ashby.matches_url("https://jobs.ashbyhq.com/linear") is True

    sr = SmartRecruitersAdapter()
    assert sr.matches_url("https://jobs.smartrecruiters.com/Visa") is True

    wd = WorkdayAdapter()
    assert wd.matches_url("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite") is True

    feed = FeedAdapter()
    assert feed.matches_url("https://remoteok.com/api") is True


@pytest.mark.asyncio
async def test_greenhouse_mock_fetch():
    mock_gh_response = {
        "jobs": [
            {
                "id": 123456,
                "title": "Senior Software Engineer, Platform",
                "absolute_url": "https://boards.greenhouse.io/testco/jobs/123456",
                "location": {"name": "Remote - US"},
                "content": "<p>Build scalable platform services.</p>",
                "updated_at": "2026-09-01T10:00:00Z",
            }
        ]
    }

    async def mock_handler(request):
        return httpx.Response(200, json=mock_gh_response)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = GreenhouseAdapter()
        postings = await adapter.fetch({"name": "TestCo", "url": "https://boards.greenhouse.io/testco"}, client)

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Senior Software Engineer, Platform"
    assert p.company == "TestCo"
    assert p.canonical_url == "https://boards.greenhouse.io/testco/jobs/123456"
    assert p.external_id == "123456"


@pytest.mark.asyncio
async def test_source_isolation_on_failure():
    """Verify that a broken source does not stop other sources from being scanned."""
    async def mock_handler(request):
        url = str(request.url)
        if "failing-source" in url:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, json={"jobs": [{"id": 1, "title": "SWE", "absolute_url": "https://boards.greenhouse.io/good/1"}]})

    transport = httpx.MockTransport(mock_handler)
    registry = SourceRegistry()

    sources = [
        {"name": "BadSource", "url": "https://boards.greenhouse.io/failing-source", "adapter": "greenhouse"},
        {"name": "GoodSource", "url": "https://boards.greenhouse.io/good", "adapter": "greenhouse"},
    ]

    async with httpx.AsyncClient(transport=transport) as client:
        import asyncio
        semaphore = asyncio.Semaphore(2)
        results = await asyncio.gather(
            registry.scan_source(sources[0], client, semaphore),
            registry.scan_source(sources[1], client, semaphore),
        )

    # BadSource should return empty list safely without raising exception
    assert results[0] == []
    # GoodSource should successfully yield posting
    assert len(results[1]) == 1
    assert results[1][0].title == "SWE"


@pytest.mark.asyncio
async def test_universal_web_adapter_schema_org():
    """Verify UniversalWebAdapter parses Schema.org JobPosting JSON-LD."""
    from job_hunt.discovery.adapters.web import UniversalWebAdapter

    sample_html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Senior Rust / Web3 Developer",
          "hiringOrganization": {
            "@type": "Organization",
            "name": "CryptoStartup"
          },
          "jobLocation": {
            "@type": "Place",
            "address": {
              "addressLocality": "Remote",
              "addressCountry": "Global"
            }
          },
          "url": "https://web3jobs.example/job/rust-dev-123",
          "description": "Build high throughput distributed systems in Rust."
        }
        </script>
      </head>
      <body><h1>Job Board</h1></body>
    </html>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_html)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = UniversalWebAdapter()
        postings = await adapter.fetch({"name": "Web3Portal", "url": "https://web3jobs.example"}, client)

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Senior Rust / Web3 Developer"
    assert p.company == "CryptoStartup"
    assert p.canonical_url == "https://web3jobs.example/job/rust-dev-123"
    assert p.location == "Remote, Global"
    assert "Rust" in p.description


def test_linkedin_adapter_matching():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter
    li = LinkedInAdapter()
    assert li.matches_url("https://www.linkedin.com/jobs/search") is True
    assert li.matches_url("https://linkedin.com/jobs/view/123") is True
    assert li.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_linkedin_adapter_mock_fetch():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    sample_html = """
    <li>
      <div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:99887766">
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/senior-backend-engineer-at-acme-99887766?refId=xyz"></a>
        <h3 class="base-search-card__title">Senior Backend Engineer - Python</h3>
        <h4 class="base-search-card__subtitle">Acme Cloud</h4>
        <span class="job-search-card__location">Remote, Worldwide</span>
        <time datetime="2026-09-04">2026-09-04</time>
      </div>
    </li>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_html)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = LinkedInAdapter()
        entry = {
            "adapter": "linkedin",
            "queries": ["Backend Engineer"],
            "locations": ["Worldwide"],
            "max_pages_per_query": 1,
            "target_jobs_count": 10,
        }
        postings = await adapter.fetch(entry, client)

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Senior Backend Engineer - Python"
    assert p.company == "Acme Cloud"
    assert p.canonical_url == "https://www.linkedin.com/jobs/view/senior-backend-engineer-at-acme-99887766"
    assert p.location == "Remote, Worldwide"
    assert p.external_id == "99887766"
    assert p.source == "linkedin"
    assert p.application_type is None


@pytest.mark.asyncio
async def test_linkedin_adapter_detects_easy_apply():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    sample_html = """
    <li>
      <div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:11122233">
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/senior-python-at-techcorp-11122233"></a>
        <button class="jobs-apply-button">Easy Apply</button>
        <h3 class="base-search-card__title">Senior Python Engineer</h3>
        <h4 class="base-search-card__subtitle">TechCorp</h4>
        <span class="job-search-card__location">Remote</span>
      </div>
    </li>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_html)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = LinkedInAdapter()
        entry = {
            "adapter": "linkedin",
            "queries": ["Python"],
            "locations": ["Remote"],
            "max_pages_per_query": 1,
            "target_jobs_count": 10,
        }
        postings = await adapter.fetch(entry, client)

    assert len(postings) == 1
    assert postings[0].application_type == "easy_apply"


@pytest.mark.asyncio
async def test_linkedin_adapter_detects_external_apply():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    sample_html = """
    <li>
      <div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:44455566">
        <a class="base-card__full-link" href="https://techcorp.com/careers/apply/456"></a>
        <h3 class="base-search-card__title">Full Stack Developer</h3>
        <h4 class="base-search-card__subtitle">TechCorp</h4>
        <span class="job-search-card__location">New York</span>
      </div>
    </li>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_html)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = LinkedInAdapter()
        entry = {
            "adapter": "linkedin",
            "queries": ["Full Stack"],
            "locations": ["New York"],
            "max_pages_per_query": 1,
            "target_jobs_count": 10,
        }
        postings = await adapter.fetch(entry, client)

    assert len(postings) == 1
    assert postings[0].application_type == "external_url"


@pytest.mark.asyncio
async def test_linkedin_malformed_card_does_not_break_batch():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    sample_html = """
    <li>
      <div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:111">
        <h3 class="base-search-card__title"></h3>
      </div>
    </li>
    <li>
      <div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:222">
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/good-role-222"></a>
        <h3 class="base-search-card__title">Good Role</h3>
        <h4 class="base-search-card__subtitle"><a href="https://www.linkedin.com/company/goodco">GoodCo</a></h4>
        <span class="job-search-card__location">Remote</span>
      </div>
    </li>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_html)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = LinkedInAdapter()
        postings = await adapter.fetch(
            {"adapter": "linkedin", "queries": ["x"], "locations": ["Remote"],
             "max_pages_per_query": 1, "target_jobs_count": 10},
            client,
        )

    assert len(postings) == 1
    assert postings[0].title == "Good Role"
    assert postings[0].metadata.get("company_url") == "https://www.linkedin.com/company/goodco"


def test_linkedin_jobage_to_tpr():
    from job_hunt.discovery.adapters.linkedin import jobage_to_tpr

    assert jobage_to_tpr(1) == "r86400"
    assert jobage_to_tpr(7) == "r604800"
    assert jobage_to_tpr(0) is None
    assert jobage_to_tpr(-3) is None


def test_linkedin_worktype_flag():
    from job_hunt.discovery.adapters.linkedin import worktype_flag

    assert worktype_flag("remote") == "2"
    assert worktype_flag("hybrid") == "3"
    assert worktype_flag("onsite") == "1"
    assert worktype_flag("on-site") == "1"
    assert worktype_flag("anything-else") is None
    assert worktype_flag(None) is None


def test_linkedin_job_active_topcard_scope():
    from job_hunt.discovery.adapters.linkedin import is_linkedin_job_active

    closed_html = """
    <html><body>
      <figure class="closed-job closed-job__flavor topcard__flavor-row">
        <figcaption>No longer accepting applications</figcaption>
      </figure>
      <div class="show-more-less-html__markup"><p>Great role.</p></div>
    </body></html>
    """
    assert is_linkedin_job_active(closed_html) is False

    boilerplate_html = """
    <html><body>
      <div class="topcard"><h1>Open Role</h1><button>Apply</button></div>
      <div class="show-more-less-html__markup"><p>We are "no longer accepting applications" for a different closed role quoted here.</p></div>
    </body></html>
    """
    assert is_linkedin_job_active(boilerplate_html) is True

    assert is_linkedin_job_active("") is False


@pytest.mark.asyncio
async def test_feed_adapter_rss_xml_parsing():
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <title>Crypto Jobs RSS</title>
        <item>
          <title>Senior Distributed Systems Engineer</title>
          <link>https://cryptocurrencyjobs.co/engineering/senior-distributed-systems-engineer/</link>
          <dc:creator>Decentralized Labs</dc:creator>
          <description>Lead engineering on high throughput consensus engines.</description>
          <pubDate>Fri, 04 Sep 2026 12:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    async def mock_handler(request):
        return httpx.Response(200, text=sample_xml)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = FeedAdapter()
        postings = await adapter.fetch(
            {"name": "cryptocurrencyjobs", "url": "https://cryptocurrencyjobs.co/index.xml"},
            client,
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Senior Distributed Systems Engineer"
    assert p.company == "Decentralized Labs"
    assert p.canonical_url == "https://cryptocurrencyjobs.co/engineering/senior-distributed-systems-engineer"
    assert "consensus" in p.description


def test_linkedin_seconds_to_tpr():
    from job_hunt.discovery.adapters.linkedin import seconds_to_tpr

    assert seconds_to_tpr(43200) == "r43200"
    assert seconds_to_tpr(0) is None
    assert seconds_to_tpr(-5) is None
    assert seconds_to_tpr(None) is None


def test_linkedin_geo_search_params_match_user_target():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    params = LinkedInAdapter._build_search_params(
        keywords="backend", location=None, start=0,
        geo_id=92000000, distance=25, tpr_seconds=43200,
    )
    assert params["geoId"] == 92000000
    assert params["distance"] == 25
    assert params["f_TPR"] == "r43200"
    assert params["keywords"] == "backend"
    assert "location" not in params  # geo mode ignores location strings


def test_linkedin_geo_tasks_ignore_locations():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    tasks = LinkedInAdapter._build_search_tasks(
        ["backend", "fullstack", "software"], ["Remote", "Egypt"], 2, geo_id=92000000
    )
    assert len(tasks) == 3 * 2  # queries x pages, no location fan-out
    assert all(loc is None for _, loc, _ in tasks)


@pytest.mark.asyncio
async def test_linkedin_geo_fetch_hits_geo_url():
    from job_hunt.discovery.adapters.linkedin import LinkedInAdapter

    seen_urls = []

    async def mock_handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = LinkedInAdapter()
        await adapter.fetch(
            {"adapter": "linkedin", "queries": ["backend"], "geo_id": 92000000,
             "distance": 25, "tpr_seconds": 43200,
             "max_pages_per_query": 1, "target_jobs_count": 5},
            client,
        )

    assert seen_urls, "expected at least one request"
    url = seen_urls[0]
    assert "geoId=92000000" in url
    assert "distance=25" in url
    assert "f_TPR=r43200" in url
    assert "keywords=backend" in url


def test_bayt_adapter_matching():
    from job_hunt.discovery.adapters.bayt import BaytAdapter
    b = BaytAdapter()
    assert b.matches_url("https://www.bayt.com/en/egypt/jobs/backend-jobs-in-cairo/") is True
    assert b.matches_url("https://boards.greenhouse.io/stripe") is False


def test_naukri_adapter_matching():
    from job_hunt.discovery.adapters.naukri import NaukriAdapter
    n = NaukriAdapter()
    assert n.matches_url("https://www.naukri.com/backend-jobs") is True
    assert n.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_bayt_mock_fetch():
    import time
    from job_hunt.discovery.adapters import bayt as bayt_mod
    from job_hunt.discovery.adapters.bayt import BaytAdapter

    now = int(time.time())
    fresh = now - 3600
    old = now - 10 * 86400
    sample_html = f"""
    <html><body><ul>
      <li data-js-job data-job-id="111">
        <h2><a href="/en/egypt/jobs/backend-engineer-111/">Backend Engineer</a></h2>
        <div class="job-company-location-wrapper"><a href="/en/company/acme">Acme</a></div>
        <dt class="jb-label-location"><span>Cairo</span><span>Egypt</span></dt>
        <span data-automation-jobactivedate="{fresh}"></span>
      </li>
      <li data-js-job data-job-id="222">
        <h2><a href="/en/egypt/jobs/stale-role-222/">Stale Role</a></h2>
        <div class="job-company-location-wrapper">OldCo</div>
        <dt class="jb-label-location"><span>Riyadh</span><span>Saudi Arabia</span></dt>
        <span data-automation-jobactivedate="{old}"></span>
      </li>
      <li data-js-job><h2>Broken card without link</h2></li>
    </ul></body></html>
    """

    async def fake_fetch_text(url, params=None, headers=None, timeout=20):
        return 200, sample_html, url

    original = bayt_mod.fetch_text
    bayt_mod.fetch_text = fake_fetch_text
    try:
        async with httpx.AsyncClient() as client:
            adapter = BaytAdapter()
            postings = await adapter.fetch(
                {"adapter": "bayt", "queries": ["backend"], "locations": ["Cairo, Egypt"],
                 "max_pages_per_query": 1, "target_jobs_count": 10, "hours_old": 72},
                client,
            )
    finally:
        bayt_mod.fetch_text = original

    assert len(postings) == 1  # stale filtered by hours_old, broken skipped
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.company == "Acme"
    assert p.source == "bayt"
    assert p.external_id == "bayt-111"
    assert "111" in p.canonical_url


@pytest.mark.asyncio
async def test_naukri_mock_fetch():
    import time
    from job_hunt.discovery.adapters import naukri as naukri_mod
    from job_hunt.discovery.adapters.naukri import NaukriAdapter, generate_nkparam

    assert generate_nkparam("srp")  # non-empty RSA token

    now_ms = int(time.time() * 1000)
    payload = {
        "noOfJobs": 1,
        "jobDetails": [
            {
                "jobId": "555",
                "title": "Backend Developer",
                "companyName": "Flipkart",
                "staticUrl": "flipkart-jobs",
                "jdURL": "/job-listings/backend-developer-flipkart-555",
                "createdDate": now_ms - 3600 * 1000,
                "companyApplyJob": False,
                "jobType": "Full Time",
                "vacancy": 3,
                "placeholders": [
                    {"type": "location", "label": "Hybrid - Bengaluru"},
                    {"type": "salary", "label": "Not disclosed"},
                ],
                "salaryDetail": {"hideSalary": True},
                "tagsAndSkills": "Python,Django",
                "experienceText": "2-4 Yrs",
                "logoPathV3": "",
                "ambitionBoxData": {},
            }
        ],
    }

    async def fake_fetch_json(url, params=None, headers=None, timeout=20):
        assert headers and "nkparam" in headers
        return 200, payload, url

    original = naukri_mod.fetch_json_async
    naukri_mod.fetch_json_async = fake_fetch_json
    try:
        async with httpx.AsyncClient() as client:
            adapter = NaukriAdapter()
            postings = await adapter.fetch(
                {"adapter": "naukri", "queries": ["backend"], "locations": ["Bengaluru"],
                 "max_pages_per_query": 1, "target_jobs_count": 10, "hours_old": 72},
                client,
            )
    finally:
        naukri_mod.fetch_json_async = original

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Developer"
    assert p.company == "Flipkart"
    assert p.source == "naukri"
    assert p.external_id == "nk-555"
    assert "Python" in (p.description or "")


def test_indeed_adapter_matching():
    from job_hunt.discovery.adapters.indeed import IndeedAdapter
    assert IndeedAdapter().matches_url("https://eg.indeed.com/jobs?q=backend") is True
    assert IndeedAdapter().matches_url("https://boards.greenhouse.io/x") is False


def test_ziprecruiter_adapter_matching():
    from job_hunt.discovery.adapters.ziprecruiter import ZipRecruiterAdapter
    assert ZipRecruiterAdapter().matches_url("https://www.ziprecruiter.com/jobs-search") is True
    assert ZipRecruiterAdapter().matches_url("https://boards.greenhouse.io/x") is False


def test_glassdoor_adapter_matching():
    from job_hunt.discovery.adapters.glassdoor import GlassdoorAdapter
    assert GlassdoorAdapter().matches_url("https://www.glassdoor.com/Job/egypt-jobs.htm") is True
    assert GlassdoorAdapter().matches_url("https://boards.greenhouse.io/x") is False


def test_google_jobs_adapter_matching():
    from job_hunt.discovery.adapters.google_jobs import GoogleJobsAdapter
    assert GoogleJobsAdapter().matches_url("https://www.google.com/search?q=jobs") is True
    assert GoogleJobsAdapter().matches_url("https://boards.greenhouse.io/x") is False


@pytest.mark.asyncio
async def test_indeed_mock_fetch():
    from job_hunt.discovery.adapters.indeed import IndeedAdapter

    payload = {
        "data": {
            "jobSearch": {
                "results": [
                    {
                        "job": {
                            "key": "abc123",
                            "title": "Backend Engineer",
                            "datePublished": 1790000000000,
                            "description": {"html": "<p>Python backend role.</p>"},
                            "employer": {"name": "Acme", "relativeCompanyPageUrl": "/cmp/Acme"},
                            "location": {"city": "Cairo", "admin1Code": None, "countryCode": "EG"},
                            "attributes": [],
                            "compensation": {},
                        }
                    }
                ],
                "pageInfo": {"nextCursor": None},
            }
        }
    }

    async def mock_handler(request):
        assert request.url.path == "/graphql"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = IndeedAdapter()
        postings = await adapter.fetch(
            {"adapter": "indeed", "queries": ["backend"], "locations": ["Cairo, Egypt"],
             "country": "Egypt", "max_pages_per_query": 1, "target_jobs_count": 10},
            client,
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.company == "Acme"
    assert p.source == "indeed"
    assert p.external_id == "in-abc123"
    assert "abc123" in p.canonical_url


@pytest.mark.asyncio
async def test_ziprecruiter_mock_fetch():
    import base64 as _b64
    from job_hunt.discovery.adapters.ziprecruiter import ZipRecruiterAdapter, page_values

    target = "https://acme.com/apply/123"
    token = _b64.urlsafe_b64encode(b"X" + bytes([len(target)]) + target.encode()).decode().rstrip("=")
    job = {
        "listingKey": "z9",
        "title": "Backend Developer",
        "status": {"postedAtUtc": "2026-09-30T10:00:00Z"},
        "rawCanonicalZipJobPageUrl": "/jobs/1",
        "company": {"name": "Acme"},
        "companyUrl": "/c/Acme",
        "location": {"city": "Austin", "stateCode": "TX", "countryCode": "US"},
        "employmentTypes": [{"name": "NAME_FULL_TIME"}],
        "locationTypes": [{"name": "REMOTE"}],
        "pay": {"metadata": {"visible": True}, "interval": "PAY_INTERVAL_YEAR",
                "min": 120000, "max": 150000, "currency": "PAY_CURRENCY_USD"},
        "applyButtonConfig": {"externalApplyUrl": "https://www.ziprecruiter.com/rd?match_token=" + token},
        "companyLogo": {},
    }
    inner = '{"jobKeysMap":{"k1":' + __import__("json").dumps(job) + '},"jobCount":1}'
    html = 'self.__next_f.push([1,"' + inner.replace('"', '\\"') + '"])'

    cards, count = page_values(html, "jobKeysMap", "jobCount")
    assert count == 1

    from job_hunt.discovery.adapters import ziprecruiter as zr_mod

    async def fake_fetch(url, params=None, headers=None, timeout=20):
        return 200, html, url

    original = zr_mod.fetch_text
    zr_mod.fetch_text = fake_fetch
    try:
        async with httpx.AsyncClient() as client:
            adapter = ZipRecruiterAdapter()
            postings = await adapter.fetch(
                {"adapter": "ziprecruiter", "queries": ["backend"], "locations": ["Austin, TX"],
                 "max_pages_per_query": 1, "target_jobs_count": 10},
                client,
            )
    finally:
        zr_mod.fetch_text = original

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Developer"
    assert p.source == "ziprecruiter"
    assert p.external_id == "zr-z9"  # real listing key preferred over map key
    assert p.metadata.get("job_url_direct") == target


@pytest.mark.asyncio
async def test_glassdoor_mock_fetch():
    from job_hunt.discovery.adapters import glassdoor as gd_mod
    from job_hunt.discovery.adapters.glassdoor import GlassdoorAdapter

    listing = {
        "jobview": {
            "job": {"listingId": 777, "jobTitleText": "Backend Engineer"},
            "header": {
                "employerNameFromSearch": "Acme",
                "employer": {"id": 99},
                "locationName": "Cairo, Egypt",
                "locationType": "",
                "ageInDays": 0,
            },
        }
    }
    body = [{"data": {"jobListings": {"jobListings": [listing], "paginationCursors": []}}}]

    async def fake_post(url, json=None, headers=None, timeout=20):
        return 200, body, url

    async def fake_get(url, params=None, headers=None, timeout=20):
        return 200, "[]", url

    orig_post, orig_get = gd_mod.post_json, gd_mod.fetch_text
    gd_mod.post_json = fake_post
    gd_mod.fetch_text = fake_get
    try:
        async with httpx.AsyncClient() as client:
            adapter = GlassdoorAdapter()
            postings = await adapter.fetch(
                {"adapter": "glassdoor", "queries": ["backend"], "locations": ["Remote"],
                 "remote_only": True, "max_pages_per_query": 1, "target_jobs_count": 10},
                client,
            )
    finally:
        gd_mod.post_json, gd_mod.fetch_text = orig_post, orig_get

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.source == "glassdoor"
    assert p.external_id == "gd-777"


@pytest.mark.asyncio
async def test_google_jobs_mock_fetch():
    import json as _json
    from job_hunt.discovery.adapters.google_jobs import GoogleJobsAdapter, extract_infos

    info = (
        ["Backend Engineer", "Acme", "Cairo, Egypt"]
        + [[["https://acme.com/jobs/1"]]]
        + [None] * 8
        + ["2 days ago"]
        + [None] * 6
        + ["Python role. Remote friendly."]
        + [None] * 8
        + ["g123"]
    )
    assert len(info) == 29
    assert extract_infos({"jobs": [info]}) == [info]

    page = (
        _json.dumps({"jobs": [info], "other": [1, 2, 3]})
        + ' data-async-fc="cursor1"'
    )

    async def mock_handler(request):
        return httpx.Response(200, text=page)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = GoogleJobsAdapter()
        postings = await adapter.fetch(
            {"adapter": "google", "queries": ["backend"], "locations": ["Cairo"],
             "max_pages_per_query": 1, "target_jobs_count": 10},
            client,
        )

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Backend Engineer"
    assert p.company == "Acme"
    assert p.source == "google"
    assert p.external_id == "go-g123"
    assert p.metadata.get("remote_flag") is True
