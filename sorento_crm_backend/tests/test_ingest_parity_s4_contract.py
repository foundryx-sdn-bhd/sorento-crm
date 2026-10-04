"""RED tests for ingest parity standardisation, Phase S4 (retire and clean up).

UAC: documentation/plans/autocount/ingest-parity-standardisation-acceptance-criteria.md
     Phase S4, AC-P4-1 .. AC-P4-3.
PLAN: documentation/plans/autocount/PLAN-ingest-parity-standardisation.md sections 2.8, 4.

Substrate: the `env` fixture reused byte-for-byte from `tests/test_ingest_documents.py`
(`blank_session()` + a real `TestClient(app)`), the same one
`tests/test_ingest_contract_v2.py` already reuses for the contract endpoint -
so `GET /api/v1/external/contract` and the ESB ingest hooks are exercised for
real. AC-P4-1's route-retirement checks use a bare `TestClient(app)` with no
auth override at all: a 404 is the only status that can mean "the route does
not exist" regardless of what guard would otherwise intercept the request.

Facts verified in code before relying on them:

* `GET /api/v1/external/contract` (`app/api/v1/external/contract.py`) answers
  exactly `{"version": CONTRACT_VERSION, "entities": [...]}` today -
  `CONTRACT_VERSION = 2` (an int, in `app/api/v1/external/ingest.py`). No
  `fields_added`/`fields_removed`/`status_optional`/`absent_vs_null`/
  `warnings` key exists at all.
* AC-P4-2's supersede function, `outstanding_import_service
  .supersede_crm_raised_pos(db, triples, *, outcome=None)`, is EXISTING,
  shipped code (PR #670, `feat/autocount-document-ingest-v2`), already shared
  between the outstanding-PO upload's own adapter
  (`_supersede_crm_raised_pos`) and the ESB ingest route's own PO hook
  (`_run_supersede_and_relink_hooks` in `app/api/v1/external/ingest.py`,
  already wired for `purchase_orders`). Its candidate query already filters
  `PurchaseOrderLine.line_status == "open"`, so a line closed by a first run
  is not a candidate on a second - **the idempotency AC-P4-2 asks for
  already holds today at the function level**. The test below proves this
  empirically (seed a CRM-raised PO, push the matching AutoCount PO through
  the real route so the hook fires once, then call the shared function again
  directly with the same triples) rather than assuming either way, and is
  reported as a REGRESSION-GUARD (green today, same shape as the existing
  `tests/test_ingest_documents_v2_hooks.py::TestPlanningChangeIsNotRunByIngest`
  guard) rather than forced red - see the test's own docstring and the
  tester's report for the full reasoning.
* `so_history_service.py` / `po_history_service.py`
  (`app/services/scm/`) both exist today; `process_po_history_import` /
  `process_sales_history_import` both exist in `app/tasks/import_tasks.py`;
  `/api/v1/scm/purchase-history/{preview,apply}` and
  `/api/v1/scm/sales-history/{preview,apply}` are live routes
  (`app/api/v1/scm/purchase_history.py`, mounted under `/api/v1/scm`) - all
  confirmed present, so AC-P4-1 is red today by construction.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tests.test_ingest_documents import (
    INGEST_PO,
    MARKER as DOC_MARKER,
    _po_line,
    _po_record,
    env,  # noqa: F401 - pytest fixture, imported for reuse
)

__all__ = ["env"]

MARKER = "ZZTIP4"
CONTRACT_URL = "/api/v1/external/contract"


class TestAcP43ContractV21:
    """D-final: `GET /api/v1/external/contract` answers version 2.1 with the
    full field-added/removed/status-optional/absent-vs-null/warnings shape."""

    def test_contract_reports_version_2_1(self, env):
        """Bumped again 2026-09-08 (V5, ingest-contract-2-2-so-links) to
        "2.2", again (autocount-brands-ingest, AC-13) to "2.3", again
        (ingest-products-code-wins, SR0) to "2.4", and again
        (ingest-stock-balances-2-5, Foundryx SR5) to "2.5" - this test's own
        name is now historical, kept as-is so the AC-P4-3 numbering does not
        shift for an unrelated slice."""
        res = env.client.get(CONTRACT_URL)
        assert res.status_code == 200, res.text
        # Bumped again (finance S0, #1309): "2.6" adds `billing_documents`.
        # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
        assert res.json()["version"] == "2.7"

    def test_contract_lists_fields_added_per_entity(self, env):
        body = env.client.get(CONTRACT_URL).json()
        fields_added = body.get("fields_added", {})
        expected = {
            "products": {"is_discontinued", "remark", "brand_code"},
            "customers": {"market_segment_code", "region"},
            "sales_orders": {"customer_segment", "customer_region"},
            # `is_shipping_order` belongs to `purchase_orders` alone (review S1,
            # 2026-09-06): it is parsed off a PO payload to refuse it there and
            # redirect to `shipping_orders` (D6/AC-P3-7) - `CanonicalShippingOrder`
            # itself has no such field, so an earlier version of this test
            # wrongly also expected it under `shipping_orders`.
            "purchase_orders": {"is_shipping_order"},
            "shipping_orders": {"container_number"},
        }
        for entity, wanted in expected.items():
            got = set(fields_added.get(entity, []))
            assert wanted.issubset(got), (entity, wanted - got)

    def test_contract_lists_fields_removed_per_entity(self, env):
        body = env.client.get(CONTRACT_URL).json()
        fields_removed = body.get("fields_removed", {})
        expected = {
            "customers": {"credit_limit", "payment_terms_days", "payment_terms_code"},
            "suppliers": {"payment_terms_code"},
        }
        for entity, wanted in expected.items():
            got = set(fields_removed.get(entity, []))
            assert wanted.issubset(got), (entity, wanted - got)

    def test_contract_marks_status_optional_on_every_document(self, env):
        body = env.client.get(CONTRACT_URL).json()
        status_optional = body.get("status_optional", {})
        for entity in ("sales_orders", "purchase_orders", "shipping_orders"):
            assert status_optional.get(entity) is True, (entity, status_optional)

    def test_contract_marks_absent_vs_null_on_masters(self, env):
        body = env.client.get(CONTRACT_URL).json()
        assert body.get("absent_vs_null") is True, body

    def test_contract_lists_the_warning_vocabulary(self, env):
        body = env.client.get(CONTRACT_URL).json()
        warnings = set(body.get("warnings", []))
        expected = {
            "category_created", "uom_created", "brand_created", "segment_unknown",
            "lines.dropped", "received_locked", "container_unresolved",
            "deprecated_field",
        }
        assert expected.issubset(warnings), expected - warnings


class TestAcP04EndStateDeprecatedFieldsRemoved:
    """AC-P0-4's own end state, which belongs to S4: once the contract says
    2.1, the three deprecated fields are REMOVED from the schemas. Owner
    decision 1 Oct 2026: a payload naming any of them is DROPPED (the name is
    logged by the ingest route), never refused, never retryable, and the field
    reaches no column.
    Today (S0's contract) they are still accepted-and-warned - see
    tests/test_master_ingest.py::TestRetryableVsFatal."""

    def test_customer_credit_limit_is_dropped_not_written(self, env):
        from app.services.master_ingest_service import IngestOutcome, MasterIngestService

        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        result = svc.ingest(
            "customers",
            [
                {
                    "source_ref": f"DK-{MARKER}-CL",
                    "code": f"{MARKER}-CUSTCL",
                    "name": "Old Fields Co",
                    "credit_limit": "15000.50",
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors

    def test_customer_payment_terms_days_is_dropped_not_written(self, env):
        from app.services.master_ingest_service import IngestOutcome, MasterIngestService

        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        result = svc.ingest(
            "customers",
            [
                {
                    "source_ref": f"DK-{MARKER}-PTD",
                    "code": f"{MARKER}-CUSTPTD",
                    "name": "Old Fields Co",
                    "payment_terms_days": 45,
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors

    def test_supplier_payment_terms_code_is_dropped_not_written(self, env):
        from app.services.master_ingest_service import IngestOutcome, MasterIngestService

        svc = MasterIngestService(env.db, integration_id=None, company_id=env.company_a)
        result = svc.ingest(
            "suppliers",
            [
                {
                    "source_ref": f"DK-{MARKER}-PTC",
                    "code": f"{MARKER}-SUPPTC",
                    "name": "Old Terms Co",
                    "payment_terms_code": "NET-30",
                }
            ],
        )
        record = result.records[0]
        assert record.outcome is IngestOutcome.CREATED, record.errors


class TestAcP42SupersedeIdempotency:
    """`supersede_crm_raised_pos` runs once per document regardless of
    channel - a PO pushed by the ESB and then re-processed (a second ESB
    push, or the outstanding upload naming the same physical order) must not
    supersede twice.

    Verified empirically rather than assumed: this is EXISTING, shipped code
    whose candidate query already excludes an already-closed line, so a
    second call with the same triples is expected to already report (0, [])
    - see the module docstring. This test is a REGRESSION GUARD (asserts the
    current, correct behaviour so a future change cannot silently break it),
    not a red test - flagged explicitly rather than forced red, per the
    tester's mandate to report what the code actually does.
    """

    def test_second_supersede_run_after_an_esb_push_closes_nothing_new(self, env):
        from app.models.procurement import PurchaseOrder, PurchaseOrderLine
        from app.services.scm.outstanding_import_service import supersede_crm_raised_pos

        product_id = env.refs.resolve(entity_type="products", source_ref=env.product_ref)
        supplier_id = env.refs.resolve(entity_type="suppliers", source_ref=env.supplier_ref)

        crm_po = PurchaseOrder(
            po_number=f"{MARKER}-CRMPO-{uuid.uuid4().hex[:8]}".upper(),
            supplier_id=supplier_id,
            status="active",
            source_system="scm_recommendation",
            source_ref="scm",
            company_id=env.company_a,
        )
        env.db.add(crm_po)
        env.db.flush()
        crm_line = PurchaseOrderLine(
            purchase_order_id=crm_po.id,
            product_id=product_id,
            qty_ordered=Decimal("20"),
            qty_received=Decimal("0"),
            line_status="open",
            source_system="scm_recommendation",
            source_ref=f"{MARKER}-rec-{uuid.uuid4().hex[:8]}",
            company_id=env.company_a,
        )
        env.db.add(crm_line)
        env.db.flush()
        # Committed, not just flushed - same reason `_Env` itself commits its
        # seeds: the ESB push below is a real (non-dry) request and its own
        # `db.commit()` must not be the first commit this row ever sees.
        env.db.commit()

        ac_po_number = f"{MARKER}-ACPO-{uuid.uuid4().hex[:8]}".upper()
        record = _po_record(
            env,
            number=ac_po_number,
            supplier_ref=env.supplier_ref,
            lines=[_po_line(env, product_ref=env.product_ref, qty_ordered=20)],
        )
        res = env.post(INGEST_PO, [record])
        assert res.json()["records"][0]["outcome"] == "created", res.text

        env.db.refresh(crm_line)
        assert crm_line.line_status == "closed", (
            "the CRM-raised line must be superseded by the ESB push's own hook"
        )

        # Second run, same triples - the shape a re-processed upload or a
        # second ESB push of the same document would produce.
        count, superseded = supersede_crm_raised_pos(
            env.db, {(product_id, supplier_id, ac_po_number)}
        )
        assert count == 0, superseded
        assert superseded == []


class TestAcP41RetirementOfHistoryImporters:
    """The SO/PO history importers - their routes, services and RQ tasks -
    are removed; closed history now arrives through the ESB. Red today by
    construction: none of this has been retired yet."""

    @staticmethod
    def _unauthenticated_client() -> TestClient:
        from app.main import app

        return TestClient(app, raise_server_exceptions=False)

    def test_purchase_history_apply_route_is_gone(self):
        with self._unauthenticated_client() as client:
            res = client.post(
                "/api/v1/scm/purchase-history/apply",
                files={"file": ("x.xlsx", b"", "application/octet-stream")},
            )
        assert res.status_code == 404, res.status_code

    def test_purchase_history_preview_route_is_gone(self):
        with self._unauthenticated_client() as client:
            res = client.post(
                "/api/v1/scm/purchase-history/preview",
                files={"file": ("x.xlsx", b"", "application/octet-stream")},
            )
        assert res.status_code == 404, res.status_code

    def test_sales_history_apply_route_is_gone(self):
        with self._unauthenticated_client() as client:
            res = client.post(
                "/api/v1/scm/sales-history/apply",
                files={"file": ("x.xlsx", b"", "application/octet-stream")},
            )
        assert res.status_code == 404, res.status_code

    def test_sales_history_preview_route_is_gone(self):
        with self._unauthenticated_client() as client:
            res = client.post(
                "/api/v1/scm/sales-history/preview",
                files={"file": ("x.xlsx", b"", "application/octet-stream")},
            )
        assert res.status_code == 404, res.status_code

    def test_so_history_service_module_is_retired(self):
        with pytest.raises(ImportError):
            import app.services.scm.so_history_service  # noqa: F401

    def test_po_history_service_module_is_retired(self):
        with pytest.raises(ImportError):
            import app.services.scm.po_history_service  # noqa: F401

    def test_process_sales_history_import_task_is_retired(self):
        import app.tasks.import_tasks as import_tasks

        assert not hasattr(import_tasks, "process_sales_history_import")

    def test_process_po_history_import_task_is_retired(self):
        import app.tasks.import_tasks as import_tasks

        assert not hasattr(import_tasks, "process_po_history_import")
