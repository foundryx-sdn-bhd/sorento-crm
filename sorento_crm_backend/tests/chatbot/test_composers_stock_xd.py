"""CHAT-COMPOSER S1 change (c): the stock rows under an ETA miss use the numbered stock
block (AC-1.5, AC-1.6, AC-1.7). Pure, no database.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services.chatbot.label_catalog import Localizer, defaults

NUMBERED = "1. *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n*BRW:* 60 (O/S: 0)"
LEAD = "But here are the stock details for the requested products:"


def _stock_item(code="SRT5734-WH", total="60 (O/S: 0)", brw="60 (O/S: 0)", flags=None):
    return {
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "total", "label": "Total", "value": total},
            {"key": "brw", "label": "BRW", "value": brw},
        ],
        "flags": flags or {},
    }


def _zs(origin, *codes):
    return {
        "active": True,
        "origin_domain": origin,
        "team": "warehouse",
        "missing": [{"code": c, "_n": c, "uuid": f"u-{c}"} for c in codes],
    }


def _render(items, zs):
    from app.services.chatbot.lanes.business.answer import crossdomain_render

    return crossdomain_render({"items": items, "has_result": True}, zeroset=zs, validator={})["_xdBlock"]


def _set_gate(monkeypatch, on):
    """Gate on needs the new Settings field (red until S0). Gate off is the default, so a
    Settings without the field is already off and the call is a no-op."""
    if on:
        monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)
    else:
        if hasattr(settings, "chat_composer_enabled"):
            monkeypatch.setattr(settings, "chat_composer_enabled", False, raising=False)


def _gate(monkeypatch, on):
    _set_gate(monkeypatch, on)


def test_eta_miss_stock_block_numbered_gate_on(monkeypatch):
    _gate(monkeypatch, True)
    block = _render([_stock_item()], _zs("incoming", "SRT5734-WH"))["block"]
    assert NUMBERED in block
    assert not [ln for ln in block.splitlines() if ln.startswith("- *")]
    assert LEAD in block


def test_eta_miss_stock_block_dash_form_gate_off(monkeypatch):
    _gate(monkeypatch, False)
    block = _render([_stock_item()], _zs("incoming", "SRT5734-WH"))["block"]
    assert block == (
        f"No incoming for SRT5734-WH.\n\n{LEAD}\n\n"
        "- *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n*BRW:* 60 (O/S: 0)"
    )


def test_two_rows_number_one_and_two_gate_on(monkeypatch):
    _gate(monkeypatch, True)
    items = [_stock_item(), _stock_item("SRT5735-WH", 7, 7)]
    block = _render(items, _zs("incoming", "SRT5734-WH", "SRT5735-WH"))["block"]
    assert "\n\n1. *Product Code:* SRT5734-WH" in block
    assert "\n\n2. *Product Code:* SRT5735-WH\n*Total:* 7\n*BRW:* 7" in block


def test_xd_block_equals_main_path_block(monkeypatch):
    from app.services.chatbot.composers import stock
    from app.services.chatbot.lanes.business.fetch import output_structurer

    item = _stock_item(flags={"discontinued": True, "alternative_lines": ["X x 1: no"]})
    env = {"items": [item], "answers": [{"text": "x"}], "has_result": True}
    main = output_structurer(env, {"tool": "crm_inventory_stock_balance_list", "semantic_input": {}})["response"]
    main_row = main.split("\n\n", 1)[1]
    assert stock.block(1, item) == main_row
    _gate(monkeypatch, True)
    xd = _render([item], _zs("incoming", "SRT5734-WH"))["block"]
    assert main_row.split("\n⚠")[0] in xd


def test_block_numbers_from_n():
    from app.services.chatbot.composers import stock

    assert stock.block(3, _stock_item()) == NUMBERED.replace("1. ", "3. ", 1)


@pytest.mark.parametrize("gate", [False])
def test_stock_miss_incoming_rows_unchanged(monkeypatch, gate):
    """Origin inventory (incoming rows under a stock miss), gate off. S2 numbers the gate-on rows
    (test_composers_s2_incoming.py)."""
    _gate(monkeypatch, gate)
    inc = {
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": "SRTWC286"},
            {"key": "c", "label": "Container", "value": "SEGU4008631"},
            {"key": "e", "label": "ETA", "value": "2026-10-15"},
            {"key": "q", "label": "Incoming Quantity", "value": 40},
        ],
        "flags": {"unallocated": True},
    }
    block = _render([inc], _zs("inventory", "SRTWC286"))["block"]
    assert block == (
        "No stock for SRTWC286.\n\nBut there is INCOMING stock (ETA) for the requested products:\n\n"
        "- *Product Code:* SRTWC286\n*Container:* SEGU4008631\n*ETA:* 2026-10-15\n"
        "*Incoming Quantity:* 40\n\U0001f6a9  *(PENDING ALLOCATION)*"
    )


def test_rendered_codes_stamped_gate_on_only(monkeypatch):
    zs = _zs("incoming", "SRT5734-WH")
    _gate(monkeypatch, False)
    assert "rendered_codes" not in _render([_stock_item()], zs)
    _gate(monkeypatch, True)
    xd = _render([_stock_item()], zs)
    assert set(xd["rendered_codes"]) == {"SRT5734-WH"}


def _result(block_text, rendered=None):
    xd_block = {"block": block_text, "nothing_codes": []}
    if rendered is not None:
        xd_block["rendered_codes"] = rendered
    return {
        "zeroset": {
            "_xd": {
                "missing": [
                    {"code": "AAA1", "_n": "AAA1", "zero": True},
                    {"code": "BBB2", "_n": "BBB2", "zero": True},
                ]
            }
        },
        "render": {"_xdBlock": xd_block},
    }


def test_zero_note_names_only_rendered_codes_gate_off(monkeypatch):
    from app.services.chatbot.answer_bridge import _prefix_zero_note

    _gate(monkeypatch, False)
    out = _prefix_zero_note(_result("- *Product Code:* BBB2\n*Total:* 0"))
    assert out["render"]["_xdBlock"]["block"].startswith("No stock for BBB2.\n\n")


def test_zero_note_names_only_rendered_codes_gate_on(monkeypatch):
    """The numbered block's rows are not read by the "- *Product Code:*" pattern, so the
    note reads the `rendered_codes` the render stamped."""
    from app.services.chatbot.answer_bridge import _prefix_zero_note

    _gate(monkeypatch, True)
    out = _prefix_zero_note(_result("1. *Product Code:* BBB2\n*Total:* 0", rendered=["BBB2"]))
    assert out["render"]["_xdBlock"]["block"].startswith("No stock for BBB2.\n\n")
    assert "AAA1" not in out["render"]["_xdBlock"]["block"]


def test_xd_labels_translate_ms(monkeypatch):
    _gate(monkeypatch, True)
    block = _render([_stock_item()], _zs("incoming", "SRT5734-WH"))["block"]
    ms = Localizer("ms", defaults("ms")).reply(block)
    assert "1. *Kod Produk:* SRT5734-WH" in ms
    assert "Product Code" not in ms
