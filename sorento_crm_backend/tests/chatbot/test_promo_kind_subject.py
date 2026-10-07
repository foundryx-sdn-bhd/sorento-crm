"""PROMO-SUBJECT-FILTER R5 red tests: promotion KINDS (AC-19, AC-21, AC-22).

UAC `documentation/plans/chatbot/promo-subject-filter-acceptance-criteria.md` section R5.
Parser output field: `promotion_kind` (a `promotion_types.type_code` string, or null).

Engine-level through `test_outstanding_lane._run_turn`; the MCP double here is BACKED BY THE
REAL ROUTE (`GET /api/v1/marketing/promotions` over the seeded rows), so "live" (is_active
and inside its window), brand-of-linked-product and the customer's levels are the backend's
own answer, not a retyped one. The double hands the lane `{"has_result", "items", "data"}`
with each row's `description` (the promotion name). Fake promotion names only.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct, PromotionType
from app.models.product import Brand
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.chatbot.head import parser as parser_mod
from tests._mc_lookup_seed import product
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of
from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot.test_promo_subject_filter import _services

TODAY = date.today()
NAMES = ["Sorento Dealer", "Sorento Office", "Mocha Dealer"]
PROMO_TOOL = "crm_marketing_promotions_list"


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #


def _brand(db, name):
    row = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}"[:50], brand_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _promo(db, name, ptype, prod, *, levels, start=None, end=None, active=True):
    p = Promotion(
        id=str(uuid.uuid4()), description=name,
        start_date=start or TODAY - timedelta(days=5), end_date=end or TODAY + timedelta(days=30),
        is_active=active, access_levels=list(levels), promotion_type_id=ptype.id,
    )
    db.add(p)
    db.flush()
    group = PromotionGroup(promotion_id=p.id, group_name="Default", sort_order=0)
    db.add(group)
    db.flush()
    db.add(PromotionProduct(id=str(uuid.uuid4()), promotion_id=p.id, promotion_group_id=group.id, product_id=prod.id))
    db.flush()
    return p


def _seed_world(session_factory, *, sorento_live_flyer: bool, other_brands_live: bool = True) -> dict[str, Any]:
    _seed_contact(session_factory, variables={})
    db = session_factory()
    srt, mocha = _brand(db, "Sorento"), _brand(db, "Mocha")
    flyer = PromotionType(type_code="a3_flyer", type_name="A3 Flyer", show_expired=True, match_markers=["a3"], match_priority=40, is_default=False)
    standard = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add_all([flyer, standard])
    db.add_all(
        [
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="mocha_dealer", name="Mocha Dealer"),
            ContactAccessType(code="mocha_office", name="Mocha Office"),
        ]
    )
    db.flush()
    srt_prod, mocha_prod = product(db, company_id=DEFAULT_COMPANY_ID), product(db, company_id=DEFAULT_COMPANY_ID)
    srt_prod.brand_id, mocha_prod.brand_id = srt.id, mocha.id
    db.flush()
    # a Sorento standard promo: must never be offered for a Sorento A3 flyer ask
    _promo(db, "ZZT SORENTO STANDARD 2026", standard, srt_prod, levels=["sorento_dealer", "sorento_office"])
    # an EXPIRED, inactive Sorento flyer: never counts as live
    _promo(db, "ZZT SORENTO A3 FLYER OLD", flyer, srt_prod, levels=["sorento_dealer"],
           start=TODAY - timedelta(days=90), end=TODAY - timedelta(days=30), active=False)
    if sorento_live_flyer:
        _promo(db, "ZZT SORENTO A3 FLYER 2026", flyer, srt_prod, levels=["sorento_dealer"])
    if other_brands_live:
        _promo(db, "ZZT MOCHA A3 FLYER A", flyer, mocha_prod, levels=["mocha_dealer"])
        _promo(db, "ZZT MOCHA A3 FLYER A", flyer, mocha_prod, levels=["mocha_dealer"])  # same name, two rows
        _promo(db, "ZZT MOCHA A3 FLYER B", flyer, mocha_prod, levels=["mocha_dealer"])
        # a level the customer does not hold, and another kind: neither is listed
        _promo(db, "ZZT MOCHA A3 FLYER OFFICE ONLY", flyer, mocha_prod, levels=["mocha_office"])
        _promo(db, "ZZT MOCHA STANDARD", standard, mocha_prod, levels=["mocha_dealer"])
    db.commit()
    return {"srt": srt.id, "mocha": mocha.id}


def _route_backed_mcp(monkeypatch, session_factory):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    user = {"id": "130c548f-048f-53b2-97a6-3a54676bea77", "email": "u@test.com", "auth_method": "api_key"}
    def _db():
        yield session_factory()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_or_api_key] = lambda: user
    app.dependency_overrides[apply_company_scope] = lambda: None
    client = TestClient(app)
    captured: list[tuple[str, dict[str, Any]]] = []
    forwarded = ("access_levels", "promotion_types", "brand_ids", "scope_terms", "product_ids")

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        params = {k: args[k] for k in forwarded if args.get(k)}
        res = client.get("/api/v1/marketing/promotions/", params=params)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        return json.dumps({"has_result": bool(data), "items": data, "data": data})

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _qf(*, kind: str | None, brand: str | None = "Sorento", typed: str = "sroento") -> dict[str, Any]:
    entities = (
        [{"raw": typed, "hint": "brand", "canonical_code": brand, "current_message": True, "confident": True}]
        if brand
        else []
    )
    return _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", entities=entities,
        contains_flyer=kind is not None, promotion_kind=kind,
    )


def _brand_resolving_services(session_factory) -> ResolveGateServices:
    """As the real flow: a brand word the parser named resolves to the seeded Sorento row."""

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        row = session_factory().query(Brand).filter(Brand.brand_name == "Sorento").first()
        if row is None:
            return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}
        match = {"uuid": str(row.id), "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        return {
            "tokens": asked,
            "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in NAMES], resolve_entity=_resolve, probe=lambda **_: None
    )


def _say(session_factory, monkeypatch, qf, text, msg_id, **kw):
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id,
        resolve_services=_brand_resolving_services(session_factory), **kw,
    )
    return (result.reply or {}).get("text") or "", captured


# --------------------------------------------------------------------------- #
# AC-19: the parser reads kinds from promotion_types
# --------------------------------------------------------------------------- #


def test_parser_schema_declares_a_promotion_kind_field():
    """AC-19: a structured `promotion_kind` field (type code or null) is in the schema."""
    schema = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
    assert "promotion_kind" in schema["properties"], sorted(schema["properties"])
    assert "promotion_kind" in schema["required"]


def test_parser_is_shown_the_kinds_from_the_promotion_types_table(session_factory, monkeypatch):
    """AC-19: the kinds come from the table, not code: a seeded kind nothing in code knows
    reaches the text the parser reads."""
    _seed_world(session_factory, sorento_live_flyer=True)
    db = session_factory()
    db.add(PromotionType(type_code="zzt_kind_alpha", type_name="ZZT Kind Alpha", show_expired=True, match_markers=[], match_priority=70, is_default=False))
    db.commit()
    _route_backed_mcp(monkeypatch, session_factory)
    seen: list[str] = []
    _run_turn(
        session_factory, monkeypatch, qf=_qf(kind=None, brand=None), text_body="any promo", msg_id="zzt-pk-prompt",
        resolve_services=_services(NAMES), capture_user_block=seen,
    )
    block = "\n".join(seen)
    assert "ZZT Kind Alpha" in block and "A3 Flyer" in block, block


def test_bare_flyer_names_no_kind_so_every_kind_stays_in_play(session_factory, monkeypatch):
    """AC-19 (Q4b a): parser emits promotion_kind null for a bare "flyer": probes and fetch
    carry no promotion_types, and the normal menu is shown."""
    _seed_world(session_factory, sorento_live_flyer=True)
    captured = _route_backed_mcp(monkeypatch, session_factory)
    reply, _ = _say(session_factory, monkeypatch, _qf(kind=None), "sorento flyer", "zzt-pk-bare")
    calls = [a for n, a in captured if n == PROMO_TOOL]
    assert calls, captured
    assert all(not a.get("promotion_types") for a in calls), captured
    assert reply.startswith("Which access level"), reply


# --------------------------------------------------------------------------- #
# AC-21: kind + brand with no live promotion
# --------------------------------------------------------------------------- #


def test_no_live_kind_for_the_brand_lists_other_brands_by_distinct_name(session_factory, monkeypatch):
    """AC-21: exact lines, duplicate name collapsed, not-held level / other kind / expired
    Sorento flyer absent, no menu."""
    _seed_world(session_factory, sorento_live_flyer=False)
    _route_backed_mcp(monkeypatch, session_factory)
    reply, _ = _say(session_factory, monkeypatch, _qf(kind="a3_flyer"), "sroento a3 flyer", "zzt-pk-none")
    lines = reply.splitlines()
    assert lines[0] == "There is no Sorento A3 flyer right now.", reply
    assert lines[1] == "Other A3 flyers:", reply
    assert sorted(lines[2:]) == ["ZZT MOCHA A3 FLYER A", "ZZT MOCHA A3 FLYER B"], reply
    assert "Which access level" not in reply, reply
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


def test_no_live_kind_and_no_other_brand_has_one_is_the_first_line_only(session_factory, monkeypatch):
    """AC-21: no other brand has a live one: the first line only."""
    _seed_world(session_factory, sorento_live_flyer=False, other_brands_live=False)
    _route_backed_mcp(monkeypatch, session_factory)
    reply, _ = _say(session_factory, monkeypatch, _qf(kind="a3_flyer"), "sroento a3 flyer", "zzt-pk-only-first")
    assert reply.splitlines()[0] == "There is no Sorento A3 flyer right now.", reply
    assert "Other A3 flyers:" not in reply and "Which access level" not in reply, reply
    assert len(reply.strip().splitlines()) == 1, reply


# --------------------------------------------------------------------------- #
# AC-22: kind + brand WITH live promotions
# --------------------------------------------------------------------------- #


def test_kind_and_brand_with_live_rows_shows_the_stamped_menu_then_sends_kind_and_brand(session_factory, monkeypatch):
    """AC-22: stamped menu narrowed to kind + brand; the pick sends only that kind and brand."""
    ids = _seed_world(session_factory, sorento_live_flyer=True)
    captured = _route_backed_mcp(monkeypatch, session_factory)
    reply, _ = _say(session_factory, monkeypatch, _qf(kind="a3_flyer"), "sroento a3 flyer", "zzt-pk-live-ask")
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    assert "1. Office - no promotion" in reply and "2. Dealer - has promotion" in reply, reply
    probes = [a for n, a in captured if n == PROMO_TOOL]
    assert probes and all(a.get("promotion_types") == ["a3_flyer"] and a.get("brand_ids") == [ids["srt"]] for a in probes), captured

    pick = _parser_output(domain_hint=None, intent_hint=None, entities=[], access_levels=[], reference_positions=[2])
    n_before = len(captured)
    _say(session_factory, monkeypatch, pick, "2", "zzt-pk-live-pick")
    fetches = [a for n, a in captured[n_before:] if n == PROMO_TOOL]
    assert fetches, captured
    assert all(
        a.get("promotion_types") == ["a3_flyer"] and a.get("brand_ids") == [ids["srt"]] and a.get("access_levels") == ["Sorento Dealer"]
        for a in fetches
    ), fetches
