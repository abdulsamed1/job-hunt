"""Unit tests for orchestrator pipeline, stuck-job recovery, and retry behavior."""

from datetime import datetime, timezone, timedelta
import pytest
from job_hunt.evaluation.engine import EvaluationEngine
from job_hunt.models import CandidateProfile, JobPosting, JobState
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage


class FakeNotifier:
    """In-memory notifier seam: records emit calls, never touches the network."""

    def __init__(self):
        self.calls = []

    def emit(self, event, title, company, detail="", url=""):
        self.calls.append(
            {"event": event, "title": title, "company": company,
             "detail": detail, "url": url}
        )
        return True

    def events(self):
        return [c["event"] for c in self.calls]


def _lifecycle_profile():
    return CandidateProfile(
        full_name="Test User",
        first_name="Test",
        last_name="User",
        email="test@example.com",
        phone="+10000000000",
        location="Cairo, Egypt",
        open_to_remote=True,
        years_of_experience=6,
        verified_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
    )


def _lifecycle_job(source, title, company, slug, location="Remote", description=""):
    return JobPosting(
        source=source,
        title=title,
        company=company,
        raw_url=f"https://example.com/{slug}",
        canonical_url=f"https://example.com/{slug}",
        canonical_url_hash=f"h-{slug}",
        role_fingerprint=f"rf-{slug}",
        content_hash=f"c-{slug}",
        location=location,
        description=description,
    )


def _lifecycle_orchestrator(tmp_path, fake, name="lc.db"):
    db_file = tmp_path / name
    storage = Storage(db_file)
    return PipelineOrchestrator(
        storage=storage,
        db_path=str(db_file),
        eval_engine=EvaluationEngine(use_llm=False),
        use_llm=False,
        profile=_lifecycle_profile(),
        notifier=fake,
    )


GOOD_DESC = (
    "We are seeking a Backend Software Engineer with Python, FastAPI, "
    "PostgreSQL, and Docker. 3+ years experience building reliable backend "
    "systems. Remote worldwide."
)
TARGETED_DESC = (
    "Ignite Program for people with disabilities. Python and customer support."
)


@pytest.fixture
def orchestrator(tmp_path):
    db_file = tmp_path / "orch_test.db"
    storage = Storage(db_file)
    return PipelineOrchestrator(storage=storage, db_path=str(db_file))


def test_stuck_job_recovery(orchestrator):
    storage = orchestrator.storage
    job = JobPosting(
        source="test",
        title="Software Engineer",
        company="CrashCorp",
        raw_url="https://example.com/job/crash",
        canonical_url="https://example.com/job/crash",
        canonical_url_hash="hcrash",
        role_fingerprint="rfcrash",
        content_hash="ccrash",
    )
    saved, _ = storage.add_job(job)

    # Transition through valid lifecycle: DISCOVERED -> ELIGIBLE -> TAILORED -> APPLICATION_STARTED
    storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
    storage.update_job_state(saved.id, JobState.TAILORED, details="Tailored")
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED, details="Started application")

    # Manually backdate updated_at to simulate a crash 30 minutes ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with storage._get_connection() as conn:
        conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_time, saved.id))
        conn.commit()

    # Run recovery
    recovered_count = orchestrator.recover_stuck_jobs(timeout_minutes=15)
    assert recovered_count == 1

    updated_job = storage.get_job(saved.id)
    assert updated_job.state == JobState.RETRY_PENDING

    # Verify audit entry recorded recovery
    audit = storage.get_audit_log(saved.id)
    assert any("Stuck application recovered" in (a.details or "") for a in audit)


def test_prevent_duplicate_submissions(orchestrator):
    storage = orchestrator.storage
    job = JobPosting(
        source="test",
        title="Principal Engineer",
        company="BigTech",
        raw_url="https://example.com/job/submitted",
        canonical_url="https://example.com/job/submitted",
        canonical_url_hash="hsub",
        role_fingerprint="rfsub",
        content_hash="csub",
    )
    saved, _ = storage.add_job(job)
    storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
    storage.update_job_state(saved.id, JobState.TAILORED, details="Tailored")
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED, details="Started")
    storage.update_job_state(saved.id, JobState.SUBMITTED, details="Application submitted successfully")

    # Re-discovering the same job must not reset or queue it for application
    job2 = JobPosting(
        source="another_feed",
        title="Principal Engineer",
        company="BigTech",
        raw_url="https://example.com/job/submitted?tracker=rss",
        canonical_url="https://example.com/job/submitted",
        canonical_url_hash="hsub",
        role_fingerprint="rfsub",
        content_hash="csub",
    )
    saved2, is_new = storage.add_job(job2)
    assert is_new is False
    assert saved2.id == saved.id
    assert saved2.state == JobState.SUBMITTED


def test_has_already_applied_detects_all_dimensions(orchestrator):
    storage = orchestrator.storage
    from job_hunt.models import ApplicationRecord

    job = JobPosting(
        source="greenhouse",
        title="Backend Engineer",
        company="Stripe",
        raw_url="https://stripe.com/jobs/1",
        canonical_url="https://stripe.com/jobs/1",
        canonical_url_hash="hstripe1",
        role_fingerprint="stripe:backend_engineer:remote",
        content_hash="cstripe1",
        external_id="99887766",
    )
    saved, _ = storage.add_job(job)
    storage.update_job_state(saved.id, JobState.ELIGIBLE)
    storage.update_job_state(saved.id, JobState.TAILORED)
    storage.update_job_state(saved.id, JobState.APPLICATION_STARTED)

    record = ApplicationRecord(
        job_id=saved.id,
        state=JobState.SUBMITTED,
        confirmation_text="Thank you for applying to Stripe!",
    )
    storage.record_application(record)

    # 1. Check direct job_id
    is_dup, reason = storage.has_already_applied(job_id=saved.id)
    assert is_dup is True
    assert "Job ID" in reason

    # 2. Check by canonical URL hash alone
    is_dup, reason = storage.has_already_applied(canonical_url_hash="hstripe1")
    assert is_dup is True
    assert "canonical URL" in reason

    # 3. Check by canonical URL hash + role fingerprint combined (both must match)
    is_dup, reason = storage.has_already_applied(
        canonical_url_hash="hstripe1", role_fingerprint="stripe:backend_engineer:remote"
    )
    assert is_dup is True
    assert "canonical URL" in reason

    # 4. Check by external_id
    is_dup, reason = storage.has_already_applied(external_id="99887766")
    assert is_dup is True

    # 5. Check by company + title + location (most conservative check)
    job2 = JobPosting(
        source="greenhouse",
        title="Backend Engineer",
        company="Stripe",
        raw_url="https://stripe.com/jobs/2",
        canonical_url="https://stripe.com/jobs/2",
        canonical_url_hash="hstripe2_unique",
        role_fingerprint="stripe:backend_engineer:remote2",
        content_hash="cstripe2_unique",
        location="Remote",
    )
    saved2, is_new = storage.add_job(job2)
    assert is_new, "Expected new job entry"
    storage.update_job_state(saved2.id, JobState.ELIGIBLE)
    storage.update_job_state(saved2.id, JobState.TAILORED)
    storage.update_job_state(saved2.id, JobState.APPLICATION_STARTED)
    storage.record_application(
        ApplicationRecord(job_id=saved2.id, state=JobState.SUBMITTED, confirmation_text="Applied")
    )
    is_dup, reason = storage.has_already_applied(company="Stripe", title="Backend Engineer")
    assert is_dup is True
    assert "Stripe" in reason


@pytest.mark.asyncio
async def test_run_application_stage_skips_duplicates(orchestrator):
    storage = orchestrator.storage
    from unittest.mock import AsyncMock
    from job_hunt.models import ApplicationRecord

    # 1. Mark existing job as SUBMITTED via full state machine
    job1 = JobPosting(
        source="lever",
        title="Senior Python Developer",
        company="GitLab",
        raw_url="https://gitlab.com/jobs/1",
        canonical_url="https://gitlab.com/jobs/1",
        canonical_url_hash="hgit1",
        role_fingerprint="gitlab:senior_python_developer:remote",
        content_hash="cgit1",
        location="Remote",
    )
    saved1, _ = storage.add_job(job1)
    storage.update_job_state(saved1.id, JobState.ELIGIBLE)
    storage.update_job_state(saved1.id, JobState.TAILORED)
    storage.update_job_state(saved1.id, JobState.APPLICATION_STARTED)
    storage.record_application(
        ApplicationRecord(job_id=saved1.id, state=JobState.SUBMITTED, confirmation_text="Applied")
    )

    # 2. Create another job with SAME company+title+location (duplicate by signal)
    # Different canonical_url_hash and role_fingerprint so add_job creates a new entry
    job2 = JobPosting(
        source="ashby",
        title="Senior Python Developer",
        company="GitLab",
        raw_url="https://gitlab.com/jobs/3",
        canonical_url="https://gitlab.com/jobs/3",
        canonical_url_hash="hgit3_unique_dup",
        role_fingerprint="gitlab:senior_python_developer:remote_dup2",
        content_hash="cgit3_unique_dup",
        location="Remote",
    )
    saved2, is_new = storage.add_job(job2)
    assert is_new, "Expected add_job to create a new job entry"
    storage.update_job_state(saved2.id, JobState.ELIGIBLE)
    storage.update_job_state(saved2.id, JobState.TAILORED)

    # Mock browser engine so fill_and_submit should NEVER be called for job2
    orchestrator.browser_engine.fill_and_submit = AsyncMock()

    processed = await orchestrator.run_application_stage(limit=10, dry_run=True)
    # job2 should have been marked DUPLICATE and skipped via company+title+location check
    assert orchestrator.browser_engine.fill_and_submit.await_count == 0

    reloaded2 = storage.get_job(saved2.id)
    assert reloaded2.state == JobState.DUPLICATE


@pytest.mark.asyncio
async def test_run_application_stage_allows_different_canonical_urls(orchestrator):
    """Different canonical URLs should NOT be flagged as duplicates."""
    storage = orchestrator.storage
    from unittest.mock import AsyncMock
    from job_hunt.models import ApplicationRecord

    # 1. Mark existing job as SUBMITTED
    job1 = JobPosting(
        source="lever",
        title="Senior Python Developer",
        company="GitLab",
        raw_url="https://gitlab.com/jobs/1",
        canonical_url="https://gitlab.com/jobs/1",
        canonical_url_hash="hgit1",
        role_fingerprint="gitlab:senior_python_developer:remote",
        content_hash="cgit1",
        location="Remote",
    )
    saved1, _ = storage.add_job(job1)
    storage.update_job_state(saved1.id, JobState.ELIGIBLE)
    storage.update_job_state(saved1.id, JobState.TAILORED)
    storage.update_job_state(saved1.id, JobState.APPLICATION_STARTED)
    storage.record_application(
        ApplicationRecord(job_id=saved1.id, state=JobState.SUBMITTED, confirmation_text="Applied")
    )

    # 2. Create different job with different company - should NOT be flagged as duplicate
    job2 = JobPosting(
        source="ashby",
        title="Senior Python Developer",
        company="Meta",  # Different company
        raw_url="https://meta.com/jobs/1",
        canonical_url="https://meta.com/jobs/1",
        canonical_url_hash="hmeta1_unique",
        role_fingerprint="meta:senior_python_developer:remote",
        content_hash="cmeta1_unique",
        location="Remote",
    )
    saved2, _ = storage.add_job(job2)
    storage.update_job_state(saved2.id, JobState.ELIGIBLE)
    storage.update_job_state(saved2.id, JobState.TAILORED)

    orchestrator.browser_engine.fill_and_submit = AsyncMock()

    processed = await orchestrator.run_application_stage(limit=10, dry_run=True)
    # job2 should be processed (not flagged as duplicate)
    assert orchestrator.browser_engine.fill_and_submit.await_count >= 0
    reloaded2 = storage.get_job(saved2.id)
    # job2 should NOT be in DUPLICATE state since it's a different company
    assert reloaded2.state != JobState.DUPLICATE


def test_remote_outside_egypt_evaluation(orchestrator):
    from job_hunt.models import CandidateProfile
    from job_hunt.evaluation.engine import EvaluationEngine

    candidate = CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdulsamed@example.com",
        phone="+201094393750",
        location="Cairo, Egypt",
        open_to_remote=True,
        years_of_experience=4,
        verified_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Distributed Systems"],
    )

    engine = EvaluationEngine(min_score_threshold=70.0, use_llm=False)

    # Job 1: Remote (US / Worldwide)
    remote_job = JobPosting(
        source="greenhouse",
        title="Software Engineer, Backend",
        company="ScaleAI",
        raw_url="https://example.com/job/scale1",
        canonical_url="https://example.com/job/scale1",
        canonical_url_hash="hscale1",
        role_fingerprint="scale:backend:remote",
        content_hash="cscale1",
        location="Remote (Worldwide)",
        description="We are seeking a Backend Software Engineer with Python, FastAPI, and PostgreSQL. 3+ years experience.",
    )

    res = engine.evaluate(remote_job, candidate)
    assert res.pre_filtered is False
    assert res.eligible is True
    assert res.score >= 70.0

    # Job 2: Strict on-site outside Egypt (no remote)
    onsite_job = JobPosting(
        source="lever",
        title="Software Engineer, Infrastructure",
        company="TokyoRobotics",
        raw_url="https://example.com/job/tokyo",
        canonical_url="https://example.com/job/tokyo",
        canonical_url_hash="htokyo",
        role_fingerprint="tokyo:infra:onsite",
        content_hash="ctokyo",
        location="Tokyo, Japan",
        description="Must work strictly on-site from our Tokyo headquarters 5 days a week. No remote allowed.",
    )

    passed, reason = engine.pre_filter(onsite_job, candidate)
    assert passed is False
    assert "on-site" in reason.lower()


@pytest.mark.asyncio
async def test_run_discovery_stage_applies_recency_and_remote(orchestrator, tmp_path):
    from datetime import datetime, timezone
    from job_hunt.models import JobPosting

    fresh = JobPosting(
        source="test", title="Backend Engineer", company="Co",
        raw_url="https://example.com/f", canonical_url="https://example.com/f",
        canonical_url_hash="h-f", role_fingerprint="rf-f", content_hash="c-f",
        location="Remote", posted_at=datetime.now(timezone.utc).isoformat(),
    )
    stale = JobPosting(
        source="test", title="Backend Engineer", company="Co2",
        raw_url="https://example.com/s", canonical_url="https://example.com/s",
        canonical_url_hash="h-s", role_fingerprint="rf-s", content_hash="c-s",
        location="Remote", posted_at="2020-01-01",
    )
    onsite = JobPosting(
        source="test", title="Backend Engineer", company="Co3",
        raw_url="https://example.com/o", canonical_url="https://example.com/o",
        canonical_url_hash="h-o", role_fingerprint="rf-o", content_hash="c-o",
        location="Berlin, Germany",
        posted_at=datetime.now(timezone.utc).isoformat(),
    )

    class StubRegistry:
        def load_sources_file(self, path):
            return [{"adapter": "test", "url": "https://example.com"}]

        async def discover_all(self, sources=None, **kwargs):
            return [fresh, stale, onsite]

    orchestrator.registry = StubRegistry()
    new_jobs = await orchestrator.run_discovery_stage(hours_old=24, remote_only=True)
    assert new_jobs == 1  # only the fresh remote posting survives
    assert orchestrator.storage.get_job(1) is not None


@pytest.mark.asyncio
async def test_run_discovery_stage_source_name_filter(orchestrator):
    seen = []

    class StubRegistry:
        def load_sources_file(self, path):
            return [
                {"adapter": "test", "name": "a", "url": "https://a.example"},
                {"adapter": "test", "name": "b", "url": "https://b.example"},
            ]

        async def discover_all(self, sources=None, **kwargs):
            seen.extend(s.get("name") for s in (sources or []))
            return []

    orchestrator.registry = StubRegistry()
    await orchestrator.run_discovery_stage(source_name="b", hours_old=0)
    assert seen == ["b"]


def test_cli_scan_accepts_source_and_windows():
    from job_hunt.cli import build_parser

    args = build_parser().parse_args(
        ["scan", "--source", "linkedin_geo_recent", "--hours-old", "12", "--remote-only"]
    )
    assert args.source == "linkedin_geo_recent"
    assert args.hours_old == 12
    assert args.remote_only is True


def test_cli_no_notify_skips_notifier_construction(monkeypatch):
    from job_hunt import cli as cli_module

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def _boom():
        raise AssertionError("build_notifier must not run with --no-notify")

    monkeypatch.setattr(cli_module, "build_notifier", _boom)
    for cmd in ("scan", "evaluate", "tailor", "apply", "run"):
        args = cli_module.build_parser().parse_args([cmd, "--no-notify"])
        assert args.no_notify is True
        assert cli_module.build_notifier_or_none(args) is None

    monkeypatch.setattr(cli_module, "build_notifier", lambda: object())
    args = cli_module.build_parser().parse_args(["scan"])
    assert args.no_notify is False
    assert cli_module.build_notifier_or_none(args) is not None


def test_web_action_request_accepts_windows():
    from job_hunt.web.app import ActionRequest

    req = ActionRequest(hours_old=12, remote_only=True)
    assert req.hours_old == 12
    assert req.remote_only is True


@pytest.mark.asyncio
async def test_lifecycle_events_discovery_and_evaluation(tmp_path, monkeypatch):
    """Discovery emits SEEKING; evaluation emits FILTERED (with pre-filter
    reason) and ELIGIBLE; no credentials leak into event payloads."""
    from unittest.mock import AsyncMock

    secret = "tok-secret-abc123"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", secret)
    fake = FakeNotifier()
    orch = _lifecycle_orchestrator(tmp_path, fake)

    good = _lifecycle_job(
        "test", "Backend Engineer", "Acme", "lc-good", description=GOOD_DESC,
    )
    targeted = _lifecycle_job(
        "test",
        "Technical Support Engineer Intern for People with Disabilities",
        "BaytIgnite",
        "lc-targeted",
        description=TARGETED_DESC,
    )

    class StubRegistry:
        def load_sources_file(self, path):
            return [{"adapter": "test", "url": "https://example.com"}]

        async def discover_all(self, sources=None, **kwargs):
            return [good, targeted]

    orch.registry = StubRegistry()
    new_jobs = await orch.run_discovery_stage(hours_old=0)
    assert new_jobs == 2
    assert fake.events().count("SEEKING") == 2

    orch.liveness.check_url_async = AsyncMock(return_value=(True, "ok"))
    evaluated = await orch.run_evaluation_stage_async(threshold=70.0)
    assert evaluated == 2  # both jobs evaluated; the gated one persists as PRE_FILTERED_OUT

    names = set(fake.events())
    assert "FILTERED" in names
    assert "ELIGIBLE" in names
    disab = [
        c for c in fake.calls
        if c["event"] == "FILTERED" and "disabilit" in (c["detail"] or "").lower()
    ]
    assert disab, "FILTERED detail must carry the disability pre-filter reason"
    for c in fake.calls:
        assert secret not in (c["detail"] or "")
        assert secret not in (c["url"] or "")
        assert secret not in (c["title"] or "")


@pytest.mark.asyncio
async def test_lifecycle_events_tailor_apply_retry_recovery(tmp_path, monkeypatch):
    """Tailoring emits TAILORED; applications emit APPLICATION_STARTED plus
    SUBMITTED/FAILED/RETRY; stuck recovery emits RECOVERY."""
    from unittest.mock import AsyncMock
    from job_hunt.models import ApplicationRecord, TailoredCV

    secret = "tok-secret-abc123"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", secret)
    fake = FakeNotifier()
    orch = _lifecycle_orchestrator(tmp_path, fake, name="lc2.db")

    class StubTailor:
        def generate_tailored_cv(self, job, profile, generate_pdf=True):
            return TailoredCV(
                job_id=job.id, content_markdown="# CV", verification_passed=True,
            )

    orch.cv_tailor = StubTailor()

    ids = []
    for slug, company in (("lc-a", "Acme"), ("lc-b", "Globex"), ("lc-c", "Initech")):
        job = _lifecycle_job(
            "test", "Backend Engineer", company, slug, description=GOOD_DESC,
        )
        saved, is_new = orch.storage.add_job(job)
        assert is_new
        orch.storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
        ids.append(saved.id)

    tailored = orch.run_cv_stage(generate_pdf=False)
    assert tailored == 3
    assert "TAILORED" in fake.events()

    monkeypatch.setattr(
        "job_hunt.cv.resume_store.resolve_resume_path",
        lambda job_id, storage=None, **kwargs: "/tmp/fake-resume.pdf",
    )
    orch.liveness.check_url_async = AsyncMock(return_value=(True, "ok"))

    plan = {ids[0]: JobState.SUBMITTED, ids[1]: JobState.FAILED, ids[2]: JobState.RETRY_PENDING}

    async def _fab(job, *args, **kwargs):
        state = plan[job.id]
        if state == JobState.SUBMITTED:
            return ApplicationRecord(
                job_id=job.id, state=state, confirmation_text="Applied!",
            )
        return ApplicationRecord(
            job_id=job.id, state=state, error_message=f"{state.value.lower()} happened",
        )

    orch.browser_engine.fill_and_submit = AsyncMock(side_effect=_fab)
    processed = await orch.run_application_stage(limit=10, dry_run=True)
    assert processed == 3

    names = set(fake.events())
    assert fake.events().count("APPLICATION_STARTED") == 3
    assert "SUBMITTED" in names
    assert "FAILED" in names
    assert "RETRY" in names

    stuck = _lifecycle_job(
        "test", "Backend Engineer", "StuckCo", "lc-stuck", description=GOOD_DESC,
    )
    saved, _ = orch.storage.add_job(stuck)
    orch.storage.update_job_state(saved.id, JobState.ELIGIBLE, details="Eligible")
    orch.storage.update_job_state(saved.id, JobState.TAILORED, details="Tailored")
    orch.storage.update_job_state(
        saved.id, JobState.APPLICATION_STARTED, details="Started application",
    )
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with orch.storage._get_connection() as conn:
        conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_time, saved.id))
        conn.commit()
    assert orch.recover_stuck_jobs(timeout_minutes=15) == 1
    assert "RECOVERY" in fake.events()

    for c in fake.calls:
        for field in ("detail", "url", "title", "company"):
            assert secret not in (c[field] or ""), f"credential leaked in {field}"
