"""PRODUCT-ALTERNATIVES: the rendered Alternative line (card Q4 (a), owner 8 Oct 2026).

Dealer (availability): one extra line `Alternative: <code> x <N>: <verdict>` under the old
line, after its discontinued marker. The old line keeps its wording, except that
`Please refer to your salesman.` drops from it when an alternative line has stock.
Staff (compact): `*Alternative:* <code>` under the old block.

Built through the REAL MCP presenter and `fetch.output_structurer`, the way production
renders the stock tool's payload.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.services.chatbot.lanes.business import fetch


def _present(payload: dict) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.presenters import present_response
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return json.loads(present_response("crm_inventory_stock_balance_list", json.dumps(payload)))


def _avail(code, qty, branch, *, discontinued=False, available=None, eta=None, alternatives=None):
    return {
        "product_id": f"id-{code}",
        "product_code": code,
        "product_name": code,
        "needs_quantity": False,
        "requested_qty": qty,
        "branch": branch,
        "cap_unset": False,
        "category_name": "Category",
        "eta": eta,
        "packing_list": None,
        "available_qty": available,
        "is_discontinued": discontinued,
        "alternatives": alternatives or [],
    }


def _availability_reply(entries: list) -> str:
    envelope = _present(
        {
            "data": [],
            "pagination": {"total": len(entries), "page": 1, "limit": 50},
            "empty": False,
            "stock_visibility": {"mode": "availability", "source": "contact"},
            "stock_availability": entries,
            "last_updated_at": "2026-10-08T10:00:00",
        }
    )
    return fetch.output_structurer(envelope, {"semantic_input": {}})["response"]


def test_discontinued_with_in_stock_alternative_drops_refer_from_old_line():
    reply = _availability_reply(
        [
            _avail(
                "SRTBF11821",
                5,
                "no_incoming",
                discontinued=True,
                alternatives=[_avail("SRTBF11821-NEW", 5, "in_stock")],
            )
        ]
    )
    assert reply == (
        "SRTBF11821 x 5: ❌ No stock and no incoming.\n"
        "⚠️  *(PRODUCT DISCONTINUED)*\n"
        "Alternative: SRTBF11821-NEW x 5: ✅ Got stock."
    )


def test_alternative_without_stock_keeps_refer_on_both_lines():
    reply = _availability_reply(
        [
            _avail(
                "BMC6067",
                30,
                "too_big",
                discontinued=True,
                alternatives=[_avail("BMC6067-NEW", 30, "no_incoming")],
            )
        ]
    )
    assert reply == (
        "BMC6067 x 30: \U0001F6AB the quantity is more than what I can confirm here. "
        "Please refer to your salesman.\n"
        "⚠️  *(PRODUCT DISCONTINUED)*\n"
        "Alternative: BMC6067-NEW x 30: ❌ No stock and no incoming. "
        "Please refer to your salesman."
    )


def test_partial_and_eta_alternatives_use_the_existing_verdicts():
    reply = _availability_reply(
        [
            _avail(
                "SRTBF11416",
                10,
                "no_incoming",
                discontinued=True,
                alternatives=[_avail("SRTBF11416-NEW", 10, "in_stock", available=5)],
            ),
            _avail(
                "SRTWC286-SH-200",
                2,
                "no_incoming",
                alternatives=[_avail("SRTWC286-SH-NEW-200", 2, "incoming", eta="20/10/2026")],
            ),
        ]
    )
    assert reply == (
        "SRTBF11416 x 10: ❌ No stock and no incoming.\n"
        "⚠️  *(PRODUCT DISCONTINUED)*\n"
        "Alternative: SRTBF11416-NEW x 10: ✅ Only 5 got stock.\n\n"
        "SRTWC286-SH-200 x 2: ❌ No stock and no incoming. Please refer to your salesman.\n"
        "Alternative: SRTWC286-SH-NEW-200 x 2: ❌ No stock, ETA 20/10/2026."
    )


def test_no_alternative_reply_is_byte_identical_to_today():
    reply = _availability_reply([_avail("SRT5674", 5, "no_incoming", discontinued=True)])
    assert reply == (
        "SRT5674 x 5: ❌ No stock and no incoming. Please refer to your salesman.\n"
        "⚠️  *(PRODUCT DISCONTINUED)*"
    )


def test_compact_block_prints_alternative_code_under_marker():
    envelope = _present(
        {
            "data": [],
            "pagination": {"total": 2, "page": 1, "limit": 50},
            "empty": False,
            "stock_visibility": {"mode": "compact", "source": "contact", "warehouse_codes": None},
            "stock_summary": [
                {
                    "product_id": "a",
                    "product_code": "SRTBF11821",
                    "product_name": "Old",
                    "total_on_hand": 0,
                    "locations": [],
                    "flags": {"discontinued": True, "alternatives": ["SRTBF11821-NEW"]},
                },
                {
                    "product_id": "b",
                    "product_code": "SRTBF11821-NEW",
                    "product_name": "New",
                    "total_on_hand": 16,
                    "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 16}],
                    "flags": {"discontinued": False},
                },
            ],
            "last_updated_at": "2026-10-08T10:00:00",
        }
    )
    reply = fetch.output_structurer(envelope, {"semantic_input": {}})["response"]
    assert "⚠️  *(PRODUCT DISCONTINUED)*\n*Alternative:* SRTBF11821-NEW" in reply
    assert reply.count("*Alternative:*") == 1


def _stamped(entries: list) -> list:
    envelope = _present(
        {
            "data": [],
            "pagination": {"total": len(entries), "page": 1, "limit": 50},
            "empty": False,
            "stock_visibility": {"mode": "availability", "source": "contact"},
            "stock_availability": entries,
        }
    )
    return envelope["stock_availability"]


def test_a_refer_printed_on_an_alternative_line_is_logged_as_a_referral():
    """Reviewer round 1: CUSTOMER-ASKS-REFER-ONLY logs exactly the entries whose printed
    lines referred the dealer; an Alternative line that refers counts."""
    [entry] = _stamped(
        [
            _avail(
                "BMC6067",
                5,
                "in_stock",
                discontinued=True,
                alternatives=[_avail("BMC6067-NEW", 5, "no_incoming")],
            )
        ]
    )
    assert entry["refers_to_salesman"] is True


def test_an_in_stock_alternative_clears_the_old_lines_referral():
    [entry] = _stamped(
        [
            _avail(
                "SRTBF11821",
                5,
                "no_incoming",
                discontinued=True,
                alternatives=[_avail("SRTBF11821-NEW", 5, "in_stock")],
            )
        ]
    )
    assert entry["refers_to_salesman"] is False
