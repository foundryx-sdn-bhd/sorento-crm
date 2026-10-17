"""Shared seeding for the Sample Request (SR-CORE) suites.

Everything is seeded here, nothing is borrowed from existing rows (CI's database is
empty). Test support only: no implementation of the feature lives in this file.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import text

from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.models.user import User

MARKER = "zzt-sr"
BASE = "/api/v1/project-sales"


def uid() -> str:
    return str(uuid.uuid4())


def sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def second_company(db) -> str:
    company_id = uid()
    db.execute(
        text(
            "insert into companies (id, code, name, is_active) "
            "values (:i, :c, :n, true)"
        ),
        {"i": company_id, "c": f"Z{uid()[:5]}".upper(), "n": f"{MARKER} other co"},
    )
    db.flush()
    return company_id


def make_user(db, name: str) -> str:
    user_id = uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def make_product(db, *, code: str | None = None, name: str | None = None, active: bool = True):
    uom = UnitOfMeasure(id=uid(), uom_code=f"ZZT{uid()[:4]}", uom_name="Piece")
    category = ProductCategory(
        id=uid(), category_code=f"ZZT-{uid()[:8]}", category_name=f"{MARKER} cat"
    )
    brand = Brand(id=uid(), brand_code=f"ZZT-{uid()[:6]}", brand_name=f"{MARKER} Brand")
    db.add_all([uom, category, brand])
    db.flush()
    row = Product(
        id=uid(),
        product_code=code or f"ZZT-{uid()[:8]}",
        product_name=name or f"{MARKER} item {uid()[:6]}",
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=100,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def make_customer(db, company_id: str, *, code: str | None = None):
    from app.models.order import Customer

    row = Customer(
        id=uid(),
        customer_code=code or f"ZZT-C{uid()[:6]}",
        customer_name=f"{MARKER} debtor {uid()[:6]}",
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def make_project(db, company_id: str, owner: str, title: str | None = None):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=title or f"{MARKER} Tower {uid()[:6]}",
    )


def seed_catalogue(db) -> SimpleNamespace:
    """Products every suite needs: a normal one, a dashed code, and an inactive one."""
    return SimpleNamespace(
        plain=make_product(db),
        plain2=make_product(db),
        dashed=make_product(db, code="ZZT-9101", name=f"{MARKER} dashed item"),
        inactive=make_product(db, active=False),
    )


def full_payload(*, product_id: str, **over) -> dict:
    """A payload that passes every submit rule with both settings switches OFF."""
    body = {
        "customer_name": f"{MARKER} typed customer",
        "project_title": f"{MARKER} typed project",
        "delivery_date": "2026-12-01",
        "delivery_mode": "driver",
        "ship_to_name": f"{MARKER} site",
        "lines": [{"product_id": product_id, "qty": 1}],
    }
    body.update(over)
    return body
