"""Short file links: reading and opening them (PHOTO-SEND-CAP).

The chatbot mints `chatbot_file_links` rows (`app/services/chatbot/file_links.py`); the
public `/api/v1/public/file-links/{code}` route opens them. What both halves share lives
here, in core, because core never imports the chatbot package (`tests/chatbot/
test_import_boundary.py`); the chatbot side imports these instead.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.chatbot_file_link import ChatbotFileLink

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


def is_sheet(filename: Any, mime_type: Any) -> bool:
    name = str(filename or "")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension in SHEET_EXTENSIONS or str(mime_type or "").lower() in SHEET_MIME_TYPES


def expiry_days(db: Session) -> int | None:
    """`system_settings.chatbot_file_link_expiry_days`; None (never) when unset."""
    from app.models.user import SystemSetting

    row = db.query(SystemSetting.chatbot_file_link_expiry_days).first()
    days = row[0] if row is not None else None
    return int(days) if isinstance(days, int) and days > 0 else None


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


#: Opens of one link this close together are ONE click: a page that loads twice (a dev
#: server's double render, a browser retry) must not double the A/B readout.
CLICK_DEDUPE_SECONDS = 10


def record_click(db: Session, row: ChatbotFileLink, *, now: datetime | None = None) -> None:
    """One more open, counted in SQL, unless the same link was opened in the last
    `CLICK_DEDUPE_SECONDS` (browser cold-start in the cloud browser pass showed one tap
    arriving as up to four requests)."""
    from sqlalchemy import case, or_
    from sqlalchemy import func as sa_func

    at = now or datetime.utcnow()
    fresh = or_(
        ChatbotFileLink.last_clicked_at.is_(None),
        ChatbotFileLink.last_clicked_at <= at - timedelta(seconds=CLICK_DEDUPE_SECONDS),
    )
    db.query(ChatbotFileLink).filter(ChatbotFileLink.id == row.id).update(
        {
            ChatbotFileLink.click_count: case((fresh, ChatbotFileLink.click_count + 1), else_=ChatbotFileLink.click_count),
            ChatbotFileLink.first_clicked_at: sa_func.coalesce(ChatbotFileLink.first_clicked_at, at),
            # Moves only with a counted click, so the window is fixed, never sliding
            # (review N-a): reopening every 9 s still counts every 10 s.
            ChatbotFileLink.last_clicked_at: case((fresh, at), else_=ChatbotFileLink.last_clicked_at),
        },
        synchronize_session=False,
    )
    db.commit()


def _clean_host(value: str | None) -> str:
    """A bare host. A value written with a scheme or a path (`https://cdn.example.com/`)
    is reduced to its host, so a mistyped env value cannot drop a signer host off the list."""
    raw = (value or "").strip().lower()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    return raw.split("/", 1)[0].rstrip(".")


def signer_hosts() -> set[str]:
    """The CDN hosts this deployment's own storage credentials sign and read for."""
    import os

    return {h for h in (_clean_host(os.getenv("CLOUDFRONT_DOMAIN")), _clean_host(os.getenv("R2_CDN_DOMAIN"))) if h}


def link_hosts() -> set[str]:
    """Every host whose files may be sent as a short link: the hosts this deployment signs
    for, and no others (owner ruling, 9 Oct 2026). ONE list, read by minting
    (`chatbot/file_links.links_for`) and by the public route, so the two never disagree."""
    return signer_hosts()


def host_of(url: str | None) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:  # a malformed netloc (security review N4) names no host
        return ""


def is_linkable(url: str | None) -> bool:
    """An https url on a signer host, default port, no userinfo, no `..` in the path
    (raw or percent-encoded): security review S2 / N3."""
    from urllib.parse import unquote, urlparse

    if not url:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and host_of(url) in link_hosts()
        and ".." not in unquote(parsed.path or "")
    )


def download_bytes(url: str) -> bytes:
    """The stored file's bytes, read through this deployment's storage backend. A url on a
    host that is not linkable is refused; a bare key is read as is."""
    from app.services.storage_router import (
        default_provider,
        detect_provider_from_url,
        extract_key,
        get_backend,
    )

    if host_of(url) and not is_linkable(url):
        raise ValueError("host not on the link list")
    key = extract_key(url)
    if not key:
        raise ValueError("no storage key")
    return get_backend(detect_provider_from_url(url) or default_provider()).download_file(key)
