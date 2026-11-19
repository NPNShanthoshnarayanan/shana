"""Paths and settings. `config.toml` in the Shana home overrides DEFAULTS key by key."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

DEFAULTS: dict = {
    "notify": {"mac": True, "ntfy_server": "https://ntfy.sh", "ntfy_topic": ""},
    "monitor": {
        "stuck_minutes": 15,  # running with no log change this long -> "stuck"
        "finished_min_seconds": 60,  # only ping "finished" for turns longer than this
        "window_minutes": 120,  # sessions touched within this window are watched
        "same_work_threshold": 0.45,
        "loop_threshold": 20,  # consecutive same tool calls to flag as a doom loop
    },
    "topics": {"ping_after_minutes": 60},
    "report": {"ccusage": "ccusage@20.0.51"},
    "logs": {
        "claude": "~/.claude/projects",
        "other": {
            "codex": "~/.codex/sessions/**/*.jsonl",
            "gemini": "~/.gemini/tmp/**/*.json",
            "opencode": "~/.local/share/opencode/storage/session/**/*.json",
        },
    },
    # Each tier is a list of "provider:model"; the first available one is used.
    "models": {
        "cheap": ["claude:haiku", "deepseek:deepseek-chat", "kimi:kimi-k2-turbo-preview"],
        "mid": ["claude:sonnet"],
        "strong": ["claude:opus"],
        "plan": ["claude:opus"],
        "summarize": ["claude:haiku", "deepseek:deepseek-chat"],
    },
    # Non-Claude providers run through Claude Code pointed at their Anthropic-compatible endpoint.
    # Prices are USD per 1M tokens; fill them from the provider's price page (0 = unknown).
    "providers": {
        "deepseek": {"base_url": "https://api.deepseek.com/anthropic", "key_env": "DEEPSEEK_API_KEY",
                     "price_in": 0.0, "price_out": 0.0},
        "kimi": {"base_url": "https://api.moonshot.ai/anthropic", "key_env": "MOONSHOT_API_KEY",
                 "price_in": 0.0, "price_out": 0.0},
        # One key, many open models; per-model prices go in `prices = {"<model>" = [in, out]}`.
        "deepinfra": {"base_url": "https://api.deepinfra.com/anthropic", "key_env": "DEEPINFRA_API_KEY",
                      "price_in": 0.0, "price_out": 0.0, "prices": {}, "monthly_cap_usd": 3.0},
    },
    "orchestrator": {"budget_usd": 2.0, "max_escalations": 2, "step_timeout_s": 900, "check_timeout_s": 600},
}


def home() -> Path:
    return Path(os.environ.get("SHANA_HOME") or Path(__file__).resolve().parent.parent)


def path(*parts: str) -> Path:
    return home().joinpath(*parts)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load() -> dict:
    f = path("config.toml")
    if not f.exists():
        return copy.deepcopy(DEFAULTS)
    with f.open("rb") as fh:
        return _merge(DEFAULTS, tomllib.load(fh))


def secret(name: str) -> str:
    """Read a key from the environment, else from data/keys.toml (never committed)."""
    if os.environ.get(name):
        return os.environ[name]
    f = path("data", "keys.toml")
    if f.exists():
        with f.open("rb") as fh:
            return str(tomllib.load(fh).get(name, ""))
    return ""
