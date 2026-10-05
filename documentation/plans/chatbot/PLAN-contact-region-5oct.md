# PLAN - Contact region (West / East Malaysia)

Status: card filed to crew 5 Oct 2026, awaiting answers (Q1 control shape, Q2 copy access, Q3 list column).
Track: M (migration, existing screen). Lane CONTACT-REGION, branch `crew/contact-region`, one PR.
UAC: `contact-region-acceptance-criteria.md`. Paths under `sorento_crm_backend/app/` unless shown.

Owner ask 5 Oct: "How do I set the region of internal contacts, like West or East Malaysia?" Owner chose this
small lane instead of waiting for ACCESS-MODEL (#1434). It takes over the contact half of
`documentation/plans/procurement/PLAN-region-packing-list-2oct.md` ("Dependency on ACCESS-MODEL" table): ACCESS-MODEL
drops its own `respond_contacts.regions` column and Regions control and repoints at this one.

## Schema (one additive Alembic revision, `creg_0001_contact_regions`, down_revision = main head)
- `respond_contacts.regions` text[] NOT NULL server_default `'{west}'`,
  CHECK `ck_respond_contacts_regions`: `cardinality(regions) >= 1 AND regions <@ ARRAY['west','east']::text[]`
  (same shape as `ck_inbound_shipments_regions`, `models/procurement.py:289`). Backfill = default.
- Model `RespondContact.regions` (`models/access.py:230`).

## Backend
1. `ContactChatbotUpdate` (`api/v1/user_management/contacts.py:361`) gains `regions: list[Literal["west","east"]] | None`,
   validator: non-empty, de-duplicated, canonical order (west, east); explicit null -> 422. Absent = leave alone.
   Handler (`:377`) sets `contact.regions`.
2. Response: `RespondContactResponse.regions: list[str] = ["west"]` (`schemas/user.py:~75`) and the hand-built dict in
   `ContactService.contact_to_response_dict` (`services/contact_service.py:~817`).
3. `eta_policy.contact_regions` (`services/eta_policy.py:72`) reads the column; missing row -> `{west}`. Signature kept.
4. Copy access (Q2): `contact_access_copy_service.py` copies `regions` beside `SWITCHES` (`:46`).
5. Audit: automatic (RespondContact has no `__audit_skip__`, `services/audit_service.py:100`).

## Frontend
- `contact.types.ts` + `contactChatbotService.ts`: `regions` read + sent on the chatbot PUT.
- `ContactChatbotSection.tsx`: Regions control in the switch grid beside "Packing list allowed" (`:225`), options from
  `lib/packing-list-regions.ts`; `SearchableMultiSelect` (Q1 a) or `SearchableSelect` West only / West + East (Q1 b).

## Tests (red first, `test(red):` commit alone)
- `tests/test_contact_regions.py`: AC-CR-1..10 (schema, PUT/GET, 422s, 403, audit, contact_regions).
- `tests/test_incoming_region_filter.py` style: AC-CR-11..12 through `rules_for_contact` with a stored East contact.
- vitest `ContactChatbotSection.regions.test.tsx`: AC-CR-14..16.

## Not in scope
Contact access export, contacts list Region column (Q3), guessing region from names (owner rule).
