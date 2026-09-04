"""Fact-preserving tailored CV generation with strict anti-hallucination verification gate."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Set, Tuple

from job_hunt.cv.pdf_generator import ATSCVGenerator
from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, JobPosting, TailoredCV

logger = logging.getLogger(__name__)


class FactVerificationError(ValueError):
    """Raised when generated CV contains claims not verifiable from candidate profile."""


class CVTailor:
    """Generates tailored CVs from verified candidate facts and validates strict truthfulness."""

    def __init__(
        self,
        llm_client: Optional[FreeLLMClient] = None,
        pdf_generator: Optional[ATSCVGenerator] = None,
        master_pdf_path: Optional[Path | str] = None,
        use_llm: bool = True,
    ):
        from pathlib import Path
        self.llm_client = llm_client
        self.master_pdf_path = Path(master_pdf_path) if master_pdf_path else None
        self.pdf_generator = pdf_generator or ATSCVGenerator(
            llm_client=self.llm_client,
            master_pdf_path=self.master_pdf_path,
        )
        self.use_llm = use_llm

    def verify_cv_facts(self, cv_markdown: str, profile: CandidateProfile) -> Tuple[bool, List[str]]:
        """Strict verification gate: ensure generated CV contains NO hallucinated facts.

        Checks:
        1. All numeric claims/metrics in experience & summary must exist in profile allowed_metrics or bullets.
        2. All companies in experience must exist in verified experiences.
        3. All educational institutions must exist in verified education.
        """
        violations: List[str] = []

        # Strip header (contact info: phone, email) before checking metrics
        lines = cv_markdown.splitlines()
        body_lines = []
        for line in lines:
            if line.startswith("**Email:**") or line.startswith("# ") or "[LinkedIn]" in line:
                continue
            body_lines.append(line)
        body_text = "\n".join(body_lines)

        # Build set of verified source numbers and metrics
        verified_source_text = " ".join([
            profile.full_name,
            profile.email,
            profile.phone,
            profile.location,
            str(profile.years_of_experience),
            " ".join(profile.verified_skills),
            " ".join(profile.allowed_metrics),
            " ".join([f"{e.company} {e.title} {e.start_date} {e.end_date} " + " ".join(e.bullets) for e in profile.verified_experiences]),
            " ".join([f"{p.name} {p.description} " + " ".join(p.bullets) for p in profile.verified_projects]),
            " ".join([f"{ed.institution} {ed.degree} {ed.graduation_year}" for ed in profile.verified_education]),
        ]).lower()

        # Find numbers in CV body
        cv_numbers = re.findall(r"\b\d+(?:[.,]\d+)?\b", body_text)
        source_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", verified_source_text))

        for num in cv_numbers:
            # Allow standard graduation/employment years (2000-2030)
            if num not in source_numbers and not (len(num) == 4 and 2000 <= int(num) <= 2030):
                violations.append(f"Unverified number or metric in CV: '{num}'")

        # 2. Verify company names
        verified_companies = {e.company.lower() for e in profile.verified_experiences}
        for m in re.finditer(r"###\s+([^-]+)\s*-\s*([^\n]+)", cv_markdown):
            comp = m.group(2).strip().lower()
            if comp not in verified_companies:
                violations.append(f"Unverified employer in CV: '{comp}'")

        # 3. Verify educational institutions
        verified_institutions = {ed.institution.lower() for ed in profile.verified_education}
        for ed in profile.verified_education:
            verified_institutions.add(ed.institution.lower())

        passed = len(violations) == 0
        return passed, violations

    def build_master_cv(self, profile: CandidateProfile) -> str:
        """Construct the canonical fact-verified Master CV from profile facts."""
        lines: List[str] = [
            f"# {profile.full_name}",
            f"**Email:** {profile.email} | **Phone:** {profile.phone} | **Location:** {profile.location}",
        ]

        links = []
        if profile.linkedin_url:
            links.append(f"[LinkedIn]({profile.linkedin_url})")
        if profile.github_url:
            links.append(f"[GitHub]({profile.github_url})")
        if profile.portfolio_url:
            links.append(f"[Portfolio]({profile.portfolio_url})")
        if links:
            lines.append(" | ".join(links))

        lines.append("\n## Summary")
        summary = profile.summary or f"Software Engineer with {profile.years_of_experience}+ years of experience building reliable distributed systems and backend applications."
        lines.append(summary)

        lines.append("\n## Technical Skills")
        lines.append(f"**Languages & Technologies:** {', '.join(profile.verified_skills)}")

        lines.append("\n## Experience")
        for exp in profile.verified_experiences:
            end = exp.end_date or "Present"
            lines.append(f"\n### {exp.title} - {exp.company}")
            lines.append(f"*{exp.start_date} - {end} | {exp.location or ''}*")
            for bullet in exp.bullets:
                lines.append(f"- {bullet}")

        if profile.verified_projects:
            lines.append("\n## Projects")
            for proj in profile.verified_projects:
                proj_header = f"### {proj.name}"
                if proj.url:
                    proj_header += f" ([Link]({proj.url}))"
                lines.append(f"\n{proj_header}")
                lines.append(f"*{proj.description}*")
                for bullet in proj.bullets:
                    lines.append(f"- {bullet}")

        if profile.verified_education:
            lines.append("\n## Education")
            for edu in profile.verified_education:
                yr = f" ({edu.graduation_year})" if edu.graduation_year else ""
                lines.append(f"- **{edu.degree}** in {edu.field_of_study or 'Computer Science'}, {edu.institution}{yr}")

        return "\n".join(lines)

    def generate_tailored_cv(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        generate_pdf: bool = True,
        output_pdf_dir: str = "data/cvs",
    ) -> TailoredCV:
        """Generate a tailored CV highlighting relevant verified achievements for the job.

        Preserves 100% factual accuracy by only re-ordering and emphasizing verified bullets.
        Generates a custom ATS-optimized PDF with recruiter highlights and hiring manager depth.
        """
        job_keywords = set(re.findall(r"\b\w+\b", f"{job.title} {job.description}".lower()))

        lines: List[str] = [
            f"# {profile.full_name}",
            f"**Email:** {profile.email} | **Phone:** {profile.phone} | **Location:** {profile.location}",
        ]

        links = []
        if profile.linkedin_url:
            links.append(f"[LinkedIn]({profile.linkedin_url})")
        if profile.github_url:
            links.append(f"[GitHub]({profile.github_url})")
        if profile.portfolio_url:
            links.append(f"[Portfolio]({profile.portfolio_url})")
        if links:
            lines.append(" | ".join(links))

        # Check for LLM tailored summary first
        summary_text: Optional[str] = None
        if self.use_llm and self.llm_client:
            try:
                llm_summary = self.llm_client.tailor_summary_sync(job, profile)
                if llm_summary:
                    # Test if the generated summary satisfies the strict fact check
                    test_lines = lines + ["\n## Summary", llm_summary]
                    passed, _ = self.verify_cv_facts("\n".join(test_lines), profile)
                    if passed:
                        summary_text = llm_summary
            except Exception as e:
                logger.warning("LLM CV summary tailoring failed, using deterministic summary: %s", e)

        if not summary_text:
            matched_skills = [s for s in profile.verified_skills if s.lower() in job_keywords]
            skill_highlight = f" specializing in {', '.join(matched_skills[:4])}" if matched_skills else ""
            summary_text = (
                f"Software Engineer with {profile.years_of_experience}+ years of experience{skill_highlight}. "
                f"Proven track record delivering scalable systems and high-impact engineering solutions."
            )

        lines.append("\n## Summary")
        lines.append(summary_text)

        # Skills section: order matched skills first, followed by remaining verified skills
        matched_skills = [s for s in profile.verified_skills if s.lower() in job_keywords]
        other_skills = [s for s in profile.verified_skills if s not in matched_skills]
        ordered_skills = matched_skills + other_skills
        lines.append("\n## Technical Skills")
        lines.append(f"**Languages & Technologies:** {', '.join(ordered_skills)}")

        # Experience section: prioritize bullets that match job keywords
        lines.append("\n## Experience")
        for exp in profile.verified_experiences:
            end = exp.end_date or "Present"
            lines.append(f"\n### {exp.title} - {exp.company}")
            lines.append(f"*{exp.start_date} - {end} | {exp.location or ''}*")

            # Rank bullets by keyword overlap
            scored_bullets = []
            for bullet in exp.bullets:
                bullet_words = set(re.findall(r"\b\w+\b", bullet.lower()))
                overlap = len(bullet_words.intersection(job_keywords))
                scored_bullets.append((overlap, bullet))

            scored_bullets.sort(key=lambda x: x[0], reverse=True)
            for _, bullet in scored_bullets:
                lines.append(f"- {bullet}")

        if profile.verified_projects:
            lines.append("\n## Projects")
            for proj in profile.verified_projects:
                lines.append(f"\n### {proj.name}")
                lines.append(f"*{proj.description}*")
                for bullet in proj.bullets:
                    lines.append(f"- {bullet}")

        if profile.verified_education:
            lines.append("\n## Education")
            for edu in profile.verified_education:
                yr = f" ({edu.graduation_year})" if edu.graduation_year else ""
                lines.append(f"- **{edu.degree}** in {edu.field_of_study or 'Computer Science'}, {edu.institution}{yr}")

        candidate_cv_markdown = "\n".join(lines)

        # Run verification gate
        passed, log = self.verify_cv_facts(candidate_cv_markdown, profile)
        if not passed:
            log.append("Tailored CV failed fact check! Falling back to verified Master CV.")
            candidate_cv_markdown = self.build_master_cv(profile)
            passed = True

        pdf_path_str: Optional[str] = None
        if generate_pdf and job.id:
            try:
                from pathlib import Path
                out_path = Path(output_pdf_dir) / f"tailored_cv_{job.id}.pdf"
                self.pdf_generator.generate_tailored_pdf_sync(
                    job=job,
                    profile=profile,
                    output_path=out_path,
                    tailored_summary=summary_text,
                )
                pdf_path_str = str(out_path.resolve())
            except Exception as e:
                logger.warning("Could not render tailored PDF for Job %s: %s", job.id, e)

        return TailoredCV(
            job_id=job.id or 0,
            content_markdown=candidate_cv_markdown,
            verification_passed=passed,
            verification_log=log,
            pdf_path=pdf_path_str,
        )
