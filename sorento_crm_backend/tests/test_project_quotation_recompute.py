"""S19 - recompute the guardrails on a version against TODAY's master data.

The client refused a bulk migration and asked for a button: "I need to have a recompute
button rather than you go and bulk write the data ... in case someone change at the master
data (product or any configuration), then the quotation can refresh to repull this".

They are right, and the reason is in the data. Both alerts are computed on line WRITE and
STORED on the line (AC-E7), which is correct for a quotation the customer holds and wrong
for one still being priced: the flags on a draft go stale the moment a series or a floor
moves, and nothing re-asks the question. So the fix has to be repeatable rather than a
one-off correction.

Three properties are pinned here:

1. It CORRECTS, in both directions - a line that is no longer non-standard loses the flag,
   and a line that has just fallen below a new floor gains one.
2. It REPORTS what moved. "6 lines are no longer non-standard" is the answer; a silent
   success toast is not.
3. It never touches a FROZEN or ISSUED version. Those flags are what was true when the
   customer was sent the paper, and rewriting them would rewrite quoted history.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.projects import ProjectSeries
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-recompute"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=f"{MARKER} Ali"))
    db.flush()
    return user_id


def _uom(db) -> str:
    row = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add(row)
    db.flush()
    return row.id


def _category(db, name: str, parent_id=None) -> ProductCategory:
    row = ProductCategory(
        id=_uid(),
        category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} {name}",
        parent_category_id=parent_id,
    )
    db.add(row)
    db.flush()
    return row


def _product(db, category_id, uom_id: str, list_price: str = "1000.00") -> Product:
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} Basin",
        description="Wall-hung basin, white",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=Decimal(list_price),
    )
    db.add(row)
    db.flush()
    return row


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tower {_uid()[:12]}",
    )


def _scope(db, company_id: str, owner: str, series_id=None):
    """A project with one quotation scope and its version 1 open."""
    from app.services import project_quotation_service as quotes

    project = _project(db, company_id, owner)
    quotation = quotes.create_quotation(
        db,
        project=project,
        actor_user_id=owner,
        payload={"scope_label": "House Units", "series_id": series_id},
    )
    return quotation, quotes.current_version(db, quotation.id)


# ------------------------------------------------------------------- the fix


def test_a_line_flagged_against_a_series_that_no_longer_applies_is_cleared():
    """The live defect in one test: 46 lines carry ``is_non_standard`` judged against a
    series their quotation no longer nominates, and nothing re-asks on read."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )
        # Stale by hand, exactly as the live rows are: flagged once, never re-asked.
        line.is_non_standard = True
        db.flush()

        report = quotes.recompute_version(db, version=version)

        assert line.is_non_standard is False
        assert report["no_longer_non_standard"] == 1
        assert report["changed_count"] == 1
        assert report["line_count"] == 1


def test_a_product_removed_from_the_series_becomes_non_standard_on_recompute():
    """The other direction. Master data moved AFTER the line was priced, and pressing
    recompute is what makes the quotation say so."""
    from app.services import project_pricing_service as pricing
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        series = ProjectSeries(id=_uid(), company_id=company_id, name=f"{MARKER} Premium")
        db.add(series)
        db.flush()
        # A category the product is not under, so removing the name leaves it outside.
        pricing.set_series_categories(
            db, series=series, category_ids=[_category(db, "Elsewhere").id]
        )
        pricing.set_series_products(db, series=series, product_ids=[product.id])

        quotation, version = _scope(db, company_id, owner, series_id=series.id)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )
        assert line.is_non_standard is False

        # The admin takes it off the list.
        pricing.set_series_products(db, series=series, product_ids=[])

        report = quotes.recompute_version(db, version=version)

        assert line.is_non_standard is True
        assert report["now_non_standard"] == 1
        assert report["no_longer_non_standard"] == 0


def test_a_floor_set_after_the_line_was_priced_is_picked_up():
    from app.services import project_pricing_service as pricing
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        category = _category(db, "Basins")
        product = _product(db, category.id, uom, "1000.00")
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "500.00", "quantity": 1},
        )
        assert line.is_below_floor is False

        pricing.upsert_floor_rule(
            db,
            company_id=company_id,
            payload={"mode": "percent", "value": "90", "category_id": category.id},
        )

        report = quotes.recompute_version(db, version=version)

        assert line.is_below_floor is True
        assert line.floor_value_applied == Decimal("900.00")
        assert report["now_below_floor"] == 1


def test_a_floor_that_has_been_lifted_clears_the_breach():
    from app.services import project_pricing_service as pricing
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        category = _category(db, "Basins")
        product = _product(db, category.id, uom, "1000.00")
        rule = pricing.upsert_floor_rule(
            db,
            company_id=company_id,
            payload={"mode": "percent", "value": "90", "category_id": category.id},
        )
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "500.00", "quantity": 1},
        )
        assert line.is_below_floor is True

        pricing.delete_floor_rule(db, rule)

        report = quotes.recompute_version(db, version=version)

        assert line.is_below_floor is False
        assert line.floor_value_applied is None
        assert report["no_longer_below_floor"] == 1


def test_nothing_changed_is_reported_as_nothing_changed():
    """The commonest outcome, and the one a silent success toast hides. A reader has to be
    able to tell "I checked and it is already right" from "I pressed a button"."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )

        report = quotes.recompute_version(db, version=version)

        assert report["changed_count"] == 0
        assert report["line_count"] == 1
        assert report["changed_lines"] == []


def test_the_lines_that_moved_are_named_by_their_product_code():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )
        line.is_non_standard = True
        db.flush()

        report = quotes.recompute_version(db, version=version)

        assert report["changed_lines"] == [product.product_code]


def test_an_off_catalog_line_stays_non_standard_and_is_not_reported_as_a_change():
    """It is not stale, it is correct: "we quoted something that is not in our catalogue"
    is the case the alert exists for. Re-confirming it must not appear as news."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "description_snapshot": "Bespoke stone counter",
                "unit_price": "4000.00",
                "quantity": 1,
            },
        )
        assert line.is_non_standard is True

        report = quotes.recompute_version(db, version=version)

        assert line.is_non_standard is True
        assert report["changed_count"] == 0


def test_a_line_already_below_its_floor_does_not_raise_a_second_breach_event():
    """``_apply_guardrails`` emits on the TRANSITION only (AC-E6a), and recompute must
    not turn that into an alert per press."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        category = _category(db, "Basins")
        product = _product(db, category.id, uom, "1000.00")
        from app.services import project_pricing_service as pricing

        pricing.upsert_floor_rule(
            db,
            company_id=company_id,
            payload={"mode": "percent", "value": "90", "category_id": category.id},
        )
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "500.00", "quantity": 1},
        )
        # The breach raised on the WRITE is the one management was told about.
        assert len(quotes.pop_breach_events(line)) == 1

        first = quotes.recompute_version(db, version=version)
        second = quotes.recompute_version(db, version=version)

        assert first["breach_events"] == []
        assert second["breach_events"] == []
        assert first["changed_count"] == 0


def test_a_newly_breached_line_does_raise_one_breach_event():
    from app.services import project_pricing_service as pricing
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        category = _category(db, "Basins")
        product = _product(db, category.id, uom, "1000.00")
        quotation, version = _scope(db, company_id, owner)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "500.00", "quantity": 1},
        )
        pricing.upsert_floor_rule(
            db,
            company_id=company_id,
            payload={"mode": "percent", "value": "90", "category_id": category.id},
        )

        report = quotes.recompute_version(db, version=version)

        assert len(report["breach_events"]) == 1
        assert report["breach_events"][0]["floor_value"] == Decimal("900.00")


def test_a_frozen_version_refuses_to_be_recomputed():
    """Its flags are what was true when the customer was sent the paper."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )
        quotes.revise(db, quotation=quotation, actor_user_id=owner)

        with pytest.raises(AppException) as exc:
            quotes.recompute_version(db, version=version)

        assert exc.value.status_code == 422


def test_the_version_total_is_untouched_by_a_recompute():
    """It re-asks the guardrails and nothing else. A recompute that moved money would be
    a re-price, which is not what the button says."""
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 4},
        )
        before = version.total_amount

        quotes.recompute_version(db, version=version)

        assert version.total_amount == before == Decimal("3600.00")


def test_a_line_naming_a_product_this_company_cannot_see_is_counted_and_named():
    """The live case, and the reason the report needed this field.

    46 lines of one quotation point at another company's identically-coded product row.
    Under the acting company's scope that product does not exist, so the line reads as
    off-catalog and stays non-standard - which is exactly what a re-SAVE would compute, so
    the flags are not stale and nothing changes. Reported plainly, because "nothing
    changed" over 46 unreadable products is true and useless.
    """
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db)
        uom = _uom(db)
        product = _product(db, _category(db, "Basins").id, uom)
        quotation, version = _scope(db, company_id, owner)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "900.00", "quantity": 1},
        )
        # The product goes out of reach without the line being touched.
        db.delete(product)
        db.flush()

        report = quotes.recompute_version(db, version=version)

        assert report["unresolved_products"] == 1
        assert line.is_non_standard is True
