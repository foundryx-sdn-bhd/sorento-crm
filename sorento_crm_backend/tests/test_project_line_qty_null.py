"""Owner rule: a blank quantity is never saved as a silent default.

A project quotation line and a project PO line PUT with an explicit null quantity used to be
stored as 1. The route must refuse it (422) and leave the stored quantity alone.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from .test_project_po_document_save import (  # noqa: F401  (api is a fixture)
    BASE,
    _line,
    _po,
    _product,
    _quoted_version,
    api,
)


def _qline(client, db, project_id: str):
    version_id, product = _quoted_version(client, db, project_id)
    line = client.post(
        f"{BASE}/quotation-versions/{version_id}/lines",
        json={"product_id": product.id, "unit_price": "100.00", "quantity": "5"},
    )
    assert line.status_code == 201, line.text
    return version_id, product, line.json()["id"]


def _qty(client, version_id: str, line_id: str) -> Decimal:
    rows = client.get(f"{BASE}/quotation-versions/{version_id}/lines").json()["data"]
    return Decimal(str(next(r for r in rows if r["id"] == line_id)["quantity"]))


def _pline(client, db, project_id: str):
    version_id, product = _quoted_version(client, db, project_id)
    po = _po(client, project_id, version_id, "PO-QTY-1")
    line = _line(client, po["id"], product_id=product.id, unit_price="900.00", quantity="5")
    return po["id"], product, line["id"]


def _pqty(client, po_id: str, line_id: str) -> Decimal:
    rows = client.get(f"{BASE}/purchase-orders/{po_id}/lines").json()["data"]
    return Decimal(str(next(r for r in rows if r["id"] == line_id)["quantity"]))


def _qurl(version_id, line_id):
    return f"{BASE}/quotation-versions/{version_id}/lines/{line_id}"


def _purl(po_id, line_id):
    return f"{BASE}/purchase-orders/{po_id}/lines/{line_id}"


# ---------------------------------------------------------------- quotation line


def test_quotation_line_null_quantity_is_422_and_unchanged(api):
    client, db, *_rest, project = api
    version_id, _p, line_id = _qline(client, db, project.id)
    res = client.put(_qurl(version_id, line_id), json={"quantity": None})
    assert res.status_code == 422, res.text
    assert _qty(client, version_id, line_id) == Decimal("5")


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_quotation_line_nan_inf_quantity_is_422(api, bad):
    client, db, *_rest, project = api
    version_id, _p, line_id = _qline(client, db, project.id)
    res = client.put(_qurl(version_id, line_id), json={"quantity": bad})
    assert res.status_code == 422, res.text
    assert _qty(client, version_id, line_id) == Decimal("5")


def test_quotation_line_omitted_quantity_unchanged(api):
    client, db, *_rest, project = api
    version_id, _p, line_id = _qline(client, db, project.id)
    res = client.put(_qurl(version_id, line_id), json={"unit_price": "110.00"})
    assert res.status_code == 200, res.text
    assert _qty(client, version_id, line_id) == Decimal("5")


def test_quotation_line_valid_quantity_saves(api):
    client, db, *_rest, project = api
    version_id, _p, line_id = _qline(client, db, project.id)
    res = client.put(_qurl(version_id, line_id), json={"quantity": "7"})
    assert res.status_code == 200, res.text
    assert _qty(client, version_id, line_id) == Decimal("7")


def test_quotation_line_zero_quantity_records_current_behaviour(api):
    client, db, *_rest, project = api
    version_id, _p, line_id = _qline(client, db, project.id)
    res = client.put(_qurl(version_id, line_id), json={"quantity": "0"})
    # Pinned to today's behaviour: a quantity of 0 is accepted and stored (not refused).
    assert res.status_code == 200, res.text
    assert _qty(client, version_id, line_id) == Decimal("0")


# ---------------------------------------------------------------------- PO line


def test_po_line_null_quantity_is_422_and_unchanged(api):
    client, db, *_rest, project = api
    po_id, _p, line_id = _pline(client, db, project.id)
    res = client.put(_purl(po_id, line_id), json={"quantity": None})
    assert res.status_code == 422, res.text
    assert _pqty(client, po_id, line_id) == Decimal("5")


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_po_line_nan_inf_quantity_is_422(api, bad):
    client, db, *_rest, project = api
    po_id, _p, line_id = _pline(client, db, project.id)
    res = client.put(_purl(po_id, line_id), json={"quantity": bad})
    assert res.status_code == 422, res.text
    assert _pqty(client, po_id, line_id) == Decimal("5")


def test_po_line_omitted_quantity_unchanged(api):
    client, db, *_rest, project = api
    po_id, _p, line_id = _pline(client, db, project.id)
    res = client.put(_purl(po_id, line_id), json={"unit_price": "910.00"})
    assert res.status_code == 200, res.text
    assert _pqty(client, po_id, line_id) == Decimal("5")


def test_po_line_valid_quantity_saves(api):
    client, db, *_rest, project = api
    po_id, _p, line_id = _pline(client, db, project.id)
    res = client.put(_purl(po_id, line_id), json={"quantity": "7"})
    assert res.status_code == 200, res.text
    assert _pqty(client, po_id, line_id) == Decimal("7")


def test_po_line_zero_quantity_records_current_behaviour(api):
    client, db, *_rest, project = api
    po_id, _p, line_id = _pline(client, db, project.id)
    res = client.put(_purl(po_id, line_id), json={"quantity": "0"})
    # Pinned to today's behaviour: a quantity of 0 is accepted and stored (not refused).
    assert res.status_code == 200, res.text
    assert _pqty(client, po_id, line_id) == Decimal("0")
