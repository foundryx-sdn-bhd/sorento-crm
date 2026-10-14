"""Scope (d): the series import resolves a sheet code to a SET or a FAMILY (UAC D1-D7).

Resolution per code, first hit wins, never fuzzy: exact product, then active set, then
family (`CODE-*` products and sets). Set members are listed on the series but never priced.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import text

from app.models.base import company_scope
from app.models.company import Company
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_set import ProductSet, ProductSetMember

from ._pg_fixture import blank_session

BASE = "/api/v1/project-sales"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


class _W:
    pass


@contextmanager
def _world():
    from app.models.projects import ProjectSeries

    with blank_session() as db:
        w = _W()
        w.db = db
        w.company_id = _sorento(db)
        uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
        cat = ProductCategory(id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name="zzt-sfam")
        db.add_all([uom, cat])
        db.flush()
        w.uom, w.cat = uom, cat
        w.series = ProjectSeries(id=_uid(), company_id=w.company_id, name="zzt-sfam series")
        db.add(w.series)
        db.flush()
        with company_scope(db, frozenset({w.company_id})):
            yield w


def _product(w, code, *, active=True, company_id=None):
    row = Product(
        id=_uid(), product_code=code, product_name=code, category_id=w.cat.id,
        base_uom_id=w.uom.id, list_price=Decimal("1000.00"), is_active=active,
        company_id=company_id or w.company_id,
    )
    w.db.add(row)
    w.db.flush()
    return row


def _set(w, code, members, *, active=True, company_id=None):
    ps = ProductSet(
        id=_uid(), set_code=code, name=code, is_active=active,
        company_id=company_id or w.company_id,
    )
    w.db.add(ps)
    w.db.flush()
    for i, m in enumerate(members):
        w.db.add(ProductSetMember(
            id=_uid(), product_set_id=ps.id, product_id=m.id, quantity=Decimal("1"),
            contributes_to_price=(i == 0), sort_order=i,
        ))
    w.db.flush()
    return ps


def _apply(w, codes, mode="append"):
    from app.services import project_pricing_service as pricing

    return pricing.apply_series_product_codes(w.db, series=w.series, codes=codes, mode=mode)


def _row(code, price=None, disc=None):
    from app.services.project_pricing_service import _SeriesRow

    return _SeriesRow(
        code=code,
        selling_price=None if price is None else Decimal(price),
        max_discount_pct=None if disc is None else Decimal(disc),
    )


def _on_series(w):
    from app.services import project_pricing_service as pricing

    ids = pricing.series_product_ids(w.db, w.series.id)
    codes = {p.product_code for p in w.db.query(Product).filter(Product.id.in_(list(ids))).all()} if ids else set()
    return codes


def _price(w, product):
    from app.services import project_pricing_service as pricing

    return pricing.series_pricing_map(w.db, w.series.id).get(product.id)


# ---------------------------------------------------------------- D2 family


def test_d2_family_variants_each_get_a_row_with_the_sheet_price():
    with _world() as w:
        a = _product(w, "ZSF1-SH")
        b = _product(w, "ZSF1-SH-150")
        _product(w, "ZSF12")  # continuation, not family
        report = _apply(w, [_row("ZSF1", "500", "5")])
        assert _on_series(w) == {"ZSF1-SH", "ZSF1-SH-150"}
        assert _price(w, a) == (Decimal("500"), Decimal("5"))
        assert _price(w, b) == (Decimal("500"), Decimal("5"))
        assert report["matched_codes"] == 1 and report["unmatched_codes"] == []


def test_d2_reimport_with_a_new_price_updates_every_variant():
    with _world() as w:
        a = _product(w, "ZSF2-A")
        b = _product(w, "ZSF2-B")
        _apply(w, [_row("ZSF2", "500", "5")])
        _apply(w, [_row("ZSF2", "650", "7")])
        assert _price(w, a) == (Decimal("650"), Decimal("7"))
        assert _price(w, b) == (Decimal("650"), Decimal("7"))


def test_d1_family_ignores_spaces_in_the_sheet_code():
    with _world() as w:
        _product(w, "ZSF3-CR-L")
        _apply(w, ["ZSF3- CR"])
        assert _on_series(w) == {"ZSF3-CR-L"}


# ------------------------------------------------------------------ D3 sets


def test_d3_set_members_listed_but_never_priced():
    with _world() as w:
        p1, p2 = _product(w, "ZSF4-PED"), _product(w, "ZSF4-CIS")
        _set(w, "ZSF4SET", [p1, p2])
        report = _apply(w, [_row("ZSF4 SET", "900", "8")])
        assert _on_series(w) == {"ZSF4-PED", "ZSF4-CIS"}
        assert _price(w, p1) == (None, None)
        assert _price(w, p2) == (None, None)
        assert report["unmatched_codes"] == []


def test_d3_member_already_priced_keeps_its_numbers():
    with _world() as w:
        p1, p2 = _product(w, "ZSF5-PED"), _product(w, "ZSF5-CIS")
        _set(w, "ZSF5SET", [p1, p2])
        _apply(w, [_row("ZSF5-PED", "300", "4")])
        _apply(w, [_row("ZSF5SET", "900", "8")])
        assert _price(w, p1) == (Decimal("300"), Decimal("4"))
        assert _price(w, p2) == (None, None)


# --------------------------------------------------------------- D4 report


def test_d4_report_lists_resolved_with_via_and_sorted_members():
    with _world() as w:
        p1, p2 = _product(w, "ZSF6-PED"), _product(w, "ZSF6-CIS")
        _set(w, "ZSF6SET", [p1, p2])
        _product(w, "ZSF7-B")
        _product(w, "ZSF7-A")
        _product(w, "ZSF8-X")
        _set(w, "ZSF8-SET", [p1])  # family hitting a set
        report = _apply(w, ["ZSF6SET", "ZSF7", "ZSF8", "ZSF-NOTHING"])
        by_code = {r["code"]: r for r in report["resolved"]}
        assert by_code["ZSF6SET"] == {"code": "ZSF6SET", "via": "set", "members": ["ZSF6-CIS", "ZSF6-PED"]}
        assert by_code["ZSF7"] == {"code": "ZSF7", "via": "family", "members": ["ZSF7-A", "ZSF7-B"]}
        assert by_code["ZSF8"]["via"] == "family"
        assert by_code["ZSF8"]["members"] == ["ZSF6-PED", "ZSF8-X"]
        assert report["unmatched_codes"] == ["ZSF-NOTHING"]
        assert report["matched_codes"] == 3


def test_d4_exact_codes_are_not_in_resolved():
    with _world() as w:
        _product(w, "ZSF9")
        report = _apply(w, ["ZSF9"])
        assert report["resolved"] == []


# ------------------------------------------------------------ D5 exact wins


def test_d5_exact_product_beats_its_own_variants():
    with _world() as w:
        _product(w, "ZSF10")
        _product(w, "ZSF10-SH")
        report = _apply(w, ["ZSF10"])
        assert _on_series(w) == {"ZSF10"}
        assert report["resolved"] == []


def test_d5_set_code_beats_family_of_other_codes():
    with _world() as w:
        p1 = _product(w, "ZSF11-PED")
        _product(w, "ZSF11-OTHER")
        _set(w, "ZSF11", [p1])
        report = _apply(w, ["ZSF11"])
        assert _on_series(w) == {"ZSF11-PED"}
        assert report["resolved"][0]["via"] == "set"


# ------------------------------------------- D3b set parts inside a family


def test_d3b_family_set_parts_get_a_row_but_no_price_others_do():
    with _world() as w:
        plain = _product(w, "ZSF20-SH-150")
        part = _product(w, "ZSF20-SC")
        _set(w, "ZSF20-RL", [part])
        _apply(w, [_row("ZSF20", "500", "5")])
        assert _on_series(w) == {"ZSF20-SH-150", "ZSF20-SC"}
        assert _price(w, plain) == (Decimal("500"), Decimal("5"))
        assert _price(w, part) == (None, None)


def test_d3b_part_of_an_inactive_set_still_gets_the_price():
    with _world() as w:
        old = _product(w, "ZSF21-OLD")
        _set(w, "ZSF21-OLDSET", [old], active=False)
        _apply(w, [_row("ZSF21", "500", "5")])
        assert _price(w, old) == (Decimal("500"), Decimal("5"))


# --------------------------------------------------------------- D1 negatives


def test_d1_inactive_products_and_sets_never_resolve():
    with _world() as w:
        _product(w, "ZSF12-OFF", active=False)
        p = _product(w, "ZSF13-PED")
        _set(w, "ZSF13SET", [p], active=False)
        _set(w, "ZSF14-SET", [p], active=False)
        report = _apply(w, ["ZSF12", "ZSF13SET", "ZSF14"])
        assert _on_series(w) == set()
        assert report["unmatched_codes"] == ["ZSF12", "ZSF13SET", "ZSF14"]


def test_d1_continuation_and_embedded_codes_are_not_family():
    with _world() as w:
        _product(w, "ZSF15")
        _product(w, "ZSF152")        # continuation of ZSF15 without a dash
        _product(w, "TPE-9204")
        report = _apply(w, ["ZSF1", "PSJLTPE-9204"])
        assert _on_series(w) == set()
        assert sorted(report["unmatched_codes"]) == ["PSJLTPE-9204", "ZSF1"]


# --------------------------------------------------------------- D7 company


def test_d7_another_companys_set_and_family_never_resolve():
    with _world() as w:
        other = Company(id=_uid(), name=f"zzt-other-{_uid()[:6]}", code=f"ZO{_uid()[:6]}")
        w.db.add(other)
        w.db.flush()
        fp = _product(w, "ZSF16-X", company_id=other.id)
        _set(w, "ZSF17SET", [fp], company_id=other.id)
        report = _apply(w, ["ZSF16", "ZSF17SET"])
        assert _on_series(w) == set()
        assert sorted(report["unmatched_codes"]) == ["ZSF16", "ZSF17SET"]


# ------------------------------------------------------------------- route


from .test_project_series_product_routes import (  # noqa: E402,F401
    BASE as _ROUTE_BASE,
    _category,
    _product as _route_product,
    _series as _route_series,
    _uom,
    api,
)


def test_d4_paste_route_returns_resolved(api):
    client, db, _company_id, _user_id, _project = api
    uom = _uom(db)
    category = _category(db)
    _route_product(db, "ZZTSF-FAM-A", category.id, uom)
    _route_product(db, "ZZTSF-FAM-B", category.id, uom)
    db.commit()
    series = _route_series(client)
    response = client.post(
        f"{_ROUTE_BASE}/config/series/{series['id']}/products",
        json={"codes": ["ZZTSF-FAM"], "mode": "append"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unmatched_codes"] == []
    assert body["resolved"] == [
        {"code": "ZZTSF-FAM", "via": "family", "members": ["ZZTSF-FAM-A", "ZZTSF-FAM-B"]}
    ]
