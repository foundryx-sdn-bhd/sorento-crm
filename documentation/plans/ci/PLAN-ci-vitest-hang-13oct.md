# PLAN: vitest shard 2 "hang" (CI-VITEST-HANG)

Status: in progress (small fix track: CI config only, no migration, no product code)

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

## Fix

To be filled in from local measurements (see below).
