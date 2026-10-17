# CARD: AC-BILLING, billing documents S2 to S4 plus outstanding (17 Oct)

Status: APPROVED (owner 18 Oct ~05:40, "billing outstanding ok"); Q1 to Q5 all (a): Q2 = anyone with `finance.billing_documents.view` sees outstanding, no new slug (kept as the constant `OUTSTANDING_VIEW_SLUG`). Built on PR #1770: S2, S3, S5 green, reviewer and security review fixes in, browser pass PASS; owner hand test pending. Track: full (migration, contract bump, new ingest entity).
Lane AC-BILLING, PR #1770, branch `claude/autocount-billing-outstanding-351yyn`.
Plan: `PLAN-finance-billing-documents-27sep.md` (S0 #1314 and S1 #1321 on main). Schema reference:
`REF-autocount-billing-schema.md`. Owner 15 Oct ~21:00: the billing sync INCLUDES outstanding per invoice and per
customer, read-only; this overrides plan section 7 ("receipts, outstanding: not built") and rulings Q10/Q11.

## 1. What exists (measured on main fde0a559)

| Piece | Where |
|---|---|
| Tables `finance.billing_documents` + `billing_document_lines`, type CHECK invoice / cash_sale / credit_note / debit_note | `be/app/models/finance.py:62`, `:173`; `be/alembic/versions/fin_0001_*.py:166` |
| Ingest, read-back, deletions, contract 2.6 | `be/app/services/finance/billing_document_ingest_service.py`; doors `be/app/api/v1/external/ingest.py:132,159,188`; contract now `2.9` (`ingest.py:321`) |
| Line -> SO line, only by `from_line_ref` = an SO line's `source_ref` | `billing_document_ingest_service.py:487` |
| DO lines in the CRM carry `dtl_key` and their resolved `sales_order_line_id` (DO ingest 2.7) | `be/app/models/order.py:567-591` |
| Customer AR seam, declared "ingested from AutoCount (D23)", **nothing writes it** | `be/app/models/order.py:167-172` (`ar_outstanding`, `ar_ageing_json`, `ar_as_of`) |
| Its one consumer: the credit-exposure warning on project SO drafts (today it says "outstanding balance has not been ingested yet") | `be/app/services/project_so_draft_service.py:1805-1822` |
| No finance FE, no finance API router (S2 not built) | `fe/app/(protected)/` has no `finance/`; no `be/app/api/v1/finance/` |
| Customer record tabs Details, Branches, Asks | `fe/app/(protected)/order-management/customers/components/CustomerDetail.tsx:28-32` |
| SO record tabs General, Lines, Delivery, Transfers | `fe/app/(protected)/scm/sales-orders/[id]/components/SalesOrderDetail.tsx:1788-1803` |

## 2. Plan update for S2 to S4 (plus S5 outstanding)

- **S2** unchanged from the plan (FINANCE > Billing Documents list + record, UAC S2-1..S2-12), plus an
  **Outstanding** column on the list (right aligned, blank when not synced) and a filter "Open only".
- **S3** unchanged (SO record "Invoices" section, customer record "Billing documents" tab), plus: each row shows
  its outstanding; the customer record shows **Outstanding RM x as of <date>** in the meta strip under the header.
  S3-1 gains one CRM change: an invoice line sent with `from_doc_type = 'DO'` and `from_line_ref` =
  the DO line's ref resolves to the CRM DO line (by `dtl_key`) and from there to its `sales_order_line_id`.
  Exact only; most invoices are "Transfer from DO" in AutoCount, so without this hop the SO record would
  show almost no invoices.
- **S4** unchanged (runbook + tally), runs after 25 Oct on live access.
- **S5 (new) outstanding, read-only.**
  - Migration `fin_0003_billing_outstanding`: `billing_documents.outstanding Numeric(15,2) NULL`,
    `due_date Date NULL`, `outstanding_as_of timestamp NULL`. NULL means "not synced or not posted to GL", shown
    blank, never 0.
  - Customer total goes into the existing `customers.ar_outstanding` / `ar_as_of`, so the credit-exposure
    warning starts telling the truth with no code change there.
  - New push entity `ar_outstanding` (contract 2.10), one record per debtor, the whole truth for that debtor:

    ```json
    {"companyCode": "SRT", "records": [{
      "source_ref": "SRT_DB:debtor:1234",
      "customer_ref": "SRT_DB:1234", "customer_code": "300-R009",
      "as_of": "2026-10-26T09:00:00",
      "total_outstanding": 15230.40,
      "documents": [
        {"source_ref": "SRT_DB:IV:88213", "outstanding": 1100.00, "due_date": "2026-11-25"},
        {"source_ref": "SRT_DB:CN:4410", "outstanding": -200.00, "due_date": null}
      ]}]}
    ```
    `total_outstanding` is AutoCount's own debtor figure in MYR (see Q1). `documents` lists every OPEN AR item of
    the debtor (IV, CS, DN outstanding; CN unapplied as a negative). A billing document of that customer that
    is stored with a non-zero outstanding and is absent from the push is set to 0 (paid). A listed ref that is
    not in the CRM is kept out and counted in the verdict (`documents_unknown`), never created. Stale guard on
    `as_of`. Slugs: `finance.billing_documents.edit` to push, `.view` to read back (no new slug).
  - Where staff see it (all behind `finance.billing_documents.view`, see Q2): billing list column + "Open only"
    filter; billing record Details tab (Outstanding, Due date, as of); customer record meta strip + Billing
    documents tab column; SO record Invoices section column; the existing credit warning on project SO drafts.

## 3. Shared-service pull contract (same pattern as SO / PO / Cancel / Consignment SQL pulls)

Lives in `foundryx-shared-service`, its own PR there (this sandbox cannot reach that repo; a session with it does).

- **Billing documents -> contract 2.6 `billing_documents` (already on main).** Four presets on the
  `_linked_preset` pattern (`service_backend/modules/autocount/presets.py:681-703`), entity `billing_document`,
  `key_columns=("DocKey",)`, `watermark_column="LastModified"`, `doc_date_column="DocDate"`, `from_date` =
  2023-01-01. Header and line SELECTs per `REF-autocount-billing-schema.md` sections 2-3 (header query shape in the
  research 8.4), the billing line cut (keep non-zero `SubTotal`; drop text lines), the line fingerprint
  aggregates `COUNT(*), SUM(Qty), SUM(SubTotal), SUM(Tax), MAX(DtlKey), COUNT(FromDocDtlKey)`, plus
  `SUM(TransferedQty)` on IVDTL/CSDTL, in both the header OUTER APPLY and the periodic sweep. Identity
  `{db}:{IV|CS|CN|DN}:{DocKey}`, line `...:{DtlKey}`. Push order IV, CS, then CN, DN. Reconcile vanishings to
  `/billing_documents/deletions`. Line source refs: `FromDocType='SO'` -> the SO line's existing ref
  `{db}:{SO.DocKey}:{SODTL.DtlKey}` (`mapping.py:1499-1507`, the PODTL template `presets.py:348-356`);
  `'DO'` -> the DO line ref `{db}:DO:{DocKey}:{DtlKey}`; `'IV'` (CN lines) -> `{db}:IV:{DocKey}:{DtlKey}`.
- **Outstanding -> contract 2.10 `ar_outstanding` (new).** A separate task over `ARInvoice`, `ARDN`, `ARCN`
  per `DebtorCode`; a per-debtor fingerprint `SUM(Outstanding), COUNT(open), MAX(LastModified)` decides which
  debtors to push (a knock-off may not bump any `LastModified`, research section 6); hourly sweep plus a daily
  full pass. AR row -> billing doc ref by `SourceType`/`SourceKey` if those columns exist, else `DocNo` +
  `DebtorCode` (to confirm on access).
- **Precondition (owner, after 25 Oct):** the read-only SQL login has no SELECT on IV (nor DO/GR); it needs
  SELECT on IV, IVDTL, CS, CSDTL, CN, CNDTL, DN, DNDTL, ARInvoice, ARDN, ARCN (and Debtor, Item, already held).

## 4. Build now (seeded data) vs after 25 Oct (live access)

| Now, in this lane | After 25 Oct |
|---|---|
| S2 list + record, BE (list-query resource, detail endpoint) and FE | The research checks (REF section 6), flipping every [I] |
| S3 sections, the DO-line hop | Shared-service presets run on real rows; mapping fixes |
| S5 migration, `ar_outstanding` door + contract 2.10, customer total write, every outstanding UI | First `?dry_run=true` pushes, then live sync |
| Fixture JSON extended (IV from DO, CN against IV, DN charge line, CS walk-in, outstanding snapshot) | S4 backfill from 2023 and the tally (S4-2, S4-3) |
| S4 runbook draft; contract doc sections 14-15 | |
| Shared-service presets written against the schema reference (separate PR, needs a session with that repo) | |

## 5. Questions (owner)

1. **Customer total.** (a) AutoCount's own debtor figure pushed by the shared service (includes AR entries keyed
   straight into AR and unapplied payments), or (b) the CRM's sum of the synced billing documents' outstanding.
   **Rec (a):** it matches the AutoCount statement and the credit warning that already reads
   `customers.ar_outstanding`; (b) silently misses anything not raised as IV/CS/CN/DN.
2. **Who sees outstanding.** (a) Everyone with `finance.billing_documents.view`, no new slug, or (b) a separate
   `finance.outstanding.view` so a role can see invoices but not balances. **Rec (a):** nobody has asked for
   the split; the trigger for (b) is a role that must see invoices without balances.
3. **Wire shape.** (a) A per-debtor snapshot entity `ar_outstanding` (above), or (b) `outstanding` / `due_date`
   as extra fields on each `billing_documents` record. **Rec (a):** payments move outstanding without moving
   the invoice's `LastModified`, so (b) would re-push whole documents with lines for every payment, and it
   cannot carry the customer total.
4. **Invoice from a DO, link to the SO.** (a) Hop through the CRM's own DO line (exact by DtlKey, DO ingest
   2.7 already links DO lines to SO lines), or (b) leave DO-sourced invoice lines unlinked. **Rec (a):** most
   invoices are transferred from a DO, so (b) leaves the SO record's Invoices section nearly empty.
5. **Payment detail.** (a) Amount outstanding + due date only (no receipts, no who-paid-when, no ageing
   buckets), or (b) also receipts and knock-offs. **Rec (a):** it is what was asked and keeps the NDA scope
   (ruling Q6) to amounts; triggers for (b): the owner asks for collections, or for ageing.

## 6. Element -> component map (Phase 1, commits eb3c698b, 23ad8585)

| Screen element | Component | Repo path |
|---|---|---|
| FINANCE heading + Billing Documents item | menu entries (`permission` view, `moduleKey` finance) | `fe/config/menu.config.tsx`, `fe/lib/route-module-map.ts` |
| List page shell, header (no Add), Export | `PageHeader`, `Container`, `Button` gated by `useCanExportBilling` | `fe/app/(protected)/finance/billing-documents/components/BillingDocumentsView.tsx`, `fe/app/(protected)/finance/billing-documents/lib/permissions.ts` |
| Filters: Type / Status / Customer / Agent / Date / Open only / search | `SearchableMultiSelect`, `SearchableSelect` (clearable), `DateRangePicker`, `Switch`, `ListSearchInput` | same file; query string `fe/app/(protected)/finance/billing-documents/lib/listQuery.ts` |
| List grid (fixed, resizable) | `DataGrid`, `DataGridTable`, `DataGridColumnHeader`, `DataGridPagination` | same file |
| Type and Status pills | `BillingTypeBadge`, `BillingStatusBadge` (`Badge`) | `fe/app/(protected)/finance/billing-documents/components/BillingBadges.tsx` |
| Outstanding cell (blank / Paid / amount) | `OutstandingCell` | `fe/app/(protected)/finance/billing-documents/components/OutstandingCell.tsx` |
| Record header, meta strip, prev/next | `PageHeader`, `ListPager`, `BackToList` | `fe/app/(protected)/finance/billing-documents/[id]/components/BillingDocumentDetail.tsx` |
| Record tabs Details / Lines / Related | `Tabs` line variant, `Field`, `PanelDataGrid` | same file |
| Customer record: Billing documents tab | `BillingDocumentsView customerId` inside `CUSTOMER_TABS` | `fe/app/(protected)/order-management/customers/components/CustomerDetail.tsx` (+ `[id]/edit/page.tsx`, same tabs) |
| Customer record: "Outstanding RM x as of date" | `CustomerOutstandingMeta` | `fe/app/(protected)/finance/billing-documents/components/CustomerOutstandingMeta.tsx` |
| SO record: Invoices tab | `SalesOrderInvoices` | `fe/app/(protected)/finance/billing-documents/components/SalesOrderInvoices.tsx`, `fe/app/(protected)/scm/sales-orders/[id]/components/SalesOrderDetail.tsx` |
| Data | `useBillingDocumentsQuery`, `useBillingDocumentQuery`, `useCustomerOutstandingQuery` -> `billingDocumentService` | `fe/app/(protected)/finance/billing-documents/hooks/useBillingDocuments.ts`, `fe/app/(protected)/finance/billing-documents/services/billingDocumentService.ts` |

Mock data (illustrative, masked; the crew data case was a dealer with five SOs and no AR data anywhere
before 25 Oct) lives only in the Phase 1 mock and goes when the service is wired.
