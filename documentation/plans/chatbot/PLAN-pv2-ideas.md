# PLAN PV2-IDEAS (13 Oct night)

Status: in progress (size L lane per crew brief; contract scope S/M)

Contract: `documentation/plans/chatbot/CONTRACT-PV2-13oct.md` section 5 "Ideation" and section 9 lane PV2-IDEAS-dialogue.

Owns `sorento_crm_backend/app/services/chatbot/pv2/ideas.py`; additive kwargs only in
`ideation_capture_service.py` and `ideation_capture_replies.py`. Tests `tests/chatbot/pv2/ideas_*`.

1. (a) similar-ideas list becomes PendingQuestion(kind="idea_similar", options 1..N + new); a pick arrives as
   `answers_bot_question` + `option_key` and reaches `handle_capture_turn` as `choice_in`.
2. (b) reply to the missing-field question (kind="idea_missing_field") updates the held draft (`update_held=True`).
3. (c) `ideation.idea_count >= 2` -> PendingQuestion(kind="idea_multi", "N ideas: save both?").
4. Missing optional fields are listed in the reply, not asked.
5. Without the new kwargs, v1 behaviour is byte-identical (regression tests on the existing ideation suites).
