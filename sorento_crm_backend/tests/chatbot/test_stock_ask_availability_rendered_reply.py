"""Chatbot stock ask v2 S3 fix round 1, Blocking 1 (R6 B1, R14) and AC-SA312.

Reviewer pass, PR #1247 S3 at e01ef5c2: AC-SA312's regex guard ran only over the MCP
presenter's item TITLE (`sorento_crm_mcp/tests/test_presenters_availability_lines.py`),
never over the actual WhatsApp text `output_structurer` builds - so `_availability_intro`'s
shared intro and `_item_line`'s position number (`f"{position}. "`) both reached the
dealer unguarded. `_availability_intro`'s old `_AVAILABILITY_NO` line ("Sorry, we do not
have enough stock for that quantity.") is a statement about OUR stock, which R6 B1
forbids, and is false for a `too_big` entry with plenty on hand. This file guards the
RENDERED reply `output_structurer` produces, not the presenter's item title alone.
Reviewer pass round 2, PR #1247 S3 at ae16ee35, Blocking 1 (the part round 1 left unfixed):
the hand-built `_availability_envelope` helper below never carries `last_updated_at`, so the
footer `output_structurer` appends at the end of every reply (`fetch.py`'s `_fmt_ts` +
`"_Data last updated: ..._"` line, fed by `StockService._apply_stock_visibility` setting
`payload["last_updated_at"]` for every policy mode, availability included) went unchecked.
`test_answered_reply_carries_no_last_updated_footer` below builds the envelope through the
REAL MCP presenter (`sorento_crm_mcp.presenters.present_response`) with `last_updated_at` set,
the way production always has it, so the footer is actually exercised.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from app.services.chatbot.lanes.business import fetch


def _entry(code: str, qty: int, branch: str, tail: str) -> dict:
    return {
        "title": f"{code} x {qty}: {tail}",
        "fields": [],
        "flags": {"needs_quantity": False, "branch": branch},
    }


def _availability_envelope(items: list) -> dict:
    return {
        "result_type": "stock_availability",
        "intro": "",
        "items": items,
        "has_result": True,
    }


def test_answered_reply_carries_no_intro_and_no_numbering():
    """A too_big entry with stock on hand elsewhere must never be preceded by a
    sentence claiming OUR stock is short, and no line is prefixed with a position
    number - the item title alone (product, quantity, R6 sentence) is the whole
    answer, plan sample (g)."""
    entries = [
        _entry(
            "SRT-TOOBIG",
            250,
            "too_big",
            "🚫 the quantity is more than what I can confirm here. Please refer to "
            "your salesman.",
        ),
        _entry(
            "SRT-INSTOCK",
            5,
            "in_stock",
            "\u2705 Please refer to your salesman.",
        ),
    ]
    out = fetch.output_structurer(_availability_envelope(entries), {"semantic_input": {}})

    assert "Sorry, we do not have enough stock" not in out["response"]
    assert "enough stock" not in out["response"]
    assert not out["response"].startswith("1.")
    assert "1. SRT-TOOBIG" not in out["response"]
    assert "2. SRT-INSTOCK" not in out["response"]
    assert out["response"] == (
        "SRT-TOOBIG x 250: 🚫 the quantity is more than what I can confirm here. "
        "Please refer to your salesman.\n\n"
        "SRT-INSTOCK x 5: \u2705 Please refer to your salesman."
    )


def test_ac_sa312_rendered_reply_has_no_digit_of_ours():
    """AC-SA312, over the RENDERED reply: the only digits anywhere in the message are
    the dealer's own asked quantities and the ETA date, never a position number or a
    stock figure of ours."""
    entries = [
        _entry(
            "SRT-BIG",
            42,
            "too_big",
            "🚫 the quantity is more than what I can confirm here. Please refer to "
            "your salesman.",
        ),
        _entry("SRT-ETA", 7, "incoming", "no stock at the moment, ETA 19/10/2026."),
    ]
    out = fetch.output_structurer(_availability_envelope(entries), {"semantic_input": {}})

    assert re.findall(r"\d+", out["response"]) == ["42", "7", "19", "10", "2026"]


def _real_entry(code: str, qty: int, branch: str, *, eta: str | None = None) -> dict:
    """The raw shape `StockService._apply_stock_visibility` puts on the wire for an
    `availability` entry (`inventory_service.py`'s entry shape, AC-SA310)."""
    return {
        "product_id": "33333333-3333-4333-8333-333333333333",
        "product_code": code,
        "product_name": code,
        "needs_quantity": False,
        "requested_qty": qty,
        "branch": branch,
        "cap_unset": None,
        "category_name": "Category",
        "eta": eta,
        "packing_list": None,
    }


def _real_availability_envelope(entries: list, last_updated_at: str = "2026-08-24T18:00:00") -> dict:
    """The REAL MCP presenter, imported the way `tests/chatbot/test_outstanding_lane.py`
    imports it: append the `sorento_crm_mcp` that sits beside THIS checkout's backend, so
    a stale editable install in the shared venv cannot win. Skipped, never failed, where
    the package is absent. `last_updated_at` set on the raw payload is what makes this
    envelope carry the one field the hand-built `_availability_envelope` above never had -
    the same field `StockService._apply_stock_visibility` sets in production for every
    policy mode, availability included."""
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.presenters import present_response
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    payload = {
        "data": [],
        "pagination": {"total": 0, "page": 1, "limit": 50},
        "empty": True,
        "stock_visibility": {"mode": "availability", "warehouse_codes": None, "source": "contact"},
        "stock_availability": entries,
        "last_updated_at": last_updated_at,
    }
    raw = json.dumps(payload)
    return json.loads(present_response("crm_inventory_stock_balance_list", raw))


def test_answered_reply_carries_no_last_updated_footer():
    """Reviewer pass round 2 Blocking 1: production always sets `last_updated_at`
    (`inventory_service.py:1322`, every policy mode). `output_structurer` (`fetch.py:2633`)
    appends it as `_Data last updated: dd/mm/yyyy hh:mm:ss_` - a line that starts with
    neither a product code nor one of the four R6 sentences, and whose digits are ours,
    which AC-SA312 forbids. An answered `stock_availability` reply must not carry it."""
    entries = [_real_entry("SRT-BIG", 42, "too_big"), _real_entry("SRT-ETA", 7, "incoming", eta="19/10/2026")]
    envelope = _real_availability_envelope(entries)

    out = fetch.output_structurer(envelope, {"semantic_input": {}})

    assert "Data last updated" not in out["response"]
    assert re.findall(r"\d+", out["response"]) == ["42", "7", "19", "10", "2026"]


# --------------------------------------------------------------------------- #
# Owner hand test 26 Sep, slice 1 (scout comment on PR #1247, section 5): the footer
# was gated on "every entry answered", so the QUANTITY QUESTION turns (T1, T3, T8,
# T13, T16) still ended in `_Data last updated: ..._` - a timestamp of ours on an
# availability reply, which R14 / AC-SA312 do not allow. The gate is the result type.
# --------------------------------------------------------------------------- #


def _asking_entry(code: str) -> dict:
    return {
        **_real_entry(code, 0, "in_stock"),
        "needs_quantity": True,
        "requested_qty": None,
        "branch": None,
    }


_SRTWC286_FAMILY = [
    "SRTWC286-SH",
    "SRTWC286-SH-UF",
    "SRTWC286-SH-L",
    "SRTWC286-SH-NL",
    "SRTWC286-SH-W",
    "SRTWC286-SH-B",
    "SRTWC286-SH-G",
    "SRTWC286-SH-M",
    "SRTWC286-SH-C",
    "SRTWC286-SH-R",
]


def test_t1_quantity_question_reply_carries_no_footer():
    """T1 "check stock srtwc286": ten entries, every one `needs_quantity`. The reply is
    a question, and still an availability reply - no timestamp of ours on it."""
    envelope = _real_availability_envelope([_asking_entry(c) for c in _SRTWC286_FAMILY])

    out = fetch.output_structurer(envelope, {"semantic_input": {}})

    assert "Data last updated" not in out["response"]
    assert "How many units do you need?" in out["response"]


def test_t16_one_product_quantity_question_carries_no_footer():
    """T16 / T13 shape: one product, no quantity bound - the single-product question."""
    envelope = _real_availability_envelope([_asking_entry("ELP3754")])

    out = fetch.output_structurer(envelope, {"semantic_input": {}})

    assert "Data last updated" not in out["response"]


def test_compact_reply_keeps_its_footer_r10():
    """R10: staff (compact / detailed) replies are untouched - the footer still prints."""
    envelope = {
        "result_type": "stock_compact",
        "intro": "Stock summary for the requested products.",
        "items": [{"title": "SRT-A", "fields": [{"label": "KL", "value": 5}]}],
        "has_result": True,
        "last_updated_at": "2026-08-24T18:00:00",
    }

    out = fetch.output_structurer(envelope, {"semantic_input": {}})

    assert "Data last updated" in out["response"]
