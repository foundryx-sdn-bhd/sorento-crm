# PLAN: guard tests know #1718's projects tables (RELEASE-FIX-1017C)

Status: implemented (PR pending). Small fix track: tests only, no product code, no migration.

## Problem

Release run 39033958489 (main 63f1610f9), job "Backend test suite (Postgres, xdist) (serial)",
3 failed in `tests/test_migration_354_projects_schema_move.py`:

- `test_the_mapping_covers_exactly_the_models_that_declare_the_projects_schema` (model set has
  `sample_requests`, `sample_request_lines`; revision 354's `TABLES` does not)
- `test_upgrade_moves_and_renames_when_the_tables_are_in_the_default_schema` ("downgrade did
  not produce the pre-move schema": the two tables stay in `projects`, the expected set omits them)
- `test_the_index_rename_map_is_the_one_the_models_derive` (`KeyError: 'sample_requests'`)

SR-CORE (#1718, merged b303169fd) added `projects.sample_requests` (company-owned) and
`projects.sample_request_lines`, created straight into `projects` by `sr_0001_sample_requests`,
long after 354 moved the older tables. The PR gate runs only changed test files, so the
whole-tree guards that enumerate projects-schema models were never run on #1718.

The same cause fails three more tests in the xdist shards on a fresh bootstrap_env database
(local run, base 63f1610f9: 6 failed, 43 passed):

- `tests/test_projects_audit_entity_types.py` x2 (`KeyError: 'sample_requests'` /
  `'sample_request_lines'`), same `BORN_AFTER_THE_MOVE` list as the 354 test
- `tests/test_company_scope.py::test_every_company_id_table_is_registered`
  (`expected 164 owned tables, found 165`; `sample_requests` uses `CompanyScopedMixin`)

## Fix

- Add both tables to `BORN_AFTER_THE_MOVE` in the 354 test and the audit entity type test,
  with the comment naming #1718 and `sr_0001_sample_requests` (the lists' own convention).
  The hunks match crew/oi-line-split ce328fbcf byte for byte, so that lane's next merge of main
  takes them cleanly.
- `test_company_scope.py`: `expected_owned` 164 -> 165, with a `+ 1 (SR-CORE #1718)` line.
  crew/oi-line-split carries 165 -> 166 on top of its own `so_line_splits` bump; that line
  conflicts textually when it merges main, and the right resolution is its 166.

Migration 354 and `sr_0001` are untouched. The guards keep their intent: a table that declares
`projects` must be either in 354's `TABLES` or named here as born after the move.

## Verification

Serial (`-p no:xdist`) on a throwaway database built by `scripts.bootstrap_env`:
the three files above red on base, green after the fix; kill test by reverting the fix.
