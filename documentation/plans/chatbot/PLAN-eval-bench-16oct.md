# PLAN: EVAL-BENCH, chatbot test bench foundations (step 0 of the 16 Oct scale design)

Status: Plan (behaviour card to crew before any code). Track: M/L, LEAD pattern. Branch `crew/eval-bench`.

Owner approved the design 16 Oct (D1 step order, D4 prompts as code, D5 masked export, D9 weighted gates).
Source analyses (crew-only, not in repo): `crew/state/reports/CHATBOT-SCALE-D-HARNESS-16oct.md` (harness),
`CHATBOT-SCALE-C-CORPUS-16oct.md` (corpus), `CHATBOT-SCALE-DESIGN-16oct.html` (design).

## Why

Harness report section 2: identical reruns flip 5-6% of parser-gold items, E2E noise (178-180/214) is as big as the
effects measured, and runs carry the RUNNER's sha, not the code under test (`scripts/chatbot_eval/run.py:80-90`), and no
rendered-prompt hash (`ai_prompt_registry.render` fills variables from the live DB). A free deterministic signal
(8,909 recorded parser outputs) goes unused. Step 0 makes the bench trustworthy before steps 1-7 change the bot.

## Deliverables (each its own small PR or crew-tooling change; red-first where code)

| # | Deliverable | Lives in | Gate |
|---|---|---|---|
| 1 | Provenance: every eval run record and every chatbot turn trace carries rendered-prompt sha256, prompt version, model, reasoning effort, code sha; scorer rejects a run without it | repo (turn trace + `scripts/chatbot_eval`) + crew scorer | pytest red-first + kill |
| 2 | L1 replay gate ($0): recorded parser outputs through PR code vs main code (grounding, resolve, plan, compose), per-case diffs (ids only), < 5 min | crew tooling `crew/state/eval/tooling/replay/`, doc in `crew/state/eval/README.md` | tool tests red-first + a run on F4 (known diffs cand-0174/0108/0127) |
| 3 | Repeated runs + error bars: parser-gold scorer and hidden E2E summary report mean and paired 95% CI over >= 3 reps | crew tooling (`parser-gold/tooling/score.py`, `tooling/compare.py`) | tool tests red-first; re-score existing reps ($0) |
| 4 | Real-mix weighting + restore recipe: weighted gate set + growth slices (dealer, Malay/Chinese, heavy asks); recipe loading ONLY chat history / turn tables + minimal lookups from the owner's prod backup into a small local DB, refreshing corpus + candidate regression items | crew tooling + recipe doc | recipe dry-run on a dev dump (no prod access) |
| 5 | Hop timing: webhook -> n8n -> CRM -> reply per hop on real data | crew report | report with queries and numbers |

## Rules

- Held-out items, traces and any prod-derived data stay crew-only; never sent to cloud lanes; reports carry ids only.
- No LLM spend beyond a $3 validation without asking crew. No prod access by this lane.
- Never a full snapshot copy (`createdb -T` / full restore): table-filtered `pg_restore` only.

## Open facts (being mapped; filled before the behaviour card)

- Turn-trace persistence point and which provenance fields it already stores. UNVERIFIED.
- Replay entry points past grounding (resolve / plan / compose) and their DB/MCP needs. UNVERIFIED.
- Timestamp columns for respond.io message time vs CRM received/replied. UNVERIFIED.

## Slices (order)

1. D1 provenance (repo PR, S-sized inside the lane): red tests, then code.
2. D3 CI scorer (crew tooling, $0 on existing reps).
3. D2 L1 replay (crew tooling).
4. D5 hop timing (report).
5. D4 weighting + restore recipe (crew tooling + doc).
