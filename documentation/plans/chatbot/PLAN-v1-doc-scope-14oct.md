# PLAN: v1 chatbot - unlinked non-staff contacts get no other customers' documents (V1-DOC-SCOPE)

Status: in progress (small fix track + security review; no migration, no new reply text)

## Problem

An unlinked non-staff contact (no customer link, no active office access type; owner rule:
unlinked != customer) can name a delivery-order number, or a sales-order number with the
`sales_orders.outstanding` reveal, and v1 answers with that document for ANY customer.

## Fix (same shapes v1 already uses)

1. The resolver per-match drop (`turn_runtime._without_foreign_sales_orders`) also drops a
   delivery-order match whose customer is not in the contact's links (the
   `so_status._scope_check` rule). Unlinked non-staff: no customer document matches.
2. Business lane SO-by-number with the reveal: `linked = None if ref_staff else links`, the
   rule the PO-ref branch already uses.
3. DO list asks with no number: scoped to links for non-staff (empty links: nothing).

Staff (office access) unchanged. Blocked cases reuse existing wording.

## Acceptance

See `v1-doc-scope-acceptance-criteria.md`.
