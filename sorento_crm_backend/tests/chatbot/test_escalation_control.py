"""ESCALATION-CONTROL (owner, 30 Sep 2026; PLAN-escalation-control-30sep.md).

"We need to be able to control each contact that they cannot access the escalation:
cannot force escalate, won't be offered escalation; this is for dealer."

Owner change, 30 Sep 2026: ONE per-contact flag decides, `respond_contacts.
escalation_allowed` (NOT NULL, default true, every existing contact backfilled true). Access
types no longer decide anything. A contact whose flag is unticked on the contact page is
offered no hand-off, sees no routing picker, and asking for a person or answering an old
offer gets "Please refer to your salesman." (`turn/task.py::REFER_TO_SALESMAN`) with no
hand-off. A miss says what could not be found, THEN "Please refer to your salesman."
(owner ruling Q2), never the bare line. Staff and allowed contacts are unchanged.

Engine turns reuse `test_escalation_agent_carry.py`'s harness: the real engine, the
`/external/next-assignee` and SLA seams captured at their own boundary.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot import escalation_control
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.state import Profile, escalation_barred, offers_escalation
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import (
    _capture_next_assignee,
    _capture_sla,
    _second_turn_envelope,
    _seed_contact,
    _seed_product,
    _stub_incoming_probe_empty,
    _write_open_question,
    _yes_verdict,
)

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

def _contact_pk(session_factory) -> str:
    return session_factory().execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
    ).scalar()


def _set_flag(session_factory, allowed: bool) -> str:
    """The contact page's "Chatbot hands over to support teams" switch, set on the one row."""
    db = session_factory()
    pk = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
    ).scalar()
    db.execute(text("UPDATE respond_contacts SET escalation_allowed = :a WHERE id = :c"), {"a": allowed, "c": pk})
    db.commit()
    return pk


def _make_dealer(session_factory) -> str:
    """A blocked contact: the flag unticked."""
    return _set_flag(session_factory, False)


def _give_types(session_factory, pk: str, names: list[str]) -> None:
    """Access types on the contact. They no longer decide escalation (owner change)."""
    db = session_factory()
    for name in names:
        code = f"zzt_{name.lower().replace(' ', '_')}"
        db.execute(
            text(
                "INSERT INTO contact_access_types (code, name, is_active) VALUES (:c, :n, true) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"c": code, "n": name},
        )
        db.execute(
            text("INSERT INTO respond_contact_access_types (contact_id, access_type_code) VALUES (:p, :c)"),
            {"p": pk, "c": code},
        )
    db.commit()


def _reply(result: Any) -> str:
    """The reply text and every action the turn would send, as one string."""
    import json

    reply = result.reply if isinstance(result.reply, dict) else {}
    return " ".join([str(reply.get("text") or ""), json.dumps(result.actions or [], default=str)])


def _open_question(session_factory) -> Any:
    row = session_factory().execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    ).first()
    return (row.session_vars or {}).get("open_question")


def _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access):
    """A real incoming ETA miss (AC-1785's turn 1): for an unbarred contact the reply
    offers "Would you like me to escalate to purchasing team?" and arms a team_pick."""
    _seed_product(session_factory, code="ZZTSC07")
    _stub_incoming_probe_empty(monkeypatch)
    stub_parser(
        verdict(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[entity("ZZTSC07", hint="product", confident=True)],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
    )
    stub_access()
    return engine_mod.run_turn(_envelope(), session_factory=session_factory)


def _help_verdict() -> dict[str, Any]:
    return verdict(
        message_type="request_for_help",
        domain_hint=None,
        intent_hint=None,
        entities=[],
        user_goal="wants to talk to a human",
        routing={"suggested_team": "customer_service", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


# --------------------------------------------------------------------------- #
# The per-contact flag (owner change, 30 Sep 2026)
# --------------------------------------------------------------------------- #

MR_LOO_TYPES = [
    "Sorento Office",
    "Sorento Dealer",
    "Mocha Dealer",
    "Mocha Office",
    "Cabana Office",
    "Cabana Dealer",
    "End User",
]


class TestTheContactFlagDecides:
    def test_a_new_contact_is_allowed(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        _seed_contact(session_factory, phone="+60900000046")
        stored = session_factory().execute(
            text("SELECT escalation_allowed FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()
        assert stored is True
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.escalation_allowed is True

    def test_the_flag_unticked_blocks(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        _seed_contact(session_factory, phone="+60900000052")
        _set_flag(session_factory, False)
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.escalation_allowed is False

    def test_dealer_access_types_do_not_block_when_the_flag_allows(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        _seed_contact(session_factory, phone="+60900000051")
        _give_types(session_factory, _contact_pk(session_factory), ["Sorento Dealer", "Mocha Dealer"])
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.escalation_allowed is True

    def test_mr_loo_asking_for_a_person_is_handed_over(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The owner's hand test: Mr Loo (office, dealer and end-user types) is allowed."""
        _seed_contact(session_factory, phone="+60900000045")
        _give_types(session_factory, _contact_pk(session_factory), MR_LOO_TYPES)
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        stub_parser(_help_verdict())
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.branch_kind == "out_of_scope" and len(bodies) == 1, (result.branch_kind, bodies)
        assert REFER_TO_SALESMAN not in _reply(result)


# --------------------------------------------------------------------------- #
# The helper every offer site reads (R2)
# --------------------------------------------------------------------------- #


class TestOffersEscalation:
    def test_the_three_audiences(self) -> None:
        assert offers_escalation(Profile()) is True
        assert offers_escalation(None) is True
        assert offers_escalation(Profile(tier="office")) is False
        assert offers_escalation(Profile(escalation_allowed=False)) is False
        assert escalation_barred(Profile(tier="office")) is False

    def test_no_offer_site_reads_the_staff_check_alone(self) -> None:
        """Every bot-initiated offer gate reads `offers_escalation`, so a gate still calling
        `is_staff_profile` directly would offer a dealer. The one call left is
        `answer_bridge`'s staff-only roster trim, which the barred strip right after it
        (`without_escalation`) covers."""
        import ast

        root = Path(__file__).resolve().parents[2] / "app" / "services" / "chatbot"
        calls: dict[str, int] = {}
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "is_staff_profile":
                    key = str(path.relative_to(root))
                    calls[key] = calls.get(key, 0) + 1
        assert calls == {"answer_bridge.py": 1, "turn/state.py": 1}, calls

#: Every "no offer for this audience" scenario the staff suite pins, one per offer builder
#: (`turn/compose.py`'s composer offer, `answer_bridge`'s cross-domain ladder rung, the
#: order miss, `not_found_error_message`'s outstanding and catalogue branches, the
#: withheld-offer pending, the did-you-mean roster). Replayed with the audience swapped
#: from staff to a BARRED DEALER: whichever builder fires, it must stay silent.
_STAFF_NO_OFFER_SCENARIOS = [
    "test_t10_staff_inventory_miss_gets_no_warehouse_escalation_offer",
    "test_t11_staff_stock_miss_composer_offer_absent",
    "test_staff_ladder_rung_gets_no_warehouse_escalation_offer",
    "test_staff_order_miss_gets_no_customer_service_offer",
    "test_staff_outstanding_report_empty_miss_gets_no_offer",
    "test_staff_catalog_could_not_find_miss_gets_no_offer",
    "test_withheld_offer_arms_no_hidden_escalation",
    "test_staff_did_you_mean_roster_stays_open_without_an_offer",
]


@pytest.mark.parametrize("scenario", _STAFF_NO_OFFER_SCENARIOS)
def test_every_offer_builder_is_silent_for_a_barred_dealer(scenario, request, monkeypatch) -> None:
    import inspect

    from tests.chatbot import test_samantha_26sep_s11_escalation_audience as staff_suite

    def barred_dealer(**kwargs: Any) -> Profile:
        return Profile(**{**kwargs, "tier": "dealer", "escalation_allowed": False})

    monkeypatch.setattr(staff_suite, "Profile", barred_dealer)
    fn = getattr(staff_suite, scenario)
    fn(**{name: request.getfixturevalue(name) for name in inspect.signature(fn).parameters})


# --------------------------------------------------------------------------- #
# The backstop (R2): whatever composed the reply, no offer survives
# --------------------------------------------------------------------------- #


class TestStripOffers:
    def test_the_offer_sentence_goes_and_the_salesman_line_stands(self) -> None:
        answer = SimpleAnswer("No stock found.\n\nWould you like me to escalate to purchasing team?")
        out = escalation_control.strip_offers(answer, Profile(escalation_allowed=False))
        assert out.text == f"No stock found.\n\n{REFER_TO_SALESMAN}"

    def test_a_routing_picker_goes_whole(self) -> None:
        text_ = (
            "Couldn't find ZZT.\n\nWould you like me to escalate to customer service team?\n\n"
            "Please choose who to route to (reply with the number):\n1. Alice\n2. Bob\n\n"
            "If you have no preference, just reply 'yes' and we'll assign automatically."
        )
        question = pending_ask(
            "member_offer",
            [
                {"position": 1, "label": "Alice", "entity_type": "member"},
                {"position": 2, "label": "Bob", "entity_type": "member"},
            ],
            team="customer_service",
        )
        out = escalation_control.strip_offers(SimpleAnswer(text_, question), Profile(escalation_allowed=False))
        assert out.question is None
        assert out.text == f"Couldn't find ZZT.\n\n{REFER_TO_SALESMAN}"

    def test_a_roster_keeps_its_products_and_loses_its_members(self) -> None:
        question = pending_ask(
            "product_pick",
            [
                {"position": 1, "label": "ZZT-A", "entity_type": "product", "uuid": str(uuid.uuid4())},
                {"position": 2, "label": "Alice", "entity_type": "member"},
            ],
            team="customer_service",
            payload={"escalate_offered": True},
        )
        out = escalation_control.strip_offers(SimpleAnswer("Did you mean:\n1. ZZT-A", question), Profile(escalation_allowed=False))
        assert [o["label"] for o in out.question.options] == ["ZZT-A"]
        assert "escalate_offered" not in out.question.payload and out.question.team is None

    def test_a_reply_with_no_offer_is_untouched(self) -> None:
        answer = SimpleAnswer("Here is the stock.")
        assert escalation_control.strip_offers(answer, Profile(escalation_allowed=False)) is answer


class SimpleAnswer:
    """The `turn/compose.Answer` fields `strip_offers` reads."""

    def __new__(cls, text_: str, question: Any = None):
        from app.services.chatbot.turn.compose import Answer

        return Answer(text=text_, question=question)


# --------------------------------------------------------------------------- #
# Engine turns (R3, R5)
# --------------------------------------------------------------------------- #


class TestDealerIsNeverOfferedAndCannotForce:
    def test_a_miss_offers_no_escalation_and_arms_nothing(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000047")
        _make_dealer(session_factory)
        result = _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access)
        reply = _reply(result)
        assert "escalate" not in reply.lower(), reply
        assert "route to" not in reply.lower(), reply
        # Owner ruling Q2: the miss is said, then the salesman line; never the bare line.
        # The allowed contact's reply is the same with "Would you like me to escalate to
        # purchasing team?" where the salesman line is (measured 30 Sep 2026).
        assert (result.reply or {}).get("text") == (
            "Here's what you want:\n\u2022 product: ZZTSC07\n\nBut no incoming matched these.\n"
            "No incoming and no stock for ZZTSC07.\n\nPlease refer to your salesman."
        )
        oq = _open_question(session_factory)
        assert oq is None or oq.get("kind") not in {"team_pick", "member_offer", "company_pick"}, oq

    def test_asking_for_a_person_gets_the_salesman_line_and_no_hand_off(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000044")
        _make_dealer(session_factory)
        bodies = _capture_next_assignee(monkeypatch)
        sla = _capture_sla(monkeypatch)
        stub_parser(_help_verdict())
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert bodies == [] and sla == [], (bodies, sla)
        assert REFER_TO_SALESMAN in _reply(result), _reply(result)
        assert "routed" not in _reply(result).lower()

    def test_a_stale_yes_to_an_old_offer_hands_nothing_over(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000054")
        _make_dealer(session_factory)
        _write_open_question(
            session_factory,
            open_question={
                "kind": "team_pick",
                "expects": "yes_no",
                "team": "purchasing",
                "asked_at_turn": 1,
                "payload": {"agent": "incoming_stock_enquiries"},
                "options": [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {"team": "purchasing"}}],
            },
        )
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        stub_parser(_yes_verdict())
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert bodies == [], bodies
        assert REFER_TO_SALESMAN in _reply(result), _reply(result)


class TestUnbarredContactsAreUnchanged:
    def test_a_plain_contact_is_still_offered_and_handed_over(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000042")
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access)
        assert (_open_question(session_factory) or {}).get("kind") == "team_pick"
        stub_parser(_yes_verdict())
        result = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)
        assert result.branch_kind == "out_of_scope" and len(bodies) == 1, (result.branch_kind, bodies)

    def test_a_staff_profile_still_asks_for_a_person_and_is_handed_over(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000043")
        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
            {"p": '{"tier": "office"}', "c": str(CONTACT_ID)},
        )
        db.commit()
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        stub_parser(_help_verdict())
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert result.branch_kind == "out_of_scope" and len(bodies) == 1, (result.branch_kind, bodies)

    def test_ticking_the_flag_again_restores_the_offer(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_contact(session_factory, phone="+60900000049")
        _set_flag(session_factory, False)
        _set_flag(session_factory, True)
        result = _incoming_miss(session_factory, monkeypatch, stub_parser, stub_access)
        assert "escalate" in _reply(result).lower(), _reply(result)
        assert (_open_question(session_factory) or {}).get("kind") == "team_pick"


# --------------------------------------------------------------------------- #
# Review B1 / S2: the small-talk fallback's "pass this to the ... team" offer
# --------------------------------------------------------------------------- #

from tests.chatbot.test_memory_s4_fallback_replay import (  # noqa: E402
    CASES_DIR,
    _load_case,
    _run_turn as _fallback_turn,
    _seed_contact as _seed_fallback_contact,
    lane,  # noqa: F401 - fixture by name
    sent_text,
)

_CUSTOMER_OFFER_CASE = CASES_DIR / "06-frustration-offers-the-customers-orders.json"


def _play_fallback(session_factory, stub_access, lane, *, barred: bool, clarifier: dict | None = None):
    case = _load_case(_CUSTOMER_OFFER_CASE)
    pk = _seed_fallback_contact(session_factory, case["given"], level=case["given"]["memory_level"])
    if barred:
        db = session_factory()
        db.execute(text("UPDATE respond_contacts SET escalation_allowed = false WHERE id = :c"), {"c": pk})
        db.commit()
    lane.clarifier_answer = dict(clarifier or case["turn"]["clarifier"])
    result, _prompt = _fallback_turn(
        session_factory,
        stub_access,
        message=case["turn"]["message"],
        verdict_overrides=case["turn"]["verdict"],
        n=20,
        console=False,
    )
    return result


class TestFallbackLane:
    def test_an_unbarred_contact_is_still_offered_the_customer_service_team(
        self, session_factory, stub_access, lane
    ) -> None:
        sent = sent_text(_play_fallback(session_factory, stub_access, lane, barred=False))
        assert "Customer Service team" in sent, sent

    def test_a_barred_contact_is_offered_no_team(self, session_factory, stub_access, lane) -> None:
        sent = sent_text(_play_fallback(session_factory, stub_access, lane, barred=True))
        assert "team" not in sent.lower() and "pass this" not in sent.lower(), sent

    def test_a_clarifier_that_writes_an_escalate_sentence_is_stripped_before_sending(
        self, session_factory, stub_access, lane
    ) -> None:
        """The casual lane builds its send action before the tail, so the backstop runs
        in `_run_casual_lane` itself."""
        result = _play_fallback(
            session_factory,
            stub_access,
            lane,
            barred=True,
            clarifier={"ack": "Sorry about that. Would you like me to escalate to customer service team?", "language": "en"},
        )
        sent = sent_text(result)
        assert "escalate" not in sent.lower(), sent
        assert REFER_TO_SALESMAN in sent, sent


class TestNamedTeamAndTrace:
    def test_a_named_team_escalation_with_a_product_word_gets_the_referral_not_a_roster(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Review S3: `apply`'s named-team rule (no fetch, no roster) holds for the barred
        lane too, so "escalate ZZT... to purchasing team" is answered with the referral."""
        _seed_contact(session_factory, phone="+60900000005")
        _make_dealer(session_factory)
        # Near neighbours, so the business lane has a did-you-mean roster to ask.
        _seed_product(session_factory, code="ZZTNOPE10")
        _seed_product(session_factory, code="ZZTNOPE11")
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        stub_parser(
            verdict(
                message_type="request_for_help",
                domain_hint=None,
                intent_hint=None,
                user_goal="trying to escalate ZZTNOPE1 to the purchasing team",
                entities=[entity("ZZTNOPE1", hint="product", confident=False)],
                routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries"},
                escalation={"is_escalation_confirmation": False, "company_pick": None},
            )
        )
        stub_access()
        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert bodies == [], bodies
        assert (result.reply or {}).get("text") == REFER_TO_SALESMAN, _reply(result)

    def test_the_trace_says_the_escalation_was_withheld(self) -> None:
        """Review S4: a barred turn keeps branch kind `out_of_scope`; the routed line must
        not claim it was routed to a person."""
        from app.services.chatbot import trace as trace_mod

        why = trace_mod.routed_why("out_of_scope", {}, True, lane="escalation_barred")
        assert why.startswith("Escalation withheld"), why
        assert trace_mod.routed_why("out_of_scope", {}, True).startswith("Routed to escalation")


def test_apply_plans_a_barred_named_team_escalation_with_no_kind_menu_and_no_fetch() -> None:
    """Review S3 at `apply()`: round 5's named-team rule (`test_escalation_round5_escalation_
    words_win.py::TestApplyPlansTheEscalationOnly`) holds for a barred contact, so the
    turn reaches the referral instead of a kind menu over "water closet"."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Focus, State
    from app.services.chatbot.turn_runtime import with_named_team_escalation
    from tests.chatbot._turn_helpers import build_policy
    from tests.chatbot.test_escalation_round5_escalation_words_win import _fixed_code_verdict

    state = State(focus=Focus(), pending=None, profile=Profile(escalation_allowed=False), turn_no=3)
    _state, plan = apply(
        state,
        with_named_team_escalation(_fixed_code_verdict()),
        build_policy(),
        {"water closet": {"promotion": 1, "attachment_type": 1}},
    )
    assert plan.ask is None, plan.ask
    assert plan.fetch == []
    assert plan.trace.lane == "escalation_barred"


def test_apply_closes_an_open_escalation_offer_on_entry_for_a_barred_contact() -> None:
    """Kill-matrix K6: the entry close is its own guard (the `_lane` bar and the engine
    guard also stop a stale "yes", so the engine test alone cannot see it)."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Focus, State
    from tests.chatbot._turn_helpers import build_policy

    offer = pending_ask(
        "team_pick",
        [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {"team": "purchasing"}}],
        team="purchasing",
        asked_at_turn=2,
    )
    state = State(focus=Focus(), pending=offer, profile=Profile(escalation_allowed=False), turn_no=3)
    new_state, plan = apply(state, verdict(message_type="casual", entities=[]), build_policy())
    assert "escalation_barred_offer_closed" in plan.trace.rules_fired, plan.trace.rules_fired
    assert new_state.pending is None


class TestAmbiguousContact:
    """Security review S2 (second round): one respond.io id on two rows in the workspace is
    one person. Stock is denied (today's rule) and so is the hand-off when ANY row is
    unticked; only when every row is ticked is the contact allowed."""

    def _two_rows(self, session_factory, *, second_barred: bool) -> None:
        _seed_contact(session_factory, phone="+60900000053")
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id, "
                "escalation_allowed) SELECT gen_random_uuid()::text, respond_io_id, '+60000009402', "
                "'{}'::jsonb, workspace_id, :second FROM respond_contacts WHERE phone_number = '+60900000053'"
            ),
            {"second": not second_barred},
        )
        db.execute(text("UPDATE respond_contacts SET escalation_allowed = false WHERE phone_number = '+60900000053'"))
        db.commit()

    def test_every_row_barred_is_barred(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        self._two_rows(session_factory, second_barred=True)
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.stock_allowed is False and profile.escalation_allowed is False

    def test_one_unticked_row_blocks(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        self._two_rows(session_factory, second_barred=False)
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.stock_allowed is False and profile.escalation_allowed is False

    def test_one_unticked_row_of_three_blocks_whichever_two_the_check_reads(self, session_factory) -> None:
        """Review nit: the ambiguity check reads two rows (`LIMIT 2`); the block reads all."""
        from app.services.chatbot.turn_runtime import load_profile

        self._two_rows(session_factory, second_barred=False)
        db = session_factory()
        db.execute(text("UPDATE respond_contacts SET escalation_allowed = true WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)})
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id, "
                "escalation_allowed) SELECT gen_random_uuid()::text, respond_io_id, '+60000009403', "
                "'{}'::jsonb, workspace_id, false FROM respond_contacts WHERE phone_number = '+60900000053'"
            )
        )
        db.commit()
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.escalation_allowed is False

    def test_every_row_ticked_is_allowed(self, session_factory) -> None:
        from app.services.chatbot.turn_runtime import load_profile

        self._two_rows(session_factory, second_barred=False)
        db = session_factory()
        db.execute(text("UPDATE respond_contacts SET escalation_allowed = true WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)})
        db.commit()
        profile, _ = load_profile(session_factory(), str(CONTACT_ID))
        assert profile.escalation_allowed is True



@pytest.mark.parametrize(
    "shape",
    [
        {"message_type": "request_for_help"},
        {"message_type": "escalation"},
        {"escalation": {"is_escalation_confirmation": True}},
    ],
)
def test_lane_blocks_every_forced_door_for_a_barred_contact(shape) -> None:
    """R3 at `_lane` itself (kill-matrix K8): the three doors into the escalation lane
    all land on "escalation_barred"; an unbarred contact still gets "escalation"."""
    from app.services.chatbot.turn.apply import _lane
    from tests.chatbot._turn_helpers import build_policy

    v = verdict(entities=[], domain_hint=None, intent_hint=None, **shape)
    assert _lane(v, [], build_policy(), barred=True) == "escalation_barred"
    assert _lane(v, [], build_policy()) == "escalation"


# --------------------------------------------------------------------------- #
# Owner ruling Q2: a blocked miss says what could not be found, then refers
# --------------------------------------------------------------------------- #

_MISS_SHAPES = {
    # What the owner reads on the console, exactly (measured before the change: the same
    # text with "Would you like me to escalate to ... team?" where the salesman line is).
    "order number": (
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            entities=[entity("SO999001", hint="order", confident=True)],
            routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
        ),
        'Couldn\'t find: "SO999001" (order). Please refer to your salesman.',
    ),
    "product code": (
        verdict(
            domain_hint="master_products",
            intent_hint="check_product",
            entities=[entity("ZZTNOPE9", hint="product", confident=True)],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        ),
        'Couldn\'t find: "ZZTNOPE9" (product). Please refer to your salesman.',
    ),
    "product near a real code": (
        verdict(
            domain_hint="master_products",
            intent_hint="check_product",
            entities=[entity("ZZTSC0", hint="product", confident=False)],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        ),
        "Here's what you want:\n\u2022 product: ZZTSC07\n\nBut no master products matched these. "
        "Please refer to your salesman.",
    ),
}


@pytest.mark.parametrize("shape", list(_MISS_SHAPES))
def test_a_blocked_miss_names_what_was_asked_then_refers_to_the_salesman(
    shape, session_factory, stub_parser, stub_access, monkeypatch
) -> None:
    _seed_contact(session_factory, phone="+60900000048")
    _seed_product(session_factory, code="ZZTSC07")
    _set_flag(session_factory, False)
    _stub_incoming_probe_empty(monkeypatch)
    v, expected = _MISS_SHAPES[shape]
    stub_parser(v)
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert (result.reply or {}).get("text") == expected
    oq = _open_question(session_factory)
    assert oq is None or oq.get("kind") not in {"team_pick", "member_offer", "company_pick"}, oq


@pytest.mark.parametrize("shape", list(_MISS_SHAPES))
def test_an_allowed_miss_still_offers_the_team(
    shape, session_factory, stub_parser, stub_access, monkeypatch
) -> None:
    _seed_contact(session_factory, phone="+60900000050")
    _seed_product(session_factory, code="ZZTSC07")
    _stub_incoming_probe_empty(monkeypatch)
    v, expected = _MISS_SHAPES[shape]
    stub_parser(v)
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    text_ = (result.reply or {}).get("text") or ""
    assert "Would you like me to escalate to" in text_ and REFER_TO_SALESMAN not in text_, text_



@pytest.mark.parametrize(
    "kwargs, expected_tail",
    [
        ({"missing": "pink (finish)"}, "Couldn't find: pink (finish). Please refer to your salesman."),
        ({"leg": "master products"}, "But no master products matched these. Please refer to your salesman."),
    ],
)
def test_the_what_you_want_reply_ends_with_the_salesman_line_for_a_blocked_contact(kwargs, expected_tail) -> None:
    """Kill-matrix K15: the attribute / predicate miss replies (`near_miss_reply`,
    `unknown_values_reply`, `described_members_reply`) all end in `what_you_want_reply`, and
    a blocked contact's caller passes the salesman line where the team would be."""
    from app.services.chatbot.lanes.business.answer import what_you_want_reply

    lines = ["• finish: gunmetal"]
    blocked = what_you_want_reply("basin taps", lines, team=REFER_TO_SALESMAN, **kwargs)
    assert blocked.endswith(expected_tail), blocked
    assert "escalate" not in blocked
    allowed = what_you_want_reply("basin taps", lines, team="customer service", **kwargs)
    assert allowed.endswith("Would you like me to escalate to customer service team?"), allowed


def test_the_composer_miss_ends_with_the_salesman_line_for_a_blocked_contact() -> None:
    """Review (per-contact round) should-fix 1: `turn/compose.py`'s own offer arm. The
    barred analogue of the S11 staff test, with the exact text (measured by the reviewer)."""
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.state import Focus, State
    from tests.chatbot.test_samantha_26sep_s11_escalation_audience import _policy, _total_miss_envelope

    state = State(focus=Focus(), pending=None, profile=Profile(escalation_allowed=False), turn_no=11)
    answer = compose([_total_miss_envelope()], state, _policy(), ctx=None)
    assert answer.offer is None
    assert answer.text == "*inventory* for M210-GM:\n\nPlease refer to your salesman."

    allowed = compose(
        [_total_miss_envelope()], State(focus=Focus(), pending=None, profile=Profile(), turn_no=11), _policy(), ctx=None
    )
    assert allowed.text.endswith("Would you like me to escalate to warehouse team?"), allowed.text


def test_the_silent_company_offer_is_not_made_to_a_blocked_contact() -> None:
    """Review nit: `answer_bridge.apply_silent_company_offer`'s barred early return, on the
    envelope AC-1803 uses to prove the offer IS made to an allowed contact."""
    from app.services.chatbot import answer_bridge as bridge
    from app.services.chatbot.turn.compose import Answer

    envelope = {
        "figures": [{"fields": [{"label": "Product", "value": "SRTSC07"}]}],
        "denied": False,
        "raw_fragment": {
            "fetch": {
                "lookup_companies": [{"id": "zzt-co-1", "name": "Sorento"}, {"id": "zzt-co-2", "name": "Mocha"}],
                "answers": [{"fields": [{"key": "company_name", "label": "Company", "value": "Sorento"}]}],
            }
        },
    }
    parser = {"routing": {"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"}}
    kwargs = dict(envelope=envelope, parser=parser, gate={}, asked_at_turn=1, turn_id="zzt-turn-1")
    answer = Answer(text="Found it in Sorento.", question=None)

    allowed = bridge.apply_silent_company_offer(answer, profile=Profile(), **kwargs)
    assert allowed.question is not None and allowed.question.kind == "team_pick"
    blocked = bridge.apply_silent_company_offer(answer, profile=Profile(escalation_allowed=False), **kwargs)
    assert blocked is answer
