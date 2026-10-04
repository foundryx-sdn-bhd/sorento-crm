"""The project / task / lead status graphs, readable by anyone who can see a project.

Owner ruling 1 Oct 2026 (never-stuck L10, AUDIT-never-stuck-2026-10-01 section 5). The
pipeline board, the status-move buttons on a project and a lead, and the task status dropdown
all read these graphs. The admin route, ``/system/statuses/graph/{entity_type}``, is gated on
``system.statuses.view`` and held by administrators alone, so a salesperson saw "No pipeline
stages configured" and no buttons. Same pattern and same slug as ``quotation-approval-graph``
(``quotation_documents.py``): read-only, the three project-sales graphs and nothing else, no
live record counts. The graphs are still edited only under Setup > Status Graphs.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.modules.projects.status_entities import (
    PROJECT_ENTITY_TYPE,
    PROJECT_LEAD_ENTITY_TYPE,
    PROJECT_TASK_ENTITY_TYPE,
)
from app.schemas.status import StatusGraphResponse, StatusResponse, StatusTransitionResponse
from app.services.error_handler import AppException
from app.services.identifier_resolver import is_uuid
from app.services.status_service import resolve_graph

router = APIRouter()

VIEW = "projects.projects.view"

PROJECT_SALES_GRAPHS = frozenset(
    {PROJECT_ENTITY_TYPE, PROJECT_TASK_ENTITY_TYPE, PROJECT_LEAD_ENTITY_TYPE}
)


@router.get("/status-graph/{entity_type}", response_model=StatusGraphResponse)
async def get_project_sales_status_graph(
    entity_type: str,
    scope_id: Optional[str] = Query(
        default=None,
        description="A project template's forked graph. Falls back to the default when it has not forked.",
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    if entity_type not in PROJECT_SALES_GRAPHS:
        raise AppException(
            status_code=404,
            message="Status graph not found.",
            code="status_graph_not_found",
        )
    # A template id is a uuid; anything else can only ever resolve the default, and would
    # otherwise abort the query on the uuid cast (a 500).
    if scope_id and not is_uuid(scope_id):
        scope_id = None
    graph = resolve_graph(db, entity_type, scope_id)
    return StatusGraphResponse(
        entity_type=entity_type,
        requested_scope_id=scope_id,
        resolved_scope_id=graph.resolved_scope_id,
        is_fork=graph.is_fork,
        statuses=[StatusResponse.model_validate(s) for s in graph.statuses],
        transitions=[StatusTransitionResponse.model_validate(t) for t in graph.transitions],
    )
