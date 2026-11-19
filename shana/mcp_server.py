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
    "shana_issue_add": ("Record a problem with Shana ITSELF (a wrong answer it gave, its behaviour, UI, cost, missing "
                        "feature). Never use it for bugs in other projects or tools. fix_now=true when the user already said fix it: it is "
                        "approved and the fix starts at once.", _s(title="string", details="string", fix_now="boolean")),
    "shana_issue_close": ("Close a Shana issue that is not needed (made by mistake, duplicate, not a Shana problem).",
                          _s(number="integer", reason="string")),
    "shana_set_model": ("Really change which model OpenCode uses for 'chat' (Shana), 'subagents' or 'explore'. Use a "
                        "DeepInfra model id like deepseek-ai/DeepSeek-V4.1-Flash. Applies to the next message.",
                        _s(target={"type": "string", "enum": ["chat", "subagents", "explore"]}, model="string")),
    "shana_models_used": ("The REAL models (and cost) used by recent OpenCode sessions and their subagents, read from "
                          "OpenCode's database. Use it before saying which model did something.", _s(hours="integer")),
    "shana_jobs": ("ALWAYS use for any question about background jobs, workers or tasks Shana started (status, "
                   "result, which model): lists recent jobs with real status and model. Jobs are not in the project "
                   "folder, so never search files for them.", _s()),
    "shana_budget": ("Real DeepInfra spend this month vs the monthly cap, per model.", _s()),
    "shana_issues": ("List Shana's own issues and their status.", _s()),
    "shana_proof_check": ("Check that numbers in an answer appear in the given source files or texts.",
                          _s(answer="string", sources={"type": "array", "items": {"type": "string"}})),
    "shana_worker": ("Hand one task to a background worker (default: Claude Opus via the Claude subscription) and get a "
                     "job id at once; the user gets a ping when it is done. Use for hard steps.",
                     _s(task="string", cwd="string", model={"type": "string", "enum": ["opus", "sonnet", "cheap"]})),
    "shana_job_status": ("Status and result of a background worker job (full id, part of it, or 'latest').",
                         _s(job_id="string")),
    "shana_issue_approve": ("Approve the planned fix for a Shana issue and start fixing it in the background "
                            "(only after the user says yes).", _s(number="integer")),
    "shana_model_quality": ("How often each model's work passed its checks, and which models are benched.", _s()),
    "shana_cost_today": ("Today's Claude tokens and Shana's extra model costs so far (fast estimate).", _s()),
}


def call(name: str, a: dict) -> str:
    if name == "shana_status":
        return monitor.status_table()
    if name == "shana_memory_search":
        return "\n".join(f"{h['file']}: {h['text']}" for h in memory.search(a.get("query", ""))) or "Nothing found."
    if name == "shana_memory_add":
        line = memory.add(a.get("kind", "fact"), a.get("text", ""), a.get("project") or "general", source="chat")
        return f"RECEIPT: saved to memory: {line}" if line else "RECEIPT: nothing saved (already in memory)."
    if name == "shana_open_items":
        return "\n".join(f"{i['n']}. {i['text']} ({i['date']})" for i in memory.open_items()) or "Nothing open."
    if name == "shana_open_close":
        text = memory.close_open(int(a.get("number", 0)), a.get("how", "done"))
        return f"RECEIPT: open item closed ({a.get('how', 'done')}): {text}" if text else "NOT DONE: no such open item."
    if name == "shana_issue_add":
        i, dup = tracker.add_or_match(a.get("title", ""), a.get("details", ""))
        got = i or dup
        msg = (f"RECEIPT: same as existing issue #{dup['id']} ({dup['title']}); nothing new created." if dup
               else f"RECEIPT: issue #{i['id']} created.")
        if a.get("fix_now") and got and got.get("status") not in ("fixed", "closed"):
            return msg + " " + call("shana_issue_approve", {"number": int(got["id"])})
        return msg
    if name == "shana_issue_close":
        i = tracker.close(int(a.get("number", 0)), a.get("reason", ""))
        return f"RECEIPT: issue #{i['id']} closed: {a.get('reason', '')}" if i else "NOT DONE: no such issue."
    if name == "shana_set_model":
        from shana import install
        return install.set_opencode_model(a.get("target", "chat"), a.get("model", ""))
    if name == "shana_models_used":
        from shana import opencode
        return opencode.models_report(int(a.get("hours") or 12))
    if name == "shana_jobs":
        from shana import worker
        return worker.list_jobs()
    if name == "shana_budget":
        from shana import budget
        return budget.summary(budget.refresh())
    if name == "shana_issues":
        return "\n".join(f"#{i['id']} [{i.get('status')}] {i.get('title')}" for i in tracker.list_issues()) or "No issues."
    if name == "shana_proof_check":
        return json.dumps(proof.check(a.get("answer", ""), a.get("sources", [])), indent=1)
    if name == "shana_worker":
        from shana import models, worker
        m = a.get("model") or "opus"
        spec = models.pick("cheap") if m == "cheap" else f"claude:{m}"
        job = worker.start(a.get("task", ""), a.get("cwd") or ".", spec)
        return (f"RECEIPT: job {job['id']} started on {spec} ({worker.billing(spec)}). It pings the user when done; "
                "check it later with shana_job_status (job id or 'latest'). It has NOT finished yet.")
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
        return (f"RECEIPT: issue #{n} approved; the fix runs in the background. It counts as fixed only when the tests "
                "pass AND the issue's own check proves the problem is gone; the user gets a ping with the result. "
                "It is NOT fixed yet.")
    if name == "shana_model_quality":
        from shana import models
        return models.quality_report()
    if name == "shana_cost_today":
        from shana import budget
        day = now().date()
        t = report.claude_tokens(day)
        return f"Claude (subscription) tokens today: {t['total']:,}.\n" + budget.summary(budget.refresh())
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
