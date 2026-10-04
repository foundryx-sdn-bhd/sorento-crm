# PLAN: a status-filtered order miss names EVERY resolved order, not the first one

Status: in progress. Track: small fix. Owner ask 23 Sep 2026 ("the user asks 3 but it
only answers 1, why ah" / "yes").
UAC: `chatbot-order-status-all-orders-23sep-acceptance-criteria.md`.

## Evidence (prod, 23 Sep 2026 08:58, contact 900000105, turn 48)

- Message "STATUS DELIVERY / PS202609-0374 / PS202609-0398 / PS202609-0410". Parser:
  `domain_hint: order`, `order_status: delivered`, three `hint: order` entities.
- Gate resolved all three (`compatible_entities` has three `customer_order` rows, each
  match carries `display.status = "New Order"` and `display.customer_name`).
- `crm_order_management_orders_list` ran with the three numbers AND status=delivered;
  all three are `New Order`, so it returned "No matching results found".
- Reply: "Order PS202609-0374 (MATRIX EXCELCON SDN BHD (PROJECT)) hasn't been delivered
  yet - current status: New Order. Would you like me to escalate ...". PS202609-0398 and
  PS202609-0410 were dropped without a word.

## Cause (one seam)

`app/services/chatbot/lanes/business/answer.py::not_found_error_message`, the
"status-filter-aware" branch (~line 3579): `order_match = jsc.find(all_matches, ...)`
takes the FIRST order match in `compat_uuids` and renders one line. The branch was
written for one specific order; a multi-order ask hits it with N resolved orders.

## Fix

Same function, same branch. Collect every match in `all_matches` whose uuid is in
`compat_uuids` and whose `entity_type` is in `_ORDER_TYPES`, deduped by uuid
(`all_matches` is built from `intersection` + `by_entity_type` + `resolutions`, so a
uuid appears up to three times), in `gate.compatible_entities` order (that is the
order the customer typed). Render one line per order with the EXISTING wording,
unchanged per line, joined by `\n`, then the one escalate question:

```
Order PS202609-0374 (MATRIX EXCELCON SDN BHD (PROJECT)) hasn't been delivered yet - current status: New Order.
Order PS202609-0398 (MATRIX EXCELCON SDN BHD (PROJECT)) hasn't been delivered yet - current status: New Order.
Order PS202609-0410 (COMMERCE HOUSE SDN BHD (PROJECT)) hasn't been delivered yet - current status: New Order.
Would you like me to escalate to Sorento customer service team?
```

Both arms (`delivered` and `outstanding`) get the loop. One order = byte-identical
output to today (existing test `test_s6c_answer_lane.py` "hasn't been delivered yet"
stays green unchanged). No new string, no helper table, no parser change.

Not in scope: whether the parser should read "STATUS DELIVERY" as a delivered FILTER
rather than a plain status ask. Today's path already tells the customer the status per
order, so that is a wording ruling for the owner, not this fix.

## Tests (pytest, `tests/chatbot/test_s6c_answer_lane.py`, `not_found_error_message` direct)

1. Three resolved orders, `order_status: delivered`, none delivered: three lines in
   compatible_entities order, each with code + customer + "current status: X", one
   escalate question at the end.
2. Same with `order_status: outstanding`: three "has no outstanding items" lines.
3. Duplicate matches (same uuid in `intersection`, `by_entity_type` and `resolutions`)
   still render once per order.
4. One resolved order: output unchanged from before the fix (existing test).
5. `tests/chatbot/test_turn_replay.py` stays green.

## Fix 2: the CS member picker sends no quick replies

Owner ruling 23 Sep 2026: the numbered text list ("Please choose who to route to
(reply with the number): 1. Maryam Ariffin ...") is enough - the same names must not
ALSO go out as WhatsApp quick-reply buttons (a wall of up to a dozen taps). Scope:
`member_offer` ONLY - `team_pick`, `company_pick`, every roster kind keep their quick
replies exactly as today. `result_set` stays populated (a numbered reply still resolves
through it); only `quick_replies` is withheld.

Two seams, one shared predicate:

- `app/services/chatbot/turn/pending.py::quick_replies_suppressed(kind)` - `kind ==
  "member_offer"` directly (one kind does not need a table), next to
  `ESCALATION_OFFER_KINDS`.
- `app/services/chatbot/engine.py::_quick_replies_of(answer)` (the miss arm's own
  `answer.question` -> reply seam, `_run_answer`'s caller) - returns `None` when the
  question's kind is suppressed, before joining option labels.
- `app/services/chatbot/turn/compose.py::compose_question(pending, ...)` (a PENDING
  RE-ASK - the same open question printed again on a later turn) - same predicate,
  same result.

Checked and NOT touched: `tail/reply_ladder.compose_reply`'s own `quick_reply` never
carried member names in the first place (only `access-level-choice-message` and
`build-suggest-offer`'s `suggest_quick_reply` feed it) - the member names only ever
reached `quick_replies` through `answer.question.options` at the two seams above.
`tail/compose.py::crossdomain_compose`'s own `quick_reply` edit is the unrelated
"Yes escalate / No it's okay" cross-domain buttons. `lanes/escalation.py::
_clarify_actions` is the team clarify's own quick replies, a different kind.

Tests: `tests/chatbot/test_rearch_r4_miss_engine.py::TestAC1866MemberOfferSendsNoQuickReplies`
(engine-level: the real `answer_bridge.answer_for` mints the `member_offer` Pending
with a stubbed CS roster, `engine._quick_replies_of` - the exact function `_run_answer`
calls - is asserted `None` while `result_set` still lists the members) and
`TestAC1867OtherPendingKindsKeepTheirQuickReplies` (regression guard: `team_pick` /
`product_pick` still yield the comma-joined string, at both the `_quick_replies_of` and
`compose_question` re-ask seams).

## Fix 3: the console's harness state actually replaces the stored memory

Owner-measured on console contact 900000008, turns at 20:41:40 and 20:41:51 +09, in
`chatbot.turns`.

### Cause

The console sends the state it carries as `previous_conversation_state` (dry run,
D14). `engine.py::_inject_harness_session` wrote that value to
`session_vars["variables"]` ON TOP of the contact's stored row, without touching the
stored row's own top-level keys. `session_state.py::five_keys` returns the STORED
top-level five keys whenever ANY of `session_state.FIVE_KEYS` is present on
`session_vars`, and only falls through to the `variables` nest when NONE is - so for
any contact whose stored row is already in the new five-key shape (every contact
since #952, the 22 Sep 2026 rearch), the console's carried state was silently ignored
and the engine ran on the contact's REAL prod memory: a `customer_pick` the console
showed one turn earlier was invisible, "2" resolved against the stored
`outstanding_detail` question, and the stored focus customers leaked into the answer.
Injection only ever worked for a contact whose stored row was legacy-shaped or empty.
Live turns are unaffected (`engine.py` gates the injection on `dry_run`).

### Fix (one seam, `_inject_harness_session`)

When `previous_conversation_state` is present:

- Value is a dict carrying any of `session_state.FIVE_KEYS` (the console's own echo
  of `result.session_vars`): set ALL five keys on `session_vars` from it
  (`value.get(key)` for each, so a missing key is `None`), and drop the stored
  `variables` nest. Missing keys become `None` on purpose: the harness state replaces
  the memory for this turn, it does not merge with it.
- Value is `{}`: "remembers nothing" (the membership rule `_harness_keys_present`
  already documents): all five keys `None`, `variables` dropped.
- Value is a dict with none of the five keys but not empty (the legacy flat shape,
  `contracts.LegacyVariables`): keep today's behaviour - write it to `variables` -
  and REMOVE the five keys from `session_vars` so `five_keys` falls through to its
  legacy projection instead of reading the (now absent) stored top-level keys.

`session_state.FIVE_KEYS` is imported, not duplicated. `referenced_result_set`
handling is unchanged. The `received` trace's `remembered_keys` fact
(`session_state.five_keys(session_block)`, read AFTER injection) is already honest
once `five_keys` reads correctly - no separate change needed there.

### Tests (`tests/chatbot/test_harness_injections.py`, next to `TestHarnessInjectionsG8`)

Unit-level on `_inject_harness_session` + `turn_runtime.load_state`, the same two
functions a real turn calls in sequence. `TestFix3HarnessFiveKeyStateReplacesTheStoredFiveKeys`:
AC-1868 (a stored new-shape row with an `outstanding_detail` question and a stored
customer; a harness value naming a DIFFERENT customer and a `customer_pick` question
- RED before the fix, confirmed by temporarily reverting the fix and rerunning),
AC-1869 (harness `{}` erases both), AC-1870 (harness in the legacy flat shape still
projects through `variables`, and the stored top-level keys are stripped so they do
not leak). All three confirmed red before the fix, green after (verified by a
temporary revert + rerun + restore, no git operations).

### Found during verification, ruled on, and fixed in the runner: 23 pre-existing `test_turn_replay.py` cases would have regressed, all under `replay_turns/console/`

Found during verification and taken to the owner for a ruling rather than silently
signed, re-recorded, or excluded by the coder - the ruling below resolved it with a
fix in the runner, not a fixture or engine change. Every `console/*.json`
replay case's OWN captured `envelope.previous_conversation_state` (67 of 136 files
carry a five-key-shaped one at some step) is written in a wire shape that predates
several CURRENT conventions: `focus.customer` (singular, wrapped
`{set_at, set_at_turn, source, value}`) instead of `focus.customers` (plural, flat
list, `turn/state.py::focus_to_wire`'s shape), `focus.order_status` instead of
`document` + `status`, and `open_question.options[].idx` / a top-level-less `team`
(nested under `payload.team` instead) instead of `options[].position` / a top-level
`team` (`turn/pending.py::to_wire`'s shape). `focus_from_wire`'s own docstring names
two of these as compat shims for "the first cut of the wire shape" and "the
pre-rearch wire shape" - these captures are from before those conventions
stabilised, most likely genuine pre-rearch production console conversations
committed as regression fixtures in the same commit as the rearch itself
(`0a335146e`, 22 Sep 2026).

Before this fix, `_inject_harness_session` was a no-op for any of these steps (the
bug this whole fix closes), so replay silently fell back to the freshly-computed
session from the PRIOR replayed step, which happens to still answer correctly. With
the fix, the captured OLD-shaped `previous_conversation_state` now genuinely reaches
`five_keys`/`focus_from_wire`, which cannot read several of its fields, and 23 of the
67 affected files diverge on `branch_kind` (confirmed: reverting only this fix's
`engine.py` change restores all 23 to green, both ways verified by hand). This is a
real, measured conflict between a correct fix and stale fixture data, not a defect in
the fix - `tests/chatbot/replay_turns/DIVERGENCES.md` is explicit that a divergence
needs a captain/owner SIGNATURE ("none of these are the tester's to sign"), so this
PLAN records the finding rather than a coder unilaterally signing, re-recording, or
excluding the 23 files.

**Owner ruling, 23 Sep 2026: option (d), none of the three named above.**
`test_turn_replay.py` chains state through the contact's `session_vars` ROW BY
DESIGN - the file's own note ("Chain state carries step to step via `session_patch`,
not the harness key") already says this runner writes step N's `session_patch` onto
the row itself before step N+1 runs, rather than relying on
`previous_conversation_state`. The captured `previous_conversation_state` in
`replay_turns/console/*.json` was therefore never what the engine actually ran on at
record time either - it is stale data the runner should not send at all, and the 77
old-shape captures are exactly that. Fix: `tests/chatbot/test_turn_replay.py::
_build_envelope` strips `previous_conversation_state` from every replayed envelope
before the turn runs, in one place, with a comment naming why; `referenced_result_set`
and `prompt_overrides` are untouched. No change to the JSON fixtures, `DIVERGENCES.md`,
`focus_from_wire`, or `from_wire`. Verified: `pytest tests/chatbot/test_harness_
injections.py tests/chatbot/test_console_turn_endpoint.py tests/chatbot/
test_turn_replay.py tests/chatbot/test_s6c_answer_lane.py tests/chatbot/
test_rearch_r4_miss_engine.py -q` - 693 passed, 133 skipped, 2 xfailed, 0 failed (same
skip count as before Fix 3).
