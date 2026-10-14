# PLAN: PV2 redesign - typed refs, answer register, subject stack (14 Oct 2026)

Status: Build, round R2 merge-ready (R1 merged into crew/pv2 as 941fb867a via #1670; R2 = PR #1673, branch
`crew/pv2-redesign-r2`); next R-GAPS after crew merges R2.
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

## Work split (R1)
- Tester: red tests per UAC R1 (pure builder tests from recorded envelopes, engine-hook test, so_status/MCP/report row
  tests, PO-gate parity), replay fixtures from real envelopes (read-only dev DB traces).
- Coder A: pv2/state.py RegisterEntry + register, pv2/register.py builder.
- Coder B: engine hook, so_status/ref_status rows, MCP orders `lines`, report rows pass-through, PO gate.
- Reviewer + security at the end (security: register keys never rendered; reveal-filtered fields never recorded).
