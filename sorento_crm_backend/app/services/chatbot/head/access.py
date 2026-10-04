"""The contact-to-agent access check, in process.

n8n POSTs `/external/access-agent/check` and puts the response on `ctx.access`. The port
calls the SAME service that endpoint calls, so the item shape on `ctx.access` is
byte-identical - which matters because `route-turn` spreads it into its own output and
every n8n node downstream of the migration boundary still reads those keys by name.

Not an MCP tool and not an HTTP hop: this is a service call in the same process.
`space_id` comes from the default respond workspace row rather than n8n's hard-coded
`364817` (D5).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.mcp_access_service import evaluate_agent

logger = logging.getLogger(__name__)


def _resolve_contact_with_null_workspace_fallback(
    db: Session, *, contact_id: str, space_id: str | None
) -> str | None:
    """`field_access.resolve_contact_id`, then a fallback for the NULL-workspace gap.

    Moved to `app.services.field_access.resolve_contact_with_null_workspace_
    fallback` (security re-verify, SF-1): a non-chatbot caller - the spec-
    fallback resolve route - needs the SAME resolution `check_access` uses,
    and importing a chatbot-lane module into a route is a layering smell. This
    is a thin wrapper so every existing call site in this module is unchanged.
    """
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    return resolve_contact_with_null_workspace_fallback(
        db, contact_id=contact_id, space_id=space_id
    )


def _granted_field_reveal_keys(db: Session, *, contact_id: str, space_id: str | None) -> list[str]:
    """This contact's granted field-reveal keys (chatbot growth r1, Slice C).

    A SEPARATE resolution from `evaluate_agent`'s (which does not return the
    internal `respond_contacts.id` a field-reveal grant is keyed on) rather than
    a change to it - `evaluate_agent` is the access-agent decision n8n's
    `/external/access-agent/check` also calls, and this stays untouched (Slice C
    decision). Fails closed to `[]`: an unresolvable contact reveals nothing, the
    same posture `field_access.py::resolve_contact_id` takes.
    """
    try:
        from app.services.contact_field_reveal_service import granted_keys

        resolved = _resolve_contact_with_null_workspace_fallback(
            db, contact_id=contact_id, space_id=space_id
        )
        if resolved is None:
            return []
        return granted_keys(db, resolved)
    except Exception:  # noqa: BLE001 - a lookup failure must fail closed, not fail the turn
        logger.warning("chatbot: field-reveal lookup failed for %s", contact_id, exc_info=True)
        return []


def contact_reveal_grants(db: Session, *, contact_id: str, space_id: str | None) -> list[str]:
    """The contact's reveal grants, the SAME read `check_access` puts in `attributes`
    (so the parser prompt's audience is what every refusal reads), without an access
    decision. Fails closed to `[]`, the minimal audience (PARSER-PER-AUDIENCE)."""
    return _granted_field_reveal_keys(db, contact_id=contact_id, space_id=space_id)


def _hidden_spec_keys(db: Session, *, contact_id: str, space_id: str | None) -> list[str]:
    """This contact's hidden spec keys, sorted (PLAN-spec-visibility-policy.md
    "Chatbot seam"). Resolved once per turn, the SAME contact resolution field
    reveals use (the NULL-workspace fallback) - but unlike field reveals, this
    FAILS CLOSED TO THE DEFAULT POLICY, never to `[]`: a spec question always
    gets an answer, so an unresolvable contact gets the default's hidden set
    rather than "nothing hidden".

    The registry read is the FULL one (B1, security review), not active-only:
    the hidden set is a READ, and a merely deactivated key must stay hidden or
    stay shown, whichever the stored policy already says.
    """
    from app.services.spec_visibility import (
        DEFAULT_HIDDEN_KEYS,
        default_policy,
        full_registry_rows,
        hidden_keys,
        resolve_policy,
    )

    try:
        resolved = _resolve_contact_with_null_workspace_fallback(
            db, contact_id=contact_id, space_id=space_id
        )
        policy = resolve_policy(db, resolved, space_id) if resolved is not None else default_policy(db)
        registry_keys = {key for key, _label in full_registry_rows(db)}
        return sorted(hidden_keys(policy, registry_keys))
    except Exception:  # noqa: BLE001 - a lookup failure must fail closed to the default policy
        logger.warning("chatbot: spec visibility lookup failed for %s", contact_id, exc_info=True)
        try:
            registry_keys = {key for key, _label in full_registry_rows(db)}
            return sorted(hidden_keys(default_policy(db), registry_keys))
        except Exception:  # noqa: BLE001 - the registry itself is unreachable too (S1: still
            # closed, never `[]` - a spec question always gets an answer, and the ship-closed
            # floor is a Python constant, not a query, so it survives even this).
            return sorted(DEFAULT_HIDDEN_KEYS)


def check_access(
    db: Session,
    *,
    agent_code: Any,
    contact_id: str,
    space_id: str | None,
) -> dict[str, Any]:
    """`ctx.access`: `{allowed, decision, agent_name, attributes,
    all_attributes_allowed, hidden_spec_keys}`.

    `attributes` is this contact's granted field-reveal keys (`[]` when none) and
    `all_attributes_allowed` is always `False`: nothing here is an "everything"
    grant, only named keys. `output_structurer` (Slice A/C) drops any field whose
    `restricted` key is absent from this list.

    An unknown agent fails CLOSED (`deny_unknown_agent`) - `deriveRouting`'s `ideate` case
    is the single source of truth for that agent name, and the denial message is rendered
    from the same field, so a stale agent cannot make the refusal say the wrong thing.
    """
    decision = evaluate_agent(
        db,
        agent_code=agent_code,
        contact_id=contact_id,
        space_id=space_id,
    )
    return {
        "allowed": decision.allowed,
        "decision": decision.decision,
        "agent_name": decision.agent_name,
        "attributes": _granted_field_reveal_keys(db, contact_id=contact_id, space_id=space_id),
        "all_attributes_allowed": False,
        "hidden_spec_keys": _hidden_spec_keys(db, contact_id=contact_id, space_id=space_id),
    }


def default_space_id(db: Session) -> str | None:
    """The default respond workspace's `space_id` (D5, kills the hard-coded 364817)."""
    from app.services.respond_workspace_service import RespondWorkspaceService

    try:
        workspace = RespondWorkspaceService(db).get_default()
    except Exception:  # noqa: BLE001 - a missing workspace must not fail the turn here
        logger.warning("chatbot: default respond workspace lookup failed", exc_info=True)
        return None
    space_id = getattr(workspace, "space_id", None) if workspace is not None else None
    return str(space_id) if space_id else None
