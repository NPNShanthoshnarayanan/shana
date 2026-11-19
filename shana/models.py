"""Run a prompt on a model and record its cost.

Everything runs through the Claude Code CLI: `claude:*` models use your Claude login; other providers
(DeepSeek, Kimi, …) use their Anthropic-compatible endpoint with `--bare` and their own key.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from shana import config
from shana.util import append_jsonl, now

TIERS = ["cheap", "mid", "strong"]
AGENT_TOOLS = "Bash Read Edit Write Glob Grep"


@dataclass
class Result:
    ok: bool
    text: str
    spec: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    session_id: str = ""

    def tagged(self) -> str:
        return f"{tag(self.spec)} {self.text or self.error}"


def tag(spec: str) -> str:
    """Label added by code from the model actually started, e.g. '[Claude sonnet · Pro]', '[DeepSeek V4.1 Flash]'."""
    provider, model = spec.split(":", 1)
    return f"[Claude {model} · Pro]" if provider == "claude" else f"[{pretty(model)}]"


def pretty(model_id: str) -> str:
    """'deepseek-ai/DeepSeek-V4.1-Flash' -> 'DeepSeek V4.1 Flash'."""
    return model_id.split("/")[-1].replace("-", " ")


def claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def available(spec: str) -> bool:
    provider = spec.split(":", 1)[0]
    if provider == "claude":
        return Path(claude_bin()).exists()
    p = config.load()["providers"].get(provider)
    if not (p and config.secret(p["key_env"])):
        return False
    if provider == "deepinfra":
        from shana import budget
        return not budget.over_cap()  # monthly cap reached: the next model in the tier (Claude) takes the work
    return True


def quality(spec: str) -> dict:
    """Pass rate of the model's last checked runs (orchestrator steps and tracker fixes, from data/quality.jsonl)."""
    from shana.util import parse_ts, read_jsonl
    q = config.load()["quality"]
    runs = [e for e in read_jsonl(config.path("data", "quality.jsonl")) if e.get("spec") == spec and e.get("checked")]
    last = runs[-int(q["window"]):]
    rate = sum(bool(e["ok"]) for e in last) / len(last) if last else 1.0
    since = (now() - parse_ts(last[-1]["ts"])).total_seconds() / 3600 if last else 1e9
    weak = len(last) >= int(q["min_runs"]) and rate < float(q["min_pass"])
    return {"spec": spec, "runs": len(last), "pass_rate": round(rate, 2), "benched": weak and since < q["cooldown_hours"],
            "last": last[-1]["ts"] if last else ""}


def record_quality(spec: str, ok: bool, checked: bool = True, what: str = "") -> None:
    append_jsonl(config.path("data", "quality.jsonl"), {"ts": now().isoformat(), "spec": spec, "ok": ok,
                                                        "checked": checked, "what": what})


def pick(tier: str) -> str:
    """First usable model of the tier in config order; a model failing too many recent checks is benched for a
    cool-down (then gets one trial run again), unless it is the tier's last option."""
    specs = config.load()["models"].get(tier) or ["claude:haiku"]
    specs = [specs] if isinstance(specs, str) else specs
    usable = [s for s in specs if available(s)]
    good = [s for s in usable if not quality(s)["benched"]]
    return (good or usable or ["claude:haiku"])[0]


def quality_report() -> str:
    seen = {s for tier in config.load()["models"].values() for s in ([tier] if isinstance(tier, str) else tier)}
    rows = [quality(s) for s in sorted(seen)]
    rows = [r for r in rows if r["runs"]]
    return "\n".join(f"{r['spec']}: {int(r['pass_rate'] * 100)}% of last {r['runs']} checked runs passed"
                     f"{' — BENCHED (stronger model used until the cool-down ends)' if r['benched'] else ''}"
                     for r in rows) or "No checked runs yet."


def _cost(provider: str, model: str, data: dict, tin: int, tout: int) -> float:
    if provider == "claude":
        return float(data.get("total_cost_usd") or 0)
    p = config.load()["providers"].get(provider, {})
    pin, pout = (p.get("prices") or {}).get(model) or (p.get("price_in", 0), p.get("price_out", 0))
    return tin / 1e6 * float(pin) + tout / 1e6 * float(pout)


def run(spec: str, prompt: str, *, cwd: str | Path | None = None, agent: bool = False, timeout: float = 600,
        label: str = "") -> Result:
    provider, model = spec.split(":", 1)
    cmd = [claude_bin(), "-p", "--model", model, "--output-format", "json", "--strict-mcp-config"]
    env = {**os.environ, "SHANA_INTERNAL": "1"}
    sid = ""
    if agent:
        sid = str(uuid.uuid4())
        mark = config.path("data", "internal-sessions.txt")
        mark.parent.mkdir(parents=True, exist_ok=True)
        with mark.open("a") as fh:
            fh.write(sid + "\n")
        cmd += ["--session-id", sid, "--permission-mode", "acceptEdits", "--allowedTools", *AGENT_TOOLS.split()]
    else:
        cmd += ["--tools", "", "--no-session-persistence"]
        cwd = cwd or config.path("data", "tmp")
    if provider != "claude":
        p = config.load()["providers"][provider]
        cmd.append("--bare")
        env.update({"ANTHROPIC_BASE_URL": p["base_url"], "ANTHROPIC_API_KEY": config.secret(p["key_env"])})
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        # Claude Code's own helper calls ask for its haiku/sonnet/opus slots; point every slot at this model.
        for slot in ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                     "ANTHROPIC_SMALL_FAST_MODEL"):
            env[slot] = model
    if cwd:
        Path(cwd).mkdir(parents=True, exist_ok=True)
    try:
        cp = subprocess.run(cmd, input=prompt, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                            check=False)
        data = json.loads(cp.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        res = Result(False, "", spec, error=repr(e))
    else:
        u = data.get("usage") or {}
        tin = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        tout = u.get("output_tokens", 0)
        ok = cp.returncode == 0 and not data.get("is_error")
        res = Result(ok, data.get("result", ""), spec, _cost(provider, model, data, tin, tout), tin, tout,
                     "" if ok else (data.get("result") or cp.stderr)[-800:])
    res.session_id = sid
    append_jsonl(config.path("data", "ledger.jsonl"), {
        "ts": now().isoformat(), "label": label, "spec": spec, "provider": provider, "ok": res.ok,
        "cost_usd": round(res.cost_usd, 6), "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
        # agent runs on Claude are saved as normal sessions, so the daily log scan already counts them
        "in_logs": agent and provider == "claude"})
    return res


def run_json(spec: str, prompt: str, **kw) -> tuple[dict | list | None, Result]:
    """Run and parse the first JSON object/array in the answer."""
    res = run(spec, prompt, **kw)
    text = res.text or ""
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1]), res
            except ValueError:
                continue
    return None, res
