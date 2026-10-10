# PLAN: resolver probes for SO number, customer PO ref, our PO number, purchase request (REF-PROBES)

Status: Phase 2 (tester red tests). Track: M feature, LEAD pattern. No UI. Parser prompt publish
migration only (no schema change). Security review required (new read route + MCP tool).

UAC: `ref-probes-acceptance-criteria.md`. Card: `ref-probes-behaviour-card.md`.
Origin: owner 11 Oct architecture review (PR #1566, section #refres).

## Facts (base fd3eb5a90)

- DO probe only for order words: `entity_resolver.py:997`; `sales_order` aliased to it (:77).
- `so_status.lookup` misses every dashed SO number: filters `so_number IN so_key(word)`
  (`so_status.py:171-176`), `so_key` strips dashes; eval_base 48,718 of 204,170 so_numbers
  are dashed, 0 stored stripped.
- Asker grants live in `ctx["access"]["attributes"]`, tier in `state_out.profile`; both known
  at `engine.py:5188` (resolver_ctx), not inside the resolver.
- Resolver fallback reruns over `_RESOLVER_ENTITY_TYPES` (`system/references.py:49`), so new
  kinds stay OUT of it (else a dealer DO miss would probe PO/PR).
- Cross-kind picker `turn/reconcile.py:84-113`; parser hints `contracts.py:266`; gate types
  `lanes/business/gate.py:74`; SO/PO-ref arms `lanes/business/__init__.py:1432-1500`.
- Parser prompt versioned in DB: publish via alembic like `sopr_0002_parser_po_refs.py`.

## Slices (one PR)

S1 Resolver probes (`entity_resolver.py`): `_probe_sales_order`, `_probe_customer_po_ref`
   (po_ref_key fold), `_probe_purchase_order`, `_probe_purchase_request` beside
   `_probe_customer_order`; register in `_TIER1_PROBES` (:4763); drop alias :77; SO/PO into
   `_company_scoped_models` (:4316). Not in `references.py:49`.
S2 Visibility (`contracts.py`, `resolve_gate.py:672`, `engine.py:5188`): new hints
   delivery_order, sales_order, customer_po_ref, purchase_order, purchase_request;
   `REFERENCE_NARROW` (order -> any; purchase_order -> {purchase_order, customer_po_ref});
   `visible_reference_kinds(attributes, is_staff)`; body lists every visible kind when any
   reference hint is present.
S3 Dealer scope (`turn_runtime.resolve_kinds` ~:2530): drop sales_order / customer_po_ref
   matches whose customer is outside enforced scope, before the picker is built.
S4 Reconcile (`turn/reconcile.py`): named kind with an in-kind hit keeps only it; kind words
   in labels ("delivery order", "PO ref"); `narrowed_miss_lines` -> "No <kind> X.".
S5 Answers: fix dashed SO lookup; route placed sales_order -> so_numbers, customer_po_ref ->
   po_refs (staff `linked=None`), purchase_order / purchase_request -> new in-process arm
   (`chatbot/ref_status.py`) gated by staff + `purchase_orders.placed`; cards name the kind.
S6 DO header label "Order" -> "Delivery order" (`tail/scope_block.py:53`); narrowed-miss line
   prepended after compose (`engine.py` ~:6276).
S7 Parser: REF_KINDS_ADDENDUM after PO_REFS_ADDENDUM + publish migration; ALLOWED rows in
   `gate.py` for order / sales / purchase_order domains.
S8 PR read surface (brief + AC-19): `GET /procurement/purchase-requests/by-number/{number}`
   read-only, MCP ToolSpec (pattern 9a49921c9), `UNDOMAINED_CHATBOT_TOOLS` allow-list, parity
   test. Chatbot itself reads in-process (S5), like `so_status`.

## Deviations from the card (flagged to crew)

- DO kind word rides the existing header line: "Delivery order: 202610-0838".
- PO/PR need staff tier AND `purchase_orders.placed` (dealers can hold that grant for the
  ladder rung, `answer.py:1190`).

## Risk (tests likely to move)

test_so_number_ask.py, test_po_ref_ask.py, test_*kind_pick*.py, test_rearch_s2_reconciliation.py,
test_do_ask_tester_pass1.py + replay JSONs with "Order: ", test_customer_scope_lane.py,
test_rearch_r11_security_round.py, tests reading `_TIER1_PROBES`, body-equality tests of
`resolve_entity_body`.
