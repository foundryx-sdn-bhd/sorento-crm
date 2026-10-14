"""PV2-EVAL UAC-4: deterministic checks. Pure functions."""
from __future__ import annotations

import pytest

from scripts.pv2_battery.checks import CheckFail, TurnRecord, find_internal_keys, find_uuids, run_checks, sent_files

UUID = "3f2b8c1e-9d4a-4e6b-8a1c-5d7e9f0a1b2c"


def msg(text, **kw):
    return {"kind": "send_message", "text": text, "quick_replies": [], "dry_run": True, **kw}


def files(*urls):
    return {"kind": "send_attachments", "attachments_src": [{"url": u} for u in urls], "dry_run": True}


def turn(reply="Here you go.", actions=None, **kw):
    acts = [msg(reply)] if actions is None else actions
    return TurnRecord(text="hi", reply=reply, actions=acts, **kw)


def names(fails):
    return {f.check for f in fails}


def test_clean_turn_passes():
    assert run_checks(turn(), {"kind": "any", "about": "x"}, None) == []


def test_checkfail_shape():
    f = CheckFail("must", "missing X")
    assert f.check == "must" and f.detail == "missing X"


def test_uuid_leak():
    assert find_uuids(f"id {UUID} here") == [UUID] or UUID in find_uuids(f"id {UUID} here")
    assert find_uuids("SO422057 and 202610-0151") == []
    assert "uuid_leak" in names(run_checks(turn(f"ref {UUID}"), {"about": "x"}, None))


@pytest.mark.parametrize("text", [
    "DISPLAY_STANDEE_APPLICATION_FORM_MOCHA", "see branch_kind", "run stock_check", "Hello {{name}}",
    "{code} has stock", "Result: None", "value is null", "got undefined"])
def test_internal_keys_detected(text):
    assert find_internal_keys(text), text
    assert "internal_key_leak" in names(run_checks(turn(text), {"about": "x"}, None))


@pytest.mark.parametrize("text", [
    "SRTWC286-SH has 12 units", "CKS1052-BL and SRTWF12C-GM-NEW", "DO 202610-0151 and SO422057",
    "https://cdn.example/files/my_file_name.jpg", "SRTWC286-SH_TD.jpg", "Nonetheless nothing",
    "Anyone home? Nulled values are fine"])
def test_not_internal_keys(text):
    assert find_internal_keys(text) == [], text
    assert "internal_key_leak" not in names(run_checks(turn(text), {"about": "x"}, None))


def test_sent_files():
    acts = [msg("a"), files("https://cdn.example/A.jpg", "https://cdn.example/B.jpg")]
    got = sent_files(acts)
    assert len(got) == 2 and got[0]["url"].endswith("A.jpg")
    assert sent_files([msg("a")]) == []


def test_silent():
    assert "silent" in names(run_checks(turn("", actions=[]), {"about": "x"}, None))
    assert "silent" in names(run_checks(turn("  ", actions=[msg("   ")]), {"about": "x"}, None))
    assert "silent" in names(run_checks(turn("x", actions=[files("https://cdn.example/A.jpg")]), {"about": "x"}, None))
    assert "silent" not in names(run_checks(turn(), {"about": "x"}, None))


def test_false_gap_real_example():
    t = turn("SRTWC286-SH has no Product Photos.",
             actions=[msg("SRTWC286-SH has no Product Photos."),
                      files("https://cdn.example/SRTWC286-SH.jpg", "https://cdn.example/SRTWC286-SH_TD.jpg")])
    assert "false_gap" in names(run_checks(t, {"about": "x"}, None))


@pytest.mark.parametrize("reply", ["SRTWC286-SH tiada Gambar.", "SRTWC286-SH 没有产品图片。"])
def test_false_gap_other_languages(reply):
    t = turn(reply, actions=[msg(reply), files("https://cdn.example/SRTWC286-SH.jpg")])
    assert "false_gap" in names(run_checks(t, {"about": "x"}, None))


def test_false_gap_not_for_code_whose_files_were_not_sent():
    reply = "Here are SRTWC286-SH files. CKS1052-BL has no Product Photos."
    t = turn(reply, actions=[msg(reply), files("https://cdn.example/SRTWC286-SH.jpg")])
    assert "false_gap" not in names(run_checks(t, {"about": "x"}, None))


def test_false_gap_needs_files_sent():
    t = turn("SRTWC286-SH has no Product Photos.")
    assert "false_gap" not in names(run_checks(t, {"about": "x"}, None))


def test_stuck():
    assert "stuck" in names(run_checks(turn("Same answer."), {"about": "x"}, "  Same answer.\n"))
    assert "stuck" not in names(run_checks(turn("Same answer."), {"about": "x", "allow_repeat": True}, "Same answer."))
    assert "stuck" not in names(run_checks(turn("New."), {"about": "x"}, "Old."))
    assert "stuck" not in names(run_checks(turn("New."), {"about": "x"}, None))


def test_must_is_case_insensitive_substring():
    ok = run_checks(turn("Stock for srtwc286 is 5"), {"about": "x", "must": ["SRTWC286", "5"]}, None)
    assert "must" not in names(ok)
    bad = run_checks(turn("Stock is 5"), {"about": "x", "must": ["SRTWC286"]}, None)
    assert "must" in names(bad)
    assert any("SRTWC286" in f.detail for f in bad if f.check == "must")


def test_must_any_groups():
    exp = {"about": "x", "must_any": [["ETA", "incoming"], ["units", "pcs"]]}
    assert "must_any" not in names(run_checks(turn("Incoming 5 pcs"), exp, None))
    assert "must_any" in names(run_checks(turn("Incoming 5"), exp, None))


def test_must_not():
    exp = {"about": "x", "must_not": ['Which "mou"']}
    assert "must_not" in names(run_checks(turn('which "MOU" do you mean'), exp, None))
    assert "must_not" not in names(run_checks(turn("fine"), exp, None))


def test_attachments_true_false():
    sent = turn("ok", actions=[msg("ok"), files("https://cdn.example/A.jpg")])
    unsent = turn("ok")
    assert "attachments" in names(run_checks(unsent, {"about": "x", "attachments": True}, None))
    assert "attachments" not in names(run_checks(sent, {"about": "x", "attachments": True}, None))
    assert "attachments" in names(run_checks(sent, {"about": "x", "attachments": False}, None))
    assert "attachments" not in names(run_checks(unsent, {"about": "x", "attachments": False}, None))
    assert "attachments" not in names(run_checks(sent, {"about": "x"}, None))


def test_v1_fallback():
    assert "v1_fallback" in names(run_checks(turn(mode="v2", pv2_event=False), {"about": "x"}, None))
    assert "v1_fallback" not in names(run_checks(turn(mode="v2", pv2_event=True), {"about": "x"}, None))
    assert "v1_fallback" not in names(run_checks(turn(mode="v1", pv2_event=False), {"about": "x"}, None))


def test_version_mismatch():
    assert "version_mismatch" in names(run_checks(turn(prompt_version="a", expected_version="b"), {"about": "x"}, None))
    assert "version_mismatch" not in names(run_checks(turn(prompt_version="a", expected_version="a"), {"about": "x"}, None))
    assert "version_mismatch" not in names(run_checks(turn(), {"about": "x"}, None))


def test_tool_error():
    assert "tool_error" in names(run_checks(turn(tool_errors=["boom"]), {"about": "x"}, None))
    assert "tool_error" not in names(run_checks(turn(), {"about": "x"}, None))
