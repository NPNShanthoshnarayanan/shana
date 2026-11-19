"""Scheduled tracker scan: read the new parts of Shana's conversations and note Shana's own problems as issues.

Runs every few hours (launchd `com.shana.track`). Only messages added since the last scan are read, so each run is
small. A cheap model reads them and lists problems with Shana itself: wrong or unchecked answers, claims without
proof, requests it did not do, things the user had to repeat, UX the user disliked. Repeats of open issues are merged.
"""
from __future__ import annotations

from datetime import timedelta

from shana import config, models, notify, opencode, tracker
from shana.util import log_error, now, read_json, short, write_json

PROMPT = """You review a chat between a user and their AI assistant "Shana". Find problems with SHANA ITSELF that hurt
the user's experience. Count only real, visible problems:
- a wrong answer, or one Shana had to correct after the user pushed back
- a claim made without checking (assumed facts, numbers, "done"/"fixed"/"switched" with no proof)
- something the user asked for that Shana did not do, or did differently
- the user had to repeat or correct themselves, or showed frustration
- an answer that was confusing, too long, or not in simple English
- a missing Shana feature the user asked for
Ignore bugs in other projects or tools the user works on (they are not Shana's problems).
Be strict: only problems the user would clearly agree with. Merge problems with the same cause into ONE issue.
Return ONLY JSON: {{"issues": [{{"title": "short problem statement", "evidence": "quote the user and/or Shana",
"kind": "bug" or "feature"}}]}} with at most 3 issues, or {{"issues": []}} if there are none.

CHAT (newest part; [U] = user, [S] = Shana):
{chat}
"""


def _state_path():
    return config.path("data", "scan-state.json")


def _new_messages(session_id: str, after_seq: int) -> list[tuple[int, str, dict]]:
    import json
    import sqlite3
    con = sqlite3.connect(f"file:{opencode.db_path()}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("select seq, type, data from session_message where session_id=? and seq>? "
                           "and type in ('user','assistant') order by seq", (session_id, after_seq)).fetchall()
    finally:
        con.close()
    return [(q, t, json.loads(d or "{}")) for q, t, d in rows]


def _render(msgs: list[tuple[int, str, dict]], limit: int = 14000) -> str:
    lines = []
    for _, t, d in msgs:
        if t == "user":
            text = d.get("text", "")
        else:
            text = " ".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text")
        if text.strip():
            lines.append(f"[{'U' if t == 'user' else 'S'}] {short(text, 700)}")
    return "\n".join(lines)[-limit:]


def run(hours: int = 48, runner=models.run_json) -> dict:
    """Scan Shana's (main) OpenCode sessions touched in the last `hours`; returns counts of what was noted."""
    state = read_json(_state_path(), {})
    spec = models.pick("summarize")
    stats = {"sessions": 0, "new_issues": 0, "merged": 0, "titles": []}
    cfg = config.load().get("tracker", {})
    skip = tuple(cfg.get("skip_dirs", ["/private/tmp", "/tmp", "/private/var/folders", "/var/folders"]))
    cap = int(cfg.get("max_new_per_run", 5))
    for s in opencode.sessions(now() - timedelta(hours=hours)):
        if s.parent_id or s.directory.startswith(skip):
            continue  # subagents, and test/scratch chats, are not the user's conversations
        msgs = _new_messages(s.id, int(state.get(s.id, 0)))
        if not any(t == "user" for _, t, _ in msgs):
            continue
        chat = _render(msgs)
        if not chat.strip():
            state[s.id] = msgs[-1][0]
            continue
        try:
            data, res = runner(spec, PROMPT.format(chat=chat), label="track-scan", timeout=300)
        except Exception as e:  # noqa: BLE001
            log_error("scan.model", e)
            continue
        if not isinstance(data, dict):
            continue  # keep the position so the next run tries these messages again
        stats["sessions"] += 1
        for it in (data.get("issues") or [])[:3]:
            if stats["new_issues"] >= cap:
                break
            title = short(str(it.get("title", "")), 100)
            if not title:
                continue
            body = f"Found by the scheduled scan in session \"{s.title}\".\n\nEvidence:\n> {short(str(it.get('evidence', '')), 600)}"
            new, dup = tracker.add_or_match(title, body, source="scan", kind=it.get("kind", "bug"))
            if new:
                stats["new_issues"] += 1
                stats["titles"].append(f"#{new['id']} {title}")
            elif dup:
                stats["merged"] += 1
        state[s.id] = msgs[-1][0]
    write_json(_state_path(), state)
    if stats["new_issues"]:
        notify.send(f"🐞 Tracker noted {stats['new_issues']} new Shana issue(s)", short("; ".join(stats["titles"]), 220))
    stats["at"] = now().isoformat()
    return stats
