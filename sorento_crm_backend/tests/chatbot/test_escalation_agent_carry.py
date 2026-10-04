"""RED tests for AC-1784..AC-1795 (PLAN-chatbot-escalation-agent-carry.md).

Written test-first, Phase 2: `turn_runtime.with_routing_agent_default` does not yet take
`pending=`/`session=` keyword arguments, and nothing yet writes an `"agent"` key onto an
escalation offer's `payload`. Every test below either imports a symbol/keyword that does
not exist yet (an immediate `TypeError`/`ImportError`) or drives the real engine and
asserts the CARRIED agent, which today falls back to `DEFAULT_SUGGESTED_AGENT`
(`"general_enquiries"`) instead of the minting turn's real one.

Postgres only, via `tests/chatbot/conftest.py`'s blank-schema `session_factory`. Every
scenario seeds its own contact/workspace/product chain; nothing is read off a shared row.
Nothing here reaches an LLM, n8n, respond.io or the real MCP server (`conftest.py`'s
autouse guards already forbid the LLM/MCP paths; the `/external/next-assignee` and SLA
seams are captured by monkeypatching the exact in-process functions
`app.services.chatbot.lanes.escalation_services._next_assignee` /
`_sla_create` call - `app.api.v1.external.next_assignee.post_next_assignee` and
`ConversationSLATrackingService.create_tracking` - the same boundary
`test_s5_escalation_seams.py` documents as "the two CRM services stubbed at their own
boundary").

**Design decisions, flagged rather than silently assumed** (per the captain's brief):

* AC-1784/AC-1786/AC-1791/AC-1794 build "turn 1" by writing the session state a real
  minting turn would have LEFT BEHIND (`respond_contacts.session_vars.open_question`,
  the exact `Pending.to_wire()` shape) directly, the same pattern
  `test_rearch_s6_handpass1_findings.py::_seed_top_level_session_vars` already uses to
  represent a prior turn. Turn 2 - the acceptance turn the carry fix actually lives in -
  runs for real through `engine.run_turn`, all the way to the captured `/external/
  next-assignee` body. The offer-MINTING half (does turn 1 itself WRITE `payload["agent"]`
  correctly) is covered separately by AC-1792, which drives two real minting turns
  through the engine (a single-team and a multi-team `team_pick`, both reproducible today
  with a single zero-stock product) plus one direct call into the production
  `answer_bridge.answer_for` entry point for the roster case.
* AC-1786 hand-builds a multi-team pending with two options carrying DIFFERENT
  `payload["team"]`/`payload["agent"]` pairs. Production's own multi-team mint site
  (`turn/compose.py::_team_pick_question`, exercised for real in AC-1792) can only ever
  give every option the SAME agent (one parser verdict, one `routing.suggested_agent`),
  so a genuine "two different agents" fixture has to be built by hand - this tests the
  ACCEPTANCE side's per-option read (the thing the carry fix touches), not any one
  mint site's internal signature, per the captain's own instruction not to overfit here.

**Review round 1 (reviewer verdict READY, SHOULDs folded, coder-authored red-then-green
per the small-fix-track brief):** AC-1796/AC-1797 pin the acceptable-offer GATE
(SHOULD-1); AC-1798 pins the member-option FALL-THROUGH (SHOULD-3); AC-1799 covers the
six further mint sites SHOULD-2 named, one end-to-end (the company clarify's own
answering "1" turn) and five pure assertions straight against the private mint
functions themselves, cheaper than an engine replay for the same observation; AC-1800
is NIT-7's direct `_team_pick_question` unit test. AC-1788 and AC-1790, and half of
AC-1787 and AC-1793, are DELETED rather than adjusted (SHOULD-4 retired the `session=`
fallback they pinned) - see the comment above `TestPrecedenceChain`.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import DEFAULT_SUGGESTED_AGENT
from app.services.chatbot import turn_runtime
from app.services.chatbot.turn.pending import ask as pending_ask

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _link_contact_company, _seed_workspace

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")


# --------------------------------------------------------------------------- #
# Shared builders
# --------------------------------------------------------------------------- #


def _db(session_factory, *, scope: frozenset[str] = frozenset({SORENTO})):
    """Stamp `company_scope` explicitly, same reasoning as every other S3+ test that
    needs several `session_factory()` calls in one seed chain (the lazily-firing
    `after_begin` default races across them otherwise)."""
    db = session_factory()
    db.info["company_scope"] = scope
    return db


def _seed_contact(session_factory, *, phone: str) -> None:
    workspace_id = _seed_workspace(session_factory)
    db = _db(session_factory)
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb), :wid)"
        ),
        {"cid": str(CONTACT_ID), "phone": phone, "sv": json.dumps({}), "wid": workspace_id},
    )
    db.commit()
    _link_contact_company(session_factory, company_id=SORENTO)


def _seed_product(session_factory, *, code: str) -> None:
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    db = _db(session_factory)
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=f"ZZTC-{code}",
        category_name="ZZT",
        class_label="zzt",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=f"ZZTU-{code}", uom_name="ZZT uom")
    db.add_all([cat, uom])
    db.flush()
    db.add(
        Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=f"ZZT {code}",
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=1,
        )
    )
    db.commit()


def _seed_product_with_brand(session_factory, *, code: str, brand_code: str | None) -> None:
    """`_seed_product`'s twin, with a REAL `brands` row on `Product.brand_id` - the
    resolver's own post-pass (`entity_resolver.py::_attach_brand_info`) stamps
    `display.brand.brand_code` off this exact FK, which is what
    `lanes/business/gate.py::run_gate` reads into `routing_brand` (AC-1804..1806,
    round 4, owner-approved). `brand_code=None` seeds a product with NO brand at
    all - the gate then has nothing to resolve (AC-1806's own no-brand case)."""
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    db = _db(session_factory)
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=f"ZZTC-{code}",
        category_name="ZZT",
        class_label="zzt",
        search_synonyms=[],
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=f"ZZTU-{code}", uom_name="ZZT uom")
    db.add_all([cat, uom])
    brand_id = None
    if brand_code:
        brand = Brand(id=str(uuid.uuid4()), brand_code=brand_code, brand_name=f"ZZT {brand_code.title()}")
        db.add(brand)
        db.flush()
        brand_id = brand.id
    db.flush()
    db.add(
        Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=f"ZZT {code}",
            category_id=cat.id,
            base_uom_id=uom.id,
            brand_id=brand_id,
            list_price=1,
        )
    )
    db.commit()


def _seed_escalation_team(
    session_factory,
    *,
    agent_code: str,
    team_code: str,
    team_label: str,
    members: list[tuple[str, list[str]]],
) -> dict[str, str]:
    """A round-robin-ready `(agent, team)` pair, seeded fresh (owner ruling,
    AC-1804..1806's own brief: "seed team_member_brands rows yourself... never read
    existing data"). Generic over WHICH agent/team/members, so AC-1804/1805's own
    Packing List (`incoming_stock_enquiries`/`purchasing`, Lucas=mocha,
    Jereen=every other brand) and AC-1807/1808's Marketing - Product
    (`general_enquiries`/`marketing_product`, Kia Yee=mocha, Tay Zhi Yang=every
    other brand) share ONE seeding shape rather than two copies of the same
    SLAPolicy/AgentTeam scaffolding.

    `members` is `[(name, brands), ...]`, sort order = list order. Returns
    `{name.lower(): user_id, "team_id": ..., "agent_id": ...}`.
    """
    from app.models.access import AccessAgent, AgentTeam, Team, TeamMember, team_member_brands
    from app.models.sla import SLAPolicy, SLAPolicyTier
    from app.models.user import User
    from app.services.chatbot.lanes.escalation import NEXT_ASSIGNEE_POLICY_CODE, NEXT_ASSIGNEE_TIER

    db = _db(session_factory)
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code=agent_code, name=f"ZZT {agent_code}", is_active=True))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name=f"ZZT {team_label}", company_id=SORENTO))
    # `_next_assignee_body` always sends the escalation lane's own literal policy
    # code/tier (`NEXT_ASSIGNEE_POLICY_CODE`/`NEXT_ASSIGNEE_TIER`) - the REAL
    # `/external/next-assignee` handler 404s without a matching row, unlike the
    # mocked seam the file's other tests use.
    policy_id = str(uuid.uuid4())
    db.add(
        SLAPolicy(
            id=policy_id, code=NEXT_ASSIGNEE_POLICY_CODE, name="ZZT Policy", is_active=True, company_id=SORENTO
        )
    )
    db.flush()
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=NEXT_ASSIGNEE_TIER,
            tier_name="ZZT Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )
    db.flush()

    def _member(name: str, brands: list[str], sort_order: int) -> str:
        user_id = str(uuid.uuid4())
        slug = name.lower().replace(" ", "-")
        db.add(User(id=user_id, email=f"zzt-{slug}@zzt.test", name=f"ZZT {name}", status="ACTIVE"))
        db.flush()
        member_id = str(uuid.uuid4())
        db.add(TeamMember(id=member_id, team_id=team_id, user_id=user_id, sort_order=sort_order))
        db.flush()
        for code in brands:
            db.execute(team_member_brands.insert().values(team_member_id=member_id, brand_code=code))
        db.flush()
        return user_id

    ids: dict[str, str] = {}
    for sort_order, (name, brands) in enumerate(members, start=1):
        ids[name.lower()] = _member(name, brands, sort_order)

    db.add(
        AgentTeam(
            id=str(uuid.uuid4()), agent_id=agent_id, code=team_code, team_id=team_id, tier=1, company_id=SORENTO
        )
    )
    db.commit()
    return {**ids, "team_id": team_id, "agent_id": agent_id}


def _seed_packing_list_team(session_factory) -> dict[str, str]:
    """AC-1804/AC-1805's own team: Lucas (tagged ONLY `mocha`) and Jereen (every OTHER
    brand this test names - `sorento` and `cabana` - never `mocha`), matching the prod
    tagging the owner described ("Jereen = every brand except mocha, Lucas = mocha")."""
    result = _seed_escalation_team(
        session_factory,
        agent_code="incoming_stock_enquiries",
        team_code="purchasing",
        team_label="Packing List",
        members=[("Lucas", ["mocha"]), ("Jereen", ["sorento", "cabana"])],
    )
    return {"lucas_id": result["lucas"], "jereen_id": result["jereen"], **result}


def _stub_incoming_probe_empty(monkeypatch) -> None:
    """Every MCP probe (incoming stock included) answers "nothing" - a genuine ETA miss,
    no network reached (`conftest.py::_no_real_mcp_calls` would otherwise raise)."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        return json.dumps({"answers": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)


def _write_open_question(session_factory, *, open_question: dict[str, Any]) -> None:
    """Overwrite `respond_contacts.session_vars` with a top-level five-key shape whose
    `open_question` is exactly this pending's wire form - the same shape
    `turn_runtime.load_state` reads on the NEXT turn (`session_state.five_keys`)."""
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :c"),
        {
            "sv": json.dumps(
                {
                    "focus": None,
                    "open_question": open_question,
                    "ideation": None,
                    "access_levels": [],
                    "contains_flyer": False,
                }
            ),
            "c": str(CONTACT_ID),
        },
    )
    db.commit()


def _capture_next_assignee(monkeypatch, *, response: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Replace the REAL `/external/next-assignee` handler
    (`app.api.v1.external.next_assignee.post_next_assignee`, the exact function
    `escalation_services._next_assignee`'s closure imports and calls) with a fake that
    records every body and returns a canned assignee - no database write, no real
    round-robin draw. Same seam `test_s5_escalation_seams.py`'s module docstring names.
    """
    import app.api.v1.external.next_assignee as next_assignee_mod

    bodies: list[dict[str, Any]] = []
    canned = {
        "assignee_id": "zzt-usr-1",
        "assignee_email": "pic@zzt.example",
        "assignee_name": "ZZT PIC",
        "assignee_respond_user_id": "zzt-respond-usr-1",
        "team_set_code": "ZZT",
        "brand_code": None,
        "company_id": None,
        "is_already_assigned": False,
        **(response or {}),
    }

    async def fake_post_next_assignee(body: dict, current_user: dict = None, db: Any = None):
        bodies.append(dict(body))
        return canned

    monkeypatch.setattr(next_assignee_mod, "post_next_assignee", fake_post_next_assignee)
    return bodies


def _capture_real_next_assignee(monkeypatch) -> list[dict[str, Any]]:
    """AC-1804..1806 (round 4): WRAPS the REAL `/external/next-assignee` handler
    rather than replacing it - every request body AND its real response are
    captured, so the assertion is against an ACTUAL round-robin draw over seeded
    `team_member_brands` rows (`_seed_packing_list_team`), not a canned stub. The
    escalation lane's own closure re-imports `post_next_assignee` off the module at
    CALL time (`escalation_services._next_assignee`'s own docstring), so patching
    the module attribute here is picked up the same way `_capture_next_assignee`'s
    fake already proves it is."""
    import app.api.v1.external.next_assignee as next_assignee_mod

    real = next_assignee_mod.post_next_assignee
    calls: list[dict[str, Any]] = []

    async def wrapped(body: dict, current_user: dict = None, db: Any = None):
        response = await real(body=body, current_user=current_user, db=db)
        calls.append({"body": dict(body), "response": dict(response)})
        return response

    monkeypatch.setattr(next_assignee_mod, "post_next_assignee", wrapped)
    return calls


def _capture_sla(monkeypatch) -> list[Any]:
    """Replace `ConversationSLATrackingService.create_tracking` - the payload it is
    given is a REAL `ConversationSLATrackingCreate`, validated for real, so the SLA
    body's shape is exercised exactly as `_sla_create`'s own closure builds it."""
    from app.services.sla_service import ConversationSLATrackingService

    captured: list[Any] = []

    def fake_create_tracking(self, payload: Any) -> Any:
        captured.append(payload)
        return type(
            "FakeRow",
            (),
            {
                "id": "zzt-sla-1",
                "initiated_at": None,
                "due_at": None,
                "due_at_resolution": None,
            },
        )()

    monkeypatch.setattr(ConversationSLATrackingService, "create_tracking", fake_create_tracking)
    return captured


def _yes_verdict(**overrides: Any) -> dict[str, Any]:
    """The bare "yes" acceptance turn: no domain, no entities, no routing of its own.
    #1323: over an escalation offer `decide()` accepts on the parser's semantic
    `is_escalation_confirmation` only, which the published prompt sets for a "yes"
    alongside `is_affirmative`; a roster's attached offer still reads `is_affirmative`."""
    base = verdict(
        message_type="casual",
        domain_hint=None,
        intent_hint=None,
        entities=[],
        is_affirmative=True,
        escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": None},
        routing={"suggested_team": None, "suggested_agent": None},
    )
    base.update(overrides)
    return base


def _second_turn_envelope(message_id: str = "ZZT-msg-2", text: str = "yes") -> Any:
    """A second turn's own envelope - a DIFFERENT `messageId` from `_envelope()`'s
    default (`"ZZT-msg-1"`), or D15's duplicate-message guard replays turn 1's own
    cached reply verbatim instead of processing this message at all (measured: the
    reply text and branch_kind both came back byte-identical to turn 1 until this was
    fixed - the deduplication working exactly as intended, on two calls this test
    itself made look like the same respond.io delivery)."""
    from tests.chatbot.test_engine import CONTACT_ID as _CID

    return _envelope(
        message={
            "event_type": "message.received",
            "contact": {"id": _CID},
            "message": {
                "messageId": message_id,
                "contactId": _CID,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": text},
            },
        }
    )


def _position_verdict(position: int, **overrides: Any) -> dict[str, Any]:
    """A numbered pick over a multi-team roster - the ONE other door
    `decide()`/`picked_positions` opens for an `expects == "pick"` team_pick."""
    base = verdict(
        message_type="casual",
        domain_hint=None,
        intent_hint=None,
        entities=[],
        reference_positions=[position],
        routing={"suggested_team": None, "suggested_agent": None},
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# AC-1784: the headline - two-turn engine replay, roster escalate offer
# --------------------------------------------------------------------------- #


class TestAC1784RosterEscalateOfferCarriesTheMintingTurnsAgent:
    def test_ac_1784_bare_yes_over_a_roster_escalate_offer_carries_the_minting_turns_agent(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000022")
        stub_parser(_yes_verdict())
        stub_access()
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)

        # Turn 1's own state: an incoming ETA miss whose reply carried a roster of
        # candidate products PLUS the escalate offer ("reply 'yes' to escalate to
        # purchasing team" - `payload["escalate_offered"] is True` is what turns a
        # bare "yes" over a still-open roster into an escalation acceptance,
        # `turn/apply.py:668`), parsed on that turn with
        # `routing = {"suggested_team": "purchasing", "suggested_agent":
        # "incoming_stock_enquiries"}`.
        _write_open_question(
            session_factory,
            open_question={
                "kind": "product_pick",
                "expects": None,
                "team": "purchasing",
                "asked_at_turn": 1,
                "payload": {
                    "domain": "incoming",
                    "escalate_offered": True,
                    "agent": "incoming_stock_enquiries",
                },
                "options": [
                    {
                        "position": 1,
                        "label": "SRTSC07-A",
                        "entity_type": "product",
                        "uuid": str(uuid.uuid4()),
                        "payload": {},
                    },
                    {
                        "position": 2,
                        "label": "SRTSC07-B",
                        "entity_type": "product",
                        "uuid": str(uuid.uuid4()),
                        "payload": {},
                    },
                ],
            },
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(bodies) == 1, f"expected exactly one next-assignee call: {bodies!r}"
        body = bodies[0]
        assert body["team_code"] == "purchasing", body
        # THE failing assertion today: with no fix, `with_routing_agent_default` never
        # reads the pending's carried agent, so this turn's own null parser routing
        # falls to `DEFAULT_SUGGESTED_AGENT` ("general_enquiries").
        assert body["agent_code"] == "incoming_stock_enquiries", body


# --------------------------------------------------------------------------- #
# AC-1785: single-team `team_pick`, fully real two-turn engine replay - no planting
# --------------------------------------------------------------------------- #


class TestAC1785SingleTeamTeamPickCarriesTheMintingTurnsAgentNotTheDefault:
    def test_ac_1785_single_team_team_pick_carries_the_minting_turns_agent(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000015")
        _seed_product(session_factory, code="ZZTSC07")
        _stub_incoming_probe_empty(monkeypatch)
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)

        # Turn 1: a real ETA miss, no siblings at all - `turn/compose.py::
        # _team_pick_question`'s single-team branch ("Would you like me to escalate to
        # purchasing team?", one "Yes" option), reachable end to end with one seeded
        # zero-stock product and no did-you-mean roster.
        stub_parser(
            verdict(
                domain_hint="incoming",
                intent_hint="check_incoming",
                entities=[entity("ZZTSC07", hint="product", confident=True)],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        stub_access()
        turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert turn1.branch_kind == "business_query", turn1.branch_kind

        row = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).first()
        open_question = (row.session_vars or {}).get("open_question")
        assert open_question is not None and open_question.get("kind") == "team_pick", open_question
        assert open_question.get("team") == "purchasing", open_question

        # Turn 2: the bare "yes" - the parser names no team and no agent of its own.
        stub_parser(_yes_verdict())
        result = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(bodies) == 1, bodies
        body = bodies[0]
        assert body["team_code"] == "purchasing", body
        assert body["agent_code"] == "incoming_stock_enquiries", body
        assert body["agent_code"] != DEFAULT_SUGGESTED_AGENT, (
            "today's bug: the carried agent falls to the hard default"
        )


# --------------------------------------------------------------------------- #
# AC-1786: multi-team `team_pick`, numbered pick carries THAT option's own agent
# --------------------------------------------------------------------------- #


class TestAC1786MultiTeamPickCarriesThePickedOptionsOwnAgent:
    def _plant_multi_team_pending(self, session_factory) -> None:
        _write_open_question(
            session_factory,
            open_question={
                "kind": "team_pick",
                "expects": "pick",
                "team": None,
                "asked_at_turn": 1,
                "payload": {},
                "options": [
                    {
                        "position": 1,
                        "label": "stock",
                        "entity_type": "team",
                        "payload": {"team": "warehouse", "agent": "general_enquiries"},
                    },
                    {
                        "position": 2,
                        "label": "incoming stock",
                        "entity_type": "team",
                        "payload": {"team": "purchasing", "agent": "incoming_stock_enquiries"},
                    },
                    {
                        "position": 3,
                        "label": "No it's okay",
                        "entity_type": "team",
                        "payload": {"team": None, "agent": None, "hold": True},
                    },
                ],
            },
        )

    def test_ac_1786_picking_option_two_by_number_carries_option_twos_agent(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000014")
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        self._plant_multi_team_pending(session_factory)

        stub_parser(_position_verdict(2))
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(bodies) == 1, bodies
        body = bodies[0]
        assert body["team_code"] == "purchasing", body
        assert body["agent_code"] == "incoming_stock_enquiries", (
            f"picking option 2 must carry option 2's OWN agent, not option 1's "
            f"('general_enquiries') and not the default: {body!r}"
        )
        assert body["agent_code"] != "general_enquiries", body


# --------------------------------------------------------------------------- #
# AC-1787, AC-1789: `with_routing_agent_default`'s precedence chain, pure python.
#
# AC-1788 and AC-1790 are RETIRED (reviewer round 1, SHOULD-4): both pinned a
# `session=` fallback (`_prior_suggested_agent`, a `variables.routing.suggested_agent`
# nest one turn back) that no writer in this codebase ever produces for the agent half
# - unlike the team half, which `_prior_suggested_team` reads off a nest a real writer
# DOES still produce. `with_routing_agent_default` no longer takes a `session=` keyword
# at all, so their own two tests (and the ONE half of AC-1787/AC-1793 that exercised the
# same session carry) are deleted rather than left calling a removed parameter.
# --------------------------------------------------------------------------- #


class TestPrecedenceChain:
    def test_ac_1787_a_parser_named_agent_this_turn_is_never_overridden(self) -> None:
        verdict_in = {"routing": {"suggested_team": "purchasing", "suggested_agent": "order_enquiries"}}
        pending = pending_ask(
            "product_pick",
            [],
            team="purchasing",
            payload={"escalate_offered": True, "agent": "incoming_stock_enquiries", "domain": "incoming"},
        )

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "order_enquiries", out

    def test_ac_1789_no_offer_parser_null_falls_to_the_hard_default(self) -> None:
        verdict_in = {"routing": {"suggested_team": None, "suggested_agent": None}}

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=None)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out


# --------------------------------------------------------------------------- #
# AC-1796/AC-1797 (reviewer round 1, SHOULD-1): `_accepted_pending_agent` is gated on
# the SAME condition `turn/apply.py:691` uses for "this offer is acceptable" -
# `pending.kind in OFFER_KINDS or pending.payload.get("escalate_offered") is True`.
# Without the gate a roster pending with no escalate offer attached could still supply
# an agent here while `lane_parse_output`'s own team chain (which does not read that
# same pending at all) left the team at its default - the pair disagreeing.
# --------------------------------------------------------------------------- #


class TestGateOnAcceptableOffers:
    def test_ac_1796_a_roster_without_escalate_offered_carries_no_agent(self) -> None:
        verdict_in = {"routing": {"suggested_team": None, "suggested_agent": None}}
        # `product_pick` is a ROSTER kind, not in `OFFER_KINDS` - and this one carries
        # NO `escalate_offered` flag, unlike AC-1784's roster (still open while the
        # customer asks about something else entirely, the fresh-handover shape the
        # review measured the 404 on).
        pending = pending_ask(
            "product_pick",
            [{"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}}],
            team="purchasing",
            payload={"agent": "incoming_stock_enquiries"},
        )

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out

    def test_ac_1797_the_same_roster_with_escalate_offered_does_carry(self) -> None:
        # Review round 2, SHOULD-1 residual: `escalate_offered` alone is no longer
        # enough - the turn must also ACCEPT (`TestAC1802EscalateOfferedRosterCarries
        # OnlyOnAccept` pins that axis on its own). This AC's own point survives
        # unmodified once accept is granted: the gate's OTHER side, an
        # `escalate_offered` roster (not `OFFER_KINDS`) DOES carry, where AC-1796's
        # otherwise-identical roster without the flag does not.
        verdict_in = {"routing": {"suggested_team": None, "suggested_agent": None}, "is_affirmative": True}
        pending = pending_ask(
            "product_pick",
            [{"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}}],
            team="purchasing",
            payload={"escalate_offered": True, "agent": "incoming_stock_enquiries"},
        )

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out


# --------------------------------------------------------------------------- #
# AC-1798 (reviewer round 1, SHOULD-3): a numbered pick that lands on a MEMBER option
# (a combined roster+CS offer's own member half - no `payload["agent"]`, no
# `payload["hold"]`) falls through to the pending's own top-level agent, since picking
# one IS an escalation acceptance (`turn/apply.py:546`); only the explicit hold option
# short-circuits to `None`.
# --------------------------------------------------------------------------- #


class TestMemberOptionFallThrough:
    def _plant_combined_pending(self):
        return pending_ask(
            "team_pick",
            [
                {"position": 1, "label": "Jane Doe", "entity_type": "member", "payload": {}},
                {
                    "position": 2,
                    "label": "No it's okay",
                    "entity_type": "team",
                    "payload": {"team": None, "agent": None, "hold": True},
                },
            ],
            team=None,
            expects="pick",
            payload={"agent": "incoming_stock_enquiries"},
        )

    def test_ac_1798_a_member_option_pick_carries_the_pendings_own_agent(self) -> None:
        verdict_in = _position_verdict(1)
        pending = self._plant_combined_pending()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out

    def test_ac_1798_the_hold_option_pick_carries_none_and_falls_to_the_default(self) -> None:
        verdict_in = _position_verdict(2)
        pending = self._plant_combined_pending()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out


# --------------------------------------------------------------------------- #
# AC-1791: access is checked against the CARRIED agent, not the default
# --------------------------------------------------------------------------- #


class TestAC1791AccessCheckedAgainstTheCarriedAgent:
    def _plant_roster_offer(self, session_factory) -> None:
        _write_open_question(
            session_factory,
            open_question={
                "kind": "product_pick",
                "expects": None,
                "team": "purchasing",
                "asked_at_turn": 1,
                "payload": {
                    "domain": "incoming",
                    "escalate_offered": True,
                    "agent": "incoming_stock_enquiries",
                },
                "options": [
                    {
                        "position": 1,
                        "label": "SRTSC07-A",
                        "entity_type": "product",
                        "uuid": str(uuid.uuid4()),
                        "payload": {},
                    },
                ],
            },
        )

    def _spy_access(self, monkeypatch, *, allowed_agent: str):
        calls: list[str | None] = []

        def fake_check_access(db, *, agent_code, contact_id, space_id):
            calls.append(agent_code)
            return {
                "allowed": agent_code == allowed_agent,
                "decision": "allow" if agent_code == allowed_agent else "deny_no_access",
                "agent_name": "General Enquiries",
                "attributes": None,
                "all_attributes_allowed": None,
            }

        monkeypatch.setattr(engine_mod, "check_access", fake_check_access)
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
        return calls

    def test_ac_1791_a_contact_granted_only_the_carried_agent_is_allowed_on_the_yes_turn(
        self, session_factory, stub_parser, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000012")
        self._plant_roster_offer(session_factory)
        stub_parser(_yes_verdict())
        calls = self._spy_access(monkeypatch, allowed_agent="incoming_stock_enquiries")
        _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert calls == ["incoming_stock_enquiries"], calls
        assert result.branch_kind != "access_denied", result.branch_kind

    def test_ac_1791_a_contact_granted_only_the_default_agent_is_denied_on_the_yes_turn(
        self, session_factory, stub_parser, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000021")
        self._plant_roster_offer(session_factory)
        stub_parser(_yes_verdict())
        calls = self._spy_access(monkeypatch, allowed_agent=DEFAULT_SUGGESTED_AGENT)
        # Today's bug means this check is made against `DEFAULT_SUGGESTED_AGENT`, which
        # this contact IS granted - so today the turn is wrongly ALLOWED through and
        # reaches the real escalation lane. Mocked so that misbehaviour never reaches a
        # real service call while this test is red for the documented reason.
        _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert calls == ["incoming_stock_enquiries"], (
            "the access check must still be made against the CARRIED agent, not the "
            f"default, even though this contact's grant denies it: {calls!r}"
        )
        assert result.branch_kind == "access_denied", result.branch_kind


# --------------------------------------------------------------------------- #
# AC-1792: the offer WRITE side - the minting turn stores the agent on the payload
# --------------------------------------------------------------------------- #


class TestAC1792OfferPayloadCarriesTheAgent:
    def test_ac_1792_single_team_team_pick_stores_the_agent_on_pending_payload(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000010")
        _seed_product(session_factory, code="ZZTSC08")
        _stub_incoming_probe_empty(monkeypatch)
        stub_parser(
            verdict(
                domain_hint="incoming",
                intent_hint="check_incoming",
                entities=[entity("ZZTSC08", hint="product", confident=True)],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        stub_access()

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        row = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).first()
        open_question = (row.session_vars or {}).get("open_question") or {}
        assert open_question.get("kind") == "team_pick", open_question
        assert open_question.get("payload", {}).get("agent") == "incoming_stock_enquiries", (
            f"a single-team team_pick must store the minting turn's agent on "
            f"pending.payload['agent']: {open_question!r}"
        )

    def test_ac_1792_multi_team_team_pick_stores_the_agent_on_each_option_and_none_on_the_hold(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000018")
        _seed_product(session_factory, code="ZZTSC09")
        _stub_incoming_probe_empty(monkeypatch)
        stub_parser(
            verdict(
                asks=[{"domain": "inventory"}, {"domain": "incoming"}],
                domain_hint=None,
                intent_hint=None,
                entities=[entity("ZZTSC09", hint="product", confident=True)],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        stub_access()

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        row = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).first()
        open_question = (row.session_vars or {}).get("open_question") or {}
        assert open_question.get("kind") == "team_pick", open_question
        options = open_question.get("options") or []
        assert len(options) == 3, options
        non_hold = [o for o in options if not (o.get("payload") or {}).get("hold")]
        hold = [o for o in options if (o.get("payload") or {}).get("hold")]
        assert non_hold, options
        for option in non_hold:
            assert option["payload"].get("agent") == "incoming_stock_enquiries", (
                f"each real option must carry the minting turn's agent beside its own "
                f"team: {option!r}"
            )
            assert "team" in option["payload"], option
        assert hold, options
        assert hold[0]["payload"].get("agent") is None, (
            f"the hold option ('No it's okay') must carry agent: None, exactly like "
            f"its team: {hold[0]!r}"
        )

    def test_ac_1792_roster_escalate_offer_stores_the_agent_on_pending_payload(self) -> None:
        """The did-you-mean/sibling roster mint site (`answer_bridge.py::_miss_question`),
        driven through the real, already-proven production entry point
        `answer_bridge.answer_for` - the same seam
        `test_rearch_r4_bridge_miss.py::TestDidYouMeanRosterMintsAProductPick` already
        exercises for the team half (`pending.team == "purchasing"`), extended with a
        real `routing.suggested_agent` on the minting turn."""
        from app.services.chatbot import copy as copy_mod
        from app.services.chatbot.lanes.business.services import AnswerServices

        bridge = pytest.importorskip("app.services.chatbot.answer_bridge")

        raw = "SRTWT165-FT"
        parser = {
            "domain_hint": "incoming",
            "intent_hint": "check_incoming",
            "message_type": "business_query",
            "entities": [{"raw": raw, "hint": "product", "current_message": True, "confident": True}],
            "routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            "access_levels": [],
        }
        resolved = {
            "resolutions": [
                {
                    "token": raw,
                    "matches": [],
                    "alternatives": [
                        {
                            "canonical_code": "11111111-1111-4111-8111-111111111111",
                            "entity_type": "product",
                            "uuid": "11111111-1111-4111-8111-111111111111",
                            "display": {"product_name": "SRTWT165-FTX"},
                            "match_tier": "fuzzy",
                        },
                        {
                            "canonical_code": "22222222-2222-4222-8222-222222222222",
                            "entity_type": "product",
                            "uuid": "22222222-2222-4222-8222-222222222222",
                            "display": {"product_name": "SRTWT165-FTY"},
                            "match_tier": "fuzzy",
                        },
                    ],
                }
            ],
            "unresolved_tokens": [raw],
            "tokens": [raw],
        }
        gate = {"gate_passed": True, "compatible_entities": [], "gate_debug": {"domain": "incoming"}}
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}
        services = AnswerServices(
            mcp_probe=lambda name, args: {"has_result": False, "answers": []},
            family_fetch=lambda query: {"data": []},
        )

        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx={"parse": {"output": parser}, "contact": {"id": "zzt-1792-contact"}, "session": {}},
            canned=copy_mod.fallback_copy(),
            services=services,
            db=None,
            asked_at_turn=3,
        )

        assert answer is not None
        assert answer.question is not None, "a did-you-mean offer must raise a Pending"
        pending = answer.question
        assert pending.kind == "product_pick", pending.kind
        assert pending.team == "purchasing", pending.team
        assert pending.payload.get("escalate_offered") is True, pending.payload
        assert pending.payload.get("agent") == "incoming_stock_enquiries", (
            f"the roster escalate offer must store the minting turn's agent on "
            f"pending.payload['agent']: {pending.payload!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1793: a HAND-BUILT pending with no agent falls through to the default cleanly, no
# KeyError. NIT-5 (reviewer round 1): an ENGINE-minted pending never actually stores
# `agent: None` in practice - `with_routing_agent_default` has already filled the hard
# default into `routing.suggested_agent` for THIS turn by the time any mint site reads
# it, so a real mint's own top-level payload always carries a real agent code.
# `agent: None` is reachable only by hand-building a pending directly, as below.
# --------------------------------------------------------------------------- #


class TestAC1793NoAgentOnThePendingFallsThroughCleanly:
    def test_ac_1793_a_hand_built_pending_with_agent_none_falls_to_the_hard_default(
        self,
    ) -> None:
        verdict_in = {"routing": {"suggested_team": None, "suggested_agent": None}}
        pending = pending_ask(
            "team_pick",
            [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
            team="customer_service",
            payload={"agent": None},
        )

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out
        # /external/next-assignee 400s on an empty agent_code - the carry must never
        # land there as "".
        assert out["routing"]["suggested_agent"] != "", out


# --------------------------------------------------------------------------- #
# AC-1794: the SLA body carries the same agent_code as the next-assignee body
# --------------------------------------------------------------------------- #


class TestAC1794SlaBodyCarriesTheSameAgentCodeAsNextAssignee:
    def test_ac_1794_sla_body_and_next_assignee_body_agree_on_agent_code(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000013")
        _seed_product(session_factory, code="ZZTSC10")
        _stub_incoming_probe_empty(monkeypatch)
        stub_parser(
            verdict(
                domain_hint="incoming",
                intent_hint="check_incoming",
                entities=[entity("ZZTSC10", hint="product", confident=True)],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        stub_access()
        turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert turn1.branch_kind == "business_query", turn1.branch_kind

        next_assignee_bodies = _capture_next_assignee(monkeypatch)
        sla_bodies = _capture_sla(monkeypatch)
        stub_parser(_yes_verdict())

        result = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(next_assignee_bodies) == 1, next_assignee_bodies
        assert len(sla_bodies) == 1, sla_bodies
        assert next_assignee_bodies[0]["agent_code"] == "incoming_stock_enquiries", next_assignee_bodies
        assert sla_bodies[0].agent_code == next_assignee_bodies[0]["agent_code"], (
            sla_bodies[0].agent_code,
            next_assignee_bodies[0]["agent_code"],
        )


# --------------------------------------------------------------------------- #
# AC-1795: the TEAM chain in `lane_parse_output` is byte-for-byte unchanged
# --------------------------------------------------------------------------- #


class TestAC1795TeamChainFallsToThePriorCarryUnaffectedByTheAgentCarry:
    def test_ac_1795_team_precedence_chain_is_unaffected_by_the_agent_carry(self) -> None:
        """A pin, not a new behaviour: whatever `lane_parse_output`'s own team chain
        computes, it must read exactly as it does today regardless of what
        `suggested_agent` the SAME call carries - the two axes are independent
        fields on one dict. The real regression gate is
        `tests/chatbot/test_s5_escalation_lane.py` and
        `tests/chatbot/test_turn_replay.py`, run in full alongside this file (both
        stay green, per the plan).

        Owner ruling 23 Sep 2026, R9 (fix round 5): flipped the TEAM assertion below
        from "purchasing" to "warehouse" - `verdict_in` names no `is_affirmative`, no
        escalation confirmation and no `reference_positions` landing on the open
        offer's own option, so this is a FRESH-question shape (no `accepted_team` or
        `policy` is passed either, so the domain rung cannot resolve one either), and
        it falls through to the prior session's carried "warehouse" instead.

        S10 (fix round 6): `offer_pending` below is passed but no longer read for
        its team AT ALL - `lane_parse_output` has no open-offer arm left
        (`accepted_team`, sourced from `turn/apply.py::_answer_pending`'s own
        `trace.team`, is the only path an accepted offer's team reaches this
        function by). The class is renamed to name the value this test actually
        pins ("warehouse", the prior carry), not the retired "pending wins"
        behaviour its old name described. The AGENT assertion, this test's own
        actual purpose, is untouched by either R9 or S10 - the agent chain does not
        read `pending`/acceptance at all."""
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": "incoming_stock_enquiries"},
            "entities": [],
        }
        offer_pending = pending_ask(
            "team_pick",
            [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
            team="purchasing",
        )
        prior_session = {"session_vars": {"variables": {"routing": {"suggested_team": "warehouse"}}}}

        out = turn_runtime.lane_parse_output(
            verdict_in,
            pending=offer_pending,
            prior_session=prior_session,
        )

        # R9: this verdict does not ACCEPT the open offer, so the prior session's own
        # carried team is what answers instead - see the docstring above.
        assert out["routing"]["suggested_team"] == "warehouse", out["routing"]
        # And the agent this call was given rides straight through, untouched by the
        # team chain above it.
        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out["routing"]


# --------------------------------------------------------------------------- #
# AC-1799 (reviewer round 1, SHOULD-2): the six remaining mint sites each stamp THIS
# turn's `routing.suggested_agent` the same way the two already-fixed sites do. The
# company clarify's own answering "1" turn is the one end-to-end case (a real engine
# replay through to the captured `/external/next-assignee` body); every other site
# gets a pure assertion straight against the private mint function itself - none of
# them touch the database or the parser, so there is no reason to pay for an engine
# replay just to observe one dict.
# --------------------------------------------------------------------------- #


class TestAC1799SixMoreMintSitesStampTheAgent:
    def test_company_clarify_answering_turn_carries_the_agent_on_the_wire_body(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The one end-to-end case: turn 1's state is planted as `engine.py::
        _question_offered`'s own company-clarify arm would have left it (payload's
        top-level `agent`, per-option `company`/`company_id`, exactly the shape
        `_option_payload`'s "company" branch plus this fix's own top-level stamp
        build), and turn 2 answers "1" for real through `engine.run_turn`, all the
        way to the captured `/external/next-assignee` body."""
        _seed_contact(session_factory, phone="+60900000016")
        _write_open_question(
            session_factory,
            open_question={
                "kind": "company_pick",
                "expects": "pick",
                "team": "purchasing",
                "asked_at_turn": 1,
                "payload": {"agent": "incoming_stock_enquiries"},
                "options": [
                    {
                        "position": 1,
                        "label": "ACME SDN BHD",
                        "entity_type": "company",
                        "payload": {
                            "company": "ACME SDN BHD",
                            "company_id": "zzt-company-1",
                            "brand_code": None,
                        },
                    },
                    {
                        "position": 2,
                        "label": "BETA SDN BHD",
                        "entity_type": "company",
                        "payload": {
                            "company": "BETA SDN BHD",
                            "company_id": "zzt-company-2",
                            "brand_code": None,
                        },
                    },
                ],
            },
        )
        stub_parser(_position_verdict(1))
        stub_access()
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(bodies) == 1, bodies
        body = bodies[0]
        assert body["team_code"] == "purchasing", body
        assert body["agent_code"] == "incoming_stock_enquiries", body

    def test_question_offered_team_clarify_stamps_agent_on_each_option(self) -> None:
        ctx = {
            "parse": {
                "output": {
                    "routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}
                }
            }
        }
        values = {
            "clarify": {"clarify_team": True, "clarify_team_options": [{"team": "purchasing", "label": "Purchasing"}]}
        }

        pending = engine_mod._question_offered(ctx, values, {}, {})

        assert pending is not None and pending.kind == "team_pick", pending
        assert pending.options[0]["payload"]["team"] == "purchasing", pending.options
        assert pending.options[0]["payload"]["agent"] == "incoming_stock_enquiries", pending.options

    def test_question_offered_member_offer_stamps_agent_on_top_level_payload(self) -> None:
        ctx = {"parse": {"output": {"routing": {"suggested_agent": "incoming_stock_enquiries"}}}}
        outcome = {"build-cs-member-offer": {"cs_last_result_set": [{"idx": 1, "label": "Jane Doe", "uuid": "u1"}]}}

        pending = engine_mod._question_offered(ctx, {}, outcome, {})

        assert pending is not None and pending.kind == "member_offer", pending
        assert pending.payload.get("agent") == "incoming_stock_enquiries", pending.payload

    def test_question_offered_escalate_catalog_stamps_agent_on_top_level_payload(self) -> None:
        ctx = {
            "parse": {
                "output": {
                    "routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}
                }
            }
        }
        outcome = {"escalate-catalog": {"is_escalate_offer": True}}

        pending = engine_mod._question_offered(ctx, {}, outcome, {})

        assert pending is not None and pending.kind == "team_pick", pending
        assert pending.team == "purchasing", pending
        assert pending.payload.get("agent") == "incoming_stock_enquiries", pending.payload

    def test_answer_bridge_miss_question_member_offer_arm_stamps_top_level_payload(
        self,
    ) -> None:
        bridge = pytest.importorskip("app.services.chatbot.answer_bridge")

        pending = bridge._miss_question(
            {},
            {"build-cs-member-offer": {"member_offer": True, "cs_last_result_set": [{"idx": 1, "label": "Jane Doe", "uuid": "u1"}]}},
            gate={},
            parser={"routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}},
            asked_at_turn=1,
            text="",
        )

        assert pending is not None and pending.kind == "member_offer", pending
        assert pending.payload.get("agent") == "incoming_stock_enquiries", pending.payload

    def _roster_re_arm(self, *, carried_agent: str | None, ctx_agent: str | None):
        """Drives `turn/compose.py::compose`'s roster re-arm directly: a STILL-OPEN
        roster (contract 36) that already carries an escalate offer, over a fresh miss
        on the SAME domain it was already escalating for - the branch at
        `compose.py:396-408` that keeps the carried pending rather than minting a
        fresh `_team_pick_question`."""
        from app.services.chatbot.turn.compose import compose as _compose
        from app.services.chatbot.turn.policy import Policy
        from app.services.chatbot.turn.state import Focus, Profile, State

        from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

        row = _domain_row("incoming", narrowing={"product": "narrow_to_code"})
        row["label"] = "Incoming (ETA)"
        row["escalation_team_code"] = "purchasing"
        policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)

        carried = pending_ask(
            "product_pick",
            [{"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}}],
            team="purchasing",
            payload={"agent": carried_agent, "escalate_offered": True},
        )
        state = State(focus=Focus(), pending=carried, profile=Profile(), turn_no=2)
        envelopes = [
            {"domain": "incoming", "denied": False, "entities": ["A"], "figures": [], "files": [], "miss": ["A"]}
        ]

        answer = _compose(envelopes, state, policy, SimpleNamespace(suggested_agent=ctx_agent))

        assert answer.question is not None and answer.question.kind == "product_pick", answer.question
        return answer.question

    def test_compose_roster_re_arm_keeps_its_own_carried_agent_over_ctx(self) -> None:
        question = self._roster_re_arm(carried_agent="incoming_stock_enquiries", ctx_agent="general_enquiries")

        assert question.payload.get("agent") == "incoming_stock_enquiries", question.payload

    def test_compose_roster_re_arm_keeps_no_agent_when_the_roster_already_has_a_team(self) -> None:
        # Corrected in review round 2, SHOULD-A (this test's own original name and
        # assertion, "falls to ctx when the roster carries no agent", was the BUG:
        # this helper's `carried` always carries its OWN team ("purchasing"), so
        # mixing THIS turn's ctx agent onto it is exactly the stale-team/fresh-agent
        # mismatch the fix closes. See `TestAC1801RosterReArmKeepsBothHalvesFrom
        # OneSource` for the fuller two-axis picture (team present vs absent).
        question = self._roster_re_arm(carried_agent=None, ctx_agent="incoming_stock_enquiries")

        assert question.payload.get("agent") is None, question.payload


# --------------------------------------------------------------------------- #
# AC-1800 (NIT-7, reviewer round 1): a direct unit test on
# `turn/compose.py::_team_pick_question` itself, not just through a full engine
# replay - the kill test in review round 1 showed the compose single-team branch was
# untested on its own.
# --------------------------------------------------------------------------- #


class TestAC1800TeamPickQuestionDirectUnitTest:
    def test_single_team_branch_stores_the_agent_on_the_pendings_payload(self) -> None:
        from app.services.chatbot.turn.compose import _team_pick_question
        from app.services.chatbot.turn.policy import Policy

        from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

        row = _domain_row("incoming", narrowing={"product": "narrow_to_code"})
        row["escalation_team_code"] = "purchasing"
        policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)

        pending = _team_pick_question(["incoming"], policy, agent="x")

        assert pending is not None
        assert pending.expects == "yes_no", pending
        assert pending.payload.get("agent") == "x", pending.payload


# --------------------------------------------------------------------------- #
# AC-1801 (review round 2, SHOULD-A, BLOCKING): the roster re-arm's `team` and
# `agent` come from the SAME source, not independently. A carried roster CAN hold a
# team with no agent at all (`answer_bridge.py`'s D4 narrower roster, `turn/
# apply.py`'s narrow ask), and mixing a STALE carried team with THIS turn's fresh
# agent produced a pair no `agent_teams` link exists for (measured: an incoming
# miss with no agent, re-armed under a later order-domain miss, paired
# `order_enquiries` with the OLD `purchasing` team).
# --------------------------------------------------------------------------- #


class TestAC1801RosterReArmKeepsBothHalvesFromOneSource:
    def _re_arm(
        self,
        *,
        carried_team: str | None,
        carried_agent: str | None,
        ctx_agent: str | None,
        miss_domain: str = "incoming",
        miss_team: str = "purchasing",
    ):
        from app.services.chatbot.turn.compose import compose as _compose
        from app.services.chatbot.turn.policy import Policy
        from app.services.chatbot.turn.state import Focus, Profile, State

        from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

        row = _domain_row(miss_domain, narrowing={"product": "narrow_to_code"})
        row["escalation_team_code"] = miss_team
        policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)

        carried = pending_ask(
            "product_pick",
            [{"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}}],
            team=carried_team,
            payload={"agent": carried_agent, "escalate_offered": True},
        )
        state = State(focus=Focus(), pending=carried, profile=Profile(), turn_no=2)
        envelopes = [
            {
                "domain": miss_domain,
                "denied": False,
                "entities": ["A"],
                "figures": [],
                "files": [],
                "miss": ["A"],
            }
        ]

        answer = _compose(envelopes, state, policy, SimpleNamespace(suggested_agent=ctx_agent))

        assert answer.question is not None and answer.question.kind == "product_pick", answer.question
        return answer.question

    def test_ac_1801_a_carried_team_with_no_agent_re_armed_under_a_different_domain_carries_no_agent(
        self,
    ) -> None:
        # The measured bug: an incoming miss left `team=purchasing` with no agent;
        # a LATER order-domain miss over the SAME still-open roster used to pair
        # THIS turn's own agent (`order_enquiries`) with the STALE carried team
        # (`purchasing`) - a pool `/external/next-assignee` has no link for. The
        # team stays exactly what it was (round 1's own rule, unchanged); the
        # agent now stays with it, not with this turn.
        question = self._re_arm(
            carried_team="purchasing",
            carried_agent=None,
            ctx_agent="order_enquiries",
            miss_domain="order",
            miss_team="order_team",
        )

        assert question.team == "purchasing", question.team
        assert question.payload.get("agent") is None, question.payload

    def test_ac_1801_a_carried_roster_with_no_team_takes_both_halves_from_this_turn(self) -> None:
        question = self._re_arm(
            carried_team=None,
            carried_agent=None,
            ctx_agent="incoming_stock_enquiries",
            miss_domain="incoming",
            miss_team="purchasing",
        )

        assert question.team == "purchasing", question.team
        assert question.payload.get("agent") == "incoming_stock_enquiries", question.payload

    def test_ac_1801_a_carried_roster_that_already_has_its_own_agent_keeps_it_over_this_turns(
        self,
    ) -> None:
        # Round 1's own case, pinned again alongside the new one: a roster that
        # already carries BOTH halves keeps its own, regardless of what this
        # turn's miss would otherwise have supplied.
        question = self._re_arm(
            carried_team="purchasing",
            carried_agent="incoming_stock_enquiries",
            ctx_agent="general_enquiries",
            miss_domain="incoming",
            miss_team="purchasing",
        )

        assert question.team == "purchasing", question.team
        assert question.payload.get("agent") == "incoming_stock_enquiries", question.payload


# --------------------------------------------------------------------------- #
# AC-1802 (review round 2, SHOULD-1 residual): the acceptable-offer gate is
# "ACCEPTABLE", not "ACCEPTED" - an `escalate_offered` roster (not an `OFFER_KINDS`
# pending) only carries its agent when THIS turn actually accepts it, the same
# signal `decide()` itself reads (`turn/decide.py:571-573`), or a numbered pick
# that lands on one of the roster's own options.
# --------------------------------------------------------------------------- #


class TestAC1802EscalateOfferedRosterCarriesOnlyOnAccept:
    def _plant(self):
        return pending_ask(
            "product_pick",
            [{"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}}],
            team="purchasing",
            payload={"escalate_offered": True, "agent": "incoming_stock_enquiries"},
        )

    def _plant_combined(self):
        """The REAL shape `_miss_question`'s owner-R2 combine mints (review round 2
        NIT-A's own citation, `turn/apply.py::_picks_a_member_option`): one roster,
        PRODUCT options first, CS-MEMBER options continuing the same numbering -
        `escalate_offered` sits on the pending's own top-level payload either way."""
        return pending_ask(
            "product_pick",
            [
                {"position": 1, "label": "SRTSC07-A", "entity_type": "product", "payload": {}},
                {"position": 2, "label": "Jane Doe", "entity_type": "member", "payload": {}},
            ],
            team="purchasing",
            payload={"escalate_offered": True, "agent": "incoming_stock_enquiries"},
        )

    def test_ac_1802_a_non_accepting_verdict_over_an_escalate_offered_roster_falls_to_the_default(
        self,
    ) -> None:
        # A message that names something else entirely while the roster (with its
        # attached escalate sentence) is still open - no yes, no escalation
        # confirmation, no number over the roster.
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": None},
            "is_affirmative": False,
            "escalation": {"is_escalation_confirmation": False},
        }
        pending = self._plant()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out

    def test_ac_1802_an_affirmative_verdict_over_the_same_roster_carries_the_agent(self) -> None:
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": None},
            "is_affirmative": True,
        }
        pending = self._plant()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out

    def test_ac_1802_an_escalation_confirmation_flag_also_carries(self) -> None:
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": None},
            "escalation": {"is_escalation_confirmation": True},
        }
        pending = self._plant()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out

    def test_ac_1802_a_numbered_pick_landing_on_a_member_option_also_carries(self) -> None:
        # The positive side of NIT-A: a numbered pick that lands on the roster's
        # OWN member option (the combined roster+CS-member shape) still counts as
        # an escalation acceptance, exactly as `_picks_a_member_option` reads it.
        verdict_in = _position_verdict(2)
        pending = self._plant_combined()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == "incoming_stock_enquiries", out

    def test_ac_1802_a_numbered_pick_landing_on_a_product_option_does_not_carry(self) -> None:
        # NIT-A (review round 2): a numbered pick over an ORDINARY product option
        # (not member-typed) is a business fetch, not an escalation acceptance -
        # measured: "2" over a did-you-mean roster that also carried an attached
        # escalate sentence used to carry the roster's agent onto a plain product
        # pick, with the team still at its default (the mismatched pair reaches
        # the access check and any miss-offer minted off that same turn).
        verdict_in = _position_verdict(1)
        pending = self._plant_combined()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out

    def test_ac_1802_an_affirmative_verdict_with_an_explicit_decline_does_not_carry(
        self,
    ) -> None:
        # NIT-B (review round 2): the accept signal is vetoed by the SAME two reads
        # `decide()`'s own qualifier uses (`turn/decide.py:571-575`) - a verdict
        # naming BOTH `is_affirmative: true` and `escalation.escalation_declined:
        # true` is not something the parser is asked to keep mutually exclusive,
        # and `decide()` treats the decline as decisive either way.
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": None},
            "is_affirmative": True,
            "escalation": {"escalation_declined": True},
        }
        pending = self._plant()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out

    def test_ac_1802_an_escalation_confirmation_with_is_affirmative_false_does_not_carry(
        self,
    ) -> None:
        # NIT-B's other half: `facts["negated"]` off `is_affirmative is False` -
        # this pins the same read `decide()` makes off a message the parser marked
        # as a genuine "no", proven through the escalation-confirmation door
        # (`is_escalation_confirmation` alone would otherwise carry, per the test
        # right above this one).
        verdict_in = {
            "routing": {"suggested_team": None, "suggested_agent": None},
            "is_affirmative": False,
            "escalation": {"is_escalation_confirmation": True},
        }
        pending = self._plant()

        out = turn_runtime.with_routing_agent_default(verdict_in, pending=pending)

        assert out["routing"]["suggested_agent"] == DEFAULT_SUGGESTED_AGENT, out


# --------------------------------------------------------------------------- #
# AC-1803 (review round 2, item 3): the two `team_pick` mint sites round 1 missed.
# The silent-company escalate offer's team is THIS turn's own `routing.
# suggested_team`, so the agent rides beside it; the cross-domain rung's offer
# names a team off the RUNG's own render block, never this turn's routing at all,
# and is deliberately left unstamped.
# --------------------------------------------------------------------------- #


class TestAC1803TheTwoMissedTeamPickMintSites:
    def test_the_silent_company_offer_stamps_this_turns_agent(self) -> None:
        from app.services.chatbot.turn.compose import Answer as _Answer

        bridge = pytest.importorskip("app.services.chatbot.answer_bridge")

        envelope = {
            "figures": [{"fields": [{"label": "Product", "value": "SRTSC07"}]}],
            "denied": False,
            "raw_fragment": {
                "fetch": {
                    "lookup_companies": [
                        {"id": "zzt-co-1", "name": "Sorento"},
                        {"id": "zzt-co-2", "name": "Mocha"},
                    ],
                    "answers": [
                        {
                            "fields": [
                                {"key": "company_name", "label": "Company", "value": "Sorento"}
                            ]
                        }
                    ],
                }
            },
        }
        parser = {"routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}}
        answer = _Answer(text="Found it in Sorento.", question=None)

        result = bridge.apply_silent_company_offer(
            answer, envelope=envelope, parser=parser, gate={}, asked_at_turn=1, turn_id="zzt-turn-1"
        )

        assert result.question is not None, result
        assert result.question.kind == "team_pick", result.question
        assert result.question.team == "purchasing", result.question
        assert result.question.payload.get("agent") == "incoming_stock_enquiries", result.question.payload

    def test_the_crossdomain_offer_pending_carries_no_agent(self) -> None:
        bridge = pytest.importorskip("app.services.chatbot.answer_bridge")

        result = {"render": {"_xdBlock": {"any": True, "block": "some rendered text", "team": "purchasing"}}}

        question = bridge._crossdomain_offer_pending(result, asked_at_turn=1)

        assert question is not None, question
        assert question.kind == "team_pick", question
        assert question.team == "purchasing", question
        assert question.payload.get("agent") is None, question.payload


# --------------------------------------------------------------------------- #
# AC-1804/AC-1805/AC-1806 (round 4, owner-approved scope extension, 22 Sep 2026):
# the BRAND rides the SAME acceptance carry the agent now uses, so a Packing List
# escalation draws the brand-tagged member instead of rotating the whole team -
# "a MOCHA product's escalation goes to Lucas, a SORENTO product's to Jereen"
# (Packing List tags in prod: Jereen = every brand except mocha, Lucas = mocha).
# AC-1804/AC-1805 run the REAL `/external/next-assignee` handler over a freshly
# seeded team (`_capture_real_next_assignee` + `_seed_packing_list_team`) rather
# than the file's usual canned stub, because "the drawn assignee is the
# brand-tagged member" is the actual claim under test - a mock could not prove it.
# --------------------------------------------------------------------------- #


class TestAC1804And1805And1806BrandCarriedToNextAssignee:
    def _run_incoming_miss_then_yes(
        self,
        session_factory,
        monkeypatch,
        stub_parser,
        stub_access,
        *,
        phone: str,
        product_code: str,
        brand_code: str | None,
    ) -> list[dict[str, Any]]:
        _seed_contact(session_factory, phone=phone)
        _seed_product_with_brand(session_factory, code=product_code, brand_code=brand_code)
        _seed_packing_list_team(session_factory)
        _stub_incoming_probe_empty(monkeypatch)
        stub_parser(
            verdict(
                domain_hint="incoming",
                intent_hint="check_incoming",
                entities=[entity(product_code, hint="product", confident=True)],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        stub_access()
        turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert turn1.branch_kind == "business_query", turn1.branch_kind

        stub_parser(_yes_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        result = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(calls) == 1, calls
        return calls

    def test_ac_1804_a_sorento_product_escalates_to_jereen(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        calls = self._run_incoming_miss_then_yes(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            phone="+60900000019",
            product_code="ZZTSC-SRT",
            brand_code="sorento",
        )

        body = calls[0]["body"]
        response = calls[0]["response"]
        assert body["brand_code"] == "sorento", body
        assert response.get("assignee_name") == "ZZT Jereen", (
            f"a SORENTO product must draw the sorento-tagged member (Jereen), not "
            f"rotate the whole team: {response!r}"
        )

    def test_ac_1805_a_mocha_product_escalates_to_lucas(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        calls = self._run_incoming_miss_then_yes(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            phone="+60900000023",
            product_code="ZZTSC-MCH",
            brand_code="mocha",
        )

        body = calls[0]["body"]
        response = calls[0]["response"]
        assert body["brand_code"] == "mocha", body
        assert response.get("assignee_name") == "ZZT Lucas", (
            f"a MOCHA product must draw the mocha-tagged member (Lucas), not "
            f"rotate the whole team: {response!r}"
        )

    def test_ac_1806_a_product_with_no_brand_carries_none_and_rotates_the_whole_team(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        calls = self._run_incoming_miss_then_yes(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            phone="+60900000011",
            product_code="ZZTSC-NOBRAND",
            brand_code=None,
        )

        body = calls[0]["body"]
        response = calls[0]["response"]
        assert body["brand_code"] is None, body
        # No brand to narrow by - the whole team (both Lucas and Jereen) is
        # eligible, exactly as it was before this fix ever carried a brand at all.
        assert response.get("assignee_name") in ("ZZT Lucas", "ZZT Jereen"), response

    def test_ac_1806_a_picked_members_own_brand_wins_over_the_carried_one(self) -> None:
        # Pure test on `escalation_context` itself: the picked-member row's OWN
        # brand must keep outranking a generic `carried_brand` (round 4's own new
        # rung sits AFTER the roster arms, never before them).
        from app.services.chatbot.lanes.escalation import escalation_context

        ctx = {
            "parse": {
                "output": {
                    "routing": {"suggested_team": "purchasing"},
                    "escalation": {
                        "preferred_assignee_id": "zzt-member-1",
                        # A carried brand that would say "mocha" if the picked
                        # member's own row did not outrank it.
                        "carried_brand": "mocha",
                    },
                }
            },
            "session": {
                "session_vars": {
                    "variables": {
                        "last_result_set": [
                            {
                                "uuid": "zzt-member-1",
                                "brand_code": "sorento",
                                "company_id": "zzt-co-1",
                                "company_name": "ZZT Co",
                            }
                        ],
                    }
                }
            },
        }

        result = escalation_context({}, ctx=ctx)

        assert result["routing_source"] == "picked_member", result
        assert result["brand_code"] == "sorento", (
            f"the picked member's OWN brand must win over the carried one: {result!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1807/AC-1808 (round 5, evidence: does the brand carry hold for a PHOTO/
# attachment escalation, not just incoming?). Journey: `product_attachment` domain
# (photo request) for a product with NO attachment -> the rich miss
# (`test_rearch_r5_production_decides.py::TestFetchedEmptyIsAMiss::
# test_attachment_fetch_with_zero_rows_is_the_rich_miss` shows the shape) -> its
# escalate offer (team `marketing_product` off the domain row, agent
# `general_enquiries` off the parser's own domain map) -> "yes". Same real-draw
# pattern as AC-1804/AC-1805 - `_seed_escalation_team`, `_capture_real_next_
# assignee` - over a Marketing - Product team (prod: Kia Yee = mocha, Tay Zhi Yang
# = every other brand, Charissa untagged; reproduced here with the same two-member
# shape AC-1804/1805 use).
# --------------------------------------------------------------------------- #


class TestAC1807And1808PhotoMissEscalationCarriesTheBrand:
    def _run_photo_miss_then_yes(
        self,
        session_factory,
        monkeypatch,
        stub_parser,
        stub_access,
        *,
        phone: str,
        product_code: str,
        brand_code: str,
    ) -> list[dict[str, Any]]:
        from tests.chatbot.test_product_attachment_picker_stamp import _seed_attachment_type

        _seed_contact(session_factory, phone=phone)
        _seed_product_with_brand(session_factory, code=product_code, brand_code=brand_code)
        _seed_attachment_type(session_factory, "Product Photos")
        _seed_escalation_team(
            session_factory,
            agent_code="general_enquiries",
            team_code="marketing_product",
            team_label="Marketing - Product",
            members=[("Kia Yee", ["mocha"]), ("Tay Zhi Yang", ["sorento", "cabana"])],
        )
        _stub_incoming_probe_empty(monkeypatch)
        stub_parser(
            verdict(
                domain_hint="product_attachment",
                intent_hint="check_product_attachment",
                entities=[
                    entity(product_code, hint="product", confident=True),
                    entity("product photos", hint="attachment_type", canonical_code="photo", confident=True),
                ],
                # The parser's own domain map for `product_attachment` (test_rearch_r5's
                # own module docstring, quoted in this file's class comment): team off
                # the domain row, agent `general_enquiries`.
                routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
            )
        )
        stub_access()
        turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert turn1.branch_kind == "business_query", turn1.branch_kind

        stub_parser(_yes_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        result = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(calls) == 1, calls
        return calls

    def test_ac_1807_a_mocha_product_photo_miss_escalates_to_the_mocha_tagged_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        calls = self._run_photo_miss_then_yes(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            phone="+60900000017",
            product_code="ZZTPH-MCH",
            brand_code="mocha",
        )

        body = calls[0]["body"]
        response = calls[0]["response"]
        assert body["brand_code"] == "mocha", body
        assert body["agent_code"] == "general_enquiries", body
        assert body["team_code"] == "marketing_product", body
        assert response.get("assignee_name") == "ZZT Kia Yee", (
            f"a MOCHA product's photo miss must draw the mocha-tagged member (Kia "
            f"Yee), not rotate the whole team: {response!r}"
        )

    def test_ac_1808_a_sorento_product_photo_miss_escalates_to_the_sorento_tagged_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        calls = self._run_photo_miss_then_yes(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            phone="+60900000020",
            product_code="ZZTPH-SRT",
            brand_code="sorento",
        )

        body = calls[0]["body"]
        response = calls[0]["response"]
        assert body["brand_code"] == "sorento", body
        assert body["agent_code"] == "general_enquiries", body
        assert body["team_code"] == "marketing_product", body
        assert response.get("assignee_name") == "ZZT Tay Zhi Yang", (
            f"a SORENTO product's photo miss must draw the sorento-tagged member "
            f"(Tay Zhi Yang), not rotate the whole team: {response!r}"
        )
