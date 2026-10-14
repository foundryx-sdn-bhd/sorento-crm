"""MIRROR-LINKS: the link table gains a kind (seat_cover | mirror | shelf).

Plan: documentation/plans/products/PLAN-mirror-shelf-links-14oct.md (section 5 is the contract).
UAC:  documentation/plans/products/mirror-shelf-links-14oct-acceptance-criteria.md.

Column mapping (unchanged): `seat_cover_product_id` is the accessory end (seat cover, mirror,
shelf), `wc_product_id` is the host end (WC, cabinet).
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
from app.services.error_handler import AppException
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _client(db, allow: set[str], monkeypatch) -> TestClient:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('compat-kind-caller')}@zzt.test",
        name="ZZT Compat Kind Caller",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    caller = {"id": str(user.id), "email": user.email}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        scope = frozenset({DEFAULT_COMPANY_ID})
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


def _p(db, code):
    return product(db, company_id=DEFAULT_COMPANY_ID, code=code)


def _stem(prefix="ZZMR"):
    return f"{prefix}{unique_code(alpha=True)[:10].upper()}9"


def _link(db, accessory, host, kind, source=compat.SOURCE_MANUAL):
    link = ProductCompatibility(
        company_id=accessory.company_id,
        seat_cover_product_id=accessory.id,
        wc_product_id=host.id,
        source=source,
        kind=kind,
    )
    db.add(link)
    db.flush()
    return link


def _codes(products) -> list[str]:
    return [p.product_code for p in products]


def _page(p) -> str:
    return f"/api/v1/master-data/products/{p.id}/compatibility"


# --------------------------------------------------------------------------- table


def test_kind_defaults_to_seat_cover(db):
    stem = _stem()
    cover, wc = _p(db, f"{stem}C"), _p(db, f"{stem}W")
    link = ProductCompatibility(
        company_id=cover.company_id,
        seat_cover_product_id=cover.id,
        wc_product_id=wc.id,
        source="manual",
    )
    db.add(link)
    db.flush()
    db.refresh(link)
    assert link.kind == "seat_cover"


def test_unknown_kind_is_refused_by_the_table(db):
    stem = _stem()
    a, b = _p(db, f"{stem}M"), _p(db, f"{stem}B")
    db.add(
        ProductCompatibility(
            company_id=a.company_id,
            seat_cover_product_id=a.id,
            wc_product_id=b.id,
            source="manual",
            kind="lamp",
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


def test_same_pair_same_kind_twice_is_refused_other_kind_allowed(db):
    stem = _stem()
    mirror, cabinet = _p(db, f"{stem}M"), _p(db, f"{stem}B")
    _link(db, mirror, cabinet, "mirror")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                ProductCompatibility(
                    company_id=mirror.company_id,
                    seat_cover_product_id=mirror.id,
                    wc_product_id=cabinet.id,
                    source="manual",
                    kind="mirror",
                )
            )
            db.flush()
    # The same two products under another kind are a different link.
    _link(db, mirror, cabinet, "shelf")
    _link(db, mirror, cabinet, "seat_cover")


# --------------------------------------------------------------------------- reads


def test_covers_for_and_wcs_for_ignore_mirror_and_shelf_links(db):
    stem = _stem()
    cabinet = _p(db, f"{stem}B")
    mirror, shelf = _p(db, f"{stem}M"), _p(db, f"{stem}S")
    cover = _p(db, f"{stem}SEAT")
    _link(db, mirror, cabinet, "mirror")
    _link(db, shelf, cabinet, "shelf")
    _link(db, cover, cabinet, "seat_cover")
    assert _codes(compat.covers_for(db, [cabinet.id])[cabinet.id]) == [cover.product_code]
    assert mirror.id not in compat.wcs_for(db, [mirror.id])
    assert shelf.id not in compat.wcs_for(db, [shelf.id])
    assert compat.wcs_for(db, [cover.id])[cover.id][0].id == cabinet.id


def test_a_cabinet_with_only_a_mirror_has_no_seat_cover(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    _link(db, mirror, cabinet, "mirror")
    assert compat.covers_for(db, [cabinet.id]) == {}
    assert compat.wcs_for(db, [mirror.id]) == {}


def test_accessories_for_and_hosts_for_by_kind_ordered_by_code(db):
    stem = _stem()
    cabinet = _p(db, f"{stem}B")
    m_b, m_a = _p(db, f"{stem}MB"), _p(db, f"{stem}MA")
    shelf = _p(db, f"{stem}S")
    _link(db, m_b, cabinet, "mirror")
    _link(db, m_a, cabinet, "mirror")
    _link(db, shelf, cabinet, "shelf")

    mirrors = compat.accessories_for(db, [cabinet.id], "mirror")
    assert _codes(mirrors[cabinet.id]) == [m_a.product_code, m_b.product_code]
    shelves = compat.accessories_for(db, [cabinet.id], "shelf")
    assert _codes(shelves[cabinet.id]) == [shelf.product_code]
    assert compat.accessories_for(db, [cabinet.id], "seat_cover") == {}

    hosts = compat.hosts_for(db, [m_a.id], "mirror")
    assert _codes(hosts[m_a.id]) == [cabinet.product_code]
    assert compat.hosts_for(db, [m_a.id], "shelf") == {}


def test_list_for_product_rows_carry_kind_and_both_sides_hold_every_kind(db):
    stem = _stem()
    cabinet, mirror, shelf = _p(db, f"{stem}B"), _p(db, f"{stem}M"), _p(db, f"{stem}S")
    cover = _p(db, f"{stem}SEAT")
    other_cabinet = _p(db, f"{_stem('ZZBF')}B")
    _link(db, mirror, cabinet, "mirror", source=compat.SOURCE_FLYER)
    _link(db, shelf, cabinet, "shelf", source=compat.SOURCE_FLYER)
    _link(db, cover, cabinet, "seat_cover")
    _link(db, mirror, other_cabinet, "mirror")

    on_cabinet = compat.list_for_product(db, cabinet.id)
    assert on_cabinet["fits"] == []
    # Ordered by kind (seat_cover, mirror, shelf), then code.
    assert [(r["kind"], r["product_code"]) for r in on_cabinet["covers"]] == [
        ("seat_cover", cover.product_code),
        ("mirror", mirror.product_code),
        ("shelf", shelf.product_code),
    ]
    assert set(on_cabinet["covers"][1]) == {
        "id", "product_id", "product_code", "product_name", "source", "is_discontinued", "kind",
    }

    on_mirror = compat.list_for_product(db, mirror.id)
    assert on_mirror["covers"] == []
    assert {(r["kind"], r["product_code"]) for r in on_mirror["fits"]} == {
        ("mirror", cabinet.product_code),
        ("mirror", other_cabinet.product_code),
    }


# --------------------------------------------------------------------------- add_manual


def test_add_manual_mirror_from_the_cabinet_page(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    row = compat.add_manual(db, cabinet.id, mirror.id, "accessory", None, kind="mirror")
    assert row["kind"] == "mirror"
    link = db.get(ProductCompatibility, row["id"])
    assert (link.seat_cover_product_id, link.wc_product_id) == (mirror.id, cabinet.id)
    assert (link.kind, link.source) == ("mirror", "manual")


def test_add_manual_mirror_from_the_mirror_page(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    row = compat.add_manual(db, mirror.id, cabinet.id, "host", None, kind="mirror")
    assert row["kind"] == "mirror"
    link = db.get(ProductCompatibility, row["id"])
    assert (link.seat_cover_product_id, link.wc_product_id) == (mirror.id, cabinet.id)


def test_add_manual_shelf_both_roles(db):
    stem = _stem()
    cabinet, shelf, cabinet2 = _p(db, f"{stem}B"), _p(db, f"{stem}S"), _p(db, f"{stem}B2")
    compat.add_manual(db, cabinet.id, shelf.id, "accessory", None, kind="shelf")
    compat.add_manual(db, shelf.id, cabinet2.id, "host", None, kind="shelf")
    assert _codes(compat.hosts_for(db, [shelf.id], "shelf")[shelf.id]) == sorted(
        [cabinet.product_code, cabinet2.product_code]
    )


def test_a_sc_code_may_host_a_mirror_link(db):
    stem = _stem()
    host, mirror = _p(db, f"{stem}-SC"), _p(db, f"{stem}M")
    row = compat.add_manual(db, host.id, mirror.id, "accessory", None, kind="mirror")
    assert row["kind"] == "mirror"
    # The guard still holds for seat cover links.
    cover = _p(db, f"{stem}SEAT")
    with pytest.raises(AppException) as err:
        compat.add_manual(db, host.id, cover.id, "accessory", None, kind="seat_cover")
    assert err.value.status_code == 400


def test_duplicate_same_kind_is_409_either_orientation_other_kind_allowed(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    compat.add_manual(db, cabinet.id, mirror.id, "accessory", None, kind="mirror")
    for page, other, role in (
        (cabinet, mirror, "accessory"),
        (mirror, cabinet, "host"),
        (cabinet, mirror, "host"),  # turned round
        (mirror, cabinet, "accessory"),
    ):
        with pytest.raises(AppException) as err:
            compat.add_manual(db, page.id, other.id, role, None, kind="mirror")
        assert err.value.status_code == 409
    # The same pair under another kind is a different link.
    compat.add_manual(db, cabinet.id, mirror.id, "accessory", None, kind="shelf")


def test_legacy_role_with_another_kind_is_400(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    for role in ("wc", "seat_cover"):
        with pytest.raises(AppException) as err:
            compat.add_manual(db, cabinet.id, mirror.id, role, None, kind="mirror")
        assert err.value.status_code == 400


def test_unknown_kind_or_role_is_400(db):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    for role, kind in (("accessory", "lamp"), ("lid", "mirror")):
        with pytest.raises(AppException) as err:
            compat.add_manual(db, cabinet.id, mirror.id, role, None, kind=kind)
        assert err.value.status_code == 400


def test_legacy_roles_still_mean_seat_cover(db):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    row = compat.add_manual(db, wc.id, cover.id, "seat_cover", None)
    assert row["kind"] == "seat_cover"


# --------------------------------------------------------------------------- API


def test_post_with_kind_shelf_is_201_and_carries_kind(db, monkeypatch):
    stem = _stem("ZZBF")
    cabinet, shelf = _p(db, f"{stem}B"), _p(db, f"{stem}S")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(cabinet),
        json={"other_product_id": shelf.id, "other_role": "accessory", "kind": "shelf"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["kind"] == "shelf"
    assert data["product_id"] == shelf.id
    link = db.get(ProductCompatibility, data["id"])
    assert (link.seat_cover_product_id, link.wc_product_id, link.kind) == (
        shelf.id,
        cabinet.id,
        "shelf",
    )


def test_post_from_the_mirror_page_as_host(db, monkeypatch):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(mirror),
        json={"other_product_id": cabinet.id, "other_role": "host", "kind": "mirror"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["kind"] == "mirror"


def test_post_bad_kind_is_422(db, monkeypatch):
    stem = _stem()
    cabinet, mirror = _p(db, f"{stem}B"), _p(db, f"{stem}M")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(cabinet),
        json={"other_product_id": mirror.id, "other_role": "seat_cover", "kind": "lamp"},
    )
    assert response.status_code == 422
    # A valid role does not rescue a bad kind, and a valid kind does not rescue a bad role.
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(cabinet),
        json={"other_product_id": mirror.id, "other_role": "lid", "kind": "mirror"},
    )
    assert response.status_code == 422


def test_post_without_kind_still_makes_a_seat_cover_link(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "seat_cover"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["kind"] == "seat_cover"
    assert db.get(ProductCompatibility, response.json()["data"]["id"]).kind == "seat_cover"


def test_get_rows_carry_kind_and_include_mirror_and_shelf(db, monkeypatch):
    stem = _stem()
    cabinet, mirror, shelf = _p(db, f"{stem}B"), _p(db, f"{stem}M"), _p(db, f"{stem}S")
    _link(db, mirror, cabinet, "mirror", source=compat.SOURCE_FLYER)
    _link(db, shelf, cabinet, "shelf", source=compat.SOURCE_FLYER)
    client = _client(db, {VIEW}, monkeypatch)

    on_cabinet = client.get(_page(cabinet)).json()["data"]
    assert {(r["kind"], r["product_id"]) for r in on_cabinet["covers"]} == {
        ("mirror", mirror.id),
        ("shelf", shelf.id),
    }
    on_mirror = client.get(_page(mirror)).json()["data"]
    assert [(r["kind"], r["product_id"]) for r in on_mirror["fits"]] == [("mirror", cabinet.id)]


def test_each_kind_product_type_is_a_registry_product_type():
    from app.services.product_spec_registry import SPEC_REGISTRY_SEED

    [entry] = [e for e in SPEC_REGISTRY_SEED if e["spec_key"] == "product_type"]
    for kind, nouns in compat.KINDS.items():
        assert nouns.product_type in entry["allowed_values"], kind
    assert compat.KINDS["seat_cover"].product_type == "toilet_seat"
