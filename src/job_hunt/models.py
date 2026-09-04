"""Data schemas and lifecycle states for autonomous job application agent."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class JobState(str, Enum):
    """Lifecycle state machine states for a job posting."""

    DISCOVERED = "DISCOVERED"
    DUPLICATE = "DUPLICATE"
    PRE_FILTERED_OUT = "PRE_FILTERED_OUT"
    EVALUATED = "EVALUATED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    TAILORED = "TAILORED"
    APPLICATION_STARTED = "APPLICATION_STARTED"
    SUBMITTED = "SUBMITTED"
    BLOCKED_CAPTCHA = "BLOCKED_CAPTCHA"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"


VALID_TRANSITIONS: Dict[JobState, List[JobState]] = {
    JobState.DISCOVERED: [
        JobState.DUPLICATE,
        JobState.PRE_FILTERED_OUT,
        JobState.EVALUATED,
        JobState.ELIGIBLE,
        JobState.REJECTED,
    ],
    JobState.DUPLICATE: [],
    JobState.PRE_FILTERED_OUT: [],
    JobState.EVALUATED: [
        JobState.ELIGIBLE,
        JobState.REJECTED,
    ],
    JobState.ELIGIBLE: [
        JobState.TAILORED,
        JobState.REJECTED,
        JobState.APPLICATION_STARTED,
    ],
    JobState.REJECTED: [
        JobState.ELIGIBLE,  # re-evaluation override
    ],
    JobState.TAILORED: [
        JobState.APPLICATION_STARTED,
        JobState.FAILED,
    ],
    JobState.APPLICATION_STARTED: [
        JobState.SUBMITTED,
        JobState.BLOCKED_CAPTCHA,
        JobState.FAILED,
        JobState.RETRY_PENDING,
    ],
    JobState.SUBMITTED: [],
    JobState.BLOCKED_CAPTCHA: [
        JobState.APPLICATION_STARTED,
        JobState.FAILED,
        JobState.RETRY_PENDING,
    ],
    JobState.FAILED: [
        JobState.RETRY_PENDING,
        JobState.APPLICATION_STARTED,
    ],
    JobState.RETRY_PENDING: [
        JobState.APPLICATION_STARTED,
        JobState.FAILED,
    ],
}


def is_valid_transition(from_state: JobState, to_state: JobState) -> bool:
    """Check if state transition is allowed in the state machine."""
    if from_state == to_state:
        return True
    return to_state in VALID_TRANSITIONS.get(from_state, [])


class JobPosting(BaseModel):
    """Normalized job posting record."""

    id: Optional[int] = None
    external_id: Optional[str] = None
    source: str
    source_name: Optional[str] = None
    title: str
    company: str
    raw_url: str
    canonical_url: str
    canonical_url_hash: str
    role_fingerprint: str
    content_hash: str
    location: Optional[str] = None
    description: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    state: JobState = JobState.DISCOVERED
    posted_at: Optional[str] = None
    created_at: Optional[str] = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Optional[str] = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Experience(BaseModel):
    """Verified candidate employment history record."""

    company: str
    title: str
    start_date: str
    end_date: Optional[str] = "Present"
    location: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)


class Project(BaseModel):
    """Verified candidate personal/open-source project."""

    name: str
    description: str
    bullets: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    url: Optional[str] = None


class Education(BaseModel):
    """Verified candidate educational qualification."""

    institution: str
    degree: str
    field_of_study: Optional[str] = None
    graduation_year: Optional[int] = None
    location: Optional[str] = None


class CandidateProfile(BaseModel):
    """Comprehensive, fact-checked candidate profile."""

    full_name: str
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str
    work_authorization: str = "Authorized to work in Egypt, Remote Worldwide"
    sponsorship_required: bool = False
    open_to_remote: bool = True
    remote_only: bool = False
    target_locations: List[str] = Field(
        default_factory=lambda: ["Remote", "Worldwide", "US", "Europe", "UK", "Global", "EMEA"]
    )
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    years_of_experience: int = 5
    summary: Optional[str] = None
    verified_skills: List[str] = Field(default_factory=list)
    verified_experiences: List[Experience] = Field(default_factory=list)
    verified_projects: List[Project] = Field(default_factory=list)
    verified_education: List[Education] = Field(default_factory=list)
    allowed_metrics: List[str] = Field(default_factory=list)
    custom_answers: Dict[str, str] = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    """Result of deterministic and AI fit evaluation."""

    job_id: Optional[int] = None
    score: float = 0.0
    eligible: bool = False
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    reasoning: str = ""
    pre_filtered: bool = False
    pre_filter_reason: Optional[str] = None


class TailoredCV(BaseModel):
    """Fact-verified tailored CV generated for a specific job."""

    job_id: int
    content_markdown: str
    verification_passed: bool = False
    verification_log: List[str] = Field(default_factory=list)
    created_at: Optional[str] = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ApplicationRecord(BaseModel):
    """Record of an automated application submission."""

    id: Optional[int] = None
    job_id: int
    state: JobState
    attempt_count: int = 1
    applied_at: Optional[str] = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    submission_payload: Dict[str, Any] = Field(default_factory=dict)
    screenshot_path: Optional[str] = None
    confirmation_text: Optional[str] = None
    error_message: Optional[str] = None


class AuditEntry(BaseModel):
    """Immutable audit trail record for state transitions."""

    id: Optional[int] = None
    job_id: int
    from_state: Optional[str] = None
    to_state: str
    timestamp: str
    details: Optional[str] = None
    error: Optional[str] = None
