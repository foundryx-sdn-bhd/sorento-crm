"""CHAT-FIND: the LIVE parser emits `find.filters` for a fit question, and only for one.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`CHAT_FIND_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it).

The prompt is the production line with the find block published the way the release
post-step publishes it (`chatbot_find_prompt.with_find_block`), and the `{{find_filters}}`
variable rendered from the registry (`product_find.parser_vocabulary`), as `ai_prompt_registry
.render` does at run time. The source text is `CHAT_FIND_PROMPT_FILE` when set (crew points it
at the real production version, v52, exported from the DB), else the committed prod-shaped
snapshot `alembic/data/chatbot_semantic_parser.prod-20261001.txt` (policy markers, static
blocks: the v52 shape). The policy blocks are re-rendered from the registry seed, as
`test_trap_specs_live_parser.py` does. Plan section 4, card examples.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.live_external

TODAY = "Tuesday, 13 October 2026"
KNOWN_BRANDS = [
    {"brand_name": "Sorento", "brand_code": "SRT"},
    {"brand_name": "Mocha", "brand_code": "MCH"},
    {"brand_name": "Cabana", "brand_code": "CBN"},
]
_DATA = Path(__file__).resolve().parents[2] / "alembic" / "data"


def _source_text() -> str:
    path = os.environ.get("CHAT_FIND_PROMPT_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return (_DATA / "chatbot_semantic_parser.prod-20261001.txt").read_text(encoding="utf-8")


def _system(db) -> str:
    from app.services import product_find
    from app.services.chatbot_find_prompt import TOKEN, with_find_block
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, render_prompt_blocks

    text = _source_text()
    if BLOCKS_BEGIN in text and BLOCKS_END in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        _old, tail = rest.split(BLOCKS_END, 1)
        text = f"{head}{BLOCKS_BEGIN}\n{render_prompt_blocks(db)}{BLOCKS_END}{tail}"
    text = with_find_block(text)
    return text.replace(TOKEN, product_find.parser_vocabulary(db)).replace("{{current_date}}", TODAY)


def _parse(system_text: str, message: str) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    config = parser.ParserConfig(
        system_prompt=system_text,
        prompt_version=0,
        provider="openai",
        model=os.environ.get("CHAT_FIND_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )
    block = parser.build_user_block(
        previous_response="(none)",
        latest_user_message=message,
        pending_kind=None,
        focus=None,
        recent_exchanges=None,
        brands=KNOWN_BRANDS,
    )
    out = dict(parser.parse(config, block))
    print(
        json.dumps(
            {
                "message": message,
                "find": out.get("find"),
                "domain_hint": out.get("domain_hint"),
                "intent_hint": out.get("intent_hint"),
                "entities": [
                    {k: e.get(k) for k in ("raw", "hint", "canonical_code", "current_message")}
                    for e in (out.get("entities") or [])
                    if isinstance(e, dict)
                ],
            },
            ensure_ascii=False,
        )
    )
    return out


@pytest.fixture()
def db(session_factory):
    from tests.chatbot.test_lane_require import _seed_registry

    s = session_factory()
    _seed_registry(s)
    s.commit()
    yield s
    s.close()


def _keys(out: dict[str, Any]) -> list[str]:
    find = out.get("find") if isinstance(out.get("find"), dict) else {}
    return [f.get("key") for f in find.get("filters") or [] if isinstance(f, dict)]


def _product_codes(out: dict[str, Any]) -> set[str]:
    return {
        str(e.get("canonical_code") or e.get("raw") or "").upper().replace(" ", "")
        for e in out.get("entities") or []
        if isinstance(e, dict) and e.get("hint") == "product"
    }


@pytest.mark.parametrize(
    "message,code",
    [
        ("what seat cover fits SRTWC8383-RL-MBL", "SRTWC8383-RL-MBL"),
        ("which WC fits SRTSCBD823-MBL", "SRTSCBD823-MBL"),
        ("SRTWC8122 pakai seat cover apa?", "SRTWC8122"),
        ("SRTWC191-G2 配什么马桶盖?", "SRTWC191-G2"),
        ("SRTSCBD824-BL boleh pakai WC mana?", "SRTSCBD824-BL"),
    ],
)
def test_a_fit_question_emits_fits_and_keeps_the_code_a_product(db, message: str, code: str) -> None:
    out = _parse(_system(db), message)
    assert _keys(out) == ["fits"], out
    assert code in _product_codes(out), out


def test_a_fit_question_with_no_code_emits_fits_and_invents_none(db) -> None:
    out = _parse(_system(db), "which seat cover fits my WC?")
    assert _keys(out) == ["fits"], out
    assert not _product_codes(out), out


@pytest.mark.parametrize(
    "message", ["SRTWC8383-RL-MBL stock?", "SRTSCBD823-MBL price", "seat cover got promotion?"]
)
def test_a_fact_question_emits_no_find(db, message: str) -> None:
    out = _parse(_system(db), message)
    assert _keys(out) == [], out
