"""Part B — problems with Shana itself. Reported by you or found automatically; fixed only after you approve.

Each issue is one .md file in issues/ with a small header (id, title, kind, status, source, created).
Status flow: new → planned → approved → fixed (or dropped).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
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


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower())}


def add_or_match(title: str, details: str = "", source: str = "user", kind: str = "?",
                 threshold: float = 0.5) -> tuple[dict | None, dict | None]:
    """Create an issue unless an open one (or one closed in the last 3 days) says much the same."""
    words = _words(title)
    recent = f"{now() - timedelta(days=3):%Y-%m-%d}"
    for i in list_issues():
        if i.get("status") in ("closed", "dropped", "fixed") and i.get("created", "") < recent:
            continue
        other = _words(i.get("title", ""))
        if words and other and len(words & other) / min(len(words), len(other)) >= threshold + 0.1:
            return None, i
    return add(title, details, source, kind), None


def close(n: int, reason: str = "") -> dict | None:
    return set_status(n, "closed", f"## Closed ({now():%Y-%m-%d})\n{reason or 'closed'}")


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
{{"kind": "bug" or "feature", "plan": ["step 1", "step 2", "..."], "files": ["files to change"], "test": "how to test",
 "check": "ONE shell command run in the Shana folder that FAILS now and PASSES once the user's problem is really gone
 (it must test the real symptom, not just that code exists), or \"manual: <what the user should try>\" if no command can"}}

Issue #{id}: {title}
{body}
"""

FIX_PROMPT = """Implement this approved plan for issue #{id} in the Shana tool (current folder).
Keep changes small. Add or update a test in tests/. Then run: python3 -m unittest discover -s tests -q
Finish with 3 short lines: what changed, files, test result.

The fix must be GLOBAL, not just for this session: if it changes how Shana or its agent behaves (labels, routing,
cost tracking, wording), update the shared prompt {prompt_file} and/or the shared config (config.example.toml,
shana/config.py defaults) as well as the code, so every session benefits.

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
    _write(i, i["body"] + f"\n## Plan\n{text}\n\nFiles: {', '.join(data.get('files', []))}\nTest: {data.get('test', '')}\n"
                          f"Check: {data.get('check') or 'manual: try it yourself'}\n")
    return set_status(n, "planned")


def fix_prompt(i: dict) -> str:
    return FIX_PROMPT.format(prompt_file=config.path("agents", "shana.md"), **i)


def _check_cmd(i: dict) -> str:
    m = re.search(r"^Check: (.+)$", i.get("body", ""), re.M)
    return m.group(1).strip() if m else "manual: try it yourself"


def fix(n: int) -> dict | None:
    """Approved issues only. Fixed = tests pass AND the issue's own check proves the symptom is gone; else not fixed."""
    from shana import notify
    i = get(n)
    if not i or i.get("status") != "approved":
        return i
    if "## Plan" not in i.get("body", "") and not re.search(r"^Check: ", i.get("body", ""), re.M):
        # approved straight from chat ("fix it"): plan first so the fix has a check, then fix
        plan(n)
        i = set_status(n, "approved")
    check = _check_cmd(i)
    shell = (lambda: run(["/bin/sh", "-c", check], cwd=config.home(), timeout=600)) if not check.startswith("manual") else None
    before = shell() if shell else None
    spec = models.pick("mid")
    res = models.run(spec, fix_prompt(i), cwd=config.home(), agent=True, timeout=1800, label=f"issue-{n}-fix")
    tests = run(["python3", "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=config.home(), timeout=600)
    after = shell() if shell else None
    tests_ok = res.ok and tests.returncode == 0
    if not tests_ok:
        status, why = "approved", "tests failed or the fixing agent failed"
    elif shell is None:
        status, why = "needs-check", f"please check it yourself: {check[7:].strip()}"
    elif after.returncode != 0:
        status, why = "approved", "its check still fails, so the problem is not gone"
    elif before is not None and before.returncode == 0:
        status, why = "needs-check", "the check also passed before the fix, so it does not prove anything; please try it"
    else:
        status, why = "fixed", "tests pass and its check now passes (it failed before)"
    tail = (tests.stderr or tests.stdout)[-600:] + "\n" + ((after.stdout + after.stderr) if after else "")[-400:]
    note = (f"## Result ({now():%Y-%m-%d %H:%M}) → {status}: {why}\n{short(res.text or res.error, 600)}\n\n"
            f"Tests: {'passed' if tests.returncode == 0 else 'FAILED'}\nCheck: {check}\n```\n{tail}\n```")
    out = set_status(n, status, note)
    if shell is not None or not tests_ok:
        models.record_quality(spec, status == "fixed", True, f"issue #{n} fix")
    if status == "fixed":
        run(["git", "add", "-A"], cwd=config.home())
        run(["git", "commit", "-qm", f"🐛 fix(#{n}): {short(i['title'], 60)}"], cwd=config.home())
    icon = {"fixed": "✅", "needs-check": "🟡"}.get(status, "❌")
    notify.send(f"{icon} Shana issue #{n}: {status}", short(f"{i['title']} — {why}", 200))
    return out


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
