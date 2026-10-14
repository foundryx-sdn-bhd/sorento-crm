"""V1-CHATREPORT-REVOKE: deactivating or trashing a salesperson's CRM user revokes the chat
report's unscoped fetch.

`chat_report.asker_of` classified through `contact_asker.classify`, whose third arm makes any
contact with a `sales_agents.contact_id` link a salesperson once no ACTIVE user is linked. So a
contact whose user was set INACTIVE or trashed still classified `salesperson`, and `scope_for`
relaxed its chat-report fetch. The salesperson class now also needs the active-role-salesperson
check (`contact_asker.is_salesperson_user`): active, not trashed, role `salesperson`, linked
through `users.respond_contact_id`. Active salespeople and dealers are unchanged.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.services.chatbot.lanes.business import chat_report
from tests.chatbot.test_customer_scope_lane import _link_customers
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_outstanding_lane import _SPACE_ID, _seed_contact

STRICT = {"ids": ["c1"], "enforced": True}


def _contact_id(db) -> str:
    return db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
    ).scalar_one()


def _salesperson_user(session_factory, *, status: str = "ACTIVE", trashed: bool = False) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db = session_factory()
    try:
        role = db.query(UserRole).filter_by(slug="salesperson").first()
        if role is None:
            role = UserRole(id=str(uuid.uuid4()), slug="salesperson", name=f"ZZT Salesperson {uuid.uuid4().hex[:6]}")
            db.add(role)
            db.flush()
        user_id = str(uuid.uuid4())
        db.add(User(id=user_id, email=f"zzt-{user_id[:8]}@zzt.test", name="ZZT Salesperson",
                    status=status, is_trashed=trashed, respond_contact_id=_contact_id(db)))
        db.flush()
        db.add(UserRoleAssignment(id=str(uuid.uuid4()), user_id=user_id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _sales_agent_link(session_factory) -> None:
    from app.models.sales_agent import SalesAgent

    db = session_factory()
    try:
        db.add(SalesAgent(sales_agent=f"ZZT{uuid.uuid4().hex[:6]}", contact_id=_contact_id(db)))
        db.commit()
    finally:
        db.close()


def _asker_and_scope(session_factory) -> tuple[str | None, dict]:
    db = session_factory()
    try:
        asker = chat_report.asker_of(db, CONTACT_ID, _SPACE_ID)
    finally:
        db.close()
    return asker, chat_report.scope_for(chat_report.DOMAIN, STRICT, asker=asker, granted=True)


def test_inactive_salesperson_user_with_agent_link_is_scoped(session_factory):
    _seed_contact(session_factory, variables={})
    _salesperson_user(session_factory, status="INACTIVE")
    _sales_agent_link(session_factory)

    asker, scope = _asker_and_scope(session_factory)

    assert asker != "salesperson"
    assert scope["enforced"] is True


def test_trashed_salesperson_user_with_agent_link_is_scoped(session_factory):
    _seed_contact(session_factory, variables={})
    _salesperson_user(session_factory, trashed=True)
    _sales_agent_link(session_factory)

    asker, scope = _asker_and_scope(session_factory)

    assert asker != "salesperson"
    assert scope["enforced"] is True


def test_active_role_salesperson_is_unchanged(session_factory):
    _seed_contact(session_factory, variables={})
    _salesperson_user(session_factory)
    _sales_agent_link(session_factory)

    asker, scope = _asker_and_scope(session_factory)

    assert asker == "salesperson"
    assert scope["enforced"] is False


def test_dealer_is_unchanged(session_factory):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT REVOKE DEALER SDN BHD")

    asker, scope = _asker_and_scope(session_factory)

    assert asker == "dealer"
    assert scope["enforced"] is True
