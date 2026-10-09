"""MULTI-ASK-PLAN slices 2-4, red first (owner, 9 Oct 2026; PROD turn 8 Oct 19:50).

"SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么时候 ，2026
总共卖了多少个，2026年每位业务员共卖了多少个" is six asks. The parser emits them as
`sub_asks` (one per thing asked, in order), the engine runs ONE query per sub-ask and
the reply is one `Label: value` line per sub-ask, nothing unasked.

Through `engine.run_turn` on the r9 console (real engine, real resolver over seeded
codes); the parser verdict and the MCP tools are stubbed. SRTWC286-SH stands in for
SRTWT7445: it hits exactly AND has nine prefix siblings in the seeded catalogue (ten
codes in all), the shape the owner's code has. ELP3754 has no siblings.

Card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md (Acceptance 1-8).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import turn_runtime

from tests.chatbot._r9_engine_console import EngineConsole, OWNER_FAMILY
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

ALL_GRANTS = [
    "inventory.sellable",
    "purchase_orders.cost",
    "purchase_orders.supplier",
    "purchase_orders.placed",
    "sales_orders.sales_report",
]

OWNER_MESSAGE = (
    "SRTWC286-SH,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么时候 ，"
    "2026 总共卖了多少个，2026年每位业务员共卖了多少个"
)

Y2026 = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
NO_WINDOW = {"date_from": None, "date_to": None}


def sub(measure: str, label: str, *, window=None, group_by=None, basis=None) -> dict[str, Any]:
    return {
        "measure": measure,
        "group_by": group_by,
        **(window or NO_WINDOW),
        "basis": basis,
        "label": label,
    }


def owner_sub_asks(basis: str | None = "delivered") -> list[dict[str, Any]]:
    return [
        sub("received_qty", "入库了多少数量", window=Y2026),
        sub("supplier", "供应商是谁"),
        sub("last_cost", "采购价是多少钱"),
        sub("last_receipt", "最后一次进库是什么时候"),
        sub("sold_total", "总共卖了多少个", window=Y2026, basis=basis),
        sub("sold_by", "每位业务员共卖了多少个", window=Y2026, group_by="sales_agent", basis=basis),
    ]


def owner_verdict(*, code: str = "SRTWC286-SH", family: bool | None = False, basis: str | None = "delivered"):
    return verdict(
        domain_hint="purchase_cost",
        intent_hint="check_po_cost",
        entities=[entity(code, hint="product")],
        sub_asks=owner_sub_asks(basis),
        family=family,
    )


def pick(position: int) -> dict[str, Any]:
    """The parser's reading of a reply to an open numbered question."""
    return verdict(
        domain_hint=None,
        intent_hint=None,
        entities=[],
        open_question_answer={"mode": "pick", "picked": [position], "items": [], "qty_for_all": None},
    )


class Tools:
    """The MCP tools the per-ask runner may call, answering raw JSON (no `view=render`)."""

    def __init__(self, codes: dict[str, str], agents: int = 2) -> None:
        self.codes = codes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.agents = agents

    def names(self) -> list[str]:
        return [n for n, _a in self.calls]

    def args(self, name: str) -> list[dict[str, Any]]:
        return [a for n, a in self.calls if n == name]

    def __call__(self, client, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, args))
        ids = [i for i in _ids(args.get("product_ids")) if i in self.codes]
        if name == "crm_procurement_grn_summary":
            return json.dumps(
                {
                    "data": [
                        {
                            "product_id": pid,
                            "product_code": self.codes[pid],
                            "received_qty": 1200,
                            "grn_count": 3,
                            "last_receipt_date": "2026-09-18",
                            "last_receipt_qty": 300,
                        }
                        for pid in ids
                    ]
                }
            )
        if name == "crm_procurement_po_last_cost_list":
            return json.dumps(
                {
                    "data": [
                        {
                            "po_number": "PO-2026-0412",
                            "product_id": pid,
                            "product_code": self.codes[pid],
                            "po_quantity": 500,
                            "po_date": "2026-06-03",
                            "currency": "MYR",
                            "unit_cost": 50.0,
                            "discount_per_unit": 5.0,
                            "unit_cost_after_discount": 45.0,
                            "warehouse": "BRW",
                            "supplier": "ABC SDN BHD",
                        }
                        for pid in ids
                    ]
                }
            )
        if name == "crm_report_ask":
            if args.get("group_by"):
                rows = [
                    {"rank": i + 1, "name": f"AGENT{i + 1:02d}", "qty": 300 - i, "amount": 1000.0}
                    for i in range(self.agents)
                ]
            else:
                rows = []
            return json.dumps(
                {
                    "status": "ok",
                    "rows": rows,
                    "more": 0,
                    "total_count": len(rows),
                    "total": {"qty": 860, "amount": 9999.0},
                }
            )
        if name == "crm_inventory_stock_balance_list":
            if not args.get("include_sellable"):
                # The detailed policy mode (the default): paged rows, no summary.
                return json.dumps(
                    {"data": [{"product_id": pid, "quantity_on_hand": 60} for pid in ids], "stock_visibility": {"mode": "detailed"}}
                )
            # Like the real route: the summary covers only the rows of the page it answers,
            # 7 stock rows per product here, `limit` rows a page (default 50).
            covered = ids[: -(-int(args.get("limit") or 50) // 7)]
            return json.dumps(
                {
                    "data": [],
                    "stock_visibility": {"mode": "detailed", "warehouse_codes": None, "source": "contact"},
                    "stock_summary": [
                        {"product_id": pid, "product_code": self.codes[pid], "total_on_hand": 120, "locations": []}
                        for pid in covered
                    ],
                }
            )
        return json.dumps({"data": []})


def _ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        return [s for s in raw.replace("[", "").replace("]", "").replace('"', "").split(",") if s]
    return []


RUNNER_TOOLS = {
    "crm_procurement_grn_summary",
    "crm_procurement_po_last_cost_list",
    "crm_report_ask",
}


def _console(
    session_factory, monkeypatch, stub_access, phone: str, grants=ALL_GRANTS, agents: int = 2, staff: bool = True
):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)
    if staff:
        # Staff is `chatbot_profile.tier == "office"` (`turn_state.is_staff_profile`).
        from sqlalchemy import text as sql

        from tests.chatbot.test_engine import CONTACT_ID

        db = session_factory()
        db.execute(
            sql("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
            {"p": '{"tier": "office"}', "c": str(CONTACT_ID)},
        )
        db.commit()
        db.close()
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    stub_access(attributes=list(grants))
    tools = Tools(c.codes, agents=agents)
    from app.services.ai_assistant_service import MCPRuntimeClient

    # A function, not the instance: an instance set on the class is never bound.
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda client, name, args: tools(client, name, args))
    c.tools = tools
    return c


OWNER_REPLY = "\n".join(
    [
        "SRTWC286-SH",
        "2026 入库数量: 1,200",
        "供应商: ABC SDN BHD（PO-2026-0412，03/06/2026）",
        "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）",
        "最后一次进库: 18/09/2026（300）",
        "2026 总共卖出（已出货）: 860",
        "2026 每位业务员卖出（已出货）:",
        "1. AGENT01: 300",
        "2. AGENT02: 299",
    ]
)


# ---------------------------------------------------------------- Acceptance 1


def test_the_owner_turn_is_one_line_per_ask_in_asked_order(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009501")
    text = c.say(OWNER_MESSAGE, owner_verdict())
    assert text == OWNER_REPLY


def test_the_owner_turn_makes_one_query_per_ask_on_the_exact_code(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009502")
    c.say(OWNER_MESSAGE, owner_verdict())
    names = c.tools.names()
    # received + last receipt share one GRN call; supplier + last cost share one PO call.
    assert names.count("crm_procurement_grn_summary") == 1
    assert names.count("crm_procurement_po_last_cost_list") == 1
    assert names.count("crm_report_ask") == 2
    # Nothing unasked: no stock, no order report, no incoming.
    assert set(names) == RUNNER_TOOLS
    exact = [c.uuid_of["SRTWC286-SH"]]
    for name, args in c.tools.calls:
        assert _ids(args.get("product_ids")) == exact, (name, args)
    grn = c.tools.args("crm_procurement_grn_summary")[0]
    assert (grn.get("date_from"), grn.get("date_to")) == ("2026-01-01", "2026-12-31")
    total, by_agent = sorted(c.tools.args("crm_report_ask"), key=lambda a: bool(a.get("group_by")))
    for args in (total, by_agent):
        assert args.get("basis") == "delivered"
        assert args.get("measure") == "qty"
        assert (args.get("date_from"), args.get("date_to")) == ("2026-01-01", "2026-12-31")
        assert not args.get("product_code"), "product_code is a PREFIX filter, never sent"
    assert not total.get("group_by")
    assert by_agent.get("group_by") == "sales_agent"
    assert int(by_agent.get("top_n")) == 1000


def test_an_english_multi_ask_answers_in_english(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009503")
    text = c.say(
        "ELP3754 last cost and last received date",
        verdict(
            domain_hint="purchase_cost",
            intent_hint="check_po_cost",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("last_cost", "last cost"), sub("last_receipt", "last received date")],
            family=None,
        ),
    )
    assert text == "\n".join(
        [
            "ELP3754",
            "Last cost: RM 45.00 / unit (PO-2026-0412, 03/06/2026)",
            "Last received: 18/09/2026 (300)",
        ]
    )


# ---------------------------------------------------------------- Acceptance 2 (Q2)


def test_a_code_with_variants_and_no_scope_asks_back_first(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009504")
    text = c.say(OWNER_MESSAGE, owner_verdict(family=None))
    n = len(OWNER_FAMILY)
    assert text.splitlines()[0] == f"SRTWC286-SH 有 {n} 个款式。只要 SRTWC286-SH，还是全部 {n} 款？"
    assert not set(c.tools.names()) & RUNNER_TOOLS
    assert c.stored_question and c.stored_question.get("kind") == "sub_ask_scope"

    # "只要这个" -> option 1, the exact code: the six asks run.
    text = c.say("只要这个", pick(1))
    assert text == OWNER_REPLY


def test_all_variants_answers_one_summed_line_per_ask(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009505")
    c.say(OWNER_MESSAGE, owner_verdict(family=None))
    text = c.say("全部", pick(2))
    n = len(OWNER_FAMILY)
    lines = text.splitlines()
    assert lines[0] == f"SRTWC286-SH（全部 {n} 款）"
    # Every stub row is 1,200 received per product: the family is ONE summed line.
    assert lines[1] == f"2026 入库数量: {1200 * n:,}"
    family_ids = sorted(c.uuid_of[code] for code in OWNER_FAMILY)
    for name, args in c.tools.calls:
        if name in RUNNER_TOOLS:
            assert sorted(_ids(args.get("product_ids"))) == family_ids, name


def test_family_true_up_front_never_asks(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009506")
    text = c.say(OWNER_MESSAGE, owner_verdict(family=True))
    assert text.splitlines()[0] == f"SRTWC286-SH（全部 {len(OWNER_FAMILY)} 款）"


def test_a_code_with_no_variants_never_asks(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009507")
    text = c.say(OWNER_MESSAGE.replace("SRTWC286-SH", "ELP3754"), owner_verdict(code="ELP3754", family=None))
    assert text.splitlines()[0] == "ELP3754"
    assert "款式" not in text


# ---------------------------------------------------------------- Acceptance 3 (Q4)


def test_no_basis_answers_the_rest_and_asks_the_basis_last(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009508")
    text = c.say(OWNER_MESSAGE, owner_verdict(basis=None))
    assert text == "\n".join(
        [
            "SRTWC286-SH",
            "2026 入库数量: 1,200",
            "供应商: ABC SDN BHD（PO-2026-0412，03/06/2026）",
            "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）",
            "最后一次进库: 18/09/2026（300）",
            "卖出按已出货还是按下单算？",
        ]
    )
    assert "crm_report_ask" not in c.tools.names()
    assert c.stored_question and c.stored_question.get("kind") == "sub_ask_basis"

    text = c.say("已出货", pick(1))
    assert text == "\n".join(
        [
            "SRTWC286-SH",
            "2026 总共卖出（已出货）: 860",
            "2026 每位业务员卖出（已出货）:",
            "1. AGENT01: 300",
            "2. AGENT02: 299",
        ]
    )
    assert {a.get("basis") for a in c.tools.args("crm_report_ask")} == {"delivered"}


def test_ordered_is_option_two(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009509")
    c.say(OWNER_MESSAGE, owner_verdict(basis=None))
    text = c.say("下单", pick(2))
    assert "2026 总共卖出（下单）: 860" in text.splitlines()
    assert {a.get("basis") for a in c.tools.args("crm_report_ask")} == {"ordered"}


# ---------------------------------------------------------------- Acceptance 4 (R5)


def test_the_window_shows_only_on_lines_whose_query_used_it(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009510")
    text = c.say(OWNER_MESSAGE, owner_verdict())
    for line in text.splitlines():
        if line.startswith(("供应商", "采购价", "最后一次进库")):
            assert "2026 " not in line.split(":")[0], line
    assert "Order date" not in text and "订单日期" not in text


# ---------------------------------------------------------------- Acceptance 5 (grants)


def test_a_line_the_contact_may_not_see_says_so_and_the_rest_answer(session_factory, monkeypatch, stub_access):
    grants = [g for g in ALL_GRANTS if g != "purchase_orders.cost"]
    c = _console(session_factory, monkeypatch, stub_access, "+60000009511", grants=grants)
    text = c.say(OWNER_MESSAGE, owner_verdict())
    lines = text.splitlines()
    assert "采购价: 您没有查看权限" in lines
    assert "供应商: ABC SDN BHD（PO-2026-0412，03/06/2026）" in lines
    assert "45.00" not in text and "50.0" not in text


def test_no_sales_grant_refuses_only_the_sold_lines(session_factory, monkeypatch, stub_access):
    grants = [g for g in ALL_GRANTS if g != "sales_orders.sales_report"]
    c = _console(session_factory, monkeypatch, stub_access, "+60000009512", grants=grants)
    text = c.say(OWNER_MESSAGE, owner_verdict())
    lines = text.splitlines()
    assert "2026 总共卖出（已出货）: 您没有查看权限" in lines
    assert "2026 每位业务员卖出（已出货）: 您没有查看权限" in lines
    assert "crm_report_ask" not in c.tools.names()
    assert "2026 入库数量: 1,200" in lines


# ---------------------------------------------------------------- Acceptance 6 (never stuck)


def test_an_ask_with_no_query_says_not_available_yet(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009513")
    text = c.say(
        "ELP3754 margin and last cost",
        verdict(
            domain_hint="purchase_cost",
            intent_hint="check_po_cost",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("other", "margin"), sub("last_cost", "last cost")],
            family=None,
        ),
    )
    assert text.splitlines() == [
        "ELP3754",
        "margin: not available yet",
        "Last cost: RM 45.00 / unit (PO-2026-0412, 03/06/2026)",
    ]


def test_nothing_on_record_is_said_plainly(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009514")

    def empty(client, name, args):
        c.tools.calls.append((name, args))
        if name == "crm_report_ask":
            return json.dumps({"status": "ok", "rows": [], "more": 0, "total_count": 0, "total": {"qty": 0, "amount": 0}})
        return json.dumps({"data": []})

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", empty)
    text = c.say(OWNER_MESSAGE.replace("SRTWC286-SH", "ELP3754"), owner_verdict(code="ELP3754"))
    assert text.splitlines()[:6] == [
        "ELP3754",
        "2026 入库数量: 0",
        "供应商: 没有记录",
        "采购价: 没有记录",
        "最后一次进库: 没有记录",
        "2026 总共卖出（已出货）: 0",
    ]


# ---------------------------------------------------------------- Acceptance 7 (scope)


def test_a_single_sub_ask_keeps_todays_path(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009515")
    c.say(
        "ELP3754 last cost",
        verdict(
            domain_hint="purchase_cost",
            intent_hint="check_po_cost",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("last_cost", "last cost")],
            family=None,
        ),
    )
    assert "crm_procurement_grn_summary" not in c.tools.names()
    # Today's purchase_cost lane asks the tool with view=render; the runner never does.
    assert all(a.get("view") == "render" for a in c.tools.args("crm_procurement_po_last_cost_list"))


def test_a_dealer_keeps_todays_path(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009516")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
    c.say(OWNER_MESSAGE, owner_verdict())
    assert "crm_procurement_grn_summary" not in c.tools.names()


# ---------------------------------------------------------------- Acceptance 8 (Q5)


def test_a_long_list_prints_every_row_packed_into_the_fewest_messages(session_factory, monkeypatch, stub_access):
    from app.services.chatbot.engine import WHATSAPP_MESSAGE_MAX_CHARS, split_marked_message

    c = _console(session_factory, monkeypatch, stub_access, "+60000009517", agents=400)
    text = c.say(OWNER_MESSAGE, owner_verdict())
    parts = split_marked_message(text)
    assert len(parts) > 1
    body = "\n".join(parts)
    for i in range(1, 401):
        assert f"\n{i}. AGENT{i:02d}: " in body
    for k, part in enumerate(parts):
        assert len(part) <= WHATSAPP_MESSAGE_MAX_CHARS
        if k + 1 < len(parts):
            # The first row of the next part would not have fitted in this one.
            nxt = [ln for ln in parts[k + 1].splitlines()[1:] if ln.strip()][0]
            assert len(part) + 1 + len(nxt) > WHATSAPP_MESSAGE_MAX_CHARS - len("(9999/9999)\n")


def test_pack_parts_leaves_a_short_text_alone_and_marks_a_long_one():
    from app.services.chatbot.engine import pack_parts, split_marked_message

    assert pack_parts("a\nb") == "a\nb"
    long = "\n".join(f"{i}. AGENT{i:03d}: {i}" for i in range(1, 600))
    packed = pack_parts(long)
    parts = split_marked_message(packed)
    assert len(parts) > 1
    rows = [ln for p in parts for ln in p.splitlines() if ln and not ln.startswith("(")]
    assert rows == long.splitlines()


# ---------------------------------------------------------------- security review B1 / B2


def test_a_contact_refused_the_agent_gets_the_refusal_not_the_figures(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009518")
    stub_access(allowed=False, decision="deny", attributes=list(ALL_GRANTS))
    text = c.say(OWNER_MESSAGE, owner_verdict())
    assert not set(c.tools.names()) & RUNNER_TOOLS
    assert "1,200" not in text and "ABC SDN BHD" not in text and "AGENT01" not in text


def test_every_runner_call_names_the_contact_and_the_space(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009519")
    c.say(OWNER_MESSAGE, owner_verdict())
    calls = [(n, a) for n, a in c.tools.calls if n in RUNNER_TOOLS]
    assert calls
    for name, args in calls:
        assert args.get("contact_id"), (name, args)
        assert args.get("space_id"), (name, args)


# ---------------------------------------------------------------- review round 1


def test_the_stock_line_reads_the_policy_total_on_the_detailed_mode(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009520")
    text = c.say(
        "ELP3754 stock and last cost",
        verdict(
            domain_hint="inventory",
            intent_hint="check_stock",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("stock", "stock"), sub("last_cost", "last cost")],
            family=None,
        ),
    )
    assert text.splitlines()[:2] == ["ELP3754", "Stock: 120"]
    [args] = c.tools.args("crm_inventory_stock_balance_list")
    assert args.get("include_sellable") is True
    assert "sellable" not in text.lower()


def test_a_customer_filter_is_todays_path_never_an_all_customer_total(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009521")
    c.say(
        "ELP3754 2026 sold to ABC Trading and last cost",
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("ELP3754", hint="product"), entity("ABC Trading", hint="customer")],
            sub_asks=[sub("sold_total", "sold", window=Y2026, basis="delivered"), sub("last_cost", "last cost")],
            family=None,
        ),
    )
    assert "crm_procurement_grn_summary" not in c.tools.names()
    assert not any(a.get("view") != "render" for a in c.tools.args("crm_report_ask"))


def test_a_non_staff_contact_keeps_todays_path(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009522", staff=False)
    c.say(OWNER_MESSAGE, owner_verdict())
    assert "crm_procurement_grn_summary" not in c.tools.names()


def test_the_incoming_window_is_shown_when_it_was_applied(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009523")
    text = c.say(
        "ELP3754 2026 incoming and last cost",
        verdict(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("incoming", "incoming", window=Y2026), sub("last_cost", "last cost")],
            family=None,
        ),
    )
    assert text.splitlines()[1].startswith("Incoming 2026: ")
    [args] = c.tools.args("crm_incoming_stock_by_product")
    assert (args.get("eta_from"), args.get("eta_to")) == ("2026-01-01", "2026-12-31")


def test_the_scope_question_remembers_a_basis_turn_one_already_said(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009524")
    v = owner_verdict(family=None, basis=None)
    v["basis"] = "delivered"  # said once for the message, not per ask
    c.say(OWNER_MESSAGE, v)
    assert c.stored_question.get("kind") == "sub_ask_scope"
    text = c.say("只要这个", pick(1))
    assert "卖出按已出货还是按下单算？" not in text
    assert "2026 总共卖出（已出货）: 860" in text.splitlines()


def test_all_variants_counts_the_whole_family_past_the_resolver_cap(session_factory):
    """The resolver's prefix probe stops at PREFIX_LIMIT (20); the family count does not."""
    from app.services.chatbot.engine import _product_family
    from app.services.entity_resolver import PREFIX_LIMIT

    from tests.chatbot._r9_engine_console import _seed_products

    codes = [f"ZZTFAMX-{i:02d}" for i in range(PREFIX_LIMIT + 5)]
    _seed_products(session_factory, codes)
    db = session_factory()
    try:
        from app.models.base import set_company_scope
        from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

        set_company_scope(db, frozenset({SORENTO}))
        family = _product_family(db, "zztfamx-")
    finally:
        db.close()
    assert [f["code"] for f in family] == codes


# ---------------------------------------------------------------- review round 2


def test_a_family_stock_line_counts_every_variant_past_one_page(session_factory, monkeypatch, stub_access):
    """Ten variants at 7 stock rows each are 70 rows: past the route's default page of 50."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000009525")
    text = c.say(
        "SRTWC286-SH all variants stock and last cost",
        verdict(
            domain_hint="inventory",
            intent_hint="check_stock",
            entities=[entity("SRTWC286-SH", hint="product")],
            sub_asks=[sub("stock", "stock"), sub("last_cost", "last cost")],
            family=True,
        ),
    )
    assert text.splitlines()[1] == f"Stock: {120 * len(OWNER_FAMILY):,}"
    [args] = c.tools.args("crm_inventory_stock_balance_list")
    assert int(args.get("limit")) == 5000


def test_a_family_past_the_ceiling_is_todays_path(session_factory, monkeypatch, stub_access):
    from app.services.chatbot.turn import sub_asks as sub_asks_mod

    monkeypatch.setattr(sub_asks_mod, "FAMILY_MAX", 3)
    c = _console(session_factory, monkeypatch, stub_access, "+60000009526")
    c.say(OWNER_MESSAGE, owner_verdict(family=True))
    assert "crm_procurement_grn_summary" not in c.tools.names()


def test_a_code_with_an_underscore_is_matched_as_typed(session_factory):
    from app.services.chatbot.engine import _product_family

    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTUS_A-1", "ZZTUSXA-1"])
    db = session_factory()
    try:
        from app.models.base import set_company_scope
        from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

        set_company_scope(db, frozenset({SORENTO}))
        assert [f["code"] for f in _product_family(db, "ZZTUS_A")] == ["ZZTUS_A-1"]
    finally:
        db.close()
