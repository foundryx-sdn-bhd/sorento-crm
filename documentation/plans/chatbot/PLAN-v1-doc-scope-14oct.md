# PLAN: v1 chatbot - unlinked non-staff contacts get no other customers' documents (V1-DOC-SCOPE)

Status: in review (small fix track + security review; no migration, no new reply text). PR #1648.

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

Staff unchanged. Blocked cases reuse the existing refusal line ("Sorry, that isn't under your
account. I can only check on your own account.").

## Who is staff (crew ruling (a), 14 Oct)

Staff = `chatbot_profile.tier == "office"` OR an active office access type
(`contact_customer_scope.is_office_staff`), the same rule in all three fixes. The engine stamps it
once per turn as `ctx["doc_staff"]`; `ref_staff` (tier only) is untouched because it also gates our
own PO / PR reveals. About 30 existing tests seeded an unlinked, tierless contact playing an office
role; they now mark it staff explicitly, assertions unchanged.

## Open before merge

The owner runs the read-only SQL in the PR's crew-done comment on production to see which unlinked
contacts with neither marker asked order questions in the last 60 days (they lose those answers).

## Acceptance

See `v1-doc-scope-acceptance-criteria.md`.
