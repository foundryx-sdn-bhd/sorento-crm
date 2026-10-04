# PLAN - Chatbot Turn Engine: n8n business logic moves into the CRM

Status: S0 + S1 IMPLEMENTED on lane feat/chatbot-turn-engine (4 Sep 2026); approved "ok good to go", 6 review rounds, D1 to D16; re-ported onto the LIVE n8n body 5 Sep (see S1 "pending re-port"); S1b DELIVERED 5 Sep (-40.0% prompt, published unlabelled, promote is the owner's call); S2 (the tail) MERGED 5 Sep; S6a MERGED 5 Sep, behind `system_settings.chatbot_business_lane_enabled` (default off) until the n8n edit in `n8n-changes.md` S6a is made; S4 (the `low_signal` clarifier lane) and S5 (the escalation lane) DELIVERED 5 Sep, both inert until the owner adds their `branch_kind` to `system_settings.chatbot_completed_lanes`; S6c + S7 MERGED 5 Sep (#674); **S8a DELIVERED 5 Sep** (AC-803, AC-804, AC-806, AC-807, AC-808) - chatbot config now lives on the respond workspace row and not in `.env`, and the hazard table below is closed out. **AC-809 + AC-810 DELIVERED 5 Sep** - System > Settings > Chatbot now owns the lane list, the stock-denial switch, the unsupported-domain list and the two switches that used to be `CHATBOT_BUSINESS_LANE_ENABLED` / `CHATBOT_ORDERING_ENABLED` (both are `system_settings` columns read per turn, migration `480_chatbot_switches`); `CHATBOT_TURN_ON_WORKER` stays a deployment property. S8b is still open and gated on the owner's S7 promote (AC-801, AC-802, AC-805)
UAC: `documentation/plans/chatbot/chatbot-turn-engine-acceptance-criteria.md`
Classification: **MODULE** (`chatbot`), own Postgres schema `chatbot` (D12)
Owner decisions: D1 to D13 in the UAC; rulings R1 to R6 in the UAC
Source maps (scratch, regenerate if lost): n8n spine map + CRM inventory produced 4 Sep 2026
from `sorento_crm_n8n/n8n-workflows-init/export/*` and `sorento_crm_backend/app/*`

## Why

Every chatbot change today is an n8n change: 18 workflows on the turn path, 305 nodes, about
12,000 lines of JavaScript held inside JSON strings across ~120 Code nodes, 165 by-name
`$('node')` reads that no rewiring redirects, and a scheduler whose fan-out order is not stable
(measured 37 vs 612 on the same edge). Nothing greps, nothing type-checks, and the only
regression net is a custom replay harness in the n8n repo. The owner has no confidence in n8n
as a business-logic layer and every change takes a long planning session.

The CRM already owns most of the ingredients: the session store, the ideation turn (the one lane
already built this way), a three-provider LLM client with structured output, a versioned prompt
registry with an admin UI, the entity resolver, the MCP catalog and an in-process MCP client,
the access, team, assignee and SLA services. What n8n adds on top is the glue and the state
compiler. That glue is what moves.

## What exists (measured)

### n8n turn path (live spine `S4N1LiisAqA4hpMC`, export `spine-rs-1a`, 2 Sep 2026)

| stage | n8n today | lines JS |
|---|---|---|
| ingress + dispatch | `sorento-main` webhook > redis `q:{contact}` + `ready-contacts`; `sorento-dispatcher` 1 Hz tick, per-contact `INCR` lock TTL 120 s, calls the spine | small |
| media intake | `sub-media-intake` > `POST /external/media/process` (CRM), patches transcript into the envelope | 450 |
| head | `get-session-vars` (CRM GET) > `sub-semantic-parser` (gpt-5.4-mini, 48 KB prompt, then `output_exchange.js`) > `check-access` (CRM POST) > `build-ctx` > `route-turn` (13 predicates) | 2,300 |
| lanes | canned x6, `ideate` (CRM POST), `offer_hold`, `low_signal` (gpt-4.1-mini), `out_of_scope` (`sub-escalation` + `sub-human-intervention`), `business_query` / `check_promotion` / `stock_denied` (`sub-main-processing` + 6 subs, up to 5 MCP calls, deterministic answer text) | 7,000 |
| tail | `sub-output`: `build-outcome` > `compile-current-state` (1,948) > `crossdomain-compose` > `PUT conversation-variables` (wholesale) + `sub-sendmsg` + `send-attachments` | 2,400 |

Two LLM calls per turn at most (parser; small-talk clarifier on `low_signal` only). The answer
text on the happy path is deterministic (`output-structurer.js`, 482 lines). The `AI Agent`
nodes in `sub-get-results` are orphaned.

Contracts that carry everything: `ctx` (6 keys), `qf` (27 LLM keys + 69 derived),
`session_vars.variables` (~35 keys, 9 write-only, 1 read-only), `branch_kind` (13),
`_exit_kind` (4), `_fetch_arm` (3), the `sub-output` input (13 nullable values),
`reply = {text, quick_replies, session_patch}`.

External surface n8n touches on the turn path: 14 CRM REST endpoints, 1 MCP endpoint (14 tool
names), 1 direct pgvector SQL on the production DB, 1 OpenAI embeddings call, 2 OpenAI chat
calls, respond.io send / assign / update-contact, 8 redis key families.

Replay corpus in the n8n repo: `tests/fixtures/nodes/<slug>/<node>/*.json`, 1,535 files
(111 MB), 602 real captures across 181 executions, each `{ctx, input, expected, ran}` and
PII-scrubbed; 11 full-execution `worlds`; 5 `regression-guards`; 5 named canaries. Coverage by
`branch_kind` is dense for `not_found`, `out_of_scope`, `access_choice`, `check_promotion`,
`business_query`, `not_supported`, thin for `low_signal` (2) and `ideate` (4).

### CRM (main at `ca9babee3`, 4 Sep 2026)

| capability | where |
|---|---|
| external auth | `X-API-Key` > `get_external_api_user` > integration principal (DB-backed) > RBAC slug via `require_external_permission`; map in `app/api/v1/external/permissions.py` |
| session store | `respond_contacts.session_vars` JSONB; `conversation_variables_service.get_for_contact` / `overwrite_for_contact` (`FOR UPDATE`, wholesale) |
| the pattern | `app/services/ideation_turn_service.handle_turn` + `app/api/v1/external/ideation.py`: fail-closed config, LLM in a never-raising extractor, integration_log on every call |
| LLM | `llm_provider.get_provider(...).chat(messages, json_schema=...)`, OpenAI / Anthropic / Gemini |
| prompts | `ai_prompt_registry` (17 keys, versioned, 60 s cache, DB-down fallback, admin UI) |
| MCP | `sorento_crm_mcp/catalog.py` (40 tools; ideation NOT yet a tool, added in S3 per D6) + `ai_assistant_service.MCPRuntimeClient` (HTTP JSON-RPC client for the streamable-HTTP MCP endpoint) |
| tool search | `EmbeddingReadService` behind `POST /external/rag/tool-search` |
| resolver | service behind `POST /system/references/resolve` (`app/api/v1/system/references.py:2133`), `entity_pins` supported |
| access / teams / assignee / SLA | `mcp_access_service.evaluate_agent`, `ContactAccessTypeService`, `company_routing_service` (team members), next-assignee service (`next_assignee.py`, commits a round-robin cursor), `ConversationSLATrackingService` |
| worker | RQ, 7 queues in `worker.py:DEFAULT_QUEUES`, worker_fast latency split (#569) |
| modules | `app/modules/runtime/module_manifest.py` + `bootstrap.py` per module + `require_module_enabled_with_api_key` |
| settings | `system_settings` singleton, two manual GET dict builders in `user_management/settings.py` |

## Target architecture

### Package (module-private, one public entry point)

```
app/services/chatbot/
  __init__.py            # exports run_turn, complete_turn ONLY
  contracts.py           # Pydantic: Envelope, Ctx, ParseOutput (qf), SessionVars (extra=forbid),
                         #   BranchKind, Reply, Action, TurnResult; every enum as ONE Literal
  engine.py              # run_turn(envelope, *, session_factory) -> TurnResult ;
                         #   complete_turn(turn_id, fragments, *, session_factory)
                         #   A session FACTORY, not a session: the capacity rule below
                         #   forbids holding one across the LLM call, which a
                         #   request-scoped Depends(get_db) session cannot satisfy.
  head/parser.py         # LLM call (prompt key chatbot_semantic_parser), strict schema
  head/output_exchange.py# port of output_exchange.js + suggest-follow-up.js
  head/access.py         # contact-to-agent access check (service behind /external/access-agent/check; not MCP)
  head/route.py          # port of route-turn.js -> BranchKind + item stamp
  lanes/canned.py        # escalate-catalog copy, tag-only arms, offer_hold
  lanes/ideate.py        # calls MCP tool crm_ideation_turn (D6), folds session_vars.ideation
  lanes/casual.py        # low_signal: resolve for prompt + clarifier LLM + central-exchange
  lanes/escalation.py    # escalation-input/context/gates + assignment actions
  lanes/business/resolve_gate.py   # get-access-types, resolve-entity, tier-gate,
                                   #   disallowed-entity-gate, pickers, resolve-exit-*
  lanes/business/fetch.py          # tool search, tool-filter, tier-probe, entity-ids-transformer,
                                   #   MCP call, output-structurer, fetch-result
  lanes/business/answer.py         # validator, promo-picker, crossdomain-*, build-result,
                                   #   sub-answer, sub-miss-suggest, build-suggest-offer
  tail/outcome.py        # build-outcome (15-key map)
  tail/compile_state.py  # compile-current-state -> SessionVars
  tail/compose.py        # crossdomain-compose + reply seal
  copy.py                # registry keys for canned replies (fallback = today's text)
  delegate.py            # migration-only: the {delegate, ctx, item} envelope for n8n lanes
  dispatch.py            # S7: per-contact FIFO ticket in redis, inside the request
app/api/v1/external/chat.py      # POST /chat/turn, POST /chat/turn/{id}/complete
app/tasks/chat_turns.py          # RQ: run_turn_job, optional offload behind CHATBOT_TURN_ON_WORKER (S7)
app/models/chatbot_turn.py       # chatbot_turns inbox
app/modules/chatbot/bootstrap.py # MODULE_KEY = "chatbot"
tests/chatbot/                   # replay + unit + endpoint + inbox + boundary
tests/fixtures/chatbot/          # vendored golden subset (< 3 MB)
```

Boundary rule (guardrail test): nothing outside the module's own ROUTERS
(`app/api/v1/external/chat.py`, the n8n-facing turn endpoint, and
`app/api/v1/system/chatbot.py`, the admin turn-trace endpoint added at S2b),
`app/tasks/chat_turns.py`, `app/modules/chatbot/` or `tests/chatbot/` imports
`app.services.chatbot`. The package imports core services freely. That is the whole
"liftable later" story: the day a measured trigger fires, the package moves behind an HTTP
boundary with the same contracts. **Lift trigger (named, not built):** p95 turn latency above
`chat_latency_p99_target_seconds` for a week, or API-box CPU above 70 percent sustained with
the chatbot queue as the top consumer. Until then it is a modular monolith.

### Transport contract with n8n (D4, D9, D14)

Synchronous at every stage of the migration and at the end state. The caller sends.

```
POST /api/v1/external/chat/turn            body { envelope: A }
  -> 200 { turn_id, ctx, item, branch_kind, delegate: "<lane>" | null,
           reply?: {text, quick_replies, result_set, attachments_src},
           actions?: [...], session_patch?: {...} }      # session_patch only on dry run
POST /api/v1/external/chat/turn/{id}/complete   body = sub-output input contract (S2 to S6)
POST /api/v1/external/chat/turn/complete        same body; the turn is identified from
                                                (ctx.contact.id, ctx.text.message.messageId)
  -> 200 { turn_id, reply, actions, is_test, session_patch? }
```

`delegate` names the n8n lane that must still run during migration (`business_query`,
`check_promotion`, `stock_denied`, `out_of_scope`, `low_signal`); n8n's `route` Switch
shrinks by one output per migrated lane. `delegate = null` means the CRM finished the turn:
the caller sends `reply` and executes `actions`. From S7, with Ordering
(`system_settings.chatbot_ordering_enabled`) on, the CRM owns the tail: `/turn` returns the finished reply and `/complete` answers 410
Gone. The `/complete` ROUTE and `delegate.py` are deleted at S8, not S7, because n8n's S2
tail keeps calling `/complete` until the S7 promote lands on the n8n side and deleting the
route before that would strand every turn a lane still completes.

`actions[]` vocabulary (the caller executes, in order): `send_message {text, quick_replies,
result_set}`, `send_attachments {attachments_src, reply}`, `assign_conversation
{respond_user_id}`, `add_comment {text, mention_user_ids}`, `update_contact_fields {fields}`.
Every action carries `dry_run` (true on test envelopes, D14) so the clone's `test-guard`
records instead of sends, exactly as today. **D14 also means no shared-resource contention
(the redis ordering keys) and no operator-visible rows** - a dry run writes nothing outside
`chatbot.turns`, takes no place in the queue a live message from that contact waits on, and
does not appear on the failed-contacts list, in Chat History or behind the Retry button
(H57). **A dry run returns every action it would have
taken, flagged `dry_run`, with preview placeholders where a side effect would have supplied
the value** (AC-507): the lane still reaches no seam, so where a real run would have read an
id off `next-assignee` the preview carries `null` plus `preview: true`, and where it would
have read a timestamp off `sla_create` the rendered text carries `<preview>`. Anything not
behind a seam - a fixed sentence, or one interpolating state the turn already resolved -
carries its real value on both. The shape, the order and the key set are therefore identical
live and dry, which is what lets the executor render ONE set of expressions against both.

**Implemented verbatim at S3** (5 Sep 2026, field shapes agreed with the n8n executor), so
this line needs no amendment - but two properties of it are worth stating because an
implementation could satisfy the field NAMES and still break the sender:

- `quick_replies` is the SEALED `compile-current-state` value, unchanged: n8n's own
  comma-joined string, or null when the turn offered none. `sub-sendmsg`'s `quick_reply`
  input has never been given a list, and the sender is the half that did NOT move into the
  CRM, so an action that normalised the type would break it. `result_set` is likewise
  `variables.last_result_set` as sealed.
- `send_attachments` is emitted ONLY when `reply.attachments_src` is non-null, and it comes
  AFTER `send_message` for the reason n8n wires it that way: the text explains the files.
  It carries the whole `reply` object because `sub-send-attachments` reads more than one
  field off it.

**The executor executes `actions[]` and NOTHING else (ruling, 5 Sep 2026, help-crm).**
n8n never sends `reply.text`. `reply` is the record of what was composed (and what the
trace screen and the duplicate-delivery replay read); `actions` is the instruction list,
so a lane whose words are only on `reply.text` is a customer left in silence. Every lane
that finishes in the CRM therefore puts its customer copy in an action:

- the eight S3 kinds and `low_signal` all end at `_send_actions`, or (for `low_signal`)
  at the one `send_message` the casual lane stamps before its tail runs;
- a failed HEAD returns the parser's error reply as a `send_message` (AC-105);
- a failed TAIL returns the same error reply as a `send_message` with the row's `dry_run`,
  rather than a null `reply` and an empty list (5 Sep);
- `/complete` also carries `is_test` (the row's), so the caller's test-guard reads one
  field instead of remembering what `/turn` said two calls ago.

One measured shape difference, recorded rather than smoothed over: `low_signal`'s action
is stamped BEFORE the tail (it has to be on the row before the tail reads `prior_actions`,
so a duplicate delivery replays the action as well as the reply, D15). It therefore carries
the clarifier's text with `quick_replies: []` and no `result_set` key, where `_send_actions`
would have carried the sealed `quick_reply` and `last_result_set`. Identical text; the
executor reads a missing `result_set` the same way it reads a null.

Measured while implementing: no canned lane can produce quick replies today.
`compile-current-state` sets `quickReply` from `access-level-choice-message` or
`build-suggest-offer` only, and no canned lane supplies either fragment, so every one of
the eight seals a null. That is why the pass-through is what S3's own test asserts on a
lane and the populated shape is asserted as a unit.

**D14's input half (O2, AC-112).** A dry-run envelope may also carry three optional harness
keys, and the engine honours them ONLY on a dry run: `mock_reformulator_output` replaces the
parser call (no provider is asked; the mock goes through the same `post_process` +
`suggest_follow_up` a real emission does, so the harness exercises the code instead of
bypassing it, and a malformed mock is a failed `understood` stage exactly like a malformed
model answer), while `previous_conversation_state` and `referenced_result_set` replace the
stored memory for that turn and are never written back. They are `Envelope` EXTRAS rather
than declared fields on purpose: a harness contract that a live producer should have no
reason to reach for. On a live envelope all three are ignored and listed in the `received`
record's `harness_keys_ignored` (empty list when there are none), so a stray key is visible
rather than silently answering a real customer from a mock. Tests are named after the n8n
guards they replace: `TestHarnessInjectionsG6` (reformulator bypass),
`TestHarnessInjectionsG8` (session injection).

**Why not an outbound webhook (rejected at review round 3):** a fixed CRM-to-n8n URL would
decide egress on the CRM side, so a chat-console or clone turn would push to the live sender.
Returning the data keeps egress with the caller and keeps the harness's containment model
(`plans/spine-decomposition-plan.md` G1 to G10) intact. n8n already waits the whole turn
(`call-spine` `waitForSubWorkflow: true`), so nothing gets slower.

**SUPERSEDED 1 Oct 2026 by CHATBOT-QUEUE-FIX** (`PLAN-chatbot-queue-fix-1oct.md`): per-ticket
heartbeat keys replace `running` and the stall grace, and a queue timeout runs the turn. The
paragraph below is the original S7 design.

**Per-contact ordering moves to the CRM at S7 (round 4, owner: 50 dealers, 100 questions at
once).** The n8n dispatcher pops ONE contact per 1 s tick, so a 50-contact burst is served one
per second regardless of what the CRM does. From S7 the request itself serialises per contact:
on arrival `ticket = INCR chatbot:seq:{contact}` (expire 1 h, stamped with its holder's
liveness in the same script); the request waits (200 ms poll, max
`CHATBOT_QUEUE_WAIT_SECONDS` = 45) until `chatbot:done:{contact} >= ticket - 1`, runs the
turn, and advances `done` monotonically in a `finally` that covers the wait as well as the
stages. Two repairs, for two different deaths, and **both are kept on purpose**: giving up
after the queue budget advances `done` to your own ticket, which is what unblocks the
contact when a process died mid-turn with its `chatbot:running:{contact}` key still set (it
looks alive for the key's whole TTL, so no absence can be seen); and a waiter that sees
`done` stalled with the key ABSENT for `STALL_GRACE_SECONDS` repairs the counter and
proceeds, which is what covers a holder whose key has since lapsed, or was lost, while its
successors are still arriving - there the absence is the only evidence there is, and
waiting out the full budget would fail a turn that could have been answered. Both paths are
pytest-covered. Different contacts never wait on each
other. The dispatcher and its redis lists are retired; n8n's ingress posts each message
directly. Until S7 the dispatcher stays and bounds load at ~1 turn/s. The CRM inbox row stays a
record and a trace, never a queue. Retry from the trace screen re-posts the envelope through
the same ordering; the CRM never sends.

**Switchover must survive S7 (AC-714).** The carve state (`failover_watermark`), the producer's
`in-failover?` gate and the poller stay exactly as they are; the only S7 change on each
injector is "push to redis" becoming "call the thin spine". Both injectors flip in the same
promote (the concurrency plan's own lesson). Clone run: carve, message via poller, un-carve,
message via webhook, duplicate during the switch runs once.

**Webhook fallback (D15).** The failover poller (`CYNq34WZx83POLQ5` > `sorento-main-INJECT`)
and the webhook producer are two injectors of one envelope shape; both call `/chat/turn`. The
engine is idempotent per respond `message_id` (`chatbot.turns` unique on
`(contact_respond_id, message_id)`): a duplicate returns the original reply with
`duplicate: true` and the caller's Switch sends nothing. The poller posts a buffered batch in
respond timestamp order; the per-contact ticket keeps it in order and never interleaves it
with a live message. The carve state (`failover_watermark`) stays in n8n's own tables.

**Worker offload is optional** (`CHATBOT_TURN_ON_WORKER`, default off): enqueue on `chat` and
wait for the result inside the request, the pattern `/external/media` already uses. Built in
S7 behind the flag, switched on only when the lift trigger is measured.

### The `chatbot.turns` inbox and trace (D12, D13)

Schema `chatbot` (migration does `CREATE SCHEMA IF NOT EXISTS chatbot`; model pins
`__table_args__={"schema": "chatbot"}`). One table, `chatbot.turns`: `id uuid,
contact_respond_id text, envelope jsonb, is_test bool, status
(processing|delegated|done|failed), stage text, branch_kind text, error text, attempt int
default 1, message_id text, ingress text, trace jsonb, created_at, started_at, finished_at`, index
`(contact_respond_id, status, created_at)`, UNIQUE `(contact_respond_id, message_id)` (D15), plus `shadow_of text null` (the n8n turn id
when the row is a shadow-mode dry run, gate 4). A row is created by `/turn` and closed by
`/complete` (or by `/turn` itself when `delegate = null`), status `delegated` in between. It
is a record and a trace, never a queue (the dispatcher in n8n is the queue). Retention: 90
days, pruned by the existing scheduler sweep pattern.

`trace` is an ordered array of stage records written by the engine as each stage ends:

```
{ stage: received|understood|access|routed|looked_up|replied|remembered|sent,
  status: ok|failed|skipped, started_at, ms,
  summary: "Understood as a business query about product SRTWC8517, dealer tier",
  why:     "Routed to the business lane: access allowed, no escalation asked",
  facts:   { ...small flat dict for the UI rows... },
  error:   "...one sentence..." | null,
  raw:     { ...technical payload, byte-capped like the AI trace... } }
```

`summary` and `why` are sentences produced by the engine from structured state (D11: never
from the customer's text), so the screen in S2b renders words, not JSON. `raw` feeds the
"Technical details" viewer. The n8n `state_trace` on `chat_histories` keeps being written by
the S1 head for the existing StateTracePanel until S2b replaces that panel.

### Session state contract

`SessionVars` is a Pydantic model with `extra = "forbid"`. Allowlist = every key
`compile-current-state` writes today (R2: the owner keeps all of them, including the nine
with no reader yet): `message_type, intent_hint, domain_hint, user_goal, query_scope,
query_brands, access_levels, entities, routing, escalation, response, last_result_set,
selection_context, date_filter_start, date_filter_end, date_mode, requested_attributes,
match_mode, contains_flyer, dym_offer, dym_candidates, ideation, dym_last_result_set,
tier_menu, picker_last_result_set, picker_families, picker_domain, picker_selection_context,
picker_families_carried, routing_roster_plan, routing_brand, routing_brand_source,
routing_company, routing_companies`, plus the new `pending {kind, team?, domain?}` marker
(R3). `extra = "forbid"` still matters: it is what stops harness keys leaking into customer
sessions (H15). `is_active` stays a phantom read (the parser port reads it as null).

**`pending` is written for ONE kind at S2, and that is deliberate** (amended 5 Sep 2026).
`PendingKind` declares five, but the other four (`team_clarify`, `company_clarify`,
`tier_ask`, `member_offer`) already have a structured reader today - `selection_context`
plus `last_result_set`, which the parser post-processor reads without touching text. Only
`escalation_offer` replaces a TEXT read (`output_exchange.offer_is_open`), so only it is
written; writing a marker nobody reads would be machinery for a hypothetical. S5 writes
the clarify kinds when its escalation lane needs them.
Top-level siblings `user_response` and `quick_reply` persist as today. The ideation service
keeps writing `ideation` through the same store (already in-process, already the CRM).

### Text-sniffing inventory (D11)

The owner's rule: understanding text is the parser's job; everything after it is
deterministic over structured state. The port reproduces existing behaviour first (D8), so
every place the JS matches raw customer text or a previous reply outside the parser is
inventoried during S1 and listed here with its disposition:

| site | what it matches | disposition |
|---|---|---|
| `output_exchange.js` `offeredEscalation` regex over previous `response` | "would you like me to escalate" | replaced by `pending.kind = escalation_offer` (R3, S1/S2) |
| `crossdomain-compose.js` `isAnswered` regex over previous `response` | "Previous turn (" | replaced at S2 by a VALUE, not a session key: `CompiledState.answered_domain`. The question is "did THIS turn's business-summary arm run", which never crosses a turn boundary, so the compiler hands the answer down instead of persisting one. `last_answer_domain` is therefore NOT a session key (amended 5 Sep 2026) |
| `route-turn.js` `tierRepick` | bare digit or exact menu word in the raw message | reproduced (owner's own Fix 6 rule; exact match, not fuzzy); candidate to move into the parser as `tier_pick` after parity |
| `escalation-context.js` `_CO_ALIASES` company name / code match | company name or alias in `escalation.company_pick` | reproduced; the parser already emits `company_pick`, the alias table is a STOPGAP mirror, candidate to delete once the parser output is trusted |
| `output_exchange.js` `domain_switched_by_keyword` and sibling keyword checks | domain keywords in the raw message | reproduced for parity; listed as divergence candidates for the parser prompt (backlog issue) |
| `output_exchange.js` `_coCompanyPick` deterministic tier | the reply minus fillers, word-boundary matched against the offered company pool; refused by a negator or a product-code-like token | reproduced (5 Sep re-port): the LIVE body still has it. The export's rev 8 deletes it and hands the whole job to the prompt, but rev 8 is the unpromoted B-TEAM-1' change, so parity keeps the tier until the owner promotes it |
| `output_exchange.js` member-offer `extract()` + `_ORD` | a bare number or an ordinal WORD in the raw reply | reproduced; the same shape as `route-turn.js` `tierRepick` and inventoried with it |
| `output_exchange.js` `_statedTiers` / `_statedBrands` | tier and brand words in the raw message (English and Malay literals only) | reproduced. The prompt is what covers every other language, which is why the ACCESS LEVELS vocabulary cannot move out of it |
| `resolve_gate._incoming_named_in_message` (7 Sep 2026, AC-819) | the customer's message tokenised, ANY token in `DOMAIN_SWITCH_WORDS` mapping to `incoming` | a NEW call site of the already-inventoried switch-word table, imported, not copied. Decides only whether an `incoming` domain the model attached to a container-hinted token that resolved to products alone was the CUSTOMER's word or the model's guess; the captures forced it (`domain_signal_source` is `intent_explicit` on "M90ss any eta" and on a bare "srtwc287" alike). Same disposition as the switch reader: reproduced parity of the table, divergence candidate for the parser prompt (the container shape belongs there) |
| `escalation._catalogue_teams` (7 Sep 2026, AC-821) | the PARSER's own `routing.suggested_team` word, split-and-membership tested against `SUGGESTED_TEAMS` | NOT a text read, and listed so nobody has to re-derive that: the input is a parser output field, and the thing it is matched against is OUR OWN eight-slug routing vocabulary. Nothing reads `ctx.text` or a previous reply. It exists because the amended prompt contract (R-a) lets the model return the customer's own word verbatim when that word names several teams or none, and something deterministic then has to say WHICH teams it named. Permanent, not a parity stopgap: mapping an ambiguous word to one team is exactly what the prompt must NOT do |
| `output_exchange._team_clarify_pick` (7 Sep 2026, AC-822) | the customer's reply, `strip()` + `casefold()`, compared by EQUALITY against the quick-reply labels and team slugs WE persisted on `pending.options` for THIS ask | a read of the customer's message, and listed as one rather than argued out of the table. What makes it D11-compatible rather than a violation is the shape: equality against a closed list of strings THIS CODEBASE composed one turn earlier, never a regex, a substring or a word list over prose. It is the TAP - the customer pressed a button whose text we wrote - and the parser's own team is tried first, so this only ever runs when the model came back with no team for a reply to a question we asked. Delete it the day a tap is guaranteed to parse; nothing else depends on it |
| R-b `resolve_gate.resolve_bare_reply_under_member_offer` bare-reply member-offer narrowing (owner console pass 5, item B2, 7 Sep 2026, H80/AC-829; corrected on review round 1, blocker B3) | the raw message (`ctx.text.message.message.text`) is read TWICE: its own word count decides whether to probe at all (≤4 words, `co_company_pick`'s own bound), and the message itself is sent to the RESOLVER as one token | a read of the customer's message, but the TEXT decides nothing about what it IS - that is the resolver's job, over real data, the same delegation `resolve_entity_body`'s own `fallback_to_all_types: true` already makes for every entity the parser DOES extract. The first cut of this row (an equality-against-`routing_companies[]`-code-segments reader) is RETIRED: measured against the real vendored capture (6ea9fd1a), no code in `routing_companies[].codes` contains "rpacc" at all, so that reader could never have fired in production - it was built to satisfy an invented test shape. Production's real "rpacc" is a literal product code (two rows, cross-company same-code duplicates). "rpacc" under an open `member_offer` with `entities: []` (turn 6ea9fd1a) is the capture; nothing downstream of the parser can invent the narrowing without asking the resolver somewhere, and `output_exchange` itself has no resolver seam (S6a: it is a pure port of a pre-resolution node) - the read therefore lives in `resolve_gate.run()`, between the resolver seam and the gate, not in `output_exchange`. **Accepted cost, review round 2**: a QUALIFYING turn (open `member_offer`, zero entities named, a bare reply of four words or fewer) pays a SECOND resolver round trip - the probe (`resolve_gate.py` ~:328, inside `resolve_bare_reply_under_member_offer`) and the main `resolve_entity_body` call (~:731) are two separate HTTP round trips to the same route, not one. The alternative - inject the bare text as ONE entity (a guessed `hint`) into `parser["entities"]` before the single main call runs, and let its own `fallback_to_all_types: true` decide - was rejected: `_token_of` (`resolve_gate.py`) applies the product-specific separator fold (`_PRODUCT_FOLD`) ONLY when `hint == "product"`, so a wrong guess sends an un-folded token to a probe that needed folding (a customer-hinted "SRTWC-286" stays "SRTWC-286", never becomes "SRTWC286"), and `_build_token_type_map`'s positional pairing would bind that ONE token to ONE guessed type, so a wrong guess never even reaches the other type's probes at all - the resolver could not "decide" if the guess had already decided for it. The extra round trip is the price of NOT guessing; it is bounded to the one shape this function's own preconditions gate on, not every turn |

Rule for new code in the package: no regex or substring match over `ctx.text` or over a
previous reply. A reviewer finding one is a merge blocker.

### Configuration (D5)

| what | where | why here |
|---|---|---|
| parser prompt, clarifier prompt | prompt registry keys `chatbot_semantic_parser`, `chatbot_clarifier` | versioned, admin UI, per-key model override already exist |
| canned replies (7) | prompt registry keys `chatbot_reply_*` | same store, owner journey B |
| respond.io `space_id` | default respond workspace row (`respond_workspaces.space_id`) | already the integration's home; kills the hard-coded `364817` |
| unsupported domains | `system_settings.chatbot_unsupported_domains` (JSON, default the two today) | the one list the owner has changed; column, not table (CLAUDE.md "both dict builders") |
| stock denial lanes | `system_settings.chatbot_stock_denial_enabled` (bool, default false) | R1: the corrected vocabulary turns two never-tested lanes on; a data switch with a test, not a surprise |
| which lanes the CRM may FINISH | `system_settings.chatbot_completed_lanes` (JSON array of `branch_kind`, default `[]`) | S4: `CRM_COMPLETED_BRANCH_KINDS` says what the CODE can complete, this says what it MAY, and both are required. Without it a lane starts answering the moment it deploys and the n8n edit has to land in the same window or the lane runs twice. With it, deploy / compare / switch on / cut n8n are four separate reversible steps, one lane at a time (AC-308) |
| worker offload, wait | env `CHATBOT_TURN_ON_WORKER` (off), `CHATBOT_TURN_WAIT_SECONDS` (60), `WORKER_QUEUES` | ops, not owner |

### Test strategy (the reason for the port)

1. **Node replay (golden, written first).** For each ported node, `tests/chatbot/test_replay.py`
   parametrises over `nodes/<slug>/<node>/*.json`, feeds `input` + `ctx` to the Python port,
   compares to `expected` after a JSON round trip. Corpus path from `CHATBOT_FIXTURES_DIR`
   (default: the sibling checkout); absent = skip. A curated subset (one fixture per node per
   `branch_kind`, the 11 worlds, the 5 regression guards, the 5 canaries; under 3 MB) is
   vendored to `tests/fixtures/chatbot/` and always runs, in CI too.
2. **Divergence register.** `tests/chatbot/divergences.py` lists `(node, fixture, hazard,
   reason)`. A mismatch passes only if registered. This is how "parity before improvement"
   (D8) is enforced mechanically.
3. **World replay.** The 11 `worlds` run end to end through `run_turn` with the LLM, MCP,
   resolver and access services stubbed from the world's captured node outputs. Asserts
   `reply.text`, `quick_replies`, `session_patch`, `actions`.
4. **Contract tests.** Enum single-source (H28), `SessionVars` allowlist (H15), by-entity-type
   iteration (H16), `_isTimeline` sentinel (H46), dry-run before side effects (H37).
5. **Endpoint + dry-run tests** on Postgres via `tests/_pg_fixture.py` (`ZZT` prefix, scoped
   deletes): auth 401/403, module guard, dry-run writes nothing (row counts), failure path,
   retry re-push.
6. **Live LLM parity (opt-in, not CI).** `scripts/chatbot_parser_parity.py` replays the
   `regression-guards` inputs through the live parser N times and diffs against `_parser_raw`.
   Run before S1 promote and whenever the prompt key changes.
7. **Clone smoke (n8n side, unchanged).** The existing 15-turn clone set + canaries, run per
   slice via the n8n repo's harness in `uac` mode. This is the E2E gate before each promote.

Backend tests run on Postgres only. Brief for the tester names exact files; never the full
suite on the shared DB.


### Capacity and safety of the synchronous turn (owner question, round 3)

- Until S7, intake is capped by the n8n dispatcher (one contact per second). From S7 the
  target is the owner's burst: 50 dealers x 2 questions = 100 turns at once. First questions
  all run in parallel (5 to 10 s), second questions wait only for their own contact (10 to
  20 s), last reply of the burst about 20 s instead of about 65 s today.
- At 100 concurrent: API threads 150 of 320 busy (running + waiters); Postgres behind
  PgBouncer (compose) with the no-session-across-I/O rule; ~50 parser calls in flight at the
  LLM provider (429 = failed stage with backoff); MCP server run with 2 to 4 workers; **n8n
  queue mode's per-worker concurrency limit (default 10) is the first throttle** and is a
  config change (`N8N_CONCURRENCY_PRODUCTION_LIMIT` or more n8n workers).
- **The 5 Sep 2026 numbers just below measured `access_denied`, not a business turn** - the
  load gate's seeded contacts carried no access grant until a 6 Sep 2026 fix (see
  `n8n-changes.md`'s Step 4 and AC-711). Re-measured 6 Sep 2026, chatbot-s8 lane backend,
  one uvicorn `--reload` worker, mocked parser, grant + business-lane switches on (`uptime`
  immediately before: load averages 4.88 5.34 6.24). Raw numbers:
  `wall 120.1s turns 100 p50 120.00s p95 120.06s`, `errors 95`,
  `branch_kind: {'business_query': 30, '(none)': 3}`. `branch_kind` confirms real business
  turns now run and zero `access_denied`; p95 120.06s is the CLIENT's own request timeout
  (95 of 100 requests errored or were still waiting when it gave up), not a completion
  time. This run predates the `_wait_for_turns_to_settle` fix shipped in the same PR: a
  further 21 turns landed roughly 3 minutes later as
  `branch_kind=None, stage=received, status=failed`, because cleanup had already deleted
  their contacts while the single worker was still draining its backlog - the exact bug
  that fix closes. Load average spiked to 52 right after (unrelated concurrent work,
  confirmed via `ps`), which made a same-session repeat unsafe to compare against. The
  pool/thread numbers below are therefore this section's own open question, not yet
  re-answered on the corrected path; a multi-worker re-run (or the prod-host run
  `n8n-changes.md` names) is the next step, not this fix's job.
  **Re-verified 6 Sep 2026** after the settle-wait and STRICT-gate fix, `--timeout 240`
  (load averages 4.16 5.16 6.27 before): `errors 100`,
  `branch_kind: {'business_query': 25}`, `75 turn row(s) missing`, every GRADED row
  `business_query / remembered / done`. 16 more rows landed as `stage=received, status=
  failed` about 10 minutes after grading and cleanup had already run - the settle-wait
  narrows this race, it does not close it once the backlog outlives even its 180s window,
  since a row not yet inserted is invisible to a poller (see `n8n-changes.md`'s Step 4).
  `db connections: baseline 19 peak 40` rules out Postgres; the single dev worker is
  still the ceiling.
- **The seeded contact carries no `contact_access_types` row.** A real dealer holds at
  least one; an entitlement-filtered fetch against an untyped contact is therefore lighter
  than what a real dealer's turn does, so the numbers above are a floor, not a ceiling, on
  fetch cost. Not fixed here - noted so a future capacity run knows what it is not yet
  measuring.
- **What the ceiling actually is, measured (5 Sep 2026, load gate 3b).** The estimate that
  used to sit here ("beyond ~250 concurrent, API threads become the limit") was wrong about
  which resource binds first, and the review that found it was right: the auth dependencies
  ran on the request's `Depends(get_db)` session and never ended their transaction, so every
  in-flight turn pinned one PgBouncer server connection (pool 50) for its whole duration,
  the 45 s ordering wait included. That is fixed (one `db.rollback()` before `run_turn`).
  The gate then ran, with S7 mode on (then an env flag, a settings column since AC-810)
  on ONE uvicorn worker, mocked
  parser, against a local backend:
  - 50 contacts x 2 messages fired at once (100 turns): zero errors, zero contacts answered
    out of arrival order, no overlapping turns, p50 0.88 s, **p95 1.24 s** (target 12 s);
  - the 300-turn repeat (50 x 6): zero errors, zero out of order, p50 1.89 s, **p95 3.77 s**;
  - database connections: with the other processes on the shared dev database subtracted
    (6 remained with this backend stopped), the burst drove the worker to roughly **29 of
    its own 30** (`pool_size=10, max_overflow=20`) at peak - the ceiling. No request ever
    waited long enough to fail (zero `QueuePool` timeouts), but that is not AC-711's "below
    60%" on a single-worker lane box. Production runs `WEB_CONCURRENCY: 8`, so the same
    burst spreads across eight pools; the number to watch there is PgBouncer's 50 server
    connections, which the rollback above now frees between transactions instead of holding
    for the whole turn. **Open with the owner: either the 60% clause is measured per
    production worker (where it holds) or the lane box needs a bigger pool for the gate to
    mean it.**
  - the real limit at this size is uvicorn's 40-thread pool for sync routes, not the
    database. At 300 concurrent, 25 to 48 of the 50 contacts had their two messages reach
    the ENGINE out of send order, because 300 requests contend for 40 threads before any
    engine code runs. The CRM answers in the order it received them, which is all a
    per-request ticket can promise; ordering at the true ingress would need `/chat/turn` to
    be `async` and take the ticket on the event loop. **That is the trigger to make it
    async** - and the same change is what would make the worker offload raise the
    concurrency ceiling, which today it does not (it moves the LLM's CPU off the API
    process, not the thread; see `_run_on_worker`).
  - **The 6 Sep load gate measured the MISS path and must be re-run after H56's fix.** Every
    turn in that burst resolved with no company scope, so the resolver returned zero rows and
    the turn stopped at "Couldn't find" before the fetch step: the p50 / p95 above are the
    cost of a turn that never looked anything up. The fix adds two indexed reads per turn
    (contact + memberships, on a session of its own) and, more importantly, lets the fetch
    and answer halves actually run, so the numbers will move. Re-run the gate before the
    R7 cutover and replace the figures above.
- Sync vs async does not change any of this; a callback design would have moved the same
  work and left the 1/s dispatcher in place.
- API: `WEB_CONCURRENCY: 8` uvicorn workers (compose), default 40-thread pool each for sync
  routes. DB: `pool_size=10, max_overflow=20` per worker (`app/database.py`).
- **Rule: never hold a DB session across LLM or MCP I/O.** The engine closes the session before
  the parser / clarifier / MCP calls and reopens after (the 96/100 connection incident is the
  evidence). Guardrail test asserts no open transaction during the parser call.
  - **ONE named exemption, S6a's resolver seam** (5 Sep 2026). The business lane runs inside
    the session opened for access / routed, so that session is held across the resolver's
    OPTIONAL spec-search model call (2 to 3 s, only when `understand_phrase` fires and the
    normal probes missed). It is an exemption rather than a bug because the resolver is a
    database service - it cannot be called without a session at all - and because the
    connection count is unchanged from today: n8n makes the same call over HTTP while its own
    request holds a pool connection for the whole turn. What is different is only WHERE the
    connection is held. **Trigger to remove it: S6b**, which adds the MCP fetch call and
    splits `looked_up` into its own stage; the session closes before that stage and reopens
    after, exactly as the parser call already does. Pinned by
    `tests/chatbot/test_s6a_gate_dry_run_and_seams.py::TestCapacityRuleDuringResolverSeam`,
    an `xfail(strict=True)` that goes red the day the split lands and the exemption should
    be deleted.
- Timeouts: n8n HTTP node 60 s explicit; parser 8 s **(bound not enforced until #656)**; each
  MCP call 10 s; over = failed stage. The parser bound is not wired because
  `llm_provider.LLMProvider.chat` takes no timeout at all - each provider builds its own
  SDK client - so enforcing it means changing that shared signature and all three
  implementations, which is core work this program does not own. A declared-but-unapplied
  constant reads as a guarantee, so S1 ships without one rather than with a fiction.
- Isolation when measured: a second backend container from the same image, nginx routes
  `/api/v1/external/chat` to it. That is the lift trigger's first step; no code change.

### Cutover ladder (five gates, each with an exit number)

0. **Corpus growth, every slice** (owner: 1,535 fixtures and 11 worlds are a floor). Before a
   slice PR opens, re-capture the nodes it ports from fresh live executions with the n8n
   repo's `scripts/capture-fixtures.py` + census recipe until every branch of every ported
   node has at least 5 real captures; grow worlds to 100+ with at least 5 per `branch_kind`
   and per shape (picker, did-you-mean, tier ask, escalation, offer-hold, media), including
   multi-turn worlds (3 to 5 turns of one contact) for the memory paths. Shadow mode (gate 4)
   keeps adding captures. Coverage matrix lives in `tests/chatbot/COVERAGE.md`, regenerated by
   `scripts/chatbot_fixture_coverage.py`; an empty cell blocks the slice.
1. Node replay (CI): 100% equal or registered divergence; per-node coverage target met.
2. World replay (CI): reply, quick replies, session patch, actions equal on every world.
3. Clone smoke before each promote: same lane and text as the pre-S1 baseline, zero egress.
3b. **Load** (round 4): `scripts/chatbot_load.py` fires 100, then 300, concurrent dry-run turns
   (50 contacts x 2 messages, `is_test: true`, real LLM behind `--live-llm`) at the lane's
   backend. Exit: p95 under 12 s, zero errors, DB pool below 60%, per-contact order preserved
   (ticket order == reply order), n8n concurrency limit raised to match. Runs before the S7
   promote and after any latency-adding change (the "spine p99 vs lock TTL" check the n8n
   plan asked for).
4. **Shadow mode** (new): the live spine also calls `/chat/turn` with `is_test: true` and keeps
   its own reply; the CRM stores its would-be reply beside n8n's actual one
   (`chatbot.turns.shadow_of = <chat_histories.turn_id>`). Exit on 500 consecutive live turns:
   branch parity 99%+, reply-text parity 97%+, every mismatch triaged, p95 under target, zero
   writes proven by row counts. Run at S1 (head) for 3 to 7 days and at S6c (full turn) for 3
   to 7 days. Costs one extra HTTP call per turn during the window; needs nothing beyond D14.
5. Pilot one contact, then one company, then all; no failed turns for 48 h before widening.

### Growth axes (owner question: more tools, more reasoning, horizontal scale)

Same six-layer shape as `PLAN-ai-assistant-architecture.md`. Each axis grows by configuration
and is proven by the ladder above, never by rewrite:

| axis | after this program | grows by |
|---|---|---|
| tools | MCP catalog (40 + `crm_ideation_turn`, which tool search can also pick like any other tool), embedding tool search | one `ToolSpec` + sync; rendering migrates to MCP presenters |
| understanding | registry prompt, strict schema, per-key model | registry publish replayed against the corpus first |
| reasoning | deterministic lanes (parity) | per-lane strategy flag `deterministic` / `agent`; `agent` reuses `ai_assistant_service._run_agent_loop` (tool budget `ai_assistant_tool_call_limit`, write tools stripped on dry run) behind the same Reply / actions / SessionVars contracts; on per lane, per tenant, measured in shadow mode |
| memory | allowlisted session vars | `FrameService` frames from the one Remembered stage |
| evals | replay + shadow | shadow mode permanent for any candidate; judge from `PLAN-ai-assistant-evals-guardrails.md` |
| scale | stateless engine, 8 workers, state in Postgres + Redis | replicas, dedicated chat container, worker offload flag; per-tenant rows, never code branches |

Sequencing: parity first (this program), agent strategy per lane afterwards, behind shadow
mode. Two unknowns at once (new engine + new reasoning) would have no baseline.

## Slices

Each slice = one lane branch = one PR, independently deployable, revertible by re-wiring n8n
(old nodes stay disabled one release). n8n edits follow the n8n repo's plan-build-test-promote
flow; promotion is the owner's call each time. Line counts are the JavaScript being ported.

### S0 - Scaffold (with S1 in the first PR)

Module bootstrap + manifest entry (deps: `base, product, inventory, order, marketing,
procurement, resources, sla`); `chatbot` schema + `chatbot.turns` migration with the `trace`
column and stage writer (AC-003, AC-007; chain onto the current main head,
`scripts/alembic-reparent.sh` at pre-PR gate); `contracts.py` with every enum; slug
`integration.chat_turn.submit` in `EXTERNAL_ENDPOINT_PERMISSIONS` + grant migration for the
n8n integration role; router mount; replay harness + divergence register + vendored subset;
import-boundary test. AC-001 to AC-006.

### S1 - Head (2,300 lines)

`head/parser.py` (prompt key + strict `ParseOutput` schema; model from `agent_model` override
else AI-assistant config; temp 0), `head/output_exchange.py` (1,882 + 55: the deterministic
post-processor, including `deriveRouting` and the dash normaliser), `head/access.py`,
`head/route.py` (245: the 13 predicates, lazy, in ladder order; tier re-pick; `TAG_ONLY`
item shape). `engine.run_turn` up to routing; `delegate.py` returns the n8n envelope.
Human-intervened check becomes an `update_contact_fields` action (AC-108). Audio-not-patched
becomes a failed turn (AC-107). R3 reader accepts both forms. R5 fail-loud.
n8n: replace five spine nodes with one `httpRequest` + two re-emitters (AC-110).

#### S1 pending re-port: B-TEAM-1' (added 5 Sep 2026)

**The port was made from the working-tree EXPORT, and the export is not what production
runs.** The n8n partner session fetched the LIVE `sub-semantic-parser` read-only and the
two bodies differ:

| | live | working-tree export |
|---|---|---|
| `output_exchange.js` | 1,881 lines, sha `a837333a13a2` | 2,043 lines, MANIFEST `locally_edited` |
| system message | 46,942 chars, sha256 `90c0741997...bdf87b66` | 49,318 chars |

The extra material is one unpromoted lane change, **B-TEAM-1'** (+241/-83 over 10 hunks):
`routing.team_source`, a 4-rank team ladder replacing the `?? 'customer_service'` default,
a `resource_attachment` row in `deriveRouting`, a pending `team_clarify` completion block,
and a state-only company-pick resolver with the deterministic word-match tier deleted.

`head/output_exchange.py`, `head/parser.py`'s `ParseOutput` schema and
`chatbot_parser_prompt.py` are now faithful to the LIVE body. Evidence, both directions:

* the five `parser-*` fixtures that sat in `STALE_FIXTURES` because the port emitted a null
  team where they expect `purchasing` / `marketing_product` / `warehouse` now replay EQUAL,
  and their entries are retired. They were live-faithful captures graded against the wrong
  body, which is what a stale-fixture list looks like when the port is the stale side;
* the 19 hand-built fixtures that now fail are reproduced EXACTLY by the pre-re-port Python
  and by nothing else (19 of 19, no residue). They pin the unpromoted body, so they take
  those entries instead. **Every real capture in the corpus is graded; not one `parser-*` or
  `exec-*` fixture is excluded.**

**Re-port B-TEAM-1' when the owner promotes the escalation-routing lane's B3 step**, and
retire the 19 entries in the same change. Diffs:
`output_exchange.LIVE-vs-WORKTREE.diff` and `output_exchange.HEAD-vs-LIVE.diff` in the n8n
session scratchpad (`.../11a092cf-e08a-4fa0-b142-99499e993633/scratchpad/`), beside
`output_exchange.live.js` and `sub-semantic-parser.systemMessage.live.txt`. Nothing goes in
`divergences.py`: this is parity with production, not a deliberate hazard fix.

One consequence worth stating, because it reads as a regression and is not: with the live
body the LLM's own `suggested_team` is used ONLY on a `request_for_help` turn, and every
other turn falls through `deriveRouting` -> prior state -> the hard `customer_service`
default. This body therefore never emits a null team, and `resource_attachment` routes by
the prior-state carry rather than to `marketing_product`.

**The Switch on `duplicate` must sit BEFORE the `build-ctx` / `route-turn` re-emitters.**
A duplicate delivery (D15) returns the FIRST turn's stored answer, and a duplicate of a
turn that FAILED has no `ctx` to replay - the re-emitters read
`$('build-ctx').first().json.ctx.<key>` and throw on a null. Gating first also skips the
work n8n would otherwise redo for a message it must not answer twice. The CRM persists
`ctx` and `item` on `chatbot.turns.response` precisely so the happy-path duplicate has
something real to hand back; the ordering is what stops the failed-turn case throwing.
Parity gate: AC-102, AC-103, AC-111.

### S1b - Parser prompt slim-down (same lane as S1, after parity, before promote)

D16. Inventory the 46 KB system message section by section
(`documentation/plans/chatbot/parser-prompt-inventory.md`): `understanding` stays;
`rule` moves into `output_exchange.py` where it already has a deterministic twin; `example`
survives only when it stands for a phrasing class the corpus shows; `dead` goes. AC-151 to
AC-155. This is where the owner's "no overfitting, no bloat, LLM only for language" lands.

**Delivered (5 Sep 2026), on the LIVE body after the re-port above.**

* 46,906 -> 28,124 characters, **-40.04%** (AC-154). Published as registry version 2 with
  NO label by migration `475_chatbot_parser_prompt_slim`; `production` stays on version 1,
  so the promote is a label move and the rollback is the reverse move. The migration seeds
  both on a fresh database.
* Six `rule` sections deleted, all six with an existing twin in `output_exchange.py`
  (domain-to-team map, the `business_query` force, the `attachment_type` drop on
  `master_products`, the `broaden_axis` domain restore, the brand-plus-tier access-level
  split, the legacy promotion-team suffix). Four `dead` sections deleted, including 700
  characters of n8n JavaScript the registry cannot evaluate and hands to the model as
  source code. **No new post-processor code**: `output_exchange.py` is untouched by S1b.
  Unit cover in `tests/chatbot/test_output_exchange_rules.py`, every case feeding a
  deliberately non-compliant emission.
* Date maths, `demand_qty` and ordinal resolution were each investigated and REJECTED as
  moves, on evidence, in the inventory. Two date gates were measured against the replay
  corpus and would have rewritten fixtures the post-processor deliberately produces.

**AC-153's 99% bar cannot be met by any prompt, and the measurement says why.** A control
run sending the LIVE prompt down BOTH lanes agrees with itself on only 99.0% of key
instances, and on the free-prose `user_goal` only 81.6%. The parser is not deterministic at
temperature 0. Old vs new is 95.8% post-processed (97.5% excluding `user_goal`), i.e. 3.2
points from the noise floor, with every disagreement triaged: 13 improvements, 11
regressions, 20 "neither matches the capture", 38 noise, 1 ungraded, zero untriaged. The
regressions are enumerated in the inventory and none is systematic. **Owner call at
promote**, and the bar in AC-153 should be restated against the measured floor.

**AC-155 could not be met as written**: the corpus contains no Malay capture at all (249
real captures, zero). The parity script uses eight real corpus turns with the message
translated and the real previous state kept, labelled `synthetic-from-corpus`; seven of the
eight agree on every key. Capturing real Malay turns is a backlog item.

### S2 - Tail (2,400 lines)

`tail/outcome.py` (117), `tail/compile_state.py` (1,948 > `SessionVars`), `tail/compose.py`
(110), `copy.py` (escalate-catalog 104, registry keys), CS member offer (23 + 163, team
members in-process). `engine.complete_turn` writes session vars via
`overwrite_for_contact` and closes the turn. R2 drops, R3 marker written.

**Landed 5 Sep 2026**, with four shape notes worth carrying forward:
`tail/member_offer.py` holds `cs-roster-plan` + the roster read + `build-cs-member-offer`;
the roster read is `app/services/team_roster_service.list_team_roster`, EXTRACTED from
`app/api/v1/external/team_members.py` so the endpoint n8n still uses and the tail resolve
the same pool (an id offered by one must be accepted by next-assignee, which is the
endpoint's own stated contract). `compile_current_state` returns a `CompiledState`, not a
bare item: `item` is what the corpus grades and `answered_domain` is what replaces
`crossdomain-compose`'s regex. And `copy.py` reads its strings from
`app/services/chatbot_reply_copy.py`, OUTSIDE the package, because `ai_prompt_registry` is
core and core must not import the module (AC-002). Fourth, `/complete` refuses any turn
that is not `delegated` with a 409 and closes every tail failure `failed` at `remembered`:
without the guard a FAILED turn could be completed, which wrote a fabricated reply into
the customer's session and overwrote the R4 / H32 failure record with `done`.
n8n: `sub-output` body + `save-session-vars` replaced by `/complete` (AC-207). After this PR
the CRM is the only session writer on the turn path (AC-207 grep is the proof).
Parity gate: AC-202, AC-204, AC-205, AC-208.

### S2b - Turn trace in Chat History (FE first, then a read endpoint)

The one screen in the program (D13). **Phase 1:** `ChatTranscript` gains a `TurnPanel` under
each incoming message (replaces `StateTracePanel` once S2b ships): status line, stage timeline
in sentences, failed-stage reason + Retry + "Technical details" (`SearchableCode`), Kept / New /
Cleared memory rows; a "Failed turns only" filter on the list. Built against a mock in
`services/chatbotTurnService.ts` with the contract at the top of the file. Verified with
agent-browser from `/` via System > Chat History at 375 and 1280. **Phase 2:**
`GET /api/v1/system/chatbot/turns` + `POST .../{id}/retry` (`app/api/v1/system/chatbot.py`,
reads `chatbot.turns`, slugs `system.chat_history.view` / `.manage`), mock swapped at the
service boundary. AC-251 to AC-260. Mockup: review page section 10.

**Delegated TTL (AC-260).** A turn handed to an n8n lane is `delegated` until that lane calls
`/complete`. When the lane dies mid-turn the call never comes, so a minute-ly sweep
(`app/services/chatbot_turn_sweep.py`, registered next to the existing ticks in
`app/scheduler/task_scheduler.py`) fails every `delegated` row older than
`CHATBOT_DELEGATED_TTL_MINUTES` (default 10) with a trace note. Two reasons it cannot wait for
S7: the trace list otherwise fills with ghosts that read as work in progress, and R4 makes
Retry available on FAILED turns only, so a stuck row is unrecoverable from the screen that
exists to recover it. The sweep lives in core, not in `app/services/chatbot/`, because the
scheduler is core and AC-002 forbids core importing the package - it settles a ROW, and the
model is core.

#### AC-259 evidence run (agent-browser, 5 Sep 2026)

Against the REAL endpoints: dev server :3000 and the lane backend :8002 (`--reload`,
`ENABLE_SCHEDULER=true`), isolated browser session `chatbot-s2b`. Data was three `ZZT9001`
chat rows plus three turns (answered / failed-at-understood / stale delegated), seeded and
deleted again in the same session; the screenshots are in the coder's scratchpad, and the
run is written here so it can be re-walked.

Steps, and what each one proved:

1. `open http://localhost:3000`, sign in, then **sidebar only**: System > Messaging > Chat
   History (never a deep URL, so the nav config and the permission gate are exercised).
2. List loads: `GET /api/v1/system/chat-history?date_from=..&date_to=..&page=1&limit=50`.
3. Filters > "Failed turns only: off" -> on. Two calls follow, in order:
   `GET /api/v1/system/chatbot/turns/failed-contacts?from=..&to=..` then
   `GET /api/v1/system/chat-history?...&contact_id=ZZT9001&contact_id=900000111&page=1...`.
   That second call IS B1: the contacts the aggregate named are sent back as repeated
   `contact_id`, so the rows, the total and the pager describe one set. Each row carries the
   "failed at understood" badge.
4. Row click opens the drawer: `GET /api/v1/system/chatbot/turns?contact_respond_id=ZZT9001&limit=200`.
   Three turn panels, each with the short id chip (`#6791`, `#f713`, `#780a`).
5. Expand the failed turn: Received ok, Understood failed with the provider error, the
   collapsed "Access, Routed, Looked up, Replied, Remembered, Sent / not reached" row, and
   `tokens 0` on the Understood facts (SEC2). "Technical details" opens the searchable raw
   payload viewer.
6. Retry is DISABLED with the reason "Retry is not configured in this environment.", read off
   `retry_available` / `retry_unavailable_reason` on the LIST response (S7: no second route).
7. AC-260 observed live rather than simulated: a turn left `delegated` since 4 Sep and a
   seeded stale one were both flipped to `failed` / `Failed at Handover` by the minute-ly
   sweep while the run was open, and the panel shows the sweep note as a FOOTER line under
   the timeline ("Gave up waiting: n8n lane did not complete within 10 minutes.") rather than
   as a ninth stage row (S8).
8. Drawer's own "Failed turns only" toggle: 3 panels -> 1.
9. 1280x800 and 375x812: `scrollWidth - clientWidth == 0` on both, drawer 375 wide at 375, no
   panel overflow; drawer closed and reopened (end state, not a mid-transition frame).
10. Console: zero errors, and zero warnings after the drawer was given
    `aria-describedby={undefined}` (Radix warns for a `SheetContent` with no description; the
    alternative, a sentence on screen, is an on-screen explanation).

### S3 - Canned lanes, offer-hold, ideation (150 lines + bridge)

Eight branch kinds complete inside `run_turn` (AC-301). Copy via registry (AC-302). Ideation
as MCP tool `crm_ideation_turn` (new catalog entry + `sync_catalog`, AC-303, AC-307), called
through `MCPRuntimeClient` like every business tool. `chatbot_unsupported_domains` column + both dict builders + schema
(AC-304). n8n: eight Switch outputs and eleven nodes deleted, one `delegate == null` gate added
(AC-305). This is the first PR that makes n8n visibly smaller.

### S4 - low_signal (150 lines)

`lanes/casual.py`: resolve-for-prompt (in-process), `construct-user-prompt` port, prompt key
`chatbot_clarifier`, `central-exchange` fence-stripping. Error = failed turn + today's text
(AC-403). n8n: three nodes deleted (AC-404).

**Delivered 5 Sep 2026.** Two decisions worth recording, because neither is in the UAC and
both change what an operator sees:

1. **A lane setup failure is the LANE's failure, not the engine's.** `resolve_for_prompt`,
   `construct_user_prompt` and `resolve_clarifier_config` all exist to make the clarifier
   call possible, so a missing AI-assistant config or API key is the same customer-visible
   event as the call itself failing: the lane cannot answer. All three are therefore inside
   the lane's own `try`, and the turn fails at `stage = casual_llm` with `branch_kind` still
   `low_signal` and today's `sub-error-logger` text (AC-403). Letting them reach
   `run_turn`'s catch-all instead would null the branch kind and send the PARSER's error
   reply, which is a different lane's words for a different failure. Two pre-existing tests
   (`test_trace_legibility::test_low_signal`, `test_s6a_gate_dry_run_and_seams[low_signal]`)
   assert the branch kind survives, and both were red until this was folded in.
2. **A successful turn ends at `remembered`, not `casual_llm`.** The lane runs S2's tail
   (`complete_turn`: outcome -> compile-state -> compose -> session write), so it closes
   where every other completed lane closes. `casual_llm` is the FAILURE stage only. The row
   passes through `delegated` / `routed` first, which is not bookkeeping: it is the state
   the turn is genuinely in while the clarifier runs, it is what `complete_turn` refuses to
   run without, and it is what the trace screen should show if the process dies mid-call.
   The item handed to the tail is `sub-answer`'s own output shape
   (`{...central-exchange, outcome_fragment}`), NOT `route-turn`'s item - hand it the latter
   and the entry gate runs `escalate-catalog`, which has no case for `low_signal`, produces
   an empty `response`, and wins the compile-state ladder over `central-exchange`. The reply
   comes out blank. Measured, not reasoned: the first wiring did exactly that.

### S5 - Escalation (400 lines)

`lanes/escalation.py`: `escalation-input`, `escalation-context`, the company clarify gate and
reply (`pending.kind`), assignment path as `actions[]` with next-assignee + SLA create
in-process behind a dry-run gate evaluated FIRST (H37). H2 is structurally impossible in one
function (AC-505). n8n: four spine nodes deleted, two subs unpublished; the outbound executes
assign / comment (AC-506).

**Delivered 5 Sep 2026, and the plan above was wrong about the graph.** It described the
EXPORT of `sub-escalation`, which carries uncommitted riders from two unpromoted builds. The
LIVE workflow (`fr2u3e6FKg52cPvK` @ `bac9613b`, 10 nodes, confirmed by 33 captures on the
version) is simpler, and the slice was ported from the live bodies:

* **No `fresh-entity-gate`.** The lane never calls the resolver, so **H26 stays open**:
  escalation routing is brand-blind exactly as it is in production. `resolve_and_gate` is in
  the services bundle for the day B-HB-1 promotes and is asserted never called.
* **No team clarify.** **H27 stays open** too, and the reason is worth writing down: a null
  `suggested_team` is not reachable through the real pipeline at all, because
  `head/output_exchange.derive_routing`'s nullish chain hard-defaults it to
  `customer_service` long before this lane sees it. The hazard lives in the PARSER, not
  here. Porting a clarify the live graph does not have would have shipped behaviour
  production has never run.
* **The ladder has five outcomes, not six, and no `gate` rank**: `picked_member` ->
  `company_pick` -> `sameTeam` (`prior_state` / `prior_state_no_company` /
  `multi_company_unpicked`) -> `stated_brand` -> `none`.

Both omissions are `xfail(strict=True)` in `tests/chatbot/test_s5_escalation_lane.py`, so the
promotion flips them green and forces the markers off rather than being remembered.

**Two decisions, and one open question.**

1. **The lane owns its own unit of work.** `next_assignee` and `sla_create` run on a session
   of the lane's own, not the turn's routing transaction: a turn that fails later must not
   roll an assignment back out from under the person who has already been told about it.
2. **`assign_conversation` is the first action kind the spine does not already perform.**
   Every earlier slice returned `send_message`, which `head-arm` already routes. The n8n
   action executor is therefore a PREREQUISITE of switching this lane on, not a follow-up:
   without it the customer is told a person is coming and nobody is assigned
   (n8n-changes.md, S5 step 1).
3. **The escalation arm RUNS THE TAIL** (decided 5 Sep). n8n sends this arm through
   `tag-out-of-scope` -> `sub-output`, which persists the session - the routing axes and
   `escalate-catalog`'s `includeResponse: false` state text - so skipping the tail would
   have quietly dropped both the moment the lane was switched on. After the lane produces
   its actions the arm hands `complete_turn` the `tag-out-of-scope` item
   (`{branch_kind: "out_of_scope"}`, NOT `route-turn`'s item, which is what makes the entry
   gate run `escalate-catalog`) plus the `clarify` fragment when the clarify arm fired.
   Compile-state writes the session, or returns `session_patch` on a dry run, and the trace
   ends `[..., looked_up, replied, remembered]`. The acknowledgement TEXT stays a tail
   concern and is not one of the actions.
4. **The lane writes no chat history** (decided 5 Sep). `sub-add-comment-respond` does two
   things when it runs - the respond.io comment AND a CRM chat-history POST - and it keeps
   doing both when the caller executes the `add_comment` action. Writing it here as well
   would double the comment: one row from this lane, one from the sub, minutes apart and
   under different authors. Asserted by ROW COUNT in
   `tests/chatbot/test_s5_no_chat_history_write.py`, not by grep, because a count catches an
   import three layers down.
5. **Action shapes** (agreed with the n8n executor author, 5 Sep). `send_message` carries
   `{kind, text, quick_replies, result_set, dry_run}`, with the two sealed halves filled from
   the tail's reply after `complete_turn` returns; `assign_conversation` is
   `{kind, respond_user_id, dry_run}`; `add_comment` is
   `{kind, text, mention_user_ids, dry_run}` where `mention_user_ids` is exactly one RESPOND
   user id (the executor maps it to `sub-add-comment-respond`'s `user_id`, which is what
   respond.io needs for a mention) and the text carries no `{{@user.<id>}}` markup because
   the sub prefixes that itself. A `send_attachments` action is appended last when the tail
   produced an `attachments_src`. The comment text was verified byte for byte against the
   live node expression, timestamps included (`%Y-%m-%d %H:%M:%S` at a fixed +08:00).
   Sample responses for the executor: `documentation/plans/chatbot/samples/`.

### S6 - Business lane (about 7,000 lines, three PRs)

- **S6a resolve + gate (1,500):** `get-access-types`, `resolve-entity` (references resolve
  service, `entity_pins`, H38), `tier-gate` (230), `disallowed-entity-gate` (1,001),
  `build-ctx-resolved`, incoming / customer pickers (157) with their probes,
  `resolve-exit-*`. H16, H46 contract
  tests. Ships behind `delegate` for the fetch step: `/turn` returns `_exit_kind` + `ctx'` and
  n8n's `sub-main-processing` enters at `resolve-arm`.

  **As built (5 Sep 2026), four things the slice learned and the plan did not say:**

  1. **A switch, not a silent cutover.** `chatbot_business_lane_enabled` (default
     FALSE; a config flag at S6a, a `system_settings` column on the Chatbot settings
     screen since AC-810) decides whether the three business arms run the lane. It exists because the n8n
     edit is a separate, hand-made, owner-gated step: until it happens n8n still calls
     `sub-resolve-and-gate` itself, so an unflagged CRM would run the resolver twice on
     every business turn, spec-search model call included. The flag is the shadow window's
     switch and the rollback. See `n8n-changes.md` S6a.
  1b. **n8n retries `resolve-entity`; the port does not.** The httpRequest node carries
     `retryOnFail` and the in-process call has no equivalent, so a transient resolver
     failure that n8n would have survived becomes a shadow-lane failure here (recorded at
     `looked_up`, turn still delegated). Deliberately not added: an in-process call to a
     local service has none of the failure modes a network hop does, and a retry around a
     database call that has already opened a transaction is its own hazard. Revisit if the
     shadow window shows any resolver failures at all; that is the same evidence gate the
     cutover already has.
  2. **The pickers' probes need S6b.** `probe-incoming` / `probe-customer-orders` call
     `sub-get-results`, whose `entity-ids-transformer` and `output-structurer` are S6b, so
     the S6a probe seam raises and both annotators take their own documented UNPROBED arm
     (bare picker with `customer_probe_skip_reason: 'probe_unavailable'`; today's "None of
     these have incoming stock right now."). That is a real difference on picker turns and
     it is the reason the shadow window has to include picker traffic.
  3. **H50 is REPRODUCED, not fixed.** The plan said "brand / company carried once from the
     resolved row (H50)". Carrying it once changes `resolved_company` /
     `resolved_companies` / `routing_brand` / `routing_companies` on real turns, and D8
     says parity before improvement: a fix is a separate, named, tested divergence, and
     there is no evidence yet about which of the node's two derivations is the right one to
     keep. Ported faithfully; the trigger for the fix is a captured turn where the two
     disagree and the owner says which is correct.
  4. **Every capture predates two output keys the shipping bodies emit.** All 254 captures
     were taken against `disallowed-entity-gate` at 934 lines and `tier-gate` at 195; the
     export ships 1,001 and 230. The delta is five changes, of which two are unconditional
     new keys (`specific_options`, `tier_pick_domain`). They are excluded from the replay
     comparison and carry unit tests instead - see `tests/chatbot/_corpus.py`
     `CAPTURE_BODY_ADDITIONS` for the evidence. With them excluded, 212 of 212 gate
     captures, 6 of 6 tier-gate captures and 10 of 10 whole-sub replays are byte-equal.
     **Superseded 5 Sep** by a capture run against the LIVE sub (`tKeQUkZK5cFK9BFa`,
     version `4f367b1c`, pool 682/682): 84 files whose bodies ARE the shipping ones, so
     both keys are graded on them and nothing is excluded. The exclusion is now applied per
     FIXTURE, derived from whether that capture's own `expected` carries the key, so it
     cannot outlive the captures that need it.
- **S6b fetch (700):** tool search in-process (H53), `tool-filter` (59, zero tools = `not_found`
  outcome, H11), tier probe plan / collect, `entity-ids-transformer` (145), `MCPRuntimeClient`
  call at the configured URL (H52), `output-structurer` (482), `fetch-result` (48). Verify the
  live tool-selection distribution before porting any per-tool branch (H49).
- **S6c answer + miss (3,500):** `validator` (46), `promo-picker` (596), `crossdomain-zeroset`
  (151) + probe + `crossdomain-render` (181), `build-result` (88), `sub-answer` (central-exchange,
  miss roster 342, partial dym 553), `not-found-error-message` (667), `sub-miss-suggest`
  (`dym-transform` 561, `dym-annotate` 247, three probes, `family-fetch` via the products
  service), `build-suggest-offer` (925), `access-level-choice-message` (96). H45 one predicate.
  n8n: the whole business lane and seven subs gone (AC-610). Full smoke + canaries (AC-611).

### S7 - Thin spine + CRM per-contact ordering

`/turn` and `/complete` collapse into `/turn` returning the finished reply: in S7 mode
(`system_settings.chatbot_ordering_enabled`) `/complete` answers 410 Gone naming the mode. **Deleting the
route and `delegate.py` moves to S8** - n8n's S2 tail still calls `/complete` until the S7
promote lands on the n8n side, so the code stays until the caller is gone.
`dispatch.py`: redis ticket FIFO per contact inside the request (AC-709, AC-710).
D14 dry-run asserted by row counts (AC-702). Retry re-posts the envelope through the same
ordering (AC-705). Optional worker offload behind `CHATBOT_TURN_ON_WORKER` (AC-703). Load gate
3b before promote (AC-711). n8n: the dispatcher and its redis lists retired; ingress = webhook
> `sub-media-intake` > one HTTP node > one Switch on `action.kind` over the existing send /
assign / comment nodes; `N8N_CONCURRENCY_PRODUCTION_LIMIT` raised; old monolith unpublished;
the clone calls the same endpoint with `is_test: true` (AC-706). H6, H12, H30, H31, H54 land
here. Pilot on one contact first (AC-707), console containment proven (AC-708), then all.

### S8 - Retire and settle (two halves)

**S8a, no promote needed (starts after #674 merges).** Chatbot config leaves the environment
(owner ruling 5 Sep: nothing chatbot-shaped in `.env`, it does not scale past one tenant).
The retry ingress URL and key move to the respond workspace row, the same shape the ideation
intake already uses there (`ideation_shared_service_url` + Fernet ciphertext), edited on the
Respond Workspaces screen under `user_management.settings.edit` (AC-804). The URL is validated
on save and on use: https only, no redirects followed, host must not resolve to the CRM
itself, loopback or a private range, so an admin-editable outbound URL is not an SSRF vector.
`CHATBOT_RETRY_INGRESS_URL` / `_KEY` are deleted from `config.py`; the Retry button reads the
workspace row and says "not configured" when it is empty. Security nits from the S2-S5
review close (AC-806). The chatbot settings get a screen (AC-809, issue #679) and the two
owner-operated switches leave the environment for system_settings (AC-810); only
`CHATBOT_TURN_ON_WORKER` stays a deployment property. The Prompts screen gets a "Run a turn" test for the two chatbot keys,
a dry-run envelope with the chosen prompt version whose trace renders inline (AC-807). S2's
`tier_menu` STALE_FIXTURES entries migrate to CAPTURE_BODY_ADDITIONS (AC-808). Hazard table
closed out (AC-803). Worktree GC.

**S8a DELIVERED, 5 Sep 2026.** What landed, and the two things worth knowing:

1. **The retry ingress is a workspace row, and the URL is checked twice** (AC-804).
   `app/services/outbound_url_guard.py` runs on save AND on use - https only, every resolved
   address checked (not just the first), loopback / RFC 1918 / CGNAT / link-local / IPv6 ULA
   refused, the CRM's own hostname refused, `follow_redirects=False` passed explicitly. It does
   NOT close DNS rebinding, and the guard says so with the trigger for building the pinned
   transport that would. Documentation ranges (TEST-NET) are deliberately allowed: `is_private`
   calls them private, which is true about their reservation and wrong about the risk.
2. **AC-808 was a migration, not an exclusion.** All ten `STALE_FIXTURES` entries moved onto
   `CAPTURE_BODY_ADDITIONS`, so those captures are graded on everything except the one key their
   body predates. `build-ctx` / `media` is unconditional in the live body (measured: 114 of 118
   captures carry it). `compile-current-state` / `tier_menu` is CONDITIONAL, so that entry is
   justified by measurement instead: of 261 captures, 1 carries it on both sides, 254 on neither,
   0 on the expected side alone, and the 6 the port emits it for are exactly the six former stale
   names. The trigger to revisit is a NEW capture appearing in that "port only" column.

**S8a security review, three decisions recorded here rather than left as omissions.** (1) The
Settings slug reaches the two chatbot retry fields through a route of their own,
`PUT /respond-workspaces/{id}/chatbot-retry`; widening the row PUT to that slug had handed it
`api_key`, `base_url` and `is_default` as well. (2) A retry field sent blank or null now CLEARS,
which is what the screen's "Leave blank to turn Retry off" always promised, and gives the key a
revoke path. (3) AC-807's Prompts Test keeps a CALLER-SUPPLIED `contact_respond_id` rather than
inventing a dev-contact column on the workspace, and the endpoint additionally requires
`system.chat_history.view` - the slug the Chat History trace screen uses to show a contact's turn
trace - so nobody reads a contact's remembered state with a weaker slug.

Also closed at S8a, outside the AC list, from a production report: `post_process` used to raise a
bare `KeyError: 'reference_positions'` on a malformed parser emission. It now validates the
emission up front and raises `ParserOutputError` naming every missing key, so H44's guarantee
(failed `understood`, no default routing) arrives with an explanation an operator can act on.

**S8b, after the S7 spine is PUT and order measurement passes.** `/turn/{id}/complete`, the
id-less `/turn/complete` and `delegate.py` are deleted (AC-805; S7 answers 410 until then),
legacy regex readers removed (AC-801), disabled n8n nodes deleted and exports refreshed
(AC-802, n8n side) - which is also where H10's per-node keep-or-drop audit and H54's dead
`Update a Contact` fields land - and n8n repo `CLAUDE.md` updated to say the turn path is the
CRM.

Deferred to the backlog, not S8: the capacity split (service bundles taking a session factory)
and the S9 idea of retargeting the corpus to turn-level behaviour and redesigning lane by lane
behind it (owner, 5 Sep: the n8n code is not a design to keep; the characterization suite is
what makes a rewrite safe).

## Hazard disposition

From the n8n map's 55 catalogued hazards. **Closed out at S8a (AC-803).** Every row carries
one of three markers, and each one is traceable to code, a test or a backlog entry that
exists today - nothing here is an intention:

* **fixed (AC-x)** - the hazard cannot happen in the port, and the AC names what proves it.
  "by construction" means the port's shape removes it rather than a branch being added.
* **reproduced (AC-x)** - the live behaviour is deliberately KEPT (D8: parity before
  improvement), with the AC that pins it. Where the reproduction is registered as a
  divergence it names the entry in `tests/chatbot/divergences.py`.
* **backlog (id)** - real, and not this program. The id is a `documentation/backlogs/
  backlog.md` entry, `S8b` (this plan's own second half, with its AC), or `S9`.

| id | hazard | close-out |
|---|---|---|
| H1 | `stock_check` vs `check_stock`, two dead lanes | **fixed (AC-306)** - correct vocabulary in `head/route.py`, both lanes behind `system_settings.chatbot_stock_denial_enabled`, default off (R1 resolved); `tests/chatbot/test_route_unit.py`. **Flag-off is not byte-identical and that is deliberate:** live still EVALUATES its dead predicate, so a contact with no `is_allowed_stock` custom field makes `custom_fields.find(...).value` throw and the turn dies; with the flag off the port skips the predicate and answers `business_query`. A strict improvement (an answered turn instead of a dropped one), invisible to the corpus because every captured contact carries the field, and visible in shadow mode as a CRM reply where live sent nothing. With the flag ON the throw is reproduced exactly, and the turn is recorded `failed` at `stage = routed` rather than escaping. The 4 Sep capture run found the first real turns the flag would wake: `rs1a-15118057`, `15129939`, `15137785`, `15139158`, all `check_stock` from contacts without stock access, all `business_query` in live and `demand_qty` with the flag on. |
| H2 | clarify-company-reply race | **fixed (AC-505)** - `lanes/escalation.py` builds the clarify ask from the turn's own resolved companies, not from a re-read; `tests/chatbot/test_s5_escalation_lane.py`. |
| H3, H51 | fan-out order, 165 by-name reads | **fixed (AC-102, AC-103, by construction)** - one in-process call graph, so there is no fan-out to order and no `$('node')` to read; each of the 165 reads became a named parameter, and the replay of every `route-turn` / `output_exchange` fixture is what proves none was dropped. |
| H4 | media lane guards: (a) a chat-console media turn reaches the billable `POST /external/media/process`, (b) `if-media-in` FALSE silently makes a media turn a text turn, (c) the async poll loop has zero observed live traffic | **backlog (BL-055)** - all three sit in `sub-media-intake`, which stays n8n by D1, so none of them moved with this port. The CRM half of (a) is closed by construction: the turn engine never calls the media endpoint at all - `ctx.media` arrives ON the envelope - and a dry-run turn writes nothing outside `chatbot.turns` (AC-702). The gate that (a) actually needs is on the CALLER, which is the n8n side. (c) was not ported: it has no live traffic to port. |
| H5 | audio dead end | **fixed (AC-107)** - an unpatched audio attachment closes the turn at `intake` with the recorded reason instead of vanishing; `engine.py`, `tests/chatbot/test_engine.py`. |
| H6 | second unlocked spine entry | **fixed (AC-701)** - S7 mode makes `/turn` the only trigger and `/complete` answers 410 Gone naming the mode; `tests/chatbot/test_s7_dispatch_edges.py`. The two `/complete` routes and `delegate.py` are DELETED at **S8b (AC-805)**, gated on the owner's S7 promote. |
| H7 | orphaned answer LLM | **reproduced (AC-607)** - there is no answer LLM anywhere in the business lane (D10), which is what live actually does; `lanes/business/fetch.py` states it and `output_structurer` is deterministic string building. |
| H8, H9, H47 | sendmsg fallback / presign error / mimeType | **backlog (BL-050)** - n8n outbound, stays n8n by D1. |
| H10 | dark-by-flag nodes | **backlog (S8b, AC-802)** - no dark node's intent was ported into S1 to S6c (the lanes were ported from the LIVE reachable bodies), so the outstanding half is the n8n-side per-node keep-or-drop, which is the same pass that deletes the disabled nodes and refreshes the export. |
| H11 | zero tools = empty turn | **fixed (AC-604)** - `tool-filter` emitting zero tools becomes a distinguishable `not_found` outcome instead of a silent dead end; `lanes/business/fetch.py`, `tests/chatbot/test_s6b_fetch_lane.py`. |
| H12 | empty pop = silent success | **fixed (AC-701, by construction)** - there is no pop: the request IS the turn, so an empty queue cannot look like a completed run; `tests/chatbot/test_s7_ordering_and_offload.py`. |
| H13 | frozen string contracts | **fixed (AC-203, AC-106)** - the escalation offer is read from a persisted `pending` marker (`tail/pending.py`), not from the bot's own previous words. Registered in `divergences.py` as `H13/H14 (R3)`, field-scoped so every other byte of the session patch is still graded. The legacy regex readers themselves are removed at **S8b (AC-801)**. |
| H14 | pending state inferred from text | **reproduced (AC-202) then fixed (AC-203)** - the PRINCIPLE is kept (every bot question that expects a shaped answer persists a marker) and the text-sniffing is not; same divergence entry as H13. |
| H15 | fresh object literal | **fixed (AC-203)** - `SessionVars` is Pydantic with `extra="forbid"`, so a key nobody declared cannot be written; `tests/chatbot/test_tail_units.py`. |
| H16 | by_entity_type keys rendered | **reproduced (AC-603)** - kept as a contract test rather than changed, because the rendered keys are what customers read today; `lanes/business/gate.py`, `tests/chatbot/test_resolve_gate_unit.py`. |
| H17 to H21, H24, H25 | resolver / MCP data bugs (LESSONS 66 to 85) | **backlog (BL-051)** - CRM-side, one issue each. |
| H22, H23 | cross-domain session pollution / dym leak | **fixed (AC-609)** - per-domain allowlist at S2 and verified in the answer lane; registered in `divergences.py` as `H22 / H23`, and pinned by `tests/chatbot/test_s6c_answer_lane.py`. |
| H26, H27 | escalation brand-blind / hard team default | **fixed (AC-502)** - `lanes/escalation.py` ports the brand-aware team ladder rather than the `?? 'customer_service'` default; `tests/chatbot/test_s5_escalation_lane.py`. |
| H28 | enum drift | **fixed (AC-109)** - every enum the parser emits is declared once in `contracts.py` and asserted against the prompt; `tests/chatbot/test_contracts.py`. |
| H29 | carried picker beats born roster | **fixed (AC-205)** - `tail/compile_state.py`; registered in `divergences.py` for `compile-current-state/b56-roster-turn`, whose capture IS the defect, and pinned by `test_tail_units.py::TestBornRosterWins`. |
| H30, H31 | per-contact FIFO + lock TTL | **reproduced (AC-709, AC-710)** - per-contact FIFO kept, contacts parallel, on a redis ticket instead of n8n's one-per-second dispatcher; `services/chatbot/dispatch.py`. The TTL check became load gate 3b (AC-711, measured 5 Sep: 100 turns p95 1.24 s, 300 turns p95 3.77 s, zero out of order). |
| H32 | failed turn dropped | **fixed (AC-704)** - every failure closes the row `failed` with its stage, error and trace, and answers with today's error reply; manual Retry only, no auto-retry (R4). `divergences.py` carries the `H32` entry; `tests/chatbot/test_engine_failure_paths.py`. |
| H33 to H36 | human-intervened sweeper | **backlog (BL-049)** - R6, own plan. |
| H37 | next-assignee before is_test guard | **fixed (AC-503)** - the dry-run check is evaluated BEFORE any side-effecting seam in `lanes/escalation.py`, and `lanes/ideate.py` was brought to the same rule after the round-2 security review; `tests/chatbot/test_s5_escalation_lane.py`. |
| H38 | duplicate codes across companies | **fixed (AC-601)** - `entity_pins` ported at S6a; `lanes/business/resolve_gate.py`, `tests/chatbot/test_s6a_gate_dry_run_and_seams.py`. |
| H39, H40 | dym dedupe drop / cert twins | **reproduced (AC-608)** - ported faithfully and pinned by the S6c replay; the fix stays a future registered divergence, which needs the owner's ruling. `tests/chatbot/test_s6c_answer_lane.py`. |
| H41, H48 | parser prompt weaknesses | **backlog (BL-053)** - prompt, not port. |
| H42 | menu-word substring mis-map | **reproduced (AC-102)** - exact-match kept, as live does; `head/route.py`, `tests/chatbot/test_route_unit.py`. |
| H43 | missing `$4` | **fixed (AC-604, by construction)** - the in-process call binds the domain as a parameter, so `domain=None` can only mean "no filter", never "the caller forgot to wire it"; `lanes/business/fetch.py`. |
| H44 | soft default on malformed parser output | **fixed (AC-105)** - strict structured output at the provider, and anything else is a failed `understood` stage with no default routing (R5). Hardened again at S8a: `post_process` validates the emission up front and raises `ParserOutputError` naming the missing key, so a malformed mock or model answer can no longer surface as a bare `KeyError` on the trace. `tests/chatbot/test_harness_injections.py`. |
| H45 | did-you-mean offers rows already shown | **fixed (AC-609)** - one predicate in `lanes/business/answer.py`; `tests/chatbot/test_s6c_answer_lane.py`. |
| H46 | `_isTimeline` contains-sentinel | **fixed (AC-603)** - the sentinel and its reading are declared ONCE (`contracts.TIMELINE_SENTINEL` + `is_timeline`) with a contract test including the mixed-array case, and `output-structurer` consumes that reading instead of re-deriving it. |
| H49 | `orders_by_product_list` never selected | **reproduced (AC-604)** - verified before S6b and left alone: the tool has never been selected in any graded capture, so the lookup tables are ported verbatim and no per-tool branch was added. The measurement that would justify one has not been taken. `lanes/business/fetch.py` records this. |
| H50 | brand / company derived in five places | **reproduced (AC-602)** - explicitly NOT fixed at S6a: carrying it once changes `resolved_company` / `resolved_companies` / `routing_brand` / `routing_companies` on real turns, and D8 wants a named, tested divergence with evidence first. Trigger: a captured turn where the node's two derivations disagree, plus the owner's ruling on which wins. |
| H52 | raw IP, plaintext MCP, SQL interpolation | **fixed (AC-604)** for the CRM half - the MCP URL is configuration, not a literal, in `lanes/business/fetch.py`. The `live-respond-close-convo` SQL interpolation is **backlog (BL-049)**. |
| H53 | n8n hits production Postgres | **fixed (AC-604)** - tool search runs in process against the request's own session. The SLA reads by `live-respond-*` are **backlog (BL-049)**. |
| H54 | dead custom fields | **fixed (AC-108)** - the port emits exactly one contact-field write, `{"is_human_intervened": false}` (`engine.py`, the single `update_contact_fields` action site); none of the five `removed:true` legacy fields is carried. Deleting the fields from n8n's own `Update a Contact` node is **S8b (AC-802)**. |
| H55 | scope continuity never implemented | **backlog (BL-052)** - an undelivered requirement, not a bug to reproduce; deliver after parity. |
| H56 | in-process route call skipped the company-scope dependency | **fixed (AC-811, this PR)** - the engine calls the resolver ROUTE (and through it stock, promotions, product attachments) in process, so `apply_company_scope` never ran and every session read `UNSET`, which `build_company_predicate` compiles to `false()` for every owned model: every business turn answered "Couldn't find: `<code>` (product)" for a product in the contact's own company (measured in prod and locally, 6 Sep 2026). `run_turn` now resolves the contact's companies once, from the SAME rule the X-API-Key path uses (`company_scope_resolver.resolve_contact_company_scope`), and wraps the session factory so every session the turn opens carries it; an unknown contact fails closed to zero rows, never to "all companies". ALL THREE session seams under `app/services/chatbot/` are covered: (1) `engine._session`, every call site, via the wrapped factory; (2) the business lane's own `answer_services_for` family read, which opens off that same factory; (3) the escalation lane's `escalation_services.production_session()`, which reached for `SessionLocal` directly and now takes the turn's factory. Seam 3 was NOT a failing lane: `post_next_assignee` pins its own scope (`_scope_request_to_company`) before every `Team` / `AgentTeam` read, so the round-robin draw worked. What ran unscoped is the pre-pin half (`_routing_company_for_body`) and the lane's own unit of work; threading the factory is defence in depth and keeps one mechanism for the whole turn instead of a per-callee pin. The tail (`complete_turn`, n8n's `/complete` entry) stamps the row's contact unconditionally. Evidence: `tests/chatbot/test_engine_company_scope.py` (6 tests). |
| H57 | dry run was not isolated: four shared surfaces | **fixed (AC-812, this PR)** - `Envelope.dry_run` suppressed the ENGINE's writes and nothing else, so a test turn from the Prompts screen against a real contact still (1) wrote an `integration_log` row carrying that contact's id and the customer's message text on `/turn` and on `/complete`, (2) took a real ticket on the SHARED per-contact redis ordering keys (`chatbot:seq|done|running:{contact}`), so a live WhatsApp message from that contact queued behind a test, (3) shadowed D15 dedup - `_existing_turn` matched `(contact, message_id)` alone, so the live delivery of that message came back `duplicate: true` carrying the test row's canned reply, which means the customer was answered with nothing at all (and, in the mirror, a test replay of an already-live message ran nothing, which made the Test button useless on exactly the messages worth testing), and (4) appeared on the operator surfaces - counted in `GET /turns/failed-contacts`, listed in Chat History, and RETRYABLE, where retry re-posts the original message at the live n8n ingress and answers the real contact. Fixed at each site: the call log is skipped on a dry run (the `chatbot.turns` row already carries envelope, response and trace under `is_test`, which is strictly more); a dry-run turn bypasses ordering entirely (`ordered = _s7_mode(...) and not dry_run`) because a test turn must never delay a real customer; dedup narrows to the envelope's own `is_test` and migration `481_chatbot_turns_is_test` widens the unique key to `(contact_respond_id, message_id, attempt, is_test)` so the two worlds can coexist; the two reads default `include_test=false` and retry answers 409 `test_turn_not_retryable` before it claims the row. Evidence: `tests/chatbot/test_dry_run_isolation.py` (12 tests). |
| H58 | the MCP tool pick could select a WRITE tool | **fixed (AC-813, this PR)** - the business lane picks ONE tool per turn by cosine similarity over the shared `mcp_tool` embedding pool and calls the single top hit (`lanes/business/fetch.py::tool_filter`) with no allow-list check, and six write tools live in that pool: `crm_it_support_ticket_create`, `crm_complaint_close`, `crm_order_cancel`, `crm_purchase_request_approve`, `crm_purchase_request_reject` and `crm_ideation_turn`. Nothing but the scores stood between an ordinary customer question and cancelling an order. **The pool is NOT filtered** - it is shared with the in-app AI assistant, whose Tool-RAG only sees what is embedded and whose four record actions are put there ON PURPOSE by `record_action_bootstrap`, gated behind a user confirmation and a permission check; filtering it would have taken the assistant's write tools away and needed a prod re-seed to take effect. The rule is the CHATBOT's, because the chatbot has no user to confirm with, so it lives on the chatbot's own two seams: `services._tool_search` drops anything outside the allow-list from the candidate list (before `tool_filter`, so a write tool is not even among the `rejected`, and NOT inside `tool_filter` itself, which is graded byte-for-byte against 38 captures), and `fetch.ensure_read_only` refuses one at both call seams - `fetch.call_tool` and `services._mcp_call`, the single choke point every `mcp_probe` caller goes through. The allow-list is a FROZEN SET in the backend (`fetch.CHATBOT_READ_ONLY_TOOLS`), not a catalogue walk: the deployed backend image has no copy of `sorento_crm_mcp` (compose builds it with `context: ./sorento_crm_backend`, `mcp` is not in `requirements.txt`, no volume mounts it), so a catalogue read on the turn path would raise in every container and `run_fetch`'s broad `except` would turn EVERY live business turn into "MCP tool X failed". The catalogue stays the source of truth for WHICH names belong in it - `method` GET, or an explicit `read_only=True` for a POST whose body is transport rather than a side effect - and CI asserts the two are EQUAL in both directions, where the catalogue is readable. No migration, no re-seed, no deploy step. Evidence: `tests/chatbot/test_tool_pool_is_read_only.py` (18 tests) and `test_dry_run_isolation.py::TestMcpToolPickRefusesWriteTools`. |
| H59 | the ideate lane was unreachable: `route-turn`'s request-for-help arm sits one rung above the ideate arm | **fixed (owner console pass, 6 Sep 2026)** - "I have an idea" parses as `message_type: request_for_help` with `domain_hint: ideate`, and `wants_escalation_or_help()` fires first, so the turn answered `out_of_scope` the moment access flipped to allow. `domain_hint == 'ideate'` is exempted beside `portal_link`. A DIVERGENCE from live, whose ladder order hides the arm permanently; registered as `divergences.IDEATE_NOT_SHADOWED_BY_REQUEST_FOR_HELP`, not fixture-visible (no capture carries an ideate hint). Console-check note: the lane is ALSO gated by access, and this database has no `ideation` access agent at all, so every ideate turn is `access_denied` before routing - a data prerequisite the owner has to grant, not a code path. Evidence: `test_route_unit.py::TestIdeateNeverShadowedByHelpRequest`. |
| H60 | a cold "all of them" was answered "Hi!" | **fixed (owner console pass, 6 Sep 2026)** - the parser reads the scope correctly (`scope_intent: broaden`, `broaden_axis: all`) but also stamps `message_type: casual` with no domain, and `is_low_signal()` fires on `casual` alone one arm above `is_clarification()`. The clarification predicate now also accepts that scope pair on a null domain and its arm moves above the low-signal one; the two message_type sets are disjoint, so no parse the live ladder ever saw changes branch. A ROUTER backstop, not a parser retune - the scope keys were already right, and re-teaching the prompt to stop stamping `casual` on a two-word reply has no upper bound. Registered as `divergences.BROADEN_ALL_IS_A_CLARIFICATION`. Evidence: `test_route_unit.py::TestBroadenAllNeverReadAsLowSignal`, plus a real cold turn in the console check (`clarify_menu`). |
| H61 | the status-aware miss message dropped the estimated delivery date | **fixed (owner console pass, 6 Sep 2026)** - `not-found-error-message`'s delivered-status arm derives ` (estimated delivery <date>)` and then never uses it; the JS does the same, and the port was faithful. The owner's ruling wants the date said, and the resolved order's own display already carries it. Registered as `divergences.STATUS_MISS_MESSAGE_STATES_THE_ETA`; not fixture-visible. Evidence: `test_s6c_answer_lane.py::TestStatusAwareMissMessageIncludesTheEtaDate`. |
| H62 | a requested code with nothing on either side was silently dropped | **fixed (owner console pass, 6 Sep 2026)** - `crossdomain-render`'s "positive facts only" rule (`if not rows: continue`) meant a three-code turn answered about the two that had something and never mentioned the third. Owner ruling: name it ("no stock and no incoming") and offer the escalation. Two guards keep the new line honest: a code the probe never ASKED about (no uuid) stays silent, because an absence nothing established is exactly what the rule exists to prevent; and `_xd.missing` means "the primary render did not echo this code", which is only "this code has nothing" when the render is product-keyed or came back empty - a warehouse breakdown and a demand-quantity verdict both answer ABOUT a code without printing it, and two engine-path tests caught the first version denying the stock it had just printed. Registered field-scoped on the one capture that reaches the case (`crossdomain-render/exec-14131197`). Evidence: `test_s6c_answer_lane.py::TestThirdCodeWithNoStockAndNoIncomingIsNamedWithEscalation`. |
| H63 | the multi-company "no records" line leaked an internal debtor code | **fixed (AC-814, owner console pass, 6 Sep 2026)** - see AC-814. Labelled through `answer._AXES`, one name per customer record, alias rows collapsed (customers only - a product code that extends another is a different product), the date window stated, and all the silent companies in one sentence. Registered field-scoped on `output-structurer/gr-15145805`, the one graded capture that reaches the block. |
| H64 | an escalation inherited the previous turn's team | **fixed (AC-815, owner console pass, 6 Sep 2026)** - see AC-815. A `person_mention` resolves against the staff roster (one match routes to their own team as a direct pick, no match or several asks); a null-team ask with a previous routing asks instead of inheriting. Live has neither gate, so it is a registered divergence (`divergences.ESCALATION_ROUTES_BY_STAFF_LOOKUP`), and the four graded escalation nodes are untouched - the gate lives in `run()`. The decision is previewed on a dry run as well, because the console the owner tests from runs dry; measured on a real turn ("escalate to Nurain" asked which team instead of assigning to the inherited `purchasing`). |
| H65 | the pending context was rewritten every turn | **fixed (AC-816, this PR)** - `compile-current-state` recomputed `selection_context` and `last_result_set` from THIS turn's outcome alone, so a turn that built no offer of its own wrote `null` plus the ANSWER's own rows. After a tier menu, a did-you-mean, a promo picker or a member offer, the customer's FIRST pick worked and the second had nothing left to resolve against: "1" answered, then "2" came back as row 2 of the answer, or as nothing. `_picker_carry` had already fixed exactly this for the `require_specific` disambiguation picker (H29) and its docstring records the reason the other four were excluded, but the excluded four are the ones the customer meets most. `tail/compile_state.py::_offer_carry` now carries the previous label and roster forward when this turn's ladder produced neither; a new offer this turn still replaces it (the ladder runs first and the carry only runs when the ladder was silent, so H29 is untouched) and a domain change still clears it. `member_offer` keeps ONE safety condition rather than the blanket exclusion, because the hazard `_picker_carry` names is real: it is carried only while the escalation offer is UNANSWERED - neither accepted (`is_escalation_confirmation` / `preferred_assignee_id`) nor declined (`escalation_declined`) this turn - so the arming pin can never be re-seated after the customer has answered it. Owner ruling, 6 Sep 2026: the old n8n spine carries the same reset, so this is a NEW rule and not a port bug, and the 13 captures of the shape are registered in `tests/chatbot/divergences.py`; 22 world replays move from graded to skipped, each with a NAMED reason and in three classes: 14 through the ruling's own clause in `worlds.body_difference`, 7 through that file's pre-existing "the parser post-processor disagrees with the body that produced this capture" arm (rules 2 to 4 change the head's output, and grading a TAIL against a different understanding of the turn would attribute a head-side difference to the tail), and 1 multi-turn chain that now grades a shorter clean prefix. Evidence: `tests/chatbot/test_tail_units.py` (`TestTierAndPromoOffersCarryUntilOverwritten`, `TestTheMemberOfferCarryStopsAtTheAnswer`) and `test_topic_unit.py`. |
| H66 | carried entities outlived the topic | **fixed (AC-816, this PR)** - the entity-op executor keeps every prior entity whose axis the current turn did not name, which is right inside one subject and wrong across two: an entity named while the customer was asking about orders went on narrowing the promotion question that followed, and the domain blocklist only catches the ones whose HINT is illegal in the new domain. A hint that is perfectly legal there and still belongs to the old subject was invisible. `head/output_exchange.py` now drops the carried set on a topic change, placed AFTER the blocklist so the existing mechanism keeps its own removals and its own diagnostics, and gated on three conditions: the domain must be the customer's own (a DECISIVE intent plus a domain, never the model's guess at a bare token's shape - fork exec 13687305), the new question must bring its own entity (a turn with no scope of its own has the carry as its only scope), and `topic.changed` must agree. Two captures in the whole corpus change, both the exact shape the rule exists for. Two neighbours ride with it under the same ruling: a pending member offer no longer reads a filter reply (a date window or an entity) as an out-of-range pick, and an out-of-range digit against a single-company roster reprompts instead of falling to `low_signal`. Evidence: `tests/chatbot/test_output_exchange_rules.py`, `test_route_unit.py`, `test_resolve_gate_unit.py::TestBareEntityInheritanceIsBlockedAtResolveTime`. |
| H67 | the ambiguous-customer picker could not be told apart, and its "has delivery" stamp counted any order | **fixed (AC-817, this PR)** - two defects on one line. The line rendered the account name alone, and `_rep_label` folds a family to its bare name, so a line stood for accounts the customer could not tell apart; it now carries every company ledger its family spans (`(SRT, MOCHA)`), which the resolver stamps as `company_code` beside `company_name` in `_attach_company_info`. Review of #706, S1/S2: the suffix first read the representative row's code alone, wrong for a family spanning two ledgers; and the "two identical lines" cause the AC first gave was withdrawn - `_cust_base` groups by name, so one name in two ledgers is ONE family that passes silently with both ledgers' rows, as live has it (captures rg-15114061, rg-15125764), and a per-ledger split was tried, measured (eight captures moved, identical lines wherever `company_code` is absent) and not taken. And `pickers.annotate_customer`'s `with_delivery` set was built from EVERY row `crm_order_management_orders_list` returned, which answers "does this customer have ORDERS", not "does this customer have a DO" - a customer whose orders had not shipped read `- has delivery`. It now counts only rows carrying an `Actual Delivery Date` and the stamp reads `- has DO` / `- no DO`. Registered field-scoped on the 4 `annotate-customer-picker` captures and the 2 `sub-resolve-and-gate` exit arms that carry the message; none of it can be fixture-visible, because n8n's own body says "delivery" and counts every row. Evidence: `tests/chatbot/test_s6a_gate_dry_run_and_seams.py::TestOwnerRulingACustomerPickerDOStamp`, `::TestAPickerLineNamesEveryLedgerItsFamilySpans`, `tests/chatbot/test_resolve_gate_unit.py::TestPickerProbeArms::test_an_order_with_no_delivery_order_is_not_counted_as_one`. |
| H68 | the picker probe seam was never wired, so every live picker rendered bare | **fixed (AC-817, this PR)** - `services._probe` raised `NotImplementedError` on the grounds that `sub-get-results`' `entity-ids-transformer` and its renderer were unported. Both landed with `_mcp_probe` in #705 (lesson 102), so the picker probe is bound to that same sub-workflow seam rather than to a second one: `resolve_gate._run_probe` was catching the exception on EVERY ambiguous-customer turn and rendering the bare picker, which means the annotator's stamp - old wording or new - had never once reached a customer. Not rebuilt against `OrderService` in process, deliberately: `crm_order_management_orders_list` is a read the CRM already serves through the MCP tool, and a second render envelope in the backend would have to be kept in step with `sorento_crm_mcp/presenters.py`, which is the file the annotator's own field labels are read from. Evidence: `tests/chatbot/test_s6a_gate_dry_run_and_seams.py::TestOwnerRulingATheCustomerPickerReachesTheProductionProbeSeam` - the whole seam on real seeded Postgres rows, faking only `MCPRuntimeClient.call_tool` and only with the STRING the real client returns. |
| H69 | a pending escalation offer swallowed a request for a different team | **fixed (AC-817, this PR)** - turns 9a40182a and 8b3a3b80. `offered_escalation and is_affirmative` ignored `routing` entirely, so "escalate to marketing" over a pending warehouse offer confirmed the WAREHOUSE offer and the conversation was assigned and commented "Team: warehouse". The check now reads the parser's OWN team off the pre-derivation snapshot (`llm_team_n`), because the derived `routing` has already inherited the offer's team through the nullish chain and therefore always agrees with itself. A named different team wins and is not a confirmation; a `request_for_help` naming no team sets `escalation.team_unresolved` so the lane asks. "Is this an acceptance?" is answered by the model's own `escalation.is_escalation_confirmation` and nothing else: `message_type` cannot tell "ESCALATE" from "can someone else help me" (both `request_for_help`, `is_affirmative: true`, null routing), and the accept-word list the first cut put behind the flag was a D11 hard-fail that re-opened the defect ("ok, can someone else help me"); the one capture the model gets wrong ("YES ESCALTE", parser-15074293) is a registered divergence (review of #706, B2). The lane's own half is the same distinction one layer down, bounded by the review's B1: an acceptance skips the clarify, and the clarify needs an OPEN offer or an INHERITED non-default team - the previous routing is never absent, because the chain's hard default (`DEFAULT_SUGGESTED_TEAM`) is persisted every turn. Evidence: `tests/chatbot/test_output_exchange_rules.py::TestOwnerRulingD1PendingOfferTeamMismatch`, `tests/chatbot/test_s5_escalation_lane.py::TestOwnerRulingD1LaneTeamMismatch`. |
| H70 | a filter reply under an open roster was answered with a clarifier | **fixed (AC-818, this PR)** - live turn 0d1fc129. AC-816 rule 3 restored the domain, the intent, the window and the entities, and stamped `member_offer_filter_modification`; it did not touch `message_type`, and the parser had emitted `casual` for "last month". `route.decide`'s `is_low_signal` arm tests that word first, so the turn branched `low_signal` and the customer was asked for the date range they had just given. `is_low_signal` now returns false for a turn carrying the flag AND a domain: a turn that changed a filter on a question that is still open is the opposite of content-free. The domain is still required, so the flag alone can never rescue a turn with no question behind it. No capture carries the flag, so nothing in the replay corpus moves. Evidence: `tests/chatbot/test_route_unit.py::TestAFilterModificationIsNeverLowSignal`, `tests/chatbot/test_r3_pending_end_to_end.py::test_both_narrowed_turns_are_ANSWERED_and_the_roster_stays_pending`. |
| H71 | a mis-hinted product code invented an incoming domain and answered the wrong question | **fixed (AC-819, this PR)** - live turn ace4cec6. The parser hinted "srtwc287" `inbound_shipment` and emitted `check_incoming` / `incoming`, so `domain_signal_source` was `intent_explicit` and AC-816 rule 4's bare-entity inheritance was bypassed by a domain the model had invented from a mis-shaped token; the reply led with "No incoming stock (ETA) found for SRTWC287" while the resolver's answer for the same token was two PRODUCTS and no shipment. `resolve_gate.retype_shipment_miss` runs between the resolver and the gate - the first point in the turn where that evidence exists, and the reason this cannot live in `output_exchange` - and retypes the entity when there are zero shipment matches and at least one product match. The DOMAIN half additionally requires that the customer's own message named no incoming word, because `domain_signal_source` cannot tell "M90ss any eta" (capture rg-15123789) from a bare code: four of the five graded captures of this shape say ETA or incoming aloud, and a product legitimately HAS incoming stock. Five captures carry the retype and nothing else in the sub's output moves on any of them, registered field-scoped. The prompt-side half (state the container shape) is a D2 prompt change outside this lane. Evidence: `tests/chatbot/test_resolve_gate_unit.py::TestAShipmentHintedTokenThatIsOnlyAProductIsRetyped`. |
| H72 | a zero-stock code was mentioned only inside the incoming block | **fixed (AC-820, this PR)** - prod turn 858c9c54. H62 taught the render to name a code with nothing on EITHER side; a code with nothing on the ASKED side and something on the other was still named only inside "But there is INCOMING stock (ETA) ...", so the stock question came back as two codes' stock followed by an incoming fact about a third and nothing said the third had no stock. `crossdomain_render` now opens its block with "No {stock|incoming} for <codes>.", worded from `origin_domain` like its both-empty sibling, and the block is appended under the primary answer so the line is the stock section's last word. Built here rather than in the stock composer because this is the only place that knows both that the primary render did not echo the code and that the other domain was probed for it; guarded by the same `can_state_absence` test, so a warehouse breakdown or a demand verdict never gets "no stock" printed under the stock it just showed. Six graded captures move on `_xdBlock.block` alone, registered field-scoped, with the ETA-sort capture's block graded explicitly minus the one new sentence so that test does not go vacuous. Evidence: `tests/chatbot/test_s6c_answer_lane.py::TestAZeroStockCodeIsNamedBeforeTheIncomingBlock`. |
| H73 | the team menu fired on a request that named no team | **fixed (AC-821, this PR)** - prod turns 1f0428cb / 9089ef88 (execs 15501799 / 15502378), a regression #706 introduced. H64's premise was INHERITANCE ("this turn named no team, its domain derives none, and the team it would assign to is the previous turn's"), and it could not tell its own case from the one it broke: "escalate to marketing" and "I want to talk to a human" both reach the lane with a null parser team over the same carried `purchasing`, and D11 forbids reading the two messages to tell them apart. So the premise is DELETED and the discriminator moves to the parser, which is where reading a message is the job: `routing.suggested_team` now carries the customer's own team word verbatim when it names several catalogue teams or none, and null means "named none", and a turn that named none keeps THE ROUTING CHAIN'S OWN RESULT - the carried team when a previous turn had one, else the table default (corrected after the review of #713 B3, which measured `purchasing` where this row had said "the routing table default"; the CODE stands by captain's ruling, because the carried team is the pre-#706 chain and live parity and the owner's complaint was the MENU). The lane narrows that word against `SUGGESTED_TEAMS` (`_catalogue_teams`, inventoried under D11): one member assigns, several ask over exactly those, none asks over the vocabulary. D1's open-offer premise and the acceptance short-circuit are untouched. The prompt half is a REGISTRY change, not a code one - v1 (the live body) holds the `production` label, migration `480_chatbot_parser_team_word` publishes the amended slim text unlabelled, and the owner promotes; until then production parses a null team for "escalate to marketing" and the lane assigns the default, which is the pre-#706 behaviour and not the regression. No capture can show any of this (n8n has no team clarify at all), so nothing in the replay corpus moves; the two H64 unit stubs that said null for "escalate to marketing" now say "marketing", which is the model's job under the amended contract, and their assertions are unchanged. Evidence: `tests/chatbot/test_pass4_item5_no_team_named_keeps_default_routing.py`, `test_pass4_item1b_marketing_ambiguous_clarify.py`, `test_s5_escalation_lane.py::TestAnAcceptanceIsNeverAskedWhichTeam`. |
| H74 | a team_clarify answer was never consumed | **fixed (AC-822, this PR)** - turns 08e74db8 -> 0d7d5a23 (execs 15500464 / 15500487). The marker was WRITTEN by `compile_state` and read by nobody: the customer answered "marketing product", the parser got the team right and stamped `casual` for the bare noun phrase, and the routing chain's `req_help` ternary discards a team on any other message_type - so the chain fell to the stale `purchasing` carried from before the escalation was asked for and `is_low_signal` answered "Hi! How can I help you today?". `message_type` is the wrong question for a reply to a question WE asked, so while the clarify is open this turn's own team wins whatever it says and the turn is promoted to `request_for_help`, which is `route.decide`'s escalation arm. A tap resolves too: the ask's teams are persisted on the marker as `{team, label}` (`team_clarify` becomes the third `PendingKind` written, which `tail/pending`'s docstring anticipated - R-a narrows the ask, so `selection_context` alone no longer says WHICH teams were offered), and the reply is compared to them by EQUALITY, inventoried under D11. Lifetime measured, not assumed: `_offer_carry` needs a non-empty roster and a team clarify has none, so the ask expires on the next turn either way and the member offer's 3-turn TTL is NOT copied. Evidence: `tests/chatbot/test_pass4_item1a_team_clarify_consumed.py`. |
| H75 | a date-only narrowing of an open member offer lost the offer's scope | **fixed (AC-823, this PR)** - turns 0eef1cc3 -> 48ee6081, live chain 15503158 -> 15503189. AC-818 fixed the ROUTING half (the turn is no longer `low_signal`) and the turn still refused with "I need at least one filter", because the scope it would have narrowed was gone. Measured, not inferred: the two obvious suspects are both fine on the capture (the window survives, the flag is stamped), the entity-op executor keeps `hanlim` + `srtwc286` correctly, and `if message_type == 'casual' and not engages_offer: entities = []` (~:2172) wipes them 400 lines above the filter-modification arm whose own comment promises "the window and the ENTITY are kept". That line cannot tell "last month" from a bare "hi", because whether this is a filter modification of an open offer is decided downstream of it - so the arm that knows restores the offer's scope, only when the turn brought none of its own. One new diagnostic key (`member_offer_scope_reused`), registered field-scoped with the three of its class. Evidence: `tests/chatbot/test_pass4_item3_last_month_under_member_offer.py`. |
| H76 | a numbered pick over a partial-miss roster dropped the code that had resolved | **fixed (AC-824 / issue #708, this PR)** - a partial product miss is claimed by `build-suggest-offer` before the tail runs, so the turn persists `selection_context: suggest_offer` and never writes `dym_last_result_set`, which is what the numbered did-you-mean handler keys on. The pick therefore fell to the generic positional arm, whose contract is replacement, and the answer covered the pick alone. `apply_dym_pick` keeps its roster key and the positional arm hands it the other roster, using the `for_raw` linkage `dym_offer.candidates` already persists. Two gates keep the corpus byte-equal AND the semantics honest: the linkage must land on a prior entity (capture `parser-15157067` has a separator mismatch, so it does not, and does not move), and the prior scope must hold something that is not a source token (a FULL miss has nothing to preserve, and its `entity_op: reuse` arm is graded by `test_r3_pending_end_to_end.py`). One new diagnostic key, registered field-scoped. Evidence: `tests/chatbot/test_pass4_item4_issue708_partial_pick_scope.py`. |
| H77 | a picked customer was re-resolved from a debtor code 99 accounts share | **fixed (AC-825, console pass 5, 7 Sep 2026)** - turns 5bb0426e -> cc0075ae / 0d9332a0. Measured against the local prod-copy DB: the date window survives every stage and the resolver mis-ranks nothing (`gate_passed: true`, `require_specific: false`, full coverage), so neither first-pass hypothesis holds. `resolve_entity_body` sends the carried entity's `canonical_code` as the token and omits `entity_pins`, because the resolver route refuses a pin in AND mode by design (`references.py` ~:1652; PR #456 is OR-mode only) - so the uuid the pick IS gets thrown away and a code 99 production rows share is re-resolved, returning 15 other accounts without the picked one. The gate then passes them: `cust_pinned` suppresses the "which company" ask, so the pin stops the QUESTION without constraining the ANSWER. Fixed at `gate.py`, not at the resolver route (AND-mode `entity_pins` stays refused, PR #456's own contract, unchanged): the pre-existing "A PINNED PICK WINS OVER FUZZY RE-RESOLUTION" mechanism already re-seated a carried pick's uuid (exec 13705266's fix widened its ENTRY gate to `pin_uuids_all`, carried-or-current) but its FILTER half - `pin_types` / `pin_bases` / `pin_codes` and `_keep`'s own uuid check - stayed built from `pins` / `pin_uuids` (this-turn only), so a carried pick with no current-turn pin left `pin_types` empty and every resolver row read as "untouched". Widened all four to `pins_all` / `pin_uuids_all`; the resolver's own wrong rows for the shared debtor code are now REPLACED by the picked uuid, never merged with it. The unit test is a plain (non-xfail) test against `run_gate` directly, exercising the fix's own layer. Evidence: `tests/chatbot/test_pass4_item2_last_month_keeps_customer_scope.py::TestACarriedCustomerPickIsPinnedAtTheGateNotAtTheResolver`. |
| H78 | a migration seeding a new `attachment_types` row broke "photo" resolution | **fixed (AC-827, console pass 5, 7 Sep 2026; corrected on review round 1, blockers B1/B2)** - prod turns a5317cf4 / b4369aba, three minutes after control turn 008676fc sent the file clean. The deploy that shipped #713 (043e2a0be) also carried #707's migration `485_shipment_line_photo_type.py`, seeding "Shipment Line Photo" alongside the pre-existing "Product Photos" row; both contain the substring "photo", so Tier 2's substring probe now returns two candidates where it used to return one. `gate.py`'s own non-product ambiguity handling has no per-type narrowing and silently picked `non_products[0]`, order-dependent. #713's own diff never touches attachment-type resolution, so the migration is the whole cause. Fixed by DOMAIN-SCOPING at the resolver rather than touching the shared non-product ambiguity handler (every other entity type funnels through it too - a much wider, unmeasured blast radius): `entity_resolver._product_attachment_type_ids` restricts Tier 2 `attachment_type` candidates, only when `domain_hint == "product_attachment"`, to types a PRODUCT actually carries a file of. **First cut was wrong, caught on review**: it filtered `attachments.entity_type == 'product'`, and PRODUCTION HAS ZERO SUCH ROWS (a product's file links via `product_attachments`, never via `attachments.entity_type` - measured: 4302 NULL / 77 `dealer_kit_asset` / 41 `stock_list` / 9 `supplier_stock_list` / 1 `project`, zero `product`), so the frozenset the first cut returned was EMPTY in production and "photo" stayed exactly as ambiguous as before the fix - a green test suite that changed nothing live. Corrected to join through `product_attachments`, which returns the real four types (2108 Product Photos / 1033 Technical Specifications / 569 Certification / 1 Promotion). Tier 1 exact stays untouched. **Honest limit, not claimed away**: those four types can still collide on a substring - "spec" or "cert" plausibly matches two of the four - and `gate.py`'s silent `non_products[0]` pick is UNCHANGED for that residual case; this fix narrows the candidate pool, it does not make the pick deterministic for every possible word. The `send_attachments` reds were also mis-diagnosed on the first pass: the test's own `tool_search` stub returned `[]`, which makes `run_fetch` return `not_found` before `mcp_call` is ever reached (`lanes/business/__init__.py:289-292`) - `product_attachment` DOES have a fetch path (`crm_master_product_attachments_list`), and both tests now cross the real seam (`tool_search` faked, `mcp_call` the production binding with only `MCPRuntimeClient.call_tool` monkeypatched to the STRING shape it returns) and pass for real; issue #727 (opened for a "missing zero-tool fetch") is CLOSED as invalid. Evidence: `tests/chatbot/test_pass5_item1_photo_attachment_alias.py`. |
| H79 | a "last month" that set a concrete date was wiped by its own `broaden_axis` | **fixed (AC-828, console pass 5, 7 Sep 2026)** - prod turns e4381b0d / 98526b81 (member_offer, business_query, `date_filter_start/end` set by the SAME parser output that also carries `broaden_axis: "date"`). `head/output_exchange.py`'s `entity_op == "reuse"` arm treats `broaden_axis == "date"` as an unconditional "drop the window" signal and nulls `date_filter_start` / `date_filter_end` / `date_mode` before ever consulting `has_current_date` (computed one line above, read only by the sibling `elif`), so a turn that named `broaden_axis: "date"` AND supplied a real date was wiped exactly like a turn that said "all dates" and supplied none. Pre-existing, not part of #713's diff (confirmed by line range). Fixed by gating the wipe on `not has_current_date` too - a turn that supplies a concrete date this turn keeps it, whatever `broaden_axis` says. Not fixture-visible: no captured `output_exchange` fixture anywhere in the corpus carries `broaden_axis: "date"` at all. Evidence: `tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::TestB1LastMonthUnderMemberOfferKeepsTheParsersOwnDateFilter`. |
| H80 | a bare product code under an open member offer had no narrowing mechanism | **fixed (AC-829, console pass 5, 7 Sep 2026, D11 inventory row R-b; corrected on review round 1, blockers B3/B4)** - prod turn 6ea9fd1a: "rpacc" after a working "last month" (offer hanlim/srtwc286) is never extracted as an entity by the parser at all (`entities: []`, `message_type: "casual"`), so nothing downstream of the parser could invent a product entity for it, and the reply kept naming the OLD product ("Product: srtwc286") instead of narrowing to the new one. **First cut was wrong, caught on review**: it read the bare reply against `prev_state.routing_companies[].codes` for a matching SEGMENT and invented a code shape ("SRTWC286-SH-RPACC") to make its own test pass - measured against the REAL vendored 6ea9fd1a capture, no code in its `routing_companies` contains "rpacc" anywhere. Production's real shape: TWO products are coded literally `RPACC` (cross-company duplicates, same as `srtwc286` itself), a shape only the RESOLVER can tell apart from junk text. Fixed per #698's own rule, restated for a zero-entity bare reply: `resolve_gate.resolve_bare_reply_under_member_offer` (between the resolver seam and the gate - `output_exchange` itself has no resolver access) sends the whole bare message to the resolver as one token, `allowed_entity_types=["product","customer"]`, and on a same-code-collapsed unambiguous match REPLACES the matching half of the carried pair (`canonical_code` set to the RESOLVED code, never left null, so the MAIN resolve call that follows exact-matches it rather than re-searching the bare word freely across the catalogue); anything else changes nothing, so a genuine miss answers not-found off the unchanged carried pair, never `offer_hold` (B3's own regression guard). Also fixes the second-order defect the mechanism exposed: a narrowed entity's `current_message: True` left `message_type` at "casual", which routed the REST of the turn as an unanswered offer even though the query had just been answered - `message_type` now promotes to "business_query" the same way AC-816 rule 3 already does. Not fixture-visible: no `sub-resolve-and-gate` capture has the precondition shape. Evidence: `tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::TestB2ABareProductCodeUnderTheOfferNarrowsTheProduct`. |

## Non-goals

- One frontend surface only: the turn trace in Chat History (S2b, D13). Every other slice is
  backend-only and **skips Phase 1 (FE mock) by declaration**; those PR descriptions say so.
- No parser prompt changes in S1 beyond reading the `pending` marker. Parity first; S1b then
  slims the prompt under D16 with the corpus as the regression net.
- No CRM-side WhatsApp sending (D9). The primitive exists; not wired.
- No change to `live-respond-send-user`, `live-respond-close-convo`, the `ht-*` sweeper (R6).
- No multi-tenant resolution work: the module reads `DEFAULT_TENANT_ID` like every other module
  until tenant resolution lands.

## Rollout and rollback

Per slice: PR green (unit + replay + endpoint), clone smoke green on the n8n test clone
against the lane's backend, owner promotes the n8n edit, deploy CRM first then n8n (the CRM
endpoint must exist before n8n calls it; the old nodes still work until re-wired). Rollback =
re-wire n8n to the disabled nodes; CRM endpoint stays, harmless. S7 rolls out on one pilot
contact via the ingress workflow's contact filter, then all.

## Risks

- **Parser schema strictness.** n8n's agent node returns free JSON; the port validates a strict
  27-key schema. A model that occasionally emits an extra key would fail validation where n8n
  tolerated it. Mitigation: the live-parity script before S1 promote; `ParseOutput` ignores
  unknown keys but rejects missing required ones.
- **Fixture staleness.** The corpus was captured against exports dated 28 Aug to 2 Sep. Verify
  exports (`export-workflows.py --verify`) before vendoring the subset; re-capture the thin
  branches (`low_signal`, `ideate`) with the harness before S3 / S4.
- **Synchronous turn latency.** The parser call runs in the API request (2 to 5 s), same as
  the ideation turn today, and the full turn adds MCP calls (up to 5) from S6. Uvicorn's
  threadpool absorbs this at current volume (about 2,200 turns per capture window); the
  worker-offload flag exists for the day it does not.
- **Two writers for `ideation`.** Already true today (n8n nests, CRM writes flat). S2 makes the
  CRM the only writer and normalises to flat.
- **`sub-media-intake` stays in n8n** and still calls the CRM media endpoint. Fine, it is
  transport by D1, but the audio-patch dependency (H5) is now an explicit contract in AC-107.

## Backlog (written to `documentation/backlogs/backlog.md` at ticketing)

Turn inbox UI; CRM-side sending; human-intervened sweeper port (R6); H8 / H9 / H47 n8n
outbound fixes; H17 to H25 resolver and MCP data bugs as separate issues; H55 scope
continuity; parser prompt items H41 / H48; retention job for `chatbot_turns`.
