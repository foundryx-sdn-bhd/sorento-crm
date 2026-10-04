# PLAN: conversation thread reads local first; Respond.io only for delta sync and backfill

Lane: CHAT-LOCAL-FIRST. Owner approved 30 Sep 2026 ("go for the chat histories lane").
Status: PR open (#1402), built and tested, awaiting review + owner hand test. Track: feature (migration, new external ingest surface: security reviewer runs).
UAC: `chat-local-first-acceptance-criteria.md` alongside.

## 1. Problem

The thread view calls Respond.io on every open and on every 10 s poll
(`conversation_thread_service.fetch_thread_page`, Respond lane first, local lane only on failure).
With ~100 dealers and many salespeople in the portal, Respond.io is the bottleneck and the outage.
`chat_histories` is fed by n8n through Redis and the owner does not trust it alone ("sometimes it
will fail"), so local must be a cache that self-heals, never one that is blindly trusted.

## 2. Facts (read on origin/main d7b3a8cb)

- List is local already: `app/api/v1/public/portal_conversations.py:66` ->
  `portal_conversation_service.list_for_agent`. Kept.
- Thread page: `portal_conversations.py:80` -> `sla_service.py:5931 _thread_page_for_contact` ->
  `conversation_thread_service.py:738 fetch_thread_page`. Respond first (`integration_service.py:491
  list_messages`, 50 per page, 15 s timeout), local on failure (`:769`), every live page written back
  (`:776 _persist_best_effort`). Search is local (`:805`).
- Frontend polls: portal `ConversationThread.tsx` THREAD_POLL_MS 10 s; CRM inbox
  `useConversationsInbox.ts` list 30 s / thread 10 s, 60 s with the live stream.
- Ingest: n8n -> `POST /api/v1/external/chat-history/messages` (`external/chat_history.py:60`),
  idempotent by `(contact_id, message_id)` through the partial unique index
  (`CHAT_HISTORY_DEDUPE_PREDICATE`, `models/chat_history.py:16`). CRM sends mirrored at once
  (`mirror_outgoing_send`, `:647`). Rows lack media URL/type and `sender.source`
  (`_row_to_item`, `:183`).
- Event bus: `conversation_event_bus.publish(EVENT_MESSAGE, contact_id=<respond id>)` pokes open
  threads over Redis pub/sub (`conversation_event_bus.py:244`).
- Scheduler: APScheduler in the worker (`app/scheduler/task_scheduler.py:698 start_scheduler`),
  `ENABLE_SCHEDULER=true` on the batch worker only. Two mechanisms: DB-configured
  `scheduled_tasks` rows through `register_handler` (`:666`), and fixed `scheduler.add_job` ticks
  (`_chatbot_delegated_sweep_tick`, every minute). Per-tick DB session: `scheduler_session`.
- No cache, no 429 / Retry-After handling, no call counter anywhere on the Respond client.

## 3. Design (simplest thing that works)

### R1 Local first (`conversation_thread_service.fetch_thread_page`)
- If the contact has at least one `chat_histories` row on its channel, the page is served from the
  local keyset lane. The Respond client is not called on the request path.
- If the contact has no local row: today's behaviour (live Respond page, then stored), so a
  first open of a never-ingested contact still renders the real thread.
- `before` (scroll past the oldest stored row) when the local lane runs dry: ONE inline Respond
  read older than the oldest stored id fills the rows, then the local page answers (R2).
  `has_more_older` stays true on a short local page until Respond has said the start was
  reached (a contact with three n8n rows and a long thread on Respond must keep offering
  history), except while the last older read failed within the interval.

### R2 Delta sync state (new table `chat_thread_sync_state`, additive)
- One row per `(channel, contact_id)`: `newest_synced_message_id` (the watermark), `oldest_reached`,
  `last_activity_at`, `last_synced_at`, `last_error`, `last_error_at`. The oldest stored id is
  read from the rows (a scroll-back continues from whatever is oldest locally).
- `sync_newer(db, contact, client)`: ONE `list_messages(cursor=f"-{watermark}")` call, where the
  watermark (`newest_synced_message_id`) is the newest id a Respond read confirmed and is advanced
  ONLY by a Respond read: a row n8n, the webhook or a CRM send wrote never moves it, so a message
  one of those lanes missed is still behind it and gets recovered, and rows they wrote after it
  are re-read and have media / sender filled in (review B2 / B3). No watermark yet: the newest
  page with no cursor. Then `persist_messages`, publish `EVENT_MESSAGE` when rows were written,
  queue the phone push for a fresh (under 1 h) incoming row the sync stored first, stamp
  `last_synced_at`.
  `sync_older(db, contact, client)`: ONE call with `cursor=<oldest stored id>`, same store, sets
  `oldest_reached` when fewer than 50 came back.
- Newer: queued from `fetch_thread_page` on an in-process 2-thread pool after the local page is
  built (never blocks the render), at most once per 30 s per contact
  (`SYNC_MIN_INTERVAL_SECONDS`, a constant: one preference does not need a setting), with a
  conditional UPDATE on the state row as the race guard between processes. Older: runs INLINE in
  the scroll-back request that would run past the oldest stored row, because the page being
  asked for is the data being fetched; it is user-driven, never a poll, and the rows it fills are
  kept for ever.

### R3 Direct Respond.io webhook
- `POST /api/v1/public/respond/webhook` (`app/api/v1/public/respond_webhook.py`), under the
  public router because Respond.io cannot send the CRM's `X-API-Key`: auth is the webhook's own
  signature (HMAC-SHA256 of the raw body with `RESPOND_WEBHOOK_SECRET`, header
  `x-respond-signature` or `x-webhook-signature`, hex or base64) or, for a webhook set up with a
  custom header, `X-Respond-Webhook-Secret` equal to the secret. Unset secret = 503. The sandbox
  could not reach `docs.respond.io`, so the header names are the documented convention; the
  setup doc asks the owner to confirm them on the Respond.io page.
- Maps `message.received` / `message.sent` to the same row the n8n ingest writes through one
  shared writer (`app/services/chat_history_ingest_service.py`), so dedupe with the n8n lane is
  the existing partial unique index. Secret in the environment rather than on the workspace
  row: one deployment has one Respond.io webhook today; the second workspace pays for the
  per-row generalisation.
- Owner-side setup steps: `documentation/reference/RESPOND-WEBHOOK-SETUP.md`.

### R4 Background reconcile
- A `scheduled_tasks` row `chat_history_reconcile` (the existing DB-configured scheduler,
  `app/scheduler/task_scheduler.py` `register_handler`, seeded by the migration like
  `chat_message_resolver` in 291). The handler enqueues the run on the `respond_io` RQ queue
  (`app/tasks/chat_reconcile_tasks.py`, one run at a time through a Redis lock) because the
  heartbeat runs handlers one after another and a few hundred Respond calls inline would stall
  every task behind them (review B5). Every 5 minutes (the row's interval), contacts whose
  `chat_thread_sync_state.last_activity_at` is within `activity_days` (task metadata, default 7)
  and whose next read is due (not synced for at least a quarter of the time they have been quiet,
  so a contact idle for 6 days is read every 36 h, one active 10 min ago every tick) get
  `sync_newer`, `concurrency` (metadata, default 2) calls in flight per workspace key, with
  `respond_rate_limit.py` backoff: `Retry-After` honoured, else 1, 2, 4 ... 60 s doubling per
  consecutive 429; 5xx and transport errors arm the same window; a 404 does not.
- `last_activity_at` is stamped by every lane that writes a row (n8n, webhook, CRM send mirror,
  delta reads) and seeded from the last 7 days at deploy, so load scales with contacts that had
  activity, never with viewers. A contact whose every feed is silent for `activity_days` drops
  out of the window until it is opened or a message lands.

### R5 Local gaps (additive migration on `chat_histories`)
- `media_url`, `media_type`, `media_file_name`, `sender_source`, `sender_user_id`. Filled by
  the delta/reconcile path (Respond items carry them), the direct webhook, and n8n when it sends
  them (new optional fields on `ChatHistoryMessageIngestRequest`). `_row_to_item` renders an
  attachment block and `sender.source` from the columns. Old rows fill in as the reconcile
  re-reads them (fill-if-null upsert), no one-shot script.

### R6 Observability
- `respond_call_counter`: every `RespondClient` HTTP call (one `_http()` builder with an httpx
  request hook) increments a per-minute counter (Redis `INCR`, 15 min TTL, in-process fallback)
  and logs at INFO with method and path. Read through
  `GET /api/v1/system/chat-history/respond-io-calls` (`system.chat_history.view`) so the hand
  test can show "open = at most 1, polls = 0".

### R7 UI
- No new screen. Portal and CRM inbox both read through `_thread_page_for_contact`.

### R8 Tests
- `tests/test_chat_local_first.py` (service: local-first, empty-local fallback, delta cursor
  newer/older, sync throttle, `_row_to_item` media/sender), `tests/test_respond_webhook_ingest.py`
  (signature, dedupe against n8n row, media columns), `tests/test_chat_reconcile.py` (429 backoff,
  Retry-After, concurrency cap, activity window).

## 4. Out of scope
- Rate limiting the thread routes per token (ticket in PLAN-sales-conversation-view-30sep.md).
- Configuring Respond.io itself (owner does that from the setup doc).

## 6. Grill (posted as one crew-ask on PR #1402, 30 Sep 2026; answered by crew the same day)

Ruling: "all as recommended (crew decides on the owner's approved design). Q12 is an owner
action - crew will tell him to check the Respond.io webhook signature header when he sets the
webhook up; document the setup steps in the PR."

| # | Decision | Chosen | Premise, with evidence (owner ruling 30 Sep: every statement grounded in code, or marked UNVERIFIED) |
| --- | --- | --- | --- |
| 1 | Webhook secret location | environment variable `RESPOND_WEBHOOK_SECRET` (`app/config.py`, `respond_webhook.py:255`) | Precedent for a per-workspace secret column: `app/models/respond_workspace.py:52-66` (`chatbot_retry_ingress_key_ciphertext`, ruling 5 Sep 2026). CORRECTION: the code supports several workspaces (`respond_workspace.py:69` `is_default`; `integration_service.py:215-216` resolves the contact's own workspace), so with one env secret every workspace's Respond.io webhook must be created with the SAME secret. "One workspace in production today" is UNVERIFIED (no production data here). |
| 2 | Scroll-back past the oldest stored row | ONE inline Respond read in that request (`conversation_thread_service.py` `_fill_older_if_needed`), then the local page | The client stops asking once a page says `has_more_older: false`: `components/common/conversation/useConversationThread.ts:326-327` (`loadOlder` returns when `!hasMoreOlder`) and `:343` (set from the page). A background fill would therefore never be picked up. |
| 3 | Delta cursor | Respond-side watermark (`chat_thread_sync_state.newest_synced_message_id`), advanced only by a Respond read (`chat_thread_sync_service._advance_watermark`) | A CRM send writes its row directly: `conversation_thread_service.py:794` `mirror_outgoing_send` -> `persist_messages` (`:645`); the n8n ingest too (`chat_history_ingest_service.upsert_message_row`). A cursor keyed on the newest stored row would sit past a message n8n dropped one minute earlier (reviewer B2). |
| 4 | Old rows without media | filled as reads reach them; no one-shot script | Ticket R5 wording: "Backfill for old rows comes from the delta/reconcile path, not a one-shot script" (lane brief, recorded in section 3 R5). |
| 5 | Reconcile cadence and window | every 5 min, 7-day window, spacing by quiet time (`_sync_due`, divisor 4), knobs on the task row (`clf_0001` seed metadata) | Arithmetic on the shipped defaults: `batch_limit` 500 x 288 ticks/day = 144,000 reads/day if every active contact were read every tick. "A few thousand a day" with spacing is an ESTIMATE, not measured; it depends on how many contacts are active inside any 10 minutes. |
| 6 | Reconcile execution | enqueued on the `respond_io` RQ queue (`app/tasks/chat_reconcile_tasks.py`), one run at a time | The heartbeat runs handlers one after another: `app/services/scheduled_task_service.py:605` (`for task in due:`) and `:631` (`summary = handler(db, task)`); `form_action_commit` (deferred deletes) is registered on the same heartbeat (`task_scheduler.py` `register_task_handlers`). The "2 minutes inline" figure is an ESTIMATE (0.5 s per call, 500 contacts, 2 in flight); the deferred-delete task's interval was not verified here. |
| 7 | Phone push from the sync lane | yes, for an incoming row under 1 h old (`PUSH_FRESHNESS`), deduped on the message id | Dedupe: `app/tasks/message_push_tasks.py:78` (`dedup_key=row.message_id`). The n8n ingest is the only other push source (`chat_history_ingest_service.announce_new_row`), and it skips a row that already existed, so a message the sync stored first would otherwise never buzz. |
| 8 | Poll throttle | 30 s constant (`chat_thread_sync_service.SYNC_MIN_INTERVAL_SECONDS`), not a setting | Design choice (PRINCIPLES "one preference does not need a table"); no factual premise. |
| 9 | `has_more_older` on a short local page | true until Respond said the start was reached; false while an older read failed within 30 s (`older_read_allowed`) | CORRECTION of the wording "a refresh recovers it": the client sets `hasMoreOlder` only from a `before` page (`useConversationThread.ts:343`) and the two jump handlers (`:483`, `:538`); the newest-window poll never resets it, and it starts true on open (`:203`) and on a thread switch (`:264`). So a throttled short page reads as the conversation start until the thread is reopened or switched, not until the next poll. |
| 10 | Webhook media URLs | only known Respond media hosts kept (`respond_webhook._allowed_media_url` over `media_proxy_service.allowed_hosts`) | The thread renders the URL straight into an image tag: `components/common/RespondChatList.tsx:388-389` (`<img src={item.url}`), while the preview modal goes through the proxy (`:522`), which refuses unknown hosts (`app/services/media_proxy_service.py:97-116`). |
| 11 | Webhook mount | `/api/v1/public/respond/webhook`, own signature auth, 503 while unset | Every `/api/v1/external/*` route requires the API key (`app/api/v1/external/__init__.py`, module docstring: guard on every router). Whether Respond.io's webhook form can send a custom `X-API-Key` header is UNVERIFIED (docs unreachable from the sandbox); the public mount needs nothing from that form beyond a URL. |
| 12 | Signature header names | owner confirms on the Respond.io form at setup time; steps in `documentation/reference/RESPOND-WEBHOOK-SETUP.md` and the PR | UNVERIFIED: the sandbox cannot reach docs.respond.io. The code accepts `x-respond-signature`, `x-webhook-signature` (hex, base64, optional `sha256=`) or `X-Respond-Webhook-Secret` (`respond_webhook.py:60-63`). |
| 13 | Channel normalisation | WhatsApp flavours stored as `whatsapp` (`respond_webhook._normalise_channel`) | Every thread read is scoped to channel `whatsapp`: `conversation_thread_service.py:83` (`ThreadContact.channel` default) and `thread_contact_for` (`:91-110`) never sets another value; the n8n ingest schema defaults to `whatsapp` (`app/schemas/external/chat_history.py:10`). The value n8n sends in production is UNVERIFIED here, but a row on any other channel is already invisible to the thread today. |
| 14 | First open of an empty thread | one live page, stored, then local | Today's order on main: Respond first, local on failure (`origin/main` `conversation_thread_service.py:758-780`). |
| 15 | Call counter | Redis per-minute counter, admin read under `system.chat_history.view` (`app/api/v1/system/chat_history.py:90`) | No metrics exporter in the backend: `requirements.txt` has no prometheus / statsd / opentelemetry entry (grep, 30 Sep). |

## 5. Build notes

- Files: migration `alembic/versions/clf_0001_chat_local_first.py`; models `chat_history.py`
  (5 columns), `chat_thread_sync_state.py`; services `conversation_thread_service.py`
  (local-first `fetch_thread_page`, `_row_to_item` media + sender, `persist_messages` fill-if-null,
  `_fill_older_if_needed`), `chat_thread_sync_service.py`, `respond_rate_limit.py`,
  `respond_call_counter.py`, `chat_history_ingest_service.py`; routes
  `public/respond_webhook.py`, `external/chat_history.py` (refactored onto the shared writer),
  `system/chat_history.py` (counter read); `scheduler/task_scheduler.py` (handler);
  `config.py` (`respond_webhook_secret`); `schemas/external/chat_history.py` (optional media and
  sender fields for n8n).
- Tests: `tests/test_chat_local_first.py` (A, B, E), `tests/test_respond_webhook_ingest.py` (C),
  `tests/test_chat_reconcile.py` (D). 79 tests after the two review rounds. Existing thread, ingest, portal, inbox and
  Respond client suites re-run green.
- No frontend change: both the portal thread and the CRM inbox read through
  `sla_service._thread_page_for_contact`; media renders through the existing
  `describeMessageAttachments` shapes (`message.attachment.{type,url,fileName}`).
- Deviation from the ticket's first wording: the older read on scroll-back runs inline rather
  than in the background (section 3, R2) because a background fill would leave the requested page
  empty and the client with no reason to ask again.
- Review rounds (Opus reviewer + security-reviewer, findings in PR #1402): watermark instead of
  "newest stored row" as the delta cursor, `has_more_older` on short local pages, reconcile
  offloaded to RQ, phone push from the sync lane, OTP template redaction and a body cap on the
  webhook, byte-wise signature compare, older-read throttle after a failure, media host
  allowlist, tighter fill-if-null UPDATE, seconds timestamps.
- Process note: the migration, service and tests were written in one session (cloud sandbox,
  crew lane) rather than by separate tester / coder agents; the reviewer and security-reviewer
  passes ran as Opus agents (Phase 3) before the PR left draft.
