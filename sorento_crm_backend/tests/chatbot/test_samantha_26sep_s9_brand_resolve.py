"""Phase 2 RED tests - issue #1262 (the Samantha case), GROUP C brand slice, slice 9
(finding F1a), UAC `chatbot-samantha-slices-26sep-acceptance-criteria.md` AC-S9-3 /
AC-S9-6, "Rulings assumed" 7 and 8. Round 3 section 6 step 4 (the brand resolve): a
verdict entity `{raw, hint: "brand"}` that matches a live brand resolves to that
brand's ids INSIDE the chatbot - never re-typed to customer/transporter, never sent
through the shared resolver's order-domain fan-out
(`app.services.entity_resolver._DOMAIN_HINT_EXPANSIONS["order"]["brand"]`, which stays
in place for n8n/MCP callers, ruling 7) - and the order-domain gate admits a brand; the
report tools take a `brand_ids` filter (ruling 8: "other order reports" = the orders
list / by-product tools that already take `product_ids`).

T2/T3 of the trace (round 2 explainer, `samantha-chat-diagnosis.html` section 1):
"brand Sorento" + dealer "Cheng Huat Sentul" must never ask "which customer" over ten
SORENTO names and never ask "transporter or customer?".

Written before any of the resolve step exists. No implementation looked at beyond what
is read here to confirm the RED reason.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Brand
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, stub_access  # noqa: F401


class TestGateAdmitsBrandForOrder:
    """AC-S9-3's gate half: `order` must admit a `brand` entity - today's matrix does
    not (S4 point 7 only added `warehouse`)."""

    def test_brand_is_an_allowed_type_for_order(self) -> None:
        assert "brand" in gate_mod.ALLOWED["order"], (
            f"the order-domain gate must admit a brand entity: {gate_mod.ALLOWED['order']}"
        )


class TestEntityIdsTransformerMapsBrandToBrandIds:
    """AC-S9-4/AC-S9-6: once a brand entity carries a resolved uuid, the transformer
    must turn it into `brand_ids` the same way `product`/`customer` already become
    `product_ids`/`customer_ids` - `TYPE_TO_PARAM` has no `brand` entry today."""

    BRAND_UUID = "11111111-1111-1111-1111-111111111111"

    def _trigger(self, *, tool: str) -> dict[str, Any]:
        return {
            "entities": [
                {"uuid": self.BRAND_UUID, "entity_type": "brand", "canonical_code": "Sorento"},
            ],
            "tool": tool,
            "semantic_input": {"contact_id": "1", "space_id": "s"},
        }

    def test_outstanding_report_gets_brand_ids(self) -> None:
        out = fetch_mod.entity_ids_transformer(self._trigger(tool="crm_outstanding_report"))
        assert out.get("brand_ids") == [self.BRAND_UUID], out

    def test_orders_list_gets_brand_ids(self) -> None:
        out = fetch_mod.entity_ids_transformer(
            self._trigger(tool="crm_order_management_orders_list")
        )
        assert out.get("brand_ids") == [self.BRAND_UUID], out

    def test_orders_by_product_gets_brand_ids(self) -> None:
        out = fetch_mod.entity_ids_transformer(
            self._trigger(tool="crm_order_management_orders_by_product_list")
        )
        assert out.get("brand_ids") == [self.BRAND_UUID], out


class TestSharedResolverFanoutUnchangedForOtherCallers:
    """Guard, ruling 7 - may already be green: the shared resolver's OWN order-domain
    brand fan-out (n8n / MCP callers) is untouched by this slice. Kept here, named,
    so a coder does not "fix" F1a by deleting it."""

    def test_domain_hint_expansion_still_fans_brand_out_to_customer_and_transporter(self) -> None:
        from app.services.entity_resolver import _DOMAIN_HINT_EXPANSIONS

        assert _DOMAIN_HINT_EXPANSIONS["order"]["brand"] == frozenset(
            {"customer", "customer_order", "transporter"}
        )


def _seed_brand(session_factory, *, name: str, code: str, company_id: str = DEFAULT_COMPANY_ID) -> str:
    db = session_factory()
    set_company_scope(db, frozenset({company_id}))
    row = Brand(id=str(uuid.uuid4()), brand_name=name, brand_code=code, is_active=True, company_id=company_id)
    db.add(row)
    db.commit()
    return row.id


def _seed_contact_scoped_to_sorento(session_factory) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
            "VALUES (gen_random_uuid(), :sid, 'ZZT s9 workspace', 'ZZT-cipher') ON CONFLICT DO NOTHING"
        ),
        {"sid": "364817"},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb), "
            "(SELECT id FROM respond_workspaces WHERE space_id = :sid LIMIT 1))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000032", "sv": json.dumps({"variables": {}}), "sid": "364817"},
    )
    db.execute(
        text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
        ),
        {"cid": str(CONTACT_ID), "company_id": DEFAULT_COMPANY_ID},
    )
    db.commit()


def _open_question_of(session_factory) -> dict[str, Any]:
    db = session_factory()
    row = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": str(CONTACT_ID)},
    ).first()
    raw = row.session_vars if row is not None else {}
    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return parsed.get("open_question") or {}


class TestT2BrandTokenNotResolvedAsCustomerInOrderDomain:
    """T2/T3 end to end (AC-S9-3, AC-S9-5): "brand Sorento" + dealer "Cheng Huat
    Sentul" under `domain_hint: order`. Today NOTHING intercepts a `brand`-hinted
    entity before the shared resolver, so this stub - built to mimic exactly what
    T2/T3's own trace measured (`_DOMAIN_HINT_EXPANSIONS["order"]["brand"]` fanning
    "Sorento" out to a customer AND a transporter match) - is asked about "Sorento"
    at all, and no `brand_ids` ever reaches the tool call. Once the slice lands,
    "Sorento" must resolve locally against the live `brands` table and never reach
    this resolver stub.

    TWO real turns, not one (coordinator round, 26 Sep 2026): granting the SO reveal
    and naming no document word is exactly Contract 38 / AC-1130's own bare-outstanding
    override - `test_outstanding_lane.py::TestCustomerOnlyOutstandingAskReachesTheReport
    ::test_bare_outstanding_customer_only_arms_the_scope_question` pins that a
    customer-only (or, now, customer+brand) subject on the bare word "outstanding"
    arms `outstanding_scope` ("Outstanding for which document?") rather than calling
    the report directly - so turn 1 here grades the brand-resolve half only (no
    tool call yet, brand never reaches the shared resolver, no customer/kind pick),
    and turn 2 answers the scope question ("3" = Both) and grades that the CARRIED
    brand reaches `crm_outstanding_report` alongside the carried customer. Turn 2 is
    the one this file expects to find still red: Samantha's real chat went through
    this exact question (T2), and the coder's own brand-resolve wiring
    (`lanes/business/__init__.py`'s `outstanding_brand_ids` on `semantic_input`) does
    not yet carry it into the scope-ask's own stored `filters`.
    """

    CUSTOMER_UUID = "22222222-2222-2222-2222-222222222222"
    TRANSPORTER_UUID = "33333333-3333-3333-3333-333333333333"
    CHENG_HUAT_UUID = "44444444-4444-4444-4444-444444444444"

    def _resolve_entity(self, asked: list[str]):
        def _fn(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            asked.extend(tokens)
            resolutions = []
            for token in tokens:
                if token == "Sorento":
                    # The T2/T3 fan-out, reproduced verbatim (never what a fixed
                    # chatbot should still be asking this seam about).
                    resolutions.append(
                        {
                            "token": token,
                            "resolved": False,
                            "matches": [
                                {
                                    "uuid": self.TRANSPORTER_UUID,
                                    "entity_type": "transporter",
                                    "canonical_code": "Sorento",
                                    "match_tier": "exact",
                                },
                                {
                                    "uuid": self.CUSTOMER_UUID,
                                    "entity_type": "customer",
                                    "canonical_code": "Sorento",
                                    "match_tier": "exact",
                                },
                            ],
                        }
                    )
                elif token == "Cheng Huat Sentul":
                    resolutions.append(
                        {
                            "token": token,
                            "resolved": True,
                            "matches": [
                                {
                                    "uuid": self.CHENG_HUAT_UUID,
                                    "entity_type": "customer",
                                    "canonical_code": "Cheng Huat Sentul",
                                    "match_tier": "exact",
                                }
                            ],
                        }
                    )
            return {"tokens": tokens, "resolutions": resolutions, "unresolved_tokens": []}

        return _fn

    def test_sorento_never_reaches_the_shared_resolver_and_the_report_gets_brand_ids(
        self, session_factory, monkeypatch
    ) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod
        from app.services.chatbot.lanes.business.services import AnswerServices

        _seed_contact_scoped_to_sorento(session_factory)
        brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")

        set_chatbot_switches(session_factory, business_lane=True)
        from app.models.user import SystemSetting

        db = session_factory()
        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting()
            db.add(row)
        row.chatbot_completed_lanes = ["business_query"]
        db.commit()

        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": True,
                "decision": "allow",
                "agent_name": "General",
                "attributes": ["sales_orders.outstanding"],
                "all_attributes_allowed": None,
            },
        )

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            order_status="outstanding",
            entities=[
                {"raw": "Sorento", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True},
                {
                    "raw": "Cheng Huat Sentul",
                    "hint": "customer",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                },
            ],
        )
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)

        asked: list[str] = []
        resolve_services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(self._resolve_entity(asked)),
            probe=lambda **_: None,
        )
        captured: list[tuple[str, dict[str, Any]]] = []

        def mcp_call(name: str, args: dict) -> str:
            captured.append((name, dict(args)))
            return json.dumps({"has_result": False, "items": []})

        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: resolve_services
        )
        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )

        # -- turn 1: "outstanding brand Sorento dealer Cheng Huat Sentul" -------- #
        envelope = _envelope()
        envelope.message["message"]["message"]["text"] = "outstanding brand Sorento dealer Cheng Huat Sentul"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        assert "Sorento" not in asked, (
            f"the brand token must resolve locally against the live brands table and "
            f"never reach the shared resolver's order-domain fan-out: asked={asked!r}"
        )
        assert "Cheng Huat Sentul" in asked, asked

        reply_text = ((result.reply or {}).get("text") or "")
        assert "transporter" not in reply_text.lower(), reply_text
        assert "which customer" not in reply_text.lower(), reply_text

        # Contract 38 / AC-1130: a customer-only (now customer+brand) subject on the
        # BARE "outstanding" word arms the scope question rather than calling the
        # report this turn - `TestCustomerOnlyOutstandingAskReachesTheReport::
        # test_bare_outstanding_customer_only_arms_the_scope_question` pins the
        # identical shape for a customer alone.
        assert captured == [], (
            f"no report/order tool may be called while the scope question is open: {captured}"
        )
        assert "Outstanding for which document?" in reply_text, reply_text
        open_question = _open_question_of(session_factory)
        assert open_question.get("kind") == "outstanding_scope", (
            f"a customer+brand outstanding ask must arm the scope question exactly "
            f"like a product/customer ask does: {open_question!r}"
        )

        # -- turn 2: "3" (Both) answers the scope question ----------------------- #
        qf_turn2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[3],
        )
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf_turn2)
        envelope2 = _envelope()
        envelope2.message["message"]["messageId"] = "ZZT-s9-brand-scope-answer-1"
        envelope2.message["message"]["message"]["text"] = "3"
        engine_mod.run_turn(envelope2, session_factory=session_factory)

        assert captured, (
            "the scope answer must run crm_outstanding_report on the carried "
            "customer AND brand - it never called any tool at all"
        )
        name, args = captured[-1]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("customer_ids") == [self.CHENG_HUAT_UUID], (
            f"the carried customer must survive the scope answer: {args}"
        )
        assert args.get("brand_ids") == [brand_id], (
            f"the carried BRAND must survive the scope answer too - the scope-ask's "
            f"own stored filters do not carry `outstanding_brand_ids` yet: {args}"
        )


# --------------------------------------------------------------------------- #
# Phase 3 fix-round findings (26 Sep, security review pass on the coder's own S9
# strip - `turn_runtime.py`'s `resolve_kinds` strips a brand-hinted entity matching
# a LIVE brand from the ctx handed to `resolve_gate.run`, with no domain check at
# all - `_brand_hinted_entities_matching_live` runs, and its matches are removed
# from `ctx["parse"]["output"]["entities"]`, for EVERY `branch_kind`/domain, not
# only the order-domain fan-out this slice was written to avoid.
# --------------------------------------------------------------------------- #


class TestPromotionBrandGateFailsClosed:
    def test_promotion_brand_gate_still_fails_closed_for_an_unheld_live_brand(
        self, session_factory, monkeypatch
    ) -> None:
        """Security B1, rewritten (26 Sep, coordinator round 2): the previous version
        called `tier_gate()` directly with `parser={"entities": []}` - i.e. it tested
        the ALREADY-STRIPPED state by construction, which no code path can ever make
        fail closed (an empty `entities` list can never carry a brand for the gate
        to see, whatever the gate does with it). The coder's own fix
        (`turn_runtime.resolve_kinds`) makes the strip ORDER-DOMAIN ONLY, so the real
        guarantee is: for a PROMOTION-domain turn, the live-brand entity survives
        into what `resolve_gate.run` (and therefore `tier_gate`) actually sees.

        Exercises the real `resolve_kinds` -> `resolve_gate.run` seam (`ResolveOutcome
        .payload["gate"]`, the SAME raw dict `gate.py::run_gate` returns, which is
        what an unheld-brand miss reply is composed from): a contact entitled ONLY to
        "Sorento Dealer" asks about Cabana promotions, Cabana IS an active `Brand` in
        the contact's own company. `gate["brand_gate_empty"]` must be `True`, the
        gate's own `access_notice` must name "cabana", and `compatible_entities` must
        be empty (no Sorento file/row leaked through as a fallback).

        This was CONFIRMED red for the right reason before the coder's fix landed,
        by temporarily reverting `resolve_kinds`'s own `is_order_domain` gate back to
        an unconditional strip (`_brand_hinted_entities_matching_live(db, entities) if
        True else []`) and restoring it exactly afterward: `brand_gate_empty` flipped
        to `False` and `access_notice` went empty.
        """
        import uuid as _uuid

        from app.models.base import set_company_scope as _set_company_scope

        from app.services.chatbot import turn_runtime
        from app.services.chatbot.lanes.business import services as business_services
        from app.services.chatbot.lanes.business.services import ResolveGateServices

        db = session_factory()
        _set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        db.add(
            Brand(
                id=str(_uuid.uuid4()), brand_code="CAB", brand_name="Cabana",
                is_active=True, company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.commit()

        def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            asked = list(body.get("tokens") or [])
            return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}

        resolve_services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(_resolve_entity),
            probe=lambda **_: None,
        )
        monkeypatch.setattr(
            business_services, "production_services", lambda db, **kw: resolve_services
        )

        ctx = {
            "contact": {"id": "zzt-s9-promo-gate"},
            "parse": {
                "output": {
                    "domain_hint": "promotion",
                    "entities": [
                        {
                            "raw": "Cabana", "hint": "brand", "canonical_code": None,
                            "current_message": True, "confident": True,
                        },
                    ],
                }
            },
            "session": {},
        }
        outcome = turn_runtime.resolve_kinds(
            db, ctx=ctx, branch_kind="check_promotion", space_id=None, dry_run=True
        )
        gate = (outcome.payload or {}).get("gate") or {}

        assert gate.get("brand_gate_empty") is True, (
            f"a contact asking for an UNHELD live brand must still fail closed: {gate!r}"
        )
        assert "cabana" in (gate.get("access_notice") or "").lower(), (
            f"the refusal must name the unheld brand: {gate.get('access_notice')!r}"
        )
        assert gate.get("compatible_entities") == [], (
            f"no Sorento (or any other) row may leak through as a fallback: {gate!r}"
        )

    def test_mixed_order_and_promotion_turn_keeps_the_brand_gate_closed(
        self, session_factory, monkeypatch
    ) -> None:
        """Security review follow-up (26 Sep, coordinator round 3): a message asking
        for outstanding orders AND Cabana promotions in one turn - the parser hints
        `domain_hint: "order"` (the order half won the domain read) while `route()`
        still sends the turn through the promotion access-check entry
        (`branch_kind="check_promotion"`, `entry="access_check"`), because a
        promotion ask reaches the tier gate before anything else regardless of which
        domain word the parser preferred.

        `resolve_kinds`'s own `is_order_domain` check (~1223) reads `domain_hint`
        ALONE - it has no idea `branch_kind` is `"check_promotion"` this turn - so it
        strips the Cabana entity from `ctx.parse.output.entities` (~1236-1243)
        exactly as it would for a pure order-domain ask, and `resolve_gate.run`
        (~1052) hands `run_tier_gate` that ALREADY-STRIPPED dict. The gate closes on
        an empty `query_brands` the same way `test_promotion_brand_gate_still_fails_
        closed_for_an_unheld_live_brand` measured before its own fix: `brand_gate_
        empty` false, the full "Sorento Dealer" entitlement survives untouched.

        Same seam and fixture as the sibling test above; only `domain_hint` and
        `branch_kind` differ. Only the GATE is asserted here (not on the resolver's
        own token list) - the resolver-side question needs `resolve_gate.run`'s
        actual `resolved` payload, a separate carrier from `gate`, and is out of
        scope for this test.
        """
        import uuid as _uuid

        from app.models.base import set_company_scope as _set_company_scope

        from app.services.chatbot import turn_runtime
        from app.services.chatbot.lanes.business import services as business_services
        from app.services.chatbot.lanes.business.services import ResolveGateServices

        db = session_factory()
        _set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        db.add(
            Brand(
                id=str(_uuid.uuid4()), brand_code="CAB", brand_name="Cabana",
                is_active=True, company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.commit()

        def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            asked = list(body.get("tokens") or [])
            return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}

        resolve_services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(_resolve_entity),
            probe=lambda **_: None,
        )
        monkeypatch.setattr(
            business_services, "production_services", lambda db, **kw: resolve_services
        )

        ctx = {
            "contact": {"id": "zzt-s9-mixed-order-promo-gate"},
            "parse": {
                "output": {
                    "domain_hint": "order",
                    "entities": [
                        {
                            "raw": "Cabana", "hint": "brand", "canonical_code": None,
                            "current_message": True, "confident": True,
                        },
                    ],
                }
            },
            "session": {},
        }
        outcome = turn_runtime.resolve_kinds(
            db, ctx=ctx, branch_kind="check_promotion", space_id=None, dry_run=True
        )
        gate = (outcome.payload or {}).get("gate") or {}

        assert gate.get("brand_gate_empty") is True, (
            f"a mixed order+promotion turn must still fail closed on the unheld "
            f"brand, not fall back to the full entitlement: {gate!r}"
        )
        assert "cabana" in (gate.get("access_notice") or "").lower(), (
            f"the refusal must name the unheld brand even on a mixed-domain turn: "
            f"{gate.get('access_notice')!r}"
        )

    def test_brand_strip_is_order_domain_only(self, session_factory, monkeypatch) -> None:
        """The strip exists to keep a brand OUT of the shared resolver's order-domain
        fan-out (F1a) - it must not also blind the resolver for inventory/promotion
        domains, where `gate.py` (~1546-1556) reads `parser.get("entities")` for a
        `hint: "brand"` entity ON PURPOSE (brand-grouping a promotion roster).

        Red: `turn_runtime.resolve_kinds` strips the brand-hinted entity
        UNCONDITIONALLY (no domain/branch_kind check at all) - for an "inventory"
        domain turn naming ONLY a brand token, the entities list is emptied before
        `resolve_gate.run` is even reached, and the early-return guard
        (`if not entities and entry != "access_check": return ...`) then skips the
        resolver ENTIRELY, so `resolve_gate.run` (and gate.py's own brand-grouping
        code behind it) never sees the token at all.
        """
        import uuid as _uuid

        from app.models.base import set_company_scope as _set_company_scope

        from app.services.chatbot import turn_runtime
        from app.services.chatbot.lanes.business import resolve_gate as resolve_gate_mod
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        _set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        db.add(
            Brand(
                id=str(_uuid.uuid4()), brand_code="CAB", brand_name="Cabana",
                is_active=True, company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.commit()

        captured: dict[str, Any] = {}

        def _fake_run(ctx, entry, extra, **kw):
            captured["ctx"] = ctx
            captured["entry"] = entry
            return {"resolved": {"resolutions": []}, "gate": {"compatible_entities": []}}

        monkeypatch.setattr(resolve_gate_mod, "run", _fake_run)
        monkeypatch.setattr(business_services, "production_services", lambda db, **kw: object())

        entities = [
            {"raw": "Cabana", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True},
        ]
        ctx = {"parse": {"output": {"domain_hint": "inventory", "entities": list(entities)}}}
        turn_runtime.resolve_kinds(db, ctx=ctx, branch_kind="business_query", space_id=None, dry_run=True)

        assert captured.get("entry") is not None, (
            "an inventory-domain turn naming only a brand must still reach the "
            "resolver (gate.py's own brand-grouping code needs to see the token) - "
            f"it never got called at all: {captured!r}"
        )
        surviving = (
            ((captured.get("ctx") or {}).get("parse") or {}).get("output", {}).get("entities")
        )
        assert surviving, (
            f"the brand entity must not be stripped for a non-order domain: {surviving!r}"
        )


class TestUnlistedBrandNeverSelectsASubjectlessReport:
    def test_unlisted_brand_word_does_not_call_outstanding_report_unfiltered(
        self, session_factory
    ) -> None:
        """`lanes/business/__init__.py`'s `has_brand` counts ANY brand-hinted entity
        from the parser, whether or not it ever matched a live brand - "brand XYZ"
        (not a real brand, no other subject named) used to set `has_brand = True` and
        pick `crm_outstanding_report` anyway, with no resolvable filter at all
        (`recompose`/AC-S2-3's own "Customer: all" shape, reachable through an
        unlisted brand word instead of an unusable customer). `has_brand` is now
        `bool(semantic_input.get("outstanding_brand_ids"))` - resolved ids only - so
        an unresolved brand word is correctly "no subject", and with no OTHER
        subject either, the turn keeps today's plain order-list path
        (`crm_order_management_orders_list`), the SAME carve-out
        `test_outstanding_lane.py::TestToolPick::
        test_outstanding_without_a_subject_keeps_order_list` already pins for a
        subject-less outstanding ask in general - `crm_outstanding_report` must
        never be the tool picked here.
        """
        from app.services.chatbot.lanes.business import run_fetch
        from app.services.chatbot.lanes.business.services import FetchServices

        captured: list[tuple[str, dict[str, Any]]] = []

        def _call(name: str, args: dict[str, Any]) -> str:
            captured.append((name, dict(args)))
            return json.dumps({"has_result": False, "items": []})

        payload = {
            "gate": {"compatible_entities": []},
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": {
                        "domain_hint": "order", "intent_hint": "check_order",
                        "order_status": "outstanding",
                        "entities": [
                            {
                                "raw": "XYZ", "hint": "brand", "canonical_code": None,
                                "current_message": True, "confident": True,
                            },
                        ],
                    }
                },
                "contact": {"id": "zzt-s9-unlisted-brand"},
                "access": {"attributes": ["sales_orders.outstanding"]},
            },
        }
        run_fetch(payload, services=FetchServices(mcp_call=_call))

        called_names = [name for name, _args in captured]
        assert "crm_outstanding_report" not in called_names, (
            f"an unlisted brand word with no other subject must never reach "
            f"crm_outstanding_report unfiltered: {captured!r}"
        )

    def test_unlisted_brand_word_is_not_dropped_in_silence(
        self, session_factory, monkeypatch
    ) -> None:
        """The other half: an unresolved brand word must still be NAMED in the
        reply ("I could not find XYZ"), the same `unplaced`/`unresolved` line every
        other unresolvable token already gets (turn/compose.py's own "a token
        nobody could place is named, never dropped in silence" rule) - through a
        real `engine.run_turn`, since that line is composed above `run_fetch`'s own
        return shape.
        """
        from tests.chatbot.test_outstanding_lane import _run_turn

        _seed_contact_scoped_to_sorento(session_factory)
        qf = _parser_output(
            domain_hint="order", intent_hint="check_order", order_status="outstanding",
            entities=[
                {"raw": "XYZ", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True},
            ],
        )
        result, _captured = _run_turn(
            session_factory, monkeypatch,
            qf=qf, text_body="outstanding brand XYZ", msg_id="ZZT-s9-unlisted-brand-silent-1",
            attributes=["sales_orders.outstanding"], matches={},  # XYZ never resolves to anything
        )
        reply = (result.reply or {}).get("text") or ""
        assert "XYZ" in reply, (
            f"an unresolved brand word must be named, not silently dropped: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Fix lane round 2, B1 (reviewer pass at 4719a829): AC-S9-6 THROUGH THE CHATBOT.
# The strip keeps a live brand away from the shared resolver, so the brand never
# became a gate row with a uuid and `TYPE_TO_PARAM["brand"]` never fired for the
# orders tools: "delivery orders brand Sorento for Cheng Huat Sentul" called
# `crm_order_management_orders_list` with `customer_ids` only, and the header
# printed `Customer: Cheng Huat Sentul` with no Brand line, so the answer read as
# brand-filtered when it was not.
# --------------------------------------------------------------------------- #


class TestB1OrdersListAskGetsTheBrand:
    CHENG_HUAT_UUID = "44444444-4444-4444-4444-444444444444"

    def _resolve_entity(self, asked: list[str]):
        def _fn(body: dict[str, Any]) -> dict[str, Any]:
            tokens = list(body.get("tokens") or [])
            asked.extend(tokens)
            resolutions = [
                {
                    "token": token,
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": self.CHENG_HUAT_UUID,
                            "entity_type": "customer",
                            "canonical_code": "Cheng Huat Sentul",
                            "match_tier": "exact",
                        }
                    ],
                }
                for token in tokens
                if token == "Cheng Huat Sentul"
            ]
            return {"tokens": tokens, "resolutions": resolutions, "unresolved_tokens": []}

        return _fn

    def _run(self, session_factory, monkeypatch, *, tool_body: dict[str, Any]):
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.head import parser as parser_mod
        from app.services.chatbot.lanes.business.services import AnswerServices

        _seed_contact_scoped_to_sorento(session_factory)
        brand_id = _seed_brand(session_factory, name="Sorento", code="SRT")
        set_chatbot_switches(session_factory, business_lane=True)
        from app.models.user import SystemSetting

        db = session_factory()
        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting()
            db.add(row)
        row.chatbot_completed_lanes = ["business_query"]
        db.commit()

        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, *, agent_code, contact_id, space_id: {
                "allowed": True,
                "decision": "allow",
                "agent_name": "General",
                "attributes": [],
                "all_attributes_allowed": None,
            },
        )

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            order_status="delivered",
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            entities=[
                {"raw": "Sorento", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True},
                {
                    "raw": "Cheng Huat Sentul",
                    "hint": "customer",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                },
            ],
        )
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)

        asked: list[str] = []
        resolve_services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(self._resolve_entity(asked)),
            probe=lambda **_: None,
        )
        captured: list[tuple[str, dict[str, Any]]] = []

        def mcp_call(name: str, args: dict) -> str:
            captured.append((name, dict(args)))
            return json.dumps(tool_body)

        monkeypatch.setattr(
            engine_mod.business_services, "production_services", lambda db, *, space_id=None: resolve_services
        )
        monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call))
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )

        envelope = _envelope()
        envelope.message["message"]["message"]["text"] = "delivery orders brand Sorento for Cheng Huat Sentul"
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
        return brand_id, asked, captured, ((result.reply or {}).get("text") or "")

    def _assert_orders_call_carries_the_brand(self, brand_id, asked, captured) -> None:
        assert "Sorento" not in asked, asked
        order_calls = [
            (n, a) for n, a in captured if n in fetch_mod.ORDER_TOOLS
        ]
        assert order_calls, f"the orders ask must call an orders tool: {captured}"
        _, args = order_calls[0]
        assert args.get("customer_ids") == [self.CHENG_HUAT_UUID], args
        assert args.get("brand_ids") == [brand_id], (
            f"the live brand must narrow the orders tool too (AC-S9-6), never be "
            f"dropped in silence: {args}"
        )

    def test_orders_miss_sends_brand_ids_and_names_the_brand(self, session_factory, monkeypatch) -> None:
        brand_id, asked, captured, reply = self._run(
            session_factory, monkeypatch, tool_body={"has_result": False, "items": []}
        )
        self._assert_orders_call_carries_the_brand(brand_id, asked, captured)
        assert "Brand: Sorento" in reply, reply

    def test_orders_hit_names_the_brand_in_the_scope_header(self, session_factory, monkeypatch) -> None:
        body = {
            "has_result": True,
            "items": [
                {
                    "order_number": "DO-ZZT-1",
                    "customer_name": "Cheng Huat Sentul",
                    "actual_delivery_date": "2026-09-01",
                }
            ],
            "data": [
                {
                    "order_number": "DO-ZZT-1",
                    "customer_name": "Cheng Huat Sentul",
                    "actual_delivery_date": "2026-09-01",
                }
            ],
        }
        brand_id, asked, captured, reply = self._run(session_factory, monkeypatch, tool_body=body)
        self._assert_orders_call_carries_the_brand(brand_id, asked, captured)
        assert "Brand: Sorento" in reply, reply
