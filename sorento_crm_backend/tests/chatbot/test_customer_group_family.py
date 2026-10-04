"""Lane CUSTOMER-GROUP, slice S2 red tests (AC-10 .. AC-15): the chatbot family seam.

PLAN ``documentation/plans/master-data/PLAN-customer-group-2oct.md`` "Chatbot: one seam".

Contract under test: ``app.services.ledger_family.customer_groups(mapping)`` is a context
manager holding ``{normalised customer_name: group name}`` (upper-cased, whitespace collapsed)
for the turn; ``ledger_family_key`` / ``ledger_family_label`` consult it first and fall back to
the name rule. ``app.services.customer_group_service.family_overrides(db)`` builds the mapping.

Written BEFORE any implementation. The seam is imported inside each test so every test fails on
its own assertion or on the missing function, never at collection.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.access import RespondContactCustomer
from app.models.base import set_company_scope

from tests._ask_todo_seed import SORENTO
from tests._ask_todo_seed import contact as seed_contact
from tests._ask_todo_seed import customer as seed_customer
from tests._pg_fixture import blank_session

HARDWARE = "CHIN CHUN HARDWARE SDN BHD"
HOMEMART = "CHIN CHUN HOMEMART SDN BHD"
HANLIM = "HANLIM TRADING SDN BHD"
JUBIN_NS = "JUBIN BMS (NS) SDN BHD"
JUBIN_1990 = "JUBIN BMS (1990) SDN BHD"


def _seam():
    from app.services import ledger_family

    return ledger_family


def _u() -> str:
    return str(uuid.uuid4())


def _cand(name: str, uid: str | None = None) -> dict:
    uid = uid or _u()
    return {"raw": name, "name": name, "uuid": uid, "canonical_code": f"ZZT-{uid[:6]}"}


# ============================================================ AC-10 the seam


def test_ac10_mapped_name_follows_the_group_and_everything_else_follows_the_rule():
    lf = _seam()
    rule_key = lf.ledger_family_key(f"{HOMEMART} [A/C I]")
    rule_label = lf.ledger_family_label(f"{HOMEMART} [A/C I]")

    # Exact full names only (ruling (b)): the marker-stripped probe is gone, so the mapping
    # names the bare row AND the `[A/C I]` row.
    with lf.customer_groups({HOMEMART: HARDWARE, f"{HOMEMART} [A/C I]": HARDWARE}):
        assert lf.ledger_family_key(f"{HOMEMART} [A/C I]") == lf.ledger_family_key(HARDWARE)
        assert lf.ledger_family_key(HOMEMART) == lf.ledger_family_key(HARDWARE)
        assert lf.ledger_family_label(HOMEMART) == HARDWARE
        # A name that is not in the map gives exactly the rule's output.
        assert lf.ledger_family_key("OTHER SHOP SDN BHD [A/C I]") == "OTHER SHOP"
        assert lf.ledger_family_label("OTHER SHOP SDN BHD [A/C I]") == "OTHER SHOP SDN BHD"

    # Outside the context the rule only.
    assert lf.ledger_family_key(f"{HOMEMART} [A/C I]") == rule_key == "CHIN CHUN HOMEMART"
    assert lf.ledger_family_label(f"{HOMEMART} [A/C I]") == rule_label


def test_ac10_the_map_is_keyed_on_the_normalised_name_so_case_and_spacing_do_not_matter():
    lf = _seam()
    with lf.customer_groups({"CHIN CHUN HOMEMART SDN BHD": HARDWARE}):
        assert lf.ledger_family_label("chin  chun   homemart sdn bhd") == HARDWARE


def test_ac10_loader_maps_grouped_customers_and_drops_a_name_in_two_groups():
    from app.services import customer_group_service

    with blank_session() as db:
        set_company_scope(db, frozenset({SORENTO}))
        g1, g2 = _u(), _u()
        for gid, name in ((g1, HARDWARE), (g2, "ZZT OTHER GROUP")):
            db.execute(
                text(
                    "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
                    "VALUES (:i, :c, :n, now(), now())"
                ),
                {"i": gid, "c": SORENTO, "n": name},
            )
        grouped = seed_customer(db, f"{HOMEMART} [A/C I]", None)
        split_a = seed_customer(db, "SPLIT NAME SDN BHD", None)
        split_b = seed_customer(db, "SPLIT NAME SDN BHD", None)
        seed_customer(db, "NO GROUP SDN BHD", None)
        for row, gid in ((grouped, g1), (split_a, g1), (split_b, g2)):
            db.execute(
                text("UPDATE customers SET customer_group_id = :g WHERE id = :c"), {"g": gid, "c": row.id}
            )
        db.commit()

        mapping = customer_group_service.family_overrides(db)

        assert mapping[f"{HOMEMART} [A/C I]"] == HARDWARE
        assert "SPLIT NAME SDN BHD" not in mapping
        assert "NO GROUP SDN BHD" not in mapping


# ============================================================ AC-11 .. AC-13 narrow roster


def _roster(candidates):
    from app.services.chatbot.turn.narrow import _options

    return _options(candidates, "customer", "ledger_family")


def test_ac11_hanlim_roster_is_one_line_with_or_without_the_seeded_group():
    lf = _seam()
    ids = [_u(), _u(), _u()]
    names = [f"{HANLIM} [A/C I]", f"{HANLIM} [A/C II]", HANLIM]
    cands = [_cand(n, i) for n, i in zip(names, ids)]

    def check(options):
        assert [o["label"] for o in options] == [HANLIM]
        assert sorted(options[0]["uuids"]) == sorted(ids)

    check(_roster(cands))
    with lf.customer_groups({n.upper(): HANLIM for n in names}):
        check(_roster(cands))


def test_ac12_two_families_merge_into_one_line_once_grouped():
    lf = _seam()
    a, b = _u(), _u()
    cands = [_cand(f"{HARDWARE} [A/C I]", a), _cand(f"{HOMEMART} [A/C I]", b)]

    assert len(_roster(cands)) == 2

    with lf.customer_groups({f"{HARDWARE} [A/C I]": HARDWARE, f"{HOMEMART} [A/C I]": HARDWARE}):
        options = _roster(cands)
    assert [o["label"] for o in options] == [HARDWARE]
    assert sorted(options[0]["uuids"]) == sorted([a, b])


def test_ac13_a_name_the_rule_merges_splits_when_only_one_is_grouped():
    lf = _seam()
    ns, y1990 = _u(), _u()
    cands = [_cand(f"{JUBIN_NS} [A/C I]", ns), _cand(f"{JUBIN_1990} [A/C I]", y1990)]

    assert len(_roster(cands)) == 1

    with lf.customer_groups({f"{JUBIN_1990} [A/C I]": JUBIN_1990}):
        options = _roster(cands)
    assert len(options) == 2
    assert {o["uuids"][0] for o in options} == {ns, y1990}


def test_ac13_jubin_real_scenario_roster_and_exact_name_gate():
    from app.services.chatbot.lanes.business import resolve_gate

    lf = _seam()
    group = "JUBIN BMS SDN BHD"
    plain, a1, a3, ns = _u(), _u(), _u(), _u()
    names = {
        plain: "JUBIN BMS (1990) SDN BHD",
        a1: "JUBIN BMS (1990) SDN BHD [A/C I]",
        a3: "JUBIN BMS (1990) SDN BHD [A/C III]",
        ns: "JUBIN BMS (NS) SDN BHD [A/C I]",
    }
    mapping = {names[u]: group for u in (plain, a1, a3)}

    with lf.customer_groups(mapping):
        options = _roster([_cand(names[u], u) for u in (plain, a1, a3, ns)])
        assert len(options) == 2
        by_label = {o["label"]: o for o in options}
        assert sorted(by_label[group]["uuids"]) == sorted([plain, a1, a3])
        (ns_line,) = [o for o in options if o["label"] != group]
        assert ns_line["uuids"] == [ns]

        matches = [
            {"uuid": u, "entity_type": "customer", "display": {"customer_name": names[u]}}
            for u in (plain, a1, a3, ns)
        ]
        parser = {
            "entities": [{"hint": "customer", "current_message": True, "raw": "jubin bms sdn bhd", "account": 1}]
        }
        resolved = {"resolutions": [{"token": "jubin bms sdn bhd", "matches": list(matches)}]}
        levels = {plain: None, a1: 1, a3: 3, ns: 1}

        refusal = resolve_gate.narrow_by_account(parser, resolved, lambda ids: {i: levels.get(i) for i in ids})

    assert refusal is None
    kept = {m["uuid"] for m in resolved["resolutions"][0]["matches"]}
    assert kept == {a1}, f"the NS ledger must not answer for the group's name: {kept}"


# ============================================================ AC-14 one test per call site


def test_ac14_compose_header_dedupes_ledgers_of_one_group():
    from app.services.chatbot.turn.compose import _header_subjects

    lf = _seam()
    entities = [f"{HARDWARE} [A/C I]", f"{HOMEMART} [A/C I]"]
    assert len(_header_subjects(entities)) == 2

    with lf.customer_groups({f"{HARDWARE} [A/C I]": HARDWARE, f"{HOMEMART} [A/C I]": HARDWARE}):
        assert _header_subjects(entities) == [HARDWARE]


def test_ac14_session_state_picker_family_uuids_follow_the_group():
    from app.services.chatbot.session_state import _legacy_option

    lf = _seam()
    first, second = _u(), _u()
    row = {"idx": 1, "label": f"{HOMEMART} [A/C I]", "uuid": second, "entity_type": "customer"}
    # `picker_families` keyed by the key of the line the gate wrote: the group's.
    families = {lf.ledger_family_key(HARDWARE): [first, second]}

    assert "uuids" not in _legacy_option(row, "customer", 1, families)

    with lf.customer_groups({f"{HOMEMART} [A/C I]": HARDWARE}):
        option = _legacy_option(row, "customer", 1, families)
    assert option["uuids"] == [first, second]


def test_ac14_resolve_gate_no_such_account_names_the_group():
    from app.services.chatbot.lanes.business import resolve_gate

    lf = _seam()
    a, b = _u(), _u()
    customers = [
        {"uuid": a, "display": {"customer_name": f"{HARDWARE} [A/C I]"}},
        {"uuid": b, "display": {"customer_name": f"{HOMEMART} [A/C I]"}},
    ]
    levels = {a: 1, b: 2}

    rule = resolve_gate._no_such_account("chin chun", 3, customers, levels)
    assert rule.startswith('None of the customers matching "chin chun" has Account 3')

    with lf.customer_groups({f"{HARDWARE} [A/C I]": HARDWARE, f"{HOMEMART} [A/C I]": HARDWARE}):
        line = resolve_gate._no_such_account("chin chun", 3, customers, levels)
    assert line.startswith(f"{HARDWARE} has no Account 3.")


def test_ac14_gate_cust_base_returns_the_group_key_for_a_mapped_match():
    from app.services.chatbot.lanes.business import gate

    lf = _seam()
    match = {"canonical_code": "ZZT-1", "display": {"customer_name": HOMEMART}}
    assert gate._cust_base(match) == "CHIN CHUN HOMEMART"

    with lf.customer_groups({HOMEMART: HARDWARE}):
        assert gate._cust_base(match) == "CHIN CHUN HARDWARE"
    assert gate._cust_base(match) == "CHIN CHUN HOMEMART"


def test_ac14_stock_ask_contact_label_follows_the_group():
    from app.services import stock_ask_service

    with blank_session() as db:
        set_company_scope(db, frozenset({SORENTO}))
        gid = _u()
        db.execute(
            text(
                "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
                "VALUES (:i, :c, :n, now(), now())"
            ),
            {"i": gid, "c": SORENTO, "n": HARDWARE},
        )
        contact_id = seed_contact(db, "Ah Seng")
        rows = [seed_customer(db, f"{HARDWARE} [A/C I]", None), seed_customer(db, f"{HOMEMART} [A/C I]", None)]
        for row in rows:
            db.add(RespondContactCustomer(id=_u(), contact_id=contact_id, customer_id=row.id, company_id=SORENTO))
        db.flush()
        # Before grouping: two shops, so no trading name (today's behaviour).
        assert stock_ask_service._family_names_by_contact(db, {contact_id}) == {}
        for row in rows:
            db.execute(
                text("UPDATE customers SET customer_group_id = :g WHERE id = :c"), {"g": gid, "c": row.id}
            )
        db.commit()

        assert stock_ask_service._family_names_by_contact(db, {contact_id}) == {contact_id: HARDWARE}


# ============================================================ AC-15 no cache across turns


def test_ac15_the_next_entry_follows_the_new_mapping():
    lf = _seam()
    with lf.customer_groups({HOMEMART: HARDWARE}):
        assert lf.ledger_family_label(HOMEMART) == HARDWARE
    with lf.customer_groups({HOMEMART: "SOMEWHERE ELSE SDN BHD"}):
        assert lf.ledger_family_label(HOMEMART) == "SOMEWHERE ELSE SDN BHD"
    with lf.customer_groups({}):
        assert lf.ledger_family_label(HOMEMART) == HOMEMART
        assert lf.ledger_family_key(HOMEMART) == "CHIN CHUN HOMEMART"
