"""WA-CONCISE S2 (card v4): cross-domain replies, one block per product code. AC-8 to AC-15.

UAC: documentation/plans/chatbot/wa-concise-acceptance-criteria.md. A whole engine turn
(`engine.run_turn` on the Postgres fixture, parser / resolver / MCP runner stubbed - the
harness of `test_rearch_r11_zero_stock_ladder.py`) and the WHOLE reply text is asserted.
Placeholder data only.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from tests.chatbot._wa_concise_helpers import (
    EMPTY_PAYLOAD,
    INCOMING_TOOL,
    PO_TOOL,
    STOCK_TOOL,
    compact_entry,
    compact_payload,
    detailed_payload,
    detailed_row,
    envelope_json,
    incoming_payload,
    incoming_shipment,
)
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)

OFFER = "Would you like me to escalate to purchasing team?"
GONE = (
    "Here's what you want",
    "But no incoming matched these.",
    "No incoming for",
    "But here are the stock details for the requested products:",
    "But there is INCOMING stock (ETA) for the requested products:",
)

UUIDS = {
    "SRTWT5844-GM": "58440000-0000-0000-0000-000000000001",
    "SRTWT5844-BL": "58440000-0000-0000-0000-000000000002",
    "SRTWB1543": "15430000-0000-0000-0000-000000000001",
    "SRTWB1543-BL": "15430000-0000-0000-0000-000000000002",
    "SRTWB2200-BL": "22000000-0000-0000-0000-000000000002",
    "SRTPO0001": "00010000-0000-0000-0000-000000000001",
}

Responder = Callable[[dict[str, Any]], str]


def _uuid_codes(args: dict[str, Any]) -> list[str]:
    ids = args.get("product_ids") or []
    return [c for c, u in UUIDS.items() if u in ids]


def _resolver_bundle(codes: dict[str, str]) -> ResolveGateServices:
    """Every code resolves to one product at tier `exact`. The lane sends tokens with the
    dashes stripped ("SRTWB2200BL"), so tokens are matched on the dash-free form and the
    canonical code keeps its dash."""
    by_flat = {c.replace("-", "").upper(): c for c in codes}

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = [t for t in (body.get("tokens") or []) if t.replace("-", "").upper() in by_flat]
        return {
            "tokens": asked,
            "resolutions": [
                {
                    "raw": t,
                    "token": t,
                    "matches": [
                        {
                            "uuid": codes[by_flat[t.replace("-", "").upper()]],
                            "entity_type": "product",
                            "canonical_code": by_flat[t.replace("-", "").upper()],
                            "match_tier": "exact",
                        }
                    ],
                }
                for t in asked
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


def _message(text: str):
    """The inbound envelope with the customer's words, which must carry every asked code
    (the engine drops a parsed entity that is not in the message)."""
    return _envelope(
        message={
            "event_type": "message.received",
            "contact": {"id": CONTACT_ID},
            "message": {
                "messageId": "ZZT-msg-1",
                "contactId": CONTACT_ID,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": text},
            },
        }
    )


def _turn(
    session_factory,
    monkeypatch,
    stub_parser,
    stub_access,
    *,
    codes: list[str],
    intent: str,
    domain: str,
    tools: dict[str, Responder],
    word: str = "check",
    staff: bool = False,
    ladder: dict[str, list[str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """One real turn; returns (the whole customer-visible text, the reply dict)."""
    from sqlalchemy import text as sql_text

    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod

    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    for row in db.query(SystemSetting).all():
        row.chatbot_completed_lanes = ["business_query"]
        row.chatbot_crossdomain_ladder = ladder or {
            "inventory": ["incoming", "purchase_order"],
            "incoming": ["inventory", "purchase_order"],
        }
    if staff:
        db.execute(
            sql_text(
                "UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {"p": json.dumps({"tier": "office"}), "cid": str(CONTACT_ID)},
        )
    db.commit()

    def _mcp_call(name: str, args: dict[str, Any]) -> str:
        fn = tools.get(name)
        if fn is None:
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})
        return fn(args)

    bundle = _resolver_bundle({c: UUIDS[c] for c in codes})
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
    )
    stub_parser(
        _parser_output(
            intent_hint=intent,
            domain_hint=domain,
            entities=[
                {"raw": c, "hint": "product", "canonical_code": None, "current_message": True}
                for c in codes
            ],
        )
    )
    stub_access(attributes=["purchase_orders.placed", "inventory.sellable"])

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert result.status == "done", result.error
    reply = result.reply or {}
    return (reply.get("text") or ""), reply


def _stock(entries_by_code: dict[str, list[tuple[str, int, int | None]]]) -> Responder:
    """The stock tool: compact entries (real presenter) for the codes asked that have any."""

    def _fn(args: dict[str, Any]) -> str:
        entries = [
            compact_entry(c, entries_by_code[c]) for c in _uuid_codes(args) if c in entries_by_code
        ]
        if not entries:
            return envelope_json(STOCK_TOOL, EMPTY_PAYLOAD)
        return envelope_json(STOCK_TOOL, compact_payload(entries))

    return _fn


def _incoming(rows_by_code: dict[str, dict]) -> Responder:
    def _fn(args: dict[str, Any]) -> str:
        shipments = [rows_by_code[c] for c in _uuid_codes(args) if c in rows_by_code]
        if not shipments:
            return envelope_json(INCOMING_TOOL, EMPTY_PAYLOAD)
        return envelope_json(INCOMING_TOOL, incoming_payload(shipments))

    return _fn


def _po(rows: bool = False) -> Responder:
    def _fn(args: dict[str, Any]) -> str:
        if not rows:
            return envelope_json(PO_TOOL, EMPTY_PAYLOAD)
        return envelope_json(
            PO_TOOL,
            {
                "data": [
                    {
                        "po_number": "PO-0001",
                        "product_code": "SRTPO0001",
                        "ordered_qty": 10,
                        "outstanding_qty": 10,
                        "po_date": "2026-09-11",
                        "location": "BRW",
                    }
                ],
                "pagination": {"total": 1},
            },
        )

    return _fn


def _ask_incoming(session_factory, monkeypatch, stub_parser, stub_access, **kw):
    return _turn(
        session_factory, monkeypatch, stub_parser, stub_access,
        intent="check_incoming", domain="incoming", **kw,
    )


def _ask_stock(session_factory, monkeypatch, stub_parser, stub_access, **kw):
    return _turn(
        session_factory, monkeypatch, stub_parser, stub_access,
        intent="check_stock", domain="inventory", **kw,
    )


def _no_old_phrases(said: str) -> None:
    for phrase in GONE:
        assert phrase not in said, (phrase, said)



INC_A = incoming_shipment(
    "SRTWB1543", "IAAU1907074", "2026-09-09", 49, [("BRW", 35), ("BRW-BB", 4), ("BRW-SMC", 10)]
)
INC_A_LINES = (
    "*Container:* IAAU1907074\n*ETA:* 2026-09-09\n*Incoming Quantity:* 49\n"
    "*Warehouse Allocations:* BRW (35), BRW-BB (4), BRW-SMC (10)"
)


def _stock_detailed(rows: list[dict], companies: list[str]) -> Responder:
    def _fn(args: dict[str, Any]) -> str:
        payload = detailed_payload(rows)
        payload["lookup_companies"] = [{"name": n} for n in companies]
        return envelope_json(STOCK_TOOL, payload)

    return _fn


def _fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch):
    return dict(
        session_factory=session_factory,
        monkeypatch=monkeypatch,
        stub_parser=stub_parser,
        stub_access=stub_access,
    )


# ---- AC-8 ------------------------------------------------------------------ #


def test_ac8_incoming_miss_with_stock_is_one_block(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-GM"],
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock({"SRTWT5844-GM": [("BRW", 24, 10)]}),
            PO_TOOL: _po(),
        },
    )

    _no_old_phrases(said)
    assert said == f"*Product Code:* SRTWT5844-GM\n*Incoming:* none\n*BRW:* 24 (O/S: 10)\n\n{OFFER}", said


# ---- AC-9 ------------------------------------------------------------------ #


def test_ac9_all_zero_nothing_on_order(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-BL"],
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock({"SRTWT5844-BL": [("BRW", 0, None)]}),
            PO_TOOL: _po(),
        },
    )

    _no_old_phrases(said)
    assert said == (
        f"*Product Code:* SRTWT5844-BL\n*Incoming:* none\n*Stock:* 0\n*PO:* none\n\n{OFFER}"
    ), said


def test_ac9_zero_row_with_outstanding_keeps_its_location_line(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-BL"],
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock({"SRTWT5844-BL": [("BRW", 0, 233)]}),
            PO_TOOL: _po(),
        },
    )

    _no_old_phrases(said)
    assert said == (
        "*Product Code:* SRTWT5844-BL\n*Incoming:* none\n*BRW:* 0 (O/S: 233)\n*PO:* none"
        f"\n\n{OFFER}"
    ), said


# ---- AC-10 ----------------------------------------------------------------- #


def test_ac10_no_stock_row_nothing_on_order(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-BL"],
        tools={INCOMING_TOOL: _incoming({}), STOCK_TOOL: _stock({}), PO_TOOL: _po()},
    )

    _no_old_phrases(said)
    assert "No incoming, no stock and nothing on order" not in said, said
    # The block is the whole body; the escalation offer under it is AC-14's locked phrase.
    assert said == (
        f"*Product Code:* SRTWT5844-BL\n*Incoming:* none\n*Stock:* none\n*PO:* none\n\n{OFFER}"
    ), said


# ---- AC-11 ----------------------------------------------------------------- #


def test_ac11_po_placed_lines_sit_in_the_code_block(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTPO0001"],
        tools={INCOMING_TOOL: _incoming({}), STOCK_TOOL: _stock({}), PO_TOOL: _po(True)},
    )

    _no_old_phrases(said)
    assert "but PO is placed" not in said, said
    assert said == (
        "*Product Code:* SRTPO0001\n*Incoming:* none\n*Stock:* none\n*PO:* PO-0001\n"
        "*Ordered:* 10\n*Outstanding:* 10\n*PO date:* 2026-09-11\n*Location:* BRW"
        f"\n\n{OFFER}"
    ), said


# ---- AC-12 ----------------------------------------------------------------- #


def test_ac12_mixed_incoming_block_first_numbering_continues(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWB1543", "SRTWB1543-BL"],
        tools={
            INCOMING_TOOL: _incoming({"SRTWB1543": INC_A}),
            STOCK_TOOL: _stock({"SRTWB1543-BL": [("BRW", 20, 0)]}),
            PO_TOOL: _po(),
        },
    )

    _no_old_phrases(said)
    assert "I have attached the file(s) below." not in said, said
    assert "No incoming for" not in said, said
    assert said == (
        f"1. *Product Code:* SRTWB1543\n{INC_A_LINES}\n\n"
        "2. *Product Code:* SRTWB1543-BL\n*Incoming:* none\n*BRW:* 20 (O/S: 0)\n\n"
        f"{OFFER}"
    ), said


# ---- AC-13 ----------------------------------------------------------------- #

_WAREHOUSE_OFFER = "Would you like me to escalate to warehouse team?"


def test_ac13_mirror_no_stock_row_incoming_exists(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_stock(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWB1543"],
        tools={INCOMING_TOOL: _incoming({"SRTWB1543": INC_A}), STOCK_TOOL: _stock({}), PO_TOOL: _po()},
    )

    _no_old_phrases(said)
    assert "No stock for" not in said, said
    assert said == (
        f"*Product Code:* SRTWB1543\n*Stock:* none\n{INC_A_LINES}\n\n{_WAREHOUSE_OFFER}"
    ), said


def test_ac13_mirror_zero_stock_row_incoming_exists(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_stock(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWB1543"],
        tools={
            INCOMING_TOOL: _incoming({"SRTWB1543": INC_A}),
            STOCK_TOOL: _stock({"SRTWB1543": [("BRW", 0, 0)]}),
            PO_TOOL: _po(),
        },
    )

    _no_old_phrases(said)
    assert "No stock for" not in said, said
    assert said == (
        f"*Product Code:* SRTWB1543\n*Stock:* 0\n{INC_A_LINES}\n\n{_WAREHOUSE_OFFER}"
    ), said


# ---- AC-14 ----------------------------------------------------------------- #


def test_ac14_staff_gets_the_blocks_without_the_offer(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-GM"],
        staff=True,
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock({"SRTWT5844-GM": [("BRW", 24, 10)]}),
            PO_TOOL: _po(),
        },
    )

    assert said == "*Product Code:* SRTWT5844-GM\n*Incoming:* none\n*BRW:* 24 (O/S: 10)", said


def test_ac14_guard_dealer_offer_phrase_and_quick_replies_are_locked(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    """Regression guard, green today: the locked phrase closes the dealer's reply and
    the quick reply is today's."""
    said, reply = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-GM"],
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock({"SRTWT5844-GM": [("BRW", 24, 10)]}),
            PO_TOOL: _po(),
        },
    )

    assert said.endswith(OFFER), said
    assert reply.get("quick_replies") == "Yes", reply


# ---- AC-15 (regression guard, green today) --------------------------------- #


def test_ac15_guard_company_searched_but_empty_keeps_its_line(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said, _ = _ask_incoming(
        **_fixtures(session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch),
        codes=["SRTWT5844-GM"],
        tools={
            INCOMING_TOOL: _incoming({}),
            STOCK_TOOL: _stock_detailed(
                [detailed_row("Sorento", "SRTWT5844-GM", "BRW", 24)], ["Sorento", "Mocha"]
            ),
            PO_TOOL: _po(),
        },
    )

    assert "*Mocha:* no stock records for SRTWT5844-GM." in said, said
