"""PROMO-SUBJECT-FILTER AC-17 with a class word beside the code: "kitchen sink SRTKS0000 promo".

The parser gives a category entity ("kitchen sink") AND a code entity ("SRTKS0000", hint None or
"product") the resolver places on a product. The code is the stricter subject: probe and pick keep
`product_ids` and send NO `scope_terms`, so the reply is that product's promotion only.
Fake names only; presenter-backed route stub from test_promo_brand_category.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_brand_category import _mcp
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _promo
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf

LEVELS = ["Sorento Office", "Sorento Dealer"]
CODE = "SRTKS0000"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _sink(db, brand, code):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    row.brand_id = brand.id
    db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=row.id, values={"class": {"value": "Kitchen Sink"}}))
    db.flush()
    return row


def _world(session_factory):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Kitchen Sink")
    db = session_factory()
    brand = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add(brand)
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add_all([ContactAccessType(code="sorento_office", name="Sorento Office"), ContactAccessType(code="sorento_dealer", name="Sorento Dealer")])
    db.flush()
    mine, other = _sink(db, brand, CODE), _sink(db, brand, "ZZTKS9999")
    levels = ["sorento_office", "sorento_dealer"]
    _promo(db, "ZZT PROMO P1 FOR THE CODE", std, mine, levels=levels)
    _promo(db, "ZZT PROMO P2 OTHER SINK", std, other, levels=levels)
    db.commit()
    return str(mine.id)


def _resolver(code_uuid: str) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        hits = [r for r in asked if r.strip().upper() == CODE]
        match = {"uuid": code_uuid, "entity_type": "product", "canonical_code": CODE, "match_tier": "exact"}
        return {
            "tokens": asked,
            "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in hits],
            "unresolved_tokens": [r for r in asked if r not in hits],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in LEVELS], resolve_entity=_resolve, probe=lambda **_: None
    )


def _qf(code_hint):
    ents = [
        {"raw": "kitchen sink", "hint": "category", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": CODE, "hint": code_hint, "canonical_code": None, "current_message": True, "confident": True},
    ]
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)


@pytest.mark.parametrize("code_hint", [None, "product"], ids=["hint-none", "hint-product-control"])
def test_a_code_beside_a_class_keeps_product_ids_and_sends_no_subject(session_factory, monkeypatch, code_hint):
    pid = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)

    def turn(qf, text, msg_id):
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id, resolve_services=_resolver(pid)
        )
        return (result.reply or {}).get("text") or ""

    turn(_qf(code_hint), f"kitchen sink {CODE} promo", f"zzt-cb-ask-{code_hint}")
    n = len(captured)
    reply = turn(_pick_qf(1), "1", f"zzt-cb-pick-{code_hint}")
    calls = [a for name, a in captured if name == PROMO_TOOL]
    assert calls and len(captured) > n, captured
    for a in calls:
        assert a.get("product_ids") == [pid], a
        assert not a.get("scope_terms"), a
    assert "ZZT PROMO P1 FOR THE CODE" in reply, reply
    assert "ZZT PROMO P2 OTHER SINK" not in reply, reply
