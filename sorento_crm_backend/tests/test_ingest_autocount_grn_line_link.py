"""RED tests for lane GRN-PULL-CRM: a GRN line with no usable `FromDocDtlKey` links to ONE PO
line or ONE SPO line by `FromDocNo` + product + position (plan section 1.3).

Plan: documentation/plans/autocount/PLAN-autocount-grn-pull-crm-02oct.md
UAC:  documentation/plans/autocount/autocount-grn-pull-crm-02oct-acceptance-criteria.md

Owner rulings 2 Oct: `FromDocNo` wins over the hand-typed `OurPONo` (Q3 a); a cancelled GRN
no longer consumes SPO capacity (Q4 a); PO lines link too. Live AutoCount evidence (crew, 2 Oct):
`FromDocDtlKey` is 0 on every GRN line, AutoCount already writes one GRN line per SPO line in
the SPO's Seq order, `FromDocType` reads 'PO' even for SPOs, and the GRN's Location can differ
from the SPO's. So: the number's table decides PO vs SPO, the k-th GRN line of an item takes
the k-th unused line of that item (quantity confirms, D1 a), Location is never a key, and
nothing is FIFO-split. D1-D4 are on the second crew-ask; these tests follow its
recommendations.

The push route is the substrate: the pull's apply runs the same `AutocountDocIngestService`
(one writer), and the route runs the GRN receipt hook after its commit. Everything is reused
by import from `tests/test_ingest_autocount_do_grn.py` (`env`, the GRN sample, `_with_from`).

The sample GRN `ZZGRN-0001` (DocKey 800001) has two lines: 810001 = ZZAC-P1 x 100 at
ZZAC-WH1, 810002 = ZZAC-P2 x 20 at ZZAC-WH1.
"""
from __future__ import annotations

import copy
import zlib
from datetime import datetime, timedelta

import pytest

from app.models.procurement import (
    PickingHeader,
    PickingLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from app.models.inventory import Warehouse

from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    GRN_DELETE,
    _another,
    _records,
    _with_from,
    env,
    grn_records,
)

REF = "db1:GRN:800001"
P1_LINE, P2_LINE = 810001, 810002
LINK_WARNINGS = {
    "po_line_unresolved", "purchase_order_unresolved", "over_receipt",
    "item_not_on_order", "from_doc_type_unsupported",
}


# ===================================================================== seeding
def _po(env, number: str, *lines: tuple[str, int, int | None], company=None) -> tuple[str, list[str]]:
    """A purchase order with lines `(product_id, qty, dtl_key)`; returns (po id, line ids).
    PO lines carry no line number: AutoCount's DtlKey (in `source_ref`) is their order (D4)."""
    company = company or env.company
    po = PurchaseOrder(po_number=number, company_id=company)
    env.db.add(po)
    env.db.flush()
    ids = []
    for product_id, qty, dtl in lines:
        line = PurchaseOrderLine(
            purchase_order_id=po.id, product_id=product_id, qty_ordered=qty, company_id=company,
            source_ref=f"db1:{zlib.crc32(number.encode()) % 10**6}:{dtl}" if dtl is not None else None,
        )
        env.db.add(line)
        env.db.flush()
        ids.append(str(line.id))
    env.db.commit()
    return str(po.id), ids


def _spo(env, number: str, *lines: tuple[str, int], warehouse_id=None, company=None,
         first_line=1) -> list[str]:
    """SPO lines `(product_id, qty)` numbered `first_line`, `first_line + 1`, ... - the order
    the SPO ingest writes AutoCount's Seq in. Same `created_at` on purpose: line order, not
    age, must decide."""
    ids = []
    for offset, (product_id, qty) in enumerate(lines):
        row = SPOAllocation(spo_number=number, spo_line_number=first_line + offset,
                            product_id=product_id, allocated_quantity=qty,
                            warehouse_id=warehouse_id, company_id=company or env.company,
                            created_at=datetime(2026, 9, 1))
        env.db.add(row)
        env.db.flush()
        ids.append(str(row.id))
    env.db.commit()
    return ids


def _wh(env, code: str) -> str:
    row = Warehouse(warehouse_code=code, warehouse_name=code, company_id=env.company)
    env.db.add(row)
    env.db.commit()
    return str(row.id)


def _grn(*, line=0, doc_type="PO", doc_no=None, dtl=0, our_po=None, qty=None, rec=None) -> dict:
    """The sample GRN with line `line` naming `doc_no` (key `dtl`, 0 = AutoCount's usual)."""
    rec = copy.deepcopy(rec or grn_records()[0])
    if doc_no is not None:
        _with_from(rec, line, doc_type, doc_no, dtl)
    if our_po is not None:
        rec["Details"][line]["OurPONo"] = our_po
    if qty is not None:
        rec["Details"][line]["Qty"] = qty
    return rec


def _doc(doc_key: int, doc_no: str, lines: list[tuple[str, float, str, str | None]],
         doc_date="2026-09-15T00:00:00") -> dict:
    """A GRN of `lines` `(ItemCode, Qty, Location, FromDocNo)` in Seq order, every line with
    FromDocType 'PO' and FromDocDtlKey 0 - the live shape (crew evidence, 2 Oct)."""
    rec = copy.deepcopy(grn_records()[0])
    template = rec["Details"][0]
    rec.update({"DocKey": doc_key, "DocNo": doc_no, "DocDate": doc_date})
    details = []
    for index, (item, qty, location, from_doc_no) in enumerate(lines, start=1):
        detail = copy.deepcopy(template)
        detail.update({
            "DocKey": doc_key, "DtlKey": doc_key * 1000 + index, "Seq": index * 16,
            "ItemCode": item, "Qty": qty, "SmallestQty": qty, "Location": location,
            "SubTotal": qty, "SubTotalExTax": qty, "FromDocType": "PO" if from_doc_no else None,
            "FromDocNo": from_doc_no, "FromDocDtlKey": 0, "OurPONo": None,
        })
        details.append(detail)
    rec["Details"] = details
    return rec


def _push(env, *recs) -> dict:
    return _records(env.push_grn(list(recs)))


def _lines(env, doc_key=800001) -> dict[int, PickingLine]:
    return {l.dtl_key: l for l in env.grn_lines(env.grn(doc_key).id)}


def _links(env, doc_key: int) -> list[str | None]:
    """Each line's SPO-or-PO link, in Seq order."""
    rows = sorted(env.grn_lines(env.grn(doc_key).id), key=lambda l: l.seq)
    return [l.spo_allocation_id or l.po_line_id for l in rows]


def _link_warnings(record: dict) -> set[str]:
    return set(record.get("warnings") or []) & LINK_WARNINGS


# ===================================================================== AC-GP-11
@pytest.mark.parametrize("shape", ["absent", "null"])
def test_gp11_absent_from_keys_behave_as_null(env, shape):
    """Live presence of the From* keys is unverified (crew, 2 Oct): absent = null = blank."""
    rec = copy.deepcopy(grn_records()[0])
    for detail in rec["Details"]:
        for key in ("FromDocType", "FromDocNo", "FromDocDtlKey", "OurPONo"):
            if shape == "absent":
                detail.pop(key, None)
            else:
                detail[key] = None
    r = _push(env, rec)[REF]
    assert r["outcome"] == "created"
    assert _link_warnings(r) == set()
    for line in _lines(env).values():
        assert (line.from_doc_type, line.from_doc_no, line.from_dtl_key) == (None, None, None)
        assert line.po_line_id is None and line.spo_allocation_id is None




# ===================================================================== PO lines (Q2: link PO lines)
def test_gp21_key_zero_links_the_one_po_line_of_the_product(env):
    """Owner sample shape: FromDocType PO, FromDocNo PO-..., FromDocDtlKey 0 (dev
    GR-2026/09-0070 against PO-2026/09-0018)."""
    po_id, (po_line, _) = _po(env, "PO-2026/07-0013", (env.p1, 100, 7001), (env.p2, 50, 7002))
    r = _push(env, _grn(doc_no="PO-2026/07-0013"))[REF]
    assert _link_warnings(r) == set()
    line = _lines(env)[P1_LINE]
    assert line.po_line_id == po_line
    assert line.purchase_order_id == po_id
    assert line.spo_allocation_id is None
    assert (line.from_doc_type, line.from_doc_no, line.from_dtl_key) == ("PO", "PO-2026/07-0013", None)
    assert r["lines"]["linked"] == 1


def test_gp22_po_lines_of_one_product_are_taken_in_dtl_key_order(env):
    """D4: PO lines carry no line number; AutoCount's DtlKey orders them. The first-written
    row has the HIGHER DtlKey, so insertion order would give the wrong answer."""
    # Equal quantities everywhere (review B2), so only the DtlKey order can decide.
    _, (second, first) = _po(env, "PO-ZZ-0022", (env.p1, 50, 7202), (env.p1, 50, 7201))
    rec = _doc(800022, "ZZGRN-0022", [("ZZAC-P1", 50, "ZZAC-WH1", "PO-ZZ-0022"),
                                      ("ZZAC-P1", 50, "ZZAC-WH1", "PO-ZZ-0022")])
    r = _push(env, rec)["db1:GRN:800022"]
    assert _link_warnings(r) == set()
    assert _links(env, 800022) == [first, second]


def test_gp_po_line_link_never_writes_po_qty_received(env):
    """The PO feed owns `purchase_order_lines.qty_received` (plan 1.3)."""
    _, (po_line,) = _po(env, "PO-ZZ-0023", (env.p1, 100, 7301))
    _push(env, _grn(doc_no="PO-ZZ-0023"))
    env.db.expire_all()
    assert env.db.get(PurchaseOrderLine, po_line).qty_received == 0


def test_gp_d3_product_not_on_the_po_links_the_document_and_says_so(env):
    """D3 (a): no line of the product on the named PO - the only 'names a source, not
    linked' case, and it carries `item_not_on_order`."""
    po_id, _ = _po(env, "PO-ZZ-0024", (env.p2, 10, 7401))  # P2 only; line 0 is P1
    r = _push(env, _grn(doc_no="PO-ZZ-0024"))[REF]
    assert "item_not_on_order" in r["warnings"]
    line = _lines(env)[P1_LINE]
    assert line.po_line_id is None
    assert line.purchase_order_id == po_id


# ===================================================================== SPO lines
def test_gp23_key_zero_links_the_spo_line_and_counts_the_receipt(env):
    """Dev GR-2026/09-0079 shape: one SPO line per product. The receipt hook (route, and the
    pull's apply) recomputes the SPO line's received quantity."""
    (alloc,) = _spo(env, "SPO-2026/09-0005", (env.p2, 50), warehouse_id=env.wh1)
    r = _push(env, _grn(line=1, doc_no="SPO-2026/09-0005"))[REF]
    assert _link_warnings(r) == set()
    line = _lines(env)[P2_LINE]
    assert line.spo_allocation_id == alloc
    assert line.po_line_id is None
    assert line.spo_number_raw == "SPO-2026/09-0005"
    env.db.expire_all()
    assert env.db.get(SPOAllocation, alloc).quantity_received == 20


def test_gp23b_spo_number_is_matched_like_the_excel_upload(env):
    """`_spo_match_key` (alphanumeric, upper case): spo-2026.09-0005 names SPO-2026/09-0005."""
    (alloc,) = _spo(env, "SPO-2026/09-0005", (env.p2, 50), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_no="spo-2026.09-0005"))
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp24_kth_grn_line_of_an_item_takes_the_kth_spo_line(env):
    """Live GR-2026/09-0090 / SPO-2026/09-0010 shape: one GRN line per SPO line, the same
    item repeated (SRTWT167 BRW-BB x4: 106/24/195/675), and the GRN's Location CHANGED
    against the SPO's (BRW -> MWH): Location is never a key, and nothing is FIFO-split."""
    _wh(env, "ZZAC-MWH")
    spo = _spo(env, "SPO-ZZ-0090", (env.p1, 106), (env.p1, 24), (env.p2, 5), (env.p1, 195),
               (env.p1, 675), warehouse_id=env.wh1)
    rec = _doc(800090, "ZZGRN-0090", [
        ("ZZAC-P1", 106, "ZZAC-MWH", "SPO-ZZ-0090"),
        ("ZZAC-P1", 24, "ZZAC-MWH", "SPO-ZZ-0090"),
        ("ZZAC-P2", 5, "ZZAC-MWH", "SPO-ZZ-0090"),
        ("ZZAC-P1", 195, "ZZAC-MWH", "SPO-ZZ-0090"),
        ("ZZAC-P1", 675, "ZZAC-WH1", "SPO-ZZ-0090"),
    ])
    r = _push(env, rec)["db1:GRN:800090"]
    assert _link_warnings(r) == set()
    assert _links(env, 800090) == spo
    assert len(env.grn_lines(env.grn(800090).id)) == 5  # one picking line per AutoCount line
    assert r["lines"]["linked"] == 5 and r["lines"]["unlinked"] == 0


def test_gp24b_equal_quantities_still_take_distinct_lines(env):
    """Two GRN lines of one item with the same qty take two different SPO lines, in order."""
    spo = _spo(env, "SPO-ZZ-0091", (env.p1, 10), (env.p1, 10), warehouse_id=env.wh1)
    rec = _doc(800091, "ZZGRN-0091", [("ZZAC-P1", 10, "ZZAC-WH1", "SPO-ZZ-0091"),
                                      ("ZZAC-P1", 10, "ZZAC-WH1", "SPO-ZZ-0091")])
    _push(env, rec)
    assert _links(env, 800091) == spo


def test_gp25_over_receipt_links_with_a_warning(env):
    (alloc,) = _spo(env, "SPO-ZZ-0025", (env.p2, 5), warehouse_id=env.wh1)  # line receives 20
    r = _push(env, _grn(line=1, doc_no="SPO-ZZ-0025"))[REF]
    assert "over_receipt" in r["warnings"]
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp26_second_grn_takes_the_lines_the_first_did_not(env):
    """Partial receipts across GRNs: GRN 1 receives SPO line 1, GRN 2 lands on line 2."""
    first, second = _spo(env, "SPO-ZZ-0026", (env.p2, 20), (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _doc(800261, "ZZGRN-0261", [("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0026")]))
    _push(env, _doc(800262, "ZZGRN-0262", [("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0026")]))
    assert _links(env, 800261) == [first]
    assert _links(env, 800262) == [second]


def test_gp26a_d1_quantity_confirms_the_line(env):
    """D1 (a): exact remaining first, then enough remaining, in line order. SPO lines 100 and
    50. GRN A receives 40 of line 1 (partial). GRN B receives 50: line 1 has 60 left, line 2
    has exactly 50, so line 2. GRN C receives 60: exactly what line 1 has left."""
    line1, line2 = _spo(env, "SPO-ZZ-0075", (env.p2, 100), (env.p2, 50), warehouse_id=env.wh1)
    _push(env, _doc(800751, "ZZGRN-0751", [("ZZAC-P2", 40, "ZZAC-WH1", "SPO-ZZ-0075")]))
    _push(env, _doc(800752, "ZZGRN-0752", [("ZZAC-P2", 50, "ZZAC-WH1", "SPO-ZZ-0075")]))
    _push(env, _doc(800753, "ZZGRN-0753", [("ZZAC-P2", 60, "ZZAC-WH1", "SPO-ZZ-0075")]))
    assert _links(env, 800751) == [line1]
    assert _links(env, 800752) == [line2]
    assert _links(env, 800753) == [line1]


def test_gp26b_repush_of_the_same_grn_keeps_its_own_line(env):
    """A GRN never competes with itself: a later push of the same document keeps the SPO
    line it already took even though its own receipt now uses that line up."""
    (alloc,) = _spo(env, "SPO-ZZ-0027", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_no="SPO-ZZ-0027"))
    again = _grn(line=1, doc_no="SPO-ZZ-0027")
    again["Details"][0]["Qty"] = 99
    again["LastModified"] = "2026-07-28T10:00:00.000"
    r = _push(env, again)[REF]
    assert "over_receipt" not in r.get("warnings", [])
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp_d2_adopting_a_split_excel_grn_merges_to_one_line(env):
    """D2 (a), live GR-2026/09-0075 shape (CWB242 2 + 98 in the CRM vs 100 in AutoCount): the
    AutoCount line claims the Excel lines of its product that add up to its qty, keeps the
    first one's id, deletes the rest, and takes ITS OWN link (the Excel FIFO link is
    replaced)."""
    line1, line2 = _spo(env, "SPO-ZZ-0750", (env.p1, 100), (env.p1, 100), warehouse_id=env.wh1)
    header = PickingHeader(picking_number="ZZGRN-0750", picking_type="goods_received",
                           picking_status="approved", company_id=env.company)
    env.db.add(header)
    env.db.flush()
    keep = PickingLine(picking_header_id=header.id, product_id=env.p1, quantity_expected=2,
                       quantity_picked=2, spo_allocation_id=line2, company_id=env.company)
    gone = PickingLine(picking_header_id=header.id, product_id=env.p1, quantity_expected=98,
                       quantity_picked=98, spo_allocation_id=line2, company_id=env.company)
    env.db.add_all([keep, gone])
    env.db.commit()
    keep_id, gone_id = str(keep.id), str(gone.id)

    rec = _doc(800750, "ZZGRN-0750", [("ZZAC-P1", 100, "ZZAC-WH1", "SPO-ZZ-0750")])
    r = _push(env, rec)["db1:GRN:800750"]
    assert r["outcome"] == "updated" and "adopted_by_doc_no" in r["warnings"]
    rows = env.grn_lines(header.id)
    # One row. Which of the two keeps its id is stored order, which the Excel rows (same
    # transaction, no Seq) do not fix; either is one of the CRM's own ids, never a new one.
    assert [(l.quantity_picked, l.spo_allocation_id) for l in rows] == [(100, line1)]
    assert str(rows[0].id) in {keep_id, gone_id}
    env.db.expire_all()
    assert env.db.get(SPOAllocation, line2).quantity_received == 0  # the moved link released


# ===================================================================== Q3 a, no source
def test_gp27_from_doc_no_wins_over_our_po_no(env):
    _, (po_line,) = _po(env, "PO-ZZ-0270", (env.p1, 100, 7701))
    _po(env, "PO-ZZ-0271", (env.p1, 100, 7702))
    _push(env, _grn(doc_no="PO-ZZ-0270", our_po="PO-ZZ-0271"))
    assert _lines(env)[P1_LINE].po_line_id == po_line


def test_gp27b_our_po_no_is_used_when_from_doc_no_is_blank(env):
    _, (po_line,) = _po(env, "PO-ZZ-0272", (env.p1, 100, 7703))
    _push(env, _grn(our_po="PO-ZZ-0272"))
    assert _lines(env)[P1_LINE].po_line_id == po_line


def test_gp27c_from_doc_type_never_decides_po_or_spo(env):
    """Live: FromDocType reads 'PO' on SPO lines too. The number's table decides."""
    (alloc,) = _spo(env, "SPO-ZZ-0273", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_type="PO", doc_no="SPO-ZZ-0273"))
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp28_no_source_document_is_unlinked_without_a_warning(env):
    """Dev FGR2026/09-0022 / GR-2026/07-0001 shape (11 of 257 live lines)."""
    r = _push(env, grn_records()[0])[REF]
    assert _link_warnings(r) == set()
    assert all(l.po_line_id is None and l.spo_allocation_id is None for l in _lines(env).values())


# ===================================================================== waiting fill, scope
def test_gp30_unresolved_line_links_when_the_po_arrives(env):
    r = _push(env, _grn(doc_no="PO-ZZ-0300"))[REF]
    assert "purchase_order_unresolved" in r["warnings"]
    assert _lines(env)[P1_LINE].po_line_id is None
    _, (po_line,) = _po(env, "PO-ZZ-0300", (env.p1, 100, 7801))
    # Any later non-dry GRN batch fills it, here one for a different document.
    _push(env, _another(grn_records()[0], 800009, "ZZGRN-0009", 810090))
    assert _lines(env)[P1_LINE].po_line_id == po_line


def test_gp30b_unresolved_line_links_when_the_spo_arrives(env):
    _push(env, _grn(line=1, doc_no="SPO-ZZ-0301"))
    (alloc,) = _spo(env, "SPO-ZZ-0301", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _another(grn_records()[0], 800009, "ZZGRN-0009", 810090))
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp31_company_b_po_and_spo_never_link(env):
    _po(env, "PO-ZZ-0310", (env.p1, 100, 7901), company=env.company_b)
    _spo(env, "SPO-ZZ-0311", (env.p2, 50), company=env.company_b)
    rec = _grn(doc_no="PO-ZZ-0310")
    _with_from(rec, 1, "PO", "SPO-ZZ-0311", 0)
    r = _push(env, rec)[REF]
    assert "purchase_order_unresolved" in r["warnings"]
    lines = _lines(env)
    assert lines[P1_LINE].po_line_id is None
    assert lines[P2_LINE].spo_allocation_id is None


# ===================================================================== Q4 a
def test_gp32_cancelled_grn_no_longer_consumes_spo_capacity(env):
    """The pool counts every non-rejected GRN; a cancelled one must not hold capacity either
    (owner Q4 a). Pinned on the shared matcher, so the Excel upload gets the same rule."""
    from app.services.grn_spo_matching import build_allocation_pool

    (alloc,) = _spo(env, "SPO-ZZ-0320", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_no="SPO-ZZ-0320"))
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc
    pool = build_allocation_pool(env.db, product_id=env.p2, spo_number="SPO-ZZ-0320",
                                 company_id=env.company)
    assert pool == []  # used up

    env.delete(GRN_DELETE, [800001], "2026-07-01", "2026-07-31")
    env.db.expire_all()
    assert env.grn(800001).picking_status == "cancelled"
    pool = build_allocation_pool(env.db, product_id=env.p2, spo_number="SPO-ZZ-0320",
                                 company_id=env.company)
    assert [(e.allocation_id, e.available) for e in pool] == [(alloc, 20)]

    second = _another(_grn(line=1, doc_no="SPO-ZZ-0320"), 800002, "ZZGRN-0002", 820001)
    r = _push(env, second)["db1:GRN:800002"]
    assert "over_receipt" not in r.get("warnings", [])
    assert {l.dtl_key: l for l in env.grn_lines(env.grn(800002).id)}[820002].spo_allocation_id == alloc


def test_gp_d3_product_not_on_the_spo_keeps_the_spo_number(env):
    """D3 for an SPO: no header table, so the document link is `spo_number_raw`."""
    _spo(env, "SPO-ZZ-0330", (env.p1, 10), warehouse_id=env.wh1)  # P1 only; line 1 is P2
    r = _push(env, _grn(line=1, doc_no="SPO-ZZ-0330"))[REF]
    assert "item_not_on_order" in r["warnings"]
    line = _lines(env)[P2_LINE]
    assert line.spo_allocation_id is None
    assert line.spo_number_raw == "SPO-ZZ-0330"


def test_gp_forward_matching_never_splits_an_autocount_grn_line(env):
    """The Excel-side forward matcher FIFO-splits waiting lines into new rows; an AutoCount
    GRN line is one row per DtlKey and waits for the ingest's own fill instead."""
    from app.services.grn_spo_matching import forward_match_grn_lines_for_spo

    _push(env, _grn(line=1, doc_no="SPO-ZZ-0340"))
    line = _lines(env)[P2_LINE]
    line.spo_number_raw = "SPO-ZZ-0340"  # as stated, still waiting for its SPO line
    env.db.commit()
    _spo(env, "SPO-ZZ-0340", (env.p2, 5), (env.p2, 15), warehouse_id=env.wh1)
    result = forward_match_grn_lines_for_spo(env.db, "SPO-ZZ-0340", company_id=env.company)
    assert result.candidate_lines == 0
    assert len(env.grn_lines(env.grn(800001).id)) == 2



# ===================================================================== fix round (review)
def test_gp_b1_adopted_excel_line_waiting_for_its_spo_links_when_it_arrives(env):
    """Review B1: an Excel GRN line states SPO-ZZ-0401 before the SPO exists; AutoCount
    adopts the GRN (its line naming the same SPO); when the SPO arrives the next batch's
    waiting fill links it (forward matching skips AutoCount GRNs)."""
    header = PickingHeader(picking_number="ZZGRN-0001", picking_type="goods_received",
                           picking_status="approved", company_id=env.company)
    env.db.add(header)
    env.db.flush()
    env.db.add(PickingLine(picking_header_id=header.id, product_id=env.p2, quantity_expected=20,
                           quantity_picked=20, spo_number_raw="SPO-ZZ-0401",
                           company_id=env.company))
    env.db.commit()
    r = _push(env, _grn(line=1, doc_no="SPO-ZZ-0401"))[REF]
    assert "purchase_order_unresolved" in r["warnings"]
    assert _lines(env)[P2_LINE].spo_allocation_id is None
    (alloc,) = _spo(env, "SPO-ZZ-0401", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _another(grn_records()[0], 800009, "ZZGRN-0009", 810090))
    assert _lines(env)[P2_LINE].spo_allocation_id == alloc


def test_gp_s4_a_line_moving_from_a_po_to_an_spo_keeps_one_link(env):
    _, (po_line,) = _po(env, "PO-ZZ-0402", (env.p2, 20, 8001))
    (alloc,) = _spo(env, "SPO-ZZ-0402", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_no="PO-ZZ-0402"))
    assert _lines(env)[P2_LINE].po_line_id == po_line
    moved = _grn(line=1, doc_no="SPO-ZZ-0402")
    moved["LastModified"] = "2026-07-28T10:00:00.000"
    _push(env, moved)
    line = _lines(env)[P2_LINE]
    assert (line.spo_allocation_id, line.po_line_id, line.purchase_order_id) == (alloc, None, None)
    back = _grn(line=1, doc_no="PO-ZZ-0402")
    back["LastModified"] = "2026-07-29T10:00:00.000"
    _push(env, back)
    line = _lines(env)[P2_LINE]
    assert (line.po_line_id, line.spo_allocation_id, line.spo_number_raw) == (po_line, None, None)
    env.db.expire_all()
    assert env.db.get(SPOAllocation, alloc).quantity_received == 0  # receipt given back


def test_gp_s5_an_spo_the_crm_raised_links_its_spo_line_not_its_po_line(env):
    """A CRM-raised SPO is a purchase order AND SPO lines under one number: the SPO wins."""
    _, (po_line,) = _po(env, "S-SPO-ZZ-0403", (env.p2, 20, None))
    (alloc,) = _spo(env, "S-SPO-ZZ-0403", (env.p2, 20), warehouse_id=env.wh1)
    _push(env, _grn(line=1, doc_no="S-SPO-ZZ-0403"))
    line = _lines(env)[P2_LINE]
    assert (line.spo_allocation_id, line.po_line_id) == (alloc, None)


def test_gp_s6_waiting_fill_never_takes_a_line_a_sibling_holds(env):
    """SPO lines L1, L2, L3 all used up (L1, L2 by other GRNs, L3 by line A of this GRN);
    line B waits. The fill must leave L3 to A: the last line no sibling holds is L2."""
    l1, l2, l3 = _spo(env, "SPO-ZZ-0404", (env.p2, 20), (env.p2, 20), (env.p2, 20),
                      warehouse_id=env.wh1)
    for key, number in ((800441, "ZZGRN-0441"), (800442, "ZZGRN-0442")):
        _push(env, _doc(key, number, [("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0404")]))
    _push(env, _doc(800443, "ZZGRN-0443", [("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0404"),
                                           ("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0404")]))
    assert _links(env, 800441) == [l1] and _links(env, 800442) == [l2]
    a_line, b_line = sorted(env.grn_lines(env.grn(800443).id), key=lambda l: l.seq)
    assert a_line.spo_allocation_id == l3
    b_line.spo_allocation_id = None  # as if B had landed before the SPO line was free
    env.db.commit()
    _push(env, _another(grn_records()[0], 800009, "ZZGRN-0009", 810090))
    assert _links(env, 800443) == [l3, l2]


def test_gp_s7_over_receipt_takes_the_first_line_with_anything_left(env):
    """D1 (a) rung 3: no line has 10 left (5 and 3), so the first with any remaining."""
    first, second = _spo(env, "SPO-ZZ-0405", (env.p2, 5), (env.p2, 3), warehouse_id=env.wh1)
    r = _push(env, _doc(800451, "ZZGRN-0451", [("ZZAC-P2", 10, "ZZAC-WH1", "SPO-ZZ-0405")]))
    assert "over_receipt" in r["db1:GRN:800451"]["warnings"]
    assert _links(env, 800451) == [first]


def test_gp_n2_waiting_fill_never_reaches_company_b(env):
    """Security review N2: a line waiting on PO-ZZ-0406 / SPO-ZZ-0407 stays unlinked when
    company B, not this one, receives documents with those numbers."""
    rec = _grn(doc_no="PO-ZZ-0406")
    _with_from(rec, 1, "PO", "SPO-ZZ-0407", 0)
    _push(env, rec)
    _po(env, "PO-ZZ-0406", (env.p1, 100, 8101), company=env.company_b)
    _spo(env, "SPO-ZZ-0407", (env.p2, 20), company=env.company_b)
    _push(env, _another(grn_records()[0], 800009, "ZZGRN-0009", 810090))
    lines = _lines(env)
    assert lines[P1_LINE].po_line_id is None and lines[P1_LINE].purchase_order_id is None
    assert lines[P2_LINE].spo_allocation_id is None


# ===================================================================== e2e gaps (crew, 2 Oct)
def _push_spo(env, number: str, lines: list[tuple[str, int]]) -> list[str]:
    """An SPO arriving through the existing AutoCount SPO path: the shipping-order ingest
    (`ShippingOrderIngestService`), the route's commit, then its post-commit hooks
    (`_run_document_hooks`), exactly as `POST /external/ingest/shipping_orders` runs them.
    Lines `(ItemCode, qty)` in AutoCount Seq order; returns the SPO lines' ids in
    `spo_line_number` order."""
    import zlib

    from app.api.v1.external.ingest import _run_document_hooks
    from app.services.shipping_order_ingest_service import ShippingOrderIngestService

    doc_key = zlib.crc32(number.encode()) % 10**6
    svc = ShippingOrderIngestService(env.db, integration_id=None, company_id=env.company)
    result = svc.ingest("shipping_orders", [{
        "source_ref": f"db1:{doc_key}", "spo_number": number, "status": "open",
        "lines": [
            {"source_ref": f"db1:{doc_key}:{doc_key * 100 + i}", "product_code": code,
             "qty_ordered": str(qty), "warehouse_code": "ZZAC-WH1"}
            for i, (code, qty) in enumerate(lines, start=1)
        ],
    }])
    assert result.records[0].outcome.value in ("created", "updated"), result.records[0].errors
    env.db.commit()
    _run_document_hooks(env.db, "shipping_orders", svc, actor=None)
    env.db.expire_all()
    rows = (env.db.query(SPOAllocation)
            .filter(SPOAllocation.company_id == env.company, SPOAllocation.spo_number == number)
            .order_by(SPOAllocation.spo_line_number).all())
    return [str(r.id) for r in rows]


def test_gp_e2e_waiting_lines_link_when_their_spo_arrives_through_the_spo_ingest(env):
    """Crew e2e gap 1 + 2 (GR-2026/10-0006 against SPO-2026/09-0115, not on dev): the GRN
    lands first, every line waits with `from_doc_no` kept (`our_po_no` and
    `spo_number_raw` are NULL, AutoCount sends no OurPONo); the SPO then arrives through
    the shipping-order ingest and its post-commit hook links the waiting lines at once,
    k-th line of an item to the k-th SPO line of that item, quantity confirming, and the
    SPO lines' received quantities follow. No later GRN batch is needed."""
    _wh(env, "ZZAC-MWH")
    rec = _doc(800115, "ZZGRN-0115", [
        ("ZZAC-P1", 106, "ZZAC-MWH", "SPO-ZZ-0115"),
        ("ZZAC-P1", 24, "ZZAC-MWH", "SPO-ZZ-0115"),
        ("ZZAC-P2", 5, "ZZAC-MWH", "SPO-ZZ-0115"),
        ("ZZAC-P1", 195, "ZZAC-MWH", "SPO-ZZ-0115"),
    ])
    r = _push(env, rec)["db1:GRN:800115"]
    assert "purchase_order_unresolved" in r["warnings"]
    for line in env.grn_lines(env.grn(800115).id):
        assert (line.from_doc_no, line.our_po_no, line.spo_number_raw) == ("SPO-ZZ-0115", None, None)
        assert line.spo_allocation_id is None and line.po_line_id is None

    spo = _push_spo(env, "SPO-ZZ-0115", [("ZZAC-P1", 106), ("ZZAC-P1", 24), ("ZZAC-P2", 5),
                                         ("ZZAC-P1", 195)])

    assert _links(env, 800115) == spo
    received = {str(a.id): a.quantity_received for a in env.db.query(SPOAllocation)
                .filter(SPOAllocation.id.in_(spo)).all()}
    assert [received[i] for i in spo] == [106, 24, 5, 195]


def test_gp_e2e_quantity_confirms_when_the_spo_lists_an_item_in_another_order(env):
    """The SPO lists P1 24 before P1 106; the GRN receives 106 then 24. Each GRN line takes
    the SPO line whose remaining equals its quantity (D1 a), not the bare position."""
    rec = _doc(800116, "ZZGRN-0116", [("ZZAC-P1", 106, "ZZAC-WH1", "SPO-ZZ-0116"),
                                      ("ZZAC-P1", 24, "ZZAC-WH1", "SPO-ZZ-0116")])
    _push(env, rec)
    first, second = _push_spo(env, "SPO-ZZ-0116", [("ZZAC-P1", 24), ("ZZAC-P1", 106)])
    assert _links(env, 800116) == [second, first]


def test_gp_e2e_spo_arrival_never_links_another_companys_waiting_line(env):
    rec = _doc(800117, "ZZGRN-0117", [("ZZAC-P2", 20, "ZZAC-WH1", "SPO-ZZ-0117")])
    _push(env, rec)
    line = env.grn_lines(env.grn(800117).id)[0]
    line.company_id = env.company_b  # the same waiting line, but company B's
    env.db.commit()
    _push_spo(env, "SPO-ZZ-0117", [("ZZAC-P2", 20)])
    env.db.expire_all()
    assert env.db.get(PickingLine, line.id).spo_allocation_id is None
