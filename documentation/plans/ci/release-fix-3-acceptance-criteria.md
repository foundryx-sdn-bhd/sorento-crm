# UAC: RELEASE-FIX-3

1. `pytest -p no:xdist tests/test_migration_*.py` (the release's serial set) passes in one run,
   fin_0001 + fin_0002 + frc_0001 included, in file order.
2. No migration test pins an alembic head or a date literal; the single-head check stays.
3. A contact read of any of the six promotion routes with NO `access_levels`:
   - contact holds access types: sees exactly what those levels allow;
   - contact holds none, or the id resolves to no contact: sees nothing;
   - a brand-scoped contact sees only in-scope products; an unscoped one sees all brands.
4. A contact read whose `access_levels` names resolve to nothing still sees nothing.
5. A staff read (no `contact_id`) is unchanged: no access filter.
6. All of `tests/chatbot/test_sec_promo_access.py` and `tests/test_brand_scope_round2.py` pass.
