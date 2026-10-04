# UAC: DO-OWNERSHIP-GUARD

Plan: `PLAN-do-ownership-guard.md`. "AutoCount row" = `orders.doc_key IS NOT NULL`.

| AC | Given / When / Then | Test |
| --- | --- | --- |
| AC-OG01 | Every `orders` column is in exactly one ownership set. | test_order_field_ownership::test_registry_partitions_every_orders_column |
| AC-OG02 | The Master columns AutoCount owns equal the old skip list; no Overall Tracking column is AutoCount's. | ::test_sheet_mappings_follow_registry |
| AC-OG03 | Master re-upload on an AutoCount row: Created Time and Cancel=Y ignored, Remarks CS and Type written. | ::test_master_keeps_created_time_and_cancel_on_autocount_row |
| AC-OG04 | DO ingest header carrying a tracking column fails the record and writes nothing. | test_ingest_autocount_do_grn::test_ingest_header_write_stays_in_autocount_columns |
| AC-OG05 | RMA / doc_key NULL row gets every Master and Overall Tracking column. | ::test_unowned_row_gets_every_master_and_tracking_column |
| AC-OG06 | Master + Tracking re-upload after adoption keeps AutoCount's values, writes tracking ones. | test_ingest_autocount_do_grn::test_master_reupload_after_adoption_keeps_autocount_values |
| AC-OG07 | The vanished sweep never cancels a doc_key NULL row. | test_ingest_autocount_do_grn::test_deletion_never_touches_unowned_row |
| AC-OG08 | JSON bulk import on an AutoCount row skips AutoCount keys and warns naming them; a plain row is written in full; the response keeps `warnings`. | ::test_bulk_import_skips_autocount_owned_keys, ::test_bulk_import_response_carries_warnings |
| AC-OG09 | Manual edit (and cancel) naming an AutoCount field on an AutoCount row: 409 `AUTOCOUNT_OWNED`, message names the fields + "owned by AutoCount", nothing written. | ::test_update_order_rejects_autocount_owned_field, ::test_cancel_autocount_row_is_rejected |
| AC-OG10 | Order Tracking fields stay editable on an AutoCount row; the customer is not re-pointed. | ::test_update_order_tracking_fields_on_autocount_row |
| AC-OG11 | A plain row takes every field as before. | ::test_update_order_plain_row_unchanged |
| AC-OG12 | Single-order read lists `autocount_owned_fields` for an AutoCount row, `[]` otherwise. | ::test_order_response_names_autocount_owned_fields |
| AC-OG13 | Line add / edit / delete / bulk delete on an AutoCount row: 409 naming `lines`, nothing changed. | ::test_order_line_crud_rejected_on_autocount_row |
| AC-OG14 | Lines of a plain row stay editable. | ::test_order_line_crud_on_plain_row_unchanged |
| AC-OG15 | FE: AutoCount fields read-only with "From AutoCount", tracking fields editable, PUT omits AutoCount fields; lines card read-only. Usable at 375px and 1280px. | OrderForm.autocountOwned.test.tsx, OrderLinesCard.test.tsx, agent-browser run |
| AC-OG16 | Ingest test tracking-column lists include `order_type` (and `estimated_delivery_date`). | test_ingest_autocount_do_grn / test_autocount_pull_delivery_orders TRACKING_COLUMNS |
