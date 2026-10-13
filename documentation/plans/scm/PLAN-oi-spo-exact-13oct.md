# PLAN: OI links follow the exact PO line to its SPO allocation (OI-SPO-EXACT)

Status: Plan (small fix track candidate, backend only, no migration). Lane OI-SPO-EXACT.

## Problem (owner, 13 Oct)

OI links show an SPO that the SO line's PO line is not on. `pair_needs` `_through_po` falls back
to ANY SPO allocation of the same PO number + product when no allocation names the exact PO line,
so an OI row is linked to an SPO line bought for another SO line.

## Rule (owner: AutoCount linkage is the truth, explicit links only)

SO line -> the PO line whose `from_so_line_ref` == SO line `source_ref` -> ONLY SPO allocations
whose `from_po_number` AND `from_po_line_ref` name that PO line. None -> link the OI row to the PO
line itself. No document-level fallback. Same rule for the display worklist.

## Work

1. Behaviour card (rule, examples, edge cases).
2. Red-first tests (case 1 shape).
3. Fix in `project_order_inquiry_import_service.py` and `project_order_inquiry_service.py`.
4. Guarded idempotent repair script (dry run default, `--apply`).
5. Reviewer, kill tests.
