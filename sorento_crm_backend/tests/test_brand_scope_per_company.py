"""RED tests - the brand scope applies per company (BRAND-SCOPE-PER-COMPANY).

`documentation/plans/chatbot/brand-scope-per-company-6oct-acceptance-criteria.md`.

World (`tests/_brand_scope_seed.py`): company SRT owns brands MOCHA and SORENTO and three products
(MOCHA, SORENTO, no brand); company MCH (`world.mch`) owns NO brand and two NULL-brand products.
Scope `[SORENTO]` must keep the SORENTO product, drop the other two SRT products (the parent
lane's Q1), and let EVERY MCH product through: MCH owns none of the scoped brands.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.base import set_brand_scope, set_company_scope
from app.models.order import OrderLine, SalesOrderLine
from app.models.product import Brand, Product
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._brand_scope_seed import api, contact, db, leaks, world  # noqa: F401  (fixtures by name)
from tests._pg_fixture import unique_code

BASE = "/api/v1"


def _svc():
    from app.services import contact_brand_scope

    return contact_brand_scope


def _all_codes(world) -> list[str]:
    return [*world.codes.values(), *world.mch.codes.values()]


def _visible(db, world) -> set[str]:
    return {p.product_code for p in db.query(Product).filter(Product.product_code.in_(_all_codes(world))).all()}


@pytest.fixture
def dual_session(db, world):
    """The dual contact's session: company scope SRT + MCH, brand scope [SORENTO]."""
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    set_brand_scope(db, frozenset({world.sorento.id}))
    return db


def _expected(world) -> set[str]:
    return {world.codes["sorento"], *world.mch.codes.values()}


# --------------------------------------------------------------------- AC-1 / AC-2 session criterion


def test_session_criterion_passes_every_product_of_a_company_with_no_scoped_brand(dual_session, world) -> None:
    """AC-1: SORENTO of SRT and both MCH products; the MOCHA and NULL-brand SRT products stay out."""
    assert _visible(dual_session, world) == _expected(world)


def test_without_a_brand_scope_the_dual_session_sees_all_five(db, world) -> None:
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    assert _visible(db, world) == set(_all_codes(world))


def test_join_from_a_line_table_keeps_the_mch_line(dual_session, world) -> None:
    """AC-2: an MCH DO line survives a join to Product; the SRT MOCHA line does not."""
    mch = world.mch
    rows = (
        dual_session.query(OrderLine.product_id)
        .join(Product, Product.id == OrderLine.product_id)
        .filter(OrderLine.order_id.in_([world.do_mixed.id, mch.do_one.id]))
        .all()
    )
    assert {str(r[0]) for r in rows} == {world.p_sorento.id, mch.p_one.id}


def test_aliased_product_is_filtered_per_company(dual_session, world) -> None:
    from sqlalchemy.orm import aliased

    alias = aliased(Product)
    got = {r.product_code for r in dual_session.query(alias).filter(alias.product_code.in_(_all_codes(world))).all()}
    assert got == _expected(world)


def test_a_query_that_joins_brands_itself_is_not_captured(dual_session, world) -> None:
    """The companies-of-scope subquery must not correlate to an outer `brands` join."""
    got = {
        p.product_code
        for p in dual_session.query(Product)
        .outerjoin(Brand, Brand.id == Product.brand_id)
        .filter(Product.product_code.in_(_all_codes(world)))
        .all()
    }
    assert got == _expected(world)


# --------------------------------------------------------------------- AC-3 Core clauses


def test_product_in_scope_clauses_keep_mch_lines(dual_session, world) -> None:
    mch = world.mch
    q = dual_session.query(OrderLine.product_id).filter(
        OrderLine.order_id.in_([world.do_mixed.id, world.do_null_only.id, mch.do_one.id]),
        *_svc().product_in_scope_clauses(dual_session, OrderLine.product_id),
    )
    assert {str(r[0]) for r in q.all()} == {world.p_sorento.id, mch.p_one.id}


def test_complaint_in_scope_clauses_keep_a_complaint_with_an_mch_line(dual_session, world) -> None:
    from app.models.complaints import Complaint, ComplaintProductLine

    mch = world.mch
    c = Complaint(
        id=str(uuid.uuid4()), complaint_number=unique_code("CMP"), complaint_date=None,
        customer_name="ZZT MOCHA CUSTOMER", product_code=mch.p_one.product_code, status="approved",
    )
    dual_session.add(c)
    dual_session.flush()
    dual_session.add(ComplaintProductLine(id=str(uuid.uuid4()), complaint_id=c.id, product_code=mch.p_one.product_code, quantity="1", sort_order=0))
    dual_session.flush()
    ids = [c.id, *(x.id for x in world.complaints.values())]
    got = {
        r.id
        for r in dual_session.query(Complaint)
        .filter(Complaint.id.in_(ids), *_svc().complaint_in_scope_clauses(dual_session))
        .all()
    }
    assert got == {c.id, world.complaints["mixed"].id, world.complaints["sorento_only"].id}


# --------------------------------------------------------------------- AC-4 output guard helpers


def test_out_of_scope_codes_leave_mch_codes_alone(db, world) -> None:
    mch = world.mch
    scope = frozenset({world.sorento.id})
    out = _svc().out_of_scope_product_codes(db, scope, [*_all_codes(world), "ZZT-NO-SUCH-CODE"])
    assert out == {world.codes["mocha"], world.codes["null"]}
    assert not out & set(mch.codes.values())


def test_out_of_scope_ids_leave_mch_ids_alone(db, world) -> None:
    mch = world.mch
    scope = frozenset({world.sorento.id})
    ids = [p.id for p in (*world.products, *mch.products)]
    assert _svc().out_of_scope_product_ids(db, scope, ids) == {world.p_mocha.id, world.p_null.id}


def test_guard_helpers_read_the_brands_companies_with_company_scope_off(db, world) -> None:
    """A Mocha-only contact's session cannot see the SRT brand row, yet its scope must still
    exempt MCH (the stored list is the truth, not what the viewer can see)."""
    mch = world.mch
    set_company_scope(db, frozenset({mch.company_id}))
    scope = frozenset({world.sorento.id})
    assert _svc().out_of_scope_product_codes(db, scope, list(mch.codes.values())) == set()
    assert _svc().out_of_scope_product_ids(db, scope, [p.id for p in mch.products]) == set()


def test_brand_predicate_takes_the_company_column(db, world) -> None:
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    pred = _svc().brand_predicate(frozenset({world.sorento.id}), Product.brand_id, Product.company_id)
    got = {p.product_code for p in db.query(Product).filter(Product.product_code.in_(_all_codes(world))).filter(pred)}
    assert got == _expected(world)


# --------------------------------------------------------------------- AC-5 orders analytics filter


def test_scope_product_filter_keeps_mch_product_ids(dual_session, world) -> None:
    from app.services.order_service import scope_product_filter

    mch = world.mch
    stmt = scope_product_filter(dual_session, None)
    ids = {str(i) for (i,) in dual_session.execute(stmt).all()}
    assert {world.p_sorento.id, mch.p_one.id, mch.p_two.id} <= ids
    assert not ids & {world.p_mocha.id, world.p_null.id}


# --------------------------------------------------------------------- AC-6 sentinel stays closed


def test_no_brand_id_sentinel_hides_every_product_mch_included(db, world) -> None:
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    sentinel = frozenset({_svc().NO_BRAND_ID})
    set_brand_scope(db, sentinel)
    assert _visible(db, world) == set()
    assert _svc().in_scope_product_codes(db, _all_codes(world)) == set()
    products = Product.__table__
    core = db.execute(
        select(products.c.product_code).where(
            products.c.product_code.in_(_all_codes(world)),
            products.c.id.in_(select(products.c.id).where(_svc().brand_predicate(sentinel, products.c.brand_id, products.c.company_id))),
        )
    ).all()
    assert core == []
    set_brand_scope(db, None)
    assert _svc().out_of_scope_product_codes(db, sentinel, _all_codes(world)) == set(_all_codes(world))


def test_a_scope_of_only_deleted_brand_ids_hides_everything(db, world) -> None:
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    set_brand_scope(db, frozenset({str(uuid.uuid4())}))
    assert _visible(db, world) == set()


# --------------------------------------------------------------------- AC-13 an MCH brand appears later


def test_an_mch_brand_added_to_the_list_restricts_mch_too(db, world) -> None:
    mch = world.mch
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, mch.company_id}))
    mch_brand = Brand(id=str(uuid.uuid4()), brand_code=unique_code("MB")[:50], brand_name="ZZT MCH BRAND", company_id=mch.company_id)
    db.add(mch_brand)
    db.flush()
    mch.p_one.brand_id = mch_brand.id
    db.flush()
    # Not in the list yet: MCH is still unrestricted, both MCH products pass.
    set_brand_scope(db, frozenset({world.sorento.id}))
    assert _visible(db, world) == _expected(world)
    # In the list: MCH becomes a restricted company, its NULL-brand product goes out (Q1).
    set_brand_scope(db, frozenset({world.sorento.id, mch_brand.id}))
    assert _visible(db, world) == {world.codes["sorento"], mch.codes["mch_one"]}


# --------------------------------------------------------------------- AC-12 the worker re-stamp


def test_a_fresh_session_stamped_with_brand_ids_applies_the_per_company_rule(db, world) -> None:
    """Both workers do `set_brand_scope(db, frozenset(brand_ids))` on a session of their own."""
    from tests._pg_fixture import blank_session

    mch = world.mch
    with blank_session() as fresh:
        set_company_scope(fresh, None)
        set_brand_scope(fresh, frozenset(str(b) for b in [world.sorento.id]))
        got = {p.product_code for p in fresh.query(Product).filter(Product.product_code.in_(_all_codes(world))).all()}
    assert got == _expected(world), got


# --------------------------------------------------------------------- AC-8 .. AC-10 REST via X-API-Key


REST_PATHS = [
    ("stock_balance", f"{BASE}/inventory/stock/balance", {"query": "ZZT"}),
    ("products_search", f"{BASE}/master-data/products", {"query": "ZZT"}),
    ("orders_by_product", f"{BASE}/order-management/orders/by-product", {"product_query": "ZZT"}),
]


def _seen(resp, world) -> dict[str, bool]:
    return {**leaks(resp, world), **{k: c in resp.text for k, c in world.mch.codes.items()}}


@pytest.mark.parametrize(("name", "path", "params"), REST_PATHS, ids=[p[0] for p in REST_PATHS])
def test_dual_contact_sees_sorento_brand_and_every_mch_product(api, world, name, path, params) -> None:
    """AC-8."""
    resp = api.get(path, world.mch.dual, **params)
    assert resp.status_code == 200, resp.text
    assert _seen(resp, world) == {"mocha": False, "sorento": True, "null": False, "mch_one": True, "mch_two": True}, name


@pytest.mark.parametrize(("name", "path", "params"), REST_PATHS, ids=[p[0] for p in REST_PATHS])
def test_mocha_only_contact_sees_every_mch_product(api, world, name, path, params) -> None:
    """AC-9: the Sorento brand list on a Mocha-only contact hides nothing of Mocha."""
    resp = api.get(path, world.mch.mocha_only, **params)
    assert resp.status_code == 200, resp.text
    assert _seen(resp, world) == {"mocha": False, "sorento": False, "null": False, "mch_one": True, "mch_two": True}, name


def test_sorento_only_contact_is_unchanged(api, world) -> None:
    """AC-10: a SRT-only contact scoped to SORENTO sees SORENTO and nothing of MCH."""
    srt_only = contact(api.db, brand_ids=[world.sorento.id])
    api.db.commit()
    world.mch  # the MCH rows exist
    resp = api.get(f"{BASE}/inventory/stock/balance", srt_only, query="ZZT")
    assert resp.status_code == 200, resp.text
    assert _seen(resp, world) == {"mocha": False, "sorento": True, "null": False, "mch_one": False, "mch_two": False}


def test_dual_contact_out_of_scope_sorento_code_still_reads_as_nonexistent(api, world) -> None:
    """Q4 of the parent card holds for the dual contact: the MOCHA-brand code of SRT and the
    unbranded SRT code answer like a code that does not exist, while an MCH code answers."""
    dual = world.mch.dual
    ghost = "ZZT-GHOST-" + uuid.uuid4().hex[:8].upper()
    miss = api.get(f"{BASE}/inventory/stock/balance", dual, query=ghost)
    for code in (world.codes["mocha"], world.codes["null"]):
        out = api.get(f"{BASE}/inventory/stock/balance", dual, query=code)
        assert out.text.replace(code, "<X>") == miss.text.replace(ghost, "<X>")
    hit = api.get(f"{BASE}/inventory/stock/balance", dual, query=world.mch.codes["mch_one"]).json()["data"]
    assert [r["product"]["product_code"] for r in hit] == [world.mch.codes["mch_one"]]
    assert hit[0]["quantity_on_hand"] == 80
