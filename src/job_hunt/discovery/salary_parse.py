"""Bounded free-text salary parser. Implausible values return None, never a guess."""
from __future__ import annotations
import re

def parse_salary_text(text: str):
    if not text:
        return None
    m = re.search(r"\$\s*([\d.,]+)\s*k?\s*(?:-|–|to)\s*\$?\s*([\d.,]+)\s*k?", text, re.I)
    hourly = re.search(r"\$\s*([\d.,]+)\s*(?:/|per\s+hour|hr\b)", text, re.I)
    def num(s: str) -> float:
        v = float(s.replace(",", ""))
        return v * 1000 if v < 1000 and "k" in text.lower() else v
    if m:
        lo, hi = num(m.group(1)), num(m.group(2))
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
