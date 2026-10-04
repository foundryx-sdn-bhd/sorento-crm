# CARD - Per-contact accessible brands (CONTACT-BRAND-SCOPE)

Status: ANSWERED by owner 4 Oct 2026, all five as recommended; building (track: standard, L, migration + scoping). Plan: `PLAN-contact-brand-scope-4oct.md`, UAC: `contact-brand-scope-4oct-acceptance-criteria.md`.

Owner (4 Oct, gist): assign accessible brands to each contact; null = all brands; a scoped
contact is bound to products of those brands in ALL kinds of asking; guard very strongly;
dropdown like per-contact locations.

## What exists today (read on 6bc6e2395, data from prod copy sorento_ai_automation_0925)

- Brand lives on `products.brand_id -> brands.id` (`sorento_crm_backend/app/models/product.py:98`).
  12 brands, one company. Active products 14,909; **3,461 (23%) have NO brand**
  (`select count(*) filter (where brand_id is null) from products where is_active`).
- `Brand.access_levels` (`product.py:117`) is NOT a product filter: it only gates
  attachments/certificates/promotions (`product_predicate_service.py:193,238,319,383`) and the
  promotion fallback (`system/references.py:761`). Every brand row holds the default
  `["dealer","end_user"]`, so it gates nothing in practice. Brand scope is a new, separate rule.
- Per-contact locations = `StockVisibilityPolicy` (`app/models/access.py:803`), UI
  `components/stock-visibility/StockVisibilitySection.tsx` (SearchableMultiSelect :363) on the
  contact **Profile** tab (`user-management/contacts/[id]/page.tsx:185`). 35 of 100 contacts
  have a per-contact location row.
- Precedent for a per-contact scope: customer scope (`app/services/contact_customer_scope.py`,
  enforced in `engine._customer_scope_gate` engine.py:1688 and the fetch `ScopeViolation`
  guard `lanes/business/fetch.py:1193-1207` over `CUSTOMER_SCOPED_TOOLS`).
- Mixed-brand orders are common: 38,198 of 201,924 sales orders carry lines of 2+ brands.

## Rules (proposed)

R1. New column `respond_contacts.brand_ids uuid[] NULL`. NULL or `{}` = all brands (today,
    unchanged byte for byte). Additive migration, nullable, no backfill.
R2. One function `contact_brand_scope(db, contact_id) -> BrandScope(brand_ids | None)` in
    `app/services/contact_brand_scope.py` (core, never imports chatbot), the only reader.
R3. Enforced at three central layers, not per tool:
    a. **Session criterion**: the turn session (`engine._scoped_factory` engine.py:818) and the
       REST/MCP request session (`apply_company_scope` company_scope_resolver.py:336, which
       already reads `contact_id`) carry the brand scope in `session.info`; the existing
       `do_orm_execute` listener adds `Product.brand_id IN scope` (same mechanism as company
       scope). Covers resolver, did-you-mean, pickers, spec/price, product sets, stock, ETA,
       reports that select Product.
    b. **Raw-SQL / denormalised paths** (entity_resolver trigram/embedding tiers via
       `_chat_visible_product_ids` entity_resolver.py:2747; order/DO lines with `product_code`
       columns): explicit filter through the same function.
    c. **Fetch output guard** (defence in depth, last step before the answer, beside the
       customer `ScopeViolation` guard fetch.py:1193): every tool result row naming a product
       code outside scope is dropped before the answer is built.
R4. Out-of-scope code = unknown code. The reply is the SAME text the contact gets for a code
    that does not exist; no did-you-mean drawn from other brands; never stock, ETA, price or
    existence.
R5. Orders/DO/SO: the contact sees only in-scope lines; an order with no in-scope line is
    "not found". Reports (top selling, low stock, sales, outstanding) count only in-scope lines.
R6. Escalation summary to staff names the scoped products only (the staff side is unscoped).
R7. Regression guard: a meta-test enumerates `CHATBOT_READ_ONLY_TOOLS` (fetch.py:1338) plus the
    MCP `CATALOG` (`sorento_crm_mcp/catalog.py:56`) and fails if any tool is missing from a
    `BRAND_SCOPE_TREATMENT` map (`filtered` / `no_products`). Per-path red tests with a
    MOCHA-only contact; kill tests (remove the criterion -> red); console replays.

## UI

"Brands" `SearchableMultiSelect` card on the contact Profile tab, directly under the
Locations card (`[id]/page.tsx:185`), loading `/master-data/brands/select`. Empty = "All
brands". Pure reuse of the locations pattern, so **no mock** (owner exception for pure reuse).
Contacts list: a hideable "Brands" column (`ContactsList.tsx:215`), "All" when empty.

## Real examples (masked, prod copy)

1. Contact Shi*** (..179), access types end_user + mocha_dealer + mocha_office, set to MOCHA.
   "stock MAP5045C" (MOCHA) -> answered as today.
2. Same contact: "stock SRTWB7108" (SORENTO) -> same reply as an unknown code.
3. Same contact: "MWC7630-SH-S6" (no brand on the product row) -> see Q1.
4. Contact Sar*** (..392), cabana_office + sorento_office, set to CABANA + SORENTO: "top
   selling this month" -> CABANA and SORENTO products only; "status of SO with CKS901-NG and a
   BRAVAT line" -> CKS901-NG line only.
5. Contact with no brands set (all 100 today) -> no change anywhere.

## Edge cases

- Brand later deactivated: id stays in the list; its products still pass the scope (they are
  hidden elsewhere by is_active). Brand deleted: FK-free array, stale id simply matches nothing.
- Numbered pick / follow-up carrying a uuid from before the scope was set: re-checked at fetch
  (R3c), so a stale pick cannot leak.
- Multi-company: brands are per company (`uq_brands_company_brand_code`); today one company
  owns all 12, so ids are fine. Second company with its own MOCHA would need picking both.
- Fits #1434 ACCESS-MODEL: per-contact scope beside its `regions`, outside roles; if #1434
  lands first, the Brands card moves to its Access tab with regions.
- #1463 CONTACT-BULK-ACCESS copy set excludes stock/spec visibility today; see Q5.

## Questions (each with recommendation)

Q1. Products with NO brand (23% of active) for a scoped contact: (a) hidden, (b) visible.
    **Rec (a)**: "only products of those brands" is strict; visible would leak e.g. MWC7630-SH-S6.
Q2. Office-staff contacts (sorento/cabana/mocha office): (a) brand scope applies to them too,
    (b) exempt like customer scope (`contact_customer_scope.py:59`). **Rec (a)**: the list is
    set by hand per contact, so an admin who sets it means it.
Q3. Mixed-brand order (19% of orders): (a) show only in-scope lines, amounts summed from them,
    (b) hide the whole order if any line is out of scope. **Rec (a)**: the dealer still gets
    its own lines; (b) would hide most of a dealer's real orders.
Q4. Wording for an out-of-scope code: (a) identical to the existing unknown-code reply,
    (b) a distinct "not available to you" line. **Rec (a)**: never confirms the code exists.
Q5. Contacts list + copy access: (a) add the hideable Brands column now and ask #1463 to add
    brands to its copy set, (b) settings card only. **Rec (a)**: cheap, and brands are an
    access facet like access types.

## Owner answers (4 Oct 2026)

- Q1 (a): products with NO brand are HIDDEN for a brand-scoped contact.
- Q2 (a): brand scope applies to office staff too (owner sets it per contact).
- Q3 (a): mixed-brand orders show only in-scope lines; amounts from those lines.
- Q4 (a): out-of-scope code gets the identical unknown-code reply.
- Q5 (a): hideable Brands column on the contacts list now; crew asks #1463 to add brands to its copy set.
- No mock: pure reuse of the locations pattern.

## Owner ruling, project forecast (4 Oct 2026)

- Forecast rule (b) stands: a brand-scoped contact gets NO project forecast (all-brand totals
  would leak other brands' money). The chatbot answers with the existing not-allowed path
  (`ToolNotAllowed`); the REST route returns 403. One seam:
  `contact_brand_scope.brand_scope_allows_tool`. Option (c), per-brand project scoping, is the
  named follow-up if project brands are ever recorded per project.
