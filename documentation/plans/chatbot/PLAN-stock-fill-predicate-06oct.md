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

A. `engine.py`: on a plan whose every fetch is a task's own (`spec.filters["task"]`), the
   resolver is handed the verdict as it is, never the unsettled carry. The task's slots are
   the fetch's subjects already (`turn/task.py::StockQtyTask.to_fetch`,
   `turn_runtime.make_tool_runner`'s task arm), so a carried word resolves nothing the fetch
   can use. With no entities `resolve_kinds` asks the resolver nothing: predicate None.
   `turn_runtime.make_tool_runner`: a `filters["task"]` spec never takes the turn's set
   predicate on its gate, so the rule holds even when the fill verdict itself carries a
   stray non-code word.
B. `turn/compose.py` never-stuck: a section that rendered nothing for its named subjects
   prints the existing "No {label} found for {codes}." miss line when the tool found
   nothing, and the `error` arm's existing retry sentence when the tool answered but the
   lane rendered nothing (a withheld set), never a bare header and never a false "no stock".

Chosen seams: the engine's carry hand-off is where `plan` is known, so the task fetch is
recognised there and nowhere earlier; `with_carried_entities` itself cannot tell a fill
turn from a later ask while a task is still open. The gate guard is the one place a
predicate is attached to a fetch.

## Tests

`sorento_crm_backend/tests/chatbot/test_stock_fill_predicate.py`: two engine replays of the
turn 121 state (in stock + discontinued; no stock, no incoming) with a seam assertion on the
resolver bodies, a tool-runner gate test for the predicate rule, two compose tests for the
never-stuck rule. Three older compose tests that pinned a bare header over an empty
hand-built envelope were updated to read the header over a row.
