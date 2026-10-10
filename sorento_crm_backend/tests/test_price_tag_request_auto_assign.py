"""Auto-assign on submit (D8, PLAN-price-tag-r7-request-ux AC-S3-1..3).

``portal_submit_price_tag_request`` fires the form SLA (``emit_form_event``)
today and stops there - nothing copies the tracker's resolved assignee onto
``PriceTagRequest.assigned_to_id`` or moves the request past ``new``. D8
adds that: after the emit, read the (``price_tag_request``, id) tracker and,
when it carries an assignee, copy it onto the request and transition
``new -> designing``. No tracker / no assignee leaves the request ``new`` and
unclaimed (Claim as today, AC-S3-2). A failure in that block must not fail
the submit itself (AC-S3-3) - logged, request stays ``new``.

RED note for the coder: this pins the helper name
``PriceTagRequestService.auto_assign_from_tracker`` for AC-S3-3's
monkeypatch to have something to target - implement the D8 block as (or
behind) a staticmethod of that name on ``PriceTagRequestService``, called
from ``portal_submit_price_tag_request`` after ``emit_form_event``. Every
test in this file is RED until that exists: the assignment tests fail
because nothing copies the tracker's assignee yet, and the monkeypatch test
fails with an ``AttributeError`` because the target does not exist.

Team/agent/config seeding mirrors ``tests/test_form_handling_lock.py``'s
``seed`` fixture (the closest full ``AccessAgent``/``Team``/``AgentTeam``/
``TeamMember``/``FormSLAConfig`` chain in this suite), trimmed to one tier-1
team of exactly one member - the smallest chain ``emit_form_event`` can
resolve an assignee from.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session, unique_code
from tests import _ptag_r9_seed

_BASE = "/api/v1/public/portal/submissions/price_tag_request"
_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"


def _seed_contact_who_can_see_the_form(db) -> str:
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("contact"),
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    link_contact_company(db, contact.id)
    return contact.id


def _seed_product(db) -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
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


def _seed_one_member_tier1_config(db) -> str:
    """An active ``form_sla_configs`` row for ``price_tag_request`` / ``submit``
    whose tier-1 team has exactly ONE member. Returns that member's user id.
    """
    from app.models.access import AccessAgent, AgentTeam, Team, TeamMember
    from app.models.sla import FormSLAConfig, SLAPolicy, SLAPolicyTier
    from app.models.user import User

    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=unique_code("POL"), name="ZZT policy"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )

    agent_id = str(uuid.uuid4())
    agent_code = unique_code("agent")
    db.add(AccessAgent(id=agent_id, code=agent_code, name="ZZT Marketing Agent"))

    team_id = str(uuid.uuid4())
    team_set_code = unique_code("teamset")
    db.add(Team(id=team_id, name=unique_code("Team"), company_id=_SORENTO_COMPANY_ID))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code=team_set_code,
            team_id=team_id,
            tier=1,
            company_id=_SORENTO_COMPANY_ID,
        )
    )
    db.flush()

    member_id = str(uuid.uuid4())
    db.add(User(id=member_id, email=f"{member_id}@t.zzt", name="ZZT Marketing Mei", status="ACTIVE"))
    db.flush()
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=member_id))

    db.add(
        FormSLAConfig(
            id=str(uuid.uuid4()),
            source_entity_type="price_tag_request",
            stage_code=unique_code("stage"),
            policy_id=policy_id,
            agent_code=agent_code,
            team_set_code=team_set_code,
            start_event="submit",
            is_active=True,
            notify_assignee=False,
        )
    )
    db.commit()
    return member_id


@pytest.fixture
def client():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    with blank_session() as db:
        contact_id = _seed_contact_who_can_see_the_form(db)

        def _override_get_db():
            yield db

        def _override_portal_token():
            return PortalToken(id=str(uuid.uuid4()), contact_id=contact_id, space_id="zzt-space")

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_portal_token] = _override_portal_token
        try:
            with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
                yield c, db, contact_id
        finally:
            app.dependency_overrides.clear()


def _submittable_payload(product_id: str) -> dict:
    return {
        "debtor_name": "ZZT Dealer",
        "needed_by_date": str(date.today() + timedelta(days=7)),
        # r9 D7: who prints has no default and submit refuses without it.
        "print_by": "office",
        "lines": [{"line_type": "product", "product_id": product_id}],
    }


class TestAutoAssignWithAnActiveConfig:
    def test_submit_lands_as_designing_assigned_to_the_lone_tier1_member(self, client):
        """AC-S3-1."""
        c, db, _contact_id = client
        product_id = _seed_product(db)
        member_id = _seed_one_member_tier1_config(db)
        created = c.post(_BASE, json=_submittable_payload(product_id)).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "designing"
        assert body["assigned_to_id"] == member_id

    def test_an_sla_tracker_exists_for_the_request(self, client):
        """AC-S3-1: the tracker itself, not just the copied field."""
        c, db, _contact_id = client
        from app.models.sla import ConversationSLATracking

        product_id = _seed_product(db)
        member_id = _seed_one_member_tier1_config(db)
        created = c.post(_BASE, json=_submittable_payload(product_id)).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")
        assert res.status_code == 200, res.text

        tracker = (
            db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == "price_tag_request",
                ConversationSLATracking.source_entity_id == created["id"],
            )
            .first()
        )
        assert tracker is not None
        assert tracker.assigned_to_id == member_id

    def test_auto_assign_creates_the_tag_sheet_page(self, client):
        """B1: a request auto-assigned straight into `designing` needs the
        same tag_sheet page Claim would have created, or GET .../design
        404s NO_PAGE with no Claim button left to fix it from."""
        c, db, _contact_id = client
        from app.models.dealer_kit import Page
        from app.models.price_tag import PriceTagRequest

        product_id = _seed_product(db)
        _seed_one_member_tier1_config(db)
        created = c.post(_BASE, json=_submittable_payload(product_id)).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")
        assert res.status_code == 200, res.text

        db.expire_all()
        row = (
            db.query(PriceTagRequest)
            .filter(PriceTagRequest.id == created["id"])
            .first()
        )
        assert row.page_id is not None

        page = db.query(Page).filter(Page.id == row.page_id).first()
        assert page is not None
        assert page.kind == "tag_sheet"


class TestNoActiveConfig:
    def test_submit_leaves_the_request_new_and_unassigned(self, client):
        """AC-S3-2: the Claim path is unchanged - nothing to auto-place with."""
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(_BASE, json=_submittable_payload(product_id)).json()

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "new"
        assert body["assigned_to_id"] is None


class TestTheAssignmentBlockFailingDoesNotFailSubmit:
    def test_submit_still_succeeds_as_new_when_auto_assign_raises(self, client, monkeypatch):
        """AC-S3-3. Patch target: ``PriceTagRequestService.auto_assign_from_tracker``
        (the coder implements the D8 read-tracker-and-copy block under that name)."""
        c, db, _contact_id = client
        from app.services.price_tag_request_service import PriceTagRequestService

        product_id = _seed_product(db)
        _seed_one_member_tier1_config(db)
        created = c.post(_BASE, json=_submittable_payload(product_id)).json()

        def _boom(*args, **kwargs):
            raise RuntimeError("ZZT boom")

        monkeypatch.setattr(
            PriceTagRequestService, "auto_assign_from_tracker", staticmethod(_boom)
        )

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "new"
        assert body["assigned_to_id"] is None


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    Every transition here goes through the real notifier, which sends over the
    network unless something stops it - the run log used to carry a live
    ``Window check: Respond.io list_messages failed`` per transition.
    """
    return _ptag_r9_seed.block_respond(monkeypatch)
