"""SPEC-PROMO-PARAMS hand test step 1 (owner, 12 Oct 2026, crew trace e2a6498a): a size the
parser puts ON the category entity reaches the tool.

"600mm basin got stock?" parsed (parser v86) to ONE entity: hint category, raw "600mm basin",
canonical "basin", spec_key dim_width, spec_value 600, and no specification entity. Grounding
swapped the raw to "basin" and the spec pair on the category entity was read by nothing, so the
stock tool got the basin ids with no constraint and listed every basin.

Rule (owner): the parser decides, code only honours what the parser emitted; no word rule on the
customer's text. A spec pair the parser emitted on a category entity is a specification of that
ask. A millimetre number there carries no axis word of the customer's own, so it is the virtual
`size` key and the route reads the axis from the data (`product_spec_filter.resolve_size`): one
axis holds it -> that axis; several -> "Length or Width?"; none -> the values on file.

Made-up product codes only (ZZT-*).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.chatbot.test_lane_require import _seed_registry


@pytest.fixture()
def vdb(session_factory):
    db = session_factory()
    _seed_registry(db)
    db.commit()
    yield db
    db.close()


def _category(raw: str, canonical: str | None = None, **spec: Any) -> dict[str, Any]:
    return {
        "raw": raw, "hint": "category", "canonical_code": canonical,
        "current_message": True, "confident": True, "hint_confident": True,
        "quantity": None, **spec,
    }


def _ground(db, *entities: dict[str, Any], message: str | None = None, domain: str = "inventory"):
    from app.services.chatbot.head.grounding import ground

    verdict, _notes = ground(db, {"entities": list(entities), "domain_hint": domain}, message=message)
    return verdict


def _specs(entities):
    return [e for e in entities if isinstance(e, dict) and e.get("hint") == "specification"]


# --------------------------------------------------------------------------- grounding


def test_g1_mm_number_on_a_category_entity_becomes_a_size_specification(vdb):
    """RED: the pair stays on the category entity and no specification entity exists."""
    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    specs = _specs(verdict["entities"])
    assert [(s["spec_key"], s["spec_value"]) for s in specs] == [("size", 600)], verdict["entities"]
    assert specs[0]["current_message"] is True


def test_g1b_the_category_entity_keeps_its_subject_and_drops_the_pair(vdb):
    """The subject still grounds; the pair is not left on it for a later reader to guess at."""
    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    subjects = [e for e in verdict["entities"] if e.get("hint") == "category"]
    assert subjects, verdict["entities"]
    assert all(not e.get("spec_key") for e in subjects), subjects


def test_g2_an_enum_pair_on_a_category_entity_keeps_its_key(vdb):
    """RED: a finish the parser put on the category entity is a finish specification."""
    verdict = _ground(
        vdb, _category("gunmetal basin", "basin", spec_key="finish", spec_value="gunmetal"),
        message="gunmetal basin got stock?",
    )
    specs = _specs(verdict["entities"])
    assert ("finish", "gunmetal") in [(s["spec_key"], s["spec_value"]) for s in specs], verdict["entities"]


def test_g3_a_category_entity_without_a_pair_adds_no_specification(vdb):
    """Guard: nothing new appears when the parser emitted no pair."""
    verdict = _ground(vdb, _category("basin"), message="basin got stock?")
    assert _specs(verdict["entities"]) == [], verdict["entities"]


def test_g4_an_unknown_key_on_a_category_entity_is_not_invented(vdb):
    """Guard: a key the registry does not hold, with a word value, grounds to nothing new."""
    verdict = _ground(
        vdb, _category("zzt basin", "basin", spec_key="zzt_not_a_key", spec_value="zzt"),
        message="zzt basin got stock?",
    )
    assert [s for s in _specs(verdict["entities"]) if s.get("spec_value") is not None] == [], verdict["entities"]


# --------------------------------------------------------------------------- lane mapping


def test_m1_the_lane_sends_the_size_constraint(vdb):
    """RED: `spec_constraints_for` reads only specification entities, so the size never left."""
    from app.services.chatbot.lanes.business import fetch

    verdict = _ground(
        vdb, _category("600mm basin", "basin", spec_key="dim_width", spec_value=600),
        message="600mm basin got stock?",
    )
    constraints = fetch.spec_constraints_for({**verdict, "intent_hint": "check_stock"}, vdb) or []
    assert {"key": "size", "op": "eq", "value": 600} in constraints, constraints
    assert fetch.carries_size(constraints)


# --------------------------------------------------------------------------- the turn


STOCK_TOOL = "crm_inventory_stock_balance_list"
STOCK_PATH = "/api/v1/inventory/stock/balance"


def _seed_basins(session_factory) -> None:
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product
    from tests.chatbot.test_outstanding_lane import _seed_contact
    from tests.chatbot.test_promo_subject_filter import _class

    _seed_contact(session_factory, variables={})
    _class(session_factory, "Wash Basin", ("basin",))
    db = session_factory()
    _seed_registry(db)
    mm = lambda v: {"value": v, "unit": "mm"}  # noqa: E731
    for tag, extra in (
        ("L1", {"dim_length": mm(600)}),
        ("L2", {"dim_length": mm(600)}),
        ("W1", {"dim_width": mm(600)}),
        ("N1", {}),
    ):
        p = product(db, company_id=DEFAULT_COMPANY_ID, code=f"ZZT-SOC-{tag}-{uuid.uuid4().hex[:4]}")
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=p.id, values={"class": {"value": "Wash Basin"}, **extra}))
    db.commit()


def _route_backed_stock(monkeypatch, session_factory):
    """The stock tool answered by the real route and the real MCP presenter."""
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService
    from tests.chatbot.test_outstanding_lane import _present_response

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    user = {"id": "130c548f-048f-53b2-97a6-3a54676bea77", "email": "u@test.com", "auth_method": "api_key"}

    def _db():
        yield session_factory()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_or_api_key] = lambda: user
    app.dependency_overrides[apply_company_scope] = lambda: None
    client = TestClient(app)
    captured: list[tuple[str, dict[str, Any]]] = []

    def call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        if name != STOCK_TOOL:
            return json.dumps({"has_result": False, "items": []})
        params = {
            k: (json.dumps(v) if k == "spec_constraints" else v)
            for k, v in args.items()
            if k != "view" and v not in (None, "", [])
        }
        res = client.get(STOCK_PATH, params=params)
        assert res.status_code == 200, res.text
        return _present_response()(name, res.text)

    return call, captured


@pytest.fixture()
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_t1_size_on_the_category_entity_asks_length_or_width(session_factory, monkeypatch, _clear_overrides):
    """RED (crew trace e2a6498a): the size is ignored and every basin is listed. The real
    resolver reads the seeded basins, as on the owner's console."""
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_outstanding_lane import _run_turn
    from tests.chatbot import test_outstanding_lane as harness

    _seed_basins(session_factory)
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    qf = _parser_output(
        domain_hint="inventory", intent_hint="check_stock",
        entities=[_category("600mm basin", "basin", spec_key="dim_width", spec_value=600)],
    )
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="600mm basin got stock?",
        msg_id=f"zzt-soc-{uuid.uuid4().hex[:6]}", real_resolver=True,
    )
    reply = (result.reply or {}).get("text") or ""
    stock_calls = [a for n, a in captured if n == STOCK_TOOL]
    assert stock_calls, f"the stock tool must be asked: {captured}"
    sent = [c for a in stock_calls for c in (a.get("spec_constraints") or [])]
    assert {"key": "size", "op": "eq", "value": 600} in sent, stock_calls
    assert "Length" in reply and "Width" in reply, reply
    assert "ZZT-SOC-" not in reply, f"no basin may be listed before the axis is known: {reply}"
