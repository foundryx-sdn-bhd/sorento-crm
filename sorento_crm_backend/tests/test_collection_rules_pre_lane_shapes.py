"""NUMBER-FIELDS round 5 (S1): the collection gate refuses only bad number values. Shapes the
builder produced and that saved before the lane (empty nested group, blank condition, empty
"is any of", blank between) still save, and so do pins sent with a leftover blank condition."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402
from tests.test_dealer_kit_routes import _ADMIN_ID, api  # noqa: F401

URL = "/api/v1/dealer-kit/collections"


def _cond(fact, operator, value):
    return {"kind": "condition", "fact": fact, "operator": operator, "value": value}


def _group(*rules):
    return {"kind": "group", "combinator": "and", "rules": list(rules)}


SHAPES = {
    "empty_nested_group": _group(_group()),
    "blank_condition": _group(_cond("product.category", "eq", "")),
    "empty_is_any_of": _group(_cond("product.brand", "in", [])),
    "blank_between": _group(_cond("product.listPrice", "between", ["", ""])),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_create_collection_accepts_pre_lane_shape(api, shape):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post(URL, json={"scope": "library", "name": "ZZT shape", "conditions": SHAPES[shape]})
    assert res.status_code == 201, res.text


@pytest.mark.parametrize("shape", list(SHAPES))
def test_update_collection_accepts_pre_lane_shape(api, shape):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    with TestClient(app, raise_server_exceptions=False) as c:
        made = c.post(URL, json={"scope": "library", "name": "ZZT shape"})
        assert made.status_code == 201, made.text
        res = c.put(
            f"{URL}/{made.json()['id']}",
            json={"scope": "library", "name": "ZZT shape", "conditions": SHAPES[shape]},
        )
    assert res.status_code == 200, res.text


def test_saving_pins_with_a_leftover_blank_condition_works(api):  # noqa: F811
    db, _as = api
    _as(_ADMIN_ID)
    pin = str(uuid.uuid4())
    with TestClient(app, raise_server_exceptions=False) as c:
        made = c.post(URL, json={"scope": "library", "name": "ZZT pins"})
        assert made.status_code == 201, made.text
        res = c.put(
            f"{URL}/{made.json()['id']}",
            json={
                "scope": "library",
                "name": "ZZT pins",
                "conditions": SHAPES["blank_condition"],
                "pinnedProductIds": [pin],
            },
        )
    assert res.status_code == 200, res.text
