# PLAN: PV2 redesign - typed refs, answer register, subject stack (14 Oct 2026)

Status: Build, R-GAPS-b (R1 merged 941fb867a #1670; R2 merged 0690ef90a #1673; R-GAPS-a merged 8ae511217 #1676;
R-GAPS-b on branch `crew/pv2-redesign-rgaps-b`).
Round order: R1, R2, R-GAPS (crew 14 Oct), R3, R4, R5.
Earlier: round R1 (owner GO 14 Oct on crew/state/reports/PV2-REDESIGN-design-14oct.md; LEAD lane PV2-FIX;
branch `crew/pv2-redesign-r1` from crew/pv2 c56a5d34f; PRs into crew/pv2). Owner 14 Oct 18:35: NO CAP on LLM spend for
PV2; R2, R-GAPS and R3 stay code + tests, live checks on the PROD prompt version (on a copy) start after R4 (see R5).
pytest -n 1.
UAC: `pv2-redesign-14oct-acceptance-criteria.md` (beside this file).

## Why
Owner and crew failure analysis (14 Oct, 29 failing PV2 turns): 14 of 15 model-side failures were act decisions, and
8 were caused by our own prompt rule lines. PV2 cannot point at what a reply printed ("this product" after an order,
"the second one", "the one before this") because nothing records what was printed. The redesign records it (answer
register), keeps earlier subjects (subject stack), lets the parser POINT instead of choosing an act, and lets code
derive the move. Still one parser call per turn; replies stay code templates.

## Owner rules for this redesign
- No prompt line for particles ('mou', 'ada tak', '有冇'): too much teaching.
- New entity type = a registry row (`chatbot_entity_kinds`), never a prompt rule or a hard-coded table.
- Act stays one eval cycle as a shadow field (compare old act vs derived move) before it is removed.
- Queued ruling (14 Oct): a non-staff PO list needs no `purchase_orders.placed` grant; PV2 matches v1.

## Rounds (each = one PR into crew/pv2: plan/UAC delta, red tests, fix, kill proof, reviewer + security)
- **R1 Answer register writer (design risk 1).** State only; MEMORY unchanged, so live parser behaviour is unchanged
  and no eval is needed to merge. Plus the PO-gate ruling.
- **R2 Subject stack + PendingQuestion.origin.** Held -> S1, topics -> S2-S5 (same rendering as today's T-keys until R4);
  `origin` keeps the full original ask (replaces r8a `deferred`). Backward-compatible load of stored state.
- **R1 review carry-overs into R2:** (a) only the main business `_run_answer` path writes the register; other answer
  paths and the stock-ask render keep the previous entries, so cover them before R4 reads the register; (b) a test for
  popping order `lines` when the Products field is hidden (fetch.py output_structurer); (c) `printed_rows` indexes
  the unfiltered `answers` while `envelope_of` drops non-dict answers from `figures`: align the two (security nit).
- **R-GAPS Frozen v1 lanes as PV2 acceptance cases (after R2, before R3/R4).** Owner rule 14 Oct: every real scenario
  behind a frozen v1 chatbot PR is a PV2 acceptance case; a scenario PV2 does not handle naturally is a PV2 fail.
  Source: crew/state/reports/PV2-FROZEN-LANES-coverage-14oct.md (9 of 10 real; PV2 fully handles none). Code-level tests
  from handwritten parses only (no LLM), red first, then fix, kill proof, reviewer + security. Cases in UAC RG-1..RG-7;
  design facts (file:line) are mapped at round start. If the diff passes ~600 lines it splits into R-GAPS-a/b PRs.
  - #1570 CLARIFY-POLICY: carried + new subject -> clarify (owner 11 Oct); PV2 table case-072
    (converted_replay.jsonl:115) expects the opposite and is corrected; staff clarify loop cap.
  - #1582 SPEC-PROMO: no Length/Width axis question (owner 12 Oct); "Sizes on file". test_resolve_specs.py:120 pin
    changes. The prompt_v2.txt:169-172 example is a prompt edit (live parser behaviour): held for R4/R5, not R-GAPS.
  - #1603 CHAT-SPARSE: "yes" to a v1 escalate offer routes with the asked text (test_fix_r2f.py:63 pin flips).
  - #1617 QTY-CARRIED: photo, then a bare count with no open question -> stock for that count.
  - #1443 COMBO-STOCK: set codes (sets are not in gate.py:85 / resolve.py:570-631 today).
  - #1517 ROGUE: an empty ask is a question, not a lookup; lists never show unsearchable or inactive rows.
  - #1572 REPORTS-STEP2: outstanding by channel keeps the product; a stale subject never rides a report chain.
  - Found in R2 review (pre-R2 behaviour, kept for parity in R2): a pick on one of several same-kind items drops the
    others ("5 A1 and 3 mou", pick 1 -> only SRTWC286; brand "sorento + moc", pick 1 -> only mocha).
  - Found in R2 review (pre-R2 behaviour, kept): a which_held pick drops a remove of a HELD item asked beside a "this
    one" (RG-7c).
  - Not in R-GAPS: #1613 seat-cover peer links (-> R3, relation kinds in the registry; the parser field is R4);
    #1625 idea capture (after #1625 merges); #1624 Gemini (only if the owner picks Gemini).
  - R5 live list from the coverage file: cand-0230/0220 replays; "HAVE 10??" after a photo; "product" alone;
    "srtwc286-250"; seat-cover fit EN/MS/ZH; the owner's 13 Oct report conversation; problem message -> idea capture;
    Gemini probe only if chosen.
- **R3 Entity-kind registry columns.** Migration on `chatbot_entity_kinds`: parent_kinds, child_kinds, domains,
  default_domain, is_document, localized labels. Move the hard-coded tables (pv2/resolve.py:217-237 kind map,
  clarify_copy.py `_KIND_WORD`, bridge.py `V1_HINT`, apply.py `DOMAIN_BY_FOUND_KIND`, apply.py `DOCUMENT_KINDS`,
  and R1's tool->kind map) to registry reads. Behaviour-preserving (same outputs, proven by the replay set).
  Plus peer-link relation kinds (crew 14 Oct, frozen #1613 SEAT-AS-PRODUCT): a seat cover linked to a product is a
  relation, not containment; "8152 seat cover stock" answers the linked cover codes, no link -> "No seat cover is on
  record for X." The parser's relation field is R4.
- **R4 Parser v3 schema + code-derived move.** refs[] {kind, points_back_to: held|S<n>|R<n>|R*|previous_subject|new,
  within, phrase, key, doc_number, op, quantity}, unclear[], social, escalation; act kept as shadow. MEMORY renders the
  register (R<n>, children indented) and the stack (S<n>). Code derives the move (design D rules 1-8). One generic
  prompt rule for refs. Built and tested on recorded + handwritten parses only.
- **Security rule for R2+ (security review of R1):** acting on a register entry (an SO uuid, a PO number key) goes
  back through the same grant and links checks as a typed ask; a stored key is never used directly (e.g. a PO number
  recorded from a non-staff PO list must not reach the staff-only ref_status lookup).
- **R5 Live checks + iterate (owner 14 Oct 18:35: NO CAP on LLM spend for PV2).** After R4, run live checks on the PROD
  prompt version on a copy (not only unit tests), with the PV2-EVAL lane's scenario battery + runner (built in parallel,
  test data and harness only) pulled into the rounds as it lands. Iterate on live failures until the battery passes;
  fixes go into code, schema or generic rules only (never keyword rules, never per-case prompt patches). Owner's bar for
  the hand test: very smooth across many real and improvised scenarios. Dev key only; an auth/IP error means the prod
  key was hit: stop and report. Until R4, rounds stay code + tests (no live calls needed to merge R2, R-GAPS, R3).
  Includes the R4 act-shadow comparison (old act vs derived move) and the R-GAPS live list.

## R1 design (register writer)
Facts (explorer map, 14 Oct, file:line on c56a5d34f):
- PV2 never sees v1's lane output for a Lookup: PV2 runs before the lanes (engine.py:4727), returns a verdict with no
  reply (pv2/answer.py:261-267), the lanes run (run_fetch 6777, compose 6864), and `_pv2_keep_state` (4212-4251) saves
  PV2's pre-lane state. `after_v1(rows=)` (pv2/turn.py:293,328) and `apply.record_rows` (apply.py:1368) have no callers.
- Structured printed rows exist in each fetch envelope (`figures` = items {title, fields[{key,label,value}], kind?,
  file_urls?}; `files`; `listed_products`; `lane_ask.last_result_set`; `sales_ranking_frame.shown`), built by
  output_structurer (fetch.py:3743) and envelope_of (turn_runtime.py:5478-5511).
- Gaps: SO cards are text only (so_status.py:48-56, 155-215, 356-430; ref_status.py:36); the MCP orders presenter drops
  an order's product lines except the joined `products` string (presenters.py `_orders_list` 429-473); crm_sales_report
  and the outstanding report pass no rows (fetch.py 2386-2457, 2500-2579, envelopes 2897-2920).

Build:
1. `pv2/state.py`: `RegisterEntry {id "R<n>", kind, key, label, parent: "R<n>"|None, subject: "S1", turn}` and
   `ConversationState.register: list[RegisterEntry]` (cap 20, newest reply first, only the last 2 replies kept).
   Backward-compatible load (missing field = empty).
2. `pv2/register.py` (pure): `entries_from_envelopes(envelopes, turn) -> list[RegisterEntry]`. Reads only printed,
   keyed fields (never the item title, never a field the reveal filter removed), per envelope in printed order.
   Kinds: order/DO card -> delivery_order (children: its product lines -> product); PO line -> purchase_order with its
   product as child; stock/incoming/products/attachment rows -> product; set page `listed_products` -> product;
   top selling `last_result_set` -> its entity_type; report_ask `drill_rows` / delivered breakdown rows -> the group_by
   kind (customer/product/...). Keys (code/uuid/number) are stored but never rendered. One small tool->kind map, marked
   for the R3 registry move.
3. Engine hook (engine.py ~7347, `_run_stages_body`, before `return _run_answer(...)`): when the turn is a PV2 turn
   (`media_box["pv2_state"]` present), set `register` on that state from the turn's envelopes. A PV2 direct reply
   (clarify/not-found, no lanes) writes no rows and keeps the previous reply's entries (last 2 replies).
4. Sources for the gaps: so_status `lookup`/`list_text`/`lookup_po_refs` and ref_status return (id, number, debtor)
   beside the cards and the lane carries them on the `_fixed_reply` fetch; the MCP orders presenter adds an unprinted
   `lines: [{product_code, qty}]` key; crm_sales_report breakdown rows and the delivered `_delivered_reply` pass rows.
5. PO gate ruling: PV2's `_grant_refusal` (pv2/turn.py:191, 244) no longer requires `purchase_orders.placed` for a
   non-staff PO list; v1 parity test first.
Not in R1: MEMORY rendering of the register, row picks from the register (the stale LAST ANSWER ROWS after a Lookup,
explorer note 3, is fixed in R4 with the register), the subject stack.

## R2 design (subject stack + PendingQuestion.origin + R1 carry-overs)
Facts (crew/pv2 941fb867a): `ConversationState.held: Held` + `topics: list[Held]` (newest first, `TOPICS_CAP` 5,
`push_topic` state.py:235, `topic_key` T<n> :243, rendered as TOPICS in history.py:156); `PendingQuestion.deferred`
(domain, attachment kinds, quantity) set at apply.py:161-192 and completed by `_complete_deferred` (apply.py:1242) on a
pick (:538); `RegisterEntry.subject` is the constant "S1" (R1).
Build:
1. Stable subject identity: `Held.sid` ("s<n>" from a `ConversationState.next_sid` counter) assigned when a subject
   starts; kept when the subject is pushed to topics and when it is switched back. Stack view helpers:
   `subject_label(state, sid) -> "S1".."S5" | None` (S1 = held, S2..S5 = topics newest first) and
   `subject_at(state, "S<n>") -> Held | None`. Storage stays `held` + `topics` (no rename); MEMORY unchanged (T-keys)
   until R4 renders S<n>.
2. The register records the printing subject's sid (`RegisterEntry.subject`), so an entry still names its subject after
   a new topic pushes it.
3. `PendingQuestion.origin`: the full original ask (Ask dump) wherever a question interrupts an ask (every place that
   sets `deferred` today, plus the v1 picker mirror when PV2 had sent a Lookup). A pick completes the ORIGINAL ask
   with the picked entity: domain, attachment kinds, quantity, period, specs, status and report all come from origin,
   through the normal apply path (no second copy of the merge logic). `deferred` is read only for state saved before R2.
   Re-review ruling (14 Oct, fix round 4): the full origin is replayed only where held did NOT take the ask (unclear
   act, flagged clarify, a missed ask's question, the v1 picker mirror). A question raised after `_commit` (held already
   carries the ask's period, status, specs, kinds, ops and report) replays nothing that already landed: its origin keeps
   only the items still waiting on a question, so a second ambiguous item is asked next instead of dropped. Replaying a
   committed ask re-ran specs and ops and looped (closest-product pick, remove + ambiguous customer, which_held).
   When a turn answers a pending question, the grant checks also judge the held subject (domain, report), not only
   the origin.
4. R1 carry-overs: (a) every PV2 answer path that prints rows writes the register, not only `_run_answer`, and the
   stock-ask render records what it prints; (b) a test that output_structurer pops order `lines` when Products is
   hidden; (c) `printed_rows` and `envelope_of`'s figures stay aligned when a non-dict answer is dropped.
Not in R2: rendering S<n>/R<n> in MEMORY, parser schema changes (R4), registry columns (R3).
Carried to R4: after a re-started subject, held and its topic copy share one sid (pre-R2 MEMORY keeps the copy), so
`subject_at("S3")` and `subject_label` are not inverse; the S<n> rendering must settle copies before it numbers them.

## R-GAPS design (maps 14 Oct on crew/pv2 0690ef90a; file:line there)
Two read-only maps (in-memory probes, no DB, no LLM) measured every case; ~405 app + ~900 test lines, so the round splits.
Generic rules only: structured parse fields, resolver facts and registry-shaped constants (marked for the R3 move);
never words from the user's text.

**R-GAPS-a (PV2-only conversation mechanics, PR 1):**
- RG-3 #1603: engine.py `_pv2_hand_over_turn` (9247-9256) is true for any turn with a pv2_turn event in parser_mode v2,
  so a "yes" to a v1 escalate offer (PV2 Lookup, route lookup) gets the routed reply text saved (9201-9206). Remove when
  v1 #1603 merges. test_fix_r2f.py:63-71 flips.
- RG-4 #1617: a count that names nothing (quantity set; no key, phrase or doc number) counts the held subject: on a
  new_topic it is `refers_to_held`; on refines_held/new_topic with no domain named, when the held domain does not read
  quantities (`QUANTITY_DOMAINS = {"inventory"}`, R3 registry), the domain becomes inventory (v1 rule "a quantity is a
  stock question", lead ruling 14 Oct: also after an incoming, order, PO or promotion answer, v1 parity: the only v1
  reader of a quantity is the stock demand block; the switch drops attachment kinds the new domain does not take);
  with nothing pending, answers_bot_question carrying only a count is a refine. Real shape:
  fixtures/livecheck_r5r6.jsonl:24, :50.
- RG-7 part 2 #1572: in `_new_subject_in_a_new_domain` (apply.py:240-254) a refine that opens a report on a subject
  without one, in a new domain, starts a new topic (a `refers_to_held` entity still carries); `_report_entity_filters`
  also runs when the ask itself opens a report (apply.py:692), so brands beside a new report become report filters.
  Same-dimension filters named together in one ask join (a refine of a held brand report with "sorento and mocha"
  gives both, base gave the last).
- RG-7b: options of a question raised beside a same-kind sibling that landed carry `{"op": "add", "phrase": ...}`;
  `_without_picked` drops only the entity with that phrase (all of the kind for older state); the replay treats the
  picked kind as already landed (`joins`), so remaining same-kind items land or are asked as add; every option-bearing
  entity question (ambiguous, did-you-mean, closest) carries the phrase and quantity; inventory `list_all` branches
  honour `set_kinds`.
  Pins flip: test_redesign_r2_fix2.py:169, :178; test_redesign_r2_fix4.py:223.
- RG-7c: `_Miss(entity=...)` at the raise sites (apply.py:842, 849, 863, 965) records the raiser on
  `PendingQuestion.raised_by`; a which_held pick drops the raiser, `refers_to_held` entities and same-kind non-remove
  entities, and replays the other removes (raised_by None = R2 fix 6 rule); in a pick replay a remove whose target was
  held before the pick is satisfied, never a miss; the raiser is matched on its identifying fields, not the whole dump.

**R-GAPS-b (PR 2, after a merges; rulings below):**
- RG-1 #1570: `clarify_carry_scope` when an ask (no `refers_to_held`, no report) leaves a held narrowing kind A unnamed
  and resolves a new narrowing kind B (customer + product on order and sales, from v1's policy rows): "Delivery for B:
  only A (from earlier) or all <A>s?", nothing fetched; pick 1 keeps A, pick 2 clears A and keeps the period (v1); a
  subject-less reply while it is open re-asks and merges its period into the origin. Never asked about the contact's
  own (scope-limited) customer (v1 guard). Loop cap: identical clarify questions count as misses for every clarify kind;
  at the cap staff get v1's "I still need X for Y." (no refer line, no offer). case-072
  (converted_replay.jsonl:114-115) corrected with its turn-1 resolver row; test_carry_turn.py:108-116 and
  converted_replay.jsonl:58 flip.
- RG-2 #1582: the held product ids reach `_place_number` (resolve.py:954-993); members holding the number on any mm axis
  become the held products (no axis question); none -> "No 300 mm for SRTWC286. Sizes on file: <axis> <values> mm; ..."
  (en/zh/ms, no options, no closest picks); no product held -> class products holding it on any axis, capped; a parser
  flag about a spec phrase the ask already carries is skipped (as apply.py:149-151 does for entities). Flips:
  test_resolve_specs.py:120-128, test_redesign_r2_fix4.py a1. prompt_v2.txt:169-172 example stays for R4/R5.
- RG-5 #1443: the resolver keeps a `product_set` match (resolve.py:621 drops it today): `ResolvedRef.set`, members from
  product_sets/product_set_members (company scoped, active only), expanded to member refs like a family in inventory
  (v1 SET_EXPANDING_DOMAINS); a base code whose products belong to active sets offers those sets as a pick.
- RG-6 #1517: an empty subject (no refs, specs, report, low stock, period, form) in inventory or master_products (#1517
  ruling; R3 registry) is a which-product question, no tool call; port #1517's `chat_visible` (product_service, products
  route, fetch.py, MCP catalog; MCP rebuild at deploy); `Product.is_active` on the PV2 family lists (resolve.py:564, 597).

Rulings (crew a76095395, 14 Oct): staff loop cap uses v1's line; keep the own-customer guard (carry question tested on
staff and on a dealer with a non-own customer); sets expand in inventory only, a base code offers its ACTIVE sets as one
pick for everyone, inactive sets do not answer; empty ask = question in inventory + master_products only; per-axis miss
line with "Sizes on file", no closest-product picks.

**Moved to R4:** RG-7 part 1 ("Dealer outstanding only"): no channel exists on the outstanding path (schema, bridge,
fetch, MCP, route, service); it needs a backend channel param plus a parser field (`Ask.channel`), built with R4.

## Work split (R1)
- Tester: red tests per UAC R1 (pure builder tests from recorded envelopes, engine-hook test, so_status/MCP/report row
  tests, PO-gate parity), replay fixtures from real envelopes (read-only dev DB traces).
- Coder A: pv2/state.py RegisterEntry + register, pv2/register.py builder.
- Coder B: engine hook, so_status/ref_status rows, MCP orders `lines`, report rows pass-through, PO gate.
- Reviewer + security at the end (security: register keys never rendered; reveal-filtered fields never recorded).
