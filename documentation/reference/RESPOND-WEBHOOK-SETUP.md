# Respond.io direct message webhook: owner setup

Lane CHAT-LOCAL-FIRST (`documentation/plans/sla/PLAN-chat-local-first-30sep.md`, R3). The CRM
stores every WhatsApp message in `chat_histories` and renders the thread from that table.
Two feeds write it as messages happen: n8n (unchanged) and, with this setup, Respond.io
itself. The two dedupe on the Respond message id, so either one alone keeps the thread
current and the reconcile job (`chat_history_reconcile`, every 5 minutes) covers anything
both missed.

Nothing here is configured by the CRM: the steps below are done once, by the owner, in
Respond.io and in the CRM's settings.

## 1. Respond.io: create the two webhooks

Respond.io makes one webhook per event type, and generates each webhook's signing key
itself (a base64 string such as `k9/Fq+2Zr8...Rg8=`); the key cannot be chosen or edited.
So there are two webhooks and two keys.

In Respond.io: **Settings -> Integrations -> Developer API -> Webhooks -> Add webhook**
(the menu names are Respond.io's and may move; the page is the one that lists webhook
subscriptions for the workspace). Create both:

| Webhook | URL | Event |
| --- | --- | --- |
| 1 | `https://<crm host>/api/v1/public/respond/webhook` | New Incoming Message (`message.received`) |
| 2 | the same URL | New Outgoing Message (`message.sent`) |

Copy each webhook's signing key.

## 2. CRM: paste both keys

**Settings -> Integrations -> Respond.io webhook -> Signing keys**: paste each webhook's
signing key, one per line, and Save. The saved value is never shown again (only its last
four characters); to change it, paste the full list again.

Fallback for a deploy without the settings row: `RESPOND_WEBHOOK_SECRET` in the backend
environment (`sorento_crm_backend/.env`, or the compose / secrets file the deploy uses),
the keys separated by commas:

```
RESPOND_WEBHOOK_SECRET=<key of webhook 1>,<key of webhook 2>
```

Keys may be separated by new lines, commas or spaces (a base64 key contains none of them);
empty entries are ignored. Until at least one key is set the endpoint answers `503` and
writes nothing, so the order of steps 1 and 2 does not matter.

## 3. How a delivery is checked

The CRM verifies each delivery as HMAC-SHA256 of the raw request body, read from the
`x-respond-signature` header (also accepted: `x-webhook-signature`; hex or base64), and
accepts it when the signature matches ANY of the configured keys. A webhook configured
with a custom header instead of a signing key may send `X-Respond-Webhook-Secret` with one
of the keys as its value: the CRM accepts either proof. One is required; a delivery with
neither, or signed with a key that is not in the list, is refused with `401` and nothing is
stored.

Adding a third webhook later (another event type) means adding its key as another line.

## 4. Verify

1. Send a WhatsApp message from a phone to the business number.
2. In the CRM, open **System -> Integration logs** and filter channel `respond_webhook`:
   one inbound row per delivery, status `success`, and the body of the message in the
   payload column. A `failed` row with `Bad webhook signature` means that webhook's signing
   key is missing from the list in step 2.
3. Open the contact's thread in the portal or the Conversations inbox: the message is
   there without a Respond.io read (`GET /api/v1/system/chat-history/respond-io-calls`
   shows the per-minute Respond.io call count; a webhook delivery adds none).

## 5. What the webhook does not do

- It does not replace n8n: the bot still runs there. A message that arrives from both
  lands once (`status: duplicate` on the second delivery, whichever it is).
- It stores the message text, the attachment URL / type / file name, and who sent it. It
  does not download media; the thread streams attachments through the existing media
  proxy from the URL Respond.io gives.
- It ignores every other event type with `200 ignored`, so the subscription can safely be
  widened later without breaking deliveries.
