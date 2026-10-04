# PLAN: cust_group_0001 without the name-matched seed (CUST-GROUP-SEED-REVIEW)

Status: PR #1451 in CI. Track: small fix.
UAC: `customer-group-seed-review-acceptance-criteria.md`.

## Why

Owner ruling 3 Oct 2026 (option b): no automatic name-matching joins, explicit links only.
`cust_group_0001` (#1441) ran `seed()` on upgrade and joined customers into `customer_groups`
by `ledger_family_key` name similarity. It is not on prod yet (prod head `grn_pull_0001_perm`).

## Change (PR #1451)

- `sorento_crm_backend/alembic/versions/cust_group_0001.py`: `seed()`, its call in `upgrade()`
  and the `plan_groups` helper removed; DDL unchanged; revision id and `down_revision`
  unchanged so dev, which already ran the old body, stays at the same head (its seeded rows are
  left as they are).
- `scripts/customer_groups_seed_sql.py` (name-matched seed / rename SQL printer) deleted, and
  with it `shared_bracket_label` / `_bracket_runs` in `app/services/ledger_family.py`, which
  nothing else used.
- Test: `tests/test_customer_groups_seed.py` runs `upgrade()` under an alembic `Operations`
  context on the blank Postgres schema with the table and column dropped first; both are
  created and no customer is grouped. Red against the old body.

## Seeding: none (owner option (c), 3 Oct 2026)

No proposal SQL, apply script or import. The office sets groups by hand with the bulk
"Set customer group" action in CUSTOMER-BULK-OPS.
