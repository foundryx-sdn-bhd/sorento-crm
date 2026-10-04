# UAC: CUST-GROUP-SEED-REVIEW

Plan: `PLAN-customer-group-seed-review-3oct.md`.

- AC-1 [BE] `alembic upgrade` through `cust_group_0001` creates `customer_groups` and
  `customers.customer_group_id` and assigns NO customer to any group, even when a numbered
  family (`X SDN BHD [A/C I]`, `X SDN BHD [A/C II]`) exists. `customer_groups` holds 0 rows.
- AC-2 [BE] The revision id stays `cust_group_0001` with `down_revision` `picker_no_cap_0001`;
  one alembic head.
- AC-3 [BE] No name-matching group helper ships: no `seed` / `plan_groups` in the migration and
  no `scripts/customer_groups_seed_sql.py`.
