"""NUMBER-FIELDS round 4 (S2): the dealer-kit collection write path refuses a non-finite numeric
rule value (it never reached validate_tree), and nothing is stored."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402

# Reuses the seeded roles + dependency overrides of the dealer-kit route tests.
from tests.test_dealer_kit_routes import _ADMIN_ID, api  # noqa: F401

URL = "/api/v1/dealer-kit/collections"
BAD = ["NaN", "Infinity", "-Infinity"]


def _tree(operator: str, value):
    return {
        "kind": "group",
        "combinator": "and",
        "rules": [
            {"kind": "condition", "fact": "product.listPrice", "operator": operator, "value": value}
        ],
    }


@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("operator", ["gt", "between"])
def test_create_collection_refuses_non_finite_rule_value(api, operator, bad):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    value = [bad, "5"] if operator == "between" else bad
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(
            URL,
            json={"scope": "library", "name": "ZZT range", "conditions": _tree(operator, value)},
        )
    assert res.status_code in (400, 422), res.text
    assert db.query(Collection).count() == 0


@pytest.mark.parametrize("bad", BAD)
def test_update_collection_refuses_non_finite_rule_value_and_keeps_the_old_rule(api, bad):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    good = _tree("gt", "10")
    with TestClient(app, raise_server_exceptions=False) as c:
        made = c.post(URL, json={"scope": "library", "name": "ZZT range", "conditions": good})
        assert made.status_code == 201, made.text
        res = c.put(
            f"{URL}/{made.json()['id']}",
            json={"scope": "library", "name": "ZZT range", "conditions": _tree("between", [bad, "5"])},
        )
    assert res.status_code in (400, 422), res.text
    db.expire_all()
    row = db.query(Collection).one()
    assert row.conditions_json == good


def _kindless_root(value="NaN"):
    return {
        "combinator": "and",
        "rules": [
            {"kind": "condition", "fact": "product.listPrice", "operator": "neq", "value": value}
        ],
    }


def test_create_collection_refuses_non_finite_value_under_a_kindless_root(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(
            URL,
            json={"scope": "library", "name": "ZZT kindless", "conditions": _kindless_root()},
        )
    assert res.status_code in (400, 422), res.text
    assert db.query(Collection).count() == 0


def test_update_collection_refuses_non_finite_value_under_a_kindless_root(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    good = _tree("gt", "10")
    with TestClient(app, raise_server_exceptions=False) as c:
        made = c.post(URL, json={"scope": "library", "name": "ZZT kindless", "conditions": good})
        assert made.status_code == 201, made.text
        res = c.put(
            f"{URL}/{made.json()['id']}",
            json={"scope": "library", "name": "ZZT kindless", "conditions": _kindless_root()},
        )
    assert res.status_code in (400, 422), res.text
    db.expire_all()
    assert db.query(Collection).one().conditions_json == good


def _condition_with_empty_rules(value="NaN"):
    return {
        "kind": "group",
        "combinator": "and",
        "rules": [
            {
                "kind": "condition",
                "fact": "product.listPrice",
                "operator": "neq",
                "value": value,
                "rules": [],
            }
        ],
    }


def test_create_collection_refuses_non_finite_value_on_a_condition_carrying_empty_rules(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(
            URL,
            json={
                "scope": "library",
                "name": "ZZT sneaky",
                "conditions": _condition_with_empty_rules(),
            },
        )
    assert res.status_code in (400, 422), res.text
    assert db.query(Collection).count() == 0


def test_update_collection_refuses_non_finite_value_on_a_condition_carrying_empty_rules(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    from app.models.dealer_kit import Collection

    good = _tree("gt", "10")
    with TestClient(app, raise_server_exceptions=False) as c:
        made = c.post(URL, json={"scope": "library", "name": "ZZT sneaky", "conditions": good})
        assert made.status_code == 201, made.text
        res = c.put(
            f"{URL}/{made.json()['id']}",
            json={
                "scope": "library",
                "name": "ZZT sneaky",
                "conditions": _condition_with_empty_rules(),
            },
        )
    assert res.status_code in (400, 422), res.text
    db.expire_all()
    assert db.query(Collection).one().conditions_json == good
