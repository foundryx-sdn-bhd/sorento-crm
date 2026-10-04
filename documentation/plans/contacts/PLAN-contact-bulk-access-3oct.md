# PLAN: Contact bulk access copy + access list filters (CONTACT-BULK-ACCESS)

Status: Review done - built, reviewer + security-reviewer clean after fix rounds, sandbox browser pass PASS; waiting on owner hand test and crew's masked dev rows for the card examples. Track: M (LEAD pattern), no migration, no new permission slug.

Card: `CARD-contact-bulk-access-3oct.md`. UAC: `contact-bulk-access-3oct-acceptance-criteria.md`.
Mock: `documentation/mockups/CONTACT-BULK-ACCESS/index.html` (v1, on hold).

## Slices

S1 Backend copy service + endpoint (A1)
- New `app/services/contact_access_copy_service.py`: `snapshot(db, contact_id) -> AccessSnapshot`,
  `diff(source, target) -> list[Change]`, `copy_access(db, source_id, target_ids, dry_run, actor_id)`.
  The access set is one tuple of facets, each a (read, write) pair; ACCESS-MODEL swaps the reveals and
  agents entries for roles and overrides (card 3c). Per-target `begin_nested()` savepoint, commit at the end
  of the route (`get_db` never commits).
- Writes reuse existing writers: `contact_field_reveal_service.set_granted_keys`, the access-types
  relationship, the switch columns, `ContactService.write_chatbot_profile_keeping_facts` for tier, direct
  `ContactAgentAccess` upsert/delete (match by contact id, then by phone for legacy rows).
- Route in `app/api/v1/user_management/contacts.py`, declared before `/{contact_id}` routes, guarded by
  `require_permission("user_management.contacts.edit")`.

S2 Backend list columns + filters (A2)
- `ContactService.list_contacts` grows the filters; `contact_to_response_dict` path adds `chatbot_tier` and a
  batched `cost_visible`; schema `RespondContactResponse` declares both (response_model drops undeclared).
- `access_differs_from` reuses S1's snapshot + diff over the candidate set (contacts table is ~250 rows; one
  batched snapshot query per facet, not per row).

S3 Frontend (A2.2-A2.3, A3)
- `contacts/lib/listQuery.ts` filters, `ContactAccessFilters.tsx`, columns in `ContactsList.tsx`,
  `BulkCopyAccessDialog.tsx` replacing `BulkCopySettingsFromContactDialog.tsx`, service + hook.

## Test list (red first)

Backend `tests/test_contact_bulk_copy_access.py` (Postgres, `blank_session`, HTTP through TestClient):
1. dry_run writes nothing; returns change rows (A1.1, A1.2, A1.10)
2. apply makes target equal to source on every facet, replace semantics removes extra reveal + agent (A1.3)
3. customers / memory / name untouched; source untouched (A1.4)
4. identical target -> unchanged, no write (A1.5)
5. source among targets -> skipped (A1.6)
6. unknown target -> failed, others applied (A1.7)
7. one target's write raises (monkeypatch) -> failed, its access unchanged, others applied (A1.8)
8. unknown source 404; 501 targets 422 (A1.9)
9. no edit permission 403 (A1.11)
10. legacy phone-only agent row -> no duplicate (A1.12)
11. preview == apply change rows (A1.13)

Backend `tests/test_contacts_access_filters.py`:
12. row carries chatbot_tier + cost_visible (A2.1)
13. each filter alone, and two combined (A2.3, A2.4)
14. access_differs_from == contacts a copy would change, excludes X (A2.5)
15. customer_id respects company scope (A2.6)

Frontend vitest:
16. listQuery filters round-trip URL (A2.3)
17. BulkCopyAccessDialog: preview renders counts and red/green rows; apply shows result table; HTTP error ends on
    the message, not a spinner (A3.2-A3.4)

Kill tests (reviewer): mutate replace -> add-only, drop the per-target savepoint, drop the scope predicate; each
must turn a test red.

## Contract (Phase 2, binding for tests and code)

`POST /api/v1/user-management/contacts/bulk-copy-access`, permission `user_management.contacts.edit`.

Request: `{"source_contact_id": str, "target_contact_ids": [str] (1..500, duplicates collapsed, order kept), "dry_run": bool}`.
Unknown source -> 404. Empty list or more than 500 -> 422.

Response 200:
```
{ "dry_run": bool,
  "source": {"id", "label", "summary": [{"label", "value"}]},   # label = name or phone; summary = one display
                                                     # line per facet in facet order (values joined ", ")
  "results": [ { "contact_id", "label" (null when not found),
                 "status": "changed" | "unchanged" | "skipped" | "failed",
                 "changes": [Change], "error": str | null } ],
  "counts": {"changed", "unchanged", "skipped", "failed"} }
Change = { "facet": str, "label": str, "before": any, "after": any, "added": [str], "removed": [str] }
```
Facets and labels, in this order:
`access_types` "Access types" (before/after = sorted codes; added/removed = access type NAMES),
`tier` "Tier" (string or null), `chatbot_stock_allowed` "Stock checks", `notify_salesman` "Notify salesman",
`packing_list_allowed` "Packing list", `chatbot_eta_offset_applied` "ETA buffer days",
`escalation_allowed` "Escalation" (booleans), `field_reveals` "Field reveals" (before/after = sorted granted keys;
added/removed = reveal LABELS), `agent_access` "Agent access" (before/after = sorted
`{agent_code, is_allowed, valid_from, valid_to}` (ISO or null); added/removed = agent NAMES, an agent whose
is_allowed or dates differ appears in both added and removed). Scalars carry `added: []`, `removed: []`. A facet
appears only when it differs. `status` = `changed` iff `changes` non-empty, for dry run and apply alike.
Skipped error text: "This is the source contact." Not found: "Contact not found."

List `GET /api/v1/user-management/contacts/` new query params: `access_type` (code), `tier`
(`dealer|office|end_user|none`), `cost`, `escalation`, `packing_list`, `stock` (each `yes|no`), `customer_id`,
`access_differs_from` (contact id). Each row gains `chatbot_tier: str|null` and `cost_visible: bool`.
