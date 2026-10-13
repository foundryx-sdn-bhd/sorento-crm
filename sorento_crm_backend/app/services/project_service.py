"""Project Sales service layer.

Registration is the entry point to the whole module: everything downstream
(quotations, samples, forecast) hangs off a project that someone claimed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.projects import (
    OUTCOME_OPEN,
    Project,
    ProjectBrand,
    ProjectCollaborator,
    ProjectParty,
    ProjectPurchaseOrder,
    ProjectSalesProfile,
    ProjectStakeholder,
    ProjectTakeoverRequest,
    ProjectTemplate,
    ProjectTemplateRole,
    ProjectType,
)
from app.models.status import Status
from app.status_engine.registry import get_status_entity
from app.schemas.common import MAX_PAGE_LIMIT
from app.services.error_handler import AppException
from app.services.numbering_service import NumberingService
from app.services.project_clash_service import (
    ClashCandidate,
    find_clashes,
    normalise_project_title,
)

NUMBERING_DOC_TYPE = "project"


def _describe_blockers(db: Session, blockers: List[ClashCandidate]) -> str:
    """The refusal message. Names the incumbent so the user knows who to talk to.

    "Already registered" on its own is a dead end; the point of the block is to
    route the second person to the first one, or to the request-to-join path.
    """
    owner_ids = [b.owner_user_id for b in blockers if b.owner_user_id]
    names = {}
    if owner_ids:
        from app.models.user import User

        names = {
            u.id: (u.name or u.email)
            for u in db.query(User).filter(User.id.in_(owner_ids)).all()
        }

    parts = []
    for blocker in blockers:
        who = names.get(blocker.owner_user_id) or "an unassigned owner"
        parts.append(f'"{blocker.title}" ({blocker.project_code}), held by {who}')

    joined = "; ".join(parts)
    return (
        f"This development is already registered as {joined}. "
        "Ask to join the project, or raise a dispute for a manager to decide."
    )


def register_project(
    db: Session,
    *,
    company_id: str,
    actor_user_id: str,
    developer_party_id: Optional[str],
    title: str,
    type_id: Optional[str] = None,
    template_id: Optional[str] = None,
    details: Optional[dict] = None,
    brand_ids: Optional[List[str]] = None,
    owner_user_id: Optional[str] = None,
    surface_threshold: Optional[float] = None,
    block_threshold: Optional[float] = None,
) -> Project:
    """Claim a development for ``actor_user_id``.

    Raises 409 when an open project under the same developer is the same
    development. There is no override flag: the recourse is request-to-join or a
    dispute (ADR-0004), both of which leave a record, whereas a force-create flag
    would leave none and would become the default click.

    The clash check runs BEFORE the number is drawn, so a rejected attempt does not
    burn a project code and leave a gap in the sequence.
    """
    clean_title = " ".join((title or "").split())
    if not clean_title:
        raise AppException(
            status_code=422,
            message="A project title is required.",
            code="project_title_required",
        )

    blockers = [
        candidate
        for candidate in find_clashes(
            db,
            company_id=company_id,
            developer_party_id=developer_party_id,
            title=clean_title,
            surface_threshold=surface_threshold,
            block_threshold=block_threshold,
        )
        if candidate.blocks
    ]
    if blockers:
        raise AppException(
            status_code=409,
            message=_describe_blockers(db, blockers),
            code="project_already_registered",
        )

    # commit_rule=False so drawing the number joins the caller's transaction: if the
    # insert below fails, the sequence rolls back with it rather than leaving a hole.
    code = NumberingService(db).get_next_number(NUMBERING_DOC_TYPE, commit_rule=False)
    if not code:
        raise AppException(
            status_code=422,
            message=(
                "No enabled numbering rule for projects. Configure one under "
                "Settings before registering a project."
            ),
            code="project_numbering_rule_missing",
        )

    project = Project(
        company_id=company_id,
        project_code=code,
        title=clean_title,
        normalised_title=normalise_project_title(clean_title),
        developer_party_id=developer_party_id,
        type_id=type_id,
        template_id=template_id,
        outcome=OUTCOME_OPEN,
        owner_user_id=owner_user_id or actor_user_id,
        created_by=actor_user_id,
    )
    db.add(project)
    db.flush()

    # The initial funnel position comes from the graph, so it is configurable and a
    # forked template graph starts its projects on its own first rung. A project with
    # no graph configured yet is left status-less rather than refused: the
    # registration is the valuable part, and the funnel can be set up after.
    from app.services import status_service

    try:
        entity = get_status_entity("project")
        scope_id = entity.scope_for(project) if entity else None
        project.status_id = status_service.initial_status(db, "project", scope_id).id
    except AppException:
        project.status_id = None

    if details or brand_ids is not None:
        apply_sales_details(db, project, details or {}, brand_ids=brand_ids)

    # The template's checklist lands with the project (AC-N1), so a new registration
    # already knows its next action instead of being an empty shell someone has to
    # remember to populate.
    if project.template_id:
        from app.services.project_task_service import instantiate_template_tasks

        instantiate_template_tasks(
            db, project=project, actor_user_id=actor_user_id
        )

    db.flush()
    return project


# ------------------------------------------------------------- edit rights


MANAGE_PERMISSION = "projects.projects.manage"

TAKEOVER_JOIN = "join"
TAKEOVER_DISPUTE = "dispute"
TAKEOVER_KINDS = (TAKEOVER_JOIN, TAKEOVER_DISPUTE)

TAKEOVER_PENDING = "pending"
TAKEOVER_APPROVED = "approved"
TAKEOVER_REJECTED = "rejected"


def _is_collaborator(db: Session, project_id: str, user_id: str) -> bool:
    return (
        db.query(ProjectCollaborator)
        .filter(
            ProjectCollaborator.project_id == project_id,
            ProjectCollaborator.user_id == user_id,
        )
        .first()
        is not None
    )


def can_edit_project(
    db: Session, project: Project, user_id: str, permissions: Set[str]
) -> bool:
    """Owner, approved collaborator, or manager.

    Reading is intentionally wide open (UAC Group J) and is NOT gated here: the module
    exists to make other people's pursuits visible, so a read filter would defeat it.
    """
    if MANAGE_PERMISSION in (permissions or set()):
        return True
    if user_id and project.owner_user_id == user_id:
        return True
    return bool(user_id) and _is_collaborator(db, project.id, user_id)


def assert_can_edit_project(
    db: Session, project: Project, user_id: str, permissions: Set[str]
) -> None:
    if can_edit_project(db, project, user_id, permissions):
        return
    raise AppException(
        status_code=403,
        message=(
            f"{project.project_code} belongs to another salesperson. Ask to join it "
            "as a collaborator, or raise a dispute for a manager to decide."
        ),
        code="project_not_editable",
    )


def _add_collaborator(
    db: Session, project_id: str, user_id: str, granted_by: Optional[str]
) -> None:
    """Idempotent: an already-granted user must not raise on the composite PK."""
    if _is_collaborator(db, project_id, user_id):
        return
    db.add(
        ProjectCollaborator(
            project_id=project_id, user_id=user_id, granted_by=granted_by
        )
    )
    db.flush()


# --------------------------------------------------- join / dispute requests


def create_takeover_request(
    db: Session,
    *,
    project: Project,
    requester_user_id: str,
    kind: str,
    reason: str,
) -> ProjectTakeoverRequest:
    """The recourse path from a blocked registration (AC-C7).

    Hard blocking with no way out produces defensive land-grabbing and pushes the
    argument back into WhatsApp, so both routes exist and both leave a record.
    """
    if kind not in TAKEOVER_KINDS:
        raise AppException(
            status_code=422,
            message="A request must be either a join request or a dispute.",
            code="project_takeover_kind_invalid",
        )
    if not (reason or "").strip():
        raise AppException(
            status_code=422,
            message="Give a reason -- the owner or manager decides on it.",
            code="project_takeover_reason_required",
        )
    if project.owner_user_id and project.owner_user_id == requester_user_id:
        raise AppException(
            status_code=422,
            message=f"You already own {project.project_code}.",
            code="project_takeover_self",
        )

    existing = (
        db.query(ProjectTakeoverRequest)
        .filter(
            ProjectTakeoverRequest.project_id == project.id,
            ProjectTakeoverRequest.requester_user_id == requester_user_id,
            ProjectTakeoverRequest.status == TAKEOVER_PENDING,
        )
        .first()
    )
    if existing:
        raise AppException(
            status_code=409,
            message=(
                f"You already have a request open on {project.project_code}. "
                "Wait for it to be decided."
            ),
            code="project_takeover_already_open",
        )

    request = ProjectTakeoverRequest(
        project_id=project.id,
        requester_user_id=requester_user_id,
        kind=kind,
        reason=reason.strip(),
        status=TAKEOVER_PENDING,
    )
    db.add(request)
    db.flush()
    return request


def decide_takeover_request(
    db: Session,
    *,
    request: ProjectTakeoverRequest,
    decider_user_id: str,
    decider_permissions: Set[str],
    approve: bool,
    decision_note: Optional[str] = None,
) -> ProjectTakeoverRequest:
    """Approve or reject, once.

    The two kinds resolve differently on purpose. A JOIN grants collaborator rights
    and leaves ownership alone. A DISPUTE transfers ownership -- and demotes the
    previous owner to collaborator rather than locking them out, because they did
    real work on the project and the decision was about who leads it, not about
    erasing their access.
    """
    if request.status != TAKEOVER_PENDING:
        raise AppException(
            status_code=409,
            message=f"This request was already {request.status}.",
            code="project_takeover_already_decided",
        )

    project = db.query(Project).filter(Project.id == request.project_id).first()
    if project is None:
        raise AppException(
            status_code=404,
            message="The project this request refers to no longer exists.",
            code="project_not_found",
        )

    is_manager = MANAGE_PERMISSION in (decider_permissions or set())
    is_owner = bool(decider_user_id) and project.owner_user_id == decider_user_id
    # A dispute is a manager decision by definition: letting the incumbent rule on a
    # claim against themselves is not a decision.
    if request.kind == TAKEOVER_DISPUTE:
        allowed = is_manager
    else:
        allowed = is_manager or is_owner
    if not allowed:
        raise AppException(
            status_code=403,
            message=(
                "Only the project owner or a sales manager can decide this request."
                if request.kind == TAKEOVER_JOIN
                else "Only a sales manager can decide a dispute."
            ),
            code="project_takeover_not_decidable",
        )

    request.status = TAKEOVER_APPROVED if approve else TAKEOVER_REJECTED
    request.decided_by = decider_user_id
    request.decided_at = datetime.utcnow()
    request.decision_note = (decision_note or "").strip() or None

    if approve:
        if request.kind == TAKEOVER_JOIN:
            _add_collaborator(
                db, project.id, request.requester_user_id, decider_user_id
            )
        else:
            previous_owner = project.owner_user_id
            project.owner_user_id = request.requester_user_id
            if previous_owner and previous_owner != request.requester_user_id:
                _add_collaborator(db, project.id, previous_owner, decider_user_id)

    db.flush()
    return request


# ------------------------------------------------------------- serialisation


def _name_map(db: Session, user_ids) -> Dict[str, str]:
    ids = [uid for uid in set(user_ids) if uid]
    if not ids:
        return {}
    from app.models.user import User

    return {
        u.id: (u.name or u.email)
        for u in db.query(User).filter(User.id.in_(ids)).all()
    }


def _party_name_map(db: Session, party_ids) -> Dict[str, str]:
    ids = [pid for pid in set(party_ids) if pid]
    if not ids:
        return {}
    return {
        p.id: p.name
        for p in db.query(ProjectParty).filter(ProjectParty.id.in_(ids)).all()
    }


def _brands_for(db: Session, project_ids) -> Dict[str, List[tuple]]:
    """``{project_id: [(brand_id, brand_name), ...]}`` in one query, not N."""
    ids = [pid for pid in set(project_ids) if pid]
    if not ids:
        return {}
    from app.models.product import Brand

    rows = (
        db.query(ProjectBrand.project_id, Brand.id, Brand.brand_name)
        .join(Brand, Brand.id == ProjectBrand.brand_id)
        .filter(ProjectBrand.project_id.in_(ids))
        .order_by(Brand.brand_name)
        .all()
    )
    out: Dict[str, List[tuple]] = {}
    for project_id, brand_id, brand_name in rows:
        out.setdefault(project_id, []).append((brand_id, brand_name))
    return out


def _days_since(moment: Optional[datetime]) -> Optional[int]:
    if moment is None:
        return None
    return max(0, (datetime.utcnow() - moment).days)


def serialize_projects(
    db: Session,
    projects: List[Project],
    *,
    actor_user_id: Optional[str] = None,
    permissions: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Resolve every FK to a human-readable name in bulk.

    Bulk because the pipeline board renders every open project at once, and a
    per-row owner/developer/brand lookup would be a few hundred queries per load.

    ``can_edit`` is computed here so the FE never has to re-derive the ownership
    rule -- a second implementation of it in TypeScript is a second place for it to
    be wrong.
    """
    if not projects:
        return []

    permissions = permissions or set()
    is_manager = MANAGE_PERMISSION in permissions

    owner_names = _name_map(db, [p.owner_user_id for p in projects])
    party_names = _party_name_map(
        db, [p.developer_party_id for p in projects]
    )
    brands = _brands_for(db, [p.id for p in projects])

    profiles = {
        row.project_id: row
        for row in db.query(ProjectSalesProfile)
        .filter(ProjectSalesProfile.project_id.in_([p.id for p in projects]))
        .all()
    }
    profile_party_names = _party_name_map(
        db,
        [pr.architect_party_id for pr in profiles.values()]
        + [pr.main_contractor_party_id for pr in profiles.values()],
    )

    status_ids = [p.status_id for p in projects if p.status_id]
    statuses = (
        {
            s.id: s
            for s in db.query(Status).filter(Status.id.in_(set(status_ids))).all()
        }
        if status_ids
        else {}
    )

    type_ids = [p.type_id for p in projects if p.type_id]
    types = (
        {
            t.id: t.name
            for t in db.query(ProjectType).filter(ProjectType.id.in_(set(type_ids))).all()
        }
        if type_ids
        else {}
    )
    template_ids = [p.template_id for p in projects if p.template_id]
    templates = (
        {
            t.id: t.name
            for t in db.query(ProjectTemplate)
            .filter(ProjectTemplate.id.in_(set(template_ids)))
            .all()
        }
        if template_ids
        else {}
    )

    # Where the pursuit came from, when it came from a lead (AC-O10). Bulk, and only
    # when some project on the page HAS a lead: a directly-registered pipeline pays
    # nothing for a feature it does not use.
    lead_ids = {p.lead_id for p in projects if p.lead_id}
    leads = {}
    if lead_ids:
        from app.models.projects import ProjectLead

        leads = {
            row.id: row
            for row in db.query(ProjectLead).filter(ProjectLead.id.in_(lead_ids)).all()
        }

    # The customer is the lead's buyer (owner, 13 Oct): the Overview shows it and the
    # quotation header defaults its name, phone and Attn from it. Bulk like the rest.
    customers = _customers_for(
        db, [lead.customer_id for lead in leads.values() if lead.customer_id]
    )

    # Next action is DERIVED from the earliest open task (AC-N6). One bulk query for
    # the whole page, because the board renders every open project at once.
    from app.services.project_task_service import next_action_for_projects

    next_actions = next_action_for_projects(db, [p.id for p in projects])

    collaborating = set()
    if actor_user_id and not is_manager:
        collaborating = {
            row.project_id
            for row in db.query(ProjectCollaborator)
            .filter(
                ProjectCollaborator.user_id == actor_user_id,
                ProjectCollaborator.project_id.in_([p.id for p in projects]),
            )
            .all()
        }

    out: List[Dict[str, Any]] = []
    for project in projects:
        status = statuses.get(project.status_id) if project.status_id else None
        profile = profiles.get(project.id)
        project_brands = brands.get(project.id, [])
        out.append(
            {
                "id": project.id,
                "project_code": project.project_code,
                "title": project.title,
                "outcome": project.outcome,
                "loss_reason": project.loss_reason,
                "admin_ref": project.admin_ref,
                "developer_party_id": project.developer_party_id,
                "developer_name": party_names.get(project.developer_party_id),
                "type_id": project.type_id,
                "type_name": types.get(project.type_id),
                "template_id": project.template_id,
                "template_name": templates.get(project.template_id),
                # Null here is meaningful, not missing: the project was registered
                # directly, and the detail page says so rather than showing a blank.
                "lead_id": project.lead_id,
                "lead_code": (
                    leads[project.lead_id].lead_code
                    if project.lead_id in leads
                    else None
                ),
                "lead_source": (
                    leads[project.lead_id].source if project.lead_id in leads else None
                ),
                "lead_created_at": (
                    leads[project.lead_id].created_at
                    if project.lead_id in leads
                    else None
                ),
                "lead_owner_user_id": (
                    leads[project.lead_id].owner_user_id
                    if project.lead_id in leads
                    else None
                ),
                **_customer_fields(
                    customers.get(leads[project.lead_id].customer_id)
                    if project.lead_id in leads
                    else None
                ),
                "status_id": project.status_id,
                "status_key": status.key if status else None,
                "status_label": status.label if status else None,
                "owner_user_id": project.owner_user_id,
                "owner_name": owner_names.get(project.owner_user_id),
                "is_critical": project.is_critical,
                "critical_at": project.critical_at,
                "management_support": project.management_support,
                "management_notes": project.management_notes,
                "registered_company_name": (
                    profile.registered_company_name if profile else None
                ),
                "location": profile.location if profile else None,
                "address": profile.address if profile else None,
                "architect_party_id": profile.architect_party_id if profile else None,
                "architect_name": (
                    profile_party_names.get(profile.architect_party_id)
                    if profile
                    else None
                ),
                "main_contractor_party_id": (
                    profile.main_contractor_party_id if profile else None
                ),
                "main_contractor_name": (
                    profile_party_names.get(profile.main_contractor_party_id)
                    if profile
                    else None
                ),
                "estimated_sales_value": (
                    profile.estimated_sales_value if profile else None
                ),
                "launch_date": profile.launch_date if profile else None,
                "expected_delivery_from": (
                    profile.expected_delivery_from if profile else None
                ),
                "expected_delivery_to": (
                    profile.expected_delivery_to if profile else None
                ),
                "brands": [name for _bid, name in project_brands],
                "brand_ids": [bid for bid, _name in project_brands],
                "last_meaningful_activity_at": project.last_meaningful_activity_at,
                "days_since_last_activity": _days_since(
                    project.last_meaningful_activity_at
                ),
                "next_action_date": next_actions.get(project.id, {}).get(
                    "next_action_date"
                ),
                "next_action_overdue": next_actions.get(project.id, {}).get(
                    "next_action_overdue", False
                ),
                "open_task_count": next_actions.get(project.id, {}).get(
                    "open_task_count", 0
                ),
                # Staleness ladder (AC-H6). Sent as the stamped level rather than recomputed
                # per row: the sweep is what decides a rung, and a list that computed its own
                # answer could disagree with the notification the owner just received.
                "stale_level": int(project.stale_level or 0),
                "stale_reason": project.stale_reason,
                "stale_since": project.stale_since,
                "is_unattended": int(project.stale_level or 0) >= 3,
                "can_edit": (
                    is_manager
                    or (
                        bool(actor_user_id)
                        and project.owner_user_id == actor_user_id
                    )
                    or project.id in collaborating
                ),
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            }
        )
    return out


def serialize_project(
    db: Session,
    project: Project,
    *,
    actor_user_id: Optional[str] = None,
    permissions: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    return serialize_projects(
        db, [project], actor_user_id=actor_user_id, permissions=permissions
    )[0]


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise AppException(
            status_code=404,
            message="Project not found.",
            code="project_not_found",
        )
    return project


# ---------------------------------------------------------------- listing


def status_ids_for_keys(db: Session, keys: List[str]) -> List[str]:
    """Translate funnel KEYS to status ids, across the default graph and every fork.

    Keys, not ids, because `key` is the documented stable identity per entity_type (grill
    finding G3) and a caller (an AI agent, a report, a webhook) should be able to say
    "tendering" without a status-table round trip. Every scope is included: a template that
    forked its graph still has a rung keyed `tendering`, and "which projects are tendering"
    means all of them.

    An unknown key raises 422 naming the valid ones -- returning zero rows would read as
    "nothing is at that stage", which is a different and wrong answer.
    """
    from app.models.status import Status

    wanted = [k.strip() for k in keys if (k or "").strip()]
    if not wanted:
        return []

    rows = (
        db.query(Status.id, Status.key)
        .filter(Status.entity_type == "project", Status.key.in_(wanted))
        .all()
    )
    found = {row[1] for row in rows}
    missing = [k for k in wanted if k not in found]
    if missing:
        known = sorted(
            {
                row[0]
                for row in db.query(Status.key)
                .filter(Status.entity_type == "project")
                .distinct()
                .all()
            }
        )
        raise AppException(
            status_code=422,
            message=(
                f"Unknown project stage {', '.join(repr(k) for k in missing)}. "
                f"Valid stages: {', '.join(known)}."
            ),
            code="project_status_key_unknown",
        )
    return [str(row[0]) for row in rows]


def list_projects(
    db: Session,
    *,
    company_id: str,
    search: Optional[str] = None,
    status_ids: Optional[List[str]] = None,
    outcomes: Optional[List[str]] = None,
    owner_user_ids: Optional[List[str]] = None,
    developer_party_ids: Optional[List[str]] = None,
    project_ids: Optional[List[str]] = None,
    type_ids: Optional[List[str]] = None,
    brand_ids: Optional[List[str]] = None,
    only_critical: bool = False,
    page: int = 1,
    limit: int = 50,
    sort: str = "created_at",
    direction: str = "desc",
) -> tuple:
    """``(projects, total)``.

    Sorting is whitelisted rather than reflected off the model: an arbitrary
    ``sort`` string would let a caller order by any column, including ones the
    listing never exposes.
    """
    query = db.query(Project).filter(Project.company_id == company_id)

    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Project.title).like(needle),
                func.lower(Project.project_code).like(needle),
            )
        )
    if status_ids:
        query = query.filter(Project.status_id.in_(status_ids))
    if outcomes:
        query = query.filter(Project.outcome.in_(outcomes))
    if owner_user_ids:
        query = query.filter(Project.owner_user_id.in_(owner_user_ids))
    if developer_party_ids:
        query = query.filter(Project.developer_party_id.in_(developer_party_ids))
    if project_ids:
        query = query.filter(Project.id.in_(project_ids))
    if type_ids:
        query = query.filter(Project.type_id.in_(type_ids))
    if brand_ids:
        query = query.filter(
            Project.id.in_(
                db.query(ProjectBrand.project_id).filter(
                    ProjectBrand.brand_id.in_(brand_ids)
                )
            )
        )
    if only_critical:
        query = query.filter(Project.is_critical.is_(True))

    total = query.count()

    sortable = {
        "created_at": Project.created_at,
        "updated_at": Project.updated_at,
        "project_code": Project.project_code,
        "title": Project.title,
        "outcome": Project.outcome,
        "last_meaningful_activity_at": Project.last_meaningful_activity_at,
    }
    column = sortable.get(sort, Project.created_at)
    query = query.order_by(
        column.asc() if str(direction).lower() == "asc" else column.desc()
    )

    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 50), MAX_PAGE_LIMIT))
    rows = query.offset((page - 1) * limit).limit(limit).all()
    return rows, total


# ------------------------------------------------------------ profile + edit


def _profile_for(db: Session, project_id: str) -> ProjectSalesProfile:
    profile = (
        db.query(ProjectSalesProfile)
        .filter(ProjectSalesProfile.project_id == project_id)
        .first()
    )
    if profile is None:
        profile = ProjectSalesProfile(project_id=project_id)
        db.add(profile)
        db.flush()
    return profile


_PROFILE_FIELDS = (
    "registered_company_name",
    "location",
    "address",
    "architect_party_id",
    "main_contractor_party_id",
    "estimated_sales_value",
    "launch_date",
    "expected_delivery_from",
    "expected_delivery_to",
)

_PROJECT_FIELDS = (
    "type_id",
    "template_id",
    "loss_reason",
    "admin_ref",
    "management_support",
    "management_notes",
)


def _set_brands(db: Session, project_id: str, brand_ids: List[str]) -> None:
    db.query(ProjectBrand).filter(ProjectBrand.project_id == project_id).delete(
        synchronize_session=False
    )
    for brand_id in dict.fromkeys(brand_ids or []):
        db.add(ProjectBrand(project_id=project_id, brand_id=brand_id))
    db.flush()


def apply_sales_details(
    db: Session,
    project: Project,
    payload: Dict[str, Any],
    *,
    brand_ids: Optional[List[str]] = None,
) -> None:
    """Write the Sorento-specific half plus the shared optional fields.

    Split across two tables (ADR-0003) so the generic skeleton stays portable, which
    is why this helper exists rather than one setattr loop.
    """
    profile_updates = {
        key: payload[key] for key in _PROFILE_FIELDS if key in payload
    }
    if profile_updates:
        profile = _profile_for(db, project.id)
        for key, value in profile_updates.items():
            setattr(profile, key, value)

    for key in _PROJECT_FIELDS:
        if key in payload:
            setattr(project, key, payload[key])

    if brand_ids is not None:
        _set_brands(db, project.id, brand_ids)
    db.flush()


def update_project(
    db: Session,
    project: Project,
    payload: Dict[str, Any],
    *,
    actor_user_id: str,
    permissions: Set[str],
) -> Project:
    """Edit a project. Retitling re-runs the clash check.

    A rename is how the lock would otherwise be bypassed: register "Setia Alam
    Phase 9", then rename it to the colleague's project. The DB unique index catches
    the exact-key case, but only the matcher catches the near-duplicate.
    """
    assert_can_edit_project(db, project, actor_user_id, permissions)

    new_title = payload.get("title")
    new_developer = (
        payload["developer_party_id"]
        if "developer_party_id" in payload
        else project.developer_party_id
    )
    retitling = bool(new_title) and (
        normalise_project_title(new_title) != project.normalised_title
    )
    redeveloping = new_developer != project.developer_party_id

    if retitling or redeveloping:
        effective_title = new_title or project.title
        blockers = [
            candidate
            for candidate in find_clashes(
                db,
                company_id=project.company_id,
                developer_party_id=new_developer,
                title=effective_title,
            )
            if candidate.blocks and candidate.project_id != project.id
        ]
        if blockers:
            raise AppException(
                status_code=409,
                message=_describe_blockers(db, blockers),
                code="project_already_registered",
            )
        if new_title:
            project.title = " ".join(new_title.split())
            project.normalised_title = normalise_project_title(project.title)
        project.developer_party_id = new_developer

    if "owner_user_id" in payload and payload["owner_user_id"]:
        # Reassignment is a manager act. The owner handing their own project to
        # someone else quietly is how accountability for a stalled pursuit vanishes.
        if MANAGE_PERMISSION not in (permissions or set()):
            if payload["owner_user_id"] != project.owner_user_id:
                raise AppException(
                    status_code=403,
                    message="Only a sales manager can reassign a project owner.",
                    code="project_owner_reassign_forbidden",
                )
        project.owner_user_id = payload["owner_user_id"]

    if "is_critical" in payload and payload["is_critical"] is not None:
        set_critical(project, bool(payload["is_critical"]))

    apply_sales_details(
        db, project, payload, brand_ids=payload.get("brand_ids")
    )

    # Present-and-null is an unlink; absent leaves the link alone (#1339).
    if "lead_id" in payload:
        from app.services.project_lead_service import set_project_lead

        set_project_lead(
            db,
            project,
            payload["lead_id"],
            actor_user_id=actor_user_id,
            permissions=permissions,
        )
    return project


def _customers_for(db: Session, customer_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Name, phone and main contact per customer, in two queries for the whole page."""
    ids = {cid for cid in customer_ids if cid}
    if not ids:
        return {}
    from app.models.order import Customer, CustomerContact

    rows = db.query(Customer).filter(Customer.id.in_(ids)).all()
    contacts = {
        row.customer_id: row
        for row in db.query(CustomerContact)
        .filter(
            CustomerContact.customer_id.in_(ids),
            CustomerContact.contact_role == "main",
        )
        .all()
    }
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        contact = contacts.get(row.id)
        out[row.id] = {
            "customer_id": row.id,
            "customer_name": row.customer_name,
            # The office line first, then the mobile, then the main contact's own number.
            "customer_phone": row.phone_number
            or row.mobile_number
            or (contact.phone or contact.mobile if contact else None),
            "customer_attn": contact.full_name if contact else None,
        }
    return out


def _customer_fields(customer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if customer:
        return customer
    return {
        "customer_id": None,
        "customer_name": None,
        "customer_phone": None,
        "customer_attn": None,
    }


def set_critical(project: Project, critical: bool) -> None:
    """AC-G7. Stamps the date only on the transition INTO critical.

    Re-saving a project that is already critical must not reset the clock -- "days in
    final negotiation" is the number management acts on.
    """
    if critical and not project.is_critical:
        project.critical_at = datetime.utcnow()
    if not critical:
        project.critical_at = None
    project.is_critical = critical


def change_status(
    db: Session,
    project: Project,
    *,
    to_status_id: str,
    actor_user_id: str,
    permissions: Set[str],
) -> Project:
    """Move a project along the funnel, through the engine's legality check.

    Server-side (AC-B4): an illegal drag on the board is rejected here regardless of
    what the client sends.
    """
    assert_can_edit_project(db, project, actor_user_id, permissions)

    from app.services import status_service

    entity_type = "project"
    scope_id = None
    entity = get_status_entity(entity_type)
    if entity is not None:
        scope_id = entity.scope_for(project)

    if project.status_id is None:
        # A project with no status yet (imported, or created before its graph was
        # configured) may enter the graph at its initial status only.
        initial = status_service.initial_status(db, entity_type, scope_id)
        if to_status_id != initial.id:
            raise AppException(
                status_code=422,
                message=(
                    f"{project.project_code} has no status yet, so it can only start "
                    f"at '{initial.label}'."
                ),
                code="project_status_not_started",
            )
    else:
        status_service.assert_transition_allowed(
            db, entity_type, project.status_id, to_status_id, scope_id=scope_id
        )

    project.status_id = to_status_id
    db.flush()

    # A funnel move is real work (AC-H2), so it advances the staleness clock and writes the
    # feed row. Done here rather than in the route because the board, the detail page and
    # any future automation all come through this one function.
    from app.services import project_activity_service as activity

    activity.record_project_event(
        db,
        project=project,
        template="stage_changed",
        payload={"to_status_id": str(to_status_id)},
        actor_id=actor_user_id,
    )
    db.flush()
    return project


def delete_project(
    db: Session,
    project: Project,
    *,
    actor_user_id: str,
    permissions: Set[str],
) -> None:
    """Hard delete (AC-G10), blocked once a Project PO exists.

    A project with a customer PO against it is commercial history, and deleting it
    would silently remove revenue from every report that reads it. Archive is the
    action for that case.
    """
    assert_can_edit_project(db, project, actor_user_id, permissions)

    # Through the ORM rather than raw SQL: the table is `projects.purchase_orders` and
    # `public.purchase_orders` is a DIFFERENT, supplier-side table (ADR-0002, ADR-0011),
    # so an unqualified count would silently read procurement's rows. The model carries
    # the schema, and a test fixture's schema translation carries with it.
    po_count = db.query(ProjectPurchaseOrder).filter_by(project_id=project.id).count()
    if po_count:
        raise AppException(
            status_code=409,
            message=(
                f"{project.project_code} has {po_count} purchase order(s) "
                "recorded against it. Archive it instead of deleting."
            ),
            code="project_has_purchase_orders",
        )

    db.delete(project)
    db.flush()


def resolve_user_names(db: Session, user_ids) -> Dict[str, str]:
    """Public alias for the bulk user-name lookup, for route serialisers."""
    return _name_map(db, user_ids)


def serialize_clash_candidates(db: Session, candidates) -> List[Dict[str, Any]]:
    """Render each candidate with enough context to judge it (AC-C6a).

    Owner, status and value are what let someone tell "my colleague's live tender" from
    "a different phase with a similar name". A bare title would make the block look
    arbitrary.

    Lives here rather than in a router because two routers need it: the registration
    preview and the lead qualify preview must describe a clash identically, or the same
    collision reads as two different problems.
    """
    if not candidates:
        return []

    rows_by_id = {
        p.id: p
        for p in db.query(Project)
        .filter(Project.id.in_([c.project_id for c in candidates]))
        .all()
    }
    serialised = serialize_projects(
        db, [rows_by_id[c.project_id] for c in candidates if c.project_id in rows_by_id]
    )
    by_id = {row["id"]: row for row in serialised}

    out: List[Dict[str, Any]] = []
    for candidate in candidates:
        row = by_id.get(candidate.project_id, {})
        out.append(
            {
                "project_id": candidate.project_id,
                "project_code": candidate.project_code,
                "title": candidate.title,
                "outcome": candidate.outcome,
                "status_label": row.get("status_label"),
                "owner_user_id": candidate.owner_user_id,
                "owner_name": row.get("owner_name"),
                "developer_name": row.get("developer_name"),
                "estimated_sales_value": row.get("estimated_sales_value"),
                "brands": row.get("brands", []),
                "last_activity_at": row.get("last_meaningful_activity_at"),
                "similarity": candidate.similarity,
                "blocks": candidate.blocks,
            }
        )
    return out
