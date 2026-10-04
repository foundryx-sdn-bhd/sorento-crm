"""Team Tasks listing: recursive visibility, self/resolved exclusion, filters."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.access import Team, TeamMember, RespondContact
from app.models.sla import ConversationSLATracking, SLAPolicy
from app.models.user import User
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        # descendant_team_ids() runs a recursive CTE as raw text() SQL over an
        # unqualified `teams`, which schema_translate_map does not rewrite -- see
        # the note in test_coverage_hod_assign. Align search_path with the blank
        # schema so it reads this test's teams and not the real ones.
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


def _user(db, name) -> str:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{name}@x.com", name=name, status="ACTIVE"))
    db.commit()
    return uid


def _team(db, name, parent_id=None) -> str:
    tid = str(uuid.uuid4())
    db.add(Team(id=tid, name=name, parent_team_id=parent_id))
    db.commit()
    return tid


def _member(db, team_id, user_id):
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=user_id))
    db.commit()


def _policy(db) -> str:
    pid = str(uuid.uuid4())
    db.add(SLAPolicy(id=pid, code="p", name="Policy"))
    db.commit()
    return pid


def _track(db, pid, *, assignee, resolved=False, due_h=5, src="stock_inquiry"):
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=pid,
            current_tier=1,
            assigned_to_id=assignee,
            due_at=datetime(2026, 6, 1) + timedelta(hours=due_h),
            is_resolved=resolved,
            source_entity_type=src,
            source_entity_id=str(uuid.uuid4()),
        )
    )
    db.commit()


def test_peers_visible_self_and_resolved_excluded(db):
    pid = _policy(db)
    me = _user(db, "me")
    peer = _user(db, "charissa")
    team = _team(db, "Product")
    _member(db, team, me)
    _member(db, team, peer)
    _track(db, pid, assignee=peer, due_h=2)
    _track(db, pid, assignee=peer, resolved=True, due_h=1)  # resolved -> excluded
    _track(db, pid, assignee=me, due_h=3)  # my own -> excluded

    out = ConversationSLATrackingService(db).list_team_pending(me)
    assert out["total"] == 1
    assert out["data"][0]["assignee_id"] == peer
    assert out["data"][0]["assignee_name"] == "charissa"
    assert out["data"][0]["team_label"] == "Product"


def test_manager_sees_child_and_grandchild(db):
    pid = _policy(db)
    mgr_user = _user(db, "manager")
    child_user = _user(db, "child")
    grand_user = _user(db, "grand")
    mgr = _team(db, "Manager")
    child = _team(db, "Product", parent_id=mgr)
    grand = _team(db, "Sub", parent_id=child)
    _member(db, mgr, mgr_user)
    _member(db, child, child_user)
    _member(db, grand, grand_user)
    _track(db, pid, assignee=child_user, due_h=1)
    _track(db, pid, assignee=grand_user, due_h=2)

    out = ConversationSLATrackingService(db).list_team_pending(mgr_user)
    assert {r["assignee_id"] for r in out["data"]} == {child_user, grand_user}


def test_scope_isolation(db):
    pid = _policy(db)
    me = _user(db, "me")
    outsider = _user(db, "outsider")
    my_team = _team(db, "Mine")
    other_team = _team(db, "Other")
    _member(db, my_team, me)
    _member(db, other_team, outsider)
    _track(db, pid, assignee=outsider, due_h=1)

    out = ConversationSLATrackingService(db).list_team_pending(me)
    assert out["total"] == 0


def test_assignee_and_team_filters(db):
    pid = _policy(db)
    me = _user(db, "me")
    a = _user(db, "alice")
    b = _user(db, "bob")
    team1 = _team(db, "T1")
    team2 = _team(db, "T2", parent_id=team1)  # child so both visible from team1
    _member(db, team1, me)
    _member(db, team1, a)
    _member(db, team2, b)
    _track(db, pid, assignee=a, due_h=1)
    _track(db, pid, assignee=b, due_h=2)

    svc = ConversationSLATrackingService(db)
    # No filter -> both
    assert svc.list_team_pending(me)["total"] == 2
    # Assignee filter
    only_a = svc.list_team_pending(me, assignee=a)
    assert only_a["total"] == 1 and only_a["data"][0]["assignee_id"] == a
    # Team filter -> only members of team2 (bob)
    only_t2 = svc.list_team_pending(me, team=team2)
    assert only_t2["total"] == 1 and only_t2["data"][0]["assignee_id"] == b


def test_includes_form_and_conversation_rows(db):
    pid = _policy(db)
    me = _user(db, "me")
    peer = _user(db, "peer")
    team = _team(db, "T")
    _member(db, team, me)
    _member(db, team, peer)
    _track(db, pid, assignee=peer, src="complaint", due_h=1)  # form
    # conversation row needs a contact for reference resolution; use a contact id
    c = RespondContact(id=str(uuid.uuid4()), phone_number="+60123", name="Cust", session_vars={})
    db.add(c)
    db.commit()
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=pid,
            current_tier=1,
            assigned_to_id=peer,
            due_at=datetime(2026, 6, 1) + timedelta(hours=2),
            is_resolved=False,
            source_entity_type=None,
            respond_contact_id=c.id,
        )
    )
    db.commit()

    out = ConversationSLATrackingService(db).list_team_pending(me)
    assert out["total"] == 2
    forms = [r for r in out["data"] if r["is_form_sla"]]
    convs = [r for r in out["data"] if not r["is_form_sla"]]
    assert len(forms) == 1 and len(convs) == 1


def test_ticket_row_carries_the_same_shape_my_pending_emits(db):
    """A team row for an intervention ticket must carry EVERY field the widget
    needs to open the drawer instead of falling through to the SLA detail page -
    the row used to be built independently of /my-pending and silently dropped
    all of these (list_team_pending / list_my_pending now share one row
    builder, `ConversationSLATrackingService._pending_row`)."""
    pid = _policy(db)
    me = _user(db, "me")
    peer = _user(db, "peer")
    team = _team(db, "T")
    _member(db, team, me)
    _member(db, team, peer)
    c = RespondContact(
        id=str(uuid.uuid4()),
        phone_number="+60120000551",
        name="Aisyah",
        respond_io_id="10099001",
        session_vars={},
    )
    db.add(c)
    db.commit()
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=pid,
            current_tier=1,
            assigned_to_id=peer,
            due_at=datetime(2026, 6, 1) + timedelta(hours=2),
            is_resolved=False,
            source_entity_type=None,
            respond_contact_id=c.id,
            source_message_id="wamid.team-1",
            source_message_text="Please help with my order.",
        )
    )
    db.commit()

    out = ConversationSLATrackingService(db).list_team_pending(me)
    assert out["total"] == 1
    row = out["data"][0]
    assert row["is_intervention_ticket"] is True
    assert row["contact_name"] == "Aisyah"
    assert row["contact_phone"] == "+60120000551"
    assert row["enquiry_snippet"] == "Please help with my order."
    assert row["respond_io_id"] == "10099001"
    # Team-only fields still there, unaffected by the shared row builder.
    assert row["assignee_id"] == peer
    assert row["team_label"] == "T"


def test_non_ticket_conversation_row_still_carries_respond_io_id(db):
    """A legacy (pre-migration) conversation row is not a ticket, but the My
    Team row should still deep-link to the Respond inbox like My Pending -
    the team-pending serializer used to omit respond_io_id entirely."""
    pid = _policy(db)
    me = _user(db, "me")
    peer = _user(db, "peer")
    team = _team(db, "T2")
    _member(db, team, me)
    _member(db, team, peer)
    c = RespondContact(
        id=str(uuid.uuid4()),
        phone_number="+60120000552",
        name="Legacy Contact",
        respond_io_id="10099002",
        session_vars={},
    )
    db.add(c)
    db.commit()
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=pid,
            current_tier=1,
            assigned_to_id=peer,
            due_at=datetime(2026, 6, 1) + timedelta(hours=2),
            is_resolved=False,
            source_entity_type=None,
            respond_contact_id=c.id,
            # No source_message_id -> legacy row, never a ticket.
        )
    )
    db.commit()

    out = ConversationSLATrackingService(db).list_team_pending(me)
    assert out["total"] == 1
    row = out["data"][0]
    assert "is_intervention_ticket" not in row
    assert row["respond_io_id"] == "10099002"
