"""System API: chatbot roles, the access registry and per-contact access (ACCESS-MODEL S4).

Roles are dynamic rows (`chatbot_roles`); a role ticks domains and fields from the registry
(`chatbot_domains`, `chatbot_domain_fields`); a contact holds roles plus plus/minus overrides.
Both screens (role page, contact Access tab) write the same rows. Writes go through the ORM
so the automatic audit hooks record them (`audit_service`, default-on for staff writes).

Permissions: reads `user_management.access_agents.view`, role writes
`user_management.reference_data.manage`; contact access GET `user_management.contacts.view`,
PUT `user_management.contacts.edit`.
"""
from __future__ import annotations

import re
from typing import Any

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.access import RespondContact
from app.models.chatbot_access import (
    ChatbotDomainField,
    ChatbotRole,
    ChatbotRoleDomain,
    ChatbotRoleField,
    ContactAccessOverride,
    ContactChatbotRole,
)
from app.models.chatbot_policy import ChatbotDomain
from app.schemas.chatbot_access import (
    ContactAccessResponse,
    ContactAccessUpdate,
    ContactRole,
    EffectiveAccessResponse,
    OverrideItem,
    RegistryDomain,
    RegistryField,
    RegistryResponse,
    RoleContact,
    RoleCreate,
    RoleGrantsUpdate,
    RoleResponse,
    RoleUpdate,
)
from app.services.chatbot.access_tree import tree_for_contact
from app.services.company_scope import get_company_scope
from app.services.user_service import UserPermissionService
from app.services.error_handler import handle_conflict, handle_not_found, handle_unprocessable

router = APIRouter(prefix="/chatbot")

ACCESS_VIEW = "user_management.access_agents.view"
ROLE_MANAGE = "user_management.reference_data.manage"
CONTACT_VIEW = "user_management.contacts.view"
CONTACT_EDIT = "user_management.contacts.edit"


def _read_access(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Any Access tab read: either view slug is enough (ruling Q4), neither is 403."""
    service = UserPermissionService(db)
    if not any(service.check_user_has_permission(current_user["id"], s) for s in (ACCESS_VIEW, CONTACT_VIEW)):
        raise HTTPException(status_code=403, detail=f"Permission required: {ACCESS_VIEW} or {CONTACT_VIEW}")
    return current_user


def _require_manage(db: Session, current_user: dict) -> None:
    """Granting access (a role holder, a plus override) needs the role-manage slug (ruling Q3)."""
    if not UserPermissionService(db).check_user_has_permission(current_user["id"], ROLE_MANAGE):
        raise HTTPException(status_code=403, detail=f"Permission required: {ROLE_MANAGE}")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _actor(current_user: dict) -> str | None:
    return str(current_user.get("id") or "") or None


def _role_or_404(db: Session, role_id: str) -> ChatbotRole:
    try:
        role = db.query(ChatbotRole).filter(ChatbotRole.id == role_id).first()
    except Exception:  # noqa: BLE001 - a malformed id is not a role
        db.rollback()
        role = None
    if role is None:
        raise handle_not_found("Role", role_id)
    return role


def _role_out(db: Session, role: ChatbotRole) -> RoleResponse:
    domains = sorted(
        d for (d,) in db.query(ChatbotRoleDomain.domain_name).filter(ChatbotRoleDomain.role_id == role.id)
    )
    fields = sorted(
        f for (f,) in db.query(ChatbotRoleField.field_key).filter(ChatbotRoleField.role_id == role.id)
    )
    count = db.query(ContactChatbotRole).filter(ContactChatbotRole.role_id == role.id).count()
    return RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        sees_all_customers=bool(role.sees_all_customers),
        audience_tier=role.audience_tier,
        domains=domains,
        fields=fields,
        contact_count=count,
    )


def _refuse_duplicate_name(db: Session, name: str, except_id: str | None = None) -> None:
    q = db.query(ChatbotRole.id).filter(func.lower(ChatbotRole.name) == name.lower())
    if except_id:
        q = q.filter(ChatbotRole.id != except_id)
    if q.first():
        raise handle_conflict(f"A role named {name} already exists.")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "role"


# --- registry -------------------------------------------------------------------------------


def _tier1_names(db: Session) -> dict[tuple[str, str], str]:
    """(agent code, team set code) -> tier-1 team names, sorted, ", " joined.

    Plain SQL: this is an admin read of configuration, so the auto company filter (which
    hides every row from a session with no scope set) is replaced by the admin-listing rule:
    a single-company admin sees that company's rows, an unscoped one sees all.
    """
    scope = get_company_scope(db)
    if scope is not None and not (isinstance(scope, frozenset) and scope):
        return {}  # UNSET or empty: fail closed, no names
    scoped = scope is not None
    rows = db.execute(
        text(
            "SELECT a.code, at.code, t.name FROM access_agents a "
            "JOIN agent_teams at ON at.agent_id = a.id JOIN teams t ON t.id = at.team_id "
            "WHERE at.tier = 1" + (" AND (at.company_id = ANY(CAST(:ids AS uuid[])) OR at.company_id IS NULL)" if scoped else "")
        ),
        {"ids": sorted(scope)} if scoped else {},
    ).fetchall()
    names: dict[tuple[str, str], set[str]] = {}
    for agent, code, name in rows:
        names.setdefault((agent, code), set()).add(name)
    return {k: ", ".join(sorted(v)) for k, v in names.items()}


@router.get("/access/registry", response_model=RegistryResponse)
def get_registry(
    current_user: dict = Depends(_read_access),
    db: Session = Depends(get_db),
):
    _ = current_user
    fields: dict[str, list[ChatbotDomainField]] = {}
    for f in db.query(ChatbotDomainField).order_by(ChatbotDomainField.sort_order, ChatbotDomainField.key):
        fields.setdefault(f.domain_name, []).append(f)
    tier1 = _tier1_names(db)
    agent_names = dict(db.execute(text("SELECT code, name FROM access_agents")).fetchall())
    out = []
    for d in db.query(ChatbotDomain).order_by(ChatbotDomain.sort_order, ChatbotDomain.name):
        team = None
        if d.escalation_agent_code and d.escalation_team_code:
            team = tier1.get((d.escalation_agent_code, d.escalation_team_code))
        out.append(
            RegistryDomain(
                name=d.name,
                label=d.label,
                supported=bool(d.supported),
                access_section=d.access_section,
                group=d.access_group,
                access_label=d.access_label or d.label,
                access_description=d.access_description,
                escalation_agent_code=d.escalation_agent_code,
                escalation_agent_name=agent_names.get(d.escalation_agent_code),
                escalation_team_code=d.escalation_team_code,
                escalation_tier1_team=team,
                fields=[RegistryField(key=f.key, label=f.label, kind=f.kind) for f in fields.get(d.name, [])],
            )
        )
    return RegistryResponse(domains=out)


# --- roles ----------------------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    current_user: dict = Depends(_read_access),
    db: Session = Depends(get_db),
):
    _ = current_user
    roles = db.query(ChatbotRole).order_by(ChatbotRole.sort_order, ChatbotRole.name).all()
    return [_role_out(db, r) for r in roles]


@router.post("/roles", response_model=RoleResponse, status_code=201)
def create_role(
    payload: RoleCreate,
    current_user: dict = Depends(require_permission(ROLE_MANAGE)),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise handle_unprocessable("A role needs a name.")
    _refuse_duplicate_name(db, name)
    base = _slug(name)
    code, n = base, 2
    while db.query(ChatbotRole.id).filter(ChatbotRole.code == code).first():
        code, n = f"{base}_{n}", n + 1
    top = db.query(func.coalesce(func.max(ChatbotRole.sort_order), 0)).scalar() or 0
    role = ChatbotRole(
        code=code,
        name=name,
        description=payload.description,
        sees_all_customers=payload.sees_all_customers,
        audience_tier=payload.audience_tier,
        sort_order=top + 1,
        created_by=_actor(current_user),
        updated_by=_actor(current_user),
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


@router.get("/roles/{role_id}", response_model=RoleResponse)
def get_role(
    role_id: str,
    current_user: dict = Depends(_read_access),
    db: Session = Depends(get_db),
):
    _ = current_user
    return _role_out(db, _role_or_404(db, role_id))


@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    current_user: dict = Depends(require_permission(ROLE_MANAGE)),
    db: Session = Depends(get_db),
):
    role = _role_or_404(db, role_id)
    if payload.name is not None:
        if not payload.name.strip():
            raise handle_unprocessable("A role needs a name.")
        _refuse_duplicate_name(db, payload.name.strip(), role.id)
        role.name = payload.name.strip()
    if payload.description is not None:
        role.description = payload.description
    if payload.sees_all_customers is not None:
        role.sees_all_customers = payload.sees_all_customers
    if payload.clear_audience_tier:
        role.audience_tier = None
    elif payload.audience_tier is not None:
        role.audience_tier = payload.audience_tier
    role.updated_by = _actor(current_user)
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: str,
    current_user: dict = Depends(require_permission(ROLE_MANAGE)),
    db: Session = Depends(get_db),
):
    _ = current_user
    delete_role_row(db, _role_or_404(db, role_id))
    db.commit()
    return Response(status_code=204)


def delete_role_row(db: Session, role: ChatbotRole) -> None:
    """The delete both the route and the deferred `chatbot_role.delete` action run (no commit)."""
    held = db.query(ContactChatbotRole).filter(ContactChatbotRole.role_id == role.id).count()
    if held:
        raise handle_conflict(
            f"This role is held by {held} contact{'s' if held != 1 else ''}. Remove it from them first."
        )
    for model in (ChatbotRoleDomain, ChatbotRoleField):
        for row in db.query(model).filter(model.role_id == role.id).all():
            db.delete(row)
    db.flush()
    db.delete(role)


@router.put("/roles/{role_id}/grants", response_model=RoleResponse)
def put_role_grants(
    role_id: str,
    payload: RoleGrantsUpdate,
    current_user: dict = Depends(require_permission(ROLE_MANAGE)),
    db: Session = Depends(get_db),
):
    role = _role_or_404(db, role_id)
    domains, fields = set(payload.domains), set(payload.fields)
    ticked = {d for (d,) in db.query(ChatbotRoleDomain.domain_name).filter(ChatbotRoleDomain.role_id == role.id)}
    known_d = (
        {
            n
            for (n,) in db.query(ChatbotDomain.name).filter(
                ChatbotDomain.name.in_(domains), ChatbotDomain.supported.is_(True)
            )
        }
        if domains
        else set()
    ) | (domains & ticked)
    known_f = {k for (k,) in db.query(ChatbotDomainField.key).filter(ChatbotDomainField.key.in_(fields))} if fields else set()
    bad = sorted(domains - known_d) + sorted(fields - known_f)
    if bad:
        raise handle_unprocessable(f"Unknown domain or field: {', '.join(bad)}.")
    _replace_ticks(db, role.id, ChatbotRoleDomain, "domain_name", domains)
    _replace_ticks(db, role.id, ChatbotRoleField, "field_key", fields)
    role.updated_by = _actor(current_user)
    db.commit()
    db.refresh(role)
    return _role_out(db, role)


def _replace_ticks(db: Session, role_id: str, model: Any, column: str, wanted: set[str]) -> None:
    """Changed rows only: delete what is no longer ticked, add what is new."""
    have = {getattr(r, column): r for r in db.query(model).filter(model.role_id == role_id).all()}
    for key, row in have.items():
        if key not in wanted:
            db.delete(row)
    for key in wanted - set(have):
        db.add(model(role_id=role_id, **{column: key}))


@router.get("/roles/{role_id}/contacts", response_model=list[RoleContact])
def list_role_contacts(
    role_id: str,
    current_user: dict = Depends(_read_access),
    db: Session = Depends(get_db),
):
    _ = current_user
    role = _role_or_404(db, role_id)
    rows = (
        db.query(RespondContact.id, RespondContact.name)
        .join(ContactChatbotRole, ContactChatbotRole.contact_id == RespondContact.id)
        .filter(ContactChatbotRole.role_id == role.id)
        .order_by(RespondContact.name, RespondContact.id)
        .all()
    )
    return [RoleContact(id=i, name=n) for i, n in rows]


class RoleContactsAdd(BaseModel):
    contact_ids: list[str]


@router.post("/roles/{role_id}/contacts", response_model=list[RoleContact])
def add_role_contacts(
    role_id: str,
    payload: RoleContactsAdd,
    current_user: dict = Depends(require_permission(CONTACT_EDIT)),
    db: Session = Depends(get_db),
):
    _require_manage(db, current_user)
    role = _role_or_404(db, role_id)
    ids = list(dict.fromkeys(payload.contact_ids))
    for cid in ids:
        _contact_or_404(db, cid)
    held = {c for (c,) in db.query(ContactChatbotRole.contact_id).filter(ContactChatbotRole.role_id == role.id)}
    for cid in ids:
        if cid not in held:
            db.add(ContactChatbotRole(contact_id=cid, role_id=role.id))
    db.commit()
    return list_role_contacts(role.id, current_user, db)


@router.delete("/roles/{role_id}/contacts/{contact_id}", status_code=204)
def remove_role_contact(
    role_id: str,
    contact_id: str,
    current_user: dict = Depends(require_permission(CONTACT_EDIT)),
    db: Session = Depends(get_db),
):
    _ = current_user
    role = _role_or_404(db, role_id)
    _contact_or_404(db, contact_id)
    unlink_role_contact(db, role.id, contact_id)
    db.commit()
    return Response(status_code=204)


def unlink_role_contact(db: Session, role_id: str, contact_id: str) -> None:
    """The unlink both the route and the deferred `chatbot_role_contact.unlink` action run (no commit)."""
    for row in db.query(ContactChatbotRole).filter(
        ContactChatbotRole.role_id == role_id, ContactChatbotRole.contact_id == contact_id
    ):
        db.delete(row)


# --- contact access -------------------------------------------------------------------------


def _contact_or_404(db: Session, contact_id: str) -> RespondContact:
    contact = db.query(RespondContact).filter(RespondContact.id == contact_id).first() if _is_uuid(contact_id) else None
    if contact is None:
        raise handle_not_found("Contact", contact_id)
    return contact


def _contact_access_out(db: Session, contact_id: str) -> ContactAccessResponse:
    roles = (
        db.query(ChatbotRole)
        .join(ContactChatbotRole, ContactChatbotRole.role_id == ChatbotRole.id)
        .filter(ContactChatbotRole.contact_id == contact_id)
        .order_by(ChatbotRole.sort_order, ChatbotRole.name)
        .all()
    )
    overrides = (
        db.query(ContactAccessOverride)
        .filter(ContactAccessOverride.contact_id == contact_id)
        .order_by(ContactAccessOverride.domain_name, ContactAccessOverride.field_key)
        .all()
    )
    tree = tree_for_contact(db, contact_id)
    return ContactAccessResponse(
        roles=[ContactRole(id=r.id, code=r.code, name=r.name) for r in roles],
        overrides=[
            OverrideItem(domain_name=o.domain_name, field_key=o.field_key, granted=bool(o.granted))
            for o in overrides
        ],
        effective=EffectiveAccessResponse(
            domains=sorted(tree.domains),
            attributes=list(tree.attributes),
            sees_all_customers=tree.sees_all_customers,
            tiers=sorted(tree.tiers),
        ),
    )


@router.get("/contacts/{contact_id}/access", response_model=ContactAccessResponse)
def get_contact_access(
    contact_id: str,
    current_user: dict = Depends(_read_access),
    db: Session = Depends(get_db),
):
    _ = current_user
    _contact_or_404(db, contact_id)
    return _contact_access_out(db, contact_id)


@router.put("/contacts/{contact_id}/access", response_model=ContactAccessResponse)
def put_contact_access(
    contact_id: str,
    payload: ContactAccessUpdate,
    current_user: dict = Depends(require_permission(CONTACT_EDIT)),
    db: Session = Depends(get_db),
):
    _contact_or_404(db, contact_id)
    role_ids = list(dict.fromkeys(payload.role_ids))
    if not all(_is_uuid(r) for r in role_ids):
        raise handle_unprocessable("Unknown role: malformed id.")
    seen_keys = [(o.domain_name, o.field_key) for o in payload.overrides]
    if len(set(seen_keys)) != len(seen_keys):
        raise handle_unprocessable("Two overrides on the same topic or detail.")
    known_roles = (
        {i for (i,) in db.query(ChatbotRole.id).filter(ChatbotRole.id.in_(role_ids))} if role_ids else set()
    )
    bad = [r for r in role_ids if r not in known_roles]
    domains = {o.domain_name for o in payload.overrides}
    field_keys = {o.field_key for o in payload.overrides if o.field_key}
    known_d = {n for (n,) in db.query(ChatbotDomain.name).filter(ChatbotDomain.name.in_(domains))} if domains else set()
    known_f = (
        {k for (k,) in db.query(ChatbotDomainField.key).filter(ChatbotDomainField.key.in_(field_keys))}
        if field_keys
        else set()
    )
    bad += sorted(domains - known_d) + sorted(field_keys - known_f)
    if bad:
        raise handle_unprocessable(f"Unknown role, domain or field: {', '.join(bad)}.")

    held = {r.role_id: r for r in db.query(ContactChatbotRole).filter(ContactChatbotRole.contact_id == contact_id)}
    plus_now = {
        (o.domain_name, o.field_key)
        for o in db.query(ContactAccessOverride).filter(
            ContactAccessOverride.contact_id == contact_id, ContactAccessOverride.granted.is_(True)
        )
    }
    adds = [r for r in role_ids if r not in held] + [
        k for k, o in ((k, o) for k, o in zip(seen_keys, payload.overrides)) if o.granted and k not in plus_now
    ]
    # Deleting a minus override restores the role's grant, so it counts as a grant.
    minus_dropped = [
        1
        for o in db.query(ContactAccessOverride).filter(
            ContactAccessOverride.contact_id == contact_id, ContactAccessOverride.granted.is_(False)
        )
        if (o.domain_name, o.field_key) not in set(seen_keys)
    ]
    if adds or minus_dropped:
        _require_manage(db, current_user)
    for rid, row in held.items():
        if rid not in role_ids:
            db.delete(row)
    for rid in role_ids:
        if rid not in held:
            db.add(ContactChatbotRole(contact_id=contact_id, role_id=rid))

    wanted: dict[tuple[str, str | None], bool] = {}
    for o in payload.overrides:
        wanted[(o.domain_name, o.field_key)] = o.granted
    existing = {
        (o.domain_name, o.field_key): o
        for o in db.query(ContactAccessOverride).filter(ContactAccessOverride.contact_id == contact_id)
    }
    for key, row in existing.items():
        if key not in wanted:
            db.delete(row)
        elif row.granted != wanted[key]:
            row.granted = wanted[key]
            row.updated_by = _actor(current_user)
    db.flush()
    for (domain, field_key), granted in wanted.items():
        if (domain, field_key) not in existing:
            db.add(
                ContactAccessOverride(
                    contact_id=contact_id,
                    domain_name=domain,
                    field_key=field_key,
                    granted=granted,
                    updated_by=_actor(current_user),
                )
            )
    db.commit()
    return _contact_access_out(db, contact_id)
