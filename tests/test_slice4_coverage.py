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
    resp.json.return_value = {"jobs": [{"id": "1", "title": "Backend Engineer", "company": "Acme", "location": "Remote", "url": "https://x/1", "description": "Python and PostgreSQL " * 50}]}
    resp.raise_for_status.return_value = None
    client.get.return_value = resp
    jobs = asyncio.run(ad.fetch({"url": "https://freehire.me/api/v1/agent/jobs/search", "queries": ["backend"], "locations": ["Remote"]}, client))
    assert jobs and len(jobs[0].description) > 400
