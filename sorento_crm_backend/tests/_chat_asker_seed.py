"""Seed for the CA-3 asker / category / brand tests. Not a test module. Synthetic names only.

Builds on `tests/_chat_report_seed.seed_world` (read its table first) and gives the contacts
behind its facts an identity, then adds a few more asks. A fact's `contact_respond_id` is
"ZZT-<n>" and joins `respond_contacts.respond_io_id` (the way `facts._who` reads it).

    contact  identity                                          asks (Sep 1 to 30, counted by default)
    c1       customer link cust_a only         -> dealer       F1 F2 (cust_a)                      2
    c2       agent "ZZT Agent Two" + link cust_b -> salesperson F3 F3b F4 (cust_b)                  3
    c3       row with no link at all           -> unclassified F5 F10 (no customer)                 2
    c8       a CRM user linked (users.respond_contact_id) -> staff    Sep 11 availability           1
    c9       active office access type + link cust_a -> staff         Sep 12 availability           1
    c10      agent "ZZT Agent Ten", no link    -> salesperson Sep 13 order.status                   1
    c5       a CRM user linked -> staff                            F7 Sep 10 availability       1

Every fact is stamped the way `facts._who` writes it: a user-linked contact is audience staff /
contact_type office; else a customer link is dealer / dealer; else (no links, an agent link
alone included) unknown / office. Crew ruling 10 Oct: DEFAULT_ASKERS is the ONE switch for chat
runs, so the office stamp must not hide anyone. c5 (the office-typed F7) is given a user link so
it is staff and DEFAULT_ASKERS alone keeps it out; naming staff brings back c5, c8, c9 (3 asks).

Default (dealer + salesperson + unclassified): 8 asks, Dealer 2, Salesperson 4, Unclassified 2,
Staff 0 shown. With every asker: 11 asks, Staff 3. Distinct unclassified contacts: 1 (c3).
cust_a's card agent is "ZZT Agent Two"; cust_b has none.

Products: p1 (named on F1 F2) is category "ZZT Water Closet", brand "ZZT Sorento"; p2 (named on
F3, a salesperson ask) is category "ZZT Basin", brand "ZZT Other".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from tests._chat_report_seed import add_fact, add_item, day, seed_world, _uid


def _respond_contact(db, n: str):
    from app.models.access import RespondContact

    row = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6{uuid.uuid4().int % 10**10:010d}",
                         respond_io_id=f"ZZT-{n}")
    db.add(row)
    db.flush()
    return row


def link_customer(db, contact, cust):
    from app.models.access import RespondContactCustomer
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=contact.id, customer_id=cust.id,
                                  company_id=DEFAULT_COMPANY_ID))
    db.flush()


def link_user(db, contact, *, status: str = "ACTIVE", trashed: bool = False):
    from app.models.user import User

    row = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Staff User",
               status=status, respond_contact_id=contact.id)
    row.is_trashed = trashed
    db.add(row)
    db.flush()
    return row


def link_agent(db, contact, name: str):
    from tests._sales_report_do_seed import seed_agent

    agent = seed_agent(db, code=name)
    agent.contact_id = contact.id
    db.flush()
    return agent


def give_office_type(db, contact):
    from tests.test_top_selling_report import _access

    _access(db, contact, "Sorento Office")


def _stamp(db, *, office, dealer):
    """Re-stamp the facts' contact_type / audience the way `facts._who` would write them."""
    from app.models.chat_facts import ChatAskFact

    for tag, ctype, audience in (*((n, "office", None) for n in office), *((n, "dealer", "dealer") for n in dealer)):
        audience = audience or ("staff" if tag in ("c5", "c8") else "unknown")
        db.query(ChatAskFact).filter(ChatAskFact.contact_respond_id == f"ZZT-{tag}").update(
            {"contact_type": ctype, "audience": audience}
        )
    db.flush()


def seed_asker_world(db) -> SimpleNamespace:
    from app.models.product import Brand, ProductCategory
    from app.services.company_scope import DEFAULT_COMPANY_ID

    w = seed_world(db)
    # c1 (cust_a) and c2 (cust_b) are already linked dealers from seed_world; c3 is meant unclassified here
    from app.models.access import RespondContactCustomer

    db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == w.c3.id).delete()
    db.flush()
    w.agent_two = link_agent(db, w.c2, "ZZT Agent Two")
    w.cust_a.sales_agent_id = w.agent_two.id

    w.c8, w.c9, w.c10 = (_respond_contact(db, n) for n in ("c8", "c9", "c10"))
    link_user(db, w.c8)
    give_office_type(db, w.c9)
    link_customer(db, w.c9, w.cust_a)
    w.agent_ten = link_agent(db, w.c10, "ZZT Agent Ten")
    add_fact(db, when=day(9, 11), contact="c8", turn=_uid(), domain="inventory", topic="inventory.availability")
    add_fact(db, when=day(9, 12), contact="c9", turn=_uid(), domain="inventory", topic="inventory.availability",
             customer_id=w.cust_a.id)
    add_fact(db, when=day(9, 13), contact="c10", turn=_uid(), domain="order", topic="order.status")

    _stamp(db, office=("c3", "c5", "c8", "c10"), dealer=("c1", "c2", "c9"))

    w.cat_wc = ProductCategory(id=str(uuid.uuid4()), category_code=f"ZZTWC{uuid.uuid4().hex[:6]}",
                               category_name="ZZT Water Closet", company_id=DEFAULT_COMPANY_ID)
    w.cat_basin = ProductCategory(id=str(uuid.uuid4()), category_code=f"ZZTBA{uuid.uuid4().hex[:6]}",
                                  category_name="ZZT Basin", company_id=DEFAULT_COMPANY_ID)
    w.brand_s = Brand(id=str(uuid.uuid4()), brand_code=f"ZS{uuid.uuid4().hex[:6]}", brand_name="ZZT Sorento",
                      company_id=DEFAULT_COMPANY_ID)
    w.brand_o = Brand(id=str(uuid.uuid4()), brand_code=f"ZO{uuid.uuid4().hex[:6]}", brand_name="ZZT Other",
                      company_id=DEFAULT_COMPANY_ID)
    db.add_all([w.cat_wc, w.cat_basin, w.brand_s, w.brand_o])
    db.flush()
    w.p1.category_id, w.p1.brand_id = w.cat_wc.id, w.brand_s.id
    w.p2.category_id, w.p2.brand_id = w.cat_basin.id, w.brand_o.id
    db.flush()
    return w


def give_role(db, user, slug: str):
    """Assign the user the role with `slug` (created on first use)."""
    from app.models.user import UserRole, UserRoleAssignment

    role = db.query(UserRole).filter(UserRole.slug == slug).first()
    if role is None:
        role = UserRole(id=str(uuid.uuid4()), slug=slug, name=f"ZZT {slug}")
        db.add(role)
        db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.flush()
    return role
