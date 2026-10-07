# PLAN: DBR debtor cleanup

Status: BUILDING (Q2-Q5 answered 7 Oct, Q1 chat links pending owner). Track: L (prod data delete), LEAD pattern.
Lane: crew/dbr-debtor-cleanup. UAC: `dbr-debtor-cleanup-acceptance-criteria.md`.

## Journey

Staff upload a DO sheet or key a DO whose debtor code is blank (SAMPLE, STOCK TRANSFER, TEMPERORY BILL ...).
The DO shows its debtor name. No fake `DBR-` customer appears in Customers, and filtering by a real
customer never sweeps in these DOs because their name happens to match.

## Slices (pending answers; written on the recommended options)

1. Stop minting DBR customers. `_upsert_customer_from_debtor` (`sorento_crm_backend/app/services/order_service.py:2264-2299`)
   returns None for a blank code. `_sync_order_master_refs` (`:2329`) must then write `customer_id = None`
   explicitly so update (`:2466-2474`) clears an existing DBR id (GAP 1). AutoCount-owned DOs untouched.
   `scripts/backfill_order_master_refs.py:82` follows automatically.
2. Customer filter / dealer scope match by `customer_id` only: drop the legacy debtor-name branch
   (`order_service.py:928-949`, `1559-1576`, `1699-1712`, `1869-1885`) [Q4].
3. Name text fallback where a reader shows or searches the customer name only [Q5]:
   outstanding report DO side (`services/outstanding_report_service.py:503-509,538-541`),
   delivery-order-lines dataset Customer column (`services/reports/datasets/delivery_order_lines.py:80,172`),
   export quick search (`services/list_query_export_service.py:167-182`). Display/search only, never a link.
4. Data script `sorento_crm_backend/scripts/cleanup_dbr_customers.py`: dry run default (prints counts + rows),
   `--apply` in one transaction: `orders.customer_id = NULL` for DOs on DBR customers;
   `stock_asks.customer_id = NULL` [Q2]; delete DBR customers (cascades chat links [Q1]). DBR = `customer_code ~ '^DBR-[0-9a-f]{10}$'`
   AND no `integration_references` row. Prints refusal if any other FK table references a DBR row.
   Never run from the lane; crew runs on prod after release with owner go.

No migration (`orders.customer_id` nullable; DB FK is NO ACTION, script nulls first). No UI change.

## Tests (tester, red first)

- create / update / sheet import with blank debtor code -> `customer_id` NULL, no new customer row, debtor_name kept.
- update of a DO that holds a DBR id with blank code -> id cleared.
- non-blank code still links / creates by code (regression; existing `tests/test_debtor_code_unique.py`).
- customer filter on a customer named "SAMPLE" does not return customer-less "SAMPLE" DOs; dealer scope same.
- outstanding report / DO-lines dataset show debtor name for a customer-less DO; export quick search finds it by debtor name.
- script: dry run changes nothing; apply nulls DOs + asks, deletes only DBR rows, leaves a non-DBR customer and a
  DBR-looking code with an integration_reference alone.

Existing tests that assert the old blank-code behaviour (`tests/test_debtor_code_unique.py:225-226`,
`tests/test_order_master_ref_upsert.py:65-66`) are rewritten by the tester to the new rule.
