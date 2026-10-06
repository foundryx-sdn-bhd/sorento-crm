"""ACCESS-MODEL final review (red): incoming-field parity in the legacy mapping (AC-AM-19, 20, 20b).

What a contact holding `incoming_stock_enquiries` sees today on the incoming-stock endpoint:
  agent-wide default rows (`agent_field_access`, contact_id NULL, is_allowed)
  + its per-contact rows (an override either way)
  + `field_access.DEFAULT_ALLOWED` (unless a row says otherwise).
After `map_legacy_contacts` the effective `incoming_stock.*` attributes must EQUAL that set, and
the loss and gain reports must cover incoming fields (rows name the contact and the key).
"""
from __future__ import annotations

from sqlalchemy import text

from tests.chatbot._access_seed import make_customer_link
from tests.chatbot.test_chatbot_access_migration import (
    ALL_AGENTS,
    ALL_SEVEN,
    DEFAULT_ALLOWED,
    FIVE_AGENTS,
    INCOMING_FIELDS,
    K_PLACED,
    K_SELLABLE,
    _access,
    _agents,
    _legacy_contact,
    _role_codes,
    _seeded,
)

AGENT = "incoming_stock_enquiries"
ETA = "estimated_arrival_date"
DELAY = "eta_delay_date"
CONSIGNEE = "consignee"
CONTAINER = "shipping_container_number"


def _row(db, field, allowed, contact=None, agent=AGENT) -> None:
    db.execute(
        text(
            "INSERT INTO agent_field_access (id, agent_code, resource, field_key, contact_id, is_allowed) "
            "VALUES (gen_random_uuid(), :a, 'incoming_stock', :f, :c, :ok)"
        ),
        {"a": agent, "f": field, "c": contact, "ok": allowed},
    )
    db.commit()


def _incoming(db, pk) -> set[str]:
    return {a.removeprefix("incoming_stock.") for a in _access(db, pk).attributes if a.startswith("incoming_stock.")}


def _expected(defaults_on=(), defaults_off=(), mine_on=(), mine_off=()) -> set[str]:
    resolved = {f: True for f in DEFAULT_ALLOWED}
    resolved.update({f: True for f in defaults_on})
    resolved.update({f: False for f in defaults_off})
    resolved.update({f: True for f in mine_on})
    resolved.update({f: False for f in mine_off})
    return {f for f, ok in resolved.items() if ok}


def _mentions(report, pk, key=None) -> list:
    return [
        r for r in report
        if pk in set(map(str, r.values())) and (key is None or key in set(map(str, r.values())))
    ]


def _world(session_factory):
    db, wid = _seeded(session_factory)
    return db, wid, _agents(db)


def _management(db, wid, agents):
    return _legacy_contact(db, wid, agents, agents=ALL_AGENTS, keys=ALL_SEVEN, name="Holder M")


def test_a_management_holder_whose_old_set_was_eta_plus_defaults_does_not_gain_the_other_fields(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    pk = _management(db, wid, agents)
    map_legacy_contacts(db)
    db.commit()
    assert _role_codes(db, pk) == ["management"]
    assert _incoming(db, pk) == _expected()
    assert CONSIGNEE not in _incoming(db, pk)


def test_an_agent_wide_default_row_allowing_a_field_outside_the_role_survives_the_mapping(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)
    sales = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Holder S")
    map_legacy_contacts(db)
    db.commit()
    assert _role_codes(db, sales) == ["sales_office"]
    assert _incoming(db, sales) == _expected(defaults_on=[DELAY])


def test_a_dealer_keeps_the_container_number_and_a_default_row_field(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)
    dealer = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Holder D")
    make_customer_link(db, dealer)
    map_legacy_contacts(db)
    db.commit()
    assert _role_codes(db, dealer) == ["dealer"]
    got = _incoming(db, dealer)
    assert CONTAINER in got
    assert got == _expected(defaults_on=[DELAY])


def test_per_contact_rows_override_the_agent_default_in_either_direction(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)  # agent-wide allow
    _row(db, "loc", True)  # agent-wide allow
    pk = _management(db, wid, agents)
    _row(db, DELAY, False, contact=pk)  # this contact is denied what the default allows
    _row(db, ETA, False, contact=pk)  # and denied a DEFAULT_ALLOWED field
    _row(db, "etd_date", True, contact=pk)
    map_legacy_contacts(db)
    db.commit()
    assert _incoming(db, pk) == _expected(
        defaults_on=[DELAY, "loc"], mine_on=["etd_date"], mine_off=[DELAY, ETA]
    )


def test_an_agent_wide_default_deny_of_a_default_allowed_field_is_honoured(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, CONTAINER, False)
    pk = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Holder P")
    map_legacy_contacts(db)
    db.commit()
    assert _incoming(db, pk) == _expected(defaults_off=[CONTAINER])


def test_the_loss_and_gain_reports_are_empty_for_incoming_fields_after_mapping(session_factory):
    from app.services.chatbot.access_seed import access_gain_report, access_loss_report, map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)
    mgmt = _management(db, wid, agents)
    dealer = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Holder D2")
    make_customer_link(db, dealer)
    map_legacy_contacts(db)
    db.commit()
    for pk in (mgmt, dealer):
        assert _mentions(access_loss_report(db), pk) == []
        assert _mentions(access_gain_report(db), pk) == []


def test_negative_control_the_gain_report_names_a_contact_and_incoming_key_when_a_minus_override_is_lost(session_factory):
    from app.models.chatbot_access import ContactAccessOverride
    from app.services.chatbot.access_seed import access_gain_report, map_legacy_contacts

    db, wid, agents = _world(session_factory)
    pk = _management(db, wid, agents)
    map_legacy_contacts(db)
    db.commit()
    db.query(ContactAccessOverride).filter(
        ContactAccessOverride.contact_id == pk, ContactAccessOverride.granted.is_(False)
    ).delete()
    db.commit()
    assert CONSIGNEE in _incoming(db, pk), "the broken mapping widens the contact"
    rows = _mentions(access_gain_report(db), pk, f"incoming_stock.{CONSIGNEE}")
    assert rows, "a gained incoming field must be reported with the contact and the key"


def test_negative_control_the_loss_report_names_a_contact_and_incoming_key_when_a_plus_override_is_lost(session_factory):
    from app.models.chatbot_access import ContactAccessOverride
    from app.services.chatbot.access_seed import access_loss_report, map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)
    sales = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Holder S3")
    map_legacy_contacts(db)
    db.commit()
    db.query(ContactAccessOverride).filter(
        ContactAccessOverride.contact_id == sales, ContactAccessOverride.granted.is_(True)
    ).delete()
    db.commit()
    assert DELAY not in _incoming(db, sales)
    rows = _mentions(access_loss_report(db), sales, f"incoming_stock.{DELAY}")
    assert rows, "a lost default-row incoming field must be reported with the contact and the key"


# Legacy `field_access.decide` rules (re-check round): owner agent held; per-contact ALLOW rows
# count only under held agents; DENY rows count regardless of agent; an agent-wide deny wins.
HELD_PLAIN = ("general_enquiries", "incoming_stock_enquiries", "order_enquiries")
NO_INCOMING = ("general_enquiries", "order_enquiries", "ideation")


def test_a_stale_per_contact_allow_under_a_non_held_agent_is_ignored(session_factory):
    from app.services.chatbot.access_seed import access_gain_report, map_legacy_contacts

    db, wid, agents = _world(session_factory)
    pk = _legacy_contact(db, wid, agents, agents=HELD_PLAIN, keys=(K_SELLABLE, K_PLACED), name="Holder T1")
    _row(db, "loc", True, contact=pk, agent="lead_time_enquiries")  # dropped agent
    map_legacy_contacts(db)
    db.commit()
    assert "loc" not in _incoming(db, pk)
    assert _incoming(db, pk) == _expected()
    assert _mentions(access_gain_report(db), pk) == []


def test_a_per_contact_deny_under_a_non_held_agent_still_denies(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    pk = _legacy_contact(db, wid, agents, agents=HELD_PLAIN, keys=(K_SELLABLE, K_PLACED), name="Holder T2")
    _row(db, ETA, False, contact=pk, agent="lead_time_enquiries")
    map_legacy_contacts(db)
    db.commit()
    assert ETA not in _incoming(db, pk)
    assert _incoming(db, pk) == _expected(mine_off=[ETA])


def test_a_holder_without_the_incoming_agent_sees_no_gated_incoming_fields(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, DELAY, True)  # agent-wide allow on an agent this contact does not hold
    pk = _legacy_contact(db, wid, agents, agents=NO_INCOMING, keys=ALL_SEVEN, name="Holder T3")
    map_legacy_contacts(db)
    db.commit()
    assert _role_codes(db, pk) == ["management"]
    got = _incoming(db, pk)
    assert got == _expected(), "only the DEFAULT_ALLOWED fields every caller already got"
    assert DELAY not in got and CONSIGNEE not in got


def test_two_held_agents_with_conflicting_agent_wide_rows_deny_wins(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid, agents = _world(session_factory)
    _row(db, "loc", True)
    _row(db, "loc", False, agent="general_enquiries")
    _row(db, DELAY, True, agent="general_enquiries")
    _row(db, DELAY, False)
    pk = _legacy_contact(db, wid, agents, agents=HELD_PLAIN, keys=(K_SELLABLE, K_PLACED), name="Holder T4")
    map_legacy_contacts(db)
    db.commit()
    got = _incoming(db, pk)
    assert "loc" not in got and DELAY not in got
    assert got == _expected()


def test_negative_control_the_gain_report_flags_a_widened_holder_without_the_incoming_agent(session_factory):
    from app.models.chatbot_access import ContactAccessOverride
    from app.services.chatbot.access_seed import access_gain_report, map_legacy_contacts

    db, wid, agents = _world(session_factory)
    pk = _legacy_contact(db, wid, agents, agents=NO_INCOMING, keys=ALL_SEVEN, name="Holder T5")
    map_legacy_contacts(db)
    db.commit()
    assert _mentions(access_gain_report(db), pk) == []
    db.query(ContactAccessOverride).filter(
        ContactAccessOverride.contact_id == pk, ContactAccessOverride.granted.is_(False)
    ).delete()
    db.commit()
    assert CONSIGNEE in _incoming(db, pk)
    assert _mentions(access_gain_report(db), pk, f"incoming_stock.{CONSIGNEE}")
