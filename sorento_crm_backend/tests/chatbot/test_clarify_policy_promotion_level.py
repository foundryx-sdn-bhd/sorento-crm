"""CLARIFY-POLICY AC-102 (owner Q2): the promotion access-level menu is UNCHANGED. Regression guards UAC: documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

Harness: `test_promo_kind_subject`'s route-backed MCP double (the real promotions route over the
seeded rows) through `test_outstanding_lane._run_turn`. The contact's audience is
`respond_contacts.chatbot_profile.tier` ("office" = staff, `turn/state.py::STAFF_TIER`).

ASSUMPTION (AC-8, UNVERIFIED in the UAC): `chatbot_profile.tier` is only the REMEMBERED tier pick
(`turn/narrow.py:427`), not the audience, so it is not used here. A "dealer" contact is modelled as
a contact linked to a customer whose entitlements are Dealer + End User (no office level), the
`do_ask.is_dealer` shape; "staff" is a contact entitled to an Office level. The tests assert the
observable result (no menu, which level was fetched), so a different derivation still passes them.
Fake names only.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product import Brand
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_kind_subject import _brand, _promo, _qf, _route_backed_mcp

STAFF_NAMES = ["Sorento Dealer", "Sorento Office"]
DEALER_NAMES = ["Sorento Dealer", "End User"]
DEALER_PROMO = "ZZT SORENTO DEALER DEAL"
OFFICE_PROMO = "ZZT SORENTO OFFICE DEAL"
END_USER_PROMO = "ZZT SORENTO END USER DEAL"
SHARED_PROMO = "ZZT SORENTO SHARED DEAL"
PROMO_TOOL = "crm_marketing_promotions_list"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _world(session_factory, *, promos: list[tuple[str, list[str]]], linked: bool = False) -> str:
    _seed_contact(session_factory, variables={})
    if linked:
        _link_customers(session_factory, "ZZT PROMO DEALER SDN BHD")
    db = session_factory()
    srt = _brand(db, "Sorento")
    standard = PromotionType(
        type_code="standard", type_name="Standard Promo", show_expired=True, match_markers=[], match_priority=90,
        is_default=True,
    )
    db.add(standard)
    db.add_all(
        [
            ContactAccessType(code="sorento_dealer", name="Sorento Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()
    prod = product(db, company_id=DEFAULT_COMPANY_ID)
    prod.brand_id = srt.id
    db.flush()
    for name, levels in promos:
        _promo(db, name, standard, prod, levels=levels)
    db.commit()
    return str(srt.id)


def _services(brand_id: str, names: list[str]) -> ResolveGateServices:
    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": brand_id, "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        return {"tokens": asked, "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in asked], "unresolved_tokens": []}

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in names], resolve_entity=_resolve, probe=lambda **_: None
    )


def _ask(session_factory, monkeypatch, brand_id: str, names: list[str]):
    captured = _route_backed_mcp(monkeypatch, session_factory)
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=_qf(kind=None, typed="Sorento"), text_body="sorento promotion",
        msg_id=f"zzt-clp-promo-{uuid.uuid4().hex[:8]}", resolve_services=_services(brand_id, names),
    )
    return ((result.reply or {}).get("text") or ""), captured


def _last_levels(captured) -> list[str]:
    return [a.get("access_levels") for n, a in captured if n == PROMO_TOOL][-1]


def test_ac102_a_dealer_contact_with_two_entitled_levels_still_gets_the_menu(session_factory, monkeypatch):
    brand_id = _world(
        session_factory, linked=True, promos=[(DEALER_PROMO, ["sorento_dealer"]), (END_USER_PROMO, ["end_user"])]
    )
    reply, _ = _ask(session_factory, monkeypatch, brand_id, DEALER_NAMES)
    assert reply.startswith("Which access level do you need for Sorento?"), reply


def test_ac102_staff_with_one_live_level_still_gets_the_menu_with_has_and_no_promotion(session_factory, monkeypatch):
    brand_id = _world(session_factory, promos=[(DEALER_PROMO, ["sorento_dealer"])])
    reply, _ = _ask(session_factory, monkeypatch, brand_id, STAFF_NAMES)
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    assert "Office - no promotion" in reply and "Dealer - has promotion" in reply, reply


def test_ac102_staff_with_different_promotions_per_level_keeps_the_menu(session_factory, monkeypatch):
    brand_id = _world(session_factory, promos=[(DEALER_PROMO, ["sorento_dealer"]), (OFFICE_PROMO, ["sorento_office"])])
    reply, _ = _ask(session_factory, monkeypatch, brand_id, STAFF_NAMES)
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    assert "1. " in reply and "2. " in reply, reply


def test_ac102_the_same_promotion_at_every_level_still_gets_the_menu(session_factory, monkeypatch):
    brand_id = _world(session_factory, promos=[(SHARED_PROMO, ["sorento_dealer", "sorento_office"])])
    reply, _ = _ask(session_factory, monkeypatch, brand_id, STAFF_NAMES)
    assert reply.startswith("Which access level do you need for Sorento?"), reply


def test_ac102_every_level_empty_says_no_current_promotion(session_factory, monkeypatch):
    """Regression (cand-0265, `answer.py:333-344`). The subject label is the brand word; the UAC's
    <CODE> is whatever label the ask carries."""
    brand_id = _world(session_factory, promos=[])
    reply, _ = _ask(session_factory, monkeypatch, brand_id, STAFF_NAMES)
    assert reply.strip() == "No current promotion for Sorento.", reply
    assert "- no promotion" not in reply and "Which access level" not in reply, reply
