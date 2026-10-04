# UAC: INTENT-FRAMES

Each criterion names the test that holds it.

- **AC-1** A message of a new known intent never carries the old ask's own filters (sales
  agent, status, document, channel, date window, sort). `test_a_new_intent_drops_the_old_asks_filters_and_subject`.
- **AC-2** A new intent carries the old subject (products, customers, warehouse,
  product-describing extras) only when the parser says so: `continuation` or
  `anaphora.backward_reference` (never `entity_op: reuse` alone), and an echoed old
  entity never lands. `test_entity_op_reuse_alone_is_not_a_carry_signal`,
  `test_an_echoed_old_entity_never_lands_on_a_new_intents_frame`. `test_a_carry_signal_carries_the_subject_but_never_the_old_filters`.
- **AC-3** A new intent with no carry signal starts with no subject but its own.
  `test_a_new_intent_with_no_carry_signal_and_no_entity_starts_empty`.
- **AC-4** A refinement of the same intent narrows the frame (combine across kinds).
  `test_a_refinement_of_the_same_intent_narrows_the_frame`; low stock "taiyang only" in
  the engine replay.
- **AC-5** Stale entities never reach the resolver or a tool call on another intent's
  turn. Engine replay contract tests.
- **AC-6** No miss trigger replaces a lane's own question or its declared reply (one
  rule; `has_result` alone is not a reply). `test_no_miss_trigger_replaces_a_lanes_declared_reply`,
  `test_a_lanes_own_question_outranks_every_miss_trigger`,
  `test_a_resolver_not_found_still_speaks_when_the_lane_declared_nothing`.
- **AC-7** Nothing expires by time or turn count; an access change drops the whole frame
  (held questions and the open question included); a reset drops it (tier, brands stay).
  `test_a_frame_never_expires_by_turn_count`, `test_no_ttl_constant_survives`,
  `test_the_access_drop_takes_the_held_questions_and_the_open_question`.
- **AC-8** A pick-only roster survives a new intent and still picks ("incoming SRTWC286"
  -> "4" -> "check stock" -> "7"). Engine replay, `test_picker_domain_judgement_1352.py`.
- **AC-9** Picker -> "1" -> empty fetch ends with the escalation offer for an
  escalation-allowed contact. Engine replay.
- **AC-10** Reply format stays one fact per line `*Label:* value`, no " · ". Unchanged
  presenters; engine replays assert no " · ".
- **AC-11** "clear" / "reset" / "start over" are taught as `topic_reset` in a new
  unlabelled parser prompt version. `if_0001_parser_frames`, live-parser pass.
- **AC-12** An escalation offer the next message does not answer is closed, so a later
  "ok thanks" never hands the chat to a person (security review B1).
  `test_an_escalation_offer_the_next_message_ignores_is_closed`, live
  `test_live_ignored_offer_is_not_accepted_later`.
