"""Live achievement of target periods, counted from sales orders at read time (plan 3.2, 16.2).

Nothing is stored: sales orders keep arriving and changing from AutoCount, so a stored figure
would go stale (trigger for a snapshot: plan 3.1). One query per screen covers every period
shown, whatever mix of subjects, metrics, bases and scopes they carry.

**Which lines count for a period.** `sales_order_lines` of a sales order that is not
cancelled, on a line that is not cancelled (the sales report's own predicate), with a non-null
`order_date` (a null date never counts, either basis), inside the target's product scope
(a category counts its sub-categories, through a recursive CTE over `parent_category_id`; a
brand counts every product whose `brand_id` is one of the target's brands), and
credited to the subject:

- **An agent** (G2, R2): the order's `sales_agent_id` is the agent or one of their label
  siblings, the visible agents sharing the agent's non-empty `person_label`.
- **A team** (T2, R2): a stay of a member in the team whose window covers the order's date
  and whose agent (widened to label siblings) is the order's agent. An order counts once for a
  team even when two members share a person label. The window always reads the SALES ORDER's
  date, also for DO-dated quantities.

Both come down to credit rows `(agent_id, valid_from, valid_to)` per period, built in Python
from the small agent and membership tables; periods with the same rows share one credit group,
and each (group, agent)'s windows are merged so an order date falls inside at most one.

**How much a line counts** (`achievement_value_expr`): ordered amount is `line_total`, ordered
quantity `qty_ordered`, both when the order date is in the period. Delivered goes through
`delivered_by_date`: the linked DO quantities dated in the period, counted in DO date order
and capped so a line never counts more than `qty_ordered` in total, plus the residual AutoCount
reports delivered that no linked DO explains, on the order date. Amount delivered is
`round(line_total x delivered / qty_ordered, 2)`, 0 when nothing was ordered, the sales
report's `confirmed_value` exactly while no DO is linked.

**Company.** Every read is an ORM statement run through the session, and the achievement query
also names the company explicitly on every table it reads (`sales_orders`, `sales_order_lines`,
`orders`, `order_lines`, `product_categories`, `sales.target_scope`): the scope listener's
criteria do not reach inside a CTE, so a DO line of another company linked to this company's
sales order line would otherwise count. On the order tables below `sales_orders` the rule is a
UUID comparison gating the values to 0 rather than a filter (see `achieved_by_period`).
`any_do_linked` applies the same rule, so the Counts label and the figures always agree.

**Shape (review round 2, B1; #1319).** The credited agents' lines are bounded before anything
joins them: by the agents and the date span of all the periods (plus, when a period counts by
DO date, the lines a DO dated inside that span delivers), so no line outside every period
reaches a join. Each period is expanded to its days once, and a line meets its periods by an
equality on (credit group, date), a hash join, never by a range test against every period or
every credit row. The DO lines are aggregated once: `do_counted` gives each linked DO line its
capped quantity; its per-(period, line) sum on the DO date and its per-line total are unioned
with the lines dated in each period and grouped into one row per (period, line), carrying the
period's own columns, so nothing joins the periods back. No correlated subquery reads the DO
lines. The product scope is resolved once, the category tree walked once, into a small
(target, scope, product) set a hashed IN reads; an all-products target reads none of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import (
    Date,
    Integer,
    String,
    and_,
    case,
    cast,
    column,
    func,
    literal,
    literal_column,
    not_,
    or_,
    select,
    true,
    tuple_,
    union_all,
    values,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Session

from app.models.order import DOC_TYPE_CONSIGNMENT, Order, OrderLine, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory
from app.models.sales import SalesTargetScope, SalesTeamMember
from app.models.sales_agent import SalesAgent

#: (agent_id, valid_from, valid_to): the orders of `agent_id` dated inside the window count.
Credit = Tuple[str, Optional[date], Optional[date]]

_UUID = UUID(as_uuid=False)


@dataclass
class PeriodSpec:
    """One target period to count, with the credit rows of its subject."""

    period_id: str
    target_id: str
    period_start: date
    period_end: date
    metric: str
    basis: str
    product_scope: str
    credits: List[Credit] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# credit rows: label siblings and dated team membership
# --------------------------------------------------------------------------------------


def label_siblings(db: Session, company_id: str, agent_ids: Iterable[str]) -> Dict[str, Set[str]]:
    """Each agent with the visible agents sharing its non-empty `person_label` (R2), else itself."""
    ids = set(agent_ids)
    if not ids:
        return {}
    visible = or_(SalesAgent.company_id.is_(None), SalesAgent.company_id == company_id)
    agents = db.query(SalesAgent).filter(SalesAgent.id.in_(ids), visible).all()
    labels = {(a.person_label or "").strip() for a in agents} - {""}
    by_label: Dict[str, Set[str]] = {}
    if labels:
        for sibling in db.query(SalesAgent).filter(SalesAgent.person_label.in_(labels), visible):
            by_label.setdefault(sibling.person_label.strip(), set()).add(sibling.id)
    out: Dict[str, Set[str]] = {agent_id: {agent_id} for agent_id in ids}
    for agent in agents:
        label = (agent.person_label or "").strip()
        if label:
            out[agent.id] |= by_label.get(label, set())
    return out


def agent_credits(db: Session, company_id: str, agent_ids: Iterable[str]) -> Dict[str, List[Credit]]:
    """An agent target counts every order of the agent and their label siblings, any date."""
    return {
        agent_id: [(sibling, None, None) for sibling in sorted(siblings)]
        for agent_id, siblings in label_siblings(db, company_id, agent_ids).items()
    }


def team_credits(db: Session, company_id: str, team_ids: Iterable[str]) -> Dict[str, List[Credit]]:
    """A team counts each member's orders (label siblings included) inside each stay (T2)."""
    ids = set(team_ids)
    out: Dict[str, List[Credit]] = {team_id: [] for team_id in ids}
    if not ids:
        return out
    stays = db.query(SalesTeamMember).filter(SalesTeamMember.sales_team_id.in_(ids)).all()
    siblings = label_siblings(db, company_id, {s.sales_agent_id for s in stays})
    for stay in stays:
        for agent_id in sorted(siblings.get(stay.sales_agent_id, {stay.sales_agent_id})):
            out[stay.sales_team_id].append((agent_id, stay.valid_from, stay.valid_to))
    return out


# --------------------------------------------------------------------------------------
# the value of one line in one period
# --------------------------------------------------------------------------------------


def achievement_value_expr(metric: str, basis: str, delivered_qty, line_total=None, qty_ordered=None):
    """What one sales order line adds to a period, by metric and basis (3.2).

    Written once and pinned against `sales_report_service`'s `confirmed_value` by
    `test_value_expr_pinned_to_sales_report`. The ordered cells are gated on the order date
    by the caller; `delivered_qty` already carries its own dating (`delivered_by_date`).
    `line_total` and `qty_ordered` default to the `sales_order_lines` columns; the achievement
    query passes the same columns as carried through its steps.
    """
    line_total = func.coalesce(SalesOrderLine.line_total if line_total is None else line_total, 0)
    qty_ordered = SalesOrderLine.qty_ordered if qty_ordered is None else qty_ordered
    if basis == "ordered":
        return line_total if metric == "amount" else qty_ordered
    if metric == "quantity":
        return delivered_qty
    return func.coalesce(func.round(line_total * delivered_qty / func.nullif(qty_ordered, 0), 2), 0)


# --------------------------------------------------------------------------------------
# the query
# --------------------------------------------------------------------------------------


def _credit_groups(specs: List[PeriodSpec]) -> Tuple[Dict[str, int], List[Tuple[int, str, date, date]]]:
    """Each period's credit group, and `(group, agent_id, valid_from, valid_to)` per group.

    Periods with the same credit rows (every period of one target: they share its subject)
    share one group, so a line meets one credit row per subject crediting its agent, not one
    per period. Each (group, agent)'s windows are merged: two stays of label siblings can give
    one agent overlapping windows, and merged, an order date falls inside at most one row, so
    joining on the credit never counts a line twice (the EXISTS it replaces had the same
    meaning). An open end is the earliest or latest date, never NULL: a NULL in VALUES is
    untyped text to Postgres, and `text <= date` does not exist.
    """
    group_of: Dict[str, int] = {}
    groups: Dict[Tuple[Credit, ...], int] = {}
    for s in specs:
        key = tuple(
            sorted(
                {(a, f or date.min, t or date.max) for a, f, t in s.credits},
            )
        )
        group_of[s.period_id] = groups.setdefault(key, len(groups))
    rows = []
    for key, group in groups.items():
        by_agent: Dict[str, List[Tuple[date, date]]] = {}
        for agent_id, valid_from, valid_to in key:
            by_agent.setdefault(agent_id, []).append((valid_from, valid_to))
        for agent_id, spans in by_agent.items():
            spans.sort()
            start, end = spans[0]
            for next_start, next_end in spans[1:]:
                if next_start <= end:
                    end = max(end, next_end)
                else:
                    rows.append((group, agent_id, start, end))
                    start, end = next_start, next_end
            rows.append((group, agent_id, start, end))
    return group_of, rows


def achieved_by_period(db: Session, specs: List[PeriodSpec], company_id: str) -> Dict[str, Decimal]:
    """`{period_id: achieved}` for every spec, one statement for all of them.

    Steps (review round 2, B1; #1319), each reading a small VALUES, one earlier step once, or an
    index: `period_days` (every day of every period), `agent_lines` (the credited agents' live
    lines dated inside the periods' span, or delivered by a DO dated inside it), `do_counted`
    (their linked DO lines with the capped quantity, and the line's columns), then one row per
    (period, line) from three sources grouped together, each meeting its periods on (credit
    group, date) through the credit rows of the line's agent: lines dated in the period, DO
    quantity dated in the period, and all linked DO quantity on the line's own period.
    """
    out: Dict[str, Decimal] = {s.period_id: Decimal("0") for s in specs}
    group_of, credit_rows = _credit_groups(specs)
    if not credit_rows:
        return out

    # Every day of every period, with the period's own columns: a line meets its periods by an
    # equality on the date (a hash join), never by a range test against every period, and the
    # period's metric, basis and scope ride along to the end, so nothing joins the periods back.
    pv = values(
        column("period_id", _UUID),
        column("target_id", _UUID),
        column("credit_group", Integer),
        column("pstart", Date),
        column("pend", Date),
        column("metric", String),
        column("basis", String),
        column("product_scope", String),
        name="pv",
    ).data(
        [
            (
                s.period_id, s.target_id, group_of[s.period_id], s.period_start, s.period_end,
                s.metric, s.basis, s.product_scope,
            )
            for s in specs
        ]
    )
    # A function in FROM sees the items before it (implicitly LATERAL). Plain timestamps, so
    # the series never runs in the session's time zone (a zone that skips a date would skip
    # that day).
    day = (
        func.generate_series(
            cast(pv.c.pstart, TIMESTAMP),
            cast(pv.c.pend, TIMESTAMP),
            literal_column("interval '1 day'"),
        )
        .table_valued("day")
        .render_derived(name="d")
    )
    period_days = (
        select(
            pv.c.period_id, pv.c.target_id, pv.c.credit_group, pv.c.metric, pv.c.basis,
            pv.c.product_scope, cast(day.c.day, Date).label("day"),
        )
        .select_from(pv)
        .join(day, true())
        # Built once and hashed, never re-run per line where a single use would be inlined.
        .cte("period_days")
        .prefix_with("MATERIALIZED")
    )

    def credits(name: str):
        # One VALUES per use: a VALUES construct cannot be aliased, it renders its own name.
        return values(
            column("credit_group", Integer),
            column("agent_id", _UUID),
            column("valid_from", Date),
            column("valid_to", Date),
            name=name,
        ).data(credit_rows)

    agent_ids = sorted({row[1] for row in credit_rows})
    delivered = [s for s in specs if s.basis == "delivered"]

    # The four order tables are read as tables, not mapped classes: the company rule is named
    # explicitly on each below, and the scope listener's own `company_id IN (...)` on a mapped
    # class is an indexable constant a statistics-less planner walks instead of the key.
    SO, SOL = SalesOrder.__table__.c, SalesOrderLine.__table__.c
    OL, ORD = OrderLine.__table__.c, Order.__table__.c

    # Another company's line or DO line counts nothing: its values are gated to 0 by a UUID
    # comparison inside CASE rather than filtered out, so the planner reaches each line by its
    # parent's key and never walks a whole-company index. A statistics-less planner (a freshly
    # filled table) believes that index returns one row and probes it once per order: 8.5 s at
    # 160,000 lines (#1319, measured), where the key walk takes milliseconds.
    mine = SOL.company_id == company_id

    def credited_lines():
        # The credited agents' live lines with a date: the sales report's predicate.
        return (
            select(
                SOL.id.label("sol_id"),
                SOL.product_id.label("product_id"),
                # AC-CANCEL-CRM: an AutoCount Cancel SO is neither sold nor delivered, though
                # TransferedQty (`qty_delivered`) counts it; both leave it out.
                case((mine, SOL.qty_ordered - func.coalesce(SOL.qty_cancelled, 0)),
                     else_=0).label("qty_ordered"),
                case((mine, func.greatest(SOL.qty_delivered - func.coalesce(SOL.qty_cancelled, 0),
                                          0)), else_=0).label("qty_delivered"),
                case((mine, SOL.line_total), else_=0).label("line_total"),
                mine.label("mine"),
                SO.order_date.label("order_date"),
                SO.sales_agent_id.label("agent_id"),
            )
            .select_from(SalesOrder.__table__)
            .join(SalesOrderLine.__table__, SOL.sales_order_id == SO.id)
            .where(
                SO.company_id == company_id,
                SO.sales_agent_id.in_(agent_ids),
                SO.status != "cancelled",
                SOL.line_status != "cancelled",
                # AC-CONSIGN-CRM Q5 a: a consignment is not a sale.
                SO.doc_type != DOC_TYPE_CONSIGNMENT,
                SO.order_date.isnot(None),
            )
        )

    # Only lines dated inside the span every period together covers count by their order date.
    in_span = SO.order_date.between(
        min(s.period_start for s in specs), max(s.period_end for s in specs)
    )
    dated = credited_lines().where(in_span)
    if delivered:
        # A line ordered outside that span still counts where a DO dated inside a delivered
        # period delivers it; no company here, this only bounds which lines are read and
        # `do_counted` gates the rest. The DO lines in the span are one hashed set each such line
        # is looked up in, never a join: a statistics-less planner, sizing `order_lines` from
        # its page count and declared column widths, estimated them at 5 rows and joined them to
        # every line the agent sold by a filter instead of a hash, 26 million comparisons and
        # 3.3 s at 7,200 DO lines once #1354 S2 widened `order_lines`. `IS TRUE` keeps the IN a
        # filter: a bare one is pulled up into exactly such a join. Uncorrelated, so it is costed
        # once (a correlated EXISTS was costed per line, which switched on JIT: 2.4 s of it).
        delivered_in_span = SOL.id.in_(
            select(OL.sales_order_line_id)
            .select_from(OrderLine.__table__)
            .join(Order.__table__, ORD.id == OL.order_id)
            .where(
                ORD.order_date.between(
                    min(s.period_start for s in delivered), max(s.period_end for s in delivered)
                )
            )
        )
        by_do = credited_lines().where(not_(in_span), delivered_in_span.is_(true()))
        # The two halves split on the order date, so no line is read twice.
        agent_lines = union_all(dated, by_do).cte("agent_lines")
    else:
        agent_lines = dated.cte("agent_lines")
    line_cols = (
        agent_lines.c.product_id,
        agent_lines.c.qty_ordered,
        agent_lines.c.qty_delivered,
        agent_lines.c.line_total,
        agent_lines.c.order_date,
        agent_lines.c.agent_id,
    )

    def in_periods(stmt, name: str, agent_id, order_date, on_date):
        """`stmt` joined to the periods containing `on_date` whose subject credits `agent_id`
        on `order_date` (the credit window always reads the SALES ORDER's date). The merged
        credit windows match an order date at most once, so no row repeats."""
        pd, cr = period_days.alias(name), credits(f"c{name}")
        stmt = stmt.join(
            cr, and_(cr.c.agent_id == agent_id, order_date.between(cr.c.valid_from, cr.c.valid_to))
        ).join(pd, and_(pd.c.credit_group == cr.c.credit_group, pd.c.day == on_date))
        return stmt, (pd.c.period_id, pd.c.target_id, pd.c.metric, pd.c.basis, pd.c.product_scope), pd

    sources = []
    if delivered:
        # Each linked DO line of those lines with the quantity it counts under the cap:
        # `greatest(least(quantity, qty_ordered - prior), 0)`, `prior` the sum of the line's
        # earlier live DO lines in (DO date nulls last, DO id, line sequence) order, so the
        # first `qty_ordered` units delivered count and the rest counts nowhere (S1-8, S1-26 d).
        # A cancelled or soft-deleted DO, another company's DO or DO line, or any DO of another
        # company's line, counts nothing: its quantity is 0 here rather than filtered out, so
        # the planner walks the DO by its key and never by the cancelled, deleted or company
        # index. (Gating the line alone is not enough: a negative DO quantity would lift
        # `counted` or the residual above 0 on a line whose own values are 0.)
        live_qty = case(
            (
                and_(
                    agent_lines.c.mine,
                    ORD.is_cancelled.is_(False),
                    ORD.deleted_at.is_(None),
                    OL.company_id == company_id,
                    ORD.company_id == company_id,
                ),
                OL.quantity,
            ),
            else_=0,
        )
        prior = func.sum(live_qty).over(
            partition_by=OL.sales_order_line_id,
            order_by=(ORD.order_date.asc().nullslast(), ORD.id, OL.line_sequence),
            rows=(None, -1),
        )
        do_counted = (
            select(
                agent_lines.c.sol_id,
                *line_cols,
                ORD.order_date.label("do_date"),
                live_qty.label("qty"),
                func.greatest(
                    func.least(live_qty, agent_lines.c.qty_ordered - func.coalesce(prior, 0)), 0
                ).label("counted"),
            )
            .select_from(agent_lines)
            .join(OrderLine.__table__, OL.sales_order_line_id == agent_lines.c.sol_id)
            .join(Order.__table__, ORD.id == OL.order_id)
            .cte("do_counted")
        )
        do_cols = (
            do_counted.c.product_id,
            do_counted.c.qty_ordered,
            do_counted.c.qty_delivered,
            do_counted.c.line_total,
            do_counted.c.order_date,
            do_counted.c.agent_id,
        )
        # The capped DO quantity per (period, line), bucketed by the DO date.
        by_do_rows, do_period, pd = in_periods(
            select(do_counted).select_from(do_counted), "pd",
            do_counted.c.agent_id, do_counted.c.order_date, do_counted.c.do_date,
        )
        sources.append(
            by_do_rows.with_only_columns(
                *do_period, do_counted.c.sol_id, *do_cols,
                literal(0).label("own"), func.sum(do_counted.c.counted).label("by_do"),
                literal(0).label("all_linked"),
            )
            .where(pd.c.basis == "delivered")
            # The line's and the period's columns ride along in the key: one value each.
            .group_by(*do_period, do_counted.c.sol_id, *do_cols)
        )
        # Every linked DO quantity of the line, whatever its date, on the line's own period
        # (the residual's input).
        linked = (
            select(do_counted.c.sol_id, *do_cols, func.sum(do_counted.c.qty).label("qty"))
            .group_by(do_counted.c.sol_id, *do_cols)
            .subquery("linked")
        )
        linked_rows, linked_period, pl = in_periods(
            select(linked).select_from(linked), "pl",
            linked.c.agent_id, linked.c.order_date, linked.c.order_date,
        )
        sources.append(
            linked_rows.with_only_columns(
                *linked_period, linked.c.sol_id,
                linked.c.product_id, linked.c.qty_ordered, linked.c.qty_delivered,
                linked.c.line_total, linked.c.order_date, linked.c.agent_id,
                literal(0).label("own"), literal(0).label("by_do"), linked.c.qty.label("all_linked"),
            ).where(pl.c.basis == "delivered")
        )
    # Rows of (period..., line, line columns..., own, by_do, all_linked). `own` is 1 on the
    # line's order-date period: ordered figures and the delivered residual count there only.
    own_rows, own_period, _ = in_periods(
        select(agent_lines).select_from(agent_lines), "pb",
        agent_lines.c.agent_id, agent_lines.c.order_date, agent_lines.c.order_date,
    )
    sources.append(
        own_rows.with_only_columns(
            *own_period, agent_lines.c.sol_id, *line_cols,
            literal(1).label("own"), literal(0).label("by_do"), literal(0).label("all_linked"),
        )
    )
    rows = union_all(*sources).subquery("rows")
    keys = (
        rows.c.period_id, rows.c.target_id, rows.c.metric, rows.c.basis, rows.c.product_scope,
        rows.c.sol_id, rows.c.product_id, rows.c.qty_ordered, rows.c.qty_delivered,
        rows.c.line_total, rows.c.order_date, rows.c.agent_id,
    )
    pairs = (
        select(
            *keys,
            func.max(rows.c.own).label("own"),
            func.sum(rows.c.by_do).label("by_do"),
            func.sum(rows.c.all_linked).label("all_linked"),
        )
        .group_by(*keys)
        .cte("pairs")
    )

    in_period = pairs.c.own == 1
    confirmed = func.least(pairs.c.qty_delivered, pairs.c.qty_ordered)
    residual = case((in_period, func.greatest(confirmed - pairs.c.all_linked, 0)), else_=0)
    delivered_qty = pairs.c.by_do + residual

    def cell(metric: str, basis: str):
        value = achievement_value_expr(
            metric, basis, delivered_qty, pairs.c.line_total, pairs.c.qty_ordered
        )
        if basis == "ordered":
            value = case((in_period, value), else_=0)
        return (and_(pairs.c.metric == metric, pairs.c.basis == basis), value)

    value = case(
        cell("amount", "ordered"),
        cell("quantity", "ordered"),
        cell("quantity", "delivered"),
        cell("amount", "delivered"),
        else_=0,
    )

    stmt = select(pairs.c.period_id, func.sum(value)).group_by(pairs.c.period_id)
    scoped = sorted({s.target_id for s in specs if s.product_scope != "all"})
    if scoped:
        # An all-products target reads no scope at all.
        stmt = stmt.where(
            or_(
                pairs.c.product_scope == "all",
                tuple_(pairs.c.target_id, pairs.c.product_scope, pairs.c.product_id).in_(
                    _scope_products(scoped, {s.product_scope for s in specs}, company_id)
                ),
            )
        )
    for period_id, total in db.execute(stmt).all():
        out[str(period_id)] = Decimal(total or 0)
    return out


def _scope_products(target_ids: List[str], scopes: Set[str], company_id: str):
    """`(target_id, product_scope, product_id)` for every product a scoped target counts, read
    once: its products, every product of its categories and their sub-categories (the tree
    walked once, a "Basins" target counts "Basins > Countertop"), every product of its brands."""
    scope = SalesTargetScope
    parts = []
    if "categories" in scopes:
        # UNION, not UNION ALL, so a cycle in the tree still terminates.
        scope_cat = (
            select(scope.target_id.label("target_id"), scope.product_category_id.label("category_id"))
            .where(
                scope.target_id.in_(target_ids),
                scope.company_id == company_id,
                scope.product_category_id.isnot(None),
            )
            .cte("scope_cat", recursive=True)
        )
        scope_cat = scope_cat.union(
            select(scope_cat.c.target_id, ProductCategory.id)
            .join(ProductCategory, ProductCategory.parent_category_id == scope_cat.c.category_id)
            .where(ProductCategory.company_id == company_id)
        )
        parts.append(
            select(scope_cat.c.target_id, literal("categories", String), Product.id)
            .join(Product, Product.category_id == scope_cat.c.category_id)
        )
    if "products" in scopes:
        parts.append(
            select(scope.target_id, literal("products", String), scope.product_id).where(
                scope.target_id.in_(target_ids),
                scope.company_id == company_id,
                scope.product_id.isnot(None),
            )
        )
    if "brands" in scopes:
        # A brand counts every product carrying it (the owner's hand test of 27 Sep).
        parts.append(
            select(scope.target_id, literal("brands", String), Product.id)
            .join(Product, Product.brand_id == scope.brand_id)
            .where(
                scope.target_id.in_(target_ids),
                scope.company_id == company_id,
                scope.brand_id.isnot(None),
            )
        )
    return union_all(*parts) if len(parts) > 1 else parts[0]


def unassigned_amount(db: Session, on: date, company_id: str) -> Decimal:
    """Ordered amount with no agent, in the calendar month of `on` (S1-14)."""
    month_start = on.replace(day=1)
    next_month = date(on.year + (on.month == 12), on.month % 12 + 1, 1)
    total = db.execute(
        select(func.coalesce(func.sum(func.coalesce(SalesOrderLine.line_total, 0)), 0))
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .where(
            SalesOrder.company_id == company_id,
            SalesOrderLine.company_id == company_id,
            SalesOrder.sales_agent_id.is_(None),
            SalesOrder.status != "cancelled",
            SalesOrderLine.line_status != "cancelled",
            SalesOrder.doc_type != DOC_TYPE_CONSIGNMENT,
            SalesOrder.order_date >= month_start,
            SalesOrder.order_date < next_month,
        )
    ).scalar()
    return Decimal(total or 0)


def any_do_linked(db: Session, company_id: str) -> bool:
    """True once any live DO line of the company is linked to one of its sales order lines.

    The same company rule the achievement query applies to DO lines, so the Counts label reads "by DO date"
    exactly when a DO line can count.
    """
    return bool(
        db.execute(
            select(literal(True))
            .select_from(OrderLine)
            .join(Order, Order.id == OrderLine.order_id)
            .join(SalesOrderLine, SalesOrderLine.id == OrderLine.sales_order_line_id)
            .where(
                OrderLine.company_id == company_id,
                Order.company_id == company_id,
                SalesOrderLine.company_id == company_id,
                Order.is_cancelled.is_(False),
                Order.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar()
    )


def achieved_pct(achieved: Optional[Decimal], target: Optional[Decimal]) -> Optional[float]:
    """round(achieved / target x 100, 1); None with no target or a target of 0."""
    if achieved is None or target is None or Decimal(target) == 0:
        return None
    return float(round(Decimal(achieved) / Decimal(target) * 100, 1))
