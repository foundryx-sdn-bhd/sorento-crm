"""PV2-FIX r8a item 1 (shared v1 composer): one product with BOTH a Product Photos and a Technical
Specifications file is folded by `fetch._file_rows` into one row with two "Attachment Type" fields;
`compose._types_without_files` must see both, never print a false "has no Product Photos" line.

Owner turn 7 (6866b1b8) and live JD-ST12-14 / LOO-A1 sent both files and still printed the false line."""
from __future__ import annotations

from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.turn import compose

ASKED = [
    {"name": "Product Photos", "keys": ["Product Photos", "product_photos"]},
    {"name": "Technical Specifications", "keys": ["Technical Specifications", "technical_specifications"]},
]


def _envelope():
    def row(kind, filename):
        return {"fields": [{"label": "Product Code", "value": "SRTWC286-SH"},
                           {"label": "Attachment Type", "value": kind}, {"label": "File Name", "value": filename}],
                "flags": {}, "file_urls": [f"https://cdn.test/{filename}"]}

    return {"result_type": "product_attachments",
            "items": [row("Product Photos", "SRTWC286-SH.jpg"), row("Technical Specifications", "SRTWC286-SH_TD.jpg")]}


def test_the_folded_row_carries_both_attachment_types():
    items = fetch._file_rows(_envelope(), links=False)["items"]
    assert len(items) == 1
    kinds = [f["value"] for f in items[0]["fields"] if f["label"] == "Attachment Type"]
    assert kinds == ["Product Photos", "Technical Specifications"]


def test_no_gap_is_named_when_both_files_are_present():
    figures = fetch._file_rows(_envelope(), links=False)["items"]
    assert compose._types_without_files(["SRTWC286-SH"], ASKED, figures) == []


def test_a_kind_that_really_has_no_file_is_still_named():
    envelope = _envelope()
    envelope["items"] = envelope["items"][:1]          # only the photo exists
    figures = fetch._file_rows(envelope, links=False)["items"]
    assert compose._types_without_files(["SRTWC286-SH"], ASKED, figures) == [("SRTWC286-SH", ["Technical Specifications"])]
