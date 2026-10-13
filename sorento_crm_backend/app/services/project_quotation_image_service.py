"""The quotation's side of the product picture: live while it is a draft, frozen at issue.

`product_image_service` owns the DECISION (which photograph is the product). This module owns
WHEN a quotation stops asking. Those are different questions and they resolve at different
moments:

- **A draft asks every time it is read.** Only 30 of the 535 products with candidate photos carry
  a choice, so almost every line is priced before anybody has answered for its product. Stamping
  the answer onto the line at save time would mean the choice made ten minutes later never
  reaches the quotation, and nobody is going to re-save 52 lines to collect it.
- **Issuing writes it down.** `project_quotation_lines.image_attachment_id` is the record of the
  photograph the customer was sent. Once stamped it is never overwritten - not by a later
  revision, not by the product's photo being re-chosen - because a re-download of R1 next year has
  to be R1.

The freeze is therefore a FILL, never an update: `image_attachment_id IS NULL` is the only row it
touches. That is what makes it safe to run again on the same version when an unchanged scope is
carried into R2, and it is why R1 and R2 of that scope cannot disagree about a picture.

The technical drawing (QUOTATION-UX) follows the same two rules in `drawing_attachment_id`: a
draft shows the product's first "Technical Specifications" image live, and issuing fills it. Both
columns can also be PICKED in the editor when a product has several (the pick is validated by
`project_quotation_service.assert_line_picks`), and a pick is simply a value the freeze will not
overwrite.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.projects import ProjectQuotationLine
from app.services import product_image_service as images


def resolved_attachment_ids(
    db: Session, lines: Sequence[Any]
) -> Dict[str, Optional[str]]:
    """Per line id, the attachment its picture cell should show.

    The stamped id wins wherever there is one - that line has been issued, and what the customer
    holds does not move. Everything else asks the product, live.
    """
    live_needed = [
        line.product_id
        for line in lines
        if not line.image_attachment_id and line.product_id
    ]
    live = images.images_for(db, live_needed) if live_needed else {}
    resolved: Dict[str, Optional[str]] = {}
    for line in lines:
        if line.image_attachment_id:
            resolved[str(line.id)] = str(line.image_attachment_id)
            continue
        found = live.get(str(line.product_id)) if line.product_id else None
        resolved[str(line.id)] = found.attachment_id if found is not None else None
    return resolved


#: The drawing cell's states. `chosen` is a picked or stamped id; `default` is the product's
#: first drawing by upload, shown live until a pick or the issue freeze writes one down.
DRAWING_CHOSEN = "chosen"
DRAWING_DEFAULT = "default"
NO_DRAWINGS = "no_drawings"


def _candidate(file: images.ProductFile) -> Dict[str, Any]:
    url, preview = images.signed_urls(file.attachment)
    return {
        "attachment_id": file.attachment_id,
        "filename": file.filename,
        "url": url,
        "preview_url": preview,
    }


def _cell(state: str, row: Optional[Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A photo or drawing cell. ``row`` is the Attachment shown, or nothing."""
    url = preview = None
    if row is not None:
        url, preview = images.signed_urls(row)
    return {
        "state": state,
        "url": url,
        "preview_url": preview,
        "attachment_id": str(row.id) if row is not None else None,
        "filename": (row.original_filename or row.stored_filename) if row is not None else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def line_images(
    db: Session,
    lines: Sequence[Any],
    frozen_versions: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Per line id, ``{"product_image": cell, "technical_drawing": cell}`` as the frontend reads them.

    One pass for the whole table: a scope runs to 52 lines and this is computed on every read of
    it, so a query per row would be 52 round trips to draw two columns. The product files are
    read once (photos and drawings together), then which products are visible, then only the
    STORED ids that are no longer among those files (a frozen line whose file was unlinked).

    A line with a stored id (picked in the editor, or stamped at issue) reports `chosen` off that
    id rather than off the product's state - the product may have no chosen photo at all today,
    and the issued line still carries the photograph that went out. Its candidates are still the
    product's live files, so a draft can change its pick - except on a version in
    ``frozen_versions`` (superseded or issued), where nothing can be picked any more: no
    candidates, a count of zero, and nothing signed for a picker that cannot open.
    """
    frozen = frozen_versions or set()
    if not lines:
        return {}

    product_ids = [line.product_id for line in lines if line.product_id]
    files = images.files_for(db, product_ids)
    photos = images.images_for(db, product_ids, files=files)

    on_file = {
        file.attachment_id: file.attachment for rows in files.values() for file in rows
    }
    stored = {
        str(value)
        for line in lines
        for value in (line.image_attachment_id, getattr(line, "drawing_attachment_id", None))
        if value
    }
    missing = [attachment_id for attachment_id in stored if attachment_id not in on_file]
    if missing:
        from app.models.resources import Attachment

        on_file.update(
            {
                str(row.id): row
                for row in db.query(Attachment).filter(Attachment.id.in_(missing)).all()
            }
        )

    cells: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        key = str(line.id)
        product_files = files.get(str(line.product_id), []) if line.product_id else []
        photo = photos.get(str(line.product_id)) if line.product_id else None
        visible = photo is not None and photo.state != images.OFF_CATALOG
        pickable = visible and str(line.version_id) not in frozen
        photo_candidates = [_candidate(f) for f in photo.candidates] if pickable else []
        drawing_files = [f for f in product_files if f.is_drawing] if visible else []
        drawing_candidates = [_candidate(f) for f in drawing_files] if pickable else []

        # --- the photo
        stamped = str(line.image_attachment_id) if line.image_attachment_id else None
        if stamped:
            photo_cell = _cell(images.CHOSEN, on_file.get(stamped), photo_candidates)
            photo_cell["attachment_id"] = stamped
        elif not line.product_id or photo is None:
            photo_cell = images.serialize(
                images.OFF_CATALOG_IMAGE
                if not line.product_id
                else images.ProductImage(product_id=str(line.product_id), state=images.NO_PHOTOS)
            )
            photo_cell["candidates"] = []
        elif photo.state == images.CHOSEN and photo.attachment_id:
            photo_cell = _cell(images.CHOSEN, on_file.get(photo.attachment_id), photo_candidates)
        else:
            photo_cell = images.serialize(photo)
            photo_cell["candidates"] = photo_candidates

        # --- the drawing
        drawn = str(line.drawing_attachment_id) if getattr(line, "drawing_attachment_id", None) else None
        if drawn:
            drawing_cell = _cell(DRAWING_CHOSEN, on_file.get(drawn), drawing_candidates)
            drawing_cell["attachment_id"] = drawn
        elif not visible:
            drawing_cell = _cell(images.OFF_CATALOG, None, [])
        elif drawing_files:
            drawing_cell = _cell(DRAWING_DEFAULT, drawing_files[0].attachment, drawing_candidates)
        else:
            drawing_cell = _cell(NO_DRAWINGS, None, [])

        if not pickable:
            for cell in (photo_cell, drawing_cell):
                cell["candidates"] = []
                cell["candidate_count"] = 0
        cells[key] = {"product_image": photo_cell, "technical_drawing": drawing_cell}
    return cells


def product_cells(db: Session, product_id: str) -> Dict[str, Any]:
    """The photo and drawing cells of a line with this product and no picks yet, candidates included.

    For a line that is not saved (or whose product was just changed): it has no row to read, but
    the person editing it still needs the product's pictures and the picker. Answered through
    ``line_images`` itself, so a draft and a saved line cannot be told two different things.
    """
    draft = SimpleNamespace(
        id="draft",
        version_id=None,
        product_id=str(product_id),
        image_attachment_id=None,
        drawing_attachment_id=None,
    )
    return line_images(db, [draft])["draft"]


def freeze_version_images(db: Session, version_id: str) -> int:
    """Stamp the photograph and the drawing onto every line of a version that has none.

    Returns how many lines got anything. Called at ISSUE, which is the moment "what was sent" is
    decided. A FILL, never an update, column by column: a line that already carries an id is what a
    customer already holds (or what a salesperson picked), and re-issuing an unchanged scope in R2
    must show what R1 showed. The drawing filled is the one the line shows unpicked: the product's
    first by upload.
    """
    lines = (
        db.query(ProjectQuotationLine)
        .filter(
            ProjectQuotationLine.version_id == str(version_id),
            ProjectQuotationLine.product_id.isnot(None),
            or_(
                ProjectQuotationLine.image_attachment_id.is_(None),
                ProjectQuotationLine.drawing_attachment_id.is_(None),
            ),
        )
        .all()
    )
    if not lines:
        return 0

    product_ids = [line.product_id for line in lines]
    files = images.files_for(db, product_ids)
    chosen = images.images_for(db, product_ids, files=files)
    filled = 0
    for line in lines:
        touched = False
        found = chosen.get(str(line.product_id))
        if (
            line.image_attachment_id is None
            and found is not None
            and found.state == images.CHOSEN
            and found.attachment_id
        ):
            line.image_attachment_id = found.attachment_id
            touched = True
        visible = found is not None and found.state != images.OFF_CATALOG
        first_drawing = next(
            (f for f in files.get(str(line.product_id), []) if f.is_drawing), None
        )
        if line.drawing_attachment_id is None and visible and first_drawing is not None:
            line.drawing_attachment_id = first_drawing.attachment_id
            touched = True
        filled += int(touched)
    if filled:
        db.flush()
    return filled


__all__ = ["freeze_version_images", "line_images", "product_cells", "resolved_attachment_ids"]
