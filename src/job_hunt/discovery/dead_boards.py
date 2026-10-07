"""Dead-board miss-counter memory.

Triage tooling, not runtime: triage scripts (e.g. ``scripts/resolve_boards.py``)
write probe outcomes here, and runtime discovery may read it to skip boards
that have 404'd repeatedly.

Only hard 404s count. Throttles (429), server errors (5xx), and network
failures record nothing and reset nothing, so a struggling-but-alive board is
never skipped.

File format: JSON ``{key: {"misses": int, "last_seen": epoch_seconds}}`` where
``key`` is e.g. ``"greenhouse:acme"``. A board is skipped once it has
``>= 3`` misses with the most recent 404 inside the 30-day recheck window.
"""

import json
import time
from pathlib import Path

THRESHOLD = 3
RECHECK_SECONDS = 30 * 24 * 3600


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path, data):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def record_result(path, key, status):
    """Record one probe outcome. Only 404 increments the miss counter."""
    if status != 404:
        return
    data = _load(path)
    entry = data.get(key)
    misses = entry.get("misses", 0) if isinstance(entry, dict) else 0
    data[key] = {"misses": misses + 1, "last_seen": time.time()}
    _save(path, data)


def should_skip(path, key, now=None):
    """True when the board has >=3 misses and the last 404 is < 30 days old."""
    if now is None:
        now = time.time()
    entry = _load(path).get(key)
    if not isinstance(entry, dict):
        return False
    if entry.get("misses", 0) < THRESHOLD:
        return False
    last_seen = entry.get("last_seen", 0)
    return (now - last_seen) < RECHECK_SECONDS
