"""#865: escalation resolves the brand from the product the conversation is about.

Owner ruling 27 Sep 2026 ("yeah go for escalation"), fix option 1 of the root-cause report
on #865 ("Root cause: null brand on escalation after a spec answer (backup of 25 Sep)").

The defect, measured on the 25 Sep backup: contact 900000107, 24 Sep 2026 09:16 MYT.
Turn 1 asked for the stainless steel grade of SRTKS8650A (a SORENTO product) and was
answered with the product card; turn 2 said "Please esculate to Marketing". The
escalation reached `/external/next-assignee` with `brand_code: null`, drew from the
un-narrowed cursor, and assigned the mocha-only member of Marketing Product. The brand
had been computed on turn 1 and dropped at the end of it: the five-key session holds the
product, not its brand, and nothing re-read it. It survived only when turn 1 happened to
mint an offer that stamped it (#1108's `carried_brand`), i.e. only when the product turn
FAILED.

The fix reads the brand off the product row at the point of use: this turn's named
product, else the product in focus. The focus rules already drop that product on a topic
reset or a newer product, so nothing here decides when the carry ends.

Every test drives the REAL engine (`engine.run_turn`) with the parser stubbed to the
recorded verdicts, over a freshly seeded roster shaped like prod (Marketing Product: Kia
Yee = mocha, Tay Zhi Yang = sorento, cabana; Packing List: Lucas = mocha, Jereen =
sorento, cabana), and the REAL `/external/next-assignee` handler draws the assignee.
Postgres only (`tests/chatbot/conftest.py`'s blank-schema `session_factory`).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import SessionVars

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import (
    _capture_real_next_assignee,
    _capture_sla,
    _second_turn_envelope,
    _seed_contact,
    _seed_escalation_team,
    _seed_packing_list_team,
    _db,
    _seed_product_with_brand,
    _yes_verdict,
)

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

MASTER_PRODUCTS_TOOL = "crm_master_products_list"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _seed_marketing_product_team(session_factory) -> dict[str, str]:
    """Marketing Product tier 1 as the backup holds it: two round-robin members, Kia Yee
    tagged only `mocha` (sort order 1, so a whole-team draw on a fresh cursor lands on
    her) and Tay Zhi Yang tagged `sorento` and `cabana`."""
    return _seed_escalation_team(
        session_factory,
        agent_code="general_enquiries",
        team_code="marketing_product",
        team_label="Marketing - Product",
        members=[("Kia Yee", ["mocha"]), ("Tay Zhi Yang", ["sorento", "cabana"])],
    )


def _stub_product_card(monkeypatch) -> None:
    """Turn 1's recorded tool answer: the master products tool returns the product card
    for whatever ids it was asked about (a clean HIT, so turn 1 mints no question). Every
    other probe, incoming included, answers nothing."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == MASTER_PRODUCTS_TOOL:
            return json.dumps(
                {
                    "items": [
                        {
                            "title": "ZZT SORENTO S/STEEL SINK",
                            "fields": [
                                {"key": "product_code", "label": "Product Code", "value": "ZZT"},
                                {"key": "material", "label": "Material", "value": "stainless_steel"},
                            ],
                            "flags": {"discontinued": True},
                        }
                    ],
                    "has_result": True,
                    "attachments": [],
                    "action_links": [],
                }
            )
        return json.dumps({"answers": [], "items": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)


def _spec_verdict(code: str) -> dict[str, Any]:
    """Turn 1 of the 24 Sep conversation, as parsed: a master_products business query
    naming one product, routed to purchasing (the domain's escalation team)."""
    return verdict(
        message_type="business_query",
        domain_hint="master_products",
        intent_hint="check_product",
        entities=[entity(code, hint="product", confident=True)],
        routing={"suggested_team": "purchasing", "suggested_agent": "purchasing"},
    )


def _escalate_to_marketing_verdict(**overrides: Any) -> dict[str, Any]:
    """Turn 2 of the 24 Sep conversation, as parsed: "Please esculate to Marketing". A help
    request naming the team, no entities, no brand word, no confirmation."""
    base = verdict(
        message_type="request_for_help",
        domain_hint=None,
        intent_hint=None,
        entities=[],
        routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )
    base.update(overrides)
    return base


def _envelope_for(message_id: str, text_: str) -> Any:
    return _second_turn_envelope(message_id=message_id, text=text_)


def _session_vars(session_factory) -> dict[str, Any]:
    db = session_factory()
    try:
        row = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).first()
        return dict(row[0] or {}) if row else {}
    finally:
        db.close()


def _looked_up_routing(session_factory, turn_id: str) -> dict[str, Any] | None:
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        records = list(row.trace or []) if row is not None else []
    finally:
        db.close()
    looked_up = [r for r in records if r.get("stage") == "looked_up"]
    assert looked_up, records
    return (looked_up[-1].get("facts") or {}).get("routing")


def _run_spec_hit(session_factory, monkeypatch, stub_parser, stub_access, *, phone: str, code: str):
    _seed_contact(session_factory, phone=phone)
    _stub_product_card(monkeypatch)
    stub_access()
    stub_parser(_spec_verdict(code))
    turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert turn1.branch_kind == "business_query", turn1.branch_kind
    return turn1


# --------------------------------------------------------------------------- #
# 1. The 24 Sep replay: spec answered, then "escalate to marketing". RED on main.
# --------------------------------------------------------------------------- #


class TestReplay24SepSpecHitThenEscalate:
    def test_24sep_replay_spec_hit_then_escalate_to_marketing_draws_the_sorento_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000036", code="SRTKS8650A"
        )
        # Turn 1 minted nothing (a clean HIT asks nothing), exactly as recorded.
        assert _session_vars(session_factory).get("open_question") is None

        stub_parser(_escalate_to_marketing_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn2 = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-2", "Please esculate to Marketing"),
            session_factory=session_factory,
        )

        assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
        assert len(calls) == 1, calls
        body, response = calls[0]["body"], calls[0]["response"]
        assert body["team_code"] == "marketing_product", body
        assert body["brand_code"] == "sorento", (
            f"the escalation is about SRTKS8650A, a SORENTO product; the brand must reach "
            f"next-assignee (24 Sep: it arrived null): {body!r}"
        )
        assert response.get("assignee_name") == "ZZT Tay Zhi Yang", (
            f"a SORENTO product escalated to marketing must draw the sorento-tagged member, "
            f"not the mocha one on the whole-team cursor: {response!r}"
        )
        assert response.get("brand_matched") is True, response

    def test_24sep_replay_the_trace_records_the_next_assignee_body_and_cursor_key(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000039", code="SRTKS8650A"
        )

        stub_parser(_escalate_to_marketing_verdict())
        _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn2 = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-2", "Please esculate to Marketing"),
            session_factory=session_factory,
        )

        routing = _looked_up_routing(session_factory, turn2.turn_id)
        assert routing is not None, "the escalation trace must record the next-assignee routing"
        assert routing["team_code"] == "marketing_product", routing
        assert routing["brand_code"] == "sorento", routing
        assert routing["routing_source"] == "focus_product", routing
        # The brand-narrowed pool keeps its own cursor (`user_service.brand_pool_key`).
        assert routing["cursor_key"] == "~b:sorento", routing
        assert routing["assignee_name"] == "ZZT Tay Zhi Yang", routing


# --------------------------------------------------------------------------- #
# 2. The proven path still holds: an offer accepted with a stamped brand.
# --------------------------------------------------------------------------- #


class TestProvenPathOfferAcceptedWithStampedBrand:
    def test_photo_miss_offer_accepted_with_a_stamped_brand_still_draws_the_brand_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Contact 900000061, 24 Sep 16:25 MYT: a spec/photo MISS minted "escalate to
        marketing product?", its `team_pick` stamped `brand_code: sorento`, and "Yes"
        drew Tay Zhi Yang. It must keep doing so, and the payload still carries the brand
        the offer turn resolved."""
        from tests.chatbot.test_escalation_agent_carry import _stub_incoming_probe_empty
        from tests.chatbot.test_product_attachment_picker_stamp import _seed_attachment_type

        _seed_contact(session_factory, phone="+60900000035")
        _seed_product_with_brand(session_factory, code="SRTUB6503", brand_code="sorento")
        _seed_attachment_type(session_factory, "Product Photos")
        _seed_marketing_product_team(session_factory)
        _stub_incoming_probe_empty(monkeypatch)
        stub_access()
        stub_parser(
            verdict(
                domain_hint="product_attachment",
                intent_hint="check_product_attachment",
                entities=[
                    entity("SRTUB6503", hint="product", confident=True),
                    entity("product photos", hint="attachment_type", canonical_code="photo", confident=True),
                ],
                routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
            )
        )
        turn1 = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert turn1.branch_kind == "business_query", turn1.branch_kind
        offer = _session_vars(session_factory).get("open_question") or {}
        assert offer.get("kind") == "team_pick", offer
        assert (offer.get("payload") or {}).get("brand_code") == "sorento", offer

        stub_parser(_yes_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn2 = engine_mod.run_turn(_second_turn_envelope(), session_factory=session_factory)

        assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"]["brand_code"] == "sorento", calls[0]["body"]
        assert calls[0]["response"].get("assignee_name") == "ZZT Tay Zhi Yang", calls[0]["response"]


# --------------------------------------------------------------------------- #
# 3. The sibling: "eta" after a product turn. RED on main.
# --------------------------------------------------------------------------- #


def _seed_settled_focus(session_factory, monkeypatch, stub_access, *, phone: str) -> None:
    """The session a product turn leaves when its product is SETTLED (the focus entry
    carries the row `uuid`): SRTKS7646, a SORENTO product, over the Packing List roster.
    Seeded directly, the pattern `test_escalation_agent_carry.py` uses for turn 1."""
    from app.models.product import Product

    from tests.chatbot.test_escalation_agent_carry import _stub_incoming_probe_empty

    _seed_contact(session_factory, phone=phone)
    _seed_product_with_brand(session_factory, code="SRTKS7646", brand_code="SORENTO")
    _seed_packing_list_team(session_factory)
    _stub_incoming_probe_empty(monkeypatch)
    stub_access()
    db = _db(session_factory)
    product_id = db.query(Product.id).filter(Product.product_code == "SRTKS7646").scalar()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :c"),
        {
            "sv": json.dumps(
                {
                    "focus": {
                        "products": [
                            {
                                "raw": "SRTKS7646",
                                "hint": "product",
                                "canonical_code": "SRTKS7646",
                                "uuid": product_id,
                                "current_message": False,
                            }
                        ],
                        "domains": ["master_products"],
                    },
                    "open_question": None,
                    "ideation": None,
                    "access_levels": [],
                    "contains_flyer": False,
                }
            ),
            "c": str(CONTACT_ID),
        },
    )
    db.commit()


class TestSiblingEtaAfterAProductTurn:
    def _eta_then_yes(self, session_factory, stub_parser, monkeypatch) -> tuple[dict[str, Any], list]:
        stub_parser(
            verdict(
                domain_hint="incoming",
                intent_hint="check_incoming",
                entities=[],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        eta = engine_mod.run_turn(_envelope_for("ZZT-msg-865-eta", "eta"), session_factory=session_factory)
        assert eta.branch_kind == "business_query", eta.branch_kind
        offer = _session_vars(session_factory).get("open_question") or {}

        stub_parser(_yes_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        yes = engine_mod.run_turn(_envelope_for("ZZT-msg-865-yes", "yes"), session_factory=session_factory)
        assert yes.branch_kind == "out_of_scope", yes.branch_kind
        return offer, calls

    def test_eta_after_a_settled_product_mints_the_offer_with_the_focus_products_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Contact 900000059, 23 Sep 13:03 MYT: "SRTKS7646" answered (SORENTO), then "eta"
        named no product; the incoming lane used the focus product, missed, and offered
        purchasing with `payload.brand_code: null`, because the gate's brand comes only
        from rows resolved on THAT turn and a SETTLED focus product (one carrying its row
        `uuid`) is never re-resolved (`with_carried_entities(unsettled_only=True)`). "Yes"
        then drew the mocha-only Packing List member. The focus is seeded as the product
        turn left it, the pattern `test_escalation_agent_carry.py` uses for turn 1."""
        _seed_settled_focus(session_factory, monkeypatch, stub_access, phone="+60900000037")

        offer, calls = self._eta_then_yes(session_factory, stub_parser, monkeypatch)

        assert offer.get("kind") == "team_pick", offer
        assert (offer.get("payload") or {}).get("brand_code") == "sorento", (
            f"the offer minted on the 'eta' turn must carry the focus product's brand: {offer!r}"
        )
        assert len(calls) == 1, calls
        assert calls[0]["body"]["team_code"] == "purchasing", calls[0]["body"]
        assert calls[0]["body"]["brand_code"] == "sorento", calls[0]["body"]
        assert calls[0]["response"].get("assignee_name") == "ZZT Jereen", calls[0]["response"]

    def test_a_fanned_out_miss_after_a_settled_product_stamps_every_team_option_with_its_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The `_team_pick_question` mint site (`turn/compose.py`): "stock and eta?" after
        the same settled product fans out to two domains, both miss, and the multi-team
        pick is minted by compose rather than the bridge. No resolver runs on this turn,
        so its brand comes from the focus product (`TurnContext.routing_brand`)."""
        _seed_settled_focus(session_factory, monkeypatch, stub_access, phone="+60900000025")
        stub_parser(
            verdict(
                asks=[{"domain": "inventory"}, {"domain": "incoming"}],
                domain_hint=None,
                intent_hint=None,
                entities=[],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        turn = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-fan", "stock and eta?"), session_factory=session_factory
        )
        assert turn.branch_kind == "business_query", turn.branch_kind

        question = _session_vars(session_factory).get("open_question") or {}
        assert question.get("kind") == "team_pick", question
        stamped = [
            (o.get("payload") or {}).get("brand_code")
            for o in question.get("options") or []
            if not (o.get("payload") or {}).get("hold")
        ]
        if not stamped:  # a single missed team is yes/no, stamped on the top-level payload
            stamped = [(question.get("payload") or {}).get("brand_code")]
        assert stamped and all(b == "sorento" for b in stamped), (
            f"every team option compose mints must carry the focus product's brand: {question!r}"
        )

    def test_eta_after_a_typed_product_hit_keeps_carrying_the_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """The same journey with turn 1 run for real: today's focus keeps the typed code
        unsettled, the resolver re-resolves it on "eta" and the gate already carries the
        brand. A guard that the focus fill does not disturb that path."""
        from tests.chatbot.test_escalation_agent_carry import _stub_incoming_probe_empty

        _seed_product_with_brand(session_factory, code="SRTKS7646", brand_code="SORENTO")
        _seed_packing_list_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000031", code="SRTKS7646"
        )
        _stub_incoming_probe_empty(monkeypatch)

        offer, calls = self._eta_then_yes(session_factory, stub_parser, monkeypatch)

        assert (offer.get("payload") or {}).get("brand_code") == "sorento", offer
        assert calls[0]["body"]["brand_code"] == "sorento", calls[0]["body"]
        assert calls[0]["response"].get("assignee_name") == "ZZT Jereen", calls[0]["response"]


# --------------------------------------------------------------------------- #
# 4. When the carry ends: a topic reset, or a newer product.
# --------------------------------------------------------------------------- #


class TestTheCarryEndsOnATopicResetOrANewerProduct:
    def test_a_topic_reset_escalation_carries_no_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000026", code="SRTKS8650A"
        )

        stub_parser(_escalate_to_marketing_verdict(topic_reset=True))
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn2 = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-2", "different thing, escalate to marketing"),
            session_factory=session_factory,
        )

        assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"]["brand_code"] is None, (
            f"a topic reset ends the conversation about SRTKS8650A; its brand must not "
            f"leak into the new topic's escalation: {calls[0]['body']!r}"
        )
        routing = _looked_up_routing(session_factory, turn2.turn_id)
        assert routing["routing_source"] == "none", routing

    def test_a_newer_product_replaces_the_focus_and_its_brand_is_the_one_drawn(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """A second product turn about a MOCHA code replaces the SORENTO one in focus, so
        "escalate to marketing" after it draws the mocha member. (A help request that names
        its own product is retyped to a business query by the head today, #866's D1, which
        this lane does not take on; the newer product is therefore named on its own turn.)"""
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_product_with_brand(session_factory, code="MWC7625", brand_code="MOCHA")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000029", code="SRTKS8650A"
        )
        stub_parser(_spec_verdict("MWC7625"))
        newer = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-newer", "and MWC7625?"), session_factory=session_factory
        )
        assert newer.branch_kind == "business_query", newer.branch_kind

        stub_parser(_escalate_to_marketing_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn3 = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-3", "Please esculate to Marketing"),
            session_factory=session_factory,
        )

        assert turn3.branch_kind == "out_of_scope", turn3.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"]["brand_code"] == "mocha", calls[0]["body"]
        assert calls[0]["response"].get("assignee_name") == "ZZT Kia Yee", calls[0]["response"]


class TestAStatedBrandOutranksTheFocusProduct:
    def test_a_mocha_catalogue_escalation_after_a_sorento_spec_answer_draws_the_mocha_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Round 2, S1 (reviewer pass at df577453): turn 1 answers a SORENTO spec, turn 2
        says "I need the Mocha catalogue, escalate to marketing" (`query_brands: ["mocha"]`,
        no entities). The customer's explicit brand on THIS turn is the `stated_brand` rung,
        which works on main; the product carried from turn 1 must not outrank it."""
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000030", code="SRTKS8650A"
        )

        stub_parser(_escalate_to_marketing_verdict(query_brands=["mocha"]))
        calls = _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        turn2 = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-mocha", "I need the Mocha catalogue, escalate to marketing"),
            session_factory=session_factory,
        )

        assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"]["brand_code"] == "mocha", (
            f"the customer named Mocha on this turn; a SORENTO product carried from the "
            f"previous turn must not outrank it: {calls[0]['body']!r}"
        )
        assert calls[0]["response"].get("assignee_name") == "ZZT Kia Yee", calls[0]["response"]
        routing = _looked_up_routing(session_factory, turn2.turn_id)
        assert routing["routing_source"] == "stated_brand", routing


class TestTheDryRunPreviewsTheSameBrand:
    def test_24sep_replay_as_a_console_dry_run_previews_the_sorento_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Round 2, S2 (kill test K9 at df577453): the console runs dry (`is_test`), and
        `_preview_routing` must apply the same focus-brand rung as the live draw, or the
        preview the owner checks drifts from the draw it previews."""
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000028", code="SRTKS8650A"
        )

        stub_parser(_escalate_to_marketing_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        sla = _capture_sla(monkeypatch)
        envelope = _envelope_for("ZZT-msg-865-dry", "Please esculate to Marketing")
        envelope.is_test = True
        assert envelope.dry_run is True
        turn2 = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"].get("preview") is True, calls[0]["body"]
        assert calls[0]["body"]["brand_code"] == "sorento", calls[0]["body"]
        assert calls[0]["response"].get("assignee_name") == "ZZT Tay Zhi Yang", calls[0]["response"]
        assert sla == [], "a dry run writes no SLA row"
        routing = _looked_up_routing(session_factory, turn2.turn_id)
        assert routing["brand_code"] == "sorento", routing
        assert routing["routing_source"] == "focus_product", routing
        assert routing["cursor_key"] == "~b:sorento", routing


class TestTheFocusBrandIsReadOnlyWhenAnOfferIsMinted:
    def test_a_hit_turn_over_a_settled_product_never_reads_the_focus_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Round 2, N2: the products x brands read feeds only a minted `team_pick`. A turn
        over a settled focus product that is answered (a HIT) mints nothing, so the read
        must not run at all."""
        _seed_settled_focus(session_factory, monkeypatch, stub_access, phone="+60900000024")
        _stub_product_card(monkeypatch)
        reads: list[Any] = []
        real = engine_mod._focus_brand

        def counting(db: Any, focus: Any) -> Any:
            reads.append(focus)
            return real(db, focus)

        monkeypatch.setattr(engine_mod, "_focus_brand", counting)
        stub_parser(
            verdict(
                message_type="business_query",
                domain_hint="master_products",
                intent_hint="check_product",
                entities=[],
                routing={"suggested_team": "purchasing", "suggested_agent": "purchasing"},
            )
        )
        turn = engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-hit", "what grade is it?"), session_factory=session_factory
        )

        assert turn.branch_kind == "business_query", turn.branch_kind
        assert _session_vars(session_factory).get("open_question") is None
        assert reads == [], f"no offer was minted, so the focus brand must not be read: {len(reads)}"

    def test_the_fanned_out_miss_reads_it_once_when_it_mints(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_settled_focus(session_factory, monkeypatch, stub_access, phone="+60900000040")
        reads: list[Any] = []
        real = engine_mod._focus_brand

        def counting(db: Any, focus: Any) -> Any:
            reads.append(focus)
            return real(db, focus)

        monkeypatch.setattr(engine_mod, "_focus_brand", counting)
        stub_parser(
            verdict(
                asks=[{"domain": "inventory"}, {"domain": "incoming"}],
                domain_hint=None,
                intent_hint=None,
                entities=[],
                routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
            )
        )
        engine_mod.run_turn(_envelope_for("ZZT-msg-865-fan2", "stock and eta?"), session_factory=session_factory)

        assert (_session_vars(session_factory).get("open_question") or {}).get("kind") == "team_pick"
        assert len(reads) == 1, len(reads)


# --------------------------------------------------------------------------- #
# 5. Contract 129: the five-key session wire shape is unchanged.
# --------------------------------------------------------------------------- #


class TestContract129FiveKeySessionUnchanged:
    def test_the_session_written_by_both_turns_is_exactly_the_five_keys_with_no_brand_on_focus(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_product_with_brand(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        _seed_marketing_product_team(session_factory)
        _run_spec_hit(
            session_factory, monkeypatch, stub_parser, stub_access, phone="+60900000027", code="SRTKS8650A"
        )
        five = {"focus", "open_question", "ideation", "access_levels", "contains_flyer"}
        assert set(SessionVars.model_fields) == five

        after_turn1 = _session_vars(session_factory)
        assert set(after_turn1) == five, after_turn1
        SessionVars.model_validate(after_turn1)  # extra="forbid": nothing new rides along

        stub_parser(_escalate_to_marketing_verdict())
        _capture_real_next_assignee(monkeypatch)
        _capture_sla(monkeypatch)
        engine_mod.run_turn(
            _envelope_for("ZZT-msg-865-2", "Please esculate to Marketing"),
            session_factory=session_factory,
        )

        after_turn2 = _session_vars(session_factory)
        assert set(after_turn2) == five, after_turn2
        SessionVars.model_validate(after_turn2)
        for snapshot in (after_turn1, after_turn2):
            for product in (snapshot.get("focus") or {}).get("products") or []:
                assert "brand_code" not in product and "brand" not in product, (
                    f"the brand is read off the product row, never persisted beside the "
                    f"product (option 3 was rejected): {product!r}"
                )


# --------------------------------------------------------------------------- #
# 6. The rung itself, pure.
# --------------------------------------------------------------------------- #


class _Seams:
    def __init__(self, brand: Any) -> None:
        self.brand = brand
        self.asked: list[Any] = []

    def product_brand(self, products: Any) -> Any:
        self.asked.append(products)
        if isinstance(self.brand, Exception):
            raise self.brand
        return self.brand


class TestApplyFocusBrand:
    FOCUS = [{"raw": "SRTKS8650A", "hint": "product", "canonical_code": "SRTKS8650A"}]

    def _item(self, source: str, brand: Any = None) -> dict[str, Any]:
        return {"routing_source": source, "brand_code": brand, "focus_products": self.FOCUS}

    def test_it_replaces_none_and_the_offer_carry(self) -> None:
        from app.services.chatbot.lanes.escalation import _apply_focus_brand

        for source, before in (("none", None), ("carried_brand", "mocha")):
            out = _apply_focus_brand(self._item(source, before), _Seams("SORENTO"))
            assert out["brand_code"] == "sorento", (source, out)
            assert out["routing_source"] == "focus_product", (source, out)

    def test_a_brand_the_customer_stated_this_turn_keeps_its_own_brand(self) -> None:
        """Round 2, S1: `stated_brand` is this turn's own `query_brands`, the customer's
        explicit word. The focus product, named this turn or carried, never outranks it."""
        from app.services.chatbot.lanes.escalation import _apply_focus_brand

        for current in (False, True):
            seams = _Seams("sorento")
            item = {
                "routing_source": "stated_brand",
                "brand_code": "mocha",
                "focus_products": [{**self.FOCUS[0], "current_message": current}],
            }
            assert _apply_focus_brand(item, seams) == item, current
            assert seams.asked == [], current

    def test_a_roster_arm_keeps_its_own_brand(self) -> None:
        from app.services.chatbot.lanes.escalation import _apply_focus_brand

        for source in ("picked_member", "company_pick", "prior_state", "multi_company_unpicked"):
            seams = _Seams("sorento")
            item = self._item(source, "mocha")
            assert _apply_focus_brand(item, seams) == item, source
            assert seams.asked == [], source

    def test_no_brand_no_focus_no_seam_or_a_failed_read_leave_the_item_alone(self) -> None:
        from app.services.chatbot.lanes.escalation import _apply_focus_brand

        item = self._item("carried_brand", "mocha")
        assert _apply_focus_brand(item, _Seams(None)) == item
        assert _apply_focus_brand(item, _Seams(RuntimeError("boom"))) == item
        assert _apply_focus_brand(item, object()) == item
        bare = {"routing_source": "none", "brand_code": None, "focus_products": []}
        assert _apply_focus_brand(bare, _Seams("sorento")) == bare


class TestFocusProductBrandRead:
    def test_products_that_disagree_on_a_brand_name_none(self, session_factory) -> None:
        from app.services.chatbot.lanes.escalation_services import focus_product_brand

        _seed_product_with_brand(session_factory, code="ZZT865-SRT", brand_code="SORENTO")
        _seed_product_with_brand(session_factory, code="ZZT865-MCH", brand_code="MOCHA")
        _seed_product_with_brand(session_factory, code="ZZT865-NONE", brand_code=None)
        db = _db(session_factory)  # the turn's own sessions carry the contact's company scope
        try:
            one = [{"raw": "zzt865-srt", "hint": "product", "canonical_code": None}]
            assert focus_product_brand(db, one) == "sorento"
            two = one + [{"raw": "ZZT865-MCH", "hint": "product", "canonical_code": "ZZT865-MCH"}]
            assert focus_product_brand(db, two) is None
            assert focus_product_brand(db, [{"raw": "ZZT865-NONE", "hint": "product"}]) is None
            # A malformed uuid falls back to the code instead of aborting the transaction.
            bad = [{"uuid": "not-a-uuid", "raw": "ZZT865-SRT", "hint": "product"}]
            assert focus_product_brand(db, bad) == "sorento"
            assert focus_product_brand(db, []) is None
        finally:
            db.close()

    def test_a_settled_entry_matches_by_its_uuid_alone(self, session_factory) -> None:
        """Round 2, N1: a settled focus entry names its row by `uuid`. When its `raw` is
        not the product code and it carries no `canonical_code`, the uuid alone must still
        find the row, or the settled product would lose its brand."""
        from app.models.product import Product
        from app.services.chatbot.lanes.escalation_services import focus_product_brand

        _seed_product_with_brand(session_factory, code="ZZT865-UID", brand_code="SORENTO")
        db = _db(session_factory)
        try:
            product_id = db.query(Product.id).filter(Product.product_code == "ZZT865-UID").scalar()
            settled = [
                {"uuid": str(product_id), "raw": "the kitchen sink", "canonical_code": None, "hint": "product"}
            ]
            assert focus_product_brand(db, settled) == "sorento"
            no_code = [{"uuid": str(product_id).upper(), "hint": "product"}]
            assert focus_product_brand(db, no_code) == "sorento"
        finally:
            db.close()


# --------------------------------------------------------------------------- #
# 7. Fix round 3: the owner's console retest of 27 Sep (about 14:39 MYT).
#
# The owner's two console messages, replayed the way the console runs them: both turns
# through `console_service.run_console_turn` (a dry run, `is_test`, `ingress=console`),
# the same contact, the second turn sent the `session_vars` the first one returned (what
# `useChatbotConsole` does between turns). The parser is stubbed to the verdicts the
# owner's trace recorded; everything after it is real.
#
# Diagnosis: the console DOES carry the focus between dry runs (turn 2 `remembered_keys:
# 1` is the focus), so hypothesis 1 does not hold. Hypothesis 2 does, one layer down:
# "check spec srtwc286" leaves the focus entry UNSETTLED (`raw`/`canonical_code`
# "srtwc286", no `uuid`: master_products does not narrow on product, so
# `turn/apply.py`'s `focus_settles_product` never runs), and the brand read matched an
# unsettled code by EXACT `product_code` only, so "SRTWC286" never found "SRTWC286-SH".
# --------------------------------------------------------------------------- #


def _seed_branded(session_factory, *, code: str, brand_code: str) -> None:
    """`_seed_product_with_brand`, reusing a brand row an earlier call already seeded
    (brand codes are unique per company)."""
    import uuid as uuid_mod

    from app.models.product import Brand, Product

    db = _db(session_factory)
    try:
        brand_id = db.query(Brand.id).filter(Brand.brand_code == brand_code).scalar()
        if brand_id is None:
            db.close()
            _seed_product_with_brand(session_factory, code=code, brand_code=brand_code)
            return
        template = db.query(Product).filter(Product.brand_id == brand_id).first()
        db.add(
            Product(
                id=str(uuid_mod.uuid4()),
                product_code=code,
                product_name=f"ZZT {code}",
                category_id=template.category_id,
                base_uom_id=template.base_uom_id,
                brand_id=brand_id,
                list_price=1,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_borrowable_envelope(session_factory) -> None:
    """The contact's last REAL inbound envelope, the one `console_service._borrow_envelope`
    reads (a console turn cannot run without one)."""
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    try:
        db.add(
            ChatbotTurn(
                contact_respond_id=str(CONTACT_ID),
                message_id="ZZT-865-r3-seed",
                ingress="webhook",
                envelope=json.loads(_envelope().model_dump_json()),
                is_test=False,
                status="done",
                stage="sent",
                branch_kind="business_query",
            )
        )
        db.commit()
    finally:
        db.close()


def _stub_any_product_tool(monkeypatch) -> None:
    """The product and stock tools answer with a card for whatever they were asked about
    (a clean HIT, as the owner's turn 1 was); every other probe answers nothing."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name in (MASTER_PRODUCTS_TOOL, "crm_inventory_stock_balance_list"):
            return json.dumps(
                {
                    "items": [
                        {
                            "title": "ZZT SORENTO PRODUCT",
                            "fields": [{"key": "product_code", "label": "Product Code", "value": "ZZT"}],
                        }
                    ],
                    "has_result": True,
                    "attachments": [],
                    "action_links": [],
                }
            )
        return json.dumps({"answers": [], "items": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)


def _product_verdict(domain: str, intent: str, token: str, **extra: Any) -> dict[str, Any]:
    return verdict(
        message_type="business_query",
        domain_hint=domain,
        intent_hint=intent,
        entities=[entity(token, hint="product", confident=True)],
        routing={"suggested_team": "purchasing", "suggested_agent": "purchasing"},
        **extra,
    )


def _owners_escalation_verdict() -> dict[str, Any]:
    """Turn 2 as the owner's trace recorded it: "please escalate to marketing team",
    `request_for_help`, the domain and intent carried from turn 1, no entities."""
    return verdict(
        message_type="request_for_help",
        domain_hint="master_products",
        intent_hint="check_product",
        entities=[],
        routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


def _console_two_turns(
    session_factory, monkeypatch, stub_parser, stub_access, *, first_text: str, first_verdict: dict[str, Any]
):
    from app.services.chatbot import console_service

    _seed_contact(session_factory, phone="+60900000032")
    _seed_borrowable_envelope(session_factory)
    _seed_marketing_product_team(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    stub_access()
    _stub_any_product_tool(monkeypatch)

    db = session_factory()
    try:
        stub_parser(first_verdict)
        # `{}` is what the console page sends on its first turn (`useChatbotConsole`).
        turn1 = console_service.run_console_turn(
            db, contact_respond_id=str(CONTACT_ID), text=first_text, session_vars={}, run_id="zzt-865-r3"
        )
        assert turn1.branch_kind == "business_query", turn1.branch_kind

        stub_parser(_owners_escalation_verdict())
        calls = _capture_real_next_assignee(monkeypatch)
        sla = _capture_sla(monkeypatch)
        turn2 = console_service.run_console_turn(
            db,
            contact_respond_id=str(CONTACT_ID),
            text="please escalate to marketing team",
            session_vars=turn1.session_vars,
            run_id="zzt-865-r3",
        )
    finally:
        db.close()
    assert turn2.branch_kind == "out_of_scope", turn2.branch_kind
    assert sla == [], "a console turn is a dry run: no SLA row"
    return turn1, turn2, calls


def _assert_sorento_draw(session_factory, turn2, calls) -> None:
    assert len(calls) == 1, calls
    body, response = calls[0]["body"], calls[0]["response"]
    assert body.get("preview") is True, body
    assert body["brand_code"] == "sorento", body
    assert response.get("assignee_name") == "ZZT Tay Zhi Yang", response
    assert response.get("brand_matched") is True, response
    routing = _looked_up_routing(session_factory, turn2.turn_id)
    assert routing["brand_code"] == "sorento", routing
    assert routing["routing_source"] == "focus_product", routing
    assert str(routing["cursor_key"]).endswith("~b:sorento"), routing
    assert routing["assignee_name"] == "ZZT Tay Zhi Yang", routing


class TestTheOwnersConsoleRetest27Sep:
    def test_check_spec_srtwc286_then_escalate_to_marketing_draws_the_sorento_member(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R1, the owner's exact two messages. On 4517e6edd: `brand_code: null`,
        `routing_source: none`, the mocha-only member (Kia Yee)."""
        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        turn1, turn2, calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text="check spec srtwc286",
            first_verdict=_product_verdict("master_products", "check_product", "srtwc286"),
        )
        _assert_sorento_draw(session_factory, turn2, calls)

    @pytest.mark.parametrize(
        ("text_", "domain", "intent", "extra"),
        [
            ("check spec {code}", "master_products", "check_product", {}),
            ("what is the price of {code}", "master_products", "check_product", {"requested_attributes": ["price"]}),
            ("stock for {code}", "inventory", "check_stock", {}),
            ("What is the stainless steel grade of {code}?", "master_products", "check_product", {}),
        ],
        ids=["spec", "price", "stock", "script-grade"],
    )
    @pytest.mark.parametrize("token", ["SRTKS8650A", "srtks8650"], ids=["exact", "partial"])
    def test_any_single_code_answer_then_escalate_draws_the_sorento_member(
        self, session_factory, stub_parser, stub_access, monkeypatch, text_, domain, intent, extra, token
    ) -> None:
        """R1: every phrasing that shows exactly one product, the full code or the part
        of it a customer types, then the owner's escalation."""
        _seed_branded(session_factory, code="SRTKS8650A", brand_code="SORENTO")
        turn1, turn2, calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text=text_.format(code=token),
            first_verdict=_product_verdict(domain, intent, token, **extra),
        )
        _assert_sorento_draw(session_factory, turn2, calls)

    def test_a_spec_answer_listing_several_products_of_one_brand_keeps_that_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        _seed_branded(session_factory, code="SRTWC286-PT", brand_code="SORENTO")
        turn1, turn2, calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text="check spec srtwc286",
            first_verdict=_product_verdict("master_products", "check_product", "srtwc286"),
        )
        _assert_sorento_draw(session_factory, turn2, calls)

    def test_a_spec_answer_listing_products_of_several_brands_names_none(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        _seed_branded(session_factory, code="SRTWC286-MC", brand_code="MOCHA")
        turn1, turn2, calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text="check spec srtwc286",
            first_verdict=_product_verdict("master_products", "check_product", "srtwc286"),
        )
        assert calls[0]["body"]["brand_code"] is None, calls[0]["body"]
        routing = _looked_up_routing(session_factory, turn2.turn_id)
        assert routing["routing_source"] == "none", routing


class TestTheConsoleCarriesWhatTheLivePathWrites:
    def test_the_console_hands_the_next_turn_the_same_focus_the_live_turn_writes(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R2: a dry run writes no session, so the console carries its own: the
        `session_vars` a console turn returns (the turn's `session_patch`) is what the page
        sends back on the next turn. It must be the memory the live turn would have
        written, focus product and all, or the hand test cannot reproduce live routing."""
        from app.services.chatbot import console_service

        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        _seed_contact(session_factory, phone="+60900000033")
        _seed_borrowable_envelope(session_factory)
        monkeypatch.setattr(console_service, "SessionLocal", session_factory)
        stub_access()
        _stub_any_product_tool(monkeypatch)
        stub_parser(_product_verdict("master_products", "check_product", "srtwc286"))

        db = session_factory()
        try:
            dry = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text="check spec srtwc286", session_vars={}, run_id="zzt-r2"
            )
        finally:
            db.close()
        assert _session_vars(session_factory) == {}, "the dry run wrote the contact's session"

        live = engine_mod.run_turn(_envelope_for("ZZT-865-r3-live", "check spec srtwc286"), session_factory=session_factory)
        assert live.branch_kind == "business_query", live.branch_kind
        written = _session_vars(session_factory)

        assert dry.session_vars is not None
        assert dry.session_vars.get("focus") == written.get("focus"), (dry.session_vars, written)
        assert (written.get("focus") or {}).get("products"), written


class TestTheConsoleShowsWhereTheEscalationWent:
    def test_the_console_turn_carries_one_readable_routing_line(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R3: the reply the customer sees is unchanged, and the console turn itself
        carries team, brand, source and assignee in one line, so the owner can see where
        it went without opening the trace."""
        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        turn1, turn2, calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text="check spec srtwc286",
            first_verdict=_product_verdict("master_products", "check_product", "srtwc286"),
        )
        assert turn2.send_messages == [
            "Your request is out of the scope of my ability and require human assistance. "
            "We are directing your enquiry to the correct person. Please wait for a moment.",
            "This inquiry has been routed to the respective person-in-charge (PIC) from "
            "marketing product team. We will get back to you soon. Thanks for your patience.",
        ], turn2.send_messages
        assert turn2.trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand sorento, source focus_product, "
            "found in Sorento, assignee ZZT Tay Zhi Yang"
        ), turn2.trace_summary
        assert turn1.trace_summary["routing_line"] is None, turn1.trace_summary

    def test_the_looked_up_stage_summary_names_the_routing(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R3, the trace screen: the escalation's `looked_up` stage reads as one line."""
        from app.models.chatbot_turn import ChatbotTurn

        _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
        _turn1, turn2, _calls = _console_two_turns(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            first_text="check spec srtwc286",
            first_verdict=_product_verdict("master_products", "check_product", "srtwc286"),
        )
        db = session_factory()
        try:
            row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn2.turn_id).first()
            records = [r for r in (row.trace or []) if r.get("stage") == "looked_up"]
        finally:
            db.close()
        assert records[-1]["summary"] == (
            "Handed the conversation to a person. Routing: team marketing_product, brand "
            "sorento, source focus_product, found in Sorento, assignee ZZT Tay Zhi Yang"
        ), records[-1]


class TestFocusProductBrandReadForAnUnsettledCode:
    def test_an_unsettled_code_is_read_the_way_the_resolver_reads_it(self, session_factory) -> None:
        """Round 3: an unsettled focus entry carries the token the customer typed. The
        brand read finds its rows by the resolver's own code tiers (exact, then prefix,
        then substring), so "srtwc286" is SRTWC286-SH, as it was in the answer."""
        from app.services.chatbot.lanes.escalation_services import focus_product_brand

        _seed_branded(session_factory, code="ZZT866-SH", brand_code="SORENTO")
        _seed_branded(session_factory, code="ZZT866-PT", brand_code="SORENTO")
        _seed_branded(session_factory, code="ZZT867-SH", brand_code="SORENTO")
        _seed_branded(session_factory, code="ZZT867-MC", brand_code="MOCHA")
        _seed_branded(session_factory, code="ZZT868", brand_code="MOCHA")
        _seed_branded(session_factory, code="ZZT868-X", brand_code="SORENTO")
        db = _db(session_factory)
        try:
            unsettled = lambda raw: [{"raw": raw, "hint": "product", "canonical_code": raw}]  # noqa: E731
            assert focus_product_brand(db, unsettled("zzt866")) == "sorento", "prefix, one brand"
            assert focus_product_brand(db, unsettled("zzt867")) is None, "prefix, two brands"
            assert focus_product_brand(db, unsettled("t866-s")) == "sorento", "substring"
            assert focus_product_brand(db, unsettled("zzt868")) == "mocha", "an exact code wins over its prefix"
            assert focus_product_brand(db, unsettled("nothing-like-it")) is None
        finally:
            db.close()


# --------------------------------------------------------------------------- #
# Fix round 4: the owner's retest of round 3 (27 Sep 21:19 to 21:20 MYT, console).
#
# Three escalations in one console conversation:
#   1. "check spec srtwc286", then "pelase esclate to marekting team": right (sorento).
#   2. "pelase escalate to marketing team MWC-SC8609-PP": went to PURCHASING with no
#      brand. The parser's own routing for a message carrying a product code is the
#      master_products domain's team (purchasing), and the lane carried it over the
#      customer's own words. The code exists only in the Mocha company, and the brand
#      read ran under the contact's Sorento scope, so it found no row at all.
#   3. "please esclate to marketin team MWCY8610": right (mocha, off the Sorento row).
#
# Owner's rule: every Mocha company item, and every Mocha-brand product in Sorento,
# escalates to the Mocha brand member of Marketing Product, whichever company the
# customer is talking to.
# --------------------------------------------------------------------------- #

MOCHA_COMPANY_ID = "6f0c2a1e-0000-4000-8000-000000000865"


def _seed_mocha_company(session_factory) -> None:
    from app.models.company import Company

    db = _db(session_factory, scope=None)
    try:
        if db.query(Company.id).filter(Company.id == MOCHA_COMPANY_ID).scalar() is None:
            db.add(Company(id=MOCHA_COMPANY_ID, name="Mocha", code="MCH", is_active=True))
            db.commit()
    finally:
        db.close()


def _seed_mocha_company_item(session_factory, *, code: str) -> None:
    """A product of the Mocha company, with no brand row, as the Mocha catalogue holds it."""
    import uuid as uuid_mod

    from app.models.product import Product, ProductCategory, UnitOfMeasure

    _seed_mocha_company(session_factory)
    db = _db(session_factory, scope=frozenset({MOCHA_COMPANY_ID}))
    try:
        cat = ProductCategory(
            id=str(uuid_mod.uuid4()),
            category_code=f"ZZTMC-{code}",
            category_name="ZZT Mocha",
            class_label="zzt",
            search_synonyms=[],
            company_id=MOCHA_COMPANY_ID,
        )
        uom = UnitOfMeasure(
            id=str(uuid_mod.uuid4()), uom_code=f"ZZTMU-{code}"[:20], uom_name="Each", company_id=MOCHA_COMPANY_ID
        )
        db.add_all([cat, uom])
        db.flush()
        db.add(
            Product(
                id=str(uuid_mod.uuid4()),
                product_code=code,
                product_name=f"ZZT Mocha {code}",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=1,
                company_id=MOCHA_COMPANY_ID,
            )
        )
        db.commit()
    finally:
        db.close()


def _escalation_verdict(*, user_goal: str, team: str, codes: list[str] = ()) -> dict[str, Any]:
    """An escalation turn as the parser reads it: `request_for_help`, the domain carried,
    the product code (if the message named one) as an entity, and the parser's own team."""
    return verdict(
        message_type="request_for_help",
        domain_hint="master_products",
        intent_hint="check_product",
        user_goal=user_goal,
        entities=[entity(code, hint="product", confident=True) for code in codes],
        routing={"suggested_team": team, "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


# The owner's four messages, in order, each with the verdict his trace recorded: the
# parser's team is `marketing_product` for the two that went right, and `purchasing`
# for "marketing team MWC-SC8609-PP" (a product code in the message reads as the
# master_products domain, whose team is purchasing). `user_goal` is the parser's own
# reading of the message, spelling corrected, which is where "marketing team" survives.
OWNERS_27SEP_ROUND4 = [
    ("check spec srtwc286", _product_verdict("master_products", "check_product", "srtwc286")),
    (
        "pelase esclate to marekting team",
        _escalation_verdict(user_goal="trying to escalate to the marketing team", team="marketing_product"),
    ),
    (
        "pelase escalate to marketing team MWC-SC8609-PP",
        _escalation_verdict(
            user_goal="trying to escalate MWC-SC8609-PP to the marketing team",
            team="purchasing",
            codes=["MWC-SC8609-PP"],
        ),
    ),
    (
        "please esclate to marketin team MWCY8610",
        _escalation_verdict(
            user_goal="trying to escalate MWCY8610 to the marketing team",
            team="marketing_product",
            codes=["MWCY8610"],
        ),
    ),
]


def _seed_owners_catalogue(session_factory) -> None:
    """SRTWC286-SH: Sorento company, SORENTO brand. MWC-SC8609-PP: Mocha company only, no
    brand row. MWCY8610: in both, the Sorento row carrying the MOCHA brand."""
    _seed_branded(session_factory, code="SRTWC286-SH", brand_code="SORENTO")
    _seed_branded(session_factory, code="MWCY8610", brand_code="MOCHA")
    _seed_mocha_company_item(session_factory, code="MWC-SC8609-PP")
    _seed_mocha_company_item(session_factory, code="MWCY8610")


def _console_replay(session_factory, monkeypatch, stub_parser, stub_access, messages) -> list[tuple[Any, list]]:
    """Every message through `console_service.run_console_turn`, the console's own path:
    dry run, one contact (Sorento company), each turn sending back the `session_vars` the
    previous one returned. Returns `(turn, next-assignee calls of that turn)` per message."""
    from app.services.chatbot import console_service

    _seed_contact(session_factory, phone="+60900000038")
    _seed_borrowable_envelope(session_factory)
    _seed_marketing_product_team(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    stub_access()
    _stub_any_product_tool(monkeypatch)
    calls = _capture_real_next_assignee(monkeypatch)
    sla = _capture_sla(monkeypatch)

    results: list[tuple[Any, list]] = []
    session_vars: dict[str, Any] = {}
    db = session_factory()
    try:
        for text_, parsed in messages:
            stub_parser(parsed)
            before = len(calls)
            turn = console_service.run_console_turn(
                db,
                contact_respond_id=str(CONTACT_ID),
                text=text_,
                session_vars=session_vars,
                run_id="zzt-865-r4",
            )
            results.append((turn, list(calls[before:])))
            session_vars = turn.session_vars or {}
    finally:
        db.close()
    assert sla == [], "a console turn is a dry run: no SLA row"
    return results


def _assert_marketing_draw(
    session_factory, turn, calls, *, brand: str, assignee: str, company: str | None
) -> None:
    assert turn.branch_kind == "out_of_scope", turn.branch_kind
    assert len(calls) == 1, calls
    body, response = calls[0]["body"], calls[0]["response"]
    assert body["team_code"] == "marketing_product", body
    assert body["brand_code"] == brand, body
    assert response.get("assignee_name") == assignee, response
    assert response.get("brand_matched") is True, response
    routing = _looked_up_routing(session_factory, turn.turn_id)
    assert routing["team_code"] == "marketing_product", routing
    assert routing["brand_code"] == brand, routing
    assert routing["routing_source"] == "focus_product", routing
    assert str(routing["cursor_key"]).endswith(f"~b:{brand}"), routing
    assert routing["assignee_name"] == assignee, routing
    assert routing.get("product_company") == company, routing
    assert turn.send_messages[-1] == (
        "This inquiry has been routed to the respective person-in-charge (PIC) from "
        "marketing product team. We will get back to you soon. Thanks for your patience."
    ), turn.send_messages


class TestTheOwnersRetestOfRound3:
    def test_the_owners_three_escalations_replayed_in_one_console_conversation(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R4: the owner's exact messages, in his order, through the console. On 0440ce8e
        escalation 2 went to purchasing with no brand."""
        _seed_owners_catalogue(session_factory)
        results = _console_replay(session_factory, monkeypatch, stub_parser, stub_access, OWNERS_27SEP_ROUND4)
        (spec, _), first, second, third = results
        assert spec.branch_kind == "business_query", spec.branch_kind

        _assert_marketing_draw(session_factory, *first, brand="sorento", assignee="ZZT Tay Zhi Yang", company="Sorento")
        _assert_marketing_draw(session_factory, *second, brand="mocha", assignee="ZZT Kia Yee", company="Mocha")
        _assert_marketing_draw(session_factory, *third, brand="mocha", assignee="ZZT Kia Yee", company="Sorento")

        assert first[0].trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand sorento, source focus_product, "
            "found in Sorento, assignee ZZT Tay Zhi Yang"
        ), first[0].trace_summary
        assert second[0].trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand mocha, source focus_product, "
            "found in Mocha, assignee ZZT Kia Yee"
        ), second[0].trace_summary

    @pytest.mark.parametrize(
        ("index", "brand", "assignee", "company"),
        [
            (2, "mocha", "ZZT Kia Yee", "Mocha"),
            (3, "mocha", "ZZT Kia Yee", "Sorento"),
        ],
        ids=["mocha-company-only", "in-both-companies"],
    )
    def test_each_one_message_escalation_on_a_fresh_conversation(
        self, session_factory, stub_parser, stub_access, monkeypatch, index, brand, assignee, company
    ) -> None:
        """R4: escalations 2 and 3 on their own, with nothing in focus before them."""
        _seed_owners_catalogue(session_factory)
        [(turn, calls)] = _console_replay(
            session_factory, monkeypatch, stub_parser, stub_access, [OWNERS_27SEP_ROUND4[index]]
        )
        _assert_marketing_draw(session_factory, turn, calls, brand=brand, assignee=assignee, company=company)

    def test_a_code_in_no_company_is_said_back_and_the_escalation_still_goes_out(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R2: a code found nowhere names no brand, the routing line says so in one line,
        and the escalation is still drawn (whole Marketing Product team)."""
        _seed_owners_catalogue(session_factory)
        [(turn, calls)] = _console_replay(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            [
                (
                    "please escalate to marketing team ZZTNOPE99",
                    _escalation_verdict(
                        user_goal="trying to escalate ZZTNOPE99 to the marketing team",
                        team="purchasing",
                        codes=["ZZTNOPE99"],
                    ),
                )
            ],
        )
        assert turn.branch_kind == "out_of_scope", turn.branch_kind
        assert len(calls) == 1, calls
        assert calls[0]["body"]["team_code"] == "marketing_product", calls[0]["body"]
        assert calls[0]["body"]["brand_code"] is None, calls[0]["body"]
        assert calls[0]["response"].get("assignee_name"), calls[0]["response"]
        assert turn.trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand none, source none, "
            "product ZZTNOPE99 not found in any company, assignee ZZT Kia Yee"
        ), turn.trace_summary


class TestAnExplicitTeamBeatsTheParsersSuggestion:
    """R1, at the lane: the team the customer named (as the parser read the message, its
    `user_goal`) wins over `routing.suggested_team`; the suggestion is only the fallback."""

    def _route(self, *, user_goal: Any, parser_team: Any, focus: bool = True, derived: Any = None):
        from app.services.chatbot.lanes.escalation import _person_routing

        output = {
            "user_goal": user_goal,
            "routing": {"suggested_team": derived or parser_team},
            "escalation": {"is_escalation_confirmation": False},
        }
        ctx = {"parse": {"output": output, "_parser_raw": {"routing": {"suggested_team": parser_team}}}}
        item = {"team": derived or parser_team, "focus_products": [{"raw": "X1", "hint": "product"}] if focus else []}
        return _person_routing(ctx, item, derived or parser_team, None)

    @pytest.mark.parametrize(
        ("goal", "parser_team", "expected"),
        [
            ("trying to escalate MWC-SC8609-PP to the marketing team", "purchasing", "marketing_product"),
            ("trying to escalate to marketing for SRtsc07 full height", "purchasing", "marketing_product"),
            ("trying to pass this to the purchasing team", "marketing_product", "purchasing"),
            ("wants to talk to customer service about SRT1", "purchasing", "customer_service"),
            ("trying to escalate to the warehouse team", "purchasing", "warehouse"),
            ("escalate to marketing product team", "purchasing", "marketing_product"),
        ],
    )
    def test_the_named_team_wins(self, goal, parser_team, expected) -> None:
        assert self._route(user_goal=goal, parser_team=parser_team) == {
            "kind": "assign",
            "team": expected,
            "assignee": None,
        }

    @pytest.mark.parametrize(
        ("goal", "parser_team"),
        [
            ("trying to escalate the request to the team", "purchasing"),
            ("trying to get the technical drawing for SRTW2600", "marketing_product"),
            (None, "purchasing"),
            ("trying to escalate to the marketing team", "marketing_product"),
        ],
        ids=["no-team-named", "no-escalation-words", "no-goal", "parser-agrees"],
    )
    def test_the_parser_team_stands_when_no_other_team_is_named(self, goal, parser_team) -> None:
        assert self._route(user_goal=goal, parser_team=parser_team) is None

    def test_marketing_with_no_product_in_focus_asks_which_marketing_team(self) -> None:
        routed = self._route(
            user_goal="trying to escalate to the marketing team", parser_team="purchasing", focus=False
        )
        assert routed["kind"] == "clarify", routed
        assert [p["team"] for p in routed["option_pairs"]] == [
            "marketing_product",
            "marketing_form",
            "marketing_promotion",
        ], routed


class TestFocusProductOriginAcrossCompanies:
    def test_the_brand_read_looks_across_companies_and_names_the_company(self, session_factory) -> None:
        """R2 at the read: Mocha-only means mocha; in both takes the brand row; neither
        is reported back; the session's own scope is left as it was."""
        from app.models.base import get_company_scope
        from app.services.chatbot.lanes.escalation_services import focus_product_brand, focus_product_origin

        _seed_owners_catalogue(session_factory)
        db = _db(session_factory)
        try:
            unsettled = lambda raw: [{"raw": raw, "hint": "product", "canonical_code": raw}]  # noqa: E731
            assert focus_product_origin(db, unsettled("MWC-SC8609-PP")) == {
                "brand": "mocha",
                "company": "Mocha",
                "not_found": [],
            }
            assert focus_product_origin(db, unsettled("MWCY8610")) == {
                "brand": "mocha",
                "company": "Sorento",
                "not_found": [],
            }
            assert focus_product_origin(db, unsettled("srtwc286")) == {
                "brand": "sorento",
                "company": "Sorento",
                "not_found": [],
            }
            assert focus_product_origin(db, unsettled("ZZTNOPE99")) == {
                "brand": None,
                "company": None,
                "not_found": ["ZZTNOPE99"],
            }
            assert focus_product_brand(db, unsettled("MWC-SC8609-PP")) == "mocha"
            assert get_company_scope(db) == frozenset({"00000000-0000-0000-0000-000000000001"})
        finally:
            db.close()

    def test_an_unbranded_sorento_company_product_still_names_no_brand(self, session_factory) -> None:
        """The Sorento company carries several brands, so its company names none: a
        Sorento row without a brand row reads as before (no brand)."""
        from app.services.chatbot.lanes.escalation_services import focus_product_origin

        _seed_product_with_brand(session_factory, code="ZZTPLAIN1", brand_code=None)
        db = _db(session_factory)
        try:
            origin = focus_product_origin(db, [{"raw": "ZZTPLAIN1", "hint": "product", "canonical_code": "ZZTPLAIN1"}])
        finally:
            db.close()
        assert origin == {"brand": None, "company": "Sorento", "not_found": []}, origin


class TestTheLiveTurnDrawsFromTheNamedTeam:
    def test_escalation_2_live_draws_the_mocha_member_of_marketing_product(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R1 + R2 on the LIVE path (not the console preview): the next-assignee body is
        made from the named team, and the SLA row describes the same draw."""
        _seed_owners_catalogue(session_factory)
        _seed_contact(session_factory, phone="+60900000034")
        _seed_marketing_product_team(session_factory)
        stub_access()
        _stub_any_product_tool(monkeypatch)
        calls = _capture_real_next_assignee(monkeypatch)
        sla = _capture_sla(monkeypatch)
        text_, parsed = OWNERS_27SEP_ROUND4[2]
        stub_parser(parsed)
        turn = engine_mod.run_turn(_envelope_for("ZZT-865-r4-live", text_), session_factory=session_factory)
        assert turn.branch_kind == "out_of_scope", turn.branch_kind
        assert len(calls) == 1, calls
        body, response = calls[0]["body"], calls[0]["response"]
        assert body.get("preview") is not True, body
        assert (body["team_code"], body["brand_code"]) == ("marketing_product", "mocha"), body
        assert response.get("assignee_name") == "ZZT Kia Yee", response
        assert len(sla) == 1 and sla[0].team_set_code == "marketing_product", sla
        routing = _looked_up_routing(session_factory, turn.turn_id)
        assert routing["product_company"] == "Mocha", routing
