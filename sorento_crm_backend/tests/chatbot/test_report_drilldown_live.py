"""REPORT-DRILLDOWN live pass: the REAL parser (no parser double) on this branch's prompt, two
turns each, through the real console turn, engine, lane, report ask and MCP presenter.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`REPORT_DRILL_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_order_broaden_live_parser.py`. The prompt is the constant plus the seeded policy blocks,
exactly as `test_parser_prompt_budget._rendered_production_prompt` renders it.

Data is seeded here (synthetic, nothing real), with the customer CARD agent set so the scoped
case returns rows. The MCP transport runs `reports.ask.run_ask` in process (the route's own
filters and echo, without its access gates, which `tests/test_report_drilldown_route.py`
covers). Every turn prints the reply so the run's trace can be pasted on the PR.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import date
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_outstanding_lane import AnswerServices, FetchServices, _present_response, _seed_contact
from tests.chatbot.test_top_selling_round6 import _seed_borrowable_envelope

pytestmark = pytest.mark.live_external

TOOL = "crm_report_ask"
TODAY = "Thursday, 08 October 2026"
PRODUCT = "SRTWT7408"
OTHER = "SRTBS2210"


def _seed(db) -> dict[str, Any]:
    from app.models.sales_agent import SalesAgent
    from tests._mc_lookup_seed import customer, product
    from tests._report_ask_so_seed import seed_so_line

    agents = {}
    for code in ("ALICE TAN", "BENNY LIM", "CHONG WEI", "DAVID OOI"):
        row = SalesAgent(id=str(uuid.uuid4()), sales_agent=code, company_id=DEFAULT_COMPANY_ID)
        db.add(row)
        agents[code] = row
    db.flush()
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=PRODUCT)
    q = product(db, company_id=DEFAULT_COMPANY_ID, code=OTHER)
    # customer -> card agent; sales (agent on the SO, product, qty, RM)
    plan = {
        "MEGAH TILES SDN BHD": ("ALICE TAN", [("ALICE TAN", p, 40, 8000), ("BENNY LIM", q, 5, 900)]),
        "SINAR BATH SDN BHD": ("ALICE TAN", [("ALICE TAN", p, 20, 4000)]),
        "KLANG HOME CENTRE": ("BENNY LIM", [("BENNY LIM", p, 30, 6000), ("BENNY LIM", q, 10, 1800)]),
        "PERAK SANITARY TRADING": ("BENNY LIM", [("ALICE TAN", p, 10, 2000)]),
        "JOHOR BUILDERS SUPPLY": ("CHONG WEI", [("CHONG WEI", p, 25, 5000)]),
        "PENANG CERAMIC HOUSE": ("DAVID OOI", [("DAVID OOI", p, 2, 400), ("DAVID OOI", q, 50, 9000)]),
    }
    for name, (card, sales) in plan.items():
        cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        cust.sales_agent_id = agents[card].id
        db.flush()
        for agent, prod, qty, total in sales:
            seed_so_line(db, cust=cust, prod=prod, qty=qty, total=f"{total}.00", agent=agents[agent],
                         order_date=date(2026, 9, 10))
    db.commit()
    return {"agents": {k: v.id for k, v in agents.items()}}


def _open_policy():
    from app.services.stock_visibility import DEFAULT_MODE, SOURCE_DEFAULT, Policy

    return Policy(mode=DEFAULT_MODE, warehouse_ids=None, source=SOURCE_DEFAULT)


def _body(db, args: dict[str, Any]) -> dict[str, Any]:
    """The route's body for the lane's args (`order_management/report_ask.report_ask`, gates aside)."""
    from app.api.v1.order_management import report_ask as route
    from app.models.base import company_scope
    from app.services.reports import ask
    from app.services.sales_report_service import _resolve_products

    def ids(key: str) -> list[str]:
        value = args.get(key)
        values = value if isinstance(value, list) else [v for v in str(value or "").split(",") if v]
        return [str(v) for v in values]

    grants = {DEFAULT_COMPANY_ID}
    filters: dict[str, list[str]] = {}
    code = (args.get("product_code") or "").strip()
    if code:
        with company_scope(db, frozenset(grants)):
            filters["product"] = [str(p.id) for p in _resolve_products(db, code)]
    for key, word in (("customer_ids", "customer"), ("product_ids", "product"), ("brand_ids", "brand"),
                      ("category_ids", "category"), ("sales_agent_ids", "sales_agent"),
                      ("customer_agent_ids", "customer_agent")):
        if ids(key):
            filters[word] = ids(key)
    if args.get("channel"):
        filters["channel"] = [args["channel"]]
    basis, group_by = args.get("basis") or "delivered", args.get("group_by")
    data = ask.run_ask(
        db, basis=basis, measure=args.get("measure") or "amount", group_by=group_by,
        date_from=date.fromisoformat(args["date_from"]), date_to=date.fromisoformat(args["date_to"]),
        # A contact with no location limit (`policy=None` is "nobody resolved": nothing, fail closed).
        filters=filters, warehouse_codes=ids("warehouse_codes"), policy=_open_policy(), company_grants=grants,
        sort=args.get("sort") or "desc", top_n=int(args["top_n"]) if args.get("top_n") else None,
    )
    echo = route._echo(
        db, grants, product_code=code, product_names=[], customers=ids("customer_ids") or None,
        brands=ids("brand_ids") or None, categories=ids("category_ids") or None,
        agents=ids("sales_agent_ids") or None, warehouse_codes=ids("warehouse_codes"),
        channel=args.get("channel"), card_agents=ids("customer_agent_ids") or None,
    )
    return {
        **data, "basis": basis, "basis_label": ask.CATALOGUE[basis]["label"],
        "measure": args.get("measure") or "amount", "sort": args.get("sort") or "desc",
        "group_by": group_by, "group_label": ask.GROUP_LABELS[group_by] if group_by else None,
        "date_from": args["date_from"], "date_to": args["date_to"], "filters": echo,
    }


class LiveConsole:
    def __init__(self, session_factory, monkeypatch) -> None:
        self.session_factory, self.monkeypatch = session_factory, monkeypatch
        self.session_vars: dict[str, Any] = {}
        self.log: list[str] = []

    def say(self, body: str) -> tuple[str, list[dict[str, Any]]]:
        from app.services.chatbot import console_service

        captured: list[dict[str, Any]] = []
        present = _present_response()

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            if name != TOOL:
                return json.dumps({"has_result": False, "items": []})
            captured.append(dict(args))
            db = self.session_factory()
            try:
                return present(name, json.dumps(_body(db, args)))
            finally:
                db.close()

        self.monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp))
        self.monkeypatch.setattr(
            engine_mod.business_services, "answer_services_for",
            lambda session_factory: AnswerServices(mcp_probe=lambda n, a: {"data": []}, family_fetch=lambda q: {"data": []}),
        )
        db = self.session_factory()
        try:
            result = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text=body,
                session_vars=copy.deepcopy(self.session_vars), run_id="zzt-report-drill-live",
            )
        finally:
            db.close()
        self.session_vars = result.session_vars or {}
        text = result.reply_text or ""
        args = [{k: v for k, v in a.items() if k not in ("_diagnostics", "view", "contact_id", "space_id")} for a in captured]
        self.log.append(f">>> {body}\n{text}\n[route args] {json.dumps(args, ensure_ascii=False)}")
        print(self.log[-1] + "\n")
        return text, args


@pytest.fixture
def live(session_factory, monkeypatch):
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod
    from tests.chatbot.test_parser_prompt_budget import _rendered_production_prompt

    _seed_contact(session_factory, variables={})
    _seed_borrowable_envelope(session_factory)
    db = session_factory()
    try:
        ids = _seed(db)
    finally:
        db.close()
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    monkeypatch.setattr(
        engine_mod, "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": ["sales_orders.sales_report"], "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    system = _rendered_production_prompt(current_date=TODAY)
    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt=system, prompt_version=0, provider="openai",
            model=os.environ.get("REPORT_DRILL_LIVE_MODEL", "gpt-5.4-mini"),
            api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
        ),
    )
    console = LiveConsole(session_factory, monkeypatch)
    console.ids = ids
    return console


T1 = f"top 3 salesman for {PRODUCT} september 2026, delivered, by amount"

#: The parser is not deterministic: every case runs RUNS times and must pass every time (crew, 8 Oct).
RUNS = int(os.environ.get("REPORT_DRILL_LIVE_RUNS", "3"))
REPEAT = pytest.mark.parametrize("run", range(RUNS))


def _turn1(live) -> None:
    text, calls = live.say(T1)
    assert calls and calls[-1]["group_by"] == "sales_agent", text
    assert "ALICE TAN" in text and "BENNY LIM" in text, text


@REPEAT
def test_scoped_customers_of_these_agents(live, run) -> None:
    _turn1(live)
    text, calls = live.say("top 10 customers for these sales agents")
    assert calls, text
    args = calls[-1]
    assert args["group_by"] == "customer" and args.get("product_code") == PRODUCT, args
    assert sorted(args["customer_agent_ids"]) == sorted(
        live.ids["agents"][k] for k in ("ALICE TAN", "BENNY LIM", "CHONG WEI")
    ), args
    assert "Customers assigned to: ALICE TAN, BENNY LIM, CHONG WEI" in text, text
    assert "PENANG CERAMIC HOUSE" not in text and "PERAK SANITARY TRADING" in text, text


@REPEAT
def test_global_top_10_customers_overall(live, run) -> None:
    _turn1(live)
    text, calls = live.say("top 10 customers overall september 2026, delivered, by amount")
    assert calls, text
    args = calls[-1]
    assert args["group_by"] == "customer" and "customer_agent_ids" not in args and "product_code" not in args, args
    assert "PENANG CERAMIC HOUSE" in text, text


@REPEAT
def test_unsure_top_10_customers_asks(live, run) -> None:
    _turn1(live)
    text, calls = live.say("top 10 customers")
    assert calls == [], text
    assert text.strip() == "Top 10 customers of the 3 sales agents above, or across all customers?", text
    text, calls = live.say("the agents above")
    assert calls and calls[-1].get("customer_agent_ids"), text


@REPEAT
def test_non_agent_drill_customers_to_their_products(live, run) -> None:
    text, calls = live.say(f"top 3 customers for {PRODUCT} september 2026, delivered, by amount")
    assert calls and calls[-1]["group_by"] == "customer", text
    text, calls = live.say("top 5 products these customers buy")
    assert calls, text
    args = calls[-1]
    assert args["group_by"] == "product" and len(args.get("customer_ids") or []) == 3, args
    # Crew decision 8 Oct: the drill asks ACROSS products, so turn 1's product does not carry;
    # MEGAH and KLANG also bought the other product.
    assert "product_code" not in args, args
    assert OTHER in text and PRODUCT in text, text
