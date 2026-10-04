"""Perf round 5 - the batch memo, TDD.

The replay profiler (a 1,000-record / ~4,100-line SO batch against the lane
DB) found the ladder resolving the SAME product ref twice per line (once for
itself, once again for `_capture_plan_exception_before`), a fresh
agent/customer-segment read per DOCUMENT even though a batch holds a few
hundred distinct agents/customers, and `dependent_probe.referrers_of`'s
catalogue query running once per DELETED line instead of once per batch.

Each test below pins ONE of those - by counting the actual SQL, never by
asserting on internal cache state, so a future refactor of HOW the memo is
built stays covered as long as the STATEMENT COUNT it exists to cut does not
regress.

No new substrate - reuses `env` from `tests.test_ingest_documents`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import event, text

from app.models.inventory import Warehouse

from tests._pg_fixture import unique_code
from tests.test_ingest_documents import (
    INGEST_SO,
    MARKER,
    _code_of_ref,
    _so_line,
    _so_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]


class TestProductRefNeverResolvedByIngest:
    def test_a_batch_never_reads_a_product_reference(self, env, monkeypatch):
        """Owner rulings (3 Oct): products match by company + code only, so a
        batch makes no `integration_references` lookup for `products` at all
        (this was "resolves once per batch" before the ref lookup went away)."""
        import app.services.integration_reference_service as irs

        code = _code_of_ref(env, env.product_ref)
        calls: list[tuple[str, str]] = []
        real_resolve = irs.IntegrationReferenceService.resolve

        def _counting(self, *, entity_type, source_ref, source_system=irs.DEFAULT_SOURCE_SYSTEM):
            calls.append((entity_type, source_ref))
            return real_resolve(
                self, entity_type=entity_type, source_ref=source_ref, source_system=source_system
            )

        monkeypatch.setattr(irs.IntegrationReferenceService, "resolve", _counting)

        line_a = _so_line(env, product_code=code, qty_ordered=5)
        line_b = _so_line(env, product_ref=line_a["product_ref"], product_code=code, qty_ordered=3)
        record = _so_record(env, lines=[line_a, line_b])

        res = env.post(INGEST_SO, [record])

        assert res.json()["records"][0]["outcome"] == "created", res.text
        assert [c for c in calls if c[0] == "products"] == [], calls


class TestBackCreatedCustomerReuse:
    def test_a_back_created_customer_code_reused_by_a_second_document_creates_one_row(
        self, env
    ):
        code = unique_code(MARKER)
        name = f"{MARKER} Shared Co"
        rec1 = _so_record(env, customer_code=code, customer_name=name)
        rec2 = _so_record(env, customer_code=code, customer_name=name)

        res = env.post(INGEST_SO, [rec1, rec2])

        body = res.json()
        assert body["summary"]["created"] == 2, body
        entry1, entry2 = body["records"]
        assert entry1["outcome"] == "created", entry1
        assert entry2["outcome"] == "created", entry2
        assert "customer_created" in entry1.get("warnings", []), entry1
        # Current behaviour, kept exactly as it is today: the second
        # document's own code+name lookup already finds the row the first
        # document just flushed, so it neither back-creates nor warns again.
        assert "customer_created" not in entry2.get("warnings", []), entry2

        count = env.db.execute(
            text("SELECT count(*) FROM customers WHERE customer_code = :c"), {"c": code}
        ).scalar()
        assert count == 1


class TestMemoDoesNotLeakAcrossResolverInstances:
    def test_two_batches_two_companies_same_code_resolve_to_their_own_row(self, env):
        """Each `env.post()` call builds a FRESH `DocumentIngestService` (one
        per request, per `ingest.py`'s route) - the realistic shape of "two
        resolver instances", exercised through the public ingest surface
        rather than by constructing the service directly and fighting the
        ambient company-scope machinery a real request always sets up first
        (`resolve_company_anchor`).
        """
        code = unique_code(MARKER)
        wh_a = Warehouse(
            id=str(uuid.uuid4()), warehouse_code=code, warehouse_name="A",
            company_id=env.company_a,
        )
        wh_b = Warehouse(
            id=str(uuid.uuid4()), warehouse_code=code, warehouse_name="B",
            company_id=env.company_b,
        )
        env.db.add_all([wh_a, wh_b])
        env.db.commit()

        record_a = _so_record(env, lines=[_so_line(env, warehouse_code=code)])
        res_a = env.post(INGEST_SO, [record_a])

        product_b_ref = env.link_product(env.company_b)
        product_b_id = env.refs_for(env.company_b).resolve(
            entity_type="products", source_ref=product_b_ref
        )
        product_b_code = env.db.execute(
            text("SELECT product_code FROM products WHERE id = :i"), {"i": str(product_b_id)}
        ).scalar()
        record_b = _so_record(
            env,
            lines=[
                _so_line(
                    env,
                    product_ref=product_b_ref,
                    product_code=product_b_code,
                    warehouse_code=code,
                )
            ],
        )
        res_b = env.post(INGEST_SO, [record_b], company_code=env.company_b_code)

        assert res_a.json()["records"][0]["outcome"] == "created", res_a.text
        assert res_b.json()["records"][0]["outcome"] == "created", res_b.text

        header_a = env.header("sales_orders", record_a["source_ref"])
        # record_b was pushed under company_b - env.header()'s default anchor
        # (company_a) would resolve nothing for it (plan D14: resolve() on a
        # scoped type only sees its own anchor's rows), reading back as None.
        header_b = env.header(
            "sales_orders", record_b["source_ref"], company_id=env.company_b
        )
        line_a_row = env.so_lines(header_a["id"])[0]
        line_b_row = env.so_lines(header_b["id"])[0]

        assert str(line_a_row["warehouse_id"]) == str(wh_a.id)
        assert str(line_b_row["warehouse_id"]) == str(wh_b.id)


class TestAgentDemandClassMemoisedPerBatch:
    def test_two_documents_naming_the_same_agent_read_its_demand_class_once(self, env):
        calls: list[str] = []
        connection = env.db.get_bind()

        def _capture(conn, cursor, statement, *_a, **_kw):
            calls.append(statement.lower())

        event.listen(connection, "before_cursor_execute", _capture)
        try:
            rec1 = _so_record(env, sales_agent_ref=env.agent_ref)
            rec2 = _so_record(env, sales_agent_ref=env.agent_ref)
            res = env.post(INGEST_SO, [rec1, rec2])
        finally:
            event.remove(connection, "before_cursor_execute", _capture)

        assert res.json()["summary"]["created"] == 2, res.text
        agent_class_reads = [
            s for s in calls
            if "sales_agents" in s and "demand_class" in s and "select" in s
        ]
        assert len(agent_class_reads) == 1, agent_class_reads
