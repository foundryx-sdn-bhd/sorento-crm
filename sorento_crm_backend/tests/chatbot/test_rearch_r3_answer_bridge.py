"""R3 RED tests - `app/services/chatbot/answer_bridge.py`'s `question_for` seam
(PLAN-chatbot-answer-half-reattach.md slice R3; UAC AC-1683, AC-1684, AC-1691's own
"no roster under two options" umbrella, AC-1694 pytest half, AC-1697/AC-1698's shape).

**Captain rulings, 20 Sep 2026, adjusting this file from its first red pass:**

1. **Bridge home.** The purity guard wins, no carve-out: the bridge lives OUTSIDE
   `turn/`, as `app/services/chatbot/answer_bridge.py` (sibling of `turn_runtime.py`,
   already the impure adaptor between the pure core and the old seams). Import path
   `app.services.chatbot.answer_bridge`. It may import `tail.outcome`,
   `tail.reply_ladder`, `lanes.business.answer`, `session_state`, `turn.pending`,
   `turn.compose.Answer`. `TestTurnPackagePurity` below adds the missing direction
   (no file under `turn/` imports `answer_bridge` or `tail`) - the `tail` half is
   already covered by `test_rearch_s2_apply_is_pure.py`'s own parametrize, so this
   file's own guard is scoped to `answer_bridge` (not a duplicate of that test).
2. **Access arm has TWO triggers, exactly as production `complete_answer:1578-1588`**:
   `if exit_kind == "access_ask" or fetch_arm == "tier-ask"`, with
   `tier_source = fetch if fetch_arm == "tier-ask" else payload`. Public surface:
   `answer_bridge.question_for(payload, *, fetch=None, parser, ctx, canned,
   asked_at_turn)`. `fetch` is the `run_fetch` fragment's own `fetch` item - measured:
   `lanes/business/__init__.py::run_fetch`'s `tier_ask` arm builds
   `item = fetch_mod.fetch_result({**payload, **collected})` (which carries `name`,
   `entitled_tiers`, `tier_availability` AND its own `_fetch_arm: "tier-ask"` at its
   OWN top level - `fetch_result` returns `{**j, "_fetch_arm": "tier-ask"}` when
   `tier_any_available` is a bool), then returns `{"kind": "tier_ask", "_fetch_arm":
   item["_fetch_arm"], "tier_probe": collected, "fetch": item}` - so `fetch` handed to
   the bridge IS `item` (the run_fetch wrapper's own `"fetch"` key), not the wrapper
   itself. `TestAccessAskArm` keeps the original hand-built `_exit_kind="access_ask"`
   cases (the "no access configured at all" arm - `resolve_gate.run`'s own exit fires
   only when the aggregate is EMPTY, see the class's own docstring) and ADDS the
   `fetch`-carried `tier-ask` variant, which is the real "entitled, must pick a tier"
   path production actually uses (`_exit_kind="continue"` + `tier_gate.tier_ask is
   True` upstream, then `run_fetch`'s own tier_ask arm probes each entitled tier -
   this is where the has/no promotion stamps come from).
   `TestMakeToolRunnerCarriesTheRealTierGate` pins the OTHER half of this: for that
   arm to fire live, `turn_runtime.make_tool_runner` must hand `lanes.business.
   run_fetch` the RESOLVER's real `tier_gate` (from `ResolveOutcome.payload
   ["tier_gate"]`) rather than today's synthetic `_tier_gate(spec, verdict, focus)`.
   Measured: `_tier_gate` returns `None` whenever `spec.filters.get("tier")` is falsy
   (no pick has happened yet) - PRECISELY the "no settled tier" case the general rule
   below covers; a pick already made (`spec.filters["tier"]` set) keeps today's
   `_tier_gate` recompose untouched. Not more subtle than that, as measured.
3. **AC-1701/F6 (attachment roster) moves to R4.** Confirmed: production never
   reaches the `offer` exit for `product_attachment` - `if_incoming_picker(gate)`
   checks `gate_debug.domain == "incoming"` literally, so `product_attachment` falls
   through to `not_found` and its roster + "has/no Product Photos" stamps come from
   the miss half (`miss_suggest`), R4's territory. This file's "offer/product" case
   stays pinned to `incoming` (the one `if_incoming_picker` + `annotate_incoming`
   actually implement today) - no test here expects an attachment `offer`.
4. `roster_cap` moved to `test_rearch_r3_roster_cap.py` with the pinned name
   `roster_caps: Mapping[str, int]` - not this file's concern.

**Pure, stubbed payloads.** No DB session anywhere in this file: `copy.fallback_copy()`
is the canned-copy object node replay itself grades against, and every payload below is
either hand-built in the shape the plan's own citations document, or produced by calling
the REAL production functions the plan names (`answer.access_level_choice_message`,
`pickers.annotate_customer`, `pickers.annotate_incoming`, `tail.outcome.escalate_catalog`,
`tail.outcome.build_outcome`, `tail.reply_ladder.compose_reply`) so the "expected" text in
every assertion is computed, never retyped by hand.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
from typing import Any

import pytest

from app.services.chatbot import copy as copy_mod
from app.services.chatbot import session_state
from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import pickers
from app.services.chatbot.tail import outcome as outcome_mod
from app.services.chatbot.tail import reply_ladder
from app.services.chatbot.turn import pending as pending_mod
from app.services.chatbot.turn.plan import FetchSpec
from app.services.chatbot.turn.state import Focus


def _bridge():
    """`app.services.chatbot.answer_bridge`, or `None` if it does not exist yet.

    Captain ruling 20 Sep 2026: the purity guard wins, no carve-out - the bridge
    lives OUTSIDE `turn/`, at this path (sibling of `turn_runtime.py`).
    """
    try:
        return importlib.import_module("app.services.chatbot.answer_bridge")
    except ModuleNotFoundError:
        return None


def _require_bridge():
    bridge = _bridge()
    assert bridge is not None, (
        "app.services.chatbot.answer_bridge does not exist yet (R3 slice, "
        "PLAN-chatbot-answer-half-reattach.md)"
    )
    assert hasattr(bridge, "question_for"), (
        "answer_bridge module exists but has no question_for(payload, *, fetch=None, "
        "parser, ctx, canned, asked_at_turn) function yet"
    )
    return bridge


def _canned():
    return copy_mod.fallback_copy()


def _promo_parser(raw: str = "srtwc286") -> dict[str, Any]:
    return {
        "domain_hint": "promotion",
        "intent_hint": "check_promotion",
        "entities": [
            {"raw": raw, "hint": "product", "current_message": True, "confident": True}
        ],
    }


def _ctx_for(parser: dict[str, Any]) -> dict[str, Any]:
    return {"parse": {"output": parser}, "contact": {"id": "zzt-contact"}, "session": {}}


# --------------------------------------------------------------------------- #
# Arm 1 - access_ask (the promotion tier picker, AC-1697/AC-1698's shape)
# --------------------------------------------------------------------------- #


def _tier_item(entitled_tiers: list[str], *, names: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": names if names is not None else ["Sorento Office", "Sorento Dealer"],
        "entitled_tiers": entitled_tiers,
        "tier_ask": True,
    }


def _expected_access_ask(item: dict[str, Any], parser: dict[str, Any], *, availability: Any):
    """The byte-for-byte production chain the plan's Design section names, computed by
    calling the real functions - never retyped copy."""
    lane_item = {
        **answer_mod.access_level_choice_message(item, parser=parser, tier_availability=availability),
        "branch_kind": "access_choice",
    }
    ctx = _ctx_for(parser)
    canned = _canned()
    catalog = outcome_mod.escalate_catalog(lane_item, ctx, canned, access_choice=lane_item)
    built = outcome_mod.build_outcome([{"json": catalog}], {"escalate-catalog": catalog})
    text = reply_ladder.compose_reply(built[0]["json"]["outcome"])["text"]
    return text, lane_item


class TestAccessAskArm:
    """The FIRST of the two `access_ask` triggers (production `complete_answer:1578`):
    `_exit_kind == "access_ask"`, `fetch=None` - `resolve_gate.run`'s own exit for an
    entry-gate aggregate that is EMPTY (the contact holds no access-type row at all,
    `answer.access_level_choice_message`'s "You have no access levels configured..."
    branch). AC-1697's shape, AC-1683 (produced by escalate_catalog + compose_reply,
    not turn/compose), AC-1684 (a Pending minted by pending.ask), and the MEASURED
    TRAP: `tier_last_result_set` rows carry `entity_type: "access_tier"`
    (`answer.py:324`), which `apply._set_kind_field`/`_CODE_ONLY_FIELDS` does not
    recognise - only `"tier"` reaches `focus.tier` (`turn/apply.py:107`). Every option
    the bridge mints for this arm must carry `entity_type == "tier"`, not the row's own
    literal value.

    `TestAccessAskArmViaTierAskFetch` below is the SECOND trigger
    (`fetch_arm == "tier-ask"`) - the real "entitled, must pick a tier" path.
    """

    @pytest.mark.parametrize(
        "availability,label",
        [
            (None, "none-measured"),
        ],
        ids=["none-measured"],
    )
    def test_text_matches_the_production_chain(self, availability, label) -> None:
        """Captain ruling 20 Sep 2026: a real `_exit_kind == "access_ask"` payload
        (aggregate-empty contact) never carries `tier_availability` - only the
        tier-ask FETCH arm's own per-tier promotion probe does
        (`TestAccessAskArmViaTierAskFetch` below). The `all-has`/`mixed` availability
        variants moved there; this arm keeps only the `None`/unmeasured case, which
        matches every payload this trigger can actually produce."""
        bridge = _require_bridge()
        item = _tier_item(["office", "dealer"])
        parser = _promo_parser()
        expected_text, _lane_item = _expected_access_ask(item, parser, availability=availability)

        payload = {**item, "_exit_kind": "access_ask"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=7
        )
        assert answer is not None, "question_for returned None for an access_ask payload"
        assert answer.text == expected_text

    def test_pending_kind_is_tier_pick_minted_via_pending_ask(self, monkeypatch) -> None:
        bridge = _require_bridge()
        calls: list[tuple[str, list[dict[str, Any]]]] = []
        real_ask = pending_mod.ask

        def _spy_ask(kind, options, **kwargs):
            calls.append((kind, options))
            return real_ask(kind, options, **kwargs)

        monkeypatch.setattr(pending_mod, "ask", _spy_ask)
        # The bridge is documented to call `turn.pending.ask` directly (its own module
        # import), so the spy must patch the SAME symbol the bridge holds a reference
        # to - if the coder imports `from app.services.chatbot.turn.pending import ask`
        # into `answer_bridge`, patch that name too so the spy is not silently bypassed.
        if hasattr(bridge, "pending_ask"):
            monkeypatch.setattr(bridge, "pending_ask", _spy_ask)
        if hasattr(bridge, "ask"):
            monkeypatch.setattr(bridge, "ask", _spy_ask)

        item = _tier_item(["office", "dealer", "end_user"])
        parser = _promo_parser()
        payload = {**item, "_exit_kind": "access_ask"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=11
        )
        assert answer is not None
        assert answer.question is not None, "an access_ask payload must raise a question"
        assert answer.question.kind == "tier_pick"
        assert answer.question.asked_at_turn == 11
        assert len(calls) >= 1, "pending.ask was never called for the access_ask arm"

    def test_options_are_in_tier_last_result_set_order_with_entity_type_tier(self) -> None:
        bridge = _require_bridge()
        item = _tier_item(["office", "dealer", "end_user"])
        parser = _promo_parser()
        payload = {**item, "_exit_kind": "access_ask"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=1
        )
        assert answer is not None and answer.question is not None
        options = answer.question.options
        assert len(options) == 3, options
        # order: office, dealer, end_user (ASK_ORDER), matching `tier_last_result_set`
        assert [o["payload"].get("value") for o in options] == ["office", "dealer", "end_user"]
        assert [o.get("label") for o in options] == ["Office", "Dealer", "End user"]
        # THE TRAP: every option must carry entity_type == "tier", never the row's own
        # "access_tier" literal, or a pick never reaches focus.tier (apply.py:107/121).
        assert [o.get("entity_type") for o in options] == ["tier", "tier", "tier"], (
            "an option minted with entity_type == 'access_tier' (the row's own literal "
            "value from answer.py:324) never reaches focus.tier - apply._set_kind_field "
            "only recognises 'tier' (turn/apply.py's _CODE_ONLY_FIELDS)"
        )

    def test_no_roster_ever_asked_with_fewer_than_two_options(self) -> None:
        """AC-1691's umbrella, for this arm: a contact entitled to exactly one tier must
        not be asked to pick among one - `narrow_decide`/`must_narrow_one`'s own rule for
        every other roster kind. Documents the expectation; the bridge is free to settle
        the single tier outright and return a `hit`-shaped Answer with no question
        (out of R3's own two arms) rather than raising a one-option pick - either
        reading is acceptable as long as `question_for` never returns a one-option
        tier_pick."""
        bridge = _require_bridge()
        item = _tier_item(["office"])
        parser = _promo_parser()
        payload = {**item, "_exit_kind": "access_ask"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=1
        )
        if answer is not None and answer.question is not None:
            assert len(answer.question.options) != 1, (
                "a one-tier entitlement must not be rendered as a one-option roster pick"
            )


def _tier_ask_fetch_item(
    entitled_tiers: list[str], *, names: list[str] | None = None, availability: Any = None
) -> dict[str, Any]:
    """The `run_fetch` tier_ask arm's own `fetch` item - `fetch.fetch_result`'s output,
    which carries the tier_gate's own keys (`name`, `entitled_tiers`, `tier_ask`) PLUS
    the per-tier probe fields PLUS its own `_fetch_arm`, all at ONE level
    (`lanes/business/__init__.py::run_fetch` ~1026-1053; `fetch_result` returns
    `{**j, "_fetch_arm": "tier-ask"}` whenever its input carries a boolean
    `tier_any_available`, which `tier_probe_collect` always sets)."""
    any_available = any(availability.values()) if availability is not None else True
    return {
        "name": names if names is not None else ["Sorento Office", "Sorento Dealer"],
        "entitled_tiers": entitled_tiers,
        "tier_ask": True,
        "tier_availability": availability,
        "tier_available_list": (
            [t for t in entitled_tiers if availability.get(t)] if availability is not None else None
        ),
        "tier_any_available": any_available,
        "_tier_probe_count": len(entitled_tiers) if availability is not None else 0,
        "_tier_probe_planned": len(entitled_tiers),
        "_fetch_arm": "tier-ask",
    }


class TestAccessAskArmViaTierAskFetch:
    """The SECOND `access_ask` trigger (production `complete_answer:1578-1588`):
    `fetch_arm == "tier-ask"`, `tier_source = fetch` (the item `_tier_ask_fetch_item`
    builds above). This is the REAL "entitled, must pick a tier" path production
    takes - the resolver's own `_exit_kind` is `"continue"` here (the resolver did
    not exit `access_ask`; the ASK is decided one step later, inside `run_fetch`'s
    own tier_ask arm, once the per-tier probe has run). See the module docstring's
    ruling 2 for the measured shape of `fetch`.
    """

    @pytest.mark.parametrize(
        "availability,label",
        [
            ({"office": True, "dealer": True}, "all-has"),
            ({"office": True, "dealer": False}, "mixed"),
        ],
        ids=["all-has", "mixed"],
    )
    def test_text_matches_the_production_chain_via_fetch(self, availability, label) -> None:
        """Captain ruling 20 Sep 2026: the `all-has`/`mixed` availability variants
        moved here from `TestAccessAskArm` - only THIS arm's payload (the per-tier
        promotion probe run inside `run_fetch`'s own tier_ask arm) ever carries
        `tier_availability`, so this is the only trigger a real stamped access-ask
        text can come from."""
        bridge = _require_bridge()
        entitled = ["office", "dealer"]
        fetch_item = _tier_ask_fetch_item(entitled, availability=availability)
        parser = _promo_parser()
        expected_text, _lane_item = _expected_access_ask(fetch_item, parser, availability=availability)

        answer = bridge.question_for(
            {"_exit_kind": "continue"},
            fetch=fetch_item,
            parser=parser,
            ctx=_ctx_for(parser),
            canned=_canned(),
            asked_at_turn=4,
        )
        assert answer is not None, "question_for returned None for a tier-ask fetch arm"
        assert answer.text == expected_text

    def test_fetch_arm_alone_is_enough_even_when_exit_kind_is_continue(self) -> None:
        """The OR condition: `fetch_arm == "tier-ask"` triggers this arm on its own -
        the resolver's own `_exit_kind` being `"continue"` (not `"access_ask"`) must
        not block it, exactly as production's `if exit_kind == "access_ask" or
        fetch_arm == "tier-ask":` reads."""
        bridge = _require_bridge()
        fetch_item = _tier_ask_fetch_item(["dealer", "office"])
        parser = _promo_parser()
        answer = bridge.question_for(
            {"_exit_kind": "continue"},
            fetch=fetch_item,
            parser=parser,
            ctx=_ctx_for(parser),
            canned=_canned(),
            asked_at_turn=1,
        )
        assert answer is not None and answer.question is not None
        assert answer.question.kind == "tier_pick"
        # the same MEASURED TRAP as TestAccessAskArm - entity_type must be "tier"
        assert [o.get("entity_type") for o in answer.question.options] == ["tier", "tier"]


# --------------------------------------------------------------------------- #
# Arm 2 - offer (the gate's own annotated pickers)
# --------------------------------------------------------------------------- #


def _customer_gate_payload() -> dict[str, Any]:
    """Hand-built in the shape `gate.run_gate`'s customer-picker arm documents
    (`gate.py:1024-1061`): `gate_clarification` is the bare numbered header (no stamps
    yet - `annotate_customer` adds those), `compatible_entities` carries one row per
    numbered line with the SAME rendered label as `title` (positional resolution), and
    `customer_probe_entities` is what made `if_customer_picker` take this exit in the
    first place. Company suffix "(MCH, SRT)" is baked into the label the same way
    `_rep_label(m) + _company_suffix(m)` builds it live."""
    labels = ["CHIN CHUN HARDWARE SDN BHD (MCH, SRT)", "CHIN CHUN HOMEMART (MCH)"]
    return {
        "gate_clarification": "Which customer do you mean? Please choose:\n"
        + "\n".join(f"{i + 1}. {label}" for i, label in enumerate(labels)),
        "compatible_entities": [
            {"uuid": f"cust-uuid-{i + 1}", "entity_type": "customer", "code": f"CUST{i + 1}", "title": label}
            for i, label in enumerate(labels)
        ],
        "customer_probe_entities": [
            {"uuid": f"cust-uuid-{i + 1}", "entity_type": "customer", "code": f"CUST{i + 1}"}
            for i in range(len(labels))
        ],
        "require_specific": False,
        "gate_debug": {"domain": "order"},
    }


def _expected_offer_text_from_annotated(annotated: dict[str, Any], parser: dict[str, Any]):
    """Composes the production reply from an ALREADY-annotated gate payload - no
    annotate call in here. Captain ruling 20 Sep 2026: production's `offer` exit is
    ALWAYS post-annotation (`resolve_gate.py:~1096`/`~1133` call
    `pickers.annotate_incoming`/`annotate_customer` with the real probe before
    returning), so the bridge must never probe or annotate - it only ever sees the
    already-annotated payload, and so must this helper."""
    lane_item = {**annotated, "branch_kind": "not_found"}
    ctx = _ctx_for(parser)
    canned = _canned()
    catalog = outcome_mod.escalate_catalog(lane_item, ctx, canned, incoming_picker=lane_item)
    built = outcome_mod.build_outcome([{"json": catalog}], {"escalate-catalog": catalog})
    text = reply_ladder.compose_reply(built[0]["json"]["outcome"])["text"]
    return text, lane_item


def _order_parser(raw: str = "srtwc286") -> dict[str, Any]:
    return {
        "domain_hint": "order",
        "intent_hint": "check_order",
        "entities": [
            {"raw": raw, "hint": "product", "current_message": True, "confident": True}
        ],
    }


class TestOfferArmCustomer:
    """AC-1694's pytest half: the customer picker, company suffix, has/no-DO stamp."""

    def test_text_matches_the_production_chain_no_do_for_one_of_two(self) -> None:
        bridge = _require_bridge()
        gate = _customer_gate_payload()
        parser = _order_parser()
        # `_row_has_do` reads the "Actual Delivery Date" field; one hit for the first
        # customer, none for the second - matches AC-1694's own worked example ("CHIN
        # CHUN HOMEMART reads 'no DO'").
        probe = {
            "answers": [
                {
                    "customer_name": "CHIN CHUN HARDWARE SDN BHD",
                    "fields": [{"label": "Actual Delivery Date", "value": "2026-09-01"}],
                }
            ]
        }
        # Annotate the SAME dict the bridge receives - production hands the bridge
        # an already-annotated payload, so the test must too, not a separately
        # annotated copy compared against an unannotated original.
        annotated = pickers.annotate_customer(gate, probe=probe, parser=parser)
        expected_text, _lane_item = _expected_offer_text_from_annotated(annotated, parser)
        assert "has DO" in expected_text and "no DO" in expected_text, expected_text

        payload = {**annotated, "_exit_kind": "offer"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=3
        )
        assert answer is not None, "question_for returned None for a customer offer payload"
        assert answer.text == expected_text

    def test_pending_kind_customer_pick_options_in_printed_order(self) -> None:
        bridge = _require_bridge()
        gate = _customer_gate_payload()
        parser = _order_parser()
        payload = {**gate, "_exit_kind": "offer"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=3
        )
        assert answer is not None and answer.question is not None
        assert answer.question.kind == "customer_pick"
        options = answer.question.options
        assert len(options) == 2, options
        assert [o.get("uuid") for o in options] == ["cust-uuid-1", "cust-uuid-2"]


def _incoming_gate_payload() -> dict[str, Any]:
    """Hand-built matching `if_incoming_picker`'s own exit (`resolve_gate.py:1081-1097`):
    `require_specific=True`, `gate_debug.domain == "incoming"`, `gate_clarification` the
    bare numbered header `run_gate`'s own product roster builds."""
    labels = ["SRTWC286-1", "SRTWC286-2"]
    return {
        "gate_clarification": (
            "Which product do you mean? Please choose:\n" + "\n".join(f"{i + 1}. {label}" for i, label in enumerate(labels))
        ),
        "compatible_entities": [
            {"uuid": f"prod-uuid-{i + 1}", "entity_type": "product", "code": label}
            for i, label in enumerate(labels)
        ],
        "require_specific": True,
        "gate_debug": {"domain": "incoming"},
    }


class TestOfferArmIncomingProduct:
    """The product-roster half of the offer arm the plan can actually produce today
    (see module docstring point 3 for why this is `incoming`, not `product_attachment`
    /AC-1701's own text)."""

    def test_text_matches_the_production_chain(self) -> None:
        bridge = _require_bridge()
        gate = _incoming_gate_payload()
        parser = {
            "domain_hint": "incoming",
            "entities": [{"raw": "srtwc286", "hint": "product", "current_message": True}],
        }
        probe = {"answers": [{"title": "SRTWC286-1"}]}
        # Annotate the SAME dict the bridge receives (captain ruling 20 Sep 2026:
        # production's offer exit is ALWAYS post-annotation - the bridge must not
        # probe or annotate, so hand it an already-annotated payload, not an
        # unannotated original compared against a separately annotated copy).
        annotated = pickers.annotate_incoming(gate, probe=probe)
        expected_text, _lane_item = _expected_offer_text_from_annotated(annotated, parser)
        assert "has incoming" in expected_text and "no incoming" in expected_text, expected_text

        payload = {**annotated, "_exit_kind": "offer"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=2
        )
        assert answer is not None
        assert answer.text == expected_text

    def test_pending_kind_product_pick_option_count_matches_printed_lines(self) -> None:
        bridge = _require_bridge()
        gate = _incoming_gate_payload()
        parser = {
            "domain_hint": "incoming",
            "entities": [{"raw": "srtwc286", "hint": "product", "current_message": True}],
        }
        payload = {**gate, "_exit_kind": "offer"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=2
        )
        assert answer is not None and answer.question is not None
        assert answer.question.kind == "product_pick"
        assert len(answer.question.options) == 2


# --------------------------------------------------------------------------- #
# question_for returns None outside its two arms (R4/R5's own territory)
# --------------------------------------------------------------------------- #


class TestQuestionForReturnsNoneOutsideItsTwoArms:
    @pytest.mark.parametrize("exit_kind", ["continue", "not_found"])
    def test_none_for_the_other_exit_kinds(self, exit_kind: str) -> None:
        bridge = _require_bridge()
        parser = _promo_parser()
        payload = {"_exit_kind": exit_kind, "gate": {}}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=1
        )
        assert answer is None, (
            f"question_for must return None for _exit_kind={exit_kind!r} (R4/R5's own arms)"
        )

    def test_none_for_a_null_payload(self) -> None:
        bridge = _require_bridge()
        parser = _promo_parser()
        answer = bridge.question_for(
            None, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=1
        )
        assert answer is None


# --------------------------------------------------------------------------- #
# AC-1684 - contract 36, a bridged roster stays sticky through with_answered_positions
# --------------------------------------------------------------------------- #


class TestStickyRosterContract36:
    def test_with_answered_positions_layers_onto_a_bridged_customer_pick(self) -> None:
        bridge = _require_bridge()
        gate = _customer_gate_payload()
        parser = _order_parser()
        payload = {**gate, "_exit_kind": "offer"}
        answer = bridge.question_for(
            payload, parser=parser, ctx=_ctx_for(parser), canned=_canned(), asked_at_turn=5
        )
        assert answer is not None and answer.question is not None
        pending = answer.question
        assert pending_mod.is_roster(pending.kind), (
            "customer_pick must be a contract-36 roster kind"
        )
        layered = pending_mod.with_answered_positions(pending, [1])
        assert layered.answered_positions == [1]
        layered_again = pending_mod.with_answered_positions(layered, [2])
        assert layered_again.answered_positions == [1, 2]
        # the roster's own options are untouched by layering an answered position
        assert layered_again.options == pending.options

    def test_tier_pick_is_a_roster_kind_the_same_as_product_and_customer(
        self,
    ) -> None:
        """SUPERSEDED (hand pass 9 ruling, 20 Sep 2026): `tier_pick` is now IN
        `pending.ROSTER_KINDS`. Main's `tail/compile_state.py::_picker_carry` draws no
        kind-based distinction at all - a `require_specific` roster stays answerable
        across as many picks as the customer makes, and the tier ask is the identical
        shape (a numbered list, one axis) to the product/customer/kind pickers this
        contract already covers. The OLD assertion here (`is_roster("tier_pick") is
        False`) pinned a gap: a SECOND, different tier position over the same
        still-open roster re-printed the FIRST pick's own reply instead of running a
        fresh fetch for the newly named tier (live turn
        25dcef1d-decc-407b-a6eb-08d8112ee814, `tests/chatbot/
        test_rearch_r9_handpass9_replay.py::
        TestPromotionTierRosterStaysAnswerableAcrossMultiplePicks`). `answered_positions`
        still does not need to accumulate INTO the tier filter itself:
        `turn/apply.py::_answer_pending` rebuilds `focus.tier`/`access_levels` fresh
        from EACH turn's own matched option, the same "replace, not merge" behaviour a
        product roster's second pick already has."""
        assert pending_mod.is_roster("tier_pick") is True


# --------------------------------------------------------------------------- #
# Captain ruling 2's other half: make_tool_runner must carry the RESOLVER's real
# tier_gate to lanes.business.run_fetch, not today's synthetic _tier_gate(...)
# --------------------------------------------------------------------------- #

TURN_RUNTIME_PY = pathlib.Path(
    inspect.getfile(turn_runtime)
)


class TestMakeToolRunnerCarriesTheRealTierGate:
    """Mirrors `test_rearch_r2_resolve_outcome_and_grant.py::
    TestMakeToolRunnerCarriesTheRealGate`'s own convention for `resolver_gate`, one
    level over: `make_tool_runner` has no parameter today that carries the
    resolver's own `tier_gate` (`ResolveOutcome.payload["tier_gate"]`) to
    `lanes.business.run_fetch` - only the synthetic `_tier_gate(spec, verdict,
    focus)` (`turn_runtime.py:1392`) does, and it returns `None` whenever
    `spec.filters.get("tier")` is falsy, i.e. exactly the "no tier settled yet"
    case this general rule covers. This test's own chosen name for the new
    parameter (`resolver_tier_gate`) is a guess, not read off any committed source -
    a coder naming it differently only needs to update this test's CALL SITE, not
    the assertions about what `lanes.business.run_fetch` must receive.

    MEASURED, not more subtle than the captain's own framing: a tier ALREADY
    settled by a pick (`spec.filters["tier"]` set) keeps TODAY'S `_tier_gate`
    recompose untouched - that arm's own test below computes its expectation by
    calling the real `turn_runtime._tier_gate` function, never retyping its
    recompose logic.
    """

    def test_no_settled_tier_passes_the_resolvers_real_tier_gate(self, monkeypatch) -> None:
        from app.services.chatbot.lanes import business

        calls: list[dict[str, Any]] = []

        def stub_run_fetch(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            calls.append(payload)
            return {"has_result": False}

        monkeypatch.setattr(business, "run_fetch", stub_run_fetch)

        sig = inspect.signature(turn_runtime.make_tool_runner)
        assert "resolver_tier_gate" in sig.parameters, (
            "make_tool_runner has no parameter carrying the resolver's own tier_gate "
            "through to lanes.business.run_fetch yet (checked via inspect.signature) - "
            "this tester's own chosen name is 'resolver_tier_gate'"
        )

        resolver_tier_gate = {
            "name": ["Sorento Office", "Sorento Dealer"],
            "entitled_tiers": ["office", "dealer"],
            "tier_ask": True,
        }
        runner = turn_runtime.make_tool_runner(
            object(),
            ctx={"parse": {"output": {}}},
            verdict={"access_levels": []},
            focus=Focus(),
            compatible_entities=[
                {"raw": "SRTWC286", "entity_type": "product", "canonical_code": "SRTWC286"}
            ],
            predicate=None,
            unplaced={},
            space_id=None,
            dry_run=True,
            turn_trace=None,
            resolver_tier_gate=resolver_tier_gate,
        )
        # NO "tier" filter - unsettled: the resolver's own tier_gate must reach run_fetch.
        spec = FetchSpec(domain="promotion", entities=[], filters={}, date_window=None)
        runner("promotion", spec)
        assert calls, "lanes.business.run_fetch was never called"
        assert calls[0]["tier_gate"] == resolver_tier_gate, (
            f"expected the resolver's real tier_gate, got {calls[0].get('tier_gate')!r}"
        )

    def test_a_settled_tier_pick_keeps_todays_recompose_against_the_real_gate(
        self, monkeypatch
    ) -> None:
        """A tier already settled by a pick still recomposes through today's
        `_tier_gate(spec, verdict, focus, resolver_tier_gate)` - it is not "synthetic"
        any more, since S4 made `_tier_gate` itself fail closed to `[]` whenever no
        resolver gate reaches it (`spec.filters.get("tier")` truthy is not enough on
        its own once a resolver ran this turn). The expectation is therefore computed
        by calling the real function WITH the same `resolver_tier_gate` the runner is
        given below, never without it - a call missing that argument is stale against
        the fail-closed contract, not a fair pin of "today's recompose".
        """
        from app.services.chatbot.lanes import business

        calls: list[dict[str, Any]] = []

        def stub_run_fetch(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            calls.append(payload)
            return {"has_result": False}

        monkeypatch.setattr(business, "run_fetch", stub_run_fetch)

        sig = inspect.signature(turn_runtime.make_tool_runner)
        assert "resolver_tier_gate" in sig.parameters, (
            "make_tool_runner has no resolver_tier_gate parameter yet"
        )

        verdict = {"access_levels": ["Sorento Dealer"]}
        focus = Focus()
        spec = FetchSpec(domain="promotion", entities=[], filters={"tier": "dealer"}, date_window=None)
        resolver_tier_gate = {
            "name": ["Sorento Dealer"], "entitled_tiers": ["dealer"], "tier_ask": True
        }
        expected = turn_runtime._tier_gate(spec, verdict, focus, resolver_tier_gate)

        runner = turn_runtime.make_tool_runner(
            object(),
            ctx={"parse": {"output": {}}},
            verdict=verdict,
            focus=focus,
            compatible_entities=[
                {"raw": "SRTWC286", "entity_type": "product", "canonical_code": "SRTWC286"}
            ],
            predicate=None,
            unplaced={},
            space_id=None,
            dry_run=True,
            turn_trace=None,
            resolver_tier_gate=resolver_tier_gate,
        )
        runner("promotion", spec)
        assert calls, "lanes.business.run_fetch was never called"
        assert calls[0]["tier_gate"] == expected, (
            f"a settled tier pick must keep today's recompose against the real "
            f"resolver gate: expected {expected!r}, got {calls[0].get('tier_gate')!r}"
        )


# --------------------------------------------------------------------------- #
# Captain ruling 1 - the missing purity direction: no file under turn/ imports
# answer_bridge (the `tail` direction is already covered by
# test_rearch_s2_apply_is_pure.py's own parametrize, not duplicated here).
# --------------------------------------------------------------------------- #


class TestTurnPackagePurity:
    def test_no_file_under_turn_imports_answer_bridge(self) -> None:
        turn_dir = TURN_RUNTIME_PY.parent / "turn"
        assert turn_dir.is_dir(), f"app/services/chatbot/turn/ does not exist: {turn_dir}"
        files = sorted(turn_dir.glob("*.py"))
        assert files, f"app/services/chatbot/turn/ has no .py files: {turn_dir}"
        hits: list[str] = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    parts = node.module.split(".")
                    if "chatbot" in parts:
                        idx = parts.index("chatbot")
                        # `from app.services.chatbot import answer_bridge`
                        if len(parts) == idx + 1 and any(
                            alias.name == "answer_bridge" for alias in node.names
                        ):
                            hits.append(f"{path.name}: from {node.module} import answer_bridge")
                        # `from app.services.chatbot.answer_bridge import X`
                        if len(parts) > idx + 1 and parts[idx + 1] == "answer_bridge":
                            hits.append(f"{path.name}: {node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "app.services.chatbot.answer_bridge" or (
                            alias.name.endswith(".answer_bridge")
                            and "chatbot" in alias.name.split(".")
                        ):
                            hits.append(f"{path.name}: import {alias.name}")
        assert hits == [], f"turn/ imports answer_bridge (the impure bridge home): {hits}"
