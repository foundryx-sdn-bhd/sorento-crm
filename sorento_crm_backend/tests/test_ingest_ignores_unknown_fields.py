"""Owner decision, 1 Oct 2026: the external ingest IGNORES fields it does not know.

The shared service adds a field when AutoCount grows one (SO-TRANSFERABLE was the case that
raised it). Until then `extra="forbid"` failed EVERY record of that entity on the first push
that carried the new key, so the two repos had to deploy in a fixed order. Now:

  * an unknown key is dropped, the record is ingested from the keys Sorento knows;
  * the unknown NAMES are logged once per request (never their values);
  * known fields keep their strict validation - a wrong type still fails the record.

Dropping is not writing: a key the schema does not declare reaches no column, so a payload
naming a CRM-owned annotation (`demand_class`, `internal_note` on a master) still cannot
touch it.
"""
from __future__ import annotations

import logging

import pytest

from app.schemas.canonical_documents import (
    CanonicalBillingDocument,
    CanonicalPurchaseOrder,
    CanonicalSalesOrder,
    CanonicalShippingOrder,
)
from app.schemas.canonical_masters import (
    CanonicalBrand,
    CanonicalCustomer,
    CanonicalProduct,
    CanonicalProductCategory,
    CanonicalSalesAgent,
    CanonicalSupplier,
    CanonicalUnitOfMeasure,
    CanonicalWarehouse,
)
from tests.test_ingest_documents import (  # noqa: F401  (`env` is a fixture)
    INGEST_SO,
    _so_line,
    _so_record,
    env,
)


def test_an_so_push_with_unknown_fields_is_ingested_and_the_extras_dropped(env, caplog):
    record = _so_record(
        env,
        lines=[_so_line(env, zz_future_line_field="secret-line-value")],
        zz_future_header_field="secret-header-value",
    )

    with caplog.at_level(logging.INFO):
        res = env.post(INGEST_SO, [record])

    assert res.status_code == 200, res.text
    entry = res.json()["records"][0]
    assert entry["outcome"] == "created", res.text
    header = env.header("sales_orders", record["source_ref"])
    assert header is not None
    assert "zz_future_header_field" not in header
    assert len(env.so_lines(header["id"])) == 1

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "zz_future_header_field" in logged
    assert "zz_future_line_field" in logged
    assert "secret-header-value" not in logged
    assert "secret-line-value" not in logged
    # Once per request, not once per record.
    unknown_lines = [r for r in caplog.records if "ingest.unknown_fields" in r.getMessage()]
    assert len(unknown_lines) == 1


def test_a_wrong_type_on_a_known_field_still_fails_the_record(env):
    record = _so_record(env, lines=[_so_line(env, qty_ordered="not-a-number")])

    res = env.post(INGEST_SO, [record])

    assert res.json()["records"][0]["outcome"] == "failed", res.text
    assert env.header("sales_orders", record["source_ref"]) is None


@pytest.mark.parametrize(
    "model,payload",
    [
        (CanonicalProductCategory, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalBrand, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalUnitOfMeasure, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalWarehouse, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalSupplier, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalCustomer, {"source_ref": "r", "code": "C", "name": "N"}),
        (CanonicalSalesAgent, {"source_ref": "r", "code": "C"}),
        (CanonicalProduct, {"source_ref": "r", "code": "C", "name": "N"}),
        (
            CanonicalSalesOrder,
            {"source_ref": "r", "so_number": "SO1", "status": "open",
             "lines": [{"source_ref": "l", "product_code": "P", "qty_ordered": 1,
                        "zz_line": 1}]},
        ),
        (
            CanonicalPurchaseOrder,
            {"source_ref": "r", "po_number": "PO1", "status": "open",
             "lines": [{"source_ref": "l", "product_code": "P", "qty_ordered": 1,
                        "from_so_external": {"db": "X", "zz_nested": 1}}]},
        ),
    ],
)
def test_every_canonical_ingest_schema_drops_unknown_keys(model, payload):
    """All /external/ingest/* schemas, not just sales orders (owner decision)."""
    parsed = model.model_validate({**payload, "zz_unknown": "x"})
    assert not hasattr(parsed, "zz_unknown")
    assert "zz_unknown" not in parsed.model_dump()


def test_the_shipping_billing_and_stock_balance_schemas_drop_unknown_keys():
    from app.services.stock_balance_ingest_service import _StockBalanceRecord

    for model in (CanonicalShippingOrder, CanonicalBillingDocument, _StockBalanceRecord):
        config = model.model_config
        assert config.get("extra") == "ignore", model.__name__
    from app.schemas.canonical_documents import CanonicalBillingDocumentLine

    assert CanonicalBillingDocumentLine.model_config.get("extra") == "ignore"


@pytest.mark.parametrize(
    "model_path",
    [
        "app.schemas.canonical_documents.CanonicalSalesOrder",
        "app.schemas.canonical_documents.CanonicalPurchaseOrder",
        "app.schemas.canonical_documents.CanonicalShippingOrder",
        "app.schemas.canonical_documents.CanonicalBillingDocument",
        "app.services.stock_balance_ingest_service._StockBalanceRecord",
    ],
)
def test_every_document_schema_reports_unknown_names_to_the_request_log(model_path):
    """The document entities share `POST /external/ingest/{entity}` with the masters
    (`ingest.py` `ingest_masters`), which logs whatever `collect_unknown_fields` gathered.
    So each document schema must NOTE its unknown names, or that one log line stays blind
    to them. Names only, qualified by model; the value never enters the set."""
    import importlib

    from pydantic import ValidationError

    from app.schemas.ingest_extras import collect_unknown_fields

    module_name, _, class_name = model_path.rpartition(".")
    model = getattr(importlib.import_module(module_name), class_name)
    with collect_unknown_fields() as seen:
        try:
            model.model_validate({"source_ref": "r", "zz_doc_unknown": "secret-value"})
        except ValidationError:
            pass  # the name is noted in the `before` validator, before field checks
    assert f"{class_name}.zz_doc_unknown" in seen
    assert not any("secret-value" in name for name in seen)


def test_validating_outside_an_ingest_request_does_not_raise():
    # No collector is set outside a request: noting must be a no-op, not `None.add`.
    CanonicalSalesOrder.model_validate(
        {"source_ref": "r", "so_number": "SO1", "status": "open", "lines": [], "zz": 1}
    )
