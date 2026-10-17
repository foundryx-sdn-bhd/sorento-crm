"""CHAT-CODE-LOOKUP slice B (R2, U2.1 to U2.4): a code that exists only as a product set is a
code. Shortlist lists it (`set code X (name)`), the engine resolves it to the set's members
for product info and stock, and a set answer carries no price. Gate off keeps today's miss.
Synthetic codes shaped like SRTWC8608-RL (S-trap set) and SRTWC8608-P-RL (P-trap set)."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_set import ProductSet, ProductSetMember
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import candidates
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

OTHER_COMPANY = "00000000-0000-0000-0000-000000000002"
BASE = "ZZSTWC8608-RL"  # the S-trap set, no product carries this code
VARIANT = "ZZSTWC8608-P-RL"  # the P-trap set
BASE_PARTS = ("ZZPEDA-ALPHA1", "ZZCIST-BETA2")  # dissimilar to the set codes: no product near lines
VARIANT_PARTS = ("ZZPEDB-GAMMA3", "ZZCIST-DELTA4")


def _fold(code: str) -> str:
    return code.replace("-", "").replace(" ", "").casefold()


def _mk_products(db, codes, *, company_id=DEFAULT_COMPANY_ID, searchable=True) -> dict[str, str]:
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=company_id,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=company_id)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in codes:
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name of {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=250, company_id=company_id, is_searchable=searchable,
        )
        db.add(p)
        ids[code] = p.id
    db.flush()
    return ids


def _mk_set(db, code, member_ids, *, name=None, company_id=DEFAULT_COMPANY_ID, active=True) -> str:
    s = ProductSet(
        id=str(uuid.uuid4()), set_code=code, name=name or f"Set {code}", company_id=company_id, is_active=active,
    )
    db.add(s)
    db.flush()
    for n, pid in enumerate(member_ids):
        db.add(ProductSetMember(id=str(uuid.uuid4()), product_set_id=s.id, product_id=pid, quantity=1, sort_order=n))
    db.flush()
    return s.id


def _lines(db, message, **kw):
    return candidates.candidate_lines(candidates.shortlist(db, message, **kw))


def _entry_line(lines, word):
    prefix = f'- "{word}": '
    return next((line for line in lines if line.startswith(prefix)), None)


# ---------------------------------------------------------------- shortlist (U2.1, U2.4)


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.company import Company

        session.add(Company(id=OTHER_COMPANY, name="Other", code=unique_code("OTH")[:20]))
        session.flush()
        yield session


def test_a_set_only_code_is_listed_as_a_set_code_line(db):
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()), name="S-trap 250")
    assert db.query(Product).filter(Product.product_code == BASE).count() == 0

    line = _entry_line(_lines(db, f"stock {BASE.lower()}", code_lookup=True), BASE.lower())

    assert line is not None and f"set code {BASE} (S-trap 250)" in line


def test_the_set_code_match_folds_case_spaces_and_dashes(db):
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()), name="S-trap 250")
    typed = _fold(BASE)
    assert typed == "zzstwc8608rl"

    line = _entry_line(_lines(db, f"stock {typed}", code_lookup=True), typed)

    assert line is not None and f"set code {BASE} (S-trap 250)" in line


def test_a_set_code_is_in_listed_codes(db):
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()))

    listed = candidates.listed_codes(candidates.shortlist(db, f"stock {BASE.lower()}", code_lookup=True))

    assert _fold(BASE) in listed


def test_a_word_holding_a_set_code_lists_it_and_the_near_set_variant_survives_product_near_codes(db):
    """The typed word holds the base set code, and the variant is only near it; six product
    codes near the word score no higher (under the FOLDED code trigrams: lower case, spaces and
    dashes removed) and must not push the set variant out of the cap."""
    ids = _mk_products(db, BASE_PARTS + VARIANT_PARTS)
    _mk_set(db, BASE, [ids[c] for c in BASE_PARTS], name="S-trap 250")
    _mk_set(db, VARIANT, [ids[c] for c in VARIANT_PARTS], name="P-trap 250")
    typed = _fold(BASE)
    sim_variant = candidates.similarity(candidates.trigrams(typed), candidates.trigrams(_fold(VARIANT)))
    decoys = [f"ZZSTWC8608-RX{n}" for n in range(6)]
    _mk_products(db, decoys)
    for d in decoys:
        sim_d = candidates.similarity(candidates.trigrams(typed), candidates.trigrams(_fold(d)))
        assert candidates.NEAR_CODE <= sim_d <= sim_variant + 1e-9, (d, sim_d, sim_variant)

    line = _entry_line(_lines(db, f"stock {typed}", code_lookup=True), typed)

    assert line is not None
    assert f"set code {BASE}" in line
    assert f"set code {VARIANT}" in line
    assert line.count("code ") <= candidates.CAP_PER_WORD * 2  # sanity: capped line


def test_a_set_of_another_company_is_not_listed(db):
    ids = _mk_products(db, BASE_PARTS, company_id=OTHER_COMPANY)
    _mk_set(db, BASE, list(ids.values()), company_id=OTHER_COMPANY)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    assert _lines(db, f"stock {BASE.lower()}", code_lookup=True) == []


def test_a_set_with_one_invisible_member_is_not_listed(db):
    seen = _mk_products(db, [BASE_PARTS[0]])
    hidden = _mk_products(db, [BASE_PARTS[1]], searchable=False)
    _mk_set(db, BASE, [seen[BASE_PARTS[0]], hidden[BASE_PARTS[1]]])

    assert _lines(db, f"stock {BASE.lower()}", code_lookup=True) == []


def test_an_inactive_set_is_not_listed(db):
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()), active=False)

    assert _lines(db, f"stock {BASE.lower()}", code_lookup=True) == []


def test_gate_off_lists_no_set_lines(db):
    ids = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(ids.values()))

    assert _lines(db, f"stock {BASE.lower()}") == []
    assert _lines(db, f"stock {BASE.lower()}", code_lookup=False) == []


# ---------------------------------------------------------------- engine (U2.1 to U2.3)


def _seed(session_factory) -> dict[str, Any]:
    db = session_factory()
    ids = _mk_products(db, BASE_PARTS + VARIANT_PARTS)
    _mk_set(db, BASE, [ids[c] for c in BASE_PARTS], name="S-trap 250")
    _mk_set(db, VARIANT, [ids[c] for c in VARIANT_PARTS], name="P-trap 250")
    db.commit()
    assert db.query(Product).filter(Product.product_code.in_([BASE, VARIANT])).count() == 0
    return ids


def _message(text: str) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-sc-{uuid.uuid4().hex[:8]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": text},
        },
    }


def _run(session_factory, stub_access, monkeypatch, *, text, raw, canonical, domain, intent, code_lookup):
    verdict = _parser_output(
        intent_hint=intent, domain_hint=domain, user_goal="ask",
        entities=[{"raw": raw, "hint": "product", "canonical_code": canonical, "current_message": True, "confident": True}],
    )

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        extra = {"code_lookup": True} if code_lookup else {}
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, **extra,
        )

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()
    result = engine_mod.run_turn(_envelope(message=_message(text)), session_factory=session_factory)
    return (result.reply or {}).get("text") or "", list(calls)


def _all_ids(calls) -> set[str]:
    return {pid for _n, a in calls for pid in (a.get("product_ids") or [])}


def _no_miss(text: str) -> None:
    assert "Did you mean" not in text and "Which product do you mean" not in text, text


def test_product_info_for_a_set_only_code_fetches_all_members_names_the_set_and_has_no_price(
    session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed(session_factory)

    text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"details of {BASE}", raw=BASE, canonical=BASE,
        domain="master_products", intent="product_info", code_lookup=True,
    )

    _no_miss(text)
    assert _all_ids(calls) == {ids[c] for c in BASE_PARTS}, calls
    assert BASE in text, text
    assert "RM" not in text and "price" not in text.lower(), text


def test_stock_for_a_set_only_code_runs_for_the_member_ids_and_names_the_set(
    session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed(session_factory)

    text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"stock {BASE}", raw=BASE, canonical=BASE,
        domain="inventory", intent="check_stock", code_lookup=True,
    )

    _no_miss(text)
    stock = [a for n, a in calls if n == "crm_inventory_stock_balance_list" and a.get("product_ids")]
    assert stock and sorted(stock[0]["product_ids"]) == sorted(ids[c] for c in BASE_PARTS), calls
    assert BASE in text, text


def test_a_set_code_plus_an_attribute_answers_the_picked_variant_only(
    session_factory, seeded, stub_access, monkeypatch
):
    ids = _seed(session_factory)

    text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"{BASE} P-TRAP", raw=f"{BASE} P-TRAP", canonical=VARIANT,
        domain="inventory", intent="check_stock", code_lookup=True,
    )

    _no_miss(text)
    assert _all_ids(calls) == {ids[c] for c in VARIANT_PARTS}, calls
    assert not _all_ids(calls) & {ids[c] for c in BASE_PARTS}, calls
    assert VARIANT in text, text


def test_gate_off_the_set_only_code_is_todays_forced_miss(session_factory, seeded, stub_access, monkeypatch):
    """Control (green today): closed lists only, the set-only code is not a product."""
    ids = _seed(session_factory)

    text, calls = _run(
        session_factory, stub_access, monkeypatch, text=f"stock {BASE}", raw=BASE, canonical=BASE,
        domain="inventory", intent="check_stock", code_lookup=False,
    )

    assert not (_all_ids(calls) & {ids[c] for c in BASE_PARTS}), calls
    assert "S-trap 250" not in text and "Name of" not in text, text
    assert "search every stock" in text or "Did you mean" in text or "Couldn't find" in text, text
