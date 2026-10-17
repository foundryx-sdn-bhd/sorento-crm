"""EVAL-BENCH: the bench fails loudly. Synthetic data only.

1. diff gate: a row broken in either arm (engine error, empty stages, duplicate) exits 3 even with 0 unintended diffs.
2. live runner: stops once errored items exceed 2% of the planned items; the CLI exits 3.
3. preflight: `run` and `live` refuse (exit 2) when the replay DB's alembic revision is not the cwd arm's head.
"""
from __future__ import annotations

import json

import pytest
import uuid
from types import SimpleNamespace

from app.services.chatbot import engine as engine_mod
from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import ChatbotTurn, recorded, _record, _turn_count  # noqa: F401
from tests.chatbot_eval.test_replay_live import LAST4, items_for, live_env  # noqa: F401


# ------------------------------------------------------------------ 1. the diff gate
def _row(turn, *, misses=(), stages=None, skipped=None):
    return {"case_id": "c1", "turn_id": turn, "skipped": skipped, "misses": list(misses),
            "stages": {"grounded": {"codes": ["SRTA100"]}} if stages is None else stages}


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def _diff_cli(tmp_path, base, head, capsys):
    rc = replay_mod.main(["diff", _write(tmp_path / "b.jsonl", base), _write(tmp_path / "h.jsonl", head)])
    return rc, capsys.readouterr().out


def test_red_identical_errored_rows_in_both_arms_are_broken(tmp_path, capsys):
    bad = _row("t1", misses=[{"error": "ProgrammingError"}], stages={})
    res = replay_mod.diff_rows([bad], [dict(bad)])
    assert res["unintended"] == [] and [b[:2] for b in res.get("broken", [])] == [("c1", "t1")], res
    rc, out = _diff_cli(tmp_path, [bad], [dict(bad)], capsys)
    assert rc == 3, out
    assert "broken 1" in out and "t1" in out


def test_red_a_row_with_empty_stages_that_is_not_skipped_is_broken(tmp_path, capsys):
    bad = _row("t2", stages={})
    assert [b[:2] for b in replay_mod.diff_rows([bad], [dict(bad)]).get("broken", [])] == [("c1", "t2")]
    assert _diff_cli(tmp_path, [bad], [dict(bad)], capsys)[0] == 3


def test_red_a_duplicate_row_is_broken(tmp_path, capsys):
    bad = _row("t3", skipped="duplicate", stages={})
    assert [b[:2] for b in replay_mod.diff_rows([bad], [dict(bad)]).get("broken", [])] == [("c1", "t3")]
    assert _diff_cli(tmp_path, [bad], [dict(bad)], capsys)[0] == 3


def test_red_an_error_in_only_one_arm_is_broken(tmp_path, capsys):
    good, bad = _row("t4"), _row("t4", misses=[{"error": "KeyError"}], stages={})
    assert replay_mod.diff_rows([good], [bad]).get("broken", [])
    assert _diff_cli(tmp_path, [good], [bad], capsys)[0] == 3


def test_guard_no_parse_skips_and_clean_rows_exit_0(tmp_path, capsys):
    skipped = _row("t5", skipped="no_parse", stages={})
    clean = _row("t6")
    res = replay_mod.diff_rows([skipped, clean], [dict(skipped), dict(clean)])
    assert res.get("broken", []) == [], res
    assert _diff_cli(tmp_path, [skipped, clean], [dict(skipped), dict(clean)], capsys)[0] == 0


def test_guard_an_unintended_diff_alone_still_exits_1(tmp_path, capsys):
    a, b = _row("t7"), _row("t7", stages={"grounded": {"codes": ["SRTB200"]}})
    assert _diff_cli(tmp_path, [a], [b], capsys)[0] == 1


# ------------------------------------------------------------------ 2. the live error-rate stop
def _ok_turn(item):
    return {"item_id": item["item_id"], "run": item.get("run"),
            "turn": {"trace": [{"kind": "tool"}], "response": {"reply": {"text": "ok"}}}}


def _patch_live_one(monkeypatch, bad_ids):
    def fake(item, row, factory, total, prompt_version_id=None):
        if item["item_id"] in bad_ids:
            raise RuntimeError("synthetic engine failure")
        return _ok_turn(item)

    monkeypatch.setattr(replay_mod, "_live_one", fake)


def _live(session_factory, recorded, n=100):
    return replay_mod.live_rows(items_for(recorded, n=n), [recorded], session_factory=session_factory,
                                max_calls=1000, max_usd=100.0, require_key_last4=LAST4)


def test_red_live_stops_once_errors_pass_two_percent_of_the_planned_items(session_factory, recorded, live_env, monkeypatch):
    _patch_live_one(monkeypatch, {"it-1", "it-2", "it-3"})
    res = _live(session_factory, recorded)
    assert len(res) == 5, [r.get("item_id") or r for r in res][-3:]
    assert res[-1] == {"stopped": "error_rate", "errors": 3, "first_ids": ["it-1", "it-2", "it-3"]}, res[-1]


def test_red_a_live_turn_with_no_trace_and_no_response_counts_as_an_error(session_factory, recorded, live_env, monkeypatch):
    def fake(item, row, factory, total, prompt_version_id=None):
        if item["item_id"] in {"it-1", "it-2", "it-3"}:
            return {"item_id": item["item_id"], "run": 2, "turn": {"trace": [], "response": None}}
        return _ok_turn(item)

    monkeypatch.setattr(replay_mod, "_live_one", fake)
    res = _live(session_factory, recorded)
    assert res[-1].get("stopped") == "error_rate" and res[-1].get("errors") == 3, res[-1]


def test_guard_two_errors_in_a_hundred_runs_every_item(session_factory, recorded, live_env, monkeypatch):
    _patch_live_one(monkeypatch, {"it-1", "it-2"})
    res = _live(session_factory, recorded)
    assert len(res) == 100 and not any(r.get("stopped") for r in res)


def _cli_live(tmp_path, session_factory, row, monkeypatch, n):
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", session_factory)
    monkeypatch.setattr(replay_mod, "_db_revisions", lambda sf: {"a1"}, raising=False)
    monkeypatch.setattr(replay_mod, "_code_heads", lambda d: {"a1"}, raising=False)
    items = tmp_path / "items.jsonl"
    items.write_text("".join(json.dumps(i) + "\n" for i in items_for(row, n=n)))
    rec = tmp_path / "recorded.jsonl"
    rec.write_text(json.dumps(row) + "\n")
    out = tmp_path / "out.jsonl"
    rc = replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(out),
                          "--max-calls", "1000", "--max-usd", "100", "--require-key-last4", LAST4])
    return rc, out


def test_red_cli_live_exits_3_and_prints_the_count_and_first_ids_on_an_error_rate_stop(
        tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _patch_live_one(monkeypatch, {f"it-{i}" for i in range(1, 8)})
    rc, _ = _cli_live(tmp_path, session_factory, recorded, monkeypatch, 100)
    out = capsys.readouterr().out
    assert rc == 3, out
    assert "3" in out and all(f"it-{i}" in out for i in (1, 2, 3)), out


# ------------------------------------------------------------------ 3. the alembic preflight
def _preflight(monkeypatch, db, code, known=True):
    monkeypatch.setattr(replay_mod, "_db_revisions", lambda sf: set(db), raising=False)
    monkeypatch.setattr(replay_mod, "_code_heads", lambda d: set(code), raising=False)
    monkeypatch.setattr(replay_mod, "_revision_known", lambda d, rev: known, raising=False)


def _cli(cmd, tmp_path, session_factory, row, monkeypatch):
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", session_factory)
    rec = tmp_path / "rec.jsonl"
    rec.write_text(json.dumps(row) + "\n")
    out = tmp_path / "out.jsonl"
    if cmd == "run":
        argv = ["run", "--traces", str(rec), "--out", str(out)]
    else:
        items = tmp_path / "items.jsonl"
        items.write_text("".join(json.dumps(i) + "\n" for i in items_for(row)))
        argv = ["live", "--items", str(items), "--recorded", str(rec), "--out", str(out),
                "--max-calls", "10", "--max-usd", "5", "--require-key-last4", LAST4]
    return replay_mod.main(argv), out


def _no_row_written(out):
    return (not out.exists()) or out.read_text() == ""


def test_red_run_refuses_when_the_replay_db_is_not_at_the_code_head(tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, {"a1"}, {"b2"})
    rc, out = _cli("run", tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    shown = text.out + text.err
    assert rc == 2, shown
    assert "alembic upgrade head" in shown and "a1" in shown and "b2" in shown, shown
    assert _no_row_written(out)


def test_red_live_refuses_when_the_replay_db_is_not_at_the_code_head(tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, {"a1"}, {"b2"})
    rc, out = _cli("live", tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    shown = text.out + text.err
    assert rc == 2, shown
    assert "alembic upgrade head" in shown and "a1" in shown and "b2" in shown, shown
    assert _no_row_written(out)


def test_guard_equal_revisions_pass_the_preflight(tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, {"a1"}, {"a1"})
    rc, _ = _cli("run", tmp_path, session_factory, recorded, monkeypatch)
    assert rc == 0, capsys.readouterr()


# ------------------------------------------------------------------ round 2: failed-status turns
def _failed_result():
    return SimpleNamespace(duplicate=False, turn_id=uuid.uuid4(), branch_kind="business_query",
                           reply={"text": "Sorry, something went wrong."}, status="failed", stage="answer",
                           error="ProgrammingError")


def test_red_a_turn_the_engine_closes_as_failed_is_recorded_with_an_error_miss(session_factory, recorded, monkeypatch):
    monkeypatch.setattr(engine_mod, "run_turn", lambda envelope, **kw: _failed_result())
    row = replay_mod.replay_rows([recorded], session_factory=session_factory)[0]
    assert any("error" in m for m in row["misses"]), row
    res = replay_mod.diff_rows([row], [dict(row)])
    assert [b[:2] for b in res.get("broken", [])] == [(row["case_id"], row["turn_id"])], res


def test_red_a_live_result_whose_turn_status_is_failed_counts_as_an_error(session_factory, recorded, live_env, monkeypatch):
    def fake(item, row, factory, total, prompt_version_id=None):
        turn = {"trace": [{"kind": "tool"}], "response": {"reply": {"text": "x"}}, "branch_kind": "b"}
        if item["item_id"] in {"it-1", "it-2", "it-3"}:
            turn["status"] = "failed"
        return {"item_id": item["item_id"], "run": 2, "turn": turn}

    monkeypatch.setattr(replay_mod, "_live_one", fake)
    res = replay_mod.live_rows(items_for(recorded, n=100), [recorded], session_factory=session_factory,
                               max_calls=1000, max_usd=100.0, require_key_last4=LAST4)
    assert res[-1].get("stopped") == "error_rate" and res[-1].get("errors") == 3, res[-1]


# ------------------------------------------------------------------ round 2: nothing compared
def test_red_a_diff_that_compares_nothing_exits_3_and_says_compared_0(tmp_path, capsys):
    rc, out = _diff_cli(tmp_path, [], [], capsys)
    assert rc == 3 and "compared 0" in out, (rc, out)


def test_red_a_diff_with_no_key_in_common_compares_nothing(tmp_path, capsys):
    rc, out = _diff_cli(tmp_path, [_row("t1")], [_row("t2")], capsys)
    assert rc == 3 and "compared 0" in out, (rc, out)


def test_guard_one_compared_clean_row_exits_0(tmp_path, capsys):
    rc, out = _diff_cli(tmp_path, [_row("t1")], [_row("t1")], capsys)
    assert rc == 0, out


# ------------------------------------------------------------------ round 2: preflight across migration lanes
def test_red_a_db_ahead_of_the_arm_passes_with_a_warning_naming_the_revision(
        tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, {"zz_9999_future"}, {"new_0002"}, known=False)
    rc, _ = _cli("run", tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    assert rc == 0, text.out + text.err
    assert "zz_9999_future" in text.err, text.err


def test_red_a_db_behind_the_arm_is_refused_with_the_upgrade_command(
        tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, {"old_0001"}, {"new_0002"}, known=True)
    rc, out = _cli("run", tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    assert rc == 2, text.out + text.err
    assert "alembic upgrade head" in text.out + text.err
    assert _no_row_written(out)


# ------------------------------------------------------------------ round 3
@pytest.mark.parametrize("cmd", ["run", "live"])
def test_red_an_unstamped_replay_db_is_refused(cmd, tmp_path, session_factory, recorded, live_env, monkeypatch, capsys):
    _preflight(monkeypatch, set(), {"a1"}, known=False)
    rc, out = _cli(cmd, tmp_path, session_factory, recorded, monkeypatch)
    text = capsys.readouterr()
    assert rc == 2, text.out + text.err
    assert "alembic_version" in text.out + text.err, text.out + text.err
    assert _no_row_written(out)


def test_red_a_clean_diff_prints_no_broken_line(tmp_path, capsys):
    rc, out = _diff_cli(tmp_path, [_row("t1")], [_row("t1")], capsys)
    assert rc == 0 and "broken" not in out.lower(), out


def test_guard_a_broken_diff_still_prints_broken_and_its_count(tmp_path, capsys):
    bad = _row("t1", misses=[{"error": "KeyError"}], stages={})
    rc, out = _diff_cli(tmp_path, [bad], [dict(bad)], capsys)
    assert rc == 3 and "BROKEN" in out and "broken 1" in out, out
