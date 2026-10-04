"""Golden-fixture coverage for the top X hot selling presenter (S1,
PLAN-chatbot-top-x-hot-selling-24sep.md / its UAC, AC-1901 to AC-1916, rewritten
for the owner's 26 Sep 2026 rulings on PR #1175; S4 moved the mocks onto the
route's own body as built on PR #1263 and wired the `present_response` dispatch).

`_top_selling` renders the WHOLE WhatsApp reply for a route body;
`_top_selling_envelope` wraps it with `result_type` / `has_result` the way
`_sales_report_envelope` does. The clarify and refusal lines are fixed
constants the lane sends before any fetch, so their goldens are `.txt` only.
Rows print the CODE only, never a name (owner, 26 Sep 2026: "don't need to show
name, just show code will do").

Fixture bytes live in TWO places for the reason `test_presenters_sales_report.py`
gives: `documentation/plans/chatbot/samples/top-selling-*` is the owner's review
copy, `tests/fixtures/top_selling/` is what the CI MCP container can see.
`test_documentation_samples_mirror_fixtures` keeps them byte-identical.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from sorento_crm_mcp.presenters import (
    PRESENTER_TOOLS,
    TOP_SELLING_ASK_BASIS,
    TOP_SELLING_ASK_GROUP,
    TOP_SELLING_ASK_METRIC,
    TOP_SELLING_N_CEILING,
    TOP_SELLING_REFUSED_OTHER_CUSTOMER,
    WHATSAPP_MESSAGE_MAX_CHARS,
    _top_selling,
    _top_selling_envelope,
    present_response,
    whatsapp_parts,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "top_selling"
_DOC_SAMPLES = Path(__file__).parent.parent.parent / "documentation" / "plans" / "chatbot" / "samples"

_BODY_GOLDENS = [
    "items-qty",
    "items-amount",
    "categories",
    "in-category",
    "customer",
    "channel",
    "agent",
    "how-many",
    "miss",
    "detail",
]
_FIXED_GOLDENS = {
    "clarify-metric": TOP_SELLING_ASK_METRIC,
    "clarify-group": TOP_SELLING_ASK_GROUP,
    "clarify-basis": TOP_SELLING_ASK_BASIS,
    "refused": TOP_SELLING_REFUSED_OTHER_CUSTOMER,
}
_ITEM_OFFER = "Reply with a rank number to see that item's customers and months."
_CATEGORY_OFFER = "Reply with a rank number to see that category's top items."


def _mock(name: str) -> dict:
    return json.loads((_FIXTURES / f"top-selling-{name}.json").read_text())


def _golden(name: str) -> str:
    return (_FIXTURES / f"top-selling-{name}.txt").read_text().rstrip("\n")


# --------------------------------------------------------------------------
# AC-1901 / AC-1909 / AC-1913: every golden, byte for byte
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", _BODY_GOLDENS)
def test_every_body_golden(name: str):
    assert _top_selling(_mock(name)) == _golden(name)


@pytest.mark.parametrize("name", sorted(_FIXED_GOLDENS))
def test_fixed_lines_match_goldens(name: str):
    assert _FIXED_GOLDENS[name] == _golden(name)


def test_items_by_quantity_header_order():
    """AC-1901: title, Ranked by, Basis, count, Customer, Category, Brand, Sales
    agent, Channel, Delivery date, in that order, then a blank line (Brand: fix lane
    round 4, owner retest 27 Sep 2026)."""
    lines = _top_selling(_mock("items-qty")).splitlines()
    assert lines[:11] == [
        "*Top 5 selling items*",
        "Ranked by: Quantity",
        "Basis: Delivered (transferred to DO)",
        "Items with sales: 1,284",
        "Customer: all",
        "Category: all",
        "Brand: all",
        "Sales agent: all",
        "Channel: all",
        "Delivery date: 01/01/2026 to 31/12/2026",
        "",
    ]


# --------------------------------------------------------------------------
# AC-1902 / AC-1914: row shapes for both grains
# --------------------------------------------------------------------------


def test_row_line_shape():
    """Owner, 26 Sep 2026: the code only, never the name, whether or not the route
    sends one."""
    rendered = _top_selling(_mock("items-qty"))
    assert "\n1. SRTWT7445: Qty 1,240, RM 86,400.00\n" in rendered
    assert "\n5. SRTWC5501: Qty 512, RM 143,360.00\n" in rendered
    assert "Kitchen Sink" not in rendered


def test_rank_number_is_the_bodys_own():
    body = _mock("items-qty")
    body["rows"] = body["rows"][:1]
    body["rows"][0]["rank"] = 7
    assert "\n7. SRTWT7445:" in _top_selling(body)


def test_category_rows():
    rendered = _top_selling(_mock("categories"))
    assert rendered.startswith("*Top 6 selling categories*\n")
    assert "Categories with sales: 6\n" in rendered
    assert "\n1. KS: Qty 4,210, RM 612,300.00\n" in rendered
    assert "KITCHEN SINK" not in rendered
    # a product with no category ranks under a null code: Unassigned
    assert "\n5. ACC: Qty 7,400, RM 0.00\n" in rendered
    assert "\n6. Unassigned: Qty 120, RM 3,600.00\n" in rendered


# --------------------------------------------------------------------------
# AC-1903 / AC-1904 / AC-1915: header axes and labels
# --------------------------------------------------------------------------


def test_absent_axis_prints_all():
    body = _mock("items-qty")
    body.pop("filters")
    for key in ("date_from", "date_to"):
        body.pop(key)
    rendered = _top_selling(body)
    for label in ("Customer", "Category", "Sales agent", "Channel", "Delivery date"):
        assert f"\n{label}: all\n" in rendered


def test_channel_prints_dealer_project_or_all():
    body = _mock("items-qty")
    body["filters"]["channel"] = "dealer"
    assert "\nChannel: Dealer\n" in _top_selling(body)
    assert "\nChannel: Project\n" in _top_selling(_mock("channel"))
    assert "\nChannel: all\n" in _top_selling(_mock("customer"))


def test_ranked_by_and_basis_labels():
    assert "\nRanked by: Quantity\n" in _top_selling(_mock("items-qty"))
    assert "\nRanked by: Amount\n" in _top_selling(_mock("items-amount"))
    assert "\nBasis: Delivered (transferred to DO)\n" in _top_selling(_mock("items-qty"))
    assert "\nBasis: Ordered\n" in _top_selling(_mock("channel"))


def test_title_follows_named_n_or_none():
    assert _top_selling(_mock("in-category")).startswith("*Top 3 selling items*\n")
    body = _mock("items-qty")
    body["n"] = None
    assert _top_selling(body).startswith("*Top selling items*\n")


def test_agent_fill_note():
    rendered = _top_selling(_mock("agent"))
    assert (
        "Sales agent: SEAN I\n"
        "Note: only 87% of sales orders in this period carry a sales agent.\n"
        "Channel: all\n"
    ) in rendered
    assert "Note:" not in _top_selling(_mock("items-qty"))


@pytest.mark.parametrize("rate, printed", [(0.87, True), (0.9449, True), (0.95, False), (1.0, False), (None, False)])
def test_agent_fill_note_threshold(rate, printed):
    """As built on PR #1263 the route sends `sales_agent_fill_rate` (0 to 1) whenever
    an agent filter is used; the presenter prints the note only below 95% (plan,
    "The reply")."""
    body = _mock("agent")
    body["sales_agent_fill_rate"] = rate
    assert ("Note: only" in _top_selling(body)) is printed


# --------------------------------------------------------------------------
# AC-1905 / AC-1907: cap and input order
# --------------------------------------------------------------------------


def test_never_prints_past_the_ceiling():
    """TOP-N-UNCAP (owner, 30 Sep 2026): no 100 cap, only the shared 1000 safety ceiling."""
    body = _mock("items-qty")
    body["n"] = TOP_SELLING_N_CEILING
    body["rows"] = [
        {"rank": i, "code": f"ZZ{i:04d}", "name": None, "quantity": 1, "amount": 1}
        for i in range(1, TOP_SELLING_N_CEILING + 6)
    ]
    rendered = _top_selling(body)
    assert "\n101. ZZ0101:" in rendered
    assert "\n1000. ZZ1000:" in rendered
    assert "ZZ1001" not in rendered


def test_presenter_keeps_input_order():
    body = _mock("items-qty")
    body["rows"] = list(reversed(body["rows"]))
    rendered = _top_selling(body)
    assert rendered.index("SRTWC5501") < rendered.index("SRTWT7445")


# --------------------------------------------------------------------------
# AC-1906 / AC-1911 / AC-1912: miss, how many, detail offer
# --------------------------------------------------------------------------


def test_miss_prints_no_offer():
    rendered = _top_selling(_mock("miss"))
    assert rendered.endswith("\n\nNo sales found.")
    assert "Reply" not in rendered
    envelope = _top_selling_envelope(_mock("miss"))
    assert envelope == {
        "result_type": "top_selling",
        "response": rendered,
        "has_result": False,
        "result_set": [],
    }


def test_how_many_reply():
    """No N named: the header states the full count and the bot asks how many (owner
    rulings 26 Sep). Not a message-size rule: a long list is split into messages
    (TOP-N-UNCAP), so the reply says nothing about length and the range it offers is 1
    to the smaller of the count and the 1000 safety ceiling (no 100 cap, 30 Sep)."""
    body = _mock("how-many")
    rendered = _top_selling(body)
    assert rendered.endswith(
        "\n\nHow many items do you want to see? Reply with a number from 1 to 1000."
    )
    assert "Items with sales: 1,284" in rendered
    assert "too long" not in rendered and "one message" not in rendered
    envelope = _top_selling_envelope(body)
    assert envelope["result_type"] == "top_selling_how_many"
    assert envelope["has_result"] is True

    body["group"] = "category"
    body["total_count"] = 6
    assert _top_selling(body).endswith(
        "How many categories do you want to see? Reply with a number from 1 to 6."
    )


@pytest.mark.parametrize(
    "name", ["items-qty", "items-amount", "in-category", "customer", "channel", "agent"]
)
def test_detail_offer_on_every_item_hit(name: str):
    rendered = _top_selling(_mock(name))
    assert rendered.endswith("\n\n" + _ITEM_OFFER)
    envelope = _top_selling_envelope(_mock(name))
    assert envelope["result_type"] == "top_selling"
    assert envelope["has_result"] is True


def test_detail_offer_on_category_hit():
    assert _top_selling(_mock("categories")).endswith("\n\n" + _CATEGORY_OFFER)


# --------------------------------------------------------------------------
# Owner, PR #1258 (26 Sep 2026 05:32Z): the ranked list is a list of choices that
# behaves like the customer and product pickers. The envelope carries one
# `result_set` row per PRINTED line, in the `{idx, label, code, entity_type}`
# roster shape (no name: owner ruling 26 Sep ~07:40Z), so the lane arms a sticky `top_selling_pick`
# (backend `turn/pending.py`) and a later "2" or a name resolves against it.
# --------------------------------------------------------------------------


def test_envelope_carries_one_pick_row_per_printed_item():
    envelope = _top_selling_envelope(_mock("items-qty"))
    assert envelope["result_set"] == [
        {"idx": 1, "label": "SRTWT7445", "code": "SRTWT7445", "entity_type": "product"},
        {"idx": 2, "label": "SRTKT39SS", "code": "SRTKT39SS", "entity_type": "product"},
        {"idx": 3, "label": "SRTBS1020", "code": "SRTBS1020", "entity_type": "product"},
        {"idx": 4, "label": "SRTSH2201", "code": "SRTSH2201", "entity_type": "product"},
        {"idx": 5, "label": "SRTWC5501", "code": "SRTWC5501", "entity_type": "product"},
    ]


def test_pick_rows_carry_no_name_key():
    """Owner ruling 26 Sep ~07:40Z (reviewer B1, PR #1273): the route sends no name on
    a ranked row, so the pick row the session stores carries none either, even when a
    stale body still has one."""
    body = _mock("items-qty")
    body["rows"][0]["name"] = "Kitchen Sink 2 Bowl"
    for row in _top_selling_envelope(body)["result_set"]:
        assert "name" not in row, row


def test_envelope_pick_rows_at_category_grain_use_the_printed_code():
    rows = _top_selling_envelope(_mock("categories"))["result_set"]
    assert [(r["idx"], r["label"], r["code"], r["entity_type"]) for r in rows] == [
        (1, "KS", "KS", "category"),
        (2, "WC", "WC", "category"),
        (3, "BM", "BM", "category"),
        (4, "KT", "KT", "category"),
        (5, "ACC", "ACC", "category"),
        (6, "Unassigned", None, "category"),
    ]


def test_pick_row_number_is_the_printed_rank():
    body = _mock("items-qty")
    body["rows"] = body["rows"][:1]
    body["rows"][0]["rank"] = 7
    assert [r["idx"] for r in _top_selling_envelope(body)["result_set"]] == [7]


@pytest.mark.parametrize("name", ["how-many", "miss", "detail"])
def test_no_pick_rows_when_no_list_was_printed(name: str):
    assert _top_selling_envelope(_mock(name))["result_set"] == []


# --------------------------------------------------------------------------
# AC-1908 / AC-1910: formats, dash guard, no paging words
# --------------------------------------------------------------------------

_BANNED = ("\u2014", "\u2013", "\u2192", "->", "=>")
_PAGING = re.compile(r"\b(more|next|lagi|showing)\b", re.IGNORECASE)


def test_money_qty_format():
    rendered = _top_selling(_mock("customer"))
    assert "Qty 180, RM 12,600.00" in rendered
    assert "RM 0.00" in rendered
    assert "RM 52,500.50" in _top_selling(_mock("items-qty"))


@pytest.mark.parametrize("name", _BODY_GOLDENS + sorted(_FIXED_GOLDENS))
def test_dash_guard_and_no_paging_words(name: str):
    text = _golden(name)
    for banned in _BANNED:
        assert banned not in text, f"{name} carries a banned character/sequence: {banned!r}"
    assert not re.search(r"-{2,}", text)
    assert not _PAGING.search(text), f"{name} carries a paging word"


def test_hostile_body_never_crashes():
    body = copy.deepcopy(_mock("items-qty"))
    body["rows"] = [{"rank": None, "code": None, "name": None, "quantity": "x", "amount": None}, "junk"]
    body["total_count"] = None
    body["filters"] = "junk"
    body["sales_agent_fill_rate"] = "junk"
    assert isinstance(_top_selling(body), str)
    body["detail"] = {"by_customer": "junk", "by_month": [None, {"month": "junk"}]}
    assert isinstance(_top_selling(body), str)


# --------------------------------------------------------------------------
# corpus drift guard (skips when documentation/ is not mounted, e.g. the CI mcp job)
# --------------------------------------------------------------------------


def test_documentation_samples_mirror_fixtures():
    if not _DOC_SAMPLES.exists():
        pytest.skip("documentation/ not present in this environment (CI mcp test container)")
    for path in _FIXTURES.iterdir():
        mirror = _DOC_SAMPLES / path.name
        assert mirror.exists(), f"{path.name} has no documentation/ mirror"
        assert mirror.read_bytes() == path.read_bytes(), f"{path.name} has drifted from its documentation/ mirror"


# --------------------------------------------------------------------------
# S4: the detail offer's answer, the count-only how-many body, and the dispatch
# --------------------------------------------------------------------------


def test_detail_reply_lists_customers_and_months_under_the_same_header():
    """AC-1964 presenter half: a rank number re-calls the route with `detail_code`,
    and the body's `detail` renders that code's customers and months under the same
    metric, basis and filter lines the ranking printed. Code only in the title."""
    rendered = _top_selling(_mock("detail"))
    assert rendered.startswith("*SRTWT7445: customers and months*\nRanked by: Quantity\n")
    assert "Kitchen Sink" not in rendered
    assert "\nTotal: Qty 1,240, RM 86,400.00\n" in rendered
    assert "*_By customer_*\n1. HANLIM TRADING SDN BHD: Qty 610, RM 42,700.00\n" in rendered
    assert "\n3. Unassigned: Qty 230, RM 15,900.00\n" in rendered
    assert "*_By month_*\nMar 2026: Qty 520, RM 36,300.00\n" in rendered
    envelope = _top_selling_envelope(_mock("detail"))
    assert envelope["result_type"] == "top_selling_detail"
    assert envelope["has_result"] is True


def test_detail_code_with_no_sales_is_the_miss():
    body = _mock("miss")
    body["detail"] = None
    envelope = _top_selling_envelope(body)
    assert envelope["has_result"] is False
    assert envelope["response"].endswith("\n\nNo sales found.")


def test_count_only_single_row_is_sent_as_is():
    """Owner, PR #1258 05:32Z: with no N named the bot asks how many, except a single
    row, which is sent (nothing to choose between). The route keeps that one row on
    a `count_only` call, so the presenter prints it as an ordinary ranking."""
    body = _mock("in-category")
    body["n"] = None
    body["rows"] = body["rows"][:1]
    body["total_count"] = 1
    rendered = _top_selling(body)
    assert rendered.startswith("*Top selling items*\n")
    assert "\n1. SRTWT7445: Qty 1,240, RM 86,400.00\n" in rendered
    assert "How many" not in rendered


def test_present_response_dispatches_the_top_selling_envelope():
    assert "crm_top_selling_report" in PRESENTER_TOOLS
    for name in ("items-qty", "how-many", "miss", "detail"):
        body = _mock(name)
        assert json.loads(present_response("crm_top_selling_report", json.dumps(body))) == (
            _top_selling_envelope(body)
        )


# --------------------------------------------------------------------------
# S4: a route refusal reaches the presenter as the AppException body
# (`{message, detail, code}`); it must never render as a ranking header.
# --------------------------------------------------------------------------


def test_customer_not_permitted_renders_the_refusal_line():
    """AC-1963 presenter half: a dealer naming a customer outside its own ledgers is
    refused by the route (403 `customer_not_permitted`, the same answer for another
    customer and for nobody). The reply is the fixed refusal line and nothing else;
    `has_result` is true so nothing escalates."""
    body = {"message": "You can only see sales for your own account.", "detail": None, "code": "customer_not_permitted"}
    envelope = json.loads(present_response("crm_top_selling_report", json.dumps(body)))
    assert envelope == {
        "result_type": "top_selling_refused",
        "response": TOP_SELLING_REFUSED_OTHER_CUSTOMER,
        "has_result": True,
        "result_set": [],
    }


def test_sales_report_not_enabled_renders_the_denial_line():
    body = {"message": "Sales report is not enabled for your account.", "detail": None, "code": "sales_report_not_enabled"}
    envelope = _top_selling_envelope(body)
    assert envelope["response"] == "Sales report is not enabled for your account."
    assert envelope["has_result"] is True
    assert envelope["result_set"] == []


def test_any_other_route_error_is_an_error_envelope():
    """A 422 (or anything else the route refuses) is an infrastructure failure for the
    lane (`envelope["error"]`), never a header over "No sales found."."""
    body = {"message": "n must be between 1 and 1000", "detail": "0", "code": "invalid_n"}
    envelope = _top_selling_envelope(body)
    assert envelope == {"error": "invalid_n: n must be between 1 and 1000"}


def test_a_named_n_of_one_reads_singular():
    body = _mock("categories")
    body["n"] = 1
    body["rows"] = body["rows"][:1]
    assert _top_selling(body).startswith("*Top 1 selling category*\n")
    body = _mock("items-qty")
    body["n"] = 1
    body["rows"] = body["rows"][:1]
    assert _top_selling(body).startswith("*Top 1 selling item*\n")


# --------------------------------------------------------------------------
# Fix lane round 4 (owner retest, 27 Sep 2026): the brand filter (F6) and the
# least sold ranking (F7)
# --------------------------------------------------------------------------


def test_brand_prints_on_the_header():
    body = _mock("items-qty")
    body["filters"]["brand_name"] = "Sorento"
    text = _top_selling(body)
    assert "\nCategory: all\nBrand: Sorento\nSales agent: all\n" in text


def test_bottom_ranking_titles_and_the_no_sale_line():
    """F7: a bottom ranking says so in its title, and one line says how items with
    no sale are treated (the plan does not rank them: the route ranks sales)."""
    body = _mock("items-qty")
    body["direction"] = "bottom"
    text = _top_selling(body)
    assert text.startswith("*Bottom 5 selling items*\n")
    assert "\nItems with no sale in this period are not ranked.\n" in text
    body["n"] = None
    assert _top_selling(body).startswith("*Least sold items*\n")
    body["group"] = "category"
    assert _top_selling(body).startswith("*Least sold categories*\n")
    body["n"] = 3
    assert _top_selling(body).startswith("*Bottom 3 selling categories*\n")


def test_a_top_ranking_has_no_no_sale_line():
    assert "no sale" not in _top_selling(_mock("items-qty"))


# --------------------------------------------------------------------------
# TOP-N-UNCAP (owner, 30 Sep 2026): a named N past 100, split into WhatsApp parts
# --------------------------------------------------------------------------


def _ranking(n: int) -> dict:
    body = _mock("items-qty")
    body["n"] = n
    body["total_count"] = max(n, 1284)
    body["rows"] = [
        {"rank": i, "code": f"SRTWC{i:04d}", "name": None, "quantity": 10_000 - i, "amount": 123_456.78 - i}
        for i in range(1, n + 1)
    ]
    return body


_MARKER = re.compile(r"^\((\d+)/(\d+)\)$")
_ROW = re.compile(r"^\d+\. SRTWC")


def _parts(rendered: str) -> list[str]:
    """The rendered reply cut at each "(k/m)" line (the engine's own split)."""
    return re.split(r"\n\n(?=\(\d+/\d+\)\n)", rendered)


def test_top_200_prints_every_row_in_ordered_whatsapp_parts():
    rendered = _top_selling(_ranking(200))
    parts = _parts(rendered)
    assert len(parts) > 1
    for k, part in enumerate(parts, start=1):
        assert len(part) <= WHATSAPP_MESSAGE_MAX_CHARS, (k, len(part))
        marker = _MARKER.match(part.split("\n", 1)[0])
        assert marker and (int(marker.group(1)), int(marker.group(2))) == (k, len(parts)), part[:20]
    rows = [line for part in parts for line in part.split("\n") if _ROW.match(line)]
    assert [int(r.split(".", 1)[0]) for r in rows] == list(range(1, 201)), "every row once, in rank order"
    # Never a row broken across two parts: every row line is whole.
    assert all(re.fullmatch(r"\d+\. SRTWC\d{4}: Qty [\d,]+, RM [\d,]+\.\d{2}", r) for r in rows)
    assert parts[0].startswith("(1/") and "*Top 200 selling items*" in parts[0]
    assert parts[-1].endswith(_ITEM_OFFER)
    envelope = _top_selling_envelope(_ranking(200))
    assert envelope["response"] == rendered
    assert [r["idx"] for r in envelope["result_set"]] == list(range(1, 201))


def test_top_5_is_one_unmarked_message():
    rendered = _top_selling(_ranking(5))
    assert _parts(rendered) == [rendered]
    assert rendered.startswith("*Top 5 selling items*")
    assert "\n5. SRTWC0005:" in rendered


def test_the_ceiling_fits_in_parts_too():
    rendered = _top_selling(_ranking(TOP_SELLING_N_CEILING))
    parts = _parts(rendered)
    assert all(len(p) <= WHATSAPP_MESSAGE_MAX_CHARS for p in parts)
    assert parts[-1].split("\n", 1)[0] == f"({len(parts)}/{len(parts)})"
    assert "\n1000. SRTWC1000:" in parts[-1]


def test_whatsapp_parts_never_splits_a_line():
    lines = [f"{i}. " + "x" * 90 for i in range(1, 120)]
    parts = whatsapp_parts("\n".join(lines), limit=1000)
    assert len(parts) > 1
    body = [line for p in parts for line in p.split("\n")[1:]]
    assert body == lines
    assert all(len(p) <= 1000 for p in parts)


def test_ceiling_matches_the_backend_copy():
    """The presenter's ceiling is a COPY of the backend's (this package cannot import
    it): read the backend source so the two can never drift apart."""
    source = (
        Path(__file__).parent.parent.parent / "sorento_crm_backend" / "app" / "services" / "sales_report_service.py"
    )
    if not source.exists():
        pytest.skip("backend source not in this container")
    match = re.search(r"^TOP_SELLING_N_CEILING = (\d+)$", source.read_text(), re.M)
    assert match and int(match.group(1)) == TOP_SELLING_N_CEILING


def test_a_long_detail_reply_is_split_too():
    """Grill Q6 (owner, 30 Sep 2026: "split detail reply here"): one code bought by
    hundreds of customers goes out in ordered WhatsApp parts, every customer once."""
    body = _mock("detail")
    body["detail"]["by_customer"] = [
        {"customer_name": f"CUSTOMER NUMBER {i:03d} SDN BHD", "quantity": 500 - i, "amount": 5000.0 - i}
        for i in range(1, 301)
    ]
    rendered = _top_selling(body)
    parts = _parts(rendered)
    assert len(parts) > 1
    assert all(len(p) <= WHATSAPP_MESSAGE_MAX_CHARS for p in parts)
    assert [p.split("\n", 1)[0] for p in parts] == [f"({k}/{len(parts)})" for k in range(1, len(parts) + 1)]
    names = [line for p in parts for line in p.split("\n") if "CUSTOMER NUMBER" in line]
    assert [int(n.split(".", 1)[0]) for n in names] == list(range(1, 301))
    assert _top_selling_envelope(body)["response"] == rendered


def test_a_short_detail_reply_is_unchanged():
    assert _parts(_top_selling(_mock("detail"))) == [_golden("detail")]
