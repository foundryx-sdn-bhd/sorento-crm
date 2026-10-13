"""A7 - cross-domain ladder (AC-920 to AC-924).

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`documentation/plans/chatbot/chatbot-growth-r1-acceptance-criteria.md` section B.

`run_crossdomain`'s existing inventory<->incoming hard pair is unchanged (AC-920); this
file covers what happens AFTER it finds nothing for a code - the new purchase_order rung,
gated by `system_settings.chatbot_crossdomain_ladder` (a plain dict here, never a DB
session; see `answer.py`'s own "no session held" rule).
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.lanes.business.answer import run_crossdomain
from app.services.chatbot.lanes.business.services import AnswerServices

_PO_TOOL = "crm_procurement_po_placed_list"
_INCOMING_TOOL = "crm_incoming_stock_list"

_PARSER = {
    "message_type": "business_query",
    "intent_hint": "check_stock",
    "domain_hint": "inventory",
    "user_goal": "check stock for SRTWC8517",
    "access_levels": [],
}


def _resolved_for(code: str, uuid: str) -> dict:
    return {
        "resolutions": [
            {
                "token": code,
                "matches": [
                    {
                        "entity_type": "product",
                        "canonical_code": code,
                        "uuid": uuid,
                        "match_tier": "exact",
                    }
                ],
            }
        ]
    }


def _validator_result(other_code: str = "SRTOTHER") -> dict:
    """A stock reply that never mentions the code under test - `crossdomain_zeroset`
    reads this as "asked for but not returned"."""
    return {
        "answers": [{"fields": [{"label": "Product Code", "value": other_code}]}],
        "response": "Some other stock line.",
    }


def _po_row(
    qty: Any,
    date: str | None,
    *,
    code: str = "SRTWC8517",
    po_number: str = "PO-1001",
    po_date: str | None = "2026-05-01",
    location: str | None = "KL-WH",
    kind: str | None = "po",
) -> dict:
    """The MCP presenter's shape after the 11 Sep 2026 ruling: `kind` is a TOP-LEVEL item
    key ("po"/"spo"), never a rendered field - Source and Expected Date no longer render
    at all. `date` (the old expected-date argument) is kept only so every existing call
    site still parses; it is genuinely irrelevant to the rung's own text now, same as
    before this ruling."""
    fields = [
        {"key": "po_number", "label": "PO Number", "value": po_number},
        {"key": "product_code", "label": "Product Code", "value": code},
        {"key": "ordered_qty", "label": "Ordered Qty", "value": qty},
        {"key": "outstanding_qty", "label": "Outstanding Qty", "value": qty},
    ]
    if po_date is not None:
        fields.append({"key": "po_date", "label": "PO Date", "value": po_date})
    if location is not None:
        fields.append({"key": "location", "label": "Location", "value": location})
    if date is not None:
        fields.append({"key": "expected_date", "label": "Expected Date", "value": date})
    item: dict[str, Any] = {"fields": fields}
    if kind is not None:
        item["kind"] = kind
    return item


def _row_block(
    *, code: str = "SRTWC8517", qty: Any, po_date: str | None = "2026-05-01",
    location: str | None = "KL-WH", number: str | None = "PO-1001", label: str = "PO",
) -> str:
    """The 11 Sep 2026 ruling's per-row lines (card v4: one block per PO, named by its number, owner ruling 4 Oct 2026), bold-labelled per the 12 Sep 2026
    ruling (AC-4/AC-5, finding 3): one field per line, `*PO date:*` and
    `*Location:*` omitted when null. Matches `_po_row`'s own defaults so a test
    only names what it overrides."""
    lines = [f"*{label}:* {number}"] if number else []
    lines += [f"*Ordered:* {qty}", f"*Outstanding:* {qty}"]
    if po_date not in (None, ""):
        lines.append(f"*PO date:* {po_date}")
    if location not in (None, ""):
        lines.append(f"*Location:* {location}")
    return "\n".join(lines)


def _stock_row(qty: Any, *, code: str = "SRTWC8517", warehouse: str = "KL-WH") -> dict:
    """A stock-balance row shaped like the real MCP presenter (`quantity_on_hand` key,
    `Quantity On Hand` label - see `sorento_crm_mcp/presenters.py`)."""
    return {
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": qty},
            {"key": "warehouse", "label": "Warehouse", "value": warehouse},
        ]
    }


def _stock_envelope(rows: list[dict], *, has_result: bool = True) -> dict:
    return {"answers": rows, "has_result": has_result, "response": "stock details"}


GRANTED = ["purchase_orders.placed"]


def _run(
    *,
    ladder: dict[str, list[str]] | None,
    incoming_response: dict,
    po_response: dict | None = None,
    code: str = "SRTWC8517",
    uuid: str = "prod-uuid-1",
    granted: list[str] | None = GRANTED,
    parser: dict | None = None,
    validator: dict | None = None,
) -> tuple[dict, list[tuple[str, dict]]]:
    calls: list[tuple[str, dict]] = []

    def mcp_probe(name: str, args: dict) -> dict:
        calls.append((name, args))
        if name in (_INCOMING_TOOL, _STOCK_TOOL):
            # the first probe: the OTHER domain's tool (incoming from a stock ask, stock
            # from an incoming ask) - `incoming_response` is its answer either way
            return incoming_response
        if name == _PO_TOOL:
            return po_response if po_response is not None else {"answers": [], "has_result": False}
        raise AssertionError(f"unexpected probe tool: {name}")

    services = AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []})
    result = run_crossdomain(
        validator if validator is not None else _validator_result(),
        parser=parser or _PARSER,
        resolved=_resolved_for(code, uuid),
        session_block={"session_vars": {"variables": {}}},
        entities_names=None,
        services=services,
        contact_id="164838271",
        space_id="900001",
        crossdomain_ladder=ladder,
        granted=granted,
    )
    return result, calls


def _composed_text(result: dict, *, answered: bool = True, miss_offer: bool = False) -> str:
    """Run the tail's ONE offer writer (`crossdomain_compose`) over the lane's block, the
    way `engine.py` does, and return the customer-visible text."""
    from app.services.chatbot.tail.compose import crossdomain_compose

    miss = "Here's what you want:\n\u2022 product: SRTWC8517\n\nBut no inventory matched these."
    if miss_offer:
        miss += " Would you like me to escalate to warehouse team?"
    item = {
        "reply": {
            "session_patch": {
                "user_response": miss,
                "variables": {"last_result_set": [{"code": "SRTOTHER"}], "response": "Previous turn"},
            }
        }
    }
    out = crossdomain_compose(
        item, result={"result": {"xd": {"block": result["render"]["_xdBlock"]}}}, answered=answered
    )
    reply = out["reply"]
    return reply.get("text") or reply["session_patch"]["user_response"]


_LADDER_WITH_PO = {"inventory": ["incoming", "purchase_order"], "incoming": ["inventory"]}
_LADDER_NO_PO = {"inventory": ["incoming"], "incoming": ["inventory"]}
# D7 (migration 491): the shipped ladder climbs to PO from either side.
_LADDER_491 = {"inventory": ["incoming", "purchase_order"], "incoming": ["inventory", "purchase_order"]}
_STOCK_TOOL = "crm_inventory_stock_balance_list"
_INCOMING_PARSER = {**_PARSER, "intent_hint": "check_incoming", "domain_hint": "incoming",
                    "user_goal": "check incoming for SRTWC8517"}


class TestAC920IncomingHasRowsUnchanged:
    def test_incoming_answers_no_po_probe_at_all(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={
                "answers": [
                    {"fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-15"},
                    ]}
                ],
                "has_result": True,
            },
        )
        tool_names = [name for name, _ in calls]
        assert _PO_TOOL not in tool_names, "incoming answered - the PO rung must never run"
        block = result["render"]["_xdBlock"]["block"]
        assert "SRTWC8517" in block and "2026-09-15" in block


class TestAC921StockMissIncomingMissPOPlaced:
    def test_po_rung_renders_the_placed_line_and_escalate_offer(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        tool_names = [name for name, _ in calls]
        assert tool_names == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert "*Stock:* none\n*Incoming:* none\n*PO:* PO-1001" in block
        assert "*PO:* PO-1001" in block
        # Owner ruling, 11 Sep 2026: one field per line, no per-document heading naming
        # PO-1001, no "pcs", no expected date.
        assert f"{_row_block(qty=50)}" in block
        # The rung writes NO offer: `crossdomain_compose` is the one writer (turns
        # 0184d84d / 5f73ddb0 / 90a1637a carried the question twice).
        assert "escalate" not in block.lower()
        # Owner ruling 22 Sep 2026, R6 (AC-EQ-5): a stock-origin ask is always suggested
        # to the warehouse team, no PO-rung override - the PO rung only writes what
        # answered the "no stock" line, never the routing team.
        assert result["render"]["_xdBlock"]["team"] == "warehouse"
        # AC-921: never the supplier, dealer or not - the PO rung's own template has no
        # supplier slot at all (see `_crossdomain_rung_rows`).
        assert "supplier" not in block.lower()
        assert "acme" not in block.lower()


class TestAC922StockMissIncomingMissPOMiss:
    def test_no_po_either_renders_the_three_way_miss(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
        )
        tool_names = [name for name, _ in calls]
        assert tool_names == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        # AC-922's own wording: "no PO", the customer's two letters and the same word the
        # question used - not `rung.replace("_", " ")`, which spelled it out (review, item 9).
        # Item 5 (8 Sep 2026): the rung reads PO lines AND unshipped SPO allocations, so
        # the three-way miss says "nothing on order" - the customer's question, not a
        # document type (was "no PO for").
        assert "*Stock:* none\n*Incoming:* none\n*PO:* none" in block
        assert "no purchase order" not in block and "no PO for" not in block
        assert "escalate" not in block.lower()  # compose is the one offer writer


class TestAC923LadderReadFromSetting:
    def test_tenant_ladder_with_no_po_rung_never_probes_po(self) -> None:
        result, calls = _run(
            ladder=_LADDER_NO_PO,
            incoming_response={"answers": [], "has_result": False},
        )
        tool_names = [name for name, _ in calls]
        assert tool_names == [_INCOMING_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        # The pre-A7 wording, unchanged: no PO rung configured, no PO rung run.
        assert "*Stock:* none\n*Incoming:* none" in block

    def test_no_ladder_at_all_is_the_pre_a7_single_probe(self) -> None:
        """`crossdomain_ladder=None` (a caller that never read the setting) behaves
        exactly as before A7 - one probe, the existing wording."""
        result, calls = _run(ladder=None, incoming_response={"answers": [], "has_result": False})
        tool_names = [name for name, _ in calls]
        assert tool_names == [_INCOMING_TOOL]
        assert "*Stock:* none\n*Incoming:* none" in result["render"]["_xdBlock"]["block"]


class TestAC924ThirdCodeNeverDeclaredAbsentWithoutBeingAsked:
    def test_a_code_the_probe_never_asked_about_is_not_named(self) -> None:
        """H62 kept: `nothing` only ever names a code that HAD a uuid (was actually
        probed). A code with no uuid at all must never appear in the PO rung's message
        either - the rung reads `nothing_missing`, which `crossdomain_render` already
        filters the same way."""
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        out = crossdomain_render(
            {"items": [], "has_result": True},
            zeroset={
                "active": True,
                "origin_domain": "inventory",
                "team": "warehouse",
                "missing": [
                    {"code": "SRTWC8517", "_n": "SRTWC8517", "uuid": "u1"},
                    # no uuid at all - never probed, must never be named as "nothing"
                    {"code": "UNPROBED-CODE", "_n": "UNPROBED-CODE", "uuid": None},
                ],
            },
            validator={},
        )
        assert out["_xdBlock"]["nothing_codes"] == ["SRTWC8517"]
        assert "UNPROBED-CODE" not in out["_xdBlock"]["block"]


class TestOwner12SepNoPhantomAttachmentSentence:
    """AC-3 (12 Sep 2026, finding 2): the cross-domain block never says "I have
    attached the file(s) below." - nothing downstream ever sends the file
    (`tail/compose.crossdomain_compose` folds only `block["block"]` text into the
    reply, and the send lane reads attachments off the PRIMARY answer alone), so
    the sentence has never been true. Byte-identical otherwise, attachments or
    not."""

    _PHRASE = "I have attached the file(s) below."

    def _block(self, *, attachments) -> str:
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        probe_result: dict[str, Any] = {
            "items": [
                {"fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 5},
                ]}
            ],
            "has_result": True,
        }
        if attachments is not None:
            probe_result["attachments"] = attachments
        zeroset = {
            "active": True,
            "origin_domain": "inventory",
            "team": "warehouse",
            "missing": [{"code": "SRTWC8517", "_n": "SRTWC8517", "uuid": "u1"}],
        }
        out = crossdomain_render(probe_result, zeroset=zeroset, validator={})
        return out["_xdBlock"]["block"]

    def test_no_sentence_when_the_probe_carries_attachments_and_rows_render(self) -> None:
        block = self._block(
            attachments=[{"url": "https://example.com/x.pdf", "filename": "x.pdf"}]
        )
        assert self._PHRASE not in block
        assert "SRTWC8517" in block
        assert "*Quantity On Hand:* 5" in block

    def test_the_block_is_byte_identical_with_and_without_attachments(self) -> None:
        with_files = self._block(
            attachments=[{"url": "https://example.com/x.pdf", "filename": "x.pdf"}]
        )
        without_files = self._block(attachments=None)
        assert with_files == without_files
        assert self._PHRASE not in with_files


class TestAC911SPOAllocationDomainNoLongerUnsupported:
    def test_spo_allocation_removed_goods_receive_stays(self) -> None:
        """AC-911: `spo_allocation` is supported; `goods_receive` still is not -
        `crm_procurement_spo_allocations_last_receipt_list` (A6) answers "last in" now,
        nothing in this plan reads GRN data.

        AC-1592 port: `head.route.DEFAULT_UNSUPPORTED_DOMAINS` is deleted. The same
        fact now lives on the `Policy` object's per-domain `supported` flag, fed by
        `chatbot_domains` (`turn/policy_rows.py::DEFAULT_DOMAIN_ROWS` is the same seed
        data a migration and a blank-schema fixture both fall back to, per
        `load_policy`'s own docstring)."""
        from app.services.chatbot.turn.policy import Policy
        from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS, DEFAULT_KIND_ROWS

        policy = Policy.from_rows(
            domains=[dict(row) for row in DEFAULT_DOMAIN_ROWS],
            kinds=[dict(row) for row in DEFAULT_KIND_ROWS],
            tier_order=["dealer", "office", "end_user"],
        )

        assert policy.domain("spo_allocation").supported is True
        assert policy.domain("goods_receive").supported is False


class TestOwner8SepTheOfferIsWrittenOnce:
    """Turns 0184d84d / 5f73ddb0 / 90a1637a (8 Sep 2026): "...27 pcs expected 2027-02-01
    Would you like me to escalate to purchasing team?\n\nWould you like me to escalate to
    purchasing team?" - the rung appended the offer into the block AND `crossdomain_compose`
    appended it again from `block["team"]`. Compose is the one writer, on every shape.

    `test_rung_found` used to pin the PO rung overriding `block["team"]` to "purchasing"
    for a stock-origin ask - owner ruling 22 Sep 2026, R6 (AC-EQ-5) retired that override:
    a stock question is always suggested to the warehouse team, whichever rung answered.
    """

    _PHRASE = "Would you like me to escalate"

    def test_rung_found(self) -> None:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        text = _composed_text(result)
        assert text.count(self._PHRASE) == 1
        # Owner ruling 22 Sep 2026, R6 (AC-EQ-5): a stock-origin ask always offers the
        # warehouse team, even when the PO rung is what answered.
        assert "escalate to warehouse team?" in text

    def test_rung_nothing(self) -> None:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
        )
        text = _composed_text(result)
        assert text.count(self._PHRASE) == 1
        assert "*Stock:* none\n*Incoming:* none\n*PO:* none" in text

    def test_first_probe_nothing(self) -> None:
        result, _ = _run(ladder=_LADDER_NO_PO, incoming_response={"answers": [], "has_result": False})
        text = _composed_text(result)
        assert text.count(self._PHRASE) == 1
        assert "escalate to warehouse team?" in text

    def test_total_miss_keeps_the_miss_sentence_own_offer(self) -> None:
        """The other compose branch: the miss sentence already carries the phrase and the
        block goes above it - still exactly one."""
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        text = _composed_text(result, answered=False, miss_offer=True)
        assert text.count(self._PHRASE) == 1


class TestD11ThePORungLineIsStructuredFields:
    """Owner ruling, 11 Sep 2026 (retires D11/D14's `Qty {outstanding_qty} placed on
    {document_date}` line shape): one field per line - Product Code, Ordered, Outstanding,
    PO date (if any), Location (if any) - no per-document heading naming the PO/SPO
    number, no "pcs", no expected/ETA date (the owner: it is not accurate)."""

    def test_the_line_is_the_structured_field_block(self) -> None:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(12, "2027-01-01")], "has_result": True},
        )
        block = result["render"]["_xdBlock"]["block"]
        assert f"{_row_block(qty=12)}" in block
        assert "expected" not in block and "pcs" not in block

    def test_an_absent_po_number_omits_the_heading_line(self) -> None:
        row = _po_row(12, "2026-07-01", po_date=None)
        row["fields"] = [f for f in row["fields"] if f["key"] != "po_number"]
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [row], "has_result": True},
        )
        assert f"{_row_block(qty=12, po_date=None, number=None)}" in result["render"]["_xdBlock"]["block"]

    def test_the_po_date_line_is_omitted_when_the_document_has_no_date(self) -> None:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(12, "2026-07-01", po_date=None)], "has_result": True},
        )
        block = result["render"]["_xdBlock"]["block"]
        assert f"{_row_block(qty=12, po_date=None)}" in block
        assert "PO date" not in block and "placed on" not in block
        assert "2026-07-01" not in block  # the (irrelevant) expected date never renders

    def test_the_location_line_is_omitted_when_the_row_has_no_location(self) -> None:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(12, "2026-07-01", location=None)], "has_result": True},
        )
        block = result["render"]["_xdBlock"]["block"]
        assert f"{_row_block(qty=12, location=None)}" in block
        assert "Location" not in block


class TestOwner8SepThePORungIsPerContact:
    """On-order information is a per-contact reveal, key `purchase_orders.placed`. Without
    the grant the rung does not run at all: no probe, no PO lines, the pre-rung note and
    the single offer - byte-identical to the ladder-off shape."""

    def _off(self) -> dict:
        result, _ = _run(ladder=None, incoming_response={"answers": [], "has_result": False})
        return result["render"]["_xdBlock"]

    def test_with_the_grant_the_rung_runs(self) -> None:
        _, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            granted=["purchase_orders.placed", "inventory.sellable"],
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]

    def test_without_the_grant_no_probe_and_the_ladder_off_shape(self) -> None:
        for granted in ([], None, ["inventory.sellable"]):
            result, calls = _run(
                ladder=_LADDER_WITH_PO,
                incoming_response={"answers": [], "has_result": False},
                po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
                granted=granted,
            )
            assert [name for name, _ in calls] == [_INCOMING_TOOL], granted
            block = result["render"]["_xdBlock"]
            off = self._off()
            assert block["block"] == off["block"] and block["team"] == off["team"]
            assert "rung" not in block
            assert _composed_text(result).count("Would you like me to escalate") == 1


class TestItem5UnshippedSPOIsOnOrderFromTheSupplier:
    """Item 5 (8 Sep 2026): the PO placed tool now returns unshipped SPO allocations as
    rows of the same shape with `kind` = "spo"; the rung words them as stock on order from
    the supplier and picks its header by what the rows are."""

    def _block(self, rows: list[dict]) -> str:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": rows, "has_result": True},
        )
        return result["render"]["_xdBlock"]["block"]

    def test_an_spo_row_reads_on_order_from_supplier(self) -> None:
        block = self._block([_po_row(7, "2026-10-05", po_number="SPO-2026/09-0001", po_date="2026-08-20", kind="spo")])
        # Owner ruling 4 Oct 2026: an SPO allocation opens with its `*SPO:*` number.
        assert f"{_row_block(qty=7, po_date='2026-08-20', number='SPO-2026/09-0001', label='SPO')}" in block

    def test_spo_parts_are_omitted_when_null(self) -> None:
        block = self._block([_po_row(7, None, po_number="SPO-1", po_date=None, location=None, kind="spo")])
        assert f"{_row_block(qty=7, po_date=None, location=None, number='SPO-1', label='SPO')}" in block
        assert "dated" not in block and "expected" not in block
        assert "PO date" not in block and "Location" not in block

    def test_a_mixed_set_keeps_the_po_header_and_lines_follow_one_another(self) -> None:
        block = self._block([
            _po_row(50, "2026-07-01", kind="po"),
            _po_row(7, "2026-10-05", po_number="SPO-9", po_date="2026-08-20", kind="spo"),
        ])
        expected = (
            f"{_row_block(qty=50)}\n{_row_block(qty=7, po_date='2026-08-20', number='SPO-9', label='SPO')}"
        )
        assert expected in block

    def test_a_row_with_no_kind_is_read_as_a_po(self) -> None:
        """An older envelope (no `kind` field at all, top-level or rendered) is today's
        PO row."""
        block = self._block([_po_row(50, "2026-07-01", kind=None)])
        assert f"{_row_block(qty=50)}" in block


class TestOwner11SepTheRungRendersStructuredFieldsPerRow:
    """Owner ruling (11 Sep 2026): one field per line per row, rows separated by ONE blank
    line - `Product Code:`, `Ordered:`, `Outstanding:`, `PO date:` (lower-case d),
    `Location:`. A null/empty `po_date` or `location` OMITS that line entirely (never a
    hyphen placeholder)."""

    def _block(self, rows: list[dict]) -> str:
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": rows, "has_result": True},
        )
        return result["render"]["_xdBlock"]["block"]

    def test_two_rows_are_separated_by_one_blank_line(self) -> None:
        # `_run`'s resolved product code is the default "SRTWC8517" - both rows must
        # match it (`_crossdomain_rung_rows` groups by product code).
        block = self._block([
            _po_row(30, "2026-08-10", po_date="2026-08-10"),
            _po_row(9, "2026-09-01", po_number="PO-2002", po_date="2026-09-01"),
        ])
        expected_rows = _row_block(qty=30, po_date="2026-08-10")
        expected_rows_2 = _row_block(qty=9, po_date="2026-09-01", number="PO-2002")
        # Card v4: no blank line inside a code's block.
        assert f"{expected_rows}\n{expected_rows_2}" in block

    def test_a_null_po_date_omits_the_po_date_line_only(self) -> None:
        block = self._block([_po_row(30, "2026-08-10", po_date=None)])
        assert "" + _row_block(qty=30, po_date=None) in block
        assert "PO date" not in block
        # the other lines still print
        assert "*Ordered:* 30" in block and "*Outstanding:* 30" in block
        assert "*Location:*" in block  # location still defaults, only po_date is null here

    def test_a_null_location_omits_the_location_line_only(self) -> None:
        block = self._block([_po_row(30, "2026-08-10", location=None)])
        assert "" + _row_block(qty=30, location=None) in block
        assert "Location" not in block
        assert "*PO date:*" in block  # po_date still defaults, only location is null here

    def test_an_all_spo_probe_keeps_the_on_order_from_supplier_header_with_no_source_field(self) -> None:
        rows = [
            _po_row(7, "2026-10-05", po_number="SPO-9", po_date="2026-08-20", kind="spo"),
            _po_row(3, "2026-10-06", po_number="SPO-10", po_date="2026-08-21", kind="spo"),
        ]
        block = self._block(rows)
        assert "*SPO:* SPO-9" in block and "*SPO:* SPO-10" in block
        assert "Source" not in block

    def test_a_mixed_po_and_spo_probe_yields_but_po_is_placed(self) -> None:
        rows = [
            _po_row(50, "2026-07-01", kind="po"),
            _po_row(7, "2026-10-05", po_number="SPO-9", po_date="2026-08-20", kind="spo"),
        ]
        block = self._block(rows)
        assert "*PO:* PO-1001" in block and "*SPO:* SPO-9" in block

    def test_ordered_qty_none_omits_the_ordered_line_only(self) -> None:
        """Reviewer fix round (11 Sep 2026): `Ordered:` follows the SAME null rule as
        `PO date:` / `Location:` - a row with no `ordered_qty` field prints `Product
        Code:` straight into `Outstanding:`, no placeholder line between them."""
        item = {
            "fields": [
                {"key": "po_number", "label": "PO Number", "value": "PO-1001"},
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 30},
                {"key": "po_date", "label": "PO Date", "value": "2026-08-10"},
                {"key": "location", "label": "Location", "value": "KL-WH"},
            ],
            "kind": "po",
        }
        block = self._block([item])
        assert (
            "*Outstanding:* 30\n*PO date:* 2026-08-10\n*Location:* KL-WH"
        ) in block
        assert "Ordered" not in block


class TestOwner12SepBoldRungLabels:
    """AC-4/AC-5 (12 Sep 2026, finding 3): `_crossdomain_rung_text` bold-labels
    every field (`*Product Code:*`, `*Ordered:*`, `*Outstanding:*`, `*PO date:*`,
    `*Location:*`) - direct unit tests of the function itself, pinning the exact
    string the 11 Sep 2026 ruling already fixed the ORDER/omission/join rules
    for."""

    def test_a_full_row_is_exactly_this_bold_block(self) -> None:
        from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text

        row = {
            "product_code": "SRTWC191-G3", "ordered_qty": 30, "qty": 30,
            "po_date": "2026-08-10", "location": "BRW",
        }
        assert _crossdomain_rung_text([row]) == (
            "*Ordered:* 30\n*Outstanding:* 30\n*PO date:* 2026-08-10\n*Location:* BRW"
        )

    def test_omitted_fields_never_print_a_placeholder_line(self) -> None:
        from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text

        row = {
            "product_code": "X", "ordered_qty": None, "qty": "N",
            "po_date": None, "location": None,
        }
        assert _crossdomain_rung_text([row]) == "*Outstanding:* N"

    def test_two_rows_follow_one_another_with_no_blank_line(self) -> None:
        from app.services.chatbot.lanes.business.answer import _crossdomain_rung_text

        row_a = {"product_code": "A", "ordered_qty": 1, "qty": 1, "po_date": None, "location": None}
        row_b = {"product_code": "B", "ordered_qty": 2, "qty": 2, "po_date": None, "location": None}
        text = _crossdomain_rung_text([row_a, row_b])
        assert text == "*Ordered:* 1\n*Outstanding:* 1\n*Ordered:* 2\n*Outstanding:* 2"
        assert "\n\n" not in text


class TestThePORungGrantKey:
    """The backend half of the lane-gated key guardrail (review round 2 / PR #735 CI):
    the rung's grant key is pinned here from the backend's own tree, and matched against
    the PO ToolSpec's declaration only where the MCP package is importable (it is
    installed in the local venv; the backend CI image lacks it and skips that half)."""

    def test_the_rung_key_is_purchase_orders_placed(self) -> None:
        from app.services.chatbot.lanes.business.answer import _CROSSDOMAIN_RUNG_GRANT

        assert _CROSSDOMAIN_RUNG_GRANT == {"purchase_order": "purchase_orders.placed"}

    def test_the_key_is_declared_on_the_po_toolspec(self) -> None:
        import importlib.util
        import sys
        from pathlib import Path

        import pytest

        # The SIBLING tree first when this is the monorepo: the venv's editable install of
        # `sorento_crm_mcp` can point at another checkout (it does on the Mac mini), and a
        # stale catalog would grade the wrong declaration. Outside the monorepo (the
        # backend CI image) the package is absent and this half skips.
        sibling = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
        if sibling.is_dir() and str(sibling) not in sys.path:
            sys.path.insert(0, str(sibling))
            sys.modules.pop("sorento_crm_mcp", None)
            sys.modules.pop("sorento_crm_mcp.catalog", None)
        if importlib.util.find_spec("sorento_crm_mcp") is None:
            pytest.skip("sorento_crm_mcp not importable (backend CI image)")
        import sorento_crm_mcp.catalog as catalog

        from app.services.chatbot.lanes.business.answer import _CROSSDOMAIN_RUNG_GRANT, _CROSSDOMAIN_RUNG_TOOL

        specs = {spec.name: spec for spec in catalog.CATALOG}
        for rung, key in _CROSSDOMAIN_RUNG_GRANT.items():
            declared = dict(specs[_CROSSDOMAIN_RUNG_TOOL[rung]].restricted_fields)
            assert key in declared, f"{key} is not declared on {_CROSSDOMAIN_RUNG_TOOL[rung]}"


class TestD11LinesFollowOneAnotherInToolOrder:
    """Owner ruling, 11 Sep 2026 (retires D11/D14's `Qty {outstanding_qty} placed on
    {document_date}` line and D2's heading-per-document shape): the structured field
    block per row, rows separated by ONE blank line, in the rows' own (the tool's) order -
    no "PO 202607-S0054 dated ..." heading, no "pcs", no expected date."""

    def test_the_exact_block(self) -> None:
        rows = [
            _po_row(42, "2026-07-13", po_number="202607-S0054", po_date="2026-07-17"),
            _po_row(12, "2026-07-31", po_number="202607-S0054", po_date="2026-07-17"),
            _po_row(7, "2026-10-05", po_number="SPO-9", po_date="2026-08-20", kind="spo"),
            _po_row(3, None, po_number="202607-S0054", po_date="2026-07-17"),
        ]
        result, _ = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": rows, "has_result": True},
        )
        block = result["render"]["_xdBlock"]["block"]
        # Owner ruling 13 Oct 2026 (STAFF-STOCK-ETA): the three open lines of
        # 202607-S0054 share product, location and PO date, so they print as ONE row
        # summed (42 + 12 + 3) at the first line's position; the SPO stays its own row.
        assert block == (
            "*Product Code:* SRTWC8517\n*Stock:* none\n*Incoming:* none\n"
            + "\n".join([
                # Owner ruling 4 Oct 2026: rows on one PO (same number, date, location)
                # are one block with the quantities summed (42 + 12 + 3).
                _row_block(qty=57, po_date="2026-07-17", number="202607-S0054"),
                _row_block(qty=7, po_date="2026-08-20", number="SPO-9", label="SPO"),
            ])
        )
        assert "pcs" not in block and "expected" not in block


class TestD7AnIncomingAskClimbsToThePORung:
    """D7 (owner ruling, 8 Sep 2026): stock -> incoming -> PO whichever domain the customer
    entered from. "hav incoming?" for a zero-stock code with an open PO line used to end
    at "No incoming and no stock for X." because the 489 ladder had no second rung on
    `incoming`; migration 491 adds it."""

    def test_from_incoming_the_po_rung_runs_and_the_wording_follows_the_climb(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(42, "2026-07-13", po_number="202607-S0054", po_date="2026-07-17")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert block.startswith(
            f"*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* none\n{_row_block(qty=42, po_date='2026-07-17', number='202607-S0054')}"
        )
        assert result["render"]["_xdBlock"]["team"] == "purchasing"
        assert _composed_text(result).count("Would you like me to escalate") == 1

    def test_from_incoming_the_three_way_miss_reads_in_the_same_order(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        assert (
            "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* none\n*PO:* none"
            in result["render"]["_xdBlock"]["block"]
        )

    def test_from_incoming_the_spo_header_variant_holds(self) -> None:
        result, _ = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(7, "2026-10-05", po_number="SPO-9", po_date="2026-08-20", kind="spo")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert (
            "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* none\n"
            in result["render"]["_xdBlock"]["block"]
        )

    def test_from_incoming_without_the_grant_no_probe_and_the_ladder_off_note(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(42, "2026-07-13")], "has_result": True},
            parser=_INCOMING_PARSER,
            granted=[],
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL]
        assert (
            "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* none"
            in result["render"]["_xdBlock"]["block"]
        )
        assert "PO" not in result["render"]["_xdBlock"]["block"]

    def test_the_489_ladder_still_stops_at_stock_from_incoming(self) -> None:
        """A tenant that kept the 489 shape (custom or not yet migrated) is unchanged."""
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(42, "2026-07-13")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL]

    def test_from_inventory_the_wording_is_unchanged(self) -> None:
        result, _ = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
        )
        assert (
            "*Product Code:* SRTWC8517\n*Stock:* none\n*Incoming:* none\n*PO:* none"
            in result["render"]["_xdBlock"]["block"]
        )


_RESOLVED_PREFIX_FAMILY = {
    "tokens": ["SRTWT6236"],
    "intersection": [
        {"entity_type": "product", "canonical_code": "SRTWT6236-GY", "uuid": "U1", "match_tier": "and"}
    ],
}
_EMPTY_STOCK_ITEM = {"answers": [], "has_result": False}


class TestOwner12SepTypedPrefixIsRequested:
    """AC-6/AC-7 (12 Sep 2026, finding 4): `crossdomain_zeroset`'s non-`resolutions`
    branch requests an intersection product by `_type_norm` prefix, not only by
    equality - guarded so a token shorter than 4 characters never prefix-matches
    (else "SRT" would request every product in the intersection)."""

    def test_ac6_typed_prefix_requests_the_one_family_member(self) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        out = crossdomain_zeroset(
            _EMPTY_STOCK_ITEM,
            parser={"domain_hint": "incoming", "message_type": "business_query"},
            resolved=_RESOLVED_PREFIX_FAMILY,
            session_block=None,
        )
        xd = out["_xd"]
        assert xd["active"] is True
        assert xd["requested"] == ["SRTWT6236-GY"]
        assert xd["missing"][0]["uuid"] == "U1"
        assert len(xd["probe_entities"]) == 1

    def test_ac6_the_exact_token_case_is_byte_identical_to_today(self) -> None:
        """Regression pin - passes today already: equality is the existing rule."""
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        resolved_exact = {
            "tokens": ["SRTWT6236-GY"],
            "intersection": _RESOLVED_PREFIX_FAMILY["intersection"],
        }
        out = crossdomain_zeroset(
            _EMPTY_STOCK_ITEM,
            parser={"domain_hint": "incoming", "message_type": "business_query"},
            resolved=resolved_exact,
            session_block=None,
        )
        xd = out["_xd"]
        assert xd["active"] is True
        assert xd["requested"] == ["SRTWT6236-GY"]
        assert xd["missing"][0]["uuid"] == "U1"
        assert len(xd["probe_entities"]) == 1

    def test_ac7_a_token_shorter_than_four_characters_never_prefix_matches(self) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        resolved_short = {
            "tokens": ["SRT"],
            "intersection": _RESOLVED_PREFIX_FAMILY["intersection"],
        }
        out = crossdomain_zeroset(
            _EMPTY_STOCK_ITEM,
            parser={"domain_hint": "incoming", "message_type": "business_query"},
            resolved=resolved_short,
            session_block=None,
        )
        assert out["_xd"]["active"] is False


class TestOwner12SepTypedPrefixEndToEndClimbsToThePORung:
    """AC-8 (12 Sep 2026): the reproduced live turn, "ETA SRTWT6236" - typed
    prefix resolves (tier `and`) to SRTWT6236-GY, incoming is empty, stock is
    empty, and the PO rung finds the open line (202607-S0034, 99 outstanding,
    BRW) that today's equality-only request skips entirely."""

    def test_the_reply_carries_the_bold_po_block_and_the_purchasing_team(self) -> None:
        calls: list[tuple[str, dict]] = []

        def mcp_probe(name: str, args: dict) -> dict:
            calls.append((name, args))
            if name == _STOCK_TOOL:
                return {"answers": [], "has_result": False}
            if name == _PO_TOOL:
                return {
                    "answers": [
                        _po_row(
                            99, "2026-08-01", code="SRTWT6236-GY",
                            po_number="202607-S0034", po_date="2026-08-01", location="BRW",
                        )
                    ],
                    "has_result": True,
                }
            raise AssertionError(f"unexpected probe tool: {name}")

        services = AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []})
        result = run_crossdomain(
            _EMPTY_STOCK_ITEM,
            parser=_INCOMING_PARSER,
            resolved=_RESOLVED_PREFIX_FAMILY,
            session_block={"session_vars": {"variables": {}}},
            entities_names=None,
            services=services,
            contact_id="164838271",
            space_id="900001",
            crossdomain_ladder={"incoming": ["inventory", "purchase_order"]},
            granted=GRANTED,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert (
            "*Product Code:* SRTWT6236-GY\n*Incoming:* none\n*Stock:* none\n"
            + _row_block(code="SRTWT6236-GY", qty=99, po_date="2026-08-01", location="BRW", number="202607-S0034")
        ) in block
        assert result["render"]["_xdBlock"]["team"] == "purchasing"


class TestOwner11SepR1AccessAttributesOnTheFirstProbe:
    """Owner ruling (11 Sep 2026, second ruling), R1: the cross-domain STOCK block must
    carry Outstanding exactly like a direct stock ask - `crossdomain_probe_args` gains a
    `granted` keyword and stamps `"access": {"attributes": list(granted)}` on the args it
    builds, so `entity_ids_transformer` can set `include_sellable` on the stock probe."""

    def test_the_grant_reaches_the_first_probes_recorded_args(self) -> None:
        _, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            parser=_INCOMING_PARSER,  # so the first probe is the STOCK tool
            granted=["inventory.sellable", "purchase_orders.placed"],
        )
        first_tool, first_args = calls[0]
        assert first_tool == _STOCK_TOOL
        access = first_args.get("access") or {}
        assert "inventory.sellable" in (access.get("attributes") or [])

    def test_without_the_grant_the_first_probe_carries_no_access_attributes(self) -> None:
        _, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            parser=_INCOMING_PARSER,
            granted=[],
        )
        first_args = calls[0][1]
        access = first_args.get("access") or {}
        assert "inventory.sellable" not in (access.get("attributes") or [])
        # Finding 5 (fix round): `granted=[]` omits the key entirely, so today's args
        # stay byte-identical - not merely an empty `attributes` list.
        assert "access" not in first_args

    def test_crossdomain_probe_args_with_grant_sets_access_attributes(self) -> None:
        """Direct unit test of the function itself, not through `run_crossdomain`."""
        from app.services.chatbot.lanes.business.answer import crossdomain_probe_args

        xd = {
            "other_tool": _STOCK_TOOL,
            "origin_domain": "incoming",
            "team": "warehouse",
            "probe_entities": [{"uuid": "u1", "entity_type": "product", "code": "SRTWC8517"}],
        }
        args = crossdomain_probe_args(
            xd, parser=_INCOMING_PARSER, entities_names=None, contact_id="164838271",
            granted=["inventory.sellable"],
        )
        assert args["access"]["attributes"] == ["inventory.sellable"]

    def test_crossdomain_probe_args_with_no_grant_never_key_errors_and_omits_the_attribute(self) -> None:
        """`granted=None` (a caller with no entitlement read) is the empty set - no
        KeyError, no `inventory.sellable` on the args."""
        from app.services.chatbot.lanes.business.answer import crossdomain_probe_args

        xd = {
            "other_tool": _STOCK_TOOL,
            "origin_domain": "incoming",
            "team": "warehouse",
            "probe_entities": [{"uuid": "u1", "entity_type": "product", "code": "SRTWC8517"}],
        }
        args = crossdomain_probe_args(
            xd, parser=_INCOMING_PARSER, entities_names=None, contact_id="164838271", granted=None,
        )
        access = args.get("access") or {}
        assert "inventory.sellable" not in (access.get("attributes") or [])


class TestOwner11SepZeroEverywhereClimbs:
    """Owner ruling (11 Sep 2026, second ruling), R2: a code whose stock rows are ALL
    `Quantity On Hand: 0` climbs the ladder like "no rows" - it is not "found" just
    because a row exists. `zero_codes` is the new `_xdBlock` key naming which of
    `nothing_codes` were zero rather than genuinely absent."""

    # ---------------------------------------------------------------- incoming-origin

    def test_incoming_origin_zero_stock_renders_rows_and_climbs_to_po(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert (
            f"*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* 0\n{_row_block(qty=50)}"
        ) == block
        assert result["render"]["_xdBlock"]["zero_codes"] == ["SRTWC8517"]
        assert "SRTWC8517" in result["render"]["_xdBlock"]["nothing_codes"]
        assert result["render"]["_xdBlock"]["rung"] == "purchase_order"
        assert result["render"]["_xdBlock"]["team"] == "purchasing"

    def test_incoming_origin_zero_stock_rung_answers_nothing(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            po_response={"answers": [], "has_result": False},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert block == "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* 0\n*PO:* none"

    def test_incoming_origin_one_nonzero_row_is_not_zero_and_skips_the_rung(self) -> None:
        """A code with at least one non-zero row is genuinely "found" - unchanged from
        today: `only_other`, no zero sentence, no PO probe at all."""
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(5, warehouse="BRW")]
            ),
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert "*Stock:* 0" not in block
        assert "*PO:*" not in block
        assert "*Quantity On Hand:* 5" in block

    def test_incoming_origin_zero_stock_no_ladder_stays_at_first_probe_note(self) -> None:
        result, calls = _run(
            ladder=None,
            incoming_response=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert block == "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* 0"

    def test_incoming_origin_zero_stock_grant_missing_stays_at_first_probe_note(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            parser=_INCOMING_PARSER,
            granted=[],
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL]  # the rung is gated off
        block = result["render"]["_xdBlock"]["block"]
        assert block == "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* 0"

    def test_incoming_origin_plain_nothing_and_zero_group_order(self) -> None:
        """Two codes: CODE-A has no stock rows at all (plain nothing), CODE-B has two
        all-zero rows. Both header sentences appear, plain group first, zero group after."""
        resolved = {
            "resolutions": [
                {
                    "token": "CODE-A",
                    "matches": [
                        {"entity_type": "product", "canonical_code": "CODE-A", "uuid": "uuid-a",
                         "match_tier": "exact"}
                    ],
                },
                {
                    "token": "CODE-B",
                    "matches": [
                        {"entity_type": "product", "canonical_code": "CODE-B", "uuid": "uuid-b",
                         "match_tier": "exact"}
                    ],
                },
            ]
        }

        def mcp_probe(name: str, args: dict) -> dict:
            if name == _STOCK_TOOL:
                # CODE-A: no rows at all. CODE-B: two zero rows.
                return _stock_envelope(
                    [_stock_row(0, code="CODE-B", warehouse="KL-WH"),
                     _stock_row(0, code="CODE-B", warehouse="BRW")]
                )
            if name == _PO_TOOL:
                return {
                    "answers": [
                        _po_row(10, "2026-07-01", code="CODE-A", po_number="PO-A"),
                        _po_row(20, "2026-07-01", code="CODE-B", po_number="PO-B"),
                    ],
                    "has_result": True,
                }
            raise AssertionError(f"unexpected probe tool: {name}")

        services = AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []})
        result = run_crossdomain(
            _validator_result(other_code="SRTOTHER"),
            parser=_INCOMING_PARSER,
            resolved=resolved,
            session_block={"session_vars": {"variables": {}}},
            entities_names=None,
            services=services,
            contact_id="164838271",
            space_id="900001",
            crossdomain_ladder=_LADDER_491,
            granted=GRANTED,
        )
        block = result["render"]["_xdBlock"]["block"]
        plain_part = (
            f"*Product Code:* CODE-A\n*Incoming:* none\n*Stock:* none\n{_row_block(qty=10, number='PO-A')}"
        )
        zero_part = (
            f"*Product Code:* CODE-B\n*Incoming:* none\n*Stock:* 0\n{_row_block(qty=20, number='PO-B')}"
        )
        assert plain_part in block
        assert zero_part in block
        assert block.index(plain_part) < block.index(zero_part)
        # Finding 4 (fix round): the exact paragraph separator between the two groups -
        # one blank line, not zero, not two.
        assert f"{plain_part}\n\n{zero_part}" in block

    # ------------------------------------------------------------------ stock-origin

    def test_stock_origin_zeroset_flags_missing_with_zero_true(self) -> None:
        """Direct unit test of `crossdomain_zeroset`: a RETURNED code whose rows are all
        zero becomes a `missing` entry with `zero: True`, still listed in
        `returned_codes`, and `active` stays True (it is now probeable)."""
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        item = _stock_envelope(
            [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
        )
        out = crossdomain_zeroset(
            item, parser=_PARSER, resolved=_resolved_for("SRTWC8517", "prod-uuid-1"),
            session_block={"session_vars": {"variables": {}}},
        )
        xd = out["_xd"]
        assert xd["active"] is True
        assert "SRTWC8517" in xd["returned_codes"]
        zero_entries = [m for m in xd["missing"] if m["_n"] == "SRTWC8517"]
        assert len(zero_entries) == 1
        assert zero_entries[0].get("zero") is True

    def test_stock_origin_no_quantity_field_at_all_is_not_flagged_zero(self) -> None:
        """An availability-mode row prints no quantity field at all - `field_pref` returns
        None, which must never be coerced to a parseable zero. Nothing else about the
        turn is missing, so `active` stays False - the code is already fully answered."""
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        item = {
            "answers": [
                {"fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                    {"key": "warehouse", "label": "Warehouse", "value": "KL-WH"},
                ]}
            ],
            "has_result": True,
        }
        out = crossdomain_zeroset(
            item, parser=_PARSER, resolved=_resolved_for("SRTWC8517", "prod-uuid-1"),
            session_block={"session_vars": {"variables": {}}},
        )
        assert out["_xd"]["active"] is False

    def test_stock_origin_zero_at_every_location_climbs_with_po_rung(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            validator=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert (
            f"*Product Code:* SRTWC8517\n*Stock:* 0\n*Incoming:* none\n{_row_block(qty=50)}"
        ) == block
        assert result["render"]["_xdBlock"]["zero_codes"] == ["SRTWC8517"]

    def test_stock_origin_zero_stock_rung_answers_nothing(self) -> None:
        """Finding 6 (fix round): the stock-origin twin of
        `test_incoming_origin_zero_stock_rung_answers_nothing` - word order flips with
        the origin, exactly like the plain "no X, no Y" sentence already does."""
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            validator=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert block == "*Product Code:* SRTWC8517\n*Stock:* 0\n*Incoming:* none\n*PO:* none"

    def test_stock_origin_zero_but_incoming_answers_no_po_probe(self) -> None:
        """The other side has real rows (an ETA) - render them as today, no zero sentence,
        no rung probe (the code is not left with nothing on either side)."""
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            validator=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            incoming_response={
                "answers": [{"fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-15"},
                ]}],
                "has_result": True,
            },
        )
        assert _PO_TOOL not in [name for name, _ in calls]
        block = result["render"]["_xdBlock"]["block"]
        assert block == "*Product Code:* SRTWC8517\n*Stock:* 0\n*ETA:* 2026-09-15"


class TestOwner11SepFixRoundGrantedValueRendersOverValue:
    """Fix round, finding 1 (R1): the compact presenter's "(O/S: n)" Outstanding suffix
    lives on `granted_value`, never on `value` (`_stock_compact`,
    sorento_crm_mcp/presenters.py). `crossdomain_render` has no field drop of its own, so
    it renders `granted_value` when a probed field carries one, `value` otherwise."""

    def test_a_row_with_granted_value_renders_it(self) -> None:
        row = {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "total_on_hand", "label": "Total", "value": 12, "granted_value": "12 (O/S: 3)"},
            ]
        }
        result, _ = _run(
            ladder=_LADDER_NO_PO,
            incoming_response={"answers": [row], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        block = result["render"]["_xdBlock"]["block"]
        assert "*Total:* 12 (O/S: 3)" in block

    def test_a_row_with_no_granted_value_renders_the_plain_value(self) -> None:
        row = {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "total_on_hand", "label": "Total", "value": 12},
            ]
        }
        result, _ = _run(
            ladder=_LADDER_NO_PO,
            incoming_response={"answers": [row], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        block = result["render"]["_xdBlock"]["block"]
        assert "*Total:* 12" in block
        assert "O/S" not in block


class TestOwner11SepFixRoundCompactZeroDetection:
    """Fix round, finding 2 (R2): `_row_qty` also reads the COMPACT row's own total (key
    `total_on_hand`, label "Total"), so a compact "Total: 0" reply is exactly as zero as
    a detailed row reading 0 at every location."""

    def test_compact_total_zero_counts_as_zero(self) -> None:
        from app.services.chatbot.lanes.business.answer import _rows_all_zero

        compact_item = {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "total_on_hand", "label": "Total", "value": 0},
            ]
        }
        assert _rows_all_zero([compact_item]) is True

    def test_compact_total_nonzero_is_not_zero(self) -> None:
        from app.services.chatbot.lanes.business.answer import _rows_all_zero

        compact_item = {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "total_on_hand", "label": "Total", "value": 5},
            ]
        }
        assert _rows_all_zero([compact_item]) is False

    def test_compact_total_zero_climbs_to_po_like_a_detailed_zero_reply(self) -> None:
        compact_row = {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                {"key": "total_on_hand", "label": "Total", "value": 0},
            ]
        }
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [compact_row], "has_result": True},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert "*Product Code:* SRTWC8517\n*Incoming:* none\n*Stock:* 0\n*PO:* PO-1001" in block


class TestOwner11SepFixRoundZeroEntryPrefixFamilyLookup:
    """Fix round, finding 7: a ZERO-flagged entry's cross-probed rows are looked up under
    every `by_code` key equal to OR prefixed by the entry's `_n` - the SAME rule
    `crossdomain_zeroset` used to flag it from a typed code's family in the first place -
    not the exact key alone. A plain (non-zero) entry keeps the exact lookup, untouched."""

    def test_zero_flagged_family_code_finds_the_sibling_incoming_rows(self) -> None:
        family_zero_validator = _stock_envelope(
            [
                _stock_row(0, code="SRTWC8517-PJ", warehouse="KL-WH"),
                _stock_row(0, code="SRTWC8517-PJ", warehouse="BRW"),
            ]
        )
        incoming_response = {
            "answers": [
                {
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWC8517-PJ"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-15"},
                    ]
                }
            ],
            "has_result": True,
        }
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            validator=family_zero_validator,
            incoming_response=incoming_response,
            po_response={"answers": [], "has_result": False},
        )
        # Found under the family - no further rung needed at all.
        assert [name for name, _ in calls] == [_INCOMING_TOOL]
        block = result["render"]["_xdBlock"]["block"]
        assert "2026-09-15" in block
        assert "*PO:*" not in block
        assert "*Stock:* 0" in block


class TestACEQ5To10StockOriginStaysWarehouse:
    """PLAN-escalation-quote-title-and-stock-team-22sep.md, owner ruling R6: a stock-origin
    ask (domain_hint inventory) is always suggested to the warehouse team, no PO-rung
    override. Incoming-origin asks are unaffected - an incoming ask that climbs to the PO
    rung still names purchasing (AC-EQ-9), because that is the team `crossdomain_zeroset`
    already set before the rung ever ran."""

    def test_ac_eq_5_stock_zero_everywhere_incoming_empty_po_placed_stays_warehouse(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            validator=_stock_envelope(
                [_stock_row(0, warehouse="KL-WH"), _stock_row(0, warehouse="BRW")]
            ),
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]
        assert "*PO:* PO-1001" in block["block"]
        assert block["team"] == "warehouse"
        assert "escalate to warehouse team?" in _composed_text(result)

    def test_ac_eq_6_no_stock_no_incoming_po_placed_stays_warehouse(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]
        block = result["render"]["_xdBlock"]
        assert "*PO:* PO-1001" in block["block"]
        assert block["team"] == "warehouse"

    def test_ac_eq_7_no_stock_incoming_found_no_po_stays_warehouse(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={
                "answers": [
                    {"fields": [
                        {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-15"},
                    ]}
                ],
                "has_result": True,
            },
        )
        tool_names = [name for name, _ in calls]
        assert _PO_TOOL not in tool_names, "incoming answered - the PO rung must never run"
        assert result["render"]["_xdBlock"]["team"] == "warehouse"

    def test_ac_eq_8_no_stock_no_incoming_no_po_stays_warehouse(self) -> None:
        result, calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [], "has_result": False},
        )
        assert [name for name, _ in calls] == [_INCOMING_TOOL, _PO_TOOL]
        assert result["render"]["_xdBlock"]["team"] == "warehouse"

    def test_ac_eq_9_incoming_origin_climbing_to_po_still_names_purchasing(self) -> None:
        result, calls = _run(
            ladder=_LADDER_491,
            incoming_response={"answers": [], "has_result": False},
            po_response={
                "answers": [
                    _po_row(42, "2026-07-13", po_number="202607-S0054", po_date="2026-07-17")
                ],
                "has_result": True,
            },
            parser=_INCOMING_PARSER,
        )
        assert [name for name, _ in calls] == [_STOCK_TOOL, _PO_TOOL]
        assert result["render"]["_xdBlock"]["team"] == "purchasing"

    def test_ac_eq_10_the_po_placed_line_still_renders_only_the_team_word_changed(self) -> None:
        result, _calls = _run(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
        )
        block = result["render"]["_xdBlock"]
        assert f"{_row_block(qty=50)}" in block["block"]
        assert block["team"] == "warehouse"
