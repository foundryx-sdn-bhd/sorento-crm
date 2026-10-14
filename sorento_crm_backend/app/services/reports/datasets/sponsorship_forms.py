"""The rows behind the sponsorship report: `purchase_requests` of type sponsorship_form.

**Company scope is deliberately `none`, and this is where that is justified.**
`purchase_requests` carries no `company_id` at all, and 0 of 48 live forms link to a
project - so scoping through the linked project, the way `sponsorship_link_service` does,
would blank the whole report for everybody. A sponsorship form belongs to whoever raised
it, not to a company, until the day the table gains a company of its own. Declared, not
defaulted (registry.Dataset refuses a dataset that says nothing about scope).

Everything the workbook shows is read here as SQL, so the detail table and the pivot are
two shapes of ONE row set and cannot disagree:

- `sales_agent` resolves LIVE from the requestor contact (same precedence as
  `PurchaseRequestHeader.requested_by_contact_name`) and falls back to the typed
  `requested_by`. A renamed contact regroups history; that is the accepted trade, and it
  is the same rule the rest of the app already shows.
- `project_title` prefers the LINKED project's title over the typed one.
- `sample_price` is the form's own lines added up, `COALESCE(total, quantity * unit_price)`,
  exactly as the form's footer computes it. A form with no lines has no sample price: NULL,
  which the engine renders as absent and the workbook prints as "-". Never 0.00.
- `project_value` is the numeric column only. The "BULK ORDER EST RM1.6MIL" style of answer
  lives in `project_value_text` and stays OUT of every total.

The three tables besides `purchase_requests` are reached as CORE tables rather than mapped
entities on purpose. `projects.projects` is company-scoped, and the ORM's `do_orm_execute`
listener would splice that scope into any statement mentioning the mapped class - which is
precisely the blanking this dataset exists to avoid.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

import sqlalchemy as sa

from app.models.access import RespondContact
from app.models.lookup import LookupOption, LookupSet
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine
from app.models.projects import Project
from app.services.reports import registry as reg
from app.services.reports.engine import month_label

# Core tables, not mapped entities (see the module docstring).
_PR = PurchaseRequestHeader.__table__
_LINES = PurchaseRequestLine.__table__
_CONTACTS = RespondContact.__table__
_PROJECTS = Project.__table__
_SETS = LookupSet.__table__
_OPTIONS = LookupOption.__table__

SUBJECT_SET_KEY = "procurement_sponsor_subject"

#: The lifecycle statuses a sponsorship form can be filtered by. `voided` is absent on
#: purpose: a voided form is not "a form with a status", it is a form that never happened,
#: and the base row set drops it whatever the filter says (AC-B5).
STATUS_LABELS: Tuple[Tuple[str, str], ...] = (
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("approved", "Approved"),
    ("processed_by_cs", "Processed by CS"),
    ("closed", "Closed"),
    ("rejected", "Rejected"),
)

DEFAULT_STATUSES: Tuple[str, ...] = ("approved", "processed_by_cs")

def _blank_to_null(column) -> Any:
    return sa.func.nullif(sa.func.btrim(column), "")


# ------------------------------------------------------------------------ expressions

_FROM = _PR.outerjoin(_CONTACTS, _CONTACTS.c.id == _PR.c.requested_by_contact_id)

#: What an unattributed form is grouped under. The pivot drops a row whose row dimension is
#: blank, so without this a form with no requestor at all (they exist: forms raised through
#: the chatbot before the requestor was captured) would be counted in the detail total and
#: missing from the summary grand total, and the two halves of the same screen would show
#: different money. A named bucket is the honest version of that row.
UNASSIGNED_AGENT = "Unassigned"

#: Freeform `name`, else first + last, else the name typed on the form.
_SALES_AGENT = sa.func.coalesce(
    _blank_to_null(_CONTACTS.c.name),
    _blank_to_null(
        sa.func.concat_ws(
            " ", _blank_to_null(_CONTACTS.c.first_name), _blank_to_null(_CONTACTS.c.last_name)
        )
    ),
    _blank_to_null(_PR.c.requested_by),
    sa.literal(UNASSIGNED_AGENT),
)

_PROJECT_TITLE = sa.func.coalesce(
    _blank_to_null(
        sa.select(_PROJECTS.c.title)
        .where(_PROJECTS.c.id == _PR.c.project_id)
        .correlate(_PR)
        .scalar_subquery()
    ),
    _blank_to_null(_PR.c.project_title),
)

#: The lookup label for the chosen subject, falling back to the stored value so a subject
#: whose option was retired still reads as something.
_SUBJECT_LABEL = sa.func.coalesce(
    sa.select(_OPTIONS.c.label)
    .select_from(_OPTIONS.join(_SETS, _SETS.c.id == _OPTIONS.c.set_id))
    .where(_SETS.c.set_key == SUBJECT_SET_KEY)
    .where(sa.func.lower(_OPTIONS.c.value) == sa.func.lower(_PR.c.sponsor_subject))
    .limit(1)
    .correlate(_PR)
    .scalar_subquery(),
    _PR.c.sponsor_subject,
)

#: The free text ALONE when the subject is "others" ("Sales Gallery"), the lookup label
#: otherwise. The client's SPONSHER PROJECT column holds the answer, not the question, and
#: this report is read beside their own workbook (AC-G5). The form's listing and detail page
#: keep the "Others: Sales Gallery" shape, where the label still tells the reader which
#: field they are looking at.
_SPONSOR_SUBJECT = sa.case(
    (_PR.c.sponsor_subject.is_(None), sa.null()),
    (
        sa.and_(
            sa.func.lower(_PR.c.sponsor_subject) == "others",
            _blank_to_null(_PR.c.sponsor_subject_other).isnot(None),
        ),
        sa.func.btrim(_PR.c.sponsor_subject_other),
    ),
    else_=_SUBJECT_LABEL,
)

_SAMPLE_PRICE = (
    sa.select(
        sa.func.sum(sa.func.coalesce(_LINES.c.total, _LINES.c.quantity * _LINES.c.unit_price))
    )
    .where(_LINES.c.purchase_request_id == _PR.c.id)
    .correlate(_PR)
    .scalar_subquery()
)

_STATUS_LABEL = sa.case(
    {value: label for value, label in STATUS_LABELS},
    value=sa.func.lower(_PR.c.status),
    else_=_PR.c.status,
)


def _month(ctx) -> Any:
    """The month bucket, which is a function of the date basis the user picked."""
    return sa.func.to_char(sa.func.date_trunc("month", ctx.date_basis), "YYYY-MM")


# ------------------------------------------------------------------------- the row set


def _row_predicates() -> List[Any]:
    """Sponsorship forms, never voided. Fixed: no param can widen past this."""
    return [
        _PR.c.request_type == "sponsorship_form",
        sa.or_(_PR.c.status.is_(None), sa.func.lower(_PR.c.status) != "voided"),
        _PR.c.voided_at.is_(None),
    ]


def _base(ctx) -> sa.Select:
    return sa.select().select_from(_FROM).where(sa.and_(*_row_predicates()))


def years(db) -> List[int]:
    """The years the filter bar offers: the ones the data holds, on ANY date basis.

    Offering only the default basis's years would leave a user who switches to Form date
    unable to pick the year their forms are actually dated in. An empty dataset falls back
    to the current year, so the filter is never a dropdown with nothing in it.
    """
    found = set()
    for column in (_PR.c.approved_at, _PR.c.request_date, _PR.c.submitted_at):
        # The same Malaysia shift the engine buckets by: a form approved 31 Dec 17:00 UTC
        # is a January form here, so January's year is the one to offer for it.
        local = reg.to_malaysia(column)
        stmt = (
            sa.select(sa.distinct(sa.func.extract("year", local)))
            .select_from(_PR)
            .where(sa.and_(*_row_predicates()))
            .where(column.isnot(None))
        )
        found.update(int(row[0]) for row in db.execute(stmt) if row[0] is not None)
    return sorted(found, reverse=True) or [reg.today_malaysia().year]


def sales_agent_options(db) -> Sequence[Tuple[str, str]]:
    """Every agent the data holds, by name. A name is the id here - no UUID reaches the UI."""
    stmt = (
        sa.select(sa.distinct(_SALES_AGENT))
        .select_from(_FROM)
        .where(sa.and_(*_row_predicates()))
        .where(_SALES_AGENT.isnot(None))
    )
    names = sorted(str(row[0]) for row in db.execute(stmt) if row[0])
    return [(name, name) for name in names]


def status_options(db) -> Sequence[Tuple[str, str]]:
    return list(STATUS_LABELS)


def status_condition(ctx, values: List[str]) -> Optional[Any]:
    return sa.func.lower(_PR.c.status).in_([v.lower() for v in values])


def sales_agent_condition(ctx, values: List[str]) -> Optional[Any]:
    return _SALES_AGENT.in_(values)


#: Sizes are the DEFAULT WIDTHS the seven workbook columns plus the four delivery-year
#: ticks need to fit a 1280 screen without a horizontal scroll (AC-G2). A user's own resize
#: still wins: the DataGrid persists it per listing.
COLUMNS: Tuple[reg.Column, ...] = (
    reg.Column("request_number", "PS No", "text", "dimension", lambda c: _PR.c.request_number, size=114),
    reg.Column("sales_agent", "Sales agent", "text", "dimension", lambda c: _SALES_AGENT, size=92),
    reg.Column("customer_name", "Customer", "text", "dimension", lambda c: _PR.c.customer_name, size=98),
    reg.Column("project_title", "Project title", "text", "dimension", lambda c: _PROJECT_TITLE, size=104),
    reg.Column("sponsor_subject", "Sponsor", "text", "dimension", lambda c: _SPONSOR_SUBJECT, size=96),
    reg.Column("project_value", "Project value", "money", "measure", lambda c: _PR.c.total_project_value, size=118),
    reg.Column(
        "project_value_text",
        "Project value as stated",
        "text",
        "text",
        lambda c: _PR.c.total_project_value_text,
        size=200,
    ),
    reg.Column("sample_price", "Sample price", "money", "measure", lambda c: _SAMPLE_PRICE, size=104),
    reg.Column(
        "expected_delivery_year",
        "Expected year of delivery",
        "integer",
        "dimension",
        lambda c: sa.cast(sa.func.extract("year", _PR.c.expected_delivery_date), sa.Integer),
        size=130,
    ),
    # The delivery DATE itself. The tick band is four columns wide (the period's year and
    # the three after it), so a form due outside it reads as four blank ticks and nothing
    # else - the report holding a fact it will not show. Hidden by default like `purpose`,
    # and one tick in the Columns panel away.
    reg.Column(
        "expected_delivery_date",
        "Expected delivery date",
        "date",
        "date",
        lambda c: _PR.c.expected_delivery_date,
        size=140,
    ),
    reg.Column(
        "month",
        "Month",
        "text",
        "dimension",
        _month,
        size=110,
        period_months=True,
        # The engine already names a month bucket for the workbook's sheet tabs; one
        # rule, so the screen's header and the file's tab can never read differently.
        value_label=month_label,
    ),
    reg.Column("status", "Status", "text", "dimension", lambda c: _STATUS_LABEL, size=140),
    reg.Column("approved_at", "Approved on", "date", "date", lambda c: _PR.c.approved_at, size=130),
    reg.Column("request_date", "Form date", "date", "date", lambda c: _PR.c.request_date, size=130),
    reg.Column("submitted_at", "Submitted on", "date", "date", lambda c: _PR.c.submitted_at, size=130),
    reg.Column("approver", "Approver", "text", "dimension", lambda c: _PR.c.approved_by, size=150),
    # The workbook's OTHERS column. The KEY stays `purpose` (a view saved before the label
    # changed still resolves); the label is what the user reads (AC-G6).
    reg.Column("purpose", "Others", "text", "text", lambda c: _PR.c.purpose, size=200),
    reg.Column(
        "delivery_address", "Delivery address", "text", "text", lambda c: _PR.c.delivery_address, size=240
    ),
    reg.Column("pic", "PIC", "text", "dimension", lambda c: _PR.c.pic, size=150),
)

DATASET = reg.Dataset(
    key="sponsorship_forms",
    scope="none",  # justified in the module docstring (AC-B6)
    columns=COLUMNS,
    date_bases=(
        reg.DateBasis("approved_at", "Approved", _PR.c.approved_at),
        reg.DateBasis("request_date", "Form date", _PR.c.request_date),
        reg.DateBasis("submitted_at", "Submitted", _PR.c.submitted_at),
    ),
    base=_base,
    years=years,
)


def order_by(ctx) -> Sequence[Any]:
    """Chronological on whichever date the user is reading by, then by form number."""
    return [sa.nullslast(ctx.date_basis.asc()), _PR.c.request_number.asc()]
