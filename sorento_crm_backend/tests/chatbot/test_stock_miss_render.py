"""STOCK-MISS-RENDER (owner report, 6 Oct 2026, production console turn #f763 / #17d3).

A dealer typed "Srt 364-hp x 1 stock available?" and was told:

    Couldn't find: "Srt 364-hp" (product).
    No stock and no incoming for SRT364-HP.
    Please refer to your salesman.

One minute later "SRT364-HP X 1" answered "SRT364-HP x 1: Got stock." The trace of the
first turn: tool=null, outcome=not_found, scope gate "no entities and 'inventory' requires
a scoping entity". No stock lookup ran, yet the reply stated there was no stock.

Two defects, one test each (numbered as in the lane brief):

1. The miss arm ran the cross-domain ladder on a fetch that never called its tool, and
   the ladder rendered "No stock and no incoming for SRT364-HP." over a refusal. Only a
   real empty tool result may say so.
2. The resolver DID place "Srt 364-hp" on SRT364-HP (that is where the uppercase code in
   the reply came from), but the uuid never reached the fetch: the availability-mode
   exact-code filter (`engine._exact_codes_only`) and the quantity join
   (`turn_runtime._spec_quantities`) compared the typed word against the resolver's code
   by casefold alone, while the resolver itself matches with separators folded
   (`resolve_gate._PRODUCT_FOLD`, `entity_resolver._norm_token_map`). "srt 364-hp" never
   equalled "srt364-hp", so the placed product was dropped and the fetch was refused for
   having no subject.

Fake fixture ids only: the seeded codes are the owner's, the uuids are minted per test.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business.services import AnswerServices
from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import product, stock
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

TICK = "✅"
FAMILY = ["SRT364-HP", "SRT364-HP-BL"]


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    def make(**stock_facts: Stock) -> AvailConsole:
        c = AvailConsole(session_factory, monkeypatch, stub_access, phone="+60100000902", **stock_facts)
        # The owner's two codes, beside the suite's own catalogue.
        c.codes.update(_seed(session_factory, FAMILY))
        c.uuid_of = {code: pid for pid, code in c.codes.items()}
        return c

    return make


def _typed(raw: str, quantity: int) -> dict[str, Any]:
    """The parser's own verdict for turn #f763: raw as typed, hint product, a quantity,
    and NO canonical_code (the parser did not normalise the code)."""
    return {**product(raw, quantity), "canonical_code": None}


class TestDefect2ResolvedUuidReachesTheFetch:
    def test_spaced_lowercase_code_with_quantity_runs_the_stock_tool(self, console) -> None:
        c = console(SRT364_HP=Stock(on_hand=50), SRT364_HP_BL=Stock(on_hand=50))
        out = c.say("Srt 364-hp x 1 stock available?", stock(_typed("Srt 364-hp", 1)))
        assert out == f"SRT364-HP x 1: {TICK} Got stock.", c.transcript
        assert len(c.stock_calls) == 1, c.tool_calls
        assert c.stock_calls[0].get("product_ids") == [c.uuid_of["SRT364-HP"]], c.stock_calls

    def test_the_exact_code_typed_the_same_turn_still_works(self, console) -> None:
        """Turn #17d3, the control: the code typed as spelled answered on production."""
        c = console(SRT364_HP=Stock(on_hand=50), SRT364_HP_BL=Stock(on_hand=50))
        out = c.say("SRT364-HP X 1", stock(_typed("SRT364-HP", 1)))
        assert out == f"SRT364-HP x 1: {TICK} Got stock.", c.transcript


class TestDefect1NoLadderClaimWithoutAToolCall:
    """`answer_bridge.answer_for`, the miss arm, with the exact state turn #f763 reached it
    in: the resolver placed the product (a uuid-carrying match), the runner refused the
    fetch before any tool ran (`scope_gate`, outcome not_found), and the tenant ladder
    climbs from inventory to incoming."""

    @staticmethod
    def _parser() -> dict[str, Any]:
        return {
            "domain_hint": "inventory",
            "intent_hint": "check_stock",
            "message_type": "business_query",
            "entities": [
                {
                    "raw": "Srt 364-hp",
                    "hint": "product",
                    "canonical_code": None,
                    "quantity": 1,
                    "current_message": True,
                    "confident": True,
                }
            ],
            "routing": {"suggested_team": "warehouse", "suggested_agent": "general_enquiries"},
            "access_levels": [],
        }

    def test_a_refused_fetch_never_says_no_stock(self) -> None:
        from app.services.chatbot import answer_bridge
        from app.services.chatbot import copy as copy_mod
        from app.services.chatbot.lanes.business import fetch as business_fetch
        from app.services.chatbot.lanes.business import gate as gate_mod

        parser = self._parser()
        uuid = "11111111-2222-4333-8444-555555555555"
        match = {
            "entity_type": "product",
            "uuid": uuid,
            "canonical_code": "SRT364-HP",
            "code": "SRT364-HP",
            "match_tier": "exact",
        }
        resolved = {
            "tokens": ["Srt364hp"],
            "resolutions": [{"token": "Srt364hp", "matches": [match]}],
            "unresolved_tokens": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [{**match, "raw": "Srt 364-hp"}],
            "gate_debug": {"domain": "inventory"},
        }
        scope_gate = gate_mod.run_gate(
            {},
            parser={**parser, "entities": []},
            resolver={"resolutions": []},
        )
        assert scope_gate.get("gate_passed") is False, scope_gate
        raw_fragment = {
            "fetch": {"has_result": False, "response": business_fetch.NO_RESULT_INTRO},
            "outcome": "not_found",
            "scope_gate": scope_gate,
        }
        probes: list[tuple[str, dict[str, Any]]] = []

        def mcp_probe(name: str, args: dict[str, Any]) -> dict[str, Any]:
            probes.append((name, args))
            return {"has_result": False, "answers": []}

        answer = answer_bridge.answer_for(
            {"resolved": resolved, "gate": gate, "_exit_kind": "continue"},
            envelope={"raw_fragment": raw_fragment, "unresolved": ["Srt 364-hp"]},
            parser=parser,
            ctx={"parse": {"output": parser}, "contact": {"id": "zzt-smr-contact"}, "session": {}},
            canned=copy_mod.fallback_copy(),
            services=AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []}),
            db=None,
            asked_at_turn=1,
            crossdomain_ladder={"inventory": ["incoming"], "incoming": ["inventory"]},
        )
        assert answer is not None
        assert "No stock" not in answer.text, answer.text
        assert "no incoming" not in answer.text.lower(), answer.text
        assert not probes, f"no stock lookup ran, so no rung may be climbed: {probes}"
