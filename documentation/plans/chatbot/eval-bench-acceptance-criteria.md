# EVAL-BENCH acceptance criteria (UAC)

Plan: `PLAN-eval-bench-16oct.md`. Card: `eval-bench-behaviour-card.md`. Repo paths are relative to
`sorento_crm_backend/`; crew paths to `crew/state/eval/`. No test or fixture uses real customer text: repo tests use
synthetic rows only.

## D1 Provenance

- AC1.1 A parsed turn's `understood` trace record `facts` carry `prompt_sha256` (hex sha256 of the exact system
  prompt text sent to the model, utf-8), `prompt_version`, `model`, `provider`, `reasoning_effort` (`null` when the
  call sends none) and `code_sha` (the value `/health` reports as `git_sha`).
- AC1.2 A turn whose parser call fails carries the same keys on its `understood` record.
- AC1.3 `trace.build_digest` keeps these keys in the parser block, so `results.jsonl.trace_digest` carries them.
- AC1.4 The E2E runner's `report.json` `git_sha` is the server's `/health` `git_sha`; the runner's own checkout is
  `runner_sha`. `report.json.provenance` lists the distinct values seen across turns for `code_sha`,
  `prompt_sha256`, `prompt_version`, `model`, `reasoning_effort`, and `provenance_mixed` is true when any list holds
  more than one value.
- AC1.5 The E2E runner refuses to start (before any LLM call) when `/health` does not answer with a `git_sha`.
- AC1.6 Parser-gold run rows carry the five fields. `score.py` exits non-zero naming the run file when any row lacks
  one; with `--legacy` it scores and prints `LEGACY: not a gate verdict` on the first line.
- AC1.7 `compare.py` refuses a run whose `report.json` lacks `provenance` (same `--legacy` escape).
- AC1.8 Runs offered as reps of one arm whose provenance differs (any of the five fields) are refused as reps,
  naming the differing field.

## D2 L1 replay

- AC2.1 One command (`bin/replay-gate.sh BASE_WT HEAD_WT [--split dev|hidden|all] [--intended FILE]`) replays the
  recorded parses through both worktrees and prints one line per diff: `case_id turn_id stage` (no message or reply
  text), then counts per stage; exit 0 when every diff is listed in `--intended`, 1 otherwise.
- AC2.2 Base and head = the same worktree gives 0 diffs (determinism).
- AC2.3 The F4 pair (base = main before #1737 `bb93cb626`, head = merged #1737 `48fbe8f3d`) reports grounding diffs
  on cand-0174 and cand-0127, the merged revision's diffs in the scratch replay (`f4b-rep2`); the scratch cand-0108
  diff came from an intermediate F4 revision (`f4rep3`) and must not appear.
- AC2.4 The DB the replay reads is unchanged afterwards (`chatbot.turns` count and max `created_at` equal before and
  after).
- AC2.5 A full replay of both arms finishes in under 5 minutes on the Mac mini with at most 2 worker processes.
- AC2.6 When the head's tool call args differ from the recorded call, the case reports stage `tools` and no
  `reply` diff is computed for it.
- AC2.7 A recorded turn without a parse is counted as `skipped:no_parse`, never as a diff.
- AC2.8 `--split dev` reads only dev-split runs; output for hidden runs carries ids only.

## D3 Error bars

- AC3.1 With >= 3 reps per arm, the scorer prints per arm the mean pass rate with a 95% CI, and the paired delta
  (head minus base, items matched by id, reps averaged per item) with a 95% bootstrap CI over items.
- AC3.2 Verdict: `better` only when the delta CI lower bound > 0; `worse` only when the upper bound < 0; otherwise
  `within noise`.
- AC3.3 Fewer than 3 reps in either arm prints `verdict refused: <n> reps < 3` and no verdict word.
- AC3.4 Items present in only one arm are excluded and their count printed.
- AC3.5 Same inputs and seed give the same CI to the printed precision.
- AC3.6 The E2E summary (`compare.py`) does the same over >= 3 runs per arm, pass = label in {correct, good_clarify}.

## D4 Real-mix weighting + restore recipe

- AC4.1 `weights.json` lists gate strata with weights summing to 1, each with its corpus source (report section,
  window, date).
- AC4.2 The scorer's headline is the weighted gate score with its CI; growth slices (dealer, ms/zh/mixed, heavy
  asks) print beside it with their own n and CI and never enter the headline.
- AC4.3 `tooling/corpus/restore_chat_slice.sh DUMP DB` refuses a non-custom-format dump, refuses an existing DB,
  refuses a non-local host, restores only the listed tables (TOC filtered), and prints per-table row counts and the
  DB size.
- AC4.4 `tooling/corpus/refresh.py DB` writes the mix table and candidate regression ids (by failure signal) under
  `private/corpus/<date>/` (chmod 700); output names ids and counts only.
- AC4.5 The recipe is proven once on the 16 Oct eval-base dump into a scratch DB, which is dropped afterwards.

## D5 Hop timing

- AC5.1 A read-only query set gives, per real inbound message, each hop (respond.io -> webhook/n8n -> CRM turn
  created -> engine start -> engine end -> reply at respond.io -> delivered) with p50/p90/p95 and n, run on
  `sorento_cagent_stack` with `default_transaction_read_only=on`, written to a crew report.
