"""Small shared helpers: time, JSON files, errors, subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from shana import config


def now() -> datetime:
    return datetime.now().astimezone()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, p)


def append_jsonl(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")


def read_jsonl(p: Path) -> list[dict]:
    out = []
    try:
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def log_error(where: str, exc: BaseException) -> None:
    """Every Shana failure lands here; the tracker turns them into issues."""
    append_jsonl(config.path("data", "errors.jsonl"),
                 {"ts": now().isoformat(), "where": where, "error": repr(exc),
                  "trace": traceback.format_exc()[-1500:]})


def run(cmd: list[str], *, cwd: str | Path | None = None, timeout: float = 120, env: dict | None = None,
        shell: bool = False) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, env=full_env, capture_output=True, text=True,
                          shell=shell, check=False)


def short(text: str, n: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
