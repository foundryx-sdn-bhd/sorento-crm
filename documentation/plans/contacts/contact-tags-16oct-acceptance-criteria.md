# UAC contact-tags-16oct

Status: DRAFT v2 on owner rulings 16 Oct (Q1a, Q2a, Q3 everything, Q4a, Q5a with apply / don't apply).
Card: `CARD-contact-tags-16oct.md`. Mock: `documentation/mockups/CONTACT-TAGS/index.html` (v2 pending owner).

"Tag setting" = any per-contact setting below. A tag stores only the settings switched on for it ("set");
every other setting is "not set" and never touched by apply.

| Setting | Stored in | Merge across tags (most restrictive) |
|---|---|---|
| Access types | `respond_contact_access_types` | intersection |
| Brands | `respond_contacts.brand_ids` (empty = all) | intersection of non-empty sets |
| Companies | `respond_contact_companies` (admin only) | intersection |
| Market segments | `respond_contact_market_segments` | intersection |
| Regions | `respond_contacts.regions` (at least one) | intersection; empty = conflict |
| AI replies, stock checks, escalation, packing list, notify salesman | `respond_contacts` columns | Off wins |
| ETA buffer | `chatbot_eta_offset_applied` | On wins |
| Skip period questions, files as links | `respond_contacts` columns | no order: conflict |
| Memory level | `chatbot_memory_level` | lowest wins |
| Tier | `chatbot_profile.tier` | Office loses to Dealer / End user; Dealer vs End user = conflict |
| Portal forms (8 kinds) | `contact_portal_form_overrides` | Off wins |
| Requires registered project | `respond_contacts.requires_registered_project` | On wins |
| Field reveals (incl. cost) | `contact_field_reveals` | Hidden wins |
| Chatbot agents (allow / deny) | `contact_agent_access` | Deny wins |
| Agent field access | `agent_field_access` (contact rows) | Deny wins |
| Image / voice access, monthly limit, clip length, languages | `contact_media_limit` | Off wins; lowest limit; languages intersection |
| Attachment types | `contact_attachment_types` | intersection |
| Stock visibility | `stock_visibility_policies` (contact row) | existing resolver rule (`stock_visibility.py:159-200`) |
| Spec visibility | `spec_visibility_policies` (contact row) | show intersection, hide union |
| Messaging (outbound) | `respond_contacts.outbound_enabled` | Off wins |

Never on a tag (identity or per person): linked customers, linked staff user, linked sales agent, CS
routing, memory facts, language, default ledgers, agent grant dates, media usage counters, session vars,
phone / name / workspace / portal slug.

"Expected value" of a setting for a contact = the merge of the values its tags set. A "conflict" (no
stricter value exists, or the merge is empty where empty is not allowed) is never applied: the preview and
the list name it as Conflict with the tags involved. "Current value" = what the runtime reads today for
that contact.

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
  `apply_settings` is the owner's explicit choice in the dialog ("Apply tag settings now" / "Don't apply
  now"); with "Don't apply now" no setting is written.
- T2.2 `dry_run: true` writes nothing and returns, per contact, tags before/after and (when
  `apply_settings`) the setting changes apply would make.
- T2.3 `dry_run: false` adds/removes tags; with `apply_settings: true` it also writes the expected
  values for the contact's tags after the change (T3.3). Preview equals apply when nothing moved.
- T2.4 Removing a tag never reverts a value it applied earlier.
- T2.5 Adding a tag the contact already has, or removing one it lacks, is a no-op, not an error.
- T2.6 The contact edit dialog shows and edits the contact's tags (same rules, no settings write unless
  asked).

## T3 Apply tag settings
- T3.1 `POST /api/v1/user-management/contacts/bulk-apply-tag-settings` `{contact_ids, dry_run}` (bulk
  Actions, the row "..." menu and the contact detail Tags section all call it; one id for a single contact) and
  `POST /api/v1/user-management/contact-tags/{id}/apply` `{dry_run}` (every holder of that tag) answer one
  row per contact: changed / unchanged / skipped / failed, with change rows (setting label, before,
  after, deciding tag). No ids in labels.
- T3.2 `dry_run: true` writes nothing.
- T3.3 `dry_run: false` makes every set setting's current value equal its expected value (set-valued
  settings are replaced exactly, removals shown red), and touches nothing else: not-set settings and every
  "never on a tag" item are unchanged.
- T3.4 A contact with two tags that disagree gets the restrictive value; the change row names the tag
  that decided. A conflict is not applied and is listed as Conflict with the tags involved.
- T3.5 A setting the actor may not write is skipped and listed as not applied; the rest still apply.
  Gates match the contact page: agents and agent field access need `user_management.access_agents.edit`,
  stock visibility needs `inventory.stock.edit`, companies need superadmin; the same gates apply to
  setting them on a tag.
- T3.6 Per-contact transaction: one contact's write failure answers `failed` for it and leaves its
  values untouched; the others apply.
- T3.7 Portal form apply writes a `contact_portal_form_overrides` row only where the resolved form
  visibility differs from the expected value.
- T3.8 Agent apply upserts only the agents the tags set (never deletes another agent's grant).
- T3.9 Every apply writes audit rows naming the actor and the tag.

## T4 Cross-check (list)
- T4.1 Contacts list rows carry `tags` (names) and `tag_settings` = `matches` | `differs` | `none`
  (no tag sets anything), plus the differing settings (label, current, expected, deciding tag) and
  conflicts. A conflict alone counts as `differs`.
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
  settings named on hover); toolbar shows search, Tag and Tag settings as primary filters and every other
  filter (incl. No customers linked, No tags) inside the toolbar "Filters" popover with a count and active
  chips (`components/ui/data-grid-list-toolbar.tsx` filters kind custom); bulk Actions dropdown gets Add
  tag, Remove tag, Apply tag settings; row "..." menu gets Apply tag settings.
- T5.4 Add tag dialog: pick tag(s), then choose "Apply tag settings now" (preview shown) or "Don't apply
  now". Every failure ends in a visible error with the dialog still usable.
- T5.6 Contact detail page: Tags section lists the contact's tags, every differing setting and conflict,
  and an "Apply tag settings" action with preview.
- T5.5 Usable and not clipped at 375px and 1280px.

## T6 Migration
- T6.1 New tables `contact_tags` (uuid id) and `respond_contact_tags` (contact, tag, created_at,
  created_by); cascade on contact or tag delete; single alembic head; upgrade/downgrade/upgrade clean on
  a dev copy with row counts of `respond_contacts` and every access table unchanged.
- T6.2 Seeds no tags (owner creates the list).
