"""REF-PROBES S1: exact resolver probes for SO number, customer PO ref, our PO number and
purchase request number (UAC ref-probes-acceptance-criteria.md AC-1 to AC-6).

Every row is seeded here on a blank Postgres schema (`blank_session`); nothing borrows
existing data. Probes are exact matches, never prefix or substring.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.order import SalesOrder
from app.models.procurement import PurchaseOrder, PurchaseRequestHeader
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.entity_resolver import resolve_references
from app.services.scm.po_ref import po_ref_key

from ._pg_fixture import blank_session, unique_code


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        current = session.execute(text("SHOW search_path")).scalar()
        session.execute(text(f"SET LOCAL search_path TO {current}, public"))
        set_company_scope(session, None)
        yield session


def _so(db, so_number: str, *, po_ref: str | None = None) -> str:
    sid = str(uuid.uuid4())
    db.add(
        SalesOrder(
            id=sid, company_id=DEFAULT_COMPANY_ID, so_number=so_number, status="open",
            po_ref=po_ref, po_ref_key=po_ref_key(po_ref),
        )
    )
    db.flush()
    return sid


def _po(db, po_number: str) -> str:
    pid = str(uuid.uuid4())
    db.add(PurchaseOrder(id=pid, company_id=DEFAULT_COMPANY_ID, po_number=po_number, status="active"))
    db.flush()
    return pid


def _pr(db, request_number: str) -> str:
    rid = str(uuid.uuid4())
    db.add(PurchaseRequestHeader(id=rid, request_type="purchase_request", request_number=request_number, status="draft"))
    db.flush()
    return rid


def _matches(db, token: str, types: list[str]):
    result = resolve_references(db, [token], allowed_entity_types=types, enable_embedding_fallback=False)
    for tr in result.resolutions:
        if tr.token.strip().lower() == token.strip().lower():
            return tr.matches
    return []


def _of(matches, entity_type: str):
    return [m for m in matches if m.entity_type == entity_type]


# --- AC-1 ------------------------------------------------------------------ #
def test_dashed_so_number_resolves_as_sales_order(db):
    stored = f"ZZT{unique_code()[:4].upper()}-0127"
    sid = _so(db, stored)
    typed = stored.lower().replace("-", " - ")  # lower case with spaces
    got = _of(_matches(db, typed, ["sales_order"]), "sales_order")
    assert len(got) == 1, got
    assert got[0].canonical_code == stored
    assert got[0].uuid == sid


def test_so_probe_is_exact_never_prefix(db):
    stored = f"ZZT{unique_code()[:4].upper()}-0127"
    _so(db, stored)
    assert _of(_matches(db, stored[:-1], ["sales_order"]), "sales_order") == []
    assert _of(_matches(db, stored[3:], ["sales_order"]), "sales_order") == []


# --- AC-4 ------------------------------------------------------------------ #
def test_sales_order_alias_is_its_own_type():
    from app.services.entity_resolver import _canonical_entity_type

    assert _canonical_entity_type("sales_order") == "sales_order"


def test_order_and_do_still_alias_delivery_order():
    from app.services.entity_resolver import _canonical_entity_type

    assert {_canonical_entity_type(w) for w in ("order", "do", "delivery_order")} == {"customer_order"}


# --- AC-2 ------------------------------------------------------------------ #
def test_po_ref_matches_by_folded_key(db):
    tag = unique_code()[:4].upper()
    _so(db, f"ZZT{tag}-1", po_ref=f"ZZT-{tag} / A")
    _so(db, f"ZZT{tag}-2", po_ref=f"ZZT-{tag} / A")
    got = _of(_matches(db, f"zzt {tag.lower()}/a", ["customer_po_ref"]), "customer_po_ref")
    assert len(got) == 2, got


# --- AC-3 ------------------------------------------------------------------ #
def test_purchase_order_exact_match(db):
    number = f"ZZT-PO-{unique_code()[:6].upper()}"
    pid = _po(db, number)
    got = _of(_matches(db, number.lower(), ["purchase_order"]), "purchase_order")
    assert len(got) == 1, got
    assert got[0].canonical_code == number
    assert got[0].uuid == pid


def test_purchase_order_is_exact_never_prefix(db):
    number = f"ZZT-PO-{unique_code()[:6].upper()}"
    _po(db, number)
    assert _of(_matches(db, number[:-1], ["purchase_order"]), "purchase_order") == []


def test_purchase_request_exact_match(db):
    number = f"ZZTPR{unique_code()[:5].upper()}"
    rid = _pr(db, number)
    got = _of(_matches(db, number.lower(), ["purchase_request"]), "purchase_request")
    assert len(got) == 1, got
    assert got[0].canonical_code == number
    assert got[0].uuid == rid


def test_duplicate_pr_number_returns_each_row(db):
    number = f"ZZTPR{unique_code()[:5].upper()}"
    _pr(db, number)
    _pr(db, number)
    got = _of(_matches(db, number, ["purchase_request"]), "purchase_request")
    assert len(got) == 2, got


# --- AC-5 ------------------------------------------------------------------ #
def test_so_and_po_matches_carry_company(db):
    so_number = f"ZZT{unique_code()[:4].upper()}-9"
    po_number = f"ZZT-PO-{unique_code()[:6].upper()}"
    _so(db, so_number)
    _po(db, po_number)
    so_m = _of(_matches(db, so_number, ["sales_order"]), "sales_order")
    po_m = _of(_matches(db, po_number, ["purchase_order"]), "purchase_order")
    assert so_m and po_m
    assert so_m[0].company_id == DEFAULT_COMPANY_ID
    assert po_m[0].company_id == DEFAULT_COMPANY_ID


# --- AC-6 ------------------------------------------------------------------ #
def test_fallback_never_reaches_new_kinds(db):
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post

    po_number = f"ZZT-PO-{unique_code()[:6].upper()}"
    so_number = f"ZZT{unique_code()[:4].upper()}-5"
    _po(db, po_number)
    _so(db, so_number, po_ref=po_number)
    for token in (po_number, so_number):
        payload = ResolveReferenceRequest(
            **{"tokens": [token], "allowed_entity_types": ["product"], "fallback_to_all_types": True}
        )
        out = resolve_reference_post(payload, current_user={}, db=db)
        blob = repr(out).lower()
        for kind in ("purchase_order", "purchase_request", "sales_order", "customer_po_ref"):
            assert f"'{kind}'" not in blob and f'"{kind}"' not in blob, (token, kind)
