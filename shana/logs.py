"""Read AI session logs. Claude Code is read in detail; other tools (Codex, Gemini, OpenCode) by file activity."""
from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shana import config
from shana.util import parse_ts

_SKIP_TEXT = ("<", "Caveat:", "[Request interrupted", "This session is being continued")


@dataclass
class Session:
    id: str
    tool: str
    path: Path
    title: str = ""
    cwd: str = ""
    entrypoint: str = ""
    mtime: datetime | None = None
    last_time: datetime | None = None
    last_role: str = ""  # user | assistant
    last_kind: str = ""  # prompt | tool_result | tool_use | text | interrupted
    last_text: str = ""  # last assistant text
    last_prompt_time: datetime | None = None
    pending_ask: str = ""  # question the assistant left for the user
    user_msgs: list[tuple[datetime | None, str]] = field(default_factory=list)
    assistant_texts: list[tuple[datetime | None, str]] = field(default_factory=list)
    usage: dict[str, dict] = field(default_factory=dict)  # message id -> {ts, model, input, output, cache_*}
    tool_errors: list[str] = field(default_factory=list)

    @property
    def project(self) -> str:
        return Path(self.cwd).name if self.cwd else ""

    @property
    def internal(self) -> bool:
        """Headless runs (Shana's own model calls, scripts) are not 'your' sessions."""
        return self.entrypoint == "sdk-cli" or self.id in internal_ids()

    @property
    def label(self) -> str:
        return self.title or (self.user_msgs[0][1][:60] if self.user_msgs else self.id[:8])


def internal_ids() -> set[str]:
    """Session ids Shana started itself (models.run records them; their logs look like normal desktop sessions)."""
    try:
        return set(config.path("data", "internal-sessions.txt").read_text().split())
    except OSError:
        return set()


def _iter_lines(p: Path, tail_bytes: int | None):
    try:
        with p.open("rb") as fh:
            if tail_bytes:
                fh.seek(0, 2)
                size = fh.tell()
                if size > tail_bytes:
                    fh.seek(size - tail_bytes)
                    fh.readline()  # drop the partial first line
                else:
                    fh.seek(0)
            for raw in fh:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue
    except OSError:
        return


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]


def read_claude(p: Path, tail_bytes: int | None = None) -> Session:
    s = Session(id=p.stem, tool="claude", path=p)
    try:
        s.mtime = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
    except OSError:
        pass
    for d in _iter_lines(p, tail_bytes):
        t = d.get("type")
        if t == "custom-title":
            s.title = d.get("customTitle") or s.title
            continue
        if t not in ("user", "assistant") or d.get("isSidechain"):
            continue
        s.cwd = s.cwd or d.get("cwd", "")
        s.entrypoint = s.entrypoint or d.get("entrypoint", "")
        ts = parse_ts(d.get("timestamp"))
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "user":
            items = content if isinstance(content, list) else []
            results = [c for c in items if isinstance(c, dict) and c.get("type") == "tool_result"]
            for r in results:
                if r.get("is_error"):
                    s.tool_errors.append(str(r.get("content"))[:300])
            texts = [x for x in _texts(content) if x.strip()]
            if any("[Request interrupted" in x for x in texts):
                s.last_role, s.last_kind, s.last_time = "user", "interrupted", ts
                continue
            real = [x.strip() for x in texts if not x.lstrip().startswith(_SKIP_TEXT)]
            if real:
                s.user_msgs.append((ts, "\n".join(real)))
                s.last_role, s.last_kind, s.last_time, s.last_prompt_time = "user", "prompt", ts, ts
                s.pending_ask = ""
            elif results:
                s.last_role, s.last_kind, s.last_time = "user", "tool_result", ts
            continue
        usage = msg.get("usage")
        if usage and msg.get("id"):
            s.usage[f"{msg['id']}:{d.get('requestId', '')}"] = {
                "ts": ts, "model": msg.get("model", "?"),
                "input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "cache_write": usage.get("cache_creation_input_tokens", 0)}
        for c in content or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                s.last_role, s.last_kind, s.last_time = "assistant", "tool_use", ts
                ask = (c.get("input") or {}).get("ask") if str(c.get("name", "")).endswith("wrap_up") else None
                if ask:
                    s.pending_ask = ask
            elif c.get("type") == "text" and c.get("text", "").strip():
                s.last_role, s.last_kind, s.last_time = "assistant", "text", ts
                s.last_text = c["text"].strip()
                s.assistant_texts.append((ts, s.last_text))
    if s.last_kind == "text" and not s.pending_ask and s.last_text.rstrip().endswith("?"):
        s.pending_ask = s.last_text.strip().splitlines()[-1]
    return s


def claude_files(since: datetime | None = None) -> list[Path]:
    root = Path(config.load()["logs"]["claude"]).expanduser()
    files = []
    for p in root.glob("*/*.jsonl"):
        try:
            if since is None or datetime.fromtimestamp(p.stat().st_mtime).astimezone() >= since:
                files.append(p)
        except OSError:
            continue
    return files


def claude_sessions(since: datetime | None = None, tail_bytes: int | None = None) -> list[Session]:
    return [read_claude(p, tail_bytes) for p in claude_files(since)]


def _first_user_text(p: Path) -> str:
    """Best effort for non-Claude tools: find the first user-looking text in the file."""
    for d in _iter_lines(p, None) if p.suffix == ".jsonl" else [_load_json(p)]:
        stack = [d]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if x.get("role") == "user":
                    txt = " ".join(t for t in _texts(x.get("content")) if t) or str(x.get("content", ""))
                    if txt and not txt.lstrip().startswith(_SKIP_TEXT):
                        return txt.strip()
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(reversed(x))
    return ""


def _load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def other_sessions(since: datetime) -> list[Session]:
    out = []
    for tool, pattern in config.load()["logs"]["other"].items():
        for f in glob.glob(str(Path(pattern).expanduser()), recursive=True):
            p = Path(f)
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
            except OSError:
                continue
            if mtime < since:
                continue
            first = _first_user_text(p)
            out.append(Session(id=p.stem, tool=tool, path=p, title=first[:60], mtime=mtime, last_time=mtime,
                               user_msgs=[(None, first)] if first else []))
    return out
