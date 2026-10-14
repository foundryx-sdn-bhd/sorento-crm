#!/usr/bin/env bash
# ACTIONS_RUNNER_HOOK_JOB_STARTED: every job starts with this runner's own
# Docker empty. A job that died mid-way can leave its Postgres/Redis service
# containers holding ports 5432/6379, which would fail every later job here,
# and each run's Postgres leaves an anonymous data volume behind.
set -u
docker ps -aq | xargs -r docker rm -f >/dev/null
docker network prune -f >/dev/null
docker volume prune -af >/dev/null
exit 0
