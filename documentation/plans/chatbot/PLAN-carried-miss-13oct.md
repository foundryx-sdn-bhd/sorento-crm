# PLAN - a carried unresolved token never overrides a successful answer (CARRIED-MISS)

Status: built, in review on PR #1614 (small fix track: backend chatbot only, no migration, no
auth change).

## Prod bug (13 Oct 07:33 MYT, prompt v52, prod main 958a57aae)

Contact "Sorento ACT" sent "I want to make complaint report". The parser gave `portal_link` /
`get_portal_link` with `entities: []`. `crm_portal_link_get` ran and returned the link
(`has_result` true), but the reply sent was:

> Couldn't find: "2024/02-1506" (order). Would you like me to escalate to customer service team?

`2024/02-1506` was a CARRIED focus entity (`focus.extra.order`, `current_message` false) from an
earlier turn, not in this message.

## Cause

`turn_runtime.with_carried_entities` hands every focus kind to the resolver on a turn that names
nothing. The resolver cannot place the order number, and `resolve_gate.if3_miss` clause 2
("unresolved > 0 and no compatible entities") called that a miss, although `portal_link`'s gate
had passed the empty ask (`ALLOWS_EMPTY`). The exit was `not_found`, so the answer bridge composed
"Couldn't find ... + escalation offer" over the fetched link.

## Rule (as shipped)

An unresolved token the current message never typed (`current_message` false), of a kind the
domain never looks up (not in its `ALLOWED` row, `gate_debug.allowed_lookup`), is not a miss. The
gate decides the ask without it and the answer is sent.

Deliberately narrower than "a carried token is never a miss": a carried token of a kind the
domain DOES look up (a product under `incoming`, an order number under `purchase_order`) is the
subject of the ask, so its miss stands. Answering a broad list there would answer a question
nobody asked. A reference hint (`order`) counts by the kinds it narrows to (`REFERENCE_NARROW`).

## Fix

`resolve_gate._carried_and_unusable` + `_kind_is_used`, read by `if3_miss` clause 2
(`sorento_crm_backend/app/services/chatbot/lanes/business/resolve_gate.py`). No domain list. No
change to `answer_bridge._miss_triggers`: its resolver-exit trigger reads `_exit_kind`, which is
now `continue`, and `unplaced_tokens` already drops carried non-product words. The existing
carried-only excuses in `engine.py` (product-scoped fetch, chat_analytics) are untouched.

## UAC

- U1: carried `extra.order` unresolved + `portal_link` turn: the reply is the portal link, no
  "Couldn't find", no escalation offer (engine replay).
- U2: a token the CURRENT message typed that does not resolve is still a miss.
- U3: a carried token of a kind the domain looks up and that does not resolve is still a miss; a
  settled carried product ("eta?" after a product) still answers it (`test_stale_extra_carry.py`).
- U4: forms / chat_analytics with a stale carried order number: no miss.

Tests: `sorento_crm_backend/tests/chatbot/test_carried_miss.py`.
