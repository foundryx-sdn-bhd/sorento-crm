# PLAN: AutoCount SO 'Transferable' flag; non-transferable SOs excluded from Stock Debt

Status: Review (fix round 1 Oct: vitest key-parity fixture, unknown-field log guard test, owner decision (a) recorded). Track: standard (carries a migration, so not small-fix).
UAC: `so-transferable-acceptance-criteria.md` alongside.

Lane: SO-TRANSFERABLE. Owner ask, 1 Oct 2026: AutoCount sales orders carry a header column
`Transferable` (T/F), e.g. SO421824 F, SO422024 F, SO422049 T, SO422051 T, SO422058 F. Pull it into
sorento-crm; SOs with Transferable = F must NOT be considered in Stock Debt.

## 0. What exists (scouted, PR #1421 comment "STEP 1 scout report")

- AutoCount SOs arrive by ESB push (foundryx-shared-service `SorentoSink`) to
  `POST /api/v1/external/ingest/sales_orders` -> `DocumentIngestService`
  (`app/services/document_ingest_service.py`, `DOCUMENT_SPECS["sales_orders"]`), payload
  `CanonicalSalesOrder` (`app/schemas/canonical_documents.py`). No SO pull lane exists.
- The payload has no Transferable field today; nothing in this repo names it. The shared service
  must start sending it: **ss lane required** (SorentoSink reads `SO.Transferable` and sends
  header `transferable`).
- Stock Debt reads SO demand in `StockDebtService._products` (candidate read) and
  `StockDebtService._demand`. `_demand` is also the fulfilment board's ladder input via
  `project_supply_service.planning_assignments` (R21: one assignment).

## 1. Decisions

- D1 Column `sales_orders.is_transferable BOOLEAN NULL`, no default. NULL = AutoCount never stated
  it (every pre-existing row, every Excel / manual / Order Inquiry SO); treated as transferable.
  Only an explicit FALSE is excluded (`is_transferable IS NOT FALSE`). (Crew decision, 1 Oct 2026.)
- D2 Payload `CanonicalSalesOrder.transferable: Optional[bool]`. Pydantic's lax bool parsing
  already accepts AutoCount's `T`/`F` (case-insensitive) alongside its other fixed boolean words
  (`true`/`false`, `yes`/`no`, `1`/`0`, ...); anything else fails the record as malformed. Absent or null leaves the stored value untouched (same rule as
  `debtor_code`), so an older ESB build that does not send it never blanks a stated flag.
- D3 AutoCount-owned. Written only by the ingest. Not in `SalesOrderUpdate` (the SO edit PUT
  ignores it), not editable in the UI. Read-back (`POST /external/read/sales_orders`) returns it as
  `transferable`. The DO-OWNERSHIP registry is DO-only (`order_service.py`); an SO has no
  ownership registry and one field does not earn one.
- D4 Stock Debt excludes F in BOTH reads (`_products` and `_demand`), one helper
  `StockDebtService._transferable()`. Owner ruling (b), 1 Oct 2026: F leaves the SHARED
  assignment, so the fulfilment board's ladder (`ProjectSupplyService.planning_assignments`) skips
  F lines too and R21 stays one reader. The board's own pile reads (`_group_pile_members`,
  `_check_group_borrow`, `_pile_book`, `_pile_read` in `project_supply_service.py`) apply the same
  predicate, `demand.is_transferable_order()`, so an F line never ranks as competing demand
  either. The board still LISTS an F order's lines (its row read is unchanged). Consequence: an F order gets no stock reserved anywhere
  until AutoCount flips it to T. F means "not confirmed yet for the queue" (owner).
- D5 Owner decision (a), 1 Oct 2026: F does NOT leave `is_open_demand()` / `scm.committed_v`
  (reorder, netting, coverage, location stock, SPO conversion, container requests), the
  outstanding report, the sales report or the chatbot. An F order is still real demand for
  planning and reporting; F only keeps it out of Stock Debt and the fulfilment board's
  reservation ladder (D3, D4).
- D6 UI (owner ruling, 1 Oct 2026): a Transferable column on the SO list (Yes / No / Not stated
  `Badge`, not sortable) with a Transferable filter (`transferable=yes|no|unknown`), and a
  read-only Transferable field on the SO detail General tab, the same in view and edit, with a
  muted "From AutoCount" hint on AutoCount-sourced orders only. Reuses the existing column, filter and Field components; no mock.

## 2. Slices

1. Migration `sotr_0001_so_is_transferable` (additive, nullable) + model column.
2. Ingest: schema field, `_header_values` fill-when-sent, read-back.
3. Stock Debt exclusion (`_products`, `_demand`).
4. SO list + detail: `is_transferable` on the `SalesOrder` response, list filter, FE column / filter / field.

## 3. Test list (red first)

`sorento_crm_backend/tests/test_so_transferable_ingest.py` (AC-TR-1..5),
`sorento_crm_backend/tests/scm/test_so_transferable.py` (AC-TR-6..9),
`SalesOrdersList.transferable.test.tsx` + `SalesOrderDetail.transferable.test.tsx` (AC-TR-10).
Browser evidence: `evidence/so-transferable/`.

## 3b. Ingest ignores unknown fields (owner decision, 1 Oct 2026, same lane)

`extra="forbid"` lived on `canonical_masters._Canonical`, `canonical_documents._SalesOrderExternalRef`,
`_CanonicalLine`, `CanonicalBillingDocumentLine` and `stock_balance_ingest_service._StockBalanceRecord`.
All now take `INGEST_MODEL_CONFIG` (`extra="ignore"`) plus a before-validator that notes unknown key
NAMES into a per-request set; `POST /external/ingest/{entity}` logs them once as
`ingest.unknown_fields`. Declared fields stay strict. Dropping is not writing. Test:
`tests/test_ingest_ignores_unknown_fields.py`; the tests that pinned the old reject contract now pin
drop-not-write.

## 4. Follow-ups (not in this lane)

- ss lane: SorentoSink sends header `transferable` (AutoCount `SO.Transferable`). Deploy order no
  longer matters: owner decision (1 Oct 2026) made every `/external/ingest/*` schema DROP unknown
  keys and log their names once per request (`app.schemas.ingest_extras`) instead of refusing the
  record, so an early send is ingested without the flag and a late one simply leaves it NULL.
