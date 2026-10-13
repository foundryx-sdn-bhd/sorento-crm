"""PV2-CORE: replay every fixture under `fixtures/` (CONTRACT-PV2-13oct.md section 7).

The shape check runs always; the apply/expect replay runs once CARRY's `pv2.apply` and
`pv2.state` exist (before that each case is skipped with the reason, never passed).
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.schema import ParserV2Output
from tests.chatbot.pv2 import replay

CASES = replay.all_cases()


def test_fixture_dir_has_cases():
    assert CASES, "no PV2 fixtures found"


@pytest.mark.parametrize("name,turns", CASES, ids=[c[0] for c in CASES])
def test_fixture_parser_out_is_valid(name, turns):
    for turn in turns:
        ParserV2Output.model_validate(turn["parser_out"])
        assert turn["source"] == "handwritten" or turn["source"].startswith(("converted:", "recorded:"))


@pytest.mark.parametrize("name,turns", CASES, ids=[c[0] for c in CASES])
def test_replay(name, turns):
    if not replay.carry_ready():
        pytest.skip("pv2.apply / pv2.state (PV2-CARRY) not merged yet")
    results = replay.run_case(turns)
    failures = [f"{r.where}: {f}" for r in results for f in r.failures]
    assert not failures, "\n".join(failures)


def test_check_reply_forbids_dot_separator_and_uuids():
    assert replay.check_reply("*Stock:* 3 · 4", {}) == ["reply contains forbidden ' · '"]
    assert replay.check_reply("id 1b4e28ba-2fa1-11d2-883f-0016d3cca427", {}) == ["reply leaks a UUID"]
    assert replay.check_reply("*Stock:* 3", {"reply_must": ["*Stock:*"]}) == []
