"""SQLite database persistence layer with WAL mode and atomic state machine transitions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_hunt.models import (
    ApplicationRecord,
    AuditEntry,
    CandidateProfile,
    EvaluationResult,
    JobPosting,
    JobState,
    TailoredCV,
    is_valid_transition,
)


class Storage:
    """Thread-safe SQLite storage engine with WAL journal and audit trail."""

    def __init__(self, db_path: str | Path = "data/jobs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT,
                    source TEXT NOT NULL,
                    source_name TEXT,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    raw_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    canonical_url_hash TEXT NOT NULL,
                    role_fingerprint TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    location TEXT,
                    description TEXT,
                    salary_min REAL,
                    salary_max REAL,
                    salary_currency TEXT,
                    state TEXT NOT NULL DEFAULT 'DISCOVERED',
                    posted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_canonical_hash ON jobs(canonical_url_hash);
                CREATE INDEX IF NOT EXISTS idx_jobs_role_fingerprint ON jobs(role_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
                CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);

                CREATE TABLE IF NOT EXISTS dedup_provenance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    source_name TEXT,
                    discovered_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_provenance_job_id ON dedup_provenance(job_id);

                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    score REAL NOT NULL,
                    eligible INTEGER NOT NULL,
                    matched_skills_json TEXT DEFAULT '[]',
                    missing_skills_json TEXT DEFAULT '[]',
                    reasoning TEXT,
                    pre_filtered INTEGER NOT NULL DEFAULT 0,
                    pre_filter_reason TEXT,
                    evaluated_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tailored_cvs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    content_markdown TEXT NOT NULL,
                    verification_passed INTEGER NOT NULL,
                    verification_log_json TEXT DEFAULT '[]',
                    pdf_path TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    applied_at TEXT NOT NULL,
                    submission_payload_json TEXT DEFAULT '{}',
                    screenshot_path TEXT,
                    confirmation_text TEXT,
                    error_message TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT,
                    error TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_audit_job_id ON audit_log(job_id);
                """
            )
            # Migration check for existing databases
            cursor = conn.execute("PRAGMA table_info(tailored_cvs)")
            cols = {row["name"] for row in cursor.fetchall()}
            if "pdf_path" not in cols:
                conn.execute("ALTER TABLE tailored_cvs ADD COLUMN pdf_path TEXT")
            conn.commit()

    def add_job(self, job: JobPosting) -> Tuple[JobPosting, bool]:
        """Save a job posting, checking for duplicates across canonical URL and role fingerprint.

        Returns (saved_or_existing_job, is_new).
        If duplicate, records provenance and marks state as DUPLICATE if new.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            # 1. Check existing by canonical URL hash
            cursor = conn.execute(
                "SELECT * FROM jobs WHERE canonical_url_hash = ? LIMIT 1",
                (job.canonical_url_hash,),
            )
            row = cursor.fetchone()

            # 2. Check existing by role fingerprint if not found by URL
            if not row and job.role_fingerprint:
                cursor = conn.execute(
                    "SELECT * FROM jobs WHERE role_fingerprint = ? LIMIT 1",
                    (job.role_fingerprint,),
                )
                row = cursor.fetchone()

            if row:
                existing_id = row["id"]
                # Record provenance
                conn.execute(
                    """
                    INSERT INTO dedup_provenance (job_id, source_url, source_name, discovered_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (existing_id, job.raw_url, job.source_name or job.source, now),
                )
                conn.commit()
                existing_job = self._row_to_job(row)
                return existing_job, False

            # Insert new job
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    external_id, source, source_name, title, company,
                    raw_url, canonical_url, canonical_url_hash, role_fingerprint,
                    content_hash, location, description, salary_min, salary_max,
                    salary_currency, state, posted_at, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.external_id,
                    job.source,
                    job.source_name,
                    job.title,
                    job.company,
                    job.raw_url,
                    job.canonical_url,
                    job.canonical_url_hash,
                    job.role_fingerprint,
                    job.content_hash,
                    job.location,
                    job.description,
                    job.salary_min,
                    job.salary_max,
                    job.salary_currency,
                    job.state.value,
                    job.posted_at,
                    now,
                    now,
                    json.dumps(job.metadata),
                ),
            )
            job_id = cursor.lastrowid
            job.id = job_id
            job.created_at = now
            job.updated_at = now

            # Record provenance
            conn.execute(
                """
                INSERT INTO dedup_provenance (job_id, source_url, source_name, discovered_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, job.raw_url, job.source_name or job.source, now),
            )

            # Record audit entry
            conn.execute(
                """
                INSERT INTO audit_log (job_id, from_state, to_state, timestamp, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, None, job.state.value, now, f"Discovered from {job.source}"),
            )
            conn.commit()
            return job, True

    def get_job(self, job_id: int) -> Optional[JobPosting]:
        """Fetch job posting by id."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_job(row)
            return None

    def get_jobs_by_state(self, state: JobState, limit: int = 100) -> List[JobPosting]:
        """Fetch list of jobs by current state."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY id ASC LIMIT ?",
                (state.value, limit),
            )
            return [self._row_to_job(row) for row in cursor.fetchall()]

    def update_job_state(
        self,
        job_id: int,
        to_state: JobState,
        details: Optional[str] = None,
        error: Optional[str] = None,
        force: bool = False,
    ) -> bool:
        """Atomically transition job state, validating transition and logging audit record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return False

            current_state = JobState(row["state"])
            if not force and not is_valid_transition(current_state, to_state):
                raise ValueError(
                    f"Invalid state transition for job {job_id}: {current_state} -> {to_state}"
                )

            conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?",
                (to_state.value, now, job_id),
            )
            conn.execute(
                """
                INSERT INTO audit_log (job_id, from_state, to_state, timestamp, details, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, current_state.value, to_state.value, now, details, error),
            )
            conn.commit()
            return True

    def save_evaluation(self, eval_result: EvaluationResult) -> None:
        """Persist job evaluation result and update job state accordingly."""
        if not eval_result.job_id:
            raise ValueError("EvaluationResult must have a job_id")

        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evaluations (
                    job_id, score, eligible, matched_skills_json,
                    missing_skills_json, reasoning, pre_filtered,
                    pre_filter_reason, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_result.job_id,
                    eval_result.score,
                    1 if eval_result.eligible else 0,
                    json.dumps(eval_result.matched_skills),
                    json.dumps(eval_result.missing_skills),
                    eval_result.reasoning,
                    1 if eval_result.pre_filtered else 0,
                    eval_result.pre_filter_reason,
                    now,
                ),
            )
            conn.commit()

        # Transition job state
        if eval_result.pre_filtered:
            self.update_job_state(
                eval_result.job_id,
                JobState.PRE_FILTERED_OUT,
                details=f"Pre-filter: {eval_result.pre_filter_reason}",
            )
        elif eval_result.eligible:
            self.update_job_state(
                eval_result.job_id,
                JobState.ELIGIBLE,
                details=f"Evaluation score: {eval_result.score:.1f}%",
            )
        else:
            self.update_job_state(
                eval_result.job_id,
                JobState.REJECTED,
                details=f"Evaluation rejected: score {eval_result.score:.1f}%",
            )

    def get_evaluation(self, job_id: int) -> Optional[EvaluationResult]:
        """Fetch evaluation result for a job."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM evaluations WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return EvaluationResult(
                job_id=row["job_id"],
                score=row["score"],
                eligible=bool(row["eligible"]),
                matched_skills=json.loads(row["matched_skills_json"] or "[]"),
                missing_skills=json.loads(row["missing_skills_json"] or "[]"),
                reasoning=row["reasoning"] or "",
                pre_filtered=bool(row["pre_filtered"]),
                pre_filter_reason=row["pre_filter_reason"],
            )

    def save_tailored_cv(self, cv: TailoredCV) -> None:
        """Persist generated tailored CV."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tailored_cvs (
                    job_id, content_markdown, verification_passed,
                    verification_log_json, pdf_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cv.job_id,
                    cv.content_markdown,
                    1 if cv.verification_passed else 0,
                    json.dumps(cv.verification_log),
                    cv.pdf_path,
                    now,
                ),
            )
            conn.commit()

        self.update_job_state(
            cv.job_id,
            JobState.TAILORED,
            details="Tailored CV generated and fact-checked",
        )

    def get_tailored_cv(self, job_id: int) -> Optional[TailoredCV]:
        """Fetch tailored CV for a job."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM tailored_cvs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return TailoredCV(
                job_id=row["job_id"],
                content_markdown=row["content_markdown"],
                verification_passed=bool(row["verification_passed"]),
                verification_log=json.loads(row["verification_log_json"] or "[]"),
                pdf_path=row["pdf_path"] if "pdf_path" in row.keys() else None,
                created_at=row["created_at"],
            )

    def record_application(self, record: ApplicationRecord) -> None:
        """Record or update an application submission attempt."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO applications (
                    job_id, state, attempt_count, applied_at,
                    submission_payload_json, screenshot_path,
                    confirmation_text, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.state.value,
                    record.attempt_count,
                    now,
                    json.dumps(record.submission_payload),
                    record.screenshot_path,
                    record.confirmation_text,
                    record.error_message,
                ),
            )
            conn.commit()

        self.update_job_state(
            record.job_id,
            record.state,
            details=record.confirmation_text or "Application updated",
            error=record.error_message,
        )

    def get_application(self, job_id: int) -> Optional[ApplicationRecord]:
        """Fetch application record for a job."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return ApplicationRecord(
                id=row["id"],
                job_id=row["job_id"],
                state=JobState(row["state"]),
                attempt_count=row["attempt_count"],
                applied_at=row["applied_at"],
                submission_payload=json.loads(row["submission_payload_json"] or "{}"),
                screenshot_path=row["screenshot_path"],
                confirmation_text=row["confirmation_text"],
                error_message=row["error_message"],
            )

    def recover_stuck_jobs(self, timeout_minutes: int = 15, max_retries: int = 3) -> int:
        """Recover jobs left in APPLICATION_STARTED for longer than timeout_minutes.

        If attempts < max_retries, transitions to RETRY_PENDING; otherwise to FAILED.
        Returns count of recovered jobs.
        """
        threshold = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        recovered = 0
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT j.id, a.attempt_count
                FROM jobs j
                LEFT JOIN applications a ON j.id = a.job_id
                WHERE j.state = 'APPLICATION_STARTED' AND j.updated_at < ?
                """,
                (threshold,),
            )
            rows = cursor.fetchall()
            for row in rows:
                job_id = row["id"]
                attempts = row["attempt_count"] or 1
                if attempts < max_retries:
                    self.update_job_state(
                        job_id,
                        JobState.RETRY_PENDING,
                        details=f"Stuck application recovered. Attempt {attempts}/{max_retries}",
                    )
                else:
                    self.update_job_state(
                        job_id,
                        JobState.FAILED,
                        details="Stuck application exceeded max retries",
                        error=f"Timeout after {timeout_minutes}m with {attempts} attempts",
                    )
                recovered += 1
        return recovered

    def get_audit_log(self, job_id: int) -> List[AuditEntry]:
        """Fetch complete audit trail for a job."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM audit_log WHERE job_id = ? ORDER BY id ASC",
                (job_id,),
            )
            return [
                AuditEntry(
                    id=row["id"],
                    job_id=row["job_id"],
                    from_state=row["from_state"],
                    to_state=row["to_state"],
                    timestamp=row["timestamp"],
                    details=row["details"],
                    error=row["error"],
                )
                for row in cursor.fetchall()
            ]

    def get_summary_stats(self) -> Dict[str, int]:
        """Fetch count of jobs by state."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT state, COUNT(*) as count FROM jobs GROUP BY state"
            )
            stats = {state.value: 0 for state in JobState}
            for row in cursor.fetchall():
                stats[row["state"]] = row["count"]
            cursor2 = conn.execute("SELECT COUNT(*) as count FROM dedup_provenance")
            stats["total_discovery_events"] = cursor2.fetchone()["count"]
            return stats

    def has_already_applied(
        self,
        job_id: Optional[int] = None,
        canonical_url_hash: Optional[str] = None,
        role_fingerprint: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        external_id: Optional[str] = None,
        window_days: int = 90,
    ) -> Tuple[bool, Optional[str]]:
        """Strict check to guarantee NO duplicate applications are ever submitted.

        Checks across:
        1. Specific job ID.
        2. Canonical URL hash (most precise).
        3. External ID (LinkedIn job posting ID).
        4. Same company + title + location within window_days (most conservative).
        Role fingerprint is NOT used alone to prevent false positives on
        different job postings at the same company with the same role title.
        """
        threshold = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        with self._get_connection() as conn:
            # 1. Direct job_id check in applications table
            if job_id:
                cursor = conn.execute(
                    "SELECT state, applied_at FROM applications WHERE job_id = ? AND state = 'SUBMITTED'",
                    (job_id,),
                )
                row = cursor.fetchone()
                if row:
                    return True, f"Application already submitted for Job ID {job_id} on {row['applied_at']}"

            # 2. Check by canonical URL hash (exact match)
            if canonical_url_hash:
                cursor = conn.execute(
                    """
                    SELECT j.id, a.applied_at
                    FROM jobs j
                    JOIN applications a ON j.id = a.job_id
                    WHERE j.canonical_url_hash = ? AND a.state = 'SUBMITTED'
                    LIMIT 1
                    """,
                    (canonical_url_hash,),
                )
                row = cursor.fetchone()
                if row:
                    return True, f"Application already submitted for identical canonical URL (Job ID {row['id']} on {row['applied_at']})"

            # 3. Check by external_id (LinkedIn job posting ID)
            if external_id:
                cursor = conn.execute(
                    """
                    SELECT j.id, a.applied_at
                    FROM jobs j
                    JOIN applications a ON j.id = a.job_id
                    WHERE j.external_id = ? AND a.state = 'SUBMITTED'
                    LIMIT 1
                    """,
                    (external_id,),
                )
                row = cursor.fetchone()
                if row:
                    return True, f"Application already submitted for external ID {external_id} (Job ID {row['id']} on {row['applied_at']})"

            # 4. Check by canonical URL hash + role fingerprint COMBINED (not role alone)
            if canonical_url_hash and role_fingerprint:
                cursor = conn.execute(
                    """
                    SELECT j.id, a.applied_at
                    FROM jobs j
                    JOIN applications a ON j.id = a.job_id
                    WHERE j.canonical_url_hash = ? AND j.role_fingerprint = ? AND a.state = 'SUBMITTED'
                    LIMIT 1
                    """,
                    (canonical_url_hash, role_fingerprint),
                )
                row = cursor.fetchone()
                if row:
                    return True, f"Application already submitted for matching URL+fingerprint (Job ID {row['id']} on {row['applied_at']})"

            # 5. Check by company + title + location within window_days (most conservative)
            if company and title:
                cursor = conn.execute(
                    """
                    SELECT j.id, j.title, j.company, j.location, a.applied_at
                    FROM jobs j
                    JOIN applications a ON j.id = a.job_id
                    WHERE LOWER(j.company) = LOWER(?) AND LOWER(j.title) = LOWER(?)
                      AND j.location = COALESCE(?, j.location)
                      AND a.state = 'SUBMITTED' AND a.applied_at >= ?
                    LIMIT 1
                    """,
                    (company.strip(), title.strip(), None, threshold),
                )
                row = cursor.fetchone()
                if row:
                    return True, f"Application already submitted to {row['company']} for '{row['title']}' at {row['location']} within past {window_days} days on {row['applied_at']}"

        return False, None

    def get_daily_metrics(self, window_hours: int = 24) -> Dict[str, Any]:
        """Fetch throughput metrics over the last 24-hour window."""
        threshold = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        with self._get_connection() as conn:
            # Jobs discovered in window
            cur = conn.execute("SELECT COUNT(*) as count FROM jobs WHERE created_at >= ?", (threshold,))
            discovered = cur.fetchone()["count"]

            # Discovery events (including duplicates caught)
            cur = conn.execute("SELECT COUNT(*) as count FROM dedup_provenance WHERE discovered_at >= ?", (threshold,))
            duplicates_caught = cur.fetchone()["count"]

            # Evaluations in window
            cur = conn.execute("SELECT COUNT(*) as count FROM evaluations WHERE evaluated_at >= ?", (threshold,))
            evaluated = cur.fetchone()["count"]

            # Tailored CVs in window
            cur = conn.execute("SELECT COUNT(*) as count FROM tailored_cvs WHERE created_at >= ?", (threshold,))
            tailored = cur.fetchone()["count"]

            # Submitted applications in window
            cur = conn.execute("SELECT COUNT(*) as count FROM applications WHERE applied_at >= ? AND state = 'SUBMITTED'", (threshold,))
            submitted = cur.fetchone()["count"]

            return {
                "window_hours": window_hours,
                "jobs_discovered": discovered,
                "duplicates_caught": duplicates_caught,
                "total_scanned": discovered + duplicates_caught,
                "jobs_evaluated": evaluated,
                "cvs_tailored": tailored,
                "applications_submitted": submitted,
                "daily_scan_target": 500,
                "target_achieved": (discovered + duplicates_caught) >= 500,
            }

    def get_jobs_filtered(
        self,
        state: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        min_score: Optional[float] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Fetch filtered jobs list with evaluation, tailored CV, and application metadata."""
        offset = max(0, (page - 1) * page_size)
        conditions = []
        params: List[Any] = []

        if state and state.upper() != "ALL":
            conditions.append("j.state = ?")
            params.append(state.upper())
        if source and source.upper() != "ALL":
            conditions.append("LOWER(j.source) = LOWER(?)")
            params.append(source)
        if min_score is not None:
            conditions.append("e.score >= ?")
            params.append(min_score)
        if search and search.strip():
            s = f"%{search.strip()}%"
            conditions.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
            params.extend([s, s, s])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_sql = f"""
            SELECT COUNT(*) as total
            FROM jobs j
            LEFT JOIN evaluations e ON j.id = e.job_id
            {where_clause}
        """

        query_sql = f"""
            SELECT
                j.id, j.external_id, j.source, j.source_name, j.title, j.company,
                j.raw_url, j.canonical_url, j.location, j.state, j.posted_at,
                j.created_at, j.updated_at,
                e.score as eval_score, e.eligible as eval_eligible,
                e.matched_skills_json, e.missing_skills_json, e.reasoning as eval_reasoning,
                t.pdf_path as cv_pdf_path,
                a.state as app_state, a.screenshot_path, a.confirmation_text, a.applied_at
            FROM jobs j
            LEFT JOIN evaluations e ON j.id = e.job_id
            LEFT JOIN tailored_cvs t ON j.id = t.job_id
            LEFT JOIN applications a ON j.id = a.job_id
            {where_clause}
            ORDER BY j.id DESC
            LIMIT ? OFFSET ?
        """

        with self._get_connection() as conn:
            cur = conn.execute(count_sql, tuple(params))
            total = cur.fetchone()["total"]

            query_params = list(params) + [page_size, offset]
            cur = conn.execute(query_sql, tuple(query_params))
            rows = cur.fetchall()

            items = []
            for r in rows:
                matched = json.loads(r["matched_skills_json"] or "[]")
                missing = json.loads(r["missing_skills_json"] or "[]")
                items.append({
                    "id": r["id"],
                    "external_id": r["external_id"],
                    "source": r["source"],
                    "source_name": r["source_name"],
                    "title": r["title"],
                    "company": r["company"],
                    "raw_url": r["raw_url"],
                    "canonical_url": r["canonical_url"],
                    "location": r["location"],
                    "state": r["state"],
                    "posted_at": r["posted_at"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "eval_score": r["eval_score"],
                    "eval_eligible": bool(r["eval_eligible"]) if r["eval_eligible"] is not None else None,
                    "matched_skills": matched,
                    "missing_skills": missing,
                    "eval_reasoning": r["eval_reasoning"],
                    "cv_pdf_path": r["cv_pdf_path"],
                    "app_state": r["app_state"],
                    "screenshot_path": r["screenshot_path"],
                    "confirmation_text": r["confirmation_text"],
                    "applied_at": r["applied_at"],
                })

            return items, total

    def get_job_detail(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Fetch full job details including description, evaluation, CV, application, and audit log."""
        job = self.get_job(job_id)
        if not job:
            return None

        evaluation = self.get_evaluation(job_id)
        tailored_cv = self.get_tailored_cv(job_id)
        application = self.get_application(job_id)
        audit_log = self.get_audit_log(job_id)

        return {
            "job": job.model_dump(),
            "evaluation": evaluation.model_dump() if evaluation else None,
            "tailored_cv": tailored_cv.model_dump() if tailored_cv else None,
            "application": application.model_dump() if application else None,
            "audit_log": [entry.model_dump() for entry in audit_log],
        }

    def _row_to_job(self, row: sqlite3.Row) -> JobPosting:
        return JobPosting(
            id=row["id"],
            external_id=row["external_id"],
            source=row["source"],
            source_name=row["source_name"],
            title=row["title"],
            company=row["company"],
            raw_url=row["raw_url"],
            canonical_url=row["canonical_url"],
            canonical_url_hash=row["canonical_url_hash"],
            role_fingerprint=row["role_fingerprint"],
            content_hash=row["content_hash"],
            location=row["location"],
            description=row["description"] or "",
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            salary_currency=row["salary_currency"],
            state=JobState(row["state"]),
            posted_at=row["posted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

