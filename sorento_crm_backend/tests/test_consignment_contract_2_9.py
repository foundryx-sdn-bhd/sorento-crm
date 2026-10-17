"""AC-CONSIGN-CRM round 2, UAC-01: contract 2.9 (consignment fields stated, no longer stored only)."""
from __future__ import annotations

from tests.test_ingest_autocount_do_grn import env  # noqa: F401 - fixture


def _contract(env) -> dict:
    res = env.client.get("/api/v1/external/contract")
    assert res.status_code == 200, res.text
    return res.json()


def test_contract_version_is_2_10(env):
    assert _contract(env)["version"] == "2.10"


def test_consignments_fields_added_lists_the_2_9_fields(env):
    fields = set(_contract(env)["fields_added"]["consignments"])
    expected = {"Details.TransferedQty", "DebtorCode", "DebtorName", "SalesAgent",
                "Details.UOM", "Details.UnitPrice", "Details.SubTotal"}
    assert expected <= fields, sorted(expected - fields)


def test_consignments_note_is_no_longer_stored_only_and_names_transfered_qty(env):
    note = _contract(env)["field_notes"]["consignments"]
    assert "stored only" not in note, note
    assert "TransferedQty" in note, note
