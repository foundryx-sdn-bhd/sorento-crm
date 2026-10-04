"""#1262 fix lane round 5: the owner's hand test of 27 Sep (console :3092, parser v39
then v37), replayed turn by turn.

The owner asked for the delivery order list and got the outstanding DO bucket, five
times running. Once a turn said "outstanding", `focus.status` stayed "outstanding" for
every later turn (`turn/apply.py::_focus_rules` wrote it only when a verdict named a
status, and never cleared it), `turn_runtime.lane_parse_output` read it back for every
verdict that named none, and so a "check stock" turn still projected `so_outstanding`
and every later "DO for ...", "delivery for ...", "delivery status for ...", "list of
DO for ..." was filed as `do_outstanding` instead of the delivery order list.

R1: those phrasings, without the word outstanding, run the delivery order list
(`crm_order_management_orders_list`, no outstanding `order_status`), with the brand
and customer filters. R2: `status` and `document` come from the current message; a
message with its own domain or status word (`domain_in_message: true`) never inherits
them; a bare continuation (a number answering the bot's question, "and hanlim?") does.
R3: the replay types the owner's exact messages in order. Parser emissions are the
fields a live parser reading those words under the published prompt emits (the prompt's
ORDER_STATUS FILTER / DOCUMENT rules: status and document from the current message
only, never carried).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _present_response, _report_route_body
from tests.chatbot.test_samantha_26sep_r3_brand_carry import CHENG_HUAT_UUID, _Chat

HANLIM_UUID = "55555555-5555-5555-5555-555555555555"
ORDERS_LIST = "crm_order_management_orders_list"
REPORT = "crm_outstanding_report"
OUTSTANDING_BUCKETS = {"outstanding", "so_outstanding", "do_outstanding", "outstanding_both"}

_ORDERS_ROUTE_BODY = {
    "data": [
        {
            "order_number": "DO-CHS-1",
            "debtor_name": "CHENG HUAT HARDWARE (SENTUL)",
            "order_date": "2026-09-20",
            "order_status": "in transit",
            "transporter": "LORRY 7",
        },
        {
            "order_number": "DO-CHS-2",
            "debtor_name": "CHENG HUAT HARDWARE (SENTUL)",
            "order_date": "2026-09-18",
            "actual_delivery_date": "2026-09-19",
            "order_status": "delivered",
            "transporter": "LORRY 3",
        },
    ],
    "total": 2,
}


def _brand(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "brand", "canonical_code": "Sorento", "current_message": True, "confident": True}


def _customer(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}


def _order_ask(*, entities: list[dict[str, Any]], document: list[str] | None, status: str | None, **extra: Any):
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        domain_in_message=True,
        entities=entities,
        document=document,
        status=status,
        order_status=status,
        **extra,
    )


def _pick(n: int) -> dict[str, Any]:
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        domain_in_message=False, reference_positions=[n],
    )


def _document_only(doc: str) -> dict[str, Any]:
    return _order_ask(entities=[], document=[doc], status=None)


def _check_stock() -> dict[str, Any]:
    return _parser_output(
        domain_hint="inventory",
        intent_hint="check_stock",
        domain_in_message=True,
        entities=[
            {"raw": "M488-75-pvd-GM", "hint": "product", "canonical_code": None,
             "current_message": True, "confident": True, "quantity": 4}
        ],
    )


def _do_list(customer: str, brand: str = "sorento") -> dict[str, Any]:
    # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
    return _order_ask(entities=[_brand(brand), _customer(customer)], document=["DO"], status=None, **do_window())


#: The owner's messages, verbatim and in order, with the parser's reading of each and
#: the reading the CRM must give it: "scope_ask" (the "which document?" question),
#: "outstanding" (the outstanding report), "stock" (not an order read at all) or
#: "do_list" (the delivery order list).
OWNER_TURNS: list[tuple[str, dict[str, Any], str]] = [
    (
        "outstaing brand sorento dealr cheng huat sentul",
        _order_ask(entities=[_brand("sorento"), _customer("Cheng Huat Sentul")], document=[], status="outstanding"),
        "scope_ask",
    ),
    ("1", _pick(1), "outstanding"),
    # A bare document word typed under the report's own open offer re-runs the report
    # for that document (hand pass 3 row 5, unchanged): it answers the open question.
    ("delieyr orer?", _document_only("DO"), "outstanding"),
    ("ssales order", _document_only("SO"), "outstanding"),
    ("1", _pick(1), "outstanding"),
    ("check stock X4:M488-75-pvd-GM", _check_stock(), "stock"),
    ("DO for sorento brand in cheng huat sentul", _do_list("Cheng Huat Sentul"), "do_list"),
    ("delivery for sorento brand in cheng huat sentul", _do_list("Cheng Huat Sentul"), "do_list"),
    ("delivery status for sorento brand in cheng huat sentul", _do_list("Cheng Huat Sentul"), "do_list"),
    ("list of DO for sorento brand in cheng huat sentul", _do_list("Cheng Huat Sentul"), "do_list"),
    ("list of DO for hanlim sorenot brand only", _do_list("hanlim", brand="sorenot"), "do_list"),
    ("list of DO for sorento brand in cheng huat sentul", _do_list("Cheng Huat Sentul"), "do_list"),
]


#: DO-ASK-SIMPLIFY rule 2: every DO field is a per-contact reveal; this contact is an
#: existing one, which the seed migration granted every DO field.
_DO_REVEALS = (
    "delivery_orders.status",
    "delivery_orders.pickup_time",
    "delivery_orders.transporter",
    "delivery_orders.driver",
    "delivery_orders.lorry_plate",
    "delivery_orders.order_number",
    "delivery_orders.customer",
    "delivery_orders.order_date",
    "delivery_orders.delivery_date",
    "delivery_orders.warehouse",
    "delivery_orders.products",
)


class _Replay(_Chat):
    """`_Chat` (real `engine.run_turn`, one contact, session carried between turns) with
    the two customers the owner named and a per-tool MCP double: the outstanding report
    and the orders list go through the real presenter, anything else is a miss."""

    def __init__(self, session_factory, monkeypatch):
        super().__init__(session_factory, monkeypatch, attributes=["sales_orders.outstanding", *_DO_REVEALS], tool_body={})
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.lanes.business.services import AnswerServices  # noqa: F401

        present = _present_response()
        known = {"Cheng Huat Sentul": CHENG_HUAT_UUID, "hanlim": HANLIM_UUID}

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            return {
                "tokens": tokens,
                "resolutions": [
                    {
                        "token": t,
                        "resolved": True,
                        "matches": [
                            {"uuid": known[t], "entity_type": "customer", "canonical_code": t, "match_tier": "exact"}
                        ],
                    }
                    for t in tokens
                    if t in known
                ],
                "unresolved_tokens": [t for t in tokens if t not in known],
            }

        services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(resolve_entity),
            probe=lambda **_: None,
        )

        def mcp_call(name: str, args: dict) -> str:
            self.captured.append((name, dict(args)))
            if name == REPORT:
                return present(name, json.dumps(_report_route_body(REPORT_HIT, args)))
            if name == ORDERS_LIST:
                return present(name, json.dumps(_ORDERS_ROUTE_BODY))
            return json.dumps({"has_result": False, "items": []})

        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: services
        )
        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))

        # Every projection the turn made, so a turn's reading is asserted on the bucket
        # the order tools actually received, not only on which tool ran.
        self.projected: list[str | None] = []
        real = turn_runtime.lane_parse_output

        def recording(verdict_: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            out = real(verdict_, **kwargs)
            self.projected.append(out.get("order_status"))
            return out

        monkeypatch.setattr(turn_runtime, "lane_parse_output", recording)

    def turn(self, text_body: str, qf: dict[str, Any]) -> tuple[str, list[tuple[str, dict]], list]:
        mark, pmark = len(self.captured), len(self.projected)
        reply = self.say(text_body, qf)
        return reply, self.captured[mark:], self.projected[pmark:]


def _assert_reading(n: int, text_body: str, expected: str, reply: str, calls, projected, chat) -> None:
    where = f"turn {n} {text_body!r}: calls={[c[0] for c in calls]} projected={projected} reply={reply!r}"
    names = [c[0] for c in calls]
    if expected == "scope_ask":
        assert "Outstanding for which document?" in reply, where
        assert REPORT not in names, where
    elif expected == "outstanding":
        assert REPORT in names, where
        assert ORDERS_LIST not in names, where
    elif expected == "stock":
        assert REPORT not in names and ORDERS_LIST not in names, where
        assert not (set(filter(None, projected)) & OUTSTANDING_BUCKETS), (
            f"a check stock turn must not carry an outstanding bucket: {where}"
        )
    elif expected == "do_list":
        assert REPORT not in names, f"the delivery order list, never the outstanding report: {where}"
        order_calls = [a for name, a in calls if name == ORDERS_LIST]
        assert order_calls, f"the delivery order list must run: {where}"
        args = order_calls[-1]
        assert args.get("order_status") not in OUTSTANDING_BUCKETS, where
        assert not (set(filter(None, projected)) & OUTSTANDING_BUCKETS), where
        want = HANLIM_UUID if "hanlim" in text_body else CHENG_HUAT_UUID
        assert args.get("customer_ids") == [want], where
        if "sorenot" not in text_body:
            # "sorenot" is not on the live brand list, and round 4's ruling 13 (a typed
            # brand off the list ends the carried brand, said back) is unchanged here.
            assert args.get("brand_ids") == [chat.brand_id], where
        assert "DO-CHS-1" in reply and "in transit" in reply and "LORRY 7" in reply, where
        assert "DO-CHS-2" in reply and "delivered" in reply, where
    else:  # pragma: no cover - a typo in the table above
        raise AssertionError(expected)


def test_owner_hand_test_replayed_in_order(session_factory, monkeypatch) -> None:
    chat = _Replay(session_factory, monkeypatch)
    for n, (text_body, qf, expected) in enumerate(OWNER_TURNS, start=1):
        reply, calls, projected = chat.turn(text_body, qf)
        _assert_reading(n, text_body, expected, reply, calls, projected, chat)


@pytest.mark.parametrize(
    "text_body",
    [
        "list of DO for sorento brand in cheng huat sentul",
        "DO for sorento brand in cheng huat sentul",
        "delivery for sorento brand in cheng huat sentul",
        "delivery status for sorento brand in cheng huat sentul",
        "show me the DO for cheng huat sentul",
    ],
)
def test_do_list_right_after_an_outstanding_report(session_factory, monkeypatch, text_body) -> None:
    """R1 "whatever was said before": the list ask typed straight under the outstanding
    report's own open offer is still the delivery order list."""
    chat = _Replay(session_factory, monkeypatch)
    for n, (body, qf, expected) in enumerate(OWNER_TURNS[:2], start=1):
        reply, calls, projected = chat.turn(body, qf)
        _assert_reading(n, body, expected, reply, calls, projected, chat)
    reply, calls, projected = chat.turn(text_body, _do_list("Cheng Huat Sentul"))
    _assert_reading(3, text_body, "do_list", reply, calls, projected, chat)


def test_outstanding_do_in_the_message_still_runs_the_outstanding_report(session_factory, monkeypatch) -> None:
    """The other half of R1: the message's OWN outstanding word enters the bucket, even
    after the list ran."""
    chat = _Replay(session_factory, monkeypatch)
    chat.turn(OWNER_TURNS[6][0], OWNER_TURNS[6][1])
    reply, calls, projected = chat.turn(
        "outstanding DO for sorento brand in cheng huat sentul",
        _order_ask(entities=[_brand("sorento"), _customer("Cheng Huat Sentul")], document=["DO"], status="outstanding"),
    )
    assert [c for c in calls if c[0] == REPORT], (calls, reply)
    assert "do_outstanding" in projected, projected


# --------------------------------------------------------------------------- #
# R2 at the seam: `apply()` then `lane_parse_output`, no engine.
# --------------------------------------------------------------------------- #


def _outstanding_so_focus() -> Focus:
    return Focus(
        customers=[{"raw": "Cheng Huat Sentul", "hint": "customer", "uuids": [CHENG_HUAT_UUID]}],
        domains=["order"],
        document=["SO"],
        status="outstanding",
    )


def _project(focus: Focus, v: dict[str, Any]) -> tuple[Focus, str | None]:
    state2, _plan = apply(State(focus=focus, pending=None, profile=Profile()), v, build_policy())
    out = turn_runtime.lane_parse_output(v, focus=state2.focus)
    return state2.focus, out.get("order_status")


def test_a_check_stock_turn_drops_the_carried_status_and_document() -> None:
    v = verdict(
        entities=[entity("M488-75-pvd-GM", hint="product")],
        domain_in_message=True, domain_hint="inventory", intent_hint="check_stock",
    )
    focus, order_status = _project(_outstanding_so_focus(), v)
    assert order_status is None, order_status
    assert focus.status is None and focus.document == [], focus


def test_a_do_ask_with_its_own_domain_word_starts_clean() -> None:
    v = verdict(
        entities=[entity("Cheng Huat Sentul", hint="customer")],
        domain_in_message=True, domain_hint="order", document=["DO"], status=None,
    )
    focus, order_status = _project(_outstanding_so_focus(), v)
    assert order_status is None, order_status
    assert focus.document == ["DO"] and focus.status is None, focus


def test_a_bare_continuation_keeps_the_carried_status() -> None:
    """ "and hanlim?": an entity and nothing else, leaning on the previous ask."""
    v = verdict(entities=[entity("hanlim", hint="customer")], domain_in_message=False, domain_hint=None)
    focus, order_status = _project(_outstanding_so_focus(), v)
    assert order_status == "so_outstanding", order_status
    assert focus.status == "outstanding" and focus.document == ["SO"], focus


def test_a_message_naming_its_own_status_keeps_it() -> None:
    v = verdict(
        entities=[entity("hanlim", hint="customer")],
        domain_in_message=True, domain_hint="order", document=["DO"], status="outstanding",
    )
    _focus, order_status = _project(_outstanding_so_focus(), v)
    assert order_status == "do_outstanding", order_status
