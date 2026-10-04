"""CUSTOMER-GROUP, owner requirement 2 Oct 2026: the report's `customer_name` echo
(`outstanding_report_service._customer_echo`, which every `Customer:` header of the report
lanes prints) names a customer COMPANY once, by its customer group, an ungrouped ledger under its own name (owner ruling (b), 2 Oct: no automatic name-rule joining).

The owner REVERSED AC-1163 (distinct ledger names joined) on 2 Oct 2026: ledgers of one
group print as the group name only, no count. Postgres only (`blank_session`); every row is
seeded here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._pg_fixture import blank_session, unique_code

HANLIM = "HANLIM TRADING SDN BHD"
HANLIM_NAMES = [
    "HANLIM TRADING SDN BHD [A/C I]",
    "HANLIM TRADING SDN BHD [A/C II]",
    "HANLIM TRADING SDN BHD [A/C III]",
    "HANLIM TRADING SDN BHD [A/C IV]",
    "HANLIM TRADING SDN BHD",
    "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)",
]
DEALER = [
    ("HANLIM TRADING SDN BHD [A/C I]", HANLIM),
    ("1 LIVING DEPOT SDN BHD [A/C I]", "1 LIVING DEPOT SDN BHD"),
    ("HANLIM TRADING SDN BHD [A/C II]", HANLIM),
    ("MODERNMED SDN BHD", None),
    ("SCR MARKETING (M) SDN BHD [A/C IV]", "SCR MARKETING (M) SDN BHD"),
    ("HANLIM TRADING SDN BHD [A/C III]", HANLIM),
    ("1 LIVING DEPOT SDN BHD", "1 LIVING DEPOT SDN BHD"),
    ("HANLIM TRADING SDN BHD [A/C IV]", HANLIM),
    ("SCR MARKETING (M) SDN BHD [A/C I]", "SCR MARKETING (M) SDN BHD"),
    ("HANLIM TRADING SDN BHD", HANLIM),
    ("SOON HENG HARDWARE CO.SDN.BHD. [A/C I]", "SOON HENG HARDWARE CO.SDN.BHD."),
    ("HANLIM TRADING SDN BHD (CERAMIC & ELLECI)", HANLIM),
]
DEALER_EXPECTED = (
    "HANLIM TRADING SDN BHD, 1 LIVING DEPOT SDN BHD, MODERNMED SDN BHD, "
    "SCR MARKETING (M) SDN BHD, SOON HENG HARDWARE CO.SDN.BHD."
)


def seed_ledgers(db, rows, company_id=DEFAULT_COMPANY_ID) -> list[str]:
    """Customers in the order given; `(name, group name or None)`. Returns their ids."""
    groups: dict[str, str] = {}
    ids: list[str] = []
    for name, group in rows:
        row = Customer(
            id=str(uuid.uuid4()), customer_code=unique_code("ZZTC")[:50], customer_name=name, company_id=company_id
        )
        db.add(row)
        db.flush()
        if group:
            if group not in groups:
                groups[group] = str(uuid.uuid4())
                db.execute(
                    text(
                        "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
                        "VALUES (:i, :c, :n, now(), now())"
                    ),
                    {"i": groups[group], "c": company_id, "n": group},
                )
            db.execute(
                text("UPDATE customers SET customer_group_id = :g WHERE id = :c"),
                {"g": groups[group], "c": row.id},
            )
        ids.append(row.id)
    db.commit()
    return ids


def test_echo_for_the_dealers_twelve_ledgers_is_the_owner_string():
    from app.services.outstanding_report_service import _customer_echo

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        ids = seed_ledgers(db, DEALER)

        out = _customer_echo(db, None, ids)

    assert out == DEALER_EXPECTED
    assert "accounts)" not in out and " more" not in out


def test_echo_for_six_hanlim_ledgers_is_the_group_name_only():
    from app.services.outstanding_report_service import _customer_echo

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        ids = seed_ledgers(db, [(n, HANLIM) for n in HANLIM_NAMES])

        assert _customer_echo(db, None, ids) == HANLIM


def test_echo_prints_ungrouped_ledgers_by_their_own_names():
    from app.services.outstanding_report_service import _customer_echo

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        ids = seed_ledgers(
            db,
            [
                ("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I]", None),
                ("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C II]", None),
                ("KEDAI X SDN BHD [A/C I]", None),
            ],
        )

        assert _customer_echo(db, None, ids) == (
            "CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I], "
            "CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C II], KEDAI X SDN BHD [A/C I]"
        )
