"""PROMO-SUBJECT-FILTER round 2 review should-fix red tests. Fake names only."""
from __future__ import annotations

import importlib.util
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _promo
from tests.chatbot.test_promo_owner_round2 import (
    LEVELS, _furniture_world, _mcp, _subject_qf, _turn, _unresolved,
)
from tests.chatbot.test_promo_subject_filter import _class


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _types_and_levels(db):
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    flyer = PromotionType(type_code="a3_flyer", type_name="A3 Flyer", show_expired=True, expired_max_age_days=180, match_markers=["a3"], match_priority=40, is_default=False)
    db.add_all([std, flyer])
    db.add_all(
        [
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="mocha_dealer", name="Mocha Dealer"),
        ]
    )
    db.flush()
    return std, flyer


def _brand(db, name):
    row = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


# (1) a named promotion that also carries a kind word ------------------------------------------


def test_a_resolved_promotion_name_keeps_promotion_ids_beside_a_kind(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    db = session_factory()
    std, flyer = _types_and_levels(db)
    mocha = _brand(db, "Mocha")
    prod = product(db, company_id=DEFAULT_COMPANY_ID)
    prod.brand_id = mocha.id
    p1 = _promo(db, "ZZT MOCHA A3 FLYER 0210", flyer, prod, levels=["mocha_dealer"])
    _promo(db, "ZZT MOCHA A3 FLYER 0310", flyer, prod, levels=["mocha_dealer"])
    db.commit()

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": str(p1.id), "entity_type": "promotion", "canonical_code": "ZZT MOCHA A3 FLYER 0210", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    services = ResolveGateServices(
        access_types=lambda **_: [{"name": "Mocha Dealer"}], resolve_entity=_resolve, probe=lambda **_: None
    )
    qf = _parser_output(
        domain_hint="promotion", intent_hint="check_promotion", promotion_kind="a3_flyer",
        entities=[{"raw": "ZZT MOCHA A3 FLYER 0210", "hint": "promotion", "canonical_code": None, "current_message": True, "confident": True}],
    )
    captured = _mcp(monkeypatch, session_factory)
    reply = _turn(session_factory, monkeypatch, services, qf, "ZZT MOCHA A3 FLYER 0210 a3 flyer", "zzt-r2r-named")
    calls = [a for n, a in captured if n == PROMO_TOOL]
    assert calls and all(a.get("promotion_ids") == [str(p1.id)] for a in calls), captured
    assert "ZZT MOCHA A3 FLYER 0210" in reply and "ZZT MOCHA A3 FLYER 0310" not in reply, reply


# (2) a class name that is also a registry head -----------------------------------------------


def test_a_class_word_that_is_also_a_registry_head_filters_to_the_class(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Zzcab")
    db = session_factory()
    std, _ = _types_and_levels(db)
    brand = _brand(db, "Sorento")
    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()), spec_key="furniture_type", label="Furniture type", data_type="enum",
            allowed_values=["big_zzcab", "small_zzcab"],
            synonyms={"big_zzcab": ["big zzcab"], "small_zzcab": ["small zzcab"]},
        )
    )
    db.flush()
    prod = product(db, company_id=DEFAULT_COMPANY_ID)
    prod.brand_id = brand.id
    db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=prod.id, values={"class": {"value": "Zzcab"}, "furniture_type": {"value": "big_zzcab"}}))
    db.flush()
    _promo(db, "ZZT PROMO ZZCAB", std, prod, levels=["sorento_office", "sorento_dealer"])
    db.commit()
    _mcp(monkeypatch, session_factory)
    reply = _turn(session_factory, monkeypatch, _unresolved(LEVELS), _subject_qf("zzcab"), "zzcab promo", "zzt-r2r-head")
    assert "Which zzcab" not in reply, reply
    assert "1. Office - has promotion" in reply.splitlines(), reply


# (3) no word-shape step: a plural is not a registry head ---------------------------------------


def test_a_plural_of_a_registry_head_is_not_stripped_into_a_which_question(session_factory, monkeypatch):
    _furniture_world(session_factory)
    _mcp(monkeypatch, session_factory)
    reply = _turn(session_factory, monkeypatch, _unresolved(LEVELS), _subject_qf("cabinets"), "cabinets promo", "zzt-r2r-plural")
    lines = reply.splitlines()
    assert 'I could not match "cabinets" to a product type.' in lines, reply
    assert "Which cabinet" not in reply, reply


# (4) the migration never raises on a constant without its markers -------------------------------


def test_pk_0001_logs_and_publishes_nothing_when_the_constant_lacks_a_marker(monkeypatch, caplog):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text as sql

    from app.services import chatbot_parser_prompt
    from tests._pg_fixture import blank_session

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "pk_0001_parser_promo_kind.py"
    spec = importlib.util.spec_from_file_location("zzt_pk_0001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(chatbot_parser_prompt, "SEMANTIC_PARSER_PROMPT", "a parser text with none of the markers")
    with blank_session() as db:
        conn = db.connection()
        count = lambda: conn.execute(sql("SELECT count(*) FROM ai_prompt_versions WHERE name = 'chatbot_semantic_parser'")).scalar()  # noqa: E731
        before = count()
        with caplog.at_level(logging.INFO):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()  # must not raise
        assert count() == before
    assert any("pk_0001" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]
