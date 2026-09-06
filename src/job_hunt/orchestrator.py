"""24/7 Autonomous Pipeline Orchestrator with crash recovery, backoff, and stateful tracking."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.cv.tailor import CVTailor
from job_hunt.discovery.registry import SourceRegistry
from job_hunt.evaluation.engine import EvaluationEngine
from job_hunt.liveness import LivenessDetector
from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, JobPosting, JobState
from job_hunt.reposts import RepostDetector
from job_hunt.storage import Storage

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """End-to-end autonomous job discovery and application orchestrator."""

    def __init__(
        self,
        storage: Optional[Storage] = None,
        registry: Optional[SourceRegistry] = None,
        eval_engine: Optional[EvaluationEngine] = None,
        cv_tailor: Optional[CVTailor] = None,
        browser_engine: Optional[BrowserApplicationEngine] = None,
        profile: Optional[CandidateProfile] = None,
        llm_client: Optional[FreeLLMClient] = None,
        use_llm: bool = True,
        db_path: str = "data/jobs.db",
        sources_path: str = "config/sources.yaml",
    ):
        self.storage = storage or Storage(db_path)
        self.registry = registry or SourceRegistry()
        self.sources_path = sources_path
        self._running = False
        self.use_llm = use_llm

        # Configure LLM client if enabled
        if self.use_llm:
            self.llm_client = llm_client or FreeLLMClient()
            try:
                self.llm_client.ensure_server_running()
            except Exception as e:
                logger.warning("Could not verify or auto-start FreeLLMAPI: %s", e)
        else:
            self.llm_client = None

        self.eval_engine = eval_engine or EvaluationEngine(
            llm_client=self.llm_client,
            use_llm=self.use_llm,
        )
        self.cv_tailor = cv_tailor or CVTailor(
            llm_client=self.llm_client,
            use_llm=self.use_llm,
        )
        self.browser_engine = browser_engine or BrowserApplicationEngine()
        self.liveness = LivenessDetector()
        self.repost_detector = RepostDetector()

        if profile is None:
            prof_path = Path("config/candidate_profile.json")
            if prof_path.exists():
                import json
                with open(prof_path, "r", encoding="utf-8") as f:
                    self.profile = CandidateProfile(**json.load(f))
            else:
                self.profile = CandidateProfile(
                    full_name="Alex Rivera",
                    first_name="Alex",
                    last_name="Rivera",
                    email="alex.rivera.dev@example.com",
                    phone="+1-555-0199",
                    location="San Francisco, CA, USA",
                    years_of_experience=6,
                    work_authorization="Authorized to work in Egypt, Remote Worldwide",
                    open_to_remote=True,
                    verified_skills=[
                        "Python", "FastAPI", "PostgreSQL", "Docker", "AWS",
                        "Redis", "Distributed Systems", "Kubernetes", "CI/CD"
                    ],
                    allowed_metrics=["40%", "10k", "99.9%"],
                )
        else:
            self.profile = profile

    def stop(self) -> None:
        """Signal the orchestrator loop to stop gracefully."""
        logger.info("Graceful shutdown requested.")
        self._running = False

    async def run_discovery_stage(self, max_sources: Optional[int] = None) -> int:
        """Stage 1 & 2: Discover jobs across all sources and deduplicate."""
        sources = self.registry.load_sources_file(self.sources_path)
        if max_sources:
            sources = sources[:max_sources]

        postings = await self.registry.discover_all(sources=sources)
        new_jobs = 0
        for p in postings:
            _, is_new = self.storage.add_job(p)
            if is_new:
                new_jobs += 1

        logger.info("Discovery complete. Discovered %d jobs (%d new)", len(postings), new_jobs)
        return new_jobs

    async def run_evaluation_stage_async(self, limit: int = 100, threshold: float = 70.0) -> int:
        """Stage 3, 4, 5: Pre-filter, evaluate, and score discovered jobs asynchronously."""
        pending_jobs = self.storage.get_jobs_by_state(JobState.DISCOVERED, limit=limit)
        evaluated_count = 0
        async with httpx.AsyncClient() as client:
            for job in pending_jobs:
                # Zero-token liveness check (filter dead/filled postings before spending LLM tokens)
                is_live, liveness_reason = await self.liveness.check_url_async(job.raw_url, client=client)
                if not is_live:
                    logger.info("Job %s (%s - %s) filtered out by liveness: %s", job.id, job.company, job.title, liveness_reason)
                    self.storage.update_job_state(job.id, JobState.PRE_FILTERED_OUT, details=liveness_reason, force=True)
                    continue

                eval_result = await self.eval_engine.evaluate_async(job, self.profile, threshold=threshold)
                self.storage.save_evaluation(eval_result)
                evaluated_count += 1

        logger.info("Evaluation complete. Evaluated %d jobs.", evaluated_count)
        return evaluated_count

    def run_evaluation_stage(self, limit: int = 100, threshold: float = 70.0) -> int:
        """Stage 3, 4, 5: Pre-filter, evaluate, and score discovered jobs synchronously."""
        pending_jobs = self.storage.get_jobs_by_state(JobState.DISCOVERED, limit=limit)
        evaluated_count = 0
        for job in pending_jobs:
            eval_result = self.eval_engine.evaluate(job, self.profile, threshold=threshold)
            self.storage.save_evaluation(eval_result)
            evaluated_count += 1

        logger.info("Evaluation complete. Evaluated %d jobs.", evaluated_count)
        return evaluated_count

    def run_cv_stage(self, limit: int = 50) -> int:
        """Stage 6 & 7: Generate fact-checked tailored CVs for eligible jobs."""
        eligible_jobs = self.storage.get_jobs_by_state(JobState.ELIGIBLE, limit=limit)
        tailored_count = 0
        for job in eligible_jobs:
            tailored_cv = self.cv_tailor.generate_tailored_cv(job, self.profile)
            self.storage.save_tailored_cv(tailored_cv)
            tailored_count += 1

        logger.info("CV tailoring complete. Tailored %d CVs.", tailored_count)
        return tailored_count

    # Alias for web API & consistency
    def run_tailoring_stage(self, limit: int = 50) -> int:
        """Alias for run_cv_stage."""
        return self.run_cv_stage(limit=limit)

    async def run_application_stage(self, limit: int = 10, dry_run: bool = True) -> int:
        """Stage 8 & 9: Launch browser automation to fill and submit applications."""
        tailored_jobs = self.storage.get_jobs_by_state(JobState.TAILORED, limit=limit)
        retry_jobs = self.storage.get_jobs_by_state(JobState.RETRY_PENDING, limit=limit)
        queue = tailored_jobs + retry_jobs

        processed = 0
        for job in queue:
            # Check if an application has already been submitted for this company/role/URL
            is_dup, dup_reason = self.storage.has_already_applied(
                job_id=job.id,
                canonical_url_hash=job.canonical_url_hash,
                role_fingerprint=job.role_fingerprint,
                company=job.company,
                title=job.title,
                external_id=job.external_id,
            )
            if is_dup:
                logger.warning(
                    "Skipping duplicate application for Job %s (%s - %s): %s",
                    job.id, job.company, job.title, dup_reason
                )
                self.storage.update_job_state(job.id, JobState.DUPLICATE, details=dup_reason, force=True)
                continue

            # Resolve tailored ATS-optimized PDF for this specific job posting
            tailored_pdf = Path(f"data/cvs/tailored_cv_{job.id}.pdf")
            if tailored_pdf.exists():
                resume_path = str(tailored_pdf.resolve())
            elif Path("Abdulsamed_Hamdy.pdf").exists():
                resume_path = str(Path("Abdulsamed_Hamdy.pdf").resolve())
            else:
                cv_dir = Path("data/cvs")
                cv_dir.mkdir(parents=True, exist_ok=True)
                cv_file = cv_dir / f"resume_{self.profile.last_name.lower()}.txt"
                if not cv_file.exists():
                    cv_file.write_text(self.cv_tailor.build_master_cv(self.profile), encoding="utf-8")
                resume_path = str(cv_file.resolve())

            # Fast liveness check before launching browser session
            is_live, liveness_reason = await self.liveness.check_url_async(job.raw_url)
            if not is_live:
                logger.warning("Job %s is no longer active: %s", job.id, liveness_reason)
                self.storage.update_job_state(job.id, JobState.FAILED, details=f"Liveness check: {liveness_reason}", force=True)
                continue

            self.storage.update_job_state(
                job.id,
                JobState.APPLICATION_STARTED,
                details="Starting automated browser application session",
            )
            record = await self.browser_engine.fill_and_submit(
                job,
                self.profile,
                resume_file_path=resume_path,
                dry_run=dry_run,
            )
            self.storage.record_application(record)
            processed += 1

        logger.info("Application stage complete. Processed %d applications.", processed)
        return processed

    def recover_stuck_jobs(self, timeout_minutes: int = 15) -> int:
        """Check for and recover jobs left hanging from unexpected crashes."""
        count = self.storage.recover_stuck_jobs(timeout_minutes=timeout_minutes)
        if count > 0:
            logger.warning("Recovered %d stuck jobs back to retry/failed state.", count)
        return count

    async def run_cycle(self, dry_run: bool = True, max_sources: Optional[int] = None) -> Dict[str, Any]:
        """Execute one complete end-to-end pipeline iteration."""
        # 0. Recover stuck jobs
        recovered = self.recover_stuck_jobs()

        # 1. Discovery (targeting 500+ jobs daily)
        new_jobs = await self.run_discovery_stage(max_sources=max_sources)

        # 2. Evaluation
        evaluated = await self.run_evaluation_stage_async()

        # 3. Tailored CV
        tailored = self.run_cv_stage()

        # 4. Applications (with strict duplicate avoidance)
        applied = await self.run_application_stage(dry_run=dry_run)

        stats = self.storage.get_summary_stats()
        daily_metrics = self.storage.get_daily_metrics()
        return {
            "recovered_stuck": recovered,
            "new_jobs_discovered": new_jobs,
            "jobs_evaluated": evaluated,
            "cvs_tailored": tailored,
            "applications_processed": applied,
            "daily_metrics": daily_metrics,
            "database_stats": stats,
        }

    # Alias for web/CLI callers
    run_single_cycle = run_cycle

    async def start_continuous_loop(self, interval_seconds: int = 3600, dry_run: bool = True) -> None:
        """Run 24/7 continuous autonomous job-hunting loop."""
        self._running = True
        logger.info("Starting 24/7 continuous autonomous loop (cycle interval: %ds)...", interval_seconds)

        while self._running:
            try:
                logger.info("--- Starting Pipeline Cycle ---")
                results = await self.run_cycle(dry_run=dry_run)
                metrics = results.get("daily_metrics", {})
                logger.info(
                    "24h Daily Progress: %d scanned (%d new) | Daily Target: 500+ | Evaluated: %d | Tailored: %d | Submitted: %d",
                    metrics.get("total_scanned", 0),
                    metrics.get("jobs_discovered", 0),
                    metrics.get("jobs_evaluated", 0),
                    metrics.get("cvs_tailored", 0),
                    metrics.get("applications_submitted", 0),
                )
            except Exception as e:
                logger.error("Unexpected error in pipeline cycle: %s", e, exc_info=True)

            logger.info("Sleeping for %d seconds before next cycle...", interval_seconds)
            try:
                for _ in range(interval_seconds):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                break

        logger.info("Continuous pipeline loop terminated.")
