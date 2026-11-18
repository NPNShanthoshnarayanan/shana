# Shana Part F — Memory design (approved 17 Nov 2026)

## All memory concepts, and what we do with each

| Concept | Simple meaning | Shana example | Use? |
|---|---|---|---|
| Sensory memory | Raw input, seen for a moment | Raw session logs, screenshots | **Skip** — read raw logs only when needed |
| Short-term / working memory | What the model sees right now (the prompt) | Current step + needed files + a few memories | **Use** — keep it small and fresh per step (Part C) |
| Core memory | Tiny part always loaded | Your rules + list of active projects | **Use** — one small file, ≤ ~50 lines |
| Long-term memory | Stored outside the prompt, searched when needed | Everything below | **Use** (umbrella) |
| Episodic memory | What happened, when | "17 Nov: ran 8 experiments, 50% passed, cost $3" | **Use** — session summaries, daily log |
| Semantic memory | Facts and knowledge | "the model is hosted at X", "cheap models are allowed" | **Use** — decisions, project state, checked facts |
| Procedural memory | How to do things | "Start the app with this command", "always simple English" | **Use** — rules, preferences, how-tos, skills |
| Prospective memory | Remember to do something later | "Answer pending: which storage?", "check the build again in 5 min" | **Use** — open items for Part E (missed topics) |
| Shared memory (blackboard) | One place many agents read/write | Task notes file used by all steps/sessions | **Use** — handoff notes (C) + "same work" check (A) |
| Consolidation / reflection | Turn many events into lasting facts/rules | Nightly: day's episodes → new facts/rules | **Use** — nightly job; you review in the report |
| Temporal memory / versioning | Facts have dates; old ones marked outdated | "cost was $0.70 (10 Nov) → $0.40 (17 Nov)" | **Use (simple)** — date on every fact, "replaced by" |
| Forgetting / decay | Old, unused memories fade | Old session summaries archived after 30 days | **Use (simple)** — archive, never hard-delete |
| Retrieval scoring | Pick memories by relevance + recency + importance | Search returns newest, most related first | **Later** — v1: keyword search + date |
| Entity memory | Memory grouped per person/project/thing | One file per project or model | **Use** — inside semantic memory |
| Associative / graph memory | Facts linked like a map | app → uses → model → hosted on → GPU cloud | **Later** — v1 uses `[[links]]` in .md |
| Summary buffer | Squash old chat when it gets long | Auto-compaction | **Avoid** — it caused problem 4; use fresh context + handoff instead |

## Folder layout (v1)

```
memory/
  core.md              # core: your rules + active projects (always loaded, tiny)
  facts/<project>.md   # semantic: decisions, state, checked facts (dated)
  how-to/<topic>.md    # procedural: rules, commands, workflows
  episodes/YYYY-MM-DD.md  # episodic: what each session did today
  open-items.md        # prospective: unanswered questions, half-done tasks, pending decisions
  archive/             # forgotten (not deleted) old items
tasks/<task>/notes.md  # shared/working: handoff notes for one task
```

## How it flows
1. During work: each session/step writes short episodes + open items automatically.
2. Each step loads: core.md + its task notes + top search hits (working memory stays small).
3. Nightly: consolidation turns episodes into facts/how-tos, closes done open items, archives old ones.
4. Nightly report shows new/changed memories → you fix or delete.
5. All tools read/write through one MCP server; direct API models (DeepSeek, Kimi) get memory injected by the orchestrator.

## References
- CoALA (working / episodic / semantic / procedural): https://arxiv.org/abs/2309.02427
- Generative Agents (reflection, recency + importance + relevance): https://arxiv.org/abs/2304.03442
- MemGPT / Letta (core vs archival): https://arxiv.org/abs/2310.08560
