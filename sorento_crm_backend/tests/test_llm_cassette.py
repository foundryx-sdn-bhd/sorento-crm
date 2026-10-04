"""LLM-TEST-COST: record/replay cassettes at the `llm_provider` seam, `cached_tokens`
on the parser's usage and trace, and the optional base-URL / model override.

Red first (PLAN-llm-test-cost-4oct.md). No network: every "live" call here is a stub
client returning a REAL `openai` `ChatCompletion`, so a recorded cassette round-trips
through the same pydantic type production sees.
"""
from __future__ import annotations

import os
import stat
import types
from pathlib import Path
from typing import Any

import pytest
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion

from app.services import llm_cassette
from app.services import llm_provider
from app.services.llm_provider import OpenAIProvider

SECRET = "sk-test-SECRET-never-in-a-file"
PII_TEXT = "Mr Fake Person +60 12-000 0000 asks for stock of ZZT-1"


def _completion(content: str = '{"ok": true}', cached: int | None = 0) -> ChatCompletion:
    usage: dict[str, Any] = {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-fake",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": usage,
        }
    )


class _Client:
    """A stand-in for `openai.OpenAI`: counts calls, can be told to explode."""

    def __init__(self, completion: ChatCompletion | None = None, explode: bool = False) -> None:
        self.calls: list[dict] = []
        self.embed_calls: list[dict] = []
        outer = self

        class _Completions:
            def create(inner, **kwargs):
                outer.calls.append(kwargs)
                if explode:
                    raise AssertionError("a live call was made")
                return completion or _completion()

        class _Embeddings:
            def create(inner, **kwargs):
                outer.embed_calls.append(kwargs)
                if explode:
                    raise AssertionError("a live call was made")
                return CreateEmbeddingResponse.model_validate(
                    {
                        "object": "list",
                        "model": kwargs["model"],
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.5]}],
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    }
                )

        self.chat = types.SimpleNamespace(completions=_Completions())
        self.embeddings = _Embeddings()


_ENV = ("LLM_CASSETTE_MODE", "LLM_CASSETTE_DIR", "LLM_BASE_URL", "LLM_MODEL_OVERRIDE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # Restored by hand as well: `configure_runner` writes `os.environ` directly, which
    # monkeypatch cannot undo for a variable that was unset when the test began, and a
    # leaked `replay` would turn every later test in this worker into a cassette miss.
    saved = {name: os.environ.get(name) for name in _ENV}
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    llm_provider._OPENAI_REJECTED_PARAMS.clear()
    yield
    llm_provider._OPENAI_REJECTED_PARAMS.clear()
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture
def cassette_dir(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "cassettes"
    monkeypatch.setenv("LLM_CASSETTE_DIR", str(path))
    return path


def _provider(monkeypatch, client: _Client) -> OpenAIProvider:
    provider = OpenAIProvider(SECRET, default_model="gpt-fake")
    monkeypatch.setattr(provider, "_client", lambda **_kw: client)
    return provider


MESSAGES = [
    {"role": "system", "content": "static system prompt"},
    {"role": "user", "content": PII_TEXT},
]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def _chat(provider: OpenAIProvider, **overrides: Any):
    kwargs: dict[str, Any] = {
        "model": "gpt-fake",
        "temperature": 0.0,
        "max_tokens": 50,
        "json_schema": SCHEMA,
        "json_schema_name": "parse",
    }
    kwargs.update(overrides)
    messages = kwargs.pop("messages", MESSAGES)
    return provider.chat(messages, **kwargs)


def _files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file()) if path.exists() else []


# --------------------------------------------------------------------------- #
# Mode switch
# --------------------------------------------------------------------------- #


class TestModeOff:
    def test_unset_mode_calls_live_every_time_and_writes_nothing(self, monkeypatch, cassette_dir):
        client = _Client()
        provider = _provider(monkeypatch, client)
        _chat(provider)
        _chat(provider)
        assert len(client.calls) == 2
        assert _files(cassette_dir) == []

    def test_explicit_off_is_the_same_as_unset(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "off")
        client = _Client()
        _chat(_provider(monkeypatch, client))
        assert len(client.calls) == 1
        assert _files(cassette_dir) == []

    def test_an_unknown_mode_is_refused_not_ignored(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replya")
        client = _Client()
        with pytest.raises(ValueError, match="LLM_CASSETTE_MODE"):
            _chat(_provider(monkeypatch, client))
        assert client.calls == []


class TestRecord:
    def test_record_calls_live_once_and_writes_one_private_file(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        client = _Client()
        result = _chat(_provider(monkeypatch, client))
        assert len(client.calls) == 1
        assert result.content == '{"ok": true}'
        files = _files(cassette_dir)
        assert len(files) == 1
        mode = stat.S_IMODE(files[0].stat().st_mode)
        assert mode == 0o600, oct(mode)

    def test_directories_it_creates_are_private(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        _chat(_provider(monkeypatch, _Client()))
        for directory in (cassette_dir, _files(cassette_dir)[0].parent):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700, directory

    def test_the_file_carries_no_secret_and_no_request_text(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        _chat(_provider(monkeypatch, _Client()))
        body = _files(cassette_dir)[0].read_text(encoding="utf-8")
        assert SECRET not in body
        assert PII_TEXT not in body
        assert "static system prompt" not in body

    def test_a_response_echoing_the_api_key_is_never_written(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        client = _Client(completion=_completion(content=f"leak {SECRET}"))
        with pytest.raises(llm_cassette.CassetteError):
            _chat(_provider(monkeypatch, client))
        assert _files(cassette_dir) == []


class TestReplay:
    def test_replay_returns_the_recording_without_a_live_call(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        recorded = _chat(_provider(monkeypatch, _Client(completion=_completion(cached=768))))

        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        live = _Client(explode=True)
        replayed = _chat(_provider(monkeypatch, live))

        assert live.calls == []
        assert replayed.content == recorded.content
        assert replayed.prompt_tokens == recorded.prompt_tokens == 1000
        assert replayed.cached_tokens == 768
        # `raw` is the provider-native object the agent loop reads tool calls back from.
        assert isinstance(replayed.raw, ChatCompletion)
        assert replayed.raw.choices[0].finish_reason == "stop"

    def test_a_miss_names_the_key_and_never_calls_live(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        live = _Client(explode=True)
        with pytest.raises(llm_cassette.CassetteMiss) as excinfo:
            _chat(_provider(monkeypatch, live))
        assert live.calls == []
        expected_key = llm_cassette.chat_key(
            {
                "model": "gpt-fake",
                "messages": MESSAGES,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "parse", "schema": SCHEMA, "strict": True},
                },
            }
        )
        assert expected_key in str(excinfo.value)
        assert "LLM_CASSETTE_MODE=record" in str(excinfo.value)

    def test_replay_needs_no_api_key(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        _chat(_provider(monkeypatch, _Client()))
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        provider = OpenAIProvider("", default_model="gpt-fake")
        monkeypatch.setattr(provider, "_client", lambda **_kw: (_ for _ in ()).throw(AssertionError("client built")))
        assert _chat(provider).content == '{"ok": true}'

    def test_embeddings_replay_too(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        assert _provider(monkeypatch, _Client()).embed("hello") == [0.25, 0.5]
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        live = _Client(explode=True)
        assert _provider(monkeypatch, live).embed("hello") == [0.25, 0.5]
        with pytest.raises(llm_cassette.CassetteMiss):
            _provider(monkeypatch, live).embed("a different text")
        assert live.embed_calls == []


class TestKey:
    BASE = {"model": "m", "messages": MESSAGES, "response_format": {"type": "json_object"}}

    def test_model_messages_schema_and_tools_each_change_the_key(self):
        base = llm_cassette.chat_key(self.BASE)
        assert llm_cassette.chat_key({**self.BASE, "model": "m2"}) != base
        assert llm_cassette.chat_key({**self.BASE, "messages": MESSAGES[:1]}) != base
        assert llm_cassette.chat_key({**self.BASE, "response_format": {"type": "text"}}) != base
        assert llm_cassette.chat_key({**self.BASE, "tools": [{"type": "function"}]}) != base

    def test_temperature_and_ceiling_do_not(self):
        base = llm_cassette.chat_key(self.BASE)
        assert llm_cassette.chat_key({**self.BASE, "temperature": 1.0, "max_tokens": 9}) == base

    def test_dict_order_does_not(self):
        reordered = {"response_format": self.BASE["response_format"], "messages": MESSAGES, "model": "m"}
        assert llm_cassette.chat_key(reordered) == llm_cassette.chat_key(self.BASE)


class TestStorage:
    def test_mode_label_never_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replya")
        assert llm_cassette.mode_label().startswith("invalid")

    def test_default_dir_is_outside_the_repository(self, monkeypatch):
        repo_root = Path(__file__).resolve().parents[2]
        default = llm_cassette.cassette_dir()
        assert repo_root not in default.resolve().parents

    def test_a_dir_inside_the_repository_is_refused(self, monkeypatch, tmp_path):
        # A fake checkout, so a regressed guard writes into tmp_path, never into this repo.
        (tmp_path / "checkout" / ".git").mkdir(parents=True)
        inside = tmp_path / "checkout" / "tests" / "llm_cassettes"
        monkeypatch.setenv("LLM_CASSETTE_DIR", str(inside))
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        with pytest.raises(llm_cassette.CassetteError, match="inside the repository"):
            _chat(_provider(monkeypatch, _Client()))
        assert not inside.exists()


class TestOtherProviders:
    def test_replay_refuses_a_provider_it_does_not_cover(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        provider = llm_provider.AnthropicProvider("k", default_model="claude-x")
        with pytest.raises(llm_cassette.CassetteError, match="anthropic"):
            provider.chat([{"role": "user", "content": "hi"}])
        provider = llm_provider.GeminiProvider("k", default_model="gemini-x")
        with pytest.raises(llm_cassette.CassetteError, match="gemini"):
            provider.chat([{"role": "user", "content": "hi"}])


# --------------------------------------------------------------------------- #
# cached_tokens
# --------------------------------------------------------------------------- #


class TestCachedTokens:
    def test_openai_reads_prompt_tokens_details(self, monkeypatch):
        result = _chat(_provider(monkeypatch, _Client(completion=_completion(cached=31744))))
        assert result.cached_tokens == 31744

    def test_absent_details_read_as_zero(self, monkeypatch):
        result = _chat(_provider(monkeypatch, _Client(completion=_completion(cached=None))))
        assert result.cached_tokens == 0

# --------------------------------------------------------------------------- #
# LLM_BASE_URL / LLM_MODEL_OVERRIDE
# --------------------------------------------------------------------------- #


class TestOverrides:
    def _capture_ctor(self, monkeypatch) -> list[dict]:
        import openai

        built: list[dict] = []

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                built.append(kwargs)
                self.chat = _Client().chat

        monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
        return built

    def test_unset_leaves_the_client_and_model_alone(self, monkeypatch):
        built = self._capture_ctor(monkeypatch)
        OpenAIProvider(SECRET, default_model="gpt-fake").chat(MESSAGES, model="gpt-fake")
        assert "base_url" not in built[0]
        assert built[0]["api_key"] == SECRET
        client = _Client()
        _chat(_provider(monkeypatch, client), model="gpt-asked")
        assert client.calls[0]["model"] == "gpt-asked"

    def test_base_url_reaches_the_client_when_set(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://cheap-model.invalid/v1")
        built = self._capture_ctor(monkeypatch)
        OpenAIProvider(SECRET, default_model="gpt-fake").chat(MESSAGES)
        assert built[0]["base_url"] == "https://cheap-model.invalid/v1"

    def test_model_override_replaces_the_model_on_the_wire(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL_OVERRIDE", "cheap-model-1")
        client = _Client()
        _chat(_provider(monkeypatch, client), model="gpt-fake")
        assert client.calls[0]["model"] == "cheap-model-1"


# --------------------------------------------------------------------------- #
# Runner default: replay unless --live
# --------------------------------------------------------------------------- #


class TestRunnerMode:
    def test_runner_defaults_to_replay(self, monkeypatch):
        assert llm_cassette.runner_mode(live=False, record=False) == "replay"
        assert llm_cassette.runner_mode(live=True, record=False) == "off"
        assert llm_cassette.runner_mode(live=False, record=True) == "record"

    def test_live_and_record_together_are_refused(self):
        with pytest.raises(ValueError):
            llm_cassette.runner_mode(live=True, record=True)


class TestConfigureRunner:
    def test_no_flag_means_replay(self, monkeypatch):
        assert llm_cassette.configure_runner([]) == "replay"
        assert os.environ["LLM_CASSETTE_MODE"] == "replay"

    def test_an_exported_mode_is_respected_without_a_flag(self, monkeypatch):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        assert llm_cassette.configure_runner(["--port", "8001"]) == "record"

    def test_flags_win(self, monkeypatch):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        assert llm_cassette.configure_runner(["--live"]) == "off"
        assert llm_cassette.configure_runner(["--record"]) == "record"


def _console_check():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "chatbot_console_check.py"
    spec = importlib.util.spec_from_file_location("chatbot_console_check_llm_cost", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _body() -> dict:
    """A `TurnResponse`-shaped 200 body: a turn id, no inline trace."""
    return {"turn_id": "t-1", "branch_kind": "business", "reply": {"text": "x"}}


def _check_with_row(facts: dict | None, **record: Any):
    """The console check with its row read stubbed: the turn's stored trace."""
    check = _console_check()
    understood = [{"stage": "understood", "facts": facts, **record}] if facts is not None else []
    trace = [{"stage": "received"}] + understood
    check._row_trace = lambda turn_id: trace if turn_id == "t-1" else []
    return check


class TestConsoleCheckRefusal:
    def test_a_live_parse_stops_the_run_without_live(self):
        check = _check_with_row({"llm_cassette": "off"})
        refusal = check._cassette_refusal(_body(), live=False)
        assert refusal and "--live" in refusal

    def test_a_backend_without_the_fact_counts_as_live(self):
        assert _check_with_row({})._cassette_refusal(_body(), live=False)

    def test_replay_bypass_and_live_flag_pass(self):
        assert _check_with_row({"llm_cassette": "replay"})._cassette_refusal(_body(), live=False) is None
        assert _check_with_row({"parser_bypassed": True})._cassette_refusal(_body(), live=False) is None
        assert _check_with_row({"llm_cassette": "off"})._cassette_refusal(_body(), live=True) is None
        assert _check_with_row({"llm_cassette": "off"})._cassette_refusal({"_http_error": "HTTP 500"}, live=False) is None
        # A turn that failed before the parse has no `understood` stage: nothing was spent.
        assert _check_with_row(None)._cassette_refusal(_body(), live=False) is None

    def test_a_turn_row_this_script_cannot_see_stops_the_run(self):
        check = _console_check()
        check._row_trace = lambda turn_id: []
        refusal = check._cassette_refusal(_body(), live=False)
        assert refusal and "cannot confirm replay" in refusal

    def test_a_replay_miss_stops_with_the_miss_not_a_spend_claim(self):
        check = _check_with_row({"llm_cassette": "replay"}, status="failed", error="LLM cassette miss: key abc")
        refusal = check._cassette_refusal(_body(), live=False)
        assert refusal and "cassette miss" in refusal and "spends" not in refusal

    def test_post_with_pacing_raises_system_exit(self, monkeypatch):
        check = _check_with_row({"llm_cassette": "off"})
        monkeypatch.setattr(check, "_post", lambda *a, **k: _body())
        with pytest.raises(SystemExit):
            check._post_with_pacing(None, "u", "k", {}, 1.0, live=False)
        assert check._post_with_pacing(None, "u", "k", {}, 1.0, live=True)


class TestHardening:
    """Security review round 1 (S1-S4, N1)."""

    def test_a_dir_inside_any_git_checkout_is_refused(self, monkeypatch, tmp_path):
        other_clone = tmp_path / "another-clone"
        (other_clone / ".git").mkdir(parents=True)
        monkeypatch.setenv("LLM_CASSETTE_DIR", str(other_clone / "cassettes"))
        with pytest.raises(llm_cassette.CassetteError, match="inside the repository"):
            llm_cassette.cassette_dir()

    def test_a_worktree_git_file_counts(self, monkeypatch, tmp_path):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /elsewhere\n")
        monkeypatch.setenv("LLM_CASSETTE_DIR", str(worktree / "c"))
        with pytest.raises(llm_cassette.CassetteError):
            llm_cassette.cassette_dir()

    def test_production_refuses_any_mode_but_off(self, monkeypatch):
        from app.config import settings

        for environment in ("production", "staging", "prod"):
            monkeypatch.setattr(settings, "environment", environment)
            monkeypatch.setenv("LLM_CASSETTE_MODE", "off")
            assert llm_cassette.mode() == "off"
            for value in ("record", "replay"):
                monkeypatch.setenv("LLM_CASSETTE_MODE", value)
                with pytest.raises(llm_cassette.CassetteError, match="refused"):
                    llm_cassette.mode()

    def test_an_existing_dir_open_to_others_is_refused(self, monkeypatch, cassette_dir):
        cassette_dir.mkdir(mode=0o777)
        cassette_dir.chmod(0o777)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        with pytest.raises(llm_cassette.CassetteError, match="0700"):
            _chat(_provider(monkeypatch, _Client()))

    def test_plain_http_base_url_is_refused_off_localhost(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://cheap-model.invalid/v1")
        with pytest.raises(ValueError, match="https"):
            OpenAIProvider(SECRET)._client()
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        TestOverrides()._capture_ctor(monkeypatch)
        OpenAIProvider(SECRET)._client()

    def test_direct_http_embeddings_record_and_replay(self, monkeypatch, cassette_dir):
        from app.api.v1.external import rag
        from app.config import settings
        from app.services import embedding_worker

        monkeypatch.setattr(settings, "openai_api_key", SECRET)
        posted: list[list[str]] = []

        def fake_post(chunks):
            posted.append(chunks)
            return [[0.5, float(len(c))] for c in chunks]

        monkeypatch.setattr(embedding_worker, "_post_embeddings", fake_post)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        assert embedding_worker._embed_text_chunks(["abc"]) == [[0.5, 3.0]]
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        assert embedding_worker._embed_text_chunks(["abc"]) == [[0.5, 3.0]]
        assert rag._embed_query("abc") == [0.5, 3.0]
        assert posted == [["abc"]]
        with pytest.raises(llm_cassette.CassetteMiss):
            embedding_worker._embed_text_chunks(["never recorded"])
        assert posted == [["abc"]]

    def test_replay_refuses_a_cassette_dir_open_to_others(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        _chat(_provider(monkeypatch, _Client()))
        cassette_dir.chmod(0o777)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        with pytest.raises(llm_cassette.CassetteError, match="0700"):
            _chat(_provider(monkeypatch, _Client(explode=True)))

    def test_market_research_refuses_in_replay(self, monkeypatch):
        import sys

        from app.services.scm import market_research_service

        built: list[str] = []
        fake = types.SimpleNamespace(Anthropic=lambda **kw: built.append("client") or (_ for _ in ()).throw(AssertionError("network")))
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        with pytest.raises(llm_cassette.CassetteError):
            market_research_service._anthropic_web_search("prompt", "k")
        assert built == []

    def test_direct_http_calls_refuse_in_replay(self, monkeypatch):
        import httpx

        from app.services.media_extract import transcribe

        # A regressed guard must fail here, never reach the network.
        def no_network(*args, **kwargs):
            raise AssertionError("network")

        monkeypatch.setattr(httpx, "post", no_network)
        monkeypatch.setattr(httpx, "Client", no_network)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        with pytest.raises(llm_cassette.CassetteError):
            transcribe._post_transcription(
                b"", filename="f.ogg", mime_type="audio/ogg", fields={}, api_key="k", timeout=1.0
            )
