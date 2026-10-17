# PLAN: CHAT-SPEED direct ingress (option D)

Status: Build (red-first), owner OK 18 Oct 2026 ("Chatbot speed ok"). Track: size L (migration,
external ingest surface). Card: `CARD-chat-speed-ingress.md` (options, measurement, rulings).
UAC: `chat-speed-ingress-acceptance-criteria.md`.

## What gets built

The CRM's signed respond.io webhook (`app/api/v1/public/respond_webhook.py`) starts the turn
itself. A thin n8n sender executes the finished actions. The n8n producer and dispatcher stay
untouched, so their later copy of the same message comes back `duplicate: true` and sends
nothing (D15). Measured saving: P+Q, p50 2.30 s / p90 3.53 s (dev copy, 11-15 Oct).

## Pieces

1. **Switch** (migration): `system_settings.chatbot_direct_ingress_mode` (`off` | `contacts` |
   `all`, default `off`) and `chatbot_direct_ingress_contacts` (respond.io contact ids, one per
   line or comma separated). Both go through the manual GET and PUT builders in
   `app/api/v1/user_management/settings.py` and appear on Settings > Chatbot.
2. **Sender target** (same migration): `respond_workspaces.chatbot_sender_url` and
   `chatbot_sender_key_ciphertext`, beside the retry ingress pair, with the same save-time and
   use-time `outbound_url_guard` check. Edited on System > Respond Workspaces.
3. **Gate + envelope + run + send**: `app/services/chatbot/direct_ingress.py`.
   - `skip_reason(...)`: mode, contact list, incoming WhatsApp text from the contact, not a
     duplicate, message clock at most 120 s old, and the producer's rate limit (below). It
     returns None when the turn may be enqueued.
   - `run(payload)`: looks the contact up (takeover gate, below), builds the envelope (`ingress = "respond_webhook"`), runs `run_turn` with
     `offload=False`, and POSTs `{turn_id, contact_id, actions, reply}` to the sender with
     `X-Chatbot-Sender-Key`, without following redirects. Nothing is sent for a duplicate or for
     a turn with no actions. Every POST writes an outbound `integration_log` row. A failed POST
     also appends a trace record, so the turn screen shows it. No automatic retry (R4).
4. **Doorway**: `app/tasks/chat_turns.py` gains `enqueue_direct_turn` (called by the webhook,
   never raises) and `run_direct_turn_job`. The webhook stays outside the chatbot package
   (import boundary). **The turn runs on a bounded thread pool inside the API process**
   (`DIRECT_TURN_THREADS = 8` per process), not on RQ. Measured on a dev stack, 18 Oct:
   - an RQ work-horse is forked per job, and spent a median 0.86 s from job start to the turn
     row (cold start), against a warm thread's ~0.01 s, out of a 2.30 s saving;
   - `worker_fast` is one process shared with `media`, `respond_io` and `notifications`
     (code review S1).

   `/chat/turn` already runs on an API thread per n8n call, so this is today's model. When
   the pool is full, the message is left to the n8n copy ("busy").
5. **Engine, two lines**: while direct ingress covers a contact, every live turn of it takes
   the per-contact ticket, the n8n copy included (prod runs with S7 ordering off, so nothing
   else would serialise a direct turn against a later message's n8n copy).
   A `respond_webhook` turn that would delegate is closed the S7 way, never left as a ghost.
6. **n8n**: `documentation/n8n/chatbot-sender.workflow.json` plus a README for the owner to
   import. No prod change by crew.

## The producer's four gates, mirrored (crew facts, 18 Oct)

The `sorento-main` export (6 Sep, may lag prod) stops a message for four reasons. This path
handles each one:

1. **Trigger: text and attachments only.** Here, text only (`skip_reason` "not_text"). Media
   stays on the n8n path for now: its intake waits up to the media budget on an extraction
   job, which would hold one of the few direct-turn threads that long. Trigger to add it:
   direct text live for a week, with the pool never full.
2. **Failover carve** (`failover_watermark`, an n8n table the CRM cannot read): not
   mirrored, because it does not need to be. A carved contact's messages reach `/chat/turn`
   through the poller. Whichever copy lands first runs, and the other is a D15 duplicate that
   sends nothing. While direct ingress covers the contact, both copies take the per-contact
   ticket.
3. **Human takeover** (`custom_fields.is_human_intervened` from n8n's `findcontact`): the
   signed webhook body never carries `custom_fields` (crew, 8,434 rows). So the JOB looks the
   contact up through `RespondClient.get_contact_by_identifier` (the same call), and uses
   that record as the envelope contact, which means the engine reads the same contact on both
   paths. `true` runs no turn. A failed lookup runs no turn either, and the n8n copy answers.
   Cost: one respond.io GET per direct text, about what n8n's own lookup costs inside the
   measured P, so the expected saving is P+Q minus one lookup and the sender hop. Query 3
   measures the real figure on dev.
4. **Rate limit** (more than 30 per contact per second): the same Redis INCR, 1 s TTL. Over
   the limit, the n8n copy still sends its fixed reply.

## Security review (18 Oct): fixed and residual

Fixed in the build:
- **M1:** only a body-HMAC delivery starts a turn. The shared-secret header mode carries the
  key on every request, so its holder could write any body.
- **L3:** a contact stored against a non-default workspace stays on the n8n path.
- **Nits:** a length cap on the contact list, and the sender URL logged without its query
  string.

Residual, accepted by name (no code):
- **L1, no global cap across contacts.** The n8n path has none either. Trigger: the first day
  `chatbot_sender` outbound rows exceed the n8n path's own peak per minute.
- **L2, DNS rebinding on the sender URL.** This is the residual already accepted for the
  retry ingress, now reached on inbound traffic rather than on an operator click. The URL is
  admin-set and re-checked at use.

## Code review (18 Oct): fixed

- **B1:** the engine's real `_read_switches` builder is tested.
- **B2:** the rate limit is one atomic INCR+EXPIRE script, and its TTL is tested.
- **S1:** the turn runs on the API thread pool (above).
- **S2:** no sender URL leaves the message to n8n ("no_sender").
- **S3:** the delegate refusal is tested for direct turns, and its error names direct ingress.
- **S4:** the FE clear-sender-key path is tested.
- **S5:** the 120 s window is re-checked when the turn starts.
- **Pinned contract:** `Envelope.ingress` gains `respond_webhook`, recorded in
  `external_contract_main_f14802a7c.json` (additive; no n8n caller sends it).

## Rollback

Mode `off`, effective on the next message with no deploy. In `off` the webhook does exactly
what it does today.
