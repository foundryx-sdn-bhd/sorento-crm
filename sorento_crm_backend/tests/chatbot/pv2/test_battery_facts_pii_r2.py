"""PV2-EVAL round 12: inbound channel.meta JSON string and contact ids must not reach the judge."""
from __future__ import annotations

import pytest

from scripts.pv2_battery import runner

META_STR = '{"profile": {"name": "Ali Test"}, "firstName": "Ali", "lastName": "Test", "user_id": "u-1"}'


def _looked(raw):
    return [{"stage": "looked_up", "raw": raw}]


def test_inbound_channel_meta_string_and_contact_ids_dropped():
    d = runner.facts_digest(_looked({
        "channel": {"name": "WA", "meta": META_STR}, "contact_id": "487555417", "contactId": 487555417,
        "rows": [{"Customer": "HANLIM TRADING SDN BHD [A/C I]", "Product Code": "SRTWC286-SH"}]}))
    for needle in ("Ali", "Test", "u-1", "487555417"):
        assert needle not in d, needle
    assert "HANLIM TRADING SDN BHD [A/C I]" in d and "SRTWC286-SH" in d


@pytest.mark.parametrize("key", ["contact_id", "contactid", "contactId", "CONTACT_ID", "Contact_Id"])
def test_contact_id_keys_dropped_case_and_underscore_insensitive(key):
    d = runner.facts_digest(_looked({key: "CIDMARK123", "rows": [{"Qty": "KEEPQ"}]}))
    assert "CIDMARK123" not in d and "KEEPQ" in d


def test_channel_or_meta_kept_when_plain_scalar():
    d = runner.facts_digest(_looked({"channel": "dealer", "meta": "SCALARMETA", "rows": []}))
    assert "dealer" in d and "SCALARMETA" in d


def test_channel_or_meta_dropped_when_dict_or_json_object_string():
    d = runner.facts_digest(_looked({"channel": {"name": "DICTDROP"}, "meta": '{"a": "STRDROP"}'}))
    assert "DICTDROP" not in d and "STRDROP" not in d


def test_non_object_json_string_is_a_scalar_and_kept():
    d = runner.facts_digest(_looked({"meta": "[1, 2]", "channel": "12345"}))
    assert "[1, 2]" in d and "12345" in d


def test_tool_args_keep_report_filter_channel_and_meta():
    # tool args are the bot's own call arguments (reports.py channel filter), not the inbound envelope
    d = runner.facts_digest([{"kind": "tool", "name": "outstanding", "args": {"channel": "dealer", "meta": {"total": 12}},
                              "envelope": "rows"}])
    assert "dealer" in d and "total" in d
