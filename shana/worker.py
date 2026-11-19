"""Background workers: hand one task to a model (e.g. Claude through your subscription) without blocking the chat.

`start` returns at once with a job id; the job runs in its own process, saves `tasks/<id>/result.md` and pings you.
"""
from __future__ import annotations

import re
import subprocess
import sys

from shana import config, memory, models, notify
from shana.util import now, read_json, short, write_json


def _job_path(job_id: str):
    return config.path("tasks", job_id, "job.json")


def billing(spec: str) -> str:
    """Claude models run on the subscription ($0 marginal); anything else burns the paid cap."""
    return "Claude Pro, $0 marginal" if spec.startswith("claude:") else "paid API, counts toward cap"


def _live() -> None:
    try:
        from shana import budget
        budget.write_live()
    except Exception:  # noqa: BLE001 - status text is a nice-to-have
        pass


def start(task: str, cwd: str, spec: str = "", launcher=None) -> dict:
    spec = spec or models.pick("strong")
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:30].strip("-") or "job"
    job_id = f"{now():%Y%m%d-%H%M%S}-w-{slug}"
    job = {"id": job_id, "task": task, "cwd": cwd, "spec": spec, "status": "queued", "started": now().isoformat()}
    write_json(_job_path(job_id), job)
    _live()
    cmd = [str(config.path("bin", "shana")), "worker", job_id]
    (launcher or (lambda c: subprocess.Popen(c, start_new_session=True, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)))(cmd)
    return job


def run(job_id: str) -> dict:
    job = read_json(_job_path(job_id), None)
    if not job:
        return {"status": "missing"}
    job.update(status="running")
    write_json(_job_path(job_id), job)
    res = models.run(job["spec"], job["task"] + "\n\nWhen done, reply with a short summary: what you did, files, result.",
                     cwd=job["cwd"], agent=True, timeout=config.load()["orchestrator"]["step_timeout_s"] * 2,
                     label=f"worker-{job_id}")
    job.update(status="done" if res.ok else "failed", finished=now().isoformat(), cost_usd=round(res.cost_usd, 4),
               session_id=res.session_id)
    config.path("tasks", job_id, "result.md").write_text(
        f"# {job['task']}\n\nModel: {job['spec']} ({billing(job['spec'])}) · {job['status']} · ${res.cost_usd:.3f}\n"
        f"Open the full conversation: `claude --resume {res.session_id}` (in {job['cwd']})\n\n"
        f"{res.tagged()}\n")
    write_json(_job_path(job_id), job)
    _live()
    notify.send(f"{'✅' if res.ok else '❌'} Worker {job['status']}: {short(job['task'], 45)}",
                short(res.tagged(), 160))
    memory.add("episode", f"Worker ({job['spec']}) {job['status']}: {short(job['task'], 80)} · ${res.cost_usd:.3f}",
               auto=True)
    return job


def _find(job_id: str) -> str:
    """Accept the full id, any unique part of it, or 'latest'."""
    jobs = sorted((p.parent.name for p in config.path("tasks").glob("*/job.json")), reverse=True)
    if not jobs:
        return ""
    if job_id in ("", "latest", "last"):
        return jobs[0]
    if job_id in jobs:
        return job_id
    hits = [j for j in jobs if job_id in j]
    return hits[0] if hits else ""


def list_jobs(n: int = 8) -> str:
    jobs = sorted(config.path("tasks").glob("*/job.json"), reverse=True)[:n]
    rows = [read_json(p, {}) for p in jobs]
    return "\n".join(f"{j.get('id')}: {j.get('status')} on {j.get('spec')} · ${j.get('cost_usd', 0) or 0:.3f}"
                     for j in rows) or "No jobs yet."


def status(job_id: str) -> str:
    job_id = _find(job_id)
    job = read_json(_job_path(job_id), None) if job_id else None
    if not job:
        return "No such job."
    out = f"{job['id']}: {job['status']} on {job['spec']} ({billing(job['spec'])})"
    result = config.path("tasks", job_id, "result.md")
    return out + ("\n\n" + result.read_text()[:4000] if result.exists() else "")


if __name__ == "__main__":
    print(run(sys.argv[1]))
