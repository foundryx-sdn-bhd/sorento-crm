"""Report #1: the sponsorship register, the workbook the project-sales team keeps by hand.

The whole report is this file plus its dataset. There is no route, no schema, no frontend
page and no Excel code specific to it - which is the claim the foundation was built to make
(PLAN-reporting-foundation, "Why a foundation and not a page").

The defaults are the workbook's own shape, decided with the user on 2026-08-26:

- month bucket = `approved_at`, and the basis is a filter, because a form approved in July
  for a June enquiry belongs in July's takings;
- statuses = approved + processed by CS, because a draft is not a sponsorship;
- eight detail columns, the OTHERS column dropped (empty in all twelve 2025 sheets);
- the summary is salesman by month on both money columns, which is the pivot the team
  reads first.
"""
from __future__ import annotations

from app.services.reports import registry as reg
from app.services.reports.datasets import sponsorship_forms as ds

KEY = "sponsorship"
PERMISSION = "procurement.sponsorship_forms.report"

#: The eight columns of the client's monthly table, in the client's order (AC-B7). The rest
#: of the catalog is hidden, not absent: the Columns panel still offers every one of them.
DEFAULT_DETAIL_COLUMNS = [
    "request_number",
    "sales_agent",
    "customer_name",
    "project_title",
    "sponsor_subject",
    "project_value",
    "sample_price",
    "expected_delivery_year",
]


REPORT = reg.register(
    reg.ReportDefinition(
        key=KEY,
        title="Sponsorship report",
        permission=PERMISSION,
        module_key="procurement",
        dataset=ds.DATASET,
        params=(
            reg.DateBasisParam(key="date_basis", label="Date basis", default="approved_at"),
            reg.PeriodParam(
                key="period",
                label="Period",
                # Resolved per request: the year in front of the USER, not a year baked
                # into a release and not the year this process happened to boot in.
                default=reg.current_year_period,
            ),
            reg.SelectParam(
                key="sales_agent",
                label="Sales agent",
                multi=True,
                default=(),  # empty = every agent
                options=ds.sales_agent_options,
                condition=ds.sales_agent_condition,
            ),
            reg.SelectParam(
                key="status",
                label="Status",
                multi=True,
                default=ds.DEFAULT_STATUSES,
                options=ds.status_options,
                condition=ds.status_condition,
            ),
        ),
        detail=reg.DetailLayout(
            title="Sponsorships",
            order_by=ds.order_by,
            groups=(
                reg.TickGroup(
                    source="expected_delivery_year",
                    label="Expected year of delivery",
                    # The client's own band: 2025, 2026, 2027, 2028 on a 2025 sheet. Fixed
                    # rather than derived, so an empty year still prints a column and the
                    # ids never go stale when the period moves (AC-G3).
                    members=reg.period_year_span(4),
                ),
            ),
        ),
        pivot=reg.PivotLayout(title="Summary by salesman"),
        default_view={
            "params": {
                "date_basis": "approved_at",
                # `period` is deliberately absent: the engine fills it from the param's
                # own default, which is resolved per request.
                "sales_agent": [],
                "status": list(ds.DEFAULT_STATUSES),
            },
            "detail": {
                "columns": list(DEFAULT_DETAIL_COLUMNS),
                "order": list(DEFAULT_DETAIL_COLUMNS),
            },
            "pivot": {
                "rows": "sales_agent",
                "cols": "month",
                "measures": ["project_value", "sample_price"],
            },
        },
        # The workbook is READ BESIDE the register the team keeps by hand, so it carries
        # their words, their widths and their two total labels - the client's own spelling
        # of SPONSHER PROJECT included (AC-G7 to AC-G10). Anything not named here is the
        # column's label uppercased, which is what report #2 will get for free.
        workbook=reg.WorkbookSpec(
            company_name="SORENTO SDN BHD",
            department="PROJECT SALES",
            report_title="SPONSORSHIP",
            headers={
                "request_number": "PS NO:",
                "customer_name": "CUSTOMER NAME",
                "sponsor_subject": "SPONSHER PROJECT",
            },
            # The client's own column widths, to the character.
            column_widths={
                "request_number": 15.7,
                "sales_agent": 17.1,
                "customer_name": 28.3,
                "project_title": 30.4,
                "sponsor_subject": 30.4,
                "project_value": 19.6,
                "sample_price": 23.3,
                "expected_delivery_year": 9.5,
                "purpose": 11.7,
            },
            summary_row_total_label="TOTAL VALUE (BY SALESMAN)",
            summary_total_row_label="TOTAL SALES",
        ),
    )
)
