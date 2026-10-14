"""SET-QUOTE-LINE: a quotation line may name a product SET instead of a product.

Written BEFORE the implementation (Phase 2, test-first). Every test is red until the slice lands.
Source of truth: documentation/plans/project-sales/set-quote-line-14oct-acceptance-criteria.md.
Each test names the acceptance criterion it pins.

Owner decisions the tests encode:

- the parts of the set are FROZEN on the line (`set_parts_snapshot`) and not shown on screen;
  the parts text goes in `complete_set` (A2b);
- a series prices a set in its own table, `projects.series_sets` (C1);
- when the series is silent the floor is the company DEFAULT (system) rule only, applied to the
  set list price: never a product rule, never a category rule (B3, Q4 b);
- the SO draft splits a set line into its parts from the frozen copy (E1, E2).

New symbols are imported INSIDE each test so every test fails on its own, with its own message,
instead of one collection error taking the whole file down.

Postgres only, via ``blank_session``. Every chain is seeded here (company is the seeded Sorento,
everything else is created), never read from existing rows.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.numbering import DocumentNumberingRule
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.product_set import ProductSet, ProductSetMember
from app.models.projects import (
    PriceFloorRule,
    ProjectQuotationLine,
    ProjectSeries,
)
from app.models.resources import Attachment
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-setq"
BASE = "/api/v1/project-sales"
SIGNATURE_DATA_URI = "data:image/png;base64,zzt"


# ------------------------------------------------------------------ builders


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _user(db, name: str = f"{MARKER} Baser") -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _catalogue(db):
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} Sanitary"
    )
    db.add_all([uom, category])
    db.flush()
    return category, uom


def _product(db, category, uom, code: str, list_price: str = "200.00") -> Product:
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        description=f"{MARKER} {code} description",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal(list_price),
    )
    db.add(row)
    db.flush()
    return row


def _set(
    db,
    company_id: str,
    category,
    uom,
    *,
    code: str | None = None,
    name: str | None = None,
    parts=None,
    override: str | None = None,
    active: bool = True,
) -> ProductSet:
    """A set of three: shared cistern FIRST (sort_order 0), pedestal (ticked), seat cover (qty 2).

    The cistern also sits in a second, decoy set, so it belongs to two sets and the pedestal
    (one set) is the driver by `driver_member`'s rule even though it is NOT member index 0.
    """
    token = _uid()[:6].upper()
    code = code or f"ZZTSET{token}"
    row = ProductSet(
        id=_uid(),
        company_id=company_id,
        set_code=code,
        name=name or f"{MARKER} Water closet set {token}",
        list_price_override=Decimal(override) if override is not None else None,
        is_active=active,
    )
    db.add(row)
    db.flush()
    if parts is None:
        pedestal = _product(db, category, uom, f"{code}-RL", "200.00")
        cistern = _product(db, category, uom, f"{code}-CY", "120.00")
        seat = _product(db, category, uom, f"{code}-SC", "40.00")
        decoy = ProductSet(
            id=_uid(),
            company_id=company_id,
            set_code=f"ZZTDECOY{token}",
            name=f"{MARKER} decoy {token}",
            is_active=True,
        )
        db.add(decoy)
        db.flush()
        db.add(
            ProductSetMember(
                id=_uid(),
                product_set_id=decoy.id,
                product_id=cistern.id,
                quantity=Decimal("1"),
                contributes_to_price=False,
                sort_order=0,
            )
        )
        parts = [(cistern, "1", False), (pedestal, "1", True), (seat, "2", False)]
    for order, (product, qty, ticked) in enumerate(parts):
        db.add(
            ProductSetMember(
                id=_uid(),
                product_set_id=row.id,
                product_id=product.id,
                quantity=Decimal(qty),
                contributes_to_price=ticked,
                sort_order=order,
            )
        )
    db.flush()
    db.refresh(row)
    return row


def _foreign_set(db, *, code: str, active: bool = True) -> str:
    """A set owned by ANOTHER company, by raw SQL so the scope layer cannot stamp it ours."""
    other = _uid()
    db.execute(
        text(
            "INSERT INTO companies (id, code, name, is_active) "
            "VALUES (:id, :code, :name, true)"
        ),
        {"id": other, "code": f"ZZT{_uid()[:5]}", "name": f"{MARKER} Mocha"},
    )
    set_id = _uid()
    db.execute(
        text(
            "INSERT INTO product_sets (id, company_id, set_code, name, is_active) "
            "VALUES (:id, :company, :code, :name, :active)"
        ),
        {
            "id": set_id,
            "company": other,
            "code": code,
            "name": f"{MARKER} their set",
            "active": active,
        },
    )
    return set_id


def _scope(db, company_id: str, owner: str, series_id=None):
    from app.services import project_quotation_service as quotes
    from app.services.project_service import register_project

    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tower {_uid()[:12]}",
    )
    quotation = quotes.create_quotation(
        db,
        project=project,
        actor_user_id=owner,
        payload={"scope_label": "House Units", "series_id": series_id},
    )
    return quotation, quotes.current_version(db, quotation.id)


def _series(db, company_id: str) -> ProjectSeries:
    row = ProjectSeries(id=_uid(), company_id=company_id, name=f"{MARKER} Series {_uid()[:6]}")
    db.add(row)
    db.flush()
    return row


def _series_row(db, series, product_set, *, price=None, pct=None):
    """A `projects.series_sets` row, written through the ORM class the slice introduces."""
    from app.models.projects import ProjectSeriesSet

    row = ProjectSeriesSet(
        series_id=series.id,
        product_set_id=product_set.id,
        selling_price=Decimal(price) if price is not None else None,
        max_discount_pct=Decimal(pct) if pct is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def _system_rule(db, company_id: str, mode: str, value: str) -> None:
    db.add(PriceFloorRule(id=_uid(), company_id=company_id, mode=mode, value=Decimal(value)))
    db.flush()


def _env(db, *, series_id=None):
    company_id = _sorento(db)
    project_seed_service.run(db, company_id=company_id)
    owner = _user(db)
    category, uom = _catalogue(db)
    quotation, version = _scope(db, company_id, owner, series_id=series_id)
    return {
        "company_id": company_id,
        "owner": owner,
        "category": category,
        "uom": uom,
        "quotation": quotation,
        "version": version,
    }


def _save(db, env, payload: dict, line=None):
    from app.services import project_quotation_service as quotes

    return quotes.upsert_line(
        db,
        version=env["version"],
        actor_user_id=env["owner"],
        payload=payload,
        line=line,
    )


def _set_line(db, env, product_set=None, **extra):
    """Save a set line. The set is the argument, `product_set_id=` or the company's only set."""
    set_id = extra.pop("product_set_id", None) or (product_set.id if product_set else None)
    if set_id is None:
        set_id = (
            db.query(ProductSet)
            .filter(
                ProductSet.company_id == env["company_id"],
                ~ProductSet.set_code.like("ZZTDECOY%"),
            )
            .one()
            .id
        )
    body = {"product_set_id": set_id, "unit_price": "900.00", "quantity": "10"}
    body.update(extra)
    return _save(db, env, body)


def _codes(product_set) -> list[str]:
    return [m.product.product_code for m in product_set.members]


# ------------------------------------------------------------------ A1


def test_a1_a_line_naming_both_a_product_and_a_set_is_refused_422():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        product = _product(db, env["category"], env["uom"], "ZZT-ALONE")

        with pytest.raises(AppException) as refused:
            _save(
                db,
                env,
                {
                    "product_id": product.id,
                    "product_set_id": product_set.id,
                    "unit_price": "100.00",
                    "quantity": "1",
                },
            )
        assert refused.value.status_code == 422
        assert refused.value.detail["code"] == "quotation_line_one_target"


def test_a1_the_database_refuses_a_line_with_both_a_product_and_a_set():
    """The CHECK is the backstop for any path that skips the service."""
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        product = _product(db, env["category"], env["uom"], "ZZT-CHECK")

        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(
                    ProjectQuotationLine(
                        id=_uid(),
                        company_id=env["company_id"],
                        version_id=env["version"].id,
                        product_id=product.id,
                        product_set_id=product_set.id,
                        product_code_snapshot="X",
                        description_snapshot="X",
                        unit_price=Decimal("1"),
                        quantity=Decimal("1"),
                        line_total=Decimal("1"),
                    )
                )
                db.flush()


# ------------------------------------------------------------------ A2 / A3


def test_a2_saving_a_set_line_freezes_code_name_price_and_parts():
    from app.services import product_set_service

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])

        line = _set_line(db, env)
        db.flush()

        assert line.product_id is None
        assert line.product_set_id == product_set.id
        assert line.product_code_snapshot == product_set.set_code
        assert line.description_snapshot == product_set.name
        assert line.list_price_snapshot == product_set_service.resolve_set_price(
            product_set
        ).resolved
        # pedestal 200 x 1 is the only ticked member
        assert Decimal(line.list_price_snapshot) == Decimal("200.00")

        parts = line.set_parts_snapshot
        assert [p["product_code"] for p in parts] == _codes(product_set)
        assert [p["quantity"] for p in parts] == ["1", "1", "2"] or [
            Decimal(p["quantity"]) for p in parts
        ] == [Decimal("1"), Decimal("1"), Decimal("2")]
        assert all(isinstance(p["quantity"], str) for p in parts)
        assert [p["is_driver"] for p in parts] == [False, True, False]
        member_products = {m.product.product_code: m.product_id for m in product_set.members}
        for part in parts:
            assert part["product_id"] == member_products[part["product_code"]]
            assert part["description"]
        assert set(parts[0]) >= {
            "product_id",
            "product_code",
            "description",
            "quantity",
            "is_driver",
        }


def test_a2_a_typed_description_is_kept_over_the_set_name():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])

        line = _set_line(db, env, description_snapshot=f"{MARKER} the customer's wording")

        assert line.product_set_id == product_set.id
        assert line.description_snapshot == f"{MARKER} the customer's wording"


def test_a2_the_list_price_is_the_override_when_set_and_empty_when_there_is_no_basis():
    with blank_session() as db:
        env = _env(db)
        priced = _set(db, env["company_id"], env["category"], env["uom"], override="1180.00")
        loose = _product(db, env["category"], env["uom"], "ZZT-NOTICK")
        unpriced = _set(
            db,
            env["company_id"],
            env["category"],
            env["uom"],
            parts=[(loose, "1", False)],
        )

        with_override = _set_line(db, env, **{"product_set_id": priced.id})
        without_basis = _set_line(db, env, **{"product_set_id": unpriced.id})

        assert Decimal(with_override.list_price_snapshot) == Decimal("1180.00")
        assert without_basis.list_price_snapshot is None


def test_a3_uom_defaults_to_set_and_a_given_uom_is_kept():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])

        plain = _set_line(db, env)
        given = _set_line(db, env, uom="NOS")

        assert plain.uom == "SET"
        assert given.uom == "NOS"
        assert given.product_set_id == product_set.id


# ------------------------------------------------------------------ A2b


def test_a2b_saving_a_set_line_with_no_complete_set_fills_the_part_codes():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        cistern, pedestal, seat = _codes(product_set)

        line = _set_line(db, env)

        # the seat cover is two per set, so it reads "2 x CODE"
        assert line.complete_set == f"{cistern} + {pedestal} + 2 x {seat}"


def test_a2b_a_complete_set_sent_with_a_set_pick_is_replaced_by_the_parts():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])

        line = _set_line(db, env, complete_set="c/w soft close seat")

        expected = " + ".join(
            m.product.product_code
            if Decimal(m.quantity) == 1
            else f"{Decimal(m.quantity).normalize():f} x {m.product.product_code}"
            for m in sorted(product_set.members, key=lambda m: m.sort_order or 0)
        )[:100]
        assert line.complete_set == expected


def test_a2b_the_text_is_cut_at_the_columns_100_characters():
    with blank_session() as db:
        env = _env(db)
        parts = [
            (_product(db, env["category"], env["uom"], f"ZZT-LONG-PART-CODE-{n}-{'X' * 12}"), "1", n == 0)
            for n in range(5)
        ]
        product_set = _set(db, env["company_id"], env["category"], env["uom"], parts=parts)
        full = " + ".join(m.product.product_code for m in product_set.members)
        assert len(full) > 100

        line = _set_line(db, env)

        assert len(line.complete_set) == 100
        assert line.complete_set == full[:100]


# ------------------------------------------------------------------ A4


def test_a4_a_set_that_does_not_exist_or_is_another_companys_is_refused_as_not_found():
    with blank_session() as db:
        env = _env(db)
        theirs = _foreign_set(db, code=f"ZZTFOREIGN{_uid()[:4]}")

        for set_id in (theirs, _uid()):
            with pytest.raises(AppException) as refused:
                _save(
                    db,
                    env,
                    {"product_set_id": set_id, "unit_price": "100.00", "quantity": "1"},
                )
            assert refused.value.status_code == 404
            assert refused.value.detail["code"] == "product_set_not_found"


def test_a4_a_new_pick_of_an_inactive_set_is_refused_but_an_existing_line_still_saves():
    with blank_session() as db:
        env = _env(db)
        retired = _set(db, env["company_id"], env["category"], env["uom"], active=False)

        with pytest.raises(AppException) as refused:
            _set_line(db, env, **{"product_set_id": retired.id})
        assert refused.value.status_code == 422
        assert refused.value.detail["code"] == "quotation_line_set_inactive"

        # a line quoted while the set was live, which then went inactive
        live = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env, **{"product_set_id": live.id})
        live.is_active = False
        db.flush()

        edited = _save(db, env, {"unit_price": "950.00"}, line=line)

        assert edited.product_set_id == live.id
        assert Decimal(edited.unit_price) == Decimal("950.00")


# ------------------------------------------------------------------ A5


def test_a5_changing_the_set_refreshes_the_parts_and_clears_the_picked_photo():
    with blank_session() as db:
        env = _env(db)
        first = _set(db, env["company_id"], env["category"], env["uom"])
        second = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env, **{"product_set_id": first.id})
        photo = Attachment(
            id=_uid(),
            company_id=env["company_id"],
            original_filename=f"{MARKER}-photo.jpg",
            stored_filename=f"{MARKER}-photo.jpg",
            file_path=f"https://cdn.zzt.test/{MARKER}/photo.jpg",
            mime_type="image/jpeg",
        )
        db.add(photo)
        db.flush()
        line.image_attachment_id = photo.id
        db.flush()

        changed = _save(db, env, {"product_set_id": second.id}, line=line)

        assert changed.product_set_id == second.id
        assert changed.product_code_snapshot == second.set_code
        assert [p["product_code"] for p in changed.set_parts_snapshot] == _codes(second)
        assert changed.image_attachment_id is None
        assert changed.drawing_attachment_id is None


def test_a5_any_other_edit_keeps_the_parts_as_quoted_even_if_the_set_changed_since():
    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env)
        quoted = [p["product_code"] for p in line.set_parts_snapshot]

        extra = _product(db, env["category"], env["uom"], "ZZT-ADDED-LATER")
        db.add(
            ProductSetMember(
                id=_uid(),
                product_set_id=product_set.id,
                product_id=extra.id,
                quantity=Decimal("1"),
                contributes_to_price=False,
                sort_order=9,
            )
        )
        db.flush()
        db.expire(product_set)

        edited = _save(db, env, {"unit_price": "910.00", "quantity": "12"}, line=line)

        assert [p["product_code"] for p in edited.set_parts_snapshot] == quoted
        assert "ZZT-ADDED-LATER" not in [p["product_code"] for p in edited.set_parts_snapshot]


def test_a5_moving_from_a_set_to_a_product_clears_the_set_and_its_parts():
    with blank_session() as db:
        env = _env(db)
        _set(db, env["company_id"], env["category"], env["uom"])
        product = _product(db, env["category"], env["uom"], "ZZT-PLAIN")
        line = _set_line(db, env)
        assert line.set_parts_snapshot

        changed = _save(db, env, {"product_id": product.id, "product_set_id": None}, line=line)

        assert changed.product_id == product.id
        assert changed.product_set_id is None
        assert changed.set_parts_snapshot is None


# ------------------------------------------------------------------ A6


def test_a6_revise_copies_the_set_and_the_parts_to_the_new_version():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env)
        parts = list(line.set_parts_snapshot)

        revised = quotes.revise(db, quotation=env["quotation"], actor_user_id=env["owner"])
        copied = quotes.list_lines(db, revised.id)

        assert len(copied) == 1
        assert copied[0].product_set_id == product_set.id
        assert copied[0].product_id is None
        assert copied[0].set_parts_snapshot == parts


# ------------------------------------------------------------------ A7


def test_a7_a_line_with_neither_still_needs_a_description_and_a_set_line_does_not():
    with blank_session() as db:
        env = _env(db)
        with pytest.raises(AppException) as refused:
            _save(db, env, {"unit_price": "10.00", "quantity": "1"})
        assert refused.value.detail["code"] == "quotation_line_description_required"
        assert refused.value.detail["message"] == "A non-standard item needs a description."

        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env)
        assert line.product_set_id == product_set.id
        assert line.description_snapshot == product_set.name


# ------------------------------------------------------------------ wire shape


def test_the_line_serializer_carries_product_set_id_and_set_parts():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        plain_product = _product(db, env["category"], env["uom"], "ZZT-ORDINARY")
        set_line = _set_line(db, env)
        product_line = _save(
            db, env, {"product_id": plain_product.id, "unit_price": "5.00", "quantity": "1"}
        )

        rows = {r["id"]: r for r in quotes.serialize_lines(db, [set_line, product_line])}

        assert rows[set_line.id]["product_set_id"] == product_set.id
        assert [p["product_code"] for p in rows[set_line.id]["set_parts"]] == _codes(product_set)
        assert rows[product_line.id]["product_set_id"] is None
        assert rows[product_line.id]["set_parts"] == []


# ------------------------------------------------------------------ B2 standard


def test_b2_is_set_in_series_is_true_with_no_series_and_follows_the_row_otherwise():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        listed = _set(db, env["company_id"], env["category"], env["uom"])
        unlisted = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])
        _series_row(db, series, listed, price="1000.00", pct="10.00")

        assert pricing.is_set_in_series(db, None, listed.id) is True
        assert pricing.is_set_in_series(db, series.id, listed.id) is True
        assert pricing.is_set_in_series(db, series.id, unlisted.id) is False


def test_b2_a_set_line_is_standard_only_when_the_scopes_series_has_a_row_for_it():
    with blank_session() as db:
        company_id = _sorento(db)
        series = _series(db, company_id)
        env = _env(db, series_id=series.id)
        listed = _set(db, env["company_id"], env["category"], env["uom"])
        unlisted = _set(db, env["company_id"], env["category"], env["uom"])
        _series_row(db, series, listed, price="1000.00", pct="10.00")

        standard = _set_line(db, env, **{"product_set_id": listed.id})
        non_standard = _set_line(db, env, **{"product_set_id": unlisted.id})

        assert standard.is_non_standard is False
        assert non_standard.is_non_standard is True


def test_b2_a_scope_with_no_series_treats_a_set_line_as_standard():
    with blank_session() as db:
        env = _env(db)
        _set(db, env["company_id"], env["category"], env["uom"])

        line = _set_line(db, env)

        assert line.is_non_standard is False


# ------------------------------------------------------------------ B3 / B4 floor


def test_b3_the_series_row_sets_the_floor_at_level_series():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])
        _series_row(db, series, product_set, price="1000.00", pct="10.00")

        floor = pricing.resolve_set_floor(
            db,
            company_id=env["company_id"],
            product_set=product_set,
            list_price=Decimal("200.00"),
            series_id=series.id,
        )

        assert floor is not None
        assert floor.value == Decimal("900.00")
        assert floor.level == "series"


def test_b3_a_series_row_missing_one_number_falls_to_the_company_default_on_the_set_list_price():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])
        _series_row(db, series, product_set, price="1000.00", pct=None)
        _system_rule(db, env["company_id"], "percent", "50.00")

        floor = pricing.resolve_set_floor(
            db,
            company_id=env["company_id"],
            product_set=product_set,
            list_price=Decimal("600.00"),
            series_id=series.id,
        )

        assert floor is not None
        assert floor.value == Decimal("300.00")
        assert floor.level != "series"


def test_b3_product_and_category_rules_never_apply_to_a_set():
    """Q4 b: with no company default, a rule on a member product or its category is ignored."""
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        pricing.upsert_floor_rule(
            db,
            company_id=env["company_id"],
            payload={"mode": "percent", "value": "90", "category_id": env["category"].id},
        )
        member_product = product_set.members[0].product
        pricing.upsert_floor_rule(
            db,
            company_id=env["company_id"],
            payload={"mode": "percent", "value": "90", "product_id": member_product.id},
        )

        floor = pricing.resolve_set_floor(
            db,
            company_id=env["company_id"],
            product_set=product_set,
            list_price=Decimal("1000.00"),
            series_id=None,
        )

        assert floor is None


def test_b4_a_percent_default_with_no_set_list_price_gives_no_floor_and_an_absolute_one_applies():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _system_rule(db, env["company_id"], "percent", "50.00")

        assert (
            pricing.resolve_set_floor(
                db,
                company_id=env["company_id"],
                product_set=product_set,
                list_price=None,
                series_id=None,
            )
            is None
        )

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _system_rule(db, env["company_id"], "absolute", "480.00")

        floor = pricing.resolve_set_floor(
            db,
            company_id=env["company_id"],
            product_set=product_set,
            list_price=None,
            series_id=None,
        )
        assert floor is not None and floor.value == Decimal("480.00")


def test_b3_a_saved_set_line_stores_the_floor_it_was_judged_against():
    with blank_session() as db:
        env = _env(db)
        # set list price 200 (pedestal), default 50% of list -> 100
        _set(db, env["company_id"], env["category"], env["uom"])
        _system_rule(db, env["company_id"], "percent", "50.00")

        clear = _set_line(db, env, unit_price="150.00")
        below = _set_line(db, env, unit_price="90.00")

        assert Decimal(clear.floor_value_applied) == Decimal("100.00")
        assert clear.is_below_floor is False
        assert Decimal(below.floor_value_applied) == Decimal("100.00")
        assert below.is_below_floor is True


# ------------------------------------------------------------------ B5 / B7


def test_b5_a_set_line_below_its_floor_notifies_once_and_blocks_issue_until_approved():
    from app.services import project_quotation_approval_service as approval
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        _set(db, env["company_id"], env["category"], env["uom"])
        _system_rule(db, env["company_id"], "percent", "50.00")

        line = _set_line(db, env, unit_price="90.00")
        assert line.is_below_floor is True
        assert len(quotes.pop_breach_events(line)) == 1

        # saving again while still below stays silent (transition into breach only)
        again = _save(db, env, {"quantity": "11"}, line=line)
        assert quotes.pop_breach_events(again) == []

        from app.models.projects import ProjectQuotationDocument

        document = (
            db.query(ProjectQuotationDocument)
            .filter(ProjectQuotationDocument.id == env["quotation"].document_id)
            .one()
        )
        assert approval.below_floor_line_count(db, document) == 1
        assert approval.requires_approval(db, document) is True


def test_b7_recompute_re_evaluates_set_lines():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env, unit_price="90.00")
        assert line.is_below_floor is False and line.floor_value_applied is None

        _system_rule(db, env["company_id"], "percent", "50.00")
        report = quotes.recompute_version(db, version=env["version"])

        assert line.is_below_floor is True
        assert Decimal(line.floor_value_applied) == Decimal("100.00")
        assert report["now_below_floor"] == 1
        assert len(report["breach_events"]) == 1


# ------------------------------------------------------------------ C1 series sets (service)


def test_c1_a_series_set_is_added_priced_listed_and_removed():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])

        created = pricing.set_series_set_pricing(
            db,
            series=series,
            product_set_id=product_set.id,
            selling_price=Decimal("1000.00"),
            max_discount_pct=Decimal("10.00"),
        )
        assert created["selling_price"] == Decimal("1000.00")
        assert created["derived_floor"] == Decimal("900.00")

        updated = pricing.set_series_set_pricing(
            db,
            series=series,
            product_set_id=product_set.id,
            selling_price=Decimal("1100.00"),
            max_discount_pct=Decimal("5.00"),
        )
        assert updated["derived_floor"] == Decimal("1045.00")

        rows = pricing.series_set_rows(db, series.id)
        assert len(rows) == 1
        assert rows[0]["product_set_id"] == product_set.id
        assert rows[0]["set_code"] == product_set.set_code
        assert rows[0]["set_name"] == product_set.name
        assert rows[0]["part_codes"] == _codes(product_set)
        assert rows[0]["selling_price"] == Decimal("1100.00")
        assert rows[0]["max_discount_pct"] == Decimal("5.00")
        assert rows[0]["derived_floor"] == Decimal("1045.00")

        assert pricing.remove_series_set(db, series=series, product_set_id=product_set.id) is True
        assert pricing.remove_series_set(db, series=series, product_set_id=product_set.id) is False
        assert pricing.series_set_rows(db, series.id) == []


def test_c1_a_set_without_both_numbers_has_no_derived_floor():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])

        row = pricing.set_series_set_pricing(
            db,
            series=series,
            product_set_id=product_set.id,
            selling_price=Decimal("1000.00"),
            max_discount_pct=None,
        )

        assert row["derived_floor"] is None


def test_c5_another_companys_set_cannot_be_put_on_a_series():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        series = _series(db, env["company_id"])
        theirs = _foreign_set(db, code=f"ZZTTHEIRS{_uid()[:4]}")

        for set_id in (theirs, _uid()):
            with pytest.raises(AppException) as refused:
                pricing.set_series_set_pricing(
                    db,
                    series=series,
                    product_set_id=set_id,
                    selling_price=Decimal("1.00"),
                    max_discount_pct=Decimal("1.00"),
                )
            assert refused.value.status_code == 404
            assert refused.value.detail["code"] == "product_set_not_found"
        assert pricing.series_set_rows(db, series.id) == []


# ------------------------------------------------------------------ D1 / D2 documents


def _numbering_rule(db, company_id: str) -> None:
    scoped = hasattr(DocumentNumberingRule, "company_id")
    query = db.query(DocumentNumberingRule).filter(
        DocumentNumberingRule.doc_type == "project_quotation"
    )
    if scoped:
        query = query.filter(DocumentNumberingRule.company_id == company_id)
    rule = query.first()
    if rule is None:
        rule = DocumentNumberingRule(id=_uid(), doc_type="project_quotation")
        if scoped:
            rule.company_id = company_id
        db.add(rule)
    rule.enabled = True
    rule.prefix_template = f"{MARKER}/Q/"
    rule.number_digits = 4
    rule.next_value = 1
    rule.start_value = 1
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()


def test_d1_the_pdf_prints_a_set_line_as_one_row_with_its_complete_set_text():
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        _numbering_rule(db, company_id)
        owner = _user(db)
        category, uom = _catalogue(db)
        product_set = _set(db, company_id, category, uom)
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=owner,
            developer_party_id=None,
            title=f"{MARKER} Tower {_uid()[:12]}",
        )
        document = qdocs.create_document(db, project=project, actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        line = quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_set_id": product_set.id, "unit_price": "900.00", "quantity": "4"},
        )
        qdocs.sign_as_sorento(
            db,
            document=document,
            actor_user_id=owner,
            payload={
                "mode": "draw",
                "signer_name": f"{MARKER} Baser",
                "image_data_uri": SIGNATURE_DATA_URI,
            },
        )
        issued = qdocs.issue(db, document=document, actor_user_id=owner)

        html = qpdf.build_issue_html(db, issued)

        assert product_set.set_code in html
        assert product_set.name in html
        assert line.complete_set and line.complete_set in html
        for code in _codes(product_set):
            assert code in html
        assert re.search(r">\s*4(\.0+)?\s+SET<", html)
        assert "3,600.00" in html
        # one row for the set: the product code of a part is not printed as its own row
        assert html.count(f">{product_set.set_code}<") == 1


def test_d2_the_photo_candidates_of_a_set_line_are_the_driver_parts_product_files():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        driver_product = product_set.members[1].product  # the pedestal, not index 0
        other_product = product_set.members[0].product
        driver_file = Attachment(
            id=_uid(),
            company_id=env["company_id"],
            original_filename=f"{MARKER}-pedestal.jpg",
            stored_filename=f"{MARKER}-pedestal.jpg",
            file_path=f"https://cdn.zzt.test/{MARKER}/pedestal.jpg",
            thumbnail_path=f"https://cdn.zzt.test/{MARKER}/pedestal.thumb.jpg",
            mime_type="image/jpeg",
        )
        other_file = Attachment(
            id=_uid(),
            company_id=env["company_id"],
            original_filename=f"{MARKER}-cistern.jpg",
            stored_filename=f"{MARKER}-cistern.jpg",
            file_path=f"https://cdn.zzt.test/{MARKER}/cistern.jpg",
            thumbnail_path=f"https://cdn.zzt.test/{MARKER}/cistern.thumb.jpg",
            mime_type="image/jpeg",
        )
        db.add_all([driver_file, other_file])
        db.flush()
        db.add_all(
            [
                ProductAttachment(
                    id=_uid(),
                    product_id=driver_product.id,
                    attachment_id=driver_file.id,
                    is_primary=True,
                    sort_order=0,
                ),
                ProductAttachment(
                    id=_uid(),
                    product_id=other_product.id,
                    attachment_id=other_file.id,
                    is_primary=True,
                    sort_order=0,
                ),
            ]
        )
        db.flush()
        line = _set_line(db, env)

        cell = quotes.serialize_lines(db, [line])[0]["product_image"]

        assert cell["candidate_count"] == 1
        assert [c.get("filename") for c in cell["candidates"]] == [f"{MARKER}-pedestal.jpg"]


# ------------------------------------------------------------------ routes


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db)
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Tower {_uid()[:12]}",
        )
        db.commit()
        client, originals = _client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, user_id, project
        finally:
            _restore(originals)


def _client(db, user_id: str, *, slugs=None):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    held = set(
        slugs
        if slugs is not None
        else [
            "projects.projects.view",
            "projects.projects.create",
            "projects.projects.edit",
            "projects.projects.delete",
            "projects.projects.manage",
            "projects.types.view",
            "projects.types.edit",
        ]
    )
    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in held
    UserPermissionService.get_user_permission_slugs = lambda self, uid: sorted(held)
    return TestClient(app, raise_server_exceptions=False), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


def _api_series(client, name: str = "Sets") -> dict:
    response = client.post(
        f"{BASE}/config/series", json={"name": f"{MARKER} {name}", "category_ids": []}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _api_quotation(client, project_id: str) -> dict:
    response = client.post(
        f"{BASE}/projects/{project_id}/quotations", json={"scope_label": "House Units"}
    )
    assert response.status_code == 201, response.text
    return response.json()


