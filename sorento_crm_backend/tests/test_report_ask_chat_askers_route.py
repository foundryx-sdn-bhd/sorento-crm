"""CA-3 owner hand test round, RED: asker scope, asker axis and filter, category, brand, agent on
customer rows (UAC-25 to UAC-31), through `GET /api/v1/order-management/report-ask?dataset=chat`
and `ask.run_ask`. World: `tests/_chat_asker_seed.py` (read its table first). Postgres only.

Pinned where the plan is silent (the coder follows, or asks the captain to change the test):
* route params `askers`, `category_ids`, `brand_ids` (lists); `run_ask` filter words `asker`,
  `category`, `brand` (the route param minus the plural, as `subjects` -> `subject`);
* group_by words `asker`, `salesperson`, `category`, `brand`; group labels are free;
* asker row names are exactly `Dealer`, `Salesperson`, `Staff`, `Unclassified`; a bucket with no
  asks has no row; `salesperson` rows are the asking salespeople's `sales_agents.sales_agent`
  name and carry no `Dealer` / `Unclassified` row;
* `DEFAULT_ASKERS` lives in `app.services.reports.datasets.chat_report_ask` (the chat dataset
  module) as a tuple of asker words, read at run time so a monkeypatch takes effect;
* response field `unclassified_askers` (int): present when group_by is `asker` or `salesperson`
  or an `askers` param is given, else null/absent. It counts distinct unclassified contacts with a
  counted ask in the period and the OTHER filters, ignoring the asker filter itself (so "dealers
  only" still says how many unclassified contacts are not in it), and it ignores DEFAULT_ASKERS
  for the same reason;
* DEFAULT_ASKERS is the ONE switch (crew ruling 10 Oct): facts are stamped as `facts._who` writes
  them (unlinked and user-linked contacts are contact_type "office"), and a chat run must still
  count the unclassified ones. So the `contact_type` default must not apply to chat runs;
* a customer row carries `agent` (the card agent's name, or null); other rows need not.
CONFLICT flagged: the plan says an `unclassified` asking contact is refused like a contact
without the grant, but existing UAC-17 tests (an unlinked contact holding the grant sees all)
must stay green, so no refusal test is pinned here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests._chat_asker_seed import give_office_type, link_agent, link_customer, link_user, seed_asker_world
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    client,
    db,
)
from tests.test_report_ask_chat_route import KEY, PERIOD, ROUTE, _ask, _code, _ok, _rows, _staff

D = Decimal
ALL = ["dealer", "salesperson", "staff", "unclassified"]


def _granted(db):
    """A contact holding the grant, with no link of any kind (set up further by the test)."""
    return _staff(db)


# ======================================================================== route scope (UAC-26)


def test_a_user_linked_and_customer_linked_contact_is_staff_not_dealer_scoped(client, db):
    w = seed_asker_world(db)
    contact = _granted(db)
    link_user(db, contact)
    link_customer(db, contact, w.cust_b)
    db.commit()
    body = _ok(client, contact, group_by="customer", top_n=10)
    assert D(str(body["total"]["value"])) == 8, "staff sees every customer's asks, not its own link's"
    assert {r["name"] for r in body["rows"]} == {w.cust_a.customer_name, w.cust_b.customer_name}, body["rows"]


def test_a_user_linked_contact_may_name_a_customer_outside_its_links(client, db):
    w = seed_asker_world(db)
    contact = _granted(db)
    link_user(db, contact)
    link_customer(db, contact, w.cust_b)
    db.commit()
    body = _ok(client, contact, group_by="topic", top_n=10, customer_ids=[w.cust_a.id])
    assert D(str(body["total"]["value"])) == 2, body


def test_an_office_access_type_and_customer_link_is_staff_too(client, db):
    w = seed_asker_world(db)
    contact = _granted(db)
    give_office_type(db, contact)
    link_customer(db, contact, w.cust_b)
    db.commit()
    body = _ok(client, contact, group_by="customer", top_n=10)
    assert D(str(body["total"]["value"])) == 8, body


def test_a_pure_dealer_is_still_scoped_to_its_own_customers(client, db):
    w = seed_asker_world(db)
    contact = _granted(db)
    link_customer(db, contact, w.cust_b)
    db.commit()
    body = _ok(client, contact, group_by="topic", top_n=10)
    assert D(str(body["total"]["value"])) == 3, body
    assert _ask(client, contact, group_by="customer", top_n=10).status_code == 403, "dealer dims only"


def test_a_salesperson_with_a_customer_link_is_narrowed_to_its_book_not_its_links(client, db):
    w = seed_asker_world(db)
    contact = _granted(db)
    agent = link_agent(db, contact, "ZZT Agent Book")
    w.cust_a.sales_agent_id = agent.id
    link_customer(db, contact, w.cust_b)  # a link elsewhere does not widen or replace the book
    db.commit()
    body = _ok(client, contact, group_by="topic", top_n=10)
    assert D(str(body["total"]["value"])) == 2, "only cust_a's counted asks (F1 F2), the book"


# ======================================================================== asker axis (UAC-27, 29)


def test_group_by_asker_has_the_four_words_and_staff_is_out_by_default(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="asker", top_n=10)
    assert _rows(body) == {"Dealer": D(2), "Salesperson": D(4), "Unclassified": D(2)}, body["rows"]
    assert body["group_by"] == "asker" and D(str(body["total"]["value"])) == 8, body
    assert body["unclassified_askers"] == 1, body


def test_all_four_askers_named_adds_the_staff_row(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="asker", top_n=10, askers=ALL)
    assert _rows(body) == {"Dealer": D(2), "Salesperson": D(4), "Staff": D(3), "Unclassified": D(2)}, body["rows"]
    assert D(str(body["total"]["value"])) == 11 and body["unclassified_askers"] == 1, body


def test_group_by_salesperson_ranks_asking_salespeople_by_name(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="salesperson", top_n=10)
    rows = _rows(body)
    assert rows["ZZT Agent Two"] == 3 and rows["ZZT Agent Ten"] == 1, body["rows"]
    assert body["rows"][0]["name"] == "ZZT Agent Two" and body["rows"][0]["rank"] == 1, body["rows"]
    assert not {"Dealer", "Unclassified", "Staff"} & set(rows), rows
    assert body["unclassified_askers"] == 1, body


def test_the_askers_filter_narrows_to_those_askers(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10, askers=["dealer"])
    assert D(str(body["total"]["value"])) == 2 and body["unclassified_askers"] == 1, body
    body = _ok(client, _granted(db), group_by="topic", top_n=10, askers=["staff"])
    assert D(str(body["total"]["value"])) == 3, "naming staff brings the staff asks back"
    body = _ok(client, _granted(db), group_by="topic", top_n=10, askers=["dealer", "salesperson"])
    assert D(str(body["total"]["value"])) == 6, body


def test_an_unclassified_only_filter_counts_only_them(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10, askers=["unclassified"])
    assert D(str(body["total"]["value"])) == 2 and body["unclassified_askers"] == 1, body


def test_no_asker_split_or_filter_carries_no_unclassified_count(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10)
    assert body.get("unclassified_askers") is None, body
    assert D(str(body["total"]["value"])) == 8, "the default still excludes staff"


def test_the_unclassified_count_is_zero_when_there_are_none(client, db):
    from app.models.chat_facts import ChatAskFact

    seed_asker_world(db)
    db.query(ChatAskFact).filter(ChatAskFact.contact_respond_id == "ZZT-c3").delete()
    db.commit()
    body = _ok(client, _granted(db), group_by="asker", top_n=10)
    assert body["unclassified_askers"] == 0 and "Unclassified" not in _rows(body), body


def test_default_askers_is_the_one_switch_for_unclassified(client, db, monkeypatch):
    from app.services.reports.datasets import chat_report_ask

    assert tuple(chat_report_ask.DEFAULT_ASKERS) == ("dealer", "salesperson", "unclassified")
    seed_asker_world(db)
    monkeypatch.setattr(chat_report_ask, "DEFAULT_ASKERS", ("dealer", "salesperson"))
    body = _ok(client, _granted(db), group_by="asker", top_n=10)
    assert _rows(body) == {"Dealer": D(2), "Salesperson": D(4)}, body["rows"]
    assert D(str(body["total"]["value"])) == 6 and body["unclassified_askers"] == 1, body
    named = _ok(client, _granted(db), group_by="asker", top_n=10, askers=["unclassified"])
    assert _rows(named) == {"Unclassified": D(2)}, "a message can still name them"


def test_run_ask_takes_the_asker_filter_word(db):
    from app.services.reports import ask

    seed_asker_world(db)
    body = ask.run_ask(
        db, dataset="chat", basis="asks", measure="asks", group_by="asker", date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30), filters={"asker": ["dealer", "staff"]}, warehouse_codes=[], policy=None,
        company_grants=None, sort="desc", top_n=10,
    )
    assert {r["name"]: r["value"] for r in body["rows"]} == {"Dealer": 2, "Staff": 3}, body["rows"]


# ======================================================================== category and brand (UAC-30)


def test_group_by_category_ranks_the_categories_of_the_named_products(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="category", top_n=10)
    assert _rows(body) == {"ZZT Water Closet": D(2), "ZZT Basin": D(1)}, body["rows"]
    assert body["group_by"] == "category" and body["group_label"], body


def test_group_by_brand_ranks_the_brands_of_the_named_products(client, db):
    seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="brand", top_n=10)
    assert _rows(body) == {"ZZT Sorento": D(2), "ZZT Other": D(1)}, body["rows"]


def test_shown_products_never_count_toward_a_category(client, db):
    w = seed_asker_world(db)
    w.p1.category_id = w.cat_basin.id  # F4 only SHOWED p1: it adds nothing; F1 F2 now count as Basin
    db.commit()
    body = _ok(client, _granted(db), group_by="category", top_n=10)
    assert _rows(body) == {"ZZT Basin": D(3)}, body["rows"]


def test_the_category_filter_narrows_to_asks_naming_a_product_of_it(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10, category_ids=[w.cat_wc.id])
    assert D(str(body["total"]["value"])) == 2, body


def test_the_brand_filter_narrows_to_asks_naming_a_product_of_it(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10, brand_ids=[w.brand_o.id])
    assert D(str(body["total"]["value"])) == 1, body


def test_categories_asked_by_salespeople_for_one_brand(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="category", top_n=10, askers=["salesperson"], brand_ids=[w.brand_o.id])
    assert _rows(body) == {"ZZT Basin": D(1)}, body["rows"]
    body = _ok(client, _granted(db), group_by="category", top_n=10, askers=["salesperson"], brand_ids=[w.brand_s.id])
    assert body["rows"] == [], "the salesperson never named a Sorento product"


def test_a_category_and_brand_together_narrow_both_ways(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="product", top_n=10, category_ids=[w.cat_wc.id], brand_ids=[w.brand_s.id])
    assert _rows(body) == {w.p1.product_code: D(2)}, body["rows"]
    body = _ok(client, _granted(db), group_by="product", top_n=10, category_ids=[w.cat_wc.id], brand_ids=[w.brand_o.id])
    assert body["rows"] == [], body


def test_unknown_params_are_still_422_and_the_new_ones_are_known(client, db):
    seed_asker_world(db)
    contact = _granted(db)
    resp = _ask(client, contact, group_by="topic", top_n=3, bogus="x")
    assert resp.status_code == 422 and _code(resp) == "unknown_param" and resp.json()["detail"] == "bogus", resp.text
    resp = client.get(
        ROUTE, params={"dataset": "chat", **PERIOD, **_as_contact(contact), "category_ids": ""}, headers=KEY
    )
    assert resp.status_code == 422, "category_ids was given but names nothing"


def test_the_category_filter_is_echoed_by_name(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="topic", top_n=10, category_ids=[w.cat_wc.id])
    assert [f["values"] for f in body["filters"] if f["key"] == "category"] == [["ZZT Water Closet"]], body["filters"]


# ======================================================================== agent on customer rows (UAC-31)


def test_customer_rows_carry_the_card_agent(client, db):
    w = seed_asker_world(db)
    body = _ok(client, _granted(db), group_by="customer", top_n=10)
    by_name = {r["name"]: r for r in body["rows"]}
    assert by_name[w.cust_a.customer_name]["agent"] == "ZZT Agent Two", body["rows"]
    assert "agent" in by_name[w.cust_b.customer_name] and by_name[w.cust_b.customer_name]["agent"] is None, body["rows"]


def test_unlinked_contacts_stamped_office_still_count_as_unclassified(client, db):
    """The prod-copy blocker: unlinked contacts are stamped contact_type office by the writer."""
    from tests._chat_asker_seed import _respond_contact
    from tests._chat_report_seed import _uid, add_fact, day

    seed_asker_world(db)
    _respond_contact(db, "c11")
    for hour in (3, 4):
        add_fact(db, when=day(9, 14, hour), contact="c11", turn=_uid(), domain="inventory",
                 topic="inventory.availability", contact_type="office", audience="unknown")
    db.commit()
    contact = _granted(db)
    body = _ok(client, contact, group_by="asker", top_n=10)
    assert _rows(body) == {"Dealer": D(2), "Salesperson": D(4), "Unclassified": D(4)}, body["rows"]
    assert body["unclassified_askers"] == 2, "c3 and c11, both unlinked"
    body = _ok(client, contact, group_by="salesperson", top_n=10)
    assert _rows(body)["ZZT Agent Ten"] == 1, "a salesperson with no customer link is stamped office too"


# ======================================================================== review round: asks not items, agent by id


def test_a_category_and_a_brand_count_asks_not_items(client, db):
    from tests._chat_report_seed import add_item
    from tests._mc_lookup_seed import product
    from tests._pg_fixture import unique_code
    from app.models.chat_facts import ChatAskFact
    from app.services.company_scope import DEFAULT_COMPANY_ID

    w = seed_asker_world(db)
    before = _ok(client, _granted(db), group_by="category", top_n=10)
    p3 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("PC"))
    p3.category_id, p3.brand_id = w.cat_wc.id, w.brand_s.id
    f1 = db.query(ChatAskFact).filter(ChatAskFact.contact_respond_id == "ZZT-c1").order_by(ChatAskFact.asked_at).first()
    add_item(db, f1, p3)  # the same ask now names two products of one category and brand
    db.commit()
    body = _ok(client, _granted(db), group_by="category", top_n=10)
    assert _rows(body) == {"ZZT Water Closet": D(2), "ZZT Basin": D(1)}, body["rows"]
    assert body["total"] == before["total"], "the total stays the ask count"
    body = _ok(client, _granted(db), group_by="brand", top_n=10)
    assert _rows(body) == {"ZZT Sorento": D(2), "ZZT Other": D(1)}, body["rows"]


def test_same_named_customers_in_two_companies_each_show_their_own_agent(client, db):
    from tests._chat_report_seed import _uid, add_fact, day
    from tests._mc_lookup_seed import MOCHA_ID, customer, seed_mocha

    w = seed_asker_world(db)
    seed_mocha(db)
    twin = customer(db, company_id=MOCHA_ID, name=w.cust_a.customer_name)  # no card agent
    for hour in range(5):
        add_fact(db, when=day(9, 18, hour), contact="c1", turn=_uid(), domain="inventory",
                 topic="inventory.availability", customer_id=twin.id)
    db.commit()
    body = _ok(client, _granted(db), group_by="customer", top_n=10)
    same = [r for r in body["rows"] if r["name"] == w.cust_a.customer_name]
    assert sorted(((int(r["value"]), r["agent"]) for r in same), key=lambda t: t[0]) == [
        (2, "ZZT Agent Two"), (5, None),
    ], body["rows"]
