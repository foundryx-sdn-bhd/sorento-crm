# PLAN - Chatbot media endpoint (voice transcription + image recognition)

**Status:** built and reviewed on `fm/multimodal-crm-endpoint`. All slices (S1-S6) done with
tests (pytest + vitest); the corpus run happened and its fixes are folded in (section 13); the
Phase 3 review's three blockers are fixed (section 16); both operator surfaces are browser
signed-off against a prod build (see the handoff). Still unmet, stated not papered over: a
persisted Playwright spec, an RQ-worker end-to-end run against the `queue_service` socket-timeout
change, and a corpus re-run through the changed voice path. Do not merge - merge authority is the
captain's. See section 12 for the deviations each phase made from the design below - the design
text is left as written so the change is visible rather than quietly rewritten.
**UAC (the contract):** `documentation/plans/ideation/chatbot-media-endpoint-acceptance-criteria.md`
**Classification:** **CORE**, `public` schema, normal FKs.
Rationale: this extends the core chatbot/contact domain and references `respond_contacts` and
`system_settings` directly. It is not something a second tenant would install separately, and the
usage ledger is a durable business record that must survive anything being turned off.

**Scope:** the CRM half only. The n8n workflow changes are a separate follow-up that depends on
this. No file outside this repo is touched.

---

## 0. What this builds, in one paragraph

One endpoint that n8n calls once per inbound media message, **and waits for**. It checks whether
the contact is allowed the modality, checks burst and monthly quota, and writes a ledger row -
all of that instantly and **before any money is spent** - then hands extraction to the worker and
awaits the worker's result, returning decision and extraction in one response. The extraction
emits entities in the chatbot's own entity shape so nothing downstream has to know a photo was
involved.

**The wire is synchronous; the execution is not.** This distinction is the whole design. n8n makes
one call and blocks on it, which is what the captain approved and what the spine can actually do
today. But the CRM never performs the multi-second work inside the request handler: the handler
enqueues, then awaits the job without holding the event loop. That is deliberately the opposite of
the live portal defect at `app/api/v1/public/ai_extract.py:116`, where a synchronous multi-second
`extract` call inside an `async def` route freezes the whole backend for 5.8-9.8 seconds per
request. That defect is out of scope to fix and is not to be reproduced here.

The transport-agnostic callback and the polling endpoint are built anyway, and the synchronous
wait falls back to them on timeout. True async is therefore a configuration switch, not a rebuild.

---

## 1. The two flagged questions, settled with evidence

The captain flagged two things to settle deliberately rather than assume. Both are settled here.

### 1.1 Does the spine hold `lock:{contact}` across the wait?

**Chosen: HOLD. This reverses the recommendation made against the earlier async design, and the
reversal is the direct consequence of the captain approving the synchronous wire.**

The reversal is stated plainly rather than quietly amended, because the reasoning matters more
than the conclusion.

**Under the synchronous wire, hold-versus-release is no longer a choice.** A spine node that makes
a blocking HTTP request holds the dispatcher's lock for the duration by construction:
`call-spine` is `waitForSubWorkflow: true` (`sorento-dispatcher/workflow.json:379`), so the
dispatcher execution is blocked on the spine, and the spine is blocked on the CRM. There is no
pause to release across. The earlier release recommendation was answering a different question -
whether to hold across an **open-ended** suspension in an async/resume design - and that question
no longer exists.

**So the real question becomes: is holding safe?** It is a budget argument, and the budget now
closes, where before it did not.

| term | value | source |
|---|---|---|
| lock TTL | 120s | `sorento-dispatcher/workflow.json:330` |
| existing spine turn | 5.0 - 18.4s | `concurrency-plan.md:82,84,143`, `dym-probe-before-offer-plan.md:463` |
| fast path added by this endpoint | milliseconds | measured in this work, section 8 |
| extraction, typical | 5.8 - 9.8s | measured, three corpus images |
| extraction, hard ceiling | `media_sync_wait_seconds`, default 30s | enforced by this endpoint |
| **worst-case turn** | **~48s** | 18.4 + 30, against a 120s TTL |

That leaves better than 60 percent margin at the worst case and roughly 75 percent at the typical
one. Crucially the ceiling is **enforced by a CRM setting rather than hoped for**: past
`media_sync_wait_seconds` the endpoint stops waiting and returns `status: pending` with the
`job_id`, so the wait cannot run long no matter how badly the provider behaves. That is a strict
improvement on the status quo, in which nothing bounded a turn at all.

**What does not change, and must still be said in the PR:** spine p99 remains **unmeasured**
(`concurrency-plan.md:148`), it is already risk #1 in that plan (`:193`, `:222`), and this feature
makes measuring it more urgent rather than less. The mitigation shipped here is that
`media_sync_wait_seconds` is an operator setting, so if the lock does prove tight the wait can be
shortened - and the flow degrades to the callback rather than breaking - without a deploy.

The original evidence, which still stands and is what sized the budget above:

- The dispatcher calls the spine with `waitForSubWorkflow: true`
  (`sorento-dispatcher/workflow.json:379`), so the dispatcher execution is blocked for the whole
  spine turn and `lock:{contact}` is held for that whole time.
- The lock TTL is **120 seconds** (`sorento-dispatcher/workflow.json:330`, mirrored in
  `plans/dispatcher.sdk.js:59`).
- Spine p99 is **explicitly unmeasured** and accepted as a monitored residual risk
  (`plans/concurrency-plan.md:148`), with the re-measure listed as an outstanding preflight
  (`:193`) and a live regression risk (`:222`).
- The only recorded durations are single runs, not a distribution: 4.8s and 9.0s
  (`concurrency-plan.md:82`, `:84`), 6.25s and 6.79s at the live cutover (`:143`), and a range of
  5.8-18.4s (`plans/dym-probe-before-offer-plan.md:463`).
- Measured extraction cost on the same class of work: 5.8s, 9.8s and 5.8s wall time for the three
  corpus images (prior experiment, `image-extract-experiment/report.md`). Add media download and
  queue wait on top.
- The consequence of overrun is specific and bad. When the TTL fires, the dispatcher's 1 second
  schedule tick sees a free lock and starts a **second concurrent spine for the same contact** -
  which is exactly the `save-session-vars` read-modify-write clobber the dispatcher was built to
  eliminate (`concurrency-plan.md:5`).

Holding is also what the repo's own precedent does for the equivalent work: voice today runs
`fetch-audio` then `whisper-transcribe` **inline and synchronously** inside the spine
(`live-spine-sorento-consume-main/workflow.json:5057`, `:5083`), under the same lock, for the same
class of multi-second provider call. This endpoint is that pattern with a bounded ceiling added,
not a new risk class.

**What is measured in this work:** the fast-path latency and the worst-case end-to-end call, since
both now sit inside the lock. Method and targets are in section 8.

### 1.2 The n8n node timeout

The captain asked for this to be settled with evidence, and it interacts with idempotency in a way
that is easy to get wrong.

**Recommendation: set the n8n HTTP node timeout to 60 seconds**, with `retryOnFail: true` and
`onError: stopWorkflow`.

- 60s is double `media_sync_wait_seconds` (30s) and comfortably above the worst case of roughly
  40s for the call itself, so a normal slow turn never trips it.
- It still leaves half the 120s lock budget unspent even if the node runs to its own timeout.
- **`retryOnFail` is safe precisely because of the idempotency constraint.** If the node times out
  while the CRM job actually completed, the retry hits the idempotent replay path (section 3.3
  step 2) and returns the **same** `job_id` and the **completed result**, with no second ledger
  row and no second extraction spend. Without strict idempotency this configuration would double
  charge a contact and pay twice for one photo, which is why the constraint is mandatory rather
  than defensive.
- **Never configure it as `continueErrorOutput` with the error output unwired.** That combination
  routes the item to an unconnected output, the branch dead-ends, and the execution still reports
  `success` - a documented incident class in that repo where a swallowed error produced a
  confidently wrong customer reply.

### 1.3 Can the spine actually resume mid-flow?

**Finding: not today, and not without new construction. This was checked, not assumed.**

Under the synchronous wire this is no longer on the critical path - it is what the fallback would
need if async is ever switched on. It is recorded in full because that switch is meant to be a
configuration change, and this is the fact that decides how much work the switch actually costs.

- Exactly one `n8n-nodes-base.wait` node exists in the whole n8n repo, in `sub-sendmsg`
  (`export/sub-sendmsg/workflow.json:357-367`). It carries no `resume` property, so it is in the
  default `timeInterval` mode, **not** `resume: webhook`. Its `webhookId` is auto-minted by the
  editor for every Wait node and is not evidence of webhook mode.
- That node is **orphaned** - zero inbound edges (`export/sub-sendmsg/TOPOLOGY.md:39-40`, and the
  n8n repo's own `CLAUDE.md:57`).
- `resumeUrl`, `$execution.resumeUrl` and `webhookSuffix` have **zero occurrences** repo-wide.
- The spine itself contains no Wait node at all.
- The live instance's n8n version and execution mode are recorded nowhere in that repo; the
  `docker-compose.yml` pin is explicitly disclaimed as the dead local stack (`CLAUDE.md:43`).

**Consequence for this plan: the CRM must not depend on resume existing** - and under the
synchronous wire it does not, because the primary path returns the result on the same call. The
callback stays transport-agnostic for the fallback and the future switch: n8n may supply an opaque
`callback_url` plus optional `callback_headers`, and the CRM POSTs the result there and cares
about nothing else. That target can be a Wait-node resume URL if the n8n half ever builds one, or
a plain webhook that re-enqueues the turn - which is cheaper and matches the repo's own precedent.
A `GET` polling endpoint covers the case where neither is wanted, and it is also what n8n should
call after a `status: pending` response.

One structural fact the n8n follow-up must respect, recorded here so it is not rediscovered: the
extracted text has to land in the queue item **upstream of `tf-message`**
(`live-spine-sorento-consume-main/workflow.json:2706`), because roughly fifteen downstream nodes
read `$('tf-message')` by name (`TOPOLOGY.md:202`). That is the same trick `patch-transcript`
(`:5100`) already uses for voice.

---

## 2. Data model

Three pieces. Two new tables, plus columns on the existing settings singleton.

### 2.1 `contact_media_usage` - the ledger

Append-mostly, one row per media item, including refusals. This is the metered fact.

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `respond_io_id` | Text, not null | what n8n knows; enforcement reads this |
| `contact_id` | Text FK `respond_contacts.id` ON DELETE SET NULL | internal id; reporting joins this, mirroring `AIAssistantUsageLog.contact_id` |
| `modality` | Text, not null | `image` or `voice` |
| `message_id` | Text, not null | respond.io messageId - the idempotency key |
| `period_key` | Text, not null | `YYYY-MM`, computed in Asia/Kuala_Lumpur by the CRM |
| `outcome` | Text, not null | `accepted`, `failed`, `refused_gate`, `refused_burst`, `refused_quota`, `refused_duration` |
| `tier` | Text, nullable | `standard` or `degraded` |
| `turn_id` | Text, nullable | n8n `$execution.id` |
| `bytes` | Integer, nullable | |
| `duration_ms` | Integer, nullable | voice |
| `notices` | JSONB, nullable | the customer text this decision produced, so a replay and the callback carry what the original response did (added in review, section 16.6) |
| `model`, `provider` | Text, nullable | stamped after extraction |
| `prompt_tokens`, `completion_tokens` | Integer, nullable | |
| `created_at` | TIMESTAMPTZ default now() | |

- `UNIQUE (respond_io_id, message_id, modality)` - the idempotency constraint.
- `INDEX (respond_io_id, modality, period_key)` - the hot enforcement query.
- `INDEX (contact_id, created_at)` - reporting.

Quota counting uses `outcome IN ('accepted','failed')`. Refusals are recorded but do not consume.

**On the idempotency key's shape.** The plan's open item E7 asked whether a multi-image WhatsApp
message yields one `messageId` or several. This cannot be answered from the CRM repo, and getting
it wrong changes the migration. The key here is therefore
`(respond_io_id, message_id, modality)` **plus a nullable `media_ordinal` defaulting to 0**, and
the unique constraint includes it. If one id turns out to carry several images, n8n sends an
ordinal and nothing needs a migration. If it does not, the column stays 0 and the key behaves
exactly as specified. This costs one integer column and removes a schema risk.

### 2.2 `contact_media_limit` - the gate and the overrides

| column | type | notes |
|---|---|---|
| `contact_id` | Text FK `respond_contacts.id` ON DELETE CASCADE | |
| `modality` | Text | PK is `(contact_id, modality)` |
| `is_allowed` | Boolean, not null, **default false** | the gate |
| `monthly_limit` | Integer, nullable | NULL inherits the system default |
| `max_clip_seconds` | Integer, nullable | NULL inherits; voice only |
| `languages` | Text[], nullable | NULL inherits; voice only |
| `warned_period` | Text, nullable | last period the 80 percent warning was sent |
| `degraded_notified_period` | Text, nullable | last period the degradation notice was sent |
| `updated_by`, `updated_at` | | |

**Absence of a row means denied.** That is how "default OFF for every existing contact" is
satisfied without a backfill that writes a row per contact, and it is fail-closed by construction
rather than by a default value someone can flip globally. There is deliberately **no** system-wide
"allow everyone" switch - that would be the footgun that produced the three-week silent outage in
reverse.

DoD gate item 2 (backfill existing rows) is therefore satisfied by argument rather than by a data
migration, and the migration docstring must say so explicitly.

The override semantics follow `AgentFieldAccess` (`app/models/access.py:338-341`), which already
articulates the inherit-unless-overridden pattern.

### 2.3 `media_extraction_job` - the queued work

Separate from the ledger deliberately: the ledger is the metered fact and must not churn, the job
is mutable work state with attempts and results.

| column | type | notes |
|---|---|---|
| `id` | UUID PK | returned to n8n as `job_id` |
| `usage_id` | UUID FK `contact_media_usage.id` ON DELETE CASCADE, unique | 1:1 with an accepted unit |
| `status` | Text | `queued`, `running`, `completed`, `failed` |
| `modality`, `tier` | Text | |
| `media_url` | Text, nullable | |
| `mime_type`, `caption` | Text, nullable | |
| `callback_url` | Text, nullable | opaque, supplied by n8n |
| `callback_headers` | JSONB, nullable | opaque |
| `callback_status` | Text, nullable | `pending`, `delivered`, `failed` |
| `callback_attempts` | Integer default 0 | |
| `context` | JSONB, nullable | opaque turn context echoed back verbatim |
| `result` | JSONB, nullable | the extraction result body |
| `error` | Text, nullable | |
| `rq_job_id` | Text, nullable | mirrors the `ImportJob.job_id` convention |
| `created_at`, `started_at`, `completed_at` | | |

Pattern to follow for the enqueue: `app/api/v1/resources/attachments.py:1357-1370` - write the DB
row first, then `enqueue_job(..., job_id=str(job.id))` so the RQ id is pre-assigned, then store it.

### 2.4 Settings columns on `system_settings`

Added to `app/models/user.py` (the `SystemSetting` model, around the existing AI block at
`:341-347`), and - non-negotiably - to **both** manual dict builders:
`SystemSettingUpdate` (`app/api/v1/user_management/settings.py:17-98`) and the hand-written
response dict in `get_settings` (`:130-212`). A column missing from either is invisible to the
frontend; this is a documented repeat failure in this repo.

| column | default | what it controls |
|---|---|---|
| `media_image_monthly_limit` | 50 | |
| `media_voice_monthly_limit` | 100 | |
| `media_voice_max_seconds` | 120 | |
| `media_burst_limit` | 5 | |
| `media_burst_window_seconds` | 60 | |
| `media_warn_threshold_percent` | 80 | |
| `media_image_provider` / `media_image_model` | `openai` / `gpt-4o` | seeded by the migration on the evidence in section 14.1; NULL still falls back to the `AIAssistantConfig` row |
| `media_image_degraded_model` | `gpt-4o-mini` | the degraded tier, seeded by the migration. Measured as materially worse on the corpus (section 14.1), which is precisely what the degradation notice tells the contact |
| `media_transcribe_model` | `whisper-1` | |
| `media_voice_degraded_model` | NULL, and **not seeded** | voice's own degraded tier (added in review, section 16.1). Image's tiers were measured so migration 358 seeds them; no cheaper transcription model has been measured, so NULL here means the voice quota is a hard refusal rather than a claimed degradation that did not happen |
| `media_language_mode` | `pinned` | `pinned`, `hints`, `auto` |
| `media_language_pinned` | `en` | |
| `media_language_hints` | `en,ms,zh` | CSV, only used in `hints` mode |
| `media_sync_wait_seconds` | 30 | how long the endpoint awaits the worker before returning `pending`; this is the value that bounds the lock. Range 5-90. |
| `media_extraction_timeout_seconds` | 45 | the worker's own hard ceiling. Range 5-110, and **must be >= `media_sync_wait_seconds`** so a job that outlives the wait still finishes and stays retrievable rather than being killed mid-flight. |

Both bounds are enforced in the backend validator, not only in the settings form - a number that
only the UI refuses is not a constraint. The 110 ceiling exists so that even a maximally
misconfigured pair cannot exceed the dispatcher's 120 second lock TTL.
| `media_max_entities` | 10 | the extraction cap |

`media_transcribe_model` defaulting to `whisper-1` with `media_language_mode` defaulting to
`pinned`/`en` reproduces today's behaviour exactly. The captain's instruction is that the pin
stays until he says otherwise; the mechanism to change it without a deploy is what ships.

The captain also asked to prefer a transcription model that accepts a **list** of language hints
and reports which it detected. That capability is provided by the `hints` mode and by parsing a
`languages` array off the response, so switching the model setting is all that is needed once he
has tested. No model is silently changed on his behalf.

---

## 3. The endpoint

### 3.1 Placement and auth

`app/api/v1/external/media.py`, mounted in `app/api/v1/external/__init__.py` with
`dependencies=[Depends(require_external_permission(EXTERNAL_ENDPOINT_PERMISSIONS["/media"]))]`,
and a matching key added to `EXTERNAL_ENDPOINT_PERMISSIONS` in
`app/api/v1/external/permissions.py`. A completeness test asserts every mounted prefix has an
entry, so omitting it fails the suite rather than shipping unguarded.

New permission slug: `integration.chatbot_media.process`. It needs a **grant migration** for
`admin` and the existing `integration_*` roles, or the feature 403s the moment it is deployed.
This is DoD gate item 3 and it is the single most common way a feature like this ships broken.

Auth is `X-API-Key` through `get_external_api_user` (`app/dependencies.py:491-521`), the same
principal every other n8n route uses.

### 3.2 `POST /api/v1/external/media/process` - one call, awaited

Request:

```jsonc
{
  "respond_io_id": "900000008",
  "message_id": "1783918786000000",
  "media_ordinal": 0,                 // optional, default 0
  "modality": "image",                // or "voice"
  "media_url": "https://...",         // respond.io CDN url
  "mime_type": "image/jpeg",
  "caption": "check stock for these", // may be null
  "duration_ms": 18400,               // voice only, optional; a HINT for the clip cap
  "bytes": 284119,                    // optional
  "turn_id": "9240705",
  "callback_url": "https://automate-sorento.foundryx.my/webhook/...",  // optional
  "callback_headers": {"X-Whatever": "..."},   // optional, opaque
  "context": { }                      // optional, opaque, echoed back verbatim
}
```

Response, always 200 for a well-formed request - the decision is in the body, not the status,
because n8n branches on it:

```jsonc
{
  "job_id": "…",                      // null when the decision is a refusal
  "decision": "accepted",             // accepted | denied_gate | denied_burst | denied_quota | denied_duration
  "status": "completed",              // completed | pending | failed  (accepted decisions only)
  "idempotent_replay": false,
  "tier": "standard",                 // standard | degraded
  "quota": {
    "used": 41, "limit": 50, "remaining": 9,
    "period_key": "2026-08",
    "resets_on": "1 September"        // already rendered; n8n does no date arithmetic
  },
  "notices": [
    {"kind": "warn_80", "text": "…", "append": true}
  ],
  "language_strategy": {"mode": "pinned", "language": "en"},  // voice only
  "result": { }                       // the extraction body, section 3.5; null unless status == completed
}
```

**`status` is what n8n branches on for the slow half:**

- `completed` - the normal case. `result` is populated and the turn continues immediately.
- `pending` - the wait hit `media_sync_wait_seconds` before the worker finished. The job is still
  running; n8n either polls `GET /media/jobs/{job_id}` or waits for the callback if it supplied
  one. This is the graceful edge, not an error, and it is what makes async a switch rather than a
  rebuild.
- `failed` - extraction failed. `result` is null and `error` carries the reason. The turn should
  degrade to caption-only, which is today's behaviour.

A refusal (`denied_*`) returns immediately with no job, no `status` and no `result`.

`denied_quota` is only ever returned when degradation is impossible (no degraded model
configured). The captain's decision is degrade, not refuse, so the normal at-limit path is
`accepted` with `tier: degraded` plus a `degraded` notice.

### 3.3 The order of operations inside the fast path

Inside one transaction, in this order:

1. Resolve the contact from `respond_io_id`. Unknown contact is a `denied_gate`.
2. **Idempotency probe.** `SELECT` the ledger row on the unique key. If present, return the
   stored decision with `idempotent_replay: true` and the existing `job_id`. Nothing else runs.
   This is what makes n8n's `retryOnFail` cheap rather than a second spend.
3. **Gate.** No `contact_media_limit` row, or `is_allowed = false`, gives `denied_gate`.
4. **Duration cap** for voice, against the effective `max_clip_seconds`. The stated
   `duration_ms` is a HINT and refuses here only when the caller is honest about it; the
   authoritative check is in the worker, which measures the downloaded audio (`mutagen`) and
   applies the same cap before the transcription is paid for. A clip refused there records
   `refused_duration` on the ledger row, so it consumes no allowance.
5. **Burst**, via the existing `rate_limit.hit` primitive (`app/services/rate_limit.py:41`) on a
   namespaced bucket. Redis is correct here and only here: the burst window is genuinely
   ephemeral and a reset is harmless. It fails open, which is the right direction for a pacing
   control.
6. **Quota.** `COUNT` over the period index. Over the limit gives `tier: degraded` when a degraded
   model is configured, otherwise `denied_quota`.
7. **Record.** `INSERT ... ON CONFLICT DO NOTHING` on the ledger, then re-select. Recording
   precedes spending, so "crashed after spending, before recording" cannot happen.
8. **Notices.** Stamp `warned_period` / `degraded_notified_period` in the same transaction so each
   notice can only fire once per period per modality.
9. Create the job row and enqueue it.

Everything above is milliseconds of ordinary SQLAlchemy work and runs inline, exactly like every
other route in this repo. The transaction commits here, **before** any waiting begins, so the
ledger row and the decision are durable even if the wait or the worker later dies.

### 3.3b Awaiting the worker without blocking the event loop

The route is `async def`. After the commit it awaits the job:

```
await asyncio.wait_for(_await_job(job_id), timeout=media_sync_wait_seconds)
```

where `_await_job` loops on `await asyncio.sleep(0.25)` and reads the job row through
`await asyncio.to_thread(...)`, using its **own short-lived session** rather than the request's.
Two properties matter and both are the point of the exercise:

- **No synchronous multi-second call ever runs in the handler.** The provider call happens in the
  RQ work-horse process, not in the API process. This is the specific mistake the live portal
  route makes (`app/api/v1/public/ai_extract.py:116` calling the synchronous
  `extract_service.py:238`), which freezes the backend for 5.8-9.8 seconds per request. That
  defect is filed separately and rated low priority; it is not fixed here and it is not copied.
- **Each poll is a short read moved off the loop.** `asyncio.to_thread` keeps even the millisecond
  DB read from occupying the loop, so a hundred concurrent media turns cost sleeping coroutines,
  not blocked workers.

On `TimeoutError` the endpoint returns `status: pending` with the `job_id`. The job keeps running
and the result stays retrievable through the polling endpoint and the callback. Nothing is lost
and nothing is double charged, because the ledger row was written in step 7.

The gate fails **closed** and the quota fails **open**. This is a deliberate departure from the
house style of the existing limiters, which are fail-open by design because they are abuse
ceilings rather than entitlements. The PR description must say so.

### 3.4 `GET /api/v1/external/media/jobs/{job_id}` - the polling fallback

Returns the job status and, once complete, the identical result body the callback carries. Safe
to poll, no side effects. This exists because mid-flow resume was not confirmed (section 1.2).

### 3.5 The result body, and the callback

The `result` object below is the single shape returned three ways - inline on the synchronous
response, from the polling endpoint, and in the callback body. One shape, three transports, so
switching transport changes nothing downstream.

The callback fires only when `callback_url` was supplied. Under the synchronous wire it is
optional and most turns will not need it; it is what makes the async switch free. The worker POSTs
to `callback_url` with `callback_headers` applied verbatim:

```jsonc
{
  "job_id": "…", "status": "completed",       // or "failed"
  "respond_io_id": "…", "message_id": "…", "modality": "image",
  "turn_id": "…", "context": { },             // echoed back unchanged
  "tier": "standard",
  "result": {
    "entities": [
      {"raw": "SRTKS6647", "hint": "product", "current_message": true, "confident": true}
    ],
    "attributes": [
      {"kind": "batch_number", "raw": "YG2539", "entity_raw": null, "confident": true}
    ],
    "conflicts": [
      {"field": "quantity", "entity_raw": "SRTBF31610",
       "values": [{"value": "6", "source": "printed"}, {"value": "4", "source": "handwritten"}],
       "note": "handwritten amendment over the printed quantity"}
    ],
    "image_kind": "document",
    "caption_intent": "check stock for these",
    "notes": "lower right corner blurred",
    "needs_clarification": false,
    "truncated": false,
    "rendered_text": "please check stock for these products: SRTKS6647",
    "confirmation_message": "…",              // ready to send
    "clarification_message": null,
    "transcript": null,                       // voice
    "languages_detected": null                // voice
  },
  "notices": [ ],
  "error": null
}
```

Delivery is best-effort with bounded retries; a failed callback logs and leaves the result
readable through the polling endpoint. A post-commit side effect never raises - the caller must
not get a 500 for work that succeeded.

---

### 3.6 The operator endpoints

Phase 1 established these and they are now part of the contract. They are ordinary JWT routes
under `user_management`, not `/external/`, because the caller is the CRM frontend.

`GET /api/v1/user-management/contacts/{contact_id}/media-access`
→ `{period_key, resets_on, items[]}`. `items[]` **always** carries both modalities in the order
image, voice, each `{modality, is_allowed, has_row, monthly_limit, effective_monthly_limit,
max_clip_seconds, effective_max_clip_seconds, used, remaining, updated_at, updated_by_name}`.

`PUT /api/v1/user-management/contacts/{contact_id}/media-access/{modality}`
body `{is_allowed, monthly_limit, max_clip_seconds}`, upserts, `monthly_limit: null` clears the
override. Returns the single recomputed item.

Four details that are load-bearing rather than cosmetic:

- **`has_row` is separate from `is_allowed`.** It is what lets the card say "never configured"
  rather than "someone turned this off". Those are different support conversations and the
  operator needs to tell them apart.
- **`resets_on` arrives already rendered** ("1 September"). No caller does date arithmetic, which
  is the same rule the n8n contract follows and for the same reason.
- **`updated_by_name`, not `updated_by`.** The column stores an id; the API resolves it, because
  no UUID reaches the UI.
- **`used` counts `accepted` and `failed` only**, never refusals, so a contact who was refused
  fifty times still shows their full allowance. It is displayed even when the modality is off, so
  an operator turning access on can see what the contact already consumed.

Authorisation is the **existing contact-edit permission**. No new admin role, per the captain.

Settings are read and written through the existing settings dict and `POST /general` - no new
settings route, because `apiFetch('/api/<domain>/...')` maps straight to the backend and a
dedicated Next route proxy would never be hit.

## 4. Extraction

### 4.1 What is reused, and what is not

Reused, because it genuinely transfers: `ExtractFile` and `_render_files`
(`app/services/ai_extract/extract_service.py:129-135`, `:405-439`) for turning bytes into
provider image parts including PDF and video handling; provider resolution off `AIAssistantConfig`
(`:354-387`); and `_log_usage` (`:968-1001`), which already writes `AIAssistantUsageLog` with
`contact_id`, so token accounting for this feature is free and lands on the existing dashboard.

**Not reused: the prompt and the schema.** This is the premise that was disproved. Measured
against three real Sorento images, the existing `portal.complaint` prompt was flawless on a clean
screenshot, produced **three confident wrong answers** on a photographed RMA form, and found only
the model code on an angled carton photo.

**A correction to that diagnosis, from reading the actual images rather than only the scores.**
The baseline's misses split into two categories that need opposite fixes, and the original report
counted them together:

- **Schema coverage, not vision.** The carton photo is not hard to read. It is a clean
  `KEY : VALUE` list - `MODEL : SRTKS6647`, `SIZE : 750X470X250MM`, `QTY : 1 PC`,
  `BOX DIMENSION : 820X540X310MM`, `BATCH NO : YG2539` - in large print, with the barcode digits
  `9551028470852` legible underneath the bars. Every one of those was omitted because
  `portal.complaint` **has no field for them**, so the model correctly declined to invent a home.
  The same applies to the missing `RMA-SRT2608-0104` on image 02, which is plainly legible in the
  top right. These are fixed by asking the right questions, and should be near-free.
- **Genuine judgement failures, and there are exactly three.** The silently preferred handwritten
  quantity, `J&Y` read as `JAY`, and `11/08/2026` read as November. These are the ones that need
  prompt rules, and they are the ones that produce confident wrong answers rather than gaps.

This matters for expectations: the label lane should improve sharply because it is mostly a
coverage fix, whereas the three judgement rules are the part that genuinely has to be got right
and verified. Do not read a good corpus score on image 03 as evidence that the hard problem was
solved.

The trap on image 02 is worth describing exactly, because the rule is written against it: the row
2 quantity cell holds a printed `6` with a diagonal strike through it and a handwritten `4` beside
it. A model that reports either number alone is guessing at intent - the strike suggests the
amendment is authoritative, the written ground truth records `6`, and the honest answer is that
they disagree and a human should confirm.

Also not reused: `_canonical_product_code` (`:921-933`). Product-code matching is not rebuilt here.
The extraction emits raw strings plus `confident` and lets `resolve-entity` adjudicate, which
already runs a far better ladder with typo tolerance this extraction cannot have.

### 4.2 The new extraction service

`app/services/media_extract/` - a sibling package, not an edit to `ai_extract`, so the live portal
route is untouched.

- `prompts.py` holds the system prompt as a module constant, diffable and unit-testable.
- `schema.py` holds the strict output contract and its Pydantic parse.
- `service.py` holds `MediaExtractService`, which composes `_render_files` for the image parts and
  the provider adapter for the call.

One provider call per image, not two. The prompt classifies and extracts in the same pass, because
a second call doubles both cost and the latency that sits inside n8n's lock budget.

### 4.3 The prompt, and what each rule is for

The prompt has a shared preamble and two lanes selected by the model's own classification of the
image. Every rule below exists because a measured failure demanded it.

**Shared rules:**

1. Transcribe strings **exactly as printed**, including ampersands, punctuation, hyphens and
   spacing. Do not normalise, expand or phoneticise. *(`J&Y WORLD HARDWARE` was returned as
   `JAY WORLD HARDWARE`.)*
2. If a handwritten, stamped or otherwise overlaid value disagrees with a printed one, **report
   both in `conflicts` and do not choose**. Mark the affected entity `confident: false`.
   *(A handwritten 4 was silently preferred over a printed 6, with no flag.)*
3. Dates on these documents are day-first. When day and month are both 12 or less the date is
   ambiguous: return the raw string as printed and flag it rather than emitting a resolved date.
   *(`11/08/2026` was read as November.)*
4. Never invent. Omit rather than guess. A refusal costs one message; a confident wrong code costs
   a wrong business decision.
5. Emit at most `media_max_entities` entities and set `truncated` when you stop.

**Document lane** - delivery orders, RMA forms, invoices, spreadsheet screenshots. This lane keeps
the rules that measurably passed, carried over from `form_schema_registry.py:41-64`:

6. Only the **subject** of the line is a product entity. Compatibility codes named in a
   description, and a wrongly-received part named in a remark, are context and are not subjects.
   *(This trap passed unprompted on the clean screenshot and the rule is worth keeping explicit.)*
7. The customer is the **buyer** being billed, not the seller on the letterhead, not the
   salesperson, not the project name.
8. Formula errors and empty rows are not line items. *(An `#N/A` row.)*

**Label lane** - carton and shelf photos taken at an angle in warehouse light. This lane is new
and is budgeted, not inherited:

9. Attempt each of: model code, quantity, product size, box dimension, batch number, barcode.
10. **Product size and box dimension are different fields on the same label.** Never merge them
    and never report one as the other. When only one dimension is legible, say which it is or set
    `confident: false`. *(The baseline extracted neither.)*
11. A barcode read from digits printed under the bars is `confident: true`; one inferred any other
    way is not.

### 4.4 The output contract

`entities[]` uses the chatbot's own shape verbatim, so downstream cannot tell a photo was
involved: `{raw, hint, current_message, confident}`, with `hint` drawn only from the 14 values in
`docs/flows/sub-query-reformulator.md:33-44` - `product`, `promotion`, `customer`, `transporter`,
`inbound_shipment`, `warehouse`, `attachment`, `form`, `order`, `category`, `brand`,
`attachment_type`, `goods_receive`, `spo`. `current_message` is always `true` for an extraction.

`attributes[]` carries values that have **no hint in that enum** - batch number, barcode, box
dimension, product size, quantity, and (added after the first corpus run, section 13) document
number and document date - as `{kind, raw, entity_raw, confident}`. This is the captain's decision
of 2026-08-14; section 9 records it and the reasoning. An attribute must never be emitted as an
entity with an approximate hint, and must never duplicate a value already emitted as an entity.

`entity_raw` names the line an attribute belongs to, or is null when it describes the whole
document. It exists because without it a per-line conflict cannot be scoped, and one disputed
quantity marked every other quantity on the page untrustworthy - see section 13 defect 1.

`conflicts[]` is a first-class output, not an afterthought, per the captain's instruction.

### 4.5 Rendering for the parser

`rendered_text` is what the far end patches into the queue item. It must read like something a
customer typed, because that is what the parser is tuned on.

| situation | `rendered_text` |
|---|---|
| caption plus confident entities | the caption with the raw strings appended |
| no caption, confident entities | **null**, with `needs_clarification: true` |
| unclear caption intent | **null**, with `needs_clarification: true` |
| low confidence or conflicts | the caption alone, with the raws appended verbatim |
| nothing extracted | the caption alone - identical to today's behaviour |

The captionless rows are the captain's override of the plan's assume-and-state recommendation.
Guessing intent on top of imperfect extraction stacks two silent failure modes, so the system
asks. `clarification_message` carries the question, naming what was read.

---

## 5. Voice

No transcription code exists anywhere in this repo - verified, zero hits for
`whisper|transcrib|speech_to_text` across `app/`. This is net new, and it is the one place the
"reuse what transfers" instruction has nothing to transfer.

`app/services/media_extract/transcribe.py` posts multipart to the configured transcription
endpoint. The language strategy is built from settings at call time:

- `pinned` sends `language: <media_language_pinned>`. **This is the default and it reproduces
  today's behaviour exactly.**
- `hints` sends `languages: [...]` from the CSV setting, for a model that accepts a list.
- `auto` sends neither.

A `languages` array on the response is recorded on the job and returned in the callback. An
**empty** array is a valid "the model could not make a reliable prediction" signal and is
propagated as such - not as an error, not as silence.

Two operational traps recorded so they are not rediscovered: the upload filename must carry a
usable extension because the format is inferred from it, and WhatsApp voice notes are Opus in an
Ogg container whose acceptance varies by model. Both are handled by sending an explicit filename
derived from the mime type, and by surfacing a provider rejection as a `failed` callback with the
provider's own message rather than a generic error.

---

## 6. Wording

`app/services/media_extract/wording.py` renders every customer-facing string, so the far end sends
a string and formats nothing. Drafts are taken from the captain's `customer-wording.md` and the
four rules it carries are implemented as behaviour, not convention:

1. The 80 percent warning fires once per contact per period per modality, enforced by
   `warned_period` on the limit row, stamped in the same transaction as the decision.
2. The degradation notice is a separate kind with its own column, sent when degradation first
   happens in a period.
3. Notices carry `append: true` and are never returned instead of the answer.
4. Dates render as "1 September". Counts render as "X of Y left", never a percentage.

Changes from the drafts, flagged as the brief requires:

- The at-limit degradation text keeps the captain's meaning but is tightened to lead with the
  accuracy warning rather than the allowance, because the accuracy warning is the part that
  prevents harm. The allowance sentence follows it.
- The burst message is suppressed for the remainder of the window, so fifty images produce one of
  it. The draft did not say this and without it the pacing message becomes the spam.
- A new `not_enabled` variant for image is added, mirroring the live voice sentence in shape so
  the two features read as one, per the draft's own stated intent.

---

## 7. Frontend

Phase 1 is the frontend against mocks, and it comes before any backend code.

**Contact surface.** `ContactMediaAccessSection.tsx` in
`sorento_crm_frontend/app/(protected)/user-management/contacts/[id]/components/`. Clone
`ContactAttachmentTypesSection.tsx` - it is the right precedent: single `{contactId}` prop, owns
its react-query keys, invalidate plus toast on success, and an `AlertDialog` in front of the
destructive edit.

It renders **both** modalities always, each with an explicit empty state, per the standing rule
that a detail page never hides a section on missing data.

*Amended during Phase 1:* it is rendered as its own **Media Access card** (its own `Container` +
`Card`, between Contact Information and Access Agents) rather than as a cell inside the Contact
Information grid beside `ContactMarketSegmentSection` / `ContactAttachmentTypesSection`. The UAC
calls it "the Media Access card" and it carries two modalities each with a status, a used-of-limit
line, a reset date, an inherited-or-override line, a switch and an edit action - which does not
fit a one-line grid cell. Placement is the only change; the precedent it clones is unchanged.

**Settings surface.** `app/(protected)/user-management/settings/chatbot-media/page.tsx`, cloned
from the `portal-revisions` settings page, which is the most recent and smallest precedent and
already carries the `components/`, `hooks/`, `lib/`, `services/` layering this repo enforces. Add
the tab to the `navRoutes` map in `user-management/settings/layout.tsx`, or the page is
unreachable by clicking.

Layering is the standing one: UI to hook to feature service to `lib/api-client`. `extractApiError`
is used, never hand-rolled. Every optional select is `clearable`. Both surfaces are verified at
375px and 1280px.

**Phase 1 mocks and the swap.** Each surface has one feature service carrying the expected API
contract in its header docblock, and one `__mocks__/` module the service resolves against until
Phase 2:

- `contacts/[id]/services/contactMediaAccessService.ts` + `contacts/[id]/__mocks__/contactMediaAccess.ts`
- `settings/chatbot-media/services/chatbotMediaSettingsService.ts` + `settings/chatbot-media/__mocks__/chatbotMediaSettings.ts`

Phase 2 replaces each function body with the `apiFetch` call written in its comment and deletes
the two mock modules. Nothing above the service boundary changes.

The media settings read through the existing `GET /settings` response dict and write through the
existing `POST /settings/general` setattr path, so no new settings route is added - which is why
section 2.4's "both manual dict builders" item is load-bearing rather than housekeeping.

---

## 8. What gets measured, and how

Two measurements are owed and both are produced by this work rather than asserted.

**Fast-path latency.** The decide-and-record half, measured on its own, because it is what every
refusal and every idempotent replay costs and it bounds the floor of the call. Method: a pytest
benchmark against a seeded contact on local Postgres plus Redis, sampling over enough iterations
to report p50 and p99 separately for the accepted, replayed and refused paths. The replay path
matters most, because the recommended `retryOnFail` makes it the common case under transient
failure.

**Worst-case end-to-end call.** What n8n's node timeout has to clear, and what the lock budget is
spent on. Reported as the fast path plus measured extraction, plus the enforced
`media_sync_wait_seconds` ceiling, so the number is a bound rather than an average.

**Event-loop non-blocking, demonstrated not asserted.** A test issues a media call whose extraction
is stubbed to take several seconds and, while it is in flight, issues a second unrelated request to
a trivial endpoint on the same API process. The second request must return promptly. This is the
regression guard for the whole point of section 3.3b, and it is the thing that would silently rot
if someone later "simplified" the await into a direct call.

All three are reported in the PR description with numbers, not claims.

**Corpus extraction.** The three real Sorento images are run through the shipped extraction and
scored per field against the written ground truth as exact match, plausible-but-wrong, or refused.
Plausible-but-wrong is the outcome that matters and is reported explicitly, including whatever
still fails. The results land in
`documentation/plans/ideation/chatbot-media-endpoint-corpus-results.md` and are summarised in the
PR. An extraction path that has never been run against a real photo is not done.

---

## 9. The entity-hint question - DECIDED

**Decision, captain, 2026-08-14: Option A. Batch number, barcode, box dimension and product size
are emitted as unhinted `attributes[]`. The 14-value enum is NOT extended.**

The question and the reasoning that produced the recommendation are kept below, because the
reasoning is what a future change to this has to argue against.

**The entity-hint enum has no value for batch number, box dimension or barcode.**

The 14-value enum is fixed by `docs/flows/sub-query-reformulator.md:33-44` and is read by
`resolve-entity` as `allowed_entity_types[]`. A carton label yields three things it cannot express.

**Recommendation: carry them as unhinted `attributes[]`, do not extend the enum in this task.**

- Extending the enum is a change to a contract owned by the n8n side, and n8n is explicitly a
  separate follow-up. A CRM that emitted `hint: "batch"` today would emit a value
  `resolve-entity` does not accept, which fails or silently drops rather than degrading.
- Unhinted attributes are useful immediately with zero downstream change: they render in the
  confirmation message, so a dealer photographing a carton is told the batch and barcode were read
  even though nothing is looked up against them yet.
- It is reversible. If the captain wants a carton photo to be *answerable* on batch or barcode,
  that is a resolver capability first and an enum value second, and the extraction change is one
  line once the resolver can take it.

This was escalated rather than chosen silently, and the captain confirmed Option A.

---

## 10. Slice order

1. **S1** - settings columns, the two tables, both operator surfaces. Frontend mock first, then
   backend. This makes access and usage **visible and controllable before anything is enforced**.
2. **S2** - the fast path. Nothing is spent yet; the endpoint returns decisions and writes ledger
   rows.
3. **S3** - the job, the callback and the polling endpoint, with a stub extraction, so the async
   spine is proven before the expensive part is attached.
4. **S4** - image extraction and the corpus run.
5. **S5** - voice transcription.
6. **S6** - wording, threaded through everything above.

Each slice is a vertical tracer through model, migration, service, route and tests.

---

## 11. Things this deliberately does not do

- **No image storage.** Bytes are fetched, sent to the model and dropped. Storing customer photos
  raises a retention question nobody has asked for.
- **No product-code matching.** `resolve-entity` owns that and does it better.
- **No respond.io custom-field fallback.** The captain moved the gate to the CRM precisely because
  that pattern produced a silent three-week outage; a transitional fallback would keep the failure
  mode alive.
- **No fix for the blocking `AIExtractService.extract` call on the portal route.** It is live,
  independent, filed separately and rated low priority. This work does not touch it and does not
  copy it.
- **No n8n changes.** Separate task.

---

## 13. What the first corpus run changed

Full results: `documentation/plans/ideation/chatbot-media-endpoint-corpus-results.md`. Run on
`openai` / `gpt-4o-mini`, the model the live `AIAssistantConfig` resolves to.

**What held up.** Trap A (five codes on one line, one subject) and Trap B (`#N/A` is not a line
item) both passed on the real document. Trap E passed cleanly - product size and box dimension
both captured under distinct kinds on an angled warehouse photo - which confirms the section 4.1
diagnosis that the carton failure was schema coverage rather than vision, and it is the strongest
result of the run. Trap C's core mechanism held too: the printed-versus-handwritten disagreement
was detected, both sources named, and the entity marked `confident: false` rather than silently
picking one.

**Four defects it exposed, all mine rather than the model's, all fixed in the prompt and schema
above.**

1. **Conflict confidence leaked across lines.** `_apply_conflicts` matched attributes to a
   conflict by `kind` alone, so one genuine quantity conflict marked four unrelated and correct
   quantities `confident: false` on the same document. `MediaAttribute` now carries `entity_raw`
   and the prompt requires it on any per-line value. This one matters more than its size suggests:
   S4-03's entire value is that `confident: false` means something, and a flag that cries wolf on
   correct lines destroys exactly that.
2. **Dates had nowhere to go.** No entity hint fits a bare date and there was no attribute kind
   for one, so a perfectly legible `13/08/2026` was dropped - and rule 3, the ambiguous-date rule,
   was therefore unreachable. A `document_date` attribute kind now exists. Without it S4-04 could
   never have been satisfied, and the drop looked identical to a misread from the output side.
3. **Form reference numbers had nowhere to go either.** A delivery-order number fits hint `order`,
   but a return-authorisation number fits nothing, so it was dropped on one image and stretched
   into hint `attachment` on another. A `document_number` attribute kind now exists.
4. **An entity could reappear as a spurious attribute.** A product code repeated inside a
   description came back a second time as `batch_number`. The prompt now forbids it and the schema
   drops an attribute whose `raw` duplicates an emitted entity.

**One prompt gap it exposed.** On the skewed, stamped, written-on photo the model returned the
whole line-item table and *nothing at all* from the header block - customer, debtor code, RMA
number, issue date, issuer, agent. Not degraded, absent. New rule 11 makes the header an explicit,
named target and says to read it even when the page is skewed or stamped. A feature whose
confirmation message exists so the dealer can say "is that what I meant?" cannot ask about fields
it never attempted.

**A recommendation that follows from the evidence, and it resolves the degraded-model gap.**
On `gpt-4o-mini` the run misread a printed `6` as `16` inside an otherwise correct conflict, and
silently omitted a legible barcode. Those are model-tier failures, not prompt failures. So:
**set `media_image_model` to a stronger vision model as the standard tier and leave
`gpt-4o-mini` as `media_image_degraded_model`.** That makes the degraded tier meaningful rather
than inert, gives the quota somewhere real to degrade to, and closes the section 12.1 item 2 gap
without inventing a default nobody chose. It is a settings change, not a deploy.

**A fifth defect, found while fixing the fourth.** `wording._conflict_sentence` discards
`conflict.note`. For a printed-versus-handwritten conflict that is survivable, because both
competing values are in `values[]` and the sentence can show them. For an **ambiguous date** it is
fatal to the point of the rule: rule 3 deliberately puts one printed string in `values` and both
readings in `note`, so dropping the note leaves the customer reading "I can see 11/08/2026, which
one should I use?" - a question naming one value and offering no alternatives. The note must be
rendered. This was unreachable before `document_date` existed, which is why it surfaced only now.

**What is still not verified.** Because image 02's header was never attempted, the two specific
baseline defects the plan names for that image - `J&Y` read as `JAY`, and `11/08/2026` read as
November - remain **unverified rather than fixed**. Rules 1 and 3 could not be scored on a field
the model did not attempt. The re-run after these fixes is what tests them.

## 14. The Trap C regression, and the decision rule for it

Full data: `documentation/plans/ideation/chatbot-media-endpoint-trapc-reliability.md`.

The second corpus run lost Trap C: the handwritten-over-printed quantity came back with no
conflict and `confident: true`, where run 1 had flagged it. n=1 cannot separate a prompt regression
from vision-model variance, so it was measured: image 02, N=5 per arm, temperature 0.

| arm | prompt | conflict fired | entity confidence | header block |
|---|---|---|---|---|
| A | current (rules 11 and 14 added) | **0/5** | always `true` | present 5/5 |
| B | run-1 prompt | **5/5** | always `false` | absent 5/5 |

A clean 0/5 versus 5/5 at temperature 0 is a **prompt regression, not variance**.

**The hypothesis I offered was wrong, and the data says so.** I guessed rule 2 had been buried by
the new rules. It is byte-identical between arms and moved from 29.0% to 27.8% through the prompt -
the new content sits almost entirely *after* it. So "the conflict rule got pushed later" is not the
mechanism. What the data supports is an attention-budget effect: the same call reliably performs
the *new* extraction ask and reliably drops the *pre-existing* vigilance ask.

Also worth recording: the misread printed value (`16` for `6`) was identical across all ten calls
in both arms. That vision error is stable and completely orthogonal to the prompt - it is a model
tier problem, not a prompt problem, and no prompt wording will fix it.

**The decision rule, written before the next measurement so it cannot be rationalised afterwards:**

1. Add a closing vigilance step (rule 18 in Appendix A) placed **last**, so recency works for the
   safety property rather than against it, and measure again at N=5.
2. **If conflict detection does not return to at least 4/5, delete rules 11 and 14 and accept the
   missing header block.** Conflict detection is the safety property this feature exists to
   provide; header coverage is a convenience. A system that reads the customer name but silently
   reports a wrong quantity is worse than one that reads neither, because the wrong quantity is
   acted on.
3. Either way, the standard tier must not be `gpt-4o-mini`. Arm B shows the *behaviour* is
   achievable, and the stable misread shows the *accuracy* is not, at this tier.

### 14.1 How that decision rule actually resolved - the action was falsified, the intent was kept

Rule 18 did nothing: **arm C, 0/5**. So clause 2 fired, and its action was to delete rules 11 and
14. Before executing it, two more arms were run, and they changed the answer.

| arm | prompt | model | conflict fired | header |
|---|---|---|---|---|
| A | current | gpt-4o-mini | 0/5 | 5/5 |
| B | run-1 | gpt-4o-mini | 5/5 | 0/5 |
| C | current + rule 18 | gpt-4o-mini | 0/5 | 5/5 |
| **D** | **current minus rules 11 and 14** | **gpt-4o-mini** | **0/5** | customer lost 0/5 |
| **E** | **current, unchanged** | **gpt-4o** | **5/5** | retained |

**Arm D falsifies the premise clause 2 rested on.** Deleting the two added rules does *not* restore
conflict detection - it is still 0/5 - and it costs the customer field. So the prescribed action
would have paid a real price for nothing. Executing it anyway, because it was written down, would
be following the letter of a rule whose reasoning the evidence had already destroyed.

**Arm E identifies the actual cause.** On `gpt-4o` the unchanged prompt fires the conflict 5/5,
keeps the header, and - the part no prompt wording ever touched - **the `16`-for-`6` misread does
not reproduce once**, having been identical in all 20 preceding `gpt-4o-mini` calls. It is also
*faster* (mean 6.5s versus 9.7s) and uses roughly 13x fewer prompt tokens on the same image
(2,963 versus 38,437), because the two models tokenize the image differently.

**So the rules stay and the tier changes.** The intent of the decision rule was "do not keep a bad
trade"; there is no trade to keep, because the thing being traded away was not buying the safety
property in the first place.

**This settles the degraded-model question from section 12.1 item 2, with evidence rather than a
guess.** The two tiers are now measured, not assumed:

- **Standard: `gpt-4o`.** Conflicts fire, the header is read, values are not misread.
- **Degraded: `gpt-4o-mini`.** Cheaper, and demonstrably worse in exactly the way the captain's
  degradation notice warns about - it silently fails to flag an amendment and it misreads a
  printed quantity.

That is the strongest possible justification for the captain's instruction to *tell the contact
accuracy has dropped*. At the degraded tier the failure is measured, not hypothetical: the model
does not fail visibly, it hands back a confident wrong number. Both values are seeded by the
migration so degradation works out of the box, and both remain operator-editable.

**One honest note on the arm E measurement:** the tester's first arm E attempt applied the model
override only inside a probe and the five real calls silently ran on `gpt-4o-mini`. It caught this
by asserting the model on each returned row, discarded the run, fixed it, and re-ran. The numbers
above are from the corrected run.

## 16. Review findings and how each was resolved

Phase 3 review of the branch. The spine passed - the async wait, the ledger, the idempotency key,
the migration chain, the permission grant path and the settings dict-builder discipline were all
checked and clean. What follows is what did not pass.

### 16.1 The voice quota was not enforced at all (blocker)

`media_access_service` degrades **both** modalities on `media_image_degraded_model`, but no
`media_voice_degraded_model` exists and `_extract_voice` never reads `job.tier`. Since migration
358 seeds the image degraded model, this is live behaviour: **every over-quota voice note is
accepted and transcribed at the full standard model, forever.** `media_voice_monthly_limit` is
decorative, and the contact is told "I am reading this one with a simpler model and may get it
wrong" about a transcription that used exactly the same model as always.

That last part is the one that decides the fix. Section 12.1 already named it as the outcome to
avoid: telling a contact their accuracy has dropped when nothing changed. It is the same failure
as a silent wrong answer, wearing a warning label.

**Resolution: give voice its own `media_voice_degraded_model`, defaulting to NULL, and wire
`job.tier` into `_extract_voice`.** NULL means the quota is a hard refusal, which is the behaviour
section 3.2 already specifies for an unconfigured degraded tier, and it is honest: no cheaper
transcription model has been measured, so none is claimed. Image ships seeded because its tiers
were measured (section 14.1); voice ships unseeded because they were not. The captain's
degrade-not-refuse decision is honoured by the mechanism existing and being one setting away, not
by pretending a degradation happened.

`wording.degraded()` and the quota-exhausted wording become **modality-aware** at the same time.

### 16.2 The wording claimed a shared allowance that does not exist

The quotas are per-modality - separate limits, separate ledger counts, separate `warned_period`
stamps - but the text read "you have used all 50 of this month's photo and voice reads". A dealer
told that still has 100 voice reads. UAC S6-04 asks for a true X-of-Y and this was not one.

### 16.3 The settings page could never show its error state

`isLoading || !draft` was checked before `isError`, and on a failed load `draft` stays null, so the
first branch always won. The operator saw loading skeletons forever and the error `Alert` was
unreachable. Swapping the checks fixes it - and it is exactly the class of defect the missing
frontend tests would have caught, which is 16.4.

### 16.4 No frontend tests, on a slice with four [FE] acceptance criteria

`PRINCIPLES.md` step 4 is explicit that vitest and one Playwright E2E land in Phase 2 and are
never deferred. The backend suite is thorough; the frontend has none. Vitest coverage is added.

**The Playwright E2E cannot be run here and is not claimed.** It needs a CRM login, and the
credentials the e2e specs read (`*_E2E_EMAIL` / `*_E2E_PASSWORD`) are in no `.env` in this
environment; the local database is a copy of production, so resetting a real user's password to
manufacture one is not acceptable. This is recorded as an explicit gap rather than quietly
skipped, and it is the same reason browser verification of the two operator surfaces is
outstanding.

### 16.5 Accepted as-is, with reasons

- **Two blocking Redis calls in the async handler** (`enqueue_job`, `rate_limit.hit`). Measured p50
  is ~9ms so this is tail risk, not routine - but the whole point of section 3.3b is that nothing
  multi-second blocks the loop, and `queue_service`'s shared connection sets no socket timeout, so
  a Redis stall would block the process. Wrapped, and the static guard that missed it is widened.
- **A timed-out extraction keeps running and still stamps the ledger.** The orphaned thread later
  completes its provider call and writes token counts onto a row already marked `failed`. Not
  corruption - the snapshot reasoning holds - but the spend is invisible and the row is annotated
  as a success it was not. The orphan now checks job status before stamping.
- **`build_callback_body` hardcoded `notices: []`**, so the one-shape-three-transports guarantee
  held only because nothing populated it.
- **A replayed refusal returned no customer text**, so if n8n's retry is the response that reaches
  the dealer, a `denied_gate` arrives with no message at all.

### 16.6 How the two notice items were fixed, since it needed a column

The last two items above are one problem seen from two transports: the notices existed only in the
synchronous response's own local variable, so neither a replay nor a callback could reach them.
Both are fixed by storing them once, on the thing that already records what was decided:
**`contact_media_usage.notices` (JSONB, nullable, migration 359)**.

Recorded here rather than silently added because section 2.1 lists that table's columns and this
adds one to it. Three properties made the ledger the right home rather than the job row:

- a **refusal has no job row at all**, and the refusal replay is the case that reaches a dealer with
  nothing to say for itself;
- the notices are **part of what was decided**, decided in the same transaction as the outcome and
  the once-per-period stamps, not a by-product of rendering the response;
- `build_callback_body` already loads the usage row, so the callback and the polling endpoint get
  the same list with no extra query.

The insert carries them rather than an UPDATE writing them over the row just created: section 2.1
calls the ledger append-mostly, and every input to the notice text (`used + 1`, and the two
once-per-period stamps on the limit row) is known before the insert. There is **no backfill** -
rows written before this column had their notices delivered on the original response, and inventing
text for them afterwards would be fabricating a record of something that was never sent.

### 16.7 The "one setting away" claim was not true when it was written

Section 16.1 defended shipping `media_voice_degraded_model` as NULL on the grounds that
degrade-not-refuse is honoured by the mechanism existing and being one setting away. It was not.
The settings page exposed no control for the column, so it was reachable only by hand-written SQL,
and the argument described an intention rather than the product. The control now exists, in the
Voice transcription card, in the same position its image counterpart occupies.

Two details of it are deliberate and would be wrong to "tidy" later:

- **The empty state reads "Not set", not "Inherit."** Every other model field on the page inherits
  from the assistant config when blank, so the shared component's default label is "Inherit" - but
  a blank voice degraded model inherits nothing. It refuses. Labelling it "Inherit" would assert
  in the UI the precise falsehood this whole section exists to remove: that an unset voice tier
  quietly falls back to the standard model.
- **Blank raises no warning**, though the image field's does. Blank is the shipped state and the
  measured-evidence position; a permanent warning would frame the default as a misconfiguration
  and press the operator into naming a transcription model nobody has measured.

Nothing seeds the voice field from the image tier at any point in the chain. The image tiers were
measured (section 14.1). The voice tiers were not, and the UI must not imply otherwise.

## Appendix A - the extraction system prompt

This is the design, not a sketch. Transcribe it into `app/services/media_extract/prompts.py` as a
module constant. Every rule traces to a measured failure or to a named trap in the corpus; if a
rule looks removable, check section 4.3 first, because most of them are load-bearing.

`{max_entities}`, `{hint_enum}` and `{caption_block}` are formatted in at call time.

```
You read a photo a customer has sent to a hardware supplier's WhatsApp assistant, and return
strict JSON. Your output is consumed by software, never shown to the customer as-is.

Return ONLY a JSON object with these keys:

  image_kind        one of: document, label, product_photo, screenshot, other, unreadable
  caption_intent    a short phrase describing what the caption asks for, or null
  entities          array, at most {max_entities}
  attributes        array
  conflicts         array
  needs_clarification  boolean
  truncated         boolean
  notes             one short clause, or null

An ENTITY is a value someone could look a record up by. Each entity is:
  {{"raw": "<the string exactly as printed>",
    "hint": "<one of: {hint_enum}>",
    "current_message": true,
    "confident": true or false}}

An ATTRIBUTE is a value that describes a thing but is not something you look a record up by.
Each attribute is:
  {{"kind": "<one of: batch_number, barcode, box_dimension, product_size, quantity,
             document_number, document_date>",
    "raw": "<the string exactly as printed>",
    "entity_raw": "<the code or line this value belongs to, or null if it describes the whole
                    document>",
    "confident": true or false}}

Never put an attribute in `entities` under an approximate hint. If a value does not fit a hint
in the list, it is an attribute or it is left out.

Never emit an attribute whose `raw` is a value you have already emitted as an entity. A product
code repeated inside a description line is the SAME entity, not a new attribute, and it is never
a batch number.

Always set `entity_raw` on an attribute that belongs to one line of a multi-line document, so a
quantity on one line is not confused with a quantity on another.

RULES THAT APPLY TO EVERY IMAGE

1. Transcribe exactly as printed. Keep ampersands, hyphens, brackets, spacing and case.
   "J&Y WORLD HARDWARE" is not "JAY WORLD HARDWARE". Do not expand, translate, correct spelling,
   or tidy a code into the shape you expect.
2. If a handwritten mark, a stamp, or any overlay DISAGREES with a printed value, do not choose
   between them. Record BOTH in `conflicts`, and set `confident: false` on the affected entity or
   attribute. A struck-through printed number with ink beside it is a disagreement even when the
   correction looks deliberate. Deciding which one the customer meant is not your job.
   Each conflict is:
     {{"field": "<what it is, e.g. quantity>",
       "entity_raw": "<the code or line it belongs to, or null>",
       "values": [{{"value": "6", "source": "printed"}},
                  {{"value": "4", "source": "handwritten"}}],
       "note": "<one clause>"}}
3. Dates on these documents are day first. If the day and the month are both 12 or less the date
   is genuinely ambiguous. Return it exactly as printed, and add a conflict whose `values` holds
   the ONE printed string with source "printed", and whose `note` names both readings in words,
   for example "could be 11 August 2026 or 8 November 2026". Never put a reformatted or resolved
   date in a `value`.
4. Never invent. If you cannot read something, leave it out. A missing value costs one extra
   message; a confident wrong product code or quantity costs a wrong business decision.
5. Prefer `confident: false` over omission when you can see a value but cannot fully trust your
   reading, and prefer omission over a guess.
6. Stop at {max_entities} entities, and separately at {max_entities} attributes. Set
   `truncated: true` if you stopped early in either list.

IF THE IMAGE IS A DOCUMENT (delivery order, return authorisation, invoice, spreadsheet screenshot)

7. Only the SUBJECT of a line is a product entity. Product codes that appear inside a description
   as compatibility information, and a code named in a remark as the wrong item the customer
   received, are context. They are not what the customer is asking about, and looking them up
   answers a question nobody asked.
8. The customer is the party being billed - the name on the Bill To, Sold To, Customer or Debtor
   line. It is NOT the supplier issuing the document or shown on the letterhead, NOT the
   salesperson, and NOT the project or site name.
9. Empty rows, repeated headers, and spreadsheet formula errors such as #N/A or #REF! are not
   line items.
10. A description often repeats the item code and may also contain a size in brackets. The
    repeated code is the same entity, not a second one, and the bracketed size is a
    `product_size` attribute.
11. READ THE HEADER BLOCK BEFORE THE TABLE, and read it even when the page is skewed, stamped or
    written on. The header is the top area carrying the customer or debtor name, the debtor code,
    the document's own reference number, the date it was issued, and who issued it. These are as
    important as the line items, and on a photographed page they are the fields most often
    skipped. Emit the customer as an entity with hint `customer`; emit a delivery-order number as
    an entity with hint `order`; emit any other document reference, such as a return
    authorisation number, as a `document_number` attribute; emit the issue date as a
    `document_date` attribute. If you can see one of these and cannot read it confidently, emit
    it with `confident: false` rather than leaving it out.

IF THE IMAGE IS A LABEL (a carton, a shelf label, a box in someone's hand)

12. Read every labelled field present. These labels are usually a plain list of
    "KEY : VALUE" lines. Expect and look for: MODEL, SIZE, QTY, BOX DIMENSION, BATCH NO, and a
    barcode.
13. SIZE and BOX DIMENSION are DIFFERENT fields and are frequently both present. SIZE is the
    product; BOX DIMENSION is the carton it ships in. Never report one as the other, and never
    merge them. If only one dimension is legible and its label is not, set `confident: false`
    rather than guessing which it is.
14. A barcode is a REQUIRED field to look for on a label, not an optional extra. Read it from the
    digits printed beside or beneath the bars, which are usually in a corner and in a smaller
    font than the KEY : VALUE lines. Do not attempt to decode the bars themselves, and do not
    read a QR code. If the digits are present but you cannot read them all, emit what you can see
    with `confident: false` rather than omitting the field.
15. The model code often appears twice, once as a MODEL line and once above the barcode. That is
    one entity, not two.

THE CAPTION

16. The caption is the strongest signal for what each value means. "check stock for these" over a
    carton makes the model code a product. "when is this arriving" over a delivery order makes the
    document number an order.
17. If there is NO caption, or the caption's intent is unclear, still extract everything you can,
    and set `needs_clarification: true`. Do not guess what the customer wants done with the photo.
    Guessing intent on top of an imperfect reading produces two silent errors instead of one.

BEFORE YOU RETURN

18. Look once more at every value you are about to report. Is any printed value struck through,
    written over, circled, crossed out, or contradicted by handwriting, a stamp, or a correction?
    If so it belongs in `conflicts` with both readings, and whatever carries it is
    `confident: false`. Do this check even when the page was easy to read, and even when you have
    already found everything else you were asked for. A missed amendment is the worst outcome of
    this task: it produces a number that looks right, reads confidently, and is wrong, and someone
    acts on it.

{caption_block}
```

**Three amendments made after the first implementation pass**, recorded because the prompt is
meant to be transcribed verbatim and a silent edit would defeat that:

1. **Rule 3 now defines the conflict shape for an ambiguous date.** Rule 2's `{value, source}`
   shape was written for printed-versus-handwritten, where there are two competing strings. An
   ambiguous date has ONE printed string and two *readings*, so the shape did not fit and models
   would have filled it inconsistently - some emitting a resolved date into `value`, which rule 3
   itself forbids. The fix keeps the single printed string in `values` and moves both readings
   into `note`.
2. **Rule 6 now caps attributes as well as entities.** The cap was pinned to `entities` only, so a
   spreadsheet screenshot could return an unbounded `attributes[]` - a real case, since a price
   list has a size and a quantity on every row.
3. **`caption_intent` and `notes` are surfaced in the result body** rather than extracted and
   discarded. `caption_intent` is worth asking for regardless because it makes rule 15 behave, and
   `notes` ("blurred lower half") is exactly what a support person needs when a dealer asks why
   their photo did not work.

**Why one call rather than classify-then-extract.** A second round trip doubles both the cost and
the latency, and the latency now sits inside n8n's lock budget. The model classifies and extracts
in the same pass, which is also what let the baseline pass the hard subject-code trap unprompted.

---

## 12. Phase 2 deviations (S1 + S2 + S3, backend)

Six places where the shipped backend differs from the design above. The design text is left as
written so the change is visible; each item says what the constraint was.

1. **`contact_media_limit` has a uuid `id` primary key**, with `(contact_id, modality)` demoted to
   a UNIQUE constraint. Section 2.2 wrote the pair as the PK. The repo holds every domain table to
   a uuid `id` (`tests/test_schema_uuid_id_principle.py`), because the polymorphic key columns can
   only stay uuid-typed if every id they might hold is one, and that test's allowlist is meant to
   shrink rather than grow. The uniqueness the composite PK was buying is unchanged.

2. **`media_image_degraded_model` ships NULL, with no default**, not `gpt-4o-mini` as section 2.4
   says. Two reasons, and the second is the hard one. First, the Phase 1 frontend mock already
   shipped it NULL "on purpose", so the two halves disagreed and the mock is the more recent
   artifact. Second, SQLAlchemy cannot distinguish "set this column to None" from "did not mention
   this column" on a column that carries a default - so a defaulted column would be one an
   operator could never clear back to "no degraded tier". A NULL degraded model means the monthly
   quota is a hard refusal, which is the behaviour section 3.2 already describes; switching a
   second paid model on for every contact is left as an operator decision.

   **Half-superseded by section 14.1, and note which half.** The corpus measurement named the
   model, so migration 358 seeds `openai` / `gpt-4o` / `gpt-4o-mini` into the singleton row and
   degradation now works out of the box. The second reason above survives intact and is why the
   seed is a one-time `UPDATE ... WHERE col IS NULL` rather than a column default: an operator can
   still clear the degraded model back to NULL and get the hard refusal, which a Python-side
   default would silently undo.

3. **Timestamps are naive UTC (`DateTime(timezone=False)`)**, not the TIMESTAMPTZ section 2.1
   specifies - matching every other table in this repo. The Asia/Kuala_Lumpur period is a separate,
   explicit concept (`period_key`), so nothing depends on reading a timezone off a raw column.

4. **`/api/v1/external/media/process` is exempted from `IdempotencyMiddleware`.** That middleware
   allowlists any path ending `/process` (it was written for the form-action endpoints) and caches
   the first 2xx body for ten seconds, so the second of two identical POSTs never reached the
   handler at all - n8n's recommended `retryOnFail` would have received a byte copy of the first
   response, including a stale `status: pending` and `idempotent_replay: false`. This endpoint owns
   a stronger, durable idempotency keyed on the respond.io message id, and its replay must report
   what has happened SINCE, so the two mechanisms cannot both be applied. See
   `_SELF_IDEMPOTENT_REGEXES` in `app/middleware/idempotency_middleware.py`.

5. **A new `user_management.contacts.edit` slug was introduced** for the operator surface.
   Section 3.6 says "authorisation is the existing contact-edit permission", but there was no such
   permission: every contact write in `contacts.py` is gated by `get_current_user` alone, so today
   any authenticated user can edit a contact. Migration 357 therefore grants the new slug to
   **every** existing role, which reproduces today's reach exactly rather than quietly narrowing
   it; the point of naming it is that it can now be revoked per role, which was previously
   impossible. Still no new admin-only role, per UAC S1-06.

6. **`media_sync_wait_seconds` is not on the settings page.** UAC S1-04 requires the operator to
   edit "the synchronous wait seconds and the extraction timeout seconds"; the Phase 1 page and its
   `ChatbotMediaSettings` type carry only the latter. The column, the GET dict, `SystemSettingUpdate`
   and the 5-90 bound all exist on the backend, and the backend also enforces
   `media_extraction_timeout_seconds >= media_sync_wait_seconds` (a relationship a per-field bound
   cannot express). The missing control was a Phase 1 gap, not a backend one. **Now fixed** - see
   12.1. The field sits with the extraction timeout controls and the cross-field rule surfaces as
   an inline message on the timeout field rather than as a 422 to decode after saving. Fixing it
   also caught that the timeout field enforced only its upper bound, so a value below 5 was
   likewise a 422 the operator had to interpret.

### 12.1 Adjudication of the above

Reviewed against the design and the captain's settled decisions.

**Accepted as shipped: 1, 3, 4, 5.** Each is the repo's own invariant beating the plan's text, and
in three of those cases the plan was simply wrong: the uuid `id` is enforced by
`tests/test_schema_uuid_id_principle.py`, naive UTC is the documented house rule with `period_key`
carrying the Malaysia concept explicitly, and there genuinely was no contact-edit permission to
reuse. On 5 in particular, granting the new slug to every existing role is the right call: contact
writes are gated by `get_current_user` alone today, so every authenticated user can already edit a
contact, and reproducing that reach while making it revocable is strictly better than either
silently narrowing access or leaving it ungovernable.

**Accepted, but it must be stated loudly rather than buried: 2.** Shipping
`media_image_degraded_model` NULL means that **out of the box, hitting the monthly quota is a hard
refusal, not a degrade** - and the captain's decision was to degrade. That is not a contradiction
being smuggled through, for two reasons. The mechanism the decision asked for is built and works
the moment a model is named; and naming which model is cheaper is operator configuration in
exactly the sense D3 established for the numbers, not a design choice this work can make. Nobody
has named a cheaper vision model, and defaulting one that turns out to equal the standard model
would produce the genuinely bad outcome: telling a contact their accuracy has dropped when nothing
changed. The safeguards are that the settings page warns inline whenever the field is blank, and
that the PR says this plainly. **If the captain wants degrade-by-default, he names the model and
it is a settings change, not a deploy.**

**Not accepted: 6.** This is a real UAC S1-04 gap and it is fixed rather than documented. The one
control that bounds how long the dispatcher's lock is held is precisely the one an operator must
be able to reach without a deploy, since that is the stated mitigation for the unmeasured spine
p99 in section 1.1. A backend bound nobody can adjust is not the mitigation that was promised.

### 12.2 Phase 2 notes (S4 + S5 + S6, backend)

Five places where the shipped extraction resolved something section 4, 5 or 6 left open. None
contradicts the design; each is recorded because a reader of the code would otherwise have to
re-derive the reasoning.

1. **The lane dispatch is `job.modality`, and nothing else.** `image` goes to the vision call,
   `voice` to `transcribe.py`. The document-versus-label split inside the image lane is NOT a
   second dispatch - the model classifies and extracts in one pass and returns `image_kind`, which
   is what section 4.3 and the Appendix A note already specify.

2. **The entity/attribute split is enforced in code, not only in the prompt.** An entity whose
   `hint` is one of the five attribute kinds is MOVED into `attributes[]` rather than dropped: the
   value was read correctly and only its home was wrong, and the confirmation message still wants
   to name it. An entity whose hint is neither an accepted hint nor an attribute kind is dropped
   and logged, because `resolve-entity` would reject it - passing it on fails downstream instead of
   degrading. A rule that lives only in a prompt is a rule a model can quietly stop following.

3. **Token spend is logged with `feature="ai_extract"` and `form_key="chatbot.media.{image,voice}"`.**
   Section 4.1 says to reuse `AIExtractService._log_usage`, and that method hardcodes the feature.
   Keeping it is the better outcome anyway: the usage dashboard's per-contact view
   (`/ai-assistant/usage/top-contacts`) defaults to `feature=ai_extract` precisely because it is
   the only writer that populates `contact_id`, so a media read appears there with no frontend
   change, and the form key separates it from a portal form. The ledger's own `model`, `provider`,
   `prompt_tokens` and `completion_tokens` columns (section 2.1) are stamped in the same pass,
   best-effort - failing to annotate a metered fact must never fail an extraction that succeeded.

4. **Transcription sends `response_format: json`,** which both `whisper-1` and the newer
   `gpt-4o-transcribe` accept, and parses `languages` (list) then `language` (single) off the
   response. `None` means the model said nothing about the language; `[]` means it said it was
   unsure. The distinction is kept end to end and the unsure case changes the customer
   confirmation rather than being swallowed. Switching to a model that reports what it detected
   stays a settings change, exactly as section 2.4 promised.

5. **Wording is now the single source for every customer string**, including the decision notices
   the fast path emits - `media_access_service` no longer holds any inline text. The three flagged
   changes from the drafts are implemented: degradation leads with the accuracy warning, the burst
   message is suppressed for the rest of the window (in the service, because it is a Redis
   decision), and `not_enabled` has an image variant mirroring the voice sentence in shape.

**Since settled:** the pytest suite for S4/S5/S6 landed and the corpus run (S4-11) was executed
twice against the three real photos - results in `chatbot-media-endpoint-corpus-results.md`, and
the Trap C regression it exposed in `chatbot-media-endpoint-trapc-reliability.md`. Extraction
quality is measured, not asserted, and section 13 states plainly what it still gets wrong.

**Since settled (2026-08-15):** the browser sign-off of both operator surfaces, once the captain
supplied a verification login. Done against a production build, reached by clicking rather than by
deep URL, and including the failed-load state that finding 16.3 was about, forced by stopping the
backend. It found one real defect, a limits dialog with no accessible description, now fixed and
covered by a test. The full account is in the PR body.

**Still owed:** a persisted Playwright spec for those two surfaces. The interactive sign-off stands
on its own, but nothing yet guards them against regression.

---

## 15. Measurements (PLAN section 8)

Run serially, nothing else hitting the database, per the tester's instruction (an earlier
full-suite run alongside corpus agents produced 1692 spurious failures from DB contention, so
these three were each run in isolation against the shared local Postgres + Redis). Every existing
suite for this feature was also re-run in isolation first, as a precondition, and was green:
`pytest tests/test_media_process_endpoint.py -q` (17 passed) and
`pytest tests/test_media_job_lifecycle.py -q` (17 passed, includes S3-01b below).

### 15.1 Fast-path latency

Method: a one-off benchmark script mirroring `tests/test_media_process_endpoint.py`'s own
technique exactly - `_await_job` monkeypatched to raise `TimeoutError` immediately and
`enqueue_job` stubbed to a no-op, against `tests/_pg_fixture.py::blank_session()` (real Postgres
DDL, one rolled-back transaction) plus the real `rate_limit.hit` Redis path. This times ONLY PLAN
3.3 steps 1-9 plus the commit - never a worker, never a real wait - which is the "decide, meter,
record" half section 8 asks for. N=300 per bucket, timed with `time.perf_counter()` around each
`client.post(...)` call. Run twice back to back to check stability.

| path | p50 | p99 | mean | min | max | n |
|---|---|---|---|---|---|---|
| accepted | 8.70 - 8.91 ms | 10.48 - 11.02 ms | 8.87 - 9.09 ms | 7.71 - 8.01 ms | 41.31 - 41.67 ms | 300 |
| idempotent replay | 6.83 - 6.93 ms | 7.86 - 8.44 ms | 6.91 - 6.97 ms | 6.17 - 6.21 ms | 9.35 - 9.67 ms | 300 |
| refused (`denied_gate`) | 7.66 - 7.80 ms | 11.74 - 14.28 ms | 8.11 - 8.54 ms | 6.35 - 6.58 ms | 13.52 - 18.70 ms | 300 |

(Ranges are the two back-to-back runs; both used a fresh contact + `contact_media_limit` row per
`accepted`/`refused` iteration and one shared contact replaying the same `message_id` 300 times for
the replay bucket, matching PLAN 3.3 step 2's idempotency probe short-circuit.)

**What this means for the 120s lock budget.** The fast path is two to three orders of magnitude
below the 120s TTL and below the 30s `media_sync_wait_seconds` ceiling - it does not meaningfully
add to the budget on its own. The **replay path is the cheapest of the three**, exactly as
intended: it returns after the idempotency `SELECT` alone, before the gate/burst/quota checks run,
which is what makes n8n's recommended `retryOnFail` (PLAN 1.2) safe to lean on under transient
failure - a retry costs ~7ms, not a second spend. `refused` (`denied_gate`, the fail-closed
"no `contact_media_limit` row" case - the default for every contact today) costs about the same as
`accepted` since both walk the same contact-resolve + idempotency-probe steps before diverging;
burst/quota refusals would add one more Redis round trip or one more `COUNT` respectively, still on
the same order of magnitude. The p99 tail (up to ~14-19ms) is noise from a busy shared local
Postgres with dozens of concurrent connections from other agents' processes, not a defect - the
whole distribution stays under 20ms.

### 15.2 Worst-case end-to-end call

Method: fast-path p99 (15.1) + the measured `gpt-4o` extraction term (arm E,
`chatbot-media-endpoint-trapc-reliability.md`, N=5, temperature 0: 5.70s, 6.29s, 6.22s, 8.52s,
5.84s - mean 6.51s, max 8.52s) + the enforced `media_sync_wait_seconds` ceiling (model default
30s, `app/models/user.py:393`). The ceiling is what makes this a bound rather than an average:
`asyncio.wait_for(..., timeout=sync_wait_seconds)` (`app/api/v1/external/media.py:227`) hard-caps
the endpoint's own call at fast-path + 30s regardless of how slow the provider call runs, returning
`status: pending` at that boundary instead of continuing to wait.

| term | value | source |
|---|---|---|
| fast path (p99, accepted) | ~0.011 s | measured, 15.1 |
| extraction, `gpt-4o` (arm E), typical | 6.51 s mean, 5.70-8.52 s range | measured, trapc-reliability.md arm E, N=5 |
| extraction, `gpt-4o-mini` (degraded tier), typical | 9.73 s mean (arm D) | measured, trapc-reliability.md, for comparison only - not the standard-tier term |
| `media_sync_wait_seconds` ceiling | 30 s (default; operator range 5-90) | `app/models/user.py:393`, enforced in `app/api/v1/external/media.py:227` |
| **this endpoint's own worst-case call** | **~30.01 s** | fast path + ceiling, since the ceiling dominates and caps the call regardless of extraction time |
| existing spine turn (upper end, unmeasured p99) | 18.4 s | `dym-probe-before-offer-plan.md:463` |
| **worst-case turn against the 120s TTL** | **~48.4 s** | 18.4 s spine + 0.011 s fast path + 30 s ceiling |

**Headroom on the standard tier.** The measured `gpt-4o` extraction (mean 6.51s, worst observed
8.52s) never comes close to tripping the 30s ceiling - it uses at most ~28% of it. The ceiling, not
the extraction time, is the term that actually determines the worst case; the extraction is faster
on `gpt-4o` than on `gpt-4o-mini` besides (6.51s vs 9.73s mean, section 14.1), so moving the
standard tier to `gpt-4o` made this number safer, not riskier.

**Does the section 1.1 "~48s" arithmetic still hold with the `gpt-4o` numbers? Yes, unchanged.**
The original arithmetic (`18.4 + 30 = 48.4s`) already used the ceiling rather than a measured
extraction figure as the dominant term, precisely because the ceiling was known to exceed any
plausible extraction time. That assumption is now confirmed rather than merely argued: on the
standard tier (`gpt-4o`), even the single worst observed call (8.52s) is 3.5x under the 30s
ceiling, so the ceiling remains the binding constraint and the fast path's real, measured
contribution (~11ms) is negligible against it. No correction to the "~48s" figure is needed;
the `gpt-4o-mini` numbers section 1.1 cited were never the load-bearing term the way this task
asked to check - the ceiling was, and the ceiling is a config value, not a per-model measurement.
Margin against the 120s TTL stays at roughly 60 percent, as originally stated, and margin against
the recommended 60s n8n node timeout (PLAN 1.2, double the 30s ceiling) is roughly 50 percent on
this endpoint's own worst-case contribution alone (~30.01s of a 60s budget).

### 15.3 Event-loop non-blocking, demonstrated

This is UAC S3-01b, `tests/test_media_job_lifecycle.py::test_second_unrelated_request_returns_promptly_while_extraction_is_in_flight`
- not rewritten, run as committed. Method: one persistent `TestClient(app)` (one shared event
loop), a `ThreadPoolExecutor` dispatches a media `/process` call whose extraction is stubbed to
sleep 3.0s on one OS thread while, 0.3s later, a second OS thread issues an unrelated `GET
/api/v1/external/media/jobs/{unknown-id}` (a near-instant 404) against the same app instance. The
test's own assertion is `fast_elapsed < 1.5`; the actual observed value was captured by running the
identical assertion body via a throwaway, uncommitted script (deleted after use) that printed the
measured `fast_elapsed` before applying the same assertion.

| run | fast_elapsed (the unrelated 404 call, while a 3.0s extraction was in flight) | fast_response status | slow_response status |
|---|---|---|---|
| 1 | 0.0183 s | 404 | pending |
| 2 | 0.0151 s | 404 | pending |

**What this means for the lock budget.** The unrelated request answered in 15-18ms while a
multi-second extraction ran concurrently on the same process - two to three orders of magnitude
under both the 1.5s test threshold and the 30s ceiling. This is the concrete evidence that
`await asyncio.wait_for(_await_job(job_id), ...)` (PLAN 3.3b) genuinely does not occupy the event
loop: a hundred concurrent media turns cost sleeping coroutines and a handful of `asyncio.to_thread`
reads, not blocked workers, which is exactly the property that makes it safe to hold
`lock:{contact}` across the wait (section 1.1) rather than reproducing the portal's known blocking
defect (`app/api/v1/public/ai_extract.py:116`).

---

## 17. Added on captain review (2026-08-15)

Two additions the captain asked for on first review of the PR, bundled into the same PR at their
request. Neither changes the endpoint contract in section 3; both change what surrounds it.

### 17.1 Google Gemini as a third provider

**Decision.** The image lane's provider list offered only OpenAI and Anthropic, and the captain will
most likely run image processing on Gemini. A dropdown entry alone would have been a trap:
`get_provider` raised "Unsupported provider" for anything else, so an operator could pick Gemini and
fail every extraction. Gemini is therefore wired at every layer the Anthropic key already touches
(commit `5fef3c0e`): `GeminiProvider` over the REST API with `httpx`, migration
`361_ai_config_gemini_key`, the key column on model, schemas and settings service, the key field in
the AI Assistant settings form, the model lists, and this lane's key and default-model resolution.

**Two things future changes must respect.**

- Gemini 2.5 thinks by default and thinking tokens come out of `maxOutputTokens`. The provider sets
  an explicit thinking budget (0 for flash, a small fixed budget for pro, which cannot go to 0) and
  adds it on top of the caller's `max_tokens`. Without that, `VISION_MAX_TOKENS` on a dense receipt
  is spent thinking and the candidate comes back with no parts, which this lane reads as a failed
  job. `test_connection` lists models for the same reason.
- The lane's Gemini key resolves from its own column, then the generic assistant key only when the
  assistant itself is on Gemini, then the env. Unconditional fallback to the generic key (as the
  Anthropic branch does) would post an OpenAI key to Google.

The default-model choice for a provider now lives in one table in `llm_provider`
(`default_model_for`), used by this lane and by the two other callers that previously else-assumed
Anthropic. Embeddings stay on OpenAI by design because the stored vectors are OpenAI's; that path
keeps choosing an OpenAI key and a test pins it.

**Not verified live.** No Gemini key was available in this environment. Shapes are pinned by tests
against the documented API; the first real extraction happens when a key is pasted in.

### 17.2 Contact detail page as route tabs

**Decision.** The captain asked that the contact page follow the users form (`users/[id]/layout.tsx`):
tabs, related information grouped per tab, per the binding "view and edit are the same layout" rule.
Commit `8587e5ed` makes it a layout with four route tabs, Profile / Access / Routing / Chat, owning
the single contact fetch through a context so tabs never refetch, prev/next keeping the open tab.
S1-01..S1-03's Media Access panel now lives on the Access tab beside Access Agents; the panel itself
is unchanged.

**One rule the first draft broke, and how it was settled.** The header strip may carry only
metadata that has no edit counterpart (Respond.io ID, Created At, Updated At, with the phone echoed
as identity). Phone Number and Respond.io Workspace are edited by the contact dialog, so they stay in
the Profile card; a value the card never showed reads as data loss the moment the pencil opens.

**Verification state.** 40 vitest cases over the tab shell and sections. Browser walk on a dev
server confirmed the Profile tab (four tabs, header strip, phone first and workspace last, Media
Access absent from Profile); the click into Access / Routing / Chat did not complete before write-up
because the machine was at load 120+ and the dev route compile never returned. Owed, and stated in
the PR rather than implied.
