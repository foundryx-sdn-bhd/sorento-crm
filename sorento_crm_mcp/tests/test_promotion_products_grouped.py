"""Owner Q4 option (a), 12 Oct 2026: the promotion-products presenter groups lines under their promotion.

Card `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 8 (answer shape: one row
per product line under its promotion: code, name, promo price). Owner rule: the reply states only
what the data holds.

Rows use the backend's real keys (`PromotionProductResponse`): `promotion_price` is the promo
selling price, `promotion.id/description` the parent. Today `_promotion_products` makes one flat item
per line and repeats the promotion description on every one, and it reads `selling_price`, a key the
route never sends, so the price is lost. Assertions read the whole envelope (as JSON text) because
the brief fixes the content, not the field layout.
"""
import json

import pytest

from sorento_crm_mcp.presenters import present_response

TOOL = "crm_marketing_promotion_products_list"
PAGING = {"pagination": {"total": 3, "page": 1, "limit": 50}}


def _line(promo_id, promo_desc, code, name, price=None, **promotion_extra):
    row = {
        "id": f"line-{code}",
        "promotion_id": promo_id,
        "product_id": f"prod-{code}",
        "product": {"product_code": code, "product_name": name},
        "promotion": {"id": promo_id, "description": promo_desc, **promotion_extra},
    }
    if price is not None:
        row["promotion_price"] = price
    return row


def envelope(rows):
    return json.loads(present_response(TOOL, json.dumps({"data": rows, **PAGING})))


def text(rows) -> str:
    return json.dumps(envelope(rows), ensure_ascii=False)


def test_each_promotion_appears_once_with_all_its_product_lines():
    """RED: the promotion description is repeated on every line (3 times here for A)."""
    out = text(
        [
            _line("pa", "ZZT PROMO ALPHA", "SK-1", "Sink One", "120.50"),
            _line("pa", "ZZT PROMO ALPHA", "SK-2", "Sink Two", "99.00"),
            _line("pb", "ZZT PROMO BETA", "TP-1", "Tap One", "45.00"),
        ]
    )
    assert out.count("ZZT PROMO ALPHA") == 1, out
    assert out.count("ZZT PROMO BETA") == 1, out
    for needle in ("SK-1", "Sink One", "SK-2", "Sink Two", "TP-1", "Tap One"):
        assert needle in out, (needle, out)


def test_every_line_shows_its_promo_selling_price():
    """RED: the presenter reads `selling_price`, which the route never sends; the real key
    `promotion_price` is dropped, so no price prints."""
    out = text([_line("pa", "ZZT PROMO ALPHA", "SK-1", "Sink One", "120.50"), _line("pa", "ZZT PROMO ALPHA", "SK-2", "Sink Two", "99.00")])
    assert "120.50" in out and "99.00" in out, out


def test_a_line_without_a_price_prints_no_price_text():
    """Owner rule: only what the data holds. A row with no price, no list price and no
    dimensions renders no price label at all (may pass today only because the price is dropped)."""
    out = text([_line("pa", "ZZT PROMO ALPHA", "SK-1", "Sink One")]).lower()
    assert "sk-1" in out and "sink one" in out
    assert "price" not in out, out
    assert "rm" not in out.replace("zzt promo alpha", "").replace("sink one", ""), out


def test_dates_print_only_when_the_promotion_carries_them():
    """RED on the first half: dates in the data are dropped today."""
    with_dates = text(
        [_line("pa", "ZZT PROMO ALPHA", "SK-1", "Sink One", "120.50", start_date="2026-10-01", end_date="2026-10-31")]
    )
    assert any(f in with_dates for f in ("2026-10-01", "01/10/2026", "1 Oct 2026", "01 Oct 2026")), with_dates
    assert any(f in with_dates for f in ("2026-10-31", "31/10/2026", "31 Oct 2026")), with_dates

    without = text([_line("pa", "ZZT PROMO ALPHA", "SK-1", "Sink One", "120.50")])
    assert "2026" not in without, without


def test_a_promotion_with_no_lines_invents_nothing():
    """Guard: an empty response says no match and names no promotion."""
    out = envelope([])
    assert out["has_result"] is False
    assert out["items"] == []


@pytest.mark.parametrize("n", [1, 3])
def test_line_count_matches_the_rows(n):
    """The grouping must not drop or invent product lines: n rows, n product codes."""
    rows = [_line("pa", "ZZT PROMO ALPHA", f"SK-{i}", f"Sink {i}", "10.00") for i in range(n)]
    out = text(rows)
    for i in range(n):
        assert f"SK-{i}" in out
    assert f"SK-{n}" not in out
