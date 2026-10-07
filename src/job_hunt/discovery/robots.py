"""Fail-closed robots triage. Unreadable robots.txt is not permission."""
from __future__ import annotations

from urllib.parse import urlparse


def group_allows(robots_txt: str, path: str, user_agent: str = "*") -> bool | None:
    if not (robots_txt or "").strip():
        return None
    rules: list[tuple[str, str]] = []
    current_agents: list[str] = []
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            current_agents = [value]
        elif field in ("allow", "disallow") and any(a in ("*", user_agent.lower()) for a in current_agents):
            rules.append((field, value))
    if not rules:
        return None
    best: tuple[str, str] | None = None
    for field, value in rules:
        if value and path.startswith(value) and (best is None or len(value) > len(best[1])):
            best = (field, value)
    if best is None:
        return True
    return best[0] == "allow"


async def robots_allowed(url: str, client, user_agent: str = "*") -> bool | None:
    """Fetch robots.txt for the entry URL host and evaluate the entry path.

    Returns True (allowed), False (disallowed), or None (unreadable —
    not permission, caller proceeds once without retrying as allowed).
    """
    parsed = urlparse(url or "")
    if not parsed.netloc:
        return None
    robots_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=10.0)
        if resp.status_code != 200:
            return None
        return group_allows(resp.text, parsed.path or "/", user_agent)
    except Exception:
        return None
