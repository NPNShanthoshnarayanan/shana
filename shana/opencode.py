"""Read OpenCode sessions (its local SQLite database) so the monitor and the nightly report cover OpenCode too."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shana import config

DB = "~/.local/share/opencode/opencode.db"


@dataclass
class OcSession:
    id: str
    title: str
    parent_id: str | None
    directory: str
    cost: float
    tokens: int
    created: datetime
    updated: datetime
    busy: datetime | None
    idle: datetime | None
    outcome: str

    @property
    def is_busy(self) -> bool:
        return bool(self.busy and (not self.idle or self.busy > self.idle))


def _ts(ms) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000).astimezone() if ms else None


def db_path() -> Path:
    return Path(config.load().get("opencode", {}).get("db", DB)).expanduser()


def sessions(since: datetime) -> list[OcSession]:
    p = db_path()
    if not p.exists():
        return []
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "select id, title, parent_id, directory, cost, coalesce(tokens_input,0)+coalesce(tokens_output,0)"
            "+coalesce(tokens_cache_read,0)+coalesce(tokens_cache_write,0), time_created, time_updated, time_busy, "
            "time_idle, coalesce(idle_outcome,'') from session_v2 where time_updated >= ?",
            (int(since.timestamp() * 1000),)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [OcSession(r[0], r[1] or "", r[2], r[3] or "", float(r[4] or 0), int(r[5] or 0), _ts(r[6]), _ts(r[7]),
                      _ts(r[8]), _ts(r[9]), r[10]) for r in rows]


def provider_models(provider: str) -> set[str]:
    """Model ids OpenCode lists for a provider (empty set if the CLI is unavailable)."""
    from shana.util import run
    try:
        cp = run(["opencode", "models"], timeout=60)
    except Exception:  # noqa: BLE001
        return set()
    return {line.split("/", 1)[1] for line in cp.stdout.splitlines() if line.startswith(provider + "/")}


def _messages(session_id: str) -> list[tuple[str, dict]]:
    import json
    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("select type, data from session_message where session_id=? order by seq",
                           (session_id,)).fetchall()
    finally:
        con.close()
    return [(t, json.loads(d or "{}")) for t, d in rows]


def models_report(hours: int = 12) -> str:
    """Which models really answered in recent sessions (and subagents), with cost — from OpenCode's own records."""
    from datetime import timedelta
    from shana.util import now
    rows = sorted(sessions(now() - timedelta(hours=hours)), key=lambda s: s.created)
    if not rows:
        return "No OpenCode sessions in that time."
    out = []
    for s in rows:
        used: dict[str, list] = {}
        for t, d in _messages(s.id):
            if t == "assistant":
                m = (d.get("model") or {}).get("id", "?")
                u = used.setdefault(m, [0, 0.0])
                u[0] += 1
                u[1] += float(d.get("cost") or 0)
        models = ", ".join(f"{m} ×{n} (${c:.3f})" for m, (n, c) in used.items()) or "no replies"
        out.append(f"{'  ↳ ' if s.parent_id else ''}{s.title or s.id[-8:]}: {models}")
    return "\n".join(out)


_CORRECTION = re.compile(r"\b(u r wrong|you are wrong|you're wrong|ur wrong|not correct|incorrect|wrong answer|"
                         r"dont assume|don't assume|no assumption|that is wrong|thats wrong|that's wrong)\b", re.I)
_LABEL = re.compile(r"^\W*\[([^\]]{2,40})\]")
_CLAIM = re.compile(r"\b(it works|works now|is working|now working|fixed|is done|all done|reliabl[ey]|verified|"
                    r"applied|switched)\b", re.I)
_ORDER = re.compile(r"\b(fix|do)\s+(it|that|this|all|them|#\d+)\b|^\W*(yes|go ahead|just do it)\W*$", re.I)


def _text(d: dict) -> str:
    return " ".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text")


def quality_signals(since) -> list[dict]:
    """Real signals of bad answers: user corrections, false model labels, "works/fixed" claims in a turn that ran no
    tool, and the user having to repeat an order."""
    found = []
    for s in sessions(since):
        if s.parent_id:
            continue
        msgs = _messages(s.id)
        tools_in_turn, last_order = False, False
        for k, (t, d) in enumerate(msgs):
            if t == "user":
                order = bool(_ORDER.search(d.get("text", "")))
                if order and last_order:
                    found.append({"kind": "repeated-order", "session": s.title, "user": d.get("text", "")[:120]})
                tools_in_turn, last_order = False, order
            elif t == "assistant":
                tools_in_turn |= any(p.get("type") == "tool" for p in d.get("content", []))
                txt = _text(d)
                claim = any(not re.search(r"\b(not|n't|never|no)\b[^.]{0,15}$", txt[:m.start()], re.I)
                            for m in _CLAIM.finditer(txt[:200]))  # a "done" claim leads the reply
                if claim and not tools_in_turn:
                    found.append({"kind": "unproven-claim", "session": s.title, "said": _text(d)[:200]})
            if t == "user" and _CORRECTION.search(d.get("text", "")):
                prev = next((x for tt, x in reversed(msgs[:k]) if tt == "assistant"), {})
                said = " ".join(p.get("text", "") for p in prev.get("content", []) if p.get("type") == "text")
                found.append({"kind": "user-correction", "session": s.title, "user": d.get("text", "")[:200],
                              "shana_said": said[:300]})
            if t == "assistant":
                real = (d.get("model") or {}).get("id", "")
                text = " ".join(p.get("text", "") for p in d.get("content", []) if p.get("type") == "text")
                m = _LABEL.match(text)
                norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())  # noqa: E731
                if m and real and norm(m.group(1)) not in norm(real) and norm(real.split("/")[-1]) not in norm(m.group(1)):
                    found.append({"kind": "false-model-label", "session": s.title, "label": m.group(1), "real": real})
    return found
