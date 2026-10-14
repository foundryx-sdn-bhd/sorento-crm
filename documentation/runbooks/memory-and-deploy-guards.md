# Runbook: memory and deploy guards (HA-MEM-DEPLOY-GUARD)

Owner-run. Nothing here is automated, and nothing in the repo touches prod. Plan:
`documentation/plans/deploy/PLAN-deploy-memory-guard-14oct.md`.

Why:

- **10 Sep:** the blue/green overlap pushed the 16 GB CRM box out of memory. With no swap the
  kernel killed a Postgres backend, Postgres crash-recovered, and the pipeline still said success.
- **13 Oct:** a slow migration held a deploy.
- **26 Sep (shared service):** new code ran on the old schema after a failed migration.

What the code now does on every release (no action needed for these):

| Guard | Where | On failure |
| --- | --- | --- |
| Exclusive lock `/opt/sorento-crm2/.deploy.lock` for the whole deploy (the weekly restore test waits on the same file) | `scripts/blue_green_deploy.sh` | waits up to 20 min, then fails; nothing changed |
| Pre-migration snapshot (`pg_dump -Fc`, checked with `pg_restore --list`) | same | deploy stops before any migration; old colour keeps serving |
| Memory guard: `MemAvailable` must be at least 4500 MB before the new colour starts | same | deploy stops; old colour keeps serving |
| Postgres check: db container restarted, replaced, or crash-recovered in place | same, before the nginx swap and at the end | before the swap: new colour stopped, old keeps serving. At the end: the run goes red |
| `lock_timeout` 5s, `statement_timeout` 180s on every migration | `sorento_crm_backend/alembic/env.py` | migration fails, new colour never goes healthy, old keeps serving |
| `/health` body names Redis and scheduler state (status code still DB only); `/health/deep` returns 503 when DB, Redis or the scheduler is not OK | backend | `/health/deep` is for monitors only |
| `Rollback production` workflow | `.github/workflows/rollback.yml` | see step 7 |

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_ORIGIN>` | the CRM box (SSH in the way you always do) |
| `<CRM_DOMAIN>` | the CRM's public hostname |
| `<PROD_COMPOSE>` | the on-box compose: `/opt/sorento-crm2/docker-compose.yml` |
| `<RELEASED_SHA>` | a full 40-character main commit SHA whose release run succeeded |

All box steps run as the user that GitHub Actions SSHes in as (the deploy user), from
`/opt/sorento-crm2`, unless a block starts with `sudo`.

## When

| Step | When | Time |
| --- | --- | --- |
| 1. Find out whether swap was ever applied | before the next release | 2 min |
| 2. Swap 6 GB, swappiness 10 | before the next release | 5 min |
| 3. Confirm the db service name and the free-memory baseline | before the next release | 3 min |
| 4. `mem_limit` lines in the prod compose | next quiet window | 20 min |
| 5. `SKIP_MIGRATIONS` pass-through (makes rollback work across a migration) | with step 4 | 5 min |
| 6. Point a monitor at `/health/deep` | any time | 10 min |
| 7. Rollback (when needed) | on a bad release | 10 min |
| 8. Restore from a pre-migration snapshot (emergency only) | on a bad migration | 30 min |

## 1. Is swap already on?

The swap block in `documentation/plans/deploy/PLAN-deploy-memory-and-ci-speed.md` (lines 111-132)
may or may not have been applied.

```bash
swapon --show
free -h
cat /proc/sys/vm/swappiness
grep -n swapfile /etc/fstab
```

- **Check:** a `/swapfile` line of about 6G in `swapon --show`, swappiness `10`, and an fstab line
  means it is done: skip step 2.
- **Rollback:** nothing to roll back (read-only).

## 2. Swap 6 GB, swappiness 10

Needs about 6 GB free disk: check `df -h /` first (the plan measured 104 of 193 GB used).

```bash
sudo fallocate -l 6G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
```

- **Check:** `swapon --show` lists `/swapfile` at 6G; `cat /proc/sys/vm/swappiness` prints `10`;
  `free -h` shows `Swap: 6.0Gi`.
- **Rollback:** `sudo swapoff /swapfile && sudo rm /swapfile`, delete the `/swapfile` line from
  `/etc/fstab`, `sudo rm /etc/sysctl.d/99-swap.conf && sudo sysctl -w vm.swappiness=60`.

Swappiness 10 keeps the app in RAM in normal running; swap only takes the overflow of a spike,
so the box slows down instead of the kernel killing Postgres.

## 3. db service name and memory baseline

The deploy script checks the Postgres container by its compose service name, `db` by default,
and **fails before changing anything** if no such container is running.

```bash
cd /opt/sorento-crm2
docker compose ps --services | sort
grep MemAvailable /proc/meminfo
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
```

- **Check:** the service list contains `db`. `MemAvailable` (kB, divide by 1024 for MB) is above
  4500 MB with one colour running. Note each container's usage: step 4 sizes from it.
- If the Postgres service has another name, or you need a different threshold, create
  `/opt/sorento-crm2/.deploy.env` (the script sources it; it is not in git):

  ```bash
  cat > /opt/sorento-crm2/.deploy.env <<'EOF'
  # DB_SERVICE=postgres
  # DEPLOY_MIN_AVAILABLE_MB=4500
  # SNAPSHOT_KEEP_MINUTES=2880
  # DEPLOY_LOCK_WAIT_SECONDS=1200
  EOF
  chmod 600 /opt/sorento-crm2/.deploy.env
  ```

  Uncomment only what you change.
- **Rollback:** `rm /opt/sorento-crm2/.deploy.env` (the defaults apply again).

If a release fails with `memory guard: N MB available, below DEPLOY_MIN_AVAILABLE_MB`, nothing
changed on the box. Find what is using memory (`docker stats --no-stream`), and either free it or,
once swap is on and you accept the risk, lower `DEPLOY_MIN_AVAILABLE_MB` in `.deploy.env`, then
dispatch the release again.

## 4. `mem_limit` lines in the prod compose

A limit turns "the whole box goes into the OOM killer" into "one container is restarted". Sizes
come from the 10 Sep measurements in `PLAN-deploy-memory-and-ci-speed.md` (one colour: backend
4.7 GB before gunicorn preload, preload target under 3 GB; db 2.0 GB; frontend 0.67 GB; redis
0.53 GB; worker + worker_fast 0.3 GB) plus headroom. **Re-check against your step 3 numbers:**
every limit must be at least 1.5 times what that container uses today. If the backend colour
still uses more than 2.3 GB, use `5g` for both backend colours instead of `3500m`.

| Service | `mem_limit` | Why |
| --- | --- | --- |
| `db` | `4g` | 2.0 GB measured; the pre-migration `pg_dump` runs inside this container too |
| `backend_blue`, `backend_green` | `3500m` | preload target under 3 GB; a worker over the limit is killed and gunicorn respawns it |
| `frontend_blue`, `frontend_green` | `1g` | 0.67 GB measured |
| `worker` | `2g` | imports and catalogue PDF renders (Chromium) spike above the idle 0.3 GB |
| `worker_fast` | `1g` | same image, lighter queues |

The limits are ceilings, not reservations: during the blue/green overlap their sum can exceed
16 GB. The memory guard (step 3) and swap (step 2) cover that window.

Edit `<PROD_COMPOSE>` (keep a copy first) and add one line per service, at the same indent as
`image:`:

```bash
cd /opt/sorento-crm2
cp docker-compose.yml "docker-compose.yml.bak-$(date +%Y%m%d)"
```

```yaml
services:
  db:
    mem_limit: 4g
  backend_blue:
    mem_limit: 3500m
  backend_green:
    mem_limit: 3500m
  frontend_blue:
    mem_limit: 1g
  frontend_green:
    mem_limit: 1g
  worker:
    mem_limit: 2g
  worker_fast:
    mem_limit: 1g
```

```bash
docker compose config --quiet && echo compose-ok
```

Apply without restarting anything: the backend colours, frontends and workers pick the limit up
when the next release recreates them. For the running `db`, set the same limit live (no restart;
`--memory-swap` is twice the limit, which is what compose gives a `mem_limit` by default):

```bash
docker update --memory 4g --memory-swap 8g "$(docker compose ps -q db)"
```

- **Check:** `compose-ok` printed; `docker inspect --format '{{.HostConfig.Memory}}' "$(docker compose ps -q db)"`
  prints `4294967296`; after the next release, `docker stats --no-stream` shows a `LIMIT` column
  matching the table for every listed container.
- **Rollback:** `cp docker-compose.yml.bak-<date> docker-compose.yml`, and for the db:
  `docker update --memory 0 --memory-swap -1 "$(docker compose ps -q db)"` (0 = no limit).
  Mirror the same edit into the local gitignored copy (`sorento_crm/docker-compose.yml` on your
  machine) so the two do not drift.

## 5. `SKIP_MIGRATIONS` pass-through (for rollbacks across a migration)

The rollback workflow runs the deploy script with `SKIP_MIGRATIONS=1`, because an older image's
`alembic upgrade head` does not know a newer revision and would fail. The variable only reaches
the container when the compose passes it through. In `<PROD_COMPOSE>`, under **both**
`backend_blue` and `backend_green`, in the `environment:` list (create it if missing):

```yaml
    environment:
      SKIP_MIGRATIONS: ${SKIP_MIGRATIONS:-0}
```

A normal release does not set it, so migrations still run. Note: this line wins over any
`SKIP_MIGRATIONS` in the backend `.env`; for a manual expand-contract rollout, set it in the
shell instead (`SKIP_MIGRATIONS=1 docker compose ...`).

- **Check:** `docker compose config | grep -n SKIP_MIGRATIONS` shows `SKIP_MIGRATIONS: "0"` under
  both colours.
- **Rollback:** delete the two lines. A rollback across a migration then fails its health check and
  the current colour keeps serving (safe, just not useful).

## 6. A monitor on `/health/deep`

`/health` stays the blue/green readiness probe and still answers 200 when only the database is up.
Its body now also carries `"redis": "ok"|"down"|"timeout"|"error"` and
`"scheduler": {"status": "ok"|"overdue"|..., "overdue_count": N, "last_task_run_at": ...}`.
`/health/deep` answers 503 unless the database, Redis and the scheduler are all OK. "Overdue"
uses the same rule as the admin health dashboard: an enabled scheduled task late past its grace.

On the box, against the live colour (`cat .active_color`: blue is 8000, green is 8010):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health/deep
curl -s http://127.0.0.1:8000/health/deep
```

- **Check:** `200` and `"status":"healthy"`. A `503` names the failing part.
- For an outside monitor, the host nginx needs a route for it on the API host, e.g. in the server
  block that proxies `/api/v1`: `location = /health/deep { proxy_pass http://sorento_backend; }`,
  then `sudo nginx -t && sudo nginx -s reload`, and point the monitor at
  `https://<CRM_DOMAIN>/health/deep`, alerting on anything but 200.
- **Rollback:** remove the `location` block, `sudo nginx -t && sudo nginx -s reload`.

## 7. Rollback a bad release

Code only: the database stays at its current schema (see step 8 for data).

1. Find the last good release: GitHub, Actions, "Deploy", filter `event: workflow_dispatch`, branch
   `main`, a green run before the bad one. Copy its full commit SHA (`<RELEASED_SHA>`).
2. Actions, "Rollback production", "Run workflow", branch `main`, paste `<RELEASED_SHA>`, Run.
3. Approve it under "Review deployments" (the same `production` gate as a release). It queues
   behind any running release.

The workflow refuses a SHA that is not 40 hex characters, not on main, has no successful release
run, or whose images are gone from the registry. It then runs today's `blue_green_deploy.sh` (all
guards above, snapshot included) with `IMAGE_TAG=<RELEASED_SHA>` and `SKIP_MIGRATIONS=1`.

- **Check:** the run is green, and on the box
  `docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q backend_$(cat .active_color))"`
  ends in `<RELEASED_SHA>`.
- **Rollback:** dispatch "Rollback production" again with the newer SHA, or dispatch a release
  from main.

## 8. Restore from a pre-migration snapshot (emergency only)

Every release writes `/opt/sorento-crm2/backups/pre-migrate/pre-migrate-<UTC time>-<sha>.dump`
(owner-only permissions) before the new colour migrates, and deletes ones older than 48 h.
**Restoring loses every write since the snapshot.** Use it only when a migration damaged data and
a code rollback cannot fix it. Rehearse into a scratch database first:

```bash
cd /opt/sorento-crm2
ls -lt backups/pre-migrate/ | head
F=backups/pre-migrate/<file>.dump
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" restore_check'
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_check --no-owner' < "$F"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d restore_check -Atc "select version_num from alembic_version"'
```

- **Check:** the last command prints the alembic revision from before the release.
- **Rollback (of the rehearsal):** `docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" restore_check'`.

The real restore (stop writers first: both backend colours, `worker`, `worker_fast`; then
`pg_restore --clean --if-exists -d "$POSTGRES_DB"` from the same file; then roll the code back with
step 7) is a decision to take with crew, not from this page alone.

If a release fails with `pre-migration snapshot failed`, nothing migrated. Check disk
(`df -h /opt/sorento-crm2`) and the db logs (`docker compose logs --tail=50 db`), then dispatch
the release again.

## When a release fails on a guard

| Message in the "Deploy to Server" log | Meaning | Do |
| --- | --- | --- |
| `.deploy.lock is still held` | another release or the restore test ran for 20 min | wait for it, dispatch again |
| `Postgres container 'db' not found` | service name differs | step 3 |
| `pre-migration snapshot failed` | dump could not be written or checked | step 8, last paragraph |
| `memory guard: N MB available` | not enough RAM for a second colour | step 3, last paragraph |
| `Postgres ... restarted` / `crash-recovered` before the swap | Postgres went down while the new colour started | old colour still serves; `docker compose logs --tail=200 db`, `dmesg -T \| grep -i oom`, then dispatch again |
| `deploy FAILED: Postgres went down during the deploy` | same, after the swap | the new colour serves; check the app, then the same logs |
| `canceling statement due to lock timeout` in the backend log | a migration waited more than 5s for a table | find the long transaction (`pg_stat_activity`), end it, dispatch again |
| `canceling statement due to statement timeout` | a migration statement ran more than 180s | the migration must raise its own limit (see `app/utils/migration_timeouts.py`), or set `MIGRATION_STATEMENT_TIMEOUT` in the backend `.env` for one release |
