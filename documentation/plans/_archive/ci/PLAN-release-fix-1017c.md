# PLAN: guard tests know #1718's projects tables (RELEASE-FIX-1017C)

Status: implemented (PR #1763). Small fix track: tests plus one product line (`REVISABLE_PORTAL_FORM_TYPES`), no migration.

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

## The rest of the release run (crew widened the lane)

The full run also failed three tests with other causes:

- `tests/test_portal_revision_config_routes.py::test_get_lists_one_entry_per_portal_type_even_with_no_rows`:
  #1718 added `sample_request` to `GRANTABLE_PORTAL_FORM_TYPES`, so the revision-config list
  offered a row for it. The ROUTE is wrong, not the test: `sample_request` has no revision
  engine (no adapter in `app/services/portal_revision_service.py`; the revise route refuses it,
  `app/api/v1/public/portal.py` `_check_revisable_kind`). Excluded from
  `REVISABLE_PORTAL_FORM_TYPES` like `sales_opportunity`; the not-listed / not-puttable test now
  covers both kinds (red first).
- `tests/test_oi_project_label_from_so.py::test_summary_matrix_and_export_all_honour_the_project_filter`:
  #1748 prints the OI Excel as one `ORDER INQUIRY` sheet; the test opened `JAN 26`. Same layout,
  so only the sheet name changes; the project-filter assertion stands.
- `PurchaseRequestForm.round1b.test.tsx` "round 2b narrow cell" (vitest shard 2, all three CI
  attempts): the load-time sentence comes from an async `form.trigger()` in the form's load
  effect while the Update button exists from the first render, so a sync `getByText` after
  `findByRole('Update')` raced it. Both such reads now `findByText`.

Company scope: crew asked whether `sample_requests` is registered. It is (`CompanyScopedMixin`,
`app/models/sample_request.py`); the offenders half of the guard passed and only the count
tripwire failed. A new test in `tests/test_sample_requests_core.py` pins that another company's
scope gets a 404, no list row and no ORM row.

## Verification

Serial (`-p no:xdist`) for the 354 test, `-n 2` otherwise, on a throwaway database built by
`scripts.bootstrap_env`: every failing test red on base, green after the fix, and each fix
kill-tested (revert the guard lists; skip `SampleRequest` in the scope filter; export with no
project; defer or drop the load-time trigger).
