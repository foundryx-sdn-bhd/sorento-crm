# PLAN: CI minutes cut (CI-MINUTES-CUT)

Status: built, awaiting the `ci` and `ci-full` evidence runs and the reviewer pass (small fix track: `.github/workflows` only, no app behaviour; reviewer pass required because it changes merge safety)

Lane: PR #1492 on `claude/ci-minutes-cut-3nb19t` (the sandbox's branch; `crew/ci-minutes-cut` was
not pushable from it). UAC: `ci-minutes-cut-acceptance-criteria.md` beside this file.

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
   everything else. `cancel-in-progress` stays on for both labels, in ONE per-PR concurrency
   group: a `ci-full` added while `ci` runs makes the cheap run redundant, and a `ci` added
   while `ci-full` runs means the head moved, so the full run on the old head would be
   about 200 job-minutes of waste.
4. A final gate job (`if: always()`) that fails only when a needed job failed or was
   cancelled, so a path-filtered run still reports one green check. It reports as `CI gate`
   on a `ci` run and `CI gate (full)` on every full-suite run (reviewer finding: one name
   would let a green `ci` run stand in for a `ci-full` that never ran). The ruleset
   `protect-main-branch` requires the five fast-gate names only (crew, 5 Oct); those are
   untouched and still report on every `ci` and `ci-full` run. Requiring `CI gate (full)`
   is the owner's lever to make a green `ci-full` a merge condition.
   The `changes` job declares `permissions` (contents, pull-requests, actions: read) so its
   two `gh api` calls keep working once the repo is private (reviewer finding).
5. Deploy steps, image builds, secrets and the nightly smoke workflow's content are not
   touched (the smoke already has `timeout-minutes: 120`).
6. One page in `documentation/reference/CI-LABELS-AND-MINUTES.md`: what `ci` and `ci-full`
   run, the minute budget, how to read usage.

## Budget reality

The cut makes the cheap gate cheap; it does not fit October's pace into 3,000 minutes a
month on GitHub-hosted runners (one `ci-full` per merged PR at about 210 minutes is about 30
times the allowance by itself). The levers beyond this lane, and the self-hosted runner
profile, are in `documentation/reference/CI-LABELS-AND-MINUTES.md` and the PR body.

## Evidence (filled in as the lane runs)

See the PR body: the `ci` run's job list and minutes against run 37158487882, one `ci-full`
run green, the per-lever minute math and the projected monthly minutes.
