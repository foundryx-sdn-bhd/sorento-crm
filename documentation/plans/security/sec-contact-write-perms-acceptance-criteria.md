# UAC: respond-contact write route gates

Plan: `PLAN-sec-contact-write-perms-16oct.md`

- UAC1: A signed-in user without `user_management.contacts.edit` gets 403 from each of
  `POST /contacts/bulk-sync`, `DELETE /contacts/bulk`, `POST /contacts/`, `DELETE /contacts/{id}`,
  `POST /contacts/{id}/sync`, `PUT /contacts/{id}/market-segments`,
  `PUT /contacts/{id}/attachment-types`, and nothing is written or synced.
- UAC2: A user holding `user_management.contacts.edit` gets the same success response as before on
  each of those routes.
- UAC3: Every non-GET route on the routers mounted at `/contacts` (`contacts.py`,
  `contact_media_access.py`, `contact_portal_forms.py`) is gated by plain `require_permission` on
  `user_management.contacts.edit`, except the portal-link pair (`contacts.portal_link`) and the
  superadmin-checked `PUT /contacts/{id}/companies` (pinned by its own 403 test); a new write that
  is ungated, gated on another slug or opened to X-API-Key fails the test suite.
- UAC4: No migration, no UI change; no migration- or seeder-created role that holds
  `user_management.contacts.view` loses an action (all hold `.edit` via migration 357, and
  `integration_seed` copies admin's grants). A role hand-built in the Roles screen with `.view`
  but not `.edit` would lose these writes; verify zero such roles on the prod copy before merge
  (SQL in the PR).
