import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "probe_sources.py"


def _load():
    spec = importlib.util.spec_from_file_location("probe_sources", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_ledger_is_name_keyed_with_verdicts(tmp_path):
    mod = _load()
    now = datetime.now(timezone.utc)
    probes = [
        mod.SourceProbe(name="alpha", kind="rss", industry="crypto", attempts=3, yielded=2, last_yield_at=now),
        mod.SourceProbe(name="beta", kind="greenhouse", industry="software", attempts=2, yielded=0, last_yield_at=None),
    ]
    ledger_path = tmp_path / "source_probe.json"
    mod.write_ledger(probes, ledger_path)
    data = json.loads(ledger_path.read_text())
    assert set(data.keys()) == {"alpha", "beta"}
    for v in data.values():
        assert set(v.keys()) == {"attempts", "yielded", "last_yield_at", "verdict", "kind", "industry"}
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
