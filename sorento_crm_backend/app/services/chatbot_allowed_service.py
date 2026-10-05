"""The per-contact "AI replies allowed" gate (`respond_contacts.chatbot_allowed`).

AI-ALLOWED-GATE, owner ask 5 Oct 2026: the CRM decides which contacts the chatbot answers.
One place owns both halves: the read the engine gate calls every turn, and the operations
the contact page, system screen and `scripts/set_contact_ai.py` use to flip it.

Fail direction: CLOSED. A contact with no row, or with ANY row unticked, is NOT allowed (the
same rule as `chatbot/turn_runtime.py` S2: an operator who unticks the row they can see must
not be undone by a duplicate row sharing the respond.io id). A database error while checking
is raised, never read as "allowed". This is the opposite of
`respond_outbound_service.assert_outbound_enabled`, which fails open for an unknown id.

The respond.io `is_allowed_ai` custom field is never read: the CRM row is the only source.
"""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access import RespondContact
from app.services.error_handler import AppException
from app.services.respond_outbound_service import find_contact


def is_allowed(db: Session, respond_io_id: Optional[str]) -> bool:
    """True when at least one row has this respond.io id and EVERY such row has the flag on."""
    value = str(respond_io_id or "").strip()
    if not value:
        return False
    flags = db.execute(
        select(RespondContact.chatbot_allowed).where(RespondContact.respond_io_id == value)
    ).scalars().all()
    return bool(flags) and all(bool(f) for f in flags)


def status_counts(db: Session) -> dict:
    """How many contacts the AI may answer, and how many it may not."""
    total = db.query(RespondContact).count()
    blocked = db.query(RespondContact).filter(RespondContact.chatbot_allowed.is_(False)).count()
    return {"allowed": total - blocked, "blocked": blocked, "total": total}


def set_all(db: Session, *, allowed: bool, dry_run: bool = False) -> int:
    """Switch every contact. Returns the number of rows that CHANGE (a second run reports zero)."""
    pending = db.query(RespondContact).filter(RespondContact.chatbot_allowed.is_(not allowed))
    affected = pending.count()
    if dry_run or affected == 0:
        return affected
    pending.update({RespondContact.chatbot_allowed: allowed}, synchronize_session=False)
    db.commit()
    return affected


def set_contact(
    db: Session, reference: str, *, allowed: bool, dry_run: bool = False
) -> Tuple[RespondContact, bool]:
    """Switch one contact. Returns `(contact, changed)`; an unknown reference is a 404."""
    contact = find_contact(db, reference)
    if contact is None:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"No Respond contact matches {reference!r}.",
            detail="Pass a respond_io_id, a phone number, or the internal contact id.",
            code="CONTACT_NOT_FOUND",
        )
    changed = bool(contact.chatbot_allowed) != allowed
    if changed and not dry_run:
        contact.chatbot_allowed = allowed
        db.commit()
        db.refresh(contact)
    return contact, changed
