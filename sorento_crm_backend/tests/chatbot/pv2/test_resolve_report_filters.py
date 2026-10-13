"""PV2-RESOLVE `resolve_report_spec`: report filter keys -> the live ids the report route takes.

CONTRACT-PV2-13oct section 4 (Resolver protocol) + section 5 (Reports): index keys never from a
phrase; customer names exact, else real-name candidates. No LLM, no network.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.services.chatbot.pv2.resolve import DbResolver, Held

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def spec(*filters):
    return NS(filters=[NS(dimension=d, op=op, keys=list(keys), values=[]) for d, op, keys in filters])


EMPTY = Held(domain=None, refs={}, specs=[], attachment_type=None, period=None, report=None, idea_draft_id=None)


def resolver(db, **pinned):
    return DbResolver(db, pinned={k: set(v) for k, v in pinned.items()})


def test_brand_and_category_keys_become_ids_with_display_labels(db):
    b = S.brand(db, "Sorento Brand", code="SORENTO")
    c = S.category(db, class_label="Basin")
    out = resolver(db, brand={"SORENTO"}, product_class={"basin"}).resolve_report_spec(
        spec(("brand", "in", ["SORENTO"]), ("category", "in", ["basin"])), held=EMPTY
    )
    assert out.status == "exact"
    assert out.refs["brand"] == [{"kind": "brand", "key": b.id, "label": "Sorento Brand"}]
    assert out.refs["category"] == [{"kind": "category", "key": c.id, "label": "Basin"}]


def test_an_unpinned_brand_key_is_unresolved_never_looked_up(db):
    S.brand(db, "Sorento Brand", code="SORENTO")
    out = resolver(db, brand=set()).resolve_report_spec(spec(("brand", "in", ["SORENTO"])), held=EMPTY)
    assert out.status == "none" and out.unresolved == ["brand"] and out.refs == {}


def test_exact_customer_name_resolves_to_the_customer_id(db):
    cust = S.customer(db, "C-001", "Acme Trading")
    out = resolver(db).resolve_report_spec(spec(("customer", "in", ["acme  trading"])), held=EMPTY)
    assert out.status == "exact"
    assert out.refs["customer"] == [{"kind": "customer", "key": cust.id, "label": "Acme Trading"}]


def test_inexact_customer_name_offers_real_names_keyed_by_id(db):
    a = S.customer(db, "C-001", "Acme Trading")
    b = S.customer(db, "C-002", "Acme Hardware")
    out = resolver(db).resolve_report_spec(spec(("customer", "in", ["acme"])), held=EMPTY)
    assert out.status == "ambiguous" and out.phrase == "acme"
    assert sorted(o["label"] for o in out.options) == ["Acme Hardware", "Acme Trading"]
    assert {o["key"] for o in out.options} == {a.id, b.id}
    assert all(o["payload"]["dimension"] == "customer" for o in out.options)
    for o in out.options:
        assert "C-00" not in o["label"]
        S.assert_clean_label(o["label"])


def test_filters_the_engine_cannot_run_are_left_to_the_report_lane(db):
    out = resolver(db).resolve_report_spec(
        spec(("customer", "not_in", ["acme"]), ("channel", "in", ["online"])), held=EMPTY
    )
    assert out.status == "exact" and out.refs == {} and out.unresolved == []


def test_a_dimension_this_resolver_does_not_hold_stays_unresolved(db):
    out = resolver(db).resolve_report_spec(spec(("sales_agent", "in", ["ali"])), held=EMPTY)
    assert out.status == "none" and out.unresolved == ["sales_agent"]


def test_a_product_filter_rides_on_product_ids_for_chat_and_sales(db):
    from app.services.chatbot.pv2 import report_measures as rm
    from app.services.chatbot.pv2.reports import _filter_values

    one = S.product(db, "SRTWC286-A")
    two = S.product(db, "SRTWC286-B")
    out = resolver(db).resolve_report_spec(spec(("product", "in", ["SRTWC286"])), held=EMPTY)
    assert out.status == "exact"
    assert sorted(r["key"] for r in out.refs["product"]) == sorted([one.id, two.id])
    assert [r["label"] for r in out.refs["product"]] == ["SRTWC286-A", "SRTWC286-B"]
    keys = [r["key"] for r in out.refs["product"]]
    for ds in (rm.CHAT, rm.SALES):
        values, line = _filter_values(ds, ds.dimensions["product"], keys)
        assert line is None and values == {"product_ids": keys}
