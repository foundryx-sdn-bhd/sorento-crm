# PLAN: gate delivery-order read routes on order_management.orders.view (issue #1669)

Status: in progress (small fix track: backend only, under 300 lines, no migration)

## Problem

The DO read routes in `sorento_crm_backend/app/api/v1/order_management/orders.py` depend only on
`get_current_user_or_api_key` plus the `order` module guard, so any signed-in staff user can read
every DO in their company.

## Routes gated

- `GET /orders/` (list)
- `GET /orders/debtors`
- `GET /orders/by-product`
- `GET /orders/analytics`
- `GET /orders/{order_id}/fulfilled-complaints`
- `GET /orders/{order_id}` (detail)

Each gets `require_permission_with_api_key("order_management.orders.view")`, the same gate the
outstanding/sales/top-selling reports in the same file already use.

## Tests (red first)

403 without the permission, 200 with it, API-key (integration act-as user) path.
