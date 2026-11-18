# Goal (agreed 17 Nov 2026)

Shana: one Jarvis-like assistant that helps finish AI tasks cheaply, keeps track of every session, and gets better as it is used.
Any model is allowed (DeepSeek, Kimi, Gemini, OpenRouter, Claude…).

**Style:** quiet. It sends notifications when something needs the user, and answers when the user asks. No voice in v1.

## Parts

| Part | What it does | Problems |
|---|---|---|
| **A Monitor** | Watches every session in every AI tool: running / waiting for you / finished / stuck / two sessions on the same thing. Notifies Mac + phone. Only suggests, never acts. Does NOT watch length or usage limits. | 3 |
| **B Tracker** | Records problems with this tool — user reports in plain words + auto-detects from logs (errors, "this is wrong", repeats). Talks only when asked. Labels bug/feature → fix plan → user approves → it fixes and tests. | improves the tool; picks up 6 |
| **C Orchestrator** | Strong model plans once → small steps → each step to the cheapest good-enough model, fresh small context, handoff by a notes file → cheap checks (tests/scripts) → retry with a stronger model on failure → budget stop. | 1, 2, 4, 7 |
| **D Proof checker** | Checks numbers/claims in AI answers against real data. Checks cost. | 5 |
| **F Memory layer** | One memory shared by ALL tools and models. Remembers: decisions & project state, user rules & preferences, a short summary of what each session did, checked facts & results (dated). Saves automatically; user reviews new memories in the nightly report and can fix/delete. Storage: .md files + search index, shared via MCP (approved; see memory-design.md). | 2, 4, 5; feeds A, C, D, E |
| **E Missed-topic reminder** | Across all sessions. Quick ping only for important open items (unanswered question, half-done task, pending decision) after ~1 hour untouched. Full list in the nightly report. User can say "drop it". | missed topics |

## Nightly report (once a day)
- Cost + tokens: Claude, other AI APIs, Modal GPU.
- Unfinished topics (from E).
- New issues (from B).
- New memories saved today (from F) — to review, fix or delete.

## How we know it works
- Cost per task much lower than Claude-only.
- User never needs to remember which session does what.
- No session grows long enough to be auto-summarized.
- Reported problems get a plan, then a tested fix after approval.

## Not in v1
- No web UI beyond notifications. No own model training. No voice.
