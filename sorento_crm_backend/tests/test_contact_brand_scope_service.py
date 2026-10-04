"""Phase 2 RED tests - the one reader of a contact's accessible brands (AC-1, AC-5, AC-7).

`documentation/plans/chatbot/contact-brand-scope-4oct-acceptance-criteria.md`,
`PLAN-contact-brand-scope-4oct.md` Slice 1 and the "Tests" items 1 and 2.

Imports of the new module are inside each test so one missing module is one red test.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.order import SalesOrderLine
from app.models.product import Product
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._brand_scope_seed import contact, db, world  # noqa: F401  (fixtures by name)


def _svc():
    from app.services import contact_brand_scope

    return contact_brand_scope


# --------------------------------------------------------------------- AC-1 / AC-5


def test_column_exists_and_defaults_to_null(db, world) -> None:
    """AC-1: `respond_contacts.brand_ids uuid[] NULL`; an untouched contact is unscoped."""
    udt = db.execute(
        text(
            "SELECT udt_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'respond_contacts' "
            "AND column_name = 'brand_ids'"
        )
    ).one_or_none()
    assert udt is not None, "respond_contacts.brand_ids does not exist"
    assert udt[0] == "_uuid" and udt[1] == "YES", udt
    assert db.execute(
        text("SELECT brand_ids FROM respond_contacts WHERE id = :i"), {"i": world.unscoped.id}
    ).scalar() is None


def test_null_brand_ids_is_unscoped(db, world) -> None:
    """AC-5: NULL -> None (all brands)."""
    assert _svc().contact_brand_scope(db, world.unscoped.id) is None


def test_empty_array_is_unscoped(db, world) -> None:
    """AC-5: `{}` is the same as NULL."""
    c = contact(db)
    db.execute(text("UPDATE respond_contacts SET brand_ids = '{}' WHERE id = :i"), {"i": c.id})
    db.commit()
    assert _svc().contact_brand_scope(db, c.id) is None


def test_list_gives_a_frozenset_of_string_ids(db, world) -> None:
    """AC-5: a non-empty list -> frozenset of the brand ids as strings."""
    scope = _svc().contact_brand_scope(db, world.scoped.id)
    assert isinstance(scope, frozenset)
    assert scope == frozenset({str(world.mocha.id)})


def test_two_brands_are_both_in_scope(db, world) -> None:
    c = contact(db, brand_ids=[world.mocha.id, world.sorento.id])
    db.commit()
    assert _svc().contact_brand_scope(db, c.id) == frozenset({world.mocha.id, world.sorento.id})


def test_unknown_contact_is_unscoped_not_an_error(db, world) -> None:
    """AC-5: an unknown id reads as None (the company scope already fails closed for it)."""
    assert _svc().contact_brand_scope(db, str(uuid.uuid4())) is None


def test_respond_io_id_resolves_like_the_internal_id(db, world) -> None:
    """AC-5: a Respond.io id resolves through the null-workspace fallback, as customer scope does."""
    c = contact(db, brand_ids=[world.mocha.id])
    db.execute(text("UPDATE respond_contacts SET respond_io_id = 'zzt-rid-1' WHERE id = :i"), {"i": c.id})
    db.commit()
    assert _svc().contact_brand_scope(db, "zzt-rid-1") == frozenset({world.mocha.id})


# --------------------------------------------------------------------- AC-7 output guard helper


def test_out_of_scope_codes_names_other_brand_and_null_brand(db, world) -> None:
    """AC-7 / Q1: a code whose brand is not in scope, or NULL, is out of scope; an
    in-scope code and a code that does not exist are left alone."""
    scope = frozenset({world.mocha.id})
    codes = [*world.codes.values(), "ZZT-NO-SUCH-CODE"]
    out = _svc().out_of_scope_product_codes(db, scope, codes)
    assert out == {world.codes["sorento"], world.codes["null"]}


def test_out_of_scope_codes_with_two_brands_in_scope(db, world) -> None:
    scope = frozenset({world.mocha.id, world.sorento.id})
    out = _svc().out_of_scope_product_codes(db, scope, list(world.codes.values()))
    assert out == {world.codes["null"]}


def test_brand_predicate_fails_a_null_brand(db, world) -> None:
    """Q1: `brand_col IN scope` never matches NULL."""
    pred = _svc().brand_predicate(frozenset({world.mocha.id}), Product.brand_id)
    ids = {
        p.product_code
        for p in db.query(Product).filter(Product.product_code.in_(list(world.codes.values()))).filter(pred)
    }
    assert ids == {world.codes["mocha"]}


# --------------------------------------------------------------------- session criterion (test 2)


def _codes_of(db, world) -> set[str]:
    return {
        p.product_code
        for p in db.query(Product).filter(Product.product_code.in_(list(world.codes.values()))).all()
    }


@pytest.fixture
def stamped():
    """`set_brand_scope`, imported lazily so a missing helper is a red test, not a fixture error."""

    def _stamp(db, scope) -> None:
        from app.models.base import set_brand_scope

        set_brand_scope(db, scope)

    return _stamp


def test_session_with_scope_sees_only_in_scope_products(db, world, stamped) -> None:
    """AC-7: `db.query(Product)` on a scoped session returns MOCHA only (NULL brand out)."""
    stamped(db, frozenset({world.mocha.id}))
    assert _codes_of(db, world) == {world.codes["mocha"]}


def test_session_without_scope_sees_all_three(db, world, stamped) -> None:
    """AC-6: no scope stamped (or None) -> all three, byte-identical to today."""
    assert _codes_of(db, world) == set(world.codes.values())
    stamped(db, None)
    assert _codes_of(db, world) == set(world.codes.values())


def test_get_brand_scope_round_trips_and_defaults_to_none(db, world, stamped) -> None:
    from app.models.base import get_brand_scope

    assert get_brand_scope(db) is None
    stamped(db, frozenset({world.mocha.id}))
    assert get_brand_scope(db) == frozenset({world.mocha.id})
    assert db.info["brand_scope"] == frozenset({world.mocha.id})


def test_join_from_a_line_table_to_product_is_filtered(db, world, stamped) -> None:
    """AC-7 / AC-12: a join from the SO line table to Product drops the SORENTO and the
    NULL-brand lines on a scoped session, and returns all of them without a scope."""
    ids = [world.mixed.id, world.sorento_only.id, world.null_only.id]

    def lines() -> int:
        return (
            db.query(SalesOrderLine)
            .join(Product, Product.id == SalesOrderLine.product_id)
            .filter(SalesOrderLine.sales_order_id.in_(ids))
            .count()
        )

    assert lines() == 4
    stamped(db, frozenset({world.mocha.id}))
    assert lines() == 1


def test_scope_applies_to_aliased_product(db, world, stamped) -> None:
    """The criterion is `include_aliases=True`: an aliased Product is filtered too."""
    from sqlalchemy.orm import aliased

    stamped(db, frozenset({world.mocha.id}))
    alias = aliased(Product)
    got = {
        r.product_code
        for r in db.query(alias).filter(alias.product_code.in_(list(world.codes.values()))).all()
    }
    assert got == {world.codes["mocha"]}


def test_company_scope_still_applies_alongside_brand_scope(db, world, stamped) -> None:
    """The brand criterion adds to the company criterion, it does not replace it."""
    stamped(db, frozenset({world.mocha.id}))
    set_company_scope(db, frozenset({str(uuid.uuid4())}))
    assert _codes_of(db, world) == set()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    assert _codes_of(db, world) == {world.codes["mocha"]}
