# UAC: AGENT-LOOP-SPIKE

Plan: `PLAN-agent-loop-spike-11oct.md`. Tests: `sorento_crm_backend/tests/chatbot/agent/`.
All tests use a scripted fake provider and a fake MCP call; no live LLM or MCP calls in pytest.

## Switch

- AC-1 Setting off (default): a dry-run turn runs today's stages; the agent is never called.
- AC-2 Setting on, LIVE turn: today's stages run; the agent is never called.
- AC-3 Setting on, dry-run turn: the agent answers; `branch_kind == "agent"`; `is_test` true; every
  action `dry_run` true.
- AC-4 A contact not allowed AI answers stays silent with the setting on (today's gate, agent not called).

## Loop

- AC-5 The model calls a tool, then answers `"Here you go\n{{block:1}}"`: the reply holds the
  framing text and block 1 verbatim.
- AC-6 The model's prompt holds a short block summary, never the full block body.
- AC-7 Unknown block ref (`{{block:9}}`): ref removed, unused blocks appended; reply never shows `{{`.
- AC-8 Empty final text after tool calls: reply = the blocks in order.
- AC-9 The model keeps asking for tools: after 4 rounds a final no-tools call answers; never more
  than 4 + 1 model calls.
- AC-10 Provider rate limit or timeout: reply is `RATE_LIMITED_REPLY`; row closed; no exception out.
- AC-11 A tool raises: the model gets an error result; if the turn still fails, the customer gets a
  plain reply (never empty, never a stack trace); the trace records the tool error.
- AC-12 A tool name not on the read-only allow-list is refused (never reaches MCP).

## Scope (exactly as today)

- AC-13 Customer-scoped (dealer) contact: tool schemas shown to the model carry no `customer_ids` /
  `customer_query` on customer-scoped tools.
- AC-14 Dealer: a call that names another customer's id is refused (`ScopeViolation` rule) and the
  MCP call is not made; a call naming none gets the dealer's linked ids injected.
- AC-15 Staff contact: may pass `customer_ids`; they reach the MCP call unchanged.
- AC-16 Every MCP call carries the contact's `contact_id` and `space_id`, whatever the model sent.
- AC-17 Stock not allowed for the contact: stock tools refused with a relayable result.
- AC-18 A domain whose reveal key the contact lacks (e.g. purchase cost for a dealer): its tools are
  refused with a relayable result.
- AC-19 `so_lookup` passes the contact's linked customers (dealer sees own book only).

## Memory and writes

- AC-20 `session_patch.agent` holds the last messages, records found and options offered.
- AC-21 Next turn with `previous_conversation_state` = that patch: the model's prompt holds the
  prior user message, bot reply and records.
- AC-22 Test turns write nothing except the `chatbot.turns` row: no portal token row, no file link
  row, no usage log, no session/conversation write.
- AC-23 `portal_link` in a test turn returns a link-shaped block and does not call the token route.

## Trace and style

- AC-24 `understood` facts carry summed prompt/cached/completion tokens across rounds; `looked_up`
  raw envelopes carry `tool` and `error`; `scripts/chatbot_eval/trace.parse_trace` shape reads them.
- AC-25 Rendered blocks put one fact per line and never join with ` · `.
