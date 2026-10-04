"""Person label derivation for sales agents (CUSTOMER-SALES-AGENT, AC-7).

`derive_person_label` strips a trailing roman-numeral level; it is filled where the label is
empty (backfill, agent create, ingest create) and never overwrites a label staff typed.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session

MARKER = "ZZCSA"
INGEST_AGENTS = "/api/v1/external/ingest/sales_agents"

_USER_ID = "7d0dad30-1111-4222-8333-4444555588d1"
_ROLE_ID = "7d0dad30-2222-4222-8333-4444555588d2"


# --------------------------------------------------------------- pure derivation
@pytest.mark.parametrize(
    "code, expected",
    [
        ("AGENT-A III", "AGENT-A"),
        ("AGENT-A I", "AGENT-A"),
        ("AGENT-B X IV", "AGENT-B X"),
        ("AGENT-C - I", "AGENT-C"),
        ("AGENT-D II", "AGENT-D"),
        ("ABC", "ABC"),
        ("QI", "QI"),
        ("  agent-e i ", "AGENT-E"),
        ("", None),
        (None, None),
    ],
)
def test_derive_person_label(code, expected):
    from app.services.scm.sales_agent_service import derive_person_label

    assert derive_person_label(code) == expected


# --------------------------------------------------------------- service
@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _insert_agent(db, code: str, label=None) -> str:
    agent_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO sales_agents (id, sales_agent, source, is_active, person_label) "
            "VALUES (:i, :c, 'manual', true, :l)"
        ),
        {"i": agent_id, "c": code, "l": label},
    )
    return agent_id


def _label(db, code: str):
    db.expire_all()
    return db.execute(
        text("SELECT person_label FROM sales_agents WHERE upper(btrim(sales_agent)) = :c"),
        {"c": code.strip().upper()},
    ).scalar()


def test_resolve_or_create_fills_the_derived_label_on_a_new_agent(db):
    from app.services.scm.sales_agent_service import resolve_or_create

    agent = resolve_or_create(db, "zzcsa newp iv")

    assert agent.sales_agent == "ZZCSA NEWP IV"
    assert agent.person_label == "ZZCSA NEWP"


def test_resolve_or_create_keeps_a_label_staff_typed(db):
    from app.services.scm.sales_agent_service import resolve_or_create

    _insert_agent(db, "ZZCSA KEEP II", "Person X")

    agent = resolve_or_create(db, "zzcsa keep ii")

    assert agent.person_label == "Person X"
    assert _label(db, "ZZCSA KEEP II") == "Person X"


def _run_migration(db):
    """Run the label migration's own `upgrade()` over the test connection."""
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "sa_person_label_0001.py"
    spec = importlib.util.spec_from_file_location("sa_person_label_0001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        module.upgrade()
    return module


def test_migration_fills_only_null_or_blank_labels(db):
    _insert_agent(db, "ZZCSA FILL I", None)
    _insert_agent(db, "ZZCSA BLANK III", "  ")
    _insert_agent(db, "ZZCSA TYPED II", "Person X")

    _run_migration(db)

    assert _label(db, "ZZCSA FILL I") == "ZZCSA FILL"
    assert _label(db, "ZZCSA BLANK III") == "ZZCSA BLANK"
    assert _label(db, "ZZCSA TYPED II") == "Person X"


_PARITY_CODES = [
    "AGENT-A III", "AGENT-A I", "AGENT-B X IV", "AGENT-C - I", "AGENT-D II", "ABC", "QI",
    "  agent-e i ", "", "-", " - I", "I", "AGENT-F  -  VI", "A-IX",
]


def test_migration_regex_matches_derive_person_label():
    import importlib.util
    from pathlib import Path

    from app.services.scm.sales_agent_service import derive_person_label, normalize_code

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "sa_person_label_0001.py"
    spec = importlib.util.spec_from_file_location("sa_person_label_0001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for code in _PARITY_CODES:
        migrated = module._LEVEL_SUFFIX.sub("", normalize_code(code)).rstrip(" -") or None
        assert migrated == derive_person_label(code), code


def test_sql_person_key_matches_python_derivation(db):
    """The group list's SQL key and `derive_person_label` agree on every case."""
    from app.models.sales_agent import SalesAgent
    from app.services.customer_group_service import CustomerGroupService
    from app.services.scm.sales_agent_service import derive_person_label

    key = CustomerGroupService._person_key_sql()
    for code in _PARITY_CODES:
        agent_id = _insert_agent(db, f"{code}", None)
        got = db.query(key).filter(SalesAgent.id == agent_id).scalar()
        derived = derive_person_label(code)
        assert got == (derived.lower() if derived else None), repr(code)


# --------------------------------------------------------------- ingest
@pytest.fixture
def client(db):
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_db,
        get_external_api_user,
    )
    from app.models.base import set_company_scope
    from app.models.user import User, UserRole, UserRoleAssignment
    from app.services.company_scope_resolver import apply_company_scope

    db.add(
        UserRole(
            id=_ROLE_ID, slug="superadmin", name=f"{MARKER} Superadmin",
            description="", is_protected=True, is_default=False,
        )
    )
    db.flush()
    db.add(
        User(
            id=_USER_ID, name=f"{MARKER} admin", email=f"{MARKER.lower()}-pl@test.com",
            password="x", status="active",
        )
    )
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.commit()

    def _override_get_db():
        yield db

    def _override_user():
        return {"id": _USER_ID, "email": f"{MARKER.lower()}-pl@test.com"}

    def _override_company_scope():
        set_company_scope(db, None)
        return None

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_current_user_or_api_key] = _override_user
    app.dependency_overrides[get_external_api_user] = _override_user
    app.dependency_overrides[apply_company_scope] = _override_company_scope
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _push_agent(client, db, code: str, **extra):
    company_code = db.execute(
        text("SELECT code FROM companies WHERE id = :id"), {"id": DEFAULT_COMPANY_ID}
    ).scalar()
    res = client.post(
        INGEST_AGENTS,
        json={
            "companyCode": company_code,
            "records": [{"source_ref": f"agent:{code}", "code": code, **extra}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] == "created", res.text


def test_ingest_create_without_a_label_derives_one(client, db):
    _push_agent(client, db, "ZZCSA INGA III")

    assert _label(db, "ZZCSA INGA III") == "ZZCSA INGA"


def test_ingest_create_with_a_label_stores_it_as_sent(client, db):
    _push_agent(client, db, "ZZCSA INGB III", person_label="Person T")

    assert _label(db, "ZZCSA INGB III") == "Person T"
