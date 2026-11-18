"""Tests for every Shana part, run against a temporary Shana home and fake session logs (no real model calls)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

TMP = tempfile.mkdtemp(prefix="shana-test-")
os.environ["SHANA_HOME"] = TMP
os.environ.pop("SHANA_INTERNAL", None)
LOGS = Path(TMP, "claude-logs")
Path(TMP, "config.toml").write_text(
    f'[notify]\nmac = false\n[logs]\nclaude = "{LOGS}"\n[logs.other]\n')

from shana import config, logs, memory, monitor, orchestrator, proof, report, topics, tracker  # noqa: E402
from shana import install, mcp_server  # noqa: E402
from shana.models import Result  # noqa: E402
from shana.util import now, read_jsonl  # noqa: E402


def ts(minutes_ago: float) -> str:
    return (now() - timedelta(minutes=minutes_ago)).isoformat()


def write_session(name: str, entries: list[dict], project: str = "proj") -> Path:
    p = LOGS / f"-x-{project}" / f"{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    base = {"cwd": f"/x/{project}", "entrypoint": "claude-desktop", "sessionId": name}
    p.write_text("\n".join(json.dumps({**base, **e}) for e in entries) + "\n")
    return p


def user(text: str, minutes_ago: float) -> dict:
    return {"type": "user", "timestamp": ts(minutes_ago), "message": {"role": "user", "content": text}}


def tool_result(minutes_ago: float, error: bool = False) -> dict:
    return {"type": "user", "timestamp": ts(minutes_ago),
            "message": {"content": [{"type": "tool_result", "content": "out", "is_error": error}]}}


def assistant(minutes_ago: float, text: str = "", tool: str = "", tool_input: dict | None = None,
              mid: str = "m1", tokens: int = 100) -> dict:
    content = [{"type": "tool_use", "name": tool, "input": tool_input or {}}] if tool else [{"type": "text", "text": text}]
    return {"type": "assistant", "timestamp": ts(minutes_ago), "requestId": "r-" + mid,
            "message": {"id": mid, "model": "claude-test", "content": content,
                        "usage": {"input_tokens": tokens, "output_tokens": 10, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}


def notifications() -> list[dict]:
    return read_jsonl(config.path("data", "notifications.jsonl"))


class LogsAndMonitor(unittest.TestCase):
    def test_reads_title_prompts_ask_and_dedupes_usage(self):
        p = write_session("s1", [
            {"type": "custom-title", "customTitle": "Fix login"}, user("please fix login", 5),
            assistant(4, tool="Bash", mid="a"), tool_result(4),
            assistant(3, tool="mcp__ccd_turn__wrap_up", tool_input={"ask": "Ship it?"}, mid="b"),
            assistant(3, text="Done. Ship it?", mid="b")])
        s = logs.read_claude(p)
        self.assertEqual(s.title, "Fix login")
        self.assertEqual(s.user_msgs[0][1], "please fix login")
        self.assertEqual(s.pending_ask, "Ship it?")
        self.assertEqual(len(s.usage), 2)
        self.assertEqual(monitor.status_of(s, 15), "waiting")

    def test_sessions_started_by_shana_are_internal(self):
        p = write_session("int1", [user("You are doing ONE step", 2), assistant(1, text="ok")])
        self.assertFalse(logs.read_claude(p).internal)
        config.path("data").mkdir(parents=True, exist_ok=True)
        with config.path("data", "internal-sessions.txt").open("a") as fh:
            fh.write("int1\n")
        self.assertTrue(logs.read_claude(p).internal)

    def test_running_stuck_finished(self):
        running = logs.read_claude(write_session("s2", [user("go", 3), assistant(1, tool="Bash")]))
        stuck = logs.read_claude(write_session("s3", [user("go", 40), assistant(30, tool="Bash")]))
        done = logs.read_claude(write_session("s4", [user("go", 3), assistant(1, text="All done.")]))
        self.assertEqual(monitor.status_of(running, 15), "running")
        self.assertEqual(monitor.status_of(stuck, 15), "stuck")
        self.assertEqual(monitor.status_of(done, 15), "finished")

    def test_same_work_pairs_skip_forks(self):
        a = logs.read_claude(write_session("w1", [{"type": "custom-title", "customTitle": "A"},
                                                  user("train qwen lora adapter modal budget", 2)]))
        b = logs.read_claude(write_session("w2", [{"type": "custom-title", "customTitle": "B"},
                                                  user("modal budget for qwen lora adapter train", 1)]))
        fork = logs.read_claude(write_session("w3", [{"type": "custom-title", "customTitle": "A"},
                                                     user("train qwen lora adapter modal budget", 2)]))
        pairs = monitor.same_work_pairs([a, b, fork], 0.45)
        self.assertTrue(any({x.id, y.id} == {"w1", "w2"} for x, y, _ in pairs))
        self.assertFalse(any({x.id, y.id} == {"w1", "w3"} for x, y, _ in pairs))

    def test_stop_hook_pings_long_turns_only_and_skips_internal(self):
        before = len(notifications())
        long_turn = write_session("h1", [user("build it", 5), assistant(0.1, text="Built and tested.")])
        monitor.hook("stop", {"transcript_path": str(long_turn)})
        self.assertEqual(len(notifications()), before + 1)
        self.assertIn("finished", notifications()[-1]["title"])
        short_turn = write_session("h2", [user("hi", 0.3), assistant(0.2, text="Hello.")])
        monitor.hook("stop", {"transcript_path": str(short_turn)})
        self.assertEqual(len(notifications()), before + 1)
        with mock.patch.dict(os.environ, {"SHANA_INTERNAL": "1"}):
            monitor.hook("stop", {"transcript_path": str(write_session("h3", [user("x", 9), assistant(0, text="y")]))})
        self.assertEqual(len(notifications()), before + 1)

    def test_session_start_hook_returns_core_memory(self):
        out = json.loads(monitor.hook("session-start", {}))
        self.assertIn("Core memory", out["hookSpecificOutput"]["additionalContext"])

    def test_poll_pings_stuck_once(self):
        write_session("p1", [user("long job", 50), assistant(40, tool="Bash", mid="p")])
        before = len([n for n in notifications() if "stuck" in n["title"]])
        monitor.poll()
        monitor.poll()
        after = len([n for n in notifications() if "stuck" in n["title"]])
        self.assertEqual(after, before + 1)
        self.assertIn("stuck", monitor.status_table())


class MemoryTopicsProof(unittest.TestCase):
    def test_add_search_dedupe(self):
        self.assertTrue(memory.add("fact", "Project budget raised to 25 dollars", project="demo"))
        self.assertEqual(memory.add("fact", "Project budget raised to 25 dollars", project="demo"), "")
        hits = memory.search("project budget")
        self.assertTrue(any("25 dollars" in h["text"] for h in hits))

    def test_open_items_add_close(self):
        memory.add_open("decide memory tool", source="chat")
        memory.add_open("decide memory tool", source="chat")
        items = [i for i in memory.open_items() if "decide memory tool" in i["text"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(memory.close_open(items[0]["n"], "drop"), items[0]["text"])
        self.assertFalse([i for i in memory.open_items() if "decide memory tool" in i["text"]])

    def test_topic_heuristics(self):
        waiting = logs.read_claude(write_session("t1", [user("plan", 120), assistant(90, text="Pick A or B?")]))
        fresh = logs.read_claude(write_session("t2", [user("plan", 10), assistant(5, text="Pick A or B?")]))
        noreply = logs.read_claude(write_session("t3", [user("why is it slow?", 100)]))
        kinds = {i["kind"] for i in topics.heuristics([waiting, fresh, noreply], 60)}
        self.assertEqual(kinds, {"decision waiting on you", "your question got no reply"})

    def test_proof_check(self):
        res = proof.check("Same meaning: 33 of 52 tests. Cost was $1,234.50 total. It took 3 tries.",
                          ["results: same_meaning=33 total=52 cost 1234.5"])
        self.assertTrue(res["ok"])
        bad = proof.check("Accuracy was 81.3% on 756 items.", ["615 of 756 correct"])
        self.assertEqual(bad["unproven"][0]["missing"], ["81.3"])


class TrackerOrchestratorReport(unittest.TestCase):
    def test_tracker_flow_and_errors(self):
        i = tracker.add("Report shows wrong total", "seen today")
        self.assertIsNone(tracker.add("Report shows wrong total"))
        self.assertEqual(tracker.set_status(int(i["id"]), "approved")["status"], "approved")
        from shana.util import log_error
        try:
            raise OSError("disk full")
        except OSError as e:
            log_error("unit.test", e)
        self.assertGreaterEqual(tracker.from_errors(now().date()), 1)
        self.assertTrue(any("unit.test" in x["title"] for x in tracker.list_issues()))

    def test_orchestrator_escalates_then_passes(self):
        plan = {"steps": [{"goal": "write hello.txt", "difficulty": "easy", "check": "test -f hello.txt"}]}
        planner = mock.Mock(return_value=(plan, Result(True, "", "x", cost_usd=0.01)))
        runner = mock.Mock(return_value=Result(True, "wrote it", "x", cost_usd=0.02))
        checks = iter([(False, "missing"), (True, "")])
        out = orchestrator.run_task("say hello", TMP, planner=planner, runner=runner, checker=lambda *a: next(checks))
        self.assertEqual(out["status"], "done")
        self.assertEqual(runner.call_count, 2)
        tiers = [c.args[0] for c in runner.call_args_list]
        self.assertNotEqual(tiers[0], tiers[1]) if tiers[0] != "claude:haiku" else None
        self.assertIn("failed the check", runner.call_args_list[1].args[1])
        self.assertAlmostEqual(out["cost_usd"], 0.05)

    def test_plan_falls_back_to_strong_when_cheap_plan_is_empty(self):
        good = {"steps": [{"goal": "x", "difficulty": "easy", "check": ""}]}
        planner = mock.Mock(side_effect=[(None, Result(True, "junk", "a", cost_usd=0.01)),
                                         (good, Result(True, "", "b", cost_usd=0.04))])
        steps, cost = orchestrator.plan("t", TMP, planner)
        self.assertEqual(len(steps), 1)
        self.assertAlmostEqual(cost, 0.05)
        self.assertEqual(planner.call_count, 2)

    def test_orchestrator_budget_stop(self):
        plan = {"steps": [{"goal": f"step {k}", "difficulty": "easy", "check": ""} for k in range(3)]}
        planner = mock.Mock(return_value=(plan, Result(True, "", "x", cost_usd=0.0)))
        runner = mock.Mock(return_value=Result(True, "ok", "x", cost_usd=1.0))
        out = orchestrator.run_task("big", TMP, budget=1.5, planner=planner, runner=runner, checker=lambda *a: (True, ""))
        self.assertIn("budget", out["status"])
        self.assertEqual(runner.call_count, 2)

    def test_report_counts_tokens_ledger_modal(self):
        write_session("r1", [user("x", 5), assistant(4, text="a", mid="u1", tokens=1000),
                             assistant(4, text="a", mid="u1", tokens=1000), assistant(3, text="b", mid="u2", tokens=500)])
        day = now().date()
        from shana.util import append_jsonl, write_json
        append_jsonl(config.path("data", "ledger.jsonl"), {"ts": now().isoformat(), "provider": "deepseek",
                     "cost_usd": 0.4, "tokens_in": 10, "tokens_out": 5, "in_logs": False})
        write_json(config.path("data", "modal-cost", f"{day}.json"), {"cost_usd": 0.7})
        r = report.build(day, use_ccusage=False)
        self.assertGreaterEqual(r["claude_tokens"]["input"], 1500)
        self.assertAlmostEqual(r["total_cost"], 1.1, places=2)
        page = report.write(r)
        self.assertTrue(page.exists())
        self.assertIn("Modal GPU: $0.70", report.render_md(r))


class McpAndInstall(unittest.TestCase):
    def test_mcp_roundtrip(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "shana_memory_add", "arguments": {"kind": "howto", "text": "use simple English"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "shana_memory_search", "arguments": {"query": "simple english"}}}]
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("\n".join(json.dumps(m) for m in msgs))), \
                mock.patch.object(sys, "stdout", out):
            mcp_server.serve()
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertIn("shana_status", [t["name"] for t in replies[1]["result"]["tools"]])
        self.assertIn("simple English", replies[3]["result"]["content"][0]["text"])

    def test_hooks_install_keep_other_settings(self):
        f = Path(TMP, "settings.json")
        f.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
        install.install_hooks(f)
        install.install_hooks(f)
        data = json.loads(f.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        install.remove_hooks(f)
        data = json.loads(f.read_text())
        self.assertEqual(data["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]})


class Prices(unittest.TestCase):
    def test_per_model_price_wins_over_provider_price(self):
        from shana import models
        cfg = config.load()
        cfg["providers"]["deepinfra"]["prices"] = {"m-cheap": [0.2, 0.6]}
        with mock.patch.object(config, "load", return_value=cfg):
            self.assertAlmostEqual(models._cost("deepinfra", "m-cheap", {}, 1_000_000, 1_000_000), 0.8)
            self.assertEqual(models._cost("deepinfra", "other", {}, 1_000_000, 0), 0.0)
            self.assertEqual(models._cost("claude", "haiku", {"total_cost_usd": 0.5}, 9, 9), 0.5)


class OpenCodeWorkerQuality(unittest.TestCase):
    def _db(self) -> Path:
        import sqlite3
        db = Path(TMP, "oc.db")
        db.unlink(missing_ok=True)
        con = sqlite3.connect(db)
        con.execute("create table session_v2 (id, title, parent_id, directory, cost, tokens_input, tokens_output, "
                    "tokens_cache_read, tokens_cache_write, time_created, time_updated, time_busy, time_idle, idle_outcome)")
        ms = lambda m: int((now() - timedelta(minutes=m)).timestamp() * 1000)  # noqa: E731
        con.execute("insert into session_v2 values ('p1','Build site',null,'/x/site',0.01,100,50,0,0,?,?,?,?,'succeeded')",
                    (ms(5), ms(1), ms(5), ms(1)))
        con.execute("insert into session_v2 values ('c1','Make page','p1','/x/site',0.002,10,5,0,0,?,?,?,?,'succeeded')",
                    (ms(4), ms(2), ms(4), ms(2)))
        con.execute("insert into session_v2 values ('p2','Long job',null,'/x/site',0,0,0,0,0,?,?,?,null,'')",
                    (ms(60), ms(40), ms(60)))
        con.commit()
        con.close()
        return db

    def test_opencode_sessions_monitor_and_report(self):
        from shana import opencode
        cfg = config.load()
        cfg["opencode"] = {"db": str(self._db())}
        with mock.patch.object(config, "load", return_value=cfg):
            rows = {s.id: s for s in opencode.sessions(now() - timedelta(hours=2))}
            self.assertTrue(rows["p2"].is_busy)
            self.assertFalse(rows["p1"].is_busy)
            state = {"sessions": {}, "notified": {}}
            before = len(notifications())
            monitor._poll_opencode(state, now() - timedelta(hours=2), cfg["monitor"])
            titles = [n["title"] for n in notifications()[before:]]
            self.assertTrue(any("Build site" in t and "finished" in t for t in titles))
            self.assertTrue(any("Long job" in t and "stuck" in t for t in titles))
            self.assertFalse(any("Make page" in t for t in titles))  # child sessions never ping
            self.assertEqual(state["sessions"]["c1"]["title"], "↳ Make page")
            u = report.opencode_usage(now().date())
            self.assertEqual((u["sessions"], u["subtasks"]), (2, 1))

    def test_worker_start_and_run(self):
        from shana import worker
        launched = []
        job = worker.start("fix the bug", TMP, "claude:opus", launcher=launched.append)
        self.assertEqual(launched[0][-2:], ["worker", job["id"]])
        with mock.patch("shana.models.run", return_value=Result(True, "fixed it", "claude:opus", cost_usd=0.3,
                                                                  session_id="sid-1")):
            done = worker.run(job["id"])
        self.assertEqual(done["status"], "done")
        self.assertIn("claude --resume sid-1", worker.status(job["id"]))

    def test_quality_issue_and_auto_plan(self):
        from shana.util import append_jsonl
        for ok in (False, False, True):
            append_jsonl(config.path("data", "quality.jsonl"), {"ts": now().isoformat(), "spec": "x:bad", "ok": ok})
        self.assertEqual(tracker.from_quality(now().date()), 1)
        with mock.patch("shana.models.run_json", return_value=({"kind": "bug", "plan": ["swap model"], "files": [],
                                                                "test": "run"}, Result(True, "", "m"))):
            planned = tracker.auto_plan()
        self.assertTrue(planned and all(i["status"] == "planned" for i in planned))

    def test_opencode_setup_keeps_other_settings(self):
        f = Path(TMP, "oc.json")
        f.write_text(json.dumps({"theme": "dark", "mcp": {"other": {"type": "local", "command": ["x"]}}}))
        install.setup_opencode(f)
        install.setup_opencode(f)
        d = json.loads(f.read_text())
        self.assertEqual(d["theme"], "dark")
        self.assertEqual(d["default_agent"], "shana")
        self.assertIn("shana", d["mcp"]["servers"])
        self.assertIn("other", d["mcp"])
        self.assertEqual(len([i for i in d["instructions"] if i.endswith("core.md")]), 1)


if __name__ == "__main__":
    unittest.main()
