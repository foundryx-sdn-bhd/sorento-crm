"""NEW-PRODUCT-APP staff surface, red-first. Every test cites its AC id
(documentation/plans/procurement/new-product-application-acceptance-criteria.md).

Route level on purpose: the UAC promises HTTP behaviour (codes, keys, 403 vs 404).
Postgres only (tests/_pg_fixture.py). Every test seeds its own chain; recipients for
notifications come from REAL role grants (the recipient query reads the tables), while the
route permission checks are swapped per caller like the Sample Request suite does.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ._pg_fixture import blank_session
from ._sample_request_seed import (
    MARKER,
    make_customer,
    make_product,
    make_user,
    second_company,
    sorento,
    uid,
)

NPA = "/api/v1/procurement/new-product-applications"

VIEW = "procurement.new_product_applications.view"
CREATE = "procurement.new_product_applications.create"
APPROVE = "procurement.new_product_applications.approve"
OPEN_CODE = "procurement.new_product_applications.open_code"
ALL = "ALL"


# ------------------------------------------------------------------ harness


def _install_client(db, perms: dict, state: dict):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    def actor():
        uid_ = state["user"]
        return {"id": uid_, "email": f"{uid_}@zzt.test", "role": "staff"}

    def has(self, user_id, slug):
        granted = perms.get(user_id, set())
        return granted == ALL or slug in granted

    def slugs(self, user_id):
        granted = perms.get(user_id, set())
        return {VIEW, CREATE, APPROVE, OPEN_CODE} if granted == ALL else set(granted)

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
    UserPermissionService.get_user_role_slugs = lambda self, uid_: set()
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


class Api:
    def __init__(self, client, state):
        self.client = client
        self._state = state

    def as_(self, user_id: str) -> "Api":
        self._state["user"] = user_id
        return self

    def __getattr__(self, name):
        return getattr(self.client, name)


def _grant(db, user_id: str, slugs: set, company_id: str, *, integration: bool = False) -> None:
    """A real role carrying `slugs`, assigned to an ACTIVE user with a grant for the company."""
    from app.models.company import UserCompany
    from app.models.user import (
        User,
        UserPermission,
        UserRole,
        UserRoleAssignment,
        UserRolePermission,
    )

    user = db.get(User, user_id)
    user.status = "ACTIVE"
    user.is_integration = integration
    role = UserRole(id=uid(), slug=f"zzt-npa-{uid()[:8]}", name=f"{MARKER} npa role {uid()[:8]}")
    db.add(role)
    db.flush()
    for slug in slugs:
        perm = db.query(UserPermission).filter(UserPermission.slug == slug).first()
        if perm is None:
            perm = UserPermission(id=uid(), slug=slug, name=slug)
            db.add(perm)
            db.flush()
        db.add(UserRolePermission(id=uid(), role_id=role.id, permission_id=perm.id))
    db.add(UserRoleAssignment(id=uid(), user_id=user_id, role_id=role.id))
    db.add(UserCompany(id=uid(), user_id=user_id, company_id=company_id))
    db.flush()


def _refs(db):
    from app.models.procurement import Supplier
    from app.models.product import ItemType

    product = make_product(db)
    item_type = ItemType(id=uid(), item_type_code=f"ZZT{uid()[:6]}", item_type_name="KITCHEN SINK")
    supplier = Supplier(id=uid(), supplier_code=f"ZZT-S{uid()[:6]}", supplier_name=f"{MARKER} supplier")
    db.add_all([item_type, supplier])
    db.flush()
    return SimpleNamespace(
        category=product.category_id,
        uom=product.base_uom_id,
        brand=product.brand_id,
        item_type=item_type.id,
        supplier=supplier.id,
    )


def _inquiry(db, **over):
    from app.models.procurement import StockInquiry

    row = StockInquiry(
        id=uid(),
        inquiry_number=f"SI26-Z{uid()[:4]}",
        product_code="XY-1234-MB & XY-1234-BG",
        item_description="Kitchen sink, nanograin finish & kitchen sink, stainless steel",
        project_customer=f"{MARKER} EXAMPLE DEVELOPER SDN BHD",
        project_name=f"{MARKER} EXAMPLE RESIDENCE, TOWER B",
        quantity="55",
        delivery_date="2028-01-17",
        purchasing_response="Supplier can make it, please apply for a new code.",
        status="responded",
    )
    for k, v in over.items():
        setattr(row, k, v)
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def sent(monkeypatch):
    """Every notification the feature creates: (user_id, type, entity id)."""
    from app.services.notification_service import NotificationService

    calls = []

    def capture(self, **kw):
        calls.append((kw.get("user_id"), kw.get("type"), kw.get("source_entity_id")))

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", capture)
    return calls


@pytest.fixture()
def world(sent):
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = sorento(db)
        admin = make_user(db, f"{MARKER} admin")
        sales = make_user(db, f"{MARKER} sales A")
        sales_b = make_user(db, f"{MARKER} sales B")
        manager = make_user(db, f"{MARKER} manager")
        manager_bot = make_user(db, f"{MARKER} manager bot")
        buyer = make_user(db, f"{MARKER} purchasing")
        viewer = make_user(db, f"{MARKER} viewer")
        stranger = make_user(db, f"{MARKER} stranger")
        perms = {
            admin: ALL,
            sales: {CREATE},
            sales_b: {CREATE},
            manager: {VIEW, APPROVE},
            manager_bot: {VIEW, APPROVE},
            buyer: {VIEW, OPEN_CODE},
            viewer: {VIEW},
            stranger: set(),
        }
        _grant(db, manager, {APPROVE}, company_id)
        _grant(db, manager_bot, {APPROVE}, company_id, integration=True)
        _grant(db, buyer, {OPEN_CODE}, company_id)
        _grant(db, sales, {CREATE, APPROVE}, company_id)  # an applicant who can also approve
        with company_scope(db, frozenset({company_id})):
            refs = _refs(db)
            product = make_product(db, code="ZZT-KS2433", name=f"{MARKER} nanograin sink")
            product2 = make_product(db, code="ZZT-KS2406", name=f"{MARKER} steel sink")
            inactive = make_product(db, active=False)
            customer = make_customer(db, company_id)
        inquiry = _inquiry(db)
        db.commit()

        state = {"user": sales}
        client, originals = _install_client(db, perms, state)
        api = Api(client, state)
        try:
            with company_scope(db, frozenset({company_id})):
                yield SimpleNamespace(
                    api=api, db=db, company_id=company_id, admin=admin, sales=sales,
                    sales_b=sales_b, manager=manager, manager_bot=manager_bot, buyer=buyer,
                    viewer=viewer, stranger=stranger, refs=refs, product=product,
                    product2=product2, inactive=inactive, customer=customer, inquiry=inquiry,
                    sent=sent,
                )
        finally:
            _restore(originals)


# ------------------------------------------------------------------ helpers


def _line(w, **over):
    body = {
        "proposed_code": "XY-1234-MB",
        "description": "Kitchen sink, nanograin finish",
        "category_id": w.refs.category,
        "base_uom_id": w.refs.uom,
        "brand_id": w.refs.brand,
        "item_type_id": w.refs.item_type,
        "supplier_id": None,
        "expected_qty": "55",
    }
    body.update(over)
    return body


def _body(w, **over):
    body = {
        "stock_inquiry_id": w.inquiry.id,
        "customer_name": f"{MARKER} EXAMPLE DEVELOPER SDN BHD",
        "project_name": f"{MARKER} EXAMPLE RESIDENCE, TOWER B",
        "needed_by": "2028-01-17",
        "lines": [_line(w), _line(w, proposed_code="XY-1234-BG", description="Kitchen sink, stainless steel")],
    }
    body.update(over)
    return body


def _draft(w, user=None, **over):
    res = w.api.as_(user or w.sales).post(NPA, json=_body(w, **over))
    assert res.status_code == 201, res.text
    return res.json()


def _act(w, aid, action, user, **json):
    return w.api.as_(user).post(f"{NPA}/{aid}/{action}", json=json or None)


def _submitted(w, **over):
    d = _draft(w, **over)
    res = _act(w, d["id"], "submit", w.sales)
    assert res.status_code == 200, res.text
    return res.json()


def _approved(w, **over):
    d = _submitted(w, **over)
    res = _act(w, d["id"], "approve", w.manager)
    assert res.status_code == 200, res.text
    return res.json()


def _get(w, aid, user=None):
    res = w.api.as_(user or w.admin).get(f"{NPA}/{aid}")
    assert res.status_code == 200, res.text
    return res.json()


def _types(w, aid):
    return [t for (_u, t, e) in w.sent if e == aid]


def _to(w, aid, notif_type):
    return {u for (u, t, e) in w.sent if e == aid and t == notif_type}


# ------------------------------------------------------------------ AC-NPA-01..05 draft


def test_ac01_draft_is_numbered_per_company_and_owned_by_the_caller(world):
    first = _draft(world)
    second = _draft(world)
    assert first["application_no"] == "NPA-000001"
    assert second["application_no"] == "NPA-000002"
    assert first["status"] == "draft"
    assert first["source"] == "staff"
    assert first["applicant_id"] == world.sales
    assert [l["line_no"] for l in first["lines"]] == [1, 2]


def test_ac01_create_needs_the_create_permission(world):
    res = world.api.as_(world.stranger).post(NPA, json=_body(world))
    assert res.status_code == 403


def test_ac02_a_draft_saves_empty_fields_and_cleared_numbers_stay_null(world):
    d = _draft(world, lines=[{"description": None, "expected_qty": None, "cost_price": None}])
    line = d["lines"][0]
    assert line["expected_qty"] is None
    assert line["cost_price"] is None
    assert line["category_id"] is None


def test_ac03_only_the_applicant_edits_a_draft(world):
    d = _draft(world)
    res = world.api.as_(world.sales_b).put(f"{NPA}/{d['id']}", json=_body(world, remark="x"))
    assert res.status_code == 403
    ok = world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world, remark="mine"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["remark"] == "mine"


def test_ac03_put_replaces_the_lines(world):
    d = _draft(world)
    res = world.api.as_(world.sales).put(
        f"{NPA}/{d['id']}", json=_body(world, lines=[_line(world, proposed_code="ONLY")])
    )
    assert res.status_code == 200, res.text
    assert [l["proposed_code"] for l in res.json()["lines"]] == ["ONLY"]


def test_ac03_delete_draft_is_a_registered_countdown_action_for_the_owner_only(world):
    from app.services import new_product_application_service as svc
    from app.services.error_handler import AppException
    import app.services.record_actions  # noqa: F401  (registers the actions)
    from app.services.form_action_registry import get_action

    assert get_action("new_product_application.delete") is not None
    d = _draft(world)
    with pytest.raises(AppException) as not_yours:
        svc.assert_deletable(world.db, d["id"], world.sales_b)
    assert not_yours.value.status_code == 403
    svc.delete_draft(world.db, d["id"], world.sales)
    assert world.api.as_(world.admin).get(f"{NPA}/{d['id']}").status_code == 404


def test_ac03_a_submitted_application_is_not_deletable(world):
    from app.services import new_product_application_service as svc
    from app.services.error_handler import AppException

    d = _submitted(world)
    with pytest.raises(AppException) as exc:
        svc.assert_deletable(world.db, d["id"], world.sales)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("field", ["category_id", "base_uom_id", "brand_id", "item_type_id", "supplier_id"])
def test_ac04_an_unknown_reference_is_422_naming_the_field(world, field):
    res = world.api.as_(world.sales).post(NPA, json=_body(world, lines=[_line(world, **{field: uid()})]))
    assert res.status_code == 422, res.text
    assert field in res.text


def test_ac04_another_companys_category_is_refused(world):
    from app.models.base import company_scope

    other = second_company(world.db)
    with company_scope(world.db, frozenset({other})):
        foreign = _refs(world.db)
    res = world.api.as_(world.sales).post(
        NPA, json=_body(world, lines=[_line(world, category_id=foreign.category)])
    )
    assert res.status_code == 422, res.text


def test_ac04_unknown_stock_inquiry_and_customer_are_refused(world):
    assert world.api.as_(world.sales).post(NPA, json=_body(world, stock_inquiry_id=uid())).status_code == 422
    assert world.api.as_(world.sales).post(NPA, json=_body(world, customer_id=uid())).status_code == 422


def test_ac05_prefill_copies_the_inquiry_as_text_and_matches_nothing(world):
    from app.models.order import Customer

    # A debtor whose name equals the inquiry's customer text must NOT be picked.
    same = world.db.query(Customer).filter(Customer.id == world.customer.id).one()
    same.customer_name = world.inquiry.project_customer
    world.db.flush()
    res = world.api.as_(world.sales).get(f"{NPA}/prefill", params={"stock_inquiry_id": world.inquiry.id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stock_inquiry_id"] == world.inquiry.id
    assert body["customer_id"] is None
    assert body["customer_name"] == world.inquiry.project_customer
    assert body["project_name"] == world.inquiry.project_name
    assert body["needed_by"] == "2028-01-17"
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["proposed_code"] == "XY-1234-MB & XY-1234-BG"  # never split
    assert line["description"] == world.inquiry.item_description
    assert line["expected_qty"] == "55"
    assert line.get("category_id") is None


def test_ac05_prefill_leaves_a_non_numeric_quantity_and_bad_date_empty(world):
    inq = _inquiry(world.db, quantity="1141 pcs", delivery_date="end of month")
    res = world.api.as_(world.sales).get(f"{NPA}/prefill", params={"stock_inquiry_id": inq.id})
    assert res.status_code == 200, res.text
    assert res.json()["needed_by"] is None
    assert res.json()["lines"][0]["expected_qty"] is None


# ------------------------------------------------------------------ AC-NPA-10..14 submit / decide / lock


def test_ac10_submit_needs_at_least_one_line(world):
    d = _draft(world, lines=[])
    res = _act(world, d["id"], "submit", world.sales)
    assert res.status_code == 422
    assert _get(world, d["id"])["status"] == "draft"


@pytest.mark.parametrize(
    "missing, words",
    [("description", "description"), ("category_id", "item group"), ("base_uom_id", "UOM")],
)
def test_ac10_submit_names_the_line_and_the_missing_field(world, missing, words):
    d = _draft(world, lines=[_line(world), _line(world, **{missing: None})])
    res = _act(world, d["id"], "submit", world.sales)
    assert res.status_code == 422
    assert "Line 2" in res.text
    assert words in res.text
    after = _get(world, d["id"])
    assert after["status"] == "draft"
    assert len(after["lines"]) == 2


def test_ac11_submit_moves_to_submitted_and_tells_the_approvers(world):
    d = _submitted(world)
    assert d["status"] == "submitted"
    assert d["submitted_at"]
    told = _to(world, d["id"], "new_product_application_submitted")
    # Active, non-integration approvers of the company; the applicant (who holds approve too)
    # is not told about their own application.
    assert told == {world.manager}


def test_ac11_only_the_applicant_submits(world):
    d = _draft(world)
    assert _act(world, d["id"], "submit", world.sales_b).status_code == 403


def test_ac12_a_rejected_application_is_edited_and_resubmitted_in_one_press(world):
    d = _submitted(world)
    rej = _act(world, d["id"], "reject", world.manager, reason="Supplier price too high")
    assert rej.status_code == 200, rej.text
    assert rej.json()["status"] == "rejected"
    assert rej.json()["reject_reason"] == "Supplier price too high"
    assert _to(world, d["id"], "new_product_application_rejected") == {world.sales}

    edit = world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world, remark="cheaper"))
    assert edit.status_code == 200, edit.text
    world.sent.clear()
    again = _act(world, d["id"], "submit", world.sales)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "submitted"
    assert _to(world, d["id"], "new_product_application_submitted") == {world.manager}


def test_ac13_approve_needs_the_approve_permission(world):
    d = _submitted(world)
    assert _act(world, d["id"], "approve", world.viewer).status_code == 403
    assert _act(world, d["id"], "reject", world.viewer, reason="no").status_code == 403


def test_ac13_approve_only_from_submitted(world):
    d = _draft(world)
    assert _act(world, d["id"], "approve", world.manager).status_code == 409


def test_ac13_reject_needs_a_reason(world):
    d = _submitted(world)
    assert _act(world, d["id"], "reject", world.manager, reason="  ").status_code == 422


def test_ac13_approve_tells_purchasing_and_stamps_the_decision(world):
    d = _approved(world)
    assert d["status"] == "approved"
    assert d["decided_by"] == world.manager
    assert _to(world, d["id"], "new_product_application_approved") == {world.buyer}


@pytest.mark.parametrize("state", ["submitted", "approved"])
def test_ac14_submitted_and_approved_lock_every_write(world, state):
    d = _submitted(world) if state == "submitted" else _approved(world)
    res = world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world, remark="sneaky"))
    assert res.status_code == 422
    assert "new_product_application_locked" in res.text


# ------------------------------------------------------------------ AC-NPA-20..23 revise / cancel


def test_ac20_revise_needs_a_reason(world):
    d = _approved(world)
    assert _act(world, d["id"], "revise", world.sales, reason="").status_code == 422


def test_ac20_revise_from_approved_returns_to_draft_and_keeps_the_reason(world):
    d = _approved(world)
    world.sent.clear()
    res = _act(world, d["id"], "revise", world.sales, reason="UOM is SET, not UNIT")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["revise_reason"] == "UOM is SET, not UNIT"
    assert body["decided_by"] is None
    # AC-NPA-21: from Approved nobody is told.
    assert _types(world, d["id"]) == []


def test_ac20_an_approver_may_revise(world):
    d = _approved(world)
    assert _act(world, d["id"], "revise", world.manager, reason="wrong group").status_code == 200


def test_ac20_a_stranger_may_not_revise(world):
    d = _approved(world)
    assert _act(world, d["id"], "revise", world.sales_b, reason="x").status_code == 403


def test_ac20_revise_only_from_submitted_or_approved(world):
    d = _draft(world)
    assert _act(world, d["id"], "revise", world.sales, reason="x").status_code == 409


def test_ac21_revise_from_submitted_tells_the_approvers_it_was_withdrawn(world):
    d = _submitted(world)
    world.sent.clear()
    assert _act(world, d["id"], "revise", world.sales, reason="typo").status_code == 200
    assert _to(world, d["id"], "new_product_application_withdrawn") == {world.manager}


def test_ac22_revise_and_cancel_are_refused_once_a_line_is_resolved(world):
    d = _approved(world)
    line = d["lines"][0]["id"]
    assert world.api.as_(world.buyer).post(
        f"{NPA}/{d['id']}/lines/{line}/link", json={"product_id": world.product.id}
    ).status_code == 200
    assert _act(world, d["id"], "revise", world.sales, reason="x").status_code == 409
    assert _act(world, d["id"], "cancel", world.sales).status_code == 409


def test_ac23_the_applicant_cancels_and_it_is_terminal(world):
    d = _submitted(world)
    res = _act(world, d["id"], "cancel", world.sales, reason="customer dropped it")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "cancelled"
    assert _act(world, d["id"], "submit", world.sales).status_code == 409
    put = world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world))
    assert put.status_code in (409, 422)


def test_ac23_someone_else_cannot_cancel(world):
    d = _draft(world)
    assert _act(world, d["id"], "cancel", world.sales_b).status_code == 403


# ------------------------------------------------------------------ AC-NPA-30..35 open the code


def _link(w, aid, line_id, product_id, user=None):
    return w.api.as_(user or w.buyer).post(
        f"{NPA}/{aid}/lines/{line_id}/link", json={"product_id": product_id}
    )


def test_ac30_purchasing_links_a_line_to_an_active_product(world):
    d = _approved(world)
    res = _link(world, d["id"], d["lines"][0]["id"], world.product.id)
    assert res.status_code == 200, res.text
    line = res.json()["lines"][0]
    assert line["opened_product_id"] == world.product.id
    assert line["opened_product_code"] == "ZZT-KS2433"
    assert line["resolved_by"] == world.buyer
    assert res.json()["status"] == "approved"  # line 2 still open


def test_ac30_linking_needs_open_code(world):
    d = _approved(world)
    assert _link(world, d["id"], d["lines"][0]["id"], world.product.id, user=world.sales).status_code == 403


def test_ac30_linking_only_on_an_approved_application(world):
    d = _submitted(world)
    assert _link(world, d["id"], d["lines"][0]["id"], world.product.id).status_code == 409


def test_ac30_inactive_unknown_and_other_company_products_are_refused(world):
    from app.models.base import company_scope

    d = _approved(world)
    line = d["lines"][0]["id"]
    assert _link(world, d["id"], line, world.inactive.id).status_code == 422
    assert _link(world, d["id"], line, uid()).status_code == 422
    other = second_company(world.db)
    with company_scope(world.db, frozenset({other})):
        foreign = make_product(world.db)
    assert _link(world, d["id"], line, foreign.id).status_code == 422


def test_ac30_a_line_of_another_application_is_404(world):
    a = _approved(world)
    b = _approved(world)
    assert _link(world, a["id"], b["lines"][0]["id"], world.product.id).status_code == 404


def test_ac31_product_search_ignores_dashes_and_case_and_only_lists(world):
    res = world.api.as_(world.buyer).get(f"{NPA}/products", params={"q": "zztks2433"})
    assert res.status_code == 200, res.text
    codes = [p["product_code"] for p in res.json()]
    assert "ZZT-KS2433" in codes
    assert world.api.as_(world.buyer).get(f"{NPA}/products", params={"q": ""}).json() == []
    inactive = world.api.as_(world.buyer).get(f"{NPA}/products", params={"q": world.inactive.product_code})
    assert world.inactive.product_code not in [p["product_code"] for p in inactive.json()]


def test_ac32_not_opening_needs_a_reason(world):
    d = _approved(world)
    line = d["lines"][1]["id"]
    url = f"{NPA}/{d['id']}/lines/{line}/close"
    assert world.api.as_(world.buyer).post(url, json={"reason": " "}).status_code == 422
    ok = world.api.as_(world.buyer).post(url, json={"reason": "supplier dropped BG"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["lines"][1]["closed_reason"] == "supplier dropped BG"


def test_ac34_resolving_every_line_completes_and_tells_the_applicant(world):
    d = _approved(world)
    world.sent.clear()
    assert _link(world, d["id"], d["lines"][0]["id"], world.product.id).status_code == 200
    res = world.api.as_(world.buyer).post(
        f"{NPA}/{d['id']}/lines/{d['lines'][1]['id']}/close", json={"reason": "not needed"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    assert res.json()["completed_at"]
    assert _to(world, d["id"], "new_product_application_completed") == {world.sales}


def test_ac33_undo_reopens_a_completed_application(world):
    d = _approved(world)
    _link(world, d["id"], d["lines"][0]["id"], world.product.id)
    _link(world, d["id"], d["lines"][1]["id"], world.product2.id)
    assert _get(world, d["id"])["status"] == "completed"
    res = world.api.as_(world.buyer).post(f"{NPA}/{d['id']}/lines/{d['lines'][1]['id']}/undo")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["completed_at"] is None
    assert body["lines"][1]["opened_product_id"] is None
    assert body["lines"][1]["resolved_by"] is None


def test_ac33_undo_needs_open_code(world):
    d = _approved(world)
    _link(world, d["id"], d["lines"][0]["id"], world.product.id)
    res = world.api.as_(world.sales).post(f"{NPA}/{d['id']}/lines/{d['lines'][0]['id']}/undo")
    assert res.status_code == 403


def test_ac35_a_resolved_line_cannot_be_linked_again(world):
    d = _approved(world)
    line = d["lines"][0]["id"]
    assert _link(world, d["id"], line, world.product.id).status_code == 200
    assert _link(world, d["id"], line, world.product2.id).status_code == 409


# ------------------------------------------------------------------ AC-NPA-40 notifications


def test_ac40_a_failing_notification_never_undoes_the_move(world, monkeypatch):
    from app.services.notification_service import NotificationService

    def boom(self, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", boom)
    d = _submitted(world)
    assert _get(world, d["id"])["status"] == "submitted"


def test_ac40_draft_saves_and_cancels_notify_nobody(world):
    d = _draft(world)
    world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world, remark="x"))
    _act(world, d["id"], "cancel", world.sales)
    assert _types(world, d["id"]) == []


# ------------------------------------------------------------------ AC-NPA-50..53 reads


def test_ac50_view_sees_everyone_and_create_sees_own(world):
    mine = _draft(world)
    theirs = _draft(world, user=world.sales_b)
    own = world.api.as_(world.sales).get(NPA).json()
    ids = {r["id"] for r in own["items"]}
    assert mine["id"] in ids and theirs["id"] not in ids
    every = world.api.as_(world.viewer).get(NPA).json()
    ids = {r["id"] for r in every["items"]}
    assert {mine["id"], theirs["id"]} <= ids
    assert world.api.as_(world.stranger).get(NPA).status_code == 403


def test_ac50_list_searches_number_code_and_description_and_filters_status(world):
    a = _draft(world, lines=[_line(world, proposed_code="ZZTFINDME")])
    _submitted(world)
    by_code = world.api.as_(world.viewer).get(NPA, params={"q": "zztfindme"}).json()
    assert [r["id"] for r in by_code["items"]] == [a["id"]]
    drafts = world.api.as_(world.viewer).get(NPA, params={"status": "draft"}).json()
    assert {r["status"] for r in drafts["items"]} == {"draft"}
    assert by_code["total"] == 1


def test_ac50_list_by_stock_inquiry_feeds_the_inquiry_row(world):
    d = _draft(world)
    other = _inquiry(world.db)
    rows = world.api.as_(world.viewer).get(NPA, params={"stock_inquiry_id": world.inquiry.id}).json()
    assert [r["id"] for r in rows["items"]] == [d["id"]]
    none = world.api.as_(world.viewer).get(NPA, params={"stock_inquiry_id": other.id}).json()
    assert none["items"] == []


def test_ac51_detail_is_readable_by_owner_and_view_not_by_others(world):
    d = _draft(world)
    assert world.api.as_(world.sales).get(f"{NPA}/{d['id']}").status_code == 200
    assert world.api.as_(world.viewer).get(f"{NPA}/{d['id']}").status_code == 200
    assert world.api.as_(world.sales_b).get(f"{NPA}/{d['id']}").status_code == 403


def test_ac51_detail_resolves_labels_not_ids(world):
    d = _draft(world, customer_id=world.customer.id)
    got = _get(world, d["id"])
    assert got["stock_inquiry_number"] == world.inquiry.inquiry_number
    assert got["customer_code"] == world.customer.customer_code
    line = got["lines"][0]
    for key in ("category_name", "base_uom_code", "brand_name", "item_type_name"):
        assert line[key], key


def test_ac53_another_companys_application_is_invisible(world):
    from app.models.base import company_scope

    d = _draft(world)
    other = second_company(world.db)
    with company_scope(world.db, frozenset({other})):
        assert world.api.as_(world.admin).get(f"{NPA}/{d['id']}").status_code == 404
        listed = world.api.as_(world.admin).get(NPA).json()
        assert d["id"] not in {r["id"] for r in listed["items"]}


def test_item_type_select_lists_the_company_item_types(world):
    res = world.api.as_(world.sales).get(f"{NPA}/item-types", params={"q": "kitchen"})
    assert res.status_code == 200, res.text
    assert world.refs.item_type in {r["id"] for r in res.json()}


# ------------------------------------------------------------------ review round 1


def test_review_list_rows_carry_the_opened_codes(world):
    """AC-NPA-52: the inquiry row shows the codes opened (it reads the LIST endpoint)."""
    d = _approved(world)
    assert _link(world, d["id"], d["lines"][0]["id"], world.product.id).status_code == 200
    rows = world.api.as_(world.viewer).get(NPA, params={"stock_inquiry_id": world.inquiry.id}).json()
    assert rows["items"][0]["opened_codes"] == ["ZZT-KS2433"]


def test_review_line_history_rolls_up_to_the_application():
    """Card example D: the application's audit trail shows a line's old values."""
    from app.models.new_product_application import NewProductApplicationLine
    from app.services import audit_service

    assert audit_service._parent_of(NewProductApplicationLine) == (
        "new_product_application",
        "application_id",
    )


def test_review_deleting_a_linked_product_is_a_named_409_not_a_500(world):
    from app.services.error_handler import AppException
    from app.services.product_service import ProductService

    d = _approved(world)
    assert _link(world, d["id"], d["lines"][0]["id"], world.product.id).status_code == 200
    with pytest.raises(AppException) as exc:
        ProductService(world.db).delete_product(world.product.id)
    assert exc.value.status_code == 409
    assert d["application_no"] in str(exc.value.message)


def test_review_moves_lock_the_application_row(world, monkeypatch):
    """Two purchasers finishing the last two lines at once must not leave it stuck in
    Approved: every move loads the row FOR UPDATE."""
    from app.services import new_product_application_service as svc

    seen = []
    original = svc.get_for

    def spy(db, application_id, *, contact_id=None, for_update=False):
        seen.append(for_update)
        return original(db, application_id, contact_id=contact_id, for_update=for_update)

    monkeypatch.setattr(svc, "get_for", spy)
    d = _approved(world)
    seen.clear()
    _link(world, d["id"], d["lines"][0]["id"], world.product.id)
    _act(world, d["id"], "revise", world.sales, reason="x")
    assert seen and all(seen)


def test_review_withdrawn_is_not_sent_to_the_approver_who_revised(world):
    d = _submitted(world)
    world.sent.clear()
    assert _act(world, d["id"], "revise", world.manager, reason="wrong group").status_code == 200
    assert world.manager not in _to(world, d["id"], "new_product_application_withdrawn")


def test_review_staff_cannot_edit_a_portal_application(world):
    from app.models.new_product_application import NewProductApplication

    d = _draft(world)
    row = world.db.get(NewProductApplication, d["id"])
    row.source = "portal"
    world.db.commit()
    res = world.api.as_(world.sales).put(f"{NPA}/{d['id']}", json=_body(world, remark="x"))
    assert res.status_code == 409, res.text


@pytest.mark.parametrize("field", ["description", "further_description", "remark"])
def test_review_line_text_is_capped(world, field):
    res = world.api.as_(world.sales).post(NPA, json=_body(world, lines=[_line(world, **{field: "x" * 5001})]))
    assert res.status_code == 422


def test_review_reason_is_capped(world):
    d = _submitted(world)
    assert _act(world, d["id"], "reject", world.manager, reason="x" * 5001).status_code == 422


def test_review_refusals_name_the_field_in_words(world):
    res = world.api.as_(world.sales).post(NPA, json=_body(world, lines=[_line(world, category_id=uid())]))
    assert res.status_code == 422
    assert "item group" in res.text
    assert "category_id does not exist" not in res.text
