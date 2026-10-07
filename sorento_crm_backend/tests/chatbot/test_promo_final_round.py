"""PROMO-SUBJECT-FILTER final round red tests. Fake names only."""
from __future__ import annotations

import pytest

from app.main import app
from app.models.marketing import PromotionType
from app.models.product import Product
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_promo_kind_paths import _continue_services, _mcp, _seed
from tests.chatbot.test_promo_kind_subject import _promo, _qf
from tests.chatbot.test_promo_subject_filter import HAS, _ask_qf, _say


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_named_level_with_nothing_live_there_never_says_there_is_no_flyer(session_factory, monkeypatch):
    """Multi-level contact (Sorento Office + Dealer) asks "sorento a3 flyer office"; the only
    live Sorento A3 flyer is at Dealer. "There is no Sorento A3 flyer right now." would be a
    wrong confident answer: that line is only for one held level or after the full probe."""
    ids = _seed(session_factory, sorento="none")
    db = session_factory()
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    prod = db.query(Product).filter(Product.brand_id == ids["srt"]).first()
    _promo(db, "ZZT SORENTO A3 FLYER LIVE", flyer, prod, levels=["sorento_dealer"])
    db.commit()
    _mcp(monkeypatch, session_factory)
    qf = _qf(kind="a3_flyer")
    qf["access_levels"] = ["Sorento Office"]
    result, _ = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="sorento a3 flyer office", msg_id="zzt-fr-level",
        resolve_services=_continue_services(ids["srt"]),
    )
    reply = (result.reply or {}).get("text") or ""
    assert "There is no Sorento A3 flyer right now." not in reply, reply
    assert reply.strip(), "an answer is expected: the empty-at-that-level reply or the stamped menu"


def test_a_brand_the_table_does_not_hold_is_echoed_as_typed_not_the_parsers_canonical(session_factory, monkeypatch):
    """AC-16: canonical_code "ZORBEX GROUP" matches no live brand row: the menu echoes the
    typed "zorbex"."""
    _seed_contact(session_factory, variables={})
    reply, _, _ = _say(
        session_factory, monkeypatch, _ask_qf("zorbex", "brand", canonical_code="ZORBEX GROUP"),
        "zorbex promo", "zzt-fr-echo", lambda n, a: HAS,
    )
    first = reply.splitlines()[0]
    assert first == "Which access level do you need for zorbex?", reply
