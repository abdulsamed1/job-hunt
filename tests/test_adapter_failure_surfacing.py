"""A failed fetch must surface, not be swallowed into an empty board.

The probe distinguishes "the board answered with nothing" (dead) from "the
board failed" (flaky). That distinction only works if the adapters raise on a
non-success HTTP status and let a whole-feed parse failure propagate -- a
swallowed 5xx would be recorded as a clean zero and the board would be dropped
from the Worker shard and the sweep.

The ordinary local scan path must still tolerate one failing source: the
registry isolates the error per source.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest

from job_hunt.discovery.adapters.feed import FeedAdapter
from job_hunt.discovery.adapters.recruitee import RecruiteeAdapter
from job_hunt.discovery.adapters.web import UniversalWebAdapter
from job_hunt.discovery.registry import SourceRegistry

ROOT = Path(__file__).resolve().parents[1]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_sources", ROOT / "scripts" / "probe_sources.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- feed ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_adapter_raises_on_a_server_error():
    async def handler(request):
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await FeedAdapter().fetch(
                {"name": "broken-feed", "url": "https://example.com/feed"}, client
            )


@pytest.mark.asyncio
async def test_feed_adapter_raises_on_a_404_board_that_is_gone():
    async def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await FeedAdapter().fetch(
                {"name": "gone-feed", "url": "https://example.com/feed"}, client
            )
    assert exc.value.response.status_code == 404


@pytest.mark.asyncio
async def test_feed_adapter_raises_when_the_body_is_neither_json_nor_xml():
    """A whole-feed parse failure is a failed attempt, not an empty board."""

    async def handler(request):
        return httpx.Response(200, text="not xml at all")

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="neither JSON nor parseable XML"):
            await FeedAdapter().fetch(
                {"name": "html-feed", "url": "https://example.com/feed"}, client
            )


@pytest.mark.asyncio
async def test_feed_adapter_survives_one_malformed_json_posting():
    """Per-item isolation in the JSON branch: one broken posting is skipped."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Broken Role",
                "company": "Acme",
                "url": "https://example.com/jobs/broken",
                "description": "Compensation: $103,913.30. - $120,000.00 per year",
            },
            {
                "id": 2,
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/2",
                "description": "Python role.",
            },
        ]
    }

    async def handler(request):
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        postings = await FeedAdapter().fetch(
            {"name": "json-feed", "url": "https://example.com/api"}, client
        )

    assert [p.title for p in postings] == ["Backend Engineer"]


@pytest.mark.asyncio
async def test_feed_adapter_still_returns_a_clean_empty_feed():
    """A feed that answers 200 with an empty job list is a clean zero."""
    async def handler(request):
        return httpx.Response(200, json={"jobs": []})

    async with _client(handler) as client:
        assert await FeedAdapter().fetch(
            {"name": "empty-feed", "url": "https://example.com/api"}, client
        ) == []


# --- web ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_adapter_raises_on_a_server_error():
    async def handler(request):
        return httpx.Response(503, text="nope")

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await UniversalWebAdapter().fetch(
                {"name": "broken-board", "url": "https://example.com/careers"}, client
            )


@pytest.mark.asyncio
async def test_web_adapter_raises_on_a_transport_failure():
    async def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await UniversalWebAdapter().fetch(
                {"name": "timeout-board", "url": "https://example.com/careers"}, client
            )


@pytest.mark.asyncio
async def test_web_adapter_still_returns_empty_for_a_page_without_job_cards():
    """A page that answers 200 but holds no job card is a genuine clean zero."""
    async def handler(request):
        return httpx.Response(200, text="<html><body>nothing here</body></html>")

    async with _client(handler) as client:
        assert await UniversalWebAdapter().fetch(
            {"name": "empty-board", "url": "https://example.com/careers"}, client
        ) == []


# --- recruitee ----------------------------------------------------------


@pytest.mark.asyncio
async def test_recruitee_adapter_raises_on_a_server_error():
    async def handler(request):
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await RecruiteeAdapter().fetch(
                {"name": "make", "url": "https://make.recruitee.com/api/offers/"}, client
            )


# --- the local scan path still tolerates one failing source --------------


@pytest.mark.asyncio
async def test_scan_source_isolates_a_failing_source():
    async def handler(request):
        if request.url.path == "/feed":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"jobs": []})

    entry = {"name": "broken-feed", "adapter": "feed", "url": "https://example.com/feed"}
    registry = SourceRegistry()
    async with _client(handler) as client:
        # The ordinary scan path swallows the error per source ...
        assert await registry.scan_source(entry, client, asyncio.Semaphore(1)) == []
        # ... while the probe path surfaces it.
        with pytest.raises(httpx.HTTPStatusError):
            await registry.scan_source(
                entry, client, asyncio.Semaphore(1), raise_on_error=True
            )


@pytest.mark.asyncio
async def test_discover_all_keeps_the_other_sources_when_one_fails(monkeypatch):
    """One failing board must not abort the whole scan."""
    payload = {
        "jobs": [
            {
                "id": 7,
                "title": "Backend Engineer",
                "company": "Acme",
                "url": "https://example.com/jobs/7",
                "description": "Python role.",
            }
        ]
    }

    async def handler(request):
        if request.url.path == "/broken":
            return httpx.Response(500, text="boom")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, json=payload)

    sources = [
        {"name": "broken-feed", "adapter": "feed", "url": "https://example.com/broken"},
        {"name": "good-feed", "adapter": "feed", "url": "https://example.com/good"},
    ]
    registry = SourceRegistry()
    # discover_all builds its own client: give it the mock transport.
    original_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    postings = await registry.discover_all(sources=sources)

    assert [p.title for p in postings] == ["Backend Engineer"]


# --- the probe turns the surfaced failure into a flaky verdict -----------


def test_probe_records_a_failing_feed_as_flaky_not_dead():
    mod = _load_probe()

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(500, text="boom")

    async def run():
        async with _client(handler) as client:
            return await mod._probe_one(
                {"name": "broken-feed", "adapter": "feed", "url": "https://example.com/feed",
                 "industry": "general"},
                client, asyncio.Semaphore(1), 0, True, registry=SourceRegistry(),
            )

    probe = asyncio.run(run())
    assert probe.attempts == 1
    assert probe.yielded == 0
    assert probe.error
    assert probe.fatal is False
    assert probe.verdict == "flaky"


def test_probe_records_a_gone_feed_as_dead():
    mod = _load_probe()

    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)

    async def run():
        async with _client(handler) as client:
            return await mod._probe_one(
                {"name": "gone-feed", "adapter": "feed", "url": "https://example.com/feed",
                 "industry": "general"},
                client, asyncio.Semaphore(1), 0, True, registry=SourceRegistry(),
            )

    probe = asyncio.run(run())
    assert probe.error
    assert probe.fatal is True
    assert probe.verdict == "dead"
