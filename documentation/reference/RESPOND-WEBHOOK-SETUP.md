# Respond.io direct message webhook: owner setup

Lane CHAT-LOCAL-FIRST (`documentation/plans/sla/PLAN-chat-local-first-30sep.md`, R3). The CRM
stores every WhatsApp message in `chat_histories` and renders the thread from that table.
Two feeds write it as messages happen: n8n (unchanged) and, with this setup, Respond.io
itself. The two dedupe on the Respond message id, so either one alone keeps the thread
current and the reconcile job (`chat_history_reconcile`, every 5 minutes) covers anything
both missed.

Nothing here is configured by the CRM: the steps below are done once, by the owner, in
Respond.io and in the deployment's environment.

## 1. Choose a secret

Any long random string, for example:

```
openssl rand -hex 32
```

It is entered in two places (steps 2 and 3) and never anywhere else.

## 2. Deployment: `RESPOND_WEBHOOK_SECRET`

Add to the backend environment (`sorento_crm_backend/.env`, or the compose / secrets file
the deploy uses):

```
RESPOND_WEBHOOK_SECRET=<the secret from step 1>
```

Restart the API containers. Until this is set the endpoint answers `503` and writes
nothing, so the order of steps 2 and 3 does not matter.

## 3. Respond.io: create the webhook

In Respond.io: **Settings -> Integrations -> Developer API -> Webhooks -> Add webhook**
(the menu names are Respond.io's and may move; the page is the one that lists webhook
subscriptions for the workspace).

| Field | Value |
| --- | --- |
| URL | `https://<crm host>/api/v1/public/respond/webhook` |
| Events | `Incoming Message` (`message.received`) and `Outgoing Message` (`message.sent`) |
| Signing key / secret | the secret from step 1 |

The CRM verifies each delivery as HMAC-SHA256 of the raw request body with that secret,
read from the `x-respond-signature` header (also accepted: `x-webhook-signature`; hex or
base64). If the Respond.io webhook page offers no signing key but does offer a custom
header, add the header `X-Respond-Webhook-Secret` with the secret as its value instead:
the CRM accepts either proof. One is required; a delivery with neither is refused with
`401` and nothing is stored.

Please confirm on the Respond.io page which of the two the webhook form offers, and the
exact header name it documents, and note it on the PR: the sandbox that built this lane
cannot reach `docs.respond.io`, so the header names above are the documented convention,
not a live check.

## 4. Verify

1. Send a WhatsApp message from a phone to the business number.
2. In the CRM, open **System -> Integration logs** and filter channel `respond_webhook`:
   one inbound row per delivery, status `success`, and the body of the message in the
   payload column. A `failed` row with `Bad webhook signature` means the secret in step 2
   and step 3 differ.
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
