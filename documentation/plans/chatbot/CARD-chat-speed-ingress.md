# CARD: CHAT-SPEED, cut the wait before the CRM sees a WhatsApp message (17 Oct)

Status: owner OK 18 Oct on option D; building on `PLAN-chat-speed-ingress.md` (red-first).
Two findings changed the build, recorded in the plan: media stays on the n8n path (single
`worker_fast` process), and the takeover flag comes from a respond.io contact lookup in the
job (the webhook body carries no `custom_fields`).

## Where the time goes (crew measurement, dev copy of prod, 2,349 WhatsApp turns, 8 Sep-13 Oct)

| hop | p50 s | p90 s | p95 s |
|---|---|---|---|
| user message (respond.io) -> CRM turn created | 5.26 (true ~4.8) | 7.21 | 8.18 |
| CRM queued (created -> started) | 0.01 | 0.02 | 0.02 |
| CRM engine | 3.45 | 7.37 | 10.69 |
| engine finished -> bot reply at respond.io | 0.15 | 0.20 | 0.24 |
| total | 9.55 | 14.39 | 16.97 |

The first row is half the total at p50. Today it breaks into three parts:

- **R**, respond.io delivering the webhook. Not measured yet.
- **P**, the producer `sorento-main` (NwMOBEQ1NW7LVky5): contact lookup, push `q:{c}` and
  `ready-contacts`. Not measured yet. R + P is about 3.5-4 s.
- **Q**, the dispatcher `sorento-dispatcher` (77SG9jTdVKhwMwvR): a 1 s Schedule Trigger, so
  0-1 s of tick wait (mean about 0.5 s) plus about 0.2 s from tick to the CRM POST. Q is
  about 0.7 s mean, up to 1.2 s.

How R and P split decides the choice: no option below can make R faster. The measurement step
(below) splits them with data the CRM already holds.

## What already exists (read, not guessed)

- **The CRM already receives every message straight from respond.io.**
  `POST /api/v1/public/respond/webhook` (`app/api/v1/public/respond_webhook.py:310-325`)
  verifies an HMAC-SHA256 over the raw body against one or more signing keys
  (`verify_request`, :103-125), caps the body at 256 KB (:61, :328-345) and writes
  `chat_histories`, deduped on `(contact_id, message_id)` (:298). It does not start a turn
  today.
- **The CRM can already order turns per contact without the dispatcher (S7).**
  `app/services/chatbot/dispatch.py:52-88` holds the redis ticket per contact
  (`chatbot:seq/done/alive:{contact}`). It is switched on by
  `system_settings.chatbot_ordering_enabled`, and in that mode `/chat/turn` returns the finished
  reply and actions (`app/api/v1/external/chat.py:139-158`). The n8n half, "Step 3 - retire the
  dispatcher", is written but not promoted
  (`documentation/plans/chatbot/n8n-changes.md:1037-1270`).
- **Send order beats arrival order.** `app/services/chatbot/send_order.py:1-37` answers every
  earlier-SENT queued message of the contact before the current one, keyed on respond.io's send
  time.
- **A repeated message is idempotent.** D15 has a unique key on `(contact_respond_id,
  message_id, attempt)` (`app/models/chatbot_turn.py:63-66`). A second delivery returns
  `duplicate: true` IMMEDIATELY, without waiting for the first (`engine.py:1704-1750`,
  :3135-3138), and n8n's `duplicate` arm sends nothing (`n8n-changes.md:35`).
- **The engine reads three things from `envelope.contact`:** `id` (`engine.py:1146`), `phone`
  (with a CRM fallback, `engine.py:7467-7476`) and `custom_fields[is_human_intervened]`
  (`turn/memory.py:83-93`). It runs its own media intake (`media_intake.py`) unless n8n patched
  the media upstream (`contracts.py:794-799`).
- **The CRM never sends (D9).** `/chat/turn` returns actions and the caller executes them, in
  order (`chat.py:1-8`, `contracts.py:884-886`). There are five action kinds
  (`contracts.py:414-420`).

## Options

Each option is compared against the 4.8 s true p50 of the first row.

### B. Finish S7: the producer posts `/chat/turn` itself and the dispatcher retires

- **Saves:** Q, about 0.7 s mean (0.2-1.2 s) at every percentile. R and P are unchanged.
- **n8n:** Step 3 as already written (`n8n-changes.md:1102-1118`). The producer AND the failover
  poller `sorento-main-INJECT` call `/chat/turn` together, and the `q:*`, `ready-contacts` and
  `lock:*` keys are deleted a week later. Exported JSON goes in the repo for the owner to
  import.
- **CRM:** nothing new. It needs `chatbot_ordering_enabled` on, which needs every lane in
  `chatbot_completed_lanes` first (`n8n-changes.md:1059-1071`), and n8n concurrency raised
  (step 1).
- **Ordering and lock:** the CRM ticket replaces the dispatcher's lock. **Idempotency:** D15, as
  today. **Media:** unchanged.
- **CRM down:** the producer's HTTP call fails, so the message never gets a turn, with no redis
  queue to hold it. Today the dispatcher's queue at least keeps it. The failover poller is the
  net.
- **Rollback:** before the dispatcher is deleted, revert the producer JSON. The S7 flag rolls
  back on its own (`n8n-changes.md:1256-1259`). **Measure:** turn `created_at` minus the message
  clock, before and after. **Security:** no new surface.
- **Verdict:** small and already designed, but it caps out at 0.7 s and is gated on S7
  preconditions that are not this lane's.

### C. A leaner producer (drop the respond.io contact lookup)

- **Saves:** whatever the lookup node costs inside P. It is unmeasured, and the n8n executions
  export says how much.
- **n8n:** remove the lookup and send `contact: {id}` plus the webhook's own contact block.
- **CRM:** `_contact_block` already fills `phone` (`engine.py:7467-7476`). It would also need
  `custom_fields[is_human_intervened]`, which today comes only from that lookup. If respond.io's
  webhook body does not carry `custom_fields` (UNVERIFIED), the CRM has to read it from somewhere
  else, which is new code inside the engine path. That is out of scope for this lane.
- **Verdict:** possibly worth up to P, but the saving is unknown and it touches what the engine
  reads. Only worth doing if B or D cannot ship.

### D. The CRM webhook starts the turn itself (recommended, if the measurement agrees)

- **Saves:** P + Q, minus about 0.1-0.2 s for one extra hop on the way out (below). The exact
  figure is query 2 below: CRM webhook arrival -> turn created, for the same message, both
  stamped by the CRM's own clock. Only R remains before the CRM sees the message.
- **CRM, behind one switch (default off):**
  1. In `_handle` (`respond_webhook.py:268-307`), after the `chat_histories` write, an
     `incoming` WhatsApp message that is new (not `already_existed`) is put on the `chat` RQ
     queue as a turn with `ingress = "respond_webhook"`. The envelope is the respond.io body
     verbatim, and its contact block is the webhook's own contact. Respond.io gets its 200 at
     once; the webhook never waits on the turn.
  2. The job runs `run_turn` on the worker. The existing ticket, the send-order rule and D15
     apply unchanged: the job is just another caller of the same entry point, through the RQ
     task file the import boundary already allows (`chat.py:10-12`).
  3. The finished `{turn_id, actions}` is POSTed to a thin n8n **sender** webhook, which runs
     today's Switch on `action.kind` over the existing send, assign, comment and field nodes.
     Egress stays in n8n (D9, see Q2). The URL and key sit on the respond workspace row and go
     through `outbound_url_guard` with no redirects, exactly as the Retry re-inject does
     (`dispatch.py:20-33`). A webhook turn is never `is_test`, so a console or clone turn can
     never reach this sender.
- **n8n:** one new small workflow, the sender (exported JSON in the repo). **The producer and
  dispatcher stay exactly as they are for the whole rollout.** Every message then reaches the
  CRM twice. Whichever copy arrives first runs the turn. The later copy gets `duplicate: true`
  back immediately and sends nothing (the existing D15 path, `test_d15_duplicate_race.py`).
- **Per-contact ordering and lock:** the redis ticket per contact (`dispatch.py`), plus the
  send-time rule (`send_order.py`). This covers two RQ jobs of one contact starting out of order
  too. The build adds a red test for exactly that case: two messages, the second job picked
  first. Capacity: a job waiting on its ticket holds one `chat` work-horse (on `worker_fast`,
  `worker.py:118-123`) for up to `chatbot_queue_wait_seconds`. So the number of `chat`
  work-horses caps how many contacts run at once, the way `N8N_CONCURRENCY_PRODUCTION_LIMIT`
  caps it today (`n8n-changes.md:1078-1086`). The load gate (`scripts/chatbot_load.py`) is run
  against the job path before prod.
- **Idempotency on respond.io retries:** a retried delivery is `already_existed` on
  `chat_histories`, so no job is enqueued. A retry that races the first delivery is caught by
  the D15 unique key. A replay of a signed but old body is refused at enqueue by a staleness
  guard: the message clock (the `messageId` microseconds, `conversation_thread_service.py:147-157`)
  must be under 120 s old (Q3). Anything older is still handled by the n8n path.
- **Media:** the engine's own media intake runs in the job, as it does for any envelope without
  `media` (`contracts.py:794-799`). If n8n's upstream intake still runs for its own copy, the n8n
  copy loses the race and is a duplicate. The photo-then-text overtaking case is the send-time
  rule's job (`send_order.py:3-6`) and gets a red test.
- **Human takeover:** if the webhook contact block has no `custom_fields`, the job reads the
  contact's `is_human_intervened` the way the engine reads `phone`, from what the CRM already
  stores. That is UNVERIFIED (see below), and the build's first step checks it on a real captured
  body. If neither source has the field, D does not ship until it does.
- **CRM down:** respond.io's delivery to the CRM fails, and the n8n copy calls a CRM that is
  also down. That is exactly what happens today, no better and no worse. Redis down: the
  webhook still stores the history and answers 200, and the n8n copy runs the turn. Worker down:
  the job waits on the queue and the n8n copy arrives about 5 s later and runs it. That is the
  one case D handles worse than today, because the job then runs late as a duplicate and sends
  nothing. The staleness guard drops it.
- **One risk to name:** if the CRM's turn finishes but its POST to the sender fails while n8n is
  up, the n8n copy comes back `duplicate` and sends nothing. The reply is then lost, visibly: the
  failed POST writes an outbound `integration_log` row and a trace line, and Retry on the trace
  screen re-injects the message. No automatic retry (R4).
- **Rollout and rollback:** switch off = today, byte for byte (the webhook stores and
  announces, nothing more). Rollout: dev stack, then the owner's own contact on prod (Q4), then
  everyone. Rollback is the switch, effective on the next message with no deploy. Retiring the
  producer and dispatcher is a later, separate decision, and its trigger is named in "Not in
  this lane".
- **Measure on dev:** queries 1-3 below on the turns carrying `ingress = 'respond_webhook'`
  against the rest.
- **Security (security reviewer required):** the surface is the existing signed public webhook,
  but it now spends LLM calls. Every enqueue needs a valid signature (no new auth path), a new
  message id, `traffic = incoming`, a WhatsApp channel and a fresh message clock. The body cap
  stays. The sender URL is outbound-guarded and its key is a secret field. Who can trigger a
  turn does not change: anyone who could forge a turn here already needs a respond.io signing
  key.
- **Verdict:** the largest saving, and every part of it except the sender is code the CRM
  already has. It keeps n8n as a live fallback, and the switch gives a clean rollback.

### Not chosen

- **Push instead of a 1 s poll inside the dispatcher** (a redis BLPOP trigger). It saves the same
  Q as B, but adds a second always-on n8n mechanism, and S7 already designed the dispatcher
  away.
- **A holding message** while the turn runs. The owner ruled it out (D3/D4: each WhatsApp
  message costs money). A typing indicator stays out of this lane, because WhatsApp's own
  typing indicator rides on the "mark as read" call, and whether respond.io exposes it for free
  is UNVERIFIED. Crew can check that separately. It shortens nothing.

## Measurement (read-only, before the build and again after)

On the dev copy of prod (crew), read-only. First check `SHOW timezone` is UTC: the
`integration_log.created_at` and `chat_histories.created_at` columns are `timestamp without time
zone` filled by `now()`. The message clock is respond.io's `messageId` in epoch microseconds.

```sql
-- 1. R: respond.io -> CRM webhook delivery. Needs the CRM webhook live on prod; zero rows means it is not.
WITH w AS (
  SELECT l.created_at,
         substring(l.request_payload from '"messageId": ?"?([0-9]{15,17})')::bigint AS mid
  FROM integration_log l
  WHERE l.integration_channel = 'respond_webhook' AND l.status = 'success'
    AND l.request_payload LIKE '%"traffic": "incoming"%'
    AND l.created_at >= now() - interval '30 days')
SELECT count(*),
       percentile_cont(ARRAY[0.5, 0.9, 0.95]) WITHIN GROUP (ORDER BY extract(epoch FROM created_at) - mid / 1e6)
FROM w WHERE mid IS NOT NULL;

-- 2. P + Q, the saving of option D: CRM webhook arrival -> turn created, same message.
WITH w AS (
  SELECT min(l.created_at) AS arrived,
         substring(l.request_payload from '"messageId": ?"?([0-9]{15,17})') AS mid
  FROM integration_log l
  WHERE l.integration_channel = 'respond_webhook' AND l.status = 'success'
    AND l.created_at >= now() - interval '30 days'
  GROUP BY 2)
SELECT count(*),
       percentile_cont(ARRAY[0.5, 0.9, 0.95]) WITHIN GROUP (
         ORDER BY extract(epoch FROM (t.created_at AT TIME ZONE 'UTC')) - extract(epoch FROM w.arrived))
FROM w JOIN chatbot.turns t ON t.message_id = w.mid AND t.is_test = false;

-- 3. After the build: pre-CRM time per ingress (the crew's first-row figure, split).
SELECT t.ingress, count(*),
       percentile_cont(ARRAY[0.5, 0.9, 0.95]) WITHIN GROUP (
         ORDER BY extract(epoch FROM t.created_at) - t.message_id::bigint / 1e6)
FROM chatbot.turns t
WHERE t.is_test = false AND t.message_id ~ '^[0-9]{15,17}$'
  AND t.created_at >= now() - interval '7 days'
GROUP BY 1;
```

**Owner step if query 1 returns no rows** (the CRM webhook is not configured on prod): export
read-only n8n executions with node timings for the two workflows, the last 7 days (`GET
/api/v1/executions?workflowId=NwMOBEQ1NW7LVky5&includeData=true`, then the same for
`77SG9jTdVKhwMwvR`). The webhook node's start against the message clock gives R; the run time
of the remaining nodes gives P and the contact lookup's share of it (option C). Both exports
stay on the owner's machine: they hold real messages, and this repo is treated as public.

## Owner questions (5)

1. **Which option to build?** (a) D, with the n8n path kept as a deduped fallback; (b) B only;
   (c) B now, D later. **Recommendation: (a), provided query 2 (or the export) shows P + Q of at
   least 1.5 s at p50.** D removes all of P + Q and needs no S7 precondition. Below 1.5 s, (b)
   gets most of it for no CRM code.
2. **Who sends a turn that the CRM webhook started?** (a) a thin n8n sender webhook that runs
   today's `action.kind` Switch; (b) the CRM sends through the respond.io API itself.
   **Recommendation: (a)**, because D9 keeps egress in n8n and its action nodes already handle
   all five kinds. The cost is one hop of about 0.1-0.2 s. (b) is the later step once it is
   wanted.
3. **Staleness guard: is 120 s the window?** (a) 120 s; (b) 30 s; (c) none. **Recommendation:
   (a).** It stops a replayed or very late signed body from starting a turn, and the n8n copy
   still answers anything older, so nothing is lost.
4. **Rollout switch shape?** (a) off / listed contacts / all; (b) a plain on/off.
   **Recommendation: (a).** The owner can try it on their own number on prod before every
   customer gets it. A plain on/off can only be tested on dev. It is one extra text column
   beside the boolean.
5. **The S7 preconditions: what does prod hold today?** This is a read the owner does, not a
   choice: `chatbot_ordering_enabled`, `chatbot_completed_lanes` and whether the Settings >
   Integrations respond webhook keys are set. D needs only the last one. B needs the first two.
   **Recommendation:** read them before answering Q1, since they decide whether B is even
   available.

## Answers so far (crew, 17 Oct)

- Q2: (a) a thin n8n sender webhook. Egress stays in n8n (D9).
- Q3: (a) a 120 s staleness guard.
- Q4: (a) off / listed contacts / all.
- Q5: crew reads `chatbot_ordering_enabled` and `chatbot_completed_lanes` from the dev copy of
  the 15 Oct prod backup. Whether the webhook keys are set may stay an owner read, because the
  copy was neutralised.
- Q5 (dev copy of the 15 Oct prod backup): `chatbot_completed_lanes` holds 13 lanes
  (out_of_scope, access_denied, demand_qty, clarify_menu, escalate_offer, escalation_declined,
  ideate, low_signal, not_supported, offer_hold, stock_denied, check_promotion, business_query).
  `chatbot_ordering_enabled` reads true in the copy, but it was set outside Settings, and prod's
  `/turn/{id}/complete` answered 200 all day. So ordering was effectively OFF on prod, and B's
  precondition is not met. The respond.io webhook secret was saved through Settings on prod on
  10-11 Oct and signed webhooks succeeded, so D's precondition is met.
- Q1: the rule picks **D**. It now waits on the owner's one OK. Measured by queries 1-2 on the
  dev copy, over the signed `respond_webhook` rows of 11-15 Oct UTC:

  | hop | p50 s | p90 s |
  |---|---|---|
  | R: respond.io -> CRM webhook | 3.69 | 4.60 |
  | P+Q: webhook arrival -> turn created (what D removes) | 2.30 | 3.53 |
  | pre-CRM total | 6.22 | 7.45 |

  Context for P+Q: the first-turn-only p50 is 2.29 s, the daily p50 ranges 2.18-2.42 s, and 90%
  of messages are at or above 1.5 s. Expected D saving is about 2.1 s at p50 (2.30 s less the
  ~0.1-0.2 s sender hop), and about 3.3 s at p90. R, at 3.69 s, is respond.io's own delivery
  and stays: no option in this card can shorten it. This 11-15 Oct window reads higher than
  the 8 Sep-13 Oct ~4.8 s figure at the top of the card.

## Build plan once the card is OK'd (option D, red-first)

1. Capture one real webhook body on dev (from `integration_log`, scrubbed of real data before it
   goes anywhere near the repo) and settle the `custom_fields` question.
2. Red tests (pytest, Postgres via `tests/_pg_fixture.py`):
   - switch off: no job is enqueued;
   - a new incoming message enqueues exactly one job, while a duplicate, an outgoing message, a
     non-WhatsApp channel or a stale message enqueues none;
   - a bad signature enqueues none;
   - the job's turn plus the n8n copy give one turn and one `duplicate`;
   - two jobs of one contact started out of order are answered in send order;
   - a photo followed by a text keeps its order;
   - a sender POST failure writes the outbound log and the trace line;
   - the sender URL is refused by the outbound guard on loopback or private hosts.
3. Green: the switch (one `system_settings` migration, on Settings > Chatbot), the enqueue in
   `_handle`, the job in `app/tasks/`, the sender POST.
4. n8n sender workflow JSON under `documentation/n8n/`, for the owner to import. No prod change.
5. Reviewer and security reviewer, an end-to-end run on a dev stack with a faked respond.io
   body and a stub sender, then query 3.

## Unverified (checked in build step 1, before any code)

- Whether respond.io's `message.received` body carries the contact's `custom_fields`
  (`is_human_intervened`).
- Whether the CRM webhook is configured on prod today (query 1 answers it).
- Respond.io's retry policy and timeout for a webhook that answers non-2xx. D answers 200 on
  every message path, so this only matters for the 4xx and 5xx paths that already exist.
- Whether the prod `sorento-main` holds a gate the local exports lack (a wait or debounce node,
  a bot-paused flag, a failover carve). Whatever it gates, D's enqueue has to gate the same, so
  the producer export is read before step 3.

## Not in this lane

- **Retiring the producer and dispatcher once D is live.** Trigger: D on for every contact for a
  week, with zero turns where only the n8n copy ran (query 3, `ingress = 'webhook'` with no
  `respond_webhook` twin) apart from the expected ones (worker or redis down). The failover poller
  then needs its own answer for a CRM outage, because the redis queue that holds messages today
  goes away with it.
- The engine time (the 3.45 s p50 row): the other CHAT lanes and `PLAN-chat-latency-p95-12oct.md`.
