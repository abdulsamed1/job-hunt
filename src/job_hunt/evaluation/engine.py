"""Deterministic pre-filtering and structured AI/heuristic evaluation engine."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from job_hunt.models import CandidateProfile, EvaluationResult, JobPosting

# Positive role patterns for Software Engineer & related roles
SWE_ROLE_PATTERNS = [
    re.compile(r"\bsoftware\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bbackend\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bfrontend\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bfull\s*stack\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bsystems?\s+engineer\b", re.IGNORECASE),
    re.compile(r"\binfrastructure\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bplatform\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bsite\s+reliability\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bsre\b", re.IGNORECASE),
    re.compile(r"\bdistributed\s+systems\b", re.IGNORECASE),
    re.compile(r"\bcloud\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bdevops\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bml\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bmachine\s+learning\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bdata\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bpython\s+(?:engineer|developer)\b", re.IGNORECASE),
]

# Negative patterns: non-engineering or out-of-scope roles
NEGATIVE_ROLE_PATTERNS = [
    re.compile(r"\bmarketing\b", re.IGNORECASE),
    re.compile(r"\bsales\b", re.IGNORECASE),
    re.compile(r"\brecruiter\b", re.IGNORECASE),
    re.compile(r"\btalent\s+acquisition\b", re.IGNORECASE),
    re.compile(r"\bhuman\s+resources\b", re.IGNORECASE),
    re.compile(r"\baccount\s+executive\b", re.IGNORECASE),
    re.compile(r"\baccountant\b", re.IGNORECASE),
    re.compile(r"\bfinance\b", re.IGNORECASE),
    re.compile(r"\blegal\b", re.IGNORECASE),
    re.compile(r"\bnurse\b", re.IGNORECASE),
    re.compile(r"\bmedical\b", re.IGNORECASE),
    re.compile(r"\bdriver\b", re.IGNORECASE),
    re.compile(r"\bcustomer\s+support\b", re.IGNORECASE),
    re.compile(r"\bcontent\s+writer\b", re.IGNORECASE),
    re.compile(r"\bgraphic\s+designer\b", re.IGNORECASE),
    re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE),
]

CLEARANCE_PATTERNS = [
    re.compile(r"\bactive\s+top\s+secret\b", re.IGNORECASE),
    re.compile(r"\bts/sci\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+clearance\s+required\b", re.IGNORECASE),
    re.compile(r"\bpolygraph\s+required\b", re.IGNORECASE),
]


class EvaluationEngine:
    """Evaluates job postings against a candidate profile using pre-filters and scoring."""

    def __init__(self, min_score_threshold: float = 70.0):
        self.min_score_threshold = min_score_threshold

    def pre_filter(self, job: JobPosting, profile: CandidateProfile) -> Tuple[bool, Optional[str]]:
        """Run fast deterministic pre-filters on title, clearance, and location."""
        title = job.title.strip()

        # Check negative role patterns
        for neg_pat in NEGATIVE_ROLE_PATTERNS:
            if neg_pat.search(title):
                return False, f"Title matches negative filter: {neg_pat.pattern}"

        # Check positive SWE role patterns
        matched_swe = any(pos_pat.search(title) for pos_pat in SWE_ROLE_PATTERNS)
        if not matched_swe:
            return False, "Title does not match targeted Software Engineer roles"

        # Check clearance requirements in description
        desc = job.description or ""
        for cl_pat in CLEARANCE_PATTERNS:
            if cl_pat.search(desc):
                return False, f"Requires security clearance: {cl_pat.pattern}"

        return True, None

    def evaluate(self, job: JobPosting, profile: CandidateProfile) -> EvaluationResult:
        """Evaluate a job against candidate profile, computing match score and eligibility."""
        # 1. First run pre-filter
        passed, reason = self.pre_filter(job, profile)
        if not passed:
            return EvaluationResult(
                job_id=job.id,
                score=0.0,
                eligible=False,
                matched_skills=[],
                missing_skills=[],
                reasoning=f"Pre-filtered out: {reason}",
                pre_filtered=True,
                pre_filter_reason=reason,
            )

        # 2. Extract technical keywords from job description
        text_to_scan = f"{job.title} {job.description}".lower()

        matched_skills: List[str] = []
        missing_skills: List[str] = []

        # Candidate verified skills check
        verified_skills_lower = {s.lower(): s for s in profile.verified_skills}

        for skill_lower, skill_original in verified_skills_lower.items():
            pattern = rf"\b{re.escape(skill_lower)}\b"
            if re.search(pattern, text_to_scan):
                matched_skills.append(skill_original)

        # Expanded keyword vocabulary
        common_tech_keywords = [
            "python", "go", "golang", "rust", "java", "c++", "c", "assembly", "rtos",
            "embedded", "kernel", "typescript", "javascript", "react", "node", "django",
            "fastapi", "flask", "kubernetes", "docker", "aws", "gcp", "azure", "graphql",
            "sql", "postgresql", "mysql", "redis", "kafka", "rabbitmq", "grpc", "linux",
            "ci/cd", "terraform"
        ]

        for kw in common_tech_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text_to_scan):
                if kw not in [m.lower() for m in matched_skills]:
                    missing_skills.append(kw)

        # 3. Calculate score components
        # A) Skill match ratio (weight 55)
        if matched_skills and missing_skills:
            total = len(matched_skills) + len(missing_skills)
            skill_score = (len(matched_skills) / total) * 55.0
        elif matched_skills and not missing_skills:
            skill_score = 55.0
        elif not matched_skills and missing_skills:
            skill_score = 0.0  # Zero match when requirements detected but none satisfied
        else:
            skill_score = 30.0  # General baseline when no specific tech mentioned

        # B) Seniority match (weight 25)
        # Match "10+ years" or "5 years of experience"
        yoe_match = re.search(r"(\d+)\+?\s*years?", text_to_scan)
        seniority_score = 25.0
        if yoe_match:
            req_years = int(yoe_match.group(1))
            if profile.years_of_experience >= req_years:
                seniority_score = 25.0
            elif profile.years_of_experience >= req_years - 2:
                seniority_score = 12.0
            else:
                seniority_score = 0.0

        # C) Location / Remote match (weight 20)
        loc = (job.location or "").lower()
        location_score = 20.0
        if "remote" in loc:
            location_score = 20.0
        elif any(country in loc for country in ("us", "united states", "remote us")):
            location_score = 20.0 if "us" in profile.location.lower() else 5.0
        elif "hybrid" in loc or "office" in loc or "in-office" in loc:
            cand_city = profile.location.split(",")[0].strip().lower()
            if cand_city and cand_city in loc:
                location_score = 20.0
            else:
                location_score = 0.0

        total_score = min(100.0, round(skill_score + seniority_score + location_score, 1))
        is_eligible = total_score >= self.min_score_threshold

        reasoning = (
            f"Score: {total_score}/100 (Skills: {skill_score:.1f}/55, Seniority: {seniority_score:.1f}/25, "
            f"Location: {location_score:.1f}/20). Matched {len(matched_skills)} verified skills: {', '.join(matched_skills[:5])}."
        )

        return EvaluationResult(
            job_id=job.id,
            score=total_score,
            eligible=is_eligible,
            matched_skills=matched_skills,
            missing_skills=missing_skills[:10],
            reasoning=reasoning,
            pre_filtered=False,
        )
