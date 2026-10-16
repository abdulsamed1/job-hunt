"""Safe local Telegram event notifier (standard library only, best-effort).

Sends bounded, source-tagged lifecycle events to Telegram via the Bot HTTP
API. Sending is always best-effort: missing credentials, network failures,
and API errors are logged (without token values) and never raise. Events are
deduplicated process-locally so retries do not resend the same event.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections import deque
from typing import Callable, Deque, Optional, Set, Tuple

logger = logging.getLogger(__name__)

TELEGRAM_API_TIMEOUT_SEC = 10.0
MAX_MESSAGE_LEN = 3000
MAX_SEEN_EVENTS = 2000


def format_event(
    runtime: str,
    event: str,
    title: str,
    company: str,
    detail: str,
    url: str,
) -> str:
    """Format a compact source-tagged Telegram message, bounded to 3000 chars."""
    lines = [f"[{runtime}] {event}: {title} @ {company}"]
    if detail:
        lines.append(detail)
    if url:
        lines.append(url)
    return "\n".join(lines)[:MAX_MESSAGE_LEN]


def _post_message(bot_token: str, chat_id: str, text: str) -> bool:
    """POST one message via the Telegram Bot API. Returns True on HTTP 200."""
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TELEGRAM_API_TIMEOUT_SEC) as response:
        return response.status == 200


class TelegramNotifier:
    """Best-effort Telegram event sender with process-local dedup.

    Returns booleans instead of raising: ``emit`` is True only when a
    message was actually sent. No background threads, no new dependencies.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        runtime: str = "local",
        sender: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._bot_token = bot_token or ""
        self._chat_id = chat_id or ""
        self._runtime = runtime
        self._sender: Callable[[str], bool] = sender or (
            lambda text: _post_message(self._bot_token, self._chat_id, text)
        )
        self._seen: Set[Tuple[str, str, str, str, str]] = set()
        self._order: Deque[Tuple[str, str, str, str, str]] = deque()

    def emit(
        self,
        event: str,
        title: str,
        company: str,
        detail: str = "",
        url: str = "",
    ) -> bool:
        """Send one event unless already sent. Never raises; False on no-op."""
        if not self._bot_token or not self._chat_id:
            logger.debug("Telegram event %s skipped: credentials not configured", event)
            return False
        key = (self._runtime, event, title or "", company or "", url or "")
        if key in self._seen:
            return False
        text = format_event(self._runtime, event, title, company, detail, url)
        try:
            sent = bool(self._sender(text))
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            logger.warning("Telegram event %s failed: %s", event, type(exc).__name__)
            return False
        if sent:
            self._remember(key)
        else:
            logger.warning("Telegram event %s not acknowledged", event)
        return sent

    def _remember(self, key: Tuple[str, str, str, str, str]) -> None:
        if len(self._seen) >= MAX_SEEN_EVENTS:
            oldest = self._order.popleft()
            self._seen.discard(oldest)
        self._seen.add(key)
        self._order.append(key)

    def close(self) -> None:
        """Release dedup state. No background resources to shut down."""
        self._seen.clear()
        self._order.clear()
