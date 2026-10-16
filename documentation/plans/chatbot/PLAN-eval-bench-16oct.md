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

## S2 contract (D3 error bars + D1 scorer refusal)

Repo, `scripts/chatbot_eval/stats.py` (pure, stdlib only, no numpy):

- `paired_compare(base, head, *, n_boot=10000, seed=0, min_reps=3, weights=None)`; `base`/`head` map
  `item_id -> list[bool]` (one bool per rep). Returns a dict: `n_items`, `excluded` (ids in one arm only),
  `reps` (min reps per arm), `base_mean`, `base_ci`, `head_mean`, `head_ci`, `delta`, `delta_ci` (each CI a
  `(lo, hi)` tuple, 95%, percentile bootstrap over items, reps averaged per item first), `verdict` in
  {`better`, `worse`, `within noise`} or `refused` with `reason` `"<n> reps < 3"`. Means are fractions 0..1.
  `weights` (optional) maps `item_id -> per-item weight` (the caller passes stratum share / items in that stratum, as `score.py --weights` does); when given, means and deltas are weighted means; fewer than `min_items` (10) paired items -> `refused`
  (used by S5; tests may leave it None).
- `format_compare(result) -> str`: one short block; `verdict refused: <n> reps < 3` when refused.

Crew tooling wiring (tests under `crew/state/eval/tooling/tests/`, synthetic files in tmp dirs):

- `parser-gold/tooling/score.py --compare BASE_GLOB HEAD_GLOB` reads run files, refuses rows lacking any of
  `prompt_sha256, prompt_version, model, reasoning_effort, code_sha` (exit 2, names the file) unless `--legacy`
  (then the first output line is `LEGACY: not a gate verdict`), refuses files of one arm whose provenance differs
  (exit 2, names the field), grades each row with the existing grade logic, then prints `format_compare`.
- `tooling/compare.py --reps RUN... --ref-reps REF...` does the same over E2E run dirs (pass = label in
  {correct, good_clarify}; provenance from `report.json.provenance`).

## S3 contract (D2 L1 replay)

Repo, `sorento_crm_backend/scripts/chatbot_eval/replay.py`. It imports only the stdlib and `app.*`, so crew runs
the SAME file under each worktree's own venv with that worktree's backend as cwd (`<wt>/venv/bin/python
<runner>/scripts/chatbot_eval/replay.py run ...`); the main worktree does not need to contain it. It asserts
`app.__file__` is under the cwd.

- `run --traces FILE... --out OUT.jsonl [--workers N]`: each input line is a recorded `chatbot.turns` row (+
  `case_id`). For each row, call the real `engine.run_turn(envelope, session_factory=...)` with:
  - `dry_run=True` and `mock_reformulator_output` = recorded `response.ctx.parse._parser_raw`
    (`engine.py:1296-1301, 4543-4547`); recorded session vars (`trace[received].raw.session_vars`) and
    `previous_conversation_state` passed through the existing harness keys where the engine supports them;
  - `MCPRuntimeClient.call_tool` patched: return the recorded envelope of the trace event with the same tool
    name and the same canonical args (volatile keys such as `_diagnostics` ignored); otherwise record a miss and
    return an empty error envelope;
  - any other LLM call (e.g. `lanes/casual.call_clarifier`) patched to fail loudly and recorded as `llm_blocked`;
    no non-localhost network;
  - a session factory that wraps the turn in an outer transaction (inner commits become savepoints) and rolls
    it back, so the DB is never written.
- Output, one line per input row, ids and hashes only (no message or reply text): `case_id`, `turn_id`,
  `skipped` (`no_parse` when the row has no parse), `stages`: `grounded` (normalised entities from the
  `understood`/`routed` facts), `decision` (branch kind, plan kind, lane), `tools` (list of `[name, args_sha]`),
  `reply` (`sha256` of the reply text + `branch_kind`), and `misses` (tool misses, `llm_blocked`). When a tool
  miss occurred, `reply` is omitted for that row.
- `diff BASE.jsonl HEAD.jsonl [--intended FILE]`: matches rows by (`case_id`, `turn_id`); prints `case_id turn_id
  stage` per differing stage (first differing stage per row in stage order grounded, decision, tools, reply),
  then counts per stage and per skip kind; exit 0 when every diff is in `--intended` (lines `case_id stage`),
  else 1.

Crew, `crew/state/eval/bin/replay-gate.sh BASE_WT HEAD_WT [--split dev|hidden|all] [--intended FILE]`: picks
`runs/*/traces.jsonl` rows whose `results.jsonl` split matches, dedupes identical (parse, envelope) inputs, runs
`replay.py run` in both worktrees (2 processes) against `sorento_eval_base`, then `replay.py diff`.

## S5 contract (D4 weights + restore recipe + refresh), crew tooling only

- `crew/state/eval/weights.json` (written by the lead, 16 Oct): gate strata and shares from corpus section 6e,
  growth slices, `min_n` 10. `score.py --compare` and `compare.py --reps` gain `--weights FILE`: headline =
  weighted gate (`paired_compare(..., weights=...)`), then one line per growth slice with n, means, delta CI;
  `stratum missing: <name>` / `stratum thin: <name> n=<n>` warnings.
- `crew/state/eval/tooling/corpus/restore_chat_slice.sh DUMP DB`: refuses (exit 2, one-line reason, nothing
  created) when DUMP is not a `pg_dump -Fc` archive, DB already exists, `PGHOST` is set to anything but
  localhost/127.0.0.1/a socket path, or free space on the data volume minus 3x the dump's listed table-data size
  would fall under `MIN_FREE_GIB` (default 30). Otherwise: `createdb`, `pg_restore -l` filtered to an allowlist
  (schema `chatbot`: turns, chat_ask_facts, chat_ask_items, chat_episode_facts, chat_topics; public:
  chat_histories, conversation_frames, ai_assistant_usage_logs, llm_model_prices, respond_contacts,
  respond_contact_customers, respond_contact_companies, users, sales_agents, customers) incl. their sequences,
  defaults, PKs and indexes, FKs only where both ends are in the list; `pg_restore -L <list> --no-owner
  --no-privileges -d DB`; prints row count per table and `pg_database_size`. Never `createdb -T`, never a full
  restore.
- `crew/state/eval/tooling/corpus/refresh.py --db DB --out DIR [--since DATE]`: read-only connection; writes
  `DIR/<UTC date>/mix.json` (audience, language, position/shape shares as in corpus 6c-6e, top-shape shares) and
  `candidates.jsonl` (one line per candidate: `turn_id`, `contact_respond_id` hash, `signals`, `audience`,
  `language`, `shape`; no message text), dir chmod 700. Signals: clarify / not-found / hand-over branch kinds,
  failed or errored turn, user follow-up within 120 s after a clarify or not-found reply. Pure classifier
  functions (`classify_language`, `classify_shape(position, text, has_code)`, `candidate_signals(turn,
  next_user_msg_gap_s)`) are unit-tested on synthetic rows; reuse the parser-gold builder's language helper
  (`parser-gold/tooling/build_pool.py`) if one exists rather than writing a new one.

## S3b: L1 replay in two tiers (crew decision 16 Oct)

Measured: whole-turn replay 0.8 s/row (40 real rows, 42 s wall, 16 s CPU). Corpus: 10,650 traced rows, 5,584
distinct parses (hidden 4,057, dev 1,504), 1,480 distinct core parses (case, message, domain, intent, entities).

- Tier 1, grounding: `replay.py run --stage grounding` runs only `grounding.ground()` (the f4_replay class) over
  all distinct parses; output `stages.grounded` only. Seconds to about a minute.
- Tier 2, whole turn: the 1,480 core parses, one process per arm, after a profiling pass that memoises
  contact-independent loaders inside the replay process only (both arms alike; no product code change); the gate
  prints coverage when its time box ends.
- `--full` (all distinct parses, whole turn, ~40 min) runs nightly. It is replay only: every model call is
  blocked, so it costs $0.
- The corpus is rebuilt from the restored prod DB (`sorento_prod_20261015`, adds 18 Sep-15 Oct) once crew says
  it is live.

## Scope decision (crew, 16 Oct)

PR #1743 = step-0 foundations (D1 provenance, D2 L1 replay, D3 error bars, CP0 target grader + crew tooling). It
changes nothing the owner sees: crew merges on reviewer READY + green CI, no owner hand test. CP0 (seed items from
the restored prod DB, gold label passes, paraphrase variants, recorded-state runner, baseline runs 1-3, the 30-card
page) is built on a follow-up PR from a new branch stacked on #1743, with the spend posted per step.
