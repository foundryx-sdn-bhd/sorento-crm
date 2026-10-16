# PLAN: permission gates on the respond-contact write routes

Status: review passed (reviewer READY, security-reviewer CLEAN); awaiting owner merge (full track, size S: RBAC change, no migration, no UI change)
UAC: `sec-contact-write-perms-acceptance-criteria.md`
Lane: SEC-CONTACT-WRITE-PERMS

## Problem

Seven write routes in `sorento_crm_backend/app/api/v1/user_management/contacts.py` depend on
`get_current_user` only, so any signed-in user can create, delete, bulk delete, re-sync and
re-scope a respond contact (verified at origin/main 8ceadb98):

| Route | Line | Today |
| --- | --- | --- |
| `POST /contacts/bulk-sync` | 246 | `get_current_user` |
| `DELETE /contacts/bulk` | 264 | `get_current_user` |
| `POST /contacts/` | 309 | `get_current_user` |
| `DELETE /contacts/{id}` | 582 | `get_current_user` |
| `POST /contacts/{id}/sync` | 600 | `get_current_user` (not in the original finding) |
| `PUT /contacts/{id}/market-segments` | 852 | `get_current_user` |
| `PUT /contacts/{id}/attachment-types` | 928 | `get_current_user` |

Every other contact write already uses `require_permission("user_management.contacts.edit")`:
`PUT /{id}` (326), `/bulk-copy-access` (208), `/{id}/chatbot` (389), chatbot facts (491, 522),
cs-routing (741, 765, 815), `/{id}/brands` (889), `/{id}/customers` (974), and the sibling routers
`contact_media_access.py:73` and `contact_portal_forms.py:97`. `PUT /{id}/companies` (564) is
superadmin-only in the handler body (`_require_superadmin`), already a documented exception.
`system/respond_contacts.py` outbound writes and `contact_impersonation.py` (`_ensure_admin`) are
already gated.

## Decision: reuse `user_management.contacts.edit`

The slug exists (`app/rbac/permission_registry.py:64`). Migration `357_chatbot_media_permissions`
granted it to EVERY existing role to reproduce the old "any login" reach, while
`user_management.contacts.view` (migration 359) went only to admin, superadmin, director,
warehouse_manager and the integration_* roles. So every role that can open the contacts page
(the list GET needs `.view`) already holds `.edit`: nobody using the page loses an action today.
What changes is that the seven actions become revocable per role, like every sibling write. No
new slug, no migration, no UI change (the page's Create / Delete / Sync buttons are not yet
permission-gated in the UI; with every `.view` role holding `.edit` they keep working).

## Work

1. Red tests (`tests/test_contact_write_gates.py`): per route, a caller without `.edit` gets 403
   and the handler body never runs; with `.edit` the route runs (service call stubbed, so no
   Respond.io call). Plus a structural sweep: every non-GET route in `contacts.py` carries a
   `require_permission` dependency or sits in an allowlist (`/companies` only).
2. Swap the seven `Depends(get_current_user)` for
   `Depends(require_permission("user_management.contacts.edit"))`.
3. Reviewer + security-reviewer, kill test.
