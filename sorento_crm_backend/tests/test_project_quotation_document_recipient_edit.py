"""The recipient block becomes correctable on a PATCH.

Written BEFORE the implementation, so every test here is red until
``ProjectQuotationDocumentUpdate`` carries the three snapshot fields and
``update_document`` applies them.

Why this is a product decision and not a plumbing gap. AC-A3 snapshots the recipient off the
project's developer party ONCE, at creation, and deliberately never re-derives it, so that a
party record edited next year cannot rewrite a quotation the customer is already holding. The
client asked for the "To" block to be editable in the edit view, and that asks for something
different from re-deriving: a correction to THIS document's copy, e.g. the customer's finance
department taking delivery of invoices at a different address to the one on the developer party.
So the party stays untouched in both directions.

Two claims carry the weight:

- The fields have to reach the column. The service applies an explicit ALLOW-LIST rather than
  patching whatever arrives, so a field can be perfectly valid on the schema and still be
  silently dropped, which the FE reads as "the save worked and my edit vanished".
- A PATCH that omits them must leave them alone. The FE stages only the fields somebody typed
  into, so a request correcting only Your Ref arrives without any recipient key at all. Treating
  absent as null would blank the recipient off a quotation as a side effect of fixing a
  reference, and the party it was snapshotted from would no longer be there to recover it.

Postgres only, via ``blank_session``. Every row created here carries the ``zzt-qheaderedit``
marker so nothing in this file can touch the real data the dev database holds.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.numbering import DocumentNumberingRule
from app.models.projects import ProjectParty
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-qheaderedit"

PARTY_ADDRESS = "Level 12, Menara Nadi\nJalan Ampang\n50450 Kuala Lumpur"
PARTY_PHONE = "09-0000 0078"
# What a correction looks like: the same customer, their finance department's own address. The
# newlines matter - the column holds one string and the PDF prints a line per newline.
CORRECTED_NAME = f"{MARKER} Nadi Cergas Sdn Bhd (Finance)"
CORRECTED_ADDRESS = "Finance Department\nLot 8, Jalan Kia Peng\n50450 Kuala Lumpur"
CORRECTED_PHONE = "09-0000 0077"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    """Sorento's company id as a STRING, which is the shape the app passes around."""
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _party(db, company_id: str) -> ProjectParty:
    row = ProjectParty(
        id=_uid(),
        company_id=company_id,
        party_type="developer",
        name=f"{MARKER} Nadi Cergas {_uid()[:6]}",
        address=PARTY_ADDRESS,
        phone=PARTY_PHONE,
    )
    db.add(row)
    db.flush()
    return row


def _quotation_numbering_rule(db, company_id: str) -> DocumentNumberingRule:
    """Seeded rather than borrowed: CI's database is empty, so a test that assumed an existing
    rule would pass only locally. Upserted because the module seed may already have made one."""
    scoped = hasattr(DocumentNumberingRule, "company_id")

    query = db.query(DocumentNumberingRule).filter(
        DocumentNumberingRule.doc_type == "project_quotation"
    )
    if scoped:
        query = query.filter(DocumentNumberingRule.company_id == company_id)
    rule = query.first()

    if rule is None:
        rule = DocumentNumberingRule(id=_uid(), doc_type="project_quotation")
        if scoped:
            rule.company_id = company_id
        db.add(rule)

    rule.enabled = True
    rule.prefix_template = f"{MARKER}/Q/"
    rule.number_digits = 4
    rule.next_value = 1
    rule.start_value = 1
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()
    return rule


def _document(db):
    """One document whose recipient came off a real party, which is the state a correction
    starts from."""
    from app.services import project_quotation_document_service as qdocs
    from app.services.project_service import register_project

    company_id = _sorento(db)
    project_seed_service.run(db, company_id=company_id)
    _quotation_numbering_rule(db, company_id)
    owner = _user(db, f"{MARKER} Baser")
    party = _party(db, company_id)
    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=party.id,
        title=f"{MARKER} Cabana Elmina {_uid()[:12]}",
    )
    document = qdocs.create_document(db, project=project, actor_user_id=owner)
    return document, party, owner


def _patch_body(**fields) -> dict:
    """Exactly what the route hands the service: the request parsed by the update schema, then
    ``exclude_unset`` so an absent field stays absent all the way down."""
    from app.schemas.projects import ProjectQuotationDocumentUpdate

    return ProjectQuotationDocumentUpdate(**fields).model_dump(exclude_unset=True)


def test_the_update_schema_carries_the_recipient_block():
    """The wire contract, on its own.

    Pinned separately from the service because a schema that quietly dropped the keys would
    still let a service-level test pass - the test would be handing the service a dict the real
    route can never produce.
    """
    body = _patch_body(
        recipient_name_snapshot=CORRECTED_NAME,
        recipient_address_snapshot=CORRECTED_ADDRESS,
        recipient_phone_snapshot=CORRECTED_PHONE,
    )

    assert body == {
        "recipient_name_snapshot": CORRECTED_NAME,
        "recipient_address_snapshot": CORRECTED_ADDRESS,
        "recipient_phone_snapshot": CORRECTED_PHONE,
    }


def test_a_patch_corrects_the_snapshotted_recipient():
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        document, party, _owner = _document(db)

        # It started as the party's own details, which is AC-A3 and is not what is under test.
        assert document.recipient_name_snapshot == party.name
        assert document.recipient_address_snapshot == PARTY_ADDRESS

        qdocs.update_document(
            db,
            document=document,
            payload=_patch_body(
                recipient_name_snapshot=CORRECTED_NAME,
                recipient_address_snapshot=CORRECTED_ADDRESS,
                recipient_phone_snapshot=CORRECTED_PHONE,
            ),
        )
        db.flush()
        db.refresh(document)

        assert document.recipient_name_snapshot == CORRECTED_NAME
        # The newlines survive: the address is multi-line, and the PDF prints it a line per line.
        assert document.recipient_address_snapshot == CORRECTED_ADDRESS
        assert document.recipient_address_snapshot.count("\n") == 2
        assert document.recipient_phone_snapshot == CORRECTED_PHONE

        # A correction to the quotation's copy, and to nothing else. The party is a master record
        # shared by every document that named it.
        db.refresh(party)
        assert party.name != CORRECTED_NAME
        assert party.address == PARTY_ADDRESS
        assert party.phone == PARTY_PHONE
        # And the link is kept, so the document still says who it was addressed to originally.
        assert document.recipient_party_id == party.id


def test_a_patch_that_omits_the_recipient_leaves_the_snapshot_alone():
    """Partial, not a reset.

    The FE stages only the fields somebody typed into, so correcting Your Ref alone arrives with
    no recipient key at all. If absent read as null, fixing a reference would blank the recipient
    off the letterhead, and there is nothing to recover it from - the snapshot is the only copy
    this document has.
    """
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        document, party, _owner = _document(db)
        before = (
            document.recipient_name_snapshot,
            document.recipient_address_snapshot,
            document.recipient_phone_snapshot,
        )

        qdocs.update_document(
            db,
            document=document,
            payload=_patch_body(your_ref=f"{MARKER}/NCSB/PO/551"),
        )
        db.flush()
        db.refresh(document)

        assert document.your_ref == f"{MARKER}/NCSB/PO/551"
        assert (
            document.recipient_name_snapshot,
            document.recipient_address_snapshot,
            document.recipient_phone_snapshot,
        ) == before
        assert before[0] == party.name


def test_an_explicitly_null_recipient_field_clears_it():
    """Emptied is a real answer, and a different one from untouched.

    The letterhead sends null for a field the user cleared, because a blank string would print
    as a stray empty line on the letter. Absent leaves it (the test above); null clears it.
    """
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        document, _party, _owner = _document(db)
        assert document.recipient_phone_snapshot == PARTY_PHONE

        qdocs.update_document(
            db,
            document=document,
            payload=_patch_body(recipient_phone_snapshot=None),
        )
        db.flush()
        db.refresh(document)

        assert document.recipient_phone_snapshot is None
        # Only the field that was named. The rest of the block is untouched.
        assert document.recipient_address_snapshot == PARTY_ADDRESS


def test_the_corrected_recipient_is_what_the_read_serves_back():
    """The FE re-reads the document after a save, so the correction has to be in the response
    body and not only in the column."""
    from app.services import project_quotation_document_service as qdocs

    with blank_session() as db:
        document, _party, _owner = _document(db)

        qdocs.update_document(
            db,
            document=document,
            payload=_patch_body(
                recipient_name_snapshot=CORRECTED_NAME,
                recipient_address_snapshot=CORRECTED_ADDRESS,
            ),
        )
        db.flush()

        served = qdocs.serialize_document(db, document)
        payload = served if isinstance(served, dict) else served.model_dump()

        assert payload["recipient_name_snapshot"] == CORRECTED_NAME
        assert payload["recipient_address_snapshot"] == CORRECTED_ADDRESS
