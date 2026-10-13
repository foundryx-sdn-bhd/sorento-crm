# PLAN: AutoCount Cancel SO / Cancel PO in the CRM (AC-CANCEL-CRM)

Status: Plan (behaviour card filed to owner, awaiting answers). Track: full (expected migration).

## Goal

The CRM accepts Cancel SO (XS) and Cancel PO documents from the shared service (ingest contract 2.8)
and derives cancelled quantity per SO / PO line on every sync, so cancelled quantity stops reading as
delivered, received or incoming.

## Owner rulings (13 Oct, closed)

- Cancelled qty is DERIVED on every sync: sum of cancel-doc lines for that SO/PO line. No events, no flags.
  Deleting the cancel doc in AutoCount lowers it. OI rows already retired stay retired.
- Partial cancel lowers planned qty; a line is "cancelled" only when fully cancelled.
- SRT first; Mocha later by configuration, same design.
- AutoCount is truth; no name / fuzzy matching. Cancel lines carry no line key since ~Nov 2024: match by
  SO/PO no + item (+ location + qty); ambiguous = manual review, never a guess.
- Never stuck UI; every failure ends in a clear state.

Details follow once the behaviour card is answered.
