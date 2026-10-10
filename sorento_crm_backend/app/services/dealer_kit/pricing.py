"""What THIS viewer pays for these products. The only answer to that question.

ADR 0008. The Kit turns a price into something a reader sees on a catalogue
tile, in a quote line, in a bundle heading and in a bundle's saving against
list. Four readers, none of which knew promotions existed, and adding "a dealer
sees the promo price while the promotion is live" to each of them separately
means four commercial rules drifting apart until two screens quote one product
at two prices. So it lives here, once, and every surface calls it.

Three things this owes its callers:

* **Decimal end to end.** No float arithmetic anywhere in here, and nothing
  formatted: a caller receives numbers it renders, never numbers it computes.
  Formatting happens at the edge, once.
* **A price a viewer may not see is ABSENT, not hidden.** A dealer-only offer
  resolves to ``offer_price=None`` AND ``promotion_id=None`` for a consumer, so
  neither the figure nor the fact that an offer exists can be recovered from the
  payload.
* **One query for the whole page.** ``resolve_prices`` takes a LIST for the same
  reason ``primary_image_urls`` does - a per-tile lookup turns a forty product
  catalogue into forty round trips.

The brochure decides WHICH promotion (a page carries at most one, an editorial
choice). The viewer decides WHETHER it applies: access levels plus the date
window, resolved here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.marketing import Promotion, PromotionProduct
from app.models.product import Product
from app.services import promotion_serving, promotion_window
from app.services.dealer_kit.product_images import PUBLIC_ACCESS_CODE
from app.services.dealer_kit.viewer import ViewerContext

# The company sells in Malaysia, so a promotion advertised as running "until the
# 31st" ends at the end of the Malaysian day. Deriving the day from the server's
# clock would end every offer eight hours early on a UTC host.
_MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")

# Every product row carries a currency (NOT NULL, defaulted), but an unsaved or
# part-built row handed in by a caller may not, and a tile still has to label a
# column. Matches the fallback the tile and quote resolvers already use.
DEFAULT_CURRENCY = "MYR"


@dataclass(frozen=True)
class PriceView:
    """What one product costs this reader, and why.

    ``list_price`` is reported alongside ``offer_price`` rather than replaced by
    it: the tile strikes the list price through beside the offer, and a caller
    that only received the offer could not show what the reader is saving.

    ``promotion_id`` is present only when an offer is - it answers the support
    question "why is this customer being quoted 79", and naming a promotion to a
    reader who does not get it would hand them the id of an offer they cannot
    have.
    """

    currency: str
    list_price: Optional[Decimal]
    offer_price: Optional[Decimal]
    invoice_price: Optional[Decimal]
    promotion_id: Optional[str]


def business_today() -> date:
    """The calendar day a promotion window is measured against."""
    return datetime.now(timezone.utc).astimezone(_MY_TZ).date()


def promotion_may_price(db: Session, promotion: Promotion, today: Optional[date] = None) -> bool:
    """Whether ``promotion`` may price a tile or a tag today, judged on its own.

    Live, or ended but still inside its TYPE's expired bound (owner ruling
    10 Oct 2026, PRICE-TAG-A3-EXPIRED): an A3 flyer for 180 days, a standard,
    PP or focus-item promotion until the end of the year it ended in, a special
    never. The bound is ``promotion_serving``'s, read from the type row, so the
    chatbot and the price on a tag cannot disagree about whether an ended
    offer still counts.

    ``is_active`` is NOT the test once the end date has passed: the hourly
    ``promotion_active_window`` task switches every promotion off the day after
    it ends (``marketing_service.sync_promotion_active_by_calendar_window``), so
    an ended promotion is always ``is_active=f`` and the type decides. Switched
    off INSIDE its window is still off: that is somebody pulling an offer.
    """
    today = today or business_today()
    if promotion_window.is_live(
        promotion.is_active, promotion.start_date, promotion.end_date, today
    ):
        return True
    types_by_id, default = promotion_serving.load_types(db)
    verdict = promotion_serving.evaluate([promotion], types_by_id, default, today)
    return _ended_may_price(verdict, promotion.id)


def _ended_may_price(verdict: promotion_serving.ServingVerdict, promotion_id: str) -> bool:
    """Whether the serving verdict lets this ENDED promotion still price.

    One difference from the chatbot, and only for money: with no type at all
    (no types configured, so no default either) the serving rule serves every
    ended row, keeping the bot's old answer. A price fails closed instead, the
    way it did before types existed.
    """
    return (
        verdict.is_expired_but_usable(promotion_id)
        and verdict.type_by_promotion.get(str(promotion_id)) is not None
    )


def _as_decimal(value) -> Optional[Decimal]:
    """Money, or nothing. Never a float.

    Numeric columns already arrive as Decimal; the coercion is for rows a caller
    built by hand, where an int or a float would otherwise pass straight through
    into somebody's arithmetic and land a quote total a cent out.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _may_see_offer(levels: Optional[Sequence[str]], viewer: ViewerContext) -> bool:
    """Whether this viewer is one of the audiences the promotion is priced for.

    Two deliberate readings, both differing from how imagery is gated:

    **An empty ``access_levels`` reaches nobody.** ``promotions.access_levels``
    is NOT NULL and ships defaulted to both audiences, so an empty list is
    somebody deliberately emptying it rather than a legacy row that predates
    tagging. Guessing wrong here prices a sale, so it fails closed.
    ``product_attachments.access_levels`` is treated the opposite way for the
    opposite reason: untagged photos there are legacy, and hiding all of them
    would empty the catalogue to protect nothing.

    **Staff do not bypass it.** ``is_staff`` decides the invoice price and
    nothing else. Staff checking the page they are about to send a customer must
    see the customer's price, or the preview is a lie about the page and the
    quote given over the phone disagrees with the one on screen. Seeing the
    trade price is what previewing AS a dealer is for: it changes the access
    codes, which is the one knob that decides an offer.
    """
    if not levels:
        return False

    # An INTERNAL COPY is the brochure, so it carries the brochure's own offers
    # whoever they were aimed at. An office PDF that dropped them would be
    # printed, read, and quoted from, and the figure would disagree with the page
    # the customer is looking at. Deliberately NOT keyed on is_staff: staff
    # previewing what a consumer will see must still see the consumer's price.
    if viewer.is_internal_copy:
        return True

    # The public catalogue is the consumer-facing surface, so a reader who
    # brought no code counts as a consumer - exactly as they do for imagery.
    codes = set(viewer.access_codes) or {PUBLIC_ACCESS_CODE}
    return bool(codes & set(levels))


def _offer_prices(
    db: Session,
    product_ids: Sequence[str],
    viewer: ViewerContext,
    promotion_id: str,
) -> dict[str, Decimal]:
    """``{product_id: promo price}`` for the products this offer covers today.

    ONE statement for the whole page. An offer that may not price today
    (``promotion_may_price``: not started, switched off, ended beyond its
    type's bound), an unknown one or a wrong-audience one simply yields
    nothing, which is what makes "no applicable offer" and "the promotion
    ended mid-session" the same code path: the list price, with no offer
    styling.
    """
    today = business_today()

    rows = (
        db.query(PromotionProduct, Promotion)
        .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
        .filter(PromotionProduct.promotion_id == promotion_id)
        .filter(PromotionProduct.product_id.in_(list(product_ids)))
        .all()
    )
    # Same promotion on every row: judged once. Inclusive at both ends and
    # unbounded on a blank side (`promotion_window`), so a promotion running
    # "until the 31st" still honours its price on the 31st.
    if rows and not promotion_may_price(db, rows[0][1], today):
        return {}

    offers: dict[str, Decimal] = {}
    for offer, promotion in rows:
        # Same promotion on every row, so this drops all of them or none. Kept
        # per row rather than hoisted because the invariant is then enforced
        # rather than assumed.
        if not _may_see_offer(promotion.access_levels, viewer):
            continue

        # A row with no price states no price. The promotion editor allows one
        # that records only a discount percent, and treating that as an offer
        # would leave a blank where a number belongs.
        price = _as_decimal(offer.promo_selling_price)
        if price is None:
            continue

        # One promotion can list a product in more than one group - a bundle
        # group and a headline group, say - and whichever we pick has to be the
        # same every time, or two screens quote two prices for one product. The
        # lower one wins: it is already published to this reader on this
        # promotion, so quoting the higher figure is indefensible at the
        # counter. Equal prices are the same number, so ties do not matter.
        current = offers.get(offer.product_id)
        if current is None or price < current:
            offers[offer.product_id] = price

    return offers


def resolve_prices(
    db: Session,
    products: Iterable[Product],
    viewer: ViewerContext,
    promotion_id: Optional[str] = None,
) -> dict[str, PriceView]:
    """``{product_id: PriceView}`` for every product asked about.

    ``products`` are already company-scoped by the caller; the promotion lookup
    goes through the ordinary ORM scope filter, so another company's offer
    cannot price this company's page.

    Every product asked about is answered. Callers index the result while
    rendering a grid, so a missing key would be a KeyError mid-page: "no offer"
    is a PriceView with no ``offer_price``, never an absent entry.
    """
    products = list(products)
    if not products:
        return {}

    offers: dict[str, Decimal] = {}
    if promotion_id:
        # Deduplicated: the same product can legitimately appear twice on a page
        # (two tiles, or a quote listing it on two lines).
        product_ids = list({product.id for product in products})
        offers = _offer_prices(db, product_ids, viewer, promotion_id)

    # Both gates ANDed, resolved once: the DOCUMENT asks for the invoice price
    # and the VIEWER is entitled to it. The losing case omits the number rather
    # than sending it to be hidden.
    show_invoice = viewer.invoice_price_visible

    views: dict[str, PriceView] = {}
    for product in products:
        if product.id in views:
            continue
        list_price = _a_real_price(product.list_price)
        offer = _offer_worth_showing(offers.get(product.id), list_price)
        views[product.id] = PriceView(
            currency=product.currency or DEFAULT_CURRENCY,
            list_price=list_price,
            offer_price=offer,
            invoice_price=_a_real_price(product.invoice_price) if show_invoice else None,
            promotion_id=promotion_id if offer is not None else None,
        )

    return views


def _a_real_price(value) -> Optional[Decimal]:
    """The figure, unless it is the absence of one wearing a number's clothes.

    ``products.list_price`` is NOT NULL and defaults to zero, so a product whose
    price was never imported is stored identically to one deliberately priced at
    nothing. Those are not both real categories: a genuine giveaway is a
    promotion line priced against a promotion, not a permanent list price of
    nought. So zero means "we do not have a price for this", and the honest
    rendering of that is the one the system already has for a product with no
    price at all: nothing.

    The alternative was found by opening the published catalogue in a browser,
    where a tile on the page a dealer forwards to a customer printed MYR 0.00
    against a real product. Nothing is a statement about our data. MYR 0.00 is a
    claim about the price, and one somebody could reasonably act on.

    Not a rare shape either: 10,370 of 22,805 live products carry
    ``list_price = 0.00``, 46% of one company's catalogue and 45% of the other's.

    Negatives go the same way. Nobody is owed money for taking a bath away, and
    an import reading a spreadsheet column with the wrong sign produces them.
    """
    price = _as_decimal(value)
    if price is None or price <= 0:
        return None
    return price


def flyer_price(value) -> Optional[Decimal]:
    """A product's list price as the dealer flyer prints it: ``None`` when there is none.

    The public name of ``_a_real_price`` for callers outside the Kit that default a figure
    from it (sales opportunity lines, PR #1296 F7), so zero means "no price" there too.
    """
    return _a_real_price(value)


#: What a LINE total is rounded to (D2/D3/D4, S7). The engine sums several
#: products; this keeps the sum to two places the way `_a_real_price` keeps
#: one product's own figure to two places.
_LINE_MONEY = Decimal("0.01")


def _covering_promotions(
    db: Session,
    product_ids: Sequence[str],
    viewer: ViewerContext,
    *,
    keep_id: Optional[str] = None,
) -> list[Promotion]:
    """Every audience-visible promotion that may price ANY of ``product_ids``
    today (AC-S7-2).

    A promotion covering only a choice group's CANDIDATE still counts - the
    salesperson may pick that candidate, so the line genuinely benefits from
    it (D3/AC-S7-2).

    Live promotions, plus ended ones the serving rule still allows
    (PRICE-TAG-A3-EXPIRED). Which ended promotion is ``promotion_serving``'s
    call, not this function's: the type's bound, the latest end date of each
    type wins, and a live promotion of a type suppresses that type's ended
    ones. Audience goes FIRST, because that rule ranks the set it is given: a
    dealer must not lose the DEALER flyer to an OFFICE flyer ending later that
    the dealer cannot see.

    On top of it, live beats ended across types for the SAME product: an
    ended promotion is offered only when it covers a product no live
    promotion covers. ``keep_id`` is the line's saved promotion, kept whenever
    it may still price on its own: the salesperson committed to it, and a
    newer flyer appearing does not silently reprice a saved line.
    """
    if not product_ids:
        return []
    today = business_today()
    rows = (
        db.query(Promotion, PromotionProduct.product_id)
        .join(PromotionProduct, PromotionProduct.promotion_id == Promotion.id)
        .filter(PromotionProduct.product_id.in_(list(product_ids)))
        .filter(or_(Promotion.start_date.is_(None), Promotion.start_date <= today))
        .all()
    )
    promotions: dict[str, Promotion] = {}
    covers: dict[str, set[str]] = {}
    for promotion, product_id in rows:
        if not _may_see_offer(promotion.access_levels, viewer):
            continue
        promotions[promotion.id] = promotion
        covers.setdefault(promotion.id, set()).add(product_id)
    if not promotions:
        return []

    types_by_id, default = promotion_serving.load_types(db)
    verdict = promotion_serving.evaluate(
        list(promotions.values()), types_by_id, default, today
    )
    live_ids = verdict.served_ids - verdict.expired_but_usable_ids
    live_covered = set().union(*(covers[pid] for pid in live_ids)) if live_ids else set()

    chosen = []
    for promotion_id, promotion in promotions.items():
        if promotion_id in live_ids:
            chosen.append(promotion)
        elif _ended_may_price(verdict, promotion_id) and covers[promotion_id] - live_covered:
            chosen.append(promotion)
        elif promotion_id == keep_id and promotion_may_price(db, promotion, today):
            chosen.append(promotion)
    return chosen


def promotion_expired_on(promotion: Promotion, today: Optional[date] = None) -> Optional[date]:
    """The end date of a promotion that has ended, else ``None``.

    What the dropdown labels "(expired <date>)": a promotion that reaches a
    line after its end date only does so through its type's grace.
    """
    today = today or business_today()
    if promotion.end_date is not None and promotion.end_date < today:
        return promotion.end_date
    return None


def _line_total_under(
    db: Session,
    products_by_id: dict[str, Product],
    resolved_ids: Sequence[str],
    part_ids: Sequence[str],
    viewer: ViewerContext,
    promotion_id: str,
) -> tuple[Decimal, list[str]]:
    """The line's total under ONE promotion, and which PARTS printed at list.

    Sum of (this promotion's offer, when it is worth showing, else the list
    price) over the parent plus every RESOLVED part (D3) - the same rule
    ``_offer_or_list`` already encodes for a tag's own price, generalised to
    "which promotion" rather than always the line's one. ``resolved_ids``
    includes the parent; ``part_ids`` is the subset that is worth naming back
    to the caller when one of THEM specifically missed the offer (AC-S1-8) -
    the parent missing it is not "a part at list", it is the whole line not
    benefiting.
    """
    offers = _offer_prices(db, resolved_ids, viewer, promotion_id) if resolved_ids else {}
    total = Decimal("0")
    parts_at_list: list[str] = []
    for product_id in resolved_ids:
        product = products_by_id.get(product_id)
        list_price = _a_real_price(product.list_price) if product else None
        worth = _offer_worth_showing(offers.get(product_id), list_price)
        if worth is not None:
            total += worth
        else:
            total += list_price or Decimal("0")
            if product_id in part_ids:
                parts_at_list.append(product_id)
    return total.quantize(_LINE_MONEY), parts_at_list


def line_pricing(
    db: Session,
    lines: list[dict],
    viewer: ViewerContext,
    *,
    auto_pick: bool = True,
) -> list[dict]:
    """What EVERY line of a price tag request/form costs this viewer (D2-D4, S7).

    One call answers every line at once (D4) - the portal form and the CRM
    detail page's own routes are thin wrappers around this. Each input line is
    ``{key, product_id, part_product_ids, candidate_product_ids, promotion_id?,
    manual_sell_price?}``; each output row is ``{key, list_price,
    promotion_options, auto_promotion_id, sell_price, sell_price_basis,
    parts_at_list, candidates}`` per the plan's API contract.

    ``sell_price_basis`` (AC-S7-4): ``manual`` when a manual figure is given,
    ``promotion`` when a promotion prices the line, ``list`` otherwise - the
    SAME three-way answer ``_add_lines`` writes ``show_promo_price`` from
    (AC-S7-5) and ``resolve_tags_live`` reads back for a tag's own price
    (D3/AC-S9-3), so the three surfaces cannot disagree about what a line is
    worth.

    ``auto_pick`` is who the answer is FOR (owner ruling, review round 2).
    The LOOKUP routes exist so a salesperson can see what would apply before
    choosing, so with no ``promotion_id`` given they price under
    ``auto_promotion_id`` - that is the pre-fill the portal form writes onto
    the line. A SAVED line is the other question: its basis is what the
    salesperson actually committed to, so ``auto_pick=False`` prices it under
    its STORED ``promotion_id`` alone and a line saved with none is ``list``,
    however many promotions happen to cover its product. Without the split, a
    line nobody put a promotion on printed SP at a plain list total the moment
    any promotion touched the product - the defect D3 says it retires.
    ``auto_promotion_id`` is reported either way; only what is CHOSEN changes.
    """
    results: list[dict] = []
    for line in lines:
        key = line["key"]
        product_id = line.get("product_id")
        part_ids = [pid for pid in (line.get("part_product_ids") or []) if pid]
        candidate_ids = [pid for pid in (line.get("candidate_product_ids") or []) if pid]
        given_promotion_id = line.get("promotion_id")
        manual = line.get("manual_sell_price")

        resolved_ids = [pid for pid in [product_id, *part_ids] if pid]
        lookup_ids = list({*resolved_ids, *candidate_ids})
        products_by_id: dict[str, Product] = (
            {p.id: p for p in db.query(Product).filter(Product.id.in_(lookup_ids)).all()}
            if lookup_ids
            else {}
        )

        list_price = Decimal("0")
        for product_id_ in resolved_ids:
            product = products_by_id.get(product_id_)
            price = (_a_real_price(product.list_price) if product else None) or Decimal("0")
            list_price += price
        list_price = list_price.quantize(_LINE_MONEY)

        covering = _covering_promotions(
            db, lookup_ids, viewer, keep_id=given_promotion_id
        )
        today = business_today()
        promotion_options = []
        totals_by_promotion: dict[str, tuple[Decimal, list[str]]] = {}
        for promo in covering:
            total, parts_at_list_for_promo = _line_total_under(
                db, products_by_id, resolved_ids, part_ids, viewer, promo.id
            )
            totals_by_promotion[promo.id] = (total, parts_at_list_for_promo)
            promotion_options.append(
                {
                    "id": promo.id,
                    "description": promo.description or "",
                    "sell_price": total,
                    "expired_on": promotion_expired_on(promo, today),
                }
            )
        # R12 (security review): sorted on total ALONE, a tie's winner was
        # whatever order `_covering_promotions`' un-ordered `.distinct()`
        # query happened to return - the SAME two promotions could auto-pick
        # a different one from one call to the next. `description` then
        # `id` breaks the tie deterministically.
        # PRICE-TAG-A3-EXPIRED: a live option is auto-picked over an ended
        # one even when the ended flyer is cheaper - live always wins.
        promotion_options.sort(
            key=lambda option: (
                option["expired_on"] is not None,
                option["sell_price"],
                option["description"],
                option["id"],
            )
        )

        auto_promotion_id = promotion_options[0]["id"] if promotion_options else None
        chosen_id = given_promotion_id or (auto_promotion_id if auto_pick else None)
        chosen_total = totals_by_promotion.get(chosen_id) if chosen_id else None

        if manual is not None:
            sell_price = _as_decimal(manual)
            sell_price_basis = "manual"
            parts_at_list: list[str] = []
        elif chosen_total is not None:
            sell_price, parts_at_list = chosen_total
            # R11a (security H1): `chosen_total` legitimately equals
            # `list_price` when the only covering promotion prices a
            # CANDIDATE nobody has picked yet (AC-S7-2 counts that as
            # "covering" so it shows up as an option) - a line is not "on
            # sale" for a discount that lives on a product not actually on
            # it. `promotion` basis is reserved for an offer that beat list
            # on something RESOLVED (the parent or a fixed part).
            sell_price_basis = "promotion" if sell_price < list_price else "list"
        else:
            sell_price = list_price
            sell_price_basis = "list"
            parts_at_list = []

        # One lookup for every candidate on the line, not one per candidate
        # (reviewer finding, this round) - a five-candidate open group used
        # to cost five round trips for what `_offer_prices` already answers
        # in one, the same way the resolved side of this function does.
        candidate_offers = (
            _offer_prices(db, candidate_ids, viewer, chosen_id)
            if chosen_id and candidate_ids
            else {}
        )
        candidates_out = []
        for candidate_id in candidate_ids:
            product = products_by_id.get(candidate_id)
            c_list = _a_real_price(product.list_price) if product else None
            c_sell = c_list
            if chosen_id and product:
                worth = _offer_worth_showing(candidate_offers.get(candidate_id), c_list)
                c_sell = worth if worth is not None else c_list
            candidates_out.append(
                {"product_id": candidate_id, "list_price": c_list, "sell_price": c_sell}
            )

        results.append(
            {
                "key": key,
                "list_price": list_price,
                "promotion_options": promotion_options,
                "auto_promotion_id": auto_promotion_id,
                "sell_price": sell_price,
                "sell_price_basis": sell_price_basis,
                "parts_at_list": parts_at_list,
                "candidates": candidates_out,
            }
        )
    return results


def _offer_worth_showing(
    offer: Optional[Decimal], list_price: Optional[Decimal]
) -> Optional[Decimal]:
    """The promotional figure, but only when it is actually an offer.

    A tile draws an offer as "list price struck through, offer prominent", so a
    promotion line priced at or above list renders as a crossed-out 285 beside a
    bold 1,099: the catalogue advertising a price nobody charges, presented as a
    discount. On the live database 422 of 1,178 promotion lines are at or above
    their product's list price and 20 are exactly zero.

    Those rows are not wrong as RECORDS. Promotions carry bundle and free-gift
    bookkeeping, and a line priced above list is meaningful inside a group where
    somebody is buying several things together. What is wrong is reading one of
    them as "the price of this product, alone, on its own tile", which is the
    only question this module answers.

    A product with no list price has nothing to be cheaper than, so the
    promotional figure stands on its own. The alternative would hide a product
    that is genuinely on offer because its master data is incomplete.
    """
    if offer is None or offer <= 0:
        return None
    if list_price is not None and offer >= list_price:
        return None
    return offer
