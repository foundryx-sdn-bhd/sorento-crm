"""The quotation DOCUMENT: one letterhead carrying several priced scopes.

Sits above ``project_quotation_service``, which keeps owning a scope and its version chain. The
split matters: outcome is per scope and is not a property of a revision, so this module never
touches outcome, and the project's derived outcome keeps working untouched.

Three rules are enforced here rather than left to callers, because each has already been got
wrong once somewhere in this codebase:

1. **Rate-only lines contribute zero to every total.** Scope total, grand total and the issue's
   snapshotted total all come from ONE function. Three call sites computing the same sum is how
   a quotation ends up disagreeing with its own PDF.
2. **The recipient block is snapshotted at create.** Read live, a party address edited next year
   silently rewrites what was sent last year.
3. **Issuing freezes.** An issue records the exact version each scope contributed, and
   ``project_quotation_service.is_frozen`` treats "an issue points at this version" as freezing
   it, so the rows an issue claims to hold cannot be rewritten under it.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.base import company_scope, set_company_scope
from app.models.numbering import DocumentNumberingRule
from app.models.projects import (
    QUOTATION_OUTCOME_LOST,
    QUOTATION_OUTCOME_WON,
    Project,
    ProjectParty,
    ProjectQuotation,
    ProjectQuotationDocument,
    ProjectQuotationIssue,
    ProjectQuotationIssueScope,
    ProjectQuotationLine,
    ProjectQuotationVersion,
    QuotationSignature,
)
from app.services import project_quotation_approval_service as approvals
from app.services import project_quotation_image_service as quotation_images
from app.services import project_quotation_service as scope_service
from app.services.error_handler import AppException
from app.services.numbering_service import NumberingService

logger = logging.getLogger(__name__)

QUOTATION_DOC_TYPE = "project_quotation"

ZERO = Decimal("0.00")


# ------------------------------------------------------------------ money


def line_amount(line: ProjectQuotationLine) -> Decimal:
    """What a line adds to its scope. A rate-only line adds NOTHING.

    The sample quotation carries five rate-only alternates; adding them would have overstated it
    by more than RM 235,000. `line_total` is still stored and still printed, because the customer
    is being shown a rate - it just does not count.
    """
    if line.is_rate_only:
        return ZERO
    return Decimal(line.line_total or 0)


def scope_total(db: Session, quotation: ProjectQuotation) -> Decimal:
    """The current version's priced total for one scope."""
    version = (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.quotation_id == quotation.id)
        .order_by(ProjectQuotationVersion.version_no.desc())
        .first()
    )
    if version is None:
        return ZERO
    return version_total(db, version)


def version_total(db: Session, version: ProjectQuotationVersion) -> Decimal:
    lines = (
        db.query(ProjectQuotationLine)
        .filter(ProjectQuotationLine.version_id == version.id)
        .all()
    )
    return sum((line_amount(line) for line in lines), ZERO)


def list_scopes(db: Session, document: ProjectQuotationDocument) -> List[ProjectQuotation]:
    return (
        db.query(ProjectQuotation)
        .filter(ProjectQuotation.document_id == document.id)
        .order_by(ProjectQuotation.sort_order, ProjectQuotation.created_at)
        .all()
    )


def document_total(db: Session, document: ProjectQuotationDocument) -> Decimal:
    """The sample workbook's TOTAL AMOUNT: every scope's current version, added up."""
    return sum((scope_total(db, scope) for scope in list_scopes(db, document)), ZERO)


def _item_number(line: ProjectQuotationLine, position: int) -> str:
    """The ITEM cell, defined once for every surface that prints a quotation."""
    from app.services.project_quotation_pdf_service import item_number

    return item_number(line, position)


# ------------------------------------------------------------------ create


def _next_document_no(db: Session, company_id: Optional[str]) -> str:
    """From the running-number feature, so an admin can change the format without a deploy.

    Scoped to the company: a customer-facing series that two companies share is not a series.
    Falls back to a derived number only when no rule exists at all, because refusing to create a
    quotation because nobody configured a prefix would be a worse failure than an ugly reference.
    """
    number = NumberingService(db).get_next_number(
        QUOTATION_DOC_TYPE, company_id=company_id, commit_rule=False
    )
    if number:
        return number

    highest = (
        db.query(func.count(ProjectQuotationDocument.id))
        .filter(ProjectQuotationDocument.company_id == company_id)
        .scalar()
        or 0
    )
    return f"Q-{date.today().year}-{highest + 1:04d}"


def _recipient_from_project(db: Session, project: Project) -> Dict[str, Any]:
    """Everything knowable about who this goes to, taken once and kept.

    The journey's rule: a field derivable from something we already hold is derived, never asked.
    """
    party = None
    if project.developer_party_id:
        party = (
            db.query(ProjectParty).filter(ProjectParty.id == project.developer_party_id).first()
        )
    if party is None:
        return {
            "recipient_party_id": None,
            "recipient_name_snapshot": None,
            "recipient_address_snapshot": None,
            "recipient_phone_snapshot": None,
        }
    return {
        "recipient_party_id": party.id,
        "recipient_name_snapshot": party.name,
        "recipient_address_snapshot": party.address,
        "recipient_phone_snapshot": party.phone,
    }


def create_document(
    db: Session,
    *,
    project: Project,
    actor_user_id: str,
    payload: Optional[Dict[str, Any]] = None,
    render_templates: bool = True,
) -> ProjectQuotationDocument:
    """``render_templates=False`` leaves the letter to the caller, which renders it once the
    quotation's lines exist (the form's create, #1341), so ``{{grand_total}}`` is the real one."""
    payload = payload or {}
    document = ProjectQuotationDocument(
        company_id=project.company_id,
        project_id=project.id,
        document_no=payload.get("document_no") or _next_document_no(db, project.company_id),
        your_ref=payload.get("your_ref"),
        doc_date=payload.get("doc_date") or date.today(),
        attn_name=payload.get("attn_name"),
        subject_title=payload.get("subject_title") or project.title,
        cover_letter_html=payload.get("cover_letter_html"),
        terms_html=payload.get("terms_html"),
        signatory_name=payload.get("signatory_name"),
        signatory_phone=payload.get("signatory_phone"),
        created_by=actor_user_id,
        **_recipient_from_project(db, project),
    )
    db.add(document)
    db.flush()
    if render_templates:
        _render_templates_onto(db, document, payload)
    return document


def _render_templates_onto(
    db: Session, document: ProjectQuotationDocument, payload: Dict[str, Any]
) -> None:
    """Journey step 5: the letter is already written when the salesperson gets there (AC-E2).

    Rendered ONCE, into the document's own columns, AFTER the flush - the merge fields quote the
    document's own reference, date and recipient snapshot, so the row has to exist first.

    Never re-read at print time: an admin rewriting the company letter must not rewrite a
    quotation drafted last month, and must never touch one already issued (AC-B4 / AC-E3). A
    caller that supplied its own text has already decided, and rendering over it would discard
    what a person typed.
    """
    from app.services import project_quotation_template_service as templates

    for field, kind in (
        ("cover_letter_html", templates.TEMPLATE_KIND_COVER_LETTER),
        ("terms_html", templates.TEMPLATE_KIND_TERMS),
    ):
        if (payload.get(field) or "").strip():
            continue
        rendered = templates.render_for_document(db, document=document, kind=kind)
        if rendered is not None:
            setattr(document, field, rendered)
    db.flush()


def get_document(db: Session, document_id: str) -> Optional[ProjectQuotationDocument]:
    """By id alone, for callers that already know which document they hold."""
    return (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == document_id)
        .first()
    )


def get_document_or_404(
    db: Session, project_id: str, document_id: str
) -> ProjectQuotationDocument:
    document = (
        db.query(ProjectQuotationDocument)
        .filter(
            ProjectQuotationDocument.id == document_id,
            ProjectQuotationDocument.project_id == project_id,
        )
        .first()
    )
    if not document:
        raise AppException(
            status_code=404,
            message="Quotation not found.",
            code="quotation_document_not_found",
        )
    return document


def add_scope(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    scope_label: str,
    actor_user_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> ProjectQuotation:
    """A tab on the document, with its version 1 opened immediately.

    Delegates to the existing scope service so there is one definition of "a scope and its first
    version", then binds it to this document and puts it last in tab order.
    """
    approvals.assert_unlocked(db, document)
    project = db.query(Project).filter(Project.id == document.project_id).first()
    if project is None:
        raise AppException(
            status_code=404, message="Project not found.", code="project_not_found"
        )

    body = dict(payload or {})
    body["scope_label"] = scope_label

    # Tab order is decided BEFORE the insert, because `document_id` is NOT NULL and the row
    # cannot be written unattached and fixed up afterwards.
    highest = (
        db.query(func.max(ProjectQuotation.sort_order))
        .filter(ProjectQuotation.document_id == document.id)
        .scalar()
    )
    quotation = scope_service.create_quotation(
        db,
        project=project,
        actor_user_id=actor_user_id,
        payload=body,
        document_id=document.id,
    )
    quotation.sort_order = 0 if highest is None else int(highest) + 1
    db.flush()
    return quotation


# ------------------------------------------------------------------ the form's save (#1341)

_RECIPIENT_FIELDS = (
    "recipient_name_snapshot",
    "recipient_address_snapshot",
    "recipient_phone_snapshot",
)
_SCOPE_FIELDS = ("scope_label", "series_id", "notes")


def create_document_with_scopes(
    db: Session,
    *,
    project: Project,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> Tuple[ProjectQuotationDocument, List[ProjectQuotationLine]]:
    """The quotation form's Save in create mode: letterhead, scopes and lines in one go.

    The owner's words: "I should be able to add product straight away and save when I am
    satisfied". So nothing exists until this runs, and it writes everything the page holds in the
    caller's ONE transaction: a line the server refuses raises before the route commits, and the
    rollback takes the document and every scope with it. No half quotation is ever left behind.

    Everything is written through the paths the rest of the module already uses - ``add_scope``
    for a scope and its version 1, ``replace_lines`` for the lines - so the snapshotting, the
    guardrails and the rate-only totals are the same rules, not a copy of them. A scope sent with
    no lines is saved with none: there is no placeholder line anywhere on this path.

    Returns the lines written so the route can raise below-floor notifications for them, exactly
    as the bulk line route does.
    """
    body = dict(payload)
    scopes = body.pop("scopes", None) or []
    corrections = {field: body.pop(field, None) for field in _RECIPIENT_FIELDS}
    # The letter typed in the create form's own tabs (#1341, "show them on create as well"). Held
    # back from ``create_document`` so the company template renders there as it always did, and
    # the typed text replaces it once the scopes exist: its merge fields then quote the real
    # number and total, not the ones a half-made quotation would give.
    letter = {
        field: body.pop(field)
        for field in ("cover_letter_html", "terms_html")
        if (body.get(field) or "").strip()
    }

    document = create_document(
        db, project=project, actor_user_id=actor_user_id, payload=body, render_templates=False
    )
    # A typed recipient CORRECTS the party snapshot (the result create-then-edit gave before); a
    # blank one keeps what the developer party says rather than blanking the letterhead.
    for field, value in corrections.items():
        if value is not None and str(value).strip():
            setattr(document, field, value)
    db.flush()

    written: List[ProjectQuotationLine] = []
    for item in scopes:
        written.extend(
            _add_form_scope(db, document=document, actor_user_id=actor_user_id, item=item)
        )
    # The letter, once the lines exist: typed text has its merge fields filled, and a blank tab
    # takes the company template, both against the real total rather than 0.00.
    db.flush()
    if letter:
        from app.services import project_quotation_template_service as templates

        context = templates.build_document_context(db, document=document)
        for field, text in letter.items():
            setattr(document, field, templates.render(text, context))
    _render_templates_onto(db, document, letter)
    return document, written


def letter_templates(db: Session, *, project: Project) -> Dict[str, Optional[str]]:
    """The company's active cover letter and terms, as written, for the create form (#1341).

    Unrendered on purpose: before Save there is no quotation number and no total, so a merge
    field rendered now would bake in a blank. The tokens are filled when the quotation is saved.
    """
    from app.services import project_quotation_template_service as templates

    out: Dict[str, Optional[str]] = {}
    for field, kind in (
        ("cover_letter_html", templates.TEMPLATE_KIND_COVER_LETTER),
        ("terms_html", templates.TEMPLATE_KIND_TERMS),
    ):
        template = templates.active_template(db, company_id=project.company_id, kind=kind)
        out[field] = template.body_html if template is not None else None
    return out


def remove_form_scopes(
    db: Session, *, document: ProjectQuotationDocument, scope_ids: List[str]
) -> None:
    """Edit quotation deletes saved scopes (#1341, owner on Q2: "yes can").

    Only while nothing in the scope has been issued: ANY version of it in any issue, not only the
    open one, because the customer holds that paper and a revision opened since does not change
    that. Refused before anything is deleted, so one issued scope in the list stops them all.
    """
    approvals.assert_unlocked(db, document)
    scopes = [get_scope_or_404(db, document, str(scope_id)) for scope_id in scope_ids]
    for scope in scopes:
        issued = (
            db.query(ProjectQuotationIssueScope.id)
            .filter(ProjectQuotationIssueScope.quotation_id == scope.id)
            .first()
        )
        if issued is not None:
            raise AppException(
                status_code=422,
                message=(
                    f"{scope.scope_label} has been sent to the customer and cannot be removed "
                    "from this quotation."
                ),
                code="quotation_scope_issued",
            )
    for scope in scopes:
        scope_service.delete_quotation(db, scope)


def apply_form_scopes(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    scopes: List[Dict[str, Any]],
) -> List[ProjectQuotationLine]:
    """The quotation form's Save in edit mode, for the scopes (#1341).

    Each item with an ``id`` is a scope already on THIS document (another document's scope 404s):
    its name, series and notes are applied when present, and ``lines`` when present is the FULL
    line set of its current version. That goes through ``replace_lines``, so a version the
    customer holds or that was superseded refuses with the existing 422 before anything moves -
    the status rules are the bulk line route's, unchanged. An item without an ``id`` is a new
    scope. A saved scope the payload leaves out is not touched.
    """
    approvals.assert_unlocked(db, document)
    written: List[ProjectQuotationLine] = []
    for item in scopes:
        scope_id = item.get("id")
        if not scope_id:
            written.extend(
                _add_form_scope(db, document=document, actor_user_id=actor_user_id, item=item)
            )
            continue
        scope = get_scope_or_404(db, document, str(scope_id))
        fields = {key: item[key] for key in _SCOPE_FIELDS if key in item}
        if fields:
            scope_service.update_quotation(db, quotation=scope, payload=fields)
        if item.get("lines") is not None:
            written.extend(
                scope_service.replace_lines(
                    db,
                    version=scope_service.current_version(db, scope.id),
                    actor_user_id=actor_user_id,
                    lines=item["lines"],
                )
            )
    return written


def _add_form_scope(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    item: Dict[str, Any],
) -> List[ProjectQuotationLine]:
    lines = item.get("lines") or []
    scope = add_scope(
        db,
        document=document,
        scope_label=item.get("scope_label") or "",
        actor_user_id=actor_user_id,
        payload={key: item[key] for key in ("series_id", "notes") if key in item},
    )
    if not lines:
        return []
    return scope_service.replace_lines(
        db,
        version=scope_service.current_version(db, scope.id),
        actor_user_id=actor_user_id,
        lines=lines,
    )


# ------------------------------------------------------------------ issue


def list_issues(db: Session, document: ProjectQuotationDocument) -> List[ProjectQuotationIssue]:
    """Newest first. Current is MAX(issue_no); everything below it is what was sent before."""
    return (
        db.query(ProjectQuotationIssue)
        .filter(ProjectQuotationIssue.document_id == document.id)
        .order_by(ProjectQuotationIssue.issue_no.desc())
        .all()
    )


def current_issue(
    db: Session, document: ProjectQuotationDocument
) -> Optional[ProjectQuotationIssue]:
    return (
        db.query(ProjectQuotationIssue)
        .filter(ProjectQuotationIssue.document_id == document.id)
        .order_by(ProjectQuotationIssue.issue_no.desc())
        .first()
    )


def issue(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
) -> ProjectQuotationIssue:
    """Stamp R{n} and record exactly what went out.

    Two things are frozen, for the same reason: the rendered letter and terms (the template will be
    rewritten) and the ``(scope, version)`` pairs (the versions would otherwise keep taking edits).

    A revision does NOT force every scope to move: an untouched scope contributes the same version
    it contributed last time, which is why the pairs are recorded rather than inferred from version
    numbers that advanced at different moments.
    """
    scopes = list_scopes(db, document)
    if not scopes:
        raise AppException(
            status_code=422,
            message="Add at least one scope before issuing this quotation.",
            code="quotation_document_no_scopes",
        )
    # AC-H1. No signature, no issue: an unsigned quotation in a customer's inbox is a thing
    # nobody can explain later, so the refusal happens here rather than being left to a reviewer.
    if not document.signatory_signature_id:
        raise AppException(
            status_code=422,
            message="Sign this quotation before issuing it.",
            code="quotation_document_unsigned",
        )
    # S15. Below-floor pricing needs a manager before it reaches the customer. Deliberately at
    # ISSUE and not at Sign: the internal signature is readiness, not dispatch. A quotation with
    # nothing below its floor - the overwhelming majority - is not touched by this at all.
    approvals.assert_issuable(db, document)

    highest = (
        db.query(func.max(ProjectQuotationIssue.issue_no))
        .filter(ProjectQuotationIssue.document_id == document.id)
        .scalar()
    )
    issue_no = 1 if highest is None else int(highest) + 1

    record = ProjectQuotationIssue(
        company_id=document.company_id,
        document_id=document.id,
        issue_no=issue_no,
        our_ref_text=f"{document.document_no} (R{issue_no})",
        issued_at=datetime.utcnow(),
        issued_by=actor_user_id,
        cover_letter_rendered=document.cover_letter_html,
        terms_rendered=document.terms_html,
        grand_total=ZERO,
        # Copied, not referenced: re-signing the draft later must not change the signature this
        # revision went out with.
        sorento_signature_id=document.signatory_signature_id,
    )
    db.add(record)
    db.flush()

    grand = ZERO
    for scope in scopes:
        version = scope_service.current_version(db, scope.id)
        # S21. The photograph each line goes out with, written down at the moment it goes out.
        # A draft resolves its picture live off `product_attachments.is_primary`, because almost
        # every product is priced before anybody has chosen one; issuing is what turns today's
        # answer into the record of what was sent. A FILL only, so a scope carried unchanged into
        # R2 keeps exactly the pictures R1 showed.
        quotation_images.freeze_version_images(db, version.id)
        total = version_total(db, version)
        grand += total
        db.add(
            ProjectQuotationIssueScope(
                company_id=document.company_id,
                issue_id=record.id,
                quotation_id=scope.id,
                version_id=version.id,
                sort_order=scope.sort_order,
                scope_total=total,
            )
        )
    record.grand_total = grand
    db.flush()
    # Issuing SPENDS an approval: a manager approved those prices, so the document moves off
    # `approved` and the next revision that dips below the floor has to be approved on its own
    # merits. A no-op on a quotation that was never on the graph.
    approvals.mark_issued(db, document)
    return record


# ------------------------------------------------------------------ delete


def assert_deletable(db: Session, document: ProjectQuotationDocument) -> None:
    if current_issue(db, document) is not None:
        raise AppException(
            status_code=422,
            message=(
                "This quotation has been issued to the customer and cannot be deleted. "
                "Withdraw it instead."
            ),
            code="quotation_document_issued",
        )


def delete_document(db: Session, document: ProjectQuotationDocument) -> None:
    """Hard delete, and refused outright once anything has been issued.

    An issued quotation is a thing the customer holds. Deleting the record of it would leave a
    reference in their inbox that this system cannot explain, so the exit is an explicit
    withdrawal on the status graph, never a delete.
    """
    assert_deletable(db, document)
    db.delete(document)
    db.flush()


# ------------------------------------------------------------------ serialize


def _scope_summary(db: Session, scope: ProjectQuotation) -> Dict[str, Any]:
    version = (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.quotation_id == scope.id)
        .order_by(ProjectQuotationVersion.version_no.desc())
        .first()
    )
    line_count = 0
    if version is not None:
        line_count = (
            db.query(func.count(ProjectQuotationLine.id))
            .filter(ProjectQuotationLine.version_id == version.id)
            .scalar()
            or 0
        )
    return {
        "id": str(scope.id),
        "scope_label": scope.scope_label,
        "sort_order": int(scope.sort_order or 0),
        "outcome": scope.outcome,
        "current_version_id": str(version.id) if version is not None else None,
        "current_version_no": version.version_no if version is not None else None,
        "line_count": int(line_count),
        "scope_total": version_total(db, version) if version is not None else ZERO,
    }


def _customer_decision(latest: Optional[ProjectQuotationIssue]) -> Optional[str]:
    """What the customer did with the revision they hold: accepted, asked, or nothing yet.

    Acceptance is checked FIRST and that ordering is the rule, not a coincidence: a customer who
    asked for changes and then signed has accepted, the request stays on the row as history, and
    a screen reporting "changes requested" on a quotation that is already won would be wrong on
    every one of them.
    """
    if latest is None:
        return None
    if latest.accepted_at is not None:
        return "accepted"
    if latest.changes_requested_at is not None:
        return "changes_requested"
    return None


def serialize_document(db: Session, document: ProjectQuotationDocument) -> Dict[str, Any]:
    """One document, with its tabs and its money.

    `grand_total` is summed HERE rather than by the caller, so the header, the tabs and the PDF
    cannot disagree about what a rate-only line contributes (nothing).
    """
    scopes = list_scopes(db, document)
    summaries = [_scope_summary(db, scope) for scope in scopes]
    latest = current_issue(db, document)
    # AC-H1 gates issuing on this, so the client has to be able to read it after a refresh and
    # not only in the response to its own sign call. "It is issued, so it must be signed" is not
    # a safe substitute: it is wrong on documents issued before the gate existed.
    signature = (
        db.query(QuotationSignature)
        .filter(QuotationSignature.id == document.signatory_signature_id)
        .first()
        if document.signatory_signature_id
        else None
    )
    issue_count = (
        db.query(func.count(ProjectQuotationIssue.id))
        .filter(ProjectQuotationIssue.document_id == document.id)
        .scalar()
        or 0
    )
    return {
        "id": str(document.id),
        "project_id": str(document.project_id),
        "document_no": document.document_no,
        "our_ref": (
            f"{document.document_no} (R{latest.issue_no})"
            if latest is not None
            else document.document_no
        ),
        "your_ref": document.your_ref,
        "doc_date": document.doc_date,
        "recipient_party_id": (
            str(document.recipient_party_id) if document.recipient_party_id else None
        ),
        "recipient_name_snapshot": document.recipient_name_snapshot,
        "recipient_address_snapshot": document.recipient_address_snapshot,
        "recipient_phone_snapshot": document.recipient_phone_snapshot,
        "attn_name": document.attn_name,
        "subject_title": document.subject_title,
        "cover_letter_html": document.cover_letter_html,
        "terms_html": document.terms_html,
        "signatory_name": document.signatory_name,
        "signatory_phone": document.signatory_phone,
        "signatory_signature": _serialize_signature(signature),
        "is_signed": signature is not None,
        "scopes": summaries,
        "grand_total": sum((row["scope_total"] for row in summaries), ZERO),
        "issue_count": int(issue_count),
        "current_issue_no": latest.issue_no if latest is not None else None,
        "is_issued": latest is not None,
        # Where the CUSTOMER left the revision they are currently holding, so the salesperson's
        # own screens can say it without reading the issue history. The client went looking for a
        # change request and could not find it ("when i request changes, how can i see it from
        # the system?"), and the answer cannot be a tab: the banner on the document and the badge
        # on the project's quotation list both need this, and neither fetches issues.
        #
        # Read off the LATEST issue alone. Read across the whole history it would keep telling
        # somebody to revise a quotation they have already revised and re-issued.
        "accepted_at": latest.accepted_at if latest is not None else None,
        "changes_requested_at": (
            latest.changes_requested_at if latest is not None else None
        ),
        "changes_requested_note": (
            latest.changes_requested_note if latest is not None else None
        ),
        "changes_requested_by_name": (
            latest.changes_requested_by_name if latest is not None else None
        ),
        # The one place the "acceptance wins" rule is decided. Both fields can be set - somebody
        # who asked for changes may still sign - and every surface has to reach the same answer,
        # so it is resolved once here rather than re-expressed on the counter-sign page, the
        # Signatures badge, the document banner and the list column.
        "customer_decision": _customer_decision(latest),
        # S15. The screen reads all six of these to decide whether to show the price-floor
        # block at all, so they are present on EVERY document rather than only on a gated one:
        # absent would be as bad as wrong. Spread from one function so this manual dict and the
        # approval service cannot drift - this repo has been bitten by a manual builder silently
        # dropping a new column more than once (`get_user`, `system_settings`).
        **approvals.serialize_approval(db, document),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def serialize_documents(
    db: Session, documents: List[ProjectQuotationDocument]
) -> List[Dict[str, Any]]:
    return [serialize_document(db, document) for document in documents]


def serialize_issue(db: Session, record: ProjectQuotationIssue) -> Dict[str, Any]:
    from app.models.user import User

    name = None
    if record.issued_by:
        user = db.query(User).filter(User.id == record.issued_by).first()
        name = user.name if user else None
    scope_count = (
        db.query(func.count(ProjectQuotationIssueScope.id))
        .filter(ProjectQuotationIssueScope.issue_id == record.id)
        .scalar()
        or 0
    )
    return {
        "id": str(record.id),
        "document_id": str(record.document_id),
        "issue_no": record.issue_no,
        "our_ref_text": record.our_ref_text,
        "issued_at": record.issued_at,
        "issued_by": record.issued_by,
        "issued_by_name": name,
        "grand_total": Decimal(record.grand_total or 0),
        "scope_count": int(scope_count),
        # The acceptance lands HERE, on the thing the customer actually held and signed, while the
        # screen watching for it is the document. Without these the document panel says "not
        # counter-signed yet" forever on a quotation that is already won.
        "customer_signature": _serialize_signature(
            db.query(QuotationSignature)
            .filter(QuotationSignature.id == record.customer_signature_id)
            .first()
            if record.customer_signature_id
            else None
        ),
        "accepted_at": record.accepted_at,
        "is_accepted": record.accepted_at is not None,
        # The other decision, on the same row and needed on the same screen: the salesperson
        # reads the feedback here and presses Revise. A notification alone would mean the words
        # only ever exist in an inbox.
        "changes_requested_at": record.changes_requested_at,
        "changes_requested_note": record.changes_requested_note,
        "changes_requested_by_name": record.changes_requested_by_name,
        "is_changes_requested": record.changes_requested_at is not None,
    }


def list_documents(db: Session, project_id: str) -> List[ProjectQuotationDocument]:
    return (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.project_id == project_id)
        .order_by(ProjectQuotationDocument.created_at.desc())
        .all()
    )


def update_document(
    db: Session, *, document: ProjectQuotationDocument, payload: Dict[str, Any]
) -> ProjectQuotationDocument:
    """Header edits only. `document_no` is deliberately not editable: the customer has it.

    The recipient snapshot IS editable, and it is still a snapshot. What a correction changes is
    this document's copy of who it is addressed to, never the developer party behind it, so a
    quotation can go to the customer's finance department without moving the master record - and
    the party changing later still cannot rewrite a quotation already in the customer's hands.

    Applied by allow-list and keyed on PRESENCE, because the route hands down an `exclude_unset`
    payload: a save correcting only Your Ref arrives with no recipient key at all, and reading
    absent as null would blank the letterhead as a side effect. Null is a separate, deliberate
    answer, and it clears.
    """
    approvals.assert_unlocked(db, document)
    for field in (
        "your_ref",
        "doc_date",
        "attn_name",
        "subject_title",
        "cover_letter_html",
        "terms_html",
        "signatory_name",
        "signatory_phone",
        "recipient_name_snapshot",
        "recipient_address_snapshot",
        "recipient_phone_snapshot",
    ):
        if field in payload:
            setattr(document, field, payload[field])
    db.flush()
    return document


def update_scope(
    db: Session, *, scope: ProjectQuotation, payload: Dict[str, Any]
) -> ProjectQuotation:
    scope_service.assert_scope_unlocked(db, scope)
    if "scope_label" in payload and payload["scope_label"] is not None:
        label = " ".join(str(payload["scope_label"]).split())
        if not label:
            raise AppException(
                status_code=422,
                message="A scope needs a name, e.g. Townhouse or Guard House.",
                code="quotation_scope_required",
            )
        scope.scope_label = label
    if payload.get("sort_order") is not None:
        scope.sort_order = int(payload["sort_order"])
    if "notes" in payload:
        scope.notes = payload["notes"]
    db.flush()
    return scope


def get_scope_or_404(
    db: Session, document: ProjectQuotationDocument, quotation_id: str
) -> ProjectQuotation:
    scope = (
        db.query(ProjectQuotation)
        .filter(
            ProjectQuotation.id == quotation_id,
            ProjectQuotation.document_id == document.id,
        )
        .first()
    )
    if not scope:
        raise AppException(
            status_code=404, message="Scope not found.", code="quotation_scope_not_found"
        )
    return scope


# ------------------------------------------------------------------ signing

SIGN_TOKEN_TTL_DAYS = 30


def record_signature(
    db: Session,
    *,
    company_id: Optional[str],
    owner_kind: str,
    signer_name: Optional[str],
    mode: str,
    image_data_uri: Optional[str],
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    gps_lat: Optional[str] = None,
    gps_lng: Optional[str] = None,
) -> QuotationSignature:
    """Store one signature exactly as captured.

    Always a NEW row, never a reference to a reusable one: a signature applied to a document has to
    stay what it was on the day, so re-drawing your signature next year cannot rewrite what you
    already signed. Same snapshot rule as the lines and the rendered letter.
    """
    if mode not in ("draw", "type", "initials"):
        raise AppException(
            status_code=422,
            message=f"Unknown signature mode '{mode}'.",
            code="signature_mode_invalid",
        )
    if not image_data_uri:
        raise AppException(
            status_code=422,
            message="A signature needs to be drawn, typed or initialled before it can be saved.",
            code="signature_empty",
        )

    signature = QuotationSignature(
        company_id=company_id,
        owner_kind=owner_kind,
        user_id=user_id,
        signer_name=(signer_name or "").strip() or None,
        mode=mode,
        image_data_uri=image_data_uri,
        ip_address=ip_address,
        user_agent=user_agent,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        signed_at=datetime.utcnow(),
    )
    db.add(signature)
    db.flush()
    return signature


def sign_as_sorento(
    db: Session,
    *,
    document: ProjectQuotationDocument,
    actor_user_id: str,
    payload: Dict[str, Any],
) -> QuotationSignature:
    """The internal signature, held on the DOCUMENT until an issue carries it.

    Kept on the document rather than demanded at issue time so the signing and the issuing are
    separate acts: a person signs once and can then issue, which is the order the journey has.
    """
    signature = record_signature(
        db,
        company_id=document.company_id,
        owner_kind="user",
        signer_name=payload.get("signer_name") or document.signatory_name,
        mode=payload.get("mode") or "draw",
        image_data_uri=payload.get("image_data_uri"),
        user_id=actor_user_id,
        ip_address=payload.get("ip_address"),
        user_agent=payload.get("user_agent"),
        gps_lat=payload.get("gps_lat"),
        gps_lng=payload.get("gps_lng"),
    )
    document.signatory_signature_id = signature.id
    db.flush()
    return signature


def issue_sign_link(
    db: Session, *, record: ProjectQuotationIssue, ttl_days: int = SIGN_TOKEN_TTL_DAYS
) -> str:
    """Mint (or reuse) the tokenised counter-sign link for an issue.

    Reused while it is still valid so re-sending the same quotation does not invalidate the link
    the customer already has sitting in their inbox. Rotated once it has expired.
    """
    now = datetime.utcnow()
    if (
        record.sign_token
        and record.sign_token_expires_at
        and record.sign_token_expires_at > now
    ):
        return record.sign_token
    record.sign_token = secrets.token_urlsafe(32)
    record.sign_token_expires_at = now + timedelta(days=ttl_days)
    db.flush()
    return record.sign_token


def get_issue_by_sign_token(db: Session, token: str) -> ProjectQuotationIssue:
    """Resolve a counter-sign link, refusing anything expired or unknown with the SAME message.

    Deliberately one message for both: telling a caller that a token exists but has expired
    confirms the token, and this endpoint is public.

    Resolved with the company scope OPEN, then pinned shut to the issue's own company. The signer
    is a stranger with no session and no API key, so the scope resolver leaves this request at
    UNSET, which is fail-closed and reads zero rows from every owned table: without the open
    window a live link answers "no longer valid" and the customer is told to ask for a resend of
    something that was never broken. The token is what makes opening it safe, being globally
    unique and the whole credential. Pinning afterwards is not optional: everything the handler
    reads next (scopes, lines, signatures) must stay inside the company the token belongs to, or
    one leaked link becomes a reader for every company's data.
    """
    with company_scope(db, None):
        record = (
            db.query(ProjectQuotationIssue)
            .filter(ProjectQuotationIssue.sign_token == token)
            .first()
        )
    if record is not None and record.company_id:
        set_company_scope(db, frozenset({str(record.company_id)}))
    if (
        record is None
        or record.sign_token_expires_at is None
        or record.sign_token_expires_at <= datetime.utcnow()
    ):
        raise AppException(
            status_code=404,
            message="This link is no longer valid. Ask your contact at Sorento to resend it.",
            code="quotation_sign_link_invalid",
        )
    return record


def accept_issue(
    db: Session,
    *,
    record: ProjectQuotationIssue,
    signer_name: Optional[str],
    mode: str,
    image_data_uri: Optional[str],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    gps_lat: Optional[str] = None,
    gps_lng: Optional[str] = None,
) -> ProjectQuotationIssue:
    """The customer counter-signs, and that WINS the quotation.

    Client decision (2026-08-04), overruling the evidence-only reading the UAC first proposed: a
    counter-signature is the commitment, so every scope the issue carried is marked won and the
    project's outcome derives to won through the rule that already exists.

    A scope already marked LOST is left alone. Somebody decided that deliberately, and a signature
    on a document that still lists the scope must not silently overrule a person. It stays lost,
    and the acceptance is still recorded.

    Idempotent: signing twice keeps the FIRST signature and the first accepted_at. A customer who
    double-taps has not signed two different things, and overwriting would lose the timestamp that
    matters.
    """
    if record.accepted_at is not None:
        return record

    signature = record_signature(
        db,
        company_id=record.company_id,
        owner_kind="customer",
        signer_name=signer_name,
        mode=mode,
        image_data_uri=image_data_uri,
        ip_address=ip_address,
        user_agent=user_agent,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
    )
    record.customer_signature_id = signature.id
    record.accepted_at = datetime.utcnow()
    db.flush()

    _win_the_scopes_on(db, record)
    return record


def _win_the_scopes_on(db: Session, record: ProjectQuotationIssue) -> List[ProjectQuotation]:
    """Mark every scope this issue carried as won, skipping any already lost.

    Goes through `scope_service.set_outcome` rather than setting the column, so the project's
    derived outcome is recomputed by the one function that owns that rule.
    """
    scopes = (
        db.query(ProjectQuotation)
        .join(
            ProjectQuotationIssueScope,
            ProjectQuotationIssueScope.quotation_id == ProjectQuotation.id,
        )
        .filter(ProjectQuotationIssueScope.issue_id == record.id)
        .all()
    )
    won: List[ProjectQuotation] = []
    for scope in scopes:
        if scope.outcome == QUOTATION_OUTCOME_LOST:
            continue
        if scope.outcome == QUOTATION_OUTCOME_WON:
            won.append(scope)
            continue
        scope_service.set_outcome(db, quotation=scope, outcome=QUOTATION_OUTCOME_WON)
        won.append(scope)
    db.flush()
    return won


# --------------------------------------------------------- the other answer


CHANGES_REQUESTED_TEMPLATE = "quotation_changes_requested"


def request_changes(
    db: Session,
    *,
    record: ProjectQuotationIssue,
    note: Optional[str],
    requester_name: Optional[str] = None,
) -> ProjectQuotationIssue:
    """The customer will not sign this as it stands, and says why.

    Client decision (2026-08-05): the feedback is CAPTURED and the salesperson revises by hand.
    Nothing here opens a revision, re-prices a line or moves an outcome - a customer asking for a
    lower price has not been given one, and a system that auto-revised would put words in the
    salesperson's mouth. What this owes them is the message, the moment, and a notification.

    Three refusals and one silence, each mirroring the care `accept_issue` already takes:

    * **An empty request is refused** (422). A stamped time with no words behind it renders on the
      customer's page as a settled outcome that nobody can act on.
    * **An accepted quotation is refused** (409). The counter-signature won every scope on the
      issue and moved the project's outcome; a request for changes sitting beside it would be a
      record that says both. The page stops offering the form once accepted, so this is only
      reachable from a stale tab, and it is answered rather than silently dropped.
    * **The same words twice do nothing** - no new stamp, no second activity row, no second
      notification. A double-tap is one request, exactly as a double-tap on Accept is one
      signature. Genuinely NEW words DO get through, which is the property
      ``floor_breach_dedup_key`` exists for: deduplicating the repeat must not silence somebody
      who came back with something else to say.

    The read is taken ``FOR UPDATE`` so two submissions from the same thumb serialise instead of
    racing: without the lock both see a null note and both notify.

    The reverse order is deliberately ALLOWED. Somebody who asked for changes can still decide to
    sign, and refusing a signature the customer wants to give would be worse than the mixed
    record; the request stays on the row as the history it is, and acceptance is what the page
    then reports.
    """
    text_note = (note or "").strip()
    if not text_note:
        raise AppException(
            status_code=422,
            message="Tell us what to change before sending this back.",
            code="quotation_changes_note_required",
        )

    locked = (
        db.query(ProjectQuotationIssue)
        .filter(ProjectQuotationIssue.id == record.id)
        .with_for_update()
        .first()
    ) or record

    if locked.accepted_at is not None:
        raise AppException(
            status_code=409,
            message=(
                "This quotation has already been accepted. "
                "Contact Sorento if something needs to change."
            ),
            code="quotation_already_accepted",
        )

    if locked.changes_requested_at is not None and locked.changes_requested_note == text_note:
        return locked

    name = (requester_name or "").strip() or None
    locked.changes_requested_at = datetime.utcnow()
    locked.changes_requested_note = text_note
    locked.changes_requested_by_name = name
    db.flush()

    _record_changes_requested(db, record=locked, note=text_note, requester_name=name)
    return locked


def _record_changes_requested(
    db: Session,
    *,
    record: ProjectQuotationIssue,
    note: str,
    requester_name: Optional[str],
) -> None:
    """Tell the feed and tell the salesperson. Best-effort, both of them.

    The row is already written by the time this runs, so a notification backend that is down must
    not turn a captured request into a 500 the customer would retry - and the retry would take the
    identical-note path, which writes nothing at all.
    """
    document = (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == record.document_id)
        .first()
    )
    project = (
        db.query(Project).filter(Project.id == document.project_id).first()
        if document is not None
        else None
    )
    if project is None:
        return

    who = requester_name or "The customer"
    reference = record.our_ref_text or (document.document_no if document else "")
    try:
        from app.services import project_activity_service as activity

        # Deliberately NOT on `MEANINGFUL_TEMPLATES`: a request nobody has answered is exactly
        # when a project should look unattended, and advancing the staleness clock here would
        # clear the badge the moment the customer complains.
        activity.record_project_event(
            db,
            project=project,
            template=CHANGES_REQUESTED_TEMPLATE,
            payload={
                "document_id": str(record.document_id),
                "issue_id": str(record.id),
                "issue_no": record.issue_no,
                "our_ref": reference,
                "requested_by": requester_name,
            },
            body_text=f"{who} asked for changes to {reference}: {note}",
        )
        db.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "quotation changes-requested activity not written: issue=%s (%s)", record.id, exc
        )

    try:
        from app.services import project_notify_service as notify

        notify.notify_quotation_changes_requested(
            db,
            project=project,
            document=document,
            record=record,
            note=note,
            requester_name=requester_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "quotation changes-requested notification not sent: issue=%s (%s)", record.id, exc
        )


# ------------------------------------------------------- the public sign page


def _serialize_signature(signature: Optional[QuotationSignature]) -> Optional[Dict[str, Any]]:
    if signature is None:
        return None
    return {
        "id": str(signature.id),
        "signer_name": signature.signer_name,
        "mode": signature.mode,
        "image_data_uri": signature.image_data_uri,
        "signed_at": signature.signed_at,
        "ip_address": signature.ip_address,
        "gps_lat": signature.gps_lat,
        "gps_lng": signature.gps_lng,
    }


def serialize_sign_page(db: Session, record: ProjectQuotationIssue) -> Dict[str, Any]:
    """The quotation as ISSUED, for the customer's read-only counter-sign page.

    Every line comes from the `version_id` the issue recorded, never from the scope's current
    version. If it read live rows, the page could show the customer something different from the
    PDF in their inbox, which is the one thing a signing surface must never do.
    """
    document = (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == record.document_id)
        .first()
    )
    pairs = (
        db.query(ProjectQuotationIssueScope)
        .filter(ProjectQuotationIssueScope.issue_id == record.id)
        .order_by(ProjectQuotationIssueScope.sort_order)
        .all()
    )

    scopes: List[Dict[str, Any]] = []
    for pair in pairs:
        scope = (
            db.query(ProjectQuotation)
            .filter(ProjectQuotation.id == pair.quotation_id)
            .first()
        )
        lines = (
            db.query(ProjectQuotationLine)
            .filter(ProjectQuotationLine.version_id == pair.version_id)
            .order_by(
                ProjectQuotationLine.sort_order,
                ProjectQuotationLine.created_at,
                ProjectQuotationLine.id,
            )
            .all()
        )
        scopes.append(
            {
                # The label is read live, and that is a deliberate, small exception: renaming a tab
                # does not change what was priced, and a stale label helps nobody.
                "scope_label": scope.scope_label if scope else "",
                "scope_total": Decimal(pair.scope_total or 0),
                "lines": [
                    {
                        # Same definition the PDF and the workbook use. The customer signing this
                        # page and the customer reading the PDF must see the same item numbers.
                        # Same definition the PDF and the workbook use, imported INSIDE the
                        # function on purpose: the pdf module pulls in WeasyPrint's neighbours and
                        # storage, and importing that at module scope changes what is loaded before
                        # the test fixtures build their schema.
                        "item_label": _item_number(line, position),
                        "description": line.description_snapshot,
                        "technical_spec": line.technical_spec,
                        "brand": line.brand_snapshot,
                        "product_code": line.product_code_snapshot,
                        "quantity": Decimal(line.quantity or 0),
                        "unit_price": Decimal(line.unit_price or 0),
                        "complete_set": line.complete_set,
                        "band_label": line.band_label,
                        "is_rate_only": bool(line.is_rate_only),
                        # None, not zero: the page prints "rate only" and a zero would read as free.
                        "amount": None if line.is_rate_only else Decimal(line.line_total or 0),
                    }
                    for position, line in enumerate(lines, start=1)
                ],
            }
        )

    sorento = (
        db.query(QuotationSignature)
        .filter(QuotationSignature.id == record.sorento_signature_id)
        .first()
        if record.sorento_signature_id
        else None
    )
    customer = (
        db.query(QuotationSignature)
        .filter(QuotationSignature.id == record.customer_signature_id)
        .first()
        if record.customer_signature_id
        else None
    )

    company_name = None
    if document is not None and document.company_id:
        from app.models.company import Company

        company = (
            db.query(Company).filter(Company.id == document.company_id).first()
        )
        company_name = company.name if company else None

    return {
        "our_ref": record.our_ref_text,
        "issue_no": record.issue_no,
        "doc_date": document.doc_date if document else None,
        "subject_title": document.subject_title if document else None,
        "sender_name": company_name,
        "recipient_name": document.recipient_name_snapshot if document else None,
        "recipient_address": document.recipient_address_snapshot if document else None,
        "attn_name": document.attn_name if document else None,
        "cover_letter": record.cover_letter_rendered,
        "terms": record.terms_rendered,
        "signatory_name": document.signatory_name if document else None,
        "scopes": scopes,
        "grand_total": Decimal(record.grand_total or 0),
        "sorento_signature": _serialize_signature(sorento),
        "customer_signature": _serialize_signature(customer),
        "accepted_at": record.accepted_at,
        "is_accepted": record.accepted_at is not None,
        # So the page can settle on either decision without a reload, and quote the customer's
        # own words back to them rather than making them wonder whether it sent.
        "changes_requested_at": record.changes_requested_at,
        "changes_requested_note": record.changes_requested_note,
        "changes_requested_by_name": record.changes_requested_by_name,
        "is_changes_requested": record.changes_requested_at is not None,
    }
