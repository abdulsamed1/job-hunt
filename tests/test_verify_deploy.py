"""Unit tests for scripts/verify_deploy.py pure functions (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

VALID_POINTER = "a" * 64


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_deploy.py"
    spec = importlib.util.spec_from_file_location("verify_deploy", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _health(**over):
    base = {
        "ok": True,
        "live_apply": False,
        "notification_events": 0,
        "master_resume_sha": VALID_POINTER,
    }
    base.update(over)
    return base


def test_classify_probe_unknown_source_is_failure():
    mod = _load()
    kind, msg = mod.classify_probe("nope", 404, {})
    assert kind == "fail" and "nope" in msg


def test_classify_probe_transport_errors_are_failures():
    mod = _load()
    assert mod.classify_probe("x", 502, {})[0] == "fail"
    assert mod.classify_probe("x", -1, {})[0] == "fail"
    assert mod.classify_probe("x", 500, {})[0] == "fail"
    assert mod.classify_probe("x", 503, {})[0] == "fail"


def test_classify_probe_inconclusive_is_warning_not_failure():
    mod = _load()
    kind, _ = mod.classify_probe("x", 200, {"inconclusive": True, "error": "slow"})
    assert kind == "warn"


def test_classify_probe_empty_but_clean_is_warning():
    mod = _load()
    kind, _ = mod.classify_probe("x", 200, {"raw": 10, "fresh_24h": 0, "remote_fresh": 0})
    assert kind == "warn"


def test_classify_probe_egypt_onsite_board_is_ok():
    mod = _load()
    # Fresh postings, none remote, all usable under the target-geo policy:
    # an Egypt on-site board must not read as empty.
    kind, msg = mod.classify_probe(
        "x", 200, {"raw": 66, "fresh_24h": 66, "remote_fresh": 0, "usable_fresh": 66}
    )
    assert (kind, msg) == ("ok", "")


def test_classify_probe_falls_back_to_remote_fresh_on_older_deploys():
    mod = _load()
    kind, _ = mod.classify_probe("x", 200, {"raw": 10, "fresh_24h": 5, "remote_fresh": 0})
    assert kind == "warn"


def test_classify_probe_yielding_is_ok():
    mod = _load()
    kind, msg = mod.classify_probe("x", 200, {"raw": 10, "fresh_24h": 5, "remote_fresh": 3})
    assert (kind, msg) == ("ok", "")


def test_check_health_accepts_ledger_present_and_live_off():
    mod = _load()
    assert mod.check_health(_health()) == []
    assert mod.check_health(_health(notification_events=42)) == []


def test_check_health_flags_live_apply():
    mod = _load()
    failures = mod.check_health(_health(live_apply=True))
    assert len(failures) == 1 and "live_apply" in failures[0]


def test_check_health_flags_missing_ledger():
    mod = _load()
    failures = mod.check_health(_health(notification_events=None))
    assert len(failures) == 1 and "notification_events" in failures[0]
    # An old deploy without the field at all is equally a failure.
    old = _health()
    del old["notification_events"]
    assert mod.check_health(old) != []


def test_check_health_accepts_valid_pointer():
    mod = _load()
    assert mod.check_health(_health()) == []


def test_check_health_flags_missing_pointer():
    mod = _load()
    failures = mod.check_health(_health(master_resume_sha=None))
    assert any("master_resume_sha" in f for f in failures)


def test_check_health_flags_malformed_pointer():
    mod = _load()
    failures = mod.check_health(_health(master_resume_sha="deadbeef"))
    assert any("master_resume_sha" in f for f in failures)
