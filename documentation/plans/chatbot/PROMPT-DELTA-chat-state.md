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

## Pending text changes from review (16 Oct ~23:30; the coder applies them to `STATE_SECTION`)

- `set` / `clear` axis must be one of: product, customer, warehouse, brand, tier, channel, period, status, sort,
  quantity, or an entity kind listed in the prompt. Replace "filter" with these names.
- Every value this message states needs its own `set` for its axis; a stated value without a `set` is not used.
- `switch_domain`: axis null; the new domain is the `domain_hint` you fill as usual.
- `new_subject`: the customer (see the held-slot rule), the access tier and the brand carry; nothing else.
- `pick`: one item per picked position ("1 and 3" = two picks). Only product rows of the `last list` are pickable.
- `close_question`: only when the open question is still owed (it asked for something) and the message leaves it
  unanswered; not for a question that was already answered.
- When the State block is absent or every line is none: output `[]` (nothing to change); do not output `new_subject`.
- Period (owner 16 Oct ~23:00): a follow-up keeps the held period (no op needed, or `keep period`); a period this
  message states replaces it (`set period`); for a brand-new topic with no period, judge it: `skip period` or
  `clear period` when the held period does not belong to the new question.
- Held customer: a delivery or "when will it arrive" ask with a held customer is delivery to that customer
  (`keep customer`, delivery/order domain); a clearly ETA / incoming stock ask is product-level (`skip customer`).
  Say `unclear customer` only when you cannot tell; the system then settles it from the customer record.
