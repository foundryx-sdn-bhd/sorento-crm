"""RED tests for DEBTOR-CODE-UNIQUE: one customer per debtor code per company.

Card: documentation/plans/autocount/CARD-debtor-code-unique-04oct.md (rules R1-R4, R12).
Owner decision 4 Oct 2026: debtor code is unique per company, like AutoCount; documents keep
their own buyer name in `debtor_name`.

Substrate: `tests._pg_fixture.blank_session()` (real Postgres blank schema, rolled back per
test). Every test seeds its own rows, with created_at / id set explicitly wherever survivor
ordering matters. All data is masked.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.order import Customer, CustomerAlias, Order
from app.schemas.order import CustomerCreate
from app.services import customer_identity
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.order_service import CustomerService, OrderService
from app.services.scm import customer_back_create

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZDCU"

OLD = datetime(2025, 1, 1, 9, 0, 0)
MID = datetime(2025, 6, 1, 9, 0, 0)
NEW = datetime(2026, 9, 1, 9, 0, 0)


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@pytest.fixture()
def company_b(db) -> str:
    company = Company(id=str(uuid.uuid4()), name=f"{MARKER} B", code=unique_code(MARKER)[:10])
    db.add(company)
    db.flush()
    return str(company.id)


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _seed(
    db,
    code: str,
    name: str,
    created_at: datetime,
    *,
    id: str | None = None,
    company_id: str = DEFAULT_COMPANY_ID,
    **extra,
) -> str:
    row = Customer(
        id=id or str(uuid.uuid4()),
        customer_code=code,
        customer_name=name,
        created_at=created_at,
        company_id=company_id,
        **extra,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _ref(db, entity_id: str, source_ref: str, company_id: str = DEFAULT_COMPANY_ID) -> None:
    db.add(
        IntegrationReference(
            entity_type="customers",
            entity_id=entity_id,
            company_id=company_id,
            source_system="autocount",
            source_ref=source_ref,
        )
    )
    db.flush()


def _count(db, code: str, company_id: str = DEFAULT_COMPANY_ID) -> int:
    return db.execute(
        text(
            "SELECT count(*) FROM customers WHERE company_id = :c "
            "AND lower(btrim(customer_code)) = lower(btrim(:code))"
        ),
        {"c": company_id, "code": code},
    ).scalar()


# ===================================================================== R1 / R4 identity
class TestCustomerByCode:
    def test_one_row_found_case_and_space_insensitive(self, db):
        code = _code("A")
        cid = _seed(db, code, "NAME-A", OLD)
        found = customer_identity.customer_by_code(
            db, f"  {code.lower()} ", company_id=DEFAULT_COMPANY_ID
        )
        assert found is not None and str(found.id) == cid

    def test_no_row_is_none(self, db):
        assert (
            customer_identity.customer_by_code(db, _code("NONE"), company_id=DEFAULT_COMPANY_ID)
            is None
        )

    def test_blank_code_is_none(self, db):
        _seed(db, " ", "NAME-BLANK", OLD)
        assert customer_identity.customer_by_code(db, "  ", company_id=DEFAULT_COMPANY_ID) is None

    def test_never_crosses_companies(self, db, company_b):
        set_company_scope(db, None)  # an all-companies session: the explicit filter decides
        code = _code("X")
        _seed(db, code, "NAME-SRT", OLD)
        mch = _seed(db, code, "NAME-MCH", NEW, company_id=company_b)
        found = customer_identity.customer_by_code(db, code, company_id=company_b)
        assert str(found.id) == mch

    def test_all_companies_scope_looks_in_the_write_company(self, db, company_b):
        """Security review S1: an import job with no company snapshot runs all-companies and
        writes to the default company, so the lookup must not hand back another company's
        older or linked row under the same code."""
        set_company_scope(db, None)
        code = _code("W")
        mch = _seed(db, code, "NAME-MCH", OLD, company_id=company_b)
        _ref(db, mch, f"AED_MOCHA:{code}", company_id=company_b)
        assert customer_identity.customer_by_code(db, code) is None
        got = OrderService(db)._upsert_customer_from_debtor("NAME-SRT", code)
        assert got != mch
        assert str(db.get(Customer, got).company_id) == DEFAULT_COMPANY_ID

    def test_unlinked_group_resolves_to_oldest(self, db):
        code = _code("O")
        _seed(db, code, "NAME-NEW", NEW)
        oldest = _seed(db, code, "NAME-OLD", OLD)
        _seed(db, code, "NAME-MID", MID)
        found = customer_identity.customer_by_code(db, code, company_id=DEFAULT_COMPANY_ID)
        assert str(found.id) == oldest

    def test_created_at_tie_breaks_on_lowest_id(self, db):
        code = _code("T")
        hi = "ffffffff-0000-4000-8000-000000000000"
        lo = "00000000-0000-4000-8000-000000000001"
        _seed(db, code, "NAME-HI", OLD, id=hi)
        _seed(db, code, "NAME-LO", OLD, id=lo)
        found = customer_identity.customer_by_code(db, code, company_id=DEFAULT_COMPANY_ID)
        assert str(found.id) == lo

    def test_linked_row_beats_an_older_unlinked_row(self, db):
        code = _code("L")
        _seed(db, code, "NAME-OLD", OLD)
        linked = _seed(db, code, "NAME-LINKED", NEW)
        _ref(db, linked, "AED_SORENTO:9001")
        found = customer_identity.customer_by_code(db, code, company_id=DEFAULT_COMPANY_ID)
        assert str(found.id) == linked

    def test_code_form_ref_beats_an_older_autokey_ref(self, db):
        """Card E3 / Q1: two linked rows, the code-form ref wins (owner: linkage by code)."""
        code = _code("D")
        autokey = _seed(db, code, "NAME-A", OLD)
        _ref(db, autokey, "AED_SORENTO:1003")
        code_form = _seed(db, code, "NAME-B", NEW)
        _ref(db, code_form, f"foundryx_service_s51:{code}")
        found = customer_identity.customer_by_code(db, code, company_id=DEFAULT_COMPANY_ID)
        assert str(found.id) == code_form

    def test_code_form_ref_match_is_case_and_space_insensitive(self, db):
        code = _code("C")
        autokey = _seed(db, code, "NAME-A", OLD)
        _ref(db, autokey, "AED_SORENTO:1004")
        code_form = _seed(db, code, "NAME-B", NEW)
        _ref(db, code_form, f"AED_SORENTO: {code.lower()} ")
        found = customer_identity.customer_by_code(db, code, company_id=DEFAULT_COMPANY_ID)
        assert str(found.id) == code_form


# ======================================================================= R2 create paths
class TestOrderDebtorUpsert:
    def test_same_code_new_name_links_to_the_existing_row_without_renaming(self, db):
        code = _code("U")
        cid = _seed(db, code, "CASH SALES", OLD)
        got = OrderService(db)._upsert_customer_from_debtor("BUYER-17", code)
        assert got == cid
        assert _count(db, code) == 1
        name = db.execute(
            text("SELECT customer_name FROM customers WHERE id = :i"), {"i": cid}
        ).scalar()
        assert name == "CASH SALES"

    def test_shared_code_links_to_the_would_be_survivor(self, db):
        code = _code("S")
        _seed(db, code, "BUYER-1", NEW)
        survivor = _seed(db, code, "CASH SALES", OLD)
        got = OrderService(db)._upsert_customer_from_debtor("BUYER-2", code)
        assert got == survivor
        assert _count(db, code) == 2

    def test_unknown_code_still_creates_one_row(self, db):
        code = _code("N")
        got = OrderService(db)._upsert_customer_from_debtor("NAME-NEW", code)
        row = db.get(Customer, got)
        assert row.customer_code == code and row.customer_name == "NAME-NEW"
        again = OrderService(db)._upsert_customer_from_debtor("OTHER NAME", f" {code} ")
        assert again == got
        assert _count(db, code) == 1

    def test_blank_code_makes_no_customer(self, db):
        """DBR-DEBTOR-CLEANUP: a blank code keeps the name as text on the DO and has no customer."""
        before = db.query(Customer).count()
        a = OrderService(db)._upsert_customer_from_debtor(f"{MARKER} NO CODE", None)
        b = OrderService(db)._upsert_customer_from_debtor(f"{MARKER} NO CODE 2", "")
        assert a is None and b is None
        assert db.query(Customer).count() == before


class TestBackCreate:
    def test_same_code_new_name_returns_the_existing_row(self, db):
        code = _code("B")
        cid = _seed(db, code, "CASH SALES", OLD)
        got = customer_back_create.get_or_create(
            db, code=code, name="BUYER-9", company_id=DEFAULT_COMPANY_ID
        )
        assert str(got.id) == cid
        assert _count(db, code) == 1
        assert got.customer_name == "CASH SALES"

    def test_shared_code_returns_the_would_be_survivor(self, db):
        code = _code("BS")
        _seed(db, code, "BUYER-1", NEW)
        linked = _seed(db, code, "CASH SALES", MID)
        _ref(db, linked, f"AED_SORENTO:{code}")
        _seed(db, code, "BUYER-0", OLD)
        got = customer_back_create.get_or_create(
            db, code=code, name="BUYER-2", company_id=DEFAULT_COMPANY_ID
        )
        assert str(got.id) == linked

    def test_unknown_code_creates_with_a_trimmed_code(self, db):
        code = _code("BN")
        got = customer_back_create.get_or_create(
            db, code=f" {code} ", name=" NAME-N ", company_id=DEFAULT_COMPANY_ID
        )
        assert got.customer_code == code
        assert got.customer_name == "NAME-N"

    def test_never_returns_another_companys_row(self, db, company_b):
        code = _code("BX")
        _seed(db, code, "NAME-MCH", OLD, company_id=company_b)
        got = customer_back_create.get_or_create(
            db, code=code, name="NAME-SRT", company_id=DEFAULT_COMPANY_ID
        )
        assert got is not None and str(got.company_id) == DEFAULT_COMPANY_ID


class TestManualCreate:
    def test_existing_code_under_another_name_is_a_conflict(self, db):
        code = _code("M")
        _seed(db, code, "NAME-A", OLD)
        with pytest.raises(HTTPException) as exc:
            CustomerService(db).create_customer(
                CustomerCreate(customer_code=f" {code.lower()} ", customer_name="NAME-OTHER")
            )
        assert exc.value.status_code == 409
        assert "debtor code" in str(exc.value.detail).lower()
        assert _count(db, code) == 1


class TestMasterRefResolver:
    def test_shared_code_resolves_to_the_would_be_survivor(self, db):
        from app.services.master_ref_resolver import MasterRefResolver

        code = _code("R")
        oldest = _seed(db, code, "NAME-OLD", OLD)
        _seed(db, code, "NAME-NEW", NEW)  # highest id is irrelevant now
        resolver = MasterRefResolver(db, None, company_id=DEFAULT_COMPANY_ID)
        assert resolver._resolve_by_code(Customer, code) == oldest

    def test_same_code_new_name_never_back_creates(self, db):
        from app.services.master_ref_resolver import MasterRefResolver

        code = _code("RB")
        cid = _seed(db, code, "CASH SALES", OLD)
        resolver = MasterRefResolver(db, None, company_id=DEFAULT_COMPANY_ID)
        warnings: list = []
        got = resolver._resolve_by_fallback(Customer, code, "BUYER-3", warnings)
        assert got == cid
        assert _count(db, code) == 1


# ============================================================================ R12 aliases
class TestAliases:
    def test_record_alias_is_idempotent_and_skips_the_current_name(self, db):
        code = _code("AL")
        cid = _seed(db, code, "CASH SALES", OLD)
        customer_identity.record_alias(db, cid, "BUYER-1", source="merge")
        customer_identity.record_alias(db, cid, " buyer-1 ", source="merge")
        customer_identity.record_alias(db, cid, "cash sales", source="merge")
        rows = db.query(CustomerAlias).filter(CustomerAlias.customer_id == cid).all()
        assert [r.alias_name for r in rows] == ["BUYER-1"]
        assert str(rows[0].company_id) == DEFAULT_COMPANY_ID

    def test_order_name_probe_finds_the_customer_through_an_alias(self, db):
        from app.services.entity_resolver import _probe_customer_debtor_name

        code = _code("AP")
        buyer = f"{MARKER} BUYER {uuid.uuid4().hex[:6]}"
        cid = _seed(db, code, "CASH SALES", OLD)
        customer_identity.record_alias(db, cid, buyer, source="merge")
        db.add(
            Order(
                order_number=_code("ORD"),
                debtor_name=buyer,
                debtor_code=code,
                customer_id=cid,
                company_id=DEFAULT_COMPANY_ID,
            )
        )
        db.flush()
        hits = _probe_customer_debtor_name(db, [buyer])
        assert [h.uuid for h in hits[buyer]] == [cid]

    def test_order_name_never_resolves_to_another_companys_alias(self, db, company_b):
        """Security review N1: an SRT order's buyer name never lands on an MCH customer."""
        from app.services.entity_resolver import _probe_customer_debtor_name

        set_company_scope(db, None)
        buyer = f"{MARKER} BUYER {uuid.uuid4().hex[:6]}"
        mch = _seed(db, _code("MX"), "MCH CASH", OLD, company_id=company_b)
        customer_identity.record_alias(db, mch, buyer, source="merge")
        db.add(Order(order_number=_code("ORD"), debtor_name=buyer, company_id=DEFAULT_COMPANY_ID))
        db.flush()
        hits = _probe_customer_debtor_name(db, [buyer])
        assert [h.uuid for h in hits[buyer]] == [None]

    def test_trigram_customer_search_reads_aliases(self, db):
        from app.services.entity_resolver import _trgm_lookup

        code = _code("AT")
        buyer = f"QWXZ ALIASONLY {uuid.uuid4().hex[:6].upper()}"
        cid = _seed(db, code, "CASH SALES", OLD)
        customer_identity.record_alias(db, cid, buyer, source="merge")
        hits = _trgm_lookup(db, buyer, frozenset({"customer"}))
        assert cid in {h.uuid for h in hits}
