"""Fix lane round 2, S1 (reviewer pass at 4719a829): AC-S5-4 at the ENGINE level.

The slice 5 test pinned only `turn/state.py::focus_row_label`, which the answer header
and the miss line never read, so a T7 verdict entity `{raw: "M210-GM", quantity: 5}`
replied "• product: M210-GM / But no incoming matched these." with no "(x5)". Journey
step 3 of the UAC: each quantity is shown next to its code. These tests run a real turn
through `engine.run_turn` and read the reply text.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import _envelope, _parser_output
from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _seed_contact_scoped_to_sorento

PRODUCT_UUID = "55555555-5555-5555-5555-555555555555"


def _run_t7(session_factory, monkeypatch, *, tool_body: dict[str, Any], quantity: Any = 5) -> str:
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.head import parser as parser_mod
    from app.services.chatbot.lanes.business.services import AnswerServices

    _seed_contact_scoped_to_sorento(session_factory)
    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    row.chatbot_completed_lanes = ["business_query"]
    db.commit()

    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General",
            "attributes": [],
            "all_attributes_allowed": None,
        },
    )

    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    entity = {"raw": "M210-GM", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
    if quantity is not None:
        entity["quantity"] = quantity
    qf = _parser_output(domain_hint="incoming", intent_hint="check_incoming", entities=[entity])
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        tokens = list(body.get("tokens") or [])
        return {
            "tokens": tokens,
            "resolutions": [
                {
                    "token": t,
                    "resolved": True,
                    "matches": [
                        {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": "M210-GM", "match_tier": "exact"}
                    ],
                }
                for t in tokens
                if t.replace("-", "").upper() == "M210GM"
            ],
            "unresolved_tokens": [],
        }

    resolve_services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve),
        probe=lambda **_: None,
    )
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: resolve_services
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "fetch_services",
        lambda db: FetchServices(mcp_call=lambda name, args: json.dumps(tool_body)),
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: AnswerServices(
            mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
        ),
    )
    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = "any incoming for M210-GM x5"
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    return (result.reply or {}).get("text") or ""


def test_t7_miss_names_the_quantity_beside_the_code(session_factory, monkeypatch) -> None:
    reply = _run_t7(session_factory, monkeypatch, tool_body={"has_result": False, "items": []})
    assert "M210-GM (x5)" in reply, reply


def test_t7_answer_header_names_the_quantity_beside_the_code() -> None:
    """The answer's own `*<Label>* for <codes>:` header and its "No <label> found for
    <codes>." line (`turn/compose.py`) read the envelope's codes, which carry no
    quantity - the parser's quantity rides on the focus product row."""
    from types import SimpleNamespace

    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.state import Focus, Profile, State
    from tests.chatbot.test_rearch_s3_compose_data import (
        _envelope as _compose_envelope,
        _incoming_domain_row,
        _policy,
        _stock_domain_row,
    )

    state = State(
        focus=Focus(products=[{"raw": "M210-GM", "hint": "product", "quantity": 5}]),
        pending=None,
        profile=Profile(),
        turn_no=1,
    )
    answer = compose(
        [_compose_envelope("incoming", entities=["M210-GM"])],
        state,
        _policy(_stock_domain_row(), _incoming_domain_row()),
        SimpleNamespace(),
    )
    assert "M210-GM (x5)" in answer.text, answer.text


def test_no_quantity_prints_the_bare_code(session_factory, monkeypatch) -> None:
    reply = _run_t7(
        session_factory, monkeypatch, tool_body={"has_result": False, "items": []}, quantity=None
    )
    assert "M210-GM" in reply and "(x" not in reply, reply


def test_quantity_does_not_stick_to_a_carried_product() -> None:
    """The quantity is a fact of the message that typed it (the known nit that rides
    with S1). `focus_from_wire` is the one seam a stored focus becomes a later turn's
    state, and every row read back there is CARRIED: a carried M210-GM must not keep
    printing the earlier message's "(x5)" beside it in a later subject line."""
    from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_row_label, focus_to_wire

    focus = Focus(products=[{"raw": "M210-GM", "hint": "product", "quantity": 5}])
    carried = focus_from_wire(focus_to_wire(focus))
    assert carried.products and carried.products[0].get("raw") == "M210-GM", carried.products
    assert focus_row_label(carried.products[0]) == "M210-GM", carried.products
