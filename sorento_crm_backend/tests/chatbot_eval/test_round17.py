import re
import threading

import psycopg2
import pytest

from scripts.chatbot_eval.report import GRADER_VERSION
from scripts.chatbot_eval.runner import run_case
from scripts.chatbot_eval.trace import DbContactReset
from tests.chatbot_eval.fakes import RecordingEngine
from tests.chatbot_eval.test_runner import mk_case

HEADER = "Customer: all\nProduct: TESTCODE-1, TESTCODE-2\nDates: all\n\n"
USER = "TESTCODE-1 & TESTCODE-2 outstanding list"
BOTH = [{"code": "TESTCODE-1"}, {"code": "TESTCODE-2"}]


def verdict(reply, *, must=BOTH, text=USER, history=None):
    case = mk_case(text=text, must=must, history=history or [])
    eng = RecordingEngine(replies={text: reply})
    return run_case(eng, case, text, "r").verdict


def test_codes_only_in_header_is_wrong():
    assert verdict(HEADER + "I could not fetch orders just now").label == "wrong"


def test_codes_in_body_is_correct():
    assert verdict(HEADER + "TESTCODE-1: 66 outstanding\nTESTCODE-2: 6979 outstanding").label == "correct"


def test_code_not_in_user_text_counts_from_header():
    v = verdict("Product: TESTCODE-3\nCustomer: all\n\nnothing else", must=[{"code": "TESTCODE-3"}], text="what is outstanding")
    assert v.label == "correct"


def test_history_user_turn_makes_it_an_echo():
    v = verdict("Product: TESTCODE-2\nCustomer: all\n\nnothing", must=[{"code": "TESTCODE-2"}], text="list outstanding",
                history=[{"role": "user", "text": "about TESTCODE-2"}])
    assert v.label == "wrong"


def test_reply_without_header_block_unchanged():
    assert verdict("TESTCODE-1 and TESTCODE-2 are fine").label == "correct"


def test_number_musts_unaffected_by_header():
    v = verdict("Qty: 66\nCustomer: all\n\nnothing", must=[{"number": 66}], text="TESTCODE-1 66 list")
    assert v.label == "correct"


def test_grader_version_9():
    assert GRADER_VERSION >= 9


# ---- (b) report lock reset ----
class RecCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.events.append(("exec", " ".join(sql.split()).upper(), params))

    def __getattr__(self, name):
        return lambda *a, **k: None


class RecConn:
    closed = False

    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append(("enter",))
        return self

    def __exit__(self, *a):
        self.events.append(("exit",))
        return False

    def cursor(self, *a, **k):
        return RecCursor(self)

    def commit(self):
        self.events.append(("commit",))

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_reset_fails_in_flight_chat_reorder_runs_in_same_transaction(monkeypatch):
    conn = RecConn()
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: conn)
    DbContactReset("postgresql://u:p@127.0.0.1:1/sorento_eval_t").reset("9001")
    execs = [(i, e) for i, e in enumerate(conn.events) if e[0] == "exec"]
    ups = [(i, e) for i, e in execs if e[1].startswith("UPDATE SCM.REORDER_RUN")]
    assert len(ups) == 1
    i, (_, sql, params) = ups[0]
    assert "STATUS = 'FAILED'" in sql and "REQUESTED_VIA = 'CHAT'" in sql
    assert "'QUEUED'" in sql and "'RUNNING'" in sql
    assert "9001" not in sql and not params  # static SQL, nothing interpolated
    deletes = [j for j, e in execs if e[1].startswith("DELETE")]
    assert deletes
    # one transaction: no commit/exit between the deletes and the update
    lo, hi = min(deletes + [i]), max(deletes + [i])
    assert not any(e[0] in ("commit", "exit") for e in conn.events[lo:hi])


def test_reset_guard_still_refuses_non_eval_database():
    with pytest.raises(ValueError):
        DbContactReset("postgresql://u:p@127.0.0.1:1/sorento_crm")


# ---- 17b: narrower header-echo rule ----
def test_digit_label_single_line_is_not_a_header():
    v = verdict("TESTCODE-9: No ETA", must=[{"code": "TESTCODE-9"}], text="eta for TESTCODE-9")
    assert v.label == "correct"


def test_code_echo_plus_non_echo_number_is_correct():
    v = verdict("Product: TESTCODE-1\nCustomer: all\n\n*Sales order outstanding*\nOutstanding: 1,473",
                must=[{"code": "TESTCODE-1"}, {"number": 1473}], text="TESTCODE-1 outstanding")
    assert v.label == "correct"


def test_both_codes_header_only_still_wrong():
    assert verdict("Customer: all\nProduct: TESTCODE-1, TESTCODE-2\nDates: all\n\nI could not fetch orders").label == "wrong"


def test_only_evidence_is_header_echo_is_wrong():
    v = verdict("Product: TESTCODE-1\nCustomer: all\n\nNo outstanding delivery order.",
                must=[{"code": "TESTCODE-1"}], text="TESTCODE-1 outstanding")
    assert v.label == "wrong"


def test_single_line_header_block_is_not_a_header():
    v = verdict("Product: TESTCODE-1\n\nNo outstanding delivery order.", must=[{"code": "TESTCODE-1"}], text="TESTCODE-1 outstanding")
    assert v.label == "correct"


def test_grader_version_10():
    assert GRADER_VERSION >= 10


# ---- round 19: bold header lines ----
BOLD = ("*Customer:* all\n*Product:* TESTCODE-1, TESTCODE-1-200\n*Delivery date:* 01/01/2026 to 31/12/2026\n\n"
        "*Total:* Qty 4,165")


def test_bold_header_echo_sole_evidence_is_wrong():
    assert verdict(BOLD, must=[{"code": "TESTCODE-1"}], text="Only model TESTCODE-1").label == "wrong"


def test_bold_header_echo_plus_non_echo_number_is_correct():
    v = verdict(BOLD, must=[{"code": "TESTCODE-1"}, {"number": 4165}], text="Only model TESTCODE-1")
    assert v.label == "correct"


def test_bold_line_with_digit_label_is_not_a_header():
    v = verdict("*TESTCODE-1:* 5\n*Customer:* all\n\nsomething", must=[{"code": "TESTCODE-1"}], text="Only model TESTCODE-1")
    assert v.label == "correct"


def test_grader_version_11():
    assert GRADER_VERSION == 11
