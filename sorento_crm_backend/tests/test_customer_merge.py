"""RED tests for DEBTOR-CODE-UNIQUE's merge (card R5-R13): every (company, debtor code) group
with more than one customer row collapses onto one survivor, and the merge can be undone.

Card: documentation/plans/autocount/CARD-debtor-code-unique-04oct.md.

Substrate: `tests._pg_fixture.blank_session()` (real Postgres blank schema, rolled back per
test). The service discovers customer FKs from `pg_constraint` at run time, so these tests
seed a representative subset and assert the counts move; every row is test-created and
masked.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models.company import Company
from app.models.integration_reference import IntegrationReference
from app.models.order import Customer, CustomerAlias, CustomerContact, CustomerMergeMap, Order, SalesOrder
from app.models.project_so import CustomerItemCodeMap
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import customer_merge
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code

MARKER = "ZZCM"

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


def _uid() -> str:
    return str(uuid.uuid4())


def _code(stem: str) -> str:
    return unique_code(f"{MARKER}{stem}")[:30]


def _seed(db, code, name, created_at, *, id=None, company_id=DEFAULT_COMPANY_ID, **extra) -> str:
    row = Customer(
        id=id or _uid(),
        customer_code=code,
        customer_name=name,
        created_at=created_at,
        company_id=company_id,
        **extra,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _ref(db, entity_id, source_ref, company_id=DEFAULT_COMPANY_ID) -> None:
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


def _so(db, customer_id, *, debtor_name=None, company_id=DEFAULT_COMPANY_ID) -> str:
    row = SalesOrder(
        id=_uid(),
        so_number=_code("SO"),
        status="open",
        customer_id=customer_id,
        debtor_name=debtor_name,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _order(db, customer_id, *, debtor_name=None) -> str:
    row = Order(
        id=_uid(),
        order_number=_code("ORD"),
        customer_id=customer_id,
        debtor_name=debtor_name,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _contact(db, customer_id, role="stakeholder") -> str:
    row = CustomerContact(id=_uid(), customer_id=customer_id, full_name=f"{MARKER} P", contact_role=role)
    db.add(row)
    db.flush()
    return str(row.id)


def _respond_contact(db) -> str:
    cid = f"{MARKER}-{uuid.uuid4().hex[:10]}"
    db.execute(
        text("INSERT INTO respond_contacts (id, phone_number) VALUES (:i, :p)"),
        {"i": cid, "p": "+60-000-" + uuid.uuid4().hex[:6]},
    )
    return cid


def _link(db, contact_id, customer_id, *, primary=False) -> str:
    lid = _uid()
    db.execute(
        text(
            "INSERT INTO respond_contact_customers (id, contact_id, customer_id, is_primary, company_id) "
            "VALUES (:i, :c, :cu, :p, :co)"
        ),
        {"i": lid, "c": contact_id, "cu": customer_id, "p": primary, "co": DEFAULT_COMPANY_ID},
    )
    return lid


def _product(db) -> str:
    cat = ProductCategory(id=_uid(), category_code=unique_code("cat")[:50], category_name="ZZT cat")
    uom = UnitOfMeasure(id=_uid(), uom_code=unique_code("uom")[:20], uom_name="Each")
    db.add_all([cat, uom])
    db.flush()
    row = Product(
        id=_uid(),
        company_id=DEFAULT_COMPANY_ID,
        product_code=_code("P"),
        product_name="ZZT P",
        category_id=cat.id,
        base_uom_id=uom.id,
        list_price=1,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _code_map(db, customer_id, item_code, product_id) -> str:
    row = CustomerItemCodeMap(
        id=_uid(),
        company_id=DEFAULT_COMPANY_ID,
        customer_id=customer_id,
        customer_code=item_code,
        product_id=product_id,
    )
    db.add(row)
    db.flush()
    return str(row.id)


def _scalar(db, sql, **params):
    return db.execute(text(sql), params).scalar()


def _exists(db, customer_id) -> bool:
    return bool(_scalar(db, "SELECT count(*) FROM customers WHERE id = :i", i=customer_id))


def _group(groups, code):
    key = code.strip().lower()
    hits = [g for g in groups if g.code_key == key]
    assert len(hits) == 1, f"expected one group for {code}, got {len(hits)}"
    return hits[0]


# ============================================================================ R5 / R6 plan
class TestPlan:
    def test_dry_run_reports_the_group_and_changes_nothing(self, db):
        code = _code("A")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, f" {code.lower()} ", "BUYER-1", NEW)
        _so(db, retired)
        groups = customer_merge.plan(db, codes=[code])
        g = _group(groups, code)
        assert g.survivor_id == survivor
        assert g.retired_ids == [retired]
        assert g.retired_names == ["BUYER-1"]
        assert g.fk_counts.get("sales_orders.customer_id") == 1
        assert _exists(db, retired)
        assert _scalar(db, "SELECT count(*) FROM sales_orders WHERE customer_id = :i", i=retired) == 1

    def test_single_row_codes_are_not_groups(self, db):
        code = _code("ONE")
        _seed(db, code, "NAME-A", OLD)
        assert [g for g in customer_merge.plan(db, codes=[code])] == []

    def test_same_code_in_two_companies_is_not_a_group(self, db, company_b):
        code = _code("X")
        _seed(db, code, "NAME-SRT", OLD)
        _seed(db, code, "NAME-MCH", NEW, company_id=company_b)
        assert customer_merge.plan(db, codes=[code]) == []

    def test_survivor_is_the_code_form_linked_row(self, db):
        code = _code("D")
        autokey = _seed(db, code, "NAME-A", OLD)
        _ref(db, autokey, "AED_SORENTO:1003")
        code_form = _seed(db, code, "NAME-B", NEW)
        _ref(db, code_form, f"foundryx_service_s51:{code}")
        other = _seed(db, code, "NAME-C", MID)
        g = _group(customer_merge.plan(db, codes=[code]), code)
        assert g.survivor_id == code_form
        assert set(g.retired_ids) == {autokey, other}

    def test_conflicting_values_are_listed(self, db):
        code = _code("CF")
        survivor = _seed(db, code, "NAME-A", OLD, phone_number="+60-1", is_active=True)
        retired = _seed(db, code, "NAME-B", NEW, phone_number="+60-2", is_active=False)
        g = _group(customer_merge.plan(db, codes=[code]), code)
        fields = {(c["field"], c["retired_id"]) for c in g.conflicts}
        assert ("phone_number", retired) in fields
        assert ("is_active", retired) in fields
        assert g.survivor_id == survivor

    def test_null_versus_value_is_not_a_conflict(self, db):
        code = _code("NC")
        _seed(db, code, "NAME-A", OLD)
        _seed(db, code, "NAME-B", NEW, phone_number="+60-2")
        g = _group(customer_merge.plan(db, codes=[code]), code)
        assert g.conflicts == []

    def test_plan_csv_has_one_row_per_group(self, db, tmp_path):
        code = _code("CSV")
        _seed(db, code, "NAME-A", OLD)
        _seed(db, code, "NAME-B", NEW)
        path = tmp_path / "plan.csv"
        customer_merge.write_plan_csv(customer_merge.plan(db, codes=[code]), path)
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2  # header + one group
        assert "survivor_id" in lines[0] and "keep" in lines[0]


# ============================================================================== R7-R10 apply
class TestApply:
    def test_repoints_documents_fills_names_and_deletes_retired_rows(self, db):
        code = _code("AP")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW, email="buyer@example.invalid")
        so_blank = _so(db, retired)
        so_named = _so(db, retired, debtor_name="BUYER-1 (DO NAME)")
        order = _order(db, retired)
        contact = _contact(db, retired)

        result = customer_merge.apply(db, run_id="run-1", codes=[code])

        assert [g.survivor_id for g in result.merged] == [survivor]
        assert not _exists(db, retired)
        for so in (so_blank, so_named):
            assert _scalar(db, "SELECT customer_id::text FROM sales_orders WHERE id = :i", i=so) == survivor
        assert _scalar(db, "SELECT debtor_name FROM sales_orders WHERE id = :i", i=so_blank) == "BUYER-1"
        assert (
            _scalar(db, "SELECT debtor_name FROM sales_orders WHERE id = :i", i=so_named)
            == "BUYER-1 (DO NAME)"
        )
        assert _scalar(db, "SELECT customer_id::text FROM orders WHERE id = :i", i=order) == survivor
        assert _scalar(db, "SELECT debtor_name FROM orders WHERE id = :i", i=order) == "BUYER-1"
        assert (
            _scalar(db, "SELECT customer_id::text FROM customer_contacts WHERE id = :i", i=contact)
            == survivor
        )
        # survivor keeps its name, fills its blanks
        row = db.get(Customer, survivor)
        db.refresh(row)
        assert row.customer_name == "CASH SALES"
        assert row.email == "buyer@example.invalid"

    def test_retired_names_become_aliases(self, db):
        code = _code("AL")
        survivor = _seed(db, code, "CASH SALES", OLD)
        _seed(db, code, "BUYER-1", NEW)
        _seed(db, code, "BUYER-2", MID)
        customer_merge.apply(db, run_id="run-al", codes=[code])
        names = sorted(
            a.alias_name for a in db.query(CustomerAlias).filter(CustomerAlias.customer_id == survivor)
        )
        assert names == ["BUYER-1", "BUYER-2"]

    def test_merge_map_and_audit_row_per_retired_id(self, db):
        code = _code("MM")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW)
        customer_merge.apply(db, run_id="run-mm", codes=[code])
        maps = db.query(CustomerMergeMap).filter(CustomerMergeMap.run_id == "run-mm").all()
        assert [(str(m.survivor_id), str(m.retired_id)) for m in maps] == [(survivor, retired)]
        assert maps[0].retired_row["customer_name"] == "BUYER-1"
        audit = _scalar(
            db,
            "SELECT count(*) FROM audit_logs WHERE entity_type = 'customer' AND entity_id = :s "
            "AND action = 'CUSTOMER_MERGE' AND old_values->>'id' = :r",
            s=survivor,
            r=retired,
        )
        assert audit == 1

    def test_respond_contact_linked_to_both_keeps_one_primary_link(self, db):
        code = _code("RC")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW)
        contact = _respond_contact(db)
        _link(db, contact, survivor, primary=False)
        _link(db, contact, retired, primary=True)
        customer_merge.apply(db, run_id="run-rc", codes=[code])
        rows = db.execute(
            text("SELECT customer_id::text, is_primary FROM respond_contact_customers WHERE contact_id = :c"),
            {"c": contact},
        ).all()
        assert [(r[0], r[1]) for r in rows] == [(survivor, True)]

    def test_second_main_contact_becomes_stakeholder(self, db):
        code = _code("MC")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW)
        keep = _contact(db, survivor, role="main")
        demoted = _contact(db, retired, role="main")
        customer_merge.apply(db, run_id="run-mc", codes=[code])
        roles = dict(
            db.execute(
                text("SELECT id::text, contact_role FROM customer_contacts WHERE customer_id = :s"),
                {"s": survivor},
            ).all()
        )
        assert roles == {keep: "main", demoted: "stakeholder"}

    def test_retired_main_contact_stays_main_when_survivor_has_none(self, db):
        code = _code("MN")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW)
        main = _contact(db, retired, role="main")
        customer_merge.apply(db, run_id="run-mn", codes=[code])
        assert _scalar(db, "SELECT contact_role FROM customer_contacts WHERE id = :i", i=main) == "main"
        assert (
            _scalar(db, "SELECT customer_id::text FROM customer_contacts WHERE id = :i", i=main) == survivor
        )

    def test_item_code_map_collision_keeps_the_survivors_mapping(self, db):
        code = _code("IC")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW)
        p1, p2 = _product(db), _product(db)
        kept = _code_map(db, survivor, "CUST-ITEM-1", p1)
        _code_map(db, retired, "CUST-ITEM-1", p2)
        moved = _code_map(db, retired, "CUST-ITEM-2", p2)
        customer_merge.apply(db, run_id="run-ic", codes=[code])
        db.expire_all()
        rows = {
            m.customer_code: str(m.id)
            for m in db.query(CustomerItemCodeMap).filter(CustomerItemCodeMap.customer_id == survivor)
        }
        assert rows == {"CUST-ITEM-1": kept, "CUST-ITEM-2": moved}

    def test_integration_refs_one_per_row_code_form_wins(self, db):
        code = _code("IR")
        autokey = _seed(db, code, "NAME-A", OLD)
        _ref(db, autokey, "AED_SORENTO:1003")
        code_form = _seed(db, code, "NAME-B", NEW)
        _ref(db, code_form, f"foundryx_service_s51:{code}")
        customer_merge.apply(db, run_id="run-ir", codes=[code])
        refs = db.execute(
            text(
                "SELECT entity_id, source_ref FROM integration_references "
                "WHERE entity_type = 'customers' AND entity_id IN (:a, :b)"
            ),
            {"a": autokey, "b": code_form},
        ).all()
        assert [(r[0], r[1]) for r in refs] == [(code_form, f"foundryx_service_s51:{code}")]

    def test_the_only_linked_row_survives_even_when_newer(self, db):
        code = _code("RT")
        older = _seed(db, code, "NAME-A", OLD)
        linked = _seed(db, code, "NAME-B", NEW)
        _ref(db, linked, f"AED_SORENTO:{code}")
        result = customer_merge.apply(db, run_id="run-rt", codes=[code])
        assert result.merged[0].survivor_id == linked
        assert not _exists(db, older)

    def test_conflict_group_is_skipped_without_a_resolution(self, db):
        code = _code("SK")
        survivor = _seed(db, code, "NAME-A", OLD, phone_number="+60-1")
        retired = _seed(db, code, "NAME-B", NEW, phone_number="+60-2")
        result = customer_merge.apply(db, run_id="run-sk", codes=[code])
        assert result.merged == []
        assert [g.survivor_id for g in result.skipped] == [survivor]
        assert _exists(db, retired)

    def test_conflict_group_merges_with_the_owners_keep_row(self, db):
        code = _code("KP")
        survivor = _seed(db, code, "NAME-A", OLD, phone_number="+60-1")
        retired = _seed(db, code, "NAME-B", NEW, phone_number="+60-2")
        resolutions = {(DEFAULT_COMPANY_ID, code.lower()): retired}
        result = customer_merge.apply(db, run_id="run-kp", codes=[code], resolutions=resolutions)
        assert [g.survivor_id for g in result.merged] == [survivor]
        row = db.get(Customer, survivor)
        db.refresh(row)
        assert row.phone_number == "+60-2"  # the owner kept the retired row's value
        assert row.customer_name == "NAME-A"  # identity fields never move

    def test_second_apply_is_a_no_op(self, db):
        code = _code("ID")
        _seed(db, code, "NAME-A", OLD)
        _seed(db, code, "NAME-B", NEW)
        customer_merge.apply(db, run_id="run-id1", codes=[code])
        again = customer_merge.apply(db, run_id="run-id2", codes=[code])
        assert again.merged == [] and again.skipped == []

    def test_other_company_untouched(self, db, company_b):
        code = _code("CO")
        _seed(db, code, "NAME-A", OLD)
        _seed(db, code, "NAME-B", NEW)
        mch_a = _seed(db, code, "NAME-A", OLD, company_id=company_b)
        customer_merge.apply(db, run_id="run-co", codes=[code], company_id=DEFAULT_COMPANY_ID)
        assert _exists(db, mch_a)


# ================================================================================= revert
class TestRevert:
    def test_revert_restores_rows_links_and_values(self, db):
        code = _code("RV")
        survivor = _seed(db, code, "CASH SALES", OLD)
        retired = _seed(db, code, "BUYER-1", NEW, email="b@example.invalid")
        _ref(db, retired, "AED_SORENTO:2001")
        so_blank = _so(db, retired)
        keep_main = _contact(db, survivor, role="main")
        demoted = _contact(db, retired, role="main")
        contact = _respond_contact(db)
        _link(db, contact, survivor)
        dup_link = _link(db, contact, retired, primary=True)

        before_aliases = db.query(CustomerAlias).count()
        customer_merge.apply(db, run_id="run-rv", codes=[code])
        customer_merge.revert(db, "run-rv")

        assert _exists(db, retired)
        row = db.get(Customer, retired)
        db.refresh(row)
        assert row.customer_name == "BUYER-1" and row.email == "b@example.invalid"
        surv = db.get(Customer, survivor)
        db.refresh(surv)
        assert surv.email is None
        assert _scalar(db, "SELECT customer_id::text FROM sales_orders WHERE id = :i", i=so_blank) == retired
        assert _scalar(db, "SELECT debtor_name FROM sales_orders WHERE id = :i", i=so_blank) is None
        assert _scalar(db, "SELECT contact_role FROM customer_contacts WHERE id = :i", i=demoted) == "main"
        assert (
            _scalar(db, "SELECT customer_id::text FROM customer_contacts WHERE id = :i", i=demoted)
            == retired
        )
        assert _scalar(db, "SELECT contact_role FROM customer_contacts WHERE id = :i", i=keep_main) == "main"
        assert (
            _scalar(db, "SELECT customer_id::text || is_primary FROM respond_contact_customers WHERE id = :i", i=dup_link)
            == f"{retired}true"
        )
        assert (
            _scalar(
                db,
                "SELECT source_ref FROM integration_references WHERE entity_type = 'customers' AND entity_id = :e",
                e=retired,
            )
            == "AED_SORENTO:2001"
        )
        assert db.query(CustomerAlias).count() == before_aliases
        assert all(
            m.reverted_at is not None
            for m in db.query(CustomerMergeMap).filter(CustomerMergeMap.run_id == "run-rv")
        )

    def test_revert_after_the_index_was_created_drops_it_first(self, db):
        db.execute(text("DELETE FROM customers WHERE id IN (SELECT id FROM customers c WHERE EXISTS ("
                        "SELECT 1 FROM customers d WHERE d.company_id = c.company_id AND d.id <> c.id "
                        "AND lower(btrim(d.customer_code)) = lower(btrim(c.customer_code))))"))
        code = _code("RI")
        _seed(db, code, "NAME-A", OLD)
        retired = _seed(db, code, "NAME-B", NEW)
        customer_merge.apply(db, run_id="run-ri", codes=[code])
        customer_merge.create_unique_index(db)
        customer_merge.revert(db, "run-ri")
        assert _exists(db, retired)

    def test_revert_twice_is_a_no_op(self, db):
        code = _code("R2")
        _seed(db, code, "NAME-A", OLD)
        retired = _seed(db, code, "NAME-B", NEW)
        customer_merge.apply(db, run_id="run-r2", codes=[code])
        customer_merge.revert(db, "run-r2")
        customer_merge.revert(db, "run-r2")
        assert _scalar(db, "SELECT count(*) FROM customers WHERE id = :i", i=retired) == 1


# ============================================================================== R13 index
class TestUniqueIndex:
    def test_refused_while_duplicates_remain(self, db):
        code = _code("UX")
        _seed(db, code, "NAME-A", OLD)
        _seed(db, code, "NAME-B", NEW)
        with pytest.raises(customer_merge.DuplicatesRemain):
            customer_merge.create_unique_index(db)

    def test_created_once_clean_and_then_enforced(self, db):
        db.execute(text("DELETE FROM customers WHERE id IN (SELECT id FROM customers c WHERE EXISTS ("
                        "SELECT 1 FROM customers d WHERE d.company_id = c.company_id AND d.id <> c.id "
                        "AND lower(btrim(d.customer_code)) = lower(btrim(c.customer_code))))"))
        customer_merge.create_unique_index(db)
        code = _code("UQ")
        _seed(db, code, "NAME-A", OLD)
        with pytest.raises(Exception):
            with db.begin_nested():
                _seed(db, f" {code.lower()}", "NAME-B", NEW)
