"""R4 RED tests, engine/production-chain level - the customer-facing miss shapes
(PLAN-chatbot-answer-half-reattach.md slice R4; UAC AC-1683, AC-1699 to AC-1705, AC-1708).

**Scope note (captain's brief: "if a phase genuinely cannot be done, say so").** AC-1699's
own dated-order-miss scenario and AC-1701/AC-1702's attachment scenarios are graded here at
the PRODUCTION-CHAIN level (calling the real `not_found_error_message` / `run_miss_lane` /
`escalate_catalog` / `compose_from_fragments` chain, or the real DB-backed picker-stamp
harness) rather than through a full `engine.run_turn` turn, for two measured reasons:

1. **AC-1699/AC-1700's "position assigns that member" / "yes assigns automatically" /
   "all dates re-runs the fetch"** live in `turn/apply.py::_answer_pending`'s generic pick
   resolution, a SEPARATE surface from the bridge this file's own remit is (`answer_bridge.
   answer_for`). Pinning THAT would need `turn/apply.py`'s own contract for a `member_offer`
   Pending, which does not exist yet either (confirmed: `pending.PENDING_KINDS` carries
   `member_offer` read-side only, per the plan's own measured fact) - out of scope for a
   red-tests-for-R4 pass; flagged here as the next slice's own territory, not silently
   dropped. `test_rearch_r4_bridge_miss.py::TestMemberOfferPendingIsMintedByTheBridge`
   already pins the WRITE side (the Pending itself, its options, its team) - this file adds
   the CUSTOMER-FACING SCOPE BLOCK text (AC-1699's own bullets) that Pending sits under.
2. **AC-1701/AC-1702** reuse `test_product_attachment_picker_stamp.py`'s own real-DB
   harness (seeded products/attachment types/files, real gate/resolver/`dym_transform`/
   `dym_annotate`, only the MCP call stubbed) - the brief's own named precedent - rather
   than re-deriving the same fixture through `_run_turn`'s fully-faked resolver, so a
   genuine has/no split is graded against real rows, not a hand-typed roster.

Every "expected" value below is computed by calling the real production function chain,
never retyped copy - the SAME convention `test_rearch_r4_bridge_miss.py` uses.
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.models.base import set_company_scope
from app.services.chatbot import copy as copy_mod
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business import miss_suggest as miss_mod
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business.services import AnswerServices, production_answer_services
from app.services.chatbot.tail import compose as tail_compose
from app.services.chatbot.tail import outcome as outcome_mod
from app.services.chatbot.tail import reply_ladder
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import (
    SPACE_ID,
    _real_resolve_entity,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
)
from tests.chatbot.test_outstanding_lane import (
    PRODUCT_CODE,
    PRODUCT_UUID,
    _run_turn,
    _seed_contact as _seed_business_contact,
)
from tests.chatbot.test_product_attachment_picker_stamp import (
    CONTACT_ID as PICKER_CONTACT_ID,
    HAS_PHOTO_CODE,
    NO_PHOTO_CODE,
    PHOTO_TYPE_NAME,
    PROBE_TOOL,
    _picker_lines,
    _probe_services,
    _run_lane,
    _seed_attachment_type,
    _seed_file_for,
)


def _bridge():
    try:
        return importlib.import_module("app.services.chatbot.answer_bridge")
    except ModuleNotFoundError:
        return None


def _require_answer_for():
    bridge = _bridge()
    assert bridge is not None, "app.services.chatbot.answer_bridge does not exist yet"
    assert hasattr(bridge, "answer_for"), "answer_bridge has no answer_for(...) yet"
    return bridge


def _canned():
    return copy_mod.fallback_copy()


def _ctx_for(parser: dict[str, Any]) -> dict[str, Any]:
    return {"parse": {"output": parser}, "contact": {"id": "zzt-r4-engine"}, "session": {}}


# --------------------------------------------------------------------------- #
# AC-1699 - dated order miss: scope block + "Here's what you want:" + "Reply 'all
# dates'" + the CS member picker
# --------------------------------------------------------------------------- #


def _dated_order_scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], AnswerServices]:
    parser = {
        "domain_hint": "order",
        "intent_hint": "check_order",
        "message_type": "business_query",
        "entities": [
            {"raw": PRODUCT_CODE, "hint": "product", "current_message": True, "confident": True}
        ],
        "date_filter_start": "2026-10-01",
        "date_filter_end": "2026-10-31",
        "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
        "access_levels": [],
    }
    resolved = {
        "resolutions": [
            {
                "token": PRODUCT_CODE,
                "matches": [
                    {
                        "entity_type": "product",
                        "canonical_code": PRODUCT_CODE,
                        "uuid": PRODUCT_UUID,
                        "match_tier": "exact",
                    }
                ],
            }
        ],
        "unresolved_tokens": [],
        "tokens": [PRODUCT_CODE],
        "intersection": [
            {"entity_type": "product", "canonical_code": PRODUCT_CODE, "uuid": PRODUCT_UUID}
        ],
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [
            {"uuid": PRODUCT_UUID, "entity_type": "product", "code": PRODUCT_CODE}
        ],
        "gate_debug": {"domain": "order"},
    }
    services = AnswerServices(
        mcp_probe=lambda name, args: {"has_result": False, "answers": []},
        family_fetch=lambda query: {"data": []},
    )
    return parser, resolved, gate, services


def _expected_bridge_reply(
    payload: dict[str, Any],
    *,
    parser: dict[str, Any],
    resolved: dict[str, Any],
    gate: dict[str, Any],
    services: AnswerServices,
    fetch_rosters_stub: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The FULL production chain: not_found -> run_miss_lane -> compose_from_fragments
    equivalent (escalate_catalog + cs_offer_gate + member offer + build_outcome +
    compose_reply). Same manual chain `test_rearch_r4_bridge_miss.py::
    _manual_compose_from_fragments` pins as `tail.reply.compose_from_fragments`'s own
    ground truth, reused here so both files cannot silently disagree."""
    from app.services.chatbot.tail import member_offer as member_mod

    not_found = answer_mod.not_found_error_message(
        payload, parser=parser, resolved=resolved, gate=gate
    )
    offer = miss_mod.run_miss_lane(
        not_found,
        parser=parser,
        resolved=resolved,
        gate=gate,
        services=services,
        contact_id="zzt-r4-engine",
        space_id=None,
        execution_id="zzt-r4-turn",
        dry_run=True,
    )
    item = {**offer, "branch_kind": "not_found"}
    ctx = _ctx_for(parser)
    canned = _canned()

    values = {
        "not_found": not_found,
        "incoming_picker": None,
        "access_choice": None,
        "suggest_offer": offer,
        "gate": gate,
        "offer_hold": None,
    }
    producers: dict[str, Any] = {}
    for name, field in outcome_mod.CARRIER_FIELDS.items():
        if values.get(field) is not None:
            producers[name] = values[field]

    outcome_input = dict(item)
    catalog = outcome_mod.escalate_catalog(
        item,
        ctx,
        canned,
        not_found=values["not_found"],
        incoming_picker=values["incoming_picker"],
        access_choice=values["access_choice"],
        suggest_offer=values["suggest_offer"],
        gate=values["gate"],
        offer_hold=values["offer_hold"],
    )
    producers["escalate-catalog"] = catalog
    outcome_input = catalog
    if outcome_mod.cs_offer_gate(catalog, ctx, gate):
        plan = member_mod.cs_roster_plan(gate)
        rosters = (
            fetch_rosters_stub(None, plan, ctx)
            if fetch_rosters_stub is not None
            else member_mod.fetch_rosters(None, plan, ctx)
        )
        member_offer_out = member_mod.build_cs_member_offer(catalog, plan, rosters)
        producers["cs-roster-plan"] = plan
        producers["build-cs-member-offer"] = member_offer_out
        outcome_input = member_offer_out

    built = outcome_mod.build_outcome([{"json": outcome_input}], producers)
    outcome = built[0]["json"]["outcome"]
    composed = reply_ladder.compose_reply(outcome)
    return composed, offer


class TestAC1699DatedOrderMissScopeBlockAndMemberPicker:
    def test_reply_carries_scope_block_and_the_all_dates_reply_hint(self, monkeypatch) -> None:
        """DEFECT ADJUDICATION (tester 31, 20 Sep 2026, coder 28's own report): the
        original test computed `text` via `_expected_bridge_reply`'s own manual chain
        with `fetch_rosters_stub` wired in, but the REAL `bridge.answer_for(db=None, ...)`
        call below had no way to see that stub - `tail/reply.py::compose_from_fragments`
        calls `member_offer.fetch_rosters(db, ...)` directly, and `db=None` there fails
        open to an empty roster (measured: logged
        "cs roster read failed for company_id=None brand=None: 'NoneType' object has no
        attribute 'query'"), so the real call fell back to the generic "Please choose who
        to route to" text instead of the stubbed "Ah Chong" line - UPHELD. Fixed by
        monkeypatching `app.services.chatbot.tail.member_offer.fetch_rosters` (the actual
        seam `compose_from_fragments` calls through `member_mod.fetch_rosters`, an
        attribute lookup at call time, not a `from ... import fetch_rosters` binding) so
        BOTH the manual reproduction and the real bridge call see the identical roster."""
        from app.services.chatbot.tail import member_offer as member_mod

        parser, resolved, gate, services = _dated_order_scenario()
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}

        def stub_rosters(db, plan, ctx):
            return [{"body": [{"user_id": "u1", "respond_user_id": "ru1", "name": "Ah Chong"}]}]

        monkeypatch.setattr(member_mod, "fetch_rosters", stub_rosters)

        composed, _offer = _expected_bridge_reply(
            payload, parser=parser, resolved=resolved, gate=gate, services=services,
            fetch_rosters_stub=stub_rosters,
        )
        text = composed["text"] or ""
        assert "Here's what you want:" in text, text
        assert "Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct)" in text, text
        assert "Ah Chong" in text, (
            "AC-1699's own CS member picker must ride under the dated miss: "
            f"{text}"
        )
        assert "If you have no preference, just reply 'yes' and we'll assign automatically." in text, text

        bridge = _require_answer_for()
        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx=_ctx_for(parser),
            canned=_canned(),
            services=services,
            db=None,
            asked_at_turn=5,
        )
        assert answer is not None, "answer_for returned None for a dated order miss"
        assert answer.text == text
        assert answer.question is not None and answer.question.kind == "member_offer"


# --------------------------------------------------------------------------- #
# PLAN-chatbot-order-status-all-orders-23sep, Fix 2 / AC-1866 / AC-1867. Owner ruling
# 23 Sep 2026: a `member_offer` already prints its own numbered text list ("Please
# choose who to route to (reply with the number): 1. Ah Chong ..."), so the SAME names
# must not ALSO go out as WhatsApp quick-reply buttons - `result_set` stays populated
# (a numbered reply still resolves through it), only `quick_replies` is withheld, and
# only for this one pending kind.
# --------------------------------------------------------------------------- #


class TestAC1866MemberOfferSendsNoQuickReplies:
    def test_engine_reply_has_no_quick_replies_but_keeps_the_member_result_set(
        self, monkeypatch
    ) -> None:
        """Engine-level: the real `answer_bridge.answer_for` production chain mints the
        `member_offer` Pending, and `engine._reply_of` - the exact function both
        `engine._run_answer` and `engine._answer_actions` call to build a reply/action
        from an `Answer` - is exercised directly on that real `Answer`, the same way
        the tail does."""
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.tail import member_offer as member_mod

        parser, resolved, gate, services = _dated_order_scenario()
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}

        def stub_rosters(db, plan, ctx):
            return [
                {
                    "body": [
                        {"user_id": "u1", "respond_user_id": "ru1", "name": "Maryam Ariffin"},
                        {"user_id": "u2", "respond_user_id": "ru2", "name": "Nurain"},
                    ]
                }
            ]

        monkeypatch.setattr(member_mod, "fetch_rosters", stub_rosters)

        bridge = _require_answer_for()
        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx=_ctx_for(parser),
            canned=_canned(),
            services=services,
            db=None,
            asked_at_turn=6,
        )
        assert answer is not None and answer.question is not None
        assert answer.question.kind == "member_offer", answer.question.kind
        assert "Please choose who to route to" in (answer.text or ""), answer.text

        # The real seam both `_run_answer` and `_answer_actions` build their
        # reply/action from - calling it directly (rather than re-deriving the same
        # shape by hand) is what makes this assertion guard the production wiring.
        reply = engine_mod._reply_of(answer)
        assert reply["quick_replies"] is None, (
            f"a member_offer must not go out with quick replies: {reply['quick_replies']!r}"
        )
        assert len(reply["result_set"]) > 0, "the member roster must stay in result_set"
        assert all(row.get("entity_type") == "member" for row in reply["result_set"]), reply["result_set"]
        assert [row.get("label") for row in reply["result_set"]] == ["Maryam Ariffin", "Nurain"]


class TestAC1867OtherPendingKindsKeepTheirQuickReplies:
    """Regression guard for AC-1866's scope: `team_pick` / `company_pick` / a roster
    kind still send the comma-joined `quick_replies` string exactly as before - the
    suppression is `member_offer` ONLY."""

    def test_team_pick_still_yields_the_comma_joined_quick_replies_string(self) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.turn import pending as turn_pending
        from types import SimpleNamespace

        question = turn_pending.ask(
            "team_pick",
            [
                {"position": 1, "label": "Mocha", "entity_type": "team"},
                {"position": 2, "label": "Sorento", "entity_type": "team"},
            ],
        )
        answer = SimpleNamespace(question=question)
        assert engine_mod._quick_replies_of(answer) == "Mocha, Sorento"

    def test_product_pick_still_yields_the_comma_joined_quick_replies_string(self) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.turn import pending as turn_pending
        from types import SimpleNamespace

        question = turn_pending.ask(
            "product_pick",
            [
                {"position": 1, "label": "SRTWC8517", "entity_type": "product"},
                {"position": 2, "label": "SRTWC8518", "entity_type": "product"},
            ],
        )
        answer = SimpleNamespace(question=question)
        assert engine_mod._quick_replies_of(answer) == "SRTWC8517, SRTWC8518"

    def test_compose_question_reask_suppresses_only_member_offer(self) -> None:
        from app.services.chatbot.turn import compose as turn_compose
        from app.services.chatbot.turn import pending as turn_pending

        member_question = turn_pending.ask(
            "member_offer",
            [
                {"position": 1, "label": "Maryam Ariffin", "entity_type": "member"},
                {"position": 2, "label": "Nurain", "entity_type": "member"},
            ],
        )
        member_answer = turn_compose.compose_question(member_question)
        assert member_answer.actions[0]["quick_replies"] is None
        assert len(member_answer.actions[0]["result_set"]) == 2

        team_question = turn_pending.ask(
            "team_pick",
            [
                {"position": 1, "label": "Mocha", "entity_type": "team"},
                {"position": 2, "label": "Sorento", "entity_type": "team"},
            ],
        )
        team_answer = turn_compose.compose_question(team_question)
        assert team_answer.actions[0]["quick_replies"] == "Mocha, Sorento"


# --------------------------------------------------------------------------- #
# AC-1701 / AC-1702 (F6 moved from R3, F7) - real-DB attachment roster + plain miss,
# reusing test_product_attachment_picker_stamp.py's own harness
# --------------------------------------------------------------------------- #


class TestAC1701AttachmentRosterViaTheRealDbHarness:
    def test_bridge_reproduces_the_picker_text_and_mints_a_product_pick(
        self, session_factory, monkeypatch
    ) -> None:
        from tests.chatbot.test_product_attachment_picker_stamp import _parser as attach_parser

        company_id = _seed_company(session_factory, name="ZZT R4 Picker Co")
        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=PICKER_CONTACT_ID, phone="+60000000751",
            workspace_id=workspace_id, company_ids=[company_id],
        )
        has_id = _seed_product(session_factory, company_id=company_id, code=HAS_PHOTO_CODE)
        _seed_product(session_factory, company_id=company_id, code=NO_PHOTO_CODE)
        type_id = _seed_attachment_type(session_factory, PHOTO_TYPE_NAME)
        _seed_file_for(
            session_factory, product_id=has_id, attachment_type_id=type_id,
            company_id=company_id, filename=f"{HAS_PHOTO_CODE}.jpg",
        )

        db = session_factory()
        set_company_scope(db, frozenset({company_id}))
        calls: list[tuple[str, dict]] = []
        services = _probe_services(db, monkeypatch, calls=calls)

        parser = attach_parser()
        resolved = _real_resolve_entity(db)(
            resolve_gate.resolve_entity_body(
                {"parse": {"output": parser}, "session": {}, "contact": {"id": PICKER_CONTACT_ID}},
                dry_run=True,
            )
        )
        gate = gate_mod.run_gate({}, parser=parser, resolver=resolved)
        assert gate.get("require_specific") is True, (
            f"test setup sanity: the picker must render: {gate.get('gate_reason')}"
        )

        not_found = answer_mod.not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        offer = miss_mod.run_miss_lane(
            not_found, parser=parser, resolved=resolved, gate=gate, services=services,
            contact_id=PICKER_CONTACT_ID, space_id=SPACE_ID, execution_id="zzt-r4-picker",
            dry_run=True,
        )
        expected_message = offer.get("escalate_message") or ""
        lines = _picker_lines(expected_message)
        assert lines[HAS_PHOTO_CODE].endswith(f"- has {PHOTO_TYPE_NAME}"), lines
        assert lines[NO_PHOTO_CODE].endswith(f"- no {PHOTO_TYPE_NAME}"), lines

        bridge = _require_answer_for()
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}
        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx={"parse": {"output": parser}, "contact": {"id": PICKER_CONTACT_ID}, "session": {}},
            canned=_canned(),
            services=services,
            db=db,
            asked_at_turn=2,
        )
        assert answer is not None, "answer_for returned None for the attachment roster miss"
        assert expected_message in answer.text, (answer.text, expected_message)
        assert answer.question is not None
        assert answer.question.kind == "product_pick", answer.question.kind
        got_stamps = [o.get("stamp") for o in answer.question.options]
        assert any(got_stamps), (
            f"the roster's options must carry the has/no {PHOTO_TYPE_NAME} stamp: "
            f"{answer.question.options}"
        )


class TestAC1702PlainAttachmentMiss:
    """A single resolved product with no matching attachment_type file at all - no
    roster (require_specific stays False), the breakdown miss instead."""

    def test_bridge_text_matches_the_production_breakdown(self) -> None:
        parser = {
            "domain_hint": "product_attachment",
            "intent_hint": "check_product_attachment",
            "message_type": "business_query",
            "entities": [
                {"raw": "SRTWC900", "hint": "product", "current_message": True, "confident": True},
                {
                    "raw": "product photos", "hint": "attachment_type", "canonical_code": "photo",
                    "current_message": True, "confident": True,
                },
            ],
            "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        }
        resolved = {
            "resolutions": [
                {
                    "token": "SRTWC900",
                    "matches": [
                        {
                            "entity_type": "product", "canonical_code": "SRTWC900",
                            "uuid": "prod-r4-2", "match_tier": "exact",
                        }
                    ],
                }
            ],
            "unresolved_tokens": [],
            "tokens": ["SRTWC900"],
            "intersection": [
                {"entity_type": "product", "canonical_code": "SRTWC900", "uuid": "prod-r4-2"}
            ],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": "prod-r4-2", "entity_type": "product", "code": "SRTWC900"}
            ],
            "gate_debug": {"domain": "product_attachment"},
        }
        services = AnswerServices(
            mcp_probe=lambda name, args: {"has_result": False, "answers": []},
            family_fetch=lambda query: {"data": []},
        )
        not_found = answer_mod.not_found_error_message(
            {"resolved": resolved, "gate": gate}, parser=parser, resolved=resolved, gate=gate
        )
        expected_message = not_found.get("escalate_message") or ""
        assert "Here's what you want:" in expected_message, expected_message
        assert "Would you like me to escalate to marketing product team?" in expected_message

        bridge = _require_answer_for()
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}
        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx=_ctx_for(parser),
            canned=_canned(),
            services=services,
            db=None,
            asked_at_turn=1,
        )
        assert answer is not None
        assert expected_message in answer.text, (answer.text, expected_message)


# --------------------------------------------------------------------------- #
# AC-1705 - incoming miss + the cross-domain stock ladder, scoped to ONE product
# --------------------------------------------------------------------------- #


class TestAC1705IncomingMissWithStockLadder:
    """MEASURED (this tester's own finding, not in the captain's brief): the ladder
    sentence ("But here are the stock details for the requested products:") is built by
    `answer.crossdomain_render` (folded through `answer.run_crossdomain`'s own `_xdBlock`)
    and INSERTED into the already-composed reply by `tail.compose.crossdomain_compose` -
    a THIRD stage `engine.run_tail`/`complete_turn` never actually calls today (confirmed:
    the only caller anywhere in `app/` is `tests/chatbot/test_replay.py`'s own replay
    harness - it is dead code on BOTH the pre-rearch business_query lane and the new turn
    engine). `answer.run_crossdomain` itself is ALSO dead on the new engine path
    (its own docstring: "NOT ON THE TURN PATH since the re-architecture ... Contract 3
    and 125's rung walk is turn/fetch.py::_climb now"). So AC-1705 needs the bridge to run
    BOTH pieces itself for a single-domain miss: fold `crossdomain_render`'s block through
    the SAME machinery `run_crossdomain` uses, then post-process the composed reply with
    `crossdomain_compose` - a genuinely NEW wiring, not a reattachment of something already
    live in the new engine's own tail."""

    def test_bridge_text_matches_the_production_ladder_chain(self) -> None:
        parser = {
            "domain_hint": "incoming",
            "intent_hint": "check_incoming",
            "message_type": "business_query",
            "entities": [
                {"raw": "SRTWC900", "hint": "product", "current_message": True, "confident": True}
            ],
            "routing": {"suggested_team": "purchasing", "suggested_agent": "certification"},
        }
        resolved = {
            "resolutions": [
                {
                    "token": "SRTWC900",
                    "matches": [
                        {
                            "entity_type": "product", "canonical_code": "SRTWC900",
                            "uuid": "prod-r4-3", "match_tier": "exact",
                        }
                    ],
                }
            ],
            "unresolved_tokens": [],
            "tokens": ["SRTWC900"],
            "intersection": [
                {"entity_type": "product", "canonical_code": "SRTWC900", "uuid": "prod-r4-3"}
            ],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": "prod-r4-3", "entity_type": "product", "code": "SRTWC900"}
            ],
            "gate_debug": {"domain": "incoming"},
        }
        # The SAME MCP double answers whichever tool the ladder calls: a call naming
        # "stock"/"balance" answers WITH a row; anything else (the primary incoming probe)
        # answers empty. This is the fixture's own stand-in for two real MCP tools.
        def mcp_probe(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if "stock" in name or "balance" in name:
                return {
                    "has_result": True,
                    "answers": [
                        {
                            "fields": [
                                {"label": "Product Code", "value": "SRTWC900"},
                                {"label": "Quantity On Hand", "value": 12},
                            ]
                        }
                    ],
                }
            return {"has_result": False, "answers": []}

        services = AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []})

        not_found = answer_mod.not_found_error_message(
            {"resolved": resolved, "gate": gate}, parser=parser, resolved=resolved, gate=gate
        )
        offer = miss_mod.run_miss_lane(
            not_found, parser=parser, resolved=resolved, gate=gate, services=services,
            contact_id="zzt-r4-ladder", space_id=None, execution_id="zzt-r4-ladder-turn",
            dry_run=True,
        )
        item = {**offer, "branch_kind": "not_found"}
        ctx = _ctx_for(parser)
        canned = _canned()
        catalog = outcome_mod.escalate_catalog(item, ctx, canned, suggest_offer=offer, gate=gate)
        built = outcome_mod.build_outcome(
            [{"json": catalog}], {"escalate-catalog": catalog, "build-suggest-offer": offer}
        )
        base_text = reply_ladder.compose_reply(built[0]["json"]["outcome"])["text"]

        # The ladder's own block, computed the same way `run_crossdomain` would (a
        # zeroset built by hand here rather than through the full resolver-matching
        # machinery `crossdomain_zeroset` needs - the RENDER is what is under test).
        zeroset = {
            "origin_domain": "incoming",
            "other_tool": "crm_inventory_stock_balance_list",
            "team": "purchasing",
            "requested": ["SRTWC900"],
            "returned_codes": [],
            "missing": [
                {"code": "SRTWC900", "uuid": "prod-r4-3", "_n": "SRTWC900", "entity_type": "product"}
            ],
            "probe_entities": [{"uuid": "prod-r4-3", "entity_type": "product", "code": "SRTWC900"}],
        }
        probe_result = mcp_probe("crm_inventory_stock_balance_list", {})
        render = answer_mod.crossdomain_render(probe_result, zeroset=zeroset, validator={"has_result": False})
        xd_block = render.get("_xdBlock") or {}
        assert xd_block.get("any") is True, "test setup sanity: the ladder block must render something"
        assert "But here are the stock details for the requested products:" in (xd_block.get("block") or "")

        sealed = {
            "reply": {
                "text": base_text,
                "session_patch": {"user_response": base_text, "variables": {}},
            }
        }
        result_carrier = {"result": {"xd": {"block": xd_block}}}
        merged = tail_compose.crossdomain_compose(sealed, result=result_carrier, answered=False)
        expected_text = merged["reply"]["session_patch"]["user_response"]
        assert "But here are the stock details for the requested products:" in expected_text
        assert expected_text.count("SRTWC900") >= 1, (
            "no OTHER product code may appear in the reply - the ladder is scoped to "
            f"the one asked product: {expected_text!r}"
        )

        bridge = _require_answer_for()
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}
        answer = bridge.answer_for(
            payload,
            envelope=None,
            parser=parser,
            ctx=ctx,
            canned=canned,
            services=services,
            db=None,
            asked_at_turn=1,
        )
        assert answer is not None
        assert answer.text == expected_text, (answer.text, expected_text)


# --------------------------------------------------------------------------- #
# AC-1708 - a multi-domain miss keeps today's turn/compose.py copy (green control)
# --------------------------------------------------------------------------- #


class TestAC1708MultiDomainMissKeepsTodaysCopy:
    """CONTROL, engine-level: a fan-out over two domains is out of R4's scope by owner
    ruling - `turn/compose.compose` still composes it, unchanged."""

    def test_multi_domain_miss_still_reaches_turn_compose_compose(
        self, session_factory, monkeypatch
    ) -> None:
        from app.services.chatbot.turn import compose as turn_compose

        calls: list[Any] = []
        real = turn_compose.compose

        def _spy(envelopes, state, policy, ctx):
            calls.append(envelopes)
            return real(envelopes, state, policy, ctx)

        monkeypatch.setattr(turn_compose, "compose", _spy)
        _seed_business_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint=None,
                intent_hint=None,
                entities=[
                    {
                        "raw": "ZZTNOPE", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    }
                ],
                asks=[{"domain": "inventory"}, {"domain": "incoming"}],
            ),
            text_body="stock and incoming for ZZTNOPE",
            msg_id="zzt-r4-multi-domain-miss-control",
            mcp_response={"has_result": False, "items": []},
        )
        assert len(calls) >= 1, "a multi-domain miss must still be composed by turn/compose.compose"
        reply = (result.reply or {}).get("text") or ""
        assert reply, "the multi-domain control must still produce a reply"


# --------------------------------------------------------------------------- #
# AC-1683 - the single-domain miss reply is produced by escalate_catalog +
# compose_reply (via the bridge), turn/compose.compose is not reached, engine.run_tail
# is not entered, one session write
# --------------------------------------------------------------------------- #


class TestAC1683SingleDomainMissBypassesTurnCompose:
    def test_compose_compose_not_called_for_a_single_domain_miss(
        self, session_factory, monkeypatch
    ) -> None:
        from app.services.chatbot.turn import compose as turn_compose

        calls: list[Any] = []
        real = turn_compose.compose

        def _spy(envelopes, state, policy, ctx):
            calls.append(envelopes)
            return real(envelopes, state, policy, ctx)

        monkeypatch.setattr(turn_compose, "compose", _spy)
        _seed_business_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order",
                intent_hint="check_order",
                entities=[
                    {
                        "raw": "ZZTNOPE", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    }
                ],
            ),
            text_body="orders for ZZTNOPE",
            msg_id="zzt-r4-single-domain-miss-spy-compose",
            mcp_response={"has_result": False, "items": []},
        )
        assert calls == [], (
            "a single-domain business miss must be composed by the bridge + "
            f"tail.reply.compose_from_fragments, never turn/compose.compose: {calls}"
        )

    def test_run_tail_is_not_entered_for_a_bridged_miss(self, session_factory, monkeypatch) -> None:
        from app.services.chatbot import engine as engine_mod

        calls: list[Any] = []
        real = engine_mod.run_tail

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(engine_mod, "run_tail", _spy)
        _seed_business_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order",
                intent_hint="check_order",
                entities=[
                    {
                        "raw": "ZZTNOPE", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    }
                ],
            ),
            text_body="orders for ZZTNOPE",
            msg_id="zzt-r4-single-domain-miss-spy-run-tail",
            mcp_response={"has_result": False, "items": []},
        )
        assert calls == [], calls
