# PLAN: SEC-PROMO-ACCESS - chatbot promo files fail-open past contact access

Status: ready for merge, reviewer READY + security-reviewer READY (small fix track, security). Lane SEC-PROMO-ACCESS, PR #1719. Owner approved shipping without a hand test.

## Finding
A dealer contact holding only Sorento Dealer asked "Any promo for Cabana WC?" and got Cabana files,
including OFFICE USE and end user flyers.

Chain (verified on origin/main):
- `lanes/business/tier_gate.py` `recompose`: brand cabana unheld -> `access_levels_recomposed=[]`,
  `brand_gate_empty=True`, `tier_proceed=True`.
- `engine.py` forced fetch on `tier_proceed` (one-spec plan, no tier filter).
- `turn_runtime.make_tool_runner` runner: the Security B1 fail-closed guard fires only with a tier filter.
- `lanes/business/__init__.py` `_fetch_semantic_input` sends `access_levels=[]`.
- `sorento_crm_mcp/server.py` generated tool drops `[]` from the query (`v != []`).
- route -> `ContactAccessTypeService.translate_names_to_codes` returns None for no names ->
  `PromotionService` applies no access filter.

Prod `3d4787ce0` carries the same code on every one of these files.

## Fix (defence in depth)
1. Chatbot: `turn_runtime.promotion_access_closed` joins the runner's fail-closed guard (closed brand
   gate, or empty recomposed list). The brand gate case answers with the gate's own notice
   ("You don't have access to <brand> promotions.") as a refusal; no new wording.
2. Backend: `ContactAccessTypeService.translate_names_to_codes_for_caller`: a read on behalf of a
   contact (non-blank `contact_id`) with no usable level returns `[]` (nothing). Used by all six
   promotion routes that filter on access level. Staff callers send no `contact_id`: unchanged.
3. `answer_bridge.no_live_kind_reply` makes no list call when the contact holds no level.

Security review round 1 (both blocking, both fixed red-first):
- B1: `PromotionProductService.list_promotion_products` inlined every file of a visible promotion;
  it now filters them by the contact's codes like the promotions list and get do.
- B2: a promotion fetch with no resolver tier gate sent the parser's (customer-claimed) levels;
  the runner now builds the tier gate from the contact's real access types, closed on a read failure.

Follow-ups (not this PR): F1 miss_suggest / resolve_gate probes send parser-claimed levels (names
only, no files); F2 API-key callers omitting `contact_id` still read every level; F3 get_promotion
filters inline files only; F4 product attachments / certificates apply no access-level filter;
`no_live_kind_reply` sends no `contact_id` (company scope); the brand denial on the no-resolver path
prints the generic miss (wording only, nothing is read).

## Sweep: other contact-facing document paths
- `crm_master_product_attachments_list`, `crm_certificates_list`, `crm_resource_attachments_*`
  apply no contact access-type filter at all (not the empty-list pattern); they are gated by
  document-type grants instead. Listed as follow-up, not changed here.

## Tests
`sorento_crm_backend/tests/chatbot/test_sec_promo_access.py`: red on main for both layers, kill
tests per hunk.
