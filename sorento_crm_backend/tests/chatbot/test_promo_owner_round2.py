"""PROMO-SUBJECT-FILTER owner review round 2 red tests (AC-22 to AC-27). Fake names only.

The MCP double is backed by the real route and the REAL presenter (the live `view=render` shape);
it forwards every argument the lane sends, so the test does not pin which new param carries a
spec-grounded subject, only what the customer is told.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand, Product
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_kind_paths import _seed as _seed_kind_world
from tests.chatbot.test_promo_kind_subject import PRODUCTS_TOOL, PROMO_TOOL, TODAY, _promo, _qf, promotion_route
from tests.chatbot.test_promo_single_level import _services as _single_level_services
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf

LEVELS = ["Sorento Office", "Sorento Dealer"]
_SKIP = {"_diagnostics", "view", "date_mode", "contact_id", "space_id", "is_active"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _mcp(monkeypatch, session_factory):
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

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        params: dict[str, Any] = {
            k: v for k, v in args.items() if k not in _SKIP and k != "spec_constraints" and v not in (None, "", [], {})
        }
        params["serving_policy"] = "true"
        # owner Q4 option (a), 12 Oct: a described subject is answered by the promotion-products route
        res = promotion_route(client, name, args, params)
        return lane_harness._present_response()(name, json.dumps(res.json()))

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


def _unresolved(names: list[str]) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in names], resolve_entity=_resolve, probe=lambda **_: None
    )


def _turn(session_factory, monkeypatch, services, qf, text, msg_id):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id, resolve_services=services
    )
    return (result.reply or {}).get("text") or ""


def _subject_qf(raw: str) -> dict[str, Any]:
    ents = [{"raw": raw, "hint": "category", "canonical_code": None, "current_message": True, "confident": True}]
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)


# --------------------------------------------------------------------------- #
# AC-22: a kind ask is decided by the same serving rule as every promotion
# --------------------------------------------------------------------------- #


def test_an_ended_flyer_the_type_still_serves_is_shown_marked_expired(session_factory, monkeypatch):
    """AC-22: Sorento's only A3 flyer ended 10 days ago (type show_expired, max age 180); a live
    Mocha A3 flyer exists. A multi-level contact (menu + probe path): the Dealer level is stamped
    "has promotion" (never "There is no Sorento A3 flyer right now."), and the pick serves the
    ended flyer marked "(EXPIRED)"."""
    from tests.chatbot.test_promo_kind_paths import _continue_services

    ids = _seed_kind_world(session_factory, sorento="expired_usable")
    _mcp(monkeypatch, session_factory)
    services = _continue_services(ids["srt"])
    menu = _turn(session_factory, monkeypatch, services, _qf(kind="a3_flyer"), "any sorento a3 flyer?", "zzt-r2-expired-ask")
    assert "There is no Sorento A3 flyer right now." not in menu, menu
    assert "2. Dealer - has promotion" in menu.splitlines(), menu
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(2), "2", "zzt-r2-expired-pick")
    assert "ZZT SORENTO A3 FLYER ENDED" in reply, reply
    assert "(EXPIRED)" in reply, reply


def test_a_kind_that_is_never_served_after_its_end_still_says_there_is_none(session_factory, monkeypatch):
    """AC-22: a type with show_expired off (special) and only an expired Sorento row: the serving
    rule serves nothing, so "There is no ..." stays, with no expired row shown."""
    ids = _seed_kind_world(session_factory, sorento="none")
    db = session_factory()
    special = PromotionType(type_code="special", type_name="Special Promo", show_expired=False, match_markers=[], match_priority=10, is_default=False)
    db.add(special)
    db.flush()
    srt_prod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    _promo(db, "ZZT SORENTO SPECIAL ENDED", special, srt_prod, levels=["sorento_dealer"],
           start=TODAY - timedelta(days=60), end=TODAY - timedelta(days=10), active=False)
    db.commit()
    _mcp(monkeypatch, session_factory)
    reply = _turn(
        session_factory, monkeypatch, _single_level_services(ids["srt"]), _qf(kind="special"),
        "any sorento special?", "zzt-r2-special",
    )
    first = reply.splitlines()[0]
    assert first.startswith("There is no Sorento") and first.endswith("right now."), reply
    assert "ZZT SORENTO SPECIAL ENDED" not in reply and "(EXPIRED)" not in reply, reply


# --------------------------------------------------------------------------- #
# AC-23: a subject that grounds to nothing asks back
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", ["kitchen cabinet", "seat cover"])
def test_a_subject_that_grounds_to_nothing_asks_back_never_no_current_promotion(session_factory, monkeypatch, raw):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Kitchen Sink")
    _class(session_factory, "Toilet Seat")
    _mcp(monkeypatch, session_factory)
    reply = _turn(
        session_factory, monkeypatch, _unresolved(LEVELS), _subject_qf(raw), f"{raw} promo", f"zzt-r2-ground-{raw[:4]}"
    )
    lines = reply.splitlines()
    assert f'I could not match "{raw}" to a product type.' in lines, reply
    assert "Which product or brand do you mean?" in lines, reply
    assert "No current promotion" not in reply and "Which access level" not in reply, reply


# --------------------------------------------------------------------------- #
# AC-24 / AC-25: spec attribute values ground a subject, several values ask which
# --------------------------------------------------------------------------- #


def _furniture_world(session_factory):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Bathroom Furniture")
    _class(session_factory, "Wash Basin")  # with the real CLASS_SYNONYMS: "basin" and "cabinet" are two classes
    db = session_factory()
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add_all([ContactAccessType(code="sorento_office", name="Sorento Office"), ContactAccessType(code="sorento_dealer", name="Sorento Dealer")])
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key="furniture_type", label="Furniture type", data_type="enum",
            allowed_values=["basin_cabinet", "side_cabinet", "tall_cabinet"],
            synonyms={
                "basin_cabinet": ["basin cabinet"],
                "side_cabinet": ["side cabinet"],
                "tall_cabinet": ["tall cabinet"],
            },
        )
    )
    db.flush()
    made = {}
    for value in ("basin_cabinet", "side_cabinet", "tall_cabinet"):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT{value[:4].upper()}1")
        p.brand_id = brand.id
        db.add(
            ProductSpecifications(
                id=str(uuid.uuid4()), product_id=p.id,
                values={"class": {"value": "Bathroom Furniture"}, "furniture_type": {"value": value}},
            )
        )
        db.flush()
        _promo(db, f"ZZT PROMO {value.upper()}", std, p, levels=["sorento_office", "sorento_dealer"])
        made[value] = p
    db.commit()
    return made


def test_a_spec_value_subject_filters_to_products_with_that_value(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """AC-24: "basin cabinet promo" (furniture_type basin_cabinet): stamped menu, the pick returns
    only promotions linked to basin_cabinet products."""
    _furniture_world(session_factory)
    _mcp(monkeypatch, session_factory)
    services = _unresolved(LEVELS)
    menu = _turn(session_factory, monkeypatch, services, _subject_qf("basin cabinet"), "basin cabinet promo", "zzt-r2-spec-ask")
    assert "1. Office - has promotion" in menu.splitlines(), menu
    reply = _turn(session_factory, monkeypatch, services, _pick_qf(1), "1", "zzt-r2-spec-pick")
    assert "ZZT PROMO BASIN_CABINET" in reply, reply
    assert "ZZT PROMO SIDE_CABINET" not in reply and "ZZT PROMO TALL_CABINET" not in reply, reply


def test_a_word_for_several_spec_values_asks_which_not_a_mixed_list(session_factory, monkeypatch):
    """AC-25: "cabinet promo": cabinet is the head of three furniture_type values: ask which,
    listing the options; no menu, no promotions call, no files."""
    _furniture_world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    reply = _turn(
        session_factory, monkeypatch, _unresolved(LEVELS), _subject_qf("cabinet"), "cabinet promo", "zzt-r2-several"
    )
    low = reply.lower()
    for option in ("basin cabinet", "side cabinet", "tall cabinet"):
        assert option in low, reply
    assert "Which access level" not in reply and "Here are the results" not in reply, reply
    assert not [1 for n, _a in captured if n == PROMO_TOOL], captured


# --------------------------------------------------------------------------- #
# AC-26 / AC-27: the consolidated dev parser version
# --------------------------------------------------------------------------- #

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _consolidating_migration() -> Path | None:
    hits = [
        p for p in _VERSIONS.glob("*.py")
        if "seat cover" in p.read_text().lower() and "PROMOTION KIND" in p.read_text()
    ]
    return hits[0] if len(hits) == 1 else None


def test_one_migration_carries_the_seat_cover_grounding_and_the_promotion_kind_section():
    """AC-26: the addendum text names "seat cover" and grounds it to the toilet seat type."""
    path = _consolidating_migration()
    assert path is not None, "exactly one migration must carry both the seat cover wording and PROMOTION KIND"
    text = path.read_text().lower()
    assert "toilet seat" in text, path


def test_the_migration_publishes_one_unlabelled_version_built_from_the_code_constant():
    """AC-27: run against a blank schema, it adds ONE new chatbot_semantic_parser version, with no
    label, whose text holds every line of SEMANTIC_PARSER_PROMPT plus PROMOTION KIND and the seat
    cover grounding (built from the constant, not from production text)."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text as sql

    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT
    from tests._pg_fixture import blank_session

    path = _consolidating_migration()
    assert path is not None, "no consolidating migration yet"
    spec = importlib.util.spec_from_file_location("zzt_r2_consolidating_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with blank_session() as db:
        conn = db.connection()
        before = conn.execute(sql("SELECT count(*) FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'")).scalar()
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(
            sql(
                "SELECT v.template, (SELECT count(*) FROM ai_prompt_labels l WHERE l.version_id = v.id) "
                "FROM ai_prompt_versions v WHERE v.name = 'chatbot_semantic_parser' ORDER BY v.version DESC"
            )
        ).all()
    assert len(rows) == before + 1, (before, len(rows))
    template, labels = rows[0]
    assert labels == 0, "never labelled production"
    for line in (l.strip() for l in SEMANTIC_PARSER_PROMPT.splitlines()):
        if line:
            assert line in template, line
    assert "PROMOTION KIND" in template and "seat cover" in template.lower()
