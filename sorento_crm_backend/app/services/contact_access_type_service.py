"""Contact access type catalog service.

Source of truth for the configurable per-tenant access-type catalog
(``contact_access_types``) and the many-to-many assignment between
respond contacts and access types (``respond_contact_access_types``).

Promotion / attachment visibility is evaluated by overlapping a contact's
assigned access codes against a resource's ``access_levels`` JSONB array.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models.access import (
    ContactAccessType,
    RespondContact,
    respond_contact_access_types,
)
from app.models.respond_workspace import RespondWorkspace
from app.services.error_handler import handle_validation_error, handle_conflict, handle_not_found

logger = logging.getLogger(__name__)

# Default codes to use when no catalog exists yet (e.g. before migration)
_FALLBACK_DEFAULT_CODES = ["dealer", "end_user"]


_ACCESS_LABEL_NONALNUM = re.compile(r"[^a-z0-9 ]")
_ACCESS_LABEL_SEPS = re.compile(r"[\s\-_/]+")


def _normalize_access_label(value: str) -> str:
    """Lower, swap separators to space, strip punctuation, collapse, trim."""
    if not value:
        return ""
    s = _ACCESS_LABEL_SEPS.sub(" ", value.strip().casefold())
    s = _ACCESS_LABEL_NONALNUM.sub("", s)
    return " ".join(s.split())


def _clean_keywords(value) -> list[str]:
    """Coerce admin-supplied keyword payload to a clean str list (dedup, strip, drop empties)."""
    if not value:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in value:
        s = str(v or "").strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _tokenize_access_label(value: str) -> set[str]:
    """Token set with naive plural strip (drop trailing 's' for tokens >3 chars not ending 'ss')."""
    tokens: set[str] = set()
    for tok in _normalize_access_label(value).split():
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        if tok:
            tokens.add(tok)
    return tokens


class ContactAccessTypeService:
    """CRUD on ``contact_access_types`` plus contact↔access-type M2M helpers."""

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- catalog
    def list_active_codes(self) -> list[str]:
        """Return codes of active contact access types, ordered by sort_order then code."""
        rows = (
            self.db.query(ContactAccessType.code)
            .filter(ContactAccessType.is_active.is_(True))
            .order_by(ContactAccessType.sort_order.asc().nullslast(), ContactAccessType.code.asc())
            .all()
        )
        return [r[0] for r in rows] if rows else _FALLBACK_DEFAULT_CODES.copy()

    def get_default_access_levels(self) -> list[str]:
        """Return default access_levels for new promotions/attachments (all active codes, or fallback)."""
        codes = self.list_active_codes()
        return codes if codes else _FALLBACK_DEFAULT_CODES.copy()

    def translate_names_to_codes(self, names: Optional[list[str]]) -> Optional[list[str]]:
        """Map access-level NAMES → canonical codes via contact_access_types.name.

        Case-insensitive name match against the full catalog (active or not - admins may
        rename codes; promotion JSONB still references the legacy code). Returns
        ``None`` when ``names`` is falsy / empty so the caller can short-circuit the
        filter. Empty translated list (no name matched anything) is preserved as
        ``[]`` so the JSONB overlap filter rejects every row - matches user intent
        ("filter by these names" + zero recognized names = zero results).
        """
        if not names:
            return None
        cleaned = [(n or "").strip() for n in names]
        cleaned = [n for n in cleaned if n]
        if not cleaned:
            return None
        lookup = {n.lower() for n in cleaned}
        from sqlalchemy import func
        rows = (
            self.db.query(ContactAccessType.code)
            .filter(func.lower(ContactAccessType.name).in_(lookup))
            .all()
        )
        codes: list[str] = []
        seen: set[str] = set()
        for (code,) in rows:
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes

    def translate_names_to_codes_for_caller(
        self, names: Optional[list[str]], *, contact_id: Optional[str]
    ) -> Optional[list[str]]:
        """``translate_names_to_codes``, failing CLOSED for a read made on behalf of a contact.

        SEC-PROMO-ACCESS: a contact-scoped caller (the chatbot through MCP, which sends a
        non-blank ``contact_id``) whose access list is empty or absent gets ``[]`` (nothing),
        never ``None`` (no filter). The MCP layer drops an empty list from the query string,
        so "the contact holds nothing for this ask" reaches the route as no ``access_levels``
        at all. Staff and internal callers send no ``contact_id`` and keep ``None``.
        """
        codes = self.translate_names_to_codes(names)
        if codes is None and (contact_id or "").strip():
            return []
        return codes

    def validate_access_levels(self, access_levels: list[str], field_name: str = "access_levels") -> list[str]:
        """Validate codes against the active catalog. Returns normalized (stripped, deduplicated) list."""
        if not access_levels:
            raise handle_validation_error(f"{field_name} must contain at least one access type.")
        allowed = set(self.list_active_codes())
        normalized: list[str] = []
        seen: set[str] = set()
        for a in access_levels:
            code = (a or "").strip()
            if not code:
                continue
            if code not in allowed:
                raise handle_conflict(
                    f"Invalid access type(s): {[code]}. Allowed (from catalog): {sorted(allowed)}."
                )
            if code not in seen:
                seen.add(code)
                normalized.append(code)
        if not normalized:
            raise handle_validation_error(f"{field_name} must contain at least one access type.")
        return normalized

    def list_types_for_api(self) -> list[dict]:
        """Return list of {code, name, description, sort_order, keywords} for active types (API options)."""
        rows = (
            self.db.query(
                ContactAccessType.code,
                ContactAccessType.name,
                ContactAccessType.description,
                ContactAccessType.sort_order,
                ContactAccessType.keywords,
            )
            .filter(ContactAccessType.is_active.is_(True))
            .order_by(ContactAccessType.sort_order.asc().nullslast(), ContactAccessType.code.asc())
            .all()
        )
        return [
            {
                "code": r[0],
                "name": r[1] or r[0],
                "description": r[2],
                "sort_order": r[3],
                "keywords": r[4] if isinstance(r[4], list) else [],
            }
            for r in rows
        ]

    def list_all_types(self) -> list[ContactAccessType]:
        """Return all contact access types (including inactive) for admin UI, ordered by sort_order then code."""
        return (
            self.db.query(ContactAccessType)
            .order_by(ContactAccessType.sort_order.asc().nullslast(), ContactAccessType.code.asc())
            .all()
        )

    def get_type_by_code(self, code: str) -> Optional[ContactAccessType]:
        """Get a single contact access type by code."""
        return self.db.query(ContactAccessType).filter(ContactAccessType.code == code).first()

    def create_type(self, data: dict) -> ContactAccessType:
        """Create a new contact access type. Raises if code already exists."""
        existing = self.get_type_by_code(data["code"])
        if existing:
            raise handle_conflict(f"Contact access type with code '{data['code']}' already exists.")
        row = ContactAccessType(
            code=data["code"],
            name=data["name"],
            description=data.get("description"),
            is_active=data.get("is_active", True),
            sort_order=data.get("sort_order"),
            keywords=_clean_keywords(data.get("keywords")),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_type(self, code: str, data: dict) -> ContactAccessType:
        """Update a contact access type. Code cannot be changed."""
        row = self.get_type_by_code(code)
        if not row:
            raise handle_validation_error(f"Contact access type '{code}' not found.")
        if "name" in data:
            row.name = data["name"]
        if "description" in data:
            row.description = data["description"]
        if "is_active" in data:
            row.is_active = data["is_active"]
        if "sort_order" in data:
            row.sort_order = data["sort_order"]
        if "keywords" in data:
            row.keywords = _clean_keywords(data["keywords"])
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_type(self, code: str) -> None:
        """Delete a contact access type. Cascades remove all contact assignments via the pivot."""
        row = self.get_type_by_code(code)
        if not row:
            raise handle_validation_error(f"Contact access type '{code}' not found.")
        self.db.delete(row)
        self.db.commit()

    # ---------------------------------------------------------- contact M2M
    def set_contact_access_codes(self, contact_id: str, codes: List[str]) -> List[str]:
        """Replace the contact's access-type assignment with ``codes``.

        Validates each code against the active catalog, deduplicates, and rewrites
        the pivot in a single transaction. ``codes=[]`` clears all assignments.
        Returns the final list of assigned codes (deterministic by sort_order).
        """
        normalized: list[str] = []
        if codes:
            allowed = set(self.list_active_codes())
            seen: set[str] = set()
            for code in codes:
                c = (code or "").strip()
                if not c or c in seen:
                    continue
                if c not in allowed:
                    raise handle_conflict(
                        f"Invalid access type(s): {[c]}. Allowed (from catalog): {sorted(allowed)}."
                    )
                seen.add(c)
                normalized.append(c)

        self.db.execute(
            respond_contact_access_types.delete().where(
                respond_contact_access_types.c.contact_id == contact_id
            )
        )
        if normalized:
            self.db.execute(
                insert(respond_contact_access_types),
                [{"contact_id": contact_id, "access_type_code": c} for c in normalized],
            )
        return normalized

    def get_contact_access_codes(self, contact_id: str) -> List[str]:
        """Return the contact's assigned access codes ordered by catalog sort_order then code."""
        rows = (
            self.db.query(respond_contact_access_types.c.access_type_code)
            .join(
                ContactAccessType,
                ContactAccessType.code == respond_contact_access_types.c.access_type_code,
            )
            .filter(respond_contact_access_types.c.contact_id == contact_id)
            .order_by(
                ContactAccessType.sort_order.asc().nullslast(),
                ContactAccessType.code.asc(),
            )
            .all()
        )
        return [r[0] for r in rows]

    def resolve_active_access_levels_for_contact(
        self,
        respond_io_id: str,
        space_id: str,
    ) -> list[dict]:
        """Return [{name, keywords}] for the contact's CURRENTLY-ACTIVE access codes.

        Filtered against the active catalog so deactivated rows never surface.
        ``keywords`` exposes the admin-curated lookup synonyms so MCP agents can
        match user phrasing without an extra catalog round-trip.
        """
        codes = self.resolve_contact_access_codes(respond_io_id, space_id)
        if not codes:
            return []
        rows = (
            self.db.query(
                ContactAccessType.code,
                ContactAccessType.name,
                ContactAccessType.keywords,
            )
            .filter(
                ContactAccessType.is_active.is_(True),
                ContactAccessType.code.in_(codes),
            )
            .order_by(
                ContactAccessType.sort_order.asc().nullslast(),
                ContactAccessType.code.asc(),
            )
            .all()
        )
        return [
            {
                "name": r[1] or r[0],
                "keywords": r[2] if isinstance(r[2], list) else [],
            }
            for r in rows
        ]

    def resolve_contact_access_codes(self, respond_io_id: str, space_id: str) -> List[str]:
        """Resolve a Respond.io (contact_id, space_id) pair to the contact's access codes.

        Raises 404 when no contact matches. The contact must belong to a workspace
        whose ``space_id`` matches the supplied value (exact string match).
        """
        rid = (respond_io_id or "").strip()
        sid = (space_id or "").strip()
        if not rid or not sid:
            raise handle_validation_error("contact_id and space_id are both required.")
        contact = (
            self.db.query(RespondContact)
            .join(RespondWorkspace, RespondWorkspace.id == RespondContact.workspace_id)
            .filter(RespondContact.respond_io_id == rid, RespondWorkspace.space_id == sid)
            .first()
        )
        if contact is None:
            raise handle_not_found("RespondContact", f"respond_io_id={rid}, space_id={sid}")
        return self.get_contact_access_codes(str(contact.id))

    def resolve_contact_company_ids(self, respond_io_id: str, space_id: str) -> List[str]:
        """Resolve a (contact_id, space_id) pair to the contact's company ids.

        Sibling of ``resolve_contact_access_codes`` for multi-company isolation
        (PLAN §3.13). Reads the admin-managed ``respond_contact_companies`` M2M.

        The contact itself is resolved by ``field_access.resolve_contact_id``, the
        SAME function the stock-visibility policy and the agent field grants use,
        and deliberately so: both accept either id space (internal
        ``respond_contacts.id`` or the Respond.io id, disambiguated by
        ``space_id``). While this resolver read ``respond_io_id`` alone, a caller
        holding the internal id got a POLICY applied over an EMPTY company scope -
        the reply read "we have none of that" when it meant "I could not tell
        which company you are". Two resolutions of one identity must not disagree.

        Non-raising by design (this feeds the request-entry scope resolver, which
        must never 500): returns ``[]`` when the params are blank, when NO contact
        matches, OR when the contact matched but has NO company memberships - the
        caller maps ``[]`` to an empty scope (0 owned rows, fail-closed).
        """
        rid = (respond_io_id or "").strip()
        sid = (space_id or "").strip()
        if not rid or not sid:
            return []
        from app.services.field_access import resolve_contact_id

        contact_id = resolve_contact_id(self.db, rid, sid)
        if not contact_id:
            return []
        from app.models.company import RespondContactCompany

        rows = (
            self.db.query(RespondContactCompany.company_id)
            .filter(RespondContactCompany.respond_contact_id == contact_id)
            .all()
        )
        return [r[0] for r in rows]

