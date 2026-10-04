# PLAN - Packing list region (West / East Malaysia)

Status: built (packing-list side), review READY (reviewer + security-reviewer, 2 fix rounds), awaiting CI + owner hand test; contact side waits for ACCESS-MODEL. Owner answers 2 Oct 2026: Q1 = (b) contact region grant lives in the
ACCESS-MODEL access model; Q2-Q5 = (a); mock v2 approved (default West Malaysia only). Track: L / standard
(migration). Lane REGION-PACKING-LIST, branch `crew/region-packing-list`, one PR.
UAC: `region-packing-list-acceptance-criteria.md`. Card: `CARD-region-packing-list-2oct.md`.
Paths under `sorento_crm_backend/app/` unless shown.

## Dependency on ACCESS-MODEL (PR #1434, `documentation/plans/chatbot/PLAN-access-model-2oct.md` section C, AC-AM-25)

Shape agreed with that lane 2 Oct 2026 (names accepted, no renames).

| Part | Owner | When |
|---|---|---|
| `inbound_shipments.regions`, `attachments.regions`, upload/create/detail UI, list column + filter, backfill | this lane | now |
| Region filter seam taking a region set (`IncomingStockService(regions=)`, `earliest_packing_list_shipment(regions=)`) | this lane | now |
| One reader `eta_policy.contact_regions(db, resolved_contact_id) -> frozenset[str]` | this lane | now, returns `{west}` for every contact |
| `respond_contacts.regions` text[] NOT NULL default `{west}` (CHECK non-empty, subset of west/east) in its S1 migration, `EffectiveAccess.regions`, Regions control + `regions` on `/api/v1/system/chatbot/contacts/{id}/access` | ACCESS-MODEL | its merge |
| Repoint the body of `contact_regions` at `effective_access(...).regions`, signature unchanged | ACCESS-MODEL S5 | its merge |

This lane never adds the `respond_contacts` column, so the two migrations cannot collide in either merge order.

Grant rule: values `west` / `east`; contact default `{west}`; expansion `east` held => `{east, west}` else `{west}`;
duplicate rows = intersection of raw sets then expand; unresolved / empty = `{west}`; no contact in play (staff, bare
API key) = no filter.

Interim behaviour (until ACCESS-MODEL lands): every chatbot contact is West only, so an East-only packing list is hidden
from all contacts (fail closed); West and West + East packing lists answer as today.

## Schema (one additive Alembic revision)
- `inbound_shipments.regions` text[] NOT NULL server_default '{west}', CHECK `cardinality(regions) >= 1 AND regions <@ '{west,east}'`. Backfill = default (Q2).
- `attachments.regions` text[] NULL, same CHECK when not NULL (set only for a Packing List upload; read by external create).

## Backend
1. Models + schemas: `InboundShipment.regions` (`models/procurement.py:128`), `InboundShipmentBase/Update` + responses
   (`schemas/procurement.py:351-465`), `PackingListHeader.regions` optional (`schemas/external/procurement.py:19`),
   `Attachment.regions`. Validation: non-empty, subset of {west, east}, de-duplicated; else 422.
2. Writes: external create (`api/v1/external/packing_lists.py:112`) regions = payload, else attachment, else {west};
   staff create/update (`api/v1/procurement/packing_lists.py:398,420`); staff list filter `region` (array contains)
   (`:224`); attachment create form field `regions` (`api/v1/resources/attachments.py:730`); proforma convert keeps the default.
3. Seam: `eta_policy.visible_regions(held) -> frozenset` (expansion) and `contact_regions(db, resolved_contact_id)`;
   `ContactEtaRules` gains `regions`; `UNRESOLVED.regions = {west}`.
4. Filter in SQL: `IncomingStockService(db, regions=None)`; `_region_filter(regions)` = `InboundShipment.regions && regions`
   ANDed beside every `_not_draft_shipment_filter()` (`services/incoming_stock_service.py:204,448,570,625,790,931,1044`);
   `earliest_packing_list_shipment(db, ids, regions=None)`. Routes (`api/v1/incoming_stock.py` `_Contact`) build the
   service with the contact's regions (None when no contact). Stock ask passes them (`services/inventory_service.py:1629`).

## Frontend
- `AttachmentUploadDialog.tsx`: Regions SearchableMultiSelect (at least one, West preselected) when the type code is `packing_list`; sends `regions`.
- `PackingListsList.tsx`: Regions column (Badge per region) + filter; `PackingListForm.tsx` + `PackingListDetailsTab.tsx`:
  Regions multi-select; `packingList.types.ts` + `packingListService.ts`.
- Contact Regions control: ACCESS-MODEL (its Access tab), not this lane.

## Tests (red first, `test(red):` commit alone)
- `tests/test_packing_list_regions.py`: AC-RPL-1,3,4,5,6.
- `tests/test_incoming_region_filter.py`: AC-RPL-9..13,15,16 through the seam with a region set (service + routes,
  totals/paging); interim `contact_regions` = {west}.
- vitest: upload dialog Regions (only for Packing List, West preselected, sent), list column, detail field.

## Follow-ups
- Container Status workbook attachment lists every container (Q4: fine as is).
- Chatbot turn with a blank contact_id calls incoming tools unfiltered (inherited fail-open, lanes/business/fetch.py:1216-1219); fix in the chatbot caller.
- Re-upload (owner ruling 2 Oct): a packing list uploaded onto an existing container sets the container's regions to the ones the user chose in the upload dialog (no keep-existing, no draft special case); an upload stating no regions leaves them. Prefill from the matched container is not possible: the dialog only has the file, the container is matched later by n8n.

## Migration check + hand test
Idempotent SQL at `crew/state/migrations/REGION-PACKING-LIST.sql` via `crew migrate`; checked on the dev copy before the
hand test. Security review + reviewer + browser 1280/375.
