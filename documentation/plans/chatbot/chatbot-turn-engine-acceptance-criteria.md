# UAC - Chatbot Turn Engine (n8n business logic moves into the CRM)

Plan: `documentation/plans/chatbot/PLAN-chatbot-turn-engine.md`
Predecessors: `documentation/plans/_archive/ideation/PLAN-ideation-ideate-intent.md` (the one lane that
already runs this way), n8n repo `sorento_crm_n8n/n8n-workflows-init/plans/spine-decomposition-plan.md`
(RS-0 to RS-9, the decomposition this port consumes).

## Journey

Four actors. The customer's journey is the contract that must NOT change. The other three are
the reason the work exists.

### A. Customer on WhatsApp (behaviour byte-identical to today)

Actor: a dealer or end user messaging Sorento on WhatsApp, arriving from respond.io.

1. They send a message (text, voice, photo, or a numbered reply to a list the bot showed).
2. Within the same few seconds as today, the bot answers: a product / stock / order / promotion /
   incoming / document answer, a numbered picker, a tier question, a did-you-mean offer, a
   clarification, an escalation offer, or a canned refusal. The wording, the numbering, the quick
   replies and the attachments are the ones today's n8n graph produces.
3. A bare digit or a tier word on the next turn resolves against the SAME list the bot showed,
   because the bot remembered what it offered.
4. Accepting an escalation assigns a real staff member, posts the PIC comment, and starts the SLA
   clock, exactly as today. Declining says "Escalation declined." and stops.
5. Two messages sent quickly are answered in the order sent, never interleaved.
6. If the bot fails mid-turn, the customer gets today's error reply, and the failed turn is now
   RECORDED, not dropped (the only visible delta, and it is invisible to the customer).

Derived, never asked: nothing changes for the customer. No new question, no new menu.

### B. Owner edits bot copy without touching n8n

Actor: the owner, in Settings > AI Prompts (existing screen).

1. They open the prompt registry and see new keys for the bot's canned replies (clarify menu,
   not-supported, escalation offer, demand quantity, access denied) and the two bot prompts
   (semantic parser, small-talk clarifier).
2. They edit the not-supported reply, publish it. The next WhatsApp turn uses it. No n8n change,
   no deploy.

### C. Developer changes bot behaviour

Actor: a developer (or Claude) asked to change a routing rule or a reply.

1. They open one Python package (`app/services/chatbot/`), find the rule by name (`route.py`,
   `compile_state.py`, ...), edit it.
2. They run `pytest tests/chatbot -q`. The replay suite (real captured n8n executions) tells them
   in under a minute which turns changed behaviour and how.
3. They open a PR. CI runs the same suite. Review, merge, deploy. n8n is not opened.

Derived, never asked: which n8n node to edit, which by-name reads it breaks, which sub-workflow
fork to promote. All gone.

### D. Operator troubleshooting a turn (owner ask, 4 Sep review: "human friendly, simple")

Actor: the owner or a technical staff member, in System > Chat History (existing page), after a
customer says "the bot did not answer" or "the bot answered wrongly".

1. They open the contact's thread. Every incoming message now carries a **Turn** line under it:
   a status word (Answered, Escalated, Asked to clarify, Failed at understanding), the lane in
   plain words ("Business query: product"), and the total time. Failed turns are red and the
   list can be filtered to "Failed turns only".
2. They expand the turn and read a timeline in sentences, one row per stage: Received,
   Understood, Access, Routed, Looked up, Replied, Remembered, Sent. Each row says what
   happened in words ("Understood as a business query about product SRTWC8517, dealer tier"),
   why ("Routed to the business lane because access is allowed and no escalation was asked"),
   and how long it took.
3. A failed stage shows the reason in one sentence, a Retry button (when R4 allows), and a
   "Technical details" collapsible with the raw payloads for the engineer, searchable, the same
   viewer the AI-assistant trace already uses.
4. "Remembered" shows what the bot will carry into the next turn (kept, new, cleared), so a
   wrong follow-up answer can be traced to the memory, not guessed.

Derived, never asked: nothing to configure. The trace is written by the engine on every turn.

## Decisions (locked with the owner, 4 Sep 2026)

- D1 **Cut line = transport only.** n8n keeps: respond.io webhook in, contact lookup, media
  fetch + media intake (the existing `sub-media-intake`, already CRM-backed), respond.io send,
  conversation assign, PIC comment, contact-field update. Everything between the inbound envelope
  and the outbound actions is the CRM.
- D2 **State owner moves with logic.** The CRM reads and writes `respond_contacts.session_vars`
  itself. n8n's `save-session-vars` PUT is deleted at S2 and never returns. One writer.
- D3 **Module, not core.** `chatbot` is an installable module (`MODULE_KEY = "chatbot"`,
  `require_module_enabled_with_api_key("chatbot")`), one package, one public entry point. Core
  never imports it. Per-client behaviour = per-tenant data, never code branches. Not a separate
  service now; the lift trigger is named in the plan.
- D4 **Synchronous turn, caller sends** (revised at review round 3, owner: "can't we just
  return the data and n8n process?"). `POST /chat/turn` runs the whole turn and returns
  `{reply, actions}`; the CALLER executes them. Egress therefore stays with whoever called:
  the live spine sends, the fail-closed test clone (`Hnd4S8SVH6pftjxs`) records to
  `test:egress` and never sends. No outbound webhook, no callback URL. n8n already waits the
  whole turn today (`call-spine` `waitForSubWorkflow: true`), so this is no slower for n8n.
  Worker offload is OPTIONAL: the request runs the turn in-process like the ideation endpoint;
  if p95 turn latency or API CPU crosses the named trigger, the request enqueues on the `chat`
  queue and waits for the result (the `/external/media` enqueue-and-wait pattern). Not built
  until measured.
- D5 **Configurable = existing stores, not a rule engine.** Canned copy and the two prompts live
  in the prompt registry (versioned, has an admin UI). The respond.io `space_id` (hard-coded
  `364817` in n8n) comes from the default respond workspace row. One new `system_settings` column:
  `chatbot_unsupported_domains`. Nothing else gets a knob until the owner changes it twice.
- D6 **Ideation is an MCP tool** (owner, twice: "ideation should be at the MCP side"). A new
  write tool `crm_ideation_turn` in `sorento_crm_mcp/catalog.py` wraps
  `POST /external/ideation/turn`, is synced into `mcp_tools`, and the engine's `ideate` branch
  calls it through the same in-process MCP client as every other tool (D10). Ideation is not
  special-cased anywhere: not an n8n lane, not a direct service call. The n8n
  `ideate-turn-http` node is deleted at S3.
- D7 **Strangler by turn stage, then by lane.** Head (parse + route) first, tail (compose +
  persist) second, then lanes one at a time, ingress/dispatch last. Every slice leaves production
  working and is independently revertible.

  **The control is data, not deploy** (added S4). `contracts.CRM_COMPLETED_BRANCH_KINDS` says
  which lanes the BUILD can complete; `system_settings.chatbot_completed_lanes` says which the
  owner has switched ON, default `[]`, and a turn is completed in the CRM only when its
  `branch_kind` is in both. So each lane is four separately reversible steps - deploy inert,
  shadow, add the kind to the list, delete the n8n Switch output - instead of one deploy that
  has to be matched by an n8n edit in the same window or the lane runs twice. Rollback before
  the n8n cut is removing the kind from the list: a settings edit, effective on the next turn,
  no deploy. After the cut it is an n8n restore first, which is why the cut comes last.
  Enabling a kind the build cannot complete does nothing (AC-308).
- D8 **Parity before improvement.** Each ported function is proven against the captured n8n
  fixtures BEFORE any hazard is fixed. A fix is a separate, named, tested divergence.
- D9 **n8n sends.** The CRM can send WhatsApp today (`send_text_or_template`), but the owner
  wants sending to stay in n8n. The engine returns `actions[]`; the caller executes them.
- D15 **Ingress-agnostic and idempotent per message** (round 5, owner: the respond.io webhook
  fallback poller). Both injectors (`sorento-main` webhook producer and the failover poller's
  `sorento-main-INJECT`) post the same envelope shape; the engine never knows or cares which.
  `chatbot.turns` is unique on `(contact_respond_id, message_id)`; a duplicate delivery
  returns 200 `{duplicate: true}` with the original turn's reply and actions and the caller
  sends nothing. The envelope carries `ingress: webhook | poller | retry | console`.
- D16 **LLM for language understanding and reasoning only** (owner, round 6). The parser prompt
  carries no deterministic logic: no arithmetic, no enum-to-team mapping, no date maths, no
  positional resolution rules, no state carry rules. Those live in `output_exchange.py` and the
  lanes (D11's other half). S1 ships parity first, then S1b slims the prompt by moving every
  rule that is not language understanding into code, proven by the same replay corpus and the
  live parity script: same outputs, smaller prompt, no overfitting to sample phrasings.
- D14 **Dry run is a first-class input.** An envelope with `is_test = true` (or a
  `test_run_id`) makes the engine do ZERO writes: no session-vars write, no next-assignee, no
  SLA row, no chat-history ingest. The response carries the would-be `session_patch` and every
  action flagged `dry_run: true`. Console and clone turns are safe by construction, not by
  which URL they hit.
  **A dry run returns every action it WOULD have taken, in order, flagged `dry_run`, with
  preview placeholders where a side effect would have supplied the value** (ruling 5 Sep
  2026, agreed with the n8n executor; AC-507 is the contract). Returning a shorter list on a
  dry run would make it a different shape from the live one, and the executor renders one set
  of expressions against both. So the seams stay unreached and the values they would have
  returned are stood in for: an id becomes `null` with `preview: true` beside it, a rendered
  timestamp becomes `"<preview>"`. Anything that does NOT depend on a seam - a fixed sentence,
  or one interpolating state the turn already resolved - carries its real value either way.
  **A dry run is also an INPUT surface (O2, agreed with the n8n side 5 Sep 2026).** Zero
  writes is only half of what a harness needs; the other half is being able to say what the
  turn should start from. So a dry-run envelope may carry three optional keys and the engine
  honours them: `mock_reformulator_output` replaces the parser call entirely (post-processed
  through the same `output_exchange` path, so the harness exercises the code rather than
  bypassing it), and `previous_conversation_state` / `referenced_result_set` replace the
  stored memory for that turn only. On a LIVE envelope all three are ignored and named in the
  `received` record's `harness_keys_ignored`, because a harness envelope reaching a real
  customer must not answer them from a mock in silence. AC-112; the tests are named after the
  n8n guards they replace (G6, G8).
- D10 **MCP reads stay MCP-shaped.** The business lane calls the MCP server through the existing
  in-process `MCPRuntimeClient` (precedent: `ai_assistant_service`), so tool output keeps the
  presenter shape `output-structurer` was written against. No re-shaping.
- D11 **Semantic in, deterministic processing** (owner, 4 Sep review). Understanding the
  customer's text is the parser's job (LLM). Everything after the parser is deterministic and
  works on structured state, never on raw text: no regex or fuzzy match over the customer's
  message or over a previous reply. That is why the frozen string contracts (H13) become a
  persisted `pending` marker. Existing text-sniffing sites in the ported code are reproduced
  for parity, inventoried during S1, and listed as divergence candidates to move into the
  parser prompt after parity (see plan "Text-sniffing inventory").
- D12 **Own schema.** The module owns Postgres schema `chatbot` (`chatbot.turns`, cross-schema
  FKs to `public` where needed), per PRINCIPLES "dedicated schema for namespace and clean
  uninstall". `respond_contacts.session_vars` stays in `public` (shared with ideation).
- D13 **A turn trace screen is in scope.** Every turn writes a human-readable stage trace; the
  Chat History thread drawer shows it (journey D). FE mock first (Phase 1), then backend.

## Ruling R7 (owner, 5 Sep 2026): big-bang cutover

The live spine becomes the S7 shape in ONE dispatcher repoint (head, POST /chat/turn, action
executor; no delegate path, no /complete tail), deployed as a new n8n workflow. Rollback is the
repoint back to the previous spine, which never calls the CRM. This replaces the strangler
ladder's gate 2 (shadow compare on live traffic) and gate 3 (per-lane flips): every lane goes
live for customers at once. Prerequisites on the CRM side, all before the repoint and all safe
because nothing live calls /turn until then: S8a deployed (switches on the settings screen);
on prod every branch kind in chatbot_completed_lanes, chatbot_business_lane_enabled on,
chatbot_ordering_enabled on. Safety nets that remain: the 1,694-capture replay corpus, the
S6c/S7 end-to-end matrices, the AC-711 load gate, and the clone drives graded from
chatbot.turns. S2's /complete and the sub-output promote are never used on live; S8b deletes
them after the repoint.

## Rulings (plan review, 4 Sep 2026)

- R1 **RESOLVED**: port with the correct vocabulary (`check_stock`) and gate the
  `stock_denied` / `demand_qty` lanes behind `system_settings.chatbot_stock_denial_enabled`
  (boolean, default off). Turning them on is a data change with a test.
- R2 **RESOLVED (owner overruled the recommendation)**: keep every session key, including the
  nine with no reader today ("all these have purpose, don't drop"). `SessionVars` allowlist =
  every key `compile-current-state` writes. Nothing is dropped, at S2 or S8.
- R3 **RESOLVED by principle D11**: the two frozen string contracts become the persisted
  `pending` marker. S1 reader accepts both forms, S2 writes the marker, S8 removes the regex.
- R4 **RESOLVED (round 2)**: no automatic retry. A failed turn is recorded as `failed` with its
  stage and reason, the customer gets today's error reply, and the trace screen offers a manual
  Retry button (S2b). Fact recorded for the file: n8n does NOT retry today either; the
  dispatcher pops the message and drops it on error (`plans/concurrency-plan.md` locked
  decision 6, accepted risk 2). The only retries are node-level `retryOnFail` on two single HTTP
  calls.
- R5 **RESOLVED (round 2)**: "the parser shouldn't output invalid JSON". Enforced at the source:
  the parser call uses the provider's strict structured output (`json_schema` on
  `llm_provider.chat`, the same mechanism `ai_semantic_parser` uses), so a well-formed object
  is guaranteed whenever the provider answers. Anything else (provider error, timeout, refusal,
  or a validation failure that strict mode cannot prevent) is a failed `understood` stage:
  error reply, `failed` turn, visible on the trace screen. No soft default (H44 closed).
- R6 **RESOLVED**: `live-respond-send-user`, `live-respond-close-convo` and the
  human-intervened sweeper are out of scope; backlog item with its own plan.

## Acceptance criteria

Tags: `[BE]` backend code, `[T]` test, `[N8N]` n8n transport edit, `[E2E]` real turn on the test
clone or live. Each AC traces to a journey step (A1 to D1).

### S0 - Module scaffold, inbox, replay harness (journey C, D)

- AC-001 `[BE]` Given the backend, when it boots, then `app/modules/chatbot/bootstrap.py` declares
  `MODULE_KEY = "chatbot"`, `MODULE_MANIFEST` carries the `chatbot` entry with its dependencies,
  and `app_modules_catalog` is seeded with it on first run. (C1)
- AC-002 `[BE][T]` Given the package `app/services/chatbot/`, when any module outside
  `app/api/v1/external/chat.py`, `app/api/v1/system/chatbot.py`, `app/tasks/chat_turns.py`,
  `app/modules/chatbot/` or `tests/chatbot/` imports from it, then
  `tests/chatbot/test_import_boundary.py` fails naming the importer. (C1)

  `app/api/v1/system/chatbot.py` was added to the list at S2b (5 Sep 2026): AC-257 names it
  as where the trace endpoint lives, and its Retry calls the module's own retry seam. It is
  a chatbot-module surface mounted in a shared router, not core reaching in - the asymmetry
  the rule protects (core never imports the package) is unchanged.
- AC-003 `[BE]` Given the `chatbot` migration, when applied, then schema `chatbot` exists and
  table `chatbot.turns` exists with `id, contact_respond_id, envelope (jsonb), status
  (queued|processing|delegated|done|failed), stage, branch_kind, error, attempt (int, default
  1), message_id text, ingress text, trace (jsonb: ordered stage records `{stage, status, started_at, ms, summary, why,
  facts, error, raw}`), response (jsonb), created_at, started_at, finished_at`, indexed on
  `(contact_respond_id, status, created_at)` and UNIQUE on `(contact_respond_id, message_id)`
  (D15); the model pins
  `__table_args__ = {"schema": "chatbot"}` (D12). (D1, D13)

  `response` was added at implementation (4 Sep 2026) and is listed here so the written AC
  matches the migration. It holds the answer the turn returned, `{ctx, item, actions}`
  today and `{reply, actions}` from S3. D15 needs it - "a duplicate delivery returns the
  ORIGINAL reply" is not implementable without persisting one - and S2b's Retry reads the
  same column. **Bounded on purpose:** it is written when the turn CLOSES, so a duplicate
  that arrives while the first turn is still `processing` (the likely timing, since the two
  injectors are seconds apart and a turn takes seconds) replays `duplicate: true` with a
  null `ctx` and `status: processing`. Waiting for the first turn would buy nothing - the
  caller must not answer twice either way - so what guarantees no null is dereferenced is
  n8n's Switch on `duplicate` sitting BEFORE the `build-ctx` / `route-turn` re-emitters
  (AC-110, and the plan's S1 n8n section).
- AC-007 `[BE][T]` Given any stage of a turn, when it completes or fails, then the engine
  appends one trace record with a plain-language `summary` and `why` (no JSON in either), and
  `raw` holds the technical payload; a test asserts every stage name in
  `contracts.TurnStage` appears in a full happy-path trace. (D1)
- AC-004 `[T]` Given `CHATBOT_FIXTURES_DIR` points at the sibling n8n checkout
  (`sorento_crm_n8n/n8n-workflows-init/tests/fixtures`), when `pytest tests/chatbot` runs, then
  every fixture under `nodes/<slug>/<node>/` for a ported node is replayed through its Python
  port and `expected` is compared after a JSON round trip; when the directory is absent the
  corpus tests SKIP with a message and the vendored subset under `tests/fixtures/chatbot/` still
  runs. (C2)
- AC-005 `[T]` Given a ported node with an intentional divergence (a hazard fix), when its
  fixture disagrees, then the test passes only if the case is listed in
  `tests/chatbot/divergences.py` with the hazard id and a one-line reason; an unlisted
  divergence fails. (C2)
- AC-008 `[T]` Given a slice PR, when it opens, then `tests/chatbot/COVERAGE.md` (generated by
  `scripts/chatbot_fixture_coverage.py`) shows at least 5 real captures per branch of every
  node the slice ports; fresh captures come from the n8n repo's `scripts/capture-fixtures.py`
  recipe, never hand-written. The 1,535 starting corpus is a floor. `COVERAGE.md` is kept
  honest by `tests/chatbot/test_coverage_fresh.py`, which fails when it drifts from the
  corpus. (C2)

  **Two states are under the bar and do NOT block** (amended 4 Sep 2026, after the first
  capture run):

  - `exhausted (n)` - the branch has fewer than 5 real captures AND the capture agent's
    report says the live pool for the CURRENT workflow version was scanned end to end. The
    traffic does not exist, so no further capturing produces it and blocking would block
    forever. The scanned counts are recorded at the top of `COVERAGE.md` with their capture
    date and workflow version, so the claim is auditable rather than asserted: spine
    `51f7b0d2` 567 of 946 executions, parser `ab3ec985` 239 of 3,901 (the remainder in each
    pool ran on older versions and cannot be graded against the body the export ships).
  - `dead by vocabulary` - live cannot reach the branch at all. `demand_qty` and
    `stock_denied` are the two: the spine tests `intent_hint === 'stock_check'` while the
    parser emits `check_stock` (H1), so 0 captures is the CORRECT number, not a gap. These
    are covered by unit tests behind `chatbot_stock_denial_enabled` (AC-306, R1).

  A cell short in a pool that was NOT fully scanned still blocks, and the report says which.
- AC-009 `[T]` Given the world corpus, when S2 opens, then it holds 100+ worlds including
  multi-turn worlds (3 to 5 turns of one contact) covering picker, did-you-mean, tier ask,
  escalation, offer-hold and media shapes; each later slice tops up its own lanes. (C2)

  **Measured at S2, not S1** (amended 4 Sep 2026). The n8n repo has no worlds tooling and
  exactly ONE hand-built world (`worlds/exec-14855423-live-casual`), so "100+ by S1" was a
  target against a capability that does not exist. It does not need to: a spine capture
  already carries every node output of its execution in `ctx`, so a world is DERIVED from
  one rather than captured separately. The S2 harness builds them from the spine captures
  the S1 corpus already holds (116 route-turn / 114 build-ctx today), which is also when the
  tail exists to replay them end to end. S1's gate is node replay (AC-102, AC-103); world
  replay is S2's.

  **MET at S2 (5 Sep 2026), after the tail capture batch.** `tests/chatbot/worlds.py`
  derives **166 worlds** and **26 multi-turn chains** (114 turns) and
  `tests/chatbot/test_worlds.py` replays each through `run_turn` + `complete_turn` with
  the parser, the access check and the CS roster read stubbed from that execution's own
  node outputs. 102 single-turn worlds and 7 chains grade today (86 and 6 before S1b's
  live-body re-port landed, which retired 16 of the head-side body-difference skips); the
  rest carry a NAMED body difference or are spine-only captures whose resolver and entity
  gate ran inside a sub the fixture never recorded.

  A world is either GRADED or SKIPPED BY NAME. Nothing is partly excused: the only two
  allowed value differences anywhere are the `pending` marker and `dym_offer.id`, which
  is `$execution.id` becoming the CRM turn id.

  Per shape: `plain` 81, `escalation` 37, `did_you_mean` 25, `picker` 10, `tier_ask` 9,
  `media` 4, **`offer_hold` 0**. The last one is EXHAUSTED, not short: 0 of 556
  `route-turn` runs in the scanned pool took that arm, so no further capturing produces
  one and it is covered by unit tests instead. `media` is bounded the same way - the pool
  held 2 voice, 1 video and 8 already-handled images, so the head never runs on more.

  **The source changed, and that is why the number moved.** A world used to need seven
  named head nodes; it needs TWO - `build-ctx` and `crossdomain-compose` - because every
  input is a producer's output VERBATIM on the `build-ctx` hub, which is what that node
  is for. That is what lets the `sub-output-live` captures (the tail's own workflow,
  running the body the port implements) make worlds at all, and they are the best source
  in the corpus: the hub plus the thirteen trigger fields is a complete world by
  construction.

- AC-006 `[BE][T]` Given a caller with a valid integration key but without the slug
  `integration.chat_turn.submit`, when it calls any `/api/v1/external/chat/*` route, then 403
  `permission_denied` naming the slug; with the slug and the module disabled under strict mode,
  then 403 `Module not enabled: chatbot`. (C1)

### S1 - Head: parse + access + route (journey A2, A3, C1)

- AC-101 `[BE]` Given `POST /api/v1/external/chat/turn` with body `{ envelope }` (the redis queue
  item `A` shape: `{message: B, contact, test_run_id?, scope?, mode?}`), when the contact exists,
  then the engine (a) inserts a `chatbot_turns` row, (b) reads session vars (with
  `referenced_result_set` / `referenced_state` when `message.replyTo` is present), (c) runs the
  semantic parser, (d) runs the contact-to-agent access check (the service behind `POST /external/access-agent/check`), (e) routes, and responds
  `{ turn_id, ctx, item, branch_kind, delegate }` where `ctx` is the six-key hub shape and `item`
  is byte-equal to what `route-turn` emits today (tag-only arms get `{branch_kind}` only). (A2)
- AC-102 `[T]` Given every `route-turn` fixture in the corpus, when replayed through
  `chatbot.head.route.decide`, then `branch_kind` and the stamped item are equal to `expected`. (A2)
- AC-103 `[T]` Given every `output_exchange` and `suggest-follow-up` fixture (parser
  post-processing, input = `_parser_raw` + previous state), when replayed, then the derived
  `output` is equal to `expected`, including the 69 derived keys and the unicode-dash
  normalisation. (A2, A3)
- AC-104 `[BE]` Given the parser prompt, when the engine calls the LLM, then it renders prompt key
  `chatbot_semantic_parser` from the registry (fallback = the live n8n system message, verbatim),
  temperature 0, JSON output validated by a Pydantic model whose keys are exactly the 27 declared
  output keys, and the model/provider come from `ai_prompt_registry.agent_model` with the
  AI-assistant config as fallback. (A2)
- AC-105 `[BE][T]` Given the parser call, when it runs, then it passes the strict
  `ParseOutput` JSON schema to the provider (structured output, R5); given a provider error,
  timeout, or a response that still fails `ParseOutput` validation, when routing would run,
  then the turn is marked `failed` with `stage = understood`, the response carries
  `delegate = null` and `reply = <today's parser-error reply>`, and no default routing
  happens. (A6)
- AC-106 `[BE][T]` Given a session whose `response` string carries the legacy escalation-offer
  prefix OR whose `pending.kind == 'escalation_offer'`, when the parser post-processing computes
  `offeredEscalation`, then both forms are recognised (R3, migration window). (A4)
- AC-107 `[BE][T]` Given an envelope whose attachment type is still `audio` (media intake did
  not patch it), when the head runs, then the turn is `failed` at `stage = intake` with an
  explicit error and today's error reply, never a silent empty run (H5). (A6)
- AC-108 `[BE][T]` Given the contact's `is_human_intervened` custom field is true, when the head
  runs, then the response `actions` contains `{kind: update_contact_fields, fields:
  {is_human_intervened: false}}` and the turn continues, matching today's `set-human-intervened`
  path. (A1)
- AC-109 `[T]` Given every enum the parser emits (`message_type`, `intent_hint`, `domain_hint`,
  `suggested_team`, `suggested_agent`, `entities[].hint`, `selection_context`), when any Python
  module declares one, then it imports the single `Literal` in `contracts.py`; a contract test
  greps for duplicated string sets and fails on a second copy (H28). (C1)
- AC-110 `[N8N]` Given the live spine, when S1 is promoted, then the nodes `get-session-vars`,
  `Call 'sub-query-reformulator'`, `check-access`, `build-ctx`, `route-turn` are replaced by one
  `httpRequest` to `/chat/turn` plus two one-line Code nodes named `build-ctx` and `route-turn`
  that re-emit `response.ctx` and `response.item`, so every by-name reader downstream is
  unchanged; the old nodes stay in the workflow disabled for one release. (A2)
- AC-111 `[E2E]` Given the fail-closed test clone, when the 15-turn smoke set
  (`tests/uac/RS.md` clone smoke) runs against the S1 build, then every turn reaches the same
  lane and the same reply text as the pre-S1 run. (A2 to A4)
- AC-112 `[BE][T]` (O2, agreed with the n8n side 5 Sep 2026) Given a DRY-RUN envelope
  (`is_test`, a `test_run_id`, or `mode != live`), when it carries any of the three harness
  keys, then the engine honours them:
  - `mock_reformulator_output` (an object) REPLACES the parser call entirely - no provider
    is asked, `ctx.parse` becomes `{output: <the mock, post-processed>, _parser_raw: <the
    mock>}` through the SAME `output_exchange` post-processing a real parse takes, and the
    `understood` trace record reads "Parser bypassed by harness." with
    `facts.parser_bypassed = true`. A mock that is not a parser emission takes the
    malformed-answer path: a failed turn at `understood` (R5 / H44), never a soft default;
  - `previous_conversation_state` (the variables dict) and `referenced_result_set` REPLACE
    what the session read returned, for that turn only. Never written back - D14's
    zero-writes rule is unchanged and is what the test asserts.
  - **Membership decides, not truthiness, and the value IS the variables map.**
    `previous_conversation_state: {}` is an explicit override meaning "this contact
    remembers NOTHING" - a COLD turn - and it is a different instruction from not sending
    the key at all, which leaves the stored row in place. The value is assigned straight to
    `session_vars.variables`, so it is the variables map itself and never a wrapper around
    one. (`engine._harness_keys_present` at `engine.py:351`, `_inject_harness_session` at
    `engine.py:378-380`.) The console check spells the same thing as `cold: true` on a case
    (`scripts/chatbot_console_check.py`), which is what makes an owner-reported cold turn
    reproducible against a contact who has real stored state.

  And given a LIVE envelope, when it carries any of the three, then all three are IGNORED
  and the `received` record carries `facts.harness_keys_ignored` naming them in the declared
  order (an empty list when there are none, so absent is never confusable with unreported).
  A harness envelope that reaches a real customer must not answer them from a mock in
  silence. Named after the n8n guards they replace: **G6** the reformulator bypass, **G8**
  the session injection. (A5, D14)

### S1b - Parser prompt slim-down (journey A2, C1; D11, D16)

Runs after S1 parity is green and before S1 promotes, in the same lane.

- AC-151 `[T]` Given the 48 KB parser system message, when S1b starts, then
  `documentation/plans/chatbot/parser-prompt-inventory.md` classifies every section as
  `understanding` (stays), `rule` (moves to `output_exchange.py`), `example` (kept only if it
  covers a phrasing class the corpus shows, never a single sample), or `dead` (no fixture
  exercises it), with the fixture ids that justify each row. (D16)
- AC-152 `[BE][T]` Given a `rule` section (enum-to-team map, date maths, positional resolution,
  carry rules, quantity parsing), when it moves to code, then a unit test covers it and the
  prompt no longer mentions it; the combined parser + post-processor output equals `expected`
  on every parser fixture (parity unchanged). (D16)
- AC-153 `[T]` Given the slimmed prompt, when the live parity script runs on the
  regression-guard inputs plus a 200-turn fresh sample, then agreement with the pre-slim
  parser on the 27 declared keys is 99%+ and every disagreement is triaged as
  `improvement | regression`, with zero untriaged regressions before promote. (D16)
- AC-154 `[BE]` Given prompt size, when S1b lands, then the system message is at least 40%
  smaller by characters, the number is recorded in the PR, and the prompt is published as a
  new registry version (rollback = move the label). (B2, D16)
- AC-155 `[T]` Given an input in Malay or mixed Malay-English from the corpus, when parsed,
  then the slimmed prompt's output equals the pre-slim output (language coverage is an
  understanding property, not an example table). (A1, D11)

### S2 - Tail: outcome, compose, persist (journey A3, A4, D2)

- AC-201 `[BE]` Given `POST /api/v1/external/chat/turn/{turn_id}/complete` with body = the
  `sub-output` trigger contract (`item, result, resolved, gate, offer_hold, suggest_offer,
  not_found, incoming_picker, access_choice, crossdomain_render, answer, clarify`, all nullable
  except `item`), when called, then the engine runs build-outcome, the CS member offer (team
  members in-process), compile-current-state and crossdomain-compose, writes session vars
  in-process, marks the turn `done`, and responds `{ reply: {text, quick_replies, result_set,
  attachments_src}, actions: [] }`. (A2, A3)

  **A second route, same everything else** (5 Sep 2026, agreed with the n8n side):
  `POST /api/v1/external/chat/turn/complete` takes the SAME body and returns the SAME
  response, and identifies the turn from `(ctx.contact.id, ctx.text.message.messageId)` -
  the pair `chatbot.turns` is already UNIQUE on (D15) - taking the HIGHEST attempt. It
  requires `delegated`: 404 with a sentence when no row matches, 409
  `CHATBOT_TURN_NOT_DELEGATED` otherwise, both BEFORE the tail runs, and both writing an
  `integration_log` like every other call to this endpoint.

  **Three amendments, all agreed with the n8n side (5 Sep 2026):**

  - **`done` WITH a stored response is not refused**; it falls through to
    `complete_turn`'s replay branch and returns the answer already composed (D15's
    duplicate-delivery shape). 409 is for a turn with neither a lane result to fold in nor
    an answer to replay - `processing`, `failed`, or `done` with nothing stored.
  - **Both routes answer with `is_test`** (the ROW's, decided on the envelope at `/turn`),
    so the caller's test-guard logs what it recorded instead of sent without carrying the
    head's answer across two calls.
  - **A tail that RAISES is still a 200 answer**: `reply = {text: <today's error reply>,
    quick_replies: null, result_set: null, attachments_src: null}` AND
    `actions = [{kind: send_message, text: <the same>, quick_replies: null,
    result_set: null, dry_run: <the row's is_test>}]`, never a null reply or an empty
    action list - the executor executes `actions` and nothing else. The row is still
    `failed` at `remembered` with the reason on it.

  The reason is the n8n side's, and it keeps their cut inside one workflow: `sub-output`
  holds the `ctx` but not the turn id (the id lives on the spine, two workflows up), so
  the id-in-the-path form would have meant editing the spine, `sub-main-processing` and
  every `Call 'sub-output'*` caller to thread it down. `/turn/{turn_id}/complete` is
  UNCHANGED and still the form a retry from the trace screen uses, where the id is known.
- AC-202 `[T]` Given every `compile-current-state`, `crossdomain-compose`, `build-outcome`,
  `escalate-catalog`, `cs-roster-plan`, `build-cs-member-offer` fixture, when replayed, then
  `reply.text`, `reply.quick_replies` and `session_patch.variables` equal `expected`, the only
  registered divergence being the added `pending` marker (R3). (A3)

  **MET, with one divergence more than this line predicted** (5 Sep 2026): 1,321
  fixtures replay green across the six tail nodes and S1's four. The `pending`
  divergence is FIELD-scoped, not blanket - the named path comes off both sides and
  every other byte is still compared, because a whole-node exemption for one added key
  would make that node's replay vacuous forever. The second entry is **H29 on
  `b56-roster-turn`**: that capture RECORDS the defect AC-205 fixes (the turn persisted
  the previous turn's picker under a roster it had just replaced), so it cannot be green
  and correct at once. Six further captures are registered STALE rather than divergent -
  they predate the RS-9 Fix 6 `tier_menu` block, which is a `>`-only hunk in the body the
  export ships, so nothing about the port disagrees with what ships.
- AC-203 `[BE][T]` Given `SessionVars` (Pydantic, `extra = "forbid"`), when compile-state
  produces a key not on the allowlist, then it raises before any write; the allowlist is every
  key `compile-current-state` writes today (R2: nothing dropped) plus `pending` (H15, H22). (A3)
- AC-204 `[T]` Given the `dym_offer` lifecycle fixtures, when replayed, then the eight rules
  (replace / null / domain switch / escalation committed / pick applied / answered / ttl / decay)
  fire in that order, first match wins. (A3)
- AC-205 `[T]` Given a roster born this turn AND a carried picker, when compile-state runs, then
  the born roster wins (H29). (A3)
- AC-206 `[BE]` Given the tail wrote session vars, when the write happens, then it is one
  `overwrite_for_contact` call with `FOR UPDATE`, and an `integration_log` row is written with
  `business_table = respond_contacts.session_vars` as today. (D2)
- AC-207 `[N8N]` Given `sub-output`, when S2 is promoted, then its body from `item-restore` to
  `crossdomain-compose` and the `save-session-vars` PUT are replaced by one `httpRequest` to
  `/complete`; `sub-sendmsg` and `send-attachments` read `response.reply`. n8n no longer holds a
  session-vars writer anywhere on the turn path (grep of every exported workflow). (D2)
- AC-208 `[E2E]` Given a multi-turn clone conversation (picker, digit reply, escalation offer,
  decline), when run on the S2 build, then each turn's persisted `session_vars` equals the
  pre-S2 run's except the R2 / R3 deltas. (A3, A4)

### S2b - Turn trace in Chat History (journey D1 to D4)

Phase 1 = FE against a mock (this is the one FE surface of the program); Phase 2 = the read
endpoint over `chatbot.turns.trace`. Lands after S2 so the head and tail both write stages.

- AC-251 `[FE]` Given the Chat History thread drawer, when an incoming message has a turn,
  then a **Turn** line renders under it: status word (Answered / Escalated / Asked to clarify /
  Failed at <stage>), lane in words, total time, attempt count when > 1; failed turns use the
  destructive tone. (D1)
- AC-252 `[FE]` Given the Turn line, when expanded, then a vertical stage timeline renders:
  Received, Understood, Access, Routed, Looked up, Replied, Remembered, Sent; each row has a
  status icon, `summary` and `why` sentences, and duration.

  A stage a lane never runs (a clarify ask looks nothing up) is OMITTED, never greyed. A
  stage a FAILURE stopped is different and collapses into ONE "not reached" row naming them
  together, per the approved mockup (review page section 10): on a turn that failed at stage
  two, "Routed, Looked up, Replied and Remembered did not run, memory unchanged" is the
  operator's next question, and eight greyed placeholders would bury it. (D2)
- AC-253 `[FE]` Given a failed stage, when rendered, then the row shows the reason sentence, a
  Retry button (enabled when the turn is `failed`; the only retry path, R4), and a "Technical
  details" collapsible using the existing `SearchableCode` viewer over `raw`. (D3)
- AC-254 `[FE]` Given the Remembered stage, when expanded, then it lists Kept / New / Cleared
  memory keys in words (labels, not key names, with the raw key in a tooltip). (D4)
- AC-255 `[FE]` Given the Chat History list, when the filter "Failed turns only" is on, then only
  contacts with a `failed` turn in the range are listed, and the row shows the last failed stage.
  Backed by `GET /api/v1/system/chatbot/turns/failed-contacts?from=&to=` ->
  `{items: [{contact_respond_id, last_failed_stage, last_failed_at, count}]}`: the question is
  "which contacts are worth opening", which is an aggregate over tens of rows, not a page of
  every turn grouped in the browser.

  The list then asks `GET /api/v1/system/chat-history` for those contacts' messages by
  REPEATING `contact_id`, one value per contact named by the aggregate: several ids narrow
  to those ids, one id narrows to it, an explicit blank value matches NOBODY (an empty
  filter is a filter, not an absent one) and an absent param filters nothing. The page and
  its total are therefore the filtered ones - narrowing in the browser instead left the
  pager counting rows it had just hidden. (D1)
- AC-256 `[FE]` Usable and non-clipped at 375px and 1280px; the timeline stacks, the drawer
  scrolls, no horizontal page scroll. (D2)
- AC-257 `[BE]` Given `GET /api/v1/system/chatbot/turns?contact_respond_id=&from=&to=&status=`,
  when called with `system.chat_history.view`, then it returns the turn rows with `trace` and
  `response`, newest first, paged by `limit` (default 50, max 200) and an opaque keyset
  `cursor` echoed back as `next_cursor` (null on the last page); an unknown `status` is 422,
  not an empty page. `POST /api/v1/system/chatbot/turns/{id}/retry` re-injects a `failed` turn
  (403 without `system.chat_history.manage`; 409 unless `failed`; 409 `retry_unavailable` when
  no ingress is configured, having sent nothing; 409 when a retry is IN FLIGHT, meaning
  `chatbot.turns.retry_requested_at` is set AND younger than `chatbot_retry_stale_minutes`
  (setting, default 5), so a double click cannot answer the customer twice; 502 when the
  ingress refuses, leaving the row unchanged). A marker OLDER than that window is stale and
  the turn may be retried again: the marker is cleared by the re-injected turn arriving, and
  one that never arrives (n8n dropped it, the contact was deleted, the workflow was
  mid-deploy) must not leave the row un-retryable forever. The claim is a conditional
  `UPDATE ... WHERE retry_requested_at IS NULL OR retry_requested_at < <stale cutoff>` whose
  rowcount decides, so two simultaneous POSTs cannot both pass the check and both inject.

  `GET .../turns` carries `retry_available` and `retry_unavailable_reason` on the LIST
  response, not per row: whether this environment has an ingress at all is a property of the
  deployment, and the screen needs it to disable the button rather than offer a 409 the
  operator only discovers by pressing it. On success the row STAYS `failed` and gains
  `retry_requested_at`; the response is `{turn_id, attempt}` where `attempt` is what the
  re-injected turn will carry. (D1, D3)
- AC-258 `[T]` vitest for the timeline (happy, failed, delegated, retry disabled) and the
  Kept/New/Cleared derivation; pytest for the list filters and the retry guards. (D1 to D4)
- AC-259 `[E2E]` agent-browser: from `/`, System > Chat History, open a thread, expand a turn,
  read the Understood row, open Technical details; screenshot at 375 and 1280. (D1 to D3)
- AC-260 `[BE]` Given a turn left `delegated` because the n8n lane that took it over never
  called `/complete` (workflow error, worker redeploy, execution deleted), when the sweep runs
  (every minute, from the existing APScheduler registration in
  `app/scheduler/task_scheduler.py`), then every `delegated` row whose `started_at` is older
  than `CHATBOT_DELEGATED_TTL_MINUTES` (setting, default 10) becomes `failed` with
  `stage = "delegated"`, `error = "n8n lane did not complete within N minutes"` and a trace
  note explaining it. Test turns included; running it twice changes nothing; a fresh
  `delegated` row and any `done` / `failed` row are untouched. Without this the trace list
  fills with ghosts that read as work in progress, and Retry cannot reach them at all because
  R4 makes a manual retry possible on a FAILED turn only. (D1, D3)

### S3 - Canned lanes, ideation, offer-hold inside the head (journey A2, B2, D6)

- AC-301 `[BE]` Given `branch_kind` in `{access_denied, escalate_offer, escalation_declined,
  clarify_menu, not_supported, demand_qty, offer_hold, ideate}` **and the lane is enabled in
  `system_settings.chatbot_completed_lanes`**, when `/chat/turn` runs, then it completes the turn
  itself and responds `delegate = null` with `reply` and `actions`; n8n sends and does nothing
  else. With the lane NOT enabled (the default, `[]`) the same turn responds
  `delegate = <branch_kind>` and n8n runs the lane exactly as today, so a lane can be deployed,
  compared and switched on as three separate steps. (A2)
- AC-308 `[BE][T]` Given `system_settings.chatbot_completed_lanes`, when the engine decides
  whether to complete a turn, then BOTH conditions are required: the kind is in
  `contracts.CRM_COMPLETED_BRANCH_KINDS` (what this build can do) AND in the settings list
  (what the owner has switched on). Enabling a kind the build cannot complete does nothing; an
  unknown or malformed entry is ignored with a warning and never fails the turn; the row is read
  once per turn on the session routing already holds. (A2, D5)
- AC-302 `[BE]` Given the canned copy, when rendered, then each string comes from a prompt
  registry key (`chatbot_reply_access_denied`, `chatbot_reply_clarify_menu`,
  `chatbot_reply_not_supported`, `chatbot_reply_demand_qty`, `chatbot_reply_escalate_offer`,
  `chatbot_reply_escalation_declined`, `chatbot_reply_offer_hold`) whose fallback is today's text
  verbatim, with `{{team}}`, `{{user_goal}}`, `{{companies}}` variables. (B1, B2)

  **Landed early, at S2, because the catalog IS the tail** (5 Sep 2026), with the key
  list amended against what `escalate-catalog.js` actually holds:

  - **Eight keys ship**, not seven. `escalate_offer` and `out_of_scope` each have a
    with-team AND a no-team SENTENCE in the JS ("escalate to X team?" vs "escalate this
    to our team?"), so each ships as a pair (`*_no_team`); substituting an empty
    `{{team}}` would send "escalate to  team?" to a customer. `chatbot_reply_out_of_scope`
    is added because the catalog has that arm and this list did not name it.
  - **Two of the seven named keys are NOT registered here.** `access_denied` has no
    `escalate-catalog` case at all (it falls through the switch to an empty response, and
    the port reproduces that), and `offer_hold`'s text is COMPUTED upstream by
    `offer-hold-reply` rather than canned. Registering copy for either would be inventing
    behaviour, so they land with their lanes at S3 and S5.
  - `{{companies}}` is not used by any catalog template: the multi-company sentence is
    built by `build-cs-member-offer` from the roster plan, not interpolated into canned
    copy. Only `{{team}}` and `{{user_goal}}` are declared.
  - Migration `476_chatbot_reply_copy` seeds them; the engine falls back to the same
    strings when a row is missing or the DB is unreachable, so the bot answers either way.
  - **The last two land at S3 with their lanes** (5 Sep 2026), as this note said they
    would: `chatbot_reply_access_denied` (the send node's own expression, `{{team}}` being
    the parser's `suggested_agent` with em-dashes folded to hyphens) and
    `chatbot_reply_offer_hold` (+ `_no_companies`, because a pool with no names gets a
    DIFFERENT clause rather than an empty parenthetical). Seeded by migration
    `478_chatbot_s3_copy` (S4's `477_chatbot_lanes` seeds no copy; it adds the
    `chatbot_completed_lanes` switch, and 478 chains onto it). Eleven keys now, and
    `{{companies}}` is finally used - by the offer-hold clause, which is the only canned
    string that names a pool.
- AC-303 `[BE][T]` Given `domain_hint == 'ideate'`, when the head runs, then the engine calls
  MCP tool `crm_ideation_turn` (via `MCPRuntimeClient`) with the same arguments
  `ideate-turn-http` sends today (including `media_selection` derivation from
  `reference_positions` / `select_all_expanded`), and the reply equals `build-ideate-reply`'s
  output for every ideation fixture; the tool's returned `session_vars.ideation` is folded into
  the turn's `SessionVars` by the tail. (D6)
- AC-307 `[BE][T]` Given `sorento_crm_mcp/catalog.py`, when `sync_catalog` runs, then
  `mcp_tools` has an active `crm_ideation_turn` row (`http_method = POST`, `http_path =
  /api/v1/external/ideation/turn`, `external = True`); a pytest asserts the catalog entry and
  the sync, and the MCP server's tests cover the tool's argument schema. (D6)
- AC-304 `[BE]` Given `not_supported`, when the domain list is consulted, then it is
  `system_settings.chatbot_unsupported_domains` (JSON array, default
  `["goods_receive", "spo_allocation"]`), exposed in BOTH settings GET dict builders and the
  `SystemSettingUpdate` schema. (B2)

  **Landed with a sibling** (5 Sep 2026): `system_settings.chatbot_completed_lanes`, a JSON
  list shipped EMPTY, which decides which branch kinds the CRM finishes rather than
  delegating. A lane completes only when it is in BOTH that list and the code's own
  `lanes.canned.COMPLETED_BRANCH_KINDS`, so the S3 cutover is a data change with an edit-
  the-list rollback rather than a deploy. Both columns are in the settings GET dict and the
  `SystemSettingUpdate` schema, typed `List[str]` so a bare string cannot be saved and then
  iterated one character at a time by the membership test.
- AC-306 `[BE][T]` Given `system_settings.chatbot_stock_denial_enabled` (boolean, default
  false, both dict builders), when false, then `isStockCheckDenied` is never evaluated and no
  turn routes to `stock_denied` / `demand_qty`; when true, then a contact without
  `is_allowed_stock` asking `check_stock` with entities routes to `stock_denied` (R1). (A2)
- AC-305 `[N8N]` Given the spine, when S3 is promoted, then the `route` Switch outputs for the
  eight branch kinds above are removed and a single `if delegate == null` node after the head
  call goes straight to `sub-sendmsg`; the `ideate-turn-http`, `build-ideate-reply`,
  `offer-hold-reply`, `tag-*` and `Edit Fields2` nodes are deleted. (A2)

  **Amended 5 Sep 2026, two corrections from the implementation:**

  - There is an ORDER, and it is written at node level in `n8n-changes.md` S3: CRM deploy
    (inert, `chatbot_completed_lanes = []`) -> shadow -> the owner adds the kind(s) to the
    list -> only THEN the Switch outputs and nodes go. Deleting before the data flip turns
    a reversible change into an outage; rollback before that point is editing the list.
  - `Edit Fields2` STAYS. It stamps `not_allowed_check_stock` onto the `stock_denied`
    item and that lane still delegates until S6. The CRM stamps the same field itself now
    (`engine._stamp_item`), so the node is a no-op - but a no-op that is deleted while its
    lane still runs is a lane that stops carrying the field.
  - The `if delegate == null` node already exists: it is S1's `head-arm` Switch `finished`
    route, so S3 adds no node to the spine at all.

### S4 - low_signal lane (journey A2)

- AC-401 `[BE]` Given `branch_kind == low_signal` **and the lane is enabled in
  `system_settings.chatbot_completed_lanes`**, when the head runs, then the engine resolves
  entities for the prompt (the service behind `POST /system/references/resolve`, `match_mode =
  or`, `fallback_to_all_types = true`), builds the user prompt with the same six fields
  `construct-user-prompt` builds (session vars blanked for `casual` / `unknown`), calls the LLM
  with prompt key `chatbot_clarifier` (fallback = the inline n8n system prompt verbatim), parses
  `{response}` through `central-exchange`'s fence-stripping rule, and completes the turn through
  the S2 tail (`complete_turn`), closing `done` at `remembered`. With the lane NOT enabled (the
  default `[]`) the same turn responds `delegate = "low_signal"`, the clarifier is not called at
  all, and n8n answers it exactly as today. (A2)
- AC-402 `[T]` Given every `construct-user-prompt` and `central-exchange` fixture, when
  replayed, then output equals `expected`. (A2)
- AC-403 `[BE][T]` Given the LLM call fails, when the lane runs, then the turn is `failed` at
  `stage = casual_llm` and the reply is today's `sub-error-logger2` text; nothing else is sent
  (`_casual_error` path). (A6)
- AC-404 `[N8N]` `Call 'sub-casual-llm'`, `casual-gate`, `Call 'sub-answer'1` removed from the
  spine. (A2)

### S5 - Escalation lane (journey A4)

- AC-501 `[BE]` Given `branch_kind == out_of_scope`, when the head runs, then the engine runs
  `escalation-input`, `fresh-entity-gate` (resolve + gate in-process when a fresh entity is
  present), `escalation-context` (the six-rank precedence ladder, never a hard team default,
  H27), `clarify-team-gate`, `clarify-company-gate`, and either completes with the clarify ask
  (`pending.kind = team_clarify | company_clarify`) or produces the assignment. (A4)
- AC-502 `[BE]` Given the assignment path, when it runs, then the engine calls the next-assignee
  service and SLA-tracking create in-process and returns `actions = [send_message(routed-to-PIC
  1), assign_conversation, add_comment, send_message(routed-to-PIC 2)]` in that order, plus the
  out-of-scope state text with `includeResponse = false` (state only). (A4)
- AC-503 `[BE][T]` Given a test envelope (`mode != live` or `test_run_id` present), when the
  assignment path runs, then the next-assignee call is NEVER reached (D14 dry-run evaluated
  before any side-effecting service), and the would-send actions are returned with
  `dry_run = true` (H37). (A4)
- AC-504 `[T]` Given every `escalation-context`, `clarify-team-reply`, `clarify-company-reply`
  fixture, when replayed, then output equals `expected`. (A4)
- AC-505 `[BE][T]` Given the multi-company unpicked case, when the clarify ask is produced, then
  it is ALWAYS in the reply (the H2 race cannot exist: one synchronous function). (A4)
- AC-506 `[N8N]` `escalation`, `escalation-arm`, `clarify-company-reply`, `tag-out-of-scope`
  removed from the spine; `sub-escalation` and `sub-human-intervention` unpublished; the n8n
  outbound executes `assign_conversation` / `add_comment` via the existing respond.io nodes. (A4)
- AC-507 `[E2E]` Given the clone with `is_test`, when an escalation turn runs, then
  `test:egress:{run}` records the four would-send actions in order (`send_message`,
  `assign_conversation`, `add_comment`, `send_message`), each `send_message.quick_replies`
  a non-empty comma-joined string or `null` (never a list, never `""`), and no real
  assignment happens. (A4)
  - **Amended 6 Sep 2026 (owner console pass).** "No real assignment happens" is now stated
    as what it always meant, and the placeholder it used to be measured by is gone. The
    dry run PREVIEWS the assignee the live turn would draw - `assign_conversation.
    respond_user_id` and the comment's mention carry the real member, with `preview: true` -
    because "somebody would be assigned" answered a question nobody was asking. What must
    hold instead, and what the test now asserts, is that nothing was WRITTEN: the round-robin
    cursor did not move (no row created, none advanced) and no `conversation_sla_tracking`
    row exists. The preview reads the same pool, the same cursor and the same arithmetic as
    the draw (`AccessAgentService.preview_next_assignee`, reached through
    `/external/next-assignee` with `preview: true`), so it cannot rotate differently from the
    turn it previews. The ROUTING decision is previewed on the same terms (AC-815): a dry run
    that skipped the person / team gate would show the owner an inherited team a live turn
    would never use, and the console is where the owner looks. A missing or failing preview
    seam falls back to the old null placeholder - a preview never fails a turn.
    Evidence: `test_s5_escalation_lane.py::test_dry_run_never_reaches_next_assignee`,
    `test_dry_run_isolation.py::TestDryRunEscalationPreviewsTheRealNextAssignee`,
    `test_team_hierarchy_and_round_robin.py::test_preview_next_assignee_does_not_advance_the_cursor`.

### S6 - Business lane (journey A2, A3)

Sub-sliced: S6a resolve + gate, S6b fetch, S6c answer + miss. S6a and S6b may ship before S6c
only with the tail still accepting `delegate` fragments (they do: the `/complete` contract is
the seam).

- AC-601 `[BE]` (S6a) Given `entry in {resolve, access_check}`, when the lane runs, then
  `get-access-types` (contact access types service), `resolve-entity` (references resolve
  service, with `entity_pins` and the 400 `ENTITY_PIN_MISMATCH` behaviour, H38), `tier-gate`,
  `disallowed-entity-gate`, `build-ctx-resolved`, the incoming / customer pickers and the four
  `resolve-exit-*` arms run in-process and produce `_exit_kind` with the same payload. (A2)
- AC-602 `[T]` (S6a) Given every `disallowed-entity-gate`, `tier-gate`, `annotate-*-picker`,
  `resolve-exit-*` fixture, when replayed, then output equals `expected`; the `_isTimeline`
  contains-sentinel semantic is reproduced exactly (H46). (A2)
  - Amended 5 Sep 2026, at implementation. Two clauses needed narrowing against what the
    export actually ships. (a) `_isTimeline` lives in `output-structurer`, which is S6b, not
    in any node of `sub-resolve-and-gate`; S6a therefore declares the sentinel and its
    CONTAINS reading once (`contracts.TIMELINE_SENTINEL` / `is_timeline`) with the
    mixed-array contract test, and S6b consumes that rather than re-deriving the guard a
    second time. (b) Every capture predates two output keys the shipping bodies emit
    (`specific_options`, `tier_pick_domain`); those keys are excluded from the comparison,
    with the two-commit evidence recorded in `tests/chatbot/_corpus.py`
    `CAPTURE_BODY_ADDITIONS`, and covered by unit tests instead. Everything else is compared
    unchanged.
- AC-603 `[BE]` (S6a) Given `resolved.by_entity_type`, when rendered to the customer, then only
  entity-type keys are iterated; a contract test adds a metadata key and asserts it never reaches
  the reply (H16). (A2)
- AC-604 `[BE]` (S6b) Given `_exit_kind == continue`, when fetch runs, then tool selection uses
  `EmbeddingReadService` tool search in-process (no direct pgvector SQL from outside the service
  layer, H53), `tool-filter` picks max similarity with name tiebreak, and zero tools yields a
  distinguishable `not_found` outcome instead of an empty turn (H11). (A2)
- AC-605 `[BE]` (S6b) Given the chosen tool, when the read runs, then `entity-ids-transformer`
  builds the arguments, `MCPRuntimeClient` calls the MCP server at the CONFIGURED URL (never a
  raw IP, H52), and `output-structurer` renders the deterministic answer text. (A2, D10)
- AC-606 `[T]` (S6b) Given every `entity-ids-transformer`, `output-structurer`, `tool-filter`,
  `fetch-result`, `tier-probe-*` fixture, when replayed, then output equals `expected`. (A2)
- AC-607 `[BE]` (S6c) Given a fetched result, when answer runs, then `validator`, `promo-picker`,
  `crossdomain-zeroset`, `crossdomain-probe` (second MCP call), `crossdomain-render`,
  `build-result`, and either `sub-answer` (central-exchange, miss roster, partial did-you-mean
  with its probe) or the miss lane (`not-found-error-message`, `dym-transform`, the three
  probes, `family-fetch` via the products service in-process, `build-suggest-offer`) run and
  hand the `sub-output` fragments to the tail in-process. (A2, A3)
- AC-608 `[T]` (S6c) Given every fixture for the nodes in AC-607, when replayed, then output
  equals `expected`. (A2, A3)
- AC-609 `[BE][T]` (S6c) Given the answer already contains a candidate row, when did-you-mean is
  computed, then that row is not offered again (H45, one outcome-level predicate). (A3)
- AC-610 `[N8N]` `Call 'sub-main-processing'`, `tag-entry-*`, `Edit Fields2` removed; the
  sub-workflows `sub-main-processing`, `sub-resolve-and-gate`, `sub-fetch-results`,
  `sub-get-rag`, `sub-get-results`, `sub-answer`, `sub-miss-suggest` unpublished. (A2)
- AC-611 `[E2E]` The full clone smoke set plus the `tests/cases/*.json` canaries run green on the
  S6c build with reply text equal to the pre-S1 baseline, divergences listed. (A2, A3)

### S7 - Thin spine + CRM per-contact ordering (journey A5, A6, D1)

Owner's target (round 4): 50 dealers sending 2 questions each at the same moment. The n8n
dispatcher serves one contact per second, so it is retired at S7 and the CRM orders turns per
contact inside the synchronous request. Different contacts run in parallel.

- AC-701 `[BE]` Given `POST /chat/turn` after S6c, when called, then it runs the whole turn
  in-process and returns `{turn_id, reply, actions, delegate: null}`; p95 under the
  `chat_latency_p99_target_seconds` setting on the pilot contact. (A5, D4)
- AC-702 `[BE][T]` Given an envelope with `is_test = true` or a `test_run_id`, when any stage
  runs, then no row outside `chatbot.turns` is written (session vars, SLA, round-robin cursor,
  chat history all untouched, asserted by row counts), the response carries `session_patch`
  and every action has `dry_run: true`. (D14)
- AC-703 `[BE][T]` Given the optional worker offload flag `CHATBOT_TURN_ON_WORKER = true`,
  when set, then the request enqueues `run_turn_job` on queue `chat` and waits up to
  `CHATBOT_TURN_WAIT_SECONDS` (default 60) for the result, returning the same body; when
  unset (default), the turn runs in the request. `chat` is in `DEFAULT_QUEUES` and pinned by
  `tests/test_worker_queue_defaults.py`; the server compose `WORKER_QUEUES` change is called
  out in the PR for the owner. (D4)
- AC-704 `[BE][T]` Given an exception at any stage, when the turn runs, then the row is
  `failed` with `stage` and `error`, an `integration_log` row is written, the response still
  carries the error reply as a `send_message` action, and NO automatic retry happens (R4). (A6)
- AC-705 `[BE]` Given a `failed` turn, when Retry is pressed on the trace screen (AC-257), then
  the CRM re-posts the ORIGINAL respond.io webhook body (`envelope.message` as stored on the
  row) to the n8n inject webhook until S7, and to the thin-spine webhook after, so the normal
  path (ordering, engine, caller sends) runs; the CRM never sends itself. The re-injected
  message keeps its `message_id`, so the engine treats a `failed` row carrying
  `retry_requested_at` as re-runnable rather than as a D15 duplicate: it inserts a NEW row with
  `attempt + 1` and `ingress = retry` (the unique key is
  `(contact_respond_id, message_id, attempt)`, migration 474) and clears the marker. Any other
  existing row is still a duplicate. (D3, R4)
- AC-706 `[N8N]` Given the n8n side, when S7 is promoted, then the ingress is webhook >
  `sub-media-intake` > `POST /chat/turn` > a Switch on `action.kind` feeding the existing
  `sub-sendmsg` / `send-attachments` / assign / comment / update-contact nodes; the
  `sorento-dispatcher`, the redis `q:*` / `ready-contacts` / `lock:*` keys and the old monolith
  `9qVyfUxmRQqrpGRMDLRuz` are retired; `N8N_CONCURRENCY_PRODUCTION_LIMIT` (or worker count) is
  raised so 100 concurrent executions are not throttled. The test clone `Hnd4S8SVH6pftjxs`
  calls the same endpoint with `is_test: true` and its `test-guard` records the actions to
  `test:egress:{run}`. (A5, D1, D14)
- AC-709 `[BE][T]` Given two requests for one contact arriving 50 ms apart, when both call
  `/chat/turn`, then the second runs only after the first finished, in arrival order (redis
  ticket `chatbot:seq:{contact}` + `chatbot:done:{contact}`), and two requests for two
  different contacts run concurrently (measured overlap in the test). (A5, H30)
- AC-710 `[BE][T]` Given a predecessor request that died without advancing the done counter,
  when the next waiter sees no `chatbot:running:{contact}` key for more than
  `STALL_GRACE_SECONDS`, then it repairs the counter and proceeds; given the wait exceeds
  `CHATBOT_QUEUE_WAIT_SECONDS`, then the turn is `failed` at `stage = queued` with today's
  error reply AND releases its own ticket on the way out, so the contact is blocked for one
  turn rather than for the `running` key's whole TTL. (A6)
  **Grace revised 5 Sep 2026, from 2 s to the redis socket timeout + 2 s (12 s).** The
  liveness probe is itself a redis round trip on a connection with `socket_timeout = 10`, so
  a two-second grace made one slow redis call enough to declare a LIVE predecessor dead and
  run beside it - the failure the ordering exists to prevent, reached through its own repair.
  The second clause above is what now covers the death case the grace cannot see (a process
  killed with its `running` key still set looks alive for 300 s).
  **SUPERSEDED 1 Oct 2026 by CHATBOT-QUEUE-FIX** (`documentation/plans/chatbot/PLAN-chatbot-queue-fix-1oct.md`):
  the shared `running` key and the stall grace are gone. Each ticket has its own heartbeat key
  (`chatbot:alive:{contact}:{ticket}`, 15 s TTL), `done` only moves over finished or dead
  tickets, and a wait past `CHATBOT_QUEUE_WAIT_SECONDS` RUNS the turn (recorded `timed_out`
  on `queued`) instead of failing it. Prod incident: contact 900000081, 1 Oct.
- AC-715 `[BE][T]` (added 5 Sep 2026, S7 mode) Given S7 mode is on and a turn routes to a
  lane the CRM does not complete (not in `CRM_COMPLETED_BRANCH_KINDS`, or not in
  `system_settings.chatbot_completed_lanes`), when the head finishes routing, then the turn
  is `failed` at the stage it reached with an error naming the lane and
  `chatbot_completed_lanes`, the caller gets today's error reply as a `send_message` action,
  the trace carries the reason, and R4's manual Retry applies - rather than the row being
  left `delegated` for a `/complete` that S7 mode answers 410. With the flag off the same
  turn delegates unchanged. (A6, D7)
  **LIVE turns only (D14).** A dry run delegates as before and records the same finding as
  a `skipped` trace note instead: nothing was going to complete it either way (the clone's
  `test-guard` records actions and never calls `/complete`), there is no customer waiting,
  and failing it would make the AC-711 load gate, the shadow window and the console unable
  to run in the very mode they exist to prove out - measured, every turn of a gate run went
  red on that arm. The harness therefore still SEES the lane that is not ready, which is
  what makes a shadow run the place this gets caught before a customer meets it.
- AC-712 `[BE][T]` Given the same respond `message_id` for one contact posted twice (webhook
  and poller, or a watermark re-list), when the second arrives, then no second turn runs, the
  response is 200 `{duplicate: true, turn_id: <original>, reply, actions}` and the n8n Switch
  on `duplicate` sends nothing; a third post with a different `message_id` runs normally. (A5,
  D15)
- AC-713 `[BE][T]` Given a poller batch of 5 buffered messages for one contact posted in
  respond timestamp order while a live webhook message for the same contact arrives mid-batch,
  when all six run, then replies are in ticket (arrival) order and no two turns for that
  contact overlap; the trace rows carry `ingress = poller` / `webhook`. (A5, D15)
- AC-714 `[N8N][E2E]` Given the webhook-to-polling switchover (carve state in
  `failover_watermark`, gate `in-failover?` in the producer, poller `CYNq34WZx83POLQ5` >
  `sorento-main-INJECT`), when S7 is promoted, then BOTH injectors call the thin spine (the
  concurrency plan's own lesson: flip both or one strands messages), and on the clone: (a) a
  contact carved to polling gets its reply through poller > INJECT > `/chat/turn` > send;
  (b) un-carving it moves the same contact back to the webhook path; (c) a message that
  arrives via both during the switch runs once (AC-712). Recorded as an agent-driven clone run
  in the n8n repo. (A5, D15)
- AC-711 `[E2E]` Given `scripts/chatbot_load.py` against the S7 backend with 50 contacts x 2
  messages fired at once (dry run), when it completes, then p95 turn time is under 12 s, zero
  errors, DB pool usage below 60%, every turn's `branch_kind` landing on the business lane
  (not `access_denied` or anything else - the script refuses before firing unless
  `system_settings.chatbot_business_lane_enabled` and `chatbot_completed_lanes` already let
  the business arm ANSWER), and every contact's two replies come back in the order the CRM
  RECEIVED that contact's messages, with no two of that contact's turns overlapping (both
  graded from `chatbot.turns` after the run, never from the client's send order - AC-709's
  guarantee is arrival order); repeated at 300 turns. (A5)
  **The 5 Sep 2026 measurement below is SUPERSEDED - it measured `access_denied`, not the
  business path.** The seeded contacts carried no access grant, so `branch_kind` was
  `access_denied` on 100% of the run's rows and every turn took the free canned-reply lane;
  a 6 Sep 2026 fix seeds the grant (and checks the two settings above before firing) so the
  gate reaches resolve/tier-gate/fetch/answer instead. ~~Measured 5 Sep 2026 (ordering on, one
  uvicorn worker, mocked parser): 100 turns, zero errors, zero out of order, p95 1.24 s;
  300-turn repeat p95 3.77 s.~~
  **Re-measured 6 Sep 2026**, chatbot-s8 lane backend, one uvicorn `--reload` worker,
  mocked parser, business lane switched on (`uptime` immediately before: load averages
  4.88 5.34 6.24). Raw numbers: `wall 120.1s turns 100 p50 120.00s p95 120.06s`,
  `errors 95`, `branch_kind: {'business_query': 30, '(none)': 3}`. `branch_kind` confirms
  the fix - real business turns land, zero `access_denied` - but p95 120.06s is the
  CLIENT's own request timeout, not a completion time, and this run predates the
  `_wait_for_turns_to_settle` fix that shipped in the same PR: cleanup ran the instant the
  client gave up, and 21 more turns landed roughly 3 minutes later as
  `branch_kind=None, stage=received, status=failed` because their contacts had already
  been deleted mid-drain - the exact bug that fix closes, caught by this run's own
  evidence. Load average spiked to 52 right after (unrelated concurrent work on the
  shared machine, confirmed via `ps`), which made a same-session repeat unsafe to compare
  against. None of this is a regression from this fix - it is the capacity question this
  AC was always meant to surface, previously hidden by the wrong-path measurement above;
  see the plan's capacity section for the pool clause's open question and the trigger for
  a multi-worker re-run.
  **Re-verified 6 Sep 2026** after the settle-wait and STRICT-gate fix, `--timeout 240`
  (load averages 4.16 5.16 6.27 before): `wall 240.2s p50 240.00s p95 240.07s`,
  `errors 100`, `branch_kind: {'business_query': 25}`, `75 turn row(s) missing`. Every one
  of the 25 GRADED rows is `business_query / remembered / done`. The settle-wait narrows
  the race but does not close it at this much backlog: a further 16 rows landed as
  `stage=received, status=failed` roughly 10 minutes after grading and cleanup had
  already run, past even the 180s settle window - a row not yet inserted is invisible to
  a poller, and cleaned up by hand once read (see `n8n-changes.md`'s Step 4 for the
  detail). Still RED on timing (single dev worker, unchanged finding);
  `db connections: baseline 19 peak 40` rules out Postgres
  as the ceiling here.
- AC-707 `[E2E]` Given live traffic for one pilot contact routed through S7, when they send
  three messages quickly, then the three replies arrive in order and `chatbot.turns` shows
  three `done` rows with `finished_at` ascending. (A5)
- AC-708 `[E2E]` Given the chat console (`scope: chat-ui`) drives a turn on the clone, when
  the turn completes, then `test:egress` holds the would-send actions and no WhatsApp message
  reaches any contact, and `respond_contacts.session_vars` for that contact is unchanged. (D14)

### S8 - Retire and harden (journey C1)

- AC-801 `[BE][T]` The legacy regex readers for the two frozen prefixes (R3) are removed; a
  session with only the legacy string is treated as no pending offer, and the fixture divergence
  entries are retired. (A4)
- AC-802 `[N8N]` Disabled nodes left behind by S1 to S6 are deleted; `export-workflows.py` is
  re-run and the n8n repo's `TOPOLOGY.md` for the two remaining workflows shows zero by-name reads
  into deleted nodes. (C1)
- AC-803 `[BE]` The plan's hazard table has every row marked `fixed (AC-x)`, `reproduced (AC-x)`,
  or `backlog (#issue)`. (C1)
- AC-804 `[BE][FE][T]` Retry ingress config lives on the respond workspace row
  (`chatbot_retry_ingress_url`, `chatbot_retry_ingress_key_ciphertext`, Fernet like the ideation
  intake key), editable on the Respond Workspaces screen. The two fields have their OWN route,
  `PUT /respond-workspaces/{id}/chatbot-retry`, accepting `user_management.settings.edit` OR
  `system.respond_workspaces.edit` and carrying those two fields and nothing else; the row PUT
  keeps its single `system.respond_workspaces.edit`, so a holder of the Settings slug alone
  cannot change `api_key`, `base_url`, `space_id` or `is_default` (S8a security review: the
  first shape widened the row PUT, which handed that slug the respond.io credential for the
  whole install). Key write-only (never echoed). Omitting a field leaves it alone; sending it
  blank or null CLEARS it, so blanking the URL turns Retry off and the key has a revoke path.
  Given a workspace with no URL, when an operator clicks Retry,
  then the response is 409 "retry not configured" and no outbound call is made. Given a URL
  that is not https, or whose host resolves to loopback, a private range, or the CRM's own
  host, when saved or used, then it is rejected with a 422 naming the RULE and not the address
  it resolved to; an IPv6 literal carrying a v4 address (`[::ffff:10.0.0.1]`, 6to4, Teredo) is
  unwrapped by the same normalisation a resolved address goes through, and userinfo in the URL
  is refused. Given a valid URL, when Retry runs, then the POST follows no redirects and sends
  the key as `X-Chatbot-Retry-Key`, and when the ingress refuses, the 502 reports the status
  code and a fixed message, never the ingress response body. `CHATBOT_RETRY_INGRESS_URL` and `CHATBOT_RETRY_INGRESS_KEY` no longer
  exist in `config.py` or `.env.example`. (A4, C1)
- AC-805 `[BE][T]` After the S7 spine is PUT: `POST /turn/{id}/complete`, the id-less
  `/turn/complete` and `app/services/chatbot/delegate.py` are deleted; the route inventory test
  asserts exactly one turn-creating route; `complete_turn` keeps its single caller. Gated on
  the owner's S7 promote. (H6)
- AC-806 `[BE][T]` Security nits closed: the header-masking guardrail matches every
  hand-rolled mask shape (bracket assignment, `.get`, dict literal), `_CREDENTIAL_NAME_PARTS`
  includes `auth`, the `is_test` comment at the duplicate path is corrected, and the archived
  plan no longer carries the real phone number. (C1)
- AC-807 `[BE][FE][T]` Prompts screen: for `chatbot_semantic_parser` and `chatbot_clarifier`
  the Test action posts a dry-run turn (`is_test: true`, chosen prompt version pinned via
  `prompt_overrides`) and renders the turn trace inline; zero writes outside `chatbot.turns`
  (D14); other keys keep the assistant dry run. **The contact is CALLER-SUPPLIED**
  (`contact_respond_id` on the request, blank is a 422), not read from the workspace: the
  workspace has no dev-contact column, and adding one would be a new config surface for a test
  button. Because that turn reads THAT contact's access level and remembered session state and
  hands back the trace, the endpoint requires `system.chat_history.view` - the slug the Chat
  History trace screen uses to show a contact's turn trace - IN ADDITION to
  `system.ai_assistant_settings.edit`, so nobody reads a contact's remembered state with a
  weaker slug. Both slugs, caller-named contact (owner ruling, S8a security review S3). (D5, D13)
- AC-808 `[T]` S2's `tier_menu` STALE_FIXTURES entries are migrated to
  CAPTURE_BODY_ADDITIONS with the live body cited; STALE_FIXTURES is empty. (D8)
- AC-809 `[BE][FE][T]` Chatbot settings screen (System > Settings > Chatbot, issue #679),
  admin-only under `user_management.settings.edit`, saving through the existing
  PUT /settings/general. Given the page, when it loads, then `chatbot_completed_lanes` renders
  as a checkbox list over the branch-kind vocabulary served by GET /settings/chatbot-lanes
  (single source `contracts.CRM_COMPLETED_BRANCH_KINDS`, each with a built/not-built flag from
  the engine), the current list is checked, and `chatbot_stock_denial_enabled` and
  `chatbot_unsupported_domains` are editable beside it. Given a branch kind outside
  `contracts.CRM_COMPLETED_BRANCH_KINDS`, when saved, then the save is refused with a 422
  naming the kind. No feature explanation text inside the UI. Usable at 375px and 1280px. (D5)

  **`built` is a UI hint, and the 422 is scoped to the vocabulary** (amended 5 Sep 2026,
  at implementation; security review round 2 asked for the line to say what shipped).
  Two conditions were folded into one sentence here and they are not the same thing:

  - **Not in `CRM_COMPLETED_BRANCH_KINDS`** means no code in this build can finish that
    kind, so the save is a 422 naming it. That is the sentence above, and
    `tests/chatbot/test_s8_settings_screen.py` grades it.
  - **`built == false`** means the kind ships but its second switch
    (`chatbot_business_lane_enabled`, which only the three business arms have) is off. That
    combination is NOT refused, because the promote sequence requires it: AC-810's own
    `test_business_lane_off_via_the_row_still_delegates` lists `business_query` with the
    switch off and asserts the turn DELEGATES, and `engine.py` restores the delegate for
    exactly that case so the turn is never left silent. Refusing the save would forbid a
    state the engine is built to handle, and would make "add the lane, then turn the
    switch on" unorderable.

  So the screen carries `built` rather than the server: an unbuilt kind's checkbox is
  disabled and labelled "Not built", and a kind already listed when the switch went off
  stays enabled so it can be UNCHECKED (a disabled checkbox the owner cannot clear would
  be a trap, not a guard).
- AC-810 `[BE][FE][T]` The owner-operated switches leave the environment: `CHATBOT_BUSINESS_LANE_ENABLED`
  and `CHATBOT_ORDERING_ENABLED` become system_settings columns `chatbot_business_lane_enabled`
  and `chatbot_ordering_enabled` (default false, additive migration), read by the engine per
  turn, toggled on the same screen; the env names are removed from config. `CHATBOT_TURN_ON_WORKER`
  stays a deployment property and is not on the screen. Given ordering on, when saved, then the
  screen shows the S7-mode consequence (every /complete answers 410) as the confirm dialog's
  text, and the setting round-trips through GET /settings/general. (D4, D5)

- AC-811 `[BE][T]` **Every session the turn engine opens carries the contact's company scope,
  resolved by the same rule as the API-key path.** The engine calls route and service
  functions in process (the resolver route, and through it stock, promotions and product
  attachments), so the router dependency that stamps company scope never runs for them.
  Given a contact who belongs to company A, when a turn asks about a product that exists in
  company A, then the resolve step names that product and the reply is not "Couldn't find".
  Given a same-coded product in company B, which the contact does NOT belong to, then it
  never surfaces (the fix must not widen to all companies). Given a contact with no
  `respond_contact_companies` row, then the scope is an EMPTY frozenset, the product never
  resolves, and the turn still completes (fail closed, never `None`). The identity rule is
  the contact respond id plus the default workspace's `space_id`, resolved through
  `company_scope_resolver.resolve_contact_company_scope`, the same function
  `_resolve_api_key_scope` calls, so the engine's scope and the resolver route's scope cannot
  drift. "Every session" means all three seams the engine has: `engine._session` (every call
  site, via the wrapped factory), the business lane's own family read
  (`answer_services_for`, which opens off that same factory), and the escalation lane's
  `escalation_services.production_session()`, which reached for `SessionLocal` directly and
  now takes the turn's factory. That last one is defence in depth, not a repair:
  `post_next_assignee` pins its own scope (`_scope_request_to_company`) before every
  `Team` / `AgentTeam` read, so the draw worked; the pre-pin reads
  (`_routing_company_for_body`) and the lane's own unit of work are what ran unscoped, and
  one mechanism for the turn beats a per-callee pin. The tail (`complete_turn`,
  n8n's `/complete` entry) stamps the row's contact unconditionally, since a conditional
  could not be tested: `tests/conftest.py` defaults every new session to Sorento.
  Evidence: `tests/chatbot/test_engine_company_scope.py` (6 tests: the contact's own company
  resolves, the other company's same-coded product does not, an orphan contact fails closed,
  every session `engine._session` opens carries the contact's own company rather than the
  harness default, the tail's session carries it, and the escalation lane's own session
  carries it). (H56)

- AC-812 `[BE][T]` **A dry run is isolated from live traffic on every surface, not only on
  the engine's own writes.** D14 said "a test envelope does ZERO writes outside
  `chatbot.turns`"; the engine honoured it and four surfaces around the engine did not.
  Given a dry-run turn (`is_test`, `test_run_id`, or a non-live `mode`) for a REAL contact:
  when `POST /chat/turn` answers, then no `integration_log` row is written for it, and the
  same holds for `POST /chat/turn/{id}/complete` and for the id-less form's refusals (the
  `chatbot.turns` row already carries envelope, response and trace under `is_test`, which is
  strictly more than the log line held); when ordering is on
  (`system_settings.chatbot_ordering_enabled`), then the turn takes NO ticket and touches
  none of `chatbot:seq:{contact}` / `chatbot:done:{contact}` / `chatbot:running:{contact}`,
  because those keys are shared with that contact's live traffic and a test turn must never
  delay a real customer; when a LIVE delivery of the same `messageId` arrives afterwards,
  then it runs as its OWN turn (`duplicate: false`, a new row, `is_test = false`) instead of
  being answered from the test row's canned reply with `duplicate: true`, which sends the
  customer nothing at all - and, in the mirror, a test envelope for a message that already
  ran live also runs its own turn, so replaying a real message from the Prompts screen's
  Test button works on exactly the messages worth testing. D15 is scoped, not weakened: a
  second delivery WITHIN one world is still a duplicate. The unique key becomes
  `(contact_respond_id, message_id, attempt, is_test)` (migration
  `481_chatbot_turns_is_test`) so the two worlds can coexist. On the operator surfaces:
  `GET /turns` and `GET /turns/failed-contacts` exclude `is_test` rows unless
  `include_test=true` is passed, and `POST /turns/{id}/retry` on an `is_test` row answers
  409 `test_turn_not_retryable`, posts nothing at the ingress and leaves
  `retry_requested_at` NULL - retry re-posts the original message at the LIVE n8n ingress,
  so retrying a dry run is the one action on that screen that turns a test into a real
  message to a customer. Also here: a sealed reply carrying NO `session_patch` key leaves
  `respond_contacts.session_vars` untouched on a live turn, while an EXPLICIT `{}` is a
  reset and still writes - `sealed.get("session_patch") or {}` collapsed the two and wiped a
  customer's memory on a turn that never asked for it. No new UI: the operator surfaces keep
  the query parameter, and no toggle is built until a product ask exists for one.
  Evidence: `tests/chatbot/test_dry_run_isolation.py` (12 tests, one per finding plus the
  guards each fix must not regress), and `test_chat_turn_endpoint.py::TestDryRunEndpointZero
  Writes` / `test_s8a_hardening.py::TestATestEnvelopeIsNeverADuplicateOfALiveTurn`, both
  amended in the same change because they asserted the behaviour this AC replaces. (H57, D14, D15)

- AC-813 `[BE][T]` **The chatbot may call READ tools only, and a write tool is refused
  before it is picked and again before it is called.** The business lane picks ONE tool per
  turn as the argmax of cosine similarity over the shared `mcp_tool` embedding pool and
  calls it with no allow-list check, and six write tools live in that pool:
  `crm_it_support_ticket_create`, `crm_complaint_close`, `crm_order_cancel`,
  `crm_purchase_request_approve`, `crm_purchase_request_reject` and `crm_ideation_turn`.
  **The pool keeps them.** It is shared with the in-app AI assistant, whose Tool-RAG only
  ever sees what is embedded (`ai_assistant_service._rag_select_tools`) and whose four
  record actions are put there deliberately by `record_action_bootstrap`; the assistant
  gates each behind an explicit user confirmation and a permission check. Filtering the
  pool would take the assistant's write tools away and would need a production re-seed to
  take effect at all. The rule belongs to the CHATBOT, which has no user to confirm with.
  Given a business question whose nearest tools include a write tool, when the chatbot
  searches, then that tool is not among the candidates at all
  (`lanes/business/services.py::_tool_search` filters after the collapse, so it is not even
  listed under `_tool_pick.rejected`; the filter is NOT in `tool_filter`, which is graded
  byte-for-byte against 38 captures). Given a write tool named by any other route (a payload,
  a probe, the tier probe), when the lane tries to call it, then `fetch.ensure_read_only`
  raises `ToolNotAllowed` at both call seams - `fetch.call_tool` and
  `services._mcp_call`, the one choke point every `mcp_probe` caller passes through - the
  MCP client is never invoked, the fetch fragment is an `error` naming the refused tool with
  outcome `tool_not_allowed`, and the turn is recorded failed at `looked_up` with the
  generic error reply rather than told to the customer as "I could not find anything" (a
  refusal is not an absence). The allow-list is a FROZEN SET in the backend
  (`fetch.CHATBOT_READ_ONLY_TOOLS`), never a catalogue walk on the turn path: the deployed
  backend image contains no copy of `sorento_crm_mcp` (compose builds the backend with
  `context: ./sorento_crm_backend`, `mcp` is absent from `requirements.txt`, no volume
  mounts it), so a catalogue read there would raise in every container and `run_fetch`'s
  broad `except` would turn EVERY live business turn into "MCP tool X failed". The catalogue
  remains the source of truth for WHICH names belong in the set - method GET, or an explicit
  `read_only=True` for a POST whose body is transport rather than a side effect
  (`crm_lookup_resolve`, `crm_portal_link_get`, `user_guides_read`) - and CI asserts the set
  and the catalogue are EQUAL in both directions, where the catalogue is readable: a name
  missing from the set is a read the chatbot silently lost, a name in the set that is no
  longer a read is a write it would still call. A tool not in the set is refused, so a new
  write tool is out by default. No migration, no re-seed, no deploy step.
  Evidence: `tests/chatbot/test_tool_pool_is_read_only.py` (18 tests: the set-equals-catalogue
  guardrail, the six write tools refused BY NAME and asserted not flagged `read_only`, the same
  six asserted STILL IN the pool so a well-meaning re-filter fails here, the three read-only
  POSTs allowed by name, and a source scan proving no turn-path file imports
  `sorento_crm_mcp`) and `test_dry_run_isolation.py::TestMcpToolPickRefusesWriteTools`. (H58, D10)

- AC-814 `[BE][T]` **A "no records" line names the axes it searched, and never an internal
  code.** The multi-company "which company came back empty" sentence flattened every resolved
  entity code into one comma list, so a customer with several alias accounts arrived as an
  internal debtor code plus one bullet per alias, next to the product, un-labelled: "no order
  records for 300-H070, HANLIM TRADING SDN BHD [A/C I], HANLIM TRADING SDN BHD, RPACC" (owner
  console run, 6 Sep 2026). Given a fetch that returns nothing for a turn naming a customer
  and a product, when the line is composed, then it reads "no order records found for customer
  <name>, product <code>" through the miss lane's own axis vocabulary (`answer._AXES`, one
  vocabulary for both lanes), states the date window when the turn carried one, prints ONE
  name per customer record (a customer reached by both its account code and its name is one
  customer, and an alias row collapses into its base - never for products, where a code that
  extends another is a different product), and never prints the debtor code. All the silent
  companies share one sentence rather than repeating the subject once each. A type no axis
  claims keeps its bare code, so a promotion or an attachment type is still named.
  Evidence: `tests/chatbot/test_s6b_fetch_lane.py::TestLabelledNotFoundLineNeverLeaksInternalDebtorCode`;
  registered divergence on the one capture that reaches the block (`output-structurer/gr-15145805`).
  (H63, owner ruling)

- AC-815 `[BE][T]` **An escalation routes to who the customer named, or asks - it never
  inherits the previous turn's team.** Two console turns, "escalate to Nurain" (a customer
  service person) and "escalate to marketing", both arrived with `routing = {suggested_team:
  null, suggested_agent: null}` and were assigned to the team the PREVIOUS turn had been
  routed to; the comment named `marketing_product` for the person and `purchasing` for the
  marketing ask. Given an escalation ask carrying the parser's own `person_mention` (D11 - the
  lane never reads the customer's words), when exactly one ACTIVE staff member's first name
  matches, case-insensitively, then the turn routes to THAT member's team with that member as
  the assignee and no round-robin draw happens (a named person is a direct pick; the SLA clock
  still starts). Given no match or more than one, then the lane CLARIFIES, naming the teams,
  and assigns nobody. ~~Given no person, no team, and a previous turn that HAD one, then the
  lane clarifies rather than inheriting it~~ - **SUPERSEDED by AC-821 (owner rules R-a /
  R-c, 7 Sep 2026).** That inheritance premise could not tell "escalate to marketing" from
  "I want to talk to a human" (both reach the lane with a null parser team over the same
  carried team, and D11 forbids reading the two messages apart), so it fired on the second
  and answered a request that named no team with the eight-team menu - production turns
  1f0428cb / 9089ef88. It is deleted; the discriminator is the parser's own word now, and a
  turn with nothing to inherit still behaves exactly as it did. Given nothing to inherit,
  the lane behaves exactly as it does today, so live's own unguarded null-team path and the
  B-TEAM-1' xfail both stand.
  The roster read is `users` x `team_members` x `agent_teams` on the turn's own
  company-scoped session, one hit per person per team, and it compares `users.status` as a
  literal (the column is a native enum in production, where `lower()` does not exist for it).
  The decision is previewed on a dry run too (AC-507), because the console is where the owner
  looks. Evidence:
  `tests/chatbot/test_s5_escalation_lane.py::TestPersonMentionEscalationRoutesByStaffLookup`
  (four cases, including the guard that the existing multi-company continuation still
  clarifies). (H64, owner ruling)

- AC-816 `[BE][T]` **The pending context persists until the topic changes.** One owner ruling
  (6 Sep 2026 console pass), four rules, and ONE definition of "the topic changed" -
  `app/services/chatbot/topic.py::changed(previous_domain, new_domain, new_offer=...)`,
  imported by both the head and the tail so the roster, the carried entities and the
  pending offer cannot disagree about it. Its truth table is a test
  (`tests/chatbot/test_topic_unit.py`): a new offer this turn always changes the topic; a
  turn that names NO domain never does (a pick, a date window and a bare code all arrive
  with `domain_hint: null`); a carried domain of `null` is not a change either (a subject
  acquiring a name is the same subject); otherwise it is a change when the two named
  domains differ.

  1. **An offered list persists.** Given a tier menu, a did-you-mean, a promo picker or a
     member offer was made, when the customer answers "1", then "2", then "3", then each
     pick resolves against the SAME list: the tail carries `selection_context` and
     `last_result_set` forward on every turn whose own ladder produced neither, including
     across the answer that pick just produced. A NEW offer this turn replaces the carried
     one (the ladder runs first and the carry only runs when the ladder was silent, so
     H29 / AC-205 is untouched); a domain change clears it; an out-of-range pick reprompts
     and keeps the list. `member_offer` carries only while the escalation offer is still
     UNANSWERED - neither accepted (`is_escalation_confirmation` or a resolved
     `preferred_assignee_id`) nor declined (`escalation_declined`) this turn - so
     `_picker_carry`'s documented hazard, re-arming an offer somebody already declined,
     stays closed. The rule the module chose is stated in `_offer_carry`'s docstring.
     Evidence: `tests/chatbot/test_tail_units.py::TestTierAndPromoOffersCarryUntilOverwritten`
     and `::TestTheMemberOfferCarryStopsAtTheAnswer`.

     **And a member offer has the DID-YOU-MEAN OFFER'S LIFETIME** (owner ruling, 6 Sep
     2026). "Unanswered" is not a licence to live forever: an offer the customer simply
     ignored was re-armed on every later turn, so a bare "yes" about something else -
     three, or twenty, turns on - still read as `is_escalation_confirmation` and assigned
     a human to a conversation nobody had asked to escalate. Given a pending member offer
     (`pending.kind = member_offer`, `selection_context = 'member_offer'`), when three
     turns pass without a reply to it, OR when the customer asks something else on ONE
     turn and gets an answer, then the offer is gone: `selection_context`,
     `last_result_set` and the `pending` marker are all cleared, and a later affirmative
     is not an escalation confirmation and assigns nobody
     (`output_exchange.offer_is_open` reads the marker's liveness, not only its kind). An
     accepted assignment or a decline still ends it on the turn it happens, unchanged. A
     filter modification under the offer (rule 3) does NOT clear it - the roster is still
     on screen - but it SPENDS one turn against the clock, or the narrow arm becomes the
     unbounded carry again. `ttl` starts at 3 (`tail/pending.MEMBER_OFFER_TTL`) and rides
     on the `pending` marker rather than a session key of its own, because the marker is
     already what says the offer is open and a second key could disagree with it; a marker
     with no `ttl` (written by n8n, or before this rule) is treated as open and starts its
     clock on the next carry.

     The rule is a deliberate divergence from the live spine, and the spine says why in
     its own words: `export/sub-output-live/nodes/compile-current-state.js` around line
     1307 gives the picker carry NO lifecycle on purpose - "NOT the suggest/did-you-mean
     offer: that one already has a lifecycle of its own directly above (`dym_offer` with a
     TTL ...), and two managers for one offer is how an offer ends up outliving both". The
     hazard it names is real and the ruling answers it the other way round: ONE manager,
     and it is the TTL, so the member offer and the did-you-mean offer expire by the same
     rule instead of one of them not expiring at all. No capture moves - the blanket
     H13/H14 divergence already strips `pending` from every `compile-current-state`
     comparison, so `ttl` is invisible to the corpus and the 3600-test chatbot suite is
     green with no new entry.
     Evidence: `tests/chatbot/test_tail_units.py::TestTheMemberOfferHasTheSameTtlAsTheDymOffer`
     (the clock, the answered arm, the filter arm and the `offer_is_open` seam) and
     `tests/chatbot/test_r3_pending_end_to_end.py::TestAnAbandonedMemberOfferStopsConfirming`
     (the owner's own sequence, through the real head/tail round trip on the database).

     **A carried offer carries its SUBJECT.** Given an offered list that survives a turn,
     when that turn named no domain and no entities of its own, then the carried
     `domain_hint` and `entities` ride forward with `selection_context` and
     `last_result_set` - an offer whose subject is gone is an offer nobody can answer.
     Prod execs 15445325 / 15445363: "promotion 7445" offered three tiers, the
     out-of-range "9" re-prompted correctly and kept the list, and the session it wrote
     had `entities: []` and `domain_hint: null` (the model reads a bare digit as `casual`
     with no positions, and `variables` is built from scratch out of that parse) - so the
     valid "all" that followed answered "I need at least one filter" instead of every
     tier's promotions for 7445. The carry FILLS a gap and never overwrites: a turn that
     names its own domain or entities keeps them, and a turn naming a different domain
     never reaches the carry at all. Carried entities are stamped `current_message:
     false`, which is the flag rule 2 keys on. Five of the thirteen owner-ruling-K
     captures move on these two fields and are registered per NAME rather than for the
     whole group, so the other eight go on being graded on them.
     Evidence: `tests/chatbot/test_tail_units.py::TestACarriedOfferKeepsTheSubjectItWasMadeAbout`
     and `tests/chatbot/test_r3_pending_end_to_end.py::TestAnOutOfRangePickKeepsTheProductInScope`.
  2. **Carried entities die on a topic change.** Given entities carried in the session
     block, when the customer asks an explicit question in a DIFFERENT domain that brings
     its own entity, then the carried set is dropped; when the domain is the same, or the
     turn names none, or the turn brings no entity of its own, then they are kept. The
     pass runs AFTER the domain blocklist, which keeps its own removals and its own
     diagnostics, so what it decides is exactly what the blocklist cannot see: a hint that
     is legal in the new domain and still belongs to the old subject.
     Evidence: `tests/chatbot/test_output_exchange_rules.py`, two registered captures.
  3. **A pending offer never captures non-pick text.** Given a pending member offer, when
     the reply carries a genuine FILTER then it is a modification of the question the offer
     was made about: the window and the entity are kept, the carried domain is inherited,
     no `member_reprompt` is stamped, no `correction` is set, and the offer stays pending
     for the tail to carry. "A filter" is NARROW, because "any entity at all" would swallow
     a genuinely new question asked in the same domain and leave the offer armed behind it:
     a date window counts in any domain, and an entity counts only when the customer named
     no new intent AND every entity they typed is a filter axis of the CARRIED domain
     (`MEMBER_OFFER_FILTER_HINTS` - customer/product for order, product for inventory, the
     two the owner ruled; a domain with no row takes only the date half, and the trigger
     for a third row is a measured turn read as a new query there). A new intent, or an
     entity type that is the new subject rather than a filter on the old one, is a new
     query and abandons the offer. When the reply is instead a genuinely out-of-range
     digit, then the roster is reprompted and kept -
     `route.decide` reaches `offer_hold` on an ordinary SINGLE-company roster too, never
     `low_signal`, which is what handed the customer a clarifier in place of the list they
     were answering. Evidence: `tests/chatbot/test_output_exchange_rules.py` (the date case
     and the product-code case, the latter asserted on the SEAM - the filter arm ran, the
     entity survived, the domain is kept - rather than on the absence of a reprompt, which
     passed for the wrong reason) and `test_route_unit.py`.

     **And a bare entity under an open roster is never small talk.** Given a pending
     member offer and a reply that is nothing but a token, when the model emits it
     `casual` (which it does for anything with no verb), then the turn is still read as a
     reply to the question the offer was made about: the label is decided by STATE - an
     offer is open and an entity was named - not by whether a verb was heard (D11). Prod
     exec 15443838 is the measured case: "rpacc" under a six-name roster on `order` came
     back `offer_hold` with ZERO entities and the roster re-shown, because the casual arm
     wipes `entities` and the domain-continuity block skips casual outright, so by the
     time the ladder asked "is this a filter?" there was nothing left that could be one.
     The same turn under an inventory thread with no roster answered, which is why rule 3
     read as working. WHICH kind of reply it is - a narrowing, a new query, or junk -
     stays the ladder's decision and is deliberately not taken here; a turn that engages
     the offer with a pick signal (a yes / no, a number, a position) keeps every arm it
     has today. The resolver then still reaches the product through
     `fallback_to_all_types`, whatever type rule 4's retype stamped on the bare token.
     Evidence:
     `tests/chatbot/test_r3_pending_end_to_end.py::TestAPendingOrderRosterDoesNotSwallowABareProductCode`
     (the owner's three-turn chain, real resolver, real seeded rows).
  4. **A bare entity inherits the carried business domain.** Given a carried business
     domain and a turn that is nothing but ONE entity - no `ordinal` on it, because a
     positional pick names a ROW and is never bare - with the model naming neither a
     domain nor an intent, when the turn is post-processed, then the domain is inherited
     and the entity is TYPED by it - `product` for inventory / incoming / promotion,
     `customer` for order - with no `domain_inherit_blocked` stamped, whatever the model
     hinted. Inheritance is blocked only when the entity RESOLVES to an incompatible type,
     which `disallowed-entity-gate` already refuses (today's clarify path), never on the
     parser's hint. A domain with no bare-entity type does not take the rule: inheriting
     there would let the blocklist drop the entity outright, which is worse than not
     inheriting, and the trigger for a fifth row is a measured turn of that shape.
     "Nothing but that entity" is decided by EQUALITY against the raw's own tokens (plus
     its fully joined form, so a customer who typed `srtwc286` still matches a raw the
     parser echoed as `SRTWC-286`), never by containment: the fork asks whether the raw
     `includes` the token and a short numeric token is inside almost any code.
     Evidence: `tests/chatbot/test_output_exchange_rules.py` (the inventory and order
     cases) and `test_resolve_gate_unit.py::TestBareEntityInheritanceIsBlockedAtResolveTime`
     (both sides of the resolve-time guard).

  Rule 4 is a deliberate divergence from the LIVE parser body and is registered as one:
  the hunk it descends from (`_bareEntityTurn`) exists only on the unpromoted
  `sub-semantic-parser-FORK`, and even there it does not RETYPE. Every capture the four
  rules move is registered in `tests/chatbot/divergences.py` field-scoped, so the rest of
  each capture is still graded byte for byte - 13 `compile-current-state` (rule 1), 3
  `output_exchange` (rule 4), 2 `output_exchange` (rule 2) and one blanket entry for the
  three diagnostic keys the port adds. 22 world replays move from graded to skipped, each
  with a NAMED reason through `worlds.body_difference` rather than failing, in three
  classes: 14 through the ruling's own clause, 7 through that file's pre-existing "the
  parser post-processor disagrees with the body that produced this capture" arm (rules 2
  to 4 change the head's output, and grading a TAIL against a different understanding of
  the turn would attribute a head-side difference to the tail), and 1 multi-turn chain
  that now grades a shorter clean prefix. The BOUND on rule 3 carries its own cover,
  because the captures that reach the filter arm are turns where it and n8n's Tier 3 both
  touch nothing and so cannot tell the narrow arm from a wide one: a same-domain new
  question (a `customer_order`, the order domain's own subject) and a new intent on a
  filter axis ("stock for rpacc" under an order roster) are both asserted NOT to be filter
  modifications, and both go red when the arm is widened back. Full chatbot suite green:
  3589 passed, 128 skipped, 5 xfailed. (H65, H66)

- AC-817 `[BE][T]` **Console pass 3 (6 Sep 2026): the customer picker says which ledger and
  whether there is a DO, "all of them" picks every offered code, and an explicitly named
  team beats a pending offer.** Three owner rulings, one lane.

  1. **The ambiguous-customer picker line reads `N. <name> (<company code>) - has DO` /
     `- no DO`.** Evidence turns 5ea477a6 (#15) and 934d4c18 (#16). Two defects, both fixed:
     (a) the line rendered the account name alone, which does not say which ledger a pick
     reaches - `_rep_label` folds a family to its bare name, so a line stood for accounts the
     customer could not tell apart; (b) the `- has delivery` stamp was built from EVERY order
     row the probe returned, so a customer whose orders had not shipped read as having a
     delivery. Given an ambiguous customer token, when the picker renders, then each line
     names EVERY company ledger its family spans, first-seen order ("(SRT, MOCHA)"; the
     resolver stamps `company_code` beside `company_name` in `_attach_company_info`, a row
     with none contributes nothing), and the suffix counts ONLY order rows carrying an
     `Actual Delivery Date`. The roster's `title` carries the same label as the printed line,
     so a reply by name resolves to the row that was read. Which window was measured stays
     on `customer_probe_window_days`; the per-line suffix is not hedged with it.

     **Withdrawn (review of #706, S2):** this AC first said the two ledgers' copies of one
     name rendered as "two identical lines". They do not: `_cust_base` groups a family by
     normalised name, so one name in two ledgers is ONE family and the gate passes silently
     with both ledgers' rows, and the picker only appears when two DIFFERENT names match.
     The owner's own turns are not in the local prod copy, so the exact shape they saw
     cannot be re-read here. Kept as live has it, on measured evidence: the two production
     captures of the shape (rg-15114061, JYL JUBIN under Mocha and Sorento; rg-15125764, YI
     HONG TILING under both) render one line whose pick carries both ledgers, the order
     answer downstream is already labelled per company (`routing_companies`), and a
     per-ledger split - tried, measured - moved eight captures and printed two identical
     lines wherever a payload lacks `company_code`. Stated as a test:
     `tests/chatbot/test_s6a_gate_dry_run_and_seams.py::TestAPickerLineNamesEveryLedgerItsFamilySpans`.

     **And the probe seam is wired.** `ResolveGateServices.probe`'s production binding raised
     `NotImplementedError`, so `resolve_gate._run_probe` caught it on every live turn and the
     annotator rendered the BARE picker - the stamp could not appear in production however
     right the annotator was. It is bound to `_mcp_probe`, the same sub-workflow seam
     `AnswerServices` already uses (`entity_ids_transformer` at the boundary,
     `parse_mcp_content` on the way back, both landed in #705 / lesson 102). Evidence:
     `tests/chatbot/test_s6a_gate_dry_run_and_seams.py::TestOwnerRulingACustomerPickerLabelCarriesCompanyCode`,
     `::TestOwnerRulingACustomerPickerDOStamp`,
     `::TestOwnerRulingATheCustomerPickerReachesTheProductionProbeSeam` (the whole seam on
     real Postgres rows, stopping only at the socket),
     `tests/chatbot/test_resolve_gate_unit.py::TestPickerProbeArms`.

  2. **"all of them" over a pending did-you-mean picks every offered code.** Given a turn that
     offered N codes and persisted them, when the customer replies "all of them", then the
     turn is a `business_query` in the OFFER's domain scoped to every offered code, each
     carrying the roster's own uuid so nothing re-resolves, and the answer names all of them.
     A cold "all of them" with nothing pending stays a clarify menu. `apply_dym_pick` now
     stamps `entity_op: "replace"` rather than `"replace_combine"`: it has already folded
     every prior entity it keeps into the returned list, so the only thing the executor's
     axis-wise `kept_prior` could add back is the very token the pick replaced. Evidence:
     `tests/chatbot/test_output_exchange_rules.py::TestOwnerRulingBAllOfThemOverPendingDymOffer`
     (the post-processor, both arms) and
     `tests/chatbot/test_r3_pending_end_to_end.py::TestAllOfThemOverADidYouMeanOfferAnswersEveryOfferedCode`
     (the real two-turn chain: a missing code, three REAL siblings found by the trigram tier
     on seeded Postgres rows, and the answer naming all three) and
     `::TestAPartialDidYouMeanPickReplacesOnlyTheMissingToken` (review of #706, S4: the
     `replace` arm through the engine - one code resolves, one misses, a numbered pick
     against the partial roster scopes the turn to the resolved code plus the pick and never
     the missing token). Measured while writing it: on this lane a partial product miss is
     claimed by the answer half's `dym-annotate-partial` -> `build-suggest-offer` before the
     tail runs, so turn 1 persists `selection_context: suggest_offer`, and
     `compile_state._partial_dym_block` (which writes `dym_last_result_set`) returns on its
     own clarification guard; the partial roster is therefore seeded in the block's own row
     shape before the pick. Also measured, reported, not fixed: a numbered pick over that
     `suggest_offer` roster without a `dym_last_result_set` roster in state takes the stock
     positional arm whatever the parser's `reference_target` says (None, "result" and "dym"
     all measured alike), and comes back `replace_combine` with only the pick in scope and
     the resolved code dropped. `apply_dym_pick` keys on the roster's presence.

  3. **A pending escalation offer never consumes a request for a DIFFERENT team.** Evidence
     turns 9a40182a and 8b3a3b80: a pending `{kind: escalation_offer, team: warehouse}` offer
     was open, "escalate to marketing" arrived, and the turn confirmed the offer and assigned
     + commented "Team: warehouse". Given an open offer, when the parser's OWN routing (the
     pre-derivation snapshot, never the derived value that has already inherited the offer's
     team) names a different team, then the turn is not a confirmation and routes to the named
     team. When the parser names NO team on a `request_for_help` and neither its own
     `escalation.is_escalation_confirmation` says the customer accepted,
     then `escalation.team_unresolved` is set, the turn is not a confirmation, and the lane
     asks which team with quick replies. A bare "yes" still confirms. "Accepted" is the
     model's own `escalation.is_escalation_confirmation` and NOTHING else (review of #706,
     B2): the first cut kept an accept-word list over the raw message to rescue the one
     capture where the model's flag is wrong ("YES ESCALTE", parser-15074293), which is the
     D11 hard-fail the plan names as a merge blocker and which re-opened D1 ("ok, can someone
     else help me" read "ok" as an acceptance). That capture is a registered divergence; the
     parser prompt is where a typo'd acceptance gets read as one.

     At the lane's own boundary (review of #706, B1): an acceptance is never asked which
     team - `is_escalation_confirmation` skips the clarify first, so a bare "yes" that the
     post-processor confirmed is assigned to the offered team end to end, not only at the
     post-processor. The clarify then needs one of two premises, because the previous
     routing is NEVER absent (the chain's hard default is persisted every turn, so "a
     previous turn had a routing" was true on every turn from the second): D1's - an
     escalation offer is OPEN, read through `output_exchange.offer_is_open`; or H64's -
     the team this turn would assign to is INHERITED, meaning the parser named none, the
     domain derives none (`derive_routing`, the chain's own function) and the previous
     routing is not the carried `DEFAULT_SUGGESTED_TEAM` (`contracts.py`, the one place the
     default is named). Scoping to the open offer alone would have assigned the owner's
     "escalate to marketing" turn to `purchasing` again. Evidence:
     `tests/chatbot/test_output_exchange_rules.py::TestOwnerRulingD1PendingOfferTeamMismatch`
     (four cases including the bare-"yes" and the "ok, ..." guards),
     `tests/chatbot/test_s5_escalation_lane.py::TestOwnerRulingD1LaneTeamMismatch` and
     `::TestAnAcceptanceIsNeverAskedWhichTeam` (seven cases through the lane's `run()`: the
     three the review measured, the kept 9a40182a case, an expired offer, the H64 case and a
     domain-routed guard).

  Registered divergences (`tests/chatbot/divergences.py`), all field-scoped so the rest of
  every capture still grades byte for byte: 4 `output_exchange` on `entity_op` /
  `entity_op_applied` (measured - the entity list is byte-equal on all four), 1
  `output_exchange` on `escalation` (parser-15074293, above), 4 `annotate-customer-picker`
  and 2 `sub-resolve-and-gate` exit arms on `escalate_message`.
  None of the three rules can be fixture-visible: n8n's own bodies say "delivery", stamp
  `replace_combine` and read `is_affirmative` alone. Full chatbot suite green on the PR's
  final head (count in the PR body). (H67, H68, H69)

- AC-818 `[BE][T]` **A filter reply under an open roster is ANSWERED, not re-asked.** Live
  turn 0d1fc129 (exec 15456707), case 69 turn 2. AC-816 rule 3's own half worked: the
  offer's `ttl` dropped 3 to 2, `member_offer_filter_modification` was set, the session
  carried the August window and both entities, and the domain stayed `order`. The customer
  still got "Sure, I can help with that. Please share the date range you want checked" -
  over a date range they had just given - because the parser emitted `message_type: casual`
  for "last month" and `route.decide`'s `is_low_signal` arm fires on that word alone, above
  the business arm, however much state the post-processor restored underneath it. Given a
  turn the post-processor flagged `member_offer_filter_modification` AND a `domain_hint`,
  when the router decides, then the turn is not low signal and reaches the business lane;
  the roster stays pending and spends one turn against its clock, exactly as rule 3 says.
  Given the flag with NO domain, the turn is still low signal - there is no open question
  for it to have narrowed, which is `is_low_signal`'s own fourth clause. Ordinary small talk
  is untouched. A router backstop over the UNDERSTOOD turn, reading only structured state
  (D11), same class as the broaden-all clarify and `is_offer_hold`'s single-company roster
  arm. Evidence: `tests/chatbot/test_route_unit.py::TestAFilterModificationIsNeverLowSignal`
  (four cases, two of them guards) and
  `tests/chatbot/test_r3_pending_end_to_end.py::TestAPendingOrderRosterDoesNotSwallowABareProductCode::test_both_narrowed_turns_are_ANSWERED_and_the_roster_stays_pending`
  (the real three-turn chain: the branch and the persisted `ttl`, on both filter turns).
  Console case: "a filter reply under an open roster is answered, not re-asked". (H70)

- AC-819 `[BE][T]` **A container-hinted token the resolver answers with products only is a
  product.** Live turn ace4cec6. With a stock question already in play (previous domain
  `inventory`, a warehouse escalation offer pending) the customer typed a bare "srtwc287";
  the parser hinted the token `inbound_shipment` and emitted `check_incoming` / `incoming`,
  so `domain_signal_source` was `intent_explicit`, AC-816 rule 4's bare-entity inheritance
  was bypassed by a domain the model had invented from a mis-shaped token, and the reply led
  with "No incoming stock (ETA) found for SRTWC287" - while the resolver's own answer for
  that token was two products (SRTWC287, SRTWC287-LID) and no shipment at all.

  Given a `inbound_shipment`-hinted entity, when the resolver returns ZERO shipment matches
  and at least one product match for that token, then the entity is retyped `product`
  (`shipment_hint_retyped` records it). Both halves are required: a real container keeps its
  type even when a product shares the token, and a token that resolved to nothing stays a
  miss rather than being answered as the wrong thing. Given ALSO exactly one entity in
  scope, `domain_signal_source == "intent_explicit"`, and NO incoming word in the customer's
  own message, then the invented `incoming` domain is dropped for the carried business
  domain (which is what rule 4 would have done); with nothing carried, the domain stands
  rather than being replaced by a second guess.

  **The message condition is not belt-and-braces, it is the whole discriminator.**
  `domain_signal_source` is stamped for ANY decisive intent plus a domain, said aloud or
  invented, so "M90ss any eta" (capture `sub-resolve-and-gate-rs/rg-15123789`) is IDENTICAL
  to the owner's turn in structured state. A product legitimately HAS incoming stock - the
  incoming picker lists product codes - so the entity retype is right in both cases and only
  the domain drop needs the customer's own word, read through
  `output_exchange.DOMAIN_SWITCH_WORDS`, the vocabulary that already decides a this-turn
  domain switch (imported, not copied).

  The seam is `resolve_gate.run`, between `services.resolve_entity` and `run_gate` - the
  first point in the turn where this evidence exists. NOT `output_exchange`: the
  post-processor is the parser's own step and runs before anything is resolved. Five graded
  `resolve-exit-offer` captures carry the retype and NOTHING else in the sub's output moves
  on any of them (measured, one at a time). The registration strips the parser's ENTITY
  ARRAY inside `ctx_resolved` (a strip addresses a path, not one field of one element), so
  `test_resolve_gate_unit.py::TestTheRetypedEntityArrayDiffersOnlyInTheHint` grades that
  array explicitly on all five: same length, same order, every field byte-equal except the
  one `hint` that moved (review of #706, S3). Evidence:
  `tests/chatbot/test_resolve_gate_unit.py::TestAShipmentHintedTokenThatIsOnlyAProductIsRetyped`
  (nine cases, five of them guards including the "any eta" counter-example) and
  `tests/chatbot/test_r3_pending_end_to_end.py::...::test_a_container_hinted_product_code_answers_stock_not_incoming`
  (the real resolver on seeded rows). Console cases: "a bare product code under a stock
  thread answers stock, not incoming" and its "still answers incoming" counter-example.
  **Outside this lane:** the parser prompt should state the container shape (4 letters + 7
  digits) and that product-family prefixes are products - a D2-style prompt change, noted in
  the PR body, not made here. (H71)

- AC-820 `[BE][T]` **A requested code with nothing in the domain that was ASKED is named
  there, before the other domain's block.** Prod turn 858c9c54, after #705. The three-code
  stock turn named MSK11A-QT only INSIDE the cross-domain block ("But there is INCOMING
  stock (ETA) for the requested products: MSK11A-QT container TEMU6355180 ..."), so read top
  to bottom the reply answered a stock question with two codes' stock and then, with no seam
  between them, an incoming fact about a third - leaving the customer to infer the thing
  they had actually asked. Given a requested code the PRIMARY render did not echo and the
  OTHER domain answered with rows, when the cross-domain block is built, then the block
  opens with "No {stock|incoming} for <codes>." and the other domain's lead follows it. The
  word follows the question that was asked (`origin_domain`), exactly as the both-empty
  sentence already does, and no escalation is offered - something IS being shown. Guarded by
  the same `can_state_absence` test as the both-empty sentence, so a render that answered
  ABOUT the code without printing it (a warehouse breakdown, a demand verdict) never gets
  "no stock" underneath the stock it just showed; and a code with nothing on EITHER side
  still gets its single combined sentence plus the offer (AC-814 / H62), never two
  half-sentences.

  It is built in `answer.crossdomain_render` rather than in the stock composer upstream,
  because that is the only place that knows BOTH facts the sentence rests on: that the
  primary render did not echo the code, and that the other domain was actually probed for
  it. Its block is appended under the primary answer, so a line at the head of the block IS
  the stock section's last word. Not fixture-visible - n8n has no such line - so six graded
  `crossdomain-render` captures are registered field-scoped to `_xdBlock.block`, the only
  key that moves on any of them; every one of the six is then graded explicitly in
  `test_s6c_engine_paths.py::TestCrossdomainRenderBlockIsByteEqualMinusTheOneSidedLine` (the
  block compared byte for byte with the one known sentence removed) rather than left to
  that strip (review of #706, S3).
  Evidence: `tests/chatbot/test_s6c_answer_lane.py::TestAZeroStockCodeIsNamedBeforeTheIncomingBlock`
  (four cases, two of them guards). Console case: "a code with no stock is named before the
  incoming block". (H72)
- AC-821 `[BE][T]` **The team menu fires only when the customer's OWN request names an
  ambiguous team, and it lists only the teams that word names.** Owner rules R-a, R-b and
  R-c (help-crm, 7 Sep 2026), from a production regression #706 introduced: turns 1f0428cb
  and 9089ef88 (n8n execs 15501799 / 15502378, `turns-lane3` round 12) said "I want to talk
  to a human" after an unrelated product browse and were answered "Which team should I pass
  this to - purchasing, purchasing certification, customer service, marketing product,
  marketing form, warehouse, marketing promotion or it admin?" instead of being assigned to
  anybody.

  The cause is that AC-815's own premise could not tell its case from that one. Both
  arrived at the lane with `routing.suggested_team` null and a non-default team carried
  from an earlier turn: "escalate to marketing" (must ask) and "I want to talk to a human"
  (must assign) were IDENTICAL in structured state, and D11 forbids the lane reading the
  two messages to tell them apart. So the discriminator moves to the PARSER, where reading
  the message is the job.

  * **Parser contract.** `routing.suggested_team` carries the customer's own team word
    verbatim and lowercased when that word names several catalogue teams or none
    ("marketing", "sales"); an enum member when it names exactly one ("warehouse", "CS");
    and null ONLY when the customer named no team at all. Amended in
    `SEMANTIC_PARSER_PROMPT_SLIM`'s ROUTING section and stated on the wire schema
    (`head/parser._build_json_schema`), which stays `string_or_null` for that reason.
  * **Given** a turn whose parser team is null, **then** the lane assigns and never
    clarifies, whatever an earlier turn carried (R-a). It assigns **the routing chain's own
    result: the CARRIED team when a previous turn had one, else the table default** -
    `llm_team if req_help -> derived -> prior_routing -> DEFAULT_SUGGESTED_TEAM`. Corrected
    after the review of #713 (blocker B3), which measured the code assigning `purchasing`
    on the mt-r2 turn where this AC had said `customer_service`. **The CODE is what stands**
    (captain's ruling, 7 Sep 2026): the carried team is the pre-#706 chain and live parity,
    a product browse routes to purchasing by the owner's own table and "talk to a human"
    straight after it inherits that, and the owner's complaint was the eight-team MENU, not
    the team. Both shapes are pinned in the tests: `Team: purchasing` on the dump shape and
    `Team: customer_service` on a truly cold one. The D1 open-offer premise and the
    `is_escalation_confirmation` short-circuit both stand.
  * **Given** a parser team that is an exact catalogue member, **then** the lane assigns
    THAT team, never an unrelated carried one (R-c).
  * **Given** a parser team that names several catalogue members ("marketing" - the three
    `marketing_*` teams), **then** the lane asks over exactly those members: the sentence,
    the quick replies and the persisted marker are built from one list, so a tap can never
    name a team the ask did not offer. A word that names none asks over the whole
    vocabulary, which is the only honest list when nothing matched.
  * **Given** an answer to that ask, **then** the escalation resolves to the team named
    (R-b) - see AC-822.

  The word-to-catalogue test (`escalation._catalogue_teams`) is D11-clean and inventoried
  in the plan's text-sniffing table: its input is a parser output field and it is matched
  against `SUGGESTED_TEAMS`, our own eight-slug routing vocabulary. Nothing reads `ctx.text`
  or a previous reply.

  **What the owner is signing up for when they move the label.** After the promotion, any
  team word the model returns that matches NO catalogue member yields the eight-team menu:
  `_catalogue_teams` returns `[]` for "human", "support" and "sales" alike, and the honest
  list when nothing matched is the whole vocabulary. A word matching several (the three
  `marketing_*` teams) yields those three, and a word matching exactly one is assigned.

  **How it reaches production.** The prompt registry, not the fallback file: version 1 (the
  live 46,906-character body) carries the `production` label, so migration
  `480_chatbot_parser_team_word` publishes the amended slim text as a new UNLABELLED
  version and the owner promotes it with one label move. Until then production parses
  "escalate to marketing" to a null team and the lane assigns the default - the pre-#706
  behaviour, not the #706 regression, which the lane half removes under either prompt.
  Evidence: `tests/chatbot/test_pass4_item5_no_team_named_keeps_default_routing.py`,
  `tests/chatbot/test_pass4_item1b_marketing_ambiguous_clarify.py`,
  `tests/chatbot/test_s5_escalation_lane.py::TestAnAcceptanceIsNeverAskedWhichTeam`. (H73)
- AC-822 `[BE][T]` **An answer to a team clarify resolves the escalation to that team.**
  Owner rule R-b (help-crm, 7 Sep 2026). Turns 08e74db8 -> 0d7d5a23 (n8n execs 15500464 /
  15500487, `turns-lane3`): "escalate to marketing" was asked which team, the customer
  answered "marketing product", and the answer was never consumed. The parser had it right
  (`_parser_raw.routing.suggested_team: marketing_product`) but stamped `message_type:
  casual` for the bare noun phrase, and `output_exchange`'s routing chain only reads the
  parser's team when `message_type == "request_for_help"` - so the team was discarded, the
  chain fell to the STALE `purchasing` carried from before the escalation was asked for,
  and `route.decide`'s `is_low_signal` answered "Hi! How can I help you today?". The marker
  was WRITTEN (`compile_state`) and read by nobody: `grep '"team_clarify"'` found one
  writer and no reader.

  * **Given** an open `team_clarify` and a turn whose parser named a team, **then** that
    team wins over the carried routing WHATEVER the `message_type` - an answer to a
    question we asked is a continuation of the escalation, not a new turn to classify -
    and the turn re-enters the escalation lane (`message_type` is promoted to
    `request_for_help`, which is `route.decide`'s own escalation arm).
  * **Given** an open `team_clarify` whose parser named NO team, and a reply that equals
    (`strip()` + `casefold()`, exact) one of the quick replies the ask offered, **then**
    it resolves to that team. The ask's teams are persisted on the marker as
    `{team, label}` (`pending.options`, the third `PendingKind` written), so the tap
    resolves against OUR OWN strings and can never name a team the ask did not offer.
  * **Then** the turn takes the human-intervention arm with `Team: <that team>`, sends a
    non-empty reply, and the clarify marker is cleared.
  * **Given** an open clarify and a reply that answers something else, **then** nothing
    resolves and the turn routes normally.

  **Lifetime: one turn, enforced by an explicit exclusion in `_offer_carry`.** The first
  cut of this AC claimed the life was one turn for free, because `_offer_carry` needs a
  non-empty `last_result_set` and "a team clarify has no roster". The review of #713
  (blocker B1) measured that FALSE: the clarify arm carries the PREVIOUS turn's roster
  forward (`compile_state` ~:2075) before it stamps the marker, so production dump
  0d7d5a23 arrives `team_clarify` with fifteen rows behind it, `topic.changed` returns
  False whenever either domain is falsy and a clarify turn's domain is null by
  construction, and the ttl branch is `member_offer` only - so the label was carried
  indefinitely, retyping every later team-naming turn into an escalation and re-emitting a
  marker that outranks `member_offer` and `escalation_offer` and would mask a real offer.
  `_offer_carry` now excludes the kind outright. The member offer's 3-turn TTL is still not
  copied, and now for a reason that holds: a roster stays on the customer's screen, a
  question does not. `offer_is_open` is deliberately not taught this kind either, because
  it is what turns a bare "yes" into an acceptance and "yes" is not an answer to "which
  team".

  **Given** an open clarify and a turn that brings its OWN business question (a
  `business_query`, or any turn with a domain hint), **then** it is ANSWERED, however many
  team words it carries - "which promotions is marketing running" is a question, not an
  answer to "which team".

  **What one turn costs, stated rather than discovered.** Ask on turn 1, ANY turn that does
  not answer it on turn 2, and the answer on turn 3: the marker is already gone, so
  "marketing product" arrives as a bare noun phrase the parser stamps `casual` with no
  domain, and `route.decide`'s `is_low_signal` arm answers it with the canned greeting -
  the exact failure AC-822 exists to fix, one turn later. That is an ACCEPTED trade, not an
  oversight. The alternative is re-arming the marker across turns, which is what blocker B1
  measured going wrong: the label was carried indefinitely, every later turn whose parser
  named a team was retyped into an escalation, and the marker masked real `member_offer` /
  `escalation_offer` markers made afterwards. A question the customer walked away from
  costing them one re-ask is cheaper than a question that never closes. The condition that
  would change this ruling: repeated console turns where a customer answers a team clarify
  a turn or more late. None are recorded today.
  Evidence: `tests/chatbot/test_pass4_item1a_team_clarify_consumed.py`,
  `tests/chatbot/test_s5_escalation_lane.py::TestAnAcceptanceIsNeverAskedWhichTeam::test_an_open_offer_for_the_default_team_still_asks`
  (the marker's options). (H74)
- AC-823 `[BE][T]` **A date-only narrowing of an open member offer keeps the offer's own
  scope.** Owner console pass 4, item 3 (E residue), turns 0eef1cc3 -> 48ee6081, and the
  same shape on live chain 15503158 -> 15503189 where the dropped entity was the CUSTOMER.
  "delivery to hanlim, product srtwc286" matched no order and opened a member offer; "last
  month" then came back "That would search every delivery order we have - I need at least
  one filter to narrow it down", though AC-818's own fix had landed.

  Both of the other candidate causes were measured FINE on the capture and are asserted in
  the test so a future regression there is caught rather than re-attributed: the window IS
  derived (`2026-08-01` / `2026-08-31`, surviving into `output`), and
  `member_offer_filter_modification` IS stamped with `route.decide` already yielding to it.

  **Given** an open member offer and a reply that carries only a date window, **then** the
  narrowed query runs with the offer's own scope (`hanlim` + `srtwc286`) PLUS the window,
  and never the "need at least one filter" refusal.

  The entities are not dropped by the filter-modification arm, which is where the comment
  promising "the window and the ENTITY are kept" sits: the entity-op executor keeps them
  correctly, and `if message_type == "casual" and not engages_offer: entities = []`
  (`output_exchange` ~:2172) wipes them 400 lines ABOVE that arm. That line is right about
  a bare "hi" and wrong about "last month", and it cannot tell the two apart because "is
  this a filter modification of an open offer" is decided downstream of it. So the restore
  is done in the arm that knows the answer, and only when the turn brought no scope of its
  own - a modification that DID name an entity already carries the executor's merge, and
  re-adding the prior set would put back the axis the customer just narrowed.
  Evidence: `tests/chatbot/test_pass4_item3_last_month_under_member_offer.py`. (H75)
- AC-824 `[BE][T]` **A numbered pick over a partial-miss roster keeps the code that already
  resolved.** Issue #708, owner console pass 4 item 4. "SRTKS6091 and SRTKS8091 got stock":
  the first code resolves, the second misses and gets a sibling did-you-mean. On this lane
  the partial miss is claimed by the answer half's `build-suggest-offer` before the tail
  runs, so the turn persists `selection_context: suggest_offer` and `_partial_dym_block`
  never writes `dym_last_result_set` - which is exactly what the numbered did-you-mean
  handler keys on. So "2" fell through to the generic positional arm, whose contract is
  REPLACEMENT, and the turn answered about the pick alone with SRTKS6091 dropped. The
  reviewer measured `reference_target` of null, `"result"` and `"dym"` all doing it, which
  is the tell: the discriminator was which roster happened to be in state, not the parser's
  own tag.

  **Given** an open `suggest_offer` whose `dym_offer.candidates` carry the `for_raw` /
  `for_canonical` linkage, and a numbered pick against that roster, **then** the pick
  replaces the token it was offered FOR, in place, and every other prior entity survives -
  so the scope is the already-resolved code PLUS the pick, and the answer names both.

  `apply_dym_pick` stays keyed on the roster it is handed (no widening of the numbered
  handler's `dym_last_result_set` guard, and no second merge implementation): the positional
  arm hands it the OTHER roster. Two gates, both measured before any pick is applied and
  both over the whole pick set, because threading is what makes a multi-pick accumulate:

  * the linkage must LAND on a prior entity (without it there is nothing that says which
    token the pick answers, and `apply_dym_pick` would prepend, leaving the unresolved miss
    in scope beside its own answer). Capture `parser-15157067` is this shape with a prior
    `SRTWT165-FT` against a `for_raw` of `SRTWT165FT`, so nothing ties and nothing changes;
  * the prior scope must hold something that is NOT a source token. A FULL miss has no
    resolved sibling, so the merge would preserve nothing and the plain replacement is both
    correct and what the corpus records.

  Together they leave every capture byte-equal; one new diagnostic key
  (`suggest_offer_pick_merged`) is registered field-scoped with its class. Closes #708.
  Evidence: `tests/chatbot/test_pass4_item4_issue708_partial_pick_scope.py`. (H76)
- AC-825 `[BE][T]` **A customer the customer already PICKED is not re-resolved from a debtor
  code other accounts share.** Owner console pass 4, item 2. Turns 5bb0426e ("3" ->
  L.A.W. Transport (K.L.) Sdn. Bhd. (SRT)) then cc0075ae / 0d9332a0 ("last month"): the
  reply header names L.A.W. and the rows are SILK CABINETS SDN BHD and a dozen other
  accounts.

  **Measured against the local prod-copy database** (throwaway harness, 7 Sep 2026: the
  dump's own `request_item` through `run_turn` with its `_parser_raw` as the parse and the
  MCP call captured). Both first-pass hypotheses are WRONG and are recorded so nobody
  re-runs them: the date window is NOT dropped (`2026-08-01` / `2026-08-31` survive the
  post-processor, `ctx.parse.output` and `semantic_input`, and every wrong row IS inside
  it), and the resolver mis-ranks nothing - `gate_passed: true`, `require_specific: false`,
  `gate_reason: "ok"`, `unresolved_tokens: []`, full coverage, so `REQUIRE_SPECIFIC_DOMAINS`
  is not involved either.

  What happens: `resolve_entity_body` sends `tokens: ["301-C001"]`, the carried entity's
  `canonical_code`, and OMITS `entity_pins` because the resolver route refuses a pin in AND
  mode by design (`references.py` ~:1652; PR #456's contract is OR-mode only). The uuid the
  pick is made of (`bec07281-...`) is therefore discarded and a debtor code that **99 rows
  share in production** is re-resolved from scratch. The resolver returns 15 of the 99 (its
  own limit) without the picked row, and the gate passes the lot: `cust_pinned` is true, so
  the "which company do you mean" ask is suppressed - the pin stops the QUESTION without
  constraining the ANSWER.

  **Fixed at `gate.py`, not at the resolver route (issue #715, closed console pass 5, 7
  Sep 2026).** AND-mode `entity_pins` stays refused, exactly as PR #456 shipped it
  (`references.py:1650-1655`) - the two reasons the refusal was authored for (no per-token
  view in an intersection, and a zero-intersection AND retry under `force_mode="or"` where
  a pin would suddenly apply) are unchanged and `resolve_entity_body` is untouched. The
  pre-existing "A PINNED PICK WINS OVER FUZZY RE-RESOLUTION" mechanism already re-seated a
  carried pick's uuid into `compatible_entities` (exec 13705266's own fix widened its ENTRY
  gate to `pin_uuids_all`, carried-or-current) but its FILTER half - `pin_types` /
  `pin_bases` / `pin_codes` and `_keep`'s own uuid check - stayed built from `pins` /
  `pin_uuids` (this-turn pins only), so a carried pick with no current-turn pin left
  `pin_types` empty and `_keep`'s first line (`if t not in pin_types: return True`) kept
  every resolver row "untouched": the pin stopped the QUESTION without constraining the
  ANSWER. Widened all four reads to `pins_all` / `pin_uuids_all`, the same set the entry
  gate already used - the resolver's own wrong rows for the shared debtor code are now
  REPLACED by the picked uuid, never merged with it. The unit is now a plain (non-xfail)
  test against `run_gate` directly, at the fix's own layer, with a companion assertion that
  `resolve_entity_body` still sends no `entity_pins` in AND mode (so nobody mistakes this
  for the resolver-route fix that was ruled out). Closes #715. Evidence:
  `tests/chatbot/test_pass4_item2_last_month_keeps_customer_scope.py::TestACarriedCustomerPickIsPinnedAtTheGateNotAtTheResolver`.
  (H77)
- AC-827 `[BE][T]` **`product_attachment` attachment-type resolution is domain-scoped, so a
  migration-seeded internal document type never collides with a customer-facing one.**
  Owner console pass 5, item 1 (7 Sep 2026, prod regression against #713; corrected on
  review round 1, blockers B1/B2/Q1). "send me the photo of SRTWC8517-SH-UF" (turns
  a5317cf4 / b4369aba) stopped resolving deterministically the moment migration
  `485_shipment_line_photo_type.py` seeded "Shipment Line Photo" - it shares the substring
  "photo" with the pre-existing "Product Photos" row, and `gate.py`'s own non-product
  ambiguity handler had no per-type narrowing, so the customer got a silent wrong pick or
  a did-you-mean for a document type they never asked about, never the file that DOES
  exist.

  **Given** a `product_attachment` domain query whose attachment-type token Tier 2
  substring-matches more than one `attachment_types` row, **when** only one of those rows
  is a type at least one PRODUCT actually carries a file of (via `product_attachments`,
  the real linkage - measured against the `attachments` table directly, not a hardcoded
  denylist), **then** only that row is a candidate - Tier 1 exact stays untouched, so a
  customer who types the internal type's own exact code still resolves it, and the domain
  scoping applies only when `domain_hint == "product_attachment"`, leaving every other
  caller of the shared resolver unaffected.

  **Correction, review round 1.** The first cut filtered `attachments.entity_type ==
  'product'`, which is EMPTY in production (a product's file links via
  `product_attachments.product_id -> attachment_id`; `attachments.entity_type` is NULL on
  every one of them) - the fix's own frozenset was empty and "photo" stayed exactly as
  ambiguous as before, a passing test suite that changed nothing live. Corrected to join
  through `product_attachments`; the real candidate set in production is FOUR types
  (Product Photos / Technical Specifications / Certification / Promotion), stated here
  rather than claimed away - "spec" or "cert" can still plausibly substring-match two of
  the four, and `gate.py`'s silent `non_products[0]` pick is UNCHANGED for that residual
  case. This item narrows the pool measurably (two colliding types down to the real four,
  the migration's own collision resolved); it does not make every possible word
  deterministic, and a numbered pick for the non-product ambiguity branch (mirroring the
  product branch's own `specific_options`) is the fix for the residual case, not built
  here - not exercised by any committed red, and shared machinery across every
  non-product entity type (certificates, transporters, ...), a materially wider,
  unmeasured blast radius than this item's own scope.

  Also corrected: `send_attachments` was mis-diagnosed as needing a new "zero-tool fetch"
  (issue #727, opened then CLOSED as invalid). The test harness's `tool_search` stub
  returned `[]`, which makes `run_fetch` return `not_found` before `mcp_call` is ever
  reached - `product_attachment` DOES have a fetch path
  (`crm_master_product_attachments_list`); both tests now cross the real seam (`tool_search`
  faked to a deterministic pick, `mcp_call` the PRODUCTION binding with only
  `MCPRuntimeClient.call_tool` monkeypatched to the STRING shape it actually returns - the
  same recipe `test_s6a_gate_dry_run_and_seams.py::
  TestOwnerRulingATheCustomerPickerReachesTheProductionProbeSeam` already uses) and pass
  for real, no `xfail`.

  Kill test: seed "Shipment Line Photo" without the domain scoping and the ambiguity comes
  back. Evidence: `tests/chatbot/test_pass5_item1_photo_attachment_alias.py`. (H78)
- AC-828 `[BE][T]` **A "last month" that set a concrete date this turn is not wiped by its
  own `broaden_axis`.** Owner console pass 5, item B1 (7 Sep 2026). Turns e4381b0d /
  98526b81 under an open `member_offer`: the parser's SAME output sets
  `date_filter_start`/`date_filter_end` AND `broaden_axis: "date"` in the same breath, and
  the entity-op executor's `reuse` arm read `broaden_axis == "date"` as an unconditional
  "the customer wants the window gone" and nulled the dates it had just been given, before
  ever checking `has_current_date` (computed one line above, read only by the sibling arm).

  **Given** a `reuse` turn whose `broaden_axis` is `"date"` AND whose own parser output
  already carries a concrete `date_filter_start` / `date_filter_end`, **then** the wipe does
  not fire and the turn's own date survives post-process, unchanged from what the parser
  said. Pre-existing (not part of #713's diff, confirmed by line range). Not
  fixture-visible: no captured `output_exchange` fixture in the whole corpus carries
  `broaden_axis: "date"` at all. Evidence:
  `tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::TestB1LastMonthUnderMemberOfferKeepsTheParsersOwnDateFilter`.
  (H79)
- AC-829 `[BE][T]` **A bare reply under an open member offer, that the parser extracted
  nothing from, narrows the carried pair by asking the RESOLVER.** Owner console pass 5,
  item B2 (7 Sep 2026, D11 inventory row R-b; corrected on review round 1, blockers
  B3/B4). Turn 6ea9fd1a: "rpacc" after a working "last month" (hanlim/srtwc286) is never
  extracted as an entity by the parser at all, so the reply kept naming the OLD product
  ("Product: srtwc286") - a genuine gap, not a wrong branch.

  **Correction, review round 1.** The first cut read the bare reply against
  `prev_state.routing_companies[].codes` for a matching CODE SEGMENT and invented a shape
  ("SRTWC286-SH-RPACC") no capture's `routing_companies` carries to make its own test
  pass - measured against the REAL vendored 6ea9fd1a capture, zero codes in it contain
  "rpacc". Production's real shape: TWO products are coded literally `RPACC`
  (cross-company duplicates, same shape `srtwc286` itself is), which only the RESOLVER
  can tell apart from junk text.

  **Given** an open `member_offer`, a turn that named NO entity of its own (parser
  `entities: []`), and a bare reply of four words or fewer, **when** the whole message,
  sent to the resolver as one token (`allowed_entity_types: ["product","customer"]`,
  OR-mode, `fallback_to_all_types: true` - the resolver decides, never a pattern match
  here), resolves to a single entity_type / canonical_code (same-code cross-company
  duplicates collapse to one answer, `gate.py`'s own rule for a normal token), **then**
  the matching half of the carried pair is REPLACED (`canonical_code` set to the resolved
  code, never left null - the MAIN resolve call that follows exact-matches it rather than
  re-searching the bare word freely across the catalogue and silently widening scope) and
  the turn is answered, not reprompted; **when** it resolves to nothing or something
  genuinely ambiguous, **then** nothing changes and the existing pipeline answers
  not-found off the UNCHANGED carried pair - never `offer_hold` (B3's precondition, no
  date and no entity at all, is a different, narrower shape and stays a green regression
  guard). **When the bare token resolves to a CUSTOMER instead of a product**, the SAME
  rule replaces the customer half of the pair instead - the mechanism is symmetric across
  both halves of what a member offer's carried pair can be, not product-only.

  A second-order fix travels with it: the narrowed entity's `current_message: True` left
  `message_type` at `"casual"`, which routed the rest of the turn as an unanswered
  CS-member-offer instead of the answer it had just become; `message_type` now promotes
  to `"business_query"` the same way the pre-existing "bare entity under an open member
  roster" block (AC-816 rule 3) already does. Not fixture-visible: no `sub-resolve-and-gate`
  capture has the precondition shape. Evidence:
  `tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::TestB2ABareProductCodeUnderTheOfferNarrowsTheProduct`.
  (H80)
