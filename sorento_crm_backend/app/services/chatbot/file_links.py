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
from app.services.file_link_service import (  # noqa: F401 - re-exported for the chatbot side
    CLICK_DEDUPE_SECONDS,
    SHEET_EXTENSIONS,
    SHEET_MIME_TYPES,
    download_bytes,
    expiry_days,
    is_sheet,
    record_click,
    resolve,
)

#: Crockford base32: no I, L, O or U, so a code read aloud or retyped cannot be misread.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: 16 characters of a 32-letter alphabet is 80 bits from `secrets`: the code IS the
#: capability (owner Q2 (a), no sign-in) and links never expire by default, so it must
#: stay unguessable however many links exist (security review L1).
CODE_LENGTH = 16

class LinkModeUnavailable(RuntimeError):
    """Link mode cannot run (no public base url to point a link at). The engine falls
    back to sending the files as attachments: a link to nowhere is never sent."""


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _base_url() -> str:
    from app.config import settings as app_settings

    base = (app_settings.frontend_base_url or "").strip().rstrip("/")
    if not base:
        raise LinkModeUnavailable("frontend_base_url is not set")
    return base


def short_url(code: str) -> str:
    return f"{_base_url()}/f/{code}"


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
        if not _mintable(raw):
            # Security re-review finding 2: a public, possibly never-expiring link is only
            # ever made for a file on our own CDN. Anything else goes as an attachment.
            raise LinkModeUnavailable("a file is not on our CDN")
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


def _mintable(url: str) -> bool:
    """An https url on our own CDN host whose key has no `..`. With no CDN host configured
    (a local copy) nothing can be signed at click time anyway, so the url is allowed and
    the public route refuses it (`_on_our_cdn`)."""
    from urllib.parse import urlparse

    hosts = _storage_hosts()
    if not hosts:
        return True
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in hosts
        and ".." not in (parsed.path or "")
    )


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
