# PLAN - Per-contact accessible brands (CONTACT-BRAND-SCOPE)

Status: in review, owner hand test PASS 4 Oct 2026 (PR #1469); forecast rule (b) confirmed by owner 4 Oct. Track: standard, size L (migration + scoping boundary;
security-reviewer runs). Card: `CARD-contact-brand-scope-4oct.md` (owner answers Q1-Q5 = a).
UAC: `contact-brand-scope-4oct-acceptance-criteria.md`. Branch `crew/contact-brand-scope`.

Paths relative to `sorento_crm_backend/` unless prefixed `FE/` (= `sorento_crm_frontend/`).

## Slice 1 - schema + one reader

- Migration `cbs_0001_contact_brand_ids` (id <= 32 chars), down_revision = current head
  (`merge_03oct_join6` on 6bc6e2395): `ALTER TABLE respond_contacts ADD COLUMN brand_ids uuid[]
  NULL`. Model column on `RespondContact` (`app/models/access.py:230`).
- `app/services/contact_brand_scope.py` (core; never imports `app.services.chatbot`):
  - `contact_brand_scope(db, contact_id) -> frozenset[str] | None` - None = unscoped (NULL or
    empty). `contact_id` is the INTERNAL id; a Respond.io id resolves via
    `field_access.resolve_contact_with_null_workspace_fallback` first (same as
    `contact_customer_scope.py`).
  - `brand_predicate(scope, brand_col)` -> `brand_col IN scope` (NULL brand fails it, Q1).
  - `out_of_scope_product_codes(db, scope, codes) -> set[str]` for the output guard (codes whose
    product brand is NULL or not in scope; unknown codes are left alone).

## Slice 2 - enforcement (three layers, AC-7..AC-17)

a. **Session criterion.** `session.info["brand_scope"]` (helpers `set_brand_scope` /
   `get_brand_scope` beside `set_company_scope`, `app/models/base.py:62`). The existing
   `do_orm_execute` listener (`app/services/company_scope.py:263`) gains: when a frozenset brand
   scope is set, add `with_loader_criteria(Product, Product.brand_id.in_(scope),
   include_aliases=True)` (concrete clause, not lambda - same caching reason as :289-296).
   Stamped at:
   - chatbot turn: `engine._scoped_factory` (`engine.py:818`), scope resolved once per turn next
     to `_contact_company_scope` (`engine.py:795`, call site ~:2630).
   - REST/MCP: `apply_company_scope` (`company_scope_resolver.py:336`) on the X-API-Key path with
     `contact_id` + `space_id` (`_resolve_api_key_scope` :234). Staff sessions are untouched.
b. **Paths the criterion cannot see** (raw SQL, Core subqueries, denormalised `product_code`):
   - entity_resolver raw tiers: `_chat_visible_product_ids` post-check (`entity_resolver.py:2747`)
     also drops out-of-scope ids (covers :2832, :2953, :3308 and `_trgm_lookup` :2887).
   - `chat_searchable_products()` (`app/models/product.py:375`) callers get the brand predicate
     when the session carries a scope.
   - order/report SQL that aggregates lines without selecting Product (sales report
     `orders.py:1778`, top selling `sales_report_service.top_selling` :599, outstanding
     `orders.py:1545`, sales analysis `sales/analysis.py:430`, low stock
     `scm/low_stock_report.py:570`, incoming `incoming_stock_service.py:180,336,352`): add the
     brand predicate where lines are summed, so totals come from in-scope lines (Q3).
   - escalation `escalation_services.py:346,252`, `dealer_stock.did_you_mean` :63.
   The coder proves each with the tester's per-path test, not by reading.
c. **Fetch output guard** in `lanes/business/fetch.py`, beside the customer `ScopeViolation`
   guard (:1193): after a tool returns, every list item (any depth) carrying `product_code` /
   `product_id` / `item_code` outside scope is dropped (`out_of_scope_product_codes`). Brand
   scope reaches fetch as `semantic_input["scope_brand_ids"]` (same carrier as
   `scope_customer_ids`, fetch.py:1197). Fails closed: a guard error drops the result.
- `BRAND_SCOPE_TREATMENT: dict[str, Literal["filtered", "no_products"]]` in
  `app/services/chatbot/contracts.py`, keyed by every tool name.

## Slice 3 - API + UI (AC-2..AC-4)

- `GET/PUT /api/v1/user-management/contacts/{id}/brands` in
  `app/api/v1/user_management/contacts.py`, modelled on market-segments (:694, :711);
  `user_management.contacts.edit` on PUT, view permission on GET; `[]` -> NULL; unknown id 422.
- Contacts list: `brands: [{id, brand_name}]` on each row (`contact_service.py:102-145`
  serializer + `RespondContactResponse`, assert in a test: response_model drops undeclared
  fields).
- FE: `FE/components/contacts/ContactBrandScopeSection.tsx` modelled on
  `FE/components/stock-visibility/StockVisibilitySection.tsx` (SearchableMultiSelect :363),
  rendered in `FE/app/(protected)/user-management/contacts/[id]/page.tsx` right after :185 in a
  `md:col-span-2` wrapper. Hook `FE/hooks/useContactBrandScope.ts`, service
  `FE/services/contactBrandScopeService.ts` (lib/api-client, extractApiError). Options from
  `/api/v1/master-data/brands/select`. Empty -> placeholder "All brands", clearable.
- List column "Brands" in `ContactsList.tsx` after access_types (:282): explicit `size`,
  `truncate` + `title`, "All" when empty, hideable.

## Tests (tester writes first, red)

Backend (`tests/chatbot/` and `tests/`; Postgres fixture `tests/_pg_fixture.py`; seed own
brands/products/contact, CI DB is empty):
1. `test_contact_brand_scope_service.py` - None for NULL / `{}`; frozenset for list; NULL-brand
   product out of scope.
2. `test_brand_scope_session_criterion.py` - with scope stamped, `db.query(Product)` and a join
   from a line table to Product return MOCHA only; without, all three.
3. `test_brand_scope_contract.py` (AC-16) - every name in `CHATBOT_READ_ONLY_TOOLS` and MCP
   `CATALOG` is in `BRAND_SCOPE_TREATMENT`; pattern of `tests/chatbot/test_customer_scope_fetch.py:106`.
4. `test_brand_scope_fetch_guard.py` (AC-17) - a `filtered` tool result with MOCHA, SORENTO,
   NULL-brand rows comes back MOCHA only; nested lists too.
5. Per path, REST via X-API-Key + `contact_id`/`space_id` (AC-8..AC-15): stock balance, products
   search, incoming/shipment products + packing list, orders + orders-by-product + outstanding
   (mixed order shows MOCHA line only, amount = MOCHA line; SORENTO-only order absent), top
   selling, low stock, sales report totals, sales analysis, resources current stock list.
6. In-process resolver (AC-8..AC-10): `resolve_reference_post` / `resolve_product_set` on a
   scoped turn session; trigram did-you-mean for a near-miss of the SORENTO code offers nothing
   SORENTO; numbered pick replay with a SORENTO uuid returns nothing.
7. Console replays (AC-19) through the turn runtime with the scoped contact: card examples 1-3;
   example 2 and 3 replies equal the reply for a nonexistent code.
8. API: GET/PUT `/contacts/{id}/brands` (422, `[]`->NULL, permission), list rows carry `brands`.
9. Unscoped regression (AC-6): same calls with an unscoped contact return all three products.

Frontend (vitest): ContactBrandScopeSection renders "All brands" when empty, saves the selected
ids, clear -> `[]`; ContactsList Brands column renders names / "All".

## Process

tester (red, commit `test(red):` alone) -> coder (green, never edits tester assertions without
captain sign-off) -> kill tests per layer -> reviewer + security-reviewer + browser pass in
parallel -> hand-test script -> PR.
