# Open-source tools per part (checked 17 Nov 2026)

Verdict: **no single tool does all of this.** Reuse ready pieces, build a thin "glue" layer (that glue is what we could open source).

Important fit note: the user works in the **Claude desktop app**. Terminal session managers (Agent Deck, CCManager…) only see sessions started inside them (tmux/terminal). Tools that read log files (ccusage, our own scripts) and Claude Code hooks work with desktop-app sessions too (hooks: to be tested).

## A Monitor
| Tool | Good | Gap |
|---|---|---|
| Agent Deck (Go, tmux) | Running / Waiting / Idle for Claude Code, Gemini CLI, Codex, OpenCode | Needs tmux; sessions must start inside it |
| CCManager (Node, no tmux) | Busy / waiting / idle; Claude Code, Gemini, Codex, Cursor, Copilot, Cline, OpenCode, Kimi CLI | Same: sessions must start inside it |
| Agent of Empires | TUI or web | Same |
| Claude Code hooks + **ntfy** | Notification/Stop hooks → push to phone; ntfy is open source, self-hostable | Claude Code only; other tools need their own hook |
| — | — | "Two sessions on the same thing": nothing ready → build |

## B Tracker
| Tool | Good | Gap |
|---|---|---|
| **Backlog.md** (MIT) | Tasks = plain .md files in git, kanban, built-in MCP for Claude Code/Codex | No auto-detect from logs |
| Beads (MIT) | Git-backed, dependencies, `bd ready` lists unblocked work | Same |
| — | — | Auto-detect problems from logs → build (script like the one used on 17 Nov) |

## C Orchestrator
| Tool | Good | Gap |
|---|---|---|
| claude-code-router | Keeps Claude Code, routes by kind: default / think / background / longContext to DeepSeek, Kimi… | Some sources mention EOL notices — check repo status; no check-and-escalate |
| claude-code-mux | Routing + provider failover | Small project |
| **LiteLLM** proxy | One gateway for all providers, per-day budgets, spend per model/key | Routing only, no planning |
| Panda Code | Lead splits work, failed checks escalate to stronger model (max 2 retries) | New; results only from a blog |
| Goose | Had lead/worker; now replaced by Planning Mode | Lost the escalation feature |
| — | — | plan → cheap step → check → escalate: no mature tool → build thin layer |

## D Proof checker + nightly cost
| Tool | Good | Gap |
|---|---|---|
| **ccusage** (MIT) | `ccusage daily`: tokens + cost per day for Claude Code, Codex, OpenCode; local only | Cost is an estimate |
| LiteLLM `/user/daily/activity` | Per-day spend for DeepSeek/Kimi/OpenRouter calls through the proxy | Only calls that go through LiteLLM |
| `modal billing report --json` | Modal GPU cost per day | Shown before credits |
| Langfuse (already used by the team) | Traces, cost, LLM-as-judge evals | — |
| — | — | Checking answers against real data: nothing ready → build |

## E Missed-topic reminder
| Tool | Good | Gap |
|---|---|---|
| claude-mem | Memory across Claude Code sessions, searchable | Not built for unfinished tasks; Claude Code only |
| — | — | Nightly cheap-model pass over all transcripts → list open items → build |

## F Memory layer — how others store memory
| Tool | How it stores | Good | Gap |
|---|---|---|---|
| Claude Code (today) | Plain .md files (CLAUDE.md + memory folder), loaded at start | Readable, editable | Claude only; whole index loaded every time |
| Mem0 | AI pulls short facts from chats → vector DB (search by meaning), optional graph | Quick, cheap | Hard to read/fix by hand |
| Letta (was MemGPT) | Like RAM + disk: small core memory always in the prompt, rest in archival storage the agent searches | Agent manages itself | You must run its runtime |
| Zep / Graphiti | Knowledge graph with time — old facts marked outdated | Good when facts change | Heavy; graph DB |
| **Basic Memory** (AGPL) | .md files = source of truth + SQLite search index, via MCP | Claude Code, Codex, Cursor, Gemini, ChatGPT | AGPL; cross-device sync is its paid cloud |
| open-memex (Apache-2.0) | .md files + rebuildable SQLite FTS5 index; project memory shared through git | Claude Code, Cursor, Codex | No auto sync between machines |

Recommendation: **.md files + search index, shared via MCP** — readable (fits "auto-save, you review"), git keeps history/undo, any tool with MCP can use it, search loads only the needed bits (saves tokens). Date every fact so old facts can be marked outdated.

## Shana "front door" (notifications + ask anything)
| Tool | Good | Gap |
|---|---|---|
| Hermes Agent (Nous Research) | Self-hosted, built-in cron, Telegram etc., learns skills | Big surface; security care needed |
| OpenClaw | ~two dozen chat channels, cron | Called "a security nightmare" by Cisco; wiring by hand |
| Simple: cron + scripts + ntfy | Small, safe, easy to change | No chat — ask via Claude Code |

## Recommendation
Build our own thin "Shana" glue; reuse: ntfy (notify), ccusage + `modal billing report` + LiteLLM (cost), Backlog.md (tracker), LiteLLM + claude-code-router (cheap models). Build ourselves: nightly report, missed-topic finder, log-based problem finder, "same work" detector, check-and-escalate loop.
Order: D nightly cost report + A notifications first (quick wins) → E → B → C.

## Sources
- Session managers: https://dev.to/asheshgoplani/how-to-manage-multiple-claude-code-gemini-and-codex-sessions-in-one-terminal-1dci , https://cdn.jsdelivr.net/npm/ccmanager@4.4.3/README.md , https://codex.danielvaughan.com/2026/05/05/codex-cli-session-managers-ccmanager-agent-deck-parallel-agent-orchestration/
- claude-code-router: https://www.claudelog.com/addons/claude-code-router , https://docs.rs/crate/claude-code-mux/0.5.3/source/README.md
- Escalation: https://www.mindstudio.ai/blog/panda-router-ai-model-orchestration/ , https://goose-docs.ai/blog/2025/06/16/multi-model-in-goose
- ccusage: https://www.mintlify.com/ryoppippi/ccusage/features/daily-reports
- Modal: https://modal.com/docs/cli/latest/billing
- LiteLLM: https://docs.litellm.ai/docs/proxy/provider_budget_routing , https://docs.litellm.ai/docs/proxy/cost_tracking
- Backlog.md: https://github.com/MrLesk/Backlog.md ; Beads: https://github.com/steveyegge/beads
- claude-mem: https://betterstack.com/community/guides/ai/claude-mem/
- ntfy hooks: https://pypi.org/project/claude-code-ntfy-notification-hook/ , https://dev.to/felipeelias/perfect-claude-code-notifications-setup-with-tailscale-and-ntfy-1ii1
- Memory layers: https://dev.to/plur9/mem0-vs-letta-vs-zep-which-should-you-use-for-agent-memory-1n8m , https://atlan.com/know/best-ai-agent-memory-frameworks-2026/ , https://docs.basicmemory.com/technical/technical-information , https://unpkg.com/open-memex@0.8.1/README.md
- Hermes vs OpenClaw: https://composio.dev/blog/openclaw-vs-hermes-agent , https://parallel.ai/articles/openclaw-vs-nousresearch-hermes
