"""What counts as demand, in one place.

Two books describe the same sales-order line and each owns its own columns:

    qty_ordered, qty_delivered      the sales-order book, what the customer asked for and
                                    what has shipped
    qty_required, purchasing_status the Order Inquiry sheet, what CS decided to cover and
                                    where that stands in purchasing

So the quantity to plan for is the one CS stated, falling back to what is still owed when
nobody has stated one, and always net of what has already shipped:

    GREATEST(COALESCE(qty_required, qty_ordered) - qty_delivered, 0)

`qty_required` is NULL, not zero, when unreviewed. A NULL that fell through to zero would
delete demand nobody has got round to looking at, which is the opposite of what "not
reviewed" means.

This module exists so the netting engine and `scm.committed_v` cannot drift. They are two
implementations of one rule - one in Python over the ORM, one in SQL inside a view - and a
disagreement between them shows up as a dashboard that contradicts a plan run for reasons
nobody can see. The SQL is kept here beside the expression so the next person changing one
finds the other.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import func, literal, select, text

from app.models.order import SalesOrder, SalesOrderLine
from app.services.company_scope_sql import company_sql_predicate

#: CS has ruled this line out of purchasing. It stays on the sales order (the customer is
#: still owed it) and stops being something to buy.
COVERED = "covered"

#: Nobody has looked at this line yet. It COUNTS: a plan that quietly omitted unreviewed
#: demand would be optimistic in exactly the situation where CS is behind.
NOT_REVIEWED = "not_reviewed"

#: The origin stamp the Order Inquiry feed writes on orders it creates. Its OWN column,
#: never `source_system`: when CS's outstanding extract adopts an inquiry-created order it
#: overwrites `source_system` to take ownership, and if origin were read off that column the
#: act of adoption would silently delete the order's demand from the next run. Ownership
#: moves; origin does not.
ORDER_INQUIRY_ORIGIN = "scm_order_inquiry"

#: The demand class CS filters through the Order Inquiry.
PROJECT_CLASS = "project"

#: The ORDER-level half of the demand rule, as `PLAN-scm-purchasing-uat-journey.md` P3
#: leaves it (captain, 26 Aug 2026):
#:
#:   "Project demand = the UNLINKED remainder of raised OI ORDER rows, per product and
#:    location. Nothing else project-class counts."
#:
#: The SALES-ORDER BOOK speaks for the retail side and for nothing else. A project-class
#: line is never demand as a book line - it becomes demand when CS raises an Order Inquiry
#: ORDER row for it, and it stops being demand when that row is linked to a PO or an SPO.
#: One requirement, one place at a time (plan section 1).
#:
#: What this drops is the SHEET leg: an order the old Joey feed stamped
#: `demand_origin = 'scm_order_inquiry'` used to count from the book while nobody had
#: confirmed it on the fulfilment board. M310-CR-PJ showed 16 units of Project demand at
#: BRW-BB on runs b805ba89 / 93305b25 with every one of its inquiry rows already placed,
#: which is that leg and only that leg. Such an order is now AWAITING CS, counted and named
#: by `demand_source_service.set_aside_project_demand`, never netted.
#:
#: The rule is still in TWO halves, and the second one has outlived the first: the
#: line-level `PLAN_DEMAND_LINE_SQL` below says which lines an active decision already
#: covers, which is what the fulfilment board reads as "covered". Every book-demand reader
#: applies BOTH; this half now excludes project class outright, so the second is a no-op
#: for them and the board keeps the meaning it needs.
PLAN_DEMAND_ORDER_SQL = "(so.demand_class IS DISTINCT FROM 'project')"

#: Which core sales-order LINES an active supply decision already covers.
#:
#: Since P3 (26 Aug 2026) this is no longer part of what the plan counts - the order-level
#: half above excludes project class outright, so there is no sheet quantity left for a
#: decision to displace. What it still answers, and what it is now kept for, is the
#: FULFILMENT BOARD's "covered" flag: a line CS has already decided on, per LINE, so a
#: partially confirmed order shows which of its lines are done and which are not
#: (`project_fulfilment_board_service`).
#:
#: PER LINE, and that is the whole point (PLAN-fulfilment-planning-from-autocount-so.md
#: 13.4). It used to be per ORDER, joining `projects.sales_orders.so_id` to any active
#: decision, which was exact while a confirmation had to cover every line of its order.
#: Partial confirmation ended that.
#:
#: Which lines a decision COVERS is read out of `line_snapshots`, because that JSONB is
#: where it is recorded: one object per covered line, each carrying its `core_line_id`
#: (`ProjectSupplyService._snapshot`). No new table, no new column - 13.4's open
#: sub-question, answered by measuring the lateral rather than by adding a link table.
#: The match is on the core sales-order line id and never on a reference, a document
#: number or an item code.
PLAN_DEMAND_LINE_SQL = (
    "NOT EXISTS ("
    "    SELECT 1 FROM projects.so_supply_decisions d "
    "    CROSS JOIN LATERAL jsonb_array_elements(d.line_snapshots) AS snap "
    "    WHERE d.state = 'active' "
    "      AND (snap->>'core_line_id')::uuid = sol.id)"
)


def _decided_core_line_ids():
    """The core sales-order LINES an active supply decision already covers.

    UNCORRELATED on purpose, and that is the whole reason it is a list rather than the
    `NOT EXISTS` the view uses. Callers reach this predicate with `sales_order_lines`
    aliased (the coverage timeline reads `sales_order_lines AS sales_order_lines_1`), and
    a correlated sub-select then renders a reference to an alias the enclosing query never
    made - `missing FROM-clause entry for table "sales_order_lines_1"`, every demand read
    in the system. A subquery that names nothing outside itself cannot be adapted wrongly,
    and `SalesOrderLine.id` beside it is an ordinary outer column exactly like
    `SalesOrderLine.line_status`. The view keeps its `NOT EXISTS`, which is the same set:
    raw SQL has no aliasing to get wrong.

    The `IS NOT NULL` filter is load-bearing, not tidiness: a NULL in a `NOT IN` list makes
    the whole predicate NULL, which would silently drop EVERY line from the readers that
    apply it.
    A snapshot with no `core_line_id` cannot happen through the confirmation path (a line
    with no reconciled core line is refused), and the filter means a hand-written one
    could not take the plan down with it either.

    Built over the TABLE rather than the mapped class so the company-scope loader criteria
    cannot rewrite a sub-select whose only job is to answer "which lines are decided": the
    scoping that matters is the caller's, on `sales_order_lines` itself.
    """
    from sqlalchemy import cast, literal_column
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    decision = SOSupplyDecision.__table__
    snapshots = (
        func.jsonb_array_elements(decision.c.line_snapshots)
        .table_valued("value")
        .render_derived(name="snap")
        .lateral()
    )
    core_line_id = cast(
        snapshots.c.value.op("->>")(literal_column("'core_line_id'")),
        PG_UUID(as_uuid=False),
    )
    return (
        select(core_line_id)
        .select_from(decision.join(snapshots, literal_column("true")))
        .where(decision.c.state == DECISION_ACTIVE, core_line_id.isnot(None))
    )


def is_plan_demand_order():
    """`PLAN_DEMAND_ORDER_SQL`, as a SQLAlchemy expression over `sales_orders`."""
    return SalesOrder.demand_class.is_distinct_from(PROJECT_CLASS)


def is_plan_demand_line():
    """`PLAN_DEMAND_LINE_SQL`, as a SQLAlchemy expression over `sales_order_lines`.

    Applied BESIDE `is_plan_demand_order()`, never instead of it: one says which orders
    the sheet speaks for, the other which of their lines CS has already decided.
    """
    return SalesOrderLine.id.notin_(_decided_core_line_ids())


def demand_qty():
    """The quantity to plan for, as a SQLAlchemy expression over `sales_order_lines`."""
    return func.greatest(
        func.coalesce(SalesOrderLine.qty_required, SalesOrderLine.qty_ordered)
      - func.coalesce(SalesOrderLine.qty_delivered, 0),
        0,
    )


def is_open_demand():
    """The predicate every demand reader shares, minus the caller's own scoping."""
    return (SalesOrderLine.line_status == "open") & (
        SalesOrderLine.purchasing_status != COVERED
    ) & (demand_qty() > 0)


def is_transferable_order():
    """An order AutoCount has not marked Transferable = F (SO-TRANSFERABLE).

    F means "not confirmed yet for the queue" (owner, 1 Oct 2026). Owner ruling (b): such an
    order is out of the ONE assignment Stock Debt and the fulfilment board share (R21) and out
    of the board's pile reads, so no stock is reserved for it anywhere until AutoCount flips it
    to T. NULL is "the source never said" (every Excel / manual / older order) and counts like
    T, hence `IS NOT FALSE` rather than `IS TRUE`.

    Deliberately NOT part of `is_open_demand()` (owner decision (a), 1 Oct 2026): the reorder
    run, `scm.committed_v`, coverage, the outstanding / sales reports and the chatbot keep
    counting an F order as demand.
    """
    return SalesOrder.is_transferable.isnot(False)


def plan_qty():
    """What the BOARD plans for one line: `coalesce(qty_required, qty_ordered)`.

    NOT `demand_qty()`, and the difference is the whole 14 September 2026 ruling. That one
    nets what has already shipped, because the netting engine, the reorder plan and the
    worklist all ask "what is still owed". The board asks a different question - "has anybody
    decided where this line's stock comes from" - and a delivered unit nobody sourced is a
    unit to put back, so it is planned at its full quantity.

    `qty_required` leads for the same reason it does in `demand_qty()`: the Order Inquiry
    sheet is CS's own statement of what to cover, and it beats the book's `qty_ordered` when
    somebody has stated one.
    """
    return func.coalesce(SalesOrderLine.qty_required, SalesOrderLine.qty_ordered)


def is_undecided_demand():
    """THE BOARD'S predicate: which lines it admits, and why it is not `is_open_demand()`.

    A line is admitted when nobody has ruled on it and there is something to rule on:

    - not `cancelled` - it is owed to nobody, whatever its delivered column says;
    - not `purchasing_status = covered` - a person already said no purchase is needed;
    - `plan_qty() > 0` - a line for nothing is not a question.

    DELIVERY IS DELIBERATELY ABSENT. SO421404 read Completed, three of three delivered, and
    had never been planned: the stock left the bin with nothing behind it, so no ORDER row
    ever reached purchasing and nothing was bought back. Under `is_open_demand()` that order
    is invisible the moment the book says it shipped.

    The two predicates diverge on purpose and BOTH stay (AC-S2-11), the way
    `_cancelled_pending_change_rows` already diverges for pending changes. Whether an active
    decision or a live inquiry row covers the line is a separate question this does not ask -
    that is `is_decided_demand()`, because a decided line stays ON the board, read-only,
    rather than being filtered out of it.
    """
    return (
        SalesOrderLine.line_status.is_distinct_from("cancelled")
        & (SalesOrderLine.purchasing_status.is_distinct_from(COVERED))
        & (plan_qty() > 0)
    )


def live_inquiry_core_line_ids():
    """The core lines a LIVE order inquiry row already names.

    Live means the row still stands: its state is not `cancelled` and purchasing has not
    rejected it. A rejected row is back in CS's hands and decides nothing; a cancelled one
    went away. Reached through the mirror line, because an inquiry row is keyed to
    `projects.sales_order_lines` and every reader here is keyed to the core line.

    Three narrowings, and each one is load-bearing: the row belongs to no board decision (a
    confirmation's own row is not somebody else telling purchasing), it still stands, and no
    PENDING planning-change row names the line - the book moving a line beats purchasing
    having been told, and apply cancels the placed row anyway.

    Uncorrelated and over the TABLES, for the same two reasons `_decided_core_line_ids()` is:
    callers arrive with `sales_order_lines` aliased, and the company-scope loader must not
    rewrite a sub-select whose only job is to answer "which lines were purchasing told about".
    The pending-change EXISTS correlates to `mirror` alone, which this select owns, so it is
    safe against the same aliasing.
    """
    from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
    from app.models.project_so import (
        ACK_REJECTED,
        INQUIRY_CANCELLED,
        OrderInquiryRow,
        ProjectSalesOrderLine,
    )
    from app.services.planning_change_service import PLANNING_CHANGE_STATE_PENDING

    rows = OrderInquiryRow.__table__
    mirror = ProjectSalesOrderLine.__table__
    change_rows = PlanningChangeRow.__table__
    batches = PlanningChangeBatch.__table__
    query = (
        select(mirror.c.core_sales_order_line_id)
        .select_from(rows.join(mirror, mirror.c.id == rows.c.so_line_id))
        .where(
            mirror.c.core_sales_order_line_id.isnot(None),
            rows.c.state.is_distinct_from(INQUIRY_CANCELLED),
            rows.c.ack_state.is_distinct_from(ACK_REJECTED),
            # RAISED BY SOMETHING OTHER THAN A BOARD DECISION, which is the whole point of
            # the rule. A row a confirmation wrote carries its `supply_decision_id`; the
            # migrated sheet's rows (#875) carry none, and those are the instructions the
            # board would otherwise propose for a second time.
            #
            # Without this the predicate swallowed its own tail: a line whose decision a
            # planning change had just SUPERSEDED still had that decision's live row, so
            # the board read it as decided and stopped re-planning the very line the change
            # had re-opened (eleven tests in `test_planning_change_apply_on_board.py`).
            rows.c.supply_decision_id.is_(None),
            # AND THE BOOK HAS NOT MOVED THE LINE SINCE (owner's ruling, 14 Sep 2026). A
            # pending planning-change row says the book moved it after the instruction was
            # written, and apply cancels and unlinks the placed row - so it is stale by
            # definition and decides nothing. Read here rather than only on the board so
            # the Sales Orders list's Planned pill and the board it opens cannot disagree
            # about one line.
            ~select(literal(1))
            .select_from(
                change_rows.join(
                    batches, batches.c.id == change_rows.c.batch_id
                )
            )
            .where(
                change_rows.c.core_line_id == mirror.c.core_sales_order_line_id,
                change_rows.c.applied_state == PLANNING_CHANGE_STATE_PENDING,
                batches.c.applied_at.is_(None),
            )
            .exists(),
        )
    )
    return query


def is_decided_demand():
    """"Somebody has already ruled where this line's stock comes from", in ONE place.

    Two ways a line is decided and they are equal in weight:

    - an ACTIVE supply decision names it (`_decided_core_line_ids()`, what the board has
      always meant by `covered`);
    - a LIVE order inquiry row names it. Since #875 the migrated sheet raised rows on real
      sales-order lines, so purchasing was told about 2,311 open lines the board would
      otherwise propose for all over again.

    The board's read-only row and the Sales Orders list's Planned count read THIS, so an
    order cannot read "2 of 3 planned" on the list and open a board that disagrees.
    """
    return SalesOrderLine.id.in_(_decided_core_line_ids()) | SalesOrderLine.id.in_(
        live_inquiry_core_line_ids()
    )


def qty_of(row) -> float:
    """The same rule against a fetched row, for callers that select the columns themselves."""
    required = getattr(row, "qty_required", None)
    ordered = float(row.qty_ordered or 0)
    base = float(required) if required is not None else ordered
    return max(base - float(row.qty_delivered or 0), 0.0)


#: The Order Inquiry verb that says "buy this". The only verb that is new purchasing
#: demand; every other verb is an amendment instruction or a coverage note.
BUY_VERB = "ORDER"

#: An inquiry row still waiting to be placed. `actioned` means purchasing has bought it
#: and `cancelled` means it went away, so neither is current need.
UNPLACED_INQUIRY_STATE = "raised"

#: The states whose UN-LINKED remainder is still demand, which is what `scm.committed_v`
#: counts (migration 422). `partly_linked` belongs here and `raised` alone does not: since
#: the links table a row is netted by what it has been linked to rather than emptied by a
#: state change, so a row half covered by a purchase order is half of a demand still.
UNLINKED_INQUIRY_STATES = ("raised", "partly_linked")

#: A REDIRECTED row is not carried by a replan (`PLAN-oi-replan-received-links.md`, S2):
#: the document it still shows as history has already shipped to somebody else's order,
#: so it must never net a line's demand a second time on top of the fresh row raised in
#: its place. Every leg over `projects.order_inquiry_rows` below states this ONE clause,
#: never six separate spellings of it.
NOT_REDIRECTED_SQL = "AND oir.redirected_to_pool = FALSE"

#: The one decision state that counts. A superseded or challenged revision's Buy is
#: history, and counting it would buy the same requirement twice.
ACTIVE_DECISION_STATE = "active"

#: The acknowledgement state a row is NOT counted in (`PLAN-scm-oi-handshake.md` section
#: 3): purchasing looked at the instruction and refused it, with a reason, so the quantity
#: is not owed by anybody until CS decides the line again. `scm.committed_v` drops it and
#: so does every reader of the view.
REJECTED_ACK_STATE = "rejected"

#: What the REORDER PLAN counts, which is narrower than what the view counts (the captain,
#: 27 Aug 2026): "reorder planning's project demand reads ACKNOWLEDGED rows only (and rows
#: changed after acknowledgement); awaiting rows are a count on the plan page, never
#: demand". An awaiting row is an instruction purchasing has not read yet, and buying
#: against it is buying against something that may still be amended or refused.
#:
#: The two rules are deliberately different and both are stated here so neither can be
#: read as the other: `scm.committed_v` (the board, the dashboard, the demand drill) says
#: what is owed, and `horizon_committed_select_sql` - which every plan run now reads
#: through - says what may be BOUGHT today.
PLANNED_ACK_STATES = ("acknowledged", "changed")
#: The same tuple as a SQL list, so the four predicates below are written from the
#: constants rather than beside them. Two spellings of one rule is how the view and the
#: plan come to disagree about a row without anybody editing both.
_PLANNED_ACK_SQL = ", ".join(f"'{state}'" for state in PLANNED_ACK_STATES)

#: The body of `scm.committed_v`. Kept as a constant so the migration that installs it and
#: the expression above are edited in the same file.
#:
#: Front planning (plan 4, 5.3, 6.4) splits the SAME aggregate row by demand channel. The
#: keys and the cardinality are untouched - one row per (product_id, warehouse_id) - and
#: `committed` stays the sum of the three new columns, so `scm.net_position_v` and every
#: consumer of it sees no change. What is new is that the row can now SAY which channel its
#: commitment came from, which is what makes a Project total firm and a Retail total
#: nettable without a second read model.
#:
#: Project demand has ONE source: `projects.order_inquiry_rows` (P3, captain 26 Aug 2026).
#: Two legs read it, and they are disjoint by construction:
#:
#: * the CONFIRMED leg - current un-linked Buy on a row pointing at an ACTIVE
#:   `projects.so_supply_decisions` row, landed at the location of the reconciled core SO
#:   line (the DONOR's location for an ORDER BACK).
#: * the FORM leg - a row the CS Order Inquiry Form raised that the sales-order book carries
#:   no line for, landed at the item code and stock location the ROW itself states.
#:
#: The SHEET leg is GONE. It counted an open line of a `demand_origin = 'scm_order_inquiry'`
#: order while no active decision covered that line, which is how M310-CR-PJ read 16 units of
#: Project demand at BRW-BB (SO394803 line 10 + SO411133 line 6) with every one of its
#: inquiry rows already placed, and how MSK11B read 243 at BRW-IB off SO409325. Those orders
#: came in through the old sheet feed months ago and nobody has confirmed them on the
#: fulfilment board - so they are AWAITING CS, not demand, and
#: `demand_source_service.set_aside_project_demand` is where they are counted and named.
#:
#: With the sheet leg gone the BOOK leg speaks for the retail side alone, so `project_qty`
#: and `project_confirmed_qty` are always equal (`project_committed` IS the confirmed
#: figure) and the old `decided` CTE has nothing left to exclude.
#:
#: Every constant leg column is `0::numeric`, and the cast is LOAD-BEARING. Postgres types a
#: bare `0` as integer, `SUM(integer)` comes out bigint, and `CREATE OR REPLACE VIEW` refuses
#: to change an existing column's type: applying this over a database already carrying the
#: view dies with `cannot change data type of view column "unclassified_committed" from
#: numeric to bigint`. It did, on the dev copy. The bodies before P3 never hit it because
#: their zeros sat in a `CASE` whose other arm was numeric, which coerced them; a whole
#: column of bare zeros has nothing to be coerced by.
#:
#: `unclassified_qty` is a CONSTANT ZERO, not a leg (P4). Nothing is unclassified any more:
#: migration 425 stamped every NULL `demand_class` retail and the SO import now refuses a
#: file that would create another, so a NULL class here reads as retail - the book-direct
#: channel - instead of as a fourth column nobody can act on. The COLUMN survives because
#: `CREATE OR REPLACE VIEW` may only append columns, never drop one, and dropping it would
#: mean dropping and rebuilding `scm.net_position_v` and everything under it for a figure
#: that is now always 0.
#: What an order inquiry row still asks purchasing to buy: the quantity CS raised, less
#: what is already linked to a document, less a "supplied with" bundle, floored at zero.
#: UNCAPPED for every verb (owner ruling R1, 23 Sep 2026, `PLAN-oi-order-rows-uncapped.md`):
#: "this is delivered already and we want to replenish, I don't mind order back or order,
#: as long as it needs to order". SO421985 raised three ORDER rows of 493, the AutoCount
#: pull then marked the lines delivered 493/493 and closed the order, and Start Plan
#: stopped listing it - a raised, unlinked row is buy demand until purchasing links it,
#: whatever the line's own delivered column says. That retires the 7.3 / 14 Sep cap
#: (SO368872 / SRTWC286-SH, "Buy never exceeds what the line still owes") for ORDER rows
#: too: the known consequence is that the SO368872 shape now buys the row quantity again,
#: which is the trade the owner took. The ORDER_BACK branch #1114 (22 Sep) carved out as an
#: exception is gone with it - ORDER and ORDER_BACK are now the SAME formula, because the
#: exception is now the rule.
#:
#: ONE fragment, interpolated into the view body, into the horizon SELECT and into the
#: needed-date SQL, so the card, the Remaining column and the engine cannot come to answer
#: three different numbers for one row (reviewer S1, 15 Sep). The worklist's own ORM twin is
#: `order_inquiry_worklist_service._ROW_QTY`.
_OWED_SQL = "GREATEST(oir.qty - COALESCE(lk.linked, 0) - oir.bundled_qty, 0)"

#: The FORM leg's twin, and the one the MIGRATED rows actually travel on: the order inquiry
#: sheet raises rows with no supply decision, so this is the leg that counts them. Same R1
#: formula, same reasoning: a form row's line, when it has one, no longer caps it either, so
#: the leg no longer needs to reach the core line at all (`_FORM_CORE_LINE_JOIN_SQL` and its
#: `cpsl`/`csol` joins are retired with the cap that was their only reader).
_OWED_FORM_SQL = "GREATEST(oir.qty - COALESCE(flk.linked, 0) - oir.bundled_qty, 0)"

#: The SO-scope join `horizon_committed_select_sql(so_scoped=True)` adds to BOTH project
#: legs (PLAN-reorder-plan-demand-class-orders.md, S2, 21 Sep 2026). Walks an inquiry row
#: back to its own CORE sales order - `order_inquiry_id -> order_inquiries.
#: project_sales_order_id -> projects.sales_orders.so_id -> sales_orders.id` - and matches
#: its number against the bound list. R1: the key is the WHOLE SO, intersected with every
#: other predicate already on the leg (a row outside the range or unacknowledged is still
#: excluded even when its SO is named). Never applied to the retail leg: an SO scope only
#: ever narrows the project leg. `sso.company_id = oir.company_id` (security N1, 21 Sep
#: 2026): a bare `so_number` match would let a same-numbered SO in ANOTHER company match
#: this row - SO numbers are unique per company, not globally.
_SO_SCOPE_JOIN_SQL = (
    "JOIN projects.order_inquiries soi ON soi.id = oir.order_inquiry_id\n"
    "    JOIN projects.sales_orders spso ON spso.id = soi.project_sales_order_id\n"
    "    JOIN sales_orders sso ON sso.id = spso.so_id AND sso.so_number = ANY(:so_numbers)\n"
    "        AND sso.company_id = oir.company_id"
)

COMMITTED_V_SQL = f"""
CREATE OR REPLACE VIEW scm.committed_v AS
WITH legs AS (
    -- The BOOK leg, and it is the RETAIL channel entire (P3). A project-class line is
    -- never demand as a book line: it becomes demand when CS raises an Order Inquiry
    -- ORDER row for it, which the confirmed and form legs below count, and it stops being
    -- demand when that row is linked. A NULL class reads as retail - the book-direct
    -- channel - because nothing is unclassified any more (P4).
    SELECT sol.product_id,
           sol.warehouse_id,
           0::numeric AS project_qty,
           0::numeric AS project_confirmed_qty,
           GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                  - COALESCE(sol.qty_delivered, 0), 0) AS retail_qty,
           0::numeric AS unclassified_qty
    FROM sales_order_lines sol
    JOIN sales_orders so ON so.id = sol.sales_order_id
    WHERE so.status = 'open'
      AND sol.line_status = 'open'
      AND sol.purchasing_status <> 'covered'
      AND GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                 - COALESCE(sol.qty_delivered, 0), 0) > 0
      AND so.demand_class IS DISTINCT FROM 'project'
    UNION ALL
    -- The confirmed leg: what CS decided must be bought, at the reconciled core line's
    -- product and fulfilment location, LESS whatever of it now sits on a document
    -- (PLAN-scm-cs-planning-uat.md section 3.I). Never matched on provisional_ref,
    -- autocount_doc_no or item code (plan 4).
    --
    -- Netted per ROW rather than tested per STATE. Before `order_inquiry_links` a row was
    -- all or nothing - `raised` counted the whole quantity, `placed` counted none - so a
    -- cascade that could only cover part of a row had to SPLIT the row for the arithmetic
    -- to come out, which is how nine sales-order lines became eleven instructions. A fully
    -- linked row now leaves confirmed demand exactly as `placed` did, and a half-linked
    -- one leaves half of it.
    --
    -- ORDER_BACK counts here too (PLAN-scm-purchasing-uat-journey.md section 4b): it is
    -- still demand until it is linked. At the DONOR's location, which is what the row's
    -- own `stock_location` names: the row hangs off the BORROWING line, so reading the
    -- core line's warehouse would put the hole in a warehouse that never had one.
    SELECT sol.product_id,
           CASE WHEN oir.verb = 'ORDER_BACK'
                THEN COALESCE(donor.id, sol.warehouse_id)
                ELSE sol.warehouse_id END AS warehouse_id,
           {_OWED_SQL} AS project_qty,
           {_OWED_SQL} AS project_confirmed_qty,
           0::numeric AS retail_qty,
           0::numeric AS unclassified_qty
    FROM projects.order_inquiry_rows oir
    JOIN projects.so_supply_decisions d
      ON d.id = oir.supply_decision_id
     AND d.state = 'active'
    JOIN projects.sales_order_lines psl ON psl.id = oir.so_line_id
    JOIN sales_order_lines sol ON sol.id = psl.core_sales_order_line_id
    LEFT JOIN warehouses donor ON donor.warehouse_code = oir.stock_location
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) lk ON TRUE
    WHERE oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.ack_state <> '{REJECTED_ACK_STATE}'
      AND oir.qty > 0
      -- PLAN-scm-supplied-with-companions.md ruling 6: a bundled unit never reaches
      -- reorder planning, whatever the item it rides with is covered by. R1 (23 Sep):
      -- otherwise uncapped, so the leg drops a row only once it is fully linked/bundled.
      AND {_OWED_SQL} > 0
    UNION ALL
    -- The FORM leg: an instruction the CS Order Inquiry Form raised that no supply decision
    -- points at (`PLAN-scm-cs-planning-uat.md` section 3.I; the fixture sheet's `[NL]`
    -- rows). CS writes `ORDER BACK` where a delivery date belongs, and the form is the only
    -- writer that can raise it - the fulfilment board has nothing to decide about a line
    -- AutoCount has closed. The ROW states its own item and location, and those are what
    -- this leg reads, whether or not it also names a sales-order line. Without it the
    -- fourteen instructions on SO381895's first two forms are raised, shown to purchasing,
    -- and invisible to the plan that decides what to buy.
    --
    -- Joined on the CODE and the company together, which is exactly what
    -- `uq_products_company_product_code` / `uq_warehouses_company_warehouse_code` make
    -- unique - a code alone would multiply the row once per company holding the same SKU.
    --
    -- INNER on `products`, because a row naming an item this system does not hold is demand
    -- for nothing and there is no product to attribute it to. LEFT on `warehouses`, because
    -- a row that names no location, or one we do not hold, is still demand - it comes out
    -- with a NULL warehouse, which every reader joins on `(product, warehouse)` and so
    -- matches nowhere. Counted at no location rather than invented at one, and visible in
    -- the view rather than dropped from it.
    --
    -- `supply_decision_id IS NULL` keeps this leg disjoint from the CONFIRMED leg above:
    -- a row a CS decision points at is counted there, at the core line's product and
    -- location, and every OTHER raised row is counted here at its own. The leg used to
    -- demand `so_line_id IS NULL` as well, because a row naming a book line was already
    -- counted by the SHEET leg; P3 retired that leg, and the condition would now DELETE
    -- such a row from planning instead of de-duplicating it - the form raises ORDER BACK
    -- rows carrying an `so_line_id` (`project_order_inquiry_import_service`), and they are
    -- demand like any other.
    --
    -- The `NOT EXISTS` below keeps it disjoint from the BOOK leg, which is the other half
    -- of the same worry and the half P3 opened: the book still speaks for retail, so a
    -- decision-less row naming a RETAIL line would be counted twice, once as the line and
    -- once as the row. Stated as "the book leg does not count this line" - the same four
    -- openness conditions the book leg applies, plus its class test - rather than as "the
    -- line is project": a row whose line is unreconciled, closed or fully delivered is
    -- counted by nothing else, and a class test alone would drop it.
    SELECT fp.id AS product_id,
           fw.id AS warehouse_id,
           {_OWED_FORM_SQL} AS project_qty,
           {_OWED_FORM_SQL} AS project_confirmed_qty,
           0::numeric AS retail_qty,
           0::numeric AS unclassified_qty
    FROM projects.order_inquiry_rows oir
    JOIN products fp
      ON fp.product_code = oir.item_code
     AND fp.company_id = oir.company_id
    LEFT JOIN warehouses fw
      ON fw.warehouse_code = oir.stock_location
     AND fw.company_id = oir.company_id
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) flk ON TRUE
    WHERE oir.supply_decision_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM projects.sales_order_lines fpsl
          JOIN sales_order_lines fsol ON fsol.id = fpsl.core_sales_order_line_id
          JOIN sales_orders fso ON fso.id = fsol.sales_order_id
          WHERE fpsl.id = oir.so_line_id
            AND fso.demand_class IS DISTINCT FROM 'project'
            AND fso.status = 'open'
            AND fsol.line_status = 'open'
            AND fsol.purchasing_status <> 'covered'
            AND GREATEST(COALESCE(fsol.qty_required, fsol.qty_ordered)
                       - COALESCE(fsol.qty_delivered, 0), 0) > 0)
      AND oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.ack_state <> '{REJECTED_ACK_STATE}'
      AND oir.qty > 0
      -- Ruling 6, form leg: the same "never reaches reorder planning" rule. R1 (23
      -- Sep): uncapped, same as the confirmed leg.
      AND {_OWED_FORM_SQL} > 0
)
SELECT product_id,
       warehouse_id,
       SUM(project_qty + retail_qty + unclassified_qty) AS committed,
       SUM(project_qty) AS project_committed,
       SUM(retail_qty) AS retail_committed,
       SUM(unclassified_qty) AS unclassified_committed,
       -- LAST on purpose: appended, so a CREATE OR REPLACE of this body over a database
       -- already carrying the four-column view is legal (Postgres lets a replacement add
       -- columns at the end and nowhere else). A SUBSET of `project_committed`, never a
       -- fourth addend of `committed` - adding it there would count confirmed Buy twice.
       SUM(project_confirmed_qty) AS project_confirmed_committed
FROM legs
GROUP BY product_id, warehouse_id;
"""


def horizon_committed_select_sql(
    demand_class: Optional[str] = None,
    so_scoped: bool = False,
    retail_windowed: bool = True,
) -> str:
    """THE PLAN'S committed figure: `COMMITTED_V_SQL`'s body as a bare SELECT (no
    `CREATE VIEW`), with a `:horizon` bind narrowing both legs to demand due at or before
    it, a `:horizon_start` bind (S4, PLAN-reorder-feedback-9sep.md) narrowing them to demand
    due at or after it, and with the project legs narrowed to ACKNOWLEDGED demand.

    Planning horizon (captain, 20 Aug): "SOs needed in 2030" a buyer never asked about
    should not distort a plan they only want through December. `scm.committed_v` itself
    is untouched and every OTHER reader (the demand-drill popover, the dashboard, the
    coverage timeline) keeps reading it unfiltered - this is used ONLY by
    `reorder_run_service._planning_rows`, to give a plan run its own committed figure.

    The acknowledgement rule (captain, 27 Aug, `PLAN-scm-oi-handshake.md`) is why this is
    now read on EVERY run rather than only on a horizoned one: an order-inquiry row
    purchasing has not acknowledged yet is not something to buy against - CS may still
    amend it and purchasing may still refuse it - so the two project legs count
    `PLANNED_ACK_STATES` alone. The view goes on counting an awaiting row, because it is
    still owed to the customer; what the plan page shows for the difference is the
    "N awaiting acknowledgement" chip, never a purchase.

    A NULL `:horizon` (or `:horizon_start`) reproduces `scm.committed_v`'s DATE handling
    exactly: every date comparison short-circuits true, so an unhorizoned run (the daily
    scheduled one, and any manual run that leaves the field empty) nets as it always did
    except for the acknowledgement rule above. Demand carrying NO date at all is always
    counted, whatever either bind is - unscheduled demand is still demand, not a reason to
    guess it is late or early (G2, 9 Sep ruling).

    Kept beside `COMMITTED_V_SQL` rather than derived from it: the view body is frozen
    for the migration/downgrade pair (`test_committed_v_migration_chain.py`) and must
    stay copy-pasteable, so this is a second copy of the same `legs` shape with one
    predicate added to each leg - the same relationship `COMMITTED_V_SQL` already has to
    the individual predicates in this module (`PLAN_DEMAND_ORDER_SQL` etc).

    ``demand_class`` (PLAN-reorder-plan-demand-class-orders.md, S2, 21 Sep 2026) drops
    whichever legs the run's Demand scope excludes from the UNION: ``'project'`` keeps only
    the two project legs (confirmed + form), ``'retail'`` keeps only the book leg, and
    ``None`` - every caller before this lane, and an unscoped run - keeps all three,
    rendering the exact SQL this function has always produced (T12).

    ``so_scoped`` (same plan) adds `_SO_SCOPE_JOIN_SQL` to BOTH project legs when True,
    narrowing them to the sales orders bound as `:so_numbers` (R1: the key is the whole
    SO, intersected with every predicate already on the leg - a row outside the range or
    unacknowledged is still excluded even when its SO is named). False (the default) adds
    no join and binds no `:so_numbers`, so a caller that never asks for this - every OTHER
    caller of this function today - keeps compiling/binding exactly as it always has.

    ``retail_windowed`` (Lane D, AC-D1b, owner ruling: "this order range only is for
    project") gates the SO-book leg's own `:horizon`/`:horizon_start` predicates. True (the
    default, and every caller before this lane) keeps them, so the book leg is windowed
    exactly like the two project legs always have been. False drops both predicates from
    THIS leg only - the project legs stay windowed either way - so an unscoped (All) run's
    retail side plans every open book line regardless of "Plan until", the same reading an
    unhorizoned run has always given it. `_planning_rows` is the only caller that ever
    passes False, and only when `demand_class is None`.
    """
    so_join = _SO_SCOPE_JOIN_SQL if so_scoped else ""
    retail_horizon_sql = f"""
      -- Planning horizon, book leg: a stated required_date past the cutoff is excluded;
      -- no date at all is always in.
      AND (CAST(:horizon AS date) IS NULL OR sol.required_date IS NULL
           OR sol.required_date <= CAST(:horizon AS date))
      -- Planning window START (S4, 9 Sep): the same rule, other side. G2 ruling - a
      -- required_date before the start is excluded; no date at all is always in, the same
      -- reading the end date already gives it.
      AND (CAST(:horizon_start AS date) IS NULL OR sol.required_date IS NULL
           OR sol.required_date >= CAST(:horizon_start AS date))""" if retail_windowed else ""

    retail_leg = f"""
    SELECT sol.product_id,
           sol.warehouse_id,
           0::numeric AS project_qty,
           0::numeric AS project_confirmed_qty,
           GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                  - COALESCE(sol.qty_delivered, 0), 0) AS retail_qty,
           0::numeric AS unclassified_qty
    FROM sales_order_lines sol
    JOIN sales_orders so ON so.id = sol.sales_order_id
    WHERE so.status = 'open'
      AND sol.line_status = 'open'
      AND sol.purchasing_status <> 'covered'
      AND GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                 - COALESCE(sol.qty_delivered, 0), 0) > 0
      AND so.demand_class IS DISTINCT FROM 'project'{retail_horizon_sql}"""

    confirmed_leg = f"""
    SELECT sol.product_id,
           CASE WHEN oir.verb = 'ORDER_BACK'
                THEN COALESCE(donor.id, sol.warehouse_id)
                ELSE sol.warehouse_id END AS warehouse_id,
           {_OWED_SQL} AS project_qty,
           {_OWED_SQL} AS project_confirmed_qty,
           0::numeric AS retail_qty,
           0::numeric AS unclassified_qty
    FROM projects.order_inquiry_rows oir
    JOIN projects.so_supply_decisions d
      ON d.id = oir.supply_decision_id
     AND d.state = 'active'
    JOIN projects.sales_order_lines psl ON psl.id = oir.so_line_id
    JOIN sales_order_lines sol ON sol.id = psl.core_sales_order_line_id
    {so_join}
    LEFT JOIN warehouses donor ON donor.warehouse_code = oir.stock_location
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) lk ON TRUE
    WHERE oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.ack_state IN ({_PLANNED_ACK_SQL})
      AND oir.qty > 0
      -- Ruling 6: a bundled unit never reaches reorder planning. R1: otherwise uncapped.
      AND {_OWED_SQL} > 0
      -- Planning horizon, confirmed leg: same rule, off the inquiry row's own delivery
      -- date rather than the core line's required_date.
      AND (CAST(:horizon AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date <= CAST(:horizon AS date))
      -- Planning window START (S4): same rule, other side.
      AND (CAST(:horizon_start AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date >= CAST(:horizon_start AS date))"""

    form_leg = f"""
    -- The FORM leg: an instruction the CS Order Inquiry Form raised that no supply decision
    -- points at (`PLAN-scm-cs-planning-uat.md` section 3.I; the fixture sheet's `[NL]`
    -- rows). CS writes `ORDER BACK` where a delivery date belongs, and the form is the only
    -- writer that can raise it - the fulfilment board has nothing to decide about a line
    -- AutoCount has closed. The ROW states its own item and location, and those are what
    -- this leg reads, whether or not it also names a sales-order line. Without it the
    -- fourteen instructions on SO381895's first two forms are raised, shown to purchasing,
    -- and invisible to the plan that decides what to buy.
    --
    -- Joined on the CODE and the company together, which is exactly what
    -- `uq_products_company_product_code` / `uq_warehouses_company_warehouse_code` make
    -- unique - a code alone would multiply the row once per company holding the same SKU.
    --
    -- INNER on `products`, because a row naming an item this system does not hold is demand
    -- for nothing and there is no product to attribute it to. LEFT on `warehouses`, because
    -- a row that names no location, or one we do not hold, is still demand - it comes out
    -- with a NULL warehouse, which every reader joins on `(product, warehouse)` and so
    -- matches nowhere. Counted at no location rather than invented at one, and visible in
    -- the view rather than dropped from it.
    --
    -- `supply_decision_id IS NULL` keeps this leg disjoint from the CONFIRMED leg above:
    -- a row a CS decision points at is counted there, at the core line's product and
    -- location, and every OTHER raised row is counted here at its own. The leg used to
    -- demand `so_line_id IS NULL` as well, because a row naming a book line was already
    -- counted by the SHEET leg; P3 retired that leg, and the condition would now DELETE
    -- such a row from planning instead of de-duplicating it - the form raises ORDER BACK
    -- rows carrying an `so_line_id` (`project_order_inquiry_import_service`), and they are
    -- demand like any other.
    --
    -- The `NOT EXISTS` below keeps it disjoint from the BOOK leg, which is the other half
    -- of the same worry and the half P3 opened: the book still speaks for retail, so a
    -- decision-less row naming a RETAIL line would be counted twice, once as the line and
    -- once as the row. Stated as "the book leg does not count this line" - the same four
    -- openness conditions the book leg applies, plus its class test - rather than as "the
    -- line is project": a row whose line is unreconciled, closed or fully delivered is
    -- counted by nothing else, and a class test alone would drop it.
    SELECT fp.id AS product_id,
           fw.id AS warehouse_id,
           {_OWED_FORM_SQL} AS project_qty,
           {_OWED_FORM_SQL} AS project_confirmed_qty,
           0::numeric AS retail_qty,
           0::numeric AS unclassified_qty
    FROM projects.order_inquiry_rows oir
    JOIN products fp
      ON fp.product_code = oir.item_code
     AND fp.company_id = oir.company_id
    LEFT JOIN warehouses fw
      ON fw.warehouse_code = oir.stock_location
     AND fw.company_id = oir.company_id
    {so_join}
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) flk ON TRUE
    WHERE oir.supply_decision_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM projects.sales_order_lines fpsl
          JOIN sales_order_lines fsol ON fsol.id = fpsl.core_sales_order_line_id
          JOIN sales_orders fso ON fso.id = fsol.sales_order_id
          WHERE fpsl.id = oir.so_line_id
            AND fso.demand_class IS DISTINCT FROM 'project'
            AND fso.status = 'open'
            AND fsol.line_status = 'open'
            AND fsol.purchasing_status <> 'covered'
            AND GREATEST(COALESCE(fsol.qty_required, fsol.qty_ordered)
                       - COALESCE(fsol.qty_delivered, 0), 0) > 0)
      AND oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.ack_state IN ({_PLANNED_ACK_SQL})
      AND oir.qty > 0
      -- Ruling 6, form leg: the same rule again. R1: uncapped, same as the confirmed leg.
      AND {_OWED_FORM_SQL} > 0
      -- Planning horizon, form leg: the same rule again. An ORDER BACK row states no
      -- date at all, so it is always in - unscheduled demand is still demand.
      AND (CAST(:horizon AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date <= CAST(:horizon AS date))
      -- Planning window START (S4): same rule, other side.
      AND (CAST(:horizon_start AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date >= CAST(:horizon_start AS date))"""

    if demand_class == "project":
        legs = [confirmed_leg, form_leg]
    elif demand_class == "retail":
        legs = [retail_leg]
    else:
        legs = [retail_leg, confirmed_leg, form_leg]
    legs_sql = "\n    UNION ALL\n".join(leg.strip("\n") for leg in legs)

    return f"""
WITH legs AS (
{legs_sql}
)
SELECT product_id,
       warehouse_id,
       SUM(project_qty + retail_qty + unclassified_qty) AS committed,
       SUM(project_qty) AS project_committed,
       SUM(retail_qty) AS retail_committed,
       SUM(unclassified_qty) AS unclassified_committed,
       SUM(project_confirmed_qty) AS project_confirmed_committed
FROM legs
GROUP BY product_id, warehouse_id
"""


def horizon_project_need_dates_sql() -> str:
    """WHEN the project quantity `horizon_committed_select_sql` counts is owed, per product.

    The DATE companion of that function's two project legs, and a companion is the point: a
    date read under looser conditions than the quantity describes need the plan is not
    planning for. That is what it did - the date came off `summary_order_service`, whose own
    rule is the Order Summary's (`state = 'raised'` alone, no remainder test, no horizon), so
    a row already placed on a purchase order could still make a product rank as urgent.

    So the predicates below are the project legs' own, condition for condition: the ACTIVE
    decision, `verb IN ('ORDER','ORDER_BACK')`, `state IN ('raised','partly_linked')`, the
    un-linked remainder `oir.qty > COALESCE(linked, 0)`, the form leg's `supply_decision_id
    IS NULL` and its retail-shadow `NOT EXISTS`, and the same `:horizon` bind in each. A row
    the quantity leg excludes contributes no date, and a row it counts contributes its own.

    A THIRD copy of the leg shape, and it lives here for the reason the second one does (see
    `horizon_committed_select_sql`): `COMMITTED_V_SQL` is frozen for the migration/downgrade
    pair and must stay copy-pasteable, so the variants sit beside it where the next person
    changing one finds the others.

    The confirmed leg dates off the inquiry row and falls back to the reconciled core line's
    required date, which is the rule the Order Summary established and the only one the two
    documents can both answer. `MIN` over an all-NULL group is NULL and is dropped: a Buy
    nobody has dated contributes no date at all, rather than a guess.
    """
    return f"""
WITH legs AS (
    SELECT sol.product_id,
           COALESCE(oir.delivery_date, sol.required_date) AS needed
    FROM projects.order_inquiry_rows oir
    JOIN projects.so_supply_decisions d
      ON d.id = oir.supply_decision_id
     AND d.state = 'active'
    JOIN projects.sales_order_lines psl ON psl.id = oir.so_line_id
    JOIN sales_order_lines sol ON sol.id = psl.core_sales_order_line_id
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) lk ON TRUE
    WHERE oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.qty > 0
      -- Ruling 6: a fully bundled row is not owed, so it names no date either. R1: no
      -- other cap either.
      AND {_OWED_SQL} > 0
      AND (CAST(:horizon AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date <= CAST(:horizon AS date))
    UNION ALL
    SELECT fp.id AS product_id,
           oir.delivery_date AS needed
    FROM projects.order_inquiry_rows oir
    JOIN products fp
      ON fp.product_code = oir.item_code
     AND fp.company_id = oir.company_id
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.qty), 0) AS linked
        FROM projects.order_inquiry_links l
        WHERE l.row_id = oir.id
    ) flk ON TRUE
    WHERE oir.supply_decision_id IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM projects.sales_order_lines fpsl
          JOIN sales_order_lines fsol ON fsol.id = fpsl.core_sales_order_line_id
          JOIN sales_orders fso ON fso.id = fsol.sales_order_id
          WHERE fpsl.id = oir.so_line_id
            AND fso.demand_class IS DISTINCT FROM 'project'
            AND fso.status = 'open'
            AND fsol.line_status = 'open'
            AND fsol.purchasing_status <> 'covered'
            AND GREATEST(COALESCE(fsol.qty_required, fsol.qty_ordered)
                       - COALESCE(fsol.qty_delivered, 0), 0) > 0)
      AND oir.verb IN ('ORDER', 'ORDER_BACK')
      AND oir.state IN ('raised', 'partly_linked')
      {NOT_REDIRECTED_SQL}
      AND oir.qty > 0
      AND {_OWED_FORM_SQL} > 0
      AND (CAST(:horizon AS date) IS NULL OR oir.delivery_date IS NULL
           OR oir.delivery_date <= CAST(:horizon AS date))
)
SELECT product_id, MIN(needed) AS needed
FROM legs
WHERE needed IS NOT NULL
GROUP BY product_id
"""


# =========================================================================== #
# A3/C2 (PLAN-order-sheet-oi-reports-22sep.md): the run's own Start Plan scope, read as
# ROWS rather than as a netted quantity - `_project_inquiry_map` (order sheet, Lane A)
# aggregates these into its months/customers buckets; the OI worksheet (Lane C) prints
# them as-is. One SELECT, so the sheet and the worksheet can never list a different row
# set for the same run.
# =========================================================================== #

def run_scope_oi_rows(
    db,
    product_ids: Optional[list[str]],
    *,
    so_numbers: Optional[list[str]] = None,
    horizon_start: Optional[date] = None,
    horizon: Optional[date] = None,
) -> list[dict]:
    """Every OI row the ENGINE would buy for, in the run's own scope, one dict per row:
    ``{row_id, product_id, so_number, qty, delivery_date, customer_name, project_title,
    project_label, is_pre_order}``.

    THE PREDICATE (A3, captain ruling 23 Sep after review): the CONFIRMED leg's own
    predicate in `horizon_committed_select_sql` - ``verb IN ('ORDER', 'ORDER_BACK')``,
    ``state IN ('raised', 'partly_linked')``, not redirected (`NOT_REDIRECTED_SQL`),
    ``ack_state IN PLANNED_ACK_STATES`` (acknowledged or changed - an awaiting row is a
    count on the plan page, never something to buy against), owed (`_OWED_SQL`, qty minus
    linked minus bundled) `> 0`, printed `qty` = that OWED figure, NEVER the row's raw
    `qty`. The plan's first cut read `state <> cancelled, ack_state <> rejected`, which on
    the 21 Sep prod copy admitted 2,006 placed + 5,713 actioned + 474 awaiting-ack + 12
    redirected rows the engine never buys for, so Project qty ran ABOVE Buy instead of
    tallying with it - a placed, actioned, awaiting-ack, rejected, redirected or fully
    linked row is absent under this predicate; a half-linked row counts its owed half only.

    `so_supply_decisions` still plays NO part (owner ruling 22 Sep - "read from OI, don't
    care about supply decision"): this is the confirmed leg's WHERE clause WITHOUT its
    ``JOIN so_supply_decisions ... AND state = 'active'`` - a row with `supply_decision_id
    IS NULL` (the CS form leg, never confirmed on the fulfilment board) still counts as
    long as it is otherwise in the engine's own buying scope, which is the whole SRTWB248
    story the plan's "Measured" section names.

    Product read off the CORE line, ``sol.product_id`` - the same reconciled line the
    confirmed leg requires via `psl.core_sales_order_line_id`, NOT `psl.product_id` (the
    project-side line's own, looser copy of the fact, which the confirmed leg never reads
    either). A row whose project line has not been reconciled to a core line drops out
    entirely, the same shape the confirmed leg already has.

    ``so_numbers`` (TRUTHY - fix round 4, Lane C review) narrows to `sales_orders.
    so_number` - the same COLUMN `horizon_committed_select_sql(so_scoped=True)` binds
    `:so_numbers` against via `_SO_SCOPE_JOIN_SQL`, reached by a DIFFERENT join path here
    (off the reconciled core line's own `sales_order_id`, rather than `_SO_SCOPE_JOIN_
    SQL`'s walk through `order_inquiries -> project_sales_orders.so_id`) - the two paths
    should always agree for a row this function admits at all (both name the SAME core
    sales order), but this is not "the same query", only the same column reached two
    ways. Never `order_inquiry_worklist_service._SO_NUMBER`'s `COALESCE(autocount_doc_no,
    provisional_ref)`, which is a DISPLAY label for a project SO that may never have been
    adopted, while the run's own `so_numbers` are picked off the candidate-orders endpoint,
    which lists the CORE `sales_orders.so_number` (`reorder_runs.get_candidate_orders`).

    ``None`` AND an empty list ``[]`` BOTH apply no filter (every product-scoped row is
    in scope) - the SAME `bool(so_numbers)` reading `reorder_run_service._planning_rows`
    gives its own `so_scoped` flag. `reorder_run_service.create_run` stamps
    `so_numbers = []` for a Project run where the buyer picked no order at all
    (`stored_so_numbers = []` when `demand_class == "project"` and nothing was ticked),
    and such a run still plans EVERY project order in range - `_planning_rows` reads that
    `[]` as "not narrowed" for exactly that reason. An earlier cut of this function read
    `[]` as `= ANY('{}')` (matches nothing), which printed Project qty 0 on precisely the
    runs `_planning_rows` bought for - the bug fix round 4 found. A caller that means
    "match nothing" narrows `product_ids` instead, which this function DOES read as
    empty-means-nothing - `product_ids=[]` returns `[]` immediately (the early
    `if product_ids is not None and not product_ids: return []`); `product_ids=None`
    still means no filter at all (Lane C fix round 1), the ONE asymmetry between the two
    parameters and the reason each is documented on its own terms rather than as a pair.

    ``horizon_start``/``horizon`` narrow to `delivery_date` inside `[horizon_start,
    horizon]`; a row with no delivery date is always in scope, whatever either bound is.

    ``is_pre_order`` is `projects.sales_orders.is_pre_order` off the row's own project SO,
    so a caller can print the same "PRE-ORDER" suffix `project_customer_label` gives the OI
    worklist, without a second query.

    Raw SQL, company-scoped by hand on the core sales order (`company_sql_predicate`) -
    `OrderInquiryRow` is company-scoped but a raw `text()` bypasses the ORM's own isolation
    listener.
    """
    if product_ids is not None and not product_ids:
        return []
    co, co_params = company_sql_predicate(db, "so.company_id", param_prefix="rsoi")
    so_clause = "AND so.so_number = ANY(:so_numbers)\n          " if so_numbers else ""
    rows = db.execute(text(f"""
        SELECT oir.id::text AS row_id, sol.product_id::text AS product_id,
               so.so_number AS so_number, ({_OWED_SQL}) AS qty,
               oir.delivery_date AS delivery_date,
               c.customer_name AS customer_name, pj.title AS project_title,
               so.project_label AS project_label, pso.is_pre_order AS is_pre_order
        FROM projects.order_inquiry_rows oir
        JOIN projects.sales_order_lines psl ON psl.id = oir.so_line_id
        JOIN sales_order_lines sol ON sol.id = psl.core_sales_order_line_id
        JOIN projects.sales_orders pso ON pso.id = psl.project_sales_order_id
        LEFT JOIN projects.projects pj ON pj.id = pso.project_id
        JOIN sales_orders so ON so.id = sol.sales_order_id
        LEFT JOIN customers c ON c.id = so.customer_id
        LEFT JOIN LATERAL (
            SELECT COALESCE(SUM(l.qty), 0) AS linked
            FROM projects.order_inquiry_links l
            WHERE l.row_id = oir.id
        ) lk ON TRUE
        WHERE oir.verb IN ('ORDER', 'ORDER_BACK')
          AND oir.state IN ('raised', 'partly_linked')
          {NOT_REDIRECTED_SQL}
          AND oir.ack_state IN ({_PLANNED_ACK_SQL})
          AND oir.qty > 0
          AND ({_OWED_SQL}) > 0
          AND (CAST(:pids AS text[]) IS NULL OR sol.product_id::text = ANY(:pids))
          {so_clause}AND (CAST(:horizon_start AS date) IS NULL OR oir.delivery_date IS NULL
               OR oir.delivery_date >= CAST(:horizon_start AS date))
          AND (CAST(:horizon AS date) IS NULL OR oir.delivery_date IS NULL
               OR oir.delivery_date <= CAST(:horizon AS date))
          {("AND " + co) if co else ""}
    """), {
        "pids": [str(p) for p in product_ids] if product_ids is not None else None,
        "so_numbers": list(so_numbers) if so_numbers else [],
        "horizon_start": horizon_start,
        "horizon": horizon,
        **co_params,
    }).fetchall()
    return [
        {
            "row_id": r.row_id,
            "product_id": r.product_id,
            "so_number": r.so_number,
            "qty": float(r.qty or 0),
            "delivery_date": r.delivery_date,
            "customer_name": r.customer_name,
            "project_title": r.project_title,
            "project_label": r.project_label,
            "is_pre_order": bool(r.is_pre_order),
        }
        for r in rows
    ]
