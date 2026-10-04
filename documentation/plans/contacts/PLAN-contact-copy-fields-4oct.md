# PLAN: Contact copy also copies location, stock check mode, companies, market segment (CONTACT-COPY-FIELDS)

Status: Review done - built, reviewer + security-reviewer clean after round 1, sandbox browser pass PASS; waiting on CI and owner hand test. Track: small fix (no migration, no new permission slug; security review ran because the copy now writes companies + stock visibility).

Owner ask (4 Oct 2026): the contact copy (one source, one or many targets) must also copy location, stock
check mode, companies and market segment. UAC: `contact-copy-fields-4oct-acceptance-criteria.md`.

## Where the copy lives today

- Endpoint `POST /api/v1/user-management/contacts/bulk-copy-access`
  (`sorento_crm_backend/app/api/v1/user_management/contacts.py`, `bulk_copy_contact_access`), guarded by
  `require_permission("user_management.contacts.edit")`. Single copy is the same endpoint with one target.
- Service `sorento_crm_backend/app/services/contact_access_copy_service.py`: `snapshots` reads, `diff`
  compares, `_write` writes inside a per-target savepoint, one `contact.access_copied` audit row per target.
- Semantics: **replace** (owner Q2 of CONTACT-BULK-ACCESS). Every facet, list facets included, ends equal
  to the source. No facet merges.
- Dialog `sorento_crm_frontend/app/(protected)/user-management/contacts/components/BulkCopyAccessDialog.tsx`:
  the "copy from" step shows the server's `source.summary` lines plus a hard-coded "Not copied" line.

## Field mapping (measured, not guessed)

| Owner word | Storage | Facet(s) |
| --- | --- | --- |
| location | `stock_visibility_policies` contact-tier row: `warehouse_ids` / `excluded_warehouse_ids` / `hide_zero_locations` | `stock_locations` |
| stock check mode | same row: `mode` (detailed / compact / availability) | `stock_check_mode` |
| companies | `respond_contact_companies` | `companies` |
| market segment | `respond_contact_market_segments` | `market_segments` |

Both stock facets are one row (the contact's override on the "Stock visibility" card). Replace means: the
source has an override -> the target gets the same row; the source inherits (no row) -> the target's row is
deleted so it inherits too. Written through the ORM so the table's `__audit_track__` listener records it.

## Access (no privilege escalation)

The copy endpoint stays on `user_management.contacts.edit`. Each new facet is copied only when the actor
could set it directly:

- companies: superadmin/admin only (same check as `PUT /contacts/{id}/companies`).
- stock check mode + locations: `inventory.stock.edit` (same as `PUT /inventory/stock-visibility/contacts/{id}`).
- market segments: the direct `PUT /contacts/{id}/market-segments` needs only a signed-in user, so the copy's
  own `contacts.edit` gate is already stricter. No extra check.

A facet the actor may not set is left out of the copy entirely: not compared, not shown in the preview, not
written, and named in `source.not_copied`. The preview stays equal to the apply.

## Test list (red first)

`tests/test_contact_copy_fields.py` (reuses the fixtures of `tests/test_contact_bulk_copy_access.py`), per new facet, single and bulk target:
1. stock check mode + locations copied (override row created / replaced / deleted when source inherits)
2. companies copied, replace semantics (extra company removed) for a superadmin
3. market segments copied, replace semantics
4. non-superadmin with contacts.edit: companies untouched, not in preview, named in not_copied
5. user without inventory.stock.edit: stock facets untouched
6. preview == apply rows for the new facets
FE vitest: summary renders the new lines and the server's not_copied list.

Review round 1 (reviewer + security-reviewer, no blockers):
7. "Access differs from" passes the viewer's `facets_to_skip`, so the filter lists exactly what that
   viewer's copy would change (UAC A2.5 holds for restricted users; no equal/not-equal leak of companies).
8. Stock facets also skipped for a non-admin when the strict module guard is on and `inventory` is off
   (same as `require_permission` on the direct editor).
9. A location or company id that no longer resolves shows as "removed location" / "removed company".
