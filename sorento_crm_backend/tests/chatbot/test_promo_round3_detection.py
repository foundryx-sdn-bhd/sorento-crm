"""PROMO-SUBJECT-FILTER round 3 red tests: detection consistency (AC-29 to AC-35).

Owner rulings 9 Oct 2026: the parser is told the live lists and answers from them; code only
checks membership (exact); one builder feeds the stamp and the pick; same intent, same result.
Route-backed MCP (real presenter), the REAL resolver route over the seeded DB. Fake names only.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import Promotion, PromotionGroup, PromotionType
from app.services.chatbot.lanes.business.services import ResolveGateServices, _resolve_entity
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_brand_category import _brand, _prod
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, TODAY, _promo
from tests.chatbot.test_promo_owner_round2 import _mcp, _turn
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf

LEVELS = ["Sorento Office", "Sorento Dealer", "End User"]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _types(db):
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, expired_max_age_days=365, match_markers=[], match_priority=90, is_default=True)
    flyer = PromotionType(type_code="a3_flyer", type_name="A3 Flyer", show_expired=True, expired_max_age_days=180, match_markers=["a3"], match_priority=40, is_default=False)
    db.add_all([std, flyer])
    db.add_all(
        [
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()
    return std, flyer


def _unlinked_promo(db, name, ptype, *, levels):
    p = Promotion(
        id=str(uuid.uuid4()), description=name, start_date=TODAY - timedelta(days=5), end_date=TODAY + timedelta(days=30),
        is_active=True, access_levels=list(levels), promotion_type_id=ptype.id,
    )
    db.add(p)
    db.flush()
    db.add(PromotionGroup(promotion_id=p.id, group_name="Default", sort_order=0))
    db.flush()
    return p


def _world(session_factory, *, extra=None):
    """Sorento + Mocha brands, class Grab Bar, one grab bar product per brand."""
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Grab Bar")
    db = session_factory()
    std, flyer = _types(db)
    srt, mocha = _brand(db, "Sorento"), _brand(db, "Mocha")
    srt_bar, mocha_bar = _prod(db, srt, "Grab Bar", "ZZTSB1"), _prod(db, mocha, "Grab Bar", "ZZTMB1")
    w = {"std": std, "flyer": flyer, "srt": srt, "mocha": mocha, "srt_bar": srt_bar, "mocha_bar": mocha_bar, "db": db}
    if extra:
        extra(w)
    db.commit()
    return w


def _wire(session_factory, bodies: list | None = None, names=LEVELS) -> ResolveGateServices:
    """The REAL resolver route over the seeded DB; optionally records each request body."""
    inner = _resolve_entity(session_factory())

    def _call(body: dict[str, Any]) -> dict[str, Any]:
        if bodies is not None:
            bodies.append(body)
        return inner(body)

    return ResolveGateServices(access_types=lambda **_: [{"name": n} for n in names], resolve_entity=_call, probe=lambda **_: None)


def _ask(brand_raw=None, brand_canonical=None, subject="grab bar", kind=None, extra_entities=()):
    ents = []
    if brand_raw:
        ents.append({"raw": brand_raw, "hint": "brand", "canonical_code": brand_canonical, "current_message": True, "confident": True})
    if subject:
        ents.append({"raw": subject, "hint": "category", "canonical_code": None, "current_message": True, "confident": True})
    ents.extend(extra_entities)
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents, promotion_kind=kind)


def _go(session_factory, monkeypatch, qf, text, msg_id, bodies=None):
    svc = _wire(session_factory, bodies)
    return _turn(session_factory, monkeypatch, svc, qf, text, msg_id)


# --------------------------------------------------------------------------- #
# AC-29: the parser is told the live lists
# --------------------------------------------------------------------------- #


def test_the_parser_context_lists_live_brands_product_types_and_promotion_kinds_from_the_db(session_factory, monkeypatch):
    """AC-29: a brand the code has never heard of ("ZZT Brandex"), a product type name and a kind
    seeded as rows reach the parser's user block: three lists, table-driven."""

    def extra(w):
        _brand(w["db"], "ZZT Brandex")

    _world(session_factory, extra=extra)
    _mcp(monkeypatch, session_factory)
    blocks: list[str] = []
    svc = _wire(session_factory)
    _run_turn(session_factory, monkeypatch, qf=_ask("Sorento", "Sorento"), text_body="sorento grab bar promotion", msg_id="zzt-r3-ctx", resolve_services=svc, capture_user_block=blocks)
    block = "\n".join(blocks)
    brands = next((l for l in block.splitlines() if l.startswith("Known brands:")), "")
    assert "ZZT Brandex" in brands and "Sorento" in brands, block
    kinds = next((l for l in block.splitlines() if l.startswith("Promotion kinds:")), "")
    assert "a3_flyer (A3 Flyer)" in kinds and "standard (Standard Promo)" in kinds, block
    types = next((l for l in block.splitlines() if l.lower().startswith("product types:")), "")
    assert "Grab Bar" in types, block


def test_the_parser_prompt_tells_it_to_answer_from_the_lists_fix_typos_and_mark_unplaceable_words():
    """AC-29: the promotion sections of the parser prompt (each cut at its own end) name the three
    lists, say to return listed names, correct a typo to the listed name, and mark unplaceable
    words unresolved."""
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as prompt

    sections: list[str] = []
    for chunk in prompt.split("\n== ")[1:]:
        head, _, body = chunk.partition("\n")
        if head.upper().startswith("PROMOTION"):
            sections.append(body.split("\n━")[0])
    text = "\n".join(sections).lower()
    assert text, "no PROMOTION section in the parser prompt"
    for needed in ("known brands", "product types", "promotion kinds", "typo", "unresolved"):
        assert needed in text, (needed, text)


# --------------------------------------------------------------------------- #
# AC-30 / AC-31: no substring widening, unresolved brand asks back
# --------------------------------------------------------------------------- #


def test_an_unlisted_brand_word_asks_back_and_never_widens_or_fetches(session_factory, monkeypatch):
    """AC-30/31: parser canonical null, raw "Sorneto": no tier_gate substring match, no menu, no
    fetch. The reply is `I could not match "Sorneto" to a brand.`"""
    _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    reply = _go(session_factory, monkeypatch, _ask("Sorneto", None), "Sorneto grab bar promotion", "zzt-r3-unres")
    assert reply.startswith('I could not match "Sorneto" to a brand.'), reply
    assert "Which access level" not in reply, reply
    assert not [a for n, a in captured if n == PROMO_TOOL], captured


def test_a_listed_brand_name_from_the_parser_proceeds_narrowed_to_that_brand(session_factory, monkeypatch):
    """AC-30/31: the parser corrected the typo to the listed name: the ask proceeds, narrowed."""
    def extra(w):
        _promo(w["db"], "ZZT SORENTO GRAB BAR LIVE", w["std"], w["srt_bar"], levels=["sorento_dealer"])

    w = _world(session_factory, extra=extra)
    captured = _mcp(monkeypatch, session_factory)
    reply = _go(session_factory, monkeypatch, _ask("Sorneto", "Sorento"), "Sorneto grab bar promotion", "zzt-r3-listed")
    assert reply.startswith("Which access level"), reply
    probes = [a for n, a in captured if n == PROMO_TOOL]
    assert probes and all(a.get("brand_ids") == [str(w["srt"].id)] for a in probes), probes


# --------------------------------------------------------------------------- #
# shared seed for AC-33/34/35 and AC-32
# --------------------------------------------------------------------------- #


def _expired_world(session_factory):
    def extra(w):
        db, std, flyer, sb, mb = w["db"], w["std"], w["flyer"], w["srt_bar"], w["mocha_bar"]
        ended = TODAY - timedelta(days=30)
        # Dealer: only EXPIRED Sorento grab bar rows (two standard, same end date; one A3 flyer)
        _promo(db, "ZZT SORENTO GRAB BAR EXP A", std, sb, levels=["sorento_dealer"], start=TODAY - timedelta(days=120), end=ended)
        _promo(db, "ZZT SORENTO GRAB BAR EXP B", std, sb, levels=["sorento_dealer"], start=TODAY - timedelta(days=110), end=ended)
        _promo(db, "ZZT SORENTO A3 FLYER GRAB EXP", flyer, sb, levels=["sorento_dealer"], start=TODAY - timedelta(days=90), end=TODAY - timedelta(days=20))
        # Office: one LIVE and one expired of the same ask
        _promo(db, "ZZT SORENTO GRAB BAR LIVE", std, sb, levels=["sorento_office"])
        _promo(db, "ZZT SORENTO GRAB BAR OFFICE EXP", std, sb, levels=["sorento_office"], start=TODAY - timedelta(days=100), end=ended)
        # Mocha: a LIVE A3 flyer at Dealer (mixed brand) must hide nothing of Sorento's
        _promo(db, "ZZT MOCHA A3 FLYER LIVE", flyer, mb, levels=["sorento_dealer"])

    return _world(session_factory, extra=extra)


# --------------------------------------------------------------------------- #
# AC-33 / AC-34: one builder, expired rule, stamp wording
# --------------------------------------------------------------------------- #


def test_stamp_and_pick_agree_expired_only_dealer_live_office_and_expired_hidden_beside_live(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    _expired_world(session_factory)
    _mcp(monkeypatch, session_factory)
    menu = _go(session_factory, monkeypatch, _ask("Sorento", "Sorento"), "sorento grab bar promotion", "zzt-r3-exp-ask")
    lines = menu.splitlines()
    assert "1. Office - has promotion" in lines, menu
    assert "2. Dealer - has promotion" in lines, menu
    assert "3. End user - no promotion" in lines, menu
    dealer = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-exp-2")
    for name in ("ZZT SORENTO GRAB BAR EXP A", "ZZT SORENTO GRAB BAR EXP B", "ZZT SORENTO A3 FLYER GRAB EXP"):
        assert name in dealer, dealer
    assert dealer.count("(EXPIRED)") == 3, dealer
    assert "MOCHA" not in dealer.upper(), dealer
    office = _go(session_factory, monkeypatch, _pick_qf(1), "1", "zzt-r3-exp-1")
    assert "ZZT SORENTO GRAB BAR LIVE" in office, office
    assert "OFFICE EXP" not in office and "(EXPIRED)" not in office, office


def test_a_live_other_brand_flyer_does_not_hide_the_expired_sorento_flyer(session_factory, monkeypatch):
    """AC-33 mixed brand: live Mocha A3 flyer at Dealer, expired Sorento A3 flyer: the Sorento
    flyer ask still serves it (promotion_serving hides it today because the candidate set mixes brands)."""
    _expired_world(session_factory)
    _mcp(monkeypatch, session_factory)
    qf = _ask("Sorento", "Sorento", subject=None, kind="a3_flyer")
    _go(session_factory, monkeypatch, qf, "sorento a3 flyer", "zzt-r3-mix-ask")
    reply = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-mix-2")
    assert "ZZT SORENTO A3 FLYER GRAB EXP" in reply and "(EXPIRED)" in reply, reply
    assert "MOCHA" not in reply.upper(), reply


# --------------------------------------------------------------------------- #
# AC-35: same intent, same result
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,raw",
    [("Sorneto grab bar promotion", "Sorneto"), ("sorento grab bar promotion", "sorento"), ("SORENTO grab bar promotion", "SORENTO")],
)
def test_typo_and_case_variants_give_the_one_same_reply_on_both_turns(session_factory, monkeypatch, text, raw):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """Each variant is run on its own fresh world and must produce the SAME fixed reply, so the
    three are byte-identical to one another by construction (no carried state between them)."""
    _expired_world(session_factory)
    _mcp(monkeypatch, session_factory)
    menu = _go(session_factory, monkeypatch, _ask(raw, "Sorento"), text, "zzt-r3-same-ask")
    assert menu.splitlines()[:4] == [
        "Which access level do you need for Sorento, grab bar?",
        "1. Office - has promotion",
        "2. Dealer - has promotion",
        "3. End user - no promotion",
    ], menu
    pick = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-same-2")
    names = [n for n in ("ZZT SORENTO GRAB BAR EXP A", "ZZT SORENTO GRAB BAR EXP B", "ZZT SORENTO A3 FLYER GRAB EXP") if n in pick]
    assert len(names) == 3 and pick.count("(EXPIRED)") == 3, pick
    assert "MOCHA" not in pick.upper(), pick


# --------------------------------------------------------------------------- #
# AC-32: brand = brand of linked products
# --------------------------------------------------------------------------- #


def test_brand_is_the_brand_of_linked_products_never_company_level_or_unlinked(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    def extra(w):
        db, std, flyer = w["db"], w["std"], w["flyer"]
        # stored under the Sorento company in the data, but its products are Mocha's
        _promo(db, "ZZT MOCHA PRODUCTS STD", std, w["mocha_bar"], levels=["sorento_dealer"])
        _promo(db, "ZZT MOCHA FLYER SHARED DEALER", flyer, w["mocha_bar"], levels=["sorento_dealer"])
        _unlinked_promo(db, "ZZT UNLINKED STD", std, levels=["sorento_dealer"])
        _unlinked_promo(db, "ZZT UNLINKED FLYER", flyer, levels=["sorento_dealer"])
        _promo(db, "ZZT SORENTO REAL STD", std, w["srt_bar"], levels=["sorento_dealer"])

    _world(session_factory, extra=extra)
    _mcp(monkeypatch, session_factory)
    _go(session_factory, monkeypatch, _ask("Sorento", "Sorento"), "sorento grab bar promotion", "zzt-r3-b-ask")
    std = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-b-2")
    assert "ZZT SORENTO REAL STD" in std, std
    for bad in ("MOCHA", "UNLINKED"):
        assert bad not in std.upper(), std
    _go(session_factory, monkeypatch, _ask("sorento", "Sorento", subject=None, kind="a3_flyer"), "sorento promotion flyer", "zzt-r3-b-f-ask")
    fly = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-b-f-2")
    for bad in ("MOCHA", "UNLINKED"):
        assert bad not in fly.upper(), fly


# --------------------------------------------------------------------------- #
# AC-30 kind: "flyer" is the A3 Flyer kind, never a promotion-name match
# --------------------------------------------------------------------------- #


def _flyer_world(session_factory):
    def extra(w):
        db, std, flyer = w["db"], w["std"], w["flyer"]
        _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, w["srt_bar"], levels=["sorento_dealer"])
        _promo(db, "ZZT MOCHA A3 FLYER LIVE 2", flyer, w["mocha_bar"], levels=["sorento_dealer"])
        # a standard promotion whose NAME holds the word flyer: only a name match would find it
        _promo(db, "ZZT FLYER BUNDLE STD", std, w["srt_bar"], levels=["sorento_dealer"])

    return _world(session_factory, extra=extra)


def _flyer_entity(raw):
    return {"raw": raw, "hint": "promotion", "canonical_code": None, "current_message": True, "confident": True}


def test_promotion_flyer_filters_by_the_a3_flyer_kind_and_never_name_matches_promotions(session_factory, monkeypatch):
    _flyer_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    bodies: list = []
    qf = _ask(subject=None, kind="a3_flyer", extra_entities=[_flyer_entity("flyer")])
    _go(session_factory, monkeypatch, qf, "Promotion flyer", "zzt-r3-k-ask", bodies)
    for body in bodies:
        assert "promotion" not in [str(t).lower() for t in body.get("allowed_entity_types") or []], body
        assert "flyer" not in [str(t).lower() for t in body.get("tokens") or []], body
    reply = _go(session_factory, monkeypatch, _pick_qf(1), "1", "zzt-r3-k-1")
    fetches = [a for n, a in captured if n == PROMO_TOOL]
    assert fetches and all(a.get("promotion_types") == ["a3_flyer"] and not a.get("promotion_ids") for a in fetches), fetches
    assert "ZZT FLYER BUNDLE STD" not in reply, reply


def test_sorento_promotion_flyer_serves_sorento_a3_flyers_only(session_factory, monkeypatch):
    w = _flyer_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    qf = _ask("sorento", "Sorento", subject=None, kind="a3_flyer", extra_entities=[_flyer_entity("flyer")])
    _go(session_factory, monkeypatch, qf, "sorento promotion flyer", "zzt-r3-ks-ask")
    reply = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-ks-2")
    fetches = [a for n, a in captured if n == PROMO_TOOL]
    assert fetches and all(a.get("promotion_types") == ["a3_flyer"] and a.get("brand_ids") == [str(w["srt"].id)] for a in fetches), fetches
    assert "ZZT SORENTO A3 FLYER LIVE" in reply, reply
    assert "MOCHA" not in reply.upper() and "FLYER BUNDLE" not in reply.upper(), reply


# --------------------------------------------------------------------------- #
# Review round: product types are product_type spec values (not only classes)
# --------------------------------------------------------------------------- #


def _type_world(session_factory):
    """Grab bar and toilet seat are product_type VALUES; the classes are Bathroom Accessory and
    Water Closet (Grab Bar is not a class)."""
    from app.models.product_spec import ProductSpecifications, ProductSpecRegistry

    def extra(w):
        db = w["db"]
        db.add(
            ProductSpecRegistry(
                id=str(uuid.uuid4()), spec_key="product_type", label="Product type", data_type="enum",
                allowed_values=["grab_bar", "toilet_seat"],
                synonyms={"grab_bar": ["grab bar"], "toilet_seat": ["toilet seat"]},
            )
        )
        db.flush()
        _class_rows = ("Bathroom Accessory", "Water Closet")
        bar = _prod(db, w["srt"], "Bathroom Accessory", "ZZTGB1")
        other = _prod(db, w["srt"], "Bathroom Accessory", "ZZTTR1")
        spec = db.query(ProductSpecifications).filter_by(product_id=bar.id).one()
        spec.values = {"class": {"value": "Bathroom Accessory"}, "product_type": {"value": "grab_bar"}}
        db.flush()
        _promo(db, "ZZT GRAB BAR PROMO", w["std"], bar, levels=["sorento_dealer"])
        _promo(db, "ZZT ACCESSORY OTHER PROMO", w["std"], other, levels=["sorento_dealer"])

    # `_world` seeds class "Grab Bar" and its products; the type world needs its own classes only
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Bathroom Accessory")
    _class(session_factory, "Water Closet")
    db = session_factory()
    std, flyer = _types(db)
    srt = _brand(db, "Sorento")
    w = {"db": db, "std": std, "flyer": flyer, "srt": srt}
    extra(w)
    db.commit()
    return w


def _types_line(blocks: list[str]) -> str:
    return next((l for l in "\n".join(blocks).splitlines() if l.lower().startswith("product types:")), "")


def test_the_product_types_line_lists_product_type_values_not_only_classes(session_factory, monkeypatch):
    _type_world(session_factory)
    _mcp(monkeypatch, session_factory)
    blocks: list[str] = []
    svc = _wire(session_factory)
    _run_turn(session_factory, monkeypatch, qf=_ask("Sorento", "Sorento"), text_body="sorento grab bar promotion", msg_id="zzt-r3-pt-ctx", resolve_services=svc, capture_user_block=blocks)
    line = _types_line(blocks).lower()
    assert "grab bar" in line and "toilet seat" in line, blocks


def test_a_listed_product_type_canonical_grounds_to_the_type_and_filters_by_it(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    _type_world(session_factory)
    _mcp(monkeypatch, session_factory)
    ents = [{"raw": "grab bra", "hint": "category", "canonical_code": "grab bar", "current_message": True, "confident": True}]
    qf = _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)
    menu = _go(session_factory, monkeypatch, qf, "grab bra promotion", "zzt-r3-pt-ask")
    assert menu.startswith("Which access level"), menu
    reply = _go(session_factory, monkeypatch, _pick_qf(2), "2", "zzt-r3-pt-2")
    assert "ZZT GRAB BAR PROMO" in reply, reply
    assert "ZZT ACCESSORY OTHER PROMO" not in reply, reply


def test_a_canonical_that_is_neither_a_listed_type_nor_a_class_asks_back_and_never_widens(session_factory, monkeypatch):
    _type_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    ents = [{"raw": "grab bra", "hint": "category", "canonical_code": "zzt gadget", "current_message": True, "confident": True}]
    qf = _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)
    reply = _go(session_factory, monkeypatch, qf, "grab bra promotion", "zzt-r3-pt-unl")
    assert "I could not match" in reply and "Which access level" not in reply, reply
    assert "ZZT ACCESSORY OTHER PROMO" not in reply, reply
    assert not [a for n, a in captured if n == PROMO_TOOL], captured


def test_every_canonical_code_example_in_the_promotion_subject_prompt_can_be_listed(session_factory, monkeypatch):
    """An example the Product types line could never list teaches the parser to invent a name."""
    import re

    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as prompt

    section = prompt.split("== PROMOTION SUBJECT ==", 1)[1].split("\n== ")[0].split("\n━")[0]
    examples = re.findall(r'"canonical_code":\s*"([^"]+)"', section)
    brands = {"sorento", "mocha", "cabana"}
    examples = [e for e in examples if e.lower() not in brands]
    assert examples, section
    _type_world(session_factory)
    _mcp(monkeypatch, session_factory)
    blocks: list[str] = []
    svc = _wire(session_factory)
    _run_turn(session_factory, monkeypatch, qf=_ask("Sorento", "Sorento"), text_body="sorento promotion", msg_id="zzt-r3-pt-ex", resolve_services=svc, capture_user_block=blocks)
    line = _types_line(blocks).lower()
    for e in examples:
        assert e.lower() in line, (e, line)


def test_an_unconfident_unlisted_brand_word_asks_back_and_the_substring_fallback_never_runs(session_factory, monkeypatch):
    """AC-30: {raw "sorentoo", brand, confident false} must not become Sorento through tier_gate's
    BRANDS substring scan; it asks back, no menu, no fetch."""
    _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    qf = _ask("sorentoo", None)
    qf["entities"][0]["confident"] = False
    reply = _go(session_factory, monkeypatch, qf, "sorentoo grab bar promotion", "zzt-r3-unconf")
    assert reply.startswith('I could not match "sorentoo" to a brand.'), reply
    assert "Which access level" not in reply, reply
    assert not [a for n, a in captured if n == PROMO_TOOL], captured
