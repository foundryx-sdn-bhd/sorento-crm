"""Seed helpers for the PV2-RESOLVE tests (blank Postgres schema, no LLM, no network).

Not a test module. `create_pv2_tables` stands in for PV2-CORE's migration until its tables
are on `Base`; every statement is idempotent so it keeps working after they land.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager

from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.order import Customer
from app.models.product import Brand, Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._pg_fixture import blank_session, unique_code

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def create_pv2_tables(db) -> None:
    for stmt in (
        "ALTER TABLE attachment_types ADD COLUMN IF NOT EXISTS search_synonyms JSONB NOT NULL DEFAULT '[]'::jsonb",
        "CREATE TABLE IF NOT EXISTS chatbot_alias_precedence (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
        "kind TEXT NOT NULL, alias_norm TEXT NOT NULL, winner_key TEXT NOT NULL, UNIQUE(kind, alias_norm))",
        "CREATE TABLE IF NOT EXISTS chatbot_unrecognised_phrases (id UUID PRIMARY KEY, phrase TEXT NOT NULL, "
        "kind TEXT NOT NULL, domain TEXT NULL, contact_id TEXT NULL, turn_id TEXT NULL, "
        "seen_count INTEGER NOT NULL DEFAULT 1, first_seen_at TIMESTAMP NOT NULL DEFAULT now(), "
        "last_seen_at TIMESTAMP NOT NULL DEFAULT now(), UNIQUE(phrase, kind))",
    ):
        db.execute(text(stmt))
    db.flush()


@contextmanager
def resolve_session():
    """Blank schema + the PV2 tables + the default company in scope."""
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        create_pv2_tables(db)
        yield db


def category(db, *, class_label=None, synonyms=None, code=None, searchable=True) -> ProductCategory:
    row = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=code or unique_code("CAT")[:50],
        category_name=class_label or "Category",
        class_label=class_label,
        search_synonyms=list(synonyms or []),
        is_searchable=searchable,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def brand(db, name, *, active=True, code=None) -> Brand:
    row = Brand(
        id=str(uuid.uuid4()),
        brand_code=code or unique_code("BR")[:50],
        brand_name=name,
        is_active=active,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _uom(db) -> str:
    row = UnitOfMeasure(
        id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID
    )
    db.add(row)
    db.flush()
    return row.id


def product(db, code, *, cat=None, brand_row=None) -> Product:
    cat = cat or category(db)
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=f"Name of {code}",
        category_id=cat.id,
        brand_id=brand_row.id if brand_row else None,
        base_uom_id=_uom(db),
        list_price=0,
        is_active=True,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def attachment_type(db, code, name, *, synonyms=()) -> AttachmentType:
    row = AttachmentType(id=str(uuid.uuid4()), code=code, type_name=name, allowed_extensions="pdf")
    db.add(row)
    db.flush()
    set_type_synonyms(db, code, synonyms)
    return row


def set_type_synonyms(db, code, synonyms) -> None:
    db.execute(
        text("UPDATE attachment_types SET search_synonyms = CAST(:w AS jsonb) WHERE code = :c"),
        {"w": json.dumps(list(synonyms)), "c": code},
    )
    db.flush()


def attach(db, prod: Product, atype: AttachmentType) -> None:
    name = f"{unique_code('f')}.pdf"
    att = Attachment(
        id=str(uuid.uuid4()),
        original_filename=name,
        stored_filename=name,
        file_path=f"https://cdn.test/{name}",
        attachment_type_id=atype.id,
        is_deleted=False,
    )
    db.add(att)
    db.flush()
    db.add(
        ProductAttachment(
            id=str(uuid.uuid4()), product_id=prod.id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID
        )
    )
    db.flush()


def customer(db, code, name) -> Customer:
    row = Customer(
        id=str(uuid.uuid4()), customer_code=code, customer_name=name, is_active=True, company_id=DEFAULT_COMPANY_ID
    )
    db.add(row)
    db.flush()
    return row


def registry(
    db, spec_key, label, data_type, *, unit=None, allowed=(), synonyms=None, active=True, applies_when=None
) -> ProductSpecRegistry:
    row = ProductSpecRegistry(
        id=str(uuid.uuid4()),
        spec_key=spec_key,
        label=label,
        data_type=data_type,
        unit=unit,
        allowed_values=list(allowed),
        synonyms=dict(synonyms or {}),
        applies_when=dict(applies_when or {}),
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def specs(db, prod: Product, *, klass=None, **numbers) -> ProductSpecifications:
    """values {"class": {"value": klass}, "dim_length": {"value": 600, "unit": "mm"}}."""
    values: dict = {}
    if klass:
        values["class"] = {"value": klass}
    for key, value in numbers.items():
        values[key] = {"value": value, "unit": "mm"} if isinstance(value, (int, float)) else {"value": value}
    row = ProductSpecifications(id=str(uuid.uuid4()), product_id=prod.id, values=values)
    db.add(row)
    db.flush()
    return row


def assert_clean_label(label: str) -> None:
    assert label, "empty label"
    assert not UUID_RE.search(label), f"uuid in label: {label!r}"
    assert " · " not in label, f"middle dot separator in label: {label!r}"
