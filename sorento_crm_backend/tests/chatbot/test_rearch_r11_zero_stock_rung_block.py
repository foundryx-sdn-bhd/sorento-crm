"""Hand pass 11 finding 1, rung-block round - the owner retested the zero-stock ladder
LIVE on :8081 @ `d91e53fc1` (the commit that made the ladder actually climb) and it now
climbs, but the RUNG BLOCK itself carries two defects. Ladder turn
`27f60a71-a8c9-4d17-bd99-33f008d4207f` (contact `900000008`, clone DB
`sorento_ai_automation_rearch`, read-only SELECT against `chatbot.turns.trace`, 21 Sep
2026) is functionally the SAME scenario `test_rearch_r11_zero_stock_live_replay.py`
already replays as turn `8781cd47...` (same contact, same typed text "check stock
srtwc6022", same two real products by prefix, same stock envelope shape) - re-pulled here
because it is the turn where the ladder genuinely fired and its OWN `kind: "crossdomain"`
trace event shows the two defects below, byte for byte:

* `rung: "crm_incoming_stock_list"`, `rows: 1` (the probe's own tool envelope holds ONE
  item - ONE shipment, ONE line, for SRTWC6022-SH-UF-NEW only), yet `rendered` prints the
  SAME container/ETA/qty block TWICE, back to back:

      But there is INCOMING stock (ETA) for the requested products:

      - *Product Code:* SRTWC6022-SH-UF-NEW
      *Container:* IAAU1697450
      ...
      :rotating_light:  *(PENDING ALLOCATION)*

      - *Product Code:* SRTWC6022-SH-UF-NEW
      *Container:* IAAU1697450
      ...
      :rotating_light:  *(PENDING ALLOCATION)*

  Cause, measured (`lanes/business/answer.py::crossdomain_render`, ~line 888): a
  `zero: True` missing entry's rows are looked up by `code_key == n or
  code_key.startswith(n)` against the RUNG's own `by_code` - both zero entries in
  `zeroset["missing"]` (SRTWC6022-SH-UF AND SRTWC6022-SH-UF-NEW) match the SAME single
  `by_code["SRTWC6022-SH-UF-NEW"]` bucket (SRTWC6022-SH-UF-NEW startswith
  SRTWC6022-SH-UF), so the SAME row list renders once per missing entry - two entries,
  one shared bucket, two printed blocks for what the tool envelope carries as ONE.
  Production reproduces the SAME duplicate (owner's prod paste) - a production defect,
  not a test-fixture artefact.
* The zero note above the rung block: production says
  "No stock for SRTWC6022-SH-UF-NEW." (one code, the one the rung actually answered
  about). This lane's OWN reply names BOTH: "No stock for SRTWC6022-SH-UF,
  SRTWC6022-SH-UF-NEW." Cause, measured (`app/services/chatbot/answer_bridge.py`,
  `_block_product_codes`/`_RUNG_ROW_CODE_RE`): `crossdomain_render`'s own per-row grammar
  prefixes EVERY row with a bullet dash before the label line
  (`lanes/business/answer.py:938`, `line = f"- {field_lines}"`, so the printed line reads
  "- *Product Code:* SRTWC6022-SH-UF-NEW", never a bare "*Product Code:* ..." line) -
  `_RUNG_ROW_CODE_RE = re.compile(r"^\\*Product Code:\\*\\s*(.+?)\\s*$", re.MULTILINE)`
  anchors on `^\\*Product Code:\\*`, which the leading "- " defeats on EVERY real rung
  block, not only this prefix-family one. `_block_product_codes` therefore always returns
  an EMPTY set, `_prefix_zero_note`'s own `if rendered_codes and code not in
  rendered_codes: continue` guard is an unconditional no-op (an empty set is falsy), and
  every `zero: True` missing entry with a rendered row (there is no OTHER filter) is named
  in the note - both codes here, because the duplicate-row bug above means BOTH zero
  entries found a (borrowed) row to render.

Both causes are named here for the coder; this file asserts observed behaviour only, no
internal state.

Harness: the SAME real-resolver-through-`_run_turn_engine` convention
`test_rearch_r11_zero_stock_live_replay.py` uses (imported directly rather than
re-derived, so a change to that file's constants cannot silently drift these tests out of
sync with the live trace they both replay) - `FetchServices.mcp_call` and
`AnswerServices.mcp_probe` the only doubles, the incoming probe's own row rendered through
the REAL MCP presenter (`_mcp_probe_for`/`_present_response`, "production path, not a
shortcut"). Two real products seeded per scenario so the REAL resolver settles the typed
text on its own - no hand-built `ResolveGateServices.resolve_entity` stub anywhere in this
file. Postgres only, blank schema, no live parser, no :8766, no API key, no live turn to
:8081.

Owner ruling, 21 Sep 2026: the rung printing all 14 incoming fields (Container, ETA, ETA
Delay, CIDB Inspection, Loading, ETC, ETD, Liner, China Forwarder, Malaysia Forwarder,
Consignee, Free Days Available, Incoming Quantity) is NOT a defect - no assertion in this
file about which fields a rung row prints, deliberately.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _capturing_probe, _run_turn_engine
from tests.chatbot.test_rearch_r11_zero_stock_live_replay import (
    CODE_A,
    CODE_B,
    INCOMING_TOOL,
    LIVE_GRANTED_ATTRIBUTES,
    PO_TOOL,
    STOCK_ENVELOPE,
    STOCK_TOOL,
    _RECORDED_VERDICT,
    _said,
    _set_crossdomain_ladder,
)

# The rung's own real incoming row for the -NEW product (same shipment
# `test_rearch_r11_zero_stock_live_replay.py` already pins: container IAAU1697450, ETA
# 2026-09-09, one line shipped 9). ONE row in the tool's own envelope - the duplicate the
# lane currently prints is downstream of this single row, never a second one from the MCP
# tool. NO `company_name` - the live trace's own rendered rung block (this file's own
# docstring, quoted verbatim from turn 27f60a71) has no "*Company:*" line at all, so its
# first field is "*Product Code:*" (`sorento_crm_mcp/presenters.py::_incoming_list` puts
# "Company" first among the row's pairs, but `_Builder.item`'s own `if not
# _filled(val): continue` drops a falsy one - carrying `company_name` here, as
# `test_rearch_r11_zero_stock_live_replay.py`'s own `_INCOMING_ROW` does, would put
# "*Company:*" ahead of "*Product Code:*" on the rendered line and hide the exact
# line-anchoring gap `_RUNG_ROW_CODE_RE` has on a real rung block - measured, not
# invented; the live shipment this replays genuinely renders company-free).
_INCOMING_ROW_B = {
    "shipping_container_number": "IAAU1697450",
    "estimated_arrival_date": "2026-09-09",
    "lines": [
        {"product_code": CODE_B, "remaining_incoming_quantity": 9, "warehouse_allocations": []}
    ],
}


def _reply_text(result) -> str:
    """The primary reply's own text ONLY - never `_said(result)` for a COUNT assertion.
    `_run_turn_engine`'s own `result.actions` carries a `kind: "send_message"` action
    whose `text` is a byte-identical COPY of `result.reply["text"]` (measured directly:
    both a debug print and every count-based assertion here agreed) - `_said` joins
    reply + every action's text for the `in`-substring checks the live-replay file uses
    (immune to a copy), but a `str.count(...)` assertion over that same joined string
    would double-count every occurrence and read as a defect that is not there."""
    return (result.reply or {}).get("text") or ""


def _stock_mcp_call_family() -> Any:
    def _call(name: str, args: dict[str, Any]) -> str:
        if name == STOCK_TOOL:
            return json.dumps(STOCK_ENVELOPE)
        return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

    return _call


class TestRungBlockZeroNoteNamesOnlyTheRenderedCode:
    """(a) Turn 27f60a71 replay: the zero note above the rung block must name ONLY
    SRTWC6022-SH-UF-NEW - the one code the rung's own rendered rows actually carry -
    never the family sibling SRTWC6022-SH-UF, which never gets a row of its own
    (production's own copy, the owner's prod paste)."""

    def test_the_zero_note_names_only_the_new_code(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        _set_crossdomain_ladder(session_factory)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_A)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_B)

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call_family())
        answer_probe, probe_calls = _capturing_probe(
            {INCOMING_TOOL: [_INCOMING_ROW_B], PO_TOOL: []}
        )

        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_RECORDED_VERDICT,
            text_body="check stock srtwc6022:", msg_id="zzt-r11-rung-zero-note",
            mcp_call=mcp_call, answer_mcp_probe=answer_probe,
            attributes=LIVE_GRANTED_ATTRIBUTES,
        )
        said = _said(result)
        reply_text = _reply_text(result)
        assert result.status == "done", result.error

        # Test setup sanity: the ladder genuinely climbed (matches the live trace's own
        # `kind: "crossdomain"` event - the fixture this file's docstring quotes).
        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"test setup sanity: the incoming rung must be probed exactly once before "
            f"this test's own assertions about what it PRINTED mean anything: "
            f"probe_calls={probe_calls!r}"
        )

        # EXACTLY one "No stock for <NEW only>." line - never zero, never repeated, and
        # never naming the family sibling too.
        assert reply_text.count("No stock for SRTWC6022-SH-UF-NEW.") == 1, reply_text
        assert "SRTWC6022-SH-UF, SRTWC6022-SH-UF-NEW" not in said, (
            f"the zero note must name ONLY the code the rung's own rendered rows carry "
            f"(SRTWC6022-SH-UF-NEW) - production never names the family sibling "
            f"SRTWC6022-SH-UF here, it never earned a row of its own: {said!r}"
        )


class TestRungBlockContainerPrintsExactlyOnce:
    """(b) Turn 27f60a71 replay: the probe's own tool envelope holds ONE incoming item
    (container IAAU1697450) - the rendered rung block must print that container's block
    exactly once, not twice."""

    def test_the_container_block_prints_once_not_twice(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        _set_crossdomain_ladder(session_factory)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_A)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=CODE_B)

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call_family())
        answer_probe, probe_calls = _capturing_probe(
            {INCOMING_TOOL: [_INCOMING_ROW_B], PO_TOOL: []}
        )

        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_RECORDED_VERDICT,
            text_body="check stock srtwc6022:", msg_id="zzt-r11-rung-dup-row",
            mcp_call=mcp_call, answer_mcp_probe=answer_probe,
            attributes=LIVE_GRANTED_ATTRIBUTES,
        )
        reply_text = _reply_text(result)
        assert result.status == "done", result.error

        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"test setup sanity: the incoming rung must be probed exactly once: "
            f"probe_calls={probe_calls!r}"
        )

        assert reply_text.count("IAAU1697450") == 1, (
            f"the probe's own tool envelope holds ONE item for container IAAU1697450 - "
            f"the rendered rung block must print it exactly once, not once per zero "
            f"missing entry that happens to match it by prefix: {reply_text!r}"
        )
        assert reply_text.count("PENDING ALLOCATION") == 1, (
            f"one incoming row must render as one row - its own pending-allocation flag "
            f"line must also appear exactly once, not once per missing entry it was "
            f"borrowed for: {reply_text!r}"
        )


class TestControlTwoDifferentZeroCodesEachOwnRow:
    """(d) Control: a family where two DIFFERENT (non-prefix-related) zero codes each
    have their OWN incoming row - both rows must print once each, and the zero note must
    name both codes. Explicit two-entity ask (not a shared-prefix single token), so this
    isolates that the fix for (a)/(b) must not regress the ordinary "two distinct codes,
    two distinct rows" case."""

    CODE_X = "ZZTFAM-AAA1"
    CODE_Y = "ZZTFAM-BBB2"

    def test_both_codes_each_print_their_own_row_once(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        _set_crossdomain_ladder(session_factory)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=self.CODE_X)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=self.CODE_Y)

        stock_envelope = {
            "intro": "Stock summary for the requested products.",
            "items": [
                {
                    "flags": {"discontinued": False},
                    "title": self.CODE_X,
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": self.CODE_X},
                        {
                            "key": "total_on_hand", "label": "Total", "value": 0,
                            "granted_value": "0 (O/S: 0)",
                        },
                    ],
                },
                {
                    "flags": {"discontinued": False},
                    "title": self.CODE_Y,
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": self.CODE_Y},
                        {
                            "key": "total_on_hand", "label": "Total", "value": 0,
                            "granted_value": "0 (O/S: 0)",
                        },
                    ],
                },
            ],
            "has_result": True,
            "attachments": [],
            "result_type": "stock_compact",
            "action_links": [],
            "fallback_used": False,
            "last_updated_at": "2026-09-14T17:25:40.036011",
            "stock_visibility": {
                "mode": "compact", "source": "contact", "warehouse_codes": None,
                "hide_zero_locations": False,
            },
            "restricted_fields": {"total_on_hand": "inventory.sellable"},
        }

        def _stock_mcp_call(name: str, args: dict[str, Any]) -> str:
            if name == STOCK_TOOL:
                return json.dumps(stock_envelope)
            return json.dumps({"result_type": "unknown", "items": [], "has_result": False})

        row_x = {
            "company_name": "Sorento",
            "shipping_container_number": "CONT-AAAA",
            "estimated_arrival_date": "2026-09-10",
            "lines": [
                {"product_code": self.CODE_X, "remaining_incoming_quantity": 5, "warehouse_allocations": []}
            ],
        }
        row_y = {
            "company_name": "Sorento",
            "shipping_container_number": "CONT-BBBB",
            "estimated_arrival_date": "2026-09-11",
            "lines": [
                {"product_code": self.CODE_Y, "remaining_incoming_quantity": 3, "warehouse_allocations": []}
            ],
        }

        mcp_call, fetch_calls = _mcp_double(other=_stock_mcp_call)
        answer_probe, probe_calls = _capturing_probe(
            {INCOMING_TOOL: [row_x, row_y], PO_TOOL: []}
        )

        verdict = _parser_output(
            intent_hint="check_stock",
            domain_hint="inventory",
            domain_in_message=True,
            entities=[
                {"raw": self.CODE_X, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
                {"raw": self.CODE_Y, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True},
            ],
            routing={"suggested_team": "warehouse", "suggested_agent": "general_enquiries", "team_source": None},
        )

        result = _run_turn_engine(
            session_factory, monkeypatch, qf=verdict,
            text_body=f"check stock {self.CODE_X} {self.CODE_Y}", msg_id="zzt-r11-rung-control-two-codes",
            mcp_call=mcp_call, answer_mcp_probe=answer_probe,
            attributes=LIVE_GRANTED_ATTRIBUTES,
        )
        said = _said(result)
        reply_text = _reply_text(result)
        assert result.status == "done", result.error

        stock_calls = [args for name, args in fetch_calls if name == STOCK_TOOL]
        assert len(stock_calls) == 1, (
            f"test setup sanity: the primary fetch must run exactly once: {fetch_calls!r}"
        )
        product_ids = {str(u) for u in (stock_calls[0].get("product_ids") or [])}
        assert len(product_ids) == 2, (
            f"test setup sanity: two EXPLICIT typed codes must resolve to the two "
            f"seeded products, exact tier, no ambiguity: {stock_calls!r}"
        )
        incoming_calls = [(n, a) for n, a in probe_calls if n == INCOMING_TOOL]
        assert len(incoming_calls) == 1, (
            f"test setup sanity: the incoming rung must be probed exactly once: "
            f"probe_calls={probe_calls!r}"
        )

        # Each container's own rung row prints exactly once - the two codes share no
        # prefix relationship, so `crossdomain_render`'s own `code_key.startswith(n)`
        # zero-lookup (answer.py ~line 888) can never cross-match one code's row for
        # the other's missing entry here.
        assert reply_text.count("CONT-AAAA") == 1, reply_text
        assert reply_text.count("CONT-BBBB") == 1, reply_text

        assert f"No stock for" in reply_text, reply_text
        assert self.CODE_X in said and self.CODE_Y in said, (
            f"the zero note must name BOTH distinct codes - neither is a prefix of the "
            f"other, so nothing here should collapse into a shared row or a partial "
            f"note: {said!r}"
        )
