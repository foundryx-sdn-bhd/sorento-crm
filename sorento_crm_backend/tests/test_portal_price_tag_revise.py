"""Red tests for PLAN-portal-price-tag-journey-r8 Round 3, R3-2 and AC-R2..R6.

R3-1 registers ``price_tag_request`` as a fourth ``RevisionAdapter`` in
``app/services/portal_revision_service.py`` (model ``PriceTagRequest``,
``number_attr="doc_number"``, an ``apply_lines`` callable running
``PriceTagRequestService.replace_lines`` + the submit validators). Today
``ADAPTERS`` holds only ``stock_inquiry`` / ``purchase_request`` /
``sponsorship_form`` (grep ``ADAPTERS: dict[str, RevisionAdapter]``), so
``get_adapter("price_tag_request")`` is ``None`` and every entry point below
(``PortalRevisionService.revise`` / ``policy_for`` / the generic portal.py
routes / the settings API) refuses BEFORE reaching any of this file's
assertions - the missing adapter registration, not a fixture bug.

Modelled on ``tests/test_portal_revise_flow.py`` (direct-service harness
style) and ``tests/test_portal_revision_config_routes.py`` (settings HTTP).
Every test seeds its own contact/request chain - Postgres via
``tests/_pg_fixture.py::blank_session``, never a borrowed row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.portal import PortalRevisionConfig, PortalToken
from app.models.user import SystemSetting
from app.services.portal_revision_service import PortalRevisionService
from app.services.price_tag_request_service import PriceTagRequestService
from tests._pg_fixture import blank_session, unique_code
from tests import _ptag_r9_seed

_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
_PORTAL_BASE = "/api/v1/public/portal"
_PTAG_BASE = "/api/v1/public/portal/submissions/price_tag_request"


# --------------------------------------------------------------------------- #
# Seeding (price_tag_request has no entry in tests/_revision_harness.py's
# seed_entity - stock_inquiry/purchase_request/sponsorship_form only)
# --------------------------------------------------------------------------- #


def _seed_system_settings(db, *, enabled: bool = True, cap: int = 2) -> SystemSetting:
    row = SystemSetting(id=str(uuid.uuid4()), portal_revisions_enabled=enabled, portal_max_revisions=cap)
    db.add(row)
    db.commit()
    return row


def _seed_ptag_config(
    db, *, is_enabled: bool = True, max_revisions=None, allowed_statuses=None,
) -> PortalRevisionConfig:
    row = PortalRevisionConfig(
        id=str(uuid.uuid4()),
        source_entity_type="price_tag_request",
        is_enabled=is_enabled,
        max_revisions=max_revisions,
        allowed_statuses=(
            ["new", "changes_requested"] if allowed_statuses is None else allowed_statuses
        ),
        restart_stage_code=None,
    )
    db.add(row)
    db.commit()
    return row


def _seed_contact(db):
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("ZZT Contact"),
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    link_contact_company(db, contact.id)
    db.commit()
    return contact


def _seed_token(contact) -> PortalToken:
    """A token object, not persisted, for direct-service calls (matches
    ``_revision_harness.seed_token``)."""
    return PortalToken(
        token=unique_code("tok"),
        contact_id=contact.id,
        space_id="zzt-space",
        expires_at=datetime(2099, 1, 1),
        verified_at=datetime(2026, 1, 1),
    )


def _persisted_token(db, contact) -> str:
    row = PortalToken(
        id=str(uuid.uuid4()),
        token=f"tok-{uuid.uuid4().hex}",
        contact_id=contact.id,
        space_id="zzt-space",
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row)
    db.commit()
    return row.token


def _seed_product(db, *, class_label: str = "Kitchen Sink") -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
        class_label=class_label,
    )
    brand = Brand(id=str(uuid.uuid4()), brand_code=unique_code("br"), brand_name=unique_code("Brand"))
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("uom"), uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()),
        product_code=unique_code("prod"),
        product_name=unique_code("Product"),
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=100.00,
    )
    db.add(product)
    db.flush()
    return product.id


def _seed_request(
    db, contact, product_id: str, *, status: str = "new", portal_draft_at=None, revision_no: int = 0,
):
    req = PriceTagRequestService.create_request(
        db,
        contact_id=contact.id,
        company_id=_SORENTO_COMPANY_ID,
        data={
            "debtor_name": "ZZT Original Dealer",
            "lines": [{"line_type": "product", "product_id": product_id}],
        },
    )
    req.status = status
    req.portal_draft_at = portal_draft_at
    # r9 D7: a submitted request has answered who prints, and a revise
    # re-submits through the same completeness bar.
    req.print_by = "office"
    # AC-R2: the model needs `revision_no` / `last_revised_at` (migration
    # ptag_0006_revisions) - this raises AttributeError today, which is the
    # right red reason: the column does not exist yet.
    req.revision_no = revision_no
    db.commit()
    return req


@pytest.fixture(autouse=True)
def no_queue():
    with patch("app.services.queue_service.enqueue_job", return_value=None):
        yield


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _setup(db, *, cap: int = 3, status: str = "new", **request_kwargs):
    _seed_system_settings(db, cap=cap)
    _seed_ptag_config(db, max_revisions=None)
    contact = _seed_contact(db)
    product_id = _seed_product(db)
    row = _seed_request(db, contact, product_id, status=status, **request_kwargs)
    return contact, product_id, row


def _revise_payload(product_id: str) -> dict:
    return {"debtor_name": "ZZT Revised Dealer", "products": [{"product_id": product_id, "quantity": 5}]}


# =========================================================================== #
# AC-R2: revise happy path + disabled / status / cap / terminal / ownership
# =========================================================================== #


class TestReviseHappyPath:
    def test_revise_increments_counter_and_stamps_time(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id), _revise_payload(product_id),
            "Dealer changed their mind", 0,
        )

        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.revision_no == 1
        assert fresh.last_revised_at is not None

    def test_revise_writes_a_history_row_with_the_previous_snapshot(self, db):
        from app.models.portal import PortalFormRevision

        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id), _revise_payload(product_id),
            "Dealer changed their mind", 0,
        )

        rows = (
            db.query(PortalFormRevision)
            .filter(
                PortalFormRevision.source_entity_type == "price_tag_request",
                PortalFormRevision.source_entity_id == str(row.id),
            )
            .order_by(PortalFormRevision.version_no.asc())
            .all()
        )
        assert [r.kind for r in rows] == ["original", "revision"]
        assert rows[0].snapshot_json["debtor_name"] == "ZZT Original Dealer"
        assert rows[1].snapshot_json["debtor_name"] == "ZZT Revised Dealer"
        assert "products" in rows[1].snapshot_json  # lines ride along, like PR/SF

    def test_revise_replaces_fields_and_lines(self, db):
        contact, product_id, row = _setup(db)
        second_product = _seed_product(db)
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id),
            {"debtor_name": "ZZT Revised", "products": [{"product_id": second_product, "quantity": 2}]},
            "Swapped the product", 0,
        )

        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.debtor_name == "ZZT Revised"
        assert [line.product_id for line in fresh.lines] == [second_product]

    def test_revise_restarts_status_at_new(self, db):
        contact, product_id, row = _setup(db, status="changes_requested")
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id), _revise_payload(product_id),
            "Dealer changed their mind", 0,
        )

        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).status == "new"

    def test_revise_fires_the_form_sla_restart_once(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with patch("app.services.form_sla_service.emit_form_event") as emit:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id), _revise_payload(product_id),
                "Dealer changed their mind", 0,
            )
        assert emit.call_count == 1


class TestReviseRefused:
    def test_disabled_config_refuses_422(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)
        db.query(PortalRevisionConfig).filter(
            PortalRevisionConfig.source_entity_type == "price_tag_request"
        ).update({"is_enabled": False})
        db.commit()

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id), _revise_payload(product_id), "Reason", 0,
            )
        assert exc.value.status_code == 422
        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).revision_no == 0

    @pytest.mark.parametrize("status_value", ["designing", "proof_ready", "approved", "ready", "void"])
    def test_status_outside_allowed_refuses_422(self, db, status_value):
        contact, product_id, row = _setup(db, status=status_value)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id), _revise_payload(product_id), "Reason", 0,
            )
        assert exc.value.status_code == 422
        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).revision_no == 0

    def test_max_reached_refuses_422(self, db):
        contact, product_id, row = _setup(db, cap=1, revision_no=1)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id), _revise_payload(product_id), "Reason", 1,
            )
        assert exc.value.status_code == 422
        assert "used all 1 revision" in str(exc.value.detail)

    def test_another_contact_gets_404(self, db):
        contact, product_id, row = _setup(db)
        intruder = _seed_contact(db)
        token = _seed_token(intruder)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id), _revise_payload(product_id), "Reason", 0,
            )
        assert exc.value.status_code in (403, 404)
        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).revision_no == 0


# =========================================================================== #
# AC-R3: zero lines / set-guard / duplicate product / override survival
# =========================================================================== #


class TestReviseValidatesLines:
    def test_zero_lines_refuses_422_and_nothing_changes(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id),
                {"debtor_name": "ZZT Revised", "products": []}, "Reason", 0,
            )
        assert exc.value.status_code == 422
        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.revision_no == 0
        assert len(fresh.lines) == 1  # the original line survives untouched

    def test_set_guarded_line_now_revises_and_carries_a_warning(self, db):
        """Was a 422 (AC-S2-7): a revision is refused for package reasons no more.

        The revision path ran the same guard as submit, so retiring it has to be
        proved on both or a salesperson could submit a bare cabinet and then be
        blocked from correcting the request that holds it.
        """
        contact, product_id, row = _setup(db)
        guarded_product = _seed_product(db, class_label="Bathroom Furniture")
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id),
            {"products": [{"product_id": guarded_product, "quantity": 1}]}, "Reason", 0,
        )
        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.revision_no == 1
        assert [line.package_warning for line in fresh.lines] == ["No package defined"]

    def test_duplicate_product_refuses_422_duplicate_line(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id),
                {
                    "products": [
                        {"product_id": product_id, "quantity": 1},
                        {"product_id": product_id, "quantity": 2},
                    ]
                },
                "Reason",
                0,
            )
        assert exc.value.status_code == 422
        # AC-R7: the code names the duplicate product, never a raw 500 from
        # uq_ptag_line_request_product.
        detail = getattr(exc.value, "detail", None)
        code = detail.get("code") if isinstance(detail, dict) else None
        assert code == "DUPLICATE_LINE"
        # Review round 3: the duplicate check must run BEFORE any mutation -
        # the original line has to survive a refused revision untouched, the
        # same "before any mutation" ordering the zero-lines/set-guard cases
        # above already pin.
        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert len(fresh.lines) == 1
        assert fresh.lines[0].product_id == product_id

    def test_override_survives_when_the_same_product_is_revised(self, db):
        """Marketing's own work is not in the form's payload, so a re-save must not wipe it.

        The override lives on the line's TAG since S3 (D3), and `replace_lines`
        carries the whole tag set onto whichever new row keeps the same product.
        The rule being pinned has not moved: a salesperson changing a quantity
        must not silently reset a price marketing set by hand.
        """
        from app.models.price_tag import PriceTagRequestLine

        contact, product_id, row = _setup(db)
        line = db.query(PriceTagRequestLine).filter(PriceTagRequestLine.request_id == row.id).one()
        # One tag per line exists from creation (`_add_lines`).
        tag = line.tags[0]
        tag.marketing_price_override = 42.50
        tag.marketing_override_reason = "Marketing override ZZT"
        db.commit()
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id),
            {"products": [{"product_id": product_id, "quantity": 9}]}, "Reason", 0,
        )

        db.expire_all()
        fresh_line = (
            db.query(PriceTagRequestLine).filter(PriceTagRequestLine.request_id == row.id).one()
        )
        assert len(fresh_line.tags) == 1, "a surviving line keeps its tag set, not a fresh one"
        fresh_tag = fresh_line.tags[0]
        assert float(fresh_tag.marketing_price_override or 0) == pytest.approx(42.50)
        assert fresh_tag.marketing_override_reason == "Marketing override ZZT"


# =========================================================================== #
# AC-R4: create / draft PUT with a duplicate product never 500s
# =========================================================================== #


class TestCreateAndDraftPutRefuseDuplicateProduct:
    @pytest.fixture
    def http_client(self):
        from app.api.v1.public.portal import get_portal_token
        from app.database import get_db

        with blank_session() as db:
            contact = _seed_contact(db)

            def _override_get_db():
                yield db

            def _override_portal_token():
                return PortalToken(id=str(uuid.uuid4()), contact_id=contact.id, space_id="zzt-space")

            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[get_portal_token] = _override_portal_token
            try:
                with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
                    yield c, db, contact
            finally:
                app.dependency_overrides.clear()

    def test_post_create_duplicate_product_422_not_500(self, http_client):
        c, db, _contact = http_client
        product_id = _seed_product(db)

        res = c.post(
            _PTAG_BASE,
            json={
                "debtor_name": "ZZT Dealer",
                "lines": [
                    {"line_type": "product", "product_id": product_id, "quantity": 1},
                    {"line_type": "product", "product_id": product_id, "quantity": 2},
                ],
            },
        )
        assert res.status_code == 422, res.text
        assert res.json()["code"] == "DUPLICATE_LINE"

    def test_put_draft_duplicate_product_422_not_500(self, http_client):
        c, db, _contact = http_client
        product_id = _seed_product(db)
        created = c.post(
            _PTAG_BASE,
            json={"lines": [{"line_type": "product", "product_id": product_id}]},
        ).json()

        res = c.put(
            f"{_PTAG_BASE}/{created['id']}",
            json={
                "lines": [
                    {"line_type": "product", "product_id": product_id, "quantity": 1},
                    {"line_type": "product", "product_id": product_id, "quantity": 3},
                ]
            },
        )
        assert res.status_code == 422, res.text
        assert res.json()["code"] == "DUPLICATE_LINE"


# =========================================================================== #
# AC-R5: revisions list GET, revision-draft PUT/DELETE for price_tag_request
# =========================================================================== #


class TestRevisionsListAndDraftRoutes:
    @pytest.fixture
    def route_client(self):
        from app.database import get_db

        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def _seeded(self, db):
        contact, product_id, row = _setup(db)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}
        return contact, product_id, row, headers

    def test_get_revisions_list_for_owner(self, route_client):
        c, db = route_client
        _contact, _product_id, row, headers = self._seeded(db)

        res = c.get(f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revisions", headers=headers)
        assert res.status_code == 200, res.text

    def test_get_revisions_list_other_contact_404(self, route_client):
        c, db = route_client
        _contact, _product_id, row, _headers = self._seeded(db)
        intruder = _seed_contact(db)
        intruder_headers = {"X-Portal-Token": _persisted_token(db, intruder)}

        res = c.get(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revisions",
            headers=intruder_headers,
        )
        assert res.status_code == 404, res.text

    def test_put_revision_draft_saved_and_resumed_on_detail(self, route_client):
        c, db = route_client
        _contact, product_id, row, headers = self._seeded(db)

        put_res = c.put(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft",
            headers=headers,
            json={"base_revision_no": 0, "reason": "In progress", "fields": {"debtor_name": "ZZT Draft"}},
        )
        assert put_res.status_code == 200, put_res.text

        detail = c.get(f"{_PTAG_BASE}/{row.id}", headers=headers).json()
        assert detail.get("revision_draft") is not None
        assert detail["revision_draft"]["fields"]["debtor_name"] == "ZZT Draft"

    def test_delete_revision_draft_discards_it(self, route_client):
        c, db = route_client
        _contact, product_id, row, headers = self._seeded(db)
        c.put(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft",
            headers=headers,
            json={"base_revision_no": 0, "fields": {"debtor_name": "ZZT Draft"}},
        )

        del_res = c.delete(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft", headers=headers,
        )
        assert del_res.status_code == 200, del_res.text
        assert PortalRevisionService(db).get_draft("price_tag_request", str(row.id)) is None

    def test_revision_draft_other_contact_404(self, route_client):
        c, db = route_client
        _contact, _product_id, row, _headers = self._seeded(db)
        intruder = _seed_contact(db)
        intruder_headers = {"X-Portal-Token": _persisted_token(db, intruder)}

        res = c.put(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft",
            headers=intruder_headers,
            json={"base_revision_no": 0, "fields": {}},
        )
        assert res.status_code == 404, res.text

    def test_list_summaries_carry_revision_fields(self, route_client):
        c, db = route_client
        _contact, product_id, row, headers = self._seeded(db)

        items = c.get(f"{_PTAG_BASE}", headers=headers).json()["items"]
        assert items, "the seeded request must show up in its own list"
        item = next(i for i in items if i["id"] == str(row.id))
        assert {"revision_no", "last_revised_at", "has_revision_draft"} <= set(item)


# =========================================================================== #
# AC-R6: settings API - price_tag_request row, enable with its own statuses
# =========================================================================== #


class TestPortalRevisionSettingsIncludesPriceTagRequest:
    BASE = "/api/v1/forms-management/revision-configs"

    @pytest.fixture
    def settings_client(self, monkeypatch):
        from app.database import get_db
        from app.services.user_service import UserPermissionService

        # LIST-API-PERMS: GET /revision-configs carries `user_management.settings.view` now;
        # the office actor has no users row, so answer the role lookup as admin.
        monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def _as_office_user(self):
        from app.dependencies import get_current_user, get_current_user_or_api_key

        actor = {"id": str(uuid.uuid4()), "email": "office@example.test", "role": "admin"}
        app.dependency_overrides[get_current_user] = lambda: actor
        app.dependency_overrides[get_current_user_or_api_key] = lambda: actor

    def test_get_lists_price_tag_request_seeded_disabled(self, settings_client):
        """The migration seeds this row disabled - a create_all schema (this
        fixture) has the table with no rows, mirroring the other three types'
        own 'missing row means disabled' test."""
        c, db = settings_client
        self._as_office_user()
        _seed_ptag_config(db, is_enabled=False, allowed_statuses=[])

        items = c.get(self.BASE).json()["items"]
        row = next((i for i in items if i["source_entity_type"] == "price_tag_request"), None)
        assert row is not None, items
        assert row["is_enabled"] is False

    def test_put_enables_price_tag_request_with_its_own_statuses(self, settings_client):
        c, _db = settings_client
        self._as_office_user()

        res = c.put(
            f"{self.BASE}/price_tag_request",
            json={
                "is_enabled": True,
                "max_revisions": 2,
                "allowed_statuses": ["new", "designing", "changes_requested"],
                "restart_stage_code": None,
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source_entity_type"] == "price_tag_request"
        assert body["allowed_statuses"] == ["new", "designing", "changes_requested"]


# =========================================================================== #
# Security review of S10 (aa3ff8e21) - five gaps in the revise path.
# =========================================================================== #


def _revoke_grant(db, contact) -> None:
    """Hide price_tag_request for this contact regardless of any segment grant
    (PLAN-portal-forms-market-segment D1: the grant moved off access types) -
    an ``is_enabled=False`` override wins over the segment union. Mirrors
    ``test_portal_price_tag_routes.py::_revoke_the_grant``."""
    import uuid as _uuid

    from app.models.price_tag import ContactPortalFormOverride

    db.add(
        ContactPortalFormOverride(
            id=str(_uuid.uuid4()),
            contact_id=contact.id,
            form_type="price_tag_request",
            is_enabled=False,
        )
    )
    db.commit()


# D1 (PLAN-price-tag-line-promo-combo-subject.md): `PriceTagRequest.promotion_id`
# is DROPPED (ptag_0011) - the header column the old `TestRevisePromotionAudienceGate`
# (security review Gap A) guarded no longer exists. The backlog item that class's
# retirement comment named has since landed: `_apply_price_tag_lines` now converts
# a per-LINE `promotion_id` / `manual_sell_price` through `_convert_ptag_revise_line`
# and runs them through the SAME `_add_lines` gate create/update take
# (AC-S6-4/S6-5), so this class covers the line-level audience/coverage guard on
# revise instead.


def _grant_audience_code(db, contact_id: str, code: str = "dealer") -> None:
    from app.models.access import ContactAccessType, respond_contact_access_types

    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=code))
        db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact_id, access_type_code=code
        )
    )
    db.flush()


def _promotion_covering(db, product_id: str, *, access_levels=None) -> str:
    """A promotion with a real `PromotionProduct` row for `product_id`."""
    from decimal import Decimal

    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    promotion = Promotion(
        id=str(uuid.uuid4()),
        description=unique_code("ZZT promo"),
        is_active=True,
        access_levels=access_levels or ["dealer"],
        company_id=_SORENTO_COMPANY_ID,
    )
    db.add(promotion)
    db.flush()
    group = PromotionGroup(promotion_id=promotion.id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()),
            promotion_id=promotion.id,
            promotion_group_id=str(group.id),
            product_id=product_id,
            promo_selling_price=Decimal("400.00"),
            company_id=_SORENTO_COMPANY_ID,
        )
    )
    db.flush()
    return promotion.id


def _promotion_not_covering(db, *, access_levels=None) -> str:
    """A promotion with NO `PromotionProduct` rows at all - covers nothing."""
    from app.models.marketing import Promotion

    promotion = Promotion(
        id=str(uuid.uuid4()),
        description=unique_code("ZZT promo"),
        is_active=True,
        access_levels=access_levels or ["dealer"],
        company_id=_SORENTO_COMPANY_ID,
    )
    db.add(promotion)
    db.flush()
    return promotion.id


class TestReviseLinePromotionGate:
    """The line-level AC-S6-4/S6-5 gate, exercised through the real
    `POST .../revise` route (not the direct-service harness the rest of this
    file uses), since the wire shape - `fields` vs `products`, `promotion_id`
    absent vs explicit `null` - is exactly what this class is pinning."""

    @pytest.fixture
    def revise_client(self):
        from app.database import get_db

        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def _revise(self, c, row_id, headers, *, expected_revision_no, products, fields=None, reason="Reason"):
        return c.post(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row_id}/revise",
            headers=headers,
            json={
                "reason": reason,
                "expected_revision_no": expected_revision_no,
                "fields": fields or {},
                "products": products,
            },
        )

    def test_revise_line_promotion_wrong_audience_422(self, revise_client):
        """(a) a promotion that covers the product but is not visible to this
        contact's audience is refused, naming the line."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_covering(db, product_id, access_levels=["some-other-audience"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            products=[{"product_id": product_id, "quantity": 1, "promotion_id": promotion_id}],
        )
        assert res.status_code == 422, res.text
        body = res.json()
        assert body.get("detail") == "line:0", body
        assert body.get("code") == "PROMOTION_NOT_AVAILABLE", body

    def test_revise_line_promotion_covers_nothing_422(self, revise_client):
        """(b) a promotion with no `PromotionProduct` row for any product on
        the line is refused, naming the line."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_not_covering(db, access_levels=["dealer"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            products=[{"product_id": product_id, "quantity": 1, "promotion_id": promotion_id}],
        )
        assert res.status_code == 422, res.text
        body = res.json()
        assert body.get("detail") == "line:0", body
        assert body.get("code") == "PROMOTION_NOT_AVAILABLE", body

    def test_revise_omitting_promotion_id_keeps_the_old_line_value(self, revise_client):
        """(c) a products[i] entry that omits `promotion_id` entirely carries
        the OLD line's promotion forward - a revision that only touches a
        remark must not silently drop it."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_covering(db, product_id, access_levels=["dealer"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        first = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            products=[{"product_id": product_id, "quantity": 1, "promotion_id": promotion_id}],
        )
        assert first.status_code == 200, first.text

        second = self._revise(
            c, row.id, headers,
            expected_revision_no=1,
            reason="Just a remark",
            products=[{"product_id": product_id, "quantity": 1, "remarks": "Face out"}],
        )
        assert second.status_code == 200, second.text

        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.lines[0].promotion_id == promotion_id
        assert fresh.lines[0].remarks == "Face out"

    def test_revise_null_promotion_id_clears_it(self, revise_client):
        """(d) a products[i] entry sending `promotion_id: null` explicitly
        clears the line's promotion - distinct from omitting the key."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_covering(db, product_id, access_levels=["dealer"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        first = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            products=[{"product_id": product_id, "quantity": 1, "promotion_id": promotion_id}],
        )
        assert first.status_code == 200, first.text

        second = self._revise(
            c, row.id, headers,
            expected_revision_no=1,
            reason="Cleared the promotion",
            products=[{"product_id": product_id, "quantity": 1, "promotion_id": None}],
        )
        assert second.status_code == 200, second.text

        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.lines[0].promotion_id is None

    def test_revise_manual_price_with_promotion_on_the_same_line_422(self, revise_client):
        """(e) AC-S6-4: a manual price and a promotion are mutually exclusive
        on one line, even via revise."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_covering(db, product_id, access_levels=["dealer"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            fields={"price_mode": "selling"},
            products=[
                {
                    "product_id": product_id,
                    "quantity": 1,
                    "promotion_id": promotion_id,
                    "manual_sell_price": 123.45,
                }
            ],
        )
        assert res.status_code == 422, res.text

    def test_revise_rejects_out_of_bounds_manual_price(self, revise_client):
        """R6: the revise composer's ``products[]`` line is a raw dict with
        no pydantic schema at all - unlike create/update, where a `Decimal`
        field type at least rejects a non-numeric string - so a bad
        `manual_sell_price` here reaches `_as_decimal` completely
        unvalidated. -5 and 0 convert cleanly (no bound check anywhere);
        "abc" raises `decimal.InvalidOperation` uncaught."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        for bad in (-5, 0, "1E+400", "abc"):
            res = self._revise(
                c, row.id, headers,
                expected_revision_no=0,
                fields={"price_mode": "selling"},
                products=[
                    {"product_id": product_id, "quantity": 1, "manual_sell_price": bad}
                ],
            )
            assert res.status_code == 422, (bad, res.text)

    def test_revise_response_serialises_promotion_id_and_manual_sell_price(self, revise_client):
        """R15: the revise route's own response has to carry the SAME two
        line facts create/update already return, or a caller cannot show
        what a revision just saved without a second GET."""
        c, db = revise_client
        contact, product_id, row = _setup(db)
        _grant_audience_code(db, contact.id, "dealer")
        promotion_id = _promotion_covering(db, product_id, access_levels=["dealer"])
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = self._revise(
            c, row.id, headers,
            expected_revision_no=0,
            products=[
                {"product_id": product_id, "quantity": 1, "promotion_id": promotion_id}
            ],
        )
        assert res.status_code == 200, res.text
        line = res.json()["submission"]["lines"][0]
        assert line.get("promotion_id") == promotion_id, line
        assert "manual_sell_price" in line, line


class TestRevisionRoutesRequireFormVisibility:
    """Gap B: ``_require_own_request`` (the ownership check the generic
    revision routes dispatch to for price_tag_request) checks ownership
    ONLY - never ``_assert_visible``/``_require_price_tag_request_visible``.
    A contact whose price_tag_request grant is revoked can still list, revise
    and save/discard a revision draft on their own old request."""

    @pytest.fixture
    def route_client(self):
        from app.database import get_db

        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def _seeded_but_revoked(self, db):
        contact, product_id, row = _setup(db)
        _revoke_grant(db, contact)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}
        return row, headers

    def test_list_revisions_refused_without_visibility(self, route_client):
        c, db = route_client
        row, headers = self._seeded_but_revoked(db)

        res = c.get(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revisions",
            headers=headers,
        )
        assert res.status_code in (403, 404), res.text

    def test_revise_refused_without_visibility(self, route_client):
        c, db = route_client
        row, headers = self._seeded_but_revoked(db)

        res = c.post(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revise",
            headers=headers,
            json={"reason": "Reason", "expected_revision_no": 0, "fields": {}},
        )
        assert res.status_code in (403, 404), res.text

    def test_save_revision_draft_refused_without_visibility(self, route_client):
        c, db = route_client
        row, headers = self._seeded_but_revoked(db)

        res = c.put(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft",
            headers=headers,
            json={"base_revision_no": 0, "fields": {}},
        )
        assert res.status_code in (403, 404), res.text

    def test_discard_revision_draft_refused_without_visibility(self, route_client):
        c, db = route_client
        row, headers = self._seeded_but_revoked(db)

        res = c.delete(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/revision-draft",
            headers=headers,
        )
        assert res.status_code in (403, 404), res.text


class TestAttachmentGateRechecksPolicy:
    """Gap C: ``_require_editable`` treats the mere EXISTENCE of a revision
    draft row as "editable", never re-checking whether the request's CURRENT
    status still allows a revision. A draft saved while the request was
    `new` keeps unlocking attachments after the request moves to a terminal
    status the policy would refuse outright (`ready`, `void`)."""

    class _FakeStorageBackend:
        def upload_file(self, *args, **kwargs):
            key = args[1] if len(args) > 1 else kwargs.get("file_path")
            return key, f"https://cdn.test/{key}"

        def download_file(self, key: str) -> bytes:
            return b"zzt-file-bytes"

    @pytest.fixture
    def attachment_client(self, monkeypatch):
        from app.database import get_db
        from app.models.resources import AttachmentType
        from app.services.portal_service import PORTAL_ATTACHMENT_TYPE_CODE
        import app.services.storage_router as storage_router

        with blank_session() as db:
            db.add(
                AttachmentType(
                    id=str(uuid.uuid4()),
                    code=PORTAL_ATTACHMENT_TYPE_CODE,
                    type_name="Portal Submission",
                    allowed_extensions="jpg,jpeg,png,pdf",
                    max_file_size_mb=10,
                )
            )
            db.commit()

            fake_backend = self._FakeStorageBackend()
            monkeypatch.setattr(storage_router, "default_provider", lambda: "s3")
            monkeypatch.setattr(storage_router, "get_backend", lambda provider: fake_backend)
            monkeypatch.setattr(
                storage_router, "cdn_base_url", lambda provider, key: f"https://cdn.test/{key}"
            )

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.parametrize("terminal_status", ["ready", "void"])
    def test_attachment_gate_rechecks_policy(self, attachment_client, terminal_status):
        import io

        c, db = attachment_client
        contact, product_id, row = _setup(db)
        token_str = _persisted_token(db, contact)

        # A revision draft saved while the request is still `new`.
        PortalRevisionService(db).save_draft(
            _seed_token(contact),
            "price_tag_request",
            str(row.id),
            {"debtor_name": "ZZT Draft Edit"},
            "In progress",
            0,
        )

        # The request then moves on - approved/produced/voided, well past
        # any status the policy would still allow a revision at.
        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        fresh.status = terminal_status
        db.commit()

        upload_res = c.post(
            f"{_PORTAL_BASE}/attachments",
            data={"kind": "price_tag_request", "submission_id": str(row.id)},
            files={"file": ("po.pdf", io.BytesIO(b"%PDF-1.4 zzt"), "application/pdf")},
            headers={"X-Portal-Token": token_str},
        )
        assert upload_res.status_code == 409, upload_res.text

        from app.models.entity_attachment import EntityAttachmentLink
        from app.models.resources import Attachment

        att = Attachment(
            id=str(uuid.uuid4()),
            original_filename="ZZT-existing.pdf",
            stored_filename="ZZT-existing.pdf",
            file_path=f"portal/zzt/{uuid.uuid4()}.pdf",
            mime_type="application/pdf",
            uploader_kind="contact",
            uploaded_by_contact_id=contact.id,
        )
        db.add(att)
        db.flush()
        link = EntityAttachmentLink(
            entity_type="price_tag_request", entity_id=str(row.id), attachment_id=att.id
        )
        db.add(link)
        db.commit()

        delete_res = c.delete(
            f"{_PORTAL_BASE}/attachments/{link.id}",
            headers={"X-Portal-Token": token_str},
        )
        assert delete_res.status_code == 409, delete_res.text


class TestReviseCarriesAccessories:
    """Gap D: ``_convert_ptag_revise_line`` only reads ``product_id`` /
    ``product_set_id`` / ``quantity`` / ``remarks`` off a revise payload
    line, so ``replace_lines`` -> ``_add_lines`` writes every revised line's
    ``included_accessories`` back as ``None`` - the same "silently wipes a
    per-line detail on re-save" bug the marketing-override carry-over already
    fixed, left open for this field.

    It used to cover ``alternatives`` beside it. S2 drops that column
    (AC-S2-8), and what replaced it - the line's parts - carries over through
    its own rows rather than through this one scalar, so only the accessories
    half survives here."""

    def test_revise_carries_accessories(self, db):
        from app.models.price_tag import PriceTagRequestLine

        contact, product_id, row = _setup(db)
        line = db.query(PriceTagRequestLine).filter(
            PriceTagRequestLine.request_id == row.id
        ).one()
        line.included_accessories = "Tap + waste kit"
        db.commit()
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token,
            "price_tag_request",
            str(row.id),
            {"products": [{"product_id": product_id, "quantity": 4}]},
            "Reason",
            0,
        )

        db.expire_all()
        fresh_line = (
            db.query(PriceTagRequestLine).filter(PriceTagRequestLine.request_id == row.id).one()
        )
        assert fresh_line.included_accessories == "Tap + waste kit"


class TestRevisionRoutesMalformedId:
    """Gap E: neither ``PriceTagRequestService.get_request`` (used by
    ``_require_own_request``) nor ``PortalRevisionService.fetch_owned``
    validate the path id is a UUID before it reaches the query - a
    non-UUID id 500s from Postgres refusing the comparison instead of
    answering the usual "not found" 404."""

    @pytest.fixture
    def route_client(self):
        from app.database import get_db

        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def test_list_revisions_malformed_id_404(self, route_client):
        c, db = route_client
        contact = _seed_contact(db)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = c.get(
            f"{_PORTAL_BASE}/submissions/price_tag_request/not-a-uuid/revisions",
            headers=headers,
        )
        assert res.status_code == 404, res.text

    def test_revise_malformed_id_404(self, route_client):
        c, db = route_client
        contact = _seed_contact(db)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = c.post(
            f"{_PORTAL_BASE}/submissions/price_tag_request/not-a-uuid/revise",
            headers=headers,
            json={"reason": "Reason", "expected_revision_no": 0, "fields": {}},
        )
        assert res.status_code == 404, res.text


# =========================================================================== #
# Review round 3: price_mode validation, blank-debtor completeness, and the
# neighbours route for price_tag_request.
# =========================================================================== #


class TestRevisePriceModeValidation:
    """`price_mode` rides the generic setattr whitelist with no validation at
    all - a null value 500s on the NOT NULL column instead of 422ing, and any
    string outside ('list', 'selling') just persists."""

    def test_null_price_mode_refuses_422_not_500(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id),
                {"price_mode": None, "products": [{"product_id": product_id, "quantity": 1}]},
                "Reason", 0,
            )
        assert exc.value.status_code == 422
        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).price_mode == "list"

    def test_invalid_price_mode_refuses_422(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id),
                {
                    "price_mode": "banana",
                    "products": [{"product_id": product_id, "quantity": 1}],
                },
                "Reason", 0,
            )
        assert exc.value.status_code == 422
        db.expire_all()
        assert PriceTagRequestService.get_request(db, str(row.id)).price_mode == "list"


class TestReviseRequiresDebtorWithLines:
    """`_apply_price_tag_lines` validates lines only when ``products`` is in
    the payload - it never re-runs ``validate_submittable(require_debtor=
    True)``, so a revise that blanks both debtor fields while also touching
    lines commits a request with no dealer at all."""

    def test_blank_debtor_with_products_refuses_422_submit_incomplete(self, db):
        contact, product_id, row = _setup(db)
        token = _seed_token(contact)

        with pytest.raises(HTTPException) as exc:
            PortalRevisionService(db).revise(
                token, "price_tag_request", str(row.id),
                {
                    "debtor_code": "",
                    "debtor_name": "",
                    "products": [{"product_id": product_id, "quantity": 1}],
                },
                "Reason", 0,
            )
        assert exc.value.status_code == 422
        detail = getattr(exc.value, "detail", None)
        code = detail.get("code") if isinstance(detail, dict) else None
        keys = detail.get("detail") if isinstance(detail, dict) else None
        assert code == "SUBMIT_INCOMPLETE", detail
        assert keys and "debtor_name" in keys, detail

        db.expire_all()
        fresh = PriceTagRequestService.get_request(db, str(row.id))
        assert fresh.debtor_name == "ZZT Original Dealer"
        assert fresh.revision_no == 0


class TestNeighboursRouteForPriceTagRequest:
    """``GET .../neighbours`` still dispatches through ``_check_kind``
    (``SUPPORTED_TYPES`` only) and ``PortalService.get_neighbours`` (which
    re-checks the same tuple and has no price_tag_request branch), so the
    route 422s for every price tag request instead of answering prev/next."""

    @pytest.fixture
    def route_client(self):
        from app.database import get_db

        with blank_session() as db:

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            try:
                with TestClient(app) as c:
                    yield c, db
            finally:
                app.dependency_overrides.clear()

    def test_neighbours_for_the_owner(self, route_client):
        c, db = route_client
        contact, product_id, first = _setup(db)
        second = _seed_request(db, contact, product_id)
        headers = {"X-Portal-Token": _persisted_token(db, contact)}

        res = c.get(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{first.id}/neighbours",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 2
        assert first.id in (body["prev_id"], body["next_id"]) or body["position"] in (1, 2)

    def test_neighbours_other_contact_404(self, route_client):
        c, db = route_client
        _contact, _product_id, row = _setup(db)
        intruder = _seed_contact(db)
        headers = {"X-Portal-Token": _persisted_token(db, intruder)}

        res = c.get(
            f"{_PORTAL_BASE}/submissions/price_tag_request/{row.id}/neighbours",
            headers=headers,
        )
        assert res.status_code == 404, res.text


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    Every transition here goes through the real notifier, which sends over the
    network unless something stops it - the run log used to carry a live
    ``Window check: Respond.io list_messages failed`` per transition.
    """
    return _ptag_r9_seed.block_respond(monkeypatch)


# ---------------------------------------------------------------------------
# AC-S6-10 (PLAN-price-tag-r10.md S6): `print_excluded` is a TAG fact, like
# `marketing_price_override` - a revise that keeps the same product must
# carry it forward onto the surviving tag, not reset it.
# ---------------------------------------------------------------------------


def test_ac_s6_10_print_excluded_survives_when_the_same_product_is_revised():
    from app.models.price_tag import PriceTagRequestLine

    with blank_session() as db:
        contact, product_id, row = _setup(db)
        line = db.query(PriceTagRequestLine).filter(PriceTagRequestLine.request_id == row.id).one()
        tag = line.tags[0]
        tag.print_excluded = True
        db.commit()
        token = _seed_token(contact)

        PortalRevisionService(db).revise(
            token, "price_tag_request", str(row.id),
            {"products": [{"product_id": product_id, "quantity": 9}]}, "Reason", 0,
        )

        db.expire_all()
        fresh_line = (
            db.query(PriceTagRequestLine).filter(PriceTagRequestLine.request_id == row.id).one()
        )
        assert len(fresh_line.tags) == 1
        assert fresh_line.tags[0].print_excluded is True
