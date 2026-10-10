# PLAN: CI-LEAN (release run <= 30 billed min, PR gate <= 3, no nightly, no ci-full per PR)

Status: Plan card posted, waiting on owner answers (no test deletions before them). Track: small
fix track for the workflow slices (no migration, no auth/RBAC, no ingest); test deletions are a
separate slice gated on the owner. PR #1562.

## Owner rulings this plan implements

- 9 Oct: no `ci-full` per PR.
- 10 Oct ~12:00: "i don't need the nightly test, that just adds extra overhead, i want to see if
  we can get billed 15 minutes per release run, is it possible, i want to keep our test case lean".
- 10 Oct budget: 3,000 CI min/month (private-repo allowance), 2-3 deployments/day (60-90
  releases/month). Targets: release <= 30 billed min, PR gate <= 3 billed min per run, no
  nightly, no `ci-full` on PRs.

## Measured today

Release run 38017372957 (main 347826eb, 10 Oct), per job, ceil to the minute (GitHub's rule):

| Job | Wall | Billed |
| --- | ---: | ---: |
| Backend shards 1-6 + serial | 22.9 / 29.6 / 20.6 / 28.0 / 31.7 / 35.4 / 6.1 | 177 |
| SCM shards 1-3 | 12.6 / 14.5 / 11.9 | 40 |
| Vitest shards 1-4 | 6.6 / 7.0 / 8.3 / 8.6 | 33 |
| Validate backend imports (builds the backend image a 2nd time) | 4.3 | 5 |
| Type-check frontend | 1.4 | 2 |
| Validate MCP imports | 0.8 | 1 |
| Build images: frontend / backend / mcp | 9.8 / 1.5 / 0.7 | 13 |
| build-and-deploy (blue/green 2.2) | 3.2 | 4 |
| Notify owner (SMTP secrets unset: it prints a warning and mails nothing) | 0.1 | 1 |
| Alembic gate, PII guard, Changed areas, CI gate (full) | <1 each | 4 |
| **Total** | 39 wall | **280** |

(The crew's 216 for "backend 7 shards" counts the SCM shards with them; same data.)

Other recurring spend found:

- `never-stuck-nightly.yml`: one ~25-32 min job every night (runs 37073113768..38001648406),
  RED on 7 of 8 nights since 3 Oct. ~900 min/month by itself, and nobody acts on the red.
- `deploy.yml` nightly schedule: the full suite (~250 billed) whenever main moved.
- `outline-user-guides-sync.yml`: cron every 6 h, ~4 x 1 min/day = ~120 min/month.

Suite: backend 2,199 test files (499 under `tests/chatbot`, 352 under `tests/scm`), committed
`.test_durations` = 26,410 tests / 1,716 files / 512 CPU-min; 486 files have NO duration history
(stale file, hence the 20-35 min shard spread). 107 `roundN` / `rN` / `htN` replay files = 83
CPU-min (16% of the suite): attribute_asks rounds 34.5, top_selling rounds 20.6, stock_ask_ht26
7.4, rearch r* 6.2 (29 files). `test_sales_achievement_perf.py` alone is 9.4 CPU-min.
Vitest: 1,489 files, ~27 runner-min.

## The floor (what a release costs with ZERO tests)

builds 13 + deploy 4 + gates 2 = **~19 billed today**; after the build/deploy savings below:

| Release kind | Gates | Images | Deploy | Floor |
| --- | ---: | ---: | ---: | ---: |
| Frontend changed since last release | 2 | ~11 (one job, FE 7.6 + BE 0.9 + MCP 0.3 + setup) | 4 | **~17** |
| Backend/MCP only (FE image retagged, not rebuilt) | 2 | ~3 | 4 | **~9** |
| Nothing rebuilt (re-dispatch after a rejected approval) | 2 | 1 | 4 | **~7** |

So **15 is reachable only for a release that does not rebuild the frontend AND runs no more than
~6 min of tests.** A release that rebuilds the frontend cannot reach 15 even with no tests at all:
the Next.js build is 7.6 min of real work and the deploy's blue/green wait is 2.2. The owner's
own budget line (<= 30) is reachable with a meaningful test tier; see next.

## Budget after the plan

Release (target <= 30):

| Job | Billed |
| --- | ---: |
| `changes` + alembic head (PII guard dropped at release: the push to main already ran it on the same tree) | 2 |
| **Release tests** (one job): pip/npm from cache + Postgres + migrate (~2.5), collect-only for import errors (1.6), release core tier + tests import-selected by the diff since the last release under xdist -n 4 (~6), `vitest run --changed <last release sha>` (~1-2) | **~12** |
| Images (one job, rebuild only a service whose directory tree changed; typecheck moves INTO the frontend build: `NEXT_SKIP_TYPECHECK` off, +~1 min) | ~11 / ~3 |
| build-and-deploy (+ the notify step folded in) | 4 |
| **Total** | **~29 (FE changed) / ~21 (backend only) / ~17 (same tree already passed, tests skipped)** |

PR gate (target <= 3): ONE job, `PR gate`: release the `ci` label, alembic head, PII guard,
gitleaks, `py_compile` of every changed .py, and `tsc --noEmit` only when the frontend changed.
No Postgres, no pytest, no vitest on a PR (that cannot fit in 3 min: Postgres + migrate alone is
~2). Expected 2-3 billed; the slice measures it and reports the real number.

Monthly (30 days):

| Item | Count | Each | Month |
| --- | ---: | ---: | ---: |
| Releases (2.5/day) | 75 | ~25 avg (mix of 29 / 21 / 17) | ~1,875 |
| PR gate runs | 10/day | 3 | 900 |
| Outline sync (if kept at 6 h) | 120 | 1 | 120 |
| Nightlies (both removed) | 0 | | 0 |
| **Total** | | | **~2,895 of 3,000** |

Ten PR runs a day is the ceiling at that release rate; last week ran 262 full-suite PR runs
(37/day), so crew labels `ci` once per PR head that is ready to merge, not per push. Outline sync
to every 24 h frees 90 more.

What that coverage buys per release: imports of every test file (collect-only), a fixed core tier
(~25 CPU-min, list below), every backend test file that imports what changed since the last
release (the CI-SPEED import selection, already in `scripts/ci_select_tests.py`, pointed at the
release diff instead of the PR diff), every vitest file related to a changed frontend file, the
frontend typecheck, the single alembic head. What it gives up: the other ~480 backend CPU-min and
~25 vitest min only run when something they import changes. A full run stays available by hand
(`workflow_dispatch` with `full_suite=true`, ~250 billed, ~8% of the month), never scheduled.

## Test tiers

**Release core tier** (`sorento_crm_backend/tests/release_core.txt`, ~25 CPU-min, ~6 runner-min):

- alembic: the serial migration round-trip (`tests/test_migration_*.py` heads only; today 2.5 min)
- imports / API contract: `pytest --collect-only` (all files), `tests/test_audit_actor_contract.py`,
  `tests/test_conversation_n8n_contract.py`, `tests/chatbot/test_contracts.py`
- money paths: pricing / orders / stock, the files that failed CI in the last 30 days first
  (filled from the failure data, section below), capped so the tier stays <= 25 CPU-min
- security: `test_auth_login`, `test_user_session_service`, `test_integration_auth_*`,
  `test_rbac`, `test_company_scope`, `test_dev_login` (the one that keeps the bypass out of
  deployed env files)
- chatbot core golden: `tests/chatbot/test_turn_replay.py` (231 cases, 2.7 CPU-min)

**Everything else** runs when the diff imports it. **DELETE / MERGE** proposals (owner says yes
per line before anything is removed; every file that failed CI in the last 30 days is named as
such):

1. `test_sales_achievement_perf.py` (9.4 CPU-min, 487 s setup): a perf benchmark, not a
   correctness gate. Delete; keep the query-count assertion if it has one, as a unit test.
2. 107 round / rN / htN replay files (83 CPU-min) merged into one golden file per feature
   (attribute_asks, top_selling, stock_ask, rearch, ...) driven by a cases table, dropping cases
   that are byte-identical in input and expectation. Expected 83 -> ~25 CPU-min.
3. Tests that re-check the repo instead of the product: the PII guard tree scan in pytest (the
   `pii_guard.py` job already runs it), spec/doc golden checks, the ci-meta subprocess tests that
   re-run workflow shell (keep `test_ci_docs_only_filter` / `test_ci_select_tests`, they guard
   this very workflow; trim the rest).
4. `.test_durations` refreshed (486 files missing) so selection cost estimates are real.

The failure data behind each line is appended below when the 30-day mining finishes.

## Build / deploy savings

- One `build-images` job instead of a 3-leg matrix (saves 2 roundings).
- Tag every image also by its service directory's git tree hash; when that tag already exists,
  `imagetools create` retags it to the commit sha in seconds instead of rebuilding. Frontend
  build-arg secret (`NEXT_PUBLIC_VAPID_PUBLIC_KEY`) is not in the tree hash; a rotated key needs
  a `rebuild_all=true` dispatch (input added).
- `validate-backend` / `validate-mcp` do not run at release (their image builds duplicate
  `build-images`; collect-only moves into the release tests job).
- `notify-owner` job folded into a final `if: always()` step of build-and-deploy (SMTP is unset
  today; GitHub's own failed-workflow mail still covers a failed gate).
- `CI gate (full)` does not run at release (build-and-deploy already checks every need).
- Skip tests at release when the exact tree already passed: a green release-tests job uploads a
  1-byte artifact `validated-tree-<git tree sha>`; `changes` on a later dispatch finds it and
  marks every test area false. Covers re-dispatch after a rejected approval or a deploy hiccup.

## Workflow rulings to implement (no owner question needed)

- Remove `schedule:` from `deploy.yml` and the schedule branch of `changes`.
- `ci-full` refused: a `ci-full` label on a PR runs one job that removes the label and fails
  with "ci-full is release-only (owner, 9 Oct)". The full suite is `workflow_dispatch` only.
- Red-first: `tests/test_ci_docs_only_filter.py` gets the new expectations before the workflow
  changes (nightly cases removed, `ci-full` refused, release tiers).

## Slices (one PR, commits in order)

1. Red tests for slice 2, then: drop the `deploy.yml` nightly, refuse `ci-full`.
2. Release budget: single images job + tree-hash reuse, notify folded, CI gate and validate-* off
   at release, validated-tree skip.
3. Release tests job: core tier file + release-diff selection + `vitest --changed`; full suite
   behind `full_suite=true`.
4. PR gate: one job (needs the ruleset's required-check list changed by the owner, Q3).
5. Deletions / merges, only the lines the owner approved.
6. Measure: one release-equivalent dispatch on this branch (tests only, no build/deploy off main)
   plus a PR-gate run; report billed minutes from the jobs API.

## Questions (posted as crew-ask)

See PR #1562 comments.
