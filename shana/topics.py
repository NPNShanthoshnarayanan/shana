"""Part E — notice things you left open: a question waiting for your answer, or your question with no reply."""
from __future__ import annotations

from datetime import timedelta

from shana import config, logs, memory, notify
from shana.util import now, short


def heuristics(sessions: list[logs.Session], after_minutes: int) -> list[dict]:
    items = []
    for s in sessions:
        last = s.last_time or s.mtime
        if s.internal or not last or now() - last < timedelta(minutes=after_minutes):
            continue
        if s.pending_ask and s.last_kind in ("text", "tool_use"):
            items.append({"key": f"ask:{s.id}:{last.isoformat()}", "kind": "decision waiting on you",
                          "text": s.pending_ask, "session": s.label})
        elif s.last_kind == "prompt" and s.user_msgs:
            items.append({"key": f"noreply:{s.id}:{last.isoformat()}", "kind": "your question got no reply",
                          "text": s.user_msgs[-1][1], "session": s.label})
    return items


def quick_ping(sessions: list[logs.Session], state: dict) -> list[dict]:
    """Called by the monitor poll; pings once per open item and saves it to open-items.md."""
    from shana.monitor import _once, save_state  # local import: monitor imports this module lazily too

    sent = []
    for item in heuristics(sessions, config.load()["topics"]["ping_after_minutes"]):
        if _once(state, "topic:" + item["key"]):
            notify.send(f"📌 Still open: {short(item['session'], 45)}", f"{item['kind']}: {short(item['text'], 140)}")
            memory.add_open(f"{item['kind']}: {short(item['text'], 200)}", source=item["session"], auto=True)
            sent.append(item)
    if sent:
        save_state(state)
    return sent
