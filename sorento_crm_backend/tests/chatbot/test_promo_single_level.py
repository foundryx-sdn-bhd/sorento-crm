"""PROMO-SUBJECT-FILTER AC-21 for a contact entitled to ONE access level (dealer only), so
`needs_tier_ask` is False and no tier menu / probe arm runs. Fake names only."""
from __future__ import annotations

from typing import Any

import pytest

from app.main import app
from app.models.marketing import PromotionType
from app.models.product import Product
from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_promo_kind_paths import _mcp, _seed
from tests.chatbot.test_promo_kind_subject import _promo, _qf

ONE_LEVEL = ["Sorento Dealer", "Mocha Dealer"]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _services(brand_id: str) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": brand_id, "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in ONE_LEVEL], resolve_entity=_resolve, probe=lambda **_: None
    )


def _ask(session_factory, monkeypatch, ids, msg_id):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=_qf(kind="a3_flyer"), text_body="sroento a3 flyer", msg_id=msg_id,
        resolve_services=_services(ids["srt"]),
    )
    return (result.reply or {}).get("text") or ""


def test_single_level_kind_and_brand_with_nothing_live_gives_the_ac21_reply(session_factory, monkeypatch):
    """AC-21: one entitled level, Sorento's only A3 flyer ended: the AC-21 lines, not the
    generic "Could not find promotion" offer."""
    ids = _seed(session_factory, sorento="expired_usable")
    _mcp(monkeypatch, session_factory)
    reply = _ask(session_factory, monkeypatch, ids, "zzt-sl-none")
    lines = reply.splitlines()
    assert lines[0] == "There is no Sorento A3 flyer right now.", reply
    assert lines[1] == "Other A3 flyers:", reply
    assert sorted(lines[2:]) == ["ZZT MOCHA A3 FLYER A", "ZZT MOCHA A3 FLYER B"], reply
    assert "Could not find" not in reply and "Which access level" not in reply, reply


def test_single_level_kind_and_brand_with_a_live_row_returns_it_without_a_menu(session_factory, monkeypatch):
    """Control: a live Sorento flyer at the one level is fetched straight away, no menu."""
    ids = _seed(session_factory, sorento="none")
    db = session_factory()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    prod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, prod, levels=["sorento_dealer"])
    db.commit()
    captured = _mcp(monkeypatch, session_factory)
    reply = _ask(session_factory, monkeypatch, ids, "zzt-sl-live")
    assert "Which access level" not in reply and "There is no" not in reply, reply
    calls = [a for n, a in captured if n == "crm_marketing_promotions_list"]
    assert calls and all(a.get("promotion_types") == ["a3_flyer"] and a.get("brand_ids") == [ids["srt"]] for a in calls), captured
    assert "Here are the results" in reply, reply
