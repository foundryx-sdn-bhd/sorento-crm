# PLAN: stock_qty fill turn answers the task's product (STOCK-FILL-PREDICATE)

Status: in review (PR #1502); track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

UAC: `stock-fill-predicate-acceptance-criteria.md` (alongside).

## Report

Owner, 6 Oct 2026 13:20 MYT, production, prompt v47, turn 121 of one dealer conversation.
Turn 120: the customer sent a photo; the bot replied "I read SRTWC8383-RL-GY and SORENTO
from that photo. How many units of SRTWC8383-RL-GY?". State after turn 120:

- `focus.products = [SRTWC8383-RL-GY, uuid settled]`
- `focus.extra.promotion = [{raw "SORENTO", hint "promotion", canonical_code "SORENTO"}]`
  (a parser mis-tag of the brand word; prompt matter, out of scope here)
- `tasks = [{kind stock_qty, slots [{SRTWC8383-RL-GY: null}], domain inventory, status open}]`

Turn 121: the customer typed "1". Parser: no entities, `entity_op reuse`, `check_stock`
under `inventory`, `open_question_answer {mode fill, items [{qty 1, code SRTWC8383-RL-GY,
position 1}]}`. Reply sent:

```
*stock* for SRTWC8383-RL-GY:

Please refer to your salesman.
```

Expected: `SRTWC8383-RL-GY x 1: ✅ Got stock.` with the discontinued marker (the product is
discontinued), or the no-stock line.

## Root cause (crew scout, origin/main 929e3bd23)

1. The fill turn handed the carried uuid-less SORENTO row to the resolver
   (`turn_runtime.with_carried_entities(unsettled_only=True)`, called from `engine.py`).
2. `resolve_gate.resolve_entity_body` adds `require={"stock": true}` for `check_stock`; the
   guard that drops it fires only on a typed product CODE, and SORENTO is a promotion hint.
   The resolver counted every product with stock (356 KB payload).
3. The predicate rode into the inventory fetch (`make_tool_runner` -> `gate["predicate"]`).
4. `output_structurer` withheld the rows of a set past `SET_LIST_MAX`, leaving only the set
   header; `envelope_of` stripped it (not a counted set): `lane_text ""`, no figures.
5. The composer printed the bare domain header; `envelope_missed` filed it as a total miss
   and the salesman line closed it.

## Fix (code only; the parser prompt is not touched)

A. (Owner ruling, 6 Oct 2026 17:40: the principle, not the turn type.) A fetch already
   scoped to a specific resolved product (a spec entity with a real uuid) never takes a
   set predicate, on ANY turn: a task fill, a plain reuse, a fresh pick. A set predicate
   exists only for a set question with no specific product. A stray non-product entity (a
   stale brand / promotion / category row without uuid) may resolve on its own but never
   gates or widens the product lookup. One place: `turn_runtime.make_tool_runner`'s gate,
   where the predicate is attached (`_scoped_to_a_resolved_product(spec)` reads the plan's
   own spec, never the resolver's rows, because a set question's qualifying population
   reaches `compatible_entities` with uuids of its own). The key is popped, not left
   unset: the gate starts as the resolver's own gate dict, which already carries it. The
   engine's carry hand-off is unchanged (an earlier revision skipped the carry on task
   fetch turns; the general rule made that special case unnecessary).
B. `turn/compose.py` never-stuck, with the #1501 principle (owner, 6 Oct 17:50: no claim
   without a lookup). A section that rendered nothing for its named subjects prints the
   existing "No {label} found for {codes}." miss line ONLY when a tool actually ran for
   those codes and came back empty (`envelope_of`'s `tool_ran`: `fetch.tool` is stamped by
   a real call, and an `outcome: not_found` fragment was a call not about them). When the
   tool answered but the lane rendered nothing (a withheld set), or when no tool ran for
   the codes at all (a set predicate's refusal, a gate refusal), it prints the `error`
   arm's existing retry sentence under the header. Never a bare header, never a stock
   claim nothing looked up.

Chosen seam: the gate guard is the one place a predicate is attached to a fetch, and the
plan's spec is the one record of whether the question is about a specific product.

Observed on the blank test schema, reported and not changed here: a carried uuid-less word
that the resolver cannot place is named by production's not-found composer ("Couldn't find:
SORENTO.") although nobody typed it this turn, and that composer's row re-render carries no
discontinued marker. In prod the word is a real brand and resolves.

## Tests

`sorento_crm_backend/tests/chatbot/test_stock_fill_predicate.py`: two engine replays of the
turn 121 state (in stock + discontinued; no stock, no incoming), the sibling replay (a plain
`check stock` reuse turn over the carried product and the stale extra, no task: the quantity
question, then the availability line), each with a seam assertion on the gate the lane
received (no predicate, the product alone) and on the stock tool's product ids; a
tool-runner gate test (task spec and plain product spec drop the predicate, a class-word
spec keeps it); two compose tests for the never-stuck rule. Four older expectations in three files pinned a bare header over an
empty hand-built envelope: the three header-grammar tests now read the header over a
row, and the blocked-contact escalation test expects the miss line before the salesman
line.
