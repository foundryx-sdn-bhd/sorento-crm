# CONTACT-TAGS S1 contract (schema, tag catalog, tagging, list)

Binding for tester and coder. UAC: `contact-tags-16oct-acceptance-criteria.md` (T1, T2 without apply,
T4.1 tags only, T4.2 tag filters, T4.4, T5.1, T5.3 Tags column + Tag filter + No tags, T6).
Settings, Matches/Differs and apply are S2/S3: in S1 `settings` is stored and returned as `{}` and not
validated beyond "must be an object".

## Schema (alembic `ctg_0001_contact_tags`, down_revision = main head `sds_0001_stock_debt_snapshots`)

`contact_tags` (model `ContactTag`, `sorento_crm_backend/app/models/access.py`, next to ContactAccessType)
- `id` UUID(as_uuid=False) PK, default uuid4
- `name` String(100) not null; unique index on `lower(name)` (`uq_contact_tags_lower_name`)
- `description` Text null
- `is_active` Boolean not null default true
- `sort_order` Integer not null default 0
- `settings` JSONB not null server_default `'{}'`
- `created_at`, `updated_at` (server default now), `created_by`, `updated_by` Text null

`respond_contact_tags` (association Table, same file)
- `contact_id` Text FK `respond_contacts.id` ON DELETE CASCADE
- `tag_id` UUID FK `contact_tags.id` ON DELETE CASCADE
- `created_at` not null default now, `created_by` Text null
- PK (contact_id, tag_id); index on tag_id
- Listed under JUNCTION in `tests/test_schema_uuid_id_principle.py` EXEMPTIONS (l.44-55 pattern).

Contacts stay global (no company column; `__company_shared__` not needed: no company scoping on these).

## Routes (prefix `/api/v1/user-management`, router mounted like contact-access-types)

| Method + path | Body | Answer | Gate |
|---|---|---|---|
| GET `/contact-tags?page=&limit=&search=&is_active=` | - | `ListResponse` of ContactTag (limit `le=1000`), order sort_order, name | contacts.view |
| GET `/contact-tags/{id}` | - | ContactTag, 404 unknown | contacts.view |
| POST `/contact-tags` | `{name, description?, is_active?, sort_order?}` | 201 ContactTag | contacts.edit |
| PATCH `/contact-tags/{id}` | any subset of the above | 200 ContactTag | contacts.edit |
| DELETE `/contact-tags/{id}` | - | 204; links removed by cascade; no contact access value changes | contacts.edit |
| PUT `/contacts/{contact_id}/tags` | `{tag_ids: [..]}` | 200 `{tags: [{id,name}]}` replace set | contacts.edit |
| POST `/contacts/bulk-tags` | `{contact_ids: [..], add_tag_ids: [..], remove_tag_ids: [..], dry_run: bool=true}` | 200 BulkTagResult | contacts.edit |

ContactTag = `{id, name, description, is_active, sort_order, settings, contact_count, created_at, updated_at}`.
`contact_count` = number of contacts holding it.

Validation: name trimmed; blank -> 422; duplicate (case-insensitive, trimmed, also against itself on
PATCH of another tag) -> 409 naming the clashing name; unknown tag id in PUT/bulk -> 422 naming it;
`contact_ids` empty -> 422, more than 500 -> 422; a tag in both add and remove -> 422.

BulkTagResult = `{dry_run, results: [{contact_id, label, status: changed|unchanged|failed,
tags_before: [names], tags_after: [names], error}], counts: {changed, unchanged, failed}}`, rows in
request order; unknown contact id -> that row `failed` ("contact not found"), others still apply;
adding a held tag / removing an absent one = no-op (`unchanged` when nothing else moves);
`dry_run: true` writes nothing; preview equals apply.

Deferred delete (D7): register `contact_tag.delete` in `sorento_crm_backend/app/services/record_actions.py`
like `market_segment.delete` (l.1154, l.1463): entity_types `("contact_tag",)`, WINDOW_DESTRUCTIVE,
permission `user_management.contacts.edit`, execute = the same service delete the DELETE route runs.

No tag is ever assigned by sync (`app/services/respond_sync_handler.py:261`), the external upsert
(`app/api/v1/external/respond_contacts.py:105`) or create_contact (`app/services/contact_service.py:342`).

## Contacts list (`GET /api/v1/user-management/contacts`, `app/api/v1/user_management/contacts.py:88`)

- New query params: `tag` (repeatable, tag ids, any-of), `tags=none` (contacts holding no tag).
  Implemented in `_apply_access_filters` (`app/services/contact_service.py:228-302`, copy the access_type
  `exists()` block l.240-246); combine with every existing filter.
- Each row gains `tags: [{id, name}]` ordered by tag sort_order then name, via a batched per-page map next
  to `brand_refs_by_contact` (`contact_service.py:184-199`); field declared on `RespondContactResponse`
  (`app/schemas/user.py:45-90`). GET `/contacts/{id}` returns `tags` too.

## Frontend (sorento_crm_frontend)

- Users & Access sidebar: "Contact tags" next to "Contact access types" (same nav config + permission
  pattern), page `app/(protected)/user-management/contact-tags/` cloned from
  `app/(protected)/user-management/contact-access-types/` (list, hooks, services); DataGrid fixed layout +
  resizable + Columns toggle; Add/Edit tag modal; delete via `components/common/DeferredActionButton.tsx`
  with `contact_tag.delete`; row opens `/user-management/contact-tags/[id]` detail page (PageHeader,
  RecordNavigation, read-only metadata under the header; S1 shows a Contacts section listing holders with
  an empty state; settings section arrives in S2).
- Contacts list `app/(protected)/user-management/contacts/components/ContactsList.tsx`: Tags column
  (`components/common/PillOverflow.tsx`, like access_types :347; explicit size, truncate + title); Tag
  filter (`components/common/SearchableMultiSelect.tsx`, clearable) and "No tags" checkbox next to "No
  customers linked" (S4 moves filters into the popover); URL-synced like the other filters
  (`contacts/lib/listQuery.ts:34-72`); bulk actions "Add tag (n)" / "Remove tag (n)" (contacts.edit) opening
  one dialog component (pick tags, preview step from `dry_run`, apply). Keep these additions in their own
  files where possible: PR #1732 moves the bulk actions into `contacts/lib/contactBulkActions.ts` and S4
  rebases onto it.
- Contact edit dialog `contacts/[id]/components/ContactEditDialog.tsx`: Tags multi-select (saves via
  PUT `/contacts/{id}/tags`); contact detail page shows tags as Badges near access types
  (`contacts/[id]/page.tsx:142-148`).
- Service layer: `contacts/[id]/services/contactTagService.ts` (or the contact-tags module's services/)
  through `lib/api-client`, `extractApiError`, `buildDataGridParams`; hooks with invalidate + toast.
- Export: the toolbar's client export includes the Tags column as names joined by ", ".
