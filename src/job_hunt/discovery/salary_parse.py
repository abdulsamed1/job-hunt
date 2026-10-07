"""Bounded free-text salary parser. Implausible values return None, never a guess."""
from __future__ import annotations
import re

def parse_salary_text(text: str):
    if not text:
        return None
    m = re.search(r"\$\s*([\d.,]+)\s*k?\s*(?:-|–|to)\s*\$?\s*([\d.,]+)\s*k?", text, re.I)
    hourly = re.search(r"\$\s*([\d.,]+)\s*(?:/|per\s+hour|hr\b)", text, re.I)
    def num(s: str, scope: str) -> float:
        v = float(s.replace(",", ""))
        # Scope the k-suffix to the matched salary expression only: a "k"
        # elsewhere in the posting (e.g. "Work from home") must not inflate
        # plain numbers like $120 - $150 per day.
        return v * 1000 if v < 1000 and "k" in scope else v
    if m:
        scope = m.group(0).lower()
        lo, hi = num(m.group(1), scope), num(m.group(2), scope)
        # Hourly range (e.g. $20 - $30/hour): annualize BEFORE the plausibility
        # gate, which is calibrated in yearly terms. Only when the hourly
        # signal belongs to this range (overlapping $N/hour match or an
        # hour-unit right after it) — a stray "/hr" elsewhere in the posting
        # must not inflate an already-yearly range.
        tail = text[m.end():m.end() + 24].lower()
        hourly_adjacent = (
            (hourly is not None and m.start() <= hourly.start() <= m.end())
            or "hour" in tail
            or "/hr" in tail
        )
        if hourly_adjacent:
            lo, hi = lo * 2080, hi * 2080
    elif hourly:
        h = float(hourly.group(1).replace(",", ""))
        lo = hi = h * 2080
    else:
        return None
    if not (1000 <= lo <= hi <= 700000):
        return None
    yearly = "hour" in text.lower() or lo < 350
    lo = lo * 2080 if yearly and lo < 350 else lo
    hi = hi * 2080 if yearly and hi < 350 else hi
    if not (1000 <= lo <= hi <= 700000):
        return None
    return (int(lo), int(hi), "USD", "inferred")
