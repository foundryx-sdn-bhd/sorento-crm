"""PV2-EVAL round 10: the judge is an external API; no inbound personal data may reach the facts digest."""
from __future__ import annotations

import re

from scripts.pv2_battery import runner

WAMID = "wamid.HBgLNjAxMjM0NTY3ODkVAgASGBQ"
LOOKED = {"stage": "looked_up", "facts": {"n": 1}, "raw": {
    "envelope": {"contact": {"firstName": "Ali", "lastName": "Test", "phone": "+60123456789", "id": 900000001},
                 "message": {"messageId": WAMID}, "channel": {"name": "WA 60123456789"}},
    "rows": [{"Product Code": "SRTWC286-SH", "Quantity On Hand": 3, "Doc": "202610-0151"}],
    "lane_text": "ORDER SO422057 and PO 202606-S0105 for CKS1052-BL"}}
TOOL = {"kind": "tool", "name": "lookup", "args": {"q": "x"},
        "envelope": "Salesman line 60123456789 or +60 12-345 6789; doc SO422057 qty 5"}


def _digest():
    return runner.facts_digest([LOOKED, TOOL])


def test_pii_values_are_gone():
    d = _digest()
    for needle in ("Ali", "Test", "wamid", WAMID, "60123456789", "12-345 6789", "900000001", "WA 60"):
        assert needle not in d, needle


def test_no_phone_like_digit_run():
    d = _digest()
    assert not re.search(r"\d{8,}", d)
    assert not re.search(r"\+\s?\d{2}[\s-]?\d{2}[\s-]\d{3}[\s-]?\d{4}", d)


def test_business_data_and_document_numbers_survive():
    d = _digest()
    for needle in ("SRTWC286-SH", "Quantity On Hand", "202610-0151", "SO422057", "202606-S0105", "CKS1052-BL"):
        assert needle in d, needle


def test_identity_keys_dropped_at_any_depth_case_insensitively():
    trace = [{"stage": "looked_up", "raw": {"rows": [{"Product Code": "AAA111", "Contact": {"n": "KEEPOUT1"}}],
                                            "deep": {"x": {"META": {"a": "KEEPOUT2"}, "Profile": "KEEPOUT3",
                                                           "Channel": {"name": "KEEPOUT4"}, "Envelope": "KEEPOUT5"}}}}]
    d = runner.facts_digest(trace)
    assert "AAA111" in d
    for n in range(1, 6):
        assert f"KEEPOUT{n}" not in d
