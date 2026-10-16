# PLAN: SET-STOCK-RENDER - chatbot stock answers for a product set read right

Status: Plan (behaviour card pending owner rulings). Size M, LEAD pattern. Not the small fix track:
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

## Root causes (to verify in step 1)

Filled in from the scouts' file:line evidence.

## Design

Filled in after the owner's rulings.
