# PLAN: Customer groups (CUSTOMER-GROUP)

Status: building (PR #1441); mock v1 approved 2 Oct 2026; review round open. Track: size M (crew), migration + new page.
UAC: `customer-group-acceptance-criteria.md`. Mock: `documentation/mockups/customer-group/index.html`.

## Why

Ledgers are grouped into one company only by the name rule `ledger_family_key`
(`app/services/ledger_family.py:42`). It merges different legal entities (KEDAI PAPAN HENG
CHOON (M) / (UTARA); JUBIN BMS (NS) / (KLANG) / (1990)) and cannot join one owner's two names
(CHIN CHUN HARDWARE / HOMEMART share code 300-C043). Owner rulings 2 Oct 2026: a group table
per company, seeded from numbered families (superseded 3 Oct 2026: no seed; groups set by
hand via bulk "Set customer group", CUSTOMER-BULK-OPS), no auto-assign on import, a Customer Groups page,
the name rule as fallback.

## Data

- `customer_groups`: `id` uuid pk, `company_id` (CompanyScopedMixin), `name` varchar(255) not
  null, `created_at`, `updated_at`. Unique index `(company_id, lower(name))`. Audited
  (`__audit_track__`, entity type `customer_group`, columns `name`).
- `customers.customer_group_id` uuid null, FK `customer_groups.id` ON DELETE SET NULL, index;
  added to `Customer.__audit_columns__`. Relationship `customer_group` (`lazy="selectin"`,
  same reason as `sales_agent`, `app/models/order.py:158`) + property `customer_group_name`.
- Migration `cust_group_0001`, down_revision = main head at build time (`grn_pull_0001_perm`
  today). `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`. Seed as a module-level
  `seed(connection)` (precedent `alembic/versions/acct_ledger_0001.py:25`) using
  `ledger_family_key` / `ledger_family_label`: group rows by (company_id, key); keep families
  with 2+ rows and any `account_level`; skip families whose rows already carry a group; name =
  label of the member with lowest (account_level, customer_code); insert group (on name clash in
  that company, reuse the existing group); set members' `customer_group_id` where null.
  Measured on dev: 796 groups, 2,804 rows, none of CASH / SHOPEE / LAZADA (they hold no
  numbered account, so no skip list is needed).
  **Superseded 3 Oct 2026 (owner ruling, option b: no automatic name-matching joins, explicit
  links only).** `cust_group_0001` is DDL only and assigns no customer on upgrade (lane
  CUST-GROUP-SEED-REVIEW, PR #1451). No seed follows (owner option (c)): groups are set by hand via bulk
  "Set customer group" (CUSTOMER-BULK-OPS). Lane plan:
  `documentation/plans/master-data/PLAN-customer-group-seed-review-3oct.md`.
- Crew SQL for the shared dev DB: `crew/state/migrations/CUSTOMER-GROUP.sql`, the DDL only
  (idempotent); the seed UPDATE is held for the owner per the crew contract, or run via
  `alembic upgrade` on the test copy.

## Backend (`/api/v1/order-management/customer-groups`, order_management router, mounted like `customers_select`)

Template: `app/api/v1/master_data/sales_agents.py:156-210` (agent customers list / assign) and
`CustomerService.unassign_sales_agent` (`app/services/order_service.py:3760`).

| Route | Permission | Notes |
|---|---|---|
| `GET /` | customers.view | page, limit, query, sort (name, ledger_count), dir; rows `{id, name, ledger_count, account_levels, updated_at}` |
| `GET /select` | customers.view | `{id, name, ledger_count}`, query-searched, limit 50; mounted before `/{id}` |
| `POST /` | customers.edit | `{name}`; trimmed; 422 blank; 409 duplicate (case-insensitive, same company) |
| `GET /{id}` | customers.view | `{id, name, ledger_count, account_levels, created_at, updated_at}`; other company 404 |
| `PATCH /{id}` | customers.edit | rename, same rules as POST |
| `GET /{id}/customers` | customers.view | `CustomerService.list_customers(customer_group_id=...)` |
| `POST /{id}/customers` | customers.edit | `{customer_ids}`; all or nothing; 404 unknown/out of scope; 422 other company |

Pending actions (`app/services/record_actions.py`): `customer_group.delete` (WINDOW_DESTRUCTIVE,
`order_management.customers.delete`, hard delete) and `customer.remove_from_group`
(WINDOW_REVERSIBLE, `order_management.customers.edit`, payload `customer_group_id`, 409 when
moved meanwhile), plus the park-time visibility check in `app/api/v1/system/pending_actions.py:145`.

Customer API: `CustomerResponse` + `customer_group_id`, `customer_group_name`; Create/Update
accept `customer_group_id` (company must match: 422); `list_customers` + `customer_group_id`
filter (customers are not in the list-query registry, so no registry change); `/customers/select` also returns `customer_group_id` / `customer_group_name` for the Add ledgers picker.

Service: `app/services/customer_group_service.py` (CRUD, assign, remove, counts, and
`family_overrides(db) -> dict[str, str]` for the chatbot, below).

## Chatbot: one seam, every call site

Every grouping site calls `ledger_family_key` / `ledger_family_label` on a NAME string
(narrow.py:85/160, compose.py:293, session_state.py:103, resolve_gate.py:1026/1115,
stock_ask_service.py:544), and `gate._cust_base` (gate.py:344) is the same rule on a match.
Some have no uuid (compose works on header strings), so the override is keyed by name:

- `ledger_family.py` gains a `ContextVar` holding `{normalised customer_name: group name}`
  (normalised = upper-cased, whitespace collapsed) and a context manager `customer_groups(map)`.
  `ledger_family_key(text)` returns the rule applied to the group name when `text` is in the map,
  else today's rule; `ledger_family_label` returns the group name, else today's label. Pure
  string code; no DB, no `re` (AC-1520 still holds). Precedent for a turn ContextVar:
  `app/services/chatbot/turn/refer.py:43`.
- `customer_group_service.family_overrides(db)`: one query over grouped customers in scope;
  a name whose rows sit in more than one group is left out (falls back to the rule).
- The engine sets it once per turn where the turn's scoped session opens (engine.py
  `_answer_claimed`, line ~1256; coder confirms the exact spot); `stock_ask_service` sets it
  around its own label read. `gate._cust_base` checks the map before its regex rule.
- #1433 (family_words) and #1435 (not-your-account line) are open PRs; whichever is on main
  at build time is covered by the same seam because it calls `ledger_family_key`; verified with
  a test then.

## Frontend

Template: `app/(protected)/master-data-management/sales-agents/` (list, `[id]` detail,
`SalesAgentCustomersTab`). New `app/(protected)/order-management/customer-groups/`
(`page.tsx`, `[id]/page.tsx`, `components/`, `hooks/useCustomerGroups.ts`,
`services/customerGroupService.ts`, `types/`). Menu entry in `config/menu.config.tsx` after
Customers (both menu blocks, lines ~206 and ~1648). Customer screens: `CustomerDetail.tsx`
(Group field + Group ledgers card), `CustomerForm.tsx` (Group select), `CustomersList.tsx`
(column + filter). Component map in the mock.

## Slices

- S1 BE data + seed + group API + customer API (AC-1..9).
- S2 chatbot seam (AC-10..15).
- S3 FE (AC-16..22), against the S1 API (mock stays the visual contract).

Red-first per slice: `test(red):` commit with tests only, then the code commit; kill test at end.

## Not doing (named triggers)

- Auto-assign new ledgers on import (Q3): trigger = staff report unassigned new ledgers.
- Cross-company groups (Q5): trigger = a group that must span Sorento and Mocha.
- Bulk regroup from the Customers list: trigger = staff ask to move many ledgers at once
  outside a group page.
