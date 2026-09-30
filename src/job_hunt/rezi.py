"""Rezi Pro integration via the Rezi MCP server (streamable HTTP, no SDK needed).

MCP streamable HTTP is plain HTTPS POSTs carrying JSON-RPC, so this client uses
httpx only. Authentication is a user OAuth access token (30-day lifetime, no
refresh tokens) supplied via the REZI_MCP_TOKEN environment variable or a 0600
token file. Tokens are password-equivalent: never logged, never committed.

Docs: https://www.rezi.ai/rezi-docs/resume-mcp-server
Server: https://github.com/rezi-io/rezi-mcp
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REZI_MCP_SERVER_URL = "https://api.rezi.ai/mcp"
REZI_TOKEN_ENV_VAR = "REZI_MCP_TOKEN"
REZI_TOKEN_FILE = Path(".rezi_token")
MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "job-hunt"


class ReziError(Exception):
    """Transport or API error talking to the Rezi MCP server."""


class ReziNotConfigured(ReziError):
    """No Rezi access token available; Rezi integration is disabled."""


class ReziAuthExpired(ReziError):
    """Server rejected the token (HTTP 401): re-authentication required."""


def resolve_token(explicit: Optional[str] = None) -> str:
    """Resolve the Rezi access token without ever logging its value."""
    if explicit:
        return explicit
    from_env = os.environ.get(REZI_TOKEN_ENV_VAR)
    if from_env:
        return from_env
    if REZI_TOKEN_FILE.exists():
        try:
            value = REZI_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError as exc:
            logger.warning("Could not read Rezi token file: %s", exc)
    raise ReziNotConfigured(
        f"No Rezi access token: set {REZI_TOKEN_ENV_VAR} or run scripts/rezi_login.py"
    )


def get_mcp_settings_snippet() -> Dict[str, Any]:
    """MCP client configuration block (contains no secrets)."""
    return {
        "mcpServers": {
            "rezi": {
                "url": REZI_MCP_SERVER_URL,
                "transport": "streamable-http",
            }
        }
    }


def _collection(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Rezi collection shape: UUID keys plus index ordering and hide visibility."""
    out: Dict[str, Dict[str, Any]] = {}
    for i, item in enumerate(items):
        entry = {"index": i, "hide": False}
        entry.update(item)
        out[str(uuid.uuid4())] = entry
    return out


def _ranked_bullets(bullets: List[str], job_keywords: set) -> List[str]:
    """Order bullets by posting-keyword overlap (most relevant first)."""
    scored = []
    for bullet in bullets:
        words = set(re.findall(r"\b\w+\b", bullet.lower()))
        scored.append((len(words.intersection(job_keywords)), bullet))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored]


def build_rezi_resume_data(
    job: Any, profile: Any, template: str = "modern"
) -> Dict[str, Any]:
    """Build a Rezi write_resume payload from verified profile facts only.

    Every skill, employer, project, and institution comes straight from the
    profile object — nothing is invented, so the payload is safe to send.
    Always creates; never pass an existing (master) resume_id with this output.
    """
    job_keywords = set(re.findall(r"\b\w+\b", f"{job.title} {job.description or ''}".lower()))

    experience = []
    for exp in profile.verified_experiences or []:
        experience.append(
            {
                "title": exp.title,
                "company": exp.company,
                "startDate": exp.start_date,
                "endDate": exp.end_date or "Present",
                "location": exp.location or "",
                "bullets": _ranked_bullets(list(exp.bullets or []), job_keywords),
                "technologies": list(exp.technologies or []),
            }
        )

    education = []
    for edu in profile.verified_education or []:
        education.append(
            {
                "institution": edu.institution,
                "degree": edu.degree,
                "fieldOfStudy": edu.field_of_study or "",
                "graduationYear": edu.graduation_year,
            }
        )

    projects = []
    for proj in profile.verified_projects or []:
        projects.append(
            {
                "name": proj.name,
                "description": proj.description,
                "bullets": _ranked_bullets(list(proj.bullets or []), job_keywords),
                "technologies": list(proj.technologies or []),
                "url": proj.url or "",
            }
        )

    skills = [{"name": s} for s in (profile.verified_skills or [])]

    summary = profile.summary or (
        f"Software Engineer with {profile.years_of_experience}+ years of experience "
        "building reliable backend systems."
    )

    contact: Dict[str, Any] = {
        "name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
    }
    if getattr(profile, "linkedin_url", None):
        contact["linkedin"] = profile.linkedin_url
    if getattr(profile, "github_url", None):
        contact["github"] = profile.github_url
    if getattr(profile, "portfolio_url", None):
        contact["website"] = profile.portfolio_url

    return {
        "name": f"{job.company} - {job.title}"[:80],
        "jobTitle": job.title,
        "jobDescription": (job.description or "")[:4000],
        "jobCompany": job.company,
        "template": template,
        "data": {
            "contact": contact,
            "summary": summary,
            "experience": _collection(experience),
            "education": _collection(education),
            "skills": _collection(skills),
            "projects": _collection(projects),
        },
    }


def _parse_rpc_response(resp: httpx.Response) -> Any:
    """Extract the JSON-RPC result from a JSON or SSE stream response."""
    content_type = resp.headers.get("content-type", "")
    if "event-stream" in content_type:
        payload: Optional[Dict[str, Any]] = None
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
        if payload is None:
            raise ReziError("Empty SSE stream from Rezi MCP server")
    else:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ReziError(f"Non-JSON response from Rezi MCP server: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReziError("Unexpected Rezi MCP response shape")
    if "error" in payload:
        err = payload["error"]
        raise ReziError(f"Rezi MCP error {err.get('code')}: {err.get('message')}")
    if "result" not in payload:
        raise ReziError("Rezi MCP response missing result")
    return payload["result"]


class ReziMCPClient:
    """Minimal async client for the Rezi MCP server (list/read/format/write)."""

    def __init__(
        self,
        token: Optional[str] = None,
        server_url: str = REZI_MCP_SERVER_URL,
        timeout: float = 20.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.token = resolve_token(token)
        self.server_url = server_url
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._session_id: Optional[str] = None
        self._initialized = False
        self._request_id = 0

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        if not self._initialized and method != "initialize":
            await self._initialize()
        self._request_id += 1
        body = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            body["params"] = params
        try:
            resp = await self._client.post(self.server_url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ReziError(f"Rezi MCP transport error: {exc}") from exc
        if resp.status_code == 401:
            self._initialized = False
            raise ReziAuthExpired("Rezi token rejected (HTTP 401); re-authentication required")
        if resp.status_code >= 400:
            raise ReziError(f"Rezi MCP HTTP {resp.status_code}")
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return _parse_rpc_response(resp)

    async def _initialize(self) -> None:
        await self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "1.0"},
            },
        )
        # JSON-RPC notification (no id, no response expected)
        if self._client is None:  # pragma: no cover - defensive
            raise ReziError("HTTP client missing during handshake")
        self._request_id += 1
        try:
            await self._client.post(
                self.server_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ReziError(f"Rezi MCP handshake failed: {exc}") from exc
        self._initialized = True

    async def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def list_resumes(self) -> List[Dict[str, Any]]:
        result = await self._call_tool("list_resumes", {})
        if isinstance(result, dict) and "resumes" in result:
            return result["resumes"]
        if isinstance(result, list):
            return result
        raise ReziError("Unexpected list_resumes result shape")

    async def read_resume(self, resume_id: str) -> Dict[str, Any]:
        result = await self._call_tool("read_resume", {"resume_id": resume_id})
        if not isinstance(result, dict):
            raise ReziError("Unexpected read_resume result shape")
        return result

    async def get_format(self) -> Dict[str, Any]:
        result = await self._call_tool("get_resume_format", {})
        if not isinstance(result, dict):
            raise ReziError("Unexpected get_resume_format result shape")
        return result

    async def write_resume(
        self, resume: Dict[str, Any], resume_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create (resume_id omitted) or update a resume. Never pass the master ID."""
        arguments = dict(resume)
        if resume_id is not None:
            arguments["resume_id"] = resume_id
        result = await self._call_tool("write_resume", arguments)
        if not isinstance(result, dict):
            raise ReziError("Unexpected write_resume result shape")
        return result

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
