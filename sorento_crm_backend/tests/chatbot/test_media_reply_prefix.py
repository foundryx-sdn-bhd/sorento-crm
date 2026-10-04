"""S2 (PLAN-chatbot-media-into-turn.md): the "I read .../I heard ..." reply prefix.

AC-1817 to AC-1821 (UAC section B). Every test here is expected to fail today because
the prefix does not exist at all - a media turn's reply is whatever the answering arm
produced, with no line prepended. Reuses the media-pipeline harness from
`test_media_intake_turn.py` rather than a second copy of it.
"""
from __future__ import annotations

from typing import Any

from app.models.user import SystemSetting

from app.services.chatbot import engine as engine_mod

from tests.chatbot.test_engine import (
    CONTACT_ID,
    _envelope,
    seeded,  # noqa: F401
    stub_access,  # noqa: F401
    stub_parser,  # noqa: F401
)
from tests.chatbot.test_media_intake_turn import (
    IMAGE_RESULT,
    _image_envelope,
    _seed_media_limit,
    _seed_settings,
    _voice_envelope,
    media_pipeline,  # noqa: F401
)
from tests.chatbot.test_console_turn_endpoint import NOT_SUPPORTED_OUTPUT


def _run(session_factory, monkeypatch, envelope, *, verdict: dict[str, Any]):
    import app.services.chatbot.head.parser as parser_mod
    from unittest.mock import patch as _patch

    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    with _patch.object(parser_mod, "resolve_config", fake_resolve_config), _patch.object(
        parser_mod, "parse", lambda config, user_block: verdict
    ):
        return engine_mod.run_turn(envelope, session_factory=session_factory)


class TestImagePrefixListsEveryEntity:
    """AC-1817: every entity named, order preserved, never a count."""

    def test_prefix_names_every_entity(self, session_factory, seeded, stub_access, media_pipeline, monkeypatch):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {**IMAGE_RESULT, "entities": [{"raw": "A"}, {"raw": "B"}, {"raw": "C"}, {"raw": "D"}]}
        )
        stub_access()

        result = _run(
            session_factory, monkeypatch, _image_envelope(caption="Check stock"), verdict=NOT_SUPPORTED_OUTPUT
        )

        text = (result.reply or {}).get("text") or ""
        assert text.startswith("I read A, B, C and D from that photo."), text
        assert "3 more" not in text and "+1" not in text

    def test_the_persisted_row_carries_the_same_prefixed_text(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        """Review round S1: the prefix is applied to the in-memory `TurnResult` AFTER
        the answering arm's own tail already persisted `chatbot.turns.response` and its
        `sent`/`replied` trace record - so a `_duplicate_result` replay of this turn
        must read the SAME prefixed text back, not the arm's un-prefixed original."""
        from app.models.chatbot_turn import ChatbotTurn

        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(
            {**IMAGE_RESULT, "entities": [{"raw": "A"}, {"raw": "B"}]}
        )
        stub_access()

        result = _run(
            session_factory, monkeypatch, _image_envelope(caption="Check stock"), verdict=NOT_SUPPORTED_OUTPUT
        )

        row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
        assert row is not None
        persisted_text = ((row.response or {}).get("reply") or {}).get("text") or ""
        assert persisted_text.startswith("I read A and B from that photo."), persisted_text
        assert persisted_text == (result.reply or {}).get("text")


class TestTruncatedNoteContinuesThePrefix:
    """AC-1818/AC-1821: the cap comes from `system_settings.media_max_entities`."""

    def test_truncated_note_names_the_cap(self, session_factory, seeded, stub_access, media_pipeline, monkeypatch):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5, media_max_entities=3)
        media_pipeline.set_result(
            {
                **IMAGE_RESULT,
                "entities": [{"raw": "A"}, {"raw": "B"}, {"raw": "C"}],
                "truncated": True,
            }
        )
        stub_access()

        result = _run(
            session_factory, monkeypatch, _image_envelope(caption="Check stock"), verdict=NOT_SUPPORTED_OUTPUT
        )

        text = (result.reply or {}).get("text") or ""
        assert "taken the first 3" in text, text


class TestVoicePrefixHeardCapped:
    """AC-1819: "I heard: <transcript>" capped at 160 chars with an ellipsis."""

    def test_short_transcript_printed_verbatim(self, session_factory, seeded, stub_access, media_pipeline, monkeypatch):
        _seed_media_limit(session_factory, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": "stock for SRTWB1455"})
        stub_access()

        result = _run(session_factory, monkeypatch, _voice_envelope(), verdict=NOT_SUPPORTED_OUTPUT)
        text = (result.reply or {}).get("text") or ""
        assert text.startswith("I heard: stock for SRTWB1455"), text

    def test_long_transcript_is_capped_with_an_ellipsis(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        long_transcript = "please check stock for " + ", ".join(f"CODE{i:03d}" for i in range(30))
        assert len(long_transcript) > 160
        _seed_media_limit(session_factory, modality="voice")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result({"transcript": long_transcript})
        stub_access()

        result = _run(session_factory, monkeypatch, _voice_envelope(), verdict=NOT_SUPPORTED_OUTPUT)
        text = (result.reply or {}).get("text") or ""
        first_line = text.split("\n", 1)[0]
        assert first_line.startswith("I heard: ")
        assert first_line.endswith("..."), first_line
        assert len(first_line) <= len("I heard: ") + 160 + 3


class TestPrefixExactlyOnce:
    """AC-1820."""

    def test_prefix_appears_once_whichever_arm_answered(
        self, session_factory, seeded, stub_access, media_pipeline, monkeypatch
    ):
        _seed_media_limit(session_factory, modality="image")
        _seed_settings(session_factory, media_sync_wait_seconds=5)
        media_pipeline.set_result(IMAGE_RESULT)
        stub_access()

        result = _run(
            session_factory, monkeypatch, _image_envelope(caption="Check stock"), verdict=NOT_SUPPORTED_OUTPUT
        )
        text = (result.reply or {}).get("text") or ""
        assert text.count("I read") == 1, text

    def test_a_plain_text_turn_never_gets_the_prefix(self, session_factory, seeded, stub_access, monkeypatch):
        result = _run(session_factory, monkeypatch, _envelope(), verdict=NOT_SUPPORTED_OUTPUT)
        text = (result.reply or {}).get("text") or ""
        assert "I read" not in text
        assert "I heard" not in text
