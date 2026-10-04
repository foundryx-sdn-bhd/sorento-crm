"""Customer ingest sets the AutoCount sales agent (CUSTOMER-SALES-AGENT, AC-1 .. AC-6).

PLAN: documentation/plans/customers/PLAN-customer-sales-agent-4oct.md
UAC:  documentation/plans/customers/customer-sales-agent-4oct-acceptance-criteria.md

A customers push may carry `sales_agent_code`. It resolves (case/space-insensitive) over the
`sales_agents` master and is set on the linked ledger and fanned out to every customers row of
the same company with the same customer code. Rows are read through raw SQL.

Substrate: `tests._pg_fixture.blank_session()`, every agent and customer is seeded here.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.company import Company
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session

MARKER = "ZZCSA"

INGEST_CUSTOMERS = "/api/v1/external/ingest/customers"
CONTRACT_URL = "/api/v1/external/contract"

AGENT_CODE = "ZZCSA AGENT-A III"
OTHER_AGENT_CODE = "ZZCSA AGENT-B I"

_USER_ID = "7d0dad30-1111-4222-8333-4444555588c1"
_ROLE_ID = "7d0dad30-2222-4222-8333-4444555588c2"


def _ref(stem: str) -> str:
    return f"{MARKER}:{stem}:{uuid.uuid4().hex[:8]}"


def _seed_principal(db) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db.add(
        UserRole(
            id=_ROLE_ID,
            slug="superadmin",
            name=f"{MARKER} Superadmin",
            description="",
            is_protected=True,
            is_default=False,
        )
    )
    db.flush()
    db.add(
        User(
            id=_USER_ID,
            name=f"{MARKER} admin",
            email=f"{MARKER.lower()}-admin@test.com",
            password="x",
            status="active",
        )
    )
    db.flush()
    db.add(UserRoleAssignment(user_id=_USER_ID, role_id=_ROLE_ID))
    db.flush()


class _Env:
    def __init__(self, client: TestClient, db):
        self.client = client
        self.db = db
        self.company_a = DEFAULT_COMPANY_ID
        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZCS{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()
        self.company_b_code = other.code
        self.agent_id = self.seed_agent(AGENT_CODE)
        self.other_agent_id = self.seed_agent(OTHER_AGENT_CODE)
        db.commit()

    def seed_agent(self, code: str) -> str:
        agent_id = str(uuid.uuid4())
        self.db.execute(
            text(
                "INSERT INTO sales_agents (id, sales_agent, source, is_active, company_id) "
                "VALUES (:i, :c, 'manual', true, NULL)"
            ),
            {"i": agent_id, "c": code},
        )
        return agent_id

    def seed_customer(self, *, code: str, company_id: str, agent_id=None, name=None) -> str:
        cid = str(uuid.uuid4())
        self.db.execute(
            text(
                "INSERT INTO customers (id, customer_code, customer_name, company_id, "
                "is_active, sales_agent_id, created_at, updated_at) "
                "VALUES (:i, :c, :n, :co, true, :a, now(), now())"
            ),
            {"i": cid, "c": code, "n": name or f"{MARKER} {code}", "co": company_id, "a": agent_id},
        )
        return cid

    def post_customer(
        self, *, code: str, company_code=None, dry_run=False, source_ref=None, **extra
    ):
        record = {
            "source_ref": source_ref or _ref("DEBTOR"),
            "code": code,
            "name": f"{MARKER} pushed {code}",
            **extra,
        }
        url = f"{INGEST_CUSTOMERS}?dry_run=true" if dry_run else INGEST_CUSTOMERS
        res = self.client.post(
            url, json={"companyCode": company_code or self.company_a_code, "records": [record]}
        )
        assert res.status_code == 200, res.text
        return record, res.json()["records"][0]

    def agent_of(self, customer_id: str):
        self.db.expire_all()
        value = self.db.execute(
            text("SELECT sales_agent_id FROM customers WHERE id = :i"), {"i": customer_id}
        ).scalar()
        return str(value) if value is not None else None

    def rows_by_code(self, code: str, company_id: str) -> list[dict]:
        self.db.expire_all()
        return [
            dict(r)
            for r in self.db.execute(
                text(
                    "SELECT id, sales_agent_id FROM customers "
                    "WHERE lower(btrim(customer_code)) = lower(btrim(:c)) AND company_id = :co"
                ),
                {"c": code, "co": company_id},
            ).mappings()
        ]


@pytest.fixture
def env():
    from app.dependencies import (  # safe: app.main is already loaded
        get_current_user,
        get_current_user_or_api_key,
        get_db,
        get_external_api_user,
    )
    from app.models.base import set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        _seed_principal(db)

        def _override_get_db():
            yield db

        def _override_user():
            return {"id": _USER_ID, "email": f"{MARKER.lower()}-admin@test.com"}

        def _override_company_scope():
            set_company_scope(db, None)
            return None

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_user
        app.dependency_overrides[get_external_api_user] = _override_user
        app.dependency_overrides[apply_company_scope] = _override_company_scope
        try:
            with TestClient(app) as client:
                yield _Env(client, db)
        finally:
            app.dependency_overrides.clear()


def _code() -> str:
    return f"{MARKER}-{uuid.uuid4().hex[:6].upper()}"


# ===================================================================== AC-1
class TestSetOnLinkedRow:
    def test_code_in_any_case_and_spacing_sets_the_agent_on_the_linked_row(self, env):
        code = _code()
        _, entry = env.post_customer(code=code, sales_agent_code=f" {AGENT_CODE.lower()} ")

        assert entry["outcome"] == "created", entry
        rows = env.rows_by_code(code, env.company_a)
        assert len(rows) == 1
        assert str(rows[0]["sales_agent_id"]) == env.agent_id


# ===================================================================== AC-2
class TestFanOut:
    def test_same_code_rows_of_the_company_get_the_agent_others_do_not(self, env):
        code = _code()
        backcreated = env.seed_customer(
            code=f" {code.lower()} ",
            company_id=env.company_a,
            name=f"{MARKER} back-created by a delivery order",
        )
        other_code = env.seed_customer(code=_code(), company_id=env.company_a)
        other_company = env.seed_customer(code=code, company_id=env.company_b)
        env.db.commit()

        _, entry = env.post_customer(code=code, sales_agent_code=AGENT_CODE)

        assert entry["outcome"] != "failed", entry
        assert env.agent_of(backcreated) == env.agent_id
        for row in env.rows_by_code(code, env.company_a):
            assert str(row["sales_agent_id"]) == env.agent_id
        assert env.agent_of(other_code) is None
        assert env.agent_of(other_company) is None


# ===================================================================== fan-out paths
class TestFanOutPaths:
    def test_ref_path_updates_the_linked_row_and_the_sibling(self, env):
        code = _code()
        ref = _ref("REF")
        env.post_customer(code=code, source_ref=ref)
        sibling = env.seed_customer(
            code=f" {code.lower()} ", company_id=env.company_a, agent_id=env.other_agent_id
        )
        env.db.commit()

        _, entry = env.post_customer(code=code, source_ref=ref, sales_agent_code=AGENT_CODE)

        assert entry["outcome"] == "updated", entry
        assert env.agent_of(sibling) == env.agent_id
        rows = env.rows_by_code(code, env.company_a)
        assert len(rows) == 2
        assert all(str(r["sales_agent_id"]) == env.agent_id for r in rows)

    def test_adopt_path_updates_the_adopted_row_and_the_sibling(self, env):
        code = _code()
        name = f"{MARKER} adopted {code}"
        adopted = env.seed_customer(code=code, company_id=env.company_a, name=name)
        sibling = env.seed_customer(
            code=f" {code.lower()}", company_id=env.company_a, name=f"{MARKER} other name"
        )
        env.db.commit()

        record = {
            "source_ref": _ref("ADOPT"), "code": code, "name": name,
            "sales_agent_code": AGENT_CODE,
        }
        res = env.client.post(
            INGEST_CUSTOMERS, json={"companyCode": env.company_a_code, "records": [record]}
        )
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]

        assert entry["outcome"] == "updated", entry
        assert env.agent_of(adopted) == env.agent_id
        assert env.agent_of(sibling) == env.agent_id

    def test_a_pushed_agent_overwrites_the_stored_one(self, env):
        code = _code()
        ref = _ref("OVR")
        env.post_customer(code=code, source_ref=ref, sales_agent_code=OTHER_AGENT_CODE)
        assert [str(r["sales_agent_id"]) for r in env.rows_by_code(code, env.company_a)] == [
            env.other_agent_id
        ]

        env.post_customer(code=code, source_ref=ref, sales_agent_code=AGENT_CODE)

        assert [str(r["sales_agent_id"]) for r in env.rows_by_code(code, env.company_a)] == [
            env.agent_id
        ]

    def test_another_companys_agent_is_unresolved(self, env):
        foreign = str(uuid.uuid4())
        env.db.execute(
            text(
                "INSERT INTO sales_agents (id, sales_agent, source, is_active, company_id) "
                "VALUES (:i, :c, 'manual', true, :co)"
            ),
            {"i": foreign, "c": f"{MARKER} FOREIGN I", "co": env.company_b},
        )
        env.db.commit()
        code = _code()

        _, entry = env.post_customer(code=code, sales_agent_code=f"{MARKER} FOREIGN I")

        assert "agent_unresolved" in entry.get("warnings", [])
        rows = env.rows_by_code(code, env.company_a)
        assert rows and all(r["sales_agent_id"] is None for r in rows)

    def test_the_sibling_change_is_audited_and_stamped(self, env):
        code = _code()
        sibling = env.seed_customer(code=f" {code.lower()} ", company_id=env.company_a)
        env.db.commit()

        env.post_customer(code=code, sales_agent_code=AGENT_CODE)

        env.db.expire_all()
        audited = env.db.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE entity_id = :i AND action = 'UPDATE'"
            ),
            {"i": sibling},
        ).scalar()
        assert audited >= 1
        stamped = env.db.execute(
            text("SELECT updated_at > created_at FROM customers WHERE id = :i"), {"i": sibling}
        ).scalar()
        assert stamped is True


# ===================================================================== AC-3
class TestUnknownCode:
    def test_unknown_code_warns_and_leaves_the_agent_untouched(self, env):
        code = _code()
        ref = _ref("UNK")
        env.post_customer(code=code, source_ref=ref, sales_agent_code=OTHER_AGENT_CODE)
        sibling = env.seed_customer(
            code=f" {code.lower()}", company_id=env.company_a, agent_id=env.other_agent_id
        )
        env.db.commit()

        _, entry = env.post_customer(
            code=code, source_ref=ref, sales_agent_code=f"{MARKER} NOBODY IX"
        )

        assert entry["outcome"] != "failed", entry
        assert "agent_unresolved" in entry.get("warnings", [])
        for row in env.rows_by_code(code, env.company_a):
            assert str(row["sales_agent_id"]) == env.other_agent_id
        assert env.agent_of(sibling) == env.other_agent_id


# ===================================================================== AC-4
class TestAbsentAndBlank:
    def test_absent_key_leaves_the_stored_agent(self, env):
        code = _code()
        ref = _ref("ABS")
        env.post_customer(code=code, source_ref=ref, sales_agent_code=AGENT_CODE)

        _, entry = env.post_customer(code=code, source_ref=ref)

        assert entry["outcome"] != "failed", entry
        assert "agent_unresolved" not in entry.get("warnings", [])
        rows = env.rows_by_code(code, env.company_a)
        assert [str(r["sales_agent_id"]) for r in rows] == [env.agent_id]

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_blank_leaves_the_agent_on_the_linked_and_same_code_rows(self, env, blank):
        """Owner ruling: blank behaves like an absent key, it never clears."""
        code = _code()
        ref = _ref("BLANK")
        env.post_customer(code=code, source_ref=ref, sales_agent_code=AGENT_CODE)
        sibling = env.seed_customer(
            code=f"{code.lower()} ", company_id=env.company_a, agent_id=env.agent_id
        )
        env.db.commit()

        _, entry = env.post_customer(code=code, source_ref=ref, sales_agent_code=blank)

        assert entry["outcome"] != "failed", entry
        assert "agent_unresolved" not in entry.get("warnings", [])
        assert env.agent_of(sibling) == env.agent_id
        rows = env.rows_by_code(code, env.company_a)
        assert rows and all(str(r["sales_agent_id"]) == env.agent_id for r in rows)


# ===================================================================== AC-5
class TestDryRun:
    def test_dry_run_writes_no_agent_anywhere(self, env):
        # Control: the same push for real sets the agent, so a dry run that writes
        # nothing is distinguishable from a feature that does not exist yet.
        control = _code()
        env.post_customer(code=control, sales_agent_code=AGENT_CODE)
        assert [str(r["sales_agent_id"]) for r in env.rows_by_code(control, env.company_a)] == [
            env.agent_id
        ]

        code = _code()
        ref = _ref("DRY")
        env.post_customer(code=code, source_ref=ref)
        sibling = env.seed_customer(code=f" {code.lower()} ", company_id=env.company_a)
        env.db.commit()

        res, entry = env.post_customer(
            code=code, source_ref=ref, sales_agent_code=AGENT_CODE, dry_run=True
        )

        assert entry["outcome"] != "failed", entry
        assert env.agent_of(sibling) is None
        rows = env.rows_by_code(code, env.company_a)
        assert rows and all(r["sales_agent_id"] is None for r in rows)


# ===================================================================== AC-6
class TestContract:
    def test_contract_lists_the_field_and_the_warning(self, env):
        res = env.client.get(CONTRACT_URL)
        assert res.status_code == 200, res.text
        body = res.json()
        assert "sales_agent_code" in body["fields_added"]["customers"]
        assert "agent_unresolved" in body["warnings"]
