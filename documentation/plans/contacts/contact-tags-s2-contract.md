# CONTACT-TAGS S2 contract (settings on a tag, expected vs current, Matches / Differs)

Binding for tester and coder. UAC: `contact-tags-16oct-acceptance-criteria.md` (settings table + merge
rules at the top, T1.4, T3.4 merge part, T3.5 gate part for setting on a tag, T4.1, T4.2 tag_settings,
T4.3, T4.5, T5.2, T5.3 Tag settings column, T5.6 without the apply button). Apply is S3.
Paths below are under `sorento_crm_backend/app/` unless they start with `sorento_crm_frontend/`.

## Tag `settings` JSON (stored on `contact_tags.settings`)

A key present = that setting is "set" on the tag; absent = "not set". Map sections are set per inner key.

```
access_types: [code]                       brands: "all" | [brand_id]
companies: [company_id]                    market_segments: [code]
regions: ["west"|"east"] (non-empty)
chatbot: { chatbot_allowed, chatbot_stock_allowed, escalation_allowed, packing_list_allowed,
           notify_salesman, chatbot_eta_offset_applied, chatbot_skip_period_questions,
           chatbot_files_as_links: bool,
           memory_level: "off"|"conversation"|"episodes"|"full",
           tier: "dealer"|"office"|"end_user" }          (each inner key optional)
portal_forms: { <form_type>: bool }        requires_registered_project: bool
field_reveals: { <field_key>: bool }       agents: { <agent_id>: bool }   (true = allow)
agent_fields: [ {agent_code, resource, field_key, is_allowed: bool} ]
media: { image: {is_allowed, monthly_limit?, languages?}, voice: {is_allowed, monthly_limit?,
         max_clip_seconds?, languages?} }      (each modality optional)
attachment_types: [attachment_type_id]
stock_visibility: { mode, warehouse_ids: [..]|null, excluded_warehouse_ids: [..]|null, hide_zero_locations }
spec_visibility: { spec_keys: [..]|null, excluded_spec_keys: [..]|null }
outbound_enabled: bool
```

Validation (PATCH `/contact-tags/{id}` with `settings`, full replace of the object): unknown section or
inner key, wrong type, unknown code/id against the live catalogs (access types, brands, companies,
segments, `portal_service.py` form kinds, `FIELD_REVEAL_KEYS`, agents, attachment types, warehouses,
memory levels `services/chatbot/turn/memory.py:35`, tier domain) -> 422 naming the key. Empty `regions`
-> 422.

Gate for CHANGING a section on a tag (compare old vs new settings; unchanged sections need no gate, so a
contacts.edit user can rename a tag that carries agent settings): agents + agent_fields need
`user_management.access_agents.edit`; stock_visibility needs `inventory.stock.edit`; companies need
superadmin; outbound_enabled needs `system.respond_workspaces.edit`; everything else contacts.edit.
Missing gate -> 403 naming the section; nothing saved.

## Expected value (merge across the contact's tags; only tags that set the key take part)

| Kind | Rule |
|---|---|
| bool, restrictive = Off | AND (Off wins): chatbot_allowed, chatbot_stock_allowed, escalation_allowed, packing_list_allowed, notify_salesman, portal_forms.*, field_reveals.*, agents.*, agent_fields, media.*.is_allowed, outbound_enabled |
| bool, restrictive = On | OR (On wins): chatbot_eta_offset_applied, requires_registered_project, stock hide_zero_locations |
| no order | equal values merge; different values = CONFLICT: chatbot_skip_period_questions, chatbot_files_as_links |
| tier | office loses to dealer / end_user; dealer vs end_user = CONFLICT |
| memory_level | lowest of off < conversation < episodes < full |
| sets | intersection: access_types, companies, market_segments, attachment_types, brands ("all" is neutral), regions (empty result = CONFLICT), media languages, stock warehouse_ids (null = all, neutral), spec spec_keys (null = all, neutral) |
| unions | stock excluded_warehouse_ids, spec excluded_spec_keys |
| numbers | lowest: media monthly_limit, voice max_clip_seconds (absent = neutral) |
| stock mode | most restrictive: availability > compact > detailed (`services/stock_visibility.py:159-200`) |

Each expected item records `decided_by` (tag names whose value won, sorted) and, for a conflict,
`conflict_tags` with each tag's value.

## Current value (what the runtime reads now; per section)

| Section | Reader |
|---|---|
| access_types, companies, market_segments | pivots (`contact_access_copy_service.py:217,273,278` batch the same queries) |
| brands | `respond_contacts.brand_ids` (NULL or [] = "all") |
| regions, chatbot switches, memory_level (raw column), tier (`chatbot_profile.tier`), requires_registered_project, outbound_enabled | the contact row |
| portal_forms | `services/portal_form_visibility_service.py:100` resolve_visible_form_types (membership per kind) |
| field_reveals | `services/contact_field_reveal_service.py:105` granted_keys_for (batched, effective) |
| agents | `services/field_access.py:331` contact_agent_codes (allowed, in window, active); allow = code in set |
| agent_fields | `services/field_access.py:365` allowed_fields_for(agent_codes=[code]) membership |
| media | `services/media_access_service.py:243,262,286` row + effective limit + clip seconds; no row = Off |
| attachment_types | `services/contact_attachment_access.py:34` granted_type_ids (raw grants) |
| stock_visibility | `services/stock_visibility.py:229` resolve_policy (effective) |
| spec_visibility | `services/spec_visibility.py:179` resolve_policy (effective) |

Compare set values as sets; a contact "matches" a key when current == expected. A conflict counts as a
difference (status `differs`) but has no expected value.

## Routes and fields

- GET `/contact-tags`, GET `/contact-tags/{id}`: add `differs_count` = holders whose status is `differs`
  on keys THIS tag sets (T4.5: equals the list filter `tag=<id>&tag_settings=differs` count).
- GET `/contacts/{id}/tag-settings` -> `{status: "matches"|"differs"|"none", tags: [{id,name}],
  items: [{section, key, label, current, expected, decided_by: [name], conflict: bool,
  conflict_tags: [{name, value}], differs: bool}]}`; `none` when no tag of the contact sets anything.
  Labels and values are human (agent names, form names, field labels, warehouse names), never ids.
- Contacts list rows (`GET /contacts`, declared on `RespondContactResponse`): `tag_settings:
  {status, differ_count, differ_labels: [label]}` for the page rows (batched per page next to the S1
  `tags` map). Filter `tag_settings=differs|matches` (contacts with status `none` match neither), computed
  over candidates holding at least one tag with settings, refusing above 5000 like
  `services/contact_access_copy_service.py:124`.
- Performance: per-contact resolvers are acceptable at today's size (104 dev contacts). Trigger to
  batch them: list p95 above 1 s on the dev copy, measured in the browser pass.

## Frontend

- Tag detail page (`sorento_crm_frontend/app/(protected)/user-management/contact-tags/[id]/`): Access
  settings section with the 15 groups in the mock's order (mock views C / C edit). View and edit are the
  same layout. Each group: "Not set" (muted) or its values; edit adds a "Set on this tag" switch per group
  and the group editor; bool keys use the 3-state Not set / On / Off (`components/ui/toggle-group.tsx`,
  agents Not set / Allow / Deny). Group editors reuse the contact page editors where they can take a
  value + onChange; where a contact section is wired to its own fetch/save, build the editor from the same
  primitives (SearchableMultiSelect, toggle-group, Switch) and say so in the commit. "Not on a tag" block
  greyed with reasons (mock). Save = PATCH settings; 403/422 shows the named section and keeps the edit.
- Tag list (mock view B): Differs column = `differs_count`, links to the contacts list with
  `tag=<id>&tag_settings=differs`.
- Contacts list: Tag settings column (Badge Matches / "Differs: n", title lists `differ_labels`, "-" for
  none) and a primary Tag settings select (Any / Matches / Differs), URL-synced.
- Contact detail page: Tags section (mock view G): tags, then each differing item (label, current,
  expected, decided by) and conflicts in amber; empty state when status is matches or none. The "Apply tag
  settings" button arrives in S3.
