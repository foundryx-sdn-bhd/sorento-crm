# PLAN: GRN pull from AutoCount with PO/SPO line linkage (GRN-PULL-CRM)

Status: **built, reviewed, crew e2e passed, owner hand test PASS (2 Oct); merge approved with
ss#107. Migration re-parented onto `acct_ledger_0002_vocab` after #1432.** All card questions
answered (Q3 a, Q4 a, Q5 a; Q1/Q2 replaced by line-order matching; D1 a, D2 a, D4 accept;
D3 a). PR #1427. Track: full L (new permission slug + grant
migration, prod data linkage, cross-repo with shared-service lane GRN-PULL-SS). Branch
`claude/grn-pull-crm-2pnmf9` (the sandbox's designated branch; the brief named
`crew/grn-pull-crm`), base `main` 066b966e. UAC: `autocount-grn-pull-crm-02oct-acceptance-criteria.md`
(AC-GP-01 onward). The rules follow the owner rulings in 0.1.

Scope: Goods Receive Notes pulled on request by DocDate, same path as the DO pull
(shared-service snapshot -> CRM preview/compare -> review -> user confirms). Push stays OFF.
Never writes to AutoCount. Confirm applies through `AutocountDocIngestService` (one writer).

Paths: `be/` = `sorento_crm_backend/`, `fe/` = `sorento_crm_frontend/`.

## 0. Scout (measured on `main` 066b966e)

| Thing | Where | State |
| --- | --- | --- |
| DO pull, end to end | `be/app/services/autocount_pull_service.py:39-67` (entity maps), `be/app/tasks/autocount_pull_tasks.py:691` (`_do_ingest`), `:706` (`_ingest_rows`), `:719` (`_tally_delivery_orders`), `:795` (`_preview_delivery_orders`), `:833` (`_apply_delivery_orders`), `be/app/api/v1/integrations/autocount_pull.py:332-414` (rows / download / compare dispatch), `be/app/services/autocount_pull_compare.py` | Merged (#1383). GRN is a fourth entity on the same machinery. |
| Snapshot book | `autocount_pull_service.py:697` (`snapshot_book`) | Reads the prefix before the first `:` of `source_ref`; not DO-specific, so `{book}:GRN:{DocKey}` works unchanged. |
| GRN ingest | `be/app/services/autocount_doc_ingest_service.py:932` (`_apply_grn`) | Exists. Lands on `picking_headers` / `picking_lines`, status `approved` (`cancelled` when `Cancelled`), adopts an Excel GRN by number, never unsets a link. |
| GRN line exact link | same file `:966-970`, `_po_or_spo_line` `:775` | `FromDocDtlKey > 0`: one `source_ref` match across PO lines + SPO allocations, else `po_line_unresolved`. |
| GRN line, key 0 | `:176` (`_link_key`), `:971-978` | 0 = no key. Only `OurPONo` is read: header-level `purchase_order_id` (PO) or `from_doc_type='SPO'`; **line link stays null; `FromDocNo` is stored but never resolved**. Live GRN lines carry `OurPONo` null (ingest plan 29 Sep, line 55), so today a key-0 line links nothing at all. |
| GRN receipt hook | `be/app/api/v1/external/ingest.py:741` (`_run_grn_receipt_hook`) | Route-level, post-commit: recomputes `touched_allocation_ids`. The DO apply has no hook (`autocount_pull_tasks.py:833` docstring); the GRN apply must run this one. |
| GRN Excel upload linkage | `be/app/tasks/import_tasks.py:2177` (`process_grn_lines_import`), line SPO columns `:1683` ("Our PO No." first, then "From Doc No", "SPO Number", "Transfer From"), header fallback `:2346`, group key `:2369`, draw `:2532-2544` | **SPO only, never PO lines.** Matcher `be/app/services/grn_spo_matching.py`: `build_allocation_pool` `:96` (SPO key normalised, product, company, FIFO by age, capacity = allocated - other GRNs' drawn qty - unexplained receipt) + `draw_fifo` `:224` (same warehouse first, then any, remainder unlinked). One receipt may SPLIT into several picking lines. The Excel's "Our PO No." column is the GRN listing report's rendering of the line's source document, i.e. the API's `FromDocNo` (the API's own `OurPONo` is null). |
| Split vs AutoCount line identity | `be/app/models/procurement.py:840` | `uq_picking_lines_header_dtl_key`: one picking line per AutoCount DtlKey; an AutoCount line cannot be split the way an upload line is. |
| Cancelled GRN and SPO capacity | `grn_spo_matching.py:185,292` | Pool excludes `rejected` only, so a cancelled GRN's linked lines still consume SPO capacity. `compute_received_for_allocation` counts `approved` only (`procurement_service.py:4499`), so the receipt figure is already right. |
| Shared-service GRN snapshot | crew, 2 Oct | Not on ss main (`pull_gateway_service.py:54-59`). Lane GRN-PULL-SS adds it to the contract in 1.2. `From*` line keys seen only in an ss fixture; live presence unverified, so absent keys must behave as blank. |

## 0.1 Owner rulings (2 Oct, relayed by crew)

- **Q3 (a)**: `FromDocNo` wins; `OurPONo` is hand typed and only used when `FromDocNo` is blank.
- **Q4 (a)**: the SPO capacity pool excludes `cancelled` GRNs as well as `rejected`.
- **Q5 (a)**: two-dropzone compare, lines = "DETAIL LISTING ... .xlsx", listing = "GRN Listing -
  Macro Version ... .xlsm" (column structure coming from crew).
- **Q1 / Q2 REJECTED as proposed**: "AutoCount can relate the PO/SPO to GR, we are supposed to
  be able to". Every GRN line must link to a PO line or SPO line(s); "unlinked + flagged" is
  not an accepted outcome for a line that names its source. PO lines link too. **The split /
  several-candidates design is HELD** until crew's live evidence: does AutoCount already split
  a receipt per SPO line, and is `FromDocDtlKey` ever > 0.
- Dev examples (Excel-imported, company SRT; dev has 0 AutoCount-ingested GRNs, no cancelled GRN):
  - SPO, same product split: `GR-2026/09-0090` (`SPO-2026/09-0010`: SRT756-32-CR 175 BRW on SPO
    line 99 + 5 BRW-HP on line 100; SRT1000-CR - NEW 2640 over 7 SPO lines in 3 warehouses);
    `GR-2026/09-0075` (SRTWC7405-SC, `SPO-2026/09-0050` line 32 twice, 43 + 24, line 33 43).
  - SPO, one line per product: `GR-2026/09-0079` (`SPO-2026/09-0005`).
  - PO by text only (no `po_line_id` on dev): `GR-2026/09-0070` (`PO-2026/09-0018`),
    `GR-2026/09-0092` (`PO-2026/09-0020`).
  - No source document: `FGR2026/09-0022`, `FGR2026/09-0024`, `GR-2026/07-0001`.

Consequence: the first card's "unlinked / ambiguous" outcomes are gone; section 1.3 is the
line-order design the live evidence supports (owner accepted, 2 Oct).

## 1. Design

### 1.1 Entity `goods_receive_notes`

| Map | Value |
| --- | --- |
| `ENTITY_PERMISSIONS["goods_receive_notes"]` | `procurement.grn.autocount_pull` (new registry slug, "Pull Goods Receive Notes from AutoCount") |
| `JOB_TYPES` | `autocount_grn_pull` |
| `APPLY_JOB_TYPES` | `autocount_grn_apply` |
| Router gate | add `"procurement"` to `require_any_module_enabled(...)` (`be/app/api/v1/__init__.py`; verify the module key) |
| `scope` | accepted for `delivery_orders` and `goods_receive_notes` (`autocount_pull.py:88`) |
| Migration | `grn_pull_0001_perm`: insert the slug if absent, sweep onto roles holding `procurement.grn.import` (integration roles excluded), grant `admin`. The `do_pull_0001_perm` statements with two names changed. |

### 1.2 Snapshot contract (GRN-PULL-SS, as posted on #1427 and accepted by crew 2 Oct)

`POST /api/v1/autocount/snapshots {"companyCode", "entity": "goods_receive_notes", fromDay?, toDay?, docNo?}`.
Rows = the raw vendor GRN dict verbatim (`Details` intact) + `source_ref` `{book}:GRN:{DocKey}`.
Same build / status / rows calls, `ready` header, contentHash, 409 `BUILD_IN_FLIGHT`, 10,000
document cap. The CRM strips `source_ref` before the ingest (`_ingest_rows`, DO review S1).
Line keys `FromDocType` / `FromDocNo` / `FromDocDtlKey` / `OurPONo` may be ABSENT: absent =
null = blank (pinned).

### 1.3 Line linkage (the ingest change; one resolver used by push, pull and the waiting fill)

Live evidence (crew, 2 Oct, db1 + db2, read-only): `FromDocDtlKey` = 0 on every GRN line
(257 + 71); `FromDocNo` filled on 246 of 257, `OurPONo` blank on all; `FromDocType` reads
`PO` even when `FromDocNo` is an SPO; AutoCount ALREADY writes one GRN line per SPO line, in
the SPO's Seq order, the same item repeating (GR-2026/09-0090 66 lines = SPO-2026/09-0010 66
lines; GR-2026/09-0075 38 = SPO-2026/09-0050 38); the GRN Location can differ from the SPO's
(BRW -> MWH, BRW -> BRW-RSV). Rulings: Q3 a, Q4 a, D1 a, D2 a, D4 accept (crew); D3 with the
owner answered (a); built behind `_unmatched_item`, one place to change.

1. **Source document**: `FromDocNo`, else `OurPONo` (Q3 a). Neither: no source, unlinked,
   no warning (the only silent unlinked case; 11 of 257 live lines).
2. **Exact key** `FromDocDtlKey > 0`: existing `_po_or_spo_line`, unchanged.
3. **PO or SPO by the number's table, never `FromDocType`**: `_spo_match_key` matches an
   `spo_allocations.spo_number` in the anchor company -> SPO; else an exact
   `purchase_orders.po_number` -> PO; neither -> `purchase_order_unresolved`, waiting fill.
4. **Candidates**: that document's lines with the GRN line's product, in line order (SPO:
   `spo_line_number`, which the SPO ingest assigns in Seq order,
   `shipping_order_ingest_service.py:533-539,1065`; Seq itself is not stored, `:921`. PO:
   the DtlKey in `source_ref`, D4). SPO lines retired with no picks are skipped (pool rule).
   Location is never a key.
5. **Remaining** per candidate = ordered (`allocated_quantity` / `qty_ordered`) minus the
   drawn qty of OTHER GRNs' lines linked to it (`quantity_picked`), rejected and cancelled
   GRNs excluded (Q4 a). For SPO this is `grn_spo_matching.build_allocation_pool`'s
   arithmetic, refactored into a candidates function that keeps zero-remaining rows and
   sorts by line number; the pool keeps its FIFO order for the upload. One shared function.
6. **Choice (D1 a)**: GRN lines of a product in `Seq` order; each takes a DIFFERENT unused
   candidate: first with remaining == qty; else first with remaining >= qty; else first with
   any remaining (`over_receipt`); else the last candidate (`over_receipt`). Remaining is
   decremented as lines take candidates. One GRN line = one PO/SPO line; nothing is split.
7. **No candidate (D3 a, owner 2 Oct)**: the product is not on the named document:
   `item_not_on_order`, line link null, but the header link is kept: `purchase_order_id`
   (PO) or `spo_number_raw` + `from_doc_type='SPO'` (SPO).
8. **Written columns**: `po_line_id` + `purchase_order_id` (PO) or `spo_allocation_id` +
   `spo_number_raw` (SPO); `from_doc_*` as sent (`from_dtl_key` None for 0).
9. **Re-push** of the same GRN excludes its own lines from "other GRNs" (the upload's
   `exclude_header_ids`), so it keeps its links. A link to a different target replaces the
   old one; an unresolvable re-push never unsets a link (existing `keep_links`).
10. **Adoption of an Excel GRN (D2 a)**: per product, the AutoCount line claims the stored
    lines without `dtl_key` whose `quantity_picked`, in stored order, add up to its qty; the
    first claimed keeps its id, the rest are deleted (their SPO lines' receipt recomputed,
    `legacy_links_released`); the link comes from rules 2-7 (the Excel FIFO link is
    replaced). A single exact-qty claim is the old behaviour.
11. **Waiting fill**: AutoCount lines with no line link and a source (`from_doc_no`, else
    `our_po_no`, else the SPO an adopted Excel line stated in `spo_number_raw`), no
    `from_dtl_key`, newest first, bounded by `MAX_WAITING_LINKS`, through the same resolver,
    with the lines its siblings already hold marked used. A line whose document is still
    unknown is left exactly as it was; D3 lines are retried, so they link the day the
    document gains the product.
12. **Batch order**: the pull feeds the ingest sorted by (DocDate, DocKey), so earlier
    receipts take their lines first; preview and apply identical.

Warnings join `CONTRACT_2_7_WARNINGS` and the pull's wording map: `over_receipt` "received
more than the order line has left", `item_not_on_order` "item not on the named PO / SPO".

### 1.4 Preview / apply

`_preview_goods_receive_notes` / `_apply_goods_receive_notes` = the DO pair with the entity
passed through (`_do_ingest`, `_ingest_rows`, `_tally_*` generalised by entity; the message
says "GRN created / updated / adopted by number"). Counts as DO plus `lines_linked`,
`lines_unlinked`, `lines_ambiguous` (sum of line counters + records with an ambiguous
warning). Apply: after `db.commit()`, `_run_grn_receipt_hook(db, ingest)` (moved to a shared
helper both callers import, no copy). Audit actor as DO (job actor scope).

### 1.5 Rows, download, compare

- **Excel view / download**: one row per GRN line in the DETAIL LISTING's shape and order:
  `doc_no, doc_date, creditor_code, creditor_name, from_doc_no` ("Our PO No."), `item_code,
  description, location, qty, uom`. The per-line link verdict is not a column: the Changes
  tab carries it per document (counters and worded warnings).
- **Compare (Q5 a)**, two dropzones (the DO compare UI unchanged). Real files (crew, read-only;
  never committed, fixtures use made-up values in the same shape):
  - `lines` = "DETAIL LISTING ddmmyyyy.xlsx", sheet `Sheet`, header row 1, 34 columns incl.
    `Doc No`, `Doc Date`, `Creditor Code`, `Our PO No.` (= AutoCount `FromDocNo`), `Cancelled`,
    `Item Code`, `Location`, `Qty` (`Total` / `Tax` / `Local Total` appear twice: header and
    line; read by first match only where needed). Last row is a totals row with no Doc No
    (skipped). Rows are NOT in AutoCount Seq order and a document's rows are scattered:
    compare by (Doc No, Item Code, Location) with Qty as a MULTISET per key (sorted lists of
    quantities, so 2 + 98 vs 100 is a difference and 106/24 vs 24/106 is not), plus `Our PO
    No.` vs `FromDocNo` (`_spo_match_key`-normalised) per key. Never by position.
  - `headers` = "GRN Listing - Macro Version dd.mm.yyyy.xlsm", sheet `Master` read BY NAME
    (fallback: the active sheet): `Doc. No.`, `Transfer From`, `Date`, `Creditor Code`,
    `Cancelled`; totals row skipped. Keyed by Doc No on Date, Creditor Code, Transfer From,
    Cancelled. **Flag:** the existing listing importer reads `workbook.active`
    (`import_tasks.py:1808,2213`), which in this file is `Template` (10 docs) not `Master`
    (27). Not this lane's fix; recorded for the backlog.
  - Windowed by the pull's DocDate window as DO (`pull_window`, `_excel_day`). A document only
    in AutoCount (live 2026-10-01: GR-2026/10-0006, 33 lines, missing from the listing) is an
    expected "in AutoCount, not in your Excel" difference; only-in never deletes.

### 1.6 Frontend (reuse, no new UI)

A fourth value in the existing switches: `AutocountPullEntity`, counters, columns, labels,
job page ("AutoCount GRN Pull", Back to Goods Receive Notes), the GRN list's Actions menu
(`fe/app/(protected)/procurement-management/grn/page.tsx`) gets the button + the existing
`PullScopeDialog`. If the GRN list has no Actions menu yet, that is new UI: static mock first.

### 1.7 Not built, and the trigger

- Splitting an AutoCount line across SPO allocations: the owner choosing Q1 (b).
- Writing PO `qty_received` from GRNs: the PO feed stops carrying it.
- Push enable for GRN: the owner, after a clean compare on a real window.

## 1.8 Phase 3 reviews (2 Oct) and what changed

Security review: no blocker, no should-fix. Notes taken: N1 `spo_line_candidates` requires
a company (raises without one); N2 test that the waiting fill never reaches company B; N3
the fill's sibling query carries the company predicate. N4 (compare mappings are global,
not per company) left as the DO design; trigger: Confirm on a GRN pull depending on a clean
compare.

Correctness review (kill tests 11 of 17 killed), all taken:

- **B1** an adopted Excel line still waiting for its stated SPO could never link (the fill
  skipped `spo_number_raw`, forward matching skips AutoCount GRNs): the fill now takes it
  (rule 11). Test `test_gp_b1_...`.
- **B2** the DtlKey-order test passed with the sort removed: equal quantities now.
- **B3** neither `source_doc` compare was tested: `test_gp52d` / `test_gp52e`.
- **S4** a PO to SPO re-push kept both links: a resolved document writes the whole link set
  (`_FORCE_LINKS`), and an SPO line the GRN no longer points at is released so its receipt
  is recomputed. **S5** SPO is asked before PO (a CRM-raised SPO is both). **S6/S7** guard
  tests for the sibling-held fill and the any-remaining rung. **S8** GRN list wiring test.
  **S9** UAC / plan wording aligned. Nits: one label ("goods receipt notes"), download
  column order = the Excel view's, `DocKey` given as text still orders by value, the
  source-document difference shows every Excel spelling, `isDocument` rename.

GRN-PULL-SS (ss#107, crew 2 Oct): a goods-receive-notes build with no scope is 422 at the
gateway, so `PullStartBody` refuses a GRN start without both days or a `docNo`, the GRN
dialog requires both days (`requireWindow`), and "Pull again" re-pulls the same scope. DO is
unchanged in this lane (its start still allows no scope); flagged to crew.

## 1.9 Crew end-to-end pass (2 Oct, on 4a7cf723) and what changed

Pass: pull 7 documents / 71 lines, compare as expected, Confirm created 7, re-pull
idempotent, GR-0002..0005 linked to SPO-0085..0088. Gaps closed, red-first (baad9127) then
fixed (34a67357 + this commit), each fix killed by its test:

1. **Waiting lines link when their SPO arrives.** `link_waiting_grn_lines` runs from the
   shipping-order ingest's post-commit hook (`_run_document_hooks`, next to forward
   matching, which skips AutoCount GRNs) over the AutoCount GRN lines naming the SPO
   (`from_doc_no`, `our_po_no` or `spo_number_raw`, matched the upload's way), through
   `_grn_links` (k-th line, quantity confirms), then the receipt hook. No later GRN batch
   is needed. Tests `test_gp_e2e_*` (GR-2026/10-0006 / SPO-2026/09-0115 shape, the reordered
   SPO, company B never reached).
2. **`from_doc_no` is what a re-link reads.** AutoCount sends no OurPONo, so an unlinked
   line has `our_po_no` and `spo_number_raw` NULL and keeps `from_doc_no`; pinned in the same
   test before the SPO arrives.
3. **The totals row is not a line.** `window_excel_rows` leaves out rows without a document
   number when the sheet carries that column (the browser parser omits blank cells, so the
   key is absent); 72 became 71. Test `test_gp52f`.
4. **Apply job Outcome card.** The DO and GRN applies write the `result` envelope
   (`ImportOutcome.finalize`); a fresh job no longer reads "ran before per-row outcome
   capture". Tests `test_gp40`, `test_dp_20c`.
   Round 2 (crew re-test at d54cc02d): an unchanged re-apply wrote no row, so the card read
   Skipped 0 against `skipped_rows` 7. The apply now writes one skipped row per unchanged
   document (code `unchanged`, "Already up to date"; the preview's Changes tab still lists
   only what Confirm changes), and the envelope's counts equal the job's own columns.
   `test_dp_22` re-pinned to the two skipped rows.

Crew re-test at d54cc02d: gap 1 linked 36 of 36 waiting lines (k-th by item, quantity
exact, received 3029 = allocated, GR-0002..0005 unchanged); gap 3 compares 71 lines.

## 2. Build order (tests first)

1. This plan + UAC + red tests (`be/tests/test_autocount_pull_goods_receive_notes.py`,
   `be/tests/test_ingest_autocount_grn_line_link.py`). Done.
2. Live evidence (crew) -> the line-order design (1.3) and its tests. Done.
3. Backend: ingest R2/R4 + pool `cancelled` + waiting fill; maps + slug + migration + gate;
   preview / apply / hook; rows / download / compare.
4. Frontend switches + vitest.
5. Kill tests, reviewer + security-reviewer (permission slug, external data), hand-test script.
6. Compare on a real DocDate window against the users' GRN Excel: 0 unexplained differences.
