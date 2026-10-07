# UAC: STALE-EXTRA-CARRY

Plan: `PLAN-stale-extra-carry-07oct.md` (alongside).

AC-SEC-1. A `kind_pick` minted by `turn/apply.py::apply()` at turn N carries
`asked_at_turn == N` (never None). The queued sibling pick asked once the first is
answered carries the turn that asks it.

AC-SEC-2. A `kind_pick` minted at turn N, with a `stock_qty` task opened or touched at
turn N+1, is no longer the current question at turn N+2: a bare number answers the task
(`bare_number_is_the_quantity` fires), under the existing rule in
`_stock_task_is_the_current_question`. No new ageing rule is added.

AC-SEC-3. On a fetch turn (`with_carried_entities(..., unsettled_only=True)`) whose
focus holds a product with a uuid that is not a described set's member, a carried
`category` / `product_type` row with no uuid, or a `specification` row with no uuid and
no `spec_key`, is not handed to the resolver.

AC-SEC-4. The same call still hands over: a class word carried beside a described set's
members (rows marked `described_set`); a grounded specification (`spec_key` set); every
uuid-less row when the focus holds no resolved product; every other kind (brand,
promotion, attachment_type, customer) exactly as today.

AC-SEC-5. The incident shape through the real engine: focus products `[SRT320-CR uuid]`,
`focus.extra.category` and `focus.extra.specification` "seat cover" (no uuid, `spec_key
""`), parser entities `[]`, `entity_op reuse`, domain `incoming`, intent
`check_incoming`. The reply is the product's ETA line; the resolver body
(`resolve_gate.resolve_entity_body`) carries no `scope_terms`; the incoming tool is called
with the product's id alone; the reply names no "seat cover" miss.

AC-SEC-6. `tests/chatbot` stays green in full, the described-set refinement cases
included.

AC-SEC-7. No regex, keyword or word-shape rule on user text: the engine reads parser
fields and focus rows only.
