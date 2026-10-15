"""Sales targets: create, edit, duplicate, delete, and the list and detail reads (plan 3.1, 3.8,
16.2, 16.3; UAC S1-1 to S1-29, S6-4 to S6-7).

A target is a header (who, what counts, which products, a date range and an optional split)
with one row per period holding the figure. The subject is an agent, a team or a dealer.

**A dealer target** (lane SALES-TARGET-DEALER) counts the orders of its accounts
(`target_accounts`), whatever agent sold them, and pays to `payout_customer_id`. It runs on the
same engine as every other target: the same periods, tiers and `commission_service.payout_for`.
Every subject may have thresholds in % of the figure, RM or points, tiers per period, stacking
"+ Additional" tiers and rounding to sen or ringgit. On a target whose thresholds are not a %,
each period's figure is its lowest main threshold, written here on every tier write.

**A team target is the sum of its agents' targets** (owner ruling 26 Sep 06:09, T3). It is
created with one child agent target per agent figure (`parent_target_id`), each child copying
the team's metric, basis, scope, dates and split. Each team period's `target_value` is written
here, as the sum of its children's periods with the same start, in the same transaction as
every write that can change it: create, a child's period edit, Add figure, a child's delete, a
team edit (which rewrites every child), and a team duplicate. It is stored rather than summed
at read time so every reader (the list, commission in S4, the message in S5) reads one column.
A team period is never edited directly (422 `TEAM_TARGET_IS_SUM`); a child's metric, basis,
scope, dates and split follow its parent (422 `CHILD_FOLLOWS_PARENT`), and only its name and
figures are its own. Known gap: a hard delete of a `sales_agents` row cascades a child past this
service and leaves its parent's stored sum stale until the next write (agents are archived, not
deleted, in practice).

`TGT-000123` numbers are per-company max + 1 under an advisory lock, not a numbering rule row
(16.1): nobody asked to configure the format (trigger for a rule row: the owner asks to).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import Integer, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.order import Customer, CustomerGroup
from app.models.product import Brand, Product, ProductCategory
from app.models.sales import (
    SalesPointRatio,
    SalesTarget,
    SalesTargetAccount,
    SalesTargetCommissionTier,
    SalesTargetPeriod,
    SalesTargetScope,
    SalesTargetTierRate,
    SalesTeam,
    SalesTeamMember,
)
from app.models.sales_agent import SalesAgent
from app.utils.product_display import code_with_name
from app.services.error_handler import AppException
from app.services.sales import achievement_service as ach
from app.services.sales import commission_service
from app.services.sales import team_service
from app.services.sales.period_service import add_months, generate_periods
from app.services.sales.team_service import acting_company_id, agent_label

#: The fields a child target takes from its parent (T3). Only `name` and figures are its own.
FOLLOWED_FIELDS = (
    "metric",
    "basis",
    "product_scope",
    "category_ids",
    "product_ids",
    "brand_ids",
    "start_date",
    "end_date",
    "split_every",
    "split_unit",
)


def _today() -> date:
    # The business day is the team service's (16.2), read at call time so a test pinning
    # `team_service._today` pins this too.
    return team_service._today()


def _unprocessable(message: str, code: str) -> AppException:
    return AppException(status_code=422, message=message, code=code)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------------------
# lookups
# --------------------------------------------------------------------------------------


def get_target_or_404(db: Session, target_id: str) -> SalesTarget:
    target = db.query(SalesTarget).filter(SalesTarget.id == target_id).first()
    if target is None:
        raise AppException(status_code=404, message="Target not found.", code="NOT_FOUND")
    return target


def _periods(db: Session, target_id: str) -> List[SalesTargetPeriod]:
    return (
        db.query(SalesTargetPeriod)
        .filter(SalesTargetPeriod.target_id == target_id)
        .order_by(SalesTargetPeriod.period_start)
        .all()
    )


def _children(db: Session, target_id: str) -> List[SalesTarget]:
    return (
        db.query(SalesTarget)
        .filter(SalesTarget.parent_target_id == target_id)
        .order_by(SalesTarget.target_no)
        .all()
    )


# --------------------------------------------------------------------------------------
# dealers: accounts, labels, point ratios
# --------------------------------------------------------------------------------------

_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}


def account_label(customer: Customer) -> str:
    """`A/C I` for a numbered account, else the customer's name."""
    level = customer.account_level
    if level:
        return f"A/C {_ROMAN.get(level, str(level))}"
    return customer.customer_name


def _account_out(customer: Customer, achieved: Optional[Decimal] = None) -> dict:
    return {
        "customer_id": customer.id,
        "code": customer.customer_code,
        "name": customer.customer_name,
        "label": account_label(customer),
        "achieved_value": None if achieved is None else float(achieved),
    }


def _accounts_by_target(db: Session, target_ids: Iterable[str]) -> Dict[str, List[Customer]]:
    ids = list(set(target_ids))
    out: Dict[str, List[Customer]] = {target_id: [] for target_id in ids}
    if not ids:
        return out
    rows = (
        db.query(SalesTargetAccount, Customer)
        .join(Customer, Customer.id == SalesTargetAccount.customer_id)
        .filter(SalesTargetAccount.target_id.in_(ids))
        .order_by(SalesTargetAccount.sort_order, Customer.customer_code)
        .all()
    )
    for link, customer in rows:
        out[link.target_id].append(customer)
    return out


def dealer_label(db: Session, accounts: List[Customer]) -> str:
    """The customer group's name when every account is in the same group, else the first
    account's name."""
    if not accounts:
        return ""
    groups = {c.customer_group_id for c in accounts}
    if len(groups) == 1 and None not in groups:
        group = db.get(CustomerGroup, next(iter(groups)))
        if group is not None:
            return group.name
    return accounts[0].customer_name


def _visible_customers(db: Session, ids: Sequence[str]) -> List[Customer]:
    """The customers of the acting company, in the order given; 422 for any other."""
    if len(set(ids)) != len(ids):
        raise _unprocessable("An account is listed twice.", "ACCOUNT_REPEATED")
    found = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(list(ids)))} if ids else {}
    if len(found) != len(ids):
        raise _unprocessable("That customer account was not found.", "UNKNOWN_CUSTOMER")
    return [found[i] for i in ids]


def _write_accounts(db: Session, target: SalesTarget, customer_ids: Sequence[str]) -> None:
    if not customer_ids:
        raise _unprocessable("A dealer target counts at least one account.", "ACCOUNTS_REQUIRED")
    customers = _visible_customers(db, list(customer_ids))
    db.query(SalesTargetAccount).filter(SalesTargetAccount.target_id == target.id).delete(
        synchronize_session=False
    )
    for index, customer in enumerate(customers):
        db.add(
            SalesTargetAccount(
                company_id=target.company_id,
                target_id=target.id,
                customer_id=customer.id,
                sort_order=index,
            )
        )
    db.flush()


def _write_payout(db: Session, target: SalesTarget, payout_customer_id: Optional[str]) -> None:
    """The paid account: the one named, else the first counted account (16 Oct ruling: it may
    be an account the target does not count)."""
    if payout_customer_id:
        target.payout_customer_id = _visible_customers(db, [payout_customer_id])[0].id
    else:
        first = (
            db.query(SalesTargetAccount)
            .filter(SalesTargetAccount.target_id == target.id)
            .order_by(SalesTargetAccount.sort_order)
            .first()
        )
        target.payout_customer_id = first.customer_id if first else None
    db.flush()


def point_ratios(db: Session) -> List[SalesPointRatio]:
    """The acting company's ratios, newest first."""
    return db.query(SalesPointRatio).order_by(SalesPointRatio.effective_from.desc()).all()


def ratio_on(ratios: List[SalesPointRatio], day: date) -> Optional[Decimal]:
    """The RM per point in force on `day` (the newest ratio dated on or before it)."""
    for ratio in ratios:  # newest first
        if ratio.effective_from <= day:
            return ratio.rm_per_point
    return None


def save_point_ratio(db: Session, effective_from: date, rm_per_point: float) -> SalesPointRatio:
    """One ratio per date: the same date again replaces that date's ratio."""
    row = db.query(SalesPointRatio).filter(SalesPointRatio.effective_from == effective_from).first()
    if row is None:
        row = SalesPointRatio(
            company_id=acting_company_id(db), effective_from=effective_from
        )
        db.add(row)
    row.rm_per_point = Decimal(str(rm_per_point)).quantize(Decimal("0.0001"))
    db.flush()
    return row


#: One target's scope ids: (category ids, product ids, brand ids).
ScopeIds = Tuple[List[str], List[str], List[str]]

#: A plain-words refusal for a half-empty range (owner ruling N6: both dates are required).
DATES_REQUIRED_MESSAGE = "A target needs both a start date and an end date."


def _scope_ids(db: Session, target_id: str) -> ScopeIds:
    rows = (
        db.query(SalesTargetScope)
        .filter(SalesTargetScope.target_id == target_id)
        .order_by(SalesTargetScope.id)
        .all()
    )
    return (
        [r.product_category_id for r in rows if r.product_category_id],
        [r.product_id for r in rows if r.product_id],
        [r.brand_id for r in rows if r.brand_id],
    )


def _visible_agent(db: Session, company_id: str, agent_id: Optional[str]) -> SalesAgent:
    agent = (
        team_service._visible_agents(db, company_id).filter(SalesAgent.id == agent_id).first()
        if agent_id
        else None
    )
    if agent is None:
        raise _unprocessable("That sales agent was not found.", "UNKNOWN_SALES_AGENT")
    return agent


def _stays_overlapping(
    db: Session, team_id: str, start: date, end: date
) -> Dict[str, SalesTeamMember]:
    """Members of the team on any day of `[start, end]`, one (the latest) stay per agent."""
    rows = (
        db.query(SalesTeamMember)
        .filter(
            SalesTeamMember.sales_team_id == team_id,
            or_(SalesTeamMember.valid_from.is_(None), SalesTeamMember.valid_from <= end),
            or_(SalesTeamMember.valid_to.is_(None), SalesTeamMember.valid_to >= start),
        )
        .order_by(SalesTeamMember.valid_from.asc().nullsfirst())
        .all()
    )
    return {r.sales_agent_id: r for r in rows}


def _require_member(db: Session, team_id: str, agent_id: str, start: date, end: date) -> None:
    if agent_id not in _stays_overlapping(db, team_id, start, end):
        raise _unprocessable(
            "An agent with a figure is not in the team on any day of the target's dates.",
            "AGENT_NOT_IN_TEAM",
        )


# --------------------------------------------------------------------------------------
# numbering, scope, periods
# --------------------------------------------------------------------------------------


def _next_target_nos(db: Session, company_id: str, n: int) -> List[str]:
    """`n` consecutive `TGT-000001` numbers for the company, under a transaction lock.

    A statement after the lock sees the previous holder's commit (READ COMMITTED), and one lock
    covers a team and all its children. `uq_sales_targets_company_target_no` is the backstop.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('sales.targets.target_no'), hashtext(:c))"),
        {"c": str(company_id)},
    )
    current = db.execute(
        select(func.max(cast(func.substring(SalesTarget.target_no, 5), Integer))).where(
            SalesTarget.company_id == company_id, SalesTarget.target_no.like("TGT-%")
        )
    ).scalar()
    first = int(current or 0) + 1
    return [f"TGT-{number:06d}" for number in range(first, first + n)]


def _validate_scope(
    db: Session,
    product_scope: str,
    category_ids: Sequence[str],
    product_ids: Sequence[str],
    brand_ids: Sequence[str] = (),
) -> ScopeIds:
    """Plan 3.1: `all` has no rows; `categories`, `products` and `brands` (the hand test of
    27 Sep) each take at least one row of their own kind and none of the others."""
    category_ids = list(dict.fromkeys(str(i) for i in category_ids or []))
    product_ids = list(dict.fromkeys(str(i) for i in product_ids or []))
    brand_ids = list(dict.fromkeys(str(i) for i in brand_ids or []))
    given = {"categories": category_ids, "products": product_ids, "brands": brand_ids}
    others = [kind for kind, ids in given.items() if ids and kind != product_scope]
    if product_scope == "all" and others:
        raise _unprocessable(
            "All products takes no category, product or brand.", "INVALID_SCOPE"
        )
    noun = {"categories": "category", "products": "product", "brands": "brand"}
    if product_scope in given and (not given[product_scope] or others):
        raise _unprocessable(
            f"Pick at least one {noun[product_scope]}, and nothing else.", "INVALID_SCOPE"
        )
    if category_ids:
        found = {
            c.id for c in db.query(ProductCategory).filter(ProductCategory.id.in_(category_ids))
        }
        if len(found) != len(category_ids):
            raise _unprocessable("One or more categories were not found.", "INVALID_SCOPE")
    if product_ids:
        found = {p.id for p in db.query(Product).filter(Product.id.in_(product_ids))}
        if len(found) != len(product_ids):
            raise _unprocessable("One or more products were not found.", "INVALID_SCOPE")
    if brand_ids:
        found = {b.id for b in db.query(Brand).filter(Brand.id.in_(brand_ids))}
        if len(found) != len(brand_ids):
            raise _unprocessable("One or more brands were not found.", "INVALID_SCOPE")
    return category_ids, product_ids, brand_ids


def _write_scope(
    db: Session,
    target: SalesTarget,
    category_ids: Sequence[str],
    product_ids: Sequence[str],
    brand_ids: Sequence[str] = (),
) -> None:
    db.query(SalesTargetScope).filter(SalesTargetScope.target_id == target.id).delete(
        synchronize_session=False
    )
    for category_id in category_ids:
        db.add(
            SalesTargetScope(
                company_id=target.company_id, target_id=target.id, product_category_id=category_id
            )
        )
    for product_id in product_ids:
        db.add(
            SalesTargetScope(company_id=target.company_id, target_id=target.id, product_id=product_id)
        )
    for brand_id in brand_ids:
        db.add(SalesTargetScope(company_id=target.company_id, target_id=target.id, brand_id=brand_id))


def _add_periods(db: Session, target: SalesTarget, bounds, values: Sequence[Decimal]) -> None:
    for (start, end), value in zip(bounds, values):
        db.add(
            SalesTargetPeriod(
                company_id=target.company_id,
                target_id=target.id,
                period_start=start,
                period_end=end,
                target_value=_money(value),
            )
        )


def _kept_value(old: List[SalesTargetPeriod], new_start: date) -> Decimal:
    """The keep rule (S1-5): same start keeps its figure; else the old period containing the
    new start; else the nearest old period in time."""
    for period in old:
        if period.period_start == new_start:
            return period.target_value
    for period in old:
        if period.period_start <= new_start <= period.period_end:
            return period.target_value

    def distance(period: SalesTargetPeriod) -> int:
        if new_start > period.period_end:
            return (new_start - period.period_end).days
        return (period.period_start - new_start).days

    return min(old, key=distance).target_value if old else Decimal("0")


def _regenerate_periods(db: Session, target: SalesTarget) -> None:
    """Rebuild the periods from the header, keeping figures under the keep rule."""
    old = _periods(db, target.id)
    bounds = generate_periods(target.start_date, target.end_date, target.split_every, target.split_unit)
    by_start = {p.period_start: p for p in old}
    wanted = {start for start, _ in bounds}
    values = {start: _kept_value(old, start) for start, _ in bounds}
    for period in old:
        if period.period_start not in wanted:
            db.delete(period)
    db.flush()  # gone before a new row reuses its start
    for start, end in bounds:
        existing = by_start.get(start)
        if existing is not None:
            existing.period_end = end
        else:
            _add_periods(db, target, [(start, end)], [values[start]])
    db.flush()


def resum_team(db: Session, team_target: SalesTarget) -> None:
    """Each team period = the sum of its children's periods with the same start (T3)."""
    db.flush()
    sums: Dict[date, Decimal] = {}
    child_ids = [c.id for c in _children(db, team_target.id)]
    if child_ids:
        rows = (
            db.query(SalesTargetPeriod.period_start, func.sum(SalesTargetPeriod.target_value))
            .filter(SalesTargetPeriod.target_id.in_(child_ids))
            .group_by(SalesTargetPeriod.period_start)
            .all()
        )
        sums = {start: Decimal(total or 0) for start, total in rows}
    for period in _periods(db, team_target.id):
        period.target_value = _money(sums.get(period.period_start, 0))
    db.flush()


def _tiers(db: Session, target_id: str) -> List[SalesTargetCommissionTier]:
    return _tiers_by_target(db, [target_id])[target_id]


def _tiers_by_target(
    db: Session, target_ids: Iterable[str]
) -> Dict[str, List[SalesTargetCommissionTier]]:
    ids = list(set(target_ids))
    out: Dict[str, List[SalesTargetCommissionTier]] = {target_id: [] for target_id in ids}
    if not ids:
        return out
    rows = db.query(SalesTargetCommissionTier).filter(
        SalesTargetCommissionTier.target_id.in_(ids)
    ).all()
    for tier in sorted(
        rows,
        key=lambda t: (
            t.period_start or date.min,
            t.is_additional,
            t.from_pct if t.from_pct is not None else t.threshold,
        ),
    ):
        out[tier.target_id].append(tier)
    return out


def _tier_rates(db: Session, tier_ids: Iterable[str]) -> Dict[str, Dict[str, Decimal]]:
    ids = list(set(tier_ids))
    out: Dict[str, Dict[str, Decimal]] = {}
    if not ids:
        return out
    for rate in db.query(SalesTargetTierRate).filter(SalesTargetTierRate.tier_id.in_(ids)):
        out.setdefault(rate.tier_id, {})[rate.customer_id] = rate.rate
    return out


#: The commission fields a create or PATCH payload may carry.
COMMISSION_FIELDS = ("commission_method", "threshold_unit", "rounding", "tiers_per_period", "tiers")


def _write_commission(db: Session, target: SalesTarget, commission: dict) -> None:
    """How tiers pay and the tiers (plan 3.3, UAC S4-1; one engine, UAC D2-2/D2-3).

    `commission` holds any of `COMMISSION_FIELDS`; `tiers` is the full list, replacing every
    tier. Refused in plain words: a method with no tiers or tiers with `none`; a tier without
    the threshold its target's unit needs; per-period tiers without a period or naming a date
    that does not start one; the same threshold twice in one period; points on a quantity
    target; rates per account on anything but a dealer, on an account the target does not
    count, or with the marginal method."""
    if not commission:
        _derive_figures(db, target)
        return
    method = commission.get("commission_method") or target.commission_method or "none"
    unit = commission.get("threshold_unit") or target.threshold_unit or "pct"
    rounding = commission.get("rounding") or target.rounding or "sen"
    per_period = commission.get("tiers_per_period")
    per_period = target.tiers_per_period if per_period is None else per_period
    if "tiers" in commission:
        tiers = [
            {
                "from_pct": None if t.from_pct is None else Decimal(str(t.from_pct)),
                "threshold": None if t.threshold is None else Decimal(str(t.threshold)),
                "rate": Decimal(str(t.rate or 0)),
                "bonus": t.bonus_amount,
                "is_additional": bool(t.is_additional),
                "period_start": t.period_start,
                "account_rates": (
                    None
                    if t.account_rates is None
                    else [(r.customer_id, Decimal(str(r.rate))) for r in t.account_rates]
                ),
            }
            for t in commission["tiers"] or []
        ]
    else:
        rates = _tier_rates(db, [t.id for t in _tiers(db, target.id)])
        tiers = [
            {
                "from_pct": t.from_pct,
                "threshold": t.threshold,
                "rate": t.rate,
                "bonus": t.bonus_amount,
                "is_additional": t.is_additional,
                "period_start": t.period_start,
                "account_rates": list(rates[t.id].items()) if t.id in rates else None,
            }
            for t in _tiers(db, target.id)
        ]
    if method == "none" and tiers:
        raise _unprocessable("Pick how the tiers pay, or remove the tiers.", "INVALID_TIERS")
    if method != "none" and not tiers:
        raise _unprocessable("Add at least one tier.", "INVALID_TIERS")
    if unit == "points" and target.metric != "amount":
        raise _unprocessable("Points thresholds need an amount target.", "INVALID_TIERS")
    starts = {p.period_start for p in _periods(db, target.id)}
    counted = (
        {a.customer_id for a in db.query(SalesTargetAccount).filter(SalesTargetAccount.target_id == target.id)}
        if target.subject_kind == "dealer"
        else set()
    )
    seen = set()
    for t in tiers:
        if unit == "pct":
            if t["from_pct"] is None or t["threshold"] is not None:
                raise _unprocessable("Each tier starts at a % of the target.", "INVALID_TIERS")
            value = t["from_pct"].quantize(Decimal("0.01"))
        else:
            if t["threshold"] is None or t["from_pct"] is not None:
                raise _unprocessable(
                    "Each tier needs its threshold in "
                    + ("points." if unit == "points" else "RM."),
                    "INVALID_TIERS",
                )
            value = t["threshold"].quantize(Decimal("0.01"))
        if per_period:
            if t["period_start"] is None:
                raise _unprocessable("Each tier names the period it belongs to.", "INVALID_TIERS")
            if t["period_start"] not in starts:
                raise _unprocessable(
                    "A tier names a period this target does not have.", "UNKNOWN_PERIOD"
                )
        elif t["period_start"] is not None:
            raise _unprocessable(
                "Turn on own tiers per period to give a tier a period.", "INVALID_TIERS"
            )
        key = (t["period_start"], t["is_additional"], value)
        if key in seen:
            raise _unprocessable(
                "Two tiers start at the same threshold in one period.", "INVALID_TIERS"
            )
        seen.add(key)
        if t["account_rates"] is not None:
            if target.subject_kind != "dealer":
                raise _unprocessable("Only a dealer target has rates per account.", "INVALID_TIERS")
            if method != "retroactive":
                raise _unprocessable(
                    "Rates per account need \"Highest rate on everything\".", "INVALID_TIERS"
                )
            ids = [customer_id for customer_id, _ in t["account_rates"]]
            if len(set(ids)) != len(ids):
                raise _unprocessable("A tier names an account twice.", "INVALID_TIERS")
            if set(ids) - counted:
                raise _unprocessable(
                    "A rate names an account this target does not count.", "INVALID_TIERS"
                )
    target.commission_method = method
    target.threshold_unit = unit
    target.rounding = rounding
    target.tiers_per_period = bool(per_period)
    db.query(SalesTargetCommissionTier).filter(
        SalesTargetCommissionTier.target_id == target.id
    ).delete(synchronize_session=False)
    db.flush()
    for t in tiers:
        tier = SalesTargetCommissionTier(
            company_id=target.company_id,
            target_id=target.id,
            from_pct=t["from_pct"],
            threshold=None if t["threshold"] is None else _money(t["threshold"]),
            rate=t["rate"],
            bonus_amount=None if t["bonus"] is None else _money(t["bonus"]),
            is_additional=t["is_additional"],
            period_start=t["period_start"],
        )
        db.add(tier)
        db.flush()
        for customer_id, rate in t["account_rates"] or []:
            db.add(
                SalesTargetTierRate(
                    company_id=target.company_id, tier_id=tier.id, customer_id=customer_id, rate=rate
                )
            )
    db.flush()
    _derive_figures(db, target)


def _derive_figures(db: Session, target: SalesTarget) -> None:
    """On a target whose thresholds are RM or points, each period's figure is its lowest main
    threshold (0 with none); a team's figure stays the sum of its agents'."""
    if target.threshold_unit == "pct" or target.subject_kind == "team":
        return
    tiers = [t for t in _tiers(db, target.id) if not t.is_additional and t.threshold is not None]
    for period in _periods(db, target.id):
        mine = [
            t.threshold
            for t in tiers
            if not target.tiers_per_period or t.period_start == period.period_start
        ]
        period.target_value = _money(min(mine) if mine else 0)
    db.flush()


def _commission_of(payload) -> dict:
    """The commission fields a create or PATCH payload actually sent."""
    return {key: getattr(payload, key) for key in COMMISSION_FIELDS if key in payload.model_fields_set}


# --------------------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------------------


def _new_header(company_id: str, target_no: str, user_id: Optional[str], **fields) -> SalesTarget:
    return SalesTarget(company_id=company_id, target_no=target_no, created_by_user_id=user_id, **fields)


def create_target(db: Session, payload, *, user_id: Optional[str] = None) -> SalesTarget:
    company_id = acting_company_id(db)
    if payload.start_date is None or payload.end_date is None:
        raise _unprocessable(DATES_REQUIRED_MESSAGE, "DATES_REQUIRED")
    bounds = generate_periods(
        payload.start_date, payload.end_date, payload.split_every, payload.split_unit
    )
    scope_ids = _validate_scope(
        db, payload.product_scope, payload.category_ids, payload.product_ids, payload.brand_ids
    )
    common = dict(
        name=payload.name,
        metric=payload.metric,
        basis=payload.basis,
        product_scope=payload.product_scope,
        start_date=payload.start_date,
        end_date=payload.end_date,
        split_every=payload.split_every,
        split_unit=payload.split_unit,
    )

    by_threshold = payload.threshold_unit != "pct"
    if payload.subject_kind != "dealer" and (payload.customer_ids or payload.payout_customer_id):
        raise _unprocessable("Only a dealer target has accounts.", "INVALID_SUBJECT")

    if payload.subject_kind == "dealer":
        if payload.sales_agent_id or payload.sales_team_id or payload.agent_figures is not None:
            raise _unprocessable(
                "A dealer target names accounts, not an agent or a team.", "INVALID_SUBJECT"
            )
        (target_no,) = _next_target_nos(db, company_id, 1)
        target = _new_header(company_id, target_no, user_id, subject_kind="dealer", **common)
        db.add(target)
        db.flush()
        _write_accounts(db, target, payload.customer_ids or [])
        _write_payout(db, target, payload.payout_customer_id)
        _write_scope(db, target, *scope_ids)
        _add_periods(db, target, bounds, [payload.target_value or 0] * len(bounds))
        _write_commission(db, target, _commission_of(payload))
        db.flush()
        return target

    if payload.subject_kind == "agent":
        if payload.sales_team_id or payload.agent_figures is not None:
            raise _unprocessable(
                "An agent target has no team and no agent figures.", "INVALID_SUBJECT"
            )
        if payload.target_value is None and not by_threshold:
            raise _unprocessable("Type the target figure.", "TARGET_VALUE_REQUIRED")
        agent = _visible_agent(db, company_id, payload.sales_agent_id)
        (target_no,) = _next_target_nos(db, company_id, 1)
        target = _new_header(
            company_id, target_no, user_id, subject_kind="agent", sales_agent_id=agent.id, **common
        )
        db.add(target)
        db.flush()
        _write_scope(db, target, *scope_ids)
        _add_periods(db, target, bounds, [payload.target_value or 0] * len(bounds))
        _write_commission(db, target, _commission_of(payload))
        db.flush()
        return target

    # A team target: the header, then one child per agent figure, then the sum (S1-27).
    if payload.sales_agent_id:
        raise _unprocessable("A team target names a team, not an agent.", "INVALID_SUBJECT")
    if payload.target_value is not None:
        raise _unprocessable(
            "A team target is the sum of its agents' figures; type each agent's figure.",
            "TEAM_TARGET_IS_SUM",
        )
    team = (
        db.query(SalesTeam).filter(SalesTeam.id == payload.sales_team_id).first()
        if payload.sales_team_id
        else None
    )
    if team is None:
        raise _unprocessable("That sales team was not found.", "UNKNOWN_SALES_TEAM")
    if not team.is_active:
        raise _unprocessable(f"{team.name} is inactive.", "TEAM_INACTIVE")
    figures = payload.agent_figures or []
    if not figures:
        raise _unprocessable("Type a figure for at least one agent.", "AGENT_FIGURES_REQUIRED")
    agent_ids = [f.sales_agent_id for f in figures]
    if len(set(agent_ids)) != len(agent_ids):
        raise _unprocessable("An agent is listed twice.", "AGENT_REPEATED")
    # Another company's agent is 422, the `_visible_agent` rule `add_child` applies, in one read.
    if len(_agents_by_id(db, company_id, agent_ids)) != len(agent_ids):
        raise _unprocessable("That sales agent was not found.", "UNKNOWN_SALES_AGENT")
    members = _stays_overlapping(db, team.id, payload.start_date, payload.end_date)
    if any(agent_id not in members for agent_id in agent_ids):
        raise _unprocessable(
            "An agent with a figure is not in the team on any day of the target's dates.",
            "AGENT_NOT_IN_TEAM",
        )

    numbers = _next_target_nos(db, company_id, 1 + len(figures))
    target = _new_header(
        company_id, numbers[0], user_id, subject_kind="team", sales_team_id=team.id, **common
    )
    db.add(target)
    db.flush()
    _write_scope(db, target, *scope_ids)
    _add_periods(db, target, bounds, [0] * len(bounds))
    for number, figure in zip(numbers[1:], figures):
        child = _new_header(
            company_id,
            number,
            user_id,
            subject_kind="agent",
            sales_agent_id=figure.sales_agent_id,
            parent_target_id=target.id,
            **common,
        )
        db.add(child)
        db.flush()
        _write_scope(db, child, *scope_ids)
        _add_periods(db, child, bounds, [figure.target_value] * len(bounds))
    _write_commission(db, target, _commission_of(payload))
    resum_team(db, target)
    return target


# --------------------------------------------------------------------------------------
# edit
# --------------------------------------------------------------------------------------


def _apply_header(db: Session, target: SalesTarget, changes: dict) -> None:
    """Write the non-name header fields in `changes` to one target, scope and periods included."""
    if any(key in changes and changes[key] is None for key in ("start_date", "end_date")):
        raise _unprocessable(DATES_REQUIRED_MESSAGE, "DATES_REQUIRED")
    for key in ("metric", "basis", "start_date", "end_date", "split_every", "split_unit"):
        if key in changes:
            setattr(target, key, changes[key])
    if "split_every" in changes and "split_unit" not in changes and changes["split_every"] is None:
        target.split_unit = None
    if "split_unit" in changes and "split_every" not in changes and changes["split_unit"] is None:
        target.split_every = None
    if target.end_date < target.start_date:
        raise _unprocessable("The end date is before the start date.", "INVALID_DATES")
    if (target.split_every is None) != (target.split_unit is None):
        raise _unprocessable("A split needs both how many and which unit.", "INVALID_SPLIT")

    keys = ("category_ids", "product_ids", "brand_ids")
    if {"product_scope", *keys} & set(changes):
        scope = changes.get("product_scope") or target.product_scope
        current = dict(zip(keys, _scope_ids(db, target.id)))
        if "product_scope" in changes:
            lists = [changes.get(key) or [] for key in keys]
        else:
            lists = [changes.get(key, current[key]) or [] for key in keys]
        scope_ids = _validate_scope(db, scope, *lists)
        target.product_scope = scope
        _write_scope(db, target, *scope_ids)

    if {"start_date", "end_date", "split_every", "split_unit"} & set(changes):
        _regenerate_periods(db, target)
        # A per-period tier whose period start is gone goes with its period.
        starts = [p.period_start for p in _periods(db, target.id)]
        db.query(SalesTargetCommissionTier).filter(
            SalesTargetCommissionTier.target_id == target.id,
            SalesTargetCommissionTier.period_start.isnot(None),
            SalesTargetCommissionTier.period_start.notin_(starts),
        ).delete(synchronize_session=False)
    db.flush()


def _write_figures(db: Session, target: SalesTarget, figures) -> None:
    """The batched save (the owner's retest of 27 Sep, F1): each figure's period must be one of
    this agent target's own, or on a team target one of its agents' (a team period is their sum,
    422 `TEAM_TARGET_IS_SUM`). Anything else is 422 `UNKNOWN_PERIOD` and nothing is written."""
    if not figures:
        return
    wanted = {str(f.period_id): f.target_value for f in figures}
    if target.subject_kind == "team":
        if wanted.keys() & {p.id for p in _periods(db, target.id)}:
            raise _unprocessable(
                "A team target is the sum of its agents' figures; change an agent's figure.",
                "TEAM_TARGET_IS_SUM",
            )
        owners = [c.id for c in _children(db, target.id)]
    else:
        owners = [target.id]
    periods = (
        db.query(SalesTargetPeriod)
        .filter(SalesTargetPeriod.id.in_(list(wanted)), SalesTargetPeriod.target_id.in_(owners))
        .all()
        if owners
        else []
    )
    if len(periods) != len(wanted):
        raise _unprocessable("A figure names a period this target does not have.", "UNKNOWN_PERIOD")
    for period in periods:
        period.target_value = _money(wanted[period.id])
    db.flush()


def update_target(
    db: Session, target: SalesTarget, payload, *, user_id: Optional[str] = None
) -> SalesTarget:
    """The header, the commission tiers and every changed figure, in one request (F1, F2).

    A child follows its parent for what counts and its dates (T3); its name, its figures and
    its tiers are its own (T4), and its figures write through to the team target's sum (F3).
    """
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    figures = changes.pop("figures", None) or []
    new_agents = changes.pop("new_agents", None) or []
    commission = {k: changes.pop(k) for k in COMMISSION_FIELDS if k in changes}
    accounts = changes.pop("customer_ids", None)
    payout_sent = "payout_customer_id" in changes
    payout = changes.pop("payout_customer_id", None)
    if (accounts is not None or payout_sent) and target.subject_kind != "dealer":
        raise _unprocessable("Only a dealer target has accounts.", "INVALID_SUBJECT")
    if "name" in changes and changes["name"] is None:
        changes.pop("name")
    if target.parent_target_id and set(changes) - {"name"}:
        raise _unprocessable(
            "This target follows its team target; change it there.", "CHILD_FOLLOWS_PARENT"
        )
    if new_agents and target.subject_kind != "team":
        raise _unprocessable("Only a team target has agent figures.", "NOT_A_TEAM_TARGET")
    if "name" in changes:
        target.name = changes.pop("name")

    _write_figures(db, target, figures)
    for entry in new_agents:
        _new_child(
            db,
            target,
            entry.sales_agent_id,
            {f.period_start: f.target_value for f in entry.figures},
            user_id=user_id,
        )
    if accounts is not None:
        _write_accounts(db, target, accounts)
        # Rates naming an account no longer counted go with it.
        kept = set(accounts)
        tier_ids = [t.id for t in _tiers(db, target.id)]
        if tier_ids:
            db.query(SalesTargetTierRate).filter(
                SalesTargetTierRate.tier_id.in_(tier_ids),
                SalesTargetTierRate.customer_id.notin_(kept),
            ).delete(synchronize_session=False)
    if accounts is not None or payout_sent:
        current = target.payout_customer_id
        if payout_sent:
            _write_payout(db, target, payout)
        elif current not in (accounts or []):
            _write_payout(db, target, current)

    if changes:
        _apply_header(db, target, changes)
        if target.subject_kind == "team":
            # Every child is rewritten the same way in this transaction, then the sum again.
            for child in _children(db, target.id):
                _apply_header(db, child, changes)
    # After the header: per-period tiers are checked against the periods it leaves.
    _write_commission(db, target, commission)
    if target.subject_kind == "team" and (changes or figures or new_agents):
        resum_team(db, target)
    elif target.parent_target_id and figures:
        resum_team(db, get_target_or_404(db, target.parent_target_id))
    db.flush()
    return target


def update_period(db: Session, target: SalesTarget, period_id: str, value: float) -> SalesTarget:
    if target.subject_kind == "team":
        raise _unprocessable(
            "A team target is the sum of its agents' figures; change an agent's figure.",
            "TEAM_TARGET_IS_SUM",
        )
    period = (
        db.query(SalesTargetPeriod)
        .filter(SalesTargetPeriod.id == period_id, SalesTargetPeriod.target_id == target.id)
        .first()
    )
    if period is None:
        raise AppException(status_code=404, message="Period not found.", code="NOT_FOUND")
    period.target_value = _money(value)
    db.flush()
    if target.parent_target_id:
        resum_team(db, get_target_or_404(db, target.parent_target_id))
    return target


def add_child(
    db: Session, parent: SalesTarget, agent_id: str, value: float, *, user_id: Optional[str] = None
) -> SalesTarget:
    """Add figure: a team member with no figure on this team target gets their own (S1-28)."""
    starts = [p.period_start for p in _periods(db, parent.id)]
    _new_child(db, parent, agent_id, {start: value for start in starts}, user_id=user_id)
    resum_team(db, parent)
    return parent


def _new_child(
    db: Session,
    parent: SalesTarget,
    agent_id: str,
    values: Dict[date, float],
    *,
    user_id: Optional[str] = None,
) -> SalesTarget:
    """A child agent target for a team member with no figure yet; `values` by period start, a
    period left out at 0. The caller re-sums the parent."""
    if parent.subject_kind != "team":
        raise _unprocessable("Only a team target has agent figures.", "NOT_A_TEAM_TARGET")
    _visible_agent(db, parent.company_id, agent_id)
    _require_member(db, parent.sales_team_id, agent_id, parent.start_date, parent.end_date)
    if any(c.sales_agent_id == agent_id for c in _children(db, parent.id)):
        raise _unprocessable("That agent already has a figure on this target.", "AGENT_HAS_FIGURE")
    scope_ids = _scope_ids(db, parent.id)
    (number,) = _next_target_nos(db, parent.company_id, 1)
    child = _new_header(
        parent.company_id,
        number,
        user_id,
        subject_kind="agent",
        sales_agent_id=agent_id,
        parent_target_id=parent.id,
        name=parent.name,
        metric=parent.metric,
        basis=parent.basis,
        product_scope=parent.product_scope,
        start_date=parent.start_date,
        end_date=parent.end_date,
        split_every=parent.split_every,
        split_unit=parent.split_unit,
    )
    db.add(child)
    db.flush()
    _write_scope(db, child, *scope_ids)
    bounds = [(p.period_start, p.period_end) for p in _periods(db, parent.id)]
    if set(values) - {start for start, _ in bounds}:
        raise _unprocessable("A figure names a period this target does not have.", "UNKNOWN_PERIOD")
    _add_periods(db, child, bounds, [values.get(start, 0) for start, _ in bounds])
    db.flush()
    return child


# --------------------------------------------------------------------------------------
# duplicate, delete
# --------------------------------------------------------------------------------------


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def duplicate_dates(start: date, end: date) -> Tuple[date, date]:
    """Starts the day after the source ends; whole months when month aligned, else the days."""
    new_start = end + timedelta(days=1)
    if start.day == 1 and end == _month_end(end):
        months = (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1
        return new_start, _month_end(add_months(new_start, months - 1))
    return new_start, new_start + (end - start)


def _copy(
    db: Session,
    source: SalesTarget,
    target_no: str,
    *,
    start: date,
    end: date,
    name: str,
    parent_id: Optional[str],
    user_id: Optional[str],
) -> SalesTarget:
    """A copy of one header with its scope and its figures by period index (the last repeated)."""
    copy = _new_header(
        source.company_id,
        target_no,
        user_id,
        subject_kind=source.subject_kind,
        sales_agent_id=source.sales_agent_id,
        sales_team_id=source.sales_team_id,
        parent_target_id=parent_id,
        name=name,
        metric=source.metric,
        basis=source.basis,
        product_scope=source.product_scope,
        start_date=start,
        end_date=end,
        split_every=source.split_every,
        split_unit=source.split_unit,
        commission_method=source.commission_method,
        threshold_unit=source.threshold_unit,
        rounding=source.rounding,
        tiers_per_period=source.tiers_per_period,
        payout_customer_id=source.payout_customer_id,
    )
    db.add(copy)
    db.flush()
    _write_scope(db, copy, *_scope_ids(db, source.id))
    for index, customer in enumerate(_accounts_by_target(db, [source.id])[source.id]):
        db.add(
            SalesTargetAccount(
                company_id=copy.company_id, target_id=copy.id, customer_id=customer.id, sort_order=index
            )
        )
    source_starts = [p.period_start for p in _periods(db, source.id)]
    figures = [p.target_value for p in _periods(db, source.id)] or [Decimal("0")]
    bounds = generate_periods(start, end, source.split_every, source.split_unit)
    # Per-period tiers follow their period by position: the source's first period's tiers go
    # to the copy's first period, and so on; a tier past the copy's last period is dropped.
    new_start = {old: bounds[i][0] for i, old in enumerate(source_starts) if i < len(bounds)}
    tiers = _tiers(db, source.id)
    rates = _tier_rates(db, [t.id for t in tiers])
    for tier in tiers:
        if tier.period_start is not None and tier.period_start not in new_start:
            continue
        twin = SalesTargetCommissionTier(
            company_id=copy.company_id,
            target_id=copy.id,
            from_pct=tier.from_pct,
            threshold=tier.threshold,
            rate=tier.rate,
            bonus_amount=tier.bonus_amount,
            is_additional=tier.is_additional,
            period_start=None if tier.period_start is None else new_start[tier.period_start],
        )
        db.add(twin)
        db.flush()
        for customer_id, rate in rates.get(tier.id, {}).items():
            db.add(
                SalesTargetTierRate(
                    company_id=copy.company_id, tier_id=twin.id, customer_id=customer_id, rate=rate
                )
            )
    _add_periods(db, copy, bounds, [figures[min(i, len(figures) - 1)] for i in range(len(bounds))])
    db.flush()
    return copy


def duplicate_target(db: Session, source: SalesTarget, *, user_id: Optional[str] = None) -> SalesTarget:
    """S1-12. A team duplicates its children and re-sums; a child becomes a standalone target
    (its dates no longer match the parent's)."""
    start, end = duplicate_dates(source.start_date, source.end_date)
    name = f"{source.name} (copy)"[:120]
    children = _children(db, source.id) if source.subject_kind == "team" else []
    numbers = _next_target_nos(db, source.company_id, 1 + len(children))
    copy = _copy(
        db, source, numbers[0], start=start, end=end, name=name, parent_id=None, user_id=user_id
    )
    for number, child in zip(numbers[1:], children):
        _copy(
            db,
            child,
            number,
            start=start,
            end=end,
            name=f"{child.name} (copy)"[:120],
            parent_id=copy.id,
            user_id=user_id,
        )
    if children:
        resum_team(db, copy)
    return copy


def delete_target(db: Session, target: SalesTarget) -> None:
    """Hard delete (S1-13). Periods, scope and a team's children go by the FK cascade; deleting
    a child re-sums its parent in the same transaction."""
    parent_id = target.parent_target_id
    db.delete(target)
    db.flush()
    if parent_id:
        parent = db.query(SalesTarget).filter(SalesTarget.id == parent_id).first()
        if parent is not None:
            resum_team(db, parent)


def delete_target_by_id(db: Session, target_id: str) -> None:
    """The parked `sales_target.delete` action's handler. Already gone is not an error."""
    target = db.query(SalesTarget).filter(SalesTarget.id == target_id).first()
    if target is not None:
        delete_target(db, target)


# --------------------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------------------


def _agents_by_id(db: Session, company_id: str, ids: Iterable[str]) -> Dict[str, SalesAgent]:
    ids = set(ids)
    if not ids:
        return {}
    return {
        a.id: a for a in team_service._visible_agents(db, company_id).filter(SalesAgent.id.in_(ids))
    }


def _covering_on(db: Session, on: date):
    """Membership rows covering `on`, with their team."""
    return (
        db.query(SalesTeamMember, SalesTeam)
        .join(SalesTeam, SalesTeam.id == SalesTeamMember.sales_team_id)
        .filter(
            or_(SalesTeamMember.valid_from.is_(None), SalesTeamMember.valid_from <= on),
            or_(SalesTeamMember.valid_to.is_(None), SalesTeamMember.valid_to >= on),
        )
        .all()
    )


def _code_name(code: Optional[str], name: Optional[str]) -> str:
    """ "CODE - Name", the label every scope picker shows (never an id, the hand test F5)."""
    return " - ".join(part for part in (code, name) if part)


def category_label(category: ProductCategory) -> str:
    return _code_name(category.category_code, category.category_name)


def brand_label(brand: Brand) -> str:
    return _code_name(brand.brand_code, brand.brand_name)


def _scope_labels(db: Session, target_ids: Iterable[str]) -> Dict[str, List[Tuple[str, str, str]]]:
    """`{target_id: [(id, kind, label)]}` in a stable order; kind is category, product or brand."""
    ids = list(set(target_ids))
    out: Dict[str, List[Tuple[str, str, str]]] = {i: [] for i in ids}
    if not ids:
        return out
    categories = (
        db.query(SalesTargetScope.target_id, ProductCategory)
        .join(ProductCategory, ProductCategory.id == SalesTargetScope.product_category_id)
        .filter(SalesTargetScope.target_id.in_(ids))
        .all()
    )
    for target_id, category in sorted(categories, key=lambda r: r[1].category_name):
        out[target_id].append((category.id, "category", category_label(category)))
    products = (
        db.query(SalesTargetScope.target_id, Product)
        .join(Product, Product.id == SalesTargetScope.product_id)
        .filter(SalesTargetScope.target_id.in_(ids))
        .all()
    )
    for target_id, product in sorted(products, key=lambda r: r[1].product_code):
        out[target_id].append(
            (product.id, "product", code_with_name(product.product_code, product.product_name))
        )
    brands = (
        db.query(SalesTargetScope.target_id, Brand)
        .join(Brand, Brand.id == SalesTargetScope.brand_id)
        .filter(SalesTargetScope.target_id.in_(ids))
        .all()
    )
    for target_id, brand in sorted(brands, key=lambda r: r[1].brand_code):
        out[target_id].append((brand.id, "brand", brand_label(brand)))
    return out


def _specs(
    db: Session, company_id: str, pairs: List[Tuple[SalesTarget, SalesTargetPeriod]]
) -> List[ach.PeriodSpec]:
    agent_credit = ach.agent_credits(
        db, company_id, {t.sales_agent_id for t, _ in pairs if t.subject_kind == "agent"}
    )
    team_credit = ach.team_credits(
        db, company_id, {t.sales_team_id for t, _ in pairs if t.subject_kind == "team"}
    )
    dealer_ids = {t.id for t, _ in pairs if t.subject_kind == "dealer"}
    dealer_credit = ach.dealer_credits(
        {
            target_id: [c.id for c in customers]
            for target_id, customers in _accounts_by_target(db, dealer_ids).items()
        }
    )

    def credits(target: SalesTarget):
        if target.subject_kind == "agent":
            return agent_credit.get(target.sales_agent_id, [])
        if target.subject_kind == "team":
            return team_credit.get(target.sales_team_id, [])
        return dealer_credit.get(target.id, [])

    return [
        ach.PeriodSpec(
            period_id=period.id,
            target_id=target.id,
            period_start=period.period_start,
            period_end=period.period_end,
            metric=target.metric,
            basis=target.basis,
            product_scope=target.product_scope,
            credits=credits(target),
            credit_kind=1 if target.subject_kind == "dealer" else 0,
        )
        for target, period in pairs
    ]


def _engine_tiers(
    tiers: List[SalesTargetCommissionTier], rates: Dict[str, Dict[str, Decimal]]
) -> List[commission_service.Tier]:
    return [
        commission_service.Tier(
            from_pct=t.from_pct,
            rate=t.rate,
            bonus_amount=t.bonus_amount,
            threshold=t.threshold,
            is_additional=bool(t.is_additional),
            account_rates=rates.get(t.id),
        )
        for t in tiers
    ]


def period_payout(
    target: SalesTarget,
    period: SalesTargetPeriod,
    tiers: List[commission_service.Tier],
    tier_starts: List[Optional[date]],
    by_account: Dict[Optional[str], Decimal],
    ratios: List[SalesPointRatio],
) -> commission_service.Payout:
    """One period's payout on the one engine: the period's own tiers on a per-period target,
    every tier otherwise; the ratio in force on the period's last day on a points target."""
    mine = [
        tier
        for tier, start in zip(tiers, tier_starts)
        if not target.tiers_per_period or start == period.period_start
    ]
    if target.subject_kind != "dealer":
        by_account = {None: sum(by_account.values(), Decimal("0"))}
    if not mine and tiers and (target.commission_method or "none") != "none":
        # A period with no tiers of its own on a per-period target pays nothing, not "no tiers".
        zero = Decimal("0")
        return commission_service.Payout(zero, zero, [], None, False)
    return commission_service.payout_for(
        target.commission_method or "none",
        mine,
        period.target_value,
        by_account,
        metric=target.metric,
        threshold_unit=target.threshold_unit or "pct",
        rm_per_point=ratio_on(ratios, period.period_end) if target.threshold_unit == "points" else None,
        rounding=target.rounding or "sen",
    )


def _num(value) -> Optional[float]:
    return None if value is None else float(value)


def list_targets(
    db: Session,
    *,
    on: Optional[date] = None,
    subject: str = "team",
    sales_team_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """The Targets page and the team page (16.3): one row per target period containing `on`,
    plus a "No target" row per active agent or active team with none."""
    on = on or _today()
    company_id = acting_company_id(db)
    if sales_team_id == "none" and subject != "agent":
        raise _unprocessable("No team filters agents only.", "INVALID_FILTER")

    covering = _covering_on(db, on)
    team_of_agent: Dict[str, SalesTeam] = {m.sales_agent_id: t for m, t in covering}
    active_agents = (
        team_service._visible_agents(db, company_id).filter(SalesAgent.is_active.is_(True)).all()
    )
    no_team_count = sum(1 for a in active_agents if a.id not in team_of_agent)

    pairs_q = (
        db.query(SalesTarget, SalesTargetPeriod)
        .join(SalesTargetPeriod, SalesTargetPeriod.target_id == SalesTarget.id)
        .filter(
            SalesTarget.subject_kind == subject,
            SalesTargetPeriod.period_start <= on,
            SalesTargetPeriod.period_end >= on,
        )
    )

    rows: List[dict] = []
    left_on: Dict[str, Optional[date]] = {}
    if subject == "agent":
        if sales_team_id == "none":
            subjects = {a.id: a for a in active_agents if a.id not in team_of_agent}
        elif sales_team_id:
            # The agents the team page shows for the team on `on` (16.3, "Leaving agent").
            team = team_service.get_team_or_404(db, sales_team_id)
            members = team_service.team_detail(db, team, on=on)["members"]
            subjects = _agents_by_id(db, company_id, [m["sales_agent_id"] for m in members])
            left_on = {m["sales_agent_id"]: m["valid_to"] if m["left"] else None for m in members}
        else:
            subjects = {a.id: a for a in active_agents}
        pairs = pairs_q.all()
        if sales_team_id:
            pairs = [(t, p) for t, p in pairs if t.sales_agent_id in subjects]
        subjects.update(_agents_by_id(db, company_id, {t.sales_agent_id for t, _ in pairs} - set(subjects)))
        labels = {agent_id: agent_label(agent) for agent_id, agent in subjects.items()}
        with_target = {t.sales_agent_id for t, _ in pairs}
        no_target_ids = [
            agent_id
            for agent_id, agent in subjects.items()
            if agent_id not in with_target and (agent.is_active or sales_team_id)
        ]
    else:
        teams_q = db.query(SalesTeam)
        if sales_team_id:
            teams_q = teams_q.filter(SalesTeam.id == sales_team_id)
        teams = {t.id: t for t in teams_q.all()}
        pairs = [(t, p) for t, p in pairs_q.all() if t.sales_team_id in teams]
        labels = {team_id: team.name for team_id, team in teams.items()}
        with_target = {t.sales_team_id for t, _ in pairs}
        # An inactive team gets no "No target" row; its existing targets still show (S6-3).
        no_target_ids = [
            team_id for team_id, team in teams.items() if team.is_active and team_id not in with_target
        ]
        members_of: Dict[str, List[dict]] = {}
        agents = _agents_by_id(db, company_id, {m.sales_agent_id for m, _ in covering})
        for member, team in covering:
            agent = agents.get(member.sales_agent_id)
            if agent is not None:
                members_of.setdefault(team.id, []).append(
                    {"sales_agent_id": agent.id, "label": agent_label(agent)}
                )
        for team_members in members_of.values():
            team_members.sort(key=lambda m: m["label"])

    achieved = ach.achieved_by_period(db, _specs(db, company_id, pairs), company_id)
    scopes = _scope_labels(db, {t.id for t, _ in pairs})

    def subject_fields(subject_id: str) -> dict:
        if subject == "agent":
            team = team_of_agent.get(subject_id)
            return {
                "sales_agent_id": subject_id,
                "subject_label": labels.get(subject_id, ""),
                "team_id": team.id if team else None,
                "team_name": team.name if team else None,
                "left_on": left_on.get(subject_id),
                "members": None,
            }
        return {
            "sales_team_id": subject_id,
            "subject_label": labels.get(subject_id, ""),
            "team_id": subject_id,
            "team_name": labels.get(subject_id),
            "members": members_of.get(subject_id, []),
        }

    for target, period in pairs:
        subject_id = target.sales_agent_id if subject == "agent" else target.sales_team_id
        value = achieved.get(period.id, Decimal("0"))
        rows.append(
            {
                **subject_fields(subject_id),
                "subject_kind": subject,
                "target_id": target.id,
                "target_no": target.target_no,
                "name": target.name,
                "metric": target.metric,
                "basis": target.basis,
                "product_scope": target.product_scope,
                "scope_labels": [label for _, _, label in scopes.get(target.id, [])],
                "period_id": period.id,
                "period_start": period.period_start,
                "period_end": period.period_end,
                "end_date": target.end_date,
                "target_value": _num(period.target_value),
                "achieved_value": _num(value),
                "achieved_pct": ach.achieved_pct(value, period.target_value),
                "parent_target_id": target.parent_target_id,
            }
        )
    for subject_id in no_target_ids:
        rows.append({**subject_fields(subject_id), "subject_kind": subject})

    if query and query.strip():
        needle = query.strip().lower()
        rows = [
            r
            for r in rows
            if needle in (r["subject_label"] or "").lower() or needle in (r.get("name") or "").lower()
        ]

    rows.sort(
        key=lambda r: (
            (r["subject_label"] or "").lower(),
            0 if r.get("metric") == "amount" else 1,
            r.get("end_date") or date.max,
            r.get("target_no") or "",
        )
    )
    return {
        "on": on,
        "rows": rows,
        "unassigned_amount": float(ach.unassigned_amount(db, on, company_id)),
        "no_team_count": no_team_count,
    }


def list_all_targets(
    db: Session,
    *,
    subject: str = "team",
    sales_team_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Targets > Teams and Targets > Agents (the owner's hand test of 27 Sep, "it should show a
    list of team target, that's it"): one row per target of the kind, whatever its dates, with
    the whole range's figures (the sum of its periods' targets and achievements). No date
    filter and no "No target" rows. On the Agents tab the Team filter is the agent's team
    today, or `none` for agents in no team today."""
    on = _today()
    company_id = acting_company_id(db)
    if sales_team_id == "none" and subject != "agent":
        raise _unprocessable("No team filters agents only.", "INVALID_FILTER")
    team_of_agent: Dict[str, SalesTeam] = {m.sales_agent_id: t for m, t in _covering_on(db, on)}

    targets_q = db.query(SalesTarget).filter(SalesTarget.subject_kind == subject)
    if subject == "team" and sales_team_id:
        targets_q = targets_q.filter(SalesTarget.sales_team_id == sales_team_id)
    targets = targets_q.all()
    if subject == "agent" and sales_team_id == "none":
        targets = [t for t in targets if t.sales_agent_id not in team_of_agent]
    elif subject == "agent" and sales_team_id:
        targets = [
            t for t in targets if getattr(team_of_agent.get(t.sales_agent_id), "id", None) == sales_team_id
        ]

    accounts: Dict[str, List[Customer]] = {}
    if subject == "dealer":
        accounts = _accounts_by_target(db, [t.id for t in targets])
        labels = {t.id: dealer_label(db, accounts.get(t.id, [])) for t in targets}
    elif subject == "agent":
        agents = _agents_by_id(db, company_id, {t.sales_agent_id for t in targets})
        # An agent this reader cannot see is not listed (the agent visibility rule).
        targets = [t for t in targets if t.sales_agent_id in agents]
        labels = {agent_id: agent_label(agent) for agent_id, agent in agents.items()}
    else:
        teams = {
            t.id: t
            for t in db.query(SalesTeam).filter(SalesTeam.id.in_({x.sales_team_id for x in targets}))
        } if targets else {}
        labels = {team_id: team.name for team_id, team in teams.items()}

    periods_by_target: Dict[str, List[SalesTargetPeriod]] = {}
    if targets:
        for period in db.query(SalesTargetPeriod).filter(
            SalesTargetPeriod.target_id.in_([t.id for t in targets])
        ):
            periods_by_target.setdefault(period.target_id, []).append(period)
    pairs = [(t, p) for t in targets for p in periods_by_target.get(t.id, [])]
    by_account = ach.achieved_by_period_and_account(db, _specs(db, company_id, pairs), company_id)
    achieved = {pid: sum(v.values(), Decimal("0")) for pid, v in by_account.items()}
    scopes = _scope_labels(db, {t.id for t in targets})
    tiers_of = _tiers_by_target(db, [t.id for t in targets])
    rates = _tier_rates(db, [tier.id for tiers in tiers_of.values() for tier in tiers])
    ratios = point_ratios(db) if any(t.threshold_unit == "points" for t in targets) else []
    payout_customers = {
        c.id: c
        for c in db.query(Customer).filter(
            Customer.id.in_({t.payout_customer_id for t in targets if t.payout_customer_id})
        )
    } if subject == "dealer" else {}

    def figure(target: SalesTarget, period: SalesTargetPeriod) -> Decimal:
        if target.threshold_unit != "points":
            return period.target_value
        ratio = ratio_on(ratios, period.period_end)
        return period.target_value * ratio if ratio is not None else period.target_value

    def current_commission(target: SalesTarget, periods: List[SalesTargetPeriod]) -> Optional[float]:
        tiers = tiers_of.get(target.id, [])
        now = next((p for p in periods if p.period_start <= on <= p.period_end), None)
        if not tiers or now is None:
            return None
        payout = period_payout(
            target, now, _engine_tiers(tiers, rates), [t.period_start for t in tiers],
            by_account.get(now.id, {}), ratios,
        )
        return _num(payout.commission)

    rows: List[dict] = []
    for target in targets:
        periods = periods_by_target.get(target.id, [])
        goal = sum((figure(target, p) for p in periods), Decimal("0"))
        value = sum((achieved.get(p.id, Decimal("0")) for p in periods), Decimal("0"))
        if subject == "dealer":
            mine = accounts.get(target.id, [])
            payout_customer = payout_customers.get(target.payout_customer_id)
            subject_id = target.id
            subject_fields = {
                "account_labels": [account_label(c) for c in mine],
                "account_codes": [c.customer_code for c in mine],
                "payout_label": account_label(payout_customer) if payout_customer else None,
            }
        elif subject == "agent":
            team = team_of_agent.get(target.sales_agent_id)
            subject_id = target.sales_agent_id
            subject_fields = {
                "sales_agent_id": subject_id,
                "team_id": team.id if team else None,
                "team_name": team.name if team else None,
            }
        else:
            subject_id = target.sales_team_id
            subject_fields = {
                "sales_team_id": subject_id,
                "team_id": subject_id,
                "team_name": labels.get(subject_id),
            }
        rows.append(
            {
                **subject_fields,
                "subject_kind": subject,
                "subject_label": labels.get(subject_id, ""),
                "members": None,
                "target_id": target.id,
                "target_no": target.target_no,
                "name": target.name,
                "metric": target.metric,
                "basis": target.basis,
                "product_scope": target.product_scope,
                "scope_labels": [label for _, _, label in scopes.get(target.id, [])],
                "start_date": target.start_date,
                "end_date": target.end_date,
                "target_value": _num(goal),
                "achieved_value": _num(value),
                "achieved_pct": ach.achieved_pct(value, goal),
                "parent_target_id": target.parent_target_id,
                "threshold_unit": target.threshold_unit or "pct",
                "commission_earned": current_commission(target, periods),
            }
        )

    if query and query.strip():
        needle = query.strip().lower()
        rows = [
            r
            for r in rows
            if any(needle in (r.get(key) or "").lower() for key in ("subject_label", "name", "target_no"))
            or any(needle in code.lower() for code in r.get("account_codes") or [])
        ]
    # Newest range first, then by who and the number, so the current targets lead.
    rows.sort(key=lambda r: (r["subject_label"].lower(), r["target_no"]))
    rows.sort(key=lambda r: r["start_date"], reverse=True)
    return {"on": on, "rows": rows, "unassigned_amount": 0.0, "no_team_count": 0}


def counts_label(db: Session, basis: str, company_id: str) -> str:
    if basis == "ordered":
        return "Ordered"
    return "Delivered (by DO date)" if ach.any_do_linked(db, company_id) else "Delivered"


def target_detail(db: Session, target: SalesTarget, *, on: Optional[date] = None) -> dict:
    """The target page (16.3): header, scope, periods with achievement, and a team's agents."""
    on = on or _today()
    company_id = target.company_id
    periods = _periods(db, target.id)
    by_account = ach.achieved_by_period_and_account(
        db, _specs(db, company_id, [(target, p) for p in periods]), company_id
    )
    achieved = {pid: sum(v.values(), Decimal("0")) for pid, v in by_account.items()}

    tiers = _tiers(db, target.id)
    rates = _tier_rates(db, [t.id for t in tiers])
    engine_tiers = _engine_tiers(tiers, rates)
    tier_starts = [t.period_start for t in tiers]
    ratios = point_ratios(db) if target.threshold_unit == "points" else []
    accounts = _accounts_by_target(db, [target.id])[target.id] if target.subject_kind == "dealer" else []

    def period_out(p: SalesTargetPeriod) -> dict:
        payout = period_payout(target, p, engine_tiers, tier_starts, by_account.get(p.id, {}), ratios)
        ratio = ratio_on(ratios, p.period_end) if target.threshold_unit == "points" else None
        value = achieved.get(p.id, Decimal("0"))
        # A points figure is shown in RM at the period's ratio, so it compares with sales.
        figure = p.target_value * ratio if ratio is not None else p.target_value
        return {
            "id": p.id,
            "period_start": p.period_start,
            "period_end": p.period_end,
            "target_value": float(figure),
            "achieved_value": float(value),
            "achieved_pct": ach.achieved_pct(value, figure),
            "is_current": p.period_start <= on <= p.period_end,
            "commission_earned": _num(payout.commission),
            "bonus_earned": _num(payout.bonus),
            "commission_lines": [
                {"kind": line.kind, "threshold_rm": _num(line.threshold_rm), "amount": float(line.amount)}
                for line in payout.lines
            ],
            "tier_reached": payout.tier_reached,
            "point_ratio": _num(ratio),
            "ratio_missing": payout.ratio_missing,
            "accounts": [
                _account_out(c, by_account.get(p.id, {}).get(c.id, Decimal("0"))) for c in accounts
            ],
        }

    team_name = None
    if target.subject_kind == "dealer":
        subject_label = dealer_label(db, accounts)
    elif target.subject_kind == "agent":
        agent = db.get(SalesAgent, target.sales_agent_id)
        subject_label = agent_label(agent) if agent else ""
        team = next(
            (t for m, t in _covering_on(db, on) if m.sales_agent_id == target.sales_agent_id), None
        )
        team_name = team.name if team else None
    else:
        team = db.get(SalesTeam, target.sales_team_id)
        subject_label = team.name if team else ""

    parent = (
        db.query(SalesTarget).filter(SalesTarget.id == target.parent_target_id).first()
        if target.parent_target_id
        else None
    )

    children_out: List[dict] = []
    without_figure: List[dict] = []
    child_count = 0
    if target.subject_kind == "team":
        children = _children(db, target.id)
        child_count = len(children)
        members = _stays_overlapping(db, target.sales_team_id, target.start_date, target.end_date)
        agents = _agents_by_id(db, company_id, {c.sales_agent_id for c in children} | set(members))
        # Every child's periods in one read, not one per child.
        child_periods: Dict[str, List[SalesTargetPeriod]] = {}
        if children:
            for period in (
                db.query(SalesTargetPeriod)
                .filter(SalesTargetPeriod.target_id.in_([c.id for c in children]))
                .order_by(SalesTargetPeriod.period_start)
            ):
                child_periods.setdefault(period.target_id, []).append(period)
        for child in children:
            agent = agents.get(child.sales_agent_id)
            children_out.append(
                {
                    "target_id": child.id,
                    "target_no": child.target_no,
                    "sales_agent_id": child.sales_agent_id,
                    "label": agent_label(agent) if agent else "",
                    "periods": [
                        {"id": p.id, "period_start": p.period_start, "target_value": float(p.target_value)}
                        for p in child_periods.get(child.id, [])
                    ],
                }
            )
        with_figure = {c.sales_agent_id for c in children}
        for agent_id in members:
            agent = agents.get(agent_id)
            if agent is not None and agent_id not in with_figure:
                without_figure.append({"sales_agent_id": agent_id, "label": agent_label(agent)})
        children_out.sort(key=lambda c: c["label"])
        without_figure.sort(key=lambda m: m["label"])

    return {
        "id": target.id,
        "target_no": target.target_no,
        "name": target.name,
        "subject_kind": target.subject_kind,
        "sales_agent_id": target.sales_agent_id,
        "sales_team_id": target.sales_team_id,
        "subject_label": subject_label,
        "subject_team_name": team_name,
        "parent": (
            {"id": parent.id, "name": parent.name, "target_no": parent.target_no} if parent else None
        ),
        "metric": target.metric,
        "basis": target.basis,
        "product_scope": target.product_scope,
        "start_date": target.start_date,
        "end_date": target.end_date,
        "split_every": target.split_every,
        "split_unit": target.split_unit,
        "counts_label": counts_label(db, target.basis, company_id),
        "scope": [
            {"id": item_id, "kind": kind, "label": label}
            for item_id, kind, label in _scope_labels(db, [target.id])[target.id]
        ],
        "periods": [period_out(p) for p in periods],
        "children": children_out,
        "members_without_figure": without_figure,
        "child_count": child_count,
        "commission_method": target.commission_method or "none",
        "threshold_unit": target.threshold_unit or "pct",
        "rounding": target.rounding or "sen",
        "tiers_per_period": bool(target.tiers_per_period),
        "tiers": [
            {
                "from_pct": _num(t.from_pct),
                "threshold": _num(t.threshold),
                "rate": None if t.id in rates else _num(t.rate),
                "bonus_amount": _num(t.bonus_amount),
                "is_additional": bool(t.is_additional),
                "period_start": t.period_start,
                "account_rates": (
                    [
                        {"customer_id": customer_id, "rate": float(rate)}
                        for customer_id, rate in sorted(
                            rates[t.id].items(),
                            key=lambda kv: next(
                                (i for i, c in enumerate(accounts) if c.id == kv[0]), 99
                            ),
                        )
                    ]
                    if t.id in rates
                    else None
                ),
            }
            for t in tiers
        ],
        "accounts": [_account_out(c) for c in accounts],
        "payout_account": _payout_out(db, target),
        "created_at": target.created_at,
        "updated_at": target.updated_at,
    }


def _payout_out(db: Session, target: SalesTarget) -> Optional[dict]:
    if not target.payout_customer_id:
        return None
    customer = db.query(Customer).filter(Customer.id == target.payout_customer_id).first()
    return _account_out(customer) if customer else None


def options(db: Session) -> dict:
    """The Set target modal's pickers (16.3). Products are NOT here: the modal pages them from
    `/master-data/products/select` (about 22,000 rows)."""
    company_id = acting_company_id(db)
    teams = db.query(SalesTeam).filter(SalesTeam.is_active.is_(True)).order_by(func.lower(SalesTeam.name)).all()
    stays = (
        db.query(SalesTeamMember).filter(SalesTeamMember.sales_team_id.in_([t.id for t in teams])).all()
        if teams
        else []
    )
    agents = _agents_by_id(db, company_id, {s.sales_agent_id for s in stays})
    members: Dict[str, List[dict]] = {}
    for stay in stays:
        agent = agents.get(stay.sales_agent_id)
        if agent is not None:
            members.setdefault(stay.sales_team_id, []).append(
                {
                    "sales_agent_id": agent.id,
                    "label": agent_label(agent),
                    "valid_from": stay.valid_from,
                    "valid_to": stay.valid_to,
                }
            )
    categories = (
        db.query(ProductCategory)
        .filter(ProductCategory.is_active.is_(True))
        .order_by(ProductCategory.category_name)
        .all()
    )
    brands = db.query(Brand).filter(Brand.is_active.is_(True)).order_by(Brand.brand_code).all()
    return {
        "agents": team_service.agent_options(db, company_id=company_id),
        "teams": [
            {
                "id": t.id,
                "name": t.name,
                "is_active": t.is_active,
                "members": sorted(members.get(t.id, []), key=lambda m: m["label"]),
            }
            for t in teams
        ],
        "categories": [
            {"id": c.id, "label": category_label(c), "parent_category_id": c.parent_category_id}
            for c in categories
        ],
        "brands": [{"id": b.id, "label": brand_label(b)} for b in brands],
    }


def targets_now_by_team(db: Session, team_ids: Iterable[str], on: date) -> Dict[str, int]:
    """Team targets with a period containing `on`, per team (the Sales Teams list)."""
    ids = list(team_ids)
    if not ids:
        return {}
    rows = (
        db.query(SalesTarget.sales_team_id, func.count(func.distinct(SalesTarget.id)))
        .join(SalesTargetPeriod, SalesTargetPeriod.target_id == SalesTarget.id)
        .filter(
            SalesTarget.subject_kind == "team",
            SalesTarget.sales_team_id.in_(ids),
            SalesTargetPeriod.period_start <= on,
            SalesTargetPeriod.period_end >= on,
        )
        .group_by(SalesTarget.sales_team_id)
        .all()
    )
    return {team_id: count for team_id, count in rows}



def agent_progress(db: Session, agent_id: str, *, on: Optional[date] = None) -> dict:
    """The portal's My target panel (fix lane round 2, F2): each active target of one agent,
    what it asks for over its whole range, what is achieved so far, and what the agent's open
    opportunities closing on or before its end date would add if won.

    Achievement is `achievement_service.achieved_by_period` over every period of the target,
    summed, so the panel and the target page can never disagree. An opportunity adds its
    `expected_amount` to an amount target and its lines' quantity to a quantity target, in
    full whatever the target's product scope (the "what counts" line names that scope; the
    trigger to filter by scope is a scoped target whose forecast misleads).
    """
    from app.models.sales import SalesOpportunity, SalesOpportunityLine
    from app.services.sales import opportunity_service

    on = on or _today()
    targets = (
        db.query(SalesTarget)
        .filter(
            SalesTarget.subject_kind == "agent",
            SalesTarget.sales_agent_id == agent_id,
            SalesTarget.start_date <= on,
            SalesTarget.end_date >= on,
        )
        .order_by(SalesTarget.end_date, SalesTarget.name)
        .all()
    )
    if not targets:
        return {"today": on, "targets": []}

    last_end = max(t.end_date for t in targets)
    opportunities = (
        db.query(SalesOpportunity)
        .filter(
            SalesOpportunity.sales_agent_id == agent_id,
            SalesOpportunity.outcome == "open",
            SalesOpportunity.expected_close_date <= last_end,
        )
        .order_by(SalesOpportunity.expected_close_date, SalesOpportunity.opportunity_no)
        .all()
    )
    serialized = {row["id"]: row for row in opportunity_service.serialize_list(db, opportunities)}
    qty_by_opportunity: Dict[str, Decimal] = {}
    if opportunities:
        for opportunity_id, qty in (
            db.query(SalesOpportunityLine.opportunity_id, func.sum(SalesOpportunityLine.qty))
            .filter(SalesOpportunityLine.opportunity_id.in_([o.id for o in opportunities]))
            .group_by(SalesOpportunityLine.opportunity_id)
        ):
            qty_by_opportunity[opportunity_id] = Decimal(qty or 0)

    periods_by_target = {t.id: _periods(db, t.id) for t in targets}
    achieved: Dict[str, Decimal] = {}
    for company_id in {t.company_id for t in targets}:
        pairs = [(t, p) for t in targets if t.company_id == company_id for p in periods_by_target[t.id]]
        achieved.update(ach.achieved_by_period(db, _specs(db, company_id, pairs), company_id))
    labels = _scope_labels(db, [t.id for t in targets])

    rows = []
    for target in targets:
        periods = periods_by_target[target.id]
        target_value = sum((Decimal(p.target_value) for p in periods), Decimal("0"))
        achieved_value = sum((achieved.get(p.id, Decimal("0")) for p in periods), Decimal("0"))
        before_end = [o for o in opportunities if o.expected_close_date <= target.end_date]
        items = []
        for opportunity in before_end:
            value = (
                Decimal(opportunity.expected_amount)
                if target.metric == "amount"
                else qty_by_opportunity.get(opportunity.id, Decimal("0"))
            )
            row = serialized.get(opportunity.id, {})
            items.append(
                {
                    "id": opportunity.id,
                    "opportunity_no": opportunity.opportunity_no,
                    "title": opportunity.title,
                    "customer_or_prospect": row.get("customer_name") or opportunity.prospect_name,
                    "stage_label": row.get("stage_label"),
                    "expected_close_date": opportunity.expected_close_date,
                    "value": value,
                }
            )
        pipeline = sum((i["value"] for i in items), Decimal("0"))
        projected = achieved_value + pipeline
        rows.append(
            {
                "target_id": target.id,
                "target_no": target.target_no,
                "name": target.name,
                "metric": target.metric,
                "basis": target.basis,
                "counts_label": counts_label(db, target.basis, target.company_id),
                "product_scope": target.product_scope,
                "scope_labels": [label for _id, label in labels.get(target.id, [])],
                "start_date": target.start_date,
                "end_date": target.end_date,
                "target_value": target_value,
                "achieved_value": achieved_value,
                "gap_value": max(target_value - achieved_value, Decimal("0")),
                "pipeline_value": pipeline,
                "projected_value": projected,
                "short_value": max(target_value - projected, Decimal("0")),
                "opportunities": items,
            }
        )
    return {"today": on, "targets": rows}
