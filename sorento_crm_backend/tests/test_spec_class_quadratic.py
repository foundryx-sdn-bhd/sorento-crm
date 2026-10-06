"""SPEC-CLASS-QUADRATIC: the class lookup inside `search_specs(exact=True)` must not grow
with the square of the message's word count.

Prod, 6 Oct 2026 20:02 MYT: a 15-line stock check ("CODE x QTY" per line, one line
prefixed with the word "TAP", one duplicate line, then "check stock") made
POST /external/chat/turn overrun n8n's budget. The resolve route handed the WHOLE message
to `search_specs` as `free_terms[0]` (`product_spec_understanding.understand_phrase`,
`free_terms=[phrase]`), and `_exact_classes` enumerated every word span of it (57 words
-> 1,653 spans), calling `resolve_classes_for_term` per span, three uncached queries each,
twice (`product_spec_search.py:1227` and `:1229`). Measured on the replay: 3,406 resolver
calls, 10,581 SQL statements, 18 s inside psycopg2.

The same message re-sent at 20:06 without the word "TAP" answered in 8.4 s: every token
resolved to a code, so `references.py`'s spec-fallback gate
(`not _every_caller_token_resolved(result)`) short-circuited and `search_specs` never ran.
One word the resolver cannot place is what opens the quadratic path.

Plan: documentation/plans/chatbot/PLAN-spec-class-quadratic.md (AC-SCQ-01 .. 06).
"""
from __future__ import annotations

import time
import uuid
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import product_spec_search
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from tests._pg_fixture import blank_session

ENDPOINT = "/api/v1/system/references/resolve"
_USER = {"id": str(uuid.uuid4()), "email": "n8n@example.com"}

# Fourteen codes shaped like the live ones (a letter prefix, digits, some with a dashed
# variant suffix so `_exact_classes`' word split sees the same 57 words the replay did).
CODES = [
    "ZZTWT5608",
    "ZZTWC286-SH",
    "ZZTSWT3001-GM",
    "ZZTW2000-CR",
    "ZZTKS1000",
    "ZZTKS8045-BK",
    "ZZTWB7104-WH",
    "ZZTAV101",
    "ZZTSH9001-RN",
    "ZZTWT7445",
    "ZZTWC364-HP",
    "ZZTBF220-GM",
    "ZZTWB6622-PP",
    "ZZTKS2001",
]
QUANTITIES = [5, 2, 1, 3, 4, 2, 6, 10, 1, 2, 3, 1, 2, 5]


def _message(*, tap_prefix: bool) -> str:
    """The owner's 15-line shape: 14 unique "CODE x QTY" lines, line 8 prefixed with the
    word "TAP" (the 20:02 message) or not (the 20:06 one), one duplicate line, then
    "check stock"."""
    lines = [f"{code} x {qty}" for code, qty in zip(CODES, QUANTITIES)]
    if tap_prefix:
        lines[7] = "TAP " + lines[7]
    lines.append(lines[0])  # the duplicate line
    lines.append("check stock")
    return "\n".join(lines)


def _tokens(*, tap_prefix: bool) -> list[str]:
    """What the chatbot's resolve gate sends as `tokens`: one product token per line
    (`resolve_gate._token_of`), the duplicate collapsed by the parser."""
    tokens = list(CODES)
    if tap_prefix:
        tokens[7] = "TAP " + tokens[7]
    return tokens


@pytest.fixture
def db():
    with blank_session() as s:
        cats = {
            code: ProductCategory(id=str(uuid.uuid4()), category_code=code, category_name=code)
            for code in ("SRT-WT", "SRT-WC", "SRT-KS", "SRT-WB", "SRT-FT", "SRT-SH")
        }
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-PCS", uom_name="Piece")
        s.add_all([*cats.values(), uom])
        s.flush()
        backfill_category_signals(s)
        seed_spec_registry(s)
        category_for = {
            "WT": "SRT-WT", "WC": "SRT-WC", "KS": "SRT-KS", "WB": "SRT-WB",
            "AV": "SRT-FT", "SH": "SRT-SH", "BF": "SRT-FT", "SW": "SRT-WT", "W2": "SRT-WT",
        }
        for code in CODES:
            kind = code[3:5]
            cat = cats[category_for.get(kind, "SRT-FT")]
            product = Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=code,
                description=f"SORENTO {code} TEST PRODUCT",
                category_id=cat.id,
                base_uom_id=uom.id,
                list_price=Decimal("1.00"),
            )
            s.add(product)
            s.flush()
            derive_for_code(s, code)
        yield s


@pytest.fixture
def client(db):
    from app.database import get_db
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_external_api_user,
    )
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_external_api_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@contextmanager
def _sql_counter(db):
    """Every statement the session's connection sends while the block runs."""
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", _record)


def _stored_label_reads(statements: list[str]) -> int:
    """How many times `stored_class_labels`' DISTINCT scan ran (its SQL is unique)."""
    return sum(1 for s in statements if "values->'class'->>'value'" in s)


def _chatbot_body(*, tap_prefix: bool) -> dict:
    """The body `resolve_gate.run` builds for a multi-code stock turn (match_mode "or"
    for two or more product tokens, `spec_fallback` + `exact_match` + `understand_phrase`
    always on), as a dry run."""
    return {
        "query": _message(tap_prefix=tap_prefix),
        "match_mode": "or",
        "tokens": _tokens(tap_prefix=tap_prefix),
        "allowed_entity_types": ["product"] * len(CODES),
        "access_levels": [],
        "domain": "inventory",
        "fallback_to_all_types": True,
        "limit": 15,
        "spec_fallback": True,
        "exact_match": True,
        "understand_phrase": True,
        "dry_run": True,
    }


# --------------------------------------------------------------------------- #
# AC-SCQ-01 / AC-SCQ-02: the ranker's class stage is bounded, whatever the word count
# --------------------------------------------------------------------------- #
def test_a_15_line_code_list_costs_a_bounded_number_of_statements(db):
    """The exact stage the profile blamed: `search_specs(exact=True)` with the whole
    message as a free term. Red on main: ~10k statements, ~1.7k `stored_class_labels`
    scans."""
    message = _message(tap_prefix=True)
    assert len([w for w in __import__("re").split(r"[^a-z0-9]+", message.lower()) if w]) == 57

    with _sql_counter(db) as statements:
        product_spec_search.search_specs(db, free_terms=[message], exact=True)

    assert len(statements) < 50, f"{len(statements)} statements for one exact search"
    assert _stored_label_reads(statements) <= 1, (
        f"stored_class_labels scanned {_stored_label_reads(statements)} times in one request"
    )


def test_exact_classes_runs_once_per_distinct_free_term(db, monkeypatch):
    """AC-SCQ-04: `product_spec_search.py:1229` re-ran `_exact_classes` over every term
    `:1227` had just resolved. One pass per distinct term, and the same verdict."""
    calls: Counter[str] = Counter()
    real = product_spec_search._exact_classes

    def _counting(db_, term, *args, **kwargs):
        calls[term] += 1
        return real(db_, term, *args, **kwargs)

    monkeypatch.setattr(product_spec_search, "_exact_classes", _counting)
    terms = [_message(tap_prefix=True), "tap", "check stock", "zztwt5608"]
    product_spec_search.search_specs(db, free_terms=terms, exact=True)

    assert calls, "exact mode must still resolve the free terms"
    assert max(calls.values()) == 1, dict(calls)


def test_the_class_vocabulary_is_read_once_per_search(db):
    """AC-SCQ-02: however many terms and spans one `search_specs` call resolves, the
    three vocabulary sources (ProductCategory rows, stored class labels, the `class`
    registry row) are each read from the database at most once."""
    terms = ["tap", "basin", "kitchen sink", "zztwt5608 x 5", "check stock", "tap"]
    with _sql_counter(db) as statements:
        product_spec_search.search_specs(db, free_terms=terms, exact=True)

    assert _stored_label_reads(statements) <= 1, statements
    categories = [s for s in statements if "product_categories" in s and "class_label IS NOT NULL" in s]
    assert len(categories) <= 1, categories


def test_a_fresh_lookup_sees_a_category_added_in_the_same_session(db):
    """AC-SCQ-03: the vocabulary is a per-call snapshot, never a process-wide cache: a
    row written after one lookup is seen by the next (`backfill_category_signals` runs
    in the same session on the admin path)."""
    from app.services.product_class_signal import resolve_classes_for_term

    assert resolve_classes_for_term(db, "squatting pan") == []
    row = ProductCategory(
        id=str(uuid.uuid4()),
        category_code="SRT-SP",
        category_name="SRT-SP",
        class_label="Squatting Pan",
        is_searchable=True,
    )
    db.add(row)
    db.flush()
    assert resolve_classes_for_term(db, "squatting pan") == ["Squatting Pan"]


# --------------------------------------------------------------------------- #
# The two prod paths, end to end through the resolve route (AC-SCQ-06)
# --------------------------------------------------------------------------- #
def test_the_tap_variant_opens_spec_search_and_stays_fast(client, db):
    """20:02 MYT: "TAP ZZTWT5608" is a token no code matches, so the gate at
    `references.py:3090` opens and the whole message reaches `search_specs`. The turn
    must still finish in a bounded number of statements and well inside n8n's budget."""
    started = time.monotonic()
    with _sql_counter(db) as statements:
        response = client.post(ENDPOINT, json=_chatbot_body(tap_prefix=True))
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    body = response.json()
    # Evidence line for the PR (run with -s): wall time and statement counts.
    print(
        f"\nTAP variant: {elapsed:.2f}s, {len(statements)} statements, "
        f"{_stored_label_reads(statements)} stored_class_labels scans"
    )
    assert "spec_candidates" in body, "the unresolved TAP token must open the spec fallback"
    assert _stored_label_reads(statements) <= 2, (
        f"stored_class_labels scanned {_stored_label_reads(statements)} times"
    )
    assert len(statements) < 400, f"{len(statements)} statements for the TAP variant"
    assert elapsed < 10, f"{elapsed:.1f}s for the TAP variant"


def test_without_the_tap_word_spec_search_never_runs(client, db):
    """20:06 MYT: every token resolves to its code, `_every_caller_token_resolved` is
    True and the spec fallback is skipped (the 8.4 s turn). Green on main; pinned so the
    fix cannot widen the gate."""
    started = time.monotonic()
    with _sql_counter(db) as statements:
        response = client.post(ENDPOINT, json=_chatbot_body(tap_prefix=False))
    print(f"\nno-TAP variant: {time.monotonic() - started:.2f}s, {len(statements)} statements")

    assert response.status_code == 200, response.text
    body = response.json()
    assert all(tr.get("matches") for tr in body["resolutions"]), body["resolutions"]
    assert "spec_candidates" not in body
