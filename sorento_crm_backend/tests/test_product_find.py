"""CHAT-FIND: the generic product find (registry + route), first filter `fits`.

Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md sections 2.1, 2.2, 4 (F1-F8).
Card: documentation/plans/chatbot/CARD-chat-generic-find-12oct.md.

    GET /api/v1/master-data/products/find?filters=<json>[&contact_id=..&space_id=..]
        -> {"groups": [{"filter", "anchor", "relation", "head", "rows", "more"}],
            "stock_visibility": {...} | None}

Stock (F5) is seeded for real (warehouses, stock rows, a Respond contact with a visibility
policy row, as `tests/test_stock_visibility_policy.py` does) and goes through the real
`StockService.list_stock`; nothing is monkeypatched there.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import importlib
import json
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402  (must be the first app import)

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.access import RespondContact, StockVisibilityPolicy
from app.models.base import set_company_scope
from app.models.product import ProductCategory
from app.models.product_compatibility import ProductCompatibility
from app.models.respond_workspace import RespondWorkspace
from app.models.user import User, UserStatus
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.error_handler import AppException
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock, warehouse
from tests._pg_fixture import blank_session, unique_code

VIEW = "master_data.products.view"
URL = "/api/v1/master-data/products/find"

NONE_LINE = "No seat cover or WC is on record for {code}."


def pf():
    """Imported lazily so a missing module fails each test, not the whole collection."""
    return importlib.import_module("app.services.product_find")


# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _client(db, allow: set[str], monkeypatch) -> TestClient:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('find-caller')}@zzt.test",
        name="ZZT Find Caller",
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


# --------------------------------------------------------------------------- seeding


def _p(db, code, company_id=DEFAULT_COMPANY_ID):
    return product(db, company_id=company_id, code=code)


def _stem(prefix="ZZWC"):
    return f"{prefix}{unique_code(alpha=True)[:10].upper()}9"


def _link(db, cover, wc):
    link = ProductCompatibility(
        company_id=cover.company_id,
        seat_cover_product_id=cover.id,
        wc_product_id=wc.id,
        source="manual",
    )
    db.add(link)
    db.flush()
    return link


def _filters(*product_ids, key="fits"):
    return json.dumps([{"key": key, "product_ids": list(product_ids)}])


def _find(client, filters, **extra):
    params = {"filters": filters, **extra}
    return client.get(URL, params=params)


def _groups(response):
    assert response.status_code == 200, response.text
    return response.json()["groups"]


def _row_codes(group):
    return [r["product_code"] for r in group["rows"]]


def _error_code(response) -> str:
    """The error code, wherever the handler puts it (`{"code"}` or `{"detail": {"code"}}`)."""
    body = response.json()
    if isinstance(body, dict):
        if isinstance(body.get("code"), str):
            return body["code"]
        detail = body.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            return detail["code"]
    return json.dumps(body)


def _workspace(db, space_id: str) -> RespondWorkspace:
    row = RespondWorkspace(
        id=str(uuid.uuid4()), space_id=space_id, name="ZZT workspace", api_key_ciphertext="zzt"
    )
    db.add(row)
    db.flush()
    return row


def _contact(db, space_id: str) -> RespondContact:
    workspace = _workspace(db, space_id)
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Find Contact",
        respond_io_id=str(uuid.uuid4().int % 10**8),
        workspace_id=workspace.id,
    )
    db.add(row)
    db.flush()
    return row


def _policy(db, contact, mode, warehouse_ids=None):
    row = StockVisibilityPolicy(
        id=str(uuid.uuid4()), contact_id=contact.id, mode=mode, warehouse_ids=warehouse_ids
    )
    db.add(row)
    db.flush()
    return row


def _wh(db, code):
    return warehouse(db, company_id=DEFAULT_COMPANY_ID, code=f"{code}{unique_code()[:6]}")


def _stock(db, p, wh, qty):
    return stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=qty)


def _cover_and_wc(db):
    stem = _stem()
    return _p(db, f"{stem}SEAT-MBL"), _p(db, f"{stem}-RL-MBL")


# --------------------------------------------------------------------------- F1


def test_f1_fits_on_wc_lists_covers(db, monkeypatch):
    stem = _stem()
    wc = _p(db, f"{stem}-RL-MBL")
    b, a = _p(db, f"{stem}SEATB-MBL"), _p(db, f"{stem}SEATA-MBL")
    _link(db, a, wc)
    _link(db, b, wc)
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc.id)))
    assert group["filter"] == "fits"
    assert group["anchor"] == {"product_id": wc.id, "product_code": wc.product_code}
    assert group["relation"] == "covers"
    assert group["head"] == f"Seat cover for {wc.product_code}:"
    assert _row_codes(group) == [a.product_code, b.product_code]
    assert group["rows"][0]["product_id"] == a.id
    assert group["rows"][0]["product_name"] == a.product_name
    assert group["more"] == 0


def test_f1_fits_on_cover_lists_wcs(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(cover.id)))
    assert group["relation"] == "wcs"
    assert group["head"] == f"{cover.product_code} fits these WCs:"
    assert _row_codes(group) == [wc.product_code]


def test_f1_no_link_is_the_none_line_and_no_rows(db, monkeypatch):
    p = _p(db, f"{_stem()}-UF")
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(p.id)))
    assert group["relation"] is None
    assert group["head"] == NONE_LINE.format(code=p.product_code)
    assert group["rows"] == []
    assert group["more"] == 0


def test_f1_covers_win_when_a_product_has_both_ends(db, monkeypatch):
    stem = _stem()
    middle = _p(db, f"{stem}-RL-UF")
    cover_of_middle = _p(db, f"{stem}SEAT-UF")
    other_wc = _p(db, f"{stem}-SH-UF")
    _link(db, cover_of_middle, middle)  # middle is a WC here
    _link(db, middle, other_wc)  # and a cover there
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(middle.id)))
    assert group["relation"] == "covers"
    assert group["head"] == f"Seat cover for {middle.product_code}:"
    assert _row_codes(group) == [cover_of_middle.product_code]


# --------------------------------------------------------------------------- F2


def test_f2_two_products_two_groups_in_input_order(db, monkeypatch):
    stem = _stem()
    wc1, wc2 = _p(db, f"{stem}-AA-UF"), _p(db, f"{stem}-ZZ-UF")
    c1, c2 = _p(db, f"{stem}SEAT1"), _p(db, f"{stem}SEAT2")
    _link(db, c1, wc1)
    _link(db, c2, wc2)
    # Reverse of code order, so a sorted answer would fail.
    groups = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc2.id, wc1.id)))
    assert [g["anchor"]["product_code"] for g in groups] == [wc2.product_code, wc1.product_code]
    assert _row_codes(groups[0]) == [c2.product_code]
    assert _row_codes(groups[1]) == [c1.product_code]


def test_f2_a_duplicate_id_answers_once(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    groups = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc.id, wc.id)))
    assert len(groups) == 1


# --------------------------------------------------------------------------- F3


def test_f3_another_companys_link_and_product_never_appear(db, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    wc = _p(db, f"{stem}-RL-UF")
    theirs = _p(db, f"{stem}SEATM", company_id=MOCHA_ID)
    mine = _p(db, f"{stem}SEATS")
    _link(db, theirs, wc)  # the link row belongs to Mocha (owned by the cover's company)
    _link(db, mine, wc)
    client = _client(db, {VIEW}, monkeypatch)  # Sorento scope only
    [group] = _groups(_find(client, _filters(wc.id)))
    assert _row_codes(group) == [mine.product_code]
    assert theirs.product_code not in json.dumps(group)


def test_f3_only_a_foreign_link_means_the_none_line(db, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    wc = _p(db, f"{stem}-RL-UF")
    theirs = _p(db, f"{stem}SEATM", company_id=MOCHA_ID)
    _link(db, theirs, wc)
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc.id)))
    assert group["rows"] == []
    assert group["head"] == NONE_LINE.format(code=wc.product_code)


def test_f3_an_anchor_of_another_company_is_skipped(db, monkeypatch):
    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    stem = _stem()
    foreign_wc = _p(db, f"{stem}-RL-UF", company_id=MOCHA_ID)
    foreign_cover = _p(db, f"{stem}SEATM", company_id=MOCHA_ID)
    _link(db, foreign_cover, foreign_wc)
    mine = _p(db, f"{stem}-SH-UF")
    client = _client(db, {VIEW}, monkeypatch)
    groups = _groups(_find(client, _filters(foreign_wc.id, mine.id)))
    assert [g["anchor"]["product_id"] for g in groups] == [mine.id]
    assert foreign_wc.product_code not in json.dumps(groups)


# --------------------------------------------------------------------------- F4


def test_f4_unknown_filter_key_is_422(db, monkeypatch):
    p = _p(db, f"{_stem()}-UF")
    response = _find(_client(db, {VIEW}, monkeypatch), _filters(p.id, key="colour_of"))
    assert response.status_code == 422, response.text
    assert _error_code(response) == "unknown_filter"


def test_f4_run_find_raises_unknown_filter(db):
    p = _p(db, f"{_stem()}-UF")
    with pytest.raises(AppException) as raised:
        pf().run_find(
            db, [{"key": "colour_of", "product_ids": [p.id]}], contact_id=None, space_id=None
        )
    assert raised.value.status_code == 422
    assert raised.value.code == "unknown_filter"


def test_f4_missing_empty_or_non_list_filters_are_422(db, monkeypatch):
    client = _client(db, {VIEW}, monkeypatch)
    for response in (
        client.get(URL),
        _find(client, "[]"),
        _find(client, json.dumps({"key": "fits"})),
    ):
        assert response.status_code == 422, response.text
        assert _error_code(response) == "filters_required"


def test_f4_product_ref_filter_without_ids_is_422(db, monkeypatch):
    client = _client(db, {VIEW}, monkeypatch)
    for filters in (
        json.dumps([{"key": "fits"}]),
        json.dumps([{"key": "fits", "product_ids": []}]),
    ):
        response = _find(client, filters)
        assert response.status_code == 422, response.text
        assert _error_code(response) == "product_ids_required"


def test_f4_a_bad_uuid_is_400(db, monkeypatch):
    response = _find(_client(db, {VIEW}, monkeypatch), _filters("not-a-uuid"))
    assert response.status_code == 400, response.text


def test_f4_caps_are_422_too_many(db, monkeypatch):
    client = _client(db, {VIEW}, monkeypatch)
    p = _p(db, f"{_stem()}-UF")
    nine_filters = json.dumps([{"key": "fits", "product_ids": [p.id]}] * 9)
    eleven_ids = _filters(*[str(uuid.uuid4()) for _ in range(11)])
    too_long = json.dumps([{"key": "fits", "product_ids": [p.id], "value": "a" * 5000}])
    for filters in (nine_filters, eleven_ids, too_long):
        response = _find(client, filters)
        assert response.status_code == 422, (filters[:60], response.text)
        assert _error_code(response) == "too_many"


# --------------------------------------------------------------------------- F5


def test_f5_staff_without_contact_sums_every_row_as_detailed(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    _stock(db, cover, _wh(db, "ZZTA"), 7)
    _stock(db, cover, _wh(db, "ZZTB"), 5)
    body = _find(_client(db, {VIEW}, monkeypatch), _filters(wc.id)).json()
    [row] = body["groups"][0]["rows"]
    assert row["stock"]["mode"] == "detailed"
    assert row["stock"]["on_hand"] == 12
    assert row["stock"].get("verdict") is None
    assert body["stock_visibility"] is None


def test_f5_detailed_sums_only_the_visible_rows(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    seen_a, seen_b, hidden = _wh(db, "ZZTA"), _wh(db, "ZZTB"), _wh(db, "ZZTC")
    _stock(db, cover, seen_a, 7)
    _stock(db, cover, seen_b, 5)
    _stock(db, cover, hidden, 1000)
    contact = _contact(db, "770001")
    _policy(db, contact, "detailed", warehouse_ids=[seen_a.id, seen_b.id])
    body = _find(
        _client(db, {VIEW}, monkeypatch),
        _filters(wc.id),
        contact_id=contact.id,
        space_id="770001",
    ).json()
    [row] = body["groups"][0]["rows"]
    assert row["stock"]["mode"] == "detailed"
    assert row["stock"]["on_hand"] == 12
    assert body["stock_visibility"]["mode"] == "detailed"


def test_f5_compact_shows_the_total(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    seen_a, seen_b, hidden = _wh(db, "ZZTA"), _wh(db, "ZZTB"), _wh(db, "ZZTC")
    _stock(db, cover, seen_a, 30)
    _stock(db, cover, seen_b, 4)
    _stock(db, cover, hidden, 999)
    contact = _contact(db, "770002")
    _policy(db, contact, "compact", warehouse_ids=[seen_a.id, seen_b.id])
    body = _find(
        _client(db, {VIEW}, monkeypatch),
        _filters(wc.id),
        contact_id=contact.id,
        space_id="770002",
    ).json()
    [row] = body["groups"][0]["rows"]
    assert row["stock"]["mode"] == "compact"
    assert row["stock"]["on_hand"] == 34
    assert body["stock_visibility"]["mode"] == "compact"


def test_f5_availability_carries_a_branch_and_never_our_number(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    db.query(ProductCategory).filter(ProductCategory.id == cover.category_id).one().chatbot_max_qty = 100
    wh = _wh(db, "ZZTA")
    _stock(db, cover, wh, 7777)
    contact = _contact(db, "770003")
    _policy(db, contact, "availability", warehouse_ids=[wh.id])
    response = _find(
        _client(db, {VIEW}, monkeypatch),
        _filters(wc.id),
        contact_id=contact.id,
        space_id="770003",
    )
    body = response.json()
    [row] = body["groups"][0]["rows"]
    assert row["stock"]["mode"] == "availability"
    assert row["stock"]["on_hand"] is None
    assert row["stock"]["verdict"]["branch"] == "in_stock"
    assert "7777" not in response.text
    assert body["stock_visibility"]["mode"] == "availability"


def test_f5_contact_without_space_is_an_empty_answer(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    _stock(db, cover, _wh(db, "ZZTA"), 7)
    contact = _contact(db, "770004")
    _policy(db, contact, "detailed")
    body = _find(
        _client(db, {VIEW}, monkeypatch), _filters(wc.id), contact_id=contact.id
    ).json()
    assert body["groups"] == []


def test_f5_unresolvable_contact_fails_closed(db, monkeypatch):
    cover, wc = _cover_and_wc(db)
    _link(db, cover, wc)
    _stock(db, cover, _wh(db, "ZZTA"), 7)
    _workspace(db, "770005")
    body = _find(
        _client(db, {VIEW}, monkeypatch),
        _filters(wc.id),
        contact_id="nobody-9999",
        space_id="770005",
    ).json()
    assert body["groups"] == []


# --------------------------------------------------------------------------- F6


def test_f6_discontinued_row_is_flagged(db, monkeypatch):
    stem = _stem()
    wc = _p(db, f"{stem}-RL-UF")
    live, dead = _p(db, f"{stem}SEATA"), _p(db, f"{stem}SEATB")
    dead.is_discontinued = True
    db.flush()
    _link(db, live, wc)
    _link(db, dead, wc)
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc.id)))
    by_code = {r["product_code"]: r for r in group["rows"]}
    assert by_code[dead.product_code]["is_discontinued"] is True
    assert by_code[live.product_code]["is_discontinued"] is False


def test_f6_twenty_one_links_are_twenty_rows_and_more_one(db, monkeypatch):
    stem = _stem()
    wc = _p(db, f"{stem}-RL-UF")
    covers = [_p(db, f"{stem}SEAT{i:02d}") for i in range(21)]
    for cover in covers:
        _link(db, cover, wc)
    [group] = _groups(_find(_client(db, {VIEW}, monkeypatch), _filters(wc.id)))
    assert len(group["rows"]) == 20
    assert group["more"] == 1
    assert _row_codes(group) == sorted(c.product_code for c in covers)[:20]


# --------------------------------------------------------------------------- F7


def test_f7_without_products_view_is_403(db, monkeypatch):
    p = _p(db, f"{_stem()}-UF")
    response = _find(_client(db, set(), monkeypatch), _filters(p.id))
    assert response.status_code == 403, response.text


# --------------------------------------------------------------------------- F8


def test_f8_registry_rows_are_complete_and_translated():
    module = pf()
    assert "fits" in module.FILTERS
    for key, row in module.FILTERS.items():
        assert row.key == key
        assert row.type in {"product_ref", "spec", "flag"}, row.type
        assert isinstance(row.vocabulary, str) and row.vocabulary.strip(), key
        assert callable(row.run)
        assert row.labels, f"{key} has no presenter labels"
        for english, translations in row.labels.items():
            assert english.strip()
            for lang in ("ms", "zh"):
                assert translations.get(lang), f"{key}: {english!r} has no {lang}"
    assert module.FILTERS["fits"].type == "product_ref"


def test_f8_parser_vocabulary_holds_every_filters_line(db):
    module = pf()
    for text in (module.parser_vocabulary(), module.parser_vocabulary(db)):
        for key, row in module.FILTERS.items():
            assert key in text
            assert row.vocabulary in text
