"""DELIVERY-DEBTOR-GROUP red tests (owner, 8 Oct 2026).

PLAN ``documentation/plans/chatbot/PLAN-delivery-debtor-group-08oct.md``.

"check today delivery to hanlim" offered two options: the customer, and the free-text debtor
name one DO carried ("<NAME> (QUARTZ & ONYX)", typed at DO level). That DO's debtor code is
the same customer, and the customer sits in one customer group, so there is nothing to choose.

Cause: the resolver's DO debtor-name probes (`entity_resolver._probe_customer_debtor_name`,
`_prefix_probe_customer_debtor_name`) named each hit by the DO's own `debtor_name` and linked
it to a customer by NAME only. The gate's display name and family key then read the DO-level
name, which the customer-group map (exact customer names) does not hold, so it became its own
family.

Contract under test: a DO debtor-name hit is linked to its customer by the explicit keys first
(the DO's `customer_id`, then its debtor code in the DO's own company), and carries that
customer's master name as `display.customer_name`. A DO with no link at all keeps `uuid=None`
(never guessed by name similarity). Fake names only.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.order import Customer, Order
from app.services import customer_identity
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZDDG"
MASTER = f"{MARKER} TRADING SDN BHD"
DO_NAME = f"{MARKER} TRADING SDN BHD (QUARTZ & ONYX)"
TOKEN = f"{MARKER} TRADING"


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _customer(db, name: str = MASTER, *, company_id: str = DEFAULT_COMPANY_ID, group_id: str | None = None,
              id: str | None = None):
    row = Customer(
        id=id or str(uuid.uuid4()),
        customer_code=unique_code(f"300-{MARKER}")[:30],
        customer_name=name,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    if group_id:
        db.execute(text("UPDATE customers SET customer_group_id = :g WHERE id = :c"), {"g": group_id, "c": row.id})
    return row


def _group(db, name: str = MASTER) -> str:
    gid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
            "VALUES (:i, :c, :n, now(), now())"
        ),
        {"i": gid, "c": DEFAULT_COMPANY_ID, "n": name},
    )
    return gid


def _do(db, name: str, *, code: str | None = None, customer_id: str | None = None,
        company_id: str = DEFAULT_COMPANY_ID) -> None:
    db.add(
        Order(
            order_number=unique_code(f"{MARKER}DO")[:30],
            debtor_name=name,
            debtor_code=code,
            customer_id=customer_id,
            company_id=company_id,
        )
    )
    db.flush()


def _hits_by_name(hits) -> dict[str, tuple[str | None, str | None]]:
    return {h.canonical_code: (h.uuid, h.display.get("customer_name")) for h in hits}


# ============================================================ resolver: explicit link


class TestDebtorNameProbeLinksByExplicitKey:
    def test_the_owners_case_a_do_level_name_links_to_its_customer_by_customer_id(self, db):
        """The DO carries the customer FK and the customer's code, but its own debtor name."""
        from app.services.entity_resolver import _probe_customer_debtor_name

        cust = _customer(db)
        _do(db, MASTER, code=cust.customer_code, customer_id=cust.id)
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)

        hits = _probe_customer_debtor_name(db, [TOKEN])[TOKEN]

        assert _hits_by_name(hits) == {
            MASTER: (cust.id, MASTER),
            DO_NAME: (cust.id, MASTER),
        }

    def test_exact_tier_reads_the_customer_name_not_the_do_name(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        cust = _customer(db)
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (cust.id, MASTER)}

    def test_a_do_with_only_the_debtor_code_links_by_code(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        cust = _customer(db)
        _do(db, DO_NAME, code=f"  {cust.customer_code.lower()} ")

        hits = _probe_customer_debtor_name(db, [TOKEN])[TOKEN]

        assert _hits_by_name(hits) == {DO_NAME: (cust.id, MASTER)}

    def test_the_customer_fk_wins_over_a_name_that_matches_another_customer(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        owner = _customer(db)
        _customer(db, DO_NAME)  # a different customer that happens to carry the DO's name
        _do(db, DO_NAME, code=owner.customer_code, customer_id=owner.id)

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (owner.id, MASTER)}

    def test_a_do_with_no_link_stays_unlinked_never_guessed(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        _customer(db)
        _do(db, DO_NAME, code="300-ZZDDG-NOSUCH")

        hits = _probe_customer_debtor_name(db, [TOKEN])[TOKEN]

        assert _hits_by_name(hits) == {DO_NAME: (None, None)}

    def test_a_debtor_code_never_links_to_another_companys_customer(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        set_company_scope(db, None)
        other = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
        db.add(other)
        db.flush()
        cust = _customer(db, company_id=str(other.id))
        _do(db, DO_NAME, code=cust.customer_code)  # DO in the default company

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (None, None)}

    def test_prefix_probe_links_the_same_way(self, db):
        from app.services.entity_resolver import _prefix_probe_customer_debtor_name

        cust = _customer(db)
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)

        hits = _prefix_probe_customer_debtor_name(db, TOKEN)

        assert _hits_by_name(hits) == {DO_NAME: (cust.id, MASTER)}


    def test_the_customer_fk_wins_over_the_debtor_code(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        owner = _customer(db)
        coded = _customer(db, f"{MARKER} OTHER SDN BHD")
        _do(db, DO_NAME, code=coded.customer_code, customer_id=owner.id)

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (owner.id, MASTER)}

    def test_the_debtor_code_wins_over_a_name_that_matches_another_customer(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        owner = _customer(db)
        _customer(db, DO_NAME)
        _do(db, DO_NAME, code=owner.customer_code)

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (owner.id, MASTER)}

    def test_a_code_two_customers_hold_links_nobody_and_the_name_link_stands(self, db):
        """Before the merge has run a code can hold several rows (the code-only unique index
        is created only once none remain), so a duplicated code is no link."""
        from app.services.entity_resolver import _probe_customer_debtor_name

        # `a` sorts first, so a code link that took any one holder would land on it.
        a = _customer(db, id="00000000" + str(uuid.uuid4())[8:])
        b = _customer(db, DO_NAME, id="ffffffff" + str(uuid.uuid4())[8:])
        b.customer_code = a.customer_code
        db.flush()
        _do(db, DO_NAME, code=a.customer_code)

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (b.id, DO_NAME)}

    def test_a_blank_debtor_code_never_links(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        blank = _customer(db)
        blank.customer_code = " "
        db.flush()
        _do(db, DO_NAME, code="")

        hits = _probe_customer_debtor_name(db, [DO_NAME])[DO_NAME]

        assert _hits_by_name(hits) == {DO_NAME: (None, None)}

    def test_and_mode_reads_the_customer_name_too(self, db):
        from app.services.entity_resolver import _and_probe_customer

        cust = _customer(db, f"{MARKER} ANDMODE SDN BHD")
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)

        hits = [h for h in _and_probe_customer(db, ["quartz", "onyx"]) if h.match_field == "debtor_name"]

        assert [(h.canonical_code, h.uuid, h.display.get("customer_name")) for h in hits] == [
            (DO_NAME, cust.id, cust.customer_name)
        ]


class TestOneDebtorNameOfSeveralCustomers:
    """Review B1: linking per document lets one buyer name belong to several customers
    ("CASH SALES" typed on DOs of two shops). Every customer stays a hit, never the one the
    database happened to return first."""

    SHARED = f"{MARKER} CASH SALES"

    def _seed(self, db):
        alpha = _customer(db, f"{MARKER} ALPHA SDN BHD")
        beta = _customer(db, f"{MARKER} BETA SDN BHD")
        _do(db, self.SHARED, code=alpha.customer_code, customer_id=alpha.id)
        _do(db, self.SHARED, code=beta.customer_code, customer_id=beta.id)
        _do(db, self.SHARED)  # an unlinked one beside them adds nothing to choose from
        return alpha, beta

    def test_exact_tier_keeps_one_hit_per_customer(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        alpha, beta = self._seed(db)
        hits = _probe_customer_debtor_name(db, [self.SHARED])[self.SHARED]
        assert all(h.uuid for h in hits), "the unlinked row beside linked ones is dropped"
        assert sorted(h.uuid for h in hits) == sorted([alpha.id, beta.id])

    def test_substring_tier_keeps_one_hit_per_customer(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        alpha, beta = self._seed(db)
        token = f"{MARKER} CASH"
        hits = _probe_customer_debtor_name(db, [token])[token]
        assert sorted(h.uuid for h in hits) == sorted([alpha.id, beta.id])

    def test_prefix_probe_keeps_one_hit_per_customer(self, db):
        from app.services.entity_resolver import _prefix_probe_customer_debtor_name

        alpha, beta = self._seed(db)
        hits = _prefix_probe_customer_debtor_name(db, self.SHARED)
        assert sorted(h.uuid for h in hits) == sorted([alpha.id, beta.id])

    def test_the_roster_asks_between_them(self, db):
        alpha, beta = self._seed(db)
        gate, options = _roster(db, self.SHARED)
        assert gate.get("require_specific") is True
        assert sorted(o["label"] for o in options) == sorted([alpha.customer_name, beta.customer_name])


# ============================================================ the roster


def _resolver_payload(db, token: str) -> dict:
    """What the resolver hands the gate for this token: the customer-master probe and the
    DO debtor-name probe, in that order, as `ResolutionResult.as_dict` shapes a match."""
    from app.services.entity_resolver import (
        ResolutionResult,
        TokenResolution,
        _probe_customer,
        _probe_customer_debtor_name,
    )

    matches = list(_probe_customer(db, [token])[token]) + list(_probe_customer_debtor_name(db, [token])[token])
    return ResolutionResult(
        tokens=[token],
        resolutions=[TokenResolution(token=token, matches=matches)],
        elapsed_ms=0.0,
    ).as_dict()


def _parser(raw: str) -> dict:
    return {
        "domain_hint": "order",
        "intent_hint": "check_delivery",
        "entities": [{"hint": "customer", "raw": raw, "canonical_code": raw, "current_message": True}],
    }


def _roster(db, token: str):
    from app.services import customer_group_service
    from app.services.chatbot.lanes.business.gate import run_gate
    from app.services.chatbot.turn.narrow import _options
    from app.services.chatbot.turn_runtime import candidates_by_kind, fill_customer_names
    from app.services.ledger_family import customer_groups

    resolver = _resolver_payload(db, token)
    with customer_groups(customer_group_service.family_overrides(db)):
        gate = run_gate(dict(resolver), parser=_parser(token), resolver=resolver)
        compatible = [e for e in gate.get("compatible_entities") or [] if isinstance(e, dict)]
        fill_customer_names(db, compatible)  # what `turn_runtime.resolve_kinds` does next
        grouped = candidates_by_kind(gate, compatible)
        options = _options(grouped.get("customer", []), "customer", "ledger_family")
    return gate, options


class TestOneCustomerGroupIsOneChoice:
    def test_the_owners_case_one_grouped_customer_with_a_do_level_name_is_no_picker(self, db):
        gid = _group(db)
        cust = _customer(db, group_id=gid)
        _do(db, MASTER, code=cust.customer_code, customer_id=cust.id)
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)

        gate, options = _roster(db, TOKEN)

        assert gate.get("require_specific") is not True, gate.get("gate_clarification")
        assert [o["label"] for o in options] == [MASTER]
        assert options[0]["uuids"] == [cust.id]

    def test_the_owners_case_when_the_merge_recorded_the_do_name_as_an_alias(self, db):
        """The debtor-code merge records a DO-level name as the customer's alias, so the
        name join already found the customer; the hit still printed the DO's own name, which
        the group map does not hold, and the roster read two choices."""
        gid = _group(db)
        a = _customer(db, f"{MASTER} [A/C I]", group_id=gid)
        b = _customer(db, MASTER, group_id=gid)
        customer_identity.record_alias(db, b.id, DO_NAME, source="merge")
        _do(db, MASTER, code=a.customer_code, customer_id=a.id)
        _do(db, DO_NAME, code=b.customer_code, customer_id=b.id)

        gate, options = _roster(db, TOKEN)

        assert gate.get("require_specific") is not True, gate.get("gate_clarification")
        assert [o["label"] for o in options] == [MASTER]
        assert sorted(options[0]["uuids"]) == sorted([a.id, b.id])

    def test_prod_shape_four_accounts_of_one_group_with_merge_aliases_is_one_line(self, db):
        """Crew's prod snapshot (6 Oct): one customer group, four accounts named by ledger
        marker, the 5 Oct debtor merge recorded the plain trading name as an alias of A/C I
        and the DO-level name as an alias of A/C III, and the DOs sit on A/C I and A/C III.
        Each DO-name hit printed its DO name, which the exact-name group map does not hold,
        so the group split into two picker lines. One group is one choice: no ask, and the
        pick covers every account the resolver found."""
        gid = _group(db)
        accounts = [_customer(db, f"{MASTER} [A/C {m}]", group_id=gid) for m in ("I", "II", "III", "IV")]
        ac1, _ac2, ac3, ac4 = accounts
        customer_identity.record_alias(db, ac1.id, MASTER, source="merge")
        customer_identity.record_alias(db, ac3.id, DO_NAME, source="merge")
        _do(db, MASTER, code=ac1.customer_code, customer_id=ac1.id)
        _do(db, DO_NAME, code=ac3.customer_code, customer_id=ac3.id)
        _do(db, ac4.customer_name, code=ac4.customer_code, customer_id=ac4.id)

        gate, options = _roster(db, TOKEN)

        assert gate.get("require_specific") is not True, gate.get("gate_clarification")
        assert [o["label"] for o in options] == [MASTER], [o["label"] for o in options]
        assert sorted(options[0]["uuids"]) == sorted(a.id for a in accounts)

    def test_two_ledgers_of_one_group_and_a_do_level_name_is_one_line(self, db):
        gid = _group(db)
        a = _customer(db, f"{MASTER} [A/C I]", group_id=gid)
        b = _customer(db, MASTER, group_id=gid)
        _do(db, MASTER, code=a.customer_code, customer_id=a.id)
        _do(db, DO_NAME, code=b.customer_code, customer_id=b.id)

        gate, options = _roster(db, TOKEN)

        assert gate.get("require_specific") is not True, gate.get("gate_clarification")
        assert [o["label"] for o in options] == [MASTER]
        assert sorted(options[0]["uuids"]) == sorted([a.id, b.id])

    def test_a_real_second_customer_still_gets_its_own_line(self, db):
        """The fix merges by identity, not by name: a different customer outside the group,
        with its own code, is still a choice."""
        gid = _group(db)
        cust = _customer(db, group_id=gid)
        other = _customer(db, f"{MARKER} TRADING (JB) SDN BHD")
        _do(db, DO_NAME, code=cust.customer_code, customer_id=cust.id)
        _do(db, other.customer_name, code=other.customer_code, customer_id=other.id)

        _gate, options = _roster(db, TOKEN)

        assert len(options) == 2, [o["label"] for o in options]
