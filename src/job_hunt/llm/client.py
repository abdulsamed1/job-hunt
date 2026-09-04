"""FreeLLMAPI OpenAI-compatible client adapter with health checks, auto-start, and structured reasoning."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from job_hunt.models import CandidateProfile, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_FREELLMAPI_URL = os.environ.get("FREELLMAPI_URL", "http://127.0.0.1:4000/v1")
DEFAULT_FREELLMAPI_KEY = os.environ.get(
    "FREELLMAPI_API_KEY",
    "freellmapi-e674f70bf41220400eec3345b8437a1b8ab7bee594d03be0"
)
DEFAULT_FREELLMAPI_PATH = Path("/home/abdu/production/freellmapi")


class FreeLLMClient:
    """OpenAI-compatible client tailored for the local FreeLLMAPI gateway."""

    def __init__(
        self,
        base_url: str = DEFAULT_FREELLMAPI_URL,
        api_key: str = DEFAULT_FREELLMAPI_KEY,
        model: str = "auto",
        timeout: float = 60.0,
        freellmapi_path: Path = DEFAULT_FREELLMAPI_PATH,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.freellmapi_path = freellmapi_path
        # Extract ping root (e.g., http://127.0.0.1:4000)
        parts = self.base_url.split("/v1")
        self.root_url = parts[0] if parts else self.base_url

    def is_alive(self) -> bool:
        """Synchronously check if FreeLLMAPI endpoint is responding."""
        try:
            with httpx.Client(timeout=3.0) as client:
                res = client.get(f"{self.root_url}/api/ping")
                if res.status_code == 200:
                    return True
        except Exception:
            pass

        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            with httpx.Client(timeout=3.0) as client:
                res = client.get(f"{self.base_url}/models", headers=headers)
                return res.status_code == 200
        except Exception:
            return False

    async def is_alive_async(self) -> bool:
        """Asynchronously check if FreeLLMAPI endpoint is responding."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.root_url}/api/ping")
                if res.status_code == 200:
                    return True
        except Exception:
            pass

        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/models", headers=headers)
                return res.status_code == 200
        except Exception:
            return False

    def ensure_server_running(self) -> bool:
        """Ensure FreeLLMAPI is active; attempt auto-start if local installation exists."""
        if self.is_alive():
            return True

        port = "4000"
        m = re.search(r":(\d+)", self.base_url)
        if m:
            port = m.group(1)

        server_dist = self.freellmapi_path / "server" / "dist" / "index.js"
        if not server_dist.exists():
            logger.warning("FreeLLMAPI build not found at %s", server_dist)
            return False

        logger.info("Attempting to auto-start FreeLLMAPI on port %s...", port)
        env = dict(os.environ)
        env["PORT"] = port

        try:
            subprocess.Popen(
                ["node", str(server_dist)],
                cwd=str(self.freellmapi_path),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logger.error("Failed to spawn FreeLLMAPI process: %s", e)
            return False

        # Poll for readiness
        for _ in range(12):
            if self.is_alive():
                logger.info("FreeLLMAPI started successfully and responding on port %s.", port)
                return True
            import time
            time.sleep(0.5)

        logger.warning("FreeLLMAPI started but failed to respond within 6 seconds.")
        return False

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> Optional[str]:
        """Send chat completion request to FreeLLMAPI."""
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.warning("FreeLLMAPI error %d: %s", resp.status_code, resp.text[:200])
                    return None

                data = resp.json()
                choices = data.get("choices")
                if choices and len(choices) > 0:
                    content = choices[0].get("message", {}).get("content", "")
                    return content
                return None
        except Exception as e:
            logger.warning("FreeLLMAPI request failed (%s): %s", type(e).__name__, e)
            return None

    def chat_completion_sync(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        json_mode: bool = False,
    ) -> Optional[str]:
        """Synchronous wrapper for chat_completion."""
        try:
            return asyncio.run(
                self.chat_completion(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            )
        except RuntimeError:
            # If an event loop is already running in this thread, execute in a worker thread
            import concurrent.futures
            def _runner() -> Optional[str]:
                return asyncio.run(
                    self.chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                    )
                )
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(_runner).result()
        except Exception as e:
            logger.warning("Synchronous chat completion failed (%s): %s", type(e).__name__, e)
            return None

    async def evaluate_job(
        self,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate job posting against candidate profile using FreeLLMAPI reasoning."""
        verified_skills_str = ", ".join(profile.verified_skills)
        experiences_str = "\n".join([
            f"- {exp.title} at {exp.company} ({exp.start_date} - {exp.end_date}): {'; '.join(exp.bullets[:3])}"
            for exp in profile.verified_experiences[:4]
        ])

        system_prompt = (
            "You are an expert technical recruiter and software engineering hiring evaluator. "
            "Your job is to objectively score the match between a candidate profile and a job posting.\n"
            "LOCATION & WORK MODE CRITERIA:\n"
            "- The candidate is based in Cairo, Egypt and is actively pursuing REMOTE positions worldwide (Global, US, Europe, UK, EMEA, Anywhere Remote) and international roles open to remote engineering talent.\n"
            "- If the job is Remote (Worldwide, Anywhere, Global, EMEA, Americas, US Remote, etc.), score location_fit at 100% (ideal match).\n"
            "- If the job is hybrid/flexible with remote options, score location_fit favorably.\n"
            "- Only penalize location if the job strictly mandates in-person on-site office attendance in a country outside Egypt with no remote option.\n"
            "Respond ONLY with a valid JSON object with the following exact keys:\n"
            "{\n"
            '  "score": <float between 0 and 100>,\n'
            '  "eligible": <true if score >= 70 else false>,\n'
            '  "matched_skills": [<list of candidate skills present in job>],\n'
            '  "missing_skills": [<list of required job skills candidate lacks>],\n'
            '  "seniority_fit": "<brief evaluation of years of experience and level>",\n'
            '  "location_fit": "<brief evaluation of remote/office location and global eligibility>",\n'
            '  "reasoning": "<concise 2-3 sentence executive summary of candidate fit>",\n'
            '  "pros": [<top 2-3 advantages>],\n'
            '  "cons": [<top 1-2 gaps or risks>]\n'
            "}"
        )

        user_content = (
            f"### CANDIDATE PROFILE:\n"
            f"Name: {profile.full_name}\n"
            f"Location: {profile.location} (Open to Remote Worldwide / US / EU / UK / EMEA)\n"
            f"Work Authorization: {profile.work_authorization}\n"
            f"Years of Experience: {profile.years_of_experience}\n"
            f"Verified Skills: {verified_skills_str}\n"
            f"Verified Experiences:\n{experiences_str}\n\n"
            f"### JOB POSTING:\n"
            f"Title: {job.title}\n"
            f"Company: {job.company}\n"
            f"Location: {job.location}\n"
            f"Description:\n{(job.description or '')[:3000]}\n\n"
            f"Evaluate the match and return JSON only."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw_resp = await self.chat_completion(messages, temperature=0.1, max_tokens=1000)
        if not raw_resp:
            return None

        return self._clean_and_parse_json(raw_resp)

    def evaluate_job_sync(
        self,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> Optional[Dict[str, Any]]:
        """Synchronous wrapper for evaluate_job."""
        try:
            return asyncio.run(self.evaluate_job(job, profile))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.evaluate_job(job, profile))
            finally:
                loop.close()
        except Exception as e:
            logger.warning("Synchronous job evaluation failed (%s): %s", type(e).__name__, e)
            return None

    async def tailor_summary(
        self,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> Optional[str]:
        """Generate a role-focused summary using ONLY verified facts from profile."""
        verified_skills_str = ", ".join(profile.verified_skills)
        verified_experiences_str = "\n".join([
            f"- {exp.title} at {exp.company}: {'; '.join(exp.bullets)}"
            for exp in profile.verified_experiences
        ])

        system_prompt = (
            "You are a professional resume writer for top Software Engineers.\n"
            "CRITICAL RULE: DO NOT INVENT OR HALLUCINATE ANY METRICS, DATES, COMPANIES, OR TECHNOLOGIES.\n"
            "Use ONLY the verified facts provided. Do not use numbers that do not appear in the candidate profile.\n"
            "Write a punchy, high-impact 2-3 sentence professional summary tailored to the target role.\n"
            "Output ONLY the summary text, nothing else."
        )

        user_content = (
            f"TARGET JOB: {job.title} at {job.company}\n"
            f"JOB CONTEXT: {(job.description or '')[:1000]}\n\n"
            f"CANDIDATE FACTS:\n"
            f"Name: {profile.full_name}\n"
            f"Experience: {profile.years_of_experience}+ years\n"
            f"Skills: {verified_skills_str}\n"
            f"Allowed Metrics: {', '.join(profile.allowed_metrics)}\n"
            f"Experiences:\n{verified_experiences_str}\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        summary = await self.chat_completion(messages, temperature=0.2, max_tokens=250)
        if summary:
            # Strip deepseek thinking tags if present
            summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()
            summary = summary.strip('"')
            return summary
        return None

    def tailor_summary_sync(
        self,
        job: JobPosting,
        profile: CandidateProfile,
    ) -> Optional[str]:
        """Synchronous wrapper for tailor_summary."""
        try:
            return asyncio.run(self.tailor_summary(job, profile))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.tailor_summary(job, profile))
            finally:
                loop.close()
        except Exception:
            return None

    @staticmethod
    def _clean_and_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON object from LLM response (handling thinking tags and markdown fences)."""
        clean = text.strip()

        # 1. Strip DeepSeek <think>...</think> blocks
        clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()

        # 2. Strip markdown code fences if present
        if "```" in clean:
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)

        # 3. Find outermost {...}
        m = re.search(r"(\{.*\})", clean, re.DOTALL)
        if m:
            clean = m.group(1)

        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.debug("Failed to parse LLM JSON: %s (Raw text: %s)", e, text[:150])
        return None
