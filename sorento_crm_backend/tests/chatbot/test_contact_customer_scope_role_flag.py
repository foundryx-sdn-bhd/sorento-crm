"""ACCESS-MODEL S2: customer scope reads the role flag (AC-AM-9).

`contact_customer_scope.is_office_staff` / `contact_customer_scope` decide "staff" from
`chatbot_roles.sees_all_customers` on a role the contact holds, no longer from an active
"(sorento|cabana|mocha) office" access type name. Access types keep brand tier and promotion
audience; they stop deciding customer scope.

Red-first: `app/models/chatbot_access.py` does not exist yet.
"""
from __future__ import annotations

from sqlalchemy import text

from tests.chatbot._access_seed import (
    give_role,
    make_contact,
    make_customer_link,
    make_role,
    rid,
)


def _office_type(db, contact_pk: str, name: str = "Sorento Office") -> None:
    code = rid("at")[:50]
    db.execute(
        text(
            "INSERT INTO contact_access_types (code, name, is_active, keywords) "
            "VALUES (:c, :n, true, '[]'::jsonb)"
        ),
        {"c": code, "n": name},
    )
    db.execute(
        text("INSERT INTO respond_contact_access_types (contact_id, access_type_code) VALUES (:c, :t)"),
        {"c": contact_pk, "t": code},
    )
    db.commit()


def test_office_access_type_without_a_sees_all_role_is_not_staff(session_factory):
    from app.services.contact_customer_scope import contact_customer_scope, is_office_staff

    db = session_factory()
    pk, _rio = make_contact(db)
    _office_type(db, pk)
    give_role(db, pk, make_role(db, rid("dealer"), domains=[], sees_all=False))
    assert is_office_staff(db, pk) is False
    assert contact_customer_scope(db, pk).staff is False


def test_office_access_type_with_no_role_at_all_is_not_staff(session_factory):
    from app.services.chatbot.access_tree import effective_access  # the one reader exists
    from app.services.contact_customer_scope import is_office_staff

    db = session_factory()
    pk, rio = make_contact(db)
    _office_type(db, pk)
    assert effective_access(db, contact_respond_id=rio, space_id=None).sees_all_customers is False
    assert is_office_staff(db, pk) is False


def test_sees_all_role_without_any_office_type_is_staff(session_factory):
    from app.services.contact_customer_scope import contact_customer_scope, is_office_staff

    db = session_factory()
    pk, _rio = make_contact(db)
    give_role(db, pk, make_role(db, rid("sales"), domains=[], sees_all=True))
    assert is_office_staff(db, pk) is True
    assert contact_customer_scope(db, pk).staff is True


def test_sees_all_role_unscopes_a_contact_that_has_linked_customers(session_factory):
    from app.services.contact_customer_scope import contact_customer_scope

    db = session_factory()
    pk, _rio = make_contact(db)
    make_customer_link(db, pk)
    give_role(db, pk, make_role(db, rid("sales"), domains=[], sees_all=True))
    scope = contact_customer_scope(db, pk)
    assert scope.staff is True
    assert scope.enforced is False


def test_linked_customers_without_the_flag_stay_scoped_even_with_an_office_type(session_factory):
    from app.services.contact_customer_scope import contact_customer_scope

    db = session_factory()
    pk, _rio = make_contact(db)
    make_customer_link(db, pk)
    _office_type(db, pk)
    give_role(db, pk, make_role(db, rid("dealer"), domains=[], sees_all=False))
    scope = contact_customer_scope(db, pk)
    assert scope.staff is False
    assert scope.enforced is True


def test_the_flag_on_any_held_role_is_enough(session_factory):
    from app.services.contact_customer_scope import is_office_staff

    db = session_factory()
    pk, _rio = make_contact(db)
    give_role(db, pk, make_role(db, rid("dealer"), domains=[], sees_all=False))
    assert is_office_staff(db, pk) is False
    give_role(db, pk, make_role(db, rid("purchasing"), domains=[], sees_all=True))
    assert is_office_staff(db, pk) is True


def test_a_contact_with_neither_links_nor_flag_is_not_enforced_but_not_staff(session_factory):
    """AC-AM-9 third clause: neither -> refused order asks. The scope exposes that as
    staff False with nothing linked; the chat lane refuses on exactly that pair."""
    from app.services.contact_customer_scope import contact_customer_scope

    db = session_factory()
    pk, _rio = make_contact(db)
    give_role(db, pk, make_role(db, rid("dealer"), domains=[], sees_all=False))
    scope = contact_customer_scope(db, pk)
    assert scope.linked == ()
    assert scope.staff is False
