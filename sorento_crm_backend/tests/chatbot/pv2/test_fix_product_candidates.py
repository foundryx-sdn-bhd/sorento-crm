"""PV2-FIX U1: a product phrase that is not an exact code goes through v1's product search.

Red tests written from the UAC (U1.1 to U1.7). Blank Postgres schema, no LLM.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.resolve import CUSTOMER_CANDIDATE_CAP, DbResolver, Entity, Held

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _resolve(db, phrase):
    ent = Entity(kind="product", key=None, phrase=phrase, op="set", refers_to_held=False)
    out = DbResolver(db, pinned={}).resolve_entities([ent], held=Held())
    assert len(out) == 1
    return out[0]


def _clean(res):
    for o in res.options:
        S.assert_clean_label(o.label)
    for r in res.refs:
        S.assert_clean_label(r.label)


def test_u1_1_family_phrase_asks_with_the_real_variant_products(db):
    for code in ("SRTWF12C", "SRTWF12C-BL-NEW", "SRTWF12C-GM-NEW", "MKS9505-SS-HC"):
        S.product(db, code)
    res = _resolve(db, "WF12C")
    assert res.status == "ambiguous"
    assert {o.key for o in res.options} == {"SRTWF12C", "SRTWF12C-BL-NEW", "SRTWF12C-GM-NEW"}
    for o in res.options:
        assert o.key in o.label, f"label {o.label!r} does not show the product code"
    _clean(res)


def test_u1_2_unique_substring_hit_is_used_exactly(db):
    S.product(db, "MKS9505-SS-HC")
    S.product(db, "SRTWC286-A")
    res = _resolve(db, "9505-SS")
    assert res.status == "exact"
    assert res.refs[0].key == "MKS9505-SS-HC"
    _clean(res)


def test_u1_2_did_you_mean_is_asked_never_auto_resolved(db):
    # v1's did-you-mean (family / trigram alternatives) for a code with no prefix or
    # substring hit. A single candidate is still ASKED. If pg_trgm alternatives cannot
    # find it on the blank schema this test stays and the lane coder reports it.
    S.product(db, "MKS9505-SS-HC")
    res = _resolve(db, "MKS9505-HC-SS")
    assert res.status == "ambiguous"
    assert "MKS9505-SS-HC" in {o.key for o in res.options}
    _clean(res)


def test_u1_1_candidates_are_capped(db):
    for n in range(1, 13):
        S.product(db, f"SRTZZ{9000 + n}")
    res = _resolve(db, "ZZ90")
    assert res.status == "ambiguous"
    assert 1 < len(res.options) <= CUSTOMER_CANDIDATE_CAP
    _clean(res)


def test_u1_4_phrase_matching_nothing_is_none(db):
    S.product(db, "SRTWC286-A")
    res = _resolve(db, "QQQ123")
    assert res.status == "none"
    assert res.refs == [] and res.options == []


def test_u1_6_exact_code_is_still_exact(db):
    S.product(db, "SRTWC286-SH-200")
    S.product(db, "SRTWC286-SH-300")
    res = _resolve(db, "SRTWC286-SH-200")
    assert res.status == "exact"
    assert [r.key for r in res.refs] == ["SRTWC286-SH-200"]
    _clean(res)


def test_non_chat_searchable_product_never_appears_in_options(db):
    hidden = S.category(db, class_label="Hidden", searchable=False)
    S.product(db, "SRTWF12C")
    S.product(db, "SRTWF12C-HIDDEN", cat=hidden)
    res = _resolve(db, "WF12C")
    keys = {o.key for o in res.options} | {r.key for r in res.refs}
    assert "SRTWF12C-HIDDEN" not in keys
    assert "SRTWF12C" in keys
