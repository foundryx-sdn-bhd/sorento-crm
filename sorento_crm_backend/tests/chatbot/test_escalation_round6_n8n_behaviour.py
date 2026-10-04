"""#865 fix round 6: the n8n escalation behaviour, ported into the turn engine.

Owner ruling (28 Sep 00:2x MYT, PR #1300): "we did it in n8n and it was quite ok ady, we
need to put it into our chatbot turn". The reference is sorento-crm-n8n PR #23
(`n8n-workflows-init/plans/miss-company-routing-plan.md`, `tests/miss-company-routing-UAC.md`,
the parser fork's `output_exchange` rev-5 `_coCompanyPick`). Its captain decisions of
18 Aug 2026, as the owner's ruling restates them:

1. Partial miss on an order enquiry (one company has records, the other none): the offer
   is scoped to the miss company ("Would you like me to escalate to *Sorento* customer
   service team?") with that company's member picker; a bare "yes" or a number routes
   there (UAC M1, M2, M3, M8g).
2. Quantity 0 is not a miss: a stock or incoming turn is untouched (M5, Q1, Q3, Q4).
3. Both companies miss and a bare "yes": clarify the company instead of assigning from a
   default pool; a company name, code or alias reply ("mocha", "srt", "yes please
   escalate to sorento team") resolves the pick (M4a, M7a to M7c, M8a to M8c, M8e, M8f,
   M8h), deterministic first, then the parser's own `escalation.company_pick`, validated
   against the OFFERED pool.
4. Copy: a multi-company offer says reply with the company name (*Mocha* / *Sorento*) and
   we assign accordingly; bold group headers; no per-member (Company) suffix; a
   single-company phrase names the company (M7d, M7e); a clarify after an offer that
   showed no picker drops "a number, a name" (rev-3 copy, Q5).
5. Offer hold: an unrecognised reply while a multi-company offer is open re-clarifies and
   keeps the offer open (M8d).
6. An escalation whose offer showed no roster carries the resolved brand to next-assignee
   instead of dropping it.

Harness: `run_turn` against a seeded two-company chain (companies named "Mocha" and
"Sorento", so the n8n aliases "mch" / "srt" apply), the orders tool stubbed with the
envelope `company_scope.stamp_lookup_companies` produces, the parser stubbed, and the CS
roster read (`team_roster_service.list_team_roster`) stubbed per company. The escalation
lane is the REAL `escalation.run`, forced to a dry run, so the routing it would draw is
read straight off `escalation_context` / `_next_assignee_body`. Postgres only.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import answer_bridge
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes import escalation as escalation_mod
from app.services.chatbot.lanes.escalation import _next_assignee_body, escalation_context
from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_engine_company_scope import (
    _scope_envelope,
    _seed_company,
    _seed_contact,
    _seed_product,
    _seed_workspace,
)
from tests.chatbot.test_rearch_r11_multi_company import (
    CUSTOMER_CODE,
    CUSTOMER_NAME,
    PRODUCT_CODE,
    _order_row,
    _orders_envelope,
    _said,
    _seed_customer,
    _wire,
)

CONTACT = "ZZT-contact-r6-n8n"
MOCHA = "Mocha"
SORENTO = "Sorento"

# The CS rosters the stubbed read hands back, per company (UAC M1: the miss company's
# members only; M4a: both, under a header each).
MOCHA_MEMBERS = [("zzt-u-kia", "Kia Yee")]
SORENTO_MEMBERS = [("zzt-u-jer", "Jereen Tee"), ("zzt-u-tay", "Tay Zhi Yang")]


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _chain(session_factory, *, product_order: tuple[str, str] = ("mocha", "sorento")) -> dict[str, str]:
    """`product_order` is the order the two same-code products are INSERTED in, which is
    the only thing that decides which one the resolver's exact probe returns first (it has
    no ORDER BY). The default is this file's historical order; a shared xdist worker's
    earlier tests can still lay the rows out the other way on disk (main cd220251)."""
    a = _seed_company(session_factory, name=MOCHA)
    b = _seed_company(session_factory, name=SORENTO)
    ids = {"mocha": a, "sorento": b}
    for key in product_order:
        _seed_product(session_factory, company_id=ids[key], code=PRODUCT_CODE)
    for company in (a, b):
        _seed_customer(session_factory, company_id=company, name=CUSTOMER_NAME, code=CUSTOMER_CODE)
    workspace = _seed_workspace(session_factory)
    _seed_contact(
        session_factory, contact_id=CONTACT, phone="+60000000966", workspace_id=workspace, company_ids=[a, b]
    )
    return ids


# The both-company miss names the companies in the order the resolver returned the product
# rows, and that order is the table's physical row order: the exact product probe has no
# ORDER BY and the answer node keeps the resolver's order (the n8n parity fixtures pin it,
# e.g. `crossdomain-render/exec-13488926.json` says "checked in Sorento and Mocha"). No rule
# fixes it, so a test that needs "both were searched" accepts either order.
BOTH_CHECKED = ("checked in Mocha and Sorento", "checked in Sorento and Mocha")


def _checked_both(said: str) -> bool:
    return any(phrase in said for phrase in BOTH_CHECKED)


def _stub_rosters(monkeypatch, ids: dict[str, str]) -> list[dict[str, Any]]:
    from app.services import team_roster_service

    reads: list[dict[str, Any]] = []
    by_company = {ids["mocha"]: MOCHA_MEMBERS, ids["sorento"]: SORENTO_MEMBERS}

    def fake_list_team_roster(db, **kwargs):
        reads.append(kwargs)
        members = by_company.get(kwargs.get("company_id"), [])
        return [
            {"user_id": uid, "name": name, "respond_user_id": f"9{i}{len(name)}", "sort_order": i}
            for i, (uid, name) in enumerate(members, start=1)
        ]

    monkeypatch.setattr(team_roster_service, "list_team_roster", fake_list_team_roster)
    return reads


def _order_ask(agent: str = "order_enquiries") -> dict[str, Any]:
    return _parser_output(
        intent_hint="check_order",
        domain_hint="order",
        user_goal="checking the orders of a customer for a product",
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        date_filter_start="2026-10-01",
        date_filter_end="2026-10-31",
        entities=[
            {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
        ],
        routing={"suggested_team": "customer_service", "suggested_agent": agent, "team_source": None},
    )


def _reply(**overrides: Any) -> dict[str, Any]:
    """A reply to an open offer, as the parser reads a short message: no domain, no
    entity of its own, nothing asked."""
    base: dict[str, Any] = {
        "message_type": "casual",
        "intent_hint": None,
        "domain_hint": None,
        "entities": [],
        "user_goal": "replying to the offer",
        "routing": {"suggested_team": None, "suggested_agent": None, "team_source": None},
        "escalation": {"is_escalation_confirmation": False, "company_pick": None},
    }
    base.update(overrides)
    return _parser_output(**base)


def _yes(**overrides: Any) -> dict[str, Any]:
    escalation = {"is_escalation_confirmation": True, "company_pick": None}
    escalation.update(overrides.pop("escalation", {}))
    return _reply(is_affirmative=True, escalation=escalation, **overrides)


def _position(n: int) -> dict[str, Any]:
    return _reply(reference_positions=[n])


def _dry_run_lane(calls: list[tuple[Any, Any, Any]]):
    """The REAL `escalation.run`, forced dry (no team seeded, no cursor moves)."""

    def run(ctx, item, *, dry_run=False, session_factory=None):
        result = escalation_mod.run(ctx, item, dry_run=True, session_factory=session_factory)
        calls.append((ctx, item, result))
        return result

    return run


class Conversation:
    """One contact, turn after turn, the session written to the DB between turns."""

    def __init__(
        self,
        session_factory,
        monkeypatch,
        stub_parser,
        stub_access,
        system_settings_row,
        *,
        rows,
        product_order: tuple[str, str] = ("mocha", "sorento"),
    ):
        self.sf = session_factory
        self.stub_parser = stub_parser
        self.ids = _chain(session_factory, product_order=product_order)
        self.rosters = _stub_rosters(monkeypatch, self.ids)
        envelope = _orders_envelope(
            rows(self),
            [{"id": self.ids["mocha"], "name": MOCHA}, {"id": self.ids["sorento"], "name": SORENTO}],
        )
        _wire(session_factory, system_settings_row, monkeypatch, envelope=envelope)
        stub_access()
        self.lane_calls: list[tuple[Any, Any, Any]] = []
        monkeypatch.setattr(engine_mod, "run_escalation_lane", _dry_run_lane(self.lane_calls))
        self.n = 0

    def say(self, text: str, verdict: dict[str, Any]):
        self.n += 1
        self.stub_parser(verdict)
        before = len(self.lane_calls)
        result = engine_mod.run_turn(
            _scope_envelope(CONTACT, message_id=f"zzt-r6-{self.n}", text=text),
            session_factory=self.sf,
        )
        assert result.status == "done", (text, result.error)
        self.last_lane = self.lane_calls[before:]
        return result

    def open_question(self) -> dict[str, Any]:
        from sqlalchemy import text as sql

        row = self.sf().execute(
            sql("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"), {"c": CONTACT}
        ).first()
        raw = row.session_vars if row is not None else {}
        sv = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return sv.get("open_question") or {}

    def routed(self) -> dict[str, Any]:
        """Where the escalation this turn handed over would be drawn from."""
        assert len(self.last_lane) == 1, self.last_lane
        ctx, item, result = self.last_lane[0]
        context = escalation_context(item, ctx=ctx)
        body = _next_assignee_body(ctx, context)
        return {"context": context, "body": body, "result": result}

    def company(self) -> Any:
        routed = self.routed()
        result = routed["result"]
        assert not (result.get("pending") or {}).get("kind"), ("the turn asked instead of routing", result)
        return routed["body"].get("company_id")


def _partial(conv: Conversation) -> list[dict[str, Any]]:
    return [_order_row(MOCHA, "ZZTM2609-0881"), _order_row(MOCHA, "ZZTM2609-0882")]


def _both_miss(conv: Conversation) -> list[dict[str, Any]]:
    return []


@pytest.fixture
def conversation(session_factory, monkeypatch, stub_parser, stub_access, system_settings_row):
    def start(rows, **kwargs):
        return Conversation(
            session_factory, monkeypatch, stub_parser, stub_access, system_settings_row, rows=rows, **kwargs
        )

    return start


PICKER_HEAD = "Please choose who to route to (reply with the number):"
YES_SENTENCE = "If you have no preference, just reply 'yes' and we'll assign automatically."
COMPANY_SENTENCE = (
    "If you have no preference, reply with the company name (*Mocha* / *Sorento*) and we'll assign accordingly."
)
MEMBER_CLARIFY = (
    "Both *Mocha* and *Sorento* teams are listed - reply a number, a name, or the company "
    "(*Mocha* / *Sorento*) and I'll assign automatically."
)
PLAIN_CLARIFY = (
    "Both *Mocha* and *Sorento* teams are listed - reply with the company (Mocha / Sorento) "
    "and I'll assign automatically."
)


# --------------------------------------------------------------------------- #
# R1 - a partial miss on an order enquiry: the offer and the picker are the miss company's
# --------------------------------------------------------------------------- #


class TestR1PartialMissOffersTheMissCompanysPicker:
    def test_the_offer_names_sorento_and_lists_only_its_members_numbered_after_the_orders(self, conversation):
        conv = conversation(_partial)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask()))
        assert "*Sorento:* no orders records found for" in said, said
        expected = (
            "Would you like me to escalate to *Sorento* customer service team?\n\n"
            f"{PICKER_HEAD}\n3. Jereen Tee\n4. Tay Zhi Yang\n\n{YES_SENTENCE}"
        )
        assert expected in said, said
        assert "Kia Yee" not in said, "only the MISS company's roster is offered (UAC M1)"
        assert "(Sorento)" not in said, "no per-member company suffix (rev-3)"
        assert [r.get("company_id") for r in conv.rosters] == [conv.ids["sorento"]], conv.rosters
        assert {(r.get("team_code"), r.get("agent_code")) for r in conv.rosters} == {
            ("customer_service", "order_enquiries")
        }
        question = conv.open_question()
        assert question.get("kind") == "member_offer", question
        assert [o.get("position") for o in question.get("options") or []] == [3, 4], question

    def test_a_number_routes_to_that_member_in_sorento(self, conversation):
        conv = conversation(_partial)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        conv.say("3", _position(3))
        routed = conv.routed()
        assert routed["body"].get("preferred_assignee_id") == "zzt-u-jer", routed["body"]
        assert routed["body"].get("company_id") == conv.ids["sorento"], routed["body"]
        assert routed["body"].get("team_code") == "customer_service", routed["body"]

    def test_a_bare_yes_routes_to_sorento(self, conversation):
        conv = conversation(_partial)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        conv.say("yes", _yes())
        assert conv.company() == conv.ids["sorento"]
        assert not conv.routed()["body"].get("preferred_assignee_id")

    def test_yes_mocha_on_a_sorento_only_offer_still_routes_to_sorento(self, conversation):
        """UAC M8g: the pool is the companies OFFERED, never the union."""
        conv = conversation(_partial)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        conv.say("yes mocha", _yes(escalation={"company_pick": "Mocha"}))
        assert conv.company() == conv.ids["sorento"]

    def test_srt_on_the_sorento_offer_routes_to_sorento(self, conversation):
        conv = conversation(_partial)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        conv.say("srt", _reply())
        assert conv.company() == conv.ids["sorento"]

    def test_a_non_order_partial_miss_keeps_the_plain_offer(self, conversation):
        """Members are for customer order enquiries only (rev-3): any other routing pair
        keeps the plain company-named phrase, no roster read."""
        conv = conversation(_partial)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask(agent="general_enquiries")))
        assert said.rstrip().endswith("Would you like me to escalate to *Sorento* customer service team?"), said
        assert PICKER_HEAD not in said and conv.rosters == []


# --------------------------------------------------------------------------- #
# R2 - quantity 0 is not a miss; stock and incoming turns are untouched
# --------------------------------------------------------------------------- #


def _stock_row(company: str, qty: int) -> dict[str, Any]:
    return {
        "fields": [
            {"key": "company_name", "label": "Company", "value": company},
            {"key": "product_code", "label": "Product Code", "value": "MUB6201"},
            {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": qty},
        ]
    }


def _hit_envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "figures": [{"row": 1}],
        "raw_fragment": {
            "fetch": {
                "answers": rows,
                "lookup_companies": [{"id": "co-mocha", "name": MOCHA}, {"id": "co-sorento", "name": SORENTO}],
            }
        },
    }


def _routing(team: str, agent: str) -> dict[str, Any]:
    return {"routing": {"suggested_team": team, "suggested_agent": agent}}


class TestR2QuantityZeroIsNotAMiss:
    def test_a_zero_quantity_row_in_each_company_offers_nothing(self):
        answer = turn_compose.Answer(text="stock rows", question=None)
        out = answer_bridge.apply_silent_company_offer(
            answer,
            envelope=_hit_envelope([_stock_row(MOCHA, 0), _stock_row(SORENTO, 0)]),
            parser=_routing("warehouse", "general_enquiries"),
        )
        assert out == answer

    def test_a_stock_miss_in_one_company_is_the_plain_offer_with_no_picker(self):
        answer = turn_compose.Answer(text="stock rows", question=None)
        out = answer_bridge.apply_silent_company_offer(
            answer,
            envelope=_hit_envelope([_stock_row(MOCHA, 0)]),
            parser=_routing("warehouse", "general_enquiries"),
            db=object(),
            ctx={},
        )
        assert out.text == "stock rows\n\nWould you like me to escalate to *Sorento* warehouse team?"
        assert out.question.kind == "team_pick"

    def test_an_incoming_miss_in_one_company_is_the_plain_offer_with_no_picker(self):
        answer = turn_compose.Answer(text="incoming rows", question=None)
        out = answer_bridge.apply_silent_company_offer(
            answer,
            envelope=_hit_envelope([_stock_row(MOCHA, 60)]),
            parser=_routing("purchasing", "incoming_stock_enquiries"),
            db=object(),
            ctx={},
        )
        assert out.text == "incoming rows\n\nWould you like me to escalate to *Sorento* purchasing team?"
        assert out.question.kind == "team_pick"


# --------------------------------------------------------------------------- #
# R3 + R4 - both companies miss: the picker names both, a bare yes clarifies, a company
# name / code / alias resolves the pick
# --------------------------------------------------------------------------- #


class TestR3BothMissClarifiesTheCompany:
    def _offer(self, conversation) -> Conversation:
        conv = conversation(_both_miss)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask()))
        assert _checked_both(said), said
        return conv

    def test_the_multi_company_picker_copy(self, conversation):
        conv = conversation(_both_miss)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask()))
        # A dated miss words the offer "..., or would you like me to escalate to customer service
        # team?" after the 'another month or dates' hint (a dateless DO ask can no longer miss: it asks which period).
        assert "or would you like me to escalate to customer service team?" in said, said
        assert (
            f"{PICKER_HEAD}\n*Mocha:*\n1. Kia Yee\n*Sorento:*\n2. Jereen Tee\n3. Tay Zhi Yang\n\n{COMPANY_SENTENCE}"
        ) in said, said
        assert "just reply 'yes'" not in said
        import re

        assert not re.search(r"^\d+\. .* \((Mocha|Sorento)", said, re.MULTILINE), "no per-member suffix (M7d)"

    def test_a_bare_yes_asks_which_company_and_assigns_nobody(self, conversation):
        conv = self._offer(conversation)
        result = conv.say("yes", _yes())
        ctx, item, lane = conv.last_lane[0]
        assert lane.get("pending") == {"kind": "company_clarify"}, lane
        assert not any(a.get("kind") == "assign_conversation" for a in lane.get("actions") or []), lane
        assert MEMBER_CLARIFY in _said(result), _said(result)
        # The offer stays open: a member number still picks (n8n re-persists the offer).
        question = conv.open_question()
        assert question.get("kind") == "member_offer", question

    def test_yes_then_mocha_routes_to_mocha(self, conversation):
        conv = self._offer(conversation)
        conv.say("yes", _yes())
        conv.say("mocha", _reply())
        assert conv.company() == conv.ids["mocha"]

    def test_yes_then_a_member_number_routes_to_that_member(self, conversation):
        conv = self._offer(conversation)
        conv.say("yes", _yes())
        conv.say("2", _position(2))
        body = conv.routed()["body"]
        assert body.get("preferred_assignee_id") == "zzt-u-jer", body
        assert body.get("company_id") == conv.ids["sorento"], body

    @pytest.mark.parametrize(
        ("text", "verdict", "company"),
        [
            ("mocha", _reply(), "mocha"),
            ("srt", _reply(), "sorento"),
            ("mch", _reply(), "mocha"),
            ("Mocha team pls", _reply(), "mocha"),
            ("mocha please", _reply(), "mocha"),
            ("yes mocha", _yes(), "mocha"),
            ("ok go with sorento", _yes(), "sorento"),
            (
                "yes please escalate to sorento team",
                _reply(
                    message_type="request_for_help",
                    is_affirmative=True,
                    user_goal="trying to escalate to the sorento team",
                    routing={"suggested_team": "customer_service", "suggested_agent": None, "team_source": None},
                ),
                "sorento",
            ),
            (
                "can you route this to the sorento team please",
                _reply(message_type="request_for_help", user_goal="asking to route this to the sorento team"),
                "sorento",
            ),
        ],
    )
    def test_a_company_name_code_or_alias_resolves_the_pick(self, conversation, text, verdict, company):
        conv = self._offer(conversation)
        conv.say(text, verdict)
        assert conv.company() == conv.ids[company], text

    def test_the_parsers_own_company_pick_is_the_validated_fallback(self, conversation):
        conv = self._offer(conversation)
        conv.say("the coffee brand one", _reply(escalation={"is_escalation_confirmation": False, "company_pick": "Mocha"}))
        assert conv.company() == conv.ids["mocha"]

    def test_a_parser_pick_of_a_company_never_offered_is_refused(self, conversation):
        conv = self._offer(conversation)
        conv.say("the cabana one", _reply(escalation={"is_escalation_confirmation": False, "company_pick": "Cabana"}))
        assert conv.last_lane == [], "a pick outside the offered pool routes nowhere"
        assert conv.open_question().get("kind") == "member_offer"

    def test_a_bare_yes_with_a_hallucinated_pick_still_clarifies(self, conversation):
        """UAC M7c: a reply that strips to nothing is a plain confirmation."""
        conv = self._offer(conversation)
        conv.say("yes", _yes(escalation={"company_pick": "Mocha"}))
        _ctx, _item, lane = conv.last_lane[0]
        assert lane.get("pending") == {"kind": "company_clarify"}, lane

    def test_a_negated_company_is_not_a_pick(self, conversation):
        conv = self._offer(conversation)
        conv.say("not mocha", _reply())
        assert conv.last_lane == [], conv.last_lane

    def test_no_declines(self, conversation):
        """UAC M8e."""
        conv = self._offer(conversation)
        result = conv.say(
            "no", _reply(is_affirmative=False, escalation={"is_escalation_confirmation": False, "escalation_declined": True})
        )
        assert result.branch_kind == "escalation_declined", result.branch_kind
        assert conv.last_lane == []

    def test_a_new_business_query_naming_a_company_is_answered_not_escalated(self, conversation):
        """UAC M8f / M8h."""
        conv = self._offer(conversation)
        result = conv.say(
            "any mocha promotions this month",
            _parser_output(
                message_type="business_query",
                domain_hint="promotion",
                intent_hint="check_promotion",
                entities=[],
                user_goal="asking for mocha promotions this month",
            ),
        )
        assert conv.last_lane == [], conv.last_lane
        assert result.branch_kind != "out_of_scope"


class TestR3RefusalHoldsWhicheverProductRowComesFirst:
    """Main deploy cd220251 (run 36379111785): on a shared xdist worker the Sorento product
    row sat ahead of the Mocha one, the offer said "checked in Sorento and Mocha", and the
    refusal test died in its setup before the rule it pins was ever reached. Both physical
    orders are pinned here, so the refusal is proven in each and the phrase's order is on
    the record as the resolver's, not a promise."""

    @pytest.mark.parametrize(
        ("product_order", "phrase"),
        [
            (("mocha", "sorento"), "checked in Mocha and Sorento"),
            (("sorento", "mocha"), "checked in Sorento and Mocha"),
        ],
    )
    def test_a_parser_pick_of_a_company_never_offered_is_refused(self, conversation, product_order, phrase):
        conv = conversation(_both_miss, product_order=product_order)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask()))
        assert phrase in said, said
        assert f"{PICKER_HEAD}\n*Mocha:*\n1. Kia Yee\n*Sorento:*\n2. Jereen Tee" in said, (
            "the picker's order is the lookup's company order, not the product rows'"
        )
        conv.say("the cabana one", _reply(escalation={"is_escalation_confirmation": False, "company_pick": "Cabana"}))
        assert conv.last_lane == [], "a pick outside the offered pool routes nowhere"
        assert conv.open_question().get("kind") == "member_offer"

    @pytest.mark.parametrize("product_order", [("mocha", "sorento"), ("sorento", "mocha")])
    def test_a_parser_pick_of_an_offered_company_still_routes(self, conversation, product_order):
        conv = conversation(_both_miss, product_order=product_order)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        conv.say("the coffee brand one", _reply(escalation={"is_escalation_confirmation": False, "company_pick": "Mocha"}))
        assert conv.company() == conv.ids["mocha"]


class TestR3BothMissWithNoPicker:
    """A both-company miss off the order lane (no member picker, rev-3): the plain phrase,
    then a bare yes clarifies with the rev-3 plain copy, then a company reply routes."""

    def _offer(self, conversation) -> Conversation:
        conv = conversation(_both_miss)
        said = _said(conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask(agent="general_enquiries")))
        assert _checked_both(said) and PICKER_HEAD not in said, said
        return conv

    def test_yes_clarifies_with_the_plain_copy(self, conversation):
        conv = self._offer(conversation)
        result = conv.say("yes", _yes())
        assert PLAIN_CLARIFY in _said(result), _said(result)
        assert "a number" not in _said(result)

    def test_yes_then_sorento_routes_to_sorento(self, conversation):
        conv = self._offer(conversation)
        conv.say("yes", _yes())
        conv.say("sorento", _reply())
        assert conv.company() == conv.ids["sorento"]

    def test_a_company_word_straight_after_the_offer_routes(self, conversation):
        conv = self._offer(conversation)
        conv.say("mocha", _reply())
        assert conv.company() == conv.ids["mocha"]


# --------------------------------------------------------------------------- #
# R5 - offer hold: an unrecognised reply re-clarifies and keeps the offer open
# --------------------------------------------------------------------------- #


class TestR5OfferHold:
    def test_junk_over_the_plain_offer_re_clarifies_and_keeps_it(self, conversation):
        conv = conversation(_both_miss)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask(agent="general_enquiries"))
        offered = conv.open_question()
        result = conv.say("asdkjh", _reply())
        assert result.branch_kind == "offer_hold", result.branch_kind
        assert (result.reply or {}).get("text") == PLAIN_CLARIFY, result.reply
        assert conv.last_lane == []
        held = conv.open_question()
        held["payload"].pop("ttl", None)  # AC-816: the offer's own clock still ticks
        assert held == offered
        conv.say("sorento", _reply())
        assert conv.company() == conv.ids["sorento"]

    def test_junk_over_the_member_picker_re_clarifies_and_a_number_still_picks(self, conversation):
        conv = conversation(_both_miss)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask())
        result = conv.say("asdkjh", _reply())
        assert result.branch_kind == "offer_hold", result.branch_kind
        assert (result.reply or {}).get("text") == MEMBER_CLARIFY, result.reply
        assert conv.open_question().get("kind") == "member_offer"
        conv.say("1", _position(1))
        body = conv.routed()["body"]
        assert body.get("preferred_assignee_id") == "zzt-u-kia" and body.get("company_id") == conv.ids["mocha"]

    def test_junk_after_the_clarify_holds_again_and_never_routes_by_default(self, conversation):
        conv = conversation(_both_miss)
        conv.say(f"{PRODUCT_CODE} kim seng jaya send yet", _order_ask(agent="general_enquiries"))
        conv.say("yes", _yes())
        result = conv.say("hmm", _reply())
        assert result.branch_kind == "offer_hold", result.branch_kind
        assert conv.last_lane == []
        conv.say("mch", _reply())
        assert conv.company() == conv.ids["mocha"]


# --------------------------------------------------------------------------- #
# R6 - no roster shown: the resolved brand still reaches next-assignee
# --------------------------------------------------------------------------- #


def _ctx(output: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    return {"parse": {"output": output}, "session": {"session_vars": {"variables": variables}}}


class TestR6BrandIsCarriedWhenNoRosterWasShown:
    def test_a_company_offer_whose_row_names_no_brand_carries_the_offer_turns_brand(self):
        ctx = _ctx(
            {
                "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
                "escalation": {"is_escalation_confirmation": True, "carried_brand": "mocha"},
            },
            {
                "routing": {"suggested_team": "marketing_product"},
                "routing_roster_plan": [{"company_id": "co-s", "company_name": SORENTO, "brand_code": None}],
            },
        )
        context = escalation_context({}, ctx=ctx)
        assert context["company_id"] == "co-s" and context["routing_source"] == "prior_state"
        assert context["brand_code"] == "mocha", context

    def test_a_company_pick_whose_row_names_no_brand_takes_the_focus_products_brand(self):
        ctx = _ctx(
            {
                "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
                "escalation": {"is_escalation_confirmation": True, "company_pick": "Mocha"},
            },
            {
                "routing": {"suggested_team": "marketing_product"},
                "routing_roster_plan": [
                    {"company_id": "co-m", "company_name": MOCHA, "brand_code": None},
                    {"company_id": "co-s", "company_name": SORENTO, "brand_code": None},
                ],
            },
        )
        context = escalation_context({"focus_products": [{"raw": "MWCY8610", "canonical_code": "MWCY8610"}]}, ctx=ctx)
        assert context["routing_source"] == "company_pick" and context["brand_code"] is None

        class Seam:
            def product_brand(self, products):
                return {"brand": "MOCHA", "company": MOCHA, "not_found": []}

        filled = escalation_mod._apply_focus_brand(context, Seam())
        assert filled["brand_code"] == "mocha", filled
        assert filled["company_id"] == "co-m", "the picked company still routes"

    def test_a_row_that_names_its_own_brand_keeps_it(self):
        ctx = _ctx(
            {
                "routing": {"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
                "escalation": {"is_escalation_confirmation": True, "carried_brand": "mocha"},
            },
            {
                "routing": {"suggested_team": "marketing_product"},
                "routing_roster_plan": [{"company_id": "co-s", "company_name": SORENTO, "brand_code": "sorento"}],
            },
        )
        assert escalation_context({}, ctx=ctx)["brand_code"] == "sorento"


# --------------------------------------------------------------------------- #
# R7 - the owner's round 4 transcript (28 Sep 00:05 MYT): the both-company DO miss, then
# "how aobut mocha". Rounds 3 and 5 are replayed, unchanged, in
# `test_escalation_brand_from_focus.py` and `test_escalation_round5_escalation_words_win.py`.
# --------------------------------------------------------------------------- #


class TestR7OwnersRound4Transcript:
    def _offer(self, conversation) -> Conversation:
        conv = conversation(_both_miss)
        said = _said(conv.say("DO brand sorneto for cheng huat sentul", _order_ask()))
        assert _checked_both(said), said
        return conv

    def test_how_about_mocha_read_as_a_new_order_ask_is_answered_not_escalated(self, conversation):
        conv = self._offer(conversation)
        result = conv.say(
            "how aobut mocha",
            _parser_output(
                message_type="business_query",
                intent_hint="check_order",
                domain_hint="order",
                entities=[],
                entity_op="reuse",
                # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                date_filter_start="2026-10-01",
                date_filter_end="2026-10-31",
                query_brands=["mocha"],
                user_goal="asking about the mocha orders instead",
            ),
        )
        assert conv.last_lane == [], "a new order ask naming a brand is not a company pick (M8h)"
        assert result.branch_kind == "business_query", result.branch_kind

    def test_how_about_mocha_read_as_a_reply_to_the_offer_routes_to_mocha(self, conversation):
        conv = self._offer(conversation)
        conv.say("how aobut mocha", _reply(escalation={"is_escalation_confirmation": True, "company_pick": "Mocha"}))
        assert conv.company() == conv.ids["mocha"], "never the default customer service pool"
