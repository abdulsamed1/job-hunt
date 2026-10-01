"""Cold-start diagnostics: verify the machine is ready to run the pipeline."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List


def _check(name: str, ok: bool, detail: str = "") -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_diagnostics(
    db_path: str = "data/jobs.db",
    sources_path: str = "config/sources.yaml",
    profile_path: str = "config/candidate_profile.json",
) -> Dict[str, Any]:
    """Run setup checks without side effects (read-only except a DB open)."""
    checks: List[Dict[str, Any]] = []

    checks.append(_check(
        "python_version",
        sys.version_info >= (3, 10),
        f"Python {sys.version.split()[0]}",
    ))

    try:
        from playwright.async_api import async_playwright  # noqa: F401
        browsers = Path.home() / ".cache" / "ms-playwright"
        checks.append(_check(
            "playwright_browsers",
            any(browsers.glob("chromium*")) if browsers.exists() else False,
            f"browser cache at {browsers}",
        ))
    except ImportError:
        checks.append(_check("playwright_browsers", False, "playwright not installed"))

    try:
        from job_hunt.storage import Storage

        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        Storage(db_file)  # opens + migrates; proves writability
        checks.append(_check("database", True, f"SQLite ready at {db_file}"))
    except Exception as exc:
        checks.append(_check("database", False, str(exc)[:120]))

    try:
        from job_hunt.discovery.registry import SourceRegistry

        sources = SourceRegistry().load_sources_file(sources_path)
        checks.append(_check(
            "sources_config",
            len(sources) > 0,
            f"{len(sources)} source(s) in {sources_path}",
        ))
    except Exception as exc:
        checks.append(_check("sources_config", False, str(exc)[:120]))

    try:
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        missing = [k for k in ("full_name", "email", "phone", "verified_skills", "verified_experiences")
                   if not profile.get(k)]
        checks.append(_check(
            "candidate_profile",
            not missing,
            f"missing: {missing}" if missing else f"{profile.get('full_name')} OK",
        ))
    except FileNotFoundError:
        checks.append(_check("candidate_profile", False, f"not found: {profile_path}"))
    except (OSError, ValueError) as exc:
        checks.append(_check("candidate_profile", False, str(exc)[:120]))

    try:
        from job_hunt.automation.browser import BrowserApplicationEngine

        has_session = BrowserApplicationEngine().has_linkedin_session()
        checks.append(_check(
            "linkedin_session",
            True,  # informational only: missing session degrades, never blocks setup
            "saved session present" if has_session else "no saved session (Easy Apply unavailable)",
        ))
    except Exception as exc:
        checks.append(_check("linkedin_session", True, f"unchecked: {exc}"[:120]))

    import os

    checks.append(_check(
        "rezi_token",
        bool(os.environ.get("REZI_MCP_TOKEN")) or Path(".rezi_token").exists(),
        "token present (value never displayed)" if (
            os.environ.get("REZI_MCP_TOKEN") or Path(".rezi_token").exists()
        ) else "not configured (Rezi mirroring disabled)",
    ))
    # Token presence is informational, like the LinkedIn session.
    checks[-1]["ok"] = True

    overall = all(c["ok"] for c in checks)
    return {"ok": overall, "checks": checks}


def format_report(report: Dict[str, Any]) -> str:
    lines = ["Cold-start diagnostics:"]
    for check in report["checks"]:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    lines.append("READY" if report["ok"] else "NOT READY - fix FAIL items above")
    return "\n".join(lines)
