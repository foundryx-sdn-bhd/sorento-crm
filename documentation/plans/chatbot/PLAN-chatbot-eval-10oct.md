# PLAN: CHATBOT-EVAL (generalisation harness + PR gate)

Status: built, baselined 10 Oct; PR #1556 awaiting CI + owner label spot check (30 labels). Track L.
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

## Baseline (10 Oct 2026, grader v7, eval clone of the 6 Oct snapshot upgraded to main head)

| run | cases | pass | strict pass | originals | variant gap | cost | wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| main parser v50 pinned, all 321 + 1 variant | 642 | 79.9% | 36.4% | 79.8% | -0.3 pt | $6.48 | 14.7 min |
| snapshot label v49 (closest to prod v52), same | 642 | 80.4% | 37.1% | 80.4% | 0.0 pt | $6.24 | ~15 min |
| gate config (hidden 214 + 1 variant), main v50 | 428 | 79.0% / rerun 78.3% | 34.3% | 78.0% / 78.5% | | $4.17 | 10.2 min |

- Run-to-run noise (same config twice): -0.7 pt total, 7 of 214 originals flip pass/fail. A gate delta inside
  about 2 pt is noise; read per-topic deltas and flips before calling a regression.
- Dev minus hidden: +5.1 pt (main) / -1.4 pt (v49), within noise for 107 vs 214 cases.
- Originals minus variants is about 0: on this set the parser reads rewordings as well as the originals; the
  failures sit downstream (resolution, carry-over, tool contracts), matching the 9 Oct traces.
- The 9 Oct prod failures reproduce on main: stale subject into a sales pick (`BK007A`), sales_report 422
  `subject_required`, category words resolved as customers ("Which customer"). Group-by sales agent passes
  only as unverified (wording).
- Weakest subjects (main): resources 44%, portal link 50%, forms 56%, promotion 61%, order 65%; strongest
  incoming 92%, purchase cost 92%. Malay 57% (14), Chinese 70% (46).
- Strict pass rate counts only passes that need no judge; 279 of 642 results carry a judge_note or have no
  deterministic criteria. A calibrated judge (Claude, owner decision) turns those into real passes or fails.

## Gate (crew ruling 10 Oct)

PR gate = all 214 hidden originals (~389 live calls, ~$2) on crew's budget; hidden main baseline for that
config: 78.0% pass, strict 34.6%. Rewordings weekly or on owner request. Lanes: 300 live calls/day cap.

## Judge (11 Oct)

Owner provided a dedicated Anthropic eval key (crew secrets only). Judge = claude-sonnet-5-5 at $2 / $0.10
cached / $10 per MTok; own cap `--judge-budget-usd` default $2; unknown-price models refused. Still gated on
>= 0.9 agreement: the owner's spot check (Q2: is today's main reply PASS or FAIL) becomes the first
calibration set (crew script `eval/build/calibration_from_spotcheck.py` -> `eval/hidden/calibration/`).

## Known gaps

Media and ideation turns excluded; relative-date asks carry no period numbers; labels are from the 6 Oct
snapshot (some prod replies were right at ask time); judge calibration pending the owner spot check; eval clone excludes bulk log tables and some embedding source types (resolver types kept).
