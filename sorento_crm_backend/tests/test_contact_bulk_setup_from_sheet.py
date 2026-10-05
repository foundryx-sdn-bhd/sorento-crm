"""Lane CONTACT-BULK-SETUP, addendum 5 Oct: ``contact_bulk_setup.from_sheet`` (the ``--from-sheet``
write-back of the owner's edited contact access export).

RED tests, written before the mode exists: every call fails with AttributeError (no ``from_sheet``).
Fake data only. Postgres only (``blank_session``); everything is seeded here.

Interface pinned: ``from_sheet(db, path, *, sheet="Sheet2", apply=False, allow_removals=False,
actor_id=None, out_dir=None) -> dict`` with ``counts`` (per field {"add","remove","set"} for
access_types, companies, market_segments, stock_override, customers; plus ``errors`` and
``contacts_changed``), ``plan`` (list of {contact_id, contact, field, before, after, action}) and
``errors`` (list of {row, reason}). Writes ``out_dir/writeback-plan.csv`` (0600, formula-safe).
Audit: ``contact.sheet_writeback`` per changed contact, ``contact.customer_linked_bulk`` per new link,
both through the module attribute ``audit_service.record``.
"""
from __future__ import annotations

import csv
import os

import openpyxl
import pytest

import scripts.contact_bulk_setup as cbs

from app.models.access import (
    ContactAccessType,
    MarketSegment,
    RespondContactCustomer,
    StockVisibilityPolicy,
)
from app.models.company import Company
from app.models.inventory import Warehouse
from app.services import audit_service

# fixtures and helpers reused from the existing suite (pytest picks the fixtures up by name)
from tests.test_contact_bulk_setup_script import (  # noqa: F401
    _audits,
    _contact,
    _customer,
    _state,
    db,
    dbphone,
    w,
)

HEADER = [
    "contact_id", "respond_io_id", "phone", "name", "exist in dealer contact", "boss / employee", "company",
    "access_types", "companies", "market_segments", "stock_mode_contact_override", "stock_source",
    "stock_mode_effective", "stock_locations_effective", "customers",
]


def _sheet(tmp_path, rows, name="edited.xlsx"):
    """rows: dicts keyed by HEADER names; a missing key is a blank cell. Data lives on Sheet2."""
    wb = openpyxl.Workbook()
    decoy = wb.active
    decoy.title = "Sheet1"
    decoy.append(["something", "else"])
    decoy.append(["ZZT decoy", 1])
    ws = wb.create_sheet("Sheet2")
    ws.append(HEADER)
    for r in rows:
        ws.append([r.get(h) for h in HEADER])
    path = tmp_path / name
    wb.save(path)
    return path


def _cust_text(db, contact_id):
    from app.models.order import Customer

    db.expire_all()
    parts = []
    for link in db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == contact_id):
        cu = db.get(Customer, link.customer_id)
        co = db.get(Company, cu.company_id)
        parts.append(f"{co.code} {cu.customer_code} {cu.customer_name}")
    return "; ".join(sorted(parts)) or None


def _full(db, w, c, **over):
    """The sheet row that reproduces the contact's current state (so a test changes only what it names)."""
    s = _state(db, c.id)
    code_of = {w.a.id: "ZSRT", w.b.id: "ZMCH"}
    row = {
        "contact_id": c.id,
        "phone": int(c.phone_number),
        "name": c.name,
        "access_types": ", ".join(s["types"]) or "-",
        "companies": ", ".join(sorted(code_of[i] for i in s["companies"])) or "-",
        "market_segments": ", ".join(s["segments"]) or "-",
        "stock_source": "default",
        "stock_mode_effective": "detailed",
        "stock_locations_effective": "All locations",
        "customers": _cust_text(db, c.id),
    }
    row.update(over)
    return row


def _go(db, w, path, **kw):
    kw.setdefault("actor_id", w.actor)
    return cbs.from_sheet(db, path, **kw)


def _fields(res, field):
    return [p for p in res["plan"] if p["field"] == field]


def _policy(db, contact_id):
    db.expire_all()
    return db.query(StockVisibilityPolicy).filter(StockVisibilityPolicy.contact_id == contact_id).first()


def _links(db, contact_id):
    db.expire_all()
    return db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == contact_id).count()


# 1
def test_set_exactly_access_types_adds_and_removes(db, tmp_path, w):
    c = _contact(db, 1)
    c.access_types.extend([w.types["zzt_dealer"], w.types["zzt_end_user"]])
    db.flush()
    path = _sheet(tmp_path, [_full(db, w, c, access_types="zzt_end_user, zzt_extra")])
    res = _go(db, w, path)
    assert res["counts"]["access_types"] == {"add": 1, "remove": 1, "set": 0} or (
        res["counts"]["access_types"]["add"] == 1 and res["counts"]["access_types"]["remove"] == 1
    )
    assert {p["action"] for p in _fields(res, "access_types")} == {"add", "remove"}
    assert _state(db, c.id)["types"] == ["zzt_dealer", "zzt_end_user"], "dry run writes nothing"
    _go(db, w, path, apply=True)
    assert _state(db, c.id)["types"] == ["zzt_end_user", "zzt_extra"]


# 2
def test_companies_and_market_segments_set_exactly(db, tmp_path, w):
    c = _contact(db, 1)
    c.market_segments.append(w.seg)
    from app.models.company import RespondContactCompany

    db.add(RespondContactCompany(respond_contact_id=c.id, company_id=w.a.id))
    db.flush()
    path = _sheet(tmp_path, [_full(db, w, c, companies="ZMCH", market_segments="zzt_project")])
    res = _go(db, w, path, apply=True)
    assert res["counts"]["companies"]["add"] == 1 and res["counts"]["companies"]["remove"] == 1
    assert res["counts"]["market_segments"]["add"] == 1 and res["counts"]["market_segments"]["remove"] == 1
    s = _state(db, c.id)
    assert s["companies"] == [w.b.id]
    assert s["segments"] == ["zzt_project"]


# 3
def test_key_fallback_contact_id_then_respond_io_id_then_phone(db, tmp_path, w):
    by_id, by_rio, by_phone, none = _contact(db, 1), _contact(db, 2), _contact(db, 3), _contact(db, 4)
    by_rio.respond_io_id = "990002"
    db.flush()
    rows = [
        _full(db, w, by_id, access_types="zzt_extra"),
        _full(db, w, by_rio, contact_id=None, respond_io_id=990002, access_types="zzt_extra"),
        _full(db, w, by_phone, contact_id=None, respond_io_id=None, access_types="zzt_extra"),
        {**_full(db, w, none, access_types="zzt_extra"), "contact_id": None, "respond_io_id": None, "phone": 601700009999},
    ]
    res = _go(db, w, _sheet(tmp_path, rows), apply=True)
    for c in (by_id, by_rio, by_phone):
        assert _state(db, c.id)["types"] == ["zzt_extra"]
    assert _state(db, none.id)["types"] == []
    assert res["counts"]["errors"] == 1 and len(res["errors"]) == 1
    assert res["errors"][0]["row"] == 5 and res["errors"][0]["reason"]


# 4
def test_stock_override_sets_mode_includes_and_hide_zero(db, tmp_path, w):
    c1, c2, c3 = _contact(db, 1), _contact(db, 2), _contact(db, 3)
    rows = [
        _full(db, w, c1, companies="ZMCH, ZSRT", stock_mode_contact_override="availability", stock_source="contact",
              stock_mode_effective="availability", stock_locations_effective="Only ZMCH:ZHQ, ZSRT:ZBRW"),
        _full(db, w, c2, companies="ZSRT", stock_mode_contact_override="compact", stock_source="contact",
              stock_mode_effective="compact", stock_locations_effective="All locations"),
        _full(db, w, c3, companies="ZSRT", stock_mode_contact_override="detailed", stock_source="contact",
              stock_mode_effective="detailed", stock_locations_effective="Only ZSRT:ZMWH; hide zero-quantity"),
    ]
    res = _go(db, w, _sheet(tmp_path, rows), apply=True)
    assert res["counts"]["stock_override"]["set"] == 3
    p1, p2, p3 = (_policy(db, c.id) for c in (c1, c2, c3))
    assert p1.mode == "availability"
    assert sorted(p1.warehouse_ids) == sorted([w.wh["ZHQ"].id, w.wh["ZBRW"].id])
    assert p1.excluded_warehouse_ids is None and p1.hide_zero_locations is False
    assert p2.mode == "compact" and p2.warehouse_ids is None and p2.excluded_warehouse_ids is None
    assert p3.warehouse_ids == [w.wh["ZMWH"].id] and p3.hide_zero_locations is True


# 5
def test_blank_override_clears_row_unless_source_is_contact(db, tmp_path, w):
    c1, c2 = _contact(db, 1), _contact(db, 2)
    for c in (c1, c2):
        db.add(StockVisibilityPolicy(contact_id=c.id, mode="compact", warehouse_ids=None, hide_zero_locations=False))
    db.flush()
    rows = [
        _full(db, w, c1, stock_mode_contact_override=None, stock_source="access_type",
              stock_mode_effective="compact", stock_locations_effective="All locations"),
        _full(db, w, c2, stock_mode_contact_override=None, stock_source="contact",
              stock_mode_effective="compact", stock_locations_effective="All locations"),
    ]
    res = _go(db, w, _sheet(tmp_path, rows), apply=True)
    assert [p["action"] for p in _fields(res, "stock_override") if p["contact_id"] == c1.id] == ["clear"]
    assert _policy(db, c1.id) is None
    assert _policy(db, c2.id) is not None, "the row with the error is skipped whole"
    assert res["counts"]["errors"] == 1 and res["errors"][0]["row"] == 3


# 6
def test_derived_mismatch_is_a_row_error_and_skips_the_whole_row(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, access_types="zzt_extra", stock_mode_contact_override="compact",
                stock_source="contact", stock_mode_effective="availability",
                stock_locations_effective="All locations")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1
    assert "stock_mode_contact_override" in res["errors"][0]["reason"]
    assert _state(db, c.id)["types"] == [] and _policy(db, c.id) is None
    assert res["counts"]["contacts_changed"] == 0


# 7
def test_customers_add_remove_markers_and_fallback(db, tmp_path, w):
    c = _contact(db, 1)
    gone = _customer(db, w.a, "ZZT Gone Dealer", "ZZT-G1")
    cu_new = _customer(db, w.a, "ZZT New Dealer", "ZZT-N1")
    cu_fb = _customer(db, w.b, "ZZT Fallback Dealer", "ZZT-F1")
    cu_fb.is_active = False
    db.add(RespondContactCustomer(contact_id=c.id, customer_id=gone.id, company_id=w.a.id, is_primary=False, source="manual"))
    db.flush()
    text = "ZSRT ZZT-N1 ZZT New Dealer (primary); ZMCH ZZT-F1 (inactive)"
    path = _sheet(tmp_path, [_full(db, w, c, companies="ZSRT, ZMCH", customers=text)])
    res = _go(db, w, path)
    acts = sorted((p["action"]) for p in _fields(res, "customers"))
    assert acts == ["add", "add", "remove"]
    assert res["counts"]["customers"]["add"] == 2 and res["counts"]["customers"]["remove"] == 1
    assert _links(db, c.id) == 1, "dry run writes nothing"

    _go(db, w, path, apply=True)  # removals not allowed
    db.expire_all()
    linked = {r.customer_id for r in db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == c.id)}
    assert linked == {gone.id, cu_new.id, cu_fb.id}
    assert all(not r.is_primary for r in db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == c.id)), \
        "the (primary) marker is not written"

    _go(db, w, path, apply=True, allow_removals=True)
    db.expire_all()
    linked = {r.customer_id for r in db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == c.id)}
    assert linked == {cu_new.id, cu_fb.id}


def test_unknown_customer_is_a_row_error(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, access_types="zzt_extra", customers="ZSRT ZZT-NOPE ZZT Nobody")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and "ZZT-NOPE" in res["errors"][0]["reason"]
    assert _state(db, c.id)["types"] == []


# 8
@pytest.mark.parametrize(
    "over",
    [
        {"access_types": "zzt_nope"},
        {"companies": "ZNOPE"},
        {"market_segments": "zzt_nope"},
        {"stock_mode_contact_override": "availability", "stock_source": "contact",
         "stock_mode_effective": "availability", "stock_locations_effective": "Only ZSRT:ZNOPE"},
    ],
)
def test_unknown_codes_are_row_errors_and_nothing_is_created(db, tmp_path, w, over):
    c = _contact(db, 1)
    n = (db.query(ContactAccessType).count(), db.query(Company).count(),
         db.query(MarketSegment).count(), db.query(Warehouse).count())
    res = _go(db, w, _sheet(tmp_path, [_full(db, w, c, **over)]), apply=True)
    assert res["counts"]["errors"] == 1 and res["errors"][0]["row"] == 2
    assert n == (db.query(ContactAccessType).count(), db.query(Company).count(),
                 db.query(MarketSegment).count(), db.query(Warehouse).count())
    assert _state(db, c.id)["types"] == [] and _policy(db, c.id) is None


# 9
def test_dry_run_writes_nothing_and_a_0600_plan_csv(db, tmp_path, w):
    c = _contact(db, 1)
    c.name = "=ZZT Evil"
    db.flush()
    before = _state(db, c.id)
    out = tmp_path / "out"
    out.mkdir()
    res = _go(db, w, _sheet(tmp_path, [_full(db, w, c, access_types="zzt_extra")]), out_dir=out)
    assert _state(db, c.id) == before
    assert _audits(db, event="contact.sheet_writeback") == []
    assert res["counts"]["contacts_changed"] == 1
    plan = out / "writeback-plan.csv"
    assert os.stat(plan).st_mode & 0o777 == 0o600
    with open(plan, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["contact", "field", "before", "after", "action"]
        rows = list(reader)
    assert rows and {r["contact"] for r in rows} == {"'=ZZT Evil"}
    assert {(r["field"], r["action"]) for r in rows} == {("access_types", "add")}


# 10
def test_apply_audits_per_changed_contact_and_second_apply_is_a_noop(db, tmp_path, w):
    c1, c2, c3 = _contact(db, 1), _contact(db, 2), _contact(db, 3)
    cu = _customer(db, w.a, "ZZT Dealer One", "ZZT-A1")
    path = _sheet(tmp_path, [
        _full(db, w, c1, access_types="zzt_extra", companies="ZSRT", customers="ZSRT ZZT-A1 ZZT Dealer One"),
        _full(db, w, c2, market_segments="zzt_project"),
        _full(db, w, c3),  # already exact
    ])
    res = _go(db, w, path, apply=True)
    assert res["counts"]["contacts_changed"] == 2
    assert len(_audits(db, c1.id, "contact.sheet_writeback")) == 1
    assert len(_audits(db, c2.id, "contact.sheet_writeback")) == 1
    assert _audits(db, c3.id, "contact.sheet_writeback") == []
    assert len(_audits(db, c1.id, "contact.customer_linked_bulk")) == 1
    assert _links(db, c1.id) == 1 and cu.id

    total = len(_audits(db, event="contact.sheet_writeback")), len(_audits(db, event="contact.customer_linked_bulk"))
    again = _go(db, w, path, apply=True)
    assert again["counts"]["contacts_changed"] == 0 and again["plan"] == []
    assert total == (len(_audits(db, event="contact.sheet_writeback")), len(_audits(db, event="contact.customer_linked_bulk")))


# 11
def test_one_transaction_a_late_failure_persists_nothing(db, tmp_path, w, monkeypatch):
    c1, c2 = _contact(db, 1), _contact(db, 2)
    path = _sheet(tmp_path, [_full(db, w, c1, access_types="zzt_extra"), _full(db, w, c2, access_types="zzt_extra")])
    db.commit()  # seeds survive a whole-transaction rollback
    real = audit_service.record
    hit = []

    def boom(db_, **kw):
        if kw.get("entity_id") == c2.id:
            hit.append(1)
            raise RuntimeError("simulated failure")
        return real(db_, **kw)

    monkeypatch.setattr(audit_service, "record", boom)
    assert hasattr(cbs, "from_sheet")
    try:
        _go(db, w, path, apply=True)
    except Exception:  # raising or reporting are both acceptable; persisting is not
        db.rollback()
    assert hit, "the audit failure for the 2nd contact must actually have been reached"
    assert _state(db, c1.id)["types"] == []
    assert _state(db, c2.id)["types"] == []
    assert _audits(db, event="contact.sheet_writeback") == []


# 12
def test_cli_from_sheet_requires_out(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cbs.main(["--from-sheet", str(tmp_path / "x.xlsx"), "--db-url", "postgresql://example.invalid/none"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--out" in err and "--excel" not in err, "from-sheet mode must not demand --excel"


# ---------------------------------------------------------------- review round
def _override(db, c, mode="compact", **kw):
    db.add(StockVisibilityPolicy(contact_id=c.id, mode=mode, warehouse_ids=None, hide_zero_locations=False, **kw))
    db.flush()


# A1 (blocker): derived cells are compared with the current database values
def test_a1_changed_override_with_stale_effective_cell_is_not_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    _override(db, c, "compact")
    row = _full(db, w, c, stock_mode_contact_override="availability", stock_source="contact",
                stock_mode_effective="compact", stock_locations_effective="All locations")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 0, res["errors"]
    assert _policy(db, c.id).mode == "availability"


# A2
def test_a2_edited_effective_mode_with_blank_override_becomes_a_contact_override(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, companies="ZSRT", stock_mode_contact_override=None, stock_source="default",
                stock_mode_effective="compact", stock_locations_effective="Only ZSRT:ZBRW")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 0, res["errors"]
    (p,) = _fields(res, "stock_override")
    assert p["action"] == "set" and "compact" in str(p["after"])
    pol = _policy(db, c.id)
    assert pol.mode == "compact" and pol.warehouse_ids == [w.wh["ZBRW"].id]


VALID = {"access_types": "zzt_extra", "companies": "ZSRT", "market_segments": "zzt_retail"}  # real codes only


# A4
def test_a4_override_and_effective_both_edited_to_different_values_is_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, **VALID, stock_mode_contact_override="availability", stock_source="contact",
                stock_mode_effective="compact")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and _policy(db, c.id) is None
    assert "stock_mode_effective" in res["errors"][0]["reason"]


# A5
def test_a5_edited_locations_without_a_resulting_override_is_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, **VALID, stock_locations_effective="Only ZSRT:ZBRW")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and _policy(db, c.id) is None
    assert "locations" in res["errors"][0]["reason"]


# B
@pytest.mark.parametrize("field", ["access_types", "companies", "market_segments"])
def test_b_blank_set_cell_is_a_row_error(db, tmp_path, w, field):
    c = _contact(db, 1)
    c.access_types.append(w.types["zzt_dealer"])
    db.flush()
    before = _state(db, c.id)
    res = _go(db, w, _sheet(tmp_path, [_full(db, w, c, **{field: None})]), apply=True)
    assert res["counts"]["errors"] == 1 and "blank" in res["errors"][0]["reason"]
    assert _state(db, c.id) == before


def test_b_dash_empties_the_sets(db, tmp_path, w):
    from app.models.company import RespondContactCompany

    c = _contact(db, 1)
    c.access_types.append(w.types["zzt_dealer"])
    c.market_segments.append(w.seg)
    db.add(RespondContactCompany(respond_contact_id=c.id, company_id=w.a.id))
    db.flush()
    row = _full(db, w, c, access_types="-", companies="-", market_segments="-")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 0, res["errors"]
    s = _state(db, c.id)
    assert (s["types"], s["companies"], s["segments"]) == ([], [], [])


# C
def test_c_customer_name_that_does_not_match_is_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    _customer(db, w.a, "ZZT Real Name", "ZZT-C1")
    row = _full(db, w, c, **VALID, customers="ZSRT ZZT-C1 ZZT Wrong Name")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and _links(db, c.id) == 0
    assert "name does not match" in res["errors"][0]["reason"]


def test_c_customer_company_outside_the_rows_companies_is_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    _customer(db, w.b, "ZZT Mocha Dealer", "ZZT-C2")
    row = _full(db, w, c, **VALID, customers="ZMCH ZZT-C2 ZZT Mocha Dealer")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and _links(db, c.id) == 0
    assert "not in the row" in res["errors"][0]["reason"]


def test_c_warehouse_company_outside_the_rows_companies_is_an_error(db, tmp_path, w):
    c = _contact(db, 1)
    row = _full(db, w, c, **VALID, stock_mode_contact_override="compact", stock_source="contact",
                stock_mode_effective="compact", stock_locations_effective="Only ZMCH:ZHQ")
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1 and _policy(db, c.id) is None
    assert "warehouse" in res["errors"][0]["reason"]


# D
def test_d_a_contact_matched_by_two_rows_errors_every_row(db, tmp_path, w):
    c, other = _contact(db, 1), _contact(db, 2)
    rows = [_full(db, w, c, access_types="zzt_extra"),
            {**_full(db, w, c, access_types="zzt_end_user"), "contact_id": None},
            _full(db, w, other, access_types="zzt_extra")]
    res = _go(db, w, _sheet(tmp_path, rows), apply=True)
    assert res["counts"]["errors"] == 2 and sorted(e["row"] for e in res["errors"]) == [2, 3]
    assert _state(db, c.id)["types"] == []
    assert _state(db, other.id)["types"] == ["zzt_extra"]


# E
@pytest.mark.parametrize("over,word", [({"phone": 601700009998}, "phone"), ({"respond_io_id": 998877}, "respond_io_id")])
def test_e_contact_id_disagreeing_with_phone_or_respond_io_id_is_an_error(db, tmp_path, w, over, word):
    c = _contact(db, 1)
    c.respond_io_id = "112233"
    db.flush()
    res = _go(db, w, _sheet(tmp_path, [_full(db, w, c, **{**VALID, **over})]), apply=True)
    assert res["counts"]["errors"] == 1 and _state(db, c.id)["types"] == []
    assert word in res["errors"][0]["reason"]


def test_blank_locations_cell_never_becomes_all_locations(db, tmp_path, w):
    c = _contact(db, 1)
    c.access_types.append(w.types["zzt_dealer"])
    db.add(StockVisibilityPolicy(access_type_code="zzt_dealer", mode="availability",
                                 warehouse_ids=[w.wh["ZBRW"].id], hide_zero_locations=False))
    db.flush()
    row = _full(db, w, c, companies="ZSRT", market_segments="zzt_retail", stock_mode_contact_override="availability",
                stock_source="access_type", stock_mode_effective="availability", stock_locations_effective=None)
    res = _go(db, w, _sheet(tmp_path, [row]), apply=True)
    assert res["counts"]["errors"] == 1, res["counts"]
    assert "locations" in res["errors"][0]["reason"]
    assert _policy(db, c.id) is None
