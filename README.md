# Shana

A quiet, Jarvis-like helper for people who run many AI coding sessions. It watches your sessions, pings you only
when something needs you, sends one cost report every night, remembers what matters across tools, and can do a
task step by step on the cheapest model that passes a check.

Plain Python 3.11+, no extra libraries. Works on macOS.

## What it does

| Part | What you get | Command |
|---|---|---|
| A Monitor | Mac/phone ping when a session is **waiting for you**, **finished** (turns over 1 min), **stuck** (15 min no progress) or **two sessions do the same work** | `shana status` |
| B Tracker | Problems with Shana itself — yours or found in its error log — as `issues/*.md`; fixed only after you approve | `shana issue add\|list\|plan\|approve\|fix` |
| C Orchestrator | Plan once, run each step fresh on the cheapest model, check it, retry on a stronger model, stop at the budget | `shana run "task" --cwd DIR --check "pytest -q"` |
| D Proof checker | Nightly cost + tokens (Claude via ccusage, Shana's own calls, Modal), cross-checked against Shana's own count; numbers in an answer checked against sources | `shana report`, `shana check answer.txt src/` |
| E Missed topics | Ping after 1 h when a question waits on you or your question got no reply; full list every night | `shana open`, `shana done N`, `shana drop N` |
| F Memory | `.md` files + search index, shared with every AI tool through MCP; core rules injected into every new Claude Code session | `shana memory search\|add` |

The nightly report (21:00) lands as a notification and as `reports/latest.html`.

## Install

```bash
bin/shana install        # Claude Code hooks, MCP server "shana", two launchd jobs
bin/shana setup-phone    # optional: phone pings through ntfy (prints the private topic to subscribe to)
                         # Android: turn on "Instant delivery" for the topic in the ntfy app, or pings may not arrive
bin/shana uninstall      # removes hooks, MCP server and jobs; keeps your memory and reports
```

Other AI tools (Codex, Gemini CLI, Cursor…) can use the same memory by adding the MCP server
`<path>/bin/shana mcp` in their own settings.

## Ask Shana from any chat

In a session with the MCP server, just say: *"shana, what's running?"*, *"what did I leave open?"*,
*"remember that …"*, *"search memory for …"*, *"record a Shana issue: …"*.

## OpenCode (see every subtask)

`bin/shana opencode-setup` makes **Shana the default agent** in OpenCode (terminal or desktop app): your core memory
and Shana's tools are loaded, the chat runs on the `mid` model, subagents on the `cheap` model, and every subagent is
a child session you can open. Hard steps go to `shana_worker`, which runs Claude through your Claude subscription in
the background (OpenCode itself can only use Claude with an API key). Shana adds about 1,300 tokens per request.

## Honest by design

- **Real model names.** Shana's OpenCode plugin (`opencode-plugin/shana`) tells every model call which model it
  really is, taken from the request itself, so replies never claim the wrong model. Workers are tagged by code.
  `shana_models_used` reads the real model of every session and subagent from OpenCode's database.
- **Receipts, not words.** Tools that change something return a `RECEIPT:` line (or `NOT DONE:`); Shana must quote
  it. Real actions exist for what it used to only promise: `shana_set_model`, `shana_issue_close`, `shana_jobs`.
- **Live facts.** The plugin adds a short real-data block (latest jobs, DeepInfra spend, items waiting) to each
  request, so even small models answer from facts.
- **Proven fixes.** Each planned issue gets a check that fails now and must pass after the fix. Only then is it
  `fixed` (and committed); otherwise `approved` (still broken) or `needs-check` (you try it). You get a ping either way.
- **Budget.** `shana_budget` counts real DeepInfra spend this month (OpenCode + Shana) against `monthly_cap_usd`.
  At 80% OpenCode steps down to the cheapest model; at the cap Shana's own work moves to Claude.
- **Bad answers found automatically.** Each night, your corrections ("you are wrong", "don't assume") and replies
  that named the wrong model become issues by themselves.

## Self-improving

Shana's own errors, failed model checks (a model failing ≥30% of its steps in a day) and complaints it finds in your
sessions become issues. Each night new issues get a fix plan by themselves and you get a ping; a fix runs only after
you approve (`shana issue approve N` / `shana issue fix N`, or ask Shana), and is kept only if the tests pass.

## Cheaper models

One DeepInfra key gives DeepSeek, Kimi, Qwen, GLM and more. Put keys in the environment or in `data/keys.toml` (never committed):

```toml
DEEPINFRA_API_KEY = "..."   # or DEEPSEEK_API_KEY / MOONSHOT_API_KEY
```

Then set prices (USD per 1M tokens) in `config.toml` so costs are counted — see `config.example.toml`.
Without keys, everything uses Claude (Haiku for cheap, Sonnet for mid, Opus for strong).

## Modal cost

Shana never calls Modal. Whatever owns Modal writes `data/modal-cost/<YYYY-MM-DD>.json` as `{"cost_usd": 1.23}`
(for example from `modal billing report --json`) and the nightly report picks it up.

## Folders

`shana/` code · `tests/` tests (`python3 -m unittest discover -s tests`) · `docs/` the design · `agents/` the OpenCode agent prompt ·
`memory/` your memory (its own git repo) · `issues/` · `reports/` · `tasks/` orchestrator notes · `data/` state and logs.
Personal folders are git-ignored, so the code can be published without your data.
