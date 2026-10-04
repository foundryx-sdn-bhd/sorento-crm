"""Who a WhatsApp contact is scoped to: the customers CS has linked it to.

One function for every caller (the chatbot lane, the order routes, the top selling
report), so "is this contact scoped" has one answer. Core code: it never imports the
chatbot package (tests/chatbot/test_import_boundary.py).

`contact_id` is the INTERNAL `respond_contacts.id`; a caller holding a Respond.io id
resolves it first (`field_access.resolve_contact_with_null_workspace_fallback`).

PLAN-chatbot-customer-scope-29sep.md, D1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

#: The office tier's access type names ("Sorento Office"), matched the way the
#: chatbot's `tier_gate.parse_level` reads them. Restated here, not imported.
_OFFICE_ACCESS_TYPE_RE = re.compile(r"^(sorento|cabana|mocha) office\Z")


def is_office_staff(db: Session, contact_id: str) -> bool:
    """True when the contact holds at least one ACTIVE office access type
    (`_OFFICE_ACCESS_TYPE_RE`, e.g. "Sorento Office"), whatever other types it holds."""
    from app.models.access import ContactAccessType, respond_contact_access_types

    held = (
        db.query(ContactAccessType.name)
        .join(
            respond_contact_access_types,
            respond_contact_access_types.c.access_type_code == ContactAccessType.code,
        )
        .filter(
            respond_contact_access_types.c.contact_id == contact_id,
            ContactAccessType.is_active.is_(True),
        )
        .all()
    )
    return any(_OFFICE_ACCESS_TYPE_RE.match(" ".join((name or "").split()).lower()) for (name,) in held)


@dataclass(frozen=True)
class ContactCustomerScope:
    linked: tuple[tuple[str, str, str], ...]  # (customer_id, customer_name, customer_code), link order
    staff: bool  # an ACTIVE office access type
    # customer_id -> `customers.account_level` (None = no level set). A link missing here is
    # at no level at all.
    levels: dict[str, int | None] = field(default_factory=dict)

    @property
    def enforced(self) -> bool:
        return bool(self.linked) and not self.staff

    @property
    def customer_ids(self) -> list[str]:
        return [cid for cid, _name, _code in self.linked]

    @property
    def names(self) -> list[str]:
        return [name for _cid, name, _code in self.linked]

    def match_words(self, words: list[str], accounts: list[int | None] | None = None) -> list[str] | None:
        """The linked customer ids the words name (exact customer code, or a
        case-insensitive substring of the name), in link order; None when ANY word
        names none of them (the caller refuses). `accounts` is parallel to `words`: a
        word with an account keeps only the links at that Account level."""
        matched: list[str] = []
        for index, word in enumerate(words):
            account = accounts[index] if accounts and index < len(accounts) else None
            needle = " ".join((word or "").split()).lower()
            hits = [
                cid
                for cid, name, code in self.linked
                if needle
                and (needle == (code or "").strip().lower() or needle in (name or "").lower())
                and (account is None or self.levels.get(cid) == account)
            ]
            if not hits:
                return None
            matched.extend(h for h in hits if h not in matched)
        return [cid for cid in self.customer_ids if cid in matched]


def contact_customer_scope(db: Session, contact_id: str) -> ContactCustomerScope:
    """The contact's linked customers (every company, link order) and its staff flag.

    The link read runs with company scope OFF (the sales analysis precedent, security
    review B1): under an API-key request's scope a NULL-workspace contact's row is
    hidden, and a hidden link would let a scoped contact through."""
    from app.models.access import RespondContactCustomer
    from app.models.base import company_scope
    from app.models.order import Customer

    with company_scope(db, None):
        rows = (
            db.query(RespondContactCustomer.customer_id)
            .filter(RespondContactCustomer.contact_id == str(contact_id))
            .order_by(RespondContactCustomer.created_at)
            .all()
        )
        ids: list[str] = []
        for (cid,) in rows:
            if str(cid) not in ids:
                ids.append(str(cid))
        rows = (
            db.query(Customer.id, Customer.customer_name, Customer.customer_code, Customer.account_level)
            .filter(Customer.id.in_(ids))
            .all()
        ) if ids else []
        info = {str(i): (n or "", c or "") for i, n, c, _lvl in rows}
        levels = {str(i): lvl for i, _n, _c, lvl in rows}
    linked = tuple((i, *info.get(i, ("", ""))) for i in ids)
    return ContactCustomerScope(linked=linked, staff=is_office_staff(db, str(contact_id)), levels=levels)


def refusal_line(scope: ContactCustomerScope) -> str:
    return _refusal_for([n for n in scope.names if n])


def refusal_line_for(scope: ContactCustomerScope, ids: list[str]) -> str:
    """The refusal line naming ONLY the linked ledgers in `ids`, in link order."""
    return _refusal_for([name for cid, name, _code in scope.linked if cid in ids and name])


def _refusal_for(names: list[str]) -> str:
    # Owner rule (2 Oct 2026): a customer company is named by its group name only.
    from app.services.ledger_family import group_names

    names = group_names(names)
    if len(names) <= 1:
        joined = names[0] if names else "your own account"
    else:
        joined = ", ".join(names[:-1]) + " and " + names[-1]
    stop = "" if joined.endswith(".") else "."  # "SOON HENG HARDWARE CO.SDN.BHD." ends its own sentence
    return f"Sorry, that isn't under your account. I can only check on {joined}{stop}"
