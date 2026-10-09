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
