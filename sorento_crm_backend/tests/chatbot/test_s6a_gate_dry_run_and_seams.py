"""S6a tester slice: the delegate seam, the D14 dry-run boundary, and three gate
predicates the replay corpus cannot exercise (AC-601 to AC-603, D14, H38, H46).

Everything here is on Postgres (`tests/_pg_fixture.py` via `tests/chatbot/conftest.py`'s
`session_factory`), against a blank schema, seeding its own ZZT-prefixed rows. Nothing
touches the shared dev database.

Retired 16 Sep 2026 (AC-1592, tester ruling, this session) - `TestS6aDelegateSeamFlagGating`
(16 tests) and `TestShadowFailurePath` (1 test) pin the S6a-era "shadow lane" architecture:
the business lane ran ALONGSIDE n8n's own lane, still delegating to it, recording its own
shadow attempt on `result.delegate_payload["_exit_kind"]` (`contracts.EXIT_KINDS`) and
dispatched via `engine.decide` (a ctx-in/branch-kind-out function). S6c's full rewrite
deleted `engine.decide` (AttributeError, grep-confirmed no such attribute exists) and
`result.delegate_payload` with it - the business lane runs IN PROCESS and closes its own
row now (`CRM_COMPLETED_BRANCH_KINDS` covers `business_query`/`check_promotion`/
`stock_denied` unconditionally, no flag gates it - `chatbot_business_lane_enabled` is
defined but never read from `run_turn`, same finding `test_s3_switch_and_complete_by_
body.py`'s own retirement note makes independently). Replacement coverage, both already
landed:

- The flag-gating/delegate property (`TestS6aDelegateSeamFlagGating`, all 16 methods):
  `test_s3_switch_and_complete_by_body.py::TestTheCompletedLaneSwitch` (this session) -
  measured directly that no switch holds a real branch kind back from completing today.
- The resolver-failure property (`TestShadowFailurePath.test_a_resolver_failure_still_
  completes_the_turn_delegated` - asserted `status="delegated"`, a SHADOW-lane failure
  that must not fail the turn itself): `test_engine_failure_paths.py::
  TestTheBusinessLaneOnFetchFailure.test_a_resolver_failure_is_a_graceful_miss_not_a_crash`
  (tester 11, same session) - measured the CURRENT outcome is `status="done"` with a bare
  miss reply, not `"delegated"` (no delegate exists to send it to anymore), the same
  underlying guarantee (a resolver blow-up never fails the turn) under the new shape.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.config import settings
from app.services.chatbot import contracts
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import pickers, resolve_gate
from app.services.chatbot.lanes.business.gate import run_gate
from app.services.chatbot.lanes.business.services import ResolveGateServices, production_services
from app.services.error_handler import AppException
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import (  # noqa: F401  - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
    stub_parser,
)

# --------------------------------------------------------------------------- #
# 1 and 2 (retired, see file header): the delegate seam and the shadow failure path.
# `BUSINESS_ARMS`/`OTHER_ARMS`/`_stub_bundle` were only these two classes' own helpers -
# removed with them, not left as dead code.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 3. D14 zero writes, with the lane ON, through the REAL resolve_entity seam.
# --------------------------------------------------------------------------- #


class _StubSpecResult:
    content = "{}"
    prompt_tokens = 1
    completion_tokens = 1
    total_tokens = 2


class _StubSpecProvider:
    def chat(self, messages, **kwargs):
        return _StubSpecResult()


class TestD14ZeroWritesWithTheLaneOnRealResolver:
    def test_a_dry_run_turn_through_the_real_resolver_writes_no_usage_log(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        """D14 says a dry-run turn writes ZERO rows outside `chatbot.turns`. The business
        lane's `resolve_entity` seam is the REAL `POST /system/references/resolve` route
        function (`app/services/chatbot/lanes/business/services.py::_resolve_entity`),
        which has no concept of `envelope.dry_run` at all - it is a general system route,
        not a chatbot-aware one. `resolve_gate.resolve_entity_body` unconditionally sets
        `spec_fallback: True` and `understand_phrase: True`
        (app/services/chatbot/lanes/business/resolve_gate.py:206-208), so a token that
        misses every normal probe (guaranteed on this blank schema) falls into the spec
        search path, which - when a model is configured - unconditionally writes an
        `ai_assistant_usage_logs` row and commits
        (app/services/product_spec_understanding.py:597-614, inside `understand_phrase`),
        with no dry-run guard anywhere in that call chain.
        """
        set_chatbot_switches(session_factory, business_lane=True)
        # `understand_phrase` writes `user_id=current_user.get("id")` off this setting; a
        # non-existent id makes the INSERT fail on a users.id FK instead of on dry-run
        # gating - which would make this test pass for the WRONG reason (measured: with
        # the real unseeded id the write 500s on FK, not on any dry-run check). NULL is a
        # legitimate value for this nullable column and isolates the one thing under test.
        monkeypatch.setattr(settings, "external_api_key_act_as_user_id", None)

        import app.services.product_spec_understanding as psu

        stub_provider = _StubSpecProvider()
        monkeypatch.setattr(
            psu,
            "_resolve_provider",
            lambda db, agent_name=psu.AGENT_NAME: (stub_provider, "stub-provider", "stub-model"),
        )

        qf = _parser_output(
            entities=[
                {
                    "raw": "ZZTNOSUCHPRODUCT999",
                    "hint": "product",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ]
        )
        stub_parser(qf)
        stub_access()

        envelope = _envelope(test_run_id="ZZT-run-s6a-1")
        envelope.message["message"]["message"]["text"] = "price for ZZTNOSUCHPRODUCT999"
        assert envelope.dry_run is True

        db = session_factory()
        usage_before = db.execute(text("SELECT COUNT(*) FROM ai_assistant_usage_logs")).scalar()
        session_vars_before = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        after_db = session_factory()
        usage_after = after_db.execute(text("SELECT COUNT(*) FROM ai_assistant_usage_logs")).scalar()
        session_vars_after = after_db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        assert result.branch_kind == "business_query"
        assert session_vars_after == session_vars_before, "D14: session_vars must be untouched"
        assert usage_after == usage_before, (
            "DEFECT (D14): a dry-run turn wrote an ai_assistant_usage_logs row. The "
            "business lane's resolve_entity seam "
            "(app/services/chatbot/lanes/business/services.py::_resolve_entity) calls "
            "the real resolve_reference_post regardless of envelope.dry_run, and that "
            "route's spec-search fallback "
            "(app/services/product_spec_understanding.py:597-614, understand_phrase) "
            "writes and commits AIAssistantUsageLog unconditionally whenever a model is "
            "configured - it has no dry-run parameter anywhere in the call chain."
        )


# --------------------------------------------------------------------------- #
# 4. Capacity rule: no DB session open during the resolver seam call.
# --------------------------------------------------------------------------- #


class TestCapacityRuleDuringResolverSeam:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "engine.py's own docstring at the S6a call site (app/services/chatbot/"
            "engine.py, the 'looked_up' stage comment beginning 'It runs INSIDE this "
            "session on purpose') documents that the resolver runs inside the same "
            "session opened for access/routed, holding it across the resolver's "
            "optional spec-search LLM call - the one place this turn breaks the plan's "
            "'never hold a DB session across provider I/O' rule. S6b owns the split "
            "into its own stage; flip this test green once that lands."
        ),
    )
    def test_no_db_session_is_open_during_the_resolver_seam_call(
        self, counting_session_factory, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        observed: list[int] = []

        def _resolve_entity(body):
            observed.append(counting_session_factory.state["open"])
            return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

        bundle = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(_resolve_entity),
            probe=lambda **_: None,
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: bundle,
        )
        stub_parser()
        stub_access()

        engine_mod.run_turn(_envelope(), session_factory=counting_session_factory)

        assert observed == [0], (
            "a DB session was held open during the resolver seam call - the S6a lane "
            "must close it before the resolver's optional LLM call and reopen after, "
            "the same discipline the parser call already has"
        )


# --------------------------------------------------------------------------- #
# 5. check_promotion end to end, real ContactAccessTypeService on Postgres.
# --------------------------------------------------------------------------- #


class TestCheckPromotionRealAccessTypeService:
    SPACE_ID = "364817-zzt"

    def _seed_workspace_and_contact(self, db) -> None:
        from app.models.respond_workspace import RespondWorkspace

        db.execute(
            text(
                "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
                "VALUES (gen_random_uuid(), :space_id, 'ZZT workspace', 'zzt-cipher') "
                "ON CONFLICT DO NOTHING"
            ),
            {"space_id": self.SPACE_ID},
        )
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
                "SELECT gen_random_uuid()::text, :cid, :phone, '{\"variables\": {}}'::jsonb, w.id "
                "FROM respond_workspaces w WHERE w.space_id = :space_id "
                "ON CONFLICT DO NOTHING"
            ),
            {"cid": str(CONTACT_ID), "phone": "+60000000010", "space_id": self.SPACE_ID},
        )
        db.commit()

    def _assign_access_type(self, db, *, code: str, name: str) -> None:
        db.execute(
            text(
                "INSERT INTO contact_access_types (code, name, is_active) "
                "VALUES (:code, :name, true) ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "name": name},
        )
        db.execute(
            text(
                "INSERT INTO respond_contact_access_types (contact_id, access_type_code) "
                "SELECT c.id, :code FROM respond_contacts c WHERE c.respond_io_id = :cid"
            ),
            {"code": code, "cid": str(CONTACT_ID)},
        )
        db.commit()

    def _services(self, db) -> ResolveGateServices:
        from app.services.chatbot.lanes.business.services import production_services

        real = production_services(db, space_id=self.SPACE_ID)

        def _resolve_entity(body):
            raise AssertionError("resolve-entity must not run when If4 takes its FALSE leg")

        return ResolveGateServices(
            access_types=real.access_types, resolve_entity=validating_resolve_entity(_resolve_entity), probe=lambda **_: None
        )

    def _ctx(self) -> dict[str, Any]:
        return {
            "contact": {"id": CONTACT_ID},
            "parse": {"output": {"domain_hint": "promotion", "entities": []}},
            "session": {},
        }

    def test_no_tier_asks(self, session_factory) -> None:
        db = session_factory()
        self._seed_workspace_and_contact(db)
        # No access type assigned at all: zero entitlement names.

        out = resolve_gate.run(
            self._ctx(), "access_check", {}, services=self._services(db), space_id=self.SPACE_ID
        )
        assert out["_exit_kind"] == "access_ask"
        assert out["aggregate"] == {"name": []}

    def test_one_tier_continues(self, session_factory) -> None:
        db = session_factory()
        self._seed_workspace_and_contact(db)
        self._assign_access_type(db, code="zzt_sorento_dealer", name="Sorento Dealer")

        def _resolve_entity(body):
            return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

        real = production_services(db, space_id=self.SPACE_ID)
        services = ResolveGateServices(
            access_types=real.access_types, resolve_entity=validating_resolve_entity(_resolve_entity), probe=lambda **_: None
        )
        out = resolve_gate.run(
            self._ctx(), "access_check", {}, services=services, space_id=self.SPACE_ID
        )
        assert out["_exit_kind"] != "access_ask"
        assert out["aggregate"] == {"name": ["Sorento Dealer"]}
        assert out["resolved"] is not None


# --------------------------------------------------------------------------- #
# 6. `_locale_compare`, `entity_pins` AND-mode omission, and the 400 pin mismatch.
# --------------------------------------------------------------------------- #


class TestLocaleCompareMixedCase:
    def test_all_lowercase_sorts_before_capitalised_at_equal_primary_strength(self) -> None:
        from app.services.chatbot.lanes.business.gate import _locale_compare

        assert _locale_compare("acme", "Acme") == -1
        assert _locale_compare("Acme", "acme") == 1
        assert _locale_compare("Acme", "Acme") == 0

    def test_a_mixed_case_company_pair(self) -> None:
        from app.services.chatbot.lanes.business.gate import _locale_compare

        assert _locale_compare("mastile klang sdn bhd", "Mastile Klang Sdn Bhd") == -1
        assert _locale_compare("Mastile Klang Sdn Bhd", "mastile klang sdn bhd") == 1


class TestEntityPinsBody:
    def _ctx(self, *, match_mode: str) -> dict[str, Any]:
        return {
            "contact": {"id": "c1"},
            "text": {"message": {"message": {"text": "ZZT-1"}}},
            "parse": {
                "output": {
                    "match_mode": match_mode,
                    "entities": [
                        {
                            "hint": "product",
                            "raw": "ZZT-1",
                            "uuid": "11111111-1111-1111-1111-111111111111",
                            "canonical_code": "ZZT-1",
                        }
                    ],
                }
            },
        }

    def test_and_mode_omits_entity_pins_even_with_a_pinned_uuid(self) -> None:
        body = resolve_gate.resolve_entity_body(self._ctx(match_mode="and"))
        assert "entity_pins" not in body
        assert body["match_mode"] == "and"

    def test_or_mode_with_a_pinned_uuid_carries_entity_pins(self) -> None:
        body = resolve_gate.resolve_entity_body(self._ctx(match_mode="or"))
        assert body["entity_pins"] == {"ZZT1": "11111111-1111-1111-1111-111111111111"}


class TestEntityPinMismatchSurfaces:
    """H38, through the IN-PROCESS call the S6a seam actually makes - no HTTP round trip."""

    def test_a_pin_the_resolver_cannot_match_raises_the_400_through_the_seam(
        self, session_factory
    ) -> None:
        db = session_factory()
        services = production_services(db)
        body = {
            "query": "ZZT-NOMATCH-1",
            "tokens": ["ZZT-NOMATCH-1"],
            "match_mode": "or",
            "allowed_entity_types": ["product"],
            "entity_pins": {"ZZT-NOMATCH-1": "22222222-2222-2222-2222-222222222222"},
        }
        with pytest.raises(AppException) as excinfo:
            services.resolve_entity(body)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail["code"] == "ENTITY_PIN_MISMATCH"


# --------------------------------------------------------------------------- #
# 7. `is_timeline` contains-sentinel (H46) - the three shapes named in the brief.
# --------------------------------------------------------------------------- #


class TestIsTimelineContainsSentinel:
    def test_the_three_named_shapes(self) -> None:
        assert contracts.is_timeline(["__all__", "eta_delay_date"]) is True
        assert contracts.is_timeline(["eta_delay_date"]) is False
        assert contracts.is_timeline([]) is False


# --------------------------------------------------------------------------- #
# 8. Canary: the fixture DOES observe a deliberate write made mid-run_turn.
# --------------------------------------------------------------------------- #


class TestFixtureObservesADeliberateWriteMidTurn:
    """A kill-test guard for the D14 tests above (this file's TestD14... and
    test_engine.py::TestDryRun::test_a_test_envelope_writes_nothing_outside_chatbot_turns).

    Both of those assert a NEGATIVE ("nothing was written"), which is only meaningful if
    the fixture's `session_factory` (`tests/chatbot/conftest.py`, itself built on
    `tests/_pg_fixture.py`'s `blank_schema_engine` + one shared connection with
    `Session(bind=connection, join_transaction_mode="create_savepoint")`) reliably makes a
    commit issued on the `db` object mid-`run_turn` visible to a FRESH session opened
    later on the same connection. If it did not - if a savepoint release from one of the
    several sessions `run_turn` opens and closes per turn were silently undone by a later
    stage's own commit/rollback - every "wrote nothing" assertion in this suite would pass
    vacuously no matter what the application code does.

    Proven here by forcing a write nobody asked for (`check_access` boobytrapped to also
    UPDATE `respond_contacts.session_vars`, using the exact `db` session `run_turn` itself
    opened for the access/routed stage) and asserting it IS visible afterwards. Measured
    twice while diagnosing a coordinator-reported concern (2026-09-05): forcing the
    business lane's resolver seam to ignore `dry_run` made
    `TestD14ZeroWritesWithTheLaneOnRealResolver` go red (`before=0 after=1`), and this same
    mechanism made the head-level `TestDryRun` test go red too
    (`before={'variables': {}} after={'poisoned': True, 'variables': {}}`) - so the
    negative assertions are not vacuous. This test keeps that proof permanent rather than
    a one-off manual check.
    """

    def test_a_forced_write_through_the_real_session_survives_to_a_fresh_read(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        stub_parser()
        stub_access()

        def _boobytrap_check_access(db, *, agent_code, contact_id, space_id):
            db.execute(
                text(
                    "UPDATE respond_contacts SET session_vars = jsonb_set("
                    "COALESCE(session_vars, '{}'::jsonb), '{canary}', 'true'::jsonb) "
                    "WHERE respond_io_id = :c"
                ),
                {"c": contact_id},
            )
            db.commit()
            return {
                "allowed": True,
                "decision": "allow",
                "agent_name": "General Enquiries",
                "attributes": None,
                "all_attributes_allowed": None,
            }

        monkeypatch.setattr(engine_mod, "check_access", _boobytrap_check_access)
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

        db = session_factory()
        before = db.execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        engine_mod.run_turn(_envelope(test_run_id="ZZT-run-canary-1"), session_factory=session_factory)

        after = session_factory().execute(
            text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        ).scalar()

        assert (before or {}).get("canary") is None
        assert after.get("canary") is True, (
            "the fixture did not observe a write made mid-run_turn on a fresh session - "
            "every 'wrote nothing' D14 assertion in this suite would be vacuous"
        )


# --------------------------------------------------------------------------- #
# Owner ruling, console pass 3 (6 Sep 2026), item A: the ambiguous-customer picker
# ("Which customer do you mean?", `gate.py`'s "AMBIGUOUS CUSTOMER -> ASK WHICH COMPANY"
# block, ~line 650) must stamp each option "N. <name> (<company code>) - has DO" / "-
# no DO", the same shape as the ported `pickers.annotate_incoming` "- has incoming" /
# "- no incoming" stamp and the retired spine's "has stock details" did-you-mean stamp.
# Evidence turns 5ea477a6 (#15) and 934d4c18 (#16).
#
# `ResolveGateServices.probe`'s production binding is itself unreachable
# (`app/services/chatbot/lanes/business/services.py::_probe` raises `NotImplementedError`
# - S6b's entity-ids-transformer/output-structurer wiring for the PICKER probe has not
# landed, unlike the fetch step's own use of them). The DO-stamp test below therefore
# builds the picker's `probe` argument from a REAL `OrderService(db).list_orders(...)`
# read against REAL seeded Postgres rows - never a hand-built n8n dict - wrapped in the
# envelope shape `sorento_crm_mcp/presenters.py` documents for
# `crm_order_management_orders_list` ("Customer" / "Actual Delivery Date" fields, read
# verbatim from that file, 2026-09-07): the exact shape the real probe would hand
# `annotate_customer` once wired.
# --------------------------------------------------------------------------- #


class TestOwnerRulingACustomerPickerLabelCarriesCompanyCode:
    def test_which_customer_lines_carry_the_company_code(self) -> None:
        """`gate.py`'s ambiguous-customer block renders "N. <name>" only today - no
        company code. Built from `ResolvedEntity.as_dict()`'s own field shape
        (app/services/entity_resolver.py), never a hand-built n8n dict.

        AMENDED 7 Sep 2026 (coder, with the captain): this test first asserted the
        CUSTOMER code ("ZZTDOA"). The owner's words were "company code", and the shape
        they named it from is the old spine's own "A CRAFT IDEA SDN BHD (SRT)" - SRT is
        the CRM company (`companies.code`; the two rows on this install are SRT and
        MOCHA), not a customer code. That is also the only reading that does any work
        here: the picker exists because ONE customer name resolves to several accounts,
        and printing a per-account code next to each line would still leave two lines of
        the same name to choose between when the duplicate is one account per company
        ledger. The resolver now stamps `company_code` beside `company_name`
        (`_attach_company_info`), and the fixture below carries it exactly as the real
        payload does."""
        resolver = {
            "resolutions": [
                {
                    "token": "ambigco",
                    "matches": [
                        {
                            "entity_type": "customer",
                            "canonical_code": "ZZTDOA",
                            "uuid": "cust-a",
                            "match_field": "customer_name",
                            "match_tier": "substring",
                            "similarity": None,
                            "company_id": "zzt-co-srt",
                            "company_name": "Sorento",
                            "company_code": "SRT",
                            "display": {
                                "customer_name": "ZZT Ambigco Alpha Sdn Bhd",
                                "phone_number": None,
                                "email": None,
                            },
                        },
                        {
                            "entity_type": "customer",
                            "canonical_code": "ZZTDOB",
                            "uuid": "cust-b",
                            "match_field": "customer_name",
                            "match_tier": "substring",
                            "similarity": None,
                            "company_id": "zzt-co-mocha",
                            "company_name": "Mocha",
                            "company_code": "MOCHA",
                            "display": {
                                "customer_name": "ZZT Ambigco Beta Sdn Bhd",
                                "phone_number": None,
                                "email": None,
                            },
                        },
                    ],
                }
            ]
        }
        parser = {
            "domain_hint": "order",
            "entities": [
                {"raw": "ambigco", "hint": "customer", "canonical_code": None, "current_message": True}
            ],
        }

        out = run_gate({}, parser=parser, resolver=resolver)

        assert out["require_specific"] is True, (
            "fixture must reach the ambiguous-customer branch: "
            f"gate_reason={out.get('gate_reason')!r}"
        )
        clarification = out["gate_clarification"]
        assert "ZZT Ambigco Alpha Sdn Bhd (SRT)" in clarification, (
            f"the picker line must carry the owning company's code next to the customer "
            f"name: {clarification!r}"
        )
        assert "ZZT Ambigco Beta Sdn Bhd (MOCHA)" in clarification, (
            f"the picker line must carry the owning company's code next to the customer "
            f"name: {clarification!r}"
        )
        # The roster the pick resolves against carries the SAME label, so a customer who
        # types the whole line back resolves to the row they read.
        assert [e["title"] for e in out["compatible_entities"]] == [
            "ZZT Ambigco Alpha Sdn Bhd (SRT)",
            "ZZT Ambigco Beta Sdn Bhd (MOCHA)",
        ], out["compatible_entities"]


class TestOwnerRulingACustomerPickerDOStamp:
    def test_which_customer_lines_carry_has_do_and_no_do(self, session_factory) -> None:
        """Two similarly-named customers under Sorento (the suite's default company
        scope, tests/conftest.py): one with a DO (an order carrying
        `actual_delivery_date`, app/models/order.py:271), one with none. The picker's
        suffix must read "- has DO" / "- no DO", never today's "- has delivery" /
        "- no delivery" / "- no recent delivery", and must not confuse "an order
        exists" with "a DO exists" - `pickers.annotate_customer`'s `with_delivery` set
        is built from ANY matching order row today, with no read of the delivery-date
        field at all, so customer B (an order but no DO YET) is the second, substantive
        defect this test catches alongside the wording."""
        from datetime import date

        from app.models.order import Customer, Order
        from app.services.order_service import OrderService

        db = session_factory()
        cust_a = Customer(customer_code="ZZTDOA", customer_name="ZZT Ambigco Alpha Sdn Bhd")
        cust_b = Customer(customer_code="ZZTDOB", customer_name="ZZT Ambigco Beta Sdn Bhd")
        db.add_all([cust_a, cust_b])
        db.commit()
        order_a = Order(
            order_number="ZZTDOORD-A1",
            customer_id=cust_a.id,
            debtor_name=cust_a.customer_name,
            actual_delivery_date=date(2026, 6, 1),
        )
        order_b = Order(
            order_number="ZZTDOORD-B1",
            customer_id=cust_b.id,
            debtor_name=cust_b.customer_name,
            actual_delivery_date=None,
        )
        db.add_all([order_a, order_b])
        db.commit()

        rows = OrderService(db).list_orders(customer_ids=[cust_a.id, cust_b.id], limit=100)["data"]
        assert len(rows) == 2, f"seed must produce exactly the two orders under test, got {len(rows)}"

        # The exact envelope shape `sorento_crm_mcp/presenters.py` builds for
        # `crm_order_management_orders_list` (`("Customer", o.get("debtor_name"))`,
        # `("Actual Delivery Date", o.get("actual_delivery_date"))`), fed with the REAL
        # order rows just read - this is what the real probe would hand the annotator.
        probe = {
            "items": [
                {
                    "title": row.order_number,
                    "fields": [
                        {"label": "Customer", "value": row.debtor_name},
                        {
                            "label": "Actual Delivery Date",
                            "value": row.actual_delivery_date.isoformat()
                            if row.actual_delivery_date
                            else None,
                        },
                    ],
                }
                for row in rows
            ]
        }

        gate = {
            "gate_clarification": (
                "Which customer do you mean? Please choose:\n"
                f"1. {cust_a.customer_name} ({cust_a.customer_code})\n"
                f"2. {cust_b.customer_name} ({cust_b.customer_code})"
            ),
            "compatible_entities": [
                {
                    "uuid": cust_a.id,
                    "entity_type": "customer",
                    "code": cust_a.customer_code,
                    "title": cust_a.customer_name,
                },
                {
                    "uuid": cust_b.id,
                    "entity_type": "customer",
                    "code": cust_b.customer_code,
                    "title": cust_b.customer_name,
                },
            ],
        }

        # PICKER-ONE (owner, 13 Oct 2026): the gate's customer arm records the
        # measurement as data and every line is stamped by `pickers.customer_stamp`.
        out = resolve_gate.measure_customer_picker(dict(gate), probe=probe, parser={})
        bases = set(out["customer_bases_with_do"])
        stamp_a = pickers.customer_stamp(cust_a.customer_name, bases)
        stamp_b = pickers.customer_stamp(cust_b.customer_name, bases)

        assert stamp_a == "has DO", (
            f"a customer WITH a DO (actual_delivery_date set) must read 'has DO': {stamp_a!r}"
        )
        assert stamp_b == "no DO", (
            f"a customer with NO DO (an order, but no delivery date) must read 'no DO': "
            f"{stamp_b!r}"
        )

        # Guard: the measurement only adds data; the roster's rows and order are untouched.
        assert out["compatible_entities"][0]["uuid"] == cust_a.id
        assert out["compatible_entities"][0]["title"] == cust_a.customer_name


# --------------------------------------------------------------------------- #
# Owner ruling A, the PRODUCTION seam. The stamp above is worth nothing until the
# ambiguous-customer branch actually reaches a probe on a live turn:
# `ResolveGateServices.probe`'s production binding raised `NotImplementedError` until
# 7 Sep 2026, so `resolve_gate._run_probe` caught it on every single turn and the
# annotator rendered the BARE picker.
#
# This test crosses the whole seam and stops at the socket (lesson 102): the gate's own
# `run_gate` decides the branch, `resolve_gate.run` calls the PRODUCTION bundle's `probe`,
# `entity_ids_transformer` builds the tool arguments, and only `MCPRuntimeClient.call_tool`
# is replaced - with a fake that answers the STRING the real client answers (the shape that
# defeated three fixes in #700), built from a REAL `OrderService(db).list_orders(...)` read
# of the seeded rows using the arguments the transformer actually emitted. So the customer
# uuids, the delivery window, the string parse and the annotator all run for real.
# --------------------------------------------------------------------------- #


class TestOwnerRulingATheCustomerPickerReachesTheProductionProbeSeam:
    SPACE_ID = "364817"

    def _seed(self, db: Any) -> tuple[Any, Any, str]:
        from datetime import date

        from app.models.company import Company
        from app.models.order import Customer, Order
        from tests._pg_fixture import unique_code

        company = Company(name="ZZT Probe Seam Co", code=unique_code("ZPS")[:50])
        db.add(company)
        db.flush()
        cust_a = Customer(
            customer_code="ZZTSEAMA",
            customer_name="ZZT Seamco Alpha Sdn Bhd",
            company_id=company.id,
        )
        cust_b = Customer(
            customer_code="ZZTSEAMB",
            customer_name="ZZT Seamco Beta Sdn Bhd",
            company_id=company.id,
        )
        db.add_all([cust_a, cust_b])
        db.commit()
        db.add_all(
            [
                Order(
                    order_number="ZZTSEAMORD-A1",
                    customer_id=cust_a.id,
                    debtor_name=cust_a.customer_name,
                    actual_delivery_date=date(2026, 9, 1),
                    company_id=company.id,
                ),
                Order(
                    order_number="ZZTSEAMORD-B1",
                    customer_id=cust_b.id,
                    debtor_name=cust_b.customer_name,
                    actual_delivery_date=None,
                    company_id=company.id,
                ),
            ]
        )
        db.commit()
        return cust_a, cust_b, str(company.code)

    def _resolver_payload(self, cust_a: Any, cust_b: Any, company_code: str) -> dict[str, Any]:
        def _match(cust: Any) -> dict[str, Any]:
            return {
                "entity_type": "customer",
                "canonical_code": cust.customer_code,
                "uuid": cust.id,
                "match_field": "customer_name",
                "match_tier": "substring",
                "similarity": None,
                "company_id": cust.company_id,
                "company_name": "ZZT Probe Seam Co",
                "company_code": company_code,
                "display": {"customer_name": cust.customer_name},
            }

        return {
            "tokens": ["seamco"],
            "resolutions": [
                {
                    "token": "seamco",
                    "resolved": False,
                    "ambiguous": True,
                    "matches": [_match(cust_a), _match(cust_b)],
                    "alternatives": [],
                }
            ],
            "unresolved_tokens": [],
        }

    def test_the_ambiguous_customer_branch_probes_through_the_production_bundle(
        self, session_factory, monkeypatch
    ) -> None:
        """`resolve_gate.run` -> `services.probe` -> `entity_ids_transformer` -> the MCP
        client, then the answer back through `parse_mcp_content` into the annotator. The
        seam is graded on what it ASKS (both seeded customer uuids, the render view, the
        90-day delivery window) and on what the customer then reads."""
        import json as _json

        from app.services.ai_assistant_service import MCPRuntimeClient
        from app.services.order_service import OrderService

        from app.models.base import set_company_scope

        db = session_factory()
        cust_a, cust_b, company_code = self._seed(db)
        # The scope the ENGINE stamps on every session it opens (`engine._scoped_factory`,
        # from the contact's own `respond_contact_companies` rows). Without it the suite's
        # Sorento default hides the seeded company's orders and the probe reads zero rows -
        # which is the "no DO" answer, arrived at for the wrong reason.
        set_company_scope(db, frozenset({cust_a.company_id}))
        resolver = self._resolver_payload(cust_a, cust_b, company_code)

        asked: list[tuple[str, dict[str, Any]]] = []

        def _fake_call_tool(self_client, tool_name: str, args: dict[str, Any]) -> str:
            asked.append((tool_name, args))
            rows = OrderService(db).list_orders(
                customer_ids=list(args.get("customer_ids") or []), limit=100
            )["data"]
            # `sorento_crm_mcp/presenters.py::_orders_list`, field for field, over the REAL
            # rows - and returned as the STRING `MCPRuntimeClient.call_tool` returns.
            return _json.dumps(
                {
                    "answers": [
                        {
                            "title": row.order_number,
                            "fields": [
                                {"label": "Order Number", "value": row.order_number},
                                {"label": "Customer", "value": row.debtor_name},
                                {
                                    "label": "Actual Delivery Date",
                                    "value": row.actual_delivery_date.isoformat()
                                    if row.actual_delivery_date
                                    else None,
                                },
                            ],
                        }
                        for row in rows
                    ]
                }
            )

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", _fake_call_tool)

        services = production_services(db, space_id=self.SPACE_ID)

        def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            return resolver

        services = ResolveGateServices(
            access_types=services.access_types,
            resolve_entity=validating_resolve_entity(_resolve_entity),
            probe=services.probe,  # THE seam under test, production binding
        )

        ctx = {
            "contact": {"id": CONTACT_ID},
            "parse": {
                "output": {
                    "domain_hint": "order",
                    "intent_hint": "check_order",
                    "entities": [
                        {
                            "raw": "seamco",
                            "hint": "customer",
                            "canonical_code": None,
                            "current_message": True,
                        }
                    ],
                }
            },
            "session": {},
        }

        out = resolve_gate.run(
            ctx,
            "resolve",
            {},
            services=services,
            space_id=self.SPACE_ID,
            probe_default_start="2026-06-09",
        )

        assert asked, (
            "the ambiguous-customer branch never reached `ResolveGateServices.probe` - "
            "the production binding is unwired again, and every live picker renders bare"
        )
        tool_name, args = asked[0]
        assert tool_name == resolve_gate.CUSTOMER_PROBE_TOOL, tool_name
        assert set(args.get("customer_ids") or []) == {cust_a.id, cust_b.id}, args
        assert args.get("view") == "render", args
        assert args.get("actual_delivery_date_from") == "2026-06-09", args

        # PICKER-ONE: what the customer reads is the bridge's one customer picker,
        # stamped from what this probe measured. Both customers sit in ONE company, so
        # no line carries the company tag (it is printed only when companies differ).
        from app.services.chatbot import answer_bridge, copy as copy_mod

        answer = answer_bridge.question_for(
            out,
            parser=ctx["parse"]["output"],
            ctx=ctx,
            canned=copy_mod.fallback_copy(),
            db=db,
            asked_at_turn=1,
        )
        assert answer is not None, out.get("_exit_kind")
        assert answer.text == (
            "Which one do you mean?\n"
            f"1. {cust_a.customer_name} - has DO\n"
            f"2. {cust_b.customer_name} - no DO"
        ), answer.text
        assert f"({company_code})" not in answer.text, answer.text


# --------------------------------------------------------------------------- #
# Review of #706, S1 + S2. `_cust_base` groups a customer FAMILY by normalised name
# only - n8n's own grouping, and the two production captures of the shape (rg-15114061,
# JYL JUBIN under Mocha and Sorento; rg-15125764, YI HONG TILING under both) show live
# rendering a cross-ledger family as ONE picker line whose pick carries both ledgers'
# uuids. That is kept: the customer named one business, the CRM holds it in two ledgers,
# and the order answer downstream is already labelled per company (`routing_companies`),
# so a silent pass with both ledgers is the complete answer, not a wrong one (S2 - the
# coder's disagreement with the review, stated in the PR). What was wrong (S1) is the
# SUFFIX: it read the representative row's code alone, so a two-ledger family printed one
# ledger while its pick reached both. It now names every ledger the family spans.
# --------------------------------------------------------------------------- #


def _cust_row(uuid: str, name: str, *, company: tuple[str, str, str], code: str) -> dict:
    """One resolver customer match in `ResolvedEntity.as_dict()`'s shape."""
    company_id, company_name, company_code = company
    return {
        "entity_type": "customer",
        "canonical_code": code,
        "uuid": uuid,
        "match_field": "customer_name",
        "match_tier": "substring",
        "similarity": None,
        "company_id": company_id,
        "company_name": company_name,
        "company_code": company_code,
        "display": {"customer_name": name, "phone_number": None, "email": None},
    }


SRT = ("zzt-co-srt", "Sorento", "SRT")
MOCHA = ("zzt-co-mocha", "Mocha", "MOCHA")


class TestAPickerLineNamesEveryLedgerItsFamilySpans:
    PARSER = {
        "domain_hint": "order",
        "entities": [
            {"raw": "a craft idea", "hint": "customer", "canonical_code": None, "current_message": True}
        ],
    }

    def _gate(self, matches: list[dict]) -> dict:
        from app.services.chatbot.lanes.business.gate import run_gate

        resolver = {"resolutions": [{"token": "a craft idea", "matches": matches}]}
        return run_gate({}, parser=dict(self.PARSER), resolver=resolver)

    def test_a_two_ledger_family_prints_both_codes(self) -> None:
        """S1. Two families: "A CRAFT IDEA" spanning SRT and MOCHA, and "A CRAFT IDEA
        TRADING" in SRT only. The first line must say both ledgers, because its pick
        carries both ledgers' rows; the second says its one."""
        out = self._gate(
            [
                _cust_row("u-srt-1", "A CRAFT IDEA SDN BHD", company=SRT, code="300-A001"),
                _cust_row("u-mocha", "A CRAFT IDEA SDN BHD", company=MOCHA, code="300-A001"),
                _cust_row("u-srt-2", "A CRAFT IDEA TRADING SDN BHD", company=SRT, code="300-A002"),
            ]
        )
        assert out["require_specific"] is True, out.get("gate_reason")
        titles = [e["title"] for e in out["compatible_entities"]]
        assert titles == ["A CRAFT IDEA SDN BHD (SRT, MOCHA)", "A CRAFT IDEA TRADING SDN BHD (SRT)"], (
            f"a family spanning two ledgers must name both, in first-seen order: {titles!r}"
        )
        families = out["picker_families"]
        assert set(families["A CRAFT IDEA"]) == {"u-srt-1", "u-mocha"}, families
        assert "1. A CRAFT IDEA SDN BHD (SRT, MOCHA)" in out["gate_clarification"]

    def test_a_ledger_with_no_code_on_the_row_contributes_nothing(self) -> None:
        """An older payload: the MOCHA row has no `company_code`. The line names what it
        can and never prints an empty pair of brackets."""
        mocha_no_code = _cust_row("u-mocha", "A CRAFT IDEA SDN BHD", company=MOCHA, code="300-A001")
        mocha_no_code["company_code"] = None
        out = self._gate(
            [
                _cust_row("u-srt-1", "A CRAFT IDEA SDN BHD", company=SRT, code="300-A001"),
                mocha_no_code,
                _cust_row("u-srt-2", "A CRAFT IDEA TRADING SDN BHD", company=SRT, code="300-A002"),
            ]
        )
        titles = [e["title"] for e in out["compatible_entities"]]
        assert titles[0] == "A CRAFT IDEA SDN BHD (SRT)", titles

    def test_the_same_name_in_two_ledgers_is_one_family_and_passes_with_both(self) -> None:
        """S2, the disagreement stated as a test: one name in two ledgers is ONE family
        (live parity, captures rg-15114061 / rg-15125764), the gate passes, and BOTH
        ledgers' rows go forward so the order answer covers both, labelled per company."""
        out = self._gate(
            [
                _cust_row("u-srt", "A CRAFT IDEA SDN BHD", company=SRT, code="300-A001"),
                _cust_row("u-mocha", "A CRAFT IDEA SDN BHD", company=MOCHA, code="300-A001"),
            ]
        )
        assert out["require_specific"] is False, out.get("gate_reason")
        assert {e["uuid"] for e in out["compatible_entities"]} == {"u-srt", "u-mocha"}
