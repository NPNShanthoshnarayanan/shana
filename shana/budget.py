"""Real DeepInfra spend this month (OpenCode's own message costs + Shana's own calls) against your monthly cap.

DeepInfra's key cannot read billing, so this is Shana's count from real per-message costs; the DeepInfra dashboard
stays the final word (the same key may also be used outside Shana).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from shana import config
from shana.util import now, parse_ts, read_jsonl, write_json


def _month_start() -> datetime:
    return now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def opencode_spend(provider: str = "deepinfra") -> dict[str, float]:
    from shana import opencode
    p = opencode.db_path()
    out: dict[str, float] = {}
    if not p.exists():
        return out
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("select data from session_message where type='assistant' and time_created >= ?",
                           (int(_month_start().timestamp() * 1000),)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    for (data,) in rows:
        d = json.loads(data or "{}")
        m = d.get("model") or {}
        if m.get("providerID") == provider:
            out[m.get("id", "?")] = out.get(m.get("id", "?"), 0.0) + float(d.get("cost") or 0)
    return out


def month() -> dict:
    by = opencode_spend()
    start = _month_start()
    for e in read_jsonl(config.path("data", "ledger.jsonl")):
        if e.get("provider") == "deepinfra" and parse_ts(e["ts"]) >= start:
            m = e["spec"].split(":", 1)[1]
            by[m] = by.get(m, 0.0) + float(e.get("cost_usd", 0))
    cap = float(config.load()["providers"]["deepinfra"].get("monthly_cap_usd", 3.0))
    spent = sum(by.values())
    return {"spent": round(spent, 4), "cap": cap, "left": round(cap - spent, 4), "share": spent / cap if cap else 0,
            "by_model": {k: round(v, 4) for k, v in sorted(by.items(), key=lambda x: -x[1])}, "month": f"{start:%Y-%m}"}


def over_cap(margin: float = 0.97) -> bool:
    """True when this month's DeepInfra spend is at (or within 3% of) the cap: stop sending it work."""
    try:
        return month()["share"] >= margin
    except Exception:  # noqa: BLE001 - a broken count must never block work
        return False


def refresh() -> dict:
    """Write data/budget.json for the OpenCode plugin (it downgrades to a cheaper model near the cap)."""
    b = month()
    write_json(config.path("data", "budget.json"), b)
    return b


def summary(b: dict | None = None) -> str:
    b = b or month()
    lines = [f"DeepInfra {b['month']}: ${b['spent']:.3f} of ${b['cap']:.2f} cap, ${b['left']:.3f} left "
             f"(Shana's count from real per-message costs). The exact account balance cannot be read with this key "
             f"(no billing access); it is on https://deepinfra.com/dash/billing"]
    lines += [f"  {m}: ${c:.4f}" for m, c in b["by_model"].items()]
    return "\n".join(lines)


def write_live() -> str:
    """A short block of real facts the OpenCode plugin adds to every request (so even small models answer right)."""
    import json as _json
    from shana import memory, models, tracker
    jobs = []
    for p in sorted(config.path("tasks").glob("*/job.json"), reverse=True)[:3]:
        j = _json.loads(p.read_text())
        jobs.append(f"{j.get('id')}: {j.get('status')} on {j.get('spec')}")
    b = month()
    check = [f"#{i['id']} {i['title'][:50]}" for i in tracker.list_issues("needs-check")]
    lines = [f"Background jobs (newest first): {'; '.join(jobs) or 'none yet'}",
             # no exact spend here: this block heads every request, and a number that moves would break the cache
             f"DeepInfra budget this month: {'cap reached' if b['share'] >= 1 else '80%+ used' if b['share'] >= .8 else 'under 80% used'}"
             " (exact numbers: shana_budget; the real balance is only on https://deepinfra.com/dash/billing)",
             f"Open items waiting: {len(memory.open_items())}",
             "Model check pass rates: " + models.quality_report().replace("\n", "; "),
             f"Issues waiting for the user to check: {', '.join(check) or 'none'}"]
    text = "\n".join(lines)
    config.path("data", "live.txt").write_text(text)
    return text
