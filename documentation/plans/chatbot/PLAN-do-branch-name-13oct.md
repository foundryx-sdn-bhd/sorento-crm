# PLAN - DO branch name in the chatbot DO list and the DO UI (DO-BRANCH-NAME)

Status: Review (owner answer 13 Oct: default ON via the DEFAULT_ON_KEYS prefix rule, no schema migration; data backfill below). Track: small fix
(no migration, no RBAC permission change, no new ingest surface; one new chatbot field reveal key).

## Owner ask (13 Oct 2026)

"in the DO that we sync over, actually we have a branch name field, that should be answerable in the
DO listing (controlled by contact same like other DO fields), so if the value is not null, then
according to contact access, we will show the branch name; make sure the branch name is visible in
the DO UI also so I can cross check".

## Where the value lives today

- Column: `orders.branch_name` (`sorento_crm_backend/app/models/order.py:461`), beside
  `orders.branch_code` (`:460`). DOs are the `orders` table.
- Written by the AutoCount DO ingest: `_branch_name` resolves `(debtor_code, branch_code)` against
  the synced branch book (`app/services/autocount_doc_ingest_service.py:813-827`), stored on the DO
  at `:1064`; a later branch rename re-stamps every DO of that branch (`_refresh_branch_names`,
  `:1642-1658`). AutoCount owns the column (`app/services/order_field_ownership.py:31`).
- Not on the wire: `OrderBase` / `OrderResponse` (`app/schemas/order.py:96-268`) declare neither
  `branch_code` nor `branch_name`, so `GET /api/v1/order-management/orders` (`response_model=
  ListResponse[OrderResponse]`, `app/api/v1/order_management/orders.py:304`) silently drops it. That
  is why neither the chatbot nor the DO UI can see it.

## How `branch_name` gets filled (and why existing rows can be empty)

- The ONLY source is the `branches` table (`app/models/autocount_branch.py`), written only by the
  SS `branchbypage` push to `POST /external/ingest/branches`. There is no CRM-side branch pull.
- A DO push resolves the name at write time (`_branch_name`, exact `AccNo` match first, else the
  one row for the code); a branch push re-stamps every DO of that code, but only when the branch
  row is CREATED or CHANGED (`autocount_doc_ingest_service.py:1621-1632`). An unchanged branch
  push returns before `_refresh_branch_names`.
- So a DO keeps a NULL name when (a) `branches` holds no row for its code (the branch push has not
  run for that book), (b) the code is ambiguous (several rows, none on the DO's debtor), or (c) the
  DO was written before its branch row and the branch has not changed since.
- Crew audit 13 Oct (dev copy): 6486 AutoCount DOs, 705 with `branch_code`, 0 with `branch_name`.

### Backfill (dev copy and prod, same SQL, idempotent)

Pre-check: `SELECT source_book, count(*) FROM branches GROUP BY 1;` If it returns no rows, the SQL
below fills nothing: ask SS to run the `branchbypage` push for each book first (a first push
CREATEs every row, which re-stamps the DOs by itself), then run the SQL for whatever is left.

```sql
-- DO-BRANCH-NAME backfill: orders.branch_name from branches, the same rule as
-- AutocountDocIngestService._branch_name (exact AccNo match first, else the one row for the code).
-- Fills only rows it can resolve; idempotent (re-running changes nothing).
WITH m AS (
  SELECT o.id,
         (SELECT array_agg(b.branch_name) FROM branches b
           WHERE b.company_id = o.company_id AND b.source_book = o.source_book
             AND b.branch_code = o.branch_code AND b.acc_no = o.debtor_code) AS exact,
         (SELECT array_agg(b.branch_name) FROM branches b
           WHERE b.company_id = o.company_id AND b.source_book = o.source_book
             AND b.branch_code = o.branch_code) AS by_code
    FROM orders o
   WHERE o.doc_key IS NOT NULL AND o.branch_code IS NOT NULL
), r AS (
  SELECT id,
         CASE WHEN cardinality(exact) = 1 THEN exact[1]
              WHEN cardinality(by_code) = 1 THEN by_code[1] END AS name
    FROM m
)
UPDATE orders o
   SET branch_name = r.name
  FROM r
 WHERE o.id = r.id AND r.name IS NOT NULL AND o.branch_name IS DISTINCT FROM r.name;
```

### Owner prod steps

1. After deploy, on prod: `SELECT source_book, count(*) FROM branches GROUP BY 1;`
2. If empty for a book: ask SS to run the `branchbypage` push for that book.
3. Run the backfill SQL above once (safe to rerun).
4. Check: `SELECT count(*) FILTER (WHERE branch_name IS NOT NULL), count(*) FILTER (WHERE branch_code IS NOT NULL) FROM orders WHERE doc_key IS NOT NULL;`
   A gap left over is ambiguous codes or codes SS has no branch row for: the bot prints no Branch line for those, by design.

## Behaviour

### Chatbot (DO list, `crm_order_management_orders_list`)

- New field `Branch` in `_orders_list` (`sorento_crm_mcp/sorento_crm_mcp/presenters.py:427`), one
  fact per line `Branch: <name>`, placed right after `Customer` (branch is a sub-account of the
  customer). Printed only when `branch_name` is filled (`_filled`, as for every other field); never
  derived from `branch_code`, never invented, never joined with ` · `.
- Gated like every other DO field: `b.restrict("branch_name", "delivery_orders.branch")`.
- Key `delivery_orders.branch` ("DO branch") added to `FIELD_REVEAL_KEYS`
  (`app/services/contact_field_reveal_service.py:43`) and the catalogue's `restricted_fields`
  (`sorento_crm_mcp/sorento_crm_mcp/catalog.py:670`). The pin test keeps them equal. Contacts >
  Access > Field reveals lists it with no UI change (it reads `field_reveal_keys()`).
- Default: ON for every contact (the `delivery_orders.` prefix rule, `DEFAULT_ON_KEYS`), same as
  the other DO fields; the owner switches it off per contact. Owner confirmed (a) 13 Oct 2026.
- Labels: `Branch` gets ms `Cawangan` / zh `分店` in `app/services/chatbot/label_catalog.py`.

### DO UI

- Orders list (`OrdersList.tsx`): a `Branch` column, visible by default, after Customer, truncate +
  title, explicit `size`.
- Order detail (`OrderDetail.tsx`): a read-only `Branch` field next to the customer; empty shows
  the page's usual empty value.
- Not editable: AutoCount owns it, the form keeps no input for it.

## Tests (red first)

- BE: `OrderResponse` carries `branch_name` on the list and single GET (response_model drop guard).
- MCP: `_orders_list` prints `Branch` when filled, omits it when null/blank, restricts it on
  `delivery_orders.branch`; catalogue declares the key.
- Chatbot: `test_do_field_reveals.py` covers the new key (granted shows, revoked hides);
  `test_do_reveals_default_on.py` includes it; pin test passes.
- FE vitest: list has a Branch column; detail shows the branch value.
