# PLAN: SET-STOCK-RENDER - chatbot stock answers for a product set read right

Status: PARKED after review (owner 16 Oct 18:10: owner-visible chatbot changes wait for the EVAL-BENCH chatbot hand test). Built: fixes ca3de65de + cb8e72686, PR #1744 head 6bb8a0d3c, reviewer + security reviewer done; Q1 a, Q2 b2 (crew confirmed), Q3 a. Size M, LEAD pattern. Not the small fix track:
new reply wording the owner notices.

## Why

Owner, 16 Oct 2026 ~14:00: "i am just saying the answer of the matching spec looks wrong". His
probes on 16 Oct 04:01-04:03Z (dev stack, parser v94), diagnosed in crew report
CHAT-ADVISOR-SPIKE-owner-probes-1720.md rows 1 and 4:

- "any undermunt basin has stock?" (dual Sorento + Mocha contact): header
  "under counter wash basins with stock (10)", then 34 raw product x location rows, 15 of them
  with on hand 0 under a "with stock" heading, no per-brand summary.
- "any stock for undermount basin" (staff): "(20)" then 33 unsummarised location rows; Bravat
  codes labelled "Company: Sorento"; footer "Data last updated: 21/09/2026".

## Bindings (owner rulings before this lane)

- Do NOT hide empty stock locations by default (owner 16 Oct: no).
- Availability = on-hand basis.
- One fact per line `Label: value`, no " · ".
- Never leak IDs/UUIDs; truthful, no fabricated replies.
- The parser decides meaning (no keyword rules).
- Staff see numbers, dealers see got-stock.
- Stay in the answer/presenter layer: lane CHAT-ADVISOR-SPIKE owns parser/grounding (#1720, #1737).

## Steps

1. Verify root causes (file:line) and pull 3-5 real dev examples (read-only, sorento_cagent_stack).
2. Behaviour card `CARD-set-stock-render-16oct.md` filed as ONE `crew report --ask`; build waits on
   the owner's answers.
3. UAC `set-stock-render-acceptance-criteria.md` from the rulings.
4. Tester subagent: red tests from the UAC; `test(red):` commit alone; red-proof note.
5. Coder subagent: make them green without touching the tester's assertions.
6. Kill test; reviewer subagent; parser-gold TRAIN must not drop; crew held-out; live run on the
   prod prompt (spend cap $3); hand test script; PR.

## Root causes (verified 16 Oct on main c11f4dcd1)

All seven claims CONFIRMED, two with moved lines; file:line list in the card's "What happens
today" (items 1-9). The render lands in `fetch.output_structurer` (fetch.py:3487-3497 rows,
:3596-3681 set header) over the MCP's structured items; `_stock_compact` (presenters.py:1451)
is the per-product block to reuse.

## Design (card v2, owner direction 16 Oct 17:40; defaults Q1 a, Q2 b, Q3 a)

UAC: `set-stock-render-acceptance-criteria.md` (SSR-1 to SSR-14). Touch points for the coder
(the tester does not see this section):

1. R2 over 50 (SSR-5/6): stop withholding rows. `fetch.output_structurer` withheld branch
   (fetch.py:3625-3628, and its `answers` / attachment withholding at :3703, :3712) and the
   breakdown request `resolve_product_set(breakdown_over=...)` (references.py:3456,
   resolve_gate.py:848); `build_set_header` (answer.py:2986-2992) then never prints
   `breakdown_lines`. The header's existing "showing 1 to N" form carries the cut.
2. R2 misspelt word (SSR-7): `_unknown_value_members` (references.py:1797-1840, early return
   :3331-3343) and `unknown_values_reply` (answer.py:2895, called :4429): answer the set without
   the word through the normal set path, then the existing "Couldn't find" line
   (`not_understood_line`, fetch.py:3661-3665).
3. R3 with stock (SSR-9/10): a per-call flag beside the policy check in the stock list
   (inventory_service.py:870-872 detailed rows, :1548-1555 compact lines), passed only by the set
   fetch (fetch.py:1234-1241) when the set's `require` carries stock. Not
   `quantity_operator` (it reads available, not on hand, and moves the compact total).
4. R4 twins (SSR-11): the set's `product_ids` carry every company's product for a family code,
   as the code gate does (gate.py:677-698); the count stays per family
   (product_predicate_service.py:1061).
5. R5 (SSR-12): verify `with_brand_from_offer` (turn_runtime.py:4373) fires after a fully listed
   set (it reads `set_brands`, carried at turn_runtime.py:4298 / engine.py:6969); fix only if the
   test is red.
6. R1/SSR-1 to SSR-4 hold on main already for the row loop; tests pin them.

Simplest-thing check: no new format, no new layer; one flag on an existing call, and two
branches removed.
