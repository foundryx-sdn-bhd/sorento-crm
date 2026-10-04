# PLAN: AutoCount GRN ingest fills the header SPO number (GRN-HEADER-SPO)

Status: **built, in review on PR #1484** (small fix track: backend only, no migration, no
auth/RBAC change, no new ingest surface). UAC: `grn-header-spo-04oct-acceptance-criteria.md`.

## Problem (owner, 4 Oct)

AutoCount-fed GRNs show header "SPO Number" = "-" (GR-2026/10-0008 on prod) although the
lines are allocated correctly (SPO-2026/09-0118). The header field is `picking_headers.spo_number`
(`be/app/models/procurement.py:674`, shown by `GRNDetail.tsx:207-208`); the AutoCount ingest
never writes it (`_parse` header keys and `_apply_grn` in
`be/app/services/autocount_doc_ingest_service.py`).

## Fix

Owner ruling (crew, 4 Oct): the AutoCount GRN API has NO header SPO field (GR-2026/10-0007:
`Ref` holds a container number, `RefDocNo` '-', `Description` 'PURCHASE', Remark1-4). So the
header is never read for an SPO.

1. Header SPO = the distinct SPOs the GRN's lines resolved to (Seq order, deduplicated the
   upload's way, `_spo_match_key`). One SPO: that SPO. Several: joined with ", " like the Excel
   import's `_header_spo_display`.
2. None: the header SPO is left as it is (NULL on create; a push never clears a stored value,
   so an adopted Excel GRN keeps its own). A re-push that changes it counts as `updated`.
3. Plan `PLAN-autocount-grn-do-ingest-29sep.md` section 2.4 gains the `spo_number` row.

## Tests (red first, `be/tests/test_ingest_autocount_grn_header_spo.py`)

- header empty, one line SPO: header SPO = the line SPO
- header empty, lines on two SPOs: "SPO-A, SPO-B"
- an SPO-looking `Ref` / `Description` is never read: lines on a PO leave the header SPO NULL
- re-push of an existing GRN (stored with no header SPO) fills it and reports `updated`
- no SPO anywhere: header SPO stays NULL; a re-push never clears a stored value

## Open

- A line whose SPO arrives after the GRN links later through the waiting fill; the header
  fills on the GRN's next push, not at that moment.
- GRNs already stored (GR-2026/10-0008 included) are fixed only when AutoCount sends them
  again: a re-pull of their date range through the AutoCount pull, or the next edit in
  AutoCount. No backfill script is built; the trigger for one is a GRN that AutoCount will not
  resend and still shows "-".
- A re-push that moves every line off its SPOs keeps the old header SPO (never-clear rule).
- Side effect: `update_grn` (`be/app/services/procurement_service.py`) rebuilds lines against
  `grn.spo_number` when a user edits a GRN. An AutoCount GRN used to have no header SPO, so
  that branch never ran for one; now it can. The edit already dropped AutoCount links before
  this change and the next push restores them, so the outcome is unchanged in practice.
