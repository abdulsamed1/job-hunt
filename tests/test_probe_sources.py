import asyncio
import importlib.util
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "probe_sources.py"


def _load():
    spec = importlib.util.spec_from_file_location("probe_sources", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _posting(posted_at):
    from job_hunt.models import JobPosting

    return JobPosting(
        external_id="1",
        source="s",
        source_name="c",
        title="t",
        company="c",
        raw_url="https://example.com/1",
        canonical_url="https://example.com/1",
        canonical_url_hash="h",
        role_fingerprint="f",
        content_hash="ch",
        location="Remote",
        description="d",
        posted_at=posted_at,
    )


def test_probe_ledger_is_name_keyed_with_verdicts(tmp_path):
    mod = _load()
    now = datetime.now(timezone.utc)
    probes = [
        mod.SourceProbe(name="alpha", kind="rss", industry="crypto", attempts=3, yielded=2,
                        last_yield_at=now, probed_at=now),
        mod.SourceProbe(name="beta", kind="greenhouse", industry="software", attempts=2, yielded=0,
                        last_yield_at=None, probed_at=now),
    ]
    ledger_path = tmp_path / "source_probe.json"
    mod.write_ledger(probes, ledger_path)
    data = json.loads(ledger_path.read_text())
    assert set(data.keys()) == {"alpha", "beta"}
    for v in data.values():
        assert set(v.keys()) == {"attempts", "yielded", "last_yield_at", "probed_at",
                                 "verdict", "kind", "industry", "error", "fatal"}
        assert v["verdict"] in {"live", "dead", "flaky"}


def test_verdict_live_iff_yielded():
    mod = _load()
    now = datetime.now(timezone.utc)
    live = mod.SourceProbe(name="a", kind="rss", industry="general", attempts=1, yielded=1, last_yield_at=now)
    assert live.verdict == "live"
    # A source that returned jobs is live regardless of how long ago they were posted:
    # liveness is about whether the source answers, not about feed freshness.
    stale = mod.SourceProbe(name="b", kind="rss", industry="general", attempts=1, yielded=1,
                            last_yield_at=now - timedelta(days=8))
    assert stale.verdict == "live"
    none = mod.SourceProbe(name="c", kind="rss", industry="general", attempts=0, yielded=0, last_yield_at=None)
    assert none.verdict != "live"
    flaky = mod.SourceProbe(name="d", kind="rss", industry="general", attempts=0, yielded=1, last_yield_at=now)
    assert flaky.verdict == "flaky"


def test_errored_source_is_flaky_and_a_clean_zero_is_dead():
    mod = _load()
    # A failure is not proof the board is gone.
    assert mod.classify_verdict(attempts=1, yielded=0, error="HTTP 500") == "flaky"
    assert mod.classify_verdict(attempts=1, yielded=0) == "dead"
    assert mod.classify_verdict(attempts=1, yielded=2) == "live"


def test_a_board_that_answers_404_is_dead_not_flaky():
    """404/410 is the board saying "I am gone" -- a clean zero, not a failure."""
    mod = _load()
    import httpx

    request = httpx.Request("GET", "https://boards-api.greenhouse.io/v1/boards/gone/jobs")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("404 Not Found", request=request, response=response)
    assert mod._is_gone(exc) is True
    assert mod.classify_verdict(attempts=1, yielded=0, error="HTTPStatusError: 404",
                                 fatal=mod._is_gone(exc)) == "dead"
    # A timeout is not proof of anything: flaky.
    timeout = httpx.ConnectTimeout("timed out", request=request)
    assert mod._is_gone(timeout) is False
    assert mod.classify_verdict(attempts=1, yielded=0, error="ConnectTimeout") == "flaky"


def test_last_yield_at_is_the_newest_posting_date_not_the_probe_clock():
    mod = _load()
    postings = [
        _posting("Mon, 05 Oct 2026 10:00:00 GMT"),
        _posting("Tue, 06 Oct 2026 10:00:00 GMT"),
        _posting("not-a-date"),   # unparseable dates are skipped
        _posting(None),
    ]
    newest = mod._newest_posted_at(postings)
    assert newest == datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def test_ledger_separates_the_probe_time_from_the_newest_posting():
    mod = _load()
    posted = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    probed = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    p = mod.SourceProbe(name="x", kind="rss", industry="general", attempts=1, yielded=3,
                        last_yield_at=posted, probed_at=probed)
    entry = mod.build_ledger([p])["x"]
    assert entry["last_yield_at"].startswith("2026-10-01")
    assert entry["probed_at"].startswith("2026-10-19")


def test_probe_records_an_errored_source_instead_of_dropping_it():
    mod = _load()

    class BoomRegistry:
        async def scan_source(self, entry, client, semaphore, **kwargs):
            raise RuntimeError("upstream exploded")

    async def run():
        async with httpx.AsyncClient() as client:
            return await mod._probe_one(
                {"name": "boom", "adapter": "feed", "url": "https://example.com/feed",
                 "industry": "general"},
                client, asyncio.Semaphore(1), 0, True, registry=BoomRegistry(),
            )

    probe = asyncio.run(run())
    assert probe.attempts == 1
    assert probe.yielded == 0
    assert "upstream exploded" in probe.error
    assert probe.verdict == "flaky"


def test_apply_marks_an_errored_source_unknown_not_stale_live(tmp_path):
    mod = _load()
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        "linkedin_enabled: false\nsources:\n"
        "- adapter: greenhouse\n  name: acme\n  url: https://boards.greenhouse.io/acme\n"
        "  industry: software\n  status: live\n"
    )
    probes = [mod.SourceProbe(name="acme", kind="greenhouse", industry="software", attempts=1,
                              yielded=0, last_yield_at=None, error="HTTP 500")]
    mod.apply_verdicts(probes, sources_path=sources_path)
    data = yaml.safe_load(sources_path.read_text())
    assert data["sources"][0]["status"] == "unknown"


def test_geography_gate_is_on_by_default():
    """The shipped rule keeps the geography gate on: only usable roles count."""
    mod = _load()
    params = inspect.signature(mod._probe_all_async).parameters
    assert params["remote"].default is True


def test_probe_help_states_the_liveness_rule():
    mod = _load()
    help_text = mod._build_parser().format_help()
    low = " ".join(help_text.lower().split())
    assert "remote anywhere" in low
    assert "placed in egypt" in low
    assert "at any age" in low
    assert "24h window gates the feed" in low
    assert "flaky" in low


@pytest.mark.asyncio
async def test_probe_counts_an_egypt_onsite_board_as_live(monkeypatch):
    """An Egypt on-site board is wanted by the product, so it must not read as
    dead for having no remote roles (the old remote-only measure did that)."""
    mod = _load()
    from job_hunt.models import JobPosting

    def _job(location: str) -> JobPosting:
        return JobPosting(
            source="web:wuzzuf", title="Backend Engineer", company="Acme",
            raw_url=f"https://wuzzuf.net/jobs/{abs(hash(location))}",
            canonical_url=f"https://wuzzuf.net/jobs/{abs(hash(location))}",
            canonical_url_hash=f"h-{abs(hash(location))}",
            role_fingerprint=f"rf-{abs(hash(location))}",
            content_hash=f"c-{abs(hash(location))}",
            location=location,
        )

    class FakeRegistry:
        async def scan_source(self, entry, client, semaphore, raise_on_error=False):
            return [_job("Cairo, Egypt"), _job("Austin, TX")]

    probe = await mod._probe_one(
        {"adapter": "web", "name": "wuzzuf", "url": "https://wuzzuf.net/jobs/egypt"},
        client=None, semaphore=asyncio.Semaphore(1), hours_old=0, remote=True,
        registry=FakeRegistry(),
    )
    assert probe.verdict == "live"
    assert probe.yielded == 1  # the Austin on-site posting is not usable


def test_suggest_hourly_caps_crypto_orgs_and_feeds():
    mod = _load()
    now = datetime.now(timezone.utc)
    probes = []
    for i in range(5):
        probes.append(mod.SourceProbe(name=f"cg{i}", kind="greenhouse", industry="crypto",
                                     attempts=1, yielded=10 - i, last_yield_at=now))
    for i in range(5):
        probes.append(mod.SourceProbe(name=f"cf{i}", kind="rss", industry="crypto",
                                     attempts=1, yielded=10 - i, last_yield_at=now))
    chosen = mod.suggest_hourly(probes, limit=25, max_crypto_orgs=2, max_crypto_feeds=3)
    crypto_orgs = sum(1 for n in chosen if n.startswith("cg"))
    crypto_feeds = sum(1 for n in chosen if n.startswith("cf"))
    assert crypto_orgs <= 2
    assert crypto_feeds <= 3


def test_suggest_hourly_includes_non_crypto_software_sources():
    mod = _load()
    now = datetime.now(timezone.utc)
    probes = [
        mod.SourceProbe(name="crypto1", kind="greenhouse", industry="crypto", attempts=1, yielded=100, last_yield_at=now),
    ]
    probes.append(mod.SourceProbe(name="soft1", kind="greenhouse", industry="software", attempts=1, yielded=1, last_yield_at=now))
    chosen = mod.suggest_hourly(probes, limit=25, max_crypto_orgs=2, max_crypto_feeds=3)
    assert "soft1" in chosen


def test_apply_flips_a_dead_source_back_to_live_when_it_yields_again(tmp_path):
    mod = _load()
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        "linkedin_enabled: false\nsources:\n"
        "- adapter: greenhouse\n  name: acme\n  url: https://boards.greenhouse.io/acme\n  industry: software\n  status: dead\n"
    )
    now = datetime.now(timezone.utc)
    probes = [mod.SourceProbe(name="acme", kind="greenhouse", industry="software", attempts=1, yielded=5, last_yield_at=now)]
    mod.apply_verdicts(probes, sources_path=sources_path)
    data = yaml.safe_load(sources_path.read_text())
    assert data["sources"][0]["status"] == "live"


def test_registry_skips_dead_sources_unless_asked(tmp_path):
    """`status: dead` sources are skipped locally; the probe still measures them."""
    from job_hunt.discovery.registry import SourceRegistry

    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        "linkedin_enabled: false\nsources:\n"
        "- adapter: greenhouse\n  name: alive\n  url: https://boards.greenhouse.io/alive\n  status: live\n"
        "- adapter: greenhouse\n  name: gone\n  url: https://boards.greenhouse.io/gone\n  status: dead\n"
    )
    registry = SourceRegistry()
    live = registry.load_sources_file(sources_path)
    assert [s["name"] for s in live] == ["alive"]
    with_dead = registry.load_sources_file(sources_path, include_dead=True)
    assert {s["name"] for s in with_dead} == {"alive", "gone"}
