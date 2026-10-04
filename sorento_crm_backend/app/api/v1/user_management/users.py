"""Users API routes."""
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, HTTPException, status, Body, Request
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.config import settings as app_settings
from app.services.uuid_path_param import validate_uuid_path
from app.database import get_db
from app.dependencies import get_current_user, require_permission, require_any_permission
from app.models.auth import VerificationToken
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.user import (
    UserCreate,
    UserLookupItem,
    UserResponse,
    UserRoleResponse,
    UserSelectResponse,
    UserUpdate,
)
from app.services.error_handler import AppException, handle_internal_error
from app.services.notification_service import NotificationService
from app.services.product_discontinued_scope_service import serialize_scopes
from app.services.user_service import UserService, UserPermissionService
from app.services.user_avatar_url import resolve_avatar_url_for_client

logger = logging.getLogger(__name__)


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None


class UserRolesUpdateRequest(BaseModel):
    role_ids: list[str]


class UserCompaniesUpdateRequest(BaseModel):
    company_ids: list[str]


class DailySummarySubscriptionUpdateRequest(BaseModel):
    subscribed: bool

router = APIRouter()


@router.get("/", response_model=ListResponse[UserResponse])
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    roleId: Optional[str] = Query(None),
    respond_synced: Optional[str] = Query(None),
    tier: Optional[str] = Query(None, description="Filter by tier (comma-separated, e.g. 1,2,3)"),
    trashed: Optional[str] = Query(None, description="exclude (default), only, or all"),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """Get users with pagination, filtering, and sorting."""
    try:
        service = UserService(db)
        result = service.list_users(
            page=page,
            limit=limit,
            query=query,
            status=status,
            role_id=roleId,
            respond_synced=respond_synced,
            tier=tier,
            trashed=trashed,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc"
        )
        users = result["data"]
        user_ids = [u.id for u in users]
        roles_map = service.get_roles_for_user_ids(user_ids)
        data = []
        for user in users:
            roles = roles_map.get(user.id, [])
            data.append({
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "status": user.status,
                "country": user.country,
                "timezone": user.timezone,
                "contact_number": getattr(user, "contact_number", None),
                "respond_user_id": user.respond_user_id,
                "respond_synced": user.respond_synced,
                "superior_id": user.superior_id,
                "tier": getattr(user, "tier", None),
                "daily_sla_summary_subscribed": getattr(user, "daily_sla_summary_subscribed", True),
                "avatar": resolve_avatar_url_for_client(user.avatar, getattr(user, "avatar_storage_provider", None)),
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "last_sign_in_at": user.last_sign_in_at,
                "email_verified_at": user.email_verified_at,
            # identity S1 (#1280), plan 5.3: Set a/Change password button label,
            # "Needs attention" phone banner. Derived - not a stored column.
            "has_password": bool((user.password or "").strip()),
            "phone_verified_at": getattr(user, "phone_verified_at", None),
                "is_trashed": user.is_trashed,
                "is_protected": user.is_protected,
                "roles": [{"id": r.id, "name": r.name} for r in roles],
                "superior_name": user.superior.name if user.superior else None,
            })
        return {
            "data": data,
            "pagination": result["pagination"],
            "empty": result["empty"],
        }
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_users: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise handle_internal_error(str(e))


@router.get("/select", response_model=list[UserSelectResponse])
async def get_users_select(
    query: Optional[str] = Query(None),
    respond_synced: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter by status, e.g. ACTIVE for active users only"),
    trashed: Optional[str] = Query("exclude", description="exclude (default), only, or all"),
    company_id: Optional[str] = Query(
        None,
        description="Only users holding a grant for this company. Used by the team-member "
        "picker, since team membership requires the grant.",
    ),
    phone: Optional[str] = Query(None, description="S3: exact match on contact_number (normalised)."),
    respond_contact_id: Optional[str] = Query(None, description="S3: exact match on the linked WhatsApp contact."),
    unlinked: Optional[bool] = Query(None, description="S3: only users with no linked WhatsApp contact."),
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """Get users for select dropdowns. Use status=ACTIVE and trashed=exclude for active users only."""
    try:
        service = UserService(db)
        users = service.list_users_select(
            query=query,
            respond_synced=respond_synced,
            status=status,
            trashed=trashed or "exclude",
            company_id=company_id,
            phone=phone,
            respond_contact_id=respond_contact_id,
            unlinked=unlinked,
        )
        return [UserSelectResponse.model_validate(user) for user in users]
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_users_select: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise handle_internal_error(str(e))


@router.get(
    "/lookup",
    response_model=list[UserLookupItem],
    response_model_exclude_none=True,
)
async def get_users_lookup(
    query: Optional[str] = Query(None, description="Matches the name only, never the email."),
    respond_synced: bool = Query(
        False,
        description="Only users linked to a Respond.io agent, each with its respond_user_id.",
    ),
    include_inactive: bool = Query(
        False,
        description="Also deactivated staff, for filters over past records. Never for assigning.",
    ),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The shared people picker: any signed-in staff user, active staff, id + name.

    Owner ruling 1 Oct 2026 (never-stuck L10, AUDIT-never-stuck-2026-10-01 section 5):
    assigning a colleague is a normal action in every module, so owner / assignee / watcher
    pickers must not need ``user_management.users.view``. That slug still guards
    ``/select``, which carries email and Respond.io state and filters by phone for the user
    admin screens. Read-only; nothing here changes who may write.
    """
    service = UserService(db)
    # A portal contact holds a session too (identity S0, phone sign-in); staff names and
    # Respond.io ids are not theirs to list.
    if not service.is_staff(current_user["id"]):
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            message="The people list is for staff accounts.",
            code="people_lookup_staff_only",
        )
    try:
        users = service.list_user_lookup(
            query=query, respond_synced=respond_synced, include_inactive=include_inactive
        )
        return [
            UserLookupItem(
                id=user.id,
                name=user.name,
                respond_user_id=user.respond_user_id if respond_synced else None,
            )
            for user in users
        ]
    except Exception as e:
        logger.error("Error in get_users_lookup: %s", e)
        raise handle_internal_error(str(e))


class BulkUsersRequest(BaseModel):
    user_ids: list[str]
    action: str  # "delete" | "activate" | "deactivate" | "permanent_delete" | "resend_invite"


def _send_invitation_link_for_user(db: Session, user) -> str:
    """
    Create a verification token and send an invitation email for the given user.
    Used by both single-user resend and bulk resend flows.

    A user with no email (phone-only, identity S0) is refused with 400 before any
    token is written: there is nowhere to send the link.
    """
    if not (getattr(user, "email", None) or "").strip():
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message="User has no email",
            code="USER_HAS_NO_EMAIL",
        )
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    verification_token = VerificationToken(
        identifier=user.id,
        token=token,
        expires=expires,
    )
    db.add(verification_token)
    db.commit()

    base_url = (app_settings.frontend_base_url or "").strip().rstrip("/")
    set_password_path = "/change-password"
    invite_link = (
        f"{base_url}{set_password_path}?token={token}"
        if base_url
        else f"{set_password_path}?token={token}"
    )
    try:
        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(db).render_code(
            "user_invitation",
            {"recipient": {"name": user.name or "", "email": user.email or ""}, "invite_link": invite_link},
        )
        NotificationService(db).create_with_channel_preferences(
            user_id=str(user.id),
            type="user_invitation",
            title=rendered["subject"],
            body=rendered["body_text"],
            data={"body_html": rendered["body_html"], "from_name": "Sorento AI System"},
            send_in_app=False,
            send_email=True,
            send_web_push=False,
        )
    except Exception as e:
        logger.warning("Resend invitation email error: %s", e)

    return f"Invitation link sent to {user.email}."


@router.post("/bulk", status_code=status.HTTP_200_OK)
async def bulk_users_action(
    body: BulkUsersRequest,
    current_user: dict = Depends(require_any_permission(["user_management.users.edit", "user_management.users.delete"])),
    db: Session = Depends(get_db)
):
    """Bulk delete (soft), activate, or deactivate users."""
    try:
        service = UserService(db)
        perm_service = UserPermissionService(db)
        from app.services import user_session_service

        if body.action == "delete":
            if not perm_service.check_user_has_permission(current_user["id"], "user_management.users.delete"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied for bulk delete")
            count = service.bulk_delete_users(body.user_ids)
            for uid in body.user_ids:  # boot deleted users off every device
                user_session_service.revoke_all_for_user(db, uid)
            return {"message": f"{count} user(s) deleted", "count": count}
        if body.action == "activate":
            count = service.bulk_update_user_status(body.user_ids, "ACTIVE")
            return {"message": f"{count} user(s) activated", "count": count}
        if body.action == "deactivate":
            count = service.bulk_update_user_status(body.user_ids, "INACTIVE")
            for uid in body.user_ids:  # deactivation must end live sessions
                user_session_service.revoke_all_for_user(db, uid)
            return {"message": f"{count} user(s) deactivated", "count": count}
        if body.action == "permanent_delete":
            if not perm_service.check_user_has_permission(current_user["id"], "user_management.users.delete"):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied for permanent delete")
            count = service.bulk_permanent_delete_users(body.user_ids)
            return {"message": f"{count} trashed user(s) permanently deleted", "count": count}
        if body.action == "resend_invite":
            success = 0
            failed = 0
            skipped = 0
            for user_id in body.user_ids:
                try:
                    user = service.get_user(user_id)
                    # Skipped BEFORE calling the sender (S3 1.6) - there is
                    # nowhere to send it, and it is not a failure.
                    if not (getattr(user, "email", None) or "").strip():
                        skipped += 1
                        continue
                    _send_invitation_link_for_user(db, user)
                    success += 1
                except HTTPException:
                    failed += 1
                except Exception:
                    failed += 1
            message = f"Invitation links sent to {success} user(s)."
            if skipped:
                message += f" {skipped} skipped (no email)."
            if failed:
                message += f" {failed} failed."
            return {
                "message": message,
                "success": success,
                "failed": failed,
                "skipped": skipped,
            }
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid action")
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{user_id}/force-logout", status_code=status.HTTP_200_OK)
async def force_logout_user(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db),
):
    """Admin: revoke every active session for a user, booting them off all devices."""
    from app.services import user_session_service

    count = user_session_service.revoke_all_for_user(db, user_id)
    return {"message": f"Signed out {count} device(s).", "count": count}


@router.patch("/{user_id}/daily-sla-summary-subscription", status_code=status.HTTP_200_OK)
async def update_user_daily_sla_summary_subscription(
    user_id: str,
    body: DailySummarySubscriptionUpdateRequest,
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db),
):
    """Admin update for a user's daily SLA summary subscription."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        user = service.get_user(user_id)
        user.daily_sla_summary_subscribed = bool(body.subscribed)
        db.commit()
        return {"user_id": user.id, "subscribed": bool(user.daily_sla_summary_subscribed)}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/respond-users", status_code=status.HTTP_200_OK)
async def get_respond_users(
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """Get list of Respond.io users for dropdown selection."""
    try:
        from app.services.integration_service import RespondClient
        
        client = RespondClient()
        users = client.list_users(limit=500)
        
        # Transform to a simple format for frontend
        formatted_users = []
        for user in users:
            user_id = user.get("id") or user.get("userId") or user.get("_id")
            name = user.get("name") or user.get("displayName") or user.get("email", "")
            email = user.get("email", "")
            
            if user_id:
                formatted_users.append({
                    "id": str(user_id),
                    "name": name or email or f"User {user_id}",
                    "email": email
                })
        
        return formatted_users
    except ValueError as e:
        # API key not configured
        return []
    except Exception as e:
        # Log error but return empty list to not break the form
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error fetching Respond users: {str(e)}", exc_info=True)
        return []


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user's profile."""
    try:
        from app.services.user_contact_link import sign_in_summary

        service = UserService(db)
        user_id = current_user["id"]
        user = service.get_user(user_id)
        roles = service.list_user_roles(user_id)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "status": user.status,
            "country": user.country,
            "timezone": user.timezone,
            "contact_number": getattr(user, "contact_number", None),
            "respond_user_id": user.respond_user_id,
            "respond_synced": user.respond_synced,
            "superior_id": user.superior_id,
            "tier": getattr(user, "tier", None),
            "respond_contact_id": getattr(user, "respond_contact_id", None),
            "daily_sla_summary_subscribed": getattr(user, "daily_sla_summary_subscribed", True),
            "notify_whatsapp": getattr(user, "notify_whatsapp", False),
            "notify_whatsapp_summary": getattr(user, "notify_whatsapp_summary", False),
            "notify_email_on_assignment": getattr(user, "notify_email_on_assignment", True),
            "notify_email_on_escalation": getattr(user, "notify_email_on_escalation", True),
            "notify_whatsapp_on_assignment": getattr(user, "notify_whatsapp_on_assignment", False),
            "notify_whatsapp_on_escalation": getattr(user, "notify_whatsapp_on_escalation", False),
            "notify_email_on_product_discontinued": getattr(user, "notify_email_on_product_discontinued", False),
            "notify_whatsapp_on_product_discontinued": getattr(user, "notify_whatsapp_on_product_discontinued", False),
            "notify_email_on_deadline_extended": getattr(user, "notify_email_on_deadline_extended", True),
            "notify_whatsapp_on_deadline_extended": getattr(user, "notify_whatsapp_on_deadline_extended", False),
            "notify_email_on_handling": getattr(user, "notify_email_on_handling", True),
            "notify_whatsapp_on_handling": getattr(user, "notify_whatsapp_on_handling", False),
            "notify_email_on_mention": getattr(user, "notify_email_on_mention", True),
            # S3 1.7 - the Sign-in section's five fields, on THIS dict too (LESSONS
            # 93: a field added to one manual builder and not the other never
            # reaches whichever route it was forgotten on).
            **sign_in_summary(db, user),
            # A new User column reaches the FE only through these manual dicts -
            # inheriting it on UserResponse is not enough (UAC AC-M26).
            "notify_push_message_scope": getattr(
                user, "notify_push_message_scope", "assigned_and_coverage"
            ),
            "avatar": resolve_avatar_url_for_client(user.avatar),
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_sign_in_at": user.last_sign_in_at,
            "email_verified_at": user.email_verified_at,
            # identity S1 (#1280), plan 5.3: Set a/Change password button label,
            # "Needs attention" phone banner. Derived - not a stored column.
            "has_password": bool((user.password or "").strip()),
            "phone_verified_at": getattr(user, "phone_verified_at", None),
            "is_trashed": user.is_trashed,
            "is_protected": user.is_protected,
            "roles": [{"id": r.id, "name": r.name} for r in roles],
            "superior_name": user.superior.name if user.superior else None,
            "product_discontinued_scopes": serialize_scopes(db, str(user.id)),
        }
        return UserResponse(**user_dict)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/me/permissions")
async def get_my_permissions(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return current user's effective permission slugs (for frontend RBAC)."""
    try:
        perm_service = UserPermissionService(db)
        slugs = perm_service.get_user_permission_slugs(current_user["id"])
        return {"permissions": list(slugs)}
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{user_id}/roles", response_model=list[UserRoleResponse])
async def get_user_roles(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """List roles assigned to a user."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        roles = service.list_user_roles(user_id)
        return roles
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{user_id}/roles", status_code=status.HTTP_200_OK)
async def set_user_roles(
    user_id: str,
    body: UserRolesUpdateRequest = Body(...),
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Replace user's role assignments with the given role_ids."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        return service.set_user_roles(user_id, body.role_ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{user_id}/companies")
async def get_user_companies(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """List companies granted to a user as [{id,name,code}]."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        return service.list_user_companies(user_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{user_id}/companies", status_code=status.HTTP_200_OK)
async def set_user_companies(
    user_id: str,
    body: UserCompaniesUpdateRequest = Body(...),
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Replace a user's company grants with the given company_ids."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        return service.set_user_companies(user_id, body.company_ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.view")),
    db: Session = Depends(get_db)
):
    """Get a single user by ID."""
    try:
        from app.services.user_contact_link import sign_in_summary

        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        user = service.get_user(user_id)
        roles = service.list_user_roles(user_id)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "status": user.status,
            "country": user.country,
            "timezone": user.timezone,
            "contact_number": getattr(user, "contact_number", None),
            "respond_user_id": user.respond_user_id,
            "respond_synced": user.respond_synced,
            "superior_id": user.superior_id,
            "tier": getattr(user, "tier", None),
            "respond_contact_id": getattr(user, "respond_contact_id", None),
            "daily_sla_summary_subscribed": getattr(user, "daily_sla_summary_subscribed", True),
            "notify_whatsapp": getattr(user, "notify_whatsapp", False),
            "notify_whatsapp_summary": getattr(user, "notify_whatsapp_summary", False),
            "notify_email_on_assignment": getattr(user, "notify_email_on_assignment", True),
            "notify_email_on_escalation": getattr(user, "notify_email_on_escalation", True),
            "notify_whatsapp_on_assignment": getattr(user, "notify_whatsapp_on_assignment", False),
            "notify_whatsapp_on_escalation": getattr(user, "notify_whatsapp_on_escalation", False),
            "notify_email_on_product_discontinued": getattr(user, "notify_email_on_product_discontinued", False),
            "notify_whatsapp_on_product_discontinued": getattr(user, "notify_whatsapp_on_product_discontinued", False),
            "notify_email_on_deadline_extended": getattr(user, "notify_email_on_deadline_extended", True),
            "notify_whatsapp_on_deadline_extended": getattr(user, "notify_whatsapp_on_deadline_extended", False),
            "notify_email_on_handling": getattr(user, "notify_email_on_handling", True),
            "notify_whatsapp_on_handling": getattr(user, "notify_whatsapp_on_handling", False),
            "notify_email_on_mention": getattr(user, "notify_email_on_mention", True),
            # S3 1.7 - the Sign-in section's five fields, on THIS dict too (LESSONS
            # 93: a field added to one manual builder and not the other never
            # reaches whichever route it was forgotten on).
            **sign_in_summary(db, user),
            # A new User column reaches the FE only through these manual dicts -
            # inheriting it on UserResponse is not enough (UAC AC-M26).
            "notify_push_message_scope": getattr(
                user, "notify_push_message_scope", "assigned_and_coverage"
            ),
            "avatar": resolve_avatar_url_for_client(user.avatar),
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_sign_in_at": user.last_sign_in_at,
            "email_verified_at": user.email_verified_at,
            # identity S1 (#1280), plan 5.3: Set a/Change password button label,
            # "Needs attention" phone banner. Derived - not a stored column.
            "has_password": bool((user.password or "").strip()),
            "phone_verified_at": getattr(user, "phone_verified_at", None),
            "is_trashed": user.is_trashed,
            "is_protected": user.is_protected,
            "roles": [{"id": r.id, "name": r.name} for r in roles],
            "superior_name": user.superior.name if user.superior else None,
            "product_discontinued_scopes": serialize_scopes(db, str(user.id)),
        }
        return UserResponse(**user_dict)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: dict = Depends(require_permission("user_management.users.add")),
    db: Session = Depends(get_db)
):
    """Create a new user."""
    try:
        service = UserService(db)
        user = service.create_user(user_data)
        return user
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Update a user."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        user = service.update_user(user_id, user_data)
        # The scopes are rows, not columns, so the ORM instance carries none of
        # them. Attach the serialized list so the PUT answers with what it just
        # saved (names included) instead of an empty one.
        setattr(user, "product_discontinued_scopes", serialize_scopes(db, user_id))
        return user
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: str,
    permanent: bool = Query(False, description="If true, permanently delete (only allowed for trashed users)"),
    current_user: dict = Depends(require_permission("user_management.users.delete")),
    db: Session = Depends(get_db)
):
    """Soft-delete a user (default) or permanently delete if permanent=true and user is trashed."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        if permanent:
            service.permanent_delete_user(user_id)
            return {"message": "User permanently deleted"}
        service.delete_user(user_id)
        return {"message": "User deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{user_id}/restore", status_code=status.HTTP_200_OK)
async def restore_user(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Restore a trashed user (set is_trashed=False)."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        service.restore_user(user_id)
        return {"message": "User restored successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{user_id}/resend-invite", status_code=status.HTTP_200_OK)
async def resend_invite(
    user_id: str,
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Resend invitation email so the user can set their password and sign in."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        user = service.get_user(user_id)
        message = _send_invitation_link_for_user(db, user)
        return {"message": message}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class SyncRespondRequest(BaseModel):
    respond_user_id: Optional[str] = None

@router.post("/{user_id}/sync-respond", status_code=status.HTTP_200_OK)
async def sync_respond_user(
    user_id: str,
    request_data: Optional[SyncRespondRequest] = Body(None),
    current_user: dict = Depends(require_permission("user_management.users.edit")),
    db: Session = Depends(get_db)
):
    """Sync a user with Respond.io."""
    try:
        validate_uuid_path(user_id, resource="User")
        service = UserService(db)
        # Use respond_user_id from request if provided, otherwise use from database
        respond_user_id = request_data.respond_user_id if request_data and request_data.respond_user_id else None
        result = service.sync_respond_user(user_id, respond_user_id=respond_user_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/me/profile", response_model=UserResponse)
async def update_current_user_profile(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user's profile (JSON or multipart form from CRM)."""
    try:
        from app.schemas.user import UserUpdate

        service = UserService(db)
        user_id = current_user["id"]
        content_type = (request.headers.get("content-type") or "").lower()
        update_dict: dict = {}

        if "multipart/form-data" in content_type:
            form = await request.form()
            name_raw = form.get("name")
            if name_raw is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Name is required",
                )
            name_str = name_raw if isinstance(name_raw, str) else str(name_raw)
            name_str = name_str.strip()
            if not name_str or len(name_str) > 50:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Name is required and must be at most 50 characters",
                )
            update_dict["name"] = name_str

            raw_action = form.get("avatarAction")
            if isinstance(raw_action, str):
                avatar_action = raw_action.strip()
            elif raw_action is not None:
                avatar_action = str(raw_action).strip()
            else:
                avatar_action = ""

            avatar_upload = form.get("avatarFile")

            if avatar_action == "remove":
                update_dict["avatar"] = None
            elif avatar_action == "save":
                filename = getattr(avatar_upload, "filename", None) if avatar_upload is not None else None
                if not avatar_upload or not filename:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Avatar file is required when saving a new picture",
                    )
                content = await avatar_upload.read()
                if len(content) > 1024 * 1024:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Avatar must be 1MB or smaller",
                    )
                ctype = getattr(avatar_upload, "content_type", None) or "application/octet-stream"
                if ctype not in ("image/jpeg", "image/png", "image/gif"):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Only JPG, PNG or GIF images are allowed",
                    )
                orig = Path(filename).name or "avatar.jpg"
                stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(orig).stem)[:80] or "avatar"
                ext = (Path(orig).suffix[:10] or ".jpg").lower()
                if ext not in (".jpg", ".jpeg", ".png", ".gif"):
                    ext = ".jpg"
                s3_path = f"avatars/{uuid.uuid4().hex}_{stem}{ext}"

                try:
                    from app.services.storage_router import (
                        cdn_base_url,
                        default_provider,
                        get_backend,
                    )

                    avatar_provider = default_provider()
                    backend = get_backend(avatar_provider)
                    s3_key, _ = backend.upload_file(
                        content, s3_path, content_type=ctype
                    )
                    update_dict["avatar"] = cdn_base_url(avatar_provider, s3_key)
                    update_dict["avatar_storage_provider"] = avatar_provider
                except ValueError as cfg_err:
                    logger.error("Avatar upload configuration error: %s", cfg_err)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="File storage is not configured. Contact an administrator.",
                    )
                except Exception as upload_err:
                    logger.exception("Avatar upload failed: %s", upload_err)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to upload avatar. Please try again.",
                    )
        else:
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid JSON body",
                )
            profile_data = ProfileUpdateRequest(**body)
            if profile_data.name is not None:
                update_dict["name"] = profile_data.name
            if profile_data.avatar is not None:
                update_dict["avatar"] = profile_data.avatar

        if not update_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No profile fields to update",
            )

        update_data = UserUpdate(**update_dict)
        user = service.update_user(user_id, update_data)

        roles = service.list_user_roles(user_id)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "status": user.status,
            "country": user.country,
            "timezone": user.timezone,
            "contact_number": getattr(user, "contact_number", None),
            "respond_user_id": user.respond_user_id,
            "respond_synced": user.respond_synced,
            "superior_id": user.superior_id,
            "tier": getattr(user, "tier", None),
            "respond_contact_id": getattr(user, "respond_contact_id", None),
            "daily_sla_summary_subscribed": getattr(user, "daily_sla_summary_subscribed", True),
            "notify_whatsapp": getattr(user, "notify_whatsapp", False),
            "notify_whatsapp_summary": getattr(user, "notify_whatsapp_summary", False),
            "notify_email_on_assignment": getattr(user, "notify_email_on_assignment", True),
            "notify_email_on_escalation": getattr(user, "notify_email_on_escalation", True),
            "notify_whatsapp_on_assignment": getattr(user, "notify_whatsapp_on_assignment", False),
            "notify_whatsapp_on_escalation": getattr(user, "notify_whatsapp_on_escalation", False),
            "notify_email_on_product_discontinued": getattr(user, "notify_email_on_product_discontinued", False),
            "notify_whatsapp_on_product_discontinued": getattr(user, "notify_whatsapp_on_product_discontinued", False),
            "notify_email_on_deadline_extended": getattr(user, "notify_email_on_deadline_extended", True),
            "notify_whatsapp_on_deadline_extended": getattr(user, "notify_whatsapp_on_deadline_extended", False),
            "notify_email_on_handling": getattr(user, "notify_email_on_handling", True),
            "notify_whatsapp_on_handling": getattr(user, "notify_whatsapp_on_handling", False),
            "notify_email_on_mention": getattr(user, "notify_email_on_mention", True),
            # A new User column reaches the FE only through these manual dicts -
            # inheriting it on UserResponse is not enough (UAC AC-M26).
            "notify_push_message_scope": getattr(
                user, "notify_push_message_scope", "assigned_and_coverage"
            ),
            "avatar": resolve_avatar_url_for_client(user.avatar),
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_sign_in_at": user.last_sign_in_at,
            "email_verified_at": user.email_verified_at,
            # identity S1 (#1280), plan 5.3: Set a/Change password button label,
            # "Needs attention" phone banner. Derived - not a stored column.
            "has_password": bool((user.password or "").strip()),
            "phone_verified_at": getattr(user, "phone_verified_at", None),
            "is_trashed": user.is_trashed,
            "is_protected": user.is_protected,
            "roles": [{"id": r.id, "name": r.name} for r in roles],
            "superior_name": user.superior.name if user.superior else None,
            "product_discontinued_scopes": serialize_scopes(db, str(user.id)),
        }
        return UserResponse(**user_dict)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
