"""MULTI-ASK-PLAN2 round 6, red first: the golden generalisation (owner hand test, 9 Oct 2026).

The design is parser driven: every test stubs the parser verdict and asserts nothing that
depends on a keyword in the message text. The catalogue has the dev shape: SRTWC286 has NO
exact row and three SRTWC286-SH* codes, SRTWT165 and CB6633 each have an exact row plus two
variants, SRT38-SS is an exact row with no variants.

Spec: G1 scope question before both paths, G2 every 2+ sub_ask staff message is the
runner's, G3 a sold ask with no window asks the period, G4 a vague message asks what is
wanted, G4b a new message closes the old question.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot import turn_runtime

from tests.chatbot import _r9_engine_console as r9
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_multi_ask_runner import (
    NO_WINDOW,
    RUNNER_TOOLS,
    SPO_TOOL,
    Y2026,
    _console,
    _ids,
    pick,
    sub,
)

WC = ["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-NEW"]
WT = ["SRTWT165", "SRTWT165-A", "SRTWT165-B"]
CB = ["CB6633", "CB6633-A", "CB6633-B"]
SS = ["SRT38-SS"]
STOCK_TOOL = "crm_inventory_stock_balance_list"
GRANTS_PHONE = iter(range(60000009700, 60000009800))


@pytest.fixture(autouse=True)
def dev_catalogue(monkeypatch):
    monkeypatch.setattr(r9, "OWNER_FAMILY", WC)
    monkeypatch.setattr(r9, "OTHER_CODES", WT + CB + SS + ["ELP3754"])


def phone() -> str:
    return f"+{next(GRANTS_PHONE)}"


def con(session_factory, monkeypatch, stub_access, **kw):
    return _console(session_factory, monkeypatch, stub_access, phone(), **kw)


def v(code, sub_asks, *, family=None, domain="inventory", intent="check_stock", **extra):
    return verdict(
        domain_hint=domain,
        intent_hint=intent,
        entities=[entity(code, hint="product")],
        sub_asks=sub_asks,
        family=family,
        **extra,
    )


def pids(args: dict[str, Any]) -> list[str]:
    return _ids(args.get("product_ids"))


def no_tools(c) -> bool:
    return not c.tools.calls


# ------------------------------------------------------------------ G1 scope question


def test_g1a_exact_row_with_variants_multi_ask_asks_scope(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRTWT165 库存和采购价", v("SRTWT165", [sub("stock", "库存"), sub("last_cost", "采购价")]))
    assert text.splitlines()[0] == "SRTWT165 有 3 个款式。只要 SRTWT165，还是全部 3 款？"
    assert no_tools(c)


def test_g1b_prefix_only_multi_ask_asks_which_variant(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRTWC286 采购价 入库 卖出", _case_c(basis="delivered", window=Y2026))
    assert text.splitlines() == [
        "SRTWC286 有 3 个款式，要哪个？",
        "1. 全部 3 款",
        "2. SRTWC286-SH",
        "3. SRTWC286-SH-150",
        "4. SRTWC286-SH-NEW",
    ]
    assert no_tools(c)


def _case_c(*, basis, window):
    return v(
        "SRTWC286",
        [
            sub("last_cost", "采购价"),
            sub("received_qty", "入库了多少数量"),
            sub("sold_total", "总共卖了多少个", window=window, basis=basis),
            sub("sold_by", "每位业务员卖了多少个", window=window, group_by="sales_agent", basis=basis),
        ],
        domain="purchase_cost",
        intent="check_po_cost",
    )


def test_g1b_pick_all_gives_one_combined_reply(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say("SRTWC286 采购价 入库 卖出", _case_c(basis="delivered", window=Y2026))
    text = c.say("全部", pick(1))
    lines = text.splitlines()
    assert lines[0] == "SRTWC286（全部 3 款）"
    assert "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）" in lines
    assert "2026 总共卖出（已出货）: 860" in lines
    assert any(ln.endswith(f"{1200 * 3:,}") for ln in lines)
    family = sorted(c.uuid_of[x] for x in WC)
    for name, args in c.tools.calls:
        if name in RUNNER_TOOLS:
            assert sorted(pids(args)) == family, name


def test_g1b_pick_one_variant_scopes_to_it(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say("SRTWC286 采购价 入库 卖出", _case_c(basis="delivered", window=Y2026))
    text = c.say("第三个", pick(3))
    assert text.splitlines()[0] == "SRTWC286-SH-150"
    runner_calls = [(n, a) for n, a in c.tools.calls if n in RUNNER_TOOLS]
    assert runner_calls
    for name, args in runner_calls:
        assert pids(args) == [c.uuid_of["SRTWC286-SH-150"]], name


def _legacy(c, domain="inventory", intent="check_stock", message="stock SRTWT165"):
    return c.say(
        message,
        verdict(domain_hint=domain, intent_hint=intent, entities=[entity("SRTWT165", hint="product")], sub_asks=None, family=None),
    )


def test_g1c_one_ask_stock_keeps_todays_path_no_question(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = _legacy(c)
    assert "variants" not in text and "个款式" not in text
    stock = c.tools.args(STOCK_TOOL)
    assert stock, "today's stock call runs on turn 1"
    assert sorted(pids(stock[0])) == sorted(c.uuid_of[x] for x in WT)


def test_g1_one_ask_incoming_keeps_the_gate_picker_no_scope_question(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = _legacy(c, domain="incoming", intent="check_incoming", message="incoming SRTWT165")
    assert "variants" not in text and "个款式" not in text
    assert "Related products" in text and "Reply with a number" in text


def test_g1b_over_twenty_codes_offers_all_n_and_type_the_code(session_factory, monkeypatch, stub_access):
    big = [f"ZZTBIG-{i:02d}" for i in range(21)]
    monkeypatch.setattr(r9, "OTHER_CODES", WT + SS + ["ELP3754"] + big)
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say(
        "ZZTBIG stock and last cost",
        v("ZZTBIG", [sub("stock", "stock"), sub("last_cost", "last cost")]),
    )
    lines = text.splitlines()
    assert "1. All 21" in lines
    assert any("full code" in ln for ln in lines)
    assert not any("ZZTBIG-05" in ln for ln in lines)
    assert len(lines) <= 4
    assert no_tools(c)


@pytest.mark.parametrize("family", [True, False])
def test_g1d_parser_family_set_never_asks(session_factory, monkeypatch, stub_access, family):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRTWT165 库存和采购价", v("SRTWT165", [sub("stock", "库存"), sub("last_cost", "采购价")], family=family))
    assert "个款式" not in text
    assert not (c.stored_question or {}).get("kind") == "sub_ask_scope"


def test_g1e_availability_only_contact_never_asks(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
    text = c.say("SRTWT165 库存和采购价", v("SRTWT165", [sub("stock", "库存"), sub("last_cost", "采购价")]))
    assert "个款式" not in text
    assert (c.stored_question or {}).get("kind") != "sub_ask_scope"


def test_g1e_dealer_contact_never_asks(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access, staff=False)
    text = c.say("SRTWT165 库存和采购价", v("SRTWT165", [sub("stock", "库存"), sub("last_cost", "采购价")]))
    assert "个款式" not in text
    assert (c.stored_question or {}).get("kind") != "sub_ask_scope"


def test_g1f_code_without_variants_never_asks(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRT38-SS 库存和采购价", v("SRT38-SS", [sub("stock", "库存"), sub("last_cost", "采购价")]))
    assert "个款式" not in text
    assert text.splitlines()[0] == "SRT38-SS"


# ------------------------------------------------------------------ G2 the runner for every 2+


def test_g2a_stock_and_cost_use_the_runner_lines(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRTWT165 库存和采购价", v("SRTWT165", [sub("stock", "库存"), sub("last_cost", "采购价")], family=False))
    assert text.splitlines() == [
        "SRTWT165",
        "库存: 120",
        "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）",
    ]


def test_g2b_stock_and_incoming_use_the_runner_lines(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRTWT165 库存和到货", v("SRTWT165", [sub("stock", "库存"), sub("incoming", "到货")], family=False))
    lines = text.splitlines()
    assert lines[0] == "SRTWT165"
    assert lines[1] == "库存: 120"
    assert len(lines) == 3 and lines[2].startswith("到货")
    assert c.tools.args(STOCK_TOOL)[0].get("include_sellable") is True


def test_g2c_resolved_customer_rides_the_runner(session_factory, monkeypatch, stub_access):
    from app.models.order import Customer
    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

    c = con(session_factory, monkeypatch, stub_access)
    cid = str(uuid.uuid4())
    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    db.add(Customer(id=cid, customer_code="ZZTGOLD1", customer_name="ZZT GOLDEN TRADING", company_id=SORENTO))
    db.commit()
    db.close()
    text = c.say(
        "SRTWT165 sold to ZZT GOLDEN TRADING 2026 and stock",
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("SRTWT165", hint="product"), entity("ZZT GOLDEN TRADING", hint="customer", canonical_code="ZZTGOLD1")],
            sub_asks=[sub("sold_total", "卖出", window=Y2026, basis="delivered"), sub("stock", "库存")],
            family=False,
        ),
    )
    [report] = c.tools.args("crm_report_ask")
    assert _ids(report.get("customer_ids")) == [cid]
    assert "Stock: 120" in text.splitlines()
    assert report.get("view") != "render", "the runner asks raw, never today's render path"


def test_g2d_a_measure_with_no_query_says_so_and_the_rest_answer(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say(
        "SRTWT165 margin and stock",
        v("SRTWT165", [sub("other", "margin"), sub("stock", "stock")], family=False),
    )
    lines = text.splitlines()
    assert "margin: not available yet" in lines
    assert "Stock: 120" in lines


# ------------------------------------------------------------------ G3 period question


def _sold_no_window(basis="delivered"):
    return v(
        "SRT38-SS",
        [sub("last_cost", "采购价"), sub("sold_total", "卖了多少", basis=basis)],
        domain="purchase_cost",
        intent="check_po_cost",
    )


def test_g3_sold_without_a_window_asks_the_period_last_zh(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRT38-SS 采购价 卖了多少", _sold_no_window())
    lines = text.splitlines()
    assert "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）" in lines
    assert lines[-1] == "卖出要算哪个时间段？"
    assert "crm_report_ask" not in c.tools.names()


def test_g3_sold_without_a_window_asks_the_period_last_en(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say(
        "SRT38-SS last cost and sold",
        v("SRT38-SS", [sub("last_cost", "last cost"), sub("sold_total", "sold", basis="delivered")], domain="purchase_cost", intent="check_po_cost"),
    )
    assert text.splitlines()[-1] == "Sold over which period?"
    assert "crm_report_ask" not in c.tools.names()


def test_g3_the_period_answer_runs_the_sold_lines(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say("SRT38-SS 采购价 卖了多少", _sold_no_window())
    text = c.say(
        "2026 全年",
        verdict(
            domain_hint=None, intent_hint=None, entities=[], sub_asks=None,
            date_mode="range", date_filter_start="2026-01-01", date_filter_end="2026-12-31",
            open_question_answer=None,
        ),
    )
    assert "2026 总共卖出（已出货）: 860" in text.splitlines()
    [report] = c.tools.args("crm_report_ask")
    assert (report.get("date_from"), report.get("date_to")) == ("2026-01-01", "2026-12-31")


def test_g3_period_comes_before_basis_when_both_are_missing(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    text = c.say("SRT38-SS 采购价 卖了多少", _sold_no_window(basis=None))
    lines = text.splitlines()
    assert lines[-1] == "卖出要算哪个时间段？"
    assert "卖出按已出货还是按下单算？" not in text
    assert "crm_report_ask" not in c.tools.names()
    text = c.say(
        "2026 全年",
        verdict(
            domain_hint=None, intent_hint=None, entities=[], sub_asks=None,
            date_mode="range", date_filter_start="2026-01-01", date_filter_end="2026-12-31",
        ),
    )
    assert text.splitlines()[-1] == "卖出按已出货还是按下单算？"


# ------------------------------------------------------------------ G4 vague message


def test_g4_a_vague_message_asks_what_is_wanted_zh(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say(
        "ELP3754 spo",
        verdict(domain_hint="spo_allocation", intent_hint="check_spo", entities=[entity("ELP3754", hint="product")], family=False),
    )
    before = len(c.tools.calls)
    text = c.say(
        "你好 s r t",
        verdict(
            domain_hint=None, intent_hint=None, sub_asks=None, asks=None,
            entities=[entity("s r t", hint="product", canonical_code=None, current_message=True, confident=False)],
        ),
    )
    assert text.splitlines()[0] == "你想了解 s r t 的什么？库存、到货、采购价，还是销售？"
    assert len(c.tools.calls) == before


def test_g4_a_vague_message_asks_what_is_wanted_en(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say(
        "ELP3754 spo",
        verdict(domain_hint="spo_allocation", intent_hint="check_spo", entities=[entity("ELP3754", hint="product")], family=False),
    )
    before = len(c.tools.calls)
    text = c.say(
        "s r t please",
        verdict(
            domain_hint=None, intent_hint=None, sub_asks=None, asks=None,
            entities=[entity("s r t", hint="product", canonical_code=None, current_message=True, confident=False)],
        ),
    )
    assert text.splitlines()[0] == "What would you like to know about s r t? Stock, incoming, cost or sales?"
    assert len(c.tools.calls) == before


VAGUE_EN = "What would you like to know about s r t? Stock, incoming, cost or sales?"


def test_g4_live_case_b_confident_entity_still_asks(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say(
        "ELP3754 spo",
        verdict(domain_hint="spo_allocation", intent_hint="check_spo", entities=[entity("ELP3754", hint="product")], family=False),
    )
    before = len(c.tools.calls)
    text = c.say(
        "s r t please",
        verdict(
            domain_hint=None, intent_hint=None, sub_asks=None, asks=None, continuation=False,
            anaphora={"backward_reference": False},
            entities=[entity("s r t", hint="product", canonical_code=None, current_message=True, confident=True, hint_confident=True)],
        ),
    )
    assert text.splitlines()[0] == VAGUE_EN
    assert len(c.tools.calls) == before


def test_g4_guard_a_real_follow_up_is_not_the_vague_question(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say(
        "stock SRTWT165",
        verdict(domain_hint="inventory", intent_hint="check_stock", entities=[entity("SRTWT165", hint="product")], family=False),
    )
    text = c.say(
        "CB6633",
        verdict(
            domain_hint=None, intent_hint=None, sub_asks=None, asks=None, continuation=True,
            anaphora={"backward_reference": True},
            entities=[entity("CB6633", hint="product")],
        ),
    )
    assert "What would you like to know about" not in text
    assert "你想了解" not in text


# ------------------------------------------------------------------ G4b moving on


def test_g4b_a_new_message_closes_the_basis_question(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    c.say(
        "SRT38-SS 2026 卖了多少 采购价",
        v("SRT38-SS", [sub("last_cost", "采购价"), sub("sold_total", "卖了多少", window=Y2026, basis=None)], domain="purchase_cost", intent="check_po_cost"),
    )
    assert c.stored_question and c.stored_question.get("kind") == "sub_ask_basis"
    text = c.say(
        "ELP3754 库存和采购价",
        v("ELP3754", [sub("stock", "库存"), sub("last_cost", "采购价")], family=False),
    )
    assert text.splitlines() == ["ELP3754", "库存: 120", "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）"]
    kinds = {(c.stored_question or {}).get("kind"), ((c.state or {}).get("pending") or {}).get("kind") if isinstance((c.state or {}).get("pending"), dict) else None}
    assert "sub_ask_basis" not in kinds


# ------------------------------------------------------------------ the owner's Case C end to end


def test_case_c_end_to_end(session_factory, monkeypatch, stub_access):
    c = con(session_factory, monkeypatch, stub_access)
    t1 = c.say("SRTWC286 采购价 入库 卖出", _case_c(basis="delivered", window=NO_WINDOW))
    assert t1.splitlines()[0] == "SRTWC286 有 3 个款式，要哪个？"
    assert no_tools(c)
    t2 = c.say("全部", pick(1))
    lines = t2.splitlines()
    assert lines[0] == "SRTWC286（全部 3 款）"
    assert "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）" in lines
    assert any(ln.endswith(f"{1200 * 3:,}") for ln in lines)
    assert lines[-1] == "卖出要算哪个时间段？"
    assert "crm_report_ask" not in c.tools.names()
    t3 = c.say(
        "2026 全年",
        verdict(
            domain_hint=None, intent_hint=None, entities=[], sub_asks=None,
            date_mode="range", date_filter_start="2026-01-01", date_filter_end="2026-12-31",
        ),
    )
    assert "2026 总共卖出（已出货）: 860" in t3.splitlines()
    assert any("AGENT01" in ln for ln in t3.splitlines())


# ------------------------------------------------------------------ round 6d


def _big_console(session_factory, monkeypatch, stub_access):
    big = [f"ZZTBIG-{i:02d}" for i in range(21)]
    monkeypatch.setattr(r9, "OTHER_CODES", WT + SS + ["ELP3754"] + big)
    c = con(session_factory, monkeypatch, stub_access)
    c.say("ZZTBIG stock and last cost", v("ZZTBIG", [sub("stock", "stock"), sub("last_cost", "last cost")]))
    return c


def _typed_code_checks(c, text):
    lines = text.splitlines()
    assert lines[0] == "ZZTBIG-05"
    assert "Stock: 120" in lines
    calls = [(n, a) for n, a in c.tools.calls if n in RUNNER_TOOLS | {STOCK_TOOL}]
    assert calls
    for name, args in calls:
        assert pids(args) == [c.uuid_of["ZZTBIG-05"]], name
    assert (c.stored_question or {}).get("kind") not in ("sub_ask_scope", "sub_ask_basis")


def test_g1b_over_twenty_typed_code_as_an_open_question_item(session_factory, monkeypatch, stub_access):
    c = _big_console(session_factory, monkeypatch, stub_access)
    text = c.say(
        "ZZTBIG-05",
        verdict(
            domain_hint=None, intent_hint=None, entities=[],
            open_question_answer={"mode": "pick", "picked": [], "items": [{"position": None, "code": "ZZTBIG-05", "qty": None}], "qty_for_all": None},
        ),
    )
    _typed_code_checks(c, text)


def test_g1b_over_twenty_typed_code_as_a_fresh_entity(session_factory, monkeypatch, stub_access):
    c = _big_console(session_factory, monkeypatch, stub_access)
    text = c.say(
        "ZZTBIG-05",
        verdict(
            domain_hint="inventory", intent_hint="check_stock",
            entities=[entity("ZZTBIG-05", hint="product", current_message=True)],
            sub_asks=None, open_question_answer=None,
        ),
    )
    _typed_code_checks(c, text)


def test_g2_filters_are_this_messages_customer_never_the_focus_one(session_factory, monkeypatch, stub_access):
    from app.models.order import Customer
    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

    c = con(session_factory, monkeypatch, stub_access)
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    db.add(Customer(id=a_id, customer_code="ZZTCUSTA", customer_name="ZZT ALPHA TRADING", company_id=SORENTO))
    db.add(Customer(id=b_id, customer_code="ZZTCUSTB", customer_name="ZZT BRAVO TRADING", company_id=SORENTO))
    db.commit()
    db.close()
    c.say(
        "orders for ZZT ALPHA TRADING",
        verdict(domain_hint="order", intent_hint="check_order", entities=[entity("ZZT ALPHA TRADING", hint="customer", canonical_code="ZZTCUSTA")]),
    )
    c.tools.calls.clear()
    text = c.say(
        "SRTWT165 sold to ZZT BRAVO TRADING 2026 and stock",
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("SRTWT165", hint="product"), entity("ZZT BRAVO TRADING", hint="customer", canonical_code="ZZTCUSTB")],
            sub_asks=[sub("sold_total", "sold", window=Y2026, basis="delivered"), sub("stock", "stock")],
            family=False,
        ),
    )
    [report] = c.tools.args("crm_report_ask")
    assert _ids(report.get("customer_ids")) == [b_id]
    assert "Stock: 120" in text.splitlines()


# ------------------------------------------------------------------ round 7, the parser prompt


def _addendum() -> str:
    from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

    return SUB_ASKS_ADDENDUM


def _block(text: str, start: str, stop: str) -> str:
    a = text.index(start)
    b = text.find(stop, a + len(start))
    return text[a : b if b != -1 else len(text)]


def test_p1_period_only_from_the_current_messages_own_words():
    block = _block(_addendum(), "date_from / date_to", "basis:")
    assert "calendar year" in block and "null" in block


def test_p2_basis_only_from_a_delivered_or_ordered_word_never_the_default():
    block = _block(_addendum(), "basis:", "label:")
    assert "default" in block and "null" in block


def test_p3_jin_cang_ku_is_received_never_incoming():
    lines = [ln for ln in _addendum().splitlines() if "进仓库" in ln]
    assert lines
    assert any("never incoming" in ln.lower() for ln in lines)


def test_p4_malay_jual_is_sold_in_the_sold_total_definition():
    text = _addendum()
    start = text.index('"sold_total"')
    assert "jual" in text[start : start + 160].lower()


def test_p5_a_bare_product_mention_is_no_domain_and_no_asks():
    text = _addendum()
    assert "我想问" in text and "domain_hint null" in text


def test_p6_no_live_string_appears_verbatim_in_the_prompt():
    live = [
        "srtwc286采购价、进仓库多少、卖了多少、业务员各卖多少",
        "SRT384 last cost, supplier and how many received this year?",
        "CB1178A berapa stok, harga beli last, dan jual berapa 2025?",
        "CB6633 2025 sold per salesman and total, last in bila?",
        "供应商是谁？SRTSV310 最后一次进库几时，今年卖了多少",
        "how many CWCX605 came in last month and incoming ETA, and margin",
        "SRTWT165 stock + 采购价",
        "我想问s r t",
        "SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么时候 ，2026 总共卖了多少个，2026年每位业务员共卖了多少个",
        "我想问srtwc286的采购价，还要他进仓库了多少，我们卖了多少啊，然后业务员卖了多少啊这些你都给我知道",
        "SRTWT165 berapa stok dan harga beli terakhir?",
        "CB6633 incoming and stock, plus who is the supplier",
        "SRTSV310 今年 sold per customer 和 last cost",
    ]
    text = _addendum()
    for s in live:
        assert s not in text, s
