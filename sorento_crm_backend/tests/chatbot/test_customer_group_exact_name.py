"""CUSTOMER-GROUP re-review, owner ruling (b): a ledger joins a company line ONLY through its
own explicit group.

Contract for the name map (`customer_group_service.family_overrides`,
`ledger_family.customer_group_of`):
- a name maps to a group ONLY by its exact normalised full name, and only when every row with
  that exact name (in scope) sits in that same one group;
- no marker-stripped probe and no "equals a group's own name" probe;
- a name with rows in AND out of a group, or in two groups, is UNGROUPED (own full name, own
  key), never joined;
- an ungrouped ledger's label is its own full name, even when its rule key collides with a
  group's (no `[A/C n]` stripping).
"""
from __future__ import annotations

import uuid

from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._pg_fixture import blank_session
from tests.test_customer_header_echo import seed_ledgers

JUBIN_G = "JUBIN BMS SDN BHD"


def _lf():
    from app.services import ledger_family

    return ledger_family


def _cand(name):
    uid = str(uuid.uuid4())
    return {"raw": name, "name": name, "uuid": uid, "canonical_code": f"ZZT-{uid[:6]}"}


def _roster(names):
    from app.services.chatbot.turn.narrow import _options

    return _options([_cand(n) for n in names], "customer", "ledger_family")


# ============================================================ B1: no marker or own-name probe


def test_b1_an_ungrouped_ledger_is_not_pulled_in_by_marker_stripping_or_the_groups_own_name():
    lf = _lf()
    mapping = {JUBIN_G: JUBIN_G, f"{JUBIN_G} - [A/C I]": JUBIN_G}
    other = f"{JUBIN_G} (A/C 3)"

    with lf.customer_groups(mapping):
        assert lf.customer_group_of(other) is None
        assert lf.ledger_family_key(other) != lf.ledger_family_key(JUBIN_G)
        assert lf.customer_header_words([(JUBIN_G, JUBIN_G), (other, None)]) == f"{JUBIN_G}, {other}"
        options = _roster([JUBIN_G, other])
    assert [o["label"] for o in options] == [JUBIN_G, other]


def test_b1_bracketed_ledger_beside_a_grouped_bare_name_stays_its_own_customer():
    lf = _lf()
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        seed_ledgers(db, [("X SDN BHD", "X SDN BHD"), ("X SDN BHD [A/C II]", None)])
        from app.services import customer_group_service

        mapping = customer_group_service.family_overrides(db)

    with lf.customer_groups(mapping):
        assert lf.customer_group_of("X SDN BHD [A/C II]") is None
        assert lf.ledger_family_key("X SDN BHD [A/C II]") != lf.ledger_family_key("X SDN BHD")
        assert lf.customer_header_words([("X SDN BHD", "X SDN BHD"), ("X SDN BHD [A/C II]", None)]) == (
            "X SDN BHD, X SDN BHD [A/C II]"
        )
        options = _roster(["X SDN BHD", "X SDN BHD [A/C II]"])
    assert [o["label"] for o in options] == ["X SDN BHD", "X SDN BHD [A/C II]"]


# ============================================================ S1: ungrouped label is the full name


def test_s1_ungrouped_ledger_label_is_its_full_name_and_the_roster_has_two_labels():
    lf = _lf()
    ungrouped = f"{JUBIN_G} - [A/C II]"
    with lf.customer_groups({JUBIN_G: JUBIN_G}):
        assert lf.ledger_family_label(ungrouped) == ungrouped
        options = _roster([JUBIN_G, ungrouped])
    assert [o["label"] for o in options] == [JUBIN_G, ungrouped]


# ============================================================ S2: one name in AND out of a group


def test_s2_a_name_with_rows_in_and_out_of_a_group_is_ungrouped_and_printed_once():
    """`ZZT SAME SDN BHD` has one row in group `ZZT G GROUP SDN BHD` and one ungrouped, so the
    NAME is ambiguous: it is left out of the map and printed under its own name, once. The
    group's other member (`ZZT G OTHER SDN BHD`, exact name only in that group) still prints
    as the group. Ids in first-seen order [other, same-in-group, same-ungrouped] give
    "ZZT G GROUP SDN BHD, ZZT SAME SDN BHD"."""
    from app.services import customer_group_service
    from app.services.outstanding_report_service import _customer_echo

    lf = _lf()
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        # seed_ledgers makes ONE group per distinct group name, so put the second SAME row in
        # by hand after seeding the grouped pair.
        ids = seed_ledgers(
            db, [("ZZT G OTHER SDN BHD", "ZZT G GROUP SDN BHD"), ("ZZT SAME SDN BHD", "ZZT G GROUP SDN BHD")]
        )
        ids += seed_ledgers(db, [("ZZT SAME SDN BHD", None)])

        mapping = customer_group_service.family_overrides(db)
        assert "ZZT SAME SDN BHD" not in mapping
        assert mapping["ZZT G OTHER SDN BHD"] == "ZZT G GROUP SDN BHD"
        with lf.customer_groups(mapping):
            assert lf.customer_group_of("ZZT SAME SDN BHD") is None
        assert _customer_echo(db, None, ids) == "ZZT G GROUP SDN BHD, ZZT SAME SDN BHD"


def test_s2_the_ambiguous_name_equal_to_the_groups_own_name_is_still_ungrouped():
    """The group is itself called `ZZT SAME SDN BHD` and a ledger of that exact name is in it,
    another with the same name is not: the name is ambiguous, so it is not looked up as the
    group's own name either."""
    from app.services import customer_group_service

    lf = _lf()
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        seed_ledgers(
            db, [("ZZT G OTHER SDN BHD", "ZZT SAME SDN BHD"), ("ZZT SAME SDN BHD", "ZZT SAME SDN BHD")]
        )
        seed_ledgers(db, [("ZZT SAME SDN BHD", None)])
        mapping = customer_group_service.family_overrides(db)

    assert "ZZT SAME SDN BHD" not in mapping
    with lf.customer_groups(mapping):
        assert lf.customer_group_of("ZZT SAME SDN BHD") is None
