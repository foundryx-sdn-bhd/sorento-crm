"""Hand pass 11 fix round, FINAL round - reviewer/security RE-CHECK findings, PR #952
(`.claude/handoffs/rearch-phase3-reviewer-hp11-recheck.md`,
`.claude/handoffs/rearch-phase3-security-hp11-recheck.md`). RED, test-first, lane head
`ff9b1b3a4`.

BLOCKER 1 (reviewer): `escalation_roster_plan` (`turn_runtime.py:351-381`) mints
`{"company_id": None, "company_name": company, "brand_code": None}` per option - the id
is ALWAYS None, so `_next_assignee_body`/`_sla_body` (`lanes/escalation.py:1090-1136`)
route a silent-company HIT offer's "yes" with NO company id at all, even though the
offer named a REAL company by name. `lookup_companies` (`company_scope.py:437-439`)
already carries `{"id", "name"}` per entry - the id is sitting right there.

BLOCKER 2 (owner ruling, "we clarify the company with the user when it is not clear"):
a MISS in both companies mints a `team_pick` with one option per searched company
(`lanes/business/answer.py:2934-2963`), `expects="yes_no"` - positions are INERT on a
yes/no offer (`turn/decide.py`), so a bare "yes" can never NAME a company. Measured
directly this session: `escalation_roster_plan` injects `routing_roster_plan` on ANY
accepted escalation offer (not gated on a clarify), so `escalation_context`'s `same_team`
arm reads a 2-row plan and reports `routing_source: multi_company_unpicked` - CORRECT so
far - but `_clarify_gate` (`escalation.py:427-440`) additionally requires
`prev.selection_context == "member_offer"`, a key nothing in the re-arch engine ever
writes (grep-confirmed: the only writer is the OLD n8n `member_offer` flow). So the gate
never fires, `run()` falls through to the dry-run preview branch, and a bare "yes" builds
a full `assign_conversation` PREVIEW action off a company nobody named.

SHOULD-FIX 3 (security, `_searched_companies` over-claims): `answer_bridge.py:865-897`
walks `gate.compatible_entities` with no `_NO_TOOL_ID` ({"brand", "category"}) skip,
while the "checked in X and Y" sentence it mirrors (`answer.py:2949`) does skip them -
a brand match can cross `_MIN_ROSTER_OPTIONS` and offer a company the turn's own
sentence never named as searched. Pure-function test, the same way the security pass
itself measured it (`rearch-phase3-security-hp11-recheck.md` finding 2).

SHOULD-FIX 4 (security, both HIT bridges collide): `engine.py:2140-2170` calls
`apply_crossdomain_hit` then unconditionally `apply_silent_company_offer` - the second
call's `replace(answer, text=..., question=question)` (`answer_bridge.py`, no `if
question is not None else answer.question` guard, unlike the sibling
`apply_crossdomain_hit:246`) clobbers the ladder's own `team_pick` (`warehouse`) with the
silent-company offer's own `team_pick` (`customer_service`) whenever BOTH are eligible on
one turn: a company-A zero-stock HIT (ladder fires) with company B silent.

SHOULD-FIX 5: no red pins a decline over either NEW pending this hand pass minted
(the ladder's own HIT offer, the silent-company HIT offer) - "no" must reach
`escalation_declined`, clear the pending, and make no assignment.

NIT N-b: `escalation_roster_plan` (`turn_runtime.py:373-381`) has no Mapping guard on an
option's own `payload` - `from_wire` (`turn/pending.py:186-201`) filters `options` to
dicts but does NOT sanitise each option's OWN `payload` field, so a persisted option
whose `payload` is not a dict (`"oops"`, a string) makes `(opt.get("payload") or
{}).get("company")` raise `AttributeError` on a plain string, uncaught in
`engine.py:1461-1478` (no try/except around the `escalation_roster_plan` call) - the
turn closes `status="failed"` (engine.py's own broad `except Exception` at the
per-turn level) instead of completing.

Harness: the REAL resolver against a two-company seeded chain
(`test_engine_company_scope.py`'s own helpers, reused via
`test_rearch_r11_multi_company.py`), the fetch tools stubbed with the envelope shape
`company_scope.stamp_lookup_companies` produces. Postgres only, every row seeded fresh.
No live parser, no :8766, no API key.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.access import RespondContact
from app.models.company import RespondContactCompany
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes import escalation as escalation_mod
from app.services.chatbot.lanes.business.services import FetchServices
from app.services.chatbot.lanes.escalation import (
    _next_assignee_body,
    _sla_body,
    escalation_context,
)
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    _parser_output,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_engine_company_scope import (
    _scope_envelope,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
    _set_completed_lanes,
    _wire_answer_services,
    _wire_real_resolve_entity,
)
from tests.chatbot.test_rearch_r4_answering_a_miss import _fake_escalation_lane
from tests.chatbot.test_rearch_r11_multi_company import (
    CONTACT_ID as MULTICO_CONTACT_ID,
    MOCHA,
    PRODUCT_CODE,
    SORENTO,
    _open_question,
    _order_row,
    _order_verdict,
    _orders_envelope,
    _said,
    _two_company_chain,
    _wire,
)
from tests.chatbot.test_rearch_r11_zero_stock_ladder import EMPTY_PO, INCOMING_TOOL, PO_TOOL, STOCK_TOOL, _incoming_rows


def _open_question_for(session_factory: Any, contact_id: str) -> dict[str, Any]:
    from sqlalchemy import text as _sql_text

    db = session_factory()
    row = db.execute(
        _sql_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": contact_id},
    ).first()
    raw = row.session_vars if row is not None else {}
    sv = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return sv.get("open_question") or {}


def _focus_for(session_factory: Any, contact_id: str) -> dict[str, Any]:
    from sqlalchemy import text as _sql_text

    db = session_factory()
    row = db.execute(
        _sql_text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": contact_id},
    ).first()
    raw = row.session_vars if row is not None else {}
    sv = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return sv.get("focus") or {}


# --------------------------------------------------------------------------- #
# BLOCKER 1 - a silent-company HIT offer's "yes" must route by the REAL company id
# --------------------------------------------------------------------------- #


class TestBlocker1SilentCompanyOfferRoutesByRealCompanyId:
    def test_next_assignee_and_sla_body_carry_sorentos_real_company_id(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """`TestHitInOneCompanyNamesTheSilentOne`'s own scenario (Mocha hit, Sorento
        silent) - a bare 'yes' must escalate with `company_id` = Sorento's REAL
        `companies.id`, not None, so `next-assignee`'s own company-scoped draw
        (`app/api/v1/external/next_assignee.py:280`) narrows to Sorento's own roster
        instead of falling back to the contact's default company."""
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope(
            [_order_row(MOCHA, "ZZTM2609-0901"), _order_row(MOCHA, "ZZTM2609-0902")],
            [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}],
        )
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b1-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        said = _said(result)
        assert f"escalate to *{SORENTO}*" in said, said
        pending = _open_question(session_factory)
        assert pending, pending

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b1-2", text="yes"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        ec = escalation_context(item, ctx=ctx)
        assert ec.get("company_name") == SORENTO, ec

        next_body = _next_assignee_body(ctx, ec)
        assert next_body.get("company_id") == ids["b"], (
            f"a bare 'yes' after the Sorento-named offer must draw the assignee from "
            f"Sorento's OWN company scope - the offer's own real company id must reach "
            f"the round-robin body, never None: {next_body!r}"
        )

        sla_body = _sla_body(ctx, ec, assignee={})
        assert sla_body.get("company_id") == ids["b"], (
            f"with no assignee answer overriding it, the SLA row must fall back to the "
            f"SAME real company id the round robin was asked to draw from: {sla_body!r}"
        )


def _revoke_company(session_factory: Any, *, contact_id: str, company_id: str) -> None:
    """Simulates a membership revoked BETWEEN turns - `respond_contact_companies` is
    admin-managed, not derived, so this is a real state a contact's scope can reach."""
    db = session_factory()
    contact = db.query(RespondContact).filter(RespondContact.respond_io_id == contact_id).one()
    db.query(RespondContactCompany).filter(
        RespondContactCompany.respond_contact_id == contact.id,
        RespondContactCompany.company_id == company_id,
    ).delete()
    db.commit()


# --------------------------------------------------------------------------- #
# Security SF-1 (hand pass 11 final re-check) - a company carried on a PERSISTED
# offer must be re-validated against the contact's CURRENT scope on the answering
# turn, not just trusted because it was in scope when the offer was minted.
# --------------------------------------------------------------------------- #


class TestSF1RevokedCompanyScopeDoesNotRoute:
    def test_membership_revoked_between_turns_does_not_route_to_the_lost_company(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """Same scenario as `TestBlocker1SilentCompanyOfferRoutesByRealCompanyId` (that
        test is the "an in-scope id still routes" half of this finding - unmodified,
        still green with the fix in place) - Mocha hit, Sorento silent, EXCEPT the
        contact's membership in Sorento is revoked between the offer and the bare
        "yes" that would have escalated to it. The carried `company_id` must not
        survive that: `_next_assignee_body`'s `company_id` must NOT be Sorento's id
        (None, or the contact-derived default - never the company the contact lost)."""
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope(
            [_order_row(MOCHA, "ZZTM2609-0901"), _order_row(MOCHA, "ZZTM2609-0902")],
            [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}],
        )
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sf1-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        assert f"escalate to *{SORENTO}*" in _said(result), _said(result)
        pending = _open_question(session_factory)
        assert pending, pending

        # Membership revoked BEFORE the answering turn - the pending option still
        # carries Sorento's id, but the contact can no longer see Sorento at all.
        _revoke_company(session_factory, contact_id=MULTICO_CONTACT_ID, company_id=ids["b"])

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sf1-2", text="yes"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        ec = escalation_context(item, ctx=ctx)

        next_body = _next_assignee_body(ctx, ec)
        assert next_body.get("company_id") != ids["b"], (
            f"a company the contact is no longer scoped to must never reach the "
            f"round-robin body just because it rode along on a stale pending: {next_body!r}"
        )

        sla_body = _sla_body(ctx, ec, assignee={})
        assert sla_body.get("company_id") != ids["b"], sla_body

    # Kill-test proof (measured this session, reverted and restored, `git diff`
    # confirmed clean before committing): reverting `engine.py`'s roster-plan scope
    # filter (the `if roster_plan: roster_plan = [row for row in roster_plan if ...]
    # or None` block, hoisted from `contact_scope`) back to an unconditional pass-
    # through makes `test_membership_revoked_between_turns_does_not_route_to_the_lost_
    # company` fail at `next_body.get("company_id") != ids["b"]` (Sorento's revoked id
    # comes back anyway), and leaves `TestBlocker1SilentCompanyOfferRoutesByRealCompanyId`
    # and the rest of this file green (the mutation only removes a filter that this
    # test is the sole one to exercise a REVOKED scope against).


# --------------------------------------------------------------------------- #
# BLOCKER 2(a) - a MISS in both companies, answered by NAME, routes by real id
# --------------------------------------------------------------------------- #


class TestBlocker2aCompanyWordRoutesByRealCompanyId:
    def test_mocha_routes_with_mochas_real_company_id(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope([], [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}])
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2a-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        assert "checked in" in _said(result), _said(result)

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": MOCHA.lower()},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2a-2", text="mocha"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, item = calls[0]
        ec = escalation_context(item, ctx=ctx)
        assert ec.get("company_name") == MOCHA, ec
        assert ec.get("company_id") == ids["a"], (
            f"a company answered BY NAME must resolve to that company's real id, not "
            f"None - the round robin has no way to scope to Mocha otherwise: {ec!r}"
        )


# --------------------------------------------------------------------------- #
# BLOCKER 2(b) - a MISS in both companies, answered with a BARE "yes", must ask WHICH
# company rather than assign blind - and answering that clarify by name must still
# resolve to the real company id.
# --------------------------------------------------------------------------- #


def _dry_run_escalation_lane(calls: list[tuple[Any, Any, Any]]):
    """Calls the REAL `escalation.run`, forced to `dry_run=True` regardless of what the
    engine passed - so the assertions below never depend on seeded Team/AgentTeam rows
    (`_preview_routing`'s own contract: read-only, degrades to `(None, None)` on any
    failure, `escalation.py:915-953`) while still exercising the REAL `_clarify_gate` /
    `escalation_context` / dry-run preview branch, unmodified."""

    def fake_run_escalation_lane(ctx, item, *, dry_run=False, session_factory=None):
        result = escalation_mod.run(ctx, item, dry_run=True, session_factory=session_factory)
        calls.append((ctx, item, result))
        return result

    return fake_run_escalation_lane


class TestBlocker2bBareYesOverAnUnresolvedMissAsksWhichCompany:
    def test_bare_yes_never_assigns_blind_and_asks_which_of_the_two_companies(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope([], [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}])
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2b-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        assert "checked in" in _said(result), _said(result)

        calls: list[tuple[Any, Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _dry_run_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2b-2", text="yes"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(calls) == 1, calls
        ctx, _item, lane_result = calls[0]

        assert not any(
            a.get("kind") == "assign_conversation" for a in (lane_result.get("actions") or [])
        ), (
            f"a bare 'yes' over TWO unresolved companies must never build an assignment "
            f"action - the company has to be clarified first: {lane_result!r}"
        )
        assert lane_result.get("pending") == {"kind": "company_clarify"}, (
            f"the turn must leave a company clarify pending so the NEXT message can "
            f"resolve it, exactly the marker the clarify arm already writes for the "
            f"(dead) member_offer path: {lane_result!r}"
        )
        clarify_text = next(
            (a.get("text") for a in (lane_result.get("actions") or []) if a.get("kind") == "send_message"),
            "",
        )
        assert MOCHA in clarify_text and SORENTO in clarify_text, (
            f"the clarify must name BOTH, and only, the two companies actually "
            f"searched: {clarify_text!r}"
        )
        from app.services.chatbot.lanes.escalation import _company_clarify_rows

        pool = [row["company_name"] for row in _company_clarify_rows(ctx)]
        assert set(pool) == {MOCHA, SORENTO}, (
            f"the clarify's own option pool must be exactly the two searched "
            f"companies: {pool!r}"
        )
        # NOT pinned: literal "1"/"2" digits printed in the clarify copy. Captain
        # ruling (hand pass 11 re-check, coder 42's report): `clarify_company_reply`
        # is graded byte for byte against the n8n capture corpus
        # (`test_s5_escalation_lane.py::_run_clarify_company_reply`), its tail is the
        # operator-editable `chatbot_reply_copy.CHATBOT_REPLY_OFFER_HOLD` template, and
        # `lanes/canned.py::offer_hold_clarify_text` + `test_s3_canned_and_ideate.py`
        # pin the SAME lead byte for byte - rewording it is a product/copy decision
        # with a Prompts-screen owner, not a code detail this test grades. The pick
        # IS numbered underneath (positions 1..N in exactly the printed order below)
        # even though the SENTENCE prints no digit - `TestThirdTurnClarifyAnswerRoutesByCompanyId`
        # pins that a bare "1" resolves to the first-printed company's real id.


# --------------------------------------------------------------------------- #
# The third-turn round trip: the clarify from BLOCKER 2b is answered by POSITION
# ("1") and, separately, the customer could equally answer by NAME (already proven
# turn-boundary-free by `TestBlocker2aCompanyWordRoutesByRealCompanyId` above) - both
# must route by the real `company_id`, never just the name.
# --------------------------------------------------------------------------- #


class TestThirdTurnClarifyAnswerRoutesByCompanyId:
    def test_a_bare_position_over_the_clarify_resolves_to_the_first_companys_real_id(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """T1 miss in both -> T2 bare "yes" clarifies (dry-run spy, the real
        `escalation.run`/`_clarify_gate`/`_question_offered` company branch,
        unmodified) -> T3 "1" answers the persisted `company_pick` pending by
        POSITION. The cheap round-trip the coder's own probe used (spy
        `run_escalation_lane`, read `escalation_context`/`_next_assignee_body` off
        the captured pair) rather than a live (non-dry) lane, which would need a
        company-scoped Team/AgentTeam/SLA-policy seed for a FRESH per-test company
        this file has no other reason to build."""
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope([], [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}])
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2c-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        assert "checked in" in _said(result), _said(result)

        # T2 - bare "yes" over the two-company miss clarifies (dry-run, real lane).
        dry_calls: list[tuple[Any, Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _dry_run_escalation_lane(dry_calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2c-2", text="yes"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "out_of_scope", (result2.branch_kind, result2.error)
        assert len(dry_calls) == 1, dry_calls
        _ctx2, _item2, lane_result2 = dry_calls[0]
        assert lane_result2.get("pending") == {"kind": "company_clarify"}, lane_result2

        pending = _open_question(session_factory)
        assert pending.get("kind") == "company_pick", (
            f"the clarify must persist as an answerable `company_pick` pending for "
            f"the NEXT turn to resolve: {pending!r}"
        )
        options = pending.get("options") or []
        assert len(options) == 2, options
        first = next((o for o in options if o.get("position") == 1), None)
        assert first is not None and first.get("label") == MOCHA, (
            f"positions follow the printed order (Mocha first) - the offer's own "
            f"printed pool: {options!r}"
        )
        focus_before = _focus_for(session_factory, MULTICO_CONTACT_ID)

        # T3 - "1" answers the persisted company_pick by POSITION.
        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
                is_affirmative=None, escalation={"is_escalation_confirmation": False, "company_pick": None},
            )
        )
        result3 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-b2c-3", text="1"),
            session_factory=session_factory,
        )
        assert result3.branch_kind == "out_of_scope", (result3.branch_kind, result3.error)
        assert len(calls) == 1, calls
        ctx3, item3 = calls[0]
        ec = escalation_context(item3, ctx=ctx3)
        assert ec.get("company_id") == ids["a"], (
            f"a bare position over the company clarify must resolve to the FIRST "
            f"printed company's (Mocha's) real id, not just its name riding along: {ec!r}"
        )
        assert ec.get("company_name") == MOCHA, ec

        next_body = _next_assignee_body(ctx3, ec)
        assert next_body.get("company_id") == ids["a"], (
            f"the round robin must be handed Mocha's real company id: {next_body!r}"
        )

        # Security N-2 (hand pass 11 final): Mocha's `company_id` is a COMPANY id, not a
        # product/customer entity - it must never reach `focus.uuids`.
        # `ESCALATION_OFFER_KINDS` routes `company_pick` to `_answer_offer` before the
        # generic roster path, so a casual "1" over this pending leaves focus exactly
        # where T1's resolve left it.
        focus_after = _focus_for(session_factory, MULTICO_CONTACT_ID)
        assert [p.get("uuid") for p in focus_after.get("products") or []] == [
            p.get("uuid") for p in focus_before.get("products") or []
        ], (focus_before, focus_after)
        assert [c.get("uuid") for c in focus_after.get("customers") or []] == [
            c.get("uuid") for c in focus_before.get("customers") or []
        ], (focus_before, focus_after)

    # Kill-test proof (measured this session, reverted and restored, `git diff`
    # confirmed clean before committing): reverting
    # `lanes/escalation.py::escalation_context`'s `elif pick_row is not None:` arm
    # (`company_id = jsc.get(pick_row, "company_id") or None` -> `company_id =
    # None`) - the ONE line that reads the id off the routing-roster-plan pool a
    # resolved `company_pick` matched against - makes
    # `test_a_bare_position_over_the_clarify_resolves_to_the_first_companys_real_id`
    # fail at `assert ec.get("company_id") == ids["a"]` (`None == '<uuid>'`), and
    # leaves every other test in this file green (the mutation is scoped to the
    # `company_pick` arm alone, which only this test and BLOCKER 2a exercise -
    # BLOCKER 2a fails too, correctly, for the same reason: both reach id resolution
    # through the identical pool-match arm).


# --------------------------------------------------------------------------- #
# SHOULD-FIX 3 (security) - `_searched_companies` must skip brand/category matches,
# exactly as the sentence it mirrors does (`_NO_TOOL_ID`).
# --------------------------------------------------------------------------- #


class TestSF3SearchedCompaniesSkipsBrandAndCategoryMatches:
    def test_a_brand_match_in_a_second_company_never_widens_the_searched_set(self) -> None:
        """Hand-built `resolved`/`gate` structures, the SAME shape
        `turn_runtime._company_names_by_uuid` reads and `gate.compatible_entities`
        carries - measured the same way the security pass itself found this
        (`rearch-phase3-security-hp11-recheck.md` finding 2, "Measured:
        `_searched_companies` returns ['Sorento', 'Cabana']..."). A product resolves
        ONLY in company A; a brand token resolves ONLY in company B. The "checked in"
        sentence names company A alone (`answer.py:2949`'s own `_NO_TOOL_ID` skip) -
        `_searched_companies` must agree.

        Captain ruling (hand pass 11 re-check, coder 42's report): the helper's
        RETURN SHAPE changed from a bare name list to the roster ROW
        `{"company_id", "company_name", "brand_code"}` - BLOCKER 1 needs the id to
        travel with the name from this exact function, its only source of
        `company_id` on the miss arm. Graded on the BEHAVIOUR (which companies
        survive, and that the surviving row is not stripped of its id), not the
        bare-list shape."""
        from app.services.chatbot.answer_bridge import _searched_companies

        resolved = {
            "resolutions": [
                {
                    "token": "srtwc1234",
                    "matches": [
                        {
                            "uuid": "prod-uuid-a",
                            "entity_type": "product",
                            "company_name": "ZZT Sorento",
                            "company_id": "co-uuid-a",
                        },
                    ],
                },
                {
                    "token": "cabana",
                    "matches": [
                        {
                            "uuid": "brand-uuid-b",
                            "entity_type": "brand",
                            "company_name": "ZZT Cabana",
                            "company_id": "co-uuid-b",
                        },
                    ],
                },
            ],
        }
        gate = {
            "compatible_entities": [
                {"uuid": "prod-uuid-a", "entity_type": "product"},
                {"uuid": "brand-uuid-b", "entity_type": "brand"},
            ],
        }
        companies = _searched_companies(resolved, gate)
        assert [c["company_name"] for c in companies] == ["ZZT Sorento"], (
            f"a brand/category match carries no tool-side id (_NO_TOOL_ID) and must "
            f"never be counted as a company the fetch actually searched: {companies!r}"
        )
        assert companies[0].get("company_id") == "co-uuid-a", (
            f"the surviving row must carry its own real company id (never stripped "
            f"to just the name) - BLOCKER 1 has nowhere else to read the miss arm's "
            f"company_id from: {companies!r}"
        )


# --------------------------------------------------------------------------- #
# SHOULD-FIX 4 (security) - the ladder's own offer must never be clobbered by the
# silent-company offer when both are eligible on one turn.
# --------------------------------------------------------------------------- #


def _stock_row_with_company(code: str, qty: int, company_name: str) -> dict[str, Any]:
    return {
        "fields": [
            {"key": "company_name", "label": "Company", "value": company_name},
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "total_on_hand", "label": "Total", "value": qty},
        ]
    }


def _stock_envelope(rows: list[dict[str, Any]], lookup: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "result_type": "stock",
        "intro": "Stock summary for the requested products." if rows else "No matching results found.",
        "items": rows,
        "has_result": bool(rows),
        "lookup_companies": lookup,
    }


class TestSF4LadderOfferNeverClobberedBySilentCompanyOffer:
    CODE = "ZZTSF4STOCK"
    CONTACT_ID = "ZZT-contact-sf4-ladder"

    def test_exactly_one_escalate_question_and_the_ladders_own_team_wins(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        a = _seed_company(session_factory, name="ZZT SF4 Co A")
        b = _seed_company(session_factory, name="ZZT SF4 Co B")
        _seed_product(session_factory, company_id=a, code=self.CODE)
        _seed_product(session_factory, company_id=b, code=self.CODE)
        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=self.CONTACT_ID, phone="+60000000930",
            workspace_id=workspace_id, company_ids=[a, b],
        )

        set_chatbot_switches(session_factory, business_lane=True)
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])
        _wire_real_resolve_entity(monkeypatch)
        _wire_answer_services(monkeypatch)

        stock_envelope = _stock_envelope(
            [_stock_row_with_company(self.CODE, 0, "ZZT SF4 Co A")],
            [{"id": a, "name": "ZZT SF4 Co A"}, {"id": b, "name": "ZZT SF4 Co B"}],
        )
        incoming_envelope = _incoming_rows(self.CODE)

        def _mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(stock_envelope)
            if name == INCOMING_TOOL:
                return json.dumps(incoming_envelope)
            if name == PO_TOOL:
                return json.dumps(EMPTY_PO)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        monkeypatch.setattr(
            engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
        )
        stub_parser(
            _parser_output(
                intent_hint="check_stock", domain_hint="inventory",
                entities=[
                    {"raw": self.CODE, "hint": "product", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                routing={"suggested_team": None, "suggested_agent": "general_enquiries", "team_source": None},
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(self.CONTACT_ID, message_id="zzt-sf4-1", text=f"{self.CODE} stock"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        # `_said` mirrors the reply's own text into an action too (`_send_message` /
        # the escalation lane's own carried-forward composition) - counting against
        # the RAW reply alone avoids double-counting one surviving offer as two.
        reply_text = (result.reply or {}).get("text") or ""
        said = _said(result)

        assert reply_text.lower().count("would you like me to escalate") == 1, (
            f"exactly ONE escalate question may reach the customer when both the "
            f"zero-stock ladder and the silent-company offer are eligible on the same "
            f"turn: {reply_text!r}"
        )
        assert "escalate to warehouse team" in reply_text, reply_text

        pending = _open_question_for(session_factory, self.CONTACT_ID)
        assert pending, "the surviving offer must mint an answerable pending"
        assert pending.get("team") == "warehouse", (
            f"the LADDER's own team must survive - never overwritten by the "
            f"silent-company offer's own (customer_service) team: {pending!r}"
        )


# --------------------------------------------------------------------------- #
# SHOULD-FIX 5 - a decline over either new HIT-arm offer clears the pending and
# assigns nobody.
# --------------------------------------------------------------------------- #


class TestSF5DeclineOverTheSilentCompanyOfferMakesNoAssignment:
    def test_no_declines_the_silent_company_offer_pending_cleared_no_assignment(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        ids = _two_company_chain(session_factory)
        envelope = _orders_envelope(
            [_order_row(MOCHA, "ZZTM2609-0910"), _order_row(MOCHA, "ZZTM2609-0911")],
            [{"id": ids["a"], "name": MOCHA}, {"id": ids["b"], "name": SORENTO}],
        )
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_parser(_order_verdict())
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sf5a-1", text=f"{PRODUCT_CODE} kim seng jaya send yet"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        assert _open_question(session_factory), "the silent-company offer must mint a pending"

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=False,
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(MULTICO_CONTACT_ID, message_id="zzt-sf5a-2", text="no"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "escalation_declined", (
            f"a 'no' over the silent-company offer must reach the generic decline lane, "
            f"never the escalation lane: {result2.branch_kind!r} {result2.error!r}"
        )
        assert not calls, ("no assignment may be made on a decline", calls)
        assert not _open_question(session_factory), (
            "the declined pending must be cleared, not left answerable again"
        )


class TestSF5DeclineOverTheZeroStockLadderOfferMakesNoAssignment:
    CODE = "ZZTSF5LADDER"
    CONTACT_ID = "ZZT-contact-sf5-ladder"

    def test_no_declines_the_ladder_offer_pending_cleared_no_assignment(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        from tests.chatbot.test_rearch_r11_zero_stock_ladder import _bundle, _stock_hit, _stock_row
        from app.models.user import SystemSetting

        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=self.CONTACT_ID, phone="+60000000950",
            workspace_id=workspace_id, company_ids=[],
        )
        set_chatbot_switches(session_factory, business_lane=True)
        db = session_factory()
        for row in db.query(SystemSetting).all():
            row.chatbot_completed_lanes = ["business_query"]
        db.commit()

        code_uuid = "60220000-0000-0000-0000-000000000099"

        def _mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(_stock_hit([_stock_row(self.CODE, 0, "compact")], "compact"))
            if name == INCOMING_TOOL:
                return json.dumps(_incoming_rows(self.CODE))
            if name == PO_TOOL:
                return json.dumps(EMPTY_PO)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        monkeypatch.setattr(
            engine_mod.business_services, "production_services",
            lambda db, *, space_id=None: _bundle({self.CODE: code_uuid}),
        )
        monkeypatch.setattr(
            engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
        )
        stub_parser(
            _parser_output(
                intent_hint="check_stock", domain_hint="inventory",
                entities=[{"raw": self.CODE, "hint": "product", "canonical_code": None, "current_message": True}],
            )
        )
        stub_access(attributes=["purchase_orders.placed", "inventory.sellable"])

        result = engine_mod.run_turn(
            _scope_envelope(self.CONTACT_ID, message_id="zzt-sf5b-1", text=f"{self.CODE} stock"),
            session_factory=session_factory,
        )
        assert result.status == "done", result.error
        said = "\n".join(
            [((result.reply or {}).get("text") or "")]
            + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
        )
        assert "escalate to warehouse team" in said, said
        pending = _open_question_for(session_factory, self.CONTACT_ID)
        assert pending, "the ladder's own HIT offer must mint an answerable pending"

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=False,
            )
        )
        result2 = engine_mod.run_turn(
            _scope_envelope(self.CONTACT_ID, message_id="zzt-sf5b-2", text="no"),
            session_factory=session_factory,
        )
        assert result2.branch_kind == "escalation_declined", (
            f"a 'no' over the ladder's own warehouse offer must reach the generic "
            f"decline lane, never the escalation lane: {result2.branch_kind!r} {result2.error!r}"
        )
        assert not calls, ("no assignment may be made on a decline", calls)
        assert not _open_question_for(session_factory, self.CONTACT_ID), (
            "the declined pending must be cleared, not left answerable again"
        )


# --------------------------------------------------------------------------- #
# NIT N-b - a persisted `team_pick` option whose `payload` is not a dict must not
# crash the answering turn.
# --------------------------------------------------------------------------- #


class TestNbMalformedOptionPayloadNeverCrashesTheAnsweringTurn:
    CONTACT_ID = "ZZT-contact-nb-malformed"

    def test_a_string_payload_on_a_team_pick_option_leaves_the_turn_done_not_failed(
        self, session_factory, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        from sqlalchemy import text as _sql_text

        workspace_id = _seed_workspace(session_factory)
        _seed_contact(
            session_factory, contact_id=self.CONTACT_ID, phone="+60000000940",
            workspace_id=workspace_id, company_ids=[],
        )
        set_chatbot_switches(session_factory, business_lane=True)
        _set_completed_lanes(session_factory, system_settings_row, ["business_query"])

        malformed_open_question = {
            "kind": "team_pick",
            "expects": "yes_no",
            "options": [
                {"position": 1, "label": "Yes", "entity_type": "team", "payload": "oops"},
            ],
            "team": "customer_service",
            "asked_at_turn": 1,
            "payload": {},
        }
        db = session_factory()
        db.execute(
            _sql_text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {
                "sv": json.dumps({
                    "variables": {},
                    "focus": None,
                    "open_question": malformed_open_question,
                    "ideation": None,
                    "access_levels": None,
                    "contains_flyer": None,
                }),
                "cid": self.CONTACT_ID,
            },
        )
        db.commit()

        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))
        stub_parser(
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                is_affirmative=True, escalation={"is_escalation_confirmation": True, "company_pick": None},
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            _scope_envelope(self.CONTACT_ID, message_id="zzt-nb-1", text="yes"),
            session_factory=session_factory,
        )
        assert result.status == "done", (
            f"a malformed persisted option payload must degrade to 'no company info', "
            f"never crash the whole turn to 'failed': status={result.status!r} "
            f"error={result.error!r}"
        )
        assert len(calls) == 1, calls
