# CONTACT-TAGS review fix round (16 Oct): rulings

Source: security-reviewer (MUST-FIX) + reviewer (NOT READY) on code through 76082abf9. Each item: the
finding, the captain ruling, and whether it needs a red test first (T) or is a refactor / UI cleanup (C).
Paths under `sorento_crm_backend/app/` or `sorento_crm_frontend/app/(protected)/user-management/`.

## Security

- F1 (MUST, T) Read side follows the same gates and company scope as the rest of the app.
  - `GET /contact-tags/settings-catalog` is built per caller: companies only for superadmin; agents (and
    agent field keys) only with `user_management.access_agents.view`; warehouses only with
    `inventory.stock.view`; brands under the caller's company scope (as `contact_brand_scope.py:279`).
    A list the caller cannot read is absent (not empty-by-accident): FE hides that group's editor and
    shows the group read-only as "Not visible to you" when the tag sets it.
  - `GET /contacts/{id}/tag-settings` and list `tag_settings.differ_labels`: for a section the viewer
    cannot read (companies: superadmin; agents + agent_fields: access_agents.view; stock_visibility:
    inventory.stock.view) the item keeps `section`, a generic label ("Companies", "Chatbot agents",
    "Agent field access", "Stock visibility") and `differs`, but `current`, `expected`, `decided_by`
    values are null and no names leak.
  - Brand validation on tag save uses the caller's company scope (unknown / out-of-scope brand -> 422).
- F2 (SHOULD, T) Media bounds equal the contact route (`api/v1/user_management/contact_media_access.py:41,44`):
  monthly_limit 0..100000, max_clip_seconds 1..3600, else 422.
- F3 (SHOULD, T) `differs_count` cost: `GET /contact-tags?counts=false` returns no counts (null) and
  the tag picker (`contact-tags/hooks/useContactTags.ts:43`) uses it; with counts, evaluation stops at
  the same 5000-holder cap as the filter and answers null above it (FE shows "-").
- F4 (NOTE, T) Tag membership changes are audited: one `log_audit` row per contact per add / remove
  (bulk-tags, PUT /contacts/{id}/tags), naming actor, tag and contact.
- F5 (NOTE, T) Deleting a tag needs the gates of every gated section it sets (same as clearing them by
  PATCH): route and `contact_tag.delete` execute -> 403 naming the section.
- F6 (NOTE, T) agent_fields entries are validated against GATED_FIELDS ownership on save (422), same as
  `set_field_access`; the FE editor picks resource and field from the catalog instead of free text.

## Correctness

- R1 (BLOCKER, T) An empty brand intersection across tags is a CONFLICT (like regions), never written.
- R2 (BLOCKER, T) Stock and spec apply: a new contact row is seeded from the RESOLVED (effective) policy
  and only the keys the tags set change; the preview lists every key the write changes.
- R3 (SHOULD, T) Agents: `current` = the runtime reader only (`field_access.contact_agent_codes`; phone-only
  rows do not count, as at runtime). On write, a phone-only row of that agent is adopted (its
  `respond_contact_id` set) instead of inserting, and duplicate rows for that agent are removed (copy
  service precedent `contact_access_copy_service.py:614-659`). Re-apply is unchanged.
- R5 (SHOULD, T) Portal kinds that need a linked sales agent (`AGENT_ONLY_FORM_TYPES`,
  `portal_service.py:94`) on a contact without one: the item carries a `blocked_reason`, does not count
  as a difference, and apply lists it under `not_applied` with that reason (never "changed" again).
- R6 (SHOULD, T) BulkTagDialog with "Apply tag settings now": after confirming, show a result step (tag
  counts + settings counts, failed rows with reasons, conflicts, not applied) instead of closing on a
  tag-only toast. Same never-stuck rule as the apply dialog.

## Nits

- N1 (T) The `tag_settings` list filter runs after every other filter, so the 5000 cap counts the
  filtered set (`contact_service.py:293`).
- N2 (T) 403 messages name the section in words ("Chatbot agents"), not the key.
- N3 (T) Export writes human values for Tag settings ("Matches", "Differs: 2", "-").
- N4 (T) "Apply to N contacts" counts only rows that will change (`ApplyTagSettingsDialog.tsx:248`).
- N5 (T) The stock and spec editors do not allow both the include and the exclude list.
- N6 (T) The tag detail settings area shows the greyed "Not on a tag" block from the approved mock (owner
  ruling Q3: flag, never drop silently).
- N7 (C) BulkTagDialog uses the app's radio group component, not a raw input.
- N8 (C) Services are called through hooks (`ContactTagDetail.tsx:79`, `ContactTagSettings.tsx:741`,
  `ContactTagSettingsSection.tsx:80`, `ContactEditDialog.tsx:183`).
- N9 (C) Remove the unused `holder_order` parameter; one Edit button on the tag detail header; tags shown
  once on the contact page.

## Follow-up rulings (after coder 081b93681)

- Read gates for withheld sections accept the matching edit slug too (`user_management.access_agents.edit`
  reads agents, `inventory.stock.edit` reads stock): edit implies read, as elsewhere in the app.
- F5: the deferred-action engine (`app/services/form_action_service.py`) now passes the parking actor as
  `payload["requested_by_id"]` to `execute`, so `contact_tag.delete` re-checks the section gates at commit
  time. Shared engine change: security re-check covers the other registered actions.
- Three older tests (S3 t3_8, S3 t3_9, S2 stock merge) follow R3 / F4 / F1 (test-only alignment).
- R2 preview must also list a sibling list the one-rule CHECK clears (inherited include list cleared
  when a tag sets an exclude list, and the reverse; stock and spec).

## Final security re-check (834a21bac)

- MUST: apply's `changes[].added/removed` named out-of-scope brands (`contact_tag_apply.py:387` built an
  unscoped Catalog). Fix: scoped catalog for names; test with a contact whose current brand set holds
  another company's brand.
- Known, parity with the contact Brands card (not changed here): apply writes the expected brand list
  whole, so a company-scoped user's apply can drop a brand restriction that belongs to another company;
  the preview shows it ("unavailable brand" removed) before anything is written.
