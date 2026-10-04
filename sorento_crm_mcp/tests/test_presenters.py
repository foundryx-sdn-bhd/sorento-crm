"""Render-envelope presenter coverage (view=render).

Verifies each tool's sanitized data maps into the uniform envelope:
{result_type, intro, items[{title,fields,flags}], attachments, action_links,
last_updated_at, has_result}.
"""
import json

import pytest

from sorento_crm_mcp.presenters import present_response


def env(tool, data):
    return json.loads(present_response(tool, json.dumps(data)))


def test_orders_list_one_item_per_order_products_inline():
    out = env("crm_order_management_orders_list", {
        "data": [{
            "order_number": "202606-1622", "debtor_name": "HANLIM",
            "order_date": "2026-06-10", "actual_delivery_date": "2026-06-11",
            "order_status": "Picked Up / In Transit", "pickup_time": "09:34:00",
            "transporter": "SORENTO", "driver_name": "AZHAR", "lorry_plate": "VQP1678",
            "warehouse": "BRW",
            "lines": [{"quantity": 3, "product": {"product_code": "SRTWB7109"}}],
        }],
    })
    assert out["result_type"] == "orders"
    assert len(out["items"]) == 1
    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["Order Number"] == "202606-1622"
    assert fields["Customer"] == "HANLIM"
    assert fields["Products"] == "SRTWB7109 (3)"
    assert out["has_result"] is True


def test_orders_list_quantity_trailing_zeros_truncated():
    """A whole-number decimal qty (2.0000) renders as an int; a real fraction keeps its digits."""
    out = env("crm_order_management_orders_list", {
        "data": [{
            "order_number": "X1",
            "lines": [
                {"quantity": "2.0000", "product": {"product_code": "AAA"}},
                {"quantity": "2.5000", "product": {"product_code": "BBB"}},
                {"quantity": "1.125", "product": {"product_code": "CCC"}},
            ],
        }],
    })
    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["Products"] == "AAA (2), BBB (2.5), CCC (1.125)"


def test_orders_by_product_quantity_trailing_zeros_truncated():
    out = env("crm_order_management_orders_by_product_list", {
        "data": [{
            "order_number": "X2",
            "matched_products": [
                {"product_code": "AAA", "quantity": "3.0000", "warehouse_code": "BRW"},
            ],
        }],
    })
    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    # DO-ASK-SIMPLIFY (owner hand test 3 Oct 2026): the warehouse is its own switched field,
    # no longer folded into Products, so the Warehouse reveal can hide it.
    assert fields["Products"] == "AAA (3)"
    assert fields["Warehouse"] == "BRW"


def test_incoming_list_one_item_per_line_with_attachment():
    out = env("crm_incoming_stock_list", {
        "data": [{
            "shipment_number": "OOLKSF6417", "shipping_container_number": "UETU5190029",
            "estimated_arrival_date": "2026-01-24",
            "attachment": {"filename": "PL.xlsx", "file_path": "http://x/PL.xlsx", "mime_type": "app/xlsx"},
            "lines": [
                {"product_code": "A", "product_name": "A", "remaining_incoming_quantity": 100,
                 "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 100}]},
                {"product_code": "B", "product_name": "B", "remaining_incoming_quantity": 34,
                 "warehouse_allocations": []},
            ],
        }],
    })
    assert len(out["items"]) == 2
    f0 = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert f0["Incoming Quantity"] == 100
    assert f0["Warehouse Allocations"] == "BRW (100)"
    # No aggregate total field leaks into the item.
    assert "Total Incoming Quantity" not in f0
    # Attachment lifted to envelope + intro switches to file mode.
    assert len(out["attachments"]) == 1
    assert out["intro"] == "I have attached the file(s) below."


def test_incoming_list_allocation_flags_and_unallocated_field():
    """Allocation signal: MCP owns the truth (booleans + number), n8n owns the badge.

    Line A is fully allocated, B carries no allocation at all, C is partially
    allocated (backend gap = 40). The two flags are mutually exclusive.
    """
    out = env("crm_incoming_stock_list", {
        "data": [{
            "shipment_number": "OOLKSF6417",
            "estimated_arrival_date": "2026-01-24",
            "lines": [
                {"product_code": "A", "remaining_incoming_quantity": 100,
                 "unallocated_quantity": None,
                 "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 100}]},
                {"product_code": "B", "remaining_incoming_quantity": 34,
                 "unallocated_quantity": None, "warehouse_allocations": []},
                {"product_code": "C", "remaining_incoming_quantity": 40,
                 "unallocated_quantity": 40,
                 "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 60}]},
            ],
        }],
    })
    a, b, c = out["items"]
    assert a["flags"]["unallocated"] is False
    assert a["flags"]["partially_allocated"] is False
    assert "Unallocated Quantity" not in {f["label"] for f in a["fields"]}

    assert b["flags"]["unallocated"] is True
    assert b["flags"]["partially_allocated"] is False
    # Pending allocation needs no number - the badge alone carries the signal.
    assert "Unallocated Quantity" not in {f["label"] for f in b["fields"]}

    assert c["flags"]["unallocated"] is False
    assert c["flags"]["partially_allocated"] is True
    fc = {f["label"]: f["value"] for f in c["fields"]}
    assert fc["Unallocated Quantity"] == 40
    # Existing flags survive unchanged.
    assert c["flags"]["discontinued"] is False


def test_incoming_list_with_container_and_quantity_denied_prints_no_number():
    """Issue #1328: the backend strips the container number and every quantity for a
    contact denied them (absent, not null). The row still names the product and the
    warehouse, and never prints "None" where a quantity was."""
    out = env("crm_incoming_stock_list", {
        "data": [{
            "shipment_number": "SH1",
            "estimated_arrival_date": "2026-11-02",
            "lines": [
                {"product_code": "A", "warehouse_allocations": [{"warehouse_code": "BRW"}]},
            ],
        }],
    })
    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["Warehouse Allocations"] == "BRW"
    assert "Container" not in fields
    assert "Incoming Quantity" not in fields
    assert "None" not in json.dumps(out["items"])


def test_incoming_list_missing_gap_key_claims_no_partial():
    """Forward-compat: an older backend omits `unallocated_quantity`.

    Allocations exist, so `unallocated` is false; without the gap the presenter
    must NOT guess a partial from `remaining_incoming_quantity`.
    """
    out = env("crm_incoming_stock_list", {
        "data": [{
            "shipment_number": "SH1",
            "lines": [
                {"product_code": "A", "remaining_incoming_quantity": 100,
                 "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 60}]},
            ],
        }],
    })
    flags = out["items"][0]["flags"]
    assert flags["unallocated"] is False
    assert flags["partially_allocated"] is False


def test_incoming_by_product_allocation_flags():
    out = env("crm_incoming_stock_by_product", {
        "data": [{
            "product_code": "A", "product_name": "A",
            "shipments": [
                {"shipping_container_number": "C1", "remaining_incoming_quantity": 34,
                 "unallocated_quantity": None, "warehouse_allocations": []},
                {"shipping_container_number": "C2", "remaining_incoming_quantity": 40,
                 "unallocated_quantity": 25,
                 "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 75}]},
            ],
        }],
    })
    first, second = out["items"]
    assert first["flags"]["unallocated"] is True
    assert second["flags"]["partially_allocated"] is True
    assert {f["label"]: f["value"] for f in second["fields"]}["Unallocated Quantity"] == 25


def test_incoming_shipments_carry_no_allocation_flags_set():
    """Shipment-level rows have no allocations - both flags stay false."""
    out = env("crm_incoming_stock_shipments", {
        "data": [{"shipment_number": "SH1", "total_remaining_incoming_quantity": 90,
                  "distinct_products_incoming": 2}],
    })
    flags = out["items"][0]["flags"]
    assert flags["unallocated"] is False
    assert flags["partially_allocated"] is False


def test_promotions_header_only_with_pdf():
    out = env("crm_marketing_promotions_list", {
        "data": [{
            "description": "Promo.pdf", "is_expired": True,
            "start_date": "2026-05-01", "end_date": "2026-05-31",
            "attachments": [{"attachment": {"original_filename": "Promo.pdf", "file_path": "http://x/Promo.pdf"}}],
            # products may be present in raw data but the presenter ignores them
            "products": [{"selling_price": 359.99, "product": {"product_code": "KS-001"}}],
        }],
    })
    assert len(out["items"]) == 1
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["Promotion"] == "Promo.pdf"
    assert f["Start Date"] == "2026-05-01"
    # No product fields surfaced.
    assert "Selling Price" not in f
    assert "Product Code" not in f
    assert out["items"][0]["flags"]["expired"] is True
    # promo header pdf carried into attachments
    assert any(a["filename"] == "Promo.pdf" for a in out["attachments"])


def test_attachment_url_is_file_path_verbatim_not_stored_filename():
    """The attachment `url` must be the DB `file_path` object key exactly - never
    reconstructed from `stored_filename` (the editable display name often differs
    from the real key, which produced 404-ing URLs). See presenters de-dupe block."""
    real_key = "http://cdn/promotion/id/CABANA NEW ARRIVAL END USER.pdf"
    out = env("crm_marketing_promotions_list", {
        "data": [{
            "description": "Promo", "start_date": "2026-05-08", "end_date": "2026-08-08",
            "attachments": [{"attachment": {
                "original_filename": "CABANA NEW ARRIVAL (END USER).pdf",
                "stored_filename": "(CABANA) NEW ARRIVAL END USER.pdf",
                "file_path": real_key,
                "mime_type": "application/pdf",
            }}],
        }],
    })
    a = out["attachments"][0]
    assert a["url"] == real_key                                   # url untouched
    assert a["filename"] == "(CABANA) NEW ARRIVAL END USER.pdf"   # display keeps stored_filename
    assert a["url"].endswith("CABANA NEW ARRIVAL END USER.pdf")   # NOT the (CABANA) key


def test_products_item_plus_nested_attachments():
    out = env("crm_master_products_list", {
        "data": [{
            "product_code": "SRTFC2044", "product_name": "Cistern", "list_price": "1200.00",
            "attachments": [
                {"original_filename": "a.jpg", "file_path": "http://x/a.jpg",
                 "attachment_type": {"type_name": "Product Photos"}},
            ],
        }],
    })
    assert out["result_type"] == "products"
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["List Price"] == "MYR 1200.00"
    assert len(out["attachments"]) == 1
    assert out["attachments"][0]["attachmentType"] == "Product Photos"


def test_product_name_hidden_when_equal_to_code_shown_when_distinct():
    # name == code → name line dropped (redundant in Sorento)
    out = env("crm_master_products_list", {
        "data": [{"product_code": "SRTWT107", "product_name": "SRTWT107"}],
    })
    labels = {x["label"] for x in out["items"][0]["fields"]}
    assert "Product Code" in labels
    assert "Product Name" not in labels

    # name != code → name retained
    out2 = env("crm_master_products_list", {
        "data": [{"product_code": "SRTFC2044", "product_name": "Cistern"}],
    })
    f2 = {x["label"]: x["value"] for x in out2["items"][0]["fields"]}
    assert f2["Product Name"] == "Cistern"


def test_products_price_and_dimensions_default_to_not_defined():
    out = env("crm_master_products_list", {
        "data": [{"product_code": "SRTNODIM", "product_name": "No Dims Product"}],
    })
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["List Price"] == "Not defined"
    assert f["Dimensions"] == "Not defined"


def test_stock_uses_relabelled_location_fields():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "SRTWT107", "product_name": "SRTWT107",
                  "system_location": "BRW", "system_location_description": "BUKIT RAJA",
                  "quantity_on_hand": 36, "updated_at": "2026-06-12T09:28:56+08:00"}],
    })
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["Warehouse"] == "BUKIT RAJA"
    assert f["System Location"] == "BRW"
    assert f["Quantity On Hand"] == 36
    assert out["last_updated_at"] == "2026-06-12T09:28:56+08:00"


def test_orders_so_outstanding_bucket_renders_so_shaped_rows():
    out = env("crm_order_management_orders_list", {
        "order_status": "so_outstanding",
        "data": [{
            "so_number": "SO-1001", "product_code": "SRTWC8517",
            "outstanding_qty": 7, "order_date": "2026-06-01",
            "customer": "ABC SDN BHD", "requested_delivery_date": "2026-06-15",
        }],
    })
    assert out["result_type"] == "so_outstanding"
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["SO Number"] == "SO-1001"
    assert f["Outstanding Qty"] == "7"
    assert f["Customer"] == "ABC SDN BHD"
    assert out["has_result"] is True


def test_orders_groups_render_same_shape_as_flat_list():
    row_a = {"order_number": "A1", "debtor_name": "ABC", "order_date": "2026-06-01"}
    row_b = {"order_number": "B1", "debtor_name": "XYZ", "order_date": "2026-06-02"}
    out = env("crm_order_management_orders_list", {
        "data": [row_a, row_b],
        "groups": [
            {"key": "ABC", "label": "ABC", "rows": [row_a]},
            {"key": "XYZ", "label": "XYZ", "rows": [row_b]},
        ],
    })
    assert [g["key"] for g in out["groups"]] == ["ABC", "XYZ"]
    assert out["groups"][0]["items"][0]["fields"]
    labels = {f["label"]: f["value"] for f in out["groups"][0]["items"][0]["fields"]}
    assert labels["Customer"] == "ABC"


def test_orders_groups_absent_when_backend_did_not_group():
    out = env("crm_order_management_orders_list", {
        "data": [{"order_number": "A1", "debtor_name": "ABC"}],
    })
    assert "groups" not in out


def test_no_three_line_block_is_prepended_off_a_top_level_so_outstanding_qty():
    """D8 (owner, 8 Sep 2026): the old "SO Outstanding / DO open / Delivered" block is gone;
    the per-row SO block is the only SO rendering, and the `so_outstanding` bucket's own
    summary (no products) renders nothing either."""
    out = env("crm_order_management_orders_list", {
        "data": [{"order_number": "A1", "debtor_name": "ABC",
                   "lines": [{"quantity": 3, "product": {"product_code": "SRTWC8517"}}]}],
        "summary": {
            "row_count": 5, "order_count": 5, "delivered_count": 4, "pending_count": 1,
            "customers": ["ABC"], "customer_count": 1,
            "so_outstanding_qty": 7, "so_outstanding_count": 1,
            "products": [{"product_code": "SRTWC8517", "order_count": 5, "customer_count": 1,
                          "delivered_quantity": 12, "pending_quantity": 3, "so_count": 1,
                          "so_ordered_qty": 9, "so_transferred_qty": 2, "so_outstanding_qty": 7}],
        },
    })
    labels = [f["label"] for item in out["summary_items"] for f in item["fields"]]
    assert "DO open (not yet delivered)" not in labels
    assert labels.count("SO Outstanding") == 1  # the row's own field, once
    assert "SO" in labels and "Ordered" in labels
    bucket = env("crm_order_management_orders_list", {
        "order_status": "so_outstanding",
        "data": [{"so_number": "SO-1", "product_code": "SRTWC8517", "outstanding_qty": 7}],
        "summary": {"scope": "filter", "row_count": 1, "so_outstanding_qty": 7, "so_outstanding_count": 1},
    })
    assert "summary_items" not in bucket


def test_products_specs_render_as_keyed_fields_ranked_by_backend_order():
    out = env("crm_master_products_list", {
        "data": [{
            "product_code": "SRTWC8517",
            "specs": [
                {"key": "thickness", "label": "Thickness", "value": 1.2, "unit": "mm", "rank_weight": 5.0},
                {"key": "wattage", "label": "Wattage", "value": 60, "unit": "W", "rank_weight": 3.0},
            ],
        }],
    })
    fields = out["items"][0]["fields"]
    keyed = [f for f in fields if f.get("key", "").startswith("spec:")]
    assert [f["label"] for f in keyed] == ["Thickness", "Wattage"]
    assert keyed[0]["value"] == "1.2 mm"
    assert keyed[1]["value"] == "60 W"
    assert out["spec_vocabulary"] == {"thickness": "Thickness", "wattage": "Wattage"}


def test_products_spec_value_reads_the_crms_plain_words():
    """PR #833 round 5 N4: a stored enum value ("cold_only") is shown in the plain words
    the CRM sent as `display_value`, never as stored."""
    out = env("crm_master_products_list", {
        "data": [{
            "product_code": "SRTWT5906",
            "specs": [
                {"key": "water_supply", "label": "Water supply", "value": "cold_only",
                 "display_value": "Cold only", "rank_weight": 1.0},
            ],
        }],
    })
    [field] = [f for f in out["items"][0]["fields"] if f.get("key") == "spec:water_supply"]
    assert field["value"] == "Cold only"


def test_products_no_specs_key_no_vocabulary():
    out = env("crm_master_products_list", {"data": [{"product_code": "SRTWC8517"}]})
    assert "spec_vocabulary" not in out
    assert not any(f.get("key", "").startswith("spec:") for f in out["items"][0]["fields"])


def test_purchase_orders_placed_renders_fields_and_restricts_supplier():
    out = env("crm_procurement_po_placed_list", {
        "data": [{
            "po_number": "PO-1001", "product_code": "SRTWC8517",
            "outstanding_qty": 50, "expected_date": "2026-07-01",
            "supplier": "Acme Supplies",
        }],
    })
    assert out["result_type"] == "purchase_orders_placed"
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["PO Number"] == "PO-1001"
    assert f["Outstanding Qty"] == "50"
    assert f["Supplier"] == "Acme Supplies"
    assert out["restricted_fields"] == {"supplier": "purchase_orders.supplier"}


def test_purchase_orders_placed_group_by_supplier():
    row_a = {"po_number": "PO-1", "product_code": "P1", "supplier": "Acme"}
    row_b = {"po_number": "PO-2", "product_code": "P2", "supplier": "Beta"}
    out = env("crm_procurement_po_placed_list", {
        "data": [row_a, row_b],
        "groups": [
            {"key": "Acme", "label": "Acme", "rows": [row_a]},
            {"key": "Beta", "label": "Beta", "rows": [row_b]},
        ],
    })
    assert {g["key"] for g in out["groups"]} == {"Acme", "Beta"}


def test_spo_last_receipt_intro_names_the_new_meaning():
    """AC-9 (chatbot-warehouse-entity-and-last-in): the tool answers the last SPO line
    per product, not a receipt."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-01", "product_code": "SRTWC8517",
            "spo_quantity": 30, "spo_date": "2026-06-10", "spo_date_source": "expected",
            "warehouse": "BRW",
        }],
    })
    assert out["intro"] == "Here is the last SPO line per product."


def test_spo_last_receipt_field_order_is_the_owners_reading_order():
    """AC-9, owner ruling 8 Sep 2026 (against the rendered screenshot), amended 9 Sep 2026
    (`PLAN-chatbot-last-in-container-number.md`, owner ruling: "the container number can
    put below the SPO number in the answer"): SPO Number, then Container Number if any,
    Product Code, SPO Quantity, GR Quantity if any, SPO Date, GR Date if any, Warehouse.
    Asserted as an exact LIST, not a membership test - the order IS the ruling."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-01", "container_number": "CMAU7650091",
            "product_code": "SRTWC8517",
            "spo_quantity": 30, "gr_quantity": 12,
            "spo_date": "2026-06-10", "spo_date_source": "expected",
            "gr_date": "2026-06-18", "warehouse": "BRW",
        }],
    })
    item = out["items"][0]
    assert item["title"] == "SPO-2026-01"
    assert [f["label"] for f in item["fields"]] == [
        "SPO Number",
        "Container Number",
        "Product Code",
        "SPO Quantity",
        "GR Quantity",
        "SPO Date",
        "GR Date",
        "Warehouse",
    ]
    assert [f["value"] for f in item["fields"]] == [
        "SPO-2026-01", "CMAU7650091", "SRTWC8517", "30", "12", "2026-06-10", "2026-06-18", "BRW",
    ]
    assert item["fields"][1]["key"] == "container_number"


def test_spo_last_receipt_without_a_container_keeps_the_seven_field_row():
    """AC-6 (chatbot-last-in-container-number). A row with no `container_number` renders
    the previous seven labels in the previous order - "if any" holds for the new field
    exactly as it already does for the two GR fields."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-01", "product_code": "SRTWC8517",
            "spo_quantity": 30, "gr_quantity": 12,
            "spo_date": "2026-06-10", "spo_date_source": "expected",
            "gr_date": "2026-06-18", "warehouse": "BRW",
        }],
    })
    item = out["items"][0]
    assert [f["label"] for f in item["fields"]] == [
        "SPO Number",
        "Product Code",
        "SPO Quantity",
        "GR Quantity",
        "SPO Date",
        "GR Date",
        "Warehouse",
    ]


def test_spo_last_receipt_open_line_shows_neither_gr_field():
    """"if any" on both halves: an open line has no GR Quantity and no GR Date, and the
    remaining five fields keep their order."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-02", "product_code": "P1",
            "spo_quantity": 30, "gr_quantity": None,
            "spo_date": "2026-06-10", "spo_date_source": "expected",
            "gr_date": None, "warehouse": "BRW",
        }],
    })
    assert [f["label"] for f in out["items"][0]["fields"]] == [
        "SPO Number", "Product Code", "SPO Quantity", "SPO Date", "Warehouse",
    ]


def test_spo_last_receipt_received_without_a_grn_shows_quantity_and_no_gr_date():
    """The ESB-stated path - a received quantity the GRN tables know nothing about."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-03", "product_code": "P2",
            "spo_quantity": 50, "gr_quantity": 50,
            "spo_date": "2026-06-01", "spo_date_source": "issued", "gr_date": None,
        }],
    })
    assert [f["label"] for f in out["items"][0]["fields"]] == [
        "SPO Number", "Product Code", "SPO Quantity", "GR Quantity", "SPO Date",
    ]


def test_spo_last_receipt_marks_a_recorded_date_in_its_own_label():
    """The 3% of lines with neither `expected_date` nor `issue_date` fall back to
    `created_at`, and the label says so rather than letting the reader take a bookkeeping
    timestamp for a promised delivery."""
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-04", "product_code": "P3",
            "spo_quantity": 5, "spo_date": "2026-06-01", "spo_date_source": "recorded",
        }],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert "SPO Date (recorded)" in labels
    assert "SPO Date" not in labels


def test_spo_last_receipt_issued_date_keeps_the_plain_label():
    out = env("crm_procurement_spo_allocations_last_receipt_list", {
        "data": [{
            "spo_number": "SPO-2026-05", "product_code": "P4",
            "spo_quantity": 5, "spo_date": "2026-06-01", "spo_date_source": "issued",
        }],
    })
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["SPO Date"] == "2026-06-01"


def test_stock_omits_sellable_when_backend_did_not_send_it():
    """AC-903: byte-identical when the backend answered with no `sellable` at all - no
    Outstanding field, no restricted_fields, no summary block."""
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "SRTWT107", "quantity_on_hand": 36}],
    })
    labels = [x["label"] for x in out["items"][0]["fields"]]
    assert labels == ["Product Code", "Warehouse", "System Location", "Quantity On Hand"]
    assert "restricted_fields" not in out
    assert "summary_items" not in out


def test_stock_rows_keep_every_location_and_carry_outstanding_when_sellable_is_sent():
    """D1 (owner console pass, 8 Sep 2026): the grant used to hide the location rows -
    the Open SO block lived in `summary_items`, which the CRM renders INSTEAD of the rows.
    Now each row carries `*Outstanding:* N` (its own open SO) after Quantity On Hand,
    restricted behind `inventory.sellable`, and nothing is written to the summary slot."""
    out = env("crm_inventory_stock_balance_list", {
        "data": [
            {"product_code": "SRTWT107", "warehouse": "A", "quantity_on_hand": 10, "open_so_qty": 6, "sellable": 4},
            {"product_code": "SRTWT107", "warehouse": "B", "quantity_on_hand": 20, "open_so_qty": 0, "sellable": 20},
            {"product_code": "SRTWT108", "warehouse": "A", "quantity_on_hand": 5, "open_so_qty": 8, "sellable": -3},
        ],
    })
    assert len(out["items"]) == 3
    for item, expected in zip(out["items"], (6, 0, 8)):
        labels = [f["label"] for f in item["fields"]]
        assert labels.index("Outstanding") == labels.index("Quantity On Hand") + 1
        field = next(f for f in item["fields"] if f["key"] == "open_so_qty")
        assert field["value"] == expected
    assert out["restricted_fields"] == {"open_so_qty": "inventory.sellable"}
    assert "summary_items" not in out


def test_stock_outstanding_is_the_rows_own_number_never_a_product_sum():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "SRTWT107", "warehouse": "A", "quantity_on_hand": 10, "open_so_qty": 6, "sellable": 4}],
        "stock_summary": [{"product_id": "p1", "product_code": "SRTWT107", "total_on_hand": 60, "open_so_qty": 941, "sellable": -881}],
    })
    field = next(f for f in out["items"][0]["fields"] if f["key"] == "open_so_qty")
    assert field["value"] == 6
    assert "summary_items" not in out


def test_stock_compact_total_and_warehouse_lines_carry_the_os_suffix_as_granted_value():
    """D1: `*Total:* 51 (O/S: 36)` and `*BRW:* 0 (O/S: 12)` under the grant; the suffix
    rides as `granted_value` on a keyed restricted field, so the CRM swaps it in when
    granted and strips it otherwise - the plain number always stays."""
    out = env("crm_inventory_stock_balance_list", {
        "data": [],
        "stock_visibility": {"mode": "compact", "source": "access_type"},
        "stock_summary": [{
            "product_id": "p1", "product_code": "SRTWT107", "product_name": "SRTWT107",
            "total_on_hand": 51, "open_so_qty": 36, "sellable": 15,
            "locations": [
                {"warehouse_code": "BRW", "quantity_on_hand": 0, "open_so_qty": 12},
                {"warehouse_code": "KLG", "quantity_on_hand": 51, "open_so_qty": 20},
            ],
            "flags": {},
        }],
    })
    fields = out["items"][0]["fields"]
    total = next(f for f in fields if f["label"] == "Total")
    assert total == {"key": "total_on_hand", "label": "Total", "value": 51, "granted_value": "51 (O/S: 36)"}
    brw = next(f for f in fields if f["label"] == "BRW")
    assert brw == {"key": "location_on_hand", "label": "BRW", "value": 0, "granted_value": "0 (O/S: 12)"}
    assert out["restricted_fields"] == {"total_on_hand": "inventory.sellable", "location_on_hand": "inventory.sellable"}
    assert "summary_items" not in out


def test_stock_compact_without_sellable_is_the_plain_unkeyed_block():
    out = env("crm_inventory_stock_balance_list", {
        "data": [],
        "stock_visibility": {"mode": "compact", "source": "access_type"},
        "stock_summary": [{
            "product_id": "p1", "product_code": "SRTWT107", "product_name": "SRTWT107",
            "total_on_hand": 12,
            "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 12}],
            "flags": {},
        }],
    })
    fields = out["items"][0]["fields"]
    assert fields[1] == {"label": "Total", "value": 12}
    assert fields[2] == {"label": "BRW", "value": 12}
    assert "restricted_fields" not in out
def test_forms_minimal_name_only():
    out = env("crm_forms_management_forms_list", {"data": [{"name": "Renovation Form", "attachment_id": "x"}]})
    assert out["items"][0]["fields"] == [{"label": "Form Name", "value": "Renovation Form"}]
    assert out["attachments"] == []


def test_forms_narrowed_carries_attachment():
    out = env("crm_forms_management_forms_list", {
        "data": [{
            "name": "Renovation Form",
            "attachment": {"original_filename": "RENO.pdf", "file_path": "http://x/RENO.pdf",
                           "mime_type": "application/pdf", "attachment_type": {"type_name": "Marketing Forms"}},
        }],
    })
    assert out["items"][0]["fields"][0]["value"] == "Renovation Form"
    assert len(out["attachments"]) == 1
    assert out["attachments"][0]["filename"] == "RENO.pdf"
    assert out["intro"] == "I have attached the file(s) below."


def test_resource_attachments_no_type_label():
    out = env("crm_resource_attachments_list", {
        "data": [{
            "original_filename": "Dealer Price List.pdf", "file_path": "http://x/Dealer Price List.pdf",
            "mime_type": "application/pdf", "attachment_type": {"type_name": "Direct Access"},
        }],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert labels == ["File Name"]          # no "Type" line
    assert out["attachments"][0]["attachmentType"] is None  # type stripped from the file too


def test_resource_attachments_carry_the_upload_date_when_present():
    """Re-uploaded documents keep one name, so the date is what tells them apart.

    Six revisions of the Container Status workbook are six items reading
    "Container Status 2026.xlsx". Without a date the agent cannot say which one
    it is handing over, and rows arrive newest-first for nothing.
    """
    out = env("crm_resource_attachments_list", {
        "data": [
            {"original_filename": "Container Status 2026.xlsx", "file_path": "http://x/a.xlsx",
             "uploaded_at": "2026-08-07T03:09:33"},
            {"original_filename": "Container Status 2026.xlsx", "file_path": "http://x/b.xlsx",
             "uploaded_at": "2026-08-01T09:00:00"},
        ],
    })
    assert [f["value"] for f in out["items"][0]["fields"]] == [
        "Container Status 2026.xlsx", "2026-08-07",
    ]
    # Looked up by label, not by index: a fixture that later carries a company
    # would shift the position and silently assert against the wrong field.
    assert next(
        f["value"] for f in out["items"][1]["fields"] if f["label"] == "Uploaded"
    ) == "2026-08-01"


def test_resource_attachments_same_name_different_company_are_distinguishable():
    """AC-D2. A contact granted both Mocha and Sorento gets a current workbook
    from EACH, and each company names its sheet the same thing - the File ID is
    deliberately withheld from this render, so the Company field is the only
    handle left to tell the two apart.
    """
    out = env("crm_resource_attachments_list", {
        "data": [
            {"original_filename": "Container Status 2026.xlsx", "file_path": "http://x/sorento.xlsx",
             "company_name": "Sorento"},
            {"original_filename": "Container Status 2026.xlsx", "file_path": "http://x/mocha.xlsx",
             "company_name": "Mocha"},
        ],
    })
    assert len(out["items"]) == 2
    company_by_item = [
        next(f["value"] for f in item["fields"] if f["label"] == "Company")
        for item in out["items"]
    ]
    assert company_by_item == ["Sorento", "Mocha"]
    assert company_by_item[0] != company_by_item[1], (
        "same filename, different company - the two items must not read identically"
    )


def test_resource_attachments_no_company_renders_no_company_line():
    """AC-D3. A shared / company-less attachment (`company_name` absent or
    None) must render with NO Company field at all, never an empty one."""
    out = env("crm_resource_attachments_list", {
        "data": [
            {"original_filename": "Shared Catalogue.pdf", "file_path": "http://x/shared.pdf"},
            {"original_filename": "Also Shared.pdf", "file_path": "http://x/also.pdf",
             "company_name": None},
        ],
    })
    for item in out["items"]:
        labels = [f["label"] for f in item["fields"]]
        assert "Company" not in labels


def test_resource_attachments_do_not_expose_the_row_id():
    """Reversed deliberately. The id was added so a human could trace which of
    several identically-named files the agent sent - but `render` is the
    CUSTOMER view, and the uuid went out on WhatsApp under every document. The
    id is still on the raw (non-render) response, which is where someone
    debugging is looking anyway.
    """
    out = env("crm_resource_attachments_list", {
        "data": [{
            "id": "df853300-0000-0000-0000-000000000001",
            "original_filename": "Container Status 2026.xlsx",
            "file_path": "http://x/a.xlsx",
        }],
    })
    labels = {f["label"] for f in out["items"][0]["fields"]}
    assert "File ID" not in labels
    assert "df853300-0000-0000-0000-000000000001" not in json.dumps(out["items"])


def test_uploaded_at_becomes_last_updated_at():
    """An attachment is never edited in place, so the upload IS its freshness.

    Without this every document answer reported `last_updated_at: null` and the
    agent could not say how current the file it handed over was.
    """
    out = env("crm_resource_attachments_list", {
        "data": [
            {"original_filename": "a.xlsx", "file_path": "http://x/a.xlsx",
             "uploaded_at": "2026-08-01T09:00:00"},
            {"original_filename": "b.xlsx", "file_path": "http://x/b.xlsx",
             "uploaded_at": "2026-08-07T03:09:33"},
        ],
    })
    assert out["last_updated_at"] == "2026-08-07T03:09:33"


def test_portal_link_becomes_action_link():
    out = env("crm_portal_link_get", {"portal_link": "https://portal/x", "label": "Complaint Portal"})
    assert out["action_links"] == [{"label": "Complaint Portal", "url": "https://portal/x", "type": "portal_link"}]
    assert out["has_result"] is True


def test_empty_result_envelope():
    out = env("crm_master_products_list", {"data": [], "empty": True})
    assert out["items"] == []
    assert out["attachments"] == []
    assert out["has_result"] is False
    assert out["intro"] == "No matching results found."


def test_invalid_json_returns_raw_unchanged():
    assert present_response("crm_master_products_list", "not json") == "not json"


# ---------------------------------------------------------------- field access


def _incoming_row(**clearance):
    """One shipment row as `/incoming-stock/list` returns it, plus whatever
    clearance fields this caller was permitted."""
    return {
        "shipment_number": "SHP-1",
        "shipping_container_number": "SEGU4008631",
        "estimated_arrival_date": "2026-07-08",
        "lines": [
            {
                "product_code": "SRTWB7109",
                "product_name": "Basin Mixer",
                "batch_number": "B-1",
                "remaining_incoming_quantity": 12,
                "warehouse_allocations": [
                    {"warehouse_code": "BRW", "allocated_quantity": 12}
                ],
            }
        ],
        **clearance,
    }


def test_render_shows_the_clearance_fields_a_caller_may_see():
    """The render view had a hardcoded field list that never included any of them,
    so an entitled contact still got nothing."""
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row(eta_delay_date="2026-07-12", liner_code="CMA")],
    })

    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["ETA"] == "2026-07-08"
    assert fields["ETA Delay"] == "2026-07-12"
    assert fields["Liner"] == "CMA"


def test_render_omits_a_field_the_caller_may_not_see():
    """The backend strips denied keys, so they simply are not in the row. Render
    must not invent a blank line for them - a labelled empty value reads as "not
    reached yet"."""
    out = env("crm_incoming_stock_list", {"data": [_incoming_row()]})

    labels = {f["label"] for f in out["items"][0]["fields"]}
    assert "ETA" in labels, "ships allowed, so the backend sent it"
    assert "Gatepass" not in labels
    assert "ETA Delay" not in labels


def test_render_never_gates_the_answer_itself():
    """Product, container, shipment and quantity are what the contact asked about.
    A contact who may not see a gatepass date must still be told what is arriving,
    so none of these may ever be stripped.

    ETA is NOT among them: it is gateable (revocable by an admin) though it ships
    allowed, so it renders as a clearance pair rather than as identity."""
    out = env("crm_incoming_stock_list", {"data": [_incoming_row()]})

    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["Product Code"] == "SRTWB7109"
    assert fields["Product Name"] == "Basin Mixer"
    assert "Shipment" not in fields
    assert fields["Container"] == "SEGU4008631"
    assert fields["Incoming Quantity"] == 12
    assert "BRW" in str(fields["Warehouse Allocations"])
    assert out["has_result"] is True


def test_incoming_list_render_has_no_shipment_number():
    """The auto-generated packing-list number means nothing to a customer - the
    render must not carry a field for it, though the raw payload key stays."""
    out = env("crm_incoming_stock_list", {"data": [_incoming_row()]})

    for item in out["items"]:
        keys = {f["key"] for f in item["fields"]}
        labels = {f["label"] for f in item["fields"]}
        assert "shipment_number" not in keys
        assert "Shipment" not in labels


def test_incoming_list_raw_payload_still_carries_shipment_number():
    """AC3.2: `view=render` is opt-in (module docstring) - a caller who does not ask
    for it gets the tool's raw data shape unchanged, `shipment_number` included. The
    render dropping the field (above) must not come from the presenter popping it
    off the row on the way through; it must stay a read via `.get()`."""
    from sorento_crm_mcp.presenters import _Builder, _incoming_list

    row = _incoming_row()
    _incoming_list([row], _Builder())

    assert row["shipment_number"] == "SHP-1"


def test_render_carries_the_denial_reason_through():
    """Without it the agent cannot tell "you may not see this" from "it has not
    happened yet", so it guesses - and it guesses the second one out loud."""
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row()],
        "field_access": {
            "denied": [
                {
                    "field": "gatepass_date",
                    "agent_code": "incoming_stock_enquiries",
                    "outcome": "field_not_allowed",
                    "reason": "This contact holds the agent, but this field is not allowed on it.",
                }
            ],
            "note": "Absent does NOT mean the value is unknown or not yet reached.",
        },
    })

    assert out["field_access"]["denied"][0]["outcome"] == "field_not_allowed"
    assert "not yet reached" in out["field_access"]["note"]


def test_render_omits_field_access_when_nothing_was_denied():
    out = env("crm_incoming_stock_list", {"data": [_incoming_row()]})
    assert "field_access" not in out


def test_render_fields_carry_the_crm_field_key():
    """A consumer must project on the key, not on the label.

    Two label vocabularies for the same field already disagree - render says
    "ETC", `field_access.FIELD_LABELS` says "ETC (estimated time of container
    closing)" - so label matching means picking one and being unable to
    cross-check the other. The key is stable under any label rename.
    """
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row(etc_date="2026-06-30", coa_permit_no="COA-9")],
    })

    by_key = {f["key"]: f for f in out["items"][0]["fields"] if "key" in f}
    assert by_key["estimated_arrival_date"]["value"] == "2026-07-08"
    assert by_key["etc_date"]["label"] == "ETC"
    assert by_key["coa_permit_no"]["value"] == "COA-9"
    # Identity fields are keyed too - they are just as answerable.
    assert by_key["product_code"]["value"] == "SRTWB7109"
    assert by_key["shipping_container_number"]["value"] == "SEGU4008631"
    assert by_key["remaining_incoming_quantity"]["value"] == 12
    # Every field of this result type carries one; none is null.
    assert all(f.get("key") for f in out["items"][0]["fields"])


def test_render_key_matches_the_denied_vocabulary():
    """Absent-and-denied vs absent-and-not-reached is decided by comparing the
    same token on both sides, so the two must be the same token.
    """
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row()],
        "field_access": {
            "denied": [
                {"field": "gatepass_date", "agent_code": "a", "outcome": "field_not_allowed"}
            ],
            "note": "Absent does NOT mean the value is unknown or not yet reached.",
        },
    })

    keys = {f.get("key") for f in out["items"][0]["fields"]}
    denied = {d["field"] for d in out["field_access"]["denied"]}
    assert "gatepass_date" in denied
    assert "gatepass_date" not in keys, "denied is absent from fields, by design"
    # ...and a field that is simply not reached is in neither set, which is the
    # third branch: "not recorded", never "I can't share that".
    assert "collection_date" not in keys and "collection_date" not in denied


def test_render_stock_fields_carry_the_key():
    """A cross-domain stock/incoming block is sorted by quantity and ETA. Both
    branches matched on display text, and both were dead: `estimated_arrival_date`
    was relabelled `ETA`, and an incoming row labels its quantity `Incoming
    Quantity`, not `Quantity On Hand`. Sorting on the key survives both.
    """
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "SRTWT107", "product_name": "Basin",
                  "system_location": "BRW", "system_location_description": "BUKIT RAJA",
                  "quantity_on_hand": 36}],
    })
    by_key = {f["key"]: f["value"] for f in out["items"][0]["fields"] if "key" in f}
    assert by_key["quantity_on_hand"] == 36
    assert by_key["product_code"] == "SRTWT107"
    assert by_key["warehouse"] == "BUKIT RAJA"
    assert by_key["system_location"] == "BRW"


def test_render_stock_keeps_the_key_on_the_placeholder_value():
    """Warehouse / location / quantity always render, "-" when absent, so the row
    shape never varies. The key rides along, so a consumer that projects on key
    still has to expect a non-numeric value there.
    """
    out = env("crm_inventory_stock_balance_list", {"data": [{"product_code": "X"}]})
    by_key = {f["key"]: f["value"] for f in out["items"][0]["fields"] if "key" in f}
    assert by_key["quantity_on_hand"] == "-"
    assert by_key["warehouse"] == "-"


def test_render_by_product_fields_carry_the_key():
    out = env("crm_incoming_stock_by_product", {
        "data": [{
            "product_code": "SRTWB7109",
            "product_name": "Basin Mixer",
            "shipments": [{
                "shipping_container_number": "SEGU4008631",
                "estimated_arrival_date": "2026-07-08",
                "remaining_incoming_quantity": 12,
                "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 12}],
            }],
        }],
    })
    by_key = {f["key"]: f["value"] for f in out["items"][0]["fields"] if "key" in f}
    assert by_key["product_code"] == "SRTWB7109"
    assert by_key["estimated_arrival_date"] == "2026-07-08"


def test_render_omits_key_where_the_presenter_has_no_source_key():
    """`key` is omitted, not emitted as null, so a consumer can test for it."""
    # The DO list's fields all carry keys since DO-ASK-SIMPLIFY (each is a reveal switch), so
    # the keyless example is the product list's own code field.
    out = env("crm_master_products_list", {"data": [{"product_code": "SRTWC8517"}]})
    assert out["items"][0]["fields"][0] == {"label": "Product Code", "value": "SRTWC8517"}


# ------------------------------------------------ absent-field vocabulary


def test_denied_entries_gain_the_customer_facing_label():
    """A denial has to be said out loud: "I can't share the ...". The backend
    sends the field key and an admin-register label lives elsewhere, so the
    envelope supplies the CUSTOMER register here.
    """
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row()],
        "field_access": {
            "denied": [
                {"field": "etc_date", "agent_code": "a", "outcome": "field_not_allowed"},
                {"field": "gatepass_date", "agent_code": "a", "outcome": "field_not_allowed"},
            ],
            "note": "Absent does NOT mean the value is unknown or not yet reached.",
        },
    })
    by_field = {d["field"]: d for d in out["field_access"]["denied"]}
    # Customer register: "ETC", not FIELD_LABELS' "ETC (estimated time of
    # container closing)" - this string ends up in a WhatsApp message.
    assert by_field["etc_date"]["label"] == "ETC"
    assert by_field["gatepass_date"]["label"] == "Gatepass"


def test_denied_entry_keeps_a_label_the_backend_already_sent():
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row()],
        "field_access": {
            "denied": [{"field": "etc_date", "label": "Backend Wins", "outcome": "x"}],
            "note": "n",
        },
    })
    assert out["field_access"]["denied"][0]["label"] == "Backend Wins"


def test_incoming_envelope_carries_the_field_vocabulary():
    """The case that needs it has NO denial: several containers, one carries
    `eta_delay_date` and the others do not. Nothing was withheld, so there is no
    `field_access` block to hang a label off, and the absent rows still have to
    be named. Humanising the key gives "Eta delay" and "Etd"; this gives the
    labels a present field would have rendered with.
    """
    out = env("crm_incoming_stock_list", {"data": [_incoming_row()]})
    assert "field_access" not in out, "nothing denied, so no denial block"
    vocab = out["field_vocabulary"]
    assert vocab["eta_delay_date"] == "ETA Delay"
    assert vocab["etd_date"] == "ETD"
    assert vocab["inspection_date"] == "CIDB Inspection"


def test_field_vocabulary_matches_what_a_present_field_renders_as():
    """Derived from `_CLEARANCE_PAIRS`, so the absent wording and the present
    wording cannot drift into two vocabularies - the exact failure that made
    label-matching untenable in the first place.
    """
    out = env("crm_incoming_stock_list", {
        "data": [_incoming_row(etc_date="2026-06-30", eta_delay_date="2026-07-12")],
    })
    rendered = {f["key"]: f["label"] for f in out["items"][0]["fields"] if "key" in f}
    vocab = out["field_vocabulary"]
    for key, label in rendered.items():
        if key in vocab:
            assert vocab[key] == label, f"{key} renders as {label!r} but vocabulary says {vocab[key]!r}"


def test_vocabulary_is_not_emitted_for_unrelated_result_types():
    """It is the clearance vocabulary, not a general one. Shipping it on orders
    or stock would imply those fields exist there.
    """
    out = env("crm_inventory_stock_balance_list", {"data": [{"product_code": "X"}]})
    assert "field_vocabulary" not in out


def test_document_answers_do_not_expose_the_file_uuid():
    """`render` is the CUSTOMER view and goes out on WhatsApp. The File ID row
    put an internal uuid under every document, next to the file itself.
    """
    out = env("crm_resource_attachments_list", {
        "data": [{
            "id": "1e900020-dba5-4e34-ae1c-5e0f90380095",
            "original_filename": "Container Status 2026.xlsx",
            "uploaded_at": "2026-08-08T06:31:13",
            "file_path": "https://cdn/x.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }],
    })
    fields = out["items"][0]["fields"]
    labels = [f["label"] for f in fields]
    assert labels == ["File Name", "Uploaded"]
    assert "1e900020-dba5-4e34-ae1c-5e0f90380095" not in json.dumps(fields)
    # The file itself still ships - that is the answer.
    assert out["attachments"][0]["filename"] == "Container Status 2026.xlsx"


def test_each_attachment_carries_its_own_upload_time():
    """A document class is re-uploaded under the same name, so several entries
    look identical. The envelope-level `last_updated_at` is the newest across the
    whole answer and says nothing about an individual file.
    """
    out = env("crm_resource_attachments_list", {
        "data": [
            {"original_filename": "Container Status 2026.xlsx", "file_path": "https://cdn/new.xlsx",
             "mime_type": "application/vnd.ms-excel", "uploaded_at": "2026-08-08T11:54:05"},
            {"original_filename": "Container Status 2026.xlsx", "file_path": "https://cdn/old.xlsx",
             "mime_type": "application/vnd.ms-excel", "uploaded_at": "2026-07-01T09:00:00"},
        ],
    })
    assert [a["uploadedAt"] for a in out["attachments"]] == [
        "2026-08-08T11:54:05", "2026-07-01T09:00:00",
    ]
    # Envelope keeps the newest of them, which is a different question.
    assert out["last_updated_at"] == "2026-08-08T11:54:05"


def test_attachment_without_an_upload_time_omits_the_key():
    out = env("crm_resource_attachments_list", {
        "data": [{"original_filename": "a.pdf", "file_path": "https://cdn/a.pdf"}],
    })
    assert "uploadedAt" not in out["attachments"][0]


# =============================================================================
# Multi-company reply clarity (UAC AC-C1..AC-C4,
# documentation/plans/multi-company/multi-company-reply-clarity-acceptance-criteria.md).
# =============================================================================


# --- AC-C1: lookup_companies passthrough ------------------------------------


def test_ac_c1_lookup_companies_passes_through_when_present():
    out = env("crm_inventory_stock_balance_list", {
        "data": [],
        "empty": True,
        "lookup_companies": [
            {"id": "00000000-0000-0000-0000-000000000001", "name": "Sorento"},
            {"id": "00000000-0000-0000-0000-000000000002", "name": "Mocha"},
        ],
    })
    assert out.get("lookup_companies") == [
        {"id": "00000000-0000-0000-0000-000000000001", "name": "Sorento"},
        {"id": "00000000-0000-0000-0000-000000000002", "name": "Mocha"},
    ]


def test_ac_c1_lookup_companies_omitted_not_null_when_absent():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5}],
    })
    assert "lookup_companies" not in out


def test_ac_c1_lookup_companies_omitted_when_explicitly_null():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5}],
        "lookup_companies": None,
    })
    assert "lookup_companies" not in out


# --- AC-C2: leading Company field, per builder -------------------------------


def test_ac_c2_stock_row_with_company_name_leads_with_company_field():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{
            "product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5,
            "company_name": "Mocha",
        }],
    })
    fields = out["items"][0]["fields"]
    assert fields[0] == {"key": "company_name", "label": "Company", "value": "Mocha"}


def test_ac_c2_stock_row_without_company_name_has_no_company_field():
    out = env("crm_inventory_stock_balance_list", {
        "data": [{"product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5}],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert "Company" not in labels


def test_ac_c2_orders_list_smoke_company_field():
    out = env("crm_order_management_orders_list", {
        "data": [{
            "order_number": "X1", "lines": [{"quantity": 1, "product": {"product_code": "AAA"}}],
            "company_name": "Mocha",
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_orders_by_product_smoke_company_field():
    out = env("crm_order_management_orders_by_product_list", {
        "data": [{
            "order_number": "X2",
            "matched_products": [{"product_code": "AAA", "quantity": 1}],
            "company_name": "Mocha",
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_incoming_list_smoke_company_field_from_shipment():
    """The company lives on the shipment (`s`), not the nested line."""
    out = env("crm_incoming_stock_list", {
        "data": [{
            "shipment_number": "SH1", "company_name": "Mocha",
            "lines": [{"product_code": "A", "remaining_incoming_quantity": 5}],
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_incoming_by_product_smoke_company_field_from_product():
    """The company lives on the product group (`p`), not the nested shipment."""
    out = env("crm_incoming_stock_by_product", {
        "data": [{
            "product_code": "A", "company_name": "Mocha",
            "shipments": [{"shipping_container_number": "C1", "remaining_incoming_quantity": 5}],
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_incoming_shipments_smoke_company_field():
    out = env("crm_incoming_stock_shipments", {
        "data": [{"shipment_number": "SH1", "company_name": "Mocha"}],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_promotions_smoke_company_field():
    out = env("crm_marketing_promotions_list", {
        "data": [{"description": "Promo A", "company_name": "Mocha"}],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_promotion_products_smoke_company_field():
    out = env("crm_marketing_promotion_products_list", {
        "data": [{
            "product": {"product_code": "A"}, "promotion": {"description": "Promo A"},
            "company_name": "Mocha",
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_products_smoke_company_field():
    out = env("crm_master_products_list", {
        "data": [{"product_code": "A", "product_name": "A", "company_name": "Mocha"}],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_product_attachments_smoke_company_field():
    out = env("crm_master_product_attachments_list", {
        "data": [{
            "product": {"product_code": "A"}, "attachment": {"original_filename": "a.pdf"},
            "company_name": "Mocha",
        }],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


def test_ac_c2_certificates_smoke_company_field():
    out = env("crm_certificates_list", {
        "data": [{"certificate_number": "CERT-1", "scheme": "PPS", "company_name": "Mocha"}],
    })
    fields = out["items"][0]["fields"]
    assert {"key": "company_name", "label": "Company", "value": "Mocha"} in fields


# --- AC-C3: empty-result intro names the companies searched -----------------


def test_ac_c3_empty_intro_names_two_companies():
    out = env("crm_inventory_stock_balance_list", {
        "data": [], "empty": True,
        "lookup_companies": [
            {"id": "00000000-0000-0000-0000-000000000002", "name": "Mocha"},
            {"id": "00000000-0000-0000-0000-000000000001", "name": "Sorento"},
        ],
    })
    assert out["has_result"] is False
    assert out["intro"] == "No matching results found for Mocha or Sorento."


def test_ac_c3_empty_intro_names_three_or_more_companies():
    out = env("crm_inventory_stock_balance_list", {
        "data": [], "empty": True,
        "lookup_companies": [
            {"id": "1", "name": "A"}, {"id": "2", "name": "B"}, {"id": "3", "name": "C"},
        ],
    })
    assert out["intro"] == "No matching results found for A, B or C."


def test_ac_c3_empty_intro_plain_when_a_company_name_is_missing():
    """F4 (review round): naming only the companies we happen to have a name for
    turns "I checked two companies" into a confident, wrong "I checked Sorento".
    A partial `lookup_companies` falls back to the plain intro."""
    out = env("crm_inventory_stock_balance_list", {
        "data": [], "empty": True,
        "lookup_companies": [
            {"id": "00000000-0000-0000-0000-000000000001", "name": "Sorento"},
            {"id": "00000000-0000-0000-0000-000000000002", "name": None},
        ],
    })
    assert out["intro"] == "No matching results found."


def test_ac_c3_empty_intro_plain_when_every_company_name_is_missing():
    out = env("crm_inventory_stock_balance_list", {
        "data": [], "empty": True,
        "lookup_companies": [{"id": "00000000-0000-0000-0000-000000000001"}],
    })
    assert out["intro"] == "No matching results found."


def test_ac_c3_empty_intro_unchanged_without_lookup_companies():
    out = env("crm_master_products_list", {"data": [], "empty": True})
    assert out["intro"] == "No matching results found."


# --- AC-C4: byte-identical single-company output -----------------------------


def test_ac_c4_byte_identical_when_company_keys_null_vs_absent_stock():
    # Realistic single-company shape: the row DOES carry its own company_id
    # (the ORM column, filled by `from_attributes`); only `company_name` and
    # `lookup_companies` are null because the lookup spanned one company.
    with_nulls = {
        "data": [{
            "product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5,
            "company_id": "00000000-0000-0000-0000-000000000001",
            "company_name": None,
        }],
        "lookup_companies": None,
    }
    without_keys = {
        "data": [{"product_code": "A", "warehouse": "BRW", "quantity_on_hand": 5}],
    }
    assert present_response(
        "crm_inventory_stock_balance_list", json.dumps(with_nulls)
    ) == present_response("crm_inventory_stock_balance_list", json.dumps(without_keys))


def test_ac_c4_byte_identical_when_company_keys_null_vs_absent_orders():
    with_nulls = {
        "data": [{
            "order_number": "X1", "lines": [{"quantity": 1, "product": {"product_code": "AAA"}}],
            "company_id": "00000000-0000-0000-0000-000000000001",
            "company_name": None,
        }],
        "lookup_companies": None,
    }
    without_keys = {
        "data": [{
            "order_number": "X1", "lines": [{"quantity": 1, "product": {"product_code": "AAA"}}],
        }],
    }
    assert present_response(
        "crm_order_management_orders_list", json.dumps(with_nulls)
    ) == present_response("crm_order_management_orders_list", json.dumps(without_keys))


def test_ac_c4_byte_identical_empty_result_null_lookup_companies():
    with_null = {"data": [], "empty": True, "lookup_companies": None}
    without_key = {"data": [], "empty": True}
    assert present_response(
        "crm_master_products_list", json.dumps(with_null)
    ) == present_response("crm_master_products_list", json.dumps(without_key))


# --- QS-C8: summary + pagination passthrough (order-quantity-summary) -----------


_QS_SUMMARY = {
    "scope": "filter", "row_count": 35, "order_count": 35, "delivered_count": 33,
    "pending_count": 2, "customers": ["ECO WORLD SDN BHD"], "customer_count": 1,
    "delivered_from": "2026-03-02", "delivered_to": "2026-07-15",
    "products": [{"product_code": "SRTWC8605", "delivered_quantity": 48, "pending_quantity": 12}],
}

_QS_ROW = {"order_number": "202603-0412", "debtor_name": "ECO WORLD SDN BHD",
           "order_date": "2026-03-01", "actual_delivery_date": "2026-03-02",
           "matched_products": [{"product_code": "SRTWC8605", "quantity": 10}]}


def test_qs_c8_summary_passes_through_untouched_pagination_does_not():
    out = env("crm_order_management_orders_by_product_list", {
        "data": [_QS_ROW],
        "pagination": {"total": 35, "page": 1, "limit": 20},
        "summary": _QS_SUMMARY,
    })
    assert out["summary"] == _QS_SUMMARY
    assert "pagination" not in out  # no reader on the n8n side; row_count lives in summary
    # the rows themselves render exactly as before
    assert out["items"][0]["title"] == "202603-0412"


def test_qs_c8_orders_list_passes_summary_too():
    out = env("crm_order_management_orders_list", {
        "data": [{**_QS_ROW, "lines": []}],
        "pagination": {"total": 1, "page": 1, "limit": 20},
        "summary": {**_QS_SUMMARY, "row_count": 1, "order_count": 1},
    })
    assert out["summary"]["row_count"] == 1


def test_qs_c8_summary_omitted_not_null_when_absent():
    out = env("crm_order_management_orders_by_product_list", {"data": [_QS_ROW]})
    assert "summary" not in out


def test_qs_c8_summary_omitted_when_explicitly_null():
    out = env("crm_order_management_orders_by_product_list", {"data": [_QS_ROW], "summary": None})
    assert "summary" not in out


def test_qs_c6_by_product_catalog_accepts_order_status():
    from sorento_crm_mcp.catalog import CATALOG
    spec = next(s for s in CATALOG if s.name == "crm_order_management_orders_by_product_list")
    assert "order_status" in spec.query_params
    assert "include_summary" in spec.query_params
    lst = next(s for s in CATALOG if s.name == "crm_order_management_orders_list")
    assert "include_summary" in lst.query_params


# --- QS-M: the presenter renders `summary_items` (order-quantity-summary amendment 4) -------
# Same shape as items[]: {title, fields[{key,label,value}]}. Ids mirror tests/uac/QS.md §QS-M.

import json as _json
import pathlib as _pathlib

from sorento_crm_mcp.presenters import summary_items, summary_intro

_QS6 = _json.loads((_pathlib.Path(__file__).parent / "fixtures" / "qs6-envelopes.json").read_text())

_G_ECO = {"customer": "ECO WORLD SDN BHD", "product_code": "SRTWC8605", "order_count": 5,
          "delivered_quantity": 48, "pending_quantity": 17,
          "delivered_from": "2026-03-02", "delivered_to": "2026-07-15"}
_P_8605 = {"product_code": "SRTWC8605", "order_count": 5, "delivered_quantity": 48, "pending_quantity": 17,
           "delivered_from": "2026-03-02", "delivered_to": "2026-07-15"}
_SUM_M1 = {"scope": "filter", "row_count": 5, "order_count": 5, "delivered_count": 3, "pending_count": 2,
           "customers": ["ECO WORLD SDN BHD"], "customer_count": 1,
           "delivered_from": "2026-03-02", "delivered_to": "2026-07-15",
           "products": [_P_8605], "groups": [_G_ECO]}


def _fields(item):
    return [(f["key"], f["label"], f["value"]) for f in item["fields"]]


def test_qs_m1_single_customer_single_product_is_one_item_in_the_item_shape():
    g = {**_G_ECO, "order_date_from": "2026-03-01", "order_date_to": "2026-07-20"}
    s = {**_SUM_M1, "groups": [g]}
    items = summary_items(s)
    assert len(items) == 1
    assert items[0]["title"] == "ECO WORLD SDN BHD · SRTWC8605"
    # D3 (owner, 8 Sep 2026): the DO block in the owner's order and labels
    assert _fields(items[0]) == [
        ("customer", "Customer", "ECO WORLD SDN BHD"),
        ("product_code", "Product Code", "SRTWC8605"),
        ("order_count", "DO", 5),
        ("order_date", "DO Date", "01/03/2026 \u2013 20/07/2026"),
        ("delivered_quantity", "Delivered", 48),
        ("delivered_between", "Delivery Date", "02/03/2026 – 15/07/2026"),
        ("pending_quantity", "DO Outstanding", 17),
    ]
    # every field carries a key - consumers match on it, never on the label
    assert all(set(f) == {"key", "label", "value"} for f in items[0]["fields"])


def test_qs_m10_order_date_absent_when_backend_sent_none():
    """The recorded qs6 envelopes were captured before this field existed - no
    order_date_from/to on their groups[], so no `order_date` field renders."""
    items = summary_items(_SUM_M1)  # _G_ECO carries no order_date_from/to
    assert "order_date" not in dict((k, v) for k, _, v in _fields(items[0]))


def test_qs_m11_order_date_single_day_renders_one_date():
    g = {**_G_ECO, "order_date_from": "2026-05-04", "order_date_to": "2026-05-04"}
    s = {**_SUM_M1, "groups": [g]}
    items = summary_items(s)
    assert dict((k, v) for k, _, v in _fields(items[0]))["order_date"] == "04/05/2026"


def test_qs_m2_multi_customer_gets_a_leading_total_then_one_item_per_customer():
    g2 = {**_G_ECO, "customer": "HANLIM TRADING SDN BHD", "order_count": 1, "delivered_quantity": 7,
          "pending_quantity": 0, "delivered_from": "2026-08-01", "delivered_to": "2026-08-01"}
    s = {**_SUM_M1, "customer_count": 2, "customers": ["ECO WORLD SDN BHD", "HANLIM TRADING SDN BHD"],
         "products": [{**_P_8605, "order_count": 6, "delivered_quantity": 55, "delivered_to": "2026-08-01"}],
         "groups": [_G_ECO, g2]}
    items = summary_items(s)
    assert [i["title"] for i in items] == ["SRTWC8605", "ECO WORLD SDN BHD · SRTWC8605",
                                           "HANLIM TRADING SDN BHD · SRTWC8605"]
    total = dict((k, v) for k, _, v in _fields(items[0]))
    assert total["customers"] == 2 and "customer" not in total                       # D3: a Customers field, not a title
    assert _fields(items[0])[0] == ("customers", "Customers", 2)
    assert total["delivered_quantity"] == 55 and total["order_count"] == 6           # products[], not a sum
    assert total["delivered_between"] == "02/03/2026 – 01/08/2026"
    hanlim = dict((k, v) for k, _, v in _fields(items[2]))
    assert hanlim["delivered_between"] == "01/08/2026"                              # same-day span collapses
    assert "pending_quantity" in hanlim and hanlim["pending_quantity"] == 0         # 0 is a value, kept


def test_qs_m3_two_products_bucket_under_their_own_product():
    g_b = {**_G_ECO, "product_code": "SRTWC287-ARL", "order_count": 1, "delivered_quantity": 6, "pending_quantity": 0}
    s = {**_SUM_M1, "products": [{**_P_8605, "product_code": "SRTWC287-ARL", "delivered_quantity": 6}, _P_8605],
         "groups": [g_b, _G_ECO]}   # CRM order: customer then product
    items = summary_items(s)
    assert [i["title"] for i in items] == ["ECO WORLD SDN BHD · SRTWC287-ARL", "ECO WORLD SDN BHD · SRTWC8605"]


def test_qs_m4_real_multi68_envelope():
    S = _QS6["multi68"]["envelope"]["summary"]        # captured before amendment 4: no dates on groups
    items = summary_items(S)
    named = [g for g in S["groups"] if isinstance(g.get("customer"), str) and g["customer"].strip()]
    assert len(items) == 1 + len(named)
    assert items[0]["title"] == S["products"][0]["product_code"]
    assert dict((k, v) for k, _, v in _fields(items[0]))["customers"] == len(named)
    total = dict((k, v) for k, _, v in _fields(items[0]))
    assert total["delivered_quantity"] == S["products"][0]["delivered_quantity"]
    assert "delivered_between" not in total                # old shape has no per-product dates: dropped, not None
    for it, g in zip(items[1:], named):
        assert it["title"] == f"{g['customer'].strip()} · {g['product_code']}"


def test_qs_m5_no_groups_still_states_the_product_total():
    s = {**_SUM_M1, "groups": []}
    items = summary_items(s)
    assert len(items) == 1 and items[0]["title"] == "SRTWC8605"
    assert _fields(items[0])[0] == ("product_code", "Product Code", "SRTWC8605")


def test_qs_m6_unnameable_rows_are_dropped_not_coerced():
    s = {**_SUM_M1, "products": [{"product_code": {}, "delivered_quantity": 5}, _P_8605],
         "groups": [{"customer": {"n": 1}, "product_code": "SRTWC8605", "delivered_quantity": 1}, _G_ECO]}
    items = summary_items(s)
    assert [i["title"] for i in items] == ["ECO WORLD SDN BHD · SRTWC8605"]
    dumped = _json.dumps(items)
    assert '"n": 1' not in dumped and "None" not in dumped and "null" not in dumped   # nothing coerced or leaked


def test_qs_m7_hostile_leaves_never_raise_or_leak():
    hostile = {"scope": "filter", "row_count": "1e5000", "customers": ["X"], "customer_count": True,
               "products": [{"product_code": "P", "order_count": float("nan"), "delivered_quantity": "1e5000",
                             "pending_quantity": "inf", "delivered_from": "not-a-date", "delivered_to": "2026-13-45"}],
               "groups": "nope"}
    items = summary_items(hostile)
    dumped = _json.dumps(items)
    assert "e+" not in dumped and "nan" not in dumped.lower() and "inf" not in dumped.lower()
    assert "not-a-date" not in dumped and "2026-13-45" not in dumped and "None" not in dumped
    for bad in ("not an object", [1, 2], {"products": "x", "groups": 5}):
        assert summary_items(bad) == []
    assert summary_intro(hostile, 3) is None                       # row_count unrenderable -> no intro


def test_qs_m8_intro_is_gone_except_the_truncation_notice():
    """D3 (owner, 8 Sep 2026): "Summary over N DOs." is struck; the truncation notice
    stays as its own line."""
    assert summary_intro({"row_count": 8}, 2) is None
    assert summary_intro({"row_count": 1}, 1) is None
    assert summary_intro({"row_count": 700, "groups_truncated": True}, 20) == (
        "Not every breakdown is shown — add a customer, a product or a date range.")
    assert "Not every breakdown" in summary_intro({"row_count": 9, "products_truncated": True}, 9)


def test_qs_m9_total_uses_the_crm_customer_count_not_the_visible_slice():
    """Codex F: after the 500-row ceiling groups[] may hold ONE row for a product that 68 customers
    took - the Total must still appear and say 68, from products[].customer_count."""
    s = {**_SUM_M1, "customer_count": 68, "groups_truncated": True,
         "products": [{**_P_8605, "customer_count": 68, "order_count": 187, "delivered_quantity": 32649}],
         "groups": [_G_ECO]}
    items = summary_items(s)
    assert items[0]["title"] == "SRTWC8605"
    assert dict((k, v) for k, _, v in _fields(items[0]))["customers"] == 68
    assert dict((k, v) for k, _, v in _fields(items[0]))["delivered_quantity"] == 32649
    assert items[1]["title"] == "ECO WORLD SDN BHD · SRTWC8605"
    # and with no customer_count on the product (older CRM) it falls back to the visible rows
    s2 = {**_SUM_M1, "groups": [_G_ECO]}
    assert [i["title"] for i in summary_items(s2)] == ["ECO WORLD SDN BHD · SRTWC8605"]


def test_qs_m0_summary_items_absent_unless_a_real_answer(monkeypatch):
    base = {"data": [{**_QS_ROW, "lines": []}], "pagination": {"total": 1, "page": 1, "limit": 20}}
    out = env("crm_order_management_orders_list", {**base, "summary": _SUM_M1})
    assert out["summary_items"] == summary_items(_SUM_M1)
    assert out["intro"] == "Here are the orders I found."   # D3: no "Summary over N DOs." line
    assert "summary_lines" not in out
    assert "summary_items" not in env("crm_order_management_orders_list", base)                        # no summary
    assert "summary_items" not in env("crm_order_management_orders_list", {"data": [], "summary": _SUM_M1})  # no rows
    plain = env("crm_order_management_orders_list", {**base, "summary": {"scope": "filter"}})
    assert "summary_items" not in plain and plain["intro"] == "Here are the orders I found."           # degrades: intro untouched
    import sorento_crm_mcp.presenters as _p
    monkeypatch.setattr(_p, "summary_items", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    out2 = env("crm_order_management_orders_list", {**base, "summary": _SUM_M1})
    assert out2["items"] and "summary_items" not in out2 and out2["intro"] == "Here are the orders I found."


# ------------------------------------------------ SO outstanding per row (by-product)

_BP_ROW = {"product_code": "SRTWC286", "order_count": 2, "customer_count": 1,
           "delivered_quantity": 10, "pending_quantity": 3, "so_outstanding_qty": 7}
_BP_GRP = {"customer": "HENG SENG HARDWARE SDN BHD", **_BP_ROW}


def test_by_product_so_block_renders_in_the_owners_order_before_the_do_block():
    """D3 (owner console pass, 8 Sep 2026): SO, SO Date, Ordered, Transferred to DO,
    SO Outstanding, then DO, DO Date, Delivered, Delivery Date, DO Outstanding - one field
    per line, never at the top level of the by-product summary (that path would print DO
    counts as quantities on a multi-variant row)."""
    row = {**_BP_ROW, "so_count": 3, "so_date_from": "2026-05-01", "so_date_to": "2026-06-10",
           "so_ordered_qty": 30, "so_transferred_qty": 23, "so_outstanding_qty": 7,
           "order_date_from": "2026-05-03", "order_date_to": "2026-06-12",
           "delivered_from": "2026-05-04", "delivered_to": "2026-06-13"}
    out = env("crm_order_management_orders_by_product_list", {
        "data": [{"order_number": "A1", "debtor_name": "HENG SENG HARDWARE SDN BHD"}],
        "summary": {"scope": "filter", "row_count": 2, "order_count": 2, "delivered_count": 1,
                    "pending_count": 1, "customers": ["HENG SENG HARDWARE SDN BHD"], "customer_count": 1,
                    "products": [row], "groups": [{"customer": "HENG SENG HARDWARE SDN BHD", **row}]},
    })
    items = out["summary_items"]
    assert len(items) == 1
    assert [(f["key"], f["label"], f["value"]) for f in items[0]["fields"]] == [
        ("customer", "Customer", "HENG SENG HARDWARE SDN BHD"),
        ("product_code", "Product Code", "SRTWC286"),
        ("so_count", "SO", 3),
        ("so_date", "SO Date", "01/05/2026 \u2013 10/06/2026"),
        ("so_ordered_qty", "Ordered", 30),
        ("so_transferred_qty", "Transferred to DO", 23),
        ("so_outstanding_qty", "SO Outstanding", 7),
        ("order_count", "DO", 2),
        ("order_date", "DO Date", "03/05/2026 \u2013 12/06/2026"),
        ("delivered_quantity", "Delivered", 10),
        ("delivered_between", "Delivery Date", "04/05/2026 \u2013 13/06/2026"),
        ("pending_quantity", "DO Outstanding", 3),
    ]
    assert out["intro"] == "Here are the orders I found."


def test_by_product_so_zero_still_prints_and_the_total_row_carries_customers():
    grp_a = {**_BP_GRP, "so_outstanding_qty": 7, "so_count": 2}
    grp_b = {"customer": "QUIET SDN BHD", **_BP_ROW, "so_outstanding_qty": 0, "so_count": 0}
    total = {**_BP_ROW, "customer_count": 2, "so_outstanding_qty": 7, "so_count": 2}
    out = env("crm_order_management_orders_by_product_list", {
        "data": [{"order_number": "A1"}],
        "summary": {"scope": "filter", "row_count": 3, "products": [total], "groups": [grp_a, grp_b]},
    })
    items = out["summary_items"]
    assert items[0]["title"] == "SRTWC286"
    assert items[0]["fields"][0] == {"key": "customers", "label": "Customers", "value": 2}
    so_values = [next(f["value"] for f in it["fields"] if f["key"] == "so_outstanding_qty") for it in items]
    assert so_values == [7, 7, 0]
    so_counts = [next(f["value"] for f in it["fields"] if f["key"] == "so_count") for it in items]
    assert so_counts == [2, 2, 0]


def test_by_product_summary_without_the_key_renders_no_so_field():
    row = {k: v for k, v in _BP_ROW.items() if k != "so_outstanding_qty"}
    out = env("crm_order_management_orders_by_product_list", {
        "data": [{"order_number": "A1"}],
        "summary": {"scope": "filter", "row_count": 2, "products": [row],
                    "groups": [{"customer": "HENG SENG HARDWARE SDN BHD", **row}]},
    })
    for it in out["summary_items"]:
        assert "so_outstanding_qty" not in [f["key"] for f in it["fields"]]
        assert "SO Outstanding" not in [f["label"] for f in it["fields"]]


def test_purchase_orders_placed_field_order_no_source_no_expected_date():
    """Owner ruling (11 Sep 2026): the row order is PO Number, Product Code, Ordered Qty,
    Outstanding Qty, PO Date, Location - Source (`kind`) and Expected Date never render
    again, even when the row still carries `kind` / `expected_date` (the backend keeps
    both on the raw row for its own filtering/sorting; only the presenter drops them)."""
    out = env("crm_procurement_po_placed_list", {
        "data": [{
            "company_name": "Sorento HQ", "po_number": "PO-1001", "kind": "po",
            "product_code": "SRTWC8517", "ordered_qty": 50, "outstanding_qty": 50,
            "po_date": "2026-05-01", "expected_date": "2026-07-01",
            "location": "KL-WH", "supplier": "Acme Supplies",
        }],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert labels == [
        "Company", "PO Number", "Product Code", "Ordered Qty",
        "Outstanding Qty", "PO Date", "Location", "Supplier",
    ]
    assert "Source" not in labels
    assert "Expected Date" not in labels


def test_purchase_orders_placed_without_company_starts_at_po_number():
    """Company is scope-gated and only present when the resolver widened scope; unchanged
    by this ruling."""
    out = env("crm_procurement_po_placed_list", {
        "data": [{"po_number": "PO-1001", "product_code": "SRTWC8517", "outstanding_qty": 50}],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert labels[0] == "PO Number"
    assert "Company" not in labels


def test_purchase_orders_placed_row_without_ordered_qty_or_location_omits_them():
    out = env("crm_procurement_po_placed_list", {
        "data": [{"po_number": "PO-1001", "product_code": "SRTWC8517", "outstanding_qty": 50}],
    })
    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert "Ordered Qty" not in labels
    assert "Location" not in labels


def test_purchase_orders_placed_ordered_qty_and_location_render_when_present():
    out = env("crm_procurement_po_placed_list", {
        "data": [{"po_number": "PO-1001", "product_code": "SRTWC8517",
                  "ordered_qty": 30, "outstanding_qty": 30, "location": "BRW"}],
    })
    f = {x["label"]: x["value"] for x in out["items"][0]["fields"]}
    assert f["Ordered Qty"] == "30"
    assert f["Location"] == "BRW"


def test_purchase_orders_placed_item_carries_kind_at_the_top_level():
    """The item dict now carries `kind` as a TOP-LEVEL key (sibling of `title`/`fields`/
    `flags`), not a rendered field - the chatbot rung reads it from there so it can still
    tell a PO row from an SPO row with no rendered Source field."""
    out = env("crm_procurement_po_placed_list", {
        "data": [
            {"po_number": "202607-S0031", "kind": "po", "product_code": "C-FH14", "outstanding_qty": 27},
            {"po_number": "SPO-2026/09-0001", "kind": "spo", "product_code": "C-FH14", "outstanding_qty": 7},
        ],
    })
    assert [item.get("kind") for item in out["items"]] == ["po", "spo"]
    for item in out["items"]:
        assert "Source" not in [f["label"] for f in item["fields"]]
        assert "kind" not in [f.get("key") for f in item["fields"]]


def test_purchase_orders_placed_item_has_no_kind_key_when_row_has_none():
    """Byte identity for an older/kind-less row: no top-level `kind` key at all, not
    `None`."""
    out = env("crm_procurement_po_placed_list", {
        "data": [{"po_number": "PO-1001", "product_code": "SRTWC8517", "outstanding_qty": 50}],
    })
    assert "kind" not in out["items"][0]


def test_product_attachment_with_no_link_is_listed_with_the_unavailable_note_and_never_attached():
    """D9 (8 Sep 2026): a file whose link could not be signed stays in the answer (name,
    type) with "(file link unavailable right now)" and produces no attachment entry."""
    out = env("crm_master_product_attachments_list", {
        "data": [
            {"product": {"product_code": "CWSP124"}, "attachment": {"original_filename": "CWSP124-drawing.pdf",
             "attachment_type": {"type_name": "Technical Drawing"}, "file_path": None, "mime_type": "application/pdf"}},
            {"product": {"product_code": "CWSP124"}, "attachment": {"original_filename": "CWSP124.jpg",
             "attachment_type": {"type_name": "Product Photos"}, "file_path": "https://cdn-sorento.com/x/CWSP124.jpg", "mime_type": "image/jpeg"}},
        ],
    })
    first = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert first["File Name"] == "CWSP124-drawing.pdf"
    assert first["File Link"] == "(file link unavailable right now)"
    second = {f["label"]: f["value"] for f in out["items"][1]["fields"]}
    assert "File Link" not in second
    assert [a["filename"] for a in out["attachments"]] == ["CWSP124.jpg"]


# --- PR #1329 fix round: the dealer's incoming reply ---------------------------------


def test_dealer_view_is_one_line_per_product_and_the_refer_line():
    """The backend's dealer view (an "Availability only" contact): the product code once,
    its distinct ETAs, and the one refer sentence (REFER-SALESMAN, 30 Sep 2026: never a
    name, never "salesperson"). No intro, no numbering, no field of ours."""
    for tool in ("crm_incoming_stock_list", "crm_incoming_stock_by_product"):
        out = env(tool, {
            "data": [
                {"product_code": "SRTWC286-SH-NEW", "etas": ["2026-09-08", "2026-09-20"]},
            ],
            "dealer_view": True,
        })
        assert out["result_type"] == "incoming_dealer"
        assert out["intro"] == ""
        assert [i["title"] for i in out["items"]] == ["SRTWC286-SH-NEW: \u2705 ETA 2026-09-08, 2026-09-20"]
        assert out["items"][0]["fields"] == []
        assert out["closing"] == "Please refer to your salesman."
        assert out["has_result"] is True


def test_dealer_view_ignores_a_salesperson_name_the_payload_still_carries():
    """An older backend that still sends `salesperson_name` gets the same one sentence."""
    out = env("crm_incoming_stock_list", {
        "data": [{"product_code": "A", "etas": ["2026-09-08"]}],
        "dealer_view": True,
        "salesperson_name": "Sean",
    })
    assert out["closing"] == "Please refer to your salesman."
    assert "Sean" not in str(out)


def test_dealer_view_with_nothing_incoming_is_a_miss():
    out = env("crm_incoming_stock_list", {
        "data": [], "empty": True, "dealer_view": True,
    })
    assert out["has_result"] is False
    assert out["items"] == []


def test_identical_incoming_rows_print_once():
    """Two lines that read the same once the contact's reveals are applied (the
    container and quantities withheld) are one line to the reader."""
    row = {
        "shipment_number": "SH1",
        "estimated_arrival_date": "2026-09-08",
        "lines": [{"product_code": "A", "warehouse_allocations": [{"warehouse_code": "BRW"}]}],
    }
    out = env("crm_incoming_stock_list", {"data": [row, {**row, "shipment_number": "SH2"}]})
    assert len(out["items"]) == 1


def test_incoming_rows_that_differ_still_print_apart():
    rows = [
        {"shipment_number": "SH1", "shipping_container_number": "C1",
         "estimated_arrival_date": "2026-09-08", "lines": [{"product_code": "A"}]},
        {"shipment_number": "SH2", "shipping_container_number": "C2",
         "estimated_arrival_date": "2026-09-08", "lines": [{"product_code": "A"}]},
    ]
    out = env("crm_incoming_stock_list", {"data": rows})
    assert len(out["items"]) == 2


def test_stock_compact_never_prints_an_unassigned_line():
    """STOCK-TOTAL-OS-SCOPE, owner option (b) 2 Oct 2026: open SO with no warehouse is not
    shown anywhere in the stock reply. Even a payload that carried such a key renders only
    Product Code, Total and the locations."""
    out = env("crm_inventory_stock_balance_list", {
        "data": [],
        "stock_visibility": {"mode": "compact", "source": "access_type"},
        "stock_summary": [{
            "product_id": "p1", "product_code": "MWC7624-RL-S10", "product_name": "MWC7624-RL-S10",
            "total_on_hand": 54, "open_so_qty": 0, "sellable": 54, "unassigned_open_so_qty": 12,
            "locations": [
                {"warehouse_code": "BRW", "quantity_on_hand": 0, "open_so_qty": 0},
                {"warehouse_code": "MWH", "quantity_on_hand": 54, "open_so_qty": 0},
            ],
            "flags": {},
        }],
    })
    fields = out["items"][0]["fields"]
    assert [f["label"] for f in fields] == ["Product Code", "Total", "BRW", "MWH"]
    assert fields[1]["granted_value"] == "54 (O/S: 0)"
    assert "12" not in json.dumps(out)
