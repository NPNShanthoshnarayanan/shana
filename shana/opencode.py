"""Read OpenCode sessions (its local SQLite database) so the monitor and the nightly report cover OpenCode too."""
from __future__ import annotations

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
