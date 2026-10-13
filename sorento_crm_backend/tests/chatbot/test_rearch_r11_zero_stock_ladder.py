"""Hand pass 11, finding 1 - a stock HIT whose rows ALL read 0 on hand must climb the
ladder (owner ruling, prod parity, 21 Sep 2026). RED, test-first.

Prod turn "check stock srtwc6022": the stock rows print (SRTWC6022-SH-UF, BRW, on hand 0,
O/S 5, discontinued; SRTWC6022-SH-UF-NEW on hand 0), then "No stock for
SRTWC6022-SH-UF-NEW." + "But there is INCOMING stock (ETA) for the requested products:"
+ the incoming rows (container IAAU1697450, ETA 2026-09-09, qty 9, BRW) + "Would you like
me to escalate to warehouse team?". The lane prints the stock summary only.

Measured cause (captain's brief): origin/main's HIT arm calls `answer.run_crossdomain`,
whose `crossdomain_zeroset` marks a stock-origin code whose rows all read 0 as `zero:
True` missing (`_rows_all_zero` reads `quantity_on_hand` first, then the compact
`total_on_hand`/"Total"), so the ladder probes it. On the lane, `answer_bridge.
_run_crossdomain_ladder` is reached only from `answer_for`'s MISS triggers and
`turn/fetch.py::_climb` fires only on `envelope_missed` - nothing climbs on a hit.

Harness: the SAME shape `test_foundre_rung_end_to_end.py` proved (a real `engine.run_turn`
against the Postgres blank-schema fixture, the resolver and the MCP tool runner stubbed,
the ladder walked by the engine itself). The stock tool answers a HIT with every row at
0 - in BOTH presenter modes (detailed `quantity_on_hand`, compact `total_on_hand`/
"Total") - and the rung tools answer whatever each case needs. Every expected string is
production copy from `lanes/business/answer.py` (`only_other_note`'s "No {word} for X.",
the incoming `lead`, `_group_parts`' "but PO is placed") or the owner's own prod paste -
nothing invented here. `_CROSSDOMAIN_RUNG_TEAM` is retired (owner ruling 22 Sep 2026, R6,
AC-EQ-5): the warehouse team offer below is now the SAME whether the PO rung answers or
not.

No live parser, no :8766, no API key: `stub_parser` supplies the verdict.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot.lanes.business.services import FetchServices, ResolveGateServices
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)

STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_list"
PO_TOOL = "crm_procurement_po_placed_list"

ZERO_CODE = "SRTWC6022-SH-UF-NEW"
ZERO_UUID = "60220000-0000-0000-0000-000000000001"
# Deliberately NOT "SRTWC6022-SH-UF" (a string PREFIX of `ZERO_CODE` below,
# "SRTWC6022-SH-UF-NEW") - any substring/"in" assertion that the LIVE code never
# appears would always find it living inside the zero code's own printed lines.
LIVE_CODE = "SRTWC7015-RL-UF"
LIVE_UUID = "60220000-0000-0000-0000-000000000002"

# The owner's prod copy, verbatim (finding 1's own paste).
#: Card v4: a zero-stock code is ONE block, `*Stock:* 0` under its code, then its rung's lines.
INCOMING_LEAD = f"*Product Code:* {ZERO_CODE}\n*Stock:* 0"
NO_STOCK_FOR = "*Stock:* 0"
PO_HEADER = "*PO:* PO-0003"
WAREHOUSE_OFFER = "escalate to warehouse team"
PURCHASING_OFFER = "escalate to purchasing team"

#: S3 (reviewer, PR #952 hand pass 11 round 2): "compact" above is the UNGRANTED row -
#: `value` is always a clean number, `restricted_fields` never set, so `fetch.py::
#: _keep_field`'s swap (`f["value"] = f.pop("granted_value")`) never fires and
#: `answer.py::_on_hand_number`'s string-unwrap branch (`_ON_HAND_WITH_OUTSTANDING`) is
#: never exercised - a kill test that turns `_on_hand_number` into the identity function
#: leaves every one of this file's own tests green (measured: `pytest -k compact` stays
#: 100% pass with the guard deleted). "compact_granted" is the shape live turn cfee5933
#: actually carries once a contact holds `inventory.sellable`: `value` becomes the
#: FORMATTED STRING `"{qty} (O/S: 0)"`, not the number, matching `STOCK_ENVELOPE`'s own
#: rows in `test_rearch_r11_zero_stock_live_replay.py`.
MODES = ("detailed", "compact", "compact_granted")


def _stock_row(code: str, qty: int, mode: str, *, discontinued: bool = False) -> dict[str, Any]:
    """One stock item in the RAW MCP presenter shape `output_structurer` reads.
    `detailed` is the per-warehouse row (`quantity_on_hand`); `compact` is the per-product
    total (`total_on_hand`, label "Total") - `_row_qty` reads exactly these two.
    `compact_granted` is `compact` PLUS the restricted-field grant shape (S3): the row
    carries both the clean `value` and the formatted `granted_value`, and `_stock_hit`
    below stamps the envelope's own `restricted_fields` so `fetch.py::_keep_field`
    performs the real swap before this file's assertions ever see the row."""
    if mode == "detailed":
        fields = [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "warehouse", "label": "Warehouse", "value": "BRW"},
            {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": qty},
            {"key": "outstanding", "label": "Outstanding", "value": 5 if qty == 0 else 0},
        ]
    elif mode == "compact_granted":
        fields = [
            {"key": "product_code", "label": "Product Code", "value": code},
            {
                "key": "total_on_hand", "label": "Total", "value": qty,
                "granted_value": f"{qty} (O/S: 0)",
            },
        ]
    else:
        fields = [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "total_on_hand", "label": "Total", "value": qty},
        ]
    row: dict[str, Any] = {"fields": fields}
    if discontinued:
        row["flags"] = {"discontinued": True}
    return row


def _stock_hit(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "result_type": "stock",
        "intro": (
            "Stock details found for the requested products."
            if mode == "detailed"
            else "Stock summary for the requested products."
        ),
        "items": rows,
        "has_result": True,
    }
    if mode == "compact_granted":
        # `fetch.py::output_structurer`'s restricted-field gate reads THIS key off the
        # envelope itself (`e.get("restricted_fields")`) - absent (as for plain "compact"
        # above), the whole `_keep_field` block never runs, so adding the grant to `_run`'s
        # `stub_access` call unconditionally (below) is a no-op for every other mode.
        envelope["restricted_fields"] = {"total_on_hand": "inventory.sellable"}
    return envelope


def _incoming_rows(code: str) -> dict[str, Any]:
    """The prod incoming rung: container IAAU1697450, ETA 2026-09-09, qty 9, BRW."""
    return {
        "result_type": "incoming",
        "intro": "Here is the incoming stock I found.",
        "items": [
            {
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {"key": "container", "label": "Container", "value": "IAAU1697450"},
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-09"},
                    {"key": "incoming_quantity", "label": "Incoming Quantity", "value": 9},
                    {"key": "location", "label": "Location", "value": "BRW"},
                ]
            }
        ],
        "has_result": True,
    }


def _po_rows(code: str) -> dict[str, Any]:
    """The prod PO rung: Ordered 10 / Outstanding 10 / PO date 2026-09-11 / BRW."""
    return {
        "result_type": "purchase_orders_placed",
        "intro": "Here is the PO placed I found.",
        "items": [
            {
                "fields": [
                    {"key": "po_number", "label": "PO Number", "value": "PO-0003"},
                    {"key": "product_code", "label": "Product Code", "value": code},
                    {"key": "ordered_qty", "label": "Ordered Qty", "value": 10},
                    {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 10},
                    {"key": "po_date", "label": "PO Date", "value": "2026-09-11"},
                    {"key": "location", "label": "Location", "value": "BRW"},
                ]
            }
        ],
        "has_result": True,
    }


EMPTY_INCOMING = {"result_type": "incoming", "intro": "No matching results found.", "items": [], "has_result": False}
EMPTY_PO = {"result_type": "purchase_orders_placed", "intro": "No matching results found.", "items": [], "has_result": False}


def _bundle(codes: dict[str, str]) -> ResolveGateServices:
    """Every code in `codes` resolves to exactly one product at tier `exact`."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = [t for t in (body.get("tokens") or []) if t in codes] or list(codes)
        return {
            "tokens": asked,
            "resolutions": [
                {
                    "raw": code,
                    "token": code,
                    "matches": [
                        {
                            "uuid": codes[code],
                            "entity_type": "product",
                            "canonical_code": code,
                            "match_tier": "exact",
                        }
                    ],
                }
                for code in asked
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


def _run(
    session_factory,
    monkeypatch,
    stub_parser,
    stub_access,
    *,
    codes: dict[str, str],
    stock: dict[str, Any],
    incoming: dict[str, Any],
    po: dict[str, Any],
):
    """One real stock turn. Returns (result, said, probes) where `probes` is the ordered
    list of `(tool_name, args)` the stubbed tool runner saw."""
    from app.models.user import SystemSetting
    from app.services.chatbot import engine as engine_mod

    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    for row in db.query(SystemSetting).all():
        row.chatbot_completed_lanes = ["business_query"]
        row.chatbot_crossdomain_ladder = {
            "inventory": ["incoming", "purchase_order"],
            "incoming": ["inventory", "purchase_order"],
        }
    db.commit()

    probes: list[tuple[str, dict[str, Any]]] = []

    def _mcp_call(name: str, args: dict[str, Any]) -> str:
        probes.append((name, dict(args)))
        if name == STOCK_TOOL:
            return json.dumps(stock)
        if name == INCOMING_TOOL:
            return json.dumps(incoming)
        if name == PO_TOOL:
            return json.dumps(po)
        return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

    bundle = _bundle(codes)
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp_call)
    )
    stub_parser(
        _parser_output(
            intent_hint="check_stock",
            domain_hint="inventory",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True}
                for code in codes
            ],
        )
    )
    # Both rungs are per-contact reveals (PO: `purchase_orders.placed`); `inventory.
    # sellable` grants the compact stock total's own outstanding suffix (S3,
    # `compact_granted` mode) - harmless for "detailed"/"compact", whose envelopes never
    # carry `restricted_fields` at all (`_stock_hit`'s own docstring).
    stub_access(attributes=["purchase_orders.placed", "inventory.sellable"])

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    said = "\n".join(
        [((result.reply or {}).get("text") or "")]
        + [a.get("text") or "" for a in (result.actions or []) if isinstance(a, dict)]
    )
    return result, said, probes


def _tool_calls(probes: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [args for tool, args in probes if tool == name]


# --------------------------------------------------------------------------- #
# (a) all rows zero, incoming rung answers -> stock block + "No stock for X." + the
#     incoming block + the warehouse offer
# --------------------------------------------------------------------------- #


class TestAllZeroStockHitClimbsToIncoming:
    @pytest.mark.parametrize("mode", MODES)
    def test_zero_rows_keep_the_stock_block_then_print_the_incoming_rung(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        result, said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, mode)], mode),
            incoming=_incoming_rows(ZERO_CODE),
            po=EMPTY_PO,
        )
        assert result.status == "done", result.error
        # Card v4: the zero stock rows give way to the code's own block (`*Stock:* 0`).
        assert "*Quantity On Hand:* 0" not in said and "*Total:* 0" not in said, said
        # Then the ladder, in the owner's prod copy.
        assert _tool_calls(probes, INCOMING_TOOL), (
            f"a hit whose rows all read 0 must climb to the incoming rung: {probes}"
        )
        assert NO_STOCK_FOR in said, said
        assert INCOMING_LEAD in said, said
        assert "IAAU1697450" in said, said
        assert "2026-09-09" in said, said
        assert WAREHOUSE_OFFER in said, said

    @pytest.mark.parametrize("mode", MODES)
    def test_a_discontinued_zero_row_still_climbs(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """The owner's first row IS discontinued (O/S 5, on hand 0) - the flag never
        exempts a zero row from the ladder."""
        _result, said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, mode, discontinued=True)], mode),
            incoming=_incoming_rows(ZERO_CODE),
            po=EMPTY_PO,
        )
        assert _tool_calls(probes, INCOMING_TOOL), probes
        assert INCOMING_LEAD in said, said


# --------------------------------------------------------------------------- #
# (b) all rows zero, incoming empty, PO rung answers -> PO rung copy + purchasing team
# --------------------------------------------------------------------------- #


class TestAllZeroStockHitFallsThroughToThePORung:
    @pytest.mark.parametrize("mode", MODES)
    def test_incoming_empty_then_po_rows_print_with_the_warehouse_offer(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        result, said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, mode)], mode),
            incoming=EMPTY_INCOMING,
            po=_po_rows(ZERO_CODE),
        )
        assert result.status == "done", result.error
        assert _tool_calls(probes, INCOMING_TOOL), probes
        assert _tool_calls(probes, PO_TOOL), (
            f"with the incoming rung empty the PO rung must be probed: {probes}"
        )
        # `_group_parts`' header for a PO line. Owner ruling 22 Sep 2026, R6 (AC-EQ-5)
        # retired `_CROSSDOMAIN_RUNG_TEAM` - a stock-origin ask keeps the warehouse team
        # even when the PO rung is what answered.
        assert PO_HEADER in said, said
        # Production's own field names, not the row's raw label - `_crossdomain_rung_text`
        # (answer.py:1234-1235) hardcodes "*Ordered:*"/"*Outstanding:*", matching the
        # owner's own prod paste ("*Ordered:* 10 *Outstanding:* 10").
        assert "*Ordered:* 10" in said, said
        assert "*Outstanding:* 10" in said, said
        assert "2026-09-11" in said, said
        assert WAREHOUSE_OFFER in said, said
        assert PURCHASING_OFFER not in said, (
            "a stock-origin ask never re-points to purchasing just because the PO rung "
            "answered", said
        )


# --------------------------------------------------------------------------- #
# (c) CONTROL, green today: rows non-zero -> no rung probe at all
# --------------------------------------------------------------------------- #


class TestNonZeroStockHitNeverClimbs:
    @pytest.mark.parametrize("mode", MODES)
    def test_no_rung_tool_is_called_for_a_real_hit(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        result, said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={LIVE_CODE: LIVE_UUID},
            stock=_stock_hit([_stock_row(LIVE_CODE, 812, mode)], mode),
            incoming=_incoming_rows(LIVE_CODE),
            po=_po_rows(LIVE_CODE),
        )
        assert result.status == "done", result.error
        assert [tool for tool, _ in probes] == [STOCK_TOOL], (
            f"a genuine hit asks exactly one tool, the primary: {probes}"
        )
        assert INCOMING_LEAD not in said, said
        assert PO_HEADER not in said, said
        assert "escalate" not in said.lower(), said


# --------------------------------------------------------------------------- #
# (d) mixed set: only the zero code climbs, the non-zero code never enters the ladder
# --------------------------------------------------------------------------- #


class TestMixedSetOnlyTheZeroCodeClimbs:
    @pytest.mark.parametrize("mode", MODES)
    def test_the_rung_is_scoped_to_the_zero_code_and_the_sentence_names_only_it(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        result, said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={LIVE_CODE: LIVE_UUID, ZERO_CODE: ZERO_UUID},
            stock=_stock_hit(
                [_stock_row(LIVE_CODE, 812, mode), _stock_row(ZERO_CODE, 0, mode)], mode
            ),
            incoming=_incoming_rows(ZERO_CODE),
            po=EMPTY_PO,
        )
        assert result.status == "done", result.error
        incoming_calls = _tool_calls(probes, INCOMING_TOOL)
        assert incoming_calls, f"the zero code must climb: {probes}"
        for args in incoming_calls:
            ids = args.get("product_ids") or []
            assert ZERO_UUID in ids, args
            assert LIVE_UUID not in ids, (
                "the non-zero code must never be sent up the ladder", args
            )
        assert NO_STOCK_FOR in said, said
        assert f"No stock for {LIVE_CODE}" not in said, said
        # Nothing about the live code in the incoming rung's own block. `said` joins the
        # reply text with every action's own text, and an action can mirror the reply's
        # full composed message - truncate at the offer that closes THIS rung's block, or
        # a mirrored duplicate further down would falsely reprint the (harmless) stock
        # block's own live-code row into `tail`.
        tail = said.split(INCOMING_LEAD, 1)[1] if INCOMING_LEAD in said else ""
        if WAREHOUSE_OFFER in tail:
            tail = tail.split(WAREHOUSE_OFFER, 1)[0] + WAREHOUSE_OFFER
        assert INCOMING_LEAD in said, said
        assert LIVE_CODE not in tail, ("the ladder sentence names only the zero code", tail)


# --------------------------------------------------------------------------- #
# (e) exactly ONE probe per rung per turn - no `_climb` + bridge double probe
# --------------------------------------------------------------------------- #


class TestExactlyOneProbePerRungPerTurn:
    @pytest.mark.parametrize("mode", MODES)
    def test_incoming_answers_so_incoming_is_probed_once_and_po_never(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        _result, _said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, mode)], mode),
            incoming=_incoming_rows(ZERO_CODE),
            po=EMPTY_PO,
        )
        names = [tool for tool, _ in probes]
        assert names.count(STOCK_TOOL) == 1, names
        assert names.count(INCOMING_TOOL) == 1, (
            "one climb, one probe - the fix must not fire both `_climb` and the bridge "
            f"ladder for the same rung: {names}"
        )
        assert names.count(PO_TOOL) == 0, ("the ladder stops at the first rung with rows", names)

    @pytest.mark.parametrize("mode", MODES)
    def test_both_rungs_miss_each_is_probed_exactly_once(
        self, mode, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        _result, _said, probes = _run(
            session_factory, monkeypatch, stub_parser, stub_access,
            codes={ZERO_CODE: ZERO_UUID},
            stock=_stock_hit([_stock_row(ZERO_CODE, 0, mode)], mode),
            incoming=EMPTY_INCOMING,
            po=EMPTY_PO,
        )
        names = [tool for tool, _ in probes]
        assert names.count(INCOMING_TOOL) == 1, names
        assert names.count(PO_TOOL) == 1, names
