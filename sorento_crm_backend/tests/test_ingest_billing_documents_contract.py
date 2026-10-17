"""Finance S0-18 and S0-21: contract 2.6 names billing_documents (#1309).

S0-18 reads `GET /external/contract` through the route function (the guard on it is
`integration.contract.read`, pinned by `test_ingest_contract_v2.py`). S0-21 is a text check
that the cross-repo contract of record carries section 12.
"""
from __future__ import annotations

from pathlib import Path

# MUST be the first app import - resolves the circular import in
# app.modules.runtime.guards.
from app.main import app  # noqa: E402,F401

from app.api.v1.external.contract import get_contract
from app.schemas.canonical_documents import (
    CanonicalBillingDocument,
    CanonicalBillingDocumentLine,
)

REPO = Path(__file__).resolve().parents[2]
CROSS_REPO_CONTRACT = (
    REPO / "documentation" / "plans" / "autocount" / "PLAN-autocount-cross-repo-contract.md"
)

RECORD_KEYS = {
    "source_ref",
    "document_type",
    "doc_no",
    "doc_date",
    "status",
    "source_modified_at",
    "customer_ref",
    "customer_code",
    "customer_name",
    "agent_code",
    "currency_code",
    "currency_rate",
    "net_total",
    "tax_total",
    "total",
    "local_net_total",
    "against_doc_no",
    "against_source_ref",
    "ref",
    "description",
    "lines",
}
LINE_KEYS = {
    "source_ref",
    "line_number",
    "product_ref",
    "product_code",
    "description",
    "uom",
    "quantity",
    "unit_price",
    "discount_amount",
    "net_amount",
    "tax_code",
    "tax_rate",
    "tax_amount",
    "line_total",
    "from_doc_type",
    "from_doc_no",
    "from_line_ref",
}


def test_version_is_2_6_and_lists_the_entity():
    body = get_contract()
    # Bumped again (#1354 S2): "2.7" adds `delivery_orders`, `goods_receive_notes`, `branches`.
    # Bumped again (AC-CONSIGN-CRM): "2.9" adds consignment fields; "2.8" added `cancel_sales_orders`, `cancel_purchase_orders`.
    assert body["version"] == "2.10"
    assert "billing_documents" in body["entities"]


def test_fields_added_names_every_record_and_line_key():
    added = set(get_contract()["fields_added"]["billing_documents"])
    assert RECORD_KEYS <= added
    assert {f"lines.{key}" for key in LINE_KEYS} <= added


def test_the_canonical_schema_accepts_exactly_the_plan_keys():
    # `source_doc_no` is inherited from `_Canonical` and accepted on every entity.
    assert set(CanonicalBillingDocument.model_fields) - {"source_doc_no"} == RECORD_KEYS
    assert set(CanonicalBillingDocumentLine.model_fields) == LINE_KEYS


def test_the_new_warnings_are_in_the_vocabulary():
    warnings = set(get_contract()["warnings"])
    assert {
        "customer_unresolved",
        "agent_unresolved",
        "product_unresolved",
        "stale_ignored",
    } <= warnings


def test_the_notes_state_whole_document_replace():
    note = get_contract()["field_notes"]["billing_documents"]
    assert "whole document" in note


def test_cross_repo_contract_carries_section_12():
    text = CROSS_REPO_CONTRACT.read_text()
    assert "## 12. billing_documents (contract 2.6)" in text
    section = text.split("## 12. billing_documents (contract 2.6)", 1)[1]
    for needle in (
        "/api/v1/external/ingest/billing_documents",
        "/api/v1/external/ingest/billing_documents/deletions",
        "/api/v1/external/read/billing_documents",
        "stale_ignored",
        "no start-date floor",
        "A1",
        "A8",
        "A10",
    ):
        assert needle in section, needle
