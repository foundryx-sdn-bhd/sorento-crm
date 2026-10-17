"""Dealer targets import, sixth real-file gate (lane SALES-TARGET-DEALER, crew 17 Oct, round 5).

Round 5 on the owner's 15 Oct prod backup, masked:
1. The dry-run script never installed the company scope listeners, so `company_scope(...)`
   filtered nothing and the name index held BOTH companies' customers: every tie paired an SRT
   ledger with its MCH twin, and two rows mapped to the other company. The import now names
   its one acting company and reads only that company's customers, whatever the listeners do.
2. A file name with a bracketed branch ("<NAME> (<branch>)") auto-mapped to the one ledger of
   that name, which has no such branch. Not exact, so it waits on a pick (owner ruling 16 Oct).
3. Three rows of one dealer all writing A/C I made one target list one ledger three times: the
   raw "An account is listed twice." is now a row reason naming the rows.
4. Masked OTHER reasons kept their words but x'd the row numbers, which are layout.
"""
from __future__ import annotations

import io

import pytest

from .test_sales_targets_s1 import BASE, _code, _uid, api, world  # noqa: F401  (fixtures)


def _bytes(fill):
    from openpyxl import Workbook

    wb = Workbook()
    fill(wb.active)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _customer(db, company_id, name, level):
    from app.models.order import Customer

    row = Customer(id=_uid(), company_id=company_id, customer_code=_code(), customer_name=name, account_level=level)
    db.add(row)
    db.flush()
    return row


def _one_row(name, account="A/C I"):
    def fill(ws):
        ws["A1"], ws["B1"], ws["C1"] = "DEALER NAME", "ACCOUNT", "Q1"
        ws["A2"], ws["B2"], ws["C2"] = name, account, "100,000 - 1%"

    return _bytes(fill)


def _post(client, db, data):
    files = {"file": ("t.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    db.autoflush = False
    try:
        return client.post(f"{BASE}/import", params={"dry_run": "true", "year": 2026}, files=files).json()
    finally:
        db.autoflush = True


def _other_company(db):
    from app.models.company import Company

    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    return other


def test_the_import_refuses_to_run_without_one_acting_company(api):
    from app.models.base import company_scope
    from app.services.error_handler import AppException
    from app.services.sales.dealer_target_import import run

    _client, db, _ = api
    with company_scope(db, None), pytest.raises(AppException) as err:
        run(db, _one_row("ALFA HOME"), year=2026, dry_run=True)
    assert err.value.status_code == 422
    assert err.value.code == "IMPORT_NEEDS_ONE_COMPANY"


def test_the_name_index_reads_only_the_acting_company_whatever_the_scope(api):
    """Guard against an unscoped session (the dry-run script had no listeners)."""
    from app.models.base import company_scope
    from app.services.sales.dealer_target_import import _CustomerIndex

    _client, db, company_id = api
    other = _other_company(db)
    own = _customer(db, company_id, "BRAVO HOME [A/C I]", 1)
    with company_scope(db, None):
        _customer(db, other.id, "BRAVO HOME - [A/C I]", 1)
        exact, suggestions, _capped, _why = _CustomerIndex(db, company_id).match("BRAVO HOME", "A/C I")
    assert exact is not None and exact.id == str(own.id)
    assert suggestions == []


def test_the_dry_run_script_installs_the_company_scope_listeners(monkeypatch, tmp_path):
    from app.services import company_scope as scope_module
    from scripts import dealer_targets_dry_run

    class Installed(Exception):
        pass

    def spy():
        raise Installed

    monkeypatch.setattr(scope_module, "register_company_scope_listeners", spy)
    path = tmp_path / "t.xlsx"
    path.write_bytes(_one_row("ALFA HOME"))
    with pytest.raises(Installed):
        dealer_targets_dry_run.main([str(path), "--company", "NOPE", "--year", "2026"])


def test_a_branch_no_ledger_has_waits_on_a_pick(api):
    client, db, company_id = api
    plain = _customer(db, company_id, "XRAY HOME SDN BHD [A/C I]", 1)
    row = _post(client, db, _one_row("XRAY HOME SDN BHD (KL)"))["rows"][0]
    assert row["status"] == "needs_pick", row
    assert row["reason"].startswith('no ledger of this name has the branch "KL"'), row["reason"]
    assert [s["name"] for s in row["suggestions"]] == [plain.customer_name]


def test_a_branch_the_ledger_has_still_auto_maps(api):
    client, db, company_id = api
    kl = _customer(db, company_id, "XRAY HOME SDN BHD (KL) [A/C I]", 1)
    row = _post(client, db, _one_row("XRAY HOME SDN BHD (KL)"))["rows"][0]
    assert row["match"] == "auto" and row["customer"] == kl.customer_name


def test_one_ledger_written_on_three_rows_of_a_target_names_the_rows(api):
    client, db, company_id = api
    _customer(db, company_id, "ZULU HOME SDN BHD [A/C I]", 1)

    def fill(ws):
        ws["A1"], ws["B1"], ws["C1"] = "DEALER NAME", "ACCOUNT", "Q1"
        ws["A2"] = "ZULU HOME SDN BHD"
        ws.merge_cells("A2:A4")
        ws["C2"] = "100,000 - 1%"
        ws.merge_cells("C2:C4")
        for r in (2, 3, 4):
            ws[f"B{r}"] = "A/C I"

    rows = _post(client, db, _bytes(fill))["rows"]
    for row in rows:
        assert row["status"] == "not_representable", row
        assert "rows 2, 3 and 4 are one CRM customer" in row["reason"], row["reason"]


def test_masked_reasons_keep_row_numbers():
    from scripts.dealer_targets_dry_run import _mask

    assert "row 46" in _mask("the same accounts and dates as row 46")
    assert "rows 2, 3 and 4" in _mask("rows 2, 3 and 4 are one CRM customer; a target lists each account once")


# --- reviewer + security-reviewer of the gate-6 round --------------------------------------


def test_one_code_on_two_rows_names_both_rows(api):
    """Review B1: two rows sharing a CUSTOMER CODE share one row key; the message said "rows 4 and 4"."""
    client, db, company_id = api
    c1 = _customer(db, company_id, "ZULU HOME SDN BHD [A/C I]", 1)
    c2 = _customer(db, company_id, "ZULU HOME SDN BHD [A/C II]", 2)

    def fill(ws):
        ws["A1"], ws["B1"], ws["C1"] = "DEALER NAME", "CUSTOMER CODE", "Q1"
        ws["A2"] = "ZULU HOME SDN BHD"
        ws.merge_cells("A2:A4")
        ws["C2"] = "100,000 - 1%"
        ws.merge_cells("C2:C4")
        ws["B2"], ws["B3"], ws["B4"] = c1.customer_code, c2.customer_code, c1.customer_code

    rows = _post(client, db, _bytes(fill))["rows"]
    assert all("rows 2 and 4 are one CRM customer" in r["reason"] for r in rows), [r["reason"] for r in rows]


def test_a_paren_that_is_the_matched_account_is_not_a_branch(api):
    """Review S1: "(CERAMIC)" in the file name is the account the ledger matched on."""
    client, db, company_id = api
    ceramic = _customer(db, company_id, "MOCHA K SDN BHD [CERAMIC]", None)

    def fill(ws):
        ws["A1"], ws["B1"] = "DEALER NAME", "Q1"
        ws["A2"], ws["B2"] = "MOCHA K SDN BHD (CERAMIC)", "100,000 - 1%"

    row = _post(client, db, _bytes(fill))["rows"][0]
    assert row["match"] == "auto" and row["customer"] == ceramic.customer_name, row


def test_the_malaysia_marker_and_a_registration_number_are_not_branches(api):
    client, db, company_id = api
    one = _customer(db, company_id, "ABC TRADING SDN BHD [A/C I]", 1)
    for name in ("ABC TRADING (M) SDN BHD", "ABC TRADING SDN BHD (123456-X)"):
        row = _post(client, db, _one_row(name))["rows"][0]
        assert row["match"] == "auto" and row["customer"] == one.customer_name, (name, row)


def test_the_reason_names_the_branch_that_is_missing(api):
    """Review N2: not the first paren, the one the ledger lacks."""
    client, db, company_id = api
    _customer(db, company_id, "ECHO HOME (SRT) [A/C I]", 1)
    row = _post(client, db, _one_row("ECHO HOME (SRT) (KL)"))["rows"][0]
    assert row["reason"].startswith('no ledger of this name has the branch "KL"'), row["reason"]


def test_masked_output_keeps_no_file_digits():
    """Security S1 + review S2: a colon or "row <n>" inside quoted file text, and codes."""
    from scripts.dealer_targets_dry_run import _mask

    for reason in (
        'no ledger of this name has the branch "LOT 23: KL"; pick one of its ledgers or skip',
        'Q1: cannot read the threshold "narrow 12345 deal"',
        "no customer with code borrow 4471",
        "no customer with code row 4471",
        'no ledger of this name has the account "row 77"; pick one of its ledgers or skip',
    ):
        masked = _mask(reason)
        assert not any(d in masked for d in ("23", "12345", "4471", "77")), (reason, masked)
    assert _mask("shares a target with row 46, which needs a customer picked").endswith("row 46")


def _unscoped_as(monkeypatch, company_id):
    """A session with no scope filter at all (what the dry-run script had), yet run() told it acts
    for one company: only the explicit filters keep the other company out."""
    from app.services.sales import dealer_target_import

    monkeypatch.setattr(dealer_target_import, "_acting_company", lambda db: company_id)


def test_a_code_of_another_company_is_unknown_without_the_listener(api, monkeypatch):
    from app.models.base import company_scope
    from app.services.sales.dealer_target_import import run

    _client, db, company_id = api
    other = _other_company(db)
    _unscoped_as(monkeypatch, company_id)

    def fill(ws):
        ws["A1"], ws["B1"], ws["C1"] = "DEALER NAME", "CUSTOMER CODE", "Q1"
        ws["A2"], ws["B2"], ws["C2"] = "FOX HOME", foreign.customer_code, "100,000 - 1%"

    with company_scope(db, None):
        foreign = _customer(db, other.id, "FOX HOME [A/C I]", 1)
        row = run(db, _bytes(fill), year=2026, dry_run=True)["rows"][0]
    assert row["status"] == "not_representable" and "no customer with code" in row["reason"], row


def test_a_pick_of_another_company_is_refused_without_the_listener(api, monkeypatch):
    from app.models.base import company_scope
    from app.services.sales.dealer_target_import import run

    _client, db, company_id = api
    other = _other_company(db)
    _unscoped_as(monkeypatch, company_id)
    with company_scope(db, None):
        foreign = _customer(db, other.id, "GOLF HOME [A/C I]", 1)
        row = run(db, _one_row("NOBODY HOME"), year=2026, dry_run=True, picks={"Sheet!2": foreign.id})["rows"][0]
    assert row["status"] == "not_representable", row


def test_another_company_s_target_is_never_updated_without_the_listener(api, monkeypatch):
    from datetime import date

    from app.models.base import company_scope
    from app.models.sales import SalesTarget, SalesTargetAccount
    from app.services.sales.dealer_target_import import run

    _client, db, company_id = api
    other = _other_company(db)
    own = _customer(db, company_id, "HOTEL HOME [A/C I]", 1)
    _unscoped_as(monkeypatch, company_id)
    with company_scope(db, None):
        foreign_target = SalesTarget(
            id=_uid(), company_id=other.id, target_no="TGT-999998", name="ZZT Foreign dealer",
            subject_kind="dealer", metric="amount", basis="ordered", product_scope="all",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), split_every=3, split_unit="month",
            threshold_unit="amount",
        )
        db.add(foreign_target)
        db.flush()
        db.add(SalesTargetAccount(id=_uid(), company_id=other.id, target_id=foreign_target.id, customer_id=own.id))
        db.flush()
        result = run(db, _one_row("HOTEL HOME"), year=2026, dry_run=True)
    # Read as its own company's new target, never as an update of the foreign one.
    assert result["rows"][0]["status"] == "mapped", result["rows"][0]["reason"]
    assert (result["summary"]["targets_new"], result["summary"]["targets_updated"]) == (1, 0), result["summary"]


def test_a_repeated_ledger_names_every_row_of_the_dealer_on_it(api):
    """Gate 7 nit: a quarterly target on rows 2-3 and a yearly one on rows 2-4, all one ledger:
    every row's reason names rows 2, 3 and 4, not only the first target's two."""
    client, db, company_id = api
    _customer(db, company_id, "ZULU HOME SDN BHD [A/C I]", 1)

    def fill(ws):
        ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "DEALER NAME", "ACCOUNT", "Q1", "YEARLY"
        ws["A2"] = "ZULU HOME SDN BHD"
        ws.merge_cells("A2:A4")
        ws["C2"] = "100,000 - 1%"
        ws.merge_cells("C2:C3")
        ws["D2"] = "1,000,000 - 1%"
        ws.merge_cells("D2:D4")
        for r in (2, 3, 4):
            ws[f"B{r}"] = "A/C I"

    rows = _post(client, db, _bytes(fill))["rows"]
    assert [r["reason"].split(";")[0] for r in rows] == ["rows 2, 3 and 4 are one CRM customer"] * 3, rows
