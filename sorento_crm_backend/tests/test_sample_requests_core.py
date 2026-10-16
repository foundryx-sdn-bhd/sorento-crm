"""SR-CORE staff surface, red-first (Phase 2). Every test cites its AC id.

Route level on purpose: the UAC promises HTTP behaviour (codes, keys, 403 vs 404),
and no implementation exists yet, so the expected red reason is "404 Not Found" /
missing behaviour, not an import error.

Contract: documentation/plans/projects/PLAN-sample-request-core-15oct.md, "Contract".
Postgres only (tests/_pg_fixture.py). Every test seeds its own chain.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from ._pg_fixture import blank_session
from ._sample_request_seed import (
    BASE,
    MARKER,
    full_payload,
    make_customer,
    make_product,
    make_project,
    make_user,
    seed_catalogue,
    sorento,
    uid,
)

SR = f"{BASE}/sample-requests"

ALL = "ALL"
SALES_PERMS = {
    "projects.projects.view",
    "projects.projects.edit",
    "projects.sample_requests.create",
}
COORD_PERMS = {
    "projects.projects.view",
    "projects.sample_requests.view",
    "projects.sample_requests.create",
    "projects.sample_requests.process",
}


class Api:
    """A TestClient whose caller and permissions can be swapped per call."""

    def __init__(self, client, state):
        self.client = client
        self._state = state

    def as_(self, user_id: str) -> "Api":
        self._state["user"] = user_id
        return self

    def __getattr__(self, name):
        return getattr(self.client, name)


def _install_client(db, perms: dict, roles: dict, state: dict):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    def actor():
        uid_ = state["user"]
        return {"id": uid_, "email": f"{uid_}@zzt.test", "role": roles.get(uid_, "salesperson")}

    def has(self, user_id, slug):
        granted = perms.get(user_id, set())
        return granted == ALL or slug in granted

    def slugs(self, user_id):
        granted = perms.get(user_id, set())
        return set(COORD_PERMS | SALES_PERMS) if granted == ALL else set(granted)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_user_or_api_key] = actor
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
        UserPermissionService.check_user_has_any_permission,
        UserPermissionService.get_user_role_slugs,
    )
    UserPermissionService.check_user_has_permission = has
    UserPermissionService.get_user_permission_slugs = slugs
    UserPermissionService.check_user_has_any_permission = lambda self, uid_, ss: any(
        has(self, uid_, s) for s in ss
    )
    UserPermissionService.get_user_role_slugs = lambda self, uid_: (
        {roles[uid_]} if uid_ in roles else set()
    )
    return TestClient(app), originals


def _restore(originals):
    from app.main import app
    from app.services.user_service import UserPermissionService

    (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
        UserPermissionService.check_user_has_any_permission,
        UserPermissionService.get_user_role_slugs,
    ) = originals
    app.dependency_overrides.clear()


@pytest.fixture()
def world():
    from app.models.base import company_scope
    from app.models.user import SystemSetting
    from app.services import project_seed_service

    with blank_session() as db:
        company_id = sorento(db)
        project_seed_service.run(db, company_id=company_id)
        if not db.query(SystemSetting).first():
            db.add(SystemSetting(id=uid()))

        admin = make_user(db, f"{MARKER} admin")
        sales = make_user(db, f"{MARKER} sales A")
        sales_b = make_user(db, f"{MARKER} sales B")
        coord = make_user(db, f"{MARKER} coordinator")
        stranger = make_user(db, f"{MARKER} stranger")
        perms = {
            admin: ALL,
            sales: set(SALES_PERMS),
            sales_b: set(SALES_PERMS),
            coord: set(COORD_PERMS),
            stranger: {"projects.projects.view"},
        }
        roles = {admin: "superadmin"}

        cat = seed_catalogue(db)
        customer = make_customer(db, company_id)
        project = make_project(db, company_id, admin)
        other_project = make_project(db, company_id, admin)
        db.commit()

        state = {"user": admin}
        client, originals = _install_client(db, perms, roles, state)
        api = Api(client, state)
        try:
            with company_scope(db, frozenset({company_id})):
                # A quotation version on each project, through the HTTP surface.
                def version_for(proj):
                    q = api.as_(admin).post(
                        f"{BASE}/projects/{proj.id}/quotations", json={"scope_label": "Units"}
                    )
                    assert q.status_code == 201, q.text
                    return q.json()["current_version_id"]

                yield SimpleNamespace(
                    api=api,
                    db=db,
                    company_id=company_id,
                    admin=admin,
                    sales=sales,
                    sales_b=sales_b,
                    coord=coord,
                    stranger=stranger,
                    cat=cat,
                    customer=customer,
                    project=project,
                    other_project=other_project,
                    version=version_for(project),
                    foreign_version=version_for(other_project),
                    perms=perms,
                )
        finally:
            _restore(originals)


# ------------------------------------------------------------------ helpers


def _draft(w, user=None, **over):
    body = full_payload(product_id=w.cat.plain.id, **over)
    res = w.api.as_(user or w.sales).post(SR, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _submit(w, rid, user=None):
    return w.api.as_(user or w.sales).post(f"{SR}/{rid}/submit")


def _submitted(w, **over):
    d = _draft(w, **over)
    res = _submit(w, d["id"])
    assert res.status_code in (200, 201), res.text
    return d["id"]


def _move(w, rid, to, user=None, **extra):
    return w.api.as_(user or w.coord).post(f"{SR}/{rid}/move", json={"to": to, **extra})


def _get(w, rid, user=None):
    res = w.api.as_(user or w.coord).get(f"{SR}/{rid}")
    assert res.status_code == 200, res.text
    return res.json()


def _delivered(w, **over):
    rid = _submitted(w, **over)
    assert _move(w, rid, "processing").status_code == 200
    assert _move(w, rid, "customer_confirmed", date="2026-02-20").status_code == 200
    res = _move(w, rid, "delivered", date="2026-03-01")
    assert res.status_code == 200, res.text
    return rid


# ------------------------------------------------------------------ AC-SR-01


def test_ac_sr_01_draft_is_numbered_sequentially(world):
    """AC-SR-01: SR-000001 then SR-000002 per company; draft status; source staff."""
    first = _draft(world)
    second = _draft(world)
    assert first["request_no"] == "SR-000001"
    assert second["request_no"] == "SR-000002"
    assert first["status"] == "draft"
    assert first["status_label"]
    assert first["source"] == "staff"
    assert first["salesperson_id"] == world.sales


def test_ac_sr_01_registered_project_is_prefilled_on_detail(world):
    """AC-SR-01: a request on a registered project reads back project and customer."""
    d = _draft(
        world,
        project_id=world.project.id,
        project_title=None,
        customer_id=world.customer.id,
        customer_name=None,
    )
    got = _get(world, d["id"])
    assert got["project_id"] == world.project.id
    assert got["project_name"]
    assert got["customer_code"] == world.customer.customer_code


# ------------------------------------------------------------------ AC-SR-02


@pytest.mark.parametrize(
    "drop, fields, keyword",
    [
        ({"customer_name": None, "customer_id": None}, {"customer_id", "customer_name"}, "customer"),
        ({"project_title": None, "project_id": None}, {"project_id", "project_title"}, "project"),
        ({"delivery_mode": None}, {"delivery_mode"}, "delivery mode"),
        ({"ship_to_name": None}, {"ship_to_name"}, "ship"),
        ({"lines": []}, {"lines"}, "add at least one item"),
        (
            {"board_mounting": True, "board_label": None},
            {"board_label"},
            "label",
        ),
    ],
)
def test_ac_sr_02_submit_refuses_each_missing_field(world, drop, fields, keyword):
    """AC-SR-02 / AC-SR-06: one refusal per field, code + details.field + plain message."""
    d = _draft(world, **drop)
    res = _submit(world, d["id"])
    assert res.status_code == 422, res.text
    body = res.json()
    assert body.get("code") == "SAMPLE_REQUEST_INCOMPLETE", body
    assert (body.get("details") or {}).get("field") in fields, body
    assert keyword in (body.get("message") or "").lower(), body


def test_ac_sr_02_complete_request_submits(world):
    """AC-SR-02: the complete payload submits and moves to submitted."""
    rid = _submitted(world)
    got = _get(world, rid)
    assert got["status"] == "submitted"
    assert got["submitted_at"]


# ------------------------------------------------------------------ AC-SR-03


def test_ac_sr_03_date_and_immediate_together_is_422(world):
    """AC-SR-03: both at once is refused on any write."""
    res = world.api.as_(world.sales).post(
        SR,
        json=full_payload(
            product_id=world.cat.plain.id, delivery_date="2026-12-01", deliver_immediately=True
        ),
    )
    assert res.status_code == 422, res.text


def test_ac_sr_03_neither_date_nor_immediate_refused_on_submit(world):
    """AC-SR-03: neither is a draft but cannot submit."""
    d = _draft(world, delivery_date=None, deliver_immediately=False)
    res = _submit(world, d["id"])
    assert res.status_code == 422, res.text
    body = res.json()
    assert body.get("code") == "SAMPLE_REQUEST_INCOMPLETE"
    assert body["details"]["field"] in {"delivery_date", "deliver_immediately"}


def test_ac_sr_03_immediate_alone_submits(world):
    """AC-SR-03: Immediate arrangement satisfies the delivery rule."""
    d = _draft(world, delivery_date=None, deliver_immediately=True)
    assert _submit(world, d["id"]).status_code in (200, 201)


# ------------------------------------------------------------------ AC-SR-04


def test_ac_sr_04_loan_defaults_to_two_months(world):
    """AC-SR-04: default loan = 2 months."""
    d = _draft(world)
    assert d["loan_value"] == 2
    assert d["loan_unit"] == "months"


def test_ac_sr_04_return_due_in_months(world):
    """AC-SR-04: delivered 2026-03-01 + 2 months = 2026-05-01."""
    rid = _delivered(world)
    got = _get(world, rid)
    assert got["delivered_at"].startswith("2026-03-01")
    assert got["return_due"] == "2026-05-01"


def test_ac_sr_04_return_due_in_days(world):
    """AC-SR-04: delivered 2026-03-01 + 10 days = 2026-03-11."""
    rid = _delivered(world, loan_value=10, loan_unit="days")
    assert _get(world, rid)["return_due"] == "2026-03-11"


def test_ac_sr_04_loan_value_below_one_is_422(world):
    """AC-SR-04: whole number >= 1."""
    res = world.api.as_(world.sales).post(
        SR, json=full_payload(product_id=world.cat.plain.id, loan_value=0)
    )
    assert res.status_code == 422, res.text


def test_ac_sr_04_loan_unit_outside_list_is_422(world):
    """AC-SR-04: unit is days or months only."""
    res = world.api.as_(world.sales).post(
        SR, json=full_payload(product_id=world.cat.plain.id, loan_unit="weeks")
    )
    assert res.status_code == 422, res.text


# ------------------------------------------------------------------ AC-SR-05


def test_ac_sr_05_goods_from_defaults_to_either_and_purpose_optional(world):
    """AC-SR-05: Either by default; a blank purpose still submits."""
    d = _draft(world)
    assert d["goods_from"] == "either"
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_05_goods_from_outside_list_is_422(world):
    """AC-SR-05: demo | new | either only."""
    res = world.api.as_(world.sales).post(
        SR, json=full_payload(product_id=world.cat.plain.id, goods_from="used")
    )
    assert res.status_code == 422, res.text


def test_ac_sr_05_delivery_mode_outside_list_is_422(world):
    """Contract: delivery_mode is driver | self_deliver | self_collect."""
    res = world.api.as_(world.sales).post(
        SR, json=full_payload(product_id=world.cat.plain.id, delivery_mode="drone")
    )
    assert res.status_code == 422, res.text


# ------------------------------------------------------------------ AC-SR-06


def test_ac_sr_06_board_is_a_flag_never_a_line(world):
    """AC-SR-06: board ON with a label keeps the line count equal to the picked lines."""
    d = _draft(world, board_mounting=True, board_label="TOWER A & B")
    got = _get(world, d["id"])
    assert got["board_mounting"] is True
    assert got["board_label"] == "TOWER A & B"
    assert len(got["lines"]) == 1


def test_ac_sr_06_board_alone_satisfies_the_lines_rule(world):
    """AC-SR-02/06: no lines but the board switch on submits; lines stay empty."""
    d = _draft(world, lines=[], board_mounting=True, board_label="TOWER A")
    assert _submit(world, d["id"]).status_code in (200, 201)
    assert _get(world, d["id"])["lines"] == []


# ------------------------------------------------------------------ AC-SR-10 / 13


def _bad_line(w, **line):
    return w.api.as_(w.sales).post(
        SR, json=full_payload(product_id=w.cat.plain.id, lines=[line])
    )


@pytest.mark.parametrize("which", ["unknown", "inactive"])
def test_ac_sr_10_bad_product_is_422_with_code(world, which):
    """AC-SR-10: unknown or inactive product_id -> 422 SAMPLE_REQUEST_PRODUCT_INVALID."""
    pid = uid() if which == "unknown" else world.cat.inactive.id
    res = _bad_line(world, product_id=pid, qty=1)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


@pytest.mark.parametrize("which", ["unknown", "inactive"])
def test_ac_sr_10_bad_alternative_product_is_422(world, which):
    """AC-SR-10: the alternative product fails the same check."""
    alt = uid() if which == "unknown" else world.cat.inactive.id
    res = _bad_line(world, product_id=world.cat.plain.id, alt_product_id=alt, qty=1)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


def test_ac_sr_10_missing_product_id_is_422(world):
    """AC-SR-10: a line with no product (free text) is refused."""
    res = _bad_line(world, remark="CB2829SS-CR", qty=1)
    assert res.status_code == 422, res.text


def test_ac_sr_10_update_also_checks_products(world):
    """AC-SR-10: checked on every write, not only create."""
    d = _draft(world)
    res = world.api.as_(world.sales).put(
        f"{SR}/{d['id']}",
        json={"lines": [{"product_id": world.cat.inactive.id, "qty": 1}]},
    )
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


def test_ac_sr_10_good_line_with_alternative_round_trips(world):
    """AC-SR-10: an 'X OR Y' line is product X + alternative Y, both active."""
    d = _draft(
        world,
        lines=[
            {
                "product_id": world.cat.plain.id,
                "alt_product_id": world.cat.plain2.id,
                "qty": 3,
                "remark": "blue",
            }
        ],
    )
    line = _get(world, d["id"])["lines"][0]
    assert line["product_code"] == world.cat.plain.product_code
    assert line["alt_product_code"] == world.cat.plain2.product_code
    assert line["qty"] == 3
    assert line["brand_name"]
    assert line["product_active"] is True
    assert line["line_no"] == 1


def test_ac_sr_13_qty_zero_is_422(world):
    """AC-SR-13: qty is a whole number >= 1."""
    res = _bad_line(world, product_id=world.cat.plain.id, qty=0)
    assert res.status_code == 422, res.text


def test_ac_sr_13_qty_defaults_to_one(world):
    """AC-SR-13: default 1."""
    d = _draft(world, lines=[{"product_id": world.cat.plain.id}])
    assert _get(world, d["id"])["lines"][0]["qty"] == 1


def test_ac_sr_13_same_product_twice_is_allowed(world):
    """Card edge: no dedupe."""
    pid = world.cat.plain.id
    d = _draft(world, lines=[{"product_id": pid}, {"product_id": pid}])
    assert len(_get(world, d["id"])["lines"]) == 2


# ------------------------------------------------------------------ AC-SR-11


def test_ac_sr_11_folded_search_finds_dashed_code(world):
    """AC-SR-11: 'zzt9101' lists ZZT-9101 (dash, space and case blind)."""
    for q in ("zzt9101", "ZZT 9101", "zzt-9101"):
        res = world.api.as_(world.sales).get(f"{SR}/products", params={"q": q})
        assert res.status_code == 200, res.text
        codes = [r["product_code"] for r in res.json()]
        assert "ZZT-9101" in codes, (q, codes)


def test_ac_sr_11_search_lists_only_active_and_does_not_pick(world):
    """AC-SR-11: inactive products are never offered; the result carries the 4 keys."""
    res = world.api.as_(world.sales).get(f"{SR}/products", params={"q": "ZZT"})
    assert res.status_code == 200, res.text
    rows = res.json()
    assert world.cat.inactive.product_code not in [r["product_code"] for r in rows]
    assert set(rows[0]) >= {"id", "product_code", "product_name", "brand_name"}
    assert len(rows) <= 20


def test_ac_sr_11_search_needs_create_permission(world):
    """Contract: products search needs sample_requests.create."""
    res = world.api.as_(world.stranger).get(f"{SR}/products", params={"q": "ZZT"})
    assert res.status_code == 403, res.text


def test_ac_sr_11_master_data_select_keeps_old_hits_and_gains_folded(world):
    """Plan: products/select only widens. Exact ilike still hits; folded now hits."""
    c = world.api.as_(world.sales)
    old = c.get("/api/v1/master-data/products/select", params={"query": "ZZT-9101"})
    assert old.status_code == 200, old.text
    assert "ZZT-9101" in old.text
    folded = c.get("/api/v1/master-data/products/select", params={"query": "zzt9101"})
    assert folded.status_code == 200, folded.text
    assert "ZZT-9101" in folded.text


# ------------------------------------------------------------------ AC-SR-20..22


def _settings_row(w):
    return w.api.as_(w.admin).get("/api/v1/settings/")


def test_ac_sr_20_settings_default_false_and_round_trip(world):
    """AC-SR-20: both switches returned, OFF on deploy, saved by PUT /settings/general."""
    got = _settings_row(world).json()["settings"]
    assert got["sample_request_require_project"] is False
    assert got["sample_request_require_quotation"] is False

    put = world.api.as_(world.admin).put(
        "/api/v1/settings/general",
        json={"sample_request_require_project": True, "sample_request_require_quotation": True},
    )
    assert put.status_code == 200, put.text
    after = _settings_row(world).json()["settings"]
    assert after["sample_request_require_project"] is True
    assert after["sample_request_require_quotation"] is True


def test_ac_sr_20_app_config_carries_the_switches_for_non_admins(world):
    """AC-SR-20 / plan S3: a salesperson form reads them from /settings/app-config."""
    world.api.as_(world.admin).put(
        "/api/v1/settings/general", json={"sample_request_require_project": True}
    )
    res = world.api.as_(world.sales).get("/api/v1/settings/app-config")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sample_request_require_project"] is True
    assert body["sample_request_require_quotation"] is False


def test_ac_sr_21_project_switch_off_typed_title_submits(world):
    """AC-SR-21: switch OFF, a typed title alone submits."""
    d = _draft(world, project_id=None, project_title="Typed only")
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_21_project_switch_on_refuses_typed_title_but_old_request_opens(world):
    """AC-SR-21: ON refuses a typed title; a request saved before still opens and prints."""
    old = _draft(world, project_id=None, project_title="Typed before switch")
    world.api.as_(world.admin).put(
        "/api/v1/settings/general", json={"sample_request_require_project": True}
    )
    refused = _submit(world, _draft(world, project_id=None, project_title="Typed after")["id"])
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["code"] == "SAMPLE_REQUEST_INCOMPLETE"
    assert body["details"]["field"] == "project_id"

    got = _get(world, old["id"])
    assert got["project_name"] == "Typed before switch"
    pdf = world.api.as_(world.coord).get(f"{SR}/{old['id']}/pdf")
    assert pdf.status_code == 200, pdf.text


def test_ac_sr_21_project_switch_on_registered_project_submits(world):
    """AC-SR-21: a registered project satisfies the switch."""
    world.api.as_(world.admin).put(
        "/api/v1/settings/general", json={"sample_request_require_project": True}
    )
    d = _draft(world, project_id=world.project.id, project_title=None)
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_22_quotation_switch_on_refuses_without_version(world):
    """AC-SR-22: ON, submit refuses without a quotation version."""
    world.api.as_(world.admin).put(
        "/api/v1/settings/general", json={"sample_request_require_quotation": True}
    )
    d = _draft(world, project_id=world.project.id, project_title=None)
    res = _submit(world, d["id"])
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "SAMPLE_REQUEST_INCOMPLETE"
    assert body["details"]["field"] == "quotation_version_id"


def test_ac_sr_22_quotation_switch_on_with_own_version_submits(world):
    """AC-SR-22: a version of the request's project passes."""
    world.api.as_(world.admin).put(
        "/api/v1/settings/general", json={"sample_request_require_quotation": True}
    )
    d = _draft(
        world,
        project_id=world.project.id,
        project_title=None,
        quotation_version_id=world.version,
    )
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_22_foreign_version_refused_at_any_setting(world):
    """AC-SR-22: a version of another project is 422 SAMPLE_REQUEST_QUOTATION_PROJECT."""
    res = world.api.as_(world.sales).post(
        SR,
        json=full_payload(
            product_id=world.cat.plain.id,
            project_id=world.project.id,
            project_title=None,
            quotation_version_id=world.foreign_version,
        ),
    )
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_QUOTATION_PROJECT"


# ------------------------------------------------------------------ AC-SR-30


LEGAL = [
    ("draft", "cancelled"),
    ("submitted", "cancelled"),
    ("submitted", "processing"),
    ("processing", "customer_confirmed"),
    ("processing", "cancelled"),
    ("customer_confirmed", "delivered"),
]


def _reach(w, status):
    """Drive a fresh request to `status` as the right people."""
    if status == "draft":
        return _draft(w)["id"]
    rid = _submitted(w)
    if status == "submitted":
        return rid
    assert _move(w, rid, "processing").status_code == 200
    if status == "processing":
        return rid
    assert _move(w, rid, "customer_confirmed", date="2026-02-20").status_code == 200
    if status == "customer_confirmed":
        return rid
    assert _move(w, rid, "delivered", date="2026-03-01").status_code == 200
    if status == "delivered":
        return rid
    raise AssertionError(status)


def _cancelled(w):
    rid = _draft(w)["id"]
    assert _move(w, rid, "cancelled", user=w.sales).status_code == 200
    return rid


@pytest.mark.parametrize("frm, to", LEGAL)
def test_ac_sr_30_legal_moves(world, frm, to):
    """AC-SR-30: every legal edge works for an allowed actor and reads back."""
    rid = _reach(world, frm)
    extra = {}
    if to in ("customer_confirmed", "delivered"):
        extra["date"] = "2026-03-02"
    if frm == "processing" and to == "cancelled":
        extra["reason"] = "customer changed mind"
    res = _move(world, rid, to, user=world.coord, **extra)
    assert res.status_code == 200, res.text
    assert _get(world, rid)["status"] == to


ALL_STATES = ["draft", "submitted", "processing", "customer_confirmed", "delivered", "cancelled"]
ILLEGAL = [
    (frm, to)
    for frm in ALL_STATES
    for to in ALL_STATES
    if frm != "draft" or to != "submitted"  # draft->submitted is /submit, not /move
    if (frm, to) not in LEGAL
]


@pytest.mark.parametrize("frm, to", ILLEGAL)
def test_ac_sr_30_illegal_moves_are_409(world, frm, to):
    """AC-SR-30: every other edge is refused 409 SAMPLE_REQUEST_ILLEGAL_MOVE."""
    rid = _cancelled(world) if frm == "cancelled" else _reach(world, frm)
    res = _move(world, rid, to, user=world.admin, date="2026-03-02", reason="x")
    assert res.status_code == 409, (frm, to, res.text)
    assert res.json().get("code") == "SAMPLE_REQUEST_ILLEGAL_MOVE"
    assert _get(world, rid)["status"] == frm


def test_ac_sr_30_status_label_comes_from_the_graph(world):
    """Contract: responses carry status (key) and status_label."""
    got = _get(world, _submitted(world))
    assert got["status"] == "submitted"
    assert got["status_label"]


def test_ac_sr_30_confirmed_and_delivered_need_a_date(world):
    """Contract: customer_confirmed and delivered need `date`; cancel of processing needs `reason`."""
    rid = _reach(world, "processing")
    assert _move(world, rid, "customer_confirmed").status_code == 422
    assert _move(world, rid, "cancelled").status_code == 422
    assert _move(world, rid, "cancelled", reason="out of stock").status_code == 200


# ------------------------------------------------------------------ AC-SR-31 / 32


@pytest.mark.parametrize(
    "start, to, extra",
    [
        ("submitted", "processing", {}),
        ("processing", "customer_confirmed", {"date": "2026-03-02"}),
        ("customer_confirmed", "delivered", {"date": "2026-03-02"}),
    ],
)
def test_ac_sr_31_edit_only_user_cannot_make_process_moves(world, start, to, extra):
    """AC-SR-31: projects.projects.edit without .process is 403, even on the owner's request."""
    rid = _reach(world, start)
    res = _move(world, rid, to, user=world.sales, **extra)
    assert res.status_code == 403, res.text
    assert _get(world, rid)["status"] == start


def test_ac_sr_31_owner_cannot_cancel_a_processing_request(world):
    """AC-SR-31: cancelling Processing is a process move."""
    rid = _reach(world, "processing")
    res = _move(world, rid, "cancelled", user=world.sales, reason="mine")
    assert res.status_code == 403, res.text


def test_ac_sr_31_owner_submits_and_cancels_own(world):
    """AC-SR-31: the owner submits and cancels own Draft / Submitted."""
    rid = _submitted(world)
    assert _move(world, rid, "cancelled", user=world.sales).status_code == 200
    assert _get(world, rid, user=world.sales)["status"] == "cancelled"


def test_ac_sr_31_another_salesperson_cannot_submit_or_cancel(world):
    """AC-SR-31: ownership = salesperson_id."""
    d = _draft(world)
    assert _submit(world, d["id"], user=world.sales_b).status_code in (403, 404)
    assert _move(world, d["id"], "cancelled", user=world.sales_b).status_code in (403, 404)


def test_ac_sr_31_internal_fields_need_process(world):
    """AC-SR-31: Consignment No / PO No edit is process only."""
    rid = _submitted(world)
    denied = world.api.as_(world.sales).put(
        f"{SR}/{rid}/internal", json={"consignment_no": "CSGN-1", "po_no": "PO-1"}
    )
    assert denied.status_code == 403, denied.text
    ok = world.api.as_(world.coord).put(
        f"{SR}/{rid}/internal", json={"consignment_no": "CSGN-1", "po_no": "PO-1"}
    )
    assert ok.status_code == 200, ok.text
    got = _get(world, rid)
    assert got["consignment_no"] == "CSGN-1"
    assert got["po_no"] == "PO-1"


def test_ac_sr_31_internal_fields_refused_on_cancelled(world):
    """Contract: internal edit works in any status but cancelled."""
    rid = _cancelled(world)
    res = world.api.as_(world.coord).put(
        f"{SR}/{rid}/internal", json={"consignment_no": "X", "po_no": "Y"}
    )
    assert res.status_code == 409, res.text


def test_ac_sr_32_owner_edit_after_submit_is_409(world):
    """AC-SR-32: header and lines are frozen after Submitted."""
    rid = _submitted(world)
    res = world.api.as_(world.sales).put(f"{SR}/{rid}", json={"scope_note": "late change"})
    assert res.status_code == 409, res.text


def test_ac_sr_32_draft_edit_replaces_lines(world):
    """Contract: PUT on a draft replaces all lines."""
    d = _draft(world)
    res = world.api.as_(world.sales).put(
        f"{SR}/{d['id']}",
        json={"scope_note": "FOR UNIT", "lines": [{"product_id": world.cat.plain2.id, "qty": 4}]},
    )
    assert res.status_code == 200, res.text
    got = _get(world, d["id"])
    assert got["scope_note"] == "FOR UNIT"
    assert [(l["product_code"], l["qty"]) for l in got["lines"]] == [
        (world.cat.plain2.product_code, 4)
    ]


# ------------------------------------------------------------------ AC-SR-33 / 34


def _samples_for_version(w, version_id):
    return w.db.execute(
        text("select submitted_on from projects.samples where quotation_version_id = :v"),
        {"v": version_id},
    ).fetchall()


def test_ac_sr_33_delivered_with_version_creates_a_sample_row(world):
    """AC-SR-33: Delivered + version -> one Samples row, submitted_on = delivered date."""
    _delivered(
        world,
        project_id=world.project.id,
        project_title=None,
        quotation_version_id=world.version,
    )
    rows = _samples_for_version(world, world.version)
    assert len(rows) == 1
    assert rows[0][0] == date(2026, 3, 1)


def test_ac_sr_33_delivered_without_version_creates_no_sample(world):
    """AC-SR-33: no version, no row."""
    before = world.db.execute(text("select count(*) from projects.samples")).scalar()
    _delivered(world)
    after = world.db.execute(text("select count(*) from projects.samples")).scalar()
    assert after == before


def test_ac_sr_34_each_move_writes_a_project_activity_row(world):
    """AC-SR-34: one activity row per move when the request has a project."""

    def count():
        return world.db.execute(
            text("select count(*) from activity_events where entity_id = :p"),
            {"p": world.project.id},
        ).scalar()

    d = _draft(world, project_id=world.project.id, project_title=None)
    start = count()
    _submit(world, d["id"])
    assert count() == start + 1
    assert _move(world, d["id"], "processing").status_code == 200
    assert count() == start + 2
    assert _move(world, d["id"], "customer_confirmed", date="2026-02-20").status_code == 200
    assert count() == start + 3


def test_ac_sr_34_no_project_no_project_activity(world):
    """AC-SR-34: a typed-title request writes no project activity row and does not fail."""
    q = text("select count(*) from activity_events where entity_type = 'project'")
    rid = _submitted(world)  # typed title, no project
    before = world.db.execute(q).scalar()
    assert _move(world, rid, "processing").status_code == 200
    assert world.db.execute(q).scalar() == before


# ------------------------------------------------------------------ AC-SR-51 / 54


def test_ac_sr_51_queue_defaults_to_open_statuses_immediate_first(world):
    """AC-SR-51: default Submitted+Processing+Customer confirmed; immediate first, then date asc."""
    later = _submitted(world, delivery_date="2026-12-20")
    sooner = _submitted(world, delivery_date="2026-12-05")
    immediate = _submitted(world, delivery_date=None, deliver_immediately=True)
    draft = _draft(world)["id"]

    res = world.api.as_(world.coord).get(SR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {"data", "pagination", "empty"}
    ids = [r["id"] for r in body["data"]]
    assert draft not in ids
    assert ids == [immediate, sooner, later]


def test_ac_sr_51_status_filter_and_project_filter(world):
    """AC-SR-51: status comma list and project_id filter."""
    on_project = _draft(world, project_id=world.project.id, project_title=None)["id"]
    _draft(world)
    res = world.api.as_(world.coord).get(
        SR, params={"status": "draft", "project_id": world.project.id}
    )
    assert res.status_code == 200, res.text
    assert [r["id"] for r in res.json()["data"]] == [on_project]


def test_ac_sr_51_project_tab_lists_all_statuses(world):
    """AC-SR-50/51: the tab list shows every status of that project."""
    d = _draft(world, project_id=world.project.id, project_title=None)["id"]
    res = world.api.as_(world.sales).get(f"{BASE}/projects/{world.project.id}/sample-requests")
    assert res.status_code == 200, res.text
    assert d in [r["id"] for r in res.json()["data"]]


def test_ac_sr_51_owner_without_view_sees_only_own_rows(world):
    """Contract: .create alone lists own rows only."""
    mine = _draft(world, user=world.sales)["id"]
    theirs = _draft(world, user=world.sales_b)["id"]
    ids = [
        r["id"]
        for r in world.api.as_(world.sales).get(SR, params={"status": "draft"}).json()["data"]
    ]
    assert mine in ids and theirs not in ids


def test_ac_sr_54_list_rows_carry_no_phone_or_address(world):
    """AC-SR-54: list rows never carry site_contact_phone / ship_to_address; detail does."""
    d = _draft(
        world,
        site_contact_phone="+60123456789",
        ship_to_address="1 Jalan Zzt, Masai",
    )
    rid = d["id"]
    row_keys = set()
    for url in (SR, f"{BASE}/projects/{world.project.id}/sample-requests"):
        res = world.api.as_(world.admin).get(url, params={"status": "draft"})
        assert res.status_code == 200, res.text
        assert "+60123456789" not in res.text and "Jalan Zzt" not in res.text
        for r in res.json()["data"]:
            row_keys |= set(r)
    assert "site_contact_phone" not in row_keys and "ship_to_address" not in row_keys
    assert {"id", "request_no", "status", "status_label", "line_count", "board_mounting"} <= row_keys
    detail = _get(world, rid, user=world.admin)
    assert detail["site_contact_phone"] == "+60123456789"
    assert detail["ship_to_address"] == "1 Jalan Zzt, Masai"


def test_ac_sr_54_list_row_line_count(world):
    """Contract: list row line_count."""
    d = _draft(world, lines=[{"product_id": world.cat.plain.id}, {"product_id": world.cat.plain2.id}])
    rows = world.api.as_(world.admin).get(SR, params={"status": "draft"}).json()["data"]
    assert [r["line_count"] for r in rows if r["id"] == d["id"]] == [2]


# ------------------------------------------------------------------ view permission


def test_stranger_cannot_open_or_list(world):
    """Contract: get needs view or ownership."""
    d = _draft(world)
    assert world.api.as_(world.stranger).get(f"{SR}/{d['id']}").status_code == 403
    assert world.api.as_(world.stranger).get(SR).status_code == 403


def test_create_needs_create_permission(world):
    """Contract: POST needs .create."""
    res = world.api.as_(world.stranger).post(
        SR, json=full_payload(product_id=world.cat.plain.id)
    )
    assert res.status_code == 403, res.text


def test_unknown_request_id_is_404(world):
    """Missing record -> 404."""
    assert world.api.as_(world.coord).get(f"{SR}/{uid()}").status_code == 404


# ------------------------------------------------------------------ AC-SR-60 / 61


def test_ac_sr_60_pdf_is_a_pdf(world):
    """AC-SR-60: application/pdf, %PDF header."""
    d = _draft(world, board_mounting=True, board_label="TOWER A & B")
    res = world.api.as_(world.sales).get(f"{SR}/{d['id']}/pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content.startswith(b"%PDF")


def test_ac_sr_60_pdf_text_has_board_row_first_and_or_alt(world):
    """AC-SR-60: board label is row 1, a line shows brand/model and 'OR <alt>'."""
    import fitz  # PyMuPDF, already a backend dependency

    d = _draft(
        world,
        board_mounting=True,
        board_label="TOWER A & B",
        lines=[
            {
                "product_id": world.cat.plain.id,
                "alt_product_id": world.cat.plain2.id,
                "qty": 2,
            }
        ],
    )
    res = world.api.as_(world.sales).get(f"{SR}/{d['id']}/pdf")
    assert res.status_code == 200, res.text
    text_ = "".join(page.get_text() for page in fitz.open(stream=res.content, filetype="pdf"))
    assert "TOWER A & B" in text_
    assert text_.index("TOWER A & B") < text_.index(world.cat.plain.product_code)
    assert "OR" in text_ and world.cat.plain2.product_code in text_


def test_ac_sr_61_stranger_cannot_print(world):
    """AC-SR-61: print needs ownership or .view."""
    d = _draft(world)
    assert world.api.as_(world.stranger).get(f"{SR}/{d['id']}/pdf").status_code == 403
    assert world.api.as_(world.sales_b).get(f"{SR}/{d['id']}/pdf").status_code == 403
    assert world.api.as_(world.coord).get(f"{SR}/{d['id']}/pdf").status_code == 200
