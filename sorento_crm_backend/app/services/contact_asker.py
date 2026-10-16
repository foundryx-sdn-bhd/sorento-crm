"""Who is asking: the one asker rule (CA-3 owner hand test round).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, "one asker rule" and
"Ruling 10 Oct 2026 (owner override, crew)". For a WhatsApp contact the first match of:

    salesperson  an ACTIVE, not trashed CRM user is linked (`users.respond_contact_id`) and holds
                 the role with slug `salesperson`
    staff        any other ACTIVE, not trashed linked user, or an ACTIVE office access type
    salesperson  `sales_agents.contact_id` (reached only when the contact has no active user)
    dealer       a `respond_contact_customers` link
    unclassified none of the above

ONE definition, exposed twice: `sql_expr` for the chat dataset (computed at query time from the
links as they are now, so linking a contact reclassifies its history) and `classify` for the
route, which runs the same expression for one contact. `is_staff_user` (an active user holding
a staff role), not this classification, decides chatbot document access (V1-DOC-SCOPE);
`is_salesperson_user` (the role arm alone) decides whether a `salesperson` asker keeps the chat
report's relaxed scope (`chat_report.asker_of`, V1-CHATREPORT-REVOKE).
Core code: it never imports the chatbot package (tests/chatbot/test_import_boundary.py).
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

ASKERS = ("dealer", "salesperson", "staff", "unclassified")

#: The office access type names, as `contact_customer_scope._OFFICE_ACCESS_TYPE_RE` reads them.
_OFFICE_SQL = r"^(sorento|cabana|mocha) office$"


def _salesperson_user_clauses(contact_id: Any) -> list:
    """WHERE clauses (over `users`, `user_role_assignments`, `user_roles`) for an ACTIVE, not
    trashed user linked to `contact_id` that holds the non-trashed role slug `salesperson`."""
    from app.models.user import User, UserRole, UserRoleAssignment

    users, roles, assigned = User.__table__, UserRole.__table__, UserRoleAssignment.__table__
    return [
        users.c.respond_contact_id == contact_id,
        users.c.status == "ACTIVE",
        users.c.is_trashed.is_(False),
        assigned.c.user_id == users.c.id,
        assigned.c.role_id == roles.c.id,
        roles.c.slug == "salesperson",
        roles.c.is_trashed.is_(False),
    ]


def salesperson_user_name(contact_respond_id_column: Any) -> Any:
    """The name of the role-salesperson user behind a fact's contact (NULL when there is none)."""
    from app.models.access import RespondContact
    from app.models.user import User

    rc = RespondContact.__table__
    return (
        sa.select(User.__table__.c.name)
        .where(rc.c.respond_io_id == contact_respond_id_column, *_salesperson_user_clauses(rc.c.id))
        .order_by(User.__table__.c.name)
        .limit(1)
        .scalar_subquery()
    )


def _word_for(contact_id: Any) -> Any:
    """CASE over an INTERNAL `respond_contacts.id` expression."""
    from app.models.access import ContactAccessType, RespondContactCustomer, respond_contact_access_types
    from app.models.sales_agent import SalesAgent
    from app.models.user import User

    users = User.__table__
    # A live user only: a trashed or not ACTIVE user's link says nothing about who asks.
    live = [users.c.respond_contact_id == contact_id, users.c.status == "ACTIVE", users.c.is_trashed.is_(False)]
    user_linked = sa.exists().where(*live)
    salesperson_user = sa.exists().where(*_salesperson_user_clauses(contact_id))
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
        (salesperson_user, "salesperson"),
        (sa.or_(user_linked, office), "staff"),
        (agent, "salesperson"),
        (dealer, "dealer"),
        else_="unclassified",
    )


def is_staff_user(db: Session, contact_id: str) -> bool:
    """True when an ACTIVE, not trashed CRM user linked to the contact (INTERNAL
    `respond_contacts.id`) holds at least one not trashed role outside
    `user_service.NON_STAFF_ROLE_SLUGS` (portal_user, guest): the people lookups' own staff
    definition (`user_service._holds_staff_role`), whatever the role. This, not `classify`,
    decides chatbot document access (V1-DOC-SCOPE, owner ruling 16 Oct 2026): deactivating or
    trashing the user revokes it, and a dealer's portal login, a user with no live role, an
    integration (act-as) user or a `sales_agents.contact_id` link (master data) grants nothing."""
    from app.models.user import User, UserRole, UserRoleAssignment
    from app.services.user_service import NON_STAFF_ROLE_SLUGS

    users, roles, assigned = User.__table__, UserRole.__table__, UserRoleAssignment.__table__
    held = sa.exists().where(
        users.c.respond_contact_id == str(contact_id),
        users.c.status == "ACTIVE",
        users.c.is_trashed.is_(False),
        users.c.is_integration.is_(False),
        assigned.c.user_id == users.c.id,
        assigned.c.role_id == roles.c.id,
        roles.c.slug.notin_(NON_STAFF_ROLE_SLUGS),
        roles.c.is_trashed.is_(False),
    )
    return bool(db.execute(sa.select(held)).scalar())


def is_salesperson_user(db: Session, contact_id: str) -> bool:
    """True when an ACTIVE, not trashed CRM user linked to the contact (INTERNAL
    `respond_contacts.id`) holds the not trashed role `salesperson`: the role arm alone.
    `chat_report.asker_of` reads it so a `salesperson` asker keeps the chat report's relaxed
    scope only while such a user is live (V1-CHATREPORT-REVOKE): deactivating or trashing the
    user revokes it, and a `sales_agents.contact_id` link (master data) grants nothing.
    Document access is `is_staff_user`."""
    return bool(db.execute(sa.select(sa.exists().where(*_salesperson_user_clauses(str(contact_id))))).scalar())


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
