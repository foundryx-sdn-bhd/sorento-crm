# PLAN: gate delivery-order read routes on order_management.orders.view (issue #1669)

Status: implemented, in review (small fix track: under 300 lines, no migration; backend gate plus
the existing `RequireAccess` page guard on the DO list and detail pages)

## Problem

The DO read routes in `sorento_crm_backend/app/api/v1/order_management/orders.py` depended only on
`get_current_user_or_api_key` plus the `order` module guard, so any signed-in staff user could read
every DO in their company.

## Routes gated on `order_management.orders.view`

Reads (`require_permission_with_api_key`, the gate the outstanding/sales/top-selling reports in the
same file already use):

- `GET /orders/` (list)
- `GET /orders/debtors`
- `GET /orders/by-product`
- `GET /orders/analytics`
- `GET /orders/{order_id}/fulfilled-complaints`
- `GET /orders/{order_id}` (detail)

Writes that answer with the full DO, so `PUT {}` was itself a read (security review B1):

- `PUT /orders/{order_id}` (`require_permission`, JWT only as before)
- `POST /orders/{order_id}/cancel` (`require_permission_with_api_key`, the MCP record action uses a key)
- `POST /orders/{order_id}/lines`, `PUT /orders/{order_id}/lines/{line_id}` (`require_permission`)

Frontend: `orders/page.tsx` and `orders/[id]/page.tsx` wrap in `RequireAccess`, the pattern
`inventory-management/stock-transfers/[id]/page.tsx` uses. UX only; the backend is the control.

## Tests

- `tests/test_order_read_routes_permission.py`: per read route 403 / 200 for a JWT principal and for
  an X-API-Key act-as principal; per write route 403 without the grant; `PUT {}` 200 with it.
- `orders/[id]/page.test.tsx`: a user without the grant sees AccessDenied, not the record.

## Out of scope (follow-ups)

Other DO write routes (create, delete, line delete, archive/restore, import-tracking) still have no
RBAC; the AI assistant reads DOs through MCP as the integration principal without the end user's
grant; the public portal DO lookup is unscoped; complaint creation lists candidate DOs.
