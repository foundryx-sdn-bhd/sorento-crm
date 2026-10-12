"""WC-SEAT-COMPAT: seat cover -> WC compatibility links, SKU to SKU.

Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
UAC:  documentation/plans/products/wc-seat-compat-12oct-acceptance-criteria.md.

    GET  /api/v1/master-data/products/{id}/compatibility
         -> {"data": {"covers": [row], "fits": [row]}}
    POST /api/v1/master-data/products/{id}/compatibility
         {"other_product_id", "other_role": "seat_cover"|"wc"} -> 201 row
    Unlink = server-deferred pending action `product_compatibility.unlink` on the link row.
    GET  /api/v1/master-data/product-compatibility/flyer-review
    POST /api/v1/master-data/product-compatibility/flyer-review/apply {"wc_product_ids": [...]}
    Retire = server-deferred pending action `seat_cover_spec.retire` on the registry row.

Owner ruling 12 Oct: match only what the flyer prints; no inheritance, no guesses. A WC SKU
has a cover only when a link names it. A SKU whose mapping to a printed code is not
unambiguous is UNRESOLVED on the review screen and cannot be applied. A `-SC` SKU is a seat
cover, never a WC.
"""
from __future__ import annotations

import uuid
from collections import Counter

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app  # noqa: E402

from app.services import record_actions  # noqa: F401  (registers the actions)
from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.audit import AuditLog
from app.models.base import set_company_scope
from app.models.product_compatibility import ProductCompatibility
from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
from app.models.user import User, UserStatus
from app.services import product_compatibility_service as compat
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.form_action_grace import WINDOW_DESTRUCTIVE, WINDOW_REVERSIBLE
from app.services.form_action_registry import get_action
from app.services.seat_cover_flyer_data import FLYER_ROWS, flyer_pairs
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"
SPEC_EDIT = "master_data.spec_registry.edit"


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
    per_wc = Counter(wc for _, wc, _ in pairs)
    assert {wc for wc, n in per_wc.items() if n > 1} == {"SRTWC8122"}


def test_flyer_data_never_holds_an_intelligent_seat_wc():
    wcs = {wc for _, wc, _ in flyer_pairs()}
    for smart in ("SRTWC185", "SRTWC187", "SRTWC189", "SRTWC189-G2", "SRTWC191", "SRTWC191-G2", "SRTWC195"):
        assert smart not in wcs


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


# --------------------------------------------------------------------------- flyer review


REVIEW_URL = "/api/v1/master-data/product-compatibility/flyer-review"

CATALOGUE = [
    "SRTSCBD331-UF",
    "SRTSCBD823-MBL",
    "SRTSCBD824-BL",
    "SRTSCBD333-UF",
    "SRTSCBD333A-UF",
    "SRTSCBD841-BL",
    "SRTSCBD407",
    "SRTSCBD323",
    "SRTSCBD326",
    # SRTWC8383: plain row 3, colour rows 24 (MBL), 25 (BL), 28 (GY), 29 (RG).
    "SRTWC8383-RL-UF",
    "SRTWC8383-SH-UF",
    "SRTWC8383-RL-MBL",
    "SRTWC8383-RL-BL",
    "SRTWC8383-RL-GM",  # a finish (gunmetal) the flyer never prints for 8383
    "SRTWC8383-SH-MK",  # a finish the spec engine does not know, but the catalogue uses
    "SRTWC8383-RL-FT",  # a segment nobody has said is a plain variant marker
    "SRTWC8383-RL-UF-150-NEW",  # trap, seat, size and packaging markers only
    "SRTWC8383-RL-MBL-BL",  # fits printed -MBL and -BL equally
    "SRTWC8383-SC",  # a seat cover, never a WC
    "SRTWC8152-RL-BL",
    "SRTWC8122-UF",
    # SRTWC8066: plain row 15, 8066-S colour rows 32-35.
    "SRTWC8066-RL",
    "SRTWC8066-S-BL",
    "SRTWC8066-S",  # a plain 8066-S: S is only ever printed with a colour
    "SRTWC8318",  # the exact printed code
    "SRTWC202-S-PINK",  # row 22 prints SRTWC202 with the note "Pink"
    "SRTWC203-S-PINK",  # row 23 prints SRTWC203 as Blue: a pink one is not printed
]


@pytest.fixture()
def catalogue(db):
    return {code: _p(db, code) for code in CATALOGUE}


def _review(client) -> dict:
    response = client.get(REVIEW_URL)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _model(review, family) -> dict:
    [hit] = [m for m in review["models"] if m["family_code"] == family]
    return hit


def _sku(model, code) -> dict:
    [hit] = [s for s in model["skus"] if s["product_code"] == code]
    return hit


def test_review_requires_view(db, monkeypatch):
    assert _client(db, set(), monkeypatch).get(REVIEW_URL).status_code == 403


def test_review_lists_every_printed_model_once(db, catalogue, monkeypatch):
    review = _review(_client(db, {VIEW}, monkeypatch))
    families = [m["family_code"] for m in review["models"]]
    assert len(families) == len(set(families))
    # 48 printed codes fold onto their models: SRTWC8383 carries five printed codes.
    m8383 = _model(review, "SRTWC8383")
    assert [(p["flyer_row"], p["wc_code"]) for p in m8383["printed"]] == [
        (3, "SRTWC8383"),
        (24, "SRTWC8383-MBL"),
        (25, "SRTWC8383-BL"),
        (28, "SRTWC8383-GY"),
        (29, "SRTWC8383-RG"),
    ]
    printed_pairs = sum(len(p["covers"]) for m in review["models"] for p in m["printed"])
    assert printed_pairs == 49


def test_plain_and_colour_codes_map_to_skus_by_whole_segment(db, catalogue, monkeypatch):
    m = _model(_review(_client(db, {VIEW}, monkeypatch)), "SRTWC8383")
    for code in ("SRTWC8383-RL-UF", "SRTWC8383-SH-UF"):
        sku = _sku(m, code)
        assert (sku["status"], sku["printed_code"], sku["cover_codes"]) == (
            "ready",
            "SRTWC8383",
            ["SRTSCBD331-UF"],
        )
    mbl = _sku(m, "SRTWC8383-RL-MBL")
    assert (mbl["status"], mbl["printed_code"], mbl["cover_codes"]) == (
        "ready",
        "SRTWC8383-MBL",
        ["SRTSCBD823-MBL"],
    )
    # MBL is never a match for BL.
    bl = _sku(m, "SRTWC8383-RL-BL")
    assert (bl["printed_code"], bl["cover_codes"]) == ("SRTWC8383-BL", ["SRTSCBD824-BL"])
    sized = _sku(m, "SRTWC8383-RL-UF-150-NEW")
    assert (sized["status"], sized["printed_code"]) == ("ready", "SRTWC8383")
    assert sized["extra_segments"] == ["150", "NEW", "RL", "UF"]


def test_a_finish_the_row_prints_as_a_note_resolves(db, catalogue, monkeypatch):
    review = _review(_client(db, {VIEW}, monkeypatch))
    pink = _sku(_model(review, "SRTWC202"), "SRTWC202-S-PINK")
    assert (pink["status"], pink["printed_code"], pink["cover_codes"]) == (
        "ready",
        "SRTWC202",
        ["SRTSCBD326"],
    )


def test_ambiguous_skus_are_unresolved_and_never_linked(db, catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    review = _review(client)
    pink = _sku(_model(review, "SRTWC8383"), "SRTWC8383-RL-GM")
    assert (pink["status"], pink["reason"], pink["printed_code"]) == (
        "unresolved",
        "finish_not_printed",
        None,
    )
    for code, reason in (
        ("SRTWC8383-RL-MBL-BL", "ambiguous"),
        ("SRTWC8383-SH-MK", "finish_not_printed"),
        ("SRTWC8383-RL-FT", "unknown_segment"),
    ):
        sku = _sku(_model(review, "SRTWC8383"), code)
        assert (sku["status"], sku["reason"]) == ("unresolved", reason), code
    blue_row = _sku(_model(review, "SRTWC203"), "SRTWC203-S-PINK")
    assert (blue_row["status"], blue_row["reason"]) == ("unresolved", "finish_not_printed")
    s_plain = _sku(_model(review, "SRTWC8066"), "SRTWC8066-S")
    assert (s_plain["status"], s_plain["reason"]) == ("unresolved", "sub_model_not_printed")
    # 8066-RL is a plain SKU of the plain printed 8066: resolved.
    assert _sku(_model(review, "SRTWC8066"), "SRTWC8066-RL")["status"] == "ready"
    # An apply that names them anyway writes nothing.
    data = client.post(
        f"{REVIEW_URL}/apply",
        json={"wc_product_ids": [catalogue["SRTWC8383-RL-GM"].id, catalogue["SRTWC8066-S"].id]},
    ).json()["data"]
    assert data["linked"] == 0
    assert {s["reason"] for s in data["skipped"]} == {"unresolved"}
    assert db.query(ProductCompatibility).count() == 0


def test_sc_skus_are_never_listed_as_wcs(db, catalogue, monkeypatch):
    m = _model(_review(_client(db, {VIEW}, monkeypatch)), "SRTWC8383")
    assert "SRTWC8383-SC" not in {s["product_code"] for s in m["skus"]}


def test_missing_cover_and_missing_model_are_shown_not_tickable(db, catalogue, monkeypatch):
    review = _review(_client(db, {VIEW}, monkeypatch))
    # SRTWC8088 has no SKU in this catalogue and its cover SRTSCBD330-UF is missing too.
    m8088 = _model(review, "SRTWC8088")
    assert m8088["skus"] == []
    [printed] = m8088["printed"]
    assert printed["covers"] == [{"code": "SRTSCBD330-UF", "found": False}]
    # SRTWC8318 exists exactly; its plain cover SRTSCBD323 exists.
    assert _sku(_model(review, "SRTWC8318"), "SRTWC8318")["status"] == "ready"
    assert review["counts"]["covers_missing"] == 36 - 9


def test_a_sku_whose_cover_is_missing_is_not_tickable(db, monkeypatch):
    _p(db, "SRTWC8088-RL-UF")  # its cover SRTSCBD330-UF is not in the catalogue
    review = _review(_client(db, {VIEW}, monkeypatch))
    assert _sku(_model(review, "SRTWC8088"), "SRTWC8088-RL-UF")["status"] == "cover_missing"


def test_apply_requires_edit(db, catalogue, monkeypatch):
    client = _client(db, {VIEW}, monkeypatch)
    assert client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": []}).status_code == 403


def test_apply_writes_only_the_ticked_skus(db, catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    c = catalogue
    ticked = [c["SRTWC8383-RL-UF"].id, c["SRTWC8383-RL-MBL"].id, c["SRTWC8122-UF"].id]
    response = client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": ticked})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["linked"] == 4  # 331-UF, 823-MBL, 333-UF + 333A-UF
    assert data["skipped"] == []

    result = compat.covers_for(db, [p.id for p in c.values()])
    assert _codes(result[c["SRTWC8383-RL-UF"].id]) == ["SRTSCBD331-UF"]
    assert _codes(result[c["SRTWC8383-RL-MBL"].id]) == ["SRTSCBD823-MBL"]
    assert _codes(result[c["SRTWC8122-UF"].id]) == ["SRTSCBD333-UF", "SRTSCBD333A-UF"]
    # Not ticked: nothing, even for a sibling of a ticked SKU.
    assert c["SRTWC8383-SH-UF"].id not in result
    assert {link.source for link in db.query(ProductCompatibility).all()} == {"flyer"}

    review = _review(client)
    assert _sku(_model(review, "SRTWC8383"), "SRTWC8383-RL-UF")["status"] == "linked"
    again = client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": ticked}).json()["data"]
    assert (again["linked"], again["already_linked"]) == (0, 3)


def test_apply_refuses_a_product_that_is_not_a_printed_wc(db, catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    stray = _p(db, f"{_stem()}-UF")
    data = client.post(
        f"{REVIEW_URL}/apply",
        json={"wc_product_ids": [stray.id, catalogue["SRTWC8383-SC"].id]},
    ).json()["data"]
    assert data["linked"] == 0
    assert {s["reason"] for s in data["skipped"]} == {"not_on_flyer"}


def test_apply_bad_id_is_422(db, catalogue, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    response = client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": ["not-a-uuid"]})
    assert response.status_code == 422


# --------------------------------------------------------------------------- spec retire


@pytest.fixture()
def seat_cover_spec(db, catalogue):
    from app.services.product_spec_write import apply_spec_values

    registry = db.query(ProductSpecRegistry).filter_by(spec_key="seat_cover").one_or_none()
    if registry is None:
        registry = ProductSpecRegistry(
            spec_key="seat_cover",
            label="Seat Cover",
            data_type="enum",
            allowed_values=["SRTSCBD331-UF"],
            source="user",
            is_active=True,
        )
        db.add(registry)
        db.flush()
    apply_spec_values(
        db,
        "SRTWC8383-RL-UF",
        [{"spec_key": "seat_cover", "value": "SRTSCBD331-UF", "source": "human"}],
        commit=False,
    )
    db.flush()
    return registry


def _park_retire(client, registry_id):
    return client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "seat_cover_spec.retire",
            "entity_type": "seat_cover_spec",
            "entity_id": registry_id,
            "payload": {},
        },
    )


def test_retire_is_a_registered_destructive_action():
    action = get_action("seat_cover_spec.retire")
    assert action.entity_types == ("seat_cover_spec",)
    assert action.permission == SPEC_EDIT
    assert action.window == WINDOW_DESTRUCTIVE


def test_review_reports_the_old_spec(db, seat_cover_spec, monkeypatch):
    spec = _review(_client(db, {VIEW}, monkeypatch))["seat_cover_spec"]
    assert spec == {
        "registry_id": seat_cover_spec.id,
        "is_active": True,
        "value_count": 1,
        "can_retire": False,  # no flyer link applied yet
    }


def test_retire_is_refused_before_any_flyer_link(db, seat_cover_spec, monkeypatch):
    client = _client(db, {VIEW, EDIT, SPEC_EDIT}, monkeypatch)
    assert _park_retire(client, seat_cover_spec.id).status_code == 409


def test_retire_needs_the_spec_registry_edit_permission(db, seat_cover_spec, monkeypatch):
    client = _client(db, {VIEW, EDIT}, monkeypatch)
    assert _park_retire(client, seat_cover_spec.id).status_code == 403


def test_retire_removes_the_values_and_deactivates_the_key(db, catalogue, seat_cover_spec, monkeypatch):
    client = _client(db, {VIEW, EDIT, SPEC_EDIT}, monkeypatch)
    client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": [catalogue["SRTWC8383-RL-UF"].id]})
    assert _review(client)["seat_cover_spec"]["can_retire"] is True
    assert _park_retire(client, seat_cover_spec.id).status_code == 202

    compat.retire_seat_cover_spec(db, seat_cover_spec.id)
    db.refresh(seat_cover_spec)
    assert seat_cover_spec.is_active is False
    holding = (
        db.query(ProductSpecifications)
        .filter(ProductSpecifications.values.has_key("seat_cover"))  # noqa: W601
        .count()
    )
    assert holding == 0
    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "product_spec_registry",
            AuditLog.entity_id == seat_cover_spec.id,
        )
        .all()
    )
    assert audit, "the retire leaves an audit row"
    assert "SRTWC8383-RL-UF" in str(audit[-1].old_values)
    spec = _review(client)["seat_cover_spec"]
    assert (spec["is_active"], spec["value_count"], spec["can_retire"]) == (False, 0, False)


def test_retire_waits_for_every_company_holding_a_value(db, catalogue, seat_cover_spec, monkeypatch):
    """Security review: the retire reaches every company, so one company's flyer apply is
    not enough while another company still holds a value with nothing to replace it."""
    from app.services.product_spec_write import apply_spec_values

    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    _p(db, "SRTWC8383-RL-UF", company_id=MOCHA_ID)
    apply_spec_values(
        db,
        "SRTWC8383-RL-UF",
        [{"spec_key": "seat_cover", "value": "SRTSCBD331-UF", "source": "human"}],
        commit=False,
    )
    db.flush()
    client = _client(db, {VIEW, EDIT, SPEC_EDIT}, monkeypatch)  # Sorento only
    client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": [catalogue["SRTWC8383-RL-UF"].id]})
    assert _review(client)["seat_cover_spec"]["can_retire"] is False
    assert _park_retire(client, seat_cover_spec.id).status_code == 409


def test_apply_never_reaches_another_companys_sku(db, catalogue, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    foreign_cover = _p(db, "SRTSCBD331-UF", company_id=MOCHA_ID)
    foreign_wc = _p(db, "SRTWC8383-RL-UF", company_id=MOCHA_ID)
    client = _client(db, {VIEW, EDIT}, monkeypatch)  # Sorento only
    data = client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": [foreign_wc.id]}).json()["data"]
    assert data["linked"] == 0
    assert data["skipped"] == [{"product_id": foreign_wc.id, "reason": "not_on_flyer"}]
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    assert (
        db.query(ProductCompatibility)
        .filter(ProductCompatibility.seat_cover_product_id == foreign_cover.id)
        .count()
        == 0
    )


def test_a_sku_grouped_under_a_printed_model_by_hand_is_unresolved(db, catalogue, monkeypatch):
    from app.models.product import Product

    sku = _p(db, "SRTWC8383A-RL")
    sku.family_code, sku.family_code_manual = "SRTWC8383", True
    db.flush()
    db.refresh(sku)
    assert sku.family_code == "SRTWC8383"
    review = _review(_client(db, {VIEW}, monkeypatch))
    row = _sku(_model(review, "SRTWC8383"), "SRTWC8383A-RL")
    assert (row["status"], row["reason"]) == ("unresolved", "not_printed")
    assert db.get(Product, sku.id) is not None


def test_retire_clears_derivation_rules_so_the_values_cannot_come_back(
    db, catalogue, seat_cover_spec, monkeypatch
):
    seat_cover_spec.derivation_rules = [
        {"match": "contains", "pattern": "SRTWC8383", "value": "SRTSCBD331-UF"}
    ]
    db.flush()
    client = _client(db, {VIEW, EDIT, SPEC_EDIT}, monkeypatch)
    client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": [catalogue["SRTWC8383-RL-UF"].id]})
    compat.retire_seat_cover_spec(db, seat_cover_spec.id)
    db.refresh(seat_cover_spec)
    assert seat_cover_spec.derivation_rules == []
    holding = (
        db.query(ProductSpecifications)
        .filter(ProductSpecifications.values.has_key("seat_cover"))  # noqa: W601
        .count()
    )
    assert holding == 0


def test_retire_rechecks_its_gate_when_the_countdown_lapses(
    db, catalogue, seat_cover_spec, monkeypatch
):
    client = _client(db, {VIEW, EDIT, SPEC_EDIT}, monkeypatch)
    client.post(f"{REVIEW_URL}/apply", json={"wc_product_ids": [catalogue["SRTWC8383-RL-UF"].id]})
    assert _park_retire(client, seat_cover_spec.id).status_code == 202
    for link in db.query(ProductCompatibility).all():
        compat.unlink(db, link.id)
    assert compat.retire_seat_cover_spec(db, seat_cover_spec.id) == {"removed": 0}
    db.refresh(seat_cover_spec)
    assert seat_cover_spec.is_active is True
