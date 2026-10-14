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
#: The parse of a fit ask: `value` is the word of THIS message naming the other side.
FITS = {"filters": [{"key": "fits", "value": "seat cover", "unit": None, "file_word": None}]}
#: A fit ask whose message also asks for a file ("<WC> seat cover photo").
FITS_PHOTO = {"filters": [{"key": "fits", "value": "seat cover", "unit": None, "file_word": "photo"}]}
#: The same filter with no other-side word in the message (a carried fit ask, or a leak).
FITS_UNSAID = {"filters": [{"key": "fits", "value": None, "unit": None, "file_word": None}]}

#: One envelope for every tool: what differs between two turns is then only what the lane
#: sent and how the existing structurer printed the same rows.
ENVELOPE = json.dumps(
    {
        "has_result": True,
        "response": "ZZT existing reply",
        "items": [{"product_code": COVER_ONE, "quantity_on_hand": 11}],
    }
)


def _seed_ids(session_factory) -> dict[str, str]:
    from app.models.product import Product

    db = _scoped(session_factory)
    return {code: str(pid) for pid, code in db.query(Product.id, Product.product_code).filter(Product.product_code.like("ZZT%"))}


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
        find=FITS_PHOTO,
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
        find={"filters": [{"key": "fits", "value": "WC", "unit": None, "file_word": None}]},
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


@pytest.mark.parametrize("domain,intent", [("inventory", "check_stock"), (None, None), ("master_products", "check_product")])
def test_q1_no_link_on_record_is_one_line_and_no_fetch(session_factory, monkeypatch, domain, intent) -> None:
    """Whatever domain the parser gave it: a bare code's null domain routes to the
    entities-only arm, which must not answer "I have <WC>" (reviewer blocker 1)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"seat cover for {WC_NONE}", msg_id="ZZT-seat-none",
        domain_hint=domain, intent_hint=intent, entities=[_product(WC_NONE)], find=FITS,
    )
    assert calls == [], calls
    assert reply.strip() == find_lane.NO_SEAT_COVER_LINE.format(code=WC_NONE), reply


@pytest.mark.parametrize("other", ["ZZTWC83", "ZZTWC8383"])
def test_q1_a_code_that_is_no_exact_product_sends_the_turn_on(session_factory, monkeypatch, other) -> None:
    """Q3 (a) beside a no-link WC: a typo is named by the existing miss line, a family prefix is
    placed by the resolver; neither is ever told "could not find" by this step (review round 2)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_NONE} and {other}", msg_id="ZZT-seat-typo",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_NONE), _product(other)],
        find=FITS,
    )
    assert calls, "the turn runs on to the existing stock lane"
    assert find_lane.NO_SEAT_COVER_LINE.format(code=WC_NONE) not in reply, reply
    if other == "ZZTWC83":
        assert other in reply, reply  # the existing miss line names the typo


def test_no_code_and_nothing_carried_asks_for_the_code(session_factory, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text="which seat cover fits my WC?", msg_id="ZZT-seat-nocode",
        domain_hint="master_products", intent_hint="check_product", entities=[], find=FITS,
    )
    assert calls == [], calls
    assert reply.strip() == find_lane.ASK_PRODUCT_LINE, reply


def test_which_seat_cover_fits_it_resolves_the_carried_wc(session_factory, monkeypatch) -> None:
    """Reviewer blocker 2: turn 1 asks about the WC, turn 2 "stock of its seat cover?" names no
    code. Turn 2 must fetch the COVER, exactly as "stock <cover>" after turn 1 does."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock {WC_ONE}", msg_id="ZZT-seat-carry-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_ONE)],
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_carry")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=f"stock {COVER_ONE}", msg_id="ZZT-seat-carry-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(COVER_ONE)],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_carry")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text="stock of its seat cover?", msg_id="ZZT-seat-carry-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[], find=FITS,
    )
    assert c_calls, "the control must fetch"
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


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


def test_r4_a_model_code_reads_as_its_linked_skus_and_a_stem_goes_on_as_typed(session_factory) -> None:
    """Owner 13 Oct ("8152 seat cover stock") revisits Q3 (a) for a model code only: the model's
    linked SKUs answer; a shorter stem ("ZZTWC83") or a typo is still the resolver's."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict("ZZTWC8383"))
    assert _codes(out) == [COVER_ONE]
    for stem in ("ZZTWC83", "ZZTWC838"):
        out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(stem))
        assert _codes(out) == [stem] and lines == [], stem

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


def test_r7_a_carried_product_in_the_parse_is_the_anchor(session_factory) -> None:
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


def test_k4_crm_find_is_no_chatbot_tool_any_more() -> None:
    """The MCP tool and route stay (a generic read); the chatbot neither lists nor routes it."""
    from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
    from app.services.mcp_tool_domains import CHATBOT_TOOL_DOMAINS

    assert "crm_find" not in CHATBOT_READ_ONLY_TOOLS
    assert "crm_find" not in CHATBOT_TOOL_DOMAINS


def test_e6_the_type_word_beside_the_wc_is_no_miss_and_no_narrowing(session_factory, monkeypatch) -> None:
    """Crew live check 13 Oct ("SRTWC8383-RL-MBL 配什么马桶盖" printed "找不到 马桶盖"): the class
    word the parser also emitted is no miss line: `turn_runtime.make_tool_runner`'s type-word
    filter drops it on a find turn (reviewer kill test: removing that filter turns this red)."""
    word = {"raw": "马桶盖", "hint": "category", "canonical_code": None, "current_message": True, "confident": True}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_word")
    control = _turn(
        session_factory, monkeypatch, text=COVER_ONE, msg_id="ZZT-seat-word-control",
        domain_hint="master_products", intent_hint="check_product", entities=[_product(COVER_ONE)],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_word")
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} 配什么马桶盖", msg_id="ZZT-seat-word-asked",
        domain_hint="master_products", intent_hint="check_product", entities=[_product(WC_ONE), word],
        find={"filters": [{"key": "fits", "value": "马桶盖", "unit": None, "file_word": None}]},
    )
    assert "找不到" not in reply and "could not find" not in reply.lower(), reply
    assert calls == control[1], (calls, control[1])


def test_r9_a_stored_code_in_another_spelling_still_anchors(session_factory) -> None:
    """The catalogue side is normalised too (`_norm_sql`, the indexed expression)."""
    from app.models.product_compatibility import ProductCompatibility
    from app.services.chatbot.lanes.business import find as find_lane
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    wc = seed_product(db, company_id=DEFAULT_COMPANY_ID, code="zztwc7001 - a")
    cover = seed_product(db, company_id=DEFAULT_COMPANY_ID, code="ZZTSCBD7001")
    db.add(ProductCompatibility(company_id=DEFAULT_COMPANY_ID, seat_cover_product_id=cover.id,
                                wc_product_id=wc.id, source="manual"))
    db.commit()
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict("ZZTWC7001-A"))
    assert _codes(out) == ["ZZTSCBD7001"]


def test_r10_no_code_in_the_parse_takes_the_carried_codes(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(), [WC_TWO])
    assert _codes(out) == COVERS_TWO and lines == []
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(), [])
    assert lines == [find_lane.ASK_PRODUCT_LINE]


def test_step7_a_bare_seat_cover_ask_continues_a_file_ask_like_the_bare_cover_code(session_factory, monkeypatch) -> None:
    """Crew live check 13 Oct, script step 7: engine parity for a file-domain fit ask after a file
    answer, GIVEN the parser's domain. Whether turn 2 continues the file ask is the parser's call;
    the live pair in `test_find_live_parser.py` covers that (reviewer kill test D)."""
    drawing = {"raw": "technical drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
               "current_message": True, "confident": True}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} technical drawing", msg_id="ZZT-seat-s7-1",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing],
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_s7")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=COVER_ONE, msg_id="ZZT-seat-s7-control",
        domain_hint="product_attachment", intent_hint="check_product_attachment", entities=[_product(COVER_ONE)],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_s7")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"seat cover for {WC_ONE}", msg_id="ZZT-seat-s7-2",
        domain_hint="product_attachment", intent_hint="check_product_attachment", entities=[_product(WC_ONE)],
        find=FITS,
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def _focus_codes(session_factory) -> list[str]:
    from tests.chatbot.test_outstanding_lane import _session_of

    focus = _session_of(session_factory).get("focus") or {}
    return [
        p.get("canonical_code") or p.get("code") for p in focus.get("products") or [] if isinstance(p, dict)
    ]


def test_c_after_a_seat_cover_answer_the_focus_holds_the_cover_codes(session_factory, monkeypatch) -> None:
    """Crew live check row 12: "only the 333A one" after "seat cover for SRTWC8122" jumped to the WC,
    while after the typed codes it narrows. The conversation's subject is what was answered."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_focus")
    _turn(
        session_factory, monkeypatch, text=f"stock {' '.join(COVERS_TWO)}", msg_id="ZZT-seat-focus-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(c) for c in COVERS_TWO],
    )
    control = _focus_codes(session_factory)
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_focus")
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-focus-asked",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    asked = _focus_codes(session_factory)
    assert control, "the control must settle a focus"
    assert asked == control, (asked, control)


# --------------------------------------------------------------------------- across turns (crew v94 check)


def test_x1_a_carried_relation_never_reaches_a_message_with_its_own_ask(session_factory) -> None:
    """Row 7: after a seat-cover exchange, "SRTWC8383-RL-MBL technical drawing" (its own ask, no
    other side said) was answered for the cover. The parser's own flags decide: nothing said,
    and a domain word in the message."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=FITS_UNSAID, domain_in_message=True)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out == {**verdict, "find": None} and lines == []


def test_x2_pointing_at_an_answered_cover_is_a_narrowing_not_a_fit_ask(session_factory) -> None:
    """Row 12: "only the 333A one" after the covers were answered. A carried relation does not
    re-apply to a code the conversation just answered (it is on the focus)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(COVERS_TWO[1], find=FITS_UNSAID, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out == {**verdict, "find": None} and lines == []


def test_x3_a_bare_new_code_after_a_fit_ask_carries_the_fit_ask(session_factory) -> None:
    """Crew ruling 13 Oct: "SRTWC8122 seat cover" then "SRTWC8383-RL-MBL" is the same ask for the
    new WC; the parser carries `fits` (BARE ENTITY CONTINUATION) with nothing said."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=FITS_UNSAID, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert _codes(out) == [COVER_ONE] and lines == []


def test_x4_said_in_this_message_applies_whatever_the_flags(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, domain_in_message=True)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict, [WC_ONE])
    assert _codes(out) == [COVER_ONE]


def test_x5_the_carry_through_the_engine_answers_the_new_wcs_cover(session_factory, monkeypatch) -> None:
    """Two turns, real resolver: the bare WC after a seat-cover answer fetches ITS cover, exactly as
    typing the cover code after that answer does."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-x5-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_x5")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=COVER_ONE, msg_id="ZZT-seat-x5-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(COVER_ONE)],
        domain_in_message=False,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_x5")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=WC_ONE, msg_id="ZZT-seat-x5-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_ONE)],
        find=FITS_UNSAID, domain_in_message=False,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_x6_narrowing_through_the_engine_keeps_the_answered_cover(session_factory, monkeypatch) -> None:
    """Row 12 end to end: a leaked `fits` on "only the 333A one" must fetch the 333A cover itself,
    never swap it for its WC."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-x6-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    _reply, calls = _turn(
        session_factory, monkeypatch, text="only the 333A one", msg_id="ZZT-seat-x6-2",
        domain_hint="inventory", intent_hint="check_stock",
        entities=[{**_product(COVERS_TWO[1]), "current_message": True}], find=FITS_UNSAID,
        domain_in_message=False, entity_op="replace",
    )
    ids = _seed_ids(session_factory)
    assert calls and calls[-1][1].get("product_ids") == [ids[COVERS_TWO[1]]], calls


def test_y1_the_carry_holds_when_the_parse_dropped_find(session_factory) -> None:
    """Live: on the bare WC after a fit answer the parser drops `find` 2 of 5. Its own continuation
    flag (no domain word, only a code) plus the conversation's carried relation decide (#1626)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=None, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert _codes(out) == [COVER_ONE] and lines == []


def test_y2_no_carry_onto_a_message_with_its_own_domain_word(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=None, domain_in_message=True)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out is verdict


def test_y3_no_carry_beside_another_entity_kind(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, find=None, domain_in_message=False)
    verdict["entities"].append({"raw": "KL", "hint": "warehouse", "current_message": True})
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out is verdict


def test_y4_an_answered_cover_is_never_re_anchored_even_with_a_said_value(session_factory) -> None:
    """Live: "only the 333A one" got `fits` with a made-up value 2 of 5."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(COVERS_TWO[1], domain_in_message=False)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out == {**verdict, "find": None}


def test_y5_a_value_less_fits_with_no_fit_answer_before_is_no_fit_ask(session_factory) -> None:
    """Live (13 Oct): "send me SRTWC8383-RL-MBL drawing" came back with `fits`, value null, 3 of 3;
    every real fit ask filled `value`. With no fit answer carried, such a parse is a plain turn."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    for flag in (True, False, None):
        verdict = _verdict(WC_TWO, find=FITS_UNSAID, domain_in_message=flag)
        out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, [OTHER])
        assert out == {**verdict, "find": None} and lines == [], flag

def test_y6_the_relation_rides_the_focus_and_its_wire(session_factory, monkeypatch) -> None:
    from tests.chatbot.test_outstanding_lane import _session_of

    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-y6-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    assert (_session_of(session_factory).get("focus") or {}).get("relation") == "covers"
    _turn(
        session_factory, monkeypatch, text=f"stock {OTHER}", msg_id="ZZT-seat-y6-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(OTHER)], domain_in_message=True,
    )
    assert (_session_of(session_factory).get("focus") or {}).get("relation") is None


def test_y7_the_carry_through_the_engine_when_the_parse_dropped_find(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-y7-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_y7")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=COVER_ONE, msg_id="ZZT-seat-y7-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(COVER_ONE)], domain_in_message=False,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_y7")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=WC_ONE, msg_id="ZZT-seat-y7-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_ONE)], domain_in_message=False,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_y8_narrowing_with_a_made_up_value_keeps_the_answered_cover(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-y8-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    _reply, calls = _turn(
        session_factory, monkeypatch, text="only the 333A one", msg_id="ZZT-seat-y8-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(COVERS_TWO[1])], find=FITS,
        domain_in_message=False, entity_op="replace",
    )
    ids = _seed_ids(session_factory)
    assert calls and calls[-1][1].get("product_ids") == [ids[COVERS_TWO[1]]], calls


def test_y9_a_bare_cover_code_after_a_seat_cover_answer_is_that_cover(session_factory) -> None:
    """The carried direction holds: after "seat cover for <WC>", a bare cover code is the cover
    itself, never the WCs it fits (the control of the carry parity tests)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(COVER_ONE, find=None, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert _codes(out) == [COVER_ONE] and lines == []


def test_a_value_less_fits_on_a_wc_file_ask_answers_the_wc_itself(session_factory, monkeypatch) -> None:
    """The live shape end to end: the WC's own drawing ask with a noise `fits` is the plain turn."""
    drawing = {"raw": "drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
               "current_message": True, "confident": True}
    noise = {"filters": [{"key": "fits", "value": None, "unit": None, "file_word": "drawing"}]}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_noise")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=f"send me {WC_ONE} drawing", msg_id="ZZT-seat-noise-control",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_noise")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"send me {WC_ONE} drawing", msg_id="ZZT-seat-noise-asked",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True, find=noise,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)

def _relation(session_factory):
    from tests.chatbot.test_outstanding_lane import _session_of

    return (_session_of(session_factory).get("focus") or {}).get("relation")


def test_z1_a_fit_ask_with_a_topic_reset_still_records_its_relation(session_factory, monkeypatch) -> None:
    """Reviewer round 4 blocker 2: APPLY rebuilds the focus on a reset, so the relation is written
    onto the turn's OWN focus, after APPLY."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-z1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
        topic_reset=True,
    )
    assert _relation(session_factory) == "covers"


def test_z2_a_new_ask_of_its_own_ends_the_relation(session_factory, monkeypatch) -> None:
    """Reviewer round 4 should-fix 3: a turn with its own domain word that links nothing changes
    the subject; a bare code typed after it is no seat-cover ask."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-z2-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    _turn(
        session_factory, monkeypatch, text="any promotion now?", msg_id="ZZT-seat-z2-2",
        domain_hint="promotion", intent_hint="check_promotion", entities=[], domain_in_message=True,
    )
    assert _relation(session_factory) is None


def test_z3_a_follow_up_naming_nothing_keeps_the_relation(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_TWO}", msg_id="ZZT-seat-z3-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_TWO)], find=FITS,
    )
    _turn(
        session_factory, monkeypatch, text="how many?", msg_id="ZZT-seat-z3-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[], domain_in_message=False,
    )
    assert _relation(session_factory) == "covers"


def test_z4_row7_a_carried_relation_never_reaches_a_message_with_its_own_ask(session_factory, monkeypatch) -> None:
    """Crew row 7 end to end: after a seat-cover answer, "<WC> technical drawing" with a leaked,
    unsaid `fits` is that WC's own drawing, exactly as with no `find` at all."""
    drawing = {"raw": "technical drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
               "current_message": True, "confident": True}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"stock seat cover for {WC_ONE}", msg_id="ZZT-seat-z4-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_ONE)], find=FITS,
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_z4")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} technical drawing", msg_id="ZZT-seat-z4-control",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_z4")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} technical drawing", msg_id="ZZT-seat-z4-2",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True, find=FITS_UNSAID,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


# --------------------------------------------------------------------------- crew v94 check 2 (13 Oct)

INSTALL = {"raw": "seat cover", "hint": "attachment_type", "canonical_code": "installation guide",
           "current_message": True, "confident": True}


def test_f1_no_file_word_drops_the_attachment_type_and_the_file_domain(session_factory) -> None:
    """v94 reads "seat cover" as a file ask (Installation Guide, "Which kind of file?"). The parser's
    own `file_word` says THIS message asked for no file, so the turn is a bare code."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, domain_hint="product_attachment", intent_hint="check_product_attachment")
    verdict["entities"].append(dict(INSTALL))
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert _codes(out) == [COVER_ONE] and lines == []
    assert [e for e in out["entities"] if e.get("hint") == "attachment_type"] == []
    assert out["domain_hint"] is None and out["intent_hint"] is None


def test_f2_a_file_word_keeps_the_file_ask(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    photo = {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo", "current_message": True}
    verdict = _verdict(WC_ONE, find=FITS_PHOTO, domain_hint="product_attachment", intent_hint="check_product_attachment")
    verdict["entities"].append(photo)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert out["domain_hint"] == "product_attachment" and photo in out["entities"]


def test_f3_no_file_word_leaves_a_non_file_domain_alone(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_ONE, intent_hint="check_stock"))
    assert out["domain_hint"] == "inventory" and out["intent_hint"] == "check_stock"


def test_f4_cold_seat_cover_misread_as_a_file_ask_answers_as_the_bare_cover(session_factory, monkeypatch) -> None:
    """Crew row 4 end to end: the misread parse (Installation Guide, file domain) answers exactly as
    the bare cover code does on a fresh contact."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_f4")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=COVER_ONE, msg_id="ZZT-seat-f4-control",
        domain_hint=None, intent_hint=None, entities=[_product(COVER_ONE)], domain_in_message=False,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_f4")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} seat cover", msg_id="ZZT-seat-f4-asked",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), dict(INSTALL)], find=FITS, domain_in_message=True,
    )
    assert "Installation Guide" not in a_reply and "kind of file" not in a_reply, a_reply
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_f5_narrowing_on_a_partial_code_is_the_plain_turn(session_factory, monkeypatch) -> None:
    """Row 12: "only the 333A one" (parsed "333A", not an exact product) after a seat-cover answer
    with a leaked `fits`: a carried relation that replaces nothing leaves a plain turn."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict("333A", find=FITS_UNSAID, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, COVERS_TWO, carried_relation="covers")
    assert out.get("find") is None and lines == []


def test_f6_photo_answering_the_file_question_stays_on_the_cover(session_factory, monkeypatch) -> None:
    """Row 13: after "<WC> seat cover files" answered for the cover, the reply "photo" parses with
    the WC echoed from history (current_message false). The echo is the relation's anchor, so it
    resolves to the cover, exactly as when the cover code itself is echoed."""
    photo = {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo", "current_message": True,
             "confident": True}
    files = {"filters": [{"key": "fits", "value": "seat cover", "unit": None, "file_word": "files"}]}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"{WC_ONE} seat cover files", msg_id="ZZT-seat-f6-1",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE)], find=files,
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_f6")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text="photo", msg_id="ZZT-seat-f6-control",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[{**_product(COVER_ONE), "current_message": False}, photo], domain_in_message=True,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_f6")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text="photo", msg_id="ZZT-seat-f6-2",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[{**_product(WC_ONE), "current_message": False}, photo], domain_in_message=True,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


def test_y10_a_value_that_is_the_file_word_is_no_fit_ask(session_factory) -> None:
    """Live: "send me SRTWC8383-RL-MBL drawing" once parsed with value "drawing" = file_word."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    noise = {"filters": [{"key": "fits", "value": "drawing", "unit": None, "file_word": "Drawing"}]}
    verdict = _verdict(WC_ONE, find=noise, domain_in_message=True)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert out == {**verdict, "find": None} and lines == []


def test_y11_a_value_the_message_does_not_hold_is_not_said(session_factory) -> None:
    """Live: "send me SRTWC8383-RL-MBL drawing" came back with value "seat cover" 2 of 3, a word
    the message does not hold. The rule tells the parser to copy the word from THIS message; a
    copy the message does not hold is no copy (the `grounding._as_typed` precedent)."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_ONE, domain_in_message=True)
    out, lines = find_lane.as_linked_products(
        _scoped(session_factory), verdict, message=f"send me {WC_ONE} drawing"
    )
    assert out == {**verdict, "find": None} and lines == []
    out, _lines = find_lane.as_linked_products(
        _scoped(session_factory), verdict, message=f"stock Seat Cover for {WC_ONE}"
    )
    assert _codes(out) == [COVER_ONE]


def test_y12_no_code_with_a_domain_word_of_its_own_is_no_fit_question(session_factory) -> None:
    """Live: "seat cover got promotion?" got `fits` 1 of 3; with no code, the ask-for-code line is
    for a fit question only, and the parser marks this one as having its own domain word."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(domain_hint="promotion", domain_in_message=True)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict)
    assert out == {**verdict, "find": None} and lines == []


def test_y13_the_engine_checks_the_value_against_the_message(session_factory, monkeypatch) -> None:
    drawing = {"raw": "drawing", "hint": "attachment_type", "canonical_code": "technical drawing",
               "current_message": True, "confident": True}
    noise = {"filters": [{"key": "fits", "value": "seat cover", "unit": None, "file_word": "drawing"}]}
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_y13")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=f"send me {WC_ONE} drawing", msg_id="ZZT-seat-y13-control",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_y13")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"send me {WC_ONE} drawing", msg_id="ZZT-seat-y13-asked",
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[_product(WC_ONE), drawing], domain_in_message=True, find=noise,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)


# --------------------------------------------------------------------------- owner 13 Oct: "8152 seat cover stock"


def _seed_family(session_factory) -> None:
    """SRTWC8152 / SRTWC8152-BL on the flyer: two WC SKUs of one model, each with its own cover."""
    from app.models.product_compatibility import ProductCompatibility
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    rows = {c: seed_product(db, company_id=DEFAULT_COMPANY_ID, code=c)
            for c in ("ZZTWC9152", "ZZTWC9152-BL", "ZZTSCBD920", "ZZTSCBD924-BL")}
    for cover, wc in (("ZZTSCBD920", "ZZTWC9152"), ("ZZTSCBD924-BL", "ZZTWC9152-BL")):
        db.add(ProductCompatibility(company_id=DEFAULT_COMPANY_ID, seat_cover_product_id=rows[cover].id,
                                    wc_product_id=rows[wc].id, source="manual"))
    db.commit()


def test_g1_a_partial_model_code_resolves_through_the_linked_codes_it_is_part_of(session_factory) -> None:
    """The owner typed "8152 seat cover stock": no product is 8152, but the WC SKUs on a seat-cover
    link whose code holds it are the model he means. Codes against codes, links only."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed_family(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict("9152"), message="9152 seat cover stock")
    assert _codes(out) == ["ZZTSCBD920", "ZZTSCBD924-BL"] and lines == []


def test_g2_a_token_held_by_no_linked_code_goes_on_as_typed(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    _seed_family(session_factory)
    verdict = _verdict("ZZTNOPE7")
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, message="ZZTNOPE7 seat cover")
    assert _codes(out) == ["ZZTNOPE7"] and lines == []


def test_g3_the_partial_code_through_the_engine_answers_the_covers_stock(session_factory, monkeypatch) -> None:
    from tests.chatbot.test_outstanding_lane import _run_turn  # noqa: F401

    _seed_family(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_seat_g3")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text="stock ZZTSCBD920 ZZTSCBD924-BL", msg_id="ZZT-seat-g3-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product("ZZTSCBD920"), _product("ZZTSCBD924-BL")],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_seat_g3")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text="9152 seat cover stock", msg_id="ZZT-seat-g3-asked",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product("9152")], find=FITS,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)
