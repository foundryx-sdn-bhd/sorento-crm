"""Seeds the Project Sales module's configurable defaults.

Everything here is configuration the user can change afterwards, not code. It exists
because a module whose first screen is empty cannot be evaluated: a fresh install must
show a working funnel and a usable set of project types on day one.

Idempotent and additive. It never updates or deletes an existing row, so a team that
renames "Identified" to "Sighted" or deletes a type they do not use keeps their change
across every restart. That is the difference between a seed and a reset.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.numbering import DocumentNumberingRule
from app.models.projects import ProjectTemplate, ProjectTemplateRole, ProjectType
from app.models.lookup import LookupOption, LookupSet
from app.models.status import Status, StatusTransition
from app.models.projects import (
    LEAD_DISQUALIFY_REASON_SET_KEY,
    QUOTATION_LOSS_REASON_SET_KEY,
)
from app.services.project_reference_service import DEFAULT_TEMPLATE_ROLES

logger = logging.getLogger(__name__)

PROJECT_ENTITY = "project"
PROJECT_TASK_ENTITY = "project_task"
PROJECT_LEAD_ENTITY = "project_lead"
QUOTATION_ENTITY = "quotation"
SAMPLE_REQUEST_ENTITY = "sample_request"

# The funnel from the client's process-flow PDF. The terminal rung is "PO Received",
# not "Won": status says what happened, while the commercial outcome is derived, so a
# project with a PO on one scope and a live quotation on another does not read as
# finished (grill finding G1).
#
# REGISTERED is the starting rung, not Identified. A project row cannot exist without a
# registration - registering is the write that creates it, and the clash check runs there -
# so landing new projects on "Identified" described a state they were never in. The
# pre-registration state has its own record: a LEAD. Identified survives as a rung an admin
# can still move a project back to (a registration made in error, parked rather than
# deleted), which is why it keeps its backward edge.
DEFAULT_FUNNEL = (
    # (key, label, initial, terminal)
    ("identified", "Identified", False, False),
    ("registered", "Registered", True, False),
    # Key unchanged (every stored status_id and metric reads the key); the label is the
    # owner's word for the rung, 13 Oct.
    ("specified", "Spec In", False, False),
    ("quoted", "Quoted", False, False),
    ("tendering", "Tendering", False, False),
    ("po_received", "PO Received", False, True),
    ("lost", "Lost", False, True),
    ("dormant", "Dormant", False, True),
)

# Starting win probabilities per rung (AC-I2), used only to FILL A NULL. They are a
# starting vocabulary for the Weighted number, exactly like the loss reasons below: the
# sales manager tunes them from the status editor once they have their own history, and
# the seeder never argues with that afterwards.
#
# Chosen to climb, because that is the only property a forecast reader relies on. The
# absolute values matter far less than the ordering: nobody can defend "Specified is 40%"
# on day one, but everybody agrees Specified beats Registered.
#
# Terminal rungs get a real 0 rather than a NULL. Lost is not an unknown, it is a decided
# ending, and Dormant is a pursuit nobody is working. PO Received keeps 100 for the same
# reason: it is honest even though a project that far along contributes through Committed
# rather than Weighted.
DEFAULT_WIN_PROBABILITIES = {
    "identified": 10,
    "registered": 25,
    "specified": 40,
    "quoted": 60,
    "tendering": 75,
    "po_received": 100,
    "lost": 0,
    "dormant": 0,
}

# Starting staleness thresholds per rung, in days (AC-H4). Same NULL-only fill rule as the
# probabilities above.
#
# They TIGHTEN down the funnel, which is the whole point of making them per status: a
# Registered project may fairly sit a month while the developer finishes their own paperwork,
# but a Tendering one going quiet for a week is a lost tender nobody has admitted yet.
#
# Terminal rungs are deliberately ABSENT from this map, so they stay NULL and never go stale.
# There is nothing to chase on a lost project, and a badge on a finished one is noise that
# teaches people to ignore badges.
DEFAULT_STALE_AFTER_DAYS = {
    "identified": 21,
    "registered": 30,
    "specified": 21,
    "quoted": 14,
    "tendering": 7,
}

# Forward edges, plus Lost/Dormant reachable from every live rung. Deliberately NOT a
# fully-connected graph: the point of a configurable funnel is that illegal moves are
# rejected, and "anything to anything" makes the engine decorative.
_LIVE = ("identified", "registered", "specified", "quoted", "tendering")
DEFAULT_EDGES = (
    ("identified", "registered", "Register with developer"),
    # "Spec In" is the trade's own phrase: spec = specification. "Specified in" reads as
    # a past participle and is not what anybody says out loud.
    ("registered", "specified", "Spec In"),
    ("specified", "quoted", "Quotation sent"),
    ("quoted", "tendering", "Tendering"),
    ("tendering", "po_received", "PO received"),
    ("quoted", "po_received", "PO received"),
    # Backwards, because real pursuits move backwards: a re-spec after losing a
    # tender round is normal and must not require an admin to fix the data.
    ("quoted", "specified", "Re-specify"),
    ("tendering", "quoted", "Re-quote"),
    ("registered", "identified", "Back to identified"),
    # A project moved to Spec In by mistake must be able to step back (owner, 13 Oct).
    ("specified", "registered", "Back to registered"),
)

# Ecohub's five task rungs, ported as-is. Escalate was missing from the first draft of
# this plan entirely, which is exactly the kind of gap a reference pass catches: without
# it, a blocked task can only be "Stuck", and the difference between "I cannot proceed"
# and "I have handed this to someone senior" is lost.
DEFAULT_TASK_STATUSES = (
    # (key, label, initial, terminal)
    ("not_started", "Not Started", True, False),
    ("in_progress", "In Progress", False, False),
    ("escalate", "Escalate", False, False),
    ("stuck", "Stuck", False, False),
    ("done", "Done", False, True),
)

# A task gets escalated or stuck at ANY point, not at one designated moment, so both are
# reachable from every live rung and both lead back. Done is terminal: reopening is an
# admin choice (clear the terminal flag, add the edge), not the default.
_TASK_LIVE = ("not_started", "in_progress", "escalate", "stuck")
DEFAULT_TASK_EDGES = (
    ("not_started", "in_progress", "Start"),
    ("in_progress", "done", "Mark done"),
    ("escalate", "in_progress", "Taken back"),
    ("stuck", "in_progress", "Unblocked"),
    ("escalate", "done", "Resolved by escalation"),
    ("stuck", "done", "Resolved while stuck"),
    ("not_started", "done", "Not needed after all"),
)

# The lead funnel (AC-O7). Deliberately SHORT: a lead is a rumour being firmed up, and
# a five-rung qualification pipeline for hearsay is ceremony nobody maintains. Both
# terminal rungs are real endings -- qualified leads do not come back, they become
# projects, and one lead may become several (AC-O5).
DEFAULT_LEAD_STATUSES = (
    # (key, label, initial, terminal)
    ("new", "New", True, False),
    ("contacted", "Contacted", False, False),
    ("qualifying", "Qualifying", False, False),
    ("qualified", "Qualified", False, True),
    ("disqualified", "Disqualified", False, True),
)

_LEAD_LIVE = ("new", "contacted", "qualifying")
DEFAULT_LEAD_EDGES = (
    ("new", "contacted", "Contacted"),
    ("contacted", "qualifying", "Start qualifying"),
    ("new", "qualifying", "Start qualifying"),
    ("qualifying", "qualified", "Qualified"),
    ("contacted", "qualified", "Qualified"),
)

# The price-floor approval graph (S14). Deliberately FIVE rungs and nothing more: this is a
# gate, not a workflow, and the only question it answers is whether a manager has said yes to
# pricing that went below the floor.
#
# Nothing is terminal. `issued` is not an ending - a quotation is revised and issued again, and
# the next revision may dip below the floor again - and `rejected` is answered by re-pricing.
# A terminal rung on either would strand the document with no legal move out of it.
#
# `rejected` sorts BETWEEN draft and pending_approval on purpose. Sort order is what the shared
# frontend helpers read to tell an advance from a correction (`splitStatusMoves`), and being
# sent back is a step backwards. Sorted after `issued` it would have read as the forward move
# out of pending_approval, putting Reject where Approve belongs.
DEFAULT_QUOTATION_STATUSES = (
    # (key, label, initial, terminal)
    ("draft", "Draft", True, False),
    ("rejected", "Rejected", False, False),
    ("pending_approval", "Pending Approval", False, False),
    ("approved", "Approved", False, False),
    ("issued", "Issued", False, False),
)

# The exact set, and no more. "Anything to anything" would make the engine decorative, and each
# edge here is a move somebody actually makes:
#
# - draft -> pending_approval  the salesperson asks (their own move, no extra grant)
# - pending_approval -> approved / rejected  the manager decides (permission-gated routes)
# - rejected -> draft  the salesperson tries again; it is just "edit and re-price", so it needs
#   nothing beyond the edit rights they already hold to change the quotation at all
# - approved -> issued  stamped BY issuing, never claimed by a person: it spends the approval,
#   so the next below-floor revision has to be approved on its own merits
# - issued -> pending_approval  which is how that next revision asks
DEFAULT_QUOTATION_EDGES = (
    ("draft", "pending_approval", "Send for approval", 0),
    ("pending_approval", "approved", "Approve", 0),
    ("pending_approval", "rejected", "Reject", 1),
    ("rejected", "draft", "Back to draft", 0),
    ("approved", "issued", "Issued to the customer", 0),
    ("issued", "pending_approval", "Send for approval", 0),
)

# Sample request flow (SR-CORE card rule 10). Cancelled is reachable from Draft, Submitted and
# Processing; every other move is one rung forward.
DEFAULT_SAMPLE_REQUEST_STATUSES = (
    # (key, label, initial, terminal)
    ("draft", "Draft", True, False),
    ("submitted", "Submitted", False, False),
    ("processing", "Processing", False, False),
    ("customer_confirmed", "Customer confirmed", False, False),
    ("delivered", "Delivered", False, True),
    ("cancelled", "Cancelled", False, True),
)
DEFAULT_SAMPLE_REQUEST_EDGES = (
    ("draft", "submitted", "Submit"),
    ("submitted", "processing", "Start processing"),
    ("processing", "customer_confirmed", "Customer confirmed"),
    ("customer_confirmed", "delivered", "Delivered"),
    ("draft", "cancelled", "Cancel"),
    ("submitted", "cancelled", "Cancel"),
    ("processing", "cancelled", "Cancel"),
)

# Seeded starting points for the disqualification reason lookup (AC-O6). A free-text
# reason cannot be reported on: "not interested" typed nine ways is nine buckets.
DEFAULT_LEAD_DISQUALIFY_REASONS = (
    ("no_project", "No such project"),
    ("wrong_segment", "Not our segment"),
    ("competitor_locked", "Competitor already specified"),
    ("budget", "Budget too low"),
    ("duplicate", "Duplicate of an existing lead or project"),
    ("no_response", "No response from the contact"),
)

# Seeded loss reasons for a quotation (AC-E9). Client-editable afterwards: this is a
# starting vocabulary, not a fixed enum.
DEFAULT_QUOTATION_LOSS_REASONS = (
    ("price", "Price"),
    ("spec_locked_competitor", "Spec locked to a competitor"),
    ("project_cancelled", "Project cancelled or deferred"),
    ("no_sample", "No sample submitted"),
    ("relationship", "Relationship"),
    ("lead_time", "Lead time"),
    ("other", "Other"),
)

LEAD_NUMBERING = {
    "doc_type": "project_lead",
    "prefix_template": "LEAD-",
    "number_digits": 6,
    "start_value": 1,
}

DEFAULT_TYPES = (
    # (code, name, derives_delivery_from_launch, templates)
    ("property_development", "Property Development", True, ("High Rise", "Landed")),
    ("hotel", "Hotel", False, ("New Build", "Refurbishment")),
    ("commercial_fitout", "Commercial Fitout", False, ("Office", "Retail")),
    ("renovation", "Renovation", False, ("Renovation",)),
    ("institutional", "Institutional", False, ("Institutional",)),
)

PROJECT_NUMBERING = {
    "doc_type": "project",
    "prefix_template": "PRJ-",
    "number_digits": 6,
    "start_value": 1,
}


def _uid() -> str:
    return str(uuid.uuid4())


def seed_numbering_rule(db: Session) -> bool:
    """``PRJ-000001`` upward. Prefix, padding and any date segment stay editable.

    ``doc_type`` is unique with no company column, so the sequence is shared across
    companies and codes are globally unique -- Mocha's first project may be PRJ-000247
    (AC-C8, accepted).
    """
    existing = (
        db.query(DocumentNumberingRule)
        .filter(DocumentNumberingRule.doc_type == PROJECT_NUMBERING["doc_type"])
        .first()
    )
    if existing:
        return False
    db.add(
        DocumentNumberingRule(
            id=_uid(),
            doc_type=PROJECT_NUMBERING["doc_type"],
            enabled=True,
            prefix_template=PROJECT_NUMBERING["prefix_template"],
            number_digits=PROJECT_NUMBERING["number_digits"],
            next_value=PROJECT_NUMBERING["start_value"],
            start_value=PROJECT_NUMBERING["start_value"],
            reset_policy="none",
        )
    )
    db.flush()
    return True


def seed_default_funnel(db: Session) -> int:
    """The DEFAULT graph for ``project``. Templates fork it if they need to differ.

    Skipped entirely once any default-scope project status exists, rather than
    per-status: adding back a rung the team deliberately deleted every time the app
    restarts would be worse than not seeding at all.
    """
    already = (
        db.query(func.count(Status.id))
        .filter(Status.entity_type == PROJECT_ENTITY, Status.scope_id.is_(None))
        .scalar()
    )
    if already:
        return 0

    # The count above and the insert below are two statements, and under CI's xdist two
    # workers seeding the same database can both read 0 and both insert: the second dies
    # on `uq_statuses_entity_scope_key`. A savepoint turns that race into "someone else
    # got there first", which for an idempotent seed is the same as `already`.
    by_key: Dict[str, Status] = {}
    savepoint = db.begin_nested()
    try:
        for index, (key, label, initial, terminal) in enumerate(DEFAULT_FUNNEL):
            row = Status(
                id=_uid(),
                entity_type=PROJECT_ENTITY,
                scope_id=None,
                key=key,
                label=label,
                sort_order=index,
                is_initial=initial,
                is_terminal=terminal,
                is_default=initial,
            )
            db.add(row)
            by_key[key] = row
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        return 0

    created = len(by_key)
    for from_key, to_key, label in DEFAULT_EDGES:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=PROJECT_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key[to_key].id,
                label=label,
                trigger_mode="manual",
            )
        )
    # Every live rung can end in Lost, Dormant or PO Received. Losing at the identified
    # stage is as real as losing at tender, and a PO genuinely does arrive on a project
    # whose funnel position was never kept up to date -- AC-F10's auto edge has to be
    # able to fire from wherever the project actually sits, or it silently does nothing
    # on exactly the projects that were being tracked least carefully.
    for from_key in _LIVE:
        for terminal_key, label in (
            ("lost", "Mark lost"),
            ("dormant", "Mark dormant"),
            ("po_received", "PO received"),
        ):
            if (from_key, terminal_key) in {
                (edge[0], edge[1]) for edge in DEFAULT_EDGES
            }:
                continue  # already declared explicitly above
            db.add(
                StatusTransition(
                    id=_uid(),
                    entity_type=PROJECT_ENTITY,
                    scope_id=None,
                    from_status_id=by_key[from_key].id,
                    to_status_id=by_key[terminal_key].id,
                    label=label,
                    trigger_mode="manual",
                )
            )
    db.flush()
    savepoint.commit()
    return created


def ensure_win_probabilities(db: Session) -> int:
    """Fill ``statuses.win_probability`` where it is NULL, on the default project graph.

    Its own step for the same reason ``ensure_po_received_edges`` is: ``seed_default_funnel``
    is wholesale-guarded, so an install that already has its funnel would never receive the
    probabilities the Weighted number needs and the report would read zero forever.

    NULL-only, never overwrite. NULL means nobody has expressed an opinion; any value means
    somebody has, including a deliberate 0 on a rung the team does not trust. Re-asserting
    the default on boot would revert that on every restart, and nobody connects the two
    events. Forked (scope_id NOT NULL) graphs are left alone -- a template that tuned its own
    funnel is making a choice.
    """
    rows = (
        db.query(Status)
        .filter(
            Status.entity_type == PROJECT_ENTITY,
            Status.scope_id.is_(None),
            Status.win_probability.is_(None),
            Status.key.in_(list(DEFAULT_WIN_PROBABILITIES)),
        )
        .all()
    )
    for row in rows:
        row.win_probability = DEFAULT_WIN_PROBABILITIES[row.key]
    if rows:
        db.flush()
    return len(rows)


def ensure_stale_thresholds(db: Session) -> int:
    """Fill ``statuses.stale_after_days`` where it is NULL, on the default project graph.

    Separate from `ensure_win_probabilities` even though the shape is identical, because the
    two answer different questions and a team may well tune one and not the other. Merging
    them would also make the boot summary say "3 dials filled" without saying which.

    NULL-only, and terminal rungs are not in the map at all, so they stay NULL: never stale
    is the correct answer for a finished project, not a very large number.
    """
    rows = (
        db.query(Status)
        .filter(
            Status.entity_type == PROJECT_ENTITY,
            Status.scope_id.is_(None),
            Status.stale_after_days.is_(None),
            Status.key.in_(list(DEFAULT_STALE_AFTER_DAYS)),
        )
        .all()
    )
    for row in rows:
        row.stale_after_days = DEFAULT_STALE_AFTER_DAYS[row.key]
    if rows:
        db.flush()
    return len(rows)


def ensure_po_received_edges(db: Session) -> int:
    """Add the missing live-rung -> PO Received edges on a graph that already exists.

    ``seed_default_funnel`` is wholesale-guarded, so an install seeded before AC-F10's
    auto edge existed would never get the new edges from it. Idempotent and JOIN-shaped
    ("add where missing") rather than "insert if the graph is empty", which is the only
    form that can also correct an install seeded half way through the change.

    Deliberately does NOT touch forked (scope_id NOT NULL) graphs: a template that
    pruned its own edges made a choice, and the auto edge reports a no-move rather than
    overruling it.
    """
    live_and_target = (
        db.query(Status)
        .filter(
            Status.entity_type == PROJECT_ENTITY,
            Status.scope_id.is_(None),
            Status.key.in_(list(_LIVE) + ["po_received"]),
        )
        .all()
    )
    by_key = {row.key: row for row in live_and_target}
    target = by_key.get("po_received")
    if target is None:
        return 0

    existing = {
        row[0]
        for row in db.query(StatusTransition.from_status_id)
        .filter(
            StatusTransition.entity_type == PROJECT_ENTITY,
            StatusTransition.scope_id.is_(None),
            StatusTransition.to_status_id == target.id,
        )
        .all()
    }

    added = 0
    for key in _LIVE:
        source = by_key.get(key)
        if source is None or source.id in existing:
            continue
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=PROJECT_ENTITY,
                scope_id=None,
                from_status_id=source.id,
                to_status_id=target.id,
                label="PO received",
                trigger_mode="manual",
            )
        )
        added += 1
    if added:
        db.flush()
    return added


def seed_default_task_graph(db: Session) -> int:
    """The DEFAULT graph for ``project_task`` (AC-N4).

    Same wholesale guard as the project funnel: skipped once any default-scope task
    status exists, so a team that renamed or pruned a rung keeps their change.
    """
    already = (
        db.query(func.count(Status.id))
        .filter(Status.entity_type == PROJECT_TASK_ENTITY, Status.scope_id.is_(None))
        .scalar()
    )
    if already:
        return 0

    by_key: Dict[str, Status] = {}
    for index, (key, label, initial, terminal) in enumerate(DEFAULT_TASK_STATUSES):
        row = Status(
            id=_uid(),
            entity_type=PROJECT_TASK_ENTITY,
            scope_id=None,
            key=key,
            label=label,
            sort_order=index,
            is_initial=initial,
            is_terminal=terminal,
            is_default=initial,
        )
        db.add(row)
        by_key[key] = row
    db.flush()

    for from_key, to_key, label in DEFAULT_TASK_EDGES:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=PROJECT_TASK_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key[to_key].id,
                label=label,
                trigger_mode="manual",
            )
        )
    # Escalate and Stuck reachable from every live rung, and from each other: a task
    # that has been stuck a fortnight is exactly what gets escalated.
    for from_key in _TASK_LIVE:
        for to_key, label in (("escalate", "Escalate"), ("stuck", "Mark stuck")):
            if from_key == to_key:
                continue
            db.add(
                StatusTransition(
                    id=_uid(),
                    entity_type=PROJECT_TASK_ENTITY,
                    scope_id=None,
                    from_status_id=by_key[from_key].id,
                    to_status_id=by_key[to_key].id,
                    label=label,
                    trigger_mode="manual",
                )
            )
    db.flush()
    return len(by_key)


def seed_types_and_templates(db: Session, company_id: str) -> int:
    """Per company, because types are company-scoped configuration.

    Half the project names already in the system are hotels, fitouts and renovations,
    which is why type is configurable rather than assumed to be a property
    development (AC-C2 deviation).

    Skipped wholesale once the company has ANY project type, not per type. A
    create-if-missing loop would bring back "Institutional" on every restart after
    someone deliberately deleted it, and nobody would connect the reappearance to a
    deploy. Same guard shape as the funnel, for the same reason.
    """
    if (
        db.query(func.count(ProjectType.id))
        .filter(ProjectType.company_id == company_id)
        .scalar()
    ):
        return 0

    created = 0
    for code, name, derives, template_names in DEFAULT_TYPES:
        project_type = (
            db.query(ProjectType)
            .filter(
                ProjectType.company_id == company_id,
                func.lower(ProjectType.code) == code,
            )
            .first()
        )
        if project_type is None:
            project_type = ProjectType(
                id=_uid(),
                company_id=company_id,
                name=name,
                code=code,
                derives_delivery_from_launch=derives,
                sort_order=created,
            )
            db.add(project_type)
            db.flush()
            created += 1

        for template_name in template_names:
            template = (
                db.query(ProjectTemplate)
                .filter(
                    ProjectTemplate.company_id == company_id,
                    ProjectTemplate.type_id == project_type.id,
                    func.lower(ProjectTemplate.name) == template_name.lower(),
                )
                .first()
            )
            if template is None:
                template = ProjectTemplate(
                    id=_uid(),
                    company_id=company_id,
                    type_id=project_type.id,
                    name=template_name,
                )
                db.add(template)
                db.flush()

            # The four roles the client named: decision maker, influencer, info
            # provider (the "whistle blower"), and architect.
            existing_roles = {
                r.name.lower()
                for r in db.query(ProjectTemplateRole)
                .filter(ProjectTemplateRole.template_id == template.id)
                .all()
            }
            for order, role_name in enumerate(DEFAULT_TEMPLATE_ROLES):
                if role_name.lower() in existing_roles:
                    continue
                db.add(
                    ProjectTemplateRole(
                        id=_uid(),
                        company_id=company_id,
                        template_id=template.id,
                        name=role_name,
                        sort_order=order,
                    )
                )
            db.flush()
    return created


def seed_lead_numbering_rule(db: Session) -> bool:
    """``LEAD-000001`` upward, its own sequence separate from ``PRJ-``.

    Same global-sequence caveat as the project rule: ``doc_type`` is unique with no
    company column, so lead codes are globally unique across companies.
    """
    existing = (
        db.query(DocumentNumberingRule)
        .filter(DocumentNumberingRule.doc_type == LEAD_NUMBERING["doc_type"])
        .first()
    )
    if existing:
        return False
    db.add(
        DocumentNumberingRule(
            id=_uid(),
            doc_type=LEAD_NUMBERING["doc_type"],
            enabled=True,
            prefix_template=LEAD_NUMBERING["prefix_template"],
            number_digits=LEAD_NUMBERING["number_digits"],
            next_value=LEAD_NUMBERING["start_value"],
            start_value=LEAD_NUMBERING["start_value"],
            reset_policy="none",
        )
    )
    db.flush()
    return True


def seed_default_lead_graph(db: Session) -> int:
    """The graph for ``project_lead`` (AC-O7). No scoped variants: leads have no template.

    Same wholesale guard as the other two graphs.
    """
    already = (
        db.query(func.count(Status.id))
        .filter(Status.entity_type == PROJECT_LEAD_ENTITY, Status.scope_id.is_(None))
        .scalar()
    )
    if already:
        return 0

    by_key: Dict[str, Status] = {}
    for index, (key, label, initial, terminal) in enumerate(DEFAULT_LEAD_STATUSES):
        row = Status(
            id=_uid(),
            entity_type=PROJECT_LEAD_ENTITY,
            scope_id=None,
            key=key,
            label=label,
            sort_order=index,
            is_initial=initial,
            is_terminal=terminal,
            is_default=initial,
        )
        db.add(row)
        by_key[key] = row
    db.flush()

    for from_key, to_key, label in DEFAULT_LEAD_EDGES:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=PROJECT_LEAD_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key[to_key].id,
                label=label,
                trigger_mode="manual",
            )
        )
    # Disqualifiable from every live rung. A rumour that dies on the first phone call
    # is the common case, not an exception reachable only from the last stage.
    for from_key in _LEAD_LIVE:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=PROJECT_LEAD_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key["disqualified"].id,
                label="Disqualify",
                trigger_mode="manual",
            )
        )
    db.flush()
    return len(by_key)


def seed_quotation_approval_graph(db: Session) -> int:
    """The DEFAULT graph for ``quotation`` (S14). No scoped variants: a price floor is a
    company policy, not a per-template one, so there is nothing to fork on.

    Same wholesale guard as the other three graphs: skipped once any default-scope quotation
    status exists, so a team that renamed "Pending Approval" or pruned a rung keeps their
    change across every restart.
    """
    already = (
        db.query(func.count(Status.id))
        .filter(Status.entity_type == QUOTATION_ENTITY, Status.scope_id.is_(None))
        .scalar()
    )
    if already:
        return 0

    by_key: Dict[str, Status] = {}
    for index, (key, label, initial, terminal) in enumerate(DEFAULT_QUOTATION_STATUSES):
        row = Status(
            id=_uid(),
            entity_type=QUOTATION_ENTITY,
            scope_id=None,
            key=key,
            label=label,
            sort_order=index,
            is_initial=initial,
            is_terminal=terminal,
            is_default=initial,
        )
        db.add(row)
        by_key[key] = row
    db.flush()

    for from_key, to_key, label, sort_order in DEFAULT_QUOTATION_EDGES:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=QUOTATION_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key[to_key].id,
                label=label,
                sort_order=sort_order,
                trigger_mode="manual",
            )
        )
    db.flush()
    return len(by_key)


def seed_sample_request_graph(db: Session) -> int:
    """The DEFAULT graph for ``sample_request`` (SR-CORE). One per install, no scoped
    variants. Same wholesale guard as the other graphs: skipped once any default-scope
    status exists, so a renamed label survives every restart."""
    already = (
        db.query(func.count(Status.id))
        .filter(Status.entity_type == SAMPLE_REQUEST_ENTITY, Status.scope_id.is_(None))
        .scalar()
    )
    if already:
        return 0

    by_key: Dict[str, Status] = {}
    for index, (key, label, initial, terminal) in enumerate(DEFAULT_SAMPLE_REQUEST_STATUSES):
        row = Status(
            id=_uid(),
            entity_type=SAMPLE_REQUEST_ENTITY,
            scope_id=None,
            key=key,
            label=label,
            sort_order=index,
            is_initial=initial,
            is_terminal=terminal,
            is_default=initial,
        )
        db.add(row)
        by_key[key] = row
    db.flush()

    for from_key, to_key, label in DEFAULT_SAMPLE_REQUEST_EDGES:
        db.add(
            StatusTransition(
                id=_uid(),
                entity_type=SAMPLE_REQUEST_ENTITY,
                scope_id=None,
                from_status_id=by_key[from_key].id,
                to_status_id=by_key[to_key].id,
                label=label,
                trigger_mode="manual",
            )
        )
    db.flush()
    return len(by_key)


def seed_lead_disqualify_reasons(db: Session) -> int:
    """The reason lookup set (AC-O6), created empty-safe and never re-asserted.

    Uses the existing generic lookup machinery rather than a bespoke table: an admin
    already has a screen for editing lookup options, and a second reason table would
    need a second screen.
    """
    existing = (
        db.query(LookupSet)
        .filter(LookupSet.set_key == LEAD_DISQUALIFY_REASON_SET_KEY)
        .first()
    )
    if existing:
        return 0

    lookup_set = LookupSet(
        id=_uid(),
        set_key=LEAD_DISQUALIFY_REASON_SET_KEY,
        name="Lead disqualification reasons",
        description=(
            "Why a project lead was disqualified. Read by the lead disqualify action; "
            "the conversion report groups by these."
        ),
    )
    db.add(lookup_set)
    db.flush()
    for order, (value, label) in enumerate(DEFAULT_LEAD_DISQUALIFY_REASONS):
        db.add(
            LookupOption(
                id=_uid(),
                set_id=lookup_set.id,
                value=value,
                label=label,
                sort_order=order,
            )
        )
    db.flush()
    return len(DEFAULT_LEAD_DISQUALIFY_REASONS)


def seed_quotation_loss_reasons(db: Session) -> int:
    """The loss-reason lookup set (AC-E9), on the same generic machinery as the lead
    reasons so an admin edits both in one screen."""
    existing = (
        db.query(LookupSet)
        .filter(LookupSet.set_key == QUOTATION_LOSS_REASON_SET_KEY)
        .first()
    )
    if existing:
        return 0

    lookup_set = LookupSet(
        id=_uid(),
        set_key=QUOTATION_LOSS_REASON_SET_KEY,
        name="Quotation loss reasons",
        description=(
            "Why a project quotation was lost. Mandatory when a quotation is marked "
            "lost; the win/loss report groups by these."
        ),
    )
    db.add(lookup_set)
    db.flush()
    for order, (value, label) in enumerate(DEFAULT_QUOTATION_LOSS_REASONS):
        db.add(
            LookupOption(
                id=_uid(),
                set_id=lookup_set.id,
                value=value,
                label=label,
                sort_order=order,
            )
        )
    db.flush()
    return len(DEFAULT_QUOTATION_LOSS_REASONS)


def _company_ids(db: Session) -> List[str]:
    from app.models.company import Company

    return [row[0] for row in db.query(Company.id).filter(Company.is_active.is_(True)).all()]


def run(db: Session, company_id: Optional[str] = None) -> Dict[str, int]:
    """Seed everything. Safe to call on every boot."""
    summary = {
        "numbering": 0,
        "statuses": 0,
        "task_statuses": 0,
        "lead_numbering": 0,
        "lead_statuses": 0,
        "quotation_statuses": 0,
        "sample_request_statuses": 0,
        "lead_reasons": 0,
        "quotation_loss_reasons": 0,
        "po_edges": 0,
        "win_probabilities": 0,
        "stale_thresholds": 0,
        "types": 0,
    }
    summary["numbering"] = 1 if seed_numbering_rule(db) else 0
    summary["statuses"] = seed_default_funnel(db)
    # Separate from the funnel seeder because that one is wholesale-guarded: an install
    # seeded before AC-F10 existed needs these edges added to a graph it already has.
    summary["po_edges"] = ensure_po_received_edges(db)
    summary["win_probabilities"] = ensure_win_probabilities(db)
    summary["stale_thresholds"] = ensure_stale_thresholds(db)
    summary["task_statuses"] = seed_default_task_graph(db)
    summary["lead_numbering"] = 1 if seed_lead_numbering_rule(db) else 0
    summary["lead_statuses"] = seed_default_lead_graph(db)
    summary["quotation_statuses"] = seed_quotation_approval_graph(db)
    summary["sample_request_statuses"] = seed_sample_request_graph(db)
    summary["lead_reasons"] = seed_lead_disqualify_reasons(db)
    summary["quotation_loss_reasons"] = seed_quotation_loss_reasons(db)

    companies = [company_id] if company_id else _company_ids(db)
    for cid in companies:
        # Types are company-scoped and the auto-filter would hide another company's
        # rows from this query, making the seeder re-create them on every boot.
        from app.models.base import company_scope

        with company_scope(db, frozenset({cid})):
            summary["types"] += seed_types_and_templates(db, cid)

    db.commit()
    if any(summary.values()):
        logger.info("Project Sales seed: %s", summary)
    return summary
