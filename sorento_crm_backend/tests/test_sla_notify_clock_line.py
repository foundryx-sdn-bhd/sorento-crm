"""Assignment notifications state the clock (UAC AC-G2).

Verified before building: today's copy is only "<ref> has been assigned to you.
Open: <link>". Nothing tells the human WHEN the response clock starts, so a
ticket raised at 22:00 on a Saturday reads as if it were already running - and
the assignee either panics or ignores it, both wrong.

AC-G2 makes the clock explicit, in Malaysia time, with the DAY and the ZONE
spelled out (staff read this at night and the deadline is another day):

- out of hours: "Clock starts Mon 17 Aug 09:00 MYT · respond by Mon 17 Aug 10:00 MYT"
- in hours:     "Respond by Fri 14 Aug 15:00 MYT"   (UNCONDITIONAL, never absent)

The in-hours variant is deliberately never omitted: a missing line reads as
"there is no clock", which is the failure this AC exists to remove.

The fixture crosses a WEEKEND, not just a night: a Saturday 09:25 MYT request
whose clock starts Monday 09:00 MYT. Clock start and due are seeded explicitly
rather than derived from the working calendar - CI's database has no calendar
configuration, so a test that derived them would assert about seed data instead
of about the copy.

Run:
    venv/bin/pytest tests/test_sla_notify_clock_line.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.services import sla_service as svc
from tests._pg_fixture import blank_session

# Malaysia is UTC+8, and every SLA column is naive UTC.
#   Sat 15 Aug 2026 09:25 MYT  ==  2026-08-15 01:25 UTC
#   Mon 17 Aug 2026 09:00 MYT  ==  2026-08-17 01:00 UTC
#   Mon 17 Aug 2026 10:00 MYT  ==  2026-08-17 02:00 UTC
SAT_REQUEST_UTC = datetime(2026, 8, 15, 1, 25)
MON_CLOCK_START_UTC = datetime(2026, 8, 17, 1, 0)
MON_DUE_UTC = datetime(2026, 8, 17, 2, 0)

OUT_OF_HOURS_LINE = "Clock starts Mon 17 Aug 09:00 MYT · respond by Mon 17 Aug 10:00 MYT"
IN_HOURS_LINE = "Respond by Fri 14 Aug 15:00 MYT"


class _Tracking:
    """Only the four columns the clock line reads."""

    def __init__(self, *, initiated_at, current_tier_started_at, due_at,
                 due_at_resolution=None, is_responded=False):
        self.initiated_at = initiated_at
        self.current_tier_started_at = current_tier_started_at
        self.due_at = due_at
        self.due_at_resolution = due_at_resolution
        self.is_responded = is_responded


# --------------------------------------------------------------------------- #
# The line itself                                                              #
# --------------------------------------------------------------------------- #


def test_out_of_hours_states_both_the_clock_start_and_the_deadline():
    line = svc.sla_clock_line(
        _Tracking(
            initiated_at=SAT_REQUEST_UTC,
            current_tier_started_at=MON_CLOCK_START_UTC,
            due_at=MON_DUE_UTC,
        )
    )
    assert line == OUT_OF_HOURS_LINE


def test_in_hours_states_the_deadline_unconditionally():
    # Fri 14 Aug 2026 14:00 MYT request, 15:00 MYT due - clock start == request.
    start = datetime(2026, 8, 14, 6, 0)
    line = svc.sla_clock_line(
        _Tracking(
            initiated_at=start,
            current_tier_started_at=start,
            due_at=datetime(2026, 8, 14, 7, 0),
        )
    )
    assert line == IN_HOURS_LINE
    assert "Clock starts" not in line


def test_a_sub_second_difference_is_not_treated_as_out_of_hours():
    """`current_tier_started_at` is recomputed from `now`, so the two stamps can
    differ by microseconds on an in-hours create. That is not a deferred clock."""
    line = svc.sla_clock_line(
        _Tracking(
            initiated_at=datetime(2026, 8, 14, 6, 0, 0, 0),
            current_tier_started_at=datetime(2026, 8, 14, 6, 0, 0, 900_000),
            due_at=datetime(2026, 8, 14, 7, 0),
        )
    )
    assert line == IN_HOURS_LINE


def test_after_the_first_response_the_line_names_the_running_clock():
    """The response clock has stopped; "respond by" would be a lie. The clock
    that is actually running on a reassign/takeover is the resolution one."""
    line = svc.sla_clock_line(
        _Tracking(
            initiated_at=datetime(2026, 8, 14, 6, 0),
            current_tier_started_at=datetime(2026, 8, 14, 6, 0),
            due_at=datetime(2026, 8, 14, 7, 0),
            due_at_resolution=datetime(2026, 8, 17, 2, 0),
            is_responded=True,
        )
    )
    assert line == "Resolve by Mon 17 Aug 10:00 MYT"


def test_no_deadline_at_all_produces_no_line():
    assert (
        svc.sla_clock_line(
            _Tracking(initiated_at=None, current_tier_started_at=None, due_at=None)
        )
        is None
    )


def test_the_middle_dot_survives_whatsapp_parameter_flattening():
    """WhatsApp template params reject newlines/tabs, not punctuation - so the
    same one body may feed in-app, email AND the template lane (AC-G2)."""
    from app.services.respond_messaging_service import sanitize_param

    assert "·" in sanitize_param(OUT_OF_HOURS_LINE)


# --------------------------------------------------------------------------- #
# The notifications that carry it                                              #
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture
def sent(monkeypatch):
    from app.services.notification_service import NotificationService

    calls: list[dict] = []

    def fake(self, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", fake)
    return calls


@pytest.fixture
def seeded(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=f"ZZT-{uuid.uuid4().hex[:6]}", name="ZZT Policy"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=1,
            resolution_hours=24,
        )
    )
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number="+60120000550",
            name="ZZT Clock Contact",
            respond_io_id="zzt-clock-io",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    actor_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email=f"zzt-cl-{assignee_id[:8]}@test.com", name="ZZT Assignee"))
    db.add(User(id=actor_id, email=f"zzt-ac-{actor_id[:8]}@test.com", name="ZZT Actor"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CLOCK_AGENT", name="ZZT Clock Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Clock Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_clock_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    tracking_id = str(uuid.uuid4())
    db.add(
        ConversationSLATracking(
            id=tracking_id,
            policy_id=policy_id,
            respond_contact_id=contact_id,
            assigned_to_id=assignee_id,
            current_tier=1,
            is_resolved=False,
            initiated_at=SAT_REQUEST_UTC,
            current_tier_started_at=MON_CLOCK_START_UTC,
            due_at=MON_DUE_UTC,
            due_at_resolution=datetime(2026, 8, 18, 2, 0),
        )
    )
    db.commit()
    return {
        "tracking_id": tracking_id,
        "assignee_id": assignee_id,
        "actor_id": actor_id,
        "policy_id": policy_id,
    }


def test_the_assignment_notification_carries_the_clock_line(db, seeded, sent):
    service = svc.ConversationSLATrackingService(db)
    tracking = service.get_tracking(seeded["tracking_id"])
    service._notify_assignment_on_create(tracking)

    assert sent, "no assignment notification was built"
    body = sent[0]["body"]
    assert OUT_OF_HOURS_LINE in body
    assert body.splitlines()[0].endswith("has been assigned to you.")
    # One body builder: the WhatsApp lane carries the same string, so the line
    # cannot go missing on the channel people actually read.
    assert OUT_OF_HOURS_LINE in sent[0]["data"]["whatsapp_text"]


def test_the_reassignment_notification_carries_the_clock_line(db, seeded, sent):
    service = svc.ConversationSLATrackingService(db)
    tracking = service.get_tracking(seeded["tracking_id"])
    service._notify_reassignment(
        tracking,
        actor_id=seeded["actor_id"],
        new_assignee_id=seeded["assignee_id"],
        old_assignee_id=None,
    )
    assert sent, "no reassignment notification was built"
    assert OUT_OF_HOURS_LINE in sent[0]["body"]


def test_the_coverage_copy_carries_the_clock_line(db, seeded, sent, monkeypatch):
    captured: list[dict] = []

    import app.services.coverage_subscription_service as coverage

    monkeypatch.setattr(
        coverage,
        "fan_out_coverage_copies",
        lambda db, **kwargs: captured.append(kwargs),
    )
    service = svc.ConversationSLATrackingService(db)
    tracking = service.get_tracking(seeded["tracking_id"])
    service._fan_out_assignment_coverage(tracking)

    assert captured, "no coverage copy was built"
    assert OUT_OF_HOURS_LINE in captured[0]["body"]
