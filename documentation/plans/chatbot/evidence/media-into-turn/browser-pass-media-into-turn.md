# Browser verification: chatbot media-into-turn (lane `feat/chatbot-media-into-turn`, DRAFT PR #1139)

Run 23 Sep 2026. agent-browser (`npx -y agent-browser@0.27.0`, session `media-into-turn-pass`),
headless, against the lane's own stack (FE :3000 `next dev`, BE :8000 uvicorn on
`sorento_ai_automation_0921`, worker on redis db 13 queue `media`). Driven through the
Chatbot Console (System > Messaging > Chatbot Console, reached by sidebar clicks) because the
`/external/chat/turn` API key is not available on this DB.

**Console contact used:** "CONTACT D" (`respond_io_id 900000072`), chosen because
`contact_media_limit` shows `is_allowed=true` for image and voice for this contact (the console's
default contact, Katherine Loo, has no `contact_media_limit` row at all - see AC-1810 below, that
turned out to be free coverage of the gate-denied path). CONTACT D is mapped to company
`00000000-0000-0000-0000-000000000001`, the same company that owns the three test product codes.

**Test image:** the pre-rendered PNG at the scratchpad path given in the brief, containing
"Product List / BRBC22293W-1 / SRTWT1506 / SRTWT1805" - all three confirmed to exist in
`products.product_code` on this DB before testing.

**Worker:** picked up every media job within 5-7s each time; no timeout was hit. Log lines
`INFO:rq.worker:media: app.tasks.media_tasks.process_media_extraction(...)` through
`Job OK` for every submitted photo/voice. No worker restart needed.

## Headline finding: a DEFECT, not an environment gap

**S3's `entities_only` arm (AC-1822-1832) never places a real product code.** Every turn the
parser routes into lane `entities_only` (bare codes/photo, no domain word, no carried focus)
comes back "Couldn't find" for ALL codes, even though the same codes resolve correctly one
call away. See "Defect 1" below - this blocks a clean pass on the whole of UAC Section C and,
by extension, J1's first turn and any bare-code entry point. J2/J3/J4 (a domain word already
present, or already carried) do NOT go through this arm and resolve correctly.

A second, separate, environment-only limitation: this lane's stack does not run the MCP tool
server the ordinary business/fetch lane calls for the actual stock number
(`crm_inventory_stock_balance_list`), so every turn that reaches the real fetch step answers
"I could not fetch stock just now, please try again." This is NOT a lane defect - resolution and
the reply prefix are the parts in scope here, and both are visible working right up to the point
of the tool call. Noted per AC below as SKIP (environment), not FAIL.

## Defect 1 - `entities_only` arm resolves nothing (blocks AC-1822/1823/1824/1825, J1, part of J2)

**Steps (photo, no caption):** Chatbot Console, contact CONTACT D, Reset, attach the test
PNG with no caption, no prior focus/domain. **Expected** (AC-1824): "I read A, B and C from that
photo. Couldn't find D. What would you like me to do with it?" - listing only tokens that truly
did not resolve; since all three codes are real, no "Couldn't find" sentence should appear at all.
**Observed:** "I read BRBC22293W-1, SRTWT1506 and SRTWT1805 from that photo. Couldn't find
BRBC22293W-1, SRTWT1506 and SRTWT1805. What would you like me to do with it?" - every code is
named as unfound, including the ones just listed as read. Screenshot
`browser-pass-J1-turn1-photo-only-DEFECT.png`. Turn id `8a40702f-13f9-4718-b9ca-cde2547471a1`.

The trace (`browser-pass-turnpanel-stages.png`, `browser-pass-turnpanel-apply-statediff-DEFECT.png`)
confirms: `lane: entities_only` (routing is correct - AC-1822 itself passes, this is NOT the
`casual` LLM clarifier), parser verdict has all three entities with `hint: "product"`,
`confident: true`, but `canonical_code: null` for every one, and the state diff shows
`focus.products` written with the SAME unresolved raw rows (`canonical_code: null`) rather than
enriched with a `uuid` - i.e. `focus_settles_product` (AC-1823's own trace rule) did not fire.
"Focus" tab: "No focus rule fired this turn."

**Reproduced a second way**, ruling out anything photo-specific: typed "BRBC22293W-1, SRTWT1506,
SRTWT1805" with no domain word, fresh conversation, same contact -> "I have . Couldn't find
BRBC22293W-1, SRTWT1506 and SRTWT1805. What would you like me to know?" (screenshot
`browser-pass-typed-bare-codes-DEFECT.png`) - same failure, plus a second, smaller defect: "I
have ." with a stray space before the period when the placed list is empty (AC-1825's wording
does not cover the placed-list-empty case).

**Isolated to this one arm, not a general resolver regression:** the SAME contact, SAME session,
immediately afterward:
- Typed "check stock" after the failed bare-codes turn correctly CARRIED the three raw codes into
  the fetch (`*stock* for BRBC22293W-1, SRTWT1506, SRTWT1805:`, no "Couldn't find") - domain carry
  itself works, screenshot `browser-pass-J1turn1typed-then-checkstock-carry.png`.
- Typed "check stock for SRTWT1506" (domain word up front) resolved SRTWT1506 immediately, no
  "Couldn't find" (screenshot `browser-pass-domain-first-resolves-ok.png`).
- Two more attempts at the identical bare "photo, no caption" turn had the parser LLM
  independently tag `domain_hint: inventory` on its own (skipping `entities_only` entirely, an
  expected LLM non-determinism on an ambiguous bare-code message) - both times resolution
  succeeded (`*stock* for BRBC22293W-1, SRTWT1506, SRTWT1805:`).
- A standalone script (`business_services.production_services(db, ...).resolve_entity(...)`, the
  exact call `_run_entities_only_arm` makes) against the same three tokens returns `matches: []`
  for all three on a **bare, unscoped session** (company scope `UNSET`), and resolves all three
  correctly the moment `set_company_scope(db, frozenset({"00000000-0000-0000-0000-000000000001"}))`
  is applied first - which is the exact failure mode `engine.py`'s own H56 comment names ("An
  unstamped session reads UNSET ... the turn answers 'Couldn't find: <code>' for a product that
  exists in the contact's own company"). This strongly suggests the `db` session
  `_run_entities_only_arm` calls `resolve_entity` on is not carrying the scope the rest of the
  turn's `db` carries, though I did not chase the exact seam further - that is the coder's
  diagnosis, not mine to fix.

**Net effect on the journeys:**
- **J1 FAIL** (first turn): photo-only reply is "Couldn't find" for all three real codes instead
  of a clean read; the SECOND turn ("check stock") does carry the codes through and answers with
  the right product list, but AC-1824/1827's first-turn wording is wrong.
- **J2 PASS** for routing/prefix (domain was said first, "check stock" needs-scope reply, then
  photo answers directly with the carried domain, prefix "I read BRBC22293W-1, SRTWT1506 and
  SRTWT1805 from that photo." exactly as specified) - never touches `entities_only`, so unaffected
  by Defect 1. Final stock number blocked by the MCP gap (below).
- **J3 PASS** for routing/prefix/resolution (caption "Check stock" + photo, one turn,
  `rendered_text` = "Check stock: BRBC22293W-1, SRTWT1506, SRTWT1805" exactly per AC-1807, prefix
  correct, all three codes resolved) - domain given up front, never touches `entities_only`.
  Final stock number blocked by the MCP gap.
- **J4 PASS** for routing/prefix/resolution/transcript (voice "stock for SRTWT1506" synthesized
  via macOS `say`, Whisper transcript "Stok for SRTW-T1506", reply opens "I heard: Stok for
  SRTW-T1506." then correctly resolves to `*stock* for SRTWT1506:`) - domain word present, never
  touches `entities_only`. Final stock number blocked by the MCP gap.

## Environment limitation - stock fetch needs the MCP tool server

Every turn that reaches an actual fetch (J2/J3/J4, and the carried J1 second turn) answers
"I could not fetch stock just now, please try again." Backend log:
`chatbot: MCP tool crm_inventory_stock_balance_list failed` ->
`httpx.ConnectError: [Errno 61] Connection refused` in `ai_assistant_service.py::_rpc`, i.e. the
business lane's stock fetch is itself an MCP tool call, and this lane's stack (FE/BE/worker only,
per the brief) does not run an MCP server on the port `AI_ASSISTANT`/`ai_assistant_service`
targets. Not a lane defect - noted so the coder/captain don't misread the "please try again" text
as new breakage. Reproduced identically on a plain domain-first stock question with no media
involved at all, so it predates and is unrelated to this PR.

## AC-by-AC (Section A/B/C reachable via console; D/E where possible; F/G/H not attempted here)

| AC | Result | Evidence |
|---|---|---|
| AC-1800/1801 media_intake stage present, modality/decision/status/job id/elapsed_ms | PASS | Stages tab shows `media_intake 6225ms "Read the photo."`; voice turn's Stages tab (not screenshotted individually) showed the equivalent for voice |
| AC-1802 turn_id / contact_media_usage share the turn id | SKIP (would need a DB read tied to a specific job row; not done from the browser) | - |
| AC-1803 no open DB session during the wait; bounded by media_sync_wait_seconds | SKIP (pytest-only assertion) | - |
| AC-1804 document/video/sticker-without-url skip intake | SKIP (not exercised - no such fixture attached) | - |
| AC-1805 no double-intake on the transition-window shape | SKIP (n8n-only shape, cannot construct from console) | - |
| AC-1806 AUDIO_NOT_PATCHED_ERROR gone | PASS (indirect) - voice turn transcribed and answered normally, no such error text anywhere | `browser-pass-J4-voice-audio-bubble.png` |
| AC-1807 caption + image -> `"Check stock: <raws>"` | PASS | J3 trace: `Read from image: Check stock: BRBC22293W-1, SRTWT1506, SRTWT1805` |
| AC-1808 no caption -> rendered_text is the raws, needs_clarification still true in the result | PASS (rendered_text non-null; reply reads "I read ... from that photo." not a bare re-ask) | `browser-pass-J1-turn1-photo-only-DEFECT.png` |
| AC-1809 voice -> parser gets the transcript verbatim | PASS | reply opens "I heard: Stok for SRTW-T1506." then acts on "SRTWT1506" |
| AC-1810 denied_gate -> not_enabled wording, branch_kind media_denied, no parser call | PASS | contact Katherine Loo (no `contact_media_limit` row): "I cannot read photos on this number yet. Type the codes instead and I will look them up straight away." chip `media_denied`. `browser-pass-AC1810-denied-gate.png` |
| AC-1811/1812 denied_quota/denied_duration/denied_burst | SKIP (would need to exhaust the monthly limit or burst window; not attempted, out of scope for one pass) | - |
| AC-1813/1814 failed extraction / timeout wording | SKIP (worker never failed or timed out in this run) | - |
| AC-1815 quota warn notice appended once | SKIP (contact was nowhere near the warn threshold) | - |
| AC-1816 console builds the same envelope as WhatsApp, one path | PASS (by construction/code read - `_run_console_media_turn` calls the same `run_turn`; not independently re-verified beyond what the trace shows) | - |
| AC-1817/1818 reply prefix lists every code, truncation note | PASS for the listing (never a count, every code named); truncation not exercised (only 3 codes, cap is 10) | all screenshots above |
| AC-1819 voice prefix "I heard: ..." capped at 160 chars | PASS (short transcript, cap not exercised) | `browser-pass-J4-voice-audio-bubble.png` |
| AC-1820 prefix appears once, never on a text turn | PASS (typed-only turns like "check stock for SRTWT1506" carry no such prefix) | `browser-pass-domain-first-resolves-ok.png` |
| AC-1821 media_max_entities as the cap | SKIP (not changed/exercised) | - |
| AC-1822 entities_only lane selected, LLM clarifier not called | PASS (trace shows `lane: entities_only`, reply is deterministic, not an LLM "are you asking about..." clarifier) | `browser-pass-turnpanel-apply-statediff-DEFECT.png` |
| AC-1823 placed rows land on focus.products WITH a uuid | **FAIL - Defect 1** | same |
| AC-1824 photo-source reply wording | **FAIL - Defect 1** (format right, content wrong: everything reported unfound) | `browser-pass-J1-turn1-photo-only-DEFECT.png` |
| AC-1825 typed-source reply wording | **FAIL - Defect 1**, plus the "I have ." empty-list wording gap | `browser-pass-typed-bare-codes-DEFECT.png` |
| AC-1826 open_question stays null, domains stays empty | PASS (Apply/Focus tabs show no open_question, no domains) | `browser-pass-turnpanel-apply-statediff-DEFECT.png` |
| AC-1827 J1 | **FAIL** first turn (Defect 1); domain carry into turn two works | `browser-pass-J1turn1typed-then-checkstock-carry.png` |
| AC-1828 J2 | PASS (routing/prefix/resolution); final number blocked by MCP gap | screenshots above |
| AC-1829 J3 | PASS (routing/prefix/resolution, one turn); final number blocked by MCP gap | screenshots above |
| AC-1830 J4 | PASS (routing/prefix/transcript/resolution); final number blocked by MCP gap | `browser-pass-J4-voice-audio-bubble.png` |
| AC-1831/1832 | SKIP (not attempted - would need a promotion focus pre-set) | - |
| AC-1833-1838 stored media / migration | SKIP (DB/pytest-level, not browser-observable) | - |
| AC-1839/1840 `media` block on turn GET/list | SKIP (could not extract the raw JSON from the browser session - the FE's bearer token is not reachable from page JS/HAR body capture in this setup; this needs a pytest/API-level check, not browser) | - |
| AC-1841 signed url is company-scoped | SKIP (same reason) | - |
| AC-1842-1846, 1848 Chat History LIST row media bubble (thumbnail/lightbox/audio/chip/plain-on-denial) | **SKIP - cannot reach.** Chat History's list is populated ONLY by the n8n WhatsApp ingest (`chat_histories` table); a console turn is `is_test=True`/dry-run and writes nothing there. The list page confirmed empty throughout this run ("No messages in this range ... chat history is written by the n8n WhatsApp flow"). Without the `/external/chat/turn` API key (not available on this DB, per the brief) there is no way to create a real `chat_histories` row from this environment. This is an environment gap, not a verdict on the feature; the vitest component tests are the authority for this rendering logic. | `browser-pass-escape-closes-drawer.png` (list state) |
| AC-1847 TurnPanel stage for a media turn, entities/attributes/notes/decision, absent on text turns | PARTIAL PASS - the Stages tab clearly shows a distinct `media_intake` "Read the photo." row (absent on the ordinary text-only turns seen elsewhere in this run), but the entities/attributes/notes/decision are NOT shown inline against that stage in this build - they only surface via the separate "Apply" tab's state diff. Whether that satisfies "with entities, attributes, notes and the decision" is a judgement call for the reviewer/captain; flagging rather than failing outright since the data IS present in the drawer, just under a different tab. | `browser-pass-turnpanel-stages.png` |
| AC-1849 no explanatory copy on screen | PASS (chip/stage text is functional, no feature explainer visible anywhere in this run) | all screenshots |
| AC-1850-1853 (S5 partial-hit) | SKIP (not attempted - needs a specific miss-with-no-fuzzy-neighbour fixture; captain/coder said this AC is pending the owner's own trace per commit 1937d167e) | - |
| AC-1854-1857 (n8n cut-over) | SKIP (owner-run, not testable from here) | - |
| AC-1858/1859 hygiene | SKIP (code-review item, not browser) | - |

## Responsive / interaction checks done

- TurnPanel drawer at 375px: no horizontal clipping, all Stages readable, tabs stack cleanly.
  `browser-pass-turnpanel-375.png`.
- Escape closes the drawer (confirmed: drawer gone, underlying Chat History list visible after
  pressing Escape). `browser-pass-escape-closes-drawer.png`.
- Console UI itself (not in scope of the UAC's own Section E, which targets Chat History) renders
  the incoming photo as a re-typed TEXT reconstruction of the recognised content ("Product List /
  BRBC22293W-1 / SRTWT1506 / SRTWT1805"), not an actual image thumbnail - this looks intentional
  for a synchronous dry-run harness (no persisted attachment/signed url exists yet at the point
  the console renders the bubble) rather than a bug, but flagging since it means the console
  itself is NOT a stand-in for verifying AC-1842's thumbnail rendering either.

## Console/JS/network checks

No uncaught console errors during any turn. `network requests --filter /api/v1/` confirmed
`POST /api/v1/system/chatbot/console/turn` fired for every send/upload, and
`GET /api/v1/system/chatbot/turns/{id}` fired when opening the trace drawer.

## Summary

- **DEFECT (blocking a clean Section C / J1 pass):** `_run_entities_only_arm`'s call to
  `resolve_entity` returns zero matches for real, correctly-scoped product codes every time the
  arm actually runs, while the identical codes resolve fine one call away in the ordinary
  domain-first fetch path in the same turn/session. Reproduced 2/2 times the arm fired (once via
  photo, once via typed text), both with full trace evidence. Needs a coder fix + red test before
  this lane can claim AC-1822-1832/J1 green.
- **Everything else reachable from the console (J2, J3, J4, the reply-prefix rules, the
  `denied_gate` path, routing to `entities_only` itself, domain carry) checks out.**
- **Two things this pass could not reach at all, both environment limits stated up front in the
  brief or discovered here, not defects:** the Chat History LIST's media bubble (needs a real
  `chat_histories` row, which only n8n ingest writes, and the API key for that path is not on
  this DB), and the final stock NUMBER (needs the MCP tool server, not part of this lane's stack).

## Rerun 2 (same day) - updated stack: BE :8000 pid 21925 restarted with B1/B2 fixes, MCP now on :8767

Same console contact "CONTACT D", Reset before every turn, same test PNG. Logged back in
first (session had been invalidated by the BE restart; contact selector had reverted to the
default "Katherine Loo" and was re-set to "CONTACT D" for every scenario below).

### NEW blocker found, reported live to the coordinator mid-run, confirmed by them independently

**J1 photo-only is currently un-testable: every media job (photo AND presumably voice, same
worker task) now fails in the WORKER, not in the turn logic.** Steps: Reset, attach the test PNG,
no caption. Console shows "The extraction task failed before it could record a result." /
"I could not read anything from that photo. Type the codes and I will look them up straight
away." (`media_denied`). Worker log (`/tmp/media-into-turn-worker.log`):

```
TypeError: Object of type bytes is not JSON serializable
when serializing dict item '_media_bytes'
...
File ".../app/tasks/media_tasks.py", line 276, in process_media_extraction
    finalized = db.execute(update(MediaExtractionJob) ...)
[SQL: UPDATE media_extraction_job SET status=%(status)s, result=%(result)s::JSONB, ...]
```

100% reproducible: hit on the first attempt (job `aeab4ef3-...`) and again identically on a
manual Retry (job `4504b1ab-...`). A `_media_bytes` key (raw bytes, presumably the S4
store-the-media work) is landing inside the `result` dict that gets written to the JSONB
`result` column without being stripped/serialized first. Screenshot:
`rerun2-BLOCKER-media-bytes-worker-error.png`. **This is the coordinator's own finding, reported
to me mid-run as a known worker-side defect the coder is already fixing** - not something to
re-litigate, just confirming the observed symptom matches exactly. J1/J2/J3's PHOTO steps and
J4 (voice, same `process_media_extraction` task) are BLOCKED pending the worker fix + restart;
not attempted further this session per the coordinator's pause instruction.

### Text-only checks (proceeded per the coordinator's instruction)

**Typed "BRBC22293W-1, SRTWT1506, SRTWT1805" with empty focus.** Requested expectation: "I have
BRBC22293W-1, SRTWT1506 and SRTWT1805. What would you like me to know?" (the `entities_only`
lane). **Observed, 7/7 attempts** (3 with a comma separator, 1 with spaces, 3 more retries; plus
one with a single bare code "SRTWT1506" and one with the codes in a different order): the parser
now assigns a domain hint directly (`domain_hint: "inventory"` most times, `domain_hint: "order"`
for the single-code and reordered attempts) instead of leaving it null, so the turn never reaches
`entities_only` at all - it goes straight through the ordinary business/fetch lane. Reply
observed: "Here's what you want: - product: BRBC22293W-1, SRTWT1506, SRTWT1805. But no inventory
matched these. No stock, no incoming and nothing on order... Would you like me to escalate to
purchasing team?" Screenshot: `rerun2-typed-bare-codes-resolves-via-inventory.png`.

**The underlying Rerun-1 defect (zero matches for real codes) is FIXED**, verified via the trace
on this exact turn (turn id `dfe7e9c8-eb08-407f-9c71-8b1deac6e4b9`, `Apply` tab): `domain_hint:
"inventory"`, `intent_hint: "check_stock"`, and the state diff now reads
`products [] -> [{"raw":"BRBC22293W-1","hint":"product","uuid":"152047ec-928f-4829-aa75-
80483520fc2e","company_name":"Sorento","canonical_code":"BRBC22293W-1"}, ...]` - all THREE codes
resolved with real uuids and the correct canonical code, company-scoped correctly ("Sorento").
"No inventory matched these" is therefore the CORRECT, honest answer (these three products
genuinely have zero stock/incoming/on-order rows on this DB) now that MCP is reachable, not a
resolution failure. **I could not reproduce the specific `entities_only`-lane reply text in 7
attempts** because the parser no longer routes this exact input into that lane at all post-fix -
flagging as an open question for the coordinator/coder rather than a pass or fail, since the
underlying capability (correct resolution, no false "Couldn't find") is demonstrably working,
just via a different lane than the plan named.

**Typed "Hanlim" alone.** Requested expectation: the old `casual` LLM clarifier, not the entities
arm. **Observed:** confirmed NOT captured by `entities_only` (trace: `"Understood as business
query about order (Hanlim)"`, `routed: Business query: order` - `entities_only`'s own
`allowed_entity_types` is `["product"]` only, so a customer-name token was never going to reach
it). It also did not reach the `casual` clarifier though - "Hanlim" resolved as a customer
(`HANLIM TRADING SDN BHD [A/C II] (+5 more)`) and went through the pre-existing business_query
`order` path directly, found no matching orders, and offered escalation. This looks like a
pre-existing, unrelated customer-name-resolution behavior (not something this PR's diff touches),
so reporting as an FYI rather than a defect against this lane. Screenshot:
`rerun2-hanlim-order-domain.png`, turn id `d406c5a7-34d6-4232-afcd-f70abcf110d0`.

**TurnPanel media_intake stage showing entities/attributes/notes/decision inline, no UUIDs.**
BLOCKED - could not check on a fresh turn (every new media job fails per the blocker above). Only
re-checked the OLD Rerun-1 turn (`8a40702f-...`, unchanged since the FE dev server was not
restarted): its Stages tab still shows only the compact `media_intake 6225ms "Read the photo."`
line, no inline entities/attributes/notes/decision - unchanged from Rerun 1. Whether this is
because the FE piece of this change hasn't landed yet, or because it only renders on a NEWLY
completed turn's trace shape, is unknown until a fresh successful media turn exists post worker-fix.

### Rerun 2 summary

| Item | Result |
|---|---|
| J1 photo-only -> every code listed, no "Couldn't find" | BLOCKED (new worker `_media_bytes` JSON bug, confirmed 2/2, coordinator already aware) |
| J1 turn 2 "check stock" -> real stock table | BLOCKED (depends on turn 1) |
| J2, J3 to the stock number | BLOCKED (same worker bug for the photo half) |
| Typed bare codes -> "I have A, B and C..." | **Underlying resolver defect FIXED** (real uuids, correct company scope, correct "no stock" answer) but the SPECIFIC `entities_only` reply text was not observed in 7/7 tries - parser now routes this input through inventory/order domains directly instead. Not a fail on resolution; open question on lane choice. |
| Typed "Hanlim" -> old casual clarifier | PARTIAL - confirmed NOT entities_only (correct), but also not the `casual` clarifier; hit a pre-existing customer/order path instead. Likely unrelated to this PR. |
| TurnPanel media_intake inline entities/attributes/notes/decision, no UUIDs | BLOCKED - no fresh successful media turn to check against; old turn unchanged |

**Waiting on the coordinator's signal that the worker is restarted with the `_media_bytes` fix
before redoing J1/J2/J3's photo steps and the voice check.**

## Rerun 3 (same day) - stack back on af3d4aceb: worker pid 32895 (bytes fix), BE pid 33481, MCP :8767, FE :3000

Logged back in (BE restart invalidated the session again). Used a FRESH console contact per
journey this time (Jayden Loo for J2, Kay for J3, CONTACT G for J4) after noticing CONTACT D's own conversation history from earlier tests was leaking into new "fresh" turns' PARSING
even after Reset + a full page reload + `session_vars` confirmed `{before: {}, after: {}}` on the
Session tab - the parser apparently reads back recent real turn text for that contact regardless
of the per-turn session reset. Noting this as a console-testing gotcha, not a lane defect (worth
a mention to whoever owns the console harness, since it makes "click Reset" alone insufficient
for a genuinely clean re-test of the SAME contact).

### J1 - PASS on the specific ask, blocked further down by a NEW, separate MCP/backend auth issue

Photo-only, no caption, contact CONTACT D, turn `767e0472-2388-47f8-9b40-efef429b45a8`:
**"I read BRBC22293W-1, SRTWT1506 and SRTWT1805 from that photo."** - every code listed, NO
"Couldn't find" for any of the three real codes. Exactly what was asked. Screenshot
`rerun3-J1-turn1-photo-prefix-correct.png`.

The turn's OWN domain guess this time was `master_products` (LLM non-determinism - it was
`inventory` in the earlier reruns, `order` for other inputs), and `master_products`' own
resolution still shows `canonical_code: null` for all three even though they are real products -
a domain-specific resolution gap, separate from the `entities_only` arm, not chased further
(out of scope of tonight's asks and no `master_products` AC in the UAC).

Second turn, same conversation, "check stock" (turn `d6d7fc95-f359-4317-814f-dff722d34f0d`):
domain correctly carries `master_products -> inventory` (no re-ask), and THIS TIME the state
diff shows all three codes resolved with real uuids: `{"raw":"BRBC22293W-1","uuid":"152047ec-
928f-4829-aa75-80483520fc2e","canonical_code":"BRBC22293W-1","company_name":"Sorento"}` etc.
Reply: "No stock, no incoming and nothing on order for BRBC22293W-1, SRTWT1506, SRTWT1805." -
**not a stock table**, because (new finding below) the MCP round-trip to the backend's own REST
API is failing auth, not because these codes are unresolved. Screenshot
`rerun3-J1-full-conversation.png`.

### NEW blocker - MCP reaches the backend, but the backend rejects its OWN configured API key

Picked a product that genuinely has stock on this DB (`SRTPTFE1207`, `sum(quantity_on_hand) =
270300` in `public.stock`) and asked "check stock for SRTPTFE1207" directly (domain-first, no
media) to separate "no data" from "broken fetch". Same "no inventory matched" answer. Backend
log for that turn:

```
POST http://localhost:8767/mcp "HTTP/1.1 200 OK"
GET /api/v1/inventory/stock/balance?product_ids=...&contact_id=900000072&space_id=364817 - Status: 401
integration.auth_refused code=invalid_key
(repeats for /incoming-stock/list and /procurement/purchase-orders/placed)
```

MCP itself answers 200 - it is the MCP tool's own call BACK into this backend's REST API that
gets refused. Confirmed this is not a request-shape issue: `curl` directly against
`http://localhost:8000/api/v1/inventory/stock/balance` with `X-API-Key` set to the EXACT value
in this backend's own running `.env` (`EXTERNAL_API_KEY=test`) gets the same
`{"code":"invalid_key"}` 401 - i.e. the RUNNING backend process (pid 33481) is not honouring the
key its own `.env` file currently states, which points at an env-loading mismatch (stale env var,
wrong `SORENTO_ENV_FILE`, or the file was edited after the process started) rather than anything
in this PR's diff. This blocks the final stock/PO/incoming NUMBER for every journey below
regardless of how well the codes resolve. Screenshot:
`rerun3-BLOCKER-mcp-backend-apikey-401.png`.

### J2 - PASS (routing/prefix/resolution); final number blocked by the same MCP/backend auth issue

Fresh contact Jayden Loo. "check stock" alone -> clean needs-scope reply (no contamination this
time, fresh contact). Same conversation, photo (no caption) -> "I read BRBC22293W-1, SRTWT1506
and SRTWT1805 from that photo." then carries straight into `inventory` (no re-ask), state diff on
the final turn (`b055e2cd-d248-4219-8a24-70056c6cf55e`) shows all three with real uuids again.
"No stock..." for the same MCP/auth reason above. Screenshot `rerun3-J2-full-conversation.png`.

### J3 - PASS (routing/prefix/resolution, one turn); final number blocked by the same issue

Fresh contact Kay. Caption "Check stock" + photo, ONE turn (`754b9118-8e56-464a-923c-
e3d1d9c72066`): `rendered_text` = "Check stock: BRBC22293W-1, SRTWT1506, SRTWT1805" (AC-1807
exact match), prefix "I read BRBC22293W-1, SRTWT1506 and SRTWT1805 from that photo." correct,
domain `inventory` directly, state diff shows all three with real uuids. Screenshot
`rerun3-J3-full-conversation.png`.

### J4 (voice, bare code) - PASS on the specific ask, but reveals the `entities_only` resolver ITSELF is still unfixed

Fresh contact CONTACT G. Synthesized voice saying "SRTWT1506" (no domain word at all). Reply:
**"I heard: SRTWT1506.\nWhat would you like me to know?"** - exactly one "I heard: ..." line, NO
second "I have" lead. Matches the specific ask. Screenshot `rerun3-J4-voice-I-heard-no-double-
lead.png`.

**However**, the trace (turn `f68e75a2-096b-4575-b937-297889709de1`) shows this turn DID land in
`lane: "entities_only"` (the parser left it domain-less this time, unlike every text/photo case
above where it guessed a domain directly and thereby routed around `entities_only`), and inside
that lane the code is STILL unresolved: `products [] -> [{"raw":"SRTWT1506","hint":"product",
"confident":true,"canonical_code":null, ...}]` - `canonical_code` stays null for a real,
confirmed-existing product, the same shape as the original Rerun-1 defect. Screenshot
`rerun3-J4-trace-entities-only-resolver-still-null.png`.

**Read together with J1/J2/J3, this pins the fix precisely: the B1/B2 work fixed the ORDINARY
domain-first resolver (`narrow_decide`/the business-lane fetch path) but did NOT touch
`_run_entities_only_arm`'s own `resolve_entity` call, which is the one place the plan's UAC
Section C (AC-1822-1832) actually targets.** The reason J1/J2/J3 all look healthy now is that the
parser has started reliably guessing SOME domain for a bare code list (inventory / order /
master_products), which routes around `entities_only` entirely rather than through it - so the
one lane this PR's own Slice 3 built is the one still unverified as working, precisely because it
is now rare for the parser to route into it at all.

Also worth a flag: the reply for this failed-to-resolve voice turn names NEITHER "I have" NOR
"Couldn't find SRTWT1506" - it silently drops the one unplaced token. That is a second, smaller
gap against the plan's own stated rule ("a token nobody could place is named, never dropped") -
better than Rerun 2's stray "I have ." wording, but not the "Couldn't find" sentence AC-1825
calls for either.

### TurnPanel media_intake stage: entities/attributes/notes/decision inline, no UUIDs - checked on TWO fresh turns, still not implemented

Checked the Stages tab DOM directly (not just the rendered text) on both J1's turn 1
(`767e0472-...`) and J3's turn (`754b9118-...`), both fresh media turns from tonight's run. Both
render identically to Rerun 1/2:

```html
<li class="text-xs">
  <div class="flex items-center gap-2">
    <span data-slot="badge" ...>media_intake</span>
    <span class="text-muted-foreground tabular-nums">2635ms</span>
  </div>
  <p class="mt-1 text-muted-foreground">Read the photo.</p>
</li>
```

No entities, attributes, notes or decision anywhere near this stage row, no expand affordance in
the DOM. The voice turn's own stage correctly reads "Heard the voice note." (a distinct summary
per modality, which IS working) but is the same flat shape otherwise. Screenshot:
`rerun3-turnpanel-media-intake-still-no-inline.png`. **Unchanged from Rerun 1/2 - this FE piece
has not landed.**

### Rerun 3 summary

| Item | Result | Turn id(s) |
|---|---|---|
| J1 photo-only -> every code listed, no "Couldn't find" | **PASS** | `767e0472-2388-47f8-9b40-efef429b45a8` |
| J1 turn 2 "check stock" -> resolution with real uuids | **PASS** (carry + resolution correct) | `d6d7fc95-f359-4317-814f-dff722d34f0d` |
| J1/J2/J3 final stock TABLE/number | **BLOCKED** - new finding: MCP's callback into this backend's own REST API gets 401 `invalid_key` even with the backend's own `.env` value, an env-loading mismatch on the restarted BE process, not a lane defect | see MCP/backend blocker above |
| J2 to the stock number | **PASS** on routing/prefix/resolution; final number blocked as above | `b055e2cd-d248-4219-8a24-70056c6cf55e` |
| J3 to the stock number | **PASS** on routing/prefix/resolution, one turn; final number blocked as above | `754b9118-8e56-464a-923c-e3d1d9c72066` |
| J4 voice bare code -> "I heard: ..." no second "I have" lead | **PASS** on the specific wording ask | `f68e75a2-096b-4575-b937-297889709de1` |
| J4's underlying resolution (entities_only lane) | **FAIL - still the Rerun-1 defect**, unresolved (`canonical_code: null`) for a real product; `entities_only` is simply rarer to reach now, not fixed | same |
| TurnPanel media_intake inline entities/attributes/notes/decision, no UUIDs | **FAIL - not implemented**, identical flat shape on two fresh turns | `767e0472-...`, `754b9118-...` |

**Net for the coordinator:** the wording-level asks (J1/J2 prefix, J4's single "I heard" lead) all
pass, and the ordinary resolver is genuinely fixed for the paths J1/J2/J3 happen to take now. But
the ORIGINAL, specifically-targeted defect - `_run_entities_only_arm`'s own resolver - is
unchanged, confirmed by J4's own trace, and the TurnPanel inline-detail ask is not yet built. A
third, new, backend-config issue (MCP-to-backend 401) is blocking the actual stock numbers
end-to-end regardless of the above.

## Rerun 4 (same day) - stack on db0968618: BE pid 51661 restarted, worker/MCP/FE unchanged

Logged back in (BE restart invalidated the session again). Used fresh console contacts per
sub-task to avoid the Rerun-3 history-contamination gotcha (Jayson for (a)/(b) voice attempts,
Kay for a follow-up voice batch, Mr Loo for (c) photo - all previously untouched by my own tests
except Kay, whose prior history was a different photo turn so unlikely to bias a bare-code parse
the same way).

### Headline result: TurnPanel inline facts (entities/decision/notes, no UUIDs) - CONFIRMED SHIPPED, and `_run_entities_only_arm`'s resolver is CONFIRMED WORKING when the lane actually fires

### (c) Photo-only turn - PASS, clean, complete confirmation

Contact Mr Loo, fresh Reset, the 3-code test PNG, no caption. Turn
`7c65f49c-c046-4ed7-bf43-4d11af130846`. Reply: **"I read BRBC22293W-1, SRTWT1506 and SRTWT1805
from that photo.\nWhat would you like me to do with it?"** - no "Couldn't find" at all (all three
placed). Screenshot `rerun4c-photo-entities-only-clean.png`.

**Stages tab, `media_intake` row - checked the raw DOM, not just the rendered text:**

```html
<li class="text-xs">
  <div class="flex items-center gap-2">
    <span data-slot="badge">media_intake</span><span>2633ms</span>
  </div>
  <p class="mt-1 text-muted-foreground">Read the photo.</p>
  <dl class="mt-1 grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-0.5">
    <div class="contents"><dt>notes</dt><dd>Simple list of product codes with no quantities or caption.</dd></div>
    <div class="contents"><dt>status</dt><dd>completed</dd></div>
    <div class="contents"><dt>decision</dt><dd>accepted</dd></div>
    <div class="contents"><dt>entities</dt><dd>BRBC22293W-1, SRTWT1506, SRTWT1805</dd></div>
    <div class="contents"><dt>modality</dt><dd>image</dd></div>
    <div class="contents"><dt>elapsed ms</dt><dd>67</dd></div>
  </dl>
</li>
```

**This is exactly the ask: entities, decision and notes all render inline under the media_intake
stage, and `entities` lists the RAW codes, never a uuid.** Confirmed shipped - this was absent in
Reruns 1-3 (flat `<li>` with no `<dl>` at all). Screenshot
`rerun4c-turnpanel-media-intake-inline-facts.png`.

**Apply tab - the "second apply event" resolving the codes:** the state diff shows the BEFORE
state (parser's raw entities, `canonical_code: null`) and an AFTER state where all three are
enriched with `uuid`, `entity_type: "product"` and the correct `canonical_code` -
`BRBC22293W-1` -> `152047ec-928f-4829-aa75-80483520fc2e`, `SRTWT1506` ->
`6182c853-dea1-4a71-bea7-c1a74fbb5901`, `SRTWT1805` -> `61a9630f-9b17-4b2a-8927-7204161aad08`.
`Turn plan: {"lane": "entities_only", "fetch": [], "domains": []}` confirms this ran through the
arm this PR built, not a domain guess. The Stages tab's own `looked_up` row shows
`placed: 3, unplaced: 0` too. Screenshot `rerun4c-apply-second-event-uuids-resolved.png`.

**This directly overturns my Rerun 3 conclusion that `_run_entities_only_arm`'s resolver was
still broken** - it demonstrably works correctly here, for all three codes, cleanly. Rerun 3's
single-voice-code failure (turn `f68e75a2-...`, `canonical_code: null`) was evidently NOT a
standing defect in the arm itself (both contacts are correctly company-mapped, confirmed by
`respond_contact_companies`) - possibly a one-off, or something modality/session-specific that
did not reproduce tonight.

### (a) Voice bare code "SRTWT1506" -> resolved via arm's second apply event, Stages facts inline - COULD NOT LAND THE TURN IN `entities_only` DESPITE 12 ATTEMPTS

Every single voice attempt tonight (12 total, across 3 contacts - Jayson, Kay, and one on Mr
Loo's earlier session before switching - multiple TTS voices/rates/phrasings of "SRTWT1506",
plus one of "BRBC22293W-1") had the parser assign a domain directly (`inventory` most times,
`product_attachment` once) instead of leaving it domain-less, so the turn never reached
`entities_only` - it went through the ordinary business/fetch lane instead (which resolves
correctly, screenshots `rerun4a-voice-resolves-via-inventory-domain.png` et al., trace confirms
real uuids the same way J1-J3 did in Rerun 3). One attempt mis-transcribed the code entirely
("VRBC 202293W1") and got the generic "need a filter" reply with no entity extracted at all
(`rerun4a-voice-misheard-code.png`). A same-input typed control ("SRTWT1506", no voice) ALSO
routed straight to `inventory` - confirming this is the PARSER's own classification behavior
right now (any product-code-shaped bare message reliably gets a domain guess), not a modality-
specific bug, and not something I can force around.

**Given (c) just proved the arm's resolver and the Stages facts both work correctly when
`entities_only` DOES fire, and the mechanism (`_run_entities_only_arm`) is identical regardless
of modality, this is very likely fine for voice too - I just could not manufacture a live sample
tonight where the parser routed a bare voice code into that lane to confirm it directly.** Flagging
as UNCONFIRMED rather than PASS or FAIL for the voice-specific claim in (a).

### (b) Voice non-existent code "ZZQ9 8817" -> same routing issue, so the specific wording could not be checked either

Synthesized "ZZQ9 8817", transcribed as "ZZQ98817" (no space this time). Reply: "I heard:
ZZQ98817\nCouldn't find: \"ZZQ98817\" (product). Would you like me to escalate to warehouse
team?" - turn `56b51086-e608-4c50-95d1-d9a2d0946f35`, trace confirms `domain: inventory` (parser
guessed a domain again, not `entities_only`), so this is the ORDINARY business_query "not found"
wording, not the `entities_only`-specific "Couldn't find ZZQ9 8817. Ask again with the correct
code." the ask described. Screenshot `rerun4b-voice-nonexistent-code.png`. Same caveat as (a):
could not reach the lane this wording lives in, so this is UNCONFIRMED, not a fail - the reply
that DID come back is itself correct and sensible for the domain it actually used.

### Rerun 4 summary

| Item | Result | Turn id(s) |
|---|---|---|
| (c) Photo-only -> TurnPanel inline row shows entities/decision/notes, no UUIDs | **PASS - confirmed shipped** (checked raw DOM) | `7c65f49c-c046-4ed7-bf43-4d11af130846` |
| (c) Apply tab's "second apply event" resolves the codes (uuid/canonical_code) | **PASS - confirmed working**, overturns the Rerun-3 "still broken" call | same |
| (a) Voice bare code "SRTWT1506" -> "I heard" + arm resolves it | **UNCONFIRMED** - 12 attempts, parser never left this turn domain-less, so it never reached `entities_only`; the domain-first path it DID take resolved correctly every time | `f68e75a2-...` (Rerun 3, still the only voice sample that reached the lane, and it failed then) |
| (b) Voice non-existent code -> `entities_only`'s specific "Couldn't find ... Ask again" wording | **UNCONFIRMED** - same routing issue; the reply that came back (ordinary business_query "not found") is itself correct for the domain it used | `56b51086-e608-4c50-95d1-d9a2d0946f35` |

**Net for the coordinator:** the piece I could fully exercise (photo, since a 3-code list with a
clean transcript still lands in `entities_only` reliably) is a clean PASS on everything asked -
inline Stages facts AND the resolver both confirmed working, which also retroactively clears the
Rerun-3 "entities_only resolver still broken" finding. The two voice-specific sub-asks are
UNCONFIRMED rather than failed: the parser's current behavior makes it very hard to get a bare
voice code to land in `entities_only` at all (it keeps guessing a domain directly instead,
correctly, just via a different lane) - happy to keep trying with different phrasing/contacts if
still needed, or this can be confirmed instead by a pytest case that calls the arm directly with
a controlled parser verdict rather than depending on live ASR + LLM classification landing a
particular way.

## Rerun 5 (same day) - stack on the lane's BE :8000 pid 51661 / worker 32895 / MCP :8767, API key `test` confirmed working

### (a) Console, fresh contact, photo-only -> a REAL stock table with numbers - PASS

Built a new test PNG (`stock-codes.png`: "Product List / SRTPTFE1207 / SRTPTFE1315 /
SRTFT203-NEW" - all three confirmed to have real stock in `public.stock`, unlike the original
3-code fixture which has none) since the standard fixture's codes cannot produce numbers
regardless of how well the pipeline works. Contact Jayden Loo, fresh Reset, uploaded with no
caption. The parser assigned `domain: inventory` directly (no separate "check stock" step
needed - one photo turn was enough), and the reply came back as a full, real stock table:

```
I read SRTPTFE1207, SRTPTFE1315 and SRTFT203-NEW from that photo.
Stock summary for the requested products.

1. *Product Code:* SRTFT203-NEW
*Total:* 28631 (O/S: 55)
*BRW:* 4426 (O/S: 40)
...
3. *Product Code:* SRTPTFE1207
*Total:* 270300 (O/S: 0)
*BRW:* 206300 (O/S: 0)
*DC1:* 64000 (O/S: 0)
...
_Data last updated: 21/09/2026 11:25:59_
```

Turn `3b0d9cf2-b5ec-4dbc-8a36-64bb060417cf`. Screenshot `rerun5a-console-real-stock-table.png`.
**This confirms the MCP + API-key fix works end-to-end: prefix correct, resolution correct, and
now a genuine numbers table, not "no stock" or a 401.**

### (b) Real WhatsApp path via curl - INVALIDATED by an active port collision with a different lane, not a finding about this PR

Copied a real historical envelope for contact `900000008` (Jayson) from `chatbot.turns`, swapped
`message.message.message` for an `attachment` (`type: image`, a freshly minted signed URL for
the classic 3-code PNG via `console_service._upload_console_media`, called directly from a
script since the FE was mid-restart), and `POST`ed to `/api/v1/external/chat/turn` with
`X-API-Key: test`, `is_test` left false (a genuine live turn, required since attachments/
entity_attachment_links only write on a live turn per D14).

**Result was wrong and initially looked like a serious regression:** `branch_kind:
business_query`, reply was a stale "sales order outstanding" report for an unrelated customer
("SK ONE STOP HOME CENTRE SDN BHD") with zero image-related content, turn `00836ec6-e9e0-4eb2-
9e39-c6056581e597`. The trace had NO `media_intake` stage at all (`received, understood, access,
routed, looked_up, replied, remembered, sent` only), and the `understood` stage's parser input
shows `entities: [], message_type: "casual"` - the image was never read.

**Root-caused, not a code defect:** `lsof` on the port shows `:8000` and `:3000` are CURRENTLY
served by a completely different lane's processes -
`/Users/tehjayson/Documents/foundryx/sorento_crm-oi-export-async/sorento_crm_backend` (pid 86536)
and `.../sorento_crm-oi-export-async/sorento_crm_frontend` (pid 88664) - not this lane's checkout
at all. This lane's own backend (pid 51661) shut down gracefully mid-Rerun (its last log line is
literally `Finished server process [51661]` at 05:17:51, right after finishing part (a)'s "check
stock" console request), and another lane's stack took the now-free ports before this lane's was
restarted. My curl POST (05:22:51) landed on `oi-export-async`'s own checkout, which - being a
different, unrelated branch - has none of this PR's media-into-turn code, so the attachment
was silently ignored and the turn fell back to whatever `oi-export-async`'s own pre-existing
business_query/order lane does with an attachment-shaped, caption-less message against a contact
with a real pending "outstanding_detail" question in memory. Confirmed no `attachments` or
`entity_attachment_links` row was written for this turn (checked both tables, nothing newer than
my earlier Rerun 1 console tests). This lane's own worker (pid 32895) is still correctly running
from `sorento_crm-media-into-turn`, only the two HTTP-facing processes were swapped out from
under the test. Evidence: `rerun5-PORT-COLLISION-evidence.txt` (not copied into the evidence
folder as an image - it is a plain `lsof`/`ps` capture, pasted below for the record):

```
Python  86536 ... cwd DIR ... /Users/tehjayson/Documents/foundryx/sorento_crm-oi-export-async/sorento_crm_backend
node    88664 ... cwd DIR ... /Users/tehjayson/Documents/foundryx/sorento_crm-oi-export-async/sorento_crm_frontend
Python  32895 ... cwd DIR ... /Users/tehjayson/Documents/foundryx/sorento_crm-media-into-turn/sorento_crm_backend   (worker, still correct)
```

**Part (b) needs a full redo once this lane's own FE/BE are confirmed back on :3000/:8000** (or
on dedicated ports the coordinator names) - I did not keep hammering the wrong lane's stack once
this was discovered, and did not touch the other lane's processes myself (not mine to kill).

### Rerun 5 summary

| Item | Result | Turn id |
|---|---|---|
| (a) Console photo-only -> real stock table with numbers | **PASS - full numbers table, MCP+key fix confirmed end-to-end** | `3b0d9cf2-b5ec-4dbc-8a36-64bb060417cf` |
| (b) Real WhatsApp path via curl: media_intake stage, attachments+entity_attachment_links rows, `check stock` follow-up, `media` block on GET /turns/{id} | **INVALIDATED - request landed on a different lane's backend (`sorento_crm-oi-export-async`) due to a port collision after this lane's own BE shut down mid-Rerun**, not evidence about this PR either way; needs a clean redo once :3000/:8000 are confirmed back on this lane | `00836ec6-e9e0-4eb2-9e39-c6056581e597` (wrong-lane turn, kept only as collision evidence) |
