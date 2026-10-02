from job_hunt.discovery.hosts import classify_host, is_spoof_like
from job_hunt.discovery.registry import SourceRegistry


def test_classify_host_spoof_matrix():
    assert classify_host("https://boards.greenhouse.io/stripe") == "ats"
    assert classify_host("https://jobs.ashbyhq.com/alchemy") == "ats"
    assert classify_host("https://evil-greenhouse.io/stripe") == "unverified"
    assert classify_host("https://job-boards.greenhouse.io.evil.com/x") == "unverified"
    assert classify_host("https://greenhouse.io@evil.com/x") == "unverified"
    assert classify_host("ftp://boards.greenhouse.io/x") == "unverified"
    assert classify_host("not a url") == "unverified"
    assert classify_host("https://acme.myworkdayjobs.com/jobs") == "ats"
    assert classify_host("https://evil-myworkdayjobs.com/x") == "unverified"


def test_registry_drops_spoofed_url():
    reg = SourceRegistry()
    entry = {"name": "evil", "url": "https://greenhouse.io@evil.com/x"}
    assert reg.resolve_adapter(entry) is None


def test_spoof_like_matrix():
    assert is_spoof_like("https://evil-greenhouse.io/stripe") is True
    assert is_spoof_like("https://job-boards.greenhouse.io.evil.com/x") is True
    assert is_spoof_like("https://www.linkedin.com/jobs/view/999") is False
    assert is_spoof_like("https://gitlab.com/jobs/3") is False
    assert is_spoof_like("https://boards.greenhouse.io/stripe") is False
    assert is_spoof_like("https://acme.myworkdayjobs.com/jobs") is False
    assert is_spoof_like("https://evil-myworkdayjobs.com/x") is True


from job_hunt.cli import build_parser
from job_hunt import orchestrator as orch_mod
import inspect


def test_twelve_hour_defaults():
    p = build_parser()
    assert p.parse_args(["scan"]).hours_old == 12
    assert p.parse_args(["run", "--once"]).hours_old == 12
    sig = inspect.signature(orch_mod.PipelineOrchestrator.run_discovery_stage)
    assert sig.parameters["hours_old"].default == 12


import os
from job_hunt.automation.env_scrub import scrubbed_env

def test_secret_values_removed_by_value(monkeypatch):
    monkeypatch.setenv("REZI_MCP_TOKEN", "sekrit-abc-123")
    monkeypatch.setenv("lowercase_alias", "sekrit-abc-123")
    env = scrubbed_env()
    assert "sekrit-abc-123" not in env.values()
    assert "REZI_MCP_TOKEN" not in env
    assert "lowercase_alias" not in env


import asyncio
from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.models import CandidateProfile

class FakeEl:
    def __init__(self, value): self._v = value
    async def input_value(self): return self._v

def test_verify_fill_detects_mismatch():
    eng = BrowserApplicationEngine.__new__(BrowserApplicationEngine)
    fields = [{"label": "Email", "el": FakeEl(""), "intended": "a@b.com", "required": True}]
    warnings = asyncio.run(eng.verify_fill(None, fields))
    assert any("Email" in w for w in warnings)


class _WiringInput:
    def __init__(self): self._v = ""
    async def input_value(self): return self._v
    async def fill(self, v): pass  # fill does NOT persist: simulates React-controlled drop
    async def get_attribute(self, name):
        return "Email" if name == "aria-label" else None
    async def evaluate(self, js): return ""


class _WiringCtx:
    def __init__(self, el): self._el = el
    async def query_selector_all(self, sel):
        return [self._el] if "textarea" in sel else []
    async def query_selector(self, sel): return None


def test_questionnaire_verify_blocks_unpersisted_fill():
    eng = BrowserApplicationEngine.__new__(BrowserApplicationEngine)
    eng.last_unanswered = []
    eng.last_confirmation_needed = []
    profile = CandidateProfile(
        full_name="Robin Diaz",
        first_name="Robin",
        last_name="Diaz",
        email="a@b.com",
        phone="+1-555-0188",
        location="Austin, TX",
        custom_answers={"Email": "a@b.com"},
    )
    asyncio.run(eng._fill_questionnaire(_WiringCtx(_WiringInput()), profile))
    assert any("Email" in label for label, _ in eng.last_unanswered)
    assert eng.submit_blocked_reason() is not None


def test_doctor_flags_sources_without_identity(tmp_path):
    import yaml

    from job_hunt.doctor import run_diagnostics

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(yaml.safe_dump({"sources": [
        {"name": "good-board", "url": "https://example.com/feed"},
        {"name": "careers-board", "careers_url": "https://example.com/careers"},
        {"name": "ghost-board"},
    ]}), encoding="utf-8")
    report = run_diagnostics(
        db_path=str(tmp_path / "d.db"),
        sources_path=str(yaml_path),
        profile_path=str(tmp_path / "missing.json"),
    )
    by_name = {c["name"]: c for c in report["checks"]}
    check = by_name["sources_identity"]
    assert check["ok"] is True  # warning-level: never fails the run
    assert "ghost-board" in check["detail"]
    assert "good-board" not in check["detail"]
    assert "careers-board" not in check["detail"]
