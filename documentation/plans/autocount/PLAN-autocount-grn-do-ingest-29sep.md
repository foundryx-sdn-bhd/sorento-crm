# PLAN: AutoCount Delivery Order and Goods Receive Note ingest, branch table, PO/SO line links (#1354, S2)

Status: **S2 built, in review on PR #1356** (full track: migration, new external ingest surface;
reviewer and security-reviewer rounds folded in). Branch `claude/autocount-grn-do-ingest-yi1w42`
(the session's designated branch; the brief named `feat/autocount-grn-do-ingest`), cut from
`origin/main` `b9552578e2649440931a3cf5a5ce998c8c640300`, merged with `0709a3f8`.
UAC: `autocount-grn-do-ingest-29sep-acceptance-criteria.md` (AC-AG001 onward).

Sources: issue #1354 (owner ask, vendor API list), the scout report
(`scout/autocount-grn-do` 27e4b1a3, `SCOUT-autocount-grn-do-29sep.md`), the owner's rulings
comment and the orchestrator's payload inspection on the issue. This slice is CRM only: the
shared-service HTTP source, cursor and sink (S1, S4) are another repo and another lane. The CRM
never calls any AutoCount API; it only consumes pushes.

Paths: `be/` = `sorento_crm_backend/`.

## 0. The ask and the rulings, in the owner's words

> "just got a list of API from the autocount ... think the useful one will be just GRN and DO for
> now, need to learn how we did for products, stock balance, CRM side need to have a pull from
> autocount button for verification, and also ingestion endpoint for automated push from shared
> service after pull is verified ... the DO is abit tricky also cause yeah DO has a lot of field
> and we need to store pretty much everything there ya, including the branch name, also the
> reference to the sales order line also need to store, the GRN the reference to SPO/PO also need
> to store ... basically we need to link the DO back to the SO and GRN back to the PO / SPO, the
> uploading of order tracking is still needed after the integration cause only the order tracking
> stores the transporter, actual delivery time and date, driver, lorry number etc i believe"

Rulings (29 Sep, verbatim per item):

- V1: "on that day, in malaysia timezone" - byLastModified returns documents modified ON that
  day; LastModified is Malaysia time.
- V2: "no i don't think there is pagination"
- V4: "SO and PO we will use DB transfer just like now first"
- V5: "just now includes edits, within 1 hour, but i think we should use LastModified"
- V6: "i don't think so" (no way to see a deleted document)
- V7: "BranchCode" and "i think we need to pull the branches in to sorento crm as well"
- V9: db1 is "Sorento"
- V11: "i don't think so" (one DO line never comes from several SO lines)
- Q1 "a": typed columns for what the CRM uses plus the full record in one JSON column.
- Q2 "crm need to have branch table" (overrules the scout): a CRM branch table fed from
  `branchbypage`.
- Q3 "a": the DO lands on the existing delivery order tables `orders` / `order_lines`.
- Q4 "a": the GRN lands on the existing `picking_headers` / `picking_lines`.
- Q5 "shared service": the shared service holds the push switch. The CRM records nothing.
- Q6 "a": backfill from 1 Jan 2023 (shared-service configuration; the CRM has no date floor).
- Q11 "a": SO delivered quantity keeps coming from the SO feed; DOs do not recompute it.
- Q12 "1 hour": hourly byLastModified poll on yesterday and today; JustNow is not used.
- Q7, Q8, Q9, Q10: no answer, the scout's recommendations stand (Q8 a: the tracking upload may
  create the DO first and the push adopts it by number; Q10 a: an unlinked line lands unlinked).

Payload facts (orchestrator, live payloads): LastModified carries the full timestamp
(`2026-07-27T15:37:53.367`), no pagination (plain arrays), and **no link fields**: no
`FromDocType` / `FromDocNo` / `FromDocDtlKey` on any line, `RefDocNo` empty on every header,
`OurPONo` null on every GRN line, `YourPONo` empty on every DO line, `BranchCode` empty on all
seven documents.

## 1. Contract 2.7 (the text the shared-service lane implements)

This section is copied verbatim into `PLAN-autocount-cross-repo-contract.md` section 13.

### 1.1 Doors

| Call | Slug (existing, except the new `order_management.branches.*`) | Body |
| --- | --- | --- |
| `POST /api/v1/external/ingest/delivery_orders` (`?dry_run=true` optional) | `order_management.orders.edit` | `{"companyCode", "book", "records": [DO, ...]}` |
| `POST /api/v1/external/ingest/goods_receive_notes` (`?dry_run=true`) | `procurement.grn.edit` | `{"companyCode", "book", "records": [GRN, ...]}` |
| `POST /api/v1/external/ingest/branches` (`?dry_run=true`) | `order_management.branches.edit` (new) | `{"companyCode", "book", "records": [Branch, ...]}` |
| `POST /api/v1/external/ingest/delivery_orders/deletions` (`?dry_run=true`) | `.edit` + `order_management.orders.delete` | `{"companyCode", "book", "doc_date_from", "doc_date_to", "doc_keys": [..]}` |
| `POST /api/v1/external/ingest/goods_receive_notes/deletions` (`?dry_run=true`) | `.edit` + `procurement.grn.delete` | same |
| `POST /api/v1/external/read/delivery_orders` | `order_management.orders.view` | `{"companyCode", "source_refs": ["db1:DO:55120", ...]}` |
| `POST /api/v1/external/read/goods_receive_notes` | `procurement.grn.view` | `{"companyCode", "source_refs": ["db1:GRN:771", ...]}` |

`branches` has no read and no deletions door (404 `UNKNOWN_ENTITY`; its `.view` / `.delete` slugs exist only so every permission map covers every entity); trigger for adding them: a
branch deleted in AutoCount that must disappear from the CRM.

`GET /api/v1/external/contract` answers `"version": "2.7"` and lists the three entities.

### 1.2 Envelope

- `companyCode`: the company anchor, unchanged (422 `COMPANY_ANCHOR_REQUIRED` /
  `UNKNOWN_COMPANY` / `COMPANY_ANCHOR_AMBIGUOUS` / `COMPANY_BINDING_INVALID`).
- `book`: the AutoCount company book the records came from, the path segment of the vendor URL
  (`db1` = Sorento, ruling V9). Required on the three entities; 1 to 20 characters of
  `[A-Za-z0-9_-]`, else 422 `INVALID_BODY`.
- `records`: up to 1000 (413 `BATCH_TOO_LARGE` above). A body without a `records` array is 422
  `INVALID_BODY`.

### 1.3 Record = the AutoCount record exactly as the vendor API returned it

The shared service does NOT map fields. Each record is the header object from
`deliveryorderbyLastModified` / `...bydocdate` (or the GRN equivalents), with its `Details`
array, as returned. Unknown keys are accepted and kept: the whole record is stored as sent in
one JSON column (`source_record`, ruling Q1). The CRM reads these keys:

- DO header: `DocKey`, `DocNo`, `DocDate`, `DocStatus`, `Cancelled`, `BranchCode`,
  `DebtorCode`, `DebtorName`, `DeliverAddr1..4`, `DeliverContact`, `DeliverPhone1`,
  `SalesAgent`, `ShipVia`, `ShipInfo`, `Ref`, `RefDocNo`, `Remark1..4`, `Description`,
  `CurrencyCode`, `CurrencyRate`, `Total`, `Tax`, `NetTotal`, `LocalNetTotal`,
  `CreatedTimeStamp`, `LastModified`, `Details`.
- GRN header: the same minus `BranchCode`, `DebtorCode/Name`, `DeliverAddr*`, `SalesAgent`,
  plus `CreditorCode`, `CreditorName`, `SupplierDONo`, `PurchaseAgent`.
- Line (`Details[]`): `DtlKey`, `Seq`, `ItemCode`, `Description`, `Qty`, `FOCQty`, `UOM`,
  `UnitPrice`, `Discount`, `DiscountAmt`, `SubTotal`, `Tax`, `Location`, `BatchNo`,
  `DeliveryDate`, `ProjNo`; DO adds `YourPONo`, `YourPODate`; GRN adds `OurPONo`, `OurPODate`.
  When the vendor adds them: `FromDocType`, `FromDocNo`, `FromDocDtlKey`.
- Branch: `BranchCode`, `BranchName`, `AccNo` (see Q1 below).

Required: `DocKey` (integer or integer string), `DocNo`, `DocDate` (ISO date or datetime, or
`yyyyMMdd`); per line `DtlKey` (integer). `Details` absent is an empty list. At most 2000 lines;
`DtlKey` unique within the document. `LastModified` should always be sent (see 1.6). A record
over 1 MB serialised fails (`errors.record`).

`source_ref` (echoed in every verdict and used by the read door) is derived by the CRM:
`{book}:DO:{DocKey}` / `{book}:GRN:{DocKey}`. Lines: `{book}:DO:{DocKey}:{DtlKey}`. Branches:
`{book}:BR:{AccNo}:{BranchCode}`.

### 1.4 Idempotency and provenance

- Key: `(company, book, DocKey)`, a partial unique index on `orders (company_id, source_book,
  doc_key)` and `picking_headers (company_id, source_book, doc_key)`; lines by `(header,
  dtl_key)`. Branches by `(company, book, AccNo, BranchCode)`.
- Provenance on the row: `source_book`, `doc_key`, `source_modified_at` (LastModified),
  `source_record` (the record as sent), `last_synced_at`. No `integration_references` row: the
  key lives on the row because the row can predate the feed (adoption, 1.5).

### 1.5 Adopt by number

A DO whose `DocNo` equals an existing `orders.order_number` in the anchor company that has no
`doc_key` (created by the tracking upload's Master sheet or the DO detail import) is ADOPTED:
the row keeps its id and every tracking column, gains the AutoCount identity, and AutoCount
writes its own columns (2.3). Verdict `updated` with warning `adopted_by_doc_no`. Same for a GRN
on `picking_headers.picking_number` (`picking_type='goods_received'`).

Existing lines of an adopted document (no `dtl_key`) are matched one to one to the incoming
lines: DO by (product, warehouse, quantity), GRN by (product, quantity), first fit in `Seq`
order; a matched line keeps its id (and, on a GRN, its `spo_allocation_id` / `po_line_id`). An
unmatched old line is deleted. When a deleted GRN line carried an SPO or PO link, the record
carries warning `legacy_links_released` and the SPO receipt is recomputed.

A `DocNo` held by a row that already has a DIFFERENT `doc_key` is `failed` with
`errors.DocNo`. The rest of the batch lands.

### 1.6 Stale guard, unchanged, cancel

- `LastModified` is Malaysia time when naive (ruling V1), converted and stored with its zone.
- When both stored and incoming `LastModified` exist and the incoming one is OLDER: nothing is
  written, `unchanged` + warning `stale_ignored`.
- Otherwise (newer, equal or absent) the resolved values are compared with what is stored:
  identical answers `unchanged` and writes nothing (not even `last_synced_at`); any difference
  answers `updated`. So an equal timestamp with new content (for example the vendor adding the
  `FromDoc*` fields) still lands.
- `Cancelled` truthy (`T`, `Y`, `1`, `true`, `True`) is an update: DO `is_cancelled=true`, GRN
  `is_cancelled=true` and `picking_status='cancelled'`; the row and lines stay.

### 1.7 Resolution (masters are linked, never created)

- Customer (`DebtorCode`) in the anchor; unresolved: `customer_id` null, code and name kept,
  warning `customer_unresolved`.
- Product (`ItemCode`) and warehouse (`Location`): the line columns are NOT NULL on both tables,
  so an unresolved code makes the record `retryable` with `errors["Details.N.ItemCode"]` /
  `errors["Details.N.Location"]` (the SO / PO ingest rule: products and warehouses come through
  the same feed and arrive). A GRN line with no `Location` lands with no warehouse.
- A Details row with no `ItemCode` (a description-only row) is not written as a line; it stays in
  `source_record`, counted in `lines.skipped`, warning `line_without_item`.
- Branch: `BranchCode` + `DebtorCode` against the branch table (2.1); unresolved: code kept, name
  null, warning `branch_unresolved`. Empty `BranchCode` resolves nothing and warns nothing.

### 1.8 Links

| Link | Rule |
| --- | --- |
| DO line -> SO line (exact) | `FromDocDtlKey` present and `FromDocType` absent or `SO`: the anchor's `sales_order_lines` row whose `source_ref` ends in `:{FromDocDtlKey}` (the SO feed's `{database}:{DocKey}:{DtlKey}`), inside the SO numbered `FromDocNo` when sent. Exactly one match fills `order_lines.sales_order_line_id`; none or several: null, warning `so_line_unresolved`. |
| GRN line -> PO line or SPO line (exact) | `FromDocDtlKey` present: the anchor's `purchase_order_lines` or `spo_allocations` row whose `source_ref` ends in `:{FromDocDtlKey}` (or equals it), inside `FromDocNo` when sent. Exactly one across both fills `po_line_id` or `spo_allocation_id`; else null, warning `po_line_unresolved`. |
| DO -> SO (document number) | Only when `RefDocNo` is non-empty: the anchor's one `sales_orders` row with `so_number = RefDocNo` fills `orders.sales_order_id`; none or several: null, warning `sales_order_unresolved`. The line link stays null. |
| GRN line -> PO / SPO (document number) | Only when the line's `OurPONo` is non-empty and no exact link: the anchor's one `purchase_orders` row with `po_number = OurPONo` fills `picking_lines.purchase_order_id` and `from_doc_type='PO'`; else when an `spo_allocations` row carries `spo_number = OurPONo`, `from_doc_type='SPO'` (no SPO header table to point at); else warning `purchase_order_unresolved`. The line link stays null; the FIFO SPO matcher is NOT run and `spo_number_raw` is not written. |

`from_doc_type`, `from_doc_no`, `from_dtl_key` are stored as sent on every line.

**Later fill (Q10 a).** An unlinked line fills when (a) a later push of the same document
carries the link (content changed, 1.6), or (b) the SO / PO it names has arrived by the DB
transfer: at the end of every non-dry DO / GRN batch the CRM fills, for the anchor company,
every null link whose stored `from_dtl_key` / `RefDocNo` / `OurPONo` now resolves by the rules
above (one set-based UPDATE per link kind). Trigger for hooking the DB transfer itself: a DO
measured waiting more than a day for a link its SO already carries.

**Counter.** Every record's `lines` carries `linked` and `unlinked` (lines with no line-level
link) next to `created`, `updated`, `deleted`, `adopted`, `skipped`; a batch with unlinked lines
logs `ingest.unlinked_lines entity=.. company=.. count=..`. The S3 verification screen reads the
dry-run's per-record counters.

### 1.9 Verdicts and error shape

Unchanged from 2.6: always 200, `{dry_run, summary{total, created, updated, failed, retryable,
unchanged?}, records[{source_ref, outcome, entity_id, errors?, warnings?, lines?}]}`, one
SAVEPOINT per record. Warning vocabulary added in 2.7: `adopted_by_doc_no`, `branch_unresolved`,
`line_without_item`, `so_line_unresolved`, `po_line_unresolved`, `sales_order_unresolved`,
`purchase_order_unresolved`, `legacy_links_released`, `restored`.

### 1.10 Deletions (the sweep)

AutoCount hard-deletes and shows nothing (ruling V6). The shared service's sweep calls
byDocDate for a date range, compares with what it pushed, and sends the vanished `DocKey`s:

```json
{"companyCode": "SRT", "book": "db1", "doc_date_from": "2026-08-15", "doc_date_to": "2026-09-28",
 "doc_keys": [55120, 55121]}
```

For each key: no row in the anchor with that `(book, doc_key)`: `not_found`. A row whose stored
`DocDate` is outside the range: `failed`, `errors.doc_date` (the sweep only speaks for the days
it read). Otherwise the row is marked cancelled (DO `is_cancelled`, GRN `is_cancelled` +
`picking_status='cancelled'`) and `source_vanished_at` is set: `deactivated`. **Never deleted.**
Repeating it answers `deactivated` and writes nothing. Response shape = the existing deletions
shape. `doc_keys` over 1000: 413; missing dates, `from > to`, or missing `doc_keys`: 422
`INVALID_BODY`. A later push of a vanished DocKey (the sweep was wrong) clears
`source_vanished_at`, takes `Cancelled` from the payload and answers `updated` + `restored`.

### 1.11 Branches

`POST /ingest/branches`: each record a `branchbypage` row as returned. Required `BranchCode`.
Key `(company, book, AccNo or '', BranchCode)`. Verdicts `created` / `updated` / `unchanged` /
`failed`. When a branch's name changes (or a branch arrives after its DOs), every AutoCount DO in
the anchor with that `(book, DebtorCode = AccNo, BranchCode)` gets the new `branch_name`.

### 1.12 What the shared service must add (S1, S4)

HTTP source for `branchbypage`, `goodsreceivenotebyLastModified` / `...bydocdate`,
`deliveryorderbyLastModified` / `...bydocdate` per book; hourly poll on byLastModified for
yesterday and today (Malaysia time); backfill by DocDate one day per call from 1 Jan 2023;
branches daily and before the backfill; the deletion sweep over a trailing DocDate window
(45 days, scout Q9); push each record verbatim with the envelope above; the per-entity pull /
push switch (ruling Q5).

### 1.13 Customer Branches screen (owner, 29 Sep: "i need the branch UI in #1356")

Read only: the `branchbypage` push is the only writer (1.11, ruling Q2), so no Add, Edit, Delete
or Import. `GET /api/v1/order-management/branches` (`order_management.branches.view`): page,
limit, sort, dir, query (AccNo, branch code, branch name), `book`, `in_crm`, `customer_id`;
`GET .../branches/books` for the Book filter. The customer is matched by AccNo = customer code,
trimmed and case-insensitive, in the branch's company (the DO ingest's `DebtorCode` rule).
Screens: "Customer Branches" under Delivery Orders after Customers (columns Customer Code,
Customer Name, Branch Code, Branch Name, Book, Last Synced; listing key
`order_management.branches.view`), and a Branches tab on the Customer detail and edit pages
between Details and Asks (hidden without the view slug). The table has no address, contact or
phone columns (2.1) and the `branchbypage` field list is still uninspected (6, item 1); trigger
for typed address / contact columns: one captured real branch row.

## 2. Data model (migration `ac_grn_do_0001_ingest`, one head)

Chained on `merge_29sep_batch4`: main carried two heads (`sales_agent_aliases_r7` and
`scm_reorder_run_scope_desc`, #1342), so this branch ports the schema-free merge point from
#1358 verbatim; once #1358 is on main the file is identical and the merge is a no-op.

### 2.1 `branches` (new, company scoped)

| Column | Source |
| --- | --- |
| `id`, `company_id`, `created_at`, `updated_at` | CRM |
| `source_book` | envelope `book` |
| `acc_no` (NOT NULL, `''` when absent) | `AccNo` |
| `branch_code` | `BranchCode` |
| `branch_name` | `BranchName` |
| `source_record` JSONB | the row as sent |
| `last_synced_at` | CRM |

Unique `(company_id, source_book, acc_no, branch_code)`.

### 2.2 DO header, `orders` (new columns marked *)

| Column | Source field |
| --- | --- |
| `source_book`* | envelope `book` |
| `doc_key`* BIGINT | `DocKey` |
| `source_modified_at`* timestamptz | `LastModified` |
| `source_vanished_at`* timestamptz | deletions sweep |
| `last_synced_at`* | CRM |
| `source_record`* JSONB | the whole record, `Details` included |
| `order_number` | `DocNo` |
| `order_date` | `DocDate` |
| `created_time` | `CreatedTimeStamp` |
| `customer_id`, `debtor_code`, `debtor_name` | `DebtorCode` (resolved), `DebtorCode`, `DebtorName` |
| `branch_code`*, `branch_name`* | `BranchCode`, name from `branches` |
| `deliver_address`* (text) | `DeliverAddr1..4`, non-empty lines joined by newline |
| `deliver_contact`*, `deliver_phone`* | `DeliverContact`, `DeliverPhone1` |
| `agent` | `SalesAgent` |
| `ship_via`*, `ship_info`* | `ShipVia`, `ShipInfo` |
| `ref`* | `Ref` |
| `ref_doc_no`* | `RefDocNo` |
| `sales_order_id`* FK `sales_orders` SET NULL | resolved from `RefDocNo` (1.8) |
| `remarks` | `Remark1..4`, non-empty joined by newline |
| `description`* | `Description` |
| `doc_status`* | `DocStatus` |
| `is_cancelled` | `Cancelled` |
| `currency_code`*, `currency_rate`* | `CurrencyCode`, `CurrencyRate` |
| `subtotal_amount`, `tax_amount`, `total_amount` | `Total`, `Tax`, `NetTotal` (see Q6) |
| `local_net_total`* | `LocalNetTotal` |
| `order_status_id` | status `new` on create only |

Partial unique index `uq_orders_company_book_doc_key (company_id, source_book, doc_key) WHERE
doc_key IS NOT NULL`.

### 2.3 DO line, `order_lines`

| Column | Source field |
| --- | --- |
| `dtl_key`* BIGINT | `DtlKey` (unique per order where not null) |
| `line_sequence` | `Seq` (position when absent) |
| `product_id`, `item_code`* | `ItemCode` |
| `warehouse_id`, `location_code`* | `Location` |
| `description`* | `Description` |
| `quantity` | `Qty` |
| `foc_qty`* | `FOCQty` |
| `uom`* | `UOM` |
| `unit_price` | `UnitPrice` |
| `discount_text`* | `Discount` (AutoCount's text, e.g. `5%`) |
| `discount` | `DiscountAmt` |
| `total` | `SubTotal` |
| `tax` | `Tax` |
| `batch_no`*, `delivery_date`*, `proj_no`* | `BatchNo`, `DeliveryDate`, `ProjNo` |
| `your_po_no`*, `your_po_date`* | `YourPONo`, `YourPODate` |
| `from_doc_type`*, `from_doc_no`*, `from_dtl_key`* | `FromDocType`, `FromDocNo`, `FromDocDtlKey` |
| `sales_order_line_id` (existing, first writer) | 1.8 |

### 2.4 GRN header, `picking_headers`

| Column | Source field |
| --- | --- |
| `source_book`*, `doc_key`*, `source_modified_at`*, `source_vanished_at`*, `last_synced_at`*, `source_record`* | as DO |
| `picking_number`, `picking_date` | `DocNo`, `DocDate` |
| `picking_type`, `inspection_status` | `goods_received`, `pending` on create |
| `picking_status` | `approved`, or `cancelled` when `Cancelled` (Q9) |
| `source_system` | `autocount` on create only (existing provenance rule: written once) |
| `creditor_code`*, `creditor_name`* | `CreditorCode`, `CreditorName` |
| `supplier_do_no`*, `purchase_agent`* | `SupplierDONo`, `PurchaseAgent` |
| `ship_via`*, `ship_info`*, `ref`*, `ref_doc_no`*, `remarks`*, `description`*, `doc_status`* | as DO |
| `is_cancelled`* | `Cancelled` |
| `currency_code`*, `currency_rate`*, `subtotal_amount`*, `tax_amount`*, `total_amount`*, `local_net_total`* | as DO |
| `spo_number` | no header field (the API has none; `Ref` is a container number): the distinct SPOs the lines resolved to, in Seq order, one SPO or several joined with ", " like the Excel import; left as stored when no line is on an SPO (GRN-HEADER-SPO, `PLAN-grn-header-spo-04oct.md`) |

Partial unique index `uq_picking_headers_company_book_doc_key`.

### 2.5 GRN line, `picking_lines`

| Column | Source field |
| --- | --- |
| `dtl_key`*, `seq`* | `DtlKey`, `Seq` |
| `product_id`, `item_code`* | `ItemCode` |
| `destination_warehouse_id`, `location_code`* | `Location` |
| `description`*, `uom_code`* (`uom` is the model's unit relationship) | `Description`, `UOM` |
| `qty`* NUMERIC(15,4) | `Qty` (exact) |
| `quantity_picked`, `quantity_expected` | `Qty` rounded to integer (the existing integer columns) |
| `foc_qty`* | `FOCQty` |
| `unit_cost`, `line_total` | `UnitPrice`, `SubTotal` |
| `discount_text`*, `discount_amount`*, `tax_amount`* | `Discount`, `DiscountAmt`, `Tax` |
| `batch_number_picked` | `BatchNo` |
| `delivery_date`*, `proj_no`* | `DeliveryDate`, `ProjNo` |
| `our_po_no`*, `our_po_date`* | `OurPONo`, `OurPODate` |
| `from_doc_type`*, `from_doc_no`*, `from_dtl_key`* | as DO |
| `po_line_id`, `spo_allocation_id` (existing) | exact link (1.8). The brief's `spo_line_id` IS `spo_allocation_id` (the SPO line table); no second column. |
| `purchase_order_id`* FK SET NULL | document-number link (1.8) |

## 3. Column ownership (DO and tracking upload on one `orders` row)

A row is **AutoCount-owned** when `doc_key IS NOT NULL`.

| Owner | Columns |
| --- | --- |
| AutoCount ingest | every column in 2.2 and 2.3; all lines on an AutoCount-owned DO |
| Tracking upload, Overall Tracking sheet | `actual_delivery_date`, `pickup_time` (the time), `transporter` / `transporter_id`, `driver_name`, `lorry_plate`, `checker`, `trips`, `delivery_days`, `kpi_warning`, the delivered status, `customer_ref`, `salesman`, `warehouse`, `delivery_remarks`, `delivery_remarks_cs` |
| Tracking upload, Master sheet | `remarks_cs`, `order_type`, `estimated_delivery_date`; on an AutoCount-owned row it SKIPS `order_date`, `created_time`, `debtor_code`, `debtor_name`, `agent`, `is_cancelled`, `customer_id` |

Rules:

- The ingest never writes a tracking column (pinned by comparing every tracking column before and
  after an adopt and after an update).
- The Master sheet still creates a DO that AutoCount has not sent (Q8 a); the push adopts it.
- The DO detail import skips rows for an AutoCount-owned DO with outcome `autocount_owned`
  (lines are AutoCount's); the GRN Excel import leaves an AutoCount-owned GRN's header and lines
  untouched.

## 4. Deletion rule

The CRM never deletes an AutoCount DO or GRN. A vanished DocKey is cancelled in place with
`source_vanished_at` (1.10). The row, its lines, its tracking columns and every link stay.

## 5. Build order (tests first)

1. This plan + UAC (first commit, PR comment `plan: <sha>`).
2. Red tests: `tests/test_ingest_autocount_do_grn.py`, `tests/test_ingest_autocount_branches.py`,
   `tests/test_autocount_do_ownership.py`, `tests/test_migration_ac_grn_do_0001.py`, fixtures
   `tests/fixtures/autocount/do_live_sample.json`, `grn_live_sample.json` (the live field lists,
   values anonymised).
3. Migration + models, `app/services/autocount_doc_ingest_service.py`, route wiring, contract
   2.7, ownership guards in the tracking upload and the two Excel imports.
4. Contract section 13 in `PLAN-autocount-cross-repo-contract.md`; `GET /external/contract`.

## 6. Open questions (recommendation in bold)

1. The `branchbypage` field list was not inspected. Is a branch keyed by (`AccNo`, `BranchCode`)
   or by `BranchCode` alone? **Key (`AccNo`, `BranchCode`), `AccNo` stored as `''` when absent;
   a DO resolves by (`DebtorCode`, `BranchCode`) first, then by `BranchCode` alone when exactly
   one branch in the book carries it.**
2. Unresolved `ItemCode` / `Location` on a line: **`retryable`** (NOT NULL columns; the feed
   sends products and warehouses) vs land the document without that line.
3. A Details row with no `ItemCode`: **not a line, kept in `source_record`, `lines.skipped`,
   warning `line_without_item`.**
4. Adopting an Excel-imported GRN whose lines are linked to SPO allocations: **carry the link to
   the matching AutoCount line by (product, quantity), delete the rest, recompute the SPO receipt,
   warning `legacy_links_released`**; the alternative is to refuse adoption of a linked GRN.
5. Permissions: the DO and GRN doors use existing slugs; `branches` gets its own
   `order_management.branches.{view,add,edit,delete}` (registry, created by `sync_permissions` on
   boot; the external permission guard requires a distinct slug per entity, so it cannot share
   `order_management.customers.edit`). **Grant `order_management.orders.{view,edit,delete}`,
   `procurement.grn.{view,edit,delete}` and `order_management.branches.edit` by hand to the
   foundryx integration's act-as role, no migration grant** (a sweep over roles holding
   `scm.sales_orders.edit`, the billing precedent, would give human SCM roles DO and GRN delete).
6. DO / GRN header totals: **`subtotal_amount = Total`, `tax_amount = Tax`, `total_amount =
   NetTotal` (falling back to `Total + Tax`)**; confirm against one live record's numbers.
7. A vanished DocKey whose stored DocDate is outside the swept range: **`failed`,
   `errors.doc_date`**.
8. A vanished document that is pushed again: **restored (`source_vanished_at` cleared, cancel
   from the payload), warning `restored`.**
9. GRN status for an AutoCount GRN: **`approved`** (what the Excel import writes, so SPO receipts
   count it), `cancelled` when cancelled.
10. A branch rename: **every AutoCount DO with that branch takes the new name** (a branch row
    with no AccNo never overrides a debtor's own exact branch row).
11. (security review) An exact link by `FromDocDtlKey` with no `FromDocNo` matches the SO / PO
    line whose `source_ref` ends in that DtlKey anywhere in the company, and DtlKeys are per
    book, so with a second book a lone match could be another book's line. **Accept while
    there is one book (db1, ruling V9); when a second book is pushed, require `FromDocNo` or
    map the book to the DB transfer's `{database}` prefix.** The waiting-link fill already
    only retries lines that carry `FromDocNo`.
12. (security review) `POST /ingest/branches` (slug `order_management.branches.edit`) also
    refreshes `orders.branch_name`, a display column on AutoCount DOs. **Accept: it is the
    branch's own name, and the DO push itself sets the same column.**

## 7. Not built, and the trigger for each

- Read / deletions for `branches`: a branch deleted upstream that must go from the CRM.
- Hooking the SO / PO DB transfer to fill waiting links: a DO waiting over a day for a link.
- A verification screen for DO / GRN: slice S3.
- DO / GRN detail page showing the links: slice S5.
