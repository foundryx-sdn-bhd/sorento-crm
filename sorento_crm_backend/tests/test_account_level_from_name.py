"""Phase 2 RED tests - `account_level_from_name` (ACCOUNT-LEDGER, AC-3).

`app.services.ledger_family.account_level_from_name(name) -> int | None` reads the `A/C <n>`
marker inside any `[..]` or `(..)` of a customer name (roman I..X or arabic, any case,
whitespace tolerant). Used ONCE, by the `acct_ledger_0001` seed; the live bot reads the
`customers.account_level` setting, never the name. Cases are real names from the 25 Sep copy.
"""
from __future__ import annotations

import pytest


def _fn():
    from app.services import ledger_family

    assert hasattr(ledger_family, "account_level_from_name"), "account_level_from_name is not defined"
    return ledger_family.account_level_from_name


@pytest.mark.parametrize(
    "name, level",
    [
        ("SOON HENG HARDWARE CO.SDN.BHD. [A/C I]", 1),
        ("SOON HENG HARDWARE CO.SDN.BHD. [A/C II]", 2),
        ("SOON HENG HARDWARE CO.SDN.BHD. [A/C III]", 3),
        ("SOON HENG HARDWARE CO.SDN.BHD. [A/C IV]", 4),
        ("CHIP BEE TRADING COMPANY [A/C V]", 5),
        ("ESAGRAND MARKETING SDN BHD (A/C 2)", 2),
        ("HOME TILES PLT (A/C I)", 1),
        ("ROMAN EMPIRE HOME SDN BHD [A/C II]-( KL OUTLET)", 2),
        ("YOO LIVING HOUSE [A/C III] - PRICETAG", 3),
    ],
)
def test_marker_gives_the_level(name: str, level: int) -> None:
    assert _fn()(name) == level


@pytest.mark.parametrize(
    "name",
    [
        "SOON HENG HARDWARE CO.SDN.BHD.",
        "ZZT ACME (PROJECT)",
        "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)",
        "ZZT TILES [CERAMIC]",
        "ZZT HARDWARE (SRT)",
        "",
        None,
    ],
)
def test_no_marker_gives_none(name) -> None:
    assert _fn()(name) is None


@pytest.mark.parametrize(
    "name, level",
    [
        ("ZZT LOWER [a/c ii]", 2),
        ("ZZT SPACED [ A/C  IV ]", 4),
        ("ZZT ARABIC (a/c 3)", 3),
        ("ZZT TEN [A/C X]", 10),
    ],
)
def test_case_and_whitespace_tolerant(name: str, level: int) -> None:
    assert _fn()(name) == level


def test_marker_outside_brackets_is_not_read() -> None:
    assert _fn()("ZZT A/C II TRADING") is None
