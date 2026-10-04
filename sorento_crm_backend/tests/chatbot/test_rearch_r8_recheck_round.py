"""R8 RED tests, PR #952 - Phase 3 RE-CHECK findings (tester 39, 20 Sep 2026), written
BEFORE the coder's R8 fix pass, from `.claude/handoffs/rearch-phase3-security-recheck.md`
(B2, S4) and `rearch-phase3-reviewer-recheck.md` (MB-1, MB-3, SF-1, SF-2), both measured
against coder head `6aeed4188`.

**Zero live runs**: no journey runner, no turns to :8081, no OpenAI calls, no clone-DB
query - every test here is a pure/DB-fixture pytest (Postgres, `session_factory` blank
schema) or a direct call into the module under test. Every scenario seeds its own rows.

**Test shape**: engine-level (`engine.run_turn`, real resolver/gate/narrower, both MCP
seams doubled) wherever the finding is reachable that way; a few items are deliberately
function/DB-level, matching the precedent `test_rearch_r5_production_decides.py::
TestSilentPrefixFilter` and the security re-check report's own methodology ("unit probe,
no DB, no MCP") already set for "this item is about one seam's own contract, not engine
precedence."
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import answer_bridge
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _enable_business_lane,
    _seed_contact as _seed_business_contact,
    _session_of,
)
from tests.chatbot.test_rearch_r3_bridge_engine import _ambiguous_hanlim_resolve_services
from tests.chatbot.test_rearch_r4_bridge_miss import (
    _dym_incoming_sibling_scenario,
    _expected_miss_text,
)
from tests.chatbot.test_rearch_r5_production_decides import (
    _mcp_double,
    _mcp_probe_for,
    _run_turn_real,
    _seed_contact_and_get,
)
from tests.chatbot.test_rearch_r6_review_round import (
    _capturing_probe,
    _grant_access_type,
    _mark_workspace_default,
    _run_turn_engine,
    _run_turn_engine_real,
    _seed_access_type,
    _seed_three_tiers_two_entitled,
)
from tests.chatbot.test_rearch_r7_live_parity_replay import _seed_real_attachment_type
from tests.chatbot.test_outstanding_lane import _run_turn as _run_turn_fake_resolver


# --------------------------------------------------------------------------- #
# Item 1 - SECURITY B2: the "more" page of a described set is counted with the
# PARSER's access levels, not the contact's; page 1 and page 2 disagree.
#
# Function/DB-level (matches the security report's own methodology at this exact
# seam, `turn_runtime.py:1184-1186`/`1365-1396`/`1399-1444`): `page_the_set` and
# `set_page_carry` are called exactly the way `turn_runtime.make_tool_runner`'s
# own `runner()` closure calls them, over REAL seeded products/promotions -
# never a hand-typed unrecognised-word probe.
# --------------------------------------------------------------------------- #


def _seed_class_spec(session_factory, *, product_code: str, class_label: str) -> None:
    """`resolve_product_set`'s scope-term membership clause (`product_spec_search.py::
    filter_specs`) is a predicate over `ProductSpecifications.values`, joined via an
    `exists()` - a product filed under a category alone (`_seed_products`'s own
    `ProductCategory.class_label`) never qualifies membership by itself on a fresh
    scratch schema (that only happens on the real catalogue via a backfill ETL, R15/
    AC-1339's own docstring). Seeding the derived row directly is what `test_rearch_
    s3_attribute_first.py`'s own counted-set tests never needed (they assert
    `branch_kind` only) but a real `qualifying_total`/product-id assertion does."""
    import uuid

    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    db = session_factory()
    db.info["company_scope"] = frozenset({DEFAULT_COMPANY_ID})
    product = db.query(Product).filter(Product.product_code == product_code).one()
    db.add(
        ProductSpecifications(
            id=str(uuid.uuid4()), product_id=product.id, values={"class": {"value": class_label}}
        )
    )
    db.commit()
    db.close()


class TestSetPageCarryNeverRecordsTheEntitlementItAnsweredUnder:
    def test_the_carry_has_no_access_levels_field_at_all(self, session_factory) -> None:
        """Root cause named in the security re-check: `set_page_carry` stores
        `{set_key, offset}` and nothing else - a later 'more' page has no way to
        recount under the SAME entitlement page 1 used. Main's own analogue is
        `access_levels_used` (`lanes/business/__init__.py:1571`)."""
        from app.services.chatbot import turn_runtime
        from app.services.chatbot.turn.plan import FetchSpec

        predicate = {
            "qualifying_total": 5,
            "class_labels": ["taps"],
            "require": {"promotion": True},
        }
        spec = FetchSpec(domain="promotion", entities=[], filters={}, date_window=None)
        carry = turn_runtime.set_page_carry(predicate, spec, ["tap"])
        assert carry is not None, "test setup sanity: a real qualifying set must carry a page"
        assert "access_levels" in (carry.get("set_key") or {}), (
            "security B2 root cause: set_page_carry must record the ENTITLED access "
            f"levels page 1 answered under (main's own access_levels_used), or a later "
            f"'more' page has nothing to recount honestly by: {carry!r}"
        )


class TestPageTheSetRecountDisclosesATierRestrictedPromotion:
    def test_page_2_with_the_parsers_empty_access_levels_widens_past_page_1s_entitlement(
        self, session_factory
    ) -> None:
        """AC-1698 + security B2: a contact entitled to the END-USER tier only asks a
        described-set promo question. Page 1 (the kept lane's own entitlement-filtered
        fetch) counts and lists only the end-user-visible product. The customer replies
        "more"; the runner calls `page_the_set(db, carry, access_levels=list(verdict.
        get("access_levels") or []))` - and the parser's own verdict carries `access_
        levels: []` in 249 of 249 real captures. `page_the_set` is called here EXACTLY
        that way (the runner's own call shape, `turn_runtime.py:1184-1186`), over two
        REAL seeded products: one with a promotion visible ONLY to a tier this contact
        does not hold, one visible to the contact's own tier."""
        from app.services.chatbot import turn_runtime
        from tests.chatbot.test_rearch_s3_attribute_first import _seed_products, _seed_promotion

        _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")
        _seed_access_type(session_factory, code="end_user", name="End User")

        class_label = unique_code("promotap").replace("-", "").lower()
        codes = _seed_products(
            session_factory, class_label=class_label, synonyms=[f"{class_label}s"], count=2
        )
        dealer_only_code, end_user_code = codes[0], codes[1]
        _seed_promotion(session_factory, [dealer_only_code], access_levels=["sorento_dealer"])
        _seed_promotion(session_factory, [end_user_code], access_levels=["end_user"])
        _seed_class_spec(session_factory, product_code=dealer_only_code, class_label=class_label)
        _seed_class_spec(session_factory, product_code=end_user_code, class_label=class_label)

        db = session_factory()
        db.info["company_scope"] = frozenset({DEFAULT_COMPANY_ID})
        carry = {
            "set_key": {
                "require": {"promotion": True},
                "scope_terms": [class_label],
                "domain": "promotion",
                "set_noun": "products",
            },
            "offset": 0,
        }

        # Page 1: the KEPT lane's own real entitlement (the contact's actual tier).
        page1_predicate, page1_ids = turn_runtime.page_the_set(
            db, carry, access_levels=["End User"]
        )
        assert page1_predicate["qualifying_total"] == 1, (
            f"test setup sanity: entitlement-filtered page 1 must count ONLY the "
            f"end-user-visible product: {page1_predicate!r}"
        )

        # Page 2 ("more"): the runner's OWN call shape - the parser's literal, empty
        # `access_levels` (the measured real-capture shape), never the entitlement.
        page2_predicate, page2_ids = turn_runtime.page_the_set(db, carry, access_levels=[])

        assert page2_predicate["qualifying_total"] == page1_predicate["qualifying_total"], (
            "security B2: the 'more' page must recount under the SAME entitlement page "
            f"1 used, never widen past it just because the parser stated nothing this "
            f"turn: page1={page1_predicate!r} page2={page2_predicate!r}"
        )
        assert set(page2_ids) == set(page1_ids), (
            "security B2: a tier-restricted promotion's own product must never surface "
            f"on the 'more' page just because this turn's parser verdict carried no "
            f"access_levels claim of its own: page1_ids={page1_ids!r} "
            f"page2_ids={page2_ids!r}"
        )
        db.close()

    def test_a_carry_with_no_recorded_entitlement_refuses_the_page_rather_than_running_unfiltered(
        self, session_factory
    ) -> None:
        """The other half of the same rule: when the carry itself holds no entitlement
        at all (today's actual shape - `set_page_carry` never stores one, see the sibling
        test), the page must be REFUSED (no product list, no total) rather than treated
        as "no restriction, show everything" - the same "empty means no filter" bug B1
        named at the sibling seam."""
        from app.services.chatbot import turn_runtime
        from tests.chatbot.test_rearch_s3_attribute_first import _seed_products, _seed_promotion

        _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")

        class_label = unique_code("promorefuse").replace("-", "").lower()
        codes = _seed_products(
            session_factory, class_label=class_label, synonyms=[f"{class_label}s"], count=1
        )
        _seed_promotion(session_factory, [codes[0]], access_levels=["sorento_dealer"])
        _seed_class_spec(session_factory, product_code=codes[0], class_label=class_label)

        db = session_factory()
        db.info["company_scope"] = frozenset({DEFAULT_COMPANY_ID})
        # Today's ACTUAL carry shape (see TestSetPageCarryNeverRecordsTheEntitlementItAnsweredUnder):
        # no access_levels key anywhere in it.
        carry = {
            "set_key": {
                "require": {"promotion": True},
                "scope_terms": [class_label],
                "domain": "promotion",
                "set_noun": "products",
            },
            "offset": 0,
        }
        predicate, ids = turn_runtime.page_the_set(db, carry, access_levels=[])
        assert predicate.get("qualifying_total") == 0 and ids == [], (
            "security B2: a carry with NO recorded entitlement must refuse the page "
            f"(no product list, no total) - it must never be read as 'no tier filter "
            f"at all', which is what let the dealer-only promotion's own product "
            f"surface: predicate={predicate!r} ids={ids!r}"
        )
        db.close()


# --------------------------------------------------------------------------- #
# Item 2 - SECURITY S4: the B1 fix still falls back to the parser's claim when the
# resolver's tier gate did not run. Function-level, matching the security report's
# own probe methodology at the exact seam (`turn_runtime.py:1496-1517`) - all THREE
# reachable production shapes named in the re-check (resolve_gate.run raising,
# `turn_runtime.py:776-778`; a plan naming both `ideate` and `promotion`, `turn/
# route.py:46-50`; the set_page early return, `resolve_gate.py:990`) converge on
# the SAME value at this call site: `resolver_tier_gate` is not a dict (None in
# every one of the three), so testing that one input covers all three arms - the
# general fix is at the ONE seam, not three separate reproductions.
# --------------------------------------------------------------------------- #


class TestTierGateFailsClosedWhenNoResolverTierGateRanRegardlessOfTheParsersClaim:
    def test_resolver_tier_gate_none_never_falls_back_to_an_unentitled_parser_claim(
        self,
    ) -> None:
        from app.services.chatbot.turn.plan import FetchSpec
        from app.services.chatbot.turn.state import Focus
        from app.services.chatbot.turn_runtime import _tier_gate

        spec = FetchSpec(domain="promotion", entities=[], filters={"tier": "dealer"}, date_window=None)
        # The contact is entitled to "Sorento End User" ONLY - the parser's own verdict
        # (a message that STATES a tier, per the security report's own repro shape)
        # claims "Sorento Dealer", which this contact does not hold.
        verdict = {"access_levels": ["Sorento Dealer"]}
        focus = Focus()

        result = _tier_gate(spec, verdict, focus, None)

        assert result is not None, "test setup sanity: a tier-filtered spec must return a gate dict"
        assert result.get("access_levels_recomposed") == [], (
            "security S4: on a tier-filtered fetch with NO resolver tier gate "
            "(resolver_tier_gate is not a dict - reachable via resolve_gate.run raising, "
            "an ideate+promotion co-occurring plan, or the set_page early return), the "
            "recomposed access levels must be EMPTY so the runner's own fail-closed "
            "guard (turn_runtime.py:1235-1243) answers the miss - the verdict's CLAIMED "
            f"tier must never become the authority: {result!r}"
        )

    def test_resolver_tier_gate_none_and_no_claim_already_fails_closed_as_a_control(
        self,
    ) -> None:
        """GREEN CONTROL (matches the security report's own probe row B): confirms the
        assertion above is meaningful - an empty claim already recomposes to `[]` today,
        so the sibling test's failure is really about the CLAIM being honoured, not
        about `_tier_gate` being broken in every branch."""
        from app.services.chatbot.turn.plan import FetchSpec
        from app.services.chatbot.turn.state import Focus
        from app.services.chatbot.turn_runtime import _tier_gate

        spec = FetchSpec(domain="promotion", entities=[], filters={"tier": "dealer"}, date_window=None)
        verdict: dict[str, Any] = {"access_levels": []}
        focus = Focus()

        result = _tier_gate(spec, verdict, focus, None)
        assert result is not None
        assert result.get("access_levels_recomposed") == []


# --------------------------------------------------------------------------- #
# Item 3 - REVIEWER MB-1: a fan-out verdict injects `plan.domains[0]` blind, and the
# ambiguous entity vanishes when the FIRST-named domain does not take its kind.
# --------------------------------------------------------------------------- #


class TestFanOutDomainInjectionNeverDropsTheAmbiguousEntity:
    @pytest.mark.parametrize(
        "asks_order,is_control",
        [
            pytest.param([{"domain": "order"}, {"domain": "incoming"}], True, id="order-first"),
            pytest.param([{"domain": "incoming"}, {"domain": "order"}], False, id="incoming-first"),
        ],
    )
    def test_ambiguous_customer_never_vanishes_regardless_of_which_domain_is_named_first(
        self, session_factory, monkeypatch, asks_order: list[dict[str, str]], is_control: bool
    ) -> None:
        """MB-1: `engine.py:1531-1539` stamps `domain_hint = plan.domains[0]` blind.
        `gate.ALLOWED`'s "order" row carries `customer`; "incoming" does not - so
        flipping which domain the message names FIRST decides whether the customer
        picker fires at all. Measured live (reviewer): asks=[incoming, order] answered
        'Here are the results.' TWICE over two empty sections, silently dropping the
        ambiguous customer entirely - worse than the S7 defect the injection was meant
        to fix. `order-first` is the GREEN CONTROL (already correct at head
        `6aeed4188`); `incoming-first` is the RED."""

        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: dict, user_prompt: str) -> Any:
            return {"items": [], "has_result": False}

        _seed_business_contact(session_factory, variables={})
        result, _captured = _run_turn_fake_resolver(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint=None, intent_hint=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                asks=asks_order,
            ),
            text_body="orders and incoming for hanlim",
            msg_id=f"zzt-r8-mb1-{'order' if is_control else 'incoming'}-first",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        reply = (result.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}

        assert reply.count("Here are the results.") <= 1, (
            f"MB-1: an ambiguous customer must never be answered as though BOTH "
            f"domains already produced results - each empty section printing the "
            f"same generic intro twice is a false claim of results: {reply!r}"
        )
        assert open_question, (
            f"MB-1: an ambiguous customer named in a multi-domain ask must raise SOME "
            f"picker regardless of which domain the message named first - the "
            f"blind plan.domains[0] injection must never silently drop it: "
            f"reply={reply!r} open_question={open_question!r}"
        )

    def test_same_rule_with_the_customer_taking_domain_last_in_a_three_domain_plan(
        self, session_factory, monkeypatch
    ) -> None:
        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: dict, user_prompt: str) -> Any:
            return {"items": [], "has_result": False}

        _seed_business_contact(session_factory, variables={})
        result, _captured = _run_turn_fake_resolver(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint=None, intent_hint=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                asks=[{"domain": "incoming"}, {"domain": "spo_allocation"}, {"domain": "order"}],
            ),
            text_body="incoming, spo allocation and orders for hanlim",
            msg_id="zzt-r8-mb1-three-domain-customer-last",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        reply = (result.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        assert reply.count("Here are the results.") <= 1, (
            f"MB-1 (three-domain plan): {reply!r}"
        )
        assert open_question, (
            f"MB-1: the customer-taking domain sitting LAST in a three-domain plan "
            f"must not lose the ambiguous customer either: reply={reply!r} "
            f"open_question={open_question!r}"
        )


# --------------------------------------------------------------------------- #
# Item 4 (MB-3) is applied as an in-place re-parametrization of
# `test_rearch_r7_live_parity_replay.py::
# TestIncomingHyphenSuffixNeverReadsAsAnUnknownProductType`, per the brief -
# see that file's own diff, not duplicated here.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Item 5 (SF-2) - the F8 incoming ask's own sibling-family form, pinned against
# the REAL production text (never hand-typed) via `_expected_miss_text`
# (`test_rearch_r4_bridge_miss.py`'s own reusable chain: `answer.
# not_found_error_message` -> `miss_suggest.run_miss_lane` -> `escalate_catalog`
# -> `build_outcome` -> `compose_reply`) over the SAME fixture
# `_dym_incoming_sibling_scenario` already uses (real siblings, a genuine
# has/no-incoming split).
#
# MEASURED (this session, via the production chain directly, not assumed):
#
#   "No incoming stock (ETA) found for SRTWT7202. Related products:\n"
#   "1. SRTWT7202-BL - has incoming\n2. SRTWT7202-GM - no incoming\n"
#   "Reply with a number to check its incoming, or reply 'yes' to escalate to "
#   "purchasing team."
#
# This is `answer.py::build_suggest_offer`'s OWN D3 sibling-picker template
# (`answer.py:3826-3830`), identical on `origin/main` (confirmed:
# `git show origin/main:.../answer.py` carries the same "has incoming"/"no
# incoming" numbered template at the same D3 comment). It is NOT the "Did you
# mean: ... Reply with a code to continue" wording the brief's own framing
# assumed - that is a DIFFERENT composer (`dym_transform`'s own did-you-mean
# roster, used by the certificate/technical-drawings cases, which never runs
# here because `_sibling_gate` short-circuits `run_miss_lane` before
# `dym_transform` is even called). Reported to the captain in the handoff.
# --------------------------------------------------------------------------- #


class TestIncomingDidYouMeanUsesTheSiblingFormNotTheProductDymForm:
    def test_the_real_sibling_picker_text_is_the_stamped_has_no_incoming_form(self) -> None:
        parser, resolved, extra = _dym_incoming_sibling_scenario()
        gate = extra["gate"]
        services = extra["services"]
        build_result = extra["build_result"]
        payload = {"resolved": resolved, "gate": gate, "_exit_kind": "not_found"}

        text, offer = _expected_miss_text(
            payload, parser=parser, resolved=resolved, gate=gate, services=services,
            build_result=build_result,
        )

        assert "has incoming" in text and "no incoming" in text, (
            f"SF-2: production's own sibling-family picker must stamp every "
            f"neighbour: {text!r}"
        )
        assert "Related products:" in text, text
        assert "Reply with a number to check its incoming" in text, text
        assert "purchasing team" in text, text
        # Ruling anchor: the journey's own expectation names a DIFFERENT composer's
        # wording - this assertion documents the mismatch precisely rather than
        # asserting it must never appear (which would be the wrong direction if a
        # future fix folds both composers' copy together).
        assert offer.get("suggest_offer") is True, offer


# --------------------------------------------------------------------------- #
# Item 6 (SF-1) - the tier-ask arm's own precedence over the crossdomain climb.
# INVESTIGATED, NO RED WRITTEN - see the handoff. `engine.py`'s own
# `bridge_answers_a_miss` predicate (`plan.ask is None`, ...) is independent of
# whether the fetched envelope happens to carry a `tier_ask_fetch` marker, so
# the ONLY way `question_for` returning None on that arm could suppress the
# climb is if `answer_bridge.answer_for`'s OWN later call (gated on `answer is
# None and envelopes and bridge_answers_a_miss`, not on `bridge_answered` or on
# whether `tier_fetch` was ever not-None) somehow still declined - which,
# traced through `engine.py:1925-1985`, it does not: that second call fires
# unconditionally whenever `answer` is still `None`, independent of the first
# block's own outcome. Constructing the shape mechanically (patching
# `turn_runtime.envelope_of` to stamp `tier_ask_fetch` and `answer_bridge.
# question_for` to return `None`) and driving it through `engine.run_turn`
# (the same fixture `TestIncomingMissClimbsToThePORungThroughTheBridge` in
# `test_rearch_r6_review_round.py` already proves reaches the PO rung) measured
# GREEN today - the PO rung tool is probed regardless, because `answer_for`'s
# own gate does not read `tier_fetch`'s outcome at all. Reported as a measured
# non-finding rather than forced into a red that would not fail for the right
# reason; see the handoff for the full trace and the exact code lines read.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Item 7 (reviewer MB-2's open question) - does `answer_bridge.answer_for` hand
# `miss_suggest.run_miss_lane` the SAME gate the fetch used? Spy-based, on the
# ALREADY-GREEN shipped certificate scenario (tester 38 fixed the seed realism
# gap that made it red) - "red or green, report what the spy captured" per the
# brief.
# --------------------------------------------------------------------------- #


class TestBridgeHandsTheMissLaneTheSameGateTheFetchUsed:
    def test_run_miss_lane_gate_carries_the_resolved_attachment_type_uuid(
        self, session_factory, monkeypatch
    ) -> None:
        """Reviewer MB-2's own open question: is the `gate=` argument `answer_bridge.
        answer_for` hands `miss_suggest.run_miss_lane` (`answer_bridge.py:713`,
        `resolver_payload["gate"]`, the PRE-fetch resolver gate) the SAME dict the
        resolver produced for THIS turn, and does it carry the resolved
        attachment_type/certificate uuid `miss_suggest._scoping_from` (`miss_suggest.
        py:319-346`) needs to take the STAMPED form? Spies on `answer_bridge.
        miss_mod.run_miss_lane` (call-through, real function still runs) rather than
        asserting on the reply text alone, so a future WIRING regression (not just the
        rendered word) is caught here. Also cross-checks the captured gate against the
        resolver's OWN payload (`turn_runtime.resolve_kinds`' own spy, matching
        `test_rearch_r5_production_decides.py::_spy_resolve_payload`'s convention) to
        confirm it is literally the SAME object, not a re-derived one."""
        from app.services.chatbot import turn_runtime as turn_runtime_mod

        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTCERTSPY").replace("-", "")
        neighbour_nl, neighbour_qt = f"{base}-NL", f"{base}-QT"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=base)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_nl)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_qt)
        _seed_real_attachment_type(session_factory, "Certification")

        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": base},
                        "attachment": {"attachment_type": "Certification", "original_filename": f"{base}.pdf"},
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        qf = _parser_output(
            domain_hint="product_attachment", intent_hint="check_product_attachment",
            entities=[
                {"raw": f"{base}-FT", "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "CERT", "hint": "attachment_type", "canonical_code": "certificate",
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        )

        resolve_payloads: list[dict[str, Any]] = []
        real_resolve_kinds = turn_runtime_mod.resolve_kinds

        def _spy_resolve_kinds(db, **kwargs):
            outcome = real_resolve_kinds(db, **kwargs)
            if outcome.payload is not None:
                resolve_payloads.append(outcome.payload)
            return outcome

        monkeypatch.setattr(engine_mod.turn_runtime, "resolve_kinds", _spy_resolve_kinds)

        captured_gates: list[dict[str, Any]] = []
        original_run_miss_lane = answer_bridge.miss_mod.run_miss_lane

        def _spy_run_miss_lane(*args, **kwargs):
            captured_gates.append(dict(kwargs.get("gate") or {}))
            return original_run_miss_lane(*args, **kwargs)

        monkeypatch.setattr(answer_bridge.miss_mod, "run_miss_lane", _spy_run_miss_lane)

        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"{base}-FT CERT",
            msg_id="zzt-r8-cert-gate-spy", mcp_response={"data": []}, answer_mcp_probe=answer_probe,
        )
        reply = (result.reply or {}).get("text") or ""

        assert captured_gates, f"run_miss_lane must have been called for this miss: reply={reply!r}"
        assert resolve_payloads, f"the resolver must have run for this turn: reply={reply!r}"
        gate = captured_gates[-1]
        resolver_gate = resolve_payloads[-1].get("gate") or {}

        assert gate == resolver_gate, (
            f"MB-2: answer_bridge.answer_for must hand run_miss_lane the SAME gate the "
            f"resolver produced for this turn, not a re-derived or stale one: "
            f"bridge_gate={gate!r} resolver_gate={resolver_gate!r}"
        )
        entries = gate.get("compatible_entities") or []
        type_entries = [
            e for e in entries
            if isinstance(e, dict)
            and str(e.get("entity_type") or "").lower() in ("attachment_type", "certificate")
        ]
        assert type_entries, (
            f"MB-2: the gate handed to run_miss_lane must carry the RESOLVED "
            f"attachment_type/certificate entity - this is what miss_suggest."
            f"_scoping_from needs to take the STAMPED form rather than the inline "
            f"sentence: gate={gate!r} reply={reply!r}"
        )
        assert any(str(e.get("uuid") or "").strip() for e in type_entries), (
            f"the resolved type entity must carry a real uuid, not a bare label: "
            f"{type_entries!r}"
        )
        assert reply.startswith(f'Couldn\'t find "{base}-FT" (product). Did you mean:'), (
            f"test setup sanity: this scenario is the SAME shipped, now-green "
            f"certificate did-you-mean case (tester 38's realistic-seed fix) - the "
            f"stamped form must still render: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Follow-up round (tester 41, 20 Sep 2026) - pins coder 33's SECOND fix, commit
# `19d5f7789` (`.claude/handoffs/20260920T132023Z-rearch-coder33-recheck-round-
# report.md`, "Follow-up round - live F8 re-check"). The lane-added `require` /
# `scope_terms` block in `resolve_gate.resolve_entity_body` maps a leg off the
# INTENT alone (`derive_require`), so `check_product_attachment` /
# `check_incoming` / `check_stock` carried a described-set leg on EVERY turn,
# a bare code lookup included. With no class word to scope by,
# `resolve_product_set` answered the leg over the WHOLE catalogue and
# `references._emit_spec_matches` emitted that population as a third,
# whole-query resolution of ordinary product matches - measured live for
# "SRTWT165-FT CERT": 200 unnamed products on `gate.compatible_entities`, a
# 131 KB fetch envelope, and a 206-entity did-you-mean probe that came back at
# the tool's 50-row page cap, so `miss_suggest._annotate`'s own page-
# saturation guard refused to attribute the has/no-certificate stamp and the
# reply fell to the bare inline sentence (live turns 790d43c3-91a7-4229-8bd
# and 85e536be). The fix drops the leg when the turn names a product CODE
# (`gate._is_a_described_word`, called not copied) and has no class word
# (`product_type`/`category` entity) to scope it by; a described ask keeps
# its leg untouched, and a turn naming both a code AND a class word keeps its
# scope term too.
# --------------------------------------------------------------------------- #


def _ctx_for_resolve_body(parser_output: dict[str, Any], *, text: str) -> dict[str, Any]:
    return {
        "contact": {"id": "zzt-f41-contact"},
        "text": {"message": {"message": {"text": text}}},
        "parse": {"output": parser_output},
    }


def _code_entity(code: str) -> dict[str, Any]:
    return {
        "raw": code, "hint": "product", "canonical_code": None,
        "current_message": True, "confident": True,
    }


def _class_word_entity(raw: str, *, hint: str = "product_type") -> dict[str, Any]:
    return {
        "raw": raw, "hint": hint, "canonical_code": None,
        "current_message": True, "confident": True,
    }


# One scenario per REQUIRE_LEGS-carrying intent (product_predicate_service.REQUIRE_LEGS):
# the entities a bare code-only turn of that intent emits, and the text it came from.
_CODE_ONLY_SCENARIOS: list[tuple[str, str, list[dict[str, Any]], str]] = [
    (
        "check_product_attachment", "product_attachment",
        [
            _code_entity("SRTWT165-FT"),
            {"raw": "CERT", "hint": "attachment_type", "canonical_code": "certificate",
             "current_message": True, "confident": True},
        ],
        "SRTWT165-FT CERT",
    ),
    (
        "check_incoming", "incoming",
        [_code_entity("SRTWT7202-NEW")],
        "Incoming SRTWT7202-NEW",
    ),
    (
        "check_stock", "master_products",
        [_code_entity("SRTWT165-FT")],
        "Stock SRTWT165-FT",
    ),
]


@pytest.mark.parametrize(
    "intent_hint,domain_hint,entities,text", _CODE_ONLY_SCENARIOS,
    ids=[s[0] for s in _CODE_ONLY_SCENARIOS],
)
class TestACodeOnlySubjectSendsNoDescribedSetLeg:
    def test_a_bare_code_lookup_sends_no_require_or_scope_terms(
        self, intent_hint: str, domain_hint: str, entities: list[dict[str, Any]], text: str
    ) -> None:
        """F8 re-check: a turn whose only subject is a product CODE, with no class
        word to scope a described set by, must not carry `require` /
        `predicate_words` / `scope_terms` at all - the leg would otherwise answer
        `resolve_product_set` over the whole catalogue with an empty
        `scope_terms`, which is exactly the "every product that has X" population
        that produced the live 206-entity did-you-mean probe."""
        parser_output = _parser_output(
            intent_hint=intent_hint, domain_hint=domain_hint, entities=entities,
        )
        ctx = _ctx_for_resolve_body(parser_output, text=text)

        body = resolve_gate.resolve_entity_body(ctx)

        assert "require" not in body, (
            f"F8 re-check: {intent_hint} sent require={body.get('require')!r} for a "
            f"code-only subject with no class word (text={text!r}) - this is the "
            f"described-set-over-the-whole-catalogue leg that must be dropped"
        )
        assert "scope_terms" not in body, body
        assert "predicate_words" not in body, body

    def test_the_same_intent_with_a_class_word_still_sends_the_leg(
        self, intent_hint: str, domain_hint: str, entities: list[dict[str, Any]], text: str
    ) -> None:
        """Control: a turn that ALSO names a `product_type`/`category` class word
        (a genuinely described set - "which taps have a cert") keeps its
        `require` leg and its `scope_terms` untouched - the fix must not silence
        every leg, only the code-with-nothing-to-scope-by shape."""
        parser_output = _parser_output(
            intent_hint=intent_hint, domain_hint=domain_hint,
            entities=[*entities, _class_word_entity("taps")],
        )
        ctx = _ctx_for_resolve_body(parser_output, text=f"{text} taps")

        body = resolve_gate.resolve_entity_body(ctx)

        assert "require" in body, (
            f"control: {intent_hint} with a class word present must still carry "
            f"the described-set leg - none was sent for text={text!r}"
        )
        assert body.get("scope_terms") == ["taps"], body


class TestACertificateDidYouMeanProbesOnlyItsOwnNeighbours:
    def test_the_probe_receives_the_turns_own_neighbours_not_the_catalogue(
        self, session_factory, monkeypatch
    ) -> None:
        """F8 re-check, engine-level: replays the recorded certificate verdict
        (`SRTWT165-FT` unplaced product token + `CERT` resolved attachment_type)
        and asserts the did-you-mean probe's own `entities` argument is a
        HANDFUL - this turn's own product neighbours plus the resolved
        certificate type - never the catalogue-sized population the pre-fix
        `require` leg fed it. Also asserts the reply itself is the numbered
        stamped form carrying both a "has certificate" and a "no certificate"
        line (measured live after the fix, commit `19d5f7789`: "1. SRTWT165-QT
        - has certificate\\n2. SRTWT165 - no certificate\\n3. SRTWT165-NL - no
        certificate").

        25 UNRELATED, actively-certified noise products are seeded alongside
        the 3 real neighbours: with no class word to scope the bare
        `{"certificate": True}` leg by, `resolve_product_set` answers it over
        every certified product the company has on file - this is what makes
        the reverted-fix case genuinely fail here rather than pass by
        accident of an otherwise-empty scratch schema (the pre-fix live
        incident needed a real ~200-row catalogue to surface at all)."""
        from tests.chatbot.test_rearch_s3_attribute_first import _seed_certificates

        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTF41CERT").replace("-", "")
        neighbour_nl, neighbour_qt = f"{base}-NL", f"{base}-QT"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=base)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_nl)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=neighbour_qt)
        _seed_real_attachment_type(session_factory, "Certification")

        noise_codes = [f"{base}NOISE{i:02d}" for i in range(25)]
        for noise_code in noise_codes:
            _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=noise_code)
        _seed_certificates(session_factory, noise_codes, company_id=DEFAULT_COMPANY_ID)

        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": base},
                        "attachment": {"attachment_type": "Certification", "original_filename": f"{base}.pdf"},
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        probe_calls: list[tuple[str, dict[str, Any]]] = []

        def _spying_probe(name: str, args: dict[str, Any]) -> Any:
            probe_calls.append((name, dict(args)))
            return answer_probe(name, args)

        qf = _parser_output(
            domain_hint="product_attachment", intent_hint="check_product_attachment",
            entities=[
                {"raw": f"{base}-FT", "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": "CERT", "hint": "attachment_type", "canonical_code": "certificate",
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        )
        result, _captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"{base}-FT CERT",
            msg_id="zzt-r8-f41-cert-probe-scope", mcp_response={"data": []},
            answer_mcp_probe=_spying_probe,
        )
        reply = (result.reply or {}).get("text") or ""

        assert probe_calls, f"the did-you-mean probe must have run for this miss: reply={reply!r}"
        _tool, args = probe_calls[-1]
        entities = args.get("entities") or []
        assert len(entities) < 20, (
            f"F8 re-check: the probe must carry only this turn's own neighbours, "
            f"never the whole catalogue's unnamed product population it did "
            f"pre-fix (measured live at 206 entities): got {len(entities)} "
            f"entities={entities!r} reply={reply!r}"
        )

        assert reply.startswith(f'Couldn\'t find "{base}-FT" (product). Did you mean:'), (
            f"the stamped numbered form must render, not the bare inline "
            f"sentence a page-saturated probe would fall back to: {reply!r}"
        )
        # ATTACHMENT-MULTI R5 (tester re-run 2 Oct 2026, finding 3): a resolved type stamps
        # its type_name, so the seeded Certification type reads "Certification".
        assert "has Certification" in reply, reply
        assert "no Certification" in reply, reply
