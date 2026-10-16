"""OI-LINE-SPLIT slice 1: the whole-SO split API (UAC A/B/C).

GET/PUT /api/v1/project-sales/sales-orders/{pso_id}/line-splits. The SO is SO417636's
CSK14B shape: four lines of item A at one location (301 / 417 / 192 / 504) and one line of
item B; line 200 carries a pending AutoCount change 417 -> 14 (date 20 Oct -> 20 Sep), so its
base is the `from` side. Every row is seeded here (CI's database has no data).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.scm.outstanding_diff import QTY_CHANGED, DATE_AND_QTY_CHANGED  # noqa: F401
from tests.test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _build,
    _change,
    _confirm,
    api,
    world,
)
from tests.test_planning_changes import (
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _uid,
)

D_301 = date(2026, 9, 20)
D_417 = date(2026, 10, 20)
D_192 = date(2026, 11, 20)
D_504 = date(2026, 12, 20)
D_NEW = date(2027, 1, 2)

EDIT = "projects.projects.edit"
VIEW = "projects.projects.view"


def _url(pso_id) -> str:
    return f"{BASE}/sales-orders/{pso_id}/line-splits"


def _mirror(db, order, core_so, product, wh, *, line_no, qty, when, pending_to=None):
    core = _core_line(db, core_so, product, wh, qty_ordered=str(qty), required_date=when)
    line = _project_line(db, order, line_no=line_no, product=product, core_line=core)
    line.stock_location = wh.warehouse_code
    db.flush()
    return core, line


class _So:
    pass


def _seed(api, *, with_pending=True):
    """SO with lines 100/200/300/400 of item A at own_wh and 500 of item B; 200 is pending."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    item_b = _product(db)
    s = _So()
    s.client, s.world, s.db, s.core_so, s.order, s.item_b = client, world, db, core_so, order, item_b
    s.c100, s.l100 = _mirror(db, order, core_so, world.product, world.own_wh,
                             line_no=100, qty=301, when=D_301)
    # line 200 already carries the AutoCount change: 417 @ 20 Oct -> 14 @ 20 Sep
    s.c200, s.l200 = _mirror(db, order, core_so, world.product, world.own_wh,
                             line_no=200, qty=417, when=D_417)
    s.c300, s.l300 = _mirror(db, order, core_so, world.product, world.own_wh,
                             line_no=300, qty=192, when=D_192)
    s.c400, s.l400 = _mirror(db, order, core_so, world.product, world.own_wh,
                             line_no=400, qty=504, when=D_504)
    s.c500, s.l500 = _mirror(db, order, core_so, item_b, world.own_wh,
                             line_no=500, qty=50, when=D_301)
    db.commit()
    s.batch = None
    if with_pending:
        # the batch builder only reacts to a line the book holds: confirm it as Buy 417
        resp = _confirm(client, order.id, [_line_payload(s.l200.id, buy_qty="417")])
        assert resp.status_code == 200, resp.text
        s.c200.qty_ordered = Decimal("14")
        s.l200.qty = Decimal("14")
        s.c200.required_date = D_301
        s.l200.delivery_date = D_301
        db.commit()
        s.batch = _build(world, [
            _change(DATE_AND_QTY_CHANGED, s.c200, so_number=core_so.so_number,
                    old_date=D_417, new_date=D_301, old_qty="417", new_qty="14"),
        ], core_so, [str(s.c200.id)])
        assert s.batch is not None, "premise: the pending change row exists"
    return s


def _get(s):
    r = s.client.get(_url(s.order.id))
    assert r.status_code == 200, r.text
    return r.json()


def _line(payload, line_id):
    return next(x for x in payload["lines"] if x["line_id"] == str(line_id))


def _sp(qty, when, *, on=None, cancel=False, remark=""):
    return {"qty": qty, "delivery_date": None if cancel else when.isoformat(),
            "is_cancel": cancel, "sits_on_line_id": on, "remark": remark}


def _put(s, lines):
    return s.client.put(_url(s.order.id), json={"lines": lines})


def _valid_200(s):
    return {"line_id": str(s.l200.id), "splits": [
        _sp(14, D_301, on=str(s.l200.id)),
        _sp(236, D_192, on=str(s.l300.id), remark="to 300"),
        _sp(167, D_NEW, on=str(s.l400.id)),
    ]}


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_shape_ordering_and_base_rules(api):
    s = _seed(api)
    out = _get(s)
    assert out["pso_id"] == str(s.order.id)
    assert out["so_number"] == s.core_so.so_number
    for key in ("customer", "project"):
        assert key in out
    # ordered by item_code, location, line_no: item A lines first by line_no, then item B
    # (item codes are random, so compare as the contract orders them)
    codes = [(x["item_code"], x["location"], x["line_no"]) for x in out["lines"]]
    assert codes == sorted(codes)
    assert {x["line_no"] for x in out["lines"]} == {100, 200, 300, 400, 500}
    for key in ("line_id", "line_no", "item_code", "location", "base_qty", "base_date",
                "now_qty", "now_date", "pending_change", "delivered_qty", "splits"):
        assert key in out["lines"][0], key
    l100, l200 = _line(out, s.l100.id), _line(out, s.l200.id)
    # not pending: base is the line's own open qty and date
    assert Decimal(str(l100["base_qty"])) == Decimal("301")
    assert l100["base_date"] == D_301.isoformat()
    assert not l100["pending_change"]
    assert l100["location"] == s.world.own_wh.warehouse_code
    assert l100["item_code"] == s.world.product.product_code
    # pending: base is the change's `from`, now is the mirror's current qty and date
    assert Decimal(str(l200["base_qty"])) == Decimal("417")
    assert l200["base_date"] == D_417.isoformat()
    assert Decimal(str(l200["now_qty"])) == Decimal("14")
    assert l200["now_date"] == D_301.isoformat()
    assert l200["pending_change"]
    assert l200["splits"] == []


def test_get_a_saved_split_keeps_the_base_it_was_made_against(api):
    s = _seed(api)
    r = _put(s, [_valid_200(s)])
    assert r.status_code == 200, r.text
    # AutoCount moves on: the pending change is gone and the line now reads 14 / 20 Sep
    s.db.query(type(s.batch)).filter_by(id=s.batch.id).delete()
    s.db.commit()
    l200 = _line(_get(s), s.l200.id)
    assert Decimal(str(l200["base_qty"])) == Decimal("417")
    assert l200["base_date"] == D_417.isoformat()


def test_get_requires_the_view_permission(api, monkeypatch):
    from app.services.user_service import UserPermissionService

    s = _seed(api)
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission",
                        lambda self, uid, slug: False)
    assert s.client.get(_url(s.order.id)).status_code == 403


def test_another_companys_order_is_not_visible(api):
    from app.models.base import set_company_scope
    from app.models.company import Company

    s = _seed(api)
    assert s.client.get(_url(s.order.id)).status_code == 200, "visible to its own company"
    set_company_scope(s.db, None)
    other = Company(id=_uid(), code=f"ZZO{_uid()[:8]}", name="zzt other company")
    s.db.add(other)
    s.db.flush()
    set_company_scope(s.db, frozenset({str(other.id)}))
    assert s.client.get(_url(s.order.id)).status_code == 404
    assert _put(s, []).status_code == 404


# ---------------------------------------------------------------------------
# PUT happy paths
# ---------------------------------------------------------------------------


def test_put_round_trips_splits_including_sits_on_another_line_and_a_cancel(api):
    s = _seed(api)
    cancel_line = {"line_id": str(s.l100.id), "splits": [
        _sp(200, D_301, on=str(s.l100.id)),
        _sp(101, None, on=str(s.l100.id), cancel=True, remark="customer dropped it"),
    ]}
    r = _put(s, [_valid_200(s), cancel_line])
    assert r.status_code == 200, r.text
    for out in (r.json(), _get(s)):
        l200 = _line(out, s.l200.id)
        assert [Decimal(str(x["qty"])) for x in l200["splits"]] == [14, 236, 167]
        assert [x["delivery_date"] for x in l200["splits"]] == [
            D_301.isoformat(), D_192.isoformat(), D_NEW.isoformat()]
        assert [x["sits_on_line_id"] for x in l200["splits"]] == [
            str(s.l200.id), str(s.l300.id), str(s.l400.id)]
        assert l200["splits"][1]["remark"] == "to 300"
        assert all(x["id"] and not x["is_cancel"] for x in l200["splits"])
        l100 = _line(out, s.l100.id)
        assert [x["is_cancel"] for x in l100["splits"]] == [False, True]
        assert l100["splits"][1]["delivery_date"] is None
        assert Decimal(str(l100["splits"][1]["qty"])) == Decimal("101")
        assert l100["splits"][1]["sits_on_line_id"] == str(s.l100.id)
        # lines not named stay unsplit
        assert _line(out, s.l300.id)["splits"] == []


def test_put_replaces_the_previous_splits_of_a_named_line(api):
    s = _seed(api)
    assert _put(s, [_valid_200(s)]).status_code == 200
    again = {"line_id": str(s.l200.id), "splits": [
        _sp(400, D_417, on=str(s.l200.id)), _sp(17, D_NEW, on=str(s.l200.id))]}
    assert _put(s, [again]).status_code == 200
    assert [Decimal(str(x["qty"])) for x in _line(_get(s), s.l200.id)["splits"]] == [400, 17]


# ---------------------------------------------------------------------------
# PUT refusals (422) and atomicity
# ---------------------------------------------------------------------------


def _refused(r, line_no=None):
    assert r.status_code == 422, r.text
    if line_no is not None:
        assert str(line_no) in r.text, f"message must name line {line_no}: {r.text}"


def test_put_422_when_the_sum_is_not_the_base_and_names_the_line(api):
    s = _seed(api)
    bad = {"line_id": str(s.l200.id), "splits": [
        _sp(14, D_301, on=str(s.l200.id)), _sp(236, D_192, on=str(s.l300.id)),
        _sp(150, D_NEW, on=str(s.l400.id))]}  # 400, base is 417
    _refused(_put(s, [bad]), 200)


def test_put_422_when_a_qty_is_not_positive(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(301, D_301, on=str(s.l100.id)), _sp(0, D_NEW, on=str(s.l100.id))]}
    _refused(_put(s, [bad]), 100)
    neg = {"line_id": str(s.l100.id), "splits": [
        _sp(311, D_301, on=str(s.l100.id)), _sp(-10, D_NEW, on=str(s.l100.id))]}
    _refused(_put(s, [neg]), 100)


def test_put_422_when_a_non_cancel_split_has_no_date(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(201, D_301, on=str(s.l100.id)),
        {"qty": 100, "delivery_date": None, "is_cancel": False,
         "sits_on_line_id": str(s.l100.id), "remark": ""}]}
    _refused(_put(s, [bad]), 100)


def test_put_422_when_sits_on_is_a_line_of_another_item(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(201, D_301, on=str(s.l100.id)), _sp(100, D_NEW, on=str(s.l500.id))]}
    _refused(_put(s, [bad]), 100)


def test_put_422_when_sits_on_is_a_line_at_another_location(api):
    s = _seed(api)
    core, other_loc = _mirror(s.db, s.order, s.core_so, s.world.product, s.world.pool_wh,
                              line_no=600, qty=10, when=D_301)
    s.db.commit()
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(201, D_301, on=str(s.l100.id)), _sp(100, D_NEW, on=str(other_loc.id))]}
    _refused(_put(s, [bad]), 100)


def test_put_422_when_sits_on_is_a_line_of_another_order(api):
    s = _seed(api)
    other_so = _core_so(s.db, s.world.company_id)
    other_order = _project_so(s.db, s.world.project, so_id=other_so.id,
                              autocount_doc_no=other_so.so_number)
    _c, foreign = _mirror(s.db, other_order, other_so, s.world.product, s.world.own_wh,
                          line_no=100, qty=40, when=D_301)
    s.db.commit()
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(201, D_301, on=str(s.l100.id)), _sp(100, D_NEW, on=str(foreign.id))]}
    _refused(_put(s, [bad]), 100)


def test_put_422_when_the_line_is_not_on_this_order(api):
    s = _seed(api)
    other_so = _core_so(s.db, s.world.company_id)
    other_order = _project_so(s.db, s.world.project, so_id=other_so.id,
                              autocount_doc_no=other_so.so_number)
    _c, foreign = _mirror(s.db, other_order, other_so, s.world.product, s.world.own_wh,
                          line_no=777, qty=40, when=D_301)
    s.db.commit()
    bad = {"line_id": str(foreign.id), "splits": [
        _sp(30, D_301, on=str(foreign.id)), _sp(10, D_NEW, on=str(foreign.id))]}
    assert _put(s, [bad]).status_code == 422


def test_a_refusal_saves_nothing_even_for_the_valid_lines_in_the_same_call(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(300, D_301, on=str(s.l100.id)), _sp(10, D_NEW, on=str(s.l100.id))]}  # 310, base 301
    _refused(_put(s, [_valid_200(s), bad]), 100)
    out = _get(s)
    assert _line(out, s.l200.id)["splits"] == []
    assert _line(out, s.l100.id)["splits"] == []


# ---------------------------------------------------------------------------
# Unsplit
# ---------------------------------------------------------------------------


def test_one_split_equal_to_the_base_on_its_own_line_and_date_is_unsplit(api):
    s = _seed(api)
    assert _put(s, [_valid_200(s)]).status_code == 200
    back = {"line_id": str(s.l200.id), "splits": [_sp(417, D_417, on=str(s.l200.id))]}
    r = _put(s, [back])
    assert r.status_code == 200, r.text
    assert _line(_get(s), s.l200.id)["splits"] == []
    from sqlalchemy import text
    n = s.db.execute(text("select count(*) from projects.so_line_splits "
                          "where so_line_id = :i"), {"i": str(s.l200.id)}).scalar()
    assert n == 0, "the rows are deleted, not hidden"


def test_an_empty_list_unsplits_the_line(api):
    s = _seed(api)
    assert _put(s, [_valid_200(s)]).status_code == 200
    assert _put(s, [{"line_id": str(s.l200.id), "splits": []}]).status_code == 200
    assert _line(_get(s), s.l200.id)["splits"] == []


def test_one_split_with_a_remark_stays_split(api):
    s = _seed(api)
    kept = {"line_id": str(s.l100.id),
            "splits": [_sp(301, D_301, on=str(s.l100.id), remark="hold for site")]}
    assert _put(s, [kept]).status_code == 200
    assert len(_line(_get(s), s.l100.id)["splits"]) == 1


# ---------------------------------------------------------------------------
# Sits on a cancelled line, RBAC
# ---------------------------------------------------------------------------


def test_a_split_sitting_on_a_cancelled_line_reads_back_as_a_new_line(api):
    s = _seed(api)
    assert _put(s, [_valid_200(s)]).status_code == 200
    s.c300.line_status = "cancelled"
    s.db.commit()
    l200 = _line(_get(s), s.l200.id)
    on = {x["delivery_date"]: x["sits_on_line_id"] for x in l200["splits"]}
    assert on[D_192.isoformat()] is None
    assert on[D_301.isoformat()] == str(s.l200.id)
    assert on[D_NEW.isoformat()] == str(s.l400.id)


def test_put_without_the_edit_permission_is_403_and_saves_nothing(api, monkeypatch):
    from app.services.user_service import UserPermissionService

    s = _seed(api)
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission",
                        lambda self, uid, slug: slug != EDIT)
    r = _put(s, [_valid_200(s)])
    assert r.status_code == 403, r.text
    assert s.client.get(_url(s.order.id)).status_code == 200  # view still allowed
    assert _line(_get(s), s.l200.id)["splits"] == []


# ---------------------------------------------------------------------------
# Review round 1
# ---------------------------------------------------------------------------


def test_put_naming_the_same_line_twice_is_422_and_saves_nothing(api):
    s = _seed(api)
    again = {"line_id": str(s.l100.id), "splits": [
        _sp(200, D_301, on=str(s.l100.id)), _sp(101, D_NEW, on=str(s.l100.id))]}
    r = _put(s, [again, dict(again)])
    assert r.status_code == 422, r.text
    assert _line(_get(s), s.l100.id)["splits"] == []


def test_a_remark_over_500_characters_is_422_not_500(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(200, D_301, on=str(s.l100.id), remark="x" * 501),
        _sp(101, D_NEW, on=str(s.l100.id))]}
    assert _put(s, [bad]).status_code == 422
    ok = {"line_id": str(s.l100.id), "splits": [
        _sp(200, D_301, on=str(s.l100.id), remark="x" * 500),
        _sp(101, D_NEW, on=str(s.l100.id))]}
    assert _put(s, [ok]).status_code == 200


def test_more_than_50_splits_on_one_line_is_422(api):
    s = _seed(api)
    fifty = [_sp(6, D_301, on=str(s.l100.id)) for _ in range(49)] + [_sp(7, D_NEW, on=str(s.l100.id))]
    assert sum(x["qty"] for x in fifty) == 301
    assert _put(s, [{"line_id": str(s.l100.id), "splits": fifty}]).status_code == 200
    fifty_one = [_sp(6, D_301, on=str(s.l100.id)) for _ in range(50)] + [_sp(1, D_NEW, on=str(s.l100.id))]
    assert sum(x["qty"] for x in fifty_one) == 301
    assert _put(s, [{"line_id": str(s.l100.id), "splits": fifty_one}]).status_code == 422


def test_a_qty_with_more_than_four_decimals_is_422_not_500(api):
    s = _seed(api)
    bad = {"line_id": str(s.l100.id), "splits": [
        _sp(150.12345, D_301, on=str(s.l100.id)), _sp(150.87655, D_NEW, on=str(s.l100.id))]}
    assert _put(s, [bad]).status_code == 422


def test_a_put_leaves_an_audit_row_naming_the_actor(api):
    from app.models.audit import AuditLog
    from app.services.audit_service import register_audit_listeners

    register_audit_listeners()
    s = _seed(api)
    assert _put(s, [_valid_200(s)]).status_code == 200
    s.db.commit()
    rows = (
        s.db.query(AuditLog)
        .filter(AuditLog.entity_type == "project_so_line_splits")
        .filter(AuditLog.user_id == s.world.actor)
        .all()
    )
    assert rows, "no audit_logs row for the split table names the actor"


def test_get_now_qty_is_the_open_qty_of_a_partly_delivered_line(api):
    s = _seed(api)
    s.c200.qty_delivered = Decimal("4")
    s.db.commit()
    l200 = _line(_get(s), s.l200.id)
    assert Decimal(str(l200["delivered_qty"])) == Decimal("4")
    assert Decimal(str(l200["now_qty"])) == Decimal("10"), "14 ordered, 4 delivered: 10 open"


def test_a_user_who_may_not_act_on_the_order_gets_403_and_nothing_is_saved(api):
    from tests.test_planning_changes import _user

    s = _seed(api)
    other = _user(s.db, "zzt other salesperson")
    s.world.project.owner_user_id = other
    s.db.commit()
    r = _put(s, [_valid_200(s)])
    assert r.status_code == 403, r.text
    assert _line(_get(s), s.l200.id)["splits"] == []
