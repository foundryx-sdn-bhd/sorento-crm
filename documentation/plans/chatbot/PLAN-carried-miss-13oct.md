# PLAN - a carried unresolved token never overrides a successful answer (CARRIED-MISS)

Status: in progress (small fix track: backend chatbot only, no migration, no auth change).

## Prod bug (13 Oct 07:33 MYT, prompt v52, prod main 958a57aae)

Contact "Sorento ACT" sent "I want to make complaint report". The parser gave `portal_link` /
`get_portal_link` with `entities: []`. `crm_portal_link_get` ran and returned the link
(`has_result` true), but the reply sent was:

> Couldn't find: "2024/02-1506" (order). Would you like me to escalate to customer service team?

`2024/02-1506` was a CARRIED focus entity (`focus.extra.order`, `current_message` false) from an
earlier turn, not in this message.

## Rule

A token the current message did not contain (`current_message` false) can never turn a successful
fetch into a "Couldn't find" reply and never triggers the escalation offer. If the domain answered,
the answer is sent. Never invent; never drop a real answer.

## Fix (general, no domain list)

1. Resolve gate (`if3_miss`): unresolved tokens that are all carried-only do not count as a miss.
2. Answer bridge (`_miss_triggers`): a resolver-exit miss whose unresolved tokens are all
   carried-only does not fire.
3. Existing carried-only excuses (product-scoped fetch, chat_analytics) keep working.

## UAC

- U1: carried `extra.order` unresolved + `portal_link` turn -> reply is the portal link, no
  "Couldn't find", no escalation offer.
- U2: a token in the CURRENT message that does not resolve still produces "Couldn't find" + offer.
- U3: a follow-up that needs the carried token ("eta?" after a product) still resolves it.
- U4: chat_analytics / generic help with a stale carried token answer normally.
