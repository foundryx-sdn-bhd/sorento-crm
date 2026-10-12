"""PROMO-SUBJECT-FILTER owner hand test: "only sorento kitchen sink promo" (brand + category).

The menu is stamped by a probe narrowed by subject AND brand (a Cabana dealer sink promo must not
count for Dealer; a Sorento WC promo counts nowhere), and the pick fetches by `scope_terms` +
`brand_ids`, never by the product set the resolver built from brand + category (`product_ids`).
The resolver double behaves like the real one for this ask: the brand word resolves to the
brand row, the brand + category words resolve to that brand's kitchen sink products.
Fake names only; the MCP double is backed by the real route.
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
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_kind_subject import PROMO_TOOL, _promo
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf
from tests.chatbot.test_engine import _parser_output

LEVELS = ["Sorento Office", "Sorento Dealer", "End User"]


def _mcp(monkeypatch, session_factory):
    """`test_promo_kind_paths._mcp`, but the route body goes through the REAL MCP presenter
    (the live `view=render` shape), so items carry their titles."""
    import json

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService
    from tests.chatbot import test_outstanding_lane as lane_harness

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
    from tests.chatbot.test_promo_kind_subject import promotion_route

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        params: dict[str, Any] = {k: args[k] for k in forwarded if args.get(k)}
        params["serving_policy"] = "true"
        # owner Q4 option (a), 12 Oct: a described subject is answered by the promotion-products route
        res = promotion_route(client, name, args, params)
        return lane_harness._present_response()(name, json.dumps(res.json()))

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _brand(db, name):
    row = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _prod(db, brand, klass, code):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    row.brand_id = brand.id
    db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=row.id, values={"class": {"value": klass}}))
    db.flush()
    return row


def _world(session_factory):
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Kitchen Sink")
    _class(session_factory, "Water Closet")
    db = session_factory()
    srt, cab = _brand(db, "Sorento"), _brand(db, "Cabana")
    std = PromotionType(type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90, is_default=True)
    db.add(std)
    db.add_all(
        [
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()
    srt_sinks = [_prod(db, srt, "Kitchen Sink", f"ZZTSK{i}") for i in range(3)]
    cab_sink = _prod(db, cab, "Kitchen Sink", "ZZTCK1")
    srt_wc = _prod(db, srt, "Water Closet", "ZZTWC1")
    _promo(db, "ZZT SORENTO KITCHEN SINK COMBO", std, srt_sinks[0], levels=["sorento_office", "end_user"])
    _promo(db, "ZZT CABANA SINK DEALER", std, cab_sink, levels=["sorento_dealer"])
    _promo(db, "ZZT SORENTO WC", std, srt_wc, levels=["sorento_office", "sorento_dealer", "end_user"])
    db.commit()
    return {"srt": str(srt.id), "sinks": [(str(p.id), p.product_code) for p in srt_sinks]}


def _real_like_resolver(ids) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        resolutions = []
        for raw in asked:
            word = raw.strip().lower()
            if word == "sorento":
                matches = [{"uuid": ids["srt"], "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}]
            else:  # brand + category: that brand's products of the class
                matches = [
                    {"uuid": u, "entity_type": "product", "canonical_code": c, "match_tier": "exact"} for u, c in ids["sinks"]
                ]
            resolutions.append({"raw": raw, "token": raw, "matches": matches})
        return {"tokens": asked, "resolutions": resolutions, "unresolved_tokens": []}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in LEVELS], resolve_entity=_resolve, probe=lambda **_: None
    )


def _qf():
    ents = [
        {"raw": "sorento", "hint": "brand", "canonical_code": "Sorento", "current_message": True, "confident": True},
        {"raw": "kitchen sink", "hint": "category", "canonical_code": None, "current_message": True, "confident": True},
    ]
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=ents)


def _turn(session_factory, monkeypatch, ids, qf, text, msg_id):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=text, msg_id=msg_id, resolve_services=_real_like_resolver(ids)
    )
    return (result.reply or {}).get("text") or ""


def test_menu_probe_narrows_by_subject_and_brand(session_factory, monkeypatch):
    """T1: Office and End user have the Sorento sink promo; Dealer has only Cabana's sink (other
    brand) and a Sorento WC (other subject), so Dealer is "no promotion"."""
    ids = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    reply = _turn(session_factory, monkeypatch, ids, _qf(), "only sorento kitchen sink promo", "zzt-bc-1")
    probes = [a for name, a in captured if name == PROMO_TOOL]
    assert probes, captured
    for a in probes:  # the probe asks by subject + brand, not by the resolved product set
        assert [t.lower() for t in a.get("scope_terms") or []] == ["kitchen sink"], a
        assert a.get("brand_ids") == [ids["srt"]], a
        assert not a.get("product_ids"), a
    lines = reply.splitlines()
    assert "1. Office - has promotion" in lines, reply
    assert "2. Dealer - no promotion" in lines, reply
    assert "3. End user - has promotion" in lines, reply


def test_pick_fetches_by_subject_and_brand_never_by_the_product_set(session_factory, monkeypatch):
    # owner Q4 option (a), 12 Oct: the promotion pick runs crm_marketing_promotion_products_list, one row per product line.
    """T2: pick "1": scope_terms ["kitchen sink"] + brand_ids [Sorento], no product_ids; the reply
    presents the Sorento sink promotion, with no product codes and no not-matched / escalation text."""
    ids = _world(session_factory)
    captured = _mcp(monkeypatch, session_factory)
    _turn(session_factory, monkeypatch, ids, _qf(), "only sorento kitchen sink promo", "zzt-bc-ask")
    n = len(captured)
    reply = _turn(session_factory, monkeypatch, ids, _pick_qf(1), "1", "zzt-bc-pick")
    # owner Q4 option (a), 12 Oct: the pick fetch is the promotion-products tool with spec_constraints
    from tests.chatbot.test_promo_kind_subject import PRODUCTS_TOOL, subject_of

    fetches = [a for name, a in captured[n:] if name == PRODUCTS_TOOL]
    assert fetches, captured
    assert not [1 for name, _a in captured[n:] if name == PROMO_TOOL], captured[n:]
    for a in fetches:
        assert subject_of(a) == {"kitchen sink"}, a
        assert not a.get("scope_terms"), a
        assert a.get("brand_ids") == [ids["srt"]], a
        assert not a.get("product_ids"), a
    assert "ZZT SORENTO KITCHEN SINK COMBO" in reply, reply
    # owner Q4 option (a), 12 Oct: each product line now shows its code, so "ZZTSK" is no longer
    # forbidden reply text; "never fetched by the product set" is the no-product_ids check above
    for bad in ("Here's what you want", "no promotion matched", "escalate"):
        assert bad.lower() not in reply.lower(), reply
