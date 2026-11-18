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


def start(task: str, cwd: str, spec: str = "", launcher=None) -> dict:
    spec = spec or models.pick("strong")
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:30].strip("-") or "job"
    job_id = f"{now():%Y%m%d-%H%M%S}-w-{slug}"
    job = {"id": job_id, "task": task, "cwd": cwd, "spec": spec, "status": "queued", "started": now().isoformat()}
    write_json(_job_path(job_id), job)
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
        f"# {job['task']}\n\nModel: {job['spec']} · {job['status']} · ${res.cost_usd:.3f}\n"
        f"Open the full conversation: `claude --resume {res.session_id}` (in {job['cwd']})\n\n"
        f"{res.text or res.error}\n")
    write_json(_job_path(job_id), job)
    notify.send(f"{'✅' if res.ok else '❌'} Worker {job['status']}: {short(job['task'], 45)}",
                short(res.text or res.error, 160))
    memory.add("episode", f"Worker ({job['spec']}) {job['status']}: {short(job['task'], 80)} · ${res.cost_usd:.3f}",
               auto=True)
    return job


def status(job_id: str) -> str:
    job = read_json(_job_path(job_id), None)
    if not job:
        return "No such job."
    out = f"{job['id']}: {job['status']} on {job['spec']}"
    result = config.path("tasks", job_id, "result.md")
    return out + ("\n\n" + result.read_text()[:4000] if result.exists() else "")


if __name__ == "__main__":
    print(run(sys.argv[1]))
