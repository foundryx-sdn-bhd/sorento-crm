# UAC: CI minutes cut (CI-MINUTES-CUT)

Plan: `PLAN-ci-minutes-cut-05oct.md`. Each criterion names the proof.

| # | Criterion | Proof |
| --- | --- | --- |
| 1 | A `ci`-labelled PR run never runs the six main backend shards, whatever `changes` reports. | `test_ci_label_never_runs_the_six_shards_whatever_changes_reports`; the `ci` run on PR #1492 (job list in the PR body). |
| 2 | A `ci` run executes the import-selected tests under the budget, and the PR's own changed test files above it. | `test_changes_job_outputs_prefer_the_selection_and_fall_back_above_the_budget`, `test_cli_above_the_budget_writes_no_list_and_full_true`. |
| 3 | A `ci-full`-labelled PR runs the full suite (six shards plus serial, SCM shards, vitest, type-check, validate imports, every fast gate) and deploys nothing. | `test_ci_full_label_runs_the_full_suite_and_never_deploys`, `test_ci_full_label_counts_every_area_as_changed`; one green `ci-full` run on PR #1492. |
| 4 | Both labels are one-shot: the run removes the label that fired it. | `test_release_label_job_removes_the_label_that_fired_the_run`; the label is gone after each run on #1492. |
| 5 | The full suite runs nightly on main, and skips its test jobs when main has not moved since the last green nightly. | `test_nightly_tests_main_unless_the_last_green_nightly_tested_this_head`, `test_nightly_runs_the_full_suite_or_nothing_and_never_deploys`. |
| 6 | Every test and gate job carries `timeout-minutes` (60 on the backend test jobs, 20 elsewhere: about twice the worst observed run, after a 35-minute cap cut a healthy shard at 94% on run 37369427779); the deploy jobs are untouched. | `test_every_test_and_gate_job_carries_a_timeout`; `git diff` shows no change to `build-images`, `build-and-deploy`, `notify-owner`. |
| 7 | `cancel-in-progress` stays on for `ci` and `ci-full`, in one per-PR group; other labels and other events cancel nothing. | `test_concurrency_*`. |
| 8 | The gate job reports success only when every needed job is `success` or `skipped` (a path filter) and failure on anything else (`failure`, `cancelled`, `abandoned`, unknown), and never runs on a push to main or another label. It reports as `CI gate` on a `ci` run and `CI gate (full)` on every full-suite run, so a `ci` run cannot satisfy a ruleset requiring the full one. | `test_ci_gate_*`. |
| 13 | The `changes` job's token can read the PR file list and the nightly run list on a private repo (explicit `permissions`). | `test_changes_job_token_can_read_the_pr_files_and_the_nightly_runs`. |
| 9 | The five ruleset-required check names still report on every `ci` and `ci-full` run. | Job names unchanged (`git diff`); the two runs on #1492. |
| 10 | Deploy steps, image builds, secrets and the nightly smoke workflow's content are untouched. | `git diff --stat`: no change under `build-images` / `build-and-deploy` / `notify-owner`, `never-stuck-nightly.yml` unchanged (it already had `timeout-minutes: 120`). |
| 11 | One documentation page says what `ci` and `ci-full` run, the minute budget and how to read usage. | `documentation/reference/CI-LABELS-AND-MINUTES.md`. |
| 12 | The existing pinning tests still pass. | `pytest tests/test_ci_docs_only_filter.py tests/test_ci_select_tests.py` green (198 passed). |
