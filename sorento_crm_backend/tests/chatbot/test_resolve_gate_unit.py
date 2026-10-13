"""S6a unit + contract tests: the parts no capture can grade (AC-601, AC-602, AC-603).

The 254-case replay in `test_replay.py` is the real parity gate. This file covers the four
things it cannot:

* **the two output keys the SHIPPING body added after every capture was taken** -
  `specific_options` and `tier_pick_domain` (see `_corpus.CAPTURE_BODY_ADDITIONS`). They
  are stripped before the replay compares, so without these tests they would be untested;
* **the `access_ask` exit**, which has ZERO captures in any slug;
* **H16** (AC-603): only the VALUES of `by_entity_type` are data;
* **H46**: `_isTimeline` CONTAINS the sentinel, it is not equality.

Nothing here touches a database, the network or an LLM.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import contracts
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business import pickers, resolve_gate
from app.services.chatbot.lanes.business.gate import run_gate
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.chatbot.lanes.business.tier_gate import tier_gate
from tests.chatbot.conftest import validating_resolve_entity


def _match(uuid: str, code: str, *, company: str, tier: str = "exact") -> dict[str, Any]:
    return {
        "uuid": uuid,
        "entity_type": "product",
        "canonical_code": code,
        "match_tier": tier,
        "company_name": company,
    }


def _ambiguous_resolver(matches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tokens": ["abc"],
        "resolutions": [{"token": "abc", "resolved": False, "matches": matches}],
        "unresolved_tokens": [],
    }


def _parser(domain: str) -> dict[str, Any]:
    return {
        "domain_hint": domain,
        "entities": [
            {"hint": "product", "raw": "abc", "current_message": True},
            {"hint": "attachment_type", "raw": "certificate", "canonical_code": "certificate"},
        ],
    }


class TestSpecificOptions:
    """RS-9 Fix 5: the picker's render order, exposed so line N maps to candidate N."""

    def test_it_is_always_emitted_even_when_nothing_is_ambiguous(self) -> None:
        out = run_gate({}, parser={"domain_hint": "forms", "entities": []}, resolver={})
        assert out["specific_options"] == [], (
            "the key is emitted on EVERY turn - a reader that has to tell absent from "
            "empty is a reader that will get it wrong"
        )

    def test_it_holds_the_candidates_in_the_order_the_lines_were_numbered(self) -> None:
        resolver = _ambiguous_resolver(
            [_match("u1", "ABC-1", company="Sorento"), _match("u2", "ABC-2", company="Mocha")]
        )
        out = run_gate(dict(resolver), parser=_parser("product_attachment"), resolver=resolver)
        assert out["require_specific"] is True
        labels = [c["label"] for o in out["specific_options"] for c in o["candidates"]]
        numbered = out["gate_clarification"].split("\n")[-len(labels) :]
        assert numbered == [f"{i + 1}. {label}" for i, label in enumerate(labels)]
        assert [c["company"] for o in out["specific_options"] for c in o["candidates"]] == [
            "Sorento",
            "Mocha",
        ]

    def test_f16_suffixes_only_the_duplicated_code_and_only_on_product_attachment(self) -> None:
        """A same-code cross-company twin is two lines identical apart from their number.

        F16 appends the company to ONLY those lines, and ONLY on `product_attachment`:
        `incoming`'s annotator re-joins the rendered lines to its probe BY EXACT CODE TEXT,
        so a suffixed line would miss that set and render a confident FALSE "- no incoming".
        """
        matches = [
            _match("u1", "ABC-1", company="Sorento"),
            _match("u2", "ABC-1", company="Mocha"),
            _match("u3", "ABC-2", company="Sorento"),
        ]
        attachment = run_gate(
            dict(_ambiguous_resolver(matches)),
            parser=_parser("product_attachment"),
            resolver=_ambiguous_resolver(matches),
        )
        labels = [c["label"] for o in attachment["specific_options"] for c in o["candidates"]]
        assert labels == ["ABC-1 (Sorento)", "ABC-1 (Mocha)", "ABC-2"]

        incoming_parser = {
            "domain_hint": "incoming",
            "entities": [{"hint": "product", "raw": "abc", "current_message": True}],
        }
        incoming = run_gate(
            dict(_ambiguous_resolver(matches)),
            parser=incoming_parser,
            resolver=_ambiguous_resolver(matches),
        )
        incoming_labels = [c["label"] for o in incoming["specific_options"] for c in o["candidates"]]
        assert incoming_labels == ["ABC-1", "ABC-1", "ABC-2"], (
            "the incoming picker's lines must stay byte-equal to the codes its probe "
            "matches on"
        )


class TestTierPickStamps:
    """RS-9 Fix 6 / Fix 8: the tier-menu re-pick, and the domain it forwards."""

    ENTITLED = {"name": ["Sorento Dealer", "Sorento Office", "End User"]}

    def test_a_valid_pick_overrides_whatever_the_parser_carried(self) -> None:
        out = tier_gate(
            dict(self.ENTITLED),
            parser={"domain_hint": "promotion", "access_levels": ["dealer"]},
            item={"tier_pick": "office", "tier_pick_domain": "promotion"},
        )
        assert out["tier_stated"] == ["office"]
        assert out["tier_ask"] is False
        assert out["tier_pick_domain"] == "promotion"

    def test_an_out_of_range_pick_forces_the_reprompt_on_any_domain(self) -> None:
        out = tier_gate(
            dict(self.ENTITLED),
            parser={"domain_hint": None, "access_levels": ["dealer"], "_pending_pick": True},
            item={"tier_pick_invalid": True},
        )
        assert out["tier_stated"] == []
        assert out["tier_ask"] is True, (
            "an out-of-range TIER pick must reach the reprompt even on a bare digit that "
            "states no domain and even when the parser resolved a PROMO-ROW pick"
        )

    def test_tier_pick_domain_is_null_when_nothing_stamped_it(self) -> None:
        out = tier_gate(dict(self.ENTITLED), parser={"domain_hint": "promotion"}, item=None)
        assert out["tier_pick_domain"] is None


class TestAccessAskExit:
    """The one exit arm with ZERO captures, in any slug."""

    def _services(self, names: list[str]) -> ResolveGateServices:
        def _resolve(_body: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("resolve-entity must not run when If4 takes its FALSE leg")

        return ResolveGateServices(
            access_types=lambda **_: [{"name": n} for n in names],
            resolve_entity=validating_resolve_entity(_resolve),
            probe=lambda **_: None,
        )

    def _ctx(self) -> dict[str, Any]:
        return {
            "contact": {"id": "c1"},
            "parse": {"output": {"domain_hint": "promotion", "entities": []}},
            "session": {},
        }

    def test_zero_entitlement_exits_access_ask_carrying_tier_gate(self) -> None:
        out = resolve_gate.run(
            self._ctx(), "access_check", {}, services=self._services([])
        )
        assert out["_exit_kind"] == "access_ask"
        assert out["tier_proceed"] is False
        assert out["aggregate"] == {"name": []}
        assert out["tier_gate"]["entitled_tiers"] == []
        # The three the gate never reached stay NULL, which is what n8n's presence gates read.
        assert out["resolved"] is None
        assert out["gate"] is None
        assert out["ctx_resolved"] is None
        assert out["annotate_incoming"] is None

    def test_an_entitled_contact_goes_on_to_resolve_entity(self) -> None:
        services = ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(
                lambda _body: {"tokens": [], "resolutions": [], "unresolved_tokens": []}
            ),
            probe=lambda **_: None,
        )
        out = resolve_gate.run(self._ctx(), "access_check", {}, services=services)
        assert out["_exit_kind"] != "access_ask"
        assert out["aggregate"] == {"name": ["Sorento Dealer"]}
        assert out["resolved"] is not None


class TestByEntityTypeKeysAreNeverData:
    """AC-603 / H16: only the VALUES of `by_entity_type` are rows."""

    def test_a_metadata_key_never_reaches_a_customer_facing_string(self) -> None:
        resolver = {
            "match_mode": "and",
            "tokens": ["abc"],
            "intersection": None,
            "by_entity_type": {
                "product": [_match("u1", "ABC-1", company="Sorento")],
                # A key that is METADATA, not an entity type. n8n renders `by_entity_type`
                # to customers elsewhere, and a reader that iterated KEYS would print it.
                "_result_total": 7,
            },
            "unresolved_tokens": [],
        }
        out = run_gate(dict(resolver), parser=_parser("incoming"), resolver=resolver)
        rendered = "\n".join(
            [
                str(out.get("gate_clarification") or ""),
                str(out.get("gate_reason") or ""),
                str(out.get("access_notice") or ""),
            ]
        )
        assert "_result_total" not in rendered
        assert all(e.get("entity_type") != "_result_total" for e in out["compatible_entities"])
        assert all(
            "_result_total" not in str(c.get("label"))
            for o in out["specific_options"]
            for c in o["candidates"]
        )

    def test_the_non_array_value_is_flattened_the_way_javascript_does(self) -> None:
        """`Object.values(x).flat()` passes a non-array value THROUGH, it does not drop it.

        Reproduced rather than tidied: dropping it would be a silent narrowing, and the
        row it lets through is discarded one line later for having no `uuid` anyway.
        """
        from app.services.chatbot.lanes.business.gate import _flatten_by_entity_type

        assert _flatten_by_entity_type({"product": [1, 2], "_meta": 7}) == [1, 2, 7]
        assert _flatten_by_entity_type(None) == []


class TestTimelineSentinel:
    """H46: `_isTimeline` is `.some(k === '__all__')` - CONTAINS, not equals."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (["__all__"], True),
            # The case an earlier mutation test could not falsify, and the reason the
            # guard reading it is load-bearing rather than belt-and-braces.
            (["__all__", "gatepass_date"], True),
            (["gatepass_date", "__all__"], True),
            ([" __all__ "], True),
            (["gatepass_date"], False),
            ([], False),
            ([None], False),
            (None, False),
            ("__all__", False),
        ],
    )
    def test_contains_the_sentinel(self, value: Any, expected: bool) -> None:
        assert contracts.is_timeline(value) is expected


class TestPickerProbeArms:
    """A probe that did not run must never read as a probe that found nothing."""

    GATE = {"gate_clarification": "Which customer do you mean? Please choose:\n1. ACME SDN BHD"}

    # PICKER-ONE (owner, 13 Oct 2026): the customer arm records what its probe measured
    # as DATA (`customer_bases_with_do`); the line stamps come from `pickers.
    # customer_stamp` over it and the roster prints through `compose_question`.

    def test_an_unavailable_probe_is_not_measured(self) -> None:
        out = resolve_gate.measure_customer_picker(dict(self.GATE), probe=None, parser={})
        assert out["customer_bases_with_do"] is None
        assert out["customer_probe_hits"] is None
        assert out["customer_probe_skip_reason"] == "probe_unavailable"
        assert pickers.customer_stamp("ACME SDN BHD", None) is None

    def test_a_saturated_page_is_not_measured(self) -> None:
        rows = [{"customer_name": "ACME SDN BHD"}] * pickers.PAGE_SATURATION
        out = resolve_gate.measure_customer_picker(dict(self.GATE), probe={"answers": rows}, parser={})
        assert out["customer_bases_with_do"] is None
        assert out["customer_probe_skip_reason"] == "page_saturated"

    def test_a_defaulted_window_is_reported(self) -> None:
        """Which window was measured stays on `customer_probe_window_days`; the line
        itself is never hedged with it (owner ruling A, console pass 3, 6 Sep 2026)."""
        out = resolve_gate.measure_customer_picker(dict(self.GATE), probe={"answers": []}, parser={})
        assert out["customer_probe_window_days"] == 90
        assert out["customer_bases_with_do"] == []

    def test_a_customer_dated_window_reports_no_default(self) -> None:
        out = resolve_gate.measure_customer_picker(
            dict(self.GATE), probe={"answers": []}, parser={"date_filter_start": "2026-01-01"}
        )
        assert out["customer_probe_window_days"] is None

    def test_an_order_with_no_delivery_order_is_not_counted_as_one(self) -> None:
        """The substantive half of the ruling: `crm_order_management_orders_list` returns
        every matching ORDER, and the old set counted all of them - so a customer whose
        orders had not shipped read "- has delivery"."""
        rows = [
            {
                "title": "ZZT-1",
                "fields": [
                    {"label": "Customer", "value": "ACME SDN BHD"},
                    {"label": "Actual Delivery Date", "value": None},
                ],
            }
        ]
        out = resolve_gate.measure_customer_picker(dict(self.GATE), probe={"answers": rows}, parser={})
        bases = set(out["customer_bases_with_do"])
        assert pickers.customer_stamp("ACME SDN BHD", bases) == "no DO"
        assert out["customer_probe_hits"] == 0

        rows[0]["fields"][1]["value"] = "2026-09-01"
        out = resolve_gate.measure_customer_picker(dict(self.GATE), probe={"answers": rows}, parser={})
        bases = set(out["customer_bases_with_do"])
        assert pickers.customer_stamp("ACME SDN BHD", bases) == "has DO"
        assert out["customer_probe_hits"] == 1

    def test_the_incoming_picker_keeps_the_numbering_and_the_order(self) -> None:
        gate = {"gate_clarification": "Choose:\n1. AAA-1\n2. BBB-2"}
        out = pickers.annotate_incoming(gate, probe={"answers": [{"title": "aaa-1"}]})
        assert out["escalate_message"] == "Choose:\n1. AAA-1 - has incoming\n2. BBB-2 - no incoming"
        assert out["is_clarification"] is False


class TestDelegateSeam:
    """The three arms that reach the sub, and the one Set node on one of their edges."""

    def test_only_the_three_business_arms_enter_the_lane(self) -> None:
        assert set(business.ENTRY_BY_BRANCH_KIND) == {
            "check_promotion",
            "stock_denied",
            "business_query",
        }
        assert business.handles("business_query") is True
        assert business.handles("out_of_scope") is False
        assert business.handles(None) is False

    def test_check_promotion_enters_at_access_check_and_the_others_at_resolve(self) -> None:
        assert business.ENTRY_BY_BRANCH_KIND["check_promotion"] == "access_check"
        assert business.ENTRY_BY_BRANCH_KIND["stock_denied"] == "resolve"
        assert business.ENTRY_BY_BRANCH_KIND["business_query"] == "resolve"

    def test_the_routers_item_is_forwarded_unchanged_on_every_arm(self) -> None:
        """The caller's item is never mutated, and the `Edit Fields2` stamp rides on the
        PAYLOAD instead.

        `Edit Fields2` sits on the one edge `If7`'s TRUE output takes, so the
        `stock_denied` arm - and only that arm - carries `not_allowed_check_stock`. At
        S6c the CRM is the `validator` that reads it (off the payload, since there is no
        `$('Edit Fields2')` to read by name), which is why the stamp is back.
        """
        seen_bodies: list[dict[str, Any]] = []

        def _resolve(body: dict[str, Any]) -> dict[str, Any]:
            seen_bodies.append(body)
            return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=validating_resolve_entity(_resolve), probe=lambda **_: None
        )
        ctx = {"contact": {"id": "c1"}, "parse": {"output": {"entities": []}}, "session": {}}
        for branch_kind in ("stock_denied", "business_query"):
            original = {"branch_kind": branch_kind, "allowed": True}
            fragment = business.run_until_exit(
                ctx, original, branch_kind=branch_kind, services=services
            )
            assert fragment["delegate"] == "business_query"
            assert fragment["payload"]["_exit_kind"] in contracts.EXIT_KINDS
            assert original == {"branch_kind": branch_kind, "allowed": True}, (
                "the router's item must reach the lane unmutated"
            )
            assert fragment["payload"].get("not_allowed_check_stock") is (
                True if branch_kind == "stock_denied" else None
            ), "only the arm that passes Edit Fields2 carries its one field"
        assert len(seen_bodies) == 2

    def test_a_dry_run_turn_asks_the_resolver_not_to_write_its_usage_row(self) -> None:
        """D14, at the seam the breach was measured on. The key is OMITTED when live, so a
        live body stays byte-equal to the one n8n sends."""
        bodies: list[dict[str, Any]] = []

        def _resolve(body: dict[str, Any]) -> dict[str, Any]:
            bodies.append(body)
            return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

        services = ResolveGateServices(
            access_types=lambda **_: [], resolve_entity=validating_resolve_entity(_resolve), probe=lambda **_: None
        )
        ctx = {"contact": {"id": "c1"}, "parse": {"output": {"entities": []}}, "session": {}}
        business.run_until_exit(ctx, {}, branch_kind="business_query", services=services)
        business.run_until_exit(
            ctx, {}, branch_kind="business_query", services=services, dry_run=True
        )
        assert "dry_run" not in bodies[0]
        assert bodies[1]["dry_run"] is True

    def test_every_exit_carries_the_six_contract_fields_and_a_declared_kind(self) -> None:
        """`sub-main-processing`'s presence gates read these SIX keys by name.

        A missing key is not a missing field: it is a stand-in node that silently stops
        executing, and every by-name reader downstream of it throwing. So the arm must emit
        all six on every exit, `None` included, and `_exit_kind` must be one of the four
        `resolve-arm` tests for.
        """
        assert contracts.EXIT_KINDS == ("continue", "access_ask", "not_found", "offer")
        for kind in contracts.EXIT_KINDS:
            item = resolve_gate.exit_item({"carried": 1}, exit_kind=kind, fields={})
            assert item["_exit_kind"] == kind
            assert item["carried"] == 1
            for field in contracts.EXIT_CONTRACT_FIELDS:
                assert field in item and item[field] is None


# --------------------------------------------------------------------------- #
# Owner ruling K, rule 4's OTHER half (2026-09-06): where the inheritance is allowed
# to be blocked.
#
# `output_exchange` no longer blocks a bare entity's domain inheritance on the model's
# own hint - it types the entity BY the carried domain and lets the resolver decide. That
# is only safe because the resolver's gate already refuses a token that comes back as an
# incompatible type, so this class pins the guard the parser-side rule now leans on. It
# asserts EXISTING behaviour on purpose: the rule moved the decision from parse time to
# resolve time, and a test that only covered the move would not notice if the thing it
# moved to stopped working.
# --------------------------------------------------------------------------- #


class TestBareEntityInheritanceIsBlockedAtResolveTime:
    INVENTORY_PARSER = {
        "domain_hint": "inventory",
        # Typed `product` by the carried inventory domain, whatever the model hinted.
        "entities": [{"hint": "product", "raw": "rpacc", "current_message": True}],
    }

    def _resolver(self, entity_type: str) -> dict[str, Any]:
        return {
            "tokens": ["rpacc"],
            "resolutions": [
                {
                    "token": "rpacc",
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": "u-rpacc",
                            "entity_type": entity_type,
                            "canonical_code": "RPACC",
                            "match_tier": "exact",
                            "company_name": "Sorento",
                        }
                    ],
                }
            ],
            "unresolved_tokens": [],
        }

    def test_a_token_that_resolves_only_as_a_customer_fails_the_inventory_gate(self) -> None:
        """The bare code was typed `product` by the carried domain and came back a
        CUSTOMER. That is the one signal worth blocking on, and the gate already does it -
        the turn takes today's clarify path instead of answering stock for a customer."""
        resolver = self._resolver("customer")
        out = run_gate(dict(resolver), parser=self.INVENTORY_PARSER, resolver=resolver)
        assert out["gate_passed"] is False
        assert out["gate_reason"] == "types [customer] incompatible with 'inventory'", (
            f"the resolve-time guard is what makes the parse-time one unnecessary: {out!r}"
        )

    def test_a_token_that_resolves_as_a_product_passes(self) -> None:
        """The other side of the same guard: the retype was right, so nothing blocks."""
        resolver = self._resolver("product")
        out = run_gate(dict(resolver), parser=self.INVENTORY_PARSER, resolver=resolver)
        assert out["gate_passed"] is True
        assert out["gate_reason"] == "ok"


# --------------------------------------------------------------------------- #
# D10 (owner console pass, 8 Sep 2026, turn 69d9900e "srtwc8610-sh hav incoming?"). The
# resolver's own answer for that token was a `product_set` (SRTWC8610-SH exists as a set
# code, not a product code) and the gate refused it flat: "types [product_set]
# incompatible with 'incoming'" - a genuine incompatibility, but one the customer cannot
# tell apart from a code that matched nothing, and live n8n answers the latter with a
# did-you-mean over the domain's own allowed types.
# --------------------------------------------------------------------------- #


class TestIncompatibleOnlyTokenIsStampedForTheMissLane:
    INCOMING_PARSER = {
        "domain_hint": "incoming",
        "entities": [{"hint": "product", "raw": "srtwc8610-sh", "current_message": True}],
    }

    def _resolver(self, *, matches: list, alternatives: list | None = None) -> dict[str, Any]:
        return {
            "tokens": ["srtwc8610-sh"],
            "resolutions": [
                {
                    "token": "srtwc8610-sh",
                    "resolved": True,
                    "matches": matches,
                    "alternatives": alternatives or [],
                }
            ],
            "unresolved_tokens": [],
        }

    def test_a_token_matching_only_an_incompatible_type_is_stamped(self) -> None:
        resolver = self._resolver(
            matches=[
                {
                    "uuid": "u-set",
                    "entity_type": "product_set",
                    "canonical_code": "SRTWC8610-SH",
                    "match_tier": "exact",
                    "company_name": "Sorento",
                }
            ]
        )
        out = run_gate(dict(resolver), parser=self.INCOMING_PARSER, resolver=resolver)
        assert out["gate_passed"] is False
        assert out["gate_reason"] == "types [product_set] incompatible with 'incoming'"
        assert out["gate_debug"]["incompatible_only"] == {"srtwc8610-sh": ["product_set"]}

    def test_a_token_with_an_allowed_type_match_too_is_not_stamped(self) -> None:
        """The compat refusal stays a plain pass when SOME match is allowed - only a
        token whose EVERY match is incompatible gets the miss-lane treatment."""
        resolver = self._resolver(
            matches=[
                {
                    "uuid": "u-prod",
                    "entity_type": "product",
                    "canonical_code": "SRTWC8610",
                    "match_tier": "exact",
                    "company_name": "Sorento",
                },
                {
                    "uuid": "u-set",
                    "entity_type": "product_set",
                    "canonical_code": "SRTWC8610-SH",
                    "match_tier": "exact",
                    "company_name": "Sorento",
                },
            ]
        )
        out = run_gate(dict(resolver), parser=self.INCOMING_PARSER, resolver=resolver)
        assert out["gate_passed"] is True
        assert "incompatible_only" not in out["gate_debug"]

    def test_the_miss_lane_offers_the_incompatible_only_token_over_the_allowed_types(
        self,
    ) -> None:
        """`miss_resolutions` forces the stamped token past its own `resolved` guard, and
        `_token_candidates`' existing type filter is what keeps the offer to `product`."""
        from app.services.chatbot.lanes.business.miss_suggest import (
            _token_candidates,
            miss_resolutions,
        )

        resolver = self._resolver(
            matches=[
                {
                    "uuid": "u-set",
                    "entity_type": "product_set",
                    "canonical_code": "SRTWC8610-SH",
                    "match_tier": "exact",
                    "company_name": "Sorento",
                }
            ],
            alternatives=[
                {
                    "uuid": "u-8611",
                    "entity_type": "product",
                    "canonical_code": "SRTWC8611",
                    "match_tier": "trgm",
                    "company_name": "Sorento",
                },
                {
                    "uuid": "u-8613",
                    "entity_type": "product",
                    "canonical_code": "SRTWC8613",
                    "match_tier": "trgm",
                    "company_name": "Sorento",
                },
            ],
        )
        gate = run_gate(dict(resolver), parser=self.INCOMING_PARSER, resolver=resolver)
        assert gate["gate_passed"] is False

        misses = miss_resolutions(resolver, gate=gate)
        assert [m["token"] for m in misses] == ["srtwc8610-sh"]

        cands = _token_candidates(misses[0], allowed_types=["product", "inbound_shipment"], uuid_keyed=False)
        assert [c["canonical_code"] for c in cands] == ["SRTWC8611", "SRTWC8613"]


# --------------------------------------------------------------------------- #
# D10c (owner console pass, 8 Sep 2026, turn 72146a1e "srtwc8610-sh certificate"). AND
# mode with two tokens: the product token resolves ONLY to `product_set`, the
# attachment_type token resolves fine (Certification) - so the blanket "types [...]
# incompatible" branch never fires (`compatible_entities` is non-empty, it just lacks
# `product`) and the turn used to fetch certificates scoped to NOTHING about the product,
# answering "no certificate matched these" for a subject that was silently dropped.
# --------------------------------------------------------------------------- #
class TestB1CatchesAnIncompatibleOnlyProductBesideAResolvedAttachmentType:
    PA_PARSER = {
        "domain_hint": "product_attachment",
        "entities": [
            {"hint": "product", "raw": "srtwc8610-sh", "current_message": True},
            {"hint": "attachment_type", "raw": "certificate", "current_message": True},
        ],
    }

    def _resolver(self) -> dict[str, Any]:
        return {
            "tokens": ["srtwc8610-sh", "certificate"],
            "resolutions": [
                {
                    "token": "srtwc8610-sh",
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": "u-set",
                            "entity_type": "product_set",
                            "canonical_code": "SRTWC8610-SH",
                            "match_tier": "exact",
                            "company_name": "Sorento",
                        }
                    ],
                    "alternatives": [
                        {
                            "uuid": "u-8611",
                            "entity_type": "product",
                            "canonical_code": "SRTWC8611",
                            "match_tier": "trgm",
                            "company_name": "Sorento",
                        }
                    ],
                },
                {
                    "token": "certificate",
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": "u-cert-type",
                            "entity_type": "attachment_type",
                            "canonical_code": "Certification",
                            "match_tier": "substring",
                            "company_name": None,
                        }
                    ],
                    "alternatives": [],
                },
            ],
            "unresolved_tokens": [],
        }

    def test_the_gate_refuses_rather_than_scoping_on_the_certificate_alone(self) -> None:
        resolver = self._resolver()
        out = run_gate(dict(resolver), parser=self.PA_PARSER, resolver=resolver)
        assert out["gate_passed"] is False
        assert out["gate_reason"] == (
            "'product_attachment' subject product did not resolve; refusing to scope "
            "on carried entities"
        )
        assert out["gate_debug"]["incompatible_only"] == {"srtwc8610-sh": ["product_set"]}

    def test_a_real_product_beside_the_attachment_type_still_passes(self) -> None:
        """The other half: an actual `product` match means B1 has nothing to catch."""
        resolver = self._resolver()
        resolver["resolutions"][0]["matches"] = [
            {
                "uuid": "u-prod",
                "entity_type": "product",
                "canonical_code": "SRTWC8610",
                "match_tier": "exact",
                "company_name": "Sorento",
            }
        ]
        out = run_gate(dict(resolver), parser=self.PA_PARSER, resolver=resolver)
        assert out["gate_passed"] is True
        assert "incompatible_only" not in out["gate_debug"]


# --------------------------------------------------------------------------- #
# Owner console pass 4, item F (live turn ace4cec6). The customer typed "srtwc287" with a
# stock question already in play (previous domain `inventory`, a warehouse escalation
# offer pending) and the reply led with "No incoming stock (ETA) found for SRTWC287" -
# while the resolver's own answer for that token was two PRODUCTS, SRTWC287 and
# SRTWC287-LID, and no shipment at all.
#
# The parser hinted the token `inbound_shipment` and emitted `intent_hint: check_incoming`
# / `domain_hint: incoming`, so `domain_signal_source` was `intent_explicit` and AC-816
# rule 4's bare-entity inheritance was bypassed by a domain the model had invented from a
# mis-shaped token. Nothing at parse time can tell a container number from a product code
# (that is a PROMPT question, noted in the PR body), but the RESOLVER can and already did:
# zero shipments, two products.
#
# So the correction is deterministic and post-resolve, off resolver output only (D11).
# It belongs in `resolve_gate.run`, between `services.resolve_entity` and `run_gate`, and
# NOT in `output_exchange`: the post-processor is the parser's own step and runs before
# anything has been resolved, so the evidence this rule needs does not exist there yet.
# --------------------------------------------------------------------------- #


class TestAShipmentHintedTokenThatIsOnlyAProductIsRetyped:
    CARRIED_DOMAIN = "inventory"

    def _parser(self, **overrides) -> dict[str, Any]:
        parser = {
            "message_type": "business_query",
            "intent_hint": "check_incoming",
            "domain_hint": "incoming",
            "domain_signal_source": "intent_explicit",
            "entities": [
                {
                    "raw": "srtwc287",
                    "hint": "inbound_shipment",
                    "canonical_code": None,
                    "current_message": True,
                }
            ],
        }
        parser.update(overrides)
        return parser

    def _resolver(self, *entity_types: str) -> dict[str, Any]:
        return {
            "tokens": ["srtwc287"],
            "resolutions": [
                {
                    "token": "srtwc287",
                    "resolved": len(entity_types) == 1,
                    "ambiguous": len(entity_types) > 1,
                    "matches": [
                        {
                            "uuid": f"u-{i}",
                            "entity_type": t,
                            "canonical_code": "SRTWC287" if i == 0 else "SRTWC287-LID",
                            "match_tier": "exact",
                        }
                        for i, t in enumerate(entity_types)
                    ],
                }
            ],
            "unresolved_tokens": [],
        }

    def test_zero_shipments_and_some_products_retypes_the_entity_and_drops_the_domain(
        self,
    ) -> None:
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser,
            self._resolver("product", "product"),
            carried_domain=self.CARRIED_DOMAIN,
            message="srtwc287",
        )
        assert out is True, "the correction did not fire on its own evidence"
        assert parser["entities"][0]["hint"] == "product", parser["entities"]
        assert parser["domain_hint"] == "inventory", (
            "the invented `incoming` domain had no support but that one entity, so the "
            f"carried business domain applies: {parser['domain_hint']!r}"
        )
        assert parser["intent_hint"] != "check_incoming", parser["intent_hint"]
        assert parser["shipment_hint_retyped"] == ["srtwc287"], parser

    def test_the_retyped_token_then_passes_the_inventory_gate(self) -> None:
        """End of the same move: the corrected parser is what `run_gate` sees, and the
        token that would have been refused as an incompatible type now answers stock."""
        parser = self._parser()
        resolver = self._resolver("product")
        resolve_gate.retype_shipment_miss(
            parser, resolver, carried_domain=self.CARRIED_DOMAIN, message="srtwc287"
        )
        out = run_gate(dict(resolver), parser=parser, resolver=resolver)
        assert out["gate_passed"] is True, out.get("gate_reason")

    def test_a_real_shipment_hit_is_left_alone(self) -> None:
        """Guard: a container number that IS a shipment keeps its type and its domain,
        even when a product happens to match the same token too."""
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser,
            self._resolver("inbound_shipment", "product"),
            carried_domain=self.CARRIED_DOMAIN,
            message="srtwc287",
        )
        assert out is False
        assert parser["entities"][0]["hint"] == "inbound_shipment"
        assert parser["domain_hint"] == "incoming"

    def test_a_token_that_resolved_to_nothing_is_left_alone(self) -> None:
        """Guard: a genuine miss is a miss. Retyping on no evidence would answer the
        wrong question instead of saying the code was not found."""
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser, self._resolver(), carried_domain=self.CARRIED_DOMAIN, message="srtwc287"
        )
        assert out is False
        assert parser["domain_hint"] == "incoming"

    def test_a_second_entity_keeps_the_incoming_domain(self) -> None:
        """Guard: the domain is only dropped when its ONLY support was the mis-hinted
        entity. "eta for srtwc287 and container TEMU6355180" still means incoming."""
        parser = self._parser(
            entities=[
                {"raw": "srtwc287", "hint": "inbound_shipment", "current_message": True},
                {"raw": "TEMU6355180", "hint": "inbound_shipment", "current_message": True},
            ]
        )
        out = resolve_gate.retype_shipment_miss(
            parser,
            self._resolver("product"),
            carried_domain=self.CARRIED_DOMAIN,
            message="srtwc287 and container TEMU6355180",
        )
        assert out is True, "the ENTITY is still retyped on its own evidence"
        assert parser["entities"][0]["hint"] == "product"
        assert parser["domain_hint"] == "incoming", (
            "a second entity is separate support for the domain the customer asked in: "
            f"{parser['domain_hint']!r}"
        )

    def test_a_customer_named_incoming_domain_is_kept(self) -> None:
        """Guard: `domain_signal_source` is what says whose domain it is. Anything but
        `intent_explicit` means the domain did not come from this turn's own intent, so
        there is nothing here to correct."""
        parser = self._parser(domain_signal_source="intent_none")
        out = resolve_gate.retype_shipment_miss(
            parser, self._resolver("product"), carried_domain=self.CARRIED_DOMAIN, message="srtwc287"
        )
        assert out is True
        assert parser["entities"][0]["hint"] == "product"
        assert parser["domain_hint"] == "incoming"

    def test_with_no_carried_domain_the_entity_is_retyped_and_the_domain_stands(self) -> None:
        """Guard: a cold turn has nothing to fall back to, and inventing one would be a
        guess. The type correction still lands - it rests on resolver evidence alone."""
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser, self._resolver("product"), carried_domain=None, message="srtwc287"
        )
        assert out is True
        assert parser["entities"][0]["hint"] == "product"
        assert parser["domain_hint"] == "incoming"

    def test_a_customer_who_asked_about_eta_keeps_the_incoming_domain(self) -> None:
        """The capture that proves `domain_signal_source` cannot carry this on its own
        (`sub-resolve-and-gate-rs/rg-15123789`, "M90ss any eta"): the parser stamps
        `intent_explicit` for ANY decisive intent plus a domain, said aloud or invented, so
        four of the five graded incoming captures look identical to the owner's turn in
        structured state and differ only in that the customer typed the word. A product
        HAS incoming stock - `crm_incoming_stock_list` is keyed by product code and the
        incoming picker lists product codes - so retyping the entity is right in both, and
        only the DOMAIN drop needs the customer's own word. The vocabulary is
        `output_exchange.DOMAIN_SWITCH_WORDS`, already inventoried and already the table
        that decides a this-turn domain switch; no second list."""
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser,
            self._resolver("product", "product"),
            carried_domain="order",
            message="M90ss any eta",
        )
        assert out is True, "the entity is a product either way"
        assert parser["entities"][0]["hint"] == "product"
        assert parser["domain_hint"] == "incoming", (
            "the customer said ETA, so the incoming domain is theirs and not the model's "
            f"guess: {parser['domain_hint']!r}"
        )

    def test_the_owners_bare_token_turn_drops_it(self) -> None:
        """The turn the rule exists for (ace4cec6), stated beside its own counter-example:
        the same structured state, no incoming word anywhere in the message."""
        parser = self._parser()
        out = resolve_gate.retype_shipment_miss(
            parser,
            self._resolver("product", "product"),
            carried_domain="inventory",
            message="srtwc287",
        )
        assert out is True
        assert parser["domain_hint"] == "inventory", parser["domain_hint"]


RETYPED_SUB_FIXTURES = (
    "rg-15123789",
    "rg-15128371",
    "rg-15192977",
    "rs8-t2-picker",
    "rs8a-t2-picker-T1",
)


class TestTheRetypedEntityArrayDiffersOnlyInTheHint:
    """Review of #706, S3. The item F divergence strips `ctx_resolved.ctx.parse.output.
    entities` whole on five captures, because `divergences.strip` addresses a path, not one
    field of one array element. This is what makes that strip honest: on each capture the
    array is compared element by element and the ONLY difference is the retyped hint."""

    @pytest.mark.parametrize("stem", RETYPED_SUB_FIXTURES, ids=lambda v: v)
    def test_only_the_hint_moved(self, stem: str) -> None:
        from tests.chatbot import _corpus
        from tests.chatbot.test_replay import _compare

        fixtures = [
            f for f in _corpus.sub_run_fixtures(vendored_only=False) if f.name.endswith(stem)
        ]
        if not fixtures:
            pytest.skip(_corpus.corpus_skip_reason())
        actual, expected = _compare(fixtures[0])

        def entities_of(items: Any) -> list:
            ctx = ((items[0].get("json") or {}).get("ctx_resolved") or {}).get("ctx") or {}
            return ((ctx.get("parse") or {}).get("output") or {}).get("entities") or []

        got, want = entities_of(actual), entities_of(expected)
        assert len(got) == len(want) and got, (got, want)
        moved = 0
        for g, w in zip(got, want):
            for key in sorted(set(g) | set(w)):
                if key == "hint" and g.get(key) != w.get(key):
                    assert (w.get("hint"), g.get("hint")) == ("inbound_shipment", "product"), (g, w)
                    moved += 1
                    continue
                assert g.get(key) == w.get(key), f"{key!r} differs on {stem}: {g!r} vs {w!r}"
        assert moved == 1, f"exactly one hint moves on {stem}, got {moved}"
