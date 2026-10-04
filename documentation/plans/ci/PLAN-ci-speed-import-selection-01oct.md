Status: small fix track (CI only, no product code, no migration) - in review (PR #1426)

# CI-SPEED: PR CI runs the tests that import what the PR changed; release shard 1 rebalanced

Owner approval: 1 Oct retro, decision 4.

## Measured facts (from crew's CI analysis, deploy.yml on main at 53c5f7af)

- A PR runs only the backend test files it changes (`BACKEND_TESTS_RE`, job `changes`), the SCM
  shards only on SCM paths (`SCM_RE`), and the six main shards never (`test-backend` carries
  `github.event_name != 'pull_request'`).
- 6 of 14 releases since 26 Sep failed at the full run (~40 min each). 1 Oct:
  `tests/test_ingest_parity_security_fixes.py` imports `app.services.shipping_order_ingest_service`
  (line 42) and `shipping_order_rules`, both changed by #1411 (123d4282c), and never ran on the PR.
- Release critical path is main shard 1: p50 34.3 / max 41.2 min vs 18-23 for shards 2-6. Its
  xdist step is 25.6 min vs 12.5 on shard 2. The serial migration and serial_ddl steps run only
  on shard 1 (+2.2 min).

## Change

1. `sorento_crm_backend/scripts/ci_select_tests.py` (stdlib only): given the PR's changed files,
   maps each changed `sorento_crm_backend/app/**.py` to its dotted module, adds every `app/`
   module that imports it (one level), and selects every test file that names any of those
   modules (`import app.x`, `from app.x import`, `from app.pkg import x`, or a dotted string
   such as a `mock.patch` target). Changed test files are always selected. Cap by time
   (crew ruling 1 Oct, option b): the selection's estimated CPU seconds from the committed
   `.test_durations` (summed per file; a file with no history counts at the median file) above
   3600 reports `full=true` instead of a list. The selected list is printed in the job log.
   #1411: 218 files, 4741 CPU-s on the refreshed file (2668 on the old one, which under-priced
   new tests), so it runs the full shards; either way `test_ingest_parity_security_fixes.py` runs
   on the PR.
   A changed `scripts/` file also matches tests that load it by path or bare name; a changed
   `tests/<dir>/conftest.py` selects that directory, and `tests/conftest.py` reports `full=true`.
2. `changes` checks out the backend tree (only when the PR changes backend code: `.py` under
   `app/`, `scripts/`, or a non-test helper under `tests/`) and
   runs the script. `backend_tests` becomes the selection; `backend_full=true` makes the six main
   shards and the SCM shards run on that PR (and the changed-files job steps aside).
3. `test-backend-changed` runs the selection like the shards: tests/scm and the rest in separate
   pytest sessions (a main-tree file importing `tests.scm.conftest` by name unregisters it as the
   tests/scm conftest in a mixed session: 18 errors on #1411's selection), each with xdist `--dist
   loadfile` for everything not `serial_ddl`, then `serial_ddl` serially; migration tests last.
4. Release shard rebalance. Measured on release 36875899060 (main 53c5f7af): shard 1 job 43.8 min
   (xdist 39.5 min, 5854 tests = all 5227 of tests/chatbot/ plus the start of tests/), shards 2-6
   xdist 12.8 to 20.3 min. Cause: the committed `.test_durations` (last touched 29 Sep) lacks 2686
   of shard 1's tests, which pytest-split prices at the 0.84 s average. Fix (the sandbox cannot
   download Actions artifacts, so measurements are local runs under the shard's flags): (i) all
   tests/chatbot keys from a local run x1.25; (ii) each shard region rescaled by its measured
   CI wall over modelled wall (run 36891698460); (iii) the 156 files (2856 tests) the file never
   held, local x1.55, with the CI-only 397-674 s setup of `test_sales_achievement_perf.py` priced
   at 535 s; (iv) a damped (square-root) per-region correction from run 36901464643. Verified by
   test-only dispatch runs 36891698460, 36895403885, 36901464643 and 36905481588; the last one
   ran every main shard in 15.8 to 22.7 min (critical path 22.7, was 43.8). And give the serial migration / `serial_ddl` steps
   their own `serial` matrix entry instead of shard 1. That leg is a new check name: row 19 of
   `PLAN-ci-fast-gate-29sep.md`'s required-check table, which the owner adds to the ruleset.

Not changed: caching, the production gate, image builds, release ordering.

## Tests

`sorento_crm_backend/tests/test_ci_select_tests.py` (red first): direct import, from-package
import, relative import, transitive one level (and not two), patch-string match, budget on both
sides of the threshold, median for unknown files, scm exclusion, CLI outputs, and #1411's real
diff selecting `tests/test_ingest_parity_security_fixes.py`. `tests/test_ci_docs_only_filter.py`
covers the `backend_code` output, the select step and the job gating on `backend_full`.
