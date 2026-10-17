"""CHAT-CODE-LOOKUP R2-2: a held family member whose family has no drawing on file answers
"no drawing" for the held product, not "not found" plus a did-you-mean. Gate = closed_lists AND
code_lookup. Synthetic family ZZJC802A-*; none of its members carries a drawing attachment."""
from __future__ import annotations

import uuid

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.resources import AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_code_lookup_held_family import DRAWING, _ask, _product, _turns  # noqa: F401
from tests.chatbot.test_engine import seeded, stub_access  # noqa: F401

HELD = "ZZJC802A-1500"
FAMILY = (HELD, "ZZJC802A-1700", "ZZJC802A-1800")


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZJ JC", company_id=DEFAULT_COMPANY_ID)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in FAMILY:
        p = Product(id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
                    base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID)
        db.add(p)
        ids[code] = p.id
    db.add(AttachmentType(id=str(uuid.uuid4()), type_name="Technical Specifications",
                          description="Technical Specifications / Spec / Drawing by Marketing", allowed_extensions="pdf"))
    db.commit()
    return ids


def test_r2_2_held_member_without_a_drawing_says_no_drawing_not_not_found(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)
    first = _ask("check_stock", "inventory", _product(HELD))
    second = _ask("check_product_attachment", "product_attachment", _product("zzjc802a"), DRAWING)

    (_r1, _c1), (r2, _c2) = _turns(session_factory, stub_access, monkeypatch, [(HELD, first), ("zzjc802a drawing", second)])

    text = (r2.reply or {}).get("text") or ""
    lowered = text.lower()
    assert HELD in text, text
    assert "no drawing" in lowered or "no technical" in lowered or "doesn't have a drawing" in lowered or "no attachment" in lowered, text
    assert "couldn't find" not in lowered and "not found" not in lowered, text
    assert "did you mean" not in lowered, text
