"""CHAT-CODE-LOOKUP fix round 2 red tests (G1 set company scope, G2 per-row set answers,
G3 ranking by folded score). Synthetic codes. Every behaviour stays behind the gate."""
from __future__ import annotations

import json
import re

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_company_scope
from app.models.product import Product
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import candidates
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_code_lookup_round1 import _all_ids, _priced_tool  # noqa: F401
from tests.chatbot.test_code_lookup_set_codes import (  # noqa: F401
    BASE,
    BASE_PARTS,
    OTHER_COMPANY,
    VARIANT,
    VARIANT_PARTS,
    _entry_line,
    _fold,
    _lines,
    _message,
    _mk_products,
    _mk_set,
    _scope_listeners,
    db,
)
from tests.chatbot.test_engine import _envelope, _parser_output, seeded, stub_access  # noqa: F401

# ---------------------------------------------------------------- G1: set company scope


def _seed_g1(db):
    """A clean default-company set (BASE), a set in OTHER_COMPANY, and a default-company
    set (VARIANT) with ONE member product of OTHER_COMPANY."""
    base = _mk_products(db, BASE_PARTS)
    _mk_set(db, BASE, list(base.values()), name="Clean 250")
    foreign = _mk_products(db, ["ZZFORE-OMEGA7", "ZZFORE-OMEGA8"], company_id=OTHER_COMPANY)
    _mk_set(db, "ZZOTHSET7701-RL", list(foreign.values()), company_id=OTHER_COMPANY)
    own = _mk_products(db, [VARIANT_PARTS[0]])
    cross = {VARIANT_PARTS[0]: own[VARIANT_PARTS[0]], "ZZFORE-OMEGA9": _mk_products(db, ["ZZFORE-OMEGA9"], company_id=OTHER_COMPANY)["ZZFORE-OMEGA9"]}
    _mk_set(db, VARIANT, list(cross.values()), name="Cross 250")
    # seed shape: the variant's members span two companies
    from sqlalchemy import select

    tbl = Product.__table__
    cos = {c for (c,) in db.execute(select(tbl.c.company_id).where(tbl.c.id.in_(list(cross.values())))).all()}
    assert cos == {DEFAULT_COMPANY_ID, OTHER_COMPANY}, cos
    return cross


def test_g1_a_set_with_a_member_of_another_company_is_not_listed(db):
    _seed_g1(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    shortlist = candidates.shortlist(db, f"stock {VARIANT.lower()}", code_lookup=True)

    line = _entry_line(candidates.candidate_lines(shortlist), VARIANT.lower())
    assert line is None or f"set code {VARIANT}" not in line
    assert _fold(VARIANT) not in candidates.listed_codes(shortlist)


def test_g1_control_a_clean_default_company_set_is_listed(db):
    _seed_g1(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    line = _entry_line(_lines(db, f"stock {BASE.lower()}", code_lookup=True), BASE.lower())

    assert line is not None and f"set code {BASE} (Clean 250)" in line


def test_g1_an_exact_typed_code_of_a_cross_company_set_is_todays_miss(
    session_factory, seeded, stub_access, monkeypatch
):
    from app.models.company import Company
    from tests._pg_fixture import unique_code

    db = session_factory()
    db.add(Company(id=OTHER_COMPANY, name="Other", code=unique_code("OTH")[:20]))
    db.flush()
    cross = _seed_g1(db)
    db.commit()
    verdict = _parser_output(
        intent_hint="check_stock", domain_hint="inventory", user_goal="ask",
        entities=[{"raw": VARIANT, "hint": "product", "canonical_code": VARIANT, "current_message": True, "confident": True}],
    )
    calls: list = []
    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, code_lookup=True,
        ),
    )
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()

    engine_mod.run_turn(_envelope(message=_message(f"stock {VARIANT}")), session_factory=session_factory)

    assert not (_all_ids(calls) & set(cross.values())), calls


# ---------------------------------------------------------------- G2: per-row set answers


def _run_multi(session_factory, stub_access, monkeypatch, *, text, codes, domain, intent):
    verdict = _parser_output(
        intent_hint=intent, domain_hint=domain, user_goal="ask",
        entities=[
            {"raw": c, "hint": "product", "canonical_code": c, "current_message": True, "confident": True} for c in codes
        ],
    )
    calls: list = []
    tool = _priced_tool(session_factory)

    def fake_call(self, name, args):
        calls.append((name, args))
        return tool(name, args)

    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, code_lookup=True,
        ),
    )
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call)
    stub_access()
    result = engine_mod.run_turn(_envelope(message=_message(text)), session_factory=session_factory)
    return (result.reply or {}).get("text") or "", calls


def _name(code: str) -> str:
    return f"Name of {code}"


def _price_on_line(text: str, code: str) -> bool:
    """True when a price token appears on the same line as, or right after, the member's name."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if _name(code) in line:
            window = " ".join(lines[n : n + 3])
            if "List Price" in window or "MYR" in window or re.search(r"\bRM\b", window):
                return True
    return False


_DOMAINS = [("master_products", "product_info"), ("inventory", "check_stock")]


@pytest.mark.parametrize("domain, intent", _DOMAINS)
def test_g2_two_sets_in_one_message_name_both_and_print_no_member_price(
    domain, intent, session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    ids = _mk_products(db, BASE_PARTS + VARIANT_PARTS)
    _mk_set(db, BASE, [ids[c] for c in BASE_PARTS], name="S-trap 250")
    _mk_set(db, VARIANT, [ids[c] for c in VARIANT_PARTS], name="P-trap 250")
    db.commit()

    text, calls = _run_multi(
        session_factory, stub_access, monkeypatch, text=f"details of {BASE} and {VARIANT}", codes=[BASE, VARIANT],
        domain=domain, intent=intent,
    )

    assert _all_ids(calls) == set(ids.values()), calls
    assert f"Set {BASE}" in text and f"Set {VARIANT}" in text, text
    assert "List Price" not in text and "MYR" not in text and not re.search(r"\bRM\b", text), text
    for parts, own, other in ((BASE_PARTS, BASE, VARIANT), (VARIANT_PARTS, VARIANT, BASE)):
        for code in parts:
            assert _name(code) in text, text
    a_set, b_set = text.index(f"Set {BASE}"), text.index(f"Set {VARIANT}")
    first, second = sorted([(a_set, BASE_PARTS), (b_set, VARIANT_PARTS)])
    lo, hi = first[0], second[0]
    for code in first[1]:
        assert lo < text.index(_name(code)) < hi, text
    for code in second[1]:
        assert text.index(_name(code)) > hi, text


def test_g2_a_set_plus_a_plain_product_prices_only_the_plain_product(
    session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    ids = _mk_products(db, BASE_PARTS + ("ZZPLAIN-EPSIL5",))
    _mk_set(db, BASE, [ids[c] for c in BASE_PARTS], name="S-trap 250")
    db.commit()
    plain = "ZZPLAIN-EPSIL5"

    text, calls = _run_multi(
        session_factory, stub_access, monkeypatch, text=f"details of {BASE} and {plain}", codes=[BASE, plain],
        domain="master_products", intent="product_info",
    )

    assert _all_ids(calls) == set(ids.values()), calls
    assert f"Set {BASE}" in text, text
    assert _price_on_line(text, plain), text
    for code in BASE_PARTS:
        assert _name(code) in text and not _price_on_line(text, code), text
    # the plain product is not listed under the set's heading
    set_at = text.index(f"Set {BASE}")
    assert text.index(_name(plain)) < set_at or text.index(_name(plain)) > max(text.index(_name(c)) for c in BASE_PARTS), text


# ---------------------------------------------------------------- G3: rank by folded score, kind breaks a tie


def test_g3_a_near_product_scoring_higher_than_a_near_set_is_listed_ahead_of_it(db):
    typed = "zzqrst7712ab"
    prod, set_code = "ZZQRST7712AC", "ZZQRST7XXXAB"
    pids = _mk_products(db, [prod, "ZZMEMB-ONE11"])
    sids = _mk_products(db, ["ZZMEMB-TWO22"])
    _mk_set(db, set_code, [pids["ZZMEMB-ONE11"], sids["ZZMEMB-TWO22"]], name="Near set")
    sp = candidates.similarity(candidates.trigrams(typed), candidates.trigrams(_fold(prod)))
    ss = candidates.similarity(candidates.trigrams(typed), candidates.trigrams(_fold(set_code)))
    assert ss >= candidates.NEAR_CODE_NO_EXACT and sp > ss, (sp, ss)

    line = _entry_line(_lines(db, f"stock {typed}", code_lookup=True), typed)

    assert line is not None and f"code {prod}" in line and f"set code {set_code}" in line, line
    assert line.index(f"code {prod}") < line.index(f"set code {set_code}"), line


# ---------------------------------------------------------------- round 3 H1: sets that share a member

SHARED = "ZZSHRD-SEAT01"
A_OWN, B_OWN = "ZZSHRD-AOWN01", "ZZSHRD-BOWN01"
SET_A, SET_B = "ZZSHRD3301-RL", "ZZSHRD3301-P-RL"


@pytest.mark.parametrize("domain, intent", _DOMAINS)
def test_h1_two_sets_sharing_a_member_list_it_under_each_set_without_price(
    domain, intent, session_factory, seeded, stub_access, monkeypatch
):
    from sqlalchemy import func, select

    from app.models.product_set import ProductSetMember

    db = session_factory()
    ids = _mk_products(db, [SHARED, A_OWN, B_OWN])
    _mk_set(db, SET_A, [ids[A_OWN], ids[SHARED]], name="Shared A")
    _mk_set(db, SET_B, [ids[B_OWN], ids[SHARED]], name="Shared B")
    db.commit()
    # seed shape: the seat is a member of both sets
    n = db.execute(
        select(func.count()).select_from(ProductSetMember).where(ProductSetMember.product_id == ids[SHARED])
    ).scalar()
    assert n == 2, n

    text, calls = _run_multi(
        session_factory, stub_access, monkeypatch, text=f"details of {SET_A} {SET_B}", codes=[SET_A, SET_B],
        domain=domain, intent=intent,
    )

    assert f"Set {SET_A}" in text and f"Set {SET_B}" in text, text
    assert "List Price" not in text and "MYR" not in text and not re.search(r"\bRM\b", text), text
    assert _all_ids(calls) == set(ids.values()), calls
    ids_flat = [i for _n, a in calls for i in (a.get("product_ids") or [])]
    for pid in ids.values():
        assert ids_flat.count(pid) <= 1, (pid, calls)
    # block boundaries: the set whose heading comes first ends where the other begins
    pos = sorted([(text.index(f"Set {SET_A}"), A_OWN), (text.index(f"Set {SET_B}"), B_OWN)])
    (lo, first_own), (hi, second_own) = pos
    first_block, second_block = text[lo:hi], text[hi:]
    assert _name(first_own) in first_block and _name(SHARED) in first_block, text
    assert _name(second_own) in second_block and _name(SHARED) in second_block, text


# ---------------------------------------------------------------- round 4 I1: one set named twice

TWICE_SET = "ZZTWCE8608-RL"
TWICE_PARTS = ("ZZTWCE-MEMB01", "ZZTWCE-MEMB02")


@pytest.mark.parametrize("domain, intent", _DOMAINS)
def test_i1_one_set_named_twice_prints_its_members_once(
    domain, intent, session_factory, seeded, stub_access, monkeypatch
):
    db = session_factory()
    ids = _mk_products(db, list(TWICE_PARTS))
    _mk_set(db, TWICE_SET, [ids[c] for c in TWICE_PARTS], name="Twice 250")
    db.commit()
    spaced = TWICE_SET.lower().replace("-", " ")

    text, calls = _run_multi(
        session_factory, stub_access, monkeypatch, text=f"details of {TWICE_SET} and {spaced}",
        codes=[TWICE_SET, spaced], domain=domain, intent=intent,
    )

    assert text.count(f"Set {TWICE_SET}") == 1, text
    assert "List Price" not in text and "MYR" not in text and not re.search(r"\bRM\b", text), text
    for code in TWICE_PARTS:
        assert text.count(_name(code)) == 1, text
    assert _all_ids(calls) == set(ids.values()), calls
    ids_flat = [i for _n, a in calls for i in (a.get("product_ids") or [])]
    for pid in ids.values():
        assert ids_flat.count(pid) <= 1, (pid, calls)
