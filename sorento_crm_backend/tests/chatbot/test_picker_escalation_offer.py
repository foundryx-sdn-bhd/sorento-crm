"""PICKER-ESCALATION (owner, dev, contact Jayden Loo, 3 Oct 2026): "SRTWT5844-GM delivery
for yoo living" printed the customer picker (two YOO LIVING HOUSE ledgers), "1" answered
it, and the reply was "Customer/Product/Dates" over "No orders matched these." with NO
escalation offer, for a contact whose "Chatbot hands over to support teams" switch is ON.

Owner rule: a contact that may escalate gets the escalation offer on an order enquiry that
finds no answer, a picker answer included.

Why: `engine._run_stages` read `order_list_was_open` off `order_list.is_open_order_list
(state_in.focus)`, which only asks whether the focus carries the order domain. The picker
turn had already stored that domain, so the turn that answered the picker counted as
"inside an open list" although no list had been shown, and R6 (`order_list.list_reply`)
took the offer out and swapped the miss for one line.

Owner ruling 4 Oct 2026: "any no answer should get the escalation question, that's the
gist", inside a list that really was shown as well, so R6 no longer touches a miss.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.chatbot.turn.task import REFER_TO_SALESMAN
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.models.base import set_company_scope
from tests._mc_lookup_seed import customer
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _order_ask, _pick
from tests.chatbot.test_samantha_27sep_r7_owner_replay import (
    BOTH,
    ESCALATE_WORDS,
    _OwnerChat,
)

YOO = "yoo living"
#: Two families, so the gate asks which one (`lanes/business/gate.py`, "Which customer do
#: you mean?").
YOO_NAMES = ("YOO LIVING HOUSE SDN BHD", "YOO LIVING HOUSE TRADING SDN BHD")


def _yoo_ask() -> dict[str, Any]:
    return _order_ask(
        entities=[{"raw": YOO, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
        document=["DO"],
        status="delivered",
    )


class _PickerChat(_OwnerChat):
    """The owner replay chat, plus two YOO LIVING HOUSE ledgers with no orders: the
    customer word resolves to both, so the turn prints the customer picker."""

    def __init__(self, session_factory, monkeypatch, *, escalation_allowed: bool):
        super().__init__(session_factory, monkeypatch)
        from app.services.chatbot import engine as engine_mod

        db = session_factory()
        set_company_scope(db, BOTH)
        yoo = [customer(db, company_id=DEFAULT_COMPANY_ID, name=n).id for n in YOO_NAMES]
        db.execute(
            text("UPDATE respond_contacts SET escalation_allowed = :a WHERE respond_io_id = :c"),
            {"a": escalation_allowed, "c": str(CONTACT_ID)},
        )
        db.commit()
        self.yoo = yoo

        def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            hits = [t for t in tokens if t.casefold() == YOO]
            return {
                "tokens": tokens,
                "resolutions": [
                    {"token": t, "resolved": True, "matches": [
                        {
                            "uuid": lid, "entity_type": "customer", "canonical_code": f"3000/Y{i}",
                            "match_tier": "fuzzy", "company_code": "SRT",
                            "display": {"customer_name": name, "debtor_name": name},
                        }
                        for i, (lid, name) in enumerate(zip(yoo, YOO_NAMES), 1)
                    ]}
                    for t in hits
                ],
                "unresolved_tokens": [t for t in tokens if t not in hits],
            }

        services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(resolve_entity),
            probe=lambda **_: None,
        )
        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: services
        )


def _chat(session_factory, monkeypatch, *, escalation_allowed: bool):
    from app.main import app

    chat = _PickerChat(session_factory, monkeypatch, escalation_allowed=escalation_allowed)
    return chat, app


def _picker_then_one(chat) -> tuple[str, str, list]:
    picker, calls, _ = chat.turn("delivery for yoo living", _yoo_ask())
    assert "YOO LIVING HOUSE" in picker and not [n for n, _ in calls if n == ORDERS_LIST], picker
    reply, calls, _ = chat.turn("1", _pick(1))
    assert [a for n, a in calls if n == ORDERS_LIST], calls
    return picker, reply, calls


def test_picker_answer_with_no_orders_offers_escalation(session_factory, monkeypatch) -> None:
    chat, app = _chat(session_factory, monkeypatch, escalation_allowed=True)
    try:
        _picker, reply, _calls = _picker_then_one(chat)
    finally:
        app.dependency_overrides.clear()
    low = reply.casefold()
    assert any(w in low for w in ESCALATE_WORDS), reply


def test_picker_answer_with_no_orders_never_offers_a_barred_contact(session_factory, monkeypatch) -> None:
    chat, app = _chat(session_factory, monkeypatch, escalation_allowed=False)
    try:
        _picker, reply, _calls = _picker_then_one(chat)
    finally:
        app.dependency_overrides.clear()
    low = reply.casefold()
    assert not any(w in low for w in ESCALATE_WORDS), reply
    assert REFER_TO_SALESMAN in reply, reply
