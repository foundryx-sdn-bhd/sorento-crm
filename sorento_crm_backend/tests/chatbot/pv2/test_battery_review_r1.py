"""PV2-EVAL round 6: reviewer findings. Fakes only, no database, no model."""
from __future__ import annotations

import contextlib
import inspect
import types

import pytest

from scripts.pv2_battery import checks, guard, judge, report, runner, schema

PINS = {"v2": {"id": "V2ID", "number": 12, "md5": "m2"}, "v1": {"id": "V1ID", "number": 91, "md5": "m1"}}


class GarbageClient:
    def __init__(self, out="total garbage"):
        self.calls, self.out = 0, out

    def complete(self, prompt):
        self.calls += 1
        return self.out


def _conv(turns, audience="staff", cid="stock-i01", shapes=("refine",)):
    return schema.Conversation(
        id=cid, source="improvised", origin=None, domain="stock", audience=audience, shapes=list(shapes),
        turns=[schema.Turn(t, e) for t, e in turns], file="stock.yaml")


def _res(reply="Fine answer.", actions=None, version="V2ID", turn_id="t1"):
    acts = actions if actions is not None else [{"kind": "send_message", "text": reply, "quick_replies": []}]
    return types.SimpleNamespace(turn_id=turn_id, branch_kind="x", reply_text=reply, quick_replies=[],
                                 session_vars={}, prompt_version=version, actions=acts)


@pytest.fixture()
def fakes(monkeypatch):
    from app.services.chatbot import console_service
    import app.database as database

    class Db:
        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", lambda: Db())
    monkeypatch.setattr(runner, "read_trace",
                        lambda turn_id: [{"kind": "pv2_turn", "parser_out": {"asks": []}}])
    box = types.SimpleNamespace(results=[], seen=[])

    def fake_run(db, **kw):
        box.seen.append(kw)
        item = box.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(console_service, "run_console_turn", fake_run)
    return box


def _run(conv, judge_client=None, mode="v2"):
    rows = []
    out = runner.run_conversation(conv, mode=mode, rep=1, contact_id="000000000", pins=PINS,
                                  judge_client=judge_client, sink=rows.append)
    return out


ANS = {"kind": "answer", "about": "x"}


# 1 ---------------------------------------------------------------------------
def test_lanes_once_enters_real_once_and_restores(monkeypatch):
    from app.services.chatbot import console_service

    entered = []

    @contextlib.contextmanager
    def fake_lanes():
        entered.append(1)
        yield

    monkeypatch.setattr(console_service, "_lanes_on", fake_lanes)
    with runner.lanes_once():
        for _ in range(3):
            with console_service._lanes_on():
                pass
        assert len(entered) == 1
    assert console_service._lanes_on is fake_lanes
    assert len(entered) == 1


def test_run_path_uses_lanes_once():
    assert "lanes_once" in inspect.getsource(runner.run)


# 2 ---------------------------------------------------------------------------
def test_garbage_judge_is_not_ok(fakes):
    fakes.results = [_res()]
    rows = _run(_conv([("hi", ANS)]), GarbageClient())
    assert rows[0]["judge"]["verdict"] == "judge_error" and rows[0]["ok"] is False


def test_deterministic_fail_skips_judge(fakes):
    fakes.results = [_res("leak 3f2b8c1e-9d4a-4e6b-8a1c-5d7e9f0a1b2c")]
    client = GarbageClient()
    rows = _run(_conv([("hi", ANS)]), client)
    assert client.calls == 0 and rows[0]["ok"] is False
    assert any(c["check"] == "uuid_leak" for c in rows[0]["checks"])


def test_envelope_patch_carries_both_pins(monkeypatch):
    from app.services.chatbot import console_service

    monkeypatch.setattr(console_service, "_build_envelope", lambda *a, **k: {"is_test": True, "console_origin": {"u": 1}})
    had = hasattr(console_service, "_battery_real_build")
    console_service._battery_real_build = None
    try:
        runner.install_envelope_patch(PINS)
        env = console_service._build_envelope()
    finally:
        if not had:
            del console_service._battery_real_build
    assert env["prompt_overrides"] == {"chatbot_semantic_parser": "V1ID", "chatbot_semantic_parser_v2": "V2ID"}
    assert env["is_test"] is True and "console_origin" not in env


# 3 ---------------------------------------------------------------------------
def test_all_bubbles_graded_and_joined(fakes):
    acts = [{"kind": "send_message", "text": "First bubble."}, {"kind": "send_message", "text": "Second bubble BAD."}]
    fakes.results = [_res("First bubble.", actions=acts)]
    rows = _run(_conv([("hi", {"kind": "answer", "about": "x", "must_not": ["BAD"]})]))
    assert rows[0]["reply"] == "First bubble.\n\nSecond bubble BAD."
    assert rows[0]["ok"] is False and any(c["check"] == "must_not" for c in rows[0]["checks"])


# 4 ---------------------------------------------------------------------------
def test_schedule_keeps_audience_in_one_lane():
    convs = [_conv([("a", ANS)], audience=a, cid=f"c-{i}")
             for i, a in enumerate(["staff", "dealer", "staff", "unlinked", "dealer", "staff", "dealer_nogrant"])]
    lanes = runner.schedule(convs, 3)
    assert sorted(c.id for lane in lanes for c in lane) == sorted(c.id for c in convs)
    owner = {}
    for n, lane in enumerate(lanes):
        for c in lane:
            assert owner.setdefault(c.audience, n) == n
    assert len(lanes) <= 3


# 5 ---------------------------------------------------------------------------
GOOD = {"HTTPS_PROXY": "http://127.0.0.1:9", "DATABASE_URL": "postgresql://u:p@localhost/dev"}


@pytest.mark.parametrize("over", [{"NO_PROXY": "*"}, {"no_proxy": "*"}, {"NO_PROXY": "localhost,*"},
                                  {"https_proxy": "http://proxy.example:3128"}])
def test_check_env_egress_holes(over):
    assert guard.check_env(dict(GOOD, **over))


def test_harden_env():
    env = {"NO_PROXY": "*", "no_proxy": "*", "https_proxy": "http://x:1"}
    out = guard.harden_env(env)
    e = out if isinstance(out, dict) else env
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        assert e[k] == "http://127.0.0.1:9"
    for k in ("NO_PROXY", "no_proxy"):
        assert "*" not in e[k]
        for host in ("localhost", "127.0.0.1", "api.openai.com", "api.anthropic.com"):
            assert host in e[k].split(",")
    assert guard.check_env(dict(e, DATABASE_URL=GOOD["DATABASE_URL"])) == []


def test_check_ideation_url():
    for ok in (None, "", "http://localhost:8000/x", "http://127.0.0.1:9/y", "http://[::1]:80/z"):
        guard.check_ideation_url(ok)
    with pytest.raises(guard.GuardError):
        guard.check_ideation_url("https://hooks.example.com/ideation")


# 6 ---------------------------------------------------------------------------
def test_rubric_false_gap_rule_is_type_and_code_aware():
    rule = next(l for l in judge.RUBRIC.splitlines() if "False gap" in l or "false gap" in l.lower().split(".")[0])
    low = judge.RUBRIC.lower()
    assert "type" in rule.lower() and "code" in rule.lower()
    assert "file count" not in low


def test_judge_prompt_uses_row_audience_and_calibrate_reads_it():
    p = judge.build_judge_prompt(history=[], text="t", reply="r", files=0, expect=ANS, facts="", audience="dealer_nogrant")
    assert "dealer_nogrant" in p
    assert "audience" in inspect.getsource(runner.calibrate)


# 7 ---------------------------------------------------------------------------
def test_email_and_extensionless_url_not_internal_keys():
    assert checks.find_internal_keys("Write to sales_team@example.com please") == []
    assert checks.find_internal_keys("See https://cdn.example.com/promo_flyer/view now") == []
    assert checks.find_internal_keys("Outstanding: None.")


# 8 ---------------------------------------------------------------------------
def test_table_first_fail_per_mode_and_shapes_column():
    conv = _conv([("a", ANS)] * 3, shapes=("typo", "particle"))
    def row(mode, turn, ok):
        return {"conversation": conv.id, "mode": mode, "rep": 1, "turn": turn, "ok": ok, "checks": [], "judge": None}
    rows = [row("v2", 1, True), row("v2", 2, False), row("v2", 3, True),
            row("v1", 1, True), row("v1", 2, True), row("v1", 3, False)]
    table = report.render_table(report.build_results([conv], rows),
                                {"v2": PINS["v2"], "v1": PINS["v1"], "git_sha": "x", "judge_agreement": 0.95})
    lines = table.splitlines()
    header = next(l for l in lines if l.startswith("|") and "conversation" in l).lower()
    assert "shapes" in header
    line = next(l for l in lines if conv.id in l and l.startswith("|"))
    assert "typo" in line and "particle" in line
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert "2" in cells and "3" in cells


# 9 ---------------------------------------------------------------------------
def test_turn_exception_is_runner_error_and_run_continues(fakes):
    fakes.results = [RuntimeError("boom"), _res("Second turn fine.")]
    rows = _run(_conv([("one", ANS), ("two", ANS)]))
    assert len(rows) == 2
    assert rows[0]["ok"] is False
    assert any(c["check"] == "runner_error" for c in rows[0]["checks"])
    assert rows[1]["reply"] == "Second turn fine."


def test_file_names_come_from_filename_and_type(fakes):
    item = {"url": "https://cdn.example/u.jpg", "name": "WRONGNAME", "label": "WRONGLABEL",
            "filename": "SRTWC286-SH.jpg", "attachmentType": "Product Photos"}
    acts = [{"kind": "send_message", "text": "Here."}, {"kind": "send_attachments", "attachments_src": [item]}]
    fakes.results = [_res("Here.", actions=acts)]
    rows = _run(_conv([("photos", ANS)]))
    dumped = str(rows[0]["files"])
    assert "SRTWC286-SH.jpg" in dumped and "Product Photos" in dumped
    assert "WRONGNAME" not in dumped and "WRONGLABEL" not in dumped
    assert rows[0]["files"]["count"] == 1


# 10 --------------------------------------------------------------------------
PIN = {"id": "V2ID", "number": 12}


def test_norm_version_equal_to_pin():
    assert runner._norm_version(["V2ID", None], PIN) == "V2ID"
    assert runner._norm_version([12, "V2ID"], PIN) == "V2ID"
    assert runner._norm_version(["12"], PIN) == "V2ID"


def test_norm_version_disagreeing_candidate_is_a_mismatch():
    assert runner._norm_version(["OTHER", "V2ID"], PIN) != "V2ID"
    assert runner._norm_version(["V2ID", "OTHER"], PIN) != "V2ID"
    assert runner._norm_version(["OTHER"], PIN) != "V2ID"
