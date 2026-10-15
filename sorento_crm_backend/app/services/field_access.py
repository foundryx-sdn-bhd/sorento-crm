"""Which fields an agent may be told, as data rather than as code.

Replaces the first design, which invented a whole access agent
(`container_status_enquiries`) just to unlock a hardcoded list of clearance
fields. That does not scale: a contact asking "when does my container arrive" is
already routed to `incoming_stock_enquiries` (53 contacts hold it), so gating on a
second agent means every one of them needs a second grant, n8n has to know about
an agent it never routes to, and every future sensitive field means another agent.

Here the agent stays the FUNCTION ("incoming stock enquiries") and the fields it
may reveal are rows.

**Three-way answer, not a boolean.** A denial has a reason and the reason matters
to the caller, because "you were never given this function" and "you have the
function but not this field" are different admin actions:

    ALLOWED             visible
    AGENT_NOT_ASSIGNED  the contact does not hold the agent that owns this field
    FIELD_NOT_ALLOWED   the contact holds the agent, but not this field
    CONTACT_NOT_FOUND   no contact matched at all, so no grant was ever consulted
                        (usually a space_id from a different workspace)

**Only registered fields are gated.** `GATED_FIELDS` lists what is protectable per
resource; anything absent from it is always visible. That is what keeps every
existing response byte-identical - the gate can only ever take away fields that
were added deliberately to the registry.

**Default deny for gated fields.** A newly registered sensitive field is invisible
to everyone until an admin adds a row. The alternative - visible until someone
remembers to restrict it - is how the ETA delay ends up in a dealer's chat on
deploy day.

**Enforcement lives in the data endpoint**, not in a check the caller must
remember to make. `check_attributes` exists so n8n can phrase a refusal without
fetching ("I can't share the gatepass date"), but it is a convenience: forgetting
to call it must never be the difference between safe and leaking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- the registry

#: resource -> the fields that CAN be gated on it. A field not listed here is
#: never stripped, which is what guarantees existing responses are unchanged.
#: The agent named alongside is the function that owns the field: it is the agent
#: an admin grants, and the one reported in an AGENT_NOT_ASSIGNED denial.
GATED_FIELDS: dict[str, dict[str, str]] = {
    "incoming_stock": {
        field: "incoming_stock_enquiries"
        for field in (
            "estimated_arrival_date",
            "eta_delay_date",
            "inspection_date",
            "approval_date",
            "gatepass_date",
            "warehouse_arrival_date",
            "informed_collection_date",
            "collection_date",
            "loading_date",
            "etc_date",
            "etd_date",
            "liner_code",
            "china_forwarder",
            "malaysia_forwarder",
            "consignee",
            "delivery_warehouse",
            "free_days_available",
            "loc",
            "stacked",
            "coa_permit_no",
            "source_sheet",
            # Issue #1328: a dealer may not be told the container number or the
            # quantity. Not clearance columns - see `NON_CLEARANCE_KEYS`.
            "shipping_container_number",
            "remaining_incoming_quantity",
        )
    }
}

#: Gated fields that are NOT `InboundShipment` clearance columns, so
#: `incoming_stock_service.CLEARANCE_KEYS` (which selects each gated key as a
#: shipment column) must leave them out: the container number is selected on its
#: own, and the quantity is computed per line.
NON_CLEARANCE_KEYS: frozenset[str] = frozenset(
    {"shipping_container_number", "remaining_incoming_quantity"}
)

#: Denying one field also removes these keys, wherever they sit in the payload.
#: "Quantity" is every number of units on the answer, not one key: a dealer denied
#: the incoming quantity must not read it back off the unallocated gap, a warehouse
#: allocation, or the `/shipments` total.
STRIP_WITH: dict[str, frozenset[str]] = {
    # `/by-product` also states the product's nearest ETA; a revoked ETA takes it too.
    "estimated_arrival_date": frozenset({"nearest_estimated_arrival_date"}),
    "remaining_incoming_quantity": frozenset(
        {"unallocated_quantity", "allocated_quantity", "total_remaining_incoming_quantity"}
    ),
}

#: Fields that ship ALLOWED rather than denied. Default-deny is right for a column
#: nobody could see before; it is wrong for one they already read every day.
#: `estimated_arrival_date` is the ETA the 53 contacts holding
#: `incoming_stock_enquiries` ask about daily, so it is gated (an admin CAN revoke
#: it) but seeded true, and the deploy changes nothing.
#: The container number and the quantity (#1328) ship allowed for the same reason:
#: every contact reads them today, so an admin denies them on the agent (a dealer)
#: rather than the deploy taking them from everyone.
DEFAULT_ALLOWED: frozenset[str] = frozenset(
    {"estimated_arrival_date", "shipping_container_number", "remaining_incoming_quantity"}
)

#: Never gated, and worth naming so nobody adds them by reflex: `shipment_number`,
#: `lines` and `attachment` are the answer itself - a contact who may not see a
#: gatepass date must still be told what is arriving. (The packing list attachment
#: is governed by the contact's own `packing_list_allowed`, `app/services/eta_policy.py`.)

#: Admin-facing labels. The column name is not what an admin deciding "may a
#: dealer be told this" is thinking in, and `loc` / `etc_date` / `coa_permit_no`
#: are unguessable. Falls back to a title-cased key.
FIELD_LABELS: dict[str, str] = {
    "estimated_arrival_date": "ETA",
    "eta_delay_date": "ETA delay",
    "inspection_date": "CIDB inspection",
    "approval_date": "CIDB approval",
    "gatepass_date": "Gatepass",
    "warehouse_arrival_date": "Warehouse arrival",
    "informed_collection_date": "Collection informed",
    "collection_date": "Collection",
    "loading_date": "Loading",
    "etc_date": "ETC (estimated time of container closing)",
    "etd_date": "ETD (estimated time of departure)",
    "liner_code": "Liner",
    "china_forwarder": "China forwarder",
    "malaysia_forwarder": "Malaysia forwarder",
    "consignee": "Consignee",
    "delivery_warehouse": "Delivery warehouse",
    "free_days_available": "Free days available",
    "loc": "Location",
    "stacked": "Stacked",
    "coa_permit_no": "COA permit no.",
    "source_sheet": "Source sheet",
    "shipping_container_number": "Container number",
    "remaining_incoming_quantity": "Quantity",
}


def field_label(field_key: str) -> str:
    return FIELD_LABELS.get(field_key) or field_key.replace("_", " ").capitalize()


#: What a STAFF caller needs instead. Agent grants govern contacts asking through
#: WhatsApp; a logged-in user is governed by RBAC like everything else.
CLEARANCE_PERMISSION = "procurement.packing_lists.view_clearance"

ALLOWED = "allowed"
AGENT_NOT_ASSIGNED = "agent_not_assigned"
FIELD_NOT_ALLOWED = "field_not_allowed"
NOT_GATED = "not_gated"
#: The caller named a contact we cannot find. Distinct from AGENT_NOT_ASSIGNED
#: because the fix is completely different: reporting "assign the agent" for a
#: contact that already holds it sends an admin to a screen where everything
#: already looks correct. Usually a `space_id` from the wrong workspace.
CONTACT_NOT_FOUND = "contact_not_found"


@dataclass(frozen=True)
class FieldDecision:
    field: str
    #: The agent that owns this field, so an admin knows what to grant.
    agent_code: Optional[str]
    outcome: str

    @property
    def allowed(self) -> bool:
        return self.outcome in (ALLOWED, NOT_GATED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "agent_code": self.agent_code,
            "outcome": self.outcome,
            "reason": _REASONS.get(self.outcome),
        }


_REASONS = {
    ALLOWED: None,
    NOT_GATED: None,
    AGENT_NOT_ASSIGNED: (
        "This contact does not hold the agent that owns this field. Assign the "
        "agent to the contact, then allow the field on it."
    ),
    CONTACT_NOT_FOUND: (
        "No contact matches this contact_id. If a space_id was supplied, check it "
        "belongs to the same workspace as the contact - a mismatched pair resolves "
        "to nobody. This is NOT a grant problem: the contact's agents and fields "
        "were never consulted."
    ),
    FIELD_NOT_ALLOWED: (
        "This contact holds the agent, but this field is not allowed on it. Tick "
        "the field on the agent, or add a per-contact override."
    ),
}


# ------------------------------------------------------------------- resolution


def resolve_contact_id(
    db: Session,
    contact_id: str,
    space_id: Optional[str] = None,
    *,
    raise_through: bool = False,
) -> Optional[str]:
    """Accept either id space and return the internal `respond_contacts.id`.

    Grants key on the internal id, but n8n thinks in Respond.io ids and different
    callers reach here having resolved a different amount. Guessing wrong denies a
    contact who is in fact entitled, which reads as a broken feature rather than a
    lookup mismatch, so try both.

    `space_id` disambiguates the Respond.io branch: the same `respond_io_id` can
    exist in two workspaces, and resolving to the wrong one would answer with a
    stranger's grants. Without it, an ambiguous id resolves to nothing rather than
    to a coin flip.

    `raise_through` (default False - byte-for-byte the historic behaviour): a
    lookup FAILURE is swallowed and fail-closed to `None`, the right read for
    a permission/grant check, where "resolution broke" and "resolution found
    nobody" must deny identically. Pass `True` for a caller with its OWN
    generic contract that a broken session surface as the raw exception - a
    record-action handler's `test_every_handler_resolves_its_service_import`
    proves its lazy imports are correctly named this way, and the swallow
    would hide a renamed import exactly as it would hide a real DB failure.
    """
    try:
        from app.models.access import RespondContact
        from app.models.respond_workspace import RespondWorkspace

        key = str(contact_id)
        if db.query(RespondContact.id).filter(RespondContact.id == key).first():
            return key

        q = db.query(RespondContact.id).filter(RespondContact.respond_io_id == key)
        if space_id:
            q = q.join(
                RespondWorkspace, RespondWorkspace.id == RespondContact.workspace_id
            ).filter(RespondWorkspace.space_id == str(space_id))

        rows = q.limit(2).all()
        if len(rows) != 1:
            if len(rows) > 1:
                logger.warning(
                    "respond_io_id %s matches %s contacts and no space_id was given; "
                    "denying rather than picking one",
                    key,
                    len(rows),
                )
            return None
        return rows[0][0]
    except Exception:  # noqa: BLE001 - fail closed by default
        if raise_through:
            raise
        logger.warning("Contact resolution failed for %s", contact_id, exc_info=True)
        return None


def resolve_contact_with_null_workspace_fallback(
    db: Session, *, contact_id: str, space_id: Optional[str] = None
) -> Optional[str]:
    """`resolve_contact_id`, then a fallback for the NULL-workspace gap.

    Measured (review, 7 Sep 2026): 16 contacts have `workspace_id` NULL and 6 sit
    in a non-default workspace. `resolve_contact_id`'s own JOIN against the given
    `space_id` returns zero rows for a NULL-workspace contact - `RespondContact.
    workspace_id` has nothing to join to - so a field reveal granted in the admin
    UI silently read as OFF to the chatbot. Recovered here, not inside
    `resolve_contact_id` itself: that function is also called by
    `field_access.decide()` (agent field-gating), `stock_visibility.py` (policy
    resolution) and `contact_attachment_access.py` / `contact_access_type_service.
    py`, each of which currently reads an unresolved contact as CONTACT_NOT_FOUND /
    default-deny - widening resolution for all of them is a live change to
    already-shipped access boundaries this review did not ask for and a
    security-relevant surface a field-reveal fix should not touch as a side
    effect. The non-default-workspace 6 are left unresolved here too, on purpose:
    that is a genuinely different workspace context, not this bug, and the review's
    fix names the NULL-workspace case only.

    Shared here (not left inside `chatbot.head.access`, its original home) so a
    non-chatbot caller - the spec-fallback resolve route (SF-1, security
    re-verify) - can reach the SAME resolution `check_access` uses without
    importing a chatbot-lane module into a route, a layering smell.
    """
    resolved = resolve_contact_id(db, contact_id, space_id)
    if resolved is not None:
        return resolved

    from app.models.access import RespondContact

    rows = (
        db.query(RespondContact.id)
        .filter(
            RespondContact.respond_io_id == str(contact_id),
            RespondContact.workspace_id.is_(None),
        )
        .limit(2)
        .all()
    )
    if len(rows) != 1:
        if len(rows) > 1:
            logger.warning(
                "respond_io_id %s matches %s NULL-workspace contacts; "
                "denying rather than picking one",
                contact_id,
                len(rows),
            )
        return None
    return rows[0][0]


def contact_agent_codes(db: Session, contact_id: str) -> set[str]:
    """Agent codes this contact currently holds. Fails closed to an empty set."""
    try:
        from app.models.access import AccessAgent, ContactAgentAccess

        now = datetime.utcnow()
        rows = (
            db.query(AccessAgent.code)
            .join(ContactAgentAccess, ContactAgentAccess.agent_id == AccessAgent.id)
            .filter(
                ContactAgentAccess.respond_contact_id == str(contact_id),
                ContactAgentAccess.is_allowed.is_(True),
                AccessAgent.is_active.is_(True),
                or_(
                    ContactAgentAccess.valid_from.is_(None),
                    ContactAgentAccess.valid_from <= now,
                ),
                or_(
                    ContactAgentAccess.valid_to.is_(None),
                    ContactAgentAccess.valid_to >= now,
                ),
            )
            .all()
        )
        return {code for (code,) in rows}
    except Exception:  # noqa: BLE001 - fail closed
        logger.warning(
            "Agent lookup failed for contact %s; treating as holding none",
            contact_id,
            exc_info=True,
        )
        return set()


def allowed_fields_for(
    db: Session, *, contact_id: str, resource: str, agent_codes: Iterable[str]
) -> set[str]:
    """Fields these agents may reveal to this contact.

    A row with a NULL `contact_id` applies to everyone holding the agent - that is
    the normal case. A row WITH this contact's id overrides it, allowing or denying
    that one field for that one contact without disturbing the other 52.
    """
    try:
        from app.models.access import AgentFieldAccess

        codes = list(agent_codes)
        if not codes:
            return set()

        rows = (
            db.query(
                AgentFieldAccess.field_key,
                AgentFieldAccess.contact_id,
                AgentFieldAccess.is_allowed,
            )
            .filter(
                AgentFieldAccess.resource == resource,
                AgentFieldAccess.agent_code.in_(codes),
                or_(
                    AgentFieldAccess.contact_id.is_(None),
                    AgentFieldAccess.contact_id == str(contact_id),
                ),
            )
            .all()
        )
    except Exception:  # noqa: BLE001 - fail closed
        logger.warning("Field-access lookup failed; denying all", exc_info=True)
        return set()

    # Per-contact rows win over the agent-wide default, in either direction.
    default: dict[str, bool] = {}
    override: dict[str, bool] = {}
    for field_key, row_contact_id, is_allowed in rows:
        (override if row_contact_id else default)[field_key] = bool(is_allowed)

    resolved = {**default, **override}
    # DEFAULT_ALLOWED fields are allowed unless a row says otherwise. Seeding alone
    # would not be enough: a database that has never run the bootstrap, or a field
    # added to the set later, would silently withhold an answer contacts already
    # get. An explicit row still wins in both directions, so revoking works.
    for field in DEFAULT_ALLOWED:
        resolved.setdefault(field, True)
    return {field for field, allowed in resolved.items() if allowed}


def explicitly_denied(db: Session, *, contact_id: str, resource: str) -> set[str]:
    """Fields a row explicitly denies this contact, regardless of which agents they
    hold.

    Needed only for `DEFAULT_ALLOWED`. Those fields are allowed even to a contact
    who holds no agent at all - otherwise adding ETA to the registry would REMOVE
    an answer that every caller gets today, which is the opposite of inert. But a
    deliberate revoke must still bite, and `allowed_fields_for` cannot see it
    because it only looks at rows for agents the contact holds.
    """
    try:
        from app.models.access import AgentFieldAccess

        rows = (
            db.query(AgentFieldAccess.field_key, AgentFieldAccess.contact_id, AgentFieldAccess.is_allowed)
            .filter(
                AgentFieldAccess.resource == resource,
                AgentFieldAccess.field_key.in_(DEFAULT_ALLOWED),
                or_(
                    AgentFieldAccess.contact_id.is_(None),
                    AgentFieldAccess.contact_id == str(contact_id),
                ),
            )
            .all()
        )
    except Exception:  # noqa: BLE001
        logger.warning("Default-allow lookup failed; treating as allowed", exc_info=True)
        return set()

    default: dict[str, bool] = {}
    override: dict[str, bool] = {}
    for field_key, row_contact, is_allowed in rows:
        (override if row_contact else default)[field_key] = bool(is_allowed)
    resolved = {**default, **override}
    return {f for f, allowed in resolved.items() if not allowed}


def _tree_attributes(
    db: Session, contact_id: str, space_id: Optional[str]
) -> Optional[frozenset[str]]:
    """The contact's effective access-tree attributes, or None when no contact resolves.

    An internal `respond_contacts.id` names exactly one row; a Respond.io id goes through
    `effective_access`, which applies the strict workspace rule (no NULL-workspace
    fallback) and intersects duplicate rows. Fails closed to None.
    """
    try:
        from sqlalchemy import text

        from app.services.chatbot.access_tree import effective_access, tree_for_contact

        key = str(contact_id)
        row = db.execute(text("SELECT id FROM respond_contacts WHERE id = :k"), {"k": key}).first()
        if row:
            return frozenset(tree_for_contact(db, key).attributes)
        tree = effective_access(db, contact_respond_id=key, space_id=space_id)
        return frozenset(tree.attributes) if tree.resolved else None
    except Exception:  # noqa: BLE001 - fail closed
        logger.warning("Access tree lookup failed for %s; denying", contact_id, exc_info=True)
        return None


def decide(
    db: Session,
    *,
    resource: str,
    fields: Iterable[str],
    contact_id: Optional[str],
    space_id: Optional[str] = None,
) -> list[FieldDecision]:
    """One decision per requested field, each carrying WHY."""
    registry = GATED_FIELDS.get(resource, {})
    requested = list(fields)

    if not contact_id:
        # No contact in play: nothing to resolve a grant against. Same reason as an
        # unresolvable one - claiming "the agent is not assigned" would describe a
        # grant check that never happened.
        return [
            FieldDecision(f, registry.get(f), NOT_GATED if f not in registry else CONTACT_NOT_FOUND)
            for f in requested
        ]

    visible = _tree_attributes(db, contact_id, space_id)
    if visible is None:
        # Denied, but say so as its OWN reason. Reporting a missing grant here sends an
        # admin to check access that was never consulted - and if the contact does hold
        # it, that screen looks perfectly correct, so the real cause (usually a space_id
        # from another workspace) stays hidden.
        return [
            FieldDecision(f, registry.get(f), NOT_GATED if f not in registry else CONTACT_NOT_FOUND)
            for f in requested
        ]

    # S5a (ACCESS-MODEL): for a chat contact the access tree decides. A gated field is
    # visible only while the contact's effective attributes carry `<resource>.<field>`;
    # `agent_field_access` rows and DEFAULT_ALLOWED no longer apply to a contact.
    decisions: list[FieldDecision] = []
    for field in requested:
        owner = registry.get(field)
        if owner is None:
            decisions.append(FieldDecision(field, None, NOT_GATED))
        elif f"{resource}.{field}" in visible:
            decisions.append(FieldDecision(field, owner, ALLOWED))
        else:
            decisions.append(FieldDecision(field, owner, FIELD_NOT_ALLOWED))
    return decisions


def check_attributes(
    db: Session,
    *,
    resource: str,
    attributes: Iterable[str],
    contact_id: Optional[str],
    space_id: Optional[str] = None,
) -> dict[str, Any]:
    """Answer "may this contact be told X?" without returning any data.

    For n8n, so it can refuse in words rather than fetch and find a hole. This is a
    convenience: the data endpoint enforces the same rules independently.
    """
    decisions = decide(
        db,
        resource=resource,
        fields=attributes,
        contact_id=contact_id,
        space_id=space_id,
    )
    return {
        "contact_id": contact_id,
        "resource": resource,
        "all_allowed": all(d.allowed for d in decisions),
        "attributes": [d.as_dict() for d in decisions],
    }


# --------------------------------------------------------------- payload gating


def _strip(payload: Any, drop: set[str]) -> Any:
    if isinstance(payload, dict):
        return {k: _strip(v, drop) for k, v in payload.items() if k not in drop}
    if isinstance(payload, list):
        return [_strip(item, drop) for item in payload]
    return payload


def apply_field_access(
    db: Session,
    payload: Any,
    *,
    resource: str,
    contact_id: Optional[str] = None,
    space_id: Optional[str] = None,
    current_user: Optional[Mapping[str, Any]] = None,
    staff_permission: Optional[str] = None,
) -> Any:
    """Return the payload as this caller may see it, plus why anything is missing.

    Denied fields are ABSENT from the rows - absent means "not permitted", null
    would mean "not reached yet", and an LLM will narrate a null as the second.
    The reason lives in a sibling `field_access` block instead, so the caller can
    say "I can't share that" rather than "that hasn't happened yet".
    """
    registry = GATED_FIELDS.get(resource, {})
    if not registry:
        return payload

    if contact_id:
        decisions = decide(
            db,
            resource=resource,
            fields=registry.keys(),
            contact_id=contact_id,
            space_id=space_id,
        )
    else:
        # A staff caller (a session, or the API key with no contact in play) is
        # governed by an RBAC permission, not by an agent grant.
        entitled = _staff_entitled(db, current_user, staff_permission)
        decisions = [
            FieldDecision(
                f,
                registry[f],
                ALLOWED if (entitled or f in DEFAULT_ALLOWED) else FIELD_NOT_ALLOWED,
            )
            for f in registry
        ]

    denied = [d for d in decisions if not d.allowed]
    if not denied:
        return payload

    drop = {d.field for d in denied}
    for field in list(drop):
        drop |= STRIP_WITH.get(field, frozenset())
    gated = _strip(payload, drop)
    if isinstance(gated, dict):
        gated["field_access"] = {
            "denied": [d.as_dict() for d in denied],
            "note": (
                "These fields are omitted because this caller may not see them. "
                "Absent does NOT mean the value is unknown or not yet reached."
            ),
        }
    return gated


def _staff_entitled(
    db: Session,
    current_user: Optional[Mapping[str, Any]],
    permission: Optional[str],
) -> bool:
    user_id = (current_user or {}).get("id")
    if not user_id or not permission:
        return False
    try:
        from app.services.user_service import UserPermissionService

        return bool(
            UserPermissionService(db).check_user_has_permission(str(user_id), permission)
        )
    except Exception:  # noqa: BLE001 - fail closed
        logger.warning("Permission lookup failed for %s; denying", user_id, exc_info=True)
        return False
