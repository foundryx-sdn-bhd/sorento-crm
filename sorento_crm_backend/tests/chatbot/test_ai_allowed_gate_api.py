"""AI-ALLOWED-GATE: the per-contact "AI replies allowed" flag over the API, sync, copy and script.

Plan `documentation/plans/chatbot/PLAN-ai-allowed-gate-5oct.md`, UAC AC-AI-9 .. AC-AI-13.

`respond_contacts.chatbot_allowed` (BOOLEAN NOT NULL, default true). Every contact read carries
it (asserted here because `contact_to_response_dict` is built by hand and `response_model`
drops undeclared fields), `PUT .../contacts/{id}/chatbot` writes it (absent = leave alone,
`contacts.edit` needed), the list filters on it (`ai=yes|no`), the n8n and scheduled syncs
create NEW contacts blocked, copy-access treats it as a switch, and the system API and the
`set_contact_ai.py` script drive it in bulk.

Postgres only (`blank_session`); every row is seeded here. The Respond.io client is mocked
in the sync test, nothing leaves the box.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db, get_external_api_user
from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.schemas.user import RespondContactCreate
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.error_handler import AppException
from app.services.user_service import UserPermissionService

from tests._pg_fixture import blank_session

SORENTO = DEFAULT_COMPANY_ID
BASE = "/api/v1/user-management/contacts"
SYSTEM = "/api/v1/system/respond-contacts"
EXTERNAL = "/api/v1/external/respond-contacts"
VIEW = "user_management.contacts.view"
EDIT = "user_management.contacts.edit"
SYS_VIEW = "system.respond_workspaces.view"
SYS_EDIT = "system.respond_workspaces.edit"
SYNC = "integration.contacts.sync"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "set_contact_ai.py"


@pytest.fixture
def db():
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        set_company_scope(session, frozenset({SORENTO}))
        yield session


@pytest.fixture
def perms():
    return {"granted": {VIEW, EDIT, SYS_VIEW, SYS_EDIT, SYNC}}


@pytest.fixture
def client(db, perms, monkeypatch):
    from app.database import get_db as database_get_db

    principal = {"id": "zzt-admin", "email": "zzt-admin@test.com"}

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({SORENTO})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[database_get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[get_external_api_user] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in perms["granted"],
    )
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _seed(db, *, name: str = "ZZT Contact", allowed: bool = True) -> RespondContact:
    """A contact through the ORM, with the flag explicit (the production default for a new row is blocked)."""
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+6011{str(uuid.uuid4().int)[:8]}",
        name=name,
        respond_io_id=str(uuid.uuid4().int)[:9],
        session_vars={},
    )
    db.add(row)
    row.chatbot_allowed = allowed
    db.commit()
    return row


def _set_flag(db, contact_id: str, allowed: bool) -> None:
    db.execute(
        text("UPDATE respond_contacts SET chatbot_allowed = :a WHERE id = :i"),
        {"a": allowed, "i": contact_id},
    )
    db.commit()


def _stored(db, contact_id: str) -> bool:
    return db.execute(
        text("SELECT chatbot_allowed FROM respond_contacts WHERE id = :i"), {"i": contact_id}
    ).scalar()


# --------------------------------------------------------------------------
# AC-AI-9: the contact card (GET / PUT)
# --------------------------------------------------------------------------


def test_a_contact_created_through_the_service_reads_blocked(client, db):
    """Owner ruling 5 Oct 2026 (Q1 a): the ORM default for a new contact is blocked."""
    from app.services.contact_service import ContactService

    created = ContactService(db).create_contact(
        RespondContactCreate(phone_number=f"+6011{str(uuid.uuid4().int)[:8]}", name="ZZT Fresh")
    )
    assert client.get(f"{BASE}/{created.id}").json()["chatbot_allowed"] is False


def test_a_blocked_contact_reads_blocked(client, db):
    """The dict builder lists the field; the schema default alone would read every contact allowed."""
    contact = _seed(db, allowed=False)
    assert client.get(f"{BASE}/{contact.id}").json()["chatbot_allowed"] is False


def test_put_blocks_and_allows_the_contact_and_absent_leaves_it_alone(client, db):
    contact = _seed(db)

    resp = client.put(f"{BASE}/{contact.id}/chatbot", json={"chatbot_allowed": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["chatbot_allowed"] is False
    assert _stored(db, contact.id) is False

    resp = client.put(f"{BASE}/{contact.id}/chatbot", json={"notify_salesman": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["chatbot_allowed"] is False
    assert _stored(db, contact.id) is False

    resp = client.put(f"{BASE}/{contact.id}/chatbot", json={"chatbot_allowed": True})
    assert resp.json()["chatbot_allowed"] is True
    assert _stored(db, contact.id) is True


def test_writing_the_flag_needs_the_contact_edit_grant(client, db, perms):
    contact = _seed(db)
    perms["granted"].discard(EDIT)

    resp = client.put(f"{BASE}/{contact.id}/chatbot", json={"chatbot_allowed": False})

    assert resp.status_code == 403, resp.text
    assert _stored(db, contact.id) is True


# --------------------------------------------------------------------------
# AC-AI-10: the contacts list
# --------------------------------------------------------------------------


def _list(client, **params):
    resp = client.get(f"{BASE}/", params={"limit": 100, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_list_rows_carry_the_flag(client, db):
    allowed = _seed(db, name="ZZT Listed Allowed")
    blocked = _seed(db, name="ZZT Listed Blocked", allowed=False)

    rows = {r["id"]: r for r in _list(client)}

    assert rows[allowed.id]["chatbot_allowed"] is True
    assert rows[blocked.id]["chatbot_allowed"] is False


def test_list_filter_ai_yes_selects_only_allowed_contacts(client, db):
    allowed = _seed(db, name="ZZT Yes")
    _seed(db, name="ZZT No", allowed=False)

    assert [r["id"] for r in _list(client, ai="yes")] == [allowed.id]


def test_list_filter_ai_no_selects_only_blocked_contacts(client, db):
    _seed(db, name="ZZT Yes")
    blocked = _seed(db, name="ZZT No", allowed=False)

    assert [r["id"] for r in _list(client, ai="no")] == [blocked.id]


def test_list_filter_ai_rejects_any_other_value(client, db):
    _seed(db)
    assert client.get(f"{BASE}/", params={"ai": "maybe"}).status_code == 422


# --------------------------------------------------------------------------
# AC-AI-11: creation paths
# --------------------------------------------------------------------------


def test_create_contact_leaves_the_default_blocked(db):
    from app.services.contact_service import ContactService

    created = ContactService(db).create_contact(
        RespondContactCreate(phone_number=f"+6011{str(uuid.uuid4().int)[:8]}", name="ZZT Default")
    )
    assert _stored(db, created.id) is False


def test_the_n8n_sync_creates_a_new_contact_blocked(client, db):
    phone = f"+6011{str(uuid.uuid4().int)[:8]}"
    resp = client.post(EXTERNAL, json={"phone_number": phone, "name": "ZZT Synced", "id": 987654321})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "created"
    assert _stored(db, body["id"]) is False


def test_the_n8n_sync_updating_an_existing_contact_leaves_the_flag_alone(client, db):
    contact = _seed(db, name="ZZT Existing", allowed=True)
    resp = client.post(EXTERNAL, json={"phone_number": contact.phone_number, "name": "ZZT Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"] == "updated"
    assert _stored(db, contact.id) is True


def test_the_scheduled_sync_creates_a_new_local_contact_blocked(db):
    from app.services import respond_sync_handler

    phone = f"+6011{str(uuid.uuid4().int)[:8]}"
    fake = MagicMock()
    fake.list_contacts.return_value = {
        "data": [{"id": 123456789, "firstName": "ZZT", "lastName": "Sched", "phone": phone}]
    }
    fake.get_contact_by_phone.return_value = {}
    fake.update_contact.return_value = {}
    task = SimpleNamespace(id=str(uuid.uuid4()))

    with patch.object(respond_sync_handler, "RespondClient", return_value=fake):
        summary = respond_sync_handler.run_respond_contacts_sync(db, task)

    assert summary["created_local"] == 1, summary
    row = db.query(RespondContact).filter(RespondContact.phone_number == phone).one()
    assert _stored(db, row.id) is False


# --------------------------------------------------------------------------
# Copy access: the flag is one of the switches
# --------------------------------------------------------------------------


def test_copy_access_diff_reports_a_change_when_the_flag_differs(db):
    from app.services import contact_access_copy_service as svc

    source = _seed(db, name="ZZT Source")
    target = _seed(db, name="ZZT Target", allowed=False)

    assert ("chatbot_allowed", "AI replies") in svc.SWITCHES
    snaps = svc.snapshots(db, [source, target])
    names = svc.names_for(db, snaps.values())
    changes = svc.diff(snaps[source.id], snaps[target.id], names)

    row = next(c for c in changes if c["facet"] == "chatbot_allowed")
    assert (row["label"], row["before"], row["after"]) == ("AI replies", False, True)


def test_copy_access_diff_reports_nothing_when_the_flag_matches(db):
    from app.services import contact_access_copy_service as svc

    source = _seed(db, name="ZZT Source")
    target = _seed(db, name="ZZT Target")

    snaps = svc.snapshots(db, [source, target])
    names = svc.names_for(db, snaps.values())
    changes = svc.diff(snaps[source.id], snaps[target.id], names)

    assert "chatbot_allowed" not in [c["facet"] for c in changes]


def test_copy_access_writes_the_flag_onto_the_target(client, db):
    source = _seed(db, name="ZZT Source")
    target = _seed(db, name="ZZT Target", allowed=False)

    resp = client.post(
        f"{BASE}/bulk-copy-access",
        json={"source_contact_id": source.id, "target_contact_ids": [target.id], "dry_run": False},
    )

    assert resp.status_code == 200, resp.text
    assert _stored(db, target.id) is True


# --------------------------------------------------------------------------
# AC-AI-12: the system API
# --------------------------------------------------------------------------


def test_system_list_rows_carry_the_flag_beside_outbound_enabled(client, db):
    allowed = _seed(db, name="ZZT Sys Allowed")
    blocked = _seed(db, name="ZZT Sys Blocked", allowed=False)

    rows = {r["id"]: r for r in client.get(SYSTEM).json()["data"]}

    assert rows[allowed.id]["chatbot_allowed"] is True
    assert rows[blocked.id]["chatbot_allowed"] is False
    assert "outbound_enabled" in rows[allowed.id]


def test_system_list_filter_ai_allowed_selects_the_allowed_slice(client, db):
    allowed = _seed(db, name="ZZT Sys Allowed")
    _seed(db, name="ZZT Sys Blocked", allowed=False)

    resp = client.get(SYSTEM, params={"ai": "allowed"})
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()["data"]] == [allowed.id]


def test_system_list_filter_ai_blocked_selects_the_blocked_slice(client, db):
    _seed(db, name="ZZT Sys Allowed")
    blocked = _seed(db, name="ZZT Sys Blocked", allowed=False)

    resp = client.get(SYSTEM, params={"ai": "blocked"})
    assert resp.status_code == 200, resp.text
    assert [r["id"] for r in resp.json()["data"]] == [blocked.id]


def test_system_list_filter_ai_other_value_is_a_400(client, db):
    _seed(db)
    assert client.get(SYSTEM, params={"ai": "maybe"}).status_code == 400


# --------------------------------------------------------------------------
# AC-AI-13: the service and the script
# --------------------------------------------------------------------------


def test_is_allowed_is_false_when_no_row_matches(db):
    from app.services import chatbot_allowed_service as svc

    assert svc.is_allowed(db, f"ZZT-none-{uuid.uuid4().hex[:8]}") is False


def test_is_allowed_follows_the_flag(db):
    from app.services import chatbot_allowed_service as svc

    ok = _seed(db, name="ZZT Ok")
    no = _seed(db, name="ZZT No", allowed=False)

    assert svc.is_allowed(db, ok.respond_io_id) is True
    assert svc.is_allowed(db, no.respond_io_id) is False


def test_is_allowed_is_false_when_any_row_with_that_respond_id_is_blocked(db):
    from app.services import chatbot_allowed_service as svc

    first = _seed(db, name="ZZT Dup A", allowed=True)
    twin = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+6012{str(uuid.uuid4().int)[:8]}",
        name="ZZT Dup B",
        respond_io_id=first.respond_io_id,
        session_vars={},
        chatbot_allowed=False,
    )
    db.add(twin)
    db.commit()

    assert svc.is_allowed(db, first.respond_io_id) is False
    _set_flag(db, twin.id, True)
    assert svc.is_allowed(db, first.respond_io_id) is True


def test_status_counts_split_allowed_and_blocked(db):
    from app.services import chatbot_allowed_service as svc

    _seed(db, name="ZZT A")
    _seed(db, name="ZZT B")
    _seed(db, name="ZZT C", allowed=False)

    assert svc.status_counts(db) == {"allowed": 2, "blocked": 1, "total": 3}


def test_set_all_changes_every_row_and_reports_how_many(db):
    from app.services import chatbot_allowed_service as svc

    a = _seed(db, name="ZZT A")
    b = _seed(db, name="ZZT B", allowed=False)

    assert svc.set_all(db, allowed=False) == 1
    assert _stored(db, a.id) is False
    assert _stored(db, b.id) is False
    assert svc.status_counts(db) == {"allowed": 0, "blocked": 2, "total": 2}


def test_set_all_dry_run_counts_but_writes_nothing(db):
    from app.services import chatbot_allowed_service as svc

    a = _seed(db, name="ZZT A")
    _seed(db, name="ZZT B")

    assert svc.set_all(db, allowed=False, dry_run=True) == 2
    assert _stored(db, a.id) is True


@pytest.mark.parametrize("by", ["id", "respond_io_id", "phone_number"])
def test_set_contact_resolves_the_reference_and_flips_the_flag(db, by):
    from app.services import chatbot_allowed_service as svc

    contact = _seed(db, name="ZZT One")

    found, changed = svc.set_contact(db, getattr(contact, by), allowed=False)

    assert changed is True
    assert found.id == contact.id
    assert _stored(db, contact.id) is False


def test_set_contact_reports_no_change_when_already_that_value(db):
    from app.services import chatbot_allowed_service as svc

    contact = _seed(db, name="ZZT Same")
    _, changed = svc.set_contact(db, contact.id, allowed=True)
    assert changed is False


def test_set_contact_dry_run_reports_the_change_but_writes_nothing(db):
    from app.services import chatbot_allowed_service as svc

    contact = _seed(db, name="ZZT Dry")
    _, changed = svc.set_contact(db, contact.id, allowed=False, dry_run=True)
    assert changed is True
    assert _stored(db, contact.id) is True


def test_set_contact_raises_a_404_for_an_unknown_reference(db):
    from app.services import chatbot_allowed_service as svc

    with pytest.raises(AppException) as err:
        svc.set_contact(db, f"ZZT-missing-{uuid.uuid4().hex[:8]}", allowed=False)
    assert err.value.status_code == 404


def _load_script():
    assert SCRIPT.exists(), f"{SCRIPT} is missing"
    spec = importlib.util.spec_from_file_location("set_contact_ai_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NoClose:
    """The test's session handed to a script that closes its own."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def _run_script(db, *argv: str) -> int:
    module = _load_script()
    with patch.object(module, "SessionLocal", lambda: _NoClose(db)), patch("sys.argv", ["set_contact_ai.py", *argv]):
        return module.main()


def test_script_without_yes_is_a_dry_run_that_exits_2_and_writes_nothing(db):
    contact = _seed(db, name="ZZT Script")
    assert _run_script(db, "--off", "--contact", contact.respond_io_id) == 2
    assert _stored(db, contact.id) is True


def test_script_with_yes_blocks_one_contact(db):
    contact = _seed(db, name="ZZT Script")
    other = _seed(db, name="ZZT Untouched")
    assert _run_script(db, "--off", "--contact", contact.respond_io_id, "--yes") == 0
    assert _stored(db, contact.id) is False
    assert _stored(db, other.id) is True


def test_script_off_all_with_yes_blocks_everyone_and_on_all_restores(db):
    a = _seed(db, name="ZZT A")
    b = _seed(db, name="ZZT B")
    assert _run_script(db, "--off", "--all", "--yes") == 0
    assert (_stored(db, a.id), _stored(db, b.id)) == (False, False)
    assert _run_script(db, "--on", "--all", "--yes") == 0
    assert (_stored(db, a.id), _stored(db, b.id)) == (True, True)


def test_script_explicit_dry_run_writes_nothing_and_exits_0(db):
    contact = _seed(db, name="ZZT Script")
    assert _run_script(db, "--off", "--all", "--dry-run") == 0
    assert _stored(db, contact.id) is True


def test_script_status_never_writes_and_exits_0(db):
    contact = _seed(db, name="ZZT Script")
    assert _run_script(db, "--status") == 0
    assert _stored(db, contact.id) is True
