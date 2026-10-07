# PLAN - Brand scope applies per company (BRAND-SCOPE-PER-COMPANY)

Status: in review, PR #1512, reviewer PASS + security-reviewer PASS on 55a94ec5 (track: small fix, size M, no migration, no permission change; security-reviewer
runs because the diff is multi-company data visibility). Card:
`CARD-brand-scope-per-company-6oct.md`. UAC: `brand-scope-per-company-6oct-acceptance-criteria.md`.
Branch `claude/brand-scope-per-company-pefgt8` (the sandbox's branch for lane
`crew/brand-scope-per-company`).

Paths relative to `sorento_crm_backend/`.

## Slice 1 - one predicate, every copy calls it

- `app/services/contact_brand_scope.py`
  - `scoped_brand_companies_select(scope)`: Core `select(brands.c.company_id).where(brands.c.id IN
    scope).correlate(None)`.
  - `brand_predicate(scope, brand_col, company_col)`: `or_(brand_col IN scope,
    and_(exists(companies), company_col NOT IN companies))`. The only place the rule is spelled.
  - `scoped_brand_companies(db, scope) -> frozenset[str]`: the Python twin for the output guard,
    read with company scope OFF and brand scope OFF.
  - `out_of_scope_product_codes` / `out_of_scope_product_ids`: select `company_id` too and apply
    the rule in Python with `scoped_brand_companies`.
  - `product_in_scope_clauses`, `complaint_in_scope_clauses`: Core subqueries use
    `brand_predicate(scope, products.c.brand_id, products.c.company_id)`.
  - Module docstring: the per-company sentence.
- `app/services/company_scope.py:274-283`: the hook builds its criterion with `brand_predicate(scope,
  Product.brand_id, Product.company_id)` (concrete clause, `include_aliases=True`, as before).
- `app/services/order_service.py:243-258` (`scope_product_filter`): same predicate.
- `app/models/base.py:75-79` docstring of `set_brand_scope`: the sentence.
- Kwargs sites `api/v1/sales/analysis.py:360-364`, `api/v1/scm/low_stock_report.py:522-525`:
  owned by this lane, re-read, unchanged (they forward brand ids; the worker re-stamps the
  frozenset and the hook applies the new predicate). A test proves the worker path.

## Slice 2 - tests (red first, then green)

- `tests/_brand_scope_seed.py`: `BrandWorld` gains a second company MCH, two MCH products with
  NULL brand, stock + a DO line for one of them, and a `dual` contact (SRT + MCH memberships,
  `brand_ids = [SORENTO]`) plus `mocha_only` (MCH membership, `brand_ids = [SORENTO]`). Existing
  fixtures and assertions untouched (the SRT-only world is exactly as before; new rows carry the
  `ZZT` prefix and MCH company so no existing count moves).
- `tests/test_brand_scope_per_company.py` (new): session criterion, join from a line table, aliased
  Product, `product_in_scope_clauses`, `complaint_in_scope_clauses`, `out_of_scope_product_codes`
  / `_ids`, `scope_product_filter`, NO_BRAND_ID sentinel, "MCH brand added to the list" flip,
  REST paths via X-API-Key for the dual and Mocha-only contacts (stock balance, products search,
  orders by product), worker re-stamp (`set_brand_scope` on a fresh session).
- `tests/chatbot/test_brand_scope_console_replays.py`: the dual contact asking an MCH code is
  answered; asking the CABANA code and the unbranded SRT code still equals the ghost reply.
- `tests/test_contact_brand_scope_service.py:105-112`: `brand_predicate` call gains the company
  column (signature change, assertion unchanged).

## Slice 3 - evidence

- Kill tests: revert the hook line to the old IN -> the new session-criterion test is red; drop
  `exists()` -> the sentinel test is red; drop the company read in `out_of_scope_product_codes`
  -> the guard test is red.
- reviewer + security-reviewer (scope: data visibility across companies).
- Console replay table in the PR (crew-note), owner hand test (crew-handtest).
- Owner script comment block: proposed one-line change posted as crew-note (never committed).
