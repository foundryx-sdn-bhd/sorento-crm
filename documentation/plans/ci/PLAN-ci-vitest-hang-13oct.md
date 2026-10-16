# PLAN: vitest shard 2 "hang" (CI-VITEST-HANG)

Status: PR open, awaiting review (small fix track: CI config only, no migration, no product code)

## Symptom

Release runs 38406697551 and 38415596343 (attempt 1): `Validate frontend (vitest) (2)` was
cancelled at the job's 20-min `timeout-minutes`, read as "vitest finishes but never exits".

## What the logs actually show (measured, not assumed)

| Run / job | Summary printed | Cancelled | vitest Duration |
| --- | --- | --- | --- |
| 38406697551 / 115328123227 | 13:59:24.68 | 13:59:24.89 | 1132.65s |
| 38415596343 / 115359112057 | 15:02:10.86 | 15:02:11.07 | 1130.92s |

The cancel lands 0.2s after the summary. There is no hang: vitest needed 18.9 min of the
20-min job (about 70s more go to checkout + `npm ci`). The other three shards in run
38415596343 took 17.7-18.8 min, so every shard sits at the cap and shard 2 is the one that
tips over. The rerun (attempt 2) of 38406697551 shard 2 passed in 12 min.

Per-file timing in the log: about 1.4s per file, back to back, including 10ms pure-function
`.ts` files. That is a single worker paying jsdom environment + setup per file in series
(`environment 291s, setup 153s` of the 1131s).

## Why it is not a hang

Vitest 4.1.1 `Vitest.exit()` (`node_modules/vitest/dist/chunks/cli-api.*.js`) arms an
unref'd `teardownTimeout` (10s default) timer that prints `close timed out after ...` and
calls `process.exit()`. A process that cannot exit after its summary is killed 10s later
with that warning. Neither cancelled log contains it. A `hanging-process` reporter or a
lower `teardownTimeout` would add nothing: the fail-fast on a real hang already exists.

## Fix

Vitest's default `maxWorkers` is `availableParallelism - 1`, which is 1 on the 2-vCPU
runner. `vitest.config.ts` now sets `maxWorkers: os.availableParallelism()` under `CI`
(the same switch `retry` already uses), so both CI vitest steps (the shards and the
`--changed` PR job) use both cores. Local runs keep the default.

Measured in the sandbox, `CI=true taskset -c 0,1` (two CPUs, as on the runner):

| Run | Workers | Duration | Result |
| --- | --- | --- | --- |
| `--shard=2/16` | default (1) | 227.1s | 95 files / 925 tests green |
| `--shard=2/16` | 2 | 149.5s | 95 files / 925 tests green |
| `--shard=2/4` (the CI shard) | 2 | 692.6s | 376 passed, 1 skipped (377 files, as in CI); exited 2s after the summary |

`timeout-minutes: 20` stays; its comment in `deploy.yml` now records the measured
shard time. No product or test code changes.
