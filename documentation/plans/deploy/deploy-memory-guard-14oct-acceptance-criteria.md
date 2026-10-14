# UAC: deploy memory guard, migration timeouts, rollback (HA-MEM-DEPLOY-GUARD)

Plan: `PLAN-deploy-memory-guard-14oct.md`. Runbook: `documentation/runbooks/memory-and-deploy-guards.md`.
Every row is proven by a test in `sorento_crm_backend/tests/` (named) unless marked owner.

| # | Criterion | Proof |
| --- | --- | --- |
| 1 | The deploy refuses to start the new colour when MemAvailable < `DEPLOY_MIN_AVAILABLE_MB` (default 4500), exits non-zero with a message naming both numbers, and starts nothing | `test_blue_green_deploy_guards.py::test_memory_guard_*`, `test_unreadable_meminfo_fails_closed` |
| 2 | The threshold is env-tunable, also from an on-box `.deploy.env` | `test_memory_threshold_is_env_tunable`, `test_memory_threshold_can_be_set_on_the_box_in_deploy_env` |
| 3 | The pipeline fails when the db container restarted or was replaced during the deploy, and when Postgres crash-recovered in place (10 Sep shape) | `test_db_container_restart_*`, `test_postgres_crash_recovery_*`, `test_postgres_down_after_the_swap_*`, `test_missing_db_container_*` |
| 4 | A Postgres incident before the nginx swap stops the new colour and never swaps | same tests (`_swapped` is False) |
| 5 | A checked `pg_dump` snapshot is written before the new colour (and so `alembic upgrade`) starts; on failure the deploy stops before migrating; snapshots older than 48 h are pruned, others kept; files are 0600 | `test_healthy_deploy_snapshots_*`, `test_failed_snapshot_*`, `test_snapshots_older_than_48h_*` |
| 6 | The deploy holds an exclusive flock on `DEPLOY_LOCK_PATH` (default `/opt/sorento-crm2/.deploy.lock`, overridable by env or `.deploy.env`) for its whole run, waits then fails without touching anything when it is held, and releases it on success, failure, and when a child inherited the fd | `test_the_lock_*`, `test_a_held_lock_*` |
| 7 | Migrations run under `lock_timeout` 5s and `statement_timeout` 180s by default, env-overridable, per-migration overridable without leaking to the next migration; a blocked lock fails instead of queueing; malformed values are refused | `test_migration_timeouts.py` (real env.py, throwaway DB) |
| 8 | `/health` reports Redis and scheduler state, keeps its status code on the database only, and is not slowed by a hanging probe | `test_health_deep.py::test_health_*` |
| 9 | `/health/deep` returns 503 when the database, Redis or the scheduler is not OK, with no error text | `test_health_deep.py::test_deep_*`, `test_scheduler_state_*` |
| 10 | `rollback.yml`: workflow_dispatch with a SHA, `environment: production`, the deploy queue, no new secrets, the SHA validated (40 hex, on main, a successful release, images exist) and never interpolated raw, the same script and endpoint check | `test_rollback_workflow.py` |
| 12 | A non-numeric knob fails closed; memory is checked before the dump; the dump needs free disk of twice the newest dump and at least `SNAPSHOT_MIN_FREE_MB`; output without the `PGDMP` magic is refused; stale `.partial` files are pruned; ordinary Postgres log lines and crash lines from before the deploy do not fail it | `test_a_non_numeric_knob_*`, `test_memory_is_checked_before_the_snapshot`, `test_low_disk_*`, `test_disk_must_hold_*`, `test_output_that_is_not_*`, `test_stale_partial_*`, `test_ordinary_postgres_log_lines_*`, `test_a_crash_before_*` |
| 13 | `/health` failure body carries no error text; probes refresh at most once per 10s per process | `test_health_failure_names_no_host`, `test_a_flood_of_deep_checks_*`, `test_the_cache_expires` |
| 11 | Owner: swap 6 GB / swappiness 10, `mem_limit` lines, SKIP_MIGRATIONS pass-through, `/health/deep` monitor | owner, runbook steps 0-6 |
