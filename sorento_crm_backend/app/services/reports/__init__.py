"""The reporting foundation: one kernel, one report definition per report.

`registry` says what a report IS, `engine` runs it, `xlsx_renderer` writes the workbook and
`views_service` owns saved views. Report definitions (the sponsorship report is #1, S3) live
in `definitions/` and are imported here so the registry is populated wherever it is read.
"""

# Report definitions register themselves on import. Imported HERE (not in the routes) so
# the registry is populated wherever it is read - route, RQ task or test.
from app.services.reports.definitions import sponsorship  # noqa: E402,F401
from app.services.reports.definitions import sales_yearly  # noqa: E402,F401
from app.services.reports.definitions import chat_overview  # noqa: E402,F401
from app.services.reports.definitions import chat_top_asks  # noqa: E402,F401
from app.services.reports.definitions import chat_bot_failures  # noqa: E402,F401
