"""CUSTOMER-CODE-IDENTITY (re-scoped, owner 30 Sep 2026): the customer name a
sales order was issued under is stored on the order (`sales_orders.debtor_name`)
and is what the SO-facing screens show, the master name only as the fallback.

UAC: documentation/plans/master-data/customer-code-identity-acceptance-criteria.md

  AC-01  an SO push with a `customer_name` stores it on the order; the master
         `customers.customer_name` is never written from a document
  AC-02  a re-push with a new name updates the order's name; a push without
         one leaves the stored name alone
  AC-03  the SCM sales order serializer (list + detail) prints the order's name
         first, the master's for an order that carries none; the list search
         matches it
  AC-04  `customer_label.CUSTOMER_LABEL_SQL` (reorder demand popovers, container
         requests, trend drill) prints the order's name, then the master's, then
         the debtor code
  AC-05  `so_outstanding_rows` (chatbot, MCP) does the same

Every test seeds its own chain on the blank scratch schema (`tests._pg_fixture`).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners

from tests._pg_fixture import blank_session, unique_code
from tests.test_ingest_documents import (
    INGEST_SO,
    MARKER,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


def _customer(db, company_id: str, *, code: str, name: str) -> Customer:
    row = Customer(customer_code=code, customer_name=name, company_id=company_id)
    db.add(row)
    db.flush()
    return row


def _master_name(db, customer_id: str) -> str:
    return db.execute(
        text("SELECT customer_name FROM customers WHERE id = :id"), {"id": customer_id}
    ).scalar()


# ================================================================ ingest (AC-01/02)
class TestSoIngestStoresTheOrderName:
    def test_a_changed_debtor_name_is_stored_on_the_order_not_the_master(self, env):
        """AC-01: 300-1001 pushed as "MODERNMED SDN BHD" links to the 300-1001 row
        the CRM holds and carries "MODERNMED SDN BHD" itself."""
        code = unique_code(MARKER)
        stored = _customer(env.db, env.company_a, code=code, name="1 LIVING DEPOT SDN BHD")

        res = env.post(
            INGEST_SO, [_so_record(env, customer_code=code, customer_name="MODERNMED SDN BHD")]
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        header = env.header("sales_orders", entry["source_ref"])
        assert str(header["customer_id"]) == str(stored.id)
        assert header["debtor_name"] == "MODERNMED SDN BHD"
        assert _master_name(env.db, str(stored.id)) == "1 LIVING DEPOT SDN BHD"

    def test_a_re_push_with_a_new_name_updates_the_order_name_only(self, env):
        """AC-02."""
        code = unique_code(MARKER)
        stored = _customer(env.db, env.company_a, code=code, name="ALPHA SDN BHD")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"

        for name in ("Beta Sdn Bhd", "  Gamma Sdn Bhd "):
            res = env.post(
                INGEST_SO, [_so_record(env, ref=ref, customer_code=code, customer_name=name)]
            )
            assert res.status_code == 200, res.text

        assert env.header("sales_orders", ref)["debtor_name"] == "Gamma Sdn Bhd"
        assert _master_name(env.db, str(stored.id)) == "ALPHA SDN BHD"

    def test_a_push_without_a_name_leaves_the_stored_order_name_alone(self, env):
        """AC-02."""
        code = unique_code(MARKER)
        _customer(env.db, env.company_a, code=code, name="ALPHA SDN BHD")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code, customer_name="Beta")])
        env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code)])
        assert env.header("sales_orders", ref)["debtor_name"] == "Beta"

    def test_an_identical_re_push_fills_a_missing_name_on_an_existing_order(self, env):
        """Owner's rollout ("repush all" from the shared service after deploy): an
        order ingested BEFORE the column existed carries no name; the re-push of the
        very same payload writes it even though nothing else differs - the update
        path sets every header value it was sent, it never skips on an empty diff."""
        code = unique_code(MARKER)
        _customer(env.db, env.company_a, code=code, name="ALPHA SDN BHD")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        record = _so_record(env, ref=ref, customer_code=code, customer_name="Issued Under")
        assert env.post(INGEST_SO, [record]).status_code == 200
        env.db.execute(
            text("UPDATE sales_orders SET debtor_name = NULL WHERE source_ref = :r"), {"r": ref}
        )
        assert env.header("sales_orders", ref)["debtor_name"] is None

        res = env.post(INGEST_SO, [record])

        assert res.status_code == 200, res.text
        assert res.json()["records"][0]["outcome"] == "updated"
        assert env.header("sales_orders", ref)["debtor_name"] == "Issued Under"

    def test_an_adopted_ref_less_order_takes_the_name_too(self, env):
        """A row the xlsx era created (no integration reference) is adopted by its
        SO number on the first push and takes the pushed name like any update."""
        code = unique_code(MARKER)
        master = _customer(env.db, env.company_a, code=code, name="ALPHA SDN BHD")
        legacy = SalesOrder(
            so_number=f"{MARKER}-LEGACY-{uuid.uuid4().hex[:6]}",
            status="open",
            customer_id=str(master.id),
            company_id=env.company_a,
        )
        env.db.add(legacy)
        env.db.flush()
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"

        res = env.post(
            INGEST_SO,
            [_so_record(env, ref=ref, number=legacy.so_number, customer_code=code, customer_name="Adopted Name")],
        )

        assert res.status_code == 200, res.text
        entry = res.json()["records"][0]
        assert entry["outcome"] == "updated", entry
        assert str(entry["entity_id"]) == str(legacy.id)
        assert env.header("sales_orders", ref)["debtor_name"] == "Adopted Name"

    def test_the_dry_run_reports_the_name_change_and_persists_nothing(self, env):
        code = unique_code(MARKER)
        _customer(env.db, env.company_a, code=code, name="ALPHA SDN BHD")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code, customer_name="Beta")])

        res = env.post(
            INGEST_SO,
            [_so_record(env, ref=ref, customer_code=code, customer_name="Gamma")],
            dry_run=True,
        )

        assert res.status_code == 200, res.text
        diff = res.json()["records"][0]["diff"] or {}
        assert diff.get("debtor_name") == {"current": "Beta", "incoming": "Gamma"}, diff
        assert env.header("sales_orders", ref)["debtor_name"] == "Beta"


    def test_a_push_without_a_name_freezes_the_master_name_on_a_new_order(self, env):
        """DEBTOR-CODE-LINK: every SO carries the name it was issued under from the start, so
        a later rename of the customer (master push) never rewrites it on old orders."""
        code = unique_code(MARKER)
        master = _customer(env.db, env.company_a, code=code, name="NAME-AT-ISSUE")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"

        res = env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code)])
        assert res.status_code == 200, res.text
        env.db.execute(
            text("UPDATE customers SET customer_name = 'NAME-RENAMED' WHERE id = :i"),
            {"i": str(master.id)},
        )

        assert env.header("sales_orders", ref)["debtor_name"] == "NAME-AT-ISSUE"

    def test_a_push_without_a_name_fills_a_missing_order_name_from_the_master(self, env):
        code = unique_code(MARKER)
        _customer(env.db, env.company_a, code=code, name="NAME-MASTER")
        ref = f"{MARKER}-SO-{uuid.uuid4().hex[:8]}"
        env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code, customer_name="Issued")])
        env.db.execute(
            text("UPDATE sales_orders SET debtor_name = NULL WHERE source_ref = :r"), {"r": ref}
        )

        env.post(INGEST_SO, [_so_record(env, ref=ref, customer_code=code)])

        assert env.header("sales_orders", ref)["debtor_name"] == "NAME-MASTER"


class TestManualSoCreateStoresTheOrderName:
    def test_a_manual_so_carries_the_customer_name_it_was_created_under(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.numbering_service import NumberingService
        from app.services.scm.sales_order_service import SalesOrderService

        register_company_scope_listeners()
        with blank_session() as session:
            set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
            code = unique_code(MARKER)
            master = _customer(session, DEFAULT_COMPANY_ID, code=code, name="NAME-MANUAL")
            monkeypatch.setattr(
                NumberingService, "get_next_number", lambda self, *a, **k: unique_code("SO")
            )
            data = SimpleNamespace(
                customer_code=code,
                sales_agent_id=None,
                requested_delivery_date=None,
                order_type="stock",
                priority="normal",
                lines=[],
            )

            created = SalesOrderService(session).create(data, user_id=None)

            stored = session.execute(
                text("SELECT debtor_name FROM sales_orders WHERE id = :i"), {"i": str(created["id"])}
            ).scalar()
            assert stored == "NAME-MANUAL"
            assert _master_name(session, str(master.id)) == "NAME-MANUAL"


# ============================================================ screens (AC-03..05)
@pytest.fixture()
def db():
    register_company_scope_listeners()
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _so(db, *, customer_id, debtor_name=None, debtor_code=None) -> SalesOrder:
    so = SalesOrder(
        so_number=unique_code("SO"),
        status="open",
        customer_id=customer_id,
        debtor_name=debtor_name,
        debtor_code=debtor_code,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    return so


class TestSoScreensShowTheOrderName:
    def test_the_scm_serializer_prefers_the_order_name(self, db):
        """AC-03."""
        from app.services.scm.sales_order_service import SalesOrderService

        master = _customer(db, DEFAULT_COMPANY_ID, code=unique_code(MARKER), name="MASTER NAME")
        with_own = _so(db, customer_id=str(master.id), debtor_name="ORDER NAME")
        without = _so(db, customer_id=str(master.id), debtor_name="  ")

        svc = SalesOrderService(db)
        assert svc.serialize(with_own)["customer_name"] == "ORDER NAME"
        assert svc.serialize(without)["customer_name"] == "MASTER NAME"

    def test_the_scm_list_search_matches_the_order_name(self, db):
        """AC-03."""
        from app.services.scm.sales_order_service import SalesOrderService

        master = _customer(db, DEFAULT_COMPANY_ID, code=unique_code(MARKER), name="MASTER NAME")
        needle = f"ZZTNEEDLE{uuid.uuid4().hex[:6]}"
        so = _so(db, customer_id=str(master.id), debtor_name=f"{needle} Sdn Bhd")

        result = SalesOrderService(db).list(
            page=1, limit=20, sort=None, direction="asc", query=needle, status=None, priority=None
        )
        assert result["pagination"]["total"] == 1
        assert result["data"][0]["id"] == so.id
        assert result["data"][0]["customer_name"] == f"{needle} Sdn Bhd"

    def test_the_shared_sql_label_prefers_the_order_name(self, db):
        """AC-04."""
        from app.services.scm.customer_label import CUSTOMER_JOIN_ON, CUSTOMER_LABEL_SQL

        master = _customer(db, DEFAULT_COMPANY_ID, code=unique_code(MARKER), name="MASTER NAME")
        with_own = _so(db, customer_id=str(master.id), debtor_name="ORDER NAME")
        without = _so(db, customer_id=str(master.id))
        nobody = _so(db, customer_id=None, debtor_code="300-ZZT9")

        def label(so_id: str) -> str:
            return db.execute(
                text(
                    f"SELECT {CUSTOMER_LABEL_SQL} FROM sales_orders so "
                    f"LEFT JOIN customers c ON {CUSTOMER_JOIN_ON} WHERE so.id = :id"
                ),
                {"id": so_id},
            ).scalar()

        assert label(with_own.id) == "ORDER NAME"
        assert label(without.id) == "MASTER NAME"
        assert label(nobody.id) == "Debtor 300-ZZT9"

    def test_the_trend_drill_stays_one_row_per_customer_and_prints_the_latest_order_name(self):
        """AC-04: the label is per order now, so the drill groups by customer key and
        prints the name on the customer's most recent order instead of one row per
        spelling (code review S3).

        On the rolled-back REAL database (`pg_session`), not the scratch schema: the
        drill's SQL names `scm.reorder_recommendation` schema-qualified, which the
        scratch fixture's `search_path` pin cannot redirect."""
        from datetime import date, timedelta

        from app.services.scm.trajectory_service import trajectory_for_run
        from tests._mc_lookup_seed import product
        from tests._pg_fixture import pg_session

        register_company_scope_listeners()
        with pg_session() as db:
            db.execute(
                text(
                    "INSERT INTO companies (id, name, code) VALUES (:i, 'ZZT default', :c) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"i": DEFAULT_COMPANY_ID, "c": unique_code("CO")[:20]},
            )
            set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
            master = _customer(db, DEFAULT_COMPANY_ID, code=unique_code(MARKER), name="MASTER NAME")
            prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P")[:30])
            db.flush()
            run_id = str(uuid.uuid4())
            db.execute(
                text("INSERT INTO scm.reorder_run (id, status) VALUES (:i, 'completed')"),
                {"i": run_id},
            )
            db.execute(
                text(
                    "INSERT INTO scm.reorder_recommendation (id, run_id, product_id, company_id) "
                    "VALUES (:i, :r, :p, :c)"
                ),
                {"i": str(uuid.uuid4()), "r": run_id, "p": prod.id, "c": DEFAULT_COMPANY_ID},
            )
            today = date.today()
            for name, days_ago, qty in (("OLD SPELLING", 100, 7), ("NEW SPELLING", 70, 5)):
                so = _so(db, customer_id=str(master.id), debtor_name=name)
                so.order_date = today - timedelta(days=days_ago)
                db.add(
                    SalesOrderLine(
                        sales_order_id=so.id,
                        product_id=prod.id,
                        qty_ordered=qty,
                        qty_delivered=0,
                        line_status="open",
                        company_id=DEFAULT_COMPANY_ID,
                    )
                )
            db.flush()

            facts = trajectory_for_run(db, run_id, as_of=today)
            rows = facts["series"][f"{prod.id}:project"]["customers"]
            assert len(rows) == 1, rows
            assert rows[0]["customer_name"] == "NEW SPELLING"
            assert rows[0]["customer_key"] == str(master.id)
            assert rows[0]["qty"] == 12.0

    def test_so_outstanding_rows_prefer_the_order_name(self, db):
        """AC-05."""
        from app.services.order_service import so_outstanding_rows
        from tests._mc_lookup_seed import product

        master = _customer(db, DEFAULT_COMPANY_ID, code=unique_code(MARKER), name="MASTER NAME")
        prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P")[:30])
        for name in ("ORDER NAME", None):
            so = _so(db, customer_id=str(master.id), debtor_name=name)
            db.add(
                SalesOrderLine(
                    sales_order_id=so.id,
                    product_id=prod.id,
                    qty_ordered=5,
                    qty_delivered=1,
                    line_status="open",
                    company_id=DEFAULT_COMPANY_ID,
                )
            )
        db.flush()

        rows = so_outstanding_rows(db, customer_ids=[str(master.id)])
        assert sorted(r["customer"] for r in rows) == ["MASTER NAME", "ORDER NAME"]
