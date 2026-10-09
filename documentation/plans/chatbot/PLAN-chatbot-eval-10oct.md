# PLAN: CHATBOT-EVAL (generalisation harness + PR gate)

Status: building (S1 red tests); card answered 10 Oct (see CARD Status line).
Track: L (prod-snapshot data, new gate). No migration, no auth/RBAC change. UAC:
`chatbot-eval-acceptance-criteria.md`.

## Shape

Repo holds code only; every case, label, mask map and run log lives under
`/Users/tehjayson/Documents/foundryx/crew/state/eval/` (chmod 700; `dev/`, `hidden/`, `runs/`).

1. `sorento_crm_backend/scripts/chatbot_eval/` (package, stdlib + httpx only)
   - `cases.py`: case schema (dataclasses), load/validate a case dir, refuse a dir inside the repo.
   - `engine.py`: `Engine` protocol `turn(case, text, state) -> Outcome`; `HttpTurnEngine` posts the
     case contact's envelope to `/api/v1/external/chat/turn` with `is_test`, `test_run_id` and the
     carried `previous_conversation_state`; reads `chatbot.turns.trace` for tool errors + tokens.
     Other engines load via `--engine module:Class` (follow-up tool-calling agent lane).
   - `grade.py`: deterministic checks (must / options / must_not / stuck / tool_error / uuid) then
     optional judge (`judge.py`, provider by env, calibration gate >= 90% on the calibration set).
   - `report.py`: per topic x language x verdict; underfit, overfit gaps (dev-hidden,
     original-variant); tokens, est. USD (price env), p50/p95 latency, wall time; JSON + markdown;
     `--baseline` delta.
   - `run.py` CLI: `--data-dir`/`EVAL_DATA_DIR`, `--split dev|hidden|all`, `--variants none|one|all`,
     `--max-parallel`, `--budget-usd` (hard stop), `--out`.
2. Dataset builder (crew-side, not in repo): extracts from the snapshot, masks, labels with SQL facts,
   generates + filters variants, splits 1/3 dev, 2/3 hidden.
3. Gate: `crew eval <LANE>` (crew script, not repo) runs `run.py --split hidden` on the lane stack and
   posts aggregate-only `crew-note`. Baseline JSON from main kept in `state/eval/baselines/`.

## Slices

S1 runner core (cases, engine, grade, report, CLI) red-first against fake engine + fixture cases
generated in tests (fake data only). S2 judge + calibration. S3 dataset build (crew dir). S4 baseline
on main + gate script + report.

## Triggers deferred (not built)

DB tables for eval runs (PLAN-ai-assistant-evals-guardrails section 3) only if runs must be browsed
in the CRM UI.
