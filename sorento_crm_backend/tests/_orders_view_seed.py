"""A real user holding `order_management.orders.view`, for tests of the DO read routes.

Those routes are gated on that slug (issue #1669), and the gate looks the principal up
in the database, so an override principal with a random id is answered 403. Not a test
module itself: the leading underscore keeps pytest from collecting it.
"""
from __future__ import annotations

import uuid

from app.models.user import User, UserPermission, UserRole, UserRoleAssignment, UserRolePermission

from tests._pg_fixture import unique_code

ORDERS_VIEW = "order_management.orders.view"


def user(db, stem: str = "USR") -> User:
    row = User(id=str(uuid.uuid4()), email=f"{unique_code(stem)}@test.com", name=f"ZZT {stem}", status="ACTIVE")
    db.add(row)
    db.flush()
    return row


def grant_orders_view(db, target: User) -> None:
    permission = db.query(UserPermission).filter(UserPermission.slug == ORDERS_VIEW).one_or_none()
    if permission is None:
        permission = UserPermission(id=str(uuid.uuid4()), slug=ORDERS_VIEW, name=ORDERS_VIEW)
        db.add(permission)
    role = UserRole(id=str(uuid.uuid4()), slug=unique_code("zzt_do_view").lower(), name="ZZT DO View")
    db.add(role)
    db.flush()
    db.add(UserRoleAssignment(user_id=target.id, role_id=role.id))
    db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()


def orders_view_principal(db) -> dict:
    """Seed a user with the grant and return the `current_user` dict for an override."""
    row = user(db, "DOV")
    grant_orders_view(db, row)
    return {"id": row.id, "email": row.email}
