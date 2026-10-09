"""Owner rule (5 Oct 2026): no fuzzy / edit-distance / substring matching on user text.

The menu's brand echo (AC-16) and `brand_ids` on a kind ask come ONLY from a brand the
resolver already resolved to an id, never from how a typed word is spelled. Fake names
otherwise.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.main import app
from app.models.marketing import PromotionType
from app.models.product import Product
from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_promo_kind_paths import _mcp, _seed
from tests.chatbot.test_promo_kind_subject import NAMES, PROMO_TOOL, _promo, _qf


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _world(session_factory, monkeypatch):
    ids = _seed(session_factory, sorento="none")
    db = session_factory()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    prod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, prod, levels=["sorento_dealer", "sorento_office"])
    db.commit()
    return ids, _mcp(monkeypatch, session_factory)


def _exact_resolver(brand_id: str) -> ResolveGateServices:
    """Resolves the token "sorento" (exact, case-insensitive) to the brand row; nothing else."""

    def _resolve(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {"uuid": brand_id, "entity_type": "brand", "canonical_code": "Sorento", "match_tier": "exact"}
        hits = [r for r in asked if r.strip().lower() == "sorento"]
        return {
            "tokens": asked,
            "resolutions": [{"raw": r, "token": r, "matches": [match]} for r in hits],
            "unresolved_tokens": [r for r in asked if r not in hits],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in NAMES], resolve_entity=_resolve, probe=lambda **_: None
    )


def _ask(session_factory, monkeypatch, ids, raw, msg_id):
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=_qf(kind="a3_flyer", brand=None, typed=raw) if False else _typed(raw),
        text_body=f"{raw} a3 flyer", msg_id=msg_id, resolve_services=_exact_resolver(ids["srt"]),
    )
    return (result.reply or {}).get("text") or ""


def _typed(raw: str) -> dict[str, Any]:
    qf = _qf(kind="a3_flyer", brand=None)
    qf["entities"] = [{"raw": raw, "hint": "brand", "canonical_code": None, "current_message": True, "confident": True}]
    return qf


def _brand_ids(captured) -> list:
    return [a.get("brand_ids") for n, a in captured if n == PROMO_TOOL and a.get("brand_ids")]


@pytest.mark.parametrize("raw", ["sroentoo", "xsorentox"])
def test_an_unresolved_brand_word_is_echoed_as_typed_and_never_becomes_a_brand_id(session_factory, monkeypatch, raw):
    """A typo (edit distance) and a word that merely CONTAINS a brand name are not brands:
    D16 echo of the typed word, and no probe or fetch carries `brand_ids`."""
    ids, captured = _world(session_factory, monkeypatch)
    reply = _ask(session_factory, monkeypatch, ids, raw, f"zzt-nf-{raw}")
    assert _brand_ids(captured) == [], captured
    first = reply.splitlines()[0]
    assert raw in first, reply
    assert "Sorento" not in first, reply


def test_a_brand_the_resolver_resolved_is_named_by_its_real_name_and_filters_by_its_id(session_factory, monkeypatch):
    """Control: the resolver resolved "sorento" to the brand row: echo its brand_name, and
    send its id."""
    ids, captured = _world(session_factory, monkeypatch)
    reply = _ask(session_factory, monkeypatch, ids, "sorento", "zzt-nf-resolved")
    assert reply.startswith("Which access level do you need for Sorento?"), reply
    got = _brand_ids(captured)
    assert got and all(g == [ids["srt"]] for g in got), captured
