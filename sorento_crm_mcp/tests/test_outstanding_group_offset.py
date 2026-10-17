"""CHAT-COMPOSER Lists slice red tests, L2 (MCP half): `group_offset` pages the By customer / By product
group of `crm_outstanding_report`, 20 at a time, only when the argument is present.

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` section 2 (L2-b, L2-c, L2-d), Q1 (a),
section 7. The presenter reads `group_offset` as a route echo on the body (the `detail` / `location_token`
precedent). Absent: today's bytes exactly. Written before any of it exists.
"""
from __future__ import annotations

import asyncio

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import _outstanding_envelope, _outstanding_header_lines
from sorento_crm_mcp.server import _compile_tool
from tests.test_outstanding_brand_arg import _FakeCtx, _RecordingClient

MORE_20 = 'Reply "more" to see the next 20.'
DO_OFFER = "Reply 1 for the delivery order list."


def _header(**over):
    base = {
        "product_code": "ZZT-A",
        "customer_name": None,
        "location_token": None,
        "warehouse_codes": [],
        "order_date_from": None,
        "order_date_to": None,
    }
    return {**base, **over}


def _customers(n, *, so=True):
    keys = ("ordered_qty", "outstanding_qty") if so else ("do_qty", "pending_qty")
    return [{"customer_name": f"Customer {i + 1:02d}", keys[0]: 100 - i, keys[1]: 50 - (i % 50)} for i in range(n)]


def _so_block(rows):
    return {
        "so": {"so_count": 7, "ordered_qty": 700, "transferred_qty": 100, "outstanding_qty": 600,
               "order_date_min": "2026-01-01", "order_date_max": "2026-09-01"},
        "so_by_location": [{"code": "BRW", "ordered_qty": 700, "outstanding_qty": 600}],
        "so_by_customer": rows,
    }


def _do_block(rows):
    return {
        "do": {"do_count": 4, "do_qty": 400, "delivered_qty": 100, "pending_qty": 300,
               "do_date_min": "2026-01-01", "do_date_max": "2026-09-01"},
        "do_by_location": [{"code": "BRW", "do_qty": 400, "pending_qty": 300}],
        "do_by_customer": rows,
    }


def _do_only(n, offset=None):
    report = {**_header(), **_do_block(_customers(n, so=False))}
    if offset is not None:
        report["group_offset"] = offset
    return report


def _names(text):
    return [line.split(":")[0] for line in text.splitlines() if line.startswith("Customer ")]


def test_no_group_offset_golden():
    env = _outstanding_envelope(_do_only(45))
    text = env["response"]
    assert _names(text) == [f"Customer {i:02d}" for i in range(1, 21)]
    assert "... and 25 more customers" in text
    assert 'Reply "more"' not in text
    assert "group_window" not in env
    assert text.endswith(DO_OFFER)


def test_page_one():
    env = _outstanding_envelope(_do_only(45, offset=0))
    text = env["response"]
    assert _names(text) == [f"Customer {i:02d}" for i in range(1, 21)]
    assert "... and" not in text
    assert text.endswith(f"{MORE_20}\n\n{DO_OFFER}"), text[-160:]
    assert env["group_window"] == {"offset": 0, "shown": 20, "total": 45}


def test_page_two_continues_the_ranking():
    env = _outstanding_envelope(_do_only(45, offset=20))
    text = env["response"]
    assert _names(text) == [f"Customer {i:02d}" for i in range(21, 41)]
    assert 'Reply "more" to see the next 5.' in text
    assert text.endswith(DO_OFFER)
    assert text.startswith("\n".join(_outstanding_header_lines(_do_only(45, offset=20))))
    assert "*Delivery order outstanding*" in text and "*_By location_*" in text


def test_last_page_and_past_the_end():
    last = _outstanding_envelope(_do_only(45, offset=40))
    assert _names(last["response"]) == [f"Customer {i:02d}" for i in range(41, 46)]
    assert 'Reply "more"' not in last["response"]
    assert last["response"].endswith(DO_OFFER)
    assert last["group_window"] == {"offset": 40, "shown": 5, "total": 45}

    past = _outstanding_envelope(_do_only(45, offset=60))
    expected = "\n".join(_outstanding_header_lines(_do_only(45, offset=60))) + "\n\nThat is all 45.\n\n" + DO_OFFER
    assert past["response"] == expected
    assert _names(past["response"]) == []


def test_two_blocks_share_the_offset():
    report = {**_header(), **_so_block(_customers(45)), **_do_block(_customers(30, so=False)), "group_offset": 20}
    env = _outstanding_envelope(report)
    text = env["response"]
    so_part, do_part = text.split("*Delivery order outstanding*")
    assert _names(so_part) == [f"Customer {i:02d}" for i in range(21, 41)]
    assert _names(do_part) == [f"Customer {i:02d}" for i in range(21, 31)]
    assert 'Reply "more" to see the next 5.' in text
    assert text.index('Reply "more"') < text.index("Reply with a number for detail:")
    assert env["group_window"]["offset"] == 20 and env["group_window"]["total"] == 45


def test_a_negative_or_non_integer_offset_is_ignored():
    plain = _outstanding_envelope(_do_only(45))["response"]
    for bad in (-1, "20", None):
        report = _do_only(45)
        report["group_offset"] = bad
        env = _outstanding_envelope(report)
        assert env["response"] == plain, bad
        assert "group_window" not in env


def test_the_cap_literal_is_pinned():
    from sorento_crm_mcp.presenters import OUTSTANDING_GROUP_CAP

    assert OUTSTANDING_GROUP_CAP == 20


def test_catalog_exposes_and_forwards_group_offset():
    spec = next(s for s in CATALOG if s.name == "crm_outstanding_report")
    assert "group_offset" in spec.query_params, spec.query_params

    async def call():
        client = _RecordingClient()
        await _compile_tool(spec)(_FakeCtx(client), group_offset=20)
        return client.calls

    calls = asyncio.run(call())
    assert calls and calls[0]["query"].get("group_offset") == "20", calls


def test_a_do_block_longer_than_the_so_block_drives_the_more_line_and_page_two():
    """K3: the more line counts the LONGEST group's next page and page 2 continues the DO block."""
    base = {**_header(), **_so_block(_customers(25)), **_do_block(_customers(45, so=False))}
    first = _outstanding_envelope({**base, "group_offset": 0})
    assert MORE_20 in first["response"]
    assert first["group_window"]["total"] == 45

    second = _outstanding_envelope({**base, "group_offset": 20})
    text = second["response"]
    so_part, do_part = text.split("*Delivery order outstanding*")
    assert _names(so_part) == [f"Customer {i:02d}" for i in range(21, 26)]
    assert _names(do_part) == [f"Customer {i:02d}" for i in range(21, 41)]
    assert 'Reply "more" to see the next 5.' in text
    assert second["group_window"] == {"offset": 20, "shown": 20, "total": 45}

    third = _outstanding_envelope({**base, "group_offset": 40})
    so_part, do_part = third["response"].split("*Delivery order outstanding*")
    assert _names(so_part) == [] and _names(do_part) == [f"Customer {i:02d}" for i in range(41, 46)]
    assert 'Reply "more"' not in third["response"]
