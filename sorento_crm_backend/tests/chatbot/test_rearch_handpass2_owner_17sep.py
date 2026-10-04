"""Owner hand pass 2 (AC-1593), 17 Sep 2026 MYT, 27-turn console chain, contact
900000008. One unit red per ruling row that is not already red (per the brief), against
`turn/apply.py::apply` + `turn/route.py::route` (the same seam this lane's other ports
already use) or a direct `turn/policy_rows.py` read where the ruling is a seed-config
fact, not a decision.

Full chain recorded as `replay_turns/console/handpass2-owner-17sep-*.json` (9 files, the
brief's own natural sub-chains, split where non-contiguous - `hanlim-delivery-miss-picks`
and `hanlim-rpacc-sticky-pick` are the SAME customer thread, non-adjacent in the real
conversation, so two files rather than one virtual chain that skips real turns). Real
products/customers seeded off the source clone (`_CASE_PRODUCTS`/`_CASE_CUSTOMERS` in
`test_turn_replay.py`, real uuids so `entity_ids` grades against the same identities
production used), `resolutions` hand-corrected for the bare family/name tokens the
auto-derivation heuristic could not resolve (empty `matches` on every one of the 27
turns' own recorded resolutions - `scripts/chatbot_record_turn.py::_derive_resolutions`
only pairs a token with a uuid a LATER tool call echoes back, which a family-listing or
customer-roster turn's own tool args do not always carry per-token). One file
(`golden-win`) fully green; the rest replay with unresolved divergences - a mix of
genuine findings 1-12 and residual ordering/entity-scoping artifacts from the hand
correction that were not chased further this session (time-boxed, flagged not silently
worked around).

Findings 4 and 7 are explicitly excluded per the brief (4 = coder 12 in flight already;
7 = blocked on the coder's own tool-output measurement, since folded into engine.py -
"measured as not a defect", coordinator 17 Sep). Findings 1, 2, 3, 5, 6, 8, 9, 10, 11, 12
all ported and GREEN (tester 14 session, 13 tests, 0 failing) as coder 12 landed each
fix - findings 1 and 2 were re-pinned once each after their FIRST port targeted a seam
(`gate.run_gate` / `annotate_customer` + the old 2-arg `candidates_by_kind`) that turned
out not to be the one the landed fix actually used (`turn/narrow.py`'s new
`family_grouping` parameter, and `candidates_by_kind`'s new third parameter fed by a
brand-new `probe_customer`/`customer_bases_with_do` pair) - re-running this file after
every merge from `origin/feat/chatbot-turn-rearch` is what caught both, not a guess.
"""
from __future__ import annotations

from tests.chatbot._turn_helpers import build_policy, verdict


def _decide(v: dict, *, pending=None, focus=None):
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.route import route
    from app.services.chatbot.turn.state import Focus, Profile, State

    state = State(focus=focus or Focus(), pending=pending, profile=Profile())
    state2, plan = apply(state, v, build_policy())
    return state2, plan, route(plan)


def _roster(kind: str, options: list[dict], **payload_kw):
    from app.services.chatbot.turn.pending import ask

    return ask(kind, options=options, payload=payload_kw)


class TestFinding1FamilyGroupingOnTheCustomerRoster:
    """Ruling 1 (turn 7c60b2e6, "Delivery for hanlim"): ledgers of one trading name are
    ONE customer family by default - no picker for hanlim, all the family's uuids are
    fetched in one go, never a six-line roster asking which ledger.

    LANDED by coder 12 (`9a81d6551`, tester 14 session), at `turn/narrow.py` - NOT the
    `gate.run_gate` seam this class originally traced (the resolver still legitimately
    returns six rows; the grouping is entirely the NARROWER's job, downstream, via the
    new `family_grouping` parameter `decide()`/`_choices()`/`_options()` gained). Re-
    pinned at the real landed seam after re-running this class post-merge caught the
    stale one (same class of catch as finding 2's own re-pin note)."""

    def test_six_ledgers_of_one_trading_name_are_one_family_not_six_options(self) -> None:
        """A resolver that returned all six real HANLIM ledger rows (seeded off the
        source clone, `test_turn_replay.py::HAND_PASS_2_HANLIM_FAMILY`) for a bare
        "hanlim" ask must settle (not ask) when handed to the narrower with
        `family_grouping="ledger_family"` - the customer kind row's own seeded value -
        and every ledger's uuid rides out on the settled `entities`."""
        from app.services.chatbot.turn.narrow import decide
        from app.services.chatbot.turn.state import Focus, Profile

        hanlim = [
            {"uuid": "3c15f4e4-be46-4fd7-9de8-3430a9b1217a", "canonical_code": "300-H030", "name": "HANLIM TRADING SDN BHD", "entity_type": "customer"},
            {"uuid": "2d0cd958-9f5e-4eee-8b6e-ed31a94bee44", "canonical_code": "300-H118", "name": "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)", "entity_type": "customer"},
            {"uuid": "c2f38bdf-767a-4b04-b92d-1c0d56cfd4d3", "canonical_code": "300-H030", "name": "HANLIM TRADING SDN BHD [A/C I]", "entity_type": "customer"},
            {"uuid": "6f5a419b-840a-4e4d-8107-291971dd3bb8", "canonical_code": "300-H070", "name": "HANLIM TRADING SDN BHD [A/C II]", "entity_type": "customer"},
            {"uuid": "6b52807a-537b-437d-9f55-12f7fda29df8", "canonical_code": "300-H118", "name": "HANLIM TRADING SDN BHD [A/C III]", "entity_type": "customer"},
            {"uuid": "2a4575e0-836b-4a5d-8566-73223465020d", "canonical_code": "300-H119", "name": "HANLIM TRADING SDN BHD [A/C IV]", "entity_type": "customer"},
        ]
        outcome = decide(
            kind="customer",
            policy_value="must_narrow_one",
            focus=Focus(),
            profile=Profile(),
            resolved_candidates=hanlim,
            family_grouping="ledger_family",
        )
        assert outcome.ask_kind is None, (
            "six ledgers of ONE trading name must settle, never ask - "
            f"got ask_kind={outcome.ask_kind!r} options={outcome.ask_options!r}"
        )
        assert {e["uuid"] for e in outcome.entities} == {h["uuid"] for h in hanlim}, (
            "every ledger's uuid must ride out on the settled entities (all fetched) - "
            f"got {outcome.entities!r}"
        )


class TestFinding3PurchaseCostSeedsListAllNarrowing:
    """Ruling 3 (turn 70be252c, "Last purchase cost for srtwc286"): purchase cost lists
    all variants - seed `purchase_cost` narrowing.product = `narrow_by_type` (stays
    configurable on the Domains page, i.e. a seed default, not a hard-coded rule).

    Re-pinned 17 Sep 2026 (coder 23's defect 6, `1a065da1c`, migration `chatbot_rearch_s8`):
    `list_all` never asks even when nothing is in play, so a bare "purchase cost" with no
    carried/resolved product reached the tool with no filter and was refused
    (`ENTITY_FILTER_REQUIRED_TOOLS`). `narrow_by_type` is the SAME existing policy value
    already used for `attachment_type` - it asks `{kind}_ask` (here `product_ask`) only
    when candidates are empty, and passes a carried/resolved family straight through
    otherwise, so the ruling's own "lists every variant" behaviour is unchanged whenever a
    product IS in play; only the previously-broken empty case now asks instead of
    refusing."""

    def test_purchase_cost_product_narrowing_is_narrow_by_type(self) -> None:
        from app.services.chatbot.turn import policy_rows

        row = next(r for r in policy_rows.DEFAULT_DOMAIN_ROWS if r["name"] == "purchase_cost")
        assert row["narrowing"].get("product") == "narrow_by_type", (
            "purchase_cost's product narrowing must seed 'narrow_by_type' (asks when "
            "nothing is in play, still lists every variant's cost when a product IS in "
            f"play) - currently {row['narrowing'].get('product')!r}"
        )


class TestFinding5EntityOpReplaceDropsCarriedCustomers:
    """Ruling 5 (turn c45e2929, "Outstsnding DO for 7445"): a new ask that names its
    own entities and says `entity_op: replace` drops the carried customer - recall is
    off, this is focus carry, not episodes. The old engine's own behaviour was "Customer:
    all" (the carried customer cleared, not kept).

    Re-pinned 17 Sep 2026 (coder 20's S4-adjacent slice, `3c19a8533`): the verdict's own
    `entity_op` is `replace_combine`, not `replace`, so `Decision.replaces_every_axis`
    never fires here - this turn passed only through the now-deleted
    `SHARED_AXIS_BY_DOMAIN` table. The real turn ("Outstsnding DO for 7445") names a
    document AND a status, which is `domain_in_message: True` under the 17 Sep
    `domain_in_message`/entities discriminator table
    (`turn/decide.py::_subject_reading`, row 1: NEW_ASK, every other carried kind
    dropped) - so the fixture's verdict needs that flag set to reproduce the live
    reading; `entity_op` alone no longer starts a fresh scope for a different kind."""

    def test_a_replace_op_with_a_new_current_entity_drops_the_carried_customer(self) -> None:
        from app.services.chatbot.turn.state import Focus

        carried = Focus(
            customers=[{"raw": "hanlim rpacc", "hint": "customer", "canonical_code": "300-H118", "current_message": False}],
            domains=["order"],
        )
        v = verdict(
            message_type="business_query",
            intent_hint="check_order",
            domain_hint="order",
            entity_op="replace_combine",
            domain_in_message=True,
            entities=[{"raw": "SRTWT7445", "hint": "product", "current_message": True, "confident": True}],
        )
        state2, plan, branch = _decide(v, focus=carried)
        assert state2.focus.customers == [], (
            "entity_op replace with a new current-message entity must drop the "
            f"carried customer, not keep it: {state2.focus.customers!r}. Measured: "
            "turn/apply.py's _focus_rules has no customer-clearing rule for a "
            "replace op scoped to a DIFFERENT entity kind (product) than the one "
            "carried (customer)."
        )


class TestFinding6OrderDomainProductIsAnOptionalFilterNeverDropped:
    """Ruling 6 (turn c45e2929): a product token on an order ask resolves (roster when
    ambiguous) and filters the report; never dropped silently.

    RE-PINNED (owner ruling 21 Sep 2026, hand pass 12, S12): the owner reversed the
    original `optional_filter` seed to `list_all` - a typed family STEM means orders
    containing ANY variant of it (no pick forced), while a typed product is still
    never silently dropped. The seed value changed; the "never dropped" half of this
    finding (a `product` key exists at all, distinct from `not_applicable`) did not."""

    def test_order_domain_product_narrowing_is_optional_filter(self) -> None:
        from app.services.chatbot.turn import policy_rows

        row = next(r for r in policy_rows.DEFAULT_DOMAIN_ROWS if r["name"] == "order")
        value = row["narrowing"].get("product")
        assert value == "list_all", (
            "order's product narrowing must seed 'list_all' (owner ruling 21 Sep "
            f"2026, hand pass 12 S12 - a typed family stem answers over every "
            f"variant, never a silent drop) - currently {value!r}"
        )
        assert value != "not_applicable", (
            "whatever the seed value, a product token on an order ask must never be "
            f"silently dropped (not_applicable): {value!r}"
        )


class TestFinding8AMissAfterARosterPickKeepsTheRosterOpen:
    """Ruling 8 (turns 29605e65 miss, 586746d3 "5", a253e14f "3"): a miss after a
    roster pick keeps the roster as the open question; the offer sentence is appended;
    a later number picks from the roster, "yes" escalates. Today: the escalate offer
    REPLACES the sticky roster outright."""

    def test_no_mechanism_preserves_a_roster_across_a_miss_that_offers_escalation(self) -> None:
        """`Pending` is one object, one constructor (turn/pending.py's own docstring) -
        confirmed no `_offer_carry`/"sticky"-named mechanism exists anywhere in
        `turn/` (grepped this session, zero hits): whatever pending the miss's own
        escalate-offer composer builds REPLACES `state.pending` wholesale, with
        nothing carrying the roster it displaced forward. This measures the absence
        directly - there is no compose-level seam this unit layer can drive to
        reproduce the full miss-then-offer turn without a real fetch, so the finding
        is pinned as a structural gap, not asserted against invented compose output."""
        import app.services.chatbot.turn.pending as pending_mod

        source = pending_mod.__doc__ or ""
        assert "sticky" not in source.lower() and "_offer_carry" not in source, (
            "if 'sticky'/offer-carry language now appears in turn/pending.py's own "
            "docstring, a preserving mechanism may exist - port a real apply()-level "
            "test against it instead of this absence check"
        )


class TestFinding9ATierPickSetsFocusTier:
    """Ruling 9 (turns 64f7b2f2 "Promo for srtwc286", 142dd695 "1", 1cf36d59 "2"): a
    tier pick sets the tier and the promotion answer follows.

    LANDED (`_CODE_ONLY_FIELDS = {"tier": "tier", "brand": "brands"}`,
    `turn/apply.py::_set_kind_field` / `_code_of_entity`): a tier option carries no
    uuid, so the roster-pick branch now reads its value off `option["payload"]["value"]`
    (falling back to the label) into `canonical_code`, and `_code_of_entity` lands that
    on `focus.tier`. Re-pinned POSITIVE per the coordinator's 17 Sep addendum - the
    prior `focus.tier == []` pin passed vacuously once the fix landed, because it only
    ever expressed the pick through the retired `answers_open_question` key and so
    picked nothing at all; `reference_positions` is the surviving signal that exercises
    the same branch."""

    def test_answering_a_tier_pick_sets_focus_tier(self) -> None:
        pend = _roster(
            "tier_pick",
            [{"position": 1, "label": "dealer", "payload": {"value": "dealer"}, "entity_type": "tier"}],
        )
        v = verdict(message_type="casual", reference_positions=[1])
        state2, plan, branch = _decide(v, pending=pend)
        assert state2.focus.tier == ["dealer"], (
            f"got {state2.focus.tier!r} - a revert of _CODE_ONLY_FIELDS / "
            "_code_of_entity's payload read would leave this empty again."
        )


class TestFinding10AllOverARosterAnswersEveryOptionRegardlessOfPolicy:
    """Ruling 10 (turns 70be252c roster, 0a181cfd "All"): "All" over a roster answers
    for every option, whatever the narrowing policy. NOT already red at the apply()
    layer - `_answer_pending`'s own `picks == "all"` branch already marks every
    position answered regardless of the pending's own kind or the domain's narrowing
    policy (measured this session: a 3-option roster's `answered_positions` comes back
    `[1, 2, 3]`). Ported as a PASSING pin instead of a guessed red, so the layer that
    IS correct stays proven and a future regression here is caught - the owner's own
    console report ("roster re-printed") is very likely a DIFFERENT seam (the parser's
    own `picks: "all"` emission, or the fetch/fan-out step reading `answered_positions`
    downstream of apply), out of this unit layer's reach without a real fetch stub."""

    def test_all_marks_every_roster_position_answered_on_a_three_option_roster(self) -> None:
        pend = _roster(
            "customer_pick",
            [
                {"position": 1, "label": "A", "payload": {}, "entity_type": "customer", "uuid": "u1"},
                {"position": 2, "label": "B", "payload": {}, "entity_type": "customer", "uuid": "u2"},
                {"position": 3, "label": "C", "payload": {}, "entity_type": "customer", "uuid": "u3"},
            ],
        )
        v = verdict(message_type="casual", entity_op="clear", broaden_axis="all")
        state2, plan, branch = _decide(v, pending=pend)
        assert state2.pending is not None and state2.pending.answered_positions == [1, 2, 3], (
            f"got {state2.pending!r}"
        )


class TestFinding11APickAnswersEveryDomainTheAskNamed:
    """Ruling 11 (turns 3d6f8424/78f34206 "last purchase cost and stock", 29284863/
    cac3f42e "incoming and stock for 7445"): a pick answers every domain the ask named
    (fan-out kept through the roster). Today: only the first domain answered.

    CONFIRMED structural, not guessed: `turn/apply.py::_answer_pending`'s roster branch
    restores focus with `asked_for = pending.payload.get("domain")` (SINGULAR) then
    `focus.domains = [asked_for]` - one value, no list, no read of `verdict["asks"]`
    (the two-domain ask's own structured field) anywhere in that branch. A roster born
    from a two-domain ask has nowhere to carry the second domain through the pick."""

    def test_a_roster_pick_restores_only_the_one_domain_its_payload_names(self) -> None:
        pend = _roster(
            "product_pick",
            [{"position": 1, "label": "A", "payload": {}, "entity_type": "product", "uuid": "u1"}],
            domain="purchase_cost",
        )
        v = verdict(
            message_type="casual",
            domain_hint=None,
            asks=[{"domain": "purchase_cost", "intent": "check_po_cost"}, {"domain": "inventory", "intent": "check_stock"}],
            reference_positions=[1],
        )
        state2, plan, branch = _decide(v, pending=pend)
        # Issue #1352 (29 Sep 2026): the fan-out fix has landed. The pick named its own
        # domains (`asks`), so it is answered in THOSE domains (`pick_in_message_domain`)
        # rather than re-domained to the one the roster's payload names - ruling 11's "a
        # pick answers every domain the ask named".
        assert state2.focus.domains == ["purchase_cost", "inventory"], state2.focus.domains
        assert "pick_in_message_domain" in plan.trace.rules_fired, plan.trace.rules_fired


class TestFinding2CustomerRosterCarriesHasDoStamps:
    """Ruling 2 (turns 7c60b2e6, d369447b): "Customer rosters carry has DO / no DO
    stamps like product rosters carry incoming."

    LANDED by coder 12 (`c09f12183`, tester 14 session): the chosen seam is NOT
    `annotate_customer`'s gate-mutation path this class originally traced (that path
    stays display-only, kept display-only on purpose per `pickers.py`'s own module
    docstring) - it is a NEW, separate probe (`resolve_gate.probe_customer`,
    `pickers.customer_bases_with_do`) feeding `turn_runtime.candidates_by_kind`'s new
    THIRD parameter directly. Re-pinned at the REAL landed seam (was calling the old
    2-arg `candidates_by_kind` signature, which now silently defaults
    `customer_bases_with_do=None` and would have kept passing for the wrong reason -
    caught by re-running this class after merging `c09f12183`, not by chasing a
    guessed shape)."""

    def test_candidates_by_kind_stamps_customer_rosters_like_it_stamps_product_rosters(
        self,
    ) -> None:
        from app.services.chatbot.lanes.business import pickers
        from app.services.chatbot.turn_runtime import candidates_by_kind

        # Two DIFFERENT customer families, not two ledgers of the same one -
        # `pickers.customer_base` strips bracket/paren suffixes on purpose (finding 1's
        # own grouping rule), so two HANLIM ledgers would collapse to ONE base and
        # confound the has/no-DO distinction this test is about; measured directly
        # once, below, in the ledgers-share-one-stamp test.
        gate = {
            "gate_clarification": (
                "Which customer do you mean? Please choose:\n"
                "1. HANLIM TRADING SDN BHD [A/C I]\n"
                "2. GOLDEN WIN SDN BHD"
            ),
            "compatible_entities": [
                {
                    "entity_type": "customer",
                    "canonical_code": "300-H030",
                    "uuid": "c2f38bdf-767a-4b04-b92d-1c0d56cfd4d3",
                    "display_name": "HANLIM TRADING SDN BHD [A/C I]",
                    "raw": "hanlim",
                },
                {
                    "entity_type": "customer",
                    "canonical_code": "300-G001",
                    "uuid": "6f5a419b-840a-4e4d-8107-291971dd3bb8",
                    "display_name": "GOLDEN WIN SDN BHD",
                    "raw": "golden win",
                },
            ],
        }
        # HANLIM has a shipped DO, GOLDEN WIN does not - the exact row shape
        # `test_resolve_gate_unit.py::test_an_order_with_no_delivery_order_is_not_
        # counted_as_one` already proves the probe-rows shape measures correctly.
        probe_rows = [
            {
                "title": "DO-1",
                "fields": [
                    {"label": "Customer", "value": "HANLIM TRADING SDN BHD [A/C I]"},
                    {"label": "Actual Delivery Date", "value": "2026-09-01"},
                ],
            },
            {
                "title": "SO-2",
                "fields": [
                    {"label": "Customer", "value": "GOLDEN WIN SDN BHD"},
                    {"label": "Actual Delivery Date", "value": None},
                ],
            },
        ]
        customer_bases = pickers.customer_bases_with_do({"answers": probe_rows})
        grouped = candidates_by_kind(gate, gate["compatible_entities"], customer_bases)
        customers = grouped.get("customer", [])
        stamped = {c.get("name"): c.get("stamp") for c in customers}
        assert stamped == {
            "HANLIM TRADING SDN BHD [A/C I]": "has DO",
            "GOLDEN WIN SDN BHD": "no DO",
        }, stamped

    def test_two_ledgers_of_one_family_share_the_same_stamp(self) -> None:
        """The has/no-DO stamp is keyed by FAMILY BASE (`pickers.customer_base` strips
        the `[A/C n]` suffix), same as finding 1's own grouping rule - a DO on ledger I
        is a DO for the family, and ledger II (no order of its own) reads it too."""
        from app.services.chatbot.lanes.business import pickers
        from app.services.chatbot.turn_runtime import candidates_by_kind

        gate = {"gate_clarification": "x"}
        compatible = [
            {
                "entity_type": "customer",
                "canonical_code": "300-H030",
                "uuid": "c2f38bdf-767a-4b04-b92d-1c0d56cfd4d3",
                "display_name": "HANLIM TRADING SDN BHD [A/C I]",
                "raw": "hanlim",
            },
            {
                "entity_type": "customer",
                "canonical_code": "300-H070",
                "uuid": "6f5a419b-840a-4e4d-8107-291971dd3bb8",
                "display_name": "HANLIM TRADING SDN BHD [A/C II]",
                "raw": "hanlim",
            },
        ]
        probe_rows = [
            {
                "title": "DO-1",
                "fields": [
                    {"label": "Customer", "value": "HANLIM TRADING SDN BHD [A/C I]"},
                    {"label": "Actual Delivery Date", "value": "2026-09-01"},
                ],
            },
        ]
        customer_bases = pickers.customer_bases_with_do({"answers": probe_rows})
        grouped = candidates_by_kind(gate, compatible, customer_bases)
        stamped = {c.get("name"): c.get("stamp") for c in grouped.get("customer", [])}
        assert stamped == {
            "HANLIM TRADING SDN BHD [A/C I]": "has DO",
            "HANLIM TRADING SDN BHD [A/C II]": "has DO",
        }, stamped

    def test_annotate_customer_stays_display_only_the_real_stamp_path_is_the_new_probe(
        self,
    ) -> None:
        """Guards against re-chasing the WRONG seam a second time: the old 2-arg
        `candidates_by_kind(gate, compatible)` call (no `customer_bases_with_do`)
        must produce NO stamp on a customer row even when `annotate_customer` ran
        over the same gate - the two paths are genuinely independent, not one
        silently reading the other's output."""
        from app.services.chatbot.lanes.business import pickers
        from app.services.chatbot.turn_runtime import candidates_by_kind

        gate = {
            "gate_clarification": "Which customer do you mean? Please choose:\n1. ACME SDN BHD",
            "compatible_entities": [
                {
                    "entity_type": "customer",
                    "canonical_code": "300-A1",
                    "uuid": "aaaaaaaa-0000-0000-0000-000000000001",
                    "display_name": "ACME SDN BHD",
                    "raw": "acme",
                },
            ],
        }
        probe = {
            "answers": [
                {
                    "title": "DO-1",
                    "fields": [
                        {"label": "Customer", "value": "ACME SDN BHD"},
                        {"label": "Actual Delivery Date", "value": "2026-09-01"},
                    ],
                }
            ]
        }
        annotated = pickers.annotate_customer(dict(gate), probe=probe, parser={})
        assert annotated["customer_probe_hits"] == 1, annotated["escalate_message"]

        # The OLD 2-arg call - customer_bases_with_do defaults to None, so the new
        # elif branch (turn_runtime.py's candidates_by_kind) never fires.
        grouped = candidates_by_kind(annotated, gate["compatible_entities"])
        customers = grouped.get("customer", [])
        assert all(c.get("stamp") is None for c in customers), (
            f"annotate_customer's own gate mutation must NOT leak a stamp through the "
            f"2-arg call: {customers!r}"
        )


class TestFinding12DocumentNamedSetsFocusAndDoesNotArmTheScopeAsk:
    """Ruling 12 (turn c45e2929, "Outstanding DO for 7445"): the document named in the
    message sets the scope; the question arms only when no document was named.

    Measured (tester 13, this session's predecessor): the GENERIC mechanism already
    works - `turn/apply.py`'s `_focus_rules` writes `focus.document` straight off
    `verdict["document"]` (`apply.py:593-596`) with no engine change needed, and
    `turn_runtime.lane_parse_output` already projects `outstanding_scope_ask_candidate`
    off `document` + `status` via `_DOCUMENT_STATUS_TO_ORDER_STATUS`
    (`turn_runtime.py:407-427`): a named document changes the projected bucket away
    from the bare `"outstanding"` key the candidate flag tests for, so the flag comes
    back False. Ported as a POSITIVE pin (not a guessed red) at the real
    `lane_parse_output` seam directly - the same seam `71109d8d3`'s commit body itself
    names ("No engine change: `_focus_rules` already writes `focus.document` from this
    key and the answering half already reads it"). If this ever goes red, the engine
    regressed, not the prompt."""

    def test_a_named_document_sets_focus_document(self) -> None:
        from app.services.chatbot.turn.state import Focus

        v = verdict(
            message_type="business_query",
            intent_hint="check_order_status",
            domain_hint="outstanding",
            status="outstanding",
            document=["DO"],
            entities=[
                {
                    "raw": "SRTWT7445",
                    "hint": "product",
                    "current_message": True,
                    "confident": True,
                }
            ],
        )
        state2, _plan, _branch = _decide(v, focus=Focus())
        assert state2.focus.document == ["DO"], (
            f"got {state2.focus.document!r} - turn/apply.py's _focus_rules should "
            "write focus.document straight off verdict['document']"
        )

    def test_a_named_document_does_not_arm_the_outstanding_scope_ask(self) -> None:
        from app.services.chatbot.turn_runtime import lane_parse_output

        named = lane_parse_output(
            verdict(status="outstanding", document=["DO"]), focus=None
        )
        assert named["order_status"] == "do_outstanding", named["order_status"]
        assert named["outstanding_scope_ask_candidate"] is False, (
            f"got {named['outstanding_scope_ask_candidate']!r} - a named document "
            "must move the projected bucket off the bare 'outstanding' key the "
            "scope-ask candidate flag tests for"
        )

        # Contrast case: no document named IS the one bucket the flag should arm for -
        # proves the assertion above is measuring the real branch, not a tautology.
        bare = lane_parse_output(verdict(status="outstanding", document=[]), focus=None)
        assert bare["order_status"] == "outstanding", bare["order_status"]
        assert bare["outstanding_scope_ask_candidate"] is True, bare[
            "outstanding_scope_ask_candidate"
        ]
