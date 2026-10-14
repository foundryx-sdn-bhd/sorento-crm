# PLAN: deliveries and cancellations under each SO line (SO-DELIVERIES-VIEW)

Status: Build.
- Card answered by the owner on 14 Oct: Q1-Q5 all (a).
- Mock v1 note from the owner: put the history in a lightbox opened by an icon.
- Mock v2 (3c8b05149) approved by the owner on 14 Oct.
 Track: M+ (new section on an existing screen, plus one index-only
migration). Because of the migration it gets the dev-copy check and the security-reviewer (for
the permission-gated link).

- UAC: `so-deliveries-view-14oct-acceptance-criteria.md`
- Card: `CARD-so-deliveries-view-14oct.md`
- Mock: `documentation/mockups/SO-DELIVERIES-VIEW/index.html`

## Journey

On a sales order the owner clicks the truck icon on a line and a lightbox shows the DOs delivered against it and the
Cancel SO documents that cancelled it. When AutoCount ties a DO or a cancel to the SO and the
product but not to one line, it shows in the product's block "<SKU> on this SO, not tied to one
line", never under a guessed line.

## Base

The branch is `main`. Every column read here is already on main:

- `order_lines`: `sales_order_line_id`, `from_doc_no`, `from_doc_type`, `product_id`,
  `warehouse_id`, `quantity`;
- `orders`: `order_number`, `order_date`, `actual_delivery_date`, `order_status_id`,
  `is_cancelled`, `deleted_at`;
- `autocount_cancel_lines` / `autocount_cancel_docs`.

Neither #1633 nor DO-HISTORY-L's `claude/do-history-group-links` changes these columns. Their
rules (group = SO + product, no warehouse pick) are what this read already assumes. A crew-note
on #1633 records the read contract.

## Backend

1. **Migration** `sodv_0001_ol_from_doc_idx`. The revision id is at most 32 characters;
   `down_revision` is main's head at build time (`qlg_0001_drawing_attachment` today).
   - Index `ix_order_lines_company_from_doc_no` on `order_lines (company_id, from_doc_no)`.
   - Copy `alembic/versions/mig_idx_chunks_document_id.py`: an autocommit block, CONCURRENTLY,
     `lock_timeout` 30s, and an invalid leftover dropped then rebuilt. The downgrade drops it.
   - Add the same `Index(...)` to `OrderLine.__table_args__` (`app/models/order.py:577-596`) so
     `create_all` DBs get it.
   - Crew SQL at `crew/state/migrations/SO-DELIVERIES-VIEW.sql`:
     `CREATE INDEX IF NOT EXISTS ix_order_lines_company_from_doc_no ON order_lines (company_id, from_doc_no);`
2. **Service.** New module `app/services/scm/so_line_history.py`, one function
   `so_line_history(db, so) -> dict`. It is pure placement over three reads:
   - **(a) SO lines.** Already loaded by `SalesOrderService._get_or_404`
     (`sales_order_service.py:1251`). From them: `lines_by_product` (product id to SO line
     ids) and the set of this SO's line ids.
   - **(b) DO lines.** `OrderLine` join `Order`:
     - `OrderLine.company_id == so.company_id`, `Order.deleted_at IS NULL`,
       `Order.is_cancelled IS false`;
     - and either (`from_doc_no == so.so_number` AND `from_doc_type` IN ('SO') or null) OR
       `sales_order_line_id IN line_ids`.
     - Placement: a stored link to one of our lines goes under that line. Otherwise, a product
       with one line on the SO goes under that line, and a product with several lines goes to
       the product group. A product not on the SO is dropped.
     - Status name comes from `OrderStatus.status_name`, location from
       `Warehouse.warehouse_code`.
   - **(c) Cancel SO lines.** `AutocountCancelLine` join `AutocountCancelDoc`:
     - `kind == cancel_so`, the live-doc predicate `_live_doc()`
       (`autocount_cancel_ingest_service.py:262`, import it, do not copy it), same company.
     - And either `sales_order_line_id IN line_ids` (matched) or `match_reason == group_whole`
       with a candidate in `line_ids` (reuse `_names_any`, `autocount_cancel_ingest_service.py:272`, as `cancel_sources` does at
       `:1136`), or `from_doc_no == so.so_number AND match_reason == undetermined`.
     - Placement:
       - matched with a line id: under it, with qty;
       - `group_whole`: under each candidate line on this SO, qty None;
       - `undetermined`: to the group whose product's `product_code == item_code`, dropped if
         that product is not on several lines;
       - any other status or reason: dropped.
   - **Ordering.** DOs by (`order_date`, `order_number`, `line_sequence`); cancels by
     (`doc_date`, `doc_no`, `seq`).
   - **Output.**
     - `lines`: every SO line with its two lists, possibly empty.
     - `groups`: every product on two or more lines, with `sku`, `line_ids` and its two lists.
3. **Schema** in `app/schemas/scm_orders.py`:
   - `SoLineDeliveryRow`: `order_id`, `do_number`, `do_date`, `location`, `status`,
     `actual_delivery_date`, `qty`.
   - `SoLineCancelRow`: `doc_no`, `doc_date`, `location`, `match_reason`, `qty: Optional`.
   - `SoLineHistory`: `line_id`, `deliveries`, `cancels`.
   - `SoProductGroupHistory`: `sku`, `line_ids`, `deliveries`, `cancels`.
   - `SalesOrderDeliveries`: `lines`, `groups`.
4. **Route** `GET /scm/sales-orders/{so_id}/deliveries` in `app/api/v1/scm/sales_orders.py`,
   declared before `/{so_id}`-style collisions are an issue (it is a sub-path, so none).
   - `Depends(_READ)` and `response_model=SalesOrderDeliveries`.
   - It calls `SalesOrderService(db)._get_or_404` then `so_line_history`.
   - Company scoping is the same as the SO read: confirm in the test that an other-company SO
     gives 404.

## Frontend

1. **Service.** Add `getSalesOrderDeliveries(id)` to the SCM sales-order service, through
   `lib/api-client` with `extractApiError`. Find the module beside the existing
   `getSalesOrder`, and add the matching types.
2. **Hook.** `useSalesOrderDeliveries(id, enabled)` (react-query). `enabled` flips true the first
   time any line's lightbox opens and stays true, so the read happens once (AC-SD21). Refetch is the Try
   again button.
3. **Column.** A truck icon column, first in the Lines grid columns of `SalesOrderDetail.tsx`.
   - Copy the icon-only cell button already on this grid, the Remove line column
     (`SalesOrderDetail.tsx:1317-1337`): ghost `Button mode="icon" size="sm"`, an empty header,
     `aria-label` + `title` "Deliveries for <SKU>", and the `Truck` icon (already imported,
     `:24`).
   - Size 52, `enableSorting`/`enableHiding` false; present in view AND edit.
   - Click sets `deliveriesLineId`, the same pattern as `linksLineId`
     (`SalesOrderDetail.tsx:434`, `:1429`).
   - Saved column preferences put a new first column at index 0 and keep it visible
     (`mergeColumnOrder.ts:15-24`, `useListingColumnPreferences.ts:220-233`), so there is no
     listing-key bump.
4. **Lightbox.** `PlanRowDialog` (`app/(protected)/scm/components/PlanRowDialog.tsx:2104-2138`)
   with a new kind `deliveries`, titled "Deliveries" in `PLAN_ROW_DIALOG_TITLES` (`:105-118`).
   - It renders once at the bottom of the screen, beside the "Linked to" one
     (`SalesOrderDetail.tsx:2105-2115`), with `productCode` = sku and `productName`.
   - Its body is a new `app/(protected)/scm/sales-orders/[id]/components/SoLineHistoryBody.tsx`.
5. **Body.** `SoLineHistoryBody`:
   - The line figures strip (AC-SD20), then the Delivered and Cancelled blocks, plus the group
     block when the product has a `groups` entry.
   - Each table is a `DataGrid` with a fixed layout, explicit sizes, `listingKey` null and a
     Total footer, as in `PlanRowDialog.tsx`'s drill tables (`:446-506`).
   - DO status: `<Badge status={status}>` (`OrdersList.tsx:285-291`). Dates: `fmtDate`. Qty:
     `fmtInt`.
   - Matched-by words: export the map in `app/(protected)/scm/lib/autocountCancel.ts` and add
     `undetermined: 'not tied to one line'`. One map, shared with the tooltip.
   - DO link: `useHasPermission('order_management.orders.view')`. With it, a `Link` to
     `/order-management/orders/{order_id}`; without it, plain text.
   - Loading: `Skeleton` rows. Error: `Alert variant="destructive"` "Could not load
     deliveries." with an outline `Button` "Try again" calling refetch.
   - The empty texts follow the UAC wording exactly.

## Tests (tester writes these red first, from the UAC only)

**Backend:** `tests/test_so_line_history.py`, Postgres fixture (`tests/_pg_fixture.py`). Seed
your own chain, since CI's DB is empty.

1. One line of the product, unlinked DO line: under the line (SD1). The same DO line from
   another warehouse: still under the line (SD1).
2. Two lines of the product, DO line with a stored link to line 2: under line 2 (SD2).
3. Two lines, unlinked DO line: in the group, `line_ids` = both, under neither line (SD3).
4. Row fields and order: two DOs out of date order come back sorted; actual delivery null (SD4).
5. Left out (SD5): a voided DO, a deleted DO, a product not on the SO, another company's DO with
   the same SO number, and a DO naming another SO.
6. Cancel (SD6-SD9): `item` match under its line with qty; `group_whole` under each candidate
   line with qty None; `undetermined` in the group; a void doc, a vanished doc and a
   `cancel_po` all left out.
7. Shape (SD10): a line with nothing has empty lists. A product on two lines with nothing still
   has its group. A one-line product has no group.
8. Route (SD11): 200 with `scm.dashboard.view`, 403 without it, 404 for an unknown id.
9. Migration (SD12): after upgrade the index exists and is valid; re-running the upgrade body
   is a no-op. Follow the existing `test_migration_*` pattern.

**Frontend (vitest):** `SoLineHistoryBody.test.tsx` plus a `SalesOrderDetail` case.

1. The truck button ("Deliveries for <SKU>") opens the lightbox "Deliveries · <SKU>" with the
   line figures strip; closing works (SD20).
2. Opening two lines' lightboxes calls the service once (SD21).
3. The Delivered table rows, the Total, the status pill text, the dd/mm/yyyy date and "-" for no
   actual delivery (SD22).
4. Cancelled: "item", "every line of the item" with qty "-", and "not tied to one line" (SD23).
5. The group block heading "<SKU> on this SO, not tied to one line · 3 lines" in the lightbox of
   each line of the product; none for a one-line product (SD24).
6. A DO number is a link with the permission and plain text without it (SD25).
7. Loading skeleton; the error text plus Try again calls refetch (SD26).
8. No id string in the rendered text (SD28).

**Browser:** agent-browser at 1280 and 375 on the crew test copy, reached by sidebar from `/`,
on SO419866 and SO373923.

## Out of scope (owner answers)

- DO lines whose product is not on the SO (Q5 a). Trigger: the owner expects a substitution to
  show.
- A computed "delivered with no DO" row (Q3 a).
- DOs created in the CRM by `create_do_from_so` (`sales_order_service.py:2131-2211`): they carry
  no SO number or line link. Dev count: 0 DO lines with a header SO link.
