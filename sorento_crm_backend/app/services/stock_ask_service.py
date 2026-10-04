"""Chatbot stock ask v2 (PLAN-chatbot-stock-ask-v2-24sep.md): what happens after a stock ask
is answered.

S4 (R8): when the asking contact's "Notify salesman" is on, the customer's sales agent gets
one WhatsApp message per answered product for `too_big`, `in_stock` and `no_incoming` (never
`incoming`, R6 B3). The chain is dealer contact -> customer (`resolve_customer`) ->
`customers.sales_agent_id` -> `sales_agents.contact_id` -> that contact's `respond_io_id`. A
missing link is a skip with its reason and a warning, never a raise. The send goes through
`send_text_or_template` (the `stock_ask_salesman` template outside the 24-hour window, the
same wording as plain text inside it) and every attempt writes one `integration_log` row.

Core, not the chatbot package: the engine calls in here once the turn row is closed, and the
RQ job (`app/tasks/stock_ask_tasks.py`) runs the send. Nothing here imports
`app.services.chatbot` (`tests/chatbot/test_import_boundary.py`).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.services.queue_service import enqueue_job
from app.services.respond_messaging_service import send_text_or_template

logger = logging.getLogger(__name__)

USE_CASE = "stock_ask_salesman"
#: R6: B1, B2 and B4 notify the agent; B3 (`incoming`) never does, and neither do the
#: REFER-SALESMAN branches (30 Sep 2026: the rule adds rows to the Customer asks view only).
NOTIFIED_BRANCHES = frozenset({"too_big", "in_stock", "no_incoming"})
#: The stock ask's own branches: each carries the dealer's quantity. Answered is not logged:
#: only an entry whose reply referred the customer is a row (`refer_entries`).
ANSWERED_BRANCHES = frozenset({"too_big", "in_stock", "incoming", "no_incoming"})
#: REFER-SALESMAN: a dealer's incoming ETA reply, and every other refer reply. No quantity
#: is owed; a declined did-you-mean may still carry one.
REFER_BRANCHES = frozenset({"incoming_eta", "referred"})
#: A to-do is not paged: a salesperson's open asks are tens. The cap and `truncated` are the guard.
TODO_CAP = 500

_OUTCOME = {
    "in_stock": "in stock",
    "too_big": "too big",
    "no_incoming": "no stock no incoming",
}
_MALAYSIA = timezone(timedelta(hours=8))


def outcome_phrase(
    branch: str,
    *,
    cap_unset: bool = False,
    category_name: Optional[str] = None,
    available_qty: Optional[int] = None,
    quantity: Any = None,
) -> str:
    """R8's outcome slot. B1 with no X set on the product or its category reads "no cap set
    for <category>" so the agent knows why the bot would not answer. AVAIL-MODE-REPLIES
    rule 2: a short in-stock answer reads "in stock, <N> of <Q> available"."""
    if branch == "too_big" and cap_unset:
        return f"no cap set for {category_name or 'this category'}"
    if branch == "in_stock" and isinstance(available_qty, int) and available_qty >= 1:
        return f"in stock, {available_qty} of {quantity} available"
    return _OUTCOME.get(branch, branch)


def asked_at_label(moment: datetime) -> str:
    """dd/mm/yyyy HH:MM on the Malaysia wall clock."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_MALAYSIA).strftime("%d/%m/%Y %H:%M")


def default_text(ctx: dict[str, Any]) -> str:
    """The wording sent in-window when no template is mapped (plan sample (a))."""
    return (
        f"Stock ask - {ctx['customer_name']} (contact: {ctx['contact_name']}) asked about "
        f"{ctx['product']}, qty {ctx['quantity']}, outcome: {ctx['outcome']}. "
        f"Asked at {ctx['asked_at']}."
    )


def answered_entries(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """The entries that carry an answer: a stock ask's `stock_availability` entry with a
    branch and the dealer's quantity (one still owing a quantity has no branch and is not an
    ask yet), or a REFER-SALESMAN entry (`chatbot/refer_asks.py`), whose quantity is optional."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        branch = entry.get("branch")
        qty = entry.get("requested_qty")
        has_qty = isinstance(qty, int) and not isinstance(qty, bool) and qty >= 1
        if branch in REFER_BRANCHES and (has_qty or qty is None):
            out.append(entry)
        elif branch in ANSWERED_BRANCHES and has_qty:
            out.append(entry)
    return out


def refer_entries(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """CUSTOMER-ASKS-REFER-ONLY (owner ruling 1 Oct 2026): the answered entries whose reply
    referred the customer to their salesman - the only ones Customer asks logs. The flag is
    set where the line was printed: the MCP presenter stamps a stock ask line from its own
    tail (so B3 `incoming`, "no stock at the moment, ETA ...", is never logged), and
    `chatbot/refer_asks.py` stamps every other refer reply. An entry without the flag is not
    guessed at."""
    return [e for e in answered_entries(entries) if e.get("refers_to_salesman") is True]


def answer_line(reply_text: str, entry: dict[str, Any]) -> str:
    """The exact line the dealer was sent for this entry: R14 starts every answer line
    with "<code> x <Q>:", so the line is found by that prefix in the reply."""
    label = entry.get("product_code") or entry.get("product_name") or ""
    prefix = f"{label} x {entry.get('requested_qty')}:"
    for line in (reply_text or "").splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    return prefix


def _write_company_id(db: Session, customer: Any) -> Optional[str]:
    """The ask belongs to its customer's company; an ask with no customer takes the
    turn's own company scope (the asking contact's company)."""
    if customer is not None:
        return customer.company_id
    from app.models.base import get_company_scope
    from app.services.company_scope import DEFAULT_COMPANY_ID, resolve_write_company_id

    return resolve_write_company_id(get_company_scope(db), ambiguous=DEFAULT_COMPANY_ID)


def after_answered_turn(
    db: Session,
    *,
    turn_id: str,
    contact_id: Optional[str],
    notify_salesman: bool,
    entries: Iterable[Any],
    reply_text: str = "",
    now: Optional[datetime] = None,
    source: str = "live",
) -> list[dict[str, Any]]:
    """Run once a live or chat console turn's row is closed (the engine calls this on no
    other dry run). `source` is `live` or `console` (owner ruling 28 Sep 2026: a console
    turn records and notifies too, and its rows say so on the Asks tab and portal page).

    S5: one `stock_asks` row per answered entry that referred the customer to their
    salesman (`refer_entries`; CUSTOMER-ASKS-REFER-ONLY), state open, with the exact line the dealer
    was sent (a REFER-SALESMAN entry brings its own `answer_summary`, and its `product_id`
    is resolved by code within the ask's company when the entry has none). S4: one
    `notify_salesman` job per B1 / B2 / B4 row when the contact's toggle is on and a customer
    is known; every other row records why it was not sent. Returns the facts it enqueued.
    The dealer's reply has already been handed back by then.
    """
    from app.models.order import Customer
    from app.models.stock_ask import StockAsk
    from app.services.contact_customer_service import resolve_customer

    answered = refer_entries(entries)
    if not answered:
        return []
    moment = now or datetime.now(timezone.utc)
    customer_id = resolve_customer(db, contact_id) if contact_id else None
    customer = (
        db.query(Customer).filter(Customer.id == customer_id).first() if customer_id else None
    )
    company_id = _write_company_id(db, customer)
    _resolve_product_ids(db, answered, company_id)
    # Fix round 2 (AC-SA411): the salesperson's own allowed-to-send flag, checked here so
    # no job is enqueued for a contact the send path would refuse anyway.
    blocked_agent: Optional[str] = None
    if (
        notify_salesman
        and customer is not None
        and any(e["branch"] in NOTIFIED_BRANCHES for e in answered)
    ):
        blocked_agent = _agent_not_allowed_to_send(db, contact_id, customer.id)

    rows: list[tuple[StockAsk, dict[str, Any], bool]] = []
    for entry in answered:
        branch = entry["branch"]
        if branch not in NOTIFIED_BRANCHES:
            reason: Optional[str] = "not_notified_branch"
        elif not notify_salesman:
            reason = "toggle_off"
        elif customer is None:
            reason = "no_customer"
        elif blocked_agent:
            reason = NOT_ALLOWED_TO_SEND
        else:
            reason = None
        ask = StockAsk(
            company_id=company_id,
            customer_id=customer.id if customer is not None else None,
            contact_id=contact_id,
            product_id=entry.get("product_id"),
            product_code=(entry.get("product_code") or entry.get("product_name") or "")[:100],
            quantity=entry.get("requested_qty"),
            branch=branch,
            answer_summary=entry.get("answer_summary") or answer_line(reply_text, entry),
            notified_agent=False,
            notify_skip_reason=reason,
            state="open",
            source=source,
        )
        db.add(ask)
        rows.append((ask, entry, reason is None))
    db.commit()

    enqueued = []
    for ask, entry, notify in rows:
        if ask.notify_skip_reason == NOT_ALLOWED_TO_SEND:
            _log_not_allowed(db, ask, turn_id, blocked_agent or "")
        if not notify:
            continue
        facts = {
            "ask_id": ask.id,
            "customer_id": ask.customer_id,
            "company_id": ask.company_id,
            "turn_id": turn_id,
            "contact_id": contact_id,
            "product_id": entry.get("product_id"),
            "product_code": ask.product_code,
            "product_name": entry.get("product_name"),
            "quantity": ask.quantity,
            "branch": ask.branch,
            "cap_unset": bool(entry.get("cap_unset")),
            "category_name": entry.get("category_name"),
            "available_qty": entry.get("available_qty"),
            "asked_at": moment.isoformat(),
        }
        from app.tasks.stock_ask_tasks import notify_salesman as notify_job

        try:
            enqueue_job(notify_job, facts, queue_name="respond_io", job_timeout=180)
        except Exception:  # noqa: BLE001 - e.g. Redis down: say so on the row, not "pending"
            logger.warning("stock ask %s: could not enqueue the salesman notification", ask.id, exc_info=True)
            _record_outcome(db, facts, sent=False, reason="enqueue_failed")
            continue
        enqueued.append(facts)
    return enqueued


NOT_ALLOWED_TO_SEND = "contact_not_allowed_to_send"


def _resolve_product_ids(db: Session, entries: list[dict[str, Any]], company_id: Optional[str]) -> None:
    """REFER-SALESMAN: an entry built from the reply (no `product_id`) is matched to the
    product of that code in the ask's company, so the row names the product. A code that
    matches nothing (a typo the resolver could not place) stays a bare code."""
    from app.models.product import Product

    wanted = {
        str(e.get("product_code")).strip()
        for e in entries
        if not e.get("product_id") and isinstance(e.get("product_code"), str) and e.get("product_code").strip()
    }
    if not wanted:
        return
    from sqlalchemy import func

    # Case-insensitive on both sides: a dealer types "elp3754" as often as "ELP3754".
    query = db.query(Product.id, Product.product_code).filter(
        func.lower(Product.product_code).in_([w.lower() for w in wanted])
    )
    if company_id:
        query = query.filter(Product.company_id == company_id)
    by_code = {code.casefold(): pid for pid, code in query.all()}
    for entry in entries:
        if not entry.get("product_id") and isinstance(entry.get("product_code"), str):
            pid = by_code.get(entry["product_code"].strip().casefold())
            if pid:
                entry["product_id"] = pid


def _agent_not_allowed_to_send(
    db: Session, contact_id: Optional[str], customer_id: str
) -> Optional[str]:
    """The salesperson's Respond id when the customer's salesperson has a Respond contact whose allowed-to-send
    flag (`respond_contacts.outbound_enabled`) is off. The check is the send path's own
    `assert_outbound_enabled`, not a copy of it. Any other outcome (no agent, no Respond
    id, a failed read) is None: the job then records its own reason, and the send path
    still refuses a switched-off contact."""
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import assert_outbound_enabled

    try:
        _dealer, _customer, agent_contact, _reason = _recipient(db, contact_id, customer_id)
        if agent_contact is None or not agent_contact.respond_io_id:
            return None
        identifier = str(agent_contact.respond_io_id)
        assert_outbound_enabled(identifier, db)
    except AppException as exc:
        if (getattr(exc, "detail", None) or {}).get("code") == "OUTBOUND_DISABLED":
            return identifier
        return None
    except Exception:  # noqa: BLE001 - the rows must still be written
        db.rollback()
        logger.warning("stock ask: allowed-to-send check failed", exc_info=True)
    return None


def _log_not_allowed(db: Session, ask: Any, turn_id: str, identifier: str) -> None:
    """One `skipped` integration log line per ask the gate held back."""
    try:
        _log(
            db,
            facts={"ask_id": ask.id, "turn_id": turn_id},
            identifier=identifier,
            status="skipped",
            request_payload={},
            error_message="not sent: contact not allowed to send",
        )
    except Exception:  # noqa: BLE001 - the ask row already says why
        db.rollback()
        logger.warning("stock ask %s: could not write the not-allowed log", ask.id)


def _record_outcome(db: Session, facts: dict[str, Any], *, sent: bool, reason: Optional[str]) -> None:
    """S5: the job's outcome on the ask row (`notified_agent`, or why not)."""
    ask_id = facts.get("ask_id")
    if not ask_id:
        return
    from app.models.stock_ask import StockAsk

    try:
        db.query(StockAsk).filter(StockAsk.id == ask_id).update(
            {"notified_agent": sent, "notify_skip_reason": None if sent else reason},
            synchronize_session=False,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - the send already happened or was logged
        db.rollback()
        logger.warning("stock ask %s: could not record the notification outcome", ask_id)


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _contact_label(contact: Any) -> str:
    """The contact's display name. It comes off their own WhatsApp profile, so control
    characters and line breaks are folded to one space and the length is capped before it
    reaches another person's message (security review, lane PR #1333)."""
    raw = (
        getattr(contact, "name", None)
        or " ".join(filter(None, [getattr(contact, "first_name", None), getattr(contact, "last_name", None)]))
        or getattr(contact, "phone_number", None)
        or "-"
    )
    return _CONTROL_CHARS.sub(" ", str(raw)).strip()[:100] or "-"


def _recipient(
    db: Session, contact_id: Optional[str], customer_id: Optional[str] = None
) -> tuple[Any, Any, Any, Optional[str]]:
    """(dealer contact, customer, agent's Respond contact, skip reason).

    `customer_id` is the customer the ask row was written against, in the turn's own
    company scope. When the job carries it, it is used as is: resolving the contact's
    customer again here, later and under another scope, could pick a different customer
    (security review, lane PR #1333)."""
    from app.models.access import RespondContact
    from app.models.order import Customer
    from app.models.sales_agent import SalesAgent
    from app.services.contact_customer_service import resolve_customer

    dealer = (
        db.query(RespondContact).filter(RespondContact.id == contact_id).first()
        if contact_id
        else None
    )
    if customer_id is None and contact_id:
        customer_id = resolve_customer(db, contact_id)
    customer = (
        db.query(Customer).filter(Customer.id == customer_id).first() if customer_id else None
    )
    if customer is None:
        return dealer, None, None, "no_customer"
    agent = (
        db.query(SalesAgent).filter(SalesAgent.id == customer.sales_agent_id).first()
        if customer.sales_agent_id
        else None
    )
    if agent is None:
        return dealer, customer, None, "no_sales_agent"
    agent_contact = (
        db.query(RespondContact).filter(RespondContact.id == agent.contact_id).first()
        if agent.contact_id
        else None
    )
    if agent_contact is None:
        return dealer, customer, None, "agent_has_no_contact"
    if not agent_contact.respond_io_id:
        return dealer, customer, agent_contact, "agent_contact_has_no_respond_id"
    return dealer, customer, agent_contact, None


def _log(
    db: Session,
    *,
    facts: dict[str, Any],
    identifier: str,
    status: str,
    request_payload: dict,
    status_code: Optional[int] = None,
    response_payload: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    from app.schemas.integration import IntegrationLogCreate
    from app.services.integration_service import IntegrationLogService

    IntegrationLogService(db).create_integration_log(
        IntegrationLogCreate(
            integration_channel="respond_io",
            # S5: the ask row, once there is one; a job enqueued before S5 names the turn.
            business_table="stock_asks" if facts.get("ask_id") else "chatbot_turns",
            business_id=facts.get("ask_id") or facts["turn_id"],
            external_reference=identifier,
            direction="outbound",
            endpoint=f"https://api.respond.io/v2/contact/id:{identifier}/message",
            http_method="POST",
            status=status,
            status_code=status_code,
            response_payload=response_payload,
            error_message=error_message,
        ),
        request_payload_dict=request_payload,
    )


def notify_salesman(db: Session, facts: dict[str, Any]) -> dict[str, Any]:
    """The job body: resolve the agent, send, log. Never raises.

    Returns `{"status": "sent", "sent_as": ...}`, `{"status": "skipped", "reason": ...}` or
    `{"status": "failed", "error": ...}`.
    """
    try:
        dealer, customer, agent_contact, reason = _recipient(
            db, facts.get("contact_id"), facts.get("customer_id")
        )
    except Exception as exc:  # noqa: BLE001 - a broken read is a failed attempt, not a crash
        logger.warning("stock ask %s: recipient lookup failed: %s", facts.get("turn_id"), exc)
        db.rollback()
        _record_outcome(db, facts, sent=False, reason="send_failed")
        return {"status": "failed", "error": str(exc)}
    if reason is not None:
        logger.warning(
            "stock ask %s (%s x %s): salesman not notified, %s",
            facts.get("turn_id"),
            facts.get("product_code"),
            facts.get("quantity"),
            reason,
        )
        _record_outcome(db, facts, sent=False, reason=reason)
        return {"status": "skipped", "reason": reason}

    name = facts.get("product_name")
    code = facts.get("product_code") or ""
    asked_at = facts.get("asked_at")
    moment = datetime.fromisoformat(asked_at) if asked_at else datetime.now(timezone.utc)
    ctx = {
        "outcome": outcome_phrase(
            facts.get("branch") or "",
            cap_unset=bool(facts.get("cap_unset")),
            category_name=facts.get("category_name"),
            available_qty=facts.get("available_qty"),
            quantity=facts.get("quantity"),
        ),
        "customer_name": customer.customer_name,
        "contact_name": _contact_label(dealer),
        "product": f"{code} - {name}" if name and name != code else code,
        "quantity": str(facts.get("quantity")),
        "asked_at": asked_at_label(moment),
    }
    text_ = default_text(ctx)
    identifier = str(agent_contact.respond_io_id)
    attempted: dict = {"message": {"type": "text", "text": text_}}
    try:
        result = send_text_or_template(
            db,
            identifier=identifier,
            text=text_,
            use_case=USE_CASE,
            context_vars=ctx,
            respond_contact_id=str(agent_contact.id),
        )
    except Exception as exc:  # noqa: BLE001 - every failure is a failed log row
        resp = getattr(exc, "response", None)
        code_ = getattr(resp, "status_code", None) if resp is not None else None
        body = None
        if resp is not None:
            try:
                body = (resp.text or "")[:50000]
            except Exception:  # noqa: BLE001
                body = None
        try:
            _log(
                db,
                facts=facts,
                identifier=identifier,
                status="failed",
                request_payload=getattr(exc, "request_payload", attempted),
                status_code=code_,
                response_payload=body,
                error_message=str(exc),
            )
        except Exception:  # noqa: BLE001
            logger.warning("stock ask %s: could not write the failed send log", facts.get("turn_id"))
            db.rollback()
        logger.warning("stock ask %s: salesman send failed: %s", facts.get("turn_id"), exc)
        _record_outcome(db, facts, sent=False, reason="send_failed")
        return {"status": "failed", "error": str(exc)}

    response = result.get("response")
    try:
        _log(
            db,
            facts=facts,
            identifier=identifier,
            status="success",
            request_payload=result.get("request_payload") or attempted,
            response_payload=str(response)[:50000] if response else None,
        )
    except Exception:  # noqa: BLE001 - the message went out; the row must still say so
        db.rollback()
        logger.warning("stock ask %s: could not write the success send log", facts.get("turn_id"))
    _record_outcome(db, facts, sent=True, reason=None)
    return {"status": "sent", "sent_as": result.get("sent_as")}


# --------------------------------------------------------------------------------------- #
# S5 / S6: the asks record, read and worked by the office and by the sales agent
# --------------------------------------------------------------------------------------- #


def _family_names_by_contact(db: Session, contact_ids: set[str]) -> dict[str, str]:
    """contact id -> the trading name of the customers it is linked to, for the contacts whose
    links are all ledgers of ONE shop (`ledger_family_key`, `app/services/ledger_family.py`,
    the rule the chatbot's narrower groups its roster by); a contact linked to two shops, or
    to nothing, is absent. Read under the caller's company scope, like the links themselves."""
    if not contact_ids:
        return {}
    from app.models.access import RespondContactCustomer
    from app.models.order import Customer
    from app.services.customer_group_service import family_overrides
    from app.services.ledger_family import customer_groups, ledger_family_key, ledger_family_label

    keys: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    rows = (
        db.query(RespondContactCustomer.contact_id, Customer.customer_name)
        .join(Customer, Customer.id == RespondContactCustomer.customer_id)
        .filter(RespondContactCustomer.contact_id.in_(contact_ids))
        .all()
    )
    # The office's customer groups win over the name rule (CUSTOMER-GROUP).
    with customer_groups(family_overrides(db)):
        for contact_id, name in rows:
            key = ledger_family_key(name or "")
            if not key:
                continue
            keys.setdefault(contact_id, set()).add(key)
            labels.setdefault(contact_id, ledger_family_label(name))
    return {cid: labels[cid] for cid, found in keys.items() if len(found) == 1}


def serialize(db: Session, rows: list[Any], *, with_agent: bool = False) -> list[Any]:
    """Rows as `StockAskResponse`: the contact, customer and product NAMED, never their ids.
    `with_agent` also names the customer's sales agent (`agent_code`), for the CRM manager view."""
    from app.models.access import RespondContact
    from app.models.order import Customer
    from app.models.product import Product
    from app.schemas.stock_ask import StockAskResponse

    rows = list(rows)
    contact_ids = {r.contact_id for r in rows if r.contact_id}
    customer_ids = {r.customer_id for r in rows if r.customer_id}
    product_ids = {r.product_id for r in rows if r.product_id}
    contact_rows = (
        {c.id: c for c in db.query(RespondContact).filter(RespondContact.id.in_(contact_ids))}
        if contact_ids
        else {}
    )
    contacts = {cid: _contact_label(c) for cid, c in contact_rows.items()}
    customers = (
        {
            c.id: c.customer_name
            for c in db.query(Customer.id, Customer.customer_name).filter(Customer.id.in_(customer_ids))
        }
        if customer_ids
        else {}
    )
    products = (
        {
            p.id: p.product_name
            for p in db.query(Product.id, Product.product_name).filter(Product.id.in_(product_ids))
        }
        if product_ids
        else {}
    )
    # ASKS-UX item 4 (owner, 30 Sep 2026, option a): an ask written against NO customer (its
    # contact links to several ledgers and no primary was chosen, `resolve_customer`) is named
    # after the TRADING NAME those ledgers share, so the card reads "HANLIM TRADING SDN BHD"
    # rather than the contact. Links to two different shops name nothing (the card falls back
    # to the contact). The family rule is the chatbot's, not a second one.
    family_names = _family_names_by_contact(db, {r.contact_id for r in rows if r.contact_id and not r.customer_id})
    done_user_ids = {r.done_by_user_id for r in rows if r.done_by_user_id}
    done_contact_ids = {r.done_by_contact_id for r in rows if r.done_by_contact_id} - set(contacts)
    from app.models.user import User

    user_names = (
        {u.id: u.name for u in db.query(User.id, User.name).filter(User.id.in_(done_user_ids))}
        if done_user_ids
        else {}
    )
    done_contacts = dict(contacts)
    if done_contact_ids:
        done_contacts.update(
            {c.id: _contact_label(c) for c in db.query(RespondContact).filter(RespondContact.id.in_(done_contact_ids))}
        )

    def _done_by(r: Any) -> Optional[str]:
        # "Who did it" is stored as ids; the reader gets a label (a user's name, else the contact's).
        if r.done_by_user_id and user_names.get(r.done_by_user_id):
            return user_names[r.done_by_user_id]
        if r.done_by_contact_id:
            return done_contacts.get(r.done_by_contact_id)
        return None

    agent_codes: dict[Any, str] = {}
    if with_agent and customer_ids:
        from app.models.order import Customer as _Customer
        from app.models.sales_agent import SalesAgent

        agent_codes = {
            cid: code
            for cid, code in db.query(_Customer.id, SalesAgent.sales_agent)
            .join(SalesAgent, SalesAgent.id == _owning_agent_column())
            .filter(_Customer.id.in_(customer_ids))
        }
    return [
        StockAskResponse(
            id=str(r.id),
            customer_name=customers.get(r.customer_id) if r.customer_id else family_names.get(r.contact_id),
            contact_name=contacts.get(r.contact_id),
            contact_phone=getattr(contact_rows.get(r.contact_id), "phone_number", None),
            product_code=r.product_code,
            product_name=products.get(r.product_id),
            quantity=r.quantity,
            branch=r.branch,
            answer_summary=r.answer_summary,
            notified_agent=bool(r.notified_agent),
            notify_skip_reason=r.notify_skip_reason,
            state=r.state,
            source=r.source or "live",
            note=r.note,
            created_at=r.created_at,
            updated_at=r.updated_at,
            done_at=r.done_at,
            done_by=_done_by(r),
            agent_code=agent_codes.get(r.customer_id),
        )
        for r in rows
    ]


def _page(query: Any, page: int, limit: int) -> tuple[list[Any], int]:
    from app.models.stock_ask import StockAsk

    total = query.count()
    rows = (
        query.order_by(StockAsk.created_at.desc(), StockAsk.id)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return rows, total


def _apply_update(
    db: Session,
    ask: Any,
    data: dict[str, Any],
    *,
    actor_user_id: Optional[str] = None,
    actor_contact_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Any:
    """The ONE place `done_at` and the two actor ids are written (plan 3.1): a transition to
    done stamps all three, a transition to open clears all three, a repeat of the same state or
    a note-only change touches none. Ids, never a name: `serialize` resolves the label."""
    new_state = data.get("state")
    if new_state is not None and new_state != ask.state:
        if new_state == "done":
            ask.done_at = now or datetime.utcnow()
            ask.done_by_user_id = actor_user_id
            ask.done_by_contact_id = actor_contact_id
        else:
            ask.done_at = None
            ask.done_by_user_id = None
            ask.done_by_contact_id = None
        ask.state = new_state
    if "note" in data:
        note = (data["note"] or "").strip()
        ask.note = note or None
    db.commit()
    db.refresh(ask)
    return ask


def _customer_or_404(db: Session, customer_id: str) -> Any:
    from app.models.order import Customer
    from app.services.error_handler import handle_not_found

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        raise handle_not_found("Customer", customer_id)
    return customer


def list_for_customer(db: Session, customer_id: str, *, page: int, limit: int) -> dict[str, Any]:
    """The CRM Asks tab: one customer's asks, newest first. A customer outside the caller's
    company scope is a 404, so another company's asks never show."""
    from app.models.stock_ask import StockAsk

    _customer_or_404(db, customer_id)
    rows, total = _page(db.query(StockAsk).filter(StockAsk.customer_id == customer_id), page, limit)
    return {
        "data": serialize(db, rows),
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }


def update_for_customer(
    db: Session, customer_id: str, ask_id: str, data: dict[str, Any], *, actor_user_id: str
) -> Any:
    from app.models.stock_ask import StockAsk
    from app.services.error_handler import handle_not_found

    _customer_or_404(db, customer_id)
    ask = (
        db.query(StockAsk)
        .filter(StockAsk.id == ask_id, StockAsk.customer_id == customer_id)
        .first()
    )
    if ask is None:
        raise handle_not_found("Stock ask", ask_id)
    return serialize(db, [_apply_update(db, ask, data, actor_user_id=actor_user_id)])[0]


def _owning_agent_column(entity: Any = None) -> Any:
    """The customer-to-agent relation, in ONE place: today `customers.sales_agent_id`. The
    agent scope, `serialize(with_agent=True)` and `agent_counts` all read it from here, so the
    swap to CONTACT-CUSTOMERS' relation (#1366, plan section 4) is one edit. `entity` is the
    `Customer` alias to read it from (default: `Customer`)."""
    from app.models.order import Customer

    return (entity or Customer).sales_agent_id


def _agent_scope(db: Session, agent_id: Optional[str | Iterable[str]]) -> Any:
    """S6 (R9): asks of customers assigned to this agent NOW (`customers.sales_agent_id`).
    `agent_id=None` is every agent's asks (the CRM manager's "All agents"); a collection of ids
    is those agents' asks (a team leader's team). The ONE place the agent -> customers relation
    lives (plan section 4).

    AC-ST105b (#1366): an ask with NO customer belongs to the agents handling a customer its
    contact is linked to (`respond_contact_customers`), read every time, nothing stored on the
    ask. A customer-less ask whose contact links to no handled customer belongs to nobody."""
    from sqlalchemy import and_, exists, or_
    from sqlalchemy.orm import aliased

    from app.models.access import RespondContactCustomer
    from app.models.order import Customer
    from app.models.stock_ask import StockAsk

    linked = aliased(Customer)
    owner = _owning_agent_column()
    linked_owner = _owning_agent_column(linked)

    def matches(column: Any) -> Any:
        if agent_id is None:
            return column.isnot(None)
        if isinstance(agent_id, str):
            return column == agent_id
        return column.in_(list(agent_id))

    # Every joined row is tied to the ask's own company: a link or a customer of another company
    # never puts an ask on an agent's list (security review, AC-ST105c).
    via_contact = and_(
        StockAsk.customer_id.is_(None),
        exists()
        .where(RespondContactCustomer.contact_id == StockAsk.contact_id)
        .where(RespondContactCustomer.company_id == StockAsk.company_id)
        .where(linked.id == RespondContactCustomer.customer_id)
        .where(linked.company_id == StockAsk.company_id)
        .where(matches(linked_owner)),
    )
    return (
        db.query(StockAsk)
        .outerjoin(
            Customer,
            and_(Customer.id == StockAsk.customer_id, Customer.company_id == StockAsk.company_id),
        )
        .filter(or_(matches(owner), via_contact))
    )


def list_for_agent(
    db: Session,
    agent_id: str,
    *,
    page: int,
    limit: int,
    q: Optional[str] = None,
    state: Optional[str] = None,
) -> dict[str, Any]:
    """The portal's Customer asks page, newest first. `q` matches the customer name or
    code, or the product code."""
    from sqlalchemy import or_

    from app.models.order import Customer
    from app.models.stock_ask import StockAsk

    query = _agent_scope(db, agent_id)
    if state:
        query = query.filter(StockAsk.state == state)
    term = (q or "").strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(
            or_(
                Customer.customer_name.ilike(like, escape="\\"),
                Customer.customer_code.ilike(like, escape="\\"),
                StockAsk.product_code.ilike(like, escape="\\"),
            )
        )
    rows, total = _page(query, page, limit)
    return {
        "data": serialize(db, rows),
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }


def update_for_agent(
    db: Session,
    agent_id: str,
    ask_id: str,
    data: dict[str, Any],
    *,
    actor_contact_id: str,
    actor_user_id: Optional[str] = None,
) -> Any:
    from app.models.stock_ask import StockAsk
    from app.services.error_handler import handle_not_found

    ask = _agent_scope(db, agent_id).filter(StockAsk.id == ask_id).first()
    if ask is None:
        raise handle_not_found("Stock ask", ask_id)
    return serialize(
        db,
        [_apply_update(db, ask, data, actor_contact_id=actor_contact_id, actor_user_id=actor_user_id)],
    )[0]


def update_for_sales(
    db: Session, ask_id: str, data: dict[str, Any], *, agent_id: Optional[str | Iterable[str]], actor_user_id: str
) -> Any:
    """The CRM to-do's PATCH: `agent_id` is the caller's pickable agents (self, a led team), or None for a
    caller with view_all (any ask that belongs to some agent's customer). Out of scope is a 404."""
    from app.models.stock_ask import StockAsk
    from app.services.error_handler import handle_not_found

    ask = _agent_scope(db, agent_id).filter(StockAsk.id == ask_id).first()
    if ask is None:
        raise handle_not_found("Stock ask", ask_id)
    return serialize(db, [_apply_update(db, ask, data, actor_user_id=actor_user_id)], with_agent=agent_id is None)[0]


def today_start_utc(now: datetime) -> datetime:
    """Malaysia midnight of `now`, as a naive UTC datetime. The server owns the day boundary."""
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    local = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local - timedelta(hours=8)


def todo_for_agent(
    db: Session, agent_id: Optional[str | Iterable[str]], *, now: Optional[datetime] = None, with_agent: bool = False
) -> dict[str, Any]:
    """The to-do read (plan 3.2): open asks oldest first (capped), and what was cleared today.
    `agent_id=None` is every agent (view_all). One Open list (CUSTOMER-ASKS-REFER-ONLY);
    `today_start` is the Done today window's start."""
    from app.models.stock_ask import StockAsk

    start = today_start_utc(now or datetime.utcnow())
    open_rows = (
        _agent_scope(db, agent_id)
        .filter(StockAsk.state == "open")
        .order_by(StockAsk.created_at.asc(), StockAsk.id.asc())
        .limit(TODO_CAP + 1)
        .all()
    )
    truncated = len(open_rows) > TODO_CAP
    done_rows = (
        _agent_scope(db, agent_id)
        .filter(StockAsk.state == "done", StockAsk.done_at >= start)
        .order_by(StockAsk.done_at.desc(), StockAsk.id.asc())
        .all()
    )
    return {
        "today_start": start,
        "open": serialize(db, open_rows[:TODO_CAP], with_agent=with_agent),
        "done_today": serialize(db, done_rows, with_agent=with_agent),
        "truncated": truncated,
    }


def agent_counts(
    db: Session,
    *,
    agent_ids: Optional[Iterable[str]] = None,
    include_idle: bool = False,
) -> list[dict[str, Any]]:
    """The Agent select's lines, counted over the SAME rows the to-do shows: each agent's
    `_agent_scope` (so a customer-less ask reached through a contact link counts for every agent
    it belongs to), open, every branch. One Open count (CUSTOMER-ASKS-REFER-ONLY, 1 Oct 2026:
    the to-do has no "needs attention" split any more). `agent_ids` None is every agent. Agents
    with no open ask are left out, unless `include_idle` (a team leader sees every current
    member, 0 allowed). One small query per agent: an agent list is tens."""
    from sqlalchemy import func

    from app.models.sales_agent import SalesAgent
    from app.models.stock_ask import StockAsk

    query = db.query(SalesAgent)
    if agent_ids is not None:
        query = query.filter(SalesAgent.id.in_(list(agent_ids)))
    out = []
    for agent in query.all():
        opened = (
            _agent_scope(db, agent.id)
            .filter(StockAsk.state == "open")
            .with_entities(func.count(StockAsk.id))
            .scalar()
        )
        if not opened and not include_idle:
            continue
        out.append(
            {
                "agent_id": str(agent.id),
                "code": agent.sales_agent,
                "name": agent.person_label or agent.sales_agent,
                "open": int(opened or 0),
            }
        )
    return sorted(out, key=lambda r: r["code"])


#: The window either side of the ask, and the row caps (plan 3.6).
CONVERSATION_MINUTES = 30
CONVERSATION_CAP = 60
CONVERSATION_DAY_CAP = 200


def get_ask_in_scope(db: Session, agent_id: Optional[str | Iterable[str]], ask_id: str) -> Any:
    """One ask inside an agent scope (`_agent_scope`), or a 404."""
    from app.models.stock_ask import StockAsk
    from app.services.error_handler import handle_not_found

    ask = _agent_scope(db, agent_id).filter(StockAsk.id == ask_id).first()
    if ask is None:
        raise handle_not_found("Stock ask", ask_id)
    return ask


def conversation_for_ask(db: Session, ask: Any, *, whole_day: bool = False) -> dict[str, Any]:
    """The chat around an ask (plan 3.6): the contact's `chat_histories` rows within 30 minutes
    either side of `created_at` (at most 60), or the ask's Malaysia calendar day (at most 200).

    `chat_histories.contact_id` holds the Respond.io contact id, so the ask's contact
    (`respond_contacts.id`) is resolved to its `respond_io_id` first. Only id, direction, text and
    time leave here. When the cap bites, the rows nearest the ask are kept, oldest first.
    `ask_message_id` is the outgoing row after the ask that carries its answer line, else the
    nearest outgoing row after it, else None. `contact_id` (the `respond_contacts.id`) is for the
    CRM's "Open in Conversations" link only, and is left out of an empty answer (no chat to open)."""
    from sqlalchemy import func

    from app.models.access import RespondContact
    from app.models.chat_history import ChatHistory

    empty: dict[str, Any] = {"messages": [], "ask_message_id": None, "ask_message_ref": None}
    if not ask.contact_id:
        return empty
    respond_io_id = db.query(RespondContact.respond_io_id).filter(RespondContact.id == ask.contact_id).scalar()
    if not respond_io_id:
        return empty

    created = ask.created_at
    if whole_day:
        start = today_start_utc(created)
        end, cap = start + timedelta(days=1), CONVERSATION_DAY_CAP
    else:
        start = created - timedelta(minutes=CONVERSATION_MINUTES)
        end, cap = created + timedelta(minutes=CONVERSATION_MINUTES), CONVERSATION_CAP
    in_window = [
        ChatHistory.contact_id == respond_io_id,
        ChatHistory.sent_at >= start,
        ChatHistory.sent_at < end if whole_day else ChatHistory.sent_at <= end,
    ]
    rows = (
        db.query(ChatHistory.id, ChatHistory.type, ChatHistory.message, ChatHistory.sent_at, ChatHistory.message_id)
        .filter(*in_window)
        .order_by(func.abs(func.extract("epoch", ChatHistory.sent_at - created)), ChatHistory.id)
        .limit(cap)
        .all()
    )
    rows.sort(key=lambda r: (r.sent_at, r.id))
    messages = [
        {
            "id": r.id,
            "direction": "out" if r.type == "outgoing" else "in",
            "text": r.message,
            "at": r.sent_at,
        }
        for r in rows
    ]

    # Chosen among the RETURNED messages only, so it is never an id the caller cannot see. The
    # search starts a minute before `created_at`: the ask row and the chat row are stamped by
    # different clocks.
    floor = created - timedelta(seconds=60)
    after = [m for m in messages if m["direction"] == "out" and m["at"] >= floor]
    ask_message_id = next(
        (m["id"] for m in after if ask.answer_summary and ask.answer_summary in (m["text"] or "")), None
    )
    if ask_message_id is None and after:
        ask_message_id = after[0]["id"]
    # ASKS-UX: the shared thread keys its bubbles by the Respond message id (`chat_histories.
    # message_id`), not by the row id, so the tagged row's Respond id rides along for the
    # highlight and the jump. None when the row carries none (nothing to jump to).
    ref = next((r.message_id for r in rows if r.id == ask_message_id), None) if ask_message_id is not None else None
    return {
        "messages": messages,
        "ask_message_id": ask_message_id,
        "ask_message_ref": str(ref) if ref else None,
        "contact_id": ask.contact_id,
    }


def _thread_contact_for_ask(db: Session, ask: Any):
    """The ask's contact as the shared thread's descriptor, or None (no contact, or a contact
    with no Respond id)."""
    from app.models.access import RespondContact
    from app.services.conversation_thread_service import thread_contact_for

    if not ask.contact_id:
        return None
    contact = db.query(RespondContact).filter(RespondContact.id == ask.contact_id).first()
    return thread_contact_for(contact)


#: What a portal reader gets of each thread item (security review, 30 Sep 2026): the message,
#: its direction, clock and receipts, the quote it answers and its lane. Never the staff member
#: behind a send (`sender.userId` / `sender.name`, resolved by `_attach_sender_names`), nor the
#: transport ids (`channelMessageId`, `contactId`, `channelId`).
_PORTAL_ITEM_KEYS = ("messageId", "traffic", "message", "status", "replyTo", "source")


def portal_thread_item(item: dict[str, Any]) -> dict[str, Any]:
    """One thread item as the portal may see it: `sender` reduced to its `source`."""
    out = {k: item[k] for k in _PORTAL_ITEM_KEYS if k in item}
    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    out["sender"] = {"source": sender.get("source")}
    return out


def conversation_page_for_ask(
    db: Session,
    ask: Any,
    *,
    before: Optional[str] = None,
    after: Optional[str] = None,
    around: Optional[str] = None,
    limit: int = 50,
    portal: bool = False,
) -> dict[str, Any]:
    """One scroll-back window of the ask's contact thread (ASKS-UX item 3): the SAME core and
    shape as `GET .../conversation-sla-tracking/{id}/conversation/page`, with the same
    per-contact Respond client wiring (`sla_service._thread_page_for_contact`). The caller has
    already put the ask in scope. No thread (no contact, no Respond id) answers the empty page,
    not a 404: the ask exists and the panel still has to render. A cursor that is not a message
    id raises ValueError (the core's rule); the route answers 422. `portal=True` projects each
    item through `portal_thread_item`."""
    from app.services import conversation_thread_service as thread_service
    from app.services.integration_service import RespondClient

    contact = _thread_contact_for_ask(db, ask)
    if contact is None:
        return thread_service.empty_page(limit=limit, error="No Respond.io contact linked")
    page = thread_service.fetch_thread_page(
        db,
        contact,
        before=before,
        after=after,
        around=around,
        limit=limit,
        client=RespondClient.for_identifier(db, contact.respond_io_id),
    )
    if portal:
        page["items"] = [portal_thread_item(i) for i in page["items"]]
    return page


def conversation_search_for_ask(db: Session, ask: Any, *, q: str, limit: int = 100) -> dict[str, Any]:
    """In-thread search over the ask's contact thread, same shape as the ticket-keyed twin."""
    from app.services import conversation_thread_service as thread_service

    contact = _thread_contact_for_ask(db, ask)
    if contact is None:
        return thread_service.empty_search(q=q, error="No Respond.io contact linked")
    return thread_service.search_thread(db, contact, q=q, limit=limit)
