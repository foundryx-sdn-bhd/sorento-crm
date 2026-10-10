"""Ended promotions keep pricing tiles and tags inside their TYPE's grace.

PRICE-TAG-A3-EXPIRED (owner ruling 10 Oct 2026). Four Sorento A3 flyers ended
30 Sep, the hourly window task switched them off (`is_active=f`), and ~787
products whose only SP lived on them flipped to list price on every price tag.

The rule is `promotion_serving`'s, read from the type row, never re-coded:

* A3 flyer: usable 180 days after `end_date`.
* Standard / PP / focus: usable until 31 Dec of the year it ended.
* Special: never.

Plus, for money: live beats ended for the same product, the latest ended row
of a type wins, audience is filtered BEFORE that ranking, a saved line keeps
its promotion while it is inside its grace, the catalogue tile and the tag
agree, and the sheet export only refuses after the grace.

"Today" is pinned to 10 Oct 2026 so every bound is exact, not relative to the
day CI happens to run.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

import app.services.dealer_kit.pricing as pricing_module
from app.services.dealer_kit.pricing import line_pricing, resolve_prices
from app.services.dealer_kit.viewer import ViewerContext

from tests._pg_fixture import blank_session, unique_code

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

SORENTO = "00000000-0000-0000-0000-000000000001"
TODAY = date(2026, 10, 10)
SEP_30 = date(2026, 9, 30)

DEALER = ViewerContext(access_codes=frozenset({"dealer"}))


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(pricing_module, "business_today", lambda: TODAY)
    with blank_session() as session:
        yield session


@pytest.fixture
def types(db):
    """The seeded types of migration 361, as data (CI's database has none)."""
    from app.models.marketing import PromotionType

    rows = {
        "standard": PromotionType(
            type_code="standard",
            type_name="Standard Promo",
            show_expired=True,
            expired_valid_until_year_end=True,
            is_default=True,
            match_priority=99,
        ),
        "special": PromotionType(
            type_code="special",
            type_name="Special Promo",
            show_expired=False,
            match_priority=10,
        ),
        "a3_flyer": PromotionType(
            type_code="a3_flyer",
            type_name="A3 Flyer",
            show_expired=True,
            expired_max_age_days=180,
            match_priority=40,
        ),
    }
    db.add_all(rows.values())
    db.flush()
    return rows


def _product(db, *, list_price: str):
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    stem = unique_code("A3X")
    category = ProductCategory(id=_uid(), category_code=stem, category_name=f"ZZT cat {stem}")
    brand = Brand(id=_uid(), brand_code=stem[:50], brand_name=f"ZZT {stem}")
    uom = UnitOfMeasure(id=_uid(), uom_code=stem[:20], uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=_uid(),
        company_id=SORENTO,
        product_code=stem,
        product_name=f"ZZT {stem}",
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=Decimal(list_price),
        currency="MYR",
        is_active=True,
        is_discontinued=False,
    )
    db.add(product)
    db.flush()
    return product


def _promotion(
    db,
    lines: list[tuple],
    *,
    promo_type=None,
    end: date,
    start: date | None = None,
    is_active: bool | None = None,
    access_levels: list[str] | None = None,
    description: str | None = None,
):
    """``lines`` is (product, promo_selling_price). ``is_active`` defaults to
    what the hourly window task leaves behind: off once ``end`` has passed."""
    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    promotion = Promotion(
        id=_uid(),
        description=description or unique_code("ZZT promo"),
        start_date=start or (end - timedelta(days=180)),
        end_date=end,
        is_active=(end >= TODAY) if is_active is None else is_active,
        access_levels=access_levels or ["dealer", "end_user"],
        promotion_type_id=promo_type.id if promo_type is not None else None,
        company_id=SORENTO,
    )
    db.add(promotion)
    db.flush()
    group = PromotionGroup(promotion_id=promotion.id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    for product, price in lines:
        db.add(
            PromotionProduct(
                id=_uid(),
                promotion_id=promotion.id,
                promotion_group_id=str(group.id),
                product_id=product.id,
                promo_selling_price=Decimal(price),
                company_id=SORENTO,
            )
        )
    db.flush()
    return promotion


def _price(db, product, *, promotion_id=None, viewer=DEALER, auto_pick=True):
    return line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": product.id,
                "part_product_ids": [],
                "candidate_product_ids": [],
                "promotion_id": promotion_id,
            }
        ],
        viewer=viewer,
        auto_pick=auto_pick,
    )[0]


# --------------------------------------------------------------------------- A3


def test_ended_a3_flyer_still_prices_the_line_and_says_when_it_ended(db, types):
    """The incident: SRT1000-CR, list 84, SP 69 only on the flyer that ended
    30 Sep and was switched off by the window task."""
    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    assert flyer.is_active is False, "seed: the window task has already switched it off"

    row = _price(db, product)

    assert row["auto_promotion_id"] == flyer.id
    assert row["sell_price"] == Decimal("69.00")
    assert row["sell_price_basis"] == "promotion"
    [option] = row["promotion_options"]
    assert option["expired_on"] == SEP_30


def test_a3_grace_is_the_types_180_days_inclusive(db, types):
    product = _product(db, list_price="84.00")
    day_180 = _promotion(
        db, [(product, "69.00")], promo_type=types["a3_flyer"], end=TODAY - timedelta(days=180)
    )
    row = _price(db, product)
    assert row["auto_promotion_id"] == day_180.id
    assert row["sell_price"] == Decimal("69.00")

    day_180.end_date = TODAY - timedelta(days=181)
    db.flush()
    row = _price(db, product)
    assert row["promotion_options"] == []
    assert row["sell_price"] == Decimal("84.00")
    assert row["sell_price_basis"] == "list"


# ------------------------------------------------------------- the other types


def test_standard_promo_prices_a_tag_until_the_end_of_the_year_it_ended(db, types):
    product = _product(db, list_price="500.00")
    standard = _promotion(
        db, [(product, "400.00")], promo_type=types["standard"], end=date(2026, 6, 30)
    )
    assert _price(db, product)["sell_price"] == Decimal("400.00")

    standard.end_date = date(2025, 12, 31)
    db.flush()
    assert _price(db, product)["sell_price"] == Decimal("500.00")


def test_unclassified_promotion_follows_the_default_type(db, types):
    product = _product(db, list_price="500.00")
    _promotion(db, [(product, "400.00")], promo_type=None, end=date(2026, 6, 30))
    assert _price(db, product)["sell_price"] == Decimal("400.00")


def test_special_promo_never_prices_after_its_end_date(db, types):
    product = _product(db, list_price="500.00")
    _promotion(db, [(product, "400.00")], promo_type=types["special"], end=TODAY - timedelta(days=1))
    row = _price(db, product)
    assert row["promotion_options"] == []
    assert row["sell_price"] == Decimal("500.00")


def test_switched_off_inside_its_window_is_still_off(db, types):
    """`is_active` is ignored only once the end date has passed. Pulled early
    is somebody pulling the offer."""
    product = _product(db, list_price="500.00")
    _promotion(
        db,
        [(product, "400.00")],
        promo_type=types["a3_flyer"],
        end=TODAY + timedelta(days=10),
        is_active=False,
    )
    assert _price(db, product)["promotion_options"] == []


# ------------------------------------------------------------ ranking rules


def test_live_promotion_beats_a_cheaper_ended_flyer_for_the_same_product(db, types):
    product = _product(db, list_price="84.00")
    ended = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    live = _promotion(
        db, [(product, "72.00")], promo_type=types["standard"], end=TODAY + timedelta(days=30)
    )

    row = _price(db, product)
    assert row["auto_promotion_id"] == live.id
    assert row["sell_price"] == Decimal("72.00")
    assert ended.id not in {option["id"] for option in row["promotion_options"]}


def test_auto_pick_prefers_a_live_option_over_a_cheaper_ended_one_on_a_combo_line(db, types):
    """Two products on one line: the live promotion covers the parent, the
    ended flyer covers only the part. Both are offered (each covers something
    the other does not), and the ended one even totals lower, but live wins
    the auto-pick; the ended one stays selectable."""
    parent = _product(db, list_price="500.00")
    part = _product(db, list_price="300.00")
    live = _promotion(
        db, [(parent, "480.00")], promo_type=types["standard"], end=TODAY + timedelta(days=30)
    )
    ended = _promotion(db, [(part, "200.00")], promo_type=types["a3_flyer"], end=SEP_30)

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [part.id],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=DEALER,
    )[0]

    totals = {option["id"]: option["sell_price"] for option in row["promotion_options"]}
    assert totals == {live.id: Decimal("780.00"), ended.id: Decimal("700.00")}
    assert row["auto_promotion_id"] == live.id
    assert row["sell_price"] == Decimal("780.00")


def _line(db, parent, part, *, viewer=DEALER):
    return line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [part.id],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=viewer,
    )[0]


def test_a_live_flyer_on_the_part_does_not_hide_the_ended_flyer_on_the_parent(db, types):
    """Review B1: the same-type suppression is per PRODUCT. October's live A3
    covers only the part; September's ended A3 is the parent's only offer."""
    parent = _product(db, list_price="500.00")
    part = _product(db, list_price="300.00")
    ended = _promotion(db, [(parent, "200.00")], promo_type=types["a3_flyer"], end=SEP_30)
    live = _promotion(
        db, [(part, "290.00")], promo_type=types["a3_flyer"], end=TODAY + timedelta(days=60)
    )

    totals = {o["id"]: o["sell_price"] for o in _line(db, parent, part)["promotion_options"]}
    assert totals == {live.id: Decimal("790.00"), ended.id: Decimal("500.00")}


def test_a_newer_ended_flyer_on_the_part_does_not_hide_the_older_one_on_the_parent(db, types):
    parent = _product(db, list_price="500.00")
    part = _product(db, list_price="300.00")
    august = _promotion(
        db, [(parent, "200.00")], promo_type=types["a3_flyer"], end=date(2026, 8, 31)
    )
    september = _promotion(db, [(part, "250.00")], promo_type=types["a3_flyer"], end=SEP_30)

    ids = {o["id"] for o in _line(db, parent, part)["promotion_options"]}
    assert ids == {august.id, september.id}


def test_staff_viewer_is_offered_each_audiences_latest_flyer(db, types):
    """Review S1: the CRM prices with the staff viewer, which sees every
    audience. A dealer flyer must not be ranked away by an end-user flyer
    that ended later, nor a third same-day flyer by the tie cap of two."""
    from app.services.dealer_kit.tag_data_service import staff_viewer

    product = _product(db, list_price="84.00")
    dealer = _promotion(
        db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30,
        access_levels=["dealer"],
    )
    consumer = _promotion(
        db, [(product, "75.00")], promo_type=types["a3_flyer"], end=date(2026, 10, 5),
        access_levels=["end_user"],
    )
    both = _promotion(
        db, [(product, "72.00")], promo_type=types["a3_flyer"], end=SEP_30,
    )

    ids = {o["id"] for o in _price(db, product, viewer=staff_viewer())["promotion_options"]}
    assert ids == {dealer.id, consumer.id, both.id}


def test_with_no_types_configured_an_ended_promotion_never_prices(db):
    """Money fails closed where the chatbot's fallback serves everything."""
    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], end=SEP_30)
    row = _price(db, product)
    assert row["promotion_options"] == []
    assert resolve_prices(db, [product], DEALER, flyer.id)[product.id].offer_price is None


def test_among_ended_flyers_the_latest_end_date_wins(db, types):
    product = _product(db, list_price="84.00")
    older = _promotion(
        db, [(product, "60.00")], promo_type=types["a3_flyer"], end=date(2026, 8, 31)
    )
    newer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)

    row = _price(db, product)
    ids = {option["id"] for option in row["promotion_options"]}
    assert ids == {newer.id}, "the older flyer is history, even though it is cheaper"
    assert older.id not in ids


def test_audience_is_filtered_before_the_latest_wins(db, types):
    """A DEALER flyer ending 30 Sep must not lose to an OFFICE-only flyer
    ending later that a dealer cannot see."""
    product = _product(db, list_price="84.00")
    dealer_flyer = _promotion(
        db,
        [(product, "69.00")],
        promo_type=types["a3_flyer"],
        end=SEP_30,
        access_levels=["dealer"],
    )
    _promotion(
        db,
        [(product, "65.00")],
        promo_type=types["a3_flyer"],
        end=date(2026, 10, 5),
        access_levels=["end_user"],
    )

    row = _price(db, product, viewer=DEALER)
    assert row["auto_promotion_id"] == dealer_flyer.id
    assert row["sell_price"] == Decimal("69.00")


def test_a_saved_line_keeps_its_flyer_while_it_is_inside_its_grace(db, types):
    """Owner Q2 (a): a newer flyer does not silently reprice a saved line."""
    product = _product(db, list_price="84.00")
    saved = _promotion(
        db, [(product, "60.00")], promo_type=types["a3_flyer"], end=date(2026, 8, 31)
    )
    _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)

    row = _price(db, product, promotion_id=saved.id, auto_pick=False)
    assert row["sell_price"] == Decimal("60.00")
    assert row["sell_price_basis"] == "promotion"
    assert saved.id in {option["id"] for option in row["promotion_options"]}


def test_a_saved_line_past_its_grace_prices_at_list(db, types):
    product = _product(db, list_price="84.00")
    saved = _promotion(
        db, [(product, "60.00")], promo_type=types["a3_flyer"], end=date(2026, 3, 31)
    )
    row = _price(db, product, promotion_id=saved.id, auto_pick=False)
    assert row["sell_price"] == Decimal("84.00")
    assert row["sell_price_basis"] == "list"


# ------------------------------------------------- one answer, every surface


def test_catalogue_tile_shows_the_same_sp_as_the_tag(db, types):
    """Owner Q1: unified. `resolve_prices` (every catalogue, bundle and
    collection tile, and the tag render) answers what `line_pricing` does."""
    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)

    tile = resolve_prices(db, [product], DEALER, flyer.id)[product.id]
    line = _price(db, product)

    assert tile.offer_price == Decimal("69.00")
    assert tile.promotion_id == flyer.id
    assert line["sell_price"] == tile.offer_price


def test_catalogue_tile_drops_the_offer_after_the_grace(db, types):
    product = _product(db, list_price="84.00")
    flyer = _promotion(
        db, [(product, "69.00")], promo_type=types["a3_flyer"], end=TODAY - timedelta(days=181)
    )
    tile = resolve_prices(db, [product], DEALER, flyer.id)[product.id]
    assert tile.offer_price is None
    assert tile.promotion_id is None


def test_tag_render_prints_the_ended_flyers_sp(db, types):
    from app.services.dealer_kit import tag_data_service

    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    request = _request(db, product=product, promotion_id=flyer.id)

    [row] = tag_data_service.resolve_tags_live(db, request)
    assert row["sell_price"] == Decimal("69.00")
    assert row["sell_price_basis"] == "promotion"
    assert row["show_promo_price"] is True


def test_route_serialises_expired_on(db, types):
    """`response_model` drops an undeclared field silently (LESSONS-LEARNT)."""
    from app.schemas.price_tag import LinePricingRow

    product = _product(db, list_price="84.00")
    _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    payload = LinePricingRow(**_price(db, product)).model_dump(mode="json")
    assert payload["promotion_options"][0]["expired_on"] == "2026-09-30"


def test_request_line_reports_when_its_promotion_ended(db, types):
    """Review S2: the portal's read-only line labels the saved promotion too."""
    from app.services.price_tag_request_service import PriceTagRequestService

    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    request = _request(db, product=product, promotion_id=flyer.id)

    response = PriceTagRequestService.response_with_resolved_lines(db, request)
    [line] = response.lines
    assert line.promotion_name == flyer.description
    assert line.promotion_expired_on == SEP_30
    assert response.model_dump(mode="json")["lines"][0]["promotion_expired_on"] == "2026-09-30"


# ------------------------------------------------------------------ export


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(
        id=_uid(), phone_number=f"+60{uuid.uuid4().hex[:9]}", name=unique_code("contact")
    )
    db.add(contact)
    db.flush()
    return contact


def _request(db, *, product, promotion_id):
    from app.services.price_tag_request_service import PriceTagRequestService

    request = PriceTagRequestService.submit_request(
        db,
        contact_id=_contact(db).id,
        company_id=SORENTO,
        data={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": product.id,
                    "quantity": 1,
                    "promotion_id": promotion_id,
                }
            ],
        },
    )
    db.flush()
    return request


def test_export_allows_an_ended_flyer_inside_its_grace(db, types):
    from app.services.dealer_kit.tag_sheet_export_service import _check_promotion_expired

    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    request = _request(db, product=product, promotion_id=flyer.id)

    _check_promotion_expired(db, request)  # no 409


def test_export_refuses_a_flyer_past_its_grace(db, types):
    from app.services.dealer_kit.tag_sheet_export_service import _check_promotion_expired
    from app.services.error_handler import AppException

    product = _product(db, list_price="84.00")
    flyer = _promotion(db, [(product, "69.00")], promo_type=types["a3_flyer"], end=SEP_30)
    request = _request(db, product=product, promotion_id=flyer.id)
    flyer.end_date = TODAY - timedelta(days=181)
    db.flush()

    with pytest.raises(AppException) as excinfo:
        _check_promotion_expired(db, request)
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "PROMOTION_EXPIRED"
