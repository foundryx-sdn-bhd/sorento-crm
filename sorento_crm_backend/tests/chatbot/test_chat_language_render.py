"""CHAT-LANGUAGE slice 1 (stock), AC-CL07..AC-CL11: the render seams.

`output_structurer(envelope, ctx)` reads `ctx["localizer"]` (absent = identity) and `compose`
reads `getattr(ctx, "localizer", None)`. Envelopes are built by hand in the shape the MCP
presenter emits (`sorento_crm_mcp/presenters.py::_stock`, `_stock_compact`,
`_stock_availability`, `present_response`), the way `test_restricted_fields.py` and
`test_stock_ask_availability_rendered_reply.py` build theirs.

`app.services.chatbot.label_catalog` does not exist yet, so every test is red on import.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import IDENTITY, Localizer
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

TS = "2026-10-02T09:15:30"


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _ctx(lang: str | None = None, **extra) -> dict:
    ctx: dict = {"semantic_input": {}, "tool": "crm_inventory_stock_balance_list", **extra}
    if lang is not None:
        ctx["localizer"] = _loc(lang)
    return ctx


# --------------------------------------------------------------------------- #
# Envelopes, in the presenter's shape
# --------------------------------------------------------------------------- #


def _compact_envelope() -> dict:
    """`_stock_compact` without `include_sellable`: keyed Product Code, UNKEYED Total, and
    location pairs whose label is data (the location code, no key)."""
    return {
        "result_type": "stock",
        "intro": "Stock summary for the requested products.",
        "items": [
            {
                "title": "SRTSWT3001",
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTSWT3001"},
                    {"label": "Total", "value": 51},
                    {"label": "BRW-BB", "value": 30},
                    {"label": "PKL-01", "value": 21},
                ],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def _detailed_envelope(*, discontinued: bool = False) -> dict:
    """`_stock`: the staff row, every field keyed."""
    return {
        "result_type": "stock",
        "intro": "Stock details found for the requested products.",
        "items": [
            {
                "title": "BRBC22102W",
                "fields": [
                    {"key": "company_name", "label": "Company", "value": "SORENTO"},
                    {"key": "product_code", "label": "Product Code", "value": "BRBC22102W"},
                    {"key": "product_name", "label": "Product Name", "value": 'WALL BASIN 22"'},
                    {"key": "warehouse", "label": "Warehouse", "value": "BUKIT RAJA"},
                    {"key": "system_location", "label": "System Location", "value": "BRW-BB"},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 40},
                ],
                "flags": {"discontinued": discontinued},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }


def _availability_entry(code: str, qty: int, branch: str, tail: str) -> dict:
    return {
        "title": f"{code} x {qty}: {tail}",
        "fields": [],
        "flags": {"needs_quantity": False, "branch": branch},
    }


def _availability_envelope(items: list, *, intro: str = "") -> dict:
    return {
        "result_type": "stock_availability",
        "intro": intro,
        "items": items,
        "has_result": True,
        "last_updated_at": TS,
    }


IN_STOCK = "yes, we have stock. Please refer to your salesman."
INCOMING = "no stock at the moment, ETA 15/10/2026."
NO_INCOMING = "no stock and no incoming at the moment. Please refer to your salesman."
TOO_BIG = "the quantity is more than what I can confirm here. Please refer to your salesman."


# --------------------------------------------------------------------------- #
# AC-CL07: no localizer is byte-identical to today (and to IDENTITY)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        _compact_envelope,
        _detailed_envelope,
        lambda: _detailed_envelope(discontinued=True),
        lambda: _availability_envelope(
            [
                _availability_entry("SRTSWT3001", 10, "in_stock", IN_STOCK),
                _availability_entry("SRTSWT3002", 4, "incoming", INCOMING),
            ]
        ),
        lambda: _availability_envelope(
            [{"title": "SRTSWT3001", "fields": [], "flags": {"needs_quantity": True}}],
            intro="How many units do you need?",
        ),
    ],
)
def test_ac_cl07_absent_localizer_equals_identity_and_stays_english(build):
    bare = fetch.output_structurer(build(), _ctx())
    explicit = fetch.output_structurer(build(), _ctx(localizer=IDENTITY))
    assert bare == explicit
    assert "Ringkasan" not in bare["response"] and "库存" not in bare["response"]


def test_ac_cl07_english_localizer_resolved_for_en_changes_nothing():
    base = fetch.output_structurer(_compact_envelope(), _ctx())
    again = fetch.output_structurer(_compact_envelope(), _ctx(localizer=Localizer("en", {})))
    assert base == again
    assert "*Product Code:* SRTSWT3001" in base["response"]
    assert "*Total:* 51" in base["response"]
    assert "_Data last updated: 02/10/2026 09:15:30_" in base["response"]


# --------------------------------------------------------------------------- #
# AC-CL08: compact stock, ms and zh
# --------------------------------------------------------------------------- #


def test_ac_cl08_compact_ms():
    out = fetch.output_structurer(_compact_envelope(), _ctx("ms"))
    text = out["response"]
    assert "Ringkasan stok untuk produk yang diminta." in text
    assert "*Kod Produk:* SRTSWT3001" in text
    assert "*Jumlah:* 51" in text
    assert "*BRW-BB:* 30" in text
    assert "*PKL-01:* 21" in text
    assert "_Data dikemas kini: " in text
    assert "Product Code" not in text
    assert "Total:" not in text
    assert "Data last updated" not in text
    assert "Stock summary" not in text


def test_ac_cl08_compact_zh():
    out = fetch.output_structurer(_compact_envelope(), _ctx("zh"))
    text = out["response"]
    assert "所请求产品的库存摘要。" in text
    assert "*产品代码:* SRTSWT3001" in text
    assert "*总数:* 51" in text
    assert "*BRW-BB:* 30" in text
    assert "_数据更新时间: " in text
    assert "Product Code" not in text
    assert "Total:" not in text
    assert "Data last updated" not in text


def test_ac_cl08_the_footer_value_is_the_formatted_timestamp_untouched():
    text = fetch.output_structurer(_compact_envelope(), _ctx("ms"))["response"]
    assert "_Data dikemas kini: 02/10/2026 09:15:30_" in text


def test_ac_cl08_a_missing_intro_prints_the_translated_fallback():
    env = _compact_envelope()
    env.pop("intro")
    ms = fetch.output_structurer(env, _ctx("ms"))["response"]
    assert ms.startswith("Berikut ialah hasilnya.")


def test_ac_cl08_the_state_keeps_english_labels_and_the_input_is_not_translated_in_place():
    """`answers` is `e.get("items")` and feeds positional picks and compose's figures
    (`_codes_without_rows` matches the English "Product Code"). Only the rendered text
    changes, never the rows the state carries (plan Risks)."""
    out = fetch.output_structurer(_compact_envelope(), _ctx("ms"))
    labels = [f["label"] for f in out["answers"][0]["fields"]]
    assert labels == ["Product Code", "Total", "BRW-BB", "PKL-01"]


def test_ac_cl08_summary_item_labels_are_translated():
    env = _compact_envelope()
    env["summary_items"] = [
        {"title": "Totals", "fields": [{"key": "total_on_hand", "label": "Total", "value": 51}]}
    ]
    ms = fetch.output_structurer(env, _ctx("ms"))["response"]
    assert "*Jumlah:* 51" in ms
    assert "*Total:*" not in ms


# --------------------------------------------------------------------------- #
# AC-CL09: availability, answered, zh and ms
# --------------------------------------------------------------------------- #


def test_ac_cl09_answered_in_stock_zh():
    env = _availability_envelope([_availability_entry("SRTSWT3001", 10, "in_stock", IN_STOCK)])
    out = fetch.output_structurer(env, _ctx("zh"))
    assert out["response"] == "SRTSWT3001 x 10: 有库存。请联系您的销售员。"


def test_ac_cl09_incoming_zh_keeps_the_eta_date():
    env = _availability_envelope([_availability_entry("SRTSWT3001", 10, "incoming", INCOMING)])
    out = fetch.output_structurer(env, _ctx("zh"))
    assert out["response"] == "SRTSWT3001 x 10: 目前没有库存，ETA 15/10/2026。"


def test_ac_cl09_every_verdict_in_ms_one_line_each():
    env = _availability_envelope(
        [
            _availability_entry("A1", 1, "in_stock", IN_STOCK),
            _availability_entry("B2", 2, "no_incoming", NO_INCOMING),
            _availability_entry("C3", 3, "too_big", TOO_BIG),
            _availability_entry("D4", 4, "incoming", INCOMING),
        ]
    )
    out = fetch.output_structurer(env, _ctx("ms"))
    assert out["response"] == (
        "A1 x 1: ya, stok ada. Sila rujuk jurujual anda.\n\n"
        "B2 x 2: tiada stok dan tiada barang masuk buat masa ini. Sila rujuk jurujual anda.\n\n"
        "C3 x 3: kuantiti ini melebihi apa yang boleh saya sahkan di sini. Sila rujuk jurujual anda.\n\n"
        "D4 x 4: tiada stok buat masa ini, ETA 15/10/2026."
    )


def test_ac_cl09_the_quantity_ask_is_translated_and_prints_no_footer():
    env = _availability_envelope(
        [{"title": "SRTSWT3001", "fields": [], "flags": {"needs_quantity": True}}],
        intro="How many units do you need?",
    )
    ms = fetch.output_structurer(copy.deepcopy(env), _ctx("ms"))["response"]
    zh = fetch.output_structurer(copy.deepcopy(env), _ctx("zh"))["response"]
    assert "Berapa unit yang anda perlukan?" in ms
    assert "您需要多少件？" in zh
    assert "How many units" not in ms + zh
    assert "Data dikemas" not in ms and "数据更新" not in zh


def test_ac_cl09_the_code_and_quantity_prefix_is_never_translated():
    env = _availability_envelope([_availability_entry("SRTSWT3001-GM", 250, "too_big", TOO_BIG)])
    text = fetch.output_structurer(env, _ctx("zh"))["response"]
    assert text.startswith("SRTSWT3001-GM x 250: ")


# --------------------------------------------------------------------------- #
# AC-CL10: detailed staff row, flags
# --------------------------------------------------------------------------- #


def test_ac_cl10_detailed_staff_row_zh():
    text = fetch.output_structurer(_detailed_envelope(), _ctx("zh"))["response"]
    assert "所请求产品的库存详情。" in text
    assert "*公司:* SORENTO" in text
    assert "*产品代码:* BRBC22102W" in text
    assert '*产品名称:* WALL BASIN 22"' in text
    assert "*仓库:* BUKIT RAJA" in text
    assert "*系统位置:* BRW-BB" in text
    assert "*现有数量:* 40" in text
    for english in ("Company", "Product Code", "Warehouse", "System Location", "Quantity On Hand"):
        assert english not in text


def test_ac_cl10_discontinued_flag_line_zh():
    text = fetch.output_structurer(_detailed_envelope(discontinued=True), _ctx("zh"))["response"]
    assert "产品已停产" in text
    assert "PRODUCT DISCONTINUED" not in text


def test_ac_cl10_discontinued_flag_line_ms():
    text = fetch.output_structurer(_detailed_envelope(discontinued=True), _ctx("ms"))["response"]
    assert "PRODUK DIHENTIKAN" in text
    assert "PRODUCT DISCONTINUED" not in text


def test_ac_cl10_a_restricted_keyed_open_so_label_is_translated_when_granted():
    env = _compact_envelope()
    env["items"][0]["fields"][1] = {
        "key": "total_on_hand",
        "label": "Total",
        "value": 51,
        "granted_value": "51 (O/S: 36)",
    }
    env["restricted_fields"] = {"total_on_hand": "inventory.sellable"}
    ctx = _ctx("ms", access={"allowed": True, "attributes": ["inventory.sellable"]})
    text = fetch.output_structurer(env, ctx)["response"]
    assert "*Jumlah:* 51 (O/S: 36)" in text


def test_ac_cl10_an_uncatalogued_label_stays_english_in_a_translated_reply():
    env = _detailed_envelope()
    env["items"][0]["fields"].append({"key": "finish", "label": "Finish", "value": "SRT"})
    text = fetch.output_structurer(env, _ctx("zh"))["response"]
    assert "*Finish:* SRT" in text


# --------------------------------------------------------------------------- #
# AC-CL11: compose's own sentence
# --------------------------------------------------------------------------- #


def _policy() -> Policy:
    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    return Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state() -> State:
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)


def _fig(code: str, qty: int) -> dict:
    return {
        "fields": [
            {"label": "Product Code", "value": code},
            {"label": "Warehouse", "value": "BUKIT RAJA"},
            {"label": "Quantity On Hand", "value": qty},
        ]
    }


def _env(lane_text: str) -> dict:
    return {
        "domain": "inventory",
        "denied": False,
        "entities": ["SRTSWT3001", "SRTSWT3001-GM"],
        "product_codes": ["SRTSWT3001", "SRTSWT3001-GM"],
        "figures": [_fig("SRTSWT3001-GM", 12)],
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": lane_text,
    }


def _compose(env: dict, localizer) -> str:
    ctx = SimpleNamespace(localizer=localizer)
    return compose([env], _state(), _policy(), ctx=ctx).text


ROW_MS = "*Kod Produk:* SRTSWT3001-GM\n*Gudang:* BUKIT RAJA\n*Kuantiti Ada:* 12"


def test_ac_cl11_no_stock_found_ms():
    text = _compose(_env(ROW_MS), _loc("ms"))
    assert "Tiada stok ditemui untuk SRTSWT3001." in text
    assert "No stock found" not in text


def test_ac_cl11_no_stock_found_zh():
    text = _compose(_env("*产品代码:* SRTSWT3001-GM"), _loc("zh"))
    assert "未找到 SRTSWT3001 的库存。" in text


def test_ac_cl11_ms_names_the_suffixed_code_when_it_is_the_one_missing():
    env = _env(ROW_MS)
    env["figures"] = [_fig("SRTSWT3001", 5)]
    text = _compose(env, _loc("ms"))
    assert "Tiada stok ditemui untuk SRTSWT3001-GM." in text


def test_ac_cl11_figures_are_not_mutated():
    env = _env(ROW_MS)
    before = copy.deepcopy(env["figures"])
    _compose(env, _loc("ms"))
    assert env["figures"] == before
    # `_codes_without_rows` still matches the English label on those figures.
    from app.services.chatbot.turn.compose import _codes_without_rows

    assert _codes_without_rows(["SRTSWT3001", "SRTSWT3001-GM"], env["figures"]) == ["SRTSWT3001"]


def test_ac_cl11_no_localizer_attribute_stays_english():
    ctx = SimpleNamespace()
    text = compose([_env("*Product Code:* SRTSWT3001-GM")], _state(), _policy(), ctx=ctx).text
    assert "No stock found for SRTSWT3001." in text
    text_none = compose([_env("*Product Code:* SRTSWT3001-GM")], _state(), _policy(), ctx=None).text
    assert text_none == text


def test_ac_cl11_the_translated_line_sits_above_the_translated_footer():
    """`compose` splits the lane text on the English `"\\n_Data last updated"` to keep the
    line above the footer; with a localized footer that split must still find it."""
    lane = ROW_MS + "\n\n_Data dikemas kini: 02/10/2026 09:15:30_"
    text = _compose(_env(lane), _loc("ms"))
    assert text.index("Tiada stok ditemui untuk SRTSWT3001.") < text.index("_Data dikemas kini")


def test_output_structurer_does_not_mutate_the_figure_inputs_it_is_handed():
    env = _compact_envelope()
    before = copy.deepcopy(env)
    fetch.output_structurer(env, _ctx("ms"))
    assert env == before
