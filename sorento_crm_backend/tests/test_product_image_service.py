"""S21: which photograph of a product the system shows, and where that decision lives.

Written BEFORE the implementation. There is exactly ONE image decision in this system -
`product_attachments.is_primary` - and the quotation is its third consumer, after the brochure and
3D-model generation. Everything asserted here defends that single decision:

- **The flag, and nothing but the flag.** No fallback to the first-linked row, to the lowest
  `sort_order`, or to a filename that happens to contain the product code. That fallback is the
  defect `is_primary` exists to remove: for `SRTWC286-SH` the first-linked row is one of 31 files
  including a blank page and two other products' photographs, and a wrong photo is a wrong product
  in front of a customer.
- **"Nobody has chosen yet" is a state, not an absence.** 30 of 535 products with candidate photos
  carry a choice, so on day one almost every line is in that state. It has to be reportable, with
  the number of candidates, so a screen can say what is missing and how much work it would be.
- **A deleted file is deleted everywhere.** 611 of the live product-to-image links point at an
  attachment Resource Management considers deleted; signing a URL for one would render a broken
  picture on a customer's quotation.
- **A photograph is downscaled before it is embedded.** The mean chosen image in live data is
  1.1 MB and the largest is 4.3 MB. Fifty-two of those inlined would be a PDF nobody can email.

Postgres only, via ``blank_session``. Every row carries the ``zzt-pimg`` marker, because the dev
database this runs against holds a copy of production data.
"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

from sqlalchemy import text

from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment

from ._pg_fixture import blank_session

MARKER = "zzt-pimg"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _catalogue(db):
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name=f"{MARKER} each")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} Sanitary Ware"
    )
    db.add_all([uom, category])
    db.flush()
    return uom, category


def _product(db, uom, category) -> Product:
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} WC Suite",
        description=f"{MARKER} close-coupled WC, white",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("300.00"),
    )
    db.add(row)
    db.flush()
    return row


def _attachment(
    db,
    company_id: str,
    *,
    name: str,
    mime: str = "image/jpeg",
    deleted: bool = False,
    thumbnail: bool = True,
) -> Attachment:
    row = Attachment(
        id=_uid(),
        company_id=company_id,
        original_filename=f"{MARKER}-{name}",
        stored_filename=f"{MARKER}-{name}",
        file_path=f"https://cdn.zzt.test/products/{MARKER}/{name}",
        thumbnail_path=(
            f"https://cdn.zzt.test/products/{MARKER}/{name}.thumb.jpg" if thumbnail else None
        ),
        mime_type=mime,
        is_deleted=deleted,
    )
    db.add(row)
    db.flush()
    return row


def _link(db, product, attachment, *, primary=False, sort_order=None) -> ProductAttachment:
    row = ProductAttachment(
        id=_uid(),
        product_id=product.id,
        attachment_id=attachment.id,
        is_primary=primary,
        sort_order=sort_order,
    )
    db.add(row)
    db.flush()
    return row


def _jpeg(width: int = 1600, height: int = 1600) -> bytes:
    """A real photograph-shaped JPEG, so the downscale is genuinely exercised.

    Noise rather than a flat colour: a flat image compresses to nothing, which would make the
    size assertions pass for the wrong reason.
    """
    from PIL import Image

    image = Image.frombytes(
        "RGB",
        (width, height),
        bytes(
            (x * 7 + y * 13 + c * 61) % 256
            for y in range(height)
            for x in range(width)
            for c in range(3)
        ),
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class _FakeBackend:
    """Stands in for S3/R2 so the image path is exercised without network or credentials."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.keys: list[str] = []

    def download_file(self, key):
        self.keys.append(key)
        return self.payload


# ------------------------------------------------------------------------ RES-1


def test_the_chosen_photo_is_the_one_flagged_primary():
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        first = _attachment(db, company_id, name="blank-page.jpg")
        chosen = _attachment(db, company_id, name="the-actual-wc.jpg")
        # Linked FIRST and with the lowest sort order, so a resolver that fell back to row order
        # would return the blank page rather than the product.
        _link(db, product, first, sort_order=0)
        _link(db, product, chosen, primary=True, sort_order=99)

        assert images.chosen_attachment_id(db, product.id) == chosen.id

        resolved = images.images_for(db, [product.id])[product.id]
        assert resolved.state == images.CHOSEN
        assert resolved.attachment_id == chosen.id
        assert resolved.filename == f"{MARKER}-the-actual-wc.jpg"
        assert resolved.candidate_count == 2


# ------------------------------------------------------------------------ RES-2


def test_candidates_with_nothing_flagged_read_as_not_chosen_and_never_guess():
    """The whole point of the flag. Somebody has to say which picture is the product, and until
    they do the honest answer is that nobody has - with the number of photos it would take one
    click to choose between."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        for index in range(3):
            _link(db, product, _attachment(db, company_id, name=f"candidate-{index}.jpg"),
                  sort_order=index)

        assert images.chosen_attachment_id(db, product.id) is None

        resolved = images.images_for(db, [product.id])[product.id]
        assert resolved.state == images.NOT_CHOSEN
        assert resolved.attachment_id is None
        assert resolved.candidate_count == 3


# ------------------------------------------------------------------------ RES-3


def test_a_product_with_no_photograph_says_so_rather_than_offering_a_spec_sheet():
    """`product_attachments` links whatever is attached to a product, and the live data holds 532
    PDFs. A spec sheet rendered as the product photo is worse than no photo at all, and it would
    also promise a choice the screen then fails to offer."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        _link(db, product, _attachment(db, company_id, name="spec.pdf", mime="application/pdf"))

        resolved = images.images_for(db, [product.id])[product.id]
        assert resolved.state == images.NO_PHOTOS
        assert resolved.candidate_count == 0


# ------------------------------------------------------------------------ RES-5


def test_a_photo_deleted_in_resource_management_is_deleted_here_too():
    """611 of the live product-to-image links point at a deleted attachment. Returning one would
    put a broken picture on a customer's quotation, and would invite somebody to keep a choice the
    product's own attachments tab says does not exist."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        gone = _attachment(db, company_id, name="deleted.jpg", deleted=True)
        _link(db, product, gone, primary=True)

        assert images.chosen_attachment_id(db, product.id) is None
        assert images.images_for(db, [product.id])[product.id].state == images.NO_PHOTOS


# ------------------------------------------------------------------------ RES-4


def test_a_line_with_no_product_is_off_catalog_and_needs_no_query():
    """An off-catalog line has no `product_id`, so there is no `product_attachments` row a flag
    could point at. It is not "no photo chosen" - there is nothing to choose."""
    from app.services import product_image_service as images

    with blank_session() as db:
        assert images.images_for(db, [])== {}
        assert images.OFF_CATALOG == images.for_product(db, None).state


# ------------------------------------------------------------------------ RES-6


def test_resolving_a_whole_scope_is_a_bounded_number_of_queries():
    """A 52-line quotation must not issue 52 round trips to draw its picture column."""
    from sqlalchemy import event

    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        products = []
        for index in range(12):
            product = _product(db, uom, category)
            _link(
                db,
                product,
                _attachment(db, company_id, name=f"p{index}.jpg"),
                primary=True,
            )
            products.append(product)
        db.flush()

        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", _count)
        try:
            resolved = images.images_for(db, [p.id for p in products])
        finally:
            event.remove(engine, "before_cursor_execute", _count)

        assert len(resolved) == 12
        assert all(row.state == images.CHOSEN for row in resolved.values())
        # Two: the chosen rows, and the candidate counts. Never one per product.
        assert len(statements) <= 3, statements


# --------------------------------------------------------------------- rendering


def test_an_embedded_photograph_is_downscaled_before_it_reaches_a_document(monkeypatch):
    """PDF-4 / XLS-5 in one place. The mean chosen image in live data is 1.1 MB; the largest is
    4.3 MB. The column it lands in is 60 CSS px wide, so shipping the original is tens of
    megabytes of detail nobody can see."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        original = _jpeg()
        backend = _FakeBackend(original)
        monkeypatch.setattr(images, "get_backend", lambda provider: backend)

        attachment = _attachment(db, company_id, name="huge.jpg", thumbnail=False)
        rendered = images.render(db, attachment.id)

        assert rendered is not None
        assert max(rendered.width, rendered.height) <= images.PRINT_BOX
        assert len(rendered.data) < len(original) / 10, (
            f"{len(rendered.data)} bytes from {len(original)}"
        )
        assert rendered.mime_type == "image/jpeg"


def test_the_thumbnail_is_preferred_as_the_source_when_one_exists(monkeypatch):
    """A ~320px thumbnail already exists for 25 of the 30 chosen photos. Downloading the original
    to throw 95% of it away is bandwidth spent for nothing."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        backend = _FakeBackend(_jpeg(320, 320))
        monkeypatch.setattr(images, "get_backend", lambda provider: backend)

        attachment = _attachment(db, company_id, name="wc.jpg", thumbnail=True)
        images.render(db, attachment.id)

        assert backend.keys == [f"products/{MARKER}/wc.jpg.thumb.jpg"]


def test_storage_being_down_degrades_to_no_picture_rather_than_no_quotation(monkeypatch):
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)

        def _boom(provider):
            raise RuntimeError(f"{MARKER} storage down")

        monkeypatch.setattr(images, "get_backend", _boom)
        attachment = _attachment(db, company_id, name="wc.jpg")

        assert images.render(db, attachment.id) is None
        assert images.data_uri(db, attachment.id) is None


def test_bytes_pillow_cannot_decode_render_to_nothing_whatever_their_size(monkeypatch):
    """The raw-bytes fallback is gone: it let an SVG claiming to be a jpeg reach the PDF. Anything
    Pillow cannot decode as png/jpeg/webp renders to nothing, small or large; a real image still
    renders."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)

        for label, payload in (
            ("small", b"zzt-not-an-image"),
            ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'),
            ("large", b"z" * (5 * 1024 * 1024)),
        ):
            monkeypatch.setattr(images, "get_backend", lambda provider, p=payload: _FakeBackend(p))
            row = _attachment(db, company_id, name=f"{label}.png", mime="image/png")
            assert images.render(db, row.id) is None, label
            assert images.data_uri(db, row.id) is None, label

        monkeypatch.setattr(images, "get_backend", lambda provider: _FakeBackend(_jpeg(64, 64)))
        ok = _attachment(db, company_id, name="real.jpg")
        assert images.render(db, ok.id) is not None


# ------------------------------------------------------------------- CHO-1/CHO-2


def test_choosing_a_photo_unchooses_the_previous_one():
    """The invariant is exactly one chosen image per product. Two flagged at once and the picture
    is back at the mercy of row order, which is the defect this flag exists to remove. Without the
    clear, the partial unique index rejects the write and "choose a different photo" 500s."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        first = _attachment(db, company_id, name="old.jpg")
        second = _attachment(db, company_id, name="new.jpg")
        link_a = _link(db, product, first, primary=True)
        link_b = _link(db, product, second)

        images.choose(db, link_b)

        db.refresh(link_a)
        db.refresh(link_b)
        assert link_b.is_primary is True
        assert link_a.is_primary is False
        assert images.chosen_attachment_id(db, product.id) == second.id


def test_the_product_attachment_endpoint_goes_through_the_same_choose():
    """CHO-1. `PUT /master-data/product-attachments/{id}` with `is_primary: true` is how the
    product's Attachments tab records the choice. Setting the flag straight onto the row would hit
    the partial unique index whenever another photo was already chosen, so "choose a different
    photo" would 500 - and on a database without that index it would quietly leave TWO."""
    from app.schemas.product import ProductAttachmentUpdate
    from app.services import product_image_service as images
    from app.services.product_service import ProductAttachmentService

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        link_a = _link(db, product, _attachment(db, company_id, name="old.jpg"), primary=True)
        link_b = _link(db, product, _attachment(db, company_id, name="new.jpg"))

        ProductAttachmentService(db).update_product_attachment(
            link_b.id, ProductAttachmentUpdate(is_primary=True)
        )

        db.refresh(link_a)
        db.refresh(link_b)
        assert (link_a.is_primary, link_b.is_primary) == (False, True)
        assert images.chosen_attachment_id(db, product.id) == link_b.attachment_id

        # CHO-2: clearing it leaves the product with no chosen photo rather than falling back.
        ProductAttachmentService(db).update_product_attachment(
            link_b.id, ProductAttachmentUpdate(is_primary=False)
        )
        assert images.chosen_attachment_id(db, product.id) is None


def test_linking_a_replacement_photo_as_primary_moves_the_choice_rather_than_failing():
    """The n8n intake re-posts `(product_id, attachment_id)` with `is_primary` when a photograph
    is replaced. Writing the flag straight onto the row would trip the partial unique index while
    the old photograph still holds it, so a replacement would 500 instead of moving the choice."""
    from app.schemas.product import ProductAttachmentCreate
    from app.services import product_image_service as images
    from app.services.product_service import ProductAttachmentService

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        old = _link(db, product, _attachment(db, company_id, name="old.jpg"), primary=True)
        replacement = _attachment(db, company_id, name="replacement.jpg")

        ProductAttachmentService(db).create_product_attachment(
            ProductAttachmentCreate(
                product_id=product.id, attachment_id=replacement.id, is_primary=True
            )
        )

        db.refresh(old)
        assert old.is_primary is False
        assert images.chosen_attachment_id(db, product.id) == replacement.id


def test_choosing_the_photo_that_is_already_chosen_leaves_it_chosen():
    """Idempotent, not a toggle: a double click must not leave a product with no photo."""
    from app.services import product_image_service as images

    with blank_session() as db:
        company_id = _sorento(db)
        uom, category = _catalogue(db)
        product = _product(db, uom, category)
        link = _link(db, product, _attachment(db, company_id, name="wc.jpg"), primary=True)

        images.choose(db, link)

        db.refresh(link)
        assert link.is_primary is True
        assert images.chosen_attachment_id(db, product.id) == link.attachment_id
