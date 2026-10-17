"""NUMBER-FIELDS round 4 (security r1, findings 1-4): the lane's validators carry an upper
bound so an oversize figure is a clean 4xx, never a 500, and no SQL text reaches the body."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session
from tests.test_dealer_kit_routes import _ADMIN_ID, api  # noqa: F401
from tests.test_non_finite_numbers_refused import BASE, _contact, _header_count, _line_count, _portal_client

LEAK = ("INSERT", "UPDATE", "psycopg2")


def _no_leak(res) -> None:
    for word in LEAK:
        assert word not in res.text, f"SQL text leaked into the body: {word}"


# -- security #1: a 400-digit JSON integer is finite, not a 500 --------------------------------


def test_rule_builder_numeric_accepts_a_huge_integer_as_finite():
    from app.rule_engine import schemas as rs

    assert rs._numeric(10**400) is True
    assert rs._numeric(-(10**400)) is True
    assert rs._numeric(float("nan")) is False


# -- security #2: upper bounds on the validators this lane added -------------------------------


@pytest.mark.parametrize("qty", ["10000000000000", "1e999999999999999999"])
def test_internal_pr_line_quantity_at_or_above_1e13_is_a_422(api, qty):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(
            "/api/v1/procurement/purchase-requests/",
            json={
                "request_type": "purchase_request",
                "customer_name": "ZZT",
                "products": [{"item_code": "ZZT-ITEM", "quantity": qty}],
            },
        )
    assert res.status_code == 422, res.text
    _no_leak(res)
    assert _header_count(db) == 0


def test_order_line_quantity_at_or_above_1e11_is_a_422(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(
            f"/api/v1/order-management/orders/{uuid.uuid4()}/lines",
            json={
                "product_id": str(uuid.uuid4()),
                "warehouse_id": str(uuid.uuid4()),
                "quantity": "100000000000",
            },
        )
    assert res.status_code == 422, res.text
    _no_leak(res)


def test_spo_allocation_quantity_above_int_max_is_a_422(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.put(
            f"/api/v1/procurement/spo-allocations/{uuid.uuid4()}",
            json={"allocated_quantity": 2147483648},
        )
    assert res.status_code == 422, res.text
    _no_leak(res)


# -- security #3: total_project_value with a huge exponent is a clean refusal ------------------


def test_validate_project_value_huge_exponent_is_a_clean_too_large():
    from app.services.error_handler import AppException
    from app.services.validators import validate_project_value

    with pytest.raises(AppException) as exc:
        validate_project_value("1e999999999999999999")
    assert exc.value.status_code in (400, 422)
    assert "too large" in str(exc.value.detail).lower()


def test_portal_draft_total_project_value_huge_exponent_is_a_4xx():
    with blank_session() as db:
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT", "total_project_value": "1e999999999999999999"}},
            )
            assert res.status_code in (400, 422), res.text
            assert _header_count(db) == 0


# -- security #4: portal line figures out of range are a validation refusal --------------------


@pytest.mark.parametrize("column", ["quantity", "unit_price", "total"])
def test_portal_draft_line_figure_out_of_range_is_a_400(column):
    with blank_session() as db:
        contact = _contact(db)
        line = {"item_code": "ZZT-ITEM", "quantity": "2", "unit_price": "10", "total": "20"}
        line[column] = "1E+999999"
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT"}, "products": [line]},
            )
            assert res.status_code == 400, res.text
            assert _line_count(db) == 0
            assert _header_count(db) == 0


# -- round 5 S2 / N2 ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["quantity", "unit_price", "total"])
@pytest.mark.parametrize("figure", ["1e1000000", "-1E+1000001", "1e999999999999999999"])
def test_portal_draft_line_figure_beyond_decimal_emax_is_a_400(column, figure):
    with blank_session() as db:
        contact = _contact(db)
        line = {"item_code": "ZZT-ITEM", "quantity": "2", "unit_price": "10", "total": "20"}
        line[column] = figure
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT"}, "products": [line]},
            )
            assert res.status_code == 400, res.text
            assert _line_count(db) == 0
            assert _header_count(db) == 0


def test_portal_draft_too_large_quantity_says_too_large_not_invalid_number():
    with blank_session() as db:
        contact = _contact(db)
        line = {"item_code": "ZZT-ITEM", "quantity": "10000000000000", "unit_price": "10", "total": "20"}
        with _portal_client(db, contact.id) as c:
            res = c.post(
                f"{BASE}/submissions/sponsorship_form",
                json={"fields": {"customer_name": "ZZT"}, "products": [line]},
            )
            assert res.status_code == 400, res.text
            assert "too large" in res.text.lower(), res.text
            assert "valid number" not in res.text.lower(), res.text
