# n8n changes, per slice - Chatbot Turn Engine

Plan: `documentation/plans/chatbot/PLAN-chatbot-turn-engine.md`
UAC: `documentation/plans/chatbot/chatbot-turn-engine-acceptance-criteria.md`

**This file is the n8n half of the cut, written by whoever ships the CRM half, executed by
the owner.** Every slice moves logic out of n8n; the CRM change lands first and is inert,
then the n8n edit below switches the traffic over. Nothing here is automated: the prod n8n
instance is edited by hand, so each section is written at NODE level - which node, what it
becomes, and what proves it worked - rather than as prose.

Two rules that apply to every section:

- **The CRM ships first and OFF.** A slice's CRM code is behind a flag or an unread
  response field until the n8n edit is made, so the two can be deployed in either order and
  a bad slice is reverted by turning the flag off, not by re-editing n8n under pressure.
- **For a LANE, the flag is `system_settings.chatbot_completed_lanes`** (a JSON array of
  `branch_kind`, default `[]`), and the order is fixed by it: **CRM deploy -> shadow ->
  the owner adds the kind to the list -> the n8n Switch output is deleted.** The CRM
  completes a lane only when its kind is in that list AND in
  `contracts.CRM_COMPLETED_BRANCH_KINDS`, so deploying changes nothing on its own.
  **Rollback before the n8n cut is removing the kind from the list** - a settings edit,
  effective on the next turn, no deploy and no n8n edit. That is why the n8n nodes are
  deleted LAST: after that the flag alone cannot bring the old path back.
- **A cutover has a named precondition.** It is written in the section. "It looks right" is
  not one.

---

## S1 - the spine head moves into the CRM

**Written by the n8n owner session (help-crm), 5 Sep, and pasted here verbatim** so the
three slices' n8n halves live in one file in slice order. The CRM half shipped with S0+S1.

Spine head `get-session-vars -> Call 'sub-query-reformulator' -> check-access -> build-ctx -> route-turn` is replaced by `chat-turn` (httpRequest v4.3, POST https://fe-sorento.foundryx.my/api/v1/external/chat/turn, cred crm-n8n-auth, body `{envelope: {...<queue item>, media}}`, timeout 60 s, continueErrorOutput -> sub-error-logger) -> `head-arm` (Switch v3.4: `duplicate` [$json.duplicate true -> no successor], `finished` [$json.delegate ?? 'NONE' == 'NONE' -> send-crm-reply = sendmsg with reply.text], fallback `delegate` -> build-ctx) -> `build-ctx` (one line: re-emits {ctx: response.ctx}) -> `route-turn` (one line: re-emits response.item) -> existing `route` Switch. Old five nodes renamed `(pre-S1)`, disabled, edge-less, deleted at S8 (AC-802). Clone adds `is_test: true` inside envelope (G11) and retires the session-injection / mock-parser / chat-stateful session guards (G6/G8/G9) because those inputs now ride inside the envelope. Built as stage `s1` in the n8n repo's spine-next pipeline (branch feat/s1-chat-turn-head); active node count unchanged, +12 total until S8.

The `chat-turn` timeout is mirrored in the CRM as `send_order.N8N_CHAT_TURN_TIMEOUT_SECONDS` (90 s, issue #1262); change both together.

Precondition: the endpoint answers on fe-sorento (401/422 on an empty POST). Dry run writes exactly one `chatbot.turns` row (is_test true) and nothing else (D14); proof tests listed in the plan.

---

## S2 - `sub-output` moves into the CRM

**CRM side (shipped, inert until n8n calls it).** `POST /api/v1/external/chat/turn/{turn_id}/complete`
runs the whole tail: `build-outcome`, `escalate-catalog`, the CS member offer (roster read
included, in process), `compile-current-state` and `crossdomain-compose`. It writes the
session itself and closes the turn. Nothing calls it until step 2 below, so the CRM half
can deploy days before the n8n half and change nothing.

**After this slice the CRM is the ONLY writer of `respond_contacts.session_vars` on the
turn path (D2).** That is the point of S2 and the thing AC-207's grep proves.

### The request body IS the `sub-output` trigger contract

`sub-output`'s `When Executed by Another Workflow` declares thirteen inputs. The HTTP node
sends twelve of them under the same names, plus `ctx` (optional - the CRM already stored it
on the turn row at `/turn`, and sending it anyway means the expressions below are copied
from the existing caller unchanged):

| field | expression, copied VERBATIM from `Call 'sub-output'` |
| --- | --- |
| `item` | `={{ $json }}` |
| `ctx` | `={{ $('build-ctx').first().json.ctx }}` |
| `result` | `={{ $('build-result').isExecuted ? $('build-result').first().json : null }}` |
| `resolved` | `={{ $('resolve-entity').isExecuted ? $('resolve-entity').first().json : null }}` |
| `gate` | `={{ $('disallowed-entity-gate').isExecuted ? $('disallowed-entity-gate').first().json : null }}` |
| `offer_hold` | `={{ $('offer-hold-reply').isExecuted ? $('offer-hold-reply').first().json : null }}` |
| `suggest_offer` | `={{ $('build-suggest-offer').isExecuted ? $('build-suggest-offer').first().json : null }}` |
| `not_found` | `={{ $('not-found-error-message').isExecuted ? $('not-found-error-message').first().json : null }}` |
| `incoming_picker` | `={{ $('annotate-incoming-picker').isExecuted ? $('annotate-incoming-picker').first().json : null }}` |
| `access_choice` | `={{ $('access-level-choice-message').isExecuted ? $('access-level-choice-message').first().json : null }}` |
| `crossdomain_render` | `={{ $('crossdomain-render').isExecuted ? $('crossdomain-render').first().json : null }}` |
| `answer` | `={{ $("Call 'sub-answer'").isExecuted ? $("Call 'sub-answer'").first().json : null }}` |
| `clarify` | `={{ $('clarify-company-reply').isExecuted ? $('clarify-company-reply').first().json : null }}` |

The body is `extra = "forbid"`: a field the tail does not read is a 422 naming it, not a
silent drop, so a caller whose expectations have drifted says so on the first turn.

**The producers that live in `sub-main-processing` ride on `item.outcome_fragment`.**
`build-outcome`'s RS-6.1c mechanism already exists for exactly this and needs no new field:
a key PRESENT in `item.outcome_fragment` is taken verbatim and the producer is never asked
for. The keys that must be there are the ones `sub-output`'s graph does not contain:
`central-exchange`, `validator`, `promo-picker`, `crossdomain-zeroset`,
`build-miss-member-offer`, `dym-annotate-partial`, `dym-annotate`, `build-ideate-reply`.
If `sub-main-processing` already stamps them (it does on the lanes that moved), nothing
changes; if a lane does not, its key is absent and the CRM computes null for it, which is
the same value `_one` returns today.

### The response

```
200 {
  turn_id,
  reply: { text, quick_replies, result_set, attachments_src },
  actions: [],           // S2 still delegates every lane; the caller only sends
  session_patch          // ONLY on a dry run (is_test / test_run_id), else null
}
```

`reply` carries four FIELDS rather than the sealed patch, because that is what turns each
downstream expression into one read (see step 3). `quick_replies` is n8n's own
`compile-current-state.js` value verbatim - a non-empty comma-joined STRING, or `null` when
the turn offers none - and NEVER a list, on every `send_message` action in every slice
below; `result_set` is `variables.last_result_set`, whose own empty case is `[]`, never
`null` (AC-507, measured against 61 live `sub-output` tail captures: 60 non-empty strings,
1 null, 0 empty strings, 0 lists).

### Step 1 - the HTTP node, inside `sub-output` (`qa4LWvPrhUnAPgjC`)

The cut is made INSIDE the sub rather than at its caller, so `sub-main-processing` and the
spine are untouched and the rollback is one workflow.

1. Add an `httpRequest` node `crm-complete-turn` after `When Executed by Another Workflow`:
   - method `POST`, credential `crm-n8n-auth` (the same header auth every other CRM call
     uses), URL
     `https://fe-sorento.foundryx.my/api/v1/external/chat/turn/complete` - the ID-LESS
     form, added 5 Sep so this cut touches ONE workflow. `sub-output` holds the `ctx` and
     not the turn id (the id lives on the spine, two workflows up), so the alternative was
     editing the spine, `sub-main-processing` and every `Call 'sub-output'*` caller to
     thread it through. The CRM resolves the turn from the body's own
     `(ctx.contact.id, ctx.text.message.messageId)` - the same pair `chatbot.turns` is
     UNIQUE on (D15) - taking the HIGHEST attempt, and refuses anything but a `delegated`
     turn (404 with a sentence when no row matches, 409 `CHATBOT_TURN_NOT_DELEGATED`
     otherwise). `/turn/{turn_id}/complete` is unchanged and still works, so step 2 below
     is reversible either way,
   - body `specifyBody: json`, `jsonBody` = the thirteen fields above,
   - `retryOnFail: false`. **Deliberate:** the tail WRITES the session, and a retry after a
     partial failure would re-run a turn the CRM may already have completed. The CRM's own
     idempotency covers a genuine duplicate (a second `/complete` replays the first answer
     and writes nothing), but an n8n-level retry is the wrong place to decide that.
2. Nothing else has to reach the sub. The trigger's existing thirteen inputs are the
   whole body, and `ctx` - which every caller already sends - is what identifies the turn.
   (An earlier draft of this section threaded a fourteenth `turn_id` input down from the
   spine; the id-less endpoint above is what replaced it, and it is why this cut no longer
   touches the spine at all.)

### Step 2 - re-emit, so nothing downstream moves

3. Replace `crossdomain-compose`'s body with a one-line re-emitter, keeping the NAME:

   ```js
   // RS-5: the tail moved into the CRM. This node exists only so the two by-name readers
   // below resolve, exactly as the S1 cut kept `build-ctx` and `route-turn` as names.
   return [{ json: { reply: $('crm-complete-turn').first().json.reply } }];
   ```

4. DELETE, in this order (each is now dead): `item-restore`, `entry-gate`,
   `escalate-catalog`, `cs-offer-gate`, `cs-roster-plan`, `get-cs-members`,
   `build-cs-member-offer`, `build-outcome`, `compile-current-state`, `build-ctx`, and
   ten of the eleven `g-*` gates with their carrier stubs: `g-result`/`build-result`,
   `g-resolved`/`resolve-entity`, `g-gate`/`disallowed-entity-gate`,
   `g-offer_hold`/`offer-hold-reply`, `g-suggest_offer`/`build-suggest-offer`,
   `g-not_found`/`not-found-error-message`,
   `g-incoming_picker`/`annotate-incoming-picker`,
   `g-access_choice`/`access-level-choice-message`, `g-answer`/`Call 'sub-answer'`,
   `g-clarify`/`clarify-company-reply`.

   **`g-crossdomain_render` and `crossdomain-render` are the ONE pair that stays**, and
   the gate stays WITH the carrier rather than being simplified away: `send-attachments`'
   `xd` input reads `$('crossdomain-render').isExecuted ? ... : null`, so the gate is what
   keeps `isExecuted` false on the turns where the real node did not run. Delete the gate
   and the carrier runs on every turn; delete the carrier and the expression throws on the
   NAME rather than returning null. After the deletions the wiring is
   `trigger -> crm-complete-turn -> g-crossdomain_render -> (true) crossdomain-render ->
   crossdomain-compose`, with the gate's FALSE arm going straight to `crossdomain-compose`.

5. DELETE `save-session-vars`. This is the line AC-207 is about: after it goes, no
   workflow on the turn path writes `respond_contacts.session_vars`.

### Step 3 - the two senders read one field each

6. `sorento-sub-respond-sendmsg-respond2`'s inputs change from three expressions that dig
   into the sealed patch to three that read the response:

   | input | from | to |
   | --- | --- | --- |
   | `message` | `$('crossdomain-compose').first().json.reply.text ?? null` | `={{ $('crm-complete-turn').first().json.reply.text ?? null }}` |
   | `quick_reply` | `$('crossdomain-compose').first().json.reply.quick_replies` | `={{ $('crm-complete-turn').first().json.reply.quick_replies }}` |
   | `result_set` | `$('crossdomain-compose').first().json.reply.session_patch.variables.last_result_set` | `={{ $('crm-complete-turn').first().json.reply.result_set }}` |

   The third is the one that matters: `session_patch` is no longer on the wire, so a
   reader that digs into it would get `undefined` and send an empty list silently.

7. `send-attachments`' inputs:

   | input | from | to |
   | --- | --- | --- |
   | `reply` | `$('crossdomain-compose').first().json.reply` | `={{ $('crm-complete-turn').first().json.reply }}` |
   | `attachments_src` | `$("Call 'sub-answer'").isExecuted ? ($("Call 'sub-answer'").first().json.outcome_fragment \|\| {})['central-exchange'] : null` | `={{ $('crm-complete-turn').first().json.reply.attachments_src }}` |
   | | (this is why `Call 'sub-answer'` can be deleted in step 2: the CRM computes the same value from the `answer` field it was sent) | |
   | `xd` | `$('crossdomain-render').isExecuted ? $('crossdomain-render').first().json : null` | unchanged |
   | `ctx` | `$('build-ctx').first().json.ctx` | `={{ $('When Executed by Another Workflow').first().json.ctx }}` |

   `xd` keeps its `crossdomain-render` read, which is why that carrier survives step 2.

### AC-207's proof, and it is a grep, not a judgement

From the n8n repo, after re-exporting:

```bash
python scripts/export-workflows.py --verify
grep -rn "conversation-variables" n8n-workflows-init/export/*/workflow.json
```

**Exit condition: every hit is a GET.** A `"method": "PUT"` within the same node object as
a `conversation-variables` URL, in ANY exported workflow, means a session writer survived
and S2 is not done. The GETs are `get-session-vars` on the spine (which the CRM's S1 cut
already replaced on the live spine, and which other workflows still use legitimately).

Second grep, for the deleted bodies:

```bash
grep -rln "compile-current-state\|escalate-catalog\|build-cs-member-offer" n8n-workflows-init/export/
```

Only `clone-*` and `*-live` archived exports may match. A hit in `sub-output-live` means
step 2 was not completed.

### Precondition for the promote

- `pytest tests/chatbot -q` green in the CRM, including the 166-world replay.
- The clone smoke set (15 turns) run against the S2 build: same lane and same reply text
  as the pre-S2 baseline, zero egress (AC-208 asserts the persisted `session_vars` too).
- Gate 0 not blocked (`tests/chatbot/COVERAGE.md`; the blocking set is pinned EMPTY in
  `tests/chatbot/test_coverage_fresh.py::EXPECTED_BLOCKING`). Cleared on 5 Sep by the
  `sub-output-live` capture batch: 305 real captures off the shipping body, from a pool
  scanned end to end (760 of 760 on version `c32698c1`), which is what earns `exhausted`
  for the arms that still read zero.
- The captures themselves merged from the n8n capture worktree into the sibling checkout,
  or `test_coverage_fresh.py` skips its comparison with a message saying so.

### Rollback

Steps 2 and 3 are destructive, so the rollback artefact is **the `sub-output` workflow JSON
exported immediately before step 2**. Re-import it and the sub answers from its own nodes
again; the CRM's `/complete` endpoint stays deployed and unreferenced, which is harmless -
it writes nothing unless it is called.

**The one thing a rollback does NOT undo:** turns already completed by the CRM wrote their
session through `/complete`. Those sessions are the same shape n8n wrote (the patch is
persisted verbatim, `variables` plus `user_response` plus `quick_reply`), with one addition
- the R3 `pending` marker. The S1 head reads BOTH forms (AC-106), so a rolled-back n8n
reads those sessions correctly and simply ignores the extra key.

### Not covered by this slice

- `sub-sendmsg` and `sub-send-attachments` themselves stay in n8n (D9: n8n sends).
- The `actions[]` vocabulary is returned but empty at S2 except the `update_contact_fields`
  clear the head raises (AC-108). The assign / comment actions arrive with S5.
- `chatbot_reply_access_denied` and `chatbot_reply_offer_hold`, two of the seven keys
  AC-302 names, are NOT registered here. `access_denied` has no `escalate-catalog` case at
  all (it falls through the switch to an empty response) and `offer_hold`'s text is
  computed upstream by `offer-hold-reply` rather than canned. Inventing copy for either
  would be inventing behaviour; they land with their lanes at S3 and S5.

---

## S2b - Retry re-enters through n8n's ingress (no n8n logic moves)

**This slice moves nothing out of n8n.** It is here because it is the one place the CRM
calls n8n rather than the other way round, and because two of its parts are owner items
that have to be done by hand on the n8n side.

### Retry posts the ORIGINAL respond.io body at the inject webhook

`POST /api/v1/system/chatbot/turns/{id}/retry` does NOT re-run the turn and does NOT send
anything to the customer. It takes the respond.io webhook body the CRM stored on the turn
row (`envelope.message`, verbatim, not a rebuilt envelope) and POSTs it to n8n's inject
webhook - the same front door the failover poller uses - so the retried message re-enters
with the same ordering, the same lanes and the same sending path a live message gets. The
turn then arrives back the ordinary way, as its own row with the next `attempt`.

| what | value |
| --- | --- |
| target | `chatbot_retry_ingress_url` on the DEFAULT respond workspace row (the inject webhook; from S7 the thin spine's own webhook) |
| method / body | `POST`, `application/json`, the stored respond.io webhook body unchanged |
| header | `X-Chatbot-Retry-Key: <the workspace row's retry key>`, sent only when the key is set |
| timeout | 10 s - the call being answered means "n8n accepted the message", not "the turn finished" |
| unset URL | 409 `retry_unavailable`, nothing posted, `retry_requested_at` left NULL |

**Owner items, both by hand:**

1. **n8n checks `X-Chatbot-Retry-Key` on the inject webhook before S7.** The CRM sends the
   header today; nothing on the n8n side reads it yet, so the webhook is currently as open
   as it was before. The check belongs on the webhook node itself (compare against the same
   secret, reject otherwise), and it has to be in place before S7 moves the ingress, because
   after that the URL is the spine's own and the retry path is the only caller that is not a
   respond.io delivery.
2. **Both values are entered on the Respond Workspaces screen, in production only.**
   System > Respond Workspaces > edit the default workspace > Chatbot retry: the webhook
   URL and the retry key (write-only; the screen only ever says whether one is stored).
   They live on `respond_workspaces` as of AC-804 and are NOT environment variables any
   more, so nothing about a deploy sets them. Leave them empty on a dev machine on
   purpose: one that silently injected into production n8n would answer a real customer
   from a developer's click, and the failure would read as a bug in the customer's
   conversation rather than in a config screen. Deploying the code alone therefore leaves
   Retry disabled, which the list response says out loud (`retry_available: false` plus
   `retry_unavailable_reason`) so the button is greyed rather than offering a 409. The URL
   is refused on save and again on use if it is not https or resolves anywhere internal
   (`app/services/outbound_url_guard.py`), and blanking it turns Retry off.

Precondition for turning it on: a retry of a known-failed test turn arrives back as a NEW
turn row with `attempt` 2 for the same `message_id`, and the original row is still `failed`
with `retry_requested_at` set.

### The delegated sweep is what explains a ghost

A turn the CRM handed to an n8n lane is `delegated` until that lane calls `/complete`. When
the lane dies mid-turn - workflow error, worker redeploy, execution deleted - the call never
comes. Without a sweep the row sits `delegated` forever: the trace list fills with ghosts
that read as work still in progress, and Retry cannot touch any of them, because R4 makes a
manual retry possible on a FAILED turn only.

So an APScheduler tick (every minute, `app/scheduler/task_scheduler.py`) fails every
`delegated` row older than `CHATBOT_DELEGATED_TTL_MINUTES` (default 10, measured from
`started_at` and falling back to `created_at`), with `stage = "delegated"`, `error = "n8n
lane did not complete within N minutes"` and a trace note that says the lane never reported
back. It is idempotent, it settles at most 200 rows a tick, and it includes test turns - a
clone turn that hangs is exactly as misleading on the trace screen as a live one.

**What this means for the n8n side:** a batch of swept turns is a signal, not noise. Several
at once with the same lane means that workflow died or was redeployed mid-turn; the TTL is
the window in which an n8n execution is still expected to finish, so raise it rather than
the sweep if a lane legitimately takes longer than ten minutes.

---
## S4 - the `low_signal` lane moves into the CRM

**CRM side (shipped, and inert until switched on).** S4 puts `low_signal` in
`contracts.CRM_COMPLETED_BRANCH_KINDS`, which says the CODE can complete it. It does not
say the CRM may. A turn is completed in the CRM only when its `branch_kind` is ALSO listed
in `system_settings.chatbot_completed_lanes`, which defaults to `[]` - so on the day the
CRM deploys nothing changes: every low-signal turn still gets `delegate: "low_signal"` and
n8n answers it exactly as it does today, and the clarifier does not run in the CRM at all.

**The order is: deploy, then flip the data, then cut n8n.** Not the other way round, and
the three steps are independent - each one is separately reversible.

There is no double run at any point. Once the lane is switched on, the CRM answers and
returns `delegate: null`, and the spine's `head-arm` Switch (S1) already routes a null
delegate to `send-crm-reply` and never reaches `route`. So `Call 'sub-casual-llm'` stops
being entered the moment the flag flips, before a single node is deleted; step 3 removes
nodes that are already cold.

### Which turns this covers

One `route` output. `route[11]` is the `low_signal` arm and it is the only path to
`Call 'sub-casual-llm'`.

| `route` output | node it feeds today | CRM `branch_kind` |
| --- | --- | --- |
| 11 `low_signal` | `Call 'sub-casual-llm'` | `low_signal` |

### Step 1 - deploy (nothing changes)

Deploy the CRM with `chatbot_completed_lanes = []`. Low-signal turns keep delegating and
n8n keeps answering them. The only new thing is a `chatbot.turns` row per turn, which is
what the next step is judged on.

**Precondition to proceed:** none. This step cannot change a customer's answer.

### Step 2 - flip the lane on (the real cutover)

Add `"low_signal"` to `system_settings.chatbot_completed_lanes` (Settings, or one UPDATE).
From the next turn the CRM answers, `head-arm` sends its reply, and `Call 'sub-casual-llm'`
is never entered.

**Precondition to proceed to step 3:** over at least 20 low-signal turns since the flip,

```sql
SELECT status, stage, count(*)
FROM chatbot.turns
WHERE branch_kind = 'low_signal' AND created_at > now() - interval '7 days'
GROUP BY 1, 2;
```

shows `done` / `remembered` rows and **zero** `failed` / `casual_llm`. A `failed` /
`casual_llm` row is the lane saying it could not reach the clarifier, with the reason in
`error`. Do not proceed with any.

**Rollback for this step is the flag**: remove the string, and the very next turn delegates
again and n8n answers it. No deploy, no n8n edit. That is the whole reason the flag exists,
and it is why the n8n nodes are not deleted until step 3.

### Step 3 - delete the cold nodes, in `sorento-consume-main` (`S4N1LiisAqA4hpMC`)

Three nodes go, one arrives.

| node | today | after |
| --- | --- | --- |
| `Call 'sub-casual-llm'` | executeWorkflow -> `sub-casual-llm` (`4dPJ8ykop8VIpddY`) | **DELETE** |
| `casual-gate` | IF on `$json._casual_error === true`; true arm terminal, false arm -> `Call 'sub-answer'1` | **DELETE** |
| `Call 'sub-answer'1` | executeWorkflow -> `sub-answer` (`oIzFAzi3bGgn5mTH`) | **REPLACE with a one-line Code node of the SAME NAME** |

By this point none of the three has run since step 2, so this is tidying, not a cutover.

The replacement keeps the name on purpose. `Call 'sub-output'6` maps its `answer` input as
`{{ $("Call 'sub-answer'1").isExecuted ? $("Call 'sub-answer'1").first().json : null }}`,
and that expression is shared with the arms this slice does not touch. Keeping the name
means **no edit to `sub-output`'s mapping at all** - the same trick AC-110 uses for
`build-ctx` and `route-turn`.

New `Call 'sub-answer'1` body (Code node, run once for all items):

```js
// S4: the CRM already answered. `sub-answer` used to parse the clarifier's JSON here
// (`central-exchange`); the CRM does that now and hands back the finished text.
return [{ json: { response: $('route-turn').first().json.reply.text } }];
```

`route-turn` is S1's re-emitter of the `/chat/turn` response. If the reply is exposed under
a different name when S1 is wired, use that name - the point is `response.reply.text` from
the one HTTP call, not this specific node.

Rewire: `route[11]` -> the new `Call 'sub-answer'1` -> `Call 'sub-output'6`. Both edges
already exist for the second hop; only the first changes, from
`route[11] -> Call 'sub-casual-llm'`.

**What happens to the error arm.** `casual-gate` exists to catch `_casual_error`, which
`mark-casual-error` sets when `sub-error-logger2` has ALREADY sent the error text from
inside the sub. That whole path goes: the CRM's failed lane returns the same
`sub-error-logger` text as `reply.text` plus a `send_message` action, so the error is sent
by the same outbound as any other reply and there is nothing to gate on. This is the
behaviour change to watch in step 1, and it is why the precondition counts failed rows.

### Step 4 - unpublish

`sub-casual-llm` (`4dPJ8ykop8VIpddY`) has no other caller once step 2 lands. Deactivate it;
do not delete it, so the rollback below is a re-activation rather than a rebuild.

`sub-answer` (`oIzFAzi3bGgn5mTH`) **STAYS ACTIVE.** `Call 'sub-answer'1` was one of several
callers; the business lane still uses the others, and its `central-exchange` node is still
what parses those answers. Only this caller goes.

### Rollback

**Before step 3: remove `"low_signal"` from `chatbot_completed_lanes`.** One data change,
effective on the next turn, no deploy and no n8n edit.

**After step 3** the flag alone is not enough - the CRM would stop answering and the nodes
that used to are gone, so the turn would reach `route[11]` with nothing behind it. Restore
the three nodes and re-activate `sub-casual-llm` FIRST, then clear the flag. This is the
whole reason step 3 comes last and only after the precondition: it is the one step that
takes the old path away.

### Not covered by this slice

- **Nothing about the session write.** S2's tail landed first, so a CRM-completed
  low-signal turn runs the same `complete_turn` pipeline every other lane will: outcome ->
  compile-state -> compose -> session write, closing `done` at `remembered`. While the lane
  is switched off, n8n's `Call 'sub-output'6` writes the session exactly as today.
- **The other `casual`-ish arms.** `clarify_menu`, `out_of_scope` and `not_supported` are
  canned-reply arms and belong to S3; they still go to `Call 'sub-output'6` untouched.

---

## S3 - the canned lanes, offer-hold and ideation move into the CRM

**CRM side (shipped, inert by default).** `POST /api/v1/external/chat/turn` can now FINISH
eight branch kinds itself - `access_denied`, `escalate_offer`, `escalation_declined`,
`clarify_menu`, `not_supported`, `demand_qty`, `offer_hold`, `ideate` - returning
`delegate: null` with `reply` and one `send_message` action. Ideation is called as the MCP
tool `crm_ideation_turn` through the same in-process client every business tool uses (D6,
D10), so it is not a lane in n8n and not a special case in the CRM.

**It completes NOTHING until the owner says so.** `system_settings.chatbot_completed_lanes`
is a JSON list, shipped EMPTY, and a lane runs in the CRM only when it is in BOTH that list
and the code's own `lanes.canned.COMPLETED_BRANCH_KINDS`. That is what makes this cut a
data change rather than a deploy.

### The order, and it is not negotiable

1. **Deploy the CRM.** `chatbot_completed_lanes` is `[]`, so every one of the eight still
   delegates and n8n answers exactly as it does today. Nothing a customer sees moves.
2. **Shadow.** Leave it deployed. The CRM composes nothing for these lanes yet, so the
   window here is about the REST of the turn being unchanged: zero `chatbot.turns` rows
   with `status = 'failed'`, and the eight kinds still arriving at n8n with the same
   `branch_kind` they did before.
3. **The owner adds the kind(s) to `chatbot_completed_lanes`** in Settings, one lane or
   several, and watches. From that moment the CRM answers them and the `head-arm` Switch's
   `finished` route (S1) sends the CRM's reply; n8n's own nodes for those kinds stop being
   reached, but they are still THERE.
4. **Only then delete the Switch outputs and the nodes**, below. Deleting before step 3 is
   what turns a reversible data change into an outage.

**Rollback is editing the list**, at every point before step 4: remove the kind and the
next turn delegates again. After step 4 the rollback is re-importing the workflow JSON
exported before it, which is why step 4 waits for a week of step 3.

### Step 4 - the wiring change, in the spine (`sorento-consume-main`)

5. DELETE the `route` Switch outputs for the eight kinds. What remains is
   `check_promotion`, `stock_denied`, `business_query`, `out_of_scope` and `low_signal` -
   the five S4 to S6 still own.
6. DELETE the nodes those outputs fed, now unreachable. **Five, not eleven** - read off
   the live export (`n8n-workflows-init/export/spine-rs-1a/TOPOLOGY.md`, 50 nodes) rather
   than off the earlier draft of this list, which named seven `tag-*` / `If-*` nodes the
   spine does not have:
   - `sorento-sub-respond-sendmsg-respond5` - `route[0]` (`access_denied`) goes STRAIGHT
     to it, with no tag node in between. Its `message` expression IS the access-denied
     reply, and the CRM composes that string now.
   - `ideate-turn-http` and `build-ideate-reply` - `route[3]`, in that order.
   - `offer-hold-reply` and `tag-offer-hold` - `route[4]`, in that order. The Set is what
     strips the item to `{branch_kind}` before `Call 'sub-output'6`.

   The other five kinds (`escalate_offer`, `escalation_declined`, `clarify_menu`,
   `not_supported`, `demand_qty`) have NO node of their own: their outputs go straight to
   `Call 'sub-output'6`, which stays for the lanes that still delegate. `route-turn`'s own
   `TAG_ONLY` set is where their `{branch_kind}`-only item comes from, and that code moves
   with the lane, not with a node.
7. `Edit Fields2` STAYS. It carries `not_allowed_check_stock: true` onto the
   `stock_denied` item, and that lane still delegates - but the CRM now stamps the same
   field itself (`_stamp_item`), so the node is a no-op that can go with the rest of the
   business lane at S6.

### AC-305's proof

```bash
python scripts/export-workflows.py --verify
grep -rn "ideate-turn-http\|build-ideate-reply\|offer-hold-reply" n8n-workflows-init/export/*/workflow.json
```

Only `clone-*` and archived `*-live` exports may match. A hit in the live spine means step
4 was not completed. And in the CRM, the switch itself is the second proof:
`SELECT chatbot_completed_lanes FROM system_settings` names exactly the kinds n8n no
longer routes.

### Not covered by this slice

- `low_signal` (S4), `out_of_scope` and the assignment actions (S5), `check_promotion` /
  `stock_denied` / `business_query` (S6). Their Switch outputs stay.
- The canned copy is now editable in Settings > AI Prompts (`chatbot_reply_*`), which
  means an owner CAN change what these lanes say without a deploy - and can therefore
  change it to something n8n's own nodes would not have said. That is the point of D5, and
  it is worth knowing before step 4 removes the comparison.
## S5 - the escalation lane moves into the CRM

**CRM side (shipped, inert until switched on).** Same shape as S4: `out_of_scope` joins
`contracts.CRM_COMPLETED_BRANCH_KINDS`, and the CRM completes the turn only when the owner
also lists `"out_of_scope"` in `system_settings.chatbot_completed_lanes`. Default `[]`,
so deploying changes nothing.

**This slice is the first one that returns an action the spine does not already perform.**
Every earlier slice handed back `send_message`, which `head-arm` already routes to
`send-crm-reply`. S5 returns **`assign_conversation`** and **`add_comment`** as well, and
nothing in n8n executes those today. **The action executor is a prerequisite, not a
follow-up:** until it exists, switching this lane on means the customer is told a person is
coming and no person is ever assigned. help-crm builds it as its own n8n slice.

The four actions, in the order the caller must execute them:

| # | action | what n8n does today | notes |
| --- | --- | --- | --- |
| 1 | `send_message` | `sorento-sub-respond-sendmsg-respond-routed-to-pic2` | the out-of-scope acknowledgement, BEFORE the assignment work |
| 2 | `assign_conversation` | `Assign or unassign a Conversation1` | omitted when the assignee service says `is_already_assigned` |
| 3 | `add_comment` | `Call 'sub-add-comment-respond'` | the SLA note, mentioning the assignee |
| 4 | `send_message` | `sorento-sub-respond-sendmsg-respond-routed-to-pic` | "routed to the PIC from <team> team" |

`next-assignee` and the SLA row are NOT actions: they are in-process service calls the lane
makes itself, exactly as `sub-human-intervention` makes them HTTP calls back into this same
CRM. The out-of-scope acknowledgement TEXT that `escalate-catalog` composes
(`includeResponse: false`, "Informed the user that request is out of scope...") is a tail
concern and stays there; it is not one of these actions. This order (`send_message`,
`assign_conversation`, `add_comment`, `send_message`) is the live lane order and does not
change; both `send_message` actions carry `quick_replies` as a string-or-null, never a list
(the type pin above), since this lane seals none.

### Which turns this covers

One `route` output: the `out_of_scope` arm, which is the only path to `escalation`.

### Step 1 - deploy (nothing changes)

Deploy with `chatbot_completed_lanes` unchanged. Turns keep delegating.

**Precondition to proceed: the action executor exists and is wired**, and a test turn with
`assign_conversation` + `add_comment` in `actions` results in a real assignment and a real
comment on a throwaway contact. Everything else in this section assumes that.

### Step 2 - flip the lane on

Add `"out_of_scope"` to `system_settings.chatbot_completed_lanes`. From the next turn the
CRM assigns, starts the SLA clock and hands the caller four actions; `escalation` is never
entered.

**Precondition to proceed to step 3:** over at least 20 out-of-scope turns,

```sql
SELECT status, stage, count(*)
FROM chatbot.turns
WHERE branch_kind = 'out_of_scope' AND created_at > now() - interval '7 days'
GROUP BY 1, 2;
```

shows `done` / `remembered` and **zero** `failed` / `looked_up`, and every one of those
turns has an SLA tracking row. The arm runs the TAIL, so a completed out-of-scope turn
closes at `remembered` like every other CRM-completed lane; a row still sitting at
`replied` means the tail did not finish. A `failed` / `looked_up` row is the lane saying
the assignment did not complete; its `error` says which seam.

**Rollback for this step is the flag.** Remove the string and the next turn delegates again.

### Step 3 - delete the cold nodes

| node | today | after |
| --- | --- | --- |
| `escalation` | executeWorkflow -> `sub-escalation` | **DELETE** |
| `escalation-arm` | the If on `arm` | **DELETE** |
| `clarify-company-reply` | the name-preserving re-emitter | **DELETE** |
| `tag-out-of-scope` | the Set that stamps the arm | **DELETE** |

Rewire the `route` out-of-scope output straight to `Call 'sub-output'6`, the same shape the
other CRM-completed arms use.

**`clarify-company-reply` needs one check before it goes.** `build-outcome` reads
`$('clarify-company-reply').isExecuted` through a static map, and that read is how the
clarify ask reaches the customer. On the CRM side the clarify arm returns NO actions and a
`pending: {kind: company_clarify}` marker; the ask itself travels as the tail's
`clarify_text`. Confirm on a live clarify turn that the customer still gets the ask before
deleting the node - and note that the arm did not fire once in the 33-execution capture
window, so a deliberate test turn is the only way to see it.

### Step 4 - unpublish

`sub-escalation` (`fr2u3e6FKg52cPvK`) and `sub-human-intervention` have no other caller once
step 3 lands. Deactivate both; do not delete them, so rollback is a re-activation.

### Rollback

Before step 3: remove `"out_of_scope"` from the list. After step 3: restore the four nodes
and re-activate both subs FIRST, then clear the flag.

### Not covered by this slice

- **`fresh-entity-gate` (H26) and the team clarify (H27).** Neither is on the live graph;
  both belong to unpromoted builds. The lane never calls the resolver, so escalation routing
  stays brand-blind exactly as today, and a null team is not reachable anyway because the
  parser hard-defaults it to `customer_service`. Both are `xfail(strict=True)` in the test
  suite, so the promotion announces itself.

---

## S6a - `sub-resolve-and-gate` moves into the CRM

**CRM side (shipped, inert by default).** `POST /api/v1/external/chat/turn` now returns
`delegate_payload`: exactly the item `sub-resolve-and-gate` returns today - the four
`resolve-exit-*` arms' `_exit_kind` plus `resolved`, `gate`, `ctx_resolved`, `aggregate`,
`tier_gate`, `annotate_incoming` and that arm's own item. It is `null` unless
the business lane switched on, and null on every branch kind except the three that
reach the sub.

### Which turns this covers

`sorento-consume-main`'s `route` Switch has three outputs that reach the sub, and the CRM
lane covers exactly those three:

| `route` output | node it feeds | `entry` stamped | CRM `branch_kind` |
| --- | --- | --- | --- |
| 8 `check_promotion` | `tag-entry-access-check` | `access_check` | `check_promotion` |
| 11 `stock_denied` | the SPINE's `Edit Fields2` -> `tag-entry-resolve` | `resolve` | `stock_denied` |
| fallback `business_query` | `tag-entry-resolve` | `resolve` | `business_query` |

**There are TWO nodes called `Edit Fields2`**, one in the spine (row 11 above, which sets
`not_allowed_check_stock` before the tag) and one inside `sub-main-processing` (fed by
`ef2-gate`, which re-sets it from the trigger). Everything below is about
**`sub-main-processing`'s**. It stamps `not_allowed_check_stock: true` and it **STAYS**. `validator` reads it by
name and by node, not off the flowing item:

```js
if ($('Edit Fields2').isExecuted && $('Edit Fields2').first().json.not_allowed_check_stock) {
```

`$('Edit Fields2')` on a node that does not exist THROWS, so deleting it takes the whole
`stock_denied` answer path down. The CRM therefore does NOT stamp the field: nothing
downstream of the sub reads the item for it, and a CRM-side copy would be a second writer
of a value n8n still owns.

### Step 1 - shadow window (no wiring change)

1. Turn the business lane on under System > Settings > Chatbot
   (`system_settings.chatbot_business_lane_enabled`). Nothing in n8n changes: it still
   calls `sub-resolve-and-gate` itself and still answers from it.
2. For a week of live traffic, compare each turn's `delegate_payload` with what
   `Call 'sub-resolve-and-gate'` returned on the same turn. The CRM writes the payload to
   `chatbot.turns.trace` under the `looked_up` record, so this is a query, not an
   instrumentation project.

**Precondition for step 2, and it is not negotiable:** zero `looked_up` records with
`status = failed`, and zero payload mismatches outside the two keys
`tests/chatbot/_corpus.py::CAPTURE_BODY_ADDITIONS` names. The shadow costs one extra
resolver call per business turn - that is the price of the window and the reason it is a
window and not the permanent state.

### Step 2 - the wiring change, in `sub-main-processing` (`53RxDSON8P3QSN22`)

The sub keeps its shape; only its FRONT changes. Today:

```
When Executed by Another Workflow -> build-ctx -> ef2-gate -> Edit Fields2 / item-restore
item-restore -> Call 'sub-resolve-and-gate' -> resolve-gate -> ... -> resolve-item -> resolve-arm
```

After:

1. **Add** a trigger input `resolve_payload` (type object) to
   `When Executed by Another Workflow`, beside the existing `ctx`, `item`,
   `not_allowed_check_stock`, `is_test`.
2. **Replace the body of `resolve-item`** (Code node, already the chain's item carrier)
   with a read of the trigger instead of the call:

   ```js
   return [{ json: $('When Executed by Another Workflow').first().json.resolve_payload }];
   ```

3. **Repoint the four presence gates and the five name-preserving stand-ins** from
   `$("Call 'sub-resolve-and-gate'").first().json.<key>` to
   `$('When Executed by Another Workflow').first().json.resolve_payload.<key>`. They are:

   | node | key it reads |
   | --- | --- |
   | `resolve-gate` (If) | `resolved` |
   | `aggregate-gate` (If) | `aggregate` |
   | `annotate-incoming-gate` (If) | `annotate_incoming` |
   | `resolve-entity` (Code stand-in) | `resolved` |
   | `disallowed-entity-gate` (Code stand-in) | `gate` |
   | `build-ctx-resolved` (Code stand-in) | `ctx_resolved` |
   | `Aggregate` (Code stand-in) | `aggregate` |
   | `tier-gate` (Code stand-in) | `tier_gate` |
   | `annotate-incoming-picker` (Code stand-in) | `annotate_incoming` |

   Rewiring alone does NOT redirect these - they are by-name reads (`TOPOLOGY.md`, "Read BY
   NAME"). The expression has to be edited too. This is the one step where a missed node
   fails silently: a stand-in that never executes makes every downstream
   `$('<name>').first()` throw, and the turn dies with a node-not-executed error rather
   than a wrong answer.

4. **Delete ONLY** `Call 'sub-resolve-and-gate'` (executeWorkflow) and `item-restore`
   (Code). Wire `Edit Fields2[0] -> resolve-gate` and `ef2-gate[1] -> resolve-gate`, so the
   stand-in chain still runs and still dominates its readers (LESSONS 91: a sibling has no
   ordering relation, so the chain cannot become a branch).

   **`ef2-gate` and `Edit Fields2` STAY.** `validator` reads `$('Edit Fields2').isExecuted`
   and `$('Edit Fields2').first().json.not_allowed_check_stock`, and `$('<name>')` on a
   node that does not exist throws - deleting the Set node takes the `stock_denied` answer
   path down with it, several nodes away from the edit, which is the worst shape a mistake
   here can have. `ef2-gate` is what decides whether it runs, so it stays for the same
   reason. Both are deleted at S6c with the rest of the lane (AC-610), when `validator`
   goes too.

### Step 3 - the caller, in `sorento-consume-main` (`S4N1LiisAqA4hpMC`)

5. On both `Call 'sub-main-processing'` call sites, add the input
   `resolve_payload: {{ $json.delegate_payload }}` (the CRM `/chat/turn` response the two
   re-emitters already read `ctx` and `item` from).
6. `tag-entry-resolve` / `tag-entry-access-check` stay for now: `entry` is inert once the
   CRM decides it, and leaving them costs nothing and keeps step 2 revertible. They are
   deleted at S6c with the rest of the lane (AC-610).

### Step 4 - unpublish

7. `sub-resolve-and-gate` (`tKeQUkZK5cFK9BFa`) is unpublished ONLY after a week with no
   rollback. Until then it stays published and unreferenced, which is what makes the
   rollback a one-field edit (`resolve_payload` back to the call's output).

### Rollback

Turn the business lane off on the settings screen. The CRM returns `delegate_payload: null`, so
`resolve-gate` / `aggregate-gate` / `annotate-incoming-gate` all take their FALSE arms and
`resolve-arm` receives an item with no `_exit_kind`. **That is a dead turn, not a fallback**
- so if step 2 has already landed, the rollback is to re-add the
`Call 'sub-resolve-and-gate'` node and repoint the nine expressions back. Keep a copy of
the workflow JSON from before step 2; that is the actual rollback artefact.

### Not covered by this slice

- `probe-incoming` and `probe-customer-orders` still run in n8n's `sub-get-results` when
  the CRM lane is off. With it ON, the CRM's own probe seam raises (it needs S6b's
  `entity-ids-transformer` and `output-structurer`), which both annotators render as their
  documented UNPROBED arm: the customer picker ships bare with
  `customer_probe_skip_reason: 'probe_unavailable'`, and the incoming picker ships today's
  "None of these have incoming stock right now." **This is a real behaviour difference on
  picker turns and it is why step 1's shadow window must include picker traffic**; if it
  matters to the owner before S6b, S6a stays in shadow until S6b lands.
- The `resolve-exit-access-ask` arm has zero captured executions, in any slug (see
  `tests/chatbot/COVERAGE.md`). It is covered by unit tests, not by replay.
- **For S6b, one detail that will look like a port bug and is not:** `probe-incoming`'s
  `contact_id` parameter is `={{ $('build-ctx').first().json.ctx.contact.id }} ` with a
  TRAILING SPACE, and `probe-customer-orders`' is not. Whatever S6b builds the tool
  arguments with has to decide whether to reproduce that or normalise it, and say which;
  today it reaches `sub-get-results` as a string with a space on the end.
- **`resolve-entity` carries `retryOnFail` in n8n and the port has no retry.** A transient
  resolver failure that n8n survived is a shadow-lane failure here. Named in the plan's S6a
  section; the shadow window is what says whether it matters.

---

## S6b - `sub-fetch-results`, `sub-get-rag` and `sub-get-results` move into the CRM

**CRM side (shipped, inert by default).** The fetch step is
`app/services/chatbot/lanes/business/fetch.py` plus `run_fetch`, behind the SAME
business-lane switch S6a introduced. Nothing new to turn on: a turn that
does not run S6a's resolve+gate never reaches the fetch either.

Three subs are replaced at once because they are one straight line in n8n:
`sub-fetch-results` calls `sub-get-rag` for the tool and `sub-get-results` for the answer,
and neither is called from anywhere else on the turn path.

### What the CRM does instead

| n8n | CRM |
| --- | --- |
| `Execute 'sub-get-rag'` -> `HTTP Request` (embeddings) -> `Execute a SQL query` (pgvector) -> two Code nodes | `FetchServices.embed` + `EmbeddingReadService.search_tool_chunks` + `fetch.collapse_tool_rows` |
| `tool-filter` | `fetch.tool_filter` -> `ToolPick(items, outcome)` |
| `if-tier-ask` / `tier-probe-plan` / `tier-probe` / `tier-probe-collect` / `if-tier-has-any` | `fetch.tier_probe_plan` / `tier_probe_collect`, dispatched by `run_fetch` |
| `Call 'sub-get-results'` -> `entity-ids-transformer` -> `MCP Client1` -> `output-structurer` | `fetch.entity_ids_transformer` -> `FetchServices.mcp_call` -> `fetch.output_structurer` |
| `fetch-result` | `fetch.fetch_result` |

**Two things stop being n8n's problem, and both are catalogued hazards.** `sub-get-rag`
holds a POSTGRES CREDENTIAL and runs a hand-written pgvector query against production
(H53); the CRM does the same read through `EmbeddingReadService`, so that credential can be
removed from the n8n instance entirely once the sub is unpublished. And `MCP Client` /
`MCP Client1` both hard-code a raw IP endpoint (H52); the CRM reads
`settings.ai_assistant_mcp_url`, so moving the MCP server is a config change rather than a
workflow promote.

### Step 1 - shadow window

Same shape as S6a's, and it runs on the same flag, so in practice S6a's window IS this
window once S6b is deployed: compare `delegate_payload.fetch` against what
`Call 'sub-fetch-results'` returned on the same turn.

**Precondition for step 2:** zero `looked_up` failures, and the picked TOOL identical on
every turn. The tool is the thing to watch rather than the rendered text: an embedding
model or a tool-registry change moves the pick, and a different tool is a different answer.

### Step 2 - the wiring change, in `sub-main-processing` (`53RxDSON8P3QSN22`)

1. **Add** a trigger input `fetch_payload` (type object) beside `resolve_payload` (S6a).
2. **Replace the body of `fetch-result-clean`** - already the node that strips `tool` and
   `tier_probe` back off before `validator` sees them - so it reads the trigger instead of
   the call:

   ```js
   const j = $('When Executed by Another Workflow').first().json.fetch_payload;
   const { tool, tier_probe, _fetch_arm, ...rest } = j;
   return [{ json: rest }];
   ```

3. **Repoint `build-result`**, which reads `$("Call 'sub-fetch-results'").first().json.{tool,tier_probe}`
   BY NAME, to the same trigger key. This is the one by-name read in this cut and it is the
   one a rewire does not redirect.
4. **Delete** `Call 'sub-fetch-results'` and `fetch-arm` (the Switch on `_fetch_arm`);
   `run_fetch` has already taken that decision and the CRM returns the arm it chose. Wire
   `ef2-gate`'s converged output straight to `fetch-result-clean`.

**`access-level-choice-message` STAYS** and keeps its `fetch-arm` predecessor edge replaced
by a small If on `fetch_payload._fetch_arm === 'tier-ask'`: S6b decides the arm, S6c renders
its copy, and deleting the renderer now would take the tier ask down between the two slices.

### Step 3 - unpublish

`sub-fetch-results` (`8Nlm3XmY4dJvBrPO`), `sub-get-rag` (`tWP33QOFT7SxThfT`) and
`sub-get-results` (`rysSPgUssLDf6xJc`) are unpublished only after a week with no rollback.
**`sub-get-results` is called from FOUR places, not one** - the two S6a pickers'
`probe-incoming` / `probe-customer-orders`, `tier-probe`, and the answer path - so it can be
unpublished only when S6a's probes are also in-process (they are today's known S6b
dependency, see the S6a section's "Not covered"). Until then it stays published and the two
picker probes keep calling it.

### Rollback

Turn the business lane off: with no `resolve_payload` there is no
`fetch_payload` either, and both arms fall back together. After step 2 has landed the
rollback is re-adding `Call 'sub-fetch-results'` and repointing `build-result` - keep the
workflow JSON from before that edit, which is the actual rollback artefact.

### H49, and why there is no per-tool branch

`crm_order_management_orders_by_product_list` has never been selected in ANY capture graded
so far - 39 `tool-filter` captures on the live sub, plus the earlier fork's. The port
therefore carries no branch keyed on that tool. What it DOES carry is the JS's own
`DATE_PARAMS` and `ORDER_TOOLS` lookup tables, verbatim including that tool's row: those are
tables, not branches, and dropping a row would be a silent behaviour change on the day the
tool is first picked. The measurement that would justify an actual branch has not been
taken, and the module's docstring says so.

### Not covered by this slice

- **The picker probes still need S6a's seam filled.** `run_fetch` supplies the answer path's
  MCP call; `probe-incoming` / `probe-customer-orders` are the SAME `sub-get-results` call
  with different arguments, and S6a's `services._probe` still raises. Wiring it to
  `entity_ids_transformer` + `mcp_call` is a small follow-up and is what lets `sub-get-results`
  be unpublished.
- **`fetch-result`'s `result` arm does not render an answer.** S6c owns `validator`,
  `promo-picker` and `build-result`; until then the turn still delegates to n8n's business
  lane with the fetch output attached, so nothing is re-fetched.
- **The orphaned `AI Agent` + `MCP Client` tool nodes in `sub-get-results` are NOT ported**
  and never ran in the capture pool (H7). They are deleted with the sub.

## S6c - the answer half, `sub-answer` and `sub-miss-suggest` move into the CRM

**CRM side (shipped, inert by default), and it takes TWO switches, not one.**
`chatbot_business_lane_enabled` (S6a's switch) decides whether the lane RUNS at all;
`system_settings.chatbot_completed_lanes` decides whether an arm may ANSWER. Both default
off, and both are needed: with only the first, the CRM would start answering the moment it
deploys and the n8n edit would have to land in the same window or every business turn would
be answered twice. Add `business_query`, then `check_promotion`, then `stock_denied` one at
a time; each is a data change with an instant rollback.

The three arms are the same three S6a named, and they converge on one node in n8n
(`resolve-arm`), so the CRM keeps one `delegate` name for all three.

### What the CRM does instead

| n8n | CRM |
| --- | --- |
| `validator` | `answer.validator` |
| `promo-picker` | `answer.promo_picker` |
| `crossdomain-zeroset` / `crossdomain-gate` / `crossdomain-probe` / `crossdomain-render` | `answer.run_crossdomain` (one `AnswerServices.mcp_probe` call, the turn's SECOND) |
| `build-result` | `answer.build_result` |
| `If6` / `Aggregate1` | `answer.dispatch` / `answer.aggregate_response_intro` |
| `Call 'sub-answer'` -> `answer-input` / `central-exchange` / `miss-roster-*` / `build-miss-member-offer` / `dym-transform-partial` / `dym-probe-partial` / `dym-annotate-partial` / `answer-result` | `sub_answer.py` + `business._run_answer_half` |
| `not-found-error-message` | `answer.not_found_error_message` |
| `Call 'sub-miss-suggest'` -> `sibling-gate` / `family-fetch` / `sibling-transform` / `sibling-probe` / `dym-transform` / `dym-gate` / `if-promo-dym` / `promo-dym-plan` / `promo-dym-probe` / `dym-probe` / `dym-annotate` / `miss-suggest-result` | `miss_suggest.py` + `miss_suggest.run_miss_lane` |
| `build-suggest-offer` | `answer.build_suggest_offer` (still on the SPINE in n8n, RS-7 errata) |
| `access-level-choice-message` | `answer.access_level_choice_message` |

**One credential and one raw host stop being n8n's problem.** `family-fetch` is an
`httpRequest` node pointed at `https://72.62.195.20/api/v1/master-data/products` with
`allowUnauthorizedCerts: true` and a header credential; in process it is
`ProductService.list_products(query=..., variant_filter="all", limit=5000)`, so there is no
IP to go stale, no certificate to wave through and no credential in the workflow. That is
the same class as S6b's H52 / H53 pair, on a third node.

### Which bodies the port was made from, and why it matters here more than anywhere

Four S6c node NAMES exist twice in the n8n instance, with different bodies:

| node | LIVE SPINE (`9qVyfUxmRQqrpGRMDLRuz`) | the sub-workflow | ported from |
| --- | --- | --- | --- |
| `dym-transform` | 421 lines (pre-Fix-4) | 561, `sub-miss-suggest-live@f42de9c6` | the SUB |
| `dym-annotate` | 169 lines (pre-Fix-4 / F1 / F8) | 247, same sub | the SUB |
| `build-suggest-offer` | 710 lines | 944 in `sub-main-processing-live` | the SPINE |
| `promo-picker` / `not-found-error-message` | 583 / 667 | 596 / same | the SPINE |

The rule is the SLUG THE CAPTURES CAME FROM. 33 of the 41 graded `dym-transform` captures
were taken inside `sub-miss-suggest-live`, so the sub's body is what they grade; the four
captures taken against the spine's stale inline copies are registered in
`tests/chatbot/divergences.py`, naming exactly the keys the older bodies cannot emit
(`dym_candidate_uuids`, `dym_probe_row_keys`, `probe_uuid_keyed`, `dym_ambiguous_codes`,
`dym_ambiguous_uuids`, `dym_probe_meta.key_mode`). **Those two spine nodes are dead code the
day the wiring change below lands, and re-capturing them is not worth doing.**

### Step 1 - shadow window

Deploy with `chatbot_completed_lanes` EMPTY. The lane runs, the answer half runs, and the
turn still delegates: `delegate_payload` carries the resolve + gate + fetch output exactly
as it does today. Compare the CRM's would-be reply against n8n's actual one for 3 to 7 days
(plan gate 4).

**Precondition for step 2:** branch parity 99%+ and reply-text parity 97%+ on 500
consecutive live turns, with every mismatch triaged, AND zero `replied`-stage failures. The
reply text is the thing to watch on this slice specifically: S6c is where the customer's
words are composed, and a divergence here is visible on WhatsApp rather than in a trace.

### Step 2 - flip the lane on

Add `business_query` to `system_settings.chatbot_completed_lanes`. The CRM answers, returns
`delegate: null`, and the caller's existing `delegate == null` gate (added at S3) sends the
reply without entering any n8n lane. Nothing is deleted yet, so the rollback is removing the
string again.

Then `check_promotion`, then `stock_denied`, each after its own quiet period.

### Step 3 - the wiring change, in `sorento-consume-main` (`S4N1LiisAqA4hpMC`)

Only after all three lanes have been on and quiet.

1. **Delete** `Call 'sub-main-processing'` and its two `tag-entry-*` predecessors
   (`tag-entry-access-check`, `tag-entry-resolve`) plus `Edit Fields2` - the Set node whose
   one field, `not_allowed_check_stock`, is now on the CRM's own payload (AC-610).
2. Wire the `route` Switch's three business outputs straight to the `delegate == null` gate,
   the same shape S3's eight canned arms already take.
3. **`access-level-choice-message` goes with them.** S6b deliberately kept it alive behind a
   small If on `fetch_payload._fetch_arm`; S6c renders that copy, so the If and the node are
   deleted together.

### Step 4 - unpublish

Seven sub-workflows, and they must go in this order because three of them are called from
more than one place:

1. `sub-answer` (`oIzFAzi3bGgn5mTH`) and `sub-miss-suggest` - one caller each.
2. `sub-main-processing` (`53RxDSON8P3QSN22`) - once step 3 has landed.
3. `sub-resolve-and-gate`, `sub-fetch-results`, `sub-get-rag`, `sub-get-results` - S6a's and
   S6b's, and `sub-get-results` LAST: it is still called by the two S6a picker probes
   (`probe-incoming` / `probe-customer-orders`) until that seam is filled in process.

All seven stay published, disabled, for one release. The n8n Postgres credential
`sub-get-rag` holds can be deleted from the instance once step 4 completes.

### Rollback

Before step 3: remove the branch kind from `chatbot_completed_lanes`, or turn
the business lane off. Both are data, both take effect on the next turn, and
neither needs a deploy.

After step 3: re-add the two `tag-entry-*` nodes, `Edit Fields2` and
`Call 'sub-main-processing'` from the workflow JSON saved before that edit. That JSON is the
rollback artefact and belongs in the promote note, not in this file.

### H49 is unchanged by this slice, and one more like it turned up

S6b's H49 note stands: no per-tool branch, tables kept verbatim. S6c adds a second
never-fired path with the same disposition - `promo-dym-plan` and `promo-dym-probe`, the
promotion did-you-mean fan-out, fired ZERO times in the 232-execution
`sub-miss-suggest-live` pool that was scanned end to end. The nodes ARE ported
(`miss_suggest.promo_dym_plan` and the `row_present` predicate in the annotator), because
the alternative is a lane that dead-ends the first time a promotion miss carries candidates,
and the port costs 40 lines. What is NOT built is any behaviour keyed on it that no capture
can grade. It is a real zero cell in `tests/chatbot/COVERAGE.md`, not an oversight.

### Not covered by this slice

- **The two picker probes still call `sub-get-results`.** Unchanged from S6b's note, and it
  is what keeps that sub published.
- **`build-suggest-offer` needs the turn id where n8n uses `$execution.id`.** The offer's
  identity only has to be stable within the session, so the turn id is the correct successor,
  but it can never equal a captured execution id - registered once for the world replay
  (`tests/chatbot/worlds.py::WORLD_DROP_PATHS`) and once per fixture in `divergences.py`.
- **`sub-send-attachments` is untouched.** A promotion answer's attachments still ride the
  caller's own `send_attachments` action; nothing about that path moves here.

---

## S7 - the dispatcher retires and the CRM orders each contact's turns

**CRM side (shipped, inert by default).** Two switches, both `false` on deploy. The first
is a `system_settings` column edited on System > Settings > Chatbot (AC-810), effective on
the next turn with no restart; the second stays an environment variable because it is a
property of the box, not a decision the owner makes:

| switch | default | what it does |
| --- | --- | --- |
| `system_settings.chatbot_ordering_enabled` | `false` | **S7 mode.** The request takes a redis ticket per contact and waits for the ticket before it (`app/services/chatbot/dispatch.py`), AND the CRM owns the tail: `/turn` returns the finished reply, `/turn/{id}/complete` answers 410 Gone |
| `CHATBOT_TURN_ON_WORKER` | `false` | the turn runs on the `chat` RQ queue instead of the API thread; the request still waits for it and answers the same body |

Two more knobs, both with defaults that do not need touching for the promote:
`CHATBOT_QUEUE_WAIT_SECONDS` (45; 30 and capped since CHATBOT-QUEUE-FIX, 1 Oct 2026) and `CHATBOT_TURN_WAIT_SECONDS` (60).

**Nothing changes in n8n until the owner turns Ordering on.** That is the
whole cutover: today `sorento-dispatcher` pops one contact per second and serialises the
world; after the flip the CRM serialises per contact and different contacts run at once.
Flipping the flag while the dispatcher is still in front of it does not disturb the
ordering (the CRM's ticket is always free, because the dispatcher already made sure of it),
which is why the order below is CRM flag first, n8n edit second.

**But the flag has a second half, and it is a precondition, not a detail.** In S7 mode the
CRM owns the tail: `/turn/{id}/complete` answers 410 Gone, because a caller arriving there
is about to answer a turn the CRM already answered (H6, one trigger). So the flag must not
be turned on until every lane completes INSIDE the CRM - S6c deployed and every branch kind
listed in `system_settings.chatbot_completed_lanes`. Turn it on while a lane still
delegates and that lane has nobody left to finish it: the CRM refuses the turn rather than
leaving a ghost - `failed` at the stage it reached, the error naming the lane and
`chatbot_completed_lanes`, today's error reply to the customer, and Retry available on the
trace screen once the lane is switched on. Check the settings row before step 2; the
symptom of getting it wrong is every LIVE turn of that lane failing at `routed`,
immediately and visibly, and the fix is the flag back off. A dry run (clone, console, load
gate) still delegates and records the finding on its trace, so the shadow window is where
this is meant to be caught.

### Which turns this covers

Every turn. This slice does not move logic; it moves the QUEUE. The owner's number is the
one to hold it to: 50 dealers x 2 questions arriving together. Through the dispatcher that
is 50 seconds of serving before the last dealer's first question starts. After this slice
the 50 first questions run at once and each dealer's second question waits only for their
own first.

### Step 1 - raise n8n's concurrency BEFORE anything else

`N8N_CONCURRENCY_PRODUCTION_LIMIT` (queue mode, per worker; default 10) is the first
throttle the burst meets, and it is upstream of every CRM change here. 100 concurrent
executions means either the limit raised to cover it or enough workers to add up to it.

**Proof it took:** fire the load gate (step 4) and watch the n8n executions list - if
executions sit `waiting` rather than `running`, the limit is still the ceiling and the CRM's
ordering is not what is being measured.

### Step 2 - turn Ordering on (the real cutover)

System > Settings > Chatbot, Ordering on, confirm the dialog. Effective on the next turn:
no restart, and no deploy. Nothing in n8n has changed yet, so the rollback is the same
switch back off: no workflow edit, no deploy of anything else.

**Preconditions:** every branch kind in `system_settings.chatbot_completed_lanes` (see
above - the flag also retires `/complete`), the load gate (step 4) green on the lane's own
backend, and `chatbot.turns` showing no rows failed at `stage = queued` in the shadow
window.

### Step 3 - retire the dispatcher, BOTH injectors in the same promote

The two injectors are the webhook producer and the failover poller
(`CYNq34WZx83POLQ5` -> `sorento-main-INJECT`). **Both flip together.** This is the
concurrency plan's own lesson written down: flip one and the other keeps pushing into redis
lists nothing drains, so every message that arrives through it is stranded silently - the
worst shape of failure this program has, because the customer sees nothing and no row is
written anywhere.

1. **Producer:** replace "push to redis" with the HTTP call to `/api/v1/external/chat/turn`
   (the same node shape S1 introduced) followed by the existing Switch on `action.kind`.
2. **Poller:** the same replacement inside `sorento-main-INJECT`. The carve state
   (`failover_watermark`) and the `in-failover?` gate are UNCHANGED - S7 changes where a
   carved message is posted, never how the carve is decided.
3. **Delete** `sorento-dispatcher` and its redis keys: `q:*`, `ready-contacts`, `lock:*`.
   Only after a week with no rollback; the workflow JSON from before this edit is the
   rollback artefact.
4. **Unpublish** the old monolith `9qVyfUxmRQqrpGRMDLRuz`.
5. **Clone** `Hnd4S8SVH6pftjxs` calls the same endpoint with `is_test: true`; its
   `test-guard` keeps recording actions to `test:egress:{run}`. No change beyond the URL.

### Step 4 - the load gate (AC-711), before step 2 and again after step 3

`sorento_crm_backend/scripts/chatbot_load.py`, dry-run by default. The seeded contacts are
granted every code in `ACCESS_AGENT_CODES` (`general_enquiries` and `order_enquiries` - the
agents a live parse of the script's own QUESTIONS can route to) and the script refuses to
fire unless `system_settings.chatbot_business_lane_enabled` and `chatbot_completed_lanes`
already let the CRM ANSWER a `business_query` turn rather than delegate it - see the
script's own module docstring for why (a granted contact whose turn still delegates
measures the SAME wrong path one stage further downstream). AC-711 reads STRICT: every turn
has to land on the business lane, not merely one of them.

On a lane backend:

```bash
python scripts/chatbot_load.py --base-url http://localhost:8002 --contacts 50 --messages 2
python scripts/chatbot_load.py --base-url http://localhost:8002 --contacts 50 --messages 6   # the 300-turn repeat
```

On the PROD HOST itself (`/opt/sorento-crm2/` there holds only `docker-compose.yml` and
`.env`, no checkout, so this runs inside the `backend` container) - the owner's decisive
number, mocked first then the real parser cost, second burst reported:

```bash
docker compose exec backend python scripts/chatbot_load.py \
  --base-url http://localhost:8000 --contacts 50 --messages 2 --production
docker compose exec backend python scripts/chatbot_load.py \
  --base-url http://localhost:8000 --contacts 50 --messages 2 --production
docker compose exec backend python scripts/chatbot_load.py \
  --base-url http://localhost:8000 --contacts 50 --messages 2 --production --live-llm
docker compose exec backend python scripts/chatbot_load.py \
  --base-url http://localhost:8000 --contacts 50 --messages 2 --production --live-llm
```

`--production` is required whenever `ENVIRONMENT=production` (a database-level guard, not a
hostname check - `localhost:8000` is what the backend calls itself INSIDE the container
either way) and prints exactly what it writes (`ZZT-load-` `respond_contacts` rows,
`contact_agent_access` grants, `chatbot.turns` rows - all removed again in the `finally`
except the turns, which are the evidence) before waiting for a typed `yes`. It never writes
`system_settings`, even to flip the lane switches, on a production database - if the
pre-check refuses, the owner flips them on the Settings > Chatbot screen first. `EXTERNAL_API_KEY`
comes from the container's own env (`docker compose exec backend env | grep EXTERNAL_API_KEY`,
or pass `--api-key` directly).

Green means: p95 turn time under 12 s, zero errors, EVERY turn's `branch_kind` landing on
the business lane's own arms (not `access_denied` or anything else - the run fails if even
one does not, printed as a histogram every run), and every contact's replies in the order
the CRM RECEIVED that contact's messages, with no two of that contact's turns overlapping.
Arrival order, not send order: the CRM cannot know which message the customer typed first,
only which one reached it first, and the script reports the difference separately rather
than failing on it (at 300 concurrent the load generator's own threads reorder its sends for
about half the contacts). Order and `branch_kind` are both graded from `chatbot.turns` after
the run, once every turn has settled to a terminal status (`_wait_for_turns_to_settle` polls
for this before grading OR cleaning up - see below) - never from the client's send order.

It posts `is_test: true` envelopes (D14: nothing outside `chatbot.turns` is written and no
WhatsApp message can leave), which is what makes it safe to run repeatedly.

**The 5 Sep 2026 numbers below are SUPERSEDED - they measured the wrong path.** The seeded
contacts carried no access grant, so `head/route.py`'s first predicate denied every one of
them and every turn in every run took the free `access_denied` canned lane, never the
resolve/tier-gate/fetch/answer path AC-711 is actually about (`branch_kind` was
`access_denied` on 100% of rows; a fix on 6 Sep 2026 seeds the grant and the two settings
switches above). ~~Measured on this branch, 5 Sep 2026, ordering ON, one uvicorn worker,
mocked parser: 100 turns, zero errors, zero out of order, p95 1.24 s; the 300-turn repeat p95
3.77 s.~~

**Re-measured 6 Sep 2026, chatbot-s8 lane backend, one `uvicorn --reload` worker, mocked
parser, business lane and `business_query` both switched on. Raw numbers, not a summary**
(`uptime` immediately before: load averages 4.88 5.34 6.24):

```
chatbot load: 50 contacts x 2 messages = 100 turns against http://localhost:8002/api/v1/external/chat/turn (dry run, mocked parser)
wall 120.1s  turns 100  p50 120.00s  p95 120.06s
errors 95  out-of-order contacts 2
branch_kind: {'business_query': 30, '(none)': 3}
```

`branch_kind` confirms the fix works: real `business_query` turns now land, zero
`access_denied`. p95 120.06s is the CLIENT's 120s request timeout, not a real completion
time - 95 of 100 requests either errored or were still waiting when the client gave up.
This run predates the S4 fix (`_wait_for_turns_to_settle`) that shipped alongside it: cleanup
ran immediately after the client gave up, and a further 21 turns landed roughly 3 minutes
later as `branch_kind=None, stage=received, status=failed`, because the single overloaded
worker was still draining its backlog and the contacts they needed had already been deleted -
the exact bug that fix closes, caught by this run's own evidence. None of this is a
regression from this PR - it is the capacity question AC-711 was supposed to be answering
all along, hidden by the wrong-path measurement above. Load average right after this run
spiked to 52 (unrelated concurrent work on the shared machine, confirmed via `ps`), which
made a same-session repeat unsafe to compare against and was not attempted. A real capacity
number needs either a multi-worker lane box, a smaller burst, or the prod-host run above;
that work is tracked in the plan's capacity section, not this one.

**Re-verified 6 Sep 2026, after the `_wait_for_turns_to_settle` and STRICT-gate fix, same
lane backend and settings, `--timeout 240`** (`uptime` before: load averages 4.16 5.16
6.27). Raw numbers:

```
wall 240.2s  turns 100  p50 240.00s  p95 240.07s
errors 100  out-of-order contacts 0
branch_kind: {'business_query': 25}
75 turn row(s) missing - the burst did not all reach chatbot.turns
RED - p95 240.07s is over the 12.0s target
```

At grading time every one of the 25 landed rows was `business_query / stage=remembered /
status=done` - zero of the earlier run's straggler shape in the graded report. **The
settle-wait narrows the race, it does not close it under this much backlog**: a further 16
rows landed as `branch_kind=None, stage=received, status=failed` roughly 10 minutes after
the script exited (`_wait_for_turns_to_settle` had already reported 0 non-terminal and
cleanup had already run). A row that has not been INSERTED yet is invisible to a poller
that only checks existing rows' status, and a single dev worker holding 100 concurrent
requests this far past even a 240s client timeout plus a 180s settle window can still have
requests it has not started on yet. The mechanism is doing what it is built to do - wait
out rows that exist and are still open - and a backlog this deep is itself the same
single-worker capacity finding, not a defect in the wait. All 16 were cleaned up by hand
after being read. The single dev worker is still the ceiling (25 of 100 graded even at a
240s client timeout; `db connections: baseline 19 peak 40`, so Postgres was not it), which
is the same capacity finding as the first re-measurement.
Settings were flipped for this run and restored immediately after (before: `false` /
`[]`; during: `true` / `["business_query"]`; after: `false` / `[]`, verified by re-read).

### Step 5 - the switchover proof (AC-714), on the clone

Not a load test, a correctness one, and it is the reason both injectors flip together:

1. Carve one contact to polling (`failover_watermark`), send a message: it must arrive
   through poller -> `INJECT` -> `/chat/turn` -> send.
2. Un-carve the same contact, send again: it must arrive through the webhook path.
3. Deliver ONE message through both during the switch: exactly one turn runs, the second
   answer is the first one replayed with `duplicate: true`, and the caller's Switch sends
   nothing (AC-712, already covered by `tests/chatbot/test_d15_duplicate_race.py`).

### Rollback

Before step 3: turn Ordering off on the settings screen. Effective on the next turn, no deploy.
After step 3 the dispatcher no longer exists to fall back to, which is why step 3 waits a
week behind step 2 rather than riding with it.

### Not covered by this slice

- **The `/complete` ROUTE is not deleted yet, only refused.** In S7 mode it answers 410
  Gone; with the flag off it works exactly as it did. The route and `delegate.py` are
  deleted at S8, once the S7 promote means nothing calls it - deleting them now would
  strand every turn a lane still completes.
  `tests/chatbot/test_s7_ordering_and_offload.py::TestSingleTrigger` pins both halves.
- **The worker offload stays off.** It is built and tested, and the trigger for turning it
  on is measured, not scheduled. Note what it does and does not buy: it moves the LLM
  call's CPU and memory onto a worker, but the request still waits on an API thread
  (`/chat/turn` is a synchronous endpoint), so it does not raise the concurrency ceiling.
  Turning it on also needs the server compose to run a `worker_fast` container, which
  already drains `chat` via `worker.QUEUES`.
- **Nothing here changes the egress.** The CRM still never sends; the Switch on
  `action.kind` over the existing send / assign / comment nodes is S1's and stays as it is.

---

## Owner ruling K (6 Sep 2026) - `compile-current-state` and `output_exchange` now DIVERGE by ruling

**No n8n edit is required for this one, and that is the point of the section.** The four
rules below change what the CRM persists and how it reads a reply, and n8n's own bodies
are left exactly as they are - so from this deploy onward the two are known to disagree,
by decision rather than by drift. If the n8n bodies are ever re-promoted from the CRM
port, these are the four hunks that must ride with them.

| n8n node | What live still does | What the CRM does now |
| --- | --- | --- |
| `compile-current-state` | recomputes `selection_context` / `last_result_set` from THIS turn's outcome only, so both reset to `null` / the answer's own rows whenever the turn builds no offer of its own | `tail/compile_state.py::_offer_carry` carries the previous label and roster forward on exactly those turns. A new offer this turn still replaces it, a domain change still clears it, and `member_offer` carries only while the escalation offer is unanswered |
| `route-turn` | `is_offer_hold`'s last condition is `routing_roster_plan.length > 1`, so an out-of-range digit against a single-company roster falls through to `low_signal` | a live `last_result_set` under `selection_context: member_offer` is enough to reach `offer_hold`, so the roster is reprompted |
| `output_exchange` (member-offer arm) | a reply carrying a date window or an entity reaches "Tier 4 - junk" and is reprompted as a bad pick, or "Tier 3 - new query" and abandons the offer | a FILTER MODIFICATION arm runs first, for a date window in any domain or an entity that is a filter axis of the carried domain with no new intent: the window and the entity are kept, the carried domain is inherited, nothing is reprompted, and the offer stays pending. A new intent or a new subject type still abandons it |
| `output_exchange` (domain continuity) | blocks the domain carry on the model's own hint (`domain_inherit_blocked`) | a bare single-entity turn inherits the carried business domain and the entity is TYPED by it; the resolver decides, and `disallowed-entity-gate` is the guard |

The last row is a divergence from the LIVE parser body in a second sense worth recording:
the hunk it descends from, `_bareEntityTurn`, exists only on the unpromoted
`sub-semantic-parser-FORK` (`nodes/output_exchange.js`, exec 13687305), it was never
promoted to the body answering turns today, and even on the fork it carries the domain
without RETYPING the entity. The retype is the owner's own addition.

### What proves it

Every capture the ruling moves is registered in `tests/chatbot/divergences.py`, field
scoped, so the rest of each capture is still graded byte for byte: 13
`compile-current-state` (rule 1), 3 `output_exchange` (rule 4), 2 `output_exchange`
(rule 2), and one blanket entry for the three diagnostic keys the port ADDS. 22 world
replays move from graded to skipped, each with a named reason through
`worlds.body_difference`: 14 through the ruling's own clause, 7 through that file's
pre-existing parser-body-difference arm, and 1 multi-turn chain grading a shorter prefix. Behaviour is pinned by
`test_tail_units.py`, `test_output_exchange_rules.py`, `test_route_unit.py`,
`test_resolve_gate_unit.py` and `test_topic_unit.py`.

### Rollback

There is no flag and none is needed: nothing in n8n changed, so reverting the CRM commits
restores the previous behaviour on the next turn.

---

## S-low-stock - the low stock report rides the existing send_attachments action

**No n8n node moves, and none is edited.** The low stock report answers with a workbook,
and a workbook reaches the customer through the `send_attachments` action the egress
Switch already carries (S1) - the CRM emits it exactly as it does for a catalogue PDF or a
product photo. The whole slice is CRM-side: an MCP tool, a presenter envelope, a lane gate
and a route. It is written down here because the ONE operational step it does need is a
per-contact grant nobody would find in a code diff.

### Which turns this covers

A staff contact typing "low stock report", "low stock report BRW", "low stock for
SRTWT7408 until 30 Nov" or "reorder report". The bot runs a FRESH plan for that scope and
answers with the workbook as a document, or - when the plan outruns the turn - says the
file is on its way and the worker pushes it the moment it is ready.

### Step 1 - deploy (nothing changes)

The tool, the presenter, the gate and the route ship inert: with no contact holding
`scm.low_stock_report`, every low stock ask is refused before any fetch with "Low stock
report is not enabled for your account." and the existing team picker. No run is created,
and no other turn changes - the tool is APPENDED to the inventory domain's pool, never its
first tool, so a plain stock ask ("how many CB100 in BRW") still answers from stock
balance.

### Step 2 - the wiring change (none)

There is no n8n node to add, move or re-point. `send_attachments` is already on the egress
Switch, and the CRM emits the same action shape it has since S1.

The only operational step is a GRANT, done on the CRM screen, per contact:

1. **Contacts > Access > Field reveals**: tick **Low stock report over chat**
   (`scm.low_stock_report`) for each staff contact who may run it. It is listed there with
   no code change beyond the catalog entry, and it is what the lane checks BEFORE any
   fetch - this tool's fetch creates a reorder run, so a refused contact must never reach
   it.
2. **Confirm the act-as user holds `scm.reorder.run`.** The route is reached with
   `X-API-Key`, and its first gate is that permission on whichever user
   `EXTERNAL_API_KEY_ACT_AS_USER_ID` (or the integration's own `act_as_user_id`) names.
   Without it every contact - granted or not - gets a 403 from the first gate and the
   feature is silently off.
3. **Promote the parser prompt label** (System > AI prompts > `chatbot_semantic_parser`):
   migration `517_chatbot_low_stock_vocab` publishes both bodies carrying the low stock
   words as new UNLABELLED versions, and `ai_prompt_registry.render()` reads the version
   the `production` label points at - never the Python constant. Until the label is moved
   onto the newest FULL body, "low stock report" still parses as `check_stock` and the
   intent never fires, exactly as the 14 Sep console run found. Move it after reading the
   diff; rolling back is the reverse move.

### Step 3 - watch one turn end to end

Ask "low stock report" from a granted contact. Either the reply carries the workbook in
the turn, or it says the report is being prepared and the file arrives seconds later from
the worker's own push. Exactly one of the two happens, never both and never neither - two
conditional updates on the download row decide it. The run appears in Procurement > Supply
Chain > Reorder Planning marked **via chat**, so the buyer can see why a plan nobody here
launched exists.

### Step 4 - unpublish (nothing to unpublish)

No n8n workflow version is published by this slice, so there is no label to move and
nothing to retire.

### Rollback

Untick **Low stock report over chat** for every contact. Effective on the next turn, no
deploy: the lane refuses before any fetch, exactly as it did before the grant. Move the
`chatbot_semantic_parser` `production` label back to the previous version as well if the
new vocabulary needs to go with it - the published versions are immutable, so a rollback
is one label move either way.

### Not covered by this slice

- **Category scope** ("water tap low stock"): runs have no category filter, so the ask
  would silently plan everything. Backlogged.
- **A PDF flavour** of the same report, and per-warehouse quantity columns on the All
  sheet. The workbook is what the client asked for.
- **The plan-view export is unchanged.** The buyer's own "Low stock report Excel" item
  on the Actions menu goes through My Downloads, has no reveal key, and always includes
  the Supplier column - only the chat path narrows it.
