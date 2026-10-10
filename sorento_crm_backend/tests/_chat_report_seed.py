"""Seed for the CA-3 chat dataset tests (the bot's chat report). Not a test module.

One synthetic world, every row seeded here on a blank Postgres schema (CI's database is
empty). Fake names only, no phone numbers, no message text (the fact tables hold none).

Facts (all UTC; Malaysia is +8 so none crosses a day). Weeks are Monday weeks.

    id   when    contact turn ask topic                 outcome   fail_reason  customer
    F1   Sep 8   C1      t1   1   inventory.availab.    answered  -            cust_a
    F2   Sep 9   C1      t2   1   inventory.availab.    missed    missed       cust_a
    F3   Sep 9   C2      t3   1   inventory.availab.    answered  -            cust_b
    F3b  Sep 9   C2      t3   2   inventory.availab.    answered  -            cust_b
    F4   Sep 15  C2      t4   1   order.status          answered  -            cust_b
    F5   Sep 16  C3      t5   1   (none)                technical technical    (none)
    F10  Sep 21  C3      t6   2   inventory.other       answered  -            (none)
    F6   Sep 10  C4      noise                          noise     -            (none)   excluded
    F7   Sep 10  C5      staff contact (an active CRM user is linked), availability   excluded
    F8   Sep  2  C6      before the traces begin (5 Sep)                       clipped
    F9   Oct  3  C7      after the September window                            outside

Who asks (the one asker rule): C1 C2 C3 C4 each have a respond_contacts row linked to a customer
(cust_a, cust_b, cust_a, cust_a), so they read dealer; C5 is staff (an active CRM user is linked);
C6 and C7 have no row (unclassified, outside the window anyway).

Counted for Sep 1 to Sep 30 (clipped to Sep 5): asks 7, turns (ask 1) 5, failed turns 2,
distinct contacts 3. Named items: P1 on F1 F2 (named) and F4 (shown), P2 on F3 (named);
P2 also on the noise F6 and P1 also on the office F7 (never counted).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

UTC = timezone.utc
NOT_LABELLED = "(not labelled yet)"


def _uid() -> str:
    return str(uuid.uuid4())


def day(month: int, d: int, hour: int = 3) -> datetime:
    return datetime(2026, month, d, hour, 0, tzinfo=UTC)


def seed_taxonomy(db) -> None:
    from app.models.chat_facts import ChatTopic

    for order, (code, label) in enumerate((("inventory", "Stock"), ("order", "Orders"))):
        db.add(ChatTopic(code=code, label=label, definition=label, version=1, sort_order=order))
    db.flush()
    for order, (code, parent, label) in enumerate(
        (
            ("inventory.availability", "inventory", "Availability"),
            ("inventory.other", "inventory", "Other stock"),
            ("order.status", "order", "Order status"),
        )
    ):
        db.add(ChatTopic(code=code, parent_code=parent, label=label, definition=label, version=2, sort_order=order))
    db.flush()


def add_fact(db, *, when, contact, turn, ask_seq=1, domain=None, topic=None, outcome="answered",
             failure_reason=None, contact_type="dealer", audience="dealer", customer_id=None,
             language=None, tag="ZZT"):
    from app.models.chat_facts import ChatAskFact

    row = ChatAskFact(
        id=_uid(), source="trace", turn_id=turn, ask_seq=ask_seq,
        contact_respond_id=f"{tag}-{contact}", audience=audience, contact_type=contact_type,
        asked_at=when, channel="whatsapp", outcome=outcome, failure_reason=failure_reason,
        is_media=False, phantom_dropped=False, domain=domain, topic_code=topic,
        taxonomy_version=2 if topic else None, language=language, customer_id=customer_id,
        tools=[],
    )
    db.add(row)
    db.flush()
    return row


def add_item(db, fact, product, *, role="named", kind="product"):
    from app.models.chat_facts import ChatAskItem

    db.add(ChatAskItem(id=_uid(), ask_id=fact.id, kind=kind, ref_id=str(product.id), role=role,
                       resolved_by="parser"))
    db.flush()


def _staff_contact(db, n: str):
    from app.models.access import RespondContact
    from app.models.user import User

    row = RespondContact(id=_uid(), phone_number=f"+6{uuid.uuid4().int % 10**10:010d}", respond_io_id=f"ZZT-{n}")
    db.add(row)
    db.flush()
    user = User(id=_uid(), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Staff User",
                status="ACTIVE", respond_contact_id=row.id)
    user.is_trashed = False
    db.add(user)
    db.flush()
    return row


def _dealer_contact(db, n: str, customer):
    from app.models.access import RespondContact, RespondContactCustomer
    from app.services.company_scope import DEFAULT_COMPANY_ID

    row = RespondContact(id=_uid(), phone_number=f"+6{uuid.uuid4().int % 10**10:010d}", respond_io_id=f"ZZT-{n}")
    db.add(row)
    db.flush()
    db.add(RespondContactCustomer(id=_uid(), contact_id=row.id, customer_id=customer.id,
                                  company_id=DEFAULT_COMPANY_ID))
    db.flush()
    return row


def seed_world(db) -> SimpleNamespace:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product
    from tests._pg_fixture import unique_code
    from tests._sales_report_do_seed import seed_customer

    w = SimpleNamespace()
    seed_taxonomy(db)
    w.cust_a = seed_customer(db, name=unique_code("CustA"))
    w.cust_b = seed_customer(db, name=unique_code("CustB"))
    w.p1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("PA"))
    w.p2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("PB"))
    a, b = w.cust_a.id, w.cust_b.id
    t = {n: _uid() for n in ("t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9", "t10")}
    av, other, st = "inventory.availability", "inventory.other", "order.status"
    f1 = add_fact(db, when=day(9, 8), contact="c1", turn=t["t1"], domain="inventory", topic=av, customer_id=a, language="en")
    f2 = add_fact(db, when=day(9, 9), contact="c1", turn=t["t2"], domain="inventory", topic=av, customer_id=a,
                  outcome="missed", failure_reason="missed", language="en")
    f3 = add_fact(db, when=day(9, 9), contact="c2", turn=t["t3"], domain="inventory", topic=av, customer_id=b, language="ms")
    add_fact(db, when=day(9, 9), contact="c2", turn=t["t3"], ask_seq=2, domain="inventory", topic=av, customer_id=b, language="ms")
    f4 = add_fact(db, when=day(9, 15), contact="c2", turn=t["t4"], domain="order", topic=st, customer_id=b, language="en")
    add_fact(db, when=day(9, 16), contact="c3", turn=t["t5"], domain="inventory", outcome="technical",
             failure_reason="technical", language="zh")
    add_fact(db, when=day(9, 21), contact="c3", turn=t["t6"], ask_seq=2, domain="inventory", topic=other, language="zh")
    f6 = add_fact(db, when=day(9, 10), contact="c4", turn=t["t7"], outcome="noise")
    f7 = add_fact(db, when=day(9, 10), contact="c5", turn=t["t8"], domain="inventory", topic=av, contact_type="office",
                  audience="staff")
    add_fact(db, when=day(9, 2), contact="c6", turn=t["t9"], domain="inventory", topic=av)
    add_fact(db, when=day(10, 3), contact="c7", turn=t["t10"], domain="inventory", topic=av)
    w.c1 = _dealer_contact(db, "c1", w.cust_a)
    w.c2 = _dealer_contact(db, "c2", w.cust_b)
    w.c3 = _dealer_contact(db, "c3", w.cust_a)
    w.c4 = _dealer_contact(db, "c4", w.cust_a)
    w.c5 = _staff_contact(db, "c5")  # F7's asker: a linked CRM user, so the asker rule reads staff
    add_item(db, f1, w.p1)
    add_item(db, f2, w.p1)
    add_item(db, f3, w.p2)
    add_item(db, f4, w.p1, role="shown")
    add_item(db, f6, w.p2)
    add_item(db, f7, w.p1)
    return w


def add_unlabelled(db, n: int) -> None:
    """`n` more counted asks with no topic (each its own contact), to push coverage under 80%."""
    for i in range(n):
        add_fact(db, when=day(9, 17), contact=f"u{i}", turn=_uid(), domain="inventory")
