# PLAN: CI-LEAN (Enterprise budget: release <= ~60 billed min, PR gate ~10-15 with DB tests, no nightly, no ci-full per PR)

Status: Build. Slices 1-2 done on PR #1562; card re-planned 10 Oct for the Enterprise budget.
Track: small fix track for the workflow slices (no migration, no auth/RBAC, no ingest). The
app-code speedups found while measuring (router mounting, company-scope hook) are separate lanes,
see "Out of this PR".

## Owner rulings

- 9 Oct: no `ci-full` per PR.
- 10 Oct ~12:00: no nightly; "keep our test case lean"; asked whether a release can bill 15.
- 10 Oct ~14:30 answers: Q1 the never-stuck smoke becomes manual-only. Q3 the ruleset
  `protect-main-branch` requires one check, `PR gate` (crew applies it after this merges and
  `PR gate` is green on main). Q4 a release runs its whole selection, no cap. Q5 speed up every
  slow test now; delete only a test that tests nothing.
- 10 Oct, moving to GitHub Enterprise (50,000 min/month): release <= ~60 billed min; the PR
  gate may include Postgres tests for the changed code (~10-15 min); still no nightly and no
  `ci-full` on PRs.

## Measured

Release run 38017372957 (10 Oct), billed per job, rounded up to the minute: **280**. Backend
shards 177, SCM shards 40, vitest 33, validate 8, images 13 (FE 10, BE 2, MCP 1), deploy 4,
notify 1, gates 4.

- `never-stuck-nightly.yml`: ~30 billed every night, red 7 nights in a row since 3 Oct.
- 30-day failure mining (126 of 162 failed `deploy.yml` runs): 111 distinct test files failed.
  The most frequent failures were not code: the PII guard (21 runs), the two stopwatch tests
  (`test_sales_achievement_perf`, `test_sales_targets_s1::...fast_at_scale`, 11 runs), and
  migration single-head tests on stale branches. Raw data: crew-note on #1562.
- Suite: 2,199 backend test files, 512 CPU-min, of which ~505 is database tests (crew-note (a)).
- Importing the app takes 28 s per process (7,242 FastAPI route builds for 1,852 routes), paid
  by every pytest worker: 4 workers x 7 shards = ~13 runner-min a release.

## Budget (Enterprise, 50,000 min/month)

PR gate, one job `PR gate` on the `ci` label:

| Step | Billed |
| --- | ---: |
| checkout, alembic single head, PII guard, gitleaks, changed areas | ~1 |
| Postgres + Redis, pip from cache, bootstrap, collect-only (import errors in every test file) | ~4 |
| backend tests: changed test files + import selection, tests/scm included, xdist -n 4, cap 3,600 CPU-s | 0-15 |
| frontend changed: `tsc --noEmit` + `vitest run --changed <base sha>` | 0-3 |
| **Total** | **~5 (docs or FE-light) to ~20 (cap)**, typical ~10 |

Release (`workflow_dispatch` on main):

| Job | Billed |
| --- | ---: |
| alembic head + changes (diff since the last successful release, selection, core tier) | 2 |
| `release-tests`: core tier + selection since the last release, collect-only, tsc + vitest `--changed` when the frontend changed | ~10-25 |
| images (one job; an unchanged service tree is retagged, not rebuilt) | ~3 (no FE change) / ~11 |
| build-and-deploy (+ the mail step) | 4 |
| **Total** | **~20-42**, under the ~60 line; a selection above 3,600 CPU-s runs the full sharded suite instead (Q4: no cap), ~250, expected rarely |

A re-dispatch of a tree whose release tests already passed skips the tests (a 1-byte
`validated-tree-<tree sha>` artifact): ~17.

Month: 75 releases x ~35 = ~2,600; 30 PR runs/day x ~10 = ~9,000; never-stuck by hand. **About
12,000 of 50,000.**

What it buys: every PR runs the Postgres tests its change reaches (one import step, crew-note
(b)) plus a type-check and the related vitest; every release runs a fixed core tier plus every
test the merged batch reaches. The full suite runs only when a release's selection is over the
cap, or by hand (`full_suite=true`).

## Release core tier (`sorento_crm_backend/tests/release_core.txt`, <= 600 CPU-s)

Picked from the 30-day failure data (files that caught a real regression on a release or more
than one branch) plus the money and security paths:

- auth / sessions / integration auth / RBAC / company scope: `test_auth_login`,
  `test_user_session_service`, `test_integration_auth_dependencies`,
  `test_integration_auth_resolver`, `test_company_scope`
- money paths that failed a release in 30 days: `test_stock_visibility_policy`,
  `test_cost_price_apply`, `test_ladder_v5_edges`, `test_contacts_access_filters`
- contracts: `test_audit_actor_contract`, `test_conversation_n8n_contract`,
  `chatbot/test_contracts`, `chatbot/test_import_boundary`
- chatbot core golden: `chatbot/test_turn_replay`
- schema guard: `test_schema_uuid_id_principle`

## Slow tests (Q5): speed up, nothing deleted

Every test over 20 s in `.test_durations` (24 tests, 16 files, 24 CPU-min), with cause and fix,
is in the crew-note on #1562. The big levers are not single slow tests but per-test setup:

1. Seed fixtures that rebuild the same world for every test (perf volume, chained round worlds):
   build once per file.
2. Tree-scan guard tests that re-parse the whole backend (`test_ci_select_tests`,
   `test_cost_price_round3_no_direct_writes`, ...): one cached parse per session.
3. Stopwatch tests (11 CI failures in 30 days, no code defect): plan/query-count assertions
   instead of seconds.
4. Per-round chatbot replay files: one golden file per feature (crew-note (d)).

## Out of this PR (own lanes, measured here)

- **Router mounting:** each of 1,852 routes is built ~4 times (nested `include_router`); app
  import 28 s. Flattening saves most of it in every pytest worker, the API's cold start and the
  RQ worker. Touches `app/api/v1/__init__.py` and the module guards: needs its own review.
- **Company-scope hook fallback** (`app/services/company_scope.py:266-317`): a statement whose
  mappers it cannot read gets a loader option for all ~158 scoped models (79,041 predicate builds
  for 825 queries in one chatbot test). Security-sensitive (multi-company scoping): own lane with
  security-reviewer.
- **Chatbot replay merge** (83 -> ~20 CPU-min): its own chatbot lane, mechanical, every distinct
  assertion kept.

## Slices on this PR

1. Done: no `deploy.yml` nightly; `ci-full` refused on a PR.
2. Done: one images job with tree-hash reuse; mail as a deploy step; no PII guard / CI gate at
   release.
3. Never-stuck smoke manual-only (Q1).
4. `PR gate`: one job replacing the PR side of every other job; required check name for the
   ruleset (JSON for crew in the PR).
5. `release-tests`: core tier + selection since the last release; validated-tree skip;
   `full_suite` input; the old shards only for `full_suite` or an over-cap selection.
6. Test speedups that need no app change (perf volume once per file, tree-scan cache, stopwatch
   tests, ingest cap tests, durations refresh).
7. Measure a real PR gate run and a release-equivalent dispatch; report billed minutes.
