"""Per-salesman → CS PIC pin-point routing (procurement form-SLA CS stage).

Admin pins a salesman (respond_contact) to a specific customer-service PIC for a
procurement use_case. At CS-stage spawn the form-SLA resolver consults these pins
(see ``form_sla_service._resolve_pinned_assignee``); a valid pin overrides
round-robin. Only the ``customer_service`` stage of the ``purchase_request`` access
agent reads them. A pin may target any eligible PIC (``eligible_pics_query``), not
only a member of that stage's team (CS-ROUTING-TARGETS, 14 Oct 2026).

See docs/plans/PLAN-procurement-cs-handoff-and-pinpoint-routing.md and
documentation/plans/forms/PLAN-cs-routing-targets.md.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Query, Session

from app.models.access import RespondContact, RespondContactCsRouting
from app.models.company import UserCompany
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
    UserStatus,
)
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_validation_error,
)

# The access agent + team_set whose stage reads the pins.
CS_AGENT_CODE = "purchase_request"
CS_TEAM_SET_CODE = "customer_service"
PINNABLE_USE_CASES = ("purchase_request", "sponsorship_form")
_USE_CASE_LABELS = {"purchase_request": "Purchase Request", "sponsorship_form": "Sponsorship Form"}

# The permission the CS actions on a purchase request check (purchase_requests.py
# mark-processed / close routes), so it is what makes someone a CS PIC.
PIC_PERMISSION = "procurement.purchase_requests.process"
# These roles pass every permission check (UserPermissionService.check_user_has_permission),
# so holding one says nothing about who processes purchase requests.
_BYPASS_ROLE_SLUGS = ("admin", "superadmin")


def eligible_pics_query(db: Session, *, company_id: Optional[str] = None) -> Query:
    """THE eligible-PIC rule, and the only copy of it: the users a routing rule may
    pin. Active, not trashed, not an integration user, holding ``PIC_PERMISSION``
    through a role other than admin/superadmin, and granted ``company_id``.

    ``company_id`` defaults to Sorento, as the tier-1 team lookup it replaces did,
    because the pin dropdown and the save check have no contact in scope. Assignment
    passes the pinned contact's own company (AC-E6). The dropdown, the save check and
    the form SLA resolver all go through here, so narrowing the rule is one edit.
    """
    from app.services.company_routing_service import DEFAULT_COMPANY_ID

    holds_permission = exists(
        select(UserRoleAssignment.id)
        .join(UserRole, UserRole.id == UserRoleAssignment.role_id)
        .join(UserRolePermission, UserRolePermission.role_id == UserRole.id)
        .join(UserPermission, UserPermission.id == UserRolePermission.permission_id)
        .where(
            UserRoleAssignment.user_id == User.id,
            UserPermission.slug == PIC_PERMISSION,
            UserRole.slug.notin_(_BYPASS_ROLE_SLUGS),
        )
    )
    granted_company = exists(
        select(UserCompany.id).where(
            UserCompany.user_id == User.id,
            UserCompany.company_id == str(company_id or DEFAULT_COMPANY_ID),
        )
    )
    return db.query(User).filter(
        User.status == UserStatus.ACTIVE.value,
        User.is_trashed.is_(False),
        User.is_integration.is_(False),
        holds_permission,
        granted_company,
    )


def is_eligible_pic(db: Session, user_id: str, *, company_id: Optional[str] = None) -> bool:
    """Whether ``user_id`` may be the target of a routing rule (``eligible_pics_query``)."""
    if not user_id:
        return False
    return (
        eligible_pics_query(db, company_id=company_id)
        .filter(User.id == str(user_id))
        .first()
        is not None
    )


def _normalized_conditions(match_conditions: Optional[list]) -> list:
    """Validate + shape an incoming predicate list to [{field, operator, value}].

    Rejects unknown operators and malformed predicates (422). An empty/None list is
    the wildcard []. Field/value are stored as-is (strings); type-appropriate
    operators are enforced in the UI, but the operator vocabulary is enforced here."""
    from app.services.cs_routing_match import VALID_OPERATORS

    if not match_conditions:
        return []
    out: list[dict] = []
    for i, pred in enumerate(match_conditions):
        if not isinstance(pred, dict):
            raise handle_validation_error(f"match_conditions[{i}] must be an object.")
        field = pred.get("field")
        operator = pred.get("operator")
        value = pred.get("value")
        if not field or not isinstance(field, str):
            raise handle_validation_error(f"match_conditions[{i}].field is required.")
        if operator not in VALID_OPERATORS:
            raise handle_validation_error(
                f"match_conditions[{i}].operator must be one of {VALID_OPERATORS}; "
                f"got {operator!r}."
            )
        out.append({"field": field, "operator": operator, "value": value})
    # Store in canonical (sorted) order so the DB unique index
    # md5(match_conditions::text) is deterministic per logical condition-set - 
    # matching app-side canonical_conditions (predicate order is not semantic; AND).
    out.sort(key=lambda c: (str(c["field"]), str(c["operator"]), str(c["value"])))
    return out


class CsRoutingService:
    """Manage salesman → CS PIC pins and resolve the CS candidate pool."""

    def __init__(self, db: Session):
        self.db = db

    # ---- candidate pool ----------------------------------------------------

    def list_candidates(self) -> list[dict]:
        """Every eligible PIC (``eligible_pics_query``), sorted by name, for the pin
        dropdown. Empty (not an error) when nobody qualifies, so the FE renders its
        empty state."""
        display_name = func.coalesce(User.name, User.email)
        users = (
            eligible_pics_query(self.db)
            .order_by(func.lower(display_name).asc(), User.id.asc())
            .all()
        )
        return [{"id": u.id, "name": (u.name or u.email), "email": u.email} for u in users]

    # ---- routable fields (predicate builder) -------------------------------

    # The form-header table backing each use_case (predicates match header fields).
    _USE_CASE_TABLE = {
        "purchase_request": "purchase_requests",
        "sponsorship_form": "purchase_requests",
        "complaint": "complaints",
        "stock_inquiry": "stock_inquiries",
    }
    # Curated non-lookup header fields offered as routing dimensions per table
    # (label, type). Excludes system/audit/status columns.
    _CURATED_FIELDS: dict[str, list[tuple[str, str, str]]] = {
        "purchase_requests": [
            ("customer_name", "Customer Name", "string"),
            ("project_title", "Project Title", "string"),
            ("total_project_value", "Total Project Value", "numeric"),
        ],
        "complaints": [
            ("customer_name", "Customer Name", "string"),
            ("complaint_type", "Complaint Type", "string"),
        ],
        "stock_inquiries": [
            ("customer_name", "Customer Name", "string"),
        ],
    }

    def routable_fields(self, use_case: str) -> list[dict]:
        """Fields a routing predicate can match, for a use_case's form. Lookup-bound
        columns (with their options, type='lookup') + a curated set of common
        string/numeric header fields."""
        from app.models.lookup import LookupBinding, LookupOption, LookupSet

        table = self._USE_CASE_TABLE.get(use_case)
        if not table:
            return []
        fields: list[dict] = []
        seen: set[str] = set()
        # Lookup-bound columns on the table.
        bindings = (
            self.db.query(LookupBinding)
            .filter(LookupBinding.table_name == table)
            .all()
        )
        for b in bindings:
            s = self.db.query(LookupSet).filter(LookupSet.id == b.set_id).first()
            if not s:
                continue
            opts = (
                self.db.query(LookupOption)
                .filter(LookupOption.set_id == s.id, LookupOption.is_active.is_(True))
                .order_by(LookupOption.sort_order.asc(), LookupOption.label.asc())
                .all()
            )
            fields.append({
                "field": b.column_name,
                "label": b.column_name.replace("_", " ").title(),
                "type": "lookup",
                "options": [{"value": o.value, "label": o.label} for o in opts],
            })
            seen.add(b.column_name)
        # Curated common fields (skip any already surfaced as a lookup).
        for field, label, ftype in self._CURATED_FIELDS.get(table, []):
            if field not in seen:
                fields.append({"field": field, "label": label, "type": ftype})
        return fields

    # ---- pin CRUD ----------------------------------------------------------

    def _require_contact(self, contact_id: str) -> RespondContact:
        contact = (
            self.db.query(RespondContact)
            .filter(RespondContact.id == contact_id)
            .first()
        )
        if not contact:
            raise handle_not_found("Contact", contact_id)
        return contact

    def list_for_contact(self, contact_id: str) -> list[dict]:
        """All active routing rows for a contact (predicates + priority + PIC name),
        ordered by use_case then priority (the admin evaluation order)."""
        self._require_contact(contact_id)
        rows = (
            self.db.query(RespondContactCsRouting)
            .filter(
                RespondContactCsRouting.respond_contact_id == contact_id,
                RespondContactCsRouting.is_active.is_(True),
            )
            .order_by(
                RespondContactCsRouting.use_case.asc(),
                RespondContactCsRouting.priority.asc(),
                RespondContactCsRouting.created_at.asc(),
            )
            .all()
        )
        if not rows:
            return []
        users = {
            u.id: u
            for u in self.db.query(User)
            .filter(User.id.in_([r.cs_pic_user_id for r in rows]))
            .all()
        }
        out: list[dict] = []
        for r in rows:
            u = users.get(r.cs_pic_user_id)
            out.append(
                {
                    "id": r.id,
                    "use_case": r.use_case,
                    "cs_pic_user_id": r.cs_pic_user_id,
                    "cs_pic_name": (u.name or u.email) if u else None,
                    "match_conditions": r.match_conditions or [],
                    "priority": r.priority or 0,
                }
            )
        return out

    def upsert(
        self,
        contact_id: str,
        use_case: str,
        cs_pic_user_id: str,
        *,
        match_conditions: Optional[list] = None,
        priority: int = 0,
        created_by: Optional[str] = None,
    ) -> RespondContactCsRouting:
        """Create or update a routing row for (contact, use_case, condition-set).

        Uniqueness is per distinct condition-set (canonicalized), so a contact may
        have several rows for the same use_case routing different predicate sets to
        different PICs. Validates the use_case and that the CS PIC is an eligible PIC
        (``is_eligible_pic``). ``match_conditions`` is stored canonicalized so
        logically-equal sets collide on the unique index.
        """
        from app.services.cs_routing_match import canonical_conditions

        self._require_contact(contact_id)
        if use_case not in PINNABLE_USE_CASES:
            raise handle_validation_error(
                f"use_case must be one of {PINNABLE_USE_CASES}; got {use_case!r}."
            )
        if not is_eligible_pic(self.db, cs_pic_user_id):
            raise handle_validation_error(
                "The selected user cannot be assigned. Pick someone active who can "
                "process purchase requests."
            )
        conds = _normalized_conditions(match_conditions)
        key = canonical_conditions(conds)
        # Find an existing row with the SAME canonical condition-set (not just use_case).
        row = next(
            (
                r
                for r in self.db.query(RespondContactCsRouting).filter(
                    RespondContactCsRouting.respond_contact_id == contact_id,
                    RespondContactCsRouting.use_case == use_case,
                )
                if canonical_conditions(r.match_conditions or []) == key
            ),
            None,
        )
        if row:
            row.cs_pic_user_id = cs_pic_user_id
            row.priority = priority
            row.match_conditions = conds
            row.is_active = True
        else:
            row = RespondContactCsRouting(
                respond_contact_id=contact_id,
                use_case=use_case,
                cs_pic_user_id=cs_pic_user_id,
                match_conditions=conds,
                priority=priority,
                is_active=True,
                created_by=created_by,
            )
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def replace_all(
        self,
        contact_id: str,
        rules: list[dict],
        *,
        created_by: Optional[str] = None,
    ) -> list[dict]:
        """Replace every routing rule of a contact with ``rules``, in one transaction.

        Every rule is validated before anything is deleted (use_case, an eligible PIC,
        the predicate shape, no two rules with the same conditions for one use_case),
        so a bad rule raises a 400 naming it ("Rule 2: ...", 1-based, the page's own
        order) and the stored rules stay exactly as they were. Returns the stored
        rules as ``list_for_contact`` shapes them.
        """
        from app.services.cs_routing_match import canonical_conditions

        self._require_contact(contact_id)
        prepared: list[dict] = []
        first_rule_for: dict[tuple[str, str], int] = {}
        for n, rule in enumerate(rules, start=1):
            use_case = rule.get("use_case")
            if use_case not in PINNABLE_USE_CASES:
                raise handle_validation_error(
                    f"Rule {n}: use_case must be one of {PINNABLE_USE_CASES}; got {use_case!r}."
                )
            pic = rule.get("cs_pic_user_id")
            if not is_eligible_pic(self.db, pic):
                user = self.db.query(User).filter(User.id == str(pic)).first() if pic else None
                who = (user.name or user.email) if user else "The selected user"
                raise handle_validation_error(
                    f"Rule {n}: {who} cannot be assigned. Pick someone active who can "
                    "process purchase requests."
                )
            try:
                conds = _normalized_conditions(rule.get("match_conditions"))
            except AppException as exc:
                raise handle_validation_error(f"Rule {n}: {exc.detail['message']}") from exc
            key = (use_case, canonical_conditions(conds))
            if key in first_rule_for:
                raise handle_validation_error(
                    f"Rule {n} has the same conditions as Rule {first_rule_for[key]} for "
                    f"{_USE_CASE_LABELS[use_case]}. Change or remove one of them."
                )
            first_rule_for[key] = n
            prepared.append(
                {
                    "use_case": use_case,
                    "cs_pic_user_id": pic,
                    "match_conditions": conds,
                    "priority": int(rule.get("priority") or 0),
                }
            )

        # Bulk DELETE runs at once, so the inserts below cannot collide with the
        # rows they replace on the (contact, use_case, conditions) unique index.
        self.db.query(RespondContactCsRouting).filter(
            RespondContactCsRouting.respond_contact_id == contact_id,
            RespondContactCsRouting.use_case.in_(PINNABLE_USE_CASES),
        ).delete(synchronize_session=False)
        for p in prepared:
            self.db.add(
                RespondContactCsRouting(
                    respond_contact_id=contact_id,
                    is_active=True,
                    created_by=created_by,
                    **p,
                )
            )
        self.db.commit()
        return self.list_for_contact(contact_id)

    def delete(self, contact_id: str, use_case: str, row_id: Optional[str] = None) -> None:
        """Clear routing rows. With ``row_id`` deletes one specific row; without it,
        clears every row for (contact, use_case) → reverts to round-robin. Idempotent."""
        self._require_contact(contact_id)
        q = self.db.query(RespondContactCsRouting).filter(
            RespondContactCsRouting.respond_contact_id == contact_id,
            RespondContactCsRouting.use_case == use_case,
        )
        if row_id:
            q = q.filter(RespondContactCsRouting.id == row_id)
        rows = q.all()
        for row in rows:
            self.db.delete(row)
        if rows:
            self.db.commit()
