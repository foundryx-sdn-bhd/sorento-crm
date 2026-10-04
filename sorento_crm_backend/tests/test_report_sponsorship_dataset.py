"""The sponsorship dataset and definition, report #1 (AC-B1 to AC-B5, AC-B7, AC-B8, AC-B10).

The kernel is proven elsewhere on a synthetic dataset. What is under test HERE is the one
thing that is specific to sponsorship forms: that the catalog reads the columns the client's
workbook reads, and that the two layouts agree with each other.

Every row is seeded by this test on a blank schema. CI's database holds no data, and the
local one is a copy of production - a test that leaned on either would pass for the wrong
reason today and fail for no reason tomorrow.

The rule that runs through all of it: money is ABSENT, never "0.00". A form with no lines
has no sample price, and the workbook prints "-" there; a form whose value is only the text
"BULK ORDER EST RM1.6MIL" contributes nothing to a total.

Run: pytest tests/test_report_sponsorship_dataset.py -q
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.access import RespondContact
from app.models.lookup import LookupOption, LookupSet
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine
from app.models.user import User

from tests._pg_fixture import blank_session

MARKER = "ZZT-SP"

SUBJECT_SET = "procurement_sponsor_subject"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db():
    with blank_session() as session:
        # The sponsorship dataset does not scope by company, but seeding a project does:
        # `register_project` writes an owned row, and an unset scope refuses that write.
        from app.services.company_scope import set_company_scope

        set_company_scope(session, None)
        _seed_subject_lookup(session)
        yield session


def _seed_subject_lookup(db) -> None:
    lookup_set = LookupSet(id=_uid(), set_key=SUBJECT_SET, name="Sponsor subject")
    db.add(lookup_set)
    db.flush()
    for order, (value, label) in enumerate(
        [("showroom", "Showroom"), ("mockup", "Mockup"), ("others", "Others")]
    ):
        db.add(
            LookupOption(
                id=_uid(), set_id=lookup_set.id, value=value, label=label, sort_order=order
            )
        )
    db.flush()


def _contact(db, name=None, first_name=None, last_name=None) -> RespondContact:
    contact = RespondContact(
        id=_uid(),
        phone_number=f"+6011{_uid()[:8]}",
        name=name,
        first_name=first_name,
        last_name=last_name,
        session_vars={},
    )
    db.add(contact)
    db.flush()
    return contact


def _project(db, title: str) -> str:
    """A registered project, inserted directly.

    `register_project` is the real write path, but it needs a numbering rule configured
    first - machinery this test has no opinion about. What is under test is that the
    report READS the linked project's title.
    """
    from app.models.projects import Project

    company_id = db.execute(text("select id from companies where code = 'SRT'")).scalar()
    project_id = _uid()
    db.execute(
        Project.__table__.insert().values(
            id=project_id,
            company_id=str(company_id),
            project_code=f"{MARKER}-{_uid()[:8]}",
            title=title,
            normalised_title=title.lower(),
        )
    )
    db.flush()
    return project_id


def _form(db, number: str, **kwargs) -> PurchaseRequestHeader:
    """One sponsorship form, plus its lines. Everything not named is left NULL."""
    lines = kwargs.pop("lines", ())
    header = PurchaseRequestHeader(
        id=_uid(),
        request_type="sponsorship_form",
        request_number=f"{MARKER}-{number}",
        status=kwargs.pop("status", "approved"),
        **kwargs,
    )
    db.add(header)
    db.flush()
    for order, line in enumerate(lines):
        db.add(
            PurchaseRequestLine(
                id=_uid(),
                purchase_request_id=header.id,
                item_code=f"ITEM-{order}",
                quantity=line.get("quantity"),
                unit_price=line.get("unit_price"),
                total=line.get("total"),
                sort_order=order,
            )
        )
    db.flush()
    return header


# --------------------------------------------------------------------------- running


def _definition():
    from app.services.reports import registry as reg

    definition = reg.get("sponsorship")
    assert definition is not None, "the sponsorship report is not registered"
    return definition


def _run(db, params=None, view=None):
    from app.services.reports import engine

    definition = _definition()
    merged = dict(definition.default_view["params"])
    merged["period"] = {"kind": "year", "year": 2026}
    merged.update(params or {})
    return engine.run(db, definition, merged, view)


def _rows_by_number(result) -> dict:
    return {row["request_number"]: row for row in result.layouts.detail.rows}


def _row(result, number: str) -> dict:
    return _rows_by_number(result)[f"{MARKER}-{number}"]


# ------------------------------------------------------------- AC-B1 the mapping table


def test_the_catalog_holds_the_plan_mapping(db):
    dataset = _definition().dataset
    tags = {c.key: c.tag for c in dataset.columns}

    assert tags["request_number"] == "dimension"
    assert tags["sales_agent"] == "dimension"
    assert tags["customer_name"] == "dimension"
    assert tags["project_title"] == "dimension"
    assert tags["sponsor_subject"] == "dimension"
    assert tags["project_value"] == "measure"
    assert tags["project_value_text"] == "text"
    assert tags["sample_price"] == "measure"
    assert tags["expected_delivery_year"] == "dimension"
    assert tags["month"] == "dimension"
    # Hidden by default, present in the catalog (AC-B7).
    for key in (
        "status",
        "approved_at",
        "request_date",
        "submitted_at",
        "approver",
        "purpose",
        "delivery_address",
        "pic",
    ):
        assert key in tags


def test_sales_agent_comes_from_the_linked_contact(db):
    contact = _contact(db, name="CONTACT Y")
    _form(db, "0001", approved_at=datetime(2026, 1, 5), requested_by="typed name",
          requested_by_contact_id=contact.id)

    assert _row(_run(db), "0001")["sales_agent"] == "CONTACT Y"


def test_sales_agent_uses_the_contact_first_and_last_name_when_it_has_no_name(db):
    contact = _contact(db, first_name="Siti", last_name="Rahim")
    _form(db, "0002", approved_at=datetime(2026, 1, 6), requested_by_contact_id=contact.id)

    assert _row(_run(db), "0002")["sales_agent"] == "Siti Rahim"


def test_sales_agent_falls_back_to_the_typed_name(db):
    _form(db, "0003", approved_at=datetime(2026, 1, 7), requested_by="CONTACT U")

    assert _row(_run(db), "0003")["sales_agent"] == "CONTACT U"


def test_a_form_with_no_requestor_at_all_is_still_counted(db):
    """The pivot drops a blank row dimension, so an unattributed form would otherwise be
    in the detail total and missing from the grand total: two sums, one screen."""
    from app.services.reports.datasets.sponsorship_forms import UNASSIGNED_AGENT

    _form(db, "0010", approved_at=datetime(2026, 1, 14), total_project_value=Decimal("400.00"))

    result = _run(db)
    assert _row(result, "0010")["sales_agent"] == UNASSIGNED_AGENT
    assert result.layouts.summary.grand_total["project_value"] == "400.00"
    assert result.layouts.detail.totals["project_value"] == "400.00"


def test_project_title_prefers_the_linked_project(db):
    project_id = _project(db, f"{MARKER} KSL Setia Alam")
    _form(db, "0004", approved_at=datetime(2026, 1, 8), project_id=project_id,
          project_title="whatever was typed")

    assert _row(_run(db), "0004")["project_title"] == f"{MARKER} KSL Setia Alam"


def test_project_title_falls_back_to_the_typed_title(db):
    _form(db, "0005", approved_at=datetime(2026, 1, 9), project_title="Ecoworld phase 2")

    assert _row(_run(db), "0005")["project_title"] == "Ecoworld phase 2"


def test_sponsor_subject_reads_the_lookup_label(db):
    _form(db, "0006", approved_at=datetime(2026, 1, 10), sponsor_subject="showroom")

    assert _row(_run(db), "0006")["sponsor_subject"] == "Showroom"


def test_sponsor_subject_is_the_free_text_alone_when_it_is_others(db):
    """AC-G5. The workbook's SPONSHER PROJECT column holds the answer, not the question:
    the client types "SALES GALLERY", never "Others: Sales Gallery"."""
    _form(db, "0007", approved_at=datetime(2026, 1, 11), sponsor_subject="others",
          sponsor_subject_other="Sales Gallery")

    assert _row(_run(db), "0007")["sponsor_subject"] == "Sales Gallery"


def test_sponsor_subject_keeps_the_label_when_others_carries_no_free_text(db):
    _form(db, "0071", approved_at=datetime(2026, 1, 11), sponsor_subject="others")

    assert _row(_run(db), "0071")["sponsor_subject"] == "Others"


def test_expected_delivery_year_is_the_year_of_the_delivery_date(db):
    _form(db, "0008", approved_at=datetime(2026, 1, 12),
          expected_delivery_date=date(2027, 6, 10))

    # In the detail it is a tick group (AC-B7); as a pivot dimension it is the year itself.
    summary = _run(db, view=_view_with_pivot("expected_delivery_year", "month")).layouts.summary
    assert summary.row_values == ["2027"]


def test_the_month_column_is_labelled_the_way_the_workbook_labels_it(db):
    _form(db, "0009", approved_at=datetime(2026, 1, 13))

    summary = _run(db).layouts.summary
    assert summary.col_dim.value_labels["2026-01"] == "Jan'26"


# ------------------------------------------------------------------ AC-B2 sample price


def test_sample_price_sums_the_lines(db):
    _form(
        db,
        "0100",
        approved_at=datetime(2026, 2, 1),
        lines=[
            {"total": Decimal("1000.00")},
            # No total: quantity x unit price, as the form's own footer computes it.
            {"quantity": Decimal("3"), "unit_price": Decimal("150.50")},
        ],
    )

    assert _row(_run(db), "0100")["sample_price"] == "1451.50"


def test_a_form_with_no_lines_has_no_sample_price(db):
    _form(db, "0101", approved_at=datetime(2026, 2, 2))

    result = _run(db)
    assert _row(result, "0101")["sample_price"] is None
    # Nothing to total: the measure is absent from the totals, not "0.00".
    assert "sample_price" not in result.layouts.detail.totals


# ------------------------------------------------------------------ AC-B3 project value


def test_project_value_is_the_numeric_column(db):
    _form(db, "0200", approved_at=datetime(2026, 3, 1),
          total_project_value=Decimal("1166830.70"))

    result = _run(db)
    assert _row(result, "0200")["project_value"] == "1166830.70"
    assert result.layouts.detail.totals["project_value"] == "1166830.70"


def test_a_text_only_value_stays_out_of_the_measure_and_out_of_the_total(db):
    _form(db, "0201", approved_at=datetime(2026, 3, 2),
          total_project_value_text="BULK ORDER EST RM1.6MIL")
    _form(db, "0202", approved_at=datetime(2026, 3, 3), total_project_value=Decimal("100.00"))

    result = _run(db, view=_view_with_columns(["request_number", "project_value",
                                               "project_value_text"]))
    text_row = _row(result, "0201")
    assert text_row["project_value"] is None
    assert text_row["project_value_text"] == "BULK ORDER EST RM1.6MIL"
    # The workbook prints "-" in that cell, so the column total is the other form alone.
    assert result.layouts.detail.totals["project_value"] == "100.00"


def _view_with_columns(columns):
    from app.schemas.report import ReportViewConfig

    definition = _definition()
    return ReportViewConfig.model_validate(
        {
            **definition.default_view,
            "detail": {"columns": columns, "order": columns},
        }
    )


def _view_with_columns(columns):
    """A view that asks for particular detail columns, the way the Columns panel does."""
    from app.schemas.report import ReportViewConfig

    definition = _definition()
    return ReportViewConfig.model_validate(
        {**definition.default_view, "detail": {"columns": list(columns), "order": list(columns)}}
    )


def _view_with_pivot(rows: str, cols: str, measures=("project_value",)):
    from app.schemas.report import ReportViewConfig

    definition = _definition()
    return ReportViewConfig.model_validate(
        {
            **definition.default_view,
            "pivot": {"rows": rows, "cols": cols, "measures": list(measures)},
        }
    )


# -------------------------------------------------------------------- AC-B4 date basis


def test_the_default_period_is_the_year_the_user_is_in(db, monkeypatch):
    """Resolved at import, a process booted in 2026 keeps opening the report on 2026 for as
    long as it stays up - and the report the user asked for is always "this year"."""
    from app.services.reports import engine, registry as reg

    monkeypatch.setattr(reg, "today_malaysia", lambda: date(2031, 3, 4))

    assert engine.view_config(_definition()).params["period"] == {"kind": "year", "year": 2031}


def test_the_dataset_offers_the_three_dates(db):
    dataset = _definition().dataset
    assert [b.key for b in dataset.date_bases] == ["approved_at", "request_date", "submitted_at"]
    basis_param = next(
        p for p in _definition().params if getattr(p, "key", None) == "date_basis"
    )
    assert basis_param.default == "approved_at"


def test_switching_the_date_basis_moves_a_form_between_months(db):
    _form(
        db,
        "0300",
        approved_at=datetime(2026, 4, 20),
        request_date=date(2026, 1, 15),
        submitted_at=datetime(2026, 1, 16),
        requested_by="CONTACT U",
        total_project_value=Decimal("500.00"),
    )

    # `month` is a catalog column the default view hides; the summary reads it either way.
    with_month = _view_with_columns(["request_number", "sales_agent", "month"])
    approved = _run(db, view=with_month)
    form_date = _run(db, {"date_basis": "request_date"}, view=with_month)

    assert _row(approved, "0300")["month"] == "2026-04"
    assert _row(form_date, "0300")["month"] == "2026-01"
    assert approved.layouts.summary.cells["CONTACT U"].keys() == {"2026-04"}
    assert form_date.layouts.summary.cells["CONTACT U"].keys() == {"2026-01"}


def test_a_late_evening_utc_approval_lands_in_the_malaysian_month(db):
    """`approved_at` is stored NAIVE UTC, and the whole CRM reads Malaysia time.

    31 Jul 2026 17:30 UTC is 1 Aug 2026 01:30 in Kuala Lumpur, so the form is August's
    takings on both layouts and prints 01/08 in the detail. Bucketing the raw UTC files it
    under July: invisible in a year total, wrong in every summary cell.
    """
    _form(
        db,
        "0310",
        approved_at=datetime(2026, 7, 31, 17, 30),
        requested_by="CONTACT U",
        total_project_value=Decimal("500.00"),
    )

    view = _view_with_columns(["request_number", "sales_agent", "month", "approved_at"])
    result = _run(db, view=view)
    row = _row(result, "0310")

    assert row["month"] == "2026-08"
    assert row["approved_at"] == "2026-08-01"
    assert result.layouts.summary.cells["CONTACT U"].keys() == {"2026-08"}


def test_a_malaysian_new_year_approval_is_not_pushed_out_of_the_period(db):
    """31 Dec 2025 17:00 UTC is 1 Jan 2026 in Kuala Lumpur: inside a 2026 period, and the
    period predicate has to agree with the month bucket or a form falls between them."""
    _form(db, "0311", approved_at=datetime(2025, 12, 31, 17, 0), requested_by="CONTACT U")

    view = _view_with_columns(["request_number", "sales_agent", "month"])
    assert _row(_run(db, view=view), "0311")["month"] == "2026-01"


def test_the_year_list_reads_the_malaysian_year_too(db):
    """A form approved at 2025-12-31 17:00 UTC is a 2026 form here, so 2026 is the year the
    filter must offer for it - not 2025, where the report will show nothing."""
    _form(db, "0312", approved_at=datetime(2025, 12, 31, 17, 0))

    assert _definition().dataset.years(db) == [2026]


def test_a_date_basis_that_is_a_plain_date_is_left_alone(db):
    """`request_date` is a DATE: it carries no time to shift, and shifting it would move
    every form back a day."""
    _form(db, "0313", request_date=date(2026, 1, 1), requested_by="CONTACT U")

    view = _view_with_columns(["request_number", "month", "request_date"])
    row = _row(_run(db, {"date_basis": "request_date"}, view=view), "0313")
    assert row["month"] == "2026-01"
    assert row["request_date"] == "2026-01-01"


def test_a_form_outside_the_period_on_the_chosen_basis_is_absent(db):
    _form(db, "0301", approved_at=datetime(2025, 12, 31), request_date=date(2026, 1, 2))

    assert f"{MARKER}-0301" not in _rows_by_number(_run(db))
    assert f"{MARKER}-0301" in _rows_by_number(_run(db, {"date_basis": "request_date"}))


# ------------------------------------------------------------------------ AC-B5 status


def test_the_default_status_filter_is_approved_and_processed(db):
    _form(db, "0400", approved_at=datetime(2026, 5, 1), status="approved")
    _form(db, "0401", approved_at=datetime(2026, 5, 2), status="processed_by_cs")
    _form(db, "0402", approved_at=datetime(2026, 5, 3), status="submitted")
    _form(db, "0403", approved_at=datetime(2026, 5, 4), status="draft")

    numbers = set(_rows_by_number(_run(db)))
    assert numbers == {f"{MARKER}-0400", f"{MARKER}-0401"}


def test_the_status_filter_widens(db):
    _form(db, "0404", approved_at=datetime(2026, 5, 5), status="submitted")

    result = _run(db, {"status": ["approved", "processed_by_cs", "submitted"]})
    assert f"{MARKER}-0404" in _rows_by_number(result)


def test_a_voided_form_is_never_included_whatever_the_filter_says(db):
    _form(db, "0405", approved_at=datetime(2026, 5, 6), status="voided",
          voided_at=datetime(2026, 5, 7), void_reason="duplicate")

    every_status = [value for value, _label in _status_param().options(db)]
    result = _run(db, {"status": every_status})

    assert f"{MARKER}-0405" not in _rows_by_number(result)
    assert "voided" not in every_status


def _status_param():
    return next(p for p in _definition().params if getattr(p, "key", None) == "status")


# ------------------------------------------------------------------- AC-B6 no scoping


def test_the_dataset_is_company_agnostic_and_says_so(db):
    from app.services.reports.datasets import sponsorship_forms

    assert _definition().dataset.scope == "none"
    assert "company" in (sponsorship_forms.__doc__ or "").lower()


# --------------------------------------------------------------- AC-B7 default columns


def test_the_default_detail_columns_are_the_workbook_columns_in_order(db):
    view = _definition().default_view

    assert view["detail"]["columns"] == [
        "request_number",
        "sales_agent",
        "customer_name",
        "project_title",
        "sponsor_subject",
        "project_value",
        "sample_price",
        "expected_delivery_year",
    ]
    assert view["detail"]["order"] == view["detail"]["columns"]


def test_the_others_column_is_offered_and_hidden(db):
    """AC-G6. The sheet's OTHERS column is the form's own `purpose` free text. Hidden by
    default (the client's twelve sheets leave it empty), offerable in the Columns panel."""
    dataset = _definition().dataset
    column = dataset.column("purpose")

    assert column is not None
    assert column.label == "Others"
    assert column.type == "text"
    assert "purpose" not in _definition().default_view["detail"]["columns"]


def test_the_delivery_year_renders_as_four_fixed_year_columns(db):
    """AC-G3. The workbook's own band: the period's year and the three after it, with ids
    that do not change when the period does."""
    _form(db, "0500", approved_at=datetime(2026, 6, 1),
          expected_delivery_date=date(2026, 9, 1))
    _form(db, "0501", approved_at=datetime(2026, 6, 2),
          expected_delivery_date=date(2027, 1, 1))

    detail = _run(db).layouts.detail
    group = next(g for g in detail.column_groups if g.source == "expected_delivery_year")

    assert group.label == "Expected year of delivery"
    assert group.keys == [f"expected_delivery_year_{index}" for index in range(1, 5)]
    labels = [c.label for c in detail.columns if c.key in group.keys]
    assert labels == ["2026", "2027", "2028", "2029"]
    assert _row(_run(db), "0500")["expected_delivery_year_1"] is True
    assert _row(_run(db), "0500")["expected_delivery_year_2"] is False
    assert _row(_run(db), "0501")["expected_delivery_year_2"] is True


def test_the_year_columns_are_the_same_ids_in_a_year_with_no_delivery_dates(db):
    """AC-G3. 2025's 214 rows tick nothing at all; the four columns still print, and they
    are the same four ids the user's saved column order names."""
    _form(db, "0502", approved_at=datetime(2026, 7, 1))

    detail = _run(db, {"period": {"kind": "year", "year": 2026}}).layouts.detail
    group = next(g for g in detail.column_groups if g.source == "expected_delivery_year")

    assert group.keys == [f"expected_delivery_year_{index}" for index in range(1, 5)]
    assert _row(detail_result := _run(db), "0502")["expected_delivery_year_1"] is False
    assert detail_result is not None


def test_a_delivery_year_outside_the_band_ticks_nothing_and_is_readable_in_its_own_column(db):
    """AC-G3 / S6 review. The band is the period's year and the three after it, so a 2025
    report has no column a 2029 delivery can tick: four blank cells, and until now no way
    on the screen to learn WHEN that form is due. The raw date is a catalog column of its
    own - hidden by default like `purpose`, offerable in the Columns panel."""
    _form(db, "0510", approved_at=datetime(2025, 5, 4),
          expected_delivery_date=date(2029, 3, 1))
    params = {"period": {"kind": "year", "year": 2025}}

    row = _row(_run(db, params), "0510")
    assert [row[f"expected_delivery_year_{index}"] for index in range(1, 5)] == [False] * 4

    column = _definition().dataset.column("expected_delivery_date")
    assert column is not None and column.type == "date"
    assert "expected_delivery_date" not in _definition().default_view["detail"]["columns"]

    shown = _run(db, params, view=_view_with_columns(["request_number", "expected_delivery_date"]))
    assert _row(shown, "0510")["expected_delivery_date"] == "2029-03-01"


# --------------------------------------------------------------- AC-B8 default summary


def test_the_default_summary_is_salesman_by_month(db):
    pivot = _definition().default_view["pivot"]

    assert pivot["rows"] == "sales_agent"
    assert pivot["cols"] == "month"
    assert pivot["measures"] == ["project_value", "sample_price"]


# ------------------------------------------------- AC-B10 the summary IS the detail


@pytest.fixture
def three_forms(db):
    """Two agents, two months. The pivot must be the detail, added up."""
    alice = _contact(db, name="Alice Tan")
    bob = _contact(db, name="Bob Lim")
    _form(db, "0900", approved_at=datetime(2026, 1, 10), requested_by_contact_id=alice.id,
          total_project_value=Decimal("1000.00"), lines=[{"total": Decimal("10.50")}])
    _form(db, "0901", approved_at=datetime(2026, 1, 25), requested_by_contact_id=alice.id,
          total_project_value=Decimal("250.25"))
    _form(db, "0902", approved_at=datetime(2026, 2, 18), requested_by_contact_id=bob.id,
          total_project_value=Decimal("400.00"), lines=[{"total": Decimal("5.00")}])
    return db


def test_a_summary_cell_is_the_sum_of_the_detail_rows_behind_it(three_forms):
    result = _run(three_forms)
    summary = result.layouts.summary

    assert summary.cells["Alice Tan"]["2026-01"]["project_value"] == "1250.25"
    assert summary.cells["Bob Lim"]["2026-02"]["project_value"] == "400.00"
    assert summary.cells["Alice Tan"]["2026-01"]["sample_price"] == "10.50"


def test_an_agent_with_no_form_in_a_month_has_no_cell(three_forms):
    summary = _run(three_forms).layouts.summary

    assert "2026-02" not in summary.cells["Alice Tan"]
    assert "2026-01" not in summary.cells["Bob Lim"]
    # A blank cell is absent, not zero, and the month column is still offered.
    assert "2026-02" in summary.col_dim.values


def test_the_grand_total_equals_the_detail_total(three_forms):
    result = _run(three_forms)

    assert result.layouts.summary.grand_total["project_value"] == "1650.25"
    assert result.layouts.detail.totals["project_value"] == "1650.25"
    assert result.layouts.summary.grand_total["sample_price"] == "15.50"
    assert result.layouts.detail.totals["sample_price"] == "15.50"


def test_the_row_and_column_totals_agree_with_the_cells(three_forms):
    summary = _run(three_forms).layouts.summary

    assert summary.row_totals["Alice Tan"]["project_value"] == "1250.25"
    assert summary.col_totals["2026-01"]["project_value"] == "1250.25"
    assert summary.col_totals["2026-02"]["project_value"] == "400.00"


def test_the_summary_follows_the_date_basis_too(three_forms):
    """The same three forms, none of which carries a form date: the year empties out."""
    result = _run(three_forms, {"date_basis": "request_date"})

    assert result.row_count == 0
    assert result.layouts.summary.row_values == []


# ------------------------------------------------------------------------ the filters


def test_the_sales_agent_filter_offers_the_agents_the_data_holds(three_forms):
    agent_param = next(
        p for p in _definition().params if getattr(p, "key", None) == "sales_agent"
    )
    options = [value for value, _label in agent_param.options(three_forms)]

    assert "Alice Tan" in options
    assert "Bob Lim" in options


def test_the_sales_agent_filter_narrows_the_run(three_forms):
    result = _run(three_forms, {"sales_agent": ["Alice Tan"]})

    assert result.layouts.summary.row_values == ["Alice Tan"]
    assert result.row_count == 2


def test_the_year_list_offers_the_years_the_data_holds(three_forms):
    years = _definition().dataset.years(three_forms)

    assert years[0] == 2026
