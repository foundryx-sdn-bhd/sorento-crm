"""The reporting kernel, proven on a SYNTHETIC dataset (AC-A2 to AC-A7, AC-A9).

The dataset is the scratch one in tests/_report_fixture.py, never the sponsorship report:
the claim under test is that the kernel is generic, and CI's database holds no data.

Run: pytest tests/test_report_engine_kernel.py -q
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from tests import _report_fixture as fixture
from tests._pg_fixture import pg_session

TABLE = fixture.TABLE


@pytest.fixture
def db():
    with pg_session() as session:
        fixture.create_table(session)
        yield session


@pytest.fixture
def definition():
    return fixture.definition()


def _run(db, definition, params=None, view=None, **kwargs):
    from app.services.reports import engine

    merged = dict(definition.default_view["params"])
    merged.update(params or {})
    return engine.run(db, definition, merged, view, **kwargs)


# --------------------------------------------------------------- AC-A2 declarations


def test_a_dataset_without_a_scope_declaration_is_refused():
    """AC-A2. Silence about scope is how a company's rows leak into another's report."""
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    with pytest.raises(ValueError) as excinfo:
        reg.Dataset(
            key="zzt_no_scope",
            scope=None,
            columns=(reg.Column("agent", "Agent", "text", "dimension", lambda c: t.c.agent),),
            date_bases=(reg.DateBasis("booked_on", "Booked", t.c.booked_on),),
            base=lambda c: sa.select().select_from(t),
        )
    assert "scope" in str(excinfo.value)


def test_a_company_scoped_dataset_must_name_its_company_column():
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    with pytest.raises(ValueError):
        reg.Dataset(
            key="zzt_bad_company",
            scope="company",
            columns=(reg.Column("agent", "Agent", "text", "dimension", lambda c: t.c.agent),),
            date_bases=(reg.DateBasis("booked_on", "Booked", t.c.booked_on),),
            base=lambda c: sa.select().select_from(t),
        )


def test_a_column_with_an_unknown_tag_is_refused():
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    with pytest.raises(ValueError):
        reg.Column("agent", "Agent", "text", "grouping", lambda c: t.c.agent)


def test_a_column_with_an_unknown_type_is_refused():
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    with pytest.raises(ValueError):
        reg.Column("agent", "Agent", "currency", "dimension", lambda c: t.c.agent)


def test_every_catalog_column_carries_a_label_a_type_and_a_tag(definition):
    for column in definition.dataset.columns:
        assert column.label
        assert column.type in {"text", "money", "integer", "date", "bool"}
        assert column.tag in {"dimension", "measure", "date", "text"}


# ------------------------------------------------------------------ AC-A3 validation


def test_an_unknown_param_is_a_422_naming_the_field(db, definition):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _run(db, definition, {"salesperson": ["Alice"]})
    assert excinfo.value.status_code == 422
    assert "salesperson" in str(excinfo.value.detail)


def test_an_unknown_date_basis_is_a_422_naming_the_field(db, definition):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _run(db, definition, {"date_basis": "invoiced_on"})
    assert excinfo.value.status_code == 422
    assert "invoiced_on" in str(excinfo.value.detail)


def test_an_unknown_pivot_dimension_is_a_422(db, definition):
    from fastapi import HTTPException

    from app.schemas.report import ReportViewConfig

    view = ReportViewConfig(
        params={},
        detail={"columns": [], "order": []},
        pivot={"rows": "salesperson", "cols": "month", "measures": ["amount"]},
    )
    with pytest.raises(HTTPException) as excinfo:
        _run(db, definition, view=view)
    assert excinfo.value.status_code == 422
    assert "salesperson" in str(excinfo.value.detail)


def test_rows_and_columns_cannot_be_the_same_dimension(db, definition):
    from fastapi import HTTPException

    from app.schemas.report import ReportViewConfig

    view = ReportViewConfig(
        params={},
        detail={"columns": [], "order": []},
        pivot={"rows": "agent", "cols": "agent", "measures": ["amount"]},
    )
    with pytest.raises(HTTPException) as excinfo:
        _run(db, definition, view=view)
    assert excinfo.value.status_code == 422


# ------------------------------------------------------------- AC-A4 period binding


def test_the_period_binds_to_the_chosen_date_basis(db, definition):
    result = _run(db, definition)
    numbers = [row["order_no"] for row in result.layouts.detail.rows]
    assert numbers == ["Z-001", "Z-002", "Z-003", "Z-004", "Z-005"]
    assert "Z-006" not in numbers  # 2025 on both bases


def test_switching_the_date_basis_moves_a_row_between_months(db, definition):
    booked = _run(db, definition)
    shipped = _run(db, definition, {"date_basis": "shipped_on"})

    def month_of(result, order_no):
        return next(r["month"] for r in result.layouts.detail.rows if r["order_no"] == order_no)

    assert month_of(booked, "Z-001") == "2026-01"
    assert month_of(shipped, "Z-001") == "2026-02"


def test_a_month_range_period_excludes_the_months_outside_it(db, definition):
    result = _run(
        db,
        definition,
        {"period": {"kind": "month_range", "year": 2026, "from_month": 1, "to_month": 2}},
    )
    assert [r["order_no"] for r in result.layouts.detail.rows] == ["Z-001", "Z-002"]
    assert result.period_label == "Jan'26 to Feb'26"


def test_a_custom_period_includes_its_last_day(db, definition):
    result = _run(
        db,
        definition,
        {"period": {"kind": "custom", "from": "2026-01-10", "to": "2026-01-10"}},
    )
    assert [r["order_no"] for r in result.layouts.detail.rows] == ["Z-001"]


def test_a_custom_period_is_labelled_the_way_the_crm_writes_dates(db, definition):
    """The CRM writes DD/MM/YYYY everywhere. An ISO label leaked into the title block and
    into the SUMMARY's own closing line: "GRAND TOTAL AMOUNT 2026-01-15 TO 2026-03-10"."""
    from app.services.reports import engine

    period = engine.resolve_period({"kind": "custom", "from": "2026-01-15", "to": "2026-03-10"})

    assert period.label == "15/01/2026 to 10/03/2026"
    assert period.compact_label == "15/01/2026 TO 10/03/2026"


def test_a_year_outside_the_calendar_is_a_422_rather_than_a_500(db, definition):
    """`date(0, 1, 1)` raises ValueError and `year + 1` on 9999 overflows, so a typed or
    fuzzed year came back as a server error instead of an answer about the field."""
    from fastapi import HTTPException

    for period in (
        {"kind": "year", "year": 0},
        {"kind": "year", "year": 10000},
        {"kind": "month_range", "year": 99999, "from_month": 1, "to_month": 2},
    ):
        with pytest.raises(HTTPException) as excinfo:
            _run(db, definition, {"period": period})
        assert excinfo.value.status_code == 422
        assert "year" in str(excinfo.value.detail).lower()


def test_a_select_param_filters_the_rows(db, definition):
    result = _run(db, definition, {"agent": ["Bob"]})
    assert [r["order_no"] for r in result.layouts.detail.rows] == ["Z-004", "Z-005"]


def test_an_empty_multi_select_means_no_filter(db, definition):
    assert _run(db, definition, {"agent": []}).row_count == 5


# ------------------------------------------------------------------- AC-A5 detail


def test_detail_returns_the_requested_columns_in_the_requested_order(db, definition):
    from app.schemas.report import ReportViewConfig

    view = ReportViewConfig(
        params={},
        detail={"columns": ["agent", "order_no", "amount"], "order": []},
        pivot={"rows": "agent", "cols": "month", "measures": ["amount"]},
    )
    result = _run(db, definition, view=view)
    assert [c.key for c in result.layouts.detail.columns] == ["agent", "order_no", "amount"]


def test_an_empty_column_list_means_the_whole_catalog(db, definition):
    result = _run(db, definition)
    keys = [c.key for c in result.layouts.detail.columns]
    # Every catalog column except the tick-group source, which is replaced in place.
    assert "delivery_year" not in keys
    for column in definition.dataset.columns:
        if column.key != "delivery_year":
            assert column.key in keys


def test_money_is_a_decimal_string_with_two_places(db, definition):
    row = next(r for r in _run(db, definition).layouts.detail.rows if r["order_no"] == "Z-002")
    assert row["amount"] == "250.25"
    assert _run(db, definition).layouts.detail.rows[0]["amount"] == "1000.00"


def test_a_blank_measure_stays_blank_and_never_becomes_zero(db, definition):
    row = next(r for r in _run(db, definition).layouts.detail.rows if r["order_no"] == "Z-002")
    assert row["fee"] is None


def test_detail_totals_cover_every_measure_among_the_columns(db, definition):
    totals = _run(db, definition).layouts.detail.totals
    assert totals["amount"] == "1750.24"  # 1000.00 + 250.25 + 400.00 + 99.99
    assert totals["fee"] == "36.51"  # 10.50 + 20.00 + 5.00 + 1.01


def test_a_measure_that_is_not_requested_has_no_total(db, definition):
    from app.schemas.report import ReportViewConfig

    view = ReportViewConfig(
        params={},
        detail={"columns": ["order_no", "amount"], "order": []},
        pivot={"rows": "agent", "cols": "month", "measures": ["amount"]},
    )
    totals = _run(db, definition, view=view).layouts.detail.totals
    assert "fee" not in totals


def test_a_measure_with_nothing_to_total_is_absent(db, definition):
    """Z-004 alone has no amount at all, so Amount must not total to 0.00."""
    result = _run(db, definition, {"agent": ["Bob"], "region": ["South"]})
    assert [r["order_no"] for r in result.layouts.detail.rows] == ["Z-004"]
    assert "amount" not in result.layouts.detail.totals


def test_the_tick_group_renders_one_column_per_value_present(db, definition):
    detail = _run(db, definition).layouts.detail
    group = detail.column_groups[0]
    assert group.label == "Delivery year"
    assert group.source == "delivery_year"
    assert group.keys == ["delivery_year__2026", "delivery_year__2027"]
    row = next(r for r in detail.rows if r["order_no"] == "Z-003")
    assert row["delivery_year__2027"] is True
    assert row["delivery_year__2026"] is False


def test_a_row_count_accompanies_the_result(db, definition):
    assert _run(db, definition).row_count == 5


# -------------------------------------------------------------------- AC-A6 pivot


def test_the_pivot_groups_by_the_chosen_dimensions(db, definition):
    pivot = _run(db, definition).layouts.summary
    assert pivot.row_dim.key == "agent"
    assert pivot.col_dim.key == "month"
    assert pivot.row_values == ["Alice", "Bob"]
    assert pivot.cells["Alice"]["2026-01"]["amount"] == "1250.25"
    assert pivot.cells["Alice"]["2026-03"]["amount"] == "400.00"


def test_a_missing_cell_is_absent_not_zero(db, definition):
    pivot = _run(db, definition).layouts.summary
    assert "2026-02" not in pivot.cells["Alice"]
    assert "2026-01" not in pivot.cells.get("Bob", {})


def test_a_cell_whose_measure_is_all_blank_omits_that_measure(db, definition):
    """Z-002's fee is blank, so Jan carries an amount but a fee of 10.50 only."""
    pivot = _run(db, definition).layouts.summary
    assert pivot.cells["Alice"]["2026-01"]["fee"] == "10.50"
    assert "amount" not in pivot.cells["Bob"]["2026-03"]


def test_column_values_are_ordered_naturally(db, definition):
    pivot = _run(db, definition).layouts.summary
    assert pivot.col_dim.values == [f"2026-{m:02d}" for m in range(1, 13)]
    assert pivot.col_dim.value_labels["2026-01"] == "Jan'26"


def test_row_column_and_grand_totals_are_computed_by_the_engine(db, definition):
    pivot = _run(db, definition).layouts.summary
    assert pivot.row_totals["Alice"]["amount"] == "1650.25"
    assert pivot.row_totals["Bob"]["amount"] == "99.99"
    assert pivot.col_totals["2026-03"]["amount"] == "400.00"
    assert pivot.col_totals["2026-03"]["fee"] == "25.00"
    assert pivot.grand_total["amount"] == "1750.24"
    assert pivot.grand_total["fee"] == "36.51"


def test_the_grand_total_equals_the_detail_total(db, definition):
    result = _run(db, definition)
    assert result.layouts.summary.grand_total["amount"] == result.layouts.detail.totals["amount"]


def _pivot_view(rows: str, cols: str, measures):
    from app.schemas.report import ReportViewConfig

    return ReportViewConfig(
        params={},
        detail={"columns": [], "order": []},
        pivot={"rows": rows, "cols": cols, "measures": list(measures)},
    )


def _blank_row(db, order_no: str = "Z-007") -> None:
    """A row with NO agent and NO region: the money the pivot used to lose."""
    db.execute(
        sa.text(
            f"""
            INSERT INTO {TABLE}
                (order_no, agent, region, booked_on, shipped_on, amount, fee)
            VALUES (:o, NULL, NULL, '2026-02-14', '2026-02-20', 77.00, 3.00)
            """
        ),
        {"o": order_no},
    )


def test_a_blank_dimension_value_is_bucketed_not_dropped(db, definition):
    """The pivot used to `continue` past a blank row or column value, so the Summary
    grand total silently disagreed with the Detail total. Every blank now falls in one
    named bucket and the two totals are equal again."""
    from app.services.reports import engine

    _blank_row(db)
    view = _pivot_view("agent", "region", ["amount", "fee"])
    result = _run(db, definition, {"region": []}, view=view)
    pivot = result.layouts.summary

    assert engine.BLANK_VALUE in pivot.row_values
    assert engine.BLANK_VALUE in pivot.col_dim.values
    assert pivot.cells[engine.BLANK_VALUE][engine.BLANK_VALUE]["amount"] == "77.00"
    assert pivot.grand_total["amount"] == result.layouts.detail.totals["amount"] == "1827.24"
    assert pivot.grand_total["fee"] == result.layouts.detail.totals["fee"] == "39.51"


def test_the_blank_bucket_sorts_last_on_both_axes(db, definition):
    from app.services.reports import engine

    _blank_row(db)
    pivot = _run(
        db, definition, {"region": []}, view=_pivot_view("agent", "region", ["amount"])
    ).layouts.summary

    assert pivot.row_values[-1] == engine.BLANK_VALUE
    assert pivot.col_dim.values[-1] == engine.BLANK_VALUE
    assert pivot.row_values[:-1] == ["Alice", "Bob"]


def test_dimension_values_are_ranked_numerically_not_lexically(db, definition):
    """"Agent 2" before "Agent 10". Plain lexical ordering puts 10 first, which reads as a
    sorting bug on any dimension whose values carry a number."""
    for index, agent in ((1, "Agent 10"), (2, "Agent 2")):
        db.execute(
            sa.text(
                f"""
                INSERT INTO {TABLE}
                    (order_no, agent, region, booked_on, shipped_on, amount, fee)
                VALUES (:o, :a, 'North', '2026-05-0{index}', '2026-05-09', 10.00, 1.00)
                """
            ),
            {"o": f"Z-01{index}", "a": agent},
        )
    pivot = _run(
        db, definition, view=_pivot_view("agent", "region", ["amount"])
    ).layouts.summary

    assert pivot.row_values == ["Agent 2", "Agent 10", "Alice", "Bob"]


def test_a_different_pivot_configuration_regroups_the_same_rows(db, definition):
    from app.schemas.report import ReportViewConfig

    view = ReportViewConfig(
        params={},
        detail={"columns": [], "order": []},
        pivot={"rows": "region", "cols": "agent", "measures": ["fee"]},
    )
    pivot = _run(db, definition, view=view).layouts.summary
    assert pivot.row_values == ["North", "South"]
    assert pivot.col_dim.values == ["Alice", "Bob"]
    assert pivot.cells["South"]["Bob"]["fee"] == "5.00"
    assert pivot.col_dim.value_labels is None


# --------------------------------------------------------------------- AC-A7 caps


def test_a_detail_run_over_the_cap_answers_422_capped(db, definition, monkeypatch):
    from app.services.reports import engine

    monkeypatch.setattr(engine, "DETAIL_ROW_CAP", 2)
    with pytest.raises(engine.ReportCapped) as excinfo:
        _run(db, definition)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["capped"] is True
    assert "export" in excinfo.value.detail["message"].lower()


def test_a_pivot_over_the_cell_cap_answers_422_capped(db, definition, monkeypatch):
    from app.services.reports import engine

    monkeypatch.setattr(engine, "PIVOT_CELL_CAP", 3)
    with pytest.raises(engine.ReportCapped):
        _run(db, definition)


def test_the_export_path_has_no_cap(db, definition, monkeypatch):
    from app.services.reports import engine

    monkeypatch.setattr(engine, "DETAIL_ROW_CAP", 2)
    monkeypatch.setattr(engine, "PIVOT_CELL_CAP", 3)
    result = _run(db, definition, cap=False)
    assert result.row_count == 5


# ------------------------------------------------------------------ company scope


def test_a_company_scoped_dataset_filters_by_the_session_scope(db):
    from app.services.company_scope import set_company_scope

    db.execute(sa.text(f"UPDATE {TABLE} SET company_id = 'zzt-co-a' WHERE agent = 'Alice'"))
    db.execute(sa.text(f"UPDATE {TABLE} SET company_id = 'zzt-co-b' WHERE agent <> 'Alice'"))
    definition = fixture.definition(fixture.dataset(scope="company"))

    set_company_scope(db, frozenset({"zzt-co-a"}))
    try:
        result = _run(db, definition)
        assert {r["agent"] for r in result.layouts.detail.rows} == {"Alice"}
    finally:
        set_company_scope(db, None)


def test_an_unscoped_dataset_ignores_the_session_scope(db, definition):
    from app.services.company_scope import set_company_scope

    db.execute(sa.text(f"UPDATE {TABLE} SET company_id = 'zzt-co-b'"))
    set_company_scope(db, frozenset({"zzt-co-a"}))
    try:
        assert _run(db, definition).row_count == 5
    finally:
        set_company_scope(db, None)


# ----------------------------------------------------------------------- registry


def test_a_definition_can_be_registered_and_fetched_by_key(definition):
    from app.services.reports import registry as reg

    reg.register(definition)
    try:
        assert reg.get("zzt_orders") is definition
        assert definition in reg.all_definitions()
    finally:
        reg._REGISTRY.pop("zzt_orders", None)


def test_an_unknown_key_is_not_registered(definition):
    from app.services.reports import registry as reg

    assert reg.get("zzt_nothing_here") is None


def test_a_default_view_naming_a_measure_as_a_dimension_is_refused(definition):
    from dataclasses import replace

    from app.services.reports import registry as reg

    broken = replace(
        definition,
        default_view={
            **definition.default_view,
            "pivot": {"rows": "amount", "cols": "month", "measures": ["amount"]},
        },
    )
    with pytest.raises(ValueError):
        reg.register(broken)
    reg._REGISTRY.pop("zzt_orders", None)


def test_a_definition_whose_default_period_is_nonsense_is_refused_at_import(definition):
    """A bad default period used to surface as a 422 on every page open, which reads as a
    broken screen rather than a broken definition."""
    from dataclasses import replace

    from app.services.reports import registry as reg

    params = tuple(
        reg.PeriodParam(key=p.key, label=p.label, default={"kind": "fortnight"})
        if isinstance(p, reg.PeriodParam)
        else p
        for p in definition.params
    )
    with pytest.raises(ValueError) as excinfo:
        reg.register(replace(definition, params=params))
    assert "period" in str(excinfo.value).lower()
    reg._REGISTRY.pop("zzt_orders", None)


def test_a_callable_period_default_is_resolved_when_it_is_asked_for(definition):
    """"This year" has to mean the year the USER is in, not the year the process booted."""
    from dataclasses import replace

    from app.services.reports import engine
    from app.services.reports import registry as reg

    years = iter([2031, 2032])
    params = tuple(
        reg.PeriodParam(key=p.key, label=p.label, default=lambda: {"kind": "year", "year": next(years)})
        if isinstance(p, reg.PeriodParam)
        else p
        for p in definition.params
    )
    # The default view names no period, so it falls through to the param's own default.
    with_callable = replace(
        definition,
        params=params,
        default_view={
            **definition.default_view,
            "params": {
                k: v for k, v in definition.default_view["params"].items() if k != "period"
            },
        },
    )

    assert engine.view_config(with_callable).params["period"] == {"kind": "year", "year": 2031}
    assert engine.view_config(with_callable).params["period"] == {"kind": "year", "year": 2032}


def test_the_engine_totals_with_decimals_not_floats(db, definition):
    """0.1 + 0.2 in float prints 0.30000000000000004; the workbook must not."""
    db.execute(sa.text(f"UPDATE {TABLE} SET amount = 0.10 WHERE order_no = 'Z-001'"))
    db.execute(sa.text(f"UPDATE {TABLE} SET amount = 0.20 WHERE order_no = 'Z-002'"))
    db.execute(sa.text(f"DELETE FROM {TABLE} WHERE order_no IN ('Z-003', 'Z-004', 'Z-005')"))
    totals = _run(db, definition).layouts.detail.totals
    assert totals["amount"] == "0.30"
    assert Decimal(totals["amount"]) == Decimal("0.30")


# ------------------------------------------------------------------ AC-G1 / AC-G3


def test_a_single_month_period_is_labelled_as_that_month(db, definition):
    """AC-G1. A month chip is a month_range of one month; "Jan'26 to Jan'26" reads as a
    range the user did not ask for."""
    result = _run(
        db,
        definition,
        {"period": {"kind": "month_range", "year": 2026, "from_month": 1, "to_month": 1}},
    )
    assert result.period_label == "Jan'26"
    assert [r["order_no"] for r in result.layouts.detail.rows] == ["Z-001", "Z-002"]


def test_a_single_month_workbook_is_the_summary_and_that_one_sheet(db, definition):
    """AC-G1. A month chip exports what the client's own monthly tab is: SUMMARY over the
    one month, then that month's table. Twelve sheets for a month the user narrowed to
    would hand back the year they had just filtered away."""
    from app.services.reports import engine

    params = dict(definition.default_view["params"])
    params["period"] = {"kind": "month_range", "year": 2026, "from_month": 1, "to_month": 1}
    data = engine.run_workbook(db, definition, params)

    assert [sheet.name for sheet in data.sheets] == ["JAN'26"]
    assert data.period_label == "Jan'26"
    assert data.summary.col_dim.values == ["2026-01"]


def _fixed_year_definition():
    """The scratch report with a FIXED four-year tick group instead of a derived one."""
    from dataclasses import replace

    from app.services.reports import registry as reg

    base = fixture.definition()
    return replace(
        base,
        detail=replace(
            base.detail,
            groups=(
                reg.TickGroup(
                    source="delivery_year",
                    label="Delivery year",
                    members=reg.period_year_span(4),
                ),
            ),
        ),
    )


def test_a_fixed_tick_group_renders_four_years_from_the_period(db):
    """AC-G3. The workbook's own band: the period's year and the three after it, whatever
    the data holds, so an empty year is a visible empty column rather than a missing one."""
    definition = _fixed_year_definition()
    detail = _run(db, definition).layouts.detail
    group = detail.column_groups[0]

    assert group.source == "delivery_year"
    assert group.keys == [f"delivery_year_{index}" for index in range(1, 5)]
    labels = [c.label for c in detail.columns if c.key in group.keys]
    assert labels == ["2026", "2027", "2028", "2029"]

    row = next(r for r in detail.rows if r["order_no"] == "Z-003")  # delivery year 2027
    assert row["delivery_year_2"] is True
    assert row["delivery_year_1"] is False
    assert row["delivery_year_4"] is False


def test_a_fixed_tick_groups_column_ids_survive_a_change_of_period(db):
    """AC-G3. A derived id (`delivery_year__2026`) outlives the result it came from: the
    user's saved column ORDER still names it after the period moves, and the grid logs
    "Column with id ... does not exist" on every render. A stable id cannot go stale."""
    definition = _fixed_year_definition()

    keys_2026 = _run(db, definition).layouts.detail.column_groups[0].keys
    later = _run(db, definition, {"period": {"kind": "year", "year": 2027}})
    group = later.layouts.detail.column_groups[0]

    assert group.keys == keys_2026
    labels = [c.label for c in later.layouts.detail.columns if c.key in group.keys]
    assert labels == ["2027", "2028", "2029", "2030"]


# --------------------------------------------------------------------------- integer measures


@pytest.mark.parametrize(
    "raw, shown",
    [("2.5", "3"), ("2.4", "2"), ("-2.5", "-3"), ("-2.4", "-2"), ("0", "0"), ("11", "11"), (Decimal("3.5000"), "4")],
)
def test_an_integer_measure_rounds_half_up_and_never_truncates(raw, shown):
    from app.services.reports import engine
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    column = reg.Column("qty", "Qty", "integer", "measure", lambda c: t.c.amount)
    assert engine._measure_value(column, raw) == shown


def test_a_money_measure_keeps_its_sen():
    from app.services.reports import engine
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    column = reg.Column("amt", "Amount", "money", "measure", lambda c: t.c.amount)
    assert engine._measure_value(column, "2.5") == "2.50"
