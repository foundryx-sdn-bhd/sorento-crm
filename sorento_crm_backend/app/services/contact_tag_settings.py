"""CONTACT-TAGS S2: tag settings, the expected value across a contact's tags, and the
current value the runtime reads (contract `contact-tags-s2-contract.md`).

One module, one table. A tag's `settings` JSON is flattened to leaves `(section, key, value)`;
every leaf kind has a merge rule (`_MERGE`), a current-value reader (`_Reader.current`) and a
label. The runtime rules themselves are NOT re-implemented here: current values come from the
readers the contract names (`resolve_visible_form_types`, `granted_keys_for`,
`contact_agent_codes`, `allowed_fields_for`, the media helpers, the stock and spec resolvers).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.error_handler import AppException, handle_unprocessable

ALL = "all"  # the "every brand" sentinel, neutral in an intersection

#: Top-level sections in display order (the mock's order).
SECTIONS: tuple[str, ...] = (
    "access_types",
    "brands",
    "companies",
    "market_segments",
    "regions",
    "chatbot",
    "portal_forms",
    "requires_registered_project",
    "field_reveals",
    "agents",
    "agent_fields",
    "media",
    "attachment_types",
    "stock_visibility",
    "spec_visibility",
    "outbound_enabled",
)
_SECTION_RANK = {s: i for i, s in enumerate(SECTIONS)}

#: Section names in words (403 messages, redacted labels, the apply dialog).
SECTION_TITLES: dict[str, str] = {
    "access_types": "Access types", "brands": "Brands", "companies": "Companies",
    "market_segments": "Market segments", "regions": "Regions", "chatbot": "Chatbot",
    "portal_forms": "Portal forms", "requires_registered_project": "Requires registered project",
    "field_reveals": "Field reveals", "agents": "Chatbot agents", "agent_fields": "Agent field access",
    "media": "Media access", "attachment_types": "Attachment types", "stock_visibility": "Stock visibility",
    "spec_visibility": "Spec visibility", "outbound_enabled": "Messaging (outbound)",
}

CHATBOT_BOOLS: dict[str, tuple[str, str]] = {
    # key -> (label, merge kind)
    "chatbot_allowed": ("AI replies", "and"),
    "chatbot_stock_allowed": ("Stock checks", "and"),
    "escalation_allowed": ("Escalation", "and"),
    "packing_list_allowed": ("Packing list", "and"),
    "notify_salesman": ("Notify salesman", "and"),
    "chatbot_eta_offset_applied": ("ETA buffer days", "or"),
    "chatbot_skip_period_questions": ("Skip date-range questions", "eq"),
    "chatbot_files_as_links": ("Files as links", "eq"),
}
MEMORY_LEVELS = ("off", "conversation", "episodes", "full")
MEMORY_LABELS = {"off": "Off", "conversation": "Conversation", "episodes": "Past conversations", "full": "Full"}
TIERS = ("dealer", "office", "end_user")
TIER_LABELS = {"dealer": "Dealer", "office": "Office", "end_user": "End user"}
REGIONS = ("west", "east")
REGION_LABELS = {"west": "West Malaysia", "east": "East Malaysia"}
MODALITIES = ("image", "voice")
MODE_LABELS = {"detailed": "Detailed", "compact": "Compact", "availability": "Availability"}
_MEDIA_KEYS = {
    "image": ("is_allowed", "monthly_limit", "languages"),
    "voice": ("is_allowed", "monthly_limit", "max_clip_seconds", "languages"),
}
_STOCK_KEYS = ("mode", "warehouse_ids", "excluded_warehouse_ids", "hide_zero_locations")
_SPEC_KEYS = ("spec_keys", "excluded_spec_keys")

#: section -> slug for CHANGING it on a tag. `None` = superadmin. Missing = contacts.edit.
_GATES: dict[str, Optional[str]] = {
    "agents": "user_management.access_agents.edit",
    "agent_fields": "user_management.access_agents.edit",
    "stock_visibility": "inventory.stock.edit",
    "outbound_enabled": "system.respond_workspaces.edit",
    "companies": None,
}


# ------------------------------------------------------------------ catalogs


class Catalog:
    """Human names for the codes and ids a setting holds; each map is read once."""

    def __init__(self, db: Session, scope_brands: bool = False):
        self.db = db
        # Brands follow the caller's company scope when picking and validating; names shown on
        # an item are read unscoped (a stored brand keeps its name whoever looks).
        self.scope_brands = scope_brands
        self._maps: dict[str, dict[str, str]] = {}
        self._agents: Optional[dict[str, tuple[str, str]]] = None

    def _load(self, name: str) -> dict[str, str]:
        if name in self._maps:
            return self._maps[name]
        from app.models.base import company_scope

        db = self.db
        out: dict[str, str] = {}
        if name == "access_types":
            from app.models.access import ContactAccessType

            out = {c: n for c, n in db.query(ContactAccessType.code, ContactAccessType.name)}
        elif name == "market_segments":
            from app.models.access import MarketSegment

            out = {c: n for c, n in db.query(MarketSegment.code, MarketSegment.name)}
        elif name == "brands":
            from app.models.product import Brand

            if self.scope_brands:
                out = {str(i): n for i, n in db.query(Brand.id, Brand.brand_name)}
            else:
                with company_scope(db, None):
                    out = {str(i): n for i, n in db.query(Brand.id, Brand.brand_name)}
        elif name == "companies":
            from app.models.company import Company

            out = {str(i): n for i, n in db.query(Company.id, Company.name)}
        elif name == "warehouses":
            from app.models.inventory import Warehouse

            with company_scope(db, None):
                out = {str(i): n for i, n in db.query(Warehouse.id, Warehouse.warehouse_name)}
        elif name == "attachment_types":
            from app.models.resources import AttachmentType

            out = {str(i): n for i, n in db.query(AttachmentType.id, AttachmentType.type_name)}
        self._maps[name] = out
        return out

    def agents(self) -> dict[str, tuple[str, str]]:
        """agent id -> (code, name)."""
        if self._agents is None:
            from app.models.access import AccessAgent

            self._agents = {
                str(i): (c, n) for i, c, n in self.db.query(AccessAgent.id, AccessAgent.code, AccessAgent.name)
            }
        return self._agents

    def agent_name_by_code(self, code: str) -> str:
        for c, n in self.agents().values():
            if c == code:
                return n
        return "removed agent"

    def names(self, name: str, ids: Iterable[str]) -> list[str]:
        m = self._load(name)
        return sorted(m.get(str(i), f"removed {name.replace('_', ' ')[:-1]}") for i in ids)

    def known(self, name: str) -> set[str]:
        return set(self._load(name))


_NAME_MAP = {
    ("access_types", "access_types"): "access_types",
    ("brands", "brands"): "brands",
    ("companies", "companies"): "companies",
    ("market_segments", "market_segments"): "market_segments",
    ("attachment_types", "attachment_types"): "attachment_types",
    ("stock_visibility", "warehouse_ids"): "warehouses",
    ("stock_visibility", "excluded_warehouse_ids"): "warehouses",
}


def _fmt(cat: Catalog, section: str, key: str, value: Any) -> Any:
    """A raw value as a person reads it: names for ids and codes, never the ids themselves."""
    if value == ALL and isinstance(value, str):
        return "All"
    if isinstance(value, frozenset):
        mapped = _NAME_MAP.get((section, key))
        if mapped:
            return cat.names(mapped, value)
        if section == "regions":
            return [REGION_LABELS.get(v, v) for v in REGIONS if v in value]
        return sorted(value)
    if value is None:
        return None
    if section == "chatbot" and key == "tier":
        return TIER_LABELS.get(value, value)
    if section == "chatbot" and key == "memory_level":
        return MEMORY_LABELS.get(value, value)
    if section == "stock_visibility" and key == "mode":
        return MODE_LABELS.get(value, value)
    return value


# ------------------------------------------------------------------ validation


def _bad(key: str, why: str):
    return handle_unprocessable(f"Tag setting '{key}': {why}")


def _is_bool(v) -> bool:
    return isinstance(v, bool)


def _str_list(key: str, v) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise _bad(key, "must be a list of text values")
    return v


def _check_known(cat: Catalog, name: str, values: list[str]) -> None:
    known = cat.known(name)
    for v in values:
        if v not in known:
            raise _bad(v, f"unknown value in {name}")


def validate_settings(db: Session, settings: Any) -> dict:
    """The settings object, checked against the live catalogs, or 422 naming the offending key."""
    if not isinstance(settings, dict):
        raise _bad("settings", "must be an object")
    cat = Catalog(db, scope_brands=True)
    for section, value in settings.items():
        if section not in SECTIONS:
            raise _bad(section, "unknown setting")
        _VALIDATE[section](db, cat, section, value)
    return settings


def _v_codes(name: str):
    def check(db, cat, section, value):
        _check_known(cat, name, _str_list(section, value))

    return check


def _v_brands(db, cat, section, value):
    if value == ALL:
        return
    _check_known(cat, "brands", _str_list(section, value))


def _v_regions(db, cat, section, value):
    vals = _str_list(section, value)
    if not vals:
        raise _bad(section, "needs at least one region")
    for v in vals:
        if v not in REGIONS:
            raise _bad(v, "unknown region")


def _v_chatbot(db, cat, section, value):
    if not isinstance(value, dict):
        raise _bad(section, "must be an object")
    for k, v in value.items():
        if k in CHATBOT_BOOLS:
            if not _is_bool(v):
                raise _bad(k, "must be true or false")
        elif k == "memory_level":
            if v not in MEMORY_LEVELS:
                raise _bad(k, "unknown memory level")
        elif k == "tier":
            if v not in TIERS:
                raise _bad(k, "unknown tier")
        else:
            raise _bad(k, "unknown setting")


def _v_bool_map(keys_of: Callable[[Session, Catalog], set[str]]):
    def check(db, cat, section, value):
        if not isinstance(value, dict):
            raise _bad(section, "must be an object")
        known = keys_of(db, cat)
        for k, v in value.items():
            if k not in known:
                raise _bad(k, "unknown value")
            if not _is_bool(v):
                raise _bad(k, "must be true or false")

    return check


def _portal_kinds(db, cat) -> set[str]:
    from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES

    return set(GRANTABLE_PORTAL_FORM_TYPES)


def _reveal_keys(db, cat) -> set[str]:
    from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS

    return {k for k, _ in FIELD_REVEAL_KEYS}


def _v_bool(db, cat, section, value):
    if not _is_bool(value):
        raise _bad(section, "must be true or false")


def _v_agent_fields(db, cat, section, value):
    if not isinstance(value, list):
        raise _bad(section, "must be a list")
    codes = {c for c, _ in cat.agents().values()}
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"agent_code", "resource", "field_key", "is_allowed"}:
            raise _bad(section, "each entry needs agent_code, resource, field_key and is_allowed")
        for k in ("agent_code", "resource", "field_key"):
            if not isinstance(entry[k], str) or not entry[k]:
                raise _bad(k, "must be text")
        if not _is_bool(entry["is_allowed"]):
            raise _bad("is_allowed", "must be true or false")
        if entry["agent_code"] not in codes:
            raise _bad(entry["agent_code"], "unknown agent")
        from app.services.field_access import GATED_FIELDS

        owner = GATED_FIELDS.get(entry["resource"], {}).get(entry["field_key"])
        if owner is None:
            raise _bad(entry["field_key"], f"{entry['resource']}.{entry['field_key']} is not a gated field")
        if owner != entry["agent_code"]:
            raise _bad(entry["field_key"], f"belongs to agent '{owner}', not '{entry['agent_code']}'")


def _v_media(db, cat, section, value):
    if not isinstance(value, dict):
        raise _bad(section, "must be an object")
    for modality, body in value.items():
        if modality not in MODALITIES:
            raise _bad(modality, "unknown modality")
        if not isinstance(body, dict):
            raise _bad(modality, "must be an object")
        for k, v in body.items():
            if k not in _MEDIA_KEYS[modality]:
                raise _bad(k, "unknown setting")
            if k == "is_allowed":
                if not _is_bool(v):
                    raise _bad(k, "must be true or false")
            elif k == "languages":
                _str_list(k, v)
            else:
                low, high = (1, 3600) if k == "max_clip_seconds" else (0, 100000)
                if _is_bool(v) or not isinstance(v, int) or not (low <= v <= high):
                    raise _bad(k, f"must be a whole number from {low} to {high}")


def _v_stock(db, cat, section, value):
    from app.services import stock_visibility as sv

    if not isinstance(value, dict):
        raise _bad(section, "must be an object")
    if value.get("warehouse_ids") is not None and value.get("excluded_warehouse_ids") is not None:
        raise _bad(section, "set either the include list or the exclude list, not both")
    for k, v in value.items():
        if k not in _STOCK_KEYS:
            raise _bad(k, "unknown setting")
        if k == "mode":
            if v not in sv.MODES:
                raise _bad(k, "unknown mode")
        elif k == "hide_zero_locations":
            if not _is_bool(v):
                raise _bad(k, "must be true or false")
        elif v is not None:
            _str_list(k, v)
            known = cat.known("warehouses")
            for w in v:
                if w not in known:
                    raise _bad(w, f"unknown warehouse in {k}")


def _v_spec(db, cat, section, value):
    from app.services import spec_visibility as sp

    if not isinstance(value, dict):
        raise _bad(section, "must be an object")
    if value.get("spec_keys") is not None and value.get("excluded_spec_keys") is not None:
        raise _bad(section, "set either the show list or the hide list, not both")
    for k, v in value.items():
        if k not in _SPEC_KEYS:
            raise _bad(k, "unknown setting")
        if v is not None:
            _str_list(k, v)
            try:
                sp.validated_spec_keys(db, v)
            except AppException as e:  # keep its refusal, name the key
                raise _bad(k, str(e.detail.get("message") if isinstance(e.detail, dict) else e.detail))


_VALIDATE: dict[str, Callable] = {
    "access_types": _v_codes("access_types"),
    "brands": _v_brands,
    "companies": _v_codes("companies"),
    "market_segments": _v_codes("market_segments"),
    "regions": _v_regions,
    "chatbot": _v_chatbot,
    "portal_forms": _v_bool_map(_portal_kinds),
    "requires_registered_project": _v_bool,
    "field_reveals": _v_bool_map(_reveal_keys),
    "agents": _v_bool_map(lambda db, cat: set(cat.agents())),
    "agent_fields": _v_agent_fields,
    "media": _v_media,
    "attachment_types": _v_codes("attachment_types"),
    "stock_visibility": _v_stock,
    "spec_visibility": _v_spec,
    "outbound_enabled": _v_bool,
}


def assert_gates(old: dict, new: dict, can: Callable[[str], bool], is_superadmin: Callable[[], bool]) -> None:
    """403 naming the first section whose value changed without the right to change it."""
    for section in SECTIONS:
        if old.get(section) == new.get(section) or (section not in old and section not in new):
            continue
        if section in _GATES:
            slug = _GATES[section]
            allowed = is_superadmin() if slug is None else can(slug)
        else:
            allowed = can("user_management.contacts.edit")
        if not allowed:
            raise AppException(
                status_code=403,
                message=f"You may not change {SECTION_TITLES[section]} on a tag.",
                code="FORBIDDEN",
            )


# ------------------------------------------------------------------ leaves + merge


@dataclass(frozen=True)
class TagRef:
    id: str
    name: str
    settings: dict


def leaves(tag: TagRef) -> list[tuple[str, str, Any]]:
    """The settings the tag sets, flattened. A None value is "not set" (null = all, neutral)."""
    out: list[tuple[str, str, Any]] = []
    for section, value in (tag.settings or {}).items():
        if section not in SECTIONS or value is None:
            continue
        if section in ("access_types", "companies", "market_segments", "attachment_types", "regions"):
            out.append((section, section, frozenset(value)))
        elif section == "brands":
            out.append((section, section, ALL if value == ALL else frozenset(value)))
        elif section in ("requires_registered_project", "outbound_enabled"):
            out.append((section, section, value))
        elif section in ("chatbot", "portal_forms", "field_reveals", "agents"):
            for k, v in value.items():
                out.append((section, k, v))
        elif section == "agent_fields":
            for e in value:
                out.append((section, f"{e['agent_code']}.{e['resource']}.{e['field_key']}", e["is_allowed"]))
        elif section == "media":
            for modality, body in value.items():
                for k, v in body.items():
                    out.append((section, f"{modality}.{k}", frozenset(v) if k == "languages" else v))
        elif section in ("stock_visibility", "spec_visibility"):
            for k, v in value.items():
                if v is None:
                    continue
                out.append((section, k, frozenset(v) if isinstance(v, list) else v))
    return out


def _rank(order: tuple[str, ...]):
    return {v: i for i, v in enumerate(order)}


def _m_and(vals):
    res = all(vals)
    return res, False


def _m_or(vals):
    return any(vals), False


def _m_eq(vals):
    distinct = set(vals)
    return (None, True) if len(distinct) > 1 else (vals[0], False)


def _m_tier(vals):
    s = set(vals)
    if {"dealer", "end_user"} <= s:
        return None, True
    for pick in ("dealer", "end_user"):
        if pick in s:
            return pick, False
    return "office", False


def _m_memory(vals):
    r = _rank(MEMORY_LEVELS)
    return min(vals, key=lambda v: r.get(v, 0)), False


def _m_intersect(vals):
    sets = [v for v in vals if v != ALL]
    if not sets:
        return ALL, False
    res = sets[0]
    for s in sets[1:]:
        res = res & s
    return res, False


def _m_regions(vals):
    res, _ = _m_intersect(vals)
    return (None, True) if not res else (res, False)


def _m_union(vals):
    res = frozenset()
    for v in vals:
        res = res | v
    return res, False


def _m_min(vals):
    return min(vals), False


def _m_mode(vals):
    from app.services.stock_visibility import MODES

    r = _rank(MODES)
    return max(vals, key=lambda v: r.get(v, 0)), False


def _merge_kind(section: str, key: str) -> str:
    if section == "chatbot":
        if key in CHATBOT_BOOLS:
            return CHATBOT_BOOLS[key][1]
        return {"tier": "tier", "memory_level": "memory"}[key]
    if section in ("portal_forms", "field_reveals", "agents", "agent_fields", "outbound_enabled"):
        return "and"
    if section == "requires_registered_project":
        return "or"
    if section == "regions":
        return "regions"
    if section == "media":
        field = key.split(".", 1)[1]
        return {"is_allowed": "and", "monthly_limit": "min", "max_clip_seconds": "min", "languages": "intersect"}[field]
    if section == "stock_visibility":
        return {"mode": "mode", "warehouse_ids": "intersect", "excluded_warehouse_ids": "union",
                "hide_zero_locations": "or"}[key]
    if section == "spec_visibility":
        return "intersect" if key == "spec_keys" else "union"
    if section == "brands":
        return "regions"  # an empty intersection has no value to write: a conflict, like regions
    return "intersect"  # access_types, companies, market_segments, attachment_types


_MERGE = {
    "and": _m_and, "or": _m_or, "eq": _m_eq, "tier": _m_tier, "memory": _m_memory,
    "intersect": _m_intersect, "regions": _m_regions, "union": _m_union, "min": _m_min, "mode": _m_mode,
}


# ------------------------------------------------------------------ labels


def _label(cat: Catalog, section: str, key: str) -> str:
    from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS
    from app.services.portal_form_visibility_service import _KIND_LABELS

    fixed = {
        "access_types": "Access types", "brands": "Brands", "companies": "Companies",
        "market_segments": "Market segments", "regions": "Regions",
        "requires_registered_project": "Requires registered project",
        "attachment_types": "Attachment types", "outbound_enabled": "Messaging",
    }
    if section in fixed:
        return fixed[section]
    if section == "chatbot":
        if key in CHATBOT_BOOLS:
            return CHATBOT_BOOLS[key][0]
        return "Memory level" if key == "memory_level" else "Tier"
    if section == "portal_forms":
        return f"Portal form: {_KIND_LABELS.get(key, key.replace('_', ' ').capitalize())}"
    if section == "field_reveals":
        return dict(FIELD_REVEAL_KEYS).get(key, key)
    if section == "agents":
        return f"Agent: {cat.agents().get(key, ('', 'removed agent'))[1]}"
    if section == "agent_fields":
        code, _resource, field_key = key.split(".", 2)
        return f"{cat.agent_name_by_code(code)}: {field_key}"
    if section == "media":
        modality, field = key.split(".", 1)
        what = {"is_allowed": "access", "monthly_limit": "monthly limit",
                "max_clip_seconds": "clip length", "languages": "languages"}[field]
        return f"{modality.capitalize()} {what}"
    if section == "stock_visibility":
        return {"mode": "Stock mode", "warehouse_ids": "Stock locations",
                "excluded_warehouse_ids": "Stock excluded locations",
                "hide_zero_locations": "Hide empty locations"}[key]
    return {"spec_keys": "Specs shown", "excluded_spec_keys": "Specs hidden"}[key]


# ------------------------------------------------------------------ current values


class _Reader:
    """What the runtime reads now, per leaf. Cheap facets are loaded once for the whole page."""

    def __init__(self, db: Session, contacts: list):
        self.db = db
        self.contacts = {str(c.id): c for c in contacts}
        self.ids = list(self.contacts)
        self._pivots: dict[str, dict[str, set[str]]] = {}
        self._reveals: Optional[dict[str, list[str]]] = None
        self._agent_codes: dict[str, set[str]] = {}
        self._visible_forms: dict[str, set[str]] = {}
        self._media_settings = None
        self._cat: Optional[Catalog] = None

    def _pivot(self, name: str) -> dict[str, set[str]]:
        if name in self._pivots:
            return self._pivots[name]
        out: dict[str, set[str]] = {i: set() for i in self.ids}
        if name == "access_types":
            from app.models.access import respond_contact_access_types as p

            rows = self.db.execute(select(p.c.contact_id, p.c.access_type_code).where(p.c.contact_id.in_(self.ids)))
        elif name == "market_segments":
            from app.models.access import respond_contact_market_segments as p

            rows = self.db.execute(select(p.c.contact_id, p.c.segment_code).where(p.c.contact_id.in_(self.ids)))
        elif name == "companies":
            from app.models.company import RespondContactCompany as C

            rows = self.db.query(C.respond_contact_id, C.company_id).filter(C.respond_contact_id.in_(self.ids))
        else:  # attachment_types: the raw grants (`contact_attachment_access.granted_type_ids`)
            from app.models.access import ContactAttachmentType as A

            rows = self.db.query(A.contact_id, A.attachment_type_id).filter(A.contact_id.in_(self.ids))
        for cid, value in rows:
            out[str(cid)].add(str(value))
        self._pivots[name] = out
        return out

    def current(self, cid: str, section: str, key: str, cat: Catalog) -> Any:
        c = self.contacts[cid]
        if section in ("access_types", "companies", "market_segments", "attachment_types"):
            return frozenset(self._pivot(section)[cid])
        if section == "brands":
            return frozenset(str(b) for b in c.brand_ids) if c.brand_ids else ALL
        if section == "regions":
            return frozenset(c.regions or ["west"])
        if section == "requires_registered_project":
            return bool(c.requires_registered_project)
        if section == "outbound_enabled":
            return bool(c.outbound_enabled)
        if section == "chatbot":
            if key in CHATBOT_BOOLS:
                return bool(getattr(c, key))
            if key == "memory_level":
                return c.chatbot_memory_level
            return (c.chatbot_profile or {}).get("tier") or None
        if section == "portal_forms":
            if cid not in self._visible_forms:
                from app.services.portal_form_visibility_service import resolve_visible_form_types

                self._visible_forms[cid] = resolve_visible_form_types(self.db, cid)
            return key in self._visible_forms[cid]
        if section == "field_reveals":
            if self._reveals is None:
                from app.services.contact_field_reveal_service import granted_keys_for

                self._reveals = granted_keys_for(self.db, self.ids)
            return key in self._reveals[cid]
        if section == "agents":
            return cat.agents().get(key, ("", ""))[0] in self._codes(cid)
        if section == "agent_fields":
            from app.services.field_access import allowed_fields_for

            code, resource, field_key = key.split(".", 2)
            return field_key in allowed_fields_for(self.db, contact_id=cid, resource=resource, agent_codes=[code])
        if section == "media":
            return self._media(cid, key)
        if section == "stock_visibility":
            return self._stock(cid, key)
        return self._spec(cid, key)

    def _codes(self, cid: str) -> set[str]:
        if cid not in self._agent_codes:
            from app.services.field_access import contact_agent_codes

            self._agent_codes[cid] = contact_agent_codes(self.db, cid)
        return self._agent_codes[cid]

    def _media(self, cid: str, key: str) -> Any:
        from app.services import media_access_service as m

        modality, field = key.split(".", 1)
        if self._media_settings is None:
            self._media_settings = m.resolve_media_settings(self.db)
        row = m.get_limit_row(self.db, cid, modality)
        if field == "is_allowed":
            return bool(row and row.is_allowed)
        if field == "monthly_limit":
            return m.resolve_effective_limit(self.db, cid, modality, settings=self._media_settings)
        if field == "max_clip_seconds":
            return m.effective_clip_seconds(row, self._media_settings)
        langs = row.languages if row is not None and row.languages is not None else self._media_settings.language_hints
        return frozenset(langs or [])

    def _stock(self, cid: str, key: str) -> Any:
        from app.services import stock_visibility as sv

        if "stock" not in self.__dict__:
            self.stock: dict[str, Any] = {}
        if cid not in self.stock:
            self.stock[cid] = sv.resolve_policy(self.db, cid) or sv.default_policy(self.db)
        p = self.stock[cid]
        if key == "mode":
            return p.mode
        if key == "hide_zero_locations":
            return bool(p.hide_zero_locations)
        if key == "warehouse_ids":
            return ALL_SET if p.warehouse_ids is None else frozenset(p.warehouse_ids)
        return frozenset(p.excluded_warehouse_ids or ())

    def _spec(self, cid: str, key: str) -> Any:
        from app.services import spec_visibility as sp

        if "spec" not in self.__dict__:
            self.spec: dict[str, Any] = {}
        if cid not in self.spec:
            self.spec[cid] = sp.resolve_policy(self.db, cid)
        p = self.spec[cid]
        if key == "spec_keys":
            return ALL_SET if p.spec_keys is None else frozenset(p.spec_keys)
        return frozenset(p.excluded_spec_keys or ())


class _AllSet:
    """The "every location / every spec" current value: never equal to a concrete set."""

    def __eq__(self, other):
        return isinstance(other, _AllSet)

    def __hash__(self):
        return 1


ALL_SET = _AllSet()


def _display(cat: Catalog, section: str, key: str, value: Any) -> Any:
    if isinstance(value, _AllSet):
        return "All"
    return _fmt(cat, section, key, value)


# ------------------------------------------------------------------ evaluate


def tags_by_contact_with_settings(db: Session, contact_ids: list[str]) -> dict[str, list[TagRef]]:
    """Every tag of each contact (settings included), in the S1 order."""
    from app.models.access import ContactTag, respond_contact_tags as pivot

    out: dict[str, list[TagRef]] = {}
    if not contact_ids:
        return out
    rows = db.execute(
        select(pivot.c.contact_id, ContactTag.id, ContactTag.name, ContactTag.settings)
        .join(ContactTag, ContactTag.id == pivot.c.tag_id)
        .where(pivot.c.contact_id.in_(contact_ids))
        .order_by(ContactTag.sort_order, ContactTag.name)
    ).all()
    for cid, tid, name, settings in rows:
        out.setdefault(str(cid), []).append(TagRef(str(tid), name, settings or {}))
    return out


def evaluate(
    db: Session,
    contacts: list,
    only_tags: Optional[set[str]] = None,
    tag_map: Optional[dict[str, list[TagRef]]] = None,
    redact: frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """`{contact_id: {status, tags, items}}` for every contact given.

    `only_tags`: keep only the keys a tag in that set takes part in (the tag-list Differs count and
    the `tag=` + `tag_settings=` list filter, so both count the same thing).

    `redact`: sections the viewer may not read. Their items keep `section`, a generic label and
    `differs`; `current`, `expected` and `decided_by` are null and no names leak (F1).
    """
    ids = [str(c.id) for c in contacts]
    tag_map = tag_map if tag_map is not None else tags_by_contact_with_settings(db, ids)
    cat = Catalog(db)
    reader = _Reader(db, contacts)
    out: dict[str, dict] = {}
    for cid in ids:
        tags = tag_map.get(cid, [])
        groups: dict[tuple[str, str], list[tuple[TagRef, Any]]] = {}
        for tag in tags:
            for section, key, value in leaves(tag):
                groups.setdefault((section, key), []).append((tag, value))
        # One rule per contact row: an include list on one tag against an exclude list on another
        # cannot be stored, so it is a conflict naming both tags (the exclude group folds into it).
        forced: dict[tuple[str, str], list[tuple[TagRef, Any, str]]] = {}
        for sec, inc, exc in (
            ("stock_visibility", "warehouse_ids", "excluded_warehouse_ids"),
            ("spec_visibility", "spec_keys", "excluded_spec_keys"),
        ):
            if (sec, inc) in groups and (sec, exc) in groups:
                forced[(sec, inc)] = [(t, v, inc) for t, v in groups[(sec, inc)]] + [
                    (t, v, exc) for t, v in groups.pop((sec, exc))
                ]
        items = []
        for (section, key), entries in sorted(
            groups.items(), key=lambda kv: (_SECTION_RANK[kv[0][0]], kv[0][1])
        ):
            if only_tags is not None and not any(t.id in only_tags for t, _ in entries):
                continue
            if (section, key) in forced:
                expected, conflict = None, True
                conflict_tags = [
                    {"name": t.name, "value": _display(cat, section, k, v)} for t, v, k in forced[(section, key)]
                ]
                entries = [(t, v) for t, v, _k in forced[(section, key)]]
            else:
                expected, conflict = _MERGE[_merge_kind(section, key)]([v for _, v in entries])
                conflict_tags = [
                    {"name": t.name, "value": _display(cat, section, key, v)} for t, v in entries
                ] if conflict else []
            if conflict:
                decided = []
                differs = True
                current = reader.current(cid, section, key, cat)
            else:
                decided = sorted({t.name for t, v in entries if v == expected} or {t.name for t, _ in entries})
                conflict_tags = []
                current = reader.current(cid, section, key, cat)
                differs = current != expected
            blocked = None
            if section == "portal_forms" and key in _agent_only_kinds() and differs and expected is True:
                if not _has_sales_agent(db, cid):
                    blocked, differs = "Needs a linked sales agent", False
            hidden = section in redact
            items.append(
                {
                    "section": section,
                    "key": key,
                    "label": SECTION_TITLES[section] if hidden else _label(cat, section, key),
                    "blocked_reason": blocked,
                    "current": None if hidden else _display(cat, section, key, current),
                    "expected": None if hidden or conflict else _display(cat, section, key, expected),
                    "decided_by": None if hidden else decided,
                    "conflict": conflict,
                    "conflict_tags": [] if hidden else conflict_tags,
                    "differs": differs,
                    # Raw (id / code valued) view for the writers; never serialised (response models drop it).
                    "_raw": {"expected": None if conflict else expected, "current": current},
                }
            )
        status = "none" if not items else ("differs" if any(i["differs"] for i in items) else "matches")
        out[cid] = {"status": status, "tags": [{"id": t.id, "name": t.name} for t in tags], "items": items}
    return out


def _agent_only_kinds() -> tuple[str, ...]:
    from app.services.portal_service import AGENT_ONLY_FORM_TYPES

    return AGENT_ONLY_FORM_TYPES


def _has_sales_agent(db: Session, contact_id: str) -> bool:
    from app.services import price_tag_request_service

    return bool(price_tag_request_service.sales_agents_for_contact(db, contact_id))


#: Sections only some viewers may read, with the check each needs.
#: Whoever may EDIT a thing may read it, so each gate lists the view slug and the edit slug.
_AGENTS_READ = ("user_management.access_agents.view", "user_management.access_agents.edit")
_STOCK_READ = ("inventory.stock.view", "inventory.stock.edit")
_READ_GATES: dict[str, Optional[tuple[str, ...]]] = {
    "companies": None,  # superadmin
    "agents": _AGENTS_READ,
    "agent_fields": _AGENTS_READ,
    "stock_visibility": _STOCK_READ,
}


def unreadable_sections(can: Callable[[str], bool], is_superadmin: Callable[[], bool]) -> frozenset[str]:
    """The sections this viewer may not read (their item values and names are withheld)."""
    out = set()
    for section, slugs in _READ_GATES.items():
        if not (is_superadmin() if slugs is None else any(can(s) for s in slugs)):
            out.add(section)
    return frozenset(out)


def row_summary(result: dict) -> dict:
    diff = [i["label"] for i in result["items"] if i["differs"]]
    return {"status": result["status"], "differ_count": len(diff), "differ_labels": diff}


def tag_settings_for_contact(db: Session, contact, redact: frozenset[str] = frozenset()) -> dict:
    return evaluate(db, [contact], redact=redact)[str(contact.id)]


def holders_of(db: Session, tag_id: str) -> list:
    from app.models.access import RespondContact, respond_contact_tags as pivot

    return (
        db.query(RespondContact)
        .filter(RespondContact.id.in_(select(pivot.c.contact_id).where(pivot.c.tag_id == tag_id)))
        .all()
    )


def differs_count(db: Session, tag_id: str) -> Optional[int]:
    from app.services import contact_access_copy_service

    holders = holders_of(db, tag_id)
    if not holders:
        return 0
    if len(holders) > contact_access_copy_service.MAX_DIFF_CANDIDATES:
        return None  # the same cap as the list filter
    res = evaluate(db, holders, only_tags={tag_id})
    return sum(1 for r in res.values() if r["status"] == "differs")


def leaves_of(settings: Optional[dict]) -> list:
    """Whether a raw settings object sets anything (for callers holding no TagRef)."""
    return leaves(TagRef("", "", settings or {}))


def settings_catalog(db: Session, can: Callable[[str], bool], is_superadmin: Callable[[], bool]) -> dict:
    """Every pick list the caller may read, as `[{value, label}]`, in one read.

    A list the caller cannot read is ABSENT (companies: superadmin; agents and their field keys:
    access_agents.view; warehouses: inventory.stock.view); brands follow the caller's company scope."""
    from app.models.product_spec import ProductSpecRegistry
    from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS
    from app.services.portal_form_visibility_service import _KIND_LABELS
    from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES

    cat = Catalog(db, scope_brands=True)

    def opts(name: str) -> list[dict]:
        return sorted(
            ({"value": v, "label": n} for v, n in cat._load(name).items()), key=lambda o: o["label"].lower()
        )

    out = {
        "access_types": opts("access_types"),
        "brands": opts("brands"),
        "market_segments": opts("market_segments"),
        "attachment_types": opts("attachment_types"),
        "field_reveals": [{"value": k, "label": label} for k, label in sorted(FIELD_REVEAL_KEYS)],
        "portal_forms": [
            {"value": k, "label": _KIND_LABELS.get(k, k.replace("_", " ").capitalize())}
            for k in GRANTABLE_PORTAL_FORM_TYPES
        ],
        "spec_keys": [
            {"value": k, "label": label}
            for k, label in db.query(ProductSpecRegistry.spec_key, ProductSpecRegistry.label)
            .filter(ProductSpecRegistry.is_active.is_(True))
            .order_by(ProductSpecRegistry.label)
        ],
    }
    if is_superadmin():
        out["companies"] = opts("companies")
    if any(can(s) for s in _AGENTS_READ):
        from app.services.field_access import GATED_FIELDS

        out["agents"] = sorted(
            ({"value": i, "label": n, "code": c} for i, (c, n) in cat.agents().items()),
            key=lambda o: o["label"].lower(),
        )
        out["agent_fields"] = [
            {"agent_code": owner, "resource": resource, "field_key": field_key}
            for resource, fields in GATED_FIELDS.items()
            for field_key, owner in fields.items()
        ]
    if any(can(s) for s in _STOCK_READ):
        out["warehouses"] = opts("warehouses")
    return out
