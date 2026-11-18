"""Part D (results) — check that numbers in an AI answer really appear in the source files or logs."""
from __future__ import annotations

import re
from pathlib import Path

_NUM = re.compile(r"(?<![\w.])[$₹€]?\d[\d,]*(?:\.\d+)?%?")


def norm(token: str) -> str:
    t = token.strip("$₹€%").replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def numbers(text: str) -> set[str]:
    return {norm(m) for m in _NUM.findall(text)}


def _source_text(src: str) -> str:
    p = Path(src).expanduser()
    if len(src) < 500 and p.is_file():
        return p.read_text(errors="ignore")
    if len(src) < 500 and p.is_dir():
        return "\n".join(f.read_text(errors="ignore") for f in p.rglob("*") if f.is_file() and f.stat().st_size < 5e6)
    return src


def check(answer: str, sources: list[str], ignore_small: int = 10) -> dict:
    """Return each sentence whose numbers cannot be found in any source. Small numbers (≤ ignore_small) are skipped."""
    known: set[str] = set()
    for s in sources:
        known |= numbers(_source_text(s))
    unproven, checked = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        nums = {n for n in numbers(sentence) if not (n.isdigit() and int(n) <= ignore_small)}
        if not nums:
            continue
        checked += 1
        missing = sorted(nums - known)
        if missing:
            unproven.append({"sentence": sentence.strip()[:300], "missing": missing})
    return {"sentences_checked": checked, "unproven": unproven, "ok": not unproven}
