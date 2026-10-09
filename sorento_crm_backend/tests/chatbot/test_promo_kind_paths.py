"""PROMO-SUBJECT-FILTER R5 round 3 red tests: AC-21 on the CONTINUE exit, and liveness.

(1) The resolver returns a usable result (the brand word resolves to the Sorento brand row,
    so the turn takes the engine's `continue` exit, not not_found / access_ask): a kind +
    brand ask with no LIVE row of that kind must still give the AC-21 reply, never
    "No current promotion for ...".
(2) Liveness: Sorento's only A3 flyer is ended (is_active false, end_date 10 days ago) but
    EXPIRED-BUT-USABLE under the serving policy (type a3_flyer, show_expired, max age 180
    days). The MCP double answers through the real route WITH `serving_policy=true`, as the
    real MCP tool pins it (`sorento_crm_mcp/server.py` DEFAULT params). An ended flyer is
    not "right now" (owner ruling): AC-21 reply, and no level stamped "has promotion".

Fake names only. The MCP double is backed by the real route over the seeded rows.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot import test_outstanding_lane as lane_harness
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact, _session_of
from tests.chatbot.test_promo_kind_subject import NAMES, PROMO_TOOL, TODAY, _brand, _promo, _qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _seed(session_factory, *, sorento: str) -> dict[str, str]:
    """`sorento`: "none" | "expired_usable". Mocha has two live flyers (one name twice)."""
    _seed_contact(session_factory, variables={})
    db = session_factory()
    srt, mocha = _brand(db, "Sorento"), _brand(db, "Mocha")
    flyer = PromotionType(
        type_code="a3_flyer", type_name="A3 Flyer", show_expired=True, expired_max_age_days=180,
        match_markers=["a3"], match_priority=40, is_default=False,
    )
    standard = PromotionType(
        type_code="standard", type_name="Standard Promo", show_expired=True,
        match_markers=[], match_priority=90, is_default=True,
    )
    db.add_all([flyer, standard])
    db.add_all(
        [
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="mocha_dealer", name="Mocha Dealer"),
        ]
    )
    db.flush()
    srt_prod, mocha_prod = product(db, company_id=DEFAULT_COMPANY_ID), product(db, company_id=DEFAULT_COMPANY_ID)
    srt_prod.brand_id, mocha_prod.brand_id = srt.id, mocha.id
    db.flush()
    _promo(db, "ZZT SORENTO STANDARD 2026", standard, srt_prod, levels=["sorento_dealer", "sorento_office"])
    if sorento == "expired_usable":
        _promo(db, "ZZT SORENTO A3 FLYER ENDED", flyer, srt_prod, levels=["sorento_dealer"],
               start=TODAY - timedelta(days=60), end=TODAY - timedelta(days=10), active=False)
    _promo(db, "ZZT MOCHA A3 FLYER A", flyer, mocha_prod, levels=["mocha_dealer"])
    _promo(db, "ZZT MOCHA A3 FLYER A", flyer, mocha_prod, levels=["mocha_dealer"])
    _promo(db, "ZZT MOCHA A3 FLYER B", flyer, mocha_prod, levels=["mocha_dealer"])
    db.commit()
    return {"srt": srt.id, "mocha": mocha.id}


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
    forwarded = ("access_levels", "promotion_types", "brand_ids", "scope_terms", "product_ids")

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        params: dict[str, Any] = {k: args[k] for k in forwarded if args.get(k)}
        params["serving_policy"] = "true"  # the real MCP tool's pinned default
        res = client.get("/api/v1/marketing/promotions/", params=params)
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        return json.dumps({"has_result": bool(data), "items": data, "data": data})

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


def _continue_services(brand_id: str) -> ResolveGateServices:
    """The brand word RESOLVES (to the Sorento brand row), so the resolver answers with a
    usable result and the turn takes the `continue` exit."""

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": brand_id, "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        return {
            "tokens": asked,
            "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in NAMES], resolve_entity=_resolve, probe=lambda **_: None
    )


def _ask(session_factory, monkeypatch, ids, msg_id):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=_qf(kind="a3_flyer"), text_body="sroento a3 flyer", msg_id=msg_id,
        resolve_services=_continue_services(ids["srt"]),
    )
    return (result.reply or {}).get("text") or ""


def _assert_ac21(reply: str, session_factory):
    lines = reply.splitlines()
    assert lines[0] == "There is no Sorento A3 flyer right now.", reply
    assert lines[1] == "Other A3 flyers:", reply
    assert sorted(lines[2:]) == ["ZZT MOCHA A3 FLYER A", "ZZT MOCHA A3 FLYER B"], reply
    assert "Which access level" not in reply and "has promotion" not in reply, reply
    open_question = _session_of(session_factory).get("open_question")
    assert not open_question or open_question.get("kind") != "tier_pick", open_question


def test_continue_exit_kind_and_brand_with_nothing_live_gives_the_ac21_reply(session_factory, monkeypatch):
    """AC-21 on the continue exit: never "No current promotion for ..."."""
    ids = _seed(session_factory, sorento="none")
    _mcp(monkeypatch, session_factory)
    reply = _ask(session_factory, monkeypatch, ids, "zzt-kp-continue")
    assert "No current promotion for" not in reply, reply
    _assert_ac21(reply, session_factory)


def test_expired_but_usable_flyer_is_served_and_stamps_has_promotion(session_factory, monkeypatch):
    """AC-22 (supersedes the live-only AC-21 liveness): the serving policy returns the ended
    Sorento flyer (expired_but_usable); it counts: no "There is no ..." reply, Dealer stamped
    "has promotion"."""
    ids = _seed(session_factory, sorento="expired_usable")
    captured = _mcp(monkeypatch, session_factory)
    reply = _ask(session_factory, monkeypatch, ids, "zzt-kp-expired-usable")
    assert "There is no Sorento A3 flyer right now." not in reply, reply
    assert "2. Dealer - has promotion" in reply.splitlines(), reply
    assert any(n == PROMO_TOOL for n, _a in captured), captured
