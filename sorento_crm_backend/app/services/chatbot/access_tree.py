"""`effective_access`: the one reader of the chatbot access tree (lane ACCESS-MODEL).

A contact reaches what its roles tick (union), then its overrides apply (a minus beats every
role, a plus adds). No role and no plus override = nothing. A field tick counts only while its
own domain is granted, and a granted domain with a `reveal_key` adds that key to `attributes`
(so today's reveal-key strings keep working at every enforcement seam).

Pure SQL (`text()`), no ORM models, so a migration can call it with a connection. Fails closed:
a read error returns the unresolved, empty value.

Not wired into the chat turn yet (slice S2).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectiveAccess:
    resolved: bool
    domains: frozenset[str]
    attributes: tuple[str, ...]
    sees_all_customers: bool
    roles: tuple[str, ...]
    tiers: frozenset[str]


def _empty(resolved: bool = False) -> EffectiveAccess:
    return EffectiveAccess(
        resolved=resolved,
        domains=frozenset(),
        attributes=(),
        sees_all_customers=False,
        roles=(),
        tiers=frozenset(),
    )


def contact_rows(db: Any, contact_respond_id: str, space_id: str | None) -> list[str]:
    """Every `respond_contacts.id` behind this respond.io id in this workspace.

    Workspace first; only when that finds nothing, the NULL-workspace rows (the same fallback as
    `field_access.resolve_contact_with_null_workspace_fallback`). A contact in a different,
    non-default workspace stays unresolved. With no `space_id` every row of the id counts.
    """
    cid = str(contact_respond_id)
    if not space_id:
        rows = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": cid}
        ).fetchall()
        return [r[0] for r in rows]
    scoped = db.execute(
        text(
            "SELECT c.id FROM respond_contacts c JOIN respond_workspaces w ON w.id = c.workspace_id "
            "WHERE c.respond_io_id = :cid AND w.space_id = :space"
        ),
        {"cid": cid, "space": str(space_id)},
    ).fetchall()
    if scoped:
        return [r[0] for r in scoped]
    rows = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid AND workspace_id IS NULL"),
        {"cid": cid},
    ).fetchall()
    return [r[0] for r in rows]


def tree_for_contact(db: Any, contact_pk: str) -> EffectiveAccess:
    """The effective tree of ONE `respond_contacts` row (no resolution, no intersection)."""
    params = {"c": contact_pk}
    roles = db.execute(
        text(
            "SELECT r.id, r.code, r.sees_all_customers, r.audience_tier FROM contact_chatbot_roles ccr "
            "JOIN chatbot_roles r ON r.id = ccr.role_id WHERE ccr.contact_id = :c"
        ),
        params,
    ).fetchall()
    role_ids = [str(r[0]) for r in roles]
    domains: set[str] = set()
    fields: set[str] = set()
    if role_ids:
        domains = {
            r[0]
            for r in db.execute(
                text("SELECT domain_name FROM chatbot_role_domains WHERE role_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": role_ids},
            ).fetchall()
        }
        fields = {
            r[0]
            for r in db.execute(
                text("SELECT field_key FROM chatbot_role_fields WHERE role_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": role_ids},
            ).fetchall()
        }
    for domain_name, field_key, granted in db.execute(
        text("SELECT domain_name, field_key, granted FROM contact_access_overrides WHERE contact_id = :c"),
        params,
    ).fetchall():
        target = domains if field_key is None else fields
        key = domain_name if field_key is None else field_key
        if granted:
            target.add(key)
        else:
            target.discard(key)
    attributes: set[str] = set()
    if domains:
        listed = list(domains)
        for (key,) in db.execute(
            text("SELECT reveal_key FROM chatbot_domains WHERE name = ANY(:d) AND reveal_key IS NOT NULL"),
            {"d": listed},
        ).fetchall():
            attributes.add(key)
        if fields:
            for (key,) in db.execute(
                text(
                    "SELECT key FROM chatbot_domain_fields "
                    "WHERE domain_name = ANY(:d) AND key = ANY(:f)"
                ),
                {"d": listed, "f": list(fields)},
            ).fetchall():
                attributes.add(key)
    return EffectiveAccess(
        resolved=True,
        domains=frozenset(domains),
        attributes=tuple(sorted(attributes)),
        sees_all_customers=any(bool(r[2]) for r in roles),
        roles=tuple(sorted(r[1] for r in roles)),
        tiers=frozenset(r[3] for r in roles if r[3]),
    )


def effective_access(db: Any, *, contact_respond_id: str, space_id: str | None) -> EffectiveAccess:
    """What this respond.io contact may reach. Two rows on one id in one workspace = the
    intersection of both trees (fail closed, like `turn_runtime.load_profile`)."""
    try:
        pks = contact_rows(db, contact_respond_id, space_id)
        if not pks:
            return _empty(False)
        trees = [tree_for_contact(db, pk) for pk in pks]
    except Exception:  # noqa: BLE001 - an unreadable tree reaches nothing
        logger.warning("chatbot: access tree unreadable for %s", contact_respond_id, exc_info=True)
        return _empty(False)
    first, rest = trees[0], trees[1:]
    if not rest:
        return first
    domains = first.domains.intersection(*(t.domains for t in rest))
    attributes = set(first.attributes).intersection(*(set(t.attributes) for t in rest))
    roles = set(first.roles).intersection(*(set(t.roles) for t in rest))
    return EffectiveAccess(
        resolved=True,
        domains=domains,
        attributes=tuple(sorted(attributes)),
        sees_all_customers=all(t.sees_all_customers for t in trees),
        roles=tuple(sorted(roles)),
        tiers=first.tiers.intersection(*(t.tiers for t in rest)),
    )
