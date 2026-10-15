"""Phase 2 RED tests - chat fix PR-B S9 (AC-B11): a customer picker whose accounts all carry the
same stamp answers directly instead of asking which account.

`turn_runtime.stamp_customer_ask` (the engine's ask point) stamps a typed-names roster
"has DO" / "no DO". When every option reads the same, the question changes nothing, so the bot
answers (W02 t2: "no DO" on all 4). Mixed stamps still ask. Whole turns on the
`test_picker_one.py` harness; the DO probe is a double that reports who has a delivered DO.
"""
from __future__ import annotations

from typing import Any

import pytest

ASK_HEAD = "Which one do you mean?"
TRADING = "HANLIM TRADING SDN BHD"
HARDWARE = "HANLIM HARDWARE SDN BHD"


def _turn(session_factory, monkeypatch, *, with_do: tuple[str, ...], tag: str, document=None, body="DO for hanlim trading and hanlim hardware"):
    from app.services.chatbot.lanes.business.services import ResolveGateServices
    from tests.chatbot.conftest import validating_resolve_entity
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact

    probes: list[Any] = []

    def probe(*, tool, contact_id, entities, semantic_input, user_prompt):
        probes.append(entities)
        items = [
            {"title": f"DO-{i}", "fields": [
                {"label": "Customer", "value": name},
                {"label": "Actual Delivery Date", "value": "2026-10-01"},
            ]}
            for i, name in enumerate(with_do, start=1)
        ]
        return {"items": items, "has_result": bool(items)}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(
            lambda body: {"tokens": [], "resolutions": [], "unresolved_tokens": []}
        ),
        probe=probe,
    )
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_parser_output(
            domain_hint="order", intent_hint="check_order", order_status=None, document=document,
            entities=[
                {"raw": "hanlim trading", "hint": "customer", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "hanlim hardware", "hint": "customer", "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
        ),
        text_body=body,
        msg_id=f"zzt-picker-same-stamp-{tag}",
        resolve_services=services,
    )
    return (result.reply or {}).get("text") or "", probes, captured


def test_b11_a_do_ask_where_every_account_has_no_do_answers_directly(session_factory, monkeypatch) -> None:
    reply, probes, _c = _turn(session_factory, monkeypatch, with_do=(), tag="nodo", document=["DO"])
    assert probes, "the roster was never measured"
    assert ASK_HEAD not in reply, reply
    assert "no DO" in reply or "no do" in reply.lower(), reply


def test_b11_near_miss_a_do_ask_where_every_account_has_a_do_still_asks(session_factory, monkeypatch) -> None:
    reply, probes, _c = _turn(session_factory, monkeypatch, with_do=(TRADING, HARDWARE), tag="alldo", document=["DO"])
    assert probes, "the roster was never measured"
    assert ASK_HEAD in reply, reply


def test_b11_near_miss_an_so_ask_is_never_answered_with_the_do_stamp(session_factory, monkeypatch) -> None:
    """M1 (review): the DO stamp answers a DO question only; an SO ask over the same roster asks."""
    reply, _p, _c = _turn(
        session_factory, monkeypatch, with_do=(), tag="so", document=["SO"],
        body="SO for hanlim trading and hanlim hardware",
    )
    assert ASK_HEAD in reply, reply


def test_b11_near_miss_mixed_stamps_still_ask_which_account(session_factory, monkeypatch) -> None:
    reply, _p, _c = _turn(session_factory, monkeypatch, with_do=(TRADING,), tag="mixed", document=["DO"])
    assert reply == f"{ASK_HEAD}\n1. hanlim trading - has DO\n2. hanlim hardware - no DO", reply


def test_b11_the_stamp_helper_itself_marks_none_when_unmeasured(session_factory, monkeypatch) -> None:
    """Near-miss: a roster that could not be measured (no stamp on any option) is NOT 'all the
    same': it is asked, as today (the NOT MEASURED reading)."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn import pending as pending_mod

    options = [
        {"label": "HANLIM", "name": "HANLIM", "code": "1", "uuid": "aaaaaaaa-0000-4000-8000-000000000001"},
        {"label": "HANLIM B", "name": "HANLIM B", "code": "2", "uuid": "bbbbbbbb-0000-4000-8000-000000000002"},
    ]
    ask = pending_mod.ask("customer_pick", options)
    out = turn_runtime.stamp_customer_ask(
        None, ask, ctx={}, payload={"customer_bases_with_do": None}, space_id=None
    )
    assert getattr(out, "kind", None) == "customer_pick"
    assert not any(o.get("stamp") for o in (out.options or []))
