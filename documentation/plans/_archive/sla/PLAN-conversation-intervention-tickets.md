# PLAN - Conversation Intervention Tickets

Status: Phases 1-3 DONE (PR #137 open); defect batch D1-D6 in flight; Phase 4 (parity + liveness) PLANNED 2026-08-14, awaiting user review. S4.9 backend gaps 1-4 CLOSED 2026-08-15 (contract below); FE follow-up (consume them) + gaps 5-6 still open. S4.10 (captain hands-on round 3, 2026-08-16) BUILT - six items, as-built below.
UAC: conversation-intervention-tickets-acceptance-criteria.md (sections J/K/L/M + B3/B4 added 2026-08-14)

## Decision summary

- Conversation SLA rows become per-enquiry intervention tickets: multiple open per contact,
  no contact-level uniqueness, idempotent by `source_message_id`.
- CRM is the worklist ("Mine") and the reply surface. Respond.io = transport only (Option A).
  Respond assignee cosmetic; Respond close events ignored.
- Clocks are event-driven per ticket: first-response stops on a ticket-stamped CRM send
  (fallback: unambiguous user-match from Respond-app replies); resolution stops on manual
  CRM resolve. Contact messages touch no clocks.
- Ticket model stays channel-agnostic (`source_message_id`, message refs in event logs) so
  the chat surface can later swap to the shared-service omnichannel embed without touching
  the ticket layer.
- Worklist = the EXISTING dashboard pending-tasks widget (`MyPendingSLAWidget` +
  `/my-pending`); a ticket opens as an in-place chat drawer, no new page. (Decided in
  lavish review 2026-08-12.)
- Out-of-hours decisioning moves from n8n (Redis queue) into the CRM create path, which
  already owns working-window clock normalization. (Decided in lavish review.)
- Rollout: accept the transition escalation noise - no grace multiplier. Clock pressure
  trains reply-from-CRM. (Decided in lavish review.)
  **SUPERSEDED 2026-08-15 - INERT LAUNCH (user decision, n8n session): production is
  inert at launch. Respond close keeps resolving ALL open tickets for the contact and a
  Respond agent reply keeps marking responded (agent-replied endpoint), both behind the
  n8n flag `close_resolves_tickets` (ON at launch, OFF after staff training). AC-E4 and
  AC-C3's no-Respond-call clause are deferred to the flip. #133 lands after the flip (or
  gated on the same flag). One contact-facing close message per close event, never per
  ticket. See UAC AC-E4 amendment.**

## Current state (verified 2026-08-12)

- `RespondClient` (integration_service.py): `send_message` (text) + `send_template_message`
  only. `list_messages` / `get_message` power the chat panel. No attachment/sticker/reply-to.
- `conversation_sla_tracking` already carries per-row clocks: `due_at`, `due_at_resolution`,
  `is_responded/responded_at/responded_by/response_time`, `escalated_at`. Multi-open needs
  identity + attribution work, not new clock machinery.
- Migration 180: partial unique index `respond_contact_id WHERE is_resolved=false` (conversation
  scope) - the singleton to drop.
- n8n `sub-human-intervention` (rrYXzE61gCNUck_zmXe-G): already passes `message_id` (trigger
  message) to the create endpoint; creates SLA ONLY on the unassigned branch - the
  already-assigned branch is comment-only (the bug).
- Unified composer smart-send (in-window text / out-window template) and `RespondChatList`
  exist and are shared across complaint / stock-inquiry / PR chat panels.

## Research items (resolve during Phase 1, before contract freeze)

- R1. RESOLVED (Respond OpenAPI spec, 2026-08-12): types `text`, `attachment`
  (`image|video|audio|file`), `quick_reply`, `whatsapp_template`. NO sticker, NO reply-to
  parameter. Sticker omitted from composer; reply-to = quote-prefix emulation
  (`buildQuotedReplyText`/`splitQuotedPrefix`); `reply_to_message_id` event-log only.
- R2. Media delivery: Respond fetches attachments by URL - confirm URL lifetime requirements
  vs presigned S3/R2 expiry; CMYK-to-RGB conversion applies (existing rule).
- R3. Who sets `is_responded` today (n8n agent-reply event? backend?) - map the full write
  path before changing semantics.
- R4. RESOLVED: the create path already normalizes clock start to the next working window
  (PLAN-sla-clock-start-next-working-window.md, Phase 2 complete, 43 tests green). Remaining
  work is only exposing `in_working_hours` on the create response for n8n's auto-reply
  choice.

## Phases (three-phase loop)

### Phase 1 - FE prototype (mock data, no backend changes)

- S1.1 Extend `MyPendingSLAWidget` mocks: intervention tickets in all states (fresh,
  near-breach, escalated, responded-awaiting-resolve), incl. two tickets for one contact.
  No new page.
- S1.2 Ticket chat drawer (opens in place from the widget): enquiry header (quoted trigger
  message, team, SLA chips), shared thread (mock `RespondMessageRenderable` fixtures incl.
  attachments/sticker/quote), composer with all send types (text / attach / sticker /
  reply-to), out-of-window template state, resolve confirmation.
- S1.3 Two-tickets-same-contact demo state: same thread, different headers/clocks.
- S1.4 Document the API contract at the top of the ticket service file: create/my-pending/
  detail/send/resolve request+response shapes, send-type enum (bounded by R1),
  `in_working_hours` create-response field (R4).
- Verify via Playwright MCP through the sidebar. Screenshot golden path + edge states.
- Gate: prototype rendered via lavish for user review; proceed autonomously unless
  feedback arrives.

### Phase 2 - BE wiring + tests (TDD)

- S2.1 Migration: add `source_message_id` (Text, nullable), backfill from `message_id`;
  drop the migration-180 contact-singleton index; add partial unique index on
  `source_message_id` WHERE open AND conversation scope. Alembic single head check.
- S2.2 Create endpoint semantics: multi-open create; idempotency by `source_message_id`
  (keep `already_active` response shape so the n8n contract stays stable); stop refreshing
  `message_id`; keep the response fields n8n templates read (`initiated_at`, `due_at`,
  `due_at_resolution`, `assigned_to`).
- S2.3 Audit one-open consumers (UAC F1): `get_tracking_by_contact_*`, thread-assignee
  endpoints, MCP tools, `sync_assignee_from_respond` retirement. Each gets an explicit
  multi-row semantic + test.
- S2.4 Send capability in `RespondClient`: attachment (upload via CRM storage -> URL),
  sticker, reply-to per R1; outbox `integration_log` on success+failure with
  actually-attempted payload; out-of-window template fallback reused from smart-send.
- S2.5 Ticket-context send endpoint: stamps ticket id, sets responded fields on that ticket,
  writes event log with message ref (naive-UTC gotcha: wrap datetimes with `_to_aware_utc`).
- S2.6 Fallback attribution: inbound agent-reply path (per R3) marks responded only on an
  unambiguous (contact, user) -> single-open-ticket match.
- S2.7 `/my-pending` + drawer detail + resolve endpoints wired; FE off mocks.
- S2.8 Notifications: assignment notify via existing matrix + deep link.
- S2.9 Out-of-hours in create path (R4): clock start normalized to next working window,
  `in_working_hours` in the response; `initiated_at` keeps the real request time.
- Tests: pytest (create idempotency, multi-open, index, attribution, fallback ambiguity,
  send outbox on failure, consumer audit semantics - all self-seeded, Postgres only);
  vitest (listing/detail/composer states); Playwright e2e (mine -> ticket -> send -> resolve
  round-trip; real media fixtures).
- Gate: all suites green; browser-verified against prod build before handoff.

### Phase 3 - Review + n8n cutover

- S3.1 `/code-review` + `/codex-review` (cross-model) on the branch; fix findings.
- S3.2 n8n coordination (peer session via SendMessage): always-create on both branches,
  remove the out-of-hours Redis branch (`sorento-respond-assignee-queue`) - auto-reply
  choice driven by the create response's `in_working_hours`; stop message_id refresh
  reliance; stop resolve-on-close calls; keep comment/tag optional. Contract doc = S1.4 +
  UAC sections A + H. Verify with a test-guard run (`is_test` path exists).
- S3.3 Cutover order (refined with the n8n peer session, 2026-08-12): deploy BE/FE first
  (old n8n calls remain valid - A1/A2 backward compatible; out-of-hours branch keeps
  working until flipped) -> verify old flow green -> flip DURING working hours with
  `LLEN sorento-respond-assignee-queue == 0` verified (or right after a drain tick;
  stranded queue items = lost enquiries) -> publish reworked sub to rrYXzE61gCNUck_zmXe-G
  -> post-flip canary with the dev contact (900000008), user-gated. FLIP SCOPE also
  includes `respond-close-convo` (-WkzJMQZHmsFQm6A2abLJ): gate/unpublish its
  conversation-scope resolve leg (it resolves + unassigns + messages the contact on
  Respond close events - AC-E4 noise/loop source). Post-flip tidy: remove the dead
  drain leg in `schedule-working-day-detection` (ss9S83XF7ZtmnaUyFtYZc). Post-flip BE
  hardening follow-up: reject conversation-scope is_resolved from API-key principals
  (defense-in-depth; cannot ship pre-flip without breaking the old contract).
  n8n create node moves to `POST .../conversation-sla-tracking/integration` (the bare
  POST / lacks in_working_hours in its response_model); body drops policy_id/current_tier
  (ignored), keeps agent_code/team_set_code/contact_phone_number, sends
  assigned_to_id + source_message_id (string) + source_message_text + message_id.
  AC-A5 note: the flow's is_test guard run is fail-closed proof ONLY (it short-circuits
  all branch logic); functional verification = pin-data matrix on the fork
  (vUfFUDjLAuMaeQE6), 5 cases incl. the assigned-branch create and already_active retry.
  No deploy without explicit go.
  CRITICAL (peer recon 2026-08-12): `respond-close-convo` resolves via RAW SQL with NO
  is_resolved filter - one close event would mass-resolve OPEN siblings post-flip
  (AC-E4 violation). Its edit is hard-mandatory at flip: If TRUE -> unassign, delete
  tracking-update + event-log nodes, keep unassign + is_human_intervened clear; closing
  message KEPT but gated on "contact has no open tickets" (empty open-rows GET). #133
  (BE API-key resolve rejection) ships immediately post-flip, same day.
  USER DECISION AT FLIP APPROVAL: keep or kill the contact-facing "conversation closed
  and resolved" auto-message (currently kept, gated).
  resolved_by pollution check: clean (zero non-UUID / orphan values on dev DB).

## Phase 4 - Omnichannel parity + liveness (planned 2026-08-14, dogfooding feedback)

Grounded on three read-only investigations (send-path diagnosis, n8n trigger trace with
live execution payloads, Respond API v2 capability inventory). Defect batch D1-D6
(attachment isinstance, /conversation threadpool, template sync-for-drawer, duplicate
refetch + poll fallback + silent-degrade guard, window-state TTL cache, widget
Reassign/Extend on ticket rows) runs ahead of this phase as straight fixes against the
existing UAC.

### S4.1 Human-send signal (UAC J) [BE coder + n8n peer]

- CRM side: call `enqueue_crm_chat_outbound_webhook(...)` from the human ticket-drawer
  send path (`send_chat_message_for` return / `send_ticket_message`), best-effort
  post-commit, payload per the established `crm_chat_outbound_webhook.py` builder
  (single-element array, `source: "User"`, real Respond user id via
  `_webhook_agent_respond_id`, `crm.business_id` = tracking id). Covers text, attachment,
  template sends. Never from bot paths.
- Ingest idempotency (AC-J5): `POST /api/v1/external/chat_history/messages` upserts on
  Respond `messageId` - kills the double-mirror (webhook lane + Respond trigger lane)
  at our boundary. Check existing PR-notification sends for historical duplicates.
- n8n peer edit (AC-J4): wire the webhook lane into the `If source == "User"` branch so
  `Update a Contact` (is_human_intervened) + ht lane arm for CRM sends. Safe: the
  webhook carries only CRM human traffic. Also fixes what a manual Respond reply already
  gets. Peer session owns the edit; verify with a pin-data run before publish.

### S4.2 Live thread via server push (UAC K) [BE coder + FE coder]

- Transport: SSE endpoint on FastAPI (`/api/v1/sla-management/conversation-events/stream`),
  Redis pub/sub bridge so API + worker processes can publish. No websocket infra.
- Publishers: chat ingest (inbound message -> event keyed by respond_contact_id),
  ticket create/clock mutations (-> event keyed by assignee user id).
- Subscribers: drawer (refetch thread on contact event; open drawers only - AC-K2),
  pending-tasks widget (refetch on assignee event - AC-K3). 10-15s poll stays as
  fallback (already added in D4b) and is suppressed while the stream is healthy.
- Idempotent by construction: events carry ids; FE refetches rather than appending
  pushed payloads (AC-K4).

**Shipped BE contract (2026-08-15, backend half; the FE subscriber slice builds
against this).**

- Channel: `sorento:conversation-events:v1`, one Redis pub/sub channel, override per
  environment with `CONVERSATION_EVENTS_CHANNEL` (settings
  `conversation_events_channel`). Namespaced because one broker is shared with RQ and
  with every local worktree.
- Endpoint: `GET /api/v1/sla-management/conversation-events/stream?contacts=<id>[,<id>]`
  (JWT, same principal as the sibling sla-management routes; 401 without one; max 25
  contacts). `text/event-stream`, chunked, `X-Accel-Buffering: no`.
- Frames: `event: ready` on connect (the FE's cue to refetch), then
  `event: <type>` with a JSON `data:` line, plus a `: keep-alive` comment every 25s.
  Types: `message`, `ticket_created`, `ticket_updated`.
- Payload, exactly five keys, no content ever:
  `{"type","contact_id","user_id","entity_id","ts"}`.
 - `contact_id` = the **Respond.io contact id** (`respond_contacts.respond_io_id`),
    NOT the internal `respond_contacts.id` UUID. DEVIATION from the bullet above,
    deliberate: the chat ingest receives that id verbatim (so nothing is resolved on
    the hot path) and the drawer already holds it as `respond_io_id` from
    `GET .../{tracking_id}/ticket`, so the FE has the key it must pass in `?contacts=`
    without a new field and without a UUID in a query string.
 - `user_id` = `users.id` whose worklist changed. `entity_id` = the tracking id for
    ticket events, null for `message`. `ts` = UTC ISO-8601 with `Z` (transport clock,
    not a domain datetime).
- Filtering is server-side: a client receives events where `user_id` is its own OR
  `contact_id` is in its `?contacts=` list. Nothing else leaves the process.
- Stateless: no Last-Event-ID, no replay. A reconnect resubscribes; the gap is
  covered by the refetch on `ready` plus the drawer's 10-15s poll fallback.
- Publishers wired: chat ingest (new row only, never the AC-J5 dedupe path), ticket
  create (not the idempotent retry), the shared update path (resolve / respond /
  assignment), first-response stamp, reassign, escalate, extend. Reassign and escalate
  poke BOTH the old and the new owner; the update path snapshots the assignee before
  applying, because resolving a conversation ticket unsets `assigned_to_id`. Form-SLA
  rows never reach the channel (AC-F3).

**FE half BUILT 2026-08-15. Status: DONE (AC-K1, AC-K2), vitest green, no browser
verification yet.**

- **Transport: `fetch` + `ReadableStream`, NOT `EventSource`.** The route authenticates
  with `Depends(get_current_user)`, whose token comes from `oauth2_scheme` or
  `extract_token_from_request` - and that helper reads the `Authorization: Bearer`
  header ONLY (it returns None for cookies, no route accepts a `?token=` query param,
  and the NextAuth session cookie is an encrypted JWE FastAPI cannot read anyway).
  `EventSource` cannot set request headers, so it would hit this endpoint
  unauthenticated and take a 401 forever. Reading the stream through `apiFetch` is
  therefore the only way in, and it keeps the surface on the normal layering: the
  cached/deduped JWT mint, the base-URL rewriting and the session-revoked interceptor
  all come for free. The cost is parsing the wire format ourselves, which is ~30 lines
  with its own unit tests. Recorded here because "why not EventSource" is the first
  question any reader will have. If the backend ever grows a `?token=` param or a
  readable cookie, `EventSource` becomes viable and this can be revisited - nothing
  above the service module would change.
- Shape: `services/conversationEventsService.ts` (frame parser + stream pump, contacts
  capped at the backend's 25, `ready` handled separately from data frames) ->
  `components/common/conversation/useConversationEvents.ts` (one stream per surface,
  contact set keyed on sorted contents so a re-render does not reopen it, callbacks in
  refs, 1s..30s doubling reconnect backoff reset by each `ready`, `connected` flag).
- Subscribers: the ticket drawer (`ticket.respond_io_id`, enabled only while open) and
  the inbox `ConversationThreadPane` (the selected contact). Both turn a poke into
  `invalidateQueries` and nothing else. `ready` is treated as a poke too, since a
  reconnect may have missed events. The poll stays as the fallback lane and relaxes
  from 10s to 60s while `connected` (belt and braces, not the mechanism).
- **A NOTE is poked as `message`**, not as a comment type: `ticket_comment_service._publish`
  publishes `EVENT_MESSAGE`. So a `message` event refreshes the notes query as well as
  the thread. There is no `comment.*` type on the wire and the FE must not wait for one.
- The inbox LIST is refreshed by a poke for the selected contact (its snippet and ticket
  counts just moved) but is not itself subscribed - see AC-K3 below.
- **AC-K3: the pending-tasks widget stays on polling, deliberately.** The stream filters
  server-side on `?contacts=` and caps that list at 25, while the worklist spans every
  pending ticket the user holds across an unbounded number of contacts - subscribing it
  would either truncate silently or need a user-keyed subscription the component cannot
  express. The ticket pokes it would want (`ticket_created` / `ticket_updated`) DO reach
  the open drawer, and the drawer's `onSent` / `onResolved` / `onReassigned` callbacks
  already reload the list after every action taken from it. Reason is restated in the
  code at the poll site so nobody "fixes" it later.

### S4.3 Internal comments with @mention (UAC L1-L3) [BE coder + FE coder]

- Model: `conversation_ticket_comments` (id, tracking_id FK, author_id, body,
  mentioned_user_ids uuid[], respond_comment_mirrored bool, created_at). CRM DB is the
  source of truth (Respond has no comment read-back API - verified).
- FE: comment mode toggle in the drawer composer (visually distinct, yellow-note style),
  @ typeahead over CRM users, inline render in the thread. In-app notification + deep
  link for mentioned users (existing notification service).
- Mirror OUT (L2): best-effort `POST /v2/contact/{id}/comment` with `{{@user.<id>}}` for
  mentioned users that map to Respond users. Mirror IN (L3): n8n forwards
  `comment.created` webhooks to a new ingest route; dedupe by (contact, created_at,
  text) since Respond gives no comment id in the webhook (verify payload at build time).

**n8n half of L3 is UNBUILT (verified by the n8n session 2026-08-15: no Respond
`comment.created` trigger exists on the instance, no workflow references
`POST /api/v1/external/chat-history/comments`).** It is its own future n8n slice
(plan -> build -> test -> promote, user-gated), NOT part of the S3.2 flip: dedicated
small workflow, first step = verify the comment event's exact trigger-enum name on this
instance (names differ from Respond's docs). CRM half is shipped: `comment_id` REQUIRED
as the idempotency key, X-API-Key, contact_id (respond_io_id) or phone_number.

BUILT 2026-08-15 (CRM half). The as-built wire contract, and the four places it deviates
from the sketch above, are written under AC-L3 in the UAC file (that is the contract;
this bullet is the design note). Summary of the deviations: the table is
`conversation_ticket_comments` with a NULLABLE `tracking_id` because Respond comments are
contact-scoped; dedupe keys on Respond's own `comment_id` rather than
(contact, created_at, text), and that key is REQUIRED (Phase-3 review, 2026-08-15:
without it a retry duplicated the note irrecoverably) with the unique index as the
real arbiter; the mention notification is the in-app lane only; and
comments are never written into `chat_histories` - the drawer merges the two streams at
render time. FE: a "Reply | Comment" mode switch in the drawer, an amber
`InternalCommentComposer` (shared, in `components/common/conversation/`) with an @
typeahead over `userSelectService`, and amber "Internal" bubbles interleaved by
`RespondChatList`'s new `comments` prop.

### S4.4 Snippets + variables + emoji + AI assist (UAC L4-L5) [FE coder + BE coder]

- `message_snippets` table + admin CRUD (UI-visible, workspace-global v1), "/" picker in
  the composer, `$` variables resolved from ticket context at insert time.
- Emoji picker: client-side component, insert at cursor.
- AI assist: existing CRM AI assistant drafts into the composer input, grounded on the
  visible thread; no new AI surface.

BUILT 2026-08-15 (code + tests; awaiting tester/review). The as-built wire contract and
its seven deviations are written under AC-L5 in the UAC file (that is the contract; this
bullet is the design note). The three that changed the design rather than filling it in:

- **Variable resolution moved to the BACKEND.** `GET .../message-snippets/select` returns
  `resolved_body` alongside the stored `body`, so the preview and the delivered text come
  from one implementation and the fallbacks ("Hi there" for a nameless contact) cannot
  drift. The FE only inserts.
- **`$ticket_ref` is derived (`ENQ-<last 6 hex>`), not a new column.** A real reference
  sequence would need a backfill for every existing tracker to buy nothing a customer can
  tell apart.
- **The AI draft raises instead of degrading.** `product_spec_understanding` falls back to
  a deterministic reading; there is no deterministic way to write a sentence to a customer,
  so a missing/failing/empty model is a 503 with a readable message.

All three composer features are opt-in props on the SHARED
`SharedConversationComposer`, so the complaint / stock-inquiry / PR panels inherit them by
passing a prop and are unchanged until they do. Emoji reuses `emoji-picker-react`, already
a dependency, behind `next/dynamic` with `ssr: false`.

### S4.5 Post-resolve reassurance + close semantics (UAC M) [FE coder + BE coder + n8n peer]

**LAUNCH (2026-08-25, user direction: "no longer inert, launch on deploy").** Supersedes the
inert-launch procedure below. (1) The close webhook URL is now a `system_settings` column
(`n8n_close_convo_webhook_url`, migration `420_close_convo_webhook_setting`), set on
Settings > Integrations ("Ticket resolve close webhook URL"); the env var is only the
fallback. (2) The CRM lane is built INSIDE `respond-close-convo` (-WkzJMQZHmsFQm6A2abLJ)
as a draft: plain `Webhook` (POST `/webhook/crm-close-convo-c1f4e0b2-5a7d-4b8e-9c3f-2e6a8d0b4f17`)
and the Respond trigger both feed `close-envelope` (one shape, every downstream node reads it
by name), then `close-seen` (Redis GET `close:evt:<event_id>`) -> `close-decide` ->
`close-gate?` -> `close-stamp` (Redis INCR, TTL 600 s) -> the existing chain. Gate, fail
closed: Respond lane runs for `closedBySource` `user` and `n8n` (today's behaviour kept for
bot closes), SKIPS `api` (that is the CRM's own RQ Developer-API close echoing a resolve the
webhook lane already announced) and anything unknown; CRM lane runs only for
`event == ticket_resolved && closedBySource == crm`; a repeated `event_id` within 10 min is
skipped as `duplicate-event`. Deviation from AC-M3 as written: `n8n` closes stay allowed, not
just `user`, so the "close resolves all open tickets" behaviour bot flows rely on is unchanged.
Not verified live: the MCP publish was blocked by the tool permission gate, so the draft is
unpublished; rollback after publish = republish version `4a2e963d`. Cutover: deploy CRM ->
set the URL in Settings -> publish the draft -> resolve a ticket for a test contact and check
the execution took the `crm` lane once and the following Respond `api` close was skipped.

**LAUNCH PROCEDURE (user decision via n8n session 2026-08-15, inert launch):** the CRM
close lane is a SEPARATE n8n workflow (inactive at launch); `respond-close-convo`
(Respond trigger, `eventSource: ["user"]`, resolve-all under the inert semantic) stays
active. Prod `N8N_CLOSE_CONVO_WEBHOOK_URL` stays UNSET until switchover (unset = warn +
skip, resolve unaffected, no outbox row - no retry backlog into an inactive webhook);
set it at switchover, then deactivate the Respond lane and activate the CRM lane
(rollback = re-activate). Accepted tradeoff: during launch a CRM-screen resolve sends
the customer no closing message. Retries are bounded anyway (`max_retry_allowed=3`,
exponential backoff, then parked failed). LOOP GUARD (code-verified 2026-08-15): the
close webhook fires ONLY for user-origin resolves; API-key-principal `PUT` resolves
(that is n8n's own resolve-all under the flag) never fire it, else one Respond close
would echo back as a second closing message. n8n additionally guards one closing message
per contact per 60s.

- Drawer stays open post-resolve in a Resolved state (badge, composer disabled, thread
  readable); "recently resolved" affordance links to the SLA tracking listing filtered
  to the contact.
- Close signal (AC-M3 REVISED, user direction 2026-08-14): mirror the respond-send-user
  dual-trigger pattern. n8n peer adds a plain-webhook trigger to respond-close-convo;
  CRM resolve calls it directly with a deterministic payload (tracking id, contact,
  real resolved_by, category/summary), best-effort post-commit. The Respond-trigger
  lane gains a `closedBySource == "user"` gate so the CRM's API close cannot double-run
  the flow; manual Respond-app closes keep working unchanged. Kills the
  literal-"undefined" resolved_by risk by construction.
- AC-M4 DECIDED: keep the contact-facing close message, gated on no-open-tickets.

Built 2026-08-15 (code + tests; awaiting tester/review). **As-built wire contract -
the n8n peer builds the receiving lane from THIS text.**

- **Trigger URL**: backend env `N8N_CLOSE_CONVO_WEBHOOK_URL` (settings field
  `n8n_close_convo_webhook_url`, env fallback; NOT a `system_settings` column - it is
  deployment wiring, same family as the secret). Unset = the call is skipped with a
  warning and the resolve is unaffected.
- **Method / headers**: `POST`, `Content-Type: application/json`, plus
  `X-CRM-Webhook-Secret: <N8N_CRM_WEBHOOK_SECRET>` - the SAME secret machinery as S4.1
  (AC-J6): resolved at send time, never persisted on the log row, absent (with a
  warning) when the env is unset so the n8n gate stays closed rather than the resolve
  being blocked.
- **When**: post-commit on a CRM resolve of a CONVERSATION-scope tracker, gated on
  "the contact has no other OPEN conversation-scope ticket" (the same
  `_has_other_open_conversation_siblings` gate the pre-existing RQ Respond close
  uses). Form-SLA rows never fire it. An already-resolved re-resolve short-circuits
  before it, so a retry cannot re-announce a close. Best-effort: any failure logs and
  never fails the resolve.
- **Body** (a single JSON OBJECT, not an array - this lane is ours, not a Respond
  webhook mirror):

  ```json
  {
    "event": "ticket_resolved",
    "event_id": "<uuid5(NAMESPACE_URL, '<tracking_id>:<resolved_at>')>",
    "source": "User",
    "closedBySource": "crm",
    "tracking_id": "<conversation_sla_tracking.id>",
    "contact": { "respond_io_id": "10025531", "phone": "+60123456789" },
    "resolved_by": {
      "respond_user_id": "971724",
      "crm_user_id": "<users.id>",
      "name": "Agent One",
      "display_name": "Agent One"
    },
    "resolved_at": "2026-08-15T09:00:00Z",
    "team_name": "Customer Service - Tier 1",
    "category": "Resolved",
    "summary": "Resolved from Sorento CRM SLA tracking.",
    "open_ticket_count": 0,
    "crm": { "business_table": "conversation_sla_tracking", "business_id": "<tracking_id>" }
  }
  ```

 - `event_id` is the idempotency key (hardening 1): identical across retries of the
    same resolve. The n8n lane must be safe to receive it twice.
 - `closedBySource` is a closed enum (`"crm" | "user" | "api"`); the CRM only ever
    emits `"crm"`. The Respond-trigger lane's gate fails CLOSED on unknown values
    (hardening 2).
 - `resolved_by.respond_user_id` is `null` when the CRM user has no Respond mapping,
    or when the mapping was filled with a CRM `users.id` UUID (never leaked as a
    Respond user id). `display_name` is ALWAYS a readable string for the
    contact-facing message: resolver name -> team name -> `"Customer Service"`
    (hardening 3). `team_name` is snapshotted BEFORE the resolve blanks
    `agent_id` / `team_set_code`.
 - `resolved_at` is aware UTC ISO-8601 with `Z` (transport clock, not a domain naive
    datetime).
- **Outbox**: every attempt writes an `integration_log` with
  `integration_channel = "n8n_crm_close_convo"`, `business_table =
  "conversation_sla_tracking"`, `business_id = <tracking_id>`,
  `external_reference = <respond_io_id>`. Delivered = `sent`; a transport failure
  parks it back on `pending` with `error_message` + `next_retry_at` (shared
  `IntegrationLogService` vocabulary).
- The pre-existing best-effort RQ Respond conversation-close job is UNCHANGED and
  still fires on the same gate: the webhook is additive (transport tidy-up stays,
  the flow signal is new).
- **LOOP GUARD, actually built 2026-08-15 (it was NOT in the code when the launch
  procedure above claimed it).** `_notify_close_convo_webhook_best_effort` fired on any
  resolve, principal-agnostic, so: contact closes in Respond -> n8n's
  `respond-close-convo` lane resolves the ticket via `PUT
  /conversation-sla-tracking/{id}` with the API-key principal -> our webhook fires back
  at n8n -> n8n sends the customer a SECOND closing message. `closedBySource` could not
  catch it (the payload said `"crm"`, truthfully); the missing fact was WHO asked for
  the resolve. `update_tracking` now takes `resolve_origin` (default `"user"`);
  `PUT /{tracking_id}` passes `"api_key"` when
  `current_user["auth_method"] == "api_key"`, and the unauthenticated
  `POST|PUT /integration/{tracking_id}` lane always passes it. The webhook fires only
  for `"user"`. The gate is the PRINCIPAL, not the route - a human resolving through
  `PUT` still fires it. The RQ close job is untouched by this (idempotent transport
  tidy-up, not a customer message). Pinned by
  `sorento_crm_backend/tests/test_close_convo_webhook_origin.py`; n8n's own
  one-message-per-contact-per-60s guard stays as the belt to this braces.

Deviations from the bullets above, and why:

1. **The "recently resolved" filter keys on `resolved_by`, not the assignee.** A
   conversation resolve NULLs `assigned_to` / `assigned_to_id` by design, so an
   assignee-filtered "what I resolved" link returns an empty list (pinned by test).
   The listing gained three server-side params instead:
   `contact` (Respond.io id / CRM respond_contacts.id / phone, resolved through the
   existing `resolve_internal_respond_contact_id`), `is_resolved` (bool) and
   `resolved_by` (users.id / respond_user_id / email / the literal `me`, expanded to
   the caller in the route so no UUID rides in a user-visible URL). All three also
   feed `/neighbours`, so the detail pager walks the same filtered set. An
   unresolvable `contact` or `resolved_by` returns an EMPTY set, never the unfiltered
   one.
2. **No "today" boundary on the widget link.** It is `is_resolved=true&resolved_by=me`
   sorted `resolved_at desc`, so the most recent resolution is the first row. A hard
   day boundary would hide a ticket resolved at 23:50 the moment the clock rolls, for
   no gain over ordering.
3. **The drawer's "View history" carries the Respond.io contact id** (phone as the
   fallback), never the CRM `respond_contacts.id` UUID - the drawer already holds the
   former, and a UUID in a visible URL is what the no-UUIDs rule forbids.

### S4.6 Inbound quote rendering (UAC L6) [FE coder]

- `message.received` webhook `replyTo` object -> ingest stores quoted context -> thread
  renders "replying to" block above the message body. Outbound quoting stays the
  existing prefix emulation (no API support - verified).

**Amended 2026-08-16 (captain's call): the outbound prefix emulation is REMOVED.**
Respond exposes no reply-to for API sends (403 on the raw-payload path), so our
">"-quoted line was theatre that read on screen like a real quote. Deleted: the
per-bubble Reply affordance in `RespondChatList`, the composer's quoted-reply banner
and its `replyTo` / `onClearReplyTo` props, `buildQuotedReplyText` /
`splitQuotedPrefix` / `splitMessageQuote` in `lib/respondIoChatRender.ts`, the shared
`components/common/conversation/quotedReply.ts` helper, and the `reply_to_message_id` /
`reply_to_excerpt` fields on `interventionTicketService` + `conversationsInboxService`.
The backend keeps its optional audit-only parameters (removing them is a separate
backend change); the FE no longer sends them. Everything BELOW this note - the inbound
`replyTo` ingest, `describeQuotedContext`, `QuotedContextBlock` - is unchanged and
still shipped: that quote is a real reference the contact made. One visible
consequence: a historical outgoing message that was sent with a ">" line now shows
that line as ordinary text, which is exactly what the contact received.

Built 2026-08-15 (code + tests; awaiting tester/review). **No migration: the columns
already existed.** `chat_histories.reply_to_message_id` + `reply_to_message` predate
this feature (they were added for the chatbot's numbered-option resolution), and the
external ingest already accepted and upserted both. A new JSON column would have been a
second home for the same two values.

- **Wire shape (all three lanes agree)**: a thread item carries
  `replyTo: { messageId, traffic?, message: { type?, text? } }` or nothing. The Respond
  lane passes Respond's own object through verbatim; the local lane rebuilds it from the
  two columns; the FE reads it with `describeQuotedContext`.
- **Gap closed on the S4.8 backfill**: `persist_messages` stored the quoted ID and DROPPED
  the quoted text, so a page served by the fallback lane would have rendered an empty
  "replying to" block. It now stores the excerpt too, via the same `_respond_item_text`
  the message body uses - which means a quoted PHOTO backfills as `[image] sink.jpg`
  rather than as nothing.
- **FE (SHARED `RespondChatList`, so complaint / SI / PR / portal threads inherit it)**:
  `describeQuotedContext` in `lib/respondIoChatRender.ts` + a `QuotedContextBlock` above
  the bubble body. It is a BUTTON that scrolls to the quoted message (reusing the S4.8
  bubble ref map) and flashes it for ~1.8s, but ONLY when that message is in the loaded
  window; out of window it renders as plain text, because a control that cannot do what
  it offers is worse than a label. A quote block deliberately does NOT fetch the page
  containing its target - that is the search jump's job (which replaces the window), and
  doing it from a passive quote would move the thread under a reader who only glanced.
- **Precedence**: when a message somehow carries BOTH a structured `replyTo` and our own
  ">" prefix, the structured one wins and the prefix is not rendered a second time.
  Outbound quoting is untouched (`buildQuotedReplyText` / `splitMessageQuote`).
- **S4.3 comments are unaffected**: Respond's comment webhook has no `replyTo`, and
  comments live in their own table, so nothing there could drop it.

### S4.7 Attachment filename fidelity + in-thread preview (UAC D5-D6) [BE+FE coder]
(added 2026-08-15 from captain hands-on testing)

- D5 root cause: `upload_chat_attachment` key is `{table}/{id}/{uuid}_{name}` - uuid glued
  into the URL basename, and WhatsApp names the delivered document from the basename.
  Fix: uuid as its OWN path segment (`{table}/{id}/{uuid}/{name}`), clean basename;
  verify both R2 CDN and S3 signed-url branches.
- D6: attachment bubbles open the EXISTING CRM attachment preview surface on click; no
  new viewer.
- Live-verified 2026-08-15: delivered payload URL basename is the clean filename
  (canary_d5_stock.xlsx), Respond accepted. Gotcha discovered: Respond 400s some
  extensions ("attachment url is not valid") via its own allowlist - a .txt was
  rejected while the URL served 200; xlsx/jpg fine. Route degrades gracefully
  (per-file failed entry).

Built 2026-08-15 (code + tests; awaiting tester/review):
- D5 `upload_chat_attachment` key is now `{table}/{id}/{uuid}/{name}`; `chat_attachment_basename`
  collapses whitespace to underscores (stem+extension kept). The S3 branch already
  percent-encodes via the CloudFront signer; the R2 branch encodes the key at the call
  site (`quote(key, safe="/")`) rather than changing `get_cdn_base_url`, which every
  stored attachment row's URL format depends on. No Respond fileName field exists (R1).
- D6 handled in the SHARED `RespondChatList` (so complaint / SI / PR / portal threads get
  it too, no fork): an attachment bubble with a url is a button opening
  `components/common/AttachmentPreviewModal` with `{id, name, url}` items - no
  `downloadUrl`, since chat media has no `attachments` row (which also keeps the
  token-authenticated portal thread working: the modal's authenticated byte-fetch is
  never reached). The modal's fallback slide now offers the CDN url as a download when
  there is no same-origin route, so an unpreviewable type is no longer a dead end.

### S4.8 Thread scroll-back + message search (UAC L7-L8) [BE coder + FE coder]
(added 2026-08-15 from captain hands-on testing)

- L7: cursor pagination on scroll-up; prepend with scroll anchoring, dedupe on
  message_id.
- L8: server-side search over `chat_histories` (ILIKE v1), match list + jump-to-message
  + up/down navigation + highlight. Reference: foundryx-shared-service
  `service_backend/modules/omnichannel` chat search (studied before building).

Built 2026-08-15 (code + tests; awaiting tester/review):

- **Lane order flipped vs the original bullet** (see the revision note on AC-L7).
  Respond.io's `cursorId` walk is the primary pagination lane because it is the
  system of record AND returns the full message object, so a scrolled-back page
  renders exactly like the live window; `chat_histories` is the fallback lane and
  the search substrate. Verified against the live API 2026-08-15: `cursorId=<id>`
  returns OLDER messages newest-first, `cursorId=-<id>` returns NEWER messages
  oldest-first, and `GET /message/{id}` supplies the anchor for an `around` jump.
- BE `app/services/conversation_thread_service.py`: `fetch_thread_page`
  (before / after / around + limit, **items always oldest-to-newest**,
  `has_more_older` = "the page came back full") and `search_thread`. The local lane
  is keyset on `(sent_at, id)`, NOT `created_at`: `created_at` is ingest order, so a
  backfilled 2026-05 message written today would sort as the newest thing in the
  thread. Every Respond page is written into `chat_histories` best-effort (explicit
  "which ids do we already hold" probe first, because the dedupe unique index is
  PARTIAL on `created_at >= 2026-08-14` and a pre-cutover row is invisible to
  `ON CONFLICT`). The read marks nothing seen and touches no window cache.
- Routes: `GET .../{tracking_id}/conversation/page` and `.../conversation/search`.
  Migration 327 adds `CREATE EXTENSION IF NOT EXISTS pg_trgm` + a
  `gin (message gin_trgm_ops)` index so the leading-wildcard ILIKE is indexable.
- FE lives in the SHARED layer, not the drawer: `useConversationThread`
  (window + dedupe + fetch-sequence guard + search cursor + around-jump) and
  `ConversationSearchBar`, both under `components/common/conversation/`, plus new
  optional props on the shared `RespondChatList` (`onLoadOlder` / `hasMoreOlder` /
  `isLoadingOlder` / `atConversationStart` / `searchController` / `highlightTerm`).
  The chat list owns the scroll container, so the top-threshold trigger, the
  scroll-anchored prepend (`useLayoutEffect`, `scrollTop += growth`), the bubble ref
  map and the pin-to-bottom suppression live there. Complaint / stock-inquiry / PR
  panels inherit the feature by passing the same loaders; passing none leaves them
  byte-for-byte as they were.
- A search jump to an unloaded message REPLACES the window with the `around` page
  (a spliced window would render a silent gap), and `resetKey` discards the window
  when the drawer swaps tickets in place, so one contact's history can never leak
  into another's thread.

### S4.9 Conversations inbox + drawer ergonomics (UAC N1-N8) [BE coder, then FE coder]
(added 2026-08-15 from captain dogfooding round 2; user direction: "fix my feedback and
do whatever the reviewer flagged, then proceed with codex review, push and PR")

Backend (first):
- Permissions `sla_management.conversations.view` / `.reply` + grant sweep migration
  (view <- every role holding `conversation_sla_tracking.view`; reply <- roles holding
  the ticket send/reply-equivalent permission, else same set as view - decide from the
  registry, record).
- Inbox list endpoint: `GET /sla-management/conversations?tab=mine|mentioned|unassigned|
  all&q=&cursor=&limit=` - keyset on (last_message_at desc, contact id) derived from
  `chat_histories` MAX(sent_at) per contact (materialised as a lightweight query; if the
  aggregate is too slow at scale, a `respond_contacts.last_message_at` column maintained
  by the ingest is the follow-up, note it). Row = contact ref (respond_io_id, phone,
  name), last message snippet + time, open ticket count, my-open-ticket id (for reply
  stamping), unread not in v1.
- Contact-keyed thread endpoints mirroring the ticket-keyed ones: page / search / media
  under `.../conversations/{contact_ref}/...`, gated by the view permission (NOT
  can_user_act). Ticket-keyed `.../{tracking_id}/media` proxy too (drawer). Media
  proxy: allowlisted hosts only (R2 CDN, CloudFront domain, Respond media hosts),
  streams bytes with content-type + content-disposition, viewer-scoped.
- Inbox reply endpoint: `POST .../conversations/{contact_ref}/reply` (reply permission)
  -> stamps sender's own open ticket if any (reuse send_ticket_message), else the
  unstamped human send path (same webhook signal + outbox).
- Notes list by contact for the inbox thread.

**Backend as built 2026-08-15 (this IS the FE contract - build from this text).**

Everything is under `/api/v1/sla-management`. Permissions:
`sla_management.conversations.view` (every read below) and
`sla_management.conversations.reply` (the reply). Migration `330_conversations_inbox`
creates both and copies each grant set from
`sla_management.conversation_sla_tracking.view` (9 roles each on the dev snapshot); the
drawer's send route has no slug of its own, so `.reply` reuses `.view`'s holders.
`contact_ref` is a Respond.io contact id, a `respond_contacts.id` OR a phone number -
whatever the row gave you; unresolvable -> 404. Every read that a 403 could gate is a
403 (the permission is a real gate, not an existence secret); an unknown contact is 404.

1. `GET /conversations?tab=&q=&cursor=&limit=`
 - `tab`: `mine` | `mentioned` | `unassigned` | `all` (default `all`); unknown -> 400.
 - `q`: contact name or phone fragment, ILIKE, `%`/`_`/`\` literal.
 - `limit`: default 30, max 100 (over -> 422). `cursor`: opaque, from `next_cursor`.
 - Response:
     ```json
     {
       "items": [{
         "contact_ref": "10025531",          // pass this to every /{contact_ref}/... call
         "respond_io_id": "10025531",
         "phone": "+60123456789",
         "name": "Aisyah Rahman",
         "last_message_at": "2026-08-15T02:11:03",   // naive UTC; render via formatDateTimeInMalaysia
         "last_message_snippet": "Yes please send the quote",  // <=160 chars, whitespace collapsed
         "last_message_direction": "incoming",       // "incoming" | "outgoing"
         "mentioned_at": null,                        // non-null ONLY on tab=mentioned
         "open_ticket_count": 2,
         "my_open_ticket_count": 1,
         "my_open_ticket_id": "<uuid>"                // null unless my_open_ticket_count == 1
       }],
       "next_cursor": "b64...|null",
       "has_more": true,
       "limit": 30,
       "tab": "all",
       "query": ""
     }
     ```
 - `last_message_*` can be null on the ticket tabs (a contact with an open ticket but
     no stored message). Ordering: `mentioned` is newest-NOTE first, the rest are
     newest-MESSAGE first. Paginate by passing `next_cursor` back verbatim; stop when
     it is null.
2. `GET /conversations/{contact_ref}/page?before=&after=&around=&limit=`
 - At most ONE of before/after/around (two -> 422). `limit` 1..200, default 50.
 - Response is BYTE-IDENTICAL to
     `GET /conversation-sla-tracking/{tracking_id}/conversation/page` (same service
     core), so `useConversationThread` works unchanged with contact-keyed loaders.
3. `GET /conversations/{contact_ref}/search?q=&limit=` - identical to the ticket-keyed
   `/conversation/search` (`limit` 1..200, default 100).
4. `GET /conversations/{contact_ref}/comments` - a plain array of the same
   `TicketCommentResponse` the drawer already renders, oldest first. Wider scope than
   the ticket-keyed list ON PURPOSE (AC-N3): contact-scoped notes AND every
   conversation ticket's notes for that contact.
5. `GET /conversations/{contact_ref}/media?url=<absolute url>` and
   `GET /conversation-sla-tracking/{tracking_id}/media?url=<absolute url>` - streams
   the bytes with the upstream content-type, `Content-Length` when the upstream
   declared one, and `Content-Disposition: inline; filename="<basename>"`. A host off
   the allowlist -> 400, over 50 MB -> 413, upstream 4xx/5xx passed through / 502.
   Use this as `AttachmentPreviewModal`'s `fetchBytes` for chat-media URL items.
6. `POST /conversations/{contact_ref}/reply` - JSON `{"text": "..."}` or
   `multipart/form-data` with `text` + repeated `files`, exactly like the drawer's
   `POST /conversation-sla-tracking/{id}/ticket/send`. Response is the drawer send's
   shape plus one field:
   ```json
   {
     "sent_as": "text",             // "text" | "template" | "attachment"
     "rendered_text": "...",
     "flattened": false,
     "window": {"open": true, "expires_at": null},
     "attachments": null,            // or {"delivered": ["a.pdf"], "failed": {...}|null}
     "stamped_ticket_id": "<uuid>"   // null = unstamped human send
   }
   ```
   `stamped_ticket_id` is non-null only when the sender held EXACTLY ONE open ticket for
   that contact (then it is that ticket's first response, identical to a drawer send).
   Zero or several -> unstamped: the message still goes, the outbox is still written and
   n8n still gets the human-intervention signal, so the FE should NOT block on it -
   surface it only if it wants to say "not attached to one of your enquiries".
   Empty text and no files -> 400.

Frontend (second, same worktree, after BE lands):
- New page `sla-management/conversations`: two-pane, left list (tabs, search, cursor
  "load more"), right = shared RespondChatList + composer (reply if permitted, notes
  always). Sidebar entry. Reuses useConversationThread with contact-keyed loaders.
- Drawer: Resolve + Reassign into the header action group; enquiry-quote click ->
  scroll/around; Reassign dialog shows Respond-linked badge + filter.
- Global AI-assistant launcher -> slim bottom-right edge tab (component under
  components/common or wherever the FAB lives), no overlap with drawer controls.
- AttachmentPreviewModal: `fetchBytes` override for URL items routed through the media
  proxy (Excel inline + Download work).
- "Chat Records" on the SLA-tracking detail replaced by the shared panel; complaint /
  SI / PR listed as follow-up if their panel is separate.

**Frontend as built 2026-08-15. Status: DONE (AC-N1..N8), vitest green, no browser
verification yet** (the orchestrator owns the prod build + the 375px / 1280px pass).
Per-AC detail lives under each AC in the UAC file; what belongs here is the shape and
the follow-ups.

Shape: `app/(protected)/sla-management/conversations/` = page + `ConversationsInbox`
(two-pane shell, responsive stack) + `ConversationListPane` + `ConversationThreadPane`
+ `hooks/useConversationsInbox` + `services/conversationsInboxService` (the as-built
contract above is restated at the top of that file). `RespondChatList` grew two
optional props and nothing else changed for its existing five callers: `mediaProxy`
(AC-N4, byte loader; without it there is no `downloadUrl` and the surface is
untouched) and `(focusMessageId, focusNonce)` (AC-N6, caller-driven scroll target).
`useConversationThread` grew `jumpToMessage` next to the search jump. The legacy
`SlaTrackingConversationPanel` is deleted in favour of `SlaTrackingChatRecords`.

Follow-ups this slice deliberately did NOT do (each is a backend gap, not an FE
choice):
1. ~~**Contact-keyed note CREATE.**~~ **CLOSED 2026-08-15 (backend)** - see the
   gap-closure contract below. Only the LIST was contact-keyed, so the inbox
   disabled Note unless the viewer held exactly one open ticket for the contact.
2. ~~**Contact-keyed 24h window / chat-template read.**~~ **CLOSED 2026-08-15
   (backend)** - the inbox composer could not show the out-of-window template
   inline the way the drawer does; the backend still smart-sent it.
3. ~~**`POST /conversations/{ref}/reply` carries no `reply_to_message_id` /
   `reply_to_excerpt`.**~~ **CLOSED 2026-08-15 (backend)** - the inbox thread
   offered no per-bubble Reply-quote.
4. ~~**`.../conversation-sla-tracking/visible-users` does not return the Respond
   linkage.**~~ **CLOSED 2026-08-15 (backend)** - the badge read the shared
   user-select endpoint, gated by `user_management.users.view`, and degraded to
   no-badge-no-filter when that 403'd.
5. ~~**The S4.2 SSE subscriber is still backend-only.**~~ **CLOSED 2026-08-15
   (frontend)** - see the S4.2 "FE half BUILT" note. The open thread now subscribes
   with `?contacts=<respond_io_id>` and both polls became fallbacks (30s list
   unchanged, thread 10s -> 60s while connected). The transport is fetch +
   ReadableStream, not `EventSource`, because the endpoint takes a Bearer header.
6. **Complaint / stock-inquiry / purchase-request "Chat Records" are separate
   components** (`ComplaintConversationPanel`, `StockInquiryConversationPanel`,
   `PurchaseRequestConversationPanel`) and still render the legacy bubble list. Moving
   them onto the shared thread is its own slice: each has form-SLA chrome around the
   panel, and their contact is reached by entity, not by tracking id.

**Backend gap-closure as built 2026-08-15 (gaps 1-4). This IS the FE contract for
the follow-up FE slice - build from this text.** Everything stays under
`/api/v1/sla-management`; `contact_ref` resolves exactly as it already does (a
Respond.io contact id, a `respond_contacts.id` or a phone number; unresolvable ->
404). Tests: `tests/test_conversations_contact_note_create.py` (15),
`tests/test_conversations_contact_window_template.py` (14), the quoted-reply and
picker-linkage additions in `tests/test_conversations_contact_endpoints.py` (20
total) and `tests/test_sla_takeover_reassign.py` (17 total).

1. **`POST /conversations/{contact_ref}/comments`** - permission
   `sla_management.conversations.view` (a note is internal staff context, so a
   view-holder may annotate; assignment has nothing to do with it). Body
   `{"body": "...", "mentioned_user_ids": ["<user uuid>", ...]}`, response `201`
   with the SAME `TicketCommentResponse` the drawer renders (`tracking_id` is
   `null` - the note belongs to the contact). Blank body -> 422; a mentioned user
   who no longer exists -> 400 (the whole note is refused rather than silently
   dropping the mention). The row is contact-scoped, i.e. the same shape a
   Respond-ingested note has, so it renders in `GET /{ref}/comments` AND in every
   open ticket drawer for that contact with no new rule.
   `TicketCommentService.create_for_contact` shares mention validation, the
   persist, the in-app mention notification, the Respond mirror (+ its
   `integration_log` outbox row on success AND failure) and the live-thread poke
   with `create_comment`. The only difference is the mention deep link: a drawer
   note links to its ticket (`/?ticket=<id>`), a contact note links to
   `/sla-management/conversations?contact=<contact_ref>` with
   `source_entity_type="respond_contacts"`. ~~**FE follow-up:** the inbox page does
   not read that query param yet.~~ **DONE 2026-08-15 (frontend).** The inbox reads
   `?contact=` ONCE on mount (a landing instruction, not live state - re-reading it
   would fight the user's next click) and opens the thread on that ref immediately,
   since the ref is all any contact-keyed endpoint needs. The list row replaces the
   stub when the page carrying it arrives, and only ever replaces a STUB - the list
   re-reads every 30s and swapping a user-picked selection on each of those would
   churn the thread pane for nothing. Landing tab is Mentioned: that tab is
   newest-NOTE first, so the contact the notification names is on page 1, which is
   what makes the upgrade actually happen. An unresolvable / off-page ref still
   opens the thread, it just keeps the stub (no name, no Respond id, so no live
   stream until it upgrades). Note mode itself is now unconditional.
2. **`POST /conversations/{contact_ref}/reply`** now also accepts
   `reply_to_message_id` and `reply_to_excerpt`, on BOTH the JSON body and the
   multipart form, exactly like `POST /conversation-sla-tracking/{id}/ticket/send`.
   They are audit-only and never reach Respond (it has no reply-to parameter):
   the composer composes the ">" quote prefix into `text`, which is sent verbatim.
   On the STAMPED lane they land on that ticket's `response` event log reason
   (`CRM reply (sent_as=text, reply_to_message_id=..., quoted_reply=true)`); on the
   unstamped lane there is no ticket to record them against, so they are accepted
   and dropped rather than refused. Response shape is unchanged.
3. **`GET /conversations/{contact_ref}/window`** - permission `.view`. Returns
   ```json
   {"window": {"open": false, "expires_at": null},
    "chat_template": { /* ChatTemplatePreview, or {configured:false, reason:"no_contact"} */ }}
   ```
   the SAME two objects the drawer reads off `GET .../{tracking_id}/ticket` (one
   service core, `_window_and_template`, answers both - pinned by a test that
   compares the two responses). Feed it straight into
   `SharedConversationComposer`'s `windowStateOverride` as
   `{closed: !window.open, template: chat_template}` and drop the
   `{closed:false}` hard-code. NOT DB-only: the window is a live Respond call.
   **DONE 2026-08-15 (frontend)**, with one judgement call worth naming: until
   that live read lands the composer assumes the window is OPEN, because
   showing the template form to someone who is in fact in-window makes them fill
   a template for nothing, whereas the reverse costs one smart-sent message the
   backend was going to convert anyway.
   **`POST /conversations/{contact_ref}/template-message`** - permission
   `.reply`, body `{"template_id": "<uuid>", "params": {"1": "...", ...}}` (the
   entity routes' body; a `tracking_id` in the body is ignored here because the
   ticket is derived, not chosen). Synchronous. Response:
   ```json
   {"ok": true, "queued": false, "template_name": "...", "rendered_body": "...",
    "stamped_ticket_id": "<uuid>|null"}
   ```
   Stamping follows the reply's rule exactly: exactly one open ticket of the
   sender's for that contact makes this that ticket's first response (clock +
   AC-J human-send signal, identical to the drawer's template send); zero or
   several leaves it unstamped against the contact. Unknown template -> 404,
   missing parameter -> 400 (both DB-only, before anything is sent), Respond
   refusal -> 502 with the outbox row still written and NO response stamp.
   Delivery goes through the new shared
   `respond_chat_template_service.deliver_manual_template_now`, which the entity
   chat panels' ticket lane now calls too. **FE DONE 2026-08-15:**
   `SendTemplateDialog` grew an optional `sendAdapter` (surfaced on
   `SharedConversationComposer` as `templateSendAdapter`) instead of being
   forked - its default path posts to the ENTITY chat route via
   `chatBase(entityType)`, which throws outright for a contact-keyed surface.
   Every existing caller is untouched.
4. **`GET /conversation-sla-tracking/visible-users`** rows gain
   `respond_linked: boolean` (additive; the route has no `response_model`, and a
   route-level test pins that the field reaches the wire). True when the user
   carries a REAL Respond mapping, resolved by the same
   `usable_respond_user_id()` the send path uses - so a CRM `users.id` parked in
   `respond_user_id` reads as UNLINKED, and the badge can never promise a linkage
   the send would find unusable. ~~**FE follow-up:** drop the `ReassignDialog`'s
   second, best-effort call to the `user_management.users.view`-gated user-select
   endpoint.~~ **DONE 2026-08-15 (frontend).** `VisibleUser` carries
   `respond_linked`, the dialog reads it off its own rows, and
   `hooks/useRespondLinkedUsers.ts` is DELETED (it had exactly one importer).
   The badge and the filter are now unconditional - there is no longer a
   "linkage unknown" state to degrade into, so a workspace where nobody is
   linked shows the filter emptying the list rather than hiding itself.

### Phase 4 cross-model review (codex, 2026-08-15)

Two read-only codex passes over e1db0e6e6..HEAD (backend, frontend). Findings, verified
against the code before acting:
- FE `useConversationEvents`: cleanup during a backoff wait left the suspended `run()`
  alive after unmount (cleared timer, promise never resolved) - FIXED (wake the sleeper
  on cleanup). FE `conversationEventsService`: stream reader lock not released on
  close - FIXED (`releaseLock` in `finally`).
- BE `conversation_thread_service` has-more flags use `len(rows) == limit`, so an
  exact-size final page reports one more page and the FE makes one extra fetch that
  returns empty (then shows "Beginning of this conversation"). ACCEPTED as-is: no wrong
  data, one spare round-trip in a rare case; the "next only when page full" rule is
  deliberate (inherited from the reference design). Revisit only if it shows in use.

### Main merged in (2026-08-16) - single alembic head restored

The PR merge commit was failing CI at the `bootstrap_env` stamp step with
"Multiple heads are present": the branch was 182 commits behind main, and main's
`358_scm_po_spo_history_aliases` and this lane's `330_conversations_inbox` are
independent heads. `git merge origin/main` produced exactly ONE textual conflict
(two added imports in sla_service.py, both kept) and `359_merge_tickets_main`
joins the two lineages (no DDL). Heads after merge: 1.

Codex reviewed the merge commit and raised 4 findings, all the same class and all
REFUTED with evidence rather than patched: main's new `voided_at` / `open_tracker_scope()`
invariant ("unresolved no longer means open") does not reach the conversation lane,
because the sole writer of `ConversationSLATracking.voided_at` is form_sla_service
pinned to FORM_SLA_TYPES through the negated conversation scope (UAC F4b), and the
open-ticket unique index predicate carries the conversation scope too. The invariant
is now documented at `conversation_tracking_scope()` so the next reviewer reads it
instead of re-deriving it. If anything ever voids a conversation row, every caller of
that helper needs `open_tracker_scope()` beside it.

Post-merge verification: backend Phase-4 scope 527 passed; full vitest 420 files /
3743 passed; app imports clean.

### Phase 4 execution order (user-approved 2026-08-14)

S4.1 and S4.2 first (they close the two live operational gaps: bot does not pause, thread
does not update). S4.3-S4.4 next (parity). S4.5-S4.6 last (polish). ALL slices are built
in full with equal diligence - the order is sequencing, not priority-cutting (user:
"you must build all with diligence").

Revised 2026-08-15 after captain hands-on testing: S4.7 (defect-class, small) runs
immediately, then S4.8, then S4.3 -> S4.4 -> S4.5 -> S4.6 as approved.

**n8n change cycle (binding, user 2026-08-14): plan -> build -> test -> promote, and the
promote step ALWAYS needs the user's explicit call** - per the sorento-crm-n8n working
convention: build on a fork/staging copy, verify with pin-data runs and a dev-contact
canary, publish to the live workflow only on the user's go. Applies to every n8n edit in
this phase (respond-send-user webhook-lane wiring, respond-close-convo webhook trigger +
closedBySource gate, flip-window edits). Decisions locked: AC-M4 keep-the-close-message;
comment mirroring to Respond best-effort; outbound reply-to confirmed impossible
(custom_payload 403 on our channel, tested 2026-08-14).

### S4.10 Hands-on round 3 (captain, 2026-08-16) [FE coder]

Six items off live testing. All frontend bar one additive serializer field.

1. **Who handled it, on the detail page.** The header line now reads
   `Policy ... - Current Tier N - Assigned to X`, and `Resolved by X` once the row is
   resolved (resolve NULLs `assigned_to_id` by design, so "Assigned to" is empty exactly
   on the rows a reader opens to find out who answered). ONE rule in
   `lib/slaHandler.ts`, used by the header AND the listing's "Assigned To" cell (which
   existed but showed "-" on resolved rows). The helper refuses id-shaped values, because
   the backend falls back to the raw `resolved_by` column when no user matches.
2. **"Chat Records" IS the drawer's panel.** New `TicketConversationPanel` holds the
   thread, the interleaved notes and both composers; the drawer and
   `SlaTrackingChatRecords` each mount it. The detail page had been passing a shorter
   prop set and so had no attachments, no "/" snippets, no emoji, no AI assist, no manual
   template send, no real 24h-window state and no note composer. Anything that is a
   property of the TICKET (header, chips, resolve, reassign, extend, the quoted enquiry)
   stays with the caller; the drawer drives the enquiry-quote jump through a
   `(messageId, nonce)` prop, the same idiom `RespondChatList` already takes. Fallback
   kept for the surfaces where `GET /{id}/ticket` 404s (a form-scope tracker, a viewer
   outside the ticket's act-scope): thread + the shared entity chat send, as before.
3. **Paste an image into the composer.** `SharedConversationComposer` stages
   `clipboardData.files` through the SAME `addFiles` path as Attach (no parallel lane);
   images render as thumbnails whose click opens the existing `AttachmentPreviewModal`
   on a local object URL (revoked on change/unmount), each with a remove control. Text
   plus attachments still go in one send. No captions - Respond.io has none.
4. **Scroll-to-latest everywhere.** The "jump to latest" control was rendered only for a
   detached (search-jumped) window. It is now a round down-arrow above the composer
   whenever the reader is more than about one viewport from the bottom, on every thread
   surface; detached windows re-attach through `onJumpToLatest`, attached ones simply
   scroll. One control, and it keeps the "N new" badge.
5. **Extend, from the drawer.** A gear/overflow menu beside Resolve holds Extend and
   opens the worklist's own `ExtendDueDialog` (same slug `...conversation_sla_tracking.extend`,
   same resolved / has-resolution-deadline gate). A successful extend refetches the
   ticket so the chips show the new deadline. The menu is where any further ticket action
   goes; the header takes no more buttons.
6. **An extended or near-due row is visible.** (a) Countdown urgency is three steps, not
   two: neutral, amber under `AT_RISK_MS` (raised from 15 minutes to 4 hours, about half
   a working day - the old value only fired when it was already too late to act), red
   overdue. The same threshold drives the plain "Respond by / Resolve by" text on
   non-ticket rows, which had no amber at all. (b) A row whose deadline was moved carries
   an "Extended" marker (`x2` when repeated) titled with the new due date, driven by the
   EXISTING `conversation_sla_tracking.extension_count`, newly emitted by
   `list_my_pending`, `list_team_pending` and `get_ticket_detail` (additive; those routes
   have no `response_model`). NOT built, deliberately, and still the captain's call: a
   scheduled pre-due reminder job. The scheduler still only scans OVERDUE rows and
   escalates after the fact.

Not verifiable without a browser (the captain was mid-test on :3000, so no login was
taken): the visual placement of the round jump button against a real composer, and the
thumbnail strip at 375px.

### S4.11 Voice messages in the composer (captain request, 2026-08-16) [FE coder]

UAC AC-L9. Frontend only - no backend, no migration, no new dependency.

**What shipped.** A "Voice" mic button beside Attach in the Reply toolbar (`components/
common/conversation/SharedConversationComposer.tsx`), gated on the same
`attachmentsEnabled` capability the Attach button uses - so it exists exactly where the
surface can send files to the contact, and never in Comment mode, whose composer is a
different component with no send path to the contact. Click starts, click stops: a mouse
cannot hold-to-talk comfortably, and a toggle is less code. While recording the toolbar
shows a pulsing dot, an mm:ss counter, Stop and a discard control, and Send is disabled
so Enter cannot fire a message that leaves the clip behind. `MediaRecorder` +
`getUserMedia` live in `components/common/conversation/useVoiceRecorder.ts`, which caps
the recording at 2 minutes (stopping itself and KEEPING what it captured) and releases
the microphone on stop, on discard, on an unmount mid-recording and on a recorder that
refuses to stop. Feature detection runs after mount (the server has no `MediaRecorder`),
and an unsupported browser or a denied microphone leaves the button disabled with a short
`title` reason plus a one-off toast, no prose on the surface.

**No new plumbing.** The finished clip is a `File` named `voice-message-<yyyymmdd-hhmmss>.
<ext>` staged through the SAME `addFiles` the Attach button and paste use, so the send,
the outbox row, the per-file failure handling and the thread bubble are untouched. The
staged strip gained an audio branch: an object URL and an `<audio controls>` chip so the
clip can be heard before it goes out (the existing image-thumbnail lane is unchanged, and
the lightbox still takes images only).

**Container, settled empirically (2026-08-16), not assumed.** Preference order is
`audio/mp4` -> `audio/ogg;codecs=opus` -> `audio/webm;codecs=opus`. A real clip in each
container was pushed through the composer's own upload helper
(`respond_chat_template_service.upload_chat_attachment`) and `RespondClient.send_attachment`
to the one outbound-enabled contact, with an `integration_log` written for each. Respond's
API accepted all three (200 + messageId). Its own file inspection, read back from
`GET /v2/contact/{id}/message/list`, flagged ONLY the webm clip:
`"isFileTypeSupported": false` with the mime sniffed as `video/webm`; the mp4 clip read
`audio/x-m4a` and the ogg clip `audio/ogg; codecs=opus`, neither carrying the flag. That
lines up with Meta's documented audio formats (aac, mp4, mpeg, amr, ogg-opus; no webm),
which is why webm is last-resort rather than the default Chrome would otherwise give us.

**Still open (needs the 24h window).** All three attempts then failed DELIVERY with
"WhatsApp Business: Messaging window has been closed for this contact. The message could
not be sent." (last inbound 2026-08-14T13:11Z), so the window - not the container -
stopped them, and delivery-level proof per container is outstanding. Re-run the same
three sends once the contact has messaged in. If it turns out the captain's browser can
only record webm AND webm will not deliver, the options are the captain's call: remux the
Opus packets from webm into an Ogg container in the browser (no transcoding, no ffmpeg),
or refuse to record on that browser. Neither is built.

Not verifiable without a browser: recording itself. jsdom has no `MediaRecorder` and no
microphone, so the vitest suite fakes both - it pins the wiring (staging, cancel, cap,
mic release, disabled states), not the audio.

## Execution model (per PRINCIPLES.md - named executor per step)

Run through `/feature`. Fable (main session) is the brain and stays autonomous end-to-end:
planning/grilling/contract decisions/phase gates stay in the main session; Phases 1-2
implementation is delegated to the `coder` agent in a worktree; tests to the `tester`
agent; review runs `reviewer` + `/code-review` + `/codex-review` (cross-model second
opinion); n8n changes coordinate with a peer session via SendMessage. Slice to GitHub
Issues with `/to-tickets` (bodies link back to these files - the files stay the contract).
Main already fast-forwarded to origin/main (af2dad409). User checkpoints happen through
lavish artifacts, not blocking waits.

## Risks / open questions

- Secret ops (binding, from n8n rev-2 manifest 2026-08-15): n8n retains the plaintext
  X-CRM-Webhook-Secret verbatim in execution runData on every authenticated call and
  nothing in n8n prevents it - treat the secret as disclosed to anyone with n8n access;
  never dump a webhook-lane execution with the Webhook node included; rotation order is
  digest SET first, then CRM env change, and rotation is complete only when
  pre-rotation executions age out of retention. Durable fix = HMAC-over-body -
  deliberately out of S4.1 scope; becomes a named follow-up if this secret ever gates
  more than the one bot-pause lane.

- Q1. Respond API may not support sticker or reply-to sends (R1) - composer scope shrinks,
  not blocks.
- Q4. `assigned_to` is Text + `assigned_to_id` FK both exist - tickets should write both
  (existing convention) - confirm no consumer reads only one.
- (Resolved) Q2 rollout noise: accepted, no grace period. Q3 placement: existing
  pending-tasks widget + chat drawer.
