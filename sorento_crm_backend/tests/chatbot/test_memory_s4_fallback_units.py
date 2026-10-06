"""S4 graceful fallback: the pins beside the replay corpus (AC-MEM080 to AC-MEM093).

The corpus (`test_memory_s4_fallback_replay.py`) plays plan 7.3's ten exchanges end to
end. This file pins each rule on its own: the ack guard one class at a time
(AC-MEM081), the clarifier's two answer shapes, the language pick and the `.ms` / `.zh`
templates (AC-MEM089), the reply composer per kind and per level (plan 6.0), the
two-turn history re-run (AC-MEM082), no silent turn and no exception text (AC-MEM084 to
AC-MEM086), the handover's who (AC-MEM087), and the memory trace on the lane (AC-MEM093).

Postgres only for the engine pins (`tests/chatbot/conftest.py::session_factory`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.chatbot import copy as copy_mod
from app.services.chatbot.lanes import fallback
from app.services.chatbot_reply_copy import (
    CHATBOT_REPLY_COPY,
    CHATBOT_REPLY_ESCALATE_OFFER,
    CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM,
    CHATBOT_REPLY_ESCALATION_DECLINED,
    CHATBOT_REPLY_OFFER_DECLINED,
    CHATBOT_TURN_ERROR_REPLY,
    FALLBACK_REPLY_COPY,
)
from tests.chatbot.test_engine import stub_access  # noqa: F401
from tests.chatbot.test_memory_s4_fallback_replay import (  # noqa: F401
    _run_turn,
    _seed_contact,
    _seed_frames,
    lane,
    sent_text,
)

COPY = copy_mod.fallback_copy()


# --------------------------------------------------------------------------- #
# AC-MEM081: the guard, one class per case
# --------------------------------------------------------------------------- #


class TestAckGuard:
    @pytest.mark.parametrize(
        "ack",
        [
            "Sure, 25 units are ready.",  # a digit
            "SRTWB-PP is in stock.",  # a code shape (hyphenated caps)
            "That one costs RM12.",  # a price
            "It arrives on Monday.",  # a weekday
            "Delivery is due in September.",  # a month
            "SRTWBX is the one you want.",  # an all-caps code word
            "五个都有货。",  # a CJK numeral
        ],
    )
    def test_a_fact_the_input_never_had_is_refused(self, ack: str) -> None:
        assert not fallback.ack_is_safe(ack, "message: thanks")

    def test_the_same_fact_is_allowed_when_the_input_carries_it(self) -> None:
        assert fallback.ack_is_safe("Sure, SRTWB1455 it is.", "user_goal: stock for SRTWB1455")

    def test_a_plain_human_ack_passes(self) -> None:
        assert fallback.ack_is_safe("Morning Mr Tan!", "first_name: Tan")

    def test_more_than_25_words_is_not_an_ack(self) -> None:
        assert not fallback.ack_is_safe(" ".join(["okay"] * 26), "")


class TestClarifierAnswer:
    def test_the_s4_shape(self) -> None:
        said = fallback.read_clarifier({"ack": "Hi!", "language": "ms"})
        assert (said.text, said.language, said.shape) == ("Hi!", "ms", "ack")

    def test_the_older_response_shape_is_the_whole_reply(self) -> None:
        said = fallback.read_clarifier({"response": "Hello, how can I help?"})
        assert (said.text, said.shape) == ("Hello, how can I help?", "response")

    def test_an_unknown_language_is_dropped(self) -> None:
        assert fallback.read_clarifier({"ack": "Hi", "language": "fr"}).language is None

    def test_nothing_to_say(self) -> None:
        assert fallback.read_clarifier({"ack": "", "response": ""}) is None
        assert fallback.read_clarifier("   ") is None


# --------------------------------------------------------------------------- #
# AC-MEM089: languages and the untouched existing copy
# --------------------------------------------------------------------------- #


class TestLanguages:
    def test_the_saved_language_wins_then_the_stated_then_the_clarifiers(self) -> None:
        assert fallback.pick_language("zh", "ms", "en") == "zh"
        assert fallback.pick_language(None, "ms", "en") == "ms"
        assert fallback.pick_language(None, None, "ms") == "ms"
        assert fallback.pick_language(None, None, None) == "en"

    def test_every_new_template_has_en_ms_and_zh_keys(self) -> None:
        for name in FALLBACK_REPLY_COPY:
            for suffix in ("", ".ms", ".zh"):
                key, text, _ = CHATBOT_REPLY_COPY[f"{name}{suffix}"]
                assert key == f"chatbot_reply_{name}{suffix}" and text.strip()
                assert len(key) <= 64, key  # `ai_prompt_versions.name` is String(64)

    @pytest.mark.parametrize("lang", ["en", "ms", "zh"])
    def test_render_in_picks_the_language(self, lang: str) -> None:
        texts, _ = FALLBACK_REPLY_COPY["history_offer"]
        assert COPY.render_in("history_offer", lang) == texts[lang]

    def test_a_missing_variant_falls_back_to_the_bare_key(self) -> None:
        canned = copy_mod.CannedCopy(templates={"history_offer": "EN only"})
        assert canned.render_in("history_offer", "ms") == "EN only"
        assert canned.render_in("history_offer", "fr") == "EN only"

    def test_existing_copy_is_byte_identical_and_the_escalation_regex_still_reads_it(self) -> None:
        from app.services.chatbot.lanes.business.sub_answer import _ESCALATE_OFFER_RE

        assert CHATBOT_REPLY_COPY["escalate_offer"][1] == CHATBOT_REPLY_ESCALATE_OFFER
        assert CHATBOT_REPLY_COPY["escalation_declined"][1] == CHATBOT_REPLY_ESCALATION_DECLINED == "Escalation declined."
        assert CHATBOT_REPLY_COPY["offer_declined"][1] == CHATBOT_REPLY_OFFER_DECLINED == "Okay, noted."
        for offer in (CHATBOT_REPLY_ESCALATE_OFFER, CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM):
            assert _ESCALATE_OFFER_RE.search(offer)


# --------------------------------------------------------------------------- #
# The composer per kind (plan 7.1) and per level (plan 6.0)
# --------------------------------------------------------------------------- #


def _ctx(**kw) -> fallback.FallbackContext:
    base = {"kind": "small_talk", "level": "full"}
    base.update(kw)
    return fallback.FallbackContext(**base)


class TestCompose:
    def test_history_is_numbered_with_the_rerun_offer(self) -> None:
        text = fallback.compose("Sure.", _ctx(kind="history", history=["Mon 28 Sep: a", "Thu 25 Sep: b"]), COPY, "en")
        assert text == (
            "Sure.\n\nHere's what you checked with me recently:\n1. Mon 28 Sep: a\n2. Thu 25 Sep: b"
            "\n\nReply with a number and I'll run it again with today's figures."
        )

    def test_history_lists_at_most_five(self) -> None:
        text = fallback.compose("Sure.", _ctx(kind="history", history=[f"item {i}" for i in range(8)]), COPY, "en")
        assert "5. item 4" in text and "6. " not in text

    def test_history_with_nothing_held_says_so_and_offers_the_menu(self) -> None:
        text = fallback.compose("Sure.", _ctx(kind="history"), COPY, "en")
        assert COPY.render("history_nothing") in text and COPY.render("fallback_offer") in text

    def test_small_talk_with_nothing_held_offers_the_menu(self) -> None:
        assert fallback.compose("Hi!", _ctx(), COPY, "en") == f"Hi! {COPY.render('fallback_offer')}"

    def test_small_talk_with_a_last_conversation_offers_a_rerun(self) -> None:
        text = fallback.compose(
            "Hi!", _ctx(last_time="Thu 25 Sep, Stock: Asked about stock for X and got an answer"), COPY, "en"
        )
        assert text == (
            "Hi! Last time: Thu 25 Sep, Stock: Asked about stock for X and got an answer. "
            "Want me to check any of that again, or something new?"
        )

    def test_usual_products_and_site_shape_the_offer(self) -> None:
        text = fallback.compose("Hi!", _ctx(usual_products=["A1", "B2"], usual_site="Kuching"), COPY, "en")
        assert text.endswith("Want me to check Kuching stock for A1 or B2, or something new?")

    def test_unknown_with_a_customer_offers_two_numbered_options(self) -> None:
        text = fallback.compose("Sorry.", _ctx(kind="unknown", customer="Acme", team="Customer Service"), COPY, "en")
        assert "outstanding DOs for Acme" in text and "1. Check outstanding DOs" in text
        assert "2. Customer Service team" in text

    def test_a_noted_role_names_the_customer_when_linked(self) -> None:
        stmt = [{"key": "role", "value": "purchaser"}]
        assert "I've noted you're the purchaser at Acme." in fallback.compose(
            "Ok.", _ctx(noted=stmt, customer="Acme"), COPY, "en"
        )
        assert "I've noted you're the purchaser." in fallback.compose("Ok.", _ctx(noted=stmt), COPY, "en")

    def test_a_noted_language_is_confirmed_in_that_language(self) -> None:
        text = fallback.compose("Boleh.", _ctx(noted=[{"key": "language", "value": "ms"}]), COPY, "ms")
        assert "Lepas ni saya balas dalam Bahasa Melayu." in text
        assert COPY.render("fallback_offer.ms") in text

    def test_a_template_never_leaves_a_token_in_the_reply(self) -> None:
        for kw in (
            {},
            {"kind": "history", "history": ["x"]},
            {"last_time": "x"},
            {"usual_products": ["A"]},
            {"kind": "unknown", "customer": "C", "team": "T"},
        ):
            for lang in ("en", "ms", "zh"):
                assert "{{" not in fallback.compose("Ok.", _ctx(**kw), COPY, lang)


# --------------------------------------------------------------------------- #
# Engine: levels, the two-turn re-run, no silence, no exception text, who
# --------------------------------------------------------------------------- #

FRAME = {"days_ago": 3, "domain": "inventory", "summary": "Asked about stock for SRTWB1455 and got an answer."}
STOCK = {"domain_hint": "inventory", "entities": [{"raw": "SRTWC286", "hint": "product", "canonical_code": "SRTWC286",
                                                   "current_message": True, "confident": True, "hint_confident": True}]}
USUAL = [{"key": "usual_products", "value": ["SRTWB1455"], "source": "tallied", "source_ref": None,
          "first_seen": "2026-09-01", "last_seen": "2026-09-25", "seen_count": 4, "set_by": None}]


def _turn(session_factory, stub_access, message: str, verdict: dict, n: int, console: bool = False):
    result, _prompt = _run_turn(
        session_factory, stub_access, message=message, verdict_overrides=verdict, n=n, console=console
    )
    return result


class TestLevelsDecideWhatTheReplyReads:
    def test_this_conversation_lists_the_open_one_only(self, session_factory, stub_access, lane) -> None:
        _seed_contact(session_factory, {}, level="conversation")
        _seed_frames(session_factory, [FRAME], is_test=False)
        _turn(session_factory, stub_access, "check stock srtwc286", STOCK, 0)
        result = _turn(session_factory, stub_access, "what did i ask", {"message_type": "history_question"}, 5)
        text = sent_text(result)
        assert "1. " in text and "SRTWC286" in text, text
        assert "SRTWB1455" not in text, "a closed conversation needs Past conversations or above"

    def test_past_conversations_never_use_profile_facts(self, session_factory, stub_access, lane) -> None:
        _seed_contact(session_factory, {"profile_facts": USUAL}, level="episodes")
        _seed_frames(session_factory, [FRAME], is_test=False)
        result = _turn(session_factory, stub_access, "morning", {"message_type": "casual"}, 5)
        text = sent_text(result)
        assert "Last time: " in text and ", Stock: Asked about stock for SRTWB1455" in text, text
        assert "Want me to check stock for" not in text, "usual products need Full memory"

    def test_off_holds_the_fallback_with_no_memory_line(self, session_factory, stub_access, lane) -> None:
        _seed_contact(session_factory, {"profile_facts": USUAL}, level=None)
        _seed_frames(session_factory, [FRAME], is_test=False)
        result = _turn(session_factory, stub_access, "what did i ask", {"message_type": "history_question"}, 5)
        text = sent_text(result)
        assert text == f"Sure. {COPY.render('fallback_offer')}", text
        # The clarifier's slice is empty at Off: no memory reaches it either.
        assert "memory:" not in lane.clarifier_prompts[-1]

    def test_the_clarifier_is_asked_for_an_ack_and_given_the_slice(self, session_factory, stub_access, lane) -> None:
        _seed_contact(session_factory, {"profile_facts": USUAL, "first_name": "Tan"}, level="full")
        _seed_frames(session_factory, [FRAME], is_test=False)
        _turn(session_factory, stub_access, "morning", {"message_type": "casual"}, 5)
        prompt = lane.clarifier_prompts[-1]
        assert '"ack"' in prompt and "first_name: Tan" in prompt
        assert "Recent conversations: " in prompt and ", Stock: Asked about stock for SRTWB1455" in prompt
        assert "About this contact: usual products" in prompt


class TestHistoryRerun:
    def test_a_number_after_the_list_reruns_that_line_with_a_fresh_fetch(
        self, session_factory, stub_access, lane, monkeypatch
    ) -> None:
        """AC-MEM082, two turns: the list fetches nothing; "1" (the parser resolving the
        line from the Previous response, recorded here) runs the stock lane fresh.

        The resolver places SRTWC286 (integration round 5, merge of main b299bf6e):
        #1301 slice 3 (F5) withholds the stock fetch for a code the resolver cannot
        place, so the unplaced code this test used to send no longer reaches the tool on
        either turn. Only the resolver is faked; the MCP stub the test grades is the
        `lane` fixture's own."""
        import uuid as uuid_mod

        from app.services.chatbot import engine as engine_mod
        from tests.chatbot.test_outstanding_lane import _resolve_services

        match = {"uuid": str(uuid_mod.uuid4()), "entity_type": "product", "canonical_code": "SRTWC286"}
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: _resolve_services({"SRTWC286": match}),
        )
        _seed_contact(session_factory, {}, level="full")
        _turn(session_factory, stub_access, "check stock srtwc286", STOCK, 0)
        lane.tool_calls.clear()
        listed = _turn(session_factory, stub_access, "what did i ask", {"message_type": "history_question"}, 5)
        assert "1. " in sent_text(listed) and lane.tool_calls == []
        rerun_verdict = {
            **STOCK,
            "entities": [{**STOCK["entities"][0], "current_message": False}],
        }
        rerun = _turn(session_factory, stub_access, "1", rerun_verdict, 6)
        assert rerun.branch_kind == "business_query", rerun.branch_kind
        assert lane.tool_calls, "the re-run must fetch today's figures"

    def test_a_history_question_naming_a_domain_still_answers_from_memory(
        self, session_factory, stub_access, lane
    ) -> None:
        _seed_contact(session_factory, {}, level="full")
        _turn(session_factory, stub_access, "check stock srtwc286", STOCK, 0)
        lane.tool_calls.clear()
        result = _turn(
            session_factory,
            stub_access,
            "what stock did i check",
            {"message_type": "history_question", "domain_hint": "inventory"},
            5,
        )
        assert result.branch_kind == "low_signal" and lane.tool_calls == []
        assert "1. " in sent_text(result)

    def test_a_failed_clarifier_still_lists_behind_the_canned_ack(
        self, session_factory, stub_access, lane, monkeypatch
    ) -> None:
        from app.services.chatbot.lanes import casual

        _seed_contact(session_factory, {}, level="full")
        _turn(session_factory, stub_access, "check stock srtwc286", STOCK, 0)

        def boom(config, prompt):
            raise casual.ClarifierError("provider down: sk-secret")

        monkeypatch.setattr(casual, "call_clarifier", boom)
        result = _turn(session_factory, stub_access, "what did i ask", {"message_type": "history_question"}, 5)
        text = sent_text(result)
        assert text.startswith("Noted.") and "1. " in text and "sk-secret" not in text, text


class TestNeverSilentNeverAnException:
    def test_a_clarifier_error_sends_the_apology(self, session_factory, stub_access, lane, monkeypatch) -> None:
        """AC-MEM086."""
        from app.services.chatbot.lanes import casual

        _seed_contact(session_factory, {}, level=None)

        def boom(config, prompt):
            raise ValueError("Traceback: KeyError 'response' at provider.py:88")

        monkeypatch.setattr(casual, "call_clarifier", boom)
        result = _turn(session_factory, stub_access, "hello", {"message_type": "casual"}, 0)
        assert sent_text(result) == CHATBOT_TURN_ERROR_REPLY
        assert result.status == "failed"

    def test_the_exception_prefix_is_gone_from_the_code(self) -> None:
        """AC-MEM086's grep guard."""
        app_dir = Path(__file__).resolve().parents[2] / "app"
        for path in app_dir.rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            assert "CLARIFIER_ERROR_PREFIX" not in body, path
            assert '"There is some error encountered by the AI: "' not in body, path

    def test_an_escalation_clarify_with_no_words_still_sends_the_handover_offer(self) -> None:
        """AC-MEM084/085: the lane's one clarify builder never returns silence."""
        from app.services.chatbot.lanes.business.sub_answer import _ESCALATE_OFFER_RE
        from app.services.chatbot.lanes.escalation import _clarify_actions

        actions = _clarify_actions("   ", options=[], dry_run=True)
        assert [a["kind"] for a in actions] == ["send_message"]
        assert _ESCALATE_OFFER_RE.search(actions[0]["text"])

    def test_an_escalation_decline_sends_a_visible_line(self, session_factory, stub_access, lane) -> None:
        """AC-MEM084: the decline arm answers (its existing copy)."""
        _seed_contact(session_factory, {}, level=None)
        result = _turn(
            session_factory,
            stub_access,
            "no it's okay",
            {
                "message_type": "confirmation",
                "escalation": {"is_escalation_confirmation": False, "escalation_declined": True, "company_pick": None},
            },
            0,
        )
        assert result.branch_kind == "escalation_declined", result.branch_kind
        assert sent_text(result).strip(), result.actions


class TestHandoverNamesWho:
    ASK = {
        "message_type": "request_for_help",
        "intent_hint": "commercial_request",
        "user_goal": "asking for a discount",
        "routing": {"suggested_team": "customer_service", "suggested_agent": None},
    }

    def test_a_commercial_ask_with_no_salesperson_keeps_the_team_line(
        self, session_factory, stub_access, lane
    ) -> None:
        _seed_contact(session_factory, {"customer": {"name": "Acme", "code": "AC1"}}, level="full")
        result = _turn(session_factory, stub_access, "10% off pls", self.ASK, 0, console=True)
        text = sent_text(result)
        # WA-MSG-TRIM: the one customer line no longer names the team; the PIC's comment does.
        assert text.strip() == "Routed to your PIC, they will reply shortly.", text
        comments = [a["text"] for a in result.actions if a.get("kind") == "add_comment"]
        assert comments and "Salesperson:" not in comments[0]
        assert comments[0].startswith("Team: customer_service"), comments[0]

    def test_a_non_commercial_ask_never_names_the_salesperson(self, session_factory, stub_access, lane) -> None:
        _seed_contact(
            session_factory, {"customer": {"name": "Acme", "code": "AC1", "salesperson": "Aina"}}, level="full"
        )
        ask = {**self.ASK, "intent_hint": None}
        result = _turn(session_factory, stub_access, "talk to a person", ask, 0, console=True)
        assert "Aina" not in sent_text(result)


class TestTheLaneRecordsMemory:
    def test_a_fallback_turn_writes_the_memory_trace(self, session_factory, stub_access, lane) -> None:
        """AC-MEM093."""
        from app.models.chatbot_turn import ChatbotTurn

        _seed_contact(session_factory, {}, level="full")
        result = _turn(session_factory, stub_access, "hello", {"message_type": "casual"}, 0)
        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        kinds = [r.get("kind") for r in (row.trace or []) if isinstance(r, dict)]
        assert "memory" in kinds, kinds
        looked = [r for r in row.trace if isinstance(r, dict) and r.get("stage") == "looked_up"][-1]
        assert looked["facts"]["answer_shape"] == "ack" and looked["facts"]["fallback_kind"] == "small_talk"
        assert json.dumps(looked["facts"]["memory_level"]) == '"full"'


class TestParserWords:
    """The words `mem_0003_parser_history` publishes (key-free: the live grading needs
    the provider key, `scripts/chatbot_parser_parity.py --memory-cases`)."""

    def test_the_history_question_is_named_in_the_owners_wording(self) -> None:
        from app.services.chatbot_parser_prompt import MEMORY_ADDENDUM, SEMANTIC_PARSER_PROMPT

        assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM)
        for words in (
            '"what do I normally ask about"',
            '"what did I ask"',
            '"what products do I usually ask about"',
            'never "clarification"',
            "re-runs that line",
            'intent_hint "commercial_request"',
        ):
            assert words in MEMORY_ADDENDUM, words
