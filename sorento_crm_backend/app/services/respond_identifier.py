"""Helpers for resolving Respond.io contact identifiers.

The CRM stores `RespondContact.id` (an internal UUID) on business rows like
`stock_inquiries.contact_id`, `complaints.contact_id`, etc., but the Respond.io
API + inbox URL require the contact's numeric `respond_io_id`. Mixing the two
caused 400 errors on `/v2/contact/id:<uuid>/message`. Centralize the lookup.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.access import RespondContact


def resolve_respond_io_id(db: Session, value: Optional[str]) -> Optional[str]:
    """Return the numeric `respond_io_id` for the given value.

    Accepts:
      * internal `RespondContact.id` (UUID) - the common stored value;
      * numeric `respond_io_id` already - returned unchanged if it matches a row;
      * `None`/empty - returns None.

    Looks up RespondContact by either column so callers don't need to know which
    representation they have.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    contact = (
        db.query(RespondContact)
        .filter(or_(RespondContact.id == s, RespondContact.respond_io_id == s))
        .first()
    )
    if not contact:
        return None
    rid = (contact.respond_io_id or "").strip()
    return rid or None


def contact_for_identifier(db: Session, identifier: Optional[str]) -> Optional[RespondContact]:
    """The RespondContact an OUTBOUND identifier addresses, or None.

    An outbound identifier is whatever `RespondClient` was handed for the API
    path: a bare `respond_io_id`, a prefixed `id:900000008` / `phone:+6012...`,
    or a raw phone number. The prefix is stripped and the remainder is matched
    against `respond_io_id` first, then `phone_number` - the same order the
    workspace-credential resolver uses, because both answer the same question
    ("which contact is this send for?") and must never disagree.

    Returns None when nothing matches; a first-contact send has no row yet.
    """
    if not identifier:
        return None
    value = str(identifier).strip()
    if not value:
        return None
    if ":" in value:
        value = value.split(":", 1)[1]
    if not value:
        return None
    contact = (
        db.query(RespondContact).filter(RespondContact.respond_io_id == value).first()
    )
    if contact is None:
        contact = (
            db.query(RespondContact).filter(RespondContact.phone_number == value).first()
        )
    return contact


def resolve_send_identifier(db: Session, value: Optional[str]) -> Optional[str]:
    """Return an identifier safe to pass to RespondClient.send_message / list_messages.

    Already-prefixed values like ``phone:+60123...`` or ``id:900000008`` pass through
    unchanged; otherwise the value is resolved via RespondContact to a numeric
    `respond_io_id`. Returns None when no contact can be located.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if ":" in s:
        return s
    return resolve_respond_io_id(db, s)


def format_respond_inbox_url(
    app_base_url: Optional[str], space_id: Optional[str], respond_io_id: Optional[str]
) -> Optional[str]:
    """Build the Respond.io inbox URL: {base}/space/{space_id}/inbox/{respond_io_id}."""
    if not app_base_url or not space_id or not respond_io_id:
        return None
    base = str(app_base_url).rstrip("/")
    return f"{base}/space/{str(space_id).strip()}/inbox/{str(respond_io_id).strip()}"
