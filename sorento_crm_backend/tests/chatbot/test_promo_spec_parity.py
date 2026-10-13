"""SPEC-PROMO-PARAMS owner hand test (13 Oct): "any 600mm basin promo" listed per-product price lines.

Owner ruling: a size or spec in a promotion ask only RESOLVES the products, as for stock; the
answer is the existing promotion-list answer (the promotion documents and their files) for those
products. Parity: the reply to "600mm basin promo" is the reply to "promotion for <the 600 mm
basins>", the same promotions-list call, filtered to promotions holding the resolved products.
Route-backed MCP (real presenter), the REAL resolver route over the seeded DB. Fake names only.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import PromotionType
from app.models.product_spec import ProductSpecifications
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_lane_require import _seed_registry
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_promo_brand_category import _brand, _prod
from tests.chatbot.test_promo_kind_subject import _promo
from tests.chatbot.test_promo_owner_round2 import _mcp as _main_mcp
from tests.chatbot.test_promo_round3_detection import _go
from tests.chatbot.test_promo_subject_filter import _class, _pick_qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _world(session_factory) -> dict[str, Any]:
    _seed_contact(session_factory, variables={})
    _class(session_factory, "Wash Basin", ("basin",))
    db = session_factory()
    _seed_registry(db)
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
    srt = _brand(db, "Sorento")
    b600, b500 = _prod(db, srt, "Wash Basin", "ZZTWB600"), _prod(db, srt, "Wash Basin", "ZZTWB500")
    for prod, length in ((b600, 600), (b500, 500)):
        spec = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == prod.id).one()
        spec.values = {**spec.values, "dim_length": {"value": length, "unit": "mm"}}
    db.flush()
    p600 = _promo(db, "ZZT BASIN 600 PROMO", std, b600, levels=["sorento_office", "sorento_dealer", "end_user"])
    p500 = _promo(db, "ZZT BASIN 500 PROMO", std, b500, levels=["sorento_office", "sorento_dealer", "end_user"])
    db.commit()
    return {"b600": b600, "b500": b500, "p600": p600, "p500": p500}


def _mcp(monkeypatch, session_factory):
    """Main's route-backed promotions list (`test_promo_owner_round2._mcp`), with
    `spec_constraints` sent as the JSON string the MCP server sends (`server._OBJECT_UNIONS`)."""
    import json

    from tests.chatbot import test_outstanding_lane as lane_harness

    captured = _main_mcp(monkeypatch, session_factory)
    call, _ = lane_harness._capturing_mcp()

    def _call(name: str, args: dict[str, Any]) -> Any:
        sent = dict(args)
        if isinstance(sent.get("spec_constraints"), list):
            sent["spec_constraints"] = json.dumps(sent["spec_constraints"])
        out = call(name, sent)
        captured[-1] = (name, dict(args))
        return out

    monkeypatch.setattr(lane_harness, "_capturing_mcp", lambda response=None: (_call, captured))
    return captured


def _ask(entities: list[dict[str, Any]]) -> dict[str, Any]:
    return _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=entities)


def _answer(session_factory, monkeypatch, qf, text, tag) -> tuple[str, list]:
    captured = _mcp(monkeypatch, session_factory)
    first = _go(session_factory, monkeypatch, qf, text, f"zzt-psp-{tag}-ask")
    if "1." in first and "promotion" in first.lower() and "Which" in first:
        captured.clear()
        return _go(session_factory, monkeypatch, _pick_qf(1), "1", f"zzt-psp-{tag}-pick"), list(captured)
    return first, list(captured)


def _body(reply: str) -> list[str]:
    """The promotion lines, without the header that names the subject."""
    return [line for line in reply.split("\n")[1:] if line.strip()]


def test_a_size_in_a_promotion_ask_gets_the_promotion_list_answer_for_the_resolved_products(session_factory, monkeypatch):
    """RED: the size ask answered from the promotion-products tool, one price line per product."""
    w = _world(session_factory)
    size = {"raw": "600mm", "hint": "specification", "canonical_code": None, "current_message": True,
            "confident": True, "spec_key": "size", "spec_value": 600}
    basin = {"raw": "basin", "hint": "category", "canonical_code": None, "current_message": True, "confident": True}
    by_size, size_calls = _answer(session_factory, monkeypatch, _ask([size, basin]), "any 600mm basin promo", "size")

    code = {"raw": w["b600"].product_code, "hint": "product", "canonical_code": w["b600"].product_code,
            "current_message": True, "confident": True}
    by_code, code_calls = _answer(session_factory, monkeypatch, _ask([code]), f"promotion for {w['b600'].product_code}", "code")

    assert {n for n, _a in size_calls} == {n for n, _a in code_calls} == {"crm_marketing_promotions_list"}, (size_calls, code_calls)
    assert "ZZT BASIN 600 PROMO" in by_size and "ZZT BASIN 500 PROMO" not in by_size, by_size
    assert "MYR" not in by_size, by_size
    assert _body(by_size) == _body(by_code), (by_size, by_code)
