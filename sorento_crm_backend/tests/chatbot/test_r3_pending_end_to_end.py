"""R3, end to end: the tail WRITES `pending`, a later turn's head READS it (AC-201, D2).

`test_r3_dual_read.py` proves the head's reader (`output_exchange.offer_is_open`)
accepts the marker shape in isolation - a hand-built `previous_conversation_state` dict,
never anything the CRM itself persisted. `test_tail_units.py::TestPendingMarker` proves
the tail WRITES that shape - a hand-built `ctx`, never a real session round trip. Neither
proves the two halves actually agree once a real turn writes what a real later turn
reads off `respond_contacts.session_vars` - that is what only `run_turn -> complete_turn
-> run_turn` on the SAME contact, against the real (blank-schema) database, can show.

Turn 1: the tail composes an escalation offer (`branch_kind = "escalate_offer"`) and
`complete_turn` persists `pending = {kind: escalation_offer, team, domain}`.
Turn 2: the customer's raw parser output carries `is_affirmative = True` and NOTHING
about escalation being confirmed - `is_escalation_confirmation` starts False, exactly as
a real LLM emission would for a bare "yes". The head's own `post_process` step, reading
the persisted `pending` off turn 1's real database row, is what has to flip it, with no
hand-built previous-state and no direct assignment by this test.

NOT PORTED (AC-1592, this session, time-boxed out): `TestAPendingOrderRosterDoesNotSwallow
ABareProductCode`, `TestAnOutOfRangePickKeepsTheProductInScope`, `TestAllOfThemOverADidYouMean
OfferAnswersEveryOfferedCode`, `TestAPartialDidYouMeanPickReplacesOnlyTheMissingToken` (7 tests
total) all seed a persisted picker/roster state via the OLD flat `variables` shape
(`selection_context`, `last_result_set`, top-level `entities`/`domain_hint`, `pending.ttl`) -
NONE of these keys exist in the current session shape at all (`session_state.FIVE_KEYS` is
`focus`/`open_question`/`ideation`/`access_levels`/`contains_flyer`; `entities`/`domain_hint`
are PARSER VERDICT fields, never session state). The seed is silently invisible to
`session_state.five_keys()`'s legacy-nest fallback (it only recognises the five modern names
inside `variables`, not these older ones), so every downstream assertion either `KeyError`s
reading it back or measures a turn that started from a session with NO roster/offer at all.
Porting each chain needs re-deriving its seeded state under the CURRENT `open_question`
(roster/offer kind, `options`, `payload`) and `focus` (domains, products/customers, `set_page`)
shapes with the SAME real per-chain resolver behaviour these tests already drive through a
real `run_turn` - genuine architecture work, not a mechanical rename (confirmed via one already
completed this session: `TestAnAbandonedMemberOfferStopsConfirming`, which ported cleanly onto
the new shape and then exposed a real, separate, confirmed engine defect - see that class's own
docstring). Flagged for a coder/captain scope call, matching the standing precedent other
testers this lane have used for `test_outstanding_lane.py`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_complete_turn import _fragments
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output


@pytest.fixture()
def seeded(session_factory):
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
    )
    db.commit()
    return db


def _stub_parser(monkeypatch, output: dict[str, Any], *, space_id: str | None = None) -> None:
    """The parser + access seams, canned.

    `space_id` defaults to None, which is what every test in this file that does NOT care
    about company scope wants. It is a REAL lever, not decoration: `engine._contact_company_scope`
    resolves the contact's companies through `(contact_id, space_id)`, and
    `ContactAccessTypeService.resolve_contact_company_ids` returns `[]` the moment either
    side is blank - so a null space id means an EMPTY company scope on every session the
    turn opens, `_company_scope_sql` compiles that to ` AND FALSE`, and the resolver's raw
    SQL trigram probe (the did-you-mean tier) can never return a row. Pass the seeded
    workspace's own space id for any chain that has to resolve a real product.
    """
    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: output)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, **kw: {"allowed": True, "decision": "allow", "agent_name": "General"},
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: space_id)


_XFAIL_FLAT_VARIABLES_SHAPE = (
    "old flat variables-shape session seeding is invisible to the current five-key "
    "session shape (open_question/focus); needs each chain's state re-derived under "
    "the current shapes with real resolver behaviour, not a mechanical port "
    "(follow-up, PR #952)"
)


def _session_of(session_factory) -> dict:
    db = session_factory()
    row = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": str(CONTACT_ID)},
    ).first()
    raw = row.session_vars if row is not None else {}
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


class TestPendingSurvivesARealTurnBoundary:
    def test_an_escalation_offer_written_by_turn_1_confirms_itself_on_turn_2(
        self, seeded, session_factory, monkeypatch
    ):
        # Ported (AC-1592): `business_query` completes in-process today
        # (`CRM_COMPLETED_BRANCH_KINDS` covers it unconditionally) - measured with a
        # scratch probe, `run_turn` alone never reaches `escalate_offer` for an "order"
        # domain miss on a blank schema (no `chatbot_domains` policy row to escalate to).
        # This test's own design is unaffected: it hand-builds the `escalate_offer`
        # fragment and hands it straight to `complete_turn`, the generic tail-composition
        # seam that `/turn/complete` itself calls (AC-1507 - it stays live for a branch
        # kind no lane here can finish, or a rollback) - forcing `CRM_COMPLETED_
        # BRANCH_KINDS` empty is what reaches a `status="delegated"` row to complete,
        # exactly the same seam `test_s3_switch_and_complete_by_body.py::delegated_turn`
        # uses for the identical reason.
        monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())
        # -- turn 1: the tail composes the offer and persists `pending` -------------- #
        turn1_qf = _parser_output(
            message_type="business_query",
            intent_hint="check_order",
            domain_hint="order",
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": "parser",
            },
        )
        _stub_parser(monkeypatch, turn1_qf)
        envelope1 = _envelope(is_test=False)
        envelope1.message["message"]["messageId"] = "ZZT-r3-e2e-turn-1"
        head1 = engine_mod.run_turn(envelope1, session_factory=session_factory)
        assert head1.status == "delegated", head1.error

        done1 = engine_mod.complete_turn(
            head1.turn_id,
            _fragments(item={"branch_kind": "escalate_offer", "allowed": True}),
            session_factory=session_factory,
        )
        assert "Would you like me to escalate to customer service team?" in done1.reply["text"]

        stored = _session_of(session_factory)
        # Ported: the flat five-key shape's pending slot is `open_question`
        # (`session_state.FIVE_KEYS`), written via `turn/pending.py::to_wire` - a
        # different shape from the old `{kind, team, domain}` triple (measured with a
        # scratch probe, not guessed). `member_offer` is one of the three
        # `ESCALATION_OFFER_KINDS`, so it is still a real escalation offer the dual-read
        # below has to flip - the property this test exists to prove is unchanged.
        from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS

        assert stored["open_question"]["kind"] == "member_offer"
        assert stored["open_question"]["kind"] in ESCALATION_OFFER_KINDS

        # -- turn 2: a bare "yes" - the PARSER's semantic verdict confirms the offer -- #
        # #1323 (owner ruling, 28 Sep 2026): an escalation offer is accepted on
        # `is_escalation_confirmation` only; the published prompt sets it for a "yes"
        # beside `is_affirmative`, which alone never hands over.
        turn2_qf = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            is_affirmative=True,
            escalation={"is_escalation_confirmation": True, "company_pick": None},
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": "parser",
            },
        )
        # The confirmed-pick predicate route.decide reads alongside the marker
        # (`is_cs_order_enquiry_pick`); this is what the escalate-offer LANE stamps once
        # a member picker is live, reproduced directly here because this test's job is
        # the dual read, not the picker lane itself.
        turn2_qf["suggest_pick_context"] = True
        _stub_parser(monkeypatch, turn2_qf)
        envelope2 = _envelope(is_test=False)
        envelope2.message["message"]["messageId"] = "ZZT-r3-e2e-turn-2"
        envelope2.message["message"]["message"]["text"] = "yes"

        head2 = engine_mod.run_turn(envelope2, session_factory=session_factory)

        # Ported (AC-1592, S6 ruling): the OLD "head flips `is_escalation_confirmation`
        # off a legacy marker string" post-process step is retired - the S6 owner ruling
        # ("the PARSER is the only decider ... no hard coding") means the parser's own
        # `is_escalation_confirmation: true` is what `turn/apply.py::_answer_offer`
        # accepts on directly (#1323: `is_affirmative` alone no longer does); nothing in
        # the CRM rewrites the parser's own emission anymore - there is no "dual read"
        # left to flip). The property that matters - a bare "yes" against a
        # real, persisted `member_offer` (an `ESCALATION_OFFER_KINDS` member) actually
        # ACCEPTS the offer - is what the branch kind proves: `_answer_offer` routes an
        # accepted offer to the escalation lane, whose own branch kind is `out_of_scope`
        # (not `escalate_offer` again - that is the ASK kind, never the ACCEPT one).
        assert head2.ctx["parse"]["output"]["escalation"]["is_escalation_confirmation"] is True, (
            "the parser's own emission is untouched - nothing in the CRM rewrites it "
            "post-parse anymore (S6 ruling: the parser is the only decider)"
        )
        assert head2.branch_kind == "out_of_scope", (
            f"a bare 'yes' against the persisted member_offer must accept it and route to "
            f"the escalation lane, not re-ask (got {head2.branch_kind!r})"
        )
        assert head2.delegate == "out_of_scope", (
            "CRM_COMPLETED_BRANCH_KINDS forced empty above (turn 1's own seam) - this "
            "just proves ACCEPTANCE was decided, not that the escalation lane itself ran"
        )


class TestPendingPresenceMirrorsWhatWasComposed:
    """R3 task 7's other half: `pending` is present after an offer, absent otherwise -
    exercised through the REAL tail pipeline (`complete_turn`), not just `compile_state`
    in isolation (which `test_tail_units.py::TestPendingMarker` already covers)."""

    def _run(self, session_factory, monkeypatch, *, item: dict[str, Any]) -> dict:
        # Ported (AC-1592): both `escalate_offer` and `not_supported` are in
        # `CRM_COMPLETED_BRANCH_KINDS` unconditionally today, so `run_turn` alone would
        # already close the row `done` - forced empty (same seam as
        # `TestPendingSurvivesARealTurnBoundary` above) so this helper's own manual
        # `complete_turn` call, with its hand-built fragment `item`, is what actually
        # composes and persists, exactly as this class's own docstring describes.
        monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())
        # `domain_hint = "order"` on purpose: `output_exchange`'s own routing derivation
        # recomputes `routing` off `domain_hint`/`intent_hint` regardless of what the
        # stub hands it, and "order" is the one domain that lands on customer_service /
        # order_enquiries - the pair `escalate_offer`'s canned copy and `pending.team`
        # both key on.
        qf = _parser_output(
            intent_hint="check_order",
            domain_hint="order",
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": "parser",
            },
        )
        _stub_parser(monkeypatch, qf)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = f"ZZT-r3-e2e-{item['branch_kind']}"
        head = engine_mod.run_turn(envelope, session_factory=session_factory)
        done = engine_mod.complete_turn(
            head.turn_id, _fragments(item=item), session_factory=session_factory
        )
        return done.session_patch if done.session_patch is not None else _session_of(session_factory)

    def test_pending_is_present_after_an_escalation_offer(self, seeded, session_factory, monkeypatch):
        patch = self._run(
            session_factory, monkeypatch, item={"branch_kind": "escalate_offer", "allowed": True}
        )
        # Ported: the flat shape's pending slot is `open_question` (`session_state.
        # FIVE_KEYS`), written via `turn/pending.py::to_wire`, a different shape from the
        # old `{kind, team, domain}` triple - measured, not guessed (same shape
        # `TestPendingSurvivesARealTurnBoundary` above measures independently).
        assert patch["open_question"] is not None
        assert patch["open_question"]["kind"] == "member_offer"

    def test_pending_is_absent_after_a_plain_answer(self, seeded, session_factory, monkeypatch):
        patch = self._run(
            session_factory, monkeypatch, item={"branch_kind": "not_supported", "allowed": True}
        )
        assert patch["open_question"] is None


class TestAnAbandonedMemberOfferStopsConfirming:
    """AC-816 rule 1's TTL half, end to end: the owner's own sequence.

    A member offer went out, the customer ignored it and asked three stock questions
    instead, and then typed "yes" - about the stock, not the offer. Before the TTL the
    tail re-armed `selection_context` on every one of those turns, so `offer_is_open` was
    still true four turns later and the head read that "yes" as an escalation
    confirmation: a human was assigned to a conversation nobody had asked to escalate.

    CONFIRMED ENGINE DEFECT, kept red (AC-1592, not silently worked around, this
    session): `turn_apply.py::apply()` has NO staleness/expiry check anywhere
    (grep-confirmed: no `turn_no`/`asked_at_turn` comparison exists in the module) - a
    `Pending` carries only `asked_at_turn` (which turn it was FIRST asked at, never
    compared against anything downstream). `test_tail_units.py` (this class's own
    original precedent for the seeded shape) no longer exists at all (file deleted). The
    seed below uses the CURRENT flat shape (`open_question`, not the old nested
    `variables.pending.ttl`) and is measured, with a scratch probe, to survive all three
    intervening "any update on my orders" turns completely unchanged (`_answer_pending`'s
    own "not an answer" fallback carries a pending forward verbatim, by design, for the
    ordinary sticky-question case) - so a bare "yes" on turn 4 still lands on
    `_answer_offer`'s FIRST check (`is_affirmative is True`) and accepts the FOUR-TURN-OLD
    offer, exactly the live bug AC-816 rule 1 exists to prevent. Flagged for a coder pass.
    """

    def _seed_offer(self, session_factory) -> None:
        db = session_factory()
        db.execute(
            text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "open_question": {
                            "kind": "member_offer",
                            "team": "customer_service",
                            "expects": "pick",
                            "options": [],
                            "payload": {"domain": "order"},
                            "asked_at_turn": 1,
                        }
                    }
                ),
            },
        )
        db.commit()

    def _answer_turn(self, session_factory, monkeypatch, *, n: int) -> None:
        qf = _parser_output(
            message_type="business_query",
            intent_hint="check_order",
            # The OFFER'S OWN domain, deliberately. A different domain already clears the
            # carry through `topic.changed` (rule 1's second lifetime), so a sequence that
            # switched domains would pass without the TTL and prove nothing about it.
            domain_hint="order",
            routing={
                "suggested_team": "customer_service",
                "suggested_agent": "order_enquiries",
                "team_source": "parser",
            },
        )
        _stub_parser(monkeypatch, qf)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = f"ZZT-r3-member-ttl-{n}"
        envelope.message["message"]["message"]["text"] = "any update on my orders"
        head = engine_mod.run_turn(envelope, session_factory=session_factory)
        assert head.status == "done", head.error  # business_query completes in-process today

    def test_three_stock_answers_later_a_bare_yes_escalates_nobody(
        self, seeded, session_factory, monkeypatch
    ):
        self._seed_offer(session_factory)
        for n in (2, 3, 4):
            self._answer_turn(session_factory, monkeypatch, n=n)

        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") != "member_offer", (
            f"the pending marker still says an escalation is open four turns later, "
            f"unchanged since it was first asked: {stored.get('open_question')!r}"
        )

        yes_qf = _parser_output(
            message_type="casual",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            is_affirmative=True,
            # #1323: the parser's verdict for a "yes" over an offer; the offer has
            # expired, so even the semantic confirmation finds nothing to accept.
            escalation={"is_escalation_confirmation": True, "company_pick": None},
        )
        _stub_parser(monkeypatch, yes_qf)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = "ZZT-r3-member-ttl-yes"
        envelope.message["message"]["message"]["text"] = "yes"
        head = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert head.branch_kind != "out_of_scope", (
            f"a 'yes' four turns after an ignored, abandoned offer must not accept it "
            f"and route to the escalation lane (got {head.branch_kind!r}, error "
            f"{head.error!r})"
        )


class TestAPendingOrderRosterDoesNotSwallowABareProductCode:
    """AC-816 rule 3 on the ORDER domain with a member roster (prod exec 15443838).

    The owner's chain, cold: "delivery to hanlim, product srtwc286" (no orders, escalation
    offer plus a six-name roster, `pending.kind = member_offer`, domain `order`), then
    "last month" - which PASSES, because a date window is a filter in any domain - then
    "rpacc", which came back `offer_hold` with ZERO entities and re-showed the same roster.

    The cause is upstream of rule 3 and is why the inventory version of this turn passes:
    a bare token with no verb is emitted `casual`, the casual arm wipes `entities` and the
    domain-continuity block skips casual outright, so by the time the member ladder asks
    "is this a filter?" there is nothing left to be one. Rule 3's own premise says what a
    bare entity under an open offer IS - a narrowing of the question the offer was made
    about - so the label is decided by STATE (an offer is open, an entity was named), not
    by whether the model heard a verb (D11).

    Turn 1 is SEEDED as the persisted offer, for the same reason as the TTL sequence above:
    composing a real six-name roster needs the CS gate and real team rows, and none of
    that is what this chain is about. Turns 2 and 3 are real `run_turn` calls against the
    REAL resolver on real seeded rows - which is the half that matters, because the whole
    question is what the resolver is asked and what it comes back with.
    """

    ROSTER = [{"idx": i, "label": f"Member {i}", "uuid": f"u{i}"} for i in range(1, 7)]

    def _seed_master(self, session_factory) -> str:
        from app.models.company import Company
        from app.models.order import Customer
        from app.models.product import Product, ProductCategory, UnitOfMeasure
        from tests._pg_fixture import unique_code

        db = session_factory()
        company = Company(name=unique_code("HAN"), code=unique_code("HAN")[:50])
        db.add(company)
        db.flush()
        db.add(
            Customer(
                customer_code="300-H030",
                customer_name="HANLIM TRADING SDN BHD",
                is_active=True,
                company_id=company.id,
            )
        )
        category = ProductCategory(
            category_code=unique_code("CAT")[:50],
            category_name="ZZT rule 3 category",
            company_id=company.id,
        )
        uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id)
        db.add_all([category, uom])
        db.flush()
        for code in ("SRTWC286", "RPACC"):
            db.add(
                Product(
                    product_code=code,
                    product_name=f"ZZT {code}",
                    category_id=category.id,
                    base_uom_id=uom.id,
                    list_price=10,
                    is_active=True,
                    company_id=company.id,
                )
            )
        db.commit()
        return str(company.id)

    def _seed_offer(self, session_factory) -> None:
        db = session_factory()
        db.execute(
            text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "variables": {
                            "message_type": "business_query",
                            "domain_hint": "order",
                            "intent_hint": "check_order",
                            "selection_context": "member_offer",
                            "last_result_set": self.ROSTER,
                            "entities": [
                                {
                                    "raw": "hanlim",
                                    "hint": "customer",
                                    "canonical_code": "300-H030",
                                    "current_message": False,
                                    "confident": True,
                                }
                            ],
                            "response": (
                                "Would you like me to escalate to customer service team?"
                            ),
                            "pending": {
                                "kind": "member_offer",
                                "team": "customer_service",
                                "domain": "order",
                                "ttl": 3,
                            },
                        }
                    }
                ),
            },
        )
        db.commit()

    def _wire(self, session_factory, monkeypatch, company_id: str) -> None:
        """The REAL resolver seam, scoped to the seeded company the way the engine scopes
        every session it opens (`engine._scoped_factory`, H56).

        The business lane is switched ON so the turn reaches `resolve-entity` at all - off
        it, the head delegates at `routed` and nothing resolves. The FETCH is stubbed to a
        canned success (the same stand-in `test_engine_company_scope` uses): what this
        chain grades is what the resolver was asked and what came back, not the RAG tool
        pick, which needs an embedding provider.
        """
        from tests.chatbot.conftest import set_chatbot_switches
        from tests.chatbot.test_engine_company_scope import (
            _wire_answer_services,
            _wire_answered_fetch,
            _wire_real_resolve_entity,
        )

        set_chatbot_switches(session_factory, business_lane=True)
        db = session_factory()
        db.execute(text("UPDATE system_settings SET chatbot_completed_lanes = CAST(:l AS jsonb)"), {"l": '["business_query"]'})
        db.commit()
        _wire_real_resolve_entity(monkeypatch)
        _wire_answer_services(monkeypatch)
        _wire_answered_fetch(monkeypatch)
        monkeypatch.setattr(
            engine_mod, "_contact_company_scope", lambda factory, cid: frozenset({company_id})
        )

    def _run(self, session_factory, monkeypatch, *, qf, text_body, msg_id):
        _stub_parser(monkeypatch, qf)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = msg_id
        envelope.message["message"]["message"]["text"] = text_body
        return engine_mod.run_turn(envelope, session_factory=session_factory)

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_a_bare_product_code_narrows_the_order_query_instead_of_reprompting(
        self, seeded, session_factory, monkeypatch
    ):
        company_id = self._seed_master(session_factory)
        self._seed_offer(session_factory)
        self._wire(session_factory, monkeypatch, company_id)

        # -- turn 2: "last month". A date window is a filter in ANY domain, and this half
        #    already worked - it is here so turn 3 arrives in the state it really does.
        self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query",
                intent_hint=None,
                domain_hint=None,
                entities=[],
                date_mode="range",
                date_filter_start="2026-08-01",
                date_filter_end="2026-08-31",
            ),
            text_body="last month",
            msg_id="ZZT-rule3-order-t2",
        )

        # -- turn 3: "rpacc". A bare token with no verb: the model calls it casual and
        #    names one entity, and the shape guess for a code under an order thread is
        #    the model's, not the state's.
        head = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual",
                intent_hint=None,
                domain_hint=None,
                entities=[
                    {
                        "raw": "rpacc",
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
            ),
            text_body="rpacc",
            msg_id="ZZT-rule3-order-t3",
        )

        qf3 = head.ctx["parse"]["output"]
        assert qf3["message_type"] == "business_query", (
            f"a bare code under an open roster is a narrowing, not small talk: {qf3!r}"
        )
        assert qf3["domain_hint"] == "order", (
            f"the carried domain must survive the turn: {qf3.get('domain_hint')!r}"
        )
        raws = {str(e.get("raw")).lower() for e in (qf3.get("entities") or [])}
        assert {"rpacc", "hanlim"} <= raws, (
            f"the new filter and the carried customer must both be in scope: {raws!r}"
        )
        assert (qf3.get("escalation") or {}).get("member_reprompt") is None, (
            f"the roster must not be re-offered as if the code were a bad pick: {qf3!r}"
        )
        assert qf3.get("correction") is not True
        assert qf3.get("member_offer_filter_modification") is True, (
            "rule 3's filter arm is what keeps the offer pending while the question "
            f"narrows: {qf3!r}"
        )

        from tests.chatbot.test_engine_company_scope import _looked_up_resolved, _turn_row

        resolved = _looked_up_resolved(_turn_row(session_factory, head.turn_id))
        product_codes = {
            m.get("canonical_code")
            for res in (resolved.get("resolutions") or [])
            for m in (res.get("matches") or [])
            if m.get("entity_type") == "product"
        }
        assert "RPACC" in product_codes, (
            "the resolver must still reach the product through `fallback_to_all_types`, "
            f"whatever type rule 4 stamped on the bare token: {resolved!r}"
        )

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_both_narrowed_turns_are_ANSWERED_and_the_roster_stays_pending(
        self, seeded, session_factory, monkeypatch
    ):
        """Owner console pass 4, item E - the SAME chain, graded on the branch instead of
        on the understanding (live turn 0d1fc129, exec 15456707).

        #705 got the understanding right and the turn still said "Sure, I can help with
        that. Please share the date range you want checked": the parser emitted
        `message_type: casual` for "last month", and `route.decide`'s `is_low_signal` arm
        fires on that word alone, above the business arm, however much state the
        post-processor had restored underneath it. So this grades what the customer
        actually got - the branch - and that the roster is still on screen afterwards with
        one turn spent against its clock (AC-816 rule 1's TTL, which rule 3 deliberately
        does NOT reset).
        """
        company_id = self._seed_master(session_factory)
        self._seed_offer(session_factory)
        self._wire(session_factory, monkeypatch, company_id)

        def _pending() -> dict:
            return (_session_of(session_factory)["variables"] or {}).get("pending") or {}

        assert _pending().get("ttl") == 3, _pending()

        # -- turn 2: "last month", in the shape the LIVE parser emitted it: casual, no
        #    domain, no intent, a real date window and nothing else.
        head2 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual",
                intent_hint=None,
                domain_hint=None,
                entities=[],
                date_mode="range",
                date_filter_start="2026-08-01",
                date_filter_end="2026-08-31",
            ),
            text_body="last month",
            msg_id="ZZT-item-e-t2",
        )
        qf2 = head2.ctx["parse"]["output"]
        assert qf2.get("member_offer_filter_modification") is True, qf2
        assert head2.branch_kind == "business_query", (
            "a date window that narrows a still-open order question must be ANSWERED; "
            f"low_signal is what produced 'please share the date range': {head2.branch_kind!r}"
        )
        engine_mod.complete_turn(
            head2.turn_id,
            _fragments(
                item={
                    "allowed": True,
                    "response": "Here are the orders I found.",
                    "items": [{"title": "SO-20021", "fields": []}],
                }
            ),
            session_factory=session_factory,
        )
        assert _pending().get("kind") == "member_offer", (
            f"the roster is still on screen, so the offer stays pending: {_pending()!r}"
        )
        assert _pending().get("ttl") == 2, (
            f"a filter modification SPENDS one turn against the offer's clock: {_pending()!r}"
        )

        # -- turn 3: "rpacc". Same shape, an entity filter instead of a date one.
        head3 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual",
                intent_hint=None,
                domain_hint=None,
                entities=[
                    {
                        "raw": "rpacc",
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
            ),
            text_body="rpacc",
            msg_id="ZZT-item-e-t3",
        )
        assert head3.branch_kind == "business_query", (
            "the bare product code narrows the same question and must be answered too: "
            f"{head3.branch_kind!r}"
        )

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_a_container_hinted_product_code_answers_stock_not_incoming(
        self, seeded, session_factory, monkeypatch
    ):
        """Owner console pass 4, item F (live turn ace4cec6).

        With a stock question already in play the customer typed a bare "srtwc287"; the
        parser hinted the token `inbound_shipment` and invented `check_incoming` /
        `incoming`, which made `domain_signal_source` `intent_explicit` and so bypassed
        AC-816 rule 4's bare-entity inheritance - and the reply led with "No incoming stock
        (ETA) found for SRTWC287" while the resolver's own answer for that token was
        products. Graded on the REAL resolver against real seeded rows, because the whole
        rule is "what did the resolver come back with".
        """
        company_id = self._seed_master(session_factory)
        from app.models.base import set_company_scope
        from app.models.product import Product

        db = session_factory()
        # `_seed_master` created a FRESH company; the suite's session default is Sorento's,
        # so the seeded rows are invisible to an ordinary query on this session.
        set_company_scope(db, frozenset({company_id}))
        sibling = db.query(Product).filter(Product.product_code == "SRTWC286").one()
        db.add(
            Product(
                product_code="SRTWC287",
                product_name="ZZT SRTWC287",
                category_id=sibling.category_id,
                base_uom_id=sibling.base_uom_id,
                list_price=10,
                is_active=True,
                company_id=sibling.company_id,
            )
        )
        db.commit()
        self._seed_offer(session_factory)
        self._wire(session_factory, monkeypatch, company_id)

        head = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query",
                intent_hint="check_incoming",
                domain_hint="incoming",
                entities=[
                    {
                        "raw": "srtwc287",
                        "hint": "inbound_shipment",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
            ),
            text_body="srtwc287",
            msg_id="ZZT-item-f-t1",
        )

        qf = head.ctx["parse"]["output"]
        assert qf["entities"][0]["hint"] == "product", (
            f"the resolver answered PRODUCTS and no shipment: {qf['entities']!r}"
        )
        assert qf.get("shipment_hint_retyped") == ["srtwc287"], qf
        assert qf["domain_hint"] == "order", (
            "the invented incoming domain had no support but that one mis-hinted entity "
            f"and no incoming word in the message, so the carried domain applies: "
            f"{qf['domain_hint']!r}"
        )
        assert head.branch_kind == "business_query", head.branch_kind


class TestAnOutOfRangePickKeepsTheProductInScope:
    """"promotion 7445" -> a three-tier picker -> "9" -> "all" (prod execs 15445325 /
    15445363).

    The re-prompt was right and the tier list survived, but the session it wrote had no
    product and no domain in it, so the valid "all" that followed answered "I need at
    least one filter" instead of every tier's promotions for 7445. Graded here as the real
    chain, because the defect is entirely in what ONE turn persisted and what the NEXT one
    read back off the database.

    Turn 1 is seeded as the persisted tier offer, for the same reason as the two chains
    above: composing a real tier picker needs the entitlement read and the tier probe, and
    what this chain is about is the two turns after it.
    """

    TIERS = [
        {"idx": 1, "label": "Dealer", "value": "Dealer"},
        {"idx": 2, "label": "End User", "value": "End User"},
        {"idx": 3, "label": "Contractor", "value": "Contractor"},
    ]

    def _seed_offer(self, session_factory) -> None:
        db = session_factory()
        db.execute(
            text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "variables": {
                            "message_type": "business_query",
                            "domain_hint": "promotion",
                            "intent_hint": "check_promotion",
                            "selection_context": "tier_offer",
                            "last_result_set": self.TIERS,
                            "entities": [
                                {
                                    "raw": "7445",
                                    "hint": "product",
                                    "canonical_code": "SRTWC7445",
                                    "current_message": False,
                                    "confident": True,
                                }
                            ],
                            "response": "Which access level?",
                        }
                    }
                ),
            },
        )
        db.commit()

    def _run(self, session_factory, monkeypatch, *, qf, text_body, msg_id):
        _stub_parser(monkeypatch, qf)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = msg_id
        envelope.message["message"]["message"]["text"] = text_body
        return engine_mod.run_turn(envelope, session_factory=session_factory)

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_the_next_reply_still_knows_which_product_it_is_all_of(
        self, seeded, session_factory, monkeypatch
    ):
        self._seed_offer(session_factory)

        # -- turn 2: "9". Out of range against a three-row list, and the model emits it
        #    `casual` with no positions and no entities of its own. `complete_turn` is
        #    called explicitly because the TAIL is what persists the session, and this
        #    chain is entirely about what that turn wrote down.
        head2 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[]
            ),
            text_body="9",
            msg_id="ZZT-tier-oor-9",
        )
        engine_mod.complete_turn(
            head2.turn_id,
            _fragments(item={"allowed": True, "response": "Please choose 1, 2 or 3."}),
            session_factory=session_factory,
        )

        stored = _session_of(session_factory)["variables"]
        assert stored.get("selection_context") == "tier_offer", (
            f"the tier list must survive the bad digit: {stored!r}"
        )
        assert [str(e.get("raw")) for e in (stored.get("entities") or [])] == ["7445"], (
            f"the re-prompt turn persisted no product to be 'all' OF: {stored!r}"
        )
        assert stored.get("domain_hint") == "promotion", stored

        # -- turn 3: "all". Valid, and it must reach the promotion lane with 7445 in scope.
        head = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query",
                intent_hint=None,
                domain_hint=None,
                entities=[],
                access_levels=["Dealer", "End User", "Contractor"],
            ),
            text_body="all",
            msg_id="ZZT-tier-oor-all",
        )
        qf3 = head.ctx["parse"]["output"]
        assert qf3.get("domain_hint") == "promotion", (
            f"the 'all' turn lost the offer's domain: {qf3!r}"
        )
        assert "7445" in {str(e.get("raw")) for e in (qf3.get("entities") or [])}, (
            f"the 'all' turn has nothing to be all OF: {qf3.get('entities')!r}"
        )


# --------------------------------------------------------------------------- #
# Owner ruling B, console pass 3 (6 Sep 2026), as the REAL two-turn chain.
#
# `test_output_exchange_rules.py::TestOwnerRulingBAllOfThemOverPendingDymOffer` grades the
# post-processor alone, against a hand-built `dym_last_result_set`. This grades the whole
# thing: turn 1 asks about a code that does not exist, the REAL resolver's trigram tier
# finds three REAL siblings under the contact's own company and the tail persists the offer;
# turn 2 says "all of them" and must come back scoped to all three.
#
# THE HARNESS GAP THIS FILE HAD, written down so it is not reintroduced: `seeded` above
# inserts a bare `respond_contacts` row - no workspace, no `respond_contact_companies` - and
# `_stub_parser` pointed `default_space_id` at None. `engine.run_turn` resolves the
# contact's scope from exactly that pair, so every session the turn opened carried an EMPTY
# frozenset, `entity_resolver._company_scope_sql` compiled it to ` AND FALSE`, and the
# trigram probe returned nothing however good the seed data was. The rule-3 chain above
# side-steps it by monkeypatching `engine._contact_company_scope`; this one does NOT - it
# seeds the workspace and the membership and lets the production path resolve the scope, so
# the chain also proves that path still works.
# --------------------------------------------------------------------------- #

DYM_SPACE_ID = "364817"


class TestAllOfThemOverADidYouMeanOfferAnswersEveryOfferedCode:
    OFFERED = ("SRTKS6091", "SRTKS8047", "SRTKS8050")
    MISSING = "SRTKS8091"

    def _seed_scope_and_products(self, session_factory) -> str:
        """A default workspace, a company, the contact's membership in it, and the three
        REAL sibling products the trigram tier has to find. Returns the company id."""
        from app.models.company import Company, RespondContactCompany
        from app.models.product import Product, ProductCategory, UnitOfMeasure
        from app.models.respond_workspace import RespondWorkspace
        from tests._pg_fixture import unique_code

        db = session_factory()
        workspace = RespondWorkspace(
            space_id=DYM_SPACE_ID,
            name="ZZT dym chain workspace",
            api_key_ciphertext="ZZT-cipher",
            is_default=True,
        )
        company = Company(name="Sorento", code=unique_code("SRT")[:50])
        db.add_all([workspace, company])
        db.flush()
        contact_id = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": str(CONTACT_ID)},
        ).scalar()
        db.execute(
            text("UPDATE respond_contacts SET workspace_id = :w WHERE id = :c"),
            {"w": workspace.id, "c": contact_id},
        )
        db.add(RespondContactCompany(respond_contact_id=contact_id, company_id=company.id))
        category = ProductCategory(
            category_code=unique_code("CAT")[:50],
            category_name="ZZT dym chain category",
            company_id=company.id,
        )
        uom = UnitOfMeasure(uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=company.id)
        db.add_all([category, uom])
        db.flush()
        for code in self.OFFERED:
            db.add(
                Product(
                    product_code=code,
                    product_name=f"ZZT sink {code}",
                    category_id=category.id,
                    base_uom_id=uom.id,
                    list_price=10,
                    is_active=True,
                    company_id=company.id,
                )
            )
        db.commit()
        return str(company.id)

    def _put_trgm_on_the_search_path(self, session_factory) -> None:
        """The SECOND half of the harness gap, and the one that survives fixing the scope.

        `tests/chatbot/conftest.py`'s `session_factory` pins `search_path` to the scratch
        schemas so a raw-SQL write cannot escape into the real tables - which also drops the
        schema that holds `similarity()` and the `%` operator, so every trigram probe raises
        "function similarity(text, unknown) does not exist" INSIDE its own savepoint, is
        swallowed by the probe's except-block, and the token comes back with no
        alternatives at all. Appending the extension's schema LAST is the recipe
        `tests/test_entity_resolver_trgm_normalized.py` already uses and for the same
        reason: every table name still resolves to the scratch schema first, so the guard
        that fixture exists for still holds, and the append dies with this test's own
        transaction.
        """
        db = session_factory()
        trgm_schema = db.execute(
            text(
                "SELECT n.nspname FROM pg_extension e "
                "JOIN pg_namespace n ON n.oid = e.extnamespace "
                "WHERE e.extname = 'pg_trgm'"
            )
        ).scalar()
        assert trgm_schema, "pg_trgm is not installed; the did-you-mean tier cannot run"
        current = db.execute(text("SHOW search_path")).scalar()
        db.execute(text(f'SET LOCAL search_path TO {current}, "{trgm_schema}"'))

    def _wire(self, session_factory, monkeypatch) -> None:
        from tests.chatbot.conftest import set_chatbot_switches
        from tests.chatbot.test_engine_company_scope import (
            _wire_answer_services,
            _wire_real_resolve_entity,
        )

        set_chatbot_switches(session_factory, business_lane=True)
        db = session_factory()
        db.execute(
            text("UPDATE system_settings SET chatbot_completed_lanes = CAST(:l AS jsonb)"),
            {"l": '["business_query"]'},
        )
        db.commit()
        _wire_real_resolve_entity(monkeypatch)
        _wire_answer_services(monkeypatch)

        def _run_fetch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
            """The stock answer, named per product IN SCOPE.

            Not a canned sentence: this chain's whole question is which products reached
            the answer, so the stand-in says which ones it was given. The RAG tool pick and
            the MCP read are what a canned fetch is standing in for; the scope is not.
            """
            codes = [
                str(e.get("canonical_code") or e.get("raw"))
                for e in ((payload.get("parser") or {}).get("entities") or [])
                if (e or {}).get("hint") == "product"
            ]
            response = "In stock: " + ", ".join(codes) if codes else "I need a product."
            fetch = {
                "answers": [{"note": c} for c in codes],
                "response": response,
                "has_result": bool(codes),
                "_fetch_arm": "result",
            }
            return {
                "kind": "result",
                "_fetch_arm": "result",
                "delegate": "business_query",
                "delegate_payload": {**payload, "fetch": fetch},
                "fetch": fetch,
            }

        monkeypatch.setattr(engine_mod.business, "run_fetch", _run_fetch)

    def _run(self, session_factory, monkeypatch, *, qf, text_body, msg_id):
        _stub_parser(monkeypatch, qf, space_id=DYM_SPACE_ID)
        envelope = _envelope(is_test=False)
        envelope.message["message"]["messageId"] = msg_id
        envelope.message["message"]["message"]["text"] = text_body
        return engine_mod.run_turn(envelope, session_factory=session_factory)

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_a_missing_code_offers_its_siblings_and_all_of_them_answers_for_every_one(
        self, seeded, session_factory, monkeypatch
    ):
        self._seed_scope_and_products(session_factory)
        self._put_trgm_on_the_search_path(session_factory)
        self._wire(session_factory, monkeypatch)

        # -- turn 1: "Srtks8091 got stock". No such product; the three seeded siblings are
        #    the trigram tier's neighbours, and the tail persists them as the offer.
        head1 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query",
                intent_hint="check_stock",
                domain_hint="inventory",
                entities=[
                    {
                        "raw": self.MISSING,
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
            ),
            text_body=f"{self.MISSING} got stock",
            msg_id="ZZT-dym-chain-t1",
        )
        assert head1.status in ("done", "delegated"), head1.error

        stored = _session_of(session_factory)["variables"]
        offered = [
            str(jsc_code)
            for jsc_code in (
                (c or {}).get("code") for c in ((stored.get("dym_offer") or {}).get("candidates") or [])
            )
            if jsc_code
        ]
        assert sorted(offered) == sorted(self.OFFERED), (
            f"turn 1 did not persist a did-you-mean offer over the three real siblings: "
            f"{stored.get('dym_offer')!r}"
        )
        roster = stored.get("dym_last_result_set") or stored.get("last_result_set") or []
        assert [r.get("value") for r in roster] == list(self.OFFERED), (
            f"the numbered roster the pick resolves against was not persisted: {stored!r}"
        )
        assert stored.get("selection_context") == "suggest_offer", stored

        # -- turn 2: "all of them". The parser hears small talk; the state is what makes
        #    this a pick-all over the offer.
        head2 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual",
                intent_hint=None,
                domain_hint=None,
                entities=[],
            ),
            text_body="all of them",
            msg_id="ZZT-dym-chain-t2",
        )

        qf2 = head2.ctx["parse"]["output"]
        assert qf2["message_type"] == "business_query", qf2
        assert qf2["domain_hint"] == "inventory", (
            f"the pick-all must stay in the offer's own domain: {qf2.get('domain_hint')!r}"
        )
        codes = sorted(
            str(e.get("canonical_code"))
            for e in (qf2.get("entities") or [])
            if e.get("canonical_code")
        )
        assert codes == sorted(self.OFFERED), (
            f"'all of them' must scope the turn to every offered code: {qf2.get('entities')!r}"
        )
        assert qf2.get("select_all_expanded") is True, (
            f"'all of them' must expand to every offered position: {qf2!r}"
        )
        # WHICH pick-all arm this chain takes, stated because it is not the one
        # `test_output_exchange_rules.py::TestOwnerRulingBAllOfThemOverPendingDymOffer`
        # grades. A FULL miss (no token resolved) is `build-suggest-offer`'s roster, and
        # the tail writes it as `last_result_set` + `selection_context: suggest_offer`, so
        # `output_exchange`'s select-all arm for a picker roster runs and its op is
        # "reuse" - the positional resolution downstream is what puts the three codes in
        # scope. `dym_last_result_set` (and with it `entity_op: "replace"`) is the PARTIAL
        # did-you-mean's separate roster, written only when some tokens DID answer
        # (`compile_state._partial_dym_block`). Two rosters, two arms, one visible
        # contract: every offered code ends up in scope with a uuid.
        assert qf2.get("entity_op") == "reuse", qf2.get("entity_op")
        assert all(e.get("uuid") for e in qf2["entities"]), (
            f"each picked code must carry the roster's own uuid, so the answer step needs "
            f"no re-resolution: {qf2['entities']!r}"
        )

        done2 = engine_mod.complete_turn(
            head2.turn_id,
            _fragments(item={"allowed": True}),
            session_factory=session_factory,
        ) if head2.status == "delegated" else head2
        reply = (done2.reply or {}).get("text") or ""
        for code in self.OFFERED:
            assert code in reply, f"the answer must name {code}: {reply!r}"


class TestAPartialDidYouMeanPickReplacesOnlyTheMissingToken(
    TestAllOfThemOverADidYouMeanOfferAnswersEveryOfferedCode
):
    """Review of #706, S4. `entity_op: "replace"` at `output_exchange.apply_dym_pick` is
    the arm for a pick against `dym_last_result_set` - the PARTIAL did-you-mean's roster,
    the one `compile_state._partial_dym_block` writes when some tokens answered - and the
    sibling chain above only reaches the FULL-miss arm (`reuse`). This grades the replace
    arm THROUGH THE ENGINE: the real resolver, the real head, turn 2 a numbered pick.

    MEASURED, and the reason turn 1 does not produce the roster by itself: on this lane a
    partial product miss is claimed by the ANSWER half's `dym-annotate-partial` ->
    `build-suggest-offer` before the tail runs, so the turn persists `selection_context:
    suggest_offer` with the siblings as `last_result_set`, and `_partial_dym_block` then
    returns on its own `is_clarification` guard. `dym_last_result_set` is that block's
    fallback for an answered turn no offer claimed - and the state the old spine wrote,
    which is what a customer mid-offer on cutover day carries. So turn 1 is graded on what
    it does, and the partial roster is then seeded in exactly the shape the block writes
    (`for_raw`, `for_hint`, `for_canonical` linkage included) before the pick.
    """

    RESOLVED = "SRTKS6091"  # one of the seeded siblings, typed exactly

    def _wire(self, session_factory, monkeypatch) -> None:
        """The full-miss chain's wiring, with ROSTER-SHAPED answer rows: a stand-in row
        of `{"note": code}` builds no roster row, and the tail's answered-turn arm reads
        the answer's own `last_result_set`."""
        super()._wire(session_factory, monkeypatch)

        def _run_fetch(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
            codes = [
                str(e.get("canonical_code") or e.get("raw"))
                for e in ((payload.get("parser") or {}).get("entities") or [])
                if (e or {}).get("hint") == "product" and (e or {}).get("uuid")
            ]
            response = "In stock: " + ", ".join(codes) if codes else "I need a product."
            fetch = {
                "answers": [
                    {
                        "title": c,
                        "fields": [
                            {"key": "product_code", "label": "Product Code", "value": c},
                            {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 5},
                        ],
                    }
                    for c in codes
                ],
                "response": response,
                "has_result": bool(codes),
                "_fetch_arm": "result",
            }
            return {
                "kind": "result",
                "_fetch_arm": "result",
                "delegate": "business_query",
                "delegate_payload": {**payload, "fetch": fetch},
                "fetch": fetch,
            }

        monkeypatch.setattr(engine_mod.business, "run_fetch", _run_fetch)

    def _seed_partial_roster(self, session_factory, stored: dict) -> list[dict]:
        """`dym_last_result_set` in `_partial_dym_block`'s own row shape, built from the
        siblings turn 1 actually found, written beside the state turn 1 persisted."""
        roster = [
            {
                "idx": i + 1,
                "label": c["code"],
                "value": c["code"],
                "product": c["code"],
                "uuid": c["uuid"],
                "entity_type": "product",
                "for_raw": self.MISSING,
                "for_hint": "product",
                "for_canonical": None,
            }
            for i, c in enumerate((stored.get("dym_offer") or {}).get("candidates") or [])
        ]
        assert roster, stored
        db = session_factory()
        row = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": str(CONTACT_ID)},
        ).first()
        session_vars = json.loads(row.session_vars) if isinstance(row.session_vars, str) else dict(row.session_vars or {})
        session_vars["variables"] = {**(session_vars.get("variables") or {}), "dym_last_result_set": roster}
        db.execute(
            text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :cid"),
            {"sv": json.dumps(session_vars), "cid": str(CONTACT_ID)},
        )
        db.commit()
        return roster

    @pytest.mark.xfail(strict=True, reason=_XFAIL_FLAT_VARIABLES_SHAPE)
    def test_a_numbered_pick_over_a_partial_roster_scopes_to_the_resolved_code_and_the_pick(
        self, seeded, session_factory, monkeypatch
    ):
        self._seed_scope_and_products(session_factory)
        self._put_trgm_on_the_search_path(session_factory)
        self._wire(session_factory, monkeypatch)

        head1 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query",
                intent_hint="check_stock",
                domain_hint="inventory",
                entities=[
                    {
                        "raw": self.RESOLVED,
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    },
                    {
                        "raw": self.MISSING,
                        "hint": "product",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    },
                ],
            ),
            text_body=f"{self.RESOLVED} and {self.MISSING} got stock",
            msg_id="ZZT-dym-partial-t1",
        )
        assert head1.status in ("done", "delegated"), head1.error

        stored = _session_of(session_factory)["variables"]
        # The measured arm: the answer half's suggest offer claims the partial miss.
        assert stored.get("selection_context") == "suggest_offer", stored.get("selection_context")
        assert stored.get("dym_last_result_set") is None, (
            "if the tail now writes the partial roster itself, drop `_seed_partial_roster` "
            f"and grade the turn directly: {stored.get('dym_last_result_set')!r}"
        )
        raws = {str(e.get("raw")).upper() for e in stored.get("entities") or []}
        assert {self.RESOLVED, self.MISSING} <= raws, raws
        roster = self._seed_partial_roster(session_factory, stored)
        picked = roster[1]["value"] if len(roster) > 1 else roster[0]["value"]
        position = 2 if len(roster) > 1 else 1

        # -- turn 2: the numbered pick.
        head2 = self._run(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="clarification",
                intent_hint=None,
                domain_hint=None,
                entities=[],
                reference_positions=[position],
                # The parser's own tag for a pick against the did-you-mean roster; the
                # numbered handler is keyed on it. Without it the STOCK positional arm
                # resolves the digit over `last_result_set` instead (measured: that arm
                # came back `replace_combine` with only the pick in scope and the resolved
                # SRTKS6091 dropped - a separate finding, reported, not graded here).
                reference_target="dym",
            ),
            text_body=str(position),
            msg_id="ZZT-dym-partial-t2",
        )
        qf2 = head2.ctx["parse"]["output"]
        assert qf2.get("dym_pick_applied") is True, qf2
        assert qf2.get("entity_op") == "replace", (
            f"a did-you-mean pick names its op `replace`: {qf2.get('entity_op')!r}"
        )
        codes = sorted(
            str(e.get("canonical_code") or e.get("raw")).upper() for e in (qf2.get("entities") or [])
        )
        assert codes == sorted([self.RESOLVED, picked]), (
            f"the scope is the resolved code plus the pick, and never the missing token "
            f"{self.MISSING} again - which is what `replace_combine` put back: "
            f"{qf2.get('entities')!r}"
        )
        assert head2.branch_kind == "business_query", head2.branch_kind
