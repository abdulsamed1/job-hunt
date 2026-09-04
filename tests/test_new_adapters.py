"""Unit tests for Workable and BambooHR discovery adapters."""

import pytest
import httpx
from job_hunt.discovery.adapters.workable import WorkableAdapter
from job_hunt.discovery.adapters.bamboohr import BambooHRAdapter


def test_workable_adapter_matches_url():
    adapter = WorkableAdapter()
    assert adapter.matches_url("https://apply.workable.com/acme-corp/") is True
    assert adapter.matches_url("https://jobs.lever.co/acme") is False


def test_bamboohr_adapter_matches_url():
    adapter = BambooHRAdapter()
    assert adapter.matches_url("https://acme.bamboohr.com/careers") is True
    assert adapter.matches_url("https://boards.greenhouse.io/stripe") is False


@pytest.mark.asyncio
async def test_workable_mock_fetch():
    mock_payload = {
        "name": "Acme Fintech",
        "jobs": [
            {
                "title": "Senior Distributed Backend Engineer",
                "shortcode": "ACME101",
                "url": "https://apply.workable.com/acme-fintech/j/ACME101/",
                "city": "London",
                "country": "United Kingdom",
                "telecommuting": True,
                "description": "<p>Build high-concurrency ledger systems.</p>",
                "published_on": "2026-09-04",
            }
        ],
    }

    async def mock_handler(request):
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = WorkableAdapter()
        postings = await adapter.fetch({"url": "https://apply.workable.com/acme-fintech/"}, client)

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Senior Distributed Backend Engineer"
    assert p.company == "Acme Fintech"
    assert "Remote" in p.location
    assert p.external_id == "ACME101"
    assert p.source == "workable"


@pytest.mark.asyncio
async def test_bamboohr_mock_fetch():
    mock_payload = {
        "result": [
            {
                "id": "505",
                "jobOpeningName": "Full Stack Platform Engineer",
                "location": {"city": "Berlin", "state": "BE"},
                "isRemote": True,
            }
        ]
    }

    async def mock_handler(request):
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = BambooHRAdapter()
        postings = await adapter.fetch({"url": "https://cyberstartup.bamboohr.com/careers"}, client)

    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Full Stack Platform Engineer"
    assert p.company == "Cyberstartup"
    assert "Remote" in p.location
    assert p.external_id == "505"
    assert p.source == "bamboohr"
