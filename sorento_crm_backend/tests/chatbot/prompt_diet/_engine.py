"""Engine-level harness for the PROMPT-DIET tests. Not a test file.

The provider seam is `app.services.chatbot.llm_call.chat`, so the REAL `parser.parse` runs
and builds the messages; only `parser.resolve_config` is replaced (it returns a config whose
system prompt is the STATIC stand-in below, so "the static prompt" is recognisable).

Pinned names the engine must honour (module attribute lookups, so monkeypatching works):
  * `prompt_diet.embed_text(db, text) -> list[float]`
  * `prompt_diet.nearest_examples(db, vector, k=5) -> list[Example]`
No live LLM or embedding call is possible from here.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod
from app.services.llm_provider import ChatResult
from tests.chatbot.test_engine import _parser_output

STATIC_PROMPT = "ZZT STATIC V50 STAND-IN PROMPT, byte for byte"
STATIC_VERSION = 7


class DietHarness:
    def __init__(self, monkeypatch, emissions: list[dict[str, Any]], usages: list[tuple[int, int, int]] | None = None):
        self.mp = monkeypatch
        self.emissions = emissions
        self.usages = usages or [(1000, 10, 0)]
        self.parser_calls: list[dict[str, Any]] = []
        self.embed_calls: list[str] = []
        self._install()

    # -- provider seam ------------------------------------------------------ #
    def _install(self) -> None:
        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt=STATIC_PROMPT,
                prompt_version=STATIC_VERSION,
                provider="openai",
                model="gpt-test",
                api_key="sk-test",
            )

        def fake_chat(provider, key, model, messages, **kwargs):
            if kwargs.get("json_schema_name") != parser_mod.PARSE_OUTPUT_SCHEMA_NAME:
                return ChatResult(content="{}", prompt_tokens=0, completion_tokens=0, total_tokens=0)
            i = len(self.parser_calls)
            self.parser_calls.append({"messages": messages, **kwargs})
            prompt, completion, cached = self.usages[min(i, len(self.usages) - 1)]
            emission = self.emissions[min(i, len(self.emissions) - 1)]
            return ChatResult(
                content=json.dumps(emission),
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cached_tokens=cached,
            )

        self.mp.setattr(parser_mod, "resolve_config", fake_resolve_config)
        self.mp.setattr(llm_call, "chat", fake_chat)

    # -- diet seams --------------------------------------------------------- #
    def diet_on(
        self,
        *,
        example_areas: list[str] | tuple[str, ...] = (),
        embed_error: Exception | None = None,
        nearest_error: Exception | None = None,
    ) -> None:
        from app.config import settings
        from app.services.chatbot.head import prompt_diet

        self.mp.setattr(settings, "chatbot_prompt_diet", True)
        examples = [
            prompt_diet.Example(
                area=area,
                message=f"Test example {i} for SRT-TEST-{i}",
                context=None,
                expected_output={"user_goal": f"example goal {i}"},
            )
            for i, area in enumerate(example_areas)
        ]
        self.examples = examples

        def fake_embed(db, text):
            self.embed_calls.append(text)
            if embed_error is not None:
                raise embed_error
            return [0.0] * 1536

        def fake_nearest(db, vector, k=5):
            if nearest_error is not None:
                raise nearest_error
            return list(examples)

        self.mp.setattr(prompt_diet, "embed_text", fake_embed)
        self.mp.setattr(prompt_diet, "nearest_examples", fake_nearest)

    def watch_embed_only(self) -> None:
        """Switch OFF or bypassed: record embed calls so a test can assert there are none."""
        from app.services.chatbot.head import prompt_diet

        def fake_embed(db, text):
            self.embed_calls.append(text)
            return [0.0] * 1536

        self.mp.setattr(prompt_diet, "embed_text", fake_embed)

    # -- reading results ---------------------------------------------------- #
    @staticmethod
    def system_of(call: dict[str, Any]) -> str:
        return call["messages"][0]["content"]

    @staticmethod
    def understood(session_factory, turn_id: str) -> dict[str, Any]:
        from tests.chatbot.test_engine import _turn_row

        rows = [r for r in _turn_row(session_factory, turn_id).trace if r.get("stage") == "understood"]
        assert len(rows) == 1, rows
        return rows[0]


def emission(**overrides: Any) -> dict[str, Any]:
    """A well-formed emission. `_parser_output` lacks `asks` and `topic_reset` (a known gap:
    the declared-keys check rejects it without them), so they are defaulted here."""
    base: dict[str, Any] = {"asks": [], "topic_reset": False}
    base.update(overrides)
    return _parser_output(**base)
