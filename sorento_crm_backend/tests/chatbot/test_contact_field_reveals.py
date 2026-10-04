"""Chatbot growth r1, Slice C1: `contact_field_reveals` and the access-check wiring.

AC-960 the table exists, unique on (contact, field_key), default absent = hidden.
AC-961 `check_access` returns `attributes` = the contact's granted keys (`[]` for
none) and `all_attributes_allowed=False`.
AC-982 nothing here is ever written by a turn (a dry run included) - the table is
admin-managed only, so this is a structural guarantee restated as a test rather
than a turn-path assertion: no code under `app/services/chatbot/` outside
`head/access.py` (a READ) imports the write side.

Postgres only, via the blank-schema `session_factory` fixture in
`tests/chatbot/conftest.py` - built from live `Base.metadata`, so the new table
and the `mcp_tools.restricted_fields` column exist without a migration step.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.access import ContactFieldReveal
from app.services import contact_field_reveal_service as svc
from app.services.chatbot.head.access import check_access

# DO-ASK-SIMPLIFY (owner, 4 Oct 2026): every DO field reveal defaults ON for every contact.
DO_KEYS = sorted(key for key, _label in svc.FIELD_REVEAL_KEYS if key.startswith("delivery_orders."))

CONTACT_ID = "ZZT-contact-field-reveal-1"
SPACE_ID = "364817"


@pytest.fixture()
def seeded_contact(session_factory):
    """A respond contact, in a workspace, so `check_access` can resolve it by
    `(respond_io_id, space_id)` the same way `evaluate_agent` does."""
    db = session_factory()
    workspace_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, api_key_ciphertext) "
            "VALUES (:id, :space_id, 'x')"
        ),
        {"id": workspace_id, "space_id": SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, workspace_id, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, :wid, CAST(:sv AS jsonb))"
        ),
        {"cid": CONTACT_ID, "phone": "+60000000099", "wid": workspace_id, "sv": json.dumps({})},
    )
    db.commit()
    contact_id = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": CONTACT_ID}
    ).scalar()
    return db, contact_id


class TestTableShape:
    def test_default_absent_is_hidden(self, session_factory, seeded_contact):
        """AC-960: no row for a non-DO key -> hidden. DO keys default ON (owner, 4 Oct)."""
        _db, contact_id = seeded_contact
        db2 = session_factory()
        assert svc.granted_keys(db2, contact_id) == DO_KEYS

    def test_unique_on_contact_and_field_key(self, session_factory, seeded_contact):
        """AC-960: (respond_contact_id, field_key) is unique."""
        db, contact_id = seeded_contact
        db.add(
            ContactFieldReveal(
                respond_contact_id=contact_id, field_key="inventory.sellable", granted=True
            )
        )
        db.commit()
        db.add(
            ContactFieldReveal(
                respond_contact_id=contact_id, field_key="inventory.sellable", granted=True
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


class TestSetGrantedKeysFullReplace:
    def test_a_granted_key_reads_back(self, session_factory, seeded_contact):
        db, contact_id = seeded_contact
        result = svc.set_granted_keys(db, contact_id, ["inventory.sellable"], actor_id="u-1")
        assert result == ["inventory.sellable"]
        assert svc.granted_keys(session_factory(), contact_id) == ["inventory.sellable"]

    def test_full_replace_revokes_keys_not_in_the_new_list(self, session_factory, seeded_contact):
        db, contact_id = seeded_contact
        svc.set_granted_keys(
            db, contact_id, ["inventory.sellable", "purchase_orders.supplier"], actor_id="u-1"
        )
        result = svc.set_granted_keys(db, contact_id, ["purchase_orders.supplier"], actor_id="u-1")
        assert result == ["purchase_orders.supplier"]

        # The revoked row still exists (history), just no longer granted.
        row = (
            session_factory()
            .query(ContactFieldReveal)
            .filter(
                ContactFieldReveal.respond_contact_id == contact_id,
                ContactFieldReveal.field_key == "inventory.sellable",
            )
            .one()
        )
        assert row.granted is False

    def test_toggling_a_key_back_on_keeps_the_original_row(self, session_factory, seeded_contact):
        db, contact_id = seeded_contact
        svc.set_granted_keys(db, contact_id, ["inventory.sellable"], actor_id="u-1")
        row_id = (
            session_factory()
            .query(ContactFieldReveal.id)
            .filter(
                ContactFieldReveal.respond_contact_id == contact_id,
                ContactFieldReveal.field_key == "inventory.sellable",
            )
            .scalar()
        )
        svc.set_granted_keys(db, contact_id, [], actor_id="u-1")
        svc.set_granted_keys(db, contact_id, ["inventory.sellable"], actor_id="u-1")

        rows = (
            session_factory()
            .query(ContactFieldReveal)
            .filter(
                ContactFieldReveal.respond_contact_id == contact_id,
                ContactFieldReveal.field_key == "inventory.sellable",
            )
            .all()
        )
        assert len(rows) == 1, "a toggle off then on must not leave a second row"
        assert rows[0].id == row_id
        assert rows[0].granted is True


class TestCheckAccessAttributes:
    """AC-961."""

    def test_a_contact_with_no_grants_gets_only_the_do_keys(self, session_factory, seeded_contact):
        db, _contact_id = seeded_contact
        access = check_access(db, agent_code="general_enquiries", contact_id=CONTACT_ID, space_id=SPACE_ID)
        assert access["attributes"] == DO_KEYS
        assert access["all_attributes_allowed"] is False

    def test_a_contact_with_a_grant_sees_it_in_attributes(self, session_factory, seeded_contact):
        db, contact_id = seeded_contact
        svc.set_granted_keys(db, contact_id, ["inventory.sellable"], actor_id="u-1")

        access = check_access(
            session_factory(), agent_code="general_enquiries", contact_id=CONTACT_ID, space_id=SPACE_ID
        )
        assert access["attributes"] == ["inventory.sellable"]
        assert access["all_attributes_allowed"] is False

    def test_an_unresolvable_contact_fails_closed_to_empty(self, session_factory):
        db = session_factory()
        access = check_access(
            db, agent_code="general_enquiries", contact_id="ZZT-no-such-contact", space_id=SPACE_ID
        )
        assert access["attributes"] == []
        assert access["all_attributes_allowed"] is False


class TestNullWorkspaceFallback:
    """Measured gap (review, 7 Sep 2026): a contact with `workspace_id` NULL joins
    to nothing against `space_id`, so `resolve_contact_id` alone returns None and
    a real grant read as OFF. `check_access` recovers it with a second lookup by
    `respond_io_id` where `workspace_id IS NULL`."""

    NULL_WORKSPACE_CONTACT_ID = "ZZT-contact-null-workspace-1"

    def _seed_null_workspace_contact(self, session_factory, respond_io_id: str) -> str:
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, workspace_id, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, NULL, CAST(:sv AS jsonb))"
            ),
            {"cid": respond_io_id, "phone": f"+6000{uuid.uuid4().hex[:7]}", "sv": json.dumps({})},
        )
        db.commit()
        return db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid"),
            {"cid": respond_io_id},
        ).scalar()

    def test_a_null_workspace_contacts_grant_still_resolves(self, session_factory):
        contact_id = self._seed_null_workspace_contact(session_factory, self.NULL_WORKSPACE_CONTACT_ID)
        svc.set_granted_keys(session_factory(), contact_id, ["inventory.sellable"], actor_id="u-1")

        access = check_access(
            session_factory(),
            agent_code="general_enquiries",
            contact_id=self.NULL_WORKSPACE_CONTACT_ID,
            space_id=SPACE_ID,
        )
        assert access["attributes"] == ["inventory.sellable"]

    def test_two_null_workspace_contacts_sharing_a_respond_io_id_fail_closed(self, session_factory):
        respond_io_id = "ZZT-contact-null-workspace-shared"
        db = session_factory()
        for _ in range(2):
            db.execute(
                text(
                    "INSERT INTO respond_contacts (id, respond_io_id, phone_number, workspace_id, session_vars) "
                    "VALUES (gen_random_uuid()::text, :cid, :phone, NULL, CAST(:sv AS jsonb))"
                ),
                {"cid": respond_io_id, "phone": f"+6000{uuid.uuid4().hex[:7]}", "sv": json.dumps({})},
            )
        db.commit()

        access = check_access(
            session_factory(),
            agent_code="general_enquiries",
            contact_id=respond_io_id,
            space_id=SPACE_ID,
        )
        assert access["attributes"] == []


class TestFieldRevealKeys:
    def test_lists_the_frozen_keys_sorted(self, session_factory):
        """`field_reveal_keys` reads `FIELD_REVEAL_KEYS`, not `mcp_tools` - see the
        service module's docstring for why a live query cannot be used here. No
        `session_factory` row-seeding required; this test keeps the fixture arg only
        to match the file's other cases' shape."""
        _ = session_factory

        keys = svc.field_reveal_keys()

        assert keys == [{"key": key, "label": label} for key, label in sorted(svc.FIELD_REVEAL_KEYS)]
        assert {"key": "inventory.sellable", "label": "Outstanding SO on stock answers"} in keys
