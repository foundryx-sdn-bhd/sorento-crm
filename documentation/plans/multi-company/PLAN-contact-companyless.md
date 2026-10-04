# PLAN: Contact detail page ignores the company switcher (CONTACT-COMPANYLESS)

Status: APPROVED 3 Oct 2026 (owner: Q1 always tag, Q2-Q5 as recommended), Build. Track: M (lead pattern). UAC: `contact-companyless-acceptance-criteria.md`.

## Mechanism (one helper, no registry)

`company_scope_resolver.grants_scope(db, user_id)`: a context manager that sets the session scope
to `frozenset(resolve_user_grant_ids(db, user_id))` (`company_scope_resolver.py:114`) and restores the
prior scope on exit (reuse `models/base.py:73 company_scope`). Reads only; every write on this page
already carries an explicit company (link = customer's company) or none (policy row).

Callers opt in per request with `?company_scope=grants`. Session (staff) principals only; an
X-API-Key / portal principal ignores the flag and keeps today's scope. No new permission.

## Slices

1. BE: helper + flag on `GET /inventory/warehouses/` (`api/v1/inventory/warehouses.py:28`),
   `GET /order-management/customers/select` (`customers_select.py:19`),
   `GET /master-data/products/select` (`products_select.py:18`), `GET /master-data/brands/` (`brands.py:83`).
   Under the flag every row carries `company_id` + `company_name` (always, owner Q1 (a)).
2. BE: contact-customer endpoints always read under grants: list `UM:774`, add `UM:793`
   (`get_customer_in_scope`, `contact_customer_service.py:210`), unlink park + commit
   (`pending_actions.py:153/420`, `contact_customer_service.py:279`); `profile_facts` value validation
   (`profile_facts.py:423-428`) and primary-customer facts (`:247`).
3. FE: services pass `company_scope=grants` (`stockVisibilityService.ts:268/281`,
   `customerService.ts:126` via the contact picker only, `contactChatbotService.ts:262-293`);
   every option and chip on these pickers reads `<Company> · <label>` (always, Q1 (a)). Existing
   components only; no layout change, no mock.

## Out of scope

CS routing candidates (Q4 rec (a)); chips of the stock-visibility card already read all companies
(`stock_visibility.py:82-95`), unchanged.
