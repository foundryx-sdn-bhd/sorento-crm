"""Resolving a WhatsApp contact to the customer account they belong to.

The dealer kit needs this: a consumer arrives holding a portal token, and
whether their design becomes a quote against a real account depends on knowing
which account that is. It is deliberately NOT dealer-kit code - "who is this
phone number, commercially" is a question the whole CRM asks.

Two operations, kept apart on purpose:

- **Resolution** reads confirmed links only. It answers, or it declines.
- **Proposal** matches phone numbers and returns candidates. It never writes.

The split is the whole design. An automatic phone match that linked itself would
attach a quote to the wrong company the first time two people shared a landline,
and nobody would find out until the invoice.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.access import RespondContact, RespondContactCustomer
from app.models.order import Customer
from app.models.sales_agent import SalesAgent
from app.services.company_scope_resolver import company_name_map
from app.services.error_handler import handle_not_found
from app.utils.phone_normalize import normalize_phone

# Malaysian mobile numbers are written with and without the 60 country code and
# with or without the trunk 0, so exact equality misses the common case. Nine
# digits is enough to identify a subscriber and short enough to still match
# across those prefixes; below that the match is noise, not signal.
_SUFFIX_DIGITS = 9


def list_links(db: Session, contact_id: str) -> list[RespondContactCustomer]:
    """Every link for a contact, within the caller's company scope."""
    return (
        db.query(RespondContactCustomer)
        .filter(RespondContactCustomer.contact_id == contact_id)
        .order_by(RespondContactCustomer.created_at)
        .all()
    )


def resolve_customer(db: Session, contact_id: str) -> str | None:
    """The customer this contact acts for, or None.

    None has two meanings and both are correct: nobody has linked them, or they
    are linked to several accounts and no primary has been chosen. In the second
    case an answer exists in the data but not in the domain, and returning a
    guess is worse than returning nothing - the caller can ask.
    """
    links = list_links(db, contact_id)
    if not links:
        return None
    if len(links) == 1:
        return links[0].customer_id

    primary = [link for link in links if link.is_primary]
    return primary[0].customer_id if primary else None


def link_customer(
    db: Session,
    contact_id: str,
    customer_id: str,
    is_primary: bool = False,
    source: str = "manual",
    linked_by: str | None = None,
    customer: Customer | None = None,
) -> RespondContactCustomer:
    """Create (or update) the link. Re-linking the same pair is not an error.

    Idempotent because the callers are a human clicking twice and a backfill
    re-running, and both deserve the same answer. Two concurrent inserts of the same pair
    are the same case: the loser hits the unique constraint and answers the winner's row.

    `customer` is the row a route has already read under the caller's scope; passing it
    saves the second lookup.
    """
    existing = get_link(db, contact_id, customer_id)

    # The link belongs to the CUSTOMER's company. `before_insert` would otherwise stamp it
    # from the caller's scope and raises when that scope spans two companies, so a staff
    # user scoped to both could not link at all. Read under the caller's scope: a customer
    # they cannot see leaves this None and the insert falls back to the old behaviour.
    if customer is None:
        customer = get_customer_in_scope(db, customer_id)
    company_id = customer.company_id if customer is not None else None

    if is_primary:
        _demote_other_primaries(
            db, contact_id, keep_customer_id=customer_id, company_id=company_id
        )

    if existing:
        if is_primary:
            existing.is_primary = True
        return existing

    link = RespondContactCustomer(
        contact_id=contact_id,
        customer_id=customer_id,
        company_id=company_id,
        is_primary=is_primary,
        source=source,
        linked_by=linked_by,
    )
    try:
        # A savepoint, so losing the race rolls back this insert only and not the caller's
        # open transaction.
        with db.begin_nested():
            db.add(link)
            db.flush()
    except IntegrityError:
        winner = get_link(db, contact_id, customer_id)
        if winner is None:
            raise
        if is_primary:
            winner.is_primary = True
        return winner
    return link


def unlink_customer(db: Session, contact_id: str, customer_id: str) -> bool:
    """Drop the link. The customer and the contact both survive it."""
    link = (
        db.query(RespondContactCustomer)
        .filter(
            RespondContactCustomer.contact_id == contact_id,
            RespondContactCustomer.customer_id == customer_id,
        )
        .first()
    )
    if not link:
        return False
    db.delete(link)
    return True


def _demote_other_primaries(
    db: Session,
    contact_id: str,
    keep_customer_id: str,
    company_id: str | None = None,
) -> None:
    """Only one primary per contact per company - the index enforces it too.

    Demoting here rather than letting the insert fail means "make this the
    primary" behaves like the sentence it is, instead of asking the caller to
    clear the old one first.
    """
    query = db.query(RespondContactCustomer).filter(
        RespondContactCustomer.contact_id == contact_id,
        RespondContactCustomer.customer_id != keep_customer_id,
        RespondContactCustomer.is_primary.is_(True),
    )
    if company_id is not None:
        # The index is per (contact, company): a Mocha primary does not compete with a
        # Sorento one, so demoting it would silently undo somebody else's choice.
        query = query.filter(RespondContactCustomer.company_id == company_id)
    others = query.all()
    for link in others:
        link.is_primary = False
    if others:
        # The partial unique index is checked per statement, so the demotion has
        # to reach the database before the new primary is inserted.
        db.flush()


def propose_customers(db: Session, contact_id: str) -> Sequence[Customer]:
    """Customers whose phone number looks like this contact's. Never writes.

    A proposal in the glossary sense: the system offers, a human confirms. The
    match is on the last nine digits so that 60123456789, 0123456789 and
    123456789 are recognised as one subscriber, and anything shorter than that
    is refused rather than matched loosely.
    """
    contact = db.query(RespondContact).filter(RespondContact.id == contact_id).first()
    if not contact:
        return []

    suffix = normalize_phone(contact.phone_number)[-_SUFFIX_DIGITS:]
    if len(suffix) < _SUFFIX_DIGITS:
        return []

    already_linked = {link.customer_id for link in list_links(db, contact_id)}

    # Normalising in SQL would need a per-row regexp on a column with no
    # functional index; the candidate set here is one company's customers with a
    # phone number at all, which is small enough to filter honestly in Python.
    candidates = (
        db.query(Customer)
        .filter(Customer.phone_number.isnot(None))
        .filter(Customer.phone_number != "")
        .all()
    )

    return [
        customer
        for customer in candidates
        if customer.id not in already_linked
        and normalize_phone(customer.phone_number).endswith(suffix)
    ]


def get_customer_in_scope(db: Session, customer_id: str) -> Customer | None:
    """The customer, or None when it does not exist OR the caller's scope hides it.

    Deliberately one answer for both: a route built on this cannot tell a caller which of
    the two it was. A malformed id is None too, so it never reaches Postgres as a cast.
    """
    try:
        uuid.UUID(str(customer_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return db.query(Customer).filter(Customer.id == customer_id).first()


def get_contact(db: Session, contact_id: str) -> RespondContact | None:
    return db.query(RespondContact).filter(RespondContact.id == contact_id).first()


def links_with_customers(
    db: Session, contact_id: str
) -> list[tuple[RespondContactCustomer, Customer]]:
    """Each link with its customer (and, through the customer, its agent), oldest first."""
    return (
        db.query(RespondContactCustomer, Customer)
        .join(Customer, Customer.id == RespondContactCustomer.customer_id)
        .filter(RespondContactCustomer.contact_id == contact_id)
        .order_by(RespondContactCustomer.created_at, RespondContactCustomer.id)
        .all()
    )


def customer_codes_by_contact(db: Session, contact_ids: Sequence[str]) -> dict[str, list[str]]:
    """Linked customer codes (sorted asc) per contact, ONE query for the whole page.

    Customer is company-scoped, so the join sees only the caller's customers, the same
    as the contact card's payload."""
    if not contact_ids:
        return {}
    rows = (
        db.query(RespondContactCustomer.contact_id, Customer.customer_code)
        .join(Customer, Customer.id == RespondContactCustomer.customer_id)
        .filter(RespondContactCustomer.contact_id.in_(list(contact_ids)))
        .all()
    )
    out: dict[str, list[str]] = {}
    for contact_id, code in rows:
        out.setdefault(str(contact_id), []).append(code)
    return {k: sorted(v) for k, v in out.items()}


def get_link(db: Session, contact_id: str, customer_id: str) -> RespondContactCustomer | None:
    return (
        db.query(RespondContactCustomer)
        .filter(
            RespondContactCustomer.contact_id == contact_id,
            RespondContactCustomer.customer_id == customer_id,
        )
        .first()
    )


def get_link_by_id(db: Session, link_id: str) -> RespondContactCustomer | None:
    """One link by its own id, under the caller's scope. A malformed id is None."""
    try:
        uuid.UUID(str(link_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return db.query(RespondContactCustomer).filter(RespondContactCustomer.id == link_id).first()


def unlink_by_link_id(db: Session, link_id: str) -> bool:
    """Drop one link row by its own id. The pending action's entry point.

    A link that is gone (or that the action's scope cannot see) raises not-found, so the
    parked action ends `failed` instead of reporting a commit that changed nothing."""
    link = get_link_by_id(db, link_id)
    if link is None:
        raise handle_not_found("Customer link", link_id)
    db.delete(link)
    db.commit()
    return True


def agents_for_contact(db: Session, contact_id: str) -> list[SalesAgent]:
    """The distinct sales agents handling this contact's customers, by agent code.

    Derived off the links every time, never stored: the agent is a property of the
    customer, and a copy on the contact would drift the first time a customer moved.
    """
    return (
        db.query(SalesAgent)
        .join(Customer, Customer.sales_agent_id == SalesAgent.id)
        .join(RespondContactCustomer, RespondContactCustomer.customer_id == Customer.id)
        .filter(RespondContactCustomer.contact_id == contact_id)
        .distinct()
        .order_by(SalesAgent.sales_agent)
        .all()
    )


def link_row(link: RespondContactCustomer, customer: Customer, company_names: dict[str, str]) -> dict:
    """One link as the routes answer it: the link plus its customer, that customer's agent and company."""
    return {
        "id": link.id,
        "customer_id": customer.id,
        "customer_code": customer.customer_code,
        "customer_name": customer.customer_name,
        "is_active": bool(customer.is_active),
        "source": link.source,
        "sales_agent_id": customer.sales_agent_id,
        "sales_agent_code": customer.sales_agent_code,
        "sales_agent_name": customer.sales_agent_name,
        "company_id": customer.company_id,
        "company_name": company_names.get(str(customer.company_id)),
        "created_at": link.created_at,
    }


def contact_customers_payload(db: Session, contact_id: str) -> dict:
    """The contact card's read: the links, oldest first."""
    names = company_name_map(db)
    return {
        "data": [link_row(link, customer, names) for link, customer in links_with_customers(db, contact_id)],
    }


def links_for_customer(db: Session, customer_id: str) -> list[dict]:
    """The WhatsApp contacts linked to one customer, oldest link first.

    Read-only: the customer detail page lists them; linking happens from the contact."""
    rows = (
        db.query(RespondContactCustomer, RespondContact)
        .join(RespondContact, RespondContact.id == RespondContactCustomer.contact_id)
        .filter(RespondContactCustomer.customer_id == customer_id)
        .order_by(RespondContactCustomer.created_at, RespondContactCustomer.id)
        .all()
    )
    return [
        {
            "id": link.id,
            "contact_id": contact.id,
            "name": contact.name,
            "phone_number": contact.phone_number,
            "created_at": link.created_at,
        }
        for link, contact in rows
    ]
