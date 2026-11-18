"""Part C — do a task cheaply: plan once, run each small step on the cheapest model that passes its check,
start every step fresh (no long chat history), pass results through a notes file, stop at the budget."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from shana import config, memory, models
from shana.util import append_jsonl, now, run, short

PLAN_PROMPT = """Split this task into small steps (max 8) that a coding agent can do one at a time.
Each step starts with no memory of earlier steps except a short notes file, so make each goal self-contained.
Return ONLY JSON: {{"steps": [{{"goal": "...", "difficulty": "easy|medium|hard", "check": "shell command that
exits 0 when the step is right, or empty"}}]}}
Use "easy" for simple edits/writing, "medium" for normal code, "hard" only for tricky design or debugging.

Task: {task}
Working folder: {cwd}
Things Shana remembers that may help:
{hits}
"""

STEP_PROMPT = """You are doing ONE step of a bigger task. Do only this step, then stop.

Overall task: {task}
This step: {goal}

Notes from earlier steps:
{notes}
{failure}
When done, reply with at most 3 short lines: what you changed, which files, anything the next step must know."""

_START = {"easy": 0, "medium": 1, "hard": 2}


def _task_dir(task: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:40].strip("-") or "task"
    d = config.path("tasks", f"{now():%Y%m%d-%H%M%S}-{slug}")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check(cmd: str, cwd: str, timeout: float) -> tuple[bool, str]:
    cp = run(["/bin/sh", "-c", cmd], cwd=cwd, timeout=timeout)
    return cp.returncode == 0, (cp.stdout + cp.stderr)[-1500:]


def plan(task: str, cwd: str, runner: Callable = models.run_json) -> tuple[list[dict], float]:
    hits = "\n".join(f"- {h['text']}" for h in memory.search(task, k=6)) or "- (nothing)"
    prompt, cost = PLAN_PROMPT.format(task=task, cwd=cwd, hits=hits), 0.0
    for tier in ("plan", "strong"):  # cheap planner first; the strong model only when no usable plan comes back
        data, res = runner(models.pick(tier), prompt, label=f"plan-{tier}")
        cost += res.cost_usd
        steps = [s for s in (data.get("steps", []) if isinstance(data, dict) else [])
                 if isinstance(s, dict) and s.get("goal")][:8]
        if steps:
            return steps, cost
    return [], cost


def run_task(task: str, cwd: str, check: str = "", budget: float | None = None,
             planner: Callable = models.run_json, runner: Callable = models.run,
             checker: Callable = _check) -> dict:
    cfg = config.load()["orchestrator"]
    budget = budget if budget is not None else cfg["budget_usd"]
    d = _task_dir(task)
    notes = d / "notes.md"
    steps, spent = plan(task, cwd, planner)
    notes.write_text(f"# {task}\n\nFolder: {cwd}\n\n## Plan\n" +
                     "\n".join(f"{i + 1}. [{s.get('difficulty', 'medium')}] {s['goal']}" for i, s in enumerate(steps)) + "\n\n## Log\n")
    status = "done" if steps else "no plan"
    for i, step in enumerate(steps, 1):
        failure, ok = "", False
        start = _START.get(step.get("difficulty", "medium"), 1)
        for attempt in range(cfg["max_escalations"] + 1):
            spec = models.pick(models.TIERS[min(start + attempt, 2)])
            prompt = STEP_PROMPT.format(task=task, goal=step["goal"], notes=notes.read_text()[-6000:],
                                        failure=f"\nThe last try failed the check:\n{failure}\n" if failure else "")
            res = runner(spec, prompt, cwd=cwd, agent=True, timeout=cfg["step_timeout_s"], label=f"step-{i}")
            spent += res.cost_usd
            ok, failure = res.ok, short(res.error, 800)
            chk = step.get("check") or (check if i == len(steps) else "")  # the task's own check runs at the end
            if ok and chk:
                ok, failure = checker(chk, cwd, cfg["check_timeout_s"])
            append_jsonl(config.path("data", "quality.jsonl"), {"ts": now().isoformat(), "spec": spec, "ok": ok,
                                                                "checked": bool(chk)})
            with notes.open("a") as fh:
                fh.write(f"\n### Step {i} · {spec} · {'ok' if ok else 'failed'} · ${res.cost_usd:.3f}\n"
                         f"{(res.text or res.error).strip()[:1200]}\n")
            if ok or spent >= budget:
                break
        if spent >= budget and not (ok and i == len(steps)):
            status = f"stopped at budget (${spent:.2f} of ${budget:.2f})"
            break
        if not ok:
            status = f"step {i} failed after {cfg['max_escalations'] + 1} tries"
            break
    with notes.open("a") as fh:
        fh.write(f"\n## Result\n{status} · total ${spent:.2f}\n")
    memory.add("episode", f"Shana run '{short(task, 80)}': {status}, {len(steps)} steps, ${spent:.2f}", auto=True)
    return {"status": status, "steps": len(steps), "cost_usd": round(spent, 4), "notes": str(notes)}
