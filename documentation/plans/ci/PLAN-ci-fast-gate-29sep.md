Status: small fix track - in review (PR #1375; follow-up to #1370)

# CI: fast PR gate, full suite in the merge queue, release by dispatch with owner approval

## Rulings

- Owner, 29 Sep 2026, on the options the orchestrator put forward: "I lean towards
  3 for now, and also 2 okay". Option 3 is the fast PR gate with the full suite in
  the merge queue.
- Owner, 29 Sep 2026 23:05, replacing option 2: "control the running of main branch
  CI so not every merge will trigger the CI automatically", and a live deploy must
  wait for the owner's approval. The orchestrator created the GitHub environment
  `production` (required reviewer jayson-odoo, deployment branches: main only).

Nothing in this lane changes a repository setting. The ruleset changes that make
the fast gate the real merge gate are listed below for the owner to apply.

## Measured facts (29 Sep 2026, from gh)

- Every job runs on GitHub-hosted `ubuntu-latest`; no self-hosted runner. The
  account is capped at 20 concurrent jobs (Free plan).
- One `ci`-labelled PR run: 18 jobs, about 190 job-minutes. The six main backend
  shards (`test-backend`, matrix `shard: [1..6]`) are about 60% of that; the SCM
  shards are 3 jobs, vitest 4.
- One push to main used to be 23 jobs (the 18 above plus build-images x3,
  build-and-deploy, notify-owner); main pushes replaying the full suite were
  about 18% of daily job-minutes.
- The `merge_group` trigger is wired, but the `protect-main-branch` ruleset has no
  merge-queue rule and no required status checks. The two comments in deploy.yml
  that said an unlabelled PR "cannot merge" were therefore false; both are
  rewritten in this lane.
- `npm run lint` on main today: 287 errors, 706 warnings (run in this lane's
  sandbox on f0880989). Lint is NOT added as a gate: it would block every PR on
  pre-existing errors. It stays a local command until the count is zero.

## Change

One file, `.github/workflows/deploy.yml`, no migration, no auth change. The
docs-only rule from #1370 is unchanged and applies to PR and queue runs.

### A push to main runs the alembic gate and nothing else

`changes` (and with it every test job) skips on `push`; `build-images`,
`build-and-deploy` and `notify-owner` require `workflow_dispatch`. A merge to main
is therefore one 20-second job, `Single alembic head (fast gate)`, so a fork of
the migration graph is still reported within a minute of the merge that caused
it. No test suite, no image, no deploy, no email.

### The release is a `workflow_dispatch` on main

Input `skip_tests` (boolean, default false). The run is:

1. `check-migration-heads`, `changes` (every area true; with `skip_tests` every
   area false, so the 16 test jobs skip), the full suite unless skipped.
2. `build-images` (three SHA-tagged images), in parallel with the suite.
3. `build-and-deploy`, `if: !cancelled()` plus explicit results (alembic gate and
   image build `success`, no need `failure` or `cancelled`; a skipped need is
   accepted because `skip_tests` is the only way a need skips on a dispatch),
   and now `environment: production`. GitHub holds the job before its first
   step until the owner approves it under the run's "Review deployments"
   button; a rejection fails the job. Everything that touches the server (the
   release-tag promotion, the scp, both ssh steps) is in this one job, so it is
   the only job that carries the environment. The `deploy-production`
   concurrency group stays: two releases dispatched close together deploy one
   after the other.
4. `notify-owner` mails the result (a failed gate skips the deploy and mails
   "failed", which is right: the release did not ship).

`build-images`, `build-and-deploy` and `notify-owner` also require
`github.ref == 'refs/heads/main'`: a dispatch on any other ref runs the suite
only. The environment's branch policy would refuse such a deploy anyway; the
condition keeps it from building and pushing images for a branch.

### Fast PR gate, full suite in the queue (unchanged from the first cut)

- `test-backend` (six shards) never runs on a `pull_request` event; it runs for a
  merge-queue entry and for a release.
- `changes` has an `scm` area flag. On a PR it is true when a path matches
  `SCM_RE`: `app/api/v1/scm/`, `app/services/scm/`, `app/modules/scm/`,
  `app/models/scm*`, `app/schemas/scm_*`, `tests/scm/`, an `alembic/versions/*scm*`
  migration, and the harness every backend test depends on (`tests/conftest.py`,
  `tests/_pg_fixture.py`, `tests/ci_excluded.txt`, `.test_durations`,
  `scripts/bootstrap_env.py`, `requirements.txt`), plus the workflow file. Off a
  PR it equals `backend`. `test-backend-scm` gates on `scm`. 54 non-scm backend
  files import scm services, so a change outside `SCM_RE` can still break an SCM
  test; the queue's full run is what catches that, by design.
- A `ci`-labelled PR runs: alembic head, Changed areas, Validate backend imports,
  Validate MCP imports (when mcp changed), Type-check frontend and the four
  vitest shards (when frontend changed), the three SCM shards (when `scm`
  changed). About 8 to 11 jobs and 50 to 80 job-minutes instead of 18 and 190.

### The two false comments

The trigger comment and the `check-migration-heads` comment now say: an
unlabelled PR shows no checks; whether that blocks a merge is the ruleset's
call, and as of 29 Sep 2026 it does not.

## How to trigger a release (orchestrator, after the owner has merged a batch)

```bash
# 1. Start the release on main's current head (full suite, then build, then deploy).
gh workflow run deploy.yml --repo jayson-odoo/sorento-crm --ref main

#    Or skip the suite when main's head was already validated (a green
#    merge-queue run on that exact commit): build + deploy only, ~15 job-minutes.
gh workflow run deploy.yml --repo jayson-odoo/sorento-crm --ref main -f skip_tests=true

# 2. Find the run and follow it.
gh run list --repo jayson-odoo/sorento-crm --workflow deploy.yml --event workflow_dispatch -L 1
gh run watch <run-id> --repo jayson-odoo/sorento-crm

# 3. When every gate is green the run pauses at "build-and-deploy" with
#    "Waiting for review". The OWNER approves it: run page > Review deployments
#    > tick production > Approve and deploy. Or, from the CLI as the owner:
ENV_ID=$(gh api repos/jayson-odoo/sorento-crm/environments/production --jq .id)
gh api -X POST repos/jayson-odoo/sorento-crm/actions/runs/<run-id>/pending_deployments \
  -F 'environment_ids[]='"$ENV_ID" -f state=approved -f comment="release <short sha>"
#    (state=rejected cancels the deploy; the run then reports failed and mails.)
```

The approval must come from the required reviewer (jayson-odoo); the
orchestrator's token cannot approve it. The pending approval waits up to 30 days
(GitHub's default); the alembic gate, the suite and the images are already done
by then, so approving later costs nothing.

## Ruleset changes the owner applies (Settings > Rules > Rulesets > `protect-main-branch`)

Apply after this PR merges, in this order.

1. **Require status checks to pass**: enable. "Require branches to be up to
   date before merging": OFF (the queue tests the merge result; ON would force a
   re-run of the fast gate on every base move). Source for every check: GitHub
   Actions, workflow "Build and Deploy Sorento". Add exactly these 19 names, as
   the jobs report them (a matrix leg reports `<name> (<shard>)`):

   | # | Required check name |
   |---|---|
   | 1 | `Single alembic head (fast gate)` |
   | 2 | `Changed areas` |
   | 3 | `Validate backend imports` |
   | 4 | `Validate MCP imports` |
   | 5 | `Type-check frontend` |
   | 6 | `Validate frontend (vitest) (1)` |
   | 7 | `Validate frontend (vitest) (2)` |
   | 8 | `Validate frontend (vitest) (3)` |
   | 9 | `Validate frontend (vitest) (4)` |
   | 10 | `Backend test suite - SCM (Postgres, xdist) (1)` |
   | 11 | `Backend test suite - SCM (Postgres, xdist) (2)` |
   | 12 | `Backend test suite - SCM (Postgres, xdist) (3)` |
   | 13 | `Backend test suite (Postgres, xdist) (1)` |
   | 14 | `Backend test suite (Postgres, xdist) (2)` |
   | 15 | `Backend test suite (Postgres, xdist) (3)` |
   | 16 | `Backend test suite (Postgres, xdist) (4)` |
   | 17 | `Backend test suite (Postgres, xdist) (5)` |
   | 18 | `Backend test suite (Postgres, xdist) (6)` |
   | 19 | `Backend test suite (Postgres, xdist) (serial)` |

   Row 19 joined with CI-SPEED (1 Oct 2026, PR #1426): the serial migration and
   `serial_ddl` steps moved off shard 1 onto their own matrix entry, so that leg
   must be required too or the queue stops gating them.

   A job skipped by its `if` reports `skipped`, which satisfies a required
   check, so on a PR the seven backend legs (and any area not touched) block
   nothing; in the queue they run for real and gate. A PR with no `ci` label
   reports none of these, so it cannot merge until labelled: that is the
   intent. Do NOT add `Build and push images`, `build-and-deploy`, `Notify owner
   by email` or `Release ci label (one-shot trigger)`.

2. **Require merge queue**: enable, with:
   - Merge method: **Squash and merge** (main's history is one squash commit per
     PR, `... (#NNNN)`).
   - Build concurrency: **1** (runners are scarce; one queue run at a time).
   - Minimum group size: **1**; maximum group size: **5**; wait time to meet
     minimum: **5 minutes**.
   - Only merge non-failing pull requests: **ON**.
   - Status check timeout: **60 minutes** (a full run is 25 to 35 minutes; an
     entry queued behind one in flight waits its turn).
3. Leave the existing bypass list (the owner's admin bypass) as it is.

Until the queue is on, PRs merge directly; the push to main still runs only the
alembic gate, and the release's full suite is the first time the six backend
shards run for that code.

## Operating notes

- Orchestrator flow for a PR is unchanged: add `ci` once, the run removes it;
  re-add to re-run on a new head. To merge with the queue on: "Merge when
  ready"; the queue's `merge_group` run is the full suite.
- A merge never deploys. Batch merges, then one release dispatch, then one
  owner approval.
- A hotfix is the same path: merge, dispatch, approve. There is no automatic
  route to the server any more.

## Tests

`sorento_crm_backend/tests/test_ci_docs_only_filter.py`, the same mechanism as
#1370: the `changes` step is extracted from the workflow and executed under
GitHub's bash flags with a stub `gh` that answers each endpoint from a canned
payload through the real `jq` filter; the jobs' `if` lines and the concurrency
expressions are evaluated as written, with a job table per event.

- `scm` flag: every `SCM_RE` path on a PR runs the SCM shards; non-scm backend
  paths do not; off a PR `scm` follows `backend`.
- `skip_tests`: honoured on a dispatch only (every area false, docs_only false);
  a stray value on a PR or queue run changes nothing; a dispatch without it
  counts every area even for a docs-only file list.
- Job table: a push to main runs `check-migration-heads` only, whatever the
  flags; a release runs the full suite then build + deploy + notify; a release
  with `skip_tests` runs build + deploy + notify only; a dispatch on another
  ref runs the suite and never builds or ships; a failed gate (8 variants)
  skips the deploy and still notifies; `skip_tests` with a failed build or
  alembic gate does not deploy; a merge-queue entry never deploys; PR scenarios
  (backend with and without scm paths, frontend + mcp, docs-only, another
  label) as before.
- `build-and-deploy` is asserted to carry `environment: production`, and no
  other job to carry an environment.

UAC: `ci-fast-gate-29sep-acceptance-criteria.md` alongside.
