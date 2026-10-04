# PLAN: INTENT-FRAMES, per-question conversation frames

Status: Build (size L, LEAD pattern; owner approved 4 Oct 2026, rulings Q1-Q5 recorded in
the card). Track: full (touches the parser prompt and the access-change drop).

Card: `intent-frames-behaviour-card.md`. UAC: `intent-frames-4oct-acceptance-criteria.md`.
Base: #1471 (STUCK-QTY-LOOP) and #1470 (LOWSTOCK-SEMANTIC), merged into the lane branch
first; merge those two before this one.

## What changes

1. `turn/frame.py` (new, pure): the axis classes (subject / intent-owned / contact /
   stamps; held questions stay in `turn/held.py`), `on_new_intent` (a clean frame, the
   subject carried only on `continuation` or `anaphora.backward_reference`), `expire`
   (access change only) and `stamp` (`focus.frame_access`, the composer's question
   included).
1b. `turn/held.py::consume`: only a `business_query` that answers no held question starts
   a frame; on that turn the parser's echoed entities (`current_message: false`) are
   dropped; the frame's intent changes only when a frame starts. An escalation offer the
   message does not answer is closed after APPLY (`without_unanswered_offer`).
2. `turn/apply.py::_apply`: `frame.on_new_intent` runs first, before any reader sees the
   old frame. #1471's single-kind `INTENT_OWNED_EXTRA` block in `_focus_rules` is retired
   (generalised by the frame rule).
3. `engine.py`: `frame.expire` at load (replaces `held.expire`), `frame.stamp` at both
   focus writers (replaces `held.stamp`).
4. TTL removal (owner Q1): `held.HELD_TTL_TURNS`, `held.expire`, `held.stamp`,
   `Focus.held_turn` / `held_access` (still accepted by `contracts.Focus` for rows the
   #1471 release wrote), `pending.OFFER_TTL` / `pending.tick` and its load call, the
   dead `"ttl": 3` on the did-you-mean dym marker.
5. `answer_bridge._miss_triggers`: one rule, `_lane_owns_reply` (the lane's own required
   question, or its declared reply in `LANE_OWN_REPLY_KEYS`), replacing the per-lane
   exemptions. `has_result` is deliberately not one.
6. Parser prompt: `INTENT_FRAMES_ADDENDUM` (defines `continuation`, adds clear / reset /
   start over to TOPIC RESET), published unlabelled by migration `if_0001_parser_frames`
   (additive data). Release step: promote the new `chatbot_semantic_parser` version in the
   same release. When #1470's `lss_0001` and this `if_0001` run in one release, `lss_0001`
   already publishes a body that carries this addendum (both build from the live module)
   and `if_0001` finds it and does nothing: one new version, not two.

No schema migration: the focus is JSON; one new optional key (`frame_access`).

## Tests

- `tests/chatbot/test_intent_frames.py`: field-classification contract, new intent clean
  frame, each carry signal, own-axis replacement, same-intent refinement, no-intent and
  unknown-intent turns, reset, access drop (held + open question), no TTL anywhere, wire
  and session contract, the one not-found rule.
- `tests/chatbot/test_intent_frames_engine.py`: replays of the owner's sequences through
  the real engine (availability -> compact stock loop; low stock -> "taiyang only";
  picker -> "1" -> empty -> escalation; incoming -> "4" -> "check stock" -> "7"), plus
  the lane-reads-only-its-own-frame contract at the resolver and tool seams.
- Updated: #1471's held tests (TTL and stamp tests become access/no-TTL tests), the
  #833 carry test and other follow-up fixtures (the live parser's `continuation: true`),
  the prompt-stacking tests and the prompt budget, the top selling sticky pick
  clock test.

## Process

Behaviour card ask before code (done). Reviewer, security-reviewer (the access-change drop
and the prompt), cloud browser pass with the LIVE parser, crew-note head + PASS/FAIL,
hand-test script.
