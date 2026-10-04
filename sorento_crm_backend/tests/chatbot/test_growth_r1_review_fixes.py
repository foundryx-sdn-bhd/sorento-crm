"""The review findings on `feat/chatbot-growth-data`, one test class each.

Every one of these is a defect the unit tests of the feature passed straight over, because
each lives in the SECOND rendering of the same data: the grouped shape carries the rows a
second time, the ladder carries the absence a second time, and the summary carries the
filter a second time. That is the shape worth remembering more than any individual fix.

  blocker 1  the restricted-field drop ran over `items` only, so `group_by` rendered the
             supplier a dealer must never see - and `group_by=supplier` put it in the
             SECTION HEADING, where no field filter could ever reach it (AC-907, D4).
  blocker 2  the ladder read `nothing_codes` without `can_state_absence`, so a stock turn
             that printed a warehouse breakdown could get "no stock and no incoming, but a
             PO is placed" appended underneath the stock it had just shown.
  should-fix 4  a grouped answer renumbered the rows on screen and left `answers` flat, so
             a positional pick resolved to a different record than the one numbered.
  should-fix 9  AC-922 asks for "no PO", the code said "no purchase order".

Blocker 3 (company scope on the two new ToolSpecs) is graded where the other scoped tools
are: `sorento_crm_mcp/tests/test_company_scope_params.py`.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.lanes.business.services import AnswerServices

SUPPLIER_PERM = "purchase_orders.supplier"


# --------------------------------------------------------------------------- #
# Blocker 1 - the restricted field, through the GROUPED shape.
# --------------------------------------------------------------------------- #


def _po_row(po: str, product: str, supplier: str) -> dict:
    return {
        "title": po,
        "fields": [
            {"key": "po_number", "label": "PO Number", "value": po},
            {"key": "product_code", "label": "Product Code", "value": product},
            {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 100},
            {"key": "supplier", "label": "Supplier", "value": supplier},
        ],
    }


def _po_envelope(*, group_by: str | None, with_summary: bool = False) -> dict:
    rows = [
        _po_row("PO-1", "C-FH14", "GUANGDONG WORKS"),
        _po_row("PO-2", "C-FH14", "FOSHAN METALS"),
    ]
    envelope: dict = {
        "result_type": "purchase_orders_placed",
        "intro": "Here is the PO placed I found.",
        "items": rows,
        "restricted_fields": {"supplier": SUPPLIER_PERM},
        "has_result": True,
    }
    if with_summary:
        # `summary_items` switches the renderer to SUMMARY-ONLY (a quantity ask prints no
        # rows), so it is opt-in here: every other case in this class needs the rows.
        envelope["summary_items"] = [
            {
                "title": "Summary",
                "fields": [
                    {"key": "po_placed_qty", "label": "PO placed", "value": 200},
                    {"key": "supplier", "label": "Supplier", "value": "GUANGDONG WORKS"},
                ],
            }
        ]
    if group_by == "supplier":
        envelope["groups"] = [
            {"key": "GUANGDONG WORKS", "label": "GUANGDONG WORKS", "items": [rows[0]]},
            {"key": "FOSHAN METALS", "label": "FOSHAN METALS", "items": [rows[1]]},
        ]
    elif group_by == "product":
        envelope["groups"] = [{"key": "C-FH14", "label": "C-FH14", "items": list(rows)}]
    return envelope


def _ctx(*, group_by: str | None = None, granted: list[str] | None = None) -> dict:
    semantic_input: dict = {}
    if group_by:
        semantic_input["group_by"] = group_by
    return {
        "semantic_input": semantic_input,
        "access": {"attributes": granted},
    }


class TestBlocker1SupplierNeverLeaksThroughGroupBy:
    def test_a_dealer_grouping_by_product_sees_no_supplier(self) -> None:
        """The grouped rows are a SECOND copy of the same fields, and the first cut
        filtered only the flat `items` - so every dealer grouping a PO answer read every
        supplier."""
        out = fetch.output_structurer(_po_envelope(group_by="product"), _ctx(group_by="product"))
        assert "GUANGDONG WORKS" not in out["response"]
        assert "FOSHAN METALS" not in out["response"]
        assert "PO-1" in out["response"] and "C-FH14" in out["response"]

    def test_a_dealer_grouping_by_supplier_gets_an_ungrouped_answer(self) -> None:
        """The heading IS the restricted value, so redacting fields cannot help: the axis
        is refused and the answer rendered flat."""
        envelope = _po_envelope(group_by="supplier")
        out = fetch.output_structurer(envelope, _ctx(group_by="supplier"))
        assert "GUANGDONG WORKS" not in out["response"]
        assert "FOSHAN METALS" not in out["response"]
        assert "PO-1" in out["response"] and "PO-2" in out["response"]
        assert envelope["group_by_dropped"] == "supplier"
        assert envelope["groups"] == []

    def test_the_summary_item_is_filtered_too(self) -> None:
        out = fetch.output_structurer(_po_envelope(group_by=None, with_summary=True), _ctx())
        assert "GUANGDONG WORKS" not in out["response"]
        assert "PO placed" in out["response"]

    def test_a_granted_contact_sees_the_supplier_and_keeps_the_grouping(self) -> None:
        """The other half of the gate: with the grant, both the field and the axis come
        back. A rule that only ever hides is not a gate, it is a deletion."""
        envelope = _po_envelope(group_by="supplier")
        out = fetch.output_structurer(
            envelope, _ctx(group_by="supplier", granted=[SUPPLIER_PERM])
        )
        assert "GUANGDONG WORKS" in out["response"]
        assert "FOSHAN METALS" in out["response"]
        assert "group_by_dropped" not in envelope
        assert len(envelope["groups"]) == 2

    def test_a_granted_contact_grouping_by_product_sees_the_supplier_field(self) -> None:
        out = fetch.output_structurer(
            _po_envelope(group_by="product"), _ctx(group_by="product", granted=[SUPPLIER_PERM])
        )
        assert "GUANGDONG WORKS" in out["response"]

    def test_an_unrestricted_axis_is_never_refused(self) -> None:
        """`group_by=product` is not in `restricted_fields`, so the axis stands whatever
        the grant is - the refusal keys on the restriction, never on grouping itself."""
        envelope = _po_envelope(group_by="product")
        fetch.output_structurer(envelope, _ctx(group_by="product"))
        assert "group_by_dropped" not in envelope
        assert len(envelope["groups"]) == 1


# --------------------------------------------------------------------------- #
# should-fix 4 - a positional pick after a grouped answer.
# --------------------------------------------------------------------------- #


class TestShouldFix4GroupedAnswersMatchTheNumbering:
    def test_answers_are_in_the_order_the_message_numbered_them(self) -> None:
        """Rows A, B, A grouped by product renumber as A, A, B on screen. `answers` is what
        a positional pick resolves against, so "2" must be the SECOND ROW PRINTED."""
        def _row(po: str, product: str) -> dict:
            return {
                "title": po,
                "fields": [
                    {"key": "po_number", "label": "PO Number", "value": po},
                    {"key": "product_code", "label": "Product Code", "value": product},
                ],
            }

        a1, b1, a2 = _row("PO-1", "AAA"), _row("PO-2", "BBB"), _row("PO-3", "AAA")
        envelope = {
            "result_type": "purchase_orders_placed",
            "intro": "Here is the PO placed I found.",
            "items": [a1, b1, a2],
            "groups": [
                {"key": "AAA", "label": "AAA", "items": [a1, a2]},
                {"key": "BBB", "label": "BBB", "items": [b1]},
            ],
            "has_result": True,
        }
        out = fetch.output_structurer(envelope, _ctx(group_by="product"))
        assert [a["title"] for a in out["answers"]] == ["PO-1", "PO-3", "PO-2"]
        # And that really is the printed order, not just a list we like the look of.
        response = out["response"]
        assert response.index("PO-1") < response.index("PO-3") < response.index("PO-2")

    def test_an_ungrouped_answer_is_untouched(self) -> None:
        rows = [_po_row("PO-1", "C-FH14", "X"), _po_row("PO-2", "C-FH14", "Y")]
        envelope = {
            "result_type": "purchase_orders_placed",
            "intro": "Here is the PO placed I found.",
            "items": rows,
            "has_result": True,
        }
        out = fetch.output_structurer(envelope, _ctx())
        assert out["answers"] is envelope["items"]


# --------------------------------------------------------------------------- #
# Blocker 2 - the ladder and `can_state_absence`.
# --------------------------------------------------------------------------- #

DEFAULT_LADDER = {"inventory": ["incoming", "purchase_order"], "incoming": ["inventory"]}
NO_PO_LADDER = {"inventory": ["incoming"], "incoming": ["inventory"]}
PO_TOOL = "crm_procurement_po_placed_list"
INCOMING_TOOL = "crm_incoming_stock_list"

PARSER = {
    "message_type": "business_query",
    "intent_hint": "check_stock",
    "domain_hint": "inventory",
    "user_goal": "check stock for SRTWT7445-LV-NEW",
    "access_levels": [],
    "routing": {"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
}
CODE = "SRTWT7445-LV-NEW"
UUID = "11111111-1111-1111-1111-111111111111"

WAREHOUSE_BREAKDOWN = {
    # The render `can_state_absence` exists for: it ANSWERED (has rows, `has_result`), and
    # it named warehouses rather than the product code. "The render did not echo this code"
    # is therefore NOT the same statement as "this code has nothing".
    "answers": [
        {"fields": [{"label": "Warehouse", "value": "MAIN"}, {"label": "Quantity On Hand", "value": 40}]},
        {"fields": [{"label": "Warehouse", "value": "KL2"}, {"label": "Quantity On Hand", "value": 12}]},
    ],
    "has_result": True,
    "response": "MAIN: 40\nKL2: 12",
}
TOTAL_MISS = {
    # Nothing came back at all, so absence IS statable.
    "answers": [{"fields": [{"label": "Product Code", "value": "SRTOTHER"}]}],
    "response": "Some other stock line.",
}
PO_ROWS = {
    "answers": [
        {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": CODE},
                {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 1000},
                {"key": "expected_date", "label": "Expected Date", "value": "2026-06-01"},
            ]
        }
    ],
    "has_result": True,
}
NO_ROWS = {"answers": [], "has_result": False}


def _run_ladder(*, validator, ladder, po_response=NO_ROWS, parser=None):
    """`run_crossdomain` end to end, the same seam `test_crossdomain_ladder.py` uses.

    Every input is DEEP-COPIED. `run_crossdomain` mutates what it is handed (the render is
    built over the validator and the rung stamps the parser's routing), and the module-level
    fixtures below are shared - one test's mutation reached the next one's assertions until
    this copy existed, which is a fixture bug that reads exactly like a code bug.
    """
    import copy

    calls: list[str] = []
    parser = copy.deepcopy(PARSER) if parser is None else parser
    validator = copy.deepcopy(validator)
    po_response = copy.deepcopy(po_response)

    def mcp_probe(name: str, args: dict) -> dict:
        calls.append(name)
        return NO_ROWS if name == INCOMING_TOOL else po_response

    result = answer_mod.run_crossdomain(
        validator,
        parser=parser,
        resolved={
            "resolutions": [
                {
                    "token": CODE,
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": CODE,
                            "uuid": UUID,
                            "match_tier": "exact",
                        }
                    ],
                }
            ]
        },
        session_block={"session_vars": {"variables": {}}},
        entities_names=None,
        services=AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []}),
        contact_id="900000008",
        space_id="364817",
        crossdomain_ladder=ladder,
        granted=["purchase_orders.placed"],  # the PO rung is per contact (8 Sep 2026)
    )
    return result, calls


class TestBlocker2TheLadderRespectsCanStateAbsence:
    @pytest.mark.parametrize(
        "ladder", [None, {}, NO_PO_LADDER, DEFAULT_LADDER],
        ids=["no-ladder", "empty", "no-po-rung", "489-default"],
    )
    def test_a_warehouse_breakdown_never_reaches_the_po_rung(self, ladder) -> None:
        """PARAMETRISED over the ladder, deliberately: the pre-fix guard tests all ran with
        no ladder configured, so none of them could have caught a rung that fires only when
        one IS - and migration 489 ships a `purchase_order` rung on `inventory` for every
        tenant, so the default is the case that matters."""
        result, calls = _run_ladder(
            validator=WAREHOUSE_BREAKDOWN, ladder=ladder, po_response=PO_ROWS
        )
        block = result["render"]["_xdBlock"]
        assert PO_TOOL not in calls, "the ladder probed a turn that cannot state absence"
        assert block["nothing_codes"] == []
        assert block["nothing_missing"] == []
        assert "but PO is placed" not in (block["block"] or "")
        assert "No stock" not in (block["block"] or "")

    def test_a_total_miss_still_reaches_the_rung(self) -> None:
        """The other side of the gate: the fix must not turn the feature off."""
        result, calls = _run_ladder(
            validator=TOTAL_MISS, ladder=DEFAULT_LADDER, po_response=PO_ROWS
        )
        assert PO_TOOL in calls
        assert "but PO is placed" in result["render"]["_xdBlock"]["block"]


class TestShouldFix89TheRungSentenceAndTeam:
    def test_ac922_says_no_po_not_no_purchase_order(self) -> None:
        result, _ = _run_ladder(validator=TOTAL_MISS, ladder=DEFAULT_LADDER, po_response=NO_ROWS)
        block = result["render"]["_xdBlock"]["block"]
        assert f"No stock, no incoming and nothing on order for {CODE}." in block
        assert "no purchase order" not in block

    def test_the_rung_never_overrides_the_turns_escalation_team(self) -> None:
        """Owner ruling 22 Sep 2026, R6 (AC-EQ-5) retires the 8 Sep H64 fix this test used
        to pin: a stock-origin ask is ALWAYS suggested to the warehouse team, whichever
        rung answered it - never re-pointed to purchasing just because a PO answered.
        `block["team"]` and `parser["routing"]["suggested_team"]` both stay exactly what
        `crossdomain_zeroset` set them to; the rung stamps neither any more."""
        parser = {**PARSER, "routing": {"suggested_team": "warehouse", "suggested_agent": None}}
        result, _ = _run_ladder(
            validator=TOTAL_MISS, ladder=DEFAULT_LADDER, po_response=PO_ROWS, parser=parser
        )
        block = result["render"]["_xdBlock"]
        assert "but PO is placed" in block["block"]
        # the offer itself is compose's (8 Sep 2026); the TEAM it will name is the block's
        assert "escalate" not in block["block"].lower()
        assert block["team"] == "warehouse"
        assert parser["routing"]["suggested_team"] == "warehouse"
        assert "crossdomain_rung_team" not in parser

    def test_a_rung_that_never_fires_leaves_the_routing_alone(self) -> None:
        parser = {**PARSER, "routing": {"suggested_team": "warehouse", "suggested_agent": None}}
        _run_ladder(
            validator=WAREHOUSE_BREAKDOWN,
            ladder=DEFAULT_LADDER,
            po_response=PO_ROWS,
            parser=parser,
        )
        assert parser["routing"]["suggested_team"] == "warehouse"
        assert "crossdomain_rung_team" not in parser


# --------------------------------------------------------------------------- #
# nit 10 - a blank `requested_attributes` entry shifted the miss label.
# --------------------------------------------------------------------------- #


class TestNit10ABlankRequestedAttributeDoesNotShiftTheLabel:
    def test_the_miss_sentence_names_the_word_that_missed(self) -> None:
        envelope = {
            "result_type": "products",
            "intro": "Here are the products I found.",
            "items": [
                {
                    "title": "SRTKT73SS",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTKT73SS"},
                        {"key": "spec:steel_grade", "label": "Steel grade", "value": "304"},
                    ],
                }
            ],
            "spec_vocabulary": {"steel_grade": "Steel grade"},
            "has_result": True,
        }
        # A blank entry between two real ones: the parallel-list lookup used to read
        # `req_attrs[index_in_the_filtered_list]` and name the WRONG attribute.
        out = fetch.output_structurer(
            envelope,
            {"semantic_input": {"requested_attributes": ["  ", "steel grade", "flange width"]}},
        )
        response = out["response"]
        assert "304" in response
        assert "*flange width:* not recorded for SRTKT73SS" in response
        assert "no steel grade recorded" not in response


# --------------------------------------------------------------------------- #
# Owner report, 8 Sep 2026 (the :8080 hands-on run). Six classes lived here, all
# built on the now-deleted `head/output_exchange.py::output_exchange`/
# `_switch_word_domain` (AC-1592, AC-1594). Queue item 2 (chatbot-turn-rearch,
# 16 Sep 2026): each measured against the CURRENT engine, not guessed - RETIRE
# or PORT, one rule named per class, nothing silently dropped.
#
# * **`TestD10AnIncomingAskTypesTheCodeAsAProduct`** - RETIRED. D10's own fix
#   (a container-hinted token that only resolves to a product is retyped) lives
#   now at `resolve_gate.retype_shipment_miss`, comprehensively covered by
#   `test_resolve_gate_unit.py::TestAShipmentHintedTokenThatIsOnlyAProductIsRetyped`
#   (9 tests, including this exact D10 turn and its "real shipment kept"/"genuine
#   miss left alone" negatives) - a duplicate, not a coverage hole.
#
# * **`TestOwner8SepPOAskTypesTheCodeAsAProduct`** (defect 1) - RETIRED. Measured
#   this session: `fetch.py`'s tool-param builder reads `entity_type` off the
#   GATE's `compatible_entities` (the resolver's own DB-matched type), never the
#   parser's `hint` field (grep-confirmed: `TYPE_TO_PARAM.get(entity_type)` at
#   the param-building loop). The old defect existed because the OLD pipeline's
#   fetch trusted the parser's hint; the new resolver-truth-first gate makes an
#   `order`-hinted product code answer as a product regardless, by construction -
#   structurally obsolete, not a regression to re-prove. The "carried scope
#   refused" half of the old class (a turn whose only current entity fails to
#   resolve falling back to a stale carried product) was NOT independently
#   re-verified this session - flagged, not silently assumed fixed.
#
# * **`TestOwner8SepANewAskIsNeverAnEscalationYes`** (defect 2) and
#   **`TestOwner8SepADeliveryWordPlusANameIsAnOrderAsk`** + **`TestReviewRound2B2
#   TheRetypeIsTheMeasuredArmOnly`** (defect 3, folded together - both exercise
#   the same switch-word-widened-guard mechanism) - PORTED AS RED in
#   `test_rearch_port_growth_r1_review_fixes.py`, against `turn/apply.py::apply`
#   + `turn/route.py::route` (the real APPLY/ROUTE seam, same pattern
#   `test_rearch_port_route_unit.py` already established for this file's sibling
#   port). Measured this session, not guessed: `_answer_offer` reads
#   `escalation.is_escalation_confirmation` with no defusing check for a decisive
#   intent plus a current entity (defect 2 reproduces), and `turn/apply.py` has
#   NO `switch_word`/`DOMAIN_SWITCH_WORDS` reference anywhere (grepped) - a cold
#   "delivery to hanlim" routes `out_of_scope` and the same turn over an open
#   offer re-asks `escalate_offer`, neither ever reaching `business_query`
#   (defect 3 reproduces). Confirmed regressions, not this tester's fix to make.
#
# * **`TestSwitchWordDomainOfThisMessage`** - RETIRED. Direct unit test of
#   `_switch_word_domain`, a function that no longer exists anywhere (module
#   deleted) - its own behaviour gap is what the ported defect-3 RED tests above
#   assert instead of a standalone test of a dead function.
# --------------------------------------------------------------------------- #


class TestD9AFileLinkThatCannotBeSignedIsLeftOut:
    """D9 (owner console pass, 8 Sep 2026, turn 8f4356a3 "cwsp124 technical drawing")."""

    def test_a_document_tool_timeout_is_an_answerable_absence(self) -> None:
        from app.services.chatbot.lanes.business import _fetch_failure_outcome

        assert _fetch_failure_outcome("crm_master_product_attachments_list", TimeoutError("timed out")) == "not_found"
        assert _fetch_failure_outcome("crm_resource_attachments_list", RuntimeError("MCP call timed out after 10s")) == "not_found"
        # a broken read is still a hard failure, and a stock read never becomes an absence
        assert _fetch_failure_outcome("crm_master_product_attachments_list", RuntimeError("500 from the CRM")) is None
        assert _fetch_failure_outcome("crm_inventory_stock_balance_list", TimeoutError("timed out")) is None

    def test_a_file_with_no_link_is_never_sent(self) -> None:
        from app.services.chatbot.engine import _clean_attachments

        source = {
            "items": [],
            "attachments": [
                {"url": "https://cdn/a.pdf", "filename": "a.pdf", "mimeType": "application/pdf", "attachmentType": "Drawing"},
                {"url": None, "filename": "b.pdf", "mimeType": "application/pdf", "attachmentType": "Drawing"},
                {"filename": "c.pdf"},
            ],
        }
        out = _clean_attachments(source)
        assert [a["filename"] for a in out["attachments"]] == ["a.pdf"]

    def test_the_reply_names_both_files_while_the_send_list_has_one(self) -> None:
        """End to end through `output_structurer` (the reply text) and `_attachments_src`
        (the send list): a product with two files, one unsignable, is answered whole and
        sent partial."""
        from app.services.chatbot.engine import _attachments_src

        envelope = {
            "result_type": "product_attachments",
            "intro": "Here are the product files I found.",
            "items": [
                {
                    "title": "CWSP124",
                    "fields": [
                        {"label": "Product Code", "value": "CWSP124"},
                        {"label": "File Name", "value": "CWSP124-drawing.pdf"},
                        {"key": "file_link", "label": "File Link", "value": "(file link unavailable right now)"},
                    ],
                },
                {
                    "title": "CWSP124",
                    "fields": [
                        {"label": "Product Code", "value": "CWSP124"},
                        {"label": "File Name", "value": "CWSP124.jpg"},
                    ],
                },
            ],
            "attachments": [
                {"url": None, "filename": "CWSP124-drawing.pdf", "mimeType": "application/pdf", "attachmentType": "Technical Drawing"},
                {"url": "https://cdn.test.invalid/x/CWSP124.jpg", "filename": "CWSP124.jpg", "mimeType": "image/jpeg", "attachmentType": "Product Photos"},
            ],
            "has_result": True,
        }
        out = fetch.output_structurer(envelope, {})
        assert "CWSP124-drawing.pdf" in out["response"]
        assert "CWSP124.jpg" in out["response"]
        assert "(file link unavailable right now)" in out["response"]

        cleaned = _attachments_src({"outcome_fragment": {"central-exchange": out}})
        assert [a["filename"] for a in cleaned["attachments"]] == ["CWSP124.jpg"]
