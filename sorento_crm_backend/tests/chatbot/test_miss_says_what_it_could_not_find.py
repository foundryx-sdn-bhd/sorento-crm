"""ROGUE-CONVO-REVIEW PR-2: a miss says what it could not find (classes B, C, G).

Owner, 6 Oct 2026, one dealer conversation (availability-only contact). Each class is a
general rule; the 6 Oct turns are only its red tests. Plan:
`documentation/plans/chatbot/PLAN-rogue-convo-review-06oct.md`, UAC alongside.

* B (owner ruling Q3): a bare number typed as a product ("8066 stock ?") matches its code
  family exactly the way compact mode does, through the resolver's OWN substring probe
  (`entity_resolver._prefix_probe_product`), and offers the same which-one picker. On the
  availability path `engine._exact_codes_only` discarded those rows because the token
  is not `code_like` (letters AND digits), and the dealer read the scope sentence ("That
  would search every stock we have ...") for a product they had named.
* C (owner ruling Q4): "Here's what you want" lists only what the customer named and the
  resolver placed. A miss on the customer's own product token never lists spec_search
  SET MEMBERS under that header ("product: SRTWC200-S-UF-RL (+34 more)" over an unplaced
  description line), and never carries a `set_page`.
* G: an attachment with no `description` hands media intake `caption=None`, never the
  literal string "null" (`jsc.js_string(None)`), so the parser never reads a "null"
  line above the codes.

Fake fixture codes throughout.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.engine import _exact_codes_only
from app.services.chatbot.turn import task as turn_task
from app.services.chatbot.turn.state import token_key

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import answer, numbered, product, reply, stock
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - pytest fixtures
from tests.chatbot.test_media_intake_turn import (  # noqa: F401 - pytest fixture (media_pipeline)
    _fresh_contact_id,
    _image_envelope,
    _seed_media_contact,
    _seed_media_limit,
    _seed_settings,
    media_pipeline,
)

pytestmark = pytest.mark.usefixtures("stub_access")

TICK = "✅"
SCOPE_SENTENCE = "That would search every"

#: A family a bare number sits inside (code order, the resolver's own), and one code a
#: different bare number sits inside alone.
FAMILY_8066 = ["SRTWC8066-RL", "SRTWC8066-SH", "SRTWC8066-SH-150"]
LONE_9031 = "SRTKT9031-GM"


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    def make(**stock_facts: Stock) -> AvailConsole:
        c = AvailConsole(session_factory, monkeypatch, stub_access, phone="+60100000906", **stock_facts)
        extra = _seed(session_factory, [*FAMILY_8066, LONE_9031])
        c.codes.update(extra)
        c.uuid_of = {code: pid for pid, code in c.codes.items()}
        return c

    return make


def _pick(*positions: int):
    return reply(reference_positions=list(positions), open_question_answer=answer("pick", picked=list(positions)))


# =========================================================================== class B
# A bare number typed as a product matches its family (compact parity, owner Q3).


class TestBareNumberMatchesItsFamilyOnAvailability:
    def test_bare_number_without_a_quantity_picks_then_asks_it(self, console) -> None:
        """Turn 3 as typed: the picker, never the scope sentence."""
        c = console()
        out = c.say("8066 stock ?", stock(product("8066")))
        assert SCOPE_SENTENCE not in out, out
        assert out == numbered("8066 matches 3 products. Which one?", FAMILY_8066), out
        assert c.say("2", _pick(2)) == "How many units of SRTWC8066-SH?"

    def test_bare_number_with_a_quantity_picks_then_answers(self, console) -> None:
        c = console(SRTWC8066_SH=Stock(on_hand=50))
        assert c.say("8066 x 5", stock(product("8066", 5))) == numbered("8066 x 5: which one?", FAMILY_8066)
        assert c.say("2", _pick(2)) == f"SRTWC8066-SH x 5: {TICK} Got stock."

    def test_bare_number_inside_exactly_one_code_is_that_code(self, console) -> None:
        """S55c's shape for a number: nothing to pick, so it is that code."""
        c = console()
        assert c.say("9031 got stock?", stock(product("9031"))) == f"How many units of {LONE_9031}?"

    def test_the_stock_tool_is_never_called_unfiltered(self, console) -> None:
        c = console()
        c.say("8066 stock ?", stock(product("8066")))
        for name, args in c.tool_calls:
            if name == "crm_inventory_stock_balance_list":
                assert args.get("product_ids"), (name, args)


class TestExactCodesOnlyReadsANumberLikeACode:
    """The filter's three-step ladder (exact, prefix family, substring family, else miss)
    for a bare number, over candidates the resolver itself placed."""

    @staticmethod
    def _candidate(code: str) -> dict:
        return {"uuid": f"uuid-{code}", "raw": code, "canonical_code": code, "code": code, "entity_type": "product"}

    def _run(self, tokens: tuple[str, ...], codes: list[str]):
        candidates = [self._candidate(c) for c in codes]
        kinds = {t.casefold(): {"product": len(codes)} for t in tokens}
        out = _exact_codes_only(
            {"entities": [{"raw": t, "hint": "product", "canonical_code": t} for t in tokens]},
            kinds, {"product": candidates}, list(candidates), {},
        )
        return out if out is not None else (kinds, {"product": candidates}, list(candidates), {})

    def test_a_bare_number_keeps_the_substring_family_the_resolver_placed(self) -> None:
        _kinds, candidates, compatible, unplaced = self._run(("8066",), FAMILY_8066)
        assert [c["code"] for c in candidates["product"]] == FAMILY_8066
        assert [c["code"] for c in compatible] == FAMILY_8066
        assert unplaced == {}

    def test_a_bare_number_inside_no_code_is_a_miss_not_an_ignored_token(self) -> None:
        """Nothing placed: the token stays unplaced so the reply names it (a did-you-mean
        or "Couldn't find"), never the scope sentence."""
        _kinds, candidates, compatible, unplaced = self._run(("7777",), FAMILY_8066)
        assert candidates["product"] == []
        assert compatible == []
        assert unplaced == {"7777": "7777"}

    def test_a_word_is_still_never_a_product(self) -> None:
        """Owner, 3 Oct 2026 (S47): a WORD the parser tagged as a product is not a code;
        the resolver's product resolution is code-only by design."""
        _kinds, candidates, compatible, unplaced = self._run(("new",), ["SRTWC286-SH-NEW", "SRTWC286-SH"])
        assert candidates["product"] == []
        assert compatible == []
        assert unplaced == {}

    def test_group_reads_a_bare_number_as_its_substring_family(self) -> None:
        rows = [{"product_id": f"p-{c}", "product_code": c} for c in [*FAMILY_8066, "SRT5674"]]
        assert [r["product_code"] for r in turn_task._group(rows, "8066")] == FAMILY_8066
        assert turn_task._group(rows, "new") == []


# =========================================================================== class C
# "Here's what you want" lists only what the customer named and the resolver placed.


DESCRIPTION_LINE = "SORENTO ONE PIECE WASHDOWN TOILET (S-TRAP250MM)-SRTWC8354-SH"


def _match(code: str, uuid: str, tier: str) -> dict:
    return {
        "entity_type": "product",
        "uuid": uuid,
        "canonical_code": code,
        "match_tier": tier,
        "display": {"product_name": f"ZZT {code}", "is_active": True},
    }


def _compose(*, parser_entities: list[dict], resolutions: list[dict], unresolved: list[str], compat: list[dict]) -> dict:
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    matches = [m for res in resolutions for m in res["matches"]]
    resolved = {
        "tokens": [res["token"] for res in resolutions],
        "unresolved_tokens": unresolved,
        "resolutions": resolutions,
        "intersection": matches,
        "by_entity_type": {"product": matches},
    }
    parser = {
        "domain_hint": "inventory",
        "entities": parser_entities,
        "routing": {"suggested_team": "warehouse"},
        "access_levels": [],
    }
    gate = {"gate_passed": True, "compatible_entities": compat}
    return not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)


def _rendered(out: dict) -> str:
    return (out.get("found_summary") or "") + "\n" + (out.get("escalate_message") or "")


class TestAMissNeverListsSetMembersAsWhatYouWant:
    def test_spec_search_members_under_an_unplaced_token_print_no_header(self) -> None:
        """Turn 4: the whole description line was one product token, unplaced; 35 water
        closets the spec tier read off its words sat in `compatible_entities`."""
        members = [_match(f"SRTWC20{i:02d}-X", f"uuid-{i}", "spec_search") for i in range(35)]
        out = _compose(
            parser_entities=[{"hint": "product", "raw": DESCRIPTION_LINE}],
            resolutions=[{"token": DESCRIPTION_LINE, "matches": members}],
            unresolved=[DESCRIPTION_LINE],
            compat=[{"uuid": m["uuid"], "entity_type": "product", "code": m["canonical_code"]} for m in members],
        )
        rendered = _rendered(out)
        assert "Here's what you want" not in rendered, rendered
        assert "more)" not in rendered, rendered
        assert "SRTWC2000-X" not in rendered, rendered
        assert DESCRIPTION_LINE in rendered, rendered

    def test_a_resolved_code_that_missed_stock_keeps_the_header(self) -> None:
        """Control (`test_crossdomain_ladder.py:169`'s shape): the customer's own code
        resolved, the domain had nothing - the header names it."""
        out = _compose(
            parser_entities=[{"hint": "product", "raw": "SRTWC8517"}],
            resolutions=[{"token": "SRTWC8517", "matches": [_match("SRTWC8517", "uuid-a", "exact")]}],
            unresolved=[],
            compat=[{"uuid": "uuid-a", "entity_type": "product", "code": "SRTWC8517"}],
        )
        assert "• product: SRTWC8517" in (out.get("found_summary") or ""), out

    def test_a_code_the_customer_named_stays_beside_an_unplaced_token(self) -> None:
        """Control: a code-tier row the customer named is "what you want" even when a
        second token missed; only spec-tier set members are dropped."""
        out = _compose(
            parser_entities=[{"hint": "product", "raw": "SRTWC8517"}, {"hint": "product", "raw": "xyz999"}],
            resolutions=[
                {"token": "SRTWC8517", "matches": [_match("SRTWC8517", "uuid-a", "exact")]},
                {"token": "xyz999", "matches": []},
            ],
            unresolved=["xyz999"],
            compat=[{"uuid": "uuid-a", "entity_type": "product", "code": "SRTWC8517"}],
        )
        rendered = _rendered(out)
        assert "• product: SRTWC8517" in rendered, rendered
        assert "xyz999" in rendered, rendered

    def test_a_described_set_ask_with_no_product_token_keeps_its_members(self) -> None:
        """Control: "which gunmetal basins have stock" names no product token, so the
        spec-tier members ARE what the customer asked about."""
        members = [_match(f"SRTB10{i}-GM", f"uuid-b{i}", "spec_search") for i in range(3)]
        out = _compose(
            parser_entities=[{"hint": "category", "raw": "basin"}, {"hint": "specification", "raw": "gunmetal"}],
            resolutions=[{"token": "basin", "matches": members}],
            unresolved=[],
            compat=[{"uuid": m["uuid"], "entity_type": "product", "code": m["canonical_code"]} for m in members],
        )
        assert "• product: SRTB100-GM (+2 more)" in (out.get("found_summary") or ""), out


class TestAMissOnTheTypedProductCarriesNoSetPage:
    def test_the_message_product_token_is_unplaced(self) -> None:
        verdict = {"entities": [{"hint": "product", "raw": DESCRIPTION_LINE}, {"hint": "brand", "raw": "SORENTO"}]}
        assert turn_runtime.typed_product_unplaced(verdict, {token_key(DESCRIPTION_LINE): DESCRIPTION_LINE}) is True

    def test_a_placed_product_token_is_not(self) -> None:
        verdict = {"entities": [{"hint": "product", "raw": "SRTWC8517"}]}
        assert turn_runtime.typed_product_unplaced(verdict, {}) is False

    def test_an_unplaced_non_product_token_does_not_count(self) -> None:
        """A brand word nobody could place is not a miss on the product."""
        verdict = {"entities": [{"hint": "category", "raw": "basin"}, {"hint": "brand", "raw": "ZZBRAND"}]}
        assert turn_runtime.typed_product_unplaced(verdict, {token_key("ZZBRAND"): "ZZBRAND"}) is False


# =========================================================================== class G
# A missing media caption is None, never the string "null".


def _run_image_turn(session_factory, monkeypatch, stub_parser, stub_access, media_pipeline, caption: str | None) -> Any:
    seen: list[Any] = []
    real_run = engine_mod.media_intake.run

    def spy(**kwargs: Any):
        seen.append(kwargs.get("caption"))
        return real_run(**kwargs)

    monkeypatch.setattr(engine_mod.media_intake, "run", spy)
    contact_id = _fresh_contact_id()
    _seed_media_contact(session_factory, contact_id)
    _seed_media_limit(session_factory, contact_id=contact_id, modality="image")
    _seed_settings(session_factory, media_sync_wait_seconds=5)
    media_pipeline.set_result(
        {
            "rendered_text": "PSJLSRTSC07 x50",
            "entities": [{"raw": "PSJLSRTSC07"}],
            "needs_clarification": True,
            "confirmation_message": "I read PSJLSRTSC07. What would you like me to do with it?",
        }
    )
    stub_parser()
    stub_access()
    engine_mod.run_turn(_image_envelope(caption=caption, contact_id=contact_id), session_factory=session_factory)
    assert seen, "media intake never ran"
    return seen[0]


class TestAMissingCaptionIsNone:
    def test_no_description_hands_intake_caption_none(
        self, session_factory, monkeypatch, stub_parser, stub_access, media_pipeline
    ) -> None:
        """Turns 8 and 10: the photo had no caption and the parser read a "null" line."""
        caption = _run_image_turn(session_factory, monkeypatch, stub_parser, stub_access, media_pipeline, None)
        assert caption is None, f"intake received {caption!r}; a missing caption must be None"

    def test_a_real_caption_still_reaches_intake(
        self, session_factory, monkeypatch, stub_parser, stub_access, media_pipeline
    ) -> None:
        caption = _run_image_turn(session_factory, monkeypatch, stub_parser, stub_access, media_pipeline, "X5")
        assert caption == "X5"
