# CI labels and the minute budget

The repo is private on GitHub Pro: 3,000 Linux runner minutes a month, billed per job and
rounded up to the minute. One workflow, `.github/workflows/deploy.yml`, runs everything. This
page says what each trigger runs, what it costs, and how to read the usage. The workflow file's
comments are the authority on the mechanics; this page is the operator's view.

## What runs when

| Trigger | Runs | Job-minutes (typical) |
| --- | --- | --- |
| PR labelled `ci` | The cheap gate: alembic single head, PII guard, gitleaks, changed areas, validate backend and MCP imports, type-check and vitest only when the frontend changed, the SCM shards only when SCM paths changed, and the changed backend tests (the PR's changed test files plus the files that import a changed module, up to 3600 estimated CPU-s). The six main backend shards never run here. | 12 to 25 for a backend-only change, up to about 90 when frontend and SCM both changed |
| PR labelled `ci-full` | The full suite, the merge gate: the fast gates and validate imports, plus the six main backend shards and the serial entry, the three SCM shards, the four vitest shards and the type-check, whatever changed (the changed-tests job skips: the shards cover every file). Deploys nothing. | 190 to 230 |
| `workflow_dispatch` on main (the release) | The full suite, then build images and the owner-approved deploy. With `skip_tests=true`, the build and the deploy only. | 230, or about 15 with `skip_tests` |
| `schedule` (nightly, 03:17 Malaysia time) | The full suite on main's head, unless the last green nightly already tested that head (then the fast gates only, under a minute). Deploys nothing. | 200 on a day main moved, under 2 otherwise |
| Push to main | The alembic single-head gate, PII guard and gitleaks. No tests, no deploy. | under 2 |
| Any other label | Nothing (every job skips). | under 1 |

Every labelled, queued, nightly or release run also reports one gate job that passes only
when every needed job is `success` or `skipped` (a path filter) and fails on anything else:
`failure`, `cancelled`, the `abandoned` GitHub reports for a job it never found a runner for
or a matrix whose shard hit its cap, or any new value. It reports as `CI gate` on a `ci` run
and `CI gate (full)` on every run of the full suite. A docs-only `ci` run is therefore green
with the fast gates alone. A push to main or any other label reports no gate.

## The labels

Both labels are one-shot: the run removes the label it was fired by, so adding it again
re-runs against the PR's current head. A push to the PR branch never starts a run. Both labels
share one concurrency group per PR with cancel-in-progress on: adding either label while a run
is live cancels that run (a `ci-full` makes the cheap run redundant; a `ci` after `ci-full`
means the head moved). Never change labels while a run you want is live; to re-run, remove the
label, wait ten seconds, add it alone.

- `ci` while a lane is being built: cheap, as often as needed.
- `ci-full` once per PR, right before merge, after the head has settled. A PR is merge-ready
  when its `ci-full` run is green on the head being merged.

Above the 3600 CPU-s selection budget the `ci` run executes the PR's own changed test files
only and says so in the "Select backend tests by import" step of the `Changed areas` job; the
whole selection then runs under `ci-full`. That replaces the earlier behaviour (CI-SPEED,
1 Oct 2026) of running all six shards on the PR, which fired on most backend PRs and was 59% of
all minutes in the week of 29 Sep to 5 Oct 2026.

The ruleset `protect-main-branch` requires five check names: `Single alembic head (fast
gate)`, `Changed areas`, `Validate backend imports`, `Validate MCP imports` and `Type-check
frontend`. Both labels report all five (a path-filtered one reports "skipped", which a ruleset
accepts). The ruleset does not require any shard, so a merge on a `ci`-only PR is possible by
hand; the `ci-full` run before merge is crew discipline, or the owner adds `CI gate (full)` to
the required checks, which makes a green `ci-full` on the head being merged a condition of the
merge (a `ci` run reports `CI gate`, a different name, so it cannot stand in).

## Timeouts

Every test and gate job carries `timeout-minutes`: 60 on the three backend test jobs (main
shards, SCM shards, changed tests), 20 on everything else. GitHub's default is 6 hours and a
hung job bills all of it. The caps are for a hung runner, not targets, and they sit at about
twice the worst observed run time: a main shard measures 18 to 24 minutes, 29 in the worst
run seen (37158487882), and a cap at 35 cut a healthy shard at 94% on run 37369427779 (33:55
of pytest, a shard that took 22 minutes two days earlier, so run-to-run variance is about
1.6x). If a balanced shard runs past 45 minutes, rebalance `.test_durations` or raise the cap
with that evidence. The deploy jobs (`build-images`, `build-and-deploy`, `notify-owner`) are
deliberately outside this: the deploy step already has its own 70-minute command timeout for
the worker drain.

A job that shows `cancelled` with no runner and no steps did not hit its cap. The first
`ci-full` run (37369427779, 5 Oct 2026) lost eleven jobs that way, each exactly 15 minutes
after it was queued whatever its cap, with the check-run annotation "The job was not acquired
by Runner of type hosted even after multiple attempts": GitHub gave up finding a hosted runner
while the account's 20 concurrent job slots were held by other PRs' runs. The workflow cannot
cause or cure that; the gate job goes red, and the fix is to re-add the label once the queue
has drained (or to run fewer runs at once, or to add runners). Read the annotation under the
job before blaming a cap.

## The budget

Measured 29 Sep to 5 Oct 2026 (701 runs): 85,687 minutes in seven days, 512 of them
`ci`-labelled PR runs at 126 minutes each. At that pace, about 73 PR runs a day:

| Lever | Minutes a month |
| --- | --- |
| Before: 73 PR runs a day at 126 min | about 276,000 |
| After: 73 `ci` runs a day at about 20 min (backend-only typical) | about 44,000 |
| Plus one `ci-full` per merged PR, say 15 a day at 210 min | about 95,000 |
| Plus the nightly when main moved, 30 days at 200 min | 6,000 |
| Plus releases, 29 a month at 230 min (15 with `skip_tests`) | 6,700 (or 450) |

Even after the cut, the pace of October 2026 does not fit in 3,000 minutes a month on
GitHub-hosted runners: the `ci-full` merge gate alone is about 30 times the allowance. The
cut makes the cheap gate cheap; fitting the budget needs either a self-hosted runner (free
minutes on GitHub's side, see the PR body of CI-MINUTES-CUT for the per-job profile) or far
fewer full runs. The levers, cheapest first:

1. Release with `skip_tests=true` once every merged PR has a green `ci-full` (saves about
   6,000 a month).
2. Make the nightly weekly (`cron: "17 19 * * 0"`) or drop it (saves up to 6,000).
3. Batch merges so one `ci-full` covers several PRs: `ci-full` tests the PR head merged onto
   main as it was when labelled, so the last PR of a batch labelled after the others merged
   covers the combination.
4. A self-hosted Linux runner for the shards (`runs-on` on the three backend test jobs): the
   SCM and main shards need Postgres (pgvector) and Redis as service containers, so Docker is
   required; peak concurrency on a `ci-full` run is 16 jobs (7 main entries, 3 SCM, 4 vitest,
   validate backend, validate MCP, type-check).

## How to read usage

- The month to date: GitHub, repository Settings, Actions, then the "Usage" panel under
  Billing at the organization or account level (`Settings > Billing and plans > Usage`). It
  shows minutes by workflow and by runner type; `deploy.yml` is the only one that matters.
- One run: `gh run view <id> --repo jayson-odoo/sorento-crm --json jobs` lists every job with
  `startedAt` and `completedAt`; sum the minutes, each job rounded up, for what the run
  billed. `gh api repos/jayson-odoo/sorento-crm/actions/runs/<id>/timing` gives the billable
  milliseconds directly (zero on a public repo, which is why the week of measurement used the
  job timestamps).
- A period: crew's `ci_minutes.py` (the script behind the numbers above) lists every run of a
  window with `gh run list --json databaseId,event,createdAt` and sums the jobs the same way.
- The skip reasons: a `ci` run's `Changed areas` job log says which areas changed, which test
  files were selected and whether the selection was above the budget; the gate job's log
  lists every needed job's result.
