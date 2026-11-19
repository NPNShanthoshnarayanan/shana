"""Nightly consolidation: one cheap-model call per session turns the day into memories, open items and issues."""
from __future__ import annotations

from datetime import date, datetime, time

from shana import config, logs, memory, models, tracker
from shana.util import now, read_json, short, write_json

PROMPT = """You read one AI work session of the user (excerpt below) and write memory notes in simple English.
Return ONLY JSON:
{{"summary": "1-2 sentences: what was done and the result",
 "facts": ["decisions made or checked results with their numbers (max 5)"],
 "rules": ["how the user wants the AI to work, stated by the user (max 3)"],
 "open": ["things left unfinished: unanswered questions, half-done tasks, decisions still pending (max 4)"],
 "shana_problems": ["problems the user had with the tool named Shana, if any (max 3)"]}}
Use [] when there is nothing. Only important items; no guesses.

Session title: {title}
Project: {project}

USER MESSAGES (oldest first):
{user}

LAST AI REPLIES:
{ai}
"""


def _excerpt(s: logs.Session, start: datetime) -> tuple[str, str]:
    user = [m for t, m in s.user_msgs if t is None or t >= start][-25:]
    ai = [m for t, m in s.assistant_texts if t is None or t >= start][-6:]
    return "\n".join(f"- {short(m, 400)}" for m in user), "\n".join(f"- {short(m, 500)}" for m in ai)


def _opencode_quality(start: datetime) -> int:
    """User corrections and false model labels in OpenCode chats become issues by themselves (no need to flag)."""
    from shana import opencode
    added = 0
    signals = opencode.quality_signals(start)
    for s in [x for x in signals if x["kind"] == "user-correction"][:5]:
        new, _ = tracker.add_or_match(f"Wrong answer flagged by user: {s['user'][:70]}",
                                      f"Session: {s['session']}\n\nShana said:\n> {s['shana_said']}\n\nUser: {s['user']}",
                                      source="auto", kind="bug")
        added += bool(new)
    labels = [x for x in signals if x["kind"] == "false-model-label"]
    if labels:
        new, _ = tracker.add_or_match(f"Replies named the wrong model ({len(labels)} times)",
                                      "\n".join(f"- said [{x['label']}] but ran on {x['real']}" for x in labels[:10]),
                                      source="auto", kind="bug")
        added += bool(new)
    return added


def run_day(day: date) -> dict:
    start = datetime.combine(day, time.min).astimezone()
    done_path = config.path("data", "nightly-done.json")
    done = read_json(done_path, {})
    spec = models.pick("summarize")
    stats = {"sessions": 0, "facts": 0, "rules": 0, "open": 0, "issues": 0}
    for s in logs.claude_sessions(since=start):
        user, ai = _excerpt(s, start)
        key = f"{s.id}:{day}"
        if s.internal or not user or done.get(key) == str(s.last_time):
            continue
        data, _ = models.run_json(spec, PROMPT.format(title=s.label, project=s.project or "-", user=user, ai=ai),
                                  label="nightly", timeout=300)
        if not isinstance(data, dict):
            continue
        stats["sessions"] += 1
        if data.get("summary"):
            memory.add("episode", f"{s.label}: {data['summary']}", source=s.project, auto=True)
        for f in data.get("facts", [])[:5]:
            stats["facts"] += bool(memory.add("fact", f, project=s.project or "general", source=s.label, auto=True))
        for r in data.get("rules", [])[:3]:
            stats["rules"] += bool(memory.add("howto", r, project="rules", source=s.label, auto=True))
        for o in data.get("open", [])[:4]:
            stats["open"] += bool(memory.add_open(o, source=s.label, auto=True))
        mentions_shana = "shana" in (user + ai).lower()  # otherwise the model mislabels other tools' problems
        for p in data.get("shana_problems", [])[:3] if mentions_shana else []:
            stats["issues"] += bool(tracker.add(p, f"Seen in session: {s.label}", source="auto"))
        done[key] = str(s.last_time)
    stats["issues"] += tracker.from_errors(day) + tracker.from_quality(day) + _opencode_quality(start)
    try:
        from shana import scan
        stats["issues"] += scan.run(hours=26)["new_issues"]
    except Exception as e:  # noqa: BLE001
        from shana.util import log_error
        log_error("nightly.scan", e)
    planned = tracker.auto_plan()
    if planned:
        from shana import notify
        names = ", ".join(f"#{i['id']} {i['title'][:40]}" for i in planned)
        notify.send(f"🐞 {len(planned)} Shana issue(s) planned", f"Approve a fix: {names}")
    stats["planned"] = len(planned)
    write_json(done_path, {k: v for k, v in done.items() if k.endswith(str(day)) or len(done) < 500})
    stats["at"] = now().isoformat()
    return stats
