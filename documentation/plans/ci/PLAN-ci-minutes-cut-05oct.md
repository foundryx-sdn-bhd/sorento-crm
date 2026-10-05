# PLAN: CI minutes cut (CI-MINUTES-CUT)

Status: in progress (small fix track: `.github/workflows` only, no app behaviour; reviewer pass required because it changes merge safety)

Lane: `crew/ci-minutes-cut`. UAC: `ci-minutes-cut-acceptance-criteria.md` beside this file.

## Why

The repo goes private soon. GitHub Pro allows 3,000 Linux minutes a month. Crew measured
85,687 minutes over 29 Sep to 5 Oct 2026 (701 runs, per-job minutes rounded up like GitHub
bills), which is 245k to 367k minutes a month, 82 to 122 times the allowance.

Where the minutes went (7 days):

| Driver | Share |
| --- | --- |
| Six "Backend test suite (Postgres, xdist)" shards, 18 to 24 min each | 59% |
| Three SCM shards, 10 to 12 min each | 18% |
| Four vitest shards, about 8.5 min each | 12% |
| Serial shard | 2% |
| Validate backend imports, 5.4 min | 3% |
| Everything else | under 7% |

512 `ci`-labelled PR runs in 7 days (about 73 a day) at 126 minutes each were 75% of all
minutes. A push to main runs only the alembic gate. A release (`workflow_dispatch`) is 227
minutes, 29 in 30 days. No `merge_group` run in 30 days: the merge queue is not in use.

## Root cause

`deploy.yml` already keeps the six main shards off a PR, with one escape hatch: when the
import-based selection (CI-SPEED, 1 Oct) estimates more than 3600 CPU-s, `changes` sets
`backend_full=true` and the six shards plus the SCM shards run on the PR instead of
`test-backend-changed`. Run 37158487882 (PR run, 3 Oct) shows it: every main shard, the serial
entry and all three SCM shards ran on a `ci` label, 31 minutes wall clock and about 200
job-minutes. In a tightly coupled app most backend changes reach the budget, so the "fast
gate" was the full suite on most PRs.

## Design

1. `ci` label = the cheap gate only. Fast gates (alembic head, PII guard, gitleaks, changed
   areas), validate imports (backend, MCP), typecheck and vitest when the frontend changed,
   SCM shards when SCM paths changed, and `test-backend-changed` for the import selection. The
   six main shards never run on `ci`. When the selection exceeds its budget the changed-tests
   job runs the PR's own changed test files and says so; the full suite is `ci-full`'s job.
2. `ci-full` label = the full suite as the merge gate (six shards plus serial, SCM shards,
   vitest, typecheck, validate imports, every fast gate). Same one-shot label mechanics as
   `ci`: the label is removed at the start of the run, so adding it again re-runs on the new
   head. The full suite also runs on `workflow_dispatch` (the release, unchanged) and on a
   nightly `schedule` against main.
3. `timeout-minutes` on every test job: 35 on the shards and the changed-tests job, 15 on
   everything else. `cancel-in-progress` stays on for both labels, each label in its own
   per-PR concurrency group so a `ci` re-run never cancels a running `ci-full`.
4. A final `CI gate` job (`if: always()`) that fails only when a needed job failed or was
   cancelled, so a path-filtered run still reports one green check. The ruleset
   `protect-main-branch` requires the five fast-gate names only (crew, 5 Oct); those are
   untouched and still report on every `ci` and `ci-full` run.
5. Deploy steps, image builds, secrets and the nightly smoke workflow's content are not
   touched (the smoke already has `timeout-minutes: 120`).
6. One page in `documentation/reference/CI-LABELS-AND-MINUTES.md`: what `ci` and `ci-full`
   run, the minute budget, how to read usage.

## Evidence (filled in as the lane runs)

See the PR body: the `ci` run's job list and minutes against run 37158487882, one `ci-full`
run green, the per-lever minute math and the projected monthly minutes.
