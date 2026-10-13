# PLAN: OI links follow the exact PO line to its SPO allocation (OI-SPO-EXACT)

Status: Built, reviewer READY, awaiting crew hand test on a prod copy (small fix track: backend
only, no migration, no auth/RBAC change; production diff under 100 lines, tests and the repair
script make up the rest of the ~1,000).
Lane OI-SPO-EXACT. UAC: `oi-spo-exact-acceptance-criteria.md` alongside.

## Problem (owner, 13 Oct)

OI links show an SPO that the SO line's PO line is not on.

Case 1 (crew-verified on the 12 Oct prod backup): OI-2601-9001, SO900001, SRTTEST01 32 BRW-BB.
The SO line's PO line on 202601-S9001 (32, received 0) names the SO line's ref, and no SPO
allocation names that PO line. `pair_needs._through_po` then fell back to ANY SPO allocation of
the same PO number and product, and picked SPO-2026/01-9001 line 25 (36, BRW-IB, received),
which belongs to another PO line bought for another SO line. The worklist's derived SPO entries
(`_append_derived_spo_entries`) and its filters/cards (`_derived_spo_query`) matched the same
way, by PO number and product.

Case 2 (OI-2601-9002 SRTTEST02) is not reproducible on the 12 Oct backup; likely the same
fallback. The repair script's dry run on a later prod copy will say.

## Rule (owner: AutoCount linkage is the truth, explicit links only, no matching guesses)

1. SO line -> the PO line whose `from_so_line_ref` equals the SO line's `source_ref` (or a book
   claim, `autocount` / `po_upload`, that pairs them).
2. That PO line -> ONLY the SPO allocations whose `from_po_number` AND `from_po_line_ref` name it
   (`from_po_line_ref` compared stripped, as before).
3. None -> the row links to the PO line itself.
4. Never "any allocation on this PO + product". Same rule for the worklist display entries and
   the worklist's derived-SPO filters and stage cards.

## Behaviour card

| Situation | Before | After |
| --- | --- | --- |
| PO line has its own allocation(s) | own allocation first, PO line for the remainder | unchanged |
| PO line has no allocation; a sibling line of the same PO + product does (case 1) | sibling's SPO | the PO line itself |
| Allocation states the PO number but no PO line | taken (fallback) | not taken; the PO line answers |
| SO line bought on two PO lines of one PO, each with its own container | each line's own container (exact key already existed) | unchanged; nothing crosses lines |
| Received vs open allocation | capacity is the allocation's full size either way (AC-S1-12, history) | unchanged; not this lane's question |
| Worklist: PO-linked row, open allocation of a sibling PO line | shown as derived SPO, counted in the SPO card, found by `spo_number` filter | not shown, not counted, not found |
| PO line with blank `source_ref` | could reach allocations by document | reaches none (14 Sep prod copy: 0 such lines) |
| Manual (non-auto) links | untouched | untouched; the repair never reads them |

Real-data examples and counts: the sandbox has no prod data. `scripts/repair_oi_spo_exact.py`
(dry run) lists every row it would change (OI number, product, old -> new) and the count of
allocations that state a PO number but no PO line. The 14 Sep prod copy held 0 of those
(`_less_own_shipments` docstring), so no fallback is built for them; if the dry run on the prod
copy reports any, crew asks the owner before anything is added.

## Changes

- `app/services/project_order_inquiry_import_service.py`: `_chain_allocations` returns only the
  `(PO number, PO line ref, product)` map; `_through_po` walks only that.
- `app/services/project_order_inquiry_service.py`: `links_for_rows` collects
  `(po_number, po line source_ref, product)`; `_append_derived_spo_entries` matches on
  `from_po_number`, `btrim(from_po_line_ref)`, `product_id`.
- `app/services/order_inquiry_worklist_service.py`: `_derived_spo_query` adds the line key.
- `scripts/repair_oi_spo_exact.py`: dry run by default, `--apply` commits per company. Read the dry run before `--apply` (an older cascade auto link
on a sibling's SPO has the same signature and is moved too; a row left partly linked prints
`SHORT BY <qty>`). Touches
  only AUTO links on an SPO allocation with the fallback's signature (an allocation of the
  line's item, from a PO the line's book names a line on, naming none of the line's book
  documents). Removes them through `_remove_links`, re-pairs the freed need through
  `pair_needs`, writes through `_write_link` (`auto: oi_spo_exact_repair`), refreshes link
  state. Idempotent: a second run reports 0.

## Tests

`tests/test_oi_spo_exact.py` (case 1 via ref and via claim, own allocation still first, PO-number
-only allocation not taken, split across two PO lines, the repair script);
`tests/test_order_inquiry_derived_spo.py::test_oi_spo_exact_an_allocation_of_a_sibling_po_line_is_not_derived`.
Existing fixtures that seeded allocations naming only a PO number now name the line, as
AutoCount's do.
