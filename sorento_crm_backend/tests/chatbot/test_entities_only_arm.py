"""S3 (PLAN-chatbot-media-into-turn.md): the `entities_only` lane - bare entities, no
domain, no focus, routed away from `casual`'s LLM clarifier and answered deterministically.

AC-1822 to AC-1832 (UAC section C). General fix (plan Q1): the arm serves TYPED bare
codes too, not only photo-sourced ones - most tests here type the codes directly, which
is both the cheapest way to prove the lane exists and the plan's own scope.

Every test is expected to fail today: `turn/apply.py::_lane` routes a domain-less
`business_query` straight to `"casual"` (`app/services/chatbot/turn/apply.py`, the
`message_type == "business_query" and not domains: return "casual"` line), which reaches
the stubbed LLM clarifier (`tests/chatbot/conftest.py::_stub_casual_llm`, autouse) - so
the right red reason is "the casual clarifier ran" / "lane != entities_only", never an
import error.

Reuses `test_outstanding_lane.py`'s real-engine harness (`_run_turn`, `_resolve_services`,
`_enable_business_lane`, `_session_of`) rather than a second copy of the business-lane
wiring - the same precedent `test_pass5_item2_...` and `test_pass4_item2_...` already
follow.

**Journey-runner note for the captain**: `tests/chatbot/journeys/*.json` on this branch is
NOT a pytest-collected suite - it is consumed by a separate live-console replay tool
(the file's own `"cold": true` / real `contact_respond_id` shape is a LIVE journey
recording, not a Postgres-fixture case). Per the brief's own fallback ("otherwise keep
them in `test_entities_only_arm.py` and say so"), J1-J4 (AC-1827/1828/1829/1830) are
written here as ordinary pytest tests instead of a new file under that directory.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod

from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded  # noqa: F401
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _enable_business_lane,
    _resolve_services,
    _run_turn,
    _seed_contact,
    _session_of,
    _wire_business_services,
    ResolveGateServices,
)
from tests.chatbot.test_media_intake_turn import (
    IMAGE_RESULT,
    _image_envelope,
    _seed_media_limit,
    _seed_settings,
    _voice_envelope,
    media_pipeline,  # noqa: F401
)

PRODUCT_A_CODE = "SRTWT7445"
PRODUCT_A_UUID = "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa"
PRODUCT_B_CODE = "SRTWT7446"
PRODUCT_B_UUID = "aaaaaaaa-2222-2222-2222-aaaaaaaaaaaa"
UNPLACED_CODE = "MBF-9902-ZZT"

MATCHES = {
    PRODUCT_A_CODE: {"uuid": PRODUCT_A_UUID, "entity_type": "product", "canonical_code": PRODUCT_A_CODE},
    PRODUCT_B_CODE: {"uuid": PRODUCT_B_UUID, "entity_type": "product", "canonical_code": PRODUCT_B_CODE},
}


def _bare_entities_qf(*, placed: list[str], unplaced: list[str] | None = None) -> dict[str, Any]:
    """A parser verdict for "typed bare codes, no domain, no ask" - the shape the plan
    names as the gap (`_lane`'s casual fallthrough)."""
    entities = [
        {
            "raw": code,
            "hint": "product",
            "canonical_code": None,
            "current_message": True,
            "confident": True,
        }
        for code in [*placed, *(unplaced or [])]
    ]
    return _parser_output(domain_hint=None, intent_hint=None, entities=entities, asks=[])


def _focus_products(session_factory) -> list[dict[str, Any]]:
    sess = _session_of(session_factory)
    focus = sess.get("focus") or {}
    return focus.get("products") or []


class TestRoutesToEntitiesOnlyNotCasual:
    """AC-1822."""

    def test_bare_codes_do_not_reach_the_casual_clarifier(
        self, session_factory, seeded, monkeypatch, _stub_casual_llm
    ):
        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"{PRODUCT_A_CODE}, {PRODUCT_B_CODE}",
            msg_id="ZZT-eo-1", matches=MATCHES,
        )
        assert not _stub_casual_llm, (
            f"the casual LLM clarifier ran {len(_stub_casual_llm)} time(s) - bare "
            "entities with no domain still fall through to the casual lane"
        )

    def test_a_no_hint_product_shaped_token_still_routes_to_entities_only(
        self, session_factory, seeded, monkeypatch, _stub_casual_llm
    ):
        """Review round B1(b), measured repro: a bare code the parser tagged with NO
        hint at all ("MBF-9902-ZZT" - has both letters and digits, unlike a name) is
        still a product ask, not idle chat."""
        qf = _parser_output(
            domain_hint=None,
            intent_hint=None,
            asks=[],
            entities=[
                {
                    "raw": UNPLACED_CODE,
                    "hint": None,
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
        )
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=UNPLACED_CODE,
            msg_id="ZZT-eo-hint-1", matches=MATCHES,
        )
        assert not _stub_casual_llm, (
            f"the casual LLM clarifier ran {len(_stub_casual_llm)} time(s) - a no-hint "
            "product-shaped token still fell through to casual"
        )

    def test_a_customer_only_message_falls_through_to_casual(
        self, session_factory, seeded, monkeypatch, _stub_casual_llm
    ):
        """Review round B1(b), measured repro: "Hanlim" alone (hint customer, no
        digits) is not a bare product ask - it must fall through to `casual` exactly
        as before, not misroute to the lane that can only resolve products."""
        qf = _parser_output(
            domain_hint=None,
            intent_hint=None,
            asks=[],
            entities=[
                {
                    "raw": "Hanlim",
                    "hint": "customer",
                    "canonical_code": None,
                    "current_message": True,
                    "confident": True,
                }
            ],
        )
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body="Hanlim",
            msg_id="ZZT-eo-hint-2", matches=MATCHES,
        )
        assert _stub_casual_llm, (
            "a customer-only message did not reach the casual clarifier - it "
            "misrouted to entities_only"
        )


class TestResolvesAndSettlesFocus:
    """AC-1823: placed rows land on focus.products WITH uuids; unplaced tokens do not."""

    def test_placed_tokens_carry_a_uuid_unplaced_do_not(self, session_factory, seeded, monkeypatch):
        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE], unplaced=[UNPLACED_CODE])
        _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"{PRODUCT_A_CODE}, {UNPLACED_CODE}",
            msg_id="ZZT-eo-2", matches=MATCHES,
        )
        products = _focus_products(session_factory)
        placed_row = next((p for p in products if p.get("raw") == PRODUCT_A_CODE), None)
        assert placed_row is not None, f"focus.products carries no row for {PRODUCT_A_CODE}: {products}"
        assert placed_row.get("uuid") == PRODUCT_A_UUID, (
            f"{PRODUCT_A_CODE} was not resolved onto focus.products - entities_only "
            f"never ran the resolver: {placed_row}"
        )
        unplaced_row = next((p for p in products if p.get("raw") == UNPLACED_CODE), None)
        assert unplaced_row is None or not unplaced_row.get("uuid")

    def test_the_arm_emits_its_own_apply_event_with_the_resolved_uuid(
        self, session_factory, seeded, monkeypatch
    ):
        """Kill test (reviewer round): the ONE `apply` trace event every turn gets is
        recorded BEFORE this arm ever runs, so the Apply tab stayed on the
        pre-resolution snapshot forever - a real turn (f68e75a2-096b-4575-b937-
        297889709de1) resolved correctly but LOOKED unresolved on that tab, which is
        what the browser pass reported as a defect. `_run_entities_only_arm` now
        emits its OWN second `apply` event after resolving; this asserts the LAST one
        (what `trace_detail.py::_apply()` reads, `entries[-1]`) shows the enriched
        `after` while `before` is still the raw, unresolved snapshot.
        """
        from tests.chatbot.test_media_intake_turn import _trace_of

        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=PRODUCT_A_CODE,
            msg_id="ZZT-eo-apply-event", matches=MATCHES,
        )
        trace = _trace_of(session_factory, result.turn_id)
        apply_events = [r for r in trace if r.get("kind") == "apply"]
        assert len(apply_events) >= 2, (
            "expected a SECOND apply event from the arm's own resolution, found "
            f"{len(apply_events)}: {apply_events}"
        )
        last_apply = apply_events[-1]
        products_diff = last_apply.get("state_diff", {}).get("products", {})
        after_row = (products_diff.get("after") or [None])[0]
        before_row = (products_diff.get("before") or [None])[0]
        assert after_row is not None and after_row.get("uuid") == PRODUCT_A_UUID, (
            f"the LAST apply event's 'after' must show the resolved uuid: {last_apply}"
        )
        assert before_row is not None and not before_row.get("uuid"), (
            f"the LAST apply event's 'before' must still be the pre-resolution "
            f"snapshot (no uuid yet): {last_apply}"
        )


class TestReplyWordingBySource:
    """AC-1824/AC-1825: the reply names the source (photo vs typed) with its own words."""

    def test_typed_source_reply_wording(self, session_factory, seeded, monkeypatch):
        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE], unplaced=[UNPLACED_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf,
            text_body=f"{PRODUCT_A_CODE} {PRODUCT_B_CODE} {UNPLACED_CODE}",
            msg_id="ZZT-eo-3", matches=MATCHES,
        )
        text = (result.reply or {}).get("text") or ""
        assert text.startswith(f"I have {PRODUCT_A_CODE} and {PRODUCT_B_CODE}."), text
        assert f"Couldn't find {UNPLACED_CODE}." in text, text
        assert text.rstrip().endswith("What would you like me to know?"), text

    def test_no_couldnt_find_line_when_everything_placed(self, session_factory, seeded, monkeypatch):
        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"{PRODUCT_A_CODE} {PRODUCT_B_CODE}",
            msg_id="ZZT-eo-4", matches=MATCHES,
        )
        text = (result.reply or {}).get("text") or ""
        assert "Couldn't find" not in text


class TestReplyWordingWhenNothingPlaced:
    """B1(a) (review round), end to end - never independently tested before this: the
    "nothing placed" branch of `entities_only_reply` never says "I have ." with an
    empty join. Typed and photo sources were the reviewer's own repros; the voice
    case (media_prefixed=True, from_photo=False - a genuinely different combination
    from either) is the one the browser pass asked to be added here."""

    def test_typed_source_nothing_placed(self, session_factory, seeded, monkeypatch):
        qf = _bare_entities_qf(placed=[], unplaced=[UNPLACED_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=UNPLACED_CODE,
            msg_id="ZZT-eo-nothing-1", matches=MATCHES,
        )
        text = (result.reply or {}).get("text") or ""
        assert text == f"Couldn't find {UNPLACED_CODE}. Ask again with the correct code.", text

    def test_photo_source_nothing_placed(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({**IMAGE_RESULT, "entities": [{"raw": UNPLACED_CODE}]})
        stub_access()

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        photo_qf = _bare_entities_qf(placed=[], unplaced=[UNPLACED_CODE])
        with _patch.object(parser_mod, "parse", lambda config, user_block: photo_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_image_envelope(caption=None), session_factory=session_factory)

        text = (result.reply or {}).get("text") or ""
        # A LIVE photo outcome already told the customer what was read (the engine's
        # own reply-prefix wrapper, "I read X from that photo.") - the arm's OWN
        # "I could not match any product code in that photo." lead would contradict
        # that and is suppressed the same way its "something placed" lead already is
        # (media_prefixed=True here, a real outcome ran).
        assert text == (
            f"I read {UNPLACED_CODE} from that photo.\nCouldn't find {UNPLACED_CODE}. "
            "What would you like me to do with it?"
        ), text

    def test_voice_source_nothing_placed(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        """The browser pass's own repro: a voice note naming a code nobody could place
        must still name it, after the "I heard: ..." echo - never a bare "What would
        you like me to know?" that reads as though the bot ignored the code entirely.
        """
        _seed_media_limit(session_factory, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": UNPLACED_CODE})
        stub_access()

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        voice_qf = _bare_entities_qf(placed=[], unplaced=[UNPLACED_CODE])
        with _patch.object(parser_mod, "parse", lambda config, user_block: voice_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_voice_envelope(), session_factory=session_factory)

        text = (result.reply or {}).get("text") or ""
        assert text == (
            f"I heard: {UNPLACED_CODE}\nCouldn't find {UNPLACED_CODE}. Ask again with the correct code."
        ), text


class TestOpenQuestionStaysNull:
    """AC-1826.

    NOTE FOR THE CAPTAIN: the bare `open_question is None` / `domains == []` checks are
    ALSO true today (the `casual` lane the message currently falls into sets neither), so
    they are paired with a check on the reply TEXT itself - which today is literally the
    autouse `_stub_casual_llm` fixture's canned string - so the test still goes red for
    the right reason (wrong lane), not a vacuously-true session-shape assertion.
    """

    def test_open_question_and_domains_stay_empty(self, session_factory, seeded, monkeypatch):
        qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"{PRODUCT_A_CODE} {PRODUCT_B_CODE}",
            msg_id="ZZT-eo-5", matches=MATCHES,
        )
        sess = _session_of(session_factory)
        assert sess.get("open_question") is None
        assert (sess.get("focus") or {}).get("domains") in (None, [])
        text = (result.reply or {}).get("text") or ""
        assert text != "ZZT stubbed casual reply.", (
            "the turn answered through the casual LLM clarifier stub, not the "
            "entities_only lane"
        )


class TestJourneyPhotoThenCheckStock:
    """AC-1827 (J1): photo-only turn, then "check stock" -> answers the placed codes,
    domains == ["inventory"], no re-ask for a product."""

    def test_photo_then_check_stock(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {**IMAGE_RESULT, "entities": [{"raw": PRODUCT_A_CODE}, {"raw": PRODUCT_B_CODE}]}
        )
        stub_access()

        # Step 1 has no preceding `_run_turn` call (unlike J2/J3/J4, whose photo step
        # rides the SAME `monkeypatch` a prior `_run_turn` call already wired) - so the
        # business-lane switches and the resolver seam are wired explicitly here,
        # exactly what `_run_turn` itself would do internally.
        _enable_business_lane(session_factory)
        call, _captured = _capturing_mcp()
        _wire_business_services(monkeypatch, resolve_services=_resolve_services(MATCHES), mcp_call=call)

        # Step 1: bare photo, no caption -> entities_only, no domain.
        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        photo_qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        with _patch.object(parser_mod, "parse", lambda config, user_block: photo_qf):
            with _patch.object(
                parser_mod,
                "resolve_config",
                lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                    system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
                ),
            ):
                engine_mod.run_turn(_image_envelope(caption=None), session_factory=session_factory)

        products_after_photo = _focus_products(session_factory)
        assert any(p.get("raw") == PRODUCT_A_CODE and p.get("uuid") for p in products_after_photo), (
            "photo-only turn did not settle focus.products - J1 step 1 is not wired"
        )

        # Step 2: "check stock".
        stock_qf = _parser_output(domain_hint="master_products", intent_hint="check_product", entities=[])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=stock_qf, text_body="check stock",
            msg_id="ZZT-eo-j1-2", matches=MATCHES,
        )
        assert result.branch_kind != "clarify_menu", "the bot re-asked for a product instead of using focus"


class TestJourneyCheckStockThenPhoto:
    """AC-1828 (J2): "check stock" (needs-scope reply), then a photo -> answers directly,
    prefix "I read A and B from that photo."."""

    def test_check_stock_then_photo(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {**IMAGE_RESULT, "entities": [{"raw": PRODUCT_A_CODE}, {"raw": PRODUCT_B_CODE}]}
        )
        stub_access()

        stock_qf = _parser_output(domain_hint="master_products", intent_hint="check_product", entities=[])
        _run_turn(
            session_factory, monkeypatch, qf=stock_qf, text_body="check stock",
            msg_id="ZZT-eo-j2-1", matches=MATCHES,
        )

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        photo_qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        with _patch.object(parser_mod, "parse", lambda config, user_block: photo_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_image_envelope(caption=None), session_factory=session_factory)

        text = (result.reply or {}).get("text") or ""
        assert text.startswith(f"I read {PRODUCT_A_CODE} and {PRODUCT_B_CODE} from that photo."), text


class TestJourneyCaptionInOneTurn:
    """AC-1829 (J3): photo with caption "Check stock" -> one turn, stock answered."""

    def test_captioned_photo_answers_in_one_turn(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {
                "rendered_text": f"Check stock: {PRODUCT_A_CODE}, {PRODUCT_B_CODE}",
                "entities": [{"raw": PRODUCT_A_CODE}, {"raw": PRODUCT_B_CODE}],
                "truncated": False,
                "needs_clarification": False,
            }
        )
        stub_access()

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        stock_qf = _parser_output(
            domain_hint="master_products",
            intent_hint="check_product",
            entities=[
                {"raw": PRODUCT_A_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": PRODUCT_B_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ],
        )
        with _patch.object(parser_mod, "parse", lambda config, user_block: stock_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_image_envelope(caption="Check stock"), session_factory=session_factory)

        assert result.branch_kind == "business_query", (
            f"expected the captioned photo to resolve straight to a business query, "
            f"got {result.branch_kind!r}"
        )
        text = (result.reply or {}).get("text") or ""
        assert text.startswith(f"I read {PRODUCT_A_CODE} and {PRODUCT_B_CODE} from that photo."), (
            f"the reply prefix is missing: {text!r}"
        )


class TestJourneyVoice:
    """AC-1830 (J4): voice "stock for SRTWB1455" -> stock answered, prefix "I heard: ..."."""

    def test_voice_stock_ask_answers_with_heard_prefix(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": "stock for SRTWB1455"})
        stub_access()

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        stock_qf = _parser_output(
            domain_hint="master_products",
            intent_hint="check_product",
            entities=[
                {"raw": "SRTWB1455", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )
        with _patch.object(parser_mod, "parse", lambda config, user_block: stock_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_voice_envelope(), session_factory=session_factory)

        text = (result.reply or {}).get("text") or ""
        assert text.startswith("I heard: stock for SRTWB1455"), text

    def test_voice_bare_entities_do_not_double_the_lead(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        """Review round nit: a voice note with a bare code (no domain) reaches the
        entities_only arm - `_apply_media_reply_prefix` already prepends "I heard: X",
        so the arm's OWN "I have X." lead must be suppressed, or the reply doubles it."""
        _seed_media_limit(session_factory, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": PRODUCT_A_CODE})
        stub_access()

        import app.services.chatbot.head.parser as parser_mod
        from unittest.mock import patch as _patch

        bare_qf = _bare_entities_qf(placed=[PRODUCT_A_CODE])
        with _patch.object(parser_mod, "parse", lambda config, user_block: bare_qf), _patch.object(
            parser_mod,
            "resolve_config",
            lambda db, *, current_date, override_version_id=None, grants=None: parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            ),
        ):
            result = engine_mod.run_turn(_voice_envelope(), session_factory=session_factory)

        text = (result.reply or {}).get("text") or ""
        assert text.startswith(f"I heard: {PRODUCT_A_CODE}"), text
        assert f"I have {PRODUCT_A_CODE}." not in text, f"the arm's own lead doubled the prefix: {text!r}"
        assert text.count("I heard:") == 1, text


class TestPromotionFocusCarriesTheSameWay:
    """AC-1831: a photo with a promotion focus already set applies the codes to the
    CURRENT domain, not to stock.

    NOTE FOR THE CAPTAIN: this one is NOT independently red today with typed bare
    entities - the existing focus-carry rule (`apply.py:1440`, plan Evidence 6) already
    routes a bare-entity message to `business_query` under an EXISTING domain; the plan's
    own gap is only the empty-focus/no-domain case (AC-1822). Kept here as a regression
    guard for the media source once S2 lands, and to record the "already works" finding
    rather than silently assume it - it is a positive control, not a red test."""

    def test_existing_promotion_domain_wins(self, session_factory, seeded, monkeypatch):
        # Seed a prior turn that set focus.domains = ["promotion"].
        seed_qf = _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=[])
        _run_turn(
            session_factory, monkeypatch, qf=seed_qf, text_body="any promo running",
            msg_id="ZZT-eo-promo-1", matches=MATCHES,
        )
        sess = _session_of(session_factory)
        assert (sess.get("focus") or {}).get("domains") == ["promotion"], (
            "fixture assumption failed: the seed turn did not carry a promotion focus"
        )

        bare_qf = _bare_entities_qf(placed=[PRODUCT_A_CODE, PRODUCT_B_CODE])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=bare_qf, text_body=f"{PRODUCT_A_CODE} {PRODUCT_B_CODE}",
            msg_id="ZZT-eo-promo-2", matches=MATCHES,
        )
        assert result.branch_kind != "casual", (
            "a bare-entity message under an existing promotion focus still fell "
            "through to casual instead of carrying the focus's domain"
        )


class TestCaptionNamesANewDomain:
    """AC-1832: bare entities with a non-empty focus but a NEW domain word in the
    caption follow the caption's own domain (parser decides, unchanged) - a control
    case that should already work today via the existing carry rule, included so a
    future regression here is caught beside the new lane's tests."""

    def test_new_domain_word_overrides_the_carried_one(self, session_factory, seeded, monkeypatch):
        seed_qf = _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=[])
        _run_turn(
            session_factory, monkeypatch, qf=seed_qf, text_body="any promo running",
            msg_id="ZZT-eo-newdomain-1", matches=MATCHES,
        )
        stock_qf = _parser_output(
            domain_hint="master_products",
            intent_hint="check_product",
            entities=[
                {
                    "raw": PRODUCT_A_CODE, "hint": "product", "canonical_code": None,
                    "current_message": True, "confident": True,
                }
            ],
        )
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=stock_qf, text_body=f"check stock {PRODUCT_A_CODE}",
            msg_id="ZZT-eo-newdomain-2", matches=MATCHES,
        )
        assert result.branch_kind == "business_query"


class TestRealResolverHonoursCompanyScope:
    """B2 (security review, browser pass reproduced 2/2): real, correctly
    company-scoped product codes came back "Couldn't find" every time
    `_run_entities_only_arm` actually resolved them. The real cause, measured
    (see the second test's docstring for the full trail): `resolve_reference_
    post`'s own resolutions carry a `"token"` field, never `"raw"` - the arm's
    lookup dict was keyed on the wrong field, silently collapsing every
    resolution. An explicit `set_company_scope` re-stamp was tried as a second,
    belt-and-suspenders fix and measured to be redundant - `run_turn`'s own
    `_scoped_factory` already stamps every session this arm's `db` could be, for
    any company - so it was deleted rather than kept. Every OTHER test in this
    file stubs `resolve_entity` entirely (`_wire_business_services`/
    `matches=MATCHES`), which is exactly why this gap was invisible here: these
    are the two tests in the file that run the REAL resolver, against REAL
    seeded rows, on a REAL company-mapped contact (`_seed_contact`/a Mocha
    mapping, not the company-less `seeded` fixture every other test uses).
    """

    def test_seeded_codes_place_through_the_real_resolver(self, session_factory, monkeypatch):
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._mc_lookup_seed import product as seed_product

        code_a, code_b = "ZZTSCOPEA1", "ZZTSCOPEB1"
        db = session_factory()
        seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code_a)
        seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code_b)
        db.commit()
        _seed_contact(session_factory, variables={})

        qf = _bare_entities_qf(placed=[code_a, code_b])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=f"{code_a} {code_b}",
            msg_id="ZZT-eo-scope-1", real_resolver=True,
        )

        products = _focus_products(session_factory)
        for code in (code_a, code_b):
            row = next((p for p in products if p.get("raw") == code), None)
            assert row is not None and row.get("uuid"), (
                f"{code} did not place through the real resolver - focus_settles_product "
                f"never fired on a scoped session: {row}"
            )
        text = (result.reply or {}).get("text") or ""
        assert "Couldn't find" not in text, (
            f"a real, company-scoped code came back unplaced (B2's own symptom): {text!r}"
        )

    def test_a_non_default_company_places_its_own_row_not_a_same_code_decoy(
        self, session_factory, monkeypatch
    ):
        """Hot-fix follow-up: the reviewer asked whether `test_seeded_codes_place_
        through_the_real_resolver` above actually guards the explicit
        `set_company_scope(db, contact_scope)` re-stamp B2 added, or only its OTHER
        half (the `resolutions` dict keyed on the wrong field). Measured by hand,
        twice, rather than assumed: with the re-stamp line commented out, BOTH that
        test AND this one (written first specifically to close the gap - a contact
        mapped to Mocha, a same-code decoy product under Sorento) stayed green.

        Conclusion, and why the line is now DELETED rather than kept: `db` already
        carries the correct company scope by the time this arm runs, for ANY
        contact - `run_turn` wraps `session_factory` itself (`_scoped_factory`, H56)
        before `_run_stages` opens ANY session, including this arm's own `db`, so a
        second, later `set_company_scope` call on the same session is a no-op. The
        actual B2 bug was entirely the `resolutions` dict keyed on `"raw"` instead of
        `"token"` (see the fix's other comment in `engine.py`) - that alone made
        every code look unplaced regardless of scope, on every company, which is why
        removing the redundant re-stamp never turned anything red.

        Kept as a real, non-default-company regression guard for cross-company
        isolation in this arm (a same-code decoy under a DIFFERENT company must
        never be the one that places) even though it no longer guards a specific
        line - the underlying behaviour (`_scoped_factory`'s own per-contact
        isolation) is what both this test and the one above actually exercise.
        """
        import json

        from sqlalchemy import text as sql_text

        from tests._mc_lookup_seed import MOCHA_ID, product as seed_product, seed_mocha

        shared_code = "ZZTSCOPEDUP1"
        db = session_factory()
        seed_mocha(db)
        db.commit()
        mocha_row = seed_product(db, company_id=MOCHA_ID, code=shared_code)
        db.commit()
        # The decoy: the SAME code, owned by Sorento (the default company) - a session
        # scoped to the WRONG company (or left ambient/unscoped) could still place this
        # one and the assertion below would not catch a scope leak, so its uuid must
        # differ from the one actually asserted.
        seed_product(db, company_id="00000000-0000-0000-0000-000000000001", code=shared_code)
        db.commit()

        # `_seed_contact` (test_outstanding_lane) hardcodes DEFAULT_COMPANY_ID - this
        # test needs Mocha instead, so the workspace/contact/company-mapping insert is
        # done here, same shape, different company id.
        space_id = "364817"
        db.execute(
            sql_text(
                "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
                "VALUES (gen_random_uuid(), :sid, 'ZZT mocha workspace', 'ZZT-cipher') "
                "ON CONFLICT DO NOTHING"
            ),
            {"sid": space_id},
        )
        db.execute(
            sql_text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb), "
                "(SELECT id FROM respond_workspaces WHERE space_id = :sid LIMIT 1))"
            ),
            {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": {}}), "sid": space_id},
        )
        db.execute(
            sql_text(
                "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"cid": str(CONTACT_ID), "company_id": MOCHA_ID},
        )
        db.commit()

        qf = _bare_entities_qf(placed=[shared_code])
        result, _ = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=shared_code,
            msg_id="ZZT-eo-scope-2", real_resolver=True,
        )

        products = _focus_products(session_factory)
        row = next((p for p in products if p.get("raw") == shared_code), None)
        assert row is not None and row.get("uuid"), (
            f"{shared_code} did not place for the Mocha-mapped contact: {row}"
        )
        assert row.get("uuid") == mocha_row.id, (
            f"placed the WRONG company's row (a scope leak) - expected Mocha's "
            f"{mocha_row.id}, got {row.get('uuid')}"
        )
        text = (result.reply or {}).get("text") or ""
        assert "Couldn't find" not in text, text


