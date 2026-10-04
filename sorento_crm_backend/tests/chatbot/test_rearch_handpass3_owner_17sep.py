"""Owner hand pass 3 (AC-1593), 17 Sep 2026 17:31-17:38 MYT, 26-turn console chain,
contact 900000008 (source clone `sorento_ai_automation_rearch`). One unit red per
FINAL ruling row - owner go, coordinator relay, 17 Sep 2026 evening, the UAC's own
"Hand pass 3 rulings" block (`documentation/plans/chatbot/chatbot-turn-rearch-
acceptance-criteria.md`) - superseding the earlier proposed-ruling brief
(`hand-pass-3-17sep.md`) where the two disagree.

Full chain recorded as `replay_turns/console/handpass3-owner-17sep-*.json` (5 files,
the coordinator's own natural sub-chains). Those files stay OBSERVED (currently-broken)
behaviour, not hand-edited to the ruling - see their own `_note` header and
`test_turn_replay.py`'s import-time comment for why. The reds asserting the RULING
itself live here, against the real `turn/apply.py` / `turn/narrow.py` /
`turn_runtime.py` / `head/parser.py` / `chatbot_parser_prompt.py` seams directly - same
split hand pass 2's own `test_rearch_handpass2_owner_17sep.py` precedent used.

**Rows NOT covered here, time-boxed, flagged not silently dropped:**
- Row 2 ("the answer header names each customer family once; 'all' over a customer
  roster fetches every family's ledgers") and row 6 ("the outstanding detail list
  carries the same filter header as the summary") are COMPOSE/reply-text-level claims -
  the header is built from a live fetch's own rows, not a unit-testable pure function at
  the `turn/` layer this file's other rows use. Needs either a compose-level seam this
  session did not locate, or a full `engine.run_turn` fixture with seeded order/DO rows
  (the `test_turn_replay.py` replay files above are the closest thing to that, and they
  already carry the observed-broken text verbatim for a human or a future session to
  diff against once the fix lands).
- Row 4 (date window) is WITHDRAWN per the final ruling: "the parser returned the date
  window; only the header was wrong" - not a red, nothing to port.
- Row 9 (97fb7b49, "2" after the promo miss) is hand pass 2's own finding 8, already
  covered by `test_rearch_handpass2_owner_17sep.py::
  TestFinding8AMissAfterARosterPickKeepsTheRosterOpen` - not duplicated here.
- Row 3 ("a message naming an entity of a kind the roster is not about is a
  refinement... carry the roster, plan the message") is SUPERSEDED by the final
  ruling's own "Current subject" line and the `answers_open_question` retirement's
  "no match = question stays open, message runs as itself" rule - both covered below
  (`TestCurrentSubjectLine`, `TestAnswersOpenQuestionRetired`) rather than re-asserted
  as its own separate row, to avoid two reds pinning the same behaviour at two
  different, possibly-drifting shapes.
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


# --------------------------------------------------------------------------- #
# Coordinator relay item (2): "the parser's user block gains one line, 'Current
# subject', built from the focus every turn (domains, customers, products, document,
# status, date window), so refinements and domain switches are judged with context."
# --------------------------------------------------------------------------- #


class TestCurrentSubjectLine:
    def test_build_user_block_states_the_current_subject_from_focus(self) -> None:
        """Measured: `head/parser.py::build_user_block` has no `focus` parameter at
        all today (grepped this session) - the ONLY context it states is `pending_kind`
        / `pending_options`, never what the conversation is currently ABOUT. A refinement
        ("For srtwc286 only" under an open customer roster) and a domain switch both need
        this line to be judged against something, per the final ruling."""
        from app.services.chatbot.head.parser import build_user_block
        from app.services.chatbot.turn.state import Focus

        focus = Focus(
            domains=["order"],
            customers=[{"raw": "hanlim", "hint": "customer", "canonical_code": "300-H030", "current_message": False}],
            products=[{"raw": "SRTWC286", "hint": "product", "canonical_code": "SRTWC286", "current_message": False}],
            document=["DO"],
            status="outstanding",
            date_window={"start": "2026-09-01", "end": "2026-09-30"},
        )
        block = build_user_block(
            previous_response="x",
            latest_user_message="For srtwc286 only",
            pending_kind=None,
            focus=focus,
        )
        assert "Current subject:" in block, (
            f"no 'Current subject:' line in the rendered user block: {block!r} - "
            "build_user_block has no focus parameter to build it from"
        )
        for token in ("order", "hanlim", "SRTWC286", "DO", "outstanding", "2026-09-01"):
            assert token in block, f"{token!r} missing from the Current subject line: {block!r}"

    def test_build_user_block_omits_the_line_when_focus_is_empty(self) -> None:
        from app.services.chatbot.head.parser import build_user_block
        from app.services.chatbot.turn.state import Focus

        block = build_user_block(
            previous_response="x",
            latest_user_message="hello",
            pending_kind=None,
            focus=Focus(),
        )
        assert "Current subject:" not in block, (
            f"an EMPTY focus must render no Current subject line at all: {block!r}"
        )


# --------------------------------------------------------------------------- #
# Coordinator relay item (1): `answers_open_question` is RETIRED from the parser
# schema, the prompt and APPLY. A message answers the open question when a
# `reference_positions` entry OR a named entity matches an offered option by exact
# label, OR `is_affirmative` answers an offer, OR `broaden_axis` is "all"; otherwise
# the question stays open and the message runs as itself. Supersedes
# `test_rearch_s6_open_question_parser.py`'s S6-cluster-4 wording (that file's own
# two "declares the key" assertions below are INVERTED here to the new, opposite,
# claim - the file itself is left in place rather than deleted, per "no test is
# deleted by the coder/tester", but its schema+prompt assertions are now wrong on
# purpose until a coder retires the key, at which point THIS class's assertions
# become the live truth and that file's two need deleting, not this one's).
# --------------------------------------------------------------------------- #


class TestAnswersOpenQuestionRetired:
    def test_schema_no_longer_declares_answers_open_question(self) -> None:
        from app.services.chatbot.head.parser import DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA

        assert "answers_open_question" not in DECLARED_KEYS, (
            "answers_open_question must be retired from the required schema keys - "
            f"still present: {sorted(DECLARED_KEYS)!r}"
        )
        assert "answers_open_question" not in PARSE_OUTPUT_JSON_SCHEMA.get("properties", {}), (
            "answers_open_question must be retired from the schema's own properties too"
        )

    def test_prompt_no_longer_documents_answers_open_question(self) -> None:
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert "answers_open_question" not in SEMANTIC_PARSER_PROMPT, (
            "the fallback prompt must stop instructing the model to emit a key APPLY "
            "no longer reads"
        )

    def test_a_named_entity_matching_an_offered_option_by_exact_label_answers_the_pending(
        self,
    ) -> None:
        """NEW matching mode the final ruling adds beyond what `_picked_positions`
        already reads today (`reference_positions`, `broaden_axis == 'all'`): a named
        entity whose raw/canonical text matches an offered option's own label exactly.
        Measured: `turn/apply.py::_picked_positions` has no entity-vs-label comparison
        anywhere in its body today - only `answers_open_question`, `reference_positions`
        and `broaden_axis`. A PLAIN roster kind (`product_pick`, not one of
        `OUTSTANDING_KINDS`/`ESCALATION_OFFER_KINDS`) so neither `_answer_outstanding`'s
        own "entities present = new ask" early exit nor `_answer_offer`'s explicit-
        verdict gate intercepts this before `_picked_positions` ever runs - measured
        directly (an earlier draft of this test used `outstanding_scope` and passed
        VACUOUSLY, for `_answer_outstanding`'s unrelated "new ask" rule, not for a
        label match at all)."""
        options = [
            {"position": 1, "label": "SRTWC286-SH", "entity_type": "product", "uuid": "0d0ed752-fd6f-4759-ad8f-0b40e0cbc601"},
            {"position": 2, "label": "SRTWC286-SH-150", "entity_type": "product", "uuid": "78c96bfa-edea-4695-a215-4a02cc0c53a0"},
        ]
        pending = _roster("product_pick", options, domain="stock")
        v = verdict(
            message_type="casual",
            entities=[{"raw": "SRTWC286-SH-150", "hint": "product", "canonical_code": "SRTWC286-SH-150", "current_message": True, "confident": True}],
            reference_positions=[],
            answers_open_question=None,
        )
        state2, plan, branch = _decide(v, pending=pending)
        answered = (
            state2.pending.answered_positions if state2.pending is not None else None
        )
        assert answered == [2], (
            "a named entity exactly matching option 2's label ('SRTWC286-SH-150') must "
            f"answer position 2 of the roster - got pending={state2.pending!r}"
        )

    def test_no_match_at_all_leaves_the_question_open_and_runs_the_message_as_itself(
        self,
    ) -> None:
        """"no match = question stays open, message runs as itself" - a business ask
        naming its own domain, with nothing matching the roster's labels, no
        reference_positions and no broaden_axis=all, must plan for what it IS (own
        domain reaches the plan) while the pending survives untouched. This is the
        SAME outcome `test_rearch_s6_open_question_parser.py`'s own
        `TestAbsentAnswerCarriesThePendingWithoutReprinting` class already proves via
        the OLD `answers_open_question=None` shape - restated here without that key at
        all, so it stays true once the key is deleted from `verdict()`'s own defaults."""
        options = [
            {"position": 1, "label": "Sales orders", "entity_type": "document", "payload": {"value": "so"}},
            {"position": 2, "label": "Delivery orders", "entity_type": "document", "payload": {"value": "do"}},
        ]
        pending = _roster("outstanding_scope", options, domain="outstanding")
        v = verdict(
            message_type="business_query",
            domain_hint="promotion",
            entities=[],
            reference_positions=[],
            answers_open_question=None,
        )
        state2, plan, branch = _decide(v, pending=pending)
        assert "promotion" in plan.domains, plan.domains
        assert state2.pending is not None, "the pending must survive, sticky, never dropped by a non-match"


# --------------------------------------------------------------------------- #
# Row 1 (turns 9b5e241e "Promo for srtwc286", c7cb01fc "1"): a pick settles ONLY the
# kind it picked; every other carried entity stays on the fetch.
# --------------------------------------------------------------------------- #


class TestRow1APickSettlesOnlyItsOwnKindOtherCarriedEntitiesStay:
    """Measured this session, corrected from the row's first-draft hypothesis (a
    tier pick clearing `focus.products` at the roster-answer branch) - it does NOT:
    `_answer_pending`'s roster branch only ever calls `_set_kind_field` for the
    PICKED kind, never touches any other Focus field, so `focus.products` provably
    survives already (positive pin below). The real end-to-end symptom (the owner's
    own "Fetch envelope for crm_marketing_promotions_list carried entities []")
    traces one level deeper: `_set_kind_field(focus, "tier", built)` builds `built`
    from `option["uuid"]`/`option["uuids"]` ONLY (`_answer_pending` ~line 443), and a
    `tier_pick` option naturally carries neither (a tier is not an entity with a
    UUID) - so `built` stays empty and `focus.tier` is NEVER set from a tier pick,
    confirmed directly (`trace.narrowing == ['promotion.tier:narrow_by_tier']`,
    `plan.fetch == []`: the promotion domain's own tier-narrowing policy re-arms
    instead of fetching, because the tier it is waiting on never lands). This IS
    hand pass 2's own Finding 9 (`test_rearch_handpass2_owner_17sep.py::
    TestFinding9ATierPickSetsFocusTier`, still red, not duplicated here) - Row 1 and
    Finding 9 are the SAME defect, not two."""

    def test_answering_a_tier_pick_does_not_drop_the_carried_product(self) -> None:
        """POSITIVE pin (not a red): proves the half of Row 1 that already works -
        the OTHER carried entity (the product) is untouched by a roster answer,
        confirming the owner's symptom is entirely explained by Finding 9's tier gap
        above, not by a second, separate "drops other entities" defect."""
        from app.services.chatbot.turn.state import Focus

        carried = Focus(
            products=[{"raw": "SRTWC286", "hint": "product", "canonical_code": "SRTWC286", "current_message": False}],
            domains=["promotion"],
        )
        options = [
            {"position": 1, "label": "dealer", "entity_type": "tier", "payload": {"value": "dealer"}},
            {"position": 2, "label": "office", "entity_type": "tier", "payload": {"value": "office"}},
            {"position": 3, "label": "end user", "entity_type": "tier", "payload": {"value": "end_user"}},
        ]
        pending = _roster("tier_pick", options, domain="promotion")
        v = verdict(message_type="casual", reference_positions=[1], answers_open_question=None)
        state2, plan, branch = _decide(v, pending=pending, focus=carried)
        assert state2.focus.products, (
            "if this now comes back empty, a SECOND defect (products dropped) has "
            f"appeared alongside Finding 9's tier gap - got {state2.focus.products!r}"
        )
        assert "SRTWC286" in [p.get("canonical_code") or p.get("raw") for p in state2.focus.products]

    def test_the_end_to_end_symptom_is_finding_9s_tier_gap_not_a_dropped_product(self) -> None:
        """RED: the owner's own symptom, reproduced whole - a tier pick over a
        product-scoped promotion ask must reach a FETCH (promotion, with the product
        still attached and the tier applied), not re-arm the same tier question.
        Fails today via Finding 9's gap (`focus.tier` never lands), confirming the
        two rows are one defect, fixed once."""
        from app.services.chatbot.turn.state import Focus

        carried = Focus(
            products=[{"raw": "SRTWC286", "hint": "product", "canonical_code": "SRTWC286", "current_message": False}],
            domains=["promotion"],
        )
        options = [
            {"position": 1, "label": "dealer", "entity_type": "tier", "payload": {"value": "dealer"}},
        ]
        pending = _roster("tier_pick", options, domain="promotion")
        v = verdict(message_type="casual", reference_positions=[1], answers_open_question=None)
        state2, plan, branch = _decide(v, pending=pending, focus=carried)
        assert plan.fetch, (
            "a tier pick over a product-scoped promo ask must reach a fetch, not "
            f"re-arm the tier question - plan.fetch={plan.fetch!r}, "
            f"rules_fired={plan.trace.rules_fired!r}, narrowing={plan.trace.narrowing!r}"
        )


# --------------------------------------------------------------------------- #
# Row 5 (turn bb233665, "Sales order" after the DO list): a document named in the
# message is a new scope while ANY outstanding question is open - not only the scope
# question itself.
# --------------------------------------------------------------------------- #


class TestRow5ADocumentNamedIsANewScopeWhileAnyOutstandingIsOpen:
    def test_a_named_document_over_an_open_detail_offer_is_a_new_ask_not_a_reprint(
        self,
    ) -> None:
        """Measured: `turn/apply.py::_answer_outstanding` only resolves a bare
        `document` word into `scope` when `pending.kind == 'outstanding_scope'`
        (~line 367) - the OTHER outstanding kind, `outstanding_detail` (the "after '1'
        SO list, typing '2' gives the DO list" offer), has no such check at all. A
        turn that names 'Sales order' with no entities and no reference_positions,
        over an OPEN `outstanding_detail` pending, therefore falls through
        `scope is None` at line ~374, returns None from `_answer_outstanding`, and
        `_answer_pending`'s generic `resolved is False` / unresolved path re-prints
        the SAME detail offer - exactly the owner's own repro ("Reply 1 for the
        delivery order list" re-printed after typing "Sales order")."""
        pending = _roster(
            "outstanding_detail",
            [{"position": 1, "label": "Delivery order list", "entity_type": "document", "payload": {"value": "do"}}],
            domain="outstanding",
        )
        v = verdict(
            message_type="business_query",
            document=["SO"],
            entities=[],
            reference_positions=[],
        )
        state2, plan, branch = _decide(v, pending=pending)
        assert state2.pending is None or state2.pending.kind != "outstanding_detail", (
            "a named document over an open outstanding_detail offer must be treated as "
            f"a NEW SCOPE (the offer dropped, the turn planned for the named document), "
            f"not carried/re-printed unchanged - got pending={state2.pending!r}"
        )
        assert "outstanding_pending_dropped" in plan.trace.rules_fired or "so" in str(state2.focus.document).lower(), (
            f"expected the document-named-new-scope rule to fire - trace={plan.trace.rules_fired!r}, "
            f"focus.document={state2.focus.document!r}"
        )


# --------------------------------------------------------------------------- #
# Row 7 (turn fb8b32cb, "PO for this"): rosters under promotion and purchase_order
# carry stamps (has promo / no promo, has PO / no PO) from the same probe seam as
# incoming and DO; the PO picker stays.
# --------------------------------------------------------------------------- #


class TestRow7RostersUnderPromotionAndPurchaseOrderCarryStamps:
    def test_candidates_by_kind_carries_the_promo_or_po_stamp_via_the_fourth_argument(
        self,
    ) -> None:
        """LANDED (`bce871c9c`): the same line a product carries under incoming now
        reaches the promotion / purchase_order rosters too, through a NEW fourth
        positional argument, `product_stamp: tuple[set[str], str, str] | None` -
        `(codes measured, has_label, no_label)`, read AFTER the `incoming_by_code`
        branch and only for `kind == "product"` (`turn_runtime.py::candidates_by_kind`,
        `_norm_code`-compared). Re-pinned POSITIVE per the coordinator's 17 Sep
        addendum - the prior "no stamp branch exists" pin passed vacuously once the
        fix landed, because its 2-positional-argument call has no way to reach the new
        parameter at all; this call exercises it directly, one code that matches the
        probed set and one that does not, so a revert of the fourth argument (or its
        `_norm_code` comparison) fails this test again."""
        from app.services.chatbot.turn_runtime import candidates_by_kind

        gate = {"gate_clarification": "x"}
        compatible = [
            {
                "entity_type": "product",
                "canonical_code": "SRTWC286-SH",
                "uuid": "0d0ed752-fd6f-4759-ad8f-0b40e0cbc601",
                "raw": "SRTWC286-SH",
            },
            {
                "entity_type": "product",
                "canonical_code": "SRTWT7445-LV",
                "uuid": "1a1ed752-fd6f-4759-ad8f-0b40e0cbc602",
                "raw": "SRTWT7445-LV",
            },
        ]
        grouped = candidates_by_kind(
            gate,
            compatible,
            None,
            ({"srtwc286-sh"}, "has promo", "no promo"),
        )
        products = {p["canonical_code"]: p for p in grouped.get("product", [])}
        assert products["SRTWC286-SH"]["stamp"] == "has promo", products
        assert products["SRTWT7445-LV"]["stamp"] == "no promo", products


# --------------------------------------------------------------------------- #
# Row 8 (turn f82ee09a "Eight" vs 9a245e43 "The eigthh one"): word numbers are
# positions; the prompt says so.
# --------------------------------------------------------------------------- #


class TestRow8WordNumbersArePositions:
    def test_prompt_documents_a_bare_cardinal_word_as_a_position(self) -> None:
        """Measured: `chatbot_parser_prompt.py`'s own "AN OPEN NUMBERED QUESTION..."
        and "POSITIONAL REFERENCES" sections document numerals ("2"), ordinal-suffixed
        words ("the 3rd one", "the last one") and label paraphrases ("gimme the list
        plss") as valid answers to an open numbered question - grepped this session,
        no bare CARDINAL word ("eight", "one", "two", ...) appears anywhere in the
        file (only substring false-positives inside "weight"/"height"). This
        reproduces the owner's own pair: "Eight" (bare cardinal) re-printed the
        clarify menu; "The eigthh one" (ordinal-shaped, matching the documented "the
        Nth one" pattern) worked."""
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        lower = SEMANTIC_PARSER_PROMPT.lower()
        has_cardinal_word_example = any(
            f'"{word}"' in lower or f"'{word}'" in lower
            for word in ("eight", "one", "two", "three", "four", "five", "six", "seven", "nine", "ten")
        )
        assert has_cardinal_word_example, (
            "no bare cardinal word number ('eight', 'one', ...) is documented anywhere "
            "as a valid position reference - only numerals and ordinal-suffixed forms"
        )
