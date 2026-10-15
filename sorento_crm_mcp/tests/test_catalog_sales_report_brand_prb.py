"""Phase 2 RED test - chat fix PR-B S5: the `crm_sales_report` catalog entry exposes `brand_ids`."""
from sorento_crm_mcp.catalog import CATALOG


def test_b7_crm_sales_report_exposes_brand_ids():
    spec = next(s for s in CATALOG if s.name == "crm_sales_report")
    assert "brand_ids" in set(spec.query_params), sorted(spec.query_params)
