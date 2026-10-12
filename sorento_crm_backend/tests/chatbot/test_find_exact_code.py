"""CHAT-FIND round 3, eval case chatfind-07: the exact code the customer typed wins.

Crew eval gate on 3df87cee: a dealer asked "seat cover for SRTWC8088" and was answered about
SRTWC8088-SC. `narrow_code_tokens` (`product_code_family.py`) read the words "seat cover" on the
code's line as a variant descriptor and swapped the typed code for its -SC sibling before any
resolver tier ran. On a find those words name the relation's target, not a variant, so a find
turn's resolve body carries `keep_typed_codes` and the route leaves the typed codes alone; every
other ask keeps the word narrowing (PR #833's owner rule). `find.named_this_turn` takes the
exact code first, a code it merely contains only when nothing matches exactly.
Fake ZZT codes only.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chatbot.lanes.business import find as find_lane

BASE = "ZZTWC8088"
SPARE = "ZZTWC8088-SC"


def _scoped(session_factory):
    from app.models.base import set_company_scope
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    return db


def _seed(session_factory) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    seed_product(db, company_id=DEFAULT_COMPANY_ID, code=BASE).product_name = "ZZT WATER CLOSET"
    seed_product(db, company_id=DEFAULT_COMPANY_ID, code=SPARE).product_name = "ZZT SEAT COVER"
    db.commit()


def _payload(**extra):
    from app.api.v1.system.references import ResolveReferenceRequest

    return ResolveReferenceRequest(query=f"seat cover for {BASE}", tokens=[BASE], **extra)


def test_e1_without_the_flag_the_words_still_pick_the_variant(session_factory) -> None:
    """The control: PR #833's word narrowing is unchanged for every other ask."""
    from app.api.v1.system.references import _narrowed_payload

    _seed(session_factory)
    assert _narrowed_payload(_scoped(session_factory), _payload()).tokens == [SPARE]


def test_e2_a_find_turn_keeps_the_typed_code(session_factory) -> None:
    from app.api.v1.system.references import _narrowed_payload

    _seed(session_factory)
    out = _narrowed_payload(_scoped(session_factory), _payload(keep_typed_codes=True))
    assert out.tokens == [BASE]


def test_e3_the_find_lane_sends_the_flag_and_other_asks_do_not() -> None:
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    def ctx(find):
        return {
            "parse": {
                "output": {
                    "entities": [{"raw": BASE, "hint": "product", "canonical_code": BASE, "current_message": True}],
                    "domain_hint": "master_products",
                    "find": find,
                }
            },
            "contact": {"id": "c1"},
            "message": {"text": f"seat cover for {BASE}"},
        }

    assert resolve_entity_body(ctx({"filters": [{"key": "fits", "value": None, "unit": None}]})).get(
        "keep_typed_codes"
    ) is True
    assert "keep_typed_codes" not in resolve_entity_body(ctx(None))


def _seed_specs(session_factory) -> None:
    """The two products carry a derived product_type, so a grounded spec ("seat cover" =
    toilet_seat, `product_spec_registry`) can pick the -SC sibling by VALUE."""
    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    db = _scoped(session_factory)
    for code, kind in ((BASE, "water_closet"), (SPARE, "toilet_seat")):
        pid = db.query(Product.id).filter(Product.product_code == code).scalar()
        db.add(ProductSpecifications(product_id=pid, values={"product_type": {"value": kind}}))
    db.commit()


SPECS = [{"key": "product_type", "value": "toilet_seat"}]


def test_e8_without_the_flag_a_grounded_spec_still_picks_the_variant(session_factory) -> None:
    """Reviewer round 3, should-fix 1, the control: the spec swap (`narrow_code_tokens_by_specs`)
    is PR #1513's owner rule for every other ask."""
    from app.api.v1.system.references import _narrowed_payload

    _seed(session_factory)
    _seed_specs(session_factory)
    from app.api.v1.system.references import ResolveReferenceRequest

    payload = ResolveReferenceRequest(query=BASE, tokens=[BASE], extracted_specs=SPECS)
    assert _narrowed_payload(_scoped(session_factory), payload).tokens == [SPARE]


def test_e9_a_find_turn_keeps_the_typed_code_against_a_grounded_spec_too(session_factory) -> None:
    from app.api.v1.system.references import ResolveReferenceRequest, _narrowed_payload

    _seed(session_factory)
    _seed_specs(session_factory)
    payload = ResolveReferenceRequest(query=BASE, tokens=[BASE], extracted_specs=SPECS, keep_typed_codes=True)
    assert _narrowed_payload(_scoped(session_factory), payload).tokens == [BASE]


def _ent(uuid, code):
    return {"uuid": uuid, "entity_type": "product", "code": code, "canonical_code": code}


def test_e4_named_this_turn_takes_the_exact_code_over_a_longer_one() -> None:
    parse = {"entities": [{"raw": "zztwc8088", "hint": "product", "canonical_code": "zztwc8088", "current_message": True}]}
    entities = [_ent("a", SPARE), _ent("b", BASE)]
    assert [e["uuid"] for e in find_lane.named_this_turn(parse, entities)] == ["b"]


def test_e5_a_typed_prefix_with_no_exact_row_still_matches_by_containment() -> None:
    parse = {"entities": [{"raw": "zztwc8088", "hint": "product", "canonical_code": "zztwc8088", "current_message": True}]}
    entities = [_ent("a", SPARE)]
    assert [e["uuid"] for e in find_lane.named_this_turn(parse, entities)] == ["a"]


# --------------------------------------------------------------------------- the stray miss line

FIND_TEXT = "Seat cover for SRTWT7445:\nCode: SRTSCBD823-MBL\nStock: 12"


def _find_turn(session_factory, monkeypatch, *, extra_entities, msg_id):
    from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID, _qf, _run_turn, _seed_contact

    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_qf(
            domain_hint="master_products",
            intent_hint="check_product",
            find={"filters": [{"key": "fits", "value": None, "unit": None}]},
            entities=[
                {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": PRODUCT_CODE,
                 "current_message": True, "confident": True},
                *extra_entities,
            ],
        ),
        text_body=f"{PRODUCT_CODE} 配什么马桶盖",
        msg_id=msg_id,
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=__import__("json").dumps({"response": FIND_TEXT, "has_result": True, "result_type": "find"}),
    )
    return (result.reply or {}).get("text") or "", captured


def test_e6_a_type_word_the_parser_also_emitted_is_no_miss_line(session_factory, monkeypatch) -> None:
    """Crew live check: "SRTWC8383-RL-MBL 配什么马桶盖" printed a stray "找不到 马桶盖。"."""
    reply, captured = _find_turn(
        session_factory,
        monkeypatch,
        extra_entities=[{"raw": "马桶盖", "hint": "category", "canonical_code": None,
                         "current_message": True, "confident": True}],
        msg_id="ZZT-find-stray-1",
    )
    assert captured and captured[0][0] == "crm_find", captured
    # The message is Chinese, so the reply is too: the head line names 马桶盖 itself.
    assert reply.splitlines()[:3] == ["SRTWT7445 适配的马桶盖：", "代码: SRTSCBD823-MBL", "库存: 12"], reply
    assert "找不到" not in reply and "could not find" not in reply.lower(), reply


def test_e7_a_mistyped_second_code_is_still_named(session_factory, monkeypatch) -> None:
    reply, _captured = _find_turn(
        session_factory,
        monkeypatch,
        extra_entities=[{"raw": "ZZTNOPE999", "hint": "product", "canonical_code": "ZZTNOPE999",
                         "current_message": True, "confident": True}],
        msg_id="ZZT-find-stray-2",
    )
    assert "ZZTNOPE999" in reply, reply
