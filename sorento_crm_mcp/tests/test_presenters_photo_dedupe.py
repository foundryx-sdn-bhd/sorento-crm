"""PHOTO-SEND-CAP rule R2: one file per IMAGE, not per url.

The same picture uploaded twice gets two urls and one `file_hash` (sha256 of the bytes,
`attachments.file_hash`). Measured on dev, 9 Oct 2026: 48 Sorento SRTSC products carry 102
photo rows and 62 distinct hashes, so a url-only dedupe sent about 40 copies of a picture
the dealer already had.
"""

import json

from sorento_crm_mcp.presenters import present_response


def env(tool, data):
    return json.loads(present_response(tool, json.dumps(data)))


def _row(code, url, file_hash, name="photo.jpg"):
    return {
        "product": {"product_code": code},
        "attachment": {
            "file_path": url,
            "original_filename": name,
            "stored_filename": name,
            "mime_type": "image/jpeg",
            "file_hash": file_hash,
            "attachment_type": {"type_name": "Product Photo"},
        },
    }


def test_the_entry_carries_the_file_hash():
    out = env("crm_master_product_attachments_list", {"data": [_row("SRTSC1012", "https://cdn/a.jpg", "h1")]})
    assert out["attachments"][0]["fileHash"] == "h1"


def test_one_image_under_two_urls_is_one_file():
    out = env(
        "crm_master_product_attachments_list",
        {
            "data": [
                _row("SRTSC1012", "https://cdn/a.jpg", "h1", "a.jpg"),
                _row("SRTSC1013", "https://cdn/b.jpg", "h1", "b.jpg"),
                _row("SRTSC1013", "https://cdn/c.jpg", "h2", "c.jpg"),
            ]
        },
    )
    assert [a["url"] for a in out["attachments"]] == ["https://cdn/a.jpg", "https://cdn/c.jpg"]


def test_no_hash_falls_back_to_the_url():
    out = env(
        "crm_master_product_attachments_list",
        {
            "data": [
                _row("SRTSC1012", "https://cdn/a.jpg", None, "a.jpg"),
                _row("SRTSC1013", "https://cdn/b.jpg", None, "b.jpg"),
                _row("SRTSC1014", "https://cdn/a.jpg", None, "a.jpg"),
            ]
        },
    )
    assert [a["url"] for a in out["attachments"]] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]
    assert all("fileHash" not in a for a in out["attachments"])
