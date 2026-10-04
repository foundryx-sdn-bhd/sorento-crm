# Turn replay: cases pending a real live re-record

This file tracks a case (or a whole case's remaining divergences) that is a genuine
**recording-staleness** problem - the corpus was captured under an older engine/prompt
version, or the source contact's session carried state this replay's blank-seed harness
cannot reproduce - rather than a signable owner ruling (`DIVERGENCES.md`) or a plain
engine defect. Nothing here is excused by the harness; every listed case still shows as
a FAILURE in `pytest tests/chatbot/test_turn_replay.py` until it is either re-recorded
against a migrated source DB, fixed at the harness (T4), or reclassified.

Created 16 Sep 2026 (tester, this session), per the coordinator's item 3. One row per
case; a file with dozens of individually-diverging steps is one row, not one per step -
see `DIVERGENCES.md`'s triage table for the field/occurrence breakdown this list was
built from.

## Blocked on the clone-DB migration decision (tester 7's finding, unchanged this session)

Both `sorento_ai_automation_rearch` and `sorento_ai_automation_focus_full` predate the
`chatbot_rearch_*` migration chain (`UndefinedColumn` on `system_settings.
chatbot_tier_order` the moment the recorder script reads `system_settings`). Running the
full migration chain against a historical prod-copy snapshot to unblock recording is
outside tester remit without owner/coordinator sign-off. Every row below needs that
decision made first.

## #930 / #833 - 22 cases, never recorded at all (not a file, no divergence to sign)

Documented in full in `DIVERGENCES.md`'s "Console corpus, 16 Sep 2026" section
(tester 7): `#930` (`2026-09-15-answer-feedback.yaml`, 8 cases) - the contact has ZERO
turns in `sorento_ai_automation_focus_full` (the recording run was blocked by a 401 and
never executed). `#833` (`2026-09-11-attribute-first-asks.yaml`, 14 cases) - the contact
has 12 turns in that DB but none match any of the 14 case texts (unrelated probe
session). Neither file has an `expect.parser` pin, so hand-building a verdict would be
inventing one, not reconstructing it. No case file exists under `replay_turns/` for
these 22 - there is nothing for the replay gate to fail on, this is a coverage gap, not
a red test. Needs either a permanent "accepted, uncoverable" ruling or a live rerun once
#930's auth blocker is fixed.

## Named by the coordinator, checked this session

- **`owner-15sep-chain-002` step 3** (the coordinator's own example of a "chain-under-
  old-prompt... `resolved: false` on a plainly NEW question" case) - **SUPERSEDED, not
  pending.** Measured this session: the whole file (all 4 steps) is now covered by the
  outstanding-report-scope signed cluster in `DIVERGENCES.md` (every one of its
  divergences was the same crm_outstanding_report/orders_list tool switch, no
  `resolved: false` anomaly found on step 3 specifically) - `pytest tests/chatbot/
  test_turn_replay.py::test_replay[console/owner-15sep-chain-002-console-8113f96b-0d9f-
  463e-b052-4282daf04f7a.json]` passes. Left here as a note so the next tester does not
  re-search for a problem that is already resolved.
- **"4 decline cases, recorded=orders_list / actual=crm_outstanding_report" (sign,
  ruled)** - measured this session: only **1 file, 3 occurrences** match this exact
  direction (`prod_sample/escalation-declined-900000069-chain-001-no-run-id.json`,
  steps 1/4/6), not 4 files - the other 3 the coordinator's message may have been
  counting were likely already fixed by coder 8's review-fix round merged in at the
  start of this session (`7d85b68e3`). **Not signed** - see its own row below, the file
  mixes this pattern with a `branch_kind` divergence (`escalation_declined` expected,
  `business_query` actual at step 2) that is NOT obviously the same cause, so a clean
  single-reason sign would be guessing.

## Composite / cascading chains (45 files)

Every file below fails on 3+ of the 5 comparison fields across many steps - a signature
this session's shorter, single-cause files did not show (see `DIVERGENCES.md`'s two
signed clusters, each verified step-by-step to be ONE root cause only). These mix the
already-ruled outstanding-report/narrowing-question patterns with the diagnosed-but-
unfixed T4 gap (`DIVERGENCES.md`, "T4 diagnosed" section) AND, on top of that, ordinary
session-state cascade (once one step in a chain diverges, every later step compares
against a chain-state the recorded run never actually produced - `test_turn_replay.py`'s
own docstring: "chain state carries step to step via `session_patch`"). Untangling any
one of these by inspection risks over- or under-signing; the honest path is a live
rerun (or, short of that, a full re-triage AFTER T4 lands, since T4 alone should remove
a large but currently unmeasurable fraction of each file's divergences before any
remaining ones are worth individually diagnosing).

`console/owner-15sep-chain-{001,003,004,005,006,007,009,012,015,016,017,018,019,020}`,
`console/case-{008,010,012,015,026,030,033,047}`,
`console/focus-{002,004,005,006,008,012,013}`,
`console/handbuilt-rp-001`, `console/handpass1-{002,003,004}`,
`prod_sample/business-query-{900000008,900000052,900000010,900000056,900000072,
900000053,900000060,900000101,900000102,900000039,900000088}`,
`prod_sample/check-promotion-{900000026,900000086,900000008,900000061,900000091,
900000072,900000053,900000060,900000039}`,
`prod_sample/clarify-menu-900000008`,
`prod_sample/demand-qty-{900000017,900000040}`,
`prod_sample/escalation-declined-{900000055,900000085,900000010,900000079,900000077,
900000069,900000056,900000053,900000094,900000083,900000093,900000068}`,
`prod_sample/low-signal-{900000070,900000055,900000017,900000080,900000060,900000059,
900000057,900000068,900000039}`,
`prod_sample/offer-hold-900000055`,
`prod_sample/out-of-scope-{900000055,900000081,900000089,900000008,900000052,900000010,
900000077,900000063,900000092,900000060,900000059,900000078,900000039}`,
`prod_sample/stock-denied-{900000017,900000040}`.

Not exhaustively broken down per file this session (117 remaining failing files total,
time-boxed per the coordinator's priority order - T4/T3/contract-lines still ahead in
the queue). Re-run `pytest tests/chatbot/test_turn_replay.py -q --tb=short` and re-parse
after any harness or engine change; the exact list above will shift.

## Clusters E (10, hand pass 2) and F (6, hand pass 3) - browser pass 6 is NOT a drop-in re-record source (tester 17, 17 Sep 2026, queue item 2)

The coordinator's item 2 asked for the PASS rows in `<coder wt>/documentation/plans/
chatbot/evidence/turn-rearch/browser-pass-6-18sep.md` (30 fresh turn ids, clone DB
`sorento_ai_automation_rearch`, lane head 4427bb6bb) to replace the stale expected
steps in cluster F's 6 files via `chatbot_record_turn.py`, with cluster E's 10 files
re-run the same way. Investigated, not done - two blockers, both measured, neither a
tester's to route around:

1. **Browser pass 6 ran under a DIFFERENT contact than the original recordings.**
   Pass 6's own header: "Contact used throughout: CONTACT N (`+60120000510`)". The hand
   pass 2/3 recordings (tester 14/15) were all captured against contact `900000008`
   (tester 15's own handoff: "Source DB clone... contact 900000008"). Pass 6's own
   chain 4 (item 6, PO roster stamps) hit this directly: `Purchase cost for wc286` ->
   `Sorry, you are not allowed to access purchase cost (CONTACT N lacks this grant,
   unlike the owner who recorded this chain)` - forcing a "supplementary check"
   workaround with a DIFFERENT typed message for that one item. A splice that mixes
   CONTACT N's turns into a 900000008 chain crosses `respond_contacts` rows entirely -
   different session thread, different access grants, no shared `session_patch`
   history - not a like-for-like field swap.
2. **The replay harness seeds each chain's SOURCE contact's REAL prior session
   state before step 1** (`received_session_vars`, `test_turn_replay.py`'s own T4
   mechanism) - a fresh browser `Reset` click, which every pass-6 chain started
   from, never reproduces that. Measured directly: re-running `pytest tests/chatbot/
   test_turn_replay.py -k handpass3-owner-17sep-hanlim-chinchun` at lane head
   4427bb6bb (unchanged from pass 6's own head) shows the file's actual dominant
   divergence is `pending: expected options None, got ['SRTWC286-SH-200', ...]` on
   EVERY step from 1 onward - a PRODUCT roster armed before this chain's own step 1
   even runs, carried from the source contact's real prior conversation. This is the
   pre-existing sticky-product-roster class (cluster E/F's own documented dominant
   signature, tester 15's handoff) - orthogonal to pass 6's item 2 finding (customer-
   family header dedup, which pass 6 could only observe because its OWN fresh Reset
   never carried a stuck product roster into the chain). Splicing pass 6's fresh-
   session turns into this file would not represent, let alone fix, what the file is
   actually failing on.

Both blockers point the same direction tester 15's own handoff already named for
cluster E: "a human/tester diff turn by turn against the hand-pass rulings, then a
re-record on the fixed engine - NOT a blind re-record and NOT a blind fix." Pass 6's
findings are real and valuable (items 2, 6-supplementary, 7, 8, and item 5's first
half PASS; items 1, 3, 4, 5-second-half, 9 FAIL, all with turn ids) but they answer
"does this specific behaviour work from a clean session", not "does this specific
recorded chain, with its own carried history, now replay clean" - two different
questions. Left as cluster E/F's 16 files, unsigned, un-re-recorded, for a live
turn-by-turn diff (ideally itself starting each chain from a genuine `Reset`, not a
source contact with unknown prior state) once the engine fixes pass 6 found (items 1,
3, 4, 5-second-half) land. 15 of the 16 (cluster F's v26 file is listed separately,
below, with its own more specific reason) are enumerated here so the R-A skip rule
(tester 20, 17 Sep 2026 - generalized to read every heading, not one hardcoded
section) can find them:

`console/handpass2-contact-n-incoming-picker`,
`console/handpass2-owner-17sep-golden-win`,
`console/handpass2-owner-17sep-hanlim-delivery-miss-picks`,
`console/handpass2-owner-17sep-hanlim-rpacc-sticky-pick`,
`console/handpass2-owner-17sep-incoming-stock-7445`,
`console/handpass2-owner-17sep-outstanding-do-7445-scope`,
`console/handpass2-owner-17sep-promo-tier`,
`console/handpass2-owner-17sep-purchase-cost-all`,
`console/handpass2-owner-17sep-stock-incoming`,
`console/handpass2-owner-17sep-two-domain-asks`,
`console/handpass3-contact-n-escalation-offer`,
`console/handpass3-owner-17sep-outstanding-hanlim-detail-sales-order-switch`,
`console/handpass3-owner-17sep-promo-tier`,
`console/handpass3-owner-17sep-purchase-cost-po`,
`console/handpass3-owner-17sep-stock-incoming-multipick-word-number`.

## Needs a v26 re-record (tester 16, 17 Sep 2026, coder 14's addendum)

- **`console/handpass3-owner-17sep-hanlim-chinchun-all-refinement-this-month.json`
  step 3** - the recorded verdict for "All" over the four-family customer roster
  carries `picks: "all"` with NO `broaden_axis` and NO `reference_positions` (recorded
  under the SEMANTIC_PARSER_PROMPT that still declared `answers_open_question`, v25 or
  earlier). Under the retirement's pick rule the recorded verdict therefore names no
  option at all, so the current engine correctly reads it as "not an answer" and runs
  it as itself (`low_signal`) rather than the recorded 15-ledger order list
  (`business_query`). v26 (`9f4a20b6-ce25-4c1d-a5a8-3c0d5cf26156`, coder 14's addendum,
  `parser.resolve_config` resolves to it as `production`) adds the "all with no all
  option is every number" line specifically so a live parse of "All" against the
  CURRENT prompt emits `broaden_axis: "all"`, which is what the fix is for - not
  visible on a replay of the OLD verdict. Do NOT hand-edit the recorded verdict to add
  `broaden_axis: "all"` (that derives the expectation from the engine under test,
  which is the one thing this corpus must never do) - re-record this case live against
  v26 instead. Not touched this session.

`console/handpass3-owner-17sep-hanlim-chinchun-all-refinement-this-month`.

## Harness gap: product set resolver (cluster B, 17 files, tester 17, 17 Sep 2026, queue item 5)

Every file below fails on `entity_ids` alone (recording expected the whole product
family/set, the current engine's stubs resolve exactly one). Investigated whether the
fix is a one-function stub (`resolve_product_set` returning the recorded set) per the
coordinator's instruction - it is NOT: `resolve_product_set`
(`app/services/product_predicate_service.py:517`) takes a structured `require`/`specs`/
`free_terms`/`scope_terms` argument shape and queries the DB directly (this file's own
module docstring already names this as a "Known gap, not solved here, flagged to the
captain" - a prior tester's own finding, re-confirmed unchanged this session). A stub
here is not one function returning one recorded value the way `resolve_entity`'s stub
is - it needs a per-case dispatch keyed on the call's `require`/`specs` shape, the same
scale of work as the 4 existing stubs combined, and real seeded catalogue rows besides
(this lane's private schema holds no product catalogue by design - CI's database has no
data). Out of mechanical-fix scope; needs either real seeded rows per family (a
significant corpus-seeding project) or a genuine `resolve_product_set` stub keyed the
same way `_install_stubs` keys the other four seams - a coder/captain scope call.
`case-045`'s entry below also carries a second, already-fixed divergence (its
growth_r1 branch_kind/tools regression, see DIVERGENCES.md's cluster D section) - only
its entity_ids gap remains live. Seven of the stems below (`case-002` onward) were
added after signing cluster A's `tools`/`pending` fields (DIVERGENCES.md, "Cluster A
signed" section) exposed the SAME entity_ids-only gap underneath, unrelated to cluster
A's own ladder-fanout/settled-carry story. Listed here with full path prefixes
(tester 20, 17 Sep 2026) so the generalized R-A skip rule can read the whole list as
one paragraph, not two:

`console/case-001-three-codes-the-third-has-neither-stock-nor-incoming`,
`console/case-002-a-container-number-is-named-never-its-uuid`,
`console/case-007-a-document-ask-with-nothing-to-narrow-by-is-refused-not-listed`,
`console/case-016-a-bare-product-code-under-a-stock-thread-answers-stock-not-incoming`,
`console/case-018-a-code-with-no-stock-is-named-before-the-incoming-block`,
`console/case-019-a1-spec-ask-shows-the-compact-specs-line`,
`console/case-020-a1-one-key-spec-ask-answers-that-key-only`,
`console/case-021-item-8-list-price-of-a-product-reaches-the-base-list-price-field`,
`console/case-023-item-8-seat-cover-material-reaches-the-key-that-contains-it`,
`console/case-024-e2-catalog-sorento-is-a-resource-attachment-ask-not-promotion`,
`console/case-037-a6-the-same-question-in-chinese`,
`console/case-044-owner-8-sep-stock-then-po-for-another-code-then-last-in`,
`console/case-045-owner-8-sep-a-delivery-word-plus-a-name-over-an-escalate-offer-is-an-order-ask`,
`console/case-051-finding-1-a-product-with-many-specs-lists-all-of-them-no-more`,
`console/case-052-finding-2-a-stock-ask-with-incoming-rows-never-claims-an-attachment-send`,
`console/case-053-ac-32a-default-contact-asking-for-m218-s-last-purchase-cost-is-denied`,
`console/case-054-ac-32b-granted-contact-gets-the-last-purchase-cost-answer`,
`console/case-055-ac-32c-family-ask-returns-one-row-per-member-per-location`,
`console/case-056-ac-30-last-in-for-a-family-names-every-member`,
`contract/line-001-stock-by-location`,
`prod_sample/business-query-900000061-chain-001-no-run-id`,
`prod_sample/business-query-900000059-chain-001-no-run-id`,
`prod_sample/out-of-scope-900000071-chain-001-no-run-id`,
`prod_sample/out-of-scope-900000061-chain-001-no-run-id`.

Re-measured this session (unchanged from tester 15's original clustering): 16 files
still fail on entity_ids alone; case-045 additionally confirmed clean on
branch_kind/tools (its growth_r1 regression is fixed, only this entity_ids divergence
remains).

## Not pending: case-011 and focus-003 (tester 17, 17 Sep 2026, queue item 4)

Two DIFFERENT test_turn_replay.py failures were checked against this file's clustering
and do NOT belong here - both are genuine, unsigned, already-tracked ENGINE DEFECTS
(tester 17's classification table), not a recording-staleness problem, so they stay a
live FAILURE per AC-1591 rather than being swept into this file's skip list:
`console/case-011-an-out-of-range-tier-pick-keeps-the-product-in-scope` (already-
tracked Finding 9/Row 1 tier-pick gap - step 2 `branch_kind` resolves to
`clarify_menu` instead of the recorded `check_promotion`) and
`console/focus-003-c-roster-survives-a-declined-escalate-offer-ac-1015-ac-1017` (a
plain "no" over a `team_pick` pending falls through to `business_query` instead of
`escalation_declined` - step 3 `branch_kind`, the opposite direction from the "no" fix
signed on `case-069`). Neither is listed in any list-paragraph above, and neither
should be - this heading is a marker for the next tester, not a pending entry.

## Not currently a failure, staleness owed: case-038, case-039 and case-025 turn 0 only (coder, fix rounds 2-5, 23 Sep 2026)

`console/case-038-a7-no-stock-no-incoming-but-po-is-placed.json` and `console/case-039-
a7-nothing-on-any-rung-says-so-and-offers-to-escalate.json` both recorded `escalate to
purchasing team?` as their turn's escalate offer; owner ruling R6 (22 Sep 2026,
`PLAN-escalation-quote-title-and-stock-team-22sep.md`) now renders `escalate to
warehouse team?` for the same stock-origin (`domain_hint: inventory`) shape. Neither
case is a live `pytest` failure today - their `expected.text` carries no `_pin_text`
flag, so `test_turn_replay.py`'s own text comparison never runs for them (measured:
`pytest tests/chatbot/test_turn_replay.py -k "case-038 or case-039"` passes clean on
both R6 commits). Listed here rather than left silent so a future `_pin_text: true`
re-capture of either file records `warehouse`, not the now-superseded `purchasing` -
re-record owed, not urgent.

`console/case-025-d7-an-incoming-ask-on-a-zero-stock-code-climbs-to-the-po-rung.json`
(2 turns; added to this note in fix round 3, corrected in fix round 4, resolved in
fix round 5 once owner ruling R9 landed, re-measured against S10 in fix round 6):
**turn 0** (`domain_hint: inventory`, a plain stock ask) recorded `escalate to
purchasing team?` - under R6/R7 this turn says `escalate to warehouse team?`, stale
the same way case-038/039 are; still stale, R9/S10 do not touch turn 0 (no open
offer precedes it). **Turn 1** (`domain_hint: incoming`, D7's own climb, NOT an
acceptance of turn 0's offer) is UNCHANGED, not stale: measured directly on HEAD,
turn 1 renders `escalate to purchasing team?`, exactly the recorded value - turn 1
is a fresh question (no `is_affirmative`, no escalation confirmation, no pick
landing on turn 0's open offer), so `apply()` never calls it an acceptance and
`engine.py` passes `accepted_team=None` into `lane_parse_output`, which falls
through to the domain fill (`incoming` -> `purchasing`) - the same shape the UAC's
AC-EQ-20 pins directly. Neither turn is a live `pytest` failure today (`_pin_text`
absent on both, measured); re-record owed for turn 0's own text only, not urgent.

## Not currently a failure, staleness owed: owner-15sep-chain-006 and chain-018 still carry the D14 placeholder (coder, PR #1182 fix round 1, 24 Sep 2026)

`console/owner-15sep-chain-006-console-check-1789442546.json` and `console/owner-
15sep-chain-018-console-check-1789444993.json` each have an ideate step whose
`expected.text` is still the pre-#1179 placeholder, `"[dry-run: ideation reply not
generated]"`. Neither is a live `pytest` failure today: both stems are already
covered by the "Composite / cascading chains" bucket above
(`owner-15sep-chain-{...,006,...,018,...}`), so `test_turn_replay.py` skips them
before the text comparison ever runs, and `expected.text` carries no `_pin_text`
flag on either file regardless. `console/case-005-an-idea-reaches-the-ideate-
lane.json` (this same fix round) shows what the placeholder becomes once the case
is actually re-recorded against the real `crm_ideation_turn` intake: a
`tool_results` entry for the tool plus the real `reply_text` - not a
`DIVERGENCES.md` entry, since the re-recorded case passes clean with no mismatch
to excuse. When either chain is eventually pulled out of the composite
bucket for its own re-record (T4/cascade work, not this round's scope), its ideate
step's placeholder line is owed the same treatment - re-record owed, not urgent.
