"""ACCESS-MODEL S3: a hand-off routes by the DOMAIN's escalation agent (AC-AM-17, AC-AM-18).

Seam: `turn_runtime.lane_parse_output` (turn_runtime.py ~1576-1596). It is the narrowest place
that knows the domain AND the routing pair together: it already fills `routing.suggested_team`
from `policy.domain(domain_hint).escalation_team_code`. The domain is lost after it: by the time
`lanes/escalation.py::_next_assignee_body` (:1539) and `_sla_body` (:1558) run, `ctx.parse.output`
carries only `routing.suggested_team` / `routing.suggested_agent` (they read
`routing.suggested_agent` at :1550 and :1578), and `context_item` (built at escalation.py:230-365)
holds `team`, `brand_code`, `company_id` but no domain. So the contract is: `lane_parse_output`
writes the domain row's `escalation_agent_code` over `routing.suggested_agent` (parser value kept
only when the row has none), and both body builders then carry it unchanged.

Red-first: `DomainPolicy.escalation_agent_code` and the `chatbot_domains.escalation_agent_code`
column do not exist yet.
"""
from __future__ import annotations

from sqlalchemy import text

from tests.chatbot._access_seed import make_domain, rid
from tests.chatbot._turn_helpers import POLICY_KIND_ROWS, TIER_ORDER_FIXTURE, _domain_row, verdict


def _policy(**agents):
    from app.services.chatbot.turn.policy import Policy

    rows = []
    for name, agent in agents.items():
        row = _domain_row(name, narrowing={})
        row["escalation_team_code"] = "customer_service"
        row["escalation_agent_code"] = agent
        rows.append(row)
    return Policy.from_rows(domains=rows, kinds=POLICY_KIND_ROWS, tier_order=TIER_ORDER_FIXTURE)


def _routed(policy, domain, parser_agent):
    from app.services.chatbot import turn_runtime

    v = verdict(domain_hint=domain)
    v["routing"] = {"suggested_team": None, "suggested_agent": parser_agent}
    return turn_runtime.lane_parse_output(v, domain=domain, policy=policy)


def _ctx(out):
    return {
        "parse": {"output": out},
        "contact": {"phone": "+60100000001"},
        "text": {"message": {"messageId": "42", "message": {"text": "hello"}}},
    }


_ITEM = {"team": "customer_service", "brand_code": None, "company_id": None}


def test_domain_policy_reads_the_escalation_agent_from_rows():
    policy = _policy(order="order_enquiries", inventory=None)
    assert policy.domain("order").escalation_agent_code == "order_enquiries"
    assert policy.domain("inventory").escalation_agent_code is None


def test_load_policy_reads_the_new_column(session_factory):
    from app.services.chatbot.turn.policy import load_policy

    db = session_factory()
    code = rid("agent")
    db.execute(
        text(
            "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
            "synced_to_excel) VALUES (gen_random_uuid(), :c, 'A', true, false, false)"
        ),
        {"c": code},
    )
    make_domain(db, "zzt_dom")
    db.execute(
        text("UPDATE chatbot_domains SET escalation_agent_code = :c WHERE name = 'zzt_dom'"), {"c": code}
    )
    db.commit()
    assert load_policy(db).domain("zzt_dom").escalation_agent_code == code


def test_the_domain_agent_overrides_the_parsers_guess():
    out = _routed(_policy(order="order_enquiries"), "order", parser_agent="general_enquiries")
    assert out["routing"]["suggested_agent"] == "order_enquiries"


def test_the_parser_agent_is_the_fallback_only_when_the_row_has_none():
    policy = _policy(inventory=None)
    assert policy.domain("inventory").escalation_agent_code is None  # the field exists, and is empty
    out = _routed(policy, "inventory", parser_agent="incoming_stock_enquiries")
    assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries"


def test_next_assignee_body_carries_the_domain_agent():
    from app.services.chatbot.lanes.escalation import _next_assignee_body

    out = _routed(_policy(order="order_enquiries"), "order", parser_agent="general_enquiries")
    assert _next_assignee_body(_ctx(out), dict(_ITEM))["agent_code"] == "order_enquiries"


def test_sla_body_carries_the_domain_agent():
    from app.services.chatbot.lanes.escalation import _sla_body

    out = _routed(_policy(order="order_enquiries"), "order", parser_agent="general_enquiries")
    body = _sla_body(_ctx(out), dict(_ITEM), {"assignee_id": "u-1"})
    assert body["agent_code"] == "order_enquiries"


def test_bodies_fall_back_to_the_parser_agent_when_the_row_has_none():
    from app.services.chatbot.lanes.escalation import _next_assignee_body, _sla_body

    policy = _policy(inventory=None)
    assert policy.domain("inventory").escalation_agent_code is None
    out = _routed(policy, "inventory", parser_agent="it_support")
    assert _next_assignee_body(_ctx(out), dict(_ITEM))["agent_code"] == "it_support"
    assert _sla_body(_ctx(out), dict(_ITEM), {"assignee_id": "u-1"})["agent_code"] == "it_support"


def test_contact_agent_access_does_not_influence_the_hand_off_agent(session_factory):
    """AC-AM-18: a contact with NO agent grants gets the domain agent, and so does one that
    holds a different agent. The loaded policy row decides, not contact_agent_access."""
    from app.services.chatbot.turn.policy import load_policy
    from tests.chatbot._access_seed import make_contact

    db = session_factory()
    domain_agent, other_agent = rid("dom-agent"), rid("other-agent")
    ids = {}
    for code in (domain_agent, other_agent):
        ids[code] = db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), :c, 'A', true, false, false) RETURNING id"
            ),
            {"c": code},
        ).scalar()
    make_domain(db, "zzt_dom", escalation_team_code="customer_service")
    db.execute(
        text("UPDATE chatbot_domains SET escalation_agent_code = :c WHERE name = 'zzt_dom'"),
        {"c": domain_agent},
    )
    holder, _ = make_contact(db)
    db.execute(
        text(
            "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
            "is_allowed, synced_to_excel) VALUES (gen_random_uuid(), :c, '+60', :a, true, false)"
        ),
        {"c": holder, "a": ids[other_agent]},
    )
    make_contact(db)  # a second contact with no grants at all
    db.commit()

    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes.escalation import _next_assignee_body

    policy = load_policy(db)
    v = verdict(domain_hint="zzt_dom")
    v["routing"] = {"suggested_team": None, "suggested_agent": other_agent}
    out = turn_runtime.lane_parse_output(v, domain="zzt_dom", policy=policy)
    assert _next_assignee_body(_ctx(out), dict(_ITEM))["agent_code"] == domain_agent
