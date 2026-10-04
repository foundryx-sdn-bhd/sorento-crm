# PLAN - message push notifications

Status: REVIEWED - plan markup applied 2026-08-23; ready to slice into tickets
UAC: `documentation/plans/notifications/message-push-acceptance-criteria.md`
Domain: notifications

## Depends on the infrastructure plan

`PLAN-push-infrastructure.md` repairs four defects that silently drop pushes today (the
`notifications` queue is not drained, the worker can start without VAPID and say nothing,
payloads over 4096 bytes are rejected, and nobody has installed the app). Slice S0 here
is independent and can proceed; **S3, the send path, lands after I0 and I1**, or the
first thing this new event does is reproduce all three delivery defects.

SLA assignment and escalation already push today - `form_sla_service._notify_assignee`
passes `send_web_push=False` but `notification_service.py:273-284` upgrades it to `True`
for any user holding a `push_subscriptions` row. That event needs no build, only the
repair.

## What already exists (do not rebuild)

The web-push stack shipped under TCK-33 and is complete:

| Piece | Location |
| --- | --- |
| Manifest, `display: standalone` | `sorento_crm_frontend/public/manifest.webmanifest` |
| Service worker, `push` + `notificationclick` | `sorento_crm_frontend/public/sw.js` |
| Registration | `components/pwa/ServiceWorkerRegister.tsx` |
| Subscribe / unsubscribe, VAPID | `services/pushService.ts` |
| Device opt-in card | `user-management/account/components/push-notification-preference.tsx` |
| Subscriptions table + routes | `push_subscriptions`, `POST\|DELETE /api/v1/notifications/push/subscriptions` |
| Sender, with dead-endpoint pruning | `app/tasks/notification_tasks.py::_send_web_push_for_notification` |
| VAPID keys | backend `.env`, frontend `.env.local` - both already set |

Every `NotificationService.create()` already fans a `web_push` delivery out. So push is
not the gap. The gap is that **no event is raised when a message arrives**: `event_type`
values in the codebase are `assign`, `escalated`, `approved`, `portal_submitted` and so
on - nothing message-shaped.

`conversation_event_bus.publish(EVENT_MESSAGE, ...)` already fires on every ingest
(`app/api/v1/external/chat_history.py:206` and `:309`) to poke the open drawer over
Redis pub/sub. That is a liveness poke for connected browsers, not a delivery mechanism
for a closed phone, and it is deliberately payload-free. The push path is separate.

## Design

### The one decision the user makes

One select on My Account, four values, stored as one column:

```
users.notify_push_message_scope  VARCHAR(24) NOT NULL
  DEFAULT 'assigned_and_coverage'
  in ('assigned_and_coverage', 'assigned_only', 'all_contacts', 'off')
```

A column, not a preference table, because there is exactly one event. The moment a
second event lands (mentions is the obvious one - `ticket_comments.mentioned_user_ids`
already exists), this becomes a `notification_scope_preferences(user_id, event_key,
scope)` table and the column is migrated into it. That is the trigger to watch for, and
it is written down here so the second event does not quietly become a second column.

### Flow

```
Respond.io webhook
  ->  n8n `sub-respond-save-message-redis` (UrETd-jm46tFj3Xw7w8vL)
  ->  POST /api/v1/external/chat-history/messages
             |
             | (row committed; existing event-bus poke unchanged)
             v
         enqueue_job(send_message_push, chat_history_id, queue_name="notifications")
             |
             v  RQ worker
         message_push_service.recipients_for_message(row)
             |  assignee + coverage + all_contacts users, de-duplicated
             v
         NotificationService.create(in_app + web_push, no email)
             |
             v  the existing notification pipeline, unchanged
             |
             v
         browser service worker: coalesce by tag, suppress if thread visible, show
```

There is no direct Respond.io webhook receiver in this backend - every route that
mentions Respond is either outbound (send, templates) or external-API-key ingest. The
inbound lane is Respond -> n8n -> our ingest endpoint, contracted in
`documentation/plans/observability/n8n-contract-handoff.md` ("Ingest endpoint:
`POST /api/v1/external/chat-history/messages`", save-incoming subworkflow
`sub-respond-save-message-redis`). If that ever changes to a direct webhook, only the
publisher moves - the hook below is on the committed row, not on the transport.

### Recipient resolution (`app/services/message_push_service.py`)

Inbound only. `type != 'incoming'` returns no recipients (AC-M11) - an agent reply, a
bot reply and a template send all reach the same ingest endpoint. The AC originally said
`'inbound'`; the column has only ever held `incoming` / `outgoing`, so S2 corrected both
sides (see the note under AC-M11).

1. Resolve **every open** conversation tracking for the contact - not one. Conversation
   SLA is no longer max-one-open-per-contact (`sla_service.py`, AC-F1 multi-open
   consumer audit), so a contact can be worked by several people at once. Use the plural
   lookup, filtered by `conversation_tracking_scope()`: a form-SLA stage row lives in the
   same table discriminated only by `source_entity_type`, and matching one here would
   push the wrong person (CLAUDE.md, the standing conversation-vs-form-SLA trap).
   **Do not** use `get_preferred_tracking_for_contact` - it deliberately reduces a
   multi-open contact to one representative row and would silence every other assignee.
2. Assignee = each open tracking's `assigned_to_id` (a real `users.id`; the sibling
   `assigned_to` text column is the legacy Respond user id and is not a recipient).
   A resolved ticket contributes nobody - closing your ticket is how you stop being
   notified.
3. Coverage = active, unexpired `notification_subscriptions` rows whose
   `target_user_id` is that assignee. Reuse `coverage_subscription_service`; do not
   re-query the table by hand. `expires_at` is honoured, so an expired coverer gets
   nothing.
4. `all_contacts` users = every user whose scope is `all_contacts`, regardless of
   assignment, and regardless of whether any ticket is open. This is the query that could
   grow: a single indexed scan over a column with a default, and in practice a handful of
   managers pick it.
5. Filter each candidate by that candidate's own scope (AC-M7 to AC-M9) and de-duplicate
   (AC-M12). A user who assignees two of the contact's open tickets, or who is both
   assignee and coverer, gets one push.

Each recipient's `data.link` points at the ticket that made them a recipient, so two
assignees on the same contact land on different tickets from the same message. An
`all_contacts`-only recipient gets the most recently updated open ticket, or the
contact-filtered list when none is open.

Unassigned threads push only `all_contacts` users. No team fallback, no tier walk - a
message is a personal alert, and an unassigned thread is an SLA problem the SLA system
already raises through its own events.

There is no "drop the message author" guard. Inbound messages come from the contact, and
a contact is not a CRM user, so the guard could never fire; an earlier draft carried it
and it is deleted rather than shipped as dead code.

### Delivery: bell and phone say the same thing

The in-app bell must show exactly what the phone showed, so this event goes through
`NotificationService.create()` with `in_app` + `web_push` and **no email** (AC-M16). One
event, two surfaces, one row - a user who missed the buzz finds the same item in the
bell.

The cost is bell volume: a chatty day puts a row per inbound message in the bell.
Accepted deliberately, because a push the bell cannot account for is worse than a busy
bell. If volume becomes the complaint, the fix is bell-side grouping by contact, not a
second silent delivery path.

**Idempotency is not optional here.** The ingest endpoint is reached TWICE for the same
WhatsApp message (its own docstring, AC-J5: the direct send-user lane and Respond's
outgoing-message trigger race), which is why the insert is an upsert on `message_id`.
The notification must dedupe on the same key or every message double-rings. Use
`NotificationService.create(... source_entity_type="chat_message",
source_entity_id=<message_id>, dedup_key=<message_id>, event_type="message_received")`,
which returns the existing row instead of creating a second (AC-M16a). A row with no
`message_id` has nothing to dedupe on and notifies once per ingest (AC-M16b), matching
what the insert already does.

Since `NotificationService.create()` always queues an `email` delivery, this event needs
the existing `send_email=False` path (already a parameter on the create overloads used by
`users.py:208` and friends). Nothing new is invented for it.

**Nothing is extracted for this.** Because the event goes through
`NotificationService.create()` for its bell entry, it already reaches
`notification_tasks._send_web_push_for_notification` on the `notifications` queue like
every other notification. An earlier draft pulled a `push_sender.py` module out for this
event to call; it would have been a new module whose only caller already called the
original. The 4096-byte payload fix lands in that existing function, in
`PLAN-push-infrastructure.md` slice I1.

### Payload

```json
{
  "title": "Ah Meng (Sorento Kitchen)",
  "body": "Can I get the price for the 900mm hood?",
  "data": {
    "link": "/sla-management/conversation-sla-tracking/<tracking_id>",
    "tag": "contact-<respond_io_id>",
    "contact_id": "<respond_io_id>"
  }
}
```

Title is the contact, body is the message. No channel prefix, no ticket number - the
whole point is that it reads like a message from a person.

`link` is per recipient and falls back to `/sla-management/conversation-sla-tracking?contact=<respond_io_id>`
when there is no tracking - the list already reads that query param
(`ConversationSLATrackingList.tsx:49`). No new route is needed: the per-tracking detail
page `/sla-management/conversation-sla-tracking/[id]` already exists.

Body is truncated to 120 characters. Message text does reach the lock screen and does
transit the browser vendor's push service (encrypted in transit, decrypted by the
worker). That is the same exposure Respond.io's own app carries and was confirmed as
acceptable; it is recorded here rather than left implicit, and the name-only variant is
a one-line change if the position moves.

### Service worker changes (`public/sw.js`)

Two additions to the existing `push` handler, both client-side, both requiring no
backend state:

- **Coalesce** - pass `tag: data.tag` and `renotify: true`, and before showing, read
  `self.registration.getNotifications({ tag })`. If one is displayed, increment a count
  carried in its own `data` and show "<N> new messages" instead of the text. One
  notification per contact, updated in place (AC-M21).
- **Suppress** - `clients.matchAll({ type: 'window', includeUncontrolled: true })`, and
  if any client has `visibilityState === 'visible'` and a URL matching the thread
  (tracking id or `contact=` param), return without showing (AC-M22).

Doing this in the worker rather than server-side is deliberate: the alternative is a
presence table with heartbeats, TTL sweeps and a stale-heartbeat failure mode that
silently swallows notifications. The cost is that the push still leaves the server and
counts as sent. That is the cheaper failure.

`notificationclick` already navigates to `data.link` and needs no change (AC-M24).

## Slices

Tickets: [#244](https://github.com/jayson-odoo/sorento-crm/issues/244) S0,
[#245](https://github.com/jayson-odoo/sorento-crm/issues/245) S1,
[#246](https://github.com/jayson-odoo/sorento-crm/issues/246) S2,
[#247](https://github.com/jayson-odoo/sorento-crm/issues/247) S3,
[#248](https://github.com/jayson-odoo/sorento-crm/issues/248) S4,
[#249](https://github.com/jayson-odoo/sorento-crm/issues/249) S5.

**S0 - Phase 1, FE mocked.** The scope select on My Account, hitting a stubbed hook.
All four options, loading / error / saved states, 375px and 1280px. Contract documented
at the top of the service file. No backend code. Covers AC-M1 to AC-M6.

**S1 - preference column, test-first.** Migration (short revision id, at most 32
characters - a longer head fails a fresh CI stamp; current heads include
`410_trgm_norm_idx`, so `411_notify_push_msg_scope` fits), model column, schema, both
manual dict builders in `get_user` and `get_me`, the update route with 422 on an unknown
value. FE swaps the mock for the real call. Covers AC-M25 to AC-M27.

**S2 - recipient resolution, test-first.** `message_push_service`, pure and separately
testable: it takes a chat row and returns `(user_id, link)` pairs. Every AC from M7 to
M13 is a pytest case against a seeded chain (policy -> two open trackings -> contact ->
users), each row marker-prefixed, nothing borrowed with `LIMIT 1` from the live database
- CI's database is empty. The multi-open cases (M10, M10a) need two trackings on one
contact, one later resolved. Postgres only.

**S3 - send path.** A `send_message_push` task creating the notification through
`NotificationService.create()` with `send_email=False` and the `message_id` dedup key,
best-effort enqueue after commit in the ingest route. No new transport code - the
existing notification pipeline carries it from there. Covers AC-M14 to AC-M20. A pytest
asserts the double-lane ingest produces one bell row and one push.

**S4 - service worker.** Coalesce plus visible-thread suppression, with vitest over the
handler logic (the handler is plain JS; import it or exercise it through a small
harness that fakes `self.registration` and `clients`). Covers AC-M21 to AC-M24.

**S5 - evidence run and DoD.** The agent-browser walk from AC-M28, written into this
plan and the commit message.

S0 blocks S1 (the contract). S1 blocks S2 (the scope column is what resolution reads).
S2 blocks S3. S4 is independent of S1 to S3 and can run in parallel.

## Testing seams

- `message_push_service.recipients_for_message(db, row) -> list[str]` - the seam that
  makes recipient logic testable without Redis, a browser, or a push endpoint.
- `notification_tasks._send_web_push_for_notification` - the existing seam; the send path
  is tested with `pywebpush` patched, exactly as it already is.
- The service worker's decision function, split out of the `push` listener so vitest can
  call it with a fake registration and a fake client list.

## Risks

- **iOS requires the app be added to the home screen** (iOS 16.4+). In a plain Safari
  tab `subscribeToPush()` returns false with no error, which reads as "the feature is
  broken". The existing unsupported copy already says this; the rollout note must too.
- **iOS throttles pushes to a backgrounded PWA.** Delivery is best-effort and can lag
  when the device is idle. This is not APNs and should not be sold as equal to the
  Respond.io native app.
- **A very chatty contact still costs one push per message on the wire** - coalescing is
  a display behaviour, not a send-rate limit. If that becomes a cost or battery problem,
  a per-contact rate limit belongs in `message_push_service`, not the worker.
- **`all_contacts` fan-out** grows with the number of users who choose it. Bounded in
  practice; if it stops being bounded, the fix is a cap plus a warning, not a queue.

## Evidence run, S1 to S3 (2026-08-23, agent-browser)

Recorded here rather than as a Playwright spec, per the repo standing order. Stack:
backend on :8031 and `npm run dev` on :3031, both from the `feat/message-push-backend`
worktree, against the shared dev database (a copy of production). Walked by clicking,
never by opening a deep URL.

1. Sign in at `http://localhost:3031`, avatar menu -> **My Account** -> **Profile &
   Settings**, landing on `/user-management/account`.
2. The **Message Notifications** card renders `Contacts assigned to me and my coverage`,
   read from the REAL route: `GET /api/v1/notifications/preferences/channels -> 200`
   (three calls, one per component reading the route; the S0 note about folding them
   onto one key still applies and still is not worth doing).
3. Pick **All contacts** -> `PATCH .../preferences/channels -> 200`, the select shows the
   new value, a full reload still shows it, and the column reads `all_contacts` in the
   database. **Off** was then saved and read back the same way, and the account was
   restored to the default at the end of the run.
4. AC-M27 on real data: the additive column landed on 3485 existing users, every one of
   them `assigned_and_coverage`, with no backfill script.
5. Ingest the SAME message twice (the dual-lane mirror), by hand against the real route:

   ```
   POST /api/v1/external/chat-history/messages  ->  {"id":36710,"status":"created"}
   POST /api/v1/external/chat-history/messages  ->  {"id":36710,"status":"duplicate"}
   ```

   Contact `900000008` holds TWO open conversation tickets with DIFFERENT assignees, so
   this is the multi-open case on live data. Result: **two** notifications, one per
   assignee, each linked to their OWN ticket, and **not** four:

   ```
   Agnes           | Jayson | Can I get the price for the 900mm hood?
                   | link=/sla-management/conversation-sla-tracking/8c88750b-...
   Jayson Personal | Jayson | Can I get the price for the 900mm hood?
                   | link=/sla-management/conversation-sla-tracking/0f444728-...
   tag=contact-900000008 on both
   ```

   Deliveries per notification: `in_app` sent, `web_push` sent, **no email row**. Neither
   user has a `push_subscriptions` row, so the web push was attempted and sent nothing
   without raising (AC-M17).
6. Ingest an OUTGOING message on the same contact: 201, and zero notifications (AC-M11).
7. The bell then shows exactly what the phone would have shown: title `Jayson`, body
   `Can I get the price for the 900mm hood?`.
8. No page errors and no console errors at any point. The card was re-checked at 375x812
   and 1280x800 with the real saved value; no horizontal overflow.

What this run could NOT show: a real push arriving on a device. The headless browser has
no push subscription and the environment has no registered service worker, so the last
hop is covered by the delivery row plus the pinned behaviour of
`_send_web_push_for_notification` rather than by a buzz. That hop, plus the coalescing
and visible-thread suppression, is S4's evidence.

## Definition of Done (PRINCIPLES.md gate)

1. Mock swapped to real, verified showing a real saved scope value.
2. Backfill: none needed - server default covers existing rows (AC-M27 asserts it).
3. Permission sweep: no new permission (My Account is self-service). Recorded as a
   no-op, not skipped.
4. The new column reaches the FE through BOTH manual dict builders (AC-M26).
5. Verified from the user's perspective by real sidebar clicks at 375px and 1280px.

## Backlog (deferred, `documentation/backlogs/backlog.md`)

- Admin-configurable event registry with per-event templates and quiet hours.
- Mention notifications from `ticket_comments.mentioned_user_ids` - the second event,
  and the trigger to migrate the scope column into a preference table.
- Retro-governing the existing notification events that already push blind, so a user
  can mute "escalated" pushes without muting everything.
- No new Playwright spec (repo standing order); the regression gap from AC-M28 is
  logged there.
