# PLAN: PV2 scenario battery + live runner (PV2-EVAL, 14 Oct 2026)

Status: Build, baselines running. Track M (crew lane PV2-EVAL, branch `crew/pv2-eval`, draft PR #1674 into crew/pv2;
crew/pv2 8ae511217 merged in). Baseline 1 (0690ef90a) posted on the PR; failure list handed to PV2-FIX
(`crew/state/reports/PV2-EVAL-failures-baseline1-14oct.md`).
UAC: `pv2-eval-battery-14oct-acceptance-criteria.md` (beside this file).
Behaviour card: crew ask a74982394, answered 14 Oct (all recommendations taken; v1 pinned to the prod v52 text).

## Why

The owner will hand-test PV2 and expects it to be very smooth across many scenarios: real ones and ones he
improvises (refine, reply to the bot's question, topic switch and back, "this product" after a document, several
products, quantities carried, Manglish / Malay / Chinese, particles, small talk, permissions). Single-shot checks
missed follow-up bugs before (crew memory `feedback-live-check-follow-ups`). Crew needs one battery and one
re-runnable runner that proves the bar on a copy before any URL.

## What exists and is reused

- In-process console turn: `console_service.run_console_turn` (`app/services/chatbot/console_service.py:444`),
  driven per case with chained `session_vars` by `scripts/pv2_run_cases.py:51-64`.
- Per-turn chain format and grading idea: `tests/chatbot/journeys/*.json` + `scripts/chatbot_journey.py` (HTTP,
  console route, substring matchers). The battery keeps that per-turn `expect` idea in YAML with tags.
- Parse per turn: `chatbot.turns.trace` event `pv2_turn` (`pv2/turn.py:174-186`).
- The crew eval runner (`scripts/chatbot_eval/`) grades only the final turn and cannot pin the v2 prompt
  (crew tooling notes, 14 Oct), so it is not the base here.

## Safety (crew answer Q1 (b))

The console path is not send-free: a console turn writes stock asks and enqueues the salesman notify on queue
`respond_io` (`engine.py:1356-1371`, `engine.py:9803`, `stock_ask_service.py:213-238`). The runner therefore drops
`console_origin`, forces the parser mode by patching `pv2_flag.parser_mode` (`pv2/flag.py:41-51`) in its own
process, refuses every RQ enqueue in-process, and runs with the copy's dead-proxy egress (crew `crew.toml`
testenv `env_overrides`). Ideation turns call the local shared service (`IDEATION_SHARED_SERVICE_URL` is
localhost on the copy) with `is_test`.

## Slices (one lane, one PR)

1. Docs: this plan + UAC.
2. TESTER (red, test files only): battery YAML (60+ conversations), `contacts.yaml`, `calibration.yaml`, and the
   red pytest files: battery schema + coverage, deterministic checks, judge prompt/parse, guard (outbound refused,
   envelope shape), report table. Commit `test(red): ...` alone; crew-note red-proof.
3. CODER: `sorento_crm_backend/scripts/pv2_battery/` (`schema.py`, `checks.py`, `judge.py`, `guard.py`,
   `runner.py`, `report.py`, `__main__.py`) until the red tests pass without editing them. Kill proof after green.
4. Calibrate the judge on `calibration.yaml` (target agreement >= 0.9).
5. Baseline: v2 + v1 on the copy (`crew testenv up PV2-EVAL`, API :8105, MCP :8205, DB `sorento_cagent_stack`),
   table to the draft PR as `crew-note:`, failure list to PV2-FIX by crew report.
6. Reviewer (read-only), then crew-done.

## Rounds after the first build (each red test first, kill proof on the PR)

- Type-aware `false_gap` (files carry `attachmentType` + `filename`).
- Runner imports this worktree's `sorento_crm_mcp` (the shared venv's editable install points at the primary
  checkout).
- Judge facts from the trace's tool events, looked_up raw, crossdomain lookups and access grants, capped with a cut
  marker; identity keys dropped and phones / wamids masked before the external judge sees them.
- Embedding key set in-process from the dev DB (bG4A guard, never on disk; crew answer a77754265).
- Reviewer round 1: console lane switches entered once per run (they flip `system_settings` on the shared dev DB),
  one lane per audience, every bubble graded, egress hardened, ideation target must be loopback, exact version pin.
- Ideas marked `env` and out of the pass rate while the ideation service is down (crew answer a80240368).

## Not in scope

No change under `app/`. No new Playwright spec. No prompt publish or label move. No write to the dev DB beyond the
console turn's own `is_test` rows.

## Trigger for more machinery

A second consumer of the per-turn grader (for example the PV2-FIX live checks) is the trigger to move `checks.py`
into a shared place; until then it lives with the runner.
