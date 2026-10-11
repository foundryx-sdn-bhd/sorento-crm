# CONTRACT: chatbot eval runner (S1 + S2)

Package `sorento_crm_backend/scripts/chatbot_eval/` (stdlib + httpx only, no app imports at module
import time). Tests: `sorento_crm_backend/tests/chatbot_eval/` - pure unit tests, no DB, no network,
fake engine, fake cases generated in tmp dirs (never real data).

## cases.py
- `Expect` dataclass: `outcome` (answer|clarify|not_supported|handover), `acceptable_alternates:
  list[str]` (other outcomes that also pass), `must: list[dict]` (each one key of `code|number|date|text`),
  `options: list[str]`, `must_not: list[str]`, `judge_note: str | None`.
- `Case` dataclass: `id, split (dev|hidden), topic_code, language, contact: dict, history: list[dict]
  ({role: user|bot, text}), message: dict ({text, reply_to?}), variants: list[dict] ({kind, text}), expect: Expect`.
- `load_cases(data_dir, split=None) -> LoadResult(cases, invalid)`; one `*.json` per case, recursive.
  `invalid` = list of `(path, reason)` for files missing id/split/message.text/expect.outcome or with
  an unknown outcome/split. `split` filters.
- Raises `DataDirInRepoError` when `data_dir` resolves inside the git work tree that contains the package.

## engine.py
- `Outcome` dataclass: `reply_text: str, branch_kind: str | None, dry_run: bool, tool_errors: list[str],
  tokens: dict (prompt, cached, output; ints, default 0), latency_ms: int, state: dict | None, raw: dict`.
- `Engine` Protocol: `turn(case, text, state, run_id) -> Outcome`.
- `HttpTurnEngine(base_url, api_key, timeout)`: `build_envelope(case, text, state, run_id) -> dict` per
  `app/services/chatbot/contracts.py:727-799` with `is_test: True`, `test_run_id: run_id`,
  `ingress: "console"`, contact id from `case.contact["respond_id"]` + `case.contact.get("custom_fields")`,
  message text at the contracts path, `replyTo` when `case.message.reply_to` and the text is the final
  message, `previous_conversation_state: state` when state is not None. `turn` posts to
  `/api/v1/external/chat/turn` with `X-API-Key`, maps `reply.text`, `branch_kind`, `session_patch` -> state,
  `actions[*].dry_run` all true -> dry_run.
- `load_engine(spec, **kw)`: `"http"` -> HttpTurnEngine; `"pkg.mod:Class"` -> import and instantiate.

## runner.py
- `run_case(engine, case, text, run_id) -> CaseResult(case_id, split, topic_code, language, variant_kind
  (None for original), verdict, outcome, latency_ms, tokens)`: replays each history `user` turn in order
  through the engine, carrying `outcome.state` forward, then runs `text`; only the last turn is graded.
  previous bot reply for the stuck check = last reply produced during replay, else the last history bot text.
- Any Outcome with `dry_run False` raises `NotDryRunError` (aborts the whole run).
- `run_all(engine, cases, *, variants="none"|"one"|"all", max_parallel=1, budget=None, run_id, seed=0,
  judge=None, judge_calibrated=False) -> RunResult(results, stopped_reason: None|"budget", wall_s)`.
  `one` = one variant per case chosen by `random.Random(seed)`. Budget checked before each turn; once
  exceeded no further turn starts and `stopped_reason="budget"`.

## grade.py
- `grade(expect, outcome, previous_bot_reply) -> Verdict(label, reasons, needs_judge)`, labels
  `correct|good_clarify|wrong|stuck|error`. Order:
  1. `tool_errors` non-empty or blank reply -> `error`.
  2. reply equals previous bot reply (whitespace-normalised, case-insensitive) -> `stuck`.
  3. `must_not`: `"uuid"` = a UUID in the reply; `"stale_subject:<X>"` = X in reply (case-insensitive);
     any other string = case-insensitive substring. Any hit -> `wrong`.
  4. Primary outcome check, then each alternate: `clarify` passes when every `options` item is in the
     reply (case-insensitive) -> `good_clarify`; `answer|not_supported|handover` pass when every `must`
     item matches -> `correct`. `must` matching: `code`/`text` case-insensitive substring; `number`
     matches as a whole number token allowing thousands separators (`1630` matches `1,630`, not `16300`);
     `date` (ISO `YYYY-MM-DD`) matches `YYYY-MM-DD`, `DD/MM/YYYY` or `D/M/YYYY` in the reply.
  5. Nothing passes -> `wrong`.
  `needs_judge = label in (correct, good_clarify) and judge_note`.

## judge.py
- `Judge` Protocol: `score(case, outcome) -> JudgeResult(passed, note)`.
- `calibrate(judge, items) -> float` agreement over `(case, outcome, human_passed)`.
- `apply_judge(verdict, result, calibrated)`: uncalibrated -> verdict unchanged, reason
  `judge_uncalibrated` added; calibrated + failed -> `wrong`; `CALIBRATION_MIN = 0.9`.

## budget.py
- `PRICES` per 1M tokens (https://developers.openai.com/api/docs/pricing, 10 Oct 2026):
  `gpt-5.4-mini` 0.75 / 0.075 cached / 4.50; `gpt-4.1-mini` 0.40 / 0.10 / 1.60.
- `cost_usd(tokens, model)`: `(prompt - cached) * in + cached * cached_in + output * out`, / 1e6.
- `Budget(usd_cap, model)`: `add(tokens)`, `spent`, `exceeded`.

## report.py
- `build_report(run, baseline=None) -> dict` keys: `by_topic`, `by_language` ({key: {label: count}}),
  `underfit` (pass rate on originals; pass = correct|good_clarify), `gap_dev_hidden` (dev pass rate
  minus hidden; None if a split absent), `gap_original_variant`, `tokens`, `est_usd`, `latency_p50_ms`,
  `latency_p95_ms`, `wall_s`, `stopped_reason`, `delta` (when baseline: `total` and `by_topic` pass-rate deltas).
- `render_summary(report, split) -> str` markdown; never contains message text or case ids.

## run.py
- `main(argv) -> int`: `--data-dir` (default env `EVAL_DATA_DIR`), `--split dev|hidden|all`,
  `--variants none|one|all`, `--engine`, `--base-url`, `--max-parallel`, `--budget-usd`, `--baseline`,
  `--out` (writes report JSON + per-case JSONL), `--seed`. Exit 2 on invalid cases, 3 on budget stop, 0 otherwise.

## trace.py (S1b)
Measured on `sorento_ai_automation_20261006` `chatbot.turns.trace` (a JSON array of stage entries):
- tokens: stage `understood` `facts.prompt_tokens`, `facts.cached_tokens`, `facts.completion_tokens`
  (sum over every entry carrying them).
- tool errors: (a) any entry with `status == "failed"` and a non-null `error` -> `"<stage>: <error>"`;
  (b) stage `looked_up` `raw.envelopes[*].error` non-null -> `"<tool>: <error>"`; (c) `raw.envelopes[*].lane_text`
  starting with `Error executing tool` -> `"<tool>: <lane_text>"` (engine-emitted marker, e.g. INVALID_UUID, 422).
- `parse_trace(trace: list) -> (tokens: dict, tool_errors: list[str])`, pure.
- `DbTraceReader(dsn)`: `fetch(turn_id) -> list | None` (`SELECT trace FROM chatbot.turns WHERE id = %s`), read-only
  connection (`SET default_transaction_read_only = on`), psycopg2.
- `HttpTurnEngine(..., trace_reader=None)`: when a reader is set and the response has `turn_id`, tokens and
  tool_errors come from `parse_trace(reader.fetch(turn_id))`, merged with any in the body. CLI: env
  `EVAL_TRACE_DB_URL` builds a DbTraceReader.

## Fix round 1 (reviewer + security-reviewer, 10 Oct)
- First turn sends `previous_conversation_state: {}`; later turns the carried state. Per-case
  `test_run_id = f"{run_id}:{case.id}"` (variants add `:{kind}`).
- Engine memory is not scoped by test run (`app/services/chatbot/engine.py:3736-3770` reads closed is_test
  frames and is_test turns of the same contact), so: runner calls `engine.reset_contact(case)` before each
  case when the engine has it; cases sharing a respond_id never run concurrently. `trace.DbContactReset(dsn)`
  deletes that contact's is_test `chatbot.turns` / conversation frames and refuses any DB whose name does
  not start with `sorento_eval_` (private eval clone only). `HttpTurnEngine.reset_contact` uses it when
  `EVAL_RESET_DB_URL` is set.
- `--budget-usd` with `--engine http` and no `EVAL_TRACE_DB_URL` -> exit 2.
- Transport errors after retries -> Outcome `tool_errors ["transport: <Exc>"]`; 502/504 retried.
- NotDryRunError aborts remaining cases; message omits the case id for hidden cases.
- Judge runs only when calibrated: `<data-dir>/calibration/*.json`, agreement >= 0.9, else not called and
  `judge_status: uncalibrated`; judge tokens count toward the budget.
- Case with no deterministic criteria -> `needs_judge`; report counts `unverified` when no calibrated judge ran.
- Number match: not inside letters/digits (`AP4844` no), decimals `.00` and units allowed.
- Delegated turn (reply null) -> error, reason `delegated`.
- `--out` inside the git work tree -> exit 2.
- Data egress: `--judge` sends case message + reply to the judge provider; off by default.

## Round 2 (labelling finding, 10 Oct)
- New `expect.outcome` value `silent` (contact not allowed AI answers: the right behaviour is no reply).
  Grade: tool errors -> `error`; blank or delegated reply -> `correct`; any non-blank reply -> `wrong`,
  reason `reply_when_silent_expected`. For every other outcome a blank or delegated reply stays `error`.
  `load_cases` accepts `silent`.

## Round 3 (prompt pin, 10 Oct)
- `HttpTurnEngine(..., prompt_version=None)`; when set, every envelope carries
  `prompt_overrides: {"chatbot_semantic_parser": str(prompt_version)}` (the dry-run-only harness key,
  `app/services/chatbot/engine.py:1218-1240`, same as `scripts/chatbot_console_check.py:201-203`).
  CLI `--prompt-version`. The report records `prompt_version` (pinned value or "label") and `engine`.
- Report also records `run_id`, `split`, `variants`, `cases`, `started_at` so a baseline JSON says what it measured.

## Round 4 (smoke-run finding, 10 Oct)
- Found on the first live run: case clarify (options X, Y) with alternate `answer` (no must) graded
  `correct` for a "Couldn't find" reply, because a criteria-less kind passes anything.
- Rule: an outcome kind with no deterministic criteria (clarify without options; answer / not_supported /
  handover without must) is skipped while any other kind of the same case has criteria. Only when no kind
  has criteria does the case pass deterministically (`needs_judge`, counted `unverified`).
- Report adds `strict_pass_rate`: passes that do not need the judge, over all results (unverified passes
  count as not passed), and `strict_underfit` on originals. Summary prints both rates.

## Round 5 (baseline crash, 10 Oct)
- First full run died: `DbContactReset` / `DbTraceReader` share one psycopg2 connection across the
  ThreadPoolExecutor workers ("the connection cannot be re-entered recursively"), and one exception lost
  the whole run with no report.
- Both DB helpers are safe under concurrent calls from several threads (one connection per thread, or a
  lock around each use; still lazy, still read-only for the reader).
- An unexpected exception inside one case (not NotDryRunError) becomes that case's result: label `error`,
  reason `harness: <ExcName>`; the run continues.
- Whatever stops a run (NotDryRunError, budget, Ctrl-C), `--out` still gets the report + results of the cases
  that finished, with `stopped_reason` set (`not_dry_run`, `budget`, `interrupted`).

## Round 6 (second review, 10 Oct)
- Round 4 rule narrowed: only criteria-less ALTERNATES are skipped while another kind has criteria. A
  criteria-less PRIMARY still passes deterministically with `needs_judge` (counted unverified, not strict).
- Report: `grader_version` (int, bump on any grading-rule change; this round = 6) and `delta.strict_total`
  next to `delta.total`; a baseline with a different grader_version gives `delta.comparable: false`.
- `results.jsonl` stores everything needed to grade again: `reply_text`, `branch_kind`, `delegated`,
  `tool_errors`, `previous_bot_reply`, plus the existing fields.
- `--regrade RUN_DIR`: no engine calls; re-loads the cases from `--data-dir`, re-grades every stored result
  with the current grader + labels, writes a new report (to `--out`). Reason: label corrections (owner spot
  check) and grader fixes must not cost a new paid run. Rows whose case id is gone are dropped and counted.

## Round 7 (baseline findings, 10 Oct)
- `message.reply_to` may be a plain string (the quoted text) or `{id, text}`; a string becomes
  `replyTo: {"id": "", "text": <string>}`. (3 real cases crashed with AttributeError.)
- `looked_up` `raw.envelopes[*].error` is the LANE's own verdict when the envelope has no `tool`
  (`app/services/chatbot/turn_runtime.py:5099-5101`: "the error text is the answer"), e.g. "purchase_cost needs
  the purchase_orders.cost grant", "the described set qualifies nothing". Such an entry is NOT a tool error;
  it is kept on the result as `lane_notes` and the reply is graded by the rubric. It IS a tool error when the
  envelope has a `tool`, or the text contains `returned HTTP` (a real API failure, e.g. sales-report 422).
  Failed stages and the `Error executing tool` marker stay tool errors. GRADER_VERSION 7.
- `--regrade` of a run made before round 7 re-applies the rule to stored `tool_errors`: an entry
  `"None: <text>"` (no tool) without `returned HTTP` moves to `lane_notes`.

## Round 8 (regrade metadata, 10 Oct)
- A regraded report keeps the source run's `prompt_version`, `engine`, `run_id`, `started_at`, `wall_s` and
  `variants` from `RUN_DIR/report.json` (when present) and adds `regraded_from` (the RUN_DIR path) and
  `regraded_at`. `split` is the regrade's `--split`.

## Round 9 (Claude judge, 11 Oct)
- `AnthropicJudge` default model `claude-sonnet-5-5` (env `EVAL_JUDGE_MODEL` overrides); key from env
  `ANTHROPIC_API_KEY` only (the crew gate script loads it from the crew secrets file at runtime; never in the repo,
  never printed). Token mapping from the Messages API usage: prompt = input_tokens + cache_read_input_tokens,
  cached = cache_read_input_tokens, output = output_tokens.
- `PRICES["claude-sonnet-5-5"] = (2.00, 0.10, 10.00)` per 1M tokens
  (https://platform.claude.com/docs/en/about-claude/pricing, read 11 Oct 2026).
- `--judge-budget-usd` (default 2.0): the judge has its own Budget priced on the judge model; calibration and
  scoring calls are charged to it (no longer to the chatbot budget). Before each judge call, if it is exceeded
  the judge is not called for the rest of the run (verdicts stay deterministic, those cases count as unverified)
  and the report says `judge_stopped: "budget"`. The run itself continues. Report adds `judge_usd`.
- The >= 0.9 calibration rule is unchanged; with no calibration items the judge is never called.
- Round 9b: a requested judge whose model has no entry in `PRICES` -> exit 2 naming the model, before the
  engine is built or the judge is called (a cap priced on the wrong model is not a cap).

## Round 10 (crew analysis of the hidden baseline, 11 Oct)
- Trace digest: `DbContactReset` deletes each case's turns, so a failure could not be traced to a stage. For
  every engine turn the HttpTurnEngine keeps a digest from the trace it already reads, before the next reset:
  `parser` (stage `understood` facts: domain, intent, entities, prompt_version), `resolved` (stage `looked_up`
  `raw.envelopes[*]`: tool, entities, miss, unresolved, error), `tools` (trace entries with a `tool` key: tool +
  args), `branch_kind`, `reply_text`. `Outcome.trace_digest` holds it; each results row carries `trace_digest`
  (final turn) and `replay_digests` (history turns, in order). Missing stages give empty fields, never an error.
- Stuck: the previous reply compared is already the bot's own previous turn in this run (verified on cand-0068,
  0256, 0258, 0306: the replayed history re-asked the same thing and the bot correctly answered the same way).
  Fix: the rubric is checked first; `stuck` only replaces a failing verdict (wrong) when the reply equals the
  previous one. A repeat that meets the rubric is correct. Tool errors / blank stay first. GRADER_VERSION 8.

## Round 11 (prompt pin resolves or fails, 11 Oct; follow-up PR)
- Bug (found by the prompt-diet lane): the engine renders a pinned prompt only when `prompt_overrides` holds the
  `ai_prompt_versions.id`; anything that matches no row (e.g. `"50"`) silently falls back to the code prompt
  (`app/services/ai_prompt_registry.py:1393-1407`).
- `--prompt-version X` accepts the version NUMBER or the row UUID. It is resolved before the engine is built,
  read-only, via `EVAL_TRACE_DB_URL`: `ai_prompt_versions` where `name = 'chatbot_semantic_parser'` and
  (`id = X` or `version = X`). No DB URL, or no matching row -> exit 2 with a message naming X. The envelope pins
  the resolved UUID.
- Without `--prompt-version`, the run resolves the `production` label (`ai_prompt_labels.label = 'production'`) the
  same way when `EVAL_TRACE_DB_URL` is set (no DB URL: prompt recorded as unknown, run continues).
- `report.json` gains `prompt: {id, version, template_md5, source: "pin" | "label" | "unknown"}`; `prompt_version` stays.
- Evidence from the traces: `prompt_versions_seen` = count of the parser's `prompt_version` across final-turn digests.
  A pinned or label-resolved run where any seen version differs from the resolved version sets `prompt_mismatch:
  true`, prints it on stderr and exits 5 after writing the report.
- Round 11 review: a backend that falls back to the code prompt writes `facts.prompt_version` None
  (`app/services/chatbot/engine.py:4470`, registry `_fallback`). When the prompt was resolved (source pin or label),
  a final-turn digest whose `parser` stage is present but has `prompt_version` None counts as `"none"` in
  `prompt_versions_seen` and is a mismatch (exit 5). A digest with no parser stage is skipped (no false alarm).
- A DB error while resolving (psycopg2 error, driver missing) is handled like no match: exit 2 when pinned; otherwise a
  stderr warning and source `unknown`.
- Exit precedence: a stop code (3 budget, 4 not_dry_run, 130 interrupted) wins over 5; the mismatch still shows in
  report.json.

## Round 12 (per-layer scoring, owner ruling 11 Oct)
Score each layer the label allows, from the final-turn `trace_digest` (round 10), so a failure is attributed to
the layer that broke. All new label fields are optional and start empty; they are filled over time.
- `Expect` gains `understood` (dict, optional keys `domain`, `intent`, `entities`: list of strings),
  `expected_resolved` (list of strings: record codes/names the resolver must land on) and `tool` (dict, optional
  keys `name`, `params`: dict).
- Layer checks (case-insensitive): understood = `parser.domain` equals, `parser.intent` equals, each entity string
  appears in the JSON of `parser.entities`; resolved = each string appears in the JSON of `resolved[*].entities`;
  tool = some `tools[*]` has `tool` equal to `name` and, for each params key, `args[key]` equal (strings
  case-insensitive; other values exact); reply = the existing verdict (pass = correct | good_clarify).
- Per layer per result: `pass` | `fail` | `na` (label has no field for it) | `no_trace` (label has it, digest has no
  such stage). Results rows carry `layers: {understood, resolved, tool, reply}`.
- Report: `by_layer: {layer: {pass, fail, na, no_trace, pass_rate}}` (pass_rate over pass + fail, None when 0) and
  `first_failing_layer`: for results whose reply failed, a count of the first layer in order understood -> resolved
  -> tool -> reply that is `fail` (`reply` when no earlier layer was scored). `--regrade` recomputes layers from the
  stored digests. The verdict itself is unchanged by layers. GRADER_VERSION stays 8 (verdicts unchanged).

## Round 13 (run provenance, 11 Oct)
- `report.json` records `git_sha` (full HEAD of the checkout the runner runs from, `git rev-parse HEAD` in the
  package's repo; the crew stack serves the same checkout) and `git_dirty` (true when `git status --porcelain` lists
  tracked changes). Env `EVAL_APP_SHA` overrides `git_sha` when the backend runs from another checkout. No git
  available -> both null, run continues. `--regrade` keeps the source run's values. The resolved prompt id is
  already recorded (`prompt.id`, round 11).
