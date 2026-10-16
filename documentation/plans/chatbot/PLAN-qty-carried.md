# PLAN: accept the parser's quantity on a carried product (QTY-CARRIED)

Status: ready for review, awaiting merge (small fix track: chatbot code only, no migration, no auth change)

## Problem
After the parser prompt fix ("A QUANTITY IS A STOCK QUESTION"), a bare count after a product
turn ("HAVE 10??", "ada 10?", "有10个吗") parses as domain_hint inventory, intent check_stock,
with the quantity on the CARRIED product entity (current_message false, quantity 10,
demand_qty null). The code still asks "How many units of SRTWT5880-CR?" because it ignores a
quantity on a carried entity.

## Fix
The quantity check accepts a quantity the PARSER put on any entity of this turn's verdict,
carried or typed. Parser output only; no text or keyword matching. No new reply text.

## Keep
- A turn with no quantity anywhere still asks "How many units...".
- The number in THIS message replaces an earlier quantity.

## Tests
Red-first: carried entity with quantity 10 -> stock answer for 10, no ask.

## Implemented
Root cause: `turn_runtime.without_restated_rows` dropped a carried entity that echoes a settled
focus row, taking the parser's quantity with it. An echo carrying a quantity is now kept, one per
settled row of that code, with that row's uuid. Bare echoes are still dropped.
Known edge (reviewer nit, not changed): if the parser puts a quantity on only a few echoes of a
large settled set on a document-word turn, those few replace the axis.
