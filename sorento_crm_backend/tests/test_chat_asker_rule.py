"""CA-3 owner hand test round, RED: the one asker rule (UAC-25, UAC-29 part).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md, last section
("one asker rule"). Module `app/services/contact_asker.py`, "the only copy".

Pinned where the plan is silent (the coder follows, or asks the captain to change the test):
* `asker.ASKERS == ("dealer", "salesperson", "staff", "unclassified")` (the four words, that order);
* `asker.classify(db, contact_id) -> str`, `contact_id` the INTERNAL `respond_contacts.id`
  (as `contact_customer_scope` takes it), one of the four words;
* `asker.sql_expr(contact_respond_id_column)` -> a SQL scalar expression over the fact's
  `contact_respond_id` (joined to `respond_contacts.respond_io_id`) giving the same four words.
  A fact whose contact has no `respond_contacts` row reads `unclassified`;
* precedence staff (a user linked via `users.respond_contact_id`, OR an active office access type)
  > salesperson (`sales_agents.contact_id`) > dealer (`respond_contact_customers`) > unclassified.
Postgres only; every row is seeded here.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from tests._chat_asker_seed import (
    _respond_contact,
    give_office_type,
    link_agent,
    link_customer,
    link_user,
)
from tests._chat_report_seed import add_fact, day, _uid
from tests._pg_fixture import blank_session, unique_code
from tests._sales_report_do_seed import seed_customer


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.base import set_company_scope

        set_company_scope(session, None)
        yield session


def _world(db):
    """One contact per case, each with one fact."""
    cust = seed_customer(db, name=unique_code("AskerCust"))
    contacts = {}
    for key in ("user_agent_customer", "agent_customer", "customer_only", "nothing", "office_customer",
                "user_only", "office_only", "agent_only"):
        contacts[key] = _respond_contact(db, key)
        add_fact(db, when=day(9, 10), contact=key, turn=_uid(), domain="inventory")
    link_user(db, contacts["user_agent_customer"])
    link_agent(db, contacts["user_agent_customer"], "ZZT Agent U")
    link_customer(db, contacts["user_agent_customer"], cust)
    link_agent(db, contacts["agent_customer"], "ZZT Agent B")
    link_customer(db, contacts["agent_customer"], cust)
    link_customer(db, contacts["customer_only"], cust)
    give_office_type(db, contacts["office_customer"])
    link_customer(db, contacts["office_customer"], cust)
    link_user(db, contacts["user_only"])
    give_office_type(db, contacts["office_only"])
    link_agent(db, contacts["agent_only"], "ZZT Agent A")
    db.commit()
    return contacts


EXPECTED = {
    "user_agent_customer": "staff",
    "agent_customer": "salesperson",
    "customer_only": "dealer",
    "nothing": "unclassified",
    "office_customer": "staff",
    "user_only": "staff",
    "office_only": "staff",
    "agent_only": "salesperson",
}


def test_the_four_words_are_exported_in_order():
    from app.services import contact_asker as asker

    assert tuple(asker.ASKERS) == ("dealer", "salesperson", "staff", "unclassified")


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_classify_follows_the_precedence(db, key):
    from app.services import contact_asker as asker

    contacts = _world(db)
    assert asker.classify(db, contacts[key].id) == EXPECTED[key], key


def test_an_inactive_office_access_type_is_not_staff(db):
    from app.services import contact_asker as asker
    from tests.test_top_selling_report import _access

    contact = _respond_contact(db, "inactive")
    _access(db, contact, "Sorento Office", active=False)
    db.commit()
    assert asker.classify(db, contact.id) == "unclassified"


def test_an_unknown_contact_id_is_unclassified(db):
    from app.services import contact_asker as asker

    assert asker.classify(db, "no-such-contact") == "unclassified"


def test_the_sql_expression_gives_the_same_answers_as_classify(db):
    from app.models.chat_facts import ChatAskFact
    from app.services import contact_asker as asker

    contacts = _world(db)
    facts = ChatAskFact.__table__
    got = dict(
        db.execute(
            sa.select(facts.c.contact_respond_id, asker.sql_expr(facts.c.contact_respond_id)).distinct()
        ).all()
    )
    for key, contact in contacts.items():
        assert got[f"ZZT-{key}"] == asker.classify(db, contact.id) == EXPECTED[key], key


def test_a_fact_whose_contact_has_no_row_is_unclassified_in_sql(db):
    from app.models.chat_facts import ChatAskFact
    from app.services import contact_asker as asker

    add_fact(db, when=day(9, 10), contact="ghost", turn=_uid(), domain="inventory")
    db.commit()
    facts = ChatAskFact.__table__
    (word,) = db.execute(
        sa.select(asker.sql_expr(facts.c.contact_respond_id)).where(facts.c.contact_respond_id == "ZZT-ghost")
    ).scalars().all()
    assert word == "unclassified"


def test_linking_a_contact_reclassifies_its_history(db):
    """Computed at query time from the links as they are now: no stored column, no backfill."""
    from app.models.chat_facts import ChatAskFact
    from app.services import contact_asker as asker

    contact = _respond_contact(db, "later")
    add_fact(db, when=day(9, 10), contact="later", turn=_uid(), domain="inventory")
    db.commit()
    facts = ChatAskFact.__table__
    query = sa.select(asker.sql_expr(facts.c.contact_respond_id)).where(facts.c.contact_respond_id == "ZZT-later")
    assert db.execute(query).scalar_one() == "unclassified"
    link_user(db, contact)
    db.commit()
    assert db.execute(query).scalar_one() == "staff"
