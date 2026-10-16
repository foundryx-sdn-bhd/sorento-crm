# PLAN: contact tags + tag access settings (CONTACT-TAGS, 16 Oct)

Status: Plan - owner answered card 16 Oct; mock v2 filed (filters tidy, every setting on a tag), awaiting owner OK; UAC v2 drafted. Track: full (size L: migration, access writes, new list surface). No code yet.

Lane: `crew/contact-tags`. Rebases on CONTACT-ACTIONS-COPY (branch `claude/contact-actions-copy-ufwe9b`) once it lands.

## Journey

Owner, 16 Oct: cannot tell dealers, retail salespersons, project salespersons and internal staff
apart in Users & Access > People; cannot mass-configure access (e.g. "all retail dealers: no stock
inquiry, no purchase request"); cannot cross-check who has what.

## What exists (measured, origin/main 68e0e036)

- List: `sorento_crm_frontend/app/(protected)/user-management/contacts/components/ContactsList.tsx`
  (DataGrid :580, toolbar :598, filters `ContactAccessFilters.tsx:108-213`, bulk actions :626-676).
- Contact row `respond_contacts` (`app/models/access.py:230`); switches on the row (:266-305).
- Access types `contact_access_types` M2M (`access.py:69,92`): brand x dealer/office entitlements,
  carry no switch defaults. Dev: 48/104 contacts hold none, 24 hold all 7.
- Market segments `market_segments` M2M (`access.py:126,161`): retail 34 / project 30 on dev;
  drive CS routing + portal forms (segment `portal_form_types` :150).
- Bulk copy access + "Access differs from <contact>" (`app/services/contact_access_copy_service.py:666,753`;
  route `app/api/v1/user_management/contacts.py:208`, filter :112): whole access set, REPLACE, preview.
- No tags, no preset/role concept on main. ACCESS-MODEL PR #1434 (draft, roles -> domains -> fields +
  overrides) is open and stalled since 11 Oct.

## Proposed (pending card answers)

1. Tags: `contact_tags` catalog + `respond_contact_tags` M2M; admin list cloned from
   `contact-access-types/`; list column (PillOverflow), filter, bulk Tag / Untag. Explicit only.
2. Tag access settings: each tag may store a PARTIAL set of access values (only ticked settings).
   "Apply to N contacts" = preview + write through the copy service's diff/write, limited to those
   settings. No live inheritance.
3. Cross-check: column "Tag settings" (Matches / Differs n), filter "Differs from its tag settings",
   export carries tags.

## Design (build contract, after owner OK of mock v2)

Schema (one migration, `ctg_0001_contact_tags`, re-parented onto main's head at PR time):
- `contact_tags`: `id` UUID PK, `name` (unique on lower(name)), `description`, `is_active`, `sort_order`,
  `settings` JSONB not null default `{}`, `created_at/updated_at`, `created_by/updated_by`. Seeds nothing.
- `respond_contact_tags`: `contact_id` Text FK respond_contacts ON DELETE CASCADE, `tag_id` UUID FK
  contact_tags ON DELETE CASCADE, `created_at`, `created_by`; PK (contact_id, tag_id); JUNCTION entry in
  `tests/test_schema_uuid_id_principle.py` (l.44-55 pattern). Global like contacts (no company column).

Tag `settings` JSON: one key per section; a section key present = set. Map sections (chatbot switches,
portal forms, field reveals, agents, agent field access, media) are set per inner key. Validation against
the live catalogs (access types, brands, companies, segments, agents, attachment types, warehouses).

Apply writes through the SAME service functions the contact page uses (so validation matches), inside a
per-contact savepoint, then one explicit `log_audit` row per contact naming the tag(s) (several paths are
not covered by the ORM audit listener: access-type pivot, segments, media insert, chatbot_profile):

| Section | Writer (sorento_crm_backend/app/...) | Gate for set-on-tag AND apply |
|---|---|---|
| Access types | `services/contact_access_type_service.py:249` set_contact_access_codes | contacts.edit |
| Brands | `services/contact_brand_scope.py:257` set_contact_brands | contacts.edit |
| Companies | `services/contact_service.py:419` set_contact_companies | superadmin |
| Market segments | `services/market_segment_service.py:250` set_contact_segments | contacts.edit |
| Regions, switches, memory, tier | inline in `api/v1/user_management/contacts.py:389-449`; extract to a service fn; tier via `contact_service.py:521` (keep language: see bug) | contacts.edit |
| Portal forms | inline in `api/v1/user_management/contact_portal_forms.py:97`; extract | contacts.edit |
| Requires registered project | `contact_service.py:376` update_contact | contacts.edit |
| Field reveals | `services/contact_field_reveal_service.py:135` (full-list replace: apply must merge per key) | contacts.edit |
| Agents | `services/user_service.py:1831/1877` create/update_contact_access (upsert per agent, never delete others) | access_agents.edit |
| Agent field access | `services/user_service.py:2738` set_field_access (upsert only sent keys) | access_agents.edit |
| Media | `services/media_access_service.py:453` upsert_access | contacts.edit |
| Attachment types | `services/contact_attachment_access.py:88` set_grants | contacts.edit |
| Stock visibility | `services/stock_visibility.py:422` upsert_policy | inventory.stock.edit |
| Spec visibility | `services/spec_visibility.py:317` upsert_policy | contacts.edit |
| Messaging | `services/respond_outbound_service.py:155` set_contact_outbound (never sends) | system.respond_workspaces.edit |

A section the actor cannot write is skipped and listed "not applied" (copy-service precedent,
`contact_access_copy_service.py:87`). Current values are read with the same resolvers the runtime uses.

List: tag filter = `exists()` on `respond_contact_tags` in `contact_service.py:228-302` (copy the
access_type block l.240-246); `tags=none`; `tag_settings=differs|matches` computed in Python over
candidates holding a tag with settings (cap 5000, same as `contact_access_copy_service.py:124`). Row
`tags` + `tag_settings` via a batched per-page map next to `brand_refs_by_contact`
(`contact_service.py:184-199`), fields declared on `RespondContactResponse` (`app/schemas/user.py:45-90`)
and asserted in a test (response_model drops undeclared fields).

Slices (one coder kept alive, tester red-first per slice):
1. Schema + tag CRUD + admin page + tagging (bulk-tags without apply) + Tags column / Tag / No tags filters.
2. Settings on tag (validation + gates) + expected/current/diff + Tag settings column/filter + contact Tags section.
3. Apply (bulk, per tag, per contact; add-tag apply choice) + preview dialog.
4. Filters popover + Actions items (after #1732 merges; rebase).

Out of lane (reported): login-only contact write routes (`contacts.py:246,264,310,582,852,928`);
chat language erased on Chatbot card save (`contactChatbotService.ts:218`).

## Decision log

- 16 Oct owner: Q1a, Q2a, Q3 everything (flag nonsense items), Q4a, Q5a with apply / don't apply
  choice + later per-contact and bulk "Apply tag settings". Filters: primary + More filters. See card.

## Phases

0. Card + mock approved (owner).
1. FE mock against fixtures.
2. Tester red tests from UAC -> coder green; migration on dev copy.
3. Reviewer + security-reviewer + browser 1280/375; hand test.
