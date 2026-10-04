# PLAN: DO-OWNERSHIP-GUARD - one explicit two-way field-ownership list (AutoCount DO vs Order Tracking)

Status: Review complete, awaiting owner hand test and CI (standard track: the owner rulings added manual-edit guards and a FE change; no
migration, no RBAC change, no new ingest surface). PR #1410.

crew-lane: DO-OWNERSHIP-GUARD
UAC: `do-ownership-guard-acceptance-criteria.md` (alongside).

## Journey

Once AutoCount DO ingest is live, CS keeps uploading the Order Tracking workbook and editing DOs
by hand. Neither may overwrite what AutoCount sends; AutoCount may never overwrite what Order
Tracking owns (delivery fields, Remarks CS, Type, and every field AutoCount never sends). Rows
AutoCount never sends (RMA, `doc_key IS NULL`) stay fully Order-Tracking-written.

## Verified state (main dfb288fda, paths under `sorento_crm_backend/`)

- Ownership is per row: `orders.doc_key IS NOT NULL`. No source column.
- Sheet direction: hard-coded `AUTOCOUNT_OWNED_MASTER_COLUMNS` (order_service.py:48-51), skipped
  at the Master upsert (:3057-3060). Overall Tracking columns are never AutoCount's.
- Ingest direction: implicit - whatever `_parse` + `_apply_do` put in the header dict
  (autocount_doc_ingest_service.py:304-335, 807-815).
- GAP: JSON `POST /orders/bulk-import` -> `bulk_import_orders` setattrs every key, no doc_key
  check (order_service.py:2707-2711).
- GAP: `update_order` and the order-line CRUD accept any edit of an AutoCount DO; `update_order`
  also re-points `customer_id` from the debtor text on every save.

## Decisions

1. **One registry**, `app/services/order_field_ownership.py`: `AUTOCOUNT_OWNED_ORDER_COLUMNS`,
   `ORDER_TRACKING_OWNED_ORDER_COLUMNS`, `SYSTEM_ORDER_COLUMNS`. Every `orders` column is in
   exactly one (test against the model), so a new column must pick an owner.
   - AutoCount: identity/provenance, every DO header field the ingest writes, `customer_id` and
     `sales_order_id` it resolves, and `discount_amount` (AutoCount never sends one, but every
     writer recomputes `total_amount = subtotal - discount + tax`, so editing it rewrites
     AutoCount's total).
   - Order Tracking: Remarks CS, Type, `estimated_delivery_date`, every Overall Tracking column,
     `order_status_id`, address ids.
   - The DO's **lines** are AutoCount's as a whole (the ingest reconciles the full set).
2. **Sheet import** skips `AUTOCOUNT_OWNED_ORDER_COLUMNS` on AutoCount rows. Same effective set
   as before (the Master mapping's AutoCount columns are exactly the old list), so no behaviour
   change.
3. **Ingest** calls `assert_autocount_writes(header)` before any write: a header key outside the
   AutoCount set fails that record (savepoint rolled back), never writes a tracking column.
4. **JSON bulk import**: on an AutoCount row, skips AutoCount keys and returns a per-row
   `warnings` entry naming them (`BulkImportResponse.warnings`, new).
5. **Manual edits** (owner ruling 1 Oct): `update_order` returns 409 `AUTOCOUNT_OWNED` naming
   every AutoCount field in the request ("<fields>: owned by AutoCount ..."); nothing is written.
   The debtor-text customer re-point is skipped on AutoCount rows. `POST /orders/{id}/cancel`
   goes through `update_order`, so cancelling an AutoCount DO by hand is a 409 too.
   Order-line create / update / delete / bulk-delete on an AutoCount DO: 409 naming `lines`.
6. **FE**: single-order GET/PUT stamps `autocount_owned_fields` (like `remarks_cs_locked`; empty
   on list rows, so the list payload and the MCP read do not grow). The edit form disables those
   fields with a "From AutoCount" hint (existing `disabled` + `FormDescription`, no new
   component) and leaves them out of the PUT; the lines card drops add/select/delete and shows
   "From AutoCount".
7. `estimated_delivery_date` source on AutoCount rows: **keep as is** (owner ruling 1 Oct).

## Not built / triggers

- `doc_date` tracking key is still only a year hint; nothing writes it. Out of scope.
- A per-field ownership source column: only if a row ever needs mixed ownership beyond doc_key.
- `DELETE /orders/{id}`, `DELETE /orders/bulk` and archive still remove an AutoCount DO with no
  ownership check (pre-existing; not an edit, so outside the 1 Oct ruling). A hard-deleted
  AutoCount DO is recreated by the next ingest without its Order Tracking fields; an archived
  one stays archived and keeps being updated in place. Owner ruling 1 Oct: leave as is (the
  owner deletes and re-flows from AutoCount when needed).
- Pre-existing: the order edit / line routes gate on `get_current_user` only (no
  `require_permission`). Unchanged by this lane.

## Overlap with #1408

#1408 was not merged at the time of writing; re-check `order_service.py` Master skip and
`autocount_doc_ingest_service._apply_do` on merge.
