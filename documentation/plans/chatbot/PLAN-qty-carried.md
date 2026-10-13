# PLAN: accept the parser's quantity on a carried product (QTY-CARRIED)

Status: in progress (small fix track: chatbot code only, no migration, no auth change)

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
