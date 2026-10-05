"""Lane GRN-HEADER-SPO: an AutoCount GRN's header "SPO Number" (`picking_headers.spo_number`)
comes from its lines (owner, 4 Oct: the AutoCount GRN API has no header SPO field; `Ref` holds a
container number, `Description` free text). One distinct line SPO gives that SPO, several are
joined with ", " like the Excel import's `_header_spo_display`, none leaves it alone.

Plan: documentation/plans/autocount/PLAN-grn-header-spo-04oct.md
UAC:  documentation/plans/autocount/grn-header-spo-04oct-acceptance-criteria.md

Reuses the GRN line-link suite's seeding and the shared `env` fixture.
"""
from __future__ import annotations

import copy

from app.models.procurement import PickingHeader, SPOAllocation

from tests.test_ingest_autocount_do_grn import env, grn_records  # noqa: F401 - env is a fixture
from tests.test_ingest_autocount_grn_line_link import REF, _doc, _grn, _po, _push, _spo


def _header_spo(env, doc_key: int = 800001):
    return env.grn(doc_key).spo_number


# ===================================================================== AC-GHS02
def test_ghs02_one_line_spo_fills_the_header(env):
    """Live GR-2026/10-0008 shape: lines allocated to one SPO, header SPO was '-'."""
    _spo(env, "SPO-2026/09-0118", (env.p2, 50), warehouse_id=env.wh1)
    r = _push(env, _grn(line=1, doc_no="SPO-2026/09-0118"))[REF]
    assert r["outcome"] == "created"
    assert _header_spo(env) == "SPO-2026/09-0118"


def test_ghs02b_several_lines_of_one_spo_give_it_once(env):
    _spo(env, "SPO-ZZ-0201", (env.p1, 100), (env.p2, 50), warehouse_id=env.wh1)
    _push(env, _doc(802010, "ZZGRN-2010", [("ZZAC-P1", 100, "ZZAC-WH1", "SPO-ZZ-0201"),
                                           ("ZZAC-P2", 50, "ZZAC-WH1", "spo-zz.0201")]))
    assert _header_spo(env, 802010) == "SPO-ZZ-0201"


def test_ghs02c_line_linked_by_dtl_key_names_its_spo(env):
    """A line linked through `FromDocDtlKey` carries no `spo_number_raw`; its SPO line's
    number still reaches the header."""
    (alloc,) = _spo(env, "SPO-ZZ-0202", (env.p2, 50), warehouse_id=env.wh1)
    row = env.db.get(SPOAllocation, alloc)
    row.source_ref = "db1:SPO:777:930202"
    env.db.commit()
    _push(env, _grn(line=1, doc_no="SPO-ZZ-0202", dtl=930202))
    assert _header_spo(env) == "SPO-ZZ-0202"


# ===================================================================== AC-GHS03
def test_ghs03_several_spos_are_joined_in_line_order(env):
    _spo(env, "SPO-ZZ-0302", (env.p2, 50), warehouse_id=env.wh1)
    _spo(env, "SPO-ZZ-0301", (env.p1, 100), warehouse_id=env.wh1)
    _push(env, _doc(803010, "ZZGRN-3010", [("ZZAC-P2", 50, "ZZAC-WH1", "SPO-ZZ-0302"),
                                           ("ZZAC-P1", 100, "ZZAC-WH1", "SPO-ZZ-0301")]))
    assert _header_spo(env, 803010) == "SPO-ZZ-0302, SPO-ZZ-0301"


def test_ghs03b_seq_decides_the_order_not_the_array(env):
    """AutoCount's Seq is the line order; the Details array may list lines in another order."""
    _spo(env, "SPO-ZZ-0304", (env.p2, 50), warehouse_id=env.wh1)
    _spo(env, "SPO-ZZ-0303", (env.p1, 100), warehouse_id=env.wh1)
    rec = _doc(803030, "ZZGRN-3030", [("ZZAC-P1", 100, "ZZAC-WH1", "SPO-ZZ-0303"),
                                      ("ZZAC-P2", 50, "ZZAC-WH1", "SPO-ZZ-0304")])
    rec["Details"][0]["Seq"], rec["Details"][1]["Seq"] = 64, 16
    _push(env, rec)
    assert _header_spo(env, 803030) == "SPO-ZZ-0304, SPO-ZZ-0303"


def test_ghs03c_a_linked_line_shows_the_spos_own_spelling(env):
    """A hand-typed variant spelling never reaches the header when the line linked."""
    _spo(env, "SPO-ZZ-0305", (env.p1, 100), (env.p2, 50), warehouse_id=env.wh1)
    _push(env, _doc(803050, "ZZGRN-3050", [("ZZAC-P1", 100, "ZZAC-WH1", "spo-zz.0305"),
                                           ("ZZAC-P2", 50, "ZZAC-WH1", "SPO-ZZ-0305")]))
    assert _header_spo(env, 803050) == "SPO-ZZ-0305"


# ===================================================================== AC-GHS01 / AC-GHS04
def test_ghs01_header_fields_are_never_read_as_the_spo(env):
    """An SPO number typed in `Ref` / `RefDocNo` / `Description` is ignored: lines on a PO
    leave the header SPO NULL."""
    _spo(env, "SPO-ZZ-0401", (env.p1, 100), warehouse_id=env.wh1)
    _po(env, "PO-ZZ-0401", (env.p1, 100, 4401))
    rec = _grn(doc_no="PO-ZZ-0401")
    rec.update({"Ref": "SPO-ZZ-0401", "RefDocNo": "SPO-ZZ-0401", "Description": "SPO-ZZ-0401"})
    r = _push(env, rec)[REF]
    assert r["outcome"] == "created"
    assert _header_spo(env) is None


def test_ghs04_no_line_on_an_spo_keeps_the_header_null(env):
    _push(env, copy.deepcopy(grn_records()[0]))
    assert _header_spo(env) is None


# ===================================================================== AC-GHS05
def test_ghs05_repush_fills_an_existing_grn_and_counts_as_updated(env):
    """A GRN stored before this fix (lines linked, header SPO NULL) gets it on its next push,
    even an unchanged one."""
    _spo(env, "SPO-ZZ-0501", (env.p2, 50), warehouse_id=env.wh1)
    rec = _grn(line=1, doc_no="SPO-ZZ-0501")
    _push(env, rec)
    header = env.grn(800001)
    header.spo_number = None
    env.db.commit()
    r = _push(env, copy.deepcopy(rec))[REF]
    assert r["outcome"] == "updated"
    assert _header_spo(env) == "SPO-ZZ-0501"
    assert _push(env, copy.deepcopy(rec))[REF]["outcome"] == "unchanged"


def test_ghs05b_spo_arriving_later_fills_the_header_on_the_next_push(env):
    rec = _grn(line=1, doc_no="SPO-ZZ-0502")
    _push(env, rec)
    assert _header_spo(env) is None
    _spo(env, "SPO-ZZ-0502", (env.p2, 50), warehouse_id=env.wh1)
    later = copy.deepcopy(rec)
    later["LastModified"] = "2026-07-28T10:00:00.000"
    assert _push(env, later)[REF]["outcome"] == "updated"
    assert _header_spo(env) == "SPO-ZZ-0502"


# ===================================================================== AC-GHS06
def test_ghs06_a_push_with_no_spo_never_clears_a_stored_header_spo(env):
    """An adopted Excel GRN keeps the SPO its import stored."""
    header = PickingHeader(picking_number="ZZGRN-0001", picking_type="goods_received",
                           picking_status="approved", spo_number="SPO-ZZ-EXCEL",
                           company_id=env.company)
    env.db.add(header)
    env.db.commit()
    r = _push(env, copy.deepcopy(grn_records()[0]))[REF]
    assert "adopted_by_doc_no" in r["warnings"]
    assert _header_spo(env) == "SPO-ZZ-EXCEL"
