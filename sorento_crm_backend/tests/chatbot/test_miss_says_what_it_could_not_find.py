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
from tests.chatbot.test_counted_set_no_paging import seven_taps  # noqa: F401 - pytest fixture
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
REFER = "Please refer to your salesman."


def _sent(token: str) -> str:
    """The token the way the resolver was SENT it and lists it back in
    `unresolved_tokens` / `resolutions[].token`: separators folded
    (`resolve_gate._token_of`, `_PRODUCT_FOLD`), case untouched."""
    import re

    return re.sub(r"[-\s]+", "", token)


def _match(code: str, uuid: str, tier: str) -> dict:
    return {
        "entity_type": "product",
        "uuid": uuid,
        "canonical_code": code,
        "match_tier": tier,
        "display": {"product_name": f"ZZT {code}", "is_active": True},
    }


def _compose(
    *,
    parser_entities: list[dict],
    resolutions: list[dict],
    unresolved: list[str],
    compat: list[dict],
    predicate: dict | None = None,
) -> dict:
    """`not_found_error_message` over the shapes the engine hands it. Every token in
    `resolutions` and `unresolved` is written the way the resolver lists it (`_sent`)."""
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    resolutions = [{**res, "token": _sent(res["token"])} for res in resolutions]
    matches = [m for res in resolutions for m in res["matches"]]
    resolved = {
        "tokens": [res["token"] for res in resolutions],
        "unresolved_tokens": [_sent(t) for t in unresolved],
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
    if predicate is not None:
        gate["predicate"] = predicate
    return not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)


def _rendered(out: dict) -> str:
    return (out.get("found_summary") or "") + "\n" + (out.get("escalate_message") or "")


class TestAMissNeverListsSetMembersAsWhatYouWant:
    def test_spec_search_members_under_an_unplaced_token_print_no_header(self) -> None:
        """Turn 4: the whole description line was one product token, unplaced; 35 water
        closets the spec tier read off its words (its own whole-query resolution,
        `references._emit_spec_matches`) sat in `compatible_entities`."""
        members = [_match(f"SRTWC20{i:02d}-X", f"uuid-{i}", "spec_search") for i in range(35)]
        out = _compose(
            parser_entities=[{"hint": "product", "raw": DESCRIPTION_LINE}],
            resolutions=[
                {"token": DESCRIPTION_LINE, "matches": []},
                {"token": "sorento one piece washdown toilet", "matches": members},
            ],
            unresolved=[DESCRIPTION_LINE],
            compat=[{"uuid": m["uuid"], "entity_type": "product", "code": m["canonical_code"]} for m in members],
        )
        rendered = _rendered(out)
        assert "Here's what you want" not in rendered, rendered
        assert "more)" not in rendered, rendered
        assert "SRTWC2000-X" not in rendered, rendered
        assert f'Couldn\'t find: "{DESCRIPTION_LINE}" (product).' in rendered, rendered

    def test_a_miss_quotes_the_token_as_typed_never_the_folded_or_derived_word(self) -> None:
        """Turn 11: "Srt-hp" has no digit, so the set reader took it as a description and
        reported its first word; the reply read "Here's what you want: srt / Couldn't
        find: "srt"". The miss names what the dealer typed, spelling and hyphen kept."""
        out = _compose(
            parser_entities=[{"hint": "product", "raw": "Srt-hp"}],
            resolutions=[{"token": "Srt-hp", "matches": []}],
            unresolved=["Srt-hp"],
            compat=[],
            predicate={"qualifying_total": 0, "unrecognized_terms": ["srt"], "require": {"stock": True}},
        )
        rendered = _rendered(out)
        # The #833 structure stays (a typed phrase that binds nothing is its own term,
        # `test_lane_require.test_unknown_term_clarifies_with_nearest_names`); the words
        # are the dealer's, not the reader's fragments.
        assert "Here's what you want: Srt-hp\n\nCouldn't find: \"Srt-hp\"." in rendered, rendered
        assert '"srt"' not in rendered, rendered
        assert "srthp" not in rendered.lower(), rendered

    def test_set_members_read_off_the_token_itself_never_count_as_placing_it(self) -> None:
        """A one-word token the parser tagged `product` that the spec tier read as a class
        word: the members are emitted under the whole query as `via_token`
        (`references._emit_spec_matches`), which is the typed token itself here. Those
        rows must not count as having placed the token (`resolved_toks`), or the miss
        falls to the generic "Could not find inventory for ..." instead of naming it."""
        members = [_match(f"SRTB20{i}-GM", f"uuid-c{i}", "spec_search") for i in range(4)]
        for m in members:
            m["display"]["via_token"] = "basin"
        out = _compose(
            parser_entities=[{"hint": "product", "raw": "basin"}],
            resolutions=[{"token": "basin", "matches": []}, {"token": "basin", "matches": members}],
            unresolved=["basin"],
            compat=[{"uuid": m["uuid"], "entity_type": "product", "code": m["canonical_code"]} for m in members],
        )
        rendered = _rendered(out)
        assert "Here's what you want" not in rendered, rendered
        assert 'Couldn\'t find: "basin" (product).' in rendered, rendered

    def test_a_described_set_that_named_nothing_readable_keeps_its_own_reply(self) -> None:
        """Control (AC-1320 / fix round 9 on PR #833): a CLASS word the catalogue cannot
        read, with no product token typed, still gets the described-set structure."""
        out = _compose(
            parser_entities=[{"hint": "category", "raw": "flurbish basin"}],
            resolutions=[{"token": "flurbish basin", "matches": []}],
            unresolved=[],
            compat=[],
            predicate={"qualifying_total": 0, "unrecognized_terms": ["flurbish"], "require": {"stock": True}},
        )
        rendered = _rendered(out)
        assert "Here's what you want: flurbish basin" in rendered, rendered
        assert 'Couldn\'t find: "flurbish".' in rendered, rendered

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


class TestAMissIsNamedAsTypedOnTheDealerConsole:
    """The real engine, resolver and composer over the seeded catalogue (nothing in it
    contains "srthp", and the pasted line is no code): the reply quotes the dealer's own
    token and nothing the set reader derived from it."""

    def test_a_word_shaped_product_token_is_named_as_typed(self, console) -> None:
        """Turn 11 as typed. On main: 'Here's what you want: srt / Couldn't find: "srt".'
        The structure is the described-set miss's own (fix round 9 on PR #833); the words
        are the dealer's."""
        c = console()
        out = c.say("Srt-hp", stock(product("Srt-hp")))
        assert out == f"Here's what you want: Srt-hp\n\nCouldn't find: \"Srt-hp\".\n\n{REFER}", out
        assert c.stock_calls == [], c.stock_calls

    def test_a_pasted_catalogue_line_is_named_as_typed_and_carries_no_set(self, console) -> None:
        """Turn 4 as typed, on a catalogue whose vocabulary reads none of the line's words,
        so the described-set miss structure answers with the line itself. On main the
        reader's class words were both the header and the miss ('Here's what you want:
        sorento one piece washdown toilet / Couldn't find: "sorento one piece washdown
        toilet"'). On a catalogue whose words DO read a set (prod's 35 water closets) the
        same line takes the product-code miss instead; that branch is pinned by
        `test_spec_search_members_under_an_unplaced_token_print_no_header`."""
        c = console()
        out = c.say(DESCRIPTION_LINE, stock(product(DESCRIPTION_LINE)))
        assert out == f"Here's what you want: {DESCRIPTION_LINE}\n\nCouldn't find: \"{DESCRIPTION_LINE}\".\n\n{REFER}", out
        assert "sorento one piece washdown toilet" not in out, out
        assert c.state["focus"]["set_page"] is None, c.state["focus"]
        assert c.stock_calls == [], c.stock_calls

    def test_control_a_bare_stock_ask_still_gets_the_scope_sentence(self, console) -> None:
        """`test_rearch_r13` T2's shape on the dealer console: a message that named
        nothing is the scope sentence's one case."""
        c = console()
        out = c.say("stock?", stock())
        assert out.startswith(SCOPE_SENTENCE), out


class TestASetReadOffAMissedTokenIsNotCarried:
    """The engine's `set_page` seam on a profile that carries sets (the certificate
    lane's counted-set harness, `test_counted_set_no_paging.seven_taps`: seven taps with
    certificates, the list limit lowered to five so the set is "listed to the limit" and
    `set_page_carry` is asked). Reviewer kill M7: the blank schema CAN produce this shape
    once the class word sits inside the typed line."""

    LINE = "SORENTO TAPS (CHROME)-ZZQNINE"

    @staticmethod
    def _run(session_factory, stub_parser, stub_access, seven_taps, monkeypatch, verdict: dict, text: str):
        from app.services.chatbot.lanes.business import answer as answer_mod
        from tests.chatbot.test_counted_set_no_paging import _turn
        from tests.chatbot.test_lane_require import _s4_session_vars

        monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 5)
        engine_mod, _codes, _calls, contact_id = seven_taps
        stub_parser(verdict)
        stub_access()
        reply = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text=text)
        focus = (_s4_session_vars(session_factory, contact_id) or {}).get("focus") or {}
        return reply, focus.get("set_page")

    def test_a_set_read_off_the_words_of_an_unplaced_line_is_not_carried(
        self, session_factory, stub_parser, stub_access, seven_taps, monkeypatch
    ) -> None:
        """Turn 5's shape: ONE product token, a description with no digit, no class entity.
        The spec tier reads the 7 taps off its words (qualifying 7 > 5, so the carry would
        otherwise be written: the words gave the set a description, which is exactly what
        `set_page_carry` accepts in place of a scope term)."""
        from tests.chatbot.test_counted_set_no_paging import _tap_cert_verdict

        verdict = _tap_cert_verdict(
            entities=[{"raw": self.LINE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
            user_goal=self.LINE,
        )
        reply, set_page = self._run(session_factory, stub_parser, stub_access, seven_taps, monkeypatch, verdict, self.LINE)
        assert set_page is None, set_page
        assert "Here's what you want" not in reply, reply
        assert self.LINE in reply, reply

    def test_a_set_the_customer_described_beside_a_missed_code_stays_carried(
        self, session_factory, stub_parser, stub_access, seven_taps, monkeypatch
    ) -> None:
        """Control (reviewer finding 2): "which tap has cert ZZQ9917" names the set with
        the customer's OWN class word; the reply lists it and names the missed code, so
        the carry stays for the follow-up count. The reply and the carry agree."""
        from tests.chatbot.test_counted_set_no_paging import _tap_cert_verdict

        verdict = _tap_cert_verdict()
        verdict["entities"].append({"raw": "ZZQ9917", "hint": "product", "canonical_code": None, "current_message": True, "confident": True})
        reply, set_page = self._run(
            session_factory, stub_parser, stub_access, seven_taps, monkeypatch, verdict, "which tap has cert ZZQ9917"
        )
        assert isinstance(set_page, dict) and set_page["set_key"]["scope_terms"] == ["tap"], set_page
        assert "Here's what you want: taps with certificates (7, showing 1 to 5)" in reply, reply
        assert "I could not find ZZQ9917." in reply, reply


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
