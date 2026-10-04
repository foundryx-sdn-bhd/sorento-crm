"""Onboarding requests: minting, intake, review, approval.

Module-level functions rather than a class, matching ``dealer_kit.edition_service``
- the service this one's transition machinery is copied from.

Two things are worth reading before the code.

**Every move goes through the status engine.** ``_move`` re-reads the row under a
row lock, asks ``status_service.assert_transition_allowed``, and only then stamps
whatever the transition records. That is what makes a second Approve a refusal
rather than a race: two clicks both pass a plain ``if request.status_key ==
"in_review"`` check against the same pre-state and the second one enqueues a
second provisioning job.

**Collisions are computed, never stored.** ``collisions_for`` runs three live
queries against the unique keys that would actually reject an insert
(``users.email``, ``users.contact_number``, ``respond_contacts.phone_number``). A
stored collision is a lie the moment somebody creates that user by hand, and the
review screen is exactly where a stale answer does damage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.access import RespondContact
from app.models.base import get_company_scope
from app.models.company import Company
from app.models.onboarding import (
    LANE_PENDING,
    REVIEW_APPROVED,
    REVIEW_ON_HOLD,
    REVIEW_PROPOSED,
    REVIEW_REJECTED,
    OnboardingPerson,
    OnboardingRequest,
    OnboardingTemplate,
)
from app.models.status import Status
from app.models.user import User
from app.services import status_service
from app.services.crockford import crockford_token
from app.services.error_handler import AppException
from app.services.phone_utils import normalize_msisdn

logger = logging.getLogger(__name__)

ENTITY = "onboarding_request"

DRAFT = "draft"
SENT = "sent"
SUBMITTED = "submitted"
IN_REVIEW = "in_review"
PROCESSING = "processing"
COMPLETED = "completed"
PARTIALLY_COMPLETED = "partially_completed"
REJECTED = "rejected"
CANCELLED = "cancelled"

#: How long an intake link lives by default. Long enough that a department head
#: can gather her list over a few days without asking for a new link, short
#: enough that a forgotten link in a WhatsApp thread stops working.
DEFAULT_EXPIRY_DAYS = 14

#: The only status in which the requester may write. Checked on every public
#: write, because holding a valid token is not the same as being allowed to
#: change a batch somebody is already reviewing.
REQUESTER_WRITABLE = (SENT,)

#: The only statuses in which the REVIEWER may write. Holding
#: `user_management.onboarding.edit` is not the same as being allowed to rewrite
#: a batch that has already been provisioned: once the job has run, the person
#: rows are the record of what was created, and an edit or a late verdict change
#: silently contradicts the ledger next to it (and the invitation already sent).
#: `submitted` is included because the reviewer legitimately fixes a typo before
#: pressing Start review.
REVIEWER_WRITABLE = (SUBMITTED, IN_REVIEW)


class OnboardingAuthError(Exception):
    """The intake token is missing, unknown, expired or revoked."""


@dataclass(frozen=True)
class Collision:
    kind: str
    label: str


def _utcnow() -> datetime:
    return datetime.utcnow()


def _status_by_key(db: Session, key: str) -> Status:
    """The status row for a key in the default graph.

    A missing row is a 500-shaped problem, not a user error: the graph is seeded
    by migration and its absence means the migration did not run. Said plainly
    rather than surfacing as an AttributeError three frames down.
    """
    graph = status_service.resolve_graph(db, ENTITY, None)
    status = graph.by_key(key)
    if status is None:
        raise AppException(
            status_code=500,
            message=(
                f"The onboarding status '{key}' is missing. "
                "Its status graph has not been seeded."
            ),
        )
    return status


def _assert_template_on_offer(db: Session, template_id: Optional[str]) -> None:
    """Refuse a template id that is not one of the ones on offer.

    Both writers take ``template_id`` straight from client input, and it is a
    UUID foreign key: ``"nope"`` reached Postgres as ``invalid input syntax for
    type uuid`` and a well-formed unknown id as a foreign-key violation, so a
    token holder got a 500 where every other bad field on the same payload
    answers 422.

    Compared as STRINGS against the ids actually on offer, never as a
    ``WHERE id = <input>``: that is what keeps the malformed value away from
    Postgres, which would otherwise abort the surrounding transaction as well.
    The set is what this session can see, so the check is scoped to the
    request's own company exactly as the picker is.
    """
    if not template_id:
        return
    known = {str(row[0]) for row in db.query(OnboardingTemplate.id).all()}
    if str(template_id) not in known:
        raise AppException(
            status_code=422,
            message="That access template is not available. Pick one from the list.",
        )


def _validated_company_id(db: Session, company_id: Optional[str]) -> str:
    """The company a new request belongs to, or a 422.

    Title, email and expiry were all checked and the company was not, even though
    it is the field that decides what approving the batch actually grants: an
    unknown id escaped ``db.commit()`` as a foreign-key violation and a malformed
    one as a UUID cast error, both 500s. An id outside the caller's own scope is
    refused for a different reason - it committed happily and then vanished
    behind that caller's own scope filter, so they were 404'd on the request they
    had just created.

    String comparison again, for the reason in ``_assert_template_on_offer``.
    """
    wanted = str(company_id or "").strip()
    existing = {str(row[0]) for row in db.query(Company.id).all()}
    scope = get_company_scope(db)
    if scope is None:
        # The deliberate all-companies principal (a worker, a system caller).
        visible = existing
    elif isinstance(scope, frozenset):
        visible = existing & {str(cid) for cid in scope}
    else:
        # UNSET: no scope was ever resolved, which is fail-closed everywhere else.
        visible = set()
    if wanted not in visible:
        raise AppException(
            status_code=422,
            message="Choose a company you have access to for this request.",
        )
    return wanted


def _fold_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    folded = value.strip().lower()
    return folded or None


#: A Malaysian MSISDN is `60` plus a 9- or 10-digit subscriber number, so the
#: normalised form is 11 or 12 digits.
_MSISDN_MIN_LEN = 11
_MSISDN_MAX_LEN = 12


def _plausible_msisdn(raw: Optional[str]) -> Optional[str]:
    """The MSISDN for what somebody typed, or None when it is not a phone number.

    Returns the same bare-digits form the rest of the system stores
    (``60100000002``, no leading ``+``) so a value written here compares directly
    against ``users.contact_number`` and ``respond_contacts.phone_number``.

    The length judgement lives HERE rather than in ``normalize_msisdn``, which is
    deliberately permissive because it is also the matcher that links an existing
    user to an existing contact - refusing an odd-looking stored number there
    would break a real link. Intake is the opposite situation: the number has
    never been dialled, and ``01x-34567`` normalising to ``60134567`` would be
    written to ``users.contact_number`` as though it were a phone number.
    """
    if not raw:
        return None
    normalised = normalize_msisdn(raw)
    if not normalised or not normalised.isdigit():
        return None
    if not (_MSISDN_MIN_LEN <= len(normalised) <= _MSISDN_MAX_LEN):
        return None
    return normalised


# --- requests -----------------------------------------------------------------


def get_request(db: Session, request_id: str) -> OnboardingRequest:
    """The request, or a 404.

    Company scope is applied by the ORM filter, so another company's request is a
    404 here and never a 403 - a 403 confirms the id exists.
    """
    request = (
        db.query(OnboardingRequest).filter(OnboardingRequest.id == request_id).first()
    )
    if request is None:
        raise AppException(status_code=404, message="Onboarding request not found")
    return request


#: The columns the queue may be ordered by, keyed by the `sort` the grid sends.
#: Anything else falls back to `created_at`, so a stale bookmark cannot 500.
LIST_SORT_COLUMNS = {
    "title": OnboardingRequest.title,
    "requester_name": OnboardingRequest.requester_name,
    "status": OnboardingRequest.status_key,
    "submitted_at": OnboardingRequest.submitted_at,
    "expires_at": OnboardingRequest.expires_at,
    "created_at": OnboardingRequest.created_at,
}

DEFAULT_LIST_SORT = "created_at"
DEFAULT_LIST_DIR = "desc"


def _list_query(
    db: Session,
    *,
    status_key: Optional[str] = None,
    query: Optional[str] = None,
    sort_field: str = DEFAULT_LIST_SORT,
    sort_dir: str = DEFAULT_LIST_DIR,
):
    """The filtered + sorted queue query, shared by the list page and the pager.

    Ordering always ends on `id` so two requests created in the same second keep
    a stable relative position: without a tie-breaker the same row can appear on
    two pages, or on none.
    """
    q = db.query(OnboardingRequest)
    if status_key:
        q = q.filter(OnboardingRequest.status_key == status_key)
    if query:
        like = f"%{query.strip().lower()}%"
        q = q.filter(
            func.lower(OnboardingRequest.title).like(like)
            | func.lower(OnboardingRequest.requester_name).like(like)
            | func.lower(OnboardingRequest.requester_email).like(like)
        )

    if sort_field == "company_name":
        # The name lives on `companies`, so this is the one sort that needs a
        # join. Outer, because a request whose company row was deleted must
        # still be listable rather than silently vanishing from the queue.
        q = q.outerjoin(Company, Company.id == OnboardingRequest.company_id)
        column = Company.name
    else:
        column = LIST_SORT_COLUMNS.get(sort_field or "", LIST_SORT_COLUMNS[DEFAULT_LIST_SORT])

    # NULLS LAST in both directions. `submitted_at` is null until the requester
    # sends her list back, and Postgres sorts nulls first on DESC - so "newest
    # submissions first" opened on every draft that has never been submitted at
    # all, burying the rows the reviewer came for.
    ordering = (
        column.desc().nullslast()
        if (sort_dir or "").lower() == "desc"
        else column.asc().nullslast()
    )
    return q.order_by(ordering, OnboardingRequest.id.asc())


def list_requests(
    db: Session,
    *,
    status_key: Optional[str] = None,
    query: Optional[str] = None,
    sort_field: str = DEFAULT_LIST_SORT,
    sort_dir: str = DEFAULT_LIST_DIR,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[OnboardingRequest], int]:
    """One page of the queue, plus the total size of the filtered set.

    The total is counted server-side rather than inferred from the page, so the
    pager reports how much work is waiting rather than how much was fetched.
    """
    q = _list_query(
        db,
        status_key=status_key,
        query=query,
        sort_field=sort_field,
        sort_dir=sort_dir,
    )
    total = q.order_by(None).count()
    page = max(1, page)
    limit = max(1, limit)
    # The summary counts people per row, so fetch them with the page instead of
    # one lazy load per request.
    rows = (
        q.options(selectinload(OnboardingRequest.people))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return rows, total


def create_request(
    db: Session,
    *,
    company_id: str,
    title: str,
    requester_name: str,
    requester_email: str,
    requester_phone: Optional[str] = None,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    user_id: Optional[str] = None,
) -> OnboardingRequest:
    """Mint a request and its intake link, at ``draft``."""
    title = (title or "").strip()
    if not title:
        raise AppException(status_code=422, message="Give the request a title.")
    email = _fold_email(requester_email)
    if not email:
        raise AppException(
            status_code=422, message="The requester's email address is required."
        )
    if expiry_days < 1:
        raise AppException(status_code=422, message="The link must last at least a day.")
    company_id = _validated_company_id(db, company_id)

    initial = _status_by_key(db, DRAFT)
    request = OnboardingRequest(
        company_id=company_id,
        title=title,
        requester_name=(requester_name or "").strip() or email,
        requester_email=email,
        requester_phone=_plausible_msisdn(requester_phone),
        token=crockford_token(),
        expires_at=_utcnow() + timedelta(days=expiry_days),
        status_id=initial.id,
        status_key=initial.key,
        created_by_user_id=user_id,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def resolve_token(db: Session, raw: str) -> OnboardingRequest:
    """The request behind an intake token.

    Raises ``OnboardingAuthError`` for every failure mode with the SAME shape of
    message the caller turns into a 401. The distinction between "no such token"
    and "expired" is kept because it changes what the requester should do next,
    and neither answer tells an attacker anything they could not learn by
    waiting.

    The caller reads this under an unscoped session and then narrows to the
    request's own company, mirroring `public/catalogue.py`.
    """
    token = (raw or "").strip()
    if not token:
        raise OnboardingAuthError("An onboarding link token is required.")
    request = (
        db.query(OnboardingRequest).filter(OnboardingRequest.token == token).first()
    )
    if request is None:
        raise OnboardingAuthError("This onboarding link is not valid.")
    if request.revoked_at is not None:
        raise OnboardingAuthError(
            "This onboarding link has been withdrawn. Ask for a new one."
        )
    if request.expires_at <= _utcnow():
        raise OnboardingAuthError(
            "This onboarding link has expired. Ask for a new one."
        )
    return request


def _move(
    db: Session,
    request: OnboardingRequest,
    to_key: str,
    *,
    apply: Optional[callable] = None,
) -> OnboardingRequest:
    """Move a request to ``to_key``, or refuse because the graph says so.

    The row is locked and re-read FIRST. Without it this is a
    read-then-unconditional-write: the legality check runs against whatever the
    session already had, and the UPDATE carries no ``WHERE status_key = <from>``,
    so two concurrent transitions both pass against the same pre-state and the
    second commit silently wins. The reachable version is a double-clicked
    Approve enqueueing provisioning twice.

    ``apply`` stamps whatever else the transition records and runs only AFTER the
    move is known to be legal, so a refused transition cannot leave a
    ``reviewed_by`` behind on a request that never moved.
    """
    db.refresh(request, with_for_update=True)

    target = _status_by_key(db, to_key)
    status_service.assert_transition_allowed(
        db,
        ENTITY,
        from_status_id=request.status_id,
        to_status_id=target.id,
    )

    request.status_id = target.id
    # Carried in lockstep with the id: every list query reads the KEY, so a move
    # that updated only the id leaves a finished request sitting in the queue.
    request.status_key = target.key
    if apply is not None:
        apply()

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(
            status_code=409,
            message="This onboarding request has already moved on. Reload and try again.",
        )
    db.refresh(request)
    return request


def send_request(db: Session, request_id: str, *, user_id: Optional[str] = None) -> OnboardingRequest:
    """draft -> sent. The link is now live and the requester may write."""
    request = get_request(db, request_id)

    def _stamp() -> None:
        request.sent_at = _utcnow()

    return _move(db, request, SENT, apply=_stamp)


def revoke(db: Session, request_id: str) -> OnboardingRequest:
    """Kill the link without moving the request's status.

    Revocation is a property of the TOKEN, not of the batch: a request whose link
    leaked is still a legitimate request in review, and forcing it into
    `cancelled` to stop the link would throw away the work.
    """
    request = get_request(db, request_id)
    request.revoked_at = _utcnow()
    db.commit()
    db.refresh(request)
    return request


def regenerate_token(
    db: Session, request_id: str, *, expiry_days: int = DEFAULT_EXPIRY_DAYS
) -> OnboardingRequest:
    """A fresh link, and the old one stops working the moment this commits."""
    request = get_request(db, request_id)
    request.token = crockford_token()
    request.expires_at = _utcnow() + timedelta(days=expiry_days)
    request.revoked_at = None
    db.commit()
    db.refresh(request)
    return request


def start_review(
    db: Session, request_id: str, *, user_id: Optional[str] = None
) -> OnboardingRequest:
    """submitted -> in_review, and record who picked it up."""
    request = get_request(db, request_id)

    def _stamp() -> None:
        request.reviewed_by_user_id = user_id
        request.reviewed_at = _utcnow()

    return _move(db, request, IN_REVIEW, apply=_stamp)


def reject_request(
    db: Session, request_id: str, *, reason: str, user_id: Optional[str] = None
) -> OnboardingRequest:
    """in_review -> rejected. The whole batch, with a reason the requester sees."""
    reason = (reason or "").strip()
    if not reason:
        raise AppException(
            status_code=422,
            message="Say why it is being rejected. The requester sees this.",
        )
    request = get_request(db, request_id)

    def _stamp() -> None:
        request.reviewer_note = reason
        request.reviewed_by_user_id = user_id
        request.reviewed_at = _utcnow()

    return _move(db, request, REJECTED, apply=_stamp)


def approve(
    db: Session, request_id: str, *, user_id: Optional[str] = None
) -> OnboardingRequest:
    """in_review -> processing, then queue provisioning.

    The enqueue happens in the ROUTE, after this returns and the transition has
    committed - a job that starts against an uncommitted status reads the
    request as still `in_review` and does nothing.
    """
    request = get_request(db, request_id)
    if not any(p.review_status == REVIEW_APPROVED for p in request.people):
        raise AppException(
            status_code=422,
            message=(
                "Nobody on this request is approved yet. Keep at least one person "
                "before approving the batch."
            ),
        )

    def _stamp() -> None:
        request.reviewed_by_user_id = user_id or request.reviewed_by_user_id
        request.reviewed_at = _utcnow()

    return _move(db, request, PROCESSING, apply=_stamp)


def finalise(db: Session, request: OnboardingRequest) -> OnboardingRequest:
    """processing -> completed | partially_completed, decided by the user lane.

    **Only the user lane counts in this slice**, and that is a deliberate,
    documented limit rather than an oversight: the contact and agent lanes are
    not automated yet, so counting them would mark every batch containing a
    WhatsApp-contact person as incomplete forever. They stay `pending`, the UI
    says "not yet automated", and Slice 2's backlog query is exactly
    `contact_step = 'pending'`.
    """
    approved = [p for p in request.people if p.review_status == REVIEW_APPROVED]
    failed = [p for p in approved if p.user_step == "failed"]
    target = PARTIALLY_COMPLETED if failed else COMPLETED

    def _stamp() -> None:
        request.provisioned_at = _utcnow()

    return _move(db, request, target, apply=_stamp)


# --- people -------------------------------------------------------------------

#: The fields either screen may write on a person row. Anything not listed is
#: the system's - a patch cannot reach `user_step` or `review_status`, which are
#: the columns that decide what gets created.
EDITABLE_PERSON_FIELDS = (
    "full_name",
    "nick_name",
    "role_label",
    "phone_raw",
    "email_raw",
    "template_id",
    "requester_note",
    "reviewer_note",
    "needs_system_account",
    "needs_respond_contact",
    "needs_agent_seat",
)


def _apply_person_values(db: Session, person: OnboardingPerson, values: dict) -> None:
    """Set the editable fields, re-deriving the normalised copies as they change.

    Both screens write through here, which is why the template id is checked here
    rather than in either route: a guard on one writer leaves the other one
    handing raw client input to a UUID foreign key.
    """
    if "template_id" in values:
        _assert_template_on_offer(db, values["template_id"])
    for field in EDITABLE_PERSON_FIELDS:
        if field in values:
            setattr(person, field, values[field])
    if "phone_raw" in values:
        person.phone = _plausible_msisdn(person.phone_raw)
    if "email_raw" in values:
        person.email = _fold_email(person.email_raw)


def get_person(db: Session, person_id: str) -> OnboardingPerson:
    person = db.query(OnboardingPerson).filter(OnboardingPerson.id == person_id).first()
    if person is None:
        raise AppException(status_code=404, message="That person is not on this request")
    return person


def _assert_reviewer_writable(db: Session, person: OnboardingPerson) -> None:
    """Refuse a reviewer write once the batch has left review.

    Checked here rather than only in the UI: the detail page renders the same
    grid in every status, so a stale tab is enough to reach this.
    """
    request = get_request(db, person.request_id)
    if request.status_key not in REVIEWER_WRITABLE:
        raise AppException(
            status_code=409,
            message=(
                "This batch is no longer open for review, so it cannot be changed. "
                "Anything still outstanding needs a new request."
            ),
        )


def replace_people(
    db: Session, request: OnboardingRequest, rows: Iterable[dict]
) -> list[OnboardingPerson]:
    """The requester's draft save: whole-list replace, keyed on row_number.

    A whole-list replace rather than a diff because the intake grid IS the list -
    she adds, edits and removes rows freely, and a diff protocol would need her
    client to track ids it has no reason to hold.

    Refused unless the request is still `sent`: once it is in review, somebody is
    reading the rows, and a silent rewrite underneath them is how a reviewer
    approves people who are no longer on the list.
    """
    if request.status_key not in REQUESTER_WRITABLE:
        raise AppException(
            status_code=409,
            message="This request has already been submitted and can no longer be changed.",
        )

    request.people.clear()
    db.flush()

    people: list[OnboardingPerson] = []
    for index, row in enumerate(rows, start=1):
        name = (row.get("full_name") or "").strip()
        if not name:
            # A blank row is a row she started and abandoned, not an error worth
            # refusing a 30-person submission over.
            continue
        person = OnboardingPerson(
            request_id=request.id,
            company_id=request.company_id,
            row_number=index,
            full_name=name,
            review_status=REVIEW_PROPOSED,
            user_step=LANE_PENDING,
            contact_step=LANE_PENDING,
            agent_step=LANE_PENDING,
        )
        _apply_person_values(
            db,
            person,
            {k: v for k, v in row.items() if k in EDITABLE_PERSON_FIELDS and k != "full_name"},
        )
        person.phone = _plausible_msisdn(person.phone_raw)
        person.email = _fold_email(person.email_raw)
        db.add(person)
        people.append(person)

    db.commit()
    db.refresh(request)
    return list(request.people)


def submit(
    db: Session, request: OnboardingRequest, *, requester_note: Optional[str] = None
) -> OnboardingRequest:
    """sent -> submitted. The link becomes the read-only status page.

    The note is stamped HERE rather than set by the caller before calling. It has
    to be, and the reason is not obvious: ``_move`` opens with
    ``db.refresh(request, with_for_update=True)``, and a refresh discards pending
    unflushed attribute changes. A caller that set ``request.requester_note`` and
    then called ``submit`` lost it silently - the submission succeeded, the note
    vanished, and nothing anywhere reported a problem.
    """
    if not request.people:
        raise AppException(
            status_code=422,
            message="Add at least one person before submitting.",
        )

    def _stamp() -> None:
        request.submitted_at = _utcnow()
        if requester_note is not None:
            request.requester_note = requester_note.strip() or None

    return _move(db, request, SUBMITTED, apply=_stamp)


def update_person(db: Session, person_id: str, values: dict) -> OnboardingPerson:
    person = get_person(db, person_id)
    _assert_reviewer_writable(db, person)
    _apply_person_values(
        db, person, {k: v for k, v in values.items() if k in EDITABLE_PERSON_FIELDS}
    )
    db.commit()
    db.refresh(person)
    return person


def set_person_verdict(
    db: Session,
    person_id: str,
    *,
    verdict: str,
    reason: Optional[str] = None,
) -> OnboardingPerson:
    """Keep, reject or hold one person.

    A rejection reason is required HERE and not only in the dialog: this is the
    last place that can refuse one, and a rejection the requester cannot act on
    is worse than no rejection at all.
    """
    if verdict not in (REVIEW_APPROVED, REVIEW_REJECTED, REVIEW_ON_HOLD, REVIEW_PROPOSED):
        raise AppException(status_code=422, message="That is not a review verdict.")

    person = get_person(db, person_id)
    _assert_reviewer_writable(db, person)
    if verdict == REVIEW_REJECTED:
        cleaned = (reason or "").strip()
        if not cleaned:
            raise AppException(
                status_code=422,
                message="Say why they are being rejected. The requester sees this.",
            )
        person.rejection_reason = cleaned
    else:
        # Cleared on any other verdict: a reason left behind on a kept row reads
        # as a rejection on every screen that shows it.
        person.rejection_reason = None
    person.review_status = verdict
    db.commit()
    db.refresh(person)
    return person


# --- collisions ---------------------------------------------------------------


def collisions_for(db: Session, request: OnboardingRequest) -> dict[str, list[Collision]]:
    """Person id -> what already exists, computed live against the unique keys.

    Three queries for the whole batch rather than three per person: an 18-row
    request would otherwise fire 54.

    An existing artifact is NOT an error. It pre-decides that lane as `skipped`
    with the existing id captured, so onboarding somebody who half-exists fills
    in what is missing instead of failing on a duplicate-key error nobody can
    act on.
    """
    people = list(request.people)
    emails = {p.email for p in people if p.email}
    phones = {p.phone for p in people if p.phone}

    users_by_email: dict[str, User] = {}
    users_by_phone: dict[str, User] = {}
    contacts_by_phone: dict[str, RespondContact] = {}

    if emails:
        for user in db.query(User).filter(func.lower(User.email).in_(emails)).all():
            folded = _fold_email(user.email)
            if folded:
                users_by_email[folded] = user
    if phones:
        for user in db.query(User).filter(User.contact_number.in_(phones)).all():
            if user.contact_number:
                users_by_phone[user.contact_number] = user
        for contact in (
            db.query(RespondContact).filter(RespondContact.phone_number.in_(phones)).all()
        ):
            if contact.phone_number:
                contacts_by_phone[contact.phone_number] = contact

    out: dict[str, list[Collision]] = {}
    for person in people:
        found: list[Collision] = []
        user = users_by_email.get(person.email) if person.email else None
        if user is not None:
            found.append(
                Collision(
                    kind="user_email",
                    # A name, never an id: the reviewer needs to recognise the
                    # person, and a UUID on screen is the cursor rule's exact
                    # prohibition.
                    label=f"Already a user: {user.name or user.email}",
                )
            )
        phone_user = users_by_phone.get(person.phone) if person.phone else None
        if phone_user is not None and phone_user is not user:
            found.append(
                Collision(
                    kind="user_phone",
                    label=f"Phone belongs to user: {phone_user.name or phone_user.email}",
                )
            )
        contact = contacts_by_phone.get(person.phone) if person.phone else None
        if contact is not None:
            found.append(
                Collision(
                    kind="contact_phone",
                    label=(
                        # The captain's words for the respond-contact lane: what
                        # this person already has is chatbot access.
                        "Already has chatbot AI access"
                        + (f": {contact.name}" if getattr(contact, "name", None) else "")
                    ),
                )
            )
        out[str(person.id)] = found
    return out


# --- templates ----------------------------------------------------------------


def list_templates(db: Session, *, active_only: bool = False) -> list[OnboardingTemplate]:
    q = db.query(OnboardingTemplate)
    if active_only:
        q = q.filter(OnboardingTemplate.is_active.is_(True))
    return q.order_by(OnboardingTemplate.name.asc()).all()


def get_template(db: Session, template_id: str) -> OnboardingTemplate:
    template = (
        db.query(OnboardingTemplate).filter(OnboardingTemplate.id == template_id).first()
    )
    if template is None:
        raise AppException(status_code=404, message="Access template not found")
    return template


def create_template(db: Session, values: dict, *, user_id: Optional[str] = None) -> OnboardingTemplate:
    name = (values.get("name") or "").strip()
    if not name:
        raise AppException(status_code=422, message="Give the template a name.")
    template = OnboardingTemplate(
        name=name,
        description=values.get("description"),
        is_active=values.get("is_active", True),
        role_ids=values.get("role_ids") or [],
        company_ids=values.get("company_ids") or [],
        access_agent_ids=values.get("access_agent_ids") or [],
        default_needs_system_account=values.get("default_needs_system_account", True),
        default_needs_respond_contact=values.get("default_needs_respond_contact", False),
        default_needs_agent_seat=values.get("default_needs_agent_seat", False),
        captured_from_user_id=values.get("captured_from_user_id"),
        created_by_user_id=user_id,
    )
    db.add(template)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(
            status_code=409,
            message=f"An access template called '{name}' already exists.",
        )
    db.refresh(template)
    return template


TEMPLATE_EDITABLE_FIELDS = (
    "name",
    "description",
    "is_active",
    "role_ids",
    "company_ids",
    "access_agent_ids",
    "default_needs_system_account",
    "default_needs_respond_contact",
    "default_needs_agent_seat",
)


def update_template(db: Session, template_id: str, values: dict) -> OnboardingTemplate:
    template = get_template(db, template_id)
    for field in TEMPLATE_EDITABLE_FIELDS:
        if field in values:
            setattr(template, field, values[field])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppException(
            status_code=409, message="An access template with that name already exists."
        )
    db.refresh(template)
    return template


def delete_template(db: Session, template_id: str) -> None:
    """Hard delete. Person rows keep their row and lose the pointer (SET NULL)."""
    template = get_template(db, template_id)
    db.delete(template)
    db.commit()


def capture_template_from_user(
    db: Session, user_id: str, *, name: str, actor_user_id: Optional[str] = None
) -> OnboardingTemplate:
    """Build a template out of what one existing user actually has.

    This is "copy from somebody similar" turned into an auditable object: the
    captain has always done it, and doing it from memory is why two salespeople
    hired a month apart end up with different access.
    """
    from app.models.company import UserCompany
    from app.models.user import UserRoleAssignment

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise AppException(status_code=404, message="User not found")

    role_ids = [
        str(row.role_id)
        for row in db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user_id).all()
    ]
    company_ids = [
        str(row.company_id)
        for row in db.query(UserCompany).filter(UserCompany.user_id == user_id).all()
    ]
    return create_template(
        db,
        {
            "name": name,
            "description": f"Captured from {user.name or user.email}.",
            "role_ids": role_ids,
            "company_ids": company_ids,
            "default_needs_system_account": True,
            "captured_from_user_id": str(user.id),
        },
        user_id=actor_user_id,
    )
