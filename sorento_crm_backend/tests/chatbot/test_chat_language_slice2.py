"""CHAT-LANGUAGE slice 2 (PO / SO / SPO / orders rows), AC-CL20..AC-CL26.

UAC: `documentation/plans/chatbot/chat-language-acceptance-criteria.md`, "Slice 2". The slice-2
catalog entries do not exist yet and `fetch._LOCALIZED_TOOLS` still lists only the stock tool,
so a translated render of these five tools prints English today. Envelopes are built by hand in
the shape the MCP presenter emits (`sorento_crm_mcp/presenters.py::_orders_list`,
`_orders_so_outstanding`, `_purchase_orders_placed`, `_spo_last_receipt`, `_po_last_cost`,
`_orders_by_product`), the way `test_chat_language_render.py` and `test_restricted_fields.py`
build theirs. Every translated render is also compared VALUE by VALUE with the English render:
a Localizer rewrites labels and fixed sentences, never a value.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import IDENTITY, Localizer
from app.services.chatbot.lanes.business import fetch

TS = "2026-10-02T09:15:30"

TOOL_ORDERS = "crm_order_management_orders_list"
TOOL_ORDERS_BY_PRODUCT = "crm_order_management_orders_by_product_list"
TOOL_PO_PLACED = "crm_procurement_po_placed_list"
TOOL_SPO = "crm_procurement_spo_allocations_last_receipt_list"
TOOL_PO_COST = "crm_procurement_po_last_cost_list"
SLICE2_TOOLS = (TOOL_ORDERS, TOOL_ORDERS_BY_PRODUCT, TOOL_PO_PLACED, TOOL_SPO, TOOL_PO_COST)

# The slice-2 table, pinned as literals so a drifting catalog fails loudly (the UAC's table).
SLICE2: dict[str, tuple[str, str]] = {
    "Order Number": ("No. Pesanan", "订单号"),
    "Customer": ("Pelanggan", "客户"),
    "Order Date": ("Tarikh Pesanan", "订单日期"),
    "Actual Delivery Date": ("Tarikh Penghantaran Sebenar", "实际送货日期"),
    "Status": ("Status", "状态"),
    "Pickup Time": ("Masa Pengambilan", "提货时间"),
    "Transporter": ("Pengangkut", "运输商"),
    "Driver": ("Pemandu", "司机"),
    "Lorry Plate": ("No. Plat Lori", "车牌号"),
    "Products": ("Produk", "产品"),
    "SO Number": ("No. SO", "SO 编号"),
    "Outstanding Qty": ("Kuantiti Tertunggak", "未交货数量"),
    "Requested Delivery Date": ("Tarikh Penghantaran Diminta", "要求送货日期"),
    "PO Number": ("No. PO", "PO 编号"),
    "Ordered Qty": ("Kuantiti Dipesan", "订购数量"),
    "PO Date": ("Tarikh PO", "PO 日期"),
    "Location": ("Lokasi", "位置"),
    "Supplier": ("Pembekal", "供应商"),
    "SPO Number": ("No. SPO", "SPO 编号"),
    "Container Number": ("No. Kontena", "货柜号"),
    "SPO Quantity": ("Kuantiti SPO", "SPO 数量"),
    "GR Quantity": ("Kuantiti GR", "GR 数量"),
    "SPO Date": ("Tarikh SPO", "SPO 日期"),
    "SPO Date (recorded)": ("Tarikh SPO (direkodkan)", "SPO 日期（已记录）"),
    "GR Date": ("Tarikh GR", "GR 日期"),
    "PO Quantity": ("Kuantiti PO", "PO 数量"),
    "Cost / unit": ("Kos / unit", "单位成本"),
    "Discount / unit": ("Diskaun / unit", "单位折扣"),
    "Cost after discount / unit": ("Kos selepas diskaun / unit", "折后单位成本"),
    "Here are the orders I found.": (
        "Berikut ialah pesanan yang saya temui.",
        "以下是我找到的订单。",
    ),
    "Here is the PO placed I found.": (
        "Berikut ialah PO yang telah dibuat.",
        "以下是已下的 PO。",
    ),
    "Here is the last SPO line per product.": (
        "Berikut ialah baris SPO terakhir bagi setiap produk.",
        "以下是每个产品最近的 SPO 记录。",
    ),
    "Here is the last purchase cost per product and location.": (
        "Berikut ialah kos belian terakhir bagi setiap produk dan lokasi.",
        "以下是每个产品和位置最近一次的采购成本。",
    ),
    "Here is the outstanding SO I found.": (
        "Berikut ialah SO tertunggak yang saya temui.",
        "以下是我找到的未交货 SO。",
    ),
    "Here are the outstanding orders I found.": (
        "Berikut ialah pesanan tertunggak yang saya temui.",
        "以下是我找到的未交货订单。",
    ),
    "Here are the delivered orders I found.": (
        "Berikut ialah pesanan yang telah dihantar.",
        "以下是我找到的已送货订单。",
    ),
    "No matching results found.": (
        "Tiada hasil yang sepadan ditemui.",
        "未找到匹配的结果。",
    ),
    "No matching results found for {companies}.": (
        "Tiada hasil yang sepadan ditemui untuk {companies}.",
        "在 {companies} 中未找到匹配的结果。",
    ),
    "Here are the results I found.": (
        "Berikut ialah hasil yang saya temui.",
        "以下是我找到的结果。",
    ),
    "EXPIRED": ("TAMAT TEMPOH", "已过期"),
    "PENDING ALLOCATION": ("MENUNGGU PERUNTUKAN", "待分配"),
    "PARTIAL ALLOCATION": ("PERUNTUKAN SEBAHAGIAN", "部分分配"),
}

# FIELD_KEYS additions (UAC): presenter field key -> English label.
SLICE2_FIELD_KEYS = {
    "so_number": "SO Number",
    "outstanding_qty": "Outstanding Qty",
    "order_date": "Order Date",
    "customer": "Customer",
    "requested_delivery_date": "Requested Delivery Date",
    "po_number": "PO Number",
    "ordered_qty": "Ordered Qty",
    "po_date": "PO Date",
    "location": "Location",
    "supplier": "Supplier",
    "spo_number": "SPO Number",
    "container_number": "Container Number",
    "spo_quantity": "SPO Quantity",
    "gr_quantity": "GR Quantity",
    "spo_date": "SPO Date",
    "gr_date": "GR Date",
    "warehouse": "Warehouse",
    "po_quantity": "PO Quantity",
    "unit_cost": "Cost / unit",
    "discount_per_unit": "Discount / unit",
    "unit_cost_after_discount": "Cost after discount / unit",
}


def _presenters():
    """The real MCP presenter, imported the way `test_chat_language_catalog.py` does."""
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp import presenters
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return presenters


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _ctx(tool: str, lang: str | None = None, **extra) -> dict:
    ctx: dict = {"semantic_input": {}, "tool": tool, **extra}
    if lang is not None:
        ctx["localizer"] = _loc(lang)
    return ctx


def _render(env: dict, tool: str, lang: str | None = None, **extra) -> str:
    return fetch.output_structurer(copy.deepcopy(env), _ctx(tool, lang, **extra))["response"]


_FIELD_LINE = re.compile(r"^(?:\d+\. )?\*(.+?):\* (.*)$")


def _values(text: str) -> list[str]:
    """The VALUE half of every `*Label:* value` line, in print order."""
    out = []
    for line in text.splitlines():
        m = _FIELD_LINE.match(line)
        if m:
            out.append(m.group(2))
    return out


def _assert_values_identical(env: dict, tool: str, lang: str, **extra) -> str:
    """Render en and `lang`; every value string is byte-identical and in the same order, and
    every envelope value appears. Returns the translated text."""
    en = _render(env, tool, None, **extra)
    tr = _render(env, tool, lang, **extra)
    assert _values(en), "the English render printed no field lines"
    assert _values(tr) == _values(en)
    for item in env["items"]:
        for f in item["fields"]:
            assert str(f["value"]) in tr, f
    return tr


# --------------------------------------------------------------------------- #
# Envelopes, in the presenter's shape
# --------------------------------------------------------------------------- #


def _po_placed_envelope(*, intro: str = "Here is the PO placed I found.") -> dict:
    """`_purchase_orders_placed`: keyed fields, `supplier` restricted."""
    return {
        "result_type": "purchase_orders_placed",
        "intro": intro,
        "items": [
            {
                "title": "PO-2026-0412",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"key": "po_number", "label": "PO Number", "value": "PO-2026-0412"},
                    {"key": "product_code", "label": "Product Code", "value": "BRBC22102W"},
                    {"key": "ordered_qty", "label": "Ordered Qty", "value": "120"},
                    {"key": "outstanding_qty", "label": "Outstanding Qty", "value": "45"},
                    {"key": "po_date", "label": "PO Date", "value": "2026-09-11"},
                    {"key": "location", "label": "Location", "value": "BRW-BB"},
                    {"key": "supplier", "label": "Supplier", "value": "ACME CERAMICS SDN BHD"},
                ],
                "flags": {},
                "kind": "po",
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
        "restricted_fields": {"supplier": "purchase_orders.supplier"},
    }


def _spo_envelope(*, recorded: bool) -> dict:
    """`_spo_last_receipt`: label "SPO Date (recorded)" when `spo_date_source == "recorded"`."""
    return {
        "result_type": "spo_last_receipt",
        "intro": "Here is the last SPO line per product.",
        "items": [
            {
                "title": "SPO-77001",
                "fields": [
                    {"key": "spo_number", "label": "SPO Number", "value": "SPO-77001"},
                    {"key": "container_number", "label": "Container Number", "value": "MSKU1234567"},
                    {"key": "product_code", "label": "Product Code", "value": "BRBC22102W"},
                    {"key": "spo_quantity", "label": "SPO Quantity", "value": "500"},
                    {"key": "gr_quantity", "label": "GR Quantity", "value": "300"},
                    {
                        "key": "spo_date",
                        "label": "SPO Date (recorded)" if recorded else "SPO Date",
                        "value": "2026-08-30",
                    },
                    {"key": "gr_date", "label": "GR Date", "value": "2026-09-20"},
                    {"key": "warehouse", "label": "Warehouse", "value": "BUKIT RAJA"},
                ],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def _orders_envelope(*, flags: dict | None = None) -> dict:
    """`_orders_list`: company_name keyed, every other pair keyed by label text only."""
    return {
        "result_type": "orders",
        "intro": "Here are the orders I found.",
        "items": [
            {
                "title": "SO-26-00981",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"label": "Order Number", "value": "SO-26-00981"},
                    {"label": "Customer", "value": "TAN BROTHERS HARDWARE"},
                    {"label": "Order Date", "value": "2026-09-28"},
                    {"label": "Actual Delivery Date", "value": "2026-10-01"},
                    {"label": "Status", "value": "Partially Delivered"},
                    {"label": "Pickup Time", "value": "09:30"},
                    {"label": "Transporter", "value": "FAST LOGISTICS"},
                    {"label": "Driver", "value": "Total"},  # a value equal to a catalog key: never translated
                    {"label": "Lorry Plate", "value": "PLATE-1"},
                    {"label": "Warehouse", "value": "BRW"},
                    {"label": "Products", "value": "BRBC22102W (12), SRTSWT3001 (4)"},
                ],
                "flags": flags
                or {
                    "discontinued": False,
                    "expired": False,
                    "unallocated": False,
                    "partially_allocated": False,
                },
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def _so_outstanding_envelope() -> dict:
    """`_orders_so_outstanding`: keyed fields, `outstanding_qty` restricted."""
    return {
        "result_type": "so_outstanding",
        "intro": "Here is the outstanding SO I found.",
        "items": [
            {
                "title": "SO-26-00981",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"key": "so_number", "label": "SO Number", "value": "SO-26-00981"},
                    {"key": "product_code", "label": "Product Code", "value": "BRBC22102W"},
                    {"key": "outstanding_qty", "label": "Outstanding Qty", "value": "8"},
                    {"key": "order_date", "label": "Order Date", "value": "2026-09-28"},
                    {"key": "customer", "label": "Customer", "value": "TAN BROTHERS HARDWARE"},
                    {
                        "key": "requested_delivery_date",
                        "label": "Requested Delivery Date",
                        "value": "2026-10-10",
                    },
                ],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
        "restricted_fields": {"outstanding_qty": "sales_orders.outstanding"},
    }


def _orders_by_product_envelope() -> dict:
    return {
        "result_type": "orders",
        "intro": "Here are the orders I found.",
        "items": [
            {
                "title": "SO-26-00981",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"label": "Order Number", "value": "SO-26-00981"},
                    {"label": "Customer", "value": "TAN BROTHERS HARDWARE"},
                    {"label": "Order Date", "value": "2026-09-28"},
                    {"label": "Actual Delivery Date", "value": "2026-10-01"},
                    {"label": "Products", "value": "BRBC22102W (12) @ BRW"},
                ],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def _po_cost_envelope() -> dict:
    """`_po_last_cost`: three money fields restricted to `purchase_orders.cost`, `supplier`
    to `purchase_orders.supplier`."""
    return {
        "result_type": "po_last_cost",
        "intro": "Here is the last purchase cost per product and location.",
        "items": [
            {
                "title": "PO-2026-0412",
                "fields": [
                    {"key": "po_number", "label": "PO Number", "value": "PO-2026-0412"},
                    {"key": "product_code", "label": "Product Code", "value": "BRBC22102W"},
                    {"key": "po_quantity", "label": "PO Quantity", "value": "120"},
                    {"key": "po_date", "label": "PO Date", "value": "2026-09-11"},
                    {"key": "unit_cost", "label": "Cost / unit", "value": "MYR 12.50"},
                    {"key": "discount_per_unit", "label": "Discount / unit", "value": "MYR 0.00"},
                    {
                        "key": "unit_cost_after_discount",
                        "label": "Cost after discount / unit",
                        "value": "MYR 12.50",
                    },
                    {"key": "warehouse", "label": "Warehouse", "value": "BUKIT RAJA"},
                    {"key": "supplier", "label": "Supplier", "value": "ACME CERAMICS SDN BHD"},
                ],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
        "restricted_fields": {
            "unit_cost": "purchase_orders.cost",
            "discount_per_unit": "purchase_orders.cost",
            "unit_cost_after_discount": "purchase_orders.cost",
            "supplier": "purchase_orders.supplier",
        },
    }


def _no_result_envelope(intro: str) -> dict:
    return {
        "result_type": "purchase_orders_placed",
        "intro": intro,
        "items": [],
        "has_result": False,
    }


def _grant(*perms: str) -> dict:
    return {"allowed": True, "attributes": list(perms)}


# --------------------------------------------------------------------------- #
# AC-CL20: the catalog carries every slice-2 literal
# --------------------------------------------------------------------------- #


def test_ac_cl20_slice2_content_is_exactly_the_reviewed_wording():
    for english, (ms, zh) in SLICE2.items():
        assert english in label_catalog.LABELS, english
        assert label_catalog.LABELS[english]["ms"] == ms, english
        assert label_catalog.LABELS[english]["zh"] == zh, english


def test_ac_cl20_every_slice2_entry_keeps_its_tokens_and_survives_defaults():
    for lang in ("ms", "zh"):
        d = label_catalog.defaults(lang)
        for english in SLICE2:
            assert english in d, (lang, english)
            assert label_catalog.tokens_match(english, d[english]), (lang, english)


def test_ac_cl20_field_keys_additions():
    for key, english in SLICE2_FIELD_KEYS.items():
        assert label_catalog.FIELD_KEYS.get(key) == english, key
        assert english in label_catalog.LABELS, english
    # Slice 1 mappings are untouched.
    assert label_catalog.FIELD_KEYS["open_so_qty"] == "Outstanding"
    assert label_catalog.FIELD_KEYS["company_name"] == "Company"


def test_ac_cl20_the_five_tools_are_in_the_localized_tools_set():
    for tool in SLICE2_TOOLS:
        assert tool in fetch._LOCALIZED_TOOLS, tool
    assert "crm_inventory_stock_balance_list" in fetch._LOCALIZED_TOOLS


def test_ac_cl20_tools_outside_slices_1_and_2_are_not_localized():
    for tool in (
        "crm_outstanding_report",
        "crm_top_selling_report",
        "crm_marketing_promotions_list",
        "crm_resource_attachments_list",
    ):
        assert tool not in fetch._LOCALIZED_TOOLS, tool


def test_ac_cl20_default_intros_of_the_five_tools_are_catalogued():
    p = _presenters()
    for tool in SLICE2_TOOLS:
        assert p._DEFAULT_INTRO[tool] in label_catalog.LABELS, tool


def test_ac_cl20_presenter_row_labels_are_catalogued():
    """Run each presenter over a full row: every label it prints, apart from data labels, is
    catalogued, and every keyed field's key is in FIELD_KEYS with that label (or, for the SPO
    date, the recorded variant)."""
    p = _presenters()
    full = {
        "company_name": "SORENTO",
        "order_number": "SO-1",
        "debtor_name": "X",
        "order_date": "2026-09-28",
        "actual_delivery_date": "2026-10-01",
        "order_status": "Open",
        "pickup_time": "09:30",
        "transporter": "T",
        "driver_name": "D",
        "lorry_plate": "W 1",
        "warehouse": "BRW",
        "lines": [{"product_code": "A", "quantity": 1}],
        "matched_products": [{"product_code": "A", "quantity": 1, "warehouse_code": "BRW"}],
        "so_number": "SO-1",
        "product_code": "A",
        "outstanding_qty": 3,
        "customer": "X",
        "requested_delivery_date": "2026-10-10",
        "po_number": "PO-1",
        "ordered_qty": 4,
        "po_date": "2026-09-01",
        "location": "BRW",
        "supplier": "S",
        "spo_number": "SPO-1",
        "container_number": "C1",
        "spo_quantity": 5,
        "gr_quantity": 2,
        "spo_date": "2026-08-01",
        "gr_date": "2026-09-01",
        "po_quantity": 6,
        "currency": "MYR",
        "unit_cost": 1.5,
        "discount_per_unit": 0.0,
        "unit_cost_after_discount": 1.5,
    }
    for fn, recorded in (
        (p._orders_list, False),
        (p._orders_so_outstanding, False),
        (p._purchase_orders_placed, False),
        (p._spo_last_receipt, False),
        (p._spo_last_receipt, True),
        (p._po_last_cost, False),
        (p._orders_by_product, False),
    ):
        row = dict(full, spo_date_source="recorded" if recorded else "")
        b = p._Builder()
        fn([row], b)
        assert b.items, fn.__name__
        for f in b.items[0]["fields"]:
            assert f["label"] in label_catalog.LABELS, (fn.__name__, f["label"])
            if "key" in f and f["key"] in label_catalog.FIELD_KEYS:
                english = label_catalog.FIELD_KEYS[f["key"]]
                assert f["label"] == english or f["label"] == "SPO Date (recorded)", f


def test_ac_cl20_presenter_intro_literals_are_catalogued():
    """The `present_response` intros (so_outstanding, no result, with and without companies)."""
    src = (Path(_presenters().__file__)).read_text()
    for sentence in (
        "Here is the outstanding SO I found.",
        "No matching results found.",
        "Here are the results I found.",
    ):
        assert sentence in src, f"presenter no longer prints {sentence!r}"
        assert sentence in label_catalog.LABELS, sentence
    assert "No matching results found for {companies}." in label_catalog.LABELS


# --------------------------------------------------------------------------- #
# AC-CL21: PO placed, ms
# --------------------------------------------------------------------------- #


def test_ac_cl21_po_placed_ms_labels_and_intro_with_the_grant():
    env = _po_placed_envelope()
    ctx_access = _grant("purchase_orders.supplier")
    text = _assert_values_identical(env, TOOL_PO_PLACED, "ms", access=ctx_access)
    assert "Berikut ialah PO yang telah dibuat." in text
    assert "*No. PO:* PO-2026-0412" in text
    assert "*Kuantiti Dipesan:* 120" in text
    assert "*Kuantiti Tertunggak:* 45" in text
    assert "*Tarikh PO:* 2026-09-11" in text or "*Tarikh PO:* 11/09/2026" in text
    assert "*Lokasi:* BRW-BB" in text
    assert "*Pembekal:* ACME CERAMICS SDN BHD" in text
    assert "*Kod Produk:* BRBC22102W" in text
    assert "*Syarikat:* SORENTO" in text
    for english in ("PO Number", "Ordered Qty", "Supplier", "Location", "Here is the PO placed"):
        assert english not in text, english


def test_ac_cl21_po_placed_ms_supplier_dropped_without_the_grant():
    env = _po_placed_envelope()
    for access in (None, _grant(), _grant("purchase_orders.cost")):
        extra = {} if access is None else {"access": access}
        text = _render(env, TOOL_PO_PLACED, "ms", **extra)
        assert "*Pembekal:*" not in text
        assert "ACME CERAMICS" not in text
        assert "*No. PO:* PO-2026-0412" in text
        # the English render drops it the same way
        en = _render(env, TOOL_PO_PLACED, None, **extra)
        assert "ACME CERAMICS" not in en
        assert _values(text) == _values(en)


def test_ac_cl21_po_placed_zh_labels():
    env = _po_placed_envelope()
    text = _assert_values_identical(env, TOOL_PO_PLACED, "zh", access=_grant("purchase_orders.supplier"))
    assert "以下是已下的 PO。" in text
    assert "*PO 编号:* PO-2026-0412" in text
    assert "*订购数量:* 120" in text
    assert "*供应商:* ACME CERAMICS SDN BHD" in text


def test_ac_cl21_po_placed_en_localizer_and_absent_localizer_are_byte_identical():
    env = _po_placed_envelope()
    bare = _render(env, TOOL_PO_PLACED, None)
    ident = fetch.output_structurer(copy.deepcopy(env), _ctx(TOOL_PO_PLACED, localizer=IDENTITY))["response"]
    assert bare == ident
    assert "*PO Number:* PO-2026-0412" in bare
    assert "Here is the PO placed I found." in bare


def test_ac_cl21_po_placed_footer_is_translated_and_state_stays_english():
    env = _po_placed_envelope()
    out = fetch.output_structurer(copy.deepcopy(env), _ctx(TOOL_PO_PLACED, "ms"))
    assert "_Data dikemas kini: 02/10/2026 09:15:30_" in out["response"]
    labels = [f["label"] for f in out["answers"][0]["fields"]]
    assert "PO Number" in labels and "Ordered Qty" in labels
    assert "No. PO" not in labels


# --------------------------------------------------------------------------- #
# AC-CL22: SPO last receipt, zh
# --------------------------------------------------------------------------- #


def test_ac_cl22_spo_recorded_date_zh():
    env = _spo_envelope(recorded=True)
    text = _assert_values_identical(env, TOOL_SPO, "zh")
    assert "以下是每个产品最近的 SPO 记录。" in text
    assert "*SPO 编号:* SPO-77001" in text
    assert "*货柜号:* MSKU1234567" in text
    assert "*SPO 日期（已记录）:*" in text
    assert "*SPO 数量:* 500" in text
    assert "*GR 数量:* 300" in text
    assert "*GR 日期:*" in text
    assert "*仓库:* BUKIT RAJA" in text
    assert "SPO Date" not in text
    assert "Container Number" not in text


def test_ac_cl22_spo_plain_date_zh_uses_the_plain_label():
    env = _spo_envelope(recorded=False)
    text = _assert_values_identical(env, TOOL_SPO, "zh")
    assert "*SPO 日期:*" in text
    assert "已记录" not in text


def test_ac_cl22_spo_recorded_date_ms():
    env = _spo_envelope(recorded=True)
    text = _assert_values_identical(env, TOOL_SPO, "ms")
    assert "Berikut ialah baris SPO terakhir bagi setiap produk." in text
    assert "*Tarikh SPO (direkodkan):*" in text
    assert "*No. Kontena:* MSKU1234567" in text
    assert "*Kuantiti GR:* 300" in text
    assert "*Tarikh GR:*" in text


def test_ac_cl22_a_label_that_differs_from_the_key_english_falls_back_to_label_text():
    """Both SPO Date labels share key `spo_date` (FIELD_KEYS carries "SPO Date" only); the
    by-label match covers the recorded variant."""
    loc = _loc("zh")
    assert loc.label({"key": "spo_date", "label": "SPO Date", "value": "x"}) == "SPO 日期"
    assert loc.label({"key": "spo_date", "label": "SPO Date (recorded)", "value": "x"}) == "SPO 日期（已记录）"


# --------------------------------------------------------------------------- #
# AC-CL23: orders list, ms
# --------------------------------------------------------------------------- #


def test_ac_cl23_orders_list_ms_labels_and_value_untouched_status():
    env = _orders_envelope()
    text = _assert_values_identical(env, TOOL_ORDERS, "ms")
    assert "Berikut ialah pesanan yang saya temui." in text
    assert "*No. Pesanan:* SO-26-00981" in text
    assert "*Pelanggan:* TAN BROTHERS HARDWARE" in text
    assert "*Status:* Partially Delivered" in text
    assert "*Tarikh Pesanan:*" in text
    assert "*Tarikh Penghantaran Sebenar:*" in text
    assert "*Masa Pengambilan:* 09:30" in text
    assert "*Pengangkut:* FAST LOGISTICS" in text
    assert "*Pemandu:* Total" in text
    assert "*No. Plat Lori:* PLATE-1" in text
    assert "*Gudang:* BRW" in text
    assert "*Produk:* BRBC22102W (12), SRTSWT3001 (4)" in text
    for english in ("Order Number", "Customer", "Transporter", "Lorry Plate", "Products"):
        assert english not in text, english


def test_ac_cl23_outstanding_intro_when_order_status_is_outstanding():
    env = _orders_envelope()
    text = _render(
        env, TOOL_ORDERS, "ms", semantic_input={"order_status": "outstanding"}
    )
    assert "Berikut ialah pesanan tertunggak yang saya temui." in text
    assert "Here are the outstanding orders" not in text
    zh = _render(env, TOOL_ORDERS, "zh", semantic_input={"order_status": "outstanding"})
    assert "以下是我找到的未交货订单。" in zh


def test_ac_cl23_delivered_intro_when_order_status_is_delivered():
    env = _orders_envelope()
    text = _render(env, TOOL_ORDERS, "ms", semantic_input={"order_status": "delivered"})
    assert "Berikut ialah pesanan yang telah dihantar." in text
    zh = _render(env, TOOL_ORDERS, "zh", semantic_input={"order_status": "delivered"})
    assert "以下是我找到的已送货订单。" in zh


def test_ac_cl23_outstanding_intro_english_without_a_localizer_is_unchanged():
    text = _render(
        _orders_envelope(), TOOL_ORDERS, None, semantic_input={"order_status": "outstanding"}
    )
    assert "Here are the outstanding orders I found." in text


def test_ac_cl23_flag_lines_expired_ms():
    env = _orders_envelope(flags={"expired": True})
    text = _render(env, TOOL_ORDERS, "ms")
    assert "TAMAT TEMPOH" in text
    assert "EXPIRED" not in text


def test_ac_cl23_flag_lines_pending_allocation_ms_and_zh():
    env = _orders_envelope(flags={"unallocated": True})
    ms = _render(env, TOOL_ORDERS, "ms")
    zh = _render(env, TOOL_ORDERS, "zh")
    assert "MENUNGGU PERUNTUKAN" in ms and "PENDING ALLOCATION" not in ms
    assert "待分配" in zh and "PENDING ALLOCATION" not in zh


def test_ac_cl23_flag_lines_partial_allocation_ms_and_zh():
    env = _orders_envelope(flags={"partially_allocated": True})
    ms = _render(env, TOOL_ORDERS, "ms")
    zh = _render(env, TOOL_ORDERS, "zh")
    assert "PERUNTUKAN SEBAHAGIAN" in ms and "PARTIAL ALLOCATION" not in ms
    assert "部分分配" in zh and "PARTIAL ALLOCATION" not in zh


def test_ac_cl23_flag_lines_expired_zh_keep_their_markers():
    env = _orders_envelope(flags={"expired": True})
    text = _render(env, TOOL_ORDERS, "zh")
    assert "⚠️  *(已过期)*" in text


def test_ac_cl23_flags_stay_english_without_a_localizer():
    text = _render(_orders_envelope(flags={"expired": True}), TOOL_ORDERS, None)
    assert "⚠️  *(EXPIRED)*" in text


def test_ac_cl23_so_outstanding_ms_with_the_grant():
    env = _so_outstanding_envelope()
    text = _assert_values_identical(
        env, TOOL_ORDERS, "ms", access=_grant("sales_orders.outstanding")
    )
    assert "Berikut ialah SO tertunggak yang saya temui." in text
    assert "*No. SO:* SO-26-00981" in text
    assert "*Kuantiti Tertunggak:* 8" in text
    assert "*Pelanggan:* TAN BROTHERS HARDWARE" in text
    assert "*Tarikh Penghantaran Diminta:*" in text


def test_ac_cl23_so_outstanding_qty_dropped_without_the_grant_ms():
    env = _so_outstanding_envelope()
    text = _render(env, TOOL_ORDERS, "ms")
    assert "Kuantiti Tertunggak" not in text
    assert "Outstanding Qty" not in text
    assert "*No. SO:* SO-26-00981" in text


def test_ac_cl23_orders_by_product_zh():
    env = _orders_by_product_envelope()
    text = _assert_values_identical(env, TOOL_ORDERS_BY_PRODUCT, "zh")
    assert "以下是我找到的订单。" in text
    assert "*订单号:* SO-26-00981" in text
    assert "*客户:* TAN BROTHERS HARDWARE" in text
    assert "*订单日期:*" in text
    assert "*实际送货日期:*" in text
    assert "*产品:* BRBC22102W (12) @ BRW" in text


def test_ac_cl23_orders_by_product_outstanding_intro_ms():
    text = _render(
        _orders_by_product_envelope(),
        TOOL_ORDERS_BY_PRODUCT,
        "ms",
        semantic_input={"order_status": "outstanding"},
    )
    assert "Berikut ialah pesanan tertunggak yang saya temui." in text


# --------------------------------------------------------------------------- #
# AC-CL24: PO last cost, zh, restricted cost fields
# --------------------------------------------------------------------------- #


def test_ac_cl24_po_cost_zh_with_cost_and_supplier_grants():
    env = _po_cost_envelope()
    text = _assert_values_identical(
        env,
        TOOL_PO_COST,
        "zh",
        access=_grant("purchase_orders.cost", "purchase_orders.supplier"),
    )
    assert "以下是每个产品和位置最近一次的采购成本。" in text
    assert "*单位成本:* MYR 12.50" in text
    assert "*单位折扣:* MYR 0.00" in text
    assert "*折后单位成本:* MYR 12.50" in text
    assert "*PO 数量:* 120" in text
    assert "*PO 编号:* PO-2026-0412" in text
    assert "*供应商:* ACME CERAMICS SDN BHD" in text
    assert "Cost / unit" not in text


def test_ac_cl24_po_cost_zh_money_fields_dropped_without_the_cost_grant():
    env = _po_cost_envelope()
    text = _render(env, TOOL_PO_COST, "zh", access=_grant("purchase_orders.supplier"))
    assert "单位成本" not in text and "单位折扣" not in text and "折后单位成本" not in text
    assert "MYR" not in text
    assert "*供应商:* ACME CERAMICS SDN BHD" in text
    assert "*PO 编号:* PO-2026-0412" in text


def test_ac_cl24_po_cost_zh_nothing_restricted_without_any_grant():
    text = _render(_po_cost_envelope(), TOOL_PO_COST, "zh")
    for gone in ("单位成本", "单位折扣", "折后单位成本", "供应商", "MYR", "ACME"):
        assert gone not in text, gone
    assert "*PO 编号:* PO-2026-0412" in text
    assert "*产品代码:* BRBC22102W" in text


def test_ac_cl24_po_cost_zh_supplier_alone_is_independent_of_the_cost_grant():
    text = _render(_po_cost_envelope(), TOOL_PO_COST, "zh", access=_grant("purchase_orders.cost"))
    assert "*单位成本:* MYR 12.50" in text
    assert "供应商" not in text and "ACME" not in text


def test_ac_cl24_po_cost_ms_labels():
    text = _assert_values_identical(
        _po_cost_envelope(),
        TOOL_PO_COST,
        "ms",
        # Both grants: the value-identity check needs every field rendered, supplier included.
        access=_grant("purchase_orders.cost", "purchase_orders.supplier"),
    )
    assert "*Kos / unit:* MYR 12.50" in text
    assert "*Diskaun / unit:* MYR 0.00" in text
    assert "*Kos selepas diskaun / unit:* MYR 12.50" in text
    assert "*Kuantiti PO:* 120" in text
    assert "Berikut ialah kos belian terakhir bagi setiap produk dan lokasi." in text


# --------------------------------------------------------------------------- #
# AC-CL25: "No matching results found for {companies}."
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", SLICE2_TOOLS)
def test_ac_cl25_no_results_with_companies_ms(tool):
    env = _no_result_envelope("No matching results found for Sorento and Mocha.")
    text = _render(env, tool, "ms")
    assert "Tiada hasil yang sepadan ditemui untuk Sorento and Mocha." in text
    assert "No matching results" not in text


def test_ac_cl25_no_results_with_companies_zh_reinserts_the_names_verbatim():
    env = _no_result_envelope("No matching results found for Sorento & Mocha (M) Sdn Bhd.")
    text = _render(env, TOOL_PO_PLACED, "zh")
    assert "在 Sorento & Mocha (M) Sdn Bhd 中未找到匹配的结果。" in text


def test_ac_cl25_no_results_plain_ms_and_zh():
    env = _no_result_envelope("No matching results found.")
    assert "Tiada hasil yang sepadan ditemui." in _render(env, TOOL_ORDERS, "ms")
    assert "未找到匹配的结果。" in _render(env, TOOL_ORDERS, "zh")


def test_ac_cl25_localizer_text_templates_directly():
    loc = _loc("ms")
    assert (
        loc.text("No matching results found for ACME, BETA or GAMMA.")
        == "Tiada hasil yang sepadan ditemui untuk ACME, BETA or GAMMA."
    )
    assert loc.text("No matching results found.") == "Tiada hasil yang sepadan ditemui."


def test_ac_cl25_no_results_english_without_localizer_is_unchanged():
    env = _no_result_envelope("No matching results found for Sorento and Mocha.")
    assert "No matching results found for Sorento and Mocha." in _render(env, TOOL_PO_PLACED, None)


def test_ac_cl25_the_generic_fallback_intro_is_translated():
    env = _po_placed_envelope(intro="Here are the results I found.")
    text = _render(env, TOOL_PO_PLACED, "ms")
    assert "Berikut ialah hasil yang saya temui." in text


# --------------------------------------------------------------------------- #
# AC-CL26: tools outside slices 1-2 render exactly as with no localizer
# --------------------------------------------------------------------------- #


def _promotions_envelope() -> dict:
    return {
        "result_type": "promotions",
        "intro": "Here are the matching promotions.",
        "items": [
            {
                "title": "Raya Sale",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"label": "Promotion Name", "value": "Raya Sale"},
                    {"label": "Status", "value": "Active"},
                    {"label": "Warehouse", "value": "BRW"},
                ],
                "flags": {"expired": True},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def test_ac_cl26_promotions_with_an_ms_localizer_renders_as_with_none():
    env = _promotions_envelope()
    tool = "crm_marketing_promotions_list"
    bare = fetch.output_structurer(copy.deepcopy(env), _ctx(tool))
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx(tool, "ms"))
    assert ms["response"] == bare["response"]
    assert "*Company:* SORENTO" in ms["response"]
    assert "EXPIRED" in ms["response"]
    assert "Data last updated" in ms["response"]
    assert "Syarikat" not in ms["response"] and "TAMAT" not in ms["response"]


def test_ac_cl26_attachments_with_an_ms_localizer_renders_as_with_none():
    env = {
        "result_type": "attachments",
        "intro": "Here are the documents I found.",
        "items": [
            {
                "title": "Price List",
                "fields": [{"label": "Warehouse", "value": "BRW"}],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }
    tool = "crm_resource_attachments_list"
    bare = fetch.output_structurer(copy.deepcopy(env), _ctx(tool))["response"]
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx(tool, "ms"))["response"]
    assert ms == bare
    assert "Here are the documents I found." in ms
    assert "Gudang" not in ms


def test_ac_cl26_low_stock_report_with_an_ms_localizer_renders_as_with_none():
    """Slice 3 localizes the outstanding report on purpose; the low stock report is still English."""
    text = "Product: BRBC22102W\nOutstanding SO: 12\nTotal: 51"
    env = {"response": text, "has_result": True, "result_type": "low_stock_report"}
    tool = "crm_low_stock_report"
    bare = fetch.output_structurer(copy.deepcopy(env), _ctx(tool))
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx(tool, "ms"))
    assert ms["response"] == bare["response"]
    assert "Outstanding SO: 12" in ms["response"]
    assert "Jumlah" not in ms["response"] and "Tertunggak" not in ms["response"]


def test_ac_cl26_sales_analysis_with_an_ms_localizer_renders_as_with_none():
    """Slice 3 localizes top selling on purpose; the sales analysis text is still English."""
    text = "Top selling products\n1. BRBC22102W - 120 units\n2. SRTSWT3001 - 80 units"
    env = {
        "response": text,
        "has_result": True,
        "result_type": "sales_analysis",
    }
    tool = "crm_sales_analysis"
    bare = fetch.output_structurer(copy.deepcopy(env), _ctx(tool))
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx(tool, "ms"))
    assert ms["response"] == bare["response"]
    assert "Top selling products" in ms["response"]


def test_ac_cl26_incoming_stock_with_an_ms_localizer_renders_in_malay():
    """Incoming joined the localized tools in the crew-tester round (3 Oct, AC-CLI2)."""
    env = {
        "result_type": "incoming_stock",
        "intro": "Here is the incoming stock I found.",
        "items": [
            {
                "title": "C1",
                "fields": [{"key": "product_code", "label": "Product Code", "value": "A1"}],
                "flags": {},
            }
        ],
        "has_result": True,
    }
    tool = "crm_incoming_stock_list"
    bare = fetch.output_structurer(copy.deepcopy(env), _ctx(tool))["response"]
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx(tool, "ms"))["response"]
    assert ms == bare.replace("Here is the incoming stock I found.", "Berikut ialah stok masuk yang ditemui.").replace(
        "*Product Code:*", "*Kod Produk:*"
    )
    assert "Kod Produk" in ms and "A1" in ms


# --------------------------------------------------------------------------- #
# Slice 1 behaviour is untouched by the five new tools
# --------------------------------------------------------------------------- #


def test_the_input_envelope_is_not_mutated_by_a_translated_slice2_render():
    env = _po_placed_envelope()
    before = copy.deepcopy(env)
    fetch.output_structurer(env, _ctx(TOOL_PO_PLACED, "ms", access=_grant("purchase_orders.supplier")))
    # the restricted pass may pop `granted_value`; nothing here carries one, so equal
    assert env == before
