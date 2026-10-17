"""Opportunities logged by salespeople, one copy of every rule for both routers
(plan 3.4, 3.5; section 16, slice S2).

Stages live on the status engine (`app.modules.sales.status_entities`, entity type
`sales_opportunity`); nothing here re-implements the graph, it only calls
`app.services.status_service`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Union

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.access import RespondContact
from app.models.lookup import LookupOption, LookupSet
from app.models.order import Customer, SalesOrder
from app.models.product import Product
from app.models.sales import SalesOpportunity, SalesOpportunityLine
from app.models.sales_agent import SalesAgent
from app.models.status import Status
from app.models.user import User
from app.services import status_service
from app.services.dealer_kit.pricing import flyer_price
from app.services.error_handler import AppException
from app.services.numbering_service import NumberingService
from app.services.project_link import attach_project_display, check_project_link
from app.services.sales import team_service

SALES_OPPORTUNITY_ENTITY = "sales_opportunity"
NUMBERING_DOC_TYPE = "sales_opportunity"
LOST_REASON_SET_KEY = "sales_opportunity_lost_reasons"


def _unprocessable(message: str, code: str) -> AppException:
    return AppException(status_code=422, message=message, code=code)


def _not_found() -> AppException:
    return AppException(status_code=404, message="Sales opportunity not found.", code="NOT_FOUND")


#: One agent id (the CRM) or every active agent a portal contact is linked to
#: (PORTAL-MULTI-AGENT, owner 16 Oct 2026: the portal answers for all of them together).
AgentIds = Union[str, Iterable[str]]


def _agent_id_list(agent_ids: AgentIds) -> List[str]:
    return [agent_ids] if isinstance(agent_ids, str) else list(agent_ids)


def _normalize(value: Optional[str]) -> str:
    """Lower-cased, runs of whitespace collapsed, ends trimmed (section 16, S2-15)."""
    return " ".join((value or "").split()).lower()


def _normalize_expr(column):
    from sqlalchemy import func as sa_func

    return sa_func.lower(sa_func.regexp_replace(sa_func.btrim(column), r"\s+", " ", "g"))


# --------------------------------------------------------------------------------------
# Customer or prospect (S2-15)
# --------------------------------------------------------------------------------------


def customer_options(db: Session, *, q: Optional[str], sales_agent_id: Optional[AgentIds] = None) -> dict:
    """Portal: `sales_agent_id` restricts `items` to the contact's agents' own customers (S2-5).
    CRM: `sales_agent_id` is None, so every customer of the company is offered.

    `prospect`/`blocked` always search ALL customers of the company, never just the
    agent's own - a portal salesperson must be told when the name they typed belongs to
    somebody else's customer (S2-15), which the agent-scoped `items` list would hide.
    """
    q = (q or "").strip()
    agent_ids = None if sales_agent_id is None else _agent_id_list(sales_agent_id)
    query = db.query(Customer)
    if agent_ids is not None:
        query = query.filter(Customer.sales_agent_id.in_(agent_ids))
    if q:
        # Normalised on both sides (S2-15): a typed run of extra spaces or a case
        # difference must still find "Seri Indah Renovation", the same tolerance the
        # exact-match check below applies.
        normalized_like = f"%{_normalize(q)}%"
        query = query.filter(
            or_(
                _normalize_expr(Customer.customer_code).like(normalized_like),
                _normalize_expr(Customer.customer_name).like(normalized_like),
            )
        )
    customers = query.order_by(Customer.customer_name).limit(50).all()
    items = [
        {"customer_id": c.id, "customer_code": c.customer_code, "customer_name": c.customer_name}
        for c in customers
    ]

    prospect = None
    blocked = None
    if q:
        normalized_q = _normalize(q)
        exact_matches = (
            db.query(Customer).filter(_normalize_expr(Customer.customer_name) == normalized_q).all()
        )
        if not exact_matches:
            prospect = {"name": q}
        elif agent_ids is not None and not any(
            c.sales_agent_id in agent_ids for c in exact_matches
        ):
            # Nit (Phase 3): blocked only when the exact name belongs to NO customer of
            # THIS agent's own - two customers can share a normalized name (a real prod
            # case elsewhere in this app), and an unordered `.first()` used to pick
            # whichever row Postgres felt like, occasionally the wrong one, blocking an
            # agent from a name they actually already own.
            other = exact_matches[0]
            blocked = {
                "name": other.customer_name,
                "message": f"{other.customer_name} is another agent's customer",
            }
    return {"items": items, "prospect": prospect, "blocked": blocked}


def _validate_customer_or_prospect(
    db: Session, *, customer_id: Optional[str], prospect_name: Optional[str]
) -> None:
    if customer_id and prospect_name:
        raise _unprocessable(
            "Pick a customer or type a prospect name, not both.",
            "CUSTOMER_AND_PROSPECT_TOGETHER",
        )
    if not customer_id and not prospect_name:
        raise _unprocessable(
            "A customer or a prospect name is required.", "CUSTOMER_OR_PROSPECT_REQUIRED"
        )
    if customer_id:
        _assert_customer_exists(db, customer_id)
    if prospect_name:
        normalized = _normalize(prospect_name)
        exact = (
            db.query(Customer).filter(_normalize_expr(Customer.customer_name) == normalized).first()
        )
        if exact is not None:
            raise _unprocessable(
                f'"{prospect_name}" is already a customer.', "PROSPECT_IS_A_CUSTOMER"
            )


def _assert_customer_is_agents_own(
    db: Session, *, customer_id: str, sales_agent_id: AgentIds
) -> None:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None or customer.sales_agent_id not in _agent_id_list(sales_agent_id):
        raise _unprocessable("That customer is not yours.", "CUSTOMER_NOT_YOURS")


def agent_owning_customer(
    db: Session, agents: List[SalesAgent], customer_id: Optional[str]
) -> Optional[SalesAgent]:
    """Portal: the linked agent whose customer this is, or ``None`` (no customer, or
    somebody else's - `CUSTOMER_NOT_YOURS` answers that one)."""
    if not customer_id:
        return None
    owner_id = db.query(Customer.sales_agent_id).filter(Customer.id == customer_id).scalar()
    return next((agent for agent in agents if agent.id == owner_id), None)


def default_portal_agent(db: Session, agents: List[SalesAgent]) -> SalesAgent:
    """Portal: the agent a prospect (no customer) goes to - the first by code that the
    request's company scope can see (shared, or in the scope), else the first by code."""
    from app.models.base import get_company_scope

    scope = get_company_scope(db)

    def visible(agent: SalesAgent) -> bool:
        if agent.company_id is None or scope is None:
            return True
        return isinstance(scope, frozenset) and agent.company_id in scope

    return next((agent for agent in agents if visible(agent)), agents[0])


def _assert_customer_exists(db: Session, customer_id: str) -> None:
    """N2 (Phase 3): a customer_id that names no row is a 422, not a silent FK-less
    write - `restrict_customer_to_agent_id` already proves existence for the portal
    path, but the CRM path (no restriction) never checked at all."""
    exists = db.query(Customer.id).filter(Customer.id == customer_id).first()
    if exists is None:
        raise _unprocessable("That customer was not found.", "CUSTOMER_NOT_FOUND")


def _assert_agent_visible(db: Session, *, sales_agent_id: str, company_id: str) -> None:
    """N2 (Phase 3): sales_agent_id must be a visible agent - active, and either shared
    (no company) or in this opportunity's own company."""
    agent = (
        db.query(SalesAgent)
        .filter(
            SalesAgent.id == sales_agent_id,
            SalesAgent.is_active.is_(True),
            or_(SalesAgent.company_id.is_(None), SalesAgent.company_id == company_id),
        )
        .first()
    )
    if agent is None:
        raise _unprocessable("That sales agent is not available.", "SALES_AGENT_NOT_FOUND")


# --------------------------------------------------------------------------------------
# Lines (S2-16)
# --------------------------------------------------------------------------------------


def _replace_lines(db: Session, opportunity: SalesOpportunity, lines: List[dict]) -> None:
    product_ids = [line["product_id"] for line in lines]
    list_prices: dict = {}
    if product_ids:
        # N6 (Phase 3): an inactive product counts as "not found" here too - a
        # discontinued product has no business being added to a NEW line.
        list_prices = {
            row[0]: row[1]
            for row in db.query(Product.id, Product.list_price)
            .filter(Product.id.in_(product_ids), Product.is_active.is_(True))
            .all()
        }
        missing = [pid for pid in product_ids if pid not in list_prices]
        if missing:
            raise _unprocessable("One or more products were not found.", "UNKNOWN_PRODUCT")

    opportunity.lines.clear()
    db.flush()
    for index, line in enumerate(lines):
        if "unit_price" in line:
            # Typed, or null for "no price" (a blank field in the form).
            unit_price = line["unit_price"]
        else:
            # F7 (fix round 2): the price the dealer flyer prints, zero meaning none.
            unit_price = flyer_price(list_prices[line["product_id"]])
        opportunity.lines.append(
            SalesOpportunityLine(
                company_id=opportunity.company_id,
                product_id=line["product_id"],
                qty=line["qty"],
                unit_price=unit_price,
                sort_order=index,
            )
        )


def _line_amount(line) -> Optional[Decimal]:
    if line.unit_price is None:
        return None
    return (Decimal(line.qty) * Decimal(line.unit_price)).quantize(Decimal("0.01"))


def _serialize_lines(db: Session, opportunity: SalesOpportunity) -> List[dict]:
    lines = list(opportunity.lines or [])
    if not lines:
        return []
    products = {
        p.id: p
        for p in db.query(Product).filter(Product.id.in_([l.product_id for l in lines])).all()
    }
    out = []
    for line in lines:
        product = products.get(line.product_id)
        out.append(
            {
                "id": line.id,
                "product_id": line.product_id,
                "product_code": product.product_code if product else "",
                "product_name": product.product_name if product else "",
                "qty": line.qty,
                "unit_price": line.unit_price,
                "line_amount": _line_amount(line),
            }
        )
    return out


# --------------------------------------------------------------------------------------
# Create / update / delete / get
# --------------------------------------------------------------------------------------


def create_opportunity(
    db: Session,
    *,
    company_id: str,
    payload: dict,
    sales_agent_id: Optional[str],
    source: str,
    created_by_user_id: Optional[str] = None,
    created_by_contact_id: Optional[str] = None,
    restrict_customer_to_agent_id: Optional[AgentIds] = None,
    stamp_agent_from_customer: bool = False,
    project_checked: bool = False,
) -> SalesOpportunity:
    customer_id = payload.get("customer_id")
    prospect_name = payload.get("prospect_name")
    _validate_customer_or_prospect(db, customer_id=customer_id, prospect_name=prospect_name)
    if restrict_customer_to_agent_id is not None and customer_id:
        _assert_customer_is_agents_own(
            db, customer_id=customer_id, sales_agent_id=restrict_customer_to_agent_id
        )
    if sales_agent_id:
        # N2 (Phase 3): only the CRM path can name an agent directly - the portal always
        # passes the requesting agent's own id, trivially visible, but this still guards
        # against a stale/foreign id reaching either path.
        _assert_agent_visible(db, sales_agent_id=sales_agent_id, company_id=company_id)
    if sales_agent_id is None and stamp_agent_from_customer and customer_id:
        # CRM only (S2-7): stamped from the customer, null accepted. The portal always
        # passes a concrete agent (resolved from the token before this is ever called).
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is not None:
            sales_agent_id = customer.sales_agent_id

    # FORMS-PROJECT: only a project visible in this session's company scope, unless the
    # portal route already checked it against the contact's own companies (wider than the
    # one company this create runs under, review S2).
    project_id = (
        payload.get("project_id") or None
        if project_checked
        else check_project_link(db, payload.get("project_id"))
    )

    initial = status_service.initial_status(db, SALES_OPPORTUNITY_ENTITY)
    opportunity_no = NumberingService(db).get_next_number(NUMBERING_DOC_TYPE, commit_rule=False)
    if not opportunity_no:
        raise _unprocessable(
            "No enabled numbering rule for sales opportunities. Configure one under "
            "Settings before logging one.",
            "NUMBERING_RULE_MISSING",
        )

    opportunity = SalesOpportunity(
        company_id=company_id,
        opportunity_no=opportunity_no,
        customer_id=customer_id,
        prospect_name=prospect_name,
        project_id=project_id,
        sales_agent_id=sales_agent_id,
        title=payload["title"],
        status_id=initial.id,
        outcome="open",
        expected_amount=payload.get("expected_amount") or 0,
        expected_close_date=payload["expected_close_date"],
        source=source,
        created_by_user_id=created_by_user_id,
        created_by_contact_id=created_by_contact_id,
        stage_changed_at=datetime.utcnow(),
    )
    db.add(opportunity)
    db.flush()
    _replace_lines(db, opportunity, payload.get("lines") or [])
    db.flush()
    if payload.get("expected_amount") is None:
        # F7 (fix round 2): nothing typed, so the lines are the estimate.
        opportunity.expected_amount = _lines_sum(opportunity)
        db.flush()
    return opportunity


#: The largest figure `expected_amount` (numeric(15,2)) holds.
_MAX_AMOUNT = Decimal("9999999999999.99")


def _lines_sum(opportunity: SalesOpportunity) -> Decimal:
    total = sum(
        (amount for amount in map(_line_amount, opportunity.lines) if amount is not None),
        Decimal("0"),
    )
    if total > _MAX_AMOUNT:
        raise _unprocessable(
            "The lines add up to more than an opportunity can hold.", "AMOUNT_TOO_LARGE"
        )
    return total


def _validate_lost_reason(db: Session, lost_reason: Optional[str]) -> str:
    if not lost_reason:
        raise _unprocessable(
            "A reason is required to mark this opportunity Lost.", "LOST_REASON_REQUIRED"
        )
    valid = (
        db.query(LookupOption)
        .join(LookupSet, LookupSet.id == LookupOption.set_id)
        .filter(
            LookupSet.set_key == LOST_REASON_SET_KEY,
            LookupOption.value == lost_reason,
            LookupOption.is_active.is_(True),
        )
        .first()
    )
    if valid is None:
        raise _unprocessable(
            f"'{lost_reason}' is not a configured lost reason.", "LOST_REASON_REQUIRED"
        )
    return lost_reason


def _apply_status_change(
    db: Session,
    opportunity: SalesOpportunity,
    *,
    new_status_id: str,
    lost_reason: Optional[str],
    sales_order_id: Optional[str],
) -> None:
    status_service.assert_transition_allowed(
        db, SALES_OPPORTUNITY_ENTITY, opportunity.status_id, new_status_id
    )
    target = db.query(Status).filter(Status.id == new_status_id).first()

    if target.key == "lost":
        opportunity.lost_reason = _validate_lost_reason(db, lost_reason)
    elif target.key == "won" and sales_order_id:
        # N3 (Phase 3): the order must belong to THIS opportunity's own company and be
        # live - a cancelled order or one from another company is 422, same code as "no
        # such order", so neither leaks which orders exist elsewhere.
        order = (
            db.query(SalesOrder)
            .filter(
                SalesOrder.id == sales_order_id,
                SalesOrder.company_id == opportunity.company_id,
                SalesOrder.status != "cancelled",
            )
            .first()
        )
        if order is None:
            raise _unprocessable("Sales order not found.", "SALES_ORDER_NOT_FOUND")
        if opportunity.customer_id is not None and order.customer_id != opportunity.customer_id:
            raise _unprocessable(
                "That sales order belongs to a different customer.",
                "SALES_ORDER_OTHER_CUSTOMER",
            )
        if opportunity.customer_id is None:
            # Won on a prospect: the buyer is now known (plan 3.5, section 16). The
            # prospect name is kept as history of how it started.
            opportunity.customer_id = order.customer_id
        opportunity.sales_order_id = order.id

    opportunity.status_id = target.id
    opportunity.outcome = "won" if target.key == "won" else "lost" if target.key == "lost" else "open"
    opportunity.stage_changed_at = datetime.utcnow()


def update_opportunity(
    db: Session,
    opportunity: SalesOpportunity,
    payload: dict,
    *,
    restrict_customer_to_agent_id: Optional[AgentIds] = None,
    project_checked: bool = False,
) -> SalesOpportunity:
    # N7 (Phase 3): once an opportunity is Won or Lost, nothing about it moves again -
    # not a field, not another stage - on either side. A stage move away from a terminal
    # status is already refused by the transition graph (no outgoing edges), but a plain
    # field edit (title, amount, lines, ...) has no such graph to catch it.
    if opportunity.outcome != "open" and payload:
        raise _unprocessable(
            "This opportunity is closed and cannot be edited.", "OPPORTUNITY_CLOSED"
        )

    if "customer_id" in payload or "prospect_name" in payload:
        customer_id = payload.get("customer_id", opportunity.customer_id)
        prospect_name = payload.get("prospect_name", opportunity.prospect_name)
        _validate_customer_or_prospect(db, customer_id=customer_id, prospect_name=prospect_name)
        if restrict_customer_to_agent_id is not None and customer_id:
            _assert_customer_is_agents_own(
                db, customer_id=customer_id, sales_agent_id=restrict_customer_to_agent_id
            )
        opportunity.customer_id = customer_id
        opportunity.prospect_name = prospect_name

    if "title" in payload:
        opportunity.title = payload["title"]
    if "project_id" in payload:
        opportunity.project_id = (
            payload["project_id"] or None
            if project_checked
            else check_project_link(db, payload["project_id"], current=opportunity.project_id)
        )
    if payload.get("expected_amount") is not None:
        opportunity.expected_amount = payload["expected_amount"]
    if "expected_close_date" in payload:
        opportunity.expected_close_date = payload["expected_close_date"]
    if "sales_agent_id" in payload:
        if payload["sales_agent_id"]:
            _assert_agent_visible(
                db, sales_agent_id=payload["sales_agent_id"], company_id=opportunity.company_id
            )
        opportunity.sales_agent_id = payload["sales_agent_id"]

    if "status_id" in payload:
        _apply_status_change(
            db,
            opportunity,
            new_status_id=payload["status_id"],
            lost_reason=payload.get("lost_reason"),
            sales_order_id=payload.get("sales_order_id"),
        )

    if "lines" in payload:
        _replace_lines(db, opportunity, payload.get("lines") or [])

    if "expected_amount" in payload and payload["expected_amount"] is None:
        # An explicit null is "the lines are the estimate", the create rule (F7).
        db.flush()
        opportunity.expected_amount = _lines_sum(opportunity)

    db.flush()
    return opportunity


def delete_opportunity(db: Session, opportunity: SalesOpportunity) -> None:
    db.delete(opportunity)
    db.flush()


def delete_opportunity_by_id(db: Session, opportunity_id: str) -> None:
    """The parked `sales_opportunity.delete` action's handler (Phase 3 fix B2). Already
    gone is not an error - same rule `team_service.delete_team_by_id` follows."""
    opportunity = db.query(SalesOpportunity).filter(SalesOpportunity.id == opportunity_id).first()
    if opportunity is not None:
        delete_opportunity(db, opportunity)


def get_opportunity_or_404(db: Session, opportunity_id: str) -> SalesOpportunity:
    opportunity = db.query(SalesOpportunity).filter(SalesOpportunity.id == opportunity_id).first()
    if opportunity is None:
        raise _not_found()
    return opportunity


def get_opportunity_for_agent_or_404(
    db: Session, opportunity_id: str, sales_agent_id: AgentIds
) -> SalesOpportunity:
    opportunity = (
        db.query(SalesOpportunity)
        .filter(
            SalesOpportunity.id == opportunity_id,
            SalesOpportunity.sales_agent_id.in_(_agent_id_list(sales_agent_id)),
        )
        .first()
    )
    if opportunity is None:
        raise _not_found()
    return opportunity


# --------------------------------------------------------------------------------------
# List (S2-8)
# --------------------------------------------------------------------------------------


def list_opportunities(
    db: Session,
    *,
    query: Optional[str] = None,
    status_id: Optional[str] = None,
    sales_agent_id: Optional[AgentIds] = None,
    customer_id: Optional[str] = None,
    close_from=None,
    close_to=None,
    outcome: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    sort: str = "created_at",
    dir: str = "desc",
    project_id: Optional[str] = None,
):
    q = db.query(SalesOpportunity)
    if query:
        # Nit (Phase 3): a reader searching "Kedai Mine" should find the opportunity by
        # its customer's name, not just by title/number - a correlated subquery on
        # Customer.id (rather than a join) keeps this a filter, not a row multiplier.
        like = f"%{query}%"
        customer_match = (
            db.query(Customer.id)
            .filter(Customer.id == SalesOpportunity.customer_id, Customer.customer_name.ilike(like))
            .exists()
        )
        q = q.filter(
            or_(
                SalesOpportunity.title.ilike(like),
                SalesOpportunity.opportunity_no.ilike(like),
                SalesOpportunity.prospect_name.ilike(like),
                customer_match,
            )
        )
    if status_id:
        q = q.filter(SalesOpportunity.status_id == status_id)
    if isinstance(sales_agent_id, str):
        if sales_agent_id:
            q = q.filter(SalesOpportunity.sales_agent_id == sales_agent_id)
    elif sales_agent_id is not None:
        # A list (the portal's linked agents) filters even when empty: it matches nothing.
        q = q.filter(SalesOpportunity.sales_agent_id.in_(list(sales_agent_id)))
    if customer_id:
        q = q.filter(SalesOpportunity.customer_id == customer_id)
    if project_id:
        q = q.filter(SalesOpportunity.project_id == str(project_id))
    if close_from:
        q = q.filter(SalesOpportunity.expected_close_date >= close_from)
    if close_to:
        q = q.filter(SalesOpportunity.expected_close_date <= close_to)
    if outcome:
        q = q.filter(SalesOpportunity.outcome == outcome)

    total = q.count()
    sort_columns = {
        "created_at": SalesOpportunity.created_at,
        "expected_close_date": SalesOpportunity.expected_close_date,
        "expected_amount": SalesOpportunity.expected_amount,
        "title": SalesOpportunity.title,
        "opportunity_no": SalesOpportunity.opportunity_no,
    }
    sort_col = sort_columns.get(sort, SalesOpportunity.created_at)
    order = sort_col.desc() if dir == "desc" else sort_col.asc()
    rows = q.order_by(order).offset((page - 1) * limit).limit(limit).all()
    return rows, total


# --------------------------------------------------------------------------------------
# Meta, options
# --------------------------------------------------------------------------------------


def meta(db: Session) -> dict:
    graph = status_service.resolve_graph(db, SALES_OPPORTUNITY_ENTITY, None)
    stages = [
        {
            "id": s.id,
            "key": s.key,
            "label": s.label,
            "win_probability": s.win_probability,
            "is_active": s.is_active,
            "is_terminal": s.is_terminal,
        }
        for s in graph.statuses
    ]
    options = (
        db.query(LookupOption)
        .join(LookupSet, LookupSet.id == LookupOption.set_id)
        .filter(LookupSet.set_key == LOST_REASON_SET_KEY, LookupOption.is_active.is_(True))
        .order_by(LookupOption.sort_order)
        .all()
    )
    lost_reasons = [{"value": o.value, "label": o.label} for o in options]
    return {"stages": stages, "lost_reasons": lost_reasons}


def sales_order_options(db: Session, opportunity: SalesOpportunity, *, q: Optional[str] = None) -> List[dict]:
    """That customer's non-cancelled sales orders, newest first, `q` narrowing by order
    number the same as the prospect branch below narrows by number or customer name
    (Phase 3 fix2 nit: the customer-backed branch used to ignore `q` outright)."""
    if opportunity.customer_id:
        order_query = db.query(SalesOrder).filter(
            SalesOrder.customer_id == opportunity.customer_id,
            SalesOrder.status != "cancelled",
        )
        if q:
            order_query = order_query.filter(SalesOrder.so_number.ilike(f"%{q}%"))
        orders = order_query.order_by(SalesOrder.order_date.desc().nullslast()).limit(50).all()
    else:
        order_query = (
            db.query(SalesOrder)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
            .filter(SalesOrder.status != "cancelled")
        )
        if q:
            like = f"%{q}%"
            order_query = order_query.filter(
                or_(SalesOrder.so_number.ilike(like), Customer.customer_name.ilike(like))
            )
        orders = order_query.order_by(SalesOrder.order_date.desc().nullslast()).limit(50).all()

    customer_names: Dict[str, str] = {}
    customer_ids = {o.customer_id for o in orders if o.customer_id}
    if customer_ids:
        customer_names = {
            c.id: c.customer_name
            for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
        }
    return [
        {
            "id": o.id,
            "so_number": o.so_number,
            "customer_id": o.customer_id,
            "customer_name": customer_names.get(o.customer_id),
            "order_date": o.order_date,
        }
        for o in orders
    ]


def agent_options(db: Session, *, company_id: str) -> List[dict]:
    agents = (
        db.query(SalesAgent)
        .filter(
            or_(SalesAgent.company_id.is_(None), SalesAgent.company_id == company_id),
            SalesAgent.is_active.is_(True),
        )
        .all()
    )
    return sorted(
        (
            {"id": a.id, "code": a.sales_agent, "label": team_service.agent_label(a)}
            for a in agents
        ),
        key=lambda item: item["label"],
    )


# --------------------------------------------------------------------------------------
# Serialize (both sides, section 16's detail shape)
# --------------------------------------------------------------------------------------


def _created_by_label(db: Session, opportunity: SalesOpportunity) -> Optional[str]:
    if opportunity.created_by_contact_id:
        contact = db.query(RespondContact).filter(
            RespondContact.id == opportunity.created_by_contact_id
        ).first()
        return contact.name if contact else None
    if opportunity.created_by_user_id:
        # N5 (Phase 3): serialize() is shared by the CRM and portal routers - a raw
        # staff email must never reach the portal response, and there is no reason to
        # show a CRM caller one either. "Sorento" is what every other unnamed system
        # actor in this response shape already reads as (no per-caller branch needed).
        user = db.query(User).filter(User.id == opportunity.created_by_user_id).first()
        return user.name if user and user.name else "Sorento"
    return None


def serialize(db: Session, opportunity: SalesOpportunity) -> dict:
    customer = (
        db.query(Customer).filter(Customer.id == opportunity.customer_id).first()
        if opportunity.customer_id
        else None
    )
    agent = (
        db.query(SalesAgent).filter(SalesAgent.id == opportunity.sales_agent_id).first()
        if opportunity.sales_agent_id
        else None
    )
    status = (
        db.query(Status).filter(Status.id == opportunity.status_id).first()
        if opportunity.status_id
        else None
    )
    sales_order = (
        db.query(SalesOrder).filter(SalesOrder.id == opportunity.sales_order_id).first()
        if opportunity.sales_order_id
        else None
    )
    lost_reason_label = None
    if opportunity.lost_reason:
        option = (
            db.query(LookupOption)
            .join(LookupSet, LookupSet.id == LookupOption.set_id)
            .filter(
                LookupSet.set_key == LOST_REASON_SET_KEY,
                LookupOption.value == opportunity.lost_reason,
            )
            .first()
        )
        lost_reason_label = option.label if option else opportunity.lost_reason

    transitions = []
    if opportunity.status_id:
        for edge in status_service.available_transitions(
            db, SALES_OPPORTUNITY_ENTITY, opportunity.status_id
        ):
            to_status = db.query(Status).filter(Status.id == edge.to_status_id).first()
            if to_status is None:
                continue
            transitions.append(
                {"to_status_id": to_status.id, "key": to_status.key, "label": edge.label}
            )

    return {
        "id": opportunity.id,
        "opportunity_no": opportunity.opportunity_no,
        "title": opportunity.title,
        "customer_id": opportunity.customer_id,
        "customer_code": customer.customer_code if customer else None,
        "customer_name": customer.customer_name if customer else None,
        "prospect_name": opportunity.prospect_name,
        "sales_agent_id": opportunity.sales_agent_id,
        "sales_agent_label": team_service.agent_label(agent) if agent else None,
        "status_id": opportunity.status_id,
        "stage_key": status.key if status else None,
        "stage_label": status.label if status else None,
        "win_probability": status.win_probability if status else None,
        "outcome": opportunity.outcome,
        "expected_amount": opportunity.expected_amount,
        "expected_close_date": opportunity.expected_close_date,
        "lost_reason": opportunity.lost_reason,
        "lost_reason_label": lost_reason_label,
        "sales_order_id": opportunity.sales_order_id,
        "sales_order_no": sales_order.so_number if sales_order else None,
        "source": opportunity.source,
        "created_by_label": _created_by_label(db, opportunity),
        "created_by_contact_id": opportunity.created_by_contact_id,
        "created_at": opportunity.created_at,
        "updated_at": opportunity.updated_at,
        "stage_changed_at": opportunity.stage_changed_at,
        "lines": _serialize_lines(db, opportunity),
        "available_transitions": transitions,
        **_project_fields(db, opportunity),
    }


def _project_fields(db: Session, opportunity: SalesOpportunity) -> dict:
    body = {"project_id": opportunity.project_id}
    attach_project_display(db, [body])
    return body


def serialize_list(db: Session, opportunities: List[SalesOpportunity]) -> List[dict]:
    """The list endpoint's own serializer (reviewer should-fix 5): `serialize()` above is
    right for ONE row (a detail read, a create/update response) but is an N+1 - a lookup
    per row for customer/agent/status/order/lost-reason, another query per row for the
    status graph's transitions, another for its lines with a Product join - the moment it
    is called once per row on a page. This batch-loads every one of those in a handful of
    queries regardless of page size, and never touches `available_transitions` or `lines`
    at all: a list row has no use for either (the detail page fetches its own).
    """
    if not opportunities:
        return []

    customer_ids = {o.customer_id for o in opportunities if o.customer_id}
    agent_ids = {o.sales_agent_id for o in opportunities if o.sales_agent_id}
    status_ids = {o.status_id for o in opportunities if o.status_id}
    order_ids = {o.sales_order_id for o in opportunities if o.sales_order_id}
    lost_reasons = {o.lost_reason for o in opportunities if o.lost_reason}

    customers = (
        {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()}
        if customer_ids
        else {}
    )
    agents = (
        {a.id: a for a in db.query(SalesAgent).filter(SalesAgent.id.in_(agent_ids)).all()}
        if agent_ids
        else {}
    )
    statuses = (
        {s.id: s for s in db.query(Status).filter(Status.id.in_(status_ids)).all()}
        if status_ids
        else {}
    )
    orders = (
        {o.id: o for o in db.query(SalesOrder).filter(SalesOrder.id.in_(order_ids)).all()}
        if order_ids
        else {}
    )
    lost_reason_labels: Dict[str, str] = {}
    if lost_reasons:
        options = (
            db.query(LookupOption)
            .join(LookupSet, LookupSet.id == LookupOption.set_id)
            .filter(LookupSet.set_key == LOST_REASON_SET_KEY, LookupOption.value.in_(lost_reasons))
            .all()
        )
        lost_reason_labels = {o.value: o.label for o in options}

    contact_ids = {o.created_by_contact_id for o in opportunities if o.created_by_contact_id}
    user_ids = {o.created_by_user_id for o in opportunities if o.created_by_user_id}
    contacts = (
        {c.id: c for c in db.query(RespondContact).filter(RespondContact.id.in_(contact_ids)).all()}
        if contact_ids
        else {}
    )
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )

    def _created_by_label_batched(opportunity: SalesOpportunity) -> Optional[str]:
        if opportunity.created_by_contact_id:
            contact = contacts.get(opportunity.created_by_contact_id)
            return contact.name if contact else None
        if opportunity.created_by_user_id:
            user = users.get(opportunity.created_by_user_id)
            return user.name if user and user.name else "Sorento"
        return None

    out = []
    for opportunity in opportunities:
        customer = customers.get(opportunity.customer_id) if opportunity.customer_id else None
        agent = agents.get(opportunity.sales_agent_id) if opportunity.sales_agent_id else None
        status = statuses.get(opportunity.status_id) if opportunity.status_id else None
        sales_order = orders.get(opportunity.sales_order_id) if opportunity.sales_order_id else None
        out.append(
            {
                "id": opportunity.id,
                "opportunity_no": opportunity.opportunity_no,
                "title": opportunity.title,
                "customer_id": opportunity.customer_id,
                "customer_code": customer.customer_code if customer else None,
                "customer_name": customer.customer_name if customer else None,
                "prospect_name": opportunity.prospect_name,
                "sales_agent_id": opportunity.sales_agent_id,
                "sales_agent_label": team_service.agent_label(agent) if agent else None,
                "status_id": opportunity.status_id,
                "stage_key": status.key if status else None,
                "stage_label": status.label if status else None,
                "win_probability": status.win_probability if status else None,
                "outcome": opportunity.outcome,
                "expected_amount": opportunity.expected_amount,
                "expected_close_date": opportunity.expected_close_date,
                "lost_reason": opportunity.lost_reason,
                "lost_reason_label": lost_reason_labels.get(opportunity.lost_reason)
                or opportunity.lost_reason,
                "sales_order_id": opportunity.sales_order_id,
                "sales_order_no": sales_order.so_number if sales_order else None,
                "source": opportunity.source,
                "created_by_label": _created_by_label_batched(opportunity),
                "created_by_contact_id": opportunity.created_by_contact_id,
                "created_at": opportunity.created_at,
                "updated_at": opportunity.updated_at,
                "stage_changed_at": opportunity.stage_changed_at,
                "lines": [],
                "available_transitions": [],
                "project_id": opportunity.project_id,
            }
        )
    attach_project_display(db, out)
    return out
