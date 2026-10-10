"""Shared portal-form-visibility test seeding (D8, PLAN-portal-forms-market-segment r2).

``seed_segment``'s ``portal_form_types=`` kwarg does not exist on
``MarketSegment`` until the coder's D1 migration + model change lands
(``ptag_0012_segment_portal_forms``) - every caller of this helper is
therefore red until then with a ``TypeError: 'portal_form_types' is an
invalid keyword argument for MarketSegment``. That is the point: the same
seeding a legacy portal suite uses to grant a contact extra visibility is the
seeding that proves the segment column exists.

Resolver rule as of r2 (D3, lavish 16 Sep): every contact sees the four
legacy kinds (``SUPPORTED_TYPES``) by default; a market segment only grants
kinds BEYOND that base (today: ``price_tag_request``); a per-contact override
wins over both. ``grant_portal_forms`` writes an ``is_enabled=True`` override
- useful to grant price_tag_request (or re-affirm a base kind) regardless of
segment membership. To HIDE a base kind, add an ``is_enabled=False``
``ContactPortalFormOverride`` row directly (no helper here for that - it is a
one-line model construct, not worth a wrapper).
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.access import MarketSegment, respond_contact_market_segments
from app.models.price_tag import ContactPortalFormOverride


def grant_portal_forms(db: Session, contact_id: str, kinds) -> None:
    """Write an ``is_enabled=True`` override row per kind for ``contact_id``."""
    for kind in kinds:
        db.add(
            ContactPortalFormOverride(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                form_type=kind,
                is_enabled=True,
            )
        )
    db.flush()


def seed_segment(
    db: Session, *, code: str | None = None, kinds=(), is_active: bool = True
) -> MarketSegment:
    """A ``MarketSegment`` row granting ``kinds`` beyond the base four (D3/D4).

    ``portal_form_types`` does not exist on the model until D1 lands - this
    call is itself red until then.

    ``is_active=False`` seeds a deactivated segment - its grant does not
    reach the resolver (r4/SEC3, AC-R6).
    """
    code = code or f"zzt-seg-{uuid.uuid4().hex[:8]}"
    segment = MarketSegment(
        code=code,
        name=f"ZZT segment {code}",
        is_active=is_active,
        portal_form_types=list(kinds),
    )
    db.add(segment)
    db.flush()
    return segment


def link_contact_segment(db: Session, contact_id: str, code: str) -> None:
    """Assign ``contact_id`` to the market segment ``code``."""
    db.execute(
        respond_contact_market_segments.insert().values(
            contact_id=contact_id, segment_code=code
        )
    )
    db.flush()


def link_contact_company(
    db: Session, contact_id: str, company_id: str = "00000000-0000-0000-0000-000000000001"
) -> None:
    """Tag the contact into a company (default: Sorento, the incumbent).

    The price tag portal and AI Extract resolve their company from the contact's own
    ``respond_contact_companies`` and refuse a contact with none (PRICE-TAG-COMPANY),
    so every seed that drives those routes links one.
    """
    from app.models.company import RespondContactCompany

    db.add(RespondContactCompany(respond_contact_id=contact_id, company_id=company_id))
    db.flush()
