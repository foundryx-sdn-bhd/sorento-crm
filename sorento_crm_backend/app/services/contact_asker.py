"""Who is asking: the one asker rule (CA-3 owner hand test round).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, "one asker rule".
For a WhatsApp contact the first match of:

    staff        a CRM user is linked (`users.respond_contact_id`), or an ACTIVE office access
                 type (the `contact_customer_scope.is_office_staff` test)
    salesperson  `sales_agents.contact_id`
    dealer       a `respond_contact_customers` link
    unclassified none of the above

ONE definition, exposed twice: `sql_expr` for the chat dataset (computed at query time from the
links as they are now, so linking a contact reclassifies its history) and `classify` for the
route, which runs the same expression for one contact. Core code: it never imports the chatbot
package (tests/chatbot/test_import_boundary.py).
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

ASKERS = ("dealer", "salesperson", "staff", "unclassified")

#: The office access type names, as `contact_customer_scope._OFFICE_ACCESS_TYPE_RE` reads them.
_OFFICE_SQL = r"^(sorento|cabana|mocha) office$"


def _word_for(contact_id: Any) -> Any:
    """CASE over an INTERNAL `respond_contacts.id` expression."""
    from app.models.access import ContactAccessType, RespondContactCustomer, respond_contact_access_types
    from app.models.sales_agent import SalesAgent
    from app.models.user import User

    user_linked = sa.exists().where(User.__table__.c.respond_contact_id == contact_id)
    kinds = ContactAccessType.__table__
    held = respond_contact_access_types
    office = sa.exists().where(
        sa.and_(
            held.c.contact_id == contact_id,
            held.c.access_type_code == kinds.c.code,
            kinds.c.is_active.is_(True),
            sa.func.lower(
                sa.func.regexp_replace(sa.func.trim(sa.func.coalesce(kinds.c.name, "")), r"\s+", " ", "g")
            ).op("~")(_OFFICE_SQL),
        )
    )
    agent = sa.exists().where(SalesAgent.__table__.c.contact_id == contact_id)
    dealer = sa.exists().where(RespondContactCustomer.__table__.c.contact_id == contact_id)
    return sa.case(
        (sa.or_(user_linked, office), "staff"),
        (agent, "salesperson"),
        (dealer, "dealer"),
        else_="unclassified",
    )


def classify(db: Session, contact_id: str) -> str:
    """The asker word of one contact (INTERNAL `respond_contacts.id`)."""
    return db.execute(sa.select(_word_for(sa.literal(str(contact_id))))).scalar_one()


def sql_expr(contact_respond_id_column: Any) -> Any:
    """A scalar SQL expression over a fact's `contact_respond_id`: the contact's asker word,
    `unclassified` when no `respond_contacts` row carries that Respond.io id."""
    from app.models.access import RespondContact

    rc = RespondContact.__table__
    inner = (
        sa.select(_word_for(rc.c.id))
        .where(rc.c.respond_io_id == contact_respond_id_column)
        .limit(1)
        .scalar_subquery()
    )
    return sa.func.coalesce(inner, "unclassified")
