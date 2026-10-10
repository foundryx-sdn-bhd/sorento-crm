"""CA-3 RED tests: `services/reports/ask.py` answers the chat dataset (UAC-6 to UAC-14, UAC-20).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md, "Contract".
The world is `tests/_chat_report_seed.py` (read its table first). Postgres only, all rows seeded.

The plan fixes the CATALOGUE shape (`CATALOGUE[dataset][basis]`) but not `run_ask`'s signature for
chat. Pinned here, the coder follows it or asks the captain: the existing keyword set plus
`dataset="chat"`, `basis` asks|turns, `measure` asks|failed|failure_rate|contacts, `group_by` one of
the ten words, `filters` keyed by the spec words. Everything else is asserted through the route in
`tests/test_report_ask_chat_route.py`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests._chat_report_seed import NOT_LABELLED, seed_world
from tests._pg_fixture import blank_session

D = Decimal


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


def _run(db, *, measure="asks", group_by=None, basis=None, top_n=None, sort="desc",
         date_from=date(2026, 9, 1), date_to=date(2026, 9, 30), filters=None):
    from app.services.reports import ask

    basis = basis or ("turns" if measure in ("failed", "failure_rate") else "asks")
    return ask.run_ask(
        db, dataset="chat", basis=basis, measure=measure, group_by=group_by, date_from=date_from,
        date_to=date_to, filters=filters or {}, warehouse_codes=[], policy=None, company_grants=None,
        sort=sort, top_n=top_n,
    )


def _by_name(body):
    return {r["name"]: D(str(r["value"])) for r in body["rows"]}


# ------------------------------------------------------------------------ catalogue


def test_the_catalogue_has_a_dataset_axis_and_sales_keeps_its_three_bases():
    from app.services.reports.ask import CATALOGUE

    assert set(CATALOGUE["sales"]) == {"delivered", "ordered", "outstanding"}
    assert set(CATALOGUE["chat"]) == {"asks", "turns"}


# ------------------------------------------------------------------------ UAC-6 / 7 asks


def test_rank_by_topic_counts_every_ask_and_never_noise_or_office(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, group_by="topic", top_n=10)
    assert _by_name(body) == {
        "Availability": D(5), "Order status": D(1), "Other stock": D(1), NOT_LABELLED: D(1),
    }, body["rows"]
    assert body["rows"][0]["name"] == "Availability" and body["rows"][0]["rank"] == 1, body["rows"]
    assert D(str(body["total"]["value"])) == 8, body["total"]
    assert body["more"] == 0 and body["total_count"] == 4, body


def test_top_n_cuts_the_rows_but_not_the_total(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, group_by="topic", top_n=2)
    assert len(body["rows"]) == 2 and body["rows"][0]["name"] == "Availability", body
    assert body["more"] == 2 and body["total_count"] == 4, body
    assert D(str(body["total"]["value"])) == 8, "the total is the whole set, not the printed rows"


def test_unlabelled_asks_rank_as_not_labelled_yet(db):
    seed_world(db)
    assert _by_name(_run(db, group_by="topic", top_n=10))[NOT_LABELLED] == 1


def test_rank_by_subject_uses_the_taxonomy_words(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    assert _by_name(_run(db, group_by="subject", top_n=10)) == {"Stock": D(7), "Orders": D(1)}


def test_rank_by_customer_names(db):
    w = seed_world(db)
    got = _by_name(_run(db, group_by="customer", top_n=10))
    assert got[w.cust_a.customer_name] == 2 and got[w.cust_b.customer_name] == 3, got


def test_rank_by_product_counts_asks_that_named_the_product_only(db):
    """chat_ask_items role `named`: P1 named on F1 F2, P2 on F3; the shown row, the noise ask and
    the office ask never count. owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    w = seed_world(db)
    got = _by_name(_run(db, group_by="product", top_n=10))
    assert got == {w.p1.product_code: D(3), w.p2.product_code: D(1)}, got


def test_rank_by_week_lists_every_bucket_on_the_malaysia_monday(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, group_by="week")
    assert _by_name(body) == {"2026-09-07": D(5), "2026-09-14": D(2), "2026-09-21": D(1)}, body["rows"]
    assert body["more"] == 0, "a trend lists every bucket"


def test_rank_by_language(db):
    seed_world(db)
    got = _by_name(_run(db, group_by="language", top_n=10))
    assert got["en"] == 3 and got["ms"] == 2 and got["zh"] == 2, got


def test_no_group_by_is_a_total_with_no_rows(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db)
    assert body["rows"] == [] and D(str(body["total"]["value"])) == 8, body


# ------------------------------------------------------------------------ UAC-6 turns, failed


def test_the_turns_basis_counts_ask_one_of_a_message_only(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, basis="turns", measure="asks")
    assert D(str(body["total"]["value"])) == 6, body["total"]


def test_failed_counts_failed_turns(db):
    seed_world(db)
    body = _run(db, measure="failed", group_by="topic", top_n=10)
    got = _by_name(body)
    assert got["Availability"] == 1 and got[NOT_LABELLED] == 1, got
    assert D(str(body["total"]["value"])) == 2, body["total"]


# ------------------------------------------------------------------------ UAC-8 contacts


def test_contacts_are_distinct_and_the_total_is_not_the_sum_of_rows(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, measure="contacts", group_by="topic", top_n=10)
    got = _by_name(body)
    assert got["Availability"] == 3 and got["Order status"] == 1 and got[NOT_LABELLED] == 1, got
    assert got["Other stock"] == 1, got
    assert D(str(body["total"]["value"])) == 4, "C2 and C3 sit in two rows each, C5 asks once: 4 distinct, not 6"


# ------------------------------------------------------------------------ UAC-9 failure rate


def test_failure_rate_per_week_is_failed_turns_over_non_noise_turns(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    body = _run(db, measure="failure_rate", group_by="week")
    rows = {r["name"]: r for r in body["rows"]}
    assert set(rows) == {"2026-09-07", "2026-09-14"}, "week 21 Sep has 0 turns (only ask 2): not printed"
    wk7, wk14 = rows["2026-09-07"], rows["2026-09-14"]
    assert (wk7["numerator"], wk7["denominator"]) == (1, 4), wk7
    assert (wk14["numerator"], wk14["denominator"]) == (1, 2), wk14
    assert float(wk7["value"]) == pytest.approx(25.0, abs=0.05), wk7
    assert float(wk14["value"]) == pytest.approx(50.0, abs=0.05), wk14
    total = body["total"]
    assert (total["numerator"], total["denominator"]) == (2, 6), total
    assert float(total["value"]) == pytest.approx(33.3, abs=0.05), total


def test_failure_rate_by_topic_leaves_out_a_row_with_no_turns(db):
    seed_world(db)
    body = _run(db, measure="failure_rate", group_by="topic", top_n=10)
    names = {r["name"] for r in body["rows"]}
    assert "Other stock" not in names, "its only ask is ask 2: no turns"
    assert names == {"Availability", "Order status", NOT_LABELLED}, names


# ------------------------------------------------------------------------ UAC-12 / 13 / 20


def expected_coverage_line(db, percent, version, date_from=date(2026, 9, 5), date_to=date(2026, 9, 30)):
    """The CA-3 coverage line followed by the CA-4 era lines the same run's ctx yields
    (lead sign-off 11 Oct: CA-4 era lines in the coverage line); CA-4's wording is not copied here."""
    from app.services.reports import engine
    from app.services.reports.datasets import chat_asks
    from app.services.reports.ask import CATALOGUE

    params = {"date_basis": "asked_at",
              "period": {"kind": "custom", "from": date_from.isoformat(), "to": date_to.isoformat()}}
    ctx = engine.resolve(db, CATALOGUE["chat"]["asks"]["definition"], params)
    base = f"Topics labelled: {percent}% of asks (taxonomy v{version}). From bot traces, 5 Sep 2026 on."
    return base, chat_asks.history_lines(ctx)


def test_coverage_names_percent_version_and_the_traces_note(db):
    """owner override 10 Oct: every class shown by default (F7, the staff ask, now counts).
    lead sign-off 11 Oct: CA-4 era lines in the coverage line."""
    seed_world(db)
    cov = _run(db, group_by="topic", top_n=10)["coverage"]
    assert cov["percent"] == 88 and cov["taxonomy_version"] == 2, cov
    base, eras = expected_coverage_line(db, 88, 2)
    assert cov["line"].startswith(base), cov
    rest = cov["line"][len(base):]
    assert all(line in rest for line in eras), (eras, cov)


def test_a_period_before_the_traces_is_clipped_in_the_counts(db):
    """F8 sits on 2 Sep: a period opening 1 Sep counts it never. owner override 10 Oct: every class shown by default (F7, the staff ask, now counts)."""
    seed_world(db)
    opened_early = _run(db, group_by="topic", top_n=10, date_from=date(2026, 8, 1))
    assert D(str(opened_early["total"]["value"])) == 8


def test_a_period_with_no_asks_is_an_empty_set(db):
    seed_world(db)
    body = _run(db, group_by="topic", top_n=10, date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))
    assert body["rows"] == [] and D(str(body["total"]["value"])) == 0, body


# ------------------------------------------------------------------------ UAC-10 screen parity


def test_the_bots_numbers_equal_the_top_asks_screen_for_the_same_period(db):
    """Same period, same choices: the screen's topic row totals are the bot's rows. owner override
    10 Oct: the bot counts every class by default; compared to the screen with every contact type selected."""
    from tests.test_report_chat_top_asks import _row_asks, _run as screen_run

    seed_world(db)
    screen = screen_run(
        db, {"period": {"kind": "custom", "from": "2026-09-05", "to": "2026-09-30"},
         "contact_type": ["dealer", "office", "not_allowed", "unknown"]}
    ).layouts.summary
    bot = _by_name(_run(db, group_by="topic", top_n=10))
    assert {k: D(str(v)) for k, v in _row_asks(screen).items()} == bot
