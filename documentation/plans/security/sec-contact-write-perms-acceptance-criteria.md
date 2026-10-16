# UAC: respond-contact write route gates

Plan: `PLAN-sec-contact-write-perms-16oct.md`

- UAC1: A signed-in user without `user_management.contacts.edit` gets 403 from each of
  `POST /contacts/bulk-sync`, `DELETE /contacts/bulk`, `POST /contacts/`, `DELETE /contacts/{id}`,
  `POST /contacts/{id}/sync`, `PUT /contacts/{id}/market-segments`,
  `PUT /contacts/{id}/attachment-types`, and nothing is written or synced.
- UAC2: A user holding `user_management.contacts.edit` gets the same success response as before on
  each of those routes.
- UAC3: Every non-GET route in `contacts.py` carries a `require_permission` dependency, except the
  superadmin-checked `PUT /contacts/{id}/companies`; a new ungated write fails the test suite.
- UAC4: No migration, no UI change; no role that holds `user_management.contacts.view` loses an
  action (all hold `.edit` via migration 357).
