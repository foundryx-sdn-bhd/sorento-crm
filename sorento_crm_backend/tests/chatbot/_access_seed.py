"""Seed helpers for the ACCESS-MODEL tests (lane ACCESS-MODEL, red-first, 2 Oct 2026).

Every helper seeds its own chain in the blank Postgres schema (`session_factory`); nothing
borrows an existing row. The new tables (`app/models/chatbot_access.py`) are imported INSIDE
each helper so a missing module fails the test with ImportError at the point of use, never
at collection of an unrelated file.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

SPACE_ID = "acm-space-1"


def rid(stem: str = "x") -> str:
    return f"ZZT-{stem}-{uuid.uuid4().hex[:8]}"


def make_workspace(db, space_id: str = SPACE_ID) -> str:
    wid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, api_key_ciphertext) "
            "VALUES (:id, :space_id, 'x')"
        ),
        {"id": wid, "space_id": space_id},
    )
    db.commit()
    return wid


def make_contact(
    db, *, respond_io_id: str | None = None, workspace_id: str | None = None, name: str | None = None
) -> tuple[str, str]:
    """Returns (respond_contacts.id, respond_io_id)."""
    cid = respond_io_id or rid("rio")
    pk = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, workspace_id, session_vars) "
            "VALUES (:id, :cid, :phone, :name, :wid, CAST(:sv AS jsonb))"
        ),
        {
            "id": pk,
            "cid": cid,
            "phone": f"+6012{uuid.uuid4().hex[:9]}",
            "name": name or rid("name"),
            "wid": workspace_id,
            "sv": json.dumps({}),
        },
    )
    db.commit()
    return pk, cid


def make_domain(
    db,
    name: str,
    *,
    reveal_key: str | None = None,
    supported: bool = True,
    escalation_team_code: str | None = None,
    access_section: str | None = None,
    sort_order: int = 0,
):
    from app.models.chatbot_policy import ChatbotDomain

    row = db.query(ChatbotDomain).filter(ChatbotDomain.name == name).first()
    if row is not None:
        return row
    row = ChatbotDomain(
        name=name,
        label=name.replace("_", " ").title(),
        reveal_key=reveal_key,
        supported=supported,
        escalation_team_code=escalation_team_code,
        access_section=access_section,
        sort_order=sort_order,
    )
    db.add(row)
    db.commit()
    return row


def make_field(db, domain: str, key: str, *, kind: str = "field", label: str | None = None):
    from app.models.chatbot_access import ChatbotDomainField

    row = db.query(ChatbotDomainField).filter(ChatbotDomainField.key == key).first()
    if row is not None:
        return row
    row = ChatbotDomainField(
        domain_name=domain, key=key, label=label or key, kind=kind, sort_order=0
    )
    db.add(row)
    db.commit()
    return row


def make_role(
    db,
    code: str,
    *,
    domains: list[str] | None = None,
    fields: list[str] | None = None,
    sees_all: bool = False,
    name: str | None = None,
) -> str:
    from app.models.chatbot_access import ChatbotRole, ChatbotRoleDomain, ChatbotRoleField

    role = ChatbotRole(
        code=code,
        name=name or code,
        description=None,
        sees_all_customers=sees_all,
        sort_order=0,
    )
    db.add(role)
    db.flush()
    for d in domains or []:
        db.add(ChatbotRoleDomain(role_id=role.id, domain_name=d))
    for f in fields or []:
        db.add(ChatbotRoleField(role_id=role.id, field_key=f))
    db.commit()
    return role.id


def give_role(db, contact_pk: str, role_id: str) -> None:
    from app.models.chatbot_access import ContactChatbotRole

    db.add(ContactChatbotRole(contact_id=contact_pk, role_id=role_id))
    db.commit()


def override(
    db, contact_pk: str, domain: str, *, field_key: str | None = None, granted: bool = True
) -> None:
    from app.models.chatbot_access import ContactAccessOverride

    db.add(
        ContactAccessOverride(
            contact_id=contact_pk, domain_name=domain, field_key=field_key, granted=granted
        )
    )
    db.commit()


def make_customer_link(db, contact_pk: str, name: str = "ZZT Customer") -> str:
    from app.models.order import Customer
    from app.services.company_scope import DEFAULT_COMPANY_ID

    customer = Customer(
        customer_code=rid("cust"),
        customer_name=name,
        is_active=True,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(customer)
    db.flush()
    db.execute(
        text(
            "INSERT INTO respond_contact_customers "
            "(id, contact_id, customer_id, is_primary, source, company_id) "
            "VALUES (gen_random_uuid(), :cid, :cust, true, 'manual', :company_id)"
        ),
        {"cid": contact_pk, "cust": customer.id, "company_id": DEFAULT_COMPANY_ID},
    )
    db.commit()
    return customer.id
