# PLAN: AutoCount GRN ingest fills the header SPO number (GRN-HEADER-SPO)

Status: **in build** (small fix track: backend only, no migration, no auth/RBAC change, no new
ingest surface). UAC: `grn-header-spo-04oct-acceptance-criteria.md`.

## Problem (owner, 4 Oct)

AutoCount-fed GRNs show header "SPO Number" = "-" (GR-2026/10-0008 on prod) although the
AutoCount user typed the SPO at the GRN header and the lines are allocated correctly
(SPO-2026/09-0118). The header field is `picking_headers.spo_number`
(`be/app/models/procurement.py:674`, shown by `GRNDetail.tsx:207-208`); the AutoCount ingest
never writes it (`_parse` header keys and `_apply_grn` in
`be/app/services/autocount_doc_ingest_service.py`).

## Fix

1. `_GRN_HEADER_SPO_FIELDS` (one tuple, one place) lists the AutoCount header keys that may
   carry the typed SPO, in priority order. Until the owner confirms the prod key from
   `source_record`, it holds `RefDocNo`, `Ref`, `Description` (every free-text header key of
   the live field list in `tests/fixtures/autocount/grn_live_sample.json`). The first value
   that is a known SPO number (`grn_spo_matching.spo_number_known`) becomes the header SPO.
   A value that is not a known SPO (the default "GOODS RECEIVED NOTE" description, a supplier
   ref) is ignored.
2. Fallback: the distinct SPOs the GRN's lines resolved to (Seq order, matched the upload's
   way). One SPO: that SPO. Several: joined with ", " like the Excel import's
   `_header_spo_display`.
3. Neither: the header SPO is left as it is (never cleared by a push, so an adopted Excel GRN
   keeps its value). A re-push that changes it counts as `updated`.
4. Plan `PLAN-autocount-grn-do-ingest-29sep.md` section 2.4 gains the `spo_number` row.

## Tests (red first, `be/tests/test_ingest_autocount_grn_header_spo.py`)

- header field holds a known SPO: header SPO = that value, even when lines name another SPO
- header empty, one line SPO: header SPO = the line SPO
- header empty, lines on two SPOs: "SPO-A, SPO-B"
- header value not a known SPO: falls back to the line SPO
- re-push of an existing GRN (stored with no header SPO) fills it and reports `updated`
- no SPO anywhere: header SPO stays NULL; a re-push never clears a stored value

## Open

- Which header key the AutoCount user types the SPO in (crew-ask on the PR). Changing the
  tuple is the only edit the answer needs.
