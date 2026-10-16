# CHAT-STATE parser prompt delta (S5)

One self-contained section APPENDED to the base parser prompt (`chatbot_semantic_parser`, the version the
`production` label points at on the prod copy). It edits nothing above it, so it applies to any base version. The
literal `state_changes` in it is what turns the state path on (`resolve_config`), so the prod label never opts in.

Publish: an UNLABELLED version on the dev copy only (`ai_prompt_service.LABELS` allows only production/staging,
`app/services/ai_prompt_service.py:23`); runs pin it per turn through the dry-run `prompt_overrides` envelope key
(`engine.py:1308`, EVAL-BENCH `scripts/chatbot_eval/engine.py:76-77`). The `production` label is never moved by this
lane. The single source of the prompt text is the repo constant `STATE_SECTION` in
`sorento_crm_backend/app/services/chatbot/head/chat_state_prompt.py`; `build_lane_prompt` appends it and
`scripts/publish_chat_state_prompt.py` publishes it.

Rules taught are business rules (what carries), not language: no word lists, no example phrases from any eval set.


## Pending text change (S4b/S6, owner 16 Oct ~22:45)

Add to the op table: `keep` / `skip` / `clear` / `unclear` with axis `customer` or `period`. Rule: when the State holds
a customer or a period, say for each one whether it applies to THIS message (keep), does not apply (skip, it stays
held), is dropped (clear), or you cannot tell (unclear). An ETA ask is about incoming stock (meaning, not a word).
