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
   - `skip_reason(...)`: mode, contact list, incoming WhatsApp from the contact, not a
     duplicate, message clock at most 120 s old, and the human-takeover rule (below). It
     returns None when the turn may be enqueued.
   - `run(payload)`: builds the envelope (`ingress = "respond_webhook"`), runs `run_turn` with
     `offload=False`, and POSTs `{turn_id, contact_id, actions, reply}` to the sender with
     `X-Chatbot-Sender-Key`, without following redirects. Nothing is sent for a duplicate or for
     a turn with no actions. Every POST writes an outbound `integration_log` row. A failed POST
     also appends a trace record, so the turn screen shows it. No automatic retry (R4).
4. **Doorway**: `app/tasks/chat_turns.py` gains `enqueue_direct_turn` (called by the webhook,
   never raises) and `run_direct_turn_job` (the RQ job on `chat`). The webhook stays outside
   the chatbot package (import boundary).
5. **Engine, two lines**: a `respond_webhook` turn always takes the per-contact ticket (prod
   runs with S7 ordering off, so nothing else would serialise two direct jobs of one contact).
   A `respond_webhook` turn that would delegate is closed the S7 way, never left as a ghost.
6. **n8n**: `documentation/n8n/chatbot-sender.workflow.json` plus a README for the owner to
   import. No prod change by crew.

## Human takeover (conservative until crew confirms the producer's gates)

The engine does not gate on `is_human_intervened` (it clears it and answers,
`engine.py:4228-4236`). The n8n producer may gate on it, and its export is not in this repo. So
the direct path enqueues only when the webhook contact block carries `custom_fields` and
`is_human_intervened` is not true. Otherwise it skips, and the n8n copy answers exactly as today.

## Rollback

Mode `off`, effective on the next message with no deploy. In `off` the webhook does exactly
what it does today.
