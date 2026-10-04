"""The ETA a contact may be told, and whether the packing list may go with it (#1328).

One resolver for every route that prints a shipment ETA to a chat contact: the stock
ask (`StockService._apply_stock_visibility`) and the three incoming routes
(`/incoming-stock/list`, `/by-product`, `/shipments`). Before this, the stock ask
padded the ETA with the product-or-category `chatbot_eta_offset_days` and the incoming
routes printed the exact date, so one shipment had two answers.

**The switch** is per contact: `respond_contacts.chatbot_eta_offset_applied`, beside
`notify_salesman` / `packing_list_allowed` and edited on the same Access > Chatbot card.
ON (the default, and the stock ask's behaviour before the switch existed) = the shipment
ETA plus the offset; OFF = the exact date.

**The offset** is `stock_ask_limits.effective`'s rule, unchanged: the product's own value,
else its own category's, else 0.

**No contact in play** (a staff session, the raw API key) = the exact date and the
attachment as today: these rules govern what a CONTACT is told. A contact that names
nobody we can find is told the padded date and gets no packing list - the default a
contact row would carry, and never an earlier promise than the one they would get.

**A shipment row names several products.** The incoming list is shipment-rooted, so its
ETA is one date for every line on it. It is padded by the LARGEST offset among the row's
own products: asked about one product, the lines are that product's alone and the date is
the stock ask's date exactly; asked about a supplier or a date window, the contact is never
promised the container sooner than any product on it would be.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.services.stock_ask_limits import NULL_LIMITS, effective as effective_limits

#: The date keys an incoming payload carries that ARE the shipment's arrival promise.
#: `eta_delay_date` is the revised ETA, so it is padded the same way; every other
#: checkpoint (gatepass, warehouse arrival, collection ...) is a recorded event, not a
#: promise, and prints as recorded.
ETA_KEYS: tuple[str, ...] = (
    "estimated_arrival_date",
    "eta_delay_date",
    "nearest_estimated_arrival_date",
)


@dataclass(frozen=True)
class ContactEtaRules:
    """What one contact's chat answers may carry about a shipment."""

    #: Pad the ETA with the product-or-category offset.
    offset_applied: bool
    #: Attach the shipment's packing list.
    packing_list_allowed: bool
    #: The packing list regions this contact may be told about (already expanded).
    regions: frozenset[str] = frozenset({"west"})


#: An unresolved contact: the switch's own default for the ETA, and no file.
UNRESOLVED = ContactEtaRules(
    offset_applied=True, packing_list_allowed=False, regions=frozenset({"west"})
)


def visible_regions(held: Iterable[str]) -> frozenset[str]:
    """Expand a contact's held regions: `east` held means {east, west}, else {west}."""
    return frozenset({"east", "west"}) if "east" in set(held or ()) else frozenset({"west"})


def contact_regions(db: Session, resolved_contact_id: Optional[str]) -> frozenset[str]:
    """The packing list regions this contact may be told about: West only for every contact.

    Another lane (ACCESS-MODEL) repoints this body at `effective_access(...).regions`;
    the signature must stay.
    """
    return frozenset({"west"})


def rules_for_contact(db: Session, resolved_contact_id: Optional[str]) -> ContactEtaRules:
    """The contact's two switches, read once. Takes the INTERNAL `respond_contacts.id`."""
    if not resolved_contact_id:
        return UNRESOLVED
    from app.models.access import RespondContact

    regions = visible_regions(contact_regions(db, resolved_contact_id))

    row = (
        db.query(RespondContact.chatbot_eta_offset_applied, RespondContact.packing_list_allowed)
        .filter(RespondContact.id == str(resolved_contact_id))
        .first()
    )
    if row is None:
        return UNRESOLVED
    return ContactEtaRules(
        # NOT NULL default true; `is not False` keeps the default if it ever read NULL.
        offset_applied=row[0] is not False,
        # NOT NULL default false; `is True` keeps the fail-closed reading.
        packing_list_allowed=row[1] is True,
        regions=regions,
    )


def offset_days(product: Any, category: Any) -> int:
    """The product's `chatbot_eta_offset_days`, else its own category's, else 0."""
    return effective_limits(product, category)[1]


def visible_eta(eta: Optional[date], offset: int, rules: ContactEtaRules) -> Optional[date]:
    """The ETA this contact may be told: `eta + offset` when their switch is on, else `eta`."""
    if eta is None or not rules.offset_applied or not offset:
        return eta
    return eta + timedelta(days=offset)


# --------------------------------------------------------------- incoming payloads


def _offsets_by_product_code(db: Session, codes: Iterable[str]) -> dict[str, int]:
    """`{lower(trimmed product_code): offset}`. A code carried by two companies' products
    takes the larger of their offsets - the same rule a multi-product row follows."""
    from sqlalchemy import func

    from app.models.product import Product, ProductCategory

    wanted = {c.strip().lower() for c in codes if isinstance(c, str) and c.strip()}
    if not wanted:
        return {}
    rows = (
        db.query(Product, ProductCategory)
        .outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
        .filter(func.lower(func.trim(Product.product_code)).in_(wanted))
        .all()
    )
    out: dict[str, int] = {}
    for product, category in rows:
        y = offset_days(product, category if category is not None else NULL_LIMITS)
        key = product.product_code.strip().lower()
        out[key] = max(out.get(key, 0), y)
    return out


def _offsets_by_shipment(
    db: Session, rows: Iterable[dict[str, Any]]
) -> dict[tuple[Optional[str], str], int]:
    """`{(company_id, shipment_number): offset}` for rows that name no product (`/shipments`),
    over each shipment's STILL-INCOMING lines - the same lines `/list` answers with.
    Shipment numbers are unique per company only, so the company is part of the key;
    `(None, number)` carries the largest across companies for a row with no company."""
    from app.models.procurement import InboundShipment, InboundShipmentLine
    from app.models.product import Product
    from app.services.incoming_stock_service import _still_incoming_filter

    wanted = {r.get("shipment_number") for r in rows}
    wanted = {n for n in wanted if isinstance(n, str) and n}
    if not wanted:
        return {}
    triples = (
        db.query(InboundShipment.company_id, InboundShipment.shipment_number, Product.product_code)
        .join(InboundShipmentLine, InboundShipmentLine.shipment_id == InboundShipment.id)
        .join(Product, Product.id == InboundShipmentLine.product_id)
        .filter(InboundShipment.shipment_number.in_(wanted), _still_incoming_filter())
        .distinct()
        .all()
    )
    by_code = _offsets_by_product_code(db, {code for _, _, code in triples if code})
    out: dict[tuple[Optional[str], str], int] = {}
    for company_id, number, code in triples:
        y = by_code.get((code or "").strip().lower(), 0)
        for key in ((str(company_id) if company_id else None, number), (None, number)):
            out[key] = max(out.get(key, 0), y)
    return out


def _codes_of(row: dict[str, Any]) -> list[str]:
    # `/list` rows carry `lines`, `/shipments/{id}/products` carries `products`.
    for key in ("lines", "products"):
        if isinstance(row.get(key), list):
            return [line.get("product_code") for line in row[key] if isinstance(line, dict)]
    if row.get("product_code"):
        return [row["product_code"]]
    return []


def _pad(node: dict[str, Any], offset: int, rules: ContactEtaRules) -> None:
    for key in ETA_KEYS:
        value = node.get(key)
        if isinstance(value, date):
            node[key] = visible_eta(value, offset, rules)


def _in_window(value: Any, eta_from: Optional[date], eta_to: Optional[date]) -> bool:
    if not isinstance(value, date):
        return eta_from is None and eta_to is None
    return (eta_from is None or value >= eta_from) and (eta_to is None or value <= eta_to)


# --------------------------------------------------------------- the request


def resolve_request_contact(
    db: Session, contact_id: Optional[str], space_id: Optional[str]
) -> Optional[str]:
    """The internal `respond_contacts.id` a chat request names, resolved ONCE for both the
    ETA/packing-list rules and the field reveals, so the two gates can never disagree.

    Uses the same NULL-workspace fallback the chatbot's own access check admits a contact
    through (`field_access.resolve_contact_with_null_workspace_fallback`): a contact the
    chatbot answers must not read as nobody on the data route. Unresolved = None, which
    both gates treat fail-closed."""
    if not contact_id:
        return None
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    return resolve_contact_with_null_workspace_fallback(
        db, contact_id=contact_id, space_id=space_id
    )


def max_offset_days(db: Session) -> int:
    """The largest offset any product or category carries - how far back a contact's ETA
    window must reach so a shipment whose PADDED date falls inside it is not filtered out
    on its real date."""
    from sqlalchemy import func

    from app.models.product import Product, ProductCategory

    product_max = db.query(func.max(Product.chatbot_eta_offset_days)).scalar() or 0
    category_max = db.query(func.max(ProductCategory.chatbot_eta_offset_days)).scalar() or 0
    return max(int(product_max), int(category_max), 0)


def query_eta_from(db: Session, rules: Optional[ContactEtaRules], eta_from: Optional[date]) -> Optional[date]:
    """The `eta_from` the SQL filter should use. A padded date is never earlier than the
    real one, so only the lower bound widens; `apply_to_incoming` then filters on the
    padded date, so the window a contact asks about is judged on the date they are told."""
    if eta_from is None or rules is None or not rules.offset_applied:
        return eta_from
    return eta_from - timedelta(days=max_offset_days(db))


def apply_to_incoming(
    db: Session,
    payload: Any,
    rules: ContactEtaRules,
    *,
    eta_from: Optional[date] = None,
    eta_to: Optional[date] = None,
) -> Any:
    """Apply one contact's ETA offset and packing-list rule to an incoming payload.

    Handles every incoming shape: `/list` (shipment rows with `lines`), `/by-product`
    (product rows with `shipments`), `/shipments` (bare shipment rows), and the single-row
    `/shipments/{id}/products` and `/shipments/{id}/attachment` (a dict, not a list).
    With the offset applied and a window given, rows are re-judged on the padded date
    (the SQL window was widened by `query_eta_from`). Runs BEFORE
    `field_access.apply_field_access`; the two are independent.
    """
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        return payload

    by_code: dict[str, int] = {}
    by_shipment: dict[tuple[Optional[str], str], int] = {}
    unnumbered = 0
    if rules.offset_applied:
        by_code = _offsets_by_product_code(
            db, [c for row in rows if isinstance(row, dict) for c in _codes_of(row)]
        )
        bare = [
            row
            for row in rows
            if isinstance(row, dict) and not _codes_of(row) and "shipments" not in row
        ]
        by_shipment = _offsets_by_shipment(db, bare)
        if any(not row.get("shipment_number") for row in bare):
            # No number to find its lines by: pad by the largest offset there is, so the
            # contact is never promised it sooner than any product could be.
            unnumbered = max_offset_days(db)

    windowed = rules.offset_applied and (eta_from is not None or eta_to is not None)
    kept: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        codes = _codes_of(row)
        if codes:
            offset = max((by_code.get((c or "").strip().lower(), 0) for c in codes), default=0)
        elif not row.get("shipment_number"):
            offset = unnumbered
        else:
            offset = by_shipment.get(
                (row.get("company_id"), row["shipment_number"]),
                by_shipment.get((None, row["shipment_number"]), 0),
            )
        _pad(row, offset, rules)
        if not rules.packing_list_allowed:
            row.pop("attachment", None)
        if isinstance(row.get("shipments"), list):
            shipments = []
            for shipment in row["shipments"]:
                if not isinstance(shipment, dict):
                    continue
                _pad(shipment, offset, rules)
                if not rules.packing_list_allowed:
                    shipment.pop("attachment", None)
                if not windowed or _in_window(
                    shipment.get("estimated_arrival_date"), eta_from, eta_to
                ):
                    shipments.append(shipment)
            row["shipments"] = shipments
            if windowed:
                etas = [s.get("estimated_arrival_date") for s in shipments]
                etas = [e for e in etas if isinstance(e, date)]
                row["nearest_estimated_arrival_date"] = min(etas) if etas else None
                if not shipments:
                    continue
        elif windowed and not _in_window(row.get("estimated_arrival_date"), eta_from, eta_to):
            continue
        kept.append(row)

    if isinstance(data, list) and rules.offset_applied:
        # The service orders shipment rows on the REAL ETA; two offsets can swap them,
        # so re-order on the date the contact reads (no ETA last, ties keep their order).
        if all(isinstance(r, dict) and "shipments" not in r for r in kept):
            kept.sort(
                key=lambda r: (
                    not isinstance(r.get("estimated_arrival_date"), date),
                    r.get("estimated_arrival_date") or date.max,
                )
            )
        payload["data"] = kept
    if isinstance(data, list) and len(kept) != len(rows):
        pagination = payload.get("pagination")
        if isinstance(pagination, dict) and isinstance(pagination.get("total"), int):
            pagination["total"] = max(0, pagination["total"] - (len(rows) - len(kept)))
        if not kept:
            payload["empty"] = True
    return payload


# --------------------------------------------------------------- the dealer view
#
# PR #1329 fix round (owner hand test, 28 Sep 2026: "it should just list deduped ETAs,
# and say please refer to sales person"). A dealer - a contact whose stock visibility
# policy is "Availability only", the same test the chatbot's own profile applies
# (`turn_runtime._stock_availability_only`) - is told, per product, the code once and
# its distinct ETAs, sorted, and who to ask. Nothing else: no container, no quantity, no
# allocation, no packing list. Two shipment lines with one ETA are one date, which is
# also what printed the owner's product twice (two still-incoming lines on one ETA,
# told apart only by the container and quantities the contact is not shown).


def is_dealer(db: Session, resolved_contact_id: Optional[str]) -> bool:
    """Is this contact on the "Availability only" stock policy? Unresolved = no."""
    if not resolved_contact_id:
        return False
    from app.services.stock_visibility import resolve_policy

    policy = resolve_policy(db, resolved_contact_id)
    return policy is not None and policy.mode == "availability"


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return None


def _told(node: dict[str, Any]) -> Optional[str]:
    # The revised ETA, when the contact may see one, is the date the shipment is now
    # promised for; else the ETA itself. Both are already padded (`apply_to_incoming`).
    return _iso(node.get("eta_delay_date")) or _iso(node.get("estimated_arrival_date"))


def _ddmmyyyy(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def dealer_view(payload: Any, asked: Optional[list[str]] = None) -> Any:
    """One row per product - `{"product_code", "etas"}`, the ETAs distinct, sorted and
    told as dd/mm/yyyy (AVAIL-MODE-REPLIES, the stock ask's own ETA format) - in the
    order the products first appear, plus `dealer_view`. No salesperson name: the
    presenter closes with the one refer sentence (REFER-SALESMAN, 30 Sep 2026).
    Handles `/list` (shipment rows with `lines`), `/by-product` (product rows with
    `shipments`) and `/shipments` (no product: one row, code None). A single-row payload
    (`/shipments/{id}/...`) is not a chat answer and passes through.

    `asked`: the product codes the dealer asked about. One with no shipment is still a
    row, with no ETA ("CODE: No ETA", owner hand test 3 Oct 2026), never a miss."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return payload
    etas: dict[Optional[str], set[str]] = {}
    for row in payload["data"]:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("shipments"), list):
            pairs = [(row.get("product_code"), _told(s)) for s in row["shipments"] if isinstance(s, dict)]
        elif isinstance(row.get("lines"), list):
            pairs = [(line.get("product_code"), _told(row)) for line in row["lines"] if isinstance(line, dict)]
        else:
            pairs = [(row.get("product_code"), _told(row))]
        for code, eta in pairs:
            dates = etas.setdefault(code, set())
            if eta:
                dates.add(eta)
    seen = {str(code).casefold() for code in etas if code}
    for code in asked or []:
        if code and str(code).casefold() not in seen:
            seen.add(str(code).casefold())
            etas[code] = set()
    rows = [
        {"product_code": code, "etas": [_ddmmyyyy(d) for d in sorted(dates)]}
        for code, dates in etas.items()
    ]
    out = {
        key: payload[key]
        for key in ("resolved_entities", "lookup_companies")
        if key in payload
    }
    out.update(
        {
            "data": rows,
            "empty": not rows,
            "pagination": {"total": len(rows), "page": 1, "limit": max(len(rows), 1)},
            "dealer_view": True,
        }
    )
    return out
