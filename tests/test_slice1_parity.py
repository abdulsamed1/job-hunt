"""Three-way ATS apex parity: Python hosts, Worker hosts, browser table.

The exact expected core set is asserted explicitly: greenhouse pair, lever
pair, ashby pair, smartrecruiters pair, myworkdayjobs.com, workable.com,
bamboohr.com, plus the recruitee/jobvite/teamtailor vendor apexes (stable
board hosts behind ATS_LINK_PATTERNS entries in browser.py).
"""
import re
from pathlib import Path

from job_hunt.automation.browser import KNOWN_ATS_APEXES
from job_hunt.discovery.hosts import ATS_APEXES

EXPECTED_CORE = {
    "boards.greenhouse.io", "boards-api.greenhouse.io",
    "jobs.lever.co", "api.lever.co",
    "jobs.ashbyhq.com", "api.ashbyhq.com",
    "jobs.smartrecruiters.com", "api.smartrecruiters.com",
    "myworkdayjobs.com",
    "workable.com",
    "bamboohr.com",
    "recruitee.com",
    "jobvite.com",
    "teamtailor.com",
}


def _worker_apexes():
    text = (Path(__file__).resolve().parent.parent
            / "worker" / "src" / "lib" / "hosts.ts").read_text(encoding="utf-8")
    # Double-quoted dotted strings in hosts.ts are the ATS_APEXES entries
    # ("ats"/"unverified" carry no dot and are excluded).
    return {m for m in re.findall(r'"([^"]+)"', text) if "." in m}


def test_python_hosts_contain_core_set():
    assert EXPECTED_CORE <= set(ATS_APEXES)


def test_worker_hosts_contain_core_set():
    assert EXPECTED_CORE <= _worker_apexes()


def test_browser_hosts_contain_core_set():
    assert EXPECTED_CORE <= set(KNOWN_ATS_APEXES)
