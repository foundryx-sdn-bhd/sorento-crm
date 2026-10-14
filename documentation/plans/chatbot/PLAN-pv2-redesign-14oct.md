# PLAN: PV2 redesign - typed refs, answer register, subject stack (14 Oct 2026)

Status: Build, round R1 (owner GO 14 Oct on crew/state/reports/PV2-REDESIGN-design-14oct.md; LEAD lane PV2-FIX;
branch `crew/pv2-redesign-r1` from crew/pv2 c56a5d34f; PRs into crew/pv2). CODE AND TESTS ONLY until crew relays
the owner's OK for LLM spend: no parser calls, no evals, no live checks. pytest -n 1.
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
- **R3 Entity-kind registry columns.** Migration on `chatbot_entity_kinds`: parent_kinds, child_kinds, domains,
  default_domain, is_document, localized labels. Move the hard-coded tables (pv2/resolve.py:217-237 kind map,
  clarify_copy.py `_KIND_WORD`, bridge.py `V1_HINT`, apply.py `DOMAIN_BY_FOUND_KIND`, apply.py `DOCUMENT_KINDS`,
  and R1's tool->kind map) to registry reads. Behaviour-preserving (same outputs, proven by the replay set).
- **R4 Parser v3 schema + code-derived move.** refs[] {kind, points_back_to: held|S<n>|R<n>|R*|previous_subject|new,
  within, phrase, key, doc_number, op, quantity}, unclear[], social, escalation; act kept as shadow. MEMORY renders the
  register (R<n>, children indented) and the stack (S<n>). Code derives the move (design D rules 1-8). One generic
  prompt rule for refs. Built and tested on recorded + handwritten parses only.
- **Security rule for R2+ (security review of R1):** acting on a register entry (an SO uuid, a PO number key) goes
  back through the same grant and links checks as a typed ask; a stored key is never used directly (e.g. a PO number
  recorded from a non-staff PO list must not reach the staff-only ref_status lookup).
- **R5 Eval + live (needs the owner's spend OK).** Probe set, act-shadow comparison, one dev run, crew live check.

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

## Work split (R1)
- Tester: red tests per UAC R1 (pure builder tests from recorded envelopes, engine-hook test, so_status/MCP/report row
  tests, PO-gate parity), replay fixtures from real envelopes (read-only dev DB traces).
- Coder A: pv2/state.py RegisterEntry + register, pv2/register.py builder.
- Coder B: engine hook, so_status/ref_status rows, MCP orders `lines`, report rows pass-through, PO gate.
- Reviewer + security at the end (security: register keys never rendered; reveal-filtered fields never recorded).
