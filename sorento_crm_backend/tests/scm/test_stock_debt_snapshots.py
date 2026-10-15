"""Stock Debt snapshots over the wire (STOCK-DEBT-SNAPSHOT, AC-1 .. AC-18, AC-19 is unchanged
backend and has no test here).

Contract: `documentation/plans/scm/stock-debt-snapshot-acceptance-criteria.md`.

WRITTEN BEFORE THE IMPLEMENTATION EXISTS (Phase 2 is test-first). Every not-yet-existing
module (`app.models.stock_debt_snapshot`, the record action) is imported INSIDE the test that
needs it, so each test goes red on its own missing behaviour, and a route test goes red on the
status assertion (404 today), never on a collection error.

NAMED ASSUMPTIONS about shapes the UAC leaves open (the coder may rename, the captain decides):

1. `GET /snapshots` and `GET /snapshots/{id}/rows` answer the repo envelope
   `{data: [...], pagination: {total, page, limit}}` (like `GET /stock-debt`).
2. `GET /snapshots/{id}/rows/{row_id}/lines` answers `{data: [...]}` (a bare list is also
   tolerated).
3. `GET /snapshots/{id}` carries `months`, `tba_month` and `totals` shaped like the live list's.
4. The AC-6 labels are asserted shape-agnostically: the serialised header must still print the
   OLD location code and supplier name after both were renamed.
5. Time-ordering tests stamp `created_at` explicitly: inside one rolled-back transaction every
   `now()` is identical, which would make "newest first" untestable.

Postgres only (`requires_pg`), marker-prefixed seeding, every chain seeded here.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.project_so import INQUIRY_RAISED, IV_ORDER, IV_ORDER_BACK
from app.services.scm.supply_assignment import month_key
from tests.scm.conftest import (
    SORENTO_COMPANY_ID,
    as_user,
    ensure_reference_data,
    requires_pg,
    seed_user,
)
from tests.scm.test_order_sheet_export_downloads import _NoCloseSession
from tests.scm.test_stock_debt_routes import (
    BASE,
    VIEW,
    _client,
    _demand,
    _months_ahead,
    _product,
    _project_line_for,
    _stock,
    _supplier,
    _u,
    _warehouse,
)
from tests.test_order_inquiry_reserve import _pso
from tests.test_order_inquiry_worklist import _inquiry_for, _row

pytestmark = requires_pg

SNAP = f"{BASE}/snapshots"

#: Keys the snapshot adds on top of a live `StockDebtRow`.
ROW_EXTRAS = {
    "row_id", "decision", "decision_warehouse_id", "decision_warehouse_code",
    "decision_note", "decided_by_name", "decided_at",
}
#: Keys the snapshot adds on top of a live drill demand line.
LINE_EXTRAS = {"bucket", "sales_order_line_id", "oi_rows", "now"}
FROZEN_OI = {
    "oi_id", "inquiry_no", "row_id", "qty", "delivery_date", "stock_location", "verb",
    "state",
}
HEADER_KEYS = {
    "id", "name", "filters", "filters_label", "months", "tba_month", "totals", "row_count",
    "decided_count", "created_by_name", "created_at", "can_delete", "prev_id", "next_id",
}


# --------------------------------------------------------------------------- helpers


def _marker() -> str:
    return f"ZZTSN{_u()[:6]}".upper()


def _uid_of(app, scm_app) -> str:
    _app, _db, gcu, _gcuak = scm_app
    return str(app.dependency_overrides[gcu]()["id"])


def _pool_world(db, marker: str):
    """A site pool, a BB bin that points at it (so the pool is a real `pool_warehouse_id`
    target), and one product with 100 on hand in the bin."""
    pool = _warehouse(db, f"ZZTP{_u()[:4]}".upper(), planning=False)
    bin_ = _warehouse(db, f"{pool.warehouse_code}-BB")
    bin_.pool_warehouse_id = pool.id
    db.flush()
    return pool, bin_


def _book(db, marker: str, *, stock=100):
    """Product with a covered line next month (30), a short line in three months (200) and a
    TBA line (60, booked at 2030). Returns (product, bin, pool, {name: (order, line)})."""
    pool, bin_ = _pool_world(db, marker)
    product = _product(db, f"{marker}-A")
    _stock(db, product, bin_, stock)
    lines = {}
    for name, qty, due in (
        ("near", 30, _months_ahead(1)),
        ("far", 200, _months_ahead(3)),
        ("tba", 60, date(2030, 1, 1)),
    ):
        lines[name] = _demand(
            db, product, bin_, qty=qty, required_date=due, so_number=f"{marker}-SO-{name}",
        )
    db.flush()
    return product, bin_, pool, lines


def _filters(marker: str, **extra) -> dict:
    return {"query": marker, "only_debt": False, **extra}


def _take(c, marker: str, name: str | None = None, **extra):
    return c.post(
        SNAP, json={"name": name or f"{marker} snap", "filters": _filters(marker, **extra)},
    )


def _taken(c, marker: str, name: str | None = None, **extra) -> dict:
    resp = _take(c, marker, name, **extra)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rows(c, sid: str, **params) -> dict:
    resp = c.get(f"{SNAP}/{sid}/rows", params={"limit": 200, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row_id(c, sid: str, code: str) -> str:
    hits = [r for r in _rows(c, sid)["data"] if r["product_code"] == code]
    assert hits, f"{code} is not in snapshot {sid}"
    return hits[0]["row_id"]


def _lines(c, sid: str, row_id: str, month: str | None = None) -> list[dict]:
    params = {"month": month} if month else {}
    resp = c.get(f"{SNAP}/{sid}/rows/{row_id}/lines", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["data"] if isinstance(body, dict) else body


def _strip(row: dict, extras: set) -> dict:
    return {k: v for k, v in row.items() if k not in extras}


def _frozen_line(line: dict) -> dict:
    """A stored line as it was frozen: no live `now`, OI rows narrowed to their frozen keys
    and the rows that only appeared after the snapshot dropped."""
    out = {k: v for k, v in line.items() if k != "now"}
    out["oi_rows"] = sorted(
        (
            {k: v for k, v in oi.items() if k in FROZEN_OI}
            for oi in line.get("oi_rows", [])
            if not oi.get("after_snapshot")
        ),
        key=lambda oi: oi["row_id"],
    )
    return out


def _stamp(db, snapshot_id: str, minutes_ago: int) -> None:
    db.execute(
        text("UPDATE stock_debt_snapshots SET created_at = :t WHERE id = :i"),
        {"t": datetime.utcnow() - timedelta(minutes=minutes_ago), "i": snapshot_id},
    )
    db.flush()


def _oi_on(db, line, *, qty="20", verb=IV_ORDER, state=INQUIRY_RAISED, inquiry=None,
           stock_location=None, so_line=None):
    """An order inquiry row linked to `line` through its Project SO mirror line."""
    if so_line is None:
        _order, so_line = _project_line_for(db, line)
    if inquiry is None:
        inquiry = _inquiry_for(db, SORENTO_COMPANY_ID, _pso(db, SORENTO_COMPANY_ID))
    row = _row(
        db, SORENTO_COMPANY_ID, inquiry, qty=qty, verb=verb, state=state,
        so_line_id=so_line.id, item_code=f"ZZTOI-{_u()[:6]}",
        stock_location=stock_location, delivery_date=date(2030, 3, 1),
    )
    db.refresh(inquiry)
    return inquiry, row, so_line


def _reserve_request(db, inquiry, row, *, user_id, parked=False, ordinal=1, qty="5"):
    from app.models.project_so import (
        OrderInquiryReserveRequest,
        OrderInquiryReserveRequestRow,
    )

    request = OrderInquiryReserveRequest(
        id=_u(), company_id=SORENTO_COMPANY_ID, order_inquiry_id=inquiry.id,
        ordinal=ordinal, state="requested", requested_by=user_id,
        parked_at=datetime.utcnow() if parked else None,
    )
    db.add(request)
    db.flush()
    db.add(
        OrderInquiryReserveRequestRow(
            id=_u(), company_id=SORENTO_COMPANY_ID, request_id=request.id, row_id=row.id,
            qty_requested=Decimal(qty),
        )
    )
    db.flush()
    return request


def _switch_user(scm_app):
    """Another staff user holding the view permission; returns (app, db, user_id).

    Not a second `_client()` call: that inserts a role with the fixed name
    'ZZT stock debt', and `user_roles.name` is unique, so the second insert fails.
    Same grant, its own role name."""
    app, db, gcu, gcuak = scm_app
    uid = seed_user(db, None)
    role_id = _u()
    db.execute(
        text(
            "INSERT INTO user_roles (id, slug, name, is_trashed, is_protected, "
            "is_default, created_at) VALUES (:id, :slug, :name, false, false, false, now())"
        ),
        {"id": role_id, "slug": f"zzt-sds-{role_id[:8]}", "name": f"ZZT sds {role_id[:8]}"},
    )
    permission_id = db.execute(
        text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": VIEW}
    ).scalar()
    db.execute(
        text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "VALUES (:id, :r, :p, now())"
        ),
        {"id": _u(), "r": role_id, "p": permission_id},
    )
    from app.models.user import UserRoleAssignment

    db.add(UserRoleAssignment(user_id=uid, role_id=role_id))
    db.flush()
    as_user(app, gcu, gcuak, uid)
    return app, db, uid


# --------------------------------------------------------------------------- AC-1 / AC-2


def test_save_returns_the_header_with_every_field(scm_app):
    """AC-1 + AC-8 shape: 201, the header by name, the creator's name, can_delete true."""
    app, db = _client(scm_app)
    marker = _marker()
    product, *_ = _book(db, marker)

    with TestClient(app) as c:
        resp = _take(c, marker, name=f"  {marker} first  ")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert HEADER_KEYS <= set(body), HEADER_KEYS - set(body)
    assert body["name"] == f"{marker} first", "the name is trimmed"
    assert body["row_count"] == 1
    assert body["decided_count"] == 0
    assert body["can_delete"] is True
    assert body["created_by_name"] == "SCM Test"
    assert body["filters"]["query"] == marker
    assert body["filters"]["only_debt"] is False
    assert "split" not in body["filters"], "split is an export choice, not a filter"


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 121, None],
    ids=["empty", "blank", "121-chars", "missing"],
)
def test_name_is_required_trimmed_and_capped_at_120(scm_app, name):
    """AC-2: blank / 121 characters / absent is 422; nothing stored."""
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)
    payload = {"filters": _filters(marker)}
    if name is not None:
        payload["name"] = name

    with TestClient(app) as c:
        resp = c.post(SNAP, json=payload)

    assert resp.status_code == 422, resp.text


def test_a_120_character_name_is_accepted(scm_app):
    """AC-2 boundary."""
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        resp = _take(c, marker, name="n" * 120)

    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "n" * 120


# --------------------------------------------------------------------------- AC-3 / AC-4 / AC-5


def test_stored_rows_equal_the_live_list(scm_app):
    """AC-3: every StockDebtRow field, the order, the months axis, tba_month and totals match
    the live list for the same filters."""
    app, db = _client(scm_app)
    marker = _marker()
    pool, bin_ = _pool_world(db, marker)
    for stem in ("A", "B", "C"):
        product = _product(db, f"{marker}-{stem}")
        _stock(db, product, bin_, 10)
        _demand(
            db, product, bin_, qty=40, required_date=_months_ahead(2),
            so_number=f"{marker}-SO-{stem}",
        )
        _demand(
            db, product, bin_, qty=7, required_date=date(2030, 1, 1),
            so_number=f"{marker}-SOT-{stem}",
        )
    db.flush()

    with TestClient(app) as c:
        header = _taken(c, marker)
        live = c.get(BASE, params={**_filters(marker), "limit": 200}).json()
        stored = _rows(c, header["id"])
        got_header = c.get(f"{SNAP}/{header['id']}").json()

    assert len(live["data"]) == 3
    assert [_strip(r, ROW_EXTRAS) for r in stored["data"]] == live["data"]
    assert got_header["months"] == live["months"]
    assert got_header["tba_month"] == live["tba_month"]
    assert got_header["totals"] == live["totals"]
    assert stored["pagination"]["total"] == 3


def test_stored_lines_equal_the_live_drill_per_bucket(scm_app):
    """AC-4: for every bucket of the row the stored lines equal `cell()` demand, field for
    field, each also carrying `bucket` and `sales_order_line_id` (the core line id)."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, _pool, lines = _book(db, marker)
    core_ids = {f"{marker}-SO-{k}": str(v[1].id) for k, v in lines.items()}
    buckets = {
        month_key(_months_ahead(1)): "near",
        month_key(_months_ahead(3)): "far",
        "tba": "tba",
    }

    with TestClient(app) as c:
        header = _taken(c, marker)
        row_id = _row_id(c, header["id"], product.product_code)
        stored = _lines(c, header["id"], row_id)
        for bucket, stem in buckets.items():
            live = c.get(f"{BASE}/{product.id}/cell", params={"month": bucket}).json()["demand"]
            mine = [line for line in stored if line["bucket"] == bucket]
            assert [_strip(line, LINE_EXTRAS) for line in mine] == live, bucket
            assert live, f"the seeded {stem} line must be in bucket {bucket}"
            for line in mine:
                assert line["sales_order_line_id"] == core_ids[line["so_number"]]
                assert "oi_rows" in line
        one_bucket = _lines(c, header["id"], row_id, month=month_key(_months_ahead(3)))

    assert {line["bucket"] for line in one_bucket} == {month_key(_months_ahead(3))}


def test_each_line_stores_its_order_inquiry_rows(scm_app):
    """AC-5: a line with two OI rows stores both with the frozen shape; a line with none
    stores []."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, _pool, lines = _book(db, marker)
    near = lines["near"][1]
    inquiry, row1, so_line = _oi_on(db, near, qty="20", stock_location="BRW")
    _i2, row2, _l2 = _oi_on(
        db, near, qty="10", verb=IV_ORDER_BACK, inquiry=inquiry, so_line=so_line,
    )

    with TestClient(app) as c:
        header = _taken(c, marker)
        row_id = _row_id(c, header["id"], product.product_code)
        stored = _lines(c, header["id"], row_id)

    by_so = {line["so_number"]: line for line in stored}
    two = by_so[f"{marker}-SO-near"]["oi_rows"]
    assert {oi["row_id"] for oi in two} == {str(row1.id), str(row2.id)}
    assert all(FROZEN_OI <= set(oi) for oi in two), two
    first = next(oi for oi in two if oi["row_id"] == str(row1.id))
    assert first["oi_id"] == str(inquiry.id)
    assert first["inquiry_no"] == inquiry.inquiry_no
    assert Decimal(str(first["qty"])) == Decimal("20")
    assert first["stock_location"] == "BRW"
    assert first["verb"] == IV_ORDER
    assert first["state"] == INQUIRY_RAISED
    assert by_so[f"{marker}-SO-far"]["oi_rows"] == []
    assert by_so[f"{marker}-SO-tba"]["oi_rows"] == []


# --------------------------------------------------------------------------- AC-6


def test_labels_survive_a_rename(scm_app):
    """AC-6: the location code and the supplier name the snapshot was taken under are still
    what it says after both are renamed."""
    from app.models.procurement import ProductSupplier

    app, db = _client(scm_app)
    marker = _marker()
    product, bin_, _pool, _lines_ = _book(db, marker)
    supplier = _supplier(db, f"ZZTSU{_u()[:5]}".upper())
    supplier.supplier_name = f"{marker} Old Supplier"
    db.add(
        ProductSupplier(
            id=_u(), product_id=product.id, supplier_id=supplier.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    db.flush()
    old_code = bin_.warehouse_code

    with TestClient(app) as c:
        header = _taken(
            c, marker, location=[str(bin_.id)], exclude_location=[str(bin_.id)],
            supplier_ids=[str(supplier.id)],
        )
        bin_.warehouse_code = f"ZZTNEW{_u()[:4]}".upper()
        supplier.supplier_name = f"{marker} New Supplier"
        db.flush()
        again = c.get(f"{SNAP}/{header['id']}").json()

    printed = json.dumps(again)
    assert old_code in printed, printed
    assert f"{marker} Old Supplier" in printed, printed
    assert bin_.warehouse_code not in printed
    assert f"{marker} New Supplier" not in printed


# --------------------------------------------------------------------------- AC-7


def test_more_rows_than_the_cap_is_refused_and_nothing_is_stored(scm_app, monkeypatch):
    """AC-7: past MAX_LOW_STOCK_ROWS the save is 422 'Narrow the filters first' and no header
    row is written."""
    import sys

    app, db = _client(scm_app)
    marker = _marker()
    pool, bin_ = _pool_world(db, marker)
    for stem in ("A", "B"):
        product = _product(db, f"{marker}-{stem}")
        _demand(
            db, product, bin_, qty=5, required_date=_months_ahead(1),
            so_number=f"{marker}-SO-{stem}",
        )
    db.flush()
    # Patched wherever the cap is read from at call time, including a module the coder adds
    # (the app is fully imported by now, so every `app.*` module is in sys.modules).
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "MAX_LOW_STOCK_ROWS"):
            monkeypatch.setattr(module, "MAX_LOW_STOCK_ROWS", 1)

    with TestClient(app) as c:
        resp = _take(c, marker, name=f"{marker} too big")

    assert resp.status_code == 422, resp.text
    assert "Narrow the filters first" in resp.text, resp.text
    stored = db.execute(
        text("SELECT count(*) FROM stock_debt_snapshots WHERE name = :n"),
        {"n": f"{marker} too big"},
    ).scalar()
    assert stored == 0


# --------------------------------------------------------------------------- AC-8 / AC-9


def test_prev_and_next_walk_by_created_at_and_can_delete_is_the_creators(scm_app):
    """AC-8: prev/next walk the company's snapshots in time order; can_delete is true for the
    creator only."""
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        ids = []
        for minutes_ago, stem in ((30, "a"), (20, "b"), (10, "c")):
            header = _taken(c, marker, name=f"{marker} {stem}")
            _stamp(db, header["id"], minutes_ago)
            ids.append(header["id"])
        a, b, cc = (c.get(f"{SNAP}/{i}").json() for i in ids)

        other_app, _db, _other = _switch_user(scm_app)
        seen_by_other = c.get(f"{SNAP}/{ids[1]}").json()

    assert {b["prev_id"], b["next_id"]} == {ids[0], ids[2]}
    assert {a["prev_id"], a["next_id"]} == {None, ids[1]}
    assert {cc["prev_id"], cc["next_id"]} == {None, ids[1]}
    # One direction only: whichever side holds the neighbour of B holds B on the way back.
    if b["prev_id"] == ids[0]:
        assert a["next_id"] == ids[1] and cc["prev_id"] == ids[1]
    else:
        assert a["prev_id"] == ids[1] and cc["next_id"] == ids[1]
    assert (a["can_delete"], b["can_delete"], cc["can_delete"]) == (True, True, True)
    assert seen_by_other["can_delete"] is False


def test_list_is_newest_first_and_searchable_by_name(scm_app):
    """AC-9: newest first, `query` matches the name case-insensitively, paging total."""
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        made = {}
        for minutes_ago, stem in ((30, "old"), (20, "mid"), (10, "new")):
            header = _taken(c, marker, name=f"{marker} {stem}")
            _stamp(db, header["id"], minutes_ago)
            made[stem] = header["id"]
        everything = c.get(SNAP, params={"query": marker}).json()
        one = c.get(SNAP, params={"query": f"{marker.lower()} MID"}).json()
        paged = c.get(SNAP, params={"query": marker, "page": 2, "limit": 2}).json()

    assert [h["id"] for h in everything["data"]] == [made["new"], made["mid"], made["old"]]
    assert everything["pagination"]["total"] == 3
    assert [h["id"] for h in one["data"]] == [made["mid"]]
    assert {"id", "name", "filters", "filters_label", "row_count", "decided_count",
            "created_by_name", "created_at", "can_delete"} <= set(everything["data"][0])
    assert [h["id"] for h in paged["data"]] == [made["old"]]
    assert paged["pagination"]["total"] == 3


# --------------------------------------------------------------------------- AC-10


def _four_products(db, marker):
    pool, bin_ = _pool_world(db, marker)
    products = []
    for stem in ("A", "B", "C", "D"):
        product = _product(db, f"{marker}-{stem}")
        product.product_name = f"Basin {stem.lower()}{marker}"
        _stock(db, product, bin_, 1)
        _demand(
            db, product, bin_, qty=9, required_date=_months_ahead(2),
            so_number=f"{marker}-SO-{stem}",
        )
        products.append(product)
    db.flush()
    return pool, bin_, products


def _decide(c, sid, row_id, **body):
    payload = {"decision": None, "warehouse_id": None, "note": None, **body}
    return c.put(f"{SNAP}/{sid}/rows/{row_id}/decision", json=payload)


def test_rows_query_decision_filter_paging_and_stored_order(scm_app):
    """AC-10: the stored order, `query` on code or name, every decision filter, and a
    pagination total that is the FILTERED count."""
    app, db = _client(scm_app)
    marker = _marker()
    pool, _bin, (pa, pb, pc, pd) = _four_products(db, marker)

    with TestClient(app) as c:
        live = c.get(BASE, params={**_filters(marker), "limit": 200}).json()["data"]
        header = _taken(c, marker)
        sid = header["id"]
        ids = {p.product_code: _row_id(c, sid, p.product_code) for p in (pa, pb, pc, pd)}
        assert _decide(
            c, sid, ids[pa.product_code], decision="reserve", warehouse_id=str(pool.id),
        ).status_code == 200
        assert _decide(c, sid, ids[pb.product_code], decision="buy").status_code == 200
        assert _decide(c, sid, ids[pd.product_code], decision="ignore").status_code == 200

        every = _rows(c, sid)
        by_code = lambda **p: [r["product_code"] for r in _rows(c, sid, **p)["data"]]  # noqa: E731
        decided = by_code(decision="decided")
        undecided = by_code(decision="undecided")
        reserve = by_code(decision="reserve")
        buy = by_code(decision="buy")
        ignore = by_code(decision="ignore")
        by_code_q = by_code(query=pc.product_code.lower())
        by_name_q = by_code(query=f"BASIN B{marker}")
        page2 = c.get(f"{SNAP}/{sid}/rows", params={"decision": "decided", "limit": 2, "page": 2})

    assert [r["product_code"] for r in every["data"]] == [r["product_code"] for r in live]
    assert set(decided) == {pa.product_code, pb.product_code, pd.product_code}
    assert undecided == [pc.product_code]
    assert reserve == [pa.product_code]
    assert buy == [pb.product_code]
    assert ignore == [pd.product_code]
    assert by_code_q == [pc.product_code]
    assert by_name_q == [pb.product_code]
    assert page2.status_code == 200, page2.text
    assert page2.json()["pagination"]["total"] == 3
    assert len(page2.json()["data"]) == 1
    reserved_row = next(r for r in every["data"] if r["product_code"] == pa.product_code)
    assert set(ROW_EXTRAS) <= set(reserved_row)


# --------------------------------------------------------------------------- AC-11


def test_a_snapshot_is_never_recomputed(scm_app):
    """AC-11: cancel an SO, change a quantity, add a new SO line after the snapshot; the
    header, the rows and the lines are unchanged field for field."""
    app, db = _client(scm_app)
    marker = _marker()
    product, bin_, _pool, lines = _book(db, marker)

    with TestClient(app) as c:
        header = _taken(c, marker)
        sid = header["id"]
        row_id = _row_id(c, sid, product.product_code)
        before = (
            c.get(f"{SNAP}/{sid}").json(),
            _rows(c, sid)["data"],
            [_frozen_line(line) for line in _lines(c, sid, row_id)],
        )

        lines["near"][0].status = "cancelled"
        lines["far"][1].qty_ordered = Decimal("999")
        _demand(
            db, product, bin_, qty=77, required_date=_months_ahead(1),
            so_number=f"{marker}-SO-late",
        )
        db.flush()
        live_now = c.get(BASE, params=_filters(marker)).json()["data"][0]

        after = (
            c.get(f"{SNAP}/{sid}").json(),
            _rows(c, sid)["data"],
            [_frozen_line(line) for line in _lines(c, sid, row_id)],
        )

    assert live_now["total"] != before[1][0]["total"], "the live board must have moved"
    assert after == before


# --------------------------------------------------------------------------- AC-12


def _lines_of(c, sid, row_id, so_number, month):
    return [l for l in _lines(c, sid, row_id, month=month) if l["so_number"] == so_number]


def test_now_reads_the_cancelled_order_and_zero_open_qty(scm_app):
    """AC-12: `now.so_status` is the order's current status and `open_qty` is 0 when the SO is
    not open; the frozen figures stay."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, _pool, lines = _book(db, marker)
    month = month_key(_months_ahead(1))
    so = f"{marker}-SO-near"

    with TestClient(app) as c:
        header = _taken(c, marker)
        sid = header["id"]
        row_id = _row_id(c, sid, product.product_code)
        [open_line] = _lines_of(c, sid, row_id, so, month)
        lines["near"][0].status = "cancelled"
        db.flush()
        [cancelled_line] = _lines_of(c, sid, row_id, so, month)

    assert open_line["now"]["so_status"] == "open"
    assert Decimal(str(open_line["now"]["open_qty"])) == Decimal("30")
    assert cancelled_line["now"]["so_status"] == "cancelled"
    assert Decimal(str(cancelled_line["now"]["open_qty"])) == Decimal("0")
    assert cancelled_line["open_qty"] == open_line["open_qty"], "frozen open_qty unchanged"


def test_requestable_turns_false_once_a_reserve_request_is_open(scm_app):
    """AC-12: an ORDER row in a raised inquiry with quantity left is requestable; after a
    reserve request is created on it the live read says not requestable, `requested`."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, _pool, lines = _book(db, marker)
    inquiry, row, _so_line = _oi_on(db, lines["near"][1], qty="20")
    month = month_key(_months_ahead(1))
    so = f"{marker}-SO-near"

    with TestClient(app) as c:
        app_user = _uid_of(app, scm_app)
        header = _taken(c, marker)
        sid = header["id"]
        row_id = _row_id(c, sid, product.product_code)
        [line] = _lines_of(c, sid, row_id, so, month)
        [oi_before] = line["oi_rows"]
        _reserve_request(db, inquiry, row, user_id=app_user)
        [line_after] = _lines_of(c, sid, row_id, so, month)
        [oi_after] = line_after["oi_rows"]

    assert oi_before["requestable"] is True
    assert oi_before["reserve_state"] is None
    assert oi_before["now_state"] == INQUIRY_RAISED
    assert Decimal(str(oi_before["remaining"])) == Decimal("20")
    assert oi_after["requestable"] is False
    assert oi_after["reserve_state"] == "requested"


def test_a_parked_request_reads_parked_and_a_later_row_is_after_snapshot(scm_app):
    """AC-12: a parked request is `parked`; an OI row linked to the line after the snapshot
    is appended with `after_snapshot: true` and the frozen rows do not carry the flag."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, _pool, lines = _book(db, marker)
    inquiry, row, so_line = _oi_on(db, lines["near"][1], qty="20")
    month = month_key(_months_ahead(1))
    so = f"{marker}-SO-near"

    with TestClient(app) as c:
        user = _uid_of(app, scm_app)
        header = _taken(c, marker)
        sid = header["id"]
        row_id = _row_id(c, sid, product.product_code)
        _reserve_request(db, inquiry, row, user_id=user, parked=True)
        _inq2, late_row, _sl = _oi_on(db, lines["near"][1], qty="8", so_line=so_line)
        [line] = _lines_of(c, sid, row_id, so, month)

    by_row = {oi["row_id"]: oi for oi in line["oi_rows"]}
    assert by_row[str(row.id)]["reserve_state"] == "parked"
    assert not by_row[str(row.id)].get("after_snapshot")
    assert by_row[str(late_row.id)]["after_snapshot"] is True
    assert by_row[str(late_row.id)]["requestable"] is True


# --------------------------------------------------------------------------- AC-13 / AC-14


def test_decision_set_clear_and_who_when(scm_app):
    """AC-13: set reserve with a site pool and a note, the row comes back with the pool code,
    who and when; null clears all three and who/when; reserve with no warehouse is fine."""
    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, pool, _lines_ = _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row_id = _row_id(c, sid, product.product_code)
        put = _decide(c, sid, row_id, decision="reserve", warehouse_id=str(pool.id), note="ring Eling")
        header_after = c.get(f"{SNAP}/{sid}").json()
        bare = _decide(c, sid, row_id, decision="reserve")
        cleared = _decide(c, sid, row_id)
        header_cleared = c.get(f"{SNAP}/{sid}").json()

    assert put.status_code == 200, put.text
    body = put.json()
    assert body["row_id"] == row_id
    assert body["product_code"] == product.product_code
    assert body["decision"] == "reserve"
    assert body["decision_warehouse_id"] == str(pool.id)
    assert body["decision_warehouse_code"] == pool.warehouse_code
    assert body["decision_note"] == "ring Eling"
    assert body["decided_by_name"] == "SCM Test"
    assert body["decided_at"]
    assert header_after["decided_count"] == 1
    assert bare.status_code == 200, bare.text
    assert bare.json()["decision_warehouse_id"] is None
    assert cleared.status_code == 200, cleared.text
    gone = cleared.json()
    assert gone["decision"] is None
    assert gone["decision_warehouse_id"] is None
    assert gone["decision_note"] is None
    assert gone["decided_by_name"] is None
    assert gone["decided_at"] is None
    assert header_cleared["decided_count"] == 0


def test_decision_validation_is_422(scm_app):
    """AC-13: an unknown decision, a warehouse that is not a site pool, a warehouse with buy
    (or ignore), and a 2,001 character note are all 422; the 2,000 boundary passes."""
    app, db = _client(scm_app)
    marker = _marker()
    product, bin_, pool, _lines_ = _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row_id = _row_id(c, sid, product.product_code)
        bad_value = _decide(c, sid, row_id, decision="maybe")
        not_a_pool = _decide(c, sid, row_id, decision="reserve", warehouse_id=str(bin_.id))
        with_buy = _decide(c, sid, row_id, decision="buy", warehouse_id=str(pool.id))
        with_ignore = _decide(c, sid, row_id, decision="ignore", warehouse_id=str(pool.id))
        too_long = _decide(c, sid, row_id, decision="buy", note="n" * 2001)
        edge = _decide(c, sid, row_id, decision="buy", note="n" * 2000)
        stored = c.get(f"{SNAP}/{sid}").json()

    assert bad_value.status_code == 422, bad_value.text
    assert not_a_pool.status_code == 422, not_a_pool.text
    assert with_buy.status_code == 422, with_buy.text
    assert with_ignore.status_code == 422, with_ignore.text
    assert too_long.status_code == 422, too_long.text
    assert edge.status_code == 200, edge.text
    assert stored["decided_count"] == 1, "a refused decision must store nothing"


def test_any_holder_of_the_permission_may_decide_on_anyones_snapshot(scm_app):
    """AC-14: a second user with Stock Debt access decides on the first user's snapshot."""
    app, db = _client(scm_app)
    marker = _marker()
    product, *_ = _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row_id = _row_id(c, sid, product.product_code)
        _switch_user(scm_app)
        put = _decide(c, sid, row_id, decision="buy", note="mine now")

    assert put.status_code == 200, put.text
    assert put.json()["decision"] == "buy"


# --------------------------------------------------------------------------- AC-15


def _park_delete(c, sid):
    return c.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "stock_debt_snapshot.delete",
            "entity_type": "stock_debt_snapshot",
            "entity_id": sid,
            "payload": {},
        },
    )


def _lapse_and_poll(c, db, sid, action_id):
    from app.models.sla import SlaFormAction

    db.query(SlaFormAction).filter(SlaFormAction.id == action_id).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False,
    )
    db.commit()
    return c.get(
        "/api/v1/pending-actions/current",
        params={"entity_type": "stock_debt_snapshot", "entity_id": sid},
    )


def test_delete_is_a_destructive_creator_only_deferred_action(scm_app):
    """AC-15: `stock_debt_snapshot.delete` is registered, destructive window, creator only;
    the creator's delete hard-deletes the header and its rows, another user's does not."""
    from app.services import record_actions
    from app.services.form_action_grace import WINDOW_DESTRUCTIVE, window_class_for
    from app.services.form_action_registry import REGISTRY

    action = REGISTRY["stock_debt_snapshot.delete"]
    assert window_class_for(action) == WINDOW_DESTRUCTIVE
    assert action.permission == record_actions.OWN_RECORD

    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        mine = _taken(c, marker, name=f"{marker} mine")["id"]
        theirs = _taken(c, marker, name=f"{marker} theirs")["id"]

        parked = _park_delete(c, mine)
        assert parked.status_code == 202, parked.text
        assert parked.json()["window_seconds"] == 10
        outcome = _lapse_and_poll(c, db, mine, parked.json()["id"]).json()
        assert outcome["last_outcome"]["status"] == "committed", outcome["last_outcome"]
        gone = c.get(f"{SNAP}/{mine}")
        header_rows = db.execute(
            text("SELECT count(*) FROM stock_debt_snapshots WHERE id = :i"), {"i": mine},
        ).scalar()
        stray_rows = db.execute(
            text("SELECT count(*) FROM stock_debt_snapshot_rows WHERE snapshot_id = :i"),
            {"i": mine},
        ).scalar()

        _switch_user(scm_app)
        attempt = _park_delete(c, theirs)
        if attempt.status_code == 202:
            outcome = _lapse_and_poll(c, db, theirs, attempt.json()["id"]).json()
            assert outcome["last_outcome"]["status"] == "failed", outcome["last_outcome"]
        else:
            assert attempt.status_code == 404, attempt.text
        still = c.get(f"{SNAP}/{theirs}")

    assert gone.status_code == 404
    assert header_rows == 0
    assert stray_rows == 0
    assert still.status_code == 200, "another user's delete must leave the snapshot alone"


# --------------------------------------------------------------------------- AC-16


class _CapturingBackend:
    def __init__(self):
        self.blobs: list[bytes] = []

    def upload_file(self, *, file_content, file_path, content_type):
        self.blobs.append(file_content)
        return file_path, None


def _run_export_task(db, monkeypatch, user_id, snapshot_id, **kwargs):
    from app.services.download_service import DownloadService
    from app.tasks import export_tasks

    fn = getattr(export_tasks, "generate_stock_debt_snapshot_xlsx", None)
    assert fn is not None, (
        "app.tasks.export_tasks.generate_stock_debt_snapshot_xlsx does not exist yet (AC-16)"
    )
    download = DownloadService(db).create(
        user_id=str(user_id), kind="stock_debt_snapshot_xlsx", filename="zzt-snapshot.xlsx",
    )
    db.flush()
    backend = _CapturingBackend()
    monkeypatch.setattr(export_tasks, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr(export_tasks, "get_backend", lambda provider: backend)
    monkeypatch.setattr(export_tasks, "default_provider", lambda: "s3")
    fn(str(download.id), str(user_id), str(snapshot_id), company_id=SORENTO_COMPANY_ID, **kwargs)
    row = DownloadService(db).get(str(download.id))
    return row, backend


def test_export_task_renders_the_stored_figures_with_decision_columns(scm_app, monkeypatch):
    """AC-16: the workbook is the STORED rows (live data changed first), with Decision, From
    and Note appended."""
    from openpyxl import load_workbook

    app, db = _client(scm_app)
    marker = _marker()
    product, _bin, pool, lines = _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row = next(r for r in _rows(c, sid)["data"] if r["product_code"] == product.product_code)
        assert _decide(
            c, sid, row["row_id"], decision="reserve", warehouse_id=str(pool.id), note="for Eling",
        ).status_code == 200
        # The live book moves AFTER the snapshot: the workbook must not follow it.
        lines["far"][1].qty_ordered = Decimal("5000")
        db.flush()
        live_total = c.get(BASE, params=_filters(marker)).json()["data"][0]["total"]
        user_id = _uid_of(app, scm_app)

    assert live_total != row["total"], "the live board must have moved"
    done, backend = _run_export_task(db, monkeypatch, user_id, sid, split="none", show_aux=False)

    assert done.status == "ready", getattr(done, "error", None)
    assert len(backend.blobs) == 1
    ws = load_workbook(BytesIO(backend.blobs[0])).worksheets[0]
    header = [cell.value for cell in ws[1]]
    assert header[-3:] == ["Decision", "From", "Note"], header
    data = {
        h: v for h, v in zip(
            header, next(r for r in ws.iter_rows(min_row=2, values_only=True)
                         if r[0] == product.product_code),
        )
    }
    assert data["Total"] == row["total"], (data["Total"], row["total"], live_total)
    assert str(data["Decision"]).lower() == "reserve"
    assert data["From"] == pool.warehouse_code
    assert data["Note"] == "for Eling"


def test_export_route_queues_the_snapshot_download(scm_app, monkeypatch):
    """AC-16: 201, kind `stock_debt_snapshot_xlsx`, `stock-debt-snapshot-<ddmmyyyy>.xlsx`,
    owned by the caller, enqueued on `imports` with the download id first."""
    from app.services import queue_service

    calls: list[dict] = []
    monkeypatch.setattr(
        queue_service, "enqueue_job",
        lambda func, *args, **kwargs: calls.append({"func": func, "args": args, "kwargs": kwargs})
        or type("J", (), {"id": "fake-job"})(),
    )
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        resp = c.post(f"{SNAP}/{sid}/export", json={"split": "supplier", "show_aux": True})
        user_id = _uid_of(app, scm_app)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "stock_debt_snapshot_xlsx"
    row = db.execute(
        text("SELECT kind, filename, user_id FROM user_downloads WHERE id = :i"), {"i": body["id"]},
    ).mappings().first()
    assert row["filename"] == f"stock-debt-snapshot-{date.today().strftime('%d%m%Y')}.xlsx"
    assert row["user_id"] == user_id
    assert len(calls) == 1, calls
    from app.tasks.export_tasks import generate_stock_debt_snapshot_xlsx

    assert calls[0]["func"] is generate_stock_debt_snapshot_xlsx
    assert calls[0]["args"][0] == body["id"]
    assert calls[0]["kwargs"].get("queue_name") == "imports"


def test_export_is_refused_while_one_is_in_flight(scm_app, monkeypatch):
    """AC-16: one in flight per user per kind; the second POST is 409 and leaves no row."""
    from app.services import queue_service
    from app.services.download_service import DownloadService

    monkeypatch.setattr(
        queue_service, "enqueue_job", lambda *a, **k: type("J", (), {"id": "x"})(),
    )
    app, db = _client(scm_app)
    marker = _marker()
    _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        user_id = _uid_of(app, scm_app)
        DownloadService(db).create(
            user_id=user_id, kind="stock_debt_snapshot_xlsx", filename="zzt-busy.xlsx",
        )
        db.flush()
        resp = c.post(f"{SNAP}/{sid}/export", json={"split": "none"})

    assert resp.status_code == 409, resp.text
    count = db.execute(
        text("SELECT count(*) FROM user_downloads WHERE user_id = :u"), {"u": user_id},
    ).scalar()
    assert count == 1


# --------------------------------------------------------------------------- AC-17


def test_another_companys_snapshot_is_a_404_on_every_route(scm_app):
    """AC-17: the same caller, scoped to a different company, gets 404 on the header, rows,
    lines, decision and export, and an empty list."""
    app, db = _client(scm_app)
    marker = _marker()
    product, *_ = _book(db, marker)

    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row_id = _row_id(c, sid, product.product_code)

        other = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO companies (id, name, code, is_active, created_at) "
                "VALUES (:i, :n, :c, true, now())"
            ),
            {"i": other, "n": f"{marker} other co", "c": f"{marker}-OC-{other[:8]}"},
        )
        db.flush()
        from app.models.base import set_company_scope
        from app.services.company_scope_resolver import apply_company_scope

        scope = frozenset({other})
        set_company_scope(db, scope)

        async def _scope():
            set_company_scope(db, scope)
            return scope

        app.dependency_overrides[apply_company_scope] = _scope

        header = c.get(f"{SNAP}/{sid}")
        rows = c.get(f"{SNAP}/{sid}/rows")
        lines = c.get(f"{SNAP}/{sid}/rows/{row_id}/lines")
        decision = _decide(c, sid, row_id, decision="buy")
        export = c.post(f"{SNAP}/{sid}/export", json={"split": "none"})
        listing = c.get(SNAP, params={"query": marker})

    for name, resp in (
        ("header", header), ("rows", rows), ("lines", lines), ("decision", decision),
        ("export", export),
    ):
        assert resp.status_code == 404, (name, resp.status_code, resp.text)
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"] == []


# --------------------------------------------------------------------------- AC-18


def test_without_the_permission_every_route_is_403(scm_app):
    """AC-18: no `projects.stock_debt.view`, no snapshot route."""
    app, db = _client(scm_app, permission=None)
    some = str(uuid.uuid4())

    with TestClient(app) as c:
        got = {
            "create": c.post(SNAP, json={"name": "x", "filters": {}}),
            "list": c.get(SNAP),
            "header": c.get(f"{SNAP}/{some}"),
            "rows": c.get(f"{SNAP}/{some}/rows"),
            "lines": c.get(f"{SNAP}/{some}/rows/{some}/lines"),
            "decision": c.put(
                f"{SNAP}/{some}/rows/{some}/decision",
                json={"decision": "buy", "warehouse_id": None, "note": None},
            ),
            "export": c.post(f"{SNAP}/{some}/export", json={"split": "none"}),
        }

    for name, resp in got.items():
        assert resp.status_code == 403, (name, resp.status_code, resp.text)


def test_an_api_key_principal_cannot_write(scm_app, monkeypatch):
    """AC-18: POST, PUT decision and export all use the session-only `require_permission`; a
    key-only principal holding the view permission through its act-as user is refused."""
    from app.models.integration import Integration
    from app.models.user import User, UserRoleAssignment
    from app.services import queue_service
    from app.services.integration_key_service import IntegrationKeyService
    from tests.scm.conftest import _ensure_permission_row_committed

    monkeypatch.setattr(
        queue_service, "enqueue_job", lambda *a, **k: type("J", (), {"id": "x"})(),
    )
    app, db = _client(scm_app)
    marker = _marker()
    product, *_ = _book(db, marker)
    with TestClient(app) as c:
        sid = _taken(c, marker)["id"]
        row_id = _row_id(c, sid, product.product_code)

    user = User(
        id=_u(), email=f"{_u()}@integrations.local", name="ZZTSN integration",
        status="ACTIVE", is_integration=True,
    )
    db.add(user)
    db.flush()
    role_id = _u()
    db.execute(
        text(
            "INSERT INTO user_roles (id, slug, name, is_trashed, is_protected, is_default, "
            "created_at) VALUES (:id, :slug, 'ZZT integration snapshots', false, false, "
            "false, now())"
        ),
        {"id": role_id, "slug": f"zzt-sn-integration-{role_id[:8]}"},
    )
    _ensure_permission_row_committed(VIEW)
    permission_id = db.execute(
        text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": VIEW}
    ).scalar()
    db.execute(
        text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "VALUES (:id, :r, :p, now())"
        ),
        {"id": _u(), "r": role_id, "p": permission_id},
    )
    db.add(UserRoleAssignment(user_id=user.id, role_id=role_id))
    integration = Integration(
        name=f"ZZTSN-{_u()[:8]}", type="autocount_esb", act_as_user_id=user.id, is_active=True,
    )
    db.add(integration)
    db.flush()
    key = IntegrationKeyService(db).issue_key(integration)
    db.flush()

    # Drop the session-principal overrides so the real key resolution runs.
    _app, _db, gcu, gcuak = scm_app
    app.dependency_overrides.pop(gcu, None)
    app.dependency_overrides.pop(gcuak, None)
    headers = {"X-API-Key": key}
    downloads_before = db.execute(text("SELECT count(*) FROM user_downloads")).scalar()

    with TestClient(app) as c:
        posted = c.post(
            SNAP, headers=headers, json={"name": f"{marker} by key", "filters": _filters(marker)},
        )
        put = c.put(
            f"{SNAP}/{sid}/rows/{row_id}/decision", headers=headers,
            json={"decision": "buy", "warehouse_id": None, "note": None},
        )
        exported = c.post(f"{SNAP}/{sid}/export", headers=headers, json={"split": "none"})

    for name, resp in (("create", posted), ("decision", put), ("export", exported)):
        assert resp.status_code in (401, 403), (name, resp.status_code, resp.text)
    assert db.execute(text("SELECT count(*) FROM user_downloads")).scalar() == downloads_before
