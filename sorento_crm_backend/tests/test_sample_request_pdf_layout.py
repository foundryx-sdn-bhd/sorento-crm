"""AC-SR-60 / card rule 12 / mock v2 frame 5: the print follows the paper Consignment
Requisition Form - title with the request number, board row first, Brand and Model columns,
one model per line (no alternative), the paper's footer ticks and the internal box."""
from __future__ import annotations

from app.services.sample_request_pdf_service import build_html

DETAIL = {
    "request_no": "SR-000003",
    "status_label": "Submitted",
    "salesperson_name": "Salesperson A",
    "request_date": "2026-10-10",
    "customer_name": "JV PERKASA SDN BHD (PROJECT)",
    "customer_code": "300-J099",
    "project_name": "52 double-storey terraces, Taman Nusa Duta Fasa 10",
    "scope_note": "FOR UNIT - TOWER A & B",
    "delivery_date": "2026-11-05",
    "ship_to_name": "CHENXIN PLUMBING SDN BHD",
    "ship_to_address": "Johor Bahru",
    "site_contact_name": "Site supervisor",
    "site_contact_phone": "01x",
    "site_contact_note": "call 1 day before",
    "delivery_mode": "driver",
    "loan_value": 2,
    "loan_unit": "months",
    "goods_from": "either",
    "purpose": None,
    "board_mounting": True,
    "board_label": "NUSA DUTA FASA 10",
    "consignment_no": "CSGN-1",
    "po_no": None,
    "lines": [
        {"brand_name": "CABANA", "product_code": "CB2829",
         "product_name": "Basin", "qty": 1, "remark": None},
        {"brand_name": "SORENTO", "product_code": "SRT73-CR",
         "product_name": "Tap", "qty": 1, "remark": None},
    ],
}


def _cells(html: str) -> str:
    return " ".join(html.split())


def test_title_carries_the_paper_name_and_request_no():
    html = _cells(build_html(DETAIL))
    assert "CONSIGNMENT REQUISITION FORM" in html
    assert "SR-000003" in html


def test_board_is_row_one_then_brand_and_model_columns():
    html = _cells(build_html(DETAIL))
    assert ">Brand</th>" in html and ">Model</th>" in html
    board = html.index("MOUNT &amp; LABEL SAMPLE BOARD - NUSA DUTA FASA 10")
    first_line = html.index("CB2829")
    assert board < first_line
    assert "<td>CABANA</td>" in html


def test_footer_ticks_use_the_paper_labels():
    html = _cells(build_html(DETAIL))
    assert "[x] Deliver-Van" in html
    assert "[ ] Self-Deliver" in html and "[ ] Self-Collection" in html
    assert "Loan Period" in html and "2 months" in html
    # "Either" is the paper's blank: neither box ticked.
    assert "[ ] Demo Sets" in html and "[ ] New Goods" in html


def test_internal_box_holds_consignment_and_po():
    html = _cells(build_html(DETAIL))
    assert "For Internal Purpose" in html
    assert "CSGN-1" in html


def test_dates_print_as_the_paper_writes_them():
    html = _cells(build_html(DETAIL))
    assert "10/10/2026" in html
    assert "2026-10-10" not in html


def _goods_line(html: str) -> str:
    start = html.index("Goods Taken From:")
    return html[start : html.index("</p>", start)]


def test_either_ticks_no_goods_box():
    goods = _goods_line(_cells(build_html(DETAIL)))
    assert "[x]" not in goods and "Either" not in goods
    ticked = _goods_line(_cells(build_html({**DETAIL, "goods_from": "demo"})))
    assert "[x] Demo Sets" in ticked


def test_free_text_that_looks_like_a_date_is_printed_as_typed():
    html = _cells(build_html({**DETAIL, "purpose": "2026-10-10"}))
    assert "<b>Purpose:</b> 2026-10-10" in html


def test_ac_sr_60_no_or_alternative_is_printed():
    """16 Oct: a line is one product. Even a stale alt key in the dict prints no OR."""
    stale = {**DETAIL, "lines": [{**DETAIL["lines"][0], "alt_product_code": "CB2805A"}]}
    for detail in (DETAIL, stale):
        html = _cells(build_html(detail))
        assert " OR " not in html
        assert "CB2805A" not in html


def test_ac_sr_03_request_delivery_date_prints_dd_mm_yyyy():
    """AC-SR-03: Request Delivery Date is a plain date, printed dd/mm/yyyy."""
    html = _cells(build_html(DETAIL))
    assert "Request Delivery Date" in html
    assert "05/11/2026" in html
    assert "2026-11-05" not in html
    assert "Immediate" not in html
