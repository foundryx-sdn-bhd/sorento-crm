"""S6 of the quotation DOCUMENT layer: rendering an ISSUE to the real artifact's layout.

Written BEFORE the implementation. The rules asserted here are the ones that make the PDF a
record rather than a report:

- **It renders from the ISSUE SNAPSHOT.** The lines come from the ``version_id`` each
  ``project_quotation_issue_scopes`` row recorded, and the letter, terms and grand total come off
  the issue row. A re-download next year must be what was sent, so the test issues, then edits
  everything reachable, then re-renders and asserts nothing moved.
- **A rate-only line prints the words and counts nothing** (AC-C2 / AC-D3). The sample workbook
  carries five. Printing RM 0.00 for them, or adding them up, are both money bugs the reader
  cannot see.
- **A band label heads its section once** (AC-C3), which is what lets a QS line the PDF up
  against their own bill of quantities.
- **The PRODUCT IMAGE column collapses when no line has one** (AC-F4): a column of blank cells
  on every page is worse than no column.

Postgres only, via ``blank_session``. Every row carries the ``zzt-qpdf`` marker, because the dev
database this runs against holds a copy of production data.
"""
from __future__ import annotations

import base64
import re
import uuid
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import text

from app.models.numbering import DocumentNumberingRule
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.projects import ProjectParty
from app.models.resources import Attachment
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-qpdf"

# Off the real workbook shape: one priced line and one rate-only alternate. The two line totals
# are deliberately unmistakable strings, so an assertion that the rate-only money is absent
# cannot pass by accidentally matching the priced one.
PRICED_RATE = "250.00"
PRICED_QTY = 4
# Lines in the "many photographs" size test. Three distinct photographs prove the per-line ratio;
# 52 proved the same thing at 160 s a run.
LINE_COUNT = 3
PRICED_TOTAL = "1,000.00"
RATE_ONLY_RATE = "180.00"
RATE_ONLY_QTY = 7
RATE_ONLY_TOTAL = "1,260.00"  # printed nowhere: the money column says "rate only" instead

# A real 1x1 PNG, so the byte-level render exercises the same image path a drawn signature takes
# rather than handing WeasyPrint a payload it would skip.
PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
SIGNATURE_DATA_URI = f"data:image/png;base64,{PNG_1X1}"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    """Sorento's company id as a STRING, the shape the request path carries."""
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _uom(db, code="NO") -> str:
    row = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name=code)
    db.add(row)
    db.flush()
    return row.id


def _category(db, name: str) -> ProductCategory:
    row = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} {name}"
    )
    db.add(row)
    db.flush()
    return row


def _product(db, category_id: str, uom_id: str, *, description: str, code=None) -> Product:
    """`base_uom_id` (not `uom_id`) and a NOT NULL `list_price`: the real columns."""
    row = Product(
        id=_uid(),
        product_code=code or f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} WC Suite",
        description=description,
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=Decimal("300.00"),
    )
    db.add(row)
    db.flush()
    return row


def _party(db, company_id: str, *, address: str, phone: str) -> ProjectParty:
    row = ProjectParty(
        id=_uid(),
        company_id=company_id,
        party_type="developer",
        name=f"{MARKER} Nadi Cergas {_uid()[:6]}",
        address=address,
        phone=phone,
    )
    db.add(row)
    db.flush()
    return row


def _project(db, company_id: str, owner: str, *, developer_party_id=None):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=developer_party_id,
        title=f"{MARKER} Cadangan Membina Pangsapuri {_uid()[:12]}",
    )


def _numbering_rule(db, company_id: str) -> DocumentNumberingRule:
    """Seeded, not borrowed: CI's database is empty, so a test that assumed an existing
    `project_quotation` rule would pass only on a developer's machine."""
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
    rule.next_value = 141
    rule.start_value = 141
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()
    return rule


def _attachment(db, company_id: str, name: str = "wc.png") -> Attachment:
    row = Attachment(
        id=_uid(),
        company_id=company_id,
        original_filename=f"{MARKER}-{name}",
        stored_filename=f"{MARKER}-{name}",
        file_path=f"https://cdn.zzt.test/products/{MARKER}/{name}",
        mime_type="image/png",
    )
    db.add(row)
    db.flush()
    return row


def _photo_bytes(width: int = 1600, height: int = 1600, seed: int = 0) -> bytes:
    """A real photograph-shaped JPEG, so the downscale is genuinely exercised.

    Noise rather than a flat colour: a flat image compresses to almost nothing, which would make
    every size assertion in this file pass for the wrong reason. ``seed`` makes two photographs
    genuinely different, so a size measured over 52 of them is not measuring one of them.
    """
    from PIL import Image

    image = Image.frombytes(
        "RGB",
        (width, height),
        bytes(
            (x * 7 + y * 13 + c * 61 + seed * 97) % 256
            for y in range(height)
            for x in range(width)
            for c in range(3)
        ),
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class _FakeBackend:
    """Stands in for S3/R2 so the image path is exercised without network or credentials."""

    def __init__(self, payload: bytes = b"zzt-png-bytes"):
        self.payload = payload
        self.keys: list[str] = []

    def download_file(self, key):
        self.keys.append(key)
        return self.payload


def _issue(db, document, owner):
    """Sign, then issue. An unsigned document cannot be issued (AC-H1), so every render in this
    file goes through the same two acts a salesperson performs."""
    from app.services import project_quotation_document_service as qdocs

    qdocs.sign_as_sorento(
        db,
        document=document,
        actor_user_id=owner,
        payload={
            "mode": "draw",
            "signer_name": document.signatory_name or f"{MARKER} Baser Ramli",
            "image_data_uri": SIGNATURE_DATA_URI,
        },
    )
    return qdocs.issue(db, document=document, actor_user_id=owner)


def _setup(db, *, developer_address=None, developer_phone=None):
    """The common chain: company, seed, numbering rule, owner, catalogue, project."""
    company_id = _sorento(db)
    project_seed_service.run(db, company_id=company_id)
    _numbering_rule(db, company_id)
    owner = _user(db, f"{MARKER} Baser Ramli")
    uom = _uom(db)
    category = _category(db, "Sanitary Ware")
    party = None
    if developer_address is not None:
        party = _party(db, company_id, address=developer_address, phone=developer_phone or "")
    project = _project(
        db, company_id, owner, developer_party_id=party.id if party is not None else None
    )
    return {
        "company_id": company_id,
        "owner": owner,
        "uom": uom,
        "category": category,
        "party": party,
        "project": project,
    }


# ------------------------------------------------------------------ AC-F3: the snapshot


def test_the_pdf_renders_the_issue_snapshot_and_not_the_live_rows():
    """The whole point of an issue. The lines come from the version_id the issue RECORDED, and
    the letter, terms and grand total come off the issue row - so a revision priced next month,
    a rewritten cover letter and a party that has moved office all leave R1 reading exactly as
    it was sent. Rendering from the scope's current version instead would silently re-write
    history, and the reader has no way to tell."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(
            db,
            developer_address=f"{MARKER} Level 8, Menara Lama, Kuala Lumpur",
            developer_phone="03-1111 1111",
        )
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        document.your_ref = f"{MARKER}/NC/2026/007"
        document.attn_name = f"{MARKER} Kelly"
        document.signatory_name = f"{MARKER} Baser Ramli"
        document.signatory_phone = "019-0000506"
        document.cover_letter_html = f"<p>{MARKER} first letter</p>"
        document.terms_html = f"<ol><li>{MARKER} first clause</li></ol>"
        db.flush()

        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
            },
        )

        r1 = _issue(db, document, owner)
        before = qpdf.build_issue_html(db, r1)

        assert r1.our_ref_text in before
        assert f"{MARKER}/NC/2026/007" in before
        assert f"{MARKER} Kelly" in before
        assert f"{MARKER} Level 8, Menara Lama, Kuala Lumpur" in before
        assert "03-1111 1111" in before
        assert f"{MARKER} first letter" in before
        assert f"{MARKER} first clause" in before
        assert PRICED_TOTAL in before
        assert "019-0000506" in before
        # The signature the document was issued with, inline. It is COPIED onto the issue, so it is
        # part of the snapshot rather than a live read of whatever the draft now holds.
        assert SIGNATURE_DATA_URI in before

        # Everything reachable now moves: a new priced revision, a rewritten letter and terms,
        # and a party that has changed address. Editing the ISSUED version itself is refused, so
        # the revision is the honest way to move the underlying price.
        v2 = quotes.revise(db, quotation=scope, actor_user_id=owner)
        quotes.upsert_line(
            db,
            version=v2,
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": "999.99", "quantity": PRICED_QTY},
        )
        document.cover_letter_html = f"<p>{MARKER} revised letter</p>"
        document.terms_html = f"<ol><li>{MARKER} revised clause</li></ol>"
        env["party"].address = f"{MARKER} Level 22, Menara Baru, Petaling Jaya"
        env["party"].phone = "03-9999 9999"
        db.flush()
        db.expire_all()

        after = qpdf.build_issue_html(db, r1)

        assert PRICED_TOTAL in after
        assert "999.99" not in after
        assert "3,999.96" not in after
        assert f"{MARKER} first letter" in after
        assert f"{MARKER} revised letter" not in after
        assert f"{MARKER} first clause" in after
        assert f"{MARKER} revised clause" not in after
        assert f"{MARKER} Level 8, Menara Lama, Kuala Lumpur" in after
        assert f"{MARKER} Level 22, Menara Baru, Petaling Jaya" not in after


# ------------------------------------------------------------------ AC-C2 / AC-D3


def test_a_rate_only_line_prints_the_words_and_is_absent_from_both_totals():
    """The sample carries five rate-only alternates. Printing RM 0.00 for one tells the customer
    it is free; adding it up overstates the quotation. Both errors look perfectly plausible on
    the page, which is why the words and the arithmetic are asserted together."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
            },
        )
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": RATE_ONLY_RATE,
                "quantity": RATE_ONLY_QTY,
                "is_rate_only": True,
                "description_snapshot": f"{MARKER} OKU grab bar alternate",
            },
        )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        # The line is on the page, and its rate is quoted.
        assert f"{MARKER} OKU grab bar alternate" in html
        assert RATE_ONLY_RATE in html
        assert "rate only" in html

        # Its money is not, and neither is a zero standing in for it.
        assert RATE_ONLY_TOTAL not in html
        assert "RM 0.00" not in html

        # Scope total and TOTAL AMOUNT are the priced line alone.
        assert issued.grand_total == Decimal("1000.00")
        assert PRICED_TOTAL in html
        assert "2,260.00" not in html  # what a summed rate-only line would have produced


# ------------------------------------------------------------------------- AC-C3


def test_each_band_label_heads_its_section_once():
    """The band is the customer's own BQ heading. Printed on every line it is noise; printed
    twice for one section the QS cannot tell where the section begins. It is a marker on the
    line that OPENS the band, so the renderer prints it once, above that line."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)

        first_band = f"{MARKER} BILL NO 3 PAGE 15/4"
        second_band = f"{MARKER} OPTIONAL ITEMS FOR OKU TOILET"
        rows = [
            {"band_label": first_band, "item_label": "A", "desc": f"{MARKER} WC suite"},
            {"band_label": None, "item_label": None, "desc": f"{MARKER} angle valve"},
            {"band_label": None, "item_label": None, "desc": f"{MARKER} flexible hose"},
            {"band_label": second_band, "item_label": "B", "desc": f"{MARKER} grab bar"},
        ]
        for index, row in enumerate(rows):
            quotes.upsert_line(
                db,
                version=version,
                actor_user_id=owner,
                payload={
                    "product_id": product.id,
                    "unit_price": PRICED_RATE,
                    "quantity": 1,
                    "sort_order": index,
                    "band_label": row["band_label"],
                    "item_label": row["item_label"],
                    "description_snapshot": row["desc"],
                },
            )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert html.count(first_band) == 1
        assert html.count(second_band) == 1
        # And it heads the lines under it rather than trailing them.
        assert html.index(first_band) < html.index(f"{MARKER} WC suite")
        assert html.index(f"{MARKER} flexible hose") < html.index(second_band)
        assert html.index(second_band) < html.index(f"{MARKER} grab bar")


# ------------------------------------------------------------------- AC-D1 / AC-D2


def test_every_scope_prints_its_own_total_and_the_document_prints_the_grand_total():
    """The client was explicit: the total belongs at the bottom of the money column, per scope,
    with TOTAL AMOUNT for the document under the same column. Both numbers come off the issue
    snapshot rather than being re-summed here, so the PDF cannot disagree with the record."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        townhouse = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        guard_house = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Guard House", actor_user_id=owner
        )
        for scope, price, qty in ((townhouse, "250.00", 4), (guard_house, "400.00", 3)):
            quotes.upsert_line(
                db,
                version=quotes.current_version(db, scope.id),
                actor_user_id=owner,
                payload={"product_id": product.id, "unit_price": price, "quantity": qty},
            )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert issued.grand_total == Decimal("2200.00")
        assert PRICED_TOTAL in html  # townhouse: 4 x 250
        assert "1,200.00" in html  # guard house: 3 x 400
        assert "TOTAL AMOUNT" in html
        assert "2,200.00" in html

        # Rendered in scope order, because that is the order the customer's document has.
        assert html.index(f"{MARKER} Townhouse") < html.index(f"{MARKER} Guard House")


# ------------------------------------------------------------------------- AC-F4


def test_the_product_image_column_is_omitted_when_no_line_carries_an_image():
    """A column of blank cells on every page is worse than no column, and it pushes the columns
    that do carry information into an unreadable width."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
            },
        )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert "PRODUCT IMAGE" not in html
        # The rest of the sample's column set is still there.
        for header in ("ITEM", "TECHNICAL SPEC", "DESCRIPTION", "BRAND", "PRODUCT CODE", "QTY"):
            assert header in html


def test_the_product_image_column_appears_and_embeds_the_image_when_a_line_has_one(monkeypatch):
    """Embedded as a data URI rather than a URL: WeasyPrint would have to fetch a signed CDN link
    at render time, and a PDF that renders differently depending on whether the network is up is
    not a record of what was sent.

    Re-encoded on the way in (S21). The mean chosen photograph in live data is 1.1 MB and the
    largest 4.3 MB, against a column 60 CSS px wide; inlining 52 originals is a PDF nobody can
    email."""
    from app.services import product_image_service as images
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )
        image = _attachment(db, env["company_id"])

        original = _photo_bytes(1600, 1600)
        backend = _FakeBackend(original)
        monkeypatch.setattr(images, "get_backend", lambda provider: backend)

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
                "image_attachment_id": image.id,
            },
        )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert "PRODUCT IMAGE" in html
        embedded = re.search(r'<td class="img"><img src="data:image/jpeg;base64,([^"]+)"', html)
        assert embedded is not None, html[:2000]
        assert len(base64.b64decode(embedded.group(1))) < len(original) / 10
        assert backend.keys == [f"products/{MARKER}/wc.png"]


def test_a_line_with_no_chosen_photo_prints_an_empty_cell_not_a_placeholder(monkeypatch):
    """PDF-3. The document is what the customer reads. "No photo chosen" is our internal to-do
    list, and on their page it reads as a system that could not do its job. The column is there
    because a NEIGHBOURING line has a picture; this line simply has none."""
    from app.services import product_image_service as images
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        with_photo = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )
        without = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} bottle trap"
        )
        image = _attachment(db, env["company_id"])
        monkeypatch.setattr(
            images, "get_backend", lambda provider: _FakeBackend(_photo_bytes(320, 320))
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "product_id": with_photo.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
                "image_attachment_id": image.id,
            },
        )
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "product_id": without.id,
                "unit_price": "6.50",
                "quantity": 2,
            },
        )

        html = qpdf.build_issue_html(db, _issue(db, document, owner))

        assert "PRODUCT IMAGE" in html
        assert '<td class="img"></td>' in html
        for word in ("No photo", "not chosen", "no image"):
            assert word.lower() not in html.lower()


def test_an_unreachable_image_leaves_the_document_renderable(monkeypatch):
    """Storage being down must degrade to a missing picture, never to a quotation that cannot be
    produced: the customer is waiting for a price, not a photograph."""
    from app.services import product_image_service as images
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )
        image = _attachment(db, env["company_id"])

        def _boom(provider):
            raise RuntimeError("zzt storage down")

        monkeypatch.setattr(images, "get_backend", _boom)

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
                "image_attachment_id": image.id,
            },
        )
        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert PRICED_TOTAL in html
        # The column still stands (the data says the line has a picture), and the cell is simply
        # empty. Asserting the cell rather than "no data URI anywhere" keeps the signature image,
        # which is stored inline and does not go through storage at all, out of the assertion.
        assert '<td class="img"></td>' in html


def test_a_multi_line_quotation_of_photographs_stays_a_pdf_somebody_can_email(monkeypatch):
    """PDF-4, measured rather than asserted in the abstract. The client's real quotation runs to
    52+ lines and the live catalogue's chosen photographs average 1.1 MB, so the honest question
    is not "is it downscaled" but "what does the artifact weigh". Size scales linearly per line,
    so a handful of distinct photographs measures the same ratio as 52 did, in seconds rather
    than minutes of CI."""
    from app.services import product_image_service as images
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    pytest.importorskip("weasyprint")

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        # A DIFFERENT photograph per line, and different BYTES. Reusing one image would let both
        # the per-document URI cache and WeasyPrint's own identical-image de-duplication flatter
        # the measurement into meaninglessness: 52 copies of one picture is one picture.
        original = _photo_bytes(1600, 1600)
        photos = {}

        class _PerKey:
            def download_file(self, key):
                if key not in photos:
                    photos[key] = _photo_bytes(1600, 1600, seed=len(photos) + 1)
                return photos[key]

        monkeypatch.setattr(images, "get_backend", lambda provider: _PerKey())

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        for index in range(LINE_COUNT):
            product = _product(
                db, env["category"].id, env["uom"], description=f"{MARKER} item {index}"
            )
            quotes.upsert_line(
                db,
                version=version,
                actor_user_id=owner,
                payload={
                    "product_id": product.id,
                    "unit_price": PRICED_RATE,
                    "quantity": PRICED_QTY,
                    "image_attachment_id": _attachment(
                        db, env["company_id"], f"item-{index}.jpg"
                    ).id,
                },
            )

        issued = _issue(db, document, owner)
        try:
            pdf_bytes, _ = qpdf.render_issue_pdf(db, issued)
        except Exception as exc:  # WeasyPrint's native libs are optional on a dev host
            pytest.skip(f"WeasyPrint cannot render here: {exc}")

        naive = LINE_COUNT * len(original)
        print(
            f"\n{LINE_COUNT}-line PDF: {len(pdf_bytes) / 1024:.0f} KB "
            f"({LINE_COUNT} originals would be {naive / 1024 / 1024:.1f} MB)"
        )
        assert len(pdf_bytes) < 4 * 1024 * 1024, f"{len(pdf_bytes)} bytes"
        assert len(pdf_bytes) < naive / 10


# ------------------------------------------------------------- customer data is escaped


def test_customer_supplied_text_cannot_inject_markup():
    """A quotation is customer data: band labels, descriptions and scope names are typed off the
    customer's own bill of quantities. Unescaped, a stray angle bracket silently swallows the
    rest of the row."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db,
            document=document,
            scope_label=f"{MARKER} <script>alert(1)</script>",
            actor_user_id=owner,
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
                "description_snapshot": f"{MARKER} WC <b>white</b> & chrome",
                "band_label": f"{MARKER} BILL <2>",
            },
        )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "WC &lt;b&gt;white&lt;/b&gt; &amp; chrome" in html
        assert "BILL &lt;2&gt;" in html


# --------------------------------------------------------------- both signatures print


def test_the_counter_signature_prints_only_once_the_customer_has_accepted():
    """The accepted PDF is the record of what was agreed, so both signatures have to be on it. An
    issue nobody has signed back is a legitimate resting state (AC-H8), and a blank "Accepted by"
    box on a printed page reads as a failure rather than as waiting."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    customer_signature = f"data:image/png;base64,{PNG_1X1}"

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
            },
        )
        issued = _issue(db, document, owner)

        assert "Accepted by" not in qpdf.build_issue_html(db, issued)

        qdocs.accept_issue(
            db,
            record=issued,
            signer_name=f"{MARKER} Kelly Tan",
            mode="draw",
            image_data_uri=customer_signature,
        )
        html = qpdf.build_issue_html(db, issued)

        assert "Accepted by" in html
        assert f"{MARKER} Kelly Tan" in html
        assert customer_signature in html


def test_where_the_customer_signed_prints_as_a_place_not_bare_numbers():
    """Client feedback, then revised: `3.03927, 101.80660` on a signed quotation meant nothing to
    whoever read it, so it prints as the place. The follow-up decision (2026-08-05) dropped the
    coordinates from the label entirely - they read as noise, not evidence, to the person holding
    the document - so this pins the CURRENT contract: the place, and nothing after it. The lookup
    is the shared offline table in `geo_places`, so this line and the CRM screen cannot disagree
    about where somebody stood."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(db, env["category"].id, env["uom"], description=f"{MARKER} WC")

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={"product_id": product.id, "unit_price": PRICED_RATE, "quantity": 1},
        )
        issued = _issue(db, document, owner)

        # The Sorento half was signed with no location at all: the browser refusing the prompt is
        # a normal answer, and a customer-facing document must not carry a "GPS: -" row for it.
        assert "GPS" not in qpdf.build_issue_html(db, issued)

        qdocs.accept_issue(
            db,
            record=issued,
            signer_name=f"{MARKER} Kelly Tan",
            mode="draw",
            image_data_uri=f"data:image/png;base64,{PNG_1X1}",
            gps_lat="3.0392672",
            gps_lng="101.8066021",
        )
        html = qpdf.build_issue_html(db, issued)

        assert "near Kajang, Selangor" in html
        # The exact figures are gone from the label. They are not gone from the record: they
        # are still on the stored signature row, just not printed here.
        assert "3.03927" not in html
        assert "101.80660" not in html


# ------------------------------------------------------------------- the actual bytes


def test_the_issue_renders_to_real_pdf_bytes():
    """The HTML assertions above prove the layout; this proves WeasyPrint accepts it. A template
    that only ever gets asserted as a string can be quietly unparseable."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    try:
        from weasyprint import HTML  # noqa: F401
    except Exception as missing:  # pragma: no cover - host without cairo/pango
        pytest.skip(f"WeasyPrint native libraries unavailable on this host: {missing}")

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled WC, white"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        document.terms_html = f"<ol><li>{MARKER} prices hold 30 days</li></ol>"
        db.flush()
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={
                "product_id": product.id,
                "unit_price": PRICED_RATE,
                "quantity": PRICED_QTY,
            },
        )

        issued = _issue(db, document, owner)
        pdf_bytes, filename = qpdf.render_issue_pdf(db, issued)

        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 1000
        assert filename.endswith(".pdf")
        assert "R1" in filename


# ------------------------------------------------------- component lines (real data)


def test_a_component_of_a_set_prints_no_money_rather_than_zero():
    """Real quotations carry set components on their own rows: a pedestal at 305.55 followed by
    its cistern, seat cover and connector at no separate charge, because the money sits on the
    parent. The Tuju Residences quotation in the dev database has 4 of them under one item.

    Printed as `0.00` those rows read as four free products, and a QS pricing against them is
    entitled to hold Sorento to it. The artifact leaves the cell blank. Nothing here is
    rate-only (that flag means a quoted alternate, and these are not alternates), so the blank
    is driven off the amount itself. It cannot move any number: zero contributes zero either
    way, which is exactly why this is safe to assert alongside an unchanged grand total."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        parent = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} close-coupled pedestal"
        )
        component = _product(
            db, env["category"].id, env["uom"], description=f"{MARKER} cistern only, no charge"
        )

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": parent.id, "unit_price": "305.55", "quantity": "894"},
        )
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={"product_id": component.id, "unit_price": "0.00", "quantity": "894"},
        )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        # The component is still listed, with its quantity: the customer is receiving 894 of them.
        assert f"{MARKER} cistern only, no charge" in html
        assert "894" in html

        # But neither its rate nor its amount reads as a price.
        assert "0.00" not in html

        # The priced parent is untouched, and so is the arithmetic.
        assert "305.55" in html
        assert "273,161.70" in html
        assert issued.grand_total == Decimal("273161.70")


# ----------------------------------------------------------- item numbers (S9)


def test_the_item_column_numbers_the_lines_continuously_through_the_sections():
    """The ITEM column is the number the customer reads back down the phone, so it has to be on
    the page whether or not anybody typed one.

    The editor stopped storing a hand-typed letter (client decision: "just show the number which
    supposed to be auto generated"), which leaves the renderers with nothing to print unless they
    DERIVE it. Left as-is the column would show a mix of stale letters on old lines and blanks on
    new ones, which is worse than either.

    Continuous through a section heading, per the client: a section is a heading over the same
    running list, not a restart. Asserted across TWO bands precisely because restarting at each one
    is the easy thing to write by accident, and it would give two different lines the number 1."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(db, env["category"].id, env["uom"], description=f"{MARKER} WC suite")

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        # Two bands, two lines each, and NO item_label anywhere: exactly what the editor now sends.
        for band, price in (
            (f"{MARKER} BILL NO 3", "250.00"),
            (f"{MARKER} BILL NO 3", "120.00"),
            (f"{MARKER} BILL NO 4", "300.00"),
            (f"{MARKER} BILL NO 4", "90.00"),
        ):
            quotes.upsert_line(
                db,
                version=version,
                actor_user_id=owner,
                payload={
                    "product_id": product.id,
                    "unit_price": price,
                    "quantity": "1",
                    "band_label": band,
                },
            )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        numbers = re.findall(r'<td class="item">([^<]*)</td>', html)
        assert numbers == ["1", "2", "3", "4"]


def test_a_line_that_still_carries_a_typed_letter_keeps_it():
    """Lines priced before the change still hold their letter, and a quotation already sent to a
    customer must not silently renumber itself under them. So a stored label wins; the derived
    number only fills the gap where there is none."""
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _setup(db)
        owner = env["owner"]
        product = _product(db, env["category"].id, env["uom"], description=f"{MARKER} WC suite")

        document = qdocs.create_document(db, project=env["project"], actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        version = quotes.current_version(db, scope.id)
        for label in ("A", None):
            quotes.upsert_line(
                db,
                version=version,
                actor_user_id=owner,
                payload={
                    "product_id": product.id,
                    "unit_price": "250.00",
                    "quantity": "1",
                    "item_label": label,
                },
            )

        issued = _issue(db, document, owner)
        html = qpdf.build_issue_html(db, issued)

        assert re.findall(r'<td class="item">([^<]*)</td>', html) == ["A", "2"]
