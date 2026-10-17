"""CHAT-CODE-LOOKUP fix round 1 red tests (F1 set scope, F2 expansion scope, F3 set price,
F4 availability, F6 did-you-mean). Synthetic codes. Every behaviour stays behind the gate."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_brand_scope
from app.models.product import Brand, Product
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.head import candidates
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_code_lookup_set_codes import (  # noqa: F401
    BASE,
    BASE_PARTS,
    _entry_line,
    _lines,
    _message,
    _mk_products,
    _mk_set,
    _scope_listeners,
    db,
)
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401


def _brand(db, name: str) -> Brand:
    row = Brand(
        id=str(uuid.uuid4()), brand_code=unique_code(name)[:50], brand_name=f"ZZT {name}", company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _brand_products(db, code_to_brand: dict[str, Brand]) -> dict[str, str]:
    ids = _mk_products(db, list(code_to_brand))
    for code, brand in code_to_brand.items():
        db.query(Product).filter(Product.id == ids[code]).update({"brand_id": brand.id})
    db.flush()
    return ids


# ---------------------------------------------------------------- F1: shortlist set scope


def test_f1_a_set_whose_members_are_all_another_brand_is_not_listed(db):
    a, b = _brand(db, "A"), _brand(db, "B")
    _brand_products(db, {"ZZOTHR-ALPHA9": a})  # brand A exists on a product so the company is restricted
    ids = _brand_products(db, {BASE_PARTS[0]: b, BASE_PARTS[1]: b})
    _mk_set(db, BASE, list(ids.values()))
    set_brand_scope(db, frozenset({a.id}))

    shortlist = candidates.shortlist(db, f"stock {BASE.lower()}", code_lookup=True)

    assert _entry_line(candidates.candidate_lines(shortlist), BASE.lower()) is None
    assert BASE.replace("-", "").lower() not in candidates.listed_codes(shortlist)


def test_f1_a_mixed_brand_set_is_not_listed(db):
    a, b = _brand(db, "A"), _brand(db, "B")
    ids = _brand_products(db, {BASE_PARTS[0]: a, BASE_PARTS[1]: b})
    _mk_set(db, BASE, list(ids.values()))
    set_brand_scope(db, frozenset({a.id}))

    shortlist = candidates.shortlist(db, f"stock {BASE.lower()}", code_lookup=True)

    assert _entry_line(candidates.candidate_lines(shortlist), BASE.lower()) is None
    assert BASE.replace("-", "").lower() not in candidates.listed_codes(shortlist)


def test_f1_control_an_all_own_brand_set_is_listed(db):
    a = _brand(db, "A")
    ids = _brand_products(db, {BASE_PARTS[0]: a, BASE_PARTS[1]: a})
    _mk_set(db, BASE, list(ids.values()), name="S-trap 250")
    set_brand_scope(db, frozenset({a.id}))

    line = _entry_line(_lines(db, f"stock {BASE.lower()}", code_lookup=True), BASE.lower())

    assert line is not None and f"set code {BASE}" in line


# ---------------------------------------------------------------- engine helpers


def _run(
    session_factory, stub_access, monkeypatch, *, text, canonical, domain, intent, code_lookup,
    brand_scope=None, availability=False, tool=None, raw=None, entity_canonical="__same__", confident=True,
):
    cano = canonical if entity_canonical == "__same__" else entity_canonical
    verdict = _parser_output(
        intent_hint=intent, domain_hint=domain, user_goal="ask",
        entities=[{"raw": raw or canonical, "hint": "product", "canonical_code": cano, "current_message": True, "confident": confident}],
    )

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        extra = {"code_lookup": True} if code_lookup else {}
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, **extra,
        )

    calls: list[tuple[str, dict]] = []

    def fake_call(self, name, args):
        calls.append((name, args))
        return tool(name, args) if tool else json.dumps({"ok": True, "items": []})

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    if brand_scope is not None:
        monkeypatch.setattr(engine_mod, "_contact_brand_scope", lambda f, c: brand_scope)
    if availability:
        monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: True)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call)
    stub_access()
    result = engine_mod.run_turn(_envelope(message=_message(text)), session_factory=session_factory)
    return (result.reply or {}).get("text") or "", list(calls)


def _all_ids(calls) -> set[str]:
    return {pid for _n, a in calls for pid in (a.get("product_ids") or [])}


# ---------------------------------------------------------------- F2: exact set code expansion scope


def _seed_f2(session_factory, kind: str):
    db = session_factory()
    a, b = _brand(db, "A"), _brand(db, "B")
    if kind == "nonsearchable":
        ids = _mk_products(db, [BASE_PARTS[0]])
        ids.update(_mk_products(db, [BASE_PARTS[1]], searchable=False))
        _mk_set(db, BASE, list(ids.values()))
        scope = None
    elif kind == "inactive":
        ids = _mk_products(db, BASE_PARTS)
        _mk_set(db, BASE, list(ids.values()), active=False)
        scope = None
    else:
        ids = _brand_products(db, {BASE_PARTS[0]: a, BASE_PARTS[1]: b})
        _mk_set(db, BASE, list(ids.values()))
        scope = frozenset({a.id})
    db.commit()
    return ids, scope


@pytest.mark.parametrize("kind", ["nonsearchable", "inactive", "other_brand_member"])
def test_f2_an_exact_set_code_with_an_unseen_member_is_todays_miss(
    kind, session_factory, seeded, stub_access, monkeypatch
):
    ids, scope = _seed_f2(session_factory, kind)
    args = dict(text=f"stock {BASE}", canonical=BASE, domain="inventory", intent="check_stock", brand_scope=scope)

    on_text, on_calls = _run(session_factory, stub_access, monkeypatch, code_lookup=True, **args)
    off_text, _off_calls = _run(session_factory, stub_access, monkeypatch, code_lookup=False, **args)

    assert not (_all_ids(on_calls) & set(ids.values())), on_calls
    assert on_text == off_text, (on_text, off_text)


# ---------------------------------------------------------------- F3: a set answer carries no price


def _priced_tool(db_factory):
    """The real MCP presenter over raw priced member rows (the list price is in the rows)."""
    from tests.chatbot._r9_engine_console import _present_response

    present = _present_response()

    def tool(name: str, args: dict) -> str:
        db = db_factory()
        db.info["company_scope"] = None
        ids = list(args.get("product_ids") or [])
        rows = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
        data = [
            {
                "id": p.id, "product_id": p.id, "product_code": p.product_code, "product_name": p.product_name,
                "list_price": float(p.list_price or 0), "quantity_on_hand": 7, "total": 7,
            }
            for p in rows
        ]
        payload = {"data": data, "pagination": {"total": len(data), "page": 1, "limit": 50}}
        return present(name, json.dumps(payload))

    return tool


def _no_price(text: str) -> None:
    assert "List Price" not in text and "MYR" not in text and not re.search(r"\bRM\b", text), text


def _set_answer(session_factory, stub_access, monkeypatch, domain, intent):
    db = session_factory()
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()), name="S-trap 250")
    db.commit()
    text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"details of {BASE}", canonical=BASE, domain=domain,
        intent=intent, code_lookup=True, tool=_priced_tool(session_factory),
    )
    assert _all_ids(calls) == set(ids.values()), calls  # the members were fetched
    return text


_DOMAINS = [("master_products", "product_info"), ("inventory", "check_stock")]


@pytest.mark.parametrize("domain, intent", _DOMAINS)
def test_f3_a_set_answer_shows_no_price(domain, intent, session_factory, seeded, stub_access, monkeypatch):
    _no_price(_set_answer(session_factory, stub_access, monkeypatch, domain, intent))


@pytest.mark.parametrize("domain, intent", _DOMAINS)
def test_f3_a_set_success_answer_names_the_set_code(domain, intent, session_factory, seeded, stub_access, monkeypatch):
    assert BASE in _set_answer(session_factory, stub_access, monkeypatch, domain, intent)


def test_f3_control_a_plain_product_through_the_same_stub_shows_its_price(
    session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    _mk_products(db, BASE_PARTS)
    db.commit()

    text, _calls = _run(
        session_factory, stub_access, monkeypatch, text=f"details of {BASE_PARTS[0]}", canonical=BASE_PARTS[0],
        domain="master_products", intent="product_info", code_lookup=True, tool=_priced_tool(session_factory),
    )

    assert "List Price" in text or "MYR" in text, text


# ---------------------------------------------------------------- F4: availability contact, set code stock


def test_f4_an_availability_contact_asking_stock_of_a_set_code_runs_the_stock_tool_for_all_members(
    session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()), name="S-trap 250")
    db.commit()

    _text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"stock {BASE}", canonical=BASE, domain="inventory",
        intent="check_stock", code_lookup=True, availability=True,
    )

    stock = [a for n, a in calls if n == "crm_inventory_stock_balance_list" and a.get("product_ids")]
    assert stock and sorted(stock[0]["product_ids"]) == sorted(ids.values()), calls


# ---------------------------------------------------------------- F6: a typo with no pick is today's did-you-mean


def test_f6_a_confident_typo_with_no_canonical_code_is_todays_did_you_mean(
    session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    _mk_products(db, ["ZZMC66631", "ZZFAL66631"])
    db.commit()
    args = dict(
        text="Check stock ZZCM66631", canonical="ZZCM66631", raw="ZZCM66631", entity_canonical=None,
        domain="inventory", intent="check_stock", confident=True,
    )

    on_text, _c = _run(session_factory, stub_access, monkeypatch, code_lookup=True, **args)
    off_text, _c2 = _run(session_factory, stub_access, monkeypatch, code_lookup=False, **args)

    assert "Did you mean" in off_text, off_text
    assert on_text == off_text, (on_text, off_text)
    assert "couldn't tell which part" not in on_text.lower() and "could not tell which part" not in on_text.lower(), on_text
