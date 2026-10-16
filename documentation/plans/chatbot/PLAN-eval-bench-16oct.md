# PLAN: EVAL-BENCH, chatbot test bench foundations (step 0 of the 16 Oct scale design)

Status: Build (card answered 16 Oct; S1 provenance red tests). Track: M/L, LEAD pattern. Branch `crew/eval-bench`.

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

## Facts (mapped 16 Oct; file:line relative to `sorento_crm_backend/`)

- Turn trace: `ChatbotTurn` (`chatbot.turns`, `app/models/chatbot_turn.py:42-100`), built by `TurnTrace`
  (`app/services/chatbot/trace.py:103-221`), written by `_close_turn` (`engine.py:1582`). The `understood` record
  (`engine.py:4630-4660`; failure `:4576-4590`) stores `prompt_version` only.
- Parser call: `head/parser.py:1021 parse`, `llm_call.chat(...)` at `:1040-1052`, no reasoning effort sent.
  `ParserConfig` (`:75-81`) holds `system_prompt` = `ai_prompt_registry.render(...)` (`:767`).
- Server sha: `app/main.py:595-623` (`GIT_SHA` env, else git), `/health` at `:673-676`.
- E2E runner sha is the runner checkout (`scripts/chatbot_eval/run.py:80-90`); `prompts.py:33` md5s the DB template,
  not the rendered text; `trace.build_digest` keys at `trace.py:55`.
- Replay seams already in the engine: `dry_run` + `mock_reformulator_output` (`engine.py:1296-1301, 4543-4547`),
  `previous_conversation_state` (`:1352, :4251`), `run_turn(envelope, session_factory=...)` (`:3037`); MCP stub
  pattern `tests/chatbot/test_turn_replay.py:758-827`. Commits inside the turn (`engine.py:1637` and others) need an
  outer-transaction session factory.
- Recorded traces: each `runs/*/traces.jsonl` line is a full `chatbot.turns` row + `case_id`; parse at
  `response.ctx.parse._parser_raw`; tool calls as trace events with `name`, `args`, `envelope`; split only in the
  sibling `results.jsonl`.
- Hop timestamps: `chat_histories.respond_ts` / `sent_at` / `ingest_at` / `delivered_ts`, `chatbot.turns.created_at /
  started_at / finished_at` (`engine.py:1565,1636`); corpus report gives 5.27 s p50 respond.io -> turn created.

## Where each part lives

One lane = one branch = one PR (CLAUDE.md lane rule); deliverables land as separate commits on it.

- **Repo** (versioned, CI-tested with synthetic fixtures only):
  - D1 trace provenance (`engine.py` understood records, `trace.py` digest) + E2E runner report (`scripts/chatbot_eval/run.py`).
  - D2 replay driver `scripts/chatbot_eval/replay.py`: runs inside one worktree's backend, reads trace rows, writes
    one normalised stage record per turn; a CI test replays a synthetic trace through the current engine so an
    engine change that breaks a seam fails CI, not the gate.
  - D3 stats `scripts/chatbot_eval/stats.py` (paired bootstrap, verdict words), pure functions.
- **Crew tooling** (not in git; hidden data never leaves `crew/state/eval`):
  - `bin/replay-gate.sh` (runs the driver per worktree, diffs, `--intended`), parser-gold `run_parser.py` /
    `score.py` provenance + CI wiring, `tooling/compare.py` CI wiring, `weights.json`, `tooling/corpus/`
    (restore recipe + refresh), hop-timing SQL + report, README section.
  - Crew tooling has no git: red-first evidence is the failing pytest output saved with timestamps under
    `crew/state/eval/tooling/tests/evidence/` before the code exists, quoted in crew-notes.

## Slices (order) and roles (LEAD pattern)

| Slice | Content | Tester (red first) | Coder |
|---|---|---|---|
| S1 | D1 provenance (repo) | pytest: AC1.1-1.5 | engine + run.py |
| S2 | D3 stats (repo) + score.py/compare.py wiring (crew) | pytest: AC3.x, AC1.6-1.8 | stats.py + wiring |
| S3 | D2 replay driver (repo) + replay-gate.sh (crew) | pytest: AC2.1-2.2, 2.6-2.7 synthetic; AC2.3-2.5 acceptance run by lead | driver + gate |
| S4 | D5 hop timing (crew report) | lead runs read-only SQL | n/a |
| S5 | D4 weights + restore recipe + refresh (crew) | pytest on weights/refresh with synthetic rows; recipe proof by lead | scripts |

Every subagent brief: never edit env files, never create or copy databases (the lead creates the throwaway DB),
pytest at most `-n 2`, only touched tests.
