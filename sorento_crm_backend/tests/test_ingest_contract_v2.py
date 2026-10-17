"""Group V0 - contract version + verdict warnings (slice S0).

  AC-V0-1  `GET /api/v1/external/contract` -> 200 {"version": 2, "entities": [...]}
           listing sales_orders, purchase_orders, shipping_orders and every
           master; unauthenticated -> 401; missing `integration.contract.read`
           -> 403.
  AC-V0-2  golden v1 payload: a plain A3 sales-order / purchase-order record
           ingests, is written, and reads back exactly as
           `tests/test_ingest_documents.py` already pins -- so a later v2
           slice touching the same code path cannot silently drift it. Unlike
           the rest of this file, this is NOT expected to fail today:
           sales_orders/purchase_orders document ingest already ships
           (plan section 0), and v2's whole promise is that a v1 payload is
           byte-for-byte untouched. If this goes red once S1+ lands, that IS
           the regression the plan forbids.
  AC-V0-3  `RecordResult` gains an optional `warnings: [str]`, omitted from
           `as_dict()` when empty, present when not (same rule as `errors`).
  AC-V3-1  (slugs only) migration 472 creates
           `scm.shipping_orders.{view,add,edit,delete}` and
           `integration.contract.read`, and sweeps `.edit` the way migration
           445 already sweeps `scm.sales_orders.*` / `scm.purchase_orders.*`:
           a role holding `scm.purchase_orders.edit` also ends up holding
           `scm.shipping_orders.edit`.
  AC-V4-3  `ck_scm_order_link_claim_source` admits `'autocount'`, and
           `order_link_service.SOURCE_AUTOCOUNT == "autocount"`.

Substrate: the blank scratch schema (`tests/_pg_fixture.blank_session`) for
anything that writes a row, and a rolled-back real connection
(`app/database.engine`) for the migration-body tests, mirroring
`tests/test_migration_445_grant_sweep.py` -- the two migrations touch the
SAME live grant table, so a scratch schema would prove nothing about the SQL
that will actually run. Every code is minted under a `ZZTCV2` marker.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.database import engine
from app.models.scm import OrderLinkClaim
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import IngestOutcome, RecordResult

from ._pg_fixture import blank_session

# Reused rather than re-built: the fixture already seeds a company, one linked
# master of every kind a document points at, and a superadmin principal that
# bypasses RBAC entirely, and the payload builders already produce a v1-shaped
# A3 record. `env` is a fixture (imported, not called) - noqa keeps flake8
# quiet about the "unused" import.
from .test_ingest_documents import (  # noqa: F401
    INGEST_PO,
    INGEST_SO,
    READ_PO,
    READ_SO,
    _po_line,
    _po_record,
    _so_line,
    _so_record,
    env,
)

MARKER = "ZZTCV2"
CONTRACT_URL = "/api/v1/external/contract"


# ============================================================ AC-V0-1 contract
class TestContractEndpoint:
    """`GET /api/v1/external/contract` does not exist on this branch yet, so
    every assertion below is expected to fail on a 404 - the route itself is
    missing, not merely its guard or its body shape."""

    @pytest.fixture()
    def guard_db(self):
        with blank_session() as db:
            yield db

    @staticmethod
    def _seed_key(db, slugs: list[str], label: str) -> str:
        from app.models.integration import Integration
        from app.models.user import (
            User,
            UserPermission,
            UserRole,
            UserRoleAssignment,
            UserRolePermission,
        )
        from app.services.integration_key_service import IntegrationKeyService

        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"{MARKER.lower()}-{label}-{suffix}@integrations.local",
            name=f"{MARKER} {label}",
            status="ACTIVE",
            is_integration=True,
        )
        db.add(user)
        db.flush()
        role = UserRole(
            slug=f"{MARKER.lower()}_{label}_{suffix}",
            name=f"{MARKER} {label} role {suffix}",
        )
        db.add(role)
        db.flush()
        db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
        for slug in slugs:
            perm = UserPermission(slug=slug, name=slug)
            db.add(perm)
            db.flush()
            db.add(UserRolePermission(role_id=role.id, permission_id=perm.id))
        db.flush()
        integration = Integration(
            name=f"{MARKER}-{label}-{suffix}",
            type="autocount_esb",
            act_as_user_id=user.id,
            is_active=True,
        )
        db.add(integration)
        db.flush()
        return IntegrationKeyService(db).issue_key(integration)

    def test_a_valid_principal_gets_version_2_and_every_entity(self, env):
        """Superseded 2026-09-06 (ingest-parity-standardisation S4,
        D-final): the contract version is now the STRING "2.1", not the int
        `2` this test originally pinned - `tests/test_ingest_parity_s4_contract
        .py::TestAcP43ContractV21` is the new contract test for the full
        v2.1 shape (`fields_added`/`fields_removed`/`status_optional`
        /`absent_vs_null`/`warnings`); this test still proves the entity
        list and the 200/401/403 gating this file's own name is about.

        Bumped again 2026-09-08 (V5, ingest-contract-2-2-so-links): "2.2"
        adds from_so_line_ref/from_so_external/from_po_line_ref/
        from_po_number to purchase_orders/shipping_orders lines.
        """
        res = env.client.get(CONTRACT_URL)

        assert res.status_code == 200, res.text
        body = res.json()
        # Bumped again (autocount-brands-ingest, AC-13): "2.3" adds `brands`
        # as a first-class EntitySpec. Bumped again (ingest-products-code-wins,
        # SR0): "2.4" adds products code-wins deletion `codes`. Bumped again
        # (ingest-stock-balances-2-5, Foundryx SR5): "2.5" adds `stock_balances`.
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        # Bumped again (AC-CONSIGN-CRM): "2.9" adds consignment fields; "2.8" added `cancel_sales_orders`, `cancel_purchase_orders`.
        assert body["version"] == "2.10"
        entities = set(body["entities"])
        for expected in (
            "sales_orders",
            "purchase_orders",
            "shipping_orders",
            "product_categories",
            "units_of_measure",
            "warehouses",
            "suppliers",
            "customers",
            "products",
            "sales_agents",
        ):
            assert expected in entities, expected

    def test_unauthenticated_call_is_401(self):
        with TestClient(app) as client:
            res = client.get(CONTRACT_URL)

        assert res.status_code == 401, res.text

    def test_a_principal_missing_the_slug_is_403(self, guard_db):
        from app.dependencies import get_db

        key = self._seed_key(guard_db, [f"{MARKER.lower()}.unrelated.view"], "outsider")

        app.dependency_overrides[get_db] = lambda: guard_db
        try:
            with TestClient(app) as client:
                res = client.get(CONTRACT_URL, headers={"X-API-Key": key})
        finally:
            app.dependency_overrides.clear()

        assert res.status_code == 403, res.text
        assert "integration.contract.read" in res.text


# ================================================= AC-V0-3 RecordResult.warnings
class TestRecordResultWarnings:
    """`RecordResult` has no `warnings` field and no `as_dict()` method yet -
    today only `IngestResult.as_dict()` exists, and it inlines the per-record
    dict rather than delegating to the record."""

    def test_as_dict_omits_warnings_when_absent(self):
        result = RecordResult(source_ref=f"{MARKER}:clean", outcome=IngestOutcome.CREATED)

        assert "warnings" not in result.as_dict()

    def test_as_dict_includes_warnings_when_present(self):
        result = RecordResult(
            source_ref=f"{MARKER}:warned",
            outcome=IngestOutcome.CREATED,
            warnings=["customer_created"],
        )

        assert result.as_dict()["warnings"] == ["customer_created"]


# ===================================================== AC-V0-2 golden v1 payload
class TestGoldenV1Payloads:
    """Pins today's A3 shape. Expected GREEN today (see module docstring) -
    it is the regression trip-wire for S1 onward, not a red test of missing
    S0 work."""

    def test_v1_sales_order_ingest_and_read_back_are_pinned(self, env):
        line = _so_line(env, warehouse_ref=env.warehouse_ref, unit_price="12.50", uom="PCS")
        record = _so_record(
            env,
            lines=[line],
            customer_ref=env.customer_ref,
            sales_agent_ref=env.agent_ref,
            doc_date="2026-08-30",
            requested_delivery_date="2026-09-15",
            internal_note="Site A",
        )

        res = env.post(INGEST_SO, [record])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "created", res.text

        header = env.header("sales_orders", record["source_ref"])
        assert header is not None
        assert header["so_number"] == record["so_number"]
        assert header["source_system"] == "autocount"
        assert header["status"] == "open"
        assert header["internal_note"] == "Site A"
        assert str(header["order_date"]) == "2026-08-30"
        assert str(header["requested_delivery_date"]) == "2026-09-15"
        assert header["customer_id"] is not None
        assert header["sales_agent_id"] is not None

        lines = env.so_lines(header["id"])
        assert len(lines) == 1
        assert lines[0]["qty_ordered"] == 10
        assert lines[0]["unit_price"] == Decimal("12.50")
        assert lines[0]["uom"] == "PCS"
        assert lines[0]["warehouse_id"] is not None
        assert lines[0]["line_status"] == "open"

        got = env.read(READ_SO, [record["source_ref"]]).json()["records"][0]
        assert got["so_number"] == record["so_number"]
        assert got["customer_ref"] == env.customer_ref
        assert got["sales_agent_ref"] == env.agent_ref
        assert got["doc_date"] == "2026-08-30"
        assert got["requested_delivery_date"] == "2026-09-15"
        assert got["status"] == "open"
        assert got["internal_note"] == "Site A"
        got_line = got["lines"][0]
        assert got_line["product_ref"] == env.product_ref
        assert got_line["warehouse_ref"] == env.warehouse_ref
        assert got_line["qty_ordered"] == 10
        assert got_line["uom"] == "PCS"

    def test_v1_purchase_order_ingest_and_read_back_are_pinned(self, env):
        line = _po_line(env, warehouse_ref=env.warehouse_ref, qty_received=4, unit_cost="9.99")
        record = _po_record(env, lines=[line], supplier_ref=env.supplier_ref, currency="MYR")

        res = env.post(INGEST_PO, [record])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "created", res.text

        header = env.header("purchase_orders", record["source_ref"])
        assert header is not None
        assert header["po_number"] == record["po_number"]
        assert header["status"] == "active"
        assert header["currency"] == "MYR"
        assert header["supplier_id"] is not None
        assert header["source_system"] == "autocount"

        lines = env.po_lines(header["id"])
        assert len(lines) == 1
        assert lines[0]["qty_ordered"] == 4
        assert lines[0]["qty_received"] == 4
        assert lines[0]["unit_cost"] == Decimal("9.99")
        assert lines[0]["line_status"] == "closed"

        got = env.read(READ_PO, [record["source_ref"]]).json()["records"][0]
        assert got["po_number"] == record["po_number"]
        assert got["supplier_ref"] == env.supplier_ref
        assert got["currency"] == "MYR"
        assert got["status"] == "open"
        got_line = got["lines"][0]
        assert got_line["product_ref"] == env.product_ref
        assert got_line["warehouse_ref"] == env.warehouse_ref
        assert got_line["qty_ordered"] == 4
        assert got_line["qty_received"] == 4

    def test_a_v1_line_with_no_warehouse_at_all_carries_no_warnings(self, env):
        """B1 (review round 1 fix): `warehouse_unresolved` must fire only when a
        `warehouse_ref`/`warehouse_code` was actually SENT and failed to
        resolve - a v1 line that never mentions a warehouse at all is not an
        unresolved one, and the verdict must have no `warnings` key at all
        (same omit-when-empty rule `errors` follows)."""
        line = _po_line(env, unit_cost="9.99")
        record = _po_record(env, lines=[line], supplier_ref=env.supplier_ref)

        res = env.post(INGEST_PO, [record])

        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", res.text
        assert "warnings" not in entry, entry


# ==================================================== AC-V3-1 migration 472 slugs
_MIG_472_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic",
    "versions",
    "472_ingest_v2_permissions.py",
)


def _load_migration_472():
    """Loaded fresh per call, never at module import time.

    `alembic/versions/472_ingest_v2_permissions.py` does not exist yet - doing
    this at module scope (the way `test_migration_445_grant_sweep.py` safely
    does for a migration that DOES exist) would fail collection of this whole
    file with `FileNotFoundError` and hide every other test's result. Deferred
    into each test instead, so only these tests report it.
    """
    spec = importlib.util.spec_from_file_location("mig_472_ingest_v2", _MIG_472_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # raises FileNotFoundError today
    return module


class TestMigration472Slugs:
    """AC-V3-1, slugs and the sweep only (the write path is S3)."""

    @pytest.fixture()
    def bind(self):
        connection = engine.connect()
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
            connection.close()

    def _permission_id(self, bind, slug: str):
        return bind.execute(
            text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": slug}
        ).scalar()

    def _seed_role_holding(self, bind, source_slug: str) -> str:
        role_id = str(uuid.uuid4())
        suffix = uuid.uuid4().hex[:8]
        perm_id = self._permission_id(bind, source_slug)
        if perm_id is None:
            perm_id = str(uuid.uuid4())
            bind.execute(
                text(
                    "INSERT INTO user_permissions (id, slug, name, description, created_at) "
                    "VALUES (:i, :s, :n, :d, now())"
                ),
                {"i": perm_id, "s": source_slug, "n": source_slug, "d": f"{MARKER} seeded"},
            )
        bind.execute(
            text(
                "INSERT INTO user_roles (id, slug, name, description, is_protected, is_default, is_trashed) "
                "VALUES (:i, :s, :n, :d, false, false, false)"
            ),
            {
                "i": role_id,
                "s": f"{MARKER.lower()}_{suffix}",
                "n": f"{MARKER} role {suffix}",
                "d": f"{MARKER} scratch role",
            },
        )
        bind.execute(
            text(
                "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
                "VALUES (:i, :r, :p, now())"
            ),
            {"i": str(uuid.uuid4()), "r": role_id, "p": perm_id},
        )
        return role_id

    def _grant_count(self, bind, role_id: str, slug: str) -> int:
        return bind.execute(
            text(
                "SELECT count(*) FROM user_role_permissions rp "
                "JOIN user_permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :r AND p.slug = :s"
            ),
            {"r": role_id, "s": slug},
        ).scalar()

    def test_the_shipping_order_and_contract_slugs_exist_after_apply(self, bind):
        mig = _load_migration_472()
        slugs = [
            "scm.shipping_orders.view",
            "scm.shipping_orders.add",
            "scm.shipping_orders.edit",
            "scm.shipping_orders.delete",
            "integration.contract.read",
        ]
        bind.execute(
            text(
                "DELETE FROM user_role_permissions WHERE permission_id IN "
                "(SELECT id FROM user_permissions WHERE slug = ANY(:s))"
            ),
            {"s": slugs},
        )
        bind.execute(text("DELETE FROM user_permissions WHERE slug = ANY(:s)"), {"s": slugs})

        mig.apply(bind)

        for slug in slugs:
            assert self._permission_id(bind, slug) is not None, slug

    def test_a_role_holding_purchase_orders_edit_also_holds_shipping_orders_edit(self, bind):
        mig = _load_migration_472()
        role_id = self._seed_role_holding(bind, "scm.purchase_orders.edit")

        mig.apply(bind)

        assert self._grant_count(bind, role_id, "scm.shipping_orders.edit") == 1


# ======================================================= AC-V4-3 claim source
class TestClaimSourceAutocount:
    def test_source_autocount_constant_exists(self):
        from app.services.scm import order_link_service

        assert order_link_service.SOURCE_AUTOCOUNT == "autocount"


# ============================================ S9 migration 473 constraint apply
_MIG_473_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic",
    "versions",
    "473_scm_claim_autocount_source.py",
)


def _load_migration_473():
    """Loaded fresh per call - same reason `_load_migration_472` is."""
    spec = importlib.util.spec_from_file_location("mig_473_claim_source", _MIG_473_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration473WidensTheSourceCheck:
    """S9 (review round 1 fix): the migration BODY, on the real check constraint.

    A real connection inside a rolled-back transaction (`TestMigration472Slugs`'s
    own `bind` fixture pattern, not `blank_session()`'s scratch schema): a
    scratch schema is built via `create_all` off the CURRENT models, so it
    already carries the NEW (post-473) constraint text before any migration
    ever runs as DDL against it - only a real connection can be walked
    BACKWARDS to the OLD constraint text first, the way this test needs to.
    """

    @pytest.fixture()
    def bind(self):
        connection = engine.connect()
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
            connection.close()

    # `serial_ddl`: `bind` is a plain `engine.connect()` on the REAL database and the
    # two statements below DROP and ADD a constraint on the SHARED
    # `scm.order_link_claim`, which holds an AccessExclusiveLock on it until the
    # rollback. Same hazard as issue #987's `scm.committed_v` DDL, in the sharded
    # `test-backend` job rather than the SCM one, so it leaves the xdist pool too.
    @pytest.mark.serial_ddl
    def test_apply_widens_an_old_constraint_and_an_autocount_claim_then_inserts(
        self, bind
    ):
        mig = _load_migration_473()
        # Simulate pre-473: drop whatever is there now and restore the OLD
        # (narrower) text, `IF EXISTS` for the same idempotency reason the
        # migration's own `apply()` uses.
        bind.execute(
            text(
                "ALTER TABLE scm.order_link_claim DROP CONSTRAINT IF EXISTS "
                "ck_scm_order_link_claim_source"
            )
        )
        bind.execute(text(f"ALTER TABLE scm.order_link_claim ADD CONSTRAINT "
                           f"ck_scm_order_link_claim_source CHECK ({mig._OLD})"))

        mig.apply(bind)

        claim_id = str(uuid.uuid4())
        bind.execute(
            text(
                "INSERT INTO scm.order_link_claim "
                "(id, company_id, so_number, po_number, source, claimed_at) "
                "VALUES (:id, :company_id, :so_number, :po_number, 'autocount', now())"
            ),
            {
                "id": claim_id,
                "company_id": DEFAULT_COMPANY_ID,
                "so_number": f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
                "po_number": f"{MARKER}-PO-{uuid.uuid4().hex[:8]}",
            },
        )
        stored = bind.execute(
            text("SELECT source FROM scm.order_link_claim WHERE id = :id"),
            {"id": claim_id},
        ).scalar()
        assert stored == "autocount"

    def test_apply_is_idempotent_against_a_schema_already_carrying_the_new_text(
        self, bind
    ):
        """The `IF EXISTS` nit: re-running `apply()` on a schema that already
        has the widened constraint (the normal case, since `create_all`
        converges the shared dev DB ahead of any migration ever running as
        DDL) must not raise."""
        mig = _load_migration_473()

        mig.apply(bind)
        mig.apply(bind)  # must not raise on the second run
