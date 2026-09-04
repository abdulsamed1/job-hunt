"""Command-line interface for the Autonomous Job Application System."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys


from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
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

    # scan
    p_scan = subparsers.add_parser("scan", help="Discover and deduplicate jobs from sources")
    p_scan.add_argument("--sources", default="config/sources.yaml", help="Path to sources YAML")
    p_scan.add_argument("--limit", type=int, default=None, help="Limit number of sources to scan")
    p_scan.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Pre-filter and evaluate discovered jobs")
    p_eval.add_argument("--limit", type=int, default=100, help="Max jobs to evaluate")
    p_eval.add_argument("--threshold", type=float, default=70.0, help="Eligibility score threshold")
    p_eval.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    # tailor
    p_tailor = subparsers.add_parser("tailor", help="Generate fact-verified tailored CVs for eligible jobs")
    p_tailor.add_argument("--job-id", type=int, default=None, help="Tailor for specific job ID")
    p_tailor.add_argument("--limit", type=int, default=50, help="Max CVs to generate")
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
    p_run.add_argument("--live", action="store_true", help="Execute live submission (default is dry-run)")
    p_run.add_argument("--sources-limit", type=int, default=None, help="Limit sources per cycle")
    p_run.add_argument("--db", default="data/jobs.db", help="Path to SQLite database")

    return parser


def cmd_status(args: argparse.Namespace) -> None:
    storage = Storage(args.db)
    stats = storage.get_summary_stats()
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
    print("======================================================================\n")


def cmd_scan(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db, sources_path=args.sources)
    new_jobs = asyncio.run(orch.run_discovery_stage(max_sources=args.limit))
    print(f"Scan completed: {new_jobs} new unique jobs added to tracker.")


def cmd_evaluate(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db)
    orch.eval_engine.min_score_threshold = args.threshold
    count = orch.run_evaluation_stage(limit=args.limit)
    print(f"Evaluation completed: {count} jobs processed.")


def cmd_tailor(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db)
    count = orch.run_cv_stage(limit=args.limit)
    print(f"CV Tailoring completed: {count} tailored CVs produced and fact-checked.")


def cmd_apply(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db)
    dry_run = not args.live
    count = asyncio.run(orch.run_application_stage(limit=args.limit, dry_run=dry_run))
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"Application stage completed ({mode}): {count} applications processed.")


def cmd_run(args: argparse.Namespace) -> None:
    orch = PipelineOrchestrator(db_path=args.db)
    dry_run = not args.live

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        orch.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(
            orch.start_continuous_loop(interval_seconds=args.interval, dry_run=dry_run)
        )
    finally:
        loop.close()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    elif args.command == "tailor":
        cmd_tailor(args)
    elif args.command == "apply":
        cmd_apply(args)
    elif args.command == "run":
        cmd_run(args)


if __name__ == "__main__":
    main()
