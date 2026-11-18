"""Part A — watch every session and ping only when something needs you.

Claude Code tells us directly through hooks (Stop, Notification). A poll every few minutes covers what hooks
cannot: stuck sessions, two sessions on the same work, other AI tools, and Claude sessions if hooks are not firing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from shana import config, logs, notify
from shana.util import log_error, now, parse_ts, read_json, short, write_json

_STOP_WORDS = set("""the and for that this with from have what when where which will would could should into your
about there their them then than they been were also just like need want make made only some more most very much
does done dont cant wont didn shana claude session sessions please check tell file files code work using used""".split())


def _state_path() -> Path:
    return config.path("data", "state.json")


def load_state() -> dict:
    return read_json(_state_path(), {"sessions": {}, "notified": {}, "hooks_seen": None})


def save_state(state: dict) -> None:
    cutoff = (now() - timedelta(days=3)).isoformat()
    state["notified"] = {k: v for k, v in state.get("notified", {}).items() if v >= cutoff}
    write_json(_state_path(), state)


def _once(state: dict, key: str) -> bool:
    """True the first time a key is seen; used to never send the same ping twice."""
    if key in state["notified"]:
        return False
    state["notified"][key] = now().isoformat()
    return True


def status_of(s: logs.Session, stuck_minutes: int) -> str:
    if s.tool != "claude":
        last = s.last_time or s.mtime
        return "active" if last and now() - last < timedelta(minutes=3) else "idle"
    if s.last_kind in ("text", "interrupted") or not s.last_kind:
        return "waiting" if s.pending_ask else "finished"
    last = s.last_time or s.mtime
    if last and now() - last >= timedelta(minutes=stuck_minutes):
        return "stuck"
    return "running"


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9_\-]{3,}", text.lower()) if w not in _STOP_WORDS}


def same_work_pairs(sessions: list[logs.Session], threshold: float) -> list[tuple[logs.Session, logs.Session, float]]:
    recent = []
    cutoff = now() - timedelta(minutes=30)
    for s in sessions:
        msgs = [m for t, m in s.user_msgs[-3:] if t is None or t >= cutoff]
        if msgs:
            recent.append((s, keywords(" ".join(msgs) + " " + s.title)))
    pairs = []
    for i, (a, ka) in enumerate(recent):
        for b, kb in recent[i + 1:]:
            if a.title and a.title == b.title:
                continue  # forks of one conversation are not "two sessions on the same work"
            if len(ka) >= 4 and len(kb) >= 4:
                score = len(ka & kb) / len(ka | kb)
                if score >= threshold:
                    pairs.append((a, b, round(score, 2)))
    return pairs


def _record(state: dict, s: logs.Session, status: str) -> None:
    state["sessions"][s.id] = {"tool": s.tool, "title": s.label, "status": status, "project": s.project,
                               "last": (s.last_time or s.mtime or now()).isoformat(), "path": str(s.path)}


def _ping_finished(state: dict, s: logs.Session, cfg: dict) -> None:
    status = status_of(s, cfg["stuck_minutes"])
    _record(state, s, status)
    if status not in ("waiting", "finished") or not s.last_time:
        return
    took = (s.last_time - s.last_prompt_time).total_seconds() if s.last_prompt_time else 0
    key = f"{status}:{s.id}:{s.last_time.isoformat()}"
    if status == "waiting" and _once(state, key):
        notify.send(f"🟡 {short(s.label, 50)}: waiting for you", short(s.pending_ask, 160), priority="high")
    elif status == "finished" and took >= cfg["finished_min_seconds"] and _once(state, key):
        first = s.last_text.strip().splitlines()[0] if s.last_text.strip() else "done"
        notify.send(f"✅ {short(s.label, 50)}: finished", short(first, 160))


def hook(event: str, payload: dict) -> str:
    """Handle a Claude Code hook. Returns text for stdout (only SessionStart uses it)."""
    if os.environ.get("SHANA_INTERNAL"):
        return ""
    cfg = config.load()["monitor"]
    state = load_state()
    state["hooks_seen"] = now().isoformat()
    out = ""
    transcript = Path(payload.get("transcript_path") or "")
    s = logs.read_claude(transcript, tail_bytes=400_000) if transcript.is_file() else None
    if s and not s.internal:
        if event == "stop":
            _ping_finished(state, s, cfg)
        elif event == "notification":
            msg = payload.get("message") or "needs you"
            recent_stop = any(k.startswith(("waiting:" + s.id, "finished:" + s.id)) and
                              now() - parse_ts(v) < timedelta(minutes=5) for k, v in state["notified"].items())
            if not (recent_stop and "waiting for your input" in msg):
                _record(state, s, "waiting")
                if _once(state, f"note:{s.id}:{msg}:{now():%Y%m%d%H%M}"):
                    notify.send(f"🟡 {short(s.label, 50)}: needs you", short(msg, 160), priority="high")
    if event == "session-start":
        from shana import memory
        out = json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": memory.session_context()}})
    save_state(state)
    return out


def poll() -> dict:
    """Run every few minutes: stuck sessions, same work, other tools, and a fallback when hooks are silent."""
    cfg = config.load()["monitor"]
    state = load_state()
    since = now() - timedelta(minutes=cfg["window_minutes"])
    hooks_quiet = not state.get("hooks_seen") or now() - parse_ts(state["hooks_seen"]) > timedelta(hours=24)
    sessions = [s for s in logs.claude_sessions(since, tail_bytes=400_000) if not s.internal]
    for s in sessions:
        status = status_of(s, cfg["stuck_minutes"])
        prev = state["sessions"].get(s.id, {})
        if prev.get("status") == "waiting" and prev.get("last") == (s.last_time or s.mtime or now()).isoformat():
            status = "waiting"  # a hook said it waits for you (e.g. a permission prompt); nothing moved since
        if status == "stuck" and _once(state, f"stuck:{s.id}:{s.last_time}"):
            mins = int((now() - (s.last_time or s.mtime)).total_seconds() // 60)
            notify.send(f"🔴 {short(s.label, 50)}: stuck?", f"No progress for {mins} min.", priority="high")
        if hooks_quiet and status != "waiting":
            _ping_finished(state, s, cfg)
        else:
            _record(state, s, status)
    for s in logs.other_sessions(since):
        _record(state, s, status_of(s, cfg["stuck_minutes"]))
    try:
        _poll_opencode(state, since, cfg)
    except Exception as e:  # noqa: BLE001
        log_error("monitor.opencode", e)
    for a, b, score in same_work_pairs(sessions, cfg["same_work_threshold"]):
        if _once(state, f"same:{min(a.id, b.id)}:{max(a.id, b.id)}:{now():%Y%m%d}"):
            notify.send("👥 Same work in two sessions?", f"{short(a.label, 60)}  ↔  {short(b.label, 60)}")
    old = (now() - timedelta(days=2)).isoformat()
    state["sessions"] = {k: v for k, v in state["sessions"].items() if v["last"] >= old}
    save_state(state)
    try:
        from shana import topics
        topics.quick_ping(sessions, state)
    except Exception as e:  # noqa: BLE001
        log_error("monitor.topics", e)
    return state


def _poll_opencode(state: dict, since, cfg: dict) -> None:
    """OpenCode: main sessions ping when they finish a long turn or look stuck; child sessions are listed only."""
    from shana import opencode
    for s in opencode.sessions(since):
        stuck = s.is_busy and now() - s.updated >= timedelta(minutes=cfg["stuck_minutes"])
        status = "stuck" if stuck else "running" if s.is_busy else "finished"
        title = ("↳ " if s.parent_id else "") + (s.title or s.id[:12])
        state["sessions"][s.id] = {"tool": "opencode", "title": title, "status": status,
                                   "project": Path(s.directory).name, "last": s.updated.isoformat(), "path": s.id}
        if s.parent_id:
            continue
        if stuck and _once(state, f"stuck:{s.id}:{s.updated.isoformat()}"):
            notify.send(f"🔴 {short(s.title, 50)}: stuck?", "OpenCode session made no progress for a while.",
                        priority="high")
        took = (s.idle - s.busy).total_seconds() if s.idle and s.busy else 0
        if (status == "finished" and took >= cfg["finished_min_seconds"] and s.idle >= since
                and _once(state, f"finished:{s.id}:{s.idle.isoformat()}")):
            notify.send(f"✅ {short(s.title, 50)}: finished", f"OpenCode · {s.outcome or 'done'} · ${s.cost:.3f}")


def status_table(hours: int = 12) -> str:
    state = load_state()
    cutoff = (now() - timedelta(hours=hours)).isoformat()
    rows = sorted((v for v in state["sessions"].values() if v["last"] >= cutoff), key=lambda v: v["last"], reverse=True)
    if not rows:
        return "No sessions in the last %d hours." % hours
    icon = {"running": "🔵", "waiting": "🟡", "finished": "✅", "stuck": "🔴", "active": "🔵", "idle": "⚪"}
    lines = [f"{icon.get(r['status'], '•')} {r['status']:<8} {parse_ts(r['last']):%H:%M}  {r['tool']:<6} "
             f"{short(r['title'], 55)}  [{r['project']}]" for r in rows]
    return "\n".join(lines)


def hook_main(event: str) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        out = hook(event, payload)
        if out:
            print(out)
    except Exception as e:  # noqa: BLE001 - hooks must never break the user's session
        log_error(f"hook.{event}", e)
    return 0
