"""Registered divergences between the Python port and the captured n8n fixtures.

AC-005. A replayed fixture whose output disagrees with `expected` FAILS, unless the
pair `(node, fixture)` is listed here with the hazard id it belongs to and a one-line
reason. That is how "parity before improvement" (D8) is enforced mechanically rather
than by reviewer memory: a hazard fix has to be written down as a divergence before it
can turn a red replay green, and an accidental behaviour change has nowhere to hide.

`node` is the fixture directory name (`route-turn`, `output_exchange`, ...), NOT the
Python module. `fixture` is the file stem. A `None` fixture registers the divergence for
every fixture of that node - use it only for a hazard that genuinely changes the node's
whole contract, and say so in the reason.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Divergence:
    node: str
    fixture: str | None
    hazard: str
    reason: str
    # A FIELD-scoped divergence: these paths are removed from both sides before the
    # comparison, and everything else must still be byte-equal. A blanket entry (empty
    # tuple) passes the whole fixture, which is right for a hazard that changes a node's
    # whole contract and WRONG for one that adds a key - the blanket form would make the
    # gate vacuous for that node forever. `path` is walked inside each item's `json`.
    strip_paths: tuple[tuple[str, ...], ...] = ()


# The five owner-ruling-K captures whose re-prompt turn named no domain and no entities,
# so the SUBJECT carry (AC-816 rule 1, 6 Sep 2026) moves them on those two fields as well
# as on the three the whole group moves on. Measured, one capture at a time: the other
# eight in the group are byte-equal on `entities` and `domain_hint`.
_SUBJECT_CARRY_MOVES = frozenset(
    {
        "rs34-02-escalation",
        "rs51-03-escalation",
        "rs6-05-escalation",
        "exec-14123374",
        "out-15143898",
    }
)


# Every entry here must name a hazard id or an owner decision from the plan, and be
# traceable to an AC. S1 shipped with none; S2 adds two - AC-202 authorises the `pending`
# marker, and AC-205 is the H29 fix, whose only capture records the DEFECT.
DIVERGENCES: list[Divergence] = [
    Divergence(
        node="compile-current-state",
        fixture="b56-roster-turn",
        hazard="H29 (AC-205)",
        reason=(
            "born roster beats carried picker. The capture IS the defect: on clone exec "
            "14400735 the turn rendered a nine-member CS roster and persisted the "
            "PREVIOUS turn's three-row customer picker with selection_context "
            "'disambiguation', so the next '1' re-ran the order query and the escalation "
            "was dropped. The body the export ships carries the fix "
            "(`_cpOfferBornThisTurn`); this capture predates it. Pinned by "
            "tests/chatbot/test_tail_units.py::TestBornRosterWins."
        ),
    ),
    # ------------------------------------------------------------------ #
    # OWNER RULING K, rule 1 (6 Sep 2026): an offer roster lives until the topic
    # changes. Thirteen captures record the OLD lifetime, in which the ladder
    # recomputed `selection_context` from THIS turn's outcome alone, so a turn
    # that built no offer of its own wrote `null` plus the ANSWER's own rows -
    # and the customer's second pick ("2" after "1") had nothing to resolve
    # against. `_offer_carry` now keeps the previous label and roster on exactly
    # those turns.
    #
    # This is the owner REVERSING a decision, not a port defect: the old n8n
    # spine's own `compile-current-state.js` carries the same `... || null` reset
    # the port reproduced, so no capture can show the new behaviour and every
    # capture of the shape shows the old one. Pinned by
    # test_tail_units.py::TestTierAndPromoOffersCarryUntilOverwritten and
    # ::TestTheMemberOfferCarryStopsAtTheAnswer.
    #
    # FIELD-SCOPED, deliberately: only the two fields the rule moves come off,
    # in both the sealed and the unwrapped shape, plus the `pending` marker the
    # blanket H13/H14 entry below would otherwise have handled (a per-fixture
    # entry wins the `find` lookup, so it has to carry that strip itself).
    # Every other byte of these thirteen captures is still graded.
    *(
        Divergence(
            node="compile-current-state",
            fixture=name,
            hazard="owner ruling K rule 1 (AC-816)",
            reason=(
                "the capture records the pre-ruling lifetime: the offer roster died the "
                "moment a turn built no offer of its own, so a sequential pick had no "
                "list to resolve against. Owner-ruled on 6 Sep 2026; the old spine's own "
                "compile-current-state.js has the same reset, so this is a new rule, not "
                "a port bug."
            ),
            strip_paths=(
                ("reply", "session_patch", "variables", "selection_context"),
                ("reply", "session_patch", "variables", "last_result_set"),
                ("reply", "session_patch", "variables", "pending"),
                ("variables", "selection_context"),
                ("variables", "last_result_set"),
                ("variables", "pending"),
            )
            # THE SUBJECT CARRY (prod exec 15445325, same ruling, 6 Sep 2026). A carried
            # offer now takes the domain and the entities it was made ABOUT with it, so
            # the five captures where the re-prompt turn named neither also move on those
            # two fields. Added per NAME, not to the whole group: the other eight are
            # byte-equal on `entities` and `domain_hint` and go on being graded there,
            # which is the difference between a field-scoped divergence and a blanket one.
            + (
                (
                    ("reply", "session_patch", "variables", "domain_hint"),
                    ("reply", "session_patch", "variables", "entities"),
                    ("variables", "domain_hint"),
                    ("variables", "entities"),
                )
                if name in _SUBJECT_CARRY_MOVES
                else ()
            ),
        )
        for name in (
            "rs34-02-escalation",
            "rs34-05-promopicker",
            "rs51-02-promoattach",
            "rs51-03-escalation",
            "rs6-04-promopicker",
            "rs6-05-escalation",
            "s57-t1",
            "s57-t2",
            "exec-14001191",
            "exec-14119800",
            "exec-14123374",
            "exec-14126915",
            "out-15143898",
        )
    ),
    Divergence(
        node="compile-current-state",
        fixture=None,
        hazard="H13/H14 (R3)",
        reason=(
            "the port writes the `pending` marker the JS had no equivalent of, so the "
            "next turn can ask 'is an escalation offer open?' of state instead of of the "
            "bot's own previous words (D11). Field-scoped: every other byte of the "
            "session patch is still compared, and AC-203's own test asserts the marker."
        ),
        strip_paths=(
            ("reply", "session_patch", "variables", "pending"),  # the shipping seal
            ("variables", "pending"),  # a pre-RS-3 capture, unwrapped by the runner
        ),
    ),
    # ------------------------------------------------------------------ #
    # S6c.
    # ------------------------------------------------------------------ #
    Divergence(
        node="output-structurer",
        fixture="gr-15145805",
        hazard="H63 (owner console pass, 6 Sep 2026) + E2/E3 (AC-1316/AC-1317)",
        reason=(
            "the multi-company 'which company came back empty' line is axis-labelled now "
            "('no incoming stock records found for product A, B and C') instead of a bare "
            "comma list of raw entity codes, because the un-labelled form printed an "
            "internal debtor code and one alias row per customer alongside the products, "
            "as four separate things that had been searched. Owner ruling: label it. This "
            "is the ONE graded output-structurer capture that reaches the block (four "
            "products, one silent company); field-scoped to `response`. `set_header` is "
            "the SAME turn re-architecture addition the blanket entry below names - "
            "added here too because this fixture has its own entry and `find()` returns "
            "only the first match. Every other key of the envelope is still compared byte "
            "for byte. Pinned by "
            "tests/chatbot/test_s6b_fetch_lane.py::"
            "TestLabelledNotFoundLineNeverLeaksInternalDebtorCode."
        ),
        strip_paths=(("response",), ("set_header",), ("set_described",)),
    ),
    # E2/E3 (attribute-first asks, AC-1316/AC-1317, `lanes/business/fetch.py::
    # output_structurer`): a HAS turn's set-answer header now travels out as its OWN
    # `set_header` key, not only baked into `response` - the turn re-architecture's
    # `turn/compose.py` renders its own per-row grammar from `figures`/`entities` rather
    # than reusing this arm's `response` string wholesale, so a counted-set answer needs
    # a header of its own to prepend. No n8n capture emits this key (it did not exist
    # when captured); present (a string) on a predicate-bearing answer, `null` otherwise
    # - either way the key itself is new. Blanket, not per-fixture: measured across every
    # failing `output-structurer` capture before this entry, `set_header` is the ONLY
    # key that ever differs (bar `gr-15145805` above, which also carries H63). Pinned by
    # `output_structurer`'s own E2/E3 comment.
    Divergence(
        node="output-structurer",
        fixture=None,
        hazard="E2/E3 (AC-1316/AC-1317, set_header)",
        reason=(
            "output_structurer now also returns `set_header`, a HAS turn's counted-set "
            "header as its own key so a composer with no `response` string to reuse can "
            "still prepend it. No n8n capture emits this key. `set_described` (integration "
            "round 11 on PR #833) rides beside it: whether that header names a described set."
        ),
        strip_paths=(("set_header",), ("set_described",)),
    ),
    Divergence(
        node="crossdomain-render",
        fixture="exec-14131197",
        hazard="H62 (owner console pass, 6 Sep 2026)",
        reason=(
            "'positive facts only' dropped a requested code that came back empty on BOTH "
            "sides, so the reply named only the codes that had something to show. Owner "
            "ruling: name it ('no stock and no incoming') and offer the escalation. This "
            "capture is that case exactly - one probed missing code, zero probed rows - so "
            "the port emits the negative paragraph where n8n emitted nothing. Field-scoped "
            "to the two keys that carry it; every other byte is still compared. (The "
            "sibling one-sided line, AC-820, later moved five more captures on `block` "
            "alone - registered below.) Pinned by "
            "tests/chatbot/test_s6c_answer_lane.py::"
            "TestThirdCodeWithNoStockAndNoIncomingIsNamedWithEscalation."
        ),
        strip_paths=(
            ("_xdBlock", "block"),
            ("_xdBlock", "any"),
            # A7 (chatbot-growth-r1): new diagnostic keys, see the blanket entry at the
            # bottom of this file for the full reason.
            ("_xdBlock", "nothing_codes"),
            ("_xdBlock", "nothing_note"),
            ("_xdBlock", "nothing_missing"),
            # 11 Sep 2026, second ruling (R2): one more new diagnostic key, same class.
            ("_xdBlock", "zero_codes"),
        ),
    ),
    # OWNER CONSOLE PASS 4, item G (6 Sep 2026): a requested code the PRIMARY domain
    # answered with nothing, and the OTHER one answered with something, is now named
    # ("No stock for MSK11A-QT.") above the cross-domain lead instead of appearing only
    # inside it. Turn 858c9c54: a stock question came back as two codes' stock followed
    # by an incoming fact about a third, with nothing saying the third had no stock.
    #
    # Not fixture-visible by construction - n8n's `crossdomain-render` has no such line,
    # so every capture of the shape records its absence. Six graded captures carry it
    # and the ONLY difference on each is the prefixed sentence (measured, one at a
    # time), so the divergence is `_xdBlock.block` alone: `any`, `attachments`, `team`,
    # `origin`, `probed_rows`, `rendered_rows` and the whole envelope passthrough are
    # still compared byte for byte. `exec-14126915`'s ETA sort is graded explicitly in
    # `test_s6c_engine_paths.py::TestCrossdomainRenderEtaOnlyCaptureReplays` rather than
    # left to this strip. Pinned by
    # test_s6c_answer_lane.py::TestAZeroStockCodeIsNamedBeforeTheIncomingBlock.
    *(
        Divergence(
            node="crossdomain-render",
            fixture=name,
            hazard="owner console pass 4, item G (6 Sep 2026)",
            reason=(
                "the primary domain's own miss is stated above the cross-domain block "
                "('No stock for <code>.'), where n8n said nothing at all. Field-scoped to "
                "`_xdBlock.block`; every other key of the render is still compared."
            ),
            strip_paths=(
                ("_xdBlock", "block"),
                # A7 (chatbot-growth-r1): new diagnostic keys, see the blanket entry at
                # the bottom of this file for the full reason.
                ("_xdBlock", "nothing_codes"),
                ("_xdBlock", "nothing_note"),
                ("_xdBlock", "nothing_missing"),
                # 11 Sep 2026, second ruling (R2): one more new diagnostic key, same class.
                ("_xdBlock", "zero_codes"),
            ),
        )
        for name in (
            "exec-13484326",
            "exec-13488926",
            "exec-14119800",
            "exec-14120400",
            "exec-14122546",
            "exec-14126915",
        )
    ),
    Divergence(
        node="build-suggest-offer",
        fixture="exec-14001140",
        hazard="D4 (AC-607) - the offer's identity is the TURN id",
        reason=(
            "the did-you-mean offer stamps `$execution.id` as its identity, and in the CRM "
            "that identity is the turn id. The offer only has to be stable WITHIN the "
            "session, so the successor is correct, but the value can never equal a captured "
            "n8n execution id - the same permanent difference already registered for the "
            "world replay as `worlds.WORLD_DROP_PATHS = (('dym_offer', 'id'),)`. This is "
            "the ONE graded `build-suggest-offer` capture whose only disagreement is that "
            "id (measured: 19 of 19 equal once the turn id is supplied), so the entry is "
            "per-fixture rather than node-wide - a node-wide one would pass every other "
            "capture unread."
        ),
        strip_paths=((("dym_offer", "id")),),
    ),
    *(
        Divergence(
            node="dym-transform",
            fixture=name,
            hazard="capture predates the body (S6a's CAPTURE_BODY_ADDITIONS class)",
            reason=(
                "the LIVE SPINE ships a STALE inline copy of `dym-transform` (421 lines, "
                "pre-Fix-4) while `sub-miss-suggest-live@f42de9c6` ships the 561-line body "
                "this port was made from and the 33 `sub-miss-suggest-live` captures were "
                "graded against. The three keys Fix 4 added - `dym_candidate_uuids`, "
                "`dym_probe_row_keys`, `probe_uuid_keyed` - do not exist in the older body, "
                "so this capture's `expected` cannot carry them. Measured: they are the "
                "ONLY disagreement on all three of these captures, and every other key is "
                "byte-equal. Retire the entry when the spine's inline copy is re-captured "
                "against the shipping body."
            ),
            strip_paths=(
                ("dym_candidate_uuids",),
                ("dym_probe_row_keys",),
                ("probe_uuid_keyed",),
            ),
        )
        for name in ("exec-13462354", "exec-13469053", "exec-13479632")
    ),
    Divergence(
        node="dym-annotate",
        fixture="exec-13469053",
        hazard="capture predates the body (S6a's CAPTURE_BODY_ADDITIONS class)",
        reason=(
            "the same stale-spine pair as the three `dym-transform` entries above: the live "
            "spine's inline `dym-annotate` is 169 lines (pre-Fix-4 / F1 / F8) while "
            "`sub-miss-suggest-live@f42de9c6` ships the 247-line body this port was made "
            "from. The older body emits neither `dym_ambiguous_codes` nor "
            "`dym_ambiguous_uuids` and stamps no `key_mode` on `dym_probe_meta`. Measured: "
            "those three keys are the ONLY disagreement, and the other 15 graded "
            "`dym-annotate` captures are byte-equal once the node's two by-name upstreams "
            "are supplied. Retire the entry when the spine's inline copy is re-captured."
        ),
        strip_paths=(
            ("dym_ambiguous_codes",),
            ("dym_ambiguous_uuids",),
            ("dym_probe_meta", "key_mode"),
        ),
    ),
    # ------------------------------------------------------------------ #
    # ISSUE #750 (owner, 8 Sep 2026): the `product_attachment` pickers stamp
    # "- has Product Photos" / "- no Product Photos" per line. The annotator keys
    # that probe PER UUID (Fix 4 / Fix 5, because one product code can belong to
    # two companies) and every render prints CODES, so `dym-annotate` now carries
    # the planner's own `(uuid, code, company)` map for the composer to translate
    # with, plus the resolved type's name for the noun. Neither key can be
    # fixture-visible: n8n's own body emits neither, so every capture of this node
    # records their absence by construction.
    #
    # Measured over the whole graded corpus for this node (16 captures): exactly 5
    # emit either key, they are exactly the 5 that now differ, and on all 5 the
    # ADDITION is the only disagreement - every other key is byte-equal. The other
    # 11 are untouched (10 equal, `exec-13469053` its own pre-existing entry), and
    # `build-suggest-offer`'s own captures stay byte-equal because that node strips
    # both keys again with the rest of `_DYM_CTRL_KEYS`. Field-scoped, so a real
    # change to `dym_available_codes`, `dym_probe_meta` or the passed-through
    # not-found payload still fails here. Pinned by
    # tests/chatbot/test_product_attachment_picker_stamp.py.
    *(
        Divergence(
            node="dym-annotate",
            fixture=name,
            hazard="issue #750 (product_attachment picker stamp, 8 Sep 2026)",
            reason=(
                "the node carries `dym_probe_row_keys` (the uuid-to-code map the "
                "renders need, since this domain is probed per uuid) and "
                "`dym_probe_type_name` (the resolved attachment type, so the line "
                "reads 'Product Photos' rather than the customer's own word). Live "
                "emits neither. Field-scoped to those two keys."
            ),
            strip_paths=(
                ("dym_probe_row_keys",),
                ("dym_probe_type_name",),
            ),
        )
        for name in (
            "ms-14992829",
            "ms-14993042",
            "ms-15144245",
            "ms-15151889",
            "ms-15157100",
        )
    ),
    # ------------------------------------------------------------------ #
    # OWNER RULING K, rule 4 (6 Sep 2026): a BARE entity turn is typed by the
    # carried domain, not by the model's guess at the token's shape. Four
    # captures show the old typing, and all THREE are one class: a
    # product-shaped code under a carried `order` thread, retyped `customer`
    # (`srtwc286`, `CSK11A`, `ib2700ss rta`). The retype only changes which type
    # the resolver tries FIRST - `resolve_entity_body` sends
    # `fallback_to_all_types: true`, which is the "the resolver then decides"
    # half of the ruling - so no answer is closed off by it. The cost is named
    # rather than hidden: those three reach the resolver as customers first, and
    # unfolded, because the separator fold in `_token_of` is product-hint-only.
    #
    # A FOURTH capture was registered here on the first pass and is now graded
    # again: `parser-15129616` is a positional pick ("17" against a numbered
    # list of orders) that qualified as bare only because the fork's token test
    # asks whether the raw CONTAINS the token, and "17" is inside `M2609-0173`.
    # It was a defect wearing a ruling's clothes. `_message_is_only_these_entities`
    # now matches by equality against the raw's own tokens and an entity carrying
    # an `ordinal` is never bare, so the capture is byte-equal.
    #
    # This is a deliberate divergence from the LIVE parser body: the hunk it
    # comes from (`_bareEntityTurn`) exists only on the unpromoted
    # `sub-semantic-parser-FORK`, and even there it does not RETYPE - the retype
    # is the owner's addition. Pinned by
    # test_output_exchange_rules.py::test_a_bare_entity_inherits_the_carried_domain_and_is_retyped_by_it
    # and its `order` twin, with the resolve-time guard in
    # test_resolve_gate_unit.py::TestBareEntityInheritanceIsBlockedAtResolveTime.
    *(
        Divergence(
            node="output_exchange",
            fixture=name,
            hazard="owner ruling K rule 4 (AC-816)",
            reason=(
                "the capture records the model's own hint for a bare entity under a "
                "carried business domain; the port types it by the domain and lets the "
                "resolver decide. Field-scoped to the entity list."
            ),
            strip_paths=(
                ("output", "entities"),
                ("output", "bare_entity_retyped"),
            ),
        )
        for name in (
            "s57-ok-parser",
            "parser-15101983",
            "parser-15115339",
        )
    ),
    # OWNER RULING K, rule 2 (6 Sep 2026): carried entities die on a topic
    # change. TWO captures in the whole corpus change their entity list, and
    # both are the shape the rule exists for - an explicit new query, in a
    # different domain, bringing its own scope, with a carried entity from the
    # old subject still narrowing it (`customer_order:M2609-0086` on an incoming
    # question, `category:faucets for bath tub` on a stock question). Everything
    # the domain blocklist already removed it still removes: this pass runs
    # AFTER it and only sees what the blocklist cannot, which is why the other
    # captures of the shape are byte-equal.
    *(
        Divergence(
            node="output_exchange",
            fixture=name,
            hazard="owner ruling K rule 2 (AC-816)",
            reason=(
                "the capture keeps an entity carried from the previous subject into an "
                "explicit new-domain query. Owner-ruled to die with the topic (H66). "
                "Field-scoped to the entity list."
            ),
            strip_paths=(
                ("output", "entities"),
                ("output", "entities_dropped_on_topic_change"),
            ),
        )
        for name in ("parser-15124806", "parser-15151771")
    ),
    # OWNER RULING B, console pass 3 (6 Sep 2026): a did-you-mean pick stamps
    # `entity_op: "replace"` where the JS stamped `"replace_combine"`. The four
    # captures below are every graded `output_exchange` capture in the corpus that
    # reaches `applyDymPick`, and on every one of them the ENTITY LIST is byte-equal
    # - measured, one capture at a time. That is the point: `applyDymPick` already
    # folds every prior entity it keeps into the returned list with
    # `current_message: true`, so the executor's axis-wise `kept_prior` had nothing
    # left to add on these turns and the two ops produce the same scope. They stop
    # agreeing on the turn the ruling is about, where the pick's candidate type
    # differs from the source token's hint and `replace_combine` puts the replaced
    # token back. Field-scoped to the op and its diagnostic; the entities, the
    # domain, the dates and everything else still grade byte for byte. Pinned by
    # test_output_exchange_rules.py::TestOwnerRulingBAllOfThemOverPendingDymOffer
    # and the real two-turn chain in
    # test_r3_pending_end_to_end.py::TestAllOfThemOverADidYouMeanOfferAnswersEveryOfferedCode.
    *(
        Divergence(
            node="output_exchange",
            fixture=name,
            hazard="owner ruling B (console pass 3, 6 Sep 2026)",
            reason=(
                "a did-you-mean pick names its op `replace`, not `replace_combine` - the "
                "picks ARE the scope. Field-scoped to the op; the entity list this "
                "capture records is byte-equal either way."
            ),
            strip_paths=(
                ("output", "entity_op"),
                ("output", "entity_op_applied"),
            ),
        )
        for name in (
            "parser-15118060",
            "parser-15136058",
            "parser-15143320",
            "parser-15143474",
        )
    ),
    # AC-1526 (turn re-architecture): `pickers.annotate_incoming` now also stamps
    # `incoming_by_code` - a per-code has/no-incoming boolean built off the SAME probe
    # answers the rendered "- has incoming"/"- no incoming" message already used, kept
    # as DATA rather than re-parsed out of the message string so the turn engine's
    # narrower (`turn_runtime.py::with_routing_agent_default`, `gate.get(
    # "incoming_by_code")`) can build its own roster without parsing prose. No n8n
    # capture can carry this key - the node was never asked to emit it. Blanket, not
    # per-fixture: every graded `annotate-incoming-picker` capture gains exactly this
    # one key and nothing else moves (measured across all seven failing captures before
    # this entry: `added=['incoming_by_code']`, no other key differs). Pinned by
    # `app/services/chatbot/lanes/business/pickers.py::annotate_incoming`'s own AC-1526
    # comment.
    Divergence(
        node="annotate-incoming-picker",
        fixture=None,
        hazard="AC-1526 (incoming_by_code)",
        reason=(
            "the annotator now also returns `incoming_by_code`, a per-code has/no-"
            "incoming map built from the same probe answers as the rendered message, "
            "kept as data for the turn engine's narrower rather than re-parsed from "
            "prose. No n8n capture emits this key."
        ),
        strip_paths=(("incoming_by_code",),),
    ),
    # OWNER RULING A, console pass 3 (6 Sep 2026): the ambiguous-customer picker
    # stamps "- has DO" / "- no DO" instead of "- has delivery" / "- no recent
    # delivery" / "- no delivery", and the set it stamps from now counts only order
    # rows that carry an `Actual Delivery Date`. Both halves are the owner's ruling
    # and neither can be fixture-visible: n8n's own body says "delivery" and builds
    # the set from every row the probe returned, so every capture of this node
    # records the old wording and the old membership by construction. Field-scoped
    # to the rendered message; `customer_probe_hits`, `customer_probe_window_days`,
    # `customer_probe_skip_reason`, `is_clarification` and the untouched roster all
    # still grade. Pinned by
    # test_s6a_gate_dry_run_and_seams.py::TestOwnerRulingACustomerPickerDOStamp.
    *(
        Divergence(
            node="annotate-customer-picker",
            fixture=name,
            hazard="owner ruling A (console pass 3, 6 Sep 2026)",
            reason=(
                "the picker suffix reads '- has DO' / '- no DO' and counts only orders "
                "with a delivery-order date. Live says 'delivery' and counts any order. "
                "Field-scoped to `escalate_message`."
            ),
            strip_paths=(("escalate_message",),),
        )
        for name in (
            "exec-14095480",
            "exec-14001898",
            "exec-14091114",
            "exec-14109393",
        )
    ),
    # The same ruling seen through the WHOLE sub: `resolve-exit-offer` carries the
    # annotator's message onward, so these two exit-arm captures move on exactly the
    # one field and nothing else (measured - `gate_clarification` is byte-equal,
    # because the whole-sub replay is fed the CAPTURED gate rather than re-running
    # `run_gate`). PLAN-chatbot-outstanding-report.md, S4 point 7, ADDS a second,
    # unrelated field to the same two captures: `ALLOWED["order"]` gained "warehouse",
    # so this whole-sub body's four `allowed_lookup` echoes (measured: `gate_debug`,
    # `gate.gate_debug`, `ctx_resolved.gate_debug`, `ctx_resolved.ctx.gate.gate_debug`)
    # each list one more type than a capture taken before that change - the same class
    # AC-1 already registers for the plain node replays, but THIS entry (being
    # fixture-specific) is the one `find()` returns first for these two names, so it
    # needs the same four paths added here rather than relying on AC-1's blanket entry
    # ever being reached.
    *(
        Divergence(
            node="sub-resolve-and-gate",
            fixture=name,
            hazard="owner ruling A (console pass 3, 6 Sep 2026) + AC-1 (allowed_lookup)",
            reason=(
                "the exit arm carries the customer picker's own '- has DO' / '- no DO' "
                "message (field-scoped to `escalate_message`), and separately "
                "`ALLOWED['order']` gaining 'warehouse' (S4 point 7) adds one more type "
                "to every `allowed_lookup` echo in this whole-sub body."
            ),
            strip_paths=(
                ("escalate_message",),
                ("gate_debug", "allowed_lookup"),
                ("gate", "gate_debug", "allowed_lookup"),
                ("ctx_resolved", "gate_debug", "allowed_lookup"),
                ("ctx_resolved", "ctx", "gate", "gate_debug", "allowed_lookup"),
            ),
        )
        for name in ("rg-15114061",)
    ),
    # R20 (owner round 7, 13 Sep 2026): `rg-15125764` is the SAME two fields as the entry
    # above (its `escalate_message` carries the picker's own hint wording, and its
    # `allowed_lookup` echoes predate S4 point 7) plus the two diagnostics that record
    # WHY the hint is now absent from it - and it needs its own entry because `find()`
    # returns the first match per fixture. Split from its sibling rather than widening
    # that entry, because `rg-15114061` (`order_status: null`) is NOT an outstanding ask,
    # still probes, and must keep grading both diagnostics.
    #
    # This capture IS the defect: `order_status: "outstanding"`, and the live picker
    # stamped "- no recent delivery" on all five candidates and closed with "None of
    # these have a recent delivery." The probe behind that claim measures orders with an
    # `actual_delivery_date` - DELIVERED DOs - which is the opposite population from the
    # outstanding report's own DO block, so the claim was about something the customer had
    # not asked about ("it is still kinda strange for me though, to say no DO, then later
    # when i get the summary, there is DO"). The port does not probe on an outstanding ask
    # at all, so `customer_probe_hits` is null (not measured) rather than 0 (measured
    # none), and `customer_probe_skip_reason` says which rule skipped it. Pinned by
    # tests/chatbot/test_outstanding_lane.py::TestOutstandingAskPickerHasNoDeliveryHint.
    Divergence(
        node="sub-resolve-and-gate",
        fixture="rg-15125764",
        hazard="R20 (owner round 7, 13 Sep 2026) + owner ruling A + AC-1 (allowed_lookup)",
        reason=(
            "an OUTSTANDING ask's customer picker no longer probes for a delivery order "
            "and prints no hint, so this capture's picker message, its "
            "`customer_probe_hits` (null = not measured, was 0) and its new "
            "`customer_probe_skip_reason` all move; `escalate_message` and the four "
            "`allowed_lookup` echoes move for the two reasons the sibling entry above "
            "records."
        ),
        strip_paths=(
            ("escalate_message",),
            ("customer_probe_hits",),
            ("customer_probe_skip_reason",),
            ("gate_debug", "allowed_lookup"),
            ("gate", "gate_debug", "allowed_lookup"),
            ("ctx_resolved", "gate_debug", "allowed_lookup"),
            ("ctx_resolved", "ctx", "gate", "gate_debug", "allowed_lookup"),
        ),
    ),
    # ATTACHMENT-MULTI (owner ruling 2 Oct 2026, Q3 (a)): the require-specific picker asks
    # "Which product do you mean? Please choose:" instead of "incoming search needs to be
    # more specific. Multiple matches found. Please choose:". `rg-15123789` is the one graded
    # whole-sub capture that re-runs the gate over a picker, so its header text moves in the
    # gate's own `gate_clarification` and in every copy of it downstream (the picker lines
    # are rendered after the header, so `escalate_message` moves with it). It also carries
    # pass 4 item F's retype and AC-1526's stamp map (the group entry below), so those paths
    # are kept here: `find()` returns the first match.
    Divergence(
        node="sub-resolve-and-gate",
        fixture="rg-15123789",
        hazard="ATTACHMENT-MULTI picker header + owner console pass 4, item F + AC-1526",
        reason=(
            "the picker header is the owner's plain question now; every other byte of the "
            "sub's output is unchanged (pass 4 item F's and AC-1526's own paths as below)."
        ),
        strip_paths=(
            ("ctx_resolved", "ctx", "parse", "output", "entities"),
            ("ctx_resolved", "ctx", "parse", "output", "shipment_hint_retyped"),
            ("ctx_resolved", "ctx", "parse", "output", "domain_dropped_with_shipment_hint"),
            ("incoming_by_code",),
            ("annotate_incoming", "incoming_by_code"),
            ("gate_clarification",),
            ("escalate_message",),
            ("gate", "gate_clarification"),
            ("ctx_resolved", "gate_clarification"),
            ("ctx_resolved", "ctx", "gate", "gate_clarification"),
            ("annotate_incoming", "gate_clarification"),
            ("annotate_incoming", "escalate_message"),
        ),
    ),
    # OWNER CONSOLE PASS 4, item F (6 Sep 2026): a container-hinted token that the
    # resolver answers with PRODUCTS and no shipment is retyped `product` before the
    # gate runs. Five graded `resolve-exit-offer` captures carry that shape, and on
    # every one of them the retype changes NOTHING else - measured, one at a time: the
    # exit kind, the gate, the picker text, `specific_options` and the roster are all
    # byte-equal, because the incoming lane is keyed by product code anyway. So the
    # divergence is the entity's own `hint` and the diagnostic that says why it moved,
    # and the rest of each capture still grades. Four of the five say "eta" or
    # "incoming" in the message, which is exactly why the DOMAIN half of the rule needs
    # the customer's own word and cannot run off `domain_signal_source`. Pinned by
    # test_resolve_gate_unit.py::TestAShipmentHintedTokenThatIsOnlyAProductIsRetyped.
    #
    # AC-1526 (turn re-architecture, `pickers.annotate_incoming`): the SAME five captures
    # are also `incoming` picks, so each one's `annotate_incoming` carries the new
    # `incoming_by_code` map (see the standalone `annotate-incoming-picker` entry below
    # for the full reason) - `find()` returns this entry first for these five names, so
    # the two paths are added here rather than relying on that entry ever being reached.
    *(
        Divergence(
            node="sub-resolve-and-gate",
            fixture=name,
            hazard=(
                "owner console pass 4, item F (6 Sep 2026) + AC-1526 (incoming_by_code)"
            ),
            reason=(
                "a shipment-hinted token the resolver answers with products only is "
                "retyped `product` before the gate. Field-scoped to the parser's ENTITY "
                "ARRAY inside ctx_resolved plus the two diagnostics - `strip` cannot reach "
                "one field of one element, so the whole array comes off, and "
                "test_resolve_gate_unit.py::TestTheRetypedEntityArrayDiffersOnlyInTheHint "
                "grades that array explicitly: same length, same order, every field "
                "byte-equal except the one `hint` that moved inbound_shipment -> product. "
                "`incoming_by_code` is the turn re-architecture's new per-code has/no-"
                "incoming stamp (AC-1526), absent from every n8n capture. Every other "
                "byte of the sub's output is unchanged."
            ),
            strip_paths=(
                ("ctx_resolved", "ctx", "parse", "output", "entities"),
                ("ctx_resolved", "ctx", "parse", "output", "shipment_hint_retyped"),
                ("ctx_resolved", "ctx", "parse", "output", "domain_dropped_with_shipment_hint"),
                ("incoming_by_code",),
                ("annotate_incoming", "incoming_by_code"),
            ),
        )
        for name in (
            "rg-15128371",
            "rg-15192977",
            "rs8-t2-picker",
            "rs8a-t2-picker-T1",
        )
    ),
    # OWNER RULING D1 (console pass 3) as struck by the review of #706, blocker B2: the
    # model's own `escalation.is_escalation_confirmation` is the ONE accept signal over an
    # open offer, and on this capture the model got it wrong - "YES ESCALTE" (a typo of
    # ESCALATE) came back `request_for_help`, null routing, `is_escalation_confirmation:
    # false`. Live confirmed it through `is_affirmative` alone, which is the same test
    # that confirmed "can someone else help me" (turn 9a40182a). The first cut rescued this
    # capture with an accept-word list over the raw message; that is a D11 hard-fail and it
    # re-opened D1 ("ok, can someone else help me" confirmed the stale offer). Ruling:
    # register, do not sniff. The port asks which team here; the parser prompt is where a
    # typo'd acceptance gets read as one. Field-scoped to `escalation`; the entities, the
    # routing and everything else on the capture still grade.
    Divergence(
        node="output_exchange",
        fixture="parser-15074293",
        hazard="owner ruling D1 (console pass 3) / review of #706 B2",
        reason=(
            "a request_for_help with a null routing over an open offer asks which team "
            "unless the model's own is_escalation_confirmation says it accepted; on this "
            "capture the model says false for 'YES ESCALTE'. Field-scoped to `escalation`."
        ),
        strip_paths=(("output", "escalation"),),
    ),
    # D10 (owner console pass, 8 Sep 2026, turn 69d9900e "srtwc8610-sh hav incoming?"):
    # an `incoming`-domain entity hinted `inbound_shipment` or `order` whose raw is
    # product-code-shaped (and not a real ISO 6346 container number) is retyped to
    # `product` before the resolver has to referee the parser's own guess - n8n's live
    # body has no such arm, so it left the model's `inbound_shipment` hint standing. This
    # capture ("MWC7625-SH") is the one graded corpus member the retype actually fires
    # on. Field-scoped to the entity list and the retype's own diagnostic; the domain,
    # the intent and everything else on the capture still grades byte for byte. Pinned by
    # `tests/chatbot/test_growth_r1_review_fixes.py::TestD10AnIncomingAskTypesTheCodeAsAProduct`.
    Divergence(
        node="output_exchange",
        fixture="exec-13488887",
        hazard="owner ruling D10 (8 Sep 2026, AC-816-adjacent)",
        reason=(
            "the capture records the model's own `inbound_shipment` hint for a "
            "product-shaped code under `incoming`; the port retypes it to `product` "
            "before resolution. Field-scoped to the entity list and "
            "`incoming_hint_retyped_to_product`."
        ),
        strip_paths=(
            ("output", "entities"),
            ("output", "incoming_hint_retyped_to_product"),
        ),
    ),
    # PLAN-broaden-domain-switch (exec 15121180, 9 Sep 2026): "ANY INCOMING" after a stock
    # turn on SRTWT04A came back domain_hint incoming / intent_hint check_incoming /
    # broaden_axis all / scope_intent broaden / entity_op clear - a COHERENT (incoming,
    # check_incoming) pair, which the AXIS BROADEN restore used to overwrite back to
    # inventory because it could not tell that shape apart from a genuine "all products"
    # wander. The port now keeps the model's own domain and reuses the carried SRTWT04A
    # (its `product` hint is not blocked under `incoming`), which is exactly the fix. The
    # capture PINS THE DEFECT: `domain_hint`, `intent_hint`, `routing`, `entities`,
    # `entity_op` / `entity_op_applied`, `broaden_axis`, `scope_intent`,
    # `broaden_axis_domain_restored` and the new `domain_switch_over_broaden` diagnostic
    # all move; `domain_signal_source` does not (it is computed off the model's own raw
    # emission BEFORE the restore/switch block runs, so it reads "intent_explicit" on both
    # sides) and stays out of the list. Behaviour pinned by
    # test_output_exchange_rules.py's "R7" section (AC-1, AC-2, AC-3, AC-5, AC-6, AC-7,
    # AC-11, AC-12, AC-13) - AC-4 is the pre-existing "R4" test
    # (`test_r4_widening_one_axis_keeps_the_question_it_was_asked_about`), not R7.
    Divergence(
        node="output_exchange",
        fixture="parser-15121180",
        hazard="PLAN-broaden-domain-switch",
        reason=(
            "the capture pins the pre-fix answer: a coherent (incoming, check_incoming) "
            "pair beside broaden_axis restored to the PRIOR domain (inventory) and cleared "
            "the carried product. The port now reads it as a domain switch, not a wander, "
            "and reuses the carried entity. Field-scoped to the fields the switch rule "
            "moves."
        ),
        strip_paths=(
            ("output", "domain_hint"),
            ("output", "intent_hint"),
            ("output", "broaden_axis"),
            ("output", "scope_intent"),
            ("output", "entity_op"),
            ("output", "entity_op_applied"),
            ("output", "entities"),
            ("output", "broaden_axis_domain_restored"),
            ("output", "domain_switch_over_broaden"),
            ("output", "routing"),
        ),
    ),
    # The three keys rules 2, 3 and 4 ADD to `output_exchange`'s emission. No
    # capture can contain a key the node did not emit when it was taken, so this
    # is the same class as the `pending` marker above and is handled the same
    # way: FIELD-SCOPED and blanket, so every other byte of every
    # `output_exchange` capture is still graded. Listed LAST on purpose - `find`
    # returns the first matching entry, so the per-fixture entries above win for
    # the captures whose behaviour genuinely moved.
    Divergence(
        node="output_exchange",
        fixture=None,
        hazard="owner ruling K rules 2/3/4 (AC-816) - added diagnostics",
        reason=(
            "`member_offer_filter_modification`, `bare_entity_retyped` and "
            "`entities_dropped_on_topic_change` are diagnostics the port emits and n8n "
            "has no equivalent of. Field-scoped: the rest of every capture still grades, "
            "and the behaviour behind each key is pinned by its own unit test. Only the "
            "first is reached by the corpus today (3 captures, all of them turns where "
            "the filter arm and n8n's Tier 3 both touch nothing, so the key is the whole "
            "difference); the other two are listed with it because they are the same "
            "class - a key no capture can contain - and a capture that reached one would "
            "otherwise fail on a diagnostic rather than on behaviour."
        ),
        strip_paths=(
            ("output", "member_offer_filter_modification"),
            ("output", "bare_entity_retyped"),
            ("output", "entities_dropped_on_topic_change"),
            # Console pass 4, item 3 (AC-823): the same class again - the diagnostic the
            # filter-modification arm stamps when it puts the offer's own scope back. n8n
            # has no such arm, so no capture can carry the key.
            ("output", "member_offer_scope_reused"),
            # Console pass 4, item 4 / issue #708 (AC-824): stamped when a numbered pick
            # over a `suggest_offer` roster is merged through `apply_dym_pick` instead of
            # replacing the scope. n8n has no such arm either.
            ("output", "suggest_offer_pick_merged"),
            # Console pass 5, item B2 (H80/AC-829): stamped by
            # `resolve_gate.resolve_bare_reply_under_member_offer` when a bare reply under
            # an open member_offer resolved against the RESOLVER and replaced a half of
            # the carried pair. Same class as the five above - a key no capture can
            # contain, because `sub-resolve-and-gate` has no equivalent arm at all.
            ("output", "bare_member_offer_entity_resolved"),
            # S4 points 3/4/5 (PLAN-chatbot-outstanding-report.md): the same class again.
            # `_apply_outstanding_pending` stamps these when it resolves (or fails to
            # resolve) an OPEN `outstanding_scope` / `outstanding_detail` ask -
            # `outstanding_scope_ask_candidate` (a bare "outstanding" + product, before
            # any grant check), `outstanding_reask_filters` (an out-of-range scope
            # answer), `outstanding_carried_customer_ids` and `outstanding_detail_pick`
            # (the scope/detail answer's restored filters and pick). n8n has none of
            # this mechanism, so no capture predating this plan can carry any of them.
            ("output", "outstanding_scope_ask_candidate"),
            ("output", "outstanding_reask_filters"),
            ("output", "outstanding_carried_customer_ids"),
            ("output", "outstanding_detail_pick"),
        ),
    ),
    # A7 (chatbot-growth-r1, AC-921/AC-922): `crossdomain_render`'s `_xdBlock` gained three
    # diagnostic keys - `nothing_codes`, `nothing_note`, `nothing_missing` - so
    # `run_crossdomain`'s NEW ladder rung (the purchase_order probe after the existing
    # inventory<->incoming one) can act on exactly the codes the first probe found nothing
    # for, without re-deriving them. n8n has no ladder and no equivalent keys; every
    # existing capture predates A7, so no capture can carry them. Same class as the
    # owner-ruling-K diagnostics above (a key no capture can contain), field-scoped so the
    # rest of `_xdBlock` (`block`, `any`, `team`, `origin`, ...) still grades byte-exact.
    # Behaviour pinned by test_crossdomain_ladder.py, not by these fixtures.
    Divergence(
        node="crossdomain-render",
        fixture=None,
        hazard="A7 (AC-921/AC-922) - added diagnostics",
        reason=(
            "`nothing_codes` / `nothing_note` / `nothing_missing` are new keys the port "
            "adds to `_xdBlock` for the cross-domain ladder's next rung to read; n8n's "
            "node has no ladder and no equivalent, so no capture can carry them. "
            "`zero_codes` (11 Sep 2026, second ruling, R2) joined them the same way: which "
            "of `nothing_codes` read 0 at every location rather than genuinely absent, a "
            "distinction n8n's node never draws."
        ),
        strip_paths=(
            ("_xdBlock", "nothing_codes"),
            ("_xdBlock", "nothing_note"),
            ("_xdBlock", "nothing_missing"),
            ("_xdBlock", "zero_codes"),
        ),
    ),
    # A6 (chatbot-growth-r1, AC-911): `spo_allocation` is no longer in
    # `DEFAULT_UNSUPPORTED_DOMAINS` - `crm_procurement_spo_allocations_last_receipt_list` answers
    # "last in" now, so a turn asking about it routes to `business_query` instead of
    # `not_supported`. This capture is exactly that case (test_run_id
    # "rs1b4-01-notsupported"), so the whole item diverges (branch_kind and the
    # access-check fields it carries) - blanket, because the hazard changes what this
    # turn IS, not one field of it. Owner-approved 7 Sep 2026 (this plan). Behaviour
    # pinned by test_crossdomain_ladder.py::TestAC911SPOAllocationDomainNoLongerUnsupported
    # and route.py's own DEFAULT_UNSUPPORTED_DOMAINS.
    Divergence(
        node="route-turn",
        fixture="rs1b4-01-notsupported",
        hazard="A6 (AC-911)",
        reason=(
            "spo_allocation was unblocked from DEFAULT_UNSUPPORTED_DOMAINS, so this "
            "capture's domain now routes to business_query instead of the captured "
            "not_supported - the deliberate point of A6."
        ),
    ),
    # AC-1592 test triage (review S1, live exec 11818957, `gate.py::ALLOWED`
    # `"resource_attachment"` row): a domain that used to have NO row (pass-through
    # unscoped, `gate_reason: "domain '<x>' not in matrix; passing through unscoped"`)
    # now has one (`["attachment_type", "attachment"]`), so a `resource_attachment`
    # capture's `gate_reason` moves from that sentence to `"ok"` AND gains its own
    # `gate_debug.allowed_lookup` echo (both paths named here, not split into the
    # blanket AC-1 entry below, because `find()` returns only the FIRST match per
    # fixture and this fixture-scoped entry has to be placed before that `fixture=None`
    # one to keep `gate_reason` non-blanket - so it must carry both fields itself).
    # FIXTURE-SCOPED, not blanket: `gate_reason` disagreeing is exactly the signal a real
    # regression on another domain should still fail on, so only the four graded
    # `disallowed-entity-gate` captures whose `domain` is `resource_attachment`
    # (confirmed complete - the only four of 181 graded captures with that domain) get
    # the strip; this node's own runner output has no `gate`/`ctx`/`ctx_resolved`
    # wrapper, so one path per field suffices. Behaviour pinned by
    # test_rearch_port_warehouse_not_a_document_filter.py::TestAWarehouseIsNotADocumentFilter.
    *(
        Divergence(
            node="disallowed-entity-gate",
            fixture=name,
            hazard="AC-1592 test triage (resource_attachment matrix row)",
            reason=(
                "resource_attachment gained a matrix row, so this capture's domain "
                "routes 'ok' instead of the captured 'not in matrix; passing through "
                "unscoped', and gate_debug gains its own allowed_lookup echo - the "
                "deliberate point of the row landing."
            ),
            strip_paths=(("gate_reason",), ("gate_debug", "allowed_lookup")),
        )
        for name in (
            "rg-15002021",
            "rg-15164418",
            "exec-14213685",
            "exec-14213959",
        )
    ),
    # AC-1 (chatbot-warehouse-entity-and-last-in, 8 Sep 2026): `ALLOWED` gained
    # "warehouse" on `inventory` and a whole `spo_allocation` row, so the gate's DEBUG
    # ECHO of the matrix lists one more type than every capture taken before the change.
    # `gate_debug.allowed_lookup` is `ALLOWED[domain]` copied onto the output. No capture
    # in the corpus feeds it into a decision; the one reader that derives anything from it
    # (`answer.py`'s needs-scope message, which turns the allowed types into the "give me a
    # product code or warehouse" option list) is graded separately, by
    # tests/chatbot/test_warehouse_entity.py::TestZeroEntitySpoAllocationAsksInsteadOfFanningOut.
    #
    # D10 (owner console pass, 8 Sep 2026, turn 69d9900e "srtwc8610-sh hav incoming?")
    # widens the SAME echo, added to this one entry rather than a new one because
    # `find()` returns only the first match per fixture: `gate_debug.incompatible_only`
    # is a new per-token diagnostic (`token -> [types]` for a token whose ONLY match is
    # an incompatible type), read by `miss_suggest.py`'s did-you-mean machinery. Absent
    # from every capture that predates it (the strip is a no-op there); present whenever
    # a resolved token's matches are all outside `ALLOWED[domain]`.
    #
    # FIELD-SCOPED to these two keys, deliberately: `gate_passed`, `gate_reason`,
    # `compatible_entities`, `require_specific` and every other byte are still graded, and
    # no corpus capture carries a warehouse entity for the new type to change one of them
    # (measured 8 Sep 2026: with the matrix row reverted, all 135 vendored replays pass,
    # so the echo is the ONLY thing the matrix change moves). The five nested copies are
    # the same object seen through the `resolve-exit-*` item's own spreads
    # (`gate`, `ctx.gate`, `ctx_resolved`, `ctx_resolved.ctx.gate`), which is why one
    # hazard needs five paths.
    #
    # Behaviour pinned by tests/chatbot/test_warehouse_entity.py::TestGateKeepsWarehouse.
    *(
        Divergence(
            node=node,
            fixture=None,
            hazard="AC-1 (chatbot-warehouse-entity-and-last-in) + D10 (incompatible_only)",
            reason=(
                "ALLOWED gained 'warehouse' on inventory and a spo_allocation row, so "
                "gate_debug.allowed_lookup - the gate's read-only echo of the matrix - "
                "lists one more type than a capture taken before the change; gate_debug "
                "also gained a per-token incompatible_only diagnostic (D10, present only "
                "when a resolved token's matches are all outside ALLOWED[domain]). "
                "Nothing else about the node moves."
            ),
            strip_paths=(
                ("gate_debug", "allowed_lookup"),
                ("gate", "gate_debug", "allowed_lookup"),
                ("ctx", "gate", "gate_debug", "allowed_lookup"),
                ("ctx_resolved", "gate_debug", "allowed_lookup"),
                ("ctx_resolved", "ctx", "gate", "gate_debug", "allowed_lookup"),
                ("gate_debug", "incompatible_only"),
                ("gate", "gate_debug", "incompatible_only"),
                ("ctx", "gate", "gate_debug", "incompatible_only"),
                ("ctx_resolved", "gate_debug", "incompatible_only"),
                ("ctx_resolved", "ctx", "gate", "gate_debug", "incompatible_only"),
            ),
        )
        for node in (
            "disallowed-entity-gate",
            "resolve-exit-continue",
            "resolve-exit-not-found",
            "resolve-exit-offer",
            "sub-resolve-and-gate",
        )
    ),
    # D4 (chatbot-answer-polish, 12 Sep 2026, finding 4): `crossdomain_zeroset`'s
    # non-`resolutions` branch now requests an intersection product when a typed token
    # PREFIXES its normalised code (>= 4 chars), not only on equality - n8n's node only
    # ever compared for equality, so a typed prefix like "MMC544" never requested its
    # family member "MMC544-AL-BL" at all (owner finding: "ETA SRTWT6236" never probed
    # SRTWT6236-GY's open PO line). Both captures below are real intersections where a
    # typed token is a >= 4 character prefix of a canonical_code the n8n capture treated
    # as unrequested; this port now requests them, which is the deliberate point of D4.
    # Field-scoped to `_xd` (`exec-13479632`) or, more narrowly, to `_xd.requested` alone
    # (`exec-13481094`, review fix round: only `requested` moves on this capture -
    # `active`/`missing`/`probe_entities` are unaffected here, unlike `exec-13479632` where
    # the prefix match is what makes the branch active at all); every other key of the
    # validator item still grades byte for byte. Behaviour pinned by
    # tests/chatbot/test_crossdomain_ladder.py::TestOwner12SepTypedPrefixIsRequested.
    Divergence(
        node="crossdomain-zeroset",
        fixture="exec-13479632",
        hazard="D4 (chatbot-answer-polish, 12 Sep 2026, finding 4)",
        reason=(
            "a typed token that PREFIXES an intersection product's canonical_code "
            "(>= 4 chars) now requests it, where n8n's node only matched on equality. "
            "Field-scoped to `_xd`."
        ),
        strip_paths=(("_xd",),),
    ),
    Divergence(
        node="crossdomain-zeroset",
        fixture="exec-13481094",
        hazard="D4 (chatbot-answer-polish, 12 Sep 2026, finding 4)",
        reason=(
            "a second, ALREADY-satisfied typed token in this capture's intersection now "
            "also prefix-matches a sibling canonical_code, so `_xd.requested` gains that "
            "sibling where n8n's node named the first token alone. `active` stays False "
            "either way (the primary render already returned the code), so field-scoped "
            "to `_xd.requested` only."
        ),
        strip_paths=(("_xd", "requested"),),
    ),
]


# World-level allowances live in `tests/chatbot/worlds.py`, not here, and there are
# exactly two of them (`pending` and `dym_offer.id`). A world that differs anywhere else
# is either a defect or a NAMED body difference, and a body difference SKIPS the world
# rather than excusing a key - so there is no per-world delta table to keep honest.

# ---------------------------------------------------------------------------- #
# Deliberate behaviour differences that are NOT per-fixture, recorded here because
# `DIVERGENCES` above is per-fixture and these have no fixture to attach to.
# ---------------------------------------------------------------------------- #

# D5 (S6a): n8n hard-codes the respond.io workspace as `space_id: "364817"` in FOUR places
# inside `sub-resolve-and-gate` - the `get-access-types` URL, the `resolve-entity` URL, and
# both probes' `semantic_input` / `user_prompt`. The port reads it from the default respond
# workspace row instead (`head/access.py::default_space_id`), which is the whole of D5:
# "the respond.io `space_id` comes from the default respond workspace row".
#
# NOT a replay divergence, and that is worth stating rather than leaving to inference:
# every replay passes `space_id` in explicitly (`test_replay.SUB_REPLAY_SPACE_ID = "364817"`,
# and the node runners never touch it at all), so the captures grade the same value n8n
# produced. The difference is only reachable in production, and only on an install whose
# default workspace is not 364817 - which is exactly the install D5 exists for.
#
# The one behaviour to know about: an install with NO default respond workspace row gets
# `space_id = None`, and `resolve_active_access_levels_for_contact` then 404s rather than
# silently answering for the wrong workspace. That is fail-closed on purpose.
SPACE_ID_FROM_THE_WORKSPACE_ROW = Divergence(
    node="sub-resolve-and-gate",
    fixture=None,
    hazard="D5",
    reason=(
        "n8n hard-codes space_id 364817; the port reads the default respond workspace row. "
        "Not fixture-visible: every replay pins 364817 explicitly."
    ),
)


# S4. n8n has NO reply on the low_signal lane's setup paths. `sub-casual-llm` binds its
# OpenAI credential to the node, so a missing API key, an unset AI-assistant config or a
# resolver that will not answer does not reach `sub-error-logger2` at all: the sub throws,
# the spine's caller has no error output on that edge, and the turn dies with the customer
# never told anything. Zero captures on the arm since 1 Sep, which is consistent with it
# never having produced an item to capture.
#
# The port fails that turn instead, at `stage = casual_llm` with `branch_kind` still
# `low_signal`, and sends a FIXED sentence (`casual.CLARIFIER_UNAVAILABLE_REPLY`) rather
# than interpolating the exception: those messages name providers and configuration keys,
# and none of it belongs in a WhatsApp reply. The CALL arm is unchanged and still sends
# `sub-error-logger`'s own interpolated text, which is why the two differ.
#
# Not fixture-visible - there is no capture of a path that never emitted one - so this is
# recorded here rather than against a replay.
CASUAL_SETUP_FAILURE_HAS_A_REPLY = Divergence(
    node="sub-casual-llm",
    fixture=None,
    hazard="H32",
    reason=(
        "n8n drops a low_signal turn whose clarifier could not be SET UP (no API key, no "
        "AI config, resolver error): the credential is node-bound, so nothing reaches "
        "sub-error-logger2 and the customer is told nothing. The port fails the turn at "
        "stage=casual_llm and sends a fixed sentence, never str(exc)."
    ),
)


# S6c. `crossdomain-zeroset`'s DOMAIN GUARD, and WHICH body it came from.
#
# Two live workflows ship a node called `crossdomain-zeroset` and they differ:
#
#   * `sub-main-processing-live` (id 53RxDSON8P3QSN22, version
#     53ca1c6b-a6b3-48ed-b094-2cddafb3185c) ships 151 lines, sha256
#     fb9d41cf64ea320bd016ef516fb4b0edd903cc1da9996fd01eabb4a00fc1c06d, and CARRIES the
#     guard ("DOMAIN GUARD (2026-09-01, exec 14769923)"). This is the body the plan's
#     S6c line count (151) names and the one the port was made from.
#   * `live-spine-sorento-consume-main` (id 9qVyfUxmRQqrpGRMDLRuz, version
#     c9fe3e68-b732-460d-b968-c1b4a5e5f038) ships 143 lines, sha256
#     a880d01e3629538bdde874f60875b481af7415acb6c7f12d4795171074518f92, and does NOT.
#     The spine is `active: true` and has no `Call 'sub-main-processing'` node, so IT is
#     the body answering turns today.
#
# So relative to the shipping path the guard is a CRM behaviour change, not parity, and
# it is registered as one rather than left to a comment. It only ever REMOVES codes from
# `_xd.requested` (a pick made under an order or promotion offer stops naming a product
# on an inventory turn, which printed "No stock records found for: CG-202608-051."), so
# the direction is fail-quiet, and the pick made THIS turn is unaffected.
#
# Not fixture-visible: all five graded `crossdomain-zeroset` captures predate the guard
# (22 Aug 2026) and none carries a foreign-domain `dym_offer`, so no replay changes.
# Pinned by tests/chatbot/test_s6c_answer_lane.py::TestH22H23DymOfferDomainCleared.
CROSSDOMAIN_DYM_OFFER_DOMAIN_GUARD = Divergence(
    node="crossdomain-zeroset",
    fixture=None,
    hazard="H22 / H23",
    reason=(
        "carried did-you-mean picks ride into the cross-domain read only when the offer's "
        "own domain is inventory / incoming. Present in sub-main-processing-live's 151-line "
        "body (sha fb9d41cf64ea320b), absent from the ACTIVE spine's 143-line one "
        "(sha a880d01e3629538b). Not fixture-visible: the five captures predate it."
    ),
)


# Owner ruling 11 Sep 2026, second ruling (R2): a code whose stock rows are ALL 0 is now
# treated as absent for the cross-domain ladder, so it climbs and the block says so - the
# n8n block never drew this distinction at all. Fixture-visible: three of the six item-G
# captures (`exec-14119800`, `exec-14120400`, `exec-14122546`, registered above) are, in
# real data, exactly this shape - a returned code whose cross-probed rows are all 0. Fix
# round (nit 14): a zero-flagged code no longer earns item-G's own AC-820 line at ALL (the
# zero sentence two paragraphs later already says the same thing, so printing both would
# be a duplicate) - for these three the item-G line is GONE, replaced by the zero
# sentence, not kept alongside it.
# `TestCrossdomainRenderBlockIsByteEqualMinusTheOneSidedLine` (test_s6c_engine_paths.py)
# reads which is which off `_xdBlock.zero_codes`, itself derived per capture via
# `answer._rows_all_zero` on the code's own probed rows rather than a hard-coded fixture
# list, so a fourth zero capture added later gets the same treatment with no test change.
CROSSDOMAIN_ZERO_EVERYWHERE_CLIMBS = Divergence(
    node="crossdomain-render",
    fixture=None,
    hazard="R2 (11 Sep 2026, second ruling)",
    reason="stock at 0 everywhere is treated as absent for the ladder; the n8n block never said so.",
)


# S6c. `build-suggest-offer`'s sibling picker breaks a has-incoming tie with
# `String(a.code).localeCompare(String(b.code))`; the port uses Python's code-point
# order. ICU treats punctuation and case differently: node sorts
# `SRT_100, SRT-100, SRT1, srt100, SRT100, SRTA`, Python sorts
# `SRT-100, SRT1, SRT100, SRTA, SRT_100, srt100`. Only reachable when one product family
# holds two codes differing solely in punctuation or case, which no captured turn does -
# every graded `build-suggest-offer` replay is byte-equal. Recorded rather than fixed
# because the fix is a new dependency (PyICU) for a tiebreak inside one family; the
# trigger to pay for it is a real family of that shape.
SIBLING_TIEBREAK_IS_CODE_POINT = Divergence(
    node="build-suggest-offer",
    fixture=None,
    hazard="platform (localeCompare vs code-point order)",
    reason=(
        "the sibling picker's tiebreak is ICU collation in n8n and code-point order in "
        "Python. Not fixture-visible: no captured family holds two codes differing only "
        "in punctuation or case."
    ),
)


# Owner console pass, 6 Sep 2026. `route-turn`'s `wants_escalation_or_help` fires on
# `message_type === 'request_for_help'` (portal_link exempted) ONE ARM BEFORE
# `is_ideate_domain`, so a turn the parser tagged `domain_hint: 'ideate'` never reached the
# ideate lane at all - the console run answered "I have an idea for you" with `out_of_scope`.
# The ladder order is faithful to the live workflow; it hid the ideate arm the moment access
# flipped to `allow`, which is a defect in the ORIGINAL, not in the port. The port therefore
# exempts `ideate` beside `portal_link` rather than reproducing the shadow.
#
# Not fixture-visible: no captured `route-turn` turn carries `domain_hint: 'ideate'` (the
# ideate lane shipped after the capture window), so no replay changes. Pinned by
# tests/chatbot/test_route_unit.py::TestIdeateNeverShadowedByHelpRequest.
IDEATE_NOT_SHADOWED_BY_REQUEST_FOR_HELP = Divergence(
    node="route-turn",
    fixture=None,
    hazard="H59 (owner console pass, 6 Sep 2026)",
    reason=(
        "`domain_hint: 'ideate'` is exempted from the request_for_help arm so the ideate "
        "lane is reachable. Live's ladder order shadows it permanently. Not "
        "fixture-visible: no capture carries an ideate domain hint."
    ),
)


# Owner console pass, 6 Sep 2026. A cold "all of them" - the customer answering a
# clarification menu with the broadest option - comes back `message_type: 'casual'` with a
# null `domain_hint`, alongside the `scope_intent: 'broaden'` / `broaden_axis: 'all'` the
# parser reads correctly. `is_low_signal` fires on `casual` ALONE and sits above
# `is_clarification`, so the turn was answered "Hi!". The port reads the two scope keys in
# `is_clarification` and moves that arm above `is_low_signal`; the two message_type sets are
# disjoint, so nothing the live ladder ever saw changes branch.
#
# A ROUTER backstop rather than a parser change on purpose: the scope keys are already
# right, and re-teaching the prompt to stop stamping `casual` on a two-word reply is a
# retune with no upper bound. Not fixture-visible: no captured `route-turn` turn carries
# `broaden_axis: 'all'` with a null domain. Pinned by
# tests/chatbot/test_route_unit.py::TestBroadenAllNeverReadAsLowSignal.
BROADEN_ALL_IS_A_CLARIFICATION = Divergence(
    node="route-turn",
    fixture=None,
    hazard="H60 (owner console pass, 6 Sep 2026)",
    reason=(
        "scope_intent 'broaden' + broaden_axis 'all' + null domain routes clarify_menu "
        "instead of low_signal, and the clarification arm moves above the low-signal one. "
        "Not fixture-visible: no capture carries that shape."
    ),
)


# H61 reversed (owner ruling, 10 Sep 2026). The 6 Sep 2026 owner console pass had wanted
# the delivered-status miss message to STATE the estimated delivery date the JS computes
# and discards. That ruling is now reversed: `orders.estimated_delivery_date` is not a
# real promise - the import stamps it as `order_date + 2 business days`
# (`order_service.py` ~2824) on every master row - so the CRM UI may keep showing it, but
# the chatbot / turn API must not say it. The delivered-status arm names the order and its
# current status only: "Order <code> (<customer>) hasn't been delivered yet - current
# status: <status>. Would you like me to escalate to <team> team?", with no ETA suffix.
#
# Not fixture-visible: no graded `not-found-error-message` capture reaches the
# `order_status: 'delivered'` arm with an estimated delivery date on the match. Pinned by
# tests/chatbot/test_s6c_answer_lane.py::TestStatusAwareMissMessageOmitsTheEtaDate.
STATUS_MISS_MESSAGE_OMITS_THE_ETA = Divergence(
    node="not-found-error-message",
    fixture=None,
    hazard="H61 reversed (owner ruling, 10 Sep 2026)",
    reason=(
        "the delivered-status miss message must NOT state the estimated delivery date: "
        "the import stamps it as order_date + 2 business days, not a real promise. Not "
        "fixture-visible: no capture reaches that arm with a date on the resolved order."
    ),
)


# Owner console pass, 6 Sep 2026. Two console turns - "escalate to Nurain" (a customer
# service person) and "escalate to marketing" - both arrived with
# `routing = {suggested_team: null, suggested_agent: null}`, and the turn was assigned to
# whatever team the PREVIOUS turn had been routed to: the comment named marketing_product
# for the person and purchasing for the marketing ask. Live has no gate for either case (the
# `clarify-team-gate` in the export belongs to the unpromoted B-TEAM-1' build), so the lane
# assigns whatever team reaches it.
#
# The port adds a two-armed gate ahead of the assignment: a `person_mention` is resolved
# against the staff roster (exactly one match routes to THEIR team as a direct pick, no
# match or more than one ASKS), and a turn with no person, no team and a PREVIOUS routing to
# inherit asks rather than inheriting. With no previous routing there is nothing to inherit
# and the lane carries on unchanged, which is what keeps
# `test_no_team_clarify_on_live_team_flows_through_unguarded` (live's own behaviour) green
# and `test_no_hard_default_team` (B-TEAM-1') still xfailing.
#
# Not fixture-visible: the four graded escalation nodes are `escalation-input`,
# `escalation-context`, `clarify-company-reply` and `escalation-result`, none of which this
# touches - the gate lives in `run()`, which has no capture. Pinned by
# tests/chatbot/test_s5_escalation_lane.py::TestPersonMentionEscalationRoutesByStaffLookup.
ESCALATION_ROUTES_BY_STAFF_LOOKUP = Divergence(
    node="sub-escalation",
    fixture=None,
    hazard="H64 (owner console pass, 6 Sep 2026)",
    reason=(
        "a named person routes to their own team by staff lookup, and a null-team ask with "
        "a previous routing asks instead of inheriting it. Live has neither gate. Not "
        "fixture-visible: the gate is in `run()`, and the four graded nodes are unchanged."
    ),
)


# Owner console pass 5, item B1 (7 Sep 2026, H79/AC-828). `entity_op == "reuse"`'s
# `broaden_axis == "date"` wipe unconditionally nulled `date_filter_start` /
# `date_filter_end` / `date_mode` whenever `broaden_axis` was `"date"`, even when the
# SAME parser output already carried a concrete date this turn (prod turns e4381b0d /
# 98526b81, "last month" under an open member_offer, `user_goal: "trying to specify the
# date range as last month"`). The wipe now only fires when the turn ALSO supplied no
# date of its own (gated on `has_current_date`, already computed one line above and
# already used by the sibling `elif`, just never consulted by this one).
#
# Not fixture-visible: no captured `output_exchange` fixture anywhere in the corpus
# carries `broaden_axis: "date"` at all (grepped the whole corpus, zero hits), so no
# replay can show either the old or the new behaviour - the shape reached production
# without ever being captured. Pinned by
# tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::
# TestB1LastMonthUnderMemberOfferKeepsTheParsersOwnDateFilter.
BROADEN_AXIS_DATE_KEEPS_A_CONCRETE_DATE_THIS_TURN = Divergence(
    node="output_exchange",
    fixture=None,
    hazard="H79 (owner console pass 5, 7 Sep 2026)",
    reason=(
        "broaden_axis == 'date' no longer wipes date_filter_start/end/mode when the "
        "SAME parser output already set a concrete date this turn. Not fixture-visible: "
        "no capture in the corpus carries broaden_axis: 'date' at all."
    ),
)


# Owner console pass 5, item B2 (7 Sep 2026, H80/AC-829, D11 inventory row R-b). A bare
# reply under an open `member_offer` that the parser extracted NOTHING from (prod turn
# 6ea9fd1a, "rpacc" - `entities: []`) is now sent to the RESOLVER as one token
# (`resolve_gate.resolve_bare_reply_under_member_offer`, between the resolver seam and
# the gate) and, on an unambiguous match, REPLACES the matching half of the carried
# entity pair - a mechanism `sub-resolve-and-gate` has no equivalent of at all.
#
# Not fixture-visible: this is a NEW resolver round trip with no n8n counterpart, so no
# `sub-resolve-and-gate` capture can show it either firing or not - the node's own
# graded shape (`ctx_resolved.ctx.parse.output.entities`) only diverges on a turn this
# precise precondition matches (open member_offer, zero entities named this turn, a
# bare reply of four words or fewer), and the full corpus replay stays green because no
# capture has that shape. Pinned by
# tests/chatbot/test_pass5_item2_member_offer_business_query_filter_route.py::
# TestB2ABareProductCodeUnderTheOfferNarrowsTheProduct.
BARE_MEMBER_OFFER_REPLY_ASKS_THE_RESOLVER = Divergence(
    node="sub-resolve-and-gate",
    fixture=None,
    hazard="H80 (owner console pass 5, 7 Sep 2026)",
    reason=(
        "a bare reply under an open member_offer with zero entities of its own is sent "
        "to the resolver as one token and replaces the matching half of the carried "
        "pair on an unambiguous match. n8n has no such arm. Not fixture-visible: no "
        "capture has the precondition shape (open offer, zero entities, a short bare "
        "reply)."
    ),
)


# Issue #715 (H77/AC-825, closed console pass 5, 7 Sep 2026). `gate.py`'s own "A PINNED
# PICK WINS OVER FUZZY RE-RESOLUTION" mechanism widened `pin_types` / `pin_bases` /
# `pin_codes` and `_keep`'s own uuid check from this-turn-only `pins` / `pin_uuids` to
# carried-or-current `pins_all` / `pin_uuids_all` - a carried customer pick now REPLACES
# the resolver's own re-resolved rows for a shared debtor code, never merges with them.
#
# Not fixture-visible: reachable only when a carried customer pin's debtor code is
# shared by MULTIPLE distinct accounts the resolver's bare re-search returns (production
# scale - one code shared by 99 rows); no seedable-at-test-scale capture can show it,
# and no corpus fixture happens to carry that shape either (the 182 graded
# `disallowed-entity-gate` captures are unchanged). Pinned by
# tests/chatbot/test_pass4_item2_last_month_keeps_customer_scope.py::
# TestACarriedCustomerPickIsPinnedAtTheGateNotAtTheResolver.
CARRIED_CUSTOMER_PIN_REPLACES_SHARED_DEBTOR_CODE_ROWS = Divergence(
    node="disallowed-entity-gate",
    fixture=None,
    hazard="H77 (issue #715, closed console pass 5, 7 Sep 2026)",
    reason=(
        "a carried customer pin's filter (pin_types / pin_bases / pin_codes / _keep's "
        "uuid check) now reads pins_all / pin_uuids_all instead of this-turn-only pins / "
        "pin_uuids, so the resolver's own wrong rows for a shared debtor code are "
        "REPLACED by the picked uuid. Not fixture-visible: no corpus capture carries a "
        "carried pin over a debtor code shared by several distinct resolver rows; the "
        "182 graded disallowed-entity-gate captures are unchanged."
    ),
)


def find(node: str, fixture: str) -> Divergence | None:
    """The registered divergence covering this replay, or None."""
    for d in DIVERGENCES:
        if d.node == node and (d.fixture is None or d.fixture == fixture):
            return d
    return None


def strip(items: list, paths: tuple[tuple[str, ...], ...]) -> list:
    """Remove each path from every item's `json`. A path that is not there is a no-op."""
    for item in items:
        for path in paths:
            node = item.get("json") if isinstance(item, dict) else None
            for key in path[:-1]:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, dict):
                node.pop(path[-1], None)
    return items
