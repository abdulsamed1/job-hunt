"""The Worker source shard must stay generated from config/sources.yaml.

Guards the "cover every source on Workers" requirement: no hand-kept subset,
and no source silently dropped between Python and the Worker.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "worker" / "scripts" / "gen_sources.py"
GENERATED_TS = ROOT / "worker" / "src" / "sources.generated.ts"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_sources", GEN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_file_is_up_to_date():
    result = subprocess.run(
        [sys.executable, str(GEN), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_yaml_source_is_accounted_for():
    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text()) or {}
    yaml_total = len(data.get("sources") or [])
    worker, python_only, stats = _load_generator().build()
    assert stats["yaml_total"] == yaml_total
    # Nothing may be lost: every source runs on the Worker or is explicitly
    # declared python-only (TLS impersonation / RSA / browser required).
    assert len(worker) + len(python_only) == yaml_total
    assert len(worker) == len({s["name"] for s in worker}), "duplicate source names"


def test_worker_shard_covers_full_scale():
    """Requirement: full coverage inside Cloudflare, not a 19-entry subset."""
    worker, _python_only, _stats = _load_generator().build()
    assert len(worker) >= 200, f"only {len(worker)} worker sources"


def test_ats_and_feed_sources_are_all_worker_runnable():
    """Every ATS board + feed in the YAML must run on Workers."""
    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text()) or {}
    worker_names = {s["name"] for s in _load_generator().build()[0]}
    for entry in data["sources"]:
        if entry["adapter"] in {"greenhouse", "lever", "ashby", "feed", "smartrecruiters", "ashby-index",
                                "teamtailor", "recruitee", "personio"}:
            assert entry["name"] in worker_names, f"{entry['name']} ({entry['adapter']}) missing from Worker"


def test_slice5_tenant_boards_registered_with_explicit_adapters():
    """Slice 5 proven tenants: one entry each, explicit adapter keys (their
    hosts are not in the ATS_APEXES allowlist), all Worker-runnable."""
    expected = {
        "teamtailor-career": "teamtailor",
        "teamtailor-softwarefinder-na": "teamtailor",
        "recruitee-make": "recruitee",
        "recruitee-happeo": "recruitee",
        "recruitee-radix": "recruitee",
        "personio-vivid": "personio",
    }
    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text()) or {}
    by_name = {e["name"]: e for e in data["sources"]}
    worker, python_only, _stats = _load_generator().build()
    worker_names = {s["name"] for s in worker}
    python_only_names = {s["name"] for s in python_only}
    for name, adapter in expected.items():
        assert name in by_name, f"{name} missing from sources.yaml"
        assert by_name[name]["adapter"] == adapter, f"{name} lacks explicit adapter key"
        assert name in worker_names, f"{name} not Worker-runnable"
        assert name not in python_only_names, f"{name} wrongly python-only"


def test_python_only_sources_are_benign():
    """python-only must stay limited to adapters Workers genuinely cannot do."""
    allowed = {"bayt", "naukri", "web"}
    for record in _load_generator().build()[1]:
        assert record["adapter"] in allowed, record


def test_ats_sources_carry_org_slug():
    for record in _load_generator().build()[0]:
        if record["kind"] in {"greenhouse", "lever", "ashby", "smartrecruiters"}:
            assert record.get("org"), record


def test_linkedin_standing_target_preserved():
    worker, _p, _s = _load_generator().build()
    li = [s for s in worker if s["kind"] == "linkedin"]
    assert len(li) == 1
    assert set(li[0]["queries"]) == {"backend", "fullstack", "software"}
    assert li[0]["tprSeconds"] == 43200, "12-hour TPR window is the standing target"


def test_cadence_keeps_hourly_shard_small():
    """The hourly shard must stay far below the queue batch cap."""
    worker, _p, _s = _load_generator().build()
    hourly = [s for s in worker if s["cadence"] == "hourly"]
    assert 0 < len(hourly) <= 40, len(hourly)


def test_generated_ts_is_committed_and_imported():
    assert GENERATED_TS.exists()
    index = (ROOT / "worker" / "src" / "index.ts").read_text()
    assert 'from "./sources.generated.js"' in index
    # no leftover hardcoded table that would shadow the generated shard
    assert "const SOURCES: SourceDef[] = GENERATED_SOURCES;" in index
