"""RED tests for the AutoCount `brands` ingest entity (contract 2.3).

PLAN: documentation/plans/autocount/PLAN-autocount-brands-ingest.md sections 3-5.
UAC:  documentation/plans/autocount/autocount-brands-ingest-acceptance-criteria.md
      AC-1 .. AC-15.

None of this exists yet: `brands` is not in `ENTITY_SPECS`, `INGEST_PERMISSIONS`,
`READ_PERMISSIONS` or the contract's `entities` list. Every route-level test
below is therefore expected to fail on a 404 from `require_external_permission_
for_path` (the mount-time guard that 404s an entity absent from its map) - NOT
a 200 with the wrong body, and not an ImportError, because nothing here imports
a not-yet-existing `CanonicalBrand` or `EntitySpec` entry. That is the "red for
the right reason" this file pins.

AC-14 (products push still auto-creates/links a brand) is the one exception:
it exercises EXISTING 2.2 behaviour and is expected to be GREEN today, exactly
like `test_ingest_contract_v2.py`'s AC-V0-2 golden-payload test - it stays here
as the regression guard the plan calls for, not as a red test.

Substrate: `tests._pg_fixture.blank_session()`, a real Postgres scratch schema
built from the current ORM models and rolled back per test. AC-5's prod-only
`uq_brands_company_brand_name` unique index (migration 305; the model only
declares `uq_brands_company_brand_code`) is created by hand in that test's own
setup, in the scratch schema, since `create_all` does not build it.

AC-3's brand with code `" sorento "` is created BY HAND in this fixture (never
through the ingest path) precisely because it is not stripped - the whole point
of the AC is that the ADOPTED row is the pre-existing, human-entered one, warts
and all.

Every code is minted under a `ZZTBRD` marker.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.models.company import Company
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.integration_reference_service import IntegrationReferenceService

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZTBRD"

INGEST_BRANDS = "/api/v1/external/ingest/brands"
READ_BRANDS = "/api/v1/external/read/brands"
DELETE_BRANDS = "/api/v1/external/ingest/brands/deletions"
INGEST_PRODUCTS = "/api/v1/external/ingest/products"
CONTRACT_URL = "/api/v1/external/contract"

_USER_ID = "7d0dad30-1111-4222-8333-4444555577f1"
_ROLE_ID = "7d0dad30-2222-4222-8333-4444555577f2"


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
        # Anchored to A: every direct `.refs.` call this fixture makes links
        # or resolves a `brands` reference seeded into company_a - plan D14 is
        # strict, so an unanchored call on a scoped type now raises ValueError.
        self.refs = IntegrationReferenceService(db, company_id=self.company_a)

        suffix = uuid.uuid4().hex[:8]
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZBR{suffix}")
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()
        self.company_b_code = other.code

        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()

        # AC-5's name-unique index. Prod carries it (migration 305); the model
        # only declares the code one, so create_all does not build it - added
        # here, in THIS test's scratch schema only, via the search_path that
        # blank_session already pinned.
        db.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_brands_company_brand_name "
                "ON brands (company_id, brand_name)"
            )
        )
        db.commit()

    def post(self, url: str, records: list, *, company_code=None, dry_run=False):
        return self.client.post(
            f"{url}?dry_run=true" if dry_run else url,
            json={"companyCode": company_code or self.company_a_code, "records": records},
        )

    def read(self, source_refs: list, *, company_code=None):
        return self.client.post(
            READ_BRANDS,
            json={
                "companyCode": company_code or self.company_a_code,
                "source_refs": source_refs,
            },
        )

    def delete(self, source_refs: list, *, company_code=None, dry_run=False):
        return self.client.post(
            f"{DELETE_BRANDS}?dry_run=true" if dry_run else DELETE_BRANDS,
            json={
                "companyCode": company_code or self.company_a_code,
                "source_refs": source_refs,
            },
        )

    def brand(self, *, code: str, name: str, company_id: str, **extra) -> Brand:
        row = Brand(brand_code=code, brand_name=name, company_id=company_id, **extra)
        self.db.add(row)
        self.db.flush()
        return row

    def row(self, brand_id: str):
        return (
            self.db.execute(
                text("SELECT * FROM brands WHERE id = :id"), {"id": str(brand_id)}
            )
            .mappings()
            .first()
        )

    def by_code(self, code: str, company_id: str):
        return (
            self.db.execute(
                text(
                    "SELECT * FROM brands WHERE brand_code = :c AND company_id = :cid"
                ),
                {"c": code, "cid": company_id},
            )
            .mappings()
            .first()
        )

    def count_brands(self) -> int:
        return self.db.execute(text("SELECT count(*) FROM brands")).scalar()

    def count_refs(self, entity_type: str = "brands") -> int:
        return self.db.execute(
            text(
                "SELECT count(*) FROM integration_references WHERE entity_type = :t "
                "AND source_ref LIKE :p"
            ),
            {"t": entity_type, "p": f"{MARKER}:%"},
        ).scalar()


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


def _record(*, code=None, name=None, ref=None, **extra) -> dict:
    return {
        "source_ref": ref or _ref("BRAND"),
        "code": code or unique_code(MARKER)[:20],
        "name": name or f"{MARKER} Brand",
        **extra,
    }


# ================================================================== AC-1, AC-2
class TestCreateAndRepush:
    def test_a_new_brand_creates_the_row_and_links_the_reference(self, env):
        record = _record()

        res = env.post(INGEST_BRANDS, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        row = env.by_code(record["code"], env.company_a)
        assert row is not None
        assert str(row["id"]) == entry["entity_id"]
        assert str(row["company_id"]) == env.company_a
        linked = env.refs.resolve(entity_type="brands", source_ref=record["source_ref"])
        assert str(linked) == entry["entity_id"]

    def test_repush_updates_the_same_row_absent_untouched_blank_clears(self, env):
        record = _record(description="Keep me")
        first = env.post(INGEST_BRANDS, [record])
        assert first.status_code == 200, first.text
        entity_id = first.json()["records"][0]["entity_id"]

        # Omitted `description` on re-push: untouched.
        again = env.post(
            INGEST_BRANDS, [{"source_ref": record["source_ref"], "code": record["code"], "name": record["name"]}]
        )
        assert again.status_code == 200, again.text
        entry = again.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == entity_id
        row = env.row(entity_id)
        assert row["description"] == "Keep me"

        # Explicit "" clears it.
        cleared = env.post(
            INGEST_BRANDS,
            [{"source_ref": record["source_ref"], "code": record["code"], "name": record["name"], "description": ""}],
        )
        assert cleared.status_code == 200, cleared.text
        row = env.row(entity_id)
        assert row["description"] is None
        assert env.count_brands() == 1, "still one row, never a duplicate"


# ============================================================================
class TestAdoptionAC3:
    def test_a_hand_made_brand_is_adopted_by_code_never_duplicated(self, env):
        # "hand-made": inserted directly, the way an operator would type it on
        # the Brands screen - untrimmed, exactly as AC-3 spells it.
        made = env.brand(
            code=" sorento ",
            name="Sorento",
            company_id=env.company_a,
            manufacturer="Acme Corp",
            website="https://acme.example",
            logo_url="https://acme.example/logo.png",
        )
        made_id = str(made.id)

        record = _record(code="SORENTO", name="SORENTO")
        res = env.post(INGEST_BRANDS, [record])

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert entry["entity_id"] == made_id
        assert env.count_brands() == 1, "no second row"

        row = env.row(made_id)
        assert row["manufacturer"] == "Acme Corp"
        assert row["website"] == "https://acme.example"
        assert row["logo_url"] == "https://acme.example/logo.png"


# ============================================================================
class TestCompanyScopeAC4:
    def test_same_code_in_two_companies_creates_independently(self, env):
        theirs = env.brand(code="SORENTO", name="Sorento A", company_id=env.company_a)

        record = _record(code="SORENTO", name="Sorento B")
        res = env.post(INGEST_BRANDS, [record], company_code=env.company_b_code)

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        assert entry["entity_id"] != str(theirs.id)

        a_row = env.row(theirs.id)
        assert a_row["brand_name"] == "Sorento A", "company A untouched"
        b_row = env.by_code("SORENTO", env.company_b)
        assert b_row is not None
        assert b_row["brand_name"] == "Sorento B"


# ============================================================================
class TestNameClashAC5:
    def test_code_mismatch_against_an_existing_name_fails_that_record_only(self, env):
        env.brand(code="SRT", name="SORENTO", company_id=env.company_a)

        bad = _record(code="SORENTO", name="SORENTO")
        good = _record()
        res = env.post(INGEST_BRANDS, [bad, good])

        assert res.status_code == 200, res.text
        by_ref = {r["source_ref"]: r for r in res.json()["records"]}
        assert by_ref[bad["source_ref"]]["outcome"] == "failed", by_ref[bad["source_ref"]]
        assert by_ref[good["source_ref"]]["outcome"] == "created", by_ref[good["source_ref"]]
        # The failed record created nothing under its own code - the only row
        # is still the pre-existing SRT one, never a second "SORENTO" row.
        assert env.by_code("SORENTO", env.company_a) is None


# ============================================================================
class TestFieldValidationAC6:
    def test_oversize_code_name_and_unknown_key_each_fail_their_own_record(self, env):
        too_long_code = _record(code="C" * 51)
        too_long_name = _record(name="N" * 151)
        unknown_key = _record(
            name=f"{MARKER} Brand Unknown Key", logo_url="https://not-allowed.example/logo.png"
        )
        good = _record()

        res = env.post(INGEST_BRANDS, [too_long_code, too_long_name, unknown_key, good])

        assert res.status_code == 200, res.text
        by_ref = {r["source_ref"]: r for r in res.json()["records"]}
        assert by_ref[too_long_code["source_ref"]]["outcome"] == "failed"
        assert "code" in by_ref[too_long_code["source_ref"]].get("errors", {})
        assert by_ref[too_long_name["source_ref"]]["outcome"] == "failed"
        assert "name" in by_ref[too_long_name["source_ref"]].get("errors", {})
        # Owner decision, 1 Oct 2026: an unknown key is dropped, never refused.
        assert by_ref[unknown_key["source_ref"]]["outcome"] == "created"
        assert by_ref[good["source_ref"]]["outcome"] == "created"


# ============================================================================
class TestBatchCapAC7:
    def test_over_1000_records_is_413(self, env):
        records = [_record() for _ in range(1001)]
        res = env.post(INGEST_BRANDS, records)
        assert res.status_code == 413, res.text
        assert res.json()["code"] == "BATCH_TOO_LARGE"


# ============================================================================
class TestDryRunAC8:
    def test_dry_run_writes_nothing(self, env):
        record = _record()
        res = env.post(INGEST_BRANDS, [record], dry_run=True)

        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "created"
        assert env.by_code(record["code"], env.company_a) is None
        assert env.count_refs() == 0


# ================================================================ AC-9, permission
class TestPermissionGuardAC9:
    """The real RBAC guard, on an empty schema of its own - mirrors
    `test_ingest_documents.py::TestPermissionGuard`."""

    @pytest.fixture()
    def guard_db(self):
        from app.models.integration import Integration, IntegrationApiKey
        from app.models.user import (
            User,
            UserPermission,
            UserRole,
            UserRoleAssignment,
            UserRolePermission,
        )
        from tests._pg_fixture import pg_empty_schema

        with pg_empty_schema(
            [
                User.__table__,
                UserRole.__table__,
                UserRoleAssignment.__table__,
                UserPermission.__table__,
                UserRolePermission.__table__,
                Integration.__table__,
                IntegrationApiKey.__table__,
            ]
        ) as session:
            yield session

    @pytest.fixture()
    def guard_client(self, guard_db):
        from app.dependencies import get_db as app_get_db
        from app.api.v1.external import ingest as ingest_module
        from app.api.v1.external.permissions import require_external_permission_for_path

        api = FastAPI()

        @api.post("/ingest/{entity}")
        def _ingest_stub(
            entity: str,
            _: dict = Depends(
                require_external_permission_for_path(ingest_module.INGEST_PERMISSIONS)
            ),
        ):
            return {"ok": entity}

        def _override_db():
            yield guard_db

        api.dependency_overrides[app_get_db] = _override_db
        return TestClient(api, raise_server_exceptions=False)

    @pytest.fixture()
    def keys(self, guard_db):
        from app.models.integration import Integration
        from app.models.user import (
            User,
            UserPermission,
            UserRole,
            UserRoleAssignment,
            UserRolePermission,
        )
        from app.services.integration_key_service import IntegrationKeyService

        edit_slug = "master_data.brands.edit"
        view_slug = "master_data.brands.view"
        perms = {}
        for slug in (edit_slug, view_slug):
            perm = UserPermission(slug=slug, name=slug)
            guard_db.add(perm)
            guard_db.flush()
            perms[slug] = perm

        issued = {}
        for label, held in (("editor", [edit_slug]), ("viewer", [view_slug])):
            user = User(
                email=f"{MARKER.lower()}-{label}@integrations.local",
                name=f"Integration: {label}",
                status="ACTIVE",
                is_integration=True,
            )
            guard_db.add(user)
            guard_db.flush()
            role = UserRole(slug=f"{MARKER.lower()}_{label}", name=f"{MARKER} {label}")
            guard_db.add(role)
            guard_db.flush()
            guard_db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
            for slug in held:
                guard_db.add(UserRolePermission(role_id=role.id, permission_id=perms[slug].id))
            guard_db.flush()
            integration = Integration(
                name=f"{MARKER}-{label}",
                type="autocount_esb",
                act_as_user_id=user.id,
                is_active=True,
            )
            guard_db.add(integration)
            guard_db.flush()
            issued[label] = IntegrationKeyService(guard_db).issue_key(integration)
        return issued

    def test_a_key_without_the_edit_slug_is_403_naming_it(self, guard_client, keys):
        res = guard_client.post("/ingest/brands", headers={"X-API-Key": keys["viewer"]})
        assert res.status_code == 403, res.text
        assert "master_data.brands.edit" in res.text

    def test_the_edit_slug_passes(self, guard_client, keys):
        res = guard_client.post("/ingest/brands", headers={"X-API-Key": keys["editor"]})
        assert res.status_code == 200, res.text


# ==================================================================== AC-11
class TestReadBackAC11:
    def test_read_returns_the_canonical_shape(self, env):
        record = _record(description="A description", is_active=True)
        push = env.post(INGEST_BRANDS, [record])
        assert push.status_code == 200, push.text

        res = env.read([record["source_ref"]])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == []
        got = body["records"][0]
        assert got["code"] == record["code"]
        assert got["name"] == record["name"]
        assert got["description"] == "A description"
        assert got["is_active"] is True


# ==================================================================== AC-12
class TestDeletionsAC12:
    def _linked_brand(self, env, **extra):
        row = env.brand(
            code=unique_code(MARKER)[:20], name=f"{MARKER} del", company_id=env.company_a, **extra
        )
        source_ref = _ref("DEL")
        env.refs.link(entity_type="brands", entity_id=str(row.id), source_ref=source_ref)
        env.db.commit()
        return row, source_ref

    def test_a_brand_no_row_references_is_deleted(self, env):
        row, source_ref = self._linked_brand(env)

        res = env.delete([source_ref])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deleted", entry
        assert env.row(row.id) is None
        assert env.refs.resolve(entity_type="brands", source_ref=source_ref) is None

    def test_a_brand_a_product_references_is_deactivated_not_deleted(self, env):
        row, source_ref = self._linked_brand(env)
        product = Product(
            product_code=unique_code(MARKER),
            product_name=f"{MARKER} product",
            category_id=env._category.id,
            base_uom_id=env._uom.id,
            list_price=10,
            brand_id=row.id,
            company_id=env.company_a,
        )
        env.db.add(product)
        env.db.commit()

        res = env.delete([source_ref])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deactivated", entry

        brand_row = env.row(row.id)
        assert brand_row is not None
        assert brand_row["is_active"] is False
        assert env.refs.resolve(entity_type="brands", source_ref=source_ref) == str(row.id)
        product_row = env.db.execute(
            text("SELECT brand_id FROM products WHERE id = :id"), {"id": str(product.id)}
        ).mappings().first()
        assert str(product_row["brand_id"]) == str(row.id)

    def test_a_brand_referenced_only_by_a_project_brand_row_is_deactivated(self, env):
        from app.models.projects import Project, ProjectBrand

        row, source_ref = self._linked_brand(env)
        project = Project(
            id=str(uuid.uuid4()),
            company_id=env.company_a,
            project_code=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            title=f"{MARKER} project",
            normalised_title=f"{MARKER.lower()} project",
            owner_user_id=_USER_ID,
        )
        env.db.add(project)
        env.db.flush()
        link = ProjectBrand(project_id=project.id, brand_id=row.id)
        env.db.add(link)
        env.db.commit()

        res = env.delete([source_ref])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "deactivated", entry

        brand_row = env.row(row.id)
        assert brand_row is not None
        assert brand_row["is_active"] is False
        # ORM, not schema-qualified raw SQL: `projects.brands` in a raw text()
        # string resolves against the REAL projects schema, not blank_session's
        # translated scratch one, so a bare-string query would silently read
        # (and find nothing in) production. The ORM class maps onto whatever
        # schema the current session's translate map points ProjectBrand at.
        still_there = (
            env.db.query(ProjectBrand)
            .filter_by(project_id=project.id, brand_id=row.id)
            .first()
        )
        assert still_there is not None

    def test_an_unknown_ref_is_not_found(self, env):
        res = env.delete([_ref("GONE")])
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "not_found"

    def test_dry_run_writes_nothing(self, env):
        row, source_ref = self._linked_brand(env)
        res = env.delete([source_ref], dry_run=True)
        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "deleted"
        assert env.row(row.id) is not None, "dry run must not actually delete"


# ==================================================================== AC-13
class TestContractAC13:
    def test_version_2_3_lists_brands(self, env):
        # Bumped again (ingest-products-code-wins, SR0): "2.4" adds products
        # code-wins deletion `codes`. Bumped again (ingest-stock-balances-2-5,
        # Foundryx SR5): "2.5" adds `stock_balances` - this test still proves
        # `brands` is listed among the entities, which "2.3" first introduced.
        res = env.client.get(CONTRACT_URL)
        assert res.status_code == 200, res.text
        body = res.json()
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        assert body["version"] == "2.7"
        assert "brands" in body["entities"]


# ==================================================================== AC-14
class TestProductsUnchangedAC14:
    """Existing 2.2 behaviour - expected GREEN today, guarded against
    regression once `brands` becomes a first-class EntitySpec."""

    def test_a_new_brand_code_on_a_product_push_still_auto_creates_with_a_warning(self, env):
        code = unique_code(MARKER)
        record = {
            "source_ref": _ref("ITEM"),
            "code": unique_code(MARKER),
            "name": f"{MARKER} product",
            "category_code": env._category.category_code,
            "uom_code": env._uom.uom_code,
            "brand_code": code,
        }
        res = env.post(INGEST_PRODUCTS, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        assert "brand_created" in entry.get("warnings", [])
        assert env.by_code(code, env.company_a) is not None

    def test_an_existing_brand_code_on_a_product_push_links_it_no_warning(self, env):
        existing = env.brand(code=unique_code(MARKER), name=f"{MARKER} existing", company_id=env.company_a)
        record = {
            "source_ref": _ref("ITEM2"),
            "code": unique_code(MARKER),
            "name": f"{MARKER} product 2",
            "category_code": env._category.category_code,
            "uom_code": env._uom.uom_code,
            "brand_code": existing.brand_code,
        }
        res = env.post(INGEST_PRODUCTS, [record])
        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "created", entry
        assert "brand_created" not in entry.get("warnings", [])
        product_row = env.db.execute(
            text("SELECT brand_id FROM products WHERE product_code = :c"),
            {"c": record["code"]},
        ).mappings().first()
        assert str(product_row["brand_id"]) == str(existing.id)


# ==================================================================== AC-15
class TestAllowlistAC15:
    def test_supported_entity_types_includes_brands(self):
        from app.services.integration_reference_service import SUPPORTED_ENTITY_TYPES

        assert "brands" in SUPPORTED_ENTITY_TYPES

    def test_permission_maps_cover_brands(self):
        from app.api.v1.external.ingest import (
            DELETE_PERMISSIONS,
            INGEST_PERMISSIONS,
            READ_PERMISSIONS,
        )

        assert INGEST_PERMISSIONS.get("brands") == "master_data.brands.edit"
        assert READ_PERMISSIONS.get("brands") == "master_data.brands.view"
        assert DELETE_PERMISSIONS.get("brands") == "master_data.brands.delete"
