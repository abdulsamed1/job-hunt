"""Unit tests for scripts/verify_deploy.py pure functions (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_deploy.py"
    spec = importlib.util.spec_from_file_location("verify_deploy", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_probe_unknown_source_is_failure():
    mod = _load()
    kind, msg = mod.classify_probe("nope", 404, {})
    assert kind == "fail" and "nope" in msg


def test_classify_probe_transport_errors_are_failures():
    mod = _load()
    assert mod.classify_probe("x", 502, {})[0] == "fail"
    assert mod.classify_probe("x", -1, {})[0] == "fail"


def test_classify_probe_inconclusive_is_warning_not_failure():
    mod = _load()
    kind, _ = mod.classify_probe("x", 200, {"inconclusive": True, "error": "slow"})
    assert kind == "warn"


def test_classify_probe_empty_but_clean_is_warning():
    mod = _load()
    kind, _ = mod.classify_probe("x", 200, {"raw": 10, "fresh_12h": 0, "remote_fresh": 0})
    assert kind == "warn"


def test_classify_probe_yielding_is_ok():
    mod = _load()
    kind, msg = mod.classify_probe("x", 200, {"raw": 10, "fresh_12h": 5, "remote_fresh": 3})
    assert (kind, msg) == ("ok", "")
