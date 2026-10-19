"""Docs must not drift from the code they describe.

Cheap guard against the failure mode where a safety rule or a source count is
fixed in code but the README keeps promising the old behavior.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "AGENTS.md", "DESIGN.md", "worker/README.md"]


def _text(name: str) -> str:
    return (ROOT / name).read_text()


def _all_docs() -> str:
    return "\n".join(_text(d) for d in DOCS)


def test_no_document_promises_hidden_cv_text():
    """Safety rule 3: no hidden text. Docs must not advertise the old behavior."""
    for name in DOCS:
        body = _text(name)
        # Allowed only as an explicit negation ("No white text, ...").
        for line in body.splitlines():
            for phrase in ("white text", "1pt", "invisible", "hidden text"):
                if phrase in line.lower():
                    assert re.search(r"\bno\b|\bnever\b|not\b", line.lower()), (
                        f"{name} advertises hidden-text CVs: {line.strip()[:90]}"
                    )


def test_stealth_claims_are_browser_only():
    for name in DOCS:
        for line in _text(name).splitlines():
            if "stealth" in line.lower():
                low = line.lower()
                assert any(w in low for w in ("playwright", "anti-bot", "bot detection", "navigator.webdriver")), (
                    f"{name}: 'stealth' must refer to browser evasion, not CV text: {line.strip()[:90]}"
                )


def test_source_counts_match_config_and_generator():
    gen = _text("worker/src/sources.generated.ts")
    m = re.search(r"yaml_total=(\d+) worker_supported=(\d+) python_only=(\d+)", gen)
    assert m, "generated file must carry the accounting header"
    total, worker, python_only = map(int, m.groups())
    cfg = (yaml.safe_load(_text("config/sources.yaml")) or {}).get("sources") or []
    assert len(cfg) == total, f"config has {len(cfg)} sources, generator thinks {total}"
    cfg = (yaml.safe_load(_text("config/sources.yaml")) or {}).get("sources") or []
    dead = sum(1 for e in cfg if (e or {}).get("status") == "dead")
    assert worker + python_only + dead == total, "sources must be fully accounted for"
    docs = _all_docs()
    for n in (total, worker, python_only):
        # Word-boundary matching: "150" must not be satisfied by "150ms" and
        # "27" must not be satisfied by "127.0.0.1".
        assert re.search(rf"(?<![\w]){n}(?![\w])", docs), f"docs never mention {n}"


def test_docs_state_the_measured_source_coverage():
    """The docs must carry the numbers the probe actually measured."""
    cfg = (yaml.safe_load(_text("config/sources.yaml")) or {}).get("sources") or []
    ledger = json.loads(_text("data/source_probe.json"))
    live = sum(1 for v in ledger.values() if v.get("verdict") == "live")
    dead = sum(1 for v in ledger.values() if v.get("verdict") == "dead")
    gen = _text("worker/src/sources.generated.ts")
    m = re.search(r"yaml_total=(\d+) worker_supported=(\d+) python_only=(\d+) hourly=(\d+)", gen)
    assert m, "generated file must carry the accounting header"
    total, worker, python_only, hourly = map(int, m.groups())
    assert total == len(cfg)
    assert live + dead <= len(cfg)

    docs = _all_docs()
    for n in (total, worker, python_only, live, dead, hourly):
        assert re.search(rf"(?<![\w]){n}(?![\w])", docs), f"docs never mention {n}"


def test_docs_document_the_liveness_rule():
    """The probe rule must be documented, not just implemented."""
    docs = _all_docs()
    low = " ".join(docs.lower().split())
    assert "liveness" in low
    assert "at any age" in low
    assert "status: dead" in low or "status: `dead`" in low



def test_documented_worker_endpoints_exist():
    src = _text("worker/src/index.ts")
    for endpoint in set(re.findall(r"`(?:GET|POST) (/[a-z]+)`", _all_docs())):
        assert f'"{endpoint}"' in src, f"documented endpoint {endpoint} is not implemented"


def test_documented_cli_commands_exist():
    """Every row of the README CLI table must be a real subcommand."""
    readme = _text("README.md")
    start = readme.index("## CLI Reference")
    end = readme.index("\n---", start)
    table = readme[start:end]
    cli = _text("src/job_hunt/cli.py")
    rows = set(re.findall(r"^\| `([a-z-]+)` \|", table, re.M))
    assert len(rows) >= 9, rows
    for cmd in rows:
        assert f'add_parser("{cmd}"' in cli, f"documented CLI command {cmd} does not exist"


def test_worker_readme_documents_the_generator():
    body = _text("worker/README.md")
    assert "gen_sources.py" in body
    assert "sources.generated.ts" in body
    assert "Never hand-edit" in body or "do not hand-edit" in body.lower()


def test_morning_check_docs_use_actionable_not_eligible():
    """`/recent` empties out once jobs are tailored; docs must not teach the wrong check."""
    body = _text("AGENTS.md")
    assert "actionable" in body
    assert "GET /recent" in body


def test_no_stale_counts():
    docs = _all_docs()
    for stale in ("298 Active", "62 passed", "201 Active", "FreeLLM Structured"):
        assert stale not in docs, f"stale documentation string: {stale}"


def test_docs_document_the_24h_window_and_role_gate():
    """Feed-quality guarantees: 24h window + software-only role gate must be documented."""
    docs = _all_docs()
    assert "24h" in docs or "24 hours" in docs or "24-hour" in docs
    assert "role gate" in docs.lower() or "role_gate" in docs.lower()
    assert "role_rejected" in docs


def test_docs_document_the_coverage_counters():
    """GET /coverage counters must all be documented."""
    docs = _all_docs()
    for counter in ("dated_fresh", "proxy_fresh", "dateless_dropped", "duplicate_suppressed", "role_rejected", "live_software"):
        assert counter in docs, f"docs never mention coverage counter {counter}"
