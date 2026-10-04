"""Resolve the agent code a document states onto the salesperson master.

An AutoCount extract says `SEAN III` and nothing else about who sold the order. There is no
id, no email, no list of agents anywhere else in this system - so the code IS the key, and it
arrives spelled by whoever produced the export: `SEAN III`, `sean iii`, `  SEAN III `. All
three have to reach one row, or the demand class the captain fills in lands on one of them
while the other two keep classifying nothing and look, on screen, exactly like the feature
not working.

So one normalisation, `upper(btrim())`, applied on BOTH sides: the stored code and the
lookup. Storing the raw spelling and matching case-insensitively would leave the unique
constraint unable to stop the duplicate.

An unknown code is CREATED rather than refused (UAC AC-6.4). The captain has 38 of them and
no way to enter them ahead of the first upload, so blocking the file would make the feature
unusable on day one. Created rows carry `source='import'` and a NULL `demand_class`, which is
what lets the Test result report them as "new agent, unclassified" instead of quietly
inventing master data nobody knows to look at.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.models.sales_agent import SalesAgent
from app.services.error_handler import AppException
from app.services.scm.demand_class import DEMAND_CLASSES, is_valid

logger = logging.getLogger(__name__)

#: `source` values. An import-created row is waiting for a human decision; a manual one has
#: had (or does not need) it.
IMPORT_SOURCE = "import"
MANUAL_SOURCE = "manual"


def normalize_code(code: Optional[str]) -> str:
    """The stored and compared form of an agent code: trimmed and upper-cased."""
    return (code or "").strip().upper()


#: A trailing roman-numeral level (`AGENT-A III`) after a space or dash separator. The same
#: pattern is copied into the person-label migration, which may not import app services.
_LEVEL_SUFFIX = re.compile(r"[\s-]+(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$")


def derive_person_label(code: Optional[str]) -> Optional[str]:
    """The person behind an agent code: the code without its trailing level, or None if blank.

    `AGENT-A I` and `AGENT-A III` are one person at two levels. A code with no separator before
    a numeral-looking tail (`QI`) is left whole.
    """
    key = normalize_code(code)
    label = _LEVEL_SUFFIX.sub("", key).rstrip(" -")
    return label or None


def _lookup(db: Session, normalized: str) -> Optional[SalesAgent]:
    """The master row for an already-normalised code, matched however it was stored.

    Normalising the COLUMN too, rather than comparing to the stored string, so a row written
    before this service existed (the AutoCount mirror writes whatever AutoCount says) still
    resolves instead of being duplicated. The master is tens of rows, so the unindexed
    comparison costs nothing worth optimising.
    """
    if not normalized:
        return None
    return (
        db.query(SalesAgent)
        .filter(func.upper(func.btrim(SalesAgent.sales_agent)) == normalized)
        .first()
    )


def resolve(db: Session, code: Optional[str]) -> Optional[SalesAgent]:
    """The agent this code names, or None. Writes nothing - this is the preview's read."""
    return _lookup(db, normalize_code(code))


def resolve_many(db: Session, codes: Iterable[Optional[str]]) -> dict[str, SalesAgent]:
    """Normalised code -> agent, for the codes that exist. Absent codes are simply absent.

    One query per distinct code rather than an `IN` over the normalised column: the caller
    holds a handful of codes per file (38 in the captain's whole book), and the loop keeps
    the "however it was stored" matching in exactly one place.
    """
    out: dict[str, SalesAgent] = {}
    for code in codes:
        key = normalize_code(code)
        if not key or key in out:
            continue
        agent = _lookup(db, key)
        if agent is not None:
            out[key] = agent
    return out


def resolve_or_create(db: Session, code: Optional[str], *,
                      source: str = IMPORT_SOURCE) -> Optional[SalesAgent]:
    """The agent this code names, created if nobody holds it yet.

    An existing row is returned untouched - `source`, `person_label` and `demand_class` are
    the captain's, and a weekly re-upload that restated them would undo his classification
    every Monday and make the priority flap.

    A blank code creates nothing: an empty master row is one every later import would then
    resolve to.
    """
    key = normalize_code(code)
    if not key:
        return None
    existing = _lookup(db, key)
    if existing is not None:
        return existing
    agent = SalesAgent(
        sales_agent=key, source=source, is_active=True, person_label=derive_person_label(key)
    )
    db.add(agent)
    db.flush()
    return agent


def assert_demand_class(value: Optional[str]) -> Optional[str]:
    """Return the value, or refuse it because the planner cannot weigh it.

    Rejected rather than coerced: `dealer` coerced to `retail` would be a guess about a
    salesperson's book, and coerced to nothing would drop the class the admin was trying to
    set without saying so.
    """
    if is_valid(value):
        return value
    raise AppException(
        status_code=400,
        message=(
            f"{value!r} is not a demand class the fulfilment policy can weigh. "
            f"Use one of: {', '.join(DEMAND_CLASSES)}."
        ),
    )


def _backfill_null_class_orders(db: Session, agent: SalesAgent, demand_class: str) -> int:
    """Give the agent's still-unclassified orders the agent's class.

    PLAN-demand-class-agent-arrival.md (captain ruling 28 Aug 2026): the ladder puts the
    selling agent ABOVE the customer's market segment
    (`demand_class.classify_document`'s own order), because the sales force is split by
    channel and one debtor can buy through both. A row here (its `demand_class` still NULL
    for THIS agent) has already had order type checked and found silent, so the agent - not
    a re-check of the customer's segment - is the next and correct answer. Re-ranking the
    segment above the agent at backfill time, as this used to, disagreed with the ladder
    every ingest push uses.

    One bulk UPDATE, scoped to `demand_class IS NULL AND sales_agent_id = agent.id` so only
    THIS agent's still-unclassified orders move and a row another rung already answered
    keeps that answer. Runs in the caller's transaction (flushed, not committed), so a
    demand class the policy refuses leaves both the agent and its orders unchanged.
    """
    updated = db.execute(
        text(
            """
            UPDATE sales_orders
               SET demand_class = :demand_class
             WHERE sales_agent_id = :agent_id
               AND demand_class IS NULL
            """
        ),
        {"agent_id": agent.id, "demand_class": demand_class},
    ).rowcount or 0
    if updated:
        logger.info(
            "sales_agent_service: backfilled demand_class=%s onto %d NULL-class order(s) "
            "for agent %s",
            demand_class, updated, agent.sales_agent,
        )
    return updated


def set_demand_class(db: Session, code: str, demand_class: Optional[str]) -> SalesAgent:
    """Classify an existing agent. Never creates one.

    Creating on the way past would let a typed code become a master row that then classifies
    orders under a code no document ever states.
    """
    assert_demand_class(demand_class)
    agent = resolve(db, code)
    if agent is None:
        raise AppException(
            status_code=404,
            message=f"No sales agent is held under the code {normalize_code(code)!r}.",
        )
    agent.demand_class = demand_class
    db.flush()
    if demand_class:
        _backfill_null_class_orders(db, agent, demand_class)
    return agent


def normalize_location_group(value: Optional[str]) -> Optional[str]:
    """The stored form of a location group: trimmed and upper-cased, blank -> None.

    Same normalisation as `normalize_code`, and for the same reason: a warehouse code's
    suffix (`group_of_warehouse_code`) is upper-cased by construction, so a group typed
    lower-case in the edit modal must still compare equal to it.
    """
    cleaned = (value or "").strip().upper()
    return cleaned or None


def normalize_aliases(value: Optional[str]) -> Optional[str]:
    """ "William ,  Will,, " -> "William, Will": each name trimmed, blanks and repeats
    (case-insensitive) dropped, joined with ", ". Nothing left is None."""
    names: list[str] = []
    for part in (value or "").split(","):
        name = " ".join(part.split())
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return ", ".join(names) or None


def annotate(
    db: Session,
    agent: SalesAgent,
    *,
    person_label: Optional[str] = None,
    write_person_label: bool = False,
    aliases: Optional[str] = None,
    write_aliases: bool = False,
    demand_class: Optional[str] = None,
    write_demand_class: bool = False,
    location_group: Optional[str] = None,
    write_location_group: bool = False,
    contact_id: Optional[str] = None,
    write_contact_id: bool = False,
) -> SalesAgent:
    """Apply the master screen's annotations to an already-resolved agent.

    The `write_*` flags distinguish "field omitted" from "field set to nothing", so a
    save that touches only the class cannot wipe a label the captain typed last week.

    The class is written onto THIS row, not looked up again by code. `set_demand_class`
    is the code-keyed path importers use, and its lookup is a `.first()` over a code that
    is unique only PER COMPANY: a shared row (`company_id IS NULL`) and a company-owned
    row may both spell `SEAN III`, so re-resolving the code from a row the user clicked
    could flush the class onto the OTHER one. The vocabulary still has exactly one judge,
    `assert_demand_class`, which both paths call.

    Flushes but does not commit: the caller (the annotation route) writes the two mirror
    columns in the same transaction, so a rejected class leaves the note unwritten too.

    Setting the class also backfills it onto this agent's own NULL-class orders (see
    `_backfill_null_class_orders`). Clearing it (`demand_class=None`) does not - the orders
    already classified stay classified, this is a one-way "fill the gap", never an undo.
    """
    if write_demand_class:
        assert_demand_class(demand_class)
        agent.demand_class = demand_class
        db.flush()
        if demand_class:
            _backfill_null_class_orders(db, agent, demand_class)
    if write_person_label:
        cleaned = (person_label or "").strip()
        agent.person_label = cleaned or None
    if write_aliases:
        agent.aliases = normalize_aliases(aliases)
    if write_location_group:
        agent.location_group = normalize_location_group(location_group)
    if write_contact_id:
        # No vocabulary to check and no normalising to do: it is a foreign key, so an
        # id that names nobody is refused by Postgres on the flush rather than stored
        # and read back as a broken link. An empty string is an unlink, not an id.
        cleaned = (contact_id or "").strip()
        agent.contact_id = cleaned or None
    db.flush()
    return agent


def annotate_many(
    db: Session,
    agent_ids: list[str],
    *,
    demand_class: Optional[str] = None,
    write_demand_class: bool = False,
    location_group: Optional[str] = None,
    write_location_group: bool = False,
) -> int:
    """Apply ONE annotation across a selection, and answer how many rows it touched.

    `annotate` applied N times, deliberately: same `write_*` flags, same
    `assert_demand_class`, same `normalize_location_group`, same NULL-class order backfill.
    The captain's first upload created 38 unclassified codes and every one of them had to be
    opened, edited and saved on its own; nothing about what a demand class IS changes here.

    Every id must resolve. A missing one is a 404 naming nothing was written, rather than a
    quiet "updated: 37" that leaves the operator to work out which row of his selection was
    dropped and why. Flushes but does not commit, so a class the policy refuses leaves the
    whole selection untouched - the same one-save-one-transaction rule the single-row
    annotation route follows.
    """
    if not agent_ids:
        return 0
    # Judged ONCE, before a single row is touched. `annotate` judges it too (it is the one
    # judge, and both paths call it), but doing it here as well is what makes "a refused
    # class writes nothing" structural rather than a consequence of the loop's ordering.
    if write_demand_class:
        assert_demand_class(demand_class)
    unique_ids = list(dict.fromkeys(str(a) for a in agent_ids))
    rows = db.query(SalesAgent).filter(SalesAgent.id.in_(unique_ids)).all()
    found = {str(a.id) for a in rows}
    missing = [a for a in unique_ids if a not in found]
    if missing:
        raise AppException(
            status_code=404,
            message=f"{len(missing)} of the selected sales agents no longer exist.",
        )
    for agent in rows:
        annotate(
            db, agent,
            demand_class=demand_class, write_demand_class=write_demand_class,
            location_group=location_group, write_location_group=write_location_group,
        )
    return len(rows)


def agents_for_group(db: Session, group: Optional[str]) -> list[SalesAgent]:
    """Every agent holding the given ownership group, normalised the same way it is stored.

    Used to find "whose orders live at this ownership group" - the donor-list surfacing the
    PLAN describes (section 8: "the donor list must surface the SAME AGENT's other SOs").
    A blank/None group answers empty rather than every ungrouped agent, because "show me the
    group" and "show me who has no group" are different questions and this is only the first.
    """
    key = normalize_location_group(group)
    if not key:
        return []
    return (
        db.query(SalesAgent)
        .filter(func.upper(func.btrim(SalesAgent.location_group)) == key)
        .all()
    )


def group_of_warehouse_code(code: Optional[str]) -> Optional[str]:
    """The ownership-group suffix a warehouse code carries, e.g. `BRW-BB` -> `BB`.

    The suffix AFTER THE FIRST HYPHEN, upper-cased. A plain site code (`BRW`, `MWH`) has no
    hyphen and carries no group - it is a pool, not anyone's ownership group - so this
    returns None for it rather than inventing one from the whole code.
    """
    if not code:
        return None
    text_code = str(code).strip()
    if "-" not in text_code:
        return None
    _, _, suffix = text_code.partition("-")
    suffix = suffix.strip().upper()
    return suffix or None


def scope_filter(scope):
    """SQLAlchemy predicate for `sales_agents` rows visible under a caller's company scope,
    for a LISTING (what to offer in a picker) - not for validating a single write, which
    must compare against the record actually being written to instead (see
    `CustomerService._resolve_sales_agent`, PR #1177 review security item 3: a user scoped
    to {A, B} must not be able to put a company-B agent onto a company-A customer, which a
    caller-scope check alone would allow).

    `sales_agents` is deliberately NOT `CompanyScopedMixin` (see the model docstring) - a
    shared master, `company_id` NULL for every one of the ~38 codes today, with a tenant's
    own row a future possibility - so there is no `do_orm_execute` predicate to lean on.
    Visible = shared (`company_id IS NULL`) or the agent's own company is inside `scope`.
    Same shape as the `or_(cls.company_id.is_(None), ...)` predicates `rules/product_rules.
    py` and `product_predicate_service.py` already use for a shared-vs-owned master.

    Returns `None` for "no predicate" (`scope is None`, e.g. an unrestricted X-API-Key
    caller). `UNSET`/an empty frozenset fails closed to shared rows only.
    """
    if scope is None:
        return None
    if isinstance(scope, frozenset) and scope:
        return or_(SalesAgent.company_id.is_(None), SalesAgent.company_id.in_(list(scope)))
    return SalesAgent.company_id.is_(None)


def list_active(db: Session, query: Optional[str] = None, scope=None) -> list[SalesAgent]:
    """Every active `sales_agents` row, optionally text-matched on the code or the person
    label, optionally restricted to a caller's company scope (`scope_filter`).

    The ONE query both `SalesOrderService.list_agents` (`GET /scm/sales-orders/agents`) and
    the customer form's select (`GET /customers/sales-agents-select`) read from - a router
    building its own copy is a `PRINCIPLES.md` hard-fail (DB query in a router) and the two
    copies had already started to disagree about scoping before this existed (PR #1177
    review, blocking item 1).
    """
    qs = db.query(SalesAgent).filter(SalesAgent.is_active.is_(True))
    predicate = scope_filter(scope)
    if predicate is not None:
        qs = qs.filter(predicate)
    if query:
        like = f"%{query.strip()}%"
        qs = qs.filter(
            or_(SalesAgent.sales_agent.ilike(like), SalesAgent.person_label.ilike(like))
        )
    return qs.order_by(SalesAgent.sales_agent.asc()).all()
