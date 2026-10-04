# UAC: project label on the sales order

Plan: `PLAN-so-project-label.md`. Every AC has a test unless marked "browser".

## Rules (pure functions, `app/services/project_label_rules.py`)

- AC-R1 `label_from_inquiry_cell("URC ENGINEERING / BAMBOO RESIDENCE / KUALA LUMPUR")` -> `BAMBOO RESIDENCE / KUALA LUMPUR`.
- AC-R2 `label_from_inquiry_cell("KNUSFORD/EKOTITIWANGSA/KL")` -> `EKOTITIWANGSA / KL` (slashes normalised to one space either side).
- AC-R3 `label_from_inquiry_cell("GLOBAL INGRESS/ 252U RMMJ TAMAN IMPIAN EMAS")` -> `252U RMMJ TAMAN IMPIAN EMAS`.
- AC-R4 `label_from_inquiry_cell("OTM GROUP SDN BHD (SMC-contact-o)")` -> `None`; blank -> `None`; `"A / "` -> `None`.
- AC-R5 `label_from_note` on `***PROJECT : TAIGA RESIDENCE` -> (`TAIGA RESIDENCE`, `note`); on `**PROJECT; 72U LUMIERE SETIA ALAM` -> (`72U LUMIERE SETIA ALAM`, `note`); on `PROJ: PARK GREEN @ BUKIT JALIL` -> (`PARK GREEN @ BUKIT JALIL`, `note`).
- AC-R6 `PROJECT CODE: 50-02` with no name line -> (`50-02`, `note`). `PROJECT CODE : ES(S)-DUDUK` followed by `***PROJECT : DUDUK SANTAI 1 & 2` -> (`DUDUK SANTAI 1 & 2`, `note`). `PROJECT CODE: 110-EF(C)-01` followed by `PROJECT TITLE: PROPOSED CONSTRUCTION OF 110 UNITS ...` -> the title text, `note`.
- AC-R7 Delivery block: `DELIVERY ADDRESS` newline `A-25-07 MAYA ARA RESIDENCES` newline `1 JALAN PJU 1A/1, ...` -> (`MAYA ARA RESIDENCES`, `delivery`). `DELIVERY TO : THE MET KL` -> (`THE MET KL`, `delivery`). `DELIVERY TO ADDRESS` newline `12-09 ASTER GREEN RESIDENCE,` -> (`ASTER GREEN RESIDENCE`, `delivery`). `DELIVERY ADDRESS : ` newline `DENSO (MALAYSIA) SDN BHD` -> (`DENSO (MALAYSIA) SDN BHD`, `delivery`).
- AC-R8 A `PROJECT` line beats a delivery block in the same note (`PROJECT: ZUS COFFEE @ KLANG VALLEY AREA` above `DELIVERY ADDRESS` -> `ZUS COFFEE @ KLANG VALLEY AREA`, `note`), regardless of order.
- AC-R9 `***OWN COLLECT`, `EXCHANGE MODEL FROM SRT6638 - INV: ...` newline `OWN COLLECT`, `***DELIVERY 27/08/2026`, `CONTACT : 016-000 0522`, empty, `None` -> `(None, None)`.
- AC-R10 `label_from_ref`: `JF- 9/9 3.50`, `JH - 8/9  10.17 PM`, `JH-21/08/2026  11.32 AM`, `RETAIL`, `END USER`, `REPLACEMENT`, `REPLACEMENT ORDER`, blank, `None` -> `None`. `THE MET KL`, `PINNACLE SUBANG`, `KSL BLOSSOM 733U @ SETIA ALAM` -> the trimmed text.
- AC-R11 `apply_project_label(order, label, source)`: writes when the order has no label; writes when the new source rank is >= the stored rank; leaves the order untouched (label, source, and `updated_at`) when the new rank is lower; never writes a `None` label. Rank: `inquiry` 4 > `note` 3 > `ref` 2 > `delivery` 1.

## Ingest (AutoCount push)

- AC-I1 A pushed SO whose `internal_note` is the MAYA ARA RTF sample lands with `project_label = MAYA ARA RESIDENCES`, `project_label_source = delivery`.
- AC-I2 A pushed SO whose note has `***PROJECT : TAIGA RESIDENCE` lands with (`TAIGA RESIDENCE`, `note`). Re-push with the note changed to `***PROJECT : TAIGA RESIDENCE BLOCK B` updates the label.
- AC-I3 `ref` is accepted on `CanonicalSalesOrder` (no 422). A push with `ref = "THE MET KL"` and an own-collect note lands with (`THE MET KL`, `ref`). A push with both a `PROJECT` note line and a `ref` takes the note line.
- AC-I4 A push onto an order whose label source is `inquiry` does not change the label, whatever the note says.
- AC-I5 A push with no note and no ref leaves an existing label untouched. A re-push of an identical record does not change `updated_at`.
- AC-I6 The peer session is told about the new optional `ref` field (PR body names it; contract doc updated).

## Order Inquiry importer

- AC-O1 The sheet names an SO AutoCount owns, cell `PEMBINAAN TEGUH MAJU / PASAR BESAR CHERAS - RESIDENCE / KUALA LUMPUR`: after import the order carries (`PASAR BESAR CHERAS - RESIDENCE / KUALA LUMPUR`, `inquiry`). Its figures are untouched and the row is still counted `DOCUMENT_OWNED_ELSEWHERE`.
- AC-O2 The sheet creates a provisional SO: it carries the label and source `inquiry` as well as the existing note behaviour.
- AC-O3 A customer-only cell (no slash) writes no label and leaves an existing label untouched.
- AC-O4 A re-upload with a corrected cell overwrites the earlier `inquiry` label (equal rank overwrites).

## Migration 511

- AC-M1 Adds `project_label` (Text, nullable) and `project_label_source` (String(16), nullable) to `sales_orders`. Chains on `510_strip_rtf_so_notes`. Single head. Revision id <= 32 chars.
- AC-M2 `backfill_project_labels(connection)`: a row with a plain `***PROJECT : PINE LEGACY` note gets (`PINE LEGACY`, `note`); a delivery-block note gets a `delivery` label; a row whose note is `Order Inquiry project: PEMBINAAN TEGUH MAJU / TAIGA RESIDENCE / KUALA LUMPUR` gets (`TAIGA RESIDENCE / KUALA LUMPUR`, `inquiry`); a row with an own-collect note stays NULL; a row that already has an `inquiry` label is untouched. Chunked, never loads the whole table.
- AC-M3 Downgrade drops both columns.

## API

- AC-A1 `GET` SO detail returns `project_label` and `project_label_source`; both declared on the response model (test asserts presence, not just truthiness).
- AC-A2 The SO list returns `project_label` per row and `query=BAMBOO` finds an order labelled `BAMBOO RESIDENCE / KUALA LUMPUR`.
- AC-A3 The fulfilment board row `project_label` comes from the column when set and from `_project_label_from_note` when not.

## Frontend

- AC-F1 Sales Orders grid has a "Project" column after Customer, `size: 220`, `truncate` + `title`, sortable, hidden/shown through the existing column preferences.
- AC-F2 SO detail header shows the label under the SO number when present, nothing when absent (vitest).
- AC-F3 Order card shows a "Project" field: the label plus a muted source word (`Inquiry sheet` / `Note` / `AutoCount ref` / `Delivery address`), or the usual dash when empty (vitest).
- AC-F4 Browser: open an SO with a label from `/` via the sidebar (Project Sales Admin > Sales Orders), see the column and the header, at 1280 and 375 wide. Evidence screenshot in the PR.
