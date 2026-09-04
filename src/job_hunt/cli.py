"""Command-line interface for the Autonomous Job Application System."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from pathlib import Path

from job_hunt.llm.client import FreeLLMClient
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage

Path("data").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/job_hunt.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("job-hunt-cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-hunt",
        description="Low-Cost Autonomous Job-Application Agent for Software Engineers",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Display current tracker statistics and state summary")
    p_status.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # llm-status
    p_llm = subparsers.add_parser("llm-status", help="Check FreeLLMAPI connectivity and available models")
    p_llm.add_argument("--llm-url", default="http://127.0.0.1:4000/v1", help="FreeLLMAPI base URL")

    # scan
    p_scan = subparsers.add_parser("scan", help="Discover and deduplicate jobs from sources")
    p_scan.add_argument("--sources", default="config/sources.yaml", help="Path to sources YAML")
    p_scan.add_argument("--limit", type=int, default=None, help="Limit number of sources to scan")
    p_scan.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Pre-filter and evaluate discovered jobs")
    p_eval.add_argument("--limit", type=int, default=100, help="Max jobs to evaluate")
    p_eval.add_argument("--threshold", type=float, default=70.0, help="Eligibility score threshold")
    p_eval.add_argument("--llm-url", default="http://127.0.0.1:4000/v1", help="FreeLLMAPI base URL")
    p_eval.add_argument("--no-llm", action="store_true", help="Disable LLM and use deterministic scoring only")
    p_eval.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # tailor
    p_tailor = subparsers.add_parser("tailor", help="Generate fact-verified tailored CVs for eligible jobs")
    p_tailor.add_argument("--job-id", type=int, default=None, help="Tailor for specific job ID")
    p_tailor.add_argument("--limit", type=int, default=50, help="Max CVs to generate")
    p_tailor.add_argument("--llm-url", default="http://127.0.0.1:4000/v1", help="FreeLLMAPI base URL")
    p_tailor.add_argument("--no-llm", action="store_true", help="Disable LLM and use deterministic tailoring only")
    p_tailor.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # apply
    p_apply = subparsers.add_parser("apply", help="Execute browser automation form filling")
    p_apply.add_argument("--job-id", type=int, default=None, help="Apply to specific job ID")
    p_apply.add_argument("--limit", type=int, default=10, help="Max applications to process")
    p_apply.add_argument("--live", action="store_true", help="Execute live submission (default is dry-run)")
    p_apply.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # run
    p_run = subparsers.add_parser("run", help="Run 24/7 continuous autonomous loop")
    p_run.add_argument("--interval", type=int, default=3600, help="Cycle interval in seconds (default: 3600)")
    p_run.add_argument("--once", action="store_true", help="Execute a single pipeline cycle and exit")
    p_run.add_argument("--live", action="store_true", help="Execute live submission (default is dry-run)")
    p_run.add_argument("--sources-limit", type=int, default=None, help="Limit sources per cycle")
    p_run.add_argument("--llm-url", default="http://127.0.0.1:4000/v1", help="FreeLLMAPI base URL")
    p_run.add_argument("--no-llm", action="store_true", help="Disable LLM and use deterministic evaluation only")
    p_run.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    return parser


def cmd_status(args: argparse.Namespace) -> None:
    storage = Storage(args.db)
    stats = storage.get_summary_stats()
    daily = storage.get_daily_metrics()

    print("\n==================== AUTONOMOUS JOB AGENT TRACKER ====================")
    print(f"Database: {args.db}")
    print("----------------------------------------------------------------------")
    print(f"{'State':<25} | {'Count':<10}")
    print("----------------------------------------------------------------------")
    for state, count in stats.items():
        if state != "total_discovery_events":
            print(f"{state:<25} | {count:<10}")
    print("----------------------------------------------------------------------")
    print(f"Total Discovery Events: {stats.get('total_discovery_events', 0)}")
    print("\n-------------------- DAILY THROUGHPUT (LAST 24H) ---------------------")
    target_status = "ACHIEVED [OK]" if daily.get("target_achieved") else f"IN PROGRESS ({daily.get('total_scanned', 0)}/500)"
    print(f"Daily Scan Target (500+):    {target_status}")
    print(f"Total Jobs Scanned (24h):    {daily.get('total_scanned', 0)}")
    print(f"  - New Discovered Jobs:     {daily.get('jobs_discovered', 0)}")
    print(f"  - Duplicates Blocked:      {daily.get('duplicates_caught', 0)}")
    print(f"Jobs Evaluated (24h):        {daily.get('jobs_evaluated', 0)}")
    print(f"Tailored CVs Prepared (24h): {daily.get('cvs_tailored', 0)}")
    print(f"Applications Submitted (24h):{daily.get('applications_submitted', 0)}")
    print("======================================================================\n")


def cmd_llm_status(args: argparse.Namespace) -> None:
    client = FreeLLMClient(base_url=args.llm_url)
    alive = client.is_alive()
    print("\n======================= FREELLMAPI STATUS =======================")
    print(f"Endpoint: {client.base_url}")
    print(f"Service Alive: {'YES [ACTIVE]' if alive else 'NO [OFFLINE]'}")
    if alive:
        print("Model Routing: Dynamic Auto-Failover (MemOS, Groq, Cerebras, SambaNova)")
        print("Inference Cost: $0.00 / token (Free Tier Aggregation)")
    else:
        print("Note: System will use deterministic heuristic scoring as zero-cost fallback.")
    print("=================================================================\n")


def cmd_scan(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db, sources_path=args.sources)
    new_jobs = asyncio.run(orch.run_discovery_stage(max_sources=args.limit))
    print(f"Scan completed: {new_jobs} new unique jobs added to tracker.")


def cmd_evaluate(args: argparse.Namespace) -> None:
    llm_client = FreeLLMClient(base_url=args.llm_url) if not args.no_llm else None
    orch = PipelineOrchestrator(
        db_path=args.db,
        llm_client=llm_client,
        use_llm=not args.no_llm,
    )
    orch.eval_engine.min_score_threshold = args.threshold
    count = asyncio.run(orch.run_evaluation_stage_async(limit=args.limit))
    print(f"Evaluation completed: {count} jobs processed.")


def cmd_tailor(args: argparse.Namespace) -> None:
    llm_client = FreeLLMClient(base_url=args.llm_url) if not args.no_llm else None
    orch = PipelineOrchestrator(
        db_path=args.db,
        llm_client=llm_client,
        use_llm=not args.no_llm,
    )
    if args.job_id is not None:
        job = orch.storage.get_job(args.job_id)
        if not job:
            print(f"Error: Job ID {args.job_id} not found.")
            return
        tailored_cv = orch.cv_tailor.generate_tailored_cv(job, orch.profile)
        orch.storage.save_tailored_cv(tailored_cv)
        print(f"CV tailoring completed for Job #{job.id} ({job.title} at {job.company}): {tailored_cv.pdf_path}")
        return

    count = orch.run_cv_stage(limit=args.limit)
    print(f"CV tailoring completed: {count} CVs generated.")


def cmd_apply(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db)
    mode = "LIVE" if args.live else "DRY-RUN"
    if args.job_id is not None:
        job = orch.storage.get_job(args.job_id)
        if not job:
            print(f"Error: Job ID {args.job_id} not found.")
            return
        tailored_pdf = Path(f"data/cvs/tailored_cv_{job.id}.pdf")
        if tailored_pdf.exists():
            resume_path = str(tailored_pdf.resolve())
        elif Path("Abdulsamed_Hamdy.pdf").exists():
            resume_path = str(Path("Abdulsamed_Hamdy.pdf").resolve())
        else:
            cv_dir = Path("data/cvs")
            cv_dir.mkdir(parents=True, exist_ok=True)
            cv_file = cv_dir / f"resume_{orch.profile.last_name.lower()}.txt"
            if not cv_file.exists():
                cv_file.write_text(orch.cv_tailor.build_master_cv(orch.profile), encoding="utf-8")
            resume_path = str(cv_file.resolve())

        orch.storage.update_job_state(
            job.id,
            JobState.APPLICATION_STARTED,
            details=f"Starting automated browser application session ({mode})",
        )
        record = asyncio.run(orch.browser_engine.fill_and_submit(
            job,
            orch.profile,
            resume_file_path=resume_path,
            dry_run=not args.live,
        ))
        orch.storage.record_application(record)
        print(f"Application stage completed for Job #{job.id} ({job.title}): {record.state} ({mode} mode).")
        return

    count = asyncio.run(orch.run_application_stage(limit=args.limit, dry_run=not args.live))
    print(f"Application stage completed: {count} applications processed ({mode} mode).")


def cmd_run(args: argparse.Namespace) -> None:
    llm_client = FreeLLMClient(base_url=args.llm_url) if not args.no_llm else None
    orch = PipelineOrchestrator(
        db_path=args.db,
        llm_client=llm_client,
        use_llm=not args.no_llm,
    )

    if args.once:
        logger.info("Executing single autonomous pipeline cycle (mode=%s)...", "LIVE" if args.live else "DRY-RUN")
        results = asyncio.run(orch.run_cycle(dry_run=not args.live, max_sources=args.sources_limit))
        print("\n==================== PIPELINE CYCLE SUMMARY ====================")
        for k, v in results.items():
            if k not in ("daily_metrics", "database_stats"):
                print(f"{k:<25}: {v}")
        print("================================================================\n")
        return

    def sig_handler(sig, frame):
        logger.info("Termination signal received. Shutting down loop...")
        orch.stop()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    logger.info("Starting autonomous 24/7 daemon loop (mode=%s)...", "LIVE" if args.live else "DRY-RUN")
    asyncio.run(orch.start_continuous_loop(interval_seconds=args.interval, dry_run=not args.live))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "status": cmd_status,
        "llm-status": cmd_llm_status,
        "scan": cmd_scan,
        "evaluate": cmd_evaluate,
        "tailor": cmd_tailor,
        "apply": cmd_apply,
        "run": cmd_run,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
