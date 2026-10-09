"""Report: Bot failures, where the chatbot let customers down (chat analytics CA-2).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Top asks", last
bullet. The Top asks ask dataset with the outcome filter opening on the failed outcomes
(technical, cannot do, missed), failure reason by topic. Choosing other outcomes brings those
asks back.
"""
from __future__ import annotations

from app.services.reports import registry as reg
from app.services.reports.datasets import chat_asks as ds
from app.services.reports.definitions import chat_top_asks as top

KEY = "chat_bot_failures"
PERMISSION = "chatbot.analytics.view"
MODULE_KEY = "chatbot"

FAILED_OUTCOMES = ("technical", "cannot_do", "missed")

DEFAULT_DETAIL_COLUMNS = ["asked_at", "subject", "topic", "failure_reason", "outcome", "customer"]


def _params() -> tuple:
    out = []
    for param in top.params():
        if param.key in ("count_by", "products"):
            continue
        if param.key == "outcome":
            param = reg.SelectParam(
                key=param.key,
                label=param.label,
                multi=True,
                default=FAILED_OUTCOMES,
                options=param.options,
                condition=param.condition,
            )
        out.append(param)
    return tuple(out)


REPORT = reg.register(
    reg.ReportDefinition(
        key=KEY,
        title="Bot failures",
        permission=PERMISSION,
        module_key=MODULE_KEY,
        dataset=ds.DATASET,
        params=_params(),
        detail=reg.DetailLayout(title="Asks", order_by=ds.order_by, cap="truncate"),
        pivot=reg.PivotLayout(title="Failure reason by topic"),
        default_view={
            "params": {
                "date_basis": "asked_at",
                "contact_type": list(ds.DEFAULT_CONTACT_TYPES),
                "outcome": list(FAILED_OUTCOMES),
            },
            "detail": {"columns": list(DEFAULT_DETAIL_COLUMNS), "order": list(DEFAULT_DETAIL_COLUMNS)},
            "pivot": {"rows": "failure_reason", "cols": "topic", "measures": ["asks"]},
        },
        note=lambda ctx: ds.topics_note(ctx),
        opens_on="summary",
        workbook=reg.WorkbookSpec(
            company_name="SORENTO SDN BHD",
            report_title="BOT FAILURES",
            headers={"failure_reason": "FAILURE REASON", "topic": "TOPIC"},
            month_sheets=False,
        ),
    )
)
