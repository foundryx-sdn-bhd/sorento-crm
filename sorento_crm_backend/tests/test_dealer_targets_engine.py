"""The one target engine (lane SALES-TARGET-DEALER, UAC D1): `commission_service.payout_for`.

Pure: no database. The worked examples E1 to E7 of the behaviour card v2
(`documentation/plans/sales/PLAN-dealer-sales-targets-15oct.md` section 2) plus the rules the
owner ruled on 16 Oct 00:10: highest tier reached pays, "+ Additional" lines stack and are each
rounded on their own, thresholds in % / RM / points, rounding per target.
"""
from decimal import Decimal as D

from app.services.sales.commission_service import Tier, commission_for, payout_for

A1, A3, DELUXE, MASTILE, WPLY = "acc-i", "acc-iii", "deluxe", "mastile", "wply"


def tier(threshold, rate=0, *, additional=False, accounts=None, bonus=None, from_pct=None):
    return Tier(
        from_pct=None if from_pct is None else D(str(from_pct)),
        rate=D(str(rate)),
        bonus_amount=None if bonus is None else D(str(bonus)),
        threshold=None if threshold is None else D(str(threshold)),
        is_additional=additional,
        account_rates=None if accounts is None else {k: D(str(v)) for k, v in accounts.items()},
    )


def test_payout_e1_shared_threshold_pays_named_account_only():
    out = payout_for(
        "retroactive",
        [tier(200000, accounts={A1: 2}), tier(250000, accounts={A1: 3})],
        D("0"),
        {A1: D("180000.00"), A3: D("70000.00")},
        threshold_unit="amount",
    )
    assert out.commission == D("5400.00")
    assert out.tier_reached == 2
    assert [line.amount for line in out.lines] == [D("5400.00")]


def test_payout_e2_additional_lines_stack_each_rounded_sen():
    tiers = [
        tier(150000, 2),
        tier(200000, 3),
        tier(220000, 1, additional=True),
        tier(250000, "0.5", additional=True),
    ]
    out = payout_for("retroactive", tiers, D("0"), {None: D("253450.50")}, threshold_unit="amount")
    assert [line.amount for line in out.lines] == [D("7603.52"), D("2534.51"), D("1267.25")]
    assert [line.kind for line in out.lines] == ["main", "additional", "additional"]
    # One combined 4.5% would give 11,405.27: never used.
    assert out.commission == D("11405.28")


def test_payout_e2_additional_lines_stack_rounded_ringgit():
    tiers = [
        tier(150000, 2),
        tier(200000, 3),
        tier(220000, 1, additional=True),
        tier(250000, "0.5", additional=True),
    ]
    out = payout_for(
        "retroactive", tiers, D("0"), {None: D("253450.50")}, threshold_unit="amount", rounding="ringgit"
    )
    assert out.commission == D("11406")


def test_payout_additional_not_reached_pays_nothing_for_it():
    tiers = [tier(200000, 3), tier(220000, 1, additional=True)]
    out = payout_for("retroactive", tiers, D("0"), {None: D("210000")}, threshold_unit="amount")
    assert out.commission == D("6300.00")
    assert len(out.lines) == 1


def test_payout_e3_rate_per_account_each_rounded():
    out = payout_for(
        "retroactive",
        [tier(300000, accounts={DELUXE: 2, MASTILE: 3, WPLY: "1.5"})],
        D("0"),
        {DELUXE: D("150000"), MASTILE: D("100000"), WPLY: D("60000")},
        threshold_unit="amount",
    )
    assert out.commission == D("6900.00")
    assert [line.amount for line in out.lines] == [D("6900.00")]


def test_payout_e4_below_first_threshold_pays_zero():
    out = payout_for("retroactive", [tier(200000, 2)], D("0"), {None: D("190000")}, threshold_unit="amount")
    assert out.commission == D("0.00")
    assert out.tier_reached is None


def test_payout_e4_yearly_pays_on_its_own():
    out = payout_for("retroactive", [tier(850000, 1)], D("0"), {None: D("900000")}, threshold_unit="amount")
    assert out.commission == D("9000.00")


def test_payout_e5_points_threshold_times_ratio():
    tiers = [tier(100000, 4)]
    q3 = payout_for(
        "retroactive", tiers, D("0"), {None: D("130000")}, threshold_unit="points", rm_per_point=D("1.20")
    )
    assert q3.commission == D("5200.00")
    assert q3.lines[0].threshold_rm == D("120000.00")
    q4 = payout_for(
        "retroactive", tiers, D("0"), {None: D("125000")}, threshold_unit="points", rm_per_point=D("1.30")
    )
    assert q4.commission == D("0.00")


def test_payout_points_without_ratio_reaches_nothing():
    out = payout_for(
        "retroactive", [tier(1, 4)], D("0"), {None: D("130000")}, threshold_unit="points", rm_per_point=None
    )
    assert out.ratio_missing is True
    assert out.commission == D("0.00")


def test_payout_e6_counted_account_paid_regardless_of_pay_to():
    # Pay to is where the money goes; the engine pays on the counted account's sales.
    out = payout_for("retroactive", [tier(80000, 2)], D("0"), {A3: D("90000")}, threshold_unit="amount")
    assert out.commission == D("1800.00")


def test_payout_e7_agent_fixed_rm_tiers_highest_reached():
    out = payout_for(
        "retroactive", [tier(500000, 1), tier(600000, "1.5")], D("0"), {None: D("612000")},
        threshold_unit="amount",
    )
    assert out.commission == D("9180.00")


def test_payout_tier_rate_applies_to_every_counted_account_without_account_rates():
    out = payout_for(
        "retroactive", [tier(100000, 2)], D("0"), {A1: D("60000.25"), A3: D("50000.25")},
        threshold_unit="amount",
    )
    # 1,200.005 -> 1,200.01 and 1,000.005 -> 1,000.01, each rounded.
    assert out.commission == D("2200.02")


def test_payout_pct_unit_matches_commission_for_marginal_and_retroactive():
    tiers = [tier(None, 2, from_pct=0, bonus=50), tier(None, 4, from_pct=100, bonus=100)]
    for method in ("marginal", "retroactive"):
        new = payout_for(method, tiers, D("1000"), {None: D("1500")}, threshold_unit="pct")
        old = commission_for(method, tiers, D("1000"), D("1500"))
        assert (new.commission, new.bonus) == old


def test_payout_none_method_is_none():
    out = payout_for("none", [], D("1000"), {None: D("1500")})
    assert out.commission is None and out.bonus is None


def test_payout_quantity_amount_threshold_rate_per_unit():
    out = payout_for(
        "retroactive", [tier(100, "2.5")], D("0"), {None: D("120")}, metric="quantity", threshold_unit="amount"
    )
    assert out.commission == D("300.00")
