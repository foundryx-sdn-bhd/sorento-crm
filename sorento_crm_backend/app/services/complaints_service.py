"""Complaints service for business logic."""
# ORM models declare Column[T] on the class; at runtime instance attributes are Python values.
# Pyright reports false positives here until models use SQLAlchemy 2.0 Mapped[] typing.
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportArgumentType=false
# pyright: reportCallIssue=false
# pyright: reportReturnType=false
import logging
import secrets
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, inspect, func
from typing import List, Optional, Iterable
from app.config import settings
from app.models.complaints import Complaint
from app.models.order import Order, OrderLine
from app.models.product import Product
from app.models.procurement import ViewToken
from app.models.sla import ConversationSLATracking
from app.services.sla_scope import open_tracker_scope
from app.schemas.complaints import ComplaintCreate, ComplaintUpdate
from app.services.contact_brand_scope import complaint_in_scope_clauses, in_scope_product_codes
from app.services.error_handler import handle_not_found
from app.services.response_gate import (
    ALLOWED_RESPONSE_STATUSES,
    assert_response_write_allowed,
    response_text_changed,
)
from app.services.entity_attachment_service import EntityAttachmentService
from app.services.banner_person_service import wa_phone_for_respond_user_id


# Sentinel: distinguishes "caller passed a precomputed value (maybe None)" from
# "caller passed nothing, fall back to a per-row lookup" in _serialize_complaint.
_UNSET = object()


class ComplaintService:
    """Service for complaint operations."""
    
    def __init__(self, db: Session):
        self.db = db
        self.entity_attachment_service = EntityAttachmentService(db)
        self._complaint_submission_required_fields: tuple[str, ...] = (
            "delivery_order_number",
            "complaint_date",
            "product_code",
            "complaint_type",
            "defect_description",
            "customer_name",
            "contact_number",
            "defects_discovered",
            "salesperson",
            "customer_address",
            "customer_type",
            "within_warranty",
            "product_type",
            "contact_person",
            "project_title",
            "contact_id",
            "space_id",
        )

    def get_submission_missing_fields(self, complaint_data: ComplaintCreate) -> list[str]:
        """Return missing/empty required complaint fields for submission."""
        payload = complaint_data.model_dump()
        missing: list[str] = []
        for key in self._complaint_submission_required_fields:
            value = payload.get(key)
            if value is None:
                missing.append(key)
                continue
            if isinstance(value, str) and not value.strip():
                missing.append(key)
        return missing

    def validate_submission_completeness(self, complaint_data: ComplaintCreate) -> None:
        """For external/MCP submission flow: require complete complaint payload before confirmation."""
        missing = self.get_submission_missing_fields(complaint_data)
        if missing:
            from app.services.error_handler import handle_validation_error

            raise handle_validation_error(
                "Complaint submission is incomplete. Required fields: "
                + ", ".join(self._complaint_submission_required_fields)
                + f". Missing or empty: {', '.join(missing)}."
            )

    def normalize_delivery_order_numbers(self, raw_value: Optional[str]) -> list[str]:
        """Normalize comma/semicolon/newline separated DO entries into unique tokens."""
        if raw_value is None:
            return []
        text = str(raw_value).strip()
        if not text:
            return []
        for sep in (";", "\n", "|"):
            text = text.replace(sep, ",")
        out: list[str] = []
        seen: set[str] = set()
        for part in text.split(","):
            token = (part or "").strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out

    def resolve_delivery_order_numbers(
        self, raw_value: Optional[str]
    ) -> tuple[list[str], list[str], list[str]]:
        """Resolve supplied DO numbers against orders.order_number.

        Returns:
          - resolved: canonical order numbers found in DB (preserve user order),
          - missing: user-provided tokens not found,
          - provided: normalized user-provided tokens.
        """
        provided = self.normalize_delivery_order_numbers(raw_value)
        if not provided:
            return [], [], []
        lowered = [s.lower() for s in provided]
        rows = (
            self.db.query(Order.order_number)
            .filter(Order.deleted_at.is_(None), func.lower(Order.order_number).in_(lowered))
            .all()
        )
        canonical_by_lower = {str(order_number).lower(): str(order_number) for (order_number,) in rows if order_number}
        resolved: list[str] = []
        missing: list[str] = []
        for token in provided:
            canonical = canonical_by_lower.get(token.lower())
            if canonical:
                resolved.append(canonical)
            else:
                missing.append(token)
        return resolved, missing, provided

    def infer_complaint_defaults_from_delivery_orders(self, do_numbers: list[str]) -> dict[str, object]:
        """Infer customer + product hints from selected delivery order numbers.

        Returns a dict with:
          - delivery_order_numbers
          - customer_name (when exactly one unique debtor_name found)
          - customer_name_candidates (all unique debtor names found)
          - product_code (when exactly one unique product code found)
          - product_codes (all unique product codes found)
          - product_names (all unique product names found)
        """
        normalized = self.normalize_delivery_order_numbers(", ".join(do_numbers))
        if not normalized:
            return {
                "delivery_order_numbers": [],
                "customer_name": None,
                "customer_name_candidates": [],
                "product_code": None,
                "product_codes": [],
                "product_names": [],
            }

        lowered = [s.lower() for s in normalized]
        orders = (
            self.db.query(Order.order_number, Order.debtor_name)
            .filter(Order.deleted_at.is_(None), func.lower(Order.order_number).in_(lowered))
            .all()
        )
        products = (
            self.db.query(Order.order_number, Product.product_code, Product.product_name)
            .join(OrderLine, OrderLine.product_id == Product.id)
            .join(Order, Order.id == OrderLine.order_id)
            .filter(Order.deleted_at.is_(None), func.lower(Order.order_number).in_(lowered))
            .all()
        )

        debtor_names: list[str] = []
        debtor_seen: set[str] = set()
        for _, debtor_name in orders:
            name = (str(debtor_name or "")).strip()
            if not name:
                continue
            key = name.lower()
            if key in debtor_seen:
                continue
            debtor_seen.add(key)
            debtor_names.append(name)

        product_codes: list[str] = []
        product_names: list[str] = []
        code_seen: set[str] = set()
        name_seen: set[str] = set()
        for _, product_code, product_name in products:
            code = (str(product_code or "")).strip()
            name = (str(product_name or "")).strip()
            if code:
                code_key = code.lower()
                if code_key not in code_seen:
                    code_seen.add(code_key)
                    product_codes.append(code)
            if name:
                name_key = name.lower()
                if name_key not in name_seen:
                    name_seen.add(name_key)
                    product_names.append(name)

        inferred_customer_name = debtor_names[0] if len(debtor_names) == 1 else None
        inferred_product_code = product_codes[0] if len(product_codes) == 1 else None
        return {
            "delivery_order_numbers": normalized,
            "customer_name": inferred_customer_name,
            "customer_name_candidates": debtor_names,
            "product_code": inferred_product_code,
            "product_codes": product_codes,
            "product_names": product_names,
        }

    def _build_respond_inbox_url(self, contact_id: Optional[str], space_id: Optional[str]) -> Optional[str]:
        """Build respond.io inbox URL: {base}/space/{space_id}/inbox/{respond_io_id}.

        Accepts internal RespondContact.id (UUID) and resolves it via DB to the
        numeric respond_io_id required by the Respond.io app URL + API.
        """
        if not contact_id or not space_id:
            return None
        from app.services.respond_identifier import (
            format_respond_inbox_url,
            resolve_respond_io_id,
        )

        rid = resolve_respond_io_id(self.db, contact_id)
        return format_respond_inbox_url(settings.respond_app_base_url, space_id, rid)

    def _normalize_complaint_reply_body_for_storage(self, raw: Optional[str]) -> str:
        """Keep only technician wording; strip legacy composed customer message template."""
        s = (raw or "").strip()
        if not s.startswith("There has been an update regarding your complaint"):
            return s
        idx = s.rfind(": ")
        if idx == -1:
            return s
        return s[idx + 2 :].strip()

    def _complaint_public_view_links_enabled(self) -> bool:
        """Match frontend App Store toggle for tokenized public view links."""
        from app.modules.runtime.installer import DEFAULT_TENANT_ID, is_module_enabled, tenant_has_any_module_row
        from app.modules.runtime.guards import PUBLIC_VIEW_LINKS_MODULE_KEY

        tenant_id = DEFAULT_TENANT_ID
        if not tenant_has_any_module_row(self.db, tenant_id):
            return True
        return is_module_enabled(self.db, tenant_id, PUBLIC_VIEW_LINKS_MODULE_KEY)

    def _latest_unresolved_sla_assignee_name(self, complaint_id: str) -> Optional[str]:
        """Return display name of latest unresolved SLA tracker assignee for this complaint."""
        from app.models.user import User
        tracker = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == "complaint",
                ConversationSLATracking.source_entity_id == complaint_id,
                *open_tracker_scope(),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .first()
        )
        if tracker is None:
            return None
        if tracker.assigned_to_id:
            user = self.db.query(User).filter(User.id == tracker.assigned_to_id).first()
            if user:
                return user.name or user.email or None
        if tracker.assigned_to:
            return self._resolve_user_display_name(tracker.assigned_to) or tracker.assigned_to
        return None

    def _resolve_user_display_name(self, user_id: Optional[str]) -> Optional[str]:
        """Resolve user id (CRM id or respond_user_id) to display name (name or email)."""
        if not user_id or not str(user_id).strip():
            return None
        from app.models.user import User
        user = (
            self.db.query(User)
            .filter(
                or_(User.id == user_id, User.respond_user_id == user_id)
            )
            .first()
        )
        if not user:
            return None
        return user.name or user.email or None

    def _serialize_complaint(
        self,
        complaint: Complaint,
        links_override: Optional[list] = None,
        print_count: Optional[int] = None,
        *,
        view_url_override: Optional[str] = None,
        assigned_to_name_override=_UNSET,
        handled_by_name_override=_UNSET,
        handled_by_wa_phone_override=_UNSET,
        rejected_by_wa_phone_override=_UNSET,
        last_responded_by_name_override=_UNSET,
        project_display_override=_UNSET,
    ) -> dict:
        """Serialize complaint with attachments from generic entity_attachment_links table.

        The *_override kwargs let the list path inject values it batched up front,
        so a 50-row page does not fire per-row view-token / user / SLA queries.
        When omitted, each falls back to its original per-row lookup (single-row
        detail path stays unchanged).
        """
        data = {attr.key: getattr(complaint, attr.key) for attr in inspect(complaint).mapper.column_attrs}
        data["system_id"] = str(complaint.id)
        # CONTACT-BRAND-SCOPE: a scoped contact is told only the products it may see.
        _csv = [p.strip() for p in (data.get("product_code") or "").split(",") if p.strip()]
        _allowed = in_scope_product_codes(self.db, _csv)
        if _allowed is not None:
            data["product_code"] = ",".join(p for p in _csv if p in _allowed) or None
        data["print_count"] = int(print_count or 0)
        data["form_type"] = "complaint"
        # A python property, so column_attrs skips it. The detail page gates its
        # response affordances on this rather than mirroring the allowed-status list
        # (UAC O1) - one source for the rule, on both sides of the wire.
        data["response_write_allowed"] = complaint.response_write_allowed
        # AC-L3 + the no-UUIDs-in-the-UI rule: the FE renders this dict, so a bare
        # `project_id` would put a UUID on screen. Resolved here rather than in the FE so
        # every surface (detail, list, PDF, webhook) shows the same identifier.
        data["project_code"] = None
        data["project_name"] = None
        if project_display_override is not _UNSET:
            # The list path resolved every linked project in one query. Batched for the same
            # reason as the view tokens and user names above: this lookup fired once per linked
            # row, which is the N+1 this serializer's whole override convention exists to avoid.
            code, name = project_display_override or (None, None)
            data["project_code"] = code
            data["project_name"] = name
        elif complaint.project_id:
            from app.models.projects import Project

            project = (
                self.db.query(Project.project_code, Project.title)
                .filter(Project.id == str(complaint.project_id))
                .first()
            )
            if project:
                data["project_code"] = project[0]
                data["project_name"] = project[1]
        data["view_url"] = (
            view_url_override
            if view_url_override is not None
            else self._build_complaint_view_url(str(complaint.id))
        )
        links = links_override if links_override is not None else self.entity_attachment_service.list_links("complaint", str(complaint.id))
        data["attachments"] = [
            self.entity_attachment_service.serialize_link(
                link,
                entity_key="complaint_id",
                link_type="complaint_attachment",
            )
            for link in links
        ]
        if last_responded_by_name_override is not _UNSET:
            data["last_responded_by_name"] = last_responded_by_name_override
        elif data.get("last_responded_by"):
            data["last_responded_by_name"] = self._resolve_user_display_name(data["last_responded_by"])
        else:
            data["last_responded_by_name"] = None
        if assigned_to_name_override is not _UNSET:
            data["assigned_to_name"] = assigned_to_name_override
        else:
            sla_assignee_name = self._latest_unresolved_sla_assignee_name(str(complaint.id))
            if sla_assignee_name:
                data["assigned_to_name"] = sla_assignee_name
            elif data.get("assigned_to"):
                data["assigned_to_name"] = self._resolve_user_display_name(data["assigned_to"])
            else:
                data["assigned_to_name"] = None
        # handled_by_id lives on the active SLA tracker (the form-handling-lock
        # holder), not on the complaint row, so only the batched override path
        # can populate it. Detail path has no tracker batch -> stays None.
        if handled_by_name_override is not _UNSET:
            data["handled_by_name"] = handled_by_name_override
        else:
            data["handled_by_name"] = None
        if handled_by_wa_phone_override is not _UNSET:
            data["handled_by_wa_phone"] = handled_by_wa_phone_override
        else:
            data["handled_by_wa_phone"] = None
        # Rejecter attribution for the rejection banner. complaint.rejected_by holds a
        # respond_user_id (NOT a users.id) - resolve name + wa.me digits via that path.
        if data.get("rejected_by"):
            data["rejected_by_name"] = self._resolve_user_display_name(data["rejected_by"])
            if rejected_by_wa_phone_override is not _UNSET:
                data["rejected_by_wa_phone"] = rejected_by_wa_phone_override
            else:
                data["rejected_by_wa_phone"] = wa_phone_for_respond_user_id(self.db, data["rejected_by"])
        else:
            data["rejected_by_name"] = None
            data["rejected_by_wa_phone"] = None
        # Void banner (BAN-1): resolve voided_by -> display name; wa phone null
        # (no form-banner-person-links resolver on this branch).
        data["voided_by_name"] = (
            self._resolve_user_display_name(data.get("voided_by"))
            if data.get("voided_by")
            else None
        )
        data["voided_by_wa_phone"] = None
        rc = getattr(complaint, "root_cause", None)
        data["root_cause_name"] = getattr(rc, "name", None) if rc is not None else None
        res = getattr(complaint, "resolution", None)
        data["resolution_name"] = getattr(res, "name", None) if res is not None else None
        return data
    
    def _build_list_query(
        self,
        query: Optional[str] = None,
        assigned_to: Optional[str] = None,
        status: Optional[str] = None,
        sort_field: str = "complaint_date",
        sort_dir: str = "asc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        root_cause_ids: Optional[List[str]] = None,
        resolution_ids: Optional[List[str]] = None,
        customer_names: Optional[List[str]] = None,
    ):
        """Build the filtered + sorted complaints query shared by ``list_complaints``
        and ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``Complaint.id`` as a deterministic tie-breaker
        so ``row_number``/offset position and prev/next neighbours are unambiguous
        when the primary sort column has equal values.
        """
        q = self.db.query(Complaint)
        # CONTACT-BRAND-SCOPE: only complaints with a product line the contact may see.
        for clause in complaint_in_scope_clauses(self.db):
            q = q.filter(clause)

        if query:
            like = f"%{query}%"
            q = q.filter(
                or_(
                    Complaint.complaint_number.ilike(like),
                    Complaint.delivery_order_number.ilike(like),
                    Complaint.customer_name.ilike(like),
                    Complaint.customer_type.ilike(like),
                    Complaint.customer_type_others.ilike(like),
                    Complaint.contact_person.ilike(like),
                    Complaint.contact_number.ilike(like),
                    Complaint.customer_address.ilike(like),
                    Complaint.product_code.ilike(like),
                    Complaint.product_type.ilike(like),
                    Complaint.complaint_type.ilike(like),
                    Complaint.defects_discovered.ilike(like),
                    Complaint.defect_description.ilike(like),
                    Complaint.salesperson.ilike(like),
                    Complaint.project_title.ilike(like),
                    Complaint.within_warranty.ilike(like),
                    Complaint.status.ilike(like),
                    Complaint.assigned_to.ilike(like),
                )
            )
        if assigned_to is not None and str(assigned_to).strip():
            if str(assigned_to).strip().lower() == "__unassigned__":
                q = q.filter(
                    (Complaint.assigned_to.is_(None)) | (Complaint.assigned_to == "")
                )
            else:
                q = q.filter(Complaint.assigned_to == assigned_to.strip())
        if status and str(status).strip():
            q = q.filter(Complaint.status == status.strip())
        contact_filter = (contact_id or "").strip()
        if contact_filter:
            q = q.filter(Complaint.contact_id == contact_filter)
        space_filter = (space_id or "").strip()
        if space_filter:
            q = q.filter(Complaint.space_id == space_filter)
        # Root cause / resolution: OR within each field (multi-select), AND across the
        # two. Also the query behind the "linked complaints" grid on a root cause /
        # resolution detail page, so both surfaces share one code path.
        rc_ids = [str(i).strip() for i in (root_cause_ids or []) if str(i).strip()]
        if rc_ids:
            q = q.filter(Complaint.root_cause_id.in_(rc_ids))
        res_ids = [str(i).strip() for i in (resolution_ids or []) if str(i).strip()]
        if res_ids:
            q = q.filter(Complaint.resolution_id.in_(res_ids))
        # A customer-scoped contact (PLAN-chatbot-customer-scope-29sep.md D5, AC-CS-47):
        # complaints carry the customer's NAME and no id, so the rows are those whose trimmed,
        # lower-cased name is one of the linked customers'. `None` = not scoped; an empty
        # list matches nothing.
        if customer_names is not None:
            wanted = [" ".join(str(n).split()).lower() for n in customer_names if str(n).strip()]
            q = q.filter(func.lower(func.btrim(Complaint.customer_name)).in_(wanted))

        sort_map = {
            "complaint_date": Complaint.complaint_date,
            "created_at": Complaint.created_at,
            "delivery_order_number": Complaint.delivery_order_number,
            "customer_name": Complaint.customer_name,
            "product_code": Complaint.product_code,
            "salesperson": Complaint.salesperson,
            "assigned_to": Complaint.assigned_to,
            "status": Complaint.status,
        }
        sort_column = sort_map.get(sort_field, Complaint.complaint_date)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc(), Complaint.id.asc())
        else:
            q = q.order_by(sort_column.asc(), Complaint.id.asc())
        return q

    # ------------------------------------------------------------------ #
    # Aggregation / analytics
    # ------------------------------------------------------------------ #
    ANALYTICS_GROUP_BY = ("status", "product", "month", "none")
    ANALYTICS_DATE_FIELDS = ("complaint_date", "resolved_at")

    def complaint_analytics(
        self,
        *,
        metric: str = "count",
        group_by: str = "none",
        status: Optional[str] = None,
        date_field: str = "complaint_date",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """Count complaints, optionally bucketed by status / product / month.

        Serves 'which product has the most complaints' (group_by=product, ranked
        desc) and 'how many complaints were resolved last month' (date_field=
        resolved_at + a date window - resolved rows have resolved_at set, so no
        status filter is needed). ``date_field`` selects which timestamp the
        date window and grouping-by-month apply to.
        """
        from datetime import datetime as _dt, date as _date
        from app.services.error_handler import AppException
        from fastapi import status as _http

        metric = (metric or "count").strip().lower()
        if metric != "count":
            raise AppException(
                status_code=_http.HTTP_422_UNPROCESSABLE_ENTITY,
                message=f"Unknown metric {metric!r}. Only 'count' is supported.",
                code="INVALID_METRIC",
            )
        group_by = (group_by or "none").strip().lower() or "none"
        if group_by not in self.ANALYTICS_GROUP_BY:
            raise AppException(
                status_code=_http.HTTP_422_UNPROCESSABLE_ENTITY,
                message=(
                    f"Unknown group_by {group_by!r}. Use one of: "
                    + ", ".join(self.ANALYTICS_GROUP_BY)
                ),
                code="INVALID_GROUP_BY",
            )
        date_field = (date_field or "complaint_date").strip().lower()
        if date_field not in self.ANALYTICS_DATE_FIELDS:
            raise AppException(
                status_code=_http.HTTP_422_UNPROCESSABLE_ENTITY,
                message=(
                    f"Unknown date_field {date_field!r}. Use one of: "
                    + ", ".join(self.ANALYTICS_DATE_FIELDS)
                ),
                code="INVALID_DATE_FIELD",
            )
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 50

        import re as _re

        def _parse(value: Optional[str], *, is_end: bool = False):
            s = (value or "").strip()
            if not s:
                return None
            # Bare 4-digit year → whole-year window bound.
            if _re.fullmatch(r"\d{4}", s):
                return _dt(int(s), 12, 31) if is_end else _dt(int(s), 1, 1)
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m"):
                try:
                    return _dt.strptime(s, fmt)
                except ValueError:
                    continue
            try:
                return _dt.fromisoformat(s.rstrip("Z"))
            except ValueError:
                return None

        dt_from = _parse(date_from)
        dt_to = _parse(date_to, is_end=True)

        col = getattr(Complaint, date_field)
        q = self.db.query(Complaint)
        for clause in complaint_in_scope_clauses(self.db):
            q = q.filter(clause)
        if status and status.strip():
            q = q.filter(func.lower(Complaint.status) == status.strip().lower())
        # resolved_at scoping implies the complaint was actually resolved.
        if date_field == "resolved_at":
            q = q.filter(Complaint.resolved_at.isnot(None))
        if dt_from is not None:
            q = q.filter(col >= dt_from)
        if dt_to is not None:
            # complaint_date is a DATE column, resolved_at a TIMESTAMP; SQLAlchemy
            # compares a datetime against either fine. `_parse` already resolves a
            # bare 'YYYY-MM' to the first of the month for the lower bound; callers
            # pass an explicit end date for inclusive upper windows.
            q = q.filter(col <= dt_to)

        rows = q.all()
        total = len(rows)

        def _month_key(value) -> str:
            if value is None:
                return "(no date)"
            if isinstance(value, (_dt, _date)):
                return value.strftime("%Y-%m")
            return str(value)[:7] or "(no date)"

        groups: list[dict] = []
        if group_by == "none":
            groups.append(
                {
                    "group_key": "all",
                    "group_label": "All complaints",
                    "metric": "count",
                    "value": total,
                }
            )
        else:
            buckets: dict[str, dict] = {}

            def _bump(key: str, label: str):
                b = buckets.setdefault(key, {"label": label, "count": 0})
                b["count"] += 1

            for c in rows:
                if group_by == "status":
                    key = (c.status or "(none)").strip().lower() or "(none)"
                    _bump(key, c.status or "(none)")
                elif group_by == "month":
                    val = getattr(c, date_field, None)
                    _bump(_month_key(val), _month_key(val))
                else:  # product - one complaint may cover several product codes
                    codes = self._complaint_product_codes(c)
                    allowed = in_scope_product_codes(self.db, codes)
                    if allowed is not None:
                        codes = [x for x in codes if x in allowed]
                    for code in codes or ["(unspecified)"]:
                        k = code.strip() or "(unspecified)"
                        _bump(k.lower(), k)
            for key, b in buckets.items():
                groups.append(
                    {
                        "group_key": key,
                        "group_label": b["label"],
                        "metric": "count",
                        "value": b["count"],
                    }
                )

        groups.sort(key=lambda g: g["value"], reverse=True)
        groups = groups[:limit]

        return {
            "metric": "count",
            "group_by": group_by,
            "date_field": date_field,
            "filters": {
                "status": status or None,
                "date_from": dt_from.isoformat() if dt_from else None,
                "date_to": dt_to.isoformat() if dt_to else None,
            },
            "groups": groups,
            "total": {
                "group_key": "__all__",
                "group_label": "All complaints",
                "metric": "count",
                "value": total,
            },
            "empty": total == 0,
        }

    def _complaint_product_codes(self, complaint) -> list[str]:
        """Distinct product codes for a complaint (product lines first, then the
        denormalised CSV product_code column)."""
        codes: list[str] = []
        seen: set[str] = set()
        for line in getattr(complaint, "product_lines", None) or []:
            code = (getattr(line, "product_code", None) or "").strip()
            if code and code.lower() not in seen:
                seen.add(code.lower())
                codes.append(code)
        if not codes:
            raw = (getattr(complaint, "product_code", None) or "").strip()
            for piece in raw.split(","):
                code = piece.strip()
                if code and code.lower() not in seen:
                    seen.add(code.lower())
                    codes.append(code)
        return codes

    def list_complaints(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        assigned_to: Optional[str] = None,
        status: Optional[str] = None,
        sort_field: str = "complaint_date",
        sort_dir: str = "asc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        viewer_user_id: Optional[str] = None,
        root_cause_ids: Optional[List[str]] = None,
        resolution_ids: Optional[List[str]] = None,
        customer_names: Optional[List[str]] = None,
    ):
        """List complaints. assigned_to filters by respond_user_id (assignee). status filters by complaint status.

        contact_id/space_id scope the result set to a single Respond.io contact/space (used by external callers).
        root_cause_ids/resolution_ids match ANY of the given ids per field (the list
        page's multi-selects, and the linked-complaints grid on a master-data detail page).
        """
        q = self._build_list_query(
            query=query,
            assigned_to=assigned_to,
            status=status,
            sort_field=sort_field,
            sort_dir=sort_dir,
            contact_id=contact_id,
            space_id=space_id,
            root_cause_ids=root_cause_ids,
            resolution_ids=resolution_ids,
            customer_names=customer_names,
        )

        from sqlalchemy.orm import joinedload
        q = q.options(
            joinedload(Complaint.root_cause),
            joinedload(Complaint.resolution),
        )

        total = q.count()
        offset = (page - 1) * limit
        complaints = q.offset(offset).limit(limit).all()
        complaint_ids = [str(c.id) for c in complaints]
        links_map = self.entity_attachment_service.list_links_for_entities(
            "complaint",
            complaint_ids,
        )
        from app.services.download_service import DownloadService
        print_map = DownloadService(self.db).count_map_for_user(
            viewer_user_id, "complaint", complaint_ids
        ) if viewer_user_id else {}

        # Batch the per-row enrichment that previously fired O(rows) queries:
        # view tokens, SLA assignee trackers, and user display names.
        view_url_map = self._batch_complaint_view_urls(complaint_ids)
        project_display_map = self._batch_project_display(complaints)
        sla_tracker_map = self._batch_latest_unresolved_sla_trackers(complaint_ids)
        wanted_user_ids: set[str] = set()
        for c in complaints:
            if getattr(c, "last_responded_by", None):
                wanted_user_ids.add(str(c.last_responded_by))
            if getattr(c, "assigned_to", None):
                wanted_user_ids.add(str(c.assigned_to))
        for t in sla_tracker_map.values():
            if getattr(t, "assigned_to_id", None):
                wanted_user_ids.add(str(t.assigned_to_id))
            if getattr(t, "assigned_to", None):
                wanted_user_ids.add(str(t.assigned_to))
            if getattr(t, "handled_by_id", None):
                wanted_user_ids.add(str(t.handled_by_id))
        user_name_map = self._batch_user_display_names(wanted_user_ids)

        def _assigned_name(complaint) -> Optional[str]:
            tracker = sla_tracker_map.get(str(complaint.id))
            if tracker is not None:
                if getattr(tracker, "assigned_to_id", None):
                    name = user_name_map.get(str(tracker.assigned_to_id))
                    if name:
                        return name
                if getattr(tracker, "assigned_to", None):
                    return user_name_map.get(str(tracker.assigned_to)) or tracker.assigned_to
            if getattr(complaint, "assigned_to", None):
                return user_name_map.get(str(complaint.assigned_to))
            return None

        def _handled_name(complaint) -> Optional[str]:
            tracker = sla_tracker_map.get(str(complaint.id))
            if tracker is not None and getattr(tracker, "handled_by_id", None):
                return user_name_map.get(str(tracker.handled_by_id))
            return None

        complaint_data = [
            self._serialize_complaint(
                complaint,
                links_override=links_map.get(str(complaint.id), []),
                print_count=print_map.get(str(complaint.id), 0),
                view_url_override=view_url_map.get(str(complaint.id)),
                project_display_override=(
                    project_display_map.get(str(complaint.project_id))
                    if getattr(complaint, "project_id", None)
                    else None
                ),
                assigned_to_name_override=_assigned_name(complaint),
                handled_by_name_override=_handled_name(complaint),
                # Banners (handling-lock / rejection) render on the DETAIL page only,
                # so the list path skips the per-row wa.me phone lookups (N+1). Phones
                # resolve in get_complaint / get_complaint_for_response instead.
                handled_by_wa_phone_override=None,
                rejected_by_wa_phone_override=None,
                last_responded_by_name_override=(
                    user_name_map.get(str(complaint.last_responded_by))
                    if getattr(complaint, "last_responded_by", None)
                    else None
                ),
            )
            for complaint in complaints
        ]
        
        return {
            "data": complaint_data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_complaint(self, complaint_id: str):
        """Get a complaint by ID."""
        complaint = (
            self.db.query(Complaint)
            .filter(Complaint.id == complaint_id)
            .first()
        )
        if not complaint:
            raise handle_not_found("Complaint", complaint_id)
        return complaint

    def get_complaint_with_attachments(self, complaint_id: str) -> dict:
        """Get a complaint with attachments from complaint_attachments table."""
        complaint = self.get_complaint(complaint_id)
        return self._serialize_complaint(complaint)

    def get_complaint_for_response(
        self,
        complaint_id: str,
        *,
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ) -> dict:
        """Get serialized complaint, optionally enforcing contact_id/space_id scope (external callers)."""
        complaint = self.get_complaint(complaint_id)
        c = (contact_id or "").strip()
        s = (space_id or "").strip()
        if c and (getattr(complaint, "contact_id", None) or "") != c:
            raise handle_not_found("Complaint", complaint_id)
        if s and (getattr(complaint, "space_id", None) or "") != s:
            raise handle_not_found("Complaint", complaint_id)
        return self._serialize_complaint(complaint)

    def get_or_create_view_token(self, complaint_id: str) -> str:
        """Get or create a reusable view token for this complaint. Returns the token string.

        The token row is persisted via an isolated session so that calling this from a
        serializer (read or mutation path) never piggybacks the caller's pending writes
        into a premature commit.
        """
        self.get_complaint(complaint_id)  # ensure exists
        row = (
            self.db.query(ViewToken)
            .filter(
                ViewToken.entity_type == "complaint",
                ViewToken.entity_id == complaint_id,
            )
            .first()
        )
        if row:
            return row.token
        from app.database import SessionLocal

        token_value = secrets.token_urlsafe(32)
        isolated = SessionLocal()
        try:
            existing = (
                isolated.query(ViewToken)
                .filter(
                    ViewToken.entity_type == "complaint",
                    ViewToken.entity_id == complaint_id,
                )
                .first()
            )
            if existing:
                return existing.token
            isolated.add(
                ViewToken(
                    entity_type="complaint",
                    entity_id=complaint_id,
                    token=token_value,
                )
            )
            isolated.commit()
        except Exception:
            isolated.rollback()
            raise
        finally:
            isolated.close()
        return token_value

    def _company_for_complaint(self, complaint_id: str) -> str:
        """AC-E4: a complaint routes to its contact's company, else the default.

        ``complaints`` has no company column of its own, so the contact is the only
        signal - which is exactly why the contact is the designated source rather
        than the ambient request scope.
        """
        from app.models.complaints import Complaint
        from app.services.company_routing_service import company_for_contact

        row = (
            self.db.query(Complaint.contact_id)
            .filter(Complaint.id == str(complaint_id))
            .first()
        )
        return company_for_contact(self.db, contact_id=str(row[0]) if row and row[0] else None)

    def _get_complaint_handler_user_ids(self, *, company_id: str) -> List[str]:
        """
        Members of the Tier 1 Complaint team under Access Agent code `complaint`.

        Uses team set code `complaint` + tier 1 so multiple tier-1 rows on the same agent
        (e.g. complaint + customer_service) do not trigger a conflict and the wrong team is not chosen.
        """
        from app.services.user_service import AccessAgentService
        from app.models.access import TeamMember

        log = logging.getLogger(__name__)
        agent_svc = AccessAgentService(self.db)
        agent_id = agent_svc.get_agent_id_by_code("complaint")
        if not agent_id:
            log.debug("No access agent found for code=complaint")
            return []

        team_id = agent_svc.get_team_id_by_tier(
            agent_id, 1, team_set_code="complaint", company_id=company_id
        )
        if not team_id:
            team_id = agent_svc.get_team_id_by_code(
                agent_id, "complaint", company_id=company_id
            )
        if not team_id:
            try:
                team_id = agent_svc.get_team_id_by_tier(agent_id, 1, company_id=company_id)
            except HTTPException:
                log.warning(
                    "Tier 1 for agent 'complaint' is ambiguous (multiple team sets). "
                    "Use team set code 'complaint' on Tier 1 in Team Assignments."
                )
                return []
        if not team_id:
            return []

        rows = self.db.query(TeamMember.user_id).filter(TeamMember.team_id == team_id).all()
        return [str(r[0]) for r in rows if r and r[0]]

    def _complaint_status_link_part(self, complaint, complaint_id: str) -> str:
        """Trailing link block for customer status messages.

        Prefers the interactive submission portal link (minted on the fly) so the
        contact can act/resubmit; falls back to the read-only public view link.
        Empty when public view links are disabled or nothing can be built.

        Returned on its own line (``\\n\\n{url}``) and ALWAYS appended LAST in the
        message so no sentence punctuation follows the URL - WhatsApp's link
        auto-detection would otherwise absorb a trailing ``:`` into the link,
        producing ``…/{uuid}:`` and a 500 (invalid UUID) when the contact taps it.
        """
        if not self._complaint_public_view_links_enabled():
            return ""
        from app.services.portal_service import PortalService

        portal = PortalService(self.db).submission_link(
            getattr(complaint, "contact_id", None),
            "complaint",
            complaint_id,
        )
        if portal:
            return f"\n\n{portal}"
        view_url = (self._build_complaint_view_url(complaint_id) or "").strip()
        return f"\n\n{view_url}" if view_url else ""

    def _complaint_portal_or_view_url(self, complaint, complaint_id: str) -> str:
        """Bare interactive portal link for the complaint (contact can act /
        resubmit), falling back to the read-only public view URL. Same target as
        the inline body link - used for the ``portal_url`` template variable so
        the 'Portal URL' button actually opens the portal, not the view token.
        """
        if self._complaint_public_view_links_enabled():
            from app.services.portal_service import PortalService

            portal = PortalService(self.db).submission_link(
                getattr(complaint, "contact_id", None),
                "complaint",
                complaint_id,
            )
            if portal:
                return portal.strip()
        return (self._build_complaint_view_url(complaint_id) or "").strip()

    def _complaint_view_base_url(self, base_url_override: Optional[str] = None) -> str:
        """Resolve the frontend base URL for view links (override → settings → SystemSetting).

        Factored out of _build_complaint_view_url so the list path can resolve it
        ONCE for the whole page instead of once per row.
        """
        from app.models.user import SystemSetting

        base_url = (base_url_override or "").strip().rstrip("/")
        if not base_url:
            base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        if not base_url:
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        return base_url

    @staticmethod
    def _format_complaint_view_url(base_url: str, token: str) -> str:
        return f"{base_url}/view/complaint?token={token}" if base_url else f"/view/complaint?token={token}"

    def _build_complaint_view_url(self, complaint_id: str, base_url_override: Optional[str] = None) -> str:
        """Build shareable (no-auth) frontend link for a complaint using view token."""
        view_token = self.get_or_create_view_token(complaint_id)
        base_url = self._complaint_view_base_url(base_url_override)
        return self._format_complaint_view_url(base_url, view_token)

    def _build_complaint_internal_url(self, complaint_id: str, base_url_override: Optional[str] = None) -> str:
        """In-system (authenticated) complaint detail-page link for STAFF emails.

        Staff team notifications must link to the internal page, NOT the public
        tokenized /view URL (which is being discontinued). The (protected) layout's
        deep-link-after-login carries the recipient back here once authenticated.
        """
        base_url = self._complaint_view_base_url(base_url_override)
        path = f"/complaint-management/complaints/{complaint_id}"
        return f"{base_url}{path}" if base_url else path

    def _batch_project_display(self, complaints) -> dict:
        """`{project_id: (project_code, title)}` for the linked projects on one page.

        One query for the page instead of one per linked complaint. Returns `{}` when nothing
        on the page carries a link, which is the common case.
        """
        project_ids = {
            str(c.project_id) for c in complaints if getattr(c, "project_id", None)
        }
        if not project_ids:
            return {}
        from app.models.projects import Project

        return {
            str(row[0]): (row[1], row[2])
            for row in self.db.query(Project.id, Project.project_code, Project.title)
            .filter(Project.id.in_(list(project_ids)))
            .all()
        }

    def _batch_complaint_view_urls(self, complaint_ids: List[str]) -> dict:
        """Resolve view URLs for many complaints with O(1) queries instead of O(rows).

        One SELECT for existing tokens, one isolated session to mint any missing
        ones in bulk, and the base URL resolved once. Mirrors the per-row
        get_or_create_view_token semantics (isolated session so token writes never
        piggyback the caller's pending writes).
        """
        ids = [str(i) for i in complaint_ids if i]
        if not ids:
            return {}
        existing = (
            self.db.query(ViewToken)
            .filter(ViewToken.entity_type == "complaint", ViewToken.entity_id.in_(ids))
            .all()
        )
        token_map: dict = {str(r.entity_id): r.token for r in existing}
        missing = [cid for cid in ids if cid not in token_map]
        if missing:
            from app.database import SessionLocal
            isolated = SessionLocal()
            try:
                for cid in missing:
                    ex = (
                        isolated.query(ViewToken)
                        .filter(ViewToken.entity_type == "complaint", ViewToken.entity_id == cid)
                        .first()
                    )
                    if ex:
                        token_map[cid] = ex.token
                        continue
                    tok = secrets.token_urlsafe(32)
                    isolated.add(ViewToken(entity_type="complaint", entity_id=cid, token=tok))
                    token_map[cid] = tok
                isolated.commit()
            except Exception:
                isolated.rollback()
                # Leave un-minted ids out of the map; the serializer falls back
                # to a per-row build for those rather than failing the page.
            finally:
                isolated.close()
        base_url = self._complaint_view_base_url()
        return {cid: self._format_complaint_view_url(base_url, tok) for cid, tok in token_map.items()}

    def _batch_user_display_names(self, user_ids: Iterable[str]) -> dict:
        """Map {user id or respond_user_id -> display name} for many ids in one query."""
        ids = {str(u).strip() for u in user_ids if u and str(u).strip()}
        if not ids:
            return {}
        from app.models.user import User
        users = (
            self.db.query(User)
            .filter(or_(User.id.in_(ids), User.respond_user_id.in_(ids)))
            .all()
        )
        out: dict = {}
        for u in users:
            name = u.name or u.email or None
            if not name:
                continue
            if getattr(u, "id", None):
                out[str(u.id)] = name
            if getattr(u, "respond_user_id", None):
                out[str(u.respond_user_id)] = name
        return out

    def _batch_latest_unresolved_sla_trackers(self, complaint_ids: List[str]) -> dict:
        """Map {complaint_id -> latest unresolved ConversationSLATracking} in one query."""
        ids = [str(i) for i in complaint_ids if i]
        if not ids:
            return {}
        rows = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == "complaint",
                ConversationSLATracking.source_entity_id.in_(ids),
                *open_tracker_scope(),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .all()
        )
        out: dict = {}
        for t in rows:
            cid = str(t.source_entity_id)
            if cid not in out:  # first seen = latest (desc order)
                out[cid] = t
        return out

    def notify_team_complaint_external_created(
        self,
        complaint_id: str,
        base_url_override: Optional[str] = None,
        sync_email: bool = False,
        is_resubmission: bool = False,
    ) -> None:
        """Notify tier 1 team under complaint agent (fallback project_sales) when complaint is created externally (in-app + one email to all). Email is enqueued by default so API returns quickly.

        ``is_resubmission`` marks a rejected complaint being resubmitted: the team is
        notified again under a distinct ``event_type`` keyed by the rejection cycle, so the
        idempotency constraint allows one notification per resubmission instead of swallowing it.
        """
        from datetime import datetime
        from app.models.user import User
        from app.models.complaints import Complaint
        from app.models.notification import Notification, NotificationDelivery
        from app.services.notification_service import NotificationService

        logger = logging.getLogger(__name__)
        user_ids = self._get_complaint_handler_user_ids(
            company_id=self._company_for_complaint(complaint_id)
        )
        if not user_ids:
            logger.warning(
                "No team members found for agent 'complaint' Tier 1 under team set code 'complaint'. "
                "In Team Assignments for the Complaint agent, set code 'complaint' on Tier 1 (Complaint team)."
            )
            return
        users = self.db.query(User).filter(User.id.in_(user_ids)).all()
        emails = [u.email for u in users if getattr(u, "email", None) and str(u.email).strip()]
        if not emails:
            logger.warning(
                "Complaint handler team members have no email addresses; skipping email delivery row."
            )
        # Event type keys notification idempotency. A resubmit is a distinct logical event:
        # key it by the rejection cycle (rejected_at) so each resubmission notifies exactly once.
        # The status machine (rejected -> submitted) guards against double-fire within a cycle.
        if is_resubmission:
            row = self.db.query(Complaint).filter(Complaint.id == complaint_id).first()
            rejected_at = getattr(row, "rejected_at", None)
            cycle_key = rejected_at.isoformat() if rejected_at else "resubmit"
            event_type = f"external_resubmitted:{cycle_key}"
            title = "Complaint resubmitted"
            sentence = "A previously rejected complaint has been resubmitted and requires your review."
        else:
            event_type = "external_created"
            title = "New Complaint created"
            sentence = "A new complaint has been created and requires your review."
        view_url = self._build_complaint_view_url(complaint_id, base_url_override=base_url_override)
        # #1349: the layout builds the branded mail from CONTEXT, never a hand-built
        # string. The template's own `body_text` reproduces this route's old plain
        # body verbatim, so nothing downstream that reads `notification.body` sees a
        # change in shape.
        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "complaint_created",
            {"title": title, "sentence": sentence, "view_url": view_url},
        )
        body_plain = rendered["body_text"]
        body_html = rendered["body_html"]
        notif_svc = NotificationService(self.db)
        first_uid = user_ids[0]
        existing_notif = (
            self.db.query(Notification)
            .filter(
                Notification.user_id == first_uid,
                Notification.source_entity_type == "complaint",
                Notification.source_entity_id == complaint_id,
                Notification.event_type == event_type,
            )
            .first()
        )
        if emails and existing_notif is None:
            notification = Notification(
                user_id=first_uid,
                type="complaint_notification",
                title=title,
                body=body_plain,
                data={"recipient_emails": emails, "single_email_to_all": True, "body_html": body_html},
                source_entity_type="complaint",
                source_entity_id=complaint_id,
                event_type=event_type,
            )
            self.db.add(notification)
            self.db.flush()
            self.db.add(
                NotificationDelivery(
                    notification_id=notification.id,
                    channel="in_app",
                    status="sent",
                    sent_at=datetime.utcnow(),
                )
            )
            self.db.add(NotificationDelivery(notification_id=notification.id, channel="email", status="pending"))
            self.db.commit()
            self.db.refresh(notification)
            try:
                if sync_email:
                    from app.tasks import notification_tasks
                    notification_tasks.send_notification_deliveries(str(notification.id))
                else:
                    from app.services.queue_service import enqueue_job
                    from app.tasks import notification_tasks
                    enqueue_job(
                        notification_tasks.send_notification_deliveries,
                        str(notification.id),
                        queue_name="notifications",
                    )
            except Exception as e:
                logger.warning("Failed to send/enqueue notification deliveries: %s", e)
        for uid in user_ids:
            if uid == first_uid and emails:
                continue
            try:
                notif_svc.create_in_app_only(
                    user_id=uid,
                    type="complaint_notification",
                    title=title,
                    body=body_plain,
                    source_entity_type="complaint",
                    source_entity_id=complaint_id,
                    event_type=event_type,
                )
            except Exception as e:
                logger.warning("Failed to create in-app notification for user %s: %s", uid, e)

    # ----- Replacement-DO delivery notifications (complaint auto-fulfilment) -----

    def _complaint_do_delivered_notify_tiers(self) -> tuple[int, ...]:
        """Configured Complaint-team tiers for the DO-delivered notice.

        Resolution order: DB (``system_settings.complaint_do_delivered_notify_tiers``,
        admin-editable in Settings → Complaints) → env COMPLAINT_DO_DELIVERED_NOTIFY_TIERS
        → default Tier 1 + Tier 2."""
        from app.config import settings
        from app.models.user import SystemSetting

        raw = None
        try:
            row = self.db.query(SystemSetting).first()
            if row is not None:
                raw = getattr(row, "complaint_do_delivered_notify_tiers", None)
        except Exception:
            raw = None
        if not (raw and str(raw).strip()):
            raw = getattr(settings, "complaint_do_delivered_notify_tiers", "1,2")
        raw = raw or "1,2"
        tiers: list[int] = []
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                t = int(part)
            except ValueError:
                continue
            if 1 <= t <= 3 and t not in tiers:
                tiers.append(t)
        return tuple(tiers) or (1, 2)

    def _get_complaint_team_user_ids_tiers(
        self, tiers: tuple[int, ...] = (1, 2), *, company_id: str
    ) -> List[str]:
        """Union of members across the given tiers of agent ``complaint`` (set ``complaint``).

        Used for replacement-DO delivery notifications which fan out to both Tier 1
        and Tier 2 of the Complaint team (single source of truth = team membership).
        """
        from app.services.user_service import AccessAgentService
        from app.models.access import TeamMember

        agent_svc = AccessAgentService(self.db)
        agent_id = agent_svc.get_agent_id_by_code("complaint")
        if not agent_id:
            return []
        team_ids: list[str] = []
        for tier in tiers:
            try:
                tid = agent_svc.get_team_id_by_tier(
                    agent_id, tier, team_set_code="complaint", company_id=company_id
                )
            except HTTPException:
                tid = None
            if tid:
                team_ids.append(tid)
        if not team_ids:
            return []
        rows = (
            self.db.query(TeamMember.user_id)
            .filter(TeamMember.team_id.in_(team_ids))
            .distinct()
            .all()
        )
        return [str(r[0]) for r in rows if r and r[0]]

    @staticmethod
    def _do_item_lines(items: Optional[Iterable[dict]]) -> list[str]:
        """['CODE x QTY', ...] - one entry per delivered line (skips blank codes)."""
        lines: list[str] = []
        for it in items or []:
            code = str((it or {}).get("product_code") or "").strip()
            if not code:
                continue
            qty = (it or {}).get("qty")
            qty_str = "" if qty in (None, "") else f" x {qty}"
            lines.append(f"{code}{qty_str}")
        return lines

    @classmethod
    def _format_do_items(cls, items: Optional[Iterable[dict]]) -> str:
        """Plain-text delivery-notice item block - an 'Items delivered:' header with
        one item per line ('- CODE x QTY'). Empty string when there are no items."""
        lines = cls._do_item_lines(items)
        if not lines:
            return ""
        return "Items delivered:\n" + "\n".join(f"- {line}" for line in lines)

    @classmethod
    def _format_do_items_html(cls, items: Optional[Iterable[dict]]) -> str:
        """HTML delivery-notice item block (<ul> list) for email bodies. '' when none."""
        lines = cls._do_item_lines(items)
        if not lines:
            return ""
        lis = "".join(f"<li>{line}</li>" for line in lines)
        return f"<p>Items delivered:</p>\n<ul>{lis}</ul>"

    def notify_team_do_delivered(
        self,
        complaint_id: str,
        *,
        complaint_number: str,
        order_number: str,
        items: Optional[Iterable[dict]] = None,
    ) -> None:
        """Notify the Complaint team (Tier 1 + Tier 2) that a replacement DO was
        delivered - in-app + a single email to all. Content is a delivery FACT;
        it never announces "fulfilled". Idempotency is enforced upstream by the
        ``complaint_fulfilment_orders.delivery_notified_at`` stamp, but the
        notification ``event_type`` is also keyed by the DO so two DOs delivering
        for one complaint each notify once.
        """
        from datetime import datetime
        from app.models.user import User
        from app.models.notification import Notification, NotificationDelivery

        logger = logging.getLogger(__name__)
        user_ids = self._get_complaint_team_user_ids_tiers(
            self._complaint_do_delivered_notify_tiers(),
            company_id=self._company_for_complaint(complaint_id),
        )
        if not user_ids:
            logger.warning(
                "DO-delivered notify: no Complaint team members (Tier 1/2, set 'complaint') for complaint %s.",
                complaint_id,
            )
            return
        users = self.db.query(User).filter(User.id.in_(user_ids)).all()

        items_block = self._format_do_items(items)
        item_lines = self._do_item_lines(items)
        headline = (
            f"Replacement delivery order {order_number} for complaint "
            f"{complaint_number} has been delivered."
        )
        title = "Replacement delivery order delivered"
        # Staff team email -> internal detail page, never the public /view token URL.
        view_url = self._build_complaint_internal_url(complaint_id)
        # #1349: rendered through the layout. The template's `body_text` reproduces
        # this route's old plain body verbatim (the "\n- CODE x QTY" pins on
        # `notification.body` in tests/test_complaint_do_notify.py hold unchanged).
        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "complaint_do_delivered",
            {
                "title": title,
                "headline": headline,
                "items_block": items_block,
                "item_lines": item_lines,
                "view_url": view_url,
            },
        )
        body_plain = rendered["body_text"]
        body_html = rendered["body_html"]
        event_type = f"do_delivered:{order_number}"

        # One INDIVIDUAL email per team member (each as the To recipient) + in-app.
        # Previously this was a single email with To=first member, CC=the rest - but
        # CC recipients were unreliably delivered (mail server/client filtering), so
        # only the To member actually got it. Direct per-recipient emails guarantee
        # every Tier 1/2 member receives their own copy.
        from app.services.queue_service import enqueue_job
        from app.tasks import notification_tasks

        for u in users:
            uid = str(u.id)
            existing_notif = (
                self.db.query(Notification)
                .filter(
                    Notification.user_id == uid,
                    Notification.source_entity_type == "complaint",
                    Notification.source_entity_id == complaint_id,
                    Notification.event_type == event_type,
                )
                .first()
            )
            if existing_notif is not None:
                continue
            has_email = bool(getattr(u, "email", None) and str(u.email).strip())
            notification = Notification(
                user_id=uid,
                type="complaint_notification",
                title=title,
                body=body_plain,
                data={"body_html": body_html},
                source_entity_type="complaint",
                source_entity_id=complaint_id,
                event_type=event_type,
            )
            self.db.add(notification)
            self.db.flush()
            self.db.add(
                NotificationDelivery(
                    notification_id=notification.id,
                    channel="in_app",
                    status="sent",
                    sent_at=datetime.utcnow(),
                )
            )
            if has_email:
                self.db.add(
                    NotificationDelivery(
                        notification_id=notification.id, channel="email", status="pending"
                    )
                )
            self.db.commit()
            self.db.refresh(notification)
            if has_email:
                try:
                    enqueue_job(
                        notification_tasks.send_notification_deliveries,
                        str(notification.id),
                        queue_name="notifications",
                    )
                except Exception as e:
                    logger.warning(
                        "DO-delivered notify: failed to enqueue email for user %s: %s", uid, e
                    )

    def notify_customer_do_delivered(
        self,
        complaint_id: str,
        *,
        complaint_number: str,
        order_number: str,
        items: Optional[Iterable[dict]] = None,
    ) -> bool:
        """Send the customer a Respond.io/WhatsApp delivery FACT for a replacement DO.

        Enqueues through the ``respond_io`` queue (window-aware send + integration_log
        on success AND failure). Returns False and skips gracefully when the complaint
        has no linked Respond.io contact. Never announces "fulfilled".
        """
        complaint = self.get_complaint(complaint_id)
        identifier = self._identifier_from_respond_inbox_url(
            getattr(complaint, "respond_inbox_url", None)
        )
        if not identifier:
            return False

        items_block = self._format_do_items(items)
        link_part = self._complaint_status_link_part(complaint, complaint_id)
        headline = (
            f"Your replacement delivery order {order_number} for complaint "
            f"{complaint_number} has been delivered."
        )
        display_message = (
            headline + (f"\n\n{items_block}" if items_block else "") + link_part
        )
        update_text = (
            f"Replacement delivery order {order_number} has been delivered."
        )
        if items_block:
            update_text += f"\n\n{items_block}"
        extra_vars = {
            "update": update_text.strip(),
            "portal_url": self._complaint_portal_or_view_url(complaint, complaint_id),
            "view_url": (self._build_complaint_view_url(complaint_id) or "").strip(),
        }
        # Automated system send - no acting Respond.io user; fall back to the
        # complaint's assignee (used only for chat-thread assignee resolution).
        respond_user_id = (getattr(complaint, "assigned_to", None) or "").strip()
        self._enqueue_respond_message_for_complaint(
            complaint_id=complaint_id,
            identifier=identifier,
            display_message=display_message,
            respond_user_id=respond_user_id,
            crm_sender_user_id=None,
            space_id=getattr(complaint, "space_id", None),
            extra_context_vars=extra_vars,
        )
        return True

    def get_complaint_summary_by_token(self, token_value: str) -> dict:
        """Return read-only complaint summary for the given view token. No auth required."""
        view_token = (
            self.db.query(ViewToken)
            .filter(ViewToken.token == token_value, ViewToken.entity_type == "complaint")
            .first()
        )
        if not view_token or not view_token.entity_id:
            raise handle_not_found("View link", "(invalid token)")
        complaint = self.get_complaint(str(view_token.entity_id))
        # Build public summary (read-only; no internal IDs like contact_id/space_id if desired)
        links = self.entity_attachment_service.list_links("complaint", str(complaint.id))
        link_attachments = [
            self.entity_attachment_service.serialize_link(
                link,
                entity_key="complaint_id",
                link_type="complaint_attachment",
            )
            for link in links
        ]
        return {
            "entity_type": "complaint",
            "entity_id": complaint.id,
            "delivery_order_number": getattr(complaint, "delivery_order_number", None),
            "complaint_date": getattr(complaint, "complaint_date", None),
            "customer_type": getattr(complaint, "customer_type", None),
            "customer_type_others": getattr(complaint, "customer_type_others", None),
            "within_warranty": getattr(complaint, "within_warranty", None),
            "product_type": getattr(complaint, "product_type", None),
            "defects_discovered": getattr(complaint, "defects_discovered", None),
            "complaint_type": getattr(complaint, "complaint_type", None),
            "defect_description": getattr(complaint, "defect_description", None),
            "product_code": getattr(complaint, "product_code", None),
            "quantity": getattr(complaint, "quantity", None),
            "product_lines": [
                {
                    "product_code": ln.product_code,
                    "quantity": ln.quantity,
                    "product_type": ln.product_type,
                }
                for ln in (getattr(complaint, "product_lines", None) or [])
            ],
            "salesperson": getattr(complaint, "salesperson", None),
            "customer_name": getattr(complaint, "customer_name", None),
            "contact_person": getattr(complaint, "contact_person", None),
            "contact_number": getattr(complaint, "contact_number", None),
            "customer_address": getattr(complaint, "customer_address", None),
            "project_title": getattr(complaint, "project_title", None),
            "technical_team_response": getattr(complaint, "technical_team_response", None),
            "status": getattr(complaint, "status", None),
            "last_responded_at": getattr(complaint, "last_responded_at", None),
            "created_at": getattr(complaint, "created_at", None),
            "attachments": link_attachments,
        }

    @staticmethod
    def _split_csv(value: Optional[str]) -> list[str]:
        return [p.strip() for p in (value or "").split(",") if p.strip()]

    def _apply_explicit_product_lines(self, complaint, lines) -> None:
        """Rebuild the product_lines relationship from explicit payload lines and
        re-derive the legacy product_code / product_type / quantity CSV columns
        (index-aligned) for backward compat (n8n payload, public view)."""
        from app.models.complaints import ComplaintProductLine

        complaint.product_lines = []  # delete-orphan removes prior rows
        self.db.flush()

        def _field(line, name):
            return line.get(name) if isinstance(line, dict) else getattr(line, name, None)

        codes: list[str] = []
        types: list[str] = []
        qtys: list[str] = []
        for idx, line in enumerate(lines or []):
            code = (str(_field(line, "product_code") or "")).strip()
            if not code:
                continue
            qty = _field(line, "quantity")
            qty = (str(qty).strip() or None) if qty is not None else None
            ptype = _field(line, "product_type")
            ptype = (str(ptype).strip() or None) if ptype is not None else None
            complaint.product_lines.append(
                ComplaintProductLine(
                    product_code=code,
                    quantity=qty,
                    product_type=ptype,
                    sort_order=idx,
                )
            )
            codes.append(code)
            types.append(ptype or "")
            qtys.append(qty or "")

        complaint.product_code = ", ".join(codes) if codes else None
        complaint.product_type = ", ".join(types) if any(types) else None
        complaint.quantity = ", ".join(qtys) if any(qtys) else None

    def _populate_lines_from_legacy_csv(self, complaint) -> None:
        """When a caller sets product_code (CSV) without explicit product_lines,
        keep the line-item table in sync. quantity / product_type are paired by
        comma POSITION (empties preserved), so "A, B, C" + qty "5, , 2" maps
        A=5, B=None, C=2."""
        from app.models.complaints import ComplaintProductLine

        # Split by position (do NOT drop empties) so index alignment holds.
        code_parts = [p.strip() for p in (getattr(complaint, "product_code", None) or "").split(",")]
        type_parts = [p.strip() for p in (getattr(complaint, "product_type", None) or "").split(",")]
        qty_parts = [p.strip() for p in (getattr(complaint, "quantity", None) or "").split(",")]

        complaint.product_lines = []
        self.db.flush()
        order = 0
        for idx, code in enumerate(code_parts):
            if not code:
                continue
            complaint.product_lines.append(
                ComplaintProductLine(
                    product_code=code,
                    quantity=(qty_parts[idx] or None) if idx < len(qty_parts) else None,
                    product_type=(type_parts[idx] or None) if idx < len(type_parts) else None,
                    sort_order=order,
                )
            )
            order += 1

    def create_complaint(self, complaint_data: ComplaintCreate):
        """Create a new complaint with attachments (generic entity_attachment_links)."""
        complaint_dict = complaint_data.model_dump(
            exclude={"attachments", "product_lines", "assigned_to_name", "last_responded_by_name"}
        )
        if complaint_dict.get("technical_team_response"):
            complaint_dict["technical_team_response"] = self._normalize_complaint_reply_body_for_storage(
                str(complaint_dict["technical_team_response"])
            )
        contact_id = complaint_dict.get("contact_id")
        space_id = complaint_dict.get("space_id")
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            complaint_dict["respond_inbox_url"] = respond_inbox_url
        complaint = Complaint(**complaint_dict)
        self.db.add(complaint)
        self.db.flush()

        # Per-product line items (source of truth). If the caller sent explicit
        # lines use them; otherwise derive from the product_code CSV so the table
        # stays populated for legacy/AI-extract callers.
        if complaint_data.product_lines is not None:
            self._apply_explicit_product_lines(complaint, complaint_data.product_lines)
        elif getattr(complaint, "product_code", None):
            self._populate_lines_from_legacy_csv(complaint)
        self.db.flush()

        if complaint_data.attachments:
            for att_data in complaint_data.attachments:
                self.entity_attachment_service.create_attachment_and_link(
                    entity_type="complaint",
                    entity_id=str(complaint.id),
                    file_url=att_data.file_url or "",
                    file_name=att_data.file_name or "document",
                    file_size_bytes=(
                        int(att_data.file_size_bytes)
                        if att_data.file_size_bytes is not None
                        else None
                    ),
                    attachment_type_code="complaint_document",
                )

        self.db.commit()
        self.db.refresh(complaint)
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "complaint",
                str(complaint.id),
                "submit",
                contact_id=getattr(complaint, "contact_id", None),
                actor_user_id=None,
            )
        except Exception as e:
            logging.getLogger(__name__).warning("Form SLA emit 'submit' failed for complaint %s: %s", complaint.id, e)
        return complaint

    # States in the response stage of the lifecycle. Saving/sending a technical
    # response only moves the status forward (updated/responded) FROM these.
    # Once a decision/terminal state is reached (approved, rejected,
    # processed_by_cs, closed), editing or re-sending the response must NOT
    # regress the status - the lifecycle is:
    #   submitted -> responded -> approved | rejected
    #   approved  -> processed_by_cs | closed
    #
    # The same tuple now also GATES the response write (UAC O1): outside these
    # states the technical team response cannot be rewritten at all, not merely
    # written without a status move. It lives in response_gate so the gate and
    # the status flip can never disagree about what the response stage is.
    _RESPONSE_STAGE_STATUSES: tuple[str, ...] = ALLOWED_RESPONSE_STATUSES["complaint"]

    def update_complaint(self, complaint_id: str, complaint_data: ComplaintUpdate):
        """Update a complaint."""
        complaint = self.get_complaint(complaint_id)

        update_data = complaint_data.model_dump(exclude_unset=True)
        explicit_lines = update_data.pop("product_lines", None)
        contact_id = update_data.get("contact_id") if "contact_id" in update_data else complaint.contact_id
        space_id = update_data.get("space_id") if "space_id" in update_data else complaint.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            update_data["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            update_data["respond_inbox_url"] = None

        if "technical_team_response" in update_data and update_data["technical_team_response"] is not None:
            update_data["technical_team_response"] = self._normalize_complaint_reply_body_for_storage(
                str(update_data["technical_team_response"])
            )

        # Response gate (UAC O1): the technical team response is stage output, so
        # it may only be REWRITTEN while the complaint is still waiting for one.
        # Every other field stays editable at any status, and a save that posts
        # the response back unchanged (the edit form posts the whole complaint)
        # is not a response write. Compared post-normalization so a whitespace or
        # preamble difference alone does not read as an edit.
        #
        # BOTH sides go through the normalizer, never just the incoming one. The
        # legacy-preamble strip only ever ran on WRITE, so every row stored before
        # it landed still carries "There has been an update regarding your
        # complaint ...: " - while the detail page and the edit form render (and
        # therefore post back) the stripped body. Comparing bare-incoming against
        # raw-stored read as a rewrite and 422'd an office save that only touched
        # the customer's address.
        if "technical_team_response" in update_data and response_text_changed(
            self._normalize_complaint_reply_body_for_storage(
                getattr(complaint, "technical_team_response", None)
            ),
            update_data.get("technical_team_response"),
        ):
            assert_response_write_allowed("complaint", complaint.status)

        # NOTE: a plain save must NOT auto-transition the status. Editing fields
        # (incl. technical_team_response) keeps whatever state the complaint is
        # in; status only changes when the caller explicitly sends `status`, or
        # via the dedicated reply action (update_complaint_and_reply →
        # 'responded'). The old auto-'updated' flip was unwanted.

        for key, value in update_data.items():
            setattr(complaint, key, value)

        # Per-product lines: explicit payload wins; else if product_code CSV was
        # changed, re-derive the lines so the table tracks it.
        if explicit_lines is not None:
            self._apply_explicit_product_lines(complaint, explicit_lines)
        elif "product_code" in update_data:
            self._populate_lines_from_legacy_csv(complaint)

        self.db.commit()
        self.db.refresh(complaint)
        return complaint

    def _identifier_from_respond_inbox_url(self, respond_inbox_url: Optional[str]) -> Optional[str]:
        """Resolve contact identifier from respond_inbox_url to a numeric respond_io_id.

        The URL's last segment may be either a numeric respond_io_id (correct, new
        rows) or an internal RespondContact.id UUID (legacy rows pre-migration).
        Always resolve via DB so RespondClient.send_message receives the value
        Respond.io expects.
        """
        if not respond_inbox_url or not respond_inbox_url.strip():
            return None
        parts = [p for p in respond_inbox_url.rstrip("/").split("/") if p]
        if not parts:
            return None
        from app.services.respond_identifier import resolve_send_identifier

        return resolve_send_identifier(self.db, parts[-1])

    def update_complaint_and_reply(
        self,
        complaint_id: str,
        complaint_data: "ComplaintUpdate",
        respond_user_id: str,
        request_url: str = "",
        crm_sender_user_id: Optional[str] = None,
    ):
        """
        Deliver the technical team response: set status=responded, mark SLA tracking
        as responded, record ``last_responded_*`` and queue the Respond.io message.

        This is the RESPONSE path, not the chat path, so it is gated on status
        (UAC O1): outside the response stage it raises 422. Plain messaging is a
        different endpoint (``/conversation/send-message``) and stays open at any
        status, including approved, rejected and closed complaints (UAC O2).

        DB writes commit synchronously; the external Respond.io call is decoupled
        through the ``respond_io`` queue so a downstream 4xx/5xx no longer rolls
        back the business state. All integration calls are logged via IntegrationLogService.
        """
        import logging
        from datetime import datetime, timezone
        from app.services.integration_service import IntegrationLogService
        from app.schemas.integration import IntegrationLogCreate
        from app.services.sla_service import ConversationSLATrackingService
        from app.schemas.sla import ConversationSLATrackingUpdate

        logger = logging.getLogger(__name__)
        log_service = IntegrationLogService(self.db)

        complaint = self.get_complaint(complaint_id)
        # The whole call is a response write (it rewrites technical_team_response,
        # stamps last_responded_* and fires the technical_team_response SLA event),
        # so refuse it outside the response stage rather than gating one field.
        assert_response_write_allowed("complaint", complaint.status)
        update_data = complaint_data.model_dump(exclude_unset=True)
        # Same handling as update_complaint: `product_lines` dumps to a list of plain
        # dicts, and the setattr loop below would assign them straight onto the ORM
        # relationship ("'dict' object has no attribute '_sa_instance_state'"). The
        # edit page posts the whole complaint, lines included, so Update & Reply 500'd
        # there for any complaint with a product row.
        explicit_lines = update_data.pop("product_lines", None)
        contact_id = update_data.get("contact_id") if "contact_id" in update_data else complaint.contact_id
        space_id = update_data.get("space_id") if "space_id" in update_data else complaint.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            update_data["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            update_data["respond_inbox_url"] = None

        raw_incoming = update_data.get("technical_team_response")
        if raw_incoming is None or not str(raw_incoming).strip():
            raw_incoming = getattr(complaint, "technical_team_response", None)
        if not (raw_incoming and str(raw_incoming).strip()):
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error("technical_team_response is required to reply.")

        stored_body = self._normalize_complaint_reply_body_for_storage(str(raw_incoming))
        if not stored_body:
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error("technical_team_response is required to reply.")

        update_data.pop("technical_team_response", None)

        for key, value in update_data.items():
            setattr(complaint, key, value)
        if explicit_lines is not None:
            self._apply_explicit_product_lines(complaint, explicit_lines)
        elif "product_code" in update_data:
            self._populate_lines_from_legacy_csv(complaint)
        self.db.flush()

        complaint.technical_team_response = stored_body

        identifier = self._identifier_from_respond_inbox_url(getattr(complaint, "respond_inbox_url", None))
        do_number = (getattr(complaint, "delivery_order_number", None) or "").strip()
        do_spec = f" for delivery order {do_number}" if do_number else ""
        link_part = self._complaint_status_link_part(complaint, complaint_id)

        # Attachment sentence (UAC D1-D4, D2 hard blocker): composed here for
        # the OUTGOING message only, from the count of staff-uploaded
        # (uploader_kind='user') attachments already linked to this complaint
        # (staged in the "Edit technical team response" modal).
        # complaint.technical_team_response above already holds ``stored_body``
        # verbatim (byte for byte); ``outgoing_body`` below is a send-time-only
        # variable, never assigned back to the column.
        from app.services.entity_attachment_service import (
            compose_response_attachment_sentence,
            count_staff_attachments,
        )

        attachment_sentence = compose_response_attachment_sentence(
            count_staff_attachments(self.db, "complaint", complaint_id)
        )
        outgoing_body = f"{stored_body}\n{attachment_sentence}" if attachment_sentence else stored_body

        display_message = (
            f"There has been an update regarding your complaint{do_spec}: {outgoing_body}{link_part}"
        )
        # Structured-template vars: bare `update` core (the technical response +
        # attachment sentence, D10) + explicit link. See PLAN-complaint-structured-update-template.md.
        reply_extra_vars = {
            "update": outgoing_body,
            "portal_url": self._complaint_portal_or_view_url(complaint, complaint_id),
            "view_url": (self._build_complaint_view_url(complaint_id) or "").strip(),
        }

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        sla_service = ConversationSLATrackingService(self.db)
        tracking = sla_service.get_tracking_by_source_entity("complaint", complaint_id)
        if tracking:
            try:
                sla_service.update_tracking(
                    str(tracking.id),
                    ConversationSLATrackingUpdate(
                        is_responded=True,
                        responded_at=now_utc,
                        responded_by=respond_user_id,
                    ),
                )
                log_service.create_integration_log(
                    IntegrationLogCreate(
                        integration_channel="sla_management",
                        business_table="conversation_sla_tracking",
                        business_id=str(tracking.id),
                        external_reference=complaint_id,
                        direction="inbound",
                        endpoint=request_url or "/api/v1/complaints-management/complaints/update-and-reply",
                        http_method="POST",
                        status="success",
                    ),
                    request_payload_dict={"is_responded": True, "responded_by": respond_user_id},
                )
            except Exception as sla_err:
                logger.warning("SLA tracking update failed for complaint %s: %s", complaint_id, sla_err)
                log_service.create_integration_log(
                    IntegrationLogCreate(
                        integration_channel="sla_management",
                        business_table="conversation_sla_tracking",
                        business_id=str(tracking.id),
                        external_reference=complaint_id,
                        direction="inbound",
                        endpoint=request_url or "/api/v1/complaints-management/complaints/update-and-reply",
                        http_method="POST",
                        status="failed",
                        error_message=str(sla_err),
                    ),
                    request_payload_dict={"is_responded": True, "responded_by": respond_user_id},
                )

        # Only move to 'responded' from a response-stage status; re-sending a
        # reply on a decided complaint (approved/rejected/processed_by_cs/closed)
        # still delivers the message but must NOT regress the status.
        _cur_status = (getattr(complaint, "status", None) or "").strip().lower()
        if _cur_status in self._RESPONSE_STAGE_STATUSES:
            complaint.status = "responded"
        complaint.last_responded_by = respond_user_id
        complaint.last_responded_at = now_utc
        self.db.commit()
        self.db.refresh(complaint)

        # ----- Decoupled side effects -----
        # The complaint update (status=responded + technical_team_response) is already
        # committed above. Each remaining action is independent and best-effort: a
        # failure in one must not affect the DB state or the other actions.
        #   (a) save technical response  -> committed above (primary, synchronous)
        #   (b) send Respond.io message  -> enqueue below (best-effort)
        #   (c) email automation         -> dispatch below (best-effort)
        # plus the existing form-SLA respond event (best-effort).

        # (b) Send the technical-team reply to the customer via Respond.io.
        if identifier:
            try:
                self._enqueue_respond_message_for_complaint(
                    complaint_id=complaint_id,
                    identifier=identifier,
                    display_message=display_message,
                    respond_user_id=respond_user_id,
                    crm_sender_user_id=crm_sender_user_id,
                    space_id=getattr(complaint, "space_id", None),
                    extra_context_vars=reply_extra_vars,
                )
            except Exception:
                logger.exception(
                    "Complaint %s update-and-reply: enqueue Respond.io message failed; "
                    "complaint committed, other actions unaffected.",
                    complaint_id,
                )
        else:
            logger.info(
                "Complaint %s update-and-reply: no respond_inbox_url; complaint updated but no message queued.",
                complaint_id,
            )

        # Form-SLA respond event (best-effort).
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "complaint",
                str(complaint.id),
                "technical_team_response",
                contact_id=getattr(complaint, "contact_id", None),
                actor_user_id=crm_sender_user_id or respond_user_id,
            )
        except Exception as e:
            logging.getLogger(__name__).warning("Form SLA emit 'technical_team_response' failed for complaint %s: %s", complaint.id, e)

        # (c) Email automation to target users (best-effort, fully decoupled).
        try:
            from datetime import date as _date
            from app.services.automation_service import AutomationService
            from app.services.automation_triggers import _build_complaint_link

            rc = getattr(complaint, "root_cause", None)
            res = getattr(complaint, "resolution", None)
            ctx = {
                "complaint": {
                    "id": complaint_id,
                    "complaint_number": getattr(complaint, "complaint_number", None),
                    "delivery_order_number": getattr(complaint, "delivery_order_number", None),
                    "customer_name": getattr(complaint, "customer_name", None),
                    "salesperson": getattr(complaint, "salesperson", None),
                    "product_code": getattr(complaint, "product_code", None),
                    "complaint_type": getattr(complaint, "complaint_type", None),
                    "status": "responded",
                    "technical_team_response": stored_body,
                    "link": _build_complaint_link(complaint_id),
                    "root_cause": getattr(rc, "name", None) if rc is not None else None,
                    "resolution": getattr(res, "name", None) if res is not None else None,
                },
                "today": _date.today().isoformat(),
            }
            AutomationService(self.db).dispatch_event(
                "complaint_technical_response_updated",
                context=ctx,
                source_kind="complaint",
                source_id=complaint_id,
            )
        except Exception:
            logger.exception(
                "Automation dispatch_event(complaint_technical_response_updated) failed for %s; "
                "complaint committed, other actions unaffected.",
                complaint_id,
            )

        return complaint

    _DECIDE_ALLOWED_DECISIONS: tuple[str, ...] = ("approved", "rejected")
    # Approve/Reject is a post-reply gate: the technical reply must already have been sent
    # to the customer (status="responded") before a decision can be recorded.
    _DECIDE_ALLOWED_FROM_STATUSES: tuple[str, ...] = ("responded",)

    def decide_complaint(
        self,
        complaint_id: str,
        decision: str,
        respond_user_id: str,
        request_url: str = "",
        crm_sender_user_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ):
        """Mark complaint approved/rejected and notify the contact via Respond.io.

        Mirrors update_complaint_and_reply but: (a) does not change technical_team_response,
        (b) sends a status-change message instead of the technical body, (c) sets
        ``complaint.status`` to the decision value.
        """
        from datetime import datetime, timezone
        from app.services.error_handler import handle_validation_error

        logger = logging.getLogger(__name__)

        decision = (decision or "").strip().lower()
        if decision not in self._DECIDE_ALLOWED_DECISIONS:
            raise handle_validation_error(
                f"decision must be one of {self._DECIDE_ALLOWED_DECISIONS}; got {decision!r}."
            )

        normalized_reason = (rejection_reason or "").strip() or None
        if decision == "rejected" and not normalized_reason:
            raise handle_validation_error("rejection_reason is required when rejecting a complaint.")

        complaint = self.get_complaint(complaint_id)
        current_status = (getattr(complaint, "status", None) or "").strip().lower()
        if current_status not in self._DECIDE_ALLOWED_FROM_STATUSES:
            raise handle_validation_error(
                f"Cannot {decision} a complaint while status is {current_status!r}; "
                f"expected one of {self._DECIDE_ALLOWED_FROM_STATUSES}."
            )

        do_number = (getattr(complaint, "delivery_order_number", None) or "").strip()
        do_spec = f" for delivery order {do_number}" if do_number else ""
        link_part = self._complaint_status_link_part(complaint, complaint_id)
        reason_part = (
            f" Reason: {normalized_reason}." if decision == "rejected" and normalized_reason else ""
        )
        display_message = (
            f"There has been an update regarding your complaint{do_spec}: "
            f"status changed to {decision}.{reason_part}{link_part}"
        )
        # Structured-template vars: LEAN `update` core (status only) + link.
        # "Approved" / "Rejected, reason: X" - no preamble, no inline URL.
        decide_update_core = (
            "Approved"
            if decision == "approved"
            else (f"Rejected, reason: {normalized_reason}" if normalized_reason else "Rejected")
        )
        decide_extra_vars = {
            "update": decide_update_core,
            "portal_url": self._complaint_portal_or_view_url(complaint, complaint_id),
            "view_url": (self._build_complaint_view_url(complaint_id) or "").strip(),
        }

        identifier = self._identifier_from_respond_inbox_url(
            getattr(complaint, "respond_inbox_url", None)
        )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        complaint.status = decision
        complaint.last_responded_by = respond_user_id
        complaint.last_responded_at = now_utc
        if decision == "rejected":
            complaint.rejection_reason = normalized_reason
            complaint.rejected_at = now_utc
            complaint.rejected_by = respond_user_id
        else:
            # Approving clears any prior rejection metadata so the audit trail reflects
            # the current decision; historical changes remain in the audit log.
            complaint.rejection_reason = None
            complaint.rejected_at = None
            complaint.rejected_by = None
        self.db.commit()
        self.db.refresh(complaint)

        if identifier:
            self._enqueue_respond_message_for_complaint(
                complaint_id=complaint_id,
                identifier=identifier,
                display_message=display_message,
                respond_user_id=respond_user_id,
                crm_sender_user_id=crm_sender_user_id,
                space_id=getattr(complaint, "space_id", None),
                extra_context_vars=decide_extra_vars,
            )
        else:
            logger.info(
                "Complaint %s decision=%s: no respond_inbox_url; status updated but no message queued.",
                complaint_id,
                decision,
            )

        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "complaint",
                str(complaint.id),
                decision,
                contact_id=getattr(complaint, "contact_id", None),
                actor_user_id=crm_sender_user_id or respond_user_id,
            )
        except Exception as e:
            logging.getLogger(__name__).warning("Form SLA emit '%s' failed for complaint %s: %s", decision, complaint.id, e)

        if decision == "approved":
            try:
                from datetime import date as _date
                from app.services.automation_service import AutomationService
                from app.services.automation_triggers import _build_complaint_link

                rc = getattr(complaint, "root_cause", None)
                res = getattr(complaint, "resolution", None)
                ctx = {
                    "complaint": {
                        "id": complaint_id,
                        "complaint_number": getattr(complaint, "complaint_number", None),
                        "delivery_order_number": getattr(complaint, "delivery_order_number", None),
                        "customer_name": getattr(complaint, "customer_name", None),
                        "salesperson": getattr(complaint, "salesperson", None),
                        "product_code": getattr(complaint, "product_code", None),
                        "complaint_type": getattr(complaint, "complaint_type", None),
                        "status": "approved",
                        "link": _build_complaint_link(complaint_id),
                        "root_cause": getattr(rc, "name", None) if rc is not None else None,
                        "resolution": getattr(res, "name", None) if res is not None else None,
                    },
                    "today": _date.today().isoformat(),
                }
                AutomationService(self.db).dispatch_event(
                    "complaint_approved",
                    context=ctx,
                    source_kind="complaint",
                    source_id=complaint_id,
                )
            except Exception:
                logger.exception(
                    "Automation dispatch_event(complaint_approved) failed for %s",
                    complaint_id,
                )
                # Approval already committed; never fail the whole flow on automation errors.

        return complaint

    # Customer-service close actions: only an approved complaint (post-approval,
    # customer-service stage) can be finalized. 'processed_by_cs' (CS handled it)
    # and 'closed' (can't resolve) both close the CS SLA stage.
    _RESOLVE_ALLOWED_FROM_STATUSES: tuple[str, ...] = ("approved",)
    _FINALIZE_STATUSES: tuple[str, ...] = ("processed_by_cs", "closed")
    # Customer-facing wording for the Respond.io status-update message.
    _FINALIZE_STATUS_LABELS: dict[str, str] = {
        "processed_by_cs": "processed by our customer service team",
        "closed": "closed",
    }
    # LEAN labels for the structured-template `update` core (no preamble/URL).
    _FINALIZE_UPDATE_LABELS: dict[str, str] = {
        "processed_by_cs": "Processed by CS",
        "closed": "Closed",
    }

    def mark_processed_by_cs(
        self,
        complaint_id: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Mark an approved complaint as processed by customer service.

        This is the normal CS completion (not a literal "resolved" - CS handled
        the case). Closes the customer-service SLA stage.
        """
        return self._finalize_complaint(
            complaint_id,
            "processed_by_cs",
            note=note,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )

    def close_complaint(
        self,
        complaint_id: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Close an approved complaint that can't be resolved (status='closed').

        Not a true resolution for the complaint, but it DOES close the
        customer-service SLA stage (same 'resolved' form-SLA event).
        """
        return self._finalize_complaint(
            complaint_id,
            "closed",
            note=note,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )

    def _finalize_complaint(
        self,
        complaint_id: str,
        new_status: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Finalize an approved complaint to ``new_status`` ('resolved' | 'closed').

        Three decoupled best-effort side effects after the status commit:
          (a) set status + resolved_at/by   -> committed (primary)
          (b) send a Respond.io status-update message to the contact (+ optional note)
          (c) emit the 'resolved' form-SLA event (closes the customer-service stage)
        (b) and (c) never fail the operation; the committed status is source of truth.
        """
        import logging
        from datetime import datetime, timezone

        logger = logging.getLogger(__name__)
        if new_status not in self._FINALIZE_STATUSES:
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Unsupported finalize status: {new_status!r}.")

        complaint = self.get_complaint(complaint_id)
        current_status = (getattr(complaint, "status", None) or "").strip().lower()
        if current_status == new_status:
            # Idempotent: already in the target state.
            return complaint
        if current_status not in self._RESOLVE_ALLOWED_FROM_STATUSES:
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(
                f"Complaint must be approved before it can be marked {new_status} "
                f"(current status: {current_status or 'unknown'})."
            )

        # Build the customer-facing status-update message (before commit/refresh).
        do_number = (getattr(complaint, "delivery_order_number", None) or "").strip()
        do_spec = f" for delivery order {do_number}" if do_number else ""
        link_part = self._complaint_status_link_part(complaint, complaint_id)
        note_clean = (note or "").strip()
        note_part = f" Note: {note_clean}" if note_clean else ""
        status_label = self._FINALIZE_STATUS_LABELS.get(new_status, new_status)
        display_message = (
            f"There has been an update regarding your complaint{do_spec}: "
            f"status changed to {status_label}.{note_part}{link_part}"
        )
        # Structured-template vars: LEAN `update` core (status only) + link.
        # "Processed by CS" / "Closed" - optional note kept, no preamble/URL.
        finalize_update_core = self._FINALIZE_UPDATE_LABELS.get(new_status, status_label)
        if note_clean:
            finalize_update_core += f". Note: {note_clean}"
        finalize_extra_vars = {
            "update": finalize_update_core,
            "portal_url": self._complaint_portal_or_view_url(complaint, complaint_id),
            "view_url": (self._build_complaint_view_url(complaint_id) or "").strip(),
        }
        identifier = self._identifier_from_respond_inbox_url(
            getattr(complaint, "respond_inbox_url", None)
        )

        # (a) Commit the status (primary, synchronous).
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        complaint.status = new_status
        complaint.resolved_at = now_utc
        complaint.resolved_by = respond_user_id or crm_sender_user_id
        self.db.commit()
        self.db.refresh(complaint)

        # (b) Send the status-update message to the contact (best-effort, decoupled).
        if identifier:
            try:
                self._enqueue_respond_message_for_complaint(
                    complaint_id=complaint_id,
                    identifier=identifier,
                    display_message=display_message,
                    respond_user_id=respond_user_id,
                    crm_sender_user_id=crm_sender_user_id,
                    space_id=getattr(complaint, "space_id", None),
                    extra_context_vars=finalize_extra_vars,
                )
            except Exception:
                logger.exception(
                    "Complaint %s finalize=%s: enqueue Respond.io message failed; status committed.",
                    complaint_id,
                    new_status,
                )
        else:
            logger.info(
                "Complaint %s finalize=%s: no respond_inbox_url; status updated but no message queued.",
                complaint_id,
                new_status,
            )

        # (c) Close the customer-service form-SLA stage (best-effort). Both resolved
        # and closed emit the same 'resolved' SLA event - the stage is done either way.
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "complaint",
                str(complaint.id),
                "resolved",
                contact_id=getattr(complaint, "contact_id", None),
                actor_user_id=crm_sender_user_id or respond_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'resolved' failed for complaint %s: %s", complaint.id, e)

        return complaint

    # ----- Void (terminal, irreversible) -----
    # Blocked once terminal: rejected / resolved / closed / processed_by_cs (the CS
    # completion states) or already voided. Everything else is voidable.
    _VOID_BLOCKED_STATUSES: tuple[str, ...] = (
        "voided",
        "rejected",
        "resolved",
        "closed",
        "processed_by_cs",
    )

    def void_complaint(
        self,
        complaint_id: str,
        *,
        void_reason: str,
        actor_user_id: Optional[str] = None,
        respond_user_id: Optional[str] = None,
    ):
        """Void a complaint (irreversible).

        Requires a free-text reason (>= 3 chars), allowed only from a non-terminal
        state. Sets status='voided' + the reason quad, emits the 'voided' form-SLA
        event (SLA stop is pure config), then best-effort notifies assignee +
        handling-lock holder (in-app) and the salesperson (WhatsApp).
        """
        import logging
        from datetime import datetime, timezone

        logger = logging.getLogger(__name__)
        from app.services.error_handler import handle_conflict, handle_validation_error

        reason = (void_reason or "").strip()
        if len(reason) < 3:
            raise handle_validation_error(
                "A void reason of at least 3 characters is required."
            )
        complaint = self.get_complaint(complaint_id)
        current = (getattr(complaint, "status", None) or "").strip().lower()
        if current in self._VOID_BLOCKED_STATUSES:
            raise handle_conflict(
                f"This complaint cannot be voided from its current state "
                f"({current or 'unknown'})."
            )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        complaint.status = "voided"
        complaint.voided_by = actor_user_id
        complaint.voided_at = now_utc
        complaint.void_reason = reason
        self.db.commit()
        self.db.refresh(complaint)

        # Notify BEFORE emit (emit may resolve the tracker via 'voided' resolve_event,
        # after which the in-app assignee/handler lookup would find nothing).
        self._notify_complaint_voided(
            complaint, actor_user_id=actor_user_id, respond_user_id=respond_user_id
        )

        # Emit the 'voided' form-SLA event. NO direct tracker-stop code - the tracker
        # closes only if form_sla_config.resolve_event lists 'voided' (pure config).
        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                "complaint",
                str(complaint.id),
                "voided",
                contact_id=getattr(complaint, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'voided' failed for complaint %s: %s", complaint.id, e)

        return complaint

    def _notify_complaint_voided(
        self,
        complaint,
        *,
        actor_user_id: Optional[str],
        respond_user_id: Optional[str],
    ) -> None:
        """Best-effort void notifications (NTF): assignee + handler in-app, salesperson WhatsApp."""
        import logging

        logger = logging.getLogger(__name__)
        try:
            from app.services.form_void_notify import notify_form_voided_in_app

            notify_form_voided_in_app(
                self.db,
                source_entity_type="complaint",
                source_entity_id=str(complaint.id),
                entity_number=getattr(complaint, "complaint_number", None),
                actor_user_id=actor_user_id,
            )
        except Exception:
            logger.warning("Void in-app notify failed for complaint %s", complaint.id, exc_info=True)

        # Salesperson WhatsApp via the existing complaint status-update path.
        try:
            identifier = self._identifier_from_respond_inbox_url(
                getattr(complaint, "respond_inbox_url", None)
            )
            if not identifier:
                return
            number = (getattr(complaint, "complaint_number", None) or str(complaint.id)).strip()
            reason = (getattr(complaint, "void_reason", None) or "").strip()
            do_number = (getattr(complaint, "delivery_order_number", None) or "").strip()
            do_spec = f" for delivery order {do_number}" if do_number else ""
            display_message = (
                f"Your complaint {number}{do_spec} has been voided. Reason: {reason}"
                if reason
                else f"Your complaint {number}{do_spec} has been voided."
            )
            self._enqueue_respond_message_for_complaint(
                complaint_id=str(complaint.id),
                identifier=identifier,
                display_message=display_message,
                respond_user_id=(respond_user_id or "").strip() or identifier,
                crm_sender_user_id=actor_user_id,
                space_id=getattr(complaint, "space_id", None),
                extra_context_vars={"update": "Voided", "message": display_message},
            )
        except Exception:
            logger.warning(
                "Void salesperson WhatsApp send failed for complaint %s; void committed.",
                complaint.id,
                exc_info=True,
            )

    def sync_assignee_from_respond(self, complaint_id: str) -> dict:
        """
        Fetch contact from Respond.io by complaint's contact_id, get assignee.id,
        match to CRM user by respond_user_id, and update complaint.assigned_to.
        """
        import json
        import logging
        from app.models.user import User
        from app.services.integration_service import RespondClient, IntegrationLogService
        from app.schemas.integration import IntegrationLogCreate
        from app.services.error_handler import handle_validation_error

        logger = logging.getLogger(__name__)
        complaint = self.get_complaint(complaint_id)
        contact_id = (complaint.contact_id or "").strip()
        if not contact_id:
            raise handle_validation_error(
                "No contact_id for this complaint; cannot sync assignee from Respond.io. Set Contact ID (from respond.io) on the complaint."
            )

        log_service = IntegrationLogService(self.db)
        endpoint_path = f"/v2/contact/id:{contact_id}"

        try:
            client = RespondClient()
            payload = client.get_contact_by_identifier(contact_id)
        except ValueError as e:
            logger.warning("Respond.io not configured or error: %s", e)
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="complaints",
                    business_id=complaint_id,
                    direction="outbound",
                    endpoint=endpoint_path,
                    http_method="GET",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict={"action": "sync_assignee", "contact_id": contact_id},
            )
            raise handle_validation_error(f"Respond.io API is not configured or error: {e!s}")
        except Exception as e:
            logger.exception("Respond.io get_contact failed for complaint %s", complaint_id)
            resp_payload = None
            response_obj = getattr(e, "response", None)
            response_text = getattr(response_obj, "text", None)
            if isinstance(response_text, str) and response_text:
                resp_payload = response_text[:2000] if len(response_text) > 2000 else response_text
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="complaints",
                    business_id=complaint_id,
                    direction="outbound",
                    endpoint=endpoint_path,
                    http_method="GET",
                    status="failed",
                    error_message=str(e),
                    response_payload=resp_payload,
                ),
                request_payload_dict={"action": "sync_assignee", "contact_id": contact_id},
            )
            raise

        log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="respond_io",
                business_table="complaints",
                business_id=complaint_id,
                direction="outbound",
                endpoint=endpoint_path,
                http_method="GET",
                status="success",
                response_payload=json.dumps(payload, indent=2),
            ),
            request_payload_dict={"action": "sync_assignee", "contact_id": contact_id},
        )

        assignee = payload.get("assignee")
        if not assignee or assignee.get("id") is None:
            complaint.assigned_to = None
            self.db.commit()
            return {"updated": True, "message": "Sync successful. No assignee in Respond.io; Assigned To cleared."}

        assignee_respond_id = str(assignee.get("id"))
        user = self.db.query(User).filter(User.respond_user_id == assignee_respond_id).first()
        if not user:
            return {
                "updated": False,
                "message": f"Sync successful. No user in CRM with respond_user_id '{assignee_respond_id}'; Assigned To unchanged. Link Respond.io user ID in User Management to sync.",
            }

        if complaint.assigned_to == assignee_respond_id:
            return {"updated": False, "message": "Sync successful. Assignee already in sync."}

        complaint.assigned_to = assignee_respond_id
        self.db.commit()
        self.db.refresh(complaint)
        return {
            "updated": True,
            "message": "Assignee synced from Respond.io.",
            "assigned_to": user.name or user.email or assignee_respond_id,
            "assigned_to_id": str(user.id),
        }

    def delete_complaint(self, complaint_id: str) -> None:
        """Delete a complaint and its related attachments."""
        complaint = self.get_complaint(complaint_id)
        self.entity_attachment_service.delete_links_for_entity("complaint", str(complaint.id))
        self.db.delete(complaint)
        self.db.commit()

    def bulk_delete_complaints(self, complaint_ids: list[str]) -> dict:
        """Delete multiple complaints by ID. Returns deleted_count."""
        if not complaint_ids:
            return {"message": "No complaints to delete.", "deleted_count": 0}
        for complaint_id in complaint_ids:
            self.entity_attachment_service.delete_links_for_entity("complaint", str(complaint_id))
        deleted = self.db.query(Complaint).filter(Complaint.id.in_(complaint_ids)).delete(synchronize_session=False)
        self.db.commit()
        return {"message": f"Deleted {deleted} complaint(s).", "deleted_count": deleted}

    def link_attachment_to_complaint(self, complaint_id: str, attachment_id: str, created_by: Optional[str] = None):
        """Link an existing attachment to a complaint (generic entity_attachment_links table)."""
        self.get_complaint(complaint_id)  # ensure complaint exists
        link = self.entity_attachment_service.link_existing_attachment(
            entity_type="complaint",
            entity_id=str(complaint_id),
            attachment_id=str(attachment_id),
            created_by=created_by,
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    def delete_complaint_attachment(self, link_id: str):
        """Delete a complaint-attachment link (from generic entity_attachment_links table)."""
        link = self.entity_attachment_service.delete_link(link_id, entity_type="complaint")
        self.db.commit()
        return link

    # ----- Respond.io notify helpers (shared between status-change + field updates) -----

    def _enqueue_respond_message_for_complaint(
        self,
        *,
        complaint_id: str,
        identifier: str,
        display_message: str,
        respond_user_id: str,
        crm_sender_user_id: Optional[str],
        space_id: Optional[str],
        extra_context_vars: Optional[dict] = None,
    ) -> None:
        """Push a Respond.io send into the RQ ``respond_io`` queue.

        Decouples the external API call from the request lifecycle so a Respond.io
        4xx/5xx does not roll back the surrounding business write. Failed jobs land
        in RQ's FailedJobRegistry and a ``status='failed'`` integration_logs row.

        ``extra_context_vars`` carries the structured-template vars (bare ``update``
        core + ``portal_url``). See PLAN-complaint-structured-update-template.md.
        """
        from app.services.queue_service import enqueue_job
        from app.tasks.respond_io_tasks import send_complaint_respond_message

        job = enqueue_job(
            send_complaint_respond_message,
            complaint_id,
            identifier,
            display_message,
            respond_user_id,
            crm_sender_user_id,
            space_id,
            extra_context_vars,
            queue_name="respond_io",
            job_timeout=180,
        )
        logging.getLogger(__name__).info(
            "Enqueued respond.io send job %s for complaint %s",
            job.id,
            complaint_id,
        )

    def _send_respond_message_for_complaint(
        self,
        complaint,
        *,
        identifier: str,
        display_message: str,
        respond_user_id: str,
        crm_sender_user_id: Optional[str],
        extra_context_vars: Optional[dict] = None,
    ) -> None:
        """Enqueue a Respond.io send for a complaint (compatibility wrapper)."""
        self._enqueue_respond_message_for_complaint(
            complaint_id=str(getattr(complaint, "id")),
            identifier=identifier,
            display_message=display_message,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
            space_id=getattr(complaint, "space_id", None),
            extra_context_vars=extra_context_vars,
        )

    def _notify_complaint_field(
        self,
        complaint_id: str,
        kind: str,
        *,
        respond_user_id: str,
        crm_sender_user_id: Optional[str],
    ) -> dict:
        """Send a Respond.io update message for either ``root_cause`` or ``resolution``.

        Mirrors ``decide_complaint``'s status-change message format. Persists the
        ``*_notified_at`` timestamp on the complaint so the show view can audit pings.
        """
        from datetime import datetime, timezone
        from app.services.error_handler import handle_validation_error

        if kind not in ("root_cause", "resolution"):
            raise handle_validation_error(f"Unsupported notify field: {kind}")

        complaint = self.get_complaint(complaint_id)
        target = complaint.root_cause if kind == "root_cause" else complaint.resolution
        target_fk = complaint.root_cause_id if kind == "root_cause" else complaint.resolution_id
        if not target_fk or target is None:
            label = "root cause" if kind == "root_cause" else "resolution"
            raise handle_validation_error(
                f"Cannot notify salesperson - no {label} is set on this complaint."
            )

        identifier = self._identifier_from_respond_inbox_url(
            getattr(complaint, "respond_inbox_url", None)
        )
        if not identifier:
            raise handle_validation_error(
                "Cannot notify salesperson - no Respond.io contact is linked to this complaint."
            )

        do_number = (getattr(complaint, "delivery_order_number", None) or "").strip()
        do_spec = f" for delivery order {do_number}" if do_number else ""
        link_part = self._complaint_status_link_part(complaint, complaint_id)
        label_word = "Root cause" if kind == "root_cause" else "Resolution"
        display_message = (
            f"There has been an update regarding your complaint{do_spec}: "
            f"{label_word} is identified as {target.name}.{link_part}"
        )
        # Structured-template vars: LEAN `update` core - "Root cause is X" /
        # "Resolution is X", no preamble, no inline URL.
        notify_extra_vars = {
            "update": f"{label_word} is {target.name}",
            "portal_url": self._complaint_portal_or_view_url(complaint, complaint_id),
            "view_url": (self._build_complaint_view_url(complaint_id) or "").strip(),
        }

        self._send_respond_message_for_complaint(
            complaint,
            identifier=identifier,
            display_message=display_message,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
            extra_context_vars=notify_extra_vars,
        )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        if kind == "root_cause":
            complaint.root_cause_notified_at = now_utc
        else:
            complaint.resolution_notified_at = now_utc
        self.db.commit()
        self.db.refresh(complaint)
        return {"sent": True, "message": display_message, "notified_at": now_utc.isoformat()}

    def notify_root_cause_to_salesperson(
        self,
        complaint_id: str,
        *,
        respond_user_id: str,
        crm_sender_user_id: Optional[str] = None,
    ) -> dict:
        return self._notify_complaint_field(
            complaint_id,
            "root_cause",
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )

    def notify_resolution_to_salesperson(
        self,
        complaint_id: str,
        *,
        respond_user_id: str,
        crm_sender_user_id: Optional[str] = None,
    ) -> dict:
        return self._notify_complaint_field(
            complaint_id,
            "resolution",
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )
