# PLAN: AutoCount Consignment + Consignment Return on the CRM, Cancel PO screens check (AC-CONSIGN-CRM)

Status: Build (round 2, local takeover 14 Oct; Q6 (a) answered: Qty returned column on consignment SO detail lines). Track: full (migration, new doc behaviour on ingest, contract bump).
Lane: AC-CONSIGN-CRM. PR #1654, branch `claude/autocount-consignment-crm-r7844r`, base main (#1623 merged).
Sibling: ss#122 (AC-CANCEL-PULL) pulls XS, XP, Consignment (CG-) and ConsignmentReturn; ss#125 (AC-CONSIGN-PULL)
adds `Details[].TransferedQty` on consignments, sent only when the CRM's `/external/contract` advertises >= 2.9.
UAC: `consign-crm-acceptance-criteria.md` (alongside).

## Owner rulings (13 Oct, closed)

1. Consignment = sales-order tables with a doc_type marker; shown in Order Inquiry / planning / outstanding
   labelled "Consignment"; NOT in the sales report until invoiced.
2. Consignment return: record the returned qty for reporting only; no demand / planning change.
3. Derived on every sync; exact links only; ambiguous = undetermined, shown, never auto-applied.
4. Partial cancel lowers planned qty; a line is cancelled only when fully cancelled.
5. SRT first, Mocha by configuration in the same build.

## Crew answers (14 Oct, closed)

- Q1 (a): store the 2.8 shape; accept the additive 2.9 fields. ss#125 adds only `Details.TransferedQty`;
  DebtorCode / DebtorName / SalesAgent and line UnitPrice / SubTotal / UOM are already in the 2.8 shape.
- Gate, not cutoff: a CG becomes demand only when every item line carries TransferedQty; a 2.8 record stays
  stored only and projects on the next push that carries it. No DocDate cutoff.
- Q2 (a) outstanding = Qty - TransferedQty as sent. Q3 (a) same demand-class ladder and adoption as an SO.
- Q4 (a) every return line into `qty_returned`, type kept on the stored record, no split.
- Q5 (a) a consignment never counts in an SO-based sales figure.
- Key-less return groups: #1623's exact `group_whole` arithmetic. 'CVT TO DO' is only a DocNo suffix; never parsed.
- Part C: fix PO detail edit-mode outstanding and the GRN-to-SPO pool; test the incoming / ETA edge.

## Measured facts

- Masked real records (crew data, never committed): CG-2024/10-002 (2 lines, keyed return R-01),
  CG-202606-062 (3 lines incl. two `SAMPLE` at CON, key-less partial return R-H), CG-202608-072 (1 line,
  'CVT TO DO' return). Every line sits at location `CON`. FromDocDtlKey is NULL on every return line since
  Nov 2024 (2025: 0/3053, 2026: 0/1944).
- Shared dev DB `sorento_cagent_stack` (read only): warehouse `CON` exists; products SAMPLE, CB2516SS, CB2806A,
  SRTWT165, SRTSWT3011 exist; debtors 300-S075 / 300-J096 / 300-V033 exist; sales agents ACT, ERIC NG, JENNIFER
  are `project` demand class, so the three example CGs land as project demand (Fulfilment Planning).
- `app/api/v1/external/ingest.py:311` advertised `CONTRACT_VERSION = "2.8"` at takeover: ss#125 sends
  TransferedQty only to a consumer advertising >= 2.9 (`canonical/documents.py:603,708-729` on the ss branch),
  so without a bump no CG would ever project. The contract text (`contract.py:140-147,245-251`) still said
  "accepted and stored only".

## Design (as built in round 1, commits f77a59e30, d38ad95b6)

- CG stored by #1623 in `autocount_cancel_docs` (kind consignment), then written through the SO ingest
  (`DocumentIngestService(sales_doc_type=consignment)`) from the STORED record: `source_ref`
  `{book}:CSGN:{DocKey}`, lines `{book}:CSGN:{DocKey}:{DtlKey}` (`autocount_consignment.consignment_payload`).
- Status stated: void or vanished = cancelled; every line delivered >= ordered = closed; else open.
- A CG never adopts an SO's row and vice versa (`document_ingest_service._check_doc_type`).
- Returns: #1623's line table, kind consignment_return. FromDocDtlKey exact, else group's one line, else
  `group_whole`, else undetermined. `qty_returned` = sum of matched live return lines, capped at ordered.
  Every cancel sum filters to the cancel kinds (`_a_cancel()`), so a return never reads as cancelled.
- Migration `ac_consign_0001_doc_type`: `sales_orders.doc_type` (check constraint, default sales_order),
  `sales_order_lines.qty_returned` (default 0).
- Sales exclusion so far: `sales_report_service._common_filters`, `achievement_service.achieved_by_period`,
  `unassigned_amount`.
- Part C: `grn_spo_matching._measure` consumes `qty_cancelled`; `PurchaseOrderDetail.tsx` edit-mode
  outstanding adds the cancel back.

## Round 2 work (this round)

1. Contract 2.9: bump `CONTRACT_VERSION`, list the 2.9 consignment fields, rewrite the consignment field
   notes (consignment = demand, return = reporting only).
2. Sales exclusion sweep: every remaining SO-based sales reader found by the sweep excludes consignments.
3. "Consignment" label: SO list + detail, Order Inquiry, Fulfilment Planning, outstanding; `doc_type` (and
   `qty_returned` on a consignment line) exposed by the API. Label only, existing `Badge`; no new screen.
4. Part C: incoming stock / chatbot ETA edge test; fix if real. Screen-by-screen list with file:line in the PR.
5. Drop the brittle `down_revision` assertion in `test_migration_ac_consign_0001.py` (breaks on every re-parent).

## Test list (round 2, red first)

- Contract: `/external/contract` reads 2.9; `consignments` fields list TransferedQty and the 2.9 header/line
  fields; the 9 suites that pin "2.8" read "2.9".
- Each remaining sales reader: a consignment line seeded beside an SO line is left out.
- API: SO list / detail rows carry `doc_type`; a consignment line carries `qty_returned`; OI and planning rows
  carry the source order's doc_type.
- FE (vitest): the Consignment badge renders for doc_type consignment and not for sales_order on each surface.
- Incoming: a cancelled allocation qty never reads as incoming when GRN picks sit on a sibling allocation.
