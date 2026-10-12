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
                "requested_attributes": out.get("requested_attributes"),
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


#: SEAT-AS-PRODUCT golden set (owner rule 13 Oct): the relation picks WHICH products, the
#: domain is the ask's own. Each message is parsed beside its control, the same ask with the
#: linked code(s) typed directly (links from the flyer rows, `link_seat_cover_flyer.py`), and
#: the two must agree on domain_hint; only the asked one carries `fits`.
GOLDEN = [
    # (message, the code it names, control message)
    ("seat cover for SRTWC8383-RL-MBL", "SRTWC8383-RL-MBL", "SRTSCBD823-MBL"),
    ("check stock seat cover for SRTWC8122", "SRTWC8122", "check stock SRTSCBD333-UF SRTSCBD333A-UF"),
    ("eta seat cover for SRTWC8383-RL-MBL", "SRTWC8383-RL-MBL", "eta SRTSCBD823-MBL"),
    ("SRTWC8122 马桶盖 有货吗", "SRTWC8122", "SRTSCBD333-UF SRTSCBD333A-UF 有货吗"),
    ("SRTWC8088 seat cover photo", "SRTWC8088", "SRTSCBD330-UF photo"),
    ("seat cover SRTWC8152-BL bila sampai?", "SRTWC8152-BL", "SRTSCBD824-BL bila sampai?"),
    ("SRTWC6015 的马桶盖什么时候到货", "SRTWC6015", "SRTSCBD335-UF 什么时候到货"),
    ("boss, seat cover for SRTWC8357 got stock?", "SRTWC8357", "boss, SRTSCBD332-UF got stock?"),
    ("SRTWC8122 seat cover price", "SRTWC8122", "SRTSCBD333-UF SRTSCBD333A-UF price"),
    ("WC for SRTSCBD330-UF stock", "SRTSCBD330-UF", "SRTWC8088 stock"),
    # Nothing asked: a bare code (BARE ENTITY CONTINUATION; no previous turn here, so null).
    ("seat cover SRTWC8122", "SRTWC8122", "SRTSCBD333-UF"),
    ("SRTWC8383-RL-MBL 马桶盖", "SRTWC8383-RL-MBL", "SRTSCBD823-MBL"),
    ("which seat cover fits SRTWC8383-RL-MBL", "SRTWC8383-RL-MBL", "what is SRTSCBD823-MBL"),
]


NOTHING_ASKED = {"seat cover for SRTWC8383-RL-MBL", "seat cover SRTWC8122", "SRTWC8383-RL-MBL 马桶盖"}


@pytest.mark.parametrize("message,code,control", GOLDEN)
def test_a_fit_ask_keeps_the_domain_its_cover_codes_would_get(db, message: str, code: str, control: str) -> None:
    system = _system(db)
    out = _parse(system, message)
    typed = _parse(system, control)
    assert _keys(out) == ["fits"], out
    assert code in _product_codes(out), out
    assert _keys(typed) == [], typed
    # intent_hint is a free snake_case phrase, so only the domain is held equal; both print.
    # With nothing asked the parser may also read the message as "which product is it"
    # (master_products, the existing product reply), never as a file or spec ask.
    allowed = {typed.get("domain_hint")} | ({"master_products"} if message in NOTHING_ASKED else set())
    assert out.get("domain_hint") in allowed, (out, typed)


def test_a_fit_question_with_no_code_emits_fits_and_invents_none(db) -> None:
    out = _parse(_system(db), "which seat cover fits my WC?")
    assert _keys(out) == ["fits"], out
    assert not _product_codes(out), out


@pytest.mark.parametrize(
    "message",
    [
        "SRTWC8383-RL-MBL stock?",
        "SRTSCBD823-MBL price",
        "seat cover got promotion?",
        # A FILE ask about the named code itself stays a plain file ask.
        "send me SRTWC8383-RL-MBL drawing",
    ],
)
def test_a_fact_question_emits_no_find(db, message: str) -> None:
    out = _parse(_system(db), message)
    assert _keys(out) == [], out
