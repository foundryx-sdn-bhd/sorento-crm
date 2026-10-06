"""AC-D1: an attachment payload carries `company_id` AND a resolved `company_name`.

Two companies can each hold a current "Container Status 2026.xlsx" - the UUID is
deliberately withheld from customer-facing renders (see the MCP presenter change
in the same diff), so the company name is the only handle a reader has to tell
them apart. `AttachmentService.company_name_map` resolves those names in ONE
query per page rather than per row - the file library is the busiest endpoint
in the resource module, so an N+1 there would be a real regression, not a
theoretical one.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.api.v1.resources.attachments import _stamp_company
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.company import Company
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope_resolver import apply_company_scope
from app.services.resources_service import AttachmentService

from tests._pg_fixture import blank_session, unique_code

MOCHA_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def db():
    with blank_session() as session:
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("MCH")[:20]))
        session.flush()
        yield session


def _att_type(db) -> str:
    row = AttachmentType(
        id=str(uuid.uuid4()), type_name=unique_code("Packing List")[:50],
        allowed_extensions="pdf",
    )
    db.add(row)
    db.flush()
    return row.id


def _attachment(db, *, company_id: str | None, type_id: str) -> Attachment:
    row = Attachment(
        id=str(uuid.uuid4()),
        original_filename="ZZT-doc.pdf",
        stored_filename="ZZT-doc.pdf",
        file_path="https://cdn.test/zzt-doc.pdf",
        attachment_type_id=type_id,
        is_deleted=False,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


# --- AttachmentService.company_name_map -------------------------------------


def test_company_name_map_resolves_ids_to_names(db):
    from app.services.company_scope import DEFAULT_COMPANY_ID

    type_id = _att_type(db)
    sorento_att = _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id)
    mocha_att = _attachment(db, company_id=MOCHA_ID, type_id=type_id)
    shared_att = _attachment(db, company_id=None, type_id=type_id)
    db.commit()

    names = AttachmentService(db).company_name_map([sorento_att, mocha_att, shared_att])

    assert names[DEFAULT_COMPANY_ID] == "Sorento"
    assert names[MOCHA_ID] == "Mocha"
    assert None not in names, "a shared (NULL) attachment contributes no entry"


def test_company_name_map_is_one_query_for_a_page_of_rows(db):
    """No N+1: however many rows are on the page, the company lookup is a
    single batched SELECT."""
    from app.services.company_scope import DEFAULT_COMPANY_ID

    type_id = _att_type(db)
    attachments = [
        _attachment(
            db,
            company_id=DEFAULT_COMPANY_ID if i % 2 == 0 else MOCHA_ID,
            type_id=type_id,
        )
        for i in range(6)
    ]
    db.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, *_a, **_kw):
        if "companies" in statement.lower():
            statements.append(statement)

    connection = db.get_bind()
    event.listen(connection, "before_cursor_execute", _capture)
    try:
        names = AttachmentService(db).company_name_map(attachments)
    finally:
        event.remove(connection, "before_cursor_execute", _capture)

    assert len(statements) == 1, (
        f"expected exactly one companies query for 6 rows, saw {len(statements)}: "
        f"{statements}"
    )
    assert names[DEFAULT_COMPANY_ID] == "Sorento"
    assert names[MOCHA_ID] == "Mocha"


def test_company_name_map_empty_input_issues_no_query(db):
    statements: list[str] = []

    def _capture(conn, cursor, statement, *_a, **_kw):
        if "companies" in statement.lower():
            statements.append(statement)

    connection = db.get_bind()
    event.listen(connection, "before_cursor_execute", _capture)
    try:
        names = AttachmentService(db).company_name_map([])
    finally:
        event.remove(connection, "before_cursor_execute", _capture)

    assert names == {}
    assert statements == []


# --- _stamp_company shape -----------------------------------------------------


def test_stamp_company_sets_both_keys_for_an_owned_row(db):
    from app.services.company_scope import DEFAULT_COMPANY_ID

    type_id = _att_type(db)
    att = _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id)
    db.commit()

    data: dict = {}
    _stamp_company(data, att, {DEFAULT_COMPANY_ID: "Sorento"})

    assert data["company_id"] == DEFAULT_COMPANY_ID
    assert data["company_name"] == "Sorento"


def test_stamp_company_is_explicit_none_for_a_shared_row(db):
    """AC-D3 (mirrored here at the schema level): a company-less attachment
    gets explicit None on both keys, never a missing key or an empty string."""
    type_id = _att_type(db)
    att = _attachment(db, company_id=None, type_id=type_id)
    db.commit()

    data: dict = {}
    _stamp_company(data, att, {})

    assert data["company_id"] is None
    assert data["company_name"] is None


# --- route level --------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    # The list is gated on the catalogue readers' slugs (LIST-API-PERMS); the principal
    # here is a dict with no users row, so answer the slug lookup directly. The gate has
    # its own tests (tests/test_list_api_perms.py); this file tests the company stamp.
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_permission_slugs",
        lambda self, uid: {"resource.attachments.view"},
    )
    with blank_session() as db:
        db.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("MCH")[:20]))
        db.flush()

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        principal = {"id": str(uuid.uuid4()), "email": "zzt-attachments-list@test.com"}
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        # Attachments are __company_shared__: an owned Mocha row is only visible
        # under a scope that includes Mocha. The real resolver reads a bearer
        # token, which this test does not send, so pin the scope directly -
        # what is under test here is the company stamp on the payload, not
        # scope resolution (owned by company_scope_resolver's own tests).
        async def _override_scope():
            from app.services.company_scope import DEFAULT_COMPANY_ID

            scope = frozenset({DEFAULT_COMPANY_ID, MOCHA_ID})
            set_company_scope(db, scope)
            return scope

        app.dependency_overrides[apply_company_scope] = _override_scope

        yield db

        app.dependency_overrides.clear()


def test_list_route_stamps_company_on_every_row(api):
    """AC-D1 end to end: a GET /attachments/ row carries `company_id` and a
    RESOLVED `company_name`, not just the id."""
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = api
    type_id = _att_type(db)
    mocha_att = _attachment(db, company_id=MOCHA_ID, type_id=type_id)
    db.commit()

    with TestClient(app) as c:
        res = c.get(
            f"/api/v1/resource-management/attachments/?attachment_ids={mocha_att.id}"
        )

    assert res.status_code == 200, res.text
    rows = res.json()["data"]
    assert len(rows) == 1
    assert rows[0]["company_id"] == MOCHA_ID
    assert rows[0]["company_name"] == "Mocha"
