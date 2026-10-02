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
