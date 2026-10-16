"""SCM Policy Configuration - service layer.

CRUD for scoped ``scm.reorder_policy`` rows, single-row upserts for the two global
config tables (``abc_xyz_policy`` / ``supplier_scoring_policy``), and a resolution
preview that assembles its answer from the SAME shipped engine the reorder run uses
(``reorder_engine.resolve_policy_for_sku`` / ``load_policies`` / ``resolve_policy``) - 
never a reimplementation (Risk #1, AC-PREV-2).

Storage keys match the engine's ``_policy_matches`` comparison keys verbatim:
  sku → ``products.id`` · product_class → ``product_categories.category_code`` ·
  abc_xyz_cell → ``"A-X"`` · global → NULL.

The engine's two ``factor_toggles`` JSONB toggles (``supplier_selection`` /
``lead_time_default_days``) are surfaced as first-class row fields; writes MERGE
them into the existing JSONB so unrelated keys (e.g. ``safety_stock_manual``)
survive (Risk #7, AC-EDIT-2).

DB-dependent validation (AC-VAL-7 coherence, AC-VAL-8 uniqueness, AC-VAL-9
referential) raises ``AppException(422)``; pure-field validation lives in the
schema. Writes ``commit`` (mirrors the sibling ``config.py`` savepoint pattern).
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.scm_policy import (
    AbcXyzWrite,
    ReorderPolicyWrite,
    SupplierScoringWrite,
)
from app.utils.product_display import code_with_name
from app.services.error_handler import AppException
from app.services.scm.analytics_service import (
    DEFAULT_XYZ_X_MAX,
    DEFAULT_XYZ_Y_MAX,
    _abc_xyz_policy,
    _supplier_policy,
    ensure_policy_defaults,
)
from app.services.scm.reorder_policy import DEFAULT_COVER_SCOPE
from app.services.scm.reorder_engine import (
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_SUPPLIER_SELECTION,
    _policy_matches,
    load_category_code,
    load_classification,
    load_policies,
    policy_toggles,
    resolve_policy_for_sku,
)

# UI fraction-form defaults offered when no active classification row exists.
_DEFAULT_ABC_A_FRAC = 0.80
_DEFAULT_ABC_B_FRAC = 0.15
_DEFAULT_DELIVERY_WEIGHT = 0.5
_DEFAULT_QUALITY_WEIGHT = 0.5
_DEFAULT_GRACE_DAYS = 2
_DEFAULT_MIN_SAMPLE_SIZE = 3

_CELL_RE = re.compile(r"^[ABC]-[XYZ]$")

# server-side sort allowlist → SQL expression (mirrors the FE grid's sortable columns)
_SORT = {
    "scope_type": "rp.scope_type",
    "scope_label": "COALESCE(p.product_code, pc.category_name, rp.scope_ref, '')",
    "policy_type": "rp.policy_type",
    "service_level": "rp.service_level",
    "safety_stock_method": "rp.safety_stock_method",
    "safety_days": "rp.safety_days",
    "priority": "rp.priority",
    "is_active": "rp.is_active",
}

# Full row projection + label joins (products / categories) so no UUID surfaces.
_ROW_SELECT = """
    SELECT rp.id, rp.scope_type, rp.scope_ref, rp.policy_type, rp.service_level,
           rp.safety_stock_method, rp.safety_days, rp.review_period_days,
           rp.forecast_window_days, rp.baseline_source, rp.spike_handling, rp.buy_scope,
           rp.dead_stock_days, rp.overstock_days, rp.min_override, rp.max_override,
           rp.factor_toggles, rp.is_active, rp.priority,
           rp.trajectory_window_retail_months, rp.trajectory_window_project_months,
           rp.price_stale_after_days, rp.price_movement_threshold_pct, rp.cover_scope,
           p.product_code, p.product_name, pc.category_name, pc.category_code
    FROM scm.reorder_policy rp
    LEFT JOIN products p
      ON rp.scope_type = 'sku' AND p.id::text = rp.scope_ref
    LEFT JOIN product_categories pc
      ON rp.scope_type = 'product_class' AND pc.category_code = rp.scope_ref
"""


# --- serialization ----------------------------------------------------------

def _label(m) -> str:
    st = m["scope_type"]
    if st == "global":
        return "-"
    if st == "sku":
        if m["product_code"]:
            return code_with_name(m["product_code"], m["product_name"], " · ")
        return m["scope_ref"] or ""
    if st == "product_class":
        return m["category_name"] or m["category_code"] or m["scope_ref"] or ""
    return m["scope_ref"] or ""  # abc_xyz_cell → "A-X"


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _i(v) -> Optional[int]:
    return int(v) if v is not None else None


def _serialize(m) -> dict:
    toggles = policy_toggles({"factor_toggles": m["factor_toggles"]})
    return {
        "id": str(m["id"]),
        "scope_type": m["scope_type"],
        "scope_ref": m["scope_ref"],
        "scope_label": _label(m),
        "policy_type": m["policy_type"],
        "service_level": _f(m["service_level"]),
        # NULL method == the engine's fixed_days behaviour (safety_stock()'s else branch);
        # normalise so legacy/partial rows never break the typed response.
        "safety_stock_method": m["safety_stock_method"] or "fixed_days",
        "safety_days": _f(m["safety_days"]),
        "review_period_days": _i(m["review_period_days"]),
        "forecast_window_days": _i(m["forecast_window_days"]),
        "baseline_source": m["baseline_source"],
        "spike_handling": m["spike_handling"],
        "buy_scope": m["buy_scope"],
        "dead_stock_days": _i(m["dead_stock_days"]),
        "overstock_days": _i(m["overstock_days"]),
        "min_override": _f(m["min_override"]),
        "max_override": _f(m["max_override"]),
        "priority": _i(m["priority"]) or 0,
        "is_active": bool(m["is_active"]),
        "supplier_selection": toggles["supplier_selection"],
        "lead_time_default_days": _i(toggles["lead_time_default_days"]),
        # Both in BOTH manual builders (select + dict), or the FE never sees a saved value.
        "trajectory_window_retail_months": _i(m["trajectory_window_retail_months"]),
        "trajectory_window_project_months": _i(m["trajectory_window_project_months"]),
        "price_stale_after_days": _i(m["price_stale_after_days"]),
        "price_movement_threshold_pct": _f(m["price_movement_threshold_pct"]),
        # A row written before the column existed reads as the row's own site. NULL must
        # never surface as "the whole network" - that is the behaviour the knob ends.
        "cover_scope": m["cover_scope"] or DEFAULT_COVER_SCOPE,
    }


def _get_row(db: Session, policy_id: str) -> Optional[dict]:
    m = db.execute(text(_ROW_SELECT + " WHERE rp.id = :id"),
                   {"id": policy_id}).mappings().first()
    return _serialize(m) if m else None


def _require_row(db: Session, policy_id: str) -> dict:
    row = _get_row(db, policy_id)
    if row is None:  # pragma: no cover - the row was just written, always present
        raise AppException(status_code=500, message="Reorder policy row not found after write.")
    return row


# --- validation (DB-dependent - AC-VAL-7/8/9) -------------------------------

def _validate_referential(db: Session, body: ReorderPolicyWrite) -> None:
    if body.scope_type == "sku":
        if not db.execute(text("SELECT 1 FROM products WHERE id::text = :r"),
                          {"r": body.scope_ref}).first():
            raise AppException(status_code=422,
                               message="Referenced product does not exist.")
    elif body.scope_type == "product_class":
        if not db.execute(text("SELECT 1 FROM product_categories WHERE category_code = :r"),
                          {"r": body.scope_ref}).first():
            raise AppException(status_code=422,
                               message="Referenced product class does not exist.")
    elif body.scope_type == "abc_xyz_cell":
        if not _CELL_RE.match(body.scope_ref or ""):
            raise AppException(status_code=422,
                               message="abc_xyz_cell must be an 'A-X' style cell (A|B|C)-(X|Y|Z).")


def _validate_coherence(body: ReorderPolicyWrite) -> None:
    # AC-VAL-7 - the engine silently falls back to fixed_days at run time; block the
    # save so the config UI never lies about what the run will do.
    if body.safety_stock_method == "statistical" and body.service_level is None:
        raise AppException(status_code=422,
                           message="Statistical safety stock requires a service level.")


def _validate_unique(db: Session, body: ReorderPolicyWrite,
                     exclude_id: Optional[str] = None) -> None:
    # AC-VAL-8 - a second ACTIVE policy at the same (scope_type, scope_ref) would
    # silently tie-break on priority. Block it (editing the same row is allowed).
    if body.scope_type == "global":
        sql = ("SELECT 1 FROM scm.reorder_policy WHERE scope_type = 'global' "
               "AND scope_ref IS NULL AND is_active = true")
        params: dict[str, Any] = {}
    else:
        sql = ("SELECT 1 FROM scm.reorder_policy WHERE scope_type = :st "
               "AND scope_ref = :ref AND is_active = true")
        params = {"st": body.scope_type, "ref": body.scope_ref}
    if exclude_id:
        sql += " AND id <> :xid"
        params["xid"] = exclude_id
    if db.execute(text(sql), params).first():
        raise AppException(status_code=422,
                           message="A policy already exists for this scope.")


# --- reorder policy CRUD ----------------------------------------------------

def list_policies(db: Session, *, page: int, limit: int,
                  sort: Optional[str], direction: str,
                  query: Optional[str]) -> dict:
    where = "1=1"
    params: dict[str, Any] = {}
    if query:
        where = ("(rp.scope_type ILIKE :q OR rp.scope_ref ILIKE :q OR rp.policy_type ILIKE :q "
                 "OR p.product_code ILIKE :q OR p.product_name ILIKE :q "
                 "OR pc.category_name ILIKE :q OR pc.category_code ILIKE :q)")
        params["q"] = f"%{query}%"

    total = db.execute(text(
        f"SELECT count(*) FROM scm.reorder_policy rp "
        f"LEFT JOIN products p ON rp.scope_type = 'sku' AND p.id::text = rp.scope_ref "
        f"LEFT JOIN product_categories pc ON rp.scope_type = 'product_class' "
        f"AND pc.category_code = rp.scope_ref WHERE {where}"
    ), params).scalar() or 0

    sort_expr = _SORT.get(sort or "", None)
    order = (f"{sort_expr} {'DESC' if direction.lower() == 'desc' else 'ASC'} NULLS LAST"
             if sort_expr else "rp.created_at DESC")
    params["limit"] = limit
    params["offset"] = (page - 1) * limit
    rows = db.execute(text(
        f"{_ROW_SELECT} WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset"
    ), params).mappings().all()
    return {"data": [_serialize(r) for r in rows], "total": int(total)}


def _insert_params(body: ReorderPolicyWrite) -> dict:
    toggles = {"supplier_selection": body.supplier_selection,
               "lead_time_default_days": body.lead_time_default_days}
    return {
        "scope_type": body.scope_type, "scope_ref": body.scope_ref,
        "policy_type": body.policy_type, "service_level": body.service_level,
        "safety_stock_method": body.safety_stock_method, "safety_days": body.safety_days,
        "review_period_days": body.review_period_days,
        "forecast_window_days": body.forecast_window_days,
        "baseline_source": body.baseline_source, "spike_handling": body.spike_handling,
        "buy_scope": body.buy_scope, "dead_stock_days": body.dead_stock_days,
        "overstock_days": body.overstock_days, "min_override": body.min_override,
        "max_override": body.max_override, "is_active": body.is_active,
        "priority": body.priority, "toggles": json.dumps(toggles),
        "trajectory_window_retail_months": body.trajectory_window_retail_months,
        "trajectory_window_project_months": body.trajectory_window_project_months,
        "price_stale_after_days": body.price_stale_after_days,
        "price_movement_threshold_pct": body.price_movement_threshold_pct,
    }


def create_policy(db: Session, body: ReorderPolicyWrite) -> dict:
    _validate_referential(db, body)
    _validate_coherence(body)
    _validate_unique(db, body)
    pid = str(uuid.uuid4())
    params = _insert_params(body)
    params["id"] = pid
    db.execute(text(
        "INSERT INTO scm.reorder_policy "
        "(id, scope_type, scope_ref, policy_type, service_level, safety_stock_method, "
        " safety_days, review_period_days, forecast_window_days, baseline_source, "
        " spike_handling, buy_scope, dead_stock_days, overstock_days, min_override, "
        " max_override, factor_toggles, is_active, priority, "
        " trajectory_window_retail_months, trajectory_window_project_months, "
        " price_stale_after_days, price_movement_threshold_pct, "
        " source_system, source_ref, created_at, updated_at) "
        "VALUES (:id, :scope_type, :scope_ref, :policy_type, :service_level, "
        " :safety_stock_method, :safety_days, :review_period_days, :forecast_window_days, "
        " :baseline_source, :spike_handling, :buy_scope, :dead_stock_days, :overstock_days, "
        " :min_override, :max_override, CAST(:toggles AS jsonb), :is_active, :priority, "
        " :trajectory_window_retail_months, :trajectory_window_project_months, "
        " :price_stale_after_days, :price_movement_threshold_pct, "
        " 'manual', 'ui', now(), now())"
    ), params)
    db.commit()
    return _require_row(db, pid)


def update_policy(db: Session, policy_id: str, body: ReorderPolicyWrite) -> dict:
    existing = db.execute(text(
        "SELECT id, factor_toggles FROM scm.reorder_policy WHERE id = :id"
    ), {"id": policy_id}).mappings().first()
    if not existing:
        raise AppException(status_code=404, message="Reorder policy not found.")
    _validate_referential(db, body)
    _validate_coherence(body)
    _validate_unique(db, body, exclude_id=policy_id)

    # Merge the two hoisted toggles into the existing JSONB (never clobber others).
    toggles = dict(existing["factor_toggles"] or {})
    toggles["supplier_selection"] = body.supplier_selection
    toggles["lead_time_default_days"] = body.lead_time_default_days

    params = _insert_params(body)
    params["id"] = policy_id
    params["toggles"] = json.dumps(toggles)
    db.execute(text(
        "UPDATE scm.reorder_policy SET scope_type=:scope_type, scope_ref=:scope_ref, "
        "policy_type=:policy_type, service_level=:service_level, "
        "safety_stock_method=:safety_stock_method, safety_days=:safety_days, "
        "review_period_days=:review_period_days, forecast_window_days=:forecast_window_days, "
        "baseline_source=:baseline_source, spike_handling=:spike_handling, buy_scope=:buy_scope, "
        "dead_stock_days=:dead_stock_days, overstock_days=:overstock_days, "
        "min_override=:min_override, max_override=:max_override, "
        "factor_toggles=CAST(:toggles AS jsonb), is_active=:is_active, priority=:priority, "
        "trajectory_window_retail_months=:trajectory_window_retail_months, "
        "trajectory_window_project_months=:trajectory_window_project_months, "
        "price_stale_after_days=:price_stale_after_days, "
        "price_movement_threshold_pct=:price_movement_threshold_pct, "
        # cover_scope is deliberately NOT in this SET list: it is a global setting owned by
        # PUT /scm/config/cover-scope, and writing it from here reset it to the schema
        # default on every unrelated policy save.
        "updated_at=now() WHERE id=:id"
    ), params)
    db.commit()
    return _require_row(db, policy_id)


def delete_policy(db: Session, policy_id: str) -> None:
    row = db.execute(text(
        "SELECT scope_type FROM scm.reorder_policy WHERE id = :id"
    ), {"id": policy_id}).mappings().first()
    if not row:
        raise AppException(status_code=404, message="Reorder policy not found.")
    # AC-DEL-2 - the global default is edited, never deleted.
    if row["scope_type"] == "global":
        raise AppException(status_code=422,
                           message="The global default policy cannot be deleted.")
    db.execute(text("DELETE FROM scm.reorder_policy WHERE id = :id"), {"id": policy_id})
    db.commit()


# --- classification thresholds (single global row, AC-CFG-*) ----------------

def get_classification(db: Session) -> dict:
    row = db.execute(text(
        "SELECT abc_a_pct, abc_b_pct, xyz_x_max, xyz_y_max FROM scm.abc_xyz_policy "
        "WHERE is_active = true ORDER BY updated_at DESC, created_at ASC LIMIT 1"
    )).mappings().first()
    if not row:
        return {"abc_a_pct": _DEFAULT_ABC_A_FRAC, "abc_b_pct": _DEFAULT_ABC_B_FRAC,
                "xyz_x_max": DEFAULT_XYZ_X_MAX, "xyz_y_max": DEFAULT_XYZ_Y_MAX,
                "exists": False}
    a = float(row["abc_a_pct"])
    b = float(row["abc_b_pct"])
    if a > 1:  # stored cumulative (80, 95) → convert back to fraction form for the UI
        a_frac = a / 100.0
        b_frac = (b - a) / 100.0
    else:      # already fractional (0.80, 0.15) → return verbatim
        a_frac, b_frac = a, b
    return {"abc_a_pct": a_frac, "abc_b_pct": b_frac,
            "xyz_x_max": float(row["xyz_x_max"]), "xyz_y_max": float(row["xyz_y_max"]),
            "exists": True}


def put_classification(db: Session, body: AbcXyzWrite) -> dict:
    ensure_policy_defaults(db)  # guarantee exactly one active row to update
    rid = db.execute(text(
        "SELECT id FROM scm.abc_xyz_policy WHERE is_active = true "
        "ORDER BY updated_at DESC, created_at ASC LIMIT 1"
    )).scalar()
    # Store the fraction values verbatim - the analytics engine's _normalize_abc_cuts
    # reads (0.80, 0.15) and maps it to cumulative (80, 95) with no code change.
    db.execute(text(
        "UPDATE scm.abc_xyz_policy SET abc_a_pct=:a, abc_b_pct=:b, xyz_x_max=:x, "
        "xyz_y_max=:y, is_active=true, updated_at=now() WHERE id=:id"
    ), {"a": body.abc_a_pct, "b": body.abc_b_pct, "x": body.xyz_x_max,
        "y": body.xyz_y_max, "id": rid})
    db.commit()
    return get_classification(db)


# --- supplier scoring (single global row, AC-SUP-*) -------------------------

def get_supplier_scoring(db: Session) -> dict:
    row = db.execute(text(
        "SELECT delivery_weight, quality_weight, grace_days, min_sample_size "
        "FROM scm.supplier_scoring_policy WHERE is_active = true "
        "ORDER BY updated_at DESC, created_at ASC LIMIT 1"
    )).mappings().first()
    if not row:
        return {"delivery_weight": _DEFAULT_DELIVERY_WEIGHT,
                "quality_weight": _DEFAULT_QUALITY_WEIGHT,
                "grace_days": _DEFAULT_GRACE_DAYS,
                "min_sample_size": _DEFAULT_MIN_SAMPLE_SIZE, "exists": False}
    return {"delivery_weight": float(row["delivery_weight"]),
            "quality_weight": float(row["quality_weight"]),
            "grace_days": int(row["grace_days"]),
            "min_sample_size": int(row["min_sample_size"]), "exists": True}


def put_supplier_scoring(db: Session, body: SupplierScoringWrite) -> dict:
    ensure_policy_defaults(db)
    rid = db.execute(text(
        "SELECT id FROM scm.supplier_scoring_policy WHERE is_active = true "
        "ORDER BY updated_at DESC, created_at ASC LIMIT 1"
    )).scalar()
    db.execute(text(
        "UPDATE scm.supplier_scoring_policy SET delivery_weight=:dw, quality_weight=:qw, "
        "grace_days=:g, min_sample_size=:m, is_active=true, updated_at=now() WHERE id=:id"
    ), {"dw": body.delivery_weight, "qw": body.quality_weight, "g": body.grace_days,
        "m": body.min_sample_size, "id": rid})
    db.commit()
    return get_supplier_scoring(db)


# --- resolution preview (AC-PREV-*) -----------------------------------------

_PRECEDENCE = ("sku", "abc_xyz_cell", "product_class", "global")


def _inactive_at_scope(db: Session, scope: str, product_id: str,
                       cell: Optional[str], category: Optional[str]) -> bool:
    """Does an INACTIVE policy exist that WOULD match this SKU at ``scope``?"""
    if scope == "sku":
        ref, params = "scope_ref = :r", {"r": product_id}
    elif scope == "abc_xyz_cell":
        if cell is None:
            return False
        ref, params = "scope_ref = :r", {"r": cell}
    elif scope == "product_class":
        if category is None:
            return False
        ref, params = "scope_ref = :r", {"r": category}
    else:  # global
        ref, params = "scope_ref IS NULL", {}
    row = db.execute(text(
        f"SELECT 1 FROM scm.reorder_policy WHERE scope_type = :st AND {ref} "
        f"AND is_active = false LIMIT 1"
    ), {"st": scope, **params}).first()
    return row is not None


def resolve(db: Session, product_id: str, warehouse_ref: Optional[str]) -> dict:
    prod = db.execute(text(
        "SELECT product_code, product_name FROM products WHERE id::text = :pid"
    ), {"pid": product_id}).mappings().first()
    if not prod:
        raise AppException(status_code=422, message="Product not found.")

    warehouse = None
    warehouse_id: Optional[str] = None
    if warehouse_ref:
        # The FE picker submits a warehouse_code; accept a UUID too for robustness.
        wh = db.execute(text(
            "SELECT id::text AS id, warehouse_code, warehouse_name FROM warehouses "
            "WHERE id::text = :w OR warehouse_code = :w LIMIT 1"
        ), {"w": warehouse_ref}).mappings().first()
        if wh:
            warehouse_id = wh["id"]
            warehouse = {"warehouse_code": wh["warehouse_code"],
                         "warehouse_name": wh["warehouse_name"]}

    # The winner comes from the SAME engine resolver the reorder run uses (AC-PREV-2).
    winner_dict = resolve_policy_for_sku(db, product_id, warehouse_id)
    winner_row = _get_row(db, str(winner_dict["id"])) if winner_dict else None
    winner_scope = winner_dict["scope_type"] if winner_dict else None

    # The SKU's own resolved cell + product class (null → those scopes are no-match).
    cls = load_classification(db, product_id, warehouse_id)
    cell = None
    if cls and cls.get("abc_class") and cls.get("xyz_class"):
        cell = f'{cls["abc_class"]}-{cls["xyz_class"]}'
    category = load_category_code(db, product_id)
    category_name = None
    if category:
        category_name = db.execute(text(
            "SELECT category_name FROM product_categories WHERE category_code = :c"
        ), {"c": category}).scalar()

    policies = load_policies(db)  # active only
    ref_by_scope = {"sku": product_id, "abc_xyz_cell": cell,
                    "product_class": category, "global": None}
    label_by_scope = {
        "sku": code_with_name(prod["product_code"], prod["product_name"], " · "),
        "abc_xyz_cell": cell or "-",
        "product_class": (category_name or category or "-"),
        "global": "-",
    }

    chain = []
    for scope in _PRECEDENCE:
        active_matches = [p for p in policies
                          if p["scope_type"] == scope
                          and _policy_matches(p, product_id, cell, category)]
        matched = len(active_matches) > 0
        is_winner = matched and winner_scope == scope
        if matched:
            reason = ("priority-tiebreak"
                      if (is_winner and len(active_matches) > 1)
                      else "most-specific-active")
        elif _inactive_at_scope(db, scope, product_id, cell, category):
            reason = "inactive"
        else:
            reason = "no-match"
        chain.append({
            "scope_type": scope,
            "scope_ref": ref_by_scope[scope],
            "scope_label": label_by_scope[scope],
            "matched": matched,
            "is_winner": is_winner,
            "reason": reason,
        })

    return {
        "product": {"product_code": prod["product_code"],
                    "product_name": prod["product_name"]},
        "warehouse": warehouse,
        "abc_xyz_cell": cell,
        "product_class": category,
        "winner": winner_row,
        "chain": chain,
    }
