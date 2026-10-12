"""WC-SEAT-COMPAT: seat cover <-> WC compatibility links.

Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
UAC:  documentation/plans/products/wc-seat-compat-12oct-acceptance-criteria.md.

    GET  /api/v1/master-data/products/{id}/compatibility
         -> {"data": {"covers": [cover row], "fits": [fit row]}}
    POST /api/v1/master-data/products/{id}/compatibility
         {"other_product_id", "other_role": "seat_cover"|"wc", "scope": "sku"|"model"} -> 201 row
    Unlink = server-deferred pending action `product_compatibility.unlink` on the link row.
    GET  /api/v1/master-data/product-compatibility/flyer-proposals
    POST /api/v1/master-data/product-compatibility/flyer-proposals/apply {"keys": [...]}

Resolution for a WC SKU (owner decisions a, b, 12 Oct): its own exact links if it has any;
else nothing for a colour SKU (BL / MBL / GY / RG segment) or a spare (SC segment); else the
links of its model (`family_code`).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app  # noqa: E402

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.base import set_company_scope
from app.models.product_compatibility import ProductCompatibility
from app.models.user import User, UserStatus
from app.services import product_compatibility_service as compat
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
import app.services.record_actions  # noqa: F401  (registers the actions)
from app.services.form_action_registry import get_action
from app.services.seat_cover_flyer_data import FLYER_ROWS, flyer_pairs
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
    # Letters then digits, so `family_code_for` reads a model out of it.
    return f"{prefix}{unique_code(alpha=True)[:10].upper()}9"


def _model_link(db, cover, family_code, source=compat.SOURCE_MANUAL):
    link = ProductCompatibility(
        company_id=cover.company_id,
        seat_cover_product_id=cover.id,
        wc_family_code=family_code,
        source=source,
    )
    db.add(link)
    db.flush()
    return link


def _sku_link(db, cover, wc, source=compat.SOURCE_MANUAL):
    link = ProductCompatibility(
        company_id=cover.company_id,
        seat_cover_product_id=cover.id,
        wc_product_id=wc.id,
        source=source,
    )
    db.add(link)
    db.flush()
    return link


def _cover_codes(result: dict, wc_id: str) -> list[str]:
    return sorted(p.product_code for p in result.get(wc_id, []))


# --------------------------------------------------------------------------- flyer data


def test_flyer_data_is_the_49_printed_pairs():
    pairs = flyer_pairs()
    assert len(FLYER_ROWS) == 35
    assert len(pairs) == 49
    assert len({cover for _, _, cover in pairs}) == 36
    assert len({wc for _, wc, _ in pairs}) == 48
    # Row 6: both covers for SRTWC8122 (owner decision c).
    assert {(wc, cover) for row, wc, cover in pairs if row == 6} == {
        ("SRTWC8122", "SRTSCBD333-UF"),
        ("SRTWC8122", "SRTSCBD333A-UF"),
    }
    # Each WC code has exactly one cover except SRTWC8122.
    from collections import Counter

    per_wc = Counter(wc for _, wc, _ in pairs)
    assert {wc for wc, n in per_wc.items() if n > 1} == {"SRTWC8122"}


def test_flyer_data_never_holds_an_intelligent_seat_wc():
    wcs = {wc for _, wc, _ in flyer_pairs()}
    for smart in ("SRTWC185", "SRTWC187", "SRTWC189", "SRTWC189-G2", "SRTWC191", "SRTWC191-G2", "SRTWC195"):
        assert smart not in wcs


# --------------------------------------------------------------------------- table


def test_check_rejects_a_link_with_both_targets(db):
    stem = _stem()
    cover, wc = _p(db, f"{stem}C"), _p(db, f"{stem}W-RL")
    db.add(
        ProductCompatibility(
            company_id=cover.company_id,
            seat_cover_product_id=cover.id,
            wc_family_code=wc.family_code,
            wc_product_id=wc.id,
            source="manual",
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


def test_check_rejects_a_link_with_no_target(db):
    cover = _p(db, f"{_stem()}C")
    db.add(
        ProductCompatibility(
            company_id=cover.company_id, seat_cover_product_id=cover.id, source="manual"
        )
    )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.flush()


def test_check_rejects_an_unknown_source(db):
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


# --------------------------------------------------------------------------- resolution


def test_segment_rules():
    assert compat.is_colour_sku("SRTWC8383-RL-MBL")
    assert compat.is_colour_sku("SRTWC8066-S-BL")
    assert compat.is_colour_sku("SRTWC8318 - GY")
    assert compat.is_colour_sku("srtwc8383-rg")
    assert not compat.is_colour_sku("SRTWC8383-RL-UF")
    assert not compat.is_colour_sku("SRTWC8383")
    # Whole segments only: BLUE is not BL, and the model's own letters never count.
    assert not compat.is_colour_sku("SRTWC8383-BLUE")
    assert not compat.is_colour_sku("SRTBL8383")
    assert compat.is_spare_sku("SRTWC8383-SC")
    assert compat.is_spare_sku("SRTWC8383-RL-SC")
    assert not compat.is_spare_sku("SRTWC8383-RL-UF")


def test_plain_sku_gets_its_model_link(db):
    stem = _stem()
    cover = _p(db, f"{stem}SEAT")
    wc = _p(db, f"{stem}-RL-UF")
    _model_link(db, cover, wc.family_code)
    assert _cover_codes(compat.covers_for(db, [wc.id]), wc.id) == [cover.product_code]


def test_exact_link_beats_the_model_link(db):
    stem = _stem()
    white, black = _p(db, f"{stem}SEATW"), _p(db, f"{stem}SEATB-MBL")
    wc_white = _p(db, f"{stem}-RL-UF")
    wc_black = _p(db, f"{stem}-RL-MBL")
    _model_link(db, white, wc_white.family_code)
    _sku_link(db, black, wc_black)
    result = compat.covers_for(db, [wc_white.id, wc_black.id])
    assert _cover_codes(result, wc_white.id) == [white.product_code]
    # Owner decision a: the colour SKU shows only its colour-matched cover.
    assert _cover_codes(result, wc_black.id) == [black.product_code]


def test_colour_sku_without_its_own_link_gets_nothing(db):
    stem = _stem()
    white = _p(db, f"{stem}SEAT")
    wc_white = _p(db, f"{stem}-RL-UF")
    wc_grey = _p(db, f"{stem}-RL-GY")
    _model_link(db, white, wc_white.family_code)
    # Owner decision b: no white fallback for an unlisted colour.
    assert compat.covers_for(db, [wc_grey.id]).get(wc_grey.id, []) == []


def test_spare_sku_never_inherits_the_model_link(db):
    stem = _stem()
    cover = _p(db, f"{stem}SEAT")
    wc = _p(db, f"{stem}-RL-UF")
    spare = _p(db, f"{stem}-SC")
    _model_link(db, cover, wc.family_code)
    assert compat.covers_for(db, [spare.id]).get(spare.id, []) == []


def test_two_covers_on_one_model_both_show(db):
    stem = _stem()
    a, b = _p(db, f"{stem}SEAT-UF"), _p(db, f"{stem}SEATA-UF")
    wc = _p(db, f"{stem}-UF")
    _model_link(db, a, wc.family_code)
    _model_link(db, b, wc.family_code)
    assert _cover_codes(compat.covers_for(db, [wc.id]), wc.id) == sorted(
        [a.product_code, b.product_code]
    )


def test_unlinked_wc_gets_nothing(db):
    wc = _p(db, f"{_stem()}-UF")
    assert compat.covers_for(db, [wc.id]) == {}


def test_model_link_never_crosses_companies(db):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    cover = _p(db, f"{stem}SEAT")
    wc_here = _p(db, f"{stem}-UF")
    wc_there = _p(db, f"{stem}-UF", company_id=MOCHA_ID)
    _model_link(db, cover, wc_here.family_code)
    result = compat.covers_for(db, [wc_here.id, wc_there.id])
    assert _cover_codes(result, wc_here.id) == [cover.product_code]
    assert result.get(wc_there.id, []) == []


def test_wcs_for_cover_lists_models_and_skus(db):
    stem = _stem()
    cover = _p(db, f"{stem}SEAT-BL")
    model_wc = _p(db, f"{stem}-UF")
    other_stem = _stem()
    sku_wc = _p(db, f"{other_stem}-RL-BL")
    _model_link(db, cover, model_wc.family_code)
    _sku_link(db, cover, sku_wc)
    fits = compat.wcs_for(db, [cover.id])[cover.id]
    assert {(f["scope"], f.get("family_code"), f.get("product_code")) for f in fits} == {
        ("model", model_wc.family_code, None),
        ("sku", None, sku_wc.product_code),
    }


# --------------------------------------------------------------------------- product page API


def _page(p) -> str:
    return f"/api/v1/master-data/products/{p.id}/compatibility"


def test_get_requires_view(db, monkeypatch):
    wc = _p(db, f"{_stem()}-UF")
    assert _client(db, set(), monkeypatch).get(_page(wc)).status_code == 403


def test_get_on_a_wc_lists_resolved_covers_with_their_scope(db, monkeypatch):
    stem = _stem()
    white, black = _p(db, f"{stem}SEAT"), _p(db, f"{stem}SEATB-BL")
    wc = _p(db, f"{stem}-RL-UF")
    wc_black = _p(db, f"{stem}-RL-BL")
    _model_link(db, white, wc.family_code, source=compat.SOURCE_FLYER)
    _sku_link(db, black, wc_black)
    client = _client(db, {VIEW}, monkeypatch)

    data = client.get(_page(wc)).json()["data"]
    [row] = data["covers"]
    assert row["product_id"] == white.id
    assert row["product_code"] == white.product_code
    assert row["scope"] == "model"
    assert row["family_code"] == wc.family_code
    assert row["source"] == "flyer"
    assert row["id"]
    assert data["fits"] == []

    [black_row] = client.get(_page(wc_black)).json()["data"]["covers"]
    assert black_row["product_id"] == black.id
    assert black_row["scope"] == "sku"


def test_get_on_a_cover_lists_what_it_fits(db, monkeypatch):
    stem = _stem()
    cover = _p(db, f"{stem}SEAT")
    wc1 = _p(db, f"{stem}-RL-UF")
    _p(db, f"{stem}-SL-UF")
    _p(db, f"{stem}-RL-MBL")  # colour SKU: not covered by the model link
    _p(db, f"{stem}-SC")  # spare: not covered
    _model_link(db, cover, wc1.family_code)
    data = _client(db, {VIEW}, monkeypatch).get(_page(cover)).json()["data"]
    assert data["covers"] == []
    [fit] = data["fits"]
    assert fit["scope"] == "model"
    assert fit["family_code"] == wc1.family_code
    assert fit["sku_count"] == 2


def test_add_requires_edit(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    response = _client(db, {VIEW}, monkeypatch).post(
        _page(wc), json={"other_product_id": cover.id, "other_role": "seat_cover", "scope": "sku"}
    )
    assert response.status_code == 403


def test_add_from_a_wc_page_whole_model(db, monkeypatch):
    stem = _stem()
    wc, sibling, cover = _p(db, f"{stem}-RL-UF"), _p(db, f"{stem}-SL-UF"), _p(db, f"{stem}SEAT")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(wc),
        json={"other_product_id": cover.id, "other_role": "seat_cover", "scope": "model"},
    )
    assert response.status_code == 201, response.text
    row = response.json()["data"]
    link = db.get(ProductCompatibility, row["id"])
    assert link.seat_cover_product_id == cover.id
    assert link.wc_family_code == wc.family_code
    assert link.wc_product_id is None
    assert link.source == "manual"
    assert link.created_by
    assert _cover_codes(compat.covers_for(db, [sibling.id]), sibling.id) == [cover.product_code]


def test_add_from_a_cover_page_one_sku(db, monkeypatch):
    stem = _stem()
    cover, wc = _p(db, f"{stem}SEAT-MBL"), _p(db, f"{stem}-RL-MBL")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(cover), json={"other_product_id": wc.id, "other_role": "wc", "scope": "sku"}
    )
    assert response.status_code == 201, response.text
    link = db.get(ProductCompatibility, response.json()["data"]["id"])
    assert (link.seat_cover_product_id, link.wc_product_id) == (cover.id, wc.id)


def test_add_duplicate_is_409(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    body = {"other_product_id": cover.id, "other_role": "seat_cover", "scope": "sku"}
    assert client.post(_page(wc), json=body).status_code == 201
    assert client.post(_page(wc), json=body).status_code == 409


def test_add_self_is_422(db, monkeypatch):
    wc = _p(db, f"{_stem()}-UF")
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(wc), json={"other_product_id": wc.id, "other_role": "seat_cover", "scope": "sku"}
    )
    assert response.status_code == 422


def test_add_bad_role_or_scope_is_422(db, monkeypatch):
    stem = _stem()
    wc, cover = _p(db, f"{stem}-UF"), _p(db, f"{stem}SEAT")
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    for body in (
        {"other_product_id": cover.id, "other_role": "lid", "scope": "sku"},
        {"other_product_id": cover.id, "other_role": "seat_cover", "scope": "family"},
    ):
        assert client.post(_page(wc), json=body).status_code == 422


def test_add_other_company_product_is_404(db, monkeypatch):
    seed_mocha(db)
    stem = _stem()
    wc = _p(db, f"{stem}-UF")
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    foreign = _p(db, f"{stem}SEAT", company_id=MOCHA_ID)
    response = _client(db, {VIEW, EDIT}, monkeypatch).post(
        _page(wc), json={"other_product_id": foreign.id, "other_role": "seat_cover", "scope": "sku"}
    )
    assert response.status_code == 404


def test_unlink_is_a_registered_reversible_action(db):
    action = get_action("product_compatibility.unlink")
    assert action.entity_types == ("product_compatibility",)
    assert action.permission == EDIT


def test_unlink_deletes_the_link(db):
    stem = _stem()
    cover, wc = _p(db, f"{stem}SEAT"), _p(db, f"{stem}-UF")
    link = _sku_link(db, cover, wc)
    link_id = link.id
    compat.unlink(db, link_id)
    assert db.get(ProductCompatibility, link_id) is None
    # Already gone: ends quietly.
    compat.unlink(db, link_id)


def _park(client, entity_id):
    return client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "product_compatibility.unlink",
            "entity_type": "product_compatibility",
            "entity_id": entity_id,
            "payload": {},
        },
    )


def test_unlink_parks_and_refuses_unknown_link(db, monkeypatch):
    stem = _stem()
    link = _sku_link(db, _p(db, f"{stem}SEAT"), _p(db, f"{stem}-UF"))
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    parked = _park(client, link.id)
    assert parked.status_code == 202, parked.text
    assert db.get(ProductCompatibility, link.id) is not None
    assert _park(client, str(uuid.uuid4())).status_code == 404
    assert _park(client, "not-a-uuid").status_code in (400, 404, 422)
    assert _park(_client(db, {VIEW}, monkeypatch), link.id).status_code == 403


def test_another_companys_product_and_link_are_404(db, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    cover = _p(db, f"{stem}SEAT", company_id=MOCHA_ID)
    wc = _p(db, f"{stem}-UF", company_id=MOCHA_ID)
    link = _sku_link(db, cover, wc)
    client = _client(db, {VIEW, EDIT}, monkeypatch)  # Sorento only
    assert client.get(_page(wc)).status_code == 404
    assert _park(client, link.id).status_code == 404
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    assert db.get(ProductCompatibility, link.id) is not None


# --------------------------------------------------------------------------- flyer proposals


FLYER_URL = "/api/v1/master-data/product-compatibility/flyer-proposals"


@pytest.fixture()
def flyer_catalogue(db):
    """A slice of the real catalogue shape: flyer codes with their SKU suffixes."""
    codes = [
        "SRTSCBD331-UF",
        "SRTSCBD823-MBL",
        "SRTSCBD824-BL",
        "SRTSCBD333-UF",
        "SRTSCBD333A-UF",
        "SRTSCBD841-BL",
        "SRTSCBD407",
        "SRTWC8383-RL-UF",
        "SRTWC8383-SL-UF",
        "SRTWC8383-RL-MBL",
        "SRTWC8383-RL-BL",
        "SRTWC8383-SC",
        "SRTWC8152-RL-BL",
        "SRTWC8122-UF",
        "SRTWC8066-S-BL",
        "SRTWC8066-RL",
    ]
    return {code: _p(db, code) for code in codes}


def _proposals(client) -> list[dict]:
    response = client.get(FLYER_URL)
    assert response.status_code == 200, response.text
    return response.json()["data"]["proposals"]


def _by_pair(proposals, wc, cover) -> dict:
    [hit] = [p for p in proposals if p["flyer_wc_code"] == wc and p["flyer_cover_code"] == cover]
    return hit


def test_proposals_require_view(db, monkeypatch):
    assert _client(db, set(), monkeypatch).get(FLYER_URL).status_code == 403


def test_proposals_one_per_flyer_pair(db, flyer_catalogue, monkeypatch):
    proposals = _proposals(_client(db, {VIEW}, monkeypatch))
    assert len(proposals) == 49
    assert len({p["key"] for p in proposals}) == 49


def test_plain_model_proposal_lists_the_skus_it_covers(db, flyer_catalogue, monkeypatch):
    proposals = _proposals(_client(db, {VIEW}, monkeypatch))
    row = _by_pair(proposals, "SRTWC8383", "SRTSCBD331-UF")
    assert row["flyer_row"] == 3
    assert row["scope"] == "model"
    assert row["family_code"] == "SRTWC8383"
    assert row["status"] == "ready"
    assert [c["product_code"] for c in row["covers"]] == ["SRTSCBD331-UF"]
    skus = {s["product_code"]: s for s in row["skus"]}
    assert skus["SRTWC8383-RL-UF"]["covered"] is True
    assert skus["SRTWC8383-SL-UF"]["covered"] is True
    assert (skus["SRTWC8383-RL-MBL"]["covered"], skus["SRTWC8383-RL-MBL"]["reason"]) == (False, "colour")
    assert (skus["SRTWC8383-SC"]["covered"], skus["SRTWC8383-SC"]["reason"]) == (False, "spare")


def test_colour_proposal_maps_to_the_exact_sku_by_whole_segment(db, flyer_catalogue, monkeypatch):
    proposals = _proposals(_client(db, {VIEW}, monkeypatch))
    mbl = _by_pair(proposals, "SRTWC8383-MBL", "SRTSCBD823-MBL")
    assert mbl["scope"] == "sku"
    assert [s["product_code"] for s in mbl["skus"]] == ["SRTWC8383-RL-MBL"]
    bl = _by_pair(proposals, "SRTWC8383-BL", "SRTSCBD824-BL")
    # MBL is never a match for BL.
    assert [s["product_code"] for s in bl["skus"]] == ["SRTWC8383-RL-BL"]
    s_bl = _by_pair(proposals, "SRTWC8066-S-BL", "SRTSCBD841-BL")
    assert [s["product_code"] for s in s_bl["skus"]] == ["SRTWC8066-S-BL"]


def test_missing_cover_or_wc_is_not_tickable(db, flyer_catalogue, monkeypatch):
    proposals = _proposals(_client(db, {VIEW}, monkeypatch))
    # Cover SRTSCBD330-UF is not in this catalogue.
    assert _by_pair(proposals, "SRTWC8088", "SRTSCBD330-UF")["status"] == "cover_missing"
    # Cover exists, but no SRTWC8318 SKU does.
    assert _by_pair(proposals, "SRTWC8318-MBL", "SRTSCBD834-MBL")["status"] == "cover_missing"
    # SRTSCBD824-BL exists and SRTWC8152-RL-BL carries the printed BL segment.
    assert _by_pair(proposals, "SRTWC8152-BL", "SRTSCBD824-BL")["status"] == "ready"
    # SRTSCBD407 exists, SRTWC286 does not.
    assert _by_pair(proposals, "SRTWC286", "SRTSCBD407")["status"] == "wc_missing"


def test_apply_requires_edit(db, flyer_catalogue, monkeypatch):
    client = _client(db, {VIEW}, monkeypatch)
    assert client.post(f"{FLYER_URL}/apply", json={"keys": []}).status_code == 403


def test_apply_writes_only_the_ticked_pairs(db, flyer_catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    proposals = _proposals(client)
    keys = [
        _by_pair(proposals, "SRTWC8383", "SRTSCBD331-UF")["key"],
        _by_pair(proposals, "SRTWC8383-MBL", "SRTSCBD823-MBL")["key"],
        _by_pair(proposals, "SRTWC8122", "SRTSCBD333-UF")["key"],
        _by_pair(proposals, "SRTWC8122", "SRTSCBD333A-UF")["key"],
    ]
    response = client.post(f"{FLYER_URL}/apply", json={"keys": keys})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["applied"] == 4

    c = flyer_catalogue
    result = compat.covers_for(
        db,
        [c["SRTWC8383-RL-UF"].id, c["SRTWC8383-RL-MBL"].id, c["SRTWC8383-RL-BL"].id, c["SRTWC8122-UF"].id],
    )
    assert _cover_codes(result, c["SRTWC8383-RL-UF"].id) == ["SRTSCBD331-UF"]
    assert _cover_codes(result, c["SRTWC8383-RL-MBL"].id) == ["SRTSCBD823-MBL"]
    # 8383-BL was not ticked, and a colour SKU gets no white fallback.
    assert result.get(c["SRTWC8383-RL-BL"].id, []) == []
    assert _cover_codes(result, c["SRTWC8122-UF"].id) == ["SRTSCBD333-UF", "SRTSCBD333A-UF"]
    links = db.query(ProductCompatibility).all()
    assert {link.source for link in links} == {"flyer"}

    # Now linked, and a second apply writes nothing.
    assert _by_pair(_proposals(client), "SRTWC8383", "SRTSCBD331-UF")["status"] == "linked"
    again = client.post(f"{FLYER_URL}/apply", json={"keys": keys}).json()["data"]
    assert again["applied"] == 0
    assert again["already_linked"] == 4


def test_apply_skips_a_pair_that_cannot_resolve(db, flyer_catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    key = _by_pair(_proposals(client), "SRTWC286", "SRTSCBD407")["key"]
    data = client.post(f"{FLYER_URL}/apply", json={"keys": [key]}).json()["data"]
    assert data["applied"] == 0
    assert data["skipped"] == [{"key": key, "reason": "wc_missing"}]


def test_apply_unknown_key_is_422(db, flyer_catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    response = client.post(f"{FLYER_URL}/apply", json={"keys": ["99|SRTWC1|SRTSC1"]})
    assert response.status_code == 422
