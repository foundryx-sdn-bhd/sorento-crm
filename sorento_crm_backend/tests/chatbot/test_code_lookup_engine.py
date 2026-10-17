"""CHAT-CODE-LOOKUP slice A engine path (U1.2, U1.3, U1.6, gate-off control).

Under the gate the parser's user turn carries the near-code line for a typo word, a pick on
that block is answered with no did-you-mean, and a pick not on it behaves as without a pick.
Gate-off (closed lists only) keeps today's user turn. Synthetic codes."""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded, stub_access  # noqa: F401

MEANT = "ZZMC66631"
DECOY = "ZZFAL66631"
TYPED = "ZZCM66631"


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    ids = {}
    for code in (MEANT, DECOY):
        p = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(p)
        ids[code] = p.id
    db.commit()
    return ids


def _message(text: str) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-cl-{uuid.uuid4().hex[:8]}", "contactId": CONTACT_ID, "channelId": "whatsapp",
            "traffic": "incoming", "message": {"type": "text", "text": text},
        },
    }


def _verdict(canonical: str | None) -> dict:
    return _parser_output(
        intent_hint="check_stock", domain_hint="inventory", user_goal="ask",
        entities=[{"raw": TYPED, "hint": "product", "canonical_code": canonical, "current_message": True, "confident": True}],
    )


def _run(session_factory, stub_access, monkeypatch, verdict: dict, *, code_lookup: bool):
    seen: list[str] = []

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        extra = {"code_lookup": True} if code_lookup else {}
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="m", api_key="k", closed_lists=True, **extra,
        )

    def fake_parse(config, user_block):
        seen.append(user_block)
        return verdict

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", fake_parse)
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda f, c: frozenset({DEFAULT_COMPANY_ID}))
    monkeypatch.setattr(
        MCPRuntimeClient, "call_tool", lambda self, name, args: calls.append((name, args)) or json.dumps({"ok": True, "items": []})
    )
    stub_access()
    result = engine_mod.run_turn(_envelope(message=_message(f"Check stock {TYPED}")), session_factory=session_factory)
    return result, seen[0], list(calls)


def _stock_ids(calls) -> list[list[str]]:
    return [sorted(a.get("product_ids") or []) for n, a in calls if n == "crm_inventory_stock_balance_list" and a.get("product_ids")]


def test_gate_on_the_user_turn_lists_the_typo_words_near_codes(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)

    _r, block, _c = _run(session_factory, stub_access, monkeypatch, _verdict(None), code_lookup=True)

    assert f'- "{TYPED.lower()}": near code {MEANT} (Name {MEANT})' in block or f"near code {MEANT} (Name {MEANT})" in block
    assert f"near code {DECOY} (Name {DECOY})" in block


def test_closed_lists_only_the_user_turn_has_no_near_line_below_the_floor(session_factory, seeded, stub_access, monkeypatch):
    """Control (green today): code_lookup off keeps the user turn as today."""
    _seed(session_factory)

    _r, block, _c = _run(session_factory, stub_access, monkeypatch, _verdict(None), code_lookup=False)

    assert "near code" not in block


def test_a_pick_on_the_block_is_answered_with_no_did_you_mean(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    result, _block, calls = _run(session_factory, stub_access, monkeypatch, _verdict(MEANT), code_lookup=True)

    text = (result.reply or {}).get("text") or ""
    assert _stock_ids(calls) and _stock_ids(calls)[0] == [ids[MEANT]], calls
    assert MEANT in text, text
    assert "Did you mean" not in text and "Which product do you mean" not in text, text


def test_a_pick_not_on_the_block_behaves_as_without_a_pick(session_factory, seeded, stub_access, monkeypatch):
    ids = _seed(session_factory)

    hallucinated, _b, calls_h = _run(session_factory, stub_access, monkeypatch, _verdict("ZZQQ99999"), code_lookup=True)
    baseline, _b2, calls_b = _run(session_factory, stub_access, monkeypatch, _verdict(None), code_lookup=False)

    assert (hallucinated.reply or {}).get("text") == (baseline.reply or {}).get("text")
    assert _stock_ids(calls_h) == _stock_ids(calls_b)
    assert "Did you mean" in (hallucinated.reply or {}).get("text", "")
