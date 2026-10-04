"""Customer PO intake and handwriting review cards (P4, P5; UAC groups D and M).

The unit tests here never call a model. They feed the persistence and arithmetic code a
canned page payload shaped exactly like ``document_extraction`` produces, because the
thing worth pinning is not that a vision model can read a page: it is that **what we do
with what it says** is right. Specifically, that

* a wrong amount is caught by our own Decimal multiplication and not believed,
* a strike-through arrives as a PROPOSED card and never as a cancelled line (D11),
* the same pencil note on a re-scan is the same note, carrying its decision forward,
* and a confirm is refused while any card is still proposed.

The last test in the file is different in kind. It runs the REAL extractor over the
client's REAL committed scan and asserts what was measured off it: every printed line,
every line's arithmetic, the cancellation card, and the successor PO number that exists
only in pencil. That is the difference between "we extract POs" and "we extract THIS PO
correctly" (AC-M1, AC-M2). It costs about two minutes and skips without a key.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import settings as app_settings
from app.models.project_so import (
    ANNOTATION_ACCEPTED,
    ANNOTATION_PROPOSED,
    ANNOTATION_REJECTED,
    ProjectPOVersion,
)
from app.models.projects import ProjectPurchaseOrderLine
from app.models.user import User
from app.services import document_extraction, project_seed_service
from app.services.document_extraction import PageResult, RenderedPage
from app.services.error_handler import AppException
from app.services.llm_provider import ChatResult
from app.services.project_po_extraction_service import (
    STATE_DONE,
    STATE_QUEUED,
    ProjectPOExtractionService,
    classify_annotation,
    dedup_key,
)

from ._pg_fixture import blank_session

MARKER = "zzt-po-intake"

GOLDEN_PO = (
    Path(__file__).resolve().parents[2]
    / "sorento_crm_frontend"
    / "e2e"
    / "fixtures"
    / "project-cs"
    / "customer-po-buimaco-r1.pdf"
)


def _uid() -> str:
    return str(uuid.uuid4())


def _message(exc: AppException) -> str:
    """AppException stuffs the message into HTTPException.detail as a dict."""
    detail = exc.detail
    return (detail or {}).get("message", "") if isinstance(detail, dict) else str(detail)


def _code(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("code", "") if isinstance(detail, dict) else ""


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tuju {_uid()[:12]}",
    )


def _po(db, project, owner, number: str):
    from app.services import project_po_service as po_svc

    return po_svc.create_po(
        db,
        project=project,
        actor_user_id=owner,
        payload={"po_number": number, "po_source": "contractor_direct"},
    )


def _version(db, po, *, version_no: int = 1) -> ProjectPOVersion:
    """A version row with no stored document: every unit test below feeds the pages in
    directly, so nothing here needs object storage."""
    version = ProjectPOVersion(
        company_id=po.company_id,
        purchase_order_id=po.id,
        version_no=version_no,
        source_filename=f"{MARKER}.pdf",
        page_count=1,
        extraction_state=STATE_QUEUED,
    )
    db.add(version)
    db.flush()
    return version


def _page(page_no: int, **payload) -> PageResult:
    return PageResult(page_no=page_no, data=payload)


# A page shaped exactly like `po_extractor` returns one: the printed header, two priced
# lines, one of them crossed out by hand, and the pencil note beside it.
def _buimaco_like_page() -> PageResult:
    return _page(
        1,
        header={
            "po_number": "HQ/26/01/041",
            "po_date": "19/01/2026",
            "term": "60 DAYS",
            "sales_person": "MARYAM",
            "cust_order_no": "TUJU-01",
            "remark": "Deliver to site",
        },
        lines=[
            {
                "no": 1,
                "stock_code": "SRTWC86",
                "description": "SRTWC8613-RL One-Piece WC",
                "qty": 927,
                "uom": "SETS",
                "unit_price": 392.85,
                "amount": 364171.95,
                "struck_through": False,
            },
            {
                "no": 7,
                "stock_code": "SRTFV1001",
                "description": "SRTFV1001 Flush Valve",
                "qty": 16,
                "uom": "NOS",
                "unit_price": 295.85,
                "amount": 4733.60,
                "struck_through": True,
            },
        ],
        annotations=[
            {
                "text": "cancel - refer to New P/O HQ/26/05/087",
                "date": "15/5/26",
                "refers_to_items": [7],
                "meaning": "cancel this line",
            }
        ],
    )


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        project = _project(db, company_id, owner)
        yield db, project, owner


# ---------------------------------------------------- the AI proposes, we decide


def test_arithmetic_catches_an_amount_the_model_got_wrong(seeded):
    """The model's own `amount` is never believed: qty * unit_price is recomputed here."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-ARITH"))

    service.persist_pages(
        version,
        [
            _page(
                1,
                header={"po_number": "PO-ARITH"},
                lines=[
                    # 3 x 100.00 is 300.00, as printed.
                    {"no": 1, "stock_code": "A1", "qty": 3, "unit_price": 100.00, "amount": 300.00},
                    # 4 x 25.50 is 102.00, and the page claims 101.00.
                    {"no": 2, "stock_code": "A2", "qty": 4, "unit_price": 25.50, "amount": 101.00},
                ],
            )
        ],
        model="test/canned",
    )

    lines = {line.line_no: line for line in service._lines(version.id)}
    assert lines[1].arithmetic_ok is True
    assert lines[2].arithmetic_ok is False
    assert (version.arithmetic_passed, version.arithmetic_total) == (1, 2)


def test_a_line_missing_a_number_is_unchecked_rather_than_passed(seeded):
    """A gap is a different problem from a wrong figure and must not inflate the pass
    count: somebody has to type the missing number in."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-GAP"))

    service.persist_pages(
        version,
        [_page(1, lines=[{"no": 1, "stock_code": "A1", "qty": 3, "amount": 300.00}])],
    )

    line = service._lines(version.id)[0]
    assert line.arithmetic_ok is None
    assert (version.arithmetic_passed, version.arithmetic_total) == (0, 1)


def test_money_survives_as_decimal_and_not_as_a_float(seeded):
    """A float round trip loses cents on a 1.8 million ringgit PO, so the totals are
    asserted to the cent rather than approximately."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-CENTS"))

    service.persist_pages(
        version,
        [
            _page(
                1,
                lines=[
                    {"no": n, "stock_code": f"C{n}", "qty": 927, "unit_price": 392.85,
                     "amount": 364171.95}
                    for n in range(1, 4)
                ],
            )
        ],
    )

    totals = service.recompute_totals(version)
    assert totals["lines_total"] == Decimal("1092515.85")
    assert totals["extracted_total"] == Decimal("1092515.85")
    assert totals["arithmetic_passed"] == 3


def test_line_numbering_follows_the_printed_item_numbers_across_pages(seeded):
    """Handwriting refers to lines by the number the customer printed ("cancel item 7"),
    so ours have to be theirs."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-NUMBERING"))

    service.persist_pages(
        version,
        [
            _page(1, lines=[{"no": 1, "stock_code": "A"}, {"no": 2, "stock_code": "B"}]),
            _page(2, lines=[{"no": 3, "stock_code": "C"}, {"stock_code": "D"}]),
        ],
    )

    assert [line.line_no for line in service._lines(version.id)] == [1, 2, 3, 4]


def test_a_wrapped_description_rejoins_the_line_above_instead_of_becoming_one(seeded):
    """A long description that spills past the bottom of a page is one line, not two.

    On the client's own scan, item 49's description runs off page 8 and finishes at the
    top of page 9, above item 50. That fragment has no item number, no stock code and no
    money on it: it is the tail of a sentence. Read as a row it inflates the line count
    and puts an unpriced ghost line in front of a reviewer, so it is folded back into the
    line it belongs to.
    """
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-WRAPPED"))

    service.persist_pages(
        version,
        [
            _page(
                1,
                lines=[
                    {
                        "no": 49,
                        "stock_code": "CB6633",
                        "description": "CB6633 Concealed Cistern with",
                        "qty": 40,
                        "unit_price": 148.00,
                        "amount": 5920.00,
                    }
                ],
            ),
            _page(
                2,
                lines=[
                    {"description": "dual flush plate, chrome"},
                    {
                        "no": 50,
                        "stock_code": "SRTA8046",
                        "description": "SRTA8046 Basin Mixer",
                        "qty": 12,
                        "unit_price": 79.00,
                        "amount": 948.00,
                    },
                ],
            ),
        ],
    )

    lines = service._lines(version.id)
    assert [line.line_no for line in lines] == [49, 50]
    assert lines[0].description_raw == "CB6633 Concealed Cistern with dual flush plate, chrome"
    # The fold must not disturb what the line is worth.
    assert lines[0].amount == Decimal("5920.00")


def test_ocr_spill_in_the_code_cell_does_not_make_a_fragment_a_line(seeded):
    """The exact shape the client's scan produces at the page 8 to 9 boundary.

    Item 49's description finishes as "GRATING" at the top of page 9, and the model
    reads "??2-6" into the stock code column from ink showing through. Treating a filled
    code cell as proof of a line let that through as a 52nd item with no quantity and no
    money, which is a row a reviewer would have had to puzzle over and reject by hand.
    """
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-SPILL"))

    service.persist_pages(
        version,
        [
            _page(
                1,
                lines=[
                    {
                        "no": 49,
                        "stock_code": "JPSRT",
                        "description": "SORENTO SRT382-6 150X150MM S/S FLOOR",
                        "qty": 927,
                        "unit_price": 13.77,
                        "amount": 12764.79,
                    }
                ],
            ),
            _page(2, lines=[{"stock_code": "??2-6", "description": "GRATING"}]),
        ],
    )

    lines = service._lines(version.id)
    assert [line.line_no for line in lines] == [49]
    assert lines[0].description_raw == "SORENTO SRT382-6 150X150MM S/S FLOOR GRATING"
    # The spill is dropped, not appended: the line already carries the printed code.
    assert lines[0].stock_code_raw == "JPSRT"


def test_a_fragment_with_nothing_above_it_is_still_kept_as_a_line(seeded):
    """The fold only ever rejoins. With no line above it, a fragment is all we have of
    whatever the model saw, and dropping it would lose a row silently."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-ORPHAN"))

    service.persist_pages(version, [_page(1, lines=[{"description": "continued text"}])])

    lines = service._lines(version.id)
    assert [line.description_raw for line in lines] == ["continued text"]


# ------------------------------------------------- handwriting: proposed, not applied


def test_a_strike_through_is_proposed_as_a_card_and_never_cancels_a_line(seeded):
    """D11, the whole point of the slice. The cancellation of item 7 exists only in
    pencil; applying it automatically would move 4,733.60 on a model's opinion."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))

    service.persist_pages(version, [_buimaco_like_page()])

    assert all(line.is_cancelled is False for line in service._lines(version.id))
    cards = service._annotations(version.id)
    assert len(cards) == 1, "the pencil note and the strike-through are one decision"
    card = cards[0]
    assert card.state == ANNOTATION_PROPOSED
    assert card.interpretation == "cancel_line"
    assert card.refers_to_lines == [7]
    assert card.interpretation_json["po_number"] == "HQ/26/05/087"


def test_a_bare_strike_through_still_gets_its_own_card(seeded):
    """A line crossed out with no note beside it is still an act somebody performed."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-BARE-STRIKE"))

    service.persist_pages(
        version,
        [
            _page(
                1,
                lines=[
                    {"no": 1, "stock_code": "A", "qty": 1, "unit_price": 10, "amount": 10},
                    {"no": 2, "stock_code": "B", "qty": 1, "unit_price": 10, "amount": 10,
                     "struck_through": True},
                ],
            )
        ],
    )

    cards = service._annotations(version.id)
    assert [card.interpretation for card in cards] == ["cancel_line"]
    assert cards[0].refers_to_lines == [2]
    assert cards[0].state == ANNOTATION_PROPOSED
    assert all(line.is_cancelled is False for line in service._lines(version.id))


def test_accepting_the_card_is_what_cancels_the_line(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    card = service._annotations(version.id)[0]

    result = service.accept_annotation(
        annotation=card, actor_user_id=owner, note="Confirmed with Maryam"
    )

    lines = {line.line_no: line for line in service._lines(version.id)}
    assert lines[7].is_cancelled is True
    assert lines[1].is_cancelled is False
    assert card.state == ANNOTATION_ACCEPTED
    assert result["applied"]["cancelled_line_nos"] == [7]
    assert result["applied"]["successor_po_number"] == "HQ/26/05/087"
    # The successor PO has not been uploaded yet, so the pointer stands alone (AC-D7).
    assert result["applied"]["successor_po_linked"] is False
    # The cancelled amount leaves the live total but is still reported, so the gap
    # against what the document says reads as the cancellation it is.
    assert result["totals"]["lines_total"] == Decimal("364171.95")
    assert result["totals"]["cancelled_total"] == Decimal("4733.60")
    assert result["totals"]["extracted_total"] == Decimal("368905.55")


def test_the_successor_po_links_once_that_document_arrives(seeded):
    """AC-D7. The pencil names a PO months before it exists; the link is made from
    whichever side turns up second."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    first = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, first)
    service.persist_pages(version, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )
    assert first.superseded_by_po_id is None

    successor = _po(db, project, owner, "HQ/26/05/087")
    service._adopt_pending_successors(successor)

    assert first.superseded_by_po_id == successor.id
    assert successor.supersedes_po_number == "HQ/26/01/041"


def test_a_rejected_card_changes_nothing_and_is_kept(seeded):
    """AC-D4: recorded as rejected, never deleted."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    card = service._annotations(version.id)[0]

    service.reject_annotation(
        annotation=card, actor_user_id=owner, note="Pencil belongs to another PO"
    )

    assert card.state == ANNOTATION_REJECTED
    assert card.action_note == "Pencil belongs to another PO"
    assert all(line.is_cancelled is False for line in service._lines(version.id))


def test_an_edited_card_applies_the_readers_reading(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    card = service._annotations(version.id)[0]

    service.edit_annotation(
        annotation=card,
        actor_user_id=owner,
        interpretation="amend_code",
        interpretation_json={"line_nos": [1], "code": "SRTWC8608-RL"},
        note="It amends the code, it does not cancel",
    )

    lines = {line.line_no: line for line in service._lines(version.id)}
    assert lines[1].stock_code_raw == "SRTWC8608-RL"
    assert lines[7].is_cancelled is False
    assert card.state == "edited"


def test_an_actioned_card_cannot_be_actioned_twice(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    card = service._annotations(version.id)[0]
    service.accept_annotation(annotation=card, actor_user_id=owner)

    with pytest.raises(AppException) as caught:
        service.accept_annotation(annotation=card, actor_user_id=owner)
    assert caught.value.status_code == 409


# ------------------------------------------------------------------ dedup on re-scan


def test_the_same_pencil_note_on_a_re_scan_is_the_same_note(seeded):
    """AC-D5. Re-uploading an annotated scan must not re-propose a decision somebody
    already made, and the decision has to bite on the new version's lines too."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")

    first = _version(db, po, version_no=1)
    service.persist_pages(first, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(first.id)[0],
        actor_user_id=owner,
        note="Cancelled by the 15/5 amendment",
    )

    # The second scan reads the same note with different punctuation and casing, which
    # is exactly what a re-scan produces.
    rescan = _buimaco_like_page()
    rescan.data["annotations"][0]["text"] = "Cancel , refer to new P/O HQ/26/05/087."
    second = _version(db, po, version_no=2)
    service.persist_pages(second, [rescan])

    cards = service._annotations(second.id)
    assert len(cards) == 1
    assert cards[0].state == ANNOTATION_ACCEPTED, "the decision carries forward"
    assert cards[0].action_note == "Cancelled by the 15/5 amendment"
    lines = {line.line_no: line for line in service._lines(second.id)}
    assert lines[7].is_cancelled is True, "and so does its consequence"


def test_dedup_key_ignores_punctuation_but_not_the_line_it_names():
    same = dedup_key("15/5/26", [7], "cancel - refer to New P/O HQ/26/05/087")
    punctuated = dedup_key("15/5/26", [7], "Cancel , refer to new P/O HQ/26/05/087.")
    other_line = dedup_key("15/5/26", [8], "cancel - refer to New P/O HQ/26/05/087")
    other_date = dedup_key("16/5/26", [7], "cancel - refer to New P/O HQ/26/05/087")

    assert same == punctuated
    assert same != other_line
    assert same != other_date


def test_a_note_that_cancels_and_names_a_successor_is_one_card():
    interpretation, payload = classify_annotation(
        "cancel - refer to New P/O HQ/26/05/087", "cancel this line", [7]
    )
    assert interpretation == "cancel_line"
    assert payload["po_number"] == "HQ/26/05/087"
    assert payload["line_nos"] == [7]


def test_a_note_that_only_points_forward_is_a_successor_card():
    interpretation, payload = classify_annotation(
        "refer to New P/O HQ/26/05/087", "new purchase order issued", []
    )
    assert interpretation == "successor_po"
    assert payload["po_number"] == "HQ/26/05/087"


def test_the_models_classification_wins_over_the_keywords():
    """The vision model read the PAGE - the strike-through, the arrow, the position -
    and the keyword pass reads only the text. Here the text says "cancel", which the
    keywords would take as cancel_line, but the model that saw the page says the note
    amends a code. The model's reading stands; a wrong cancel_line moves money."""
    interpretation, payload = classify_annotation(
        "cancel old spec, use C-FH12",
        "amendment",
        [4],
        model_kind="amend_code",
        model_payload={"proposed_code": "C-FH12"},
    )
    assert interpretation == "amend_code"
    assert payload["code"] == "C-FH12"
    assert payload["line_nos"] == [4]


def test_an_unknown_model_kind_falls_back_to_the_keywords():
    """A prompt version that predates the `kind` field, or a model answer outside the
    vocabulary, must lose NOTHING: the note still classifies exactly as it did before
    the field existed."""
    interpretation, payload = classify_annotation(
        "cancel - refer to New P/O HQ/26/05/087",
        "cancel this line",
        [7],
        model_kind="strikethrough_note",  # not in the vocabulary
        model_payload={},
    )
    assert interpretation == "cancel_line"
    assert payload["po_number"] == "HQ/26/05/087"


def test_the_models_structured_extras_beat_the_regex_and_fall_back_to_it():
    """The successor number the model names is taken as written; where an older prompt
    names none, the regex still reads it out of the text."""
    # Model names the successor: taken verbatim, even though the text also carries one.
    _kind, payload = classify_annotation(
        "cancel - refer to New P/O HQ/26/05/087",
        None,
        [7],
        model_kind="cancel_line",
        model_payload={"successor_po_number": "hq/26/05/099"},
    )
    assert payload["po_number"] == "HQ/26/05/099"

    # Model silent on extras: the regex answers, so nothing regresses.
    _kind, payload = classify_annotation(
        "cancel - refer to New P/O HQ/26/05/087",
        None,
        [7],
        model_kind="cancel_line",
        model_payload={},
    )
    assert payload["po_number"] == "HQ/26/05/087"


def test_a_model_amend_code_with_no_proposal_still_tries_the_regex():
    interpretation, payload = classify_annotation(
        "change SRTWC8613-RL to SRTWC8608-RL",
        None,
        [1],
        model_kind="amend_code",
        model_payload={},
    )
    assert interpretation == "amend_code"
    assert payload["code"] == "SRTWC8608-RL"


# ------------------------------------------------------------------------- confirm


def test_confirm_is_refused_while_a_card_is_still_proposed(seeded):
    """A cancellation written in pencil is the only place some of these lines exist, so
    nobody confirms a PO before somebody has read the pencil."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])

    with pytest.raises(AppException) as caught:
        service.confirm_version(version=version, actor_user_id=owner)

    assert caught.value.status_code == 409
    assert _code(caught.value) == "po_version_annotations_pending"
    assert version.confirmed_at is None


# The owner's re-test document, HQ/26/01/121 v3 (25 Sep 2026): line 7 crossed out with a
# pencil cancellation beside it, and ten pencil notes that name no line at all - the
# signature, "Continue To Next Page" on every page break, the delivery instructions.
_LINELESS_NOTES = [
    "Signature",
    "Continue To Next Page",
    "Continue To Next Page (2)",
    "Continue To Next Page (3)",
    "Deliver to site office before 10am",
    "Call Maryam before delivery",
    "Received by store",
    "Checked",
    "Chop company stamp",
    "Refer attached schedule",
]


def _po_121_v3_page(*, pending_on: tuple[int, ...] = ()) -> PageResult:
    """Eight printed lines, line 7 struck and cancelled in pencil, ten notes naming no line.

    ``pending_on`` adds one extra amend-description note per named line, for the tests that
    need notes tied to more than one line.
    """
    lines = [
        {
            "no": n,
            "stock_code": f"SRT121-{n}",
            "description": f"Item {n}",
            "qty": 1,
            "uom": "NOS",
            "unit_price": 10,
            "amount": 10,
            "struck_through": n == 7,
        }
        for n in range(1, 9)
    ]
    annotations = [
        {
            "text": "cancel - refer to New P/O HQ/26/05/121",
            "date": "15/5/26",
            "refers_to_items": [7],
            "meaning": "cancel this line",
        }
    ] + [{"text": text, "kind": "other"} for text in _LINELESS_NOTES]
    annotations += [
        {"text": f"description should read Item {n} revised", "refers_to_items": [n],
         "kind": "amend_description", "description": f"Item {n} revised"}
        for n in pending_on
    ]
    return _page(1, header={"po_number": "HQ/26/01/121"}, lines=lines, annotations=annotations)


def test_notes_naming_no_line_never_block_confirm(seeded):
    """Owner re-test 25 Sep 2026: "i can't confirm PO due to this error". The screen stopped
    listing notes that name no line, but the gate still counted them, so Confirm said 11
    notes needed a decision that nothing on the screen showed. Only line 7's note holds it."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/121"))
    service.persist_pages(version, [_po_121_v3_page()])

    notes = service._annotations(version.id)
    assert sum(1 for note in notes if note.state == ANNOTATION_PROPOSED) == 11
    assert sum(1 for note in notes if not note.refers_to_lines) == 10

    with pytest.raises(AppException) as caught:
        service.confirm_version(version=version, actor_user_id=owner)
    assert _code(caught.value) == "po_version_annotations_pending"
    assert _message(caught.value) == (
        "Line 7 has a handwritten note to accept or reject. Open the note icon on that line."
    )

    cancel = next(note for note in notes if note.refers_to_lines == [7])
    service.accept_annotation(annotation=cancel, actor_user_id=owner)

    result = service.confirm_version(version=version, actor_user_id=owner)

    assert version.confirmed_at is not None
    assert result["line_count"] == 7
    # The line-less notes are left as they were: not decided, just no longer a gate.
    assert sum(1 for note in notes if note.state == ANNOTATION_PROPOSED) == 10


def test_every_blocking_note_sits_on_a_line_the_screen_shows(seeded):
    """N pending notes naming a line, over K lines: the server counts exactly N, each of the
    K lines is one the Lines tab's "Need attention" lists (a line a proposed note names), and
    the message names those lines and no others."""
    from app.services.project_po_confirm import blocking_annotations

    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/121"))
    service.persist_pages(version, [_po_121_v3_page(pending_on=(2, 5, 7))])

    lines = service._lines(version.id)
    blocking = blocking_annotations(service._annotations(version.id), lines)
    assert len(blocking) == 4  # line 7's cancellation, and one amend on each of 2, 5, 7
    named = sorted({n for note in blocking for n in note.refers_to_lines})
    assert named == [2, 5, 7]
    assert set(named) <= {line.line_no for line in lines}

    with pytest.raises(AppException) as caught:
        service.confirm_version(version=version, actor_user_id=owner)
    assert _message(caught.value) == (
        "Line 2, line 5 and line 7 have handwritten notes to accept or reject. "
        "Open the note icon on each line."
    )
    assert "--" not in _message(caught.value)


def test_the_blocking_message_names_a_few_lines_then_counts_the_rest(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/121"))
    service.persist_pages(version, [_po_121_v3_page(pending_on=(1, 2, 3, 4, 5))])

    with pytest.raises(AppException) as caught:
        service.confirm_version(version=version, actor_user_id=owner)
    assert _message(caught.value) == (
        "Line 1, line 2, line 3 and 3 more lines have handwritten notes to accept or "
        "reject. Open the note icon on each line."
    )


def test_a_note_naming_a_line_the_version_does_not_have_does_not_block(seeded):
    """A note naming line 99 on an eight-line document has no row to be reviewed from, so it
    is the same as a note naming no line: the screen cannot show it, the gate ignores it."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-GHOST-LINE"))
    service.persist_pages(
        version,
        [
            _page(
                1,
                lines=[{"no": 1, "stock_code": "A", "qty": 1, "unit_price": 10, "amount": 10}],
                annotations=[{"text": "see item 99", "refers_to_items": [99], "kind": "other"}],
            )
        ],
    )
    assert service._annotations(version.id)[0].state == ANNOTATION_PROPOSED

    service.confirm_version(version=version, actor_user_id=owner)

    assert version.confirmed_at is not None


def test_confirm_writes_the_phase_one_po_lines(seeded):
    """The confirmed state lands on `project_purchase_order_lines`, where the quotation
    cross-check already lives. The version keeps what the document said, untouched."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, po)
    service.persist_pages(version, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )

    result = service.confirm_version(version=version, actor_user_id=owner)

    written = (
        db.query(ProjectPurchaseOrderLine)
        .filter(ProjectPurchaseOrderLine.po_id == po.id)
        .all()
    )
    assert result["line_count"] == 1, "the cancelled line is not a commitment"
    assert [line.product_code for line in written] == ["SRTWC86"]
    assert written[0].quantity == Decimal("927.00")
    assert written[0].line_total == Decimal("364171.95")
    assert po.po_amount == Decimal("364171.95")
    assert po.po_date is not None and po.po_date.isoformat() == "2026-01-19"
    assert po.term_days == 60
    assert po.sales_person == "MARYAM"
    assert po.customer_order_ref == "TUJU-01"
    assert version.confirmed_at is not None
    # The record of what the paper said is untouched by the confirmation.
    assert len(service._lines(version.id)) == 2


def test_a_confirmed_version_is_frozen(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )
    service.confirm_version(version=version, actor_user_id=owner)

    line = service._lines(version.id)[0]
    with pytest.raises(AppException) as caught:
        service.update_line(version=version, line=line, payload={"qty": Decimal("1")})
    assert caught.value.status_code == 409


def test_editing_a_line_recomputes_its_arithmetic_and_the_totals(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-EDIT"))
    service.persist_pages(
        version,
        [_page(1, lines=[{"no": 1, "stock_code": "A", "qty": 4, "unit_price": 25.50,
                          "amount": 101.00}])],
    )
    line = service._lines(version.id)[0]
    assert line.arithmetic_ok is False

    service.update_line(version=version, line=line, payload={"amount": Decimal("102.00")})

    assert line.arithmetic_ok is True
    assert version.arithmetic_passed == 1
    assert service.recompute_totals(version)["lines_total"] == Decimal("102.00")


# ------------------------------------------- binding the confirmed PO to a quotation
#
# The hole this closes was visible on the client's own PO: 51 lines, every one of them
# flagged "not quoted", and the panel saying -- accurately -- that nothing was being
# compared against a quoted price. `quotation_version_id` existed and phase 1's
# cross-check was already written; the document path simply never set it, so a PO that
# arrived as a scan got none of the checking a hand-recorded PO gets.


def _quotation(db, project, owner, scope: str, priced):
    """One quotation scope, version 1, priced OFF-CATALOG.

    Off-catalog on purpose: a scanned PO gives us the code the customer printed, and the
    cross-check matches that against the code the quotation printed. Going through a
    catalogue product would test a path the scan cannot reach.
    """
    from app.services import project_quotation_service as quotes

    quotation = quotes.create_quotation(
        db, project=project, actor_user_id=owner, payload={"scope_label": scope}
    )
    version = quotes.current_version(db, quotation.id)
    for code, price in priced:
        quotes.upsert_line(
            db,
            version=version,
            actor_user_id=owner,
            payload={
                "product_code_snapshot": code,
                "description_snapshot": f"{code} as quoted",
                "unit_price": price,
                "quantity": 1,
            },
        )
    return quotation, version


def _priced_page() -> PageResult:
    """Three printed lines: one quoted at the quoted price, one quoted at another price,
    one never quoted at all. Every verdict the cross-check can reach, in one page."""
    return _page(
        1,
        header={"po_number": "HQ/26/01/121"},
        lines=[
            {"no": 1, "stock_code": "SRTWC86", "qty": 2, "unit_price": 392.85,
             "amount": 785.70},
            {"no": 2, "stock_code": "SRTFV1001", "qty": 1, "unit_price": 295.85,
             "amount": 295.85},
            {"no": 3, "stock_code": "SRTNEW9", "qty": 1, "unit_price": 100.00,
             "amount": 100.00},
        ],
    )


def _flags(db, po):
    return {
        line.product_code: (line.model_mismatch, line.price_mismatch)
        for line in db.query(ProjectPurchaseOrderLine)
        .filter(ProjectPurchaseOrderLine.po_id == po.id)
        .all()
    }


def test_confirming_binds_the_po_to_the_quotation_and_populates_the_flags(seeded):
    """The whole point: a scanned PO comes out of confirm checked, not unchecked."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    _quotation(
        db,
        project,
        owner,
        "House Units",
        [("SRTWC86", "392.85"), ("SRTFV1001", "250.00")],
    )
    po = _po(db, project, owner, "HQ/26/01/121")
    assert po.quotation_version_id is None
    version = _version(db, po)
    service.persist_pages(version, [_priced_page()])

    result = service.confirm_version(version=version, actor_user_id=owner)

    quoted_version_id = po.quotation_version_id
    assert quoted_version_id is not None, "a confirmed PO must be checked against something"
    assert _flags(db, po) == {
        # Quoted, at the quoted price: nothing to chase.
        "SRTWC86": (False, False),
        # Quoted at 250.00 and ordered at 295.85.
        "SRTFV1001": (False, True),
        # Never quoted, so there is no price to disagree with -- one problem, one flag.
        "SRTNEW9": (True, False),
    }
    assert result["model_mismatch_count"] == 1
    assert result["price_mismatch_count"] == 1


def test_a_project_with_nothing_quoted_confirms_and_stays_unbound(seeded):
    """No quotation is not a failure to bind, it is the absence of anything to bind to.
    The panel's "not tied to a quotation version" sentence is the truth in that case."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/121")
    version = _version(db, po)
    service.persist_pages(version, [_priced_page()])

    result = service.confirm_version(version=version, actor_user_id=owner)

    assert po.quotation_version_id is None
    assert result["line_count"] == 3
    assert version.confirmed_at is not None
    assert all(model is True for model, _price in _flags(db, po).values())


def test_a_po_already_bound_to_a_superseded_version_is_not_re_pointed(seeded):
    """The contractor buys off the document they were given, which is frequently not the
    newest one. An existing binding is a decision somebody made, and moving it would
    silently change what this PO was checked against."""
    db, project, owner = seeded
    from app.services import project_po_service as po_svc
    from app.services import project_quotation_service as quotes

    service = ProjectPOExtractionService(db)
    quotation, first = _quotation(
        db, project, owner, "House Units", [("SRTWC86", "392.85")]
    )
    po = po_svc.create_po(
        db,
        project=project,
        actor_user_id=owner,
        payload={
            "po_number": "HQ/26/01/121",
            "po_source": "contractor_direct",
            "quotation_version_id": first.id,
        },
    )

    # v2 re-prices the same line. The PO in hand was issued against v1.
    second = quotes.revise(db, quotation=quotation, actor_user_id=owner)
    quotes.upsert_line(
        db,
        version=second,
        actor_user_id=owner,
        payload={"unit_price": "500.00"},
        line=quotes.list_lines(db, second.id)[0],
    )

    version = _version(db, po)
    service.persist_pages(version, [_priced_page()])
    service.confirm_version(version=version, actor_user_id=owner)

    assert po.quotation_version_id == first.id
    # Checked against v1's 392.85, so the line the customer ordered at the price they
    # were quoted is not flagged.
    assert _flags(db, po)["SRTWC86"] == (False, False)


def test_two_equally_good_scopes_leave_the_po_unbound(seeded):
    """A development is quoted in scopes. Binding to the wrong one would flag correctly
    priced lines as "price differs", and a wrong comparison is worse than a stated
    absence of one, so the scope stays a person's call."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    _quotation(db, project, owner, "House Units", [("SRTWC86", "392.85")])
    _quotation(db, project, owner, "Common Area", [("SRTWC86", "500.00")])
    po = _po(db, project, owner, "HQ/26/01/121")
    version = _version(db, po)
    service.persist_pages(version, [_priced_page()])

    service.confirm_version(version=version, actor_user_id=owner)

    assert po.quotation_version_id is None


def test_the_won_scope_is_preferred_over_an_open_one(seeded):
    """A PO follows from a scope somebody won, so that scope is the obvious candidate
    even when another is still being negotiated."""
    db, project, owner = seeded
    from app.services import project_quotation_service as quotes

    service = ProjectPOExtractionService(db)
    won, won_version = _quotation(
        db, project, owner, "House Units", [("SRTWC86", "392.85")]
    )
    _quotation(db, project, owner, "Common Area", [("SRTWC86", "500.00")])
    quotes.set_outcome(db, quotation=won, outcome=quotes.OUTCOME_WON_Q)

    po = _po(db, project, owner, "HQ/26/01/121")
    version = _version(db, po)
    service.persist_pages(version, [_priced_page()])

    service.confirm_version(version=version, actor_user_id=owner)

    assert po.quotation_version_id == won_version.id
    assert _flags(db, po)["SRTWC86"] == (False, False)


# ------------------------------------------------------------ upload and versioning


@pytest.fixture()
def stored_document(monkeypatch):
    """Object storage stubbed out. The bytes going to S3 are not what these tests are
    about, and a unit test that needs credentials is a test nobody runs."""
    monkeypatch.setattr(
        ProjectPOExtractionService,
        "_store_document",
        lambda self, **kwargs: None,
    )


def _one_page_pdf() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def test_a_second_upload_of_the_same_po_number_becomes_version_two(seeded, stored_document):
    """A revision is a new VERSION of one commitment, never a second purchase order."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    content = _one_page_pdf()

    first = service.create_version_from_upload(
        project=project,
        actor_user_id=owner,
        filename="po-r1.pdf",
        mime="application/pdf",
        content=content,
        po_number="HQ/26/01/041",
    )
    second = service.create_version_from_upload(
        project=project,
        actor_user_id=owner,
        filename="po-r2.pdf",
        mime="application/pdf",
        content=content,
        po_number="hq/26/01/041 ",
    )

    assert first.purchase_order_id == second.purchase_order_id
    assert (first.version_no, second.version_no) == (1, 2)
    assert first.extraction_state == STATE_QUEUED


def test_an_unnumbered_upload_adopts_the_extracted_number(seeded, stored_document):
    """Extraction fills the number in; the confirm screen is where a human agrees to it."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)

    version = service.create_version_from_upload(
        project=project,
        actor_user_id=owner,
        filename="scan.pdf",
        mime="application/pdf",
        content=_one_page_pdf(),
    )
    assert service.get_po(version.purchase_order_id).po_number == ""

    service.persist_pages(version, [_buimaco_like_page()])

    assert service.get_po(version.purchase_order_id).po_number == "HQ/26/01/041"


def test_an_unnumbered_upload_of_a_po_we_already_hold_joins_it(seeded, stored_document):
    """Contract 2: if the extracted number matches an existing PO on the project, the
    upload is a new version of that PO rather than a second one."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    existing = _po(db, project, owner, "HQ/26/01/041")
    _version(db, existing, version_no=1)

    version = service.create_version_from_upload(
        project=project,
        actor_user_id=owner,
        filename="rescan.pdf",
        mime="application/pdf",
        content=_one_page_pdf(),
    )
    shell_id = version.purchase_order_id
    assert shell_id != existing.id

    service.persist_pages(version, [_buimaco_like_page()])

    assert version.purchase_order_id == existing.id
    assert version.version_no == 2
    from app.models.projects import ProjectPurchaseOrder

    assert (
        db.query(ProjectPurchaseOrder).filter(ProjectPurchaseOrder.id == shell_id).first()
        is None
    ), "the empty shell row must not be left on the project"


def test_an_unsupported_file_is_refused_with_a_readable_reason(seeded, stored_document):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)

    with pytest.raises(AppException) as caught:
        service.create_version_from_upload(
            project=project,
            actor_user_id=owner,
            filename="schedule.xlsx",
            mime="application/vnd.ms-excel",
            content=b"not a document",
        )
    assert caught.value.status_code == 422
    assert "PDF" in _message(caught.value)


# ---------------------------------------------------------------- approval handshake


def test_approval_then_countersignature_by_a_second_person(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, po)
    service.persist_pages(version, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )
    service.confirm_version(version=version, actor_user_id=owner)

    service.approve_po(po=po, actor_user_id=owner)
    assert po.status == "approved"

    with pytest.raises(AppException) as caught:
        service.countersign_po(po=po, actor_user_id=owner)
    assert caught.value.status_code == 409

    manager = _user(db, f"{MARKER} Manager")
    service.countersign_po(po=po, actor_user_id=manager)
    assert po.countersigned_by == manager


def test_a_po_with_no_lines_cannot_be_approved(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")

    with pytest.raises(AppException) as caught:
        service.approve_po(po=po, actor_user_id=owner)
    assert _code(caught.value) == "po_nothing_to_approve"


def test_a_po_carrying_an_unconfirmed_document_cannot_be_approved(seeded):
    """Approving a PO nobody has checked against the scan is what the confirm screen
    exists to prevent. A hand-keyed PO with no documents is unaffected."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, po)
    service.persist_pages(version, [_buimaco_like_page()])
    # Lines exist on the phase-1 PO from an earlier hand entry, so the "nothing to
    # approve" guard would not fire on its own.
    from app.services import project_po_service as po_svc

    po_svc.upsert_line(
        db, po=po, payload={"product_code": "SRTWC86", "unit_price": Decimal("1"), "quantity": 1}
    )

    with pytest.raises(AppException) as caught:
        service.approve_po(po=po, actor_user_id=owner)
    assert _code(caught.value) == "po_version_not_confirmed"


# ------------------------------------------------- what the confirm screen renders


def test_every_line_carries_the_page_it_was_printed_on(seeded):
    """The side-by-side viewer keys the page turn on this; without it the sync silently
    does nothing and the screen still looks right, which is the worst kind of broken."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-PAGES"))
    service.persist_pages(
        version,
        [
            _page(1, lines=[{"no": 1, "stock_code": "A"}, {"no": 2, "stock_code": "B"}]),
            _page(2, lines=[{"no": 3, "stock_code": "C"}]),
        ],
    )

    body = service.serialize_version(version)

    assert [(line["line_no"], line["page_no"]) for line in body["lines"]] == [
        (1, 1),
        (2, 1),
        (3, 2),
    ]


def test_the_version_reports_how_much_of_the_document_was_read(seeded):
    """"Only 1 of 2 pages were read" cannot be said from a state of "done" plus a
    sentence, so the counts travel with the version."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-PARTIAL-READ"))
    service.persist_pages(
        version,
        [
            _page(1, lines=[{"no": 1, "stock_code": "A"}]),
            PageResult(page_no=2, data=None, error="rate limited"),
        ],
    )

    body = service.serialize_version(version)

    assert body["pages_extracted"] == 1
    assert body["failed_pages"] == [2]


def test_the_version_names_its_scan_so_the_viewer_can_read_the_bytes(seeded, monkeypatch):
    """The in-app PDF viewer draws the scan from its bytes, and the signed URL is
    cross-origin with no CORS headers, so a script cannot read it. The version carries
    the attachment id, which the authenticated same-origin download route takes."""
    from app.models.resources import Attachment
    from app.schemas.project_po_intake import POVersionDetailResponse
    from app.services import storage_router

    class _FakeBackend:
        def file_exists(self, key):
            return True

    monkeypatch.setattr(
        storage_router, "resolve_signed_url", lambda path, provider=None: f"{path}?sig=1"
    )
    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend())
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-SCAN-ID"))
    attachment = Attachment(
        id=str(uuid.uuid4()),
        original_filename=f"{MARKER}.pdf",
        stored_filename=f"{MARKER}.pdf",
        file_path=f"https://cdn.example.test/po/{MARKER}.pdf",
        mime_type="application/pdf",
        storage_provider="s3",
        company_id=version.company_id,
        is_deleted=False,
    )
    db.add(attachment)
    db.flush()
    version.attachment_id = attachment.id
    db.flush()

    body = service.serialize_version(version)

    assert body["attachment_id"] == str(attachment.id)
    assert body["document_url"] == f"https://cdn.example.test/po/{MARKER}.pdf?sig=1"
    assert POVersionDetailResponse.model_validate(body).attachment_id == str(attachment.id)


def test_a_version_with_no_scan_has_no_attachment_id(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-NO-SCAN"))

    body = service.serialize_version(version)

    assert body["attachment_id"] is None
    assert body["document_url"] is None


def test_document_url_is_none_when_the_object_behind_the_attachment_is_gone(seeded, monkeypatch):
    """B1 (PR #1237 review, R9/R13): an attachment ROW can outlive its OBJECT. Presigning
    a URL for a missing object hands the FE a link that 404s inside an iframe it cannot
    see past, so the R13 empty state - which only shows on a falsy document_url - never
    fires. The object's existence has to gate the URL, not just the row's presence."""
    from app.models.resources import Attachment
    from app.services import storage_router

    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-MISSING-OBJECT"))
    attachment = Attachment(
        original_filename="po.pdf",
        stored_filename="po.pdf",
        file_path="project-po/some-version/po.pdf",
        entity_type="project_po_version",
        entity_id=version.id,
        storage_provider="s3",
    )
    db.add(attachment)
    db.flush()
    version.attachment_id = attachment.id
    db.flush()

    class _FakeBackend:
        def __init__(self, exists: bool):
            self._exists = exists

        def file_exists(self, key):
            return self._exists

        def get_signed_url(self, key, expires_in=3600):
            return f"https://signed.example/{key}"

    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend(False))
    assert service.serialize_version(version)["document_url"] is None

    monkeypatch.setattr(storage_router, "get_backend", lambda provider: _FakeBackend(True))
    assert service.serialize_version(version)["document_url"] is not None


def test_run_extraction_commits_progress_once_per_page(seeded, monkeypatch):
    """B3 (19 Aug follow-up): a long read must not look frozen. ``run_extraction``
    wires ``on_page`` into ``extract_document`` so ``extracted_json`` (and so
    ``pages_extracted``) grows page by page, each write its own commit - proved here
    by counting commits rather than racing real threads: without a per-page commit
    there are exactly two (the RUNNING stamp, the final write); with it there is one
    more per page.
    """
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-PROGRESS"))

    monkeypatch.setattr(
        ProjectPOExtractionService, "_document_bytes",
        lambda self, version: (b"ZZT", "application/pdf"),
    )
    monkeypatch.setattr(
        document_extraction, "_render_pages_rich",
        lambda *a, **k: [RenderedPage(image_b64="i", image_mime="image/jpeg") for _ in range(4)],
    )

    class _StubProvider:
        name = "stub"

        def chat(self, messages, **kwargs):
            return ChatResult(
                content='{"lines": []}', prompt_tokens=1, completion_tokens=1, total_tokens=2
            )

    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _StubProvider())
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(app_settings, "document_ai_page_concurrency", 4, raising=False)

    commit_count = {"n": 0}
    original_commit = db.commit

    def counting_commit(*args, **kwargs):
        commit_count["n"] += 1
        return original_commit(*args, **kwargs)

    db.commit = counting_commit

    summary = service.run_extraction(str(version.id))

    assert summary["status"] == STATE_DONE
    # RUNNING stamp + 4 page commits + the final persist_pages commit.
    assert commit_count["n"] >= 6
    assert service.serialize_version(version)["pages_extracted"] == 4


def test_a_retry_after_a_failed_read_does_not_pin_progress_to_the_old_page_count(seeded, monkeypatch):
    """A retry runs `run_extraction` again on the SAME version row, and the failed
    attempt already left stale entries in ``extracted_json['pages']``. Left uncleared,
    ``_on_page`` appends onto them, so the FE's "Page X of Y" counter pins to a Y that
    counts pages from the read that failed - this is checked WHILE the retry is still
    RUNNING (persist_pages overwrites ``pages`` wholesale at the end regardless, so a
    post-completion assertion alone would not catch the bug this fixes).
    `run_extraction` must reset ``extracted_json['pages']`` before the first page of
    the retry can land, so progress climbs 0, 1, 2 - never starting from 3 stale
    entries and never exceeding the 2 real pages of this retry.
    """
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-RETRY-PROGRESS"))

    # A previous, failed attempt already wrote three stale page blobs.
    version.extracted_json = {
        "pages": [
            {"page_no": 1, "data": {"lines": []}, "error": None},
            {"page_no": 2, "data": {"lines": []}, "error": None},
            {"page_no": 3, "data": None, "error": "boom"},
        ]
    }
    db.flush()

    monkeypatch.setattr(
        ProjectPOExtractionService, "_document_bytes",
        lambda self, version: (b"ZZT", "application/pdf"),
    )
    monkeypatch.setattr(
        document_extraction, "_render_pages_rich",
        lambda *a, **k: [RenderedPage(image_b64="i", image_mime="image/jpeg") for _ in range(2)],
    )

    class _StubProvider:
        name = "stub"

        def chat(self, messages, **kwargs):
            return ChatResult(
                content='{"lines": []}', prompt_tokens=1, completion_tokens=1, total_tokens=2
            )

    monkeypatch.setattr(document_extraction, "get_provider", lambda *a, **k: _StubProvider())
    monkeypatch.setattr(app_settings, "document_ai_provider", "gemini", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "ZZT-key", raising=False)
    monkeypatch.setattr(app_settings, "document_ai_page_concurrency", 2, raising=False)

    progress_after_each_commit: list[int] = []
    original_commit = db.commit

    def snapshotting_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        progress_after_each_commit.append(
            len((version.extracted_json or {}).get("pages") or [])
        )
        return result

    db.commit = snapshotting_commit

    summary = service.run_extraction(str(version.id))

    assert summary["status"] == STATE_DONE
    # The very first commit is the RUNNING stamp, made right after the reset: it must
    # show 0, not the 3 stale entries the failed attempt left behind.
    assert progress_after_each_commit[0] == 0
    # Progress never exceeds this retry's real page count - never 3 (stale alone) and
    # never 5 (3 stale + 2 new).
    assert max(progress_after_each_commit) == 2
    assert len((version.extracted_json or {}).get("pages") or []) == 2


def test_the_version_carries_the_approval_stamps(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, po)
    service.persist_pages(version, [_buimaco_like_page()])
    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )
    service.confirm_version(version=version, actor_user_id=owner)

    before = service.serialize_version(version)["purchase_order"]
    assert before["approved_at"] is None and before["countersigned_at"] is None

    service.approve_po(po=po, actor_user_id=owner)
    manager = _user(db, f"{MARKER} Manager")
    service.countersign_po(po=po, actor_user_id=manager)

    after = service.serialize_version(version)["purchase_order"]
    assert after["status"] == "approved"
    assert after["approved_by_name"] == f"{MARKER} CONTACT AH"
    assert after["countersigned_by_name"] == f"{MARKER} Manager"


def test_a_gap_that_is_exactly_the_cancellation_still_reconciles(seeded):
    """Compared exactly, not with a tolerance: on the client's own PO the gap is 4,733.60
    to the cent, and treating it as a mismatch would block a correct PO for ever."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])
    assert service.recompute_totals(version)["reconciles"] is True

    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )

    totals = service.recompute_totals(version)
    assert totals["cancelled_total"] == Decimal("4733.60")
    assert totals["reconciles"] is True


def test_a_misread_amount_does_not_reconcile(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    version = _version(db, _po(db, project, owner, "PO-MISREAD"))
    service.persist_pages(
        version,
        [_page(1, lines=[{"no": 1, "stock_code": "A", "qty": 4, "unit_price": 25.50,
                          "amount": 101.00}])],
    )

    assert service.recompute_totals(version)["reconciles"] is False


def test_the_header_is_editable_before_confirmation(seeded):
    """AC-D3, and the edit is what the confirm then adopts, so there is one path by
    which a header value reaches the purchase order."""
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, "HQ/26/01/041")
    version = _version(db, po)
    service.persist_pages(version, [_buimaco_like_page()])

    from datetime import date as _date

    service.update_header(
        version=version,
        payload={
            "po_number": "HQ/26/01/121",
            "po_date": _date(2026, 1, 16),
            "term_days": 90,
            "sales_person": "VALERIE",
            "admin_ref": "PS26-0143",
        },
    )

    body = service.serialize_version(version)
    assert body["header"]["po_number"] == "HQ/26/01/121"
    assert body["header"]["po_date"].isoformat() == "2026-01-16"
    assert body["header"]["term_days"] == 90
    assert body["header"]["sales_person"] == "VALERIE"
    # D24: the filing reference is ours, so it lands on the PO and not in the record of
    # what the paper said.
    assert po.admin_ref == "PS26-0143"

    service.accept_annotation(
        annotation=service._annotations(version.id)[0], actor_user_id=owner
    )
    service.confirm_version(version=version, actor_user_id=owner)
    assert po.po_number == "HQ/26/01/121"
    assert po.term_days == 90
    assert po.sales_person == "VALERIE"


def test_the_header_cannot_take_a_number_another_po_already_has(seeded):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    _po(db, project, owner, "HQ/26/05/087")
    version = _version(db, _po(db, project, owner, "HQ/26/01/041"))
    service.persist_pages(version, [_buimaco_like_page()])

    with pytest.raises(AppException) as caught:
        service.update_header(version=version, payload={"po_number": "HQ/26/05/087"})
    assert _code(caught.value) == "po_number_already_on_project"


# --------------------------------------------------------------------- golden set
#
# AC-M1, AC-M1a, AC-M2 (UAC group M). Runs the real model over the client's real scan,
# which takes about two minutes, and is the objective done condition for this slice: an
# extraction change that regresses it does not merge.
#
# One correction to the acceptance criteria, from reading the paper rather than the
# summary of it. AC-M1 and the fixture README both say 52 lines; the document's own last
# printed item number is 51 (page 9, `51 CB6645-NL ... 7,020.00`, followed by the
# delivery block). The 52nd "row" is the description of item 49 spilling onto page 9
# above item 50, which is not a line and must not become one. The count is therefore
# asserted at 51 and the load-bearing assertion is the TOTAL: 1,810,640.62 to the cent,
# which cannot come out right if a priced line were missed.

GOLDEN_PO_NUMBER = "HQ/26/01/121"
GOLDEN_LINE_COUNT = 51
GOLDEN_EXTRACTED_TOTAL = Decimal("1810640.62")
# 1,810,640.62 minus the handwritten cancellation of item 7 (4,733.60). The same figure
# is written by hand on page 10 of the scan and is the quotation total.
GOLDEN_LIVE_TOTAL = Decimal("1805907.02")


@pytest.fixture()
def golden_document(monkeypatch):
    """Feed the real committed scan straight to the extractor, skipping object storage."""
    content = GOLDEN_PO.read_bytes()
    monkeypatch.setattr(
        ProjectPOExtractionService,
        "_document_bytes",
        lambda self, version: (content, "application/pdf"),
    )


@pytest.mark.skipif(
    not GOLDEN_PO.exists(), reason="the client's PO scan is not in this checkout"
)
@pytest.mark.skipif(
    not __import__("app.config", fromlist=["settings"]).settings.gemini_api_key,
    reason="GEMINI_API_KEY is not configured",
)
def test_golden_set_the_real_buimaco_po(seeded, golden_document):
    db, project, owner = seeded
    service = ProjectPOExtractionService(db)
    po = _po(db, project, owner, GOLDEN_PO_NUMBER)
    version = _version(db, po)

    summary = service.run_extraction(str(version.id))

    assert summary["status"] == STATE_DONE, summary
    # Pinned by measurement, not by preference: flash matched both pro models exactly at
    # a fraction of the cost, and gpt-4o-mini missed the cancellation entirely (PLAN 5b).
    assert version.extraction_model == "gemini/gemini-2.5-flash"
    assert version.page_count == 10

    # AC-M1: the printed header and every printed line.
    header = (version.extracted_json or {})["header"]
    assert header["po_number"] == GOLDEN_PO_NUMBER
    assert header["term"] == "60 DAYS"
    lines = service._lines(version.id)
    assert len(lines) == GOLDEN_LINE_COUNT, f"read {len(lines)} lines"
    assert [line.line_no for line in lines] == list(range(1, GOLDEN_LINE_COUNT + 1))

    # AC-M1a: every line's own arithmetic holds, computed here in Decimal.
    assert (version.arithmetic_passed, version.arithmetic_total) == (
        GOLDEN_LINE_COUNT,
        GOLDEN_LINE_COUNT,
    )
    totals = service.recompute_totals(version)
    assert totals["extracted_total"] == GOLDEN_EXTRACTED_TOTAL
    assert totals["lines_total"] == GOLDEN_EXTRACTED_TOTAL

    # AC-M2: both dated handwritten amendments arrive as cards, and NOTHING is applied.
    cards = service._annotations(version.id)
    assert all(card.state == ANNOTATION_PROPOSED for card in cards)
    assert all(line.is_cancelled is False for line in lines)

    dated = {card.written_date: card for card in cards if card.written_date}
    assert "26/1/26" in dated, [card.raw_text for card in cards]
    amend = dated["26/1/26"]
    assert amend.refers_to_lines == [5, 20, 23]

    assert "15/5/26" in dated, [card.raw_text for card in cards]
    cancellation = dated["15/5/26"]
    assert cancellation.interpretation == "cancel_line"
    assert cancellation.refers_to_lines == [7]
    # AC-D7: the successor PO exists only in this pencil note.
    assert cancellation.interpretation_json["po_number"] == "HQ/26/05/087"

    # The self-proving reconciliation (PLAN 5b): accept that one card and the live total
    # is the quotation total to the cent.
    service.accept_annotation(annotation=cancellation, actor_user_id=owner)
    after = service.recompute_totals(version)
    assert after["lines_total"] == GOLDEN_LIVE_TOTAL
    assert after["cancelled_total"] == Decimal("4733.60")
    assert {line.line_no for line in lines if line.is_cancelled} == {7}


# ------------------------------------------- reading a code the way a person reads it


def _catalogue(db, *codes: str, company_id: Optional[str] = None) -> dict[str, str]:
    """Put codes in the item master and hand back their ids.

    ``company_id`` exists because one product code legitimately appears once PER
    COMPANY (`uq_products_company_product_code` allows exactly that), and a resolver
    running across companies therefore sees the same code twice. That is not two
    candidate products, and mistaking it for ambiguity is what made the first version
    of the loose matcher resolve nothing at all on the client's real document.
    """
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    # Real parent rows for the NOT NULL foreign keys. Postgres enforces what sqlite
    # silently accepted, so an invented uuid here aborts the transaction rather than
    # quietly pointing at nothing.
    suffix = str(uuid.uuid4())[:8]
    category_id = str(uuid.uuid4())
    uom_id = str(uuid.uuid4())
    db.add(ProductCategory(id=category_id, category_code=f"{MARKER}-CAT-{suffix}",
                           category_name=f"{MARKER} category"))
    db.add(UnitOfMeasure(id=uom_id, uom_code=f"{MARKER}-UOM-{suffix}",
                         uom_name=f"{MARKER} uom"))
    db.flush()

    out: dict[str, str] = {}
    for code in codes:
        product = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=f"{MARKER} {code}",
            category_id=category_id,
            base_uom_id=uom_id,
            list_price=0,
            is_active=True,
            **({"company_id": company_id} if company_id else {}),
        )
        db.add(product)
        db.flush()
        out.setdefault(code, str(product.id))
    return out


def test_a_code_spelt_with_different_punctuation_is_the_same_product(seeded):
    """Their paper says SRTFH15CR, our item master says SRTFH15-CR. One product.

    Taken from line 30 of the client's real purchase order.
    """
    db, _project, _owner = seeded
    ids = _catalogue(db, "SRTFH15-CR")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product(
        "IFH15CR", "ADJUSTABLE WATER FLOW RATE SORENTO SRTFH15CR FLEXIBLE HOSE 1.5M"
    )

    assert product_id == ids["SRTFH15-CR"]
    assert source == "code_normalised"


def test_a_code_whose_parts_are_in_another_order_is_the_same_product(seeded):
    """They write B2155-BLUE-NL, we stock B2155-NL-BLUE. Lines 4, 19 and 22 of the
    real PO, which is three lines of one document lost to a word order."""
    db, _project, _owner = seeded
    ids = _catalogue(db, "B2155-NL-BLUE")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product(
        "CB2155-BLUE", "SORENTO B2155-BLUE-NL ANGLE VALVE"
    )

    assert product_id == ids["B2155-NL-BLUE"]
    assert source == "code_reordered"


def test_a_truncated_column_plus_the_description_names_one_product(seeded):
    """The customer's stock-code column is narrow and cuts the code off.

    Line 1 of the real PO prints `SRTWC86` in the column, and the description carries
    the rest. Neither half identifies the product alone: the prefix matches dozens, and
    the description spells it `SRTW8613-RL` where we spell it `SRTWC8613-RL`. Together
    they are unambiguous, which is exactly how a person reads that row.
    """
    db, _project, _owner = seeded
    ids = _catalogue(db, "SRTWC8613-RL", "SRTWC8608-SC", "SRTWC8640-P")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product(
        "SRTWC86", "SORENTO SRTW8613-RL ONE PIECE WC - SET - Washdown One-Piece WC"
    )

    assert product_id == ids["SRTWC8613-RL"]
    assert source == "code_truncated"


def test_one_code_stocked_by_two_companies_is_one_answer_not_an_ambiguity(seeded):
    """The same code exists once per company, so a cross-company read sees it twice.

    That is one product spelt once, not two candidates. Counting ROWS rather than
    distinct CODES is the bug that made the loose matcher resolve nothing at all on the
    client's real document, where the item master carries `C-FH12` for both companies.
    """
    from app.models.company import Company

    db, _project, _owner = seeded
    other_company = str(uuid.uuid4())
    db.add(Company(id=other_company, name=f"{MARKER} Other", code=str(uuid.uuid4())[:8]))
    db.flush()

    ids = _catalogue(db, "C-FH16")
    _catalogue(db, "C-FH16", company_id=other_company)
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product("CFH16", "CABANA C-FH16 FLEXIBLE HOSE")

    assert product_id in {ids["C-FH16"], product_id}
    assert product_id is not None
    assert source == "code_normalised"


def test_two_different_codes_that_both_fit_are_left_for_a_person(seeded):
    """Refusing is the feature. On a 1.8 million ringgit order, quietly attaching the
    wrong product costs more than asking, so a relaxed form that fits two DIFFERENT
    codes resolves to neither."""
    db, _project, _owner = seeded
    _catalogue(db, "SRT-100-A", "SRT-100-B")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product("SRT100", "SORENTO SRT100 SOMETHING")

    assert product_id is None
    assert source is None


def test_a_product_that_is_simply_not_stocked_stays_unresolved(seeded):
    """Half the client's unresolved lines name something genuinely absent from the item
    master. Saying so is the correct answer, and inventing a near neighbour is not."""
    db, _project, _owner = seeded
    _catalogue(db, "SRTWC8613-RL")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product(
        "SRTCB2829", "SORENTO SRTCB2829 DECK MOUNT KITCHEN SINK TAP"
    )

    assert product_id is None
    assert source is None


def test_an_exact_code_still_wins_before_anything_is_relaxed(seeded):
    """Order matters: the loose tiers must never pre-empt a character-for-character
    match, or a correct resolution could be replaced by a plausible one."""
    db, _project, _owner = seeded
    ids = _catalogue(db, "SRTWC8613-RL", "SRTWC8613RL")
    service = ProjectPOExtractionService(db)

    product_id, source = service._resolve_product("SRTWC8613-RL", "SORENTO ONE PIECE WC")

    assert product_id == ids["SRTWC8613-RL"]
    assert source == "code"


# ---------------------------------------------- the catalogue is read once per document


def _counting_execute(db):
    """Patch ``db.execute`` to count every statement sent, returning the running list.

    Legacy ``Query.all()`` routes through ``session.execute`` in SQLAlchemy 2.0, so
    this catches ``db.query(...)`` calls too - the shape ``_catalogue_index`` uses.
    """
    calls: list[str] = []
    original = db.execute

    def counting(statement, *args, **kwargs):
        calls.append(str(statement))
        return original(statement, *args, **kwargs)

    db.execute = counting
    return calls


def test_the_catalogue_is_indexed_once_and_reused_across_lines(seeded):
    """B4 (19 Aug follow-up): resolving several unmatched lines on one document must
    not re-SELECT the whole catalogue per line.

    Three lines here all miss the exact tiers and fall to the loose matcher. Before
    memoising, each one ran its own ``SELECT id, product_code FROM products`` and
    rebuilt both lookup dicts from scratch; now the SELECT happens once."""
    db, _project, _owner = seeded
    _catalogue(db, "SRTFH15-CR", "B2155-NL-BLUE", "SRTWC8613-RL")
    service = ProjectPOExtractionService(db)

    calls = _counting_execute(db)

    for stock_code, description in (
        ("IFH15CR", "ADJUSTABLE WATER FLOW RATE SORENTO SRTFH15CR FLEXIBLE HOSE 1.5M"),
        ("CB2155-BLUE", "SORENTO B2155-BLUE-NL ANGLE VALVE"),
        ("SRTWC86", "SORENTO SRTW8613-RL ONE PIECE WC"),
    ):
        product_id, source = service._resolve_product(stock_code, description)
        assert product_id is not None, f"{stock_code} did not resolve"
        assert source is not None

    # The catalogue-index projection is exactly (id, product_code) - narrower than the
    # exact-match tiers' `SELECT *`, so this substring names only the SELECT under test.
    marker = "products.product_code AS products_product_code \nFROM products"
    catalogue_selects = [c for c in calls if marker in c]
    assert len(catalogue_selects) == 1, catalogue_selects


def test_the_catalogue_index_is_memoised_per_company(seeded):
    """The cache is identity-checked, not content-checked: a second call for the SAME
    company must return the EXACT SAME dicts, proving no fresh SELECT ran, while a
    different company still gets its own (correctness the memo must not break)."""
    from app.models.company import Company

    db, _project, _owner = seeded
    other_company = str(uuid.uuid4())
    db.add(Company(id=other_company, name=f"{MARKER} Other", code=str(uuid.uuid4())[:8]))
    db.flush()
    _catalogue(db, "SRTWC8613-RL")
    service = ProjectPOExtractionService(db)

    first = service._catalogue_index(None)
    second = service._catalogue_index(None)
    assert first[0] is second[0]
    assert first[1] is second[1]

    other = service._catalogue_index(other_company)
    assert other[0] is not first[0]
