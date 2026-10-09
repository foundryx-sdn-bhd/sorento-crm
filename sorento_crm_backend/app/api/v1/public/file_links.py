"""Public short file links the chatbot sends in link mode (PHOTO-SEND-CAP round 2).

No login (owner Q2 (a)): the 16-character code (80 bits) is the whole capability. The frontend page
`/f/{code}` reads `GET /{code}` and then either goes to the freshly signed url (a photo, a
PDF, any other file) or opens a spreadsheet read-only in the browser from `/{code}/content`.

What a caller can learn is kept to the minimum:
* an unknown code and an expired one answer the SAME 404, so a guess learns nothing;
* no answer carries the stored (unsigned) url or a company; a file answer carries a url
  signed for one hour, minted at the click, and only an https url on our own CDN host;
* the route takes a code and nothing else (no url parameter, so no open redirect), and is
  rate limited per IP (`rate_limit_file_link_*`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.chatbot.file_links import download_bytes, is_sheet, record_click, resolve
from app.services.storage_router import resolve_signed_url
from app.utils.http import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter()

EXPIRED = "This link has expired."
#: How long the signed url a click receives stays usable.
SIGNED_URL_SECONDS = 3600


#: The type `/content` serves, by extension (security review L3): never the uploader's.
_SHEET_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
}


def _on_our_cdn(url: str | None) -> bool:
    """Only an https url on our own CDN is handed to a browser (security review L2)."""
    import os
    from urllib.parse import urlparse

    if not url:
        return False
    parsed = urlparse(url)
    hosts = {
        h
        for h in (
            (os.getenv("CLOUDFRONT_DOMAIN") or "").strip().lower().rstrip("/"),
            (os.getenv("R2_CDN_DOMAIN") or "").strip().lower().rstrip("/"),
        )
        if h
    }
    return parsed.scheme == "https" and bool(hosts) and (parsed.hostname or "").lower() in hosts


def _as_ip(value: str | None) -> str | None:
    """The value only when it IS an IP address (security re-review finding 1): the header
    is client text, and an arbitrary 8 KB string per request would mint a Redis key each."""
    import ipaddress

    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def _rate_limited(request: Request) -> None:
    from app.config import settings as app_settings
    from app.services import rate_limit

    from app.services.client_ip import client_ip

    # Behind nginx (and the Next proxy when `NEXT_PUBLIC_API_URL` is unset) the socket peer
    # is the proxy, so keying on it would put every dealer in ONE bucket (security review
    # M2). The repo's one rule for public routes is the left-most `X-Forwarded-For` entry,
    # else the peer, and only a valid IP is used as the key. That entry is client-supplied,
    # so this limit spreads honest load per client; resistance to guessing comes from the
    # 80-bit code, not from this limit. The `/f/{code}` page calls this route on the API
    # host directly, never through the Next proxy, which forwards no client address.
    ip = _as_ip(client_ip(request)) or (request.client.host if request.client else None)
    gate = rate_limit.hit(
        "chatbot_file_link",
        ip,
        limit=app_settings.rate_limit_file_link_max,
        window_seconds=app_settings.rate_limit_file_link_window_seconds,
    )
    if not gate.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again shortly.",
            headers={"Retry-After": str(gate.retry_after_seconds or app_settings.rate_limit_file_link_window_seconds)},
        )


@router.get("/{code}")
def open_file_link(code: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """What `/f/{code}` needs to open the file. Counts one click."""
    _rate_limited(request)
    link = resolve(db, code)
    if link is None:
        raise HTTPException(status_code=404, detail=EXPIRED)
    sheet = is_sheet(link.filename, link.mime_type)
    url = None
    if not sheet:
        url = resolve_signed_url(link.target_url, expires_in=SIGNED_URL_SECONDS, strict=True)
        if not _on_our_cdn(url):
            logger.warning("file link %s: the file could not be signed", link.id)
            raise HTTPException(status_code=502, detail="The file is not available right now. Please try again.")
    record_click(db, link)
    return {
        "filename": link.filename or "file",
        "mime_type": link.mime_type,
        "kind": "sheet" if sheet else "file",
        "url": url,
    }


@router.get("/{code}/content")
def file_link_content(code: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """A spreadsheet's bytes, same-origin, for the in-browser preview (a signed CDN url has
    no CORS headers, so the page cannot read it directly). Spreadsheets only."""
    _rate_limited(request)
    link = resolve(db, code)
    if link is None or not is_sheet(link.filename, link.mime_type):
        raise HTTPException(status_code=404, detail=EXPIRED)
    try:
        content = download_bytes(link.target_url)
    except Exception:  # noqa: BLE001 - storage detail never reaches a public caller
        logger.warning("file link %s: download failed", link.id, exc_info=True)
        raise HTTPException(status_code=502, detail="The file is not available right now. Please try again.")
    filename = link.filename or "file"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return Response(
        content=content,
        media_type=_SHEET_MEDIA_TYPES.get(extension, "application/octet-stream"),
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(len(content)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
