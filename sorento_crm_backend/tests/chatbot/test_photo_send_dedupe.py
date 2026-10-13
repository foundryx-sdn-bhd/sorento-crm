"""PHOTO-SEND-CAP rule R2: the send list holds one file per IMAGE.

`engine._label_attachments` is the step every lane's files pass through before n8n sends
them, one WhatsApp message per file (about 0.042 MYR each). The same picture uploaded
twice has two urls and one `file_hash`; a url-only dedupe sent it twice.
"""

from datetime import datetime

from app.schemas.product import AttachmentSimple
from app.services.chatbot.engine import _clean_attachments


def _file(url: str, file_hash: str | None, name: str) -> dict:
    entry = {"url": url, "filename": name, "mimeType": "image/jpeg", "attachmentType": "Product Photo"}
    if file_hash is not None:
        entry["fileHash"] = file_hash
    return entry


def test_one_image_under_two_urls_is_sent_once() -> None:
    out = _clean_attachments(
        {
            "items": [],
            "attachments": [
                _file("https://cdn/a.jpg", "h1", "a.jpg"),
                _file("https://cdn/b.jpg", "h1", "b.jpg"),
                _file("https://cdn/c.jpg", "h2", "c.jpg"),
            ],
        }
    )
    assert [a["url"] for a in out["attachments"]] == ["https://cdn/a.jpg", "https://cdn/c.jpg"]


def test_no_hash_still_dedupes_on_the_url() -> None:
    out = _clean_attachments(
        {
            "items": [],
            "attachments": [
                _file("https://cdn/a.jpg", None, "a.jpg"),
                _file("https://cdn/a.jpg", None, "a.jpg"),
                _file("https://cdn/b.jpg", None, "b.jpg"),
            ],
        }
    )
    assert [a["url"] for a in out["attachments"]] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_a_bare_list_is_deduped_on_the_hash_too() -> None:
    out = _clean_attachments([_file("https://cdn/a.jpg", "h1", "a.jpg"), _file("https://cdn/b.jpg", "h1", "b.jpg")])
    assert [a["url"] for a in out] == ["https://cdn/a.jpg"]


def test_the_product_attachment_response_exposes_the_file_hash() -> None:
    """Without the field on the schema, `response_model` drops it and the chatbot never
    sees a hash to dedupe on."""
    now = datetime(2026, 10, 9)
    simple = AttachmentSimple.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "original_filename": "a.jpg",
            "stored_filename": "a.jpg",
            "file_path": "https://cdn/a.jpg",
            "file_hash": "h1",
            "uploaded_at": now,
            "created_at": now,
        }
    )
    assert simple.model_dump()["file_hash"] == "h1"
