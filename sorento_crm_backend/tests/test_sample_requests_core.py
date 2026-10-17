"""SR-CORE staff surface, red-first (Phase 2). Every test cites its AC id.

Route level on purpose: the UAC promises HTTP behaviour (codes, keys, 403 vs 404),
and no implementation exists yet, so the expected red reason is "404 Not Found" /
missing behaviour, not an import error.

Contract: documentation/plans/projects/PLAN-sample-request-core-15oct.md, "Contract".
Postgres only (tests/_pg_fixture.py). Every test seeds its own chain.
"""
from __future__ import annotations

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
                # A quotation version on the project, through the HTTP surface. The sample
                # request no longer links to it; it is only the old feed's trigger, used to
                # prove Delivered no longer writes a Samples row.
                q = api.as_(admin).post(
                    f"{BASE}/projects/{project.id}/quotations", json={"scope_label": "Units"}
                )
                assert q.status_code == 201, q.text
                version = q.json()["current_version_id"]

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
                    version=version,
                    customer=customer,
                    project=project,
                    other_project=other_project,
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


def test_ac_sr_03_submit_refuses_without_delivery_date(world):
    """AC-SR-03: a draft may lack the date, but submit is refused naming delivery_date."""
    d = _draft(world, delivery_date=None)
    res = _submit(world, d["id"])
    assert res.status_code == 422, res.text
    body = res.json()
    assert body.get("code") == "SAMPLE_REQUEST_INCOMPLETE", body
    assert body["details"]["field"] == "delivery_date", body


def test_ac_sr_03_with_a_delivery_date_submits(world):
    """AC-SR-03: a plain date satisfies the rule."""
    d = _draft(world, delivery_date="2026-12-01")
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_03_removed_keys_are_not_in_the_detail(world):
    """16 Oct: quotation, alternative product and the immediate tick are gone from reads,
    even when an old client still sends them (they must not persist or come back)."""
    # An old client still sends the alternative product (valid today) and the immediate flag
    # off. Either the API ignores them or refuses them; it must never echo them back.
    res = world.api.as_(world.sales).post(
        SR,
        json=full_payload(
            product_id=world.cat.plain.id,
            deliver_immediately=False,
            lines=[
                {
                    "product_id": world.cat.plain.id,
                    "alt_product_id": world.cat.plain2.id,
                    "qty": 1,
                }
            ],
        ),
    )
    if res.status_code == 422:  # a strict schema refusing the old keys is also fine
        return
    assert res.status_code == 201, res.text
    d = res.json()
    for got in (d, _get(world, d["id"])):
        assert "quotation_version_id" not in got, sorted(got)
        assert "deliver_immediately" not in got, sorted(got)
        for line in got["lines"]:
            assert "alt_product_id" not in line, sorted(line)
            assert "alt_product_code" not in line, sorted(line)
            assert "alt_product_name" not in line, sorted(line)
    rows = world.api.as_(world.sales).get(SR, params={"status": "draft"}).json()["data"]
    row = next(r for r in rows if r["id"] == d["id"])
    for key in ("quotation_version_id", "deliver_immediately"):
        assert key not in row, sorted(row)


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


def test_ac_sr_10_good_line_round_trips(world):
    """AC-SR-10: a line is one active product with brand and qty."""
    d = _draft(
        world,
        lines=[{"product_id": world.cat.plain.id, "qty": 3, "remark": "blue"}],
    )
    line = _get(world, d["id"])["lines"][0]
    assert line["product_code"] == world.cat.plain.product_code
    assert line["qty"] == 3
    assert line["remark"] == "blue"
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


# ------------------------------------------------------------------ AC-SR-20..22


def _settings_row(w):
    return w.api.as_(w.admin).get("/api/v1/user-management/settings/")


def test_ac_sr_20_settings_default_false_and_round_trip(world):
    """AC-SR-20: the one project switch is returned, OFF on deploy, saved by PUT /settings/general."""
    first = _settings_row(world)
    assert first.status_code == 200, first.text
    got = first.json()["settings"]
    assert got["sample_request_require_project"] is False
    assert "sample_request_require_quotation" not in got

    put = world.api.as_(world.admin).put(
        "/api/v1/user-management/settings/general",
        json={"sample_request_require_project": True},
    )
    assert put.status_code == 200, put.text
    after = _settings_row(world).json()["settings"]
    assert after["sample_request_require_project"] is True
    assert "sample_request_require_quotation" not in after


def test_ac_sr_20_app_config_carries_the_switches_for_non_admins(world):
    """AC-SR-20 / plan S3: a salesperson form reads them from /settings/app-config."""
    world.api.as_(world.admin).put(
        "/api/v1/user-management/settings/general", json={"sample_request_require_project": True}
    )
    res = world.api.as_(world.sales).get("/api/v1/user-management/settings/app-config")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sample_request_require_project"] is True
    assert "sample_request_require_quotation" not in body


def test_ac_sr_21_project_switch_off_typed_title_submits(world):
    """AC-SR-21: switch OFF, a typed title alone submits."""
    d = _draft(world, project_id=None, project_title="Typed only")
    assert _submit(world, d["id"]).status_code in (200, 201)


def test_ac_sr_21_project_switch_on_refuses_typed_title_but_old_request_opens(world):
    """AC-SR-21: ON refuses a typed title; a request saved before still opens and prints."""
    old = _draft(world, project_id=None, project_title="Typed before switch")
    world.api.as_(world.admin).put(
        "/api/v1/user-management/settings/general", json={"sample_request_require_project": True}
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
        "/api/v1/user-management/settings/general", json={"sample_request_require_project": True}
    )
    d = _draft(world, project_id=world.project.id, project_title=None)
    assert _submit(world, d["id"]).status_code in (200, 201)


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


# ------------------------------------------------------------------ AC-SR-34


# Through the ORM, not raw SQL: a schema-qualified `projects.samples` in text() bypasses the
# scratch schema's translate map and reads the real (empty) `projects` schema instead.
def _sample_count(w):
    from app.models.projects import ProjectSample

    return w.db.query(ProjectSample).count()


def test_delivered_no_longer_touches_project_samples(world):
    """16 Oct (AC-SR-33 removed): Delivered writes no Samples row, with or without a project,
    even when an old client still sends the quotation version the old feed keyed on."""
    before = _sample_count(world)
    assert before == 0
    _delivered(world)
    # Old client: project + quotation_version_id. Today that makes a Samples row on Delivered.
    res = world.api.as_(world.sales).post(
        SR,
        json=full_payload(
            product_id=world.cat.plain.id,
            project_id=world.project.id,
            project_title=None,
            quotation_version_id=world.version,
        ),
    )
    if res.status_code == 201:
        rid = res.json()["id"]
        assert _submit(world, rid).status_code in (200, 201)
        assert _move(world, rid, "processing").status_code == 200
        assert _move(world, rid, "customer_confirmed", date="2026-02-20").status_code == 200
        assert _move(world, rid, "delivered", date="2026-03-01").status_code == 200
    assert _sample_count(world) == before


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


def test_ac_sr_51_queue_defaults_to_open_statuses_delivery_date_ascending(world):
    """AC-SR-51: default Submitted+Processing+Customer confirmed; delivery date ascending."""
    later = _submitted(world, delivery_date="2026-12-20")
    sooner = _submitted(world, delivery_date="2026-12-05")
    soonest = _submitted(world, delivery_date="2026-11-30")
    draft = _draft(world, delivery_date="2026-11-01")["id"]

    res = world.api.as_(world.coord).get(SR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {"data", "pagination", "empty"}
    ids = [r["id"] for r in body["data"]]
    assert draft not in ids
    assert ids == [soonest, sooner, later]


def test_ac_sr_51_default_sort_puts_a_missing_date_last(world):
    """AC-SR-51: a request with no delivery date (a draft) sorts after dated ones."""
    dated = _draft(world, delivery_date="2026-12-20")["id"]
    undated = _draft(world, delivery_date=None)["id"]
    earlier = _draft(world, delivery_date="2026-12-01")["id"]
    res = world.api.as_(world.coord).get(SR, params={"status": "draft"})
    assert res.status_code == 200, res.text
    ids = [r["id"] for r in res.json()["data"]]
    assert ids == [earlier, dated, undated]


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
    res = world.api.as_(world.coord).get(f"{SR}/{uid()}")
    assert res.status_code == 404
    assert res.json().get("code") == "NOT_FOUND", res.text  # not a bare route miss


# ------------------------------------------------------------------ AC-SR-60 / 61


def test_ac_sr_60_pdf_is_a_pdf(world):
    """AC-SR-60: application/pdf, %PDF header."""
    d = _draft(world, board_mounting=True, board_label="TOWER A & B")
    res = world.api.as_(world.sales).get(f"{SR}/{d['id']}/pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content.startswith(b"%PDF")


def test_ac_sr_60_pdf_text_has_board_row_first_no_or_and_the_date(world):
    """AC-SR-60: board label is row 1, a line shows its one model (no OR), the delivery
    date prints dd/mm/yyyy."""
    import fitz  # PyMuPDF, already a backend dependency

    d = _draft(
        world,
        board_mounting=True,
        board_label="TOWER A & B",
        delivery_date="2026-12-01",
        # An old client still sends the alternative; it must not print as "OR <code>".
        lines=[
            {"product_id": world.cat.plain.id, "alt_product_id": world.cat.plain2.id, "qty": 2}
        ],
    )
    res = world.api.as_(world.sales).get(f"{SR}/{d['id']}/pdf")
    assert res.status_code == 200, res.text
    text_ = "".join(page.get_text() for page in fitz.open(stream=res.content, filetype="pdf"))
    # A table cell wraps where the runner's fonts make it wrap, and the extracted text then
    # carries a newline mid-label ("TOWER A &\nB"). Collapse whitespace so the assertions
    # read the printed words, not the line breaks of one machine's font metrics.
    text_ = " ".join(text_.split())
    assert "TOWER A & B" in text_
    assert text_.index("TOWER A & B") < text_.index(world.cat.plain.product_code)
    assert " OR " not in text_
    assert world.cat.plain2.product_code not in text_
    assert "01/12/2026" in text_
    assert "Immediate" not in text_


def test_ac_sr_61_stranger_cannot_print(world):
    """AC-SR-61: print needs ownership or .view."""
    d = _draft(world)
    assert world.api.as_(world.stranger).get(f"{SR}/{d['id']}/pdf").status_code == 403
    assert world.api.as_(world.sales_b).get(f"{SR}/{d['id']}/pdf").status_code == 403
    assert world.api.as_(world.coord).get(f"{SR}/{d['id']}/pdf").status_code == 200


# ------------------------------------------------------------------ review round 1


def _other_company_product(w):
    from tests._sample_request_seed import second_company

    other = second_company(w.db)
    row = make_product(w.db, code="ZZT-OTHERCO-88")
    row.company_id = other  # ORM, not raw SQL: text() bypasses the scratch schema
    w.db.flush()
    return row


def test_review_s3_staff_out_of_company_product_is_refused_and_not_listed(world):
    """AC-SR-10 / plan test 5: another company's product is a 422 and never listed."""
    foreign = _other_company_product(world)
    res = _bad_line(world, product_id=foreign.id, qty=1)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"
    listed = world.api.as_(world.sales).get(f"{SR}/products", params={"q": "OTHERCO"})
    assert listed.status_code == 200
    assert listed.json() == []


def test_review_s4_edit_and_submit_need_the_create_grant(world):
    """S4: an owner whose `.create` grant is gone cannot PUT, submit or cancel own request."""
    d = _draft(world)["id"]
    world.perms[world.sales] = {"projects.projects.view"}
    put = world.api.as_(world.sales).put(f"{SR}/{d}", json={"scope_note": "x"})
    assert put.status_code == 403, put.text
    assert _submit(world, d).status_code == 403
    assert _move(world, d, "cancelled", user=world.sales).status_code == 403


def test_review_s4_move_and_internal_check_view_first(world):
    """S4: a `.process` holder with no `.view` who does not own the request is refused on
    move and on /internal."""
    rid = _submitted(world)
    blind = make_user(world.db, f"{MARKER} process only")
    world.perms[blind] = {"projects.projects.view", "projects.sample_requests.process"}
    assert _move(world, rid, "processing", user=blind).status_code == 403
    res = world.api.as_(blind).put(f"{SR}/{rid}/internal", json={"consignment_no": "C"})
    assert res.status_code == 403, res.text


def test_review_r7_inactive_product_already_on_the_request_is_kept(world):
    """R7: an update may resend a product that went inactive since; only NEW ids must be
    active. The detail marks the line inactive."""
    d = _draft(world)
    world.cat.plain.is_active = False
    world.db.flush()
    keep = [{"product_id": world.cat.plain.id, "qty": 2}]
    res = world.api.as_(world.sales).put(f"{SR}/{d['id']}", json={"scope_note": "n", "lines": keep})
    assert res.status_code == 200, res.text
    line = _get(world, d["id"])["lines"][0]
    assert line["product_active"] is False
    new = world.api.as_(world.sales).put(
        f"{SR}/{d['id']}",
        json={"lines": keep + [{"product_id": world.cat.inactive.id, "qty": 1}]},
    )
    assert new.status_code == 422, new.text
    assert new.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


def test_review_r8_timestamps_follow_the_naive_utc_convention(world):
    """R8: like every other form, `submitted_at` / `cancelled_at` go out as naive UTC
    ISO strings (no offset); the frontend shows them with `formatDateInMalaysia`."""
    from datetime import datetime, timedelta

    rid = _submitted(world)
    got = _get(world, rid)
    stamp = got["submitted_at"]
    assert stamp and not stamp.endswith("Z") and "+" not in stamp[10:]
    assert abs(datetime.utcnow() - datetime.fromisoformat(stamp)) < timedelta(minutes=5)
    assert _move(world, rid, "cancelled", user=world.sales).status_code == 200
    assert _get(world, rid)["cancelled_at"]


def test_review_r9_request_number_takes_a_per_company_advisory_lock(world):
    """R9: max+1 runs under pg_advisory_xact_lock keyed per company."""
    from sqlalchemy import event

    seen = []

    def spy(_conn, _cursor, statement, *_a):
        if "pg_advisory_xact_lock" in statement:
            seen.append(statement)

    engine = world.db.get_bind()
    event.listen(engine, "before_cursor_execute", spy)
    try:
        _draft(world)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert seen, "next_request_no did not take the advisory lock"


def test_review_r10_company_is_not_null():
    """R10: company_id is NOT NULL on the model and in the migration."""
    import pathlib

    from app.models.sample_request import SampleRequest

    assert SampleRequest.__table__.c.company_id.nullable is False
    body = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "sr_0001_sample_requests.py"
    ).read_text()
    assert "company_id UUID NOT NULL" in body


def test_another_companys_scope_cannot_read_the_request(world):
    """`sample_requests` is a CompanyScopedMixin table (tests/test_company_scope.py counts it
    as owned), so a session scoped to another company sees no row: the detail is a 404, the
    list leaves it out, and an ORM read finds nothing. The same id reads fine in its own
    company, so the 404 is the scope and not a bad id."""
    from app.models.base import company_scope
    from app.models.sample_request import SampleRequest
    from tests._sample_request_seed import second_company

    rid = _draft(world)["id"]
    other = second_company(world.db)

    with company_scope(world.db, frozenset({other})):
        assert world.api.as_(world.admin).get(f"{SR}/{rid}").status_code == 404
        rows = world.api.as_(world.admin).get(SR, params={"status": "draft"}).json()["data"]
        assert rid not in {r["id"] for r in rows}
        assert world.db.query(SampleRequest).filter(SampleRequest.id == rid).first() is None

    assert world.api.as_(world.admin).get(f"{SR}/{rid}").status_code == 200
    rows = world.api.as_(world.admin).get(SR, params={"status": "draft"}).json()["data"]
    assert rid in {r["id"] for r in rows}


def test_review_r12_customer_name_sort_uses_the_debtor_name_too(world):
    """R12: sort=customer_name orders on coalesce(debtor name, typed name)."""
    mid = make_customer(world.db, world.company_id)
    mid.customer_name = f"{MARKER} mmm debtor"
    world.db.flush()
    ids = {}
    for tag, over in (
        ("a", {"customer_name": f"{MARKER} aaa typed"}),
        ("m", {"customer_id": mid.id, "customer_name": None}),
        ("z", {"customer_name": f"{MARKER} zzz typed"}),
    ):
        ids[tag] = _submitted(world, **over)
    res = world.api.as_(world.coord).get(
        SR, params={"sort": "customer_name", "dir": "asc", "limit": 200}
    )
    assert res.status_code == 200, res.text
    order = [r["id"] for r in res.json()["data"] if r["id"] in ids.values()]
    assert order == [ids["a"], ids["m"], ids["z"]]


def test_review_submitted_at_sort_orders_by_submission(world):
    """List "Submitted" column: sort=submitted_at orders by the submission stamp."""
    first = _submitted(world)
    second = _submitted(world)
    res = world.api.as_(world.coord).get(
        SR, params={"sort": "submitted_at", "dir": "desc", "limit": 200}
    )
    assert res.status_code == 200, res.text
    order = [r["id"] for r in res.json()["data"] if r["id"] in (first, second)]
    assert order == [second, first]


def test_review_s1_migration_copies_only_create_to_edit_holders():
    """S1: view and process go to the admin roles; only `.create` is copied."""
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "sr_0001_sample_requests.py"
    )
    spec = importlib.util.spec_from_file_location("sr_0001", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._COPY_FROM_EDIT == ("projects.sample_requests.create",)


# ------------------------------------------------------------------ delete draft (deferred)


def _park_delete(w, rid, user):
    return w.api.as_(user).post(
        "/api/v1/pending-actions",
        json={
            "action_key": "sample_request.delete",
            "entity_type": "sample_request",
            "entity_id": rid,
            "payload": {},
        },
    )


def _lapse(w, rid, action_id, user):
    from datetime import datetime, timedelta

    from app.models.sla import SlaFormAction

    w.db.query(SlaFormAction).filter(SlaFormAction.id == action_id).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    w.db.commit()
    return w.api.as_(user).get(
        "/api/v1/pending-actions/current",
        params={"entity_type": "sample_request", "entity_id": rid},
    )


def test_delete_draft_is_a_destructive_deferred_action_for_the_owner(world):
    """Owner deletes own draft: parked with the destructive window, hard-deleted on lapse."""
    from app.services.form_action_grace import WINDOW_DESTRUCTIVE, window_class_for
    from app.services.form_action_registry import REGISTRY

    assert window_class_for(REGISTRY["sample_request.delete"]) == WINDOW_DESTRUCTIVE
    rid = _draft(world)["id"]
    parked = _park_delete(world, rid, world.sales)
    assert parked.status_code == 202, parked.text
    out = _lapse(world, rid, parked.json()["id"], world.sales).json()
    assert out["last_outcome"]["status"] == "committed", out["last_outcome"]
    assert world.api.as_(world.coord).get(f"{SR}/{rid}").status_code == 404


def test_delete_draft_refused_for_a_non_owner_and_a_submitted_request(world):
    """Non-owner 403, submitted request 409, both at park time; the record stays."""
    draft = _draft(world)["id"]
    assert _park_delete(world, draft, world.sales_b).status_code == 403
    sent = _submitted(world)
    refused = _park_delete(world, sent, world.sales)
    assert refused.status_code == 409, refused.text
    assert _get(world, sent)["status"] == "submitted"
    assert _get(world, draft)["status"] == "draft"


def test_review_s1_delete_rechecks_the_draft_state_when_the_window_lapses(world):
    """S-1: a draft parked for delete, then submitted inside the window, is NOT deleted on lapse."""
    rid = _draft(world)["id"]
    parked = _park_delete(world, rid, world.sales)
    assert parked.status_code == 202, parked.text
    assert _submit(world, rid).status_code in (200, 201)
    out = _lapse(world, rid, parked.json()["id"], world.sales).json()
    assert out["last_outcome"]["status"] == "failed", out["last_outcome"]
    assert _get(world, rid)["status"] == "submitted"


def test_review_audit_trail_is_readable_with_the_sample_request_view_slug(world):
    """The detail page's Audit Trail: a non-admin with the view slug (the coordinator) reads the
    record's history; a salesperson holding only create is still refused (the page hides it)."""
    rid = _draft(world)["id"]
    params = {"entity_type": "project_sample_requests", "entity_id": rid}
    ok = world.api.as_(world.coord).get("/api/v1/audit/logs/", params=params)
    assert ok.status_code == 200, ok.text
    no = world.api.as_(world.sales).get("/api/v1/audit/logs/", params=params)
    assert no.status_code == 403, no.text


def test_review_audit_trail_names_the_status_not_its_id(world):
    """No UUIDs in the UI: a status move reads "Status: Draft -> Submitted" in the Audit Trail,
    not the two raw status ids."""
    import re

    rid = _submitted(world)
    params = {"entity_type": "project_sample_requests", "entity_id": rid}
    res = world.api.as_(world.coord).get("/api/v1/audit/logs/", params=params)
    assert res.status_code == 200, res.text
    descriptions = [e.get("description") or "" for e in res.json()["data"]]
    assert "Status: Draft \u2192 Submitted" in descriptions, descriptions
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    assert not any(uuid.search(d) for d in descriptions), descriptions


def test_review_list_filters_by_salesperson(world):
    """List Filters popover: ?salesperson_id= narrows the queue to one salesperson."""
    mine = _submitted(world)
    other = _draft(world, user=world.sales_b)["id"]
    assert _submit(world, other, user=world.sales_b).status_code in (200, 201)
    res = world.api.as_(world.coord).get(SR, params={"salesperson_id": world.sales_b})
    assert res.status_code == 200, res.text
    ids = [r["id"] for r in res.json()["data"]]
    assert other in ids and mine not in ids
