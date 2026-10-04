"""Conversation intervention tickets - creation identity + multi-open (S2a).

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     section A (AC-A1 .. AC-A4)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S2.1, S2.2, S2.9)

What changes here, and why the older singleton tests move with it: a conversation
SLA row stops being "the open conversation with this contact" and becomes "this
enquiry". Identity is the triggering message (`source_message_id`), so the same
contact can hold several open tickets at once, and an n8n retry of the SAME
trigger message is the only thing that must not create a second row.

Run with:
    pytest tests/test_conversation_intervention_ticket_create.py -v
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, time, timezone
from typing import Set
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import (
    ConversationSLAEventLog,
    ConversationSLATracking,
    SLAPolicy,
    SLAPolicyTier,
)
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session, pg_empty_schema

PHONE = "+60123456789"
KL = ZoneInfo("Asia/Kuala_Lumpur")

# The migration under test (alembic/versions is not an importable package).
_MIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic",
    "versions",
    "321_ticket_source_message_id.py",
)
_spec = importlib.util.spec_from_file_location("mig_321", _MIG_PATH)
mig321 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig321)

_MIG_323_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic",
    "versions",
    "323_ticket_source_message_contact_scope.py",
)
_spec_323 = importlib.util.spec_from_file_location("mig_323", _MIG_323_PATH)
mig323 = importlib.util.module_from_spec(_spec_323)
_spec_323.loader.exec_module(mig323)

_MIG_324_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "alembic",
    "versions",
    "324_ticket_source_message_restore.py",
)
_spec_324 = importlib.util.spec_from_file_location("mig_324", _MIG_324_PATH)
mig324 = importlib.util.module_from_spec(_spec_324)
_spec_324.loader.exec_module(mig324)

# Index names owned by this slice.
OLD_CONTACT_SINGLETON_INDEX = "uq_conversation_sla_tracking_active_conversation_per_contact"
NEW_SOURCE_MESSAGE_INDEX = "uq_conversation_sla_tracking_open_source_message"
CONTACT_SOURCE_MESSAGE_INDEX = "uq_conversation_sla_tracking_open_contact_source_message"


@pytest.fixture
def db():
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


def _seed(db) -> dict:
    """Every FK this create path touches, seeded by the test (CI's DB is empty)."""
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="NORMAL", name="Normal"))
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
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id, phone_number=PHONE, name="Test Contact", session_vars={}
        )
    )
    user_one = str(uuid.uuid4())
    user_two = str(uuid.uuid4())
    db.add(User(id=user_one, email="agent1@test.com", name="Agent One", respond_user_id="900001"))
    db.add(User(id=user_two, email="agent2@test.com", name="Agent Two", respond_user_id="900002"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="AGENT1", name="Agent 1"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Team 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "user_id": user_one,
        "user_two": user_two,
        "agent_code": "AGENT1",
        "team_set_code": "general",
    }


def _payload(
    seed,
    *,
    source_message_id=None,
    message_id=None,
    assigned_to_id=None,
) -> ConversationSLATrackingCreate:
    return ConversationSLATrackingCreate(
        agent_code=seed["agent_code"],
        team_set_code=seed["team_set_code"],
        policy_id=seed["policy_id"],
        current_tier=1,
        assigned_to_id=assigned_to_id or seed["user_id"],
        contact_phone_number=PHONE,
        message_id=message_id,
        source_message_id=source_message_id,
    )


def _open_rows(db, contact_id: str) -> list[ConversationSLATracking]:
    return (
        db.query(ConversationSLATracking)
        .filter(
            ConversationSLATracking.respond_contact_id == contact_id,
            ConversationSLATracking.is_resolved.is_(False),
        )
        .all()
    )


def _assign_logs(db, tracking_id: str) -> list[ConversationSLAEventLog]:
    return (
        db.query(ConversationSLAEventLog)
        .filter(
            ConversationSLAEventLog.sla_tracking_id == str(tracking_id),
            ConversationSLAEventLog.event_type == "assign",
        )
        .all()
    )


# ---------------------------------------------------------------------------
# AC-A1 - multi-open: one ticket per enquiry, never merged by contact
# ---------------------------------------------------------------------------

def test_new_source_message_id_opens_a_second_ticket_for_the_same_contact(db):
    """The bug this feature exists for: the second enquiry used to be swallowed."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))
    second = service.create_tracking(
        _payload(seed, source_message_id="msg-2", assigned_to_id=seed["user_two"])
    )

    assert str(second.id) != str(first.id)
    assert bool(getattr(second, "_already_active", False)) is False
    rows = _open_rows(db, seed["contact_id"])
    assert len(rows) == 2
    assert {r.source_message_id for r in rows} == {"msg-1", "msg-2"}
    assert {str(r.assigned_to_id) for r in rows} == {seed["user_id"], seed["user_two"]}


def test_second_ticket_for_the_same_assignee_is_still_its_own_row(db):
    """Routing back to the SAME person is still a distinct enquiry."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))
    second = service.create_tracking(_payload(seed, source_message_id="msg-2"))

    assert str(second.id) != str(first.id)
    assert len(_open_rows(db, seed["contact_id"])) == 2


def test_each_ticket_carries_its_own_clocks_and_assign_log(db):
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))
    second = service.create_tracking(_payload(seed, source_message_id="msg-2"))

    for row in (first, second):
        assert row.due_at is not None
        assert row.due_at_resolution is not None
        assert row.is_responded is False
        assert len(_assign_logs(db, row.id)) == 1


def test_resolving_one_ticket_leaves_its_sibling_open(db):
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))
    second = service.create_tracking(_payload(seed, source_message_id="msg-2"))

    first.is_resolved = True
    first.resolved_at = datetime(2026, 6, 2, 10, 0, 0)
    db.commit()

    open_rows = _open_rows(db, seed["contact_id"])
    assert [str(r.id) for r in open_rows] == [str(second.id)]


def test_a_resolved_ticket_is_never_overwritten_by_a_new_enquiry(db):
    """Overwrite-in-place existed only to satisfy the contact singleton. With
    per-enquiry identity a closed ticket is history and keeps its resolution."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))
    first_id = str(first.id)
    first.is_resolved = True
    first.resolved_at = datetime(2026, 6, 2, 10, 0, 0)
    db.commit()

    second = service.create_tracking(_payload(seed, source_message_id="msg-2"))

    assert str(second.id) != first_id
    db.refresh(first)
    assert first.is_resolved is True
    assert first.resolved_at == datetime(2026, 6, 2, 10, 0, 0)
    assert first.source_message_id == "msg-1"


# ---------------------------------------------------------------------------
# AC-A2 - idempotency is keyed on the triggering message, and refreshes nothing
# ---------------------------------------------------------------------------

def test_repeat_source_message_id_returns_the_same_row_flagged_already_active(db):
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))

    second = service.create_tracking(_payload(seed, source_message_id="msg-1"))

    assert str(second.id) == str(first.id)
    assert bool(getattr(second, "_already_active", False)) is True
    assert len(_open_rows(db, seed["contact_id"])) == 1


def test_repeat_source_message_id_refreshes_nothing(db):
    """A retry must not move the clocks, the assignee, or message_id."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    first = service.create_tracking(
        _payload(seed, source_message_id="msg-1", message_id=111)
    )
    started, due, due_res = (
        first.current_tier_started_at,
        first.due_at,
        first.due_at_resolution,
    )

    second = service.create_tracking(
        _payload(
            seed,
            source_message_id="msg-1",
            message_id=222,
            assigned_to_id=seed["user_two"],
        )
    )

    assert second.message_id == 111
    assert str(second.assigned_to_id) == seed["user_id"]
    assert second.current_tier_started_at == started
    assert second.due_at == due
    assert second.due_at_resolution == due_res
    assert second.is_resolved is False


def test_repeat_source_message_id_writes_no_second_assign_log(db):
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    first = service.create_tracking(_payload(seed, source_message_id="msg-1"))

    service.create_tracking(_payload(seed, source_message_id="msg-1"))

    assert len(_assign_logs(db, first.id)) == 1


def test_legacy_payload_uses_message_id_as_the_identity_key(db):
    """n8n today posts `message_id` only. Deploy order is BE before n8n, so the
    old payload must keep its idempotency without a `source_message_id`."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed, message_id=555))
    assert first.source_message_id == "555"

    second = service.create_tracking(_payload(seed, message_id=555))
    assert str(second.id) == str(first.id)
    assert bool(getattr(second, "_already_active", False)) is True

    third = service.create_tracking(_payload(seed, message_id=556))
    assert str(third.id) != str(first.id)
    assert len(_open_rows(db, seed["contact_id"])) == 2


def test_payload_with_no_message_identity_keeps_the_contact_singleton(db):
    """No trigger message = no per-enquiry identity, so the legacy contact-level
    idempotency stands rather than opening a ticket per call."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed))
    second = service.create_tracking(_payload(seed))

    assert str(second.id) == str(first.id)
    assert bool(getattr(second, "_already_active", False)) is True
    assert len(_open_rows(db, seed["contact_id"])) == 1


# ---------------------------------------------------------------------------
# AC-A2 under concurrency: the pre-query is not the lock, the index is
# ---------------------------------------------------------------------------


def _blind_first_scope_call(monkeypatch):
    """Make the NEXT conversation-scope query match nothing, then behave normally.

    That is exactly what a concurrent create sees: its idempotency pre-query
    runs BEFORE the other request's INSERT commits, so it finds no open ticket
    for this (contact, trigger message) and proceeds to insert its own.
    """
    import app.services.sla_service as sla_mod

    real_scope = sla_mod.conversation_tracking_scope
    calls = {"n": 0}

    def _scope():
        calls["n"] += 1
        if calls["n"] == 1:
            # A predicate no row can satisfy - the pre-query comes back empty.
            return ConversationSLATracking.id.is_(None)
        return real_scope()

    monkeypatch.setattr(sla_mod, "conversation_tracking_scope", _scope)


def test_losing_the_index_race_hands_back_the_winners_ticket(db, monkeypatch):
    """Two deliveries of the SAME trigger message arrive at once: both pre-queries
    miss, and the loser's INSERT hits the partial unique index. That conflict is
    the idempotency question asked a second time, not a server error - the loser
    must get the winner's ticket back flagged already_active. A 500 here would
    have n8n retry into the same race and, worse, report a failed intervention
    for a ticket that exists."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    winner = service.create_tracking(_payload(seed, source_message_id="msg-race"))

    _blind_first_scope_call(monkeypatch)
    loser = service.create_tracking(_payload(seed, source_message_id="msg-race"))

    assert str(loser.id) == str(winner.id)
    assert bool(getattr(loser, "_already_active", False)) is True
    assert getattr(loser, "_in_working_hours", None) is not None
    assert len(_open_rows(db, seed["contact_id"])) == 1
    # The winner already owns the assign log; losing the race writes no second one.
    assert len(_assign_logs(db, winner.id)) == 1


def test_an_integrity_error_that_is_not_the_identity_race_still_raises(db, monkeypatch):
    """The recovery is scoped to "somebody else already opened this exact ticket".
    A conflict with nothing to hand back (here: a bad policy FK) must not be
    swallowed into a silent success."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    bad = _payload(seed, source_message_id="msg-broken")
    bad.policy_id = seed["policy_id"]
    monkeypatch.setattr(
        service,
        "_write_assign_event_log",
        lambda *a, **k: None,
    )
    # Point the row at a policy that does not exist, AFTER the service's own
    # policy lookup, by breaking the FK at flush time.
    real_add = service.db.add

    def _add(obj):
        if isinstance(obj, ConversationSLATracking):
            obj.policy_id = str(uuid.uuid4())
        return real_add(obj)

    monkeypatch.setattr(service.db, "add", _add)

    with pytest.raises(IntegrityError):
        service.create_tracking(bad)
    db.rollback()


def test_source_message_id_is_matched_only_within_the_conversation_family(db):
    """A form-SLA stage row carrying the same message must not be returned as the
    idempotent hit (AC-F3: the two families share this table)."""
    seed = _seed(db)
    form_row = ConversationSLATracking(
        id=str(uuid.uuid4()),
        policy_id=seed["policy_id"],
        current_tier=1,
        due_at=datetime(2026, 6, 1, 12, 0, 0),
        is_resolved=False,
        respond_contact_id=seed["contact_id"],
        source_entity_type="complaint",
        source_entity_id=str(uuid.uuid4()),
        source_message_id="msg-1",
    )
    db.add(form_row)
    db.commit()

    ticket = ConversationSLATrackingService(db).create_tracking(
        _payload(seed, source_message_id="msg-1")
    )

    assert str(ticket.id) != str(form_row.id)
    assert bool(getattr(ticket, "_already_active", False)) is False
    db.refresh(form_row)
    assert form_row.source_entity_type == "complaint"
    assert form_row.is_resolved is False


# ---------------------------------------------------------------------------
# AC-A3 - the index swap, enforced by the database
# ---------------------------------------------------------------------------

def _raw_ticket(db, seed, *, source_message_id, is_resolved=False, source_entity_type=None):
    row = ConversationSLATracking(
        id=str(uuid.uuid4()),
        policy_id=seed["policy_id"],
        current_tier=1,
        due_at=datetime(2026, 6, 1, 12, 0, 0),
        is_resolved=is_resolved,
        respond_contact_id=seed["contact_id"],
        source_message_id=source_message_id,
        source_entity_type=source_entity_type,
        source_entity_id=str(uuid.uuid4()) if source_entity_type else None,
    )
    db.add(row)
    return row


def test_two_open_tickets_with_the_same_source_message_id_are_rejected(db):
    seed = _seed(db)
    _raw_ticket(db, seed, source_message_id="dupe")
    db.commit()
    _raw_ticket(db, seed, source_message_id="dupe")

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_the_same_source_message_id_is_allowed_once_the_first_is_resolved(db):
    seed = _seed(db)
    _raw_ticket(db, seed, source_message_id="dupe", is_resolved=True)
    db.commit()
    _raw_ticket(db, seed, source_message_id="dupe")
    db.commit()  # must not raise

    assert len(_open_rows(db, seed["contact_id"])) == 1


def test_form_sla_rows_are_outside_the_source_message_index(db):
    seed = _seed(db)
    _raw_ticket(db, seed, source_message_id="dupe", source_entity_type="complaint")
    _raw_ticket(db, seed, source_message_id="dupe", source_entity_type="stock_inquiry")
    db.commit()  # must not raise

    rows = (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.source_message_id == "dupe")
        .all()
    )
    assert len(rows) == 2


def test_two_open_tickets_for_one_contact_are_allowed_by_the_database(db):
    """The migration-180 contact singleton is gone; nothing at the DB level may
    stop a contact holding several open enquiries."""
    seed = _seed(db)
    _raw_ticket(db, seed, source_message_id="a")
    _raw_ticket(db, seed, source_message_id="b")
    db.commit()  # must not raise

    assert len(_open_rows(db, seed["contact_id"])) == 2


# ---------------------------------------------------------------------------
# FINDING 6 (code review): a ticket's identity is (contact, trigger message),
# not the message alone. WhatsApp message ids are not guaranteed globally
# unique across different contacts/threads, so a bare source_message_id
# lookup/index can hand contact B "already_active" pointing at contact A's
# ticket on a coincidental id collision.
# ---------------------------------------------------------------------------

OTHER_PHONE = "+60120000988"


def _second_contact(db, seed) -> str:
    other_contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=other_contact_id, phone_number=OTHER_PHONE, name="Other Contact", session_vars={}
        )
    )
    db.commit()
    return other_contact_id


def test_colliding_source_message_id_across_contacts_creates_two_tickets(db):
    """The exact bug: contact B's create call must get its OWN ticket, never
    contact A's, just because their trigger messages happen to share an id."""
    seed = _seed(db)
    _second_contact(db, seed)
    service = ConversationSLATrackingService(db)

    contact_a_ticket = service.create_tracking(_payload(seed, source_message_id="collide"))
    contact_b_ticket = service.create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            current_tier=1,
            assigned_to_id=seed["user_two"],
            contact_phone_number=OTHER_PHONE,
            source_message_id="collide",
        )
    )

    assert str(contact_b_ticket.id) != str(contact_a_ticket.id), (
        "a colliding source_message_id must never hand back ANOTHER contact's ticket"
    )
    assert bool(getattr(contact_b_ticket, "_already_active", False)) is False
    assert str(contact_b_ticket.respond_contact_id) != str(contact_a_ticket.respond_contact_id)
    assert len(_open_rows(db, seed["contact_id"])) == 1
    assert len(_open_rows(db, str(contact_b_ticket.respond_contact_id))) == 1


def test_two_open_tickets_with_the_same_message_id_but_different_contacts_are_allowed_by_the_database(
    db,
):
    seed = _seed(db)
    other_contact_id = _second_contact(db, seed)

    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=seed["policy_id"],
            current_tier=1,
            due_at=datetime(2026, 6, 1, 12, 0, 0),
            is_resolved=False,
            respond_contact_id=seed["contact_id"],
            source_message_id="collide",
        )
    )
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=seed["policy_id"],
            current_tier=1,
            due_at=datetime(2026, 6, 1, 12, 0, 0),
            is_resolved=False,
            respond_contact_id=other_contact_id,
            source_message_id="collide",
        )
    )
    db.commit()  # must not raise - different contacts, same message id

    assert len(_open_rows(db, seed["contact_id"])) == 1
    assert len(_open_rows(db, other_contact_id)) == 1


def test_migration_backfills_the_column_and_swaps_the_indexes():
    """Run the real migration body against a pre-migration schema holding rows.

    Its own scratch schema, never the shared blank one: this test performs DDL,
    and mutating the shared schema would leak into every later test in the run.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with pg_empty_schema([ConversationSLATracking.__table__]) as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        conn = session.connection()
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')

        # Rewind to the pre-migration shape: no source_message_id, and the
        # migration-180 contact singleton in place.
        conn.exec_driver_sql(
            "ALTER TABLE conversation_sla_tracking DROP COLUMN IF EXISTS source_message_id"
        )
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {NEW_SOURCE_MESSAGE_INDEX}")
        conn.exec_driver_sql(
            f"""
            CREATE UNIQUE INDEX {OLD_CONTACT_SINGLETON_INDEX}
            ON conversation_sla_tracking (respond_contact_id)
            WHERE respond_contact_id IS NOT NULL
              AND is_resolved = false
              AND (source_entity_type IS NULL OR source_entity_type = 'conversation')
            """
        )

        policy_id = str(uuid.uuid4())
        conn.exec_driver_sql(
            "INSERT INTO sla_policies (id, code, name, is_active) VALUES (%s, %s, %s, %s)",
            (policy_id, "ZZT-MIG", "Migration policy", True),
        )
        open_id, resolved_id = str(uuid.uuid4()), str(uuid.uuid4())
        for row_id, resolved, message_id in (
            (open_id, False, 999),
            (resolved_id, True, 888),
        ):
            conn.exec_driver_sql(
                """
                INSERT INTO conversation_sla_tracking
                    (id, policy_id, current_tier, due_at, is_responded, is_resolved,
                     synced_to_excel, message_id)
                VALUES (%s, %s, 1, now(), false, %s, false, %s)
                """,
                (row_id, policy_id, resolved, message_id),
            )

        with Operations.context(MigrationContext.configure(conn)):
            mig321.upgrade()

        # The driver hands back native uuid.UUID objects for a UUID column, not
        # str, so the lookup keys are stringified to match open_id/resolved_id.
        backfilled = {
            str(row_id): source_message_id
            for row_id, source_message_id in conn.exec_driver_sql(
                "SELECT id, source_message_id FROM conversation_sla_tracking"
            ).fetchall()
        }
        assert backfilled[open_id] == "999"
        assert backfilled[resolved_id] == "888"

        indexes = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'conversation_sla_tracking' AND schemaname = %s",
                (schema,),
            ).fetchall()
        }
        assert OLD_CONTACT_SINGLETON_INDEX not in indexes
        assert NEW_SOURCE_MESSAGE_INDEX in indexes

        # Pre-existing rows stay readable and resolvable.
        conn.exec_driver_sql(
            "UPDATE conversation_sla_tracking SET is_resolved = true WHERE id = %s",
            (open_id,),
        )
        still_there = conn.exec_driver_sql(
            "SELECT count(*) FROM conversation_sla_tracking"
        ).scalar()
        assert still_there == 2


def test_migration_323_swaps_to_the_contact_scoped_index():
    """FINDING 6: run the real 323 migration body against a schema still on
    321's shape (source_message_id alone) and confirm the swap - the OLD
    index is gone, the NEW (contact, message) index is in place, and it
    actually permits what 321 wrongly forbade: two different contacts
    sharing a colliding source_message_id.

    Its own scratch schema (DDL test), never the shared blank one.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with pg_empty_schema([ConversationSLATracking.__table__]) as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        conn = session.connection()
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')

        # Rewind create_all's current (post-323) index to 321's shape.
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {CONTACT_SOURCE_MESSAGE_INDEX}")
        conn.exec_driver_sql(
            f"""
            CREATE UNIQUE INDEX {NEW_SOURCE_MESSAGE_INDEX}
            ON conversation_sla_tracking (source_message_id)
            WHERE source_message_id IS NOT NULL
              AND is_resolved = false
              AND (source_entity_type IS NULL OR source_entity_type = 'conversation')
            """
        )

        with Operations.context(MigrationContext.configure(conn)):
            mig323.upgrade()

        indexes = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'conversation_sla_tracking' AND schemaname = %s",
                (schema,),
            ).fetchall()
        }
        assert NEW_SOURCE_MESSAGE_INDEX not in indexes
        assert CONTACT_SOURCE_MESSAGE_INDEX in indexes

        policy_id = str(uuid.uuid4())
        conn.exec_driver_sql(
            "INSERT INTO sla_policies (id, code, name, is_active) VALUES (%s, %s, %s, %s)",
            (policy_id, "ZZT-MIG323", "Migration 323 policy", True),
        )
        contact_a, contact_b = str(uuid.uuid4()), str(uuid.uuid4())
        for contact_id in (contact_a, contact_b):
            conn.exec_driver_sql(
                "INSERT INTO respond_contacts (id, phone_number, session_vars) "
                "VALUES (%s, %s, %s)",
                (contact_id, f"+601{contact_id[:8]}", "{}"),
            )
        for contact_id in (contact_a, contact_b):
            conn.exec_driver_sql(
                """
                INSERT INTO conversation_sla_tracking
                    (id, policy_id, current_tier, due_at, is_responded, is_resolved,
                     synced_to_excel, respond_contact_id, source_message_id)
                VALUES (%s, %s, 1, now(), false, false, false, %s, %s)
                """,
                (str(uuid.uuid4()), policy_id, contact_id, "collide"),
            )  # must not raise - post-323, different contacts share the id fine

        with pytest.raises(Exception):
            conn.exec_driver_sql(
                """
                INSERT INTO conversation_sla_tracking
                    (id, policy_id, current_tier, due_at, is_responded, is_resolved,
                     synced_to_excel, respond_contact_id, source_message_id)
                VALUES (%s, %s, 1, now(), false, false, false, %s, %s)
                """,
                (str(uuid.uuid4()), policy_id, contact_a, "collide"),
            )


def test_migration_324_restores_identity_321_deduped_across_contacts():
    """321 had to guarantee a unique index on `source_message_id` ALONE, so its
    dedupe partitioned by the message id with no contact scope: two DIFFERENT
    contacts whose trigger messages shared an id had one of them blanked, and
    323's relaxation to (contact, message) never gave it back. That row's
    idempotency is gone for good - the next n8n retry opens a duplicate ticket.

    Runs the real 321 -> 323 -> 324 chain, exactly as `alembic upgrade head`
    would. Its own scratch schema (DDL test).
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with pg_empty_schema([ConversationSLATracking.__table__]) as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        conn = session.connection()
        conn.exec_driver_sql(f'SET search_path TO "{schema}"')

        # Pre-321 shape.
        conn.exec_driver_sql(
            "ALTER TABLE conversation_sla_tracking DROP COLUMN IF EXISTS source_message_id"
        )
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {CONTACT_SOURCE_MESSAGE_INDEX}")
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {NEW_SOURCE_MESSAGE_INDEX}")

        policy_id = str(uuid.uuid4())
        conn.exec_driver_sql(
            "INSERT INTO sla_policies (id, code, name, is_active) VALUES (%s, %s, %s, %s)",
            (policy_id, "ZZT-MIG324", "Migration 324 policy", True),
        )
        contact_a, contact_b = str(uuid.uuid4()), str(uuid.uuid4())
        for contact_id in (contact_a, contact_b):
            conn.exec_driver_sql(
                "INSERT INTO respond_contacts (id, phone_number, session_vars) "
                "VALUES (%s, %s, %s)",
                (contact_id, f"+601{contact_id[:8]}", "{}"),
            )

        # Two contacts, one colliding trigger message id (the repairable case).
        cross_a, cross_b = str(uuid.uuid4()), str(uuid.uuid4())
        # One contact, two open rows on the same message (genuinely forbidden).
        same_new, same_old = str(uuid.uuid4()), str(uuid.uuid4())
        rows = (
            (cross_a, contact_a, 777, "2026-08-01 10:00:00"),
            (cross_b, contact_b, 777, "2026-08-01 11:00:00"),
            (same_old, contact_a, 555, "2026-08-01 09:00:00"),
            (same_new, contact_a, 555, "2026-08-01 12:00:00"),
        )
        for row_id, contact_id, message_id, created_at in rows:
            conn.exec_driver_sql(
                """
                INSERT INTO conversation_sla_tracking
                    (id, policy_id, current_tier, due_at, is_responded, is_resolved,
                     synced_to_excel, respond_contact_id, message_id, created_at)
                VALUES (%s, %s, 1, now(), false, false, false, %s, %s, %s)
                """,
                (row_id, policy_id, contact_id, message_id, created_at),
            )

        def _identities():
            return {
                str(row_id): source_message_id
                for row_id, source_message_id in conn.exec_driver_sql(
                    "SELECT id, source_message_id FROM conversation_sla_tracking"
                ).fetchall()
            }

        with Operations.context(MigrationContext.configure(conn)):
            mig321.upgrade()
            mig323.upgrade()

        after_323 = _identities()
        # 321 is left as it shipped - its own index needs that dedupe, and
        # rewriting an applied migration would diverge from the databases that
        # already ran it. The repair is a forward step, so this intermediate
        # state is pinned deliberately.
        assert after_323[cross_a] is None, "the older cross-contact row was blanked by 321"
        assert after_323[cross_b] == "777"

        with Operations.context(MigrationContext.configure(conn)):
            mig324.upgrade()

        after_324 = _identities()
        assert after_324[cross_a] == "777", (
            "different contacts sharing a message id are two distinct tickets - "
            "both keep their identity once the index is contact-scoped"
        )
        assert after_324[cross_b] == "777"
        # Same contact, same message: exactly one may hold the identity.
        assert after_324[same_new] == "555"
        assert after_324[same_old] is None

        # Re-runnable: nothing left to restore, nothing broken by trying.
        with Operations.context(MigrationContext.configure(conn)):
            mig324.upgrade()
        assert _identities() == after_324


# ---------------------------------------------------------------------------
# AC-A4 - working hours: the clock waits, the request time does not
# ---------------------------------------------------------------------------

from app.services.calendar_service import CalendarService as _RealCalendarService


class _StubCalendar(_RealCalendarService):
    """Mon-Fri 09:00-17:00 KL, no holidays, no DB. Mirrors the stub in
    test_form_sla_clock_start.py: subclass the real CalendarService and override
    only the data providers, so `next_working_window_open` / the working-days
    math stay the real, tested algorithm (calling ``CalendarService`` by name
    from inside a monkeypatched module would resolve to this stub and recurse)."""

    def __init__(self, db=None):  # noqa: ANN001 - matches CalendarService(db)
        self.db = db

    def get_working_weekdays(self) -> Set[int]:
        return {0, 1, 2, 3, 4}

    def get_working_hours(self):
        return (time(9, 0, 0), time(17, 0, 0))

    def get_public_holidays_between(self, start_date: date, end_date: date):
        return set()


def _freeze(monkeypatch, kl_wall: datetime):
    """Pin 'now' to a KL wall-clock instant and install the stub calendar."""
    import app.services.calendar_service as calendar_mod
    import app.services.sla_service as sla_mod

    aware_utc = kl_wall.replace(tzinfo=KL).astimezone(timezone.utc)
    monkeypatch.setattr(sla_mod, "_now_utc", lambda: aware_utc)
    monkeypatch.setattr(calendar_mod, "CalendarService", _StubCalendar)
    return aware_utc


def test_out_of_hours_create_reports_out_of_hours_and_waits_for_the_window(db, monkeypatch):
    """Saturday 10:00 KL: the request time is kept, the clock starts Monday 09:00."""
    seed = _seed(db)
    requested = _freeze(monkeypatch, datetime(2026, 6, 6, 10, 0, 0))  # Sat
    service = ConversationSLATrackingService(db)

    ticket = service.create_tracking(_payload(seed, source_message_id="msg-off"))

    assert getattr(ticket, "_in_working_hours") is False
    assert ticket.initiated_at.replace(tzinfo=timezone.utc) == requested
    started_kl = ticket.current_tier_started_at.replace(tzinfo=timezone.utc).astimezone(KL)
    assert (started_kl.month, started_kl.day, started_kl.hour) == (6, 8, 9)  # Mon 09:00


def test_in_hours_create_reports_in_hours_and_starts_immediately(db, monkeypatch):
    seed = _seed(db)
    requested = _freeze(monkeypatch, datetime(2026, 6, 10, 10, 30, 0))  # Wed
    service = ConversationSLATrackingService(db)

    ticket = service.create_tracking(_payload(seed, source_message_id="msg-on"))

    assert getattr(ticket, "_in_working_hours") is True
    assert ticket.initiated_at.replace(tzinfo=timezone.utc) == requested
    assert ticket.current_tier_started_at.replace(tzinfo=timezone.utc) == requested


def test_working_hours_marker_is_present_on_an_idempotent_hit(db, monkeypatch):
    """n8n picks its auto-reply copy from the response of EVERY call, including a
    retry, so the marker cannot be exclusive to the insert path."""
    seed = _seed(db)
    _freeze(monkeypatch, datetime(2026, 6, 10, 10, 30, 0))  # Wed, in hours
    service = ConversationSLATrackingService(db)
    service.create_tracking(_payload(seed, source_message_id="msg-1"))

    _freeze(monkeypatch, datetime(2026, 6, 6, 10, 0, 0))  # Sat, out of hours
    again = service.create_tracking(_payload(seed, source_message_id="msg-1"))

    assert bool(getattr(again, "_already_active", False)) is True
    assert getattr(again, "_in_working_hours") is False
