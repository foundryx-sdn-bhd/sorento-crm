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


# --------------------------------------------------------------------------- one name, two companies (Q12 echo)


def _in_both_companies(db):
    """A granted contact that holds both companies (so both same-named rows are its own)."""
    from app.models.company import RespondContactCompany
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import MOCHA_ID

    contact = _granted(db)
    held = {str(r[0]) for r in db.query(RespondContactCompany.company_id).filter(
        RespondContactCompany.respond_contact_id == str(contact.id)).all()}
    for company in (DEFAULT_COMPANY_ID, MOCHA_ID):
        if str(company) not in held:
            db.add(RespondContactCompany(respond_contact_id=str(contact.id), company_id=company))
    db.commit()
    return contact


def test_a_brand_filter_of_one_name_in_two_companies_echoes_the_name_once(client, db):
    """Live: 'Brand: SORENTO, SORENTO'. Resolved ids that share a name echo that name once."""
    import uuid

    from app.models.product import Brand
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import MOCHA_ID, seed_mocha

    seed_world(db)
    seed_mocha(db)
    tag = uuid.uuid4().hex[:6].upper()
    a = Brand(id=str(uuid.uuid4()), brand_code=f"ZB{tag}", brand_name=f"ZZT TWIN {tag}", company_id=DEFAULT_COMPANY_ID)
    b = Brand(id=str(uuid.uuid4()), brand_code=f"ZB{tag}", brand_name=f"ZZT TWIN {tag}", company_id=MOCHA_ID)
    db.add_all([a, b])
    contact = _in_both_companies(db)
    body = _ok(client, contact, group_by="topic", top_n=5, brand_ids=[a.id, b.id])
    echoed = [f for f in body["filters"] if f["label"] == "Brand"]
    assert len(echoed) == 1 and echoed[0]["values"] == [f"ZZT TWIN {tag}"], body["filters"]


def test_a_category_filter_of_one_name_in_two_companies_echoes_the_name_once(client, db):
    import uuid

    from app.models.product import ProductCategory
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import MOCHA_ID, seed_mocha

    seed_world(db)
    seed_mocha(db)
    tag = uuid.uuid4().hex[:6].upper()
    name = f"ZZT-TWIN-CAT-{tag}"
    a = ProductCategory(id=str(uuid.uuid4()), category_code=name, category_name=name, company_id=DEFAULT_COMPANY_ID)
    b = ProductCategory(id=str(uuid.uuid4()), category_code=name, category_name=name, company_id=MOCHA_ID)
    db.add_all([a, b])
    contact = _in_both_companies(db)
    body = _ok(client, contact, group_by="topic", top_n=5, category_ids=[a.id, b.id])
    echoed = [f for f in body["filters"] if f["label"] == "Category"]
    assert len(echoed) == 1 and echoed[0]["values"] == [name], body["filters"]


# --------------------------------------------------------------------------- CA-4 history before 5 Sep (merge follow-through)


def _history_fact(db, when):
    from app.models.chat_facts import ChatAskFact
    from tests._chat_report_seed import _uid

    row = ChatAskFact(
        id=_uid(), source="history_backfill", turn_id=None, history_id=990000123, ask_seq=1,
        contact_respond_id="ZZT-hist-1", audience="dealer", contact_type="dealer", asked_at=when,
        channel="whatsapp", outcome="answered", is_media=False, phantom_dropped=False, tools=[],
    )
    db.add(row)
    db.flush()
    return row


def _run_august(db, date_to=None, date_from=None):
    from datetime import date

    from app.services.reports import ask

    return ask.run_ask(
        db, dataset="chat", basis="asks", measure="asks", group_by="topic", date_from=date_from or date(2026, 8, 1),
        date_to=date_to or date(2026, 8, 31), filters={}, warehouse_codes=[], policy=None,
        company_grants=None, sort="desc", top_n=5,
    )


def test_with_history_loaded_an_august_report_is_not_clipped_to_5_sep(db):
    from tests._chat_report_seed import day

    seed_world(db)
    _history_fact(db, day(8, 20))
    body = _run_august(db)
    assert body["date_from"] == "2026-08-20", "the period opens where the facts start"
    assert int(body["total"]["value"]) == 1, body


def test_with_history_loaded_the_empty_message_names_the_earliest_day(db):
    from datetime import date

    from tests._chat_report_seed import day

    seed_world(db)
    _history_fact(db, day(8, 20))
    body = _run_august(db, date_from=date(2026, 8, 21), date_to=date(2026, 8, 25))
    assert body["rows"] == [] and body["message"] == "No asks in that period. Chat data starts 20 Aug 2026.", body


def test_with_no_history_an_august_report_is_unchanged(db):
    from datetime import date

    seed_world(db)
    body = _run_august(db, date_to=date(2026, 9, 6))
    assert body["date_from"] == "2026-09-05", body
    body = _run_august(db, date_from=date(2026, 10, 20), date_to=date(2026, 10, 25))
    assert body["message"] == NO_ASKS, body
