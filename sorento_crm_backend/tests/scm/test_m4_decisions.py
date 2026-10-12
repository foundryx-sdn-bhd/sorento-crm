"""SCM M4 Slice B - decisions (Accept / Adjust / Reject) + PO draft→confirm→GR flow.

Drives the real ``decision_service`` / ``purchase_order_service`` against controlled
fixtures inside the ``scm_app`` savepoint (mirrors ``test_m4_cash`` / ``test_m3_run``).
Every expected number is hand-authored, derived INDEPENDENTLY of the service output.

Covered UAC: draft-PO consolidation (AC-M4.5), on_order excludes-draft/includes-active
BOTH directions (AC-M4.6 - the highest-risk test), confirm renumber, adjust
append-only + supplier switch/recompute (AC-M4.7), reject (AC-M4.8), bulk accept +
bulk confirm + create-GR (AC-M4.9), auth denial.
"""
from __future__ import annotations

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.scm import decision_service as dsvc
from app.services.scm import reorder_run_service as run_svc
from app.services.scm.purchase_order_service import PurchaseOrderService
from tests.scm.conftest import as_user, requires_pg, seed_user, set_plan_grain
from tests.scm.test_m4_cash import (
    _client,
    _link,
    _mk_demand,
    _mk_product,
    _mk_stock,
    _mk_supplier,
    _mk_warehouse,
)

pytestmark = requires_pg

_PO_CANONICAL = re.compile(r"^PO-\d{4}/\d{2}-\d{4}$")


def _ensure_po_numbering(db) -> None:
    """The canonical `purchase_order` rule confirm numbers from. Migration 274 seeds it, but
    a bootstrapped database (CI, `scripts/bootstrap_env.py`) has none, and confirm then keeps
    the draft number. Seeded here, unscoped and inside the test's transaction, only when
    the database holds no enabled rule of its own."""
    exists = db.execute(text(
        "SELECT 1 FROM document_numbering_rules WHERE doc_type = 'purchase_order' AND enabled"
    )).first()
    if exists is None:
        db.execute(text(
            "INSERT INTO document_numbering_rules (id, company_id, doc_type, enabled, "
            "prefix_template, number_digits, next_value, start_value, reset_policy, "
            "created_at, updated_at) VALUES (:id, NULL, 'purchase_order', true, "
            "'PO-{year}/{month:02d}-', 4, 1, 1, 'monthly', now(), now())"
        ), {"id": str(uuid.uuid4())})


# ===========================================================================
# fixture builders (all inside the savepoint)
# ===========================================================================

#: The two seed shapes' product codes, so every `_run_buys` caller can name its own
#: SKUs (G10) - see the module docstring for why: G1 (`PLAN-scm-reorder-oi-feedback-
#: 1sep.md`) makes the unscoped daily run committed-demand only, and these fixtures
#: build a plain stock-shortage buy off `demand_stat`, not a committed order.
_SAME_SUPPLIER_CODES = ["M4P-SHARED-A", "M4P-SHARED-B", "M4P-OTHER-C"]
_TWO_SUPPLIER_CODES = ["M4P-ALT"]


def _run_buys(db, wid_code, product_codes=None) -> str:
    # These are LOCATION-grain decisions, so the run must be created under that policy.
    set_plan_grain(db, "location")
    created = run_svc.create_run(db, [wid_code], "warehouse",
                                 product_codes=product_codes, enqueue=False)
    assert run_svc.run_reorder(created["run_id"], db=db)["status"] == "completed"
    return created["run_id"]


def _buy_recs(db, run_id):
    return db.execute(text(
        "SELECT id::text AS id, product_id::text AS product_id, "
        "supplier_id::text AS supplier_id, rounded_qty FROM scm.reorder_recommendation "
        "WHERE run_id = :r AND rec_type = 'buy' ORDER BY rank"
    ), {"r": run_id}).mappings().all()


def _draft_po_ids_for_recs(db, rec_ids):
    """Open SCM draft POs holding THESE recs' lines. Scoped by source_ref because the
    local DB (a prod-data copy) can carry unrelated draft POs from other work."""
    return db.execute(text(
        "SELECT DISTINCT pol.purchase_order_id::text FROM purchase_order_lines pol "
        "JOIN purchase_orders po ON po.id = pol.purchase_order_id "
        "WHERE pol.source_ref = ANY(:r) AND po.status = 'draft_recommendation'"
    ), {"r": list(rec_ids)}).scalars().all()


def _po_id_for_rec(db, rec_id):
    """The PO a rec's line currently lives in (via source_ref), or None."""
    return db.execute(text(
        "SELECT purchase_order_id::text FROM purchase_order_lines WHERE source_ref = :r"
    ), {"r": rec_id}).scalar()


def _seed_same_supplier(db):
    """Two low-stock SKUs sharing ONE supplier + a third SKU on a DIFFERENT supplier."""
    wid = _mk_warehouse(db, "M4W-CONS")
    shared = _mk_supplier(db, "M4 Shared Supplier")
    other = _mk_supplier(db, "M4 Other Supplier")
    a = _mk_product(db, "M4P-SHARED-A")
    b = _mk_product(db, "M4P-SHARED-B")
    c = _mk_product(db, "M4P-OTHER-C")
    for p in (a, b, c):
        _mk_stock(db, p, wid, 5)
        _mk_demand(db, p, wid, 10.0)
    _link(db, a, shared, cost=60)
    _link(db, b, shared, cost=70)
    _link(db, c, other, cost=80)
    db.flush()
    return "M4W-CONS", a, b, c


def _seed_two_supplier_product(db):
    """One low-stock SKU with a PRIMARY + a cheaper NON-PRIMARY alternative supplier."""
    wid = _mk_warehouse(db, "M4W-ALT")
    p = _mk_product(db, "M4P-ALT")
    _mk_stock(db, p, wid, 5)
    _mk_demand(db, p, wid, 10.0)
    primary = _mk_supplier(db, "M4 Primary Supplier")
    alt = _mk_supplier(db, "M4 Alt Supplier")
    _link(db, p, primary, lead=30, cost=60, primary=True)
    _link(db, p, alt, lead=20, cost=45, primary=False)
    db.flush()
    return "M4W-ALT", p


# ===========================================================================
# Staged decisions - Accept/Adjust create NO PO until Confirm decisions (M4 slice-B)
# ===========================================================================

def test_accept_stages_no_po_until_confirm(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}

    res = dsvc.accept_recommendation(db, recs[a]["id"], actor="tester")
    db.flush()
    # STAGED - status flips, but no PO exists yet (the overview-before-order model)
    assert res["draft_po_id"] is None and res["draft_po_number"] is None
    assert db.execute(text("SELECT status FROM scm.reorder_recommendation WHERE id = :id"),
                      {"id": recs[a]["id"]}).scalar() == "accepted"
    # scoped to this rec - no line/PO materialised before Confirm decisions
    assert _po_id_for_rec(db, recs[a]["id"]) is None


# ===========================================================================
# B1 (review fix) - a MoQ override reaches the draft PO at Confirm, not just the grid
# ===========================================================================

def test_moq_override_reaches_the_draft_po_at_confirm(scm_app):
    """Before the fix, `_line_inputs` built the draft PO line off the STALE `rounded_qty`
    column: a buyer's MoQ override recalculated the grid but Accept + Confirm still raised
    the PO at the OLD master rounding. `set_moq_override` now persists the recalculated
    `rounded_qty` (and `cash_impact`), so Confirm reads the SAME figure the buyer saw and
    agreed to. The override is set well ABOVE the engine's own master rounding (never
    assumed - the engine's sizing is independent of this test) so the two can never
    coincide by chance and the pin is discriminating."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M4W-MOQOV")
    p = _mk_product(db, "M4P-MOQOV")
    _mk_stock(db, p, wid, 5)
    _mk_demand(db, p, wid, 10.0)
    _link(db, p, _mk_supplier(db, "M4 MoQ Override Supplier"), moq=500, mult=1, cost=60)
    db.flush()

    run_id = _run_buys(db, "M4W-MOQOV", product_codes=["M4P-MOQOV"])
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    assert p in recs
    rec = recs[p]
    master_rounded = float(rec["rounded_qty"])

    # A MoQ well above whatever the engine sized, so the override is unmistakably a
    # DIFFERENT figure from the master rounding, however the engine happened to size it.
    override_moq = master_rounded + 5000
    override = run_svc.set_moq_override(db, rec["id"], override_moq)
    overridden_qty = override["order_qty"]
    assert overridden_qty == override_moq, "need floors up to the (huge) override MoQ"
    assert overridden_qty != master_rounded, "the override must actually change the order"

    dsvc.accept_recommendation(db, rec["id"], actor="tester")
    dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")

    po_id = _po_id_for_rec(db, rec["id"])
    assert po_id is not None, "accept + confirm must still raise a draft PO"
    line_qty = db.execute(text(
        "SELECT qty_ordered FROM purchase_order_lines WHERE source_ref = :r"
    ), {"r": rec["id"]}).scalar()
    assert float(line_qty) == overridden_qty, (
        "the draft PO line must raise at the buyer's overridden quantity"
    )
    assert float(line_qty) != master_rounded, (
        "the draft PO line must NOT raise at the stale master rounding"
    )


# ===========================================================================
# AC-M4.5 - Confirm decisions consolidates a draft PO per supplier
# ===========================================================================

def test_confirm_consolidates_one_draft_po_per_supplier(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    assert set(recs) >= {a, b, c}, "all three SKUs should buy"

    for pid in (a, b, c):
        dsvc.accept_recommendation(db, recs[pid]["id"], actor="tester")
    out = dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")
    assert out["confirmed_count"] == 3
    assert out["po_count"] == 2  # two suppliers → two consolidated drafts

    po_a = _po_id_for_rec(db, recs[a]["id"])
    po_b = _po_id_for_rec(db, recs[b]["id"])
    po_c = _po_id_for_rec(db, recs[c]["id"])
    # same supplier → SAME draft PO (2 lines); different supplier → its own draft
    assert po_a == po_b
    assert po_c != po_a

    shared_po = db.execute(text(
        "SELECT status, source_system FROM purchase_orders WHERE id = :id"
    ), {"id": po_a}).mappings().first()
    assert shared_po["status"] == "draft_recommendation"
    assert shared_po["source_system"] == "scm_recommendation"
    n_lines = db.execute(text(
        "SELECT count(*) FROM purchase_order_lines WHERE purchase_order_id = :id"
    ), {"id": po_a}).scalar()
    assert n_lines == 2

    # confirm is idempotent - re-running doesn't duplicate lines
    dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")
    assert db.execute(text(
        "SELECT count(*) FROM purchase_order_lines WHERE purchase_order_id = :id"
    ), {"id": po_a}).scalar() == 2


# ===========================================================================
# S6 (reorder-feedback-9sep.md, G5 ruling 9 Sep 2026) - Confirm never sweeps,
# location grain (this file's own decision surface: Accept/Adjust/Reject).
# ===========================================================================

def test_confirm_drafts_only_the_accepted_rec_of_three_buy_recs(scm_app):
    """AC-S6.4(a), location grain: three buy recs (a, b, c), only `a` accepted.
    `ids=None` (every decided row of the run) drafts exactly `a`'s line - `b` and `c`
    get no PO line and no status change of their own."""
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    assert set(recs) >= {a, b, c}

    dsvc.accept_recommendation(db, recs[a]["id"], actor="tester")

    out = dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")

    assert out["confirmed_count"] == 1, "only the accepted rec is confirmed"
    assert _po_id_for_rec(db, recs[a]["id"]) is not None
    assert _po_id_for_rec(db, recs[b]["id"]) is None, "an untouched rec gets no PO line"
    assert _po_id_for_rec(db, recs[c]["id"]) is None, "an untouched rec gets no PO line"
    assert db.execute(text(
        "SELECT status FROM scm.reorder_recommendation WHERE id = :id"
    ), {"id": recs[b]["id"]}).scalar() == "proposed", "confirm must not itself decide it"


def test_confirm_with_nothing_accepted_confirms_nothing(scm_app):
    """AC-S6.4(b), location grain: nothing accepted/adjusted anywhere on the run -
    confirm answers 200 with confirmed=0, po_count=0, and drafts no PO line at all."""
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    assert set(recs) >= {a, b, c}

    out = dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")

    assert out["confirmed_count"] == 0
    assert out["po_count"] == 0
    for pid in (a, b, c):
        assert _po_id_for_rec(db, recs[pid]["id"]) is None


# ===========================================================================
# AC-M4.6 - on_order excludes a draft; includes it once confirmed (BOTH directions)
# ===========================================================================

def _ordered(db, product_id, warehouse_id) -> float:
    """The PLACED-order figure. `on_order_v` is the SPO allocation now (migration 337), and
    a PO the decision flow raises has nothing shipped against it, so it would read 0 there
    whether the flow worked or not."""
    v = db.execute(text(
        "SELECT ordered FROM scm.po_ordered_v WHERE product_id = :p AND warehouse_id = :w"
    ), {"p": product_id, "w": warehouse_id}).scalar()
    return float(v) if v is not None else 0.0


def test_draft_excluded_from_on_order_until_confirmed(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    wid = db.execute(text("SELECT id FROM warehouses WHERE warehouse_code = :c"),
                     {"c": wid_code}).scalar()
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    rec_a = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}[a]

    before = _ordered(db, a, wid)
    dsvc.accept_recommendation(db, rec_a["id"], actor="tester")
    db.flush()
    # accept alone stages nothing on-order (no PO yet)
    assert _ordered(db, a, wid) == before, "a staged accept must NOT appear as ordered"

    # confirm decisions → draft PO exists but is still OUTSIDE on_order (M4-D5)
    dsvc.confirm_decisions(db, run_id, ids=None, actor="tester")
    po_id = _po_id_for_rec(db, rec_a["id"])
    assert po_id is not None
    assert _ordered(db, a, wid) == before, "a draft PO must NOT appear as ordered"

    ordered_qty = float(db.execute(text(
        "SELECT qty_ordered FROM purchase_order_lines WHERE purchase_order_id = :id"
    ), {"id": po_id}).scalar())
    assert ordered_qty > 0

    # confirm the DRAFT → active → NOW counts as on_order (M4-D6)
    out = PurchaseOrderService(db).bulk_confirm([po_id], actor="tester")
    assert out["confirmed_count"] == 1
    assert _ordered(db, a, wid) == before + ordered_qty


def test_confirm_renumbers_draft_to_canonical_sequential(scm_app):
    _, db, _, _ = scm_app
    _ensure_po_numbering(db)
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    # two distinct suppliers → two distinct draft POs (materialised at confirm)
    dsvc.accept_recommendation(db, recs[a]["id"], actor="t")
    dsvc.accept_recommendation(db, recs[c]["id"], actor="t")
    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")
    po_shared = _po_id_for_rec(db, recs[a]["id"])
    po_other = _po_id_for_rec(db, recs[c]["id"])

    draft_nums = db.execute(text(
        "SELECT po_number FROM purchase_orders WHERE id::text = ANY(:ids)"
    ), {"ids": [po_shared, po_other]}).scalars().all()
    assert all(n.startswith("PO-DRAFT-") for n in draft_nums)

    PurchaseOrderService(db).bulk_confirm([po_shared, po_other], actor="t")
    rows = db.execute(text(
        "SELECT po_number, status FROM purchase_orders WHERE id::text = ANY(:ids) ORDER BY po_number"
    ), {"ids": [po_shared, po_other]}).mappings().all()
    for r in rows:
        assert r["status"] == "active"
        assert _PO_CANONICAL.match(r["po_number"]), r["po_number"]
    # sequential per the monthly running sequence (no NaN / gaps between the two)
    seqs = sorted(int(r["po_number"].split("-")[-1]) for r in rows)
    assert seqs[1] == seqs[0] + 1


def test_confirm_is_idempotent(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    dsvc.accept_recommendation(db, recs[a]["id"], actor="t")
    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")
    po_id = _po_id_for_rec(db, recs[a]["id"])

    svc = PurchaseOrderService(db)
    assert svc.bulk_confirm([po_id], actor="t")["confirmed_count"] == 1
    num_after_first = db.execute(text(
        "SELECT po_number FROM purchase_orders WHERE id = :id"), {"id": po_id}).scalar()
    # re-confirming an already-active PO is a no-op (count 0, number unchanged)
    assert svc.bulk_confirm([po_id], actor="t")["confirmed_count"] == 0
    assert db.execute(text(
        "SELECT po_number FROM purchase_orders WHERE id = :id"), {"id": po_id}).scalar() == num_after_first


# ===========================================================================
# AC-M4.7 - Adjust: append-only override + supplier switch/recompute
# ===========================================================================

def test_adjust_switches_supplier_and_appends_override(scm_app):
    _, db, _, _ = scm_app
    wid_code, p = _seed_two_supplier_product(db)
    run_id = _run_buys(db, wid_code, product_codes=_TWO_SUPPLIER_CODES)
    rec = _buy_recs(db, run_id)[0]
    rec_id = rec["id"]
    orig_qty = float(rec["rounded_qty"])

    # frozen alternatives carry the codes the UI offered
    inp = db.execute(text(
        "SELECT inputs FROM scm.reorder_recommendation WHERE id = :id"), {"id": rec_id}
    ).scalar()
    alts = inp.get("alternatives") or []
    chosen_code = (inp.get("supplier") or {}).get("supplier_code")
    alt = next(a for a in alts if a.get("supplier_code") and a["supplier_code"] != chosen_code)

    dsvc.accept_recommendation(db, rec_id, actor="t")   # staged on proposed supplier
    override_qty = orig_qty + 25
    res = dsvc.adjust_recommendation(
        db, rec_id, override_qty=override_qty,
        override_supplier_code=alt["supplier_code"],
        reason_text="cheaper supplier, buy less", actor="t")
    # adjust is STAGED - no PO yet
    assert res["draft_po_id"] is None

    # exactly ONE override row, append-only; original recommendation untouched
    ov_rows = db.execute(text(
        "SELECT override_qty, override_supplier_id, reason_text FROM scm.recommendation_override "
        "WHERE recommendation_id = :id ORDER BY created_at"
    ), {"id": rec_id}).mappings().all()
    assert len(ov_rows) == 1
    assert float(ov_rows[0]["override_qty"]) == override_qty
    alt_sid = db.execute(text("SELECT id FROM suppliers WHERE supplier_code = :c"),
                         {"c": alt["supplier_code"]}).scalar()
    assert str(ov_rows[0]["override_supplier_id"]) == str(alt_sid)
    # the recommendation row itself is NEVER mutated (qty stays, status flips only)
    reread = db.execute(text(
        "SELECT rounded_qty, supplier_id, status FROM scm.reorder_recommendation WHERE id = :id"
    ), {"id": rec_id}).mappings().first()
    assert float(reread["rounded_qty"]) == orig_qty
    assert reread["status"] == "adjusted"

    # confirm decisions → the draft PO line reflects override qty + switched supplier
    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")
    line = db.execute(text(
        "SELECT pol.qty_ordered, po.supplier_id, po.status FROM purchase_order_lines pol "
        "JOIN purchase_orders po ON po.id = pol.purchase_order_id WHERE pol.source_ref = :r"
    ), {"r": rec_id}).mappings().first()
    assert float(line["qty_ordered"]) == override_qty
    assert str(line["supplier_id"]) == str(alt_sid)
    assert line["status"] == "draft_recommendation"

    # a SECOND adjust (back to original supplier, new qty) appends a 2nd row and,
    # after re-confirm, redirects the draft line to the original supplier.
    dsvc.adjust_recommendation(db, rec_id, override_qty=orig_qty + 5,
                               override_supplier_code=None,
                               reason_text="actually keep original supplier", actor="t")
    ov2 = db.execute(text(
        "SELECT count(*) FROM scm.recommendation_override WHERE recommendation_id = :id"
    ), {"id": rec_id}).scalar()
    assert ov2 == 2
    assert float(ov_rows[0]["override_qty"]) == override_qty  # first row unchanged

    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")
    line2 = db.execute(text(
        "SELECT pol.qty_ordered, po.supplier_id FROM purchase_order_lines pol "
        "JOIN purchase_orders po ON po.id = pol.purchase_order_id WHERE pol.source_ref = :r"
    ), {"r": rec_id}).mappings().first()
    assert float(line2["qty_ordered"]) == orig_qty + 5
    assert str(line2["supplier_id"]) != str(alt_sid)  # redirected back to proposed supplier


def test_adjust_rejects_non_positive_qty(scm_app):
    _, db, _, _ = scm_app
    wid_code, p = _seed_two_supplier_product(db)
    run_id = _run_buys(db, wid_code, product_codes=_TWO_SUPPLIER_CODES)
    rec_id = _buy_recs(db, run_id)[0]["id"]
    from app.services.error_handler import AppException
    import pytest
    with pytest.raises(AppException):
        dsvc.adjust_recommendation(db, rec_id, override_qty=0,
                                   override_supplier_code=None, reason_text="x", actor="t")


# ===========================================================================
# AC-M4.8 - Reject dismisses + stores reason
# ===========================================================================

def test_reject_dismisses_and_stores_reason(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}

    dsvc.accept_recommendation(db, recs[a]["id"], actor="t")  # accept…
    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")   # …materialise its draft line
    assert _po_id_for_rec(db, recs[a]["id"]) is not None
    dsvc.reject_recommendation(db, recs[a]["id"], reason_text="discontinued line", actor="t")

    st = db.execute(text(
        "SELECT status FROM scm.reorder_recommendation WHERE id = :id"), {"id": recs[a]["id"]}
    ).scalar()
    assert st == "dismissed"
    reason = db.execute(text(
        "SELECT reason_text FROM scm.recommendation_override WHERE recommendation_id = :id "
        "ORDER BY created_at DESC LIMIT 1"), {"id": recs[a]["id"]}).scalar()
    assert reason == "discontinued line"
    # the rejected rec's line was pulled back out of the draft PO
    n = db.execute(text(
        "SELECT count(*) FROM purchase_order_lines WHERE source_ref = :r"), {"r": recs[a]["id"]}
    ).scalar()
    assert n == 0


# ===========================================================================
# AC-M4.9 - bulk accept + bulk confirm + create-GR
# ===========================================================================

def test_bulk_accept_then_bulk_confirm(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = _buy_recs(db, run_id)
    ids = [r["id"] for r in recs]

    res = dsvc.bulk_accept(db, run_id, ids, actor="t")
    assert res["accepted_count"] == 3
    assert res["po_count"] == 0   # staged only - no PO until Confirm decisions

    conf = dsvc.confirm_decisions(db, run_id, ids=None, actor="t")
    assert conf["confirmed_count"] == 3
    assert conf["po_count"] == 2  # two suppliers → two consolidated draft POs

    draft_ids = _draft_po_ids_for_recs(db, ids)
    assert len(draft_ids) == 2
    out = PurchaseOrderService(db).bulk_confirm(list(draft_ids), actor="t")
    assert out["confirmed_count"] == 2
    remaining = db.execute(text(
        "SELECT count(*) FROM purchase_orders WHERE id::text = ANY(:ids) AND status = 'active'"
    ), {"ids": list(draft_ids)}).scalar()
    assert remaining == 2


def test_create_gr_stamps_received_and_rejects_draft(scm_app):
    _, db, _, _ = scm_app
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    actor = str(uuid.uuid4())  # picked_by_user_id is a uuid FK to users in the DB
    dsvc.accept_recommendation(db, recs[a]["id"], actor=actor)
    dsvc.confirm_decisions(db, run_id, ids=None, actor=actor)
    po_id = _po_id_for_rec(db, recs[a]["id"])
    svc = PurchaseOrderService(db)

    # a GR cannot be created from a DRAFT PO
    from app.services.error_handler import AppException
    import pytest
    with pytest.raises(AppException):
        svc.create_gr(po_id, actor=actor)

    svc.bulk_confirm([po_id], actor=actor)
    gr = svc.create_gr(po_id, actor=actor)
    assert gr["gr_reference"].startswith("GR-")

    # qty_received stamped == qty_ordered on every line; PO now received
    rows = db.execute(text(
        "SELECT qty_ordered, qty_received FROM purchase_order_lines WHERE purchase_order_id = :id"
    ), {"id": po_id}).mappings().all()
    assert rows and all(float(r["qty_received"]) == float(r["qty_ordered"]) for r in rows)
    assert db.execute(text("SELECT status FROM purchase_orders WHERE id = :id"),
                      {"id": po_id}).scalar() == "received"
    # a goods_received picking_header links back to the PO
    ph = db.execute(text(
        "SELECT picking_number FROM picking_headers WHERE source_entity_type = 'purchase_order' "
        "AND source_entity_id = :id AND picking_type = 'goods_received'"), {"id": po_id}).scalar()
    assert ph == gr["gr_reference"]


# ===========================================================================
# decisions read (badges + PO hyperlink) reflects the current PO number
# ===========================================================================

def test_list_decisions_reflects_status_and_confirmed_number(scm_app):
    app, db = _client(scm_app, "purchasing")
    _, db2, _, _ = scm_app
    _ensure_po_numbering(db)
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}

    dsvc.accept_recommendation(db, recs[a]["id"], actor="t")
    dsvc.confirm_decisions(db, run_id, ids=None, actor="t")  # materialise a's draft
    po_id = _po_id_for_rec(db, recs[a]["id"])
    dsvc.reject_recommendation(db, recs[b]["id"], reason_text="too much", actor="t")
    db.commit()

    with TestClient(app) as client:
        body = client.get(f"/api/v1/scm/reorder-runs/{run_id}/decisions").json()
    by_rec = {d["recommendation_id"]: d for d in body["data"]}
    assert by_rec[recs[a]["id"]]["status"] == "accepted"
    assert by_rec[recs[a]["id"]]["draft_po_number"].startswith("PO-DRAFT-")
    assert by_rec[recs[b]["id"]]["status"] == "dismissed"
    assert by_rec[recs[b]["id"]]["reason_text"] == "too much"

    # after confirm, the SAME decision reflects the canonical number (hyperlink stays valid)
    PurchaseOrderService(db).bulk_confirm([po_id], actor="t")
    db.commit()
    with TestClient(app) as client:
        body2 = client.get(f"/api/v1/scm/reorder-runs/{run_id}/decisions").json()
    acc = next(d for d in body2["data"] if d["recommendation_id"] == recs[a]["id"])
    assert _PO_CANONICAL.match(acc["draft_po_number"]), acc["draft_po_number"]


# ===========================================================================
# auth
# ===========================================================================

def test_accept_denied_without_reorder_run_permission(scm_app):
    app, _ = _client(scm_app, None)  # bare user
    with TestClient(app) as client:
        res = client.post(f"/api/v1/scm/recommendations/{uuid.uuid4()}/accept")
    assert res.status_code == 403


def test_bulk_confirm_denied_without_reorder_run_permission(scm_app):
    app, _ = _client(scm_app, None)
    with TestClient(app) as client:
        res = client.post("/api/v1/scm/purchase-orders/bulk-confirm", json={"ids": []})
    assert res.status_code == 403


def test_decisions_read_denied_without_dashboard_view(scm_app):
    app, _ = _client(scm_app, None)
    with TestClient(app) as client:
        res = client.get(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/decisions")
    assert res.status_code == 403


def test_confirm_decisions_denied_without_reorder_run_permission(scm_app):
    app, _ = _client(scm_app, None)
    with TestClient(app) as client:
        res = client.post(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/confirm-decisions", json={"ids": []})
    assert res.status_code == 403


def test_confirm_decisions_endpoint_materialises_drafts(scm_app):
    app, db = _client(scm_app, "purchasing")
    wid_code, a, b, c = _seed_same_supplier(db)
    run_id = _run_buys(db, wid_code, product_codes=_SAME_SUPPLIER_CODES)
    recs = {str(r["product_id"]): r for r in _buy_recs(db, run_id)}
    for pid in (a, b, c):
        dsvc.accept_recommendation(db, recs[pid]["id"], actor="t")
    db.commit()

    with TestClient(app) as client:
        res = client.post(f"/api/v1/scm/reorder-runs/{run_id}/confirm-decisions", json={"ids": []})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed_count"] == 3
    assert body["po_count"] == 2
