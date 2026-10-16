# UAC contact-tags-16oct

Status: DRAFT on the card's recommended answers (Q1a Q2a Q3 Q4a Q5a); revised when the owner answers.
Card: `CARD-contact-tags-16oct.md`. Mock: `documentation/mockups/CONTACT-TAGS/index.html`.

"Tag setting" below = one of: a portal form kind On/Off (complaint, stock_inquiry, purchase_request,
sponsorship_form, price_tag_request, sales_opportunity, conversation, customer_asks); a contact switch
On/Off (AI replies `chatbot_allowed`, stock checks, escalation, packing list, notify salesman); cost
visible (`purchase_orders.cost` reveal); a chatbot agent Allow/Deny; image access On/Off; voice note
access On/Off. A tag stores only the settings ticked on it ("set"); every other setting is "not set".

"Expected value" of a setting for a contact = the value set by its tags; when two of its tags set
different values, the restrictive one wins (Off / Hidden / Deny), and the result names that tag.
"Current value" = what the runtime reads today for that contact (portal form visibility resolver,
the contact row, the reveal row, the agent grant, the media limit row).

## T1 Tag list (catalog)
- T1.1 `GET /api/v1/user-management/contact-tags` lists tags with name, description, active, sort order,
  settings, contact count, differs count. Needs `user_management.contacts.view`.
- T1.2 Create / edit / delete a tag needs `user_management.contacts.edit`; 403 otherwise.
- T1.3 Tag names are unique, case-insensitive, trimmed; a duplicate answers 409 with the clashing name.
- T1.4 Settings reject unknown keys, unknown agent ids and non-boolean values (422, names the key).
- T1.5 Deleting a tag removes it from every contact; no contact's access value changes.
- T1.6 No tag is ever created or assigned by sync, webhook, import or name matching: a contact created
  by Respond.io sync has no tags.

## T2 Tagging contacts
- T2.1 `POST /api/v1/user-management/contacts/bulk-tags` `{contact_ids, add_tag_ids, remove_tag_ids,
  apply_settings, dry_run}`; max 500 contacts (422 above); needs `user_management.contacts.edit`.
- T2.2 `dry_run: true` writes nothing and returns, per contact, tags before/after and (when
  `apply_settings`) the setting changes apply would make.
- T2.3 `dry_run: false` adds/removes tags; with `apply_settings: true` it also writes the expected
  values for the contact's tags after the change (T3.3). Preview equals apply when nothing moved.
- T2.4 Removing a tag never reverts a value it applied earlier.
- T2.5 Adding a tag the contact already has, or removing one it lacks, is a no-op, not an error.
- T2.6 The contact edit dialog shows and edits the contact's tags (same rules, no settings write unless
  asked).

## T3 Apply tag settings
- T3.1 `POST /api/v1/user-management/contacts/bulk-apply-tag-settings` `{contact_ids, dry_run}` and
  `POST /api/v1/user-management/contact-tags/{id}/apply` `{dry_run}` (every holder of that tag) answer one
  row per contact: changed / unchanged / skipped / failed, with change rows (setting label, before,
  after, deciding tag). No ids in labels.
- T3.2 `dry_run: true` writes nothing.
- T3.3 `dry_run: false` makes every set setting's current value equal its expected value, and touches
  nothing else: not-set settings, access types, brands, customers, companies, segments, regions, tier,
  stock override, memory are unchanged.
- T3.4 A contact with two tags that disagree gets the restrictive value; the change row names the tag
  that decided.
- T3.5 A setting the actor may not write (agents need `user_management.access_agents.edit`) is skipped
  and listed as not applied; the rest still apply.
- T3.6 Per-contact transaction: one contact's write failure answers `failed` for it and leaves its
  values untouched; the others apply.
- T3.7 Portal form apply writes a `contact_portal_form_overrides` row only where the resolved form
  visibility differs from the expected value.
- T3.8 Agent apply upserts only the agents the tags set (never deletes another agent's grant).
- T3.9 Every apply writes audit rows naming the actor and the tag.

## T4 Cross-check (list)
- T4.1 Contacts list rows carry `tags` (names) and `tag_settings` = `matches` | `differs` | `none`
  (no tag sets anything), plus the differing settings (label, current, expected, deciding tag).
- T4.2 Filters: `tag=<id>` (one or several, any-of), `tags=none`, `tag_settings=differs|matches`;
  each combines with the existing filters.
- T4.3 A hand edit after apply makes the contact show `differs` naming that setting.
- T4.4 Export includes Tags and Tag settings columns.
- T4.5 Tag list differs count equals the number of its holders the list filter `tag=<id>&tag_settings=differs`
  returns.

## T5 UI
- T5.1 Users & Access > Contact tags: DataGrid list (fixed layout, resizable, Columns toggle), Add tag
  modal, row opens the tag detail page.
- T5.2 Tag detail: settings section in view and edit with the same layout; each setting Not set / On /
  Off (agents Not set / Allow / Deny); Contacts section; header CTA "Apply to N contacts" opens the
  preview; prev/next navigation; empty states heading + hint.
- T5.3 Contacts list: Tags column (pills with +N), Tag settings column (Matches / Differs: n with the
  settings named on hover), Tag + Tag settings filters, No tags checkbox; bulk Actions gets Add tag,
  Remove tag, Apply tag settings.
- T5.4 Add tag dialog: pick tag(s), then preview with "Apply tag settings too" ticked; untick = labels
  only. Every failure ends in a visible error with the dialog still usable.
- T5.5 Usable and not clipped at 375px and 1280px.

## T6 Migration
- T6.1 New tables `contact_tags` (uuid id) and `respond_contact_tags` (contact, tag, created_at,
  created_by); cascade on contact or tag delete; single alembic head; upgrade/downgrade/upgrade clean on
  a dev copy with row counts of `respond_contacts` and every access table unchanged.
- T6.2 Seeds no tags (owner creates the list).
