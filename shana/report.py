"""Part D (cost) — the nightly report: cost and tokens from every source, cross-checked, plus open items,
new issues and new memories to review."""
from __future__ import annotations

import html
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from shana import config, memory, notify
from shana.util import log_error, now, parse_ts, read_json, read_jsonl, run, write_json


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).astimezone()
    return start, start + timedelta(days=1)


def claude_tokens(day: date) -> dict:
    """Our own count from Claude Code logs (subagents included), de-duplicated like ccusage."""
    start, end = _day_bounds(day)
    root = Path(config.load()["logs"]["claude"]).expanduser()
    seen: dict[str, dict] = {}
    for p in root.rglob("*.jsonl"):
        try:
            if datetime.fromtimestamp(p.stat().st_mtime).astimezone() < start:
                continue
            with p.open("rb") as fh:
                for raw in fh:
                    if b'"usage"' not in raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    msg = d.get("message") or {}
                    u, ts = msg.get("usage"), parse_ts(d.get("timestamp"))
                    if d.get("type") != "assistant" or not u or not msg.get("id") or not ts or not start <= ts < end:
                        continue
                    seen[f"{msg['id']}:{d.get('requestId', '')}"] = {"model": msg.get("model", "?"), **u}
        except OSError:
            continue
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "by_model": {}}
    for u in seen.values():
        row = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
               "cache_read": u.get("cache_read_input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0)}
        m = out["by_model"].setdefault(u["model"], {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        for k, v in row.items():
            out[k] += v
            m[k] += v
    out["total"] = out["input"] + out["output"] + out["cache_read"] + out["cache_write"]
    return out


def ccusage(day: date) -> dict | None:
    ver = config.load()["report"]["ccusage"]
    d = f"{day:%Y%m%d}"
    config.path("data").mkdir(parents=True, exist_ok=True)
    try:
        cp = run(["npx", "-y", ver, "daily", "--json", "--since", d, "--until", d, "--offline"], timeout=240,
                 cwd=config.path("data"))
        data = json.loads(cp.stdout)
    except Exception as e:  # noqa: BLE001
        log_error("report.ccusage", e)
        return None
    rows = data.get("daily") or []
    if not rows:
        return {"cost": 0.0, "tokens": 0, "agents": [], "by_model": {}}
    r = rows[0]
    return {"cost": float(r.get("totalCost", 0)), "tokens": int(r.get("totalTokens", 0)),
            "agents": (r.get("metadata") or {}).get("agents", []),
            "by_model": {m["modelName"]: float(m.get("cost", 0)) for m in r.get("modelBreakdowns", [])}}


def ledger(day: date) -> dict:
    start, end = _day_bounds(day)
    rows = [e for e in read_jsonl(config.path("data", "ledger.jsonl")) if start <= parse_ts(e["ts"]) < end]
    extra = [e for e in rows if not e.get("in_logs")]  # not already counted by the Claude log scan
    by: dict[str, float] = {}
    for e in extra:
        by[e["provider"]] = by.get(e["provider"], 0) + e["cost_usd"]
    return {"calls": len(rows), "extra_cost": sum(by.values()), "by_provider": by,
            "extra_tokens": sum(e["tokens_in"] + e["tokens_out"] for e in extra)}


def deepinfra_month(day: date) -> dict:
    """Per-model DeepInfra calls/tokens/cost for the month of `day`, the $ cap left, and Claude Pro tokens ($0 marginal)."""
    rows = [e for e in read_jsonl(config.path("data", "ledger.jsonl"))
            if parse_ts(e["ts"]).astimezone().strftime("%Y-%m") == f"{day:%Y-%m}"]
    by: dict[str, dict] = {}
    pro = {"calls": 0, "tokens": 0}
    for e in rows:
        if e.get("provider") == "claude":
            pro["calls"] += 1
            pro["tokens"] += e.get("tokens_in", 0) + e.get("tokens_out", 0)
        elif e.get("provider") == "deepinfra":
            m = by.setdefault(e["spec"].split(":", 1)[1], {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})
            m["calls"] += 1
            m["tokens_in"] += e.get("tokens_in", 0)
            m["tokens_out"] += e.get("tokens_out", 0)
            m["cost"] += e.get("cost_usd", 0)
    cap = float(config.load()["providers"]["deepinfra"].get("monthly_cap_usd", 3.0))
    total = sum(m["cost"] for m in by.values())
    return {"by_model": by, "total": total, "cap": cap, "remaining": cap - total, "claude_pro": pro}


def opencode_usage(day: date) -> dict:
    """OpenCode sessions started that day (ccusage already prices them; this is the cross-check and the count)."""
    from shana import opencode
    start, end = _day_bounds(day)
    rows = [s for s in opencode.sessions(start) if start <= s.created < end]
    return {"sessions": len([s for s in rows if not s.parent_id]), "subtasks": len([s for s in rows if s.parent_id]),
            "tokens": sum(s.tokens for s in rows), "cost": sum(s.cost for s in rows)}


def modal(day: date) -> float | None:
    """Modal cost is written by the "Modal" session to data/modal-cost/<date>.json as {"cost_usd": 1.23}."""
    d = read_json(config.path("data", "modal-cost", f"{day:%Y-%m-%d}.json"), None)
    return float(d["cost_usd"]) if isinstance(d, dict) and "cost_usd" in d else None


def build(day: date, use_ccusage: bool = True) -> dict:
    own = claude_tokens(day)
    cc = ccusage(day) if use_ccusage else None
    led, mod = ledger(day), modal(day)
    oc = opencode_usage(day)
    checks = []
    mine = own["total"] + (oc["tokens"] if cc and "opencode" in cc["agents"] else 0)
    if cc and set(cc["agents"]) <= {"claude", "opencode"} and max(cc["tokens"], mine) > 0:
        diff = abs(cc["tokens"] - mine) / max(cc["tokens"], mine)
        checks.append({"what": "Tokens: ccusage vs Shana's own count (Claude logs + OpenCode db)", "ok": diff <= 0.05,
                       "detail": f"{cc['tokens']:,} vs {mine:,} ({diff:.1%} apart)"})
    claude_cost = cc["cost"] if cc else None
    total = (claude_cost or 0) + led["extra_cost"] + (mod or 0)
    since = (datetime.combine(day, time.min).astimezone()).isoformat()
    issues_new = []
    try:
        from shana import tracker
        issues_new = [i for i in tracker.list_issues() if i.get("created", "") >= f"{day:%Y-%m-%d}"]
    except Exception as e:  # noqa: BLE001
        log_error("report.issues", e)
    return {
        "date": f"{day:%Y-%m-%d}", "total_cost": total, "claude_cost": claude_cost, "claude_tokens": own, "opencode": oc,
        "ccusage": cc, "ledger": led, "modal_cost": mod, "checks": checks,
        "open_items": memory.open_items(),
        "new_memories": [m for m in memory.changes_since(48) if m["ts"] >= since and m.get("auto")],
        "new_issues": issues_new,
    }


def _money(v: float | None) -> str:
    return "no data" if v is None else f"${v:,.2f}"


def summary_line(r: dict) -> str:
    return (f"💰 {_money(r['total_cost'])} · 🔢 {r['claude_tokens']['total'] / 1e6:.1f}M tokens · "
            f"📌 {len(r['open_items'])} open · 🐞 {len(r['new_issues'])} new issues · 🧠 {len(r['new_memories'])} to review")


def render_md(r: dict) -> str:
    t = r["claude_tokens"]
    lines = [f"# Shana nightly report — {r['date']}", "", f"**{summary_line(r)}**", "", "## Cost",
             f"- Claude + OpenCode (from ccusage; Claude is API-equivalent): {_money(r['claude_cost'])}"]
    oc = r.get("opencode") or {}
    if oc.get("sessions") or oc.get("subtasks"):
        lines.append(f"  - OpenCode: {oc['sessions']} sessions, {oc['subtasks']} subtasks, ${oc['cost']:,.3f}, "
                     f"{oc['tokens']:,} tokens")
    if r["ccusage"]:
        lines += [f"  - {m}: ${c:,.2f}" for m, c in sorted(r["ccusage"]["by_model"].items(), key=lambda x: -x[1])]
    for prov, c in r["ledger"]["by_provider"].items():
        lines.append(f"- {prov} (Shana calls): ${c:,.2f}")
    lines += [f"- Modal GPU: {_money(r['modal_cost'])}", f"- **Total: {_money(r['total_cost'])}**", "", "## Tokens",
              f"- Claude total: {t['total']:,} (input {t['input']:,} · output {t['output']:,} · "
              f"cache read {t['cache_read']:,} · cache write {t['cache_write']:,})"]
    lines += [f"  - {m}: {sum(v.values()):,}" for m, v in t["by_model"].items() if sum(v.values())]
    lines += ["", "## Proof checks"] + [f"- {'✅' if c['ok'] else '⚠️'} {c['what']}: {c['detail']}" for c in r["checks"]]
    if not r["checks"]:
        lines.append("- (no cross-check possible today)")
    lines += ["", "## Still open"] + [f"{i['n']}. {i['text']} ({i['date']})" for i in r["open_items"]]
    lines += ["", "## New issues"] + [f"- #{i['id']} {i['title']}" for i in r["new_issues"]]
    lines += ["", "## New memories to review (fix or delete in ~/Documents/shana/memory)"]
    lines += [f"- [{m['kind']}] {m['text']}  _{m['file']}_" for m in r["new_memories"]]
    return "\n".join(lines) + "\n"


def render_html(md: str) -> str:
    body = []
    for line in md.splitlines():
        esc = html.escape(line).replace("**", "")
        if line.startswith("# "):
            body.append(f"<h1>{esc[2:]}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{esc[3:]}</h2>")
        elif line.strip():
            body.append(f"<p class='{'sub' if line.startswith('  ') else ''}'>{esc}</p>")
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Shana report</title><style>:root{--bg:#fff;--fg:#1a1a1a;--mute:#666}"
            "@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--mute:#999}}"
            "body{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;"
            "max-width:760px;margin:24px auto;padding:0 16px}h2{margin-top:28px}p{margin:4px 0}"
            ".sub{color:var(--mute);padding-left:18px}</style>" + "\n".join(body))


def write(r: dict) -> Path:
    out = config.path("reports")
    out.mkdir(parents=True, exist_ok=True)
    md = render_md(r)
    (out / f"{r['date']}.md").write_text(md)
    page = render_html(md)
    (out / f"{r['date']}.html").write_text(page)
    (out / "latest.html").write_text(page)
    write_json(config.path("data", "reports", f"{r['date']}.json"), r)
    return out / f"{r['date']}.html"


def nightly(day: date | None = None) -> dict:
    """Consolidate today's sessions into memory, then build, save and send the report."""
    day = day or now().date()
    try:
        from shana import nightly as consolidate
        consolidate.run_day(day)
    except Exception as e:  # noqa: BLE001
        log_error("report.consolidate", e)
    r = build(day)
    page = write(r)
    notify.send(f"🌙 Shana · {r['date']}", summary_line(r) + f"\nReport: {page}")
    memory.archive_old()
    memory.commit(f"nightly {r['date']}")
    return r
