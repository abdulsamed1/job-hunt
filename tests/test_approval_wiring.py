"""Patch-verification tests: config approval wiring, CLI/web forwarding, trigger guard."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from job_hunt.models import JobPosting, JobState
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage


def _write_sources(path, linkedin_extra: dict):
    import yaml

    entry = {
        "adapter": "linkedin",
        "name": "linkedin_test",
        "url": "https://www.linkedin.com/jobs/",
        "queries": ["Backend Engineer"],
        "locations": ["Remote"],
    }
    entry.update(linkedin_extra)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"sources": [entry]}, f)


def test_orchestrator_reads_linkedin_approval_flag(tmp_path):
    off = tmp_path / "sources_off.yaml"
    _write_sources(off, {"linkedin_require_approval": False})
    orch = PipelineOrchestrator(
        storage=Storage(tmp_path / "t1.db"),
        db_path=str(tmp_path / "t1.db"),
        sources_path=str(off),
        use_llm=False,
    )
    assert orch.browser_engine.require_linkedin_approval is False

    missing = tmp_path / "sources_missing.yaml"
    _write_sources(missing, {})
    orch2 = PipelineOrchestrator(
        storage=Storage(tmp_path / "t2.db"),
        db_path=str(tmp_path / "t2.db"),
        sources_path=str(missing),
        use_llm=False,
    )
    assert orch2.browser_engine.require_linkedin_approval is True


def test_cli_apply_exposes_linkedin_approved_flag():
    from job_hunt.cli import build_parser

    args = build_parser().parse_args(["apply", "--live", "--linkedin-approved"])
    assert args.linkedin_approved is True
    args2 = build_parser().parse_args(["apply"])
    assert args2.linkedin_approved is False
    args3 = build_parser().parse_args(["run", "--linkedin-approved"])
    assert args3.linkedin_approved is True


def test_web_action_request_accepts_approval_field():
    from job_hunt.web.app import ActionRequest

    req = ActionRequest(live=True, linkedin_approved=True)
    assert req.linkedin_approved is True
    assert ActionRequest().linkedin_approved is False


@pytest.mark.asyncio
async def test_run_application_stage_forwards_approval(tmp_path, monkeypatch):
    from job_hunt.models import ApplicationRecord

    monkeypatch.setattr("job_hunt.settings.linkedin_enabled", lambda *a, **k: True)
    orch = PipelineOrchestrator(
        storage=Storage(tmp_path / "t3.db"), db_path=str(tmp_path / "t3.db"), use_llm=False
    )
    job = JobPosting(
        source="linkedin",
        title="Backend Engineer",
        company="Co",
        raw_url="https://www.linkedin.com/jobs/view/999",
        canonical_url="https://www.linkedin.com/jobs/view/999",
        canonical_url_hash="h-fwd-approval",
        role_fingerprint="rf-fwd-approval",
        content_hash="c-fwd-approval",
        location="Remote",
        application_type="easy_apply",
    )
    saved, _ = orch.storage.add_job(job)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE)
    orch.storage.update_job_state(saved.id, JobState.TAILORED)

    orch.browser_engine.fill_and_submit = AsyncMock(
        return_value=ApplicationRecord(job_id=saved.id, state=JobState.SUBMITTED)
    )
    orch.liveness.check_url_async = AsyncMock(return_value=(True, "test alive"))
    await orch.run_application_stage(limit=5, dry_run=True, linkedin_approved=True)

    assert orch.browser_engine.fill_and_submit.await_count == 1
    _, kwargs = orch.browser_engine.fill_and_submit.call_args
    assert kwargs.get("linkedin_approved") is True
