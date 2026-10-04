"""``?contacts=`` on the SSE stream is a request, not a grant.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-K1/K2 (the drawer names the contact it has open, and hears only that)

The stream took the client's contact list verbatim into its server-side filter.
Respond contact ids are short numeric strings, so a caller could name any of
them and be told, live, WHENEVER that contact receives a message - the timing of
a stranger's conversation, without ever being able to read it. The frames carry
no content, which is why this is a leak rather than a breach, but "when is this
person messaging us" is exactly the sort of thing a competitor's ex-employee
would like.

The requested ids are now intersected with the ones the caller has ticket
standing for; an admin keeps the lot.

Run:
    venv/bin/pytest tests/test_conversation_events_contact_scope.py -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.api.v1.sla import conversation_events
from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

MINE_PHONE = "+60123456789"
MINE_RESPOND_ID = "10025531"
THEIRS_PHONE = "+60120000321"
THEIRS_RESPOND_ID = "10025532"
NEVER_HEARD_OF = "999888777"

_ADMINS: set[str] = set()


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _roles(monkeypatch):
    _ADMINS.clear()
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) in _ADMINS else set(),
    )
    yield
    _ADMINS.clear()


def _seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="ZZT-NORMAL", name="ZZT Normal"))
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
    for phone, rid, name in (
        (MINE_PHONE, MINE_RESPOND_ID, "Aisyah Rahman"),
        (THEIRS_PHONE, THEIRS_RESPOND_ID, "Someone Elses Customer"),
    ):
        db.add(
            RespondContact(
                id=str(uuid.uuid4()),
                phone_number=phone,
                name=name,
                respond_io_id=rid,
                session_vars={},
            )
        )
    me = str(uuid.uuid4())
    them = str(uuid.uuid4())
    admin = str(uuid.uuid4())
    db.add(User(id=me, email="zzt-me@test.com", name="Agent One"))
    db.add(User(id=them, email="zzt-them@test.com", name="Agent Two"))
    db.add(User(id=admin, email="zzt-admin@test.com", name="An Admin"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CS_AGENT", name="ZZT CS Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_cs_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    service = ConversationSLATrackingService(db)
    for phone, owner in ((MINE_PHONE, me), (THEIRS_PHONE, them)):
        service.create_tracking(
            ConversationSLATrackingCreate(
                agent_code="ZZT_CS_AGENT",
                team_set_code="zzt_cs_general",
                policy_id=policy_id,
                assigned_to_id=owner,
                contact_phone_number=phone,
                source_message_id=f"wamid.{phone}",
                source_message_text="Please connect me to a person.",
            )
        )
    return {"me": me, "them": them, "admin": admin, "policy_id": policy_id}


def test_a_contact_i_hold_a_ticket_for_is_kept(db):
    seed = _seed(db)

    allowed = conversation_events.entitled_contacts(
        db, seed["me"], {MINE_RESPOND_ID}
    )

    assert allowed == {MINE_RESPOND_ID}


def test_someone_elses_contact_is_dropped_silently(db):
    """Dropped, not refused: a 403 on an id would confirm the contact exists,
    and the drawer has nothing useful to do with the error either way."""
    seed = _seed(db)

    allowed = conversation_events.entitled_contacts(
        db, seed["me"], {MINE_RESPOND_ID, THEIRS_RESPOND_ID}
    )

    assert allowed == {MINE_RESPOND_ID}


def test_an_id_with_no_ticket_at_all_is_dropped(db):
    seed = _seed(db)

    allowed = conversation_events.entitled_contacts(
        db, seed["me"], {NEVER_HEARD_OF}
    )

    assert allowed == set()


def test_an_admin_keeps_every_id_they_asked_for(db):
    seed = _seed(db)
    _ADMINS.add(seed["admin"])

    allowed = conversation_events.entitled_contacts(
        db, seed["admin"], {MINE_RESPOND_ID, THEIRS_RESPOND_ID, NEVER_HEARD_OF}
    )

    assert allowed == {MINE_RESPOND_ID, THEIRS_RESPOND_ID, NEVER_HEARD_OF}


def test_a_resolved_ticket_still_entitles_its_resolver(db):
    """AC-M1 keeps the just-resolved drawer open, and it stays live while it is
    open. Resolve NULLs the assignee, so this only works because read scope
    follows resolved_by."""
    from app.schemas.sla import ConversationSLATrackingUpdate

    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    tracking = service.get_tracking_by_contact_phone(MINE_PHONE)
    service.update_tracking(
        str(tracking.id),
        ConversationSLATrackingUpdate(is_resolved=True, resolved_by=seed["me"]),
    )
    db.expire_all()

    assert conversation_events.entitled_contacts(db, seed["me"], {MINE_RESPOND_ID}) == {
        MINE_RESPOND_ID
    }
    assert conversation_events.entitled_contacts(
        db, seed["them"], {MINE_RESPOND_ID}
    ) == set()


def test_an_empty_request_stays_empty(db):
    seed = _seed(db)

    assert conversation_events.entitled_contacts(db, seed["me"], set()) == set()


def test_a_missing_session_grants_nothing(db):
    """Fail closed. The filter is the only thing standing between a guessed id
    and a live feed of when that contact messages us."""
    seed = _seed(db)

    assert conversation_events.entitled_contacts(None, seed["me"], {MINE_RESPOND_ID}) == set()


# --------------------------------------------------------------------------- #
# The stream's own dependency, which is what actually builds the filter set    #
# --------------------------------------------------------------------------- #


def test_the_stream_dependency_only_hands_the_generator_the_allowed_ids(db):
    """Asserted on `_stream_scope` rather than over the wire: the endpoint never
    ends, so TestClient (which buffers the whole body) cannot exercise it, and
    the transport already has its own real-server suite."""
    import asyncio

    seed = _seed(db)

    user_id, allowed = asyncio.run(
        conversation_events._stream_scope(
            contacts=f"{MINE_RESPOND_ID},{THEIRS_RESPOND_ID},{NEVER_HEARD_OF}",
            current_user={"id": seed["me"]},
            db=db,
        )
    )

    assert user_id == seed["me"]
    assert allowed == {MINE_RESPOND_ID}


def test_the_stream_dependency_caps_the_requested_list(db):
    """A client cannot pin an unbounded contact list on the server-side filter."""
    import asyncio

    seed = _seed(db)
    _ADMINS.add(seed["admin"])
    many = ",".join(str(900000 + i) for i in range(conversation_events.MAX_CONTACTS + 10))

    _uid, allowed = asyncio.run(
        conversation_events._stream_scope(
            contacts=many, current_user={"id": seed["admin"]}, db=db
        )
    )

    assert len(allowed) == conversation_events.MAX_CONTACTS
