"""SCRATCH (not committed): live parser on a prompt LINE (dev v52 / v86 text from the crew,
scratchpad only), shaped like prod (a static policy block with Specification lines and no size
line), then put through `publish_specs_block`'s own transform, rendered over the seeded registry.

LIVE_PROBE=1 CHATBOT_LIVE_LLM=1 PROBE_LINE=v52 PROBE_CACHE=... pytest -s tests/chatbot/zz_live_line_probe.py
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

import pytest

from app.services.chatbot.head import parser as parser_mod
from tests.chatbot import test_outstanding_lane as harness
from tests.chatbot.test_outstanding_lane import _run_turn
from tests.chatbot.test_spec_on_category_entity import _clear_overrides, _route_backed_stock, _seed_basins  # noqa: F401

pytestmark = [pytest.mark.skipif(not os.environ.get("LIVE_PROBE"), reason="live probe only"), pytest.mark.live_external]
SP = pathlib.Path("/tmp/claude-0/-home-user-sorento-crm/21474ded-46a7-5a6f-845f-9d2ff7548835/scratchpad")
LINE = os.environ.get("PROBE_LINE", "v52")
MODEL = os.environ.get("PROBE_MODEL", "gpt-5.4-mini")
CACHE = pathlib.Path(os.environ.get("PROBE_CACHE", str(SP / "probe")))
SEVEN = tuple((f"L{i}", {"dim_length": 600}) for i in range(5)) + (("W0", {"dim_width": 600}), ("W1", {"dim_width": 600}), ("N0", {}))
FOLLOW_UPS = ["more"]
REAL_PARSE = parser_mod.parse


class _MP:
    def __init__(self, mp):
        self.mp = mp

    def setattr(self, target, name, value=None, *a, **k):
        if target is parser_mod and name in ("parse", "resolve_config"):
            return None
        return self.mp.setattr(target, name, value, *a, **k)

    def __getattr__(self, item):
        return getattr(self.mp, item)


def _line_text() -> str:
    parts = sorted((SP / LINE).glob("*.txt"))
    return "".join(p.read_text() for p in parts)


def _prompt(db) -> str:
    """The line's text shaped like prod (static Specification lines inside the markers, no size
    line), then `publish_specs_block`'s transform, then rendered as `ai_prompt_registry.render`
    renders a version."""
    from app.services.ai_prompt_registry import _substitute
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, specification_lines
    from app.services.ai_prompt_registry import extract_tokens
    from app.services.chatbot_prompt_vars import _with_continuation_rule, _with_specs_block, render_values_safe

    static = [ln for ln in specification_lines(db) if not ln.startswith("Specification size")]
    text = _line_text()
    if BLOCKS_BEGIN not in text:
        text = f"{text.rstrip()}\n\n{BLOCKS_BEGIN}\n" + "\n".join(static) + f"\n{BLOCKS_END}\n"
    published = _with_continuation_rule(_with_specs_block(text))
    values = {"current_date": "2026-10-12", **render_values_safe(db, extract_tokens(published) - {"current_date"})}
    out = _substitute(published, values)
    (SP / f"rendered_{LINE}.txt").write_text(out)
    return out


def _live(monkeypatch, tag):
    real_parse = REAL_PARSE

    def cfg(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(system_prompt=_prompt(db), prompt_version=0, provider="openai", model=MODEL, api_key="sk-proxy")

    def parse(config, user_block):
        path = CACHE / f"{LINE}_{tag}.json"
        if path.exists():
            out = parser_mod.ParsedOutput(json.loads(path.read_text()), {})
        else:
            out = real_parse(config, user_block)
            path.write_text(json.dumps(dict(out)))
        keep = {k: out.get(k) for k in ("domain_hint", "intent_hint", "continuation", "entity_op")}
        keep["entities"] = [{k: e.get(k) for k in ("raw", "hint", "canonical_code", "spec_key", "spec_value", "current_message")} for e in out.get("entities") or []]; keep["demand_qty"] = out.get("demand_qty"); keep["message_type"] = out.get("message_type")
        print(f"PARSER[{tag}]", json.dumps(keep, ensure_ascii=False, default=str))
        return out

    monkeypatch.setattr(parser_mod, "resolve_config", cfg)
    monkeypatch.setattr(parser_mod, "parse", parse)


def _turn(session_factory, monkeypatch, text, tag):
    _live(monkeypatch, tag)
    result, _ = _run_turn(session_factory, _MP(monkeypatch), qf={}, text_body=text, msg_id=f"zzt-line-{uuid.uuid4().hex[:6]}", real_resolver=True)
    return (result.reply or {}).get("text") or ""



from tests.chatbot.test_spec_conversation_goldens import _seed, _make_dealer, _listed
from tests.chatbot.test_spec_size_on_a_code import _seed_wcs


def _chat(session_factory, monkeypatch, tag, msgs):
    call, captured = _route_backed_stock(monkeypatch, session_factory)
    monkeypatch.setattr(harness, "_capturing_mcp", lambda response=None: (call, captured))
    for i, m in enumerate(msgs):
        captured.clear()
        reply = _turn(session_factory, monkeypatch, m, f"{tag}_{i}")
        print("CALLS", json.dumps([(n, a.get("spec_constraints"), len(a.get("product_ids") or [])) for n, a in captured], default=str))
        print("REPLY", json.dumps(reply[:220], ensure_ascii=False))


def test_basin(session_factory, monkeypatch, _clear_overrides):
    w = _seed(session_factory)
    _chat(session_factory, monkeypatch, "b", ["600mm basin got stock?", "yes", f"only {w['brand_a'].lower()}", "length only"])


def test_dealer(session_factory, monkeypatch, _clear_overrides):
    _seed(session_factory)
    _make_dealer(session_factory)
    _chat(session_factory, monkeypatch, "d", ["600mm basin got stock?", "5"])


def test_family(session_factory, monkeypatch, _clear_overrides):
    w = _seed_wcs(session_factory)
    f = w["family"].lower()
    _chat(session_factory, monkeypatch, "f", [f"{f} got stock?", "200mm only"])


def test_trap(session_factory, monkeypatch, _clear_overrides):
    w = _seed_wcs(session_factory)
    f = w["family"].lower()
    _chat(session_factory, monkeypatch, "t", [f"p trap 250mm {f} got stock?", "180 then"])
