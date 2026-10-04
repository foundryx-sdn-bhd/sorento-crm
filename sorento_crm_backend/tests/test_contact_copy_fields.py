"""Lane CONTACT-COPY-FIELDS: the contact copy also copies stock check mode, locations, companies
and market segments (UAC ``documentation/plans/contacts/contact-copy-fields-4oct-acceptance-criteria.md``).

Same endpoint, fixtures and replace semantics as ``test_contact_bulk_copy_access.py``. Each new field
is checked on a single and a bulk copy, plus the access rule: a facet the actor could not set directly
is never written by the copy (C5).
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import MarketSegment, StockVisibilityPolicy
from app.models.company import Company, RespondContactCompany
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import warehouse
from tests._pg_fixture import unique_code
from tests.test_contact_bulk_copy_access import (  # noqa: F401 - fixtures
    EDIT,
    SORENTO,
    VIEW,
    _contact,
    _post,
    client,
    db,
    perms,
)

STOCK_EDIT = "inventory.stock.edit"
NEW_FACETS = ["stock_check_mode", "stock_locations", "companies", "market_segments"]


@pytest.fixture
def roles(monkeypatch):
    """The actor's role slugs; superadmin by default (companies are a superadmin field)."""
    state = {"slugs": {UserPermissionService.SUPERADMIN_ROLE_SLUG}}
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set(state["slugs"]))
    return state


@pytest.fixture
def full(perms, roles):
    perms["granted"] = {VIEW, EDIT, STOCK_EDIT}
    return perms


# ---------------------------------------------------------------- seeding


def _policy(db, contact, mode, *, include=None, exclude=None, hide_zero=False):
    row = StockVisibilityPolicy(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        mode=mode,
        warehouse_ids=include,
        excluded_warehouse_ids=exclude,
        hide_zero_locations=hide_zero,
    )
    db.add(row)
    db.flush()
    return row


def _company(db, tag):
    code = unique_code(f"ZC{tag}")[:50]
    row = Company(id=str(uuid.uuid4()), name=f"ZZT company {code}", code=code)
    db.add(row)
    db.flush()
    return row


def _grant(db, contact, *companies):
    for c in companies:
        db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=c.id))
    db.flush()


def _segment(db, tag):
    code = unique_code(f"zms{tag}")[:50]
    row = MarketSegment(code=code, name=f"ZZT segment {code}", is_active=True, sort_order=1)
    db.add(row)
    db.flush()
    return row


def _stock(db, contact_id):
    db.expire_all()
    row = db.query(StockVisibilityPolicy).filter(StockVisibilityPolicy.contact_id == contact_id).first()
    if row is None:
        return None
    return (
        row.mode,
        sorted(row.warehouse_ids) if row.warehouse_ids is not None else None,
        sorted(row.excluded_warehouse_ids) if row.excluded_warehouse_ids is not None else None,
        row.hide_zero_locations,
    )


def _companies(db, contact_id):
    db.expire_all()
    return sorted(
        str(r.company_id)
        for r in db.query(RespondContactCompany).filter(RespondContactCompany.respond_contact_id == contact_id)
    )


def _segments(db, contact_id):
    from app.models.access import RespondContact

    db.expire_all()
    return sorted(s.code for s in db.get(RespondContact, contact_id).market_segments)


def _changes(row):
    return {c["facet"]: c for c in row["changes"]}


@pytest.fixture
def wh(db):
    return [warehouse(db, company_id=SORENTO, code=unique_code(f"ZZTW{i}")[:50]) for i in range(3)]


# ---------------------------------------------------------------- stock check mode + locations (C1)


def test_c1_stock_mode_and_locations_copied_single_and_bulk(client, db, full, wh):
    w1, w2, w3 = wh
    source, t1, t2 = _contact(db, "src"), _contact(db, "t1"), _contact(db, "t2")
    _policy(db, source, "compact", include=[w1.id, w2.id], hide_zero=True)
    _policy(db, t1, "detailed", exclude=[w3.id])

    # single
    resp = _post(client, source, [t1], dry_run=False)
    assert resp.status_code == 200, resp.text
    assert _stock(db, t1.id) == _stock(db, source.id)
    # bulk, one target already equal
    body = _post(client, source, [t1, t2], dry_run=False).json()
    assert [r["status"] for r in body["results"]] == ["unchanged", "changed"]
    assert _stock(db, t2.id) == _stock(db, source.id)
    assert _stock(db, source.id) == ("compact", sorted([w1.id, w2.id]), None, True)


def test_c1_source_that_inherits_removes_the_target_override(client, db, full, wh):
    source, t1, t2 = _contact(db, "src"), _contact(db, "t1"), _contact(db, "t2")
    _policy(db, t1, "availability", include=[])
    _policy(db, t2, "compact")
    assert _post(client, source, [t1, t2], dry_run=False).status_code == 200
    assert _stock(db, t1.id) is None and _stock(db, t2.id) is None


def test_c4_stock_change_rows_carry_labels_not_ids(client, db, full, wh):
    w1, w2, w3 = wh
    source, target = _contact(db, "src"), _contact(db, "tgt")
    _policy(db, source, "compact", include=[w1.id, w2.id], hide_zero=True)
    _policy(db, target, "detailed", exclude=[w3.id])
    (row,) = _post(client, source, [target], dry_run=True).json()["results"]
    ch = _changes(row)
    mode = ch["stock_check_mode"]
    assert (mode["label"], mode["before"], mode["after"]) == ("Stock check mode", "detailed", "compact")
    loc = ch["stock_locations"]
    assert loc["label"] == "Locations"
    codes = ", ".join(sorted([w1.warehouse_code, w2.warehouse_code]))
    assert loc["after"] == f"Only {codes}; hide zero-quantity"
    assert loc["before"] == f"All except {w3.warehouse_code}"
    assert w1.id not in str(row), "no warehouse UUIDs in the change rows"


def test_c5_stock_facets_left_out_without_stock_edit(client, db, perms, roles, wh):
    perms["granted"] = {VIEW, EDIT}
    source, target = _contact(db, "src"), _contact(db, "tgt")
    _policy(db, source, "compact")
    _policy(db, target, "detailed", include=[wh[0].id])
    before = _stock(db, target.id)
    body = _post(client, source, [target], dry_run=False).json()
    assert body["results"][0]["status"] == "unchanged"
    assert _stock(db, target.id) == before
    assert {"Stock check mode", "Locations"} <= set(body["source"]["not_copied"])
    assert not {"Stock check mode", "Locations"} & {line["label"] for line in body["source"]["summary"]}


# ---------------------------------------------------------------- companies (C2, C5)


def test_c2_companies_replaced_single_and_bulk(client, db, full):
    a, b, c = _company(db, "a"), _company(db, "b"), _company(db, "c")
    source, t1, t2 = _contact(db, "src"), _contact(db, "t1"), _contact(db, "t2")
    _grant(db, source, a, b)
    _grant(db, t1, c, a)

    (row,) = _post(client, source, [t1], dry_run=True).json()["results"]
    co = _changes(row)["companies"]
    assert co["label"] == "Companies"
    assert co["added"] == [b.name] and co["removed"] == [c.name]

    assert _post(client, source, [t1], dry_run=False).status_code == 200
    assert _companies(db, t1.id) == sorted([a.id, b.id])  # c removed: replace, not merge
    assert _post(client, source, [t1, t2], dry_run=False).status_code == 200
    assert _companies(db, t2.id) == sorted([a.id, b.id])


def test_c5_non_superadmin_cannot_copy_companies(client, db, full, roles):
    roles["slugs"] = set()  # contacts.edit + stock edit, but not superadmin/admin
    a, c = _company(db, "a"), _company(db, "c")
    source, target = _contact(db, "src"), _contact(db, "tgt")
    _grant(db, source, a)
    _grant(db, target, c)
    for dry_run in (True, False):
        body = _post(client, source, [target], dry_run=dry_run).json()
        assert "companies" not in _changes(body["results"][0])
        assert "Companies" in body["source"]["not_copied"]
        assert "Companies" not in {line["label"] for line in body["source"]["summary"]}
    assert _companies(db, target.id) == [c.id]


# ---------------------------------------------------------------- market segments (C3)


def test_c3_market_segments_replaced_single_and_bulk(client, db, full):
    retail, project, other = _segment(db, "r"), _segment(db, "p"), _segment(db, "o")
    source, t1, t2 = _contact(db, "src"), _contact(db, "t1"), _contact(db, "t2")
    source.market_segments = [retail, project]
    t1.market_segments = [other]
    db.flush()

    (row,) = _post(client, source, [t1], dry_run=True).json()["results"]
    ms = _changes(row)["market_segments"]
    assert ms["label"] == "Market segments"
    assert sorted(ms["added"]) == sorted([retail.name, project.name]) and ms["removed"] == [other.name]

    assert _post(client, source, [t1], dry_run=False).status_code == 200
    assert _segments(db, t1.id) == sorted([retail.code, project.code])
    assert _post(client, source, [t1, t2], dry_run=False).status_code == 200
    assert _segments(db, t2.id) == sorted([retail.code, project.code])


def test_c3_market_segments_copied_without_superadmin_or_stock_edit(client, db, perms, roles):
    roles["slugs"] = set()
    seg = _segment(db, "x")
    source, target = _contact(db, "src"), _contact(db, "tgt")
    source.market_segments = [seg]
    db.flush()
    assert _post(client, source, [target], dry_run=False).status_code == 200
    assert _segments(db, target.id) == [seg.code]


# ---------------------------------------------------------------- preview == apply, order, summary (C4, C6)


def test_c4_preview_equals_apply_and_new_facets_follow_the_old_ones(client, db, full, wh):
    a = _company(db, "a")
    seg = _segment(db, "s")
    source, target = _contact(db, "src", notify=True), _contact(db, "tgt")
    _policy(db, source, "availability", include=[wh[0].id])
    _grant(db, source, a)
    source.market_segments = [seg]
    db.flush()
    preview = _post(client, source, [target], dry_run=True).json()["results"]
    applied = _post(client, source, [target], dry_run=False).json()["results"]
    assert preview == applied
    facets = [c["facet"] for c in applied[0]["changes"]]
    assert facets == ["notify_salesman", *NEW_FACETS]


def test_c6_summary_lists_the_four_fields(client, db, full, wh):
    a = _company(db, "a")
    seg = _segment(db, "s")
    source, target = _contact(db, "src"), _contact(db, "tgt")
    _policy(db, source, "compact", include=None)
    _grant(db, source, a)
    source.market_segments = [seg]
    db.flush()
    body = _post(client, source, [target], dry_run=True).json()
    summary = {line["label"]: line["value"] for line in body["source"]["summary"]}
    assert summary["Stock check mode"] == "Compact"
    assert summary["Locations"] == "All locations"
    assert summary["Companies"] == a.name
    assert summary["Market segments"] == seg.name
    assert "Companies" not in body["source"]["not_copied"]


def test_c6_summary_says_inherited_when_source_has_no_stock_override(client, db, full):
    source, target = _contact(db, "src"), _contact(db, "tgt")
    body = _post(client, source, [target], dry_run=True).json()
    summary = {line["label"]: line["value"] for line in body["source"]["summary"]}
    assert summary["Stock check mode"] == "Inherited"
    assert summary["Locations"] == "Inherited"


def test_c1_copy_writes_an_audit_row_carrying_the_new_facets(client, db, full, wh):
    from app.models.audit import AuditLog  # noqa: WPS433

    source, target = _contact(db, "src"), _contact(db, "tgt")
    _policy(db, source, "compact")
    assert _post(client, source, [target], dry_run=False).status_code == 200
    row = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == target.id, AuditLog.event == "contact.access_copied")
        .one()
    )
    assert row.new_values["stock_check_mode"] == "compact"
