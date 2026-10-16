# PLAN: the OI Excel is one sheet, never split by month (OI-EXCEL-ONE-SHEET)

Status: in review, PR #1748 on `crew/oi-excel-one-sheet`; owner hand test pending. Track: small-fix (about 80 changed
lines, no migration, no auth/RBAC change, no new ingest surface). Split out of lane OI-LINE-SPLIT
(crew, 16 Oct 2026).

UAC: `oi-excel-one-sheet-16oct-acceptance-criteria.md` (alongside).

## Journey

Owner, 16 Oct 2026 ~22:05: "btw our OI excel cannot split by month, this applies to all places
where OI excel is printed".

## Where the OI Excel is built

One writer, `OrderInquiryWorklistService.export_xlsx`
(`sorento_crm_backend/app/services/order_inquiry_worklist_service.py`), called by:

- the Order Inquiries list export (`app/api/v1/projects/order_inquiries.py:567`);
- the export tasks (`app/tasks/export_tasks.py:794` OI worksheet with `EXPORT_HEADINGS[:10]`,
  `:1294`, `:1364`);
- the handover attachment on Confirm and resend (`store_handover_excel`,
  `app/services/project_order_inquiry_service.py:12631`).

The per-project export (`ProjectOrderInquiryService.export_xlsx`, `:12273`) was already one sheet.

## Change

`export_xlsx` writes every row to one sheet titled `ORDER INQUIRY` (supplier, item code, then
delivery date; undated rows last in their run). `EXPORT_UNDATED_SHEET` removed. AC-C3 of
`order-sheet-oi-reports-22sep-acceptance-criteria.md` marked superseded in part.

## Rulings

- TOTAL QTY totals a supplier + item run across every delivery date, once per run (crew ruling
  16 Oct 2026, follows the owner rule). Not per month inside the one sheet.

## Hand test

Owner script: `laneboard/scripts/1748.md` (real case OI-2609-0780 / SO423386, Sorento company).
