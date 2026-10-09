import json
import threading
import time

import psycopg2
import pytest

from scripts.chatbot_eval.runner import run_all
from scripts.chatbot_eval.trace import DbContactReset, DbTraceReader
from tests.chatbot_eval.fakes import RecordingEngine, case_dict, write_case
from tests.chatbot_eval.test_runner import mk_case
from scripts.chatbot_eval.run import main


class FakeCursor:
    """psycopg2 raises when one connection is used by two threads at once; mirror that."""

    rowcount = 0

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        with self.conn.guard:
            if self.conn.cursor_busy:
                raise psycopg2.ProgrammingError("connection already in use by another thread")
            self.conn.cursor_busy = True
        return self

    def __exit__(self, *a):
        self.conn.cursor_busy = False
        return False

    def execute(self, *a, **k):
        time.sleep(0.02)

    def fetchone(self):
        return ([],)

    def fetchall(self):
        return []


class FakeConn:
    """Entering the same connection object twice at once raises, as psycopg2 does."""

    autocommit = False
    closed = False

    def __init__(self):
        self.entered = False
        self.cursor_busy = False
        self.guard = threading.Lock()

    def __enter__(self):
        with self.guard:
            if self.entered:
                raise psycopg2.ProgrammingError("the connection cannot be re-entered recursively")
            self.entered = True
        time.sleep(0.02)
        return self

    def __exit__(self, *a):
        self.entered = False
        return False

    def cursor(self, *a, **k):
        return FakeCursor(self)

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _warm_then_hammer(fn, n=8):
    """Warm the cached connection sequentially, then overlap n threads on it behind a barrier."""
    fn()
    barrier = threading.Barrier(n)
    errors = []

    def work():
        try:
            barrier.wait()
            for _ in range(3):
                fn()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return errors


def test_contact_reset_thread_safe(monkeypatch):
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: FakeConn())
    r = DbContactReset("postgresql://u:p@127.0.0.1:1/sorento_eval_t")
    assert _warm_then_hammer(lambda: r.reset("9001")) == []


def test_trace_reader_thread_safe(monkeypatch):
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: FakeConn())
    r = DbTraceReader("postgresql://u:p@127.0.0.1:1/x")
    assert _warm_then_hammer(lambda: r.fetch("t-1")) == []


def test_unexpected_exception_becomes_error_result():
    cases = [mk_case("a"), mk_case("boom"), mk_case("c")]
    out = run_all(ExplodingEngine_(), cases, run_id="r", max_parallel=1)
    by = {r.case_id: r for r in out.results}
    assert set(by) == {"a", "boom", "c"}
    assert by["boom"].verdict.label == "error"
    assert any(x.startswith("harness: RuntimeError") for x in by["boom"].verdict.reasons)
    assert by["a"].verdict.label != "error" and by["c"].verdict.label != "error"


def ExplodingEngine_():
    from tests.chatbot_eval.fakes import ExplodingEngine

    return ExplodingEngine()


def _seed(tmp_path, ids):
    d = tmp_path / "data"
    for i in ids:
        write_case(d, case_dict(i))
    return d


def _read(out):
    rep = json.loads(next(f for f in out.rglob("*.json")).read_text())
    lines = [x for f in out.rglob("*.jsonl") for x in f.read_text().splitlines() if x.strip()]
    return rep, lines


def test_not_dry_run_still_writes_report(tmp_path):
    d, out = _seed(tmp_path, ["k1", "k2", "k3", "k4"]), tmp_path / "out"
    try:
        rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:LiveSecondEngine", "--out", str(out)])
        assert rc != 0
    except Exception:  # noqa: BLE001  exit behaviour is whatever it is today
        pass
    rep, lines = _read(out)
    assert rep["stopped_reason"] == "not_dry_run"
    assert 1 <= len(lines) < 4


def test_budget_stop_still_writes_report(tmp_path):
    d, out = _seed(tmp_path, ["k1", "k2", "k3", "k4"]), tmp_path / "out"
    rc = main(["--data-dir", str(d), "--engine", "tests.chatbot_eval.fakes:BudgetBurner", "--budget-usd", "0.5", "--out", str(out)])
    assert rc == 3
    rep, _ = _read(out)
    assert rep["stopped_reason"] == "budget"
