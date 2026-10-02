"""LinkedIn must be inert across the pipeline while disabled."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from job_hunt.models import JobPosting, JobState, ApplicationRecord
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage


def _linkedin_job(**overrides):
    data = dict(
        source="linkedin",
        title="Backend Engineer",
        company="Co",
        raw_url="https://www.linkedin.com/jobs/view/123",
        canonical_url="https://www.linkedin.com/jobs/view/123",
        canonical_url_hash="h-dis",
        role_fingerprint="rf-dis",
        content_hash="c-dis",
        location="Remote",
    )
    data.update(overrides)
    return JobPosting(**data)


def _orch(tmp_path):
    return PipelineOrchestrator(
        storage=Storage(tmp_path / "t.db"), db_path=str(tmp_path / "t.db"), use_llm=False
    )


def test_linkedin_disabled_by_default_config():
    from job_hunt import settings

    assert settings.linkedin_enabled("config/sources.yaml") is False


def test_env_reenable_overrides(monkeypatch):
    from job_hunt import settings

    monkeypatch.setenv("LINKEDIN_ENABLED", "true")
    assert settings.linkedin_enabled("config/sources.yaml") is True
    monkeypatch.setenv("LINKEDIN_ENABLED", "false")
    assert settings.linkedin_enabled("config/sources.yaml") is False


def test_is_linkedin_job_detection():
    from job_hunt import settings

    assert settings.is_linkedin_job(_linkedin_job())
    assert settings.is_linkedin_job(
        _linkedin_job(source="greenhouse", raw_url="https://nl.linkedin.com/jobs/view/5")
    )
    assert not settings.is_linkedin_job(
        _linkedin_job(source="greenhouse", raw_url="https://boards.greenhouse.io/x/1")
    )


@pytest.mark.asyncio
async def test_discovery_stage_skips_linkedin_source(tmp_path):
    orch = _orch(tmp_path)
    orch.registry.discover_all = AsyncMock(return_value=[])

    await orch.run_discovery_stage(source_name="linkedin_geo_recent", hours_old=0)

    args, kwargs = orch.registry.discover_all.call_args
    sources = kwargs.get("sources") if kwargs else args[0]
    assert sources == []


def test_evaluation_stage_skips_linkedin_jobs(tmp_path):
    orch = _orch(tmp_path)
    job = _linkedin_job()
    saved, _ = orch.storage.add_job(job)
    orch.eval_engine.evaluate = MagicMock(
        return_value=MagicMock(job_id=saved.id, score=90, recommendation="apply")
    )
    orch.storage.save_evaluation = MagicMock()

    counted = orch.run_evaluation_stage(limit=5)

    assert counted == 0
    orch.eval_engine.evaluate.assert_not_called()


def test_cv_stage_skips_linkedin_jobs(tmp_path):
    orch = _orch(tmp_path)
    job = _linkedin_job()
    saved, _ = orch.storage.add_job(job)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE)
    orch.cv_tailor.generate_tailored_cv = MagicMock()

    orch.run_cv_stage(limit=5)

    orch.cv_tailor.generate_tailored_cv.assert_not_called()


@pytest.mark.asyncio
async def test_application_stage_never_launches_for_linkedin(tmp_path):
    orch = _orch(tmp_path)
    job = _linkedin_job()
    saved, _ = orch.storage.add_job(job)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE)
    orch.storage.update_job_state(saved.id, JobState.TAILORED)
    orch.browser_engine.fill_and_submit = AsyncMock()
    orch.liveness.check_url_async = AsyncMock(return_value=(True, "alive"))

    processed = await orch.run_application_stage(limit=5, dry_run=True)

    assert processed == 0
    orch.browser_engine.fill_and_submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_fill_and_submit_blocks_linkedin():
    from job_hunt.automation.browser import BrowserApplicationEngine
    from job_hunt.models import CandidateProfile

    engine = BrowserApplicationEngine(headless=True)
    profile = CandidateProfile(
        full_name="T U", first_name="T", last_name="U",
        email="t@example.com", phone="+1", location="Cairo",
        work_authorization="ok",
    )
    record = await engine.fill_and_submit(_linkedin_job(), profile, dry_run=True)

    assert record.state == JobState.FAILED
    assert "LinkedIn applications are disabled" in record.error_message
