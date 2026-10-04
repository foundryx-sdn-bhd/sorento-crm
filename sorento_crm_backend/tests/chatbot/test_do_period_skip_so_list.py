"""DO-PERIOD-SKIP AC-10 to AC-12 and AC-14: the SO list ask for a contact with "Skip date-range
questions" on. Real `engine.run_turn` through the `test_so_list.py` harness; every SO is seeded here."""
from __future__ import annotations

import pytest

from tests.chatbot._dps_helpers import SkipSwitchMonkeypatch
from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_so_list import _list_ask, _turn
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, SO_NOT_ENABLED, _seed_so

H1 = "ZETAWARE TILES SDN BHD [A/C I]"
GROUP = "ZETAWARE TILES SDN BHD"


def _seed_25(session_factory) -> str:
    """25 SOs, one per day 1..25 Sep 2026, SO600001..SO600025 (number = 600000 + day)."""
    _seed_contact(session_factory, variables={})
    (cid,) = _link_customers(session_factory, H1)
    for day in range(1, 26):
        _seed_so(session_factory, f"SO{600000 + day}", customer_id=cid, status="open", lines=[(1, 0)],
                 debtor_name=None, order_date=f"2026-09-{day:02d}")
    return cid


def _numbers(reply: str) -> list[str]:
    return [ln.split(" - ")[0] for ln in reply.strip().split("\n")[1:]]


def _newest_20() -> list[str]:
    return [f"SO{600000 + d}" for d in range(25, 5, -1)]


def test_ac11_on_and_no_dates_lists_the_newest_20_newest_first_with_no_window(session_factory, monkeypatch):
    _seed_25(session_factory)
    reply, captured = _turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _list_ask())
    first = reply.strip().split("\n")[0]
    assert first.startswith(f"Sales orders for {GROUP}") and first.endswith(":"), reply
    assert "latest" in first and "2026" not in first and " to " not in first, reply
    assert "Which period" not in reply, reply
    assert _numbers(reply) == _newest_20(), reply


def test_ac11_on_and_no_dates_with_no_rows_says_there_are_none(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, H1)
    reply, _ = _turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _list_ask())
    assert reply.strip() == f"No sales orders for {GROUP}.", reply


def test_ac11_on_and_fewer_than_20_lists_them_all(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    (cid,) = _link_customers(session_factory, H1)
    for day in (3, 9):
        _seed_so(session_factory, f"SO6100{day:02d}", customer_id=cid, lines=[(1, 0)], debtor_name=None,
                 order_date=f"2026-08-{day:02d}")
    reply, _ = _turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _list_ask())
    assert _numbers(reply) == ["SO610009", "SO610003"], reply


def test_ac12_on_and_a_nine_month_range_lists_that_range_newest_20_no_cap_reply(session_factory, monkeypatch):
    cid = _seed_25(session_factory)
    _seed_so(session_factory, "SO600100", customer_id=cid, lines=[(1, 0)], debtor_name=None, order_date="2026-01-15")
    _seed_so(session_factory, "SO600101", customer_id=cid, lines=[(1, 0)], debtor_name=None, order_date="2026-10-01")
    _seed_so(session_factory, "SO600102", customer_id=cid, lines=[(1, 0)], debtor_name=None, order_date="2025-12-20")
    reply, _ = _turn(
        session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _list_ask("2026-01-01", "2026-09-30"),
    )
    assert "I can show up to" not in reply and "Which period" not in reply, reply
    assert _numbers(reply) == _newest_20(), reply
    assert "SO600101" not in reply and "SO600102" not in reply, reply


def test_ac10_off_and_no_dates_still_asks_which_period(session_factory, monkeypatch):
    _seed_25(session_factory)
    reply, captured = _turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, False), _list_ask())
    assert reply.startswith(f"Which period for {GROUP}?\n- This month ("), reply
    assert "SO6" not in reply and captured == [], (reply, captured)


def test_ac10_off_and_a_nine_month_range_still_gets_the_cap_reply(session_factory, monkeypatch):
    _seed_25(session_factory)
    reply, _ = _turn(
        session_factory, SkipSwitchMonkeypatch(monkeypatch, False), _list_ask("2026-01-01", "2026-09-30"),
    )
    assert "I can show up to 31 days of sales orders at a time" in reply, reply
    assert "SO6" not in reply, reply


def test_ac14_the_so_grant_refusal_still_comes_first_with_the_switch_on(session_factory, monkeypatch):
    _seed_25(session_factory)
    reply, captured = _turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _list_ask(), attributes=())
    assert reply.strip() == SO_NOT_ENABLED, reply
    assert captured == [], captured
