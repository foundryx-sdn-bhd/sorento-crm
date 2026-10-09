"""Report: the Chat overview, how the chatbot answered (chat analytics CA-1).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md. The whole report is
this file plus two datasets: no route, no schema and no page of its own.

- Opens on outcome by week, counting asks and the ones that failed (technical, cannot do,
  missed), as a line chart.
- The office is out by default (a salesman testing the bot is not a customer), and so is noise (a
  greeting is not an ask); both are filters, so either can be brought back.
- "Count by" swaps the row set: asks (one row per ask) or products (one row per product an
  ask named or was shown), the report's one dataset switch, like the Yearly comparison's basis.
- The period opens on 5 Sep 2026, the first day the bot's traces exist; the note under the
  filter bar says so.
"""
from __future__ import annotations

from app.services.reports import registry as reg
from app.services.reports.datasets import chat_ask_items as items_ds
from app.services.reports.datasets import chat_asks as ds

KEY = "chat_overview"
PERMISSION = "chatbot.analytics.view"
MODULE_KEY = "chatbot"

#: What "Count by" offers. The first is the default and `definition.dataset`.
COUNT_BY: tuple = (("asks", "Asks"), ("products", "Products"))

#: Columns the detail table opens on. A key the run's dataset lacks is left out of that run,
#: so the ask grain skips the product ones and the item grain the failure ones.
DEFAULT_DETAIL_COLUMNS = [
    "asked_at",
    "outcome",
    "contact_type",
    "audience",
    "domain",
    "customer",
    "branch_kind",
    "failure_reason",
    "rows_offered",
    "reply_seconds",
    "tools",
    "product",
    "named",
    "shown",
]

#: A split message counts once here: these are turn measures (Top asks counts every ask).
TURNS = ds.turn_only(ds.DATASET)
ITEM_TURNS = ds.turn_only(items_ds.DATASET)


REPORT = reg.register(
    reg.ReportDefinition(
        key=KEY,
        title="Chat overview",
        permission=PERMISSION,
        module_key=MODULE_KEY,
        dataset=TURNS,
        params=(
            reg.DateBasisParam(key="date_basis", label="Date basis", default="asked_at"),
            reg.PeriodParam(key="period", label="Period", default=ds.default_period),
            reg.SelectParam(
                key="contact_type",
                label="Contact type",
                multi=True,
                default=ds.DEFAULT_CONTACT_TYPES,  # everyone but the office
                options=ds.contact_type_options,
                condition=ds.contact_type_condition,
            ),
            reg.SelectParam(
                key="outcome",
                label="Outcome",
                multi=True,
                default=ds.DEFAULT_OUTCOMES,  # every outcome but noise
                options=ds.outcome_options,
                condition=ds.outcome_condition,
            ),
            reg.SelectParam(
                key="count_by",
                label="Count by",
                multi=False,
                default=(COUNT_BY[0][0],),
                options=lambda db: list(COUNT_BY),
                condition=lambda ctx, values: None,  # picks the dataset, filters nothing
                clearable=False,
            ),
        ),
        detail=reg.DetailLayout(title="Asks", order_by=ds.order_by, cap="truncate"),
        pivot=reg.PivotLayout(title="Outcome by week", chart="line"),
        default_view={
            "params": {
                "date_basis": "asked_at",
                "contact_type": list(ds.DEFAULT_CONTACT_TYPES),
                "outcome": list(ds.DEFAULT_OUTCOMES),
                "count_by": [COUNT_BY[0][0]],
            },
            "detail": {
                "columns": list(DEFAULT_DETAIL_COLUMNS),
                "order": list(DEFAULT_DETAIL_COLUMNS),
            },
            "pivot": {"rows": "outcome", "cols": "week", "measures": ["asks", "failed"]},
        },
        note=lambda ctx: ds.COVERAGE_NOTE,
        opens_on="summary",
        datasets_by=("count_by", {"products": ITEM_TURNS}),
        workbook=reg.WorkbookSpec(
            company_name="SORENTO SDN BHD",
            report_title="CHAT OVERVIEW",
            headers={"outcome": "OUTCOME", "week": "WEEK"},
            month_sheets=False,
        ),
    )
)
