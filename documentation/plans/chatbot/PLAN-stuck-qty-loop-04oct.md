# PLAN: one rule for every held question (STUCK-QTY-LOOP)

Status: in progress, PR #1471 (size M after the owner widened it, 4 Oct 03:10; no migration)

## Problem

Live 4 Oct 02:56-03:00, prompt v42, webhook ingress: a contact with availability access asked
"low stock report for sorento water tap", the owner switched the contact to compact, and every
reply since (the same message again, and "clear") was the same blank-quantity "How many units
for each? 1. SRTWB1086 - ... 18. SRTWB7239SG -".

## Root cause (verified, reproduced in the real engine on main 61a54ef2)

- The reply is `StockQtyTask.question()` (`turn/task.py`), stored as an open `stock_qty` task on
  `respond_contacts.session_vars -> focus -> tasks`, opened by an earlier availability stock read
  (`engine._stock_ask_reply` -> `task.after_reply` -> `_rebuilt`).
- `low_stock_ask.take_words` (engine, before apply) moves the brand/category words off the
  entities, so the verdict reaching `task.run` is "inventory, no entities". `task.run`'s RESUME
  arm fired on exactly that shape (it never read `intent_hint`), so apply returned `fetch=[]` with
  the stored question as the reply. No fetch means the contact's current stock mode was never
  consulted: the question built under availability replayed after the switch to compact.
- "clear" the parser did not flag as `topic_reset` hit the same arm.
- Nothing else in the engine expired a held question: only the three escalation offers had a TTL,
  and nothing compared the access the question was built under with the access now.

## Held state inventory (every question that can capture the next reply)

`open_question` (one `Pending`, every kind in `pending.PENDING_KINDS` plus narrower-minted
`*_pick` / `*_ask` and `form_pick`), `focus.tasks`, `focus.required_ask`, `focus.set_page`,
`focus.set_clarify`, `focus.top_selling`. `ideation` is the ideate lane's MCP-owned draft pointer
and is classified exempt (it closes on the draft's own terminal status).

## Design (built)

`app/services/chatbot/turn/held.py`, one registry (`SLOTS`), three engine seams:

1. Load (`expire`, before the parser is shown an open question): drop everything held when the
   access fingerprint it was stamped under differs from the contact's now
   (`engine._access_fingerprint`: tier, stock allowed, availability mode, escalation allowed,
   salesman notify, packing list, field-reveal grants), or after `HELD_TTL_TURNS = 6` turns
   since the held state last changed.
2. Verdict (`consume`, before any answer reader): `topic_reset` clears everything; a message
   whose intent differs from `focus.intent` and answers nothing held (parser fields only:
   `decide()` ANSWER, a quantity the stock task claims, `open_question_answer`) clears
   everything, and marks the verdict so `decide()` reads a would-be refine as a fresh ask
   (carried entities of the old intent never reach the resolver: crew report 2).
3. Tail (`stamp`, both payload writers): `held_turn` + `held_access` on the focus when the held
   state changed. Rows written before this read as unstamped and are stamped on their next turn.

Narrowings forced by the existing suite (owner-tested behaviour, #833 / #1323):

- Only a change between two intents the routing table knows (`policy_rows` intents) counts; a
  free-phrase synonym is not a new question.
- Escalation offers survive a new intent (they accept only an explicit yes, position or company
  pick, and keep their own 3-turn clock); reset, TTL and access change still drop them.
- Crew report 2's carried-entity rule drops only ask-owned `focus.extra` kinds
  (`apply.INTENT_OWNED_EXTRA = {"sales_agent"}`): products and grounded specifications still
  carry across intents ("cert?" after "any gunmetal basin has incoming?").
- The engine's own answer readers (clarify pick, set count, required-ask reply, top selling) tell
  `consume` they answered, so their answers are never dropped.
- R22 ("outstanding_scope / outstanding_detail are sticky, no TTL") is superseded by the owner's
  4 Oct rule: they now lapse after `HELD_TTL_TURNS` untouched turns like every other question.

Plus: `task.run`'s resume needs an explicit signal (a `check_stock` intent, or a task product
named under no other intent); `answer_bridge` never turns a lane's own `required_ask` into a
not_found miss.

## Unstick on prod (needs owner approval; read-only find first)

See the crew report on PR #1471 for the SELECT and the UPDATE. After deploy no contact needs it to
get out: their next message of another intent drops the task, and a low stock ask no longer
resumes it; the UPDATE only clears the stale task before that.

## Open (owner decision, not in this lane)

- The parser prompt does not list "clear" / "reset" under `topic_reset`; an unflagged "clear"
  reads as "stock?" over the carried products (a fresh read under current access, not a replay).
- The wider "intent-owned frames" redesign (crew will ask the owner).

## Tests

`tests/chatbot/test_held_state.py` (registry contract, kind x rule matrix, stamp, stuck loop,
crew report 2) and `tests/chatbot/test_held_state_engine.py` (engine replay of the owner's
sequence).
