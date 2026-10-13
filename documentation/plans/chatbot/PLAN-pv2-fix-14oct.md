# PLAN: PV2-FIX - close the PV2 dev-eval gap (14 Oct 2026)

Status: Review (size M, LEAD pattern; base `crew/pv2` @ 923c3709b, branch `crew/pv2-fix`, PR #1646). Dev split:
run 1 79/107, run 2 88/107 (mt 29/40), run 3 88/107 (mt 28/40, must_not 0), run 4 on the review-fix head 87/107
(mt 27/40; its two new losses are parser non-determinism on identical prompt + MEMORY, PR #1646).

## Rounds (red test commit -> fix commits)

1. ef5058ade -> 33a06284d, 1a37dcd44: product candidates via v1 search, `Entity.quantity`, `Entity.doc_number`, doc/form
   resolver branch.
2. 88d318438 -> 29f08b744, 89b433efd: cross-type document fallback (v1's re-run), list misses answered like v1,
   `Ask.status` / `Ask.portal_form`, prompt misreads.
3. 97340979f -> 77c0248d7: an ask with `portal_form` answers as portal_link (dev run 2 must_not: v1 listed every form).
4. 21d8d838f -> 017fac309, f5af39225 (security review): v1's reference-kind scope for a lane with no per-match drop
   (non-staff lose sales_order / customer_po_ref), contact regions on every v1 lookup, documents exact only.
5. 8131e7f5b (+ db79d6725) -> 311bde159, 321865fdf (review): module-level resolver scope incl. customer scope,
   cross-kind hits held under the kind found, status / portal_form held, did-you-mean never auto-applied.

## Left open (crew / owner)

- A list ask whose other item raises a question drops its misses (v1 never says "not found X" after the pick).
- Candidates stop at 8 with no "and N more".
- Non-staff dealers cannot reach their own sales order through PV2 (v1's closed rule for a lane without the per-match
  customer drop, engine.py:2064-2083); a per-match drop in PV2 would lift it.
- Multi-turn media / v1-menu flows still lost on dev: cand-0161, 0258, 0259, 0306.
UAC: `pv2-fix-14oct-acceptance-criteria.md` (beside this file).

## Why

The PV2 dev gate (`crew/state/reports/PV2-FINAL-EVAL.md`) scored 60/107 against the v52 baseline's 87/107
(multi-turn 22/40 vs 28/40, good clarifications 6 vs 15). Every lost case is a "couldn't find" or a wrong
question back; none is wrong data or a permission leak. Three classes cover 30 of the 32 lost cases.

## Rules (owner rulings in force, crew brief 14 Oct)

- No word lists, regex, typo tolerance or word-overlap checks on user text in code (owner 5 Oct). Misreadings
  are fixed in the parser prompt/schema; resolution reuses existing DB lookups.
- Resolve-then-reuse: new parser fields only resolve; the reply is the existing v1 domain answer for the
  resolved entities (parity per fix: PV2 reply == v1 reply for the same resolved codes + domain).
- Candidates show real display names; never ask for a code, never show UUIDs/ids; never auto-join.
- System prompt <= ~8k tokens; index cap 8,000 chars unchanged (`pv2/index.py:36`).
- Clarify, don't guess.

Contract change approved by crew (brief, 14 Oct): CONTRACT-PV2-13oct section 1 `Entity` gains a per-item
quantity and a bare document number; section 4's "products: exact only" is relaxed to "products: v1's own
product search, candidates as options".

## Fixes

1. Product phrase not an exact code -> v1's existing product search (see "v1 paths" below). Unique hit -> ref;
   several -> a normal PendingQuestion with candidates (cap = `CUSTOMER_CANDIDATE_CAP`, labels the way v1 prints
   products); none -> v1's not-found reply.
2. Quantity -> `Entity.quantity {number, unit}` (unit as said); carried on the held product ref and sent to v1 as
   the v1 entity's `quantity` (bridge), so v1's quantity-check answer runs.
3. Doc numbers / forms -> the parser returns the bare document number separately from the doc type (entity kind);
   resolution reuses v1's doc lookups; loose form names get candidates like (1).
4. Remaining lost classes and the clarify drop: listed in the final report; only cheap in-rule fixes.

## v1 paths (file:line)

Products (v1 chain): `services.py:140-167` -> `references.py:3225 resolve_reference_post` -> `:2341 _resolve_input`
-> `entity_resolver.py:5286 resolve_references`. Tier 1 `_probe_product` (:849, dash/space-insensitive equality);
variant siblings on a tier-1 hit (:5501-5516); tier 2 `_tier2_fuzzy_lookup` (:2882) -> `_prefix_probe_product`
(:2070, prefix then substring on the stripped code, `PREFIX_LIMIT=20`); no match -> did-you-mean `alternatives`
(`_family_alternatives` :2958 then `_trgm_lookup` :3188, floor 0.30, cap 5, :5826-5873). Tier 3 embedding (:3035)
calls OpenAI and only ever yields one match: PV2 passes `enable_embedding_fallback=False`. v1 picker labels are the
product code (`lanes/business/gate.py:745-773`); v1 not-found copy `lanes/business/answer.py:4392-4396`.

Quantity: v1 reads `entities[].quantity` (`head/parser.py:171`) -> `turn_runtime.py:3230-3322 _spec_quantities`
-> `requested_quantities` (`lanes/business/fetch.py:1182-1186`). PV2 hardcodes `"quantity": None` at
`pv2/bridge.py:105`.

Documents: PV2's `_resolve_entity` returns none for every kind but product/customer/warehouse/index kinds
(`pv2/resolve.py:210-211`), so every DO / inbound / form lookup failed whatever the phrase. v1: `_probe_customer_order`
(`entity_resolver.py:1001`, `orders.order_number`, dash/space-insensitive; eval DB stores `202609-2012`,
`PS202609-0469`, `202610-0838`), `_probe_sales_order` (:1049), `_probe_inbound_shipment` (:1574), forms
`_prefix_probe_form` (:2479) / `_and_probe_form` (:4058); kind aliases :79-82. v1 prompt rule: the number without
the kind word in `raw`, the kind word as hint, a bare number keeps hint "order" (`chatbot_parser_prompt.py:936-950`);
"order" = the reference kinds the asker may see (`contracts.py:305-329`, `resolve_gate.py:582-590`).

## Work split

- Tester: red replay/unit tests from the UAC, `test(red):` commit alone.
- Coder A (resolve side): `pv2/resolve.py` product + doc + form candidates via v1 functions.
- Coder B (schema side): `pv2/schema.py`, `pv2/prompt_v2.txt`, `pv2/state.py`, `pv2/apply.py`, `pv2/bridge.py`.
- Reviewer at the end. Dev split re-run (max 4 runs) on the eval stack 8395/8396.
