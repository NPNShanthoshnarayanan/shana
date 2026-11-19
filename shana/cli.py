"""`shana <command>` — everything Shana does, from one place."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from shana import config, memory, monitor, notify, proof, report, tracker
from shana.util import log_error, now


def _day(text: str | None) -> date:
    if not text or text == "today":
        return now().date()
    if text == "yesterday":
        return now().date() - timedelta(days=1)
    return date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shana", description="Shana — quiet helper for your AI sessions.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="sessions: running / waiting / finished / stuck")
    sub.add_parser("poll", help="background check (run by launchd every 3 min)")
    h = sub.add_parser("hook", help="Claude Code hook entry")
    h.add_argument("event", choices=["stop", "notification", "session-start"])
    for name in ("nightly", "report"):
        p = sub.add_parser(name, help="consolidate memory + nightly report" if name == "nightly" else "build report only")
        p.add_argument("--date", default=None, help="YYYY-MM-DD, today or yesterday")
        p.add_argument("--no-ccusage", action="store_true")
    m = sub.add_parser("memory", help="search / add / reindex memory")
    m.add_argument("action", choices=["search", "add", "reindex", "today"])
    m.add_argument("text", nargs="*")
    m.add_argument("--kind", default="fact", choices=["fact", "howto", "episode", "open"])
    m.add_argument("--project", default="general")
    sub.add_parser("open", help="list open items")
    for name in ("done", "drop"):
        p = sub.add_parser(name, help=f"mark open item {name}")
        p.add_argument("n", type=int)
    i = sub.add_parser("issue", help="Shana's own issues: add | list | show | plan | approve | fix | drop")
    i.add_argument("action", choices=["add", "list", "show", "plan", "approve", "fix", "drop"])
    i.add_argument("arg", nargs="*")
    c = sub.add_parser("check", help="proof-check numbers in an answer file against sources")
    c.add_argument("answer")
    c.add_argument("sources", nargs="+")
    r = sub.add_parser("run", help="do a task with cheap models, step by step")
    r.add_argument("task")
    r.add_argument("--cwd", default=".")
    r.add_argument("--check", default="", help="shell command that must pass after each step")
    r.add_argument("--budget", type=float, default=None)
    w = sub.add_parser("worker", help="run a background worker job (started by the shana_worker tool)")
    w.add_argument("job_id")
    sub.add_parser("opencode-setup", help="make Shana the default agent in OpenCode")
    tr = sub.add_parser("track", help="tracker scan: read new conversation parts and note Shana's problems")
    tr.add_argument("--hours", type=int, default=48)
    sub.add_parser("mcp", help="MCP server (used by AI tools)")
    sub.add_parser("install", help="add hooks, MCP server and background jobs")
    sub.add_parser("uninstall", help="remove hooks, MCP server and background jobs")
    sub.add_parser("setup-phone", help="turn on phone notifications (ntfy)")
    t = sub.add_parser("notify-test", help="send a test notification")
    t.add_argument("text", nargs="*")
    a = ap.parse_args(argv)

    try:
        return _dispatch(a)
    except Exception as e:
        log_error(f"cli.{a.cmd}", e)
        raise


def _dispatch(a: argparse.Namespace) -> int:
    if a.cmd == "hook":
        return monitor.hook_main(a.event)
    if a.cmd == "mcp":
        from shana import mcp_server
        return mcp_server.serve()
    if a.cmd == "worker":
        from shana import worker
        worker.run(a.job_id)
        return 0
    if a.cmd == "track":
        from shana import scan
        print(json.dumps(scan.run(a.hours), indent=1))
        return 0
    if a.cmd == "opencode-setup":
        from shana import install
        print(install.setup_opencode())
        return 0
    if a.cmd == "status":
        print(monitor.status_table())
    elif a.cmd == "poll":
        monitor.poll()
    elif a.cmd == "nightly":
        r = report.nightly(_day(a.date))
        print(report.summary_line(r))
    elif a.cmd == "report":
        r = report.build(_day(a.date), use_ccusage=not a.no_ccusage)
        print(report.render_md(r))
        print(f"Saved: {report.write(r)}")
    elif a.cmd == "memory":
        text = " ".join(a.text)
        if a.action == "search":
            for hit in memory.search(text):
                print(f"{hit['file']}: {hit['text']}")
        elif a.action == "add":
            print(memory.add(a.kind, text, a.project, source="cli") or "Already saved.")
        elif a.action == "reindex":
            print(memory.reindex(force=True))
        else:
            for e in memory.changes_since(24):
                print(f"[{e['kind']}] {e['text']}  ({e['file']})")
    elif a.cmd == "open":
        for item in memory.open_items():
            print(f"{item['n']}. {item['text']} ({item['date']})")
    elif a.cmd in ("done", "drop"):
        print(f"Closed: {memory.close_open(a.n, a.cmd)}")
    elif a.cmd == "issue":
        return _issue(a.action, a.arg)
    elif a.cmd == "check":
        print(json.dumps(proof.check(Path(a.answer).read_text(), a.sources), indent=1))
    elif a.cmd == "run":
        from shana import orchestrator
        print(json.dumps(orchestrator.run_task(a.task, str(Path(a.cwd).resolve()), a.check, a.budget), indent=1))
    elif a.cmd == "install":
        from shana import install
        print(json.dumps(install.install(), indent=1))
    elif a.cmd == "uninstall":
        from shana import install
        install.uninstall()
        print("Removed hooks, MCP server and background jobs. Your memory and reports are kept.")
    elif a.cmd == "setup-phone":
        from shana import install
        topic = install.setup_phone()
        print(f"Install the 'ntfy' app on your phone and subscribe to topic:\n  {topic}\n(server: "
              f"{config.load()['notify']['ntfy_server']}). Keep the topic private — anyone with it can read the pings.")
    elif a.cmd == "notify-test":
        notify.send("Shana", " ".join(a.text) or "Test notification ✅")
    return 0


def _issue(action: str, arg: list[str]) -> int:
    n = int(arg[0]) if arg and arg[0].isdigit() else 0
    if action == "add":
        i = tracker.add(" ".join(arg))
        print(f"Issue #{i['id']} recorded." if i else "Already recorded.")
    elif action == "list":
        for i in tracker.list_issues():
            print(f"#{i['id']} [{i.get('status')}] ({i.get('kind')}) {i.get('title')}")
    elif action == "show":
        i = tracker.get(n)
        print(Path(i["path"]).read_text() if i else "No such issue.")
    elif action == "plan":
        i = tracker.plan(n)
        print(Path(i["path"]).read_text() if i else "No such issue.")
    elif action == "approve":
        print("Approved." if tracker.set_status(n, "approved") else "No such issue.")
    elif action == "fix":
        i = tracker.fix(n)
        print(f"#{n} is now {i.get('status')}" if i else "No such issue.")
        if i and i.get("status") != "fixed":
            print("Only approved issues are fixed, and only when the tests pass. See: shana issue show", n)
    elif action == "drop":
        print("Dropped." if tracker.set_status(n, "dropped") else "No such issue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
