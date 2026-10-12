"""WC-SEAT-COMPAT: seat cover -> WC compatibility links, SKU to SKU.

Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
UAC:  documentation/plans/products/wc-seat-compat-12oct-acceptance-criteria.md.

    GET  /api/v1/master-data/products/{id}/compatibility
         -> {"data": {"covers": [row], "fits": [row]}}
    POST /api/v1/master-data/products/{id}/compatibility
         {"other_product_id", "other_role": "seat_cover"|"wc"} -> 201 row
    Unlink = server-deferred pending action `product_compatibility.unlink` on the link row.

Owner ruling 12 Oct: match only what the flyer prints; no inheritance, no guesses. A WC SKU
has a cover only when a link names it. A `-SC` SKU is a seat cover, never a WC. The flyer
itself is linked by the one-off script (`tests/test_link_seat_cover_flyer.py`).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app  # noqa: E402

from app.services import record_actions  # noqa: F401  (registers the actions)
from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.base import set_company_scope
from app.models.product_compatibility import ProductCompatibility
from app.models.user import User, UserStatus
from app.services import product_compatibility_service as compat
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.form_action_grace import WINDOW_REVERSIBLE
from app.services.form_action_registry import get_action
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _client(db, allow: set[str], monkeypatch, company_id=DEFAULT_COMPANY_ID) -> TestClient:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('compat-caller')}@zzt.test",
        name="ZZT Compat Caller",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    caller = {"id": str(user.id), "email": user.email}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        scope = frozenset({company_id})
        set_company_scope(_db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_current_user_or_api_key] = lambda: caller
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear():
    yield
    for dep in (get_db, get_current_user, get_current_user_or_api_key, apply_company_scope):
        app.dependency_overrides.pop(dep, None)


def _p(db, code, company_id=DEFAULT_COMPANY_ID):
    return product(db, company_id=company_id, code=code)


def _stem(prefix="ZZWC"):
    # Letters then a digit, so `family_code_for` reads a model out of it.
    return f"{prefix}{unique_code(alpha=True)[:10].upper()}9"


def _link(db, cover, wc, source=compat.SOURCE_MANUAL):
    link = ProductCompatibility(
        company_id=cover.company_id,
        seat_cover_product_id=cover.id,
        wc_product_id=wc.id,
        source=source,
    )
    db.add(link)
    db.flush()
    return link


def _codes(products) -> list[str]:
    return sorted(p.product_code for p in products)


# --------------------------------------------------------------------------- table


def test_link_to_itself_is_refused_by_the_table(db):
    p = _p(db, f"{_stem()}-UF")
    db.add(
        ProductCompatibility(
            company_id=p.company_id, seat_cover_product_id=p.id, wc_product_id=p.id, source="manual"
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


def test_unknown_source_is_refused_by_the_table(db):
    stem = _stem()
    cover, wc = _p(db, f"{stem}C"), _p(db, f"{stem}W")
    db.add(
        ProductCompatibility(
            company_id=cover.company_id,
            seat_cover_product_id=cover.id,
            wc_product_id=wc.id,
            source="guess",
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


def test_duplicate_pair_is_refused_by_the_table(db):
    stem = _stem()
    cover, wc = _p(db, f"{stem}C"), _p(db, f"{stem}W")
    _link(db, cover, wc)
    db.add(
        ProductCompatibility(
            company_id=cover.company_id,
            seat_cover_product_id=cover.id,
            wc_product_id=wc.id,
            source="manual",
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


# --------------------------------------------------------------------------- reads


def test_spare_segment_marks_a_seat_cover():
    assert compat.is_spare_seat_cover("SRTWC8383-SC")
    assert compat.is_spare_seat_cover("srtwc8383 - rl - sc")
    assert not compat.is_spare_seat_cover("SRTWC8383-RL-UF")
    assert not compat.is_spare_seat_cover("SRTSC8383")


def test_covers_for_reads_only_the_skus_own_links(db):
    stem = _stem()
    white, black = _p(db, f"{stem}SEAT"), _p(db, f"{stem}SEATB-MBL")
    wc_white, wc_sibling = _p(db, f"{stem}-RL-UF"), _p(db, f"{stem}-SH-UF")
    wc_black = _p(db, f"{stem}-RL-MBL")
    _link(db, white, wc_white)
    _link(db, black, wc_black)
    result = compat.covers_for(db, [wc_white.id, wc_sibling.id, wc_black.id])
    assert _codes(result[wc_white.id]) == [white.product_code]
    assert _codes(result[wc_black.id]) == [black.product_code]
    # No inheritance: a sibling of the same model with no link of its own has nothing.
    assert wc_sibling.id not in result


def test_two_covers_on_one_sku_both_show(db):
    stem = _stem()
    a, b = _p(db, f"{stem}SEAT-UF"), _p(db, f"{stem}SEATA-UF")
    wc = _p(db, f"{stem}-UF")
    _link(db, a, wc)
    _link(db, b, wc)
    assert _codes(compat.covers_for(db, [wc.id])[wc.id]) == sorted([a.product_code, b.product_code])


def test_wcs_for_lists_the_linked_skus(db):
    stem = _stem()
    cover = _p(db, f"{stem}SEAT-BL")
    wc1, wc2 = _p(db, f"{stem}-RL-BL"), _p(db, f"{_stem()}-RL-BL")
    _link(db, cover, wc1)
    _link(db, cover, wc2)
    assert _codes(compat.wcs_for(db, [cover.id])[cover.id]) == sorted([wc1.product_code, wc2.product_code])


# --------------------------------------------------------------------------- product page API


def _page(p) -> str:
    return f"/api/v1/master-data/products/{p.id}/compatibility"


def test_get_requires_view(db, monkeypatch):
    wc = _p(db, f"{_stem()}-UF")
    assert _client(db, set(), monkeypatch).get(_page(wc)).status_code == 403


def test_get_lists_both_directions(db, monkeypatch):
    stem = _stem()
    cover, wc = _p(db, f"{stem}SEAT"), _p(db, f"{stem}-RL-UF")
    link = _link(db, cover, wc, source=compat.SOURCE_FLYER)
    client = _client(db, {VIEW}, monkeypatch)

    on_wc = client.get(_page(wc)).json()["data"]
    assert on_wc["fits"] == []
    [row] = on_wc["covers"]
    assert row == {
        "id": link.id,
        "product_id": cover.id,
        "product_code": cover.product_code,
        "product_name": cover.product_name,
        "source": "flyer",
        "is_discontinued": False,
    }

    on_cover = client.get(_page(cover)).json()["data"]
    assert on_cover["covers"] == []
    assert [r["product_id"] for r in on_cover["fits"]] == [wc.id]


def test_get_empty_for_an_unlinked_product(db, monkeypatch):
    wc = _p(db, f"{_stem()}-UF")
    data = _client(db, {VIEW}, monkeypatch).get(_page(wc)).json()["data"]
    assert data == {"covers": [], "fits": []}


def test_add_requires_edit(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    response = _client(db, {VIEW}, monkeypatch).post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "seat_cover"}
    )
    assert response.status_code == 403


def test_add_a_cover_from_a_wc_page(db, monkeypatch):
    stem = _stem()
    wc, sibling, cover = _p(db, f"{stem}-RL-UF"), _p(db, f"{stem}-SH-UF"), _p(db, f"{stem}SEAT")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "seat_cover"}
    )
    assert response.status_code == 201, response.text
    row = response.json()["data"]
    assert row["product_id"] == cover.id
    link = db.get(ProductCompatibility, row["id"])
    assert (link.seat_cover_product_id, link.wc_product_id) == (cover.id, wc.id)
    assert link.source == "manual"
    assert link.created_by
    # One SKU only: the sibling is not linked.
    assert sibling.id not in compat.covers_for(db, [sibling.id])


def test_add_a_wc_from_a_cover_page(db, monkeypatch):
    stem = _stem()
    cover, wc = _p(db, f"{stem}SEAT-MBL"), _p(db, f"{stem}-RL-MBL")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(cover), json={"other_product_id": wc.id, "other_role": "wc"}
    )
    assert response.status_code == 201, response.text
    link = db.get(ProductCompatibility, response.json()["data"]["id"])
    assert (link.seat_cover_product_id, link.wc_product_id) == (cover.id, wc.id)


def test_a_spare_sc_sku_is_never_a_wc(db, monkeypatch):
    stem = _stem()
    spare, cover, wc = _p(db, f"{stem}-SC"), _p(db, f"{stem}SEAT"), _p(db, f"{stem}-UF")
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    # As the other product, named as a WC (400: the service's validation answer).
    assert client.post(
        _page(cover), json={"other_product_id": spare.id, "other_role": "wc"}
    ).status_code == 400
    # As the page product, given a cover (which would make it the WC).
    assert client.post(
        _page(spare), json={"other_product_id": cover.id, "other_role": "seat_cover"}
    ).status_code == 400
    # As a seat cover, it links like any other.
    assert client.post(
        _page(spare), json={"other_product_id": wc.id, "other_role": "wc"}
    ).status_code == 201


def test_add_duplicate_is_409(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    body = {"other_product_id": cover.id, "other_role": "seat_cover"}
    assert client.post(_page(wc), json=body).status_code == 201
    assert client.post(_page(wc), json=body).status_code == 409
    # The same pair added from the other side is the same link.
    assert client.post(
        _page(cover), json={"other_product_id": wc.id, "other_role": "wc"}
    ).status_code == 409
    # And the pair turned round (the WC named as the cover's seat cover) is refused too.
    assert client.post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "wc"}
    ).status_code == 409
    assert client.post(
        _page(cover), json={"other_product_id": wc.id, "other_role": "seat_cover"}
    ).status_code == 409


def test_add_self_is_400_and_bad_role_is_422(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    assert client.post(
        _page(wc), json={"other_product_id": wc.id, "other_role": "seat_cover"}
    ).status_code == 400
    assert client.post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "lid"}
    ).status_code == 422


def _park_unlink(client, entity_id):
    return client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "product_compatibility.unlink",
            "entity_type": "product_compatibility",
            "entity_id": entity_id,
            "payload": {},
        },
    )


def test_another_companys_product_and_link_are_404(db, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    mine = _p(db, f"{stem}-UF")
    cover = _p(db, f"{stem}SEAT", company_id=MOCHA_ID)
    wc = _p(db, f"{stem}-UF", company_id=MOCHA_ID)
    link = _link(db, cover, wc)
    client = _client(db, {VIEW, EDIT}, monkeypatch)  # Sorento only
    assert client.get(_page(wc)).status_code == 404
    assert client.post(
        _page(mine), json={"other_product_id": cover.id, "other_role": "seat_cover"}
    ).status_code == 404
    assert _park_unlink(client, link.id).status_code == 404
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    assert db.get(ProductCompatibility, link.id) is not None


def test_unlink_is_a_registered_reversible_action():
    action = get_action("product_compatibility.unlink")
    assert action.entity_types == ("product_compatibility",)
    assert action.permission == EDIT
    assert action.window == WINDOW_REVERSIBLE


def test_unlink_parks_and_refuses_unknown_link(db, monkeypatch):
    stem = _stem()
    link = _link(db, _p(db, f"{stem}SEAT"), _p(db, f"{stem}-UF"))
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    parked = _park_unlink(client, link.id)
    assert parked.status_code == 202, parked.text
    assert db.get(ProductCompatibility, link.id) is not None
    assert _park_unlink(client, str(uuid.uuid4())).status_code == 404
    assert _park_unlink(client, "not-a-uuid").status_code in (400, 404, 422)
    assert _park_unlink(_client(db, {VIEW}, monkeypatch), link.id).status_code == 403


def test_unlink_deletes_the_link_and_is_quiet_when_gone(db):
    stem = _stem()
    link = _link(db, _p(db, f"{stem}SEAT"), _p(db, f"{stem}-UF"))
    link_id = link.id
    compat.unlink(db, link_id)
    assert db.get(ProductCompatibility, link_id) is None
    compat.unlink(db, link_id)
