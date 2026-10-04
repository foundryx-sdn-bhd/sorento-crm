# n8n contract changes - handoff for the sorento-crm-n8n session

**Do not apply directly to live.** This is the spec for the n8n-side session to implement
and test in its own environment before promotion.

Belongs to slice S4 of `PLAN-monitoring-enhancement.md`.

> **Status: the CRM side has shipped** (PR #25). This document has been reconciled against
> the code that actually landed, not the pre-implementation design. Where the original
> design and the shipped behaviour differ, the shipped behaviour is what is described here.

## Current state - nothing is broken, one thing is dormant

Every new ingest field is **optional** and the schema uses Pydantic's default
`extra="ignore"`, so today's n8n payloads validate and insert exactly as before. Verified
live against the shipped code: a POST with only the old fields returns `201` with a real
row id.

Measured on the live table (1520 rows) at handoff time:

| Column | Populated | Consequence |
|---|---|---|
| `turn_id` | **0** | latency pairing has no input → metric returns an empty set |
| `respond_ts` | **0** | derived from `message_id`; see below |
| `message_id` | **4** | resolver can never see the other 1516 rows |
| `ingest_at` | 1 | set by the CRM from now on, no n8n action |

So: **saving chat history works, the admin UI works, exports work.** The latency SLA
computes nothing and alerts nothing - silent, not failing. It stays that way until the
changes below land.

One visible symptom in the meantime: the transcript can show a reply sorting *before* its
question, because n8n stamps both rows at flow-execution time. That inversion is rendered
honestly rather than masked, so it disappears on its own once this work lands.

## Why

We measure the WhatsApp round trip - user presses send → our reply is accepted by Respond - 
against a **p99 target (default 10s)**, alerting from day one.

Three facts make the current payload unusable for that:

1. `sent_at` is `new Date().getTime()` - n8n's clock at save time, not when the user sent.
   On real rows an outgoing `sent_at` sometimes **precedes** the incoming it answers, so a
   naive `t1 - t0` yields negative latency.
2. `message_id` is effectively never sent. **This is the load-bearing field** - see the
   note below on how `respond_ts` is actually obtained.
3. Nothing links an outgoing message to the incoming one that triggered it. Temporal
   guessing breaks on message bursts and on proactive sends.

Both endpoints of the measurement must come from **Respond's clock**, so the number
contains zero skew between two machines.

### Important: `respond_ts` is NOT something n8n sends

This differs from the original design sketch and is the single most important thing to get
right.

The CRM does **not** derive `respond_ts` from your `sent_at`. A scheduled task
(`chat_message_resolver`, every 60s) selects rows

```sql
WHERE message_id IS NOT NULL AND respond_ts IS NULL AND resolve_attempts < 5
```

and calls Respond `GET /v2/message/{id}` to fetch the authoritative timestamp, for **both**
directions. That is what guarantees one clock.

Consequences:

- A row **without `message_id` is invisible to the resolver forever** and can never take
  part in the SLA. Sending `message_id` matters more than fixing `sent_at`.
- Fixing `sent_at` is still wanted - it drives transcript ordering and is the
  human-readable timestamp throughout the admin UI - but it is **not** what the SLA
  measures.
- A 404 from Respond is treated as "never sent" only after 5 attempts; transient errors
  never conclude that.

## Workflows

| Workflow | Id |
|---|---|
| Main | `9qVyfUxmRQqrpGRMDLRuz` |
| Save-incoming subworkflow | `UrETd-jm46tFj3Xw7w8vL` (`sub-respond-save-message-redis`) |
| Send subworkflow | `aoydkG1dbItXR5jXFEQsP` (`sorento-sub-respond-sendmsg-respond`) |

Ingest endpoint: `POST /api/v1/external/chat-history/messages`

## Change 1 - incoming save: pass the raw Respond timestamp

Node `Call 'sub-respond-save-message-redis'2`.

```diff
- "sent_at": "={{ new Date().getTime() }}"
+ "sent_at": "={{ $('tf-message').first().json.message.message.timestamp }}"
```

Verbatim epoch ms from the webhook - no arithmetic, no re-parsing. This is the
`message.timestamp` field (e.g. `1784519974000`) already present in the payload.

Apply the same change inside the `data` JSON blob, which repeats
`"sent_at": new Date().getTime()`.

## Change 2 - populate `message_id` on both directions ← highest priority

**Incoming**, from the webhook payload:

```
"message_id": "={{ $('tf-message').first().json.message.message.messageId }}"
```

**Outgoing**, the id returned by the Respond send call - the send subworkflow already
receives it; pass it through to the chat-history ingest:

```
"message_id": "={{ $json.messageId }}"      // adjust to the send node's actual output path
```

Without this the resolver cannot fill `respond_ts`, and the SLA has no data even if
`turn_id` is present. If only one change ships first, ship this one.

## Change 3 - `turn_id` on both saves

Add to **both** the incoming and the outgoing ingest calls:

```
"turn_id": "={{ $execution.id }}"
```

Same execution = same turn, so pairing is exact regardless of bursts or ordering. It
doubles as the n8n execution id, so triage can deep-link straight to the failing execution.

Proactive sends (SLA notices, campaigns) have no incoming message and must send **no
`turn_id`** - the CRM excludes those from the SLA denominator rather than guessing.

## Change 4 (NEW - added after S3 shipped) - identify yourself with `X-Source`

Not part of the original spec; this landed with the `api_call_log` slice.

Every call to `/api/v1/external/*` is now recorded in `api_call_log` with endpoint, status,
latency and redacted payloads. n8n and the MCP server authenticate with the **same shared
`EXTERNAL_API_KEY`**, so the backend cannot tell them apart unless the caller says so. The
MCP client now sends `X-Source: mcp`. n8n currently sends nothing and lands as
`source='unknown'`.

Add to every CRM HTTP call from n8n:

```
X-Source: n8n
```

Optional but useful for triage - ties a CRM row back to the execution that produced it:

```
X-Correlation-Id: {{ $execution.id }}
```

Purely additive: nothing changes if omitted, the row is still written. This is about making
the new **System Management → API Call Log** page able to answer "was that n8n or the
assistant?".

## Resulting ingest payload

All new fields optional:

```jsonc
{
  "channel": "whatsapp",
  "contact_id": "900000063",
  "phone_number": "+60160000517",
  "message": "SRTKS2405 stock level",
  "sent_at": 1784519974000,          // CHANGED: raw respond message.timestamp
  "type": "incoming",
  "message_id": "1784519974000000",  // NEW - load-bearing, resolver keys on this
  "turn_id": "48213",                // NEW: {{ $execution.id }}
  "first_name": "CONTACT A",
  "last_name": null,
  "reply_to_message_id": null,
  "reply_to_message": null
}
```

Headers:

```
X-API-Key: <EXTERNAL_API_KEY>
X-Source: n8n                          // NEW
X-Correlation-Id: {{ $execution.id }}  // NEW, optional
```

## What the CRM does once this lands

| Task | Cadence | Behaviour |
|---|---|---|
| `chat_message_resolver` | 60s | fills `respond_ts` from `message_id`; 5 attempts then gives up |
| `chat_latency_watchdog` | 60s | evaluates p99, per-turn hard ceiling, and unanswered turns |

Configurable in Settings (all have defaults, nothing to set up):

| Setting | Default | Meaning |
|---|---|---|
| `chat_latency_p99_target_seconds` | 10 | the SLA |
| `chat_latency_ceiling_multiplier` | 3 | per-turn hard ceiling = 3× target |
| `chat_latency_no_reply_minutes` | 5 | incoming with no reply at all |
| `chat_latency_min_sample` | 30 | below this, no percentile is claimed |

Three alert triggers, not one: a rolling percentile alone is blind to turns that never
complete, which is exactly the failure mode a stalled webhook produces.

## Acceptance

Keyed to `monitoring-enhancement-acceptance-criteria.md`, S4 section:

- **OBS-S4-01** - incoming `sent_at` equals the webhook's `message.timestamp` exactly.
- **OBS-S4-02** - `message_id` present on 100% of new rows, both directions.
- **OBS-S4-03** - incoming and outgoing rows of one turn share a `turn_id`.
- **OBS-S4-04** - a proactive send produces a row with no `turn_id`.

Verify the blunt way after a day of traffic:

```sql
SELECT
  count(*)          AS rows,
  count(message_id) AS with_message_id,
  count(turn_id)    AS with_turn_id,
  count(respond_ts) AS resolved
FROM chat_histories
WHERE sent_at > now() - interval '1 day';
```

`with_message_id` should equal `rows`. `resolved` should approach it within a few minutes
of each row landing (resolver runs every 60s). If `resolved` stays at 0 while
`with_message_id` climbs, the resolver is failing against Respond - check the Respond
workspace API key, not the n8n change.

## Test scenarios to simulate

| Scenario | Expected |
|---|---|
| Normal turn, reply within target | paired row, latency < 10s, no alert |
| Slow turn (inject delay > 30s) | `stalled turn` alert (hard ceiling = 3× target) |
| Incoming with no reply at all | `no reply` alert after 5 min |
| Burst: 3 incoming rapidly, 1 reply | each turn pairs by `turn_id`, no phantom breaches |
| Proactive outbound only | excluded from the SLA denominator entirely |
| Failover-sourced incoming (`event_id` prefixed `failover-`) | ingests normally; webhook lag visible as `ingest_at` − `respond_ts` |
| Send fails / message never reaches Respond | resolver 404s 5×, row marked `not_sent`, excluded from latency |

The failover row matters most - it is the case this whole slice exists for. `ingest_at −
respond_ts` is the webhook lag, kept **out** of the SLA measurement deliberately so a slow
webhook cannot masquerade as a slow agent.

## Sequencing

Independent of the CRM work, which is already merged and inert.

1. **`message_id` first** - nothing else produces data without it.
2. Then `turn_id`, which turns resolved rows into paired turns.
3. Then `sent_at`, which fixes display ordering.
4. `X-Source` any time; unrelated to the SLA.

Leave the alert thresholds at their defaults until real paired data shows the actual p99
curve, then tune. Alerting on a guessed threshold trains people to ignore the alert - which
is the failure mode this whole plan started from.
