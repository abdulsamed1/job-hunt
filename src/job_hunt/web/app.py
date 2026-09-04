"""FastAPI backend application for the Autonomous Job Hunt Mission Control Dashboard."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from job_hunt.llm.client import FreeLLMClient
from job_hunt.orchestrator import PipelineOrchestrator
from job_hunt.storage import Storage

logger = logging.getLogger("job-hunt-web")

app = FastAPI(
    title="Autonomous Job Hunt Mission Control",
    description="High-Throughput Autonomous Job Application Agent Dashboard",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "data/jobs.db"
LOG_PATH = "data/job_hunt.log"
SCREENSHOTS_DIR = Path("data/screenshots")
CVS_DIR = Path("data/cvs")
STATIC_DIR = Path(__file__).parent / "static"

# Activity tracking for async pipeline actions
system_activity: Dict[str, Any] = {
    "busy": False,
    "action": None,
    "started_at": None,
    "last_result": None,
    "error": None,
}


def get_storage() -> Storage:
    return Storage(DB_PATH)


class ActionRequest(BaseModel):
    limit: Optional[int] = None
    threshold: Optional[float] = 70.0
    live: Optional[bool] = False
    max_sources: Optional[int] = None


async def _execute_async_action(action_name: str, coro_fn, *args, **kwargs):
    global system_activity
    system_activity["busy"] = True
    system_activity["action"] = action_name
    system_activity["started_at"] = datetime.now(timezone.utc).isoformat()
    system_activity["error"] = None
    try:
        result = await coro_fn(*args, **kwargs)
        system_activity["last_result"] = {
            "action": action_name,
            "status": "success",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data": result,
        }
    except Exception as e:
        logger.exception("Error executing async action %s: %s", action_name, e)
        system_activity["error"] = str(e)
        system_activity["last_result"] = {
            "action": action_name,
            "status": "error",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }
    finally:
        system_activity["busy"] = False


# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    storage = get_storage()
    llm = FreeLLMClient()
    llm_alive = llm.is_alive()
    return {
        "status": "online",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected" if Path(DB_PATH).exists() else "uninitialized",
        "freellmapi": "online" if llm_alive else "offline",
        "activity": system_activity,
    }


@app.get("/api/stats")
async def api_stats() -> Dict[str, Any]:
    storage = get_storage()
    summary = storage.get_summary_stats()
    daily = storage.get_daily_metrics()
    llm = FreeLLMClient()
    llm_alive = llm.is_alive()

    sources_count = 0
    sources_path = Path("config/sources.yaml")
    if sources_path.exists():
        try:
            with open(sources_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                sources_count = len(data.get("sources", []))
        except Exception:
            pass

    return {
        "summary": summary,
        "daily": daily,
        "sources_count": sources_count,
        "llm_status": {
            "alive": llm_alive,
            "endpoint": llm.base_url,
        },
        "activity": system_activity,
    }


@app.get("/api/jobs")
async def api_jobs(
    state: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    storage = get_storage()
    items, total = storage.get_jobs_filtered(
        state=state,
        source=source,
        search=search,
        min_score=min_score,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "jobs": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: int) -> Dict[str, Any]:
    storage = get_storage()
    detail = storage.get_job_detail(job_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return detail


@app.get("/api/cv/{job_id}")
async def api_get_cv(job_id: int):
    storage = get_storage()
    cv = storage.get_tailored_cv(job_id)
    if cv and cv.pdf_path and Path(cv.pdf_path).exists():
        return FileResponse(cv.pdf_path, media_type="application/pdf", filename=f"CV_Job_{job_id}.pdf")

    # Fallback to master candidate CV
    master_pdf = Path("Abdulsamed_Hamdy.pdf")
    if master_pdf.exists():
        return FileResponse(master_pdf, media_type="application/pdf", filename="Abdulsamed_Hamdy.pdf")

    raise HTTPException(status_code=404, detail="CV PDF not found")


@app.get("/api/screenshots/{filename}")
async def api_get_screenshot(filename: str):
    safe_path = (SCREENSHOTS_DIR / filename).resolve()
    if not str(safe_path).startswith(str(SCREENSHOTS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(safe_path, media_type="image/png")


@app.get("/api/logs")
async def api_logs(lines: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    log_file = Path(LOG_PATH)
    if not log_file.exists():
        return {"lines": [], "count": 0}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent = [line.rstrip() for line in all_lines[-lines:]]
            return {"lines": recent, "count": len(recent)}
    except Exception as e:
        return {"lines": [f"Error reading log file: {e}"], "count": 1}


@app.get("/api/sources")
async def api_sources() -> Dict[str, Any]:
    sources_path = Path("config/sources.yaml")
    if not sources_path.exists():
        return {"sources": []}
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            sources = data.get("sources", [])
            return {"sources": sources, "total": len(sources)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TRIGGER ACTIONS
# ============================================================================


@app.post("/api/actions/scan")
async def api_action_scan(req: ActionRequest, background_tasks: BackgroundTasks):
    if system_activity["busy"]:
        raise HTTPException(status_code=409, detail=f"System is busy with {system_activity['action']}")

    orchestrator = PipelineOrchestrator(db_path=DB_PATH)
    background_tasks.add_task(
        _execute_async_action,
        "DISCOVERY_SCAN",
        orchestrator.run_discovery_stage,
        max_sources=req.max_sources or req.limit,
    )
    return {"message": "Discovery scan initiated in background", "status": "started"}


@app.post("/api/actions/evaluate")
async def api_action_evaluate(req: ActionRequest, background_tasks: BackgroundTasks):
    if system_activity["busy"]:
        raise HTTPException(status_code=409, detail=f"System is busy with {system_activity['action']}")

    orchestrator = PipelineOrchestrator(db_path=DB_PATH)
    background_tasks.add_task(
        _execute_async_action,
        "EVALUATION_BATCH",
        orchestrator.run_evaluation_stage,
        limit=req.limit or 100,
        threshold=req.threshold or 70.0,
    )
    return {"message": "Job evaluation initiated in background", "status": "started"}


@app.post("/api/actions/tailor")
async def api_action_tailor(req: ActionRequest, background_tasks: BackgroundTasks):
    if system_activity["busy"]:
        raise HTTPException(status_code=409, detail=f"System is busy with {system_activity['action']}")

    orchestrator = PipelineOrchestrator(db_path=DB_PATH)
    background_tasks.add_task(
        _execute_async_action,
        "TAILOR_BATCH",
        orchestrator.run_tailoring_stage,
        limit=req.limit or 50,
    )
    return {"message": "CV tailoring initiated in background", "status": "started"}


@app.post("/api/actions/apply")
async def api_action_apply(req: ActionRequest, background_tasks: BackgroundTasks):
    if system_activity["busy"]:
        raise HTTPException(status_code=409, detail=f"System is busy with {system_activity['action']}")

    orchestrator = PipelineOrchestrator(db_path=DB_PATH)
    dry_run = not req.live
    background_tasks.add_task(
        _execute_async_action,
        "APPLICATION_BATCH",
        orchestrator.run_application_stage,
        limit=req.limit or 10,
        dry_run=dry_run,
    )
    return {"message": f"Applications initiated ({'LIVE' if req.live else 'DRY-RUN'})", "status": "started"}


@app.post("/api/actions/cycle")
async def api_action_cycle(req: ActionRequest, background_tasks: BackgroundTasks):
    if system_activity["busy"]:
        raise HTTPException(status_code=409, detail=f"System is busy with {system_activity['action']}")

    orchestrator = PipelineOrchestrator(db_path=DB_PATH)
    dry_run = not req.live
    background_tasks.add_task(
        _execute_async_action,
        "FULL_PIPELINE_CYCLE",
        orchestrator.run_single_cycle,
        dry_run=dry_run,
        max_sources=req.max_sources,
    )
    return {"message": f"Full pipeline cycle initiated ({'LIVE' if req.live else 'DRY-RUN'})", "status": "started"}


# Static index.html fallback
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Job Hunt Mission Control UI Loading...</h1>")
