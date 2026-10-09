"""Files sent as short links instead of WhatsApp files (PHOTO-SEND-CAP round 2).

Owner, 9 Oct 2026: every file the bot sends is its own WhatsApp message (about 0.042 MYR),
so a photo set cost about 2 MYR and nobody could read it. A contact switched to link mode
(`respond_contacts.chatbot_files_as_links`, the A/B switch) gets ONE text instead, where
every file row carries `*Link:* {frontend_base_url}/f/{code}`. Card:
documentation/plans/chatbot/CARD-photo-send-links-09oct.md.

One link per distinct FILE per turn: a picture uploaded twice has two urls and one
`fileHash`, and both rows show the same link (owner Q5 (a)). The raw CDN url the lane
rendered under each row (`fetch.output_structurer`, link mode) is replaced here; a file no
row names (a generated Excel report) gets a block of its own at the end.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.chatbot_file_link import ChatbotFileLink

#: Crockford base32: no I, L, O or U, so a code read aloud or retyped cannot be misread.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: 16 characters of a 32-letter alphabet is 80 bits from `secrets`: the code IS the
#: capability (owner Q2 (a), no sign-in) and links never expire by default, so it must
#: stay unguessable however many links exist (security review L1).
CODE_LENGTH = 16

#: The spreadsheet kinds the `/f/{code}` page opens in the browser instead of
#: downloading (owner: "Excel must open in the browser with no extra software").
#: `AttachmentPreviewModal` reads these four.
SHEET_EXTENSIONS = frozenset({"xlsx", "xls", "xlsm", "csv"})
SHEET_MIME_TYPES = frozenset(
    {
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    }
)


class LinkModeUnavailable(RuntimeError):
    """Link mode cannot run (no public base url to point a link at). The engine falls
    back to sending the files as attachments: a link to nowhere is never sent."""


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def is_sheet(filename: Any, mime_type: Any) -> bool:
    name = str(filename or "")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension in SHEET_EXTENSIONS or str(mime_type or "").lower() in SHEET_MIME_TYPES


def _base_url() -> str:
    from app.config import settings as app_settings

    base = (app_settings.frontend_base_url or "").strip().rstrip("/")
    if not base:
        raise LinkModeUnavailable("frontend_base_url is not set")
    return base


def short_url(code: str) -> str:
    return f"{_base_url()}/f/{code}"


def expiry_days(db: Session) -> int | None:
    """`system_settings.chatbot_file_link_expiry_days`; None (never) when unset."""
    from app.models.user import SystemSetting

    row = db.query(SystemSetting.chatbot_file_link_expiry_days).first()
    days = row[0] if row is not None else None
    return int(days) if isinstance(days, int) and days > 0 else None


def _identity(entry: dict[str, Any]) -> str:
    """One image = one link: the content hash when the row has one, else the url."""
    file_hash = str(entry.get("fileHash") or "").strip()
    return f"hash:{file_hash}" if file_hash else f"url:{entry.get('url')}"


def links_for(
    db: Session,
    text: str,
    files: list[Any],
    *,
    contact_respond_id: str | None,
    turn_id: str | None,
) -> tuple[str, int]:
    """`(text, minted)`: the reply with every file as a short link, and how many rows were
    minted. Raises (`LinkModeUnavailable`, or a database error) rather than return a text
    that names a file it could not link; the caller falls back to attachments."""
    base = _base_url()
    days = expiry_days(db)
    now = datetime.utcnow()
    expires_at = now + timedelta(days=days) if days else None

    code_by_identity: dict[str, str] = {}
    url_by_raw: dict[str, str] = {}
    blocks: list[str] = []
    rows: list[ChatbotFileLink] = []
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        raw = str(entry["url"])
        identity = _identity(entry)
        code = code_by_identity.get(identity)
        if code is None:
            code = new_code()
            code_by_identity[identity] = code
            rows.append(
                ChatbotFileLink(
                    code=code,
                    target_url=raw,
                    filename=entry.get("filename"),
                    mime_type=entry.get("mimeType"),
                    contact_respond_id=contact_respond_id,
                    turn_id=turn_id,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
        link = f"{base}/f/{code}"
        url_by_raw[raw] = link
        for alias in entry.get("aliasUrls") or []:
            if isinstance(alias, str) and alias:
                url_by_raw[alias] = link
        if raw not in text:
            blocks.append(f"*File:* {entry.get('filename') or 'file'}\n*Link:* {link}")

    db.add_all(rows)
    db.commit()

    # Longest first, so a url that is a prefix of another never rewrites part of it.
    for raw in sorted(url_by_raw, key=len, reverse=True):
        text = text.replace(raw, url_by_raw[raw])
    if blocks:
        text = "\n\n".join(part for part in (text.strip(), *blocks) if part)
    return strip_storage_urls(text), len(rows)


def strip_raw_links(text: str, files: list[Any]) -> str:
    """Drop the `*Link:* <raw url>` lines the lane rendered, for the fallback to
    attachments: the stored storage url is never shown to a customer."""
    raws = {str(f.get("url")) for f in files if isinstance(f, dict) and f.get("url")}
    kept = [line for line in text.split("\n") if not any(raw in line for raw in raws)]
    return "\n".join(kept)


def _storage_hosts() -> set[str]:
    import os

    return {
        h
        for h in (
            (os.getenv("CLOUDFRONT_DOMAIN") or "").strip().lower().rstrip("/"),
            (os.getenv("R2_CDN_DOMAIN") or "").strip().lower().rstrip("/"),
        )
        if h
    }


def strip_storage_urls(text: str) -> str:
    """Drop every line that still names a stored CDN url (security review M1): in link
    mode the customer reads short links only, whichever path rendered the row."""
    hosts = _storage_hosts()
    if not hosts:
        return text
    return "\n".join(
        line
        for line in text.split("\n")
        if not any(f"://{host}/" in line.lower() for host in hosts)
    )


def resolve(db: Session, code: str, *, now: datetime | None = None) -> ChatbotFileLink | None:
    """The live link for `code`, or None when unknown or expired (one answer for both).

    Expired is EITHER the stamp minted with the link OR the setting as it stands now
    (security review M3): turning the expiry on later closes the links already sent.
    Revoking one link is deleting its row."""
    if not code or len(code) > 16:
        return None
    row = db.query(ChatbotFileLink).filter(ChatbotFileLink.code == code).first()
    if row is None:
        return None
    at = now or datetime.utcnow()
    if row.expires_at is not None and row.expires_at <= at:
        return None
    days = expiry_days(db)
    if days and row.created_at is not None and row.created_at + timedelta(days=days) <= at:
        return None
    return row


def record_click(db: Session, row: ChatbotFileLink, *, now: datetime | None = None) -> None:
    """One more open. Counted in SQL, so two phones opening one link at once both count."""
    from sqlalchemy import func as sa_func

    at = now or datetime.utcnow()
    db.query(ChatbotFileLink).filter(ChatbotFileLink.id == row.id).update(
        {
            ChatbotFileLink.click_count: ChatbotFileLink.click_count + 1,
            ChatbotFileLink.first_clicked_at: sa_func.coalesce(ChatbotFileLink.first_clicked_at, at),
            ChatbotFileLink.last_clicked_at: at,
        },
        synchronize_session=False,
    )
    db.commit()


def download_bytes(url: str) -> bytes:
    """The stored file's bytes, from whichever storage provider holds it."""
    from app.services.storage_router import (
        default_provider,
        detect_provider_from_url,
        extract_key,
        get_backend,
    )

    key = extract_key(url)
    if not key:
        raise ValueError("no storage key")
    return get_backend(detect_provider_from_url(url) or default_provider()).download_file(key)
