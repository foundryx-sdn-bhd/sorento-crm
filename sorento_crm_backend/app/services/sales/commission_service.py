"""Commission per target period (plan 3.3; UAC S4-1 to S4-6; the owner's retest of 27 Sep), the
one engine for agent, team and dealer targets (lane SALES-TARGET-DEALER, owner rulings 16 Oct).

Computed per period from the target's tiers, never stored:

- `marginal`: each tier's rate applies only to the slice of achievement between its threshold
  and the next tier's ("2% up to 100%, 4% above" pays 4% only on the part over target).
- `retroactive`: the highest tier reached sets the rate for the whole achievement; the tiers
  below and above it play no part (Q2).
- Thresholds: `pct` (a tier's `from_pct` of the period's target figure, the original tiers),
  `amount` (a fixed RM, or units on a quantity target) or `points` (points x the RM-per-point
  ratio in force on the period's last day, Q4; no ratio = no tier reachable).
- "+ Additional" tiers (`is_additional`) each pay their own rate on the same achievement when
  their own threshold is reached; they stack (Q5).
- A tier pays its `rate` on every counted account, or with `account_rates` only on the accounts
  it names, each at its own rate (ruling 2). `achieved` maps an account to its achievement; an
  agent or team target passes a single bucket.
- Rounding: every line (the main tier, each additional tier) and each account's amount on a
  line is rounded half up on its own, then added, never one combined rate (ruling 3). The unit
  is the target's: sen (0.01, default) or ringgit (1). The marginal main line is the sum of its
  slices, rounded once, as before.
- Bonuses: every reached non-additional tier pays its `bonus_amount` once per period.
- `rate` is a % of the achieved amount on an amount target, RM per unit on a quantity target.
- `none` returns null commission and bonus.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Hashable, List, NamedTuple, Optional, Sequence, Tuple

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
UNITS = {"sen": CENT, "ringgit": Decimal("1")}


class Tier(NamedTuple):
    from_pct: Optional[Decimal]
    rate: Optional[Decimal]
    bonus_amount: Optional[Decimal]
    #: RM, units or points, when the target's threshold unit is not `pct`.
    threshold: Optional[Decimal] = None
    is_additional: bool = False
    #: account -> rate. None: `rate` on every counted account.
    account_rates: Optional[Dict[Hashable, Decimal]] = None


class PayLine(NamedTuple):
    kind: str  # `main` or `additional`
    #: The threshold in RM (units on a quantity target) this line reached.
    threshold_rm: Optional[Decimal]
    amount: Decimal


class Payout(NamedTuple):
    commission: Optional[Decimal]
    bonus: Optional[Decimal]
    lines: List[PayLine]
    #: 1-based position of the main tier reached among the main tiers, None below the first.
    tier_reached: Optional[int]
    ratio_missing: bool


def _round(value: Decimal, unit: Decimal = CENT) -> Decimal:
    return value.quantize(unit, rounding=ROUND_HALF_UP)


def threshold_in_rm(
    tier: Tier,
    threshold_unit: str,
    target_value: Decimal,
    rm_per_point: Optional[Decimal],
) -> Optional[Decimal]:
    """The tier's threshold in RM (units on a quantity target); None when it cannot be known."""
    if threshold_unit == "pct":
        if tier.from_pct is None:
            return None
        return Decimal(tier.from_pct) * target_value / HUNDRED
    if tier.threshold is None:
        return None
    if threshold_unit == "points":
        return None if rm_per_point is None else Decimal(tier.threshold) * Decimal(rm_per_point)
    return Decimal(tier.threshold)


def payout_for(
    method: str,
    tiers: Sequence[Tier],
    target_value: Decimal,
    achieved: Dict[Hashable, Decimal],
    *,
    metric: str = "amount",
    threshold_unit: str = "pct",
    rm_per_point: Optional[Decimal] = None,
    rounding: str = "sen",
) -> Payout:
    """The period's payout from its tiers and its achievement per account."""
    ratio_missing = threshold_unit == "points" and rm_per_point is None
    if method == "none" or not tiers:
        return Payout(None, None, [], None, ratio_missing)
    unit = UNITS.get(rounding, CENT)
    target_value = Decimal(target_value or 0)
    by_account = {key: max(Decimal(v or 0), Decimal("0")) for key, v in achieved.items()}
    total = sum(by_account.values(), Decimal("0"))

    def pay(rate: Decimal, base: Decimal) -> Decimal:
        return base * rate if metric == "quantity" else base * rate / HUNDRED

    def line_amount(tier: Tier) -> Decimal:
        # Each account's amount rounded on its own, then added (ruling 3).
        if tier.account_rates is not None:
            return sum(
                (_round(pay(Decimal(rate), by_account.get(key, Decimal("0"))), unit)
                 for key, rate in tier.account_rates.items()),
                Decimal("0"),
            )
        rate = Decimal(tier.rate or 0)
        return sum((_round(pay(rate, base), unit) for base in by_account.values()), Decimal("0"))

    def at(tier: Tier) -> Optional[Decimal]:
        return threshold_in_rm(tier, threshold_unit, target_value, rm_per_point)

    def reached(tier: Tier) -> bool:
        low = at(tier)
        if low is None:
            return False
        # A target of 0 counts as 100% achieved (pct tiers, as before).
        if threshold_unit == "pct" and target_value <= 0:
            return Decimal(tier.from_pct) <= HUNDRED
        return total >= low

    main = sorted(
        (t for t in tiers if not t.is_additional),
        key=lambda t: (at(t) is None, at(t) or Decimal("0")),
    )
    lines: List[PayLine] = []
    hit = [i for i, t in enumerate(main) if reached(t)]
    tier_reached = hit[-1] + 1 if hit else None
    if method == "retroactive":
        if hit:
            top = main[hit[-1]]
            lines.append(PayLine("main", _rm(at(top)), line_amount(top)))
    else:
        # Marginal: one line, the sum of each tier's slice, rounded once (unchanged).
        slices = Decimal("0")
        for index, tier in enumerate(main):
            low = at(tier)
            if low is None:
                continue
            high = at(main[index + 1]) if index + 1 < len(main) else None
            top = total if high is None else min(total, high)
            if top > low:
                slices += pay(Decimal(tier.rate or 0), top - low)
        if hit or slices:
            lines.append(PayLine("main", _rm(at(main[hit[-1]])) if hit else None, _round(slices, unit)))
    additional = sorted(
        (t for t in tiers if t.is_additional and reached(t)), key=lambda t: at(t) or Decimal("0")
    )
    for tier in additional:
        lines.append(PayLine("additional", _rm(at(tier)), line_amount(tier)))

    commission = sum((line.amount for line in lines), Decimal("0"))
    bonus = sum(
        (Decimal(main[i].bonus_amount or 0) for i in hit), Decimal("0")
    )
    return Payout(_round(commission, unit), _round(bonus, unit), lines, tier_reached, ratio_missing)


def _rm(value: Optional[Decimal]) -> Optional[Decimal]:
    return None if value is None else _round(value)


def commission_for(
    method: str,
    tiers: Sequence[Tier],
    target_value: Decimal,
    achieved: Decimal,
    *,
    metric: str = "amount",
) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """`(commission, bonus)` for one period of a `%`-of-target target (the original tiers)."""
    out = payout_for(method, tiers, target_value, {None: achieved}, metric=metric)
    return out.commission, out.bonus
