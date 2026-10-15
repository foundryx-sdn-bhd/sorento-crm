# PLAN: PV2 R5 "follow-ups" - the code acts on what the parser read (15 Oct 2026)

Status: Review (slices A-F green + kill-proven, reviewer + security running; no prompt change). Track M (LEAD pattern, red-first, kill-proof). Branch `crew/pv2-redesign-r5` from 380b6cdf4 (R4a head,
PR #1691 unmerged), worktree sorento_crm-pv2-fix2. PR stacked on #1691 (base `crew/pv2-redesign-r4a`) so the diff
shows R5 only; it retargets to crew/pv2 once #1691 merges. Owner TOP priority 15 Oct (brief
crew/state/briefs/PV2-FIX-R5.md). Restarted after a context overflow: nothing of the first attempt was committed.
UAC: `pv2-r5-followups-acceptance-criteria.md` (beside this file). Parent plan: `PLAN-pv2-redesign-14oct.md`.

## Why

Crew's analysis of the R4a 3x battery (TRAIN classes only, anti-overfit rule): in most failing turns the parser read
the message right and PV2's own code dropped it. R4a let the parser SEE the printed rows; the code does not ACT on them.

## Behaviours (each tested directly from a parser output, never from battery wording or case ids)

1. Number / ordinal pick from the bot's own list ('2', 'no 2', '第二') acts on that row; the same list is never re-shown.
2. 'this product' / 'this one' after a one-item order or DO resolves to that line's product; several lines = ask with
   the real product names.
3. Account pick after the bot's confirm or account menu (e.g. 'A/C I only') is applied; the same confirm never loops.
4. Promotion copy pick (dealer copy / end-user copy) sends that copy.
5. Question kind survives a pick: an arrival / delivery-date question stays one; no fallback to 'Sales orders or
   delivery orders?' or 'need at least one filter' when the kind is already known.
6. Reply language: the typed language wins; a language-neutral reply keeps the conversation language.
7. Reply-build guard: a reply never prints a UUID or an internal id.

Owner product rules (15 Oct) still apply: a named customer carries when a product is named next; the clarify is only
product -> customer; staff get stock numbers, dealers got / no stock; promo replies send the list, never echo the code;
size ask -> size or 'none on record'; one exact option is used, a guess is a yes/no confirm with the real name.

## Code map (at 380b6cdf4)

(filled from the mapping pass; file:line at 380b6cdf4; paths under sorento_crm_backend/app/services/chatbot/)

### 1 Number / ordinal pick
- Parse: `Ask.answer.option_key` / `free_value` (pv2/schema.py:74-76, 121), `Ask.row_pick` (schema.py:120).
- State: `state.pending.options` (pv2/state.py:91-94, 132); PV2 questions keyed "1..n" (pv2/apply.py:1847-1849);
  did-you-mean list pv2/apply.py:1357-1383; a v1 roster mirrored as `v1_open_question` (pv2/turn.py:408-443).
  Answer rows: `state.last_rows` (state.py:134), written engine.py:4246 (v1) and pv2/answer.py:263 (reports), read via
  `register.shown` (pv2/register.py:203-207).
- DROPS (options list): consumer `_answer` pv2/apply.py:682-732. The bare-number branch (row_pick, or free_value "2")
  at apply.py:724-725 skips `v1_open_question` (in FREE_VALUE_PENDING apply.py:66), `clarify_did_you_mean` and
  `clarify_confirm`, falls to `_Miss` apply.py:753, `_miss` re-prints the same list (apply.py:1896-1903). A free_value
  on a v1 roster becomes `Lookup.free_value` (apply.py:736-752) and bridge.py:170 drops it (needs `picked`), so v1
  re-prints its roster. `refines_held` + `row_pick` while a question is open indexes the old LAST ANSWER ROWS, not
  `pending.options` (apply.py:483, 545). `clarify.needed` beside a pick re-asks before the ask is read
  (apply.py:162-182).
- DROPS (answer rows): only `refines_held` reaches `shown_row` (apply.py:483-490, 1295-1302); the `new_topic` branch
  (apply.py:517-533) never reads `row_pick`; `answers_bot_question` with no pending -> `_Miss` (apply.py:680-681).
  Kinds outside LABEL_KINDS (apply.py:1292: brand, product_type, sales_agent, tier) miss at apply.py:488-489 and open
  an option-less `clarify_other` (apply.py:1873-1883).
- FIX: one pick resolver. An open pending with numbered options wins over answer rows; a row_pick / bare number /
  ordinal maps to option N of the pending (every kind, v1 roster included -> its option_key, so the bridge sets
  `open_question_answer`); with no pending, a row_pick on shown rows acts whatever the act; the pick is read before a
  `clarify.needed` re-ask; out of range re-asks once. No new text matching (carry-safety guard).

### 2 'this product' after a one-item SO / DO
- Parse: product entity, `refers_to_held=true`, key / phrase null (pv2/schema.py:39-46, field :44; rule
  pv2/prompt_v2.txt:78).
- State: the line's product is never held; after a DO answer held is the DO + customer only (pv2/apply.py:1255-1258).
  DO lines reach the register (engine.py:4228-4246 -> pv2/register.py:66-70, row_kind product "in" the doc row) only
  when the "products" field is revealed; lanes/business/fetch.py:3084-3085 strips them otherwise. SO: nothing
  recorded (so_status.py:83-92 prints no product; `_lines_by_so` so_status.py:115-131 reads quantities only;
  `_register_rows` lanes/business/__init__.py:645-648 stores the SO number).
- DROP: `_apply_entities` pv2/apply.py:1088-1119 reads held products only; :1096-1097 (new topic) and :1103-1104
  (refine) raise `_Miss("which_held", nothing_held=True)`, `_miss` (apply.py:1873-1883) says "nothing is selected yet"
  listing the DO and customer (apply.py:1819-1824), not the line products.
- FIX: at answer time keep the single shown document's line product codes (printed or not) beside the document, NOT in
  the numbered shown rows (row_pick indexes printed rows only; R4a invariant). Before those raises: one line -> that
  product (resolved as `shown_row` does); several -> clarify kind product with the line codes as options. SO answers
  record their line product codes too.

### 5 Question kind survives a pick
- Copies: "Outstanding for which document? 1. Sales orders 2. Delivery orders 3. Both" lanes/business/__init__.py:414-418
  (armed :2019-2025) when turn_runtime.py:1891-1911 maps status outstanding + empty `document` to ("", "outstanding");
  "I need at least one filter" lanes/business/answer.py:3227-3234 (needs_scope), text :3310-3336.
- Parse: arrival = domain order + status outstanding (pv2/schema.py:126; prompt_v2.txt:45, 101); no document-type
  field. Kind held in `Held.status` (pv2/state.py:72, set apply.py:873-874) and `PendingQuestion.origin` (state.py:104;
  PV2 apply.py:208, 1686-1719; v1 turn.py:440-442 from last_lookup_ask apply.py:243, 1722-1729).
- DROPS: (1) bridge.py:220 passes status outstanding but never sets `verdict["document"]`, so v1 asks SO/DO once the
  pick supplies a customer. (2) v1_open_question free_value pick -> Lookup picked=None (apply.py:736-752), bridge.py:170-171
  sends no open_question_answer, v1 re-runs the held ask without the customer -> needs_scope (same seam as 1 and 4).
  (3) apply.py:517-522: a pick parsed new_topic restarts from an empty Held(), losing status and domain.
- FIX: the bridge sets the document for an outstanding ask (held DO -> DO, held SO -> SO, else both) so v1 never asks
  SO/DO; drop (2) is the shared pick fix; a new_topic parse that carries an answer for the open question is read as
  that answer (the parser's answer field wins over its act when they disagree, held kind kept).

### 3 Account pick after a confirm / account menu
- No account field: `Entity` (pv2/schema.py:39-46); bridge.py:128, :140 hard-code `"account": None` though v1 reads it
  (lanes/business/resolve_gate.py:1188-1202, engine.py:2202-2227).
- Holder: pending `clarify_confirm` (pv2/apply.py:1391-1398), its "yes" payload holds every account of the family in
  `keys` (pv2/resolve.py:804-812); the account menu `clarify_entity` lists families, not accounts (resolve.py:799-809).
- Consumer pv2/apply.py:755-838: "yes" sets every account (pv2/state.py:348-361), apply.py:824 re-applies the ask's
  entity; a no-key reply with an entity re-routes as a refine (apply.py:312).
- LOOP: resolve.py:781-812 groups by `ledger_family_key`, which strips the `[A/C I]` bracket
  (ledger_family.py:134-148), so 'A/C I' resolves back to the same family as a guess and apply.py:1163-1166 asks the
  same "Did you mean X?"; held changed, so `_repeat_is_a_miss` (apply.py:1930) never counts it.
- FIX: a reply to a family confirm / menu whose entity resolves (scoped to that family's accounts, by the resolver,
  not by new string matching) to a subset applies that subset; a family with several accounts offers its accounts as
  options; the repeat guard counts the same confirm subject whatever held did.

### 4 Promotion copy pick
- No tier / copy field in Ask (pv2/schema.py:112-127). The copy menu is v1's `tier_pick` roster
  (answer_bridge.py:615-643), mirrored as `v1_open_question` keyed by v1 position (pv2/turn.py:337-340, 428).
- Send path: apply.py:770-782 Lookup with the pick -> bridge.py:161-179, :261-264 set `open_question_answer` -> v1
  `_answer_pending` (turn/apply.py:722, engine.py:925) -> promotion lane sends.
- DROPS: (a) pv2/turn.py:420-421 refuses to mirror a roster with `answered_positions` (pinned by
  tests/chatbot/pv2/test_fix_r7f.py:79), so 'end user also' has no PENDING; (b) a free_value 'dealer copy' is lost at
  bridge.py:170, v1 asks the tier again; a non-matching key / row_pick -> apply.py:753 "Sorry, I didn't catch your
  choice"; (c) pv2/turn.py:119 mirrors only when PV2 has no pending of its own.
- FIX: mirror a roster that still has unanswered positions (the r7f pin is revisited with a reason in the red commit);
  a free_value on a v1 roster passes to v1 as `open_question_answer` text (v1's own roster matcher decides) instead
  of being dropped; a fresh v1 roster replaces a stale PV2 pending.

### 6 Reply language
- Parse: `ParserV2Output.language` en|zh|ms|mixed (pv2/schema.py:150), "language of the NEW MESSAGE"
  (pv2/prompt_v2.txt:149).
- Engine rule (R4a, typed > conversation > ms/zh parse): `_turn_language` engine.py:9806-9824 ->
  `language.for_turn` language.py:53-62 -> `choose(detected, conversation, saved)` language.py:46, writes
  `session_vars["reply_language"]`. Governs v1-lane replies only.
- DROP: PV2's own copy uses the raw parser value `clarify_copy.lang(out.language)` at pv2/answer.py:204 and
  pv2/turn.py:143, 165 (-> `reply_for`/`render` pv2/answer.py:83-90, pv2/clarify_copy.py:315). A neutral turn ('12',
  'ok', a code) parsed "en" flips an ms/zh conversation to English; "mixed" becomes en (pv2/clarify_copy.py:231-233).
  No conversation language reaches pv2/turn.py or pv2/answer.py.
- FIX: compute the turn language once with the engine rule and pass it into `run_turn_v2`; replace the three
  `clarify_copy.lang(out.language)` calls.

### 7 Reply-build guard
- Only sanitizer: `clarify_copy.safe_label` pv2/clarify_copy.py:305-312 (bare-UUID label -> "unnamed"), used by
  `render` option labels (:324), apply.py:421 `_labels`, apply.py:1394 `_confirm`.
- Unguarded: `_did_you_mean` pv2/apply.py:1352-1359 prints `payload["key"] or o.key`; `_size_miss` pv2/apply.py:1558
  prints `r.key`; report and idea text pass through unchecked (pv2/answer.py:239, 269), report keys can be customer
  UUIDs (pv2/reports.py:364-370).
- Choke point: `_pv2_direct_reply` engine.py:4303, `reply_text = str(pv2_result.direct_reply or "")` engine.py:4318,
  sent ~4362. FIX: one scrub there (a UUID-shaped token is replaced by its label when known, else removed) plus the
  label fallback to the printed code at the two unguarded builders.

## Slice order (one red commit + one fix commit each)

A. Shared pick resolver: behaviours 1, 4 and drop (2) of 5 (pending numbered options win, v1 roster free_value passes
   through, roster with unanswered positions mirrors, pick read before a clarify re-ask).
B. Behaviour 5 rest: bridge document for outstanding; new_topic carrying an answer.
C. Behaviour 3: account pick + repeat guard.
D. Behaviour 2: document line products.
E. Behaviour 6: one turn language into PV2 copy.
F. Behaviour 7: reply scrub at `_pv2_direct_reply` + unguarded builders.

## Work split

- Red tests: one TESTER subagent per behaviour group, from the UAC only, each with >= 1 fresh paraphrase not in the
  battery; commit `test(red): ...` alone, run, post red-proof.
- Fix: one CODER subagent (Opus: state-machine round) for the whole round, continued not respawned.
- Lead: kill-proof on each committed fix, touched-set run, reviewer + security-reviewer once per round.
- Commit after every behaviour (red commit, then fix commit).

## Touched-set (pv2 apply / engine changes)

All tests/chatbot/pv2/test_redesign_r*_*.py + test_rgaps_* + test_r4a_* + the three replay files + test_carry_safety.py
+ the DB-backed test_fix_r2*.py + tests/chatbot/test_promo_*.py + the new test_r5_*.py, pytest -n 2.

## Gates

Touched tests green, reviewer READY, security CLEAN. Prompt changes only if a behaviour needs one: then ONE dev-only
unlabelled version via pv2/index.publish_v2(label=None) on sorento_cagent_stack, id + md5 posted. Then
`crew report --done`; crew runs the 3x battery.

## Fix round 1 (reviewer NOT READY + security FINDINGS on ef8ea3023)

Red files: tests/chatbot/pv2/test_r5_fix1_picks.py, test_r5_fix1_lines.py (+ test_r5_b nothing-held param, lead
sign-off).
- Blocking: bridge.py:229 nothing held -> DO+SO made a dealer without `sales_orders.outstanding` see the SO refusal
  line (v1 outstanding_both, so_refused). Fix: both only with the SO grant, else DO.
- Blocking: the SO half of R5-2 had no real-path test (test_d2 used a register shape the SO card never produces).
- Should-fix: a new_topic in another domain carrying option_key "no" was swallowed as the answer (apply.py:392-403);
  the confirm repeat guard (apply.py:2102) and the promo copy pick reaching v1 were untested or weakly tested.
- Security should-fix: turn.py:247 `_judged_asks` read the parser's act, but apply converts a numbered refine into an
  answer, so the first grant check skipped it (the second check still refused before any fetch).
- Security nits taken: a UUID-shaped free_value on a v1 customer roster rode the carried-pick uuid branch
  (bridge.py:120-134, 263); SO doc_lines included fully delivered lines the SO card never prints; register.py:22
  `[ \t]*\(` quadratic and stripped every "()".
- Reviewer notes taken: stale doc_lines after a later answer with no rows; the real containing-name resolver matches
  "A/C I" to both A/C I and A/C II.
- Not taken (pre-existing, ticket owed): a non-staff contact with no customer links gets an unscoped customer search
  (resolve.py:740); R5-3 now also prints each account's name in the confirm. Numeric code typed exactly as printed
  misses on a numbered list (apply.py:864; not a regression).

## Slice G (crew answer 15 Oct to the linkless security finding: fold the PV2 side into R5)
Security-reviewer (file:line theirs): a non-staff contact with no customer links gets no customer scope in PV2
(pv2/turn.py:562, 579), so resolve.py:740 searches every active customer by partial name and prints up to 8 family
names per message; after a pick the v1 fetch runs without a customer restriction (lanes/business/__init__.py:1441).
FIX (PV2 side only): unlinked non-staff (contact_asker.classify not salesperson/staff) = empty customer scope in
PV2; the search returns nothing; PV2's v1 fetch call passes the empty scope (no rows); R5-3 ledger names only from
own linked accounts. UAC R5-8. Red-first, kill-proof, reviewer + security re-check. v1-only paths untouched (#1648).

## Fix round 2 (reviewer NOT READY on d7f3c4ddc)
Blocker: apply.py:414-420 compares a new topic's domain with held.domain, not the open question's origin domain, so
'2' parsed as new_topic in another domain on R5-2's which-product clarify picks the last answer's row (wrong product).
FIX: a number pick stays the open question's answer whatever its domain; the domain rule applies to option-key
answers and compares with the open question's origin domain. Also: pre-check and apply judge with the same held
(security nit + reviewer note); ledger_family.py:266 docstring; sharper confirm repeat-guard test.
