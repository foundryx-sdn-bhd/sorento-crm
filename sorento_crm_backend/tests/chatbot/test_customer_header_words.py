"""CUSTOMER-GROUP, owner requirement 2 Oct 2026: every business_query "Customer:" header names
a customer COMPANY once, by its customer group (name rule as fallback), never ledger by ledger.

Contract (owner rule change, 2 Oct): the GROUP NAME ONLY. No " (N accounts)" count and no
"and N more". One entry per group in first-seen order, joined by ", ". A grouped ledger prints
its group name; an UNGROUPED ledger prints its own full name (owner ruling (b), 2 Oct: never
joined by the name rule), identical names once.

`ledger_family.customer_header_words(entries)`: `entries` is a list of
`(customer_name, group_name or None)`; returns the header string.

`ledger_family.UNGROUPED_JOINS_NAME_MATCHED_GROUP` (owner ruling pending, default False): an
ungrouped ledger whose name rule key equals a group's key either stays its own family (False)
or joins that group (True). One switch drives the formatter, `ledger_family_key` and
`gate._cust_base`.

Written BEFORE any implementation; each test imports the seam inside its body.
"""
from __future__ import annotations

import pytest

HANLIM = "HANLIM TRADING SDN BHD"
HANLIM_NAMES = [
    "HANLIM TRADING SDN BHD [A/C I]",
    "HANLIM TRADING SDN BHD [A/C II]",
    "HANLIM TRADING SDN BHD [A/C III]",
    "HANLIM TRADING SDN BHD [A/C IV]",
    "HANLIM TRADING SDN BHD",
    "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)",
]
JUBIN_G = "JUBIN BMS SDN BHD"
JUBIN_NS = "JUBIN BMS (NS) SDN BHD [A/C I]"
JUBIN_GROUPED = ["JUBIN BMS (1990) SDN BHD", "JUBIN BMS (1990) SDN BHD [A/C I]"]

#: The owner's dealer (80560c8f-...), twelve linked ledgers, interleaved on purpose.
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


def _lf():
    from app.services import ledger_family

    return ledger_family


def _mapping(names, group):
    return {n: group for n in names}


# ============================================================ the formatter


def test_dealer_example_is_the_owner_string_group_names_only():
    out = _lf().customer_header_words(DEALER)
    assert out == DEALER_EXPECTED
    assert "accounts)" not in out and " more" not in out


def test_six_hanlim_ledgers_are_one_group_name_with_no_count():
    out = _lf().customer_header_words([(n, HANLIM) for n in HANLIM_NAMES])
    assert out == HANLIM


def test_an_ungrouped_single_ledger_prints_its_own_full_name():
    assert _lf().customer_header_words([("KEDAI X SDN BHD [A/C I]", None)]) == "KEDAI X SDN BHD [A/C I]"


def test_ungrouped_ledgers_print_their_own_names_never_joined_by_the_name_rule():
    """Owner ruling (b), 2 Oct 2026: "we shouldn't do automated process like this, very
    dangerous". A ledger joins a company line only through its explicit group; an ungrouped
    ledger is its own customer under its own full name. Identical names print once."""
    out = _lf().customer_header_words(
        [
            ("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I]", None),
            ("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C II]", None),
            ("CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I]", None),
        ]
    )
    assert out == (
        "CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C I], CHENG HUAT HARDWARE (SENTUL) SDN BHD [A/C II]"
    )


def test_homemart_ledgers_in_the_hardware_group_merge_into_its_entry():
    out = _lf().customer_header_words(
        [
            ("CHIN CHUN HARDWARE SDN BHD [A/C I]", "CHIN CHUN HARDWARE SDN BHD"),
            ("CHIN CHUN HOMEMART SDN BHD [A/C I]", "CHIN CHUN HARDWARE SDN BHD"),
        ]
    )
    assert out == "CHIN CHUN HARDWARE SDN BHD"


# ============================================================ the collision switch


def test_the_collision_switch_is_off():
    assert _lf().UNGROUPED_JOINS_NAME_MATCHED_GROUP is False


def test_ungrouped_ledger_with_a_group_matching_name_stays_its_own_customer():
    lf = _lf()
    entries = [(n, JUBIN_G) for n in JUBIN_GROUPED] + [(JUBIN_NS, None)]
    assert lf.customer_header_words(entries) == f"{JUBIN_G}, {JUBIN_NS}"


def test_ledger_family_key_and_gate_cust_base_keep_the_ungrouped_ledger_apart():
    from app.services.chatbot.lanes.business import gate

    lf = _lf()

    def match(name):
        return {"canonical_code": "ZZT-1", "display": {"customer_name": name}}

    with lf.customer_groups(_mapping(JUBIN_GROUPED, JUBIN_G)):
        assert lf.ledger_family_key(JUBIN_NS) != lf.ledger_family_key(JUBIN_GROUPED[1])
        assert gate._cust_base(match(JUBIN_NS)) != gate._cust_base(match(JUBIN_GROUPED[1]))


# ============================================================ name-only headers, turn groups


def _hanlim_rows():
    return [
        {"uuid": f"u{i}", "entity_type": "customer", "display_name": n, "code": "300-H030"}
        for i, n in enumerate(HANLIM_NAMES)
    ]


def _customer_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Customer:"))


def test_scope_block_header_names_the_group_only():
    from app.services.chatbot.tail.scope_block import search_scope_header

    lf = _lf()
    with lf.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        header = search_scope_header(
            domain="order",
            qf={},
            gate_json={"compatible_entities": _hanlim_rows()},
            resolver_json=None,
        )
    line = _customer_line(header)
    assert line == f"Customer: {HANLIM}"
    assert "accounts)" not in line and " more" not in line


def test_miss_path_header_names_the_group_only():
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    lf = _lf()
    parser = {"domain_hint": "order", "entities": [], "routing": {}, "access_levels": []}
    resolved = {
        "by_entity_type": {"customer": ["300-H030"]},
        "resolutions": [],
        "unresolved_tokens": ["ZZXX"],
        "tokens": [],
    }
    gate = {"gate_passed": True, "compatible_entities": _hanlim_rows()}
    with lf.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
    line = _customer_line(out["escalate_message"])
    assert line == f"Customer: {HANLIM}"
    assert "accounts)" not in line and " more" not in line


def test_compose_subject_line_names_the_group_only():
    from types import SimpleNamespace

    from app.services.chatbot.turn.compose import _subject_line

    lf = _lf()
    rows = [{"display_name": n, "raw": n} for n in HANLIM_NAMES]
    state = SimpleNamespace(
        focus=SimpleNamespace(customers=rows, products=[], warehouse=[]),
    )
    with lf.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        out = _subject_line(state, "warehouse")
    assert _customer_line(out) == f"Customer: {HANLIM}"


# ============================================================ roster and refusal: group name only


def test_roster_line_and_no_such_account_print_the_group_name_with_no_count():
    import uuid

    from app.services.chatbot.lanes.business import resolve_gate
    from app.services.chatbot.turn.narrow import _options

    lf = _lf()
    ids = [str(uuid.uuid4()) for _ in HANLIM_NAMES]
    cands = [{"raw": n, "name": n, "uuid": i, "canonical_code": f"ZZT-{i[:6]}"} for n, i in zip(HANLIM_NAMES, ids)]
    customers = [{"uuid": i, "display": {"customer_name": n}} for n, i in zip(HANLIM_NAMES, ids)]
    levels = {i: 1 for i in ids}

    with lf.customer_groups(_mapping(HANLIM_NAMES, HANLIM)):
        options = _options(cands, "customer", "ledger_family")
        refusal = resolve_gate._no_such_account("hanlim", 7, customers, levels)

    assert [o["label"] for o in options] == [HANLIM]
    assert refusal.startswith(f"{HANLIM} has no Account 7.")
    assert "accounts)" not in refusal
