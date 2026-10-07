from job_hunt.discovery import robots


def test_robots_parsing_rules():
    assert robots.group_allows("User-agent: *\nDisallow: /jobs\n", "/jobs", "TestBot") is False
    assert robots.group_allows("User-agent: *\nDisallow: /jobs\n", "/about", "TestBot") is True
    assert robots.group_allows("", "/", "TestBot") is None  # empty/unreadable is not permission


from job_hunt.discovery.adapters.freehire import FreehireAdapter
from job_hunt.discovery.adapters.bdjobs import BdJobsAdapter


def test_adapter_ids_registered():
    from job_hunt.discovery.registry import SourceRegistry
    reg = SourceRegistry()
    assert "freehire" in reg._adapter_map and "bdjobs" in reg._adapter_map


def test_freehire_parses_full_description():
    import asyncio
    from unittest.mock import AsyncMock
    ad = FreehireAdapter()
    client = AsyncMock()
    resp = AsyncMock()
    resp.status_code = 200
    html = "<p><strong><span>Description</span></strong></p><p>" + ("Backend Python PostgreSQL remote role. " * 30) + "</p>"
    resp.json.return_value = {
        "data": [
            {
                "public_slug": "backend-engineer-acme-btehyuk3",
                "source": "acme",
                "manually_added": False,
                "external_id": ":180950",
                "url": "https://portal.acme.org/jobs/180950/backend-engineer?utm_source=freehire.me",
                "title": "Backend Engineer",
                "company": "Acme",
                "company_slug": "acme",
                "location": "Remote",
                "description": html,
            }
        ],
        "meta": {"total": 1},
    }
    client.get.return_value = resp
    jobs = asyncio.run(ad.fetch({"url": "https://freehire.me/api/v1/agent/jobs/search", "queries": ["backend"], "locations": ["Remote"]}, client))
    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].raw_url == "https://portal.acme.org/jobs/180950/backend-engineer?utm_source=freehire.me"
    assert len(jobs[0].description) > 100


def test_bdjobs_parses_getjobsearch_response():
    import asyncio
    from unittest.mock import AsyncMock
    ad = BdJobsAdapter()
    client = AsyncMock()

    def _job(job_id, title, company, premium=False):
        job = {
            "Jobid": job_id,
            "jobTitle": title,
            "companyName": company,
            "location": "Dhaka",
            "publishDate": "2026-10-06T00:00:00Z",
            "deadline": "2026-11-06T00:00:00Z",
            "JobType": "FullTime",
            "WorkPlace": "Office",
            "Salary": "Tk. 50000 - 80000 (Monthly)",
        }
        if premium:
            job["isPremium"] = True
        return job

    payload = {
        "message": "Success",
        "data": [_job("111", "Backend Engineer", "Acme Ltd")],
        "premiumData": [_job("222", "Fullstack Developer", "Beta Ltd", premium=True)],
        "common": {"totalpages": 1},
    }
    resp = AsyncMock()
    resp.status_code = 200
    resp.json.return_value = payload
    client.get.return_value = resp
    jobs = asyncio.run(ad.fetch({
        "url": "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch",
        "queries": ["backend"],
        "locations": ["Dhaka, Bangladesh"],
        "max_pages_per_query": 1,
    }, client))
    assert len(jobs) == 2
    by_id = {j.external_id: j for j in jobs}
    assert by_id["bdjobs-111"].title == "Backend Engineer"
    assert by_id["bdjobs-111"].company == "Acme Ltd"
    assert by_id["bdjobs-111"].raw_url == "https://bdjobs.com/h/details/111"
    assert by_id["bdjobs-222"].title == "Fullstack Developer"
    assert by_id["bdjobs-222"].company == "Beta Ltd"
    assert by_id["bdjobs-222"].raw_url == "https://bdjobs.com/h/details/222"
    for job in jobs:
        assert job.title and job.company and job.raw_url and job.description


def test_bdjobs_search_params_use_keyword_and_location_code():
    ad = BdJobsAdapter()
    params = ad._search_params("backend", "Dhaka, Bangladesh", 1, 72)
    assert params["keyword"] == "backend"
    assert params["location"] == 14
    assert params["postedWithin"] == 4
    assert params["pg"] == 1
    widened = ad._search_params("backend", "Bangladesh", 1, 72)
    assert "location" not in widened


def test_indeed_direct_url_and_compensation():
    from job_hunt.discovery.adapters.indeed import IndeedAdapter
    ad = IndeedAdapter()
    job = {"key": "1", "title": "Backend Engineer", "companyName": {"text": "Acme"},
           "recruit": {"viewJobUrl": "https://acme.com/jobs/1"},
           "compensation": {"baseSalary": {"range": {"min": 100000, "max": 140000}, "unitOfWork": "YEAR"}}}
    posting = ad._parse_job(job, {})
    assert posting.job_url_direct == "https://acme.com/jobs/1"
    assert posting.salary_source == "stated"


def test_indeed_missing_key_returns_none():
    from job_hunt.discovery.adapters.indeed import IndeedAdapter
    ad = IndeedAdapter()
    job = {"title": "Backend Engineer",
           "recruit": {"viewJobUrl": "https://acme.com/jobs/1"},
           "compensation": {"baseSalary": {"range": {"min": 100000, "max": 140000}}}}
    assert ad._parse_job(job, {}) is None


def test_indeed_estimated_compensation_inferred():
    from job_hunt.discovery.adapters.indeed import IndeedAdapter
    ad = IndeedAdapter()
    job = {"key": "2", "title": "Backend Engineer",
           "recruit": {"viewJobUrl": "https://acme.com/jobs/2"},
           "compensation": {"estimated": {"currencyCode": "USD", "unitOfWork": "YEAR",
                                          "range": {"min": 90000, "max": 120000}}}}
    posting = ad._parse_job(job, {})
    assert posting.salary_min == 90000
    assert posting.salary_max == 120000
    assert posting.salary_source == "inferred"
    currency_only = {"key": "3", "title": "Backend Engineer",
                     "compensation": {"estimated": {"currencyCode": "USD"}}}
    posting2 = ad._parse_job(currency_only, {})
    assert posting2.salary_min is None and posting2.salary_max is None
    assert posting2.salary_source is None
