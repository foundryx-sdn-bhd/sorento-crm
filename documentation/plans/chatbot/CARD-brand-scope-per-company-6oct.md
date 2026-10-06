# CARD - Brand scope applies per company (BRAND-SCOPE-PER-COMPANY)

Status: posted as `crew-ask` 6 Oct 2026; building on the recommendation while the owner answers.
Plan: `PLAN-brand-scope-per-company-6oct.md`, UAC: `brand-scope-per-company-6oct-acceptance-criteria.md`.
Parent lane: CONTACT-BRAND-SCOPE (`CARD-contact-brand-scope-4oct.md`, PR #1469, owner answers Q1-Q5 = a).

Owner ruling (6 Oct 22:20, gist): a contact with BOTH companies (SRT Sorento + MCH Mocha) is limited
to the Sorento brands (SORENTO, CABANA, ELLECI, NO LOGO) on Sorento products while seeing ALL Mocha
products; a Mocha-only contact sees all Mocha products; a Sorento-only contact gets the Sorento
brands. Mocha has no brand rows; every MCH product has `brand_id` NULL.

## The rule, in one sentence

A contact's brand list restricts only the products of a company that owns at least one brand in
that list; products of every other company pass untouched, and a NULL-brand product of a
restricted company stays hidden (Q1 of the parent card, unchanged).

## What exists today (read on origin/main cdd459b64, re-verified line by line)

- One frozenset of brand ids per session, `session.info["brand_scope"]`
  (`app/models/base.py:72-87`), stamped by the chatbot turn (`chatbot/engine.py:885-925`), the
  X-API-Key request path (`company_scope_resolver.py:252-266, 365-379`) and the two workers
  (`tasks/export_tasks.py:1138-1142`, `tasks/report_export_tasks.py:66-70`).
- The predicate is `Product.brand_id IN scope`, applied globally by the `do_orm_execute` hook
  (`services/company_scope.py:274-283`) and copied in five places in
  `services/contact_brand_scope.py` (`brand_predicate` :50-52, `out_of_scope_product_codes` :98,
  `out_of_scope_product_ids` :116, `product_in_scope_clauses` :135,
  `complaint_in_scope_clauses` :255) plus `order_service.scope_product_filter` :255. A NULL brand
  fails the IN, so every MCH product is hidden from any brand-scoped contact.
- The two kwargs sites (`api/v1/sales/analysis.py:360-364`, `api/v1/scm/low_stock_report.py:522-525`)
  hand `brand_ids=sorted(scope)` to the worker, which re-stamps the same frozenset; the hook then
  applies the predicate there. They carry brand ids only.
- `Product` and `Brand` both carry `company_id` (`models/product.py:98, 220`, CompanyScopedMixin,
  NOT NULL in Postgres since migration 305), so "which companies own the scoped brands" is a
  12-row lookup on `brands`, never stored.
- Fail-closed sentinel: `NO_BRAND_ID` (`contact_brand_scope.py:27`), a brand id nothing carries,
  is what a failed scope lookup falls back to (`engine.py:902`, `lanes/business/__init__.py:2273`).
  Under the old IN it hides every product. It must keep doing so.
- The owner's bulk script (`set_contact_brands_dbeaver.sql`, owner's machine) sets the four Sorento
  brands on SRT-only contacts (101 "will update" on the prod dry run) and SKIPS the 80 MCH and
  MCH+SRT contacts because the one-list rule would hide all their Mocha products.

## Design (recommended, option A)

The scope stays a frozenset of brand ids everywhere (no new carrier, no new column, no change to
the kwargs sites or the workers). The predicate changes in ONE function,
`contact_brand_scope.brand_predicate(scope, brand_col, company_col)`, and every copy calls it:

```
scoped_companies = SELECT company_id FROM brands WHERE id IN (scope)        -- Core table, uncorrelated
product passes  <=>  brand_id IN (scope)
                     OR (EXISTS scoped_companies AND company_id NOT IN scoped_companies)
```

- `brands` is read as the Core table with `.correlate(None)`: no company-scope criterion applies
  to it (the stored list is the truth whatever the viewer's company scope) and a query that joins
  `brands` itself cannot capture the subquery.
- `EXISTS` keeps the sentinel fail-closed: `NO_BRAND_ID` (or a list of only deleted brand ids)
  owns no company, so NO company is exempt and NO product passes, exactly as today.
- Plain `OR brand_id IS NULL` is rejected: it would also pass Sorento's 3,461 NULL-brand products.

Option B (carry a `brand_scope_companies` set beside the scope, resolved at stamp time) needs a
second value through `set_brand_scope`, both workers, the two kwargs sites and `_scoped_factory`,
for the same SQL. Rejected under "simplest thing that works".

## Real dev examples (seed of the tests; names are the test world's)

Companies SRT (Sorento, `DEFAULT_COMPANY_ID`) and MCH (Mocha). Brands SORENTO and CABANA under
SRT; none under MCH. Products: `SRT-A` (SORENTO), `SRT-B` (CABANA), `SRT-N` (SRT, no brand),
`MCH-1`, `MCH-2` (MCH, no brand).

| # | Contact | Company scope | brand_ids | Asks | Today | After |
|---|---------|---------------|-----------|------|-------|-------|
| 1 | dual | {SRT, MCH} | [SORENTO] | stock `MCH-1` | not found (NULL brand) | answered |
| 2 | dual | {SRT, MCH} | [SORENTO] | stock `SRT-A` | answered | answered |
| 3 | dual | {SRT, MCH} | [SORENTO] | stock `SRT-B` (CABANA) | not found | not found |
| 4 | dual | {SRT, MCH} | [SORENTO] | stock `SRT-N` (SRT, no brand) | not found | not found (Q1 holds) |
| 5 | Mocha-only | {MCH} | [SORENTO] (script `mch_mode=apply`) or NULL | stock `MCH-2` | not found / answered | answered either way |
| 6 | Sorento-only | {SRT} | [SORENTO] | `MCH-1`, `SRT-N` | not found | not found (company scope and Q1) |

Totals follow the same lines: a dual contact's top-selling / outstanding / sales figures include
every MCH line and only the SORENTO lines of SRT.

## Edge cases

- **A brand row appears under MCH later.** Nothing changes for a contact whose list has only SRT
  brands: MCH still owns none of its scoped brands, so every MCH product (branded or not) passes.
  The day staff ADD that MCH brand to a contact's list, MCH becomes a restricted company for that
  contact and its NULL-brand products go out (Q1). That is the list meaning what it says.
- **A product moved between companies.** The predicate reads `products.company_id` live; the rule
  follows the product the moment the row changes. Nothing is cached.
- **NO_BRAND_ID in the business lane** (`_brand_scope_ids` :2245-2274, `brand_guard.guard_result`):
  the guard's Python re-check uses the same helper, so the sentinel still drops every product
  row. The helper reads the brands' companies with company scope OFF, matching the SQL.
- **API-key path** (`_resolve_api_key_brand_scope`): unchanged; the session criterion is the same
  hook. The MCP server sends `contact_id` + `space_id` and gets the per-company rule for free.
- **Company scope None with a brand scope** (security review S2). This shape DOES exist: both
  workers (`tasks/export_tasks.py:1138`, `tasks/report_export_tasks.py:66`) and two in-request
  `company_scope(db, None)` blocks (`chatbot/lanes/escalation_services.py:297-313`,
  `certificate_service.py:1323`). The predicate does not depend on the company scope, so inside
  those blocks a brand-scoped contact sees other companies' products, which is what an unscoped
  contact already sees there. The workers are bounded elsewhere: the low-stock worker adopts
  the run's own company (or fails closed), and the report worker is limited to the
  `company_grants` snapshot of the single company the route sends.
- **A product code with no company** (complaint lines; `complaint_in_scope_clauses`,
  `in_scope_product_codes`). Codes are unique per company only, and more than 11,000 codes exist
  in both Sorento and Mocha. A code is in scope when at least one product the contact's company
  scope can see under it passes the rule AND none fails; a code shared by a passing Mocha row and
  an out-of-scope Sorento row stays out (fail closed: the complaint cannot say which product it
  meant). The output guard (`out_of_scope_product_codes`, code-only items) keeps such a code,
  because the two layers before it match on product id and already show the Mocha row; this is
  the accepted trade-off of security review A1.
- **Scope with a deleted brand id only.** Owns no company, so everything is hidden, as today.
- **A list can widen instead of narrow** (security review S1, accepted risk). A company is
  restricted only while a live brand row of the list belongs to it: a same-named brand picked
  from the other company, a listed brand later hard-deleted or moved, or a company membership
  removed, can leave a company unrestricted for that contact. Only staff with
  `user_management.contacts.edit` write the list (`set_contact_brands`), brands are staff master
  data, and no contact-facing write exists, so a contact cannot widen its own view. The PUT is
  NOT made to reject brands outside the contact's companies: that is exactly the shape the
  owner's script writes on Mocha-only contacts under Q4 (a).
- **Staff UI GET/PUT `/contacts/{id}/brands`.** Unchanged: it stores the list; it never filters
  products.

## Questions (max 5, each with a recommendation)

Q1. The rule: (a) brand list restricts only companies that own a scoped brand, others pass,
    NULL-brand of a restricted company stays out; (b) `OR brand_id IS NULL` everywhere.
    **Rec (a)**: (b) un-hides Sorento's 3,461 unbranded products, the parent card's Q1 (a).
Q2. The AC-7 tests of the parent lane ("NULL brand out" for a SRT product under a SRT brand scope):
    (a) keep them green as they are, add the cross-company tests beside them; (b) flip them.
    **Rec (a)**: under rule (a) they still describe the truth; only MCH products change.
Q3. Where the company set comes from: (a) derived in the SQL predicate from `brands` (no new
    carrier); (b) resolved once at stamp time and carried beside the scope.
    **Rec (a)**: same SQL, zero signature changes, the workers and kwargs sites stay as they are.
Q4. The owner script: (a) after this lands, `mch_mode=apply` becomes the normal path and the four
    Sorento brands are written on MCH and MCH+SRT contacts too; (b) leave MCH-only contacts NULL.
    **Rec (a)**: one uniform list per contact; a Mocha-only contact reads identically either way
    (example 5), and a contact that later gains SRT is already restricted correctly.
Q5. The business-lane output guard for a dual contact's MCH rows: (a) same per-company rule in
    the guard (MCH rows kept); (b) guard stays strict and the lane answers from the SQL layer only.
    **Rec (a)**: the three layers must agree or the guard silently empties the Mocha answer.
