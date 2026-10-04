"""Issue #1323: an escalation offer is accepted only on the parser's SEMANTIC verdict.

Production, 28 Sep 2026 09:24 MYT, turn 9d9c417d-5c51-424a-b345-05da1b120c04 (n8n
execution 18185302). The session held the bot's turn-4 offer, a `team_pick` pending
with `expects: yes_no` and one option "Yes" ("Would you like me to escalate to
warehouse team?"). The contact sent a photo of ten product codes captioned "Check
stocks level". The parser read the codes and returned `is_affirmative: true` and
`open_question_answer.mode: "yes"`, while its dedicated escalation field,
`escalation.is_escalation_confirmation`, came back `false`, with ten fresh product
entities, `domain_in_message: true` and `intent_hint: check_stock`. `decide()`
accepted the offer on `is_affirmative` alone and the stock ask was handed to the
warehouse team (`out_of_scope`) with no stock looked up.

Owner ruling (verbatim, 28 Sep 2026): "I don't want us to do fuzzy search on the "yes"
or "no" in the message, the chatbot should be able to undersatnd whether the user means
escalation right? like, semantically" / "okay please fix that for the esclation
cofirmation thingy".

Sections: A `decide()`, B `apply()`, C `engine.run_turn`, D the prompt contract,
E the publish migration.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.decide import ANSWER, NEW_ASK, decide
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import (
    _capture_next_assignee,
    _capture_sla,
    _seed_contact,
    _seed_product,
    _write_open_question,
)

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

#: The ten codes the bot read off the photo (turn 9d9c417d).
PHOTO_CODES = [
    "CB110",
    "CB114",
    "CB112",
    "CB01",
    "CB07",
    "CB2320",
    "CB2340",
    "CB1198",
    "CB1198-DIY",
    "CBST15-SS",
]

#: The turn-4 offer as the session held it: one team, a yes/no question.
OFFER_WIRE: dict[str, Any] = {
    "kind": "team_pick",
    "expects": "yes_no",
    "team": "warehouse",
    "asked_at_turn": 4,
    "payload": {"agent": "general_enquiries"},
    "options": [
        {
            "position": 1,
            "label": "Yes",
            "entity_type": "team",
            "payload": {"team": "warehouse", "agent": "general_enquiries"},
        }
    ],
}


def _offer():
    return pending_ask(
        OFFER_WIRE["kind"],
        [dict(o) for o in OFFER_WIRE["options"]],
        team=OFFER_WIRE["team"],
        asked_at_turn=OFFER_WIRE["asked_at_turn"],
        expects=OFFER_WIRE["expects"],
        payload=dict(OFFER_WIRE["payload"]),
    )


def _recorded_verdict(**overrides: Any) -> dict[str, Any]:
    """The parser's verdict for "Check stocks level" plus the photo, as recorded."""
    base = verdict(
        message_type="business_query",
        intent_hint="check_stock",
        domain_hint="inventory",
        is_affirmative=True,
        domain_in_message=True,
        entities=[entity(code, hint="product") for code in PHOTO_CODES],
        open_question_answer={"mode": "yes", "picked": [], "items": [], "qty_for_all": None},
        escalation={
            "is_escalation_confirmation": False,
            "escalation_declined": None,
            "company_pick": None,
        },
        routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
    )
    base.update(overrides)
    return base


def _confirmation_verdict(**overrides: Any) -> dict[str, Any]:
    """A reply the parser read as agreeing to the handover ("yes", "ok escalate")."""
    base = verdict(
        message_type="casual",
        is_affirmative=True,
        entities=[],
        open_question_answer={"mode": "yes", "picked": [], "items": [], "qty_for_all": None},
        escalation={
            "is_escalation_confirmation": True,
            "escalation_declined": None,
            "company_pick": None,
        },
        routing={"suggested_team": None, "suggested_agent": None},
    )
    base.update(overrides)
    return base


def _state(pending=None) -> State:
    return State(focus=Focus(domains=["inventory"]), pending=pending, profile=Profile())


# =============================================================================== #
# A. decide(): the escalation offer's acceptance arm reads the semantic verdict only
# =============================================================================== #


class TestDecide:
    def test_the_recorded_verdict_is_a_new_ask_not_an_acceptance(self) -> None:
        d = decide(_recorded_verdict(), Focus(domains=["inventory"]), _offer())
        assert d.kind != ANSWER, d
        assert d.kind == NEW_ASK, d
        assert [e["canonical_code"] for e in d.entities] == PHOTO_CODES

    def test_the_semantic_confirmation_accepts_the_offer(self) -> None:
        d = decide(_confirmation_verdict(), Focus(domains=["inventory"]), _offer())
        assert d.kind == ANSWER, d

    def test_is_affirmative_alone_never_accepts_an_escalation_offer(self) -> None:
        v = _confirmation_verdict(
            escalation={"is_escalation_confirmation": False, "escalation_declined": None, "company_pick": None}
        )
        for kind in ("team_pick", "member_offer", "company_pick"):
            offer = pending_ask(kind, [], team="warehouse", expects="yes_no")
            d = decide(v, Focus(), offer)
            assert d.kind != ANSWER, (kind, d)

    def test_a_null_confirmation_does_not_accept_either(self) -> None:
        v = _confirmation_verdict(
            escalation={"is_escalation_confirmation": None, "escalation_declined": None, "company_pick": None}
        )
        assert decide(v, Focus(), _offer()).kind != ANSWER

    def test_a_decline_still_outranks_the_confirmation(self) -> None:
        v = _confirmation_verdict(
            escalation={"is_escalation_confirmation": True, "escalation_declined": True, "company_pick": None}
        )
        assert decide(v, Focus(), _offer()).kind != ANSWER

    def test_a_non_escalation_pending_keeps_reading_is_affirmative(self) -> None:
        """R2 is scoped to escalation offers: a yes over any other open question
        answers it exactly as before."""
        v = _confirmation_verdict(
            escalation={"is_escalation_confirmation": None, "escalation_declined": None, "company_pick": None}
        )
        offer = pending_ask(
            "product_pick",
            [{"position": 1, "label": "ELP3754", "entity_type": "product"}],
            expects="yes_no",
        )
        assert decide(v, Focus(), offer).kind == ANSWER


# =============================================================================== #
# B. apply(): the stock ask runs; the offer is not consumed
# =============================================================================== #


class TestApply:
    def test_the_recorded_turn_plans_the_stock_ask_for_the_ten_codes(self) -> None:
        new_state, plan = apply(_state(_offer()), _recorded_verdict(), build_policy())
        assert plan.trace.lane != "escalation", plan.trace.rules_fired
        assert "answer_pending_accept" not in plan.trace.rules_fired
        # The offer is not an answer to this message: the question stays open at
        # the pending arm, exactly as for any aside.
        assert "answer_pending_not_an_answer" in plan.trace.rules_fired
        assert plan.trace.decision == {"kind": NEW_ASK, "why": "domain_in_message"}
        assert plan.domains == ["inventory"], plan.domains
        carried = [e.get("canonical_code") for e in new_state.focus.products]
        assert carried == PHOTO_CODES, carried

    def test_the_offer_is_left_to_the_existing_stale_offer_rule(self) -> None:
        """#1323 changes WHETHER the message accepts the offer, nothing after it. The
        stock ask fetches, so the pre-existing `new_ask_closes_stale_roster` rule (a
        new ask that got its own answer closes an offer about something else; measured
        17 Sep 2026, a bare "1" under a later list handed a conversation to purchasing)
        still decides the offer's survival, and the stock answer re-offers the handover
        itself when it misses (section C)."""
        new_state, plan = apply(_state(_offer()), _recorded_verdict(), build_policy())
        assert plan.fetch, plan
        assert "new_ask_closes_stale_roster" in plan.trace.rules_fired
        assert new_state.pending is None

    def test_the_semantic_confirmation_hands_over_to_the_offered_team(self) -> None:
        _new, plan = apply(_state(_offer()), _confirmation_verdict(), build_policy())
        assert plan.trace.lane == "escalation", plan.trace.rules_fired
        assert plan.trace.team == "warehouse"
        assert plan.trace.decision == {"kind": ANSWER, "why": "escalation_confirmation"}


# =============================================================================== #
# C. engine.run_turn: out_of_scope on main, the stock ask on this head
# =============================================================================== #


class TestRunTurn:
    def _plant(self, session_factory, monkeypatch) -> list[dict[str, Any]]:
        _seed_contact(session_factory, phone="+60900000041")
        bodies = _capture_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        _write_open_question(session_factory, open_question=dict(OFFER_WIRE))
        return bodies

    def _stock_tool(self, monkeypatch) -> list[tuple[str, dict[str, Any]]]:
        """Every MCP call, recorded; each answers "nothing" (no network)."""
        from app.services.ai_assistant_service import MCPRuntimeClient

        calls: list[tuple[str, dict[str, Any]]] = []

        def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
            calls.append((name, dict(arguments)))
            return json.dumps({"answers": []})

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
        return calls

    def test_the_recorded_turn_runs_the_stock_ask_and_is_not_handed_to_a_human(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        bodies = self._plant(session_factory, monkeypatch)
        for code in PHOTO_CODES:
            _seed_product(session_factory, code=code)
        calls = self._stock_tool(monkeypatch)
        stub_parser(_recorded_verdict())
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind != "out_of_scope", result.branch_kind
        assert bodies == [], "no assignee may be drawn for a stock ask"
        stock = [args for name, args in calls if name == "crm_inventory_stock_balance_list"]
        assert stock and stock[0]["product_ids"], calls

    def test_unresolved_codes_ask_nothing_of_a_human_and_leave_the_warehouse_offer_open(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The same verdict with the codes not in the catalogue: the stock ask runs,
        misses, and the turn ends with the warehouse offer open for the customer to
        answer, never with a handover nobody agreed to."""
        from sqlalchemy import text

        bodies = self._plant(session_factory, monkeypatch)
        stub_parser(_recorded_verdict())
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind != "out_of_scope", result.branch_kind
        assert bodies == [], "no assignee may be drawn for a stock ask"
        row = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).first()
        open_question = (row.session_vars or {}).get("open_question")
        assert open_question is not None and open_question.get("kind") == "team_pick", open_question
        assert open_question.get("team") == "warehouse", open_question

    def test_the_semantic_confirmation_hands_over_to_the_offered_team(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        bodies = self._plant(session_factory, monkeypatch)
        stub_parser(_confirmation_verdict())
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "out_of_scope", result.branch_kind
        assert len(bodies) == 1, bodies
        assert bodies[0]["team_code"] == "warehouse", bodies[0]


# =============================================================================== #
# D. The prompt: one semantic verdict, published as an addendum
# =============================================================================== #


class TestPrompt:
    def _addendum(self) -> str:
        from app.services import chatbot_parser_prompt as prompt

        return prompt.ESCALATION_CONFIRMATION_ADDENDUM

    def test_the_addendum_is_the_tail_of_the_prompt(self) -> None:
        """AC-MEM071 keeps MEMORY_ADDENDUM (ending in CURRENT DATE) last for the byte-stable prefix, so only it may follow this addendum."""
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            MEMORY_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            SEMANTIC_PARSER_PROMPT,
        )

        addendum = self._addendum()
        assert SEMANTIC_PARSER_PROMPT.count(addendum) == 1
        _, tail = SEMANTIC_PARSER_PROMPT.split(addendum)
        # PLAN-po-spo-warehouse-29sep S3: PO_SPO_WAREHOUSE_ADDENDUM now sits between the two.
        assert tail == PO_SPO_WAREHOUSE_ADDENDUM + ACCOUNT_LEDGER_ADDENDUM + MEMORY_ADDENDUM
        assert "CURRENT DATE: {{current_date}}" in MEMORY_ADDENDUM.rsplit("CURRENT DATE\n", 1)[1]

    def test_it_defines_the_one_verdict_for_every_offer_shape(self) -> None:
        text = self._addendum()
        assert "is_escalation_confirmation" in text
        # The three offer shapes the bot asks.
        assert "Would you like me to escalate to" in text
        assert "numbered" in text and "team" in text
        assert "company" in text.lower()
        # Any language, wording or short form; judged by meaning.
        assert "any language" in text
        assert "meaning" in text

    def test_it_is_false_when_the_message_brings_its_own_question(self) -> None:
        text = self._addendum()
        assert "Check stocks level" in text
        assert "false" in text
        assert "stays open" in text or "remains open" in text

    def test_it_keeps_the_company_name_reply_rule(self) -> None:
        text = self._addendum()
        assert "COMPANY-NAME REPLY" in text
        assert "company_pick" in text

    def test_it_separates_is_affirmative_from_the_escalation_verdict(self) -> None:
        text = self._addendum()
        assert "is_affirmative" in text
        assert "AFFIRMATION" in text
        assert "NOT the escalation verdict" in text

    def test_no_em_or_en_dashes(self) -> None:
        text = self._addendum()
        assert EM_DASH not in text and EN_DASH not in text


# =============================================================================== #
# E. The publish migration: next version, label unmoved, idempotent
# =============================================================================== #


_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "chatbot_esc_confirm_1323.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("chatbot_esc_confirm_1323", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_chains_onto_a_committed_revision_with_a_short_id() -> None:
    module = _load_migration()
    assert module.revision == "chatbot_esc_confirm_1323"
    assert len(module.revision) <= 32
    parents = [
        path
        for path in _MIGRATION.parent.glob("*.py")
        if f'\nrevision = "{module.down_revision}"' in path.read_text()
    ]
    assert len(parents) == 1


def test_the_migration_publishes_the_next_version_unlabelled_and_is_idempotent() -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from tests._pg_fixture import blank_session

    module = _load_migration()
    with blank_session() as db:
        bind = db.connection()
        module.publish(bind)
        rows = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").all()
        carrying = [r for r in rows if (r.config_json or {}).get("chatbot_esc_confirm_1323")]
        assert len(carrying) == 1
        assert carrying[0].version == max(r.version for r in rows)
        assert "NOT the escalation verdict" in carrying[0].template
        labelled = {
            row.version_id
            for row in db.query(AIPromptLabel).filter(AIPromptLabel.name == "chatbot_semantic_parser")
        }
        assert carrying[0].id not in labelled
        module.publish(bind)
        again = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").count()
        assert again == len(rows)
