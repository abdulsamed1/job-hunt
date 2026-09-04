"""Job-tailored, ATS-optimized PDF CV generator with multi-stage recruiter and hiring manager layout."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_CHROME_PATH = os.environ.get("CHROME_PATH", "/usr/bin/google-chrome")


class ATSCVGenerator:
    """Generates professional, multi-page PDFs tailored to jobs with an invisible ATS keyword layer."""

    def __init__(
        self,
        llm_client: Optional[FreeLLMClient] = None,
        chrome_path: str = DEFAULT_CHROME_PATH,
    ):
        self.llm_client = llm_client
        self.chrome_path = chrome_path

    def generate_ats_keyword_stream(
        self,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> str:
        """Generate a clean, single-line comma-separated list of all relevant job keywords for ATS indexing."""
        # 1. If LLM is available, use the specialized ATS compression prompt
        if self.llm_client and self.llm_client.is_alive():
            try:
                system_prompt = (
                    "Please convert the following text into a clean, comma-separated list "
                    "(like: item1, item2, item3, ...), without bullet points or asterisks. "
                    "Maintain the original order and preserve any important punctuation or parentheses. "
                    "The output should be in one line."
                )
                user_content = f"Title: {job.title}\nCompany: {job.company}\nDescription:\n{(job.description or '')[:3500]}"
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ]
                res = self.llm_client.chat_completion_sync(messages, temperature=0.1, max_tokens=1000)
                if res:
                    # Strip think tags or newlines
                    res = re.sub(r"<think>.*?</think>", "", res, flags=re.DOTALL).strip()
                    cleaned = " ".join(res.splitlines()).strip().strip('"')
                    if len(cleaned) > 20:
                        return cleaned
            except Exception as e:
                logger.debug("LLM ATS keyword compression failed, using heuristic: %s", e)

        # 2. Deterministic keyword extraction fallback
        clean_desc = re.sub(r"<[^>]+>", " ", job.description or "")
        clean_desc = html.unescape(clean_desc)
        clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

        # 1. Title, Company, verified skills
        items: List[str] = [job.title, job.company]
        items.extend(profile.verified_skills)

        # 2. Extract technical keywords and phrases
        chunks = [c.strip() for c in re.split(r"[,;.\n•\-–]", clean_desc) if len(c.strip()) > 2]
        for c in chunks:
            c_clean = re.sub(r"[#*`\"'()\[\]{}]", "", c).strip()
            if 3 < len(c_clean) < 60 and not c_clean.lower().startswith("http"):
                items.append(c_clean)

        # Deduplicate preserving order
        seen = set()
        deduped = []
        for it in items:
            key = it.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(it)

        return ", ".join(deduped[:80])

    def build_html_cv(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        ats_keywords_one_line: str,
        tailored_summary: Optional[str] = None,
    ) -> str:
        """Construct semantic, responsive HTML/CSS adhering to recruiter highlights & hiring manager depth."""
        # Recruiter highlight summary
        summary = tailored_summary or profile.summary or (
            f"Software Engineer with {profile.years_of_experience}+ years of experience building scalable, "
            f"high-performance distributed backend architectures and web platforms."
        )

        # Contact & Links
        links_html = []
        if profile.linkedin_url:
            links_html.append(f'<a href="{html.escape(profile.linkedin_url)}">LinkedIn</a>')
        if profile.github_url:
            links_html.append(f'<a href="{html.escape(profile.github_url)}">GitHub</a>')
        if profile.portfolio_url:
            links_html.append(f'<a href="{html.escape(profile.portfolio_url)}">Portfolio</a>')
        links_str = " &bull; ".join(links_html)

        # Technical skills matrix
        skills_html = ", ".join([f'<span class="skill-tag">{html.escape(s)}</span>' for s in profile.verified_skills])

        # Experience items (with company context, product scope, achievements)
        exp_html = []
        for exp in profile.verified_experiences:
            end = exp.end_date or "Present"
            bullets_html = "".join([f"<li>{html.escape(b)}</li>" for b in exp.bullets])
            tech_str = ", ".join(exp.technologies) if exp.technologies else ""
            tech_line = f'<div class="exp-tech"><strong>Core Tech:</strong> {html.escape(tech_str)}</div>' if tech_str else ""

            exp_html.append(f"""
            <div class="experience-card">
                <div class="exp-header">
                    <span class="exp-title">{html.escape(exp.title)}</span> &mdash; <span class="exp-company">{html.escape(exp.company)}</span>
                    <span class="exp-date">{html.escape(exp.start_date)} &ndash; {html.escape(end)}</span>
                </div>
                <div class="exp-location">{html.escape(exp.location or 'Remote')}</div>
                <ul class="exp-bullets">
                    {bullets_html}
                </ul>
                {tech_line}
            </div>
            """)

        # Projects
        proj_html = []
        for proj in profile.verified_projects:
            link = f' (<a href="{html.escape(proj.url)}">Link</a>)' if proj.url else ""
            bullets = "".join([f"<li>{html.escape(b)}</li>" for b in proj.bullets])
            tech = f'<div class="exp-tech"><strong>Technologies:</strong> {html.escape(", ".join(proj.technologies))}</div>' if proj.technologies else ""
            proj_html.append(f"""
            <div class="project-card">
                <div class="proj-title">{html.escape(proj.name)}{link}</div>
                <div class="proj-desc">{html.escape(proj.description)}</div>
                <ul class="proj-bullets">{bullets}</ul>
                {tech}
            </div>
            """)

        # Education
        edu_html = []
        for edu in profile.verified_education:
            yr = f" ({edu.graduation_year})" if edu.graduation_year else ""
            field = f" in {html.escape(edu.field_of_study)}" if edu.field_of_study else ""
            edu_html.append(f"""
            <div class="edu-card">
                <strong>{html.escape(edu.degree)}</strong>{field} &mdash; {html.escape(edu.institution)}{yr}
            </div>
            """)

        # Full HTML document
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(profile.full_name)} - Resume</title>
<style>
    @page {{
        size: A4;
        margin: 18mm 16mm 18mm 16mm;
    }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color: #24292f;
        font-size: 10pt;
        line-height: 1.45;
        margin: 0;
        padding: 0;
    }}
    a {{ color: #0969da; text-decoration: none; }}
    .header {{
        border-bottom: 2px solid #0969da;
        padding-bottom: 8px;
        margin-bottom: 14px;
    }}
    .name {{
        font-size: 20pt;
        font-weight: 700;
        letter-spacing: -0.5px;
        margin: 0;
        color: #1f2328;
    }}
    .contact {{
        font-size: 9.5pt;
        color: #57609a;
        margin-top: 4px;
    }}
    .section-title {{
        font-size: 11.5pt;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: #0969da;
        border-bottom: 1px solid #d0d7de;
        padding-bottom: 3px;
        margin-top: 14px;
        margin-bottom: 8px;
    }}
    .summary-text {{
        font-size: 9.5pt;
        color: #333;
        margin-bottom: 10px;
    }}
    .skills-container {{
        font-size: 9pt;
        margin-bottom: 10px;
    }}
    .skill-tag {{
        display: inline-block;
        background: #f6f8fa;
        border: 1px solid #d0d7de;
        border-radius: 4px;
        padding: 1px 6px;
        margin: 2px;
        font-weight: 500;
    }}
    .experience-card, .project-card {{
        margin-bottom: 12px;
    }}
    .exp-header {{
        display: flex;
        justify-content: space-between;
        font-weight: 600;
        font-size: 10pt;
    }}
    .exp-title {{ color: #1f2328; }}
    .exp-company {{ color: #0969da; }}
    .exp-date {{ font-weight: normal; color: #57609a; font-size: 9pt; }}
    .exp-location {{ font-style: italic; font-size: 8.5pt; color: #6e7781; margin-bottom: 4px; }}
    ul.exp-bullets, ul.proj-bullets {{
        margin: 4px 0 6px 18px;
        padding: 0;
        font-size: 9pt;
    }}
    ul.exp-bullets li, ul.proj-bullets li {{
        margin-bottom: 3px;
    }}
    .exp-tech {{
        font-size: 8.5pt;
        color: #57609a;
        margin-top: 2px;
    }}
    .proj-title {{
        font-weight: 600;
        font-size: 9.5pt;
    }}
    .proj-desc {{
        font-size: 8.5pt;
        font-style: italic;
        color: #57609a;
    }}
    .edu-card {{
        font-size: 9pt;
        margin-bottom: 4px;
    }}
    /* INVISIBLE ATS KEYWORD LAYER:
       White on white text, 2.5pt, zero line height, fully parseable by Poppler pdftotext
       and ATS engines (Greenhouse, Lever, Workday, Ashby), 100% invisible to human eyes */
    .ats-keyword-bypass {{
        color: #ffffff !important;
        background-color: #ffffff !important;
        font-size: 2.5pt !important;
        line-height: 2.5pt !important;
        letter-spacing: 0px !important;
        margin-top: 15px !important;
        user-select: text !important;
        opacity: 0.01 !important;
        overflow: hidden !important;
    }}
</style>
</head>
<body>

<div class="header">
    <div class="name">{html.escape(profile.full_name)}</div>
    <div class="contact">
        {html.escape(profile.email)} &bull; {html.escape(profile.phone)} &bull; {html.escape(profile.location)} (Remote Worldwide)
        <br>{links_str}
    </div>
</div>

<div class="section-title">Professional Summary</div>
<div class="summary-text">{html.escape(summary)}</div>

<div class="section-title">Technical Expertise</div>
<div class="skills-container">{skills_html}</div>

<div class="section-title">Work Experience</div>
{"".join(exp_html)}

<div class="section-title">Key Projects & Open Source</div>
{"".join(proj_html)}

<div class="section-title">Education</div>
{"".join(edu_html)}

<!-- ATS OPTIMIZATION LAYER: Contains one-line comma-separated job requirements -->
<div class="ats-keyword-bypass">
    {html.escape(ats_keywords_one_line)}
</div>

</body>
</html>
"""

    async def generate_tailored_pdf(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        output_path: Path,
        tailored_summary: Optional[str] = None,
    ) -> Path:
        """Render a pixel-perfect, fact-checked tailored PDF with invisible ATS keyword layer."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ats_keywords = self.generate_ats_keyword_stream(job, profile)
        html_content = self.build_html_cv(
            job=job,
            profile=profile,
            ats_keywords_one_line=ats_keywords,
            tailored_summary=tailored_summary,
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=self.chrome_path,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            page = await browser.new_page()
            await page.set_content(html_content, wait_until="networkidle")
            await page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
            )
            await browser.close()

        logger.info("Generated tailored ATS-optimized PDF CV for Job %s at: %s", job.id, output_path)
        return output_path

    def generate_tailored_pdf_sync(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        output_path: Path,
        tailored_summary: Optional[str] = None,
    ) -> Path:
        """Synchronous wrapper for generate_tailored_pdf."""
        try:
            return asyncio.run(
                self.generate_tailored_pdf(job, profile, output_path, tailored_summary)
            )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.generate_tailored_pdf(job, profile, output_path, tailored_summary)
                )
            finally:
                loop.close()
