"""Install / remove Shana's hooks (Claude Code), MCP server and the two background jobs (launchd)."""
from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path

from shana import config, memory, notify
from shana.util import now, run

HOOKS = {"Stop": "stop", "Notification": "notification", "SessionStart": "session-start"}
AGENTS = {"com.shana.poll": None, "com.shana.nightly": None}
SETTINGS = Path.home() / ".claude" / "settings.json"
LAUNCH = Path.home() / "Library" / "LaunchAgents"


def bin_path() -> str:
    return str(config.path("bin", "shana"))


def _is_ours(entry: dict) -> bool:
    return any("bin/shana hook " in h.get("command", "") for h in entry.get("hooks", []))


def install_hooks(settings: Path = SETTINGS) -> None:
    data = json.loads(settings.read_text()) if settings.exists() else {}
    backup = config.path("data", f"settings.json.bak-{now():%Y%m%d%H%M%S}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        shutil.copy2(settings, backup)
    hooks = data.setdefault("hooks", {})
    for event, arg in HOOKS.items():
        entries = [e for e in hooks.get(event, []) if not _is_ours(e)]
        entries.append({"hooks": [{"type": "command", "command": f"{bin_path()} hook {arg}", "timeout": 20}]})
        hooks[event] = entries
    settings.write_text(json.dumps(data, indent=2) + "\n")


def remove_hooks(settings: Path = SETTINGS) -> None:
    if not settings.exists():
        return
    data = json.loads(settings.read_text())
    hooks = data.get("hooks", {})
    for event in HOOKS:
        kept = [e for e in hooks.get(event, []) if not _is_ours(e)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    settings.write_text(json.dumps(data, indent=2) + "\n")


def _plist(label: str, args: list[str], schedule: str) -> str:
    path_env = ":".join([str(Path.home() / ".local/bin"), str(Path.home() / ".bun/bin"), "/usr/local/bin",
                         "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    log = config.path("data", f"{label}.log")
    argxml = "".join(f"<string>{a}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array>{argxml}</array>
{schedule}
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{path_env}</string></dict>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def install_jobs() -> list[str]:
    hour = int(config.load()["report"].get("hour", 21))
    jobs = {
        "com.shana.poll": _plist("com.shana.poll", [bin_path(), "poll"], "<key>StartInterval</key><integer>180</integer>"),
        "com.shana.nightly": _plist("com.shana.nightly", [bin_path(), "nightly"],
                                    f"<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer>"
                                    "<key>Minute</key><integer>0</integer></dict>"),
    }
    LAUNCH.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    for label, xml in jobs.items():
        p = LAUNCH / f"{label}.plist"
        run(["launchctl", "bootout", f"gui/{uid}", str(p)])
        p.write_text(xml)
        run(["launchctl", "bootstrap", f"gui/{uid}", str(p)])
    return list(jobs)


def remove_jobs() -> None:
    for label in AGENTS:
        p = LAUNCH / f"{label}.plist"
        run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)])
        p.unlink(missing_ok=True)


def install_mcp() -> str:
    from shana.models import claude_bin
    listed = run([claude_bin(), "mcp", "get", "shana"], timeout=60)
    if listed.returncode == 0:
        return "already added"
    cp = run([claude_bin(), "mcp", "add", "--scope", "user", "shana", "--", bin_path(), "mcp"], timeout=60)
    return "added" if cp.returncode == 0 else f"failed: {cp.stderr.strip()[:200]}"


def install() -> dict:
    for d in ("data/tmp", "data/modal-cost", "reports", "issues", "tasks"):
        config.path(*d.split("/")).mkdir(parents=True, exist_ok=True)
    memory.ensure()
    install_hooks()
    return {"hooks": list(HOOKS), "jobs": install_jobs(), "mcp": install_mcp()}


def uninstall() -> None:
    from shana.models import claude_bin
    remove_hooks()
    remove_jobs()
    run([claude_bin(), "mcp", "remove", "--scope", "user", "shana"], timeout=60)


def setup_phone() -> str:
    """Make a private ntfy topic, save it in config.toml and send a test message."""
    topic = f"shana-{secrets.token_hex(10)}"
    cfg = config.path("config.toml")
    text = cfg.read_text() if cfg.exists() else ""
    if "[notify]" in text:
        lines = [ln for ln in text.splitlines() if not ln.startswith("ntfy_topic")]
        i = lines.index("[notify]")
        lines.insert(i + 1, f'ntfy_topic = "{topic}"')
        text = "\n".join(lines) + "\n"
    else:
        text += f'\n[notify]\nntfy_topic = "{topic}"\n'
    cfg.write_text(text)
    notify.send("Shana", "Phone notifications are on 🎉")
    return topic


OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"


def setup_opencode(path: Path = OPENCODE_CONFIG) -> str:
    """Make Shana OpenCode's default agent (memory + tools loaded), cheap models for subagents; keeps other settings."""
    from shana import models
    data = json.loads(path.read_text()) if path.exists() else {}
    tier = lambda t: models.pick(t).replace(":", "/", 1) if not models.pick(t).startswith("claude:") else None  # noqa: E731
    data.setdefault("$schema", "https://opencode.ai/config.json")
    data["default_agent"] = "shana"
    agents = data.setdefault("agent", {})
    agents["shana"] = {"mode": "primary", "description": "Shana — your assistant; delegates work to subagents",
                       "prompt": "{file:" + str(config.path("agents", "shana.md")) + "}",
                       **({"model": tier("mid")} if tier("mid") else {})}
    if tier("cheap"):
        agents.setdefault("general", {})["model"] = tier("cheap")
        data["model"] = tier("cheap")
    core = str(config.path("memory", "core.md"))
    data["instructions"] = [i for i in data.get("instructions", []) if i != core] + [core]
    mcp = data.setdefault("mcp", {})
    mcp.pop("shana", None)
    mcp.setdefault("servers", {})["shana"] = {"type": "local", "command": [bin_path(), "mcp"], "enabled": True}
    key = config.path("data", "deepinfra.key")
    if key.exists():
        data.setdefault("provider", {}).setdefault("deepinfra", {}).setdefault("options", {})["apiKey"] = \
            "{file:" + str(key) + "}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return f"OpenCode: default agent 'shana' set in {path}"
