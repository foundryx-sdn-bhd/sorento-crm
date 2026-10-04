# UAC: fast PR gate, full suite in the merge queue, release by dispatch with owner approval

Plan: `PLAN-ci-fast-gate-29sep.md`. Every AC names how it is checked.

- **AC-1 A `ci`-labelled PR never runs the six backend shards.** `Backend test
  suite (Postgres, xdist) (1..6)` report `skipped` on every PR run. Check:
  `test_pull_request_fast_gate_*`; this PR's own labelled run.
- **AC-2 SCM shards run on a PR only for SCM paths.** Check:
  `test_scm_path_on_a_pull_request_runs_the_scm_shards`,
  `test_non_scm_backend_path_on_a_pull_request_skips_the_scm_shards`.
- **AC-3 A merge-queue entry runs the full suite and never deploys.** Check:
  `test_merge_group_runs_the_full_suite_and_never_deploys`,
  `test_merge_group_never_deploys_whatever_the_flags`.
- **AC-4 A push to main runs the alembic gate and nothing else.** The run for a
  merge shows one job, `Single alembic head (fast gate)`; `Changed areas`, every
  test job, `Build and push images`, `build-and-deploy` and `Notify owner by
  email` are `skipped`; no email arrives. Check:
  `test_push_to_main_runs_the_alembic_gate_and_nothing_else`; the first merge
  after this lands (#1379 is the candidate: it must NOT deploy).
- **AC-5 A release runs the full suite, builds, then waits for approval.**
  `gh workflow run deploy.yml --ref main` on main's head: every test job runs,
  `build-images` runs in parallel, `build-and-deploy` shows "Waiting for
  review" with environment `production`, and nothing reaches the server until
  the owner approves. Check: `test_release_runs_the_full_suite_then_builds_and_deploys`,
  `test_deploy_job_carries_the_production_environment`; the first release.
- **AC-6 `skip_tests=true` releases without the suite.** The 16 test jobs are
  `skipped`, `build-images` and `build-and-deploy` run, the deploy still waits
  for approval. Check: `test_release_with_skip_tests_builds_and_deploys_only`,
  `test_release_with_skip_tests_zeroes_every_area_but_is_not_docs_only`.
- **AC-7 `skip_tests` is honoured on a dispatch only.** A stray value on a PR or
  queue run changes nothing. Check: same test, second half.
- **AC-8 A dispatch on a non-main ref tests but never ships.** `build-images`,
  `build-and-deploy`, `notify-owner` are `skipped`. Check:
  `test_release_dispatched_on_another_ref_tests_but_never_ships`.
- **AC-9 A failed gate in a release does not deploy and still mails the owner.**
  Check: `test_release_with_a_failed_gate_does_not_deploy_but_still_notifies` (8
  variants), `test_release_with_skip_tests_and_a_failed_build_does_not_deploy`.
- **AC-10 A rejected approval fails the run and mails "failed".** Check: the
  first release, by rejecting once (optional; GitHub semantics).
- **AC-11 Two releases dispatched close together deploy serially.** The second
  `build-and-deploy` queues in the `deploy-production` group. Check: diff
  (concurrency block unchanged on the job).
- **AC-12 Docs-only, other-label and concurrency behaviour from #1370 is
  unchanged for PR and queue runs.** Check: the #1370 tests in the same file.
- **AC-13 The two false comments are gone.** `grep -n "cannot merge"
  .github/workflows/deploy.yml` is empty; the trigger and `check-migration-heads`
  comments point at the ruleset and the plan. Check: grep.
- **AC-14 Docs no longer say a merge deploys.** `CLAUDE.md` lane-merge
  discipline and `documentation/agents/cloud-lanes.md` describe the release
  dispatch and the approval. Check: diff.
- **AC-15 The ruleset list is exact.** The 19 names (18 at this plan; row 19, the `serial` leg, from CI-SPEED #1426) in the plan's table equal
  the `name:` values with matrix suffixes as reported on a real run. Check: the
  check names on this PR's own `ci` run against the table.
- **AC-16 Lint is not a gate.** No lint job is added; the plan records the
  measured 287 errors. Check: diff.
