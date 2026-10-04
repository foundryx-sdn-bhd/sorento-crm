# PLAN: project label on the sales order

Status: in review (2026-09-10)
Owner call: 2026-09-10, "I am quite okay with your plan"; label = the part of the Order Inquiry cell AFTER the first slash, the part before is the customer.
UAC: `so-project-label-acceptance-criteria.md`
Lane: `feat/so-project-label`, stacked on `fix/so-note-rtf-plain-text` (needs plain-text notes; migration chains after `510_strip_rtf_so_notes`). PR base = that branch until it merges, then main.

## Problem (measured)

Sales orders carry no project. The project module is not live (`projects.projects` holds 1 row). The name exists in three free-text places, none of them a column:

| Source | Where | Coverage measured | Shape |
| --- | --- | --- | --- |
| Order Inquiry sheet | `PROJECT/CUSTOMER` column, read by `app/services/project_order_inquiry_reader.py` into `OrderInquiryRow.project` | 465 distinct cells over 15,800 rows in `JAN - DEC 2026 ORDERabc.xlsx` | `CUSTOMER / PROJECT / AREA` (201 cells), `CUSTOMER / PROJECT` (176), customer only (29), 3 or 4 slashes (59) |
| AutoCount SO Note | `sales_orders.internal_note`, plain text once the RTF lane lands | ~190 noted SOs sampled: 35% `PROJECT :` label line, 38% delivery block only, 27% own-collect or nothing | `***PROJECT : X`, `PROJECT: X`, `**PROJECT; X`, `PROJ: X`, `PROJECT CODE: 50-02`; or `DELIVERY ADDRESS` then a site line |
| AutoCount SO `Ref` | not in the push contract today | for CONTACT N / TERA / LEENA / CONTACT Y it is the project name; for CONTACT O / CONTACT AA an agent stamp `JF- 9/9 3.50` | free text |

Today the Order Inquiry importer (`project_order_inquiry_import_service.py`) keeps the cell only when it CREATES a provisional SO and no customer matches, as `internal_note = "Order Inquiry project: <whole cell>"` (12 rows locally). For an SO AutoCount already owns (75,600 rows) it stamps `demand_origin` and drops the cell.

## Design (simplest that works)

Two columns on `sales_orders`, no table, no registry:

- `project_label` Text nullable. Human-readable, groupable free text. Never a UUID.
- `project_label_source` String(16) nullable: `inquiry` | `note` | `ref` | `delivery`.

Precedence rank, highest wins: `inquiry` 4, `note` 3, `ref` 2, `delivery` 1. A writer sets the pair only when its rank is >= the stored rank (equal rank overwrites so a corrected sheet or note lands). Nothing ever clears a label. A manual edit (rank 5) is the named trigger for a later slice, not built now.

One module holds every rule: `app/services/project_label_rules.py`.

### Rule 1: Order Inquiry cell (`inquiry`)

`label_from_inquiry_cell(cell)`: split on the FIRST `/`; label = remainder with whitespace collapsed and each ` / ` normalised to one space either side. No slash = no label (the cell is a customer only). Applied by the importer to EVERY order the sheet names, including orders owned by AutoCount (same place `demand_origin` is stamped) and provisional orders it creates. The existing `Order Inquiry project:` note behaviour and the customer match on the whole cell are left unchanged in this lane.

Examples: `URC ENGINEERING / BAMBOO RESIDENCE / KUALA LUMPUR` -> `BAMBOO RESIDENCE / KUALA LUMPUR`; `KNUSFORD/EKOTITIWANGSA/KL` -> `EKOTITIWANGSA / KL`; `GLOBAL INGRESS/ 252U RMMJ TAMAN IMPIAN EMAS` -> `252U RMMJ TAMAN IMPIAN EMAS`; `OTM GROUP SDN BHD (SMC-contact-o)` -> none.

### Rule 2: note label line (`note`)

`label_from_note(plain_text)` returns `(label, source)`:

1. First line matching, case-insensitive, `^\W*(PROJECT|PROJ)(\s*(CODE|TITLE))?\s*[:;]\s*(.+)$` -> group 4, trailing punctuation stripped. A `PROJECT`/`PROJ` name line beats a `PROJECT CODE` line when both exist; a code alone is the label. Source `note`.
2. Else a delivery block: a line matching `^\W*(DELIVERY\s*(TO\s*)?(ADDRESS)?|DELIVER\s*TO|DELIVERY|SITE)\s*:?\s*(.*)$`. If group 4 is non-empty that is the site line, else the next non-empty line. Strip a leading unit token `^[A-Z]?\d{1,3}(-\d{1,3}){1,2}[A-Z]?,?\s+` (A-25-07, B-43-08, 12-09, P-40-1, A1-13-09) and trailing commas. Source `delivery`.
3. Else none. `OWN COLLECT`, `EXCHANGE MODEL`, dates, contact-only notes give nothing.

Examples from the AutoCount sample: `***PROJECT : TAIGA RESIDENCE` -> `TAIGA RESIDENCE`; `**PROJECT; 72U LUMIERE SETIA ALAM` -> `72U LUMIERE SETIA ALAM`; `PROJ: PARK GREEN @ BUKIT JALIL` -> `PARK GREEN @ BUKIT JALIL`; `PROJECT CODE: 50-02` with no name line -> `50-02`; `DELIVERY ADDRESS` / `A-25-07 MAYA ARA RESIDENCES` -> `MAYA ARA RESIDENCES` (delivery); `DELIVERY TO : THE MET KL` -> `THE MET KL` (delivery); `***OWN COLLECT` -> none.

### Rule 3: AutoCount `Ref` (`ref`)

`label_from_ref(ref)`: none when blank, when it matches the agent stamp `^[A-Z]{2,8}\s*-\s*\d{1,2}/\d{1,2}` (also `^[A-Z]{2,8}-\d`), or when it is one of `RETAIL`, `END USER`, `REPLACEMENT`, `REPLACEMENT ORDER` (case-insensitive). Else the trimmed text.

Contract: `CanonicalSalesOrder.ref: Optional[str] = Field(None, max_length=255)` in `app/schemas/canonical_documents.py`. `extra="forbid"` means the field must exist before the bridge sends it. Precedent: `CanonicalShippingOrder.container_number` carries `PO.Ref`. This is a contract change and must be reported to the peer AutoCount session (standing rule in `PLAN-autocount-cross-repo-contract.md`); the peer maps `SO.Ref` on their side.

### Where the rules run

- `document_ingest_service.py` sales-order header write (create and update): after `internal_note` lands, compute rule 2 on the plain note and rule 3 on `ref`; apply the higher-ranked non-empty result through the precedence gate.
- `project_order_inquiry_import_service.py`: rule 1 on every order the sheet names.
- Alembic `511_so_project_label`: add the two columns and backfill. Note-derived labels for every row with a plain `internal_note`; rows whose note starts with `Order Inquiry project:` get rule 1 applied to the remainder with source `inquiry`. Module-level `backfill_project_labels(connection)` helper so a test can call it. Chains on `510_strip_rtf_so_notes`.
- `project_fulfilment_board_service.py`: `project_label` column first, `_project_label_from_note` as fallback, so the board is unchanged where the column is empty.

### API and UI

- `SalesOrderResponse` (`app/schemas/scm_orders.py` near `internal_note`) and the list serializer in `app/services/scm/sales_order_service.py` carry `project_label` and `project_label_source`. Declared in the response model, asserted in a test (undeclared fields are dropped silently).
- List free-text `query` also matches `project_label` ilike. Column sortable.
- FE `SalesOrdersGrid.tsx`: column `project_label` after `customer_name`, `size: 220`, `truncate` + `title`, header "Project". Type in `scm/types/scm.types.ts`.
- FE `SalesOrderDetail.tsx`: label as the header subtitle under the SO number (read-only metadata lives in the header), and a "Project" field in the Order card showing the label with a muted source word: `Inquiry sheet`, `Note`, `AutoCount ref`, `Delivery address`. Empty state shows the usual dash. No explanatory copy.

## Out of scope, named triggers

- Manual edit of the label (rank 5): build when a user asks to correct one.
- Alias merging (`SETIA ALAM` vs `SETIA ALAM @ AGESON KENSETSU SDN BHD`): the project module's job; `normalise_project_title` + trigram in `project_clash_service` already exist for it.
- Using the customer half of the inquiry cell to improve customer matching: separate lane.
- MCP exposure of the label: add when a chatbot journey asks for it.

## Slices

1. Rules module + tests (pure functions).
2. Migration + backfill helper + test.
3. Ingest hook + `ref` contract field + tests. Peer notified.
4. Importer hook + tests.
5. API fields + search + tests. Board reads the column.
6. FE column + detail header/field + vitest.
7. Review, browser verification, PR stacked on the RTF branch.
