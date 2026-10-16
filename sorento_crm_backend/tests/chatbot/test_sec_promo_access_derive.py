"""RELEASE-FIX-3: a contact read with NO `access_levels` sees what the contact's OWN levels allow.

SEC-PROMO-ACCESS (#1719) made that read return nothing, so every contact read that does not
name its levels (a brand-scoped contact, an n8n caller) went blank. The rule now: the route
derives the contact's assigned active access codes. Never wider than the contact's own levels,
so the Cabana case (a Sorento Dealer contact) still reaches no Cabana file; a contact holding
nothing, or an id that resolves to no contact, still sees nothing. The promotion-products route
is covered end to end by `tests/test_brand_scope_round2.py` (its contacts send no levels).
Fake names only.
"""
from __future__ import annotations

from sqlalchemy import text

from app.models.access import ContactAccessType
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_sec_promo_access import (  # noqa: F401 (_clear_overrides: autouse)
    CABANA_PROMOS,
    SORENTO_PROMO,
    _clear_overrides,
    _client,
    _names,
    _seed,
)

# The contact `_seed` creates; #1719's own tests read as "437264483", which is no contact.
CONTACT = str(CONTACT_ID)
SPACE = "364817"


def _grant(session_factory, *codes: str) -> str:
    """Give the seeded contact `codes`; returns its internal id."""
    db = session_factory()
    internal = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": CONTACT}
    ).scalar_one()
    for code in codes:
        db.execute(
            text("INSERT INTO respond_contact_access_types (contact_id, access_type_code) VALUES (:i, :c)"),
            {"i": internal, "c": code},
        )
    db.commit()
    return str(internal)


class TestDeriveFromTheContact:
    def test_helper_returns_the_contacts_own_active_codes(self, session_factory):
        from app.services.contact_access_type_service import ContactAccessTypeService

        _seed(session_factory)
        db = session_factory()
        db.add(ContactAccessType(code="zzt_retired", name="ZZT Retired", is_active=False))
        db.commit()
        internal = _grant(session_factory, "sorento_dealer", "zzt_retired")
        svc = ContactAccessTypeService(session_factory())
        # either id space resolves; a deactivated level grants nothing
        assert svc.translate_names_to_codes_for_caller(None, contact_id=CONTACT) == ["sorento_dealer"]
        assert svc.translate_names_to_codes_for_caller([], contact_id=internal) == ["sorento_dealer"]
        assert svc.translate_names_to_codes_for_caller([" "], contact_id=f" {CONTACT} ") == ["sorento_dealer"]
        # named levels still win, an unknown contact holds nothing, staff reads stay unfiltered
        assert svc.translate_names_to_codes_for_caller(["Cabana Dealer"], contact_id=CONTACT) == ["cabana_dealer"]
        assert svc.translate_names_to_codes_for_caller(["No Such Level"], contact_id=CONTACT) == []
        assert svc.translate_names_to_codes_for_caller(None, contact_id="999000999") == []
        assert svc.translate_names_to_codes_for_caller(None, contact_id=None) is None

    def test_a_sorento_dealer_contact_sees_its_own_tier_and_no_cabana_file(self, session_factory, monkeypatch):
        ids = _seed(session_factory)
        _grant(session_factory, "sorento_dealer")
        client = _client(monkeypatch, session_factory)
        every = client.get("/api/v1/marketing/promotions/", params={"contact_id": CONTACT, "space_id": SPACE})
        assert _names(every) & ({SORENTO_PROMO} | set(CABANA_PROMOS)) == {SORENTO_PROMO}
        cabana = client.get(
            "/api/v1/marketing/promotions/",
            params={"brand_ids": ids["cabana"], "contact_id": CONTACT, "space_id": SPACE},
        )
        assert not _names(cabana) & set(CABANA_PROMOS)
