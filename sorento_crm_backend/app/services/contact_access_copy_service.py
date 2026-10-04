"""Copy one contact's access set to many contacts (lane CONTACT-BULK-ACCESS).

Owner job (card `documentation/plans/contacts/CARD-contact-bulk-access-3oct.md`): ~240 dealer
contacts get the same access as a reference contact the owner configured by hand. Copy means
"make the target the same as the source" for every facet below (owner Q2: replace, not add).
Linked customers, companies, CS routing, media limits and memory are never part of it (Q1).

The access set is the `FACETS` tuple, read for many contacts at once (`snapshots`) and compared
per facet (`diff`). The endpoint's dry run, its apply, and the contacts list's
`access_differs_from` filter all go through the same two functions, so the preview, the write
and the filter cannot disagree. Reveals are the effective set: DO keys default ON with no row (owner,
4 Oct 2026). ACCESS-MODEL (#1434) swaps the `field_reveals` and
`agent_access` facets for roles and overrides; nothing else here changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models.access import (
    AccessAgent,
    ContactAccessType,
    ContactAgentAccess,
    RespondContact,
    respond_contact_access_types,
)
from app.services import contact_field_reveal_service
from app.services import audit_service
from app.services.audit_service import audit_event

logger = logging.getLogger(__name__)

MAX_TARGETS = 500

TIER_LABELS = {"dealer": "Dealer", "office": "Office", "end_user": "End user"}

#: The five chatbot switches, column -> label, in card order.
SWITCHES: tuple[tuple[str, str], ...] = (
    ("chatbot_stock_allowed", "Stock checks"),
    ("notify_salesman", "Notify salesman"),
    ("packing_list_allowed", "Packing list"),
    ("chatbot_eta_offset_applied", "ETA buffer days"),
    ("escalation_allowed", "Escalation"),
)

SKIPPED_SOURCE = "This is the source contact."
NOT_FOUND = "Contact not found."
SAVE_FAILED = "Could not save this contact. Nothing was changed for it."

#: `access_differs_from` compares in Python; above this many candidates the list asks for a
#: narrower filter first (security review S2). The whole contacts table is ~250 rows today.
MAX_DIFF_CANDIDATES = 5000


@dataclass
class AgentGrant:
    agent_id: str
    agent_code: str
    agent_name: str
    is_allowed: bool
    valid_from: Any
    valid_to: Any

    def key(self) -> tuple:
        return (self.agent_code, self.is_allowed, _iso(self.valid_from), _iso(self.valid_to))

    def as_dict(self) -> dict:
        return {
            "agent_code": self.agent_code,
            "is_allowed": self.is_allowed,
            "valid_from": _iso(self.valid_from),
            "valid_to": _iso(self.valid_to),
        }


@dataclass
class AccessSnapshot:
    """One contact's access set. Reveals hold only keys this build knows (a key it does not
    know can neither be written nor revoked by `set_granted_keys`, so it is not compared)."""

    contact_id: str
    label: str
    access_types: list[str] = field(default_factory=list)
    tier: Optional[str] = None
    switches: dict[str, bool] = field(default_factory=dict)
    reveals: list[str] = field(default_factory=list)
    agents: dict[str, AgentGrant] = field(default_factory=dict)  # agent_id -> grant


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def contact_label(contact: RespondContact) -> str:
    return contact.name or contact.phone_number


# ---------------------------------------------------------------- read


def snapshots(db: Session, contacts: Iterable[RespondContact]) -> dict[str, AccessSnapshot]:
    """The access set of every contact given, in one query per facet."""
    contacts = list(contacts)
    if not contacts:
        return {}
    ids = [str(c.id) for c in contacts]
    out: dict[str, AccessSnapshot] = {}
    for c in contacts:
        profile = c.chatbot_profile or {}
        out[str(c.id)] = AccessSnapshot(
            contact_id=str(c.id),
            label=contact_label(c),
            tier=profile.get("tier") or None,
            switches={col: bool(getattr(c, col)) for col, _ in SWITCHES},
        )

    for contact_id, code in db.execute(
        select(respond_contact_access_types.c.contact_id, respond_contact_access_types.c.access_type_code)
        .where(respond_contact_access_types.c.contact_id.in_(ids))
    ):
        out[str(contact_id)].access_types.append(code)

    # The EFFECTIVE reveals, so a DO key that is ON by default with no row counts as held
    # (owner, 4 Oct 2026; `contact_field_reveal_service.granted_keys_for`).
    known = {key for key, _ in contact_field_reveal_service.FIELD_REVEAL_KEYS}
    for contact_id, keys in contact_field_reveal_service.granted_keys_for(db, ids).items():
        out[contact_id].reveals.extend(k for k in keys if k in known)

    # Agent grants: by contact id, plus legacy rows keyed only by phone (NULL contact id).
    by_phone = {c.phone_number: str(c.id) for c in contacts}
    rows = (
        db.query(ContactAgentAccess, AccessAgent)
        .join(AccessAgent, AccessAgent.id == ContactAgentAccess.agent_id)
        .filter(
            or_(
                ContactAgentAccess.respond_contact_id.in_(ids),
                ContactAgentAccess.respond_contact_id.is_(None)
                & ContactAgentAccess.respond_contact_phone.in_(list(by_phone)),
            )
        )
        .order_by(ContactAgentAccess.created_at, ContactAgentAccess.id)
        .all()
    )
    for row, agent in rows:
        owner = str(row.respond_contact_id) if row.respond_contact_id else by_phone.get(row.respond_contact_phone)
        if owner is None or owner not in out:
            continue
        # A duplicate row for the same agent: the oldest one is the grant (apply removes the rest).
        out[owner].agents.setdefault(
            str(agent.id),
            AgentGrant(
                agent_id=str(agent.id),
                agent_code=agent.code,
                agent_name=agent.name,
                is_allowed=bool(row.is_allowed),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            ),
        )

    for snap in out.values():
        snap.access_types.sort()
        snap.reveals.sort()
    return out


# ---------------------------------------------------------------- compare


def _change(facet: str, label: str, before: Any, after: Any, added=(), removed=()) -> dict:
    return {
        "facet": facet,
        "label": label,
        "before": before,
        "after": after,
        "added": list(added),
        "removed": list(removed),
    }


def _type_names(db: Session, codes: Iterable[str]) -> dict[str, str]:
    codes = list(codes)
    if not codes:
        return {}
    return dict(
        db.query(ContactAccessType.code, ContactAccessType.name)
        .filter(ContactAccessType.code.in_(codes))
        .all()
    )


def diff(source: AccessSnapshot, target: AccessSnapshot, type_names: dict[str, str]) -> list[dict]:
    """What a copy from `source` changes on `target`, one row per differing facet, in facet order."""
    changes: list[dict] = []

    if source.access_types != target.access_types:
        added = [c for c in source.access_types if c not in target.access_types]
        removed = [c for c in target.access_types if c not in source.access_types]
        changes.append(
            _change(
                "access_types",
                "Access types",
                target.access_types,
                source.access_types,
                [type_names.get(c, c) for c in added],
                [type_names.get(c, c) for c in removed],
            )
        )

    if source.tier != target.tier:
        changes.append(_change("tier", "Tier", target.tier, source.tier))

    for col, label in SWITCHES:
        if source.switches[col] != target.switches[col]:
            changes.append(_change(col, label, target.switches[col], source.switches[col]))

    if source.reveals != target.reveals:
        labels = dict(contact_field_reveal_service.FIELD_REVEAL_KEYS)
        changes.append(
            _change(
                "field_reveals",
                "Field reveals",
                target.reveals,
                source.reveals,
                [labels.get(k, k) for k in source.reveals if k not in target.reveals],
                [labels.get(k, k) for k in target.reveals if k not in source.reveals],
            )
        )

    src_keys = {aid: g.key() for aid, g in source.agents.items()}
    tgt_keys = {aid: g.key() for aid, g in target.agents.items()}
    if src_keys != tgt_keys:
        added = [g.agent_name for aid, g in source.agents.items() if tgt_keys.get(aid) != g.key()]
        removed = [g.agent_name for aid, g in target.agents.items() if src_keys.get(aid) != g.key()]
        order = lambda grants: sorted((g.as_dict() for g in grants), key=lambda d: d["agent_code"])  # noqa: E731
        changes.append(
            _change(
                "agent_access",
                "Agent access",
                order(target.agents.values()),
                order(source.agents.values()),
                sorted(added),
                sorted(removed),
            )
        )
    return changes


def summary(source: AccessSnapshot, type_names: dict[str, str]) -> list[dict]:
    """The source's access set as display lines for the dialog's first step."""
    labels = dict(contact_field_reveal_service.FIELD_REVEAL_KEYS)
    agents = sorted(g.agent_name for g in source.agents.values() if g.is_allowed)
    return [
        {"label": "Access types", "value": ", ".join(type_names.get(c, c) for c in source.access_types)},
        {"label": "Tier", "value": TIER_LABELS.get(source.tier or "", source.tier or "")},
        {
            "label": "Chatbot switches",
            "value": ", ".join(f"{label} {'on' if source.switches[col] else 'off'}" for col, label in SWITCHES),
        },
        {"label": "Field reveals", "value": ", ".join(labels.get(k, k) for k in source.reveals)},
        {"label": "Agent access", "value": ", ".join(agents)},
    ]


# ---------------------------------------------------------------- write


def _write(db: Session, source: AccessSnapshot, target: RespondContact, snap: AccessSnapshot, actor_id: Optional[str]) -> None:
    """Make `target` equal to `source` on every facet. Caller owns the savepoint."""
    target_id = str(target.id)

    if snap.access_types != source.access_types:
        db.execute(
            respond_contact_access_types.delete().where(respond_contact_access_types.c.contact_id == target_id)
        )
        if source.access_types:
            db.execute(
                respond_contact_access_types.insert(),
                [{"contact_id": target_id, "access_type_code": code} for code in source.access_types],
            )

    for col, _label in SWITCHES:
        if getattr(target, col) != source.switches[col]:
            setattr(target, col, source.switches[col])
    db.flush()

    if snap.tier != source.tier:
        # Only the `tier` key: language, ledgers and facts on the profile are the person's own.
        db.execute(
            text(
                "UPDATE respond_contacts SET chatbot_profile = CASE WHEN CAST(:t AS text) IS NULL "
                "THEN chatbot_profile - 'tier' "
                "ELSE jsonb_set(chatbot_profile, '{tier}', to_jsonb(CAST(:t AS text))) END WHERE id = :i"
            ),
            {"t": source.tier, "i": target_id},
        )

    if snap.reveals != source.reveals:
        # Through the module attribute: one writer for reveals, tests patch it here.
        contact_field_reveal_service.set_granted_keys(
            db, target_id, source.reveals, actor_id=actor_id, commit=False
        )

    _write_agents(db, source, target, actor_id)
    db.flush()


def _write_agents(db: Session, source: AccessSnapshot, target: RespondContact, actor_id: Optional[str]) -> None:
    target_id = str(target.id)
    rows = (
        db.query(ContactAgentAccess)
        .filter(
            or_(
                ContactAgentAccess.respond_contact_id == target_id,
                ContactAgentAccess.respond_contact_id.is_(None)
                & (ContactAgentAccess.respond_contact_phone == target.phone_number),
            )
        )
        .order_by(ContactAgentAccess.created_at, ContactAgentAccess.id)
        .all()
    )
    kept: dict[str, ContactAgentAccess] = {}
    for row in rows:
        agent_id = str(row.agent_id)
        if agent_id not in source.agents or agent_id in kept:
            db.delete(row)  # not held by the source, or a duplicate of a kept row
            continue
        kept[agent_id] = row

    for agent_id, grant in source.agents.items():
        row = kept.get(agent_id)
        if row is None:
            db.add(
                ContactAgentAccess(
                    respond_contact_id=target_id,
                    respond_contact_phone=target.phone_number,
                    respond_contact_name=target.name,
                    agent_id=agent_id,
                    is_allowed=grant.is_allowed,
                    valid_from=grant.valid_from,
                    valid_to=grant.valid_to,
                    created_by=actor_id,
                )
            )
            continue
        if (bool(row.is_allowed), _iso(row.valid_from), _iso(row.valid_to)) != (
            grant.is_allowed,
            _iso(grant.valid_from),
            _iso(grant.valid_to),
        ):
            row.is_allowed = grant.is_allowed
            row.valid_from = grant.valid_from
            row.valid_to = grant.valid_to


# ---------------------------------------------------------------- entry point


@audit_event("contact.access_copied")
def copy_access(
    db: Session,
    source: RespondContact,
    target_ids: list[str],
    *,
    dry_run: bool,
    actor_id: Optional[str],
) -> dict:
    """Preview (dry run) or apply a copy from `source` to every id in `target_ids`.

    Each target is written inside its own savepoint: a failure rolls back that target only
    and answers `failed` with the reason; the rest still apply. The caller commits.
    """
    target_ids = list(dict.fromkeys(str(t) for t in target_ids))
    found = {
        str(c.id): c
        for c in db.query(RespondContact).filter(RespondContact.id.in_(target_ids)).all()
    }
    snaps = snapshots(db, [source, *found.values()])
    src = snaps[str(source.id)]
    all_codes = set(src.access_types)
    for s in snaps.values():
        all_codes.update(s.access_types)
    type_names = _type_names(db, all_codes)

    results: list[dict] = []
    for target_id in target_ids:
        contact = found.get(target_id)
        if contact is None:
            results.append(_result(target_id, None, "failed", [], NOT_FOUND))
            continue
        if target_id == src.contact_id:
            results.append(_result(target_id, contact_label(contact), "skipped", [], SKIPPED_SOURCE))
            continue
        snap = snaps[target_id]
        changes = diff(src, snap, type_names)
        if not changes:
            results.append(_result(target_id, snap.label, "unchanged", [], None))
            continue
        if not dry_run:
            savepoint = db.begin_nested()
            try:
                _write(db, src, contact, snap, actor_id)
                # One row per copied contact naming the source: access types (Core pivot
                # writes) and tier (raw UPDATE) are invisible to the ORM audit listeners.
                audit_service.record(
                    db,
                    event="contact.access_copied",
                    entity_type="respond_contacts",
                    entity_id=target_id,
                    old_values={c["facet"]: c["before"] for c in changes},
                    new_values={
                        **{c["facet"]: c["after"] for c in changes},
                        "copied_from_contact_id": src.contact_id,
                    },
                    description=f"Access copied from {src.label}",
                )
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 - every failure becomes that contact's row
                savepoint.rollback()
                # The reason stays in the log: a DB error string carries SQL and other rows' values.
                logger.warning("copy access to contact %s failed: %s", target_id, exc, exc_info=True)
                results.append(_result(target_id, snap.label, "failed", changes, SAVE_FAILED))
                continue
        results.append(_result(target_id, snap.label, "changed", changes, None))

    counts = {k: 0 for k in ("changed", "unchanged", "skipped", "failed")}
    for row in results:
        counts[row["status"]] += 1
    return {
        "dry_run": dry_run,
        "source": {"id": src.contact_id, "label": src.label, "summary": summary(src, type_names)},
        "results": results,
        "counts": counts,
    }


def _result(contact_id: str, label: Optional[str], status: str, changes: list[dict], error: Optional[str]) -> dict:
    return {"contact_id": contact_id, "label": label, "status": status, "changes": changes, "error": error}


def contacts_differing_from(db: Session, source: RespondContact, candidates: list[RespondContact]) -> list[str]:
    """Ids among `candidates` (never the source) a copy from `source` would change (UAC A2.5)."""
    others = [c for c in candidates if str(c.id) != str(source.id)]
    snaps = snapshots(db, [source, *others])
    src = snaps[str(source.id)]
    return [str(c.id) for c in others if diff(src, snaps[str(c.id)], {})]
