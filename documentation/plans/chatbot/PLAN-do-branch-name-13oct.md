# PLAN - DO branch name in the chatbot DO list and the DO UI (DO-BRANCH-NAME)

Status: Build (owner answer 13 Oct: default ON via the DEFAULT_ON_KEYS prefix rule, no migration). Track: small fix
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
