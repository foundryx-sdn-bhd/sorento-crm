# UAC: chatbot empty "choose who to route to" list + phantom open_question_answer pick

Lane: CHATBOT-EMPTY-ROUTE-PICK. Plan: `PLAN-chatbot-empty-route-pick.md` (alongside).

## Journey

A customer with a DO list on screen asks about another customer and a date. The bot either
asks which customer they mean (when the name is ambiguous) or says the list is empty in one
line. It never prints a routing picker with no names in it, and a pick the parser declares
when nothing was asked changes nothing.

## Acceptance criteria

- AC-1 With no open question (no pending pick or offer, no stock task), a parser emission
  carrying `open_question_answer.mode != null` and/or non-empty `reference_positions` is read
  as the null answer and no positions before any reader; the `understood` record's
  `derived` shows the drop and a `phantom_answer` trace event names what was dropped.
  Test: `test_chatbot_empty_route_pick.py::TestPhantomAnswerIsDropped`,
  `test_owner_turn_inside_the_open_list_asks_which_customer`.
- AC-2 With an open question the declared answer is untouched.
  Test: `test_an_open_question_keeps_the_answer`.
- AC-3 Two questions are answered by a position without being an `Open question:` object:
  the ideation media menu (`ideation.pending_media`, read by `lanes/ideate.py`) and the top
  selling questions ("How many?", "By quantity or by amount?", "Customer or sales agent?",
  `focus.top_selling.asked`, read by `turn/apply.py`). While one is outstanding
  `reference_positions` stay; only the declared answer goes.
  Test: `test_a_question_answered_by_a_position_elsewhere_keeps_the_positions`,
  `test_positions_read_elsewhere_names_the_two_questions`,
  `test_the_first_one_answers_the_top_selling_who_question`.
- AC-4 Inside an open order list that answered (R6), when the routing picker's question is
  taken out, its header, its numbered rows, its close line, the escalate offer sentence and
  the multi-company group lines all leave the text; no blank line stands where they were.
  Superseded in part by owner ruling 4 Oct 2026 (PICKER-ESCALATION, "any no answer should
  get the escalation question"): an empty list is a no-answer and keeps its escalate offer
  and routing picker whole (a barred contact keeps "Please refer to your salesman."); the
  one-line "No orders matched these." is retired.
  Test: `TestListReplyDropsTheWholePicker`, `test_an_empty_list_inside_the_open_list_keeps_the_offer_and_picker`,
  `tests/chatbot/test_picker_escalation_offer.py`.
- AC-5 A routing picker never renders with zero rows (`answer_bridge._miss_company_picker`
  returns the plain offer when no option could be built; `member_offer` already returns
  `member_offer: False` with no members).
- AC-6 The owner's turn ("Zhin heng delivered on 23/9", customer resolving to two families,
  phantom pick) asks "Which customer do you mean? Please choose:" with both families, as it
  did on prompt v40, instead of a miss.
  Test: `test_owner_turn_inside_the_open_list_asks_which_customer`.
- AC-7 A first ask (no list open) is unchanged apart from the drop: the escalate offer and
  a picker with rows still render.
  Test: `test_a_first_ask_is_untouched`, `test_owner_turn_as_a_first_ask_answers_the_ask_not_the_pick`.
- AC-8 No customer-facing wording is new: the picker strings are the existing ones, moved to
  constants; "Please refer to your salesman." handling is untouched.
