# Mac mini CI runner (`sorento-mac`)

One self-hosted GitHub Actions runner, `sorento-mac-1` (labels
`self-hosted, Linux, ARM64, sorento-mac`), registered to
`jayson-odoo/sorento-crm` only. `deploy.yml` sends jobs here only when crew
says so: repo variables `RUNNER_TESTS` / `RUNNER_FAST`, or the dispatch input
`runner` (see the note above `jobs:` in `.github/workflows/deploy.yml`).
Image builds and the deploy always stay on GitHub-hosted (prod is amd64).

## Layout

- colima VM `ci`: 4 CPU, 6 GB RAM, 16 GB root disk. No Mac directory is
  mounted and no VM port is forwarded to the Mac.
- The runner is the VM user `runner` (no sudo, not in the docker group) and
  its Docker is rootless, so a job, even in a `--privileged` container, never
  gets root in the VM.
- `ci-guard.nft` (systemd `ci-guard.service`, loaded before the network, and
  the runner does not start without it): everything the runner user opens,
  its rootless Docker's network included, reaches the internet only. The Mac
  (lima's `192.168.5.2` is the Mac's localhost: dev Postgres, lane servers),
  the LAN and the tailnet are dropped, and on the VM itself only loopback
  and DNS are open. The VM's own root processes are not limited; no job runs
  as root.
- A job's Postgres/Redis service containers publish 5432/6379 on the VM's
  localhost. `job-started.sh` empties the runner's Docker at the start of
  every job.
- One runner: a 6 GB VM holds one type-check (4 GB heap) or one xdist shard,
  not two.

## Commands (on the Mac)

```bash
scripts/ci-runner/up.sh                     # install / after an edit here: VM, provision, register once, start
REGISTER=0 scripts/ci-runner/up.sh          # provision only
colima ssh -p ci -- sudo systemctl stop sorento-runner    # stop taking jobs (a running job is cancelled)
colima ssh -p ci -- sudo systemctl start sorento-runner   # take jobs again
colima stop ci                              # stop everything, frees the VM's memory (up to 6 GB)
colima start ci                             # start again; guard, Docker and runner come back by themselves
colima ssh -p ci -- sudo journalctl -u sorento-runner -f  # runner log
gh api repos/jayson-odoo/sorento-crm/actions/runners --jq '.runners[] | [.name, .status, .busy] | @tsv'
```

Start at login: copy `my.foundryx.colima-ci.plist` to `~/Library/LaunchAgents/`
and `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/my.foundryx.colima-ci.plist`.

Remove for good: delete `sorento-mac-1` under the repo's Settings > Actions >
Runners, `launchctl bootout gui/$(id -u)/my.foundryx.colima-ci`, then
`colima delete ci`.
