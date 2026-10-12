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
