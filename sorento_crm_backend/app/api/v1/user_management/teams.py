"""Teams API for round-robin assignee pools."""
from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import require_permission
from app.services.user_service import TeamService
from app.schemas.user import TeamCreate, TeamUpdate, TeamResponse, TeamMemberResponse, BrandCodesUpdate
from app.schemas.market_segment import MarketSegmentCodesUpdate
from app.services.error_handler import handle_internal_error

router = APIRouter()


@router.get("/", response_model=list[TeamResponse])
async def list_teams(
    current_user: dict = Depends(require_permission("user_management.teams.view")),
    db=Depends(get_db),
):
    """List all teams."""
    try:
        service = TeamService(db)
        return service.list_teams()
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.view")),
    db=Depends(get_db),
):
    """Get a team by ID."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        return service.get_team_view(team_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=TeamResponse, status_code=201)
async def create_team(
    data: TeamCreate,
    current_user: dict = Depends(require_permission("user_management.teams.add")),
    db=Depends(get_db),
):
    """Create a team."""
    try:
        service = TeamService(db)
        return service.create_team(data)
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    data: TeamUpdate,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Update a team."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        return service.update_team(team_id, data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{team_id}", status_code=200)
async def delete_team(
    team_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.delete")),
    db=Depends(get_db),
):
    """Delete a team."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        service.delete_team(team_id)
        return {"message": "Team deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(
    team_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.view")),
    db=Depends(get_db),
):
    """List members of a team."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        return service.list_team_members(team_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{team_id}/members", response_model=TeamMemberResponse, status_code=201)
async def add_team_member(
    team_id: str,
    body: dict,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Add a user to a team. Body: { "user_id": "<id>", "sort_order": optional int,
    "include_in_round_robin": optional bool (default true) }."""
    try:
        validate_uuid_path(team_id, resource="Team")
        user_id = body.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        service = TeamService(db)
        include_rr = body.get("include_in_round_robin")
        return service.add_team_member(
            team_id,
            user_id,
            body.get("sort_order"),
            include_in_round_robin=True if include_rr is None else bool(include_rr),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberResponse)
async def update_team_member(
    team_id: str,
    user_id: str,
    body: dict,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Update a team member. Body: { "include_in_round_robin": optional bool,
    "sort_order": optional int }."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        return service.update_team_member(
            team_id,
            user_id,
            include_in_round_robin=body.get("include_in_round_robin"),
            sort_order=body.get("sort_order"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{team_id}/members/{user_id}", status_code=200)
async def remove_team_member(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Remove a user from a team."""
    try:
        validate_uuid_path(team_id, resource="Team")
        service = TeamService(db)
        service.remove_team_member(team_id, user_id)
        return {"message": "Member removed"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{team_id}/members/{user_id}/market-segments")
async def get_team_member_market_segments(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.view")),
    db=Depends(get_db),
):
    """List the market segments (retail / project) this member serves. Empty = serves all."""
    try:
        validate_uuid_path(team_id, resource="Team")
        from app.services.market_segment_service import MarketSegmentService

        return {
            "codes": MarketSegmentService(db).get_member_segment_codes_by_team_user(
                team_id, user_id
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{team_id}/members/{user_id}/market-segments")
async def set_team_member_market_segments(
    team_id: str,
    user_id: str,
    payload: MarketSegmentCodesUpdate,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Replace the segments this member serves (empty = clear → serves all)."""
    try:
        validate_uuid_path(team_id, resource="Team")
        from app.services.market_segment_service import MarketSegmentService

        return {
            "codes": MarketSegmentService(db).set_member_segments_by_team_user(
                team_id, user_id, payload.codes
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{team_id}/members/{user_id}/brands")
async def get_team_member_brands(
    team_id: str,
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.teams.view")),
    db=Depends(get_db),
):
    """List the brands this member serves. Empty = serves all brands."""
    try:
        validate_uuid_path(team_id, resource="Team")
        from app.services.team_member_brand_service import TeamMemberBrandService

        return {
            "codes": TeamMemberBrandService(db).get_member_brand_codes_by_team_user(
                team_id, user_id
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{team_id}/members/{user_id}/brands")
async def set_team_member_brands(
    team_id: str,
    user_id: str,
    payload: BrandCodesUpdate,
    current_user: dict = Depends(require_permission("user_management.teams.edit")),
    db=Depends(get_db),
):
    """Replace the brands this member serves (empty = clear → serves all)."""
    try:
        validate_uuid_path(team_id, resource="Team")
        from app.services.team_member_brand_service import TeamMemberBrandService

        return {
            "codes": TeamMemberBrandService(db).set_member_brands_by_team_user(
                team_id, user_id, payload.codes
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
