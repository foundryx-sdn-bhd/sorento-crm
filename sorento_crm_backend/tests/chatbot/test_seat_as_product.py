"""SEAT-AS-PRODUCT: "seat cover for <WC>" is a product resolution, never an answer lane.

Owner rule (13 Oct): resolving a seat cover is resolving a product. The WC code is replaced by
its linked seat-cover codes (`product_compatibility`, #1590) and the turn runs on exactly as if
the customer had typed those codes: the domain the parser resolved (stock, ETA, files, nothing
asked) answers through the EXISTING handler and formatter. #1601's `crm_find` override in
`run_fetch` and its `Code: / Stock:` reply are gone.

Card: `documentation/plans/chatbot/CARD-seat-as-product-13oct.md` (crew: Q2 b, Q3 a, Q4 yes,
Q5 yes, Q1 a as one constant). Plan: `PLAN-seat-as-product-13oct.md`.

The parity tests run the SAME turn twice on one database: once with the seat-cover codes typed
directly (the control), rolled back to a savepoint, then with "seat cover for <WC>". The tool
calls (name and args) and the reply must be identical. Fake ZZT codes only.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_outstanding_lane import _qf, _run_turn, _seed_contact

WC_ONE = "ZZTWC8383-RL-MBL"
COVER_ONE = "ZZTSCBD823-MBL"
WC_TWO = "ZZTWC8122"
COVERS_TWO = ["ZZTSCBD333-UF", "ZZTSCBD333A-UF"]
WC_NONE = "ZZTWC8999"
OTHER = "ZZTWT5608"
FITS = {"filters": [{"key": "fits", "value": None, "unit": None}]}

#: One envelope for every tool: what differs between two turns is then only what the lane
#: sent and how the existing structurer printed the same rows.
ENVELOPE = json.dumps(
    {
        "has_result": True,
        "response": "ZZT existing reply",
        "items": [{"product_code": COVER_ONE, "quantity_on_hand": 11}],
    }
)


def _seed(session_factory) -> dict[str, str]:
    from app.models.product_compatibility import ProductCompatibility
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    rows = {
        code: seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        for code in (WC_ONE, COVER_ONE, WC_TWO, *COVERS_TWO, WC_NONE, OTHER)
    }
    for cover, wc in ((COVER_ONE, WC_ONE), (COVERS_TWO[0], WC_TWO), (COVERS_TWO[1], WC_TWO)):
        db.add(
            ProductCompatibility(
                company_id=DEFAULT_COMPANY_ID,
                seat_cover_product_id=rows[cover].id,
                wc_product_id=rows[wc].id,
                source="manual",
            )
        )
    db.commit()
    return {code: str(row.id) for code, row in rows.items()}


def _product(code: str) -> dict[str, Any]:
    return {"raw": code, "hint": "product", "canonical_code": code, "current_message": True, "confident": True}


def _turn(session_factory, monkeypatch, *, text: str, msg_id: str, **qf: Any):
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_qf(**qf),
        text_body=text,
        msg_id=msg_id,
        mcp_response=ENVELOPE,
        real_resolver=True,
    )
    return (result.reply or {}).get("text") or "", captured


def _parity(session_factory, monkeypatch, *, domain: str, intent: str, typed: list[str], asked: list[str],
            text_typed: str, text_asked: str, find: Any = FITS):
    """(control reply, control calls, asked reply, asked calls) on one database."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_parity")
    control = _turn(
        session_factory, monkeypatch, text=text_typed, msg_id="ZZT-seat-control",
        domain_hint=domain, intent_hint=intent, entities=[_product(c) for c in typed],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_parity")
    asked_out = _turn(
        session_factory, monkeypatch, text=text_asked, msg_id="ZZT-seat-asked",
        domain_hint=domain, intent_hint=intent, entities=[_product(c) for c in asked], find=find,
    )
    return control, asked_out


def _names(calls) -> list[str]:
    return [name for name, _args in calls]


# --------------------------------------------------------------------------- the three owner cases


def test_case2_stock_seat_cover_answers_exactly_as_stock_of_the_cover_codes(session_factory, monkeypatch) -> None:
    """"check stock seat cover for SRTWC8122": the existing stock answer, never `Code: / Stock:`."""
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="inventory", intent="check_stock",
        typed=COVERS_TWO, asked=[WC_TWO],
        text_typed=f"check stock {' '.join(COVERS_TWO)}", text_asked=f"check stock seat cover for {WC_TWO}",
    )
    assert "crm_find" not in _names(a_calls), a_calls
    assert c_calls, "the control must fetch"
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_case3_eta_seat_cover_answers_eta_not_stock(session_factory, monkeypatch) -> None:
    """"eta seat cover for SRTWC8383-RL-MBL": the incoming domain's own tool and reply."""
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="incoming", intent="check_incoming",
        typed=[COVER_ONE], asked=[WC_ONE],
        text_typed=f"eta {COVER_ONE}", text_asked=f"eta seat cover for {WC_ONE}",
    )
    assert "crm_find" not in _names(a_calls), a_calls
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_case1_no_domain_answers_as_the_bare_cover_code(session_factory, monkeypatch) -> None:
    """"seat cover for SRTWC8383-RL-MBL": whatever a bare product code gets."""
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="master_products", intent="check_product",
        typed=[COVER_ONE], asked=[WC_ONE],
        text_typed=COVER_ONE, text_asked=f"seat cover for {WC_ONE}",
    )
    assert "crm_find" not in _names(a_calls), a_calls
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_q4_a_file_ask_runs_the_attachment_path_on_the_cover_codes(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="product_attachment", intent="check_product_attachment",
        typed=[COVER_ONE], asked=[WC_ONE],
        text_typed=f"{COVER_ONE} photo", text_asked=f"{WC_ONE} seat cover photo",
    )
    assert "crm_find" not in _names(a_calls), a_calls
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_q2_a_product_with_no_link_beside_a_wc_answers_as_typed(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="inventory", intent="check_stock",
        typed=[COVER_ONE, OTHER], asked=[WC_ONE, OTHER],
        text_typed=f"stock {COVER_ONE} {OTHER}", text_asked=f"stock seat cover for {WC_ONE} and {OTHER}",
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_the_reverse_wc_for_a_seat_cover_resolves_its_wc(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, domain="inventory", intent="check_stock",
        typed=[WC_ONE], asked=[COVER_ONE],
        text_typed=f"stock {WC_ONE}", text_asked=f"stock WC for {COVER_ONE}",
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_q1_no_link_on_record_is_one_line_and_no_fetch(session_factory, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_NONE}", msg_id="ZZT-seat-none",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_NONE)], find=FITS,
    )
    assert calls == [], calls
    assert reply.strip() == find_lane.NO_SEAT_COVER_LINE.format(code=WC_NONE), reply


# --------------------------------------------------------------------------- the resolution step


def _scoped(session_factory):
    from app.models.base import set_company_scope

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    return db


def _verdict(*codes: str, find: Any = FITS, **extra: Any) -> dict[str, Any]:
    return {"domain_hint": "inventory", "find": find, "entities": [_product(c) for c in codes], **extra}


def _codes(verdict: dict[str, Any]) -> list[str]:
    return [e.get("canonical_code") for e in verdict.get("entities") or [] if e.get("hint") == "product"]


def test_r1_the_wc_code_becomes_its_cover_codes_in_code_order(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_TWO))
    assert _codes(out) == COVERS_TWO
    assert lines == []
    assert all(e["raw"] == e["canonical_code"] and e["current_message"] is True for e in out["entities"])
    assert out["domain_hint"] == "inventory", "the parser's domain is never touched"


def test_r2_a_typed_quantity_rides_on_each_cover_entity(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict()
    verdict["entities"] = [{**_product(WC_ONE), "quantity": 5}]
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert [(e["canonical_code"], e.get("quantity")) for e in out["entities"]] == [(COVER_ONE, 5)]


def test_r3_no_find_is_untouched(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=None)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert out is verdict and lines == []


def test_r4_q3_a_code_that_is_no_exact_product_goes_on_as_typed(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict("ZZTWC8383")  # a family prefix, no product of its own
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert _codes(out) == ["ZZTWC8383"] and lines == []


def test_r5_the_code_matches_by_the_normalised_code_not_by_spelling(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_ONE.lower().replace("-", " - ")))
    assert _codes(out) == [COVER_ONE]


def test_r6_q1_every_named_code_without_a_link_is_its_line(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_NONE, OTHER))
    assert lines == [
        find_lane.NO_SEAT_COVER_LINE.format(code=WC_NONE),
        find_lane.NO_SEAT_COVER_LINE.format(code=OTHER),
    ]


def test_r7_a_carried_product_is_no_anchor(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict()
    verdict["entities"] = [{**_product(WC_ONE), "current_message": False}]
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert _codes(out) == [COVER_ONE], "a carried WC asked 'which seat cover fits it' is the anchor too"


# --------------------------------------------------------------------------- no reply lane left


def test_k1_run_fetch_never_swaps_the_tool_for_crm_find() -> None:
    import inspect

    from app.services.chatbot import lanes

    source = inspect.getsource(lanes.business.run_fetch)
    assert "find_lane.TOOL" not in source and "find_override" not in source


def test_k2_the_structurer_has_no_find_formatter() -> None:
    from app.services.chatbot.lanes.business import fetch as fetch_mod

    assert not hasattr(fetch_mod, "_find_output")


def test_k3_the_parser_rule_keeps_the_domain_of_the_ask() -> None:
    from app.services import product_find

    rule = product_find.parser_vocabulary()
    assert "domain_hint is master_products" not in rule
    assert "Never emit it for facts about the named product itself" not in rule
