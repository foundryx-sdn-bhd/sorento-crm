"""Customer groups: one company's customer, made of several ledgers (`customers` rows).

The CRUD and membership the Customer Groups page uses, plus `family_overrides`, the one
loader the chatbot turn reads (`app.services.ledger_family.customer_groups`).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Customer, CustomerGroup
from app.models.sales_agent import SalesAgent
from app.services.company_scope import pending_company_id
from app.services.error_handler import handle_conflict, handle_not_found, handle_unprocessable
from app.services.ledger_family import normalise_customer_name
from app.services.scm.sales_agent_service import derive_person_label

_DUPLICATE = "A group with this name already exists"


class CustomerGroupService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ reads

    def _stats(self, group_ids: list[str]) -> dict[str, tuple[int, list[int]]]:
        """`{group id: (ledger count, sorted distinct account levels)}` for these groups."""
        if not group_ids:
            return {}
        rows = (
            self.db.query(Customer.customer_group_id, Customer.account_level, func.count(Customer.id))
            .filter(Customer.customer_group_id.in_(group_ids))
            .group_by(Customer.customer_group_id, Customer.account_level)
            .all()
        )
        out: dict[str, tuple[int, list[int]]] = {}
        for gid, level, n in rows:
            count, levels = out.get(gid, (0, []))
            out[gid] = (count + n, levels + ([level] if level is not None else []))
        return {gid: (count, sorted(levels)) for gid, (count, levels) in out.items()}

    @staticmethod
    def _person_key_sql():
        """SQL twin of `derive_person_label` + the label-first rule, one key per agent row."""
        code = func.regexp_replace(
            func.upper(SalesAgent.sales_agent), "^[[:space:]]+|[[:space:]]+$", "", "g"
        )
        derived = func.nullif(
            func.rtrim(
                func.regexp_replace(code, "[[:space:]-]+(I|II|III|IV|V|VI|VII|VIII|IX|X)$", ""),
                " -",
            ),
            "",
        )
        typed = func.nullif(
            func.regexp_replace(SalesAgent.person_label, "^[[:space:]]+|[[:space:]]+$", "", "g"), ""
        )
        return func.nullif(func.lower(func.coalesce(typed, derived)), "")

    def _agents(self, group_ids: list[str]) -> dict[str, tuple[Optional[str], bool]]:
        """`{group id: (agent label, mixed)}`: one person over the ledgers that carry an agent
        shows that person; two or more are mixed with no label. Unassigned ledgers are ignored."""
        if not group_ids:
            return {}
        rows = (
            self.db.query(Customer.customer_group_id, SalesAgent.person_label, SalesAgent.sales_agent)
            .join(SalesAgent, SalesAgent.id == Customer.sales_agent_id)
            .filter(Customer.customer_group_id.in_(group_ids))
            .order_by(SalesAgent.sales_agent, SalesAgent.id)
            .all()
        )
        people: dict[str, dict[str, str]] = {}
        for gid, person_label, code in rows:
            typed = (person_label or "").strip()
            label = typed or derive_person_label(code)
            if not label:
                continue
            people.setdefault(gid, {}).setdefault(label.lower(), label)
        return {
            gid: ((next(iter(keys.values())), False) if len(keys) == 1 else (None, True))
            for gid, keys in people.items()
        }

    def _shape(self, groups: list[CustomerGroup]) -> list[dict]:
        stats = self._stats([g.id for g in groups])
        agents = self._agents([g.id for g in groups])
        shaped = []
        for g in groups:
            count, levels = stats.get(g.id, (0, []))
            agent_label, agent_mixed = agents.get(g.id, (None, False))
            shaped.append(
                {
                    "id": g.id,
                    "name": g.name,
                    "ledger_count": count,
                    "account_levels": levels,
                    "sales_agent_label": agent_label,
                    "sales_agent_mixed": agent_mixed,
                    "created_at": g.created_at,
                    "updated_at": g.updated_at,
                }
            )
        return shaped

    def _counts_subquery(self):
        return (
            self.db.query(Customer.customer_group_id.label("gid"), func.count(Customer.id).label("n"))
            .filter(Customer.customer_group_id.isnot(None))
            .group_by(Customer.customer_group_id)
            .subquery()
        )

    def list_groups(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort: Optional[str] = None,
        dir: str = "asc",
        agent_mixed: bool = False,
    ):
        q = self.db.query(CustomerGroup)
        if query and query.strip():
            q = q.filter(CustomerGroup.name.ilike(f"%{query.strip()}%"))
        if agent_mixed:
            mixed = (
                self.db.query(Customer.customer_group_id)
                .join(SalesAgent, SalesAgent.id == Customer.sales_agent_id)
                .join(CustomerGroup, CustomerGroup.id == Customer.customer_group_id)
                .filter(Customer.company_id == CustomerGroup.company_id)
                .group_by(Customer.customer_group_id)
                .having(func.count(func.distinct(self._person_key_sql())) > 1)
            )
            q = q.filter(CustomerGroup.id.in_(mixed))
        total = q.count()
        descending = dir == "desc"
        if sort == "ledger_count":
            counts = self._counts_subquery()
            column = func.coalesce(counts.c.n, 0)
            q = q.outerjoin(counts, counts.c.gid == CustomerGroup.id)
        elif sort == "updated_at":
            column = CustomerGroup.updated_at
        else:
            column = func.lower(CustomerGroup.name)
        q = q.order_by(column.desc() if descending else column.asc(), CustomerGroup.id.asc())
        groups = q.offset((page - 1) * limit).limit(limit).all()
        return {
            "data": self._shape(groups),
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }

    def select(self, query: Optional[str] = None, limit: int = 50) -> list[dict]:
        q = self.db.query(CustomerGroup)
        if query and query.strip():
            q = q.filter(CustomerGroup.name.ilike(f"%{query.strip()}%"))
        groups = q.order_by(func.lower(CustomerGroup.name), CustomerGroup.id).limit(limit).all()
        stats = self._stats([g.id for g in groups])
        return [
            {"id": g.id, "name": g.name, "ledger_count": stats.get(g.id, (0, []))[0]} for g in groups
        ]

    def get_group(self, group_id: str) -> CustomerGroup:
        """The group, or 404 when it does not exist OR the caller's scope hides it."""
        try:
            uuid.UUID(str(group_id))
        except (ValueError, AttributeError, TypeError):
            raise handle_not_found("Customer group", group_id)
        group = self.db.query(CustomerGroup).filter(CustomerGroup.id == group_id).first()
        if group is None:
            raise handle_not_found("Customer group", group_id)
        return group

    def get_shaped(self, group_id: str) -> dict:
        return self._shape([self.get_group(group_id)])[0]

    # ----------------------------------------------------------------- writes

    @staticmethod
    def _clean(name: Optional[str]) -> str:
        cleaned = " ".join((name or "").split())
        if not cleaned:
            raise handle_unprocessable("Group name is required")
        return cleaned

    def _assert_name_free(self, name: str, company_id: Optional[str], exclude_id: Optional[str] = None):
        q = self.db.query(CustomerGroup.id).filter(func.lower(CustomerGroup.name) == name.lower())
        if company_id:
            q = q.filter(CustomerGroup.company_id == company_id)
        if exclude_id:
            q = q.filter(CustomerGroup.id != exclude_id)
        if q.first() is not None:
            raise handle_conflict(_DUPLICATE)

    def _commit_name(self) -> None:
        """Commit; a race past `_assert_name_free` hits the unique index and reads as the 409."""
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise handle_conflict(_DUPLICATE)

    def create_group(self, name: str) -> dict:
        cleaned = self._clean(name)
        group = CustomerGroup(name=cleaned)
        self._assert_name_free(cleaned, pending_company_id(group))
        self.db.add(group)
        self._commit_name()
        self.db.refresh(group)
        return self._shape([group])[0]

    def rename_group(self, group_id: str, name: str) -> dict:
        group = self.get_group(group_id)
        cleaned = self._clean(name)
        self._assert_name_free(cleaned, group.company_id, exclude_id=group.id)
        group.name = cleaned
        self._commit_name()
        self.db.refresh(group)
        return self._shape([group])[0]

    def delete_group(self, group_id: str) -> bool:
        """Hard delete; the FK's ON DELETE SET NULL leaves the ledgers ungrouped."""
        group = self.get_group(group_id)
        self.db.delete(group)
        self.db.commit()
        return True

    def assign_customers(self, group_id: str, customer_ids: list[str]) -> list[Customer]:
        """Move ledgers into the group (from another group too). All or nothing: an
        unknown or hidden id is 404, a customer of another company 422, nothing written."""
        group = self.get_group(group_id)
        customers: list[Customer] = []
        for customer_id in dict.fromkeys(customer_ids):
            try:
                uuid.UUID(str(customer_id))
            except (ValueError, AttributeError, TypeError):
                raise handle_not_found("Customer", customer_id)
            customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
            if customer is None:
                raise handle_not_found("Customer", customer_id)
            customers.append(customer)
        for customer in customers:
            if str(customer.company_id) != str(group.company_id):
                raise handle_unprocessable("A customer belongs to another company than this group")
        for customer in customers:
            customer.customer_group_id = group.id
        self.db.commit()
        return customers

    def remove_customer(self, customer_id: str, group_id: Optional[str]) -> bool:
        """Clear the customer's group, but only while it is still `group_id` (the removal is
        parked for a few seconds; a ledger moved meanwhile belongs to someone else now)."""
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise handle_not_found("Customer", customer_id)
        if str(customer.customer_group_id or "") != str(group_id or ""):
            raise handle_conflict("This customer has moved to another group.")
        customer.customer_group_id = None
        self.db.commit()
        return True


def family_overrides(db: Session) -> dict[str, str]:
    """`{normalised customer name: group name}` for the chatbot turn, in the caller's scope.

    A name whose rows do not all sit in one group (two groups, or a group and none) is
    left out, so it falls back to the name rule instead of guessing a side.
    """
    rows = (
        db.query(Customer.customer_name, CustomerGroup.name)
        .outerjoin(CustomerGroup, CustomerGroup.id == Customer.customer_group_id)
        .all()
    )
    seen: dict[str, Optional[str]] = {}
    clashing: set[str] = set()
    for customer_name, group_name in rows:
        key = normalise_customer_name(customer_name)
        if seen.setdefault(key, group_name) != group_name:
            clashing.add(key)
    return {k: v for k, v in seen.items() if v is not None and k not in clashing}
