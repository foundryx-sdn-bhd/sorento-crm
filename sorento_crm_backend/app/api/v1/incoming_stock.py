"""User-facing incoming-stock API.

Purpose
-------
A tight, business-rule-compliant surface for answering "is there any incoming stock?" questions.
Intended to be the primary route used by the AI assistant / MCP layer. Unlike the underlying
procurement APIs, these routes:

  * exclude already-received lines,
  * compute `remaining_incoming_quantity` server-side,
  * aggregate warehouse allocations by warehouse_code (no SPO leakage),
  * never expose `quantity_received`, `quantity_rejected`, `receipt_status`,
  * never expose internal UUIDs, SPO numbers, picking-line identifiers, or inbound_shipment_lines_id.

See `next_agents/incoming_stock_enquiries.txt` for the source rules and `app/services/
incoming_stock_service.py` for the implementation.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user_or_api_key
from app.services.eta_policy import (
    apply_to_incoming,
    dealer_view,
    is_dealer,
    query_eta_from,
    resolve_request_contact,
    rules_for_contact,
)
from app.services.field_access import CLEARANCE_PERMISSION, apply_field_access
from app.services.error_handler import handle_internal_error
from app.services.incoming_stock_service import IncomingStockService
from app.services.uuid_list_param import parse_uuid_list


router = APIRouter()


class _Contact:
    """Who a request asks on behalf of, resolved ONCE (issue #1328): the internal contact
    id, and that contact's ETA / packing-list rules. `None` rules = no contact in play (a
    staff session or the bare API key), and the payload goes out exactly as before."""

    def __init__(self, db: Session, contact_id: Optional[str], space_id: Optional[str]):
        self.asked = bool(contact_id)
        self.resolved = resolve_request_contact(db, contact_id, space_id) if contact_id else None
        self.rules = rules_for_contact(db, self.resolved) if contact_id else None
        #: The packing list regions this contact may be told about; None = no contact, no filter.
        self.regions = self.rules.regions if self.rules is not None else None

    def eta_from(self, db: Session, eta_from: Optional[date]) -> Optional[date]:
        return query_eta_from(db, self.rules, eta_from)

    def windowed(self, eta_from: Optional[date], eta_to: Optional[date]) -> bool:
        """Is this answer judged on the PADDED date? Then the service's own paging (on the
        real date, over a widened window) cannot be trusted: a page of rows that pad out of
        the window would read as "nothing arriving" while a later page holds the answer."""
        return bool(
            self.rules is not None
            and self.rules.offset_applied
            and (eta_from is not None or eta_to is not None)
        )


#: How many rows a windowed contact answer reads before judging them on the padded date:
#: _WINDOW_PAGES service pages of the service's own maximum (50). A contact asking about
#: a date window past this many shipments is answered from the earliest of them.
_WINDOW_PAGES = 10
_SERVICE_MAX_LIMIT = 50


def _fetch_window(
    fetch, contact: _Contact, *, eta_from, eta_to, page: int, limit: int, pageable: bool = True
):
    """Fetch the page the caller asked for - or, when the answer is judged on the padded
    date, every row of the widened window (up to `_WINDOW_PAGES` service pages), so
    `_for_contact` can filter them and page the survivors itself."""
    if not contact.windowed(eta_from, eta_to):
        return fetch(page, limit), None
    first = fetch(1, _SERVICE_MAX_LIMIT)
    rows = list(first.get("data") or []) if isinstance(first, dict) else []
    n = 1
    while pageable and isinstance(first, dict) and n < _WINDOW_PAGES:
        if len(rows) >= int((first.get("pagination") or {}).get("total") or 0):
            break
        n += 1
        more = fetch(n, _SERVICE_MAX_LIMIT)
        chunk = (more or {}).get("data") or []
        if not chunk:
            break
        rows.extend(chunk)
    if isinstance(first, dict):
        first["data"] = rows
    return first, (page, limit)


def _for_contact(
    db: Session,
    result,
    contact: _Contact,
    *,
    current_user,
    eta_from: Optional[date] = None,
    eta_to: Optional[date] = None,
    paged: Optional[tuple[int, int]] = None,
    asked: Optional[list[str]] = None,
):
    """One gate for every incoming route: the contact's ETA offset and packing list rule
    (`eta_policy.apply_to_incoming`), then the per-field reveals. Both read the SAME
    resolved id; an unresolved contact passes an id that resolves to nobody, so the
    field gate denies every gated field (fail closed), exactly as before this change.
    With no contact in play the payload is returned untouched."""
    if contact.rules is None:
        return result
    result = apply_to_incoming(db, result, contact.rules, eta_from=eta_from, eta_to=eta_to)
    if paged is not None and isinstance(result, dict) and isinstance(result.get("data"), list):
        # `_fetch_window` read the whole widened window: page it on the padded date here.
        page, limit = paged
        rows = result["data"]
        result["data"] = rows[(page - 1) * limit : page * limit]
        result["pagination"] = {"total": len(rows), "page": page, "limit": limit}
        result["empty"] = not result["data"]
    result = apply_field_access(
        db,
        result,
        resource="incoming_stock",
        current_user=current_user,
        contact_id=contact.resolved or _UNRESOLVED_CONTACT,
        staff_permission=CLEARANCE_PERMISSION,
    )
    if is_dealer(db, contact.resolved):
        # PR #1329 fix round: a dealer is told each product once and its distinct ETAs -
        # after the reveals, so a date the contact may not see is not told. Who to ask
        # is the presenter's one refer sentence (REFER-SALESMAN).
        # AVAIL-MODE-REPLIES: every product the dealer asked about is told, one with no
        # shipment as "No ETA" - unless a date window was asked, where no
        # row only means none in that window.
        windowed = eta_from is not None or eta_to is not None
        result = dealer_view(result, asked=None if windowed else _asked_codes(db, asked))
    return result


def _asked_codes(db: Session, tokens: Optional[list[str]]) -> list[str]:
    """The product codes a request named, in its order: each token is a product id or a
    code (the routes accept both)."""
    from app.models.product import Product
    from sqlalchemy import func, or_

    wanted = [str(t).strip() for t in tokens or [] if t and str(t).strip()]
    if not wanted:
        return []
    ids = []
    for token in wanted:
        try:
            ids.append(str(uuid.UUID(token)))
        except ValueError:
            continue
    matches = [func.lower(Product.product_code).in_([t.lower() for t in wanted])]
    if ids:
        matches.append(Product.id.in_(ids))
    rows = db.query(Product.id, Product.product_code).filter(or_(*matches)).all()
    by_token: dict[str, str] = {}
    for pid, code in rows:
        if code:
            by_token.setdefault(str(pid), code)
            by_token.setdefault(code.lower(), code)
    out: list[str] = []
    for token in wanted:
        code = by_token.get(token) or by_token.get(token.lower())
        if code and code not in out:
            out.append(code)
    return out


#: A contact id no row carries: `apply_field_access` treats a contact that named nobody
#: as CONTACT_NOT_FOUND on every gated field. Passing `None` instead would switch it to
#: the STAFF path and hand an unresolved contact the whole payload.
_UNRESOLVED_CONTACT = "__unresolved_contact__"


_CONTACT_ID_DOC = (
    "The contact this question is being asked ON BEHALF OF (respond_contacts.id or the "
    "Respond.io id). When set, the ETA carries that contact's +x days offset when their "
    "switch is on, the packing list is sent only when their packing list switch is on, "
    "and gated fields follow their Incoming Stock Enquiries field reveals."
)
_SPACE_ID_DOC = "Respond.io workspace id, to disambiguate a Respond.io `contact_id`."


@router.get("/by-product")
def get_incoming_for_product(
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `product_ids`.",
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Canonical product UUIDs (csv/JSON/repeated). Preferred filter.",
    ),
    product_id: Optional[list[str]] = Query(
        None,
        description="Legacy: product UUID / product_code. Direct callers can keep using this.",
    ),
    query: Optional[str] = Query(
        None,
        description="Free-text search over product_code and product_name.",
    ),
    eta_from: Optional[date] = Query(None, description="Include shipments with ETA on/after this date (YYYY-MM-DD)."),
    eta_to: Optional[date] = Query(None, description="Include shipments with ETA on/before this date (YYYY-MM-DD)."),
    limit: int = Query(10, ge=1, le=50),
    contact_id: Optional[str] = Query(None, description=_CONTACT_ID_DOC),
    space_id: Optional[str] = Query(None, description=_SPACE_ID_DOC),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Answer 'any incoming for product X?' questions.

    Returns, per matched product: total remaining incoming quantity, nearest ETA, per-warehouse
    allocation summary, and the individual open shipments. Optional `eta_from` / `eta_to`
    narrow to shipments arriving within a date window.
    """
    from app.services.entity_filter_helpers import (
        normalize_entities_query_param,
        resolve_or_empty,
    )

    resolved_product_filter: list[str] = []
    # Validated UUID list from the canonical param.
    uuid_list = parse_uuid_list(product_ids, param_name="product_ids")
    if uuid_list:
        resolved_product_filter.extend(uuid_list)
    # Legacy product_id (UUID-or-code, comma-separated allowed).
    for raw in product_id or []:
        if raw is None:
            continue
        for piece in str(raw).split(","):
            piece = piece.strip()
            if piece:
                resolved_product_filter.append(piece)

    try:
        contact = _Contact(db, contact_id, space_id)
    except Exception as e:
        raise handle_internal_error(str(e))

    # Resolve entities → product_codes → push through legacy product_ids path.
    entity_echo = None
    norm = normalize_entities_query_param(entities)
    if norm:
        buckets = resolve_or_empty(db, norm, regions=contact.regions)
        if buckets is not None:
            entity_echo = buckets.as_echo()
            if not buckets.product_codes:
                return {
                    "data": [],
                    "empty": True,
                    "resolved_entities": entity_echo,
                }
            resolved_product_filter.extend(buckets.product_codes)
    try:
        svc = IncomingStockService(db, regions=contact.regions)
        # A windowed contact answer reads the service's maximum and pages the products
        # that survive the padded window itself (`_fetch_window`); this route has no page.
        result, paged = _fetch_window(
            lambda _page, page_limit: svc.incoming_for_product(
                product_ids=resolved_product_filter or None,
                query=query,
                eta_from=contact.eta_from(db, eta_from),
                eta_to=eta_to,
                limit=page_limit,
            ),
            contact,
            eta_from=eta_from,
            eta_to=eta_to,
            page=1,
            limit=limit,
            pageable=False,
        )
        if entity_echo is not None and isinstance(result, dict):
            result["resolved_entities"] = entity_echo
        return _for_contact(
            db,
            result,
            contact,
            current_user=current_user,
            eta_from=eta_from,
            eta_to=eta_to,
            paged=paged,
            asked=resolved_product_filter,
        )
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/shipments")
def get_incoming_shipments(
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `shipment_ids` / `supplier_ids`.",
    ),
    shipment_ids: Optional[list[str]] = Query(
        None,
        description="Canonical inbound-shipment UUIDs (csv/JSON/repeated).",
    ),
    supplier_ids: Optional[list[str]] = Query(
        None,
        description="Canonical supplier UUIDs to narrow shipments to those suppliers.",
    ),
    query: Optional[str] = Query(
        None,
        description="Free-text search over shipment_number, container, BOL, invoice.",
    ),
    eta_from: Optional[date] = Query(None, description="Include shipments with ETA on/after this date."),
    eta_to: Optional[date] = Query(None, description="Include shipments with ETA on/before this date."),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    contact_id: Optional[str] = Query(None, description=_CONTACT_ID_DOC),
    space_id: Optional[str] = Query(None, description=_SPACE_ID_DOC),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Answer 'any incoming shipments?' / 'what is arriving this month?' questions."""
    from app.services.entity_filter_helpers import (
        normalize_entities_query_param,
        resolve_or_empty,
    )

    try:
        contact = _Contact(db, contact_id, space_id)
    except Exception as e:
        raise handle_internal_error(str(e))
    entity_echo = None
    extra_query = query
    shipment_uuid_list = parse_uuid_list(shipment_ids, param_name="shipment_ids")
    supplier_uuid_list = parse_uuid_list(supplier_ids, param_name="supplier_ids")
    norm = normalize_entities_query_param(entities)
    if norm:
        buckets = resolve_or_empty(db, norm, regions=contact.regions)
        if buckets is not None:
            entity_echo = buckets.as_echo()
            if buckets.shipment_numbers:
                # service supports free-text `query` that searches shipment_number;
                # join multiple resolved numbers with OR via repeated calls? Simplest:
                # use first resolved shipment_number as the search term. If multiple,
                # narrow via service-side IN by extending `query` to first hit.
                extra_query = buckets.shipment_numbers[0]
            elif not buckets.has_resolved_filter:
                return {
                    "data": [],
                    "empty": True,
                    "resolved_entities": entity_echo,
                }
    try:
        svc = IncomingStockService(db, regions=contact.regions)
        result, paged = _fetch_window(
            lambda p, n: svc.incoming_shipments(
                query=extra_query,
                shipment_ids=shipment_uuid_list,
                supplier_ids=supplier_uuid_list,
                eta_from=contact.eta_from(db, eta_from),
                eta_to=eta_to,
                page=p,
                limit=n,
            ),
            contact,
            eta_from=eta_from,
            eta_to=eta_to,
            page=page,
            limit=limit,
        )
        if entity_echo is not None and isinstance(result, dict):
            result["resolved_entities"] = entity_echo
        return _for_contact(
            db,
            result,
            contact,
            current_user=current_user,
            eta_from=eta_from,
            eta_to=eta_to,
            paged=paged,
        )
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/list")
def get_incoming_list(
    product_ids: Optional[list[str]] = Query(
        None,
        description="Canonical product UUIDs or product_codes/SKUs (csv/JSON/repeated). When set, lines are filtered to these products.",
    ),
    shipment_ids: Optional[list[str]] = Query(
        None,
        description="Canonical inbound-shipment UUIDs (csv/JSON/repeated).",
    ),
    supplier_ids: Optional[list[str]] = Query(
        None,
        description="Canonical supplier UUIDs to narrow shipments to those suppliers.",
    ),
    query: Optional[str] = Query(
        None,
        description="Free-text search over shipment_number, container, BOL, invoice.",
    ),
    eta_from: Optional[date] = Query(None, description="Include shipments with ETA on/after this date (YYYY-MM-DD)."),
    eta_to: Optional[date] = Query(None, description="Include shipments with ETA on/before this date (YYYY-MM-DD)."),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "The contact this question is being asked ON BEHALF OF - either the "
            "respond_contacts.id or the Respond.io id. When set, clearance dates "
            "are returned only for the fields allowed on that contact's own agent "
            "grants; the API key's privileges do not apply to a contact's question. "
            "Denied fields are absent, and the reason is in `field_access.denied`."
        ),
    ),
    space_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io workspace id. Only used to disambiguate `contact_id` when "
            "it is a Respond.io id: the same id can exist in two workspaces, and "
            "resolving to the wrong one would answer with a stranger's grants."
        ),
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Unified incoming-stock list - shipment-rooted with nested product lines.

    One MCP tool covers both "any incoming for product X?" (pass `product_ids`)
    and "what is arriving this month / from supplier Y?" (pass `eta_*` /
    `supplier_ids` / `shipment_ids`). Each shipment row carries its still-incoming
    product lines with per-warehouse allocations and the packing-list attachment.
    No aggregate totals - callers sum the line quantities themselves. At least one
    narrowing filter is required.
    """
    # product_ids may be UUIDs or product_codes, csv-joined or repeated; flatten
    # to individual tokens (the service resolves both UUID and code per token).
    flat_product_ids: list[str] = []
    for raw in product_ids or []:
        if raw is None:
            continue
        for piece in str(raw).split(","):
            piece = piece.strip()
            if piece:
                flat_product_ids.append(piece)
    try:
        contact = _Contact(db, contact_id, space_id)
        svc = IncomingStockService(db, regions=contact.regions)
        # product_ids may be UUIDs or product_codes; the service resolves both, so
        # pass through raw rather than via parse_uuid_list (which rejects codes).
        result, paged = _fetch_window(
            lambda p, n: svc.incoming_list(
                product_ids=flat_product_ids or None,
                shipment_ids=parse_uuid_list(shipment_ids, param_name="shipment_ids"),
                supplier_ids=parse_uuid_list(supplier_ids, param_name="supplier_ids"),
                query=query,
                eta_from=contact.eta_from(db, eta_from),
                eta_to=eta_to,
                page=p,
                limit=n,
            ),
            contact,
            eta_from=eta_from,
            eta_to=eta_to,
            page=page,
            limit=limit,
        )
        # Clearance dates are OMITTED, not nulled, for an unentitled caller: absent
        # means "you may not see this", null would mean "not reached yet", and an
        # LLM reading the response will narrate a null as the latter. The reason
        # rides along in a `field_access` block so the answer can say "I can't
        # share that" instead of inventing a status.
        #
        # Issue #1328: a contact's question also gets that contact's ETA offset and
        # packing list rule, before the field reveals (`_for_contact`).
        if contact.asked:
            return _for_contact(
                db,
                result,
                contact,
                current_user=current_user,
                eta_from=eta_from,
                eta_to=eta_to,
                paged=paged,
                asked=flat_product_ids,
            )
        return apply_field_access(
            db,
            result,
            resource="incoming_stock",
            current_user=current_user,
            contact_id=None,
            space_id=space_id,
            staff_permission=CLEARANCE_PERMISSION,
        )
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/shipments/{shipment_id}/products")
def get_incoming_shipment_products(
    shipment_id: str,
    contact_id: Optional[str] = Query(None, description=_CONTACT_ID_DOC),
    space_id: Optional[str] = Query(None, description=_SPACE_ID_DOC),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Answer 'what products are still incoming on this shipment?'.

    `shipment_id` accepts a UUID or any human-readable reference: shipment_number,
    shipping_container_number, bill_of_lading_number, invoice_number.
    """
    try:
        contact = _Contact(db, contact_id, space_id)
        svc = IncomingStockService(db, regions=contact.regions)
        return _for_contact(
            db,
            svc.shipment_incoming_products(shipment_id),
            contact,
            current_user=current_user,
        )
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/shipments/{shipment_id}/attachment")
def get_incoming_shipment_attachment(
    shipment_id: str,
    contact_id: Optional[str] = Query(None, description=_CONTACT_ID_DOC),
    space_id: Optional[str] = Query(None, description=_SPACE_ID_DOC),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Fetch the packing list / shipment document attachment for a shipment.

    Returns `{shipment_number, attachment: {filename, file_path, mime_type}}`, or the same
    shape with `attachment: null` when no file is linked. For a contact without the
    packing list permission (#1328) `attachment` is absent and the answer is empty.
    """
    try:
        contact = _Contact(db, contact_id, space_id)
        svc = IncomingStockService(db, regions=contact.regions)
        data = svc.shipment_attachment(shipment_id)
        if data is None:
            return {"data": None, "empty": True}
        gated = _for_contact(db, {"data": data}, contact, current_user=current_user)
        data = gated["data"]
        return {"data": data, "empty": data.get("attachment") is None}
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/grn")
def get_incoming_stock_grn(
    entities: Optional[list[str]] = Query(
        None,
        description="DEPRECATED - free-text entity bag. Prefer `shipment_ids` / `product_ids`.",
    ),
    shipment_ids: Optional[list[str]] = Query(
        None,
        description="Canonical inbound-shipment UUIDs (csv/JSON/repeated).",
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Canonical product UUIDs to narrow GRNs to lines referencing these products.",
    ),
    shipment_id: Optional[str] = Query(
        None,
        description="Legacy: shipment UUID or business reference.",
    ),
    product_id: Optional[str] = Query(
        None,
        description="Legacy: product UUID or product_code.",
    ),
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Surface GRN (goods received note) records only when the user explicitly asks.

    No contact in play and no region filter here: a future contact wiring must add both."""
    from app.services.entity_filter_helpers import (
        normalize_entities_query_param,
        resolve_or_empty,
    )
    from app.services.identifier_resolver import resolve_identifier
    from app.models.procurement import InboundShipment as _InboundShipment
    from app.models.product import Product as _Product

    entity_echo = None
    shipment_uuids: Optional[list[str]] = parse_uuid_list(shipment_ids, param_name="shipment_ids")
    product_uuids: Optional[list[str]] = parse_uuid_list(product_ids, param_name="product_ids")
    norm = normalize_entities_query_param(entities)
    if norm:
        buckets = resolve_or_empty(db, norm)
        if buckets is not None:
            entity_echo = buckets.as_echo()
            # Resolve ALL bucket entries to UUIDs (do not collapse to [0]). Merge
            # with explicit kwargs so callers can mix entities + typed UUIDs.
            if buckets.shipment_numbers:
                acc: list[str] = list(shipment_uuids or [])
                for code in buckets.shipment_numbers:
                    ids = resolve_identifier(
                        db,
                        code,
                        _InboundShipment,
                        code_fields=(
                            "shipment_number",
                            "shipping_container_number",
                            "bill_of_lading_number",
                            "invoice_number",
                        ),
                    )
                    if ids:
                        acc.extend(ids)
                shipment_uuids = list(dict.fromkeys(acc)) or None
            if buckets.product_codes:
                acc = list(product_uuids or [])
                for code in buckets.product_codes:
                    ids = resolve_identifier(
                        db, code, _Product, code_fields=("product_code",)
                    )
                    if ids:
                        acc.extend(ids)
                product_uuids = list(dict.fromkeys(acc)) or None
            if not shipment_uuids and not product_uuids and not shipment_id and not product_id:
                return {
                    "data": [],
                    "empty": True,
                    "message": "No product or shipment resolved from entities.",
                    "resolved_entities": entity_echo,
                }
    try:
        if not shipment_uuids and not product_uuids and not shipment_id and not product_id:
            return {
                "data": [],
                "empty": True,
                "message": "Provide entities or shipment_id or product_id.",
            }
        svc = IncomingStockService(db)
        result = svc.grn_records(
            shipment_id=shipment_id,
            product_id=product_id,
            shipment_uuids=shipment_uuids,
            product_uuids=product_uuids,
            limit=limit,
        )
        if entity_echo is not None and isinstance(result, dict):
            result["resolved_entities"] = entity_echo
        return result
    except Exception as e:
        raise handle_internal_error(str(e))
