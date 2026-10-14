"""What a price tag is ABOUT: the product behind a bound layer, resolved live.

The canvas edits shapes. This module is where those shapes learn what they are
showing - the code, the name, the dimensions, the spec lines, the photos and the
two prices - so a tag can be designed against the real catalogue rather than
against placeholder text.

Everything here is a READ, resolved at the moment it is asked for. Nothing it
returns is ever written into a saved document (ADR 0008): the document holds a
binding and any text a designer typed over it, and this module answers the
binding again on every open, every preview and every render. That is what makes
a promotion ending overnight change the PDF rather than leave a stale price on a
tag somebody prints next week.

Three rules it does not get to decide for itself:

* **Prices come from ``resolve_prices``.** The promotion window, the audience
  gate and "is this offer worth showing" all live there, and a second copy of
  them beside the tag code would be a second commercial answer.
* **Photos come from ``product_images``.** Trade imagery is tagged ``dealer``,
  and the tag designer is the same gate as the catalogue tile.
* **Set prices come from ``resolve_set_price``.** Ticking members IS the
  formula, and the price on a furniture set tag has to agree with the price on
  the set's own detail page.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.product import Product
from app.models.product_set import ProductSet, ProductSetMember
from app.utils.product_display import distinct_name
from app.services.dealer_kit.pricing import DEFAULT_CURRENCY, resolve_prices
from app.services.dealer_kit.product_images import gallery_images
from app.services.dealer_kit.viewer import ViewerContext
from app.services.product_spec_registry import display_spec_value

logger = logging.getLogger(__name__)

# The editor and the tag sheet designer are STAFF surfaces: marketing is
# designing a tag they are about to send to a printer, so they see the offer the
# tag will print. `is_internal_copy` is what says "this render is the brochure's
# own copy", which is exactly what a proof is.
STAFF_VIEWER = ViewerContext(
    is_staff=True,
    access_codes=frozenset(),
    show_invoice_price=False,
    is_internal_copy=True,
)


def staff_viewer() -> ViewerContext:
    """The viewer a CRM-side tag surface resolves prices and photos for."""
    return STAFF_VIEWER


def contact_viewer(db: Session, contact_id: str) -> ViewerContext:
    """A portal contact's own audience, as a pricing/promotion viewer (D4).

    Every line-level promotion check that reaches a portal contact's own
    request - create, update, revise, the line-pricing lookup - reads THIS,
    never a bare pass-through of ``access_codes`` built ad hoc per call site
    (or, worse, ``staff_viewer()``, which is ``is_internal_copy=True`` and so
    never gates on audience at all). One place answers "what can this
    contact see", matching the rule the old ``lookup_promotions`` audience
    gate followed.
    """
    from app.services.contact_access_type_service import ContactAccessTypeService

    codes = ContactAccessTypeService(db).get_contact_access_codes(contact_id)
    return ViewerContext(access_codes=frozenset(codes))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_dimensions_mm(length, width, height) -> Optional[str]:
    """``800 x 500 x 220 mm``, or nothing when no dimension is recorded.

    One rule, shared with the catalogue tile (``collection_service._dimensions``
    delegates here). Two formatters would eventually print a tag and its tile
    with different punctuation for the same product.

    A missing single dimension prints as ``-`` rather than collapsing the
    string: "800 x - x 220" says which measurement is missing, "800 x 220" is a
    lie about which is which.

    Trailing zeros go. ``dimensions_length`` is ``NUMERIC(10,2)``, so a row read
    back from the database is ``Decimal("800.00")`` and Decimal's ``g`` format
    keeps the scale it was stored at - which printed "800.00 x 500.00 x 220.00
    mm" on a tag whose flyer says "800 x 500 x 220 mm". ``normalize()`` drops the
    stored scale without touching a genuine fraction, so 12.50 still prints as
    12.5.

    ``Decimal(str(part))``, never ``Decimal(part)``: a measurement that comes
    from the reviewed spec row arrives as a JSON number, which is a Python
    FLOAT, and ``Decimal(407.3)`` is 407.2999999999999886313162278383970260620
    exactly. Printed, that is 28 digits of binary noise on a physical tag, and
    the spec branch wins over the master columns so it is the branch a real
    product with a fractional measurement takes. Going through ``str`` keeps the
    number that was measured.
    """
    parts = [length, width, height]
    if not any(part is not None for part in parts):
        return None
    return (
        " x ".join(
            "-" if part is None else f"{Decimal(str(part)).normalize():f}"
            for part in parts
        )
        + " mm"
    )


def _clean_lines(values: Iterable) -> list[str]:
    """Non-empty, stripped lines. Blank entries are noise on a printed tag."""
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_products(
    db: Session, query: Optional[str], limit: int = 20
) -> list[Product]:
    """Products for the editor's picker, searched on the server.

    The same shape as the master-data product select (code or name, active only,
    ordered by code) rather than a new index: 22,000 products is far past what a
    client-side filter can see, and a picker that silently hides most of the
    catalogue is worse than a slow one.
    """
    statement = db.query(Product).filter(Product.is_active.is_(True))

    if query and query.strip():
        needle = f"%{query.strip()}%"
        statement = statement.filter(
            or_(
                Product.product_code.ilike(needle),
                Product.product_name.ilike(needle),
            )
        )

    return statement.order_by(Product.product_code).limit(limit).all()


def search_product_sets(
    db: Session, query: Optional[str], limit: int = 20
) -> list[ProductSet]:
    """Product sets for the editor's picker."""
    statement = db.query(ProductSet).filter(ProductSet.is_active.is_(True))

    if query and query.strip():
        needle = f"%{query.strip()}%"
        statement = statement.filter(
            or_(
                ProductSet.set_code.ilike(needle),
                ProductSet.name.ilike(needle),
            )
        )

    return statement.order_by(ProductSet.set_code).limit(limit).all()


def get_product(db: Session, product_id: str) -> Optional[Product]:
    return db.query(Product).filter(Product.id == product_id).first()


def get_product_set(db: Session, set_id: str) -> Optional[ProductSet]:
    return (
        db.query(ProductSet)
        .options(joinedload(ProductSet.members).joinedload(ProductSetMember.product))
        .filter(ProductSet.id == set_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Spec lines and dimensions
# ---------------------------------------------------------------------------

# The registry's keys for the three printed measurements.
_SPEC_DIMENSION_KEYS = ("dim_length", "dim_width", "dim_height")


def _spec_row(db: Session, product: Product):
    from app.models.product_spec import ProductSpecifications

    return (
        db.query(ProductSpecifications)
        .filter(ProductSpecifications.product_id == product.id)
        .first()
    )


def _flyer_lines(db: Session, product: Product) -> list[str]:
    from app.models.product_spec import ProductFlyerText

    row = (
        db.query(ProductFlyerText)
        .filter(ProductFlyerText.product_code == product.product_code)
        .first()
    )
    if row is None:
        return []
    lines = row.lines if isinstance(row.lines, list) else []
    return _clean_lines(lines)


def spec_lines(db: Session, product: Product, spec_row=None) -> list[str]:
    """What the tag prints under the product name.

    Three sources in order of authority (D27): the derived spec sentence, then
    the printed flyer's own lines, then the product description. The description
    is a real fallback rather than a placeholder - most of the catalogue has no
    derived specs, and a tag with nothing under the name is a tag marketing has
    to type by hand.
    """
    row = spec_row if spec_row is not None else _spec_row(db, product)
    if row is not None and (row.rendered_text or "").strip():
        return _clean_lines(row.rendered_text.splitlines())

    flyer = _flyer_lines(db, product)
    if flyer:
        return flyer

    return _clean_lines((product.description or "").splitlines())


def _spec_display_value(raw, value_labels: Optional[dict] = None) -> str:
    """One reviewed spec value, as a person reads it on a tag (S3): the registry's own
    `display_spec_value` in title case (`stainless_steel` -> `Stainless Steel`, `pvc_pipe`
    -> `PVC Pipe`), so a tag and the chatbot read a value by one set of rules (PR #833
    round 5 S2)."""
    return display_spec_value(raw, value_labels, title_case=True)


def product_specs(db: Session, product: Product, spec_row=None) -> list[dict]:
    """The product's reviewed specs, key by key, for `{{spec.<key>}}` (D58).

    `spec_lines` has always carried the rendered spec SENTENCE, which is one
    block of text: a tag could print all of it or none of it. A merge field
    asks for one value, so this joins the two halves that answer that - the
    registry, which says which keys exist and what each is called, and the
    product's reviewed row, which says which of them this product carries.

    Only keys the product actually has are returned, so a token naming
    anything else resolves to nothing rather than to an empty labelled row.
    The unit comes from the REGISTRY rather than from the stored value: the
    registry is what the master-data screen edits, and a row written before a
    unit was set would otherwise print without one forever.
    """
    from app.services.product_spec_registry import active_registry

    row = spec_row if spec_row is not None else _spec_row(db, product)
    values = row.values if row is not None and isinstance(row.values, dict) else {}
    if not values:
        return []

    specs: list[dict] = []
    for key in active_registry(db):
        if key.spec_key not in values:
            continue
        stored = values[key.spec_key]
        # A value is stored bare or wrapped as {"value": ...}; `dimensions_text`
        # already reads both, and the catalogue holds both shapes.
        raw = stored.get("value") if isinstance(stored, dict) else stored
        if raw is None:
            continue
        specs.append(
            {
                "key": key.spec_key,
                "label": key.label,
                "value": _spec_display_value(raw, key.value_labels),
                "unit": key.unit,
            }
        )
    return specs


def dimensions_text(product: Product, spec_row=None) -> str:
    """Dimensions from the reviewed spec values, else from the product master.

    The spec values win because they are what somebody checked against the
    printed flyer; the master columns are an import and are wrong often enough
    that the flyer-reading slice exists to correct them.
    """
    if spec_row is not None and isinstance(spec_row.values, dict):
        measured = [
            (spec_row.values.get(key) or {}).get("value")
            if isinstance(spec_row.values.get(key), dict)
            else spec_row.values.get(key)
            for key in _SPEC_DIMENSION_KEYS
        ]
        if any(value is not None for value in measured):
            return format_dimensions_mm(*measured) or ""

    return (
        format_dimensions_mm(
            product.dimensions_length,
            product.dimensions_width,
            product.dimensions_height,
        )
        or ""
    )


# ---------------------------------------------------------------------------
# Tag data
# ---------------------------------------------------------------------------


def product_tag_data(
    db: Session,
    product: Product,
    viewer: ViewerContext,
    promotion_id: Optional[str] = None,
    *,
    with_images: bool = True,
) -> dict:
    """Everything a product block draws, for THIS viewer, right now."""
    spec_row = _spec_row(db, product)
    prices = resolve_prices(db, [product], viewer, promotion_id).get(product.id)

    return {
        "id": product.id,
        "code": product.product_code or "",
        "name": product.product_name or "",
        "dimensions": dimensions_text(product, spec_row),
        "spec_lines": spec_lines(db, product, spec_row),
        # Key by key, beside the rendered sentence: `{{spec.material}}` asks a
        # question `spec_lines` cannot answer (D58).
        "specs": product_specs(db, product, spec_row),
        # S4: staff-authored tag copy. Absent (None) renders nothing (Q5) -
        # no fallback to spec_lines/description.
        "price_tag_description": product.price_tag_description or None,
        "images": gallery_images(db, product, viewer) if with_images else [],
        "list_price": prices.list_price if prices else None,
        "offer_price": prices.offer_price if prices else None,
        "promotion_id": prices.promotion_id if prices else None,
        # PLAN D14 (price-tag-feedback-r2): CRM-owned, manual entry or a
        # non-empty AutoCount sync. Null renders an editor placeholder and
        # nothing on print (S7).
        "barcode": product.barcode or None,
        # AC-A10: the product's own currency column, defaulted the same way
        # `resolve_prices` already defaults it - a text layer prints the bare
        # figure now (Slice A), so the currency has to travel as its own field.
        "currency": prices.currency if prices else DEFAULT_CURRENCY,
    }


def _member_products(product_set: ProductSet) -> list[ProductSetMember]:
    return sorted(
        list(product_set.members or []), key=lambda m: (m.sort_order or 0, m.id)
    )


def product_set_tag_data(
    db: Session,
    product_set: ProductSet,
    viewer: ViewerContext,
    promotion_id: Optional[str] = None,
) -> dict:
    """The set's members, and what the set costs.

    The list price is the set's OWN rule (``resolve_set_price``): the ticked
    members, times their quantities, or the override somebody typed. The offer
    is the same sum with each ticked member at its promotional price where one
    applies - and absent entirely when no member is on offer, so a set with
    nothing promoted prints a plain list price rather than a discount of zero.
    """
    from app.services.product_set_service import resolve_set_price

    members = _member_products(product_set)
    member_products = [m.product for m in members if m.product is not None]

    prices = (
        resolve_prices(db, member_products, viewer, promotion_id)
        if member_products
        else {}
    )

    set_price = resolve_set_price(product_set)

    # AC-A11: the first member's currency, off the SAME resolved prices this
    # function already fetched - not a second read of the member's product row.
    set_currency = DEFAULT_CURRENCY
    for product in member_products:
        view = prices.get(product.id)
        if view is not None:
            set_currency = view.currency
            break

    offer_total = Decimal("0")
    any_offer = False
    # A member the pricing engine cannot price is not worth nothing, and the sum
    # used to add it as RM 0: a three-piece set with one unpriced member printed
    # a set offer far below the sum of its parts, which is a discount nobody
    # authorised, on paper, in a dealer's hands. Zero IS the unpriced case here -
    # `products.list_price` is NOT NULL and defaults to nought, so
    # `_a_real_price` answers None for it. When that happens the offer is
    # abandoned entirely and the tag prints the set's list price, which is the
    # set's own rule and is always true.
    every_member_priced = True
    for member in members:
        if not member.contributes_to_price or member.product is None:
            continue
        view = prices.get(member.product.id)
        unit = None
        if view is not None:
            if view.offer_price is not None:
                any_offer = True
                unit = view.offer_price
            else:
                unit = view.list_price
        if unit is None:
            every_member_priced = False
            continue
        quantity = Decimal(str(member.quantity)) if member.quantity is not None else Decimal("1")
        offer_total += unit * quantity

    return {
        "id": product_set.id,
        "set_code": product_set.set_code or "",
        "name": product_set.name or "",
        "members": [
            {
                "product_id": member.product_id,
                "code": (member.product.product_code or "") if member.product else "",
                "name": (member.product.product_name or "") if member.product else "",
                "dimensions": dimensions_text(member.product) if member.product else "",
                "quantity": member.quantity,
            }
            for member in members
        ],
        "list_price": set_price.resolved,
        "offer_price": (
            offer_total.quantize(Decimal("0.01"))
            if any_offer and every_member_priced
            else None
        ),
        "promotion_id": promotion_id if (any_offer and every_member_priced) else None,
        "currency": set_currency,
    }


# ---------------------------------------------------------------------------
# Request lines
# ---------------------------------------------------------------------------


def _set_member_text(members: Sequence[dict]) -> str:
    """``- CODE (NAME) 800 x 500 x 220 mm`` per member (AC-L.4); ``(NAME)`` only when it differs from the code."""
    lines = []
    for member in members:
        head = f"- {member['code']}"
        shown = distinct_name(member["code"], member.get("name"))
        if shown:
            head += f" ({shown})"
        if member.get("dimensions"):
            head += f" {member['dimensions']}"
        lines.append(head)
    return "\n".join(lines)


#: What a money figure is rounded to before it leaves the resolver. The engine
#: already works in Decimal; this is only so a SUM of four products cannot print
#: a third decimal place the customer would have to squint at.
_MONEY = Decimal("0.01")


def _sum_prices(values: list) -> Optional[Decimal]:
    """The package's price: the host plus its resolved parts (D4).

    None when nothing in the list has a price at all, so a product with no list
    price still prints an empty slot rather than a hard zero. An individual part
    the engine cannot price contributes nothing rather than aborting the sum -
    the rest of the package is still worth showing.
    """
    present = [Decimal(str(value)) for value in values if value is not None]
    if not present:
        return None
    return sum(present, Decimal("0")).quantize(_MONEY)


def _offer_or_list(price) -> Optional[Decimal]:
    """What one part contributes to the SELLING sum.

    The engine's offer when it has one, the list price when it does not. A part
    with no promotion line is not free and is not discounted; it is simply
    itself.
    """
    if price is None:
        return None
    return price.offer_price if price.offer_price is not None else price.list_price


def _tag_label(line_index: int, tag_index: int) -> str:
    """"1a", "1b", ... - the line's position plus a letter (D3).

    An ordinal, never an id (AC-X-2). Past 26 tags on one line the letter wraps
    and a number follows it ("1a1"), which nobody will ever see but which keeps
    the labels unique rather than silently repeating.
    """
    letter = chr(ord("a") + (tag_index % 26))
    wrap = tag_index // 26
    return f"{line_index + 1}{letter}{wrap if wrap else ''}"


def _open_groups_for(db: Session, line, tag) -> list[dict]:
    """The choice groups this TAG has not resolved yet (D3).

    A group is open when the line left it open (a part row with candidates and
    no product) AND this tag has made no choice for it. Split and Pick one both
    write `tag.choices`, so a tag that has answered simply reports nothing.

    Candidates carry the product id beside the code: "Pick one" has to NAME the
    candidate back to `PATCH .../tags/{tag_id}`, whose `choices` is
    `{role: product_id}`, and only the code is ever rendered.
    """
    from app.models.product import Product

    chosen = dict(tag.choices or {})
    groups: list[dict] = []
    for part in sorted(line.parts or [], key=lambda p: (p.sort_order or 0, p.id)):
        if part.product_id or not part.candidates:
            continue
        role = part.role or ""
        if role in chosen:
            continue
        products = {
            product.id: product
            for product in db.query(Product)
            .filter(Product.id.in_([str(c) for c in part.candidates]))
            .all()
        }
        groups.append(
            {
                "role": role,
                "candidates": [
                    {
                        "product_id": str(candidate),
                        "code": (
                            products[str(candidate)].product_code
                            if str(candidate) in products
                            else ""
                        ),
                    }
                    for candidate in part.candidates
                ],
            }
        )
    return groups


def _resolved_part_products(db: Session, line, tag) -> list:
    """The PRODUCTS printed under the host on this tag, in part order (D3/D4).

    The line's resolved part rows, plus whatever this tag chose for a group the
    line left open - so two tags split off one line list the same fixed parts
    and a different basin. The product rows rather than a rendered dict, because
    D4 needs to price them as well as print them, and asking the database twice
    for the same four products would be two round trips for one answer.
    """
    from app.models.product import Product

    chosen = dict(tag.choices or {})
    wanted: list[str] = []
    for part in sorted(line.parts or [], key=lambda p: (p.sort_order or 0, p.id)):
        if part.product_id:
            wanted.append(str(part.product_id))
        elif (part.role or "") in chosen:
            wanted.append(str(chosen[part.role or ""]))
    if not wanted:
        return []

    products = {
        product.id: product
        for product in db.query(Product).filter(Product.id.in_(wanted)).all()
    }
    return [products[pid] for pid in wanted if pid in products]


def _combo_products(
    db: Session,
    line,
    tag,
    viewer: ViewerContext,
    promotion_id,
    cache: dict,
    *,
    price_mode: str = "selling",
) -> list[dict]:
    """Every product a slot on this tag may point at (S6, AC-S6-3/S6-4).

    The line's fixed parts plus EVERY candidate of every open choice group.
    A superset of `_resolved_part_products`, which only carries the ONE
    candidate this tag chose. Each row is a `_part_row` (the same resolver a
    fixed part already goes through) plus `role` (the group's label, absent
    on a fixed part) and `chosen` (true on the candidate THIS tag's own
    `choices` names), so the subject picker can group candidates under their
    role and mark this tag's own pick.

    AC-S6-13: the ORDER is the pre-r10 narrow order first - this tag's own
    fixed parts and its chosen candidate, exactly the order
    `_resolved_part_products` produces - with the non-chosen candidates of
    every open group appended at the end, in combo order. A slot index a
    template already saved (D3/D4) must keep pointing at the same product
    after r10 widens `parts`; interleaving a leftover candidate at the
    group's own sort position would shift every index after it.
    """
    from app.models.product import Product

    chosen = dict(tag.choices or {})
    narrow: list[tuple[str, Optional[str], bool]] = []
    leftovers: list[tuple[str, Optional[str], bool]] = []
    for part in sorted(line.parts or [], key=lambda p: (p.sort_order or 0, p.id)):
        if part.product_id:
            narrow.append((str(part.product_id), None, False))
            continue
        role = part.role or ""
        chosen_id = chosen.get(role)
        for candidate in part.candidates or []:
            candidate_id = str(candidate)
            entry = (candidate_id, role, candidate_id == str(chosen_id))
            (narrow if entry[2] else leftovers).append(entry)
    wanted = narrow + leftovers
    if not wanted:
        return []

    products = {
        product.id: product
        for product in db.query(Product)
        .filter(Product.id.in_({product_id for product_id, _, _ in wanted}))
        .all()
    }
    rows: list[dict] = []
    for product_id, role, chosen_flag in wanted:
        product = products.get(product_id)
        if product is None:
            continue
        row = dict(
            _part_row(db, product, viewer, promotion_id, cache, price_mode=price_mode)
        )
        row["role"] = role
        row["chosen"] = chosen_flag
        rows.append(row)
    return rows


def _part_row(
    db: Session,
    product,
    viewer: ViewerContext,
    promotion_id,
    cache: dict,
    *,
    price_mode: str = "selling",
) -> dict:
    """One part as a first-class product (D7/AC-S9-1) - a layer may pick ANY
    part as its subject, so a part needs everything the host already carries:
    images, specs, barcode, both prices.

    Resolved through ``product_tag_data``, the SAME resolver the host goes
    through (via ``_line_product_data``), one read per DISTINCT product on
    the line - ``cache`` is that line's own dict, shared across every tag the
    line's parts get read for, so two tags split off one line do not each
    pay for the same photo/spec read.

    ``sell_price`` is the offer under the line's promotion, or ``None``
    (AC-S9-1) - never a fall back to list, unlike the LINE's own total: a
    part printing at list beside its own code is not "on sale", the tag's
    box total is what decides that. R13: a line can carry a `promotion_id`
    while its `price_mode` is still `list` (AC-S6-4 only refuses a MANUAL
    price outside Selling mode, never a promotion pick) - `price_mode` is
    checked here too, or a part resolved an offer the line itself never
    prints.
    """
    if product.id not in cache:
        cache[product.id] = product_tag_data(db, product, viewer, promotion_id)
    data = cache[product.id]
    return {
        # The id rides along so a caller can match a part back to the choice that
        # produced it (`tag_body`'s `choices_display`). Never rendered - the code
        # is what a reader sees (AC-X-2).
        "product_id": product.id,
        "code": data["code"],
        "name": data["name"],
        "dimensions": data["dimensions"],
        "spec_lines": data["spec_lines"],
        "specs": data["specs"],
        "images": data["images"],
        "barcode": data["barcode"],
        "list_price": data["list_price"],
        "sell_price": data["offer_price"] if price_mode == "selling" else None,
        # AC-A11: this part's OWN product's currency, not the host's.
        "currency": data["currency"],
        # AC-S4-4: this part's OWN product's tag copy, not the host's.
        "price_tag_description": data["price_tag_description"],
    }


def _package_text(parts: list[dict], open_groups: list[dict]) -> str:
    """The `set_members` slot text for a product tag with a package (AC-S4-1).

    `set_members` on purpose: every template already carries that slot, so a
    cabinet's package prints with no template touched (D4).

    Two shapes, deliberately different. A resolved part is a thing that is IN the
    box, so it leads with `+`; an open group is a choice the reader makes, so it
    leads with its own label and lists the candidates. A tag with neither prints
    nothing at all - most tags carry no package and must not grow a stray line.
    """
    lines = [
        " ".join(
            piece
            for piece in (
                f"+ {part['code']}",
                distinct_name(part["code"], part["name"]),
                part["dimensions"],
            )
            if piece
        )
        for part in parts
    ]
    lines += [
        f"{group['role']}: " + " / ".join(c["code"] for c in group["candidates"])
        for group in open_groups
    ]
    return "\n".join(lines)


def ordered_tags(request) -> list[tuple[int, int, object, object]]:
    """Every tag of the request in print order: (line index, tag index, line, tag).

    The line's position and the tag's position under it are what the LABEL is
    made of ("1a", "1b"), so they are produced by the same walk that produces
    the tags - a caller that numbered a subset on its own would call the second
    tag of line 3 "1a".
    """
    out: list[tuple[int, int, object, object]] = []
    for line_index, line in enumerate(
        sorted(request.lines, key=lambda l: (l.sort_order or 0, l.id))
    ):
        for tag_index, tag in enumerate(
            sorted(line.tags or [], key=lambda t: (t.sort_order or 0, t.id))
        ):
            out.append((line_index, tag_index, line, tag))
    return out


def resolve_tags_live(db: Session, request, tags=None) -> list[dict]:
    """What master data says about these tags RIGHT NOW (D3, S3).

    One row per TAG, not per line: a line whose package left a choice group
    open is split by marketing into one tag per candidate, and each of those
    prints its own basin at its own price. `line_id` still says which line
    asked for it.

    ``tags`` is the subset to answer for; the walk is still over the whole
    request so the labels are the positions they actually are. None means every
    tag.

    The marketing override wins over the resolved offer (D9) - it is a decision
    somebody made and logged a reason for, and the engine has no way to know
    about it. Since the combos slice that override is a TAG fact.
    """
    viewer = staff_viewer()
    wanted = None if tags is None else {tag.id for tag in tags}
    rows: list[dict] = []

    line_data: dict = {}
    parts_cache_by_line: dict = {}
    # One `line_pricing` round trip per DISTINCT (line, resolved set), not
    # per tag: two split siblings that resolved the same candidate ask the
    # same question, and a 30-tag request paid for 30 engine runs.
    basis_cache: dict = {}
    for line_index, tag_index, line, tag in ordered_tags(request):
        if wanted is not None and tag.id not in wanted:
            continue

        # D1/S9: the promotion is a LINE fact now, not the request's.
        promotion_id = line.promotion_id

        if line.id not in line_data:
            line_data[line.id] = _line_product_data(db, line, viewer, promotion_id)
        data = line_data[line.id]
        if data is None:
            continue
        code = data["code"]
        name = data["name"]
        dimensions = data["dimensions"]
        specs = data["spec_lines_text"]
        spec_values = data["specs"]
        images = data["images"]
        barcode = data["barcode"]
        price_tag_description = data["price_tag_description"]
        set_members = data["set_members"]

        open_groups = _open_groups_for(db, line, tag)
        part_products = _resolved_part_products(db, line, tag)
        parts_cache = parts_cache_by_line.setdefault(line.id, {})
        part_rows = [
            _part_row(
                db, product, viewer, promotion_id, parts_cache,
                price_mode=request.price_mode,
            )
            for product in part_products
        ]
        # S6 (AC-S6-3/S6-4): every product this tag's combo could show - the
        # subject picker's list, a superset of `part_rows` above, which
        # stays the narrow "this tag's own choice" list every price sum and
        # `set_members` below still reads.
        combo_rows = _combo_products(
            db, line, tag, viewer, promotion_id, parts_cache,
            price_mode=request.price_mode,
        )

        # D4. A tag with no parts is exactly today's product tag: the sums below
        # are over an empty list, so both prices and the slot text are the ones
        # this line has always answered with.
        list_price = data["list_price"]
        sell_price = data["offer_price"]
        # D7: the HOST alone, never the roll-up below - a price badge's
        # `subjectPart: -1` reads THIS, not `list_price`/`sell_price`, the
        # moment a combo has priced parts.
        parent_list_price = data["list_price"]
        parent_sell_price = data["offer_price"] if line.show_promo_price else None
        if part_products:
            part_prices = resolve_prices(db, part_products, viewer, promotion_id)
            list_price = _sum_prices(
                [list_price]
                + [
                    (
                        part_prices.get(product.id).list_price
                        if part_prices.get(product.id)
                        else None
                    )
                    for product in part_products
                ]
            )
            # The selling side takes the engine's offer where it HAS one and the
            # list price where it does not: an offer the engine cannot answer for
            # is not a discount, it is simply the price.
            sell_price = _sum_prices(
                [sell_price if sell_price is not None else data["list_price"]]
                + [
                    _offer_or_list(part_prices.get(product.id))
                    for product in part_products
                ]
            )
            package_text = _package_text(part_rows, open_groups)
            if package_text:
                set_members = package_text
        elif open_groups:
            set_members = _package_text([], open_groups)

        # D3/AC-S9-3: a hand-typed line price wins over the engine's sum, the
        # same as it does at save time (S7's `line_pricing`).
        if line.manual_sell_price is not None:
            sell_price = line.manual_sell_price

        # AC-S9-3/R11b: `sell_price_basis`, through the SAME engine
        # `line_pricing` (S7) uses for the create/update path and the
        # CRM/portal lookup routes, so the three can never disagree about
        # what a line is worth - computed per TAG, not cached per line: after
        # D6 auto-split, two tags off the same line resolve two different
        # candidates, and only one of them may actually be covered by the
        # line's promotion (a line-level cache answered both with whichever
        # tag asked first).
        if line.product_id:
            resolved_ids = [line.product_id] + [product.id for product in part_products]
        elif line.product_set_id:
            from app.services.price_tag_request_service import PriceTagRequestService

            resolved_ids = PriceTagRequestService._set_member_product_ids(
                db, line.product_set_id
            )
        else:
            resolved_ids = []
        sell_price_basis = _tag_sell_price_basis(
            db, line, resolved_ids, viewer, basis_cache
        )
        # R16: derived LIVE from THIS tag's own basis, never echoing
        # `line.show_promo_price` - that column is written once at save
        # time, per LINE (D1/D3/AC-S7-5), so a split tag that resolved the
        # ONE covered candidate would otherwise read the answer for a
        # sibling that resolved the uncovered one, and a promotion that
        # expires between save and read would keep printing SP forever.
        show_promo_price = request.price_mode == "selling" and sell_price_basis != "list"

        # The override is a SELLING price and wins over the engine's sum. It
        # never rewrites what the package LISTS at - the tag still shows what the
        # customer is saving against.
        if tag.marketing_price_override is not None:
            sell_price = Decimal(str(tag.marketing_price_override))

        rows.append(
            {
                "tag_id": tag.id,
                "line_id": line.id,
                "tag_label": _tag_label(line_index, tag_index),
                "open_groups": open_groups,
                # S6: `parts` is now the WIDE list (every candidate of every
                # open group) - a slot may bind to any of them, and the print
                # payload's image map has to reach a non-chosen candidate's
                # photo too (AC-S6-5). `own_parts` is what this tag itself
                # prints and prices - unchanged meaning, so `set_members`,
                # Tag total and an unmodified template still read it.
                "parts": combo_rows,
                "own_parts": part_rows,
                "code": code,
                "name": name,
                "dimensions": dimensions,
                "spec_lines": specs,
                "specs": spec_values,
                "set_members": set_members,
                "images": images,
                "list_price": list_price,
                "sell_price": sell_price,
                "parent_list_price": parent_list_price,
                "parent_sell_price": parent_sell_price,
                "sell_price_basis": sell_price_basis,
                "show_promo_price": show_promo_price,
                "included_accessories": line.included_accessories or "",
                # The TAG's own quantity, seeded from the line's at submit and
                # marketing's to change afterwards.
                "quantity": tag.quantity,
                "barcode": barcode,
                "currency": data["currency"],
                "price_tag_description": price_tag_description,
            }
        )

    return rows


def _tag_sell_price_basis(
    db: Session, line, resolved_product_ids: list[str], viewer, cache: dict | None = None
) -> str:
    """AC-S9-3/D4/R11b: what THIS TAG's price is based on, through the exact
    same `line_pricing` engine S7's create/update path and lookup routes use
    - a tag can never disagree with the lines table about why it prints SP
    or LP.

    Per TAG (``resolved_product_ids`` is the caller's own resolved set -
    parent plus THIS tag's chosen candidates, or a set line's members), not
    per line: a line-level cache answered every split sibling with whichever
    tag's candidate happened to ask first, even when only one of them was
    actually covered by the line's promotion.
    """
    from app.services.dealer_kit.pricing import line_pricing

    if not resolved_product_ids:
        return "list"
    cache = {} if cache is None else cache
    key = (line.id, tuple(sorted(resolved_product_ids)))
    if key in cache:
        return cache[key]
    row = line_pricing(
        db,
        lines=[
            {
                "key": "_b",
                "product_id": None,
                "part_product_ids": resolved_product_ids,
                "candidate_product_ids": [],
                "promotion_id": line.promotion_id,
                "manual_sell_price": line.manual_sell_price,
            }
        ],
        viewer=viewer,
        # Owner ruling (review round 2): a SAVED line's basis reads its
        # STORED promotion only. The auto pick is the lookup route's job -
        # inherited here, a line nobody put a promotion on printed SP at a
        # plain list total the moment any promotion covered its product.
        auto_pick=False,
    )[0]
    cache[key] = row["sell_price_basis"]
    return cache[key]


def _combo_lead_image(db: Session, line, images: list[dict]) -> list[dict]:
    """AC-S5-6: when the line's combo names a picture, that photo leads the
    tag's image list with `is_primary` forced true, followed by the ordinary
    gallery - the SAME photo `gallery_images` already returns (it ranks a
    Combo Image last, AC-S5-11), just reordered to the front for lines drawn
    through THIS combo. A line naming a combo whose image was deleted (the
    migration's FK is `ON DELETE SET NULL`) or whose picture the viewer
    cannot see falls straight back to the plain gallery order (AC-S5-9).
    """
    combo_id = getattr(line, "combo_id", None)
    if not combo_id:
        return images
    from app.models.product_combo import ProductCombo

    combo = db.query(ProductCombo).filter(ProductCombo.id == combo_id).first()
    if combo is None or not combo.image_attachment_id:
        return images
    lead = next(
        (img for img in images if img.get("attachment_id") == combo.image_attachment_id),
        None,
    )
    if lead is None:
        return images
    rest = [img for img in images if img is not lead]
    return [{**lead, "is_primary": True}, *rest]


def _line_product_data(db: Session, line, viewer, promotion_id) -> Optional[dict]:
    """The line's own product or set, resolved ONCE for all of its tags.

    Two tags split off one line share a host product; asking master data for it
    per tag would double every read a split costs.
    """
    if line.product_set_id:
        product_set = get_product_set(db, line.product_set_id)
        if product_set is None:
            return None
        data = product_set_tag_data(db, product_set, viewer, promotion_id)
        return {
            "code": data["set_code"],
            "name": data["name"],
            # A set has no spec row of its own: the specs belong to its members,
            # and a set tag lists the members rather than their materials. Same
            # for barcode - a set has no single EAN either (S7).
            "dimensions": "",
            "spec_lines_text": "",
            "specs": [],
            "images": [],
            "barcode": None,
            # A set has no price_tag_description of its own - same rule as
            # barcode above (S7).
            "price_tag_description": None,
            "set_members": _set_member_text(data["members"]),
            "list_price": data["list_price"],
            "offer_price": data["offer_price"],
            "currency": data["currency"],
        }
    if line.product_id:
        product = get_product(db, line.product_id)
        if product is None:
            return None
        data = product_tag_data(db, product, viewer, promotion_id)
        return {
            "code": data["code"],
            "name": data["name"],
            "dimensions": data["dimensions"],
            "spec_lines_text": "\n".join(data["spec_lines"]),
            "specs": data["specs"],
            "images": _combo_lead_image(db, line, data["images"]),
            "barcode": data["barcode"],
            "price_tag_description": data["price_tag_description"],
            "set_members": "",
            "list_price": data["list_price"],
            "offer_price": data["offer_price"],
            "currency": data["currency"],
        }
    return None


#: S8 (Q9 ruled, PLAN-price-tag-r10.md S8): the read seam applies a
#: product-data change by itself in these statuses. A proof already out for
#: review (`proof_ready` on) keeps today's flag-and-decide (Keep/Update).
AUTO_UPDATE_STATUSES = {"designing", "changes_requested"}


def apply_auto_data_updates(
    db: Session, request, triples: list[tuple], *, user_id: Optional[str] = None
) -> None:
    """S8: re-pin every ``(tag, live_row, changes)`` in ``triples`` to its live
    data. ``changes`` is the diff the CALLER already computed (the same list
    that lands in the read response's ``data_changes``), reused rather than
    recomputed, so what the person is told just happened and what gets
    written to ``data_update_changes`` can never disagree.

    The same steps a manual Update always took (D18): one "Before product
    update" PageVersion snapshotting the pins being replaced, the re-pin
    itself, then an "after" snapshot of the result - except this call covers
    a whole SWEEP at once, so N tags changing between two polls fold into
    ONE before-version and ONE after-version (AC-S8-3), not N of each.

    Each re-pinned tag is stamped with the three S8 columns
    (``data_updated_at``/``data_update_changes``/``data_update_version``) so
    the rail's "updated" indicator can name the change and Roll back
    (``POST versions/{n}/restore``) knows which version undoes it.
    ``user_id`` (AC-S8-15) is the staff member whose poll triggered this
    write - both PageVersions carry it as ``created_by``, an auto-applied
    version is not an anonymous system row.

    AC-S8-16: idempotent both against a repeat call with the same already-
    applied triples (a triple whose diff is already reflected in the tag's
    current pin is dropped before anything is written; an entirely
    already-applied batch writes nothing) and against two pollers racing to
    apply the SAME live edit (the page row is locked ``FOR UPDATE`` before
    ``max(version)`` is read, so a second caller blocks behind the first for
    the rest of this transaction; the whole write also runs inside its own
    SAVEPOINT, so a version-number collision that still slips through costs
    only this apply, not the caller's whole transaction - leaving the pins
    as they were rather than 500ing the read that triggered it).
    """
    if not triples:
        return
    from sqlalchemy import func
    from sqlalchemy.exc import IntegrityError

    from app.models.dealer_kit import Page, PageVersion
    from app.services.price_tag_request_service import PriceTagRequestService

    triples = [
        (tag, live, changes)
        for tag, live, changes in triples
        if tag.pinned_tag_data != pin_payload(live)
    ]
    if not triples:
        return

    PriceTagRequestService.ensure_tag_sheet_page(db, request, None)

    now = datetime.utcnow()
    changes_by_tag = {tag.id: changes for tag, _live, changes in triples}

    def pins_snapshot() -> dict:
        return {
            t.id: t.pinned_tag_data
            for line in request.lines
            for t in (line.tags or [])
            if t.pinned_tag_data is not None
        }

    savepoint = db.begin_nested()
    try:
        # AC-S8-16: FOR UPDATE - blocks a second caller computing the same
        # `max(version) + 1` for this page until this transaction ends.
        page = (
            db.query(Page)
            .filter(Page.id == request.page_id)
            .with_for_update()
            .first()
        )

        before_version_number = None
        doc = None
        label_suffix = ""
        if page is not None:
            # Lazy import: routes is the layer above services, but this is
            # the one document shape both sides need to agree on, and
            # duplicating it here is how it drifted (bleed_mm/gap_mm stale
            # against the designer's own default until this fix).
            from app.api.v1.dealer_kit.price_tag_requests import (
                _default_tag_sheet_doc,
            )

            latest = (
                db.query(PageVersion)
                .filter(PageVersion.page_id == page.id)
                .order_by(PageVersion.version.desc())
                .first()
            )
            doc = page.draft_doc or (latest.doc if latest else None) or _default_tag_sheet_doc()
            fields = sorted({
                change["label"] for changes in changes_by_tag.values() for change in changes
            })
            label_suffix = f": {', '.join(fields)}" if fields else ""
            current_max = (
                db.query(func.max(PageVersion.version))
                .filter(PageVersion.page_id == page.id)
                .scalar()
            ) or 0
            before_version = PageVersion(
                page_id=page.id,
                version=current_max + 1,
                doc=doc,
                commit_message=f"Before product update{label_suffix}",
                created_by=user_id,
                pinned_line_data=pins_snapshot(),
            )
            db.add(before_version)
            db.flush()
            before_version_number = before_version.version
            page.draft_doc = None

        for tag, live, _changes in triples:
            tag.pinned_tag_data = pin_payload(live)
            tag.pinned_at = now
            tag.data_change_ack_hash = None
            tag.data_updated_at = now
            tag.data_update_changes = changes_by_tag.get(tag.id) or []
            tag.data_update_version = before_version_number

        if page is not None:
            db.flush()
            after_version = PageVersion(
                page_id=page.id,
                version=before_version_number + 1,
                doc=doc,
                commit_message=f"Product update{label_suffix}",
                created_by=user_id,
                pinned_line_data=pins_snapshot(),
            )
            db.add(after_version)
            db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        logger.warning(
            "apply_auto_data_updates: version race on page %s for request %s - "
            "left pins as they were for this poll.",
            request.page_id,
            request.id,
        )


def resolve_request_line_data(
    db: Session, request, *, apply_updates: bool = False, user_id: Optional[str] = None
) -> list[dict]:
    """Display data for every TAG of a price tag request.

    The one resolver behind the designer's left panel, both design previews and
    the print payload, so what marketing approves on screen and what the PDF
    prints are the same numbers from the same call.

    Since r9 (D17) it answers the PINNED data when a tag has a pin: master data
    resolved live on every render meant a price edited on Tuesday silently
    rewrote the proof approved on Monday. The live resolve still runs beside it
    for a request somebody can still act on, and the difference comes back as
    ``data_changes`` for a person to decide. A terminal request skips the live
    resolve entirely - nothing can be updated, so asking master data to say so
    is work with no reader.

    The gate is per TAG since the combos slice: two tags split off one line
    resolve two different basins, so they are drawn from different data and a
    Keep on one must not silence the other.

    The marketing override wins over the pinned offer (D9/AC-S5-7) - it is a
    decision somebody made and logged a reason for.

    ``apply_updates`` (S8): the read seam that WRITES an auto-apply for a
    request in ``AUTO_UPDATE_STATUSES`` - opt-in, defaulting to False, so
    every plain read (the design canvas, the print/export payload, a version
    restore, a direct service call) keeps answering the PIN exactly as it
    stood, and only the two callers that are genuinely a "poll"
    (``GET data-changes`` and the list's touched-row sweep) pass it. Passing
    it from every reader would have master data move a tag the instant
    anyone merely opens the design, which is the r9 pin gate's entire point
    to prevent.

    ``user_id`` (AC-S8-15) is who to attribute the write to when
    ``apply_updates`` actually applies something - the caller's job to
    resolve (a real staff session holding ``.process``), never this
    resolver's; passed straight through to ``apply_auto_data_updates``.
    """
    from app.services.price_tag_request_service import PriceTagRequestService

    terminal = PriceTagRequestService.is_terminal(request)

    # Live finding: a tag claimed through a backend that predated the pin (or
    # otherwise left unpinned) stayed exposed to master data forever - reading
    # it again and again is not a decision. An in-flight request pins any tag
    # still missing one on the first read that reaches it, the same way
    # `pin_tags` pins a line added while designing; a terminal request is
    # untouched, since it can decide nothing either way (the sibling guard
    # `TestATerminalRequestNeverRunsTheLiveResolve` holds for the same reason).
    if not terminal:
        pin_tags(db, request, only_unpinned=True)

    walk = ordered_tags(request)
    rows: list[dict] = []

    # ONE live resolve for the whole request, and one promotion read (S11).
    # Per tag, a twenty-tag sheet asked master data twenty times over - and the
    # promotion once per tag on top - for a page that draws once.
    # A terminal request resolves only the tags with no pin at all: it can
    # decide nothing, so diffing the pinned ones is work with no reader.
    needed = (
        [tag for _, _, _, tag in walk]
        if not terminal
        else [tag for _, _, _, tag in walk if not tag.pinned_tag_data]
    )
    live_rows: dict = (
        {row["tag_id"]: row for row in resolve_tags_live(db, request, needed)}
        if needed
        else {}
    )
    # D1/S9: a promotion is a LINE fact now, not one read for the whole
    # request - cached by promotion id, since several lines commonly share
    # the same promotion.
    promotion_live_by_id: dict = {}
    # S8: every (tag, live, changes) this sweep found changed, for a request
    # whose status auto-applies. Collected across the WHOLE walk and applied
    # once at the end, so N tags changing between two polls fold into one
    # before-version and one after-version (AC-S8-3), not one pair each.
    auto_update = (
        apply_updates and not terminal and request.status in AUTO_UPDATE_STATUSES
    )
    to_apply: list[tuple] = []

    for line_index, tag_index, line, tag in walk:
        pinned = tag.pinned_tag_data
        if pinned:
            row = _row_from_pin(db, line, tag, pinned, _tag_label(line_index, tag_index))
            # AC-S8-13: "seen it or not", not "does a live diff exist right
            # now" - the row carries the tag's OWN updated-at, read here at
            # walk time, before this same sweep's own `apply_auto_data_
            # updates` below might set it fresh. `_row_change_count` counts
            # on this OR a non-empty live diff, so the poll that just
            # auto-applied (diff still non-empty, `data_updated_at` not yet
            # written) and every later poll before Dismiss (diff empty,
            # `data_updated_at` already set) both count the tag.
            row["data_updated_at"] = tag.data_updated_at
            if not terminal:
                live = live_rows.get(tag.id)
                if live is not None:
                    if line.promotion_id not in promotion_live_by_id:
                        promotion_live_by_id[line.promotion_id] = _promotion_is_live(
                            db, line.promotion_id
                        )
                    # The PIN AS READ, not as stored: the marketing override is
                    # applied to both sides, so the office's own decision is not
                    # read back to it as "master data moved" (S4).
                    changes = diff_pin_against_live(
                        db,
                        line,
                        row,
                        live,
                        tag.data_change_ack_hash,
                        promotion_live=promotion_live_by_id[line.promotion_id],
                    )
                    row["data_changes"] = changes
                    if auto_update and changes:
                        to_apply.append((tag, live, changes))
                else:
                    row["data_changes"] = []
            rows.append(row)
            continue

        live = live_rows.get(tag.id)
        if live is not None:
            live["data_updated_at"] = tag.data_updated_at
            if not terminal:
                live["data_changes"] = []
            rows.append(live)

    if to_apply:
        apply_auto_data_updates(db, request, to_apply, user_id=user_id)

    return rows


def resolve_version_line_data(db: Session, request, pinned_line_data: dict) -> list[dict]:
    """The tags as ONE VERSION carries them (D19/S2).

    A version holds two things: the document, and the product data pinned when
    it was written. Drawing the document against today's pins shows last week's
    layout filled with this week's prices - a page that never existed, presented
    as history, and exactly what somebody opens History to check.

    The map is keyed by TAG id, which is what the document keys its placements
    on. A version written before the pins existed has nothing of its own, so
    those tags fall back to the live resolve rather than drawing blank.
    """
    pins = pinned_line_data or {}
    rows: list[dict] = []
    for line_index, tag_index, line, tag in ordered_tags(request):
        label = _tag_label(line_index, tag_index)
        pinned = pins.get(tag.id) or pins.get(str(tag.id))
        if pinned:
            rows.append(_row_from_pin(db, line, tag, pinned, label))
            continue
        live = resolve_tags_live(db, request, [tag])
        if live:
            rows.append(live[0])
    return rows


def _row_from_pin(db: Session, line, tag, pinned: dict, tag_label: str) -> dict:
    """The pin, as the resolver's own row shape.

    Five things are read fresh rather than from the pin: the photo URLs (a
    signed link expires within the hour), WHICH photo leads (AC-S5-10: a
    combo's own cover picture is a catalogue fact, not a product fact the
    review gate is meant to freeze - a marketing user who swaps it must see
    the new cover on a request already pinned, the same reasoning that keeps
    the URLs themselves live), the marketing override (a decision that must
    survive whatever the pin says), the tag's quantity, and the label, which
    is a POSITION - deleting the line above must renumber it.
    """
    from app.services.dealer_kit.product_images import resign_images

    row = dict(pinned)
    row["tag_id"] = tag.id
    row["line_id"] = line.id
    row["tag_label"] = tag_label
    row["images"] = _combo_lead_image(db, line, resign_images(db, pinned.get("images") or []))
    # D7 gave every PART the host's own photos, and a part's signed link dies
    # on exactly the same hour. Re-signed here, at the one seam both readers
    # go through (`resolve_request_line_data` -> the export media map and
    # `resolve-prices`' own `parts[].images[].url`), or a part-bound layer on
    # a design opened a day after the pin draws a dead link.
    if pinned.get("parts"):
        row["parts"] = [
            {**part, "images": resign_images(db, part.get("images") or [])}
            for part in pinned["parts"]
        ]
    if pinned.get("own_parts"):
        row["own_parts"] = [
            {**part, "images": resign_images(db, part.get("images") or [])}
            for part in pinned["own_parts"]
        ]
    elif "own_parts" not in pinned:
        # AC-S6-11: a row pinned before r10 has no `own_parts` - its `parts`
        # was already exactly this narrow list (nothing had widened it yet),
        # kept as pinned rather than reprocessed, so `set_members`/Tag total
        # keep reading the right thing.
        row["own_parts"] = pinned.get("parts") or []
    row["quantity"] = tag.quantity
    # R16: NOT refreshed from `line.show_promo_price` - that column is a
    # per-LINE save-time value, and since D6 auto-split two tags off one
    # line can resolve two different candidates where only one is covered,
    # so the line's own column is the wrong answer for the other. The
    # pinned value is THIS tag's own, computed by the (now per-tag) live
    # resolver at pin time, and stays frozen consistently with the basis
    # it was derived from until the tag is re-pinned - never echoed from a
    # column that answers a different question.
    row["included_accessories"] = line.included_accessories or ""
    if tag.marketing_price_override is not None:
        row["sell_price"] = Decimal(str(tag.marketing_price_override))
    return row


# ---------------------------------------------------------------------------
# The product data gate (r9 S5/D16-D18)
# ---------------------------------------------------------------------------


def _plain(value):
    """JSONB-safe: Decimal is not serialisable, and money must not lose cents."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _row_change_count(row: dict) -> int:
    """1 if this row carries any product-data change, else 0.

    Code review 16 Sep: every real caller passes a raw resolver dict
    (``resolve_request_line_data``'s own ``data_changes`` key) - the earlier
    ``TagDataChangeSet``/attribute branch handled a shape no call site ever
    produces (``_change_sets`` passes the raw rows, not the filtered pydantic
    list). ``changes`` stays as a second key only because the unit tests in
    ``test_price_tag_data_change_cache.py`` exercise this function directly
    with that shorter, hand-written shape.

    AC-S8-13: the badge is "seen it or not", not "does a live diff exist
    right now" - a tag ``resolve_request_line_data`` just auto-applied
    re-pins to the live value, so a diff computed on the NEXT poll is empty
    even though nobody has looked at what changed yet. Counted instead on
    ``data_updated_at`` (set on apply, cleared only by Dismiss) for that
    case, OR the live diff for a flag-only status exactly as before - a row
    with neither key (the hand-written shape above) falls through to the
    plain diff check unchanged.
    """
    if row.get("data_updated_at") is not None:
        return 1
    return 1 if (row.get("changes") or row.get("data_changes")) else 0


def store_data_change_count(
    db: Session, request, rows: Iterable, checked_at: datetime | None = None
) -> None:
    """AC-D2: cache how many of ``rows`` carry a change, and when this ran.

    Every caller that already computed the diff calls this right after (the
    data-changes GET, recheck, pin update/keep, the detail response route -
    PLAN price-tag-currency-token-extract-prompt.md section D), then commits
    the same way it already did for its own write. A terminal request stores
    0 unconditionally: nothing on it can be updated, so a nonzero count would
    be true but is a claim nobody can act on.

    Security review 16 Sep: ``checked_at`` should be a HORIZON the caller
    captures immediately before calling ``resolve_request_line_data``, not a
    timestamp read after the resolve. Stamping post-resolve leaves a window: a
    product edited WHILE the resolve ran (read the old value, then moved)
    would stamp a `data_checked_at` after that edit and `touched_request_ids`
    would then treat the row as already checked, silently swallowing a real
    change. Every production call site now passes its own pre-resolve
    horizon; the default (``datetime.utcnow()`` taken here) exists only for a
    caller with no resolve of its own to time against.
    """
    from app.services.price_tag_request_service import PriceTagRequestService

    request.data_changed_tag_count = (
        0
        if PriceTagRequestService.is_terminal(request)
        else sum(_row_change_count(row) for row in rows)
    )
    request.data_checked_at = checked_at or datetime.utcnow()


def pin_payload(row: dict) -> dict:
    """What gets stored on the tag: the resolved row, JSON-safe.

    Photos keep their ``attachment_id``/``is_primary``; the URL travels too but
    is re-signed on every read (`_row_from_pin`), because a signed link is dead
    within the hour and the pin lives for weeks.
    """
    return _plain(row)


def data_hash(row: dict) -> str:
    """A stable fingerprint of the fields the gate compares.

    Only the fields a person is asked about: quantity, remarks and the override
    are the request's own and change without master data moving at all.
    """
    import hashlib
    import json

    subject = {
        key: _plain(row.get(key))
        for key in (
            "code",
            "name",
            "dimensions",
            "spec_lines",
            "specs",
            "set_members",
            "list_price",
            "sell_price",
            "barcode",
            "price_tag_description",
        )
    }
    # AC-S5-10: WHICH photo leads (`is_primary`) is part of what prints - a
    # combo's picture swapping the lead (the designer's product-image slot
    # draws `images[0]`) must move the hash even when the underlying set of
    # attachment ids is unchanged, so order and `is_primary` both count, not
    # just membership.
    subject["images"] = [
        {"attachment_id": image.get("attachment_id"), "is_primary": bool(image.get("is_primary"))}
        for image in row.get("images") or []
    ]
    # S6: `parts` is the WIDE combo list (every candidate of every open
    # group), which `own_parts`'s narrow sum does not cover - a candidate
    # added or removed, or a non-chosen sibling's price moving, would
    # otherwise never move the hash and the gate would stay silent even
    # though `diff_pin_against_live` walks the same wide list per part.
    subject["parts"] = [
        {
            "product_id": part.get("product_id"),
            "role": part.get("role"),
            "chosen": bool(part.get("chosen")),
            "list_price": _plain(part.get("list_price")),
            "sell_price": _plain(part.get("sell_price")),
            # AC-S4-15: a part's OWN template is a fact about that part, not
            # about the host - edited, it must move the hash the same way
            # the host's own `price_tag_description` above already does, or
            # the gate stays silent for the exact case `diff_pin_against_
            # live`'s per-part loop already knows how to report.
            "price_tag_description": part.get("price_tag_description"),
        }
        for part in row.get("parts") or []
    ]
    return hashlib.sha256(
        json.dumps(subject, sort_keys=True, default=str).encode()
    ).hexdigest()


def _money(value) -> Optional[str]:
    """Two decimals, no thousands separator and no currency symbol.

    The dialog puts old beside new, so what matters is that the two are
    comparable at a glance; the money formatting each surface already has is
    what dresses them.
    """
    return None if value is None else f"{float(value):.2f}"


def diff_pin_against_live(
    db: Session,
    line,
    pinned: dict,
    live: dict,
    ack_hash: Optional[str],
    promotion_live: Optional[bool] = None,
) -> list[dict]:
    """What master data has moved under this tag, field by field (D17/AC-S9-4).

    Silent when the live data matches the pin, and silent when it matches an
    ack somebody has already looked at and chosen to keep. A change AFTER a
    keep asks again, because it is a different change.

    ``line`` (D1, S9) - a promotion is a LINE fact now, so "did the offer
    change because the PROMOTION ended" reads ``line.promotion_id``, not a
    header the request no longer carries. ``promotion_live`` is that same
    read, done ONCE by a caller diffing every tag of the request (S11); left
    out, this reads it itself.

    A PART's own price change diffs under that part's own code (AC-S9-4),
    so the reader sees exactly which product on the tag moved rather than a
    figure folded into the tag's own total.
    """
    if ack_hash and data_hash(live) == ack_hash:
        return []
    if data_hash(live) == data_hash(pinned):
        return []

    changes: list[dict] = []

    def add(field, label, old, new, note=None):
        if old == new:
            return
        entry = {"field": field, "label": label, "old": old, "new": new}
        if note:
            entry["note"] = note
        changes.append(entry)

    add("name", "Name", pinned.get("name"), live.get("name"))
    add("dimensions", "Dimensions", pinned.get("dimensions"), live.get("dimensions"))
    add("spec_lines", "Specs", pinned.get("spec_lines"), live.get("spec_lines"))
    add("set_members", "Set members", pinned.get("set_members"), live.get("set_members"))
    add("barcode", "Barcode", pinned.get("barcode"), live.get("barcode"))
    add(
        "price_tag_description",
        "Price tag description",
        pinned.get("price_tag_description"),
        live.get("price_tag_description"),
    )
    add(
        "list_price",
        "List price",
        _money(pinned.get("list_price")),
        _money(live.get("list_price")),
    )

    pinned_offer = _money(pinned.get("sell_price"))
    live_offer = _money(live.get("sell_price"))
    if pinned_offer != live_offer:
        # One row, not two: the value moving and the promotion ending are the
        # same event, and the NOTE is what tells them apart. A line that never
        # had an offer cannot lose one, so a promotion switched off elsewhere
        # says nothing here.
        if promotion_live is None:
            promotion_live = _promotion_is_live(db, line.promotion_id)
        ended = (
            live_offer is None
            and pinned_offer is not None
            and bool(line.promotion_id)
            and not promotion_live
        )
        changes.append(
            {
                "field": "offer_price",
                "label": "Offer price",
                "old": pinned_offer,
                "new": live_offer,
                "note": "Promotion ended" if ended else None,
            }
        )

    # Specs key by key, so the dialog names the one that moved.
    pinned_specs = {spec.get("key"): spec for spec in pinned.get("specs") or []}
    live_specs = {spec.get("key"): spec for spec in live.get("specs") or []}
    for key in sorted(set(pinned_specs) | set(live_specs)):
        before, after = pinned_specs.get(key), live_specs.get(key)
        label = (after or before or {}).get("label") or key
        add(
            f"spec:{key}",
            f"Spec: {label}",
            (before or {}).get("value"),
            (after or {}).get("value"),
        )

    # Photos by attachment id: a swapped picture is two changes (one gone, one
    # arrived), which is what the dialog draws side by side.
    pinned_images = {
        image.get("attachment_id"): image for image in pinned.get("images") or []
    }
    live_images = {
        image.get("attachment_id"): image for image in live.get("images") or []
    }
    for attachment_id in sorted(set(pinned_images) | set(live_images)):
        if attachment_id in pinned_images and attachment_id in live_images:
            continue
        gone = attachment_id in pinned_images
        changes.append(
            {
                "field": f"image:{attachment_id}",
                "label": "Photo",
                "old": "Photo on the tag" if gone else None,
                "new": None if gone else "New photo",
                "old_image_url": (pinned_images.get(attachment_id) or {}).get("url"),
                "new_image_url": (live_images.get(attachment_id) or {}).get("url"),
                "note": "Photo removed" if gone else None,
            }
        )

    # D7/AC-S9-4: a PART's own price or photo change diffs under THAT part's
    # code, not folded into the tag's own list_price/sell_price sum above - a
    # reader has to know which product on the tag moved, and the host's own
    # figure already covers the host alone.
    pinned_parts = {
        part.get("product_id"): part for part in pinned.get("parts") or []
    }
    live_parts = {part.get("product_id"): part for part in live.get("parts") or []}
    for product_id in sorted(set(pinned_parts) | set(live_parts), key=str):
        before = pinned_parts.get(product_id) or {}
        after = live_parts.get(product_id) or {}
        code = after.get("code") or before.get("code") or product_id
        add(
            f"part:{code}:list_price",
            f"{code} list price",
            _money(before.get("list_price")),
            _money(after.get("list_price")),
        )
        add(
            f"part:{code}:sell_price",
            f"{code} offer price",
            _money(before.get("sell_price")),
            _money(after.get("sell_price")),
        )
        # AC-S4-15: a part's own template, diffed under that part's own code -
        # mirrors the host's own `price_tag_description` comparison above.
        add(
            f"part:{code}:price_tag_description",
            f"{code} price tag description",
            before.get("price_tag_description"),
            after.get("price_tag_description"),
        )
        before_images = {
            image.get("attachment_id") for image in before.get("images") or []
        }
        after_images = {
            image.get("attachment_id") for image in after.get("images") or []
        }
        if before_images != after_images:
            changes.append(
                {
                    "field": f"part:{code}:image",
                    "label": f"{code} photo",
                    "old": "Photo on the tag" if before_images else None,
                    "new": "New photo" if after_images else None,
                }
            )

    return changes


def _promotion_is_live(db: Session, promotion_id) -> bool:
    """Whether the request's promotion is still running (S5).

    The flag AND the window: a promotion that ran to the 30th and is still
    flagged active is over on the 1st, and the pricing engine already knows
    that - it filters on the dates. Reading `is_active` alone left the one
    wording that explains a vanished offer ("Promotion ended") missing exactly
    when it was needed.
    """
    if not promotion_id:
        return False
    from datetime import date as _date

    from app.models.marketing import Promotion

    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if promotion is None or not promotion.is_active:
        return False
    today = _date.today()
    start, end = promotion.start_date, promotion.end_date
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


def pin_tags(db: Session, request, *, only_unpinned: bool = True) -> int:
    """Freeze what the tags are drawn from (D16).

    Called when a request starts being designed, when a line is added to one
    already in progress, and when marketing resolves a tag's open choice (which
    changes what the tag resolves to, so the old pin describes a different
    product). ``only_unpinned`` is the default and the reason the gate works at
    all: re-pinning on the way back from ``changes_requested`` would swallow the
    very difference it exists to show.
    """
    tags = [
        tag
        for _, _, _, tag in ordered_tags(request)
        if not (only_unpinned and tag.pinned_tag_data is not None)
    ]
    if not tags:
        return 0
    rows = {row["tag_id"]: row for row in resolve_tags_live(db, request, tags)}
    now = datetime.utcnow()
    pinned = 0
    for tag in tags:
        row = rows.get(tag.id)
        if row is None:
            continue
        tag.pinned_tag_data = pin_payload(row)
        tag.pinned_at = now
        tag.data_change_ack_hash = None
        pinned += 1
    if pinned:
        db.flush()
    return pinned


__all__ = [
    "STAFF_VIEWER",
    "data_hash",
    "diff_pin_against_live",
    "ordered_tags",
    "resolve_tags_live",
    "resolve_version_line_data",
    "pin_tags",
    "pin_payload",
    "dimensions_text",
    "format_dimensions_mm",
    "get_product",
    "get_product_set",
    "product_set_tag_data",
    "product_specs",
    "product_tag_data",
    "resolve_request_line_data",
    "search_products",
    "search_product_sets",
    "spec_lines",
    "staff_viewer",
]
