# PLAN: refer-only reply fixes (REFER-ONLY-FIXES)

Status: Review. Track: small fix (diff under 300 lines, no migration, no auth/RBAC change, no new ingest surface).

Owner ask (3 Oct 2026, console, parser v43):

1. A contact who may NOT escalate gets "Please refer to your salesman." and must never see the
   CS escalation picker or any staff list after it. Example: an order ask for SRTWC8605-FT with
   no match printed "But no order matched these. Please refer to your salesman." followed by a
   numbered staff list, and the scope header "Customer: all customers / Product / Dates: all dates".
2. Dealer availability: "stock srtwc286-sh" -> "How many units?" -> "5" printed
   "SRTWC286-SH x 5: ❌ No incoming. Please refer to your salesman." With no stock AND no
   incoming the line must say "❌ No stock and no incoming."; with some (insufficient) stock
   and no incoming the existing wording stays.

## Cause, measured (engine turn reproduced word for word in `tests/chatbot/test_refer_only_fixes.py`)

Paths under `sorento_crm_backend/app/services/chatbot/`.

- The CS member picker is printed by `tail/member_offer.py::build_cs_member_offer` (header
  `PICKER_HEADER`, numbered rows, close) and, for a did-you-mean + roster, by
  `answer_bridge.py::_cs_roster_text_block` / `tail/reply_ladder.py` (`ROSTER_HEADER`).
- Every reply for a contact who may not escalate goes through one strip, `order_list._without_picker`,
  reached from: `engine.py:6249`
  (`_run_answer` backstop), `engine.py:7064` (casual lane), `engine.py:8308` (`run_tail`: canned
  and escalation lanes), `engine.py:5622` (`order_list.list_reply`, inside an open order list)
  and `engine.py:5750` (`dealer_stock.without_escalation`, an availability-only dealer's stock or
  ETA reply).
- The leak: `_without_picker` removed the header and close by their text, but a numbered row only
  when it matched a label on the question being dropped. `answer_bridge.answer_for`'s barred arm
  dropped the `member_offer` question and threw its options away, so the `_run_answer` backstop
  saw the frame with no labels: header, close and offer sentence went, the five staff rows stayed
  under the refer line. `dealer_stock.without_escalation` never touched the picker at all.
- Fix: a picker block is structural. Every line under `PICKER_HEADER` / `ROSTER_HEADER` up to the
  next blank (member row, `*Company:*` group header, no-members note) goes with the frame,
  whatever question is attached (`order_list.py:264-310`, the block rule at `:283-302`), so the
  `_run_answer` backstop takes the rows even after `answer_bridge.py:1893-1899` dropped the
  question. `dealer_stock.py:39` strips the picker too and drops the member options from the
  question (review SF2). The bridge itself is unchanged (review SF1: the backstop covers it).
- Scope header: `lanes/business/answer.py:3689` prints it on an order-scope miss. Owner ruling
  (3 Oct 2026, crew-ask answer (b)): it stays for everyone, refer-to-salesman misses included;
  only the staff list goes.

## Availability outcome -> wording (`sorento_crm_mcp/sorento_crm_mcp/presenters.py:1476-1515`)

Branch from `app/services/stock_ask_branch.py::branch`; `available` = on hand minus open SO.

| Outcome | Branch | Line |
| --- | --- | --- |
| Q above the category max (or none set) | too_big | `🚫 the quantity is more than what I can confirm here. Please refer to your salesman.` (unchanged) |
| available >= Q | in_stock | `✅ Please refer to your salesman.` (unchanged) |
| 1 <= available < Q, Q within the max, incoming or not | in_stock | `✅ N available. Please refer to your salesman.` (unchanged) |
| available < 1, shipment due | incoming | `❌ ETA dd/mm/yyyy.` (unchanged) |
| available < 1, nothing incoming | no_incoming | `❌ No stock and no incoming. Please refer to your salesman.` (was `❌ No incoming. ...`) |

`no_incoming` is reachable only with no stock (some stock is `in_stock`, AVAIL-MODE-REPLIES
rule 2), so it is the only line that changes. ETA asks (`CODE: ✅ ETA dd/mm/yyyy`,
`CODE: No ETA`) are untouched.
