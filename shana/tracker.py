"""Part B — problems with Shana itself. Reported by you or found automatically; fixed only after you approve.

Each issue is one .md file in issues/ with a small header (id, title, kind, status, source, created).
Status flow: new → planned → approved → fixed (or dropped).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path

from shana import config, models
from shana.util import now, parse_ts, read_jsonl, run, short

_HEAD = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _dir() -> Path:
    d = config.path("issues")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse(p: Path) -> dict:
    text = p.read_text()
    m = _HEAD.match(text)
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line) if m else {}
    meta.update({"path": str(p), "body": text[m.end():] if m else text})
    return meta


def list_issues(status: str | None = None) -> list[dict]:
    items = [_parse(p) for p in sorted(_dir().glob("*.md"))]
    return [i for i in items if status in (None, i.get("status"))]


def get(n: int) -> dict | None:
    return next((i for i in list_issues() if i.get("id") == str(n)), None)


def _write(meta: dict, body: str) -> Path:
    keys = ("id", "title", "kind", "status", "source", "created")
    head = "\n".join(f"{k}: {meta[k]}" for k in keys if k in meta)
    p = Path(meta["path"]) if meta.get("path") else _dir() / f"{int(meta['id']):03d}-{re.sub(r'[^a-z0-9]+', '-', meta['title'].lower())[:40].strip('-')}.md"
    p.write_text(f"---\n{head}\n---\n{body}")
    return p


def add(title: str, details: str = "", source: str = "user", kind: str = "?") -> dict | None:
    title = short(title, 100)
    key = re.sub(r"\W+", " ", title.lower()).strip()
    if any(re.sub(r"\W+", " ", i.get("title", "").lower()).strip() == key for i in list_issues()):
        return None
    n = max((int(i["id"]) for i in list_issues() if i.get("id", "").isdigit()), default=0) + 1
    meta = {"id": str(n), "title": title, "kind": kind, "status": "new", "source": source,
            "created": f"{now():%Y-%m-%d}"}
    meta["path"] = str(_write(meta, f"\n## Problem\n{details or title}\n"))
    return meta


def set_status(n: int, status: str, append: str = "") -> dict | None:
    i = get(n)
    if not i:
        return None
    i["status"] = status
    _write(i, i["body"] + (f"\n{append}\n" if append else ""))
    return get(n)


def from_errors(day: date) -> int:
    """Turn Shana's own errors from that day into issues (one per place + error type)."""
    start = datetime.combine(day, time.min).astimezone()
    added = 0
    for e in read_jsonl(config.path("data", "errors.jsonl")):
        if parse_ts(e["ts"]) >= start:
            kind = e["error"].split("(", 1)[0]
            added += bool(add(f"Error in {e['where']}: {kind}", f"{e['error']}\n\n```\n{e['trace']}\n```",
                              source="auto", kind="bug"))
    return added


PLAN_PROMPT = """You maintain a small Python tool called Shana (code in the current folder: shana/*.py, tests in tests/).
Write a short fix plan in simple English for this issue. Return ONLY JSON:
{{"kind": "bug" or "feature", "plan": ["step 1", "step 2", "..."], "files": ["files to change"], "test": "how to test"}}

Issue #{id}: {title}
{body}
"""

FIX_PROMPT = """Implement this approved plan for issue #{id} in the Shana tool (current folder).
Keep changes small. Add or update a test in tests/. Then run: python3 -m unittest discover -s tests -q
Finish with 3 short lines: what changed, files, test result.

Issue: {title}
{body}
"""


def plan(n: int) -> dict | None:
    i = get(n)
    if not i:
        return None
    data, res = models.run_json(models.pick("mid"), PLAN_PROMPT.format(**i), cwd=config.home(), label=f"issue-{n}-plan")
    if not isinstance(data, dict):
        return set_status(n, i["status"], f"## Plan\n(planning failed: {short(res.error or res.text, 200)})")
    i["kind"] = data.get("kind", i.get("kind", "?"))
    text = "\n".join(f"{k + 1}. {s}" for k, s in enumerate(data.get("plan", [])))
    _write(i, i["body"] + f"\n## Plan\n{text}\n\nFiles: {', '.join(data.get('files', []))}\nTest: {data.get('test', '')}\n")
    return set_status(n, "planned")


def fix(n: int) -> dict | None:
    """Only for approved issues: an agent makes the change, then the test suite decides."""
    i = get(n)
    if not i or i.get("status") != "approved":
        return i
    res = models.run(models.pick("mid"), FIX_PROMPT.format(**i), cwd=config.home(), agent=True, timeout=1800,
                     label=f"issue-{n}-fix")
    tests = run(["python3", "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=config.home(), timeout=600)
    ok = res.ok and tests.returncode == 0
    note = (f"## Result ({now():%Y-%m-%d %H:%M})\n{short(res.text or res.error, 600)}\n\n"
            f"Tests: {'passed' if tests.returncode == 0 else 'FAILED'}\n```\n{(tests.stderr or tests.stdout)[-800:]}\n```")
    return set_status(n, "fixed" if ok else "approved", note)


def from_quality(day: date, min_steps: int = 3, max_fail_rate: float = 0.3) -> int:
    """A model that fails too many checks in a day becomes an issue, so model choices get revisited."""
    start = datetime.combine(day, time.min).astimezone()
    stats: dict[str, list[int]] = {}
    for e in read_jsonl(config.path("data", "quality.jsonl")):
        if parse_ts(e["ts"]) >= start:
            s = stats.setdefault(e["spec"], [0, 0])
            s[0] += 1
            s[1] += not e["ok"]
    added = 0
    for spec, (n, bad) in stats.items():
        if n >= min_steps and bad / n >= max_fail_rate:
            added += bool(add(f"Model {spec} failed {bad} of {n} steps on {day}",
                              "Too many failed checks; consider a different model for this tier in config.toml.",
                              source="auto", kind="bug"))
    return added


def auto_plan(limit: int = 3) -> list[dict]:
    """Plan new issues by themselves; fixing still waits for your approval."""
    return [p for i in list_issues("new")[:limit] if (p := plan(int(i["id"])))]
