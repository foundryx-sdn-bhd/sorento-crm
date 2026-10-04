# UAC: status-filtered order miss names every resolved order (23 Sep 2026)

Plan: `PLAN-chatbot-order-status-all-orders-23sep.md`. Track: small fix.

- AC-1860 When N orders resolve (`gate.compatible_entities` has N order rows), the
  status-filtered tool returns nothing, and `order_status` is `delivered`, the miss
  reply carries one "Order <code> (<customer>) hasn't been delivered yet - current
  status: <status>." line per order, in `compatible_entities` order, then ONE
  "Would you like me to escalate to <team> team?" question.
- AC-1861 Same for `order_status: outstanding` with the "has no outstanding items"
  line per order.
- AC-1862 An order whose uuid appears in more than one of `intersection`,
  `by_entity_type`, `resolutions` renders once.
- AC-1863 One resolved order produces the same text as before this fix (existing
  test in `tests/chatbot/test_s6c_answer_lane.py` unchanged and green).
- AC-1864 The live turn (contact 900000105, turn 48: PS202609-0374, PS202609-0398,
  PS202609-0410, all `New Order`) satisfies AC-1860 with all three named.
- AC-1865 `tests/chatbot/test_turn_replay.py` stays green with no DIVERGENCES entry.

## Fix 2: the CS member picker sends no quick replies (owner ruling 23 Sep 2026)

- AC-1866 When a `member_offer` pending is the open question (the CS member picker,
  "Please choose who to route to (reply with the number): 1. ... 2. ..."), the reply's
  `quick_replies` is `None` - the numbered text list is the whole offer, not also a row
  of WhatsApp tap buttons. `result_set` stays populated with the member rows
  (`entity_type: "member"`, one per roster row) so a numbered reply still resolves
  against them.
- AC-1867 Every other pending kind (`team_pick`, `company_pick`, `product_pick` and
  every other roster kind) is unaffected: `quick_replies` is still the comma-joined
  option-label string exactly as before this fix.

## Fix 3: the console's harness state actually replaces the stored memory (console defect, contact 900000008, 23 Sep 2026)

- AC-1868 On a dry run, when the harness `previous_conversation_state` carries any of
  `session_state.FIVE_KEYS` (the console's own echo of `result.session_vars`), the
  turn's state is loaded from THAT value, not from the contact's stored row - a missing
  key on the harness value becomes `None` (the harness state REPLACES the memory for
  this turn, it does not merge with the stored one), even when the stored row is
  already in the new five-key shape.
- AC-1869 `previous_conversation_state: {}` erases all five keys for the turn (the
  harness saying "this contact remembers nothing"), the same membership rule
  `_harness_keys_present` already applies to `{}` vs absent.
- AC-1870 A harness value in the legacy flat shape (`contracts.LegacyVariables`, none
  of the five keys, non-empty) keeps today's behaviour: it lands in `session_vars.
  variables`, and `session_state.five_keys` projects it exactly the way it already
  does for a real legacy-shaped stored row - the stored row's own top-level five keys
  do not leak through. Live turns are unaffected either way (`engine.py` gates
  `_inject_harness_session` on `dry_run`).
