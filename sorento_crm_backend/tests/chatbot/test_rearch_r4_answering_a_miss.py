"""R4 RED tests - ANSWERING a single-domain miss's open question
(PLAN-chatbot-answer-half-reattach.md; UAC AC-1700, AC-1703's tail, AC-1704).

`test_rearch_r4_bridge_miss.py` pins the WRITE side of the R4 bridge (`answer_bridge.
answer_for` does not exist on this branch yet - the bridge's miss arm is a later
coder slice). This file pins the ANSWERING side instead - `turn/apply.py::
_answer_pending` / `_answer_offer`, and the escalation lane call they feed - which is
GENERIC and already live regardless of who eventually mints the Pending. Every
scenario below therefore seeds the open question directly onto
`respond_contacts.session_vars.open_question` in the CURRENT five-key wire shape
(`session_state.FIVE_KEYS`), the same convention `test_r3_pending_end_to_end.py::
TestAnAbandonedMemberOfferStopsConfirming` uses for the same reason (composing a real
roster needs machinery this file is not about) - never the legacy nested `variables`
shape, which `session_state._legacy_option` cannot carry a `respond_user_id` through
at all (measured: it copies only `code`/`uuid`/`uuids`/`name`/`stamp` to the option and
`value`/`team` to its payload).

`member_offer` options are seeded in the EXACT shape `test_rearch_r4_bridge_miss.py::
TestMemberOfferPendingIsMintedByTheBridge` already pins for the bridge's own write side
(`label`, `payload.respond_user_id`) - a coder landing that write side later does not
have to reconcile two different option shapes for the same Pending kind.

Driven through `engine.run_turn` directly (`_stub_parser` + `_envelope`, both reused
from `test_r3_pending_end_to_end.py`) rather than the heavier `_run_turn` harness
(`test_outstanding_lane.py`) for the escalation-arm scenarios (1, 2, 4): answering an
ESCALATION_OFFER_KINDS pending (`_answer_offer`) short-circuits BEFORE any fetch, so no
resolver/fetch wiring is needed - only `engine_mod.run_escalation_lane` is monkeypatched
as a spy (the exact pattern `test_s5_escalation_seams.py` already uses for the same
seam), capturing `(ctx, item)` without needing the escalation lane's own DB-backed
round-robin/Team/AgentTeam machinery. Scenarios 3 and 5 exercise a REAL fetch and reuse
`test_outstanding_lane.py`'s own `_enable_business_lane` / `_wire_business_services` /
`_capturing_mcp` / `_resolve_services` helpers directly.

Postgres only (`session_factory`, blank schema). Every row seeded here.

**Measured facts (this session, 20 Sep 2026), each pinned below with its own citation**:

1. AC-1700 "position assigns that member": `turn/apply.py::_answer_offer` (~214-264)
   threads `option_payload.get("team") or pending.team` into `trace.team`, which
   `turn_runtime.lane_parse_output`'s own `accepted_team` chain carries into
   `ctx.parse.output.routing.suggested_team` - but NOTHING threads an assignee: the
   `Trace` dataclass (`turn/plan.py`) has no assignee field at all, and
   `lane_parse_output` never touches `ctx.parse.output.escalation.preferred_assignee_id`
   (main's own OLD mechanism, `head/output_exchange.py:~3626`, which
   `lanes/escalation.py::escalation_context:219` still reads and which the new engine
   never populates). RED.
2. AC-1700 "yes assigns automatically": the SAME `_answer_offer` accept path, with no
   `decision.positions` at all, leaves `option_payload` empty and threads only
   `pending.team` - already correct, no assignee is set. GREEN CONTROL.
3. AC-1700 "all dates re-runs for the same carried customer": measured live via a
   throwaway debug probe (written, run, deleted, never committed) - a plain
   `business_query` naming the order domain with NO new entities and NO date fields,
   while `focus.customers` already carries the customer and an UNRELATED `member_offer`
   is still open, runs `crm_order_management_orders_list` scoped to the SAME carried
   customer uuid and with no date args at all - the miss/offer machinery does not
   swallow it. GREEN CONTROL (the generic CARRY path already lets the message plan
   itself, per `turn/decide.py`'s own module docstring: "CARRY... the message runs as
   itself").
4. AC-1703 tail, accept: `turn/apply.py:604-614` (Item 8) ALREADY threads a bare "yes"
   over ANY pending whose `payload.escalate_offered is True` to the escalation lane with
   `pending.team`, regardless of pending.kind - so a did-you-mean ROSTER's own attached
   escalate offer already escalates correctly. GREEN CONTROL.
5. AC-1703 tail, decline: `test_sales_report_lane.py::TestDetailOfferLifecycle::
   test_a_decline_closes_it_with_the_offer_declined_copy` already documents that
   `turn/apply.py:617`'s decline branch sends a NON-roster offer kind (kind in
   `OFFER_KINDS`) to the generic `escalation_declined` lane ("Escalation declined."),
   never R22(a)'s specific `offer_declined` registry copy ("Okay, noted."). Measured
   here: a ROSTER kind (`product_pick`) is NEVER in `OFFER_KINDS` (`OFFER_KINDS =
   PENDING_KINDS - ROSTER_KINDS`), so a decline over THIS shape takes the OTHER branch
   at `turn/apply.py:629` (`return focus, None, None, False`) - no lane at all, not even
   the generic copy; the pending clears silently and the message is planned as itself.
   RED (production is expected to show SOME offer-declined acknowledgement).
6. AC-1704, continuing the original ask: measured via a throwaway debug probe (written,
   run, deleted). A POSITION answer over a `product_pick` roster whose `payload.domain`
   is seeded correctly ALREADY re-runs the fetch in the ORIGINAL domain, scoped to the
   picked option's own uuid (`turn/apply.py`'s contract-121 mechanism, lines ~581-602) -
   for the `incoming` domain. GREEN CONTROL. A TYPED CODE answer (the customer retypes
   the offered label instead of its number) over the SAME roster originally reached
   `decide()`'s `_positions_by_label` match but ran its fetch scoped to nothing (the
   typed entity's own fresh, uuid-less resolution) - FIXED (coder 30, `turn/apply.py::
   _focus_rules` now skips a kind `trace.picked_kinds` already settled this turn); GREEN.
   The `product_attachment` domain originally failed BOTH ways (position AND typed code)
   for a FIXTURE reason, not a code gap - `product_attachment`'s own narrowing policy
   (`turn/policy_rows.py:61`) narrows `attachment_type` before `product`, and the seed
   never carried the asking turn's own attachment_type forward, so `attachment_type` was
   genuinely, correctly unknown and production rightly re-asked for it. FIXED (tester 34,
   `_seed` now carries `focus.extra["attachment_type"]`, matching what the asking turn
   would have left); GREEN, the pick continues to a real fetch scoped to that product.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output
from tests.chatbot.test_r3_pending_end_to_end import _session_of, _stub_parser
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _enable_business_lane,
    _resolve_services,
    _wire_business_services,
)


def _write_session_vars(session_factory, sv: dict[str, Any]) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :cid"),
        {"cid": str(CONTACT_ID), "sv": json.dumps(sv)},
    )
    db.commit()


def _seed_bare_contact(session_factory) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({})},
    )
    db.commit()


def _member_option(
    position: int, *, label: str, uuid: str, respond_user_id: str, team: str | None = None
) -> dict[str, Any]:
    """The shape `test_rearch_r4_bridge_miss.py::TestMemberOfferPendingIsMintedByTheBridge`
    already pins for the bridge's own WRITE side (`label`, `payload.respond_user_id`)."""
    payload: dict[str, Any] = {"respond_user_id": respond_user_id}
    if team:
        payload["team"] = team
    return {
        "position": position,
        "label": label,
        "uuid": uuid,
        "entity_type": "member_offer",
        "payload": payload,
    }


def _seed_member_offer(
    session_factory,
    *,
    options: list[dict[str, Any]],
    team: str = "customer_service",
    domain: str = "order",
    asked_at_turn: int = 1,
) -> None:
    _write_session_vars(
        session_factory,
        {
            "open_question": {
                "kind": "member_offer",
                "expects": "pick",
                "options": options,
                "team": team,
                "asked_at_turn": asked_at_turn,
                "payload": {"domain": domain},
            }
        },
    )


def _fake_escalation_lane(calls: list[tuple[Any, Any]]):
    def fake_run_escalation_lane(ctx, item, *, dry_run=False, session_factory=None):
        calls.append((ctx, item))
        return {"arm": "assign", "actions": [], "pending": None}

    return fake_run_escalation_lane


def _run(session_factory, monkeypatch, *, qf: dict[str, Any], text_body: str, msg_id: str):
    _stub_parser(monkeypatch, qf)
    envelope = _envelope(is_test=False)
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text_body
    return engine_mod.run_turn(envelope, session_factory=session_factory)


# --------------------------------------------------------------------------- #
# AC-1700 - answering a member_offer miss
# --------------------------------------------------------------------------- #


class TestAC1700AnsweringAMemberOfferMiss:
    def test_a_position_assigns_that_member(self, session_factory, monkeypatch) -> None:
        _seed_bare_contact(session_factory)
        _seed_member_offer(
            session_factory,
            options=[
                _member_option(1, label="Ah Chong", uuid="u1", respond_user_id="ru1"),
                _member_option(2, label="Siti", uuid="u2", respond_user_id="ru2"),
            ],
        )
        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))

        result = self._run_position(session_factory, monkeypatch, "1", "zzt-r4-member-position")

        assert result.branch_kind == "out_of_scope", (
            f"a position over an open member_offer must reach the escalation lane: "
            f"branch_kind={result.branch_kind!r} error={result.error!r}"
        )
        assert len(calls) == 1, f"the escalation lane must be called exactly once: {calls}"
        ctx, _item = calls[0]
        output = ((ctx.get("parse") or {}).get("output")) or {}
        assert (output.get("routing") or {}).get("suggested_team") == "customer_service", (
            output.get("routing")
        )
        assignee_id = (output.get("escalation") or {}).get("preferred_assignee_id")
        assert assignee_id == "u1", (
            "AC-1700 (re-pinned for security finding M2, fixed lane commit "
            "f724be767): a position over an open member_offer must assign THAT "
            "member by their own uuid 'u1' for Ah Chong, position 1 (a real "
            "users.id) - never the respond_user_id 'ru1'. "
            "app/api/v1/external/next_assignee.py:460-476 resolves "
            "preferred_assignee_id via TeamMember.user_id, and "
            "lanes/escalation.py:219-221 matches it against a roster row's own "
            f"uuid, both users.id, never a respond.io id (escalation="
            f"{output.get('escalation')!r})"
        )
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != "member_offer", (
            "the Pending must close once a position answers it"
        )

    def test_yes_assigns_automatically(self, session_factory, monkeypatch) -> None:
        """GREEN CONTROL: `_answer_offer`'s bare-yes accept path already threads only
        `pending.team`, never a specific assignee, since `option_payload` is empty when
        no position was picked."""
        _seed_bare_contact(session_factory)
        _seed_member_offer(
            session_factory,
            options=[
                _member_option(1, label="Ah Chong", uuid="u1", respond_user_id="ru1"),
                _member_option(2, label="Siti", uuid="u2", respond_user_id="ru2"),
            ],
        )
        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))

        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
        )
        result = self._run_qf(session_factory, monkeypatch, qf, "yes", "zzt-r4-member-yes")

        assert result.branch_kind == "out_of_scope", (
            f"a bare yes over an open member_offer must reach the escalation lane: "
            f"branch_kind={result.branch_kind!r} error={result.error!r}"
        )
        assert len(calls) == 1, f"the escalation lane must be called exactly once: {calls}"
        ctx, _item = calls[0]
        output = ((ctx.get("parse") or {}).get("output")) or {}
        assert (output.get("routing") or {}).get("suggested_team") == "customer_service"
        assignee_id = (output.get("escalation") or {}).get("preferred_assignee_id")
        assert assignee_id is None, (
            "AC-1700: a bare 'yes' must assign AUTOMATICALLY (round robin), never a "
            f"specific member - got preferred_assignee_id={assignee_id!r}"
        )
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != "member_offer"

    def test_all_dates_asks_which_period_and_a_dated_follow_up_reruns_for_the_carried_customer(
        self, session_factory, monkeypatch
    ) -> None:
        """DO range rules for every contact (owner 4 Oct 2026). A dated miss offers "another
        month or dates", never "all dates"; typing "all dates" is a dateless DO list ask, so it
        gets the period question and fetches nothing; a dated follow-up (an August window) then
        re-runs the order fetch exactly once, scoped to the SAME carried customer, over an
        UNRELATED open member_offer that must not swallow it."""
        from app.services.chatbot.turn.state import Focus, focus_to_wire

        _seed_bare_contact(session_factory)
        customer_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        focus = Focus(
            customers=[{"uuid": customer_uuid, "hint": "customer", "current_message": False}],
            domains=["order"],
        )
        _write_session_vars(
            session_factory,
            {
                "focus": focus_to_wire(focus),
                "open_question": {
                    "kind": "member_offer",
                    "expects": "pick",
                    "options": [
                        _member_option(1, label="Ah Chong", uuid="u1", respond_user_id="ru1"),
                    ],
                    "team": "customer_service",
                    "asked_at_turn": 1,
                    "payload": {"domain": "order"},
                },
            },
        )
        _enable_business_lane(session_factory)
        call, captured = _capturing_mcp({"has_result": False, "items": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)
        routing = {
            "suggested_team": "customer_service",
            "suggested_agent": "order_enquiries",
            "team_source": "parser",
        }

        def ask(start, end, text_body, msg_id):
            qf = _parser_output(
                message_type="business_query",
                intent_hint="check_order",
                domain_hint="order",
                entities=[],
                date_filter_start=start,
                date_filter_end=end,
                routing=routing,
            )
            mark = len(captured)
            result = self._run_qf(session_factory, monkeypatch, qf, text_body, msg_id)
            assert result.error is None, result.error
            return (result.reply or {}).get("text") or "", captured[mark:]

        # 1. "all dates" (the dated miss hint no longer offers it; see `test_rearch_r4_miss_engine.py`)
        # is a dateless DO list ask: the period question, no fetch.
        said, fetched = ask(None, None, "all dates", "zzt-r4-all-dates")
        assert fetched == [], f"'all dates' must not fetch: {fetched}"
        assert "Which period" in said, said

        # 2. A dated follow-up re-runs the fetch exactly once for the carried customer.
        _said, fetched = ask("2026-08-01", "2026-08-31", "august", "zzt-r4-august")
        assert len(fetched) == 1, f"the dated follow-up must re-run the order fetch exactly once: {fetched}"
        name, args = fetched[0]
        assert name == "crm_order_management_orders_list", (name, fetched)
        assert args.get("customer_ids") == [customer_uuid], (
            "the re-run must stay scoped to the SAME carried customer, not swallowed "
            f"by the unrelated open member_offer: {args!r}"
        )
        assert args.get("actual_delivery_date_from") == "2026-08-01", args
        assert args.get("actual_delivery_date_to") == "2026-08-31", args

    def _run_position(self, session_factory, monkeypatch, position_text: str, msg_id: str):
        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[int(position_text)],
        )
        return self._run_qf(session_factory, monkeypatch, qf, position_text, msg_id)

    def _run_qf(self, session_factory, monkeypatch, qf, text_body, msg_id):
        return _run(session_factory, monkeypatch, qf=qf, text_body=text_body, msg_id=msg_id)


# --------------------------------------------------------------------------- #
# AC-1703 tail - escalating over a did-you-mean roster's own attached offer
# --------------------------------------------------------------------------- #


class TestAC1703EscalateOverARoster:
    """A did-you-mean `product_pick` roster with `payload.escalate_offered is True`.
    The accept half is a GREEN CONTROL (Item 8, `turn/apply.py:604-614`, already threads
    a bare 'yes' to the escalation lane with `pending.team` for ANY pending carrying that
    flag). The decline half is measured RED against `test_sales_report_lane.py::
    TestDetailOfferLifecycle::test_a_decline_closes_it_with_the_offer_declined_copy`'s
    own documented seam (see module docstring point 5)."""

    def _seed_roster(self, session_factory) -> None:
        _write_session_vars(
            session_factory,
            {
                "open_question": {
                    "kind": "product_pick",
                    "expects": "pick",
                    "options": [
                        {
                            "position": 1, "label": "SRTWT165-FTX", "uuid": "prod-dym-1",
                            "uuids": ["prod-dym-1"], "entity_type": "product", "payload": {},
                        },
                        {
                            "position": 2, "label": "SRTWT165-FTY", "uuid": "prod-dym-2",
                            "uuids": ["prod-dym-2"], "entity_type": "product", "payload": {},
                        },
                    ],
                    "team": "purchasing",
                    "asked_at_turn": 1,
                    "payload": {"domain": "incoming", "escalate_offered": True},
                }
            },
        )

    def test_a_yes_escalates_to_the_pending_team(self, session_factory, monkeypatch) -> None:
        _seed_bare_contact(session_factory)
        self._seed_roster(session_factory)
        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))

        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            is_affirmative=True,
            # #1323: a "yes" over an escalation offer is the parser's semantic verdict.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
        )
        result = _run(session_factory, monkeypatch, qf=qf, text_body="yes", msg_id="zzt-r4-roster-yes")

        assert result.branch_kind == "out_of_scope", (
            f"a yes over a did-you-mean roster's escalate offer must reach the "
            f"escalation lane: branch_kind={result.branch_kind!r} error={result.error!r}"
        )
        assert len(calls) == 1, calls
        ctx, _item = calls[0]
        output = ((ctx.get("parse") or {}).get("output")) or {}
        assert (output.get("routing") or {}).get("suggested_team") == "purchasing", (
            "the escalation must route to the ROSTER's own team (purchasing), "
            f"got {output.get('routing')!r}"
        )

    def test_a_decline_gets_the_offer_declined_copy_not_silence(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_bare_contact(session_factory)
        self._seed_roster(session_factory)
        calls: list[tuple[Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake_escalation_lane(calls))

        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            is_affirmative=False,
        )
        result = _run(
            session_factory, monkeypatch, qf=qf, text_body="no thanks", msg_id="zzt-r4-roster-decline"
        )

        assert calls == [], "a decline must never reach the escalation lane"
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != "product_pick", (
            "the Pending must close on a decline either way"
        )
        reply_text = (result.reply or {}).get("text") or ""
        assert reply_text.strip() == "Okay, noted.", (
            "AC-1703: declining a did-you-mean roster's attached escalate offer must "
            "close it with R22(a)'s own offer-declined registry copy ('Okay, noted.') - "
            "measured: turn/apply.py's decline branch (line ~617) only reaches the "
            "escalation_declined lane when pending.kind is in OFFER_KINDS, and a ROSTER "
            "kind (product_pick) is NEVER in OFFER_KINDS (OFFER_KINDS = PENDING_KINDS - "
            f"ROSTER_KINDS), so no lane at all is set and no acknowledgement is composed: "
            f"got branch_kind={result.branch_kind!r} reply={reply_text!r}"
        )
        # R5 re-pin (tester 32, 20 Sep 2026, test_dry_run_isolation.py::
        # TestWordsComposedAreWordsSent's own completeness check): the words above must
        # actually reach the customer as an ACTION, not merely sit on `result.reply`.
        assert result.actions and result.actions[-1]["kind"] == "send_message", result.actions


# --------------------------------------------------------------------------- #
# AC-1704 - continuing the ORIGINAL ask after a did-you-mean pick
# --------------------------------------------------------------------------- #


class TestAC1704ContinuingTheOriginalAsk:
    """A `product_pick` roster whose `payload.domain` is seeded correctly (bypassing
    the bridge's own write-side gap reported separately under item A5). Measured live
    (module docstring point 6): the POSITION path already works for `incoming`; the
    TYPED-CODE path loses the roster's own resolved uuid even for `incoming`; the
    `product_attachment` domain fails BOTH ways."""

    _OPTIONS = [
        {
            "position": 1, "label": "SRTWT165-FTX", "uuid": "11111111-1111-1111-1111-111111111111",
            "uuids": ["11111111-1111-1111-1111-111111111111"], "code": "SRTWT165-FTX",
            "entity_type": "product", "payload": {},
        },
        {
            "position": 2, "label": "SRTWT165-FTY", "uuid": "22222222-2222-2222-2222-222222222222",
            "uuids": ["22222222-2222-2222-2222-222222222222"], "code": "SRTWT165-FTY",
            "entity_type": "product", "payload": {},
        },
    ]

    def _seed(self, session_factory, *, domain: str) -> None:
        _seed_bare_contact(session_factory)
        sv: dict[str, Any] = {
            "open_question": {
                "kind": "product_pick",
                "expects": "pick",
                "options": self._OPTIONS,
                "team": "purchasing",
                "asked_at_turn": 1,
                "payload": {"domain": domain, "escalate_offered": True},
            }
        }
        if domain == "product_attachment":
            # AC-1704 defect adjudication (tester 34, 20 Sep 2026, coder 30's own
            # report). MEASURED: `product_attachment`'s own narrowing policy dict
            # (`turn/policy_rows.py:61`, `narrowing={"attachment_type": "narrow_by_type",
            # "product": "must_narrow_one"}`) narrows `attachment_type` BEFORE `product` -
            # a real `product_pick` roster for this domain only ever exists once
            # `attachment_type` is already known (`test_rearch_r5_production_decides.py::
            # TestRequireSpecificRosterCopy::
            # test_product_attachment_ambiguous_family_uses_gates_own_header_and_stamps`'s
            # own seed always pairs the ambiguous product with an explicit
            # `attachment_type` entity in the SAME asking message). The ORIGINAL fixture
            # here never carried that forward, so `attachment_type` was genuinely,
            # correctly unknown on the answering turn and production rightly re-asked for
            # it - not a bug this test should have pinned. Fixed: seed the carried
            # attachment type on `focus.extra["attachment_type"]`, exactly what the
            # asking turn would have left there, so the answering turn's narrowing
            # reaches `product` (already settled by the pick) instead of re-asking.
            from app.services.chatbot.turn.state import Focus, focus_to_wire

            focus = Focus(
                extra={
                    "attachment_type": [
                        {
                            "raw": "product photos",
                            "hint": "attachment_type",
                            "canonical_code": "photo",
                            "current_message": False,
                            "confident": True,
                        }
                    ]
                }
            )
            sv["focus"] = focus_to_wire(focus)
        _write_session_vars(session_factory, sv)
        _enable_business_lane(session_factory)

    def test_a_position_continues_the_original_incoming_ask_with_the_picked_uuid(
        self, session_factory, monkeypatch
    ) -> None:
        """GREEN CONTROL."""
        self._seed(session_factory, domain="incoming")
        call, captured = _capturing_mcp({"has_result": False, "items": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)

        qf = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[1],
        )
        result = _run(session_factory, monkeypatch, qf=qf, text_body="1", msg_id="zzt-r4-ac1704-pos-incoming")

        assert result.error is None, result.error
        product_ids = {u for _name, args in captured for u in (args.get("product_ids") or [])}
        assert "11111111-1111-1111-1111-111111111111" in product_ids, (
            f"a position over the roster must continue the fetch scoped to THAT "
            f"option's own uuid: {captured!r}"
        )

    def test_a_typed_code_loses_the_rosters_own_resolved_uuid_on_incoming(
        self, session_factory, monkeypatch
    ) -> None:
        """RED. `decide()`'s `_positions_by_label` DOES recognise "SRTWT165-FTX" as
        answering position 1 (same as a bare "1" would) - but the fetch that then runs
        is scoped to the message's own FRESH, uuid-less resolution of the typed text,
        never to the roster's own already-resolved uuid sitting on the matched option."""
        self._seed(session_factory, domain="incoming")
        call, captured = _capturing_mcp({"has_result": False, "items": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)

        qf = _parser_output(
            message_type="business_query", intent_hint=None, domain_hint=None,
            entities=[
                {
                    "raw": "SRTWT165-FTX", "hint": "product", "canonical_code": "SRTWT165-FTX",
                    "current_message": True, "confident": True,
                }
            ],
        )
        result = _run(
            session_factory, monkeypatch, qf=qf, text_body="SRTWT165-FTX",
            msg_id="zzt-r4-ac1704-code-incoming",
        )

        assert result.error is None, result.error
        product_ids = {u for _name, args in captured for u in (args.get("product_ids") or [])}
        assert "11111111-1111-1111-1111-111111111111" in product_ids, (
            "AC-1704: replying with the OFFERED CODE must continue the original ask "
            "using that option's own already-resolved uuid, exactly like a position "
            f"pick does - instead the fetch ran with no matching product uuid at all "
            f"(captured={captured!r}, reply={(result.reply or {}).get('text')!r})"
        )

    @pytest.mark.parametrize("answer_mode", ["position", "code"])
    def test_product_attachment_domain_reasks_instead_of_continuing(
        self, session_factory, monkeypatch, answer_mode: str
    ) -> None:
        """DEFECT ADJUDICATION (tester 34, 20 Sep 2026, coder 30's own report): this was
        RED for a FIXTURE reason, not a code gap - `product_attachment`'s own narrowing
        policy (`turn/policy_rows.py:61`) narrows `attachment_type` before `product`, and
        `_seed` never carried the asking turn's own attachment_type forward. Fixed in
        `_seed` (see its own docstring); GREEN now on both `position` and `code` - the
        pick continues to a REAL fetch scoped to the picked option's own product uuid."""
        self._seed(session_factory, domain="product_attachment")
        call, captured = _capturing_mcp({"has_result": False, "items": []})
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)

        if answer_mode == "position":
            qf = _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            )
            text_body = "1"
        else:
            qf = _parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None,
                entities=[
                    {
                        "raw": "SRTWT165-FTX", "hint": "product", "canonical_code": "SRTWT165-FTX",
                        "current_message": True, "confident": True,
                    }
                ],
            )
            text_body = "SRTWT165-FTX"

        result = _run(
            session_factory, monkeypatch, qf=qf, text_body=text_body,
            msg_id=f"zzt-r4-ac1704-{answer_mode}-attachment",
        )

        assert result.error is None, result.error
        reply_text = (result.reply or {}).get("text") or ""
        assert captured, (
            f"AC-1704: answering the roster ({answer_mode}) must continue the ORIGINAL "
            f"product_attachment ask with a real fetch - instead nothing was called at "
            f"all and the same roster question was re-printed: {reply_text!r}"
        )
        _name, args = captured[0]
        assert args.get("product_ids") == ["11111111-1111-1111-1111-111111111111"], (
            "AC-1704: the fetch must be scoped to the PICKED option's own already-"
            f"resolved product uuid (position 1, SRTWT165-FTX): captured={captured!r}"
        )
