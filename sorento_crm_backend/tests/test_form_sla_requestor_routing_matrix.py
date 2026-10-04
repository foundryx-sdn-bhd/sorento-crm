"""Requestor-based CS pin routing matrix (PLAN-requested-by-contact-routing.md
groups E). Exercises the REAL ``FormSLAOrchestrator._start_for_config`` combinator

    routing_contact_id = self._routing_contact_id(...) or contact_id
    assignee = self._resolve_pinned_assignee(type, routing_contact_id, team_id, id)
    if not assignee: assignee = agent_svc.get_next_assignee(...)

with ``_routing_contact_id`` and ``_resolve_pinned_assignee`` mocked (each is
independently covered elsewhere: real-DB in test_form_sla_routing_contact_id.py,
resolver branches in test_cs_pinpoint_routing.py) so this file isolates and pins
the COMBINATOR logic itself -- in particular the E2 regression: a requestor with
no pin must fall to round-robin and must NEVER retry the submitter's pin.

Hermetic (MagicMock db), mirroring the existing mock-chain convention in
test_cs_pinpoint_routing.py / test_form_sla_default_approver_routing.py /
test_coverage_redirect.py's harness note.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import coverage_subscription_service as cov_mod
from app.services import form_sla_service as svc_mod
from app.services.form_sla_service import FormSLAOrchestrator
from app.services.user_service import AccessAgentService

SUBMITTER_ID = "submitter-contact-x"
REQUESTOR_ID = "requestor-eric"
RR_ASSIGNEE = {"id": "rr-user", "email": "rr@x.com", "name": "RR User", "respond_user_id": "ru-rr"}
PIN_ASSIGNEE = {"id": "pinned-user", "email": "pin@x.com", "name": "Pinned User", "respond_user_id": "ru-pin"}
SUBMITTER_PIN_ASSIGNEE = {
    "id": "submitter-pinned-user",
    "email": "subpin@x.com",
    "name": "Submitter Pinned User",
    "respond_user_id": "ru-subpin",
}


@pytest.fixture
def orch(monkeypatch):
    """A FormSLAOrchestrator wired so ``_start_for_config`` runs its REAL
    combinator logic end to end, with only the DB-backed leaves mocked."""
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        q.filter.return_value = q
        if model is svc_mod.AccessAgent:
            q.first.return_value = SimpleNamespace(id="agent-1", code="acode")
        elif model is svc_mod.SLAPolicyTier:
            q.first.return_value = SimpleNamespace(response_hours=24.0, resolution_hours=24.0)
        else:
            q.first.return_value = None
        return q

    db.query.side_effect = _query

    o = FormSLAOrchestrator(db)
    o._active_tracker = MagicMock(return_value=None)  # no existing tracker -> always starts fresh
    o._form_default_approver_user_id = MagicMock(return_value=None)  # never the approval-override branch

    monkeypatch.setattr(
        AccessAgentService,
        "resolve_team_with_tier_fallback",
        lambda self, agent_id, tier, team_set_code=None, *, company_id=None: ("team-1", 1),
    )
    monkeypatch.setattr(
        AccessAgentService, "get_next_assignee", lambda self, agent_id, team_id: dict(RR_ASSIGNEE)
    )
    monkeypatch.setattr(
        cov_mod, "resolve_assignee_with_coverage", lambda db, assignee: (assignee, None)
    )
    return o


def _config(source_entity_type="purchase_request"):
    return SimpleNamespace(
        id="cfg-1",
        source_entity_type=source_entity_type,
        agent_code="acode",
        team_set_code=None,
        policy_id="pol-1",
        notify_assignee=False,  # skip the Respond.io notify side-effect entirely
        resolve_event="resolved",
    )


# ---------------------------------------------------------------------------
# E1: requestor has a matching pin -> tracker assigned to the requestor's pin.
# ---------------------------------------------------------------------------


def test_e1_requestor_with_pin_is_assigned_to_that_pin(orch):
    orch._routing_contact_id = MagicMock(return_value=REQUESTOR_ID)
    orch._resolve_pinned_assignee = MagicMock(return_value=dict(PIN_ASSIGNEE))

    tracker = orch._start_for_config(_config(), "entity-1", contact_id=SUBMITTER_ID)

    orch._resolve_pinned_assignee.assert_called_once_with(
        "purchase_request", REQUESTOR_ID, "team-1", "entity-1"
    )
    assert tracker.assigned_to_id == PIN_ASSIGNEE["id"]


# ---------------------------------------------------------------------------
# E2 (the actual bug): requestor has NO pin -> round robin, and the resolver
# is called with the REQUESTOR's id, never retried with the submitter's id --
# even though the submitter (CONTACT X) DOES have a pin that must be ignored.
# ---------------------------------------------------------------------------


def test_e2_requestor_without_pin_falls_to_round_robin_never_retries_submitter(orch):
    orch._routing_contact_id = MagicMock(return_value=REQUESTOR_ID)
    # Eric (the requestor) has no pin for this use_case -> None -> round robin.
    orch._resolve_pinned_assignee = MagicMock(return_value=None)

    tracker = orch._start_for_config(_config(), "entity-1", contact_id=SUBMITTER_ID)

    # Called exactly once, with the requestor's id -- never a second call
    # retrying with the submitter's id (that was the bug being fixed).
    orch._resolve_pinned_assignee.assert_called_once_with(
        "purchase_request", REQUESTOR_ID, "team-1", "entity-1"
    )
    assert orch._resolve_pinned_assignee.call_count == 1
    for call in orch._resolve_pinned_assignee.call_args_list:
        assert SUBMITTER_ID not in call.args

    # Landed on round robin, NOT on any submitter-pin-shaped assignee.
    assert tracker.assigned_to_id == RR_ASSIGNEE["id"]
    assert tracker.assigned_to_id != SUBMITTER_PIN_ASSIGNEE["id"]


# ---------------------------------------------------------------------------
# E3 (hard blocker, regression): requestor FK NULL -> byte-identical to
# pre-feature behaviour -- pin lookup on the SUBMITTER, then round robin.
# ---------------------------------------------------------------------------


def test_e3_null_requestor_fk_falls_back_to_submitter_pin_lookup(orch):
    # _routing_contact_id returning None simulates the NULL FK case exactly as
    # it happens in _start_for_config (real function tested independently in
    # test_form_sla_routing_contact_id.py).
    orch._routing_contact_id = MagicMock(return_value=None)
    orch._resolve_pinned_assignee = MagicMock(return_value=dict(SUBMITTER_PIN_ASSIGNEE))

    tracker = orch._start_for_config(_config(), "entity-1", contact_id=SUBMITTER_ID)

    # The "or contact_id" fallback lands on the SUBMITTER - exactly today's
    # (pre-feature) behaviour.
    orch._resolve_pinned_assignee.assert_called_once_with(
        "purchase_request", SUBMITTER_ID, "team-1", "entity-1"
    )
    assert tracker.assigned_to_id == SUBMITTER_PIN_ASSIGNEE["id"]


def test_e3_null_requestor_fk_and_no_submitter_pin_falls_to_round_robin(orch):
    orch._routing_contact_id = MagicMock(return_value=None)
    orch._resolve_pinned_assignee = MagicMock(return_value=None)

    tracker = orch._start_for_config(_config(), "entity-1", contact_id=SUBMITTER_ID)

    orch._resolve_pinned_assignee.assert_called_once_with(
        "purchase_request", SUBMITTER_ID, "team-1", "entity-1"
    )
    assert tracker.assigned_to_id == RR_ASSIGNEE["id"]


# ---------------------------------------------------------------------------
# E4: tracker.respond_contact_id is ALWAYS the submitter, regardless of who
# the routing/pin landed on.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "routing_contact_id,pinned_result",
    [
        (REQUESTOR_ID, dict(PIN_ASSIGNEE)),  # E1
        (REQUESTOR_ID, None),  # E2
        (None, dict(SUBMITTER_PIN_ASSIGNEE)),  # E3 (pin found on submitter)
        (None, None),  # E3 (round robin)
    ],
)
def test_e4_respond_contact_id_is_always_the_submitter(orch, routing_contact_id, pinned_result):
    orch._routing_contact_id = MagicMock(return_value=routing_contact_id)
    orch._resolve_pinned_assignee = MagicMock(return_value=pinned_result)

    tracker = orch._start_for_config(_config(), "entity-1", contact_id=SUBMITTER_ID)

    assert tracker.respond_contact_id == SUBMITTER_ID


# ---------------------------------------------------------------------------
# E5: stock_inquiry (project_sales / purchasing stages) uses the same
# requestor-based routing as PR/SF -- parametrized over source_entity_type.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_entity_type", ["purchase_request", "sponsorship_form", "stock_inquiry"])
def test_e5_requestor_routing_applies_to_every_requestor_bearing_form_type(orch, source_entity_type):
    orch._routing_contact_id = MagicMock(return_value=REQUESTOR_ID)
    orch._resolve_pinned_assignee = MagicMock(return_value=dict(PIN_ASSIGNEE))

    tracker = orch._start_for_config(_config(source_entity_type), "entity-1", contact_id=SUBMITTER_ID)

    orch._resolve_pinned_assignee.assert_called_once_with(
        source_entity_type, REQUESTOR_ID, "team-1", "entity-1"
    )
    assert tracker.assigned_to_id == PIN_ASSIGNEE["id"]
    assert tracker.respond_contact_id == SUBMITTER_ID


# ---------------------------------------------------------------------------
# No-regression: complaint never reads the requestor FK at all (no requestor
# concept), so routing_contact_id falls straight through to the submitter.
# ---------------------------------------------------------------------------


def test_complaint_never_consults_requestor_routing(orch):
    orch._routing_contact_id = MagicMock(return_value=None)  # complaint has no FK, always None
    orch._resolve_pinned_assignee = MagicMock(return_value=None)

    tracker = orch._start_for_config(_config("complaint"), "entity-1", contact_id=SUBMITTER_ID)

    orch._resolve_pinned_assignee.assert_called_once_with(
        "complaint", SUBMITTER_ID, "team-1", "entity-1"
    )
    assert tracker.assigned_to_id == RR_ASSIGNEE["id"]
