"""An /external match-by-code resolves inside ONE company, not by row order.

The defect this pins: an ``X-API-Key`` call that carries no ``contact_id`` /
``space_id`` resolves to scope ``None`` = ALL companies
(``company_scope_resolver._resolve_api_key_scope``). ``product_code`` is unique
PER COMPANY only (``uq_products_company_product_code``, migration 305), and on
the live database 11k+ codes exist in both companies - so ``get_products_by_code``
built its ``{code: product}`` dict from an unordered ``.all()`` and kept whichever
row the scan returned last. GRN and SPO-allocation posts therefore bound goods to
an arbitrary company's product.

The fix is not "return all of them": the two candidates ARE the same product in
different companies and only one is right. The payload has to name its company
through something globally unique - a warehouse code, a container number, an SPO -
and ``pin_scope_to_companies`` pins the request to it before any code is matched.

The AutoCount ingest surface (``/external/ingest/{entity}``,
``/external/read/{entity}``) has the same problem and cannot use the same fix:
its payload names no warehouse and no container, only a business code, so there
is nothing in it to infer a company from. It therefore states the company
outright - a top-level ``companyCode``, or the calling integration's binding -
and ``resolve_company_anchor`` turns that into the one company the whole request
writes, adopts and reads inside. Group A1 of the AutoCount cross-repo contract;
the tests are the second half of this file.

Substrate: Postgres only, on the shared blank schema whose writes are discarded
(group T). Every test seeds its own chain under a ZZTANCH marker; nothing is
borrowed and nothing asserts about a production row.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.api.v1.external.utils import get_products_by_code, pin_scope_to_companies
from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code

MARKER = "ZZTANCH"
GRN = "/api/v1/external/grn/"

_USER_ID = "5b8b9c10-1111-4222-8333-444455556661"
_ROLE_ID = "5b8b9c10-2222-4222-8333-444455556662"


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
        suffix = uuid.uuid4().hex[:8]
        # The incumbent is auto-seeded by conftest; the second company is ours.
        self.company_a = DEFAULT_COMPANY_ID
        other = Company(
            id=str(uuid.uuid4()), name=f"{MARKER} B {suffix}", code=f"ZB{suffix}"
        )
        db.add(other)
        db.flush()
        self.company_b = str(other.id)
        self.company_b_code = other.code
        # Read rather than hardcoded: the incumbent's code is seeded by conftest's
        # after_create hook here and by migration 302 in production, and a test that
        # spells "SRT" out would pin a value neither of those promises.
        self.company_a_code = db.execute(
            text("SELECT code FROM companies WHERE id = :id"), {"id": self.company_a}
        ).scalar()
        self._category = ProductCategory(
            category_code=unique_code(MARKER), category_name=f"{MARKER} category"
        )
        self._uom = UnitOfMeasure(uom_code=unique_code(MARKER), uom_name=f"{MARKER} unit")
        db.add_all([self._category, self._uom])
        db.flush()

    def product(self, code: str, company_id: str) -> Product:
        row = Product(
            product_code=code,
            product_name=f"{MARKER} {code}",
            category_id=self._category.id,
            base_uom_id=self._uom.id,
            list_price=10,
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def warehouse(self, code: str, company_id: str) -> Warehouse:
        row = Warehouse(
            warehouse_code=code,
            warehouse_name=f"{MARKER} {code}",
            company_id=company_id,
        )
        self.db.add(row)
        self.db.flush()
        return row


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

        # THE POINT OF THIS SUITE: reproduce the real n8n principal, which sends
        # X-API-Key with no contact identity and therefore arrives scoped to ALL
        # companies. Pinning a company here would hide the very defect under test.
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


# ------------------------------------------------------ the helper's own rules
def test_one_anchor_company_pins_the_request(env):
    resolved = pin_scope_to_companies(env.db, [env.company_b], anchor="test")
    assert resolved == env.company_b

    code = f"{MARKER}-PIN-{uuid.uuid4().hex[:6]}"
    env.product(code, env.company_a)
    wanted = env.product(code, env.company_b)

    # Same code in both companies; the pinned scope decides which one resolves.
    found = get_products_by_code(env.db, [code])
    assert {str(p.id) for p in found.values()} == {str(wanted.id)}


def test_no_anchor_falls_back_to_the_incumbent_company(env):
    """Deterministic beats arbitrary. Every pre-multi-company row carries the
    incumbent id, so that is what these integrations were effectively resolving
    to already - the same rule `_portal_token_scope` uses."""
    assert pin_scope_to_companies(env.db, [], anchor="test") == DEFAULT_COMPANY_ID

    code = f"{MARKER}-FALL-{uuid.uuid4().hex[:6]}"
    incumbent = env.product(code, env.company_a)
    env.product(code, env.company_b)

    found = get_products_by_code(env.db, [code])
    assert {str(p.id) for p in found.values()} == {str(incumbent.id)}


def test_anchors_spanning_two_companies_are_a_400_not_a_guess(env):
    with pytest.raises(HTTPException) as excinfo:
        pin_scope_to_companies(
            env.db, [env.company_a, env.company_b], anchor="This GRN's warehouses"
        )
    assert excinfo.value.status_code == 400
    assert "more than one company" in excinfo.value.detail["message"]


# --------------------------------------------------------------- the GRN route
def test_grn_binds_the_product_of_the_company_its_warehouse_belongs_to(env):
    """The regression. Two companies hold the same product code; the GRN names a
    warehouse in the second. Before the fix the line could bind either one."""
    code = f"{MARKER}-GRN-{uuid.uuid4().hex[:6]}"
    # The WRONG company's row is inserted LAST on purpose. Unscoped,
    # `get_products_by_code` builds `{code: product}` from an unordered `.all()`,
    # so the last row scanned wins the key - on a freshly written table that is
    # this one. Seeded the other way round the old code passes by luck and the
    # test guards nothing.
    wanted = env.product(code, env.company_b)
    env.product(code, env.company_a)
    warehouse = env.warehouse(f"{MARKER}WH{uuid.uuid4().hex[:6]}", env.company_b)
    env.db.commit()

    from app.models.procurement import PickingHeader, PickingLine

    picking_number = f"{MARKER}-{uuid.uuid4().hex[:6]}"
    response = env.client.post(
        GRN,
        json={
            "goods_receive_notes": {
                "picking_number": picking_number,
                "picking_date": "2026-03-10",
            },
            "grn_lines": [
                {"product_code": code, "quantity": 2, "location": warehouse.warehouse_code}
            ],
        },
    )
    assert response.status_code == 201, response.text

    header = (
        env.db.query(PickingHeader)
        .filter(PickingHeader.picking_number == picking_number)
        .one()
    )
    line = env.db.query(PickingLine).filter(PickingLine.picking_header_id == header.id).one()
    assert str(line.product_id) == str(wanted.id)


# ============================================================ the ingest surface
# Group A1 of the AutoCount cross-repo contract. Same disease as above, different
# medicine: an ingest payload carries no warehouse and no container to infer a
# company from, so it states one - and every write, adoption and read in the
# request happens inside it.
INGEST_WAREHOUSES = "/api/v1/external/ingest/warehouses"
READ_WAREHOUSES = "/api/v1/external/read/warehouses"
INGEST_PRODUCTS = "/api/v1/external/ingest/products"


def _wh_record(code: str | None = None, name: str = "Depot", ref: str | None = None) -> dict:
    code = code or f"{MARKER}WH{uuid.uuid4().hex[:6]}"
    return {
        "source_ref": ref or f"{MARKER}-REF-{uuid.uuid4().hex[:8]}",
        "code": code,
        "name": name,
    }


def _warehouse_row(env, code: str):
    """Raw SQL on purpose: the ORM filter would hide the row of whichever company
    the request just anchored to, and which company the row landed in is exactly
    what these tests are asking."""
    return (
        env.db.execute(
            text(
                "SELECT id, warehouse_name, company_id FROM warehouses "
                "WHERE warehouse_code = :c"
            ),
            {"c": code},
        )
        .mappings()
        .first()
    )


def _link_in_company_b(env, code: str):
    """A warehouse owned by company B and claimed by an AutoCount source_ref."""
    from app.services.integration_reference_service import IntegrationReferenceService

    theirs = env.warehouse(code, env.company_b)
    source_ref = f"{MARKER}-XREF-{uuid.uuid4().hex[:8]}"
    # Anchored to B: plan D14 is strict, so the constructor's own anchor is
    # what gets stored, never inferred from the row being linked.
    IntegrationReferenceService(env.db, company_id=env.company_b).link(
        entity_type="warehouses", entity_id=str(theirs.id), source_ref=source_ref
    )
    return theirs, source_ref


def test_ingest_without_a_company_code_or_a_binding_is_refused(env):
    """AC-A1-1. Guessing is not available: the six masters are company-partitioned,
    so a push with no anchor has no correct destination, only a likely one."""
    record = _wh_record()

    res = env.client.post(INGEST_WAREHOUSES, json={"records": [record]})

    assert res.status_code == 422, res.text
    assert res.json()["code"] == "COMPANY_ANCHOR_REQUIRED"
    assert _warehouse_row(env, record["code"]) is None


def test_an_unknown_or_inactive_company_code_is_refused(env):
    """AC-A1-2. An inactive company is refused with the same code as an unknown
    one: for a caller that may not write there, the distinction is academic."""
    record = _wh_record()

    res = env.client.post(
        INGEST_WAREHOUSES, json={"companyCode": f"{MARKER}-NOPE", "records": [record]}
    )
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "UNKNOWN_COMPANY"

    dormant = Company(
        id=str(uuid.uuid4()),
        name=f"{MARKER} dormant",
        code=f"ZX{uuid.uuid4().hex[:8]}",
        is_active=False,
    )
    env.db.add(dormant)
    env.db.flush()

    res = env.client.post(
        INGEST_WAREHOUSES, json={"companyCode": dormant.code, "records": [record]}
    )
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "UNKNOWN_COMPANY"
    assert _warehouse_row(env, record["code"]) is None


def test_the_company_code_matches_code_or_autocount_ref_case_insensitively(env):
    """AC-A1-3. The ESB knows companies by their AutoCount name, an operator knows
    them by the Sorento code, and neither controls the other's casing."""
    env.db.execute(
        text("UPDATE companies SET autocount_ref = :r WHERE id = :id"),
        {"r": f"AC-{env.company_b_code}", "id": env.company_b},
    )

    by_code = _wh_record()
    res = env.client.post(
        INGEST_WAREHOUSES,
        json={"companyCode": env.company_b_code.lower(), "records": [by_code]},
    )
    assert res.status_code == 200, res.text
    assert str(_warehouse_row(env, by_code["code"])["company_id"]) == env.company_b

    by_ref = _wh_record()
    res = env.client.post(
        INGEST_WAREHOUSES,
        json={"companyCode": f"ac-{env.company_b_code}".lower(), "records": [by_ref]},
    )
    assert res.status_code == 200, res.text
    assert str(_warehouse_row(env, by_ref["code"])["company_id"]) == env.company_b


def test_the_integration_binding_anchors_and_a_disagreeing_body_code_does_not(env):
    """AC-A1-4. A dedicated integration per AutoCount company needs no body field;
    one that then names a DIFFERENT company is refused rather than ranked, because
    either half could be the mistake and only the caller knows which."""
    from app.dependencies import get_external_api_user
    from app.models.integration import Integration

    integration = Integration(
        id=str(uuid.uuid4()),
        name=f"{MARKER} esb {uuid.uuid4().hex[:6]}",
        type="esb",
        config_json={"company_code": env.company_b_code},
    )
    env.db.add(integration)
    env.db.flush()

    app.dependency_overrides[get_external_api_user] = lambda: {
        "id": _USER_ID,
        "email": f"{MARKER.lower()}-admin@test.com",
        "integration_id": str(integration.id),
    }

    bound = _wh_record()
    res = env.client.post(INGEST_WAREHOUSES, json={"records": [bound]})
    assert res.status_code == 200, res.text
    assert str(_warehouse_row(env, bound["code"])["company_id"]) == env.company_b

    clash = _wh_record()
    res = env.client.post(
        INGEST_WAREHOUSES, json={"companyCode": env.company_a_code, "records": [clash]}
    )
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "COMPANY_ANCHOR_AMBIGUOUS"
    assert _warehouse_row(env, clash["code"]) is None


def test_a_company_code_naming_two_companies_is_refused(env):
    """AC-A1-3, the half `LIMIT 1` used to decide by scan order.

    `companies.code` is unique, but `autocount_ref` carries no unique index, so
    two companies CAN be given the same AutoCount reference by hand. Answering
    with whichever row came back first would file an entire sync under an
    arbitrary company, and nothing would report it.
    """
    shared = f"AC-{MARKER}-{uuid.uuid4().hex[:6]}"
    env.db.execute(
        text("UPDATE companies SET autocount_ref = :r WHERE id IN (:a, :b)"),
        {"r": shared, "a": env.company_a, "b": env.company_b},
    )
    record = _wh_record()

    res = env.client.post(
        INGEST_WAREHOUSES, json={"companyCode": shared, "records": [record]}
    )

    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "COMPANY_ANCHOR_AMBIGUOUS"
    assert "2 active companies" in body["message"]
    assert _warehouse_row(env, record["code"]) is None


def test_a_binding_naming_no_live_company_blames_the_binding_not_the_caller(env):
    """AC-A1-2. A stale binding is a Sorento configuration row pointing at a
    company that has been renamed or deactivated. Reporting it as
    `UNKNOWN_COMPANY` named a value the caller never sent - and did it in a
    request whose own `companyCode` was perfectly good, so the ESB would have
    looked for the fault in the one field it had got right."""
    from app.dependencies import get_external_api_user
    from app.models.integration import Integration

    integration = Integration(
        id=str(uuid.uuid4()),
        name=f"{MARKER} stale {uuid.uuid4().hex[:6]}",
        type="esb",
        config_json={"company_code": f"{MARKER}-GONE"},
    )
    env.db.add(integration)
    env.db.flush()
    app.dependency_overrides[get_external_api_user] = lambda: {
        "id": _USER_ID,
        "email": f"{MARKER.lower()}-admin@test.com",
        "integration_id": str(integration.id),
    }
    record = _wh_record()

    res = env.client.post(
        INGEST_WAREHOUSES,
        json={"companyCode": env.company_a_code, "records": [record]},
    )

    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "COMPANY_BINDING_INVALID"
    assert f"{MARKER}-GONE" in body["message"]
    assert "config_json.company_code" in body["message"]
    assert _warehouse_row(env, record["code"]) is None


def test_a_created_row_carries_the_anchor_company(env):
    """AC-A1-5, the NULL-company regression. ``_apply`` raw-INSERTs, so it bypasses
    the ORM auto-stamp entirely: before the anchor it wrote no ``company_id`` at
    all, which production rejects outright (NOT NULL, migration 305) and a
    create_all test schema silently accepts (the column is ORM-nullable). Asserted
    on the value, never on the constraint."""
    record = _wh_record()

    res = env.client.post(
        INGEST_WAREHOUSES, json={"companyCode": env.company_b_code, "records": [record]}
    )

    assert res.status_code == 200, res.text
    assert res.json()["summary"]["created"] == 1
    assert str(_warehouse_row(env, record["code"])["company_id"]) == env.company_b


def test_adoption_does_not_reach_into_another_company(env):
    """AC-A1-6. Adoption by business code is what stops a first sync duplicating a
    hand-entered row - and unscoped it would hand company A's push to company B's
    record instead, silently retargeting a row nobody asked it to touch.

    ``products`` rather than ``warehouses``: this AC needs the same code in two
    companies, and ``Warehouse.warehouse_code`` still carries the pre-305 GLOBAL
    ``unique=True`` in the model, so a schema built by ``create_all`` cannot hold
    one. ``Product`` was brought into step with migration 305's composite
    (``uq_products_company_product_code``) and can."""
    code = f"{MARKER}-ADOPT-{uuid.uuid4().hex[:6]}"
    theirs = env.product(code, env.company_b)

    res = env.client.post(
        INGEST_PRODUCTS,
        json={
            "companyCode": env.company_a_code,
            "records": [
                {
                    "source_ref": f"{MARKER}-REF-{uuid.uuid4().hex[:8]}",
                    "code": code,
                    "name": f"{MARKER} pushed",
                    "category_code": env._category.category_code,
                    "uom_code": env._uom.uom_code,
                }
            ],
        },
    )

    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] == "created"

    rows = (
        env.db.execute(
            text("SELECT id, product_name, company_id FROM products WHERE product_code = :c"),
            {"c": code},
        )
        .mappings()
        .all()
    )
    by_company = {str(row["company_id"]): row for row in rows}
    assert set(by_company) == {env.company_a, env.company_b}
    assert str(by_company[env.company_b]["id"]) == str(theirs.id)
    assert by_company[env.company_b]["product_name"] == f"{MARKER} {code}"


def test_a_source_ref_linked_in_another_company_creates_a_new_row_in_this_one(env):
    """AC-19 (autocount-brands-ingest BL-056, D15) - replaces AC-A1-7.

    BL-056's behaviour flip: the reference table now carries a `company_id`
    anchor (`integration_references.company_id`), so `resolve()` scoped to
    company A genuinely finds nothing for a ref linked only under B - the same
    answer as a ref that was never linked at all. A push under A therefore
    proceeds through the ordinary adoption/create path and lands a NEW row in
    A; B's row and its own reference are untouched. `_require_same_company` is
    unreachable through refs now and is gone.

    ``products`` rather than ``warehouses`` (as before): this needs the same
    code resolvable in two companies for the follow-up read check, and
    ``Warehouse.warehouse_code`` still carries the pre-305 GLOBAL
    ``unique=True`` in the model (see the note on `test_adoption_does_not_
    reach_into_another_company` above).
    """
    from app.services.integration_reference_service import IntegrationReferenceService

    code = f"{MARKER}-A19-{uuid.uuid4().hex[:6]}"
    theirs = env.product(code, env.company_b)
    source_ref = f"{MARKER}-XREF-{uuid.uuid4().hex[:8]}"
    # Anchored to B: plan D14 is strict, so the constructor's own anchor is
    # what gets stored, never inferred from the row being linked.
    IntegrationReferenceService(env.db, company_id=env.company_b).link(
        entity_type="products", entity_id=str(theirs.id), source_ref=source_ref
    )

    # Before the push: under A, this ref resolves to nothing - the same
    # answer as one that was never linked at all.
    before_read = env.client.post(
        "/api/v1/external/read/products",
        json={"companyCode": env.company_a_code, "source_refs": [source_ref]},
    )
    assert before_read.status_code == 200, before_read.text
    assert source_ref in before_read.json()["not_found"], before_read.text

    res = env.client.post(
        INGEST_PRODUCTS,
        json={
            "companyCode": env.company_a_code,
            "records": [
                {
                    "source_ref": source_ref,
                    "code": code,
                    "name": f"{MARKER} new in A",
                    "category_code": env._category.category_code,
                    "uom_code": env._uom.uom_code,
                }
            ],
        },
    )

    assert res.status_code == 200, res.text
    entry = res.json()["records"][0]
    assert entry["outcome"] == "created", entry

    rows = (
        env.db.execute(
            text("SELECT id, product_name, company_id FROM products WHERE product_code = :c"),
            {"c": code},
        )
        .mappings()
        .all()
    )
    by_company = {str(row["company_id"]): row for row in rows}
    assert set(by_company) == {env.company_a, env.company_b}, (
        "a new row must exist in A, and B's must be untouched"
    )
    assert str(by_company[env.company_b]["id"]) == str(theirs.id)
    assert by_company[env.company_b]["product_name"] == f"{MARKER} {code}", "B's row untouched"
    new_id = str(by_company[env.company_a]["id"])
    assert new_id != str(theirs.id)

    # After the push: products carry no reference written by ingest (owner
    # ruling 2, 3 Oct), so the ref still resolves to nothing under A - it did
    # not start resolving to B's row either.
    after_read = env.client.post(
        "/api/v1/external/read/products",
        json={"companyCode": env.company_a_code, "source_refs": [source_ref]},
    )
    assert after_read.status_code == 200, after_read.text
    assert source_ref in after_read.json()["not_found"], after_read.text
    b_refs = env.db.execute(
        text(
            "SELECT count(*) FROM integration_references "
            "WHERE entity_type = 'products' AND entity_id = :i AND source_ref = :r"
        ),
        {"i": str(theirs.id), "r": source_ref},
    ).scalar()
    assert b_refs == 1, "B's own reference is untouched"
    assert new_id != str(theirs.id)


def test_read_reports_another_companys_row_as_not_found(env):
    """AC-A1-8. Reported explicitly, not omitted: the ESB acts on ``not_found``,
    and a row it is not entitled to see must read the same as one that does not
    exist. The control at the end is what proves the ref itself is fine."""
    code = f"{MARKER}WH{uuid.uuid4().hex[:6]}"
    _theirs, source_ref = _link_in_company_b(env, code)

    res = env.client.post(
        READ_WAREHOUSES,
        json={"companyCode": env.company_a_code, "source_refs": [source_ref]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["not_found"] == [source_ref]
    assert res.json()["records"] == []

    res = env.client.post(
        READ_WAREHOUSES,
        json={"companyCode": env.company_b_code, "source_refs": [source_ref]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["code"] == code
    assert res.json()["not_found"] == []
