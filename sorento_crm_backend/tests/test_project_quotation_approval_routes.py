"""S14-S16 approval routes (PLAN-quotation-approval-and-revision-request).

Written BEFORE the handlers, and against the plan rather than against them: what a client is
owed, not a restatement of what the code happens to do.

Route-level rather than service-level because the HTTP seam carries what the service cannot:
whether the refusal reaches the browser as a 422 carrying a code the block can render, whether
the approve grant actually stops a salesperson at the door, and whether the new columns survive
the response model on the way out. The sibling suite ``test_project_quotation_approval.py``
proves the rules; nothing here re-proves them, it proves they reach the wire.

Postgres only, via ``blank_session``. Every row carries the ``zzt-qapproute`` marker.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.numbering import DocumentNumberingRule
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.projects import PriceFloorRule
from app.models.status import Status
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-qapproute"
BASE = "/api/v1/project-sales"

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"
APPROVE = "projects.quotations.approve"
ALL_SLUGS = [VIEW, EDIT, "projects.projects.delete", "projects.projects.manage", APPROVE]

LIST_PRICE = "300.00"
BELOW_FLOOR = "120.00"  # against a 50%-of-list floor of 150.00
ABOVE_FLOOR = "200.00"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _numbering_rule(db, company_id: str) -> None:
    rule = (
        db.query(DocumentNumberingRule)
        .filter(DocumentNumberingRule.doc_type == "project_quotation")
        .first()
    )
    if rule is None:
        rule = DocumentNumberingRule(id=_uid(), doc_type="project_quotation")
        if hasattr(DocumentNumberingRule, "company_id"):
            rule.company_id = company_id
        db.add(rule)
    rule.enabled = True
    rule.prefix_template = f"{MARKER}/Q/"
    rule.number_digits = 4
    rule.next_value = 1
    rule.start_value = 1
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()


def _product(db) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add(uom)
    category = ProductCategory(
        id=_uid(),
        category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} Sanitary Ware",
    )
    db.add(category)
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} WC Suite",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal(LIST_PRICE),
    )
    db.add(row)
    db.flush()
    return row


def _client(db, user_id: str):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(ALL_SLUGS)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@contextmanager
def _without_permission(slug: str):
    """Run the block as somebody holding every project grant EXCEPT ``slug``."""
    from app.services.user_service import UserPermissionService

    granted = [s for s in ALL_SLUGS if s != slug]
    original_check = UserPermissionService.check_user_has_permission
    original_slugs = UserPermissionService.get_user_permission_slugs
    UserPermissionService.check_user_has_permission = (
        lambda self, uid, wanted, _denied=slug: wanted != _denied
    )
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    try:
        yield
    finally:
        UserPermissionService.check_user_has_permission = original_check
        UserPermissionService.get_user_permission_slugs = original_slugs


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        _numbering_rule(db, company_id)
        user_id = _user(db, f"{MARKER} Baser")
        product = _product(db)
        db.add(
            PriceFloorRule(
                id=_uid(),
                company_id=company_id,
                mode="percent",
                value=Decimal("50.00"),
            )
        )
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Cabana Elmina {_uid()[:12]}",
        )
        db.commit()
        client, originals = _client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, user_id, project, product
        finally:
            _restore(originals)


# --------------------------------------------------------------------- helpers


def _quotation(client, project_id: str, product_id: str, unit_price: str) -> dict:
    """A signed one-scope quotation priced at ``unit_price``, ready to issue."""
    created = client.post(f"{BASE}/projects/{project_id}/quotation-documents", json={})
    assert created.status_code == 201, created.text
    document = created.json()
    root = f"{BASE}/projects/{project_id}/quotation-documents/{document['id']}"

    scope = client.post(f"{root}/scopes", json={"scope_label": f"{MARKER} Townhouse"})
    assert scope.status_code == 201, scope.text
    version_id = scope.json()["current_version_id"]

    line = client.post(
        f"{BASE}/quotation-versions/{version_id}/lines",
        json={"product_id": product_id, "unit_price": unit_price, "quantity": 2},
    )
    assert line.status_code == 201, line.text

    signed = client.post(
        f"{root}/sign",
        json={
            "signer_name": f"{MARKER} Baser",
            "mode": "draw",
            "image_data_uri": "data:image/png;base64,zzt",
        },
    )
    assert signed.status_code == 201, signed.text
    return client.get(root).json()


def _status_id(db, key: str) -> str:
    row = (
        db.query(Status)
        .filter(
            Status.entity_type == "quotation",
            Status.scope_id.is_(None),
            Status.key == key,
        )
        .first()
    )
    assert row is not None, f"the quotation graph has no '{key}' rung"
    return row.id


# ------------------------------------------------------------------ the graph


def test_the_approval_graph_is_readable_by_anyone_who_can_see_a_project(api):
    """Its own route rather than the admin `/statuses/graph/{entity}` one, which is gated on
    `system.statuses.view` and held by administrators alone. A salesperson has to be able to
    read the rung their own quotation stands on and the label of the move out of it, or the
    block on the screen can only offer a hardcoded button the admin cannot rename."""
    client, *_ = api

    response = client.get(f"{BASE}/quotation-approval-graph")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_type"] == "quotation"
    assert {row["key"] for row in body["statuses"]} == {
        "draft",
        "rejected",
        "pending_approval",
        "approved",
        "issued",
    }
    assert body["transitions"], "the graph has no edges, so nothing can ever move"

    with _without_permission(VIEW):
        denied = client.get(f"{BASE}/quotation-approval-graph")
    assert denied.status_code == 403


# ------------------------------------------------------------------ the gate


def test_a_clean_quotation_still_issues_in_one_press(api):
    """DoD item 1, at the seam the salesperson actually uses. The approval fields are present
    and say "nothing to do here" - absent would be as bad as wrong, because the screen reads
    them to decide whether to show the block at all."""
    client, _, _, _, project, product = api

    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
    assert document["requires_approval"] is False
    assert document["below_floor_line_count"] == 0
    assert document["approval_status_key"] is None

    issued = client.post(
        f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}/issue"
    )
    assert issued.status_code == 201, issued.text
    assert issued.json()["issue_no"] == 1


def test_a_below_floor_quotation_is_refused_with_the_code_the_block_renders(api):
    """DoD item 2. The screen has to name the reason and offer the way to ask for approval, so
    the refusal carries a stable code and a count of what is wrong rather than only prose."""
    client, _, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    assert document["requires_approval"] is True
    assert document["below_floor_line_count"] == 1

    refused = client.post(
        f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}/issue"
    )
    assert refused.status_code == 422, refused.text
    assert refused.json().get("code") == "quotation_below_floor_pending_approval"


def test_the_full_approval_round_trip_over_http(api):
    """Ask, approve, issue. Every step answers with the document, because the block, the CTA
    and the scope tabs all read the same row and a response that made the client refetch to
    find out what happened would let the screen sit on a stale answer."""
    client, db, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"

    asked = client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["approval_status_key"] == "pending_approval"

    approved = client.post(f"{root}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status_key"] == "approved"

    issued = client.post(f"{root}/issue")
    assert issued.status_code == 201, issued.text

    # Issuing spends the approval, so the next below-floor revision has to ask again.
    assert client.get(root).json()["approval_status_key"] == "issued"


def test_reject_requires_a_reason_and_hands_it_back_on_the_document(api):
    client, db, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"
    client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )

    blank = client.post(f"{root}/reject", json={"reason": "   "})
    assert blank.status_code == 422, blank.text

    missing = client.post(f"{root}/reject", json={})
    assert missing.status_code == 422, missing.text

    rejected = client.post(f"{root}/reject", json={"reason": "Bring it back to RM 240."})
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["approval_status_key"] == "rejected"
    assert body["approval_rejected_reason"] == "Bring it back to RM 240."

    # C2: there is no back-to-draft step any more, for anyone.
    back = client.post(
        f"{root}/approval-status", json={"to_status_id": _status_id(db, "draft")}
    )
    assert back.status_code == 422, back.text
    assert back.json().get("code") == "quotation_status_not_self_serve"
    assert client.get(root).json()["approval_status_key"] == "rejected"

    # C1: the way on is Send for approval, straight from Rejected, reason cleared.
    again = client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    assert again.status_code == 200, again.text
    assert again.json()["approval_status_key"] == "pending_approval"
    assert again.json()["approval_rejected_reason"] is None


def test_approve_and_reject_are_gated_on_the_new_slug(api):
    """DoD item 3 at the door. A salesperson holding every other project grant must not be able
    to approve their own below-floor pricing."""
    client, db, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"
    client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )

    with _without_permission(APPROVE):
        assert client.post(f"{root}/approve").status_code == 403
        assert (
            client.post(f"{root}/reject", json={"reason": "No."}).status_code == 403
        )

    assert client.get(root).json()["approval_status_key"] == "pending_approval"


def test_the_generic_move_route_is_gated_on_edit_and_refuses_the_managers_rungs(api):
    """Asking for approval is an edit to your own quotation, so it needs the edit grant and
    nothing more. Reaching `approved` or `rejected` through it is refused even though the edges
    exist, because that would be approving with no permission and rejecting with no reason."""
    client, db, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"

    with _without_permission(EDIT):
        denied = client.post(
            f"{root}/approval-status",
            json={"to_status_id": _status_id(db, "pending_approval")},
        )
    assert denied.status_code == 403

    client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    for key in ("approved", "rejected"):
        owned = client.post(
            f"{root}/approval-status", json={"to_status_id": _status_id(db, key)}
        )
        assert owned.status_code == 422, owned.text
        assert owned.json().get("code") == "quotation_status_not_self_serve"


def test_a_move_to_a_status_that_is_not_a_status_is_a_readable_refusal(api):
    """Validation, not a 500. A stale tab holding an id from a graph an admin has since
    reshaped is an ordinary event, and the answer to it is a sentence."""
    client, _, _, _, project, product = api

    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"

    unknown = client.post(f"{root}/approval-status", json={"to_status_id": _uid()})
    assert unknown.status_code == 422, unknown.text

    malformed = client.post(f"{root}/approval-status", json={"to_status_id": "not-a-uuid"})
    assert malformed.status_code in (400, 422), malformed.text

    empty = client.post(f"{root}/approval-status", json={})
    assert empty.status_code == 422, empty.text


# ============================================================================
# QUOTE-APPROVAL-UX (15 Oct 2026): lock while Pending / Approved, explicit Revise,
# rejected stays editable, deferred delete. UAC: C1, C4, D1-D5, D8, F6.
# ============================================================================


def _grant(db, user_id: str, slug: str) -> None:
    """Give ``user_id`` ``slug`` through a real role, the way RBAC resolves it."""
    from app.models.user import (
        UserPermission,
        UserRole,
        UserRoleAssignment,
        UserRolePermission,
    )

    permission = db.query(UserPermission).filter(UserPermission.slug == slug).first()
    if permission is None:
        permission = UserPermission(id=_uid(), slug=slug, name=slug)
        db.add(permission)
    role = UserRole(id=_uid(), slug=f"{MARKER}-{_uid()[:10]}", name=f"{MARKER} {_uid()[:10]}")
    db.add(role)
    db.flush()
    db.add(UserRolePermission(id=_uid(), role_id=role.id, permission_id=permission.id))
    db.add(UserRoleAssignment(id=_uid(), user_id=user_id, role_id=role.id))
    db.flush()


def _active_user(db, name: str, **extra) -> str:
    from app.models.user import UserStatus

    user_id = _uid()
    db.add(
        User(
            id=user_id,
            email=f"{user_id}@zzt.test",
            name=name,
            status=UserStatus.ACTIVE.value,
            **extra,
        )
    )
    db.flush()
    return user_id


def _root(project, document: dict) -> str:
    return f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"


def _to_pending(client, db, root: str) -> None:
    moved = client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    assert moved.status_code == 200, moved.text


def _in_state(client, db, project, product, state: str) -> tuple[dict, str]:
    """A below-floor, signed quotation standing on ``state``. Returns (document, root)."""
    document = _quotation(client, project.id, product.id, BELOW_FLOOR)
    root = _root(project, document)
    if state in ("pending_approval", "approved", "issued"):
        _to_pending(client, db, root)
    if state in ("approved", "issued"):
        approved = client.post(f"{root}/approve")
        assert approved.status_code == 200, approved.text
    if state == "issued":
        issued = client.post(f"{root}/issue")
        assert issued.status_code == 201, issued.text
    if state == "rejected":
        _to_pending(client, db, root)
        rejected = client.post(f"{root}/reject", json={"reason": "Too low."})
        assert rejected.status_code == 200, rejected.text
    return document, root


def _scope_and_line(client, root: str) -> tuple[dict, str]:
    document = client.get(root).json()
    scope = document["scopes"][0]
    lines = client.get(
        f"{BASE}/quotation-versions/{scope['current_version_id']}/lines"
    ).json()["data"]
    return scope, lines[0]["id"]


def _snapshot(client, root: str) -> dict:
    """What a refused write must leave exactly as it was."""
    document = client.get(root).json()
    out = {
        "subject_title": document.get("subject_title"),
        "terms_html": document.get("terms_html"),
        "scopes": [(s["id"], s["scope_label"], s["current_version_no"]) for s in document["scopes"]],
        "lines": {},
    }
    for scope in document["scopes"]:
        rows = client.get(
            f"{BASE}/quotation-versions/{scope['current_version_id']}/lines"
        ).json()["data"]
        out["lines"][scope["id"]] = [(r["id"], r["unit_price"], str(r["quantity"])) for r in rows]
    return out


def _writes(client, root: str, scope: dict, line_id: str, product_id: str) -> dict:
    version = scope["current_version_id"]
    scope_id = scope["id"]
    lines = f"{BASE}/quotation-versions/{version}/lines"
    return {
        "line_create": lambda: client.post(
            lines, json={"product_id": product_id, "unit_price": "130.00", "quantity": 1}
        ),
        "line_update": lambda: client.put(
            f"{lines}/{line_id}", json={"unit_price": "130.00"}
        ),
        "line_delete": lambda: client.delete(f"{lines}/{line_id}"),
        "lines_bulk_replace": lambda: client.put(lines, json={"lines": []}),
        "recompute": lambda: client.post(f"{BASE}/quotation-versions/{version}/recompute"),
        "scope_put": lambda: client.put(
            f"{BASE}/quotations/{scope_id}", json={"scope_label": f"{MARKER} Renamed"}
        ),
        "scope_delete": lambda: client.delete(f"{BASE}/quotations/{scope_id}"),
        "scope_revise": lambda: client.post(f"{BASE}/quotations/{scope_id}/revise"),
        "patch_document_with_scopes": lambda: client.patch(
            root, json={"scopes": [{"id": scope_id, "scope_label": f"{MARKER} Renamed"}]}
        ),
        "patch_document_remove_scope": lambda: client.patch(
            root, json={"remove_scope_ids": [scope_id]}
        ),
        "scope_patch_on_document": lambda: client.patch(
            f"{root}/scopes/{scope_id}", json={"scope_label": f"{MARKER} Renamed"}
        ),
        "add_scope": lambda: client.post(
            f"{root}/scopes", json={"scope_label": f"{MARKER} Second"}
        ),
        # D2: header-only is locked too (whole quotation, crew ruling 15 Oct).
        "patch_document_header_only": lambda: client.patch(
            root, json={"subject_title": f"{MARKER} New subject"}
        ),
    }


WRITE_NAMES = [
    "line_create",
    "line_update",
    "line_delete",
    "lines_bulk_replace",
    "recompute",
    "scope_put",
    "scope_delete",
    "scope_revise",
    "patch_document_with_scopes",
    "patch_document_remove_scope",
    "scope_patch_on_document",
    "add_scope",
    "patch_document_header_only",
]


@pytest.mark.parametrize("state", ["pending_approval", "approved"])
@pytest.mark.parametrize("write", WRITE_NAMES)
def test_d1_d2_every_write_is_refused_while_pending_or_approved(api, state, write):
    client, db, _, _, project, product = api
    _, root = _in_state(client, db, project, product, state)
    scope, line_id = _scope_and_line(client, root)
    before = _snapshot(client, root)

    response = _writes(client, root, scope, line_id, product.id)[write]()

    assert response.status_code == 422, f"{write} on {state}: {response.status_code} {response.text}"
    assert response.json().get("code") == "quotation_locked_for_approval", response.text
    assert _snapshot(client, root) == before, "a refused write must change nothing"


def test_d2_signing_is_not_an_edit_and_stays_allowed_while_pending(api):
    client, db, _, _, project, product = api
    _, root = _in_state(client, db, project, product, "pending_approval")

    signed = client.post(
        f"{root}/sign",
        json={
            "signer_name": f"{MARKER} Baser",
            "mode": "draw",
            "image_data_uri": "data:image/png;base64,zzt",
        },
    )
    assert signed.status_code == 201, signed.text
    # And the lock itself is real (guards against the sign check passing on an unlocked build).
    header = client.patch(root, json={"subject_title": f"{MARKER} nope"})
    assert header.status_code == 422, header.text
    assert header.json().get("code") == "quotation_locked_for_approval"


@pytest.mark.parametrize("state", ["pending_approval", "approved"])
def test_d3_revise_clears_the_approval_records_the_reason_and_reopens_the_lines(api, state):
    from app.models.activities import ActivityEvent

    client, db, _, _, project, product = api
    document, root = _in_state(client, db, project, product, state)

    revised = client.post(
        f"{root}/approval-revise", json={"reason": "  Customer changed the spec  "}
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["approval_status_key"] == "draft"
    assert body["approval_rejected_reason"] is None

    rows = (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.entity_type == "project",
            ActivityEvent.entity_id == str(project.id),
            ActivityEvent.system_template == "quotation_approval_revised",
        )
        .all()
    )
    assert len(rows) == 1
    assert "Customer changed the spec" in (rows[0].body_text or "") or (
        "Customer changed the spec" in str((rows[0].system_payload or {}).get("reason"))
    )
    assert (rows[0].system_payload or {}).get("document_id") == document["id"]

    scope, line_id = _scope_and_line(client, root)
    edited = client.put(
        f"{BASE}/quotation-versions/{scope['current_version_id']}/lines/{line_id}",
        json={"unit_price": "130.00"},
    )
    assert edited.status_code == 200, edited.text


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_d4_revise_needs_a_reason(api, reason):
    client, db, _, _, project, product = api
    _, root = _in_state(client, db, project, product, "pending_approval")

    payload = {} if reason is None else {"reason": reason}
    refused = client.post(f"{root}/approval-revise", json=payload)
    assert refused.status_code == 422, refused.text
    if reason is not None:
        assert refused.json().get("code") == "quotation_revise_reason_required"
    assert client.get(root).json()["approval_status_key"] == "pending_approval"


@pytest.mark.parametrize("state", [None, "rejected", "issued"])
def test_d4_revise_is_refused_off_the_pending_and_approved_rungs(api, state):
    client, db, _, _, project, product = api
    if state is None:
        document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
        root = _root(project, document)
    else:
        _, root = _in_state(client, db, project, product, state)

    refused = client.post(f"{root}/approval-revise", json={"reason": "Changing it"})
    assert refused.status_code == 422, refused.text
    assert refused.json().get("code") == "quotation_not_revisable"


def test_d4_revise_from_draft_is_refused_and_without_edit_rights_is_403(api):
    client, db, _, _, project, product = api
    _, root = _in_state(client, db, project, product, "pending_approval")
    assert (
        client.post(f"{root}/approval-revise", json={"reason": "First"}).status_code == 200
    )
    again = client.post(f"{root}/approval-revise", json={"reason": "Second"})
    assert again.status_code == 422, again.text
    assert again.json().get("code") == "quotation_not_revisable"

    _to_pending(client, db, root)
    with _without_permission(EDIT):
        denied = client.post(f"{root}/approval-revise", json={"reason": "Sneaky"})
    assert denied.status_code == 403, denied.text
    assert client.get(root).json()["approval_status_key"] == "pending_approval"


def test_c4_d8_a_rejected_quotation_is_not_locked_and_stays_rejected_when_edited(api):
    client, db, _, _, project, product = api
    _, root = _in_state(client, db, project, product, "rejected")
    scope, line_id = _scope_and_line(client, root)

    line = client.put(
        f"{BASE}/quotation-versions/{scope['current_version_id']}/lines/{line_id}",
        json={"unit_price": "130.00"},
    )
    assert line.status_code == 200, line.text
    header = client.patch(root, json={"subject_title": f"{MARKER} amended"})
    assert header.status_code == 200, header.text

    after = client.get(root).json()
    assert after["approval_status_key"] == "rejected"
    assert after["approval_rejected_reason"] == "Too low."


# ------------------------------------------------------------------ F6: deferred delete

PENDING = "/api/v1/pending-actions"
DELETE_SLUG = "projects.projects.delete"


def _park_delete(client, document_id: str):
    return client.post(
        PENDING,
        json={
            "action_key": "quotation_document.delete",
            "entity_type": "quotation_document",
            "entity_id": document_id,
            "payload": {},
        },
    )


def _document_exists(db, document_id: str) -> bool:
    from app.models.projects import ProjectQuotationDocument

    db.expire_all()
    return (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == document_id)
        .first()
        is not None
    )


def test_f6_an_editor_with_the_delete_grant_parks_the_delete_and_nothing_is_removed_yet(api):
    client, _, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)

    parked = _park_delete(client, document["id"])

    assert parked.status_code == 202, parked.text
    assert {"id", "commit_at", "window_seconds"} <= set(parked.json())


def test_f6_without_the_delete_slug_it_is_refused(api):
    client, db, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)

    with _without_permission(DELETE_SLUG):
        refused = _park_delete(client, document["id"])

    assert refused.status_code == 403, refused.text
    assert _document_exists(db, document["id"])


def test_f6_the_delete_slug_without_edit_rights_on_that_project_is_refused(api, monkeypatch):
    """Same check as the DELETE route's `_editable_project`: owner, collaborator or manager."""
    from app.dependencies import get_current_user
    from app.main import app
    from app.services.user_service import UserPermissionService

    client, db, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
    stranger = _active_user(db, f"{MARKER} Stranger")
    db.commit()
    actor = {"id": stranger, "email": f"{stranger}@zzt.test", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_permission_slugs",
        lambda self, uid: [VIEW, EDIT, DELETE_SLUG],
    )

    refused = _park_delete(client, document["id"])

    assert refused.status_code == 403, refused.text
    assert _document_exists(db, document["id"])


def test_f6_an_issued_quotation_cannot_be_parked_for_delete(api):
    client, db, _, _, project, product = api
    document, _ = _in_state(client, db, project, product, "issued")

    refused = _park_delete(client, document["id"])

    assert refused.status_code == 422, refused.text
    assert refused.json().get("code") == "quotation_document_issued"
    assert _document_exists(db, document["id"])


def test_f6_when_the_window_lapses_the_server_hard_deletes_the_document(api):
    from datetime import datetime, timedelta

    from app.models.sla import SlaFormAction

    client, db, _, _, project, product = api
    document = _quotation(client, project.id, product.id, ABOVE_FLOOR)
    parked = _park_delete(client, document["id"])
    assert parked.status_code == 202, parked.text
    assert _document_exists(db, document["id"]), "nothing applies until the window lapses"

    db.query(SlaFormAction).filter(SlaFormAction.id == parked.json()["id"]).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()
    current = client.get(
        f"{PENDING}/current",
        params={"entity_type": "quotation_document", "entity_id": document["id"]},
    )
    assert current.status_code == 200, current.text
    assert (current.json().get("last_outcome") or {}).get("status") == "committed", current.text
    assert not _document_exists(db, document["id"])
