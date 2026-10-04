"""Round 3: (A) no project forecast for a brand-scoped contact, (B) the SO-NUMBER-ASK answers.

A. `crm_project_forecast` returns project and quotation money with no product dimension, so a
   scoped contact cannot be given a MOCHA-only figure: it must not run at all. The chatbot never
   picks it from a domain row (it is an undomained tool), so the seam to pin is the single MCP
   choke point `services._mcp_call(db)`; the REST route is pinned beside it.
B. `so_status.lookup` and `so_status.list_text` answer before any tool, so the fetch guard never
   sees them: the session criterion on `SalesOrderLine`/`Product` has to do the work.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._brand_scope_seed import api, db, world  # noqa: F401  (fixtures by name)
from tests._pg_fixture import unique_code

FORECAST = "/api/v1/project-sales/reports/forecast"


# --------------------------------------------------------------------- A. forecast


def _stamp(db, world, *, scoped: bool):
    from app.models.base import set_brand_scope

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    set_brand_scope(db, frozenset({world.mocha.id}) if scoped else None)
    return db


class _ClientSpy:
    calls: list[tuple] = []

    def __init__(self, *a, **k) -> None:
        pass

    def call_tool(self, name, args):
        _ClientSpy.calls.append((name, args))
        return json.dumps({"pipeline": 1})


def _seam(db, monkeypatch):
    from app.services.ai_assistant_service import MCPRuntimeClient  # noqa: F401
    import app.services.ai_assistant_service as svc
    from app.services.chatbot.lanes.business import services

    _ClientSpy.calls = []
    monkeypatch.setattr(svc, "MCPRuntimeClient", _ClientSpy)
    return services._mcp_call(db)


def test_scoped_session_never_reaches_the_forecast_tool(db, world, monkeypatch) -> None:
    from app.services.chatbot.lanes.business.fetch import ToolNotAllowed

    call = _seam(_stamp(db, world, scoped=True), monkeypatch)
    with pytest.raises(ToolNotAllowed):
        call("crm_project_forecast", {})
    assert _ClientSpy.calls == [], "the forecast tool was called for a brand-scoped contact"


def test_unscoped_session_still_reaches_the_forecast_tool(db, world, monkeypatch) -> None:
    call = _seam(_stamp(db, world, scoped=False), monkeypatch)
    call("crm_project_forecast", {})
    assert [c[0] for c in _ClientSpy.calls] == ["crm_project_forecast"]


def test_forecast_route_gives_a_scoped_contact_no_figures(api, world) -> None:
    """The existing not-granted behaviour of this route is 403 (`require_permission_with_api_key`);
    a scoped contact over X-API-Key gets that 403 and no body figures."""
    resp = api.get(FORECAST, world.scoped)
    assert resp.status_code == 403, resp.text
    assert "pipeline" not in resp.text.lower()


def test_forecast_route_still_serves_an_unscoped_contact(api, world) -> None:
    resp = api.get(FORECAST, world.unscoped)
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------- B. SO number ask


def _so(db, number: str, lines, *, customer_id) -> SalesOrder:
    so = SalesOrder(
        id=str(uuid.uuid4()), so_number=number, customer_id=customer_id, order_date=date(2026, 9, 10),
        status="open", company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    for prod, ordered, delivered in lines:
        db.add(SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=so.id, product_id=prod.id, qty_ordered=ordered,
            qty_delivered=delivered, line_total=Decimal("1.00"), line_status="open",
            company_id=DEFAULT_COMPANY_ID,
        ))
    db.flush()
    return so


def _number() -> str:
    return "SO9" + str(uuid.uuid4().int)[:6]


@pytest.fixture
def sos(db, world):
    """MOCHA line delivered in full; SORENTO and unbranded lines not delivered at all."""
    mixed = _so(db, _number(), [(world.p_mocha, 5, 5), (world.p_sorento, 5, 0), (world.p_null, 5, 0)],
                customer_id=world.cust.id)
    sorento_only = _so(db, _number(), [(world.p_sorento, 5, 0)], customer_id=world.cust.id)
    db.commit()
    return SimpleNS(mixed=mixed, sorento_only=sorento_only)


class SimpleNS:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _lookup(db, world, words, *, scoped: bool):
    from app.services.chatbot import so_status

    return so_status.lookup(_stamp(db, world, scoped=scoped), words, linked=None)


def test_so_card_unscoped_control_reads_partly_delivered(db, world, sos) -> None:
    out = _lookup(db, world, [sos.mixed.so_number], scoped=False)
    assert "partly delivered" in out.cards[0], out.cards


def test_so_card_for_a_mixed_order_reads_from_the_in_scope_line_only(db, world, sos) -> None:
    """Delivery words come from the MOCHA line (fully delivered), not the SORENTO / unbranded ones."""
    out = _lookup(db, world, [sos.mixed.so_number], scoped=True)
    assert len(out.cards) == 1, out
    assert "fully delivered" in out.cards[0] and "partly" not in out.cards[0], out.cards


def test_so_with_only_out_of_scope_lines_reads_like_a_missing_number(db, world, sos) -> None:
    from app.services.chatbot import so_status

    ghost = _number()
    real = _lookup(db, world, [sos.sorento_only.so_number], scoped=True)
    miss = _lookup(db, world, [ghost], scoped=True)
    assert real.cards == [] and real.refused is False
    assert so_status.reply_text(real, refusal="").replace(sos.sorento_only.so_number, "<X>") == (
        so_status.reply_text(miss, refusal="").replace(ghost, "<X>")
    )


def test_so_list_counts_in_scope_lines_only(db, world, sos) -> None:
    from app.services.chatbot import so_status

    names = {str(world.cust.id): world.cust.customer_name}
    window = (date(2026, 9, 1), date(2026, 9, 30))
    plain = so_status.list_text(_stamp(db, world, scoped=False), names, *window)
    assert sos.mixed.so_number in plain and sos.sorento_only.so_number in plain
    assert "partly delivered" in plain
    scoped = so_status.list_text(_stamp(db, world, scoped=True), names, *window)
    assert sos.mixed.so_number in scoped
    assert sos.sorento_only.so_number not in scoped, "an SO with no in-scope line is listed"
    mixed_row = next(l for l in scoped.splitlines() if sos.mixed.so_number in l)
    assert "fully delivered" in mixed_row and "partly" not in mixed_row, mixed_row


def _run_fetch(db, world, parse_extra):
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": []},
        "ctx": {
            "contact": {"id": world.scoped.id},
            "access": {"attributes": ["sales_orders.outstanding"]},
            "parse": {"output": {"domain_hint": "order", "intent_hint": "check_order",
                                  "message_type": "business_query", "entities": [], **parse_extra}},
        },
    }

    def _no_tool(name, args):
        raise AssertionError("the SO answer must come before any tool")

    fragment = run_fetch(payload, services=FetchServices(mcp_call=_no_tool), db=db)
    return fragment["fetch"]["response"]


def test_run_fetch_so_number_answer_on_the_engine_stamped_session(db, world, sos) -> None:
    """Engine style: the turn session is stamped, then `run_fetch` answers the SO words."""
    _stamp(db, world, scoped=True)
    ghost = _number()
    text = _run_fetch(db, world, {"so_numbers": [sos.mixed.so_number]})
    assert "fully delivered" in text and "partly" not in text, text
    only = _run_fetch(db, world, {"so_numbers": [sos.sorento_only.so_number]})
    miss = _run_fetch(db, world, {"so_numbers": [ghost]})
    assert only.replace(sos.sorento_only.so_number, "<X>") == miss.replace(ghost, "<X>")
