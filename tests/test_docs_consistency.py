"""Docs must not drift from the code they describe.

Cheap guard against the failure mode where a safety rule or a source count is
fixed in code but the README keeps promising the old behavior.
"""

from __future__ import annotations

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
    assert worker + python_only == total, "sources must be fully accounted for"
    docs = _all_docs()
    for n in (total, worker, python_only):
        assert str(n) in docs, f"docs never mention {n}"


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
