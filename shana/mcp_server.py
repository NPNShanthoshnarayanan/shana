"""MCP server over stdio, so any AI tool (Claude Code, Codex, Gemini CLI, Cursor…) can ask Shana and share memory."""
from __future__ import annotations

import json
import sys

from shana import memory, monitor, proof, report, tracker
from shana.util import log_error, now


def _s(**props) -> dict:
    return {"type": "object", "properties": {k: {"type": v} if isinstance(v, str) else v for k, v in props.items()}}


TOOLS = {
    "shana_status": ("Which AI sessions are running, waiting for the user, finished or stuck.", _s()),
    "shana_memory_search": ("Search Shana's shared memory (facts, rules, past sessions, open items).", _s(query="string")),
    "shana_memory_add": ("Save one memory. kind: fact (decision/checked result), howto (rule or way of working), "
                         "episode (what happened), open (something to come back to).",
                         _s(kind={"type": "string", "enum": ["fact", "howto", "episode", "open"]}, text="string",
                            project="string")),
    "shana_open_items": ("List things the user left open (unanswered questions, half-done tasks, pending decisions).", _s()),
    "shana_open_close": ("Close open item by its number. how: done or drop.",
                         _s(number="integer", how={"type": "string", "enum": ["done", "drop"]})),
    "shana_issue_add": ("Record a problem or feature request for the Shana tool itself.", _s(title="string", details="string")),
    "shana_issues": ("List Shana's own issues and their status.", _s()),
    "shana_proof_check": ("Check that numbers in an answer appear in the given source files or texts.",
                          _s(answer="string", sources={"type": "array", "items": {"type": "string"}})),
    "shana_worker": ("Hand one task to a background worker (default: Claude Opus via the Claude subscription) and get a "
                     "job id at once; the user gets a ping when it is done. Use for hard steps.",
                     _s(task="string", cwd="string", model={"type": "string", "enum": ["opus", "sonnet", "cheap"]})),
    "shana_job_status": ("Status and result of a background worker job.", _s(job_id="string")),
    "shana_issue_approve": ("Approve the planned fix for a Shana issue and start fixing it in the background "
                            "(only after the user says yes).", _s(number="integer")),
    "shana_cost_today": ("Today's Claude tokens and Shana's extra model costs so far (fast estimate).", _s()),
}


def call(name: str, a: dict) -> str:
    if name == "shana_status":
        return monitor.status_table()
    if name == "shana_memory_search":
        return "\n".join(f"{h['file']}: {h['text']}" for h in memory.search(a.get("query", ""))) or "Nothing found."
    if name == "shana_memory_add":
        line = memory.add(a.get("kind", "fact"), a.get("text", ""), a.get("project") or "general", source="chat")
        return f"Saved: {line}" if line else "Already saved."
    if name == "shana_open_items":
        return "\n".join(f"{i['n']}. {i['text']} ({i['date']})" for i in memory.open_items()) or "Nothing open."
    if name == "shana_open_close":
        text = memory.close_open(int(a.get("number", 0)), a.get("how", "done"))
        return f"Closed: {text}" if text else "No such open item."
    if name == "shana_issue_add":
        i = tracker.add(a.get("title", ""), a.get("details", ""))
        return f"Issue #{i['id']} recorded." if i else "Already recorded."
    if name == "shana_issues":
        return "\n".join(f"#{i['id']} [{i.get('status')}] {i.get('title')}" for i in tracker.list_issues()) or "No issues."
    if name == "shana_proof_check":
        return json.dumps(proof.check(a.get("answer", ""), a.get("sources", [])), indent=1)
    if name == "shana_worker":
        from shana import models, worker
        m = a.get("model") or "opus"
        spec = models.pick("cheap") if m == "cheap" else f"claude:{m}"
        job = worker.start(a.get("task", ""), a.get("cwd") or ".", spec)
        return f"Started job {job['id']} on {spec}. Shana will ping when it is done."
    if name == "shana_job_status":
        from shana import worker
        return worker.status(a.get("job_id", ""))
    if name == "shana_issue_approve":
        import subprocess
        from shana import config
        n = int(a.get("number", 0))
        if not tracker.set_status(n, "approved"):
            return "No such issue."
        subprocess.Popen([str(config.path("bin", "shana")), "issue", "fix", str(n)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        return f"Issue #{n} approved; the fix runs in the background and is kept only if the tests pass."
    if name == "shana_cost_today":
        day = now().date()
        t, led = report.claude_tokens(day), report.ledger(day)
        return f"Claude tokens today: {t['total']:,}. Shana extra model cost: ${led['extra_cost']:.2f}."
    raise KeyError(name)


def serve() -> int:
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if "id" not in msg:
            continue  # notifications need no answer
        method, params, result, error = msg.get("method"), msg.get("params") or {}, None, None
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                          "serverInfo": {"name": "shana", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s) in TOOLS.items()]}
            elif method == "tools/call":
                try:
                    result = {"content": [{"type": "text", "text": call(params["name"], params.get("arguments") or {})}]}
                except Exception as e:  # noqa: BLE001
                    log_error(f"mcp.{params.get('name')}", e)
                    result = {"content": [{"type": "text", "text": f"Shana error: {e!r}"}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                error = {"code": -32601, "message": f"Unknown method {method}"}
        except Exception as e:  # noqa: BLE001
            error = {"code": -32603, "message": repr(e)}
        reply = {"jsonrpc": "2.0", "id": msg["id"], **({"error": error} if error else {"result": result})}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0
