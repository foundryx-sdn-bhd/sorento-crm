"""NUMBER-FIELDS round 3 (B1, R10): a non-finite number ('NaN', 'Infinity') is refused with a
422 on the portal / external purchase request write path and in the rule builder, and nothing
is stored. A blank still becomes null where it does today.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session

BASE = "/api/v1/public/portal"
NON_FINITE = ["NaN", "Infinity", "-Infinity"]


def _uid() -> str:
    return str(uuid.uuid4())


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(id=_uid(), phone_number=f"+60{_uid().replace('-', '')[:9]}", name="SALESMAN A")
    db.add(contact)
    db.flush()
    return contact


@contextmanager
def _portal_client(db, contact_id: str):
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_portal_token] = lambda: PortalToken(
        id=_uid(), contact_id=contact_id, space_id="zzt-space"
    )
    try:
        with TestClient(app, headers={"X-Portal-Token": "zzt-token"}, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _line_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM purchase_request_lines")).scalar_one()


def _header_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM purchase_requests")).scalar_one()


@pytest.mark.parametrize("bad", NON_FINITE)
@pytest.mark.parametrize("column", ["quantity", "unit_price", "total"])
def test_portal_draft_refuses_non_finite_line_number(column, bad):
    with blank_session() as db:
        contact = _contact(db)
        line = {"item_code": "ZZT-ITEM", "quantity": "2", "unit_price": "10", "total": "20"}
        line[column] = bad
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT"}, "products": [line]},
            )
            assert res.status_code in (400, 422), res.text
            assert _line_count(db) == 0


@pytest.mark.parametrize("bad", NON_FINITE)
def test_portal_draft_refuses_non_finite_total_project_value(bad):
    with blank_session() as db:
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT", "total_project_value": bad}},
            )
            assert res.status_code in (400, 422), res.text
            assert _header_count(db) == 0


def test_portal_draft_blank_line_numbers_still_save_as_null():
    with blank_session() as db:
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={
                    "fields": {"customer_name": "ZZT", "total_project_value": ""},
                    "products": [{"item_code": "ZZT-ITEM", "quantity": "", "unit_price": "", "total": ""}],
                },
            )
            assert res.status_code in (200, 201), res.text
            row = db.execute(
                text("SELECT quantity, unit_price, total FROM purchase_request_lines")
            ).one()
            assert tuple(row) == (None, None, None)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_validate_project_value_refuses_non_finite_as_not_a_number(bad):
    from app.services.error_handler import AppException
    from app.services.validators import validate_project_value

    with pytest.raises(AppException) as exc:
        validate_project_value(bad)
    assert "must be a number" in str(exc.value.detail)


def test_validate_project_value_blank_is_still_none():
    from app.services.validators import validate_project_value

    assert validate_project_value("") is None
    assert validate_project_value(None) is None
    assert validate_project_value("12.50") == Decimal("12.50")


@pytest.mark.parametrize("bad", NON_FINITE)
@pytest.mark.parametrize("column", ["quantity", "unit_price", "total"])
def test_external_purchase_request_line_refuses_non_finite(column, bad):
    from app.schemas.external.procurement import PurchaseRequestExternalCreate

    line = {"item_code": "ZZT-ITEM", "quantity": "2", "unit_price": "10", "total": "20"}
    line[column] = bad
    with pytest.raises(ValidationError):
        PurchaseRequestExternalCreate(
            request_type="sponsorship_form",
            contact_id="c1",
            space_id="s1",
            products=[line],
        )


@pytest.mark.parametrize("bad", NON_FINITE)
def test_external_purchase_request_total_project_value_refuses_non_finite(bad):
    """The external write path: the header value is validated before anything is stored."""
    from app.schemas.external.procurement import PurchaseRequestExternalCreate
    from app.services.error_handler import AppException
    from app.services.validators import validate_project_value

    try:
        payload = PurchaseRequestExternalCreate(
            request_type="purchase_request",
            contact_id="c1",
            space_id="s1",
            total_project_value=bad,
        )
    except ValidationError:
        return  # refused at the schema: a 422
    with pytest.raises(AppException):
        validate_project_value(payload.total_project_value)


@pytest.mark.parametrize("bad", NON_FINITE)
def test_rule_builder_numeric_condition_refuses_non_finite(bad):
    from app.rule_engine import schemas as rs

    assert rs._numeric(bad) is False
    assert rs._numeric(float(bad)) is False
    assert rs._numeric("12.5") is True
    assert rs._numeric(3) is True


@pytest.mark.parametrize("operator,bad", [(op, b) for op in ("gt", "lte") for b in NON_FINITE])
def test_rule_tree_numeric_condition_with_non_finite_value_has_a_problem(monkeypatch, operator, bad):
    """A saved tree is refused (the consumer turns a non-empty problem list into a 422)."""
    from app.rule_engine import schemas as rs
    from app.rule_engine.registry import FactDef

    fact = FactDef(key="qty", label="Quantity", type="number", resolver=lambda o, d: 1)
    monkeypatch.setattr(rs, "fact_map", lambda sources: {"qty": fact})
    tree = {
        "kind": "group",
        "combinator": "and",
        "rules": [{"kind": "condition", "fact": "qty", "operator": operator, "value": bad}],
    }
    assert rs.validate_tree(tree, ["x"]), "a non-finite numeric value must be refused"


@pytest.mark.parametrize("bad", NON_FINITE)
def test_rule_tree_between_with_non_finite_member_has_a_problem(monkeypatch, bad):
    from app.rule_engine import schemas as rs
    from app.rule_engine.registry import FactDef

    fact = FactDef(key="qty", label="Quantity", type="number", resolver=lambda o, d: 1)
    monkeypatch.setattr(rs, "fact_map", lambda sources: {"qty": fact})
    tree = {
        "kind": "group",
        "combinator": "and",
        "rules": [{"kind": "condition", "fact": "qty", "operator": "between", "value": [bad, "5"]}],
    }
    assert rs.validate_tree(tree, ["x"]), "a non-finite between bound must be refused"

