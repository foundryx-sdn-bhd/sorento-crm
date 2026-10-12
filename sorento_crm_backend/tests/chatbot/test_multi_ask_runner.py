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
        if name == SPO_TOOL:
            return json.dumps(
                {
                    "data": [
                        {
                            "spo_number": "SPO-2026-0101",
                            "product_id": pid,
                            "product_code": self.codes[pid],
                            "spo_quantity": 300,
                            "gr_quantity": 300,
                            "spo_date": "2026-09-18",
                            "spo_date_source": "expected",
                            "gr_date": "2026-09-20",
                            "warehouse": "BRW",
                        }
                        for pid in ids
                    ],
                    "pagination": {"total": len(ids), "page": 1, "limit": len(ids)},
                    "empty": not ids,
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


# Owner ruling 9 Oct 2026: "last in" is the last SPO line's delivery date everywhere.
SPO_TOOL = "crm_procurement_spo_allocations_last_receipt_list"

RUNNER_TOOLS = {
    "crm_procurement_grn_summary",
    SPO_TOOL,
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
        "最后一次进库: 18/09/2026（300，SPO-2026-0101）",
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
    # received_qty stays on GRN (one call); last receipt is the SPO tool (one call, owner
    # ruling 9 Oct); supplier + last cost share one PO call.
    assert names.count("crm_procurement_grn_summary") == 1
    assert names.count(SPO_TOOL) == 1
    assert names.count("crm_procurement_po_last_cost_list") == 1
    # The total and the per-salesman list share ONE grouped call: its `total` is the whole
    # set, and the report is rate limited per contact (crew live check, 9 Oct).
    assert names.count("crm_report_ask") == 1
    # Nothing unasked: no stock, no order report, no incoming.
    assert set(names) == RUNNER_TOOLS
    exact = [c.uuid_of["SRTWC286-SH"]]
    for name, args in c.tools.calls:
        assert _ids(args.get("product_ids")) == exact, (name, args)
    grn = c.tools.args("crm_procurement_grn_summary")[0]
    assert (grn.get("date_from"), grn.get("date_to")) == ("2026-01-01", "2026-12-31")
    [spo] = c.tools.args(SPO_TOOL)
    assert int(spo.get("top_n")) == 1
    [by_agent] = c.tools.args("crm_report_ask")
    assert by_agent.get("basis") == "delivered"
    assert by_agent.get("measure") == "qty"
    assert (by_agent.get("date_from"), by_agent.get("date_to")) == ("2026-01-01", "2026-12-31")
    assert not by_agent.get("product_code"), "product_code is a PREFIX filter, never sent"
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
            "Last in: 18/09/2026 (300, SPO-2026-0101)",
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
            "最后一次进库: 18/09/2026（300，SPO-2026-0101）",
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


def _family(session_factory, code: str, *, manual: dict[str, str] | None = None) -> list[str]:
    """`engine._product_family(code)` codes, after any hand-set `family_code` in `manual`."""
    from app.models.base import set_company_scope
    from app.models.product import Product
    from app.services.chatbot.engine import _product_family

    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

    db = session_factory()
    try:
        set_company_scope(db, frozenset({SORENTO}))
        for pcode, family_code in (manual or {}).items():
            row = db.query(Product).filter(Product.product_code == pcode).one()
            row.family_code, row.family_code_manual = family_code, True
        db.flush()
        return [f["code"] for f in _product_family(db, code)]
    finally:
        db.rollback()
        db.close()


def test_all_variants_counts_the_whole_family_past_the_resolver_cap(session_factory):
    """The resolver's prefix probe stops at PREFIX_LIMIT (20); the family count does not."""
    from app.services.entity_resolver import PREFIX_LIMIT

    from tests.chatbot._r9_engine_console import _seed_products

    codes = [f"ZZTFAM9-{i:02d}" for i in range(PREFIX_LIMIT + 5)]
    _seed_products(session_factory, codes)
    assert _family(session_factory, "zztfam9") == codes


def test_the_family_is_products_family_code_never_a_code_prefix(session_factory):
    """MULTI-ASK-PLAN2 (CODE-VARIANTS #1573): ZZTFQ123 starts with ZZTFQ12 but is its own model."""
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTFQ12", "ZZTFQ12-A", "ZZTFQ12-B", "ZZTFQ123"])
    assert _family(session_factory, "ZZTFQ12") == ["ZZTFQ12", "ZZTFQ12-A", "ZZTFQ12-B"]
    assert _family(session_factory, "zztfq12-a") == ["ZZTFQ12", "ZZTFQ12-A", "ZZTFQ12-B"]


def test_a_hand_set_family_code_wins_over_the_code(session_factory):
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTFM55", "ZZTFM77"])
    manual = {"ZZTFM55": "ZZTFMGRP1", "ZZTFM77": "ZZTFMGRP1"}
    assert _family(session_factory, "ZZTFM55", manual=manual) == ["ZZTFM55", "ZZTFM77"]


def test_a_missing_dashed_code_is_no_family_never_its_siblings(session_factory):
    """Review B1: ZZTQW6-ZZ does not exist; ZZTQW6-A is never answered in its place."""
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTQW6-A", "ZZTQW6-B"])
    assert _family(session_factory, "ZZTQW6-ZZ") == []
    assert _family(session_factory, "ZZTQW6") == ["ZZTQW6-A", "ZZTQW6-B"]


def test_a_typed_variant_code_has_its_siblings_as_family(session_factory):
    """Review S1, pinned: a typed variant that exists is in its model family (CODE-VARIANTS),
    so a 2+ ask turn with `family` null asks the scope question for it."""
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTQX7", "ZZTQX7-GM", "ZZTQX7-SS"])
    assert _family(session_factory, "ZZTQX7-GM") == ["ZZTQX7", "ZZTQX7-GM", "ZZTQX7-SS"]


def test_a_code_with_no_family_is_no_family(session_factory):
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTNODIGIT-A"])
    assert _family(session_factory, "ZZTNODIGIT") == []


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
    from tests.chatbot._r9_engine_console import _seed_products

    _seed_products(session_factory, ["ZZTU_9-1", "ZZTUX9-1"])
    assert _family(session_factory, "ZZTU_9") == ["ZZTU_9-1"]


# ---------------------------------------------------------------- crew live checks, 9 Oct


def _reply(**answer) -> dict:
    base = {"mode": None, "picked": [], "items": [], "qty_for_all": None}
    base.update(answer)
    return verdict(domain_hint=None, intent_hint=None, entities=[], open_question_answer=base)


def test_the_questions_offer_their_meaning_as_the_option_code(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009527")
    c.say(OWNER_MESSAGE, owner_verdict(family=None))
    codes = [o.get("code") for o in c.stored_question.get("options") or []]
    assert codes == ["just this code", "all variants"]


def test_the_basis_question_offers_delivered_and_ordered(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009528")
    c.say(OWNER_MESSAGE, owner_verdict(basis=None))
    assert [o.get("code") for o in c.stored_question.get("options") or []] == ["delivered", "ordered"]


def test_a_basis_reply_the_parser_reads_as_an_option_item_resumes(session_factory, monkeypatch, stub_access):
    """Live check 3: "已出货" came back naming the option, not as a pick."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000009529")
    c.say(OWNER_MESSAGE, owner_verdict(basis=None))
    text = c.say("已出货", _reply(mode="fill", items=[{"position": None, "code": "delivered", "qty": None}]))
    assert "2026 总共卖出（已出货）: 860" in text.splitlines()
    assert c.stored_question is None  # answered, so closed (review F1)


def test_all_to_the_variant_question_is_the_family(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009530")
    c.say(OWNER_MESSAGE, owner_verdict(family=None))
    text = c.say("全部", _reply(mode="all"))
    assert text.splitlines()[0] == f"SRTWC286-SH（全部 {len(OWNER_FAMILY)} 款）"
    assert c.stored_question is None  # answered, so closed (review F1)


def test_a_cancel_that_echoes_an_option_never_resumes(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009532")
    c.say(OWNER_MESSAGE, owner_verdict(basis=None))
    c.tools.calls.clear()
    c.say("cancel", _reply(mode="cancel", items=[{"position": 1, "code": "delivered", "qty": None}]))
    assert "crm_report_ask" not in c.tools.names()


def test_after_just_this_one_the_focus_is_the_exact_code(session_factory, monkeypatch, stub_access):
    """Live check 6: the focus still listed every variant after "只要这个"."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000009531")
    c.say(OWNER_MESSAGE, owner_verdict(family=None))
    c.say("只要这个", pick(1))
    focus = (c.state or {}).get("focus") or {}
    codes = [p.get("canonical_code") or p.get("code") for p in focus.get("products") or []]
    assert codes == ["SRTWC286-SH"], focus



# ---------------------------------------------------------------- crew live checks round 2


def test_a_sold_total_alone_is_one_ungrouped_call(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009533")
    text = c.say(
        "ELP3754 2026 sold and last cost",
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("sold_total", "sold", window=Y2026, basis="delivered"), sub("last_cost", "last cost")],
            family=None,
        ),
    )
    [args] = c.tools.args("crm_report_ask")
    assert not args.get("group_by")
    assert "Sold 2026 (delivered): 860" in text.splitlines()


def test_a_busy_report_says_so_on_its_own_line_and_traces_why(session_factory, monkeypatch, stub_access):
    """`crm_report_ask` is rate limited per contact; a busy answer is named, never the
    generic could-not-check, and the trace records which line and why."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000009534")
    real = c.tools.__call__

    def busy(client, name, args):
        if name == "crm_report_ask":
            c.tools.calls.append((name, args))
            return json.dumps({"status": "busy", "message": "Too many reports in the last 10 minutes.", "rows": [], "total": {"qty": 0, "amount": 0}})
        return real(client, name, args)

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", busy)
    text = c.say(OWNER_MESSAGE, owner_verdict())
    lines = text.splitlines()
    assert "2026 总共卖出（已出货）: 销售报表查询太频繁，请几分钟后再试" in lines
    assert "2026 每位业务员卖出（已出货）: 销售报表查询太频繁，请几分钟后再试" in lines
    assert "2026 入库数量: 1,200" in lines


# ---------------------------------------------------------------- owner ruling 9 Oct: "last in" is the SPO date


def _last_in(code: str = "ELP3754"):
    return verdict(
        domain_hint="purchase_cost",
        intent_hint="check_po_cost",
        entities=[entity(code, hint="product")],
        sub_asks=[sub("last_cost", "last cost"), sub("last_receipt", "last in")],
        family=None,
    )


def test_last_receipt_reads_the_spo_tool_once_on_the_exact_code(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009540")
    text = c.say("ELP3754 last cost and last in", _last_in())
    assert "crm_procurement_grn_summary" not in c.tools.names()
    assert c.tools.names().count(SPO_TOOL) == 1
    [args] = c.tools.args(SPO_TOOL)
    assert _ids(args.get("product_ids")) == [c.uuid_of["ELP3754"]]
    assert int(args.get("top_n")) == 1
    assert args.get("contact_id") and args.get("space_id")
    assert text.splitlines()[-1] == "Last in: 18/09/2026 (300, SPO-2026-0101)"


def test_last_receipt_line_in_chinese_and_malay(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009541")
    zh = c.say("ELP3754 采购价和最后一次进库", _last_in())
    assert zh.splitlines()[-1] == "最后一次进库: 18/09/2026（300，SPO-2026-0101）"


def test_the_last_in_label_is_catalogued_in_malay_and_chinese():
    from app.services.chatbot.label_catalog import LABELS

    assert LABELS["Last in"] == {"ms": "Terakhir masuk", "zh": "最后一次进库"}
    assert "Last received" not in LABELS


def test_a_family_last_receipt_is_the_single_newest_spo_line(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009543")
    base = c.tools.__call__
    family_ids = [c.uuid_of[code] for code in OWNER_FAMILY]
    newest, tied = family_ids[3], family_ids[5]

    def spo(client, name, args):
        if name != SPO_TOOL:
            return base(client, name, args)
        c.tools.calls.append((name, args))
        dates = {pid: "2026-08-01" for pid in family_ids}
        dates[newest] = "2026-09-30"
        dates[tied] = "2026-09-30"
        rows = [
            {
                "spo_number": f"SPO-{i:04d}",
                "product_id": pid,
                "product_code": c.codes[pid],
                "spo_quantity": 10 * (i + 1),
                "spo_date": dates[pid],
                "spo_date_source": "expected",
                "warehouse": "BRW",
            }
            for i, pid in enumerate(family_ids)
            if pid in _ids(args.get("product_ids"))
        ]
        return json.dumps({"data": rows, "empty": not rows})

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", spo)
    text = c.say(
        "SRTWC286-SH all variants last cost and last in",
        verdict(
            domain_hint="purchase_cost",
            intent_hint="check_po_cost",
            entities=[entity("SRTWC286-SH", hint="product")],
            sub_asks=[sub("last_cost", "last cost"), sub("last_receipt", "last in")],
            family=True,
        ),
    )
    # One row, not a sum of the two members sharing the newest date; a tie takes the first.
    assert "Last in: 30/09/2026 (40, SPO-0003)" in text.splitlines()
    assert sorted(_ids(c.tools.args(SPO_TOOL)[0].get("product_ids"))) == sorted(family_ids)


def test_received_qty_and_last_receipt_each_use_their_own_tool(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009544")
    text = c.say(
        "ELP3754 2026 received and last in",
        verdict(
            domain_hint="purchase_cost",
            intent_hint="check_po_cost",
            entities=[entity("ELP3754", hint="product")],
            sub_asks=[sub("received_qty", "received", window=Y2026), sub("last_receipt", "last in")],
            family=None,
        ),
    )
    names = c.tools.names()
    assert names.count("crm_procurement_grn_summary") == 1
    assert names.count(SPO_TOOL) == 1
    grn = c.tools.args("crm_procurement_grn_summary")[0]
    assert (grn.get("date_from"), grn.get("date_to")) == ("2026-01-01", "2026-12-31")
    assert text.splitlines() == ["ELP3754", "Received 2026: 1,200", "Last in: 18/09/2026 (300, SPO-2026-0101)"]


def test_no_spo_line_says_none_on_record(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009545")
    base = c.tools.__call__

    def none(client, name, args):
        if name == SPO_TOOL:
            c.tools.calls.append((name, args))
            return json.dumps({"data": [], "empty": True})
        return base(client, name, args)

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", none)
    en = c.say("ELP3754 last cost and last in", _last_in())
    assert en.splitlines()[-1] == "Last in: none on record"
    zh = c.say("ELP3754 采购价和最后一次进库", _last_in())
    assert zh.splitlines()[-1] == "最后一次进库: 没有记录"


def test_last_receipt_without_the_placed_grant_is_refused_and_never_queried(session_factory, monkeypatch, stub_access):
    grants = [g for g in ALL_GRANTS if g != "purchase_orders.placed"]
    c = _console(session_factory, monkeypatch, stub_access, "+60000009546", grants=grants)
    text = c.say("ELP3754 last cost and last in", _last_in())
    assert SPO_TOOL not in c.tools.names()
    assert "SPO-2026-0101" not in text
    assert text.splitlines()[-1] == "Last in: not shared with you"


# ---- lone "last in" (parser domain spo_allocation, one ask): the empty reply


def _lone(code: str = "ELP3754", sub_asks=None):
    return verdict(
        domain_hint="spo_allocation",
        intent_hint="check_spo",
        entities=[entity(code, hint="product")],
        sub_asks=sub_asks,
        family=None,
    )


@pytest.mark.parametrize("sub_asks", [None, [sub("last_receipt", "last in")]])
def test_a_lone_last_in_with_no_spo_line_says_none_on_record(session_factory, monkeypatch, stub_access, sub_asks):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009547" if sub_asks is None else "+60000009548")

    def empty(client, name, args):
        c.tools.calls.append((name, args))
        return json.dumps({"data": [], "pagination": {"total": 0, "page": 1, "limit": 0}, "empty": True})

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", empty)
    en = c.say("ELP3754 last in", _lone(sub_asks=sub_asks))
    assert "matched these" not in en and "Here's what you want" not in en
    assert en.splitlines()[-1] == "Last in: none on record", en
    zh = c.say("ELP3754 最后一次进库", _lone(sub_asks=sub_asks))
    assert "matched these" not in zh
    assert zh.splitlines()[-1] == "最后一次进库: 没有记录", zh


# ---- parser prompt


def test_the_parser_prompt_teaches_last_in_date_and_quantity_is_one_ask():
    from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

    assert "The date and quantity of the last receipt are ONE last_receipt ask." in " ".join(SUB_ASKS_ADDENDUM.split())


def test_the_last_receipt_measure_is_the_spo_delivery_date_not_grn():
    from app.services.chatbot_parser_prompt import SUB_ASKS_ADDENDUM

    line = next(ln for ln in SUB_ASKS_ADDENDUM.splitlines() if '"last_receipt"' in ln)
    text = " ".join(SUB_ASKS_ADDENDUM[SUB_ASKS_ADDENDUM.index(line) :].split("\n")[:3]).lower()
    assert "spo" in text and "delivery date" in text
    assert "grn" not in text


# ---------------------------------------------------------------- R-LASTIN round 2 (reviewer)


def _empty_spo(c, monkeypatch):
    def empty(client, name, args):
        c.tools.calls.append((name, args))
        return json.dumps({"data": [], "pagination": {"total": 0, "page": 1, "limit": 0}, "empty": True})

    from app.services.ai_assistant_service import MCPRuntimeClient

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", empty)


def _bar_escalation(session_factory):
    from sqlalchemy import text as sql

    from tests.chatbot.test_engine import CONTACT_ID

    db = session_factory()
    db.execute(
        sql("UPDATE respond_contacts SET escalation_allowed = false WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    )
    db.commit()
    db.close()


def test_a_dealer_lone_last_in_none_keeps_the_escalate_offer(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009550", staff=False)
    _empty_spo(c, monkeypatch)
    text = c.say("ELP3754 last in", _lone())
    # Own-line shape (round 3): the offer is its own line after the scope lines, never glued
    # to the bare line with a space.
    lines = text.splitlines()
    assert lines[0] == "Last in: none on record", text
    assert lines[-1].startswith("Would you like me to escalate to ") and lines[-1].endswith(" team?"), text
    assert "matched these" not in text


def test_a_barred_contact_lone_last_in_none_ends_with_the_refer_sentence(session_factory, monkeypatch, stub_access):
    from app.services.chatbot.turn import refer

    c = _console(session_factory, monkeypatch, stub_access, "+60000009551", staff=False)
    _bar_escalation(session_factory)
    _empty_spo(c, monkeypatch)
    text = c.say("ELP3754 last in", _lone())
    assert text.startswith("Last in: none on record"), text
    assert text.endswith(refer.sentence()), text
    assert "escalate" not in text.lower()


def test_a_warehouse_scoped_last_in_none_keeps_its_scope(session_factory, monkeypatch, stub_access):
    from sqlalchemy import text as sql

    from app.services.company_scope import DEFAULT_COMPANY_ID

    c = _console(session_factory, monkeypatch, stub_access, "+60000009552")
    db = session_factory()
    db.execute(
        sql(
            "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active, company_id) "
            "VALUES (gen_random_uuid(), 'ZZTKL', 'ZZT KL', true, :co)"
        ),
        {"co": str(DEFAULT_COMPANY_ID)},
    )
    db.commit()
    db.close()
    _empty_spo(c, monkeypatch)
    v = verdict(
        domain_hint="spo_allocation",
        intent_hint="check_spo",
        entities=[entity("ELP3754", hint="product"), entity("ZZTKL", hint="warehouse")],
        sub_asks=None,
        family=None,
    )
    text = c.say("ELP3754 last in at ZZTKL", v)
    assert [a for a in c.tools.args(SPO_TOOL) if a.get("warehouse_ids")], "the warehouse filter must reach the tool"
    assert text != "Last in: none on record", "the warehouse scope was dropped"
    assert "ZZTKL" in text, text


def test_an_unresolved_second_code_is_still_named_beside_last_in_none(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009553")
    _empty_spo(c, monkeypatch)
    v = verdict(
        domain_hint="spo_allocation",
        intent_hint="check_spo",
        entities=[entity("ELP3754", hint="product"), entity("FOO999", hint="product")],
        sub_asks=None,
        family=None,
    )
    text = c.say("last in ELP3754 and FOO999", v)
    assert "Couldn't find: FOO999" in text, text


# (c) multi-company " - checked in A, B" is not covered: it needs the two-company seeded chain of
# test_engine_company_scope.py, too costly for this harness; the coder keeps build_breakdown_msg for it.


# ---------------------------------------------------------------- R-LASTIN round 3 (reviewer)


def test_a_zh_dealer_lone_last_in_none_is_all_chinese(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009554", staff=False)
    _empty_spo(c, monkeypatch)
    text = c.say("ELP3754 最后一次进库", _lone())
    assert "最后一次进库: 没有记录" in text.splitlines(), text
    assert "需要我转交给" in text and "团队吗" in text, text
    assert "Would you like" not in text and "none on record" not in text, text


def test_an_unresolved_second_warehouse_is_not_listed_as_a_warehouse(session_factory, monkeypatch, stub_access):
    from sqlalchemy import text as sql

    from app.services.company_scope import DEFAULT_COMPANY_ID

    c = _console(session_factory, monkeypatch, stub_access, "+60000009555")
    db = session_factory()
    db.execute(
        sql(
            "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active, company_id) "
            "VALUES (gen_random_uuid(), 'ZZTKL', 'ZZT KL', true, :co)"
        ),
        {"co": str(DEFAULT_COMPANY_ID)},
    )
    db.commit()
    db.close()
    _empty_spo(c, monkeypatch)
    v = verdict(
        domain_hint="spo_allocation",
        intent_hint="check_spo",
        entities=[
            entity("ELP3754", hint="product"),
            entity("ZZTKL", hint="warehouse"),
            entity("ZZTXX", hint="warehouse"),
        ],
        sub_asks=None,
        family=None,
    )
    text = c.say("ELP3754 last in at ZZTKL and ZZTXX", v)
    lines = text.splitlines()
    assert "Warehouse: ZZTKL" in lines, text
    assert any(ln.startswith("Couldn't find:") and "ZZTXX" in ln for ln in lines), text
    assert [ln for ln in lines if "ZZTXX" in ln] == [ln for ln in lines if ln.startswith("Couldn't find:")], text


def test_a_multi_company_lone_last_in_miss_keeps_the_bare_line_and_the_companies(session_factory):
    """Unit level (an engine harness needs the two-company chain): the generic miss builder with
    a company-scope suffix must not say `matched these` for a last-in ask."""
    from app.services.chatbot.lanes.business.answer import not_found_error_message
    from app.services.chatbot.lanes.business.gate import run_gate

    a, b = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    matches = [
        {"uuid": a, "entity_type": "product", "canonical_code": "ELP3754", "company_name": "Sorento"},
        {"uuid": b, "entity_type": "product", "canonical_code": "ELP3754", "company_name": "Mocha"},
    ]
    resolver = {"resolutions": [{"token": "ELP3754", "resolved": True, "matches": [m]} for m in matches]}
    parser = {
        "domain_hint": "spo_allocation",
        "entities": [{"raw": "ELP3754", "hint": "product"}],
        "routing": {"suggested_team": "procurement"},
        "access_levels": [],
    }
    gate = run_gate(dict(resolver), parser=parser, resolver=resolver)
    resolved = {
        "by_entity_type": {"product": matches},
        "tokens": ["ELP3754"],
        "unresolved_tokens": [],
        "resolutions": resolver["resolutions"],
    }
    out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
    message = out.get("escalate_message") or ""
    assert "Sorento" in message and "Mocha" in message, message
    assert "matched these" not in message, message
    assert "Last in: none on record" in message, message
