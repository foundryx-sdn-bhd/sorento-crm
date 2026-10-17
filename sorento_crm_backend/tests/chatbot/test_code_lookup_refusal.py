"""CHAT-CODE-LOOKUP R2-4: under the gate a product pick is refused (dropped back to the
typed word, a note recorded) only on a TRUE TIE among the typed word's near codes or when
SEVERAL listed codes hold the word and none equals it. Code never chooses: a pick that beats
the other near codes is kept (even the lower of two), an exact code, a single holder and the
gate-off turn are untouched. Synthetic codes."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import candidates, grounding, parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401


def _seed(session_factory, *codes: str) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in codes:
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(p)
        ids[code] = p.id
    db.commit()
    return ids


def _sim(typed: str, code: str) -> float:
    return candidates.similarity(candidates.trigrams(typed), candidates.trigrams(code))


def _run(session_factory, stub_access, monkeypatch, typed: str, canonical: str | None, *, code_lookup: bool):
    verdict = _parser_output(
        intent_hint="check_stock", domain_hint="inventory", user_goal="ask",
        entities=[{"raw": typed, "hint": "product", "canonical_code": canonical, "current_message": True, "confident": True}],
    )
    notes: list[Any] = []
    real_ground = grounding.ground

    def spy_ground(*a, **kw):
        out = real_ground(*a, **kw)
        notes.extend(out[1])
        return out

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        extra = {"code_lookup": True} if code_lookup else {}
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, **extra,
        )

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(grounding, "ground", spy_ground)
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()
    message = {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-rf-{uuid.uuid4().hex[:8]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": f"Check stock {typed}"},
        },
    }
    result = engine_mod.run_turn(_envelope(message=message), session_factory=session_factory)
    return result, list(calls), notes


def _stock_ids(calls) -> list[list[str]]:
    return [sorted(a.get("product_ids") or []) for n, a in calls if n == "crm_inventory_stock_balance_list" and a.get("product_ids")]


def _text(result) -> str:
    return (result.reply or {}).get("text") or ""


def _refused(notes) -> bool:
    return any("refus" in str(n.get("to", "")).lower() for n in notes)


def _assert_refused(session_factory, stub_access, monkeypatch, typed, pick, ids):
    result, calls, notes = _run(session_factory, stub_access, monkeypatch, typed, pick, code_lookup=True)
    base, base_calls, _n = _run(session_factory, stub_access, monkeypatch, typed, None, code_lookup=False)
    assert [ids[pick]] not in _stock_ids(calls), calls
    assert _stock_ids(calls) == _stock_ids(base_calls)
    assert _text(result) == _text(base)
    assert _refused(notes), notes


def _assert_kept(session_factory, stub_access, monkeypatch, typed, pick, ids):
    result, calls, notes = _run(session_factory, stub_access, monkeypatch, typed, pick, code_lookup=True)
    assert _stock_ids(calls) and ids[pick] in _stock_ids(calls)[0], calls
    assert pick in _text(result), _text(result)
    assert "Did you mean" not in _text(result), _text(result)
    assert not _refused(notes), notes


def test_a_true_tie_between_near_codes_refuses_the_pick(session_factory, seeded, stub_access, monkeypatch):
    typed, a, b = "ZZCM77741", "ZZMC77741", "ZZMD77741"
    assert _sim(typed.lower(), a) == _sim(typed.lower(), b)
    assert 0.30 <= _sim(typed.lower(), a) < candidates.NEAR_CODE
    ids = _seed(session_factory, a, b)

    _assert_refused(session_factory, stub_access, monkeypatch, typed, a, ids)


def test_a_pick_that_beats_the_other_near_code_is_kept(session_factory, seeded, stub_access, monkeypatch):
    typed, hi, lo = "ZZCM88851", "ZZMC88851", "ZZFAL88851"
    assert _sim(typed.lower(), hi) > _sim(typed.lower(), lo) >= 0.30
    ids = _seed(session_factory, hi, lo)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, hi, ids)


def test_the_lower_scoring_pick_is_still_kept(session_factory, seeded, stub_access, monkeypatch):
    typed, hi, lo = "ZZCM88852", "ZZMC88852", "ZZFAL88852"
    assert _sim(typed.lower(), hi) > _sim(typed.lower(), lo) >= 0.30
    ids = _seed(session_factory, hi, lo)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, lo, ids)


def test_several_holders_with_no_exact_code_refuse_a_holder_pick(session_factory, seeded, stub_access, monkeypatch):
    typed, a, b = "ZZH99901", "ZZH99901A", "ZZH99901B"
    ids = _seed(session_factory, a, b)

    _assert_refused(session_factory, stub_access, monkeypatch, typed, a, ids)


def test_a_single_holder_pick_is_kept(session_factory, seeded, stub_access, monkeypatch):
    typed, a = "ZZS99902", "ZZS99902A"
    ids = _seed(session_factory, a)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, a, ids)


def test_an_exact_code_pick_is_kept_even_with_other_holders(session_factory, seeded, stub_access, monkeypatch):
    typed, exact, other = "ZZE99903", "ZZE99903", "ZZE99903A"
    ids = _seed(session_factory, exact, other)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, exact, ids)


def test_gate_off_a_tie_pick_is_untouched(session_factory, seeded, stub_access, monkeypatch):
    """Control (green today): no gate, no refusal note."""
    typed, a, b = "ZZCM77742", "ZZMC77742", "ZZMD77742"
    _seed(session_factory, a, b)

    _r, _c, notes = _run(session_factory, stub_access, monkeypatch, typed, a, code_lookup=False)

    assert not _refused(notes), notes


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def test_shortlist_exposes_per_item_scores_and_holding_flags(db):
    """Contract: each WordCandidates carries `scores` (code -> folded similarity to the
    typed word) and `holds` (the codes that hold the word)."""
    from app.models.product import Product, ProductCategory, UnitOfMeasure

    cat = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="c", company_id=DEFAULT_COMPANY_ID)
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="e", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    for code in ("ZZMC77743", "ZZMD77743", "ZZH77744A", "ZZH77744B"):
        db.add(Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
        ))
    db.flush()

    near = next(e for e in candidates.shortlist(db, "stock zzcm77743", code_lookup=True) if e.gram == "zzcm77743")
    for code in ("ZZMC77743", "ZZMD77743"):
        assert near.scores[code] == _sim("zzcm77743", code.lower())
    assert not set(near.holds)

    held = next(e for e in candidates.shortlist(db, "stock zzh77744", code_lookup=True) if e.gram == "zzh77744")
    assert set(held.holds) == {"ZZH77744A", "ZZH77744B"}


# ---- review round 2: the tie refusal never fires on an exact typed code


def test_an_exact_typed_code_with_a_spec_word_variant_pick_is_kept_despite_a_tied_sibling(
    session_factory, seeded, stub_access, monkeypatch
):
    """grounding.py tie clause has no `typed not in holds` guard: typed ZZRW8608-RL is on
    file, the parser picks the p-trap variant, both variants score alike, and the pick is
    refused (stock then fetched for the wrong product)."""
    typed, exact, p, s = "ZZRW8608-RL", "ZZRW8608-RL", "ZZRW8608-P-RL", "ZZRW8608-S-RL"
    ids = _seed(session_factory, exact, p, s)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, p, ids)


def test_a_colour_suffix_typed_code_that_folds_equal_is_never_refused(session_factory, seeded, stub_access, monkeypatch):
    """Guard (may already pass): typed zzrb1409rg folds equal to ZZRB1409-RG."""
    typed, rg, bk, wh = "zzrb1409rg", "ZZRB1409-RG", "ZZRB1409-BK", "ZZRB1409-WH"
    ids = _seed(session_factory, rg, bk, wh)

    _assert_kept(session_factory, stub_access, monkeypatch, typed, rg, ids)
