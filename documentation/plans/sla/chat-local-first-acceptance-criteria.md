# UAC: chat local first (lane CHAT-LOCAL-FIRST)

Plan: `PLAN-chat-local-first-30sep.md`.

## A. Local first
- AC-LF1 A thread page for a contact with stored rows is served from `chat_histories` and makes
  zero Respond.io HTTP calls on the request path.
- AC-LF2 A thread page for a contact with no stored rows fetches one live Respond page, stores it,
  and returns it (today's behaviour).
- AC-LF3 A Respond.io failure (timeout, 5xx, 429) on the AC-LF2 path still returns the (empty)
  local page with `source: local`; the request never 500s.
- AC-LF4 The 10 s poll (portal thread, CRM inbox thread) costs zero Respond.io calls while the
  contact was synced within the last `SYNC_MIN_INTERVAL_SECONDS` (30 s, a constant).

## B. Delta sync
- AC-DS1 Opening a thread schedules at most ONE Respond.io delta call (newer than the Respond-side
  watermark, the newest id a Respond read confirmed) after the local page has been returned.
- AC-DS1b A row n8n, the webhook or a CRM send wrote never moves the watermark, so a message one of
  those lanes missed is recovered by the next delta read, and rows they wrote after it are re-read
  (that is how AC-RF3 fills them).
- AC-DS2 New rows from a delta fetch are upserted by `(contact_id, message_id)` and a `message`
  event is published on the conversation event bus for that contact.
- AC-DS3 Scrolling past the oldest stored row schedules at most one Respond.io call older than
  the stored oldest id; when fewer than a full page returns, `oldest_reached` is set and no more
  older calls are made for that contact.
- AC-DS4 The state row records `last_synced_at`, `newest_synced_message_id` (the watermark) and
  `oldest_reached`; the oldest stored id is read from the rows.
- AC-DS5 A local page shorter than the window offers older history (`has_more_older: true`) until
  Respond has said the start was reached, or while the last older read failed within the interval.

## C. Direct webhook
- AC-WH1 A Respond.io message webhook with a valid signature writes the same row the n8n ingest
  writes; the same message arriving from both lanes resolves to one row.
- AC-WH2 An invalid or missing signature (and no matching shared secret) is 401 and writes nothing.
- AC-WH3 A webhook carrying an attachment stores `media_url`, `media_type`, `media_file_name`;
  a webhook from a bot / agent stores `sender_source` (and `sender_user_id` for a user).
- AC-WH4 A new row from the webhook publishes the `message` event; a duplicate does not.

## D. Reconcile
- AC-RC1 The reconcile selects contacts whose activity (stamped by every lane that writes a row)
  is within `activity_days` (task metadata) and runs `sync_newer` for each that is due: never
  synced, or not synced for at least a quarter of the time the contact has been quiet.
- AC-RC1b The scheduled-task handler enqueues the run on the `respond_io` RQ queue and returns at
  once; one run at a time (Redis lock); the heartbeat is never blocked by it.
- AC-RC2 At most `concurrency` (task metadata) Respond.io calls are in flight per workspace key.
- AC-RC5 An incoming message the sync stored first, younger than one hour, gets the same phone
  push the n8n ingest would have queued (deduped on the message id).
- AC-RC3 A 429 with `Retry-After: N` pauses that token for N seconds; without the header the
  wait doubles per consecutive 429 (1, 2, 4, ... capped at 60 s) and resets on success.
- AC-RC4 The tick never raises out of the scheduler; a failing contact is recorded on its cursor
  row (`last_error`, `last_error_at`) and the next contact still runs.

## E. Local row fidelity
- AC-RF1 `_row_to_item` renders an attachment block (`message.type`, `message.attachment.url`,
  `type`, `fileName`) from the new columns when present, text otherwise.
- AC-RF2 `_row_to_item` renders `sender.source` from `sender_source` (`contact` for incoming
  rows with no stored source, as today).
- AC-RF3 A row stored without media / sender (n8n, before this lane) is filled on the next delta or
  reconcile read that returns that message (fill-if-null, never an overwrite), not by a one-shot
  script. Coverage: everything newer than the watermark, the newest 50 on a contact's first read,
  and older history as scroll-back reaches it; rows deeper than that stay text until reached.

## F. Observability
- AC-OB1 Every Respond.io HTTP call increments a per-minute counter and logs its path.
- AC-OB2 `GET /api/v1/system/chat-history/respond-io-calls?minutes=N` (N up to 15, the counter's
  Redis TTL) returns per-minute counts and their total.
- AC-OB3 Hand test: opening a portal thread with history reads instantly, the counter shows at most
  1 call for the open and 0 for the following polls.

## G. Migration
- AC-MG1 One additive migration: five nullable columns on `chat_histories`, new table
  `chat_thread_sync_state` (seeded from the last 7 days), one `scheduled_tasks` row. No
  `system_settings` change: the knobs are the task row's interval and metadata. Single alembic
  head.

## H. Ticket panel (crew gap, 1 Oct 2026)
- AC-TP1 `GET /conversation-sla-tracking/{id}/conversation` returns the ticket contact's thread from
  `chat_histories` (`source: local`) and makes zero Respond.io calls when rows exist.
- AC-TP2 A message delivered by the Respond.io webhook for that contact appears in that response
  without a Respond.io read.
- AC-TP3 A message sent from the ticket panel (`POST .../ticket/send`) is in `chat_histories` through
  the send mirror and appears in that response without a Respond.io read.
- AC-TP4 `cursor=<oldest message id>` pages older rows (maps to `before`).
- AC-TP5 The panel's refetch after a send renders the sent row (vitest); the e2e round-trip asserts
  the `/conversation` request carries the sent text.
