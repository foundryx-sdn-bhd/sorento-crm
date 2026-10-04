"""DO-ASK-SIMPLIFY rule 1: the DO list header names a customer company once, never ledger by ledger.

`documentation/plans/chatbot/PLAN-do-ask-simplify-2oct.md`, rule 1, as reconciled with
CUSTOMER-GROUP (#1441, owner ruling (b), 2 Oct 2026): the `Customer:` line names a customer
GROUP by its own name, once; a ledger joins a company line only through its explicit group,
and an ungrouped ledger prints its own full name. No `(N accounts)`, no `and N more`. The one
formatter is `ledger_family.customer_header_words`; the groups are the turn's
(`ledger_family.customer_groups`). The DO rows themselves keep their own full ledger name.

Pure function tests: no database, no engine.
"""
from __future__ import annotations

import json

from app.services.chatbot.tail import scope_block as scope_block_mod
from app.services.ledger_family import customer_groups

_HANLIM = [
    "HANLIM TRADING SDN BHD [A/C II]",
    "HANLIM TRADING SDN BHD [A/C I]",
    "HANLIM TRADING SDN BHD [A/C III]",
    "HANLIM TRADING SDN BHD [A/C IV]",
    "HANLIM TRADING SDN BHD",
    "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)",
]
_GROUP = "HANLIM TRADING SDN BHD"
_GROUPS = {name: _GROUP for name in _HANLIM}


def _header(names: list[str]) -> str:
    qf = {"entities": [], "date_filter_start": "2026-10-01", "date_filter_end": "2026-10-31"}
    gate_json = {
        "compatible_entities": [
            {"entity_type": "customer", "code": f"300-H{i:03d}", "display_name": name}
            for i, name in enumerate(names)
        ]
    }
    header = scope_block_mod.search_scope_header(
        domain="order", qf=qf, gate_json=gate_json, resolver_json={}
    )
    assert header is not None, "test setup sanity: the order domain must produce a header"
    return header


def _customer_line(header: str) -> str:
    lines = [line for line in header.split("\n") if line.startswith("Customer:")]
    assert len(lines) == 1, header
    return lines[0]


def test_six_grouped_ledgers_print_the_group_name_once() -> None:
    with customer_groups(_GROUPS):
        assert _customer_line(_header(_HANLIM)) == "Customer: HANLIM TRADING SDN BHD"


def test_one_grouped_ledger_prints_its_group_name() -> None:
    with customer_groups(_GROUPS):
        assert _customer_line(_header(["HANLIM TRADING SDN BHD [A/C I]"])) == "Customer: HANLIM TRADING SDN BHD"


def test_an_ungrouped_ledger_prints_its_own_full_name() -> None:
    """Owner ruling (b): no name-rule joining; a ledger outside any group is itself."""
    assert _customer_line(_header(["HANLIM TRADING SDN BHD [A/C I]"])) == (
        "Customer: HANLIM TRADING SDN BHD [A/C I]"
    )


def test_several_groups_and_ungrouped_ledgers_print_each_once_no_count() -> None:
    names = [*_HANLIM, "ZZT OTHER TRADING [A/C I]", "ZZT OTHER TRADING [A/C I]"]
    with customer_groups(_GROUPS):
        line = _customer_line(_header(names))
    assert line == "Customer: HANLIM TRADING SDN BHD, ZZT OTHER TRADING [A/C I]", line
    assert "accounts)" not in line and " more" not in line, line


def test_dates_line_is_unchanged() -> None:
    assert "Dates: 01/10/2026 to 31/10/2026" in _header(_HANLIM)


def test_the_carried_customer_rows_print_the_group_name() -> None:
    """Review B1: the answer to the period question ("this month") names no customer of its
    own, so the header reads the FOCUS carry (`_focus_words`), not the gate."""
    with customer_groups(_GROUPS):
        header = scope_block_mod.search_scope_header(
            domain="order",
            qf={"entities": [], "date_filter_start": "2026-10-01", "date_filter_end": "2026-10-31"},
            gate_json={"compatible_entities": []},
            resolver_json={},
            focus_customers=[{"hint": "customer", "display_name": name} for name in _HANLIM],
        )
    assert _customer_line(header) == "Customer: HANLIM TRADING SDN BHD", header


def test_an_empty_do_list_names_the_group_once_too() -> None:
    """Review S1: the miss composer's own header (`answer.not_found_error_message`) names the
    group the same way, so an empty DO list does not list every ledger either."""
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    rows = [
        {"uuid": f"00000000-0000-4000-8000-00000000000{i}", "entity_type": "customer", "code": "300-H", "display_name": n}
        for i, n in enumerate(_HANLIM)
    ]
    with customer_groups(_GROUPS):
        out = not_found_error_message(
            {},
            parser={"domain_hint": "order", "entities": [], "routing": {"suggested_team": "customer_service"}},
            resolved={"tokens": [], "unresolved_tokens": [], "resolutions": [], "intersection": rows, "by_entity_type": {"customer": rows}},
            gate={"gate_passed": True, "compatible_entities": rows},
        )
    first_line = (out.get("escalate_message") or "").split("\n", 1)[0]
    assert first_line == "Customer: HANLIM TRADING SDN BHD", json.dumps(out)


def test_the_not_your_account_line_names_groups_once() -> None:
    """The customer-scope refusal ("Sorry, that isn't under your account. I can only check
    on ...") names the contact's linked companies once each, by group name only. Since
    SO-NUMBER-ASK (#1435) this line groups by `ledger_family.group_names` (the name rule),
    so an ungrouped ledger loses its account marker here, unlike the DO header."""
    from app.services import contact_customer_scope as scope_mod

    scope = scope_mod.ContactCustomerScope(
        linked=tuple((f"id{i}", n, f"300-H{i}") for i, n in enumerate(_HANLIM)) + (("idz", "ZZT OTHER SDN BHD [A/C I]", "300-Z"),),
        staff=False,
    )
    with customer_groups(_GROUPS):
        line = scope_mod.refusal_line(scope)
    assert line == (
        "Sorry, that isn't under your account. I can only check on HANLIM TRADING SDN BHD and ZZT OTHER SDN BHD."
    )
