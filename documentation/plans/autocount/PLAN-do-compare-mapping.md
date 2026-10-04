# PLAN - DO compare mapping (DO-COMPARE-SIM)

Status: building, full track (migration + page). Branch `crew/do-compare-sim`.
UAC: `do-compare-mapping-acceptance-criteria.md`.

## 1. Why (measured)

Production job "AutoCount delivery orders pull, SRT" (31/08-30/09) compared 1,611 of 2,312,
1,163 only in Excel, 15,016 only in AutoCount, discount 0.65 vs 65.

Cause, reproduced locally with `sorento_crm_backend/scripts/simulate_do_compare.py` on the
owner's two workbooks against the real 01-03 Sep snapshot in local ss
(`foundryx_service_dopullss`, 1,150 docs / 3,996 lines):

- `lib/excel-utils.ts:67-86` reads sheet `Template` from any `.xlsm`. The Order Listing
  workbook has a `Template` sheet: a transaction log (no Doc Date, discount stored as a
  fraction 0.65 formatted 65%, 3,949 rows = the owner's "3,949 lines"). So the window cut
  nothing and discount scale differed. The Order Tracking workbook has no `Template` sheet,
  hence "Macro workbook must contain a 'Template' sheet".
- Not an ss mapping issue: the DO snapshot stores raw AutoCount records, no field mapping is
  consulted (ss `sync.py:2806-2811`); Discount arrives as `"37%"`, the same text the Master
  sheet carries.

| 01-03 Sep | match | differ | only Excel | only AutoCount |
|---|---|---|---|---|
| lines, sheet Template (prod behaviour) | 0 of 0 | 0 | 3,475 | 3,392 |
| lines, sheet Master (code as is) | 3,276 of 3,362 | 86 | 0 | 30 |
| headers, sheet Master (code as is) | 1,136 of 1,150 | 14 | 49 | 0 |

The 86 are promotion-package `PP` lines with a blank `Total (Ex)`: the alias fallback
(`_DO_TOTAL_EX_KEYS` ends in `total`, `autocount_pull_compare.py:239`) reads the DOCUMENT
`Total` column instead. Verified with the FE's own SheetJS (`node_modules/xlsx`) on the real
file: `sheet_to_json` names the line total `Total_1`, the document total `Total` (735.3 on
202609-0076), and omits the blank `Total (Ex)` cell. The 30 are
lines of cancelled DOs (AutoCount `Cancelled=T`), which the Order Listing excludes.

After the fix (default mapping, same data, `scripts/simulate_do_compare.py`):

| 01-03 Sep | match | differ | only Excel | only AutoCount |
|---|---|---|---|---|
| lines, mapping `order_listing` (sheet Master) | 3,362 of 3,362 | 0 | 0 | 0 |
| headers, mapping `order_tracking` (sheet Master) | 1,136 of 1,150 | 14 | 49 | 0 |

Remaining header rows, reported as-is (Q8): 13 `cancel` (sheet F, AutoCount T), 1
`debtor_code` (202609-0021: 300-W021 vs 300-W028), 49 numbers absent from the db1 snapshot
(RMA-SRT 22, CG- 11, RF 7, RMA-PS 4, MKTPT 2, RMA-CG 2, HQ/IV 1).

## 2. Design

Owner: "I need the mapping to be configurable." One table, one row per workbook kind.

- **Table** `autocount_compare_mappings` (additive migration; global table, no `company_id`, no `CompanyScopedMixin`: company scope only filters mixin models, `app/services/company_scope.py:9-11,99-122`):
  `id` uuid, `kind` unique (`order_listing` | `order_tracking`; `grn` later), `sheet_name`,
  `columns` JSON `[{excel_header, transform, field}]`, `updated_at`, `updated_by`. JSON, not
  a child table: the rows are only ever read and saved as one set. Migration seeds the
  defaults below; the service falls back to the same in-code defaults when a row is absent
  (CI databases built by `create_all` carry no seed).
- **Transforms** (fixed list, no formulas): `text`, `number`, `money`, `date`,
  `percent_text` (`"37%"`, `"40%+5%"`, a bare 37 reads as 37%), `percent_fraction` (0.37 reads
  as 37%, text with `%` as `percent_text`), `cancel_flag`.
- **Fields per kind**: order_listing `doc_no`*, `item_code`*, `location`, `doc_date`, `qty`,
  `unit_price`, `discount`, `total_ex`; order_tracking `doc_no`*, `doc_date`, `debtor_code`,
  `cancel`. (* required)
- **Compare** (`autocount_pull_compare.py`): each Excel row goes through the mapping into
  canonical fields first (header matched trimmed, case-insensitive); no alias guessing, so a
  blank mapped cell stays blank. Lines compare skips cancelled AutoCount DOs. The window reads
  `doc_date`. Doc-no normaliser stays trim + uppercase (0 prefix misses measured).
- **Routes** (under `/autocount/pulls`): `GET /compare-mappings`,
  `PUT /compare-mappings/{kind}`; permission `order_management.orders.autocount_pull`.
- **FE**: `parseExcelFile(file, { sheetName })` reads the named sheet case-insensitively,
  error names the configured sheet and the found sheets; products/stock keep today's rule.
  Compare tab: dropzone titles show the configured sheet; a "Mapping" button opens a modal:
  line tabs per kind, sheet name input, DataGrid (Excel column | Transform | Sorento field)
  edited in place (the ss Branch mapping table pattern with sorento's own DataGrid +
  `SearchableSelect`), add/remove row, Save.

### Defaults (sheet `Master` for both)

order_listing: Doc No->doc_no text, Doc Date->doc_date date, Item Code->item_code text,
Location->location text, Qty->qty number, Unit Price->unit_price money,
Discount->discount percent_text, Total (Ex)->total_ex money.
order_tracking: Doc. No.->doc_no text, Date->doc_date date, Debtor Code->debtor_code text,
Cancel->cancel cancel_flag.

## 3. Not doing (named triggers)

- ss document feed mapping: not needed; the raw record text already matches the sheet.
  Trigger: a field AutoCount carries in a different shape than every checker's workbook.
- Doc-no prefix stripping: trigger is a measured prefix miss.
- GRN kind: trigger is the GRN compare lane.

## 4. Grill (30 Sep)

Sent as one crew ask; owner answered "all as recommended" (30 Sep). Premises re-checked
against code/data after the owner's "get your facts right" ruling; corrections marked
CORRECTED, anything not proven marked UNVERIFIED.

Evidence per premise:
- Q1: CORRECTED. The ask said "company-shared"; wrong. `__company_shared__` only changes the
  predicate for `CompanyScopedMixin` models with a nullable `company_id`
  (`company_scope.py:99-122`). The mapping table has no `company_id`, so it is global.
  "CI DB has no seed": `scripts/bootstrap_env.py` builds CI from `create_all` then stamps.
- Q3: 0 prefix misses holds for 01-03 Sep only (simulation); full month UNVERIFIED.
- Q4: CORRECTED. The ask said "import-jobs manage permission"; no such check exists on these
  routes. The DO pull checks `order_management.orders.autocount_pull`
  (`autocount_pull_service.py:43`, `autocount_pull.py:120-130`); the new routes use it.
- Q5: products accept `.xlsx,.xls` only (`PullCompareTab.tsx:139`), so the Template rule never
  reaches a products compare; stock `.xlsm` still goes through `resolveImportSheetName`
  (`excel-utils.ts:76-86`), which `TemplateUploadDialog.tsx` also uses; both unchanged.
- Q6: verified with SheetJS on the real file (section 1).
- Q7: Order Listing Master `Cancelled` column: 21,654 `F`, 1 blank, 0 `T` (openpyxl count);
  all 30 only-in-AutoCount lines belong to `Cancelled=T` documents (simulation).
- Q8: CORRECTED wording. Fact: the 49 numbers (RMA-SRT, CG-, RF, RMA-PS, MKTPT, RMA-CG,
  HQ/IV) are in neither the 01-03 Sep db1 snapshot nor the Order Listing Master. That they
  are "other document types" is UNVERIFIED. The 13 cancel rows are Excel `F` vs AutoCount
  `T`; why they differ (stale sheet or later cancel) is UNVERIFIED.
- Q9: ss `sync.py:2806-2811` (origin/main fa314d19) skips the entity config for a DO snapshot;
  `doc_feed/snapshot.py:156` stores the vendor records; snapshot row for 202609-0001 carries
  `"Discount": "37%"`.
- Also CORRECTED from the first answer: the raw `Discount` read is
  `autocount_pull_compare.py:465`, not `:417`. The job's Download file
  (`map_delivery_order_rows`, `autocount_pull_service.py:784-796`) carries no Discount,
  Cancelled or SubTotalExTax, so a full-month simulation from it can only check keys, qty and
  unit price.

| # | Question | Answer |
|---|---|---|
| Q1 | Storage | new table `autocount_compare_mappings`, one row per kind, columns as JSON, global (no company_id), seeded Master defaults |
| Q2 | Transforms | fixed list in code (text, number, money, date, percent_text, percent_fraction, cancel_flag); no formula builder |
| Q3 | Doc-no normaliser | trim + uppercase only; no prefix strip until a measured miss |
| Q4 | Page | "Mapping" on the Compare tab, the ss Branch mapping table pattern (DataGrid, edit in place, one Save); permission `order_management.orders.autocount_pull` |
| Q5 | Sheet picking | configured sheet by name, case-insensitive; never `Template` for DO compare; products/stock unchanged |
| Q6 | Blank line Total (Ex) | no alias fallback; blank stays blank, not compared |
| Q7 | Cancelled DOs | skipped in the lines compare; headers compare still reports cancel |
| Q8 | Header-only docs (RMA, CG, RF...) + genuine cancel/debtor diffs | reported as-is |
| Q9 | ss doc-feed mapping | not needed, no ss lane |
| Q10 | Track / mock | full track, one PR, no mock (existing components only) |

## 5. Slices

S1 BE: model + migration + service + routes + compare via mapping (tester red first, coder).
S2 FE: sheet-by-name parse, mapping service/hooks, modal, dropzone titles (vitest red first).
S3 evidence: simulation before/after, agent-browser 1280/375, reviewer.

## 6. Later rulings (1 Oct)

- RMA / CG / RF / MKTPT / HQ document numbers: owner ruling (b), keep showing them as "Only in
  your Excel" (explainable), no skip list. Evidence: the full-month DO snapshot 4ecfe74a (6,487
  docs, 01/09-01/10) carries none of these prefixes; RMA enters sorento only through the Order
  Tracking upload (`order_service.import_excel_tracking`), which stays.
- Order Tracking re-import over an AutoCount-created order keeps the AutoCount-owned header
  columns (`order_service.py:48-51`, `:3057-3060`, pinned by
  `tests/test_autocount_do_ownership.py::test_master_sheet_keeps_autocount_columns`).
- Out of scope, separate lane recommended: every DO line carries `FromDocDtlKey = 0`, which the
  ingest reads as a link and warns "SO line not found" (`autocount_doc_ingest_service.py:159-167`,
  `:847`).
