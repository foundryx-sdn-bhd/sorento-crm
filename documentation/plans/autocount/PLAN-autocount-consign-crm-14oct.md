# PLAN: AutoCount Consignment + Consignment Return on the CRM, Cancel PO screens check (AC-CONSIGN-CRM)

Status: Plan (behaviour card in progress). Track: full (migration expected, new doc behaviour on ingest).
Lane: AC-CONSIGN-CRM. Base: PR #1623 (AC-CANCEL-CRM) head until it merges, then main.
Sibling: ss#122 (AC-CANCEL-PULL) pulls XS, XP, Consignment (CG-) and ConsignmentReturn.

## Owner rulings (13 Oct, closed)

1. Consignment = sales-order tables with a doc_type marker; shown in Order Inquiry / planning / outstanding
   labelled "Consignment"; NOT in the sales report until invoiced.
2. Consignment return: record the returned qty for reporting only; no demand / planning change.
3. Derived on every sync; exact links only; ambiguous = undetermined, shown, never auto-applied.
4. Partial cancel lowers planned qty; a line is cancelled only when fully cancelled.
5. SRT first, Mocha by configuration in the same build.

(Measured facts, rules and test list follow once the code read is done.)
