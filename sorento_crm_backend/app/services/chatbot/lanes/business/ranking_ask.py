"""The one place that picks the next clarify question for a ranking (owner ruling, 4 Oct 2026).

Both ranking lanes, top selling (`lanes/business._top_selling_question`) and the sales ranking
(`report_ask`), ask the metric first and then the basis, and neither defaults either. Each lane
keeps its own binding (what counts as settled, where the answer lands); this decides only which
line comes next. No imports: both lanes import it.
"""
from __future__ import annotations

METRIC_LINE = "By quantity or by amount?"
#: Top selling has two bases (an outstanding top selling is the outstanding report, a hop).
TOP_SELLING_BASIS_LINE = "Delivered (transferred to DO) or ordered?"
#: The sales ranking (`crm_report_ask`) has all three.
RANKING_BASIS_LINE = "Delivered, ordered or outstanding?"


def next_question(*, metric_settled: bool, basis_settled: bool, basis_line: str) -> tuple[str, str] | None:
    """`(axis, line)` for the first unsettled one of the metric then the basis; None when both
    are settled. `basis_line` is the lane's own basis wording."""
    if not metric_settled:
        return "metric", METRIC_LINE
    if not basis_settled:
        return "basis", basis_line
    return None
