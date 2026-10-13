# Mac mini CI runners (`sorento-mac`)

Two self-hosted GitHub Actions runners, `sorento-mac-1` and `sorento-mac-2`
(labels `self-hosted, Linux, ARM64, sorento-mac`), registered to
`jayson-odoo/sorento-crm` only. `deploy.yml` sends jobs here only when crew
says so: repo variables `RUNNER_TESTS` / `RUNNER_FAST`, or the dispatch input
`runner` (see the note above `jobs:` in `.github/workflows/deploy.yml`).
Image builds and the deploy always stay on GitHub-hosted (prod is amd64).

## Layout

- colima VM `ci`: 4 CPU, 6 GB RAM, 18 GB data disk, no host directories
  mounted. Docker inside it.
- `ci-guard.nft` (systemd `ci-guard.service` in the VM, required by Docker):
  containers reach the internet only. The Mac (lima's `192.168.5.2` is the
  Mac's localhost: dev Postgres, lane servers), the LAN and the tailnet are
  dropped.
- `compose.yml`: per runner, one Docker-in-Docker daemon and one runner
  sharing its network namespace. A job's Postgres/Redis service containers
  publish 5432/6379 on that runner's own localhost, so the two runners never
  clash. `job-started.sh` empties that Docker at the start of every job.

## Commands (on the Mac)

```bash
export DOCKER_HOST=unix://$HOME/.colima/ci/docker.sock
C="docker compose -f scripts/ci-runner/compose.yml"

scripts/ci-runner/up.sh     # first install / after an edit here: VM, guard, image, register once, start
$C stop runner1 runner2     # stop taking jobs (a running job is cancelled)
$C start runner1 runner2    # take jobs again
colima stop ci              # stop everything, frees the 6 GB
colima start ci             # start again; the guard and the containers come back by themselves
$C ps; $C logs -f runner1   # status / one runner's log
gh api repos/jayson-odoo/sorento-crm/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'
```

Start at login: copy `my.foundryx.colima-ci.plist` to `~/Library/LaunchAgents/`
and `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/my.foundryx.colima-ci.plist`.

Remove for good: `$C down -v` (drops the registrations' local state), delete
both runners under the repo's Settings > Actions > Runners, then
`colima delete ci`.
