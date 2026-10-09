"""PHOTO-SEND-CAP: product file rows render ONE block per product (round 1 rule 1, kept by
the owner in round 2), and in link mode every file row carries its link under it.

End to end through the real MCP presenter (the envelope the lane receives) and
`fetch.output_structurer` (the text the customer reads). The engine turns each raw url
into a short link afterwards (`file_links.links_for`); here the url is still raw.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# CI's backend environment does not install the MCP package; the source sits beside it
# (same as `tests/test_report_ask_route.py`).
if importlib.util.find_spec("sorento_crm_mcp") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sorento_crm_mcp"))

from sorento_crm_mcp.presenters import present_response  # noqa: E402

from app.services.chatbot.lanes.business import fetch

TOOL = "crm_master_product_attachments_list"


def _row(code, url, name, file_hash=None, cert=None):
    row = {
        "product": {"product_code": code, "product_name": f"{code} seat cover"},
        "attachment": {
            "file_path": url,
            "original_filename": name,
            "stored_filename": name,
            "mime_type": "image/jpeg",
            "file_hash": file_hash,
            "attachment_type": {"type_name": "Product Photo"},
        },
    }
    if cert:
        row["certificate"] = cert
    return row


def _envelope(rows):
    return json.loads(present_response(TOOL, json.dumps({"data": rows})))


def _render(rows, *, links=False):
    ctx = {"tool": TOOL, "entities": [], "semantic_input": {}}
    if links:
        ctx["files_as_links"] = True
    return fetch.output_structurer(_envelope(rows), ctx)


ROWS = [
    _row("SRTSC1012", "https://cdn.test/a.jpg", "a.jpg", "h1"),
    _row("SRTSC1013", "https://cdn.test/c.jpg", "c.jpg", "h3"),
    _row("SRTSC1012", "https://cdn.test/b.jpg", "b.jpg", "h2"),
]


def test_each_item_carries_the_urls_of_its_own_files():
    items = _envelope(ROWS)["items"]
    assert [it["file_urls"] for it in items] == [
        ["https://cdn.test/a.jpg"],
        ["https://cdn.test/c.jpg"],
        ["https://cdn.test/b.jpg"],
    ]


def test_a_second_url_of_one_image_points_at_the_kept_url():
    """Q5 (a): the shared picture is ONE file, so both products name the url that is sent."""
    items = _envelope(
        [_row("X1", "https://cdn.test/a.jpg", "a.jpg", "h1"), _row("X2", "https://cdn.test/b.jpg", "b.jpg", "h1")]
    )["items"]
    assert [it["file_urls"] for it in items] == [["https://cdn.test/a.jpg"], ["https://cdn.test/a.jpg"]]


def test_attachment_mode_numbers_products_not_files():
    text = _render(ROWS)["response"]
    assert "1. *Product Code:* SRTSC1012" in text
    assert "2. *Product Code:* SRTSC1013" in text
    assert "3." not in text
    assert text.count("*Product Code:* SRTSC1012") == 1
    block = text[text.index("1. ") : text.index("2. ")]
    assert "*File Name:* a.jpg" in block and "*File Name:* b.jpg" in block
    assert "*Link:*" not in text


def test_positional_answers_are_the_products_in_render_order():
    answers = _render(ROWS)["answers"]
    assert [a["title"] for a in answers] == ["SRTSC1012", "SRTSC1013"]


def test_link_mode_puts_each_files_link_under_it():
    text = _render(ROWS, links=True)["response"]
    assert (
        "*File Name:* a.jpg\n*Link:* https://cdn.test/a.jpg\n"
        "*Attachment Type:* Product Photo\n*File Name:* b.jpg\n*Link:* https://cdn.test/b.jpg"
    ) in text
    assert "*File Name:* c.jpg\n*Link:* https://cdn.test/c.jpg" in text


def test_certificate_rows_stay_one_per_file():
    cert = {"certificate_number": "CERT-1", "valid_until": "2027-01-01", "validity_state": "valid"}
    rows = [
        _row("X1", "https://cdn.test/c1.pdf", "c1.pdf", "k1", cert=cert),
        _row("X1", "https://cdn.test/c2.pdf", "c2.pdf", "k2", cert={**cert, "certificate_number": "CERT-2"}),
    ]
    text = _render(rows)["response"]
    assert "1. *Product Code:* X1" in text and "2. *Product Code:* X1" in text
