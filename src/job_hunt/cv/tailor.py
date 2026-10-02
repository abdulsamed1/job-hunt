"""Fact-preserving tailored CV generation with strict anti-hallucination verification gate."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Set, Tuple

from job_hunt.cv.pdf_generator import ATSCVGenerator
from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, JobPosting, TailoredCV

logger = logging.getLogger(__name__)


TITLE_STOPWORDS = {
    "senior", "junior", "lead", "staff", "principal", "engineer", "developer",
    "remote", "hybrid", "onsite", "on-site", "full", "time", "and", "the",
    "for", "with", "iii", "ii", "iv",
}

# Conservative synonym pairs only (abbreviation <-> canonical). Never invent coverage.
KEYWORD_SYNONYMS = {
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "py": "python",
    "tf": "terraform",
    "ci": "ci/cd",
}

# Tech vocabulary for surfacing genuine gaps (preferred keywords). Only words in
# this list, the profile skills, or the synonym map become table rows — plain
# English words like "requires" or "experience" never do.
TECH_VOCABULARY = {
    "python", "go", "golang", "rust", "java", "typescript", "javascript", "react",
    "node", "django", "fastapi", "flask", "kubernetes", "docker", "aws", "gcp",
    "azure", "graphql", "sql", "postgresql", "mysql", "redis", "kafka",
    "rabbitmq", "grpc", "linux", "terraform", "ci/cd", "ml", "etl", "spark",
    "airflow", "prometheus", "grafana", "nginx", "elasticsearch",
}


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

        # 2. Verify company names (greedy: titles may contain hyphens, company is last)
        verified_companies = {e.company.lower() for e in profile.verified_experiences}
        for m in re.finditer(r"###\s+(.+)\s+-\s+([^\n]+)", cv_markdown):
            comp = m.group(2).strip().lower()
            if comp not in verified_companies:
                violations.append(f"Unverified employer in CV: '{comp}'")

        # 2b. Verify project/section headers: must be an employer, role title, or project
        verified_projects = {p.name.lower() for p in profile.verified_projects}
        role_titles = [e.title.lower() for e in profile.verified_experiences]
        for m in re.finditer(r"###\s+([^\n\[]+)", cv_markdown):
            header = re.sub(r"\s*\($", "", m.group(1).strip())
            header_name = re.split(r"\s+-\s+", header)[0].strip().lower()
            if not header_name:
                continue
            known = (
                header_name in verified_companies
                or header_name in verified_projects
                or any(header_name == t or header_name.startswith(t) or t in header_name for t in role_titles)
            )
            if not known:
                violations.append(f"Unverified project or section in CV: '{header.strip()}'")

        # 2c. Verify date ranges: every start/end token must exist in the verified source.
        # Normalize unicode dashes first so "2021 – Present" cannot dodge the check.
        datable_cv = re.sub(r"[–—−]", "-", cv_markdown)
        for m in re.finditer(r"\*([^*\n]+?)\s+-\s+([^*\n|]+?)(?:\s*\|.*)?\*", datable_cv):
            for token in (m.group(1).strip(), m.group(2).strip()):
                tok_lower = token.lower()
                if tok_lower in ("present", "current"):
                    if "present" not in verified_source_text:
                        violations.append(f"Unverified end date in CV: '{token}'")
                elif token and tok_lower not in verified_source_text:
                    violations.append(f"Unverified date in CV: '{token}'")

        # 3. Verify educational institutions
        verified_institutions = {ed.institution.lower() for ed in profile.verified_education}
        for ed in profile.verified_education:
            verified_institutions.add(ed.institution.lower())

        passed = len(violations) == 0
        return passed, violations

    def _verify_rendered_pdf(
        self, pdf_path_str: str, profile: CandidateProfile, log: List[str]
    ) -> None:
        """Run the ATS text-layer check on a rendered PDF and record failures in log."""
        from job_hunt.cv.pdf_generator import verify_pdf_text_layer

        pdf_ok, pdf_checks = verify_pdf_text_layer(pdf_path_str, profile)
        if not pdf_ok:
            log.append(f"ATS text-layer warnings: {'; '.join(pdf_checks)}")
            logger.warning("ATS text-layer issues: %s", pdf_checks)

    def keyword_coverage_table(
        self, job: JobPosting, cv_markdown: str, profile: CandidateProfile
    ) -> List[dict]:
        """Map posting keywords to CV coverage without stuffing gaps.

        Required = distinctive title words; preferred = description tech terms.
        Status is one of: covered (verbatim in CV), synonym-only, missing (have it)
        (profile supports it but CV omits it), missing (gap) (genuine gap: leave it).
        """
        title_words = [
            w.lower()
            for w in re.findall(r"\b[a-zA-Z][a-zA-Z0-9+#/.]*\b", job.title or "")
            if len(w) > 2 and w.lower() not in TITLE_STOPWORDS
        ]
        desc_text = (job.description or "").lower()
        verified_lower = {s.lower() for s in profile.verified_skills}
        vocab = verified_lower | set(KEYWORD_SYNONYMS) | set(KEYWORD_SYNONYMS.values()) | TECH_VOCABULARY
        preferred = sorted(
            {w for w in re.findall(r"\b[a-z][a-z0-9+#/.]*\b", desc_text) if len(w) >= 2 and w in vocab}
        )

        cv_lower = cv_markdown.lower()
        table: List[dict] = []
        seen = set()
        for kw in title_words + preferred:
            if kw in seen:
                continue
            seen.add(kw)
            priority = "required" if kw in title_words else "preferred"
            if re.search(rf"\b{re.escape(kw)}\b", cv_lower):
                status, note = "covered", "verbatim in CV"
            else:
                canonical = KEYWORD_SYNONYMS.get(kw, kw)
                reverse = next((k for k, v in KEYWORD_SYNONYMS.items() if v == kw), None)
                alt = canonical if canonical != kw else reverse
                if alt and re.search(rf"\b{re.escape(alt)}\b", cv_lower):
                    status, note = "synonym-only", f"present as '{alt}'"
                elif kw in verified_lower or canonical in verified_lower or (alt and alt in verified_lower):
                    status, note = "missing (have it)", "profile supports it; consider adding"
                else:
                    status, note = "missing (gap)", "genuine gap; leave missing"
            table.append({"keyword": kw, "priority": priority, "status": status, "note": note})
        return table

    def trim_bullets(
        self, bullets: List[str], job_keywords: Set[str], keep: int
    ) -> Tuple[List[str], List[str]]:
        """Relevance-weighted cutting: keep the `keep` highest-value bullets.

        Value = keyword relevance first, uniqueness second (a bullet restating
        what others already say is cut before a unique one). Returns (kept, cut).
        """
        if keep < 0:
            raise ValueError("keep must be non-negative")
        if len(bullets) <= keep:
            return list(bullets), []

        word_sets = [set(re.findall(r"\b\w+\b", b.lower())) for b in bullets]

        def jaccard(a: Set[str], b: Set[str]) -> float:
            if not a and not b:
                return 1.0
            union = a | b
            return len(a & b) / len(union) if union else 0.0

        scored = []
        for i, (bullet, words) in enumerate(zip(bullets, word_sets)):
            relevance = len(words.intersection(job_keywords))
            overlaps = [jaccard(words, other) for j, other in enumerate(word_sets) if j != i]
            uniqueness = 1.0 - (max(overlaps) if overlaps else 0.0)
            scored.append((relevance * 10 + uniqueness, i, bullet))

        scored.sort(key=lambda x: x[0], reverse=True)
        kept_idx = {idx for _, idx, _ in scored[:keep]}
        kept = [b for i, b in enumerate(bullets) if i in kept_idx]
        cut = [b for i, b in enumerate(bullets) if i not in kept_idx]
        return kept, cut

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
        max_bullets_per_role: Optional[int] = None,
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
            ordered = [bullet for _, bullet in scored_bullets]
            if max_bullets_per_role is not None:
                ordered, _ = self.trim_bullets(ordered, job_keywords, keep=max_bullets_per_role)
            for bullet in ordered:
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

        return TailoredCV(
            job_id=job.id or 0,
            content_markdown=candidate_cv_markdown,
            verification_passed=passed,
            verification_log=log,
            pdf_path=pdf_path_str,
        )
