"""RED tests for DEBTOR-CODE-LINK: the customers master push is matched by
company + debtor code, never by source_ref or name.

Card: documentation/plans/autocount/CARD-debtor-code-link-04oct.md (rules R1-R7).
Owner rulings 4 Oct 2026: "for debtor, I want our linkage to be the debtor code"
and "update the FIRST one" when several customers share a code.

Substrate: `tests._pg_fixture.blank_session()` (real Postgres blank schema,
rolled back per test). Every test seeds its own rows, with created_at / id set
explicitly wherever "first row" ordering matters. All data is masked.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.order import Customer
from app.services.audit_service import register_audit_listeners
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.embedding_change_listener import register_embedding_change_listeners
from app.services.master_ingest_service import IngestOutcome, MasterIngestService
from app.services.master_ref_resolver import WARN_REF_MISMATCH

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZDCL"

OLD = datetime(2025, 1, 1, 9, 0, 0)
NEW = datetime(2026, 9, 1, 9, 0, 0)


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()
    register_audit_listeners()
    register_embedding_change_listeners()


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


def _svc(db, company_id: str = DEFAULT_COMPANY_ID) -> MasterIngestService:
    return MasterIngestService(db, integration_id=None, company_id=company_id)


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


def _refs_of(db, entity_id: str) -> list[str]:
    return list(
        db.execute(
            text(
                "SELECT source_ref FROM integration_references "
                "WHERE entity_type = 'customers' AND entity_id = :e ORDER BY source_ref"
            ),
            {"e": entity_id},
        ).scalars()
    )


def _cols(db, entity_id: str, *columns: str) -> tuple:
    return tuple(
        db.execute(
            text(f"SELECT {', '.join(columns)} FROM customers WHERE id = :i"), {"i": entity_id}
        ).first()
    )


def _scope(db, *extra: str) -> None:
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, *extra}))


def _push(db, record: dict, company_id: str = DEFAULT_COMPANY_ID):
    result = _svc(db, company_id).ingest("customers", [record])
    return result, result.records[0]


class TestFirstRowByDebtorCode:
    def test_t1_single_row_with_other_stored_ref_is_updated_and_warns_ref_mismatch(self, db):
        _scope(db)
        code = _code("T1")
        a = _seed(db, code, "NAME-A", OLD, phone_number="0100000000")
        _ref(db, a, "AED_SORENTO:1001")

        result, rec = _push(
            db,
            {
                "source_ref": f"SS:{code.lower()}",
                "code": f"  {code.lower()}  ",
                "name": "NAME-A",
                "phone_number": "0111111111",
            },
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _cols(db, a, "phone_number") == ("0111111111",)
        # the row keeps its own code spelling, like its same-code siblings (review nit)
        assert _cols(db, a, "customer_code") == (code,)
        assert _refs_of(db, a) == ["AED_SORENTO:1001"]
        assert rec.warnings == [WARN_REF_MISMATCH]

    def test_t2_two_rows_same_code_oldest_is_updated_other_untouched(self, db):
        _scope(db)
        code = _code("T2")
        a = _seed(db, code, "NAME-A", OLD, phone_number="0100000000")
        b = _seed(db, code, "NAME-B", NEW, phone_number="0122222222")

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-NEW", "phone_number": "0133333333"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        # never rename (owner decision, 4 Oct): the oldest row's fields update, its name stays
        assert _cols(db, a, "customer_name", "phone_number") == ("NAME-A", "0133333333")
        assert _cols(db, b, "customer_name", "phone_number") == ("NAME-B", "0122222222")

    def test_t3_same_created_at_lowest_id_wins_regardless_of_insert_order(self, db):
        _scope(db)
        code = _code("T3")
        id_low = "00000000-0000-4000-8000-000000000001"
        id_high = "00000000-0000-4000-8000-000000000002"
        _seed(db, code, "NAME-HIGH", OLD, id=id_high)
        _seed(db, code, "NAME-LOW", OLD, id=id_low)

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-PUSHED", "phone_number": "0144444444"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == id_low
        assert _cols(db, id_low, "customer_name", "phone_number") == ("NAME-LOW", "0144444444")
        assert _cols(db, id_high, "customer_name", "phone_number") == ("NAME-HIGH", None)

    def test_t4_exact_name_row_wins_over_the_oldest_row(self, db):
        _scope(db)
        code = _code("T4")
        a = _seed(db, code, "NAME-C", OLD)
        b = _seed(db, code, "NAME-C2", NEW)

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": " name-c2 ", "phone_number": "0155555555"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == b
        assert _cols(db, b, "lower(btrim(customer_name))", "phone_number") == ("name-c2", "0155555555")
        assert _cols(db, a, "customer_name", "phone_number") == ("NAME-C", None)
        assert rec.warnings in ([], [WARN_REF_MISMATCH])
        assert "customer_name_taken" not in rec.warnings

    def test_t5_exact_name_row_gets_the_email_oldest_untouched(self, db):
        _scope(db)
        code = _code("T5")
        a = _seed(db, code, "ALPHA", OLD, email="alpha@old.example")
        b = _seed(db, code, "BETA", NEW, email="beta@old.example")

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "BETA", "email": "pushed@new.example"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == b
        assert _cols(db, b, "customer_name", "email") == ("BETA", "pushed@new.example")
        assert _cols(db, a, "customer_name", "email") == ("ALPHA", "alpha@old.example")

    def test_t14_same_name_under_another_code_is_never_touched(self, db):
        _scope(db)
        code, other_code = _code("T14"), _code("T14X")
        other = _seed(db, other_code, "NAME-SHARED", OLD, phone_number="0100000014")
        target = _seed(db, code, "NAME-OTHER", NEW)

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-SHARED", "phone_number": "0190000014"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == target
        assert _cols(db, target, "customer_code", "customer_name", "phone_number") == (
            code,
            "NAME-OTHER",
            "0190000014",
        )
        assert _cols(db, other, "customer_code", "customer_name", "phone_number") == (
            other_code,
            "NAME-SHARED",
            "0100000014",
        )

    def test_t6_no_row_with_the_code_creates_and_links_the_pushed_ref(self, db):
        _scope(db)
        code = _code("T6")
        ref = f"SS:{code}"

        _result, rec = _push(db, {"source_ref": ref, "code": code, "name": "NAME-NEW"})

        assert rec.outcome is IngestOutcome.CREATED, rec.errors
        assert _cols(db, rec.entity_id, "customer_code", "customer_name") == (code, "NAME-NEW")
        assert _refs_of(db, rec.entity_id) == [ref]

    def test_t7_matching_never_crosses_companies(self, db, company_b):
        _scope(db, company_b)
        code = _code("T7")
        only_b = _seed(db, code, "NAME-B", OLD, company_id=company_b, phone_number="0100000007")

        _result, rec = _push(
            db, {"source_ref": f"SS:{code}", "code": code, "name": "NAME-D", "phone_number": "0177777777"}
        )
        assert rec.outcome is IngestOutcome.CREATED, rec.errors
        assert rec.entity_id != only_b
        assert _cols(db, rec.entity_id, "company_id::text") == (DEFAULT_COMPANY_ID,)
        assert _cols(db, only_b, "customer_name", "phone_number") == ("NAME-B", "0100000007")

        # and a push into company B finds only company B's row
        _result, rec_b = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-B2", "phone_number": "0188888888"},
            company_id=company_b,
        )
        assert rec_b.outcome is IngestOutcome.UPDATED, rec_b.errors
        assert rec_b.entity_id == only_b
        assert _cols(db, only_b, "customer_name", "phone_number") == ("NAME-B", "0188888888")
        assert _cols(db, rec.entity_id, "phone_number") == ("0177777777",)


class TestPushedRefIsBookkeeping:
    def test_t8_first_row_unlinked_and_ref_free_links_it_to_the_first_row(self, db):
        _scope(db)
        code = _code("T8")
        a = _seed(db, code, "NAME-A", OLD)
        b = _seed(db, code, "NAME-B", NEW)
        ref = f"AED_SORENTO:{code}"

        _result, rec = _push(
            db, {"source_ref": ref, "code": code, "name": "NAME-NEW", "phone_number": "0100000008"}
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _refs_of(db, a) == [ref]
        assert _refs_of(db, b) == []
        assert rec.warnings == []

    def test_t9_pushed_ref_held_by_the_newer_sibling_is_left_there_and_warns(self, db):
        _scope(db)
        code = _code("T9")
        a = _seed(db, code, "NAME-A", OLD)
        b = _seed(db, code, "NAME-B", NEW)
        ref = f"AED_SORENTO:{code}"
        _ref(db, b, ref)

        _result, rec = _push(
            db, {"source_ref": ref, "code": code, "name": "NAME-NEW", "phone_number": "0100000009"}
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _refs_of(db, b) == [ref]
        assert _refs_of(db, a) == []
        assert rec.warnings == [WARN_REF_MISMATCH]

    def test_t10_pushed_ref_already_on_the_first_row_is_fine_without_warning(self, db):
        _scope(db)
        code = _code("T10")
        a = _seed(db, code, "NAME-A", OLD)
        ref = f"AED_SORENTO:{code}"
        _ref(db, a, ref)

        _result, rec = _push(
            db, {"source_ref": ref, "code": code, "name": "NAME-A", "phone_number": "0100000010"}
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _refs_of(db, a) == [ref]
        assert rec.warnings == []

    def test_t15_exact_name_row_unlinked_and_ref_free_gets_the_ref(self, db):
        _scope(db)
        code = _code("T15")
        a = _seed(db, code, "NAME-A", OLD)
        b = _seed(db, code, "NAME-B", NEW)
        ref = f"AED_SORENTO:{code}"

        _result, rec = _push(
            db, {"source_ref": ref, "code": code, "name": "name-b", "phone_number": "0100000015"}
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == b
        assert _refs_of(db, b) == [ref]
        assert _refs_of(db, a) == []
        assert rec.warnings == []

    def test_t11_renumbered_code_creates_a_new_row_and_leaves_the_old_link(self, db):
        _scope(db)
        old_code, new_code = _code("T11O"), _code("T11N")
        x = _seed(db, old_code, "NAME-X", OLD)
        _ref(db, x, "AED_SORENTO:777")

        _result, rec = _push(
            db, {"source_ref": "AED_SORENTO:777", "code": new_code, "name": "NAME-X"}
        )

        assert rec.outcome is IngestOutcome.CREATED, rec.errors
        assert rec.entity_id != x
        assert _cols(db, rec.entity_id, "customer_code") == (new_code,)
        assert _cols(db, x, "customer_code") == (old_code,)
        assert _refs_of(db, x) == ["AED_SORENTO:777"]
        assert _refs_of(db, rec.entity_id) == []
        assert rec.warnings == [WARN_REF_MISMATCH]


class TestDryRunAndUnchangedBehaviour:
    def test_t12_dry_run_reports_the_first_row_as_target_and_persists_nothing(self, db):
        _scope(db)
        code = _code("T12")
        a = _seed(db, code, "NAME-A", OLD, phone_number="0100000012")
        b = _seed(db, code, "NAME-B", NEW, phone_number="0120000012")
        # commit releases the test savepoint, so the dry run's own rollback
        # discards only the ingest work and not the seeded rows
        db.commit()

        result = _svc(db).ingest(
            "customers",
            [{"source_ref": f"SS:{code}", "code": code, "name": "NAME-NEW", "phone_number": "0190000012"}],
            dry_run=True,
        )
        rec = result.records[0]

        assert result.dry_run is True
        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert rec.diff is not None and "phone_number" in rec.diff
        assert "customer_name" not in rec.diff, "the master feed never renames an existing row"
        assert _cols(db, a, "customer_name", "phone_number") == ("NAME-A", "0100000012")
        assert _cols(db, b, "phone_number") == ("0120000012",)
        assert _refs_of(db, a) == []

    def test_t13_absent_field_stays_untouched_on_the_first_row(self, db):
        _scope(db)
        code = _code("T13")
        a = _seed(db, code, "NAME-A", OLD, email="keep@example.com", is_active=False)
        _seed(db, code, "NAME-B", NEW, email="other@example.com")

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-NEW", "phone_number": "0100000013"},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _cols(db, a, "email", "is_active", "phone_number") == (
            "keep@example.com",
            False,
            "0100000013",
        )


class TestReviewRound1:
    def test_t16_same_text_ref_under_another_source_system_warns_and_never_fails(self, db):
        """Review M1: `origin_of` sees any source system, `link()` only autocount. A row whose
        stored ref has the pushed text under another system must not reach `link()`."""
        _scope(db)
        code = _code("T16")
        a = _seed(db, code, "NAME-A", OLD)
        ref = f"SS:{code}"
        db.add(
            IntegrationReference(
                entity_type="customers",
                entity_id=a,
                company_id=DEFAULT_COMPANY_ID,
                source_system="import",
                source_ref=ref,
            )
        )
        db.flush()

        _result, rec = _push(db, {"source_ref": ref, "code": code, "name": "NAME-A", "phone_number": "0100000016"})

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.warnings == [WARN_REF_MISMATCH]
        assert _cols(db, a, "phone_number") == ("0100000016",)


class TestAgentFanOutOnTheCodePath:
    def test_t17_agent_reaches_every_same_code_row_while_the_oldest_keeps_its_name(self, db):
        """#1468 x DEBTOR-CODE-LINK: the agent fan-out runs from the one code-first path, on
        the update branch, keyed by the pushed code even though the code is not rewritten."""
        from app.models.sales_agent import SalesAgent

        _scope(db)
        code = _code("T17")
        agent = SalesAgent(sales_agent=_code("AGT").upper(), company_id=None)
        db.add(agent)
        db.flush()
        a = _seed(db, code, "NAME-A", OLD)
        b = _seed(db, code, "NAME-B (DO ROW)", NEW)

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code.lower(), "name": "NAME-OTHER",
             "sales_agent_code": agent.sales_agent.lower()},
        )

        assert rec.outcome is IngestOutcome.UPDATED, rec.errors
        assert rec.entity_id == a
        assert _cols(db, a, "customer_name", "customer_code", "sales_agent_id::text") == (
            "NAME-A", code, str(agent.id),
        )
        assert _cols(db, b, "customer_name", "sales_agent_id::text") == ("NAME-B (DO ROW)", str(agent.id))

    def test_t18_agent_reaches_same_code_rows_on_the_create_branch(self, db):
        from app.models.sales_agent import SalesAgent

        _scope(db)
        code = _code("T18")
        agent = SalesAgent(sales_agent=_code("AGT").upper(), company_id=None)
        db.add(agent)
        db.flush()

        _result, rec = _push(
            db,
            {"source_ref": f"SS:{code}", "code": code, "name": "NAME-NEW",
             "sales_agent_code": agent.sales_agent},
        )

        assert rec.outcome is IngestOutcome.CREATED, rec.errors
        assert _cols(db, rec.entity_id, "sales_agent_id::text") == (str(agent.id),)
