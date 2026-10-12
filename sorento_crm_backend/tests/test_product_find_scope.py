"""CHAT-FIND review round 1: `/products/find` through the REAL company and brand scope.

Security review L4: `tests/test_product_find.py` pins the company scope; here the real
`apply_company_scope` runs with an X-API-Key + `contact_id` + `space_id` (only authentication is
stubbed, `tests/_brand_scope_seed.py`). A MOCHA-only contact asks about a MOCHA WC linked to a
SORENTO and a MOCHA seat cover, and about the SORENTO product itself. Plus L3 (a blank
`space_id`), duplicate keys and the inventory module (L1).
Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md.
"""
from __future__ import annotations

import json

from app.models.product_compatibility import ProductCompatibility
from app.services import product_find
from tests._brand_scope_seed import api, db, world  # noqa: F401  (fixtures by name)
from tests._brand_scope_seed import branded_product
from tests._pg_fixture import unique_code

URL = "/api/v1/master-data/products/find"


def _link(db, cover, wc) -> None:
    db.add(
        ProductCompatibility(
            company_id=cover.company_id, seat_cover_product_id=cover.id, wc_product_id=wc.id, source="manual"
        )
    )
    db.flush()


def _filters(*ids, key="fits") -> str:
    return json.dumps([{"key": key, "product_ids": [str(i) for i in ids]}])


def _mocha_cover(db, world):
    return branded_product(db, unique_code("MCHSC", alpha=True), world.mocha)


def test_scoped_contact_never_sees_an_out_of_brand_cover(api, db, world) -> None:
    mocha_cover = _mocha_cover(db, world)
    _link(db, mocha_cover, world.p_mocha)
    _link(db, world.p_sorento, world.p_mocha)
    resp = api.get(URL, world.scoped, filters=_filters(world.p_mocha.id))
    assert resp.status_code == 200, resp.text
    [group] = resp.json()["groups"]
    assert [r["product_code"] for r in group["rows"]] == [mocha_cover.product_code]
    assert world.p_sorento.product_code not in resp.text


def test_scoped_contact_asking_about_an_out_of_brand_product_gets_no_group(api, db, world) -> None:
    _link(db, _mocha_cover(db, world), world.p_sorento)
    resp = api.get(URL, world.scoped, filters=_filters(world.p_sorento.id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["groups"] == []
    assert world.p_sorento.product_code not in resp.text


def test_unscoped_contact_sees_both_covers(api, db, world) -> None:
    mocha_cover = _mocha_cover(db, world)
    _link(db, mocha_cover, world.p_mocha)
    _link(db, world.p_sorento, world.p_mocha)
    resp = api.get(URL, world.unscoped, filters=_filters(world.p_mocha.id))
    codes = {r["product_code"] for r in resp.json()["groups"][0]["rows"]}
    assert codes == {mocha_cover.product_code, world.p_sorento.product_code}


def test_a_blank_space_id_is_no_space_id(api, db, world) -> None:
    """L3: stripped like the scope resolver strips it, so a contact with a blank space is
    answered nothing rather than a policy-shaped answer over every company."""
    _link(db, _mocha_cover(db, world), world.p_mocha)
    resp = api.client.get(
        URL,
        params={"filters": _filters(world.p_mocha.id), "contact_id": world.unscoped.id, "space_id": "  "},
        headers={"X-API-Key": "zzt-cbs-api-key"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["groups"] == []


def test_a_repeated_key_is_read_once() -> None:
    pid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    raw = json.dumps([{"key": "fits", "product_ids": [pid]}] * 3)
    assert product_find.parse_filters(raw) == [{"key": "fits", "product_ids": [pid]}]


def test_inventory_module_off_leaves_stock_out(api, db, world, monkeypatch) -> None:
    """L1: stock is the inventory module's; with it off for the caller the rows carry none."""
    from app.api.v1.master_data import product_find as route

    monkeypatch.setattr(route, "module_allowed_with_api_key", lambda db, user, key: key != "inventory")
    _link(db, _mocha_cover(db, world), world.p_mocha)
    resp = api.get(URL, None, filters=_filters(world.p_mocha.id))
    [row] = resp.json()["groups"][0]["rows"]
    assert row["stock"] == {"mode": "none", "on_hand": None, "verdict": None}
