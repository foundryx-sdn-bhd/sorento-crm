"""r9 S4/D12-D15: the salesperson hears about it (AC-S4-1 .. AC-S4-6).

Nobody told the salesperson anything. Marketing marked a design ready and the
request sat there until the salesperson happened to open the portal. B1's
ruling is deliberately blunt: EVERY status change goes to them, including
confirmations of their own actions, plus "PDF ready" when a self print export
finishes.

The contract these tests hold the coder to:

* ``app.services.price_tag_notify.notify_salesperson(db, request, event, **ctx)``
  is the single door. ``transition_status`` calls it THROUGH THE MODULE, not
  through a bound name - the same reason the auto-export call is written that
  way today, so a test can replace the attribute.
* it is called ONCE per transition, and a failure inside it is logged and
  swallowed. A WhatsApp outage must never turn a successful approve into a
  failed one.
* the assignee's in-app bell is a different thing with a different rule: only
  ``changes_requested`` and ``approved``, deduplicated per request + status +
  round.

Red before the coder starts: the module, the SLA entry and the note
persistence do not exist.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests import _ptag_r9_seed as seed
from tests._pg_fixture import blank_session, unique_code

# Captured at COLLECTION time, before the `no_respond` autouse fixture below
# ever runs and monkeypatches the module attributes of the same names. S6/S7
# (PLAN-price-tag-ai-extract-resolver.md D10/D11) need the REAL
# `build_context_vars` (to prove the new price_tag_update branch), so its
# tests call this captured reference directly rather than going through
# `respond_messaging_service.build_context_vars`, which stays faked to `{}`
# for every other test in this file.
from app.services import respond_messaging_service as _rms

_REAL_BUILD_CONTEXT_VARS = _rms.build_context_vars

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1", reason="SKIP_LIVE_DB_TESTS=1"
)

_CRM = "/api/v1/dealer-kit/price-tag-requests/{id}"
_PORTAL = "/api/v1/public/portal/submissions/price_tag_request/{id}"


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    The notifier itself stays REAL, so the copy table and the IntegrationLog
    row - the two things half this file is about - still run.
    """
    return seed.block_respond(monkeypatch)


@pytest.fixture
def db_only():
    with blank_session() as db:
        seed.seed_marketer(db)
        yield db


@pytest.fixture
def notifier(monkeypatch):
    """Capture every ``notify_salesperson`` call instead of sending anything."""
    from app.services import price_tag_notify

    calls: list[dict] = []

    def _record(db, request, event, **ctx):
        calls.append({"request_id": request.id, "event": event, "ctx": ctx})

    monkeypatch.setattr(price_tag_notify, "notify_salesperson", _record)
    return calls


@pytest.fixture
def no_export(monkeypatch):
    """The approve auto-export is a different slice's concern."""
    monkeypatch.setattr(
        "app.services.dealer_kit.tag_sheet_export_service.request_tag_sheet_export",
        lambda *a, **k: None,
    )


def _request_at(db, status, *, print_by="office", contact_id=None):
    contact_id = contact_id or seed.seed_portal_contact(db)
    product = seed.seed_product(db)
    return seed.seed_request(
        db,
        contact_id,
        status=status,
        products=[product],
        print_by=print_by,
        assigned_to_id=seed.MARKETER_ID,
    )


# ---------------------------------------------------------------------------
# AC-S4-1 - every edge, exactly once
# ---------------------------------------------------------------------------


EDGES = [
    ("new", "designing", "self"),
    ("designing", "proof_ready", "self"),
    ("proof_ready", "changes_requested", "self"),
    ("proof_ready", "approved", "self"),
    ("approved", "ready_for_collection", "office"),
    ("ready_for_collection", "collected", "office"),
    ("designing", "rejected", "office"),
    ("proof_ready", "void", "office"),
]


class TestEveryTransitionReachesTheSalesperson:
    @pytest.mark.parametrize("current,nxt,print_by", EDGES)
    def test_one_call_per_transition_naming_the_event_and_a_portal_link(
        self, db_only, notifier, no_export, current, nxt, print_by
    ):
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, current, print_by=print_by)

        PriceTagRequestService.transition_status(
            db_only, request.id, nxt, user_id=seed.MARKETER_ID
        )

        assert len(notifier) == 1, notifier
        call = notifier[0]
        assert call["request_id"] == request.id
        assert call["event"] == nxt, (
            "the event name is the status the request landed on, so the copy "
            "table in the plan can be keyed off it"
        )

    def test_a_notifier_failure_is_logged_and_the_transition_still_commits(
        self, db_only, monkeypatch, no_export
    ):
        from app.models.price_tag import PriceTagRequest
        from app.services import price_tag_notify
        from app.services.price_tag_request_service import PriceTagRequestService

        def _boom(*_args, **_kwargs):
            raise RuntimeError("Respond.io is down")

        monkeypatch.setattr(price_tag_notify, "notify_salesperson", _boom)
        request = _request_at(db_only, "designing")

        PriceTagRequestService.transition_status(
            db_only, request.id, "proof_ready", user_id=seed.MARKETER_ID
        )
        db_only.commit()
        db_only.expire_all()

        assert (
            db_only.query(PriceTagRequest)
            .filter(PriceTagRequest.id == request.id)
            .first()
            .status
            == "proof_ready"
        )

    def test_a_refused_transition_notifies_nobody(self, db_only, notifier, no_export):
        from app.services.error_handler import AppException
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, "new")

        with pytest.raises(AppException):
            PriceTagRequestService.transition_status(
                db_only, request.id, "approved", user_id=seed.MARKETER_ID
            )

        assert notifier == []


# ---------------------------------------------------------------------------
# AC-S4-2 - the PDF
# ---------------------------------------------------------------------------


class TestPdfReady:
    def test_a_self_print_export_completing_says_the_pdf_is_ready(
        self, db_only, notifier
    ):
        from app.tasks import dealer_kit_export_tasks

        request = _request_at(db_only, "approved", print_by="self")
        seed.attach_design(db_only, request)

        dealer_kit_export_tasks.notify_price_tag_pdf_ready(db_only, request.id)

        assert [call["event"] for call in notifier] == ["pdf_ready"]

    def test_an_office_print_export_says_nothing(self, db_only, notifier):
        """The office collects the tags; the salesperson never downloads them."""
        from app.tasks import dealer_kit_export_tasks

        request = _request_at(db_only, "approved", print_by="office")
        seed.attach_design(db_only, request)

        dealer_kit_export_tasks.notify_price_tag_pdf_ready(db_only, request.id)

        assert notifier == []


# ---------------------------------------------------------------------------
# AC-S4-3 - the send is observable without Respond
# ---------------------------------------------------------------------------


class TestTheSendLeavesATrail:
    def test_it_writes_an_integration_log_row_against_price_tag_requests(
        self, db_only, monkeypatch
    ):
        """Respond sends are disabled on a lane stack, so the outbox / log row
        is how a transition's message is verified at all (AC-S4-3)."""
        from app.models.integration import IntegrationLog
        from app.services import price_tag_notify

        sent: list[dict] = []

        def _fake_send(db, *, identifier, text, use_case, context_vars=None, **_kw):
            sent.append({"identifier": identifier, "text": text, "use_case": use_case})
            return {
                "sent_as": "text",
                "response": {"id": "zzt-msg-1"},
                "window_state": "open",
                "request_payload": {"message": {"type": "text", "text": text}},
            }

        monkeypatch.setattr(
            "app.services.respond_messaging_service.send_text_or_template", _fake_send
        )
        request = _request_at(db_only, "proof_ready")

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")
        db_only.commit()

        assert len(sent) == 1, "the notifier did not reach the messaging service"
        assert request.doc_number in sent[0]["text"]

        rows = (
            db_only.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "price_tag_requests")
            .filter(IntegrationLog.business_id == str(request.id))
            .all()
        )
        assert len(rows) == 1, "no IntegrationLog row for the price tag send"
        assert rows[0].direction == "outbound"

    def test_the_message_carries_the_portal_link_for_that_submission(
        self, db_only, monkeypatch
    ):
        from app.services import price_tag_notify

        sent: list[str] = []
        monkeypatch.setattr(
            "app.services.respond_messaging_service.send_text_or_template",
            lambda db, *, identifier, text, use_case, context_vars=None, **_kw: (
                sent.append(text)
                or {
                    "sent_as": "text",
                    "response": {},
                    "window_state": "open",
                    "request_payload": {},
                }
            ),
        )
        request = _request_at(db_only, "proof_ready")

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")

        assert sent, "nothing was sent"
        assert "price_tag_request" in sent[0], (
            "the salesperson needs a link that opens THIS request in the portal"
        )


# ---------------------------------------------------------------------------
# AC-S4-4 - the assignee's bell
# ---------------------------------------------------------------------------


class TestAssigneeBell:
    def _bells(self, db, user_id: str):
        from app.models.notification import Notification

        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .filter(Notification.source_entity_type == "price_tag_request")
            .all()
        )

    @pytest.mark.parametrize(
        "current,nxt,event_type",
        [
            ("proof_ready", "changes_requested", "price_tag_changes_requested"),
            ("proof_ready", "approved", "price_tag_approved"),
        ],
    )
    def test_the_assignee_gets_one_in_app_notification(
        self, db_only, notifier, no_export, current, nxt, event_type
    ):
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, current)

        PriceTagRequestService.transition_status(
            db_only, request.id, nxt, user_id=seed.MARKETER_ID
        )
        db_only.commit()

        bells = self._bells(db_only, seed.MARKETER_ID)
        assert len(bells) == 1, [bell.title for bell in bells]
        assert request.doc_number in (bells[0].title or "")
        # There is no `link` column: a deep link travels in `data` (the same
        # place every other in-app notification puts one).
        haystack = f"{bells[0].body or ''} {bells[0].data or {}}"
        assert f"/dealer-kit/price-tag-requests/{request.id}" in haystack, haystack
        assert bells[0].event_type == event_type

    def test_a_transition_that_is_neither_rings_no_bell(
        self, db_only, notifier, no_export
    ):
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, "new")

        PriceTagRequestService.transition_status(
            db_only, request.id, "designing", user_id=seed.MARKETER_ID
        )
        db_only.commit()

        assert self._bells(db_only, seed.MARKETER_ID) == []

    def test_the_same_request_status_and_round_does_not_duplicate(
        self, db_only, notifier, no_export
    ):
        """Marketing sends the same proof back twice in one round: one bell."""
        from app.models.price_tag import PriceTagRequest
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, "proof_ready")

        PriceTagRequestService.transition_status(
            db_only, request.id, "changes_requested", user_id=seed.MARKETER_ID
        )
        db_only.commit()
        db_only.query(PriceTagRequest).filter(
            PriceTagRequest.id == request.id
        ).update({"status": "proof_ready"})
        db_only.commit()
        PriceTagRequestService.transition_status(
            db_only, request.id, "changes_requested", user_id=seed.MARKETER_ID
        )
        db_only.commit()

        assert len(self._bells(db_only, seed.MARKETER_ID)) == 1


# ---------------------------------------------------------------------------
# AC-S4-5 - the form SLA notification says PT-...
# ---------------------------------------------------------------------------


class TestFormSlaNamesTheDocument:
    def test_the_number_source_resolves_the_pt_number(self, db_only):
        from app.services import form_sla_service

        contact_id = seed.seed_portal_contact(db_only)
        product = seed.seed_product(db_only)
        request = seed.seed_request(
            db_only, contact_id, status="new", products=[product]
        )

        resolved = form_sla_service._resolve_entity_number(
            db_only, "price_tag_request", str(request.id)
        )

        assert resolved == request.doc_number
        assert resolved.startswith("PT-")

    def test_the_detail_link_points_at_the_crm_page(self):
        from app.services import form_sla_service

        entity_id = "11111111-2222-3333-4444-555555555555"

        assert form_sla_service._form_detail_link("price_tag_request", entity_id) == (
            f"/dealer-kit/price-tag-requests/{entity_id}"
        )


# ---------------------------------------------------------------------------
# AC-S4-6 - a rejection's reason survives
# ---------------------------------------------------------------------------


class TestTheTransitionNoteIsKept:
    @pytest.fixture
    def crm(self, monkeypatch):
        from app.dependencies import (
            get_current_user,
            get_current_user_or_api_key,
            get_db,
        )
        from app.models.base import set_company_scope
        from app.services import price_tag_notify
        from app.services.company_scope_resolver import apply_company_scope

        monkeypatch.setattr(
            price_tag_notify, "notify_salesperson", lambda *a, **k: None
        )
        with blank_session() as db:
            seed.seed_marketer(db)

            def _override_get_db():
                yield db

            async def _override_scope():
                scope = frozenset({seed.SORENTO})
                set_company_scope(db, scope)
                return scope

            principal = {
                "id": seed.MARKETER_ID,
                "email": "zzt-ptag-r9-marketer@test.com",
            }
            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[apply_company_scope] = _override_scope
            app.dependency_overrides[get_current_user] = lambda: principal
            app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
            try:
                with TestClient(app) as client:
                    yield client, db
            finally:
                app.dependency_overrides.clear()

    def test_a_rejection_note_lands_as_a_general_review_comment_by_the_user(
        self, crm
    ):
        """``TransitionPayload.note`` has existed since r7 and the route threw
        it away, so a rejection reason was typed into a box that discarded it."""
        client, db = crm
        from app.models.price_tag import PriceTagReviewComment

        contact_id = seed.seed_portal_contact(db)
        product = seed.seed_product(db)
        request = seed.seed_request(
            db, contact_id, status="proof_ready", products=[product]
        )

        response = client.post(
            f"{_CRM.format(id=request.id)}/transition",
            json={"status": "rejected", "note": "The dealer cancelled the order"},
        )

        assert response.status_code == 200, response.text
        rows = (
            db.query(PriceTagReviewComment)
            .filter(PriceTagReviewComment.request_id == request.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].tag_id is None
        assert rows[0].body == "The dealer cancelled the order"
        assert rows[0].author_user_id == seed.MARKETER_ID
        assert rows[0].author_contact_id is None

    def test_a_transition_with_no_note_creates_nothing(self, crm):
        client, db = crm
        from app.models.price_tag import PriceTagReviewComment

        contact_id = seed.seed_portal_contact(db)
        product = seed.seed_product(db)
        request = seed.seed_request(
            db, contact_id, status="proof_ready", products=[product]
        )

        client.post(
            f"{_CRM.format(id=request.id)}/transition", json={"status": "rejected"}
        )

        assert (
            db.query(PriceTagReviewComment)
            .filter(PriceTagReviewComment.request_id == request.id)
            .count()
            == 0
        )


# ---------------------------------------------------------------------------
# B3 - the sweep is a transition, and the salesperson hears about it too
# ---------------------------------------------------------------------------


class TestTheAutoCollectSweepNotifies:
    """AC-S4-1 says EVERY status change reaches the salesperson, and the copy
    table has a line for this one - "marked collected automatically after N
    days". The sweep writes the status straight onto the row and never calls
    the notifier, so the ONE transition nobody is present for is the one the
    salesperson is never told about: their tags are closed overnight and the
    first they hear of it is an empty counter.
    """

    def _waiting(self, db, *, days_ago: int, days: int):
        from app.models.price_tag import PriceTagRequest
        from app.models.user import SystemSetting

        row = db.query(SystemSetting).first()
        if row is None:
            row = SystemSetting(id=str(uuid.uuid4()), name="ZZT Co")
            db.add(row)
        row.price_tag_auto_collect_days = days
        request = _request_at(db, "ready_for_collection", print_by="office")
        db.query(PriceTagRequest).filter(PriceTagRequest.id == request.id).update(
            {
                "ready_for_collection_at": datetime.utcnow()
                - timedelta(days=days_ago)
            }
        )
        db.commit()
        return request

    def test_a_swept_request_notifies_once_with_the_auto_context(
        self, db_only, notifier
    ):
        from app.services.price_tag_request_service import PriceTagRequestService

        request = self._waiting(db_only, days_ago=9, days=7)

        PriceTagRequestService.run_auto_collect(db_only)

        assert len(notifier) == 1, notifier
        call = notifier[0]
        assert call["request_id"] == request.id
        assert call["event"] == "collected"
        assert call["ctx"].get("auto") is True, (
            "without it the copy reads 'marked collected', which is a claim "
            "that somebody came to the counter"
        )
        assert call["ctx"].get("days") == 7

    def test_a_request_the_sweep_left_alone_notifies_nobody(self, db_only, notifier):
        from app.services.price_tag_request_service import PriceTagRequestService

        self._waiting(db_only, days_ago=2, days=7)

        PriceTagRequestService.run_auto_collect(db_only)

        assert notifier == []

    def test_the_logged_text_is_the_copy_table_auto_line(self, db_only):
        """The lane stack has Respond disabled, so the IntegrationLog row is the
        only evidence of what was said (AC-S4-3)."""
        from app.models.integration import IntegrationLog
        from app.services.price_tag_request_service import PriceTagRequestService

        request = self._waiting(db_only, days_ago=30, days=7)

        PriceTagRequestService.run_auto_collect(db_only)
        db_only.commit()

        rows = (
            db_only.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "price_tag_requests")
            .filter(IntegrationLog.business_id == str(request.id))
            .all()
        )
        assert len(rows) == 1, "no IntegrationLog row for the swept request"
        payload = str(rows[0].request_payload)
        assert "marked collected automatically after 7 days" in payload, payload
        assert request.doc_number in payload


# ---------------------------------------------------------------------------
# S1 - the round travels with the change request
# ---------------------------------------------------------------------------


class TestTheRoundReachesBothNotifications:
    """`transition_status` takes a `notify_ctx`, and the portal's
    request-changes route passes none - so `round_no` falls back to 1 on every
    round and `count` is never set.

    Two consequences, both silent. The bell dedups on request + status + ROUND,
    so a second round of changes rings nothing at all: the assignee is told
    once, ever. And the salesperson's confirmation says "You sent change
    requests" instead of naming how many, which is the whole point of sending
    them in one call.
    """

    @pytest.fixture
    def portal(self):
        from app.api.v1.public.portal import get_portal_token
        from app.database import get_db
        from app.models.portal import PortalToken

        with blank_session() as db:
            seed.seed_marketer(db)
            contact_id = seed.seed_portal_contact(db)
            # NOT a second `block_respond`: the module's autouse `no_respond`
            # already installed one, and re-installing here would hand the test
            # a recorder nothing writes to.

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[get_portal_token] = lambda: PortalToken(
                id=str(uuid.uuid4()), contact_id=contact_id, space_id="zzt-space"
            )
            try:
                with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as client:
                    yield client, db, contact_id
            finally:
                app.dependency_overrides.clear()

    def _proof_ready(self, db, contact_id):
        product = seed.seed_product(db)
        request = seed.seed_request(
            db,
            contact_id,
            status="proof_ready",
            products=[product],
            print_by="office",
            assigned_to_id=seed.MARKETER_ID,
        )
        page, doc = seed.attach_design(db, request)
        return request, page, doc

    def _bells(self, db):
        from app.models.notification import Notification

        return (
            db.query(Notification)
            .filter(Notification.user_id == seed.MARKETER_ID)
            .filter(Notification.source_entity_type == "price_tag_request")
            .all()
        )

    def _pin(self, line_id, body):
        return {"line_id": line_id, "x": 0.2, "y": 0.3, "w": 0, "h": 0, "body": body}

    def test_a_second_round_rings_the_bell_again(self, portal):
        """The counter is walked through the REAL transitions, not poked in
        with a raw status update: `review_round` is a column
        `transition_status` increments on every entry into `proof_ready`
        (r9 review-round leftover), so a fixture that sets `status` directly
        leaves it at 0 and proves nothing about the counted rule.
        """
        from app.models.price_tag import PriceTagRequest
        from app.services.price_tag_request_service import PriceTagRequestService

        client, db, contact_id = portal
        product = seed.seed_product(db)
        request = seed.seed_request(
            db,
            contact_id,
            status="designing",
            products=[product],
            print_by="office",
            assigned_to_id=seed.MARKETER_ID,
        )
        seed.attach_design(db, request)
        line_id = request.lines[0].id

        PriceTagRequestService.transition_status(
            db, request.id, "proof_ready", user_id=seed.MARKETER_ID
        )
        db.commit()

        client.post(
            f"{_PORTAL.format(id=request.id)}/request-changes",
            json={"comments": [self._pin(line_id, "Round one")]},
        )
        assert len(self._bells(db)) == 1

        # Marketing marks the design ready again - the counter, not a
        # snapshot, is what says this is a second round.
        PriceTagRequestService.transition_status(
            db, request.id, "proof_ready", user_id=seed.MARKETER_ID
        )
        db.commit()
        db.expire_all()
        fresh = (
            db.query(PriceTagRequest).filter(PriceTagRequest.id == request.id).first()
        )
        assert fresh.review_round == 2, "counter should be 2 after the second proof"

        client.post(
            f"{_PORTAL.format(id=request.id)}/request-changes",
            json={"comments": [self._pin(line_id, "Round two")]},
        )

        bells = self._bells(db)
        assert len(bells) == 2, (
            "round 2 deduplicated against round 1, so the assignee was never "
            "told the salesperson came back"
        )
        assert len({bell.dedup_key for bell in bells}) == 2

    def test_the_salesperson_text_names_how_many_were_sent(self, portal, no_respond):
        client, db, contact_id = portal
        request, page, doc = self._proof_ready(db, contact_id)
        line_id = request.lines[0].id

        client.post(
            f"{_PORTAL.format(id=request.id)}/request-changes",
            json={
                "comments": [
                    self._pin(line_id, "Bigger price"),
                    self._pin(line_id, "Move the logo"),
                    self._pin(line_id, "Drop the badge"),
                ]
            },
        )

        sent = [row["text"] for row in no_respond]
        assert sent, "nothing was sent at all"
        assert any("You sent 3 change requests on" in text for text in sent), sent

    def test_one_pin_is_singular_not_1_change_requests(self, portal, no_respond):
        """r9 review-round leftover R6: the copy table pluralised at every
        count, including 1 - "You sent 1 change requests on" is not English.
        """
        client, db, contact_id = portal
        request, page, doc = self._proof_ready(db, contact_id)
        line_id = request.lines[0].id

        client.post(
            f"{_PORTAL.format(id=request.id)}/request-changes",
            json={"comments": [self._pin(line_id, "Bigger price")]},
        )

        sent = [row["text"] for row in no_respond]
        assert sent, "nothing was sent at all"
        assert any("You sent 1 change request on" in text for text in sent), sent
        assert not any("1 change requests" in text for text in sent), sent


# ---------------------------------------------------------------------------
# S9 - the first line of the copy table
# ---------------------------------------------------------------------------


class TestSubmitNotifies:
    """The copy table opens with "{doc} received. We will start designing
    shortly." and nothing ever sends it: the salesperson presses Submit and
    hears nothing back, which is the exact silence B1 was ruled to end.
    """

    @pytest.fixture
    def portal(self):
        from app.api.v1.public.portal import get_portal_token
        from app.database import get_db
        from app.models.portal import PortalToken

        with blank_session() as db:
            seed.seed_marketer(db)
            contact_id = seed.seed_portal_contact(db)

            def _override_get_db():
                yield db

            app.dependency_overrides[get_db] = _override_get_db
            app.dependency_overrides[get_portal_token] = lambda: PortalToken(
                id=str(uuid.uuid4()), contact_id=contact_id, space_id="zzt-space"
            )
            try:
                with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as client:
                    yield client, db, contact_id
            finally:
                app.dependency_overrides.clear()

    def test_a_submit_reaches_the_salesperson(self, portal, notifier):
        from app.services.price_tag_request_service import PriceTagRequestService

        client, db, contact_id = portal
        product = seed.seed_product(db)
        request = PriceTagRequestService.create_request(
            db,
            contact_id=contact_id,
            company_id=seed.SORENTO,
            data={
                "debtor_name": "ZZT Dealer",
                "lines": [{"line_type": "product", "product_id": product.id}],
            },
        )
        request.print_by = "office"
        db.commit()

        response = client.post(f"{_PORTAL.format(id=request.id)}/submit")

        assert response.status_code == 200, response.text
        events = [call["event"] for call in notifier]
        assert "submitted" in events, events


# ---------------------------------------------------------------------------
# S6 - the message reports something that has actually happened
# ---------------------------------------------------------------------------


class TestTheNotifierRunsAfterTheCommit:
    """A message is a promise about the database.

    The notifier fires inside `transition_status`, before any caller commits,
    so a failure on the way out - a constraint, a rollback in the route, a
    request that never reaches `db.commit()` - leaves the salesperson holding a
    WhatsApp for a transition that did not survive. The send has to come after
    the commit that makes it true.
    """

    def test_the_transition_is_committed_before_the_notifier_is_called(
        self, db_only, monkeypatch, no_export
    ):
        from app.services import price_tag_notify
        from app.services.price_tag_request_service import PriceTagRequestService

        request = _request_at(db_only, "designing")

        commits = {"count": 0}
        real_commit = db_only.commit

        def _counted_commit(*args, **kwargs):
            commits["count"] += 1
            return real_commit(*args, **kwargs)

        monkeypatch.setattr(db_only, "commit", _counted_commit)

        seen: list[int] = []
        monkeypatch.setattr(
            price_tag_notify,
            "notify_salesperson",
            lambda db, req, event, **ctx: seen.append(commits["count"]),
        )

        PriceTagRequestService.transition_status(
            db_only, request.id, "proof_ready", user_id=seed.MARKETER_ID
        )

        assert seen, "the notifier was never called"
        assert seen[0] >= 1, (
            "the notifier ran before any commit, so it can report a "
            "transition that a later rollback throws away"
        )


# ---------------------------------------------------------------------------
# S6 (PLAN-price-tag-ai-extract-resolver.md D10) - price_tag_update is a
# configurable use case, like every other status-update template.
# ---------------------------------------------------------------------------


def _seed_one_param_template(db, *, status: str = "approved"):
    from app.models.respond_template import RespondChannel, RespondMessageTemplate
    from app.models.respond_workspace import RespondWorkspace

    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=unique_code("space"),
        name="ZZT workspace",
        api_key_ciphertext="not-encrypted-test",
        is_active=True,
        is_default=True,
    )
    db.add(ws)
    db.flush()
    ch = RespondChannel(
        id=str(uuid.uuid4()), workspace_id=ws.id, respond_channel_id=453209
    )
    db.add(ch)
    db.flush()
    tpl = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        respond_template_id=1,
        name=unique_code("ptag-update"),
        language_code="en",
        status=status,
        components=[{"type": "body", "text": "{{1}}"}],
        body_text="{{1}}",
        param_count=1,
    )
    db.add(tpl)
    db.commit()
    return tpl


class TestPriceTagUpdateIsConfigurable:
    def test_ac_s6_1_the_use_case_is_offered_to_admins(self):
        """AC-S6-1: joins TEMPLATE_DEFAULT_USE_CASES."""
        from app.models.respond_template import TEMPLATE_DEFAULT_USE_CASES

        assert "price_tag_update" in TEMPLATE_DEFAULT_USE_CASES

    def test_ac_s6_1_get_defaults_lists_a_row_for_it(self, db_only):
        from app.services import respond_template_service as svc

        rows = svc.get_defaults(db_only)
        assert any(r["use_case"] == "price_tag_update" for r in rows), rows

    def test_ac_s6_1_set_default_accepts_a_one_param_template_mapped_to_message(
        self, db_only
    ):
        from app.services import respond_template_service as svc

        tpl = _seed_one_param_template(db_only)

        out = svc.set_default(
            db_only,
            "price_tag_update",
            template_id=str(tpl.id),
            param_mapping={"1": "message"},
        )
        assert out["is_valid"] is True
        assert out["param_mapping"] == {"1": "message"}

    def test_ac_s6_2_build_context_vars_resolves_the_request(self, db_only):
        """AC-S6-2: entity_number, status and a non-empty portal_url.

        Calls the REAL `build_context_vars` (captured before the file's
        `no_respond` autouse fixture fakes it to `{}` for every other test
        here) so this actually proves the new branch, not the fake.
        """
        request = _request_at(db_only, "proof_ready")

        vars_out = _REAL_BUILD_CONTEXT_VARS(
            db_only,
            use_case="price_tag_update",
            business_id=str(request.id),
            identifier=str(request.contact_id),
        )

        assert vars_out.get("entity_number") == request.doc_number
        assert vars_out.get("status") == request.status
        assert vars_out.get("portal_url"), "expected a non-empty portal_url"

    def test_ac_s6_3_notify_salesperson_sends_the_template_with_entity_number_in_context(
        self, db_only, monkeypatch
    ):
        """AC-S6-3: the send_text_or_template spy sees use_case=price_tag_update
        and context vars carrying entity_number; a success row is logged.

        `build_context_vars` is restored to the real implementation for this
        one test (still via the spy below, never a real Respond.io call) so
        the context it builds is the real price_tag_update branch's output,
        not the file's default `{}` fake.
        """
        from app.models.integration import IntegrationLog
        from app.services import price_tag_notify

        monkeypatch.setattr(_rms, "build_context_vars", _REAL_BUILD_CONTEXT_VARS)

        sent: list[dict] = []

        def _fake_send(db, *, identifier, text, use_case, context_vars=None, **_kw):
            sent.append(
                {"identifier": identifier, "use_case": use_case, "context_vars": context_vars}
            )
            return {
                "sent_as": "template",
                "response": {"id": "zzt-msg-tpl"},
                "window_state": "closed",
                "request_payload": {"message": {"type": "whatsapp_template"}},
            }

        monkeypatch.setattr(_rms, "send_text_or_template", _fake_send)

        request = _request_at(db_only, "proof_ready")

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")
        db_only.commit()

        assert len(sent) == 1, "notify_salesperson did not reach send_text_or_template"
        assert sent[0]["use_case"] == "price_tag_update"
        assert sent[0]["context_vars"] is not None
        assert sent[0]["context_vars"].get("entity_number") == request.doc_number

        rows = (
            db_only.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "price_tag_requests")
            .filter(IntegrationLog.business_id == str(request.id))
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "success"


# ---------------------------------------------------------------------------
# S7 (PLAN-price-tag-ai-extract-resolver.md D11) - the send is addressed and
# logged by respond_io_id, not by the internal RespondContact uuid.
# ---------------------------------------------------------------------------


def _seed_contact_with_respond_id(db, *, respond_io_id: str | None):
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("ZZT Sales Sam"),
        respond_io_id=respond_io_id,
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    return contact


def _outbox_client(db, monkeypatch) -> TestClient:
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.models.user import User, UserStatus
    from app.services.user_service import UserPermissionService

    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('outbox')}@zzt.test",
        name="ZZT Outbox Caller",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    principal = {"id": str(user.id), "email": user.email}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    # The system router is wrapped in `require_module_enabled_with_api_key`,
    # which depends on THIS, not `get_current_user` - unoverridden it 401s
    # before `require_permission` (system.respond_outbox.view) even runs.
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == "system.respond_outbox.view",
    )
    return TestClient(app)


class TestSendIsAddressedByRespondIoId:
    def test_ac_s7_1_send_and_both_log_rows_use_the_respond_io_id(
        self, db_only, monkeypatch
    ):
        from app.models.integration import IntegrationLog
        from app.services import price_tag_notify

        RESPOND_ID = "900000008"
        contact = _seed_contact_with_respond_id(db_only, respond_io_id=RESPOND_ID)
        product = seed.seed_product(db_only)
        request = seed.seed_request(
            db_only, contact.id, status="proof_ready", products=[product]
        )

        sent: list[dict] = []
        monkeypatch.setattr(
            _rms,
            "send_text_or_template",
            lambda db, *, identifier, text, use_case, context_vars=None, **_kw: (
                sent.append({"identifier": identifier})
                or {
                    "sent_as": "text",
                    "response": {"id": "zzt-msg-1"},
                    "window_state": "open",
                    "request_payload": {"message": {"type": "text", "text": text}},
                }
            ),
        )

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")
        db_only.commit()

        assert sent and sent[0]["identifier"] == RESPOND_ID, (
            "the send must be addressed by respond_io_id, not the internal "
            "RespondContact uuid"
        )

        rows = (
            db_only.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "price_tag_requests")
            .filter(IntegrationLog.business_id == str(request.id))
            .all()
        )
        assert len(rows) == 1
        assert rows[0].external_reference == RESPOND_ID
        assert rows[0].endpoint.endswith(f"contact/id:{RESPOND_ID}/message")

    def test_ac_s7_1_a_failed_send_still_logs_the_respond_io_id(
        self, db_only, monkeypatch
    ):
        from app.models.integration import IntegrationLog
        from app.services import price_tag_notify

        RESPOND_ID = "900000008"
        contact = _seed_contact_with_respond_id(db_only, respond_io_id=RESPOND_ID)
        product = seed.seed_product(db_only)
        request = seed.seed_request(
            db_only, contact.id, status="proof_ready", products=[product]
        )

        def _boom(db, *, identifier, text, use_case, context_vars=None, **_kw):
            raise RuntimeError("ZZT respond down")

        monkeypatch.setattr(_rms, "send_text_or_template", _boom)

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")
        db_only.commit()

        rows = (
            db_only.query(IntegrationLog)
            .filter(IntegrationLog.business_table == "price_tag_requests")
            .filter(IntegrationLog.business_id == str(request.id))
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].external_reference == RESPOND_ID
        assert rows[0].endpoint.endswith(f"contact/id:{RESPOND_ID}/message")

    def test_ac_s7_2_a_contact_with_no_respond_io_id_falls_back_to_the_contact_id(
        self, db_only, monkeypatch
    ):
        """Regression: today's behaviour, unchanged by D11."""
        from app.services import price_tag_notify

        contact = _seed_contact_with_respond_id(db_only, respond_io_id=None)
        product = seed.seed_product(db_only)
        request = seed.seed_request(
            db_only, contact.id, status="proof_ready", products=[product]
        )

        sent: list[dict] = []
        monkeypatch.setattr(
            _rms,
            "send_text_or_template",
            lambda db, *, identifier, text, use_case, context_vars=None, **_kw: (
                sent.append({"identifier": identifier})
                or {
                    "sent_as": "text",
                    "response": {},
                    "window_state": "open",
                    "request_payload": {},
                }
            ),
        )

        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")

        assert sent and sent[0]["identifier"] == str(contact.id)

    def test_ac_s7_3_the_outbox_shows_the_contact_name_and_phone(
        self, db_only, monkeypatch
    ):
        from app.services import price_tag_notify

        RESPOND_ID = "900000008"
        contact = _seed_contact_with_respond_id(db_only, respond_io_id=RESPOND_ID)
        product = seed.seed_product(db_only)
        request = seed.seed_request(
            db_only, contact.id, status="proof_ready", products=[product]
        )

        monkeypatch.setattr(
            _rms,
            "send_text_or_template",
            lambda db, *, identifier, text, use_case, context_vars=None, **_kw: {
                "sent_as": "text",
                "response": {},
                "window_state": "open",
                "request_payload": {"message": {"type": "text", "text": text}},
            },
        )
        price_tag_notify.notify_salesperson(db_only, request, "proof_ready")
        db_only.commit()

        client = _outbox_client(db_only, monkeypatch)
        try:
            resp = client.get(
                "/api/v1/system/respond-outbox",
                params={"business_table": "price_tag_requests"},
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()["data"]
            assert len(rows) == 1, rows
            row = rows[0]
            assert row["contact_name"] == contact.name
            assert row["contact_phone"] == contact.phone_number
            assert request.doc_number in (row["message_text"] or "")
        finally:
            app.dependency_overrides.clear()
