# CONTACT-TAGS S3 contract (apply tag settings, preview, add-tag apply choice)

Binding for tester and coder. UAC: T2.1-T2.3 (apply part), T3.1-T3.9, T5.2 CTA, T5.4, T5.6 button.
Builds on S2's expected / current / conflict computation (same module); apply makes current = expected.
Paths under `sorento_crm_backend/app/` unless they start with `sorento_crm_frontend/`.

## Scope of keys per entry point

| Entry point | Keys applied |
|---|---|
| POST `/user-management/contacts/bulk-apply-tag-settings` `{contact_ids, dry_run=true}` (bulk Actions, row "..." menu, contact Tags section; one id = one contact) | every key any of the contact's tags sets |
| POST `/user-management/contact-tags/{id}/apply` `{dry_run=true}` (tag detail CTA "Apply to N contacts") | every holder of the tag; only the keys THIS tag sets (value still merged across the contact's tags) |
| POST `/user-management/contacts/bulk-tags` `{..., apply_settings: bool=false}` | after the tag change, the keys set by the tags ADDED in this call; `false` writes no setting ("Don't apply now") |

Max 500 contacts per call (422 above, also for a tag with more than 500 holders: answer 422 naming the
count); empty `contact_ids` -> 422. Gate: `user_management.contacts.edit` (403 otherwise).

## Answer (all three routes; bulk-tags adds it under `settings` per row when apply_settings is true)

```
{dry_run, results: [{contact_id, label, status: changed|unchanged|skipped|failed,
   changes: [{section, key, label, before, after, decided_by: [tag name], added: [..], removed: [..]}],
   conflicts: [{label, tags: [{name, value}]}],
   not_applied: [{label, reason}],
   error}],
 counts: {changed, unchanged, skipped, failed, conflicts}}
```
Rows in request order (tag apply: holders ordered by name). Values and labels human (names), never ids.
`skipped` = contact holds no tag that sets anything in scope. Unknown contact -> `failed` "contact not
found"; the others still apply.

## Write rules

- Conflicts are never written (listed under `conflicts`).
- A section the ACTOR may not write is skipped and listed under `not_applied` with the reason, the rest
  still apply (gates as in the S2 contract: agents/agent_fields `user_management.access_agents.edit`,
  stock_visibility `inventory.stock.edit`, companies superadmin, outbound_enabled
  `system.respond_workspaces.edit`).
- Per-contact savepoint: a contact whose write raises answers `failed` with the reason and keeps every
  value it had; the others apply.
- Only keys whose current differs from expected are written; a second apply of the same request
  answers every row `unchanged` (idempotent). Preview (`dry_run: true`) writes nothing and equals apply.
- Writers (same service functions the contact page uses; PLAN Design table):
  - access_types / companies / market_segments / attachment_types / brands: replace the set exactly with
    the expected set (removals listed in `removed`). brands "all" stores NULL.
  - regions, chatbot switches, memory_level, requires_registered_project: the contact columns.
  - tier: update ONLY the `tier` key of `chatbot_profile`; `facts`, `language`, `default_ledgers` and
    every other key are kept (do not reuse a writer that replaces the profile).
  - portal_forms: upsert `contact_portal_form_overrides` (contact, form_type, is_enabled = expected) only
    for kinds whose resolved visibility differs.
  - field_reveals: per key upsert of `granted`; keys not set by the tags untouched.
  - agents: per agent upsert of `contact_agent_access` (`is_allowed` = expected, no validity window);
    rows of agents not set by the tags untouched; a legacy phone-only row of that agent counts as the
    contact's row (no duplicate), same as `services/contact_access_copy_service.py:614-659`.
  - agent_fields: `services/user_service.py:2738` set_field_access with contact_id, only sent entries.
  - media: `services/media_access_service.py:453` upsert_access with only the keys set.
  - stock_visibility / spec_visibility: upsert the contact row with the expected policy.
  - outbound_enabled: `services/respond_outbound_service.py:155`; never sends a message, never calls
    respond.io.
- Audit: one `log_audit` row per changed contact naming the actor, the tag(s) and the changed labels
  (several writers are not covered by the ORM listener).
- After apply, GET `/contacts/{id}/tag-settings` reports `matches` for every applied key.

## Frontend

- Apply preview dialog `sorento_crm_frontend/app/(protected)/user-management/contacts/components/ApplyTagSettingsDialog.tsx`
  (look of `BulkCopyAccessDialog.tsx` preview: counts pills, one card per contact, before -> after per
  change with the deciding tag, conflicts amber "not applied", not_applied grey with reason): opens with a
  `dry_run: true` call, "Apply to N contacts" sends `dry_run: false`, result step shows counts; any error
  shows the extracted message with the dialog usable (retry / close).
- Entry points: bulk Actions item "Apply tag settings (n)" (in `contacts/lib/contactTagBulkActions.ts`),
  row "..." menu item "Apply tag settings", contact detail Tags section button "Apply tag settings", tag
  detail header CTA "Apply to N contacts" (route `/contact-tags/{id}/apply`), all reusing the one dialog.
- `BulkTagDialog` step 2 (add mode): radio "Apply tag settings now" (default, preview of setting changes
  shown) / "Don't apply now" (labels only); button "Add tag to N contacts". Remove mode has no apply step.
- After apply: invalidate the contacts list, the contact detail, tag list and tag detail queries; toast.
