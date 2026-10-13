# PLAN: CI-COST (lean release ran the full suite)

Status: In review. Track: small fix track (CI only, no migration, no auth/RBAC, no ingest).

## Evidence

Release run 38099410256 (main f7e48144b, the first release after CI-LEAN #1562), job `Changed
areas` 114352042938 printed `lean release: the batch since the last successful release,
00e695568...`, `changed files (123)` and `lean=true`. Then the select step printed `2076 test
files, estimated 32731 CPU-s (budget 3600): above the budget` and wrote `full=true`. All 10 main
shards, the serial shard, 3 SCM shards and 4 vitest shards ran: ~468 billed minutes (sum of
per-job minutes rounded up).

## Root cause

`.github/workflows/deploy.yml` job `changes`, output `lean` (line 436 on f7e48144b):
`steps.filter.outputs.lean == 'true' && steps.select.outputs.full != 'true'`. An over-budget
selection set the job output to false, so every shard job's `needs.changes.outputs.lean != 'true'`
held (validate-backend 691, validate-mcp 781, test-backend-scm 893, test-backend 1126-1129,
validate-frontend 1650, typecheck-frontend 1705) and `lean-gate` (1388-1391, needs
`lean == 'true'`) skipped. That was CI-LEAN's owner Q4 ("no cap") by design. The filter step's
`if { [ "$EVENT" != "pull_request" ] && [ "${lean:-false}" != "true" ]; } || [ -n "$why" ]` (600)
was not the cause: `lean=true`, `why` empty.

Why 2076 files: the batch changed `app/models/__init__.py` (referenced by nearly every test),
`app/models/chat_facts.py` (re-exported from it, so one level of transitivity reaches the
`app.models` package: 1565 files) and `app/config.py` (1180 files).

## Fix

- `lean` output reads only the filter step. The full shards run only on `full_suite`, no earlier
  release, a failed lookup, a batch at the compare API's 300-file cap (the diff is unknown), or a
  batch that changes `tests/conftest.py` or `tests/_pg_fixture.py` (owner ruling via crew, 11 Oct
  2026, option (b): a harness change alters how every test runs; the 9 Oct release caught 6
  repo-wide breaks the changed tests missed; rare, so the average stays near the 60 line).
- Over the budget, `scripts/ci_select_tests.py` writes the test files named on stdin instead of
  an empty list: on a PR its changed tests (what the workflow already fell back to), on a release
  the batch's changed tests plus the core tier. Each PR of the batch already ran its own import
  selection in its PR gate.
- `full_suite` input text: ~470 billed, measured.
- Schedules: none on deploy.yml or never-stuck-nightly.yml (test `test_no_test_workflow_runs_on_a_schedule`).
  The only cron left is `outline-user-guides-sync.yml` (every 6 h, a docs sync, no tests).

## Estimate (billed minutes)

Calibration: PR gate 38074897990 ran 883 estimated CPU-s in 423 s (xdist -n 4), ~2.1x; setup
before the tests ~3.7 min (install, smoke imports, collect-only).

| | this batch (8 PRs) | typical 2-3 PR batch |
| --- | ---: | ---: |
| backend selection | 64 files, 1445 CPU-s (core 29 / 451 + 35 changed) | 1500-3600 CPU-s (budget cap) |
| release-tests job | ~4 setup + ~12 tests + ~4 tsc/vitest = ~20 | ~17-35 |
| alembic + changes + images + deploy | ~1 + 1 + 3-11 + 4 | same |
| **total** | **~29-37** (was ~468) | **~26-51** |

Vitest `--changed` time for a 16-file FE batch is UNVERIFIED (no lean run yet).
