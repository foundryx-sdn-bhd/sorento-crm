"""CA-3 owner live run at 58e50c3f4, RED (E3 = Q4/Q8, E4 = Q9): route + presenter.

E3  a salesperson ranking with the default askers prints `Unclassified askers: N` at most once and
    never `Unclassified askers left out` (nothing is left out by default, so the route's
    `unclassified_left_out` is null).
E4  the period has asks but the filters match none: "No asks match those filters in that period.";
    "No asks in that period. Chat data starts 5 Sep 2026." stays for a truly empty period.
"""
from __future__ import annotations

from tests._chat_asker_seed import seed_asker_world
from tests._chat_report_seed import seed_world
from tests._sales_report_do_seed import client, db, seed_customer  # noqa: F401  (client / db are fixtures)
from tests.test_report_ask_chat_askers_route import _granted
from tests.test_report_ask_chat_route import NO_ASKS, _ok

NO_MATCH = "No asks match those filters in that period."


def test_e3_a_salesperson_ranking_leaves_nothing_out_by_default(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="salesperson", top_n=10)
    assert body.get("unclassified_left_out") is None, body


def test_e3_the_salesperson_ranking_reply_says_unclassified_once_and_never_left_out(client, db):
    from sorento_crm_mcp.presenters import _report_ask

    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="salesperson", top_n=10)
    lines = _report_ask(body).split("\n")
    assert sum(1 for ln in lines if ln.startswith("Unclassified askers")) <= 1, lines
    assert not any("left out" in ln for ln in lines), lines


def test_e4_filters_matching_nothing_in_a_period_with_asks_say_so(client, db):
    w = seed_world(db)
    other = seed_customer(db, name="ZZT NO ASKS SDN BHD")
    db.commit()
    body = _ok(client, _granted(db), group_by="topic", top_n=5, customer_ids=[other.id])
    assert body["rows"] == [] and body["message"] == NO_MATCH, body


def test_e4_a_truly_empty_period_keeps_the_chat_data_starts_line(client, db):
    seed_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=5, date_from="2026-11-01", date_to="2026-11-30")
    assert body["rows"] == [] and body["message"] == NO_ASKS, body


# --------------------------------------------------------------------------- existence probe (security blocker at b84f46771)
# "No asks match those filters" is decided INSIDE the caller's own scope: a dealer (or a salesperson
# narrowed to its book) whose own customers had no asks must not learn that other customers asked.


def _lonely_customer(db):
    from tests._pg_fixture import unique_code

    return seed_customer(db, name=unique_code("ZZT Lonely"))


def test_a_dealer_with_no_asks_of_its_own_never_learns_that_others_asked(client, db):
    from tests.test_report_ask_chat_route import _dealer

    w = seed_world(db)
    lonely = _lonely_customer(db)
    db.commit()
    body = _ok(client, _dealer(db, w, lonely), group_by="topic", top_n=5)
    assert body["rows"] == [] and body["message"] == NO_ASKS, body
    body = _ok(client, _dealer(db, w, lonely), group_by="topic", top_n=5, subjects=["order"])
    assert body["message"] == NO_ASKS, "a filter does not turn it into 'others asked' either"


def test_a_salesperson_narrowed_to_a_book_with_no_asks_never_learns_that_others_asked(client, db):
    from tests._chat_asker_seed import link_agent

    seed_world(db)
    contact = _granted(db)
    agent = link_agent(db, contact, "ZZT Agent Quiet")
    lonely = _lonely_customer(db)
    lonely.sales_agent_id = agent.id
    db.commit()
    body = _ok(client, contact, group_by="topic", top_n=5)
    assert body["rows"] == [] and body["message"] == NO_ASKS, body


def test_a_dealer_with_asks_whose_filter_matches_none_gets_no_match(client, db):
    from tests.test_report_ask_chat_route import _dealer

    w = seed_world(db)
    body = _ok(client, _dealer(db, w, w.cust_a), group_by="topic", top_n=5, subjects=["order"])
    assert body["rows"] == [] and body["message"] == NO_MATCH, body


def test_staff_with_a_filter_matching_none_still_gets_no_match(client, db):
    seed_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=5, subjects=["promotion"])
    assert body["rows"] == [] and body["message"] == NO_MATCH, body
