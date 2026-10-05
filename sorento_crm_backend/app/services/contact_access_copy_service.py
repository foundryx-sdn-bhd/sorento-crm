"""Copy one contact's access set to many contacts (lane CONTACT-BULK-ACCESS).

Owner job (card `documentation/plans/contacts/CARD-contact-bulk-access-3oct.md`): ~240 dealer
contacts get the same access as a reference contact the owner configured by hand. Copy means
"make the target the same as the source" for every facet below (owner Q2: replace, not add).
Linked customers, CS routing, media limits and memory are never part of it (Q1). Companies, market
segments and the contact's stock visibility override (stock check mode + locations) joined the set
on 4 Oct 2026 (lane CONTACT-COPY-FIELDS); `facets_to_skip` leaves a facet out for an actor who could not set
it directly, so the copy never widens what that actor can change.

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
    MarketSegment,
    RespondContact,
    StockVisibilityPolicy,
    respond_contact_access_types,
    respond_contact_market_segments,
)
from app.models.company import Company, RespondContactCompany
from app.services import contact_field_reveal_service
from app.services import audit_service
from app.services.audit_service import audit_event

logger = logging.getLogger(__name__)

MAX_TARGETS = 500

TIER_LABELS = {"dealer": "Dealer", "office": "Office", "end_user": "End user"}

#: The six chatbot switches, column -> label, in card order.
SWITCHES: tuple[tuple[str, str], ...] = (
    ("chatbot_stock_allowed", "Stock checks"),
    ("notify_salesman", "Notify salesman"),
    ("packing_list_allowed", "Packing list"),
    ("chatbot_eta_offset_applied", "ETA buffer days"),
    ("escalation_allowed", "Escalation"),
    ("chatbot_allowed", "AI replies"),
)

REGION_ORDER = ("west", "east")
REGION_LABELS = {"west": "West Malaysia", "east": "East Malaysia"}

REMOVED_LOCATION = "removed location"
REMOVED_COMPANY = "removed company"
STOCK_MODE_LABELS = {"detailed": "Detailed", "compact": "Compact", "availability": "Availability only"}

#: Facets an actor may only copy when they could set them directly; the route names the ones to
#: leave out (`skip`). Companies: superadmin/admin (`PUT /contacts/{id}/companies`). Stock check
#: mode + locations: `inventory.stock.edit` (`PUT /inventory/stock-visibility/contacts/{id}`).
STOCK_FACETS = frozenset({"stock_check_mode", "stock_locations"})
COMPANIES_FACET = "companies"

#: Always named under "Not copied" in the dialog; a skipped facet's label is added to it.
NOT_COPIED = ("Linked customers", "CS routing", "Media limits", "Memory")
FACET_LABELS = {
    "stock_check_mode": "Stock check mode",
    "stock_locations": "Locations",
    "companies": "Companies",
    "market_segments": "Market segments",
}


def facets_to_skip(db: Session, user_id: str) -> frozenset[str]:
    """The facets `user_id` could not set directly, so the copy must not set them either."""
    from app.api.v1.system.companies import _is_superadmin
    from app.services.user_service import UserPermissionService

    skip: set[str] = set()
    is_admin = _is_superadmin(db, user_id)
    if not is_admin:
        skip.add(COMPANIES_FACET)
    if not UserPermissionService(db).check_user_has_permission(user_id, "inventory.stock.edit"):
        skip |= STOCK_FACETS
    elif not is_admin and _inventory_module_off(db):
        # `require_permission` on the direct editor also refuses a non-admin when the strict
        # module guard is on and the inventory module is disabled; the copy refuses with it.
        skip |= STOCK_FACETS
    return frozenset(skip)


def _inventory_module_off(db: Session) -> bool:
    from app.config import settings
    from app.modules.runtime import installer

    if not getattr(settings, "module_guard_strict", False):
        return False
    tenant = installer.DEFAULT_TENANT_ID
    return installer.tenant_has_any_module_row(db, tenant) and not installer.is_module_enabled(db, tenant, "inventory")


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


@dataclass(frozen=True)
class StockOverride:
    mode: str
    include: Optional[tuple[str, ...]]  # None = every active warehouse
    exclude: Optional[tuple[str, ...]]  # None = no exclusion
    hide_zero: bool

    def warehouse_ids(self) -> set[str]:
        return set(self.include or ()) | set(self.exclude or ())


@dataclass
class AccessSnapshot:
    """One contact's access set. Reveals hold only keys this build knows (a key it does not
    know can neither be written nor revoked by `set_granted_keys`, so it is not compared)."""

    contact_id: str
    label: str
    access_types: list[str] = field(default_factory=list)
    tier: Optional[str] = None
    switches: dict[str, bool] = field(default_factory=dict)
    #: CONTACT-REGION: the held packing list regions, canonical order (west, east).
    regions: list[str] = field(default_factory=lambda: ["west"])
    reveals: list[str] = field(default_factory=list)
    agents: dict[str, AgentGrant] = field(default_factory=dict)  # agent_id -> grant
    #: The contact-tier stock visibility row, or None when the contact inherits.
    stock: Optional[StockOverride] = None
    companies: list[str] = field(default_factory=list)  # company ids
    segments: list[str] = field(default_factory=list)  # market segment codes


@dataclass
class Names:
    """Display names for the ids a diff shows; an id missing here shows as itself."""

    types: dict[str, str] = field(default_factory=dict)  # access type code -> name
    warehouses: dict[str, str] = field(default_factory=dict)  # warehouse id -> code
    companies: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (code, name)
    segments: dict[str, str] = field(default_factory=dict)  # code -> name


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
            regions=[r for r in REGION_ORDER if r in (c.regions or ["west"])],
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

    for row in db.query(StockVisibilityPolicy).filter(StockVisibilityPolicy.contact_id.in_(ids)):
        out[str(row.contact_id)].stock = StockOverride(
            mode=row.mode,
            include=tuple(sorted(str(w) for w in row.warehouse_ids)) if row.warehouse_ids is not None else None,
            exclude=(
                tuple(sorted(str(w) for w in row.excluded_warehouse_ids))
                if row.excluded_warehouse_ids is not None
                else None
            ),
            hide_zero=bool(row.hide_zero_locations),
        )

    for contact_id, company_id in db.query(
        RespondContactCompany.respond_contact_id, RespondContactCompany.company_id
    ).filter(RespondContactCompany.respond_contact_id.in_(ids)):
        out[str(contact_id)].companies.append(str(company_id))

    for contact_id, code in db.execute(
        select(respond_contact_market_segments.c.contact_id, respond_contact_market_segments.c.segment_code)
        .where(respond_contact_market_segments.c.contact_id.in_(ids))
    ):
        out[str(contact_id)].segments.append(code)

    for snap in out.values():
        snap.access_types.sort()
        snap.reveals.sort()
        snap.companies.sort()
        snap.segments.sort()
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


def names_for(db: Session, snaps: Iterable[AccessSnapshot]) -> Names:
    """Display names for every id the given snapshots hold, one query per kind."""
    snaps = list(snaps)
    codes = {c for s in snaps for c in s.access_types}
    warehouse_ids = {w for s in snaps if s.stock for w in s.stock.warehouse_ids()}
    company_ids = {c for s in snaps for c in s.companies}
    segment_codes = {c for s in snaps for c in s.segments}
    names = Names()
    if codes:
        names.types = dict(
            db.query(ContactAccessType.code, ContactAccessType.name)
            .filter(ContactAccessType.code.in_(codes))
            .all()
        )
    if warehouse_ids:
        from app.services.stock_visibility import policy_warehouses

        # Across every company, as the Stock visibility card resolves them.
        names.warehouses = {w["id"]: w["code"] for w in policy_warehouses(db, frozenset(warehouse_ids)) or []}
    if company_ids:
        names.companies = {
            str(cid): (code, name)
            for cid, code, name in db.query(Company.id, Company.code, Company.name).filter(Company.id.in_(company_ids))
        }
    if segment_codes:
        names.segments = dict(
            db.query(MarketSegment.code, MarketSegment.name).filter(MarketSegment.code.in_(segment_codes)).all()
        )
    return names


def locations_text(stock: Optional[StockOverride], names: Names) -> Optional[str]:
    """The locations half of a stock override as one line; None when the contact inherits."""
    if stock is None:
        return None

    def codes(ids: tuple[str, ...]) -> str:
        # A deleted warehouse has no code (the array has no FK); never show its id.
        return ", ".join(sorted(names.warehouses.get(w, REMOVED_LOCATION) for w in ids))

    if stock.exclude is not None:
        text_ = f"All except {codes(stock.exclude)}" if stock.exclude else "All locations"
    elif stock.include is None:
        text_ = "All locations"
    elif not stock.include:
        text_ = "No locations"
    else:
        text_ = f"Only {codes(stock.include)}"
    return f"{text_}; hide zero-quantity" if stock.hide_zero else text_


def _locations_key(stock: Optional[StockOverride]) -> Optional[tuple]:
    return None if stock is None else (stock.include, stock.exclude, stock.hide_zero)


def _list_change(facet: str, before: list[str], after: list[str], label_of, value_of) -> dict:
    return _change(
        facet,
        FACET_LABELS[facet],
        sorted(value_of(x) for x in before),
        sorted(value_of(x) for x in after),
        sorted(label_of(x) for x in after if x not in before),
        sorted(label_of(x) for x in before if x not in after),
    )


def diff(
    source: AccessSnapshot,
    target: AccessSnapshot,
    names: Names,
    skip: frozenset[str] = frozenset(),
) -> list[dict]:
    """What a copy from `source` changes on `target`, one row per differing facet, in facet order.
    Facets in `skip` are never compared (the actor may not set them)."""
    changes: list[dict] = []
    type_names = names.types

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

    if source.regions != target.regions:
        changes.append(
            _change(
                "regions",
                "Regions",
                [REGION_LABELS.get(c, c) for c in target.regions],
                [REGION_LABELS.get(c, c) for c in source.regions],
                [REGION_LABELS.get(c, c) for c in source.regions if c not in target.regions],
                [REGION_LABELS.get(c, c) for c in target.regions if c not in source.regions],
            )
        )

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

    src_mode = source.stock.mode if source.stock else None
    tgt_mode = target.stock.mode if target.stock else None
    if "stock_check_mode" not in skip and src_mode != tgt_mode:
        changes.append(_change("stock_check_mode", FACET_LABELS["stock_check_mode"], tgt_mode, src_mode))
    if "stock_locations" not in skip and _locations_key(source.stock) != _locations_key(target.stock):
        changes.append(
            _change(
                "stock_locations",
                FACET_LABELS["stock_locations"],
                locations_text(target.stock, names),
                locations_text(source.stock, names),
            )
        )

    if COMPANIES_FACET not in skip and source.companies != target.companies:
        changes.append(
            _list_change(
                COMPANIES_FACET,
                target.companies,
                source.companies,
                lambda c: names.companies.get(c, (REMOVED_COMPANY, REMOVED_COMPANY))[1],
                lambda c: names.companies.get(c, (REMOVED_COMPANY, REMOVED_COMPANY))[0],
            )
        )
    if "market_segments" not in skip and source.segments != target.segments:
        changes.append(
            _list_change(
                "market_segments",
                target.segments,
                source.segments,
                lambda c: names.segments.get(c, c),
                lambda c: c,
            )
        )
    return changes


def not_copied(skip: frozenset[str]) -> list[str]:
    """What the copy leaves alone for this actor, as dialog labels."""
    return [*NOT_COPIED, *(FACET_LABELS[f] for f in FACET_LABELS if f in skip)]


def summary(source: AccessSnapshot, names: Names, skip: frozenset[str] = frozenset()) -> list[dict]:
    """The source's access set as display lines for the dialog's first step."""
    type_names = names.types
    labels = dict(contact_field_reveal_service.FIELD_REVEAL_KEYS)
    agents = sorted(g.agent_name for g in source.agents.values() if g.is_allowed)
    lines = [
        {"label": "Access types", "value": ", ".join(type_names.get(c, c) for c in source.access_types)},
        {"label": "Tier", "value": TIER_LABELS.get(source.tier or "", source.tier or "")},
        {
            "label": "Chatbot switches",
            "value": ", ".join(f"{label} {'on' if source.switches[col] else 'off'}" for col, label in SWITCHES),
        },
        {"label": "Regions", "value": ", ".join(REGION_LABELS.get(r, r) for r in source.regions)},
        {"label": "Field reveals", "value": ", ".join(labels.get(k, k) for k in source.reveals)},
        {"label": "Agent access", "value": ", ".join(agents)},
    ]
    extra = {
        "stock_check_mode": STOCK_MODE_LABELS.get(source.stock.mode, source.stock.mode) if source.stock else "Inherited",
        "stock_locations": locations_text(source.stock, names) or "Inherited",
        "companies": ", ".join(
            sorted(names.companies.get(c, (REMOVED_COMPANY, REMOVED_COMPANY))[1] for c in source.companies)
        ),
        "market_segments": ", ".join(names.segments.get(c, c) for c in source.segments),
    }
    lines.extend({"label": FACET_LABELS[f], "value": v} for f, v in extra.items() if f not in skip)
    return lines


# ---------------------------------------------------------------- write


def _write(
    db: Session,
    source: AccessSnapshot,
    target: RespondContact,
    snap: AccessSnapshot,
    actor_id: Optional[str],
    changed: frozenset[str] = frozenset(),
) -> None:
    """Make `target` equal to `source` on every facet. Caller owns the savepoint.

    The facets added on 4 Oct 2026 are written only when `changed` (the facets of this target's
    diff) names them, so a facet the actor may not set (left out of the diff) is never written."""
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
    if list(target.regions or ["west"]) != source.regions:
        target.regions = list(source.regions)
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

    if changed & STOCK_FACETS:
        _write_stock(db, source.stock, target_id)

    if COMPANIES_FACET in changed:
        db.query(RespondContactCompany).filter(RespondContactCompany.respond_contact_id == target_id).delete(
            synchronize_session=False
        )
        for company_id in source.companies:
            db.add(RespondContactCompany(respond_contact_id=target_id, company_id=company_id))

    if "market_segments" in changed:
        db.execute(
            respond_contact_market_segments.delete().where(respond_contact_market_segments.c.contact_id == target_id)
        )
        if source.segments:
            db.execute(
                respond_contact_market_segments.insert(),
                [{"contact_id": target_id, "segment_code": code} for code in source.segments],
            )
        # The contact's `market_segments` relationship was loaded before this Core write.
        db.expire(target, ["market_segments"])
    db.flush()


def _write_stock(db: Session, stock: Optional[StockOverride], target_id: str) -> None:
    """The target's contact-tier stock visibility row becomes the source's; none when the source
    inherits. Through the ORM, not `stock_visibility.upsert_policy` (which commits and so would end
    the per-target savepoint), so the table's audit listener still records the change. The values
    were validated when the source's row was saved."""
    row = db.query(StockVisibilityPolicy).filter(StockVisibilityPolicy.contact_id == target_id).first()
    if stock is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        row = StockVisibilityPolicy(contact_id=target_id)
        db.add(row)
    setattr(row, "mode", stock.mode)
    setattr(row, "warehouse_ids", list(stock.include) if stock.include is not None else None)
    setattr(row, "excluded_warehouse_ids", list(stock.exclude) if stock.exclude is not None else None)
    setattr(row, "hide_zero_locations", stock.hide_zero)


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
    skip: frozenset[str] = frozenset(),
) -> dict:
    """Preview (dry run) or apply a copy from `source` to every id in `target_ids`.

    `skip` names the facets this actor may not set directly (`facets_to_skip`);
    they are left out of the preview, the write and the summary, and named in `not_copied`.

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
    names = names_for(db, snaps.values())

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
        changes = diff(src, snap, names, skip)
        if not changes:
            results.append(_result(target_id, snap.label, "unchanged", [], None))
            continue
        if not dry_run:
            savepoint = db.begin_nested()
            try:
                _write(db, src, contact, snap, actor_id, frozenset(c["facet"] for c in changes))
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
        "source": {
            "id": src.contact_id,
            "label": src.label,
            "summary": summary(src, names, skip),
            "not_copied": not_copied(skip),
        },
        "results": results,
        "counts": counts,
    }


def _result(contact_id: str, label: Optional[str], status: str, changes: list[dict], error: Optional[str]) -> dict:
    return {"contact_id": contact_id, "label": label, "status": status, "changes": changes, "error": error}


def contacts_differing_from(
    db: Session,
    source: RespondContact,
    candidates: list[RespondContact],
    skip: frozenset[str] = frozenset(),
) -> list[str]:
    """Ids among `candidates` (never the source) a copy from `source` would change (UAC A2.5).
    `skip` is the viewer's `facets_to_skip`, so the list matches what their copy would do."""
    others = [c for c in candidates if str(c.id) != str(source.id)]
    snaps = snapshots(db, [source, *others])
    src = snaps[str(source.id)]
    return [str(c.id) for c in others if diff(src, snaps[str(c.id)], Names(), skip)]
