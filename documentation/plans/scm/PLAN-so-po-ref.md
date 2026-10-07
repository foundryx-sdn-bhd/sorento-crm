# PLAN: SO PO ref (SO-PO-REF)

Status: building (lane SO-PO-REF, size L: migration + ingest + chatbot + SO screens). UAC: `so-po-ref-acceptance-criteria.md`.

## Owner rulings (7 Oct 2026)

- Q1 (b): a SEPARATE canonical field `po_ref`, fed from AutoCount `UDFNote` by the shared-service mapping. `ref` keeps feeding the Project label unchanged (`app/services/document_ingest_service.py:1051-1067`).
- Q2: column `sales_orders.po_ref`, label "PO ref", on the SO list (column + search) and SO detail General tab.
- Q3 (a): no backfill in this PR; the raw ref/UDFNote was never stored in Sorento. The owner re-pushes open SOs from shared service after release.
- Q4: audience decides. Dealer/customer "PO" = their PO (this field, scoped to their own SOs). Staff "PO" = Sorento's supplier PO (existing path, untouched).
- Q5 (a): the existing list search box matches PO ref.

## Slices (one PR)

1. **Migration** `sopr_0001_so_po_ref` on head `aig_0001_chatbot_allowed`: `ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS po_ref varchar(255)`, `po_ref_key varchar(255)`, partial index `ix_sales_orders_po_ref_key` WHERE `po_ref_key IS NOT NULL`. Additive only. Model `app/models/order.py:599` gets both columns.
2. **Key fold** `po_ref_key(value)`: upper-case, strip all whitespace and hyphens (same fold as `so_status.so_key`, `app/services/chatbot/so_status.py:38`), None for blank. One helper, used at write AND lookup.
3. **Ingest**: `CanonicalSalesOrder.po_ref: Optional[str] = Field(None, max_length=255)` (`app/schemas/canonical_documents.py:259`). In `document_ingest_service` header values: written when the key is SENT (`"po_ref" in payload.model_fields_set`), blank clears, absent leaves untouched (same shape as `is_transferable`, :1016). Never touches `_apply_project_label`. Contract doc `documentation/plans/autocount/PLAN-autocount-cross-repo-contract.md` gets the field.
4. **API**: `po_ref` on `app/schemas/scm_orders.py:229` and `SalesOrderService.serialize` (`app/services/scm/sales_order_service.py:400`); search OR adds `po_ref ILIKE like` and `po_ref_key LIKE '%'||fold(q)||'%'` (:1359); sort key `po_ref`.
5. **FE**: `types/scm.types.ts:493` field; `SalesOrdersGrid.tsx` column "PO ref" next to Project (:535) with size, truncate + title; `SalesOrderDetail.tsx` Order card Field "PO ref" next to Project (:1775). Existing components only, no layout change, so no mock.
6. **Chatbot**:
   - Parser: new top-level output key `po_refs` (array of strings, nullable) in `head/parser.py` (schema property, required list, `TOLERATED_ABSENT`), same pattern as `low_stock` (commit 690d41fc7). Addendum in `chatbot_parser_prompt.py`: "PO <number>", "our PO", "my PO no" put the number in `po_refs` as typed. Migration publishes a new UNLABELLED `chatbot_semantic_parser` version (owner moves `production`, as for `lss_0001_parser_vocab`).
   - Turn: when the profile is NOT staff (`is_staff_profile`, `turn/state.py:218`) and the parse carries `po_refs`, the business lane answers from `sales_orders` by `po_ref_key`, beside the `so_numbers` arm (`turn_runtime.py:2942-2953`, `lanes/business/__init__.py:1381-1411`). Staff turns ignore `po_refs`.
   - Lookup: `so_status` gains a po-ref lookup reusing `_in_scope_so_ids`, `_in_scope`, `_card`. Scope: `customer_scope()["linked"]` (`lanes/business/services.py:595`); a non-staff contact with no scope sees nothing. Out-of-scope matches are dropped silently (no refusal line).
   - Reply (owner v2 note): one SO `PO ref: X` + card; several `PO ref: X` / `Sales orders: N` + cards newest order date first; none `I could not find PO ref X.`
   - No regex/keyword detection of PO numbers in code; the number arrives only via `po_refs`.

## Out of scope / follow-ups

- Shared service: canonical field `po_ref` in its SO preset + sink contract so the mapping screen can map `UDFNote -> po_ref` (crew routes to the shared-service session). Until then Sorento simply never receives the key (U4).
- Owner: move `UDFNote` off `Ref` on the mapping screen, re-push open SOs, move the parser `production` label to the new version.
- #1429 PARSER-PER-AUDIENCE (unmerged, `ppa_0001`) may need a rebase of the parser migration if it lands first.

## Tests

pytest: ingest (U1-U7), API list/detail/search (U8-U9), so_status po-ref lookup + lane arm (U13-U17, U20-U21), parser schema guard/budget/publish tests updated. vitest: grid column + detail field (U10-U11). Live parser test `CHATBOT_LIVE_LLM=1` for po_refs extraction; console eval via `scripts/chatbot_console_check.py --say`.
