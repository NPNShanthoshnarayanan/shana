"""Part F — memory as plain .md files (the real copy) plus a SQLite search index built from them.

Layout: core.md (always loaded) · facts/<project>.md · how-to/<topic>.md · episodes/<date>.md ·
open-items.md (things to come back to) · archive/ (old items, never deleted).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from shana import config
from shana.util import append_jsonl, now, read_jsonl, run

KINDS = {"fact": "facts", "howto": "how-to", "episode": "episodes"}
_OPEN_RE = re.compile(r"^- \[( |x|~)\] (\d{4}-\d{2}-\d{2}) (.*)$")
CORE_TEMPLATE = """# Core memory (always loaded — keep it under ~50 lines)

## Rules
- Answer in simple English.

## Active projects
- (none yet)
"""


def root() -> Path:
    return config.path("memory")


def ensure() -> Path:
    r = root()
    for d in ("facts", "how-to", "episodes", "archive"):
        (r / d).mkdir(parents=True, exist_ok=True)
    if not (r / "core.md").exists():
        (r / "core.md").write_text(CORE_TEMPLATE)
    if not (r / "open-items.md").exists():
        (r / "open-items.md").write_text("# Open items\n\n")
    if not (r / ".git").exists() and shutil.which("git"):
        run(["git", "init", "-q"], cwd=r)
    return r


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "general").lower()).strip("-")[:40] or "general"


def _log(kind: str, file: Path, text: str, auto: bool) -> None:
    append_jsonl(config.path("data", "memory-log.jsonl"),
                 {"ts": now().isoformat(), "kind": kind, "file": str(file.relative_to(root())), "text": text,
                  "auto": auto})


def add(kind: str, text: str, project: str = "general", source: str = "", auto: bool = False) -> str:
    """Save one memory line. kind: fact | howto | episode | open."""
    ensure()
    text = " ".join(text.split())
    if kind == "open":
        return add_open(text, source, auto)
    today = f"{now():%Y-%m-%d}"
    f = root() / KINDS[kind] / (f"{today}.md" if kind == "episode" else f"{_slug(project)}.md")
    if not f.exists():
        f.write_text(f"# {f.stem}\n\n")
    if text in f.read_text():
        return ""
    line = f"- [{today}] {text}" + (f" _(from: {source})_" if source else "")
    with f.open("a") as fh:
        fh.write(line + "\n")
    _log(kind, f, text, auto)
    return line


def open_items(include_closed: bool = False) -> list[dict]:
    f = ensure() / "open-items.md"
    items = []
    for line in f.read_text().splitlines():
        m = _OPEN_RE.match(line)
        if m and (include_closed or m.group(1) == " "):
            items.append({"n": len(items) + 1, "state": m.group(1), "date": m.group(2), "text": m.group(3),
                          "line": line})
    return items


def add_open(text: str, source: str = "", auto: bool = False) -> str:
    f = ensure() / "open-items.md"
    key = re.sub(r"\W+", " ", text.lower()).strip()[:80]
    if any(re.sub(r"\W+", " ", i["text"].split(" — ")[0].lower()).strip()[:80] == key
           for i in open_items(include_closed=True)):
        return ""
    line = f"- [ ] {now():%Y-%m-%d} {text}" + (f" — {source}" if source else "")
    with f.open("a") as fh:
        fh.write(line + "\n")
    _log("open", f, text, auto)
    return line


def close_open(n: int, how: str = "done") -> str:
    """Close open item number n (as listed by open_items). how: done | drop."""
    items = open_items()
    if not 1 <= n <= len(items):
        return ""
    item = items[n - 1]
    f = root() / "open-items.md"
    f.write_text(f.read_text().replace(item["line"] + "\n", "", 1))
    done = root() / "archive" / "open-items-closed.md"
    with done.open("a") as fh:
        fh.write(item["line"].replace("- [ ]", "- [x]" if how == "done" else "- [~]", 1) + f" ({how} {now():%Y-%m-%d})\n")
    return item["text"]


def _md_files() -> list[Path]:
    return [p for p in ensure().rglob("*.md") if ".git" not in p.parts]


def reindex(force: bool = False) -> Path:
    db = config.path("data", "memory-index.sqlite")
    files = _md_files()
    newest = max((p.stat().st_mtime for p in files), default=0)
    if not force and db.exists() and db.stat().st_mtime >= newest:
        return db
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("create virtual table lines using fts5(file, date, text)")
    for p in files:
        rel = str(p.relative_to(root()))
        for line in p.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                m = re.search(r"\d{4}-\d{2}-\d{2}", line)
                con.execute("insert into lines values (?,?,?)", (rel, m.group(0) if m else "", line.strip()))
    con.commit()
    con.close()
    tmp.replace(db)
    return db


def search(query: str, k: int = 8) -> list[dict]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if not words:
        return []
    con = sqlite3.connect(reindex())
    rows = con.execute("select file, date, text from lines where lines match ? order by bm25(lines), date desc limit ?",
                       (" OR ".join(f'"{w}"' for w in words), k)).fetchall()
    con.close()
    return [{"file": f, "date": d, "text": t} for f, d, t in rows]


def session_context() -> str:
    """What every new Claude Code session gets at start (kept small on purpose)."""
    core = (ensure() / "core.md").read_text()[:3000]
    n = len(open_items())
    return (f"[Shana memory]\n{core}\nOpen items waiting: {n}. Tools: shana_memory_search, shana_memory_add, "
            "shana_open_items. Save decisions, rules and checked results with shana_memory_add.")


def changes_since(hours: int = 24) -> list[dict]:
    cutoff = (now() - timedelta(hours=hours)).isoformat()
    return [e for e in read_jsonl(config.path("data", "memory-log.jsonl")) if e["ts"] >= cutoff]


def archive_old(days: int = 30) -> int:
    cutoff = f"{now() - timedelta(days=days):%Y-%m-%d}"
    moved = 0
    for p in (ensure() / "episodes").glob("*.md"):
        if p.stem < cutoff:
            (root() / "archive" / "episodes").mkdir(parents=True, exist_ok=True)
            p.rename(root() / "archive" / "episodes" / p.name)
            moved += 1
    return moved


def commit(message: str) -> None:
    r = ensure()
    if (r / ".git").exists():
        run(["git", "add", "-A"], cwd=r)
        run(["git", "-c", "user.name=Shana", "-c", "user.email=shana@localhost", "commit", "-qm", message], cwd=r)
