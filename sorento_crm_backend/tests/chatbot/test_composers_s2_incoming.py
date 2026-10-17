"""CHAT-COMPOSER S2 red tests: the cross-domain block (ETA miss says the absence once, its stock twin,
incoming rows under a stock miss numbered with rendered_codes stamped, PO rung date).
Pure, no database. Goldens are literals captured from main code on 17 Oct (gate off).
"""
from __future__ import annotations

import pytest

from app.services.chatbot.label_catalog import Localizer, defaults
from tests.chatbot.test_composers_stock_xd import _set_gate


def _f(key, label, value):
    return {"key": key, "label": label, "value": value}


def _render(items, zs):
    from app.services.chatbot.lanes.business.answer import crossdomain_render

    return crossdomain_render({"items": items, "has_result": True}, zeroset=zs, validator={})


def _zs(origin, *codes, zero=False):
    return {
        "active": True,
        "origin_domain": origin,
        "team": "warehouse",
        "missing": [{"code": c, "_n": c, "uuid": f"u-{c}", **({"zero": True} if zero else {})} for c in codes],
    }


def _incoming_row(code="CB890SS-BL-DIY", *, company=True, container="CONT1"):
    fields = [
        _f("product_code", "Product Code", code),
        _f("shipping_container_number", "Container", container),
        _f("estimated_arrival_date", "ETA", "2026-10-21"),
        _f("remaining_incoming_quantity", "Incoming Quantity", 420),
    ]
    if company:
        fields.insert(0, _f("company_name", "Company", "Sorento"))
    return {"fields": fields, "flags": {"unallocated": True}}


def _stock_row(code="SRT5734-WH"):
    return {"fields": [_f("product_code", "Product Code", code), _f("total", "Total", "60 (O/S: 0)")]}


MISS_INC = (
    "Here's what you want:\n• product: SRT5734-WH\n\nBut no incoming matched these."
    " Would you like me to escalate to warehouse team?"
)
MISS_INV = (
    "Here's what you want:\n• product: CB890SS-BL-DIY\n\nBut no inventory matched these."
    " Would you like me to escalate to warehouse team?"
)
SUGGEST = (
    "No incoming stock (ETA) found for SRT5734-WH. Related products:\n1. SRT5734-BL - has incoming"
    "\n2. SRT5734-GM - no incoming\nReply with a number to check its incoming, or reply 'yes' to escalate"
    " to warehouse team."
)
ENTITLEMENT = "SORENTO PROMO is not available to you. Would you like me to escalate to warehouse team?"

XD_INC = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))
XD_INV = _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))


def _fold(text, xd, **kw):
    from app.services.chatbot.answer_bridge import _apply_crossdomain_render

    return _apply_crossdomain_render(text, {"render": xd}, **kw)


# --------------------------------------------------------------------------- #
# Gate-off goldens (pass at red)
# --------------------------------------------------------------------------- #


def test_eta_miss_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _fold(MISS_INC, XD_INC) == (
        "Here's what you want:\n• product: SRT5734-WH\n\nBut no incoming matched these.\nNo incoming for SRT5734-WH."
        "\n\nBut here are the stock details for the requested products:\n\n- *Product Code:* SRT5734-WH"
        "\n*Total:* 60 (O/S: 0)\n\nWould you like me to escalate to warehouse team?"
    )


def test_stock_twin_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _fold(MISS_INV, XD_INV) == (
        "Here's what you want:\n• product: CB890SS-BL-DIY\n\nBut no inventory matched these.\nNo stock for CB890SS-BL-DIY."
        "\n\nBut there is INCOMING stock (ETA) for the requested products:\n\n- *Company:* Sorento"
        "\n*Product Code:* CB890SS-BL-DIY\n*Container:* CONT1\n*ETA:* 2026-10-21\n*Incoming Quantity:* 420"
        "\n\U0001f6a9  *(PENDING ALLOCATION)*\n\nWould you like me to escalate to warehouse team?"
    )


def test_hit_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _fold("SRT1: ETA x", XD_INC, answered=True) == (
        "SRT1: ETA x\nNo incoming for SRT5734-WH.\n\nBut here are the stock details for the requested products:"
        "\n\n- *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n\nWould you like me to escalate to warehouse team?"
    )


def test_suggest_offer_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _fold(SUGGEST, XD_INC) == (
        "No incoming stock (ETA) found for SRT5734-WH.\nNo incoming for SRT5734-WH.\n\nBut here are the stock details"
        " for the requested products:\n\n- *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n\nRelated products:"
        "\n1. SRT5734-BL - has incoming\n2. SRT5734-GM - no incoming\nReply with a number to check its incoming,"
        " or reply 'yes' to escalate to warehouse team."
    )


def test_stock_miss_incoming_block_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    item = _incoming_row()
    item["fields"].append(_f("loc", "Location", None))
    xd = _render([item], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]
    assert "rendered_codes" not in xd and "only_other_note" not in xd
    assert xd["block"] == (
        "No stock for CB890SS-BL-DIY.\n\nBut there is INCOMING stock (ETA) for the requested products:\n\n"
        "- *Company:* Sorento\n*Product Code:* CB890SS-BL-DIY\n*Container:* CONT1\n*ETA:* 2026-10-21"
        "\n*Incoming Quantity:* 420\n*Location:* -\n\U0001f6a9  *(PENDING ALLOCATION)*"
    )


def test_po_rung_date_gate_off_golden(monkeypatch):
    from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text

    _set_gate(monkeypatch, False)
    assert _crossdomain_rung_text([{"product_code": "X", "qty": 30, "po_date": "2026-08-10"}]) == (
        "*Product Code:* X\n*Outstanding:* 30\n*PO date:* 2026-08-10"
    )


# --------------------------------------------------------------------------- #
# Gate on: the absence is said once
# --------------------------------------------------------------------------- #


def test_eta_miss_says_it_once_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    xd = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))
    out = _fold(MISS_INC, xd)
    assert out.count("But no incoming matched these.") == 1
    assert "No incoming for SRT5734-WH." not in out
    assert "But here are the stock details for the requested products:" in out
    assert "1. *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)" in out
    assert out.endswith("Would you like me to escalate to warehouse team?")


def test_stock_twin_says_it_once_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    xd = _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))
    out = _fold(MISS_INV, xd)
    assert out.count("But no inventory matched these.") == 1
    assert "No stock for CB890SS-BL-DIY." not in out
    assert "But there is INCOMING stock (ETA) for the requested products:" in out


def test_eta_hit_keeps_only_other_line_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    xd = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))
    out = _fold("SRT1: ETA x", xd, answered=True)
    assert "No incoming for SRT5734-WH." in out
    assert out.count("No incoming for SRT5734-WH.") == 1


def test_suggest_offer_miss_once_and_picker_unchanged_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    xd = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))
    out = _fold(SUGGEST, xd)
    assert "No incoming for SRT5734-WH." not in out
    assert out.startswith("No incoming stock (ETA) found for SRT5734-WH.")
    picker = (
        "Related products:\n1. SRT5734-BL - has incoming\n2. SRT5734-GM - no incoming\n"
        "Reply with a number to check its incoming, or reply 'yes' to escalate to warehouse team."
    )
    assert out.endswith(picker)


def test_entitlement_miss_keeps_the_absence_gate_on(monkeypatch):
    """Risk R3: a miss text that does not itself say the primary domain has nothing keeps the
    crossdomain line (the fact would otherwise be lost)."""
    _set_gate(monkeypatch, True)
    xd = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))
    out = _fold(ENTITLEMENT, xd)
    assert "No incoming for SRT5734-WH." in out


def test_only_other_note_stamped_gate_on_only(monkeypatch):
    _set_gate(monkeypatch, False)
    assert "only_other_note" not in _render([_stock_row()], _zs("incoming", "SRT5734-WH"))["_xdBlock"]
    _set_gate(monkeypatch, True)
    stamped = _render([_stock_row()], _zs("incoming", "SRT5734-WH"))["_xdBlock"]
    assert stamped["only_other_note"] == "No incoming for SRT5734-WH."
    twin = _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]
    assert twin["only_other_note"] == "No stock for CB890SS-BL-DIY."


# --------------------------------------------------------------------------- #
# Gate on: incoming rows under a stock miss use the numbered block
# --------------------------------------------------------------------------- #

NUMBERED_INC = (
    "1. *Company:* Sorento\n*Product Code:* CB890SS-BL-DIY\n*Container:* CONT1\n*ETA:* 21/10/2026"
    "\n*Incoming Quantity:* 420\n\U0001f6a9  *(PENDING ALLOCATION)*"
)


def test_stock_miss_incoming_block_numbered_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    block = _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]["block"]
    assert NUMBERED_INC in block
    assert not [ln for ln in block.splitlines() if ln.startswith("- *")]
    assert "But there is INCOMING stock (ETA) for the requested products:" in block


def test_xd_incoming_row_equals_main_path_row(monkeypatch):
    from app.services.chatbot.lanes.business.fetch import output_structurer

    _set_gate(monkeypatch, True)
    item = _incoming_row()
    main = output_structurer(
        {"items": [item], "answers": [{"text": "x"}], "has_result": True},
        {"tool": "crm_incoming_stock_list", "semantic_input": {}},
    )["response"]
    row = main.split("\n\n", 1)[1]
    assert row == NUMBERED_INC
    assert row in _render([item], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]["block"]


def test_two_incoming_rows_number_in_order_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    rows = [_incoming_row("AAA1", container="C1"), _incoming_row("BBB2", container="C2")]
    block = _render(rows, _zs("inventory", "AAA1", "BBB2"))["_xdBlock"]["block"]
    assert "\n\n1. *Company:* Sorento\n*Product Code:* AAA1" in block
    assert "\n\n2. *Company:* Sorento\n*Product Code:* BBB2" in block


def test_rendered_codes_both_origins_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert list(_render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]["rendered_codes"]) == [
        "CB890SS-BL-DIY"
    ]
    assert set(_render([_stock_row()], _zs("incoming", "SRT5734-WH"))["_xdBlock"]["rendered_codes"]) == {
        "SRT5734-WH"
    }


def _zero_result(xd_render, codes):
    return {
        "zeroset": {"_xd": {"missing": [{"code": c, "_n": c, "zero": True} for c in codes]}},
        "render": xd_render,
    }


def test_zero_note_numbered_incoming_rows_gate_on(monkeypatch):
    """hp11 guard: Product Code is the FIRST field of the numbered row, which the dash-form
    pattern misses; the note must still name only the rendered zero code."""
    from app.services.chatbot.answer_bridge import _prefix_zero_note

    _set_gate(monkeypatch, True)
    zs = {
        "active": True,
        "origin_domain": "inventory",
        "team": "warehouse",
        # AAA1 has no uuid, so it is never "probed" and never lands in nothing_codes.
        "missing": [
            {"code": "AAA1", "_n": "AAA1", "uuid": None, "zero": True},
            {"code": "BBB2", "_n": "BBB2", "uuid": "u-b", "zero": True},
        ],
    }
    render = _render([_incoming_row("BBB2", company=False)], zs)
    assert "1. *Product Code:* BBB2" in render["_xdBlock"]["block"]
    out = _prefix_zero_note(_zero_result(render, ["AAA1", "BBB2"]))
    block = out["render"]["_xdBlock"]["block"]
    assert block.startswith("No stock for BBB2.\n\n"), block
    assert "AAA1" not in block


@pytest.mark.parametrize("loc,words", [("ms", ["Kontena", "Kuantiti Masuk"]), ("zh", ["货柜"])])
def test_xd_incoming_block_translates_gate_on(monkeypatch, loc, words):
    _set_gate(monkeypatch, True)
    block = _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))["_xdBlock"]["block"]
    assert "\n1. *Company:*" in "\n" + block, "numbered rows only"
    out = Localizer(loc, defaults(loc)).reply(block)
    for w in words:
        assert w in out, out
    assert "*Container:*" not in out and "*Incoming Quantity:*" not in out


def test_po_rung_date_gate_on(monkeypatch):
    from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text

    _set_gate(monkeypatch, True)
    text = _crossdomain_rung_text([{"product_code": "X", "qty": 30, "po_date": "2026-08-10"}])
    assert text == "*Product Code:* X\n*Outstanding:* 30\n*PO date:* 10/08/2026"


def test_s2_xd_output_clean_gate_on(monkeypatch):
    import re

    _set_gate(monkeypatch, True)
    texts = [
        _fold(MISS_INC, _render([_stock_row()], _zs("incoming", "SRT5734-WH"))),
        _fold(MISS_INV, _render([_incoming_row()], _zs("inventory", "CB890SS-BL-DIY"))),
    ]
    for t in texts:
        assert " · " not in t
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)
        assert not re.search(r":\*\s+\d{4}-\d{2}-\d{2}\s*$", t, re.MULTILINE)
        assert "Shipment Container" not in t
