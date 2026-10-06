"""RESOLVE-SEMANTIC-FALLBACK: the code reader (`app/services/resolver_code_reader.py`),
the ONE model call the shared resolver makes about product tokens that missed every code
tier. Owner bounds (PR #1511 card, Q3 / Q5): one call per resolve request covering every
missed product token, at most 5 tokens, a 3 s budget, one attempt, no retry; the model
sees the typed text and the token only (no candidate list); a value not inside the typed
text is refused; any failure is a miss with its reason, never a failed call; usage lands
in `ai_assistant_usage_logs` unless the call is a dry run.

The provider is stubbed at `_resolve_provider` (the same seam `product_spec_understanding`
uses): no live call, no key. Fake ZZT codes only.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import resolver_code_reader as reader_mod
from app.services.resolver_code_reader import CodeReading, guard, per_request

from ._pg_fixture import blank_session


class FakeProvider:
    def __init__(self, content: Any, *, raises: Exception | None = None):
        self.content = content if isinstance(content, str) else json.dumps(content)
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.raises is not None:
            raise self.raises
        from app.services.llm_provider import ChatResult

        return ChatResult(content=self.content, prompt_tokens=120, completion_tokens=14, total_tokens=134)


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _provider(monkeypatch, provider):
    monkeypatch.setattr(
        reader_mod, "_resolve_provider", lambda db: (provider, "openai", "gpt-test")
    )
    return provider


# --------------------------------------------------------------------------- #
# The guard: a value must be read out of the typed text
# --------------------------------------------------------------------------- #


def test_guard_accepts_a_code_inside_the_typed_text_whatever_the_separators():
    assert guard(CodeReading("code", "ZZTWT5608"), "TAP ZZTWT5608", "TAPZZTWT5608").kind == "code"
    assert guard(CodeReading("code", "zztwt5608"), "TAP ZZTWT5608", "TAPZZTWT5608").kind == "code"
    assert guard(CodeReading("code", "ZZTWC8066-SS"), "WC ZZTWC8066SS", "WCZZTWC8066SS").kind == "code"
    assert guard(CodeReading("family", "8066"), "toilet 8066", "TOILET8066").kind == "family"


def test_guard_refuses_a_value_not_inside_the_typed_text():
    out = guard(CodeReading("code", "ZZTWT5608"), "zztw5608", "zztw5608")
    assert out.kind == "none"
    assert out.value is None
    assert out.reason == reader_mod.REASON_NOT_IN_TYPED_TEXT


@pytest.mark.parametrize("value", ["SRT_5608", "SRT%5608", "srt5608%", "ZZTWT5608;", "ZZT.WT5608"])
def test_guard_refuses_a_value_with_characters_outside_letters_digits_space_hyphen(value):
    """Security review finding 1: the fold let `SRT_5608` through for "SRT 5608" and the
    probe's `ilike` then read `_` as a wildcard. Letters, digits, spaces and hyphens only;
    the two separators are what the probes already fold away."""
    out = guard(CodeReading("family", value), "TAP SRT 5608 ZZTWT5608", "TAPSRT5608ZZTWT5608")
    assert out.kind == "none"
    assert out.reason == reader_mod.REASON_BAD_CHARACTERS


def test_guard_keeps_spaces_and_hyphens_the_dealer_typed():
    assert guard(CodeReading("code", "Srt 364-hp"), "Srt 364-hp x 1", "Srt364hp") == CodeReading("code", "Srt 364-hp")


def test_guard_refuses_an_empty_value_and_an_unknown_kind():
    assert guard(CodeReading("code", ""), "TAP ZZTWT5608", "TAPZZTWT5608").kind == "none"
    assert guard(CodeReading("code", None), "TAP ZZTWT5608", "TAPZZTWT5608").kind == "none"
    assert guard(CodeReading("maybe", "ZZTWT5608"), "TAP ZZTWT5608", "TAPZZTWT5608").kind == "none"


def test_guard_falls_back_to_the_token_when_no_typed_text_was_sent():
    """No `raw_tokens`: the folded token is the only text, so only the whole token can be
    read out of it (a part has no word edge inside a folded run)."""
    assert guard(CodeReading("code", "TAPZZTWT5608"), None, "TAPZZTWT5608").kind == "code"
    assert guard(CodeReading("code", "ZZTWT5608"), None, "TAPZZTWT5608").kind == "none"
    assert guard(CodeReading("code", "ZZTWT5609"), None, "TAPZZTWT5608").kind == "none"


@pytest.mark.parametrize(
    ("kind", "value", "typed"),
    [
        ("code", "SRTWC8066", "WC SRTWC8066SS"),
        ("family", "5699", "SRTWT5699"),
        ("code", "WT56", "TAP SRTWT5608"),
        ("code", "SRTWT5608", "TAP SRTWT5608X"),
        # A hyphen is part of the code, not a word edge (reviewer, third pass): the catalogue
        # folds "SRTWC8066-SS" and "SRTWC8066SS" to one code, so a cut at the hyphen is the
        # same cut as a cut inside the run.
        ("code", "SRTWC8066", "WC SRTWC8066-SS"),
        ("code", "SRTWC286", "SRTWC286-SH x 2"),
        ("family", "364", "Srt 364-hp"),
        ("family", "SH", "SRTWC286-SH"),
        # A hyphen with spaces beside it, or doubled, is the same hyphen: the catalogue folds
        # every `[-\s]+` run ("cgb9032b- new" IS CGB9032B-NEW, its own docstring example), so
        # a cut at any of these is the cut of the third pass (reviewer, fourth pass).
        ("code", "cgb9032b", "TAP cgb9032b- new"),
        ("code", "CGB9032B", "TAP CGB9032B -NEW"),
        ("code", "SRTWC8066", "WC SRTWC8066--SS"),
        ("code", "SRTWC8066", "WC SRTWC8066 - SS"),
        ("code", "srtkt71", "srtkt71 -ss x2"),
        ("family", "SS", "SRTWC8066--SS"),
        ("code", "SRTWC8066-SS", "SRTWC8066-SS-150"),
        ("code", "8066-SS", "SRTWC8066-SS"),
    ],
)
def test_guard_refuses_a_value_cut_out_of_the_middle_of_a_typed_code(kind, value, typed):
    """Reviewer blocker (6 Oct 2026): "SRTWC8066" read out of "WC SRTWC8066SS" resolves the
    BASE product instead of the variant the dealer typed, and "5699" out of "SRTWT5699"
    opens another family's picker. A value must occur as a whole word run."""
    out = guard(CodeReading(kind, value), typed, typed.replace(" ", ""))
    assert out.kind == "none"
    assert out.reason == reader_mod.REASON_NOT_IN_TYPED_TEXT


@pytest.mark.parametrize(
    ("kind", "value", "typed"),
    [
        ("code", "SRTWT5608", "TAP SRTWT5608"),
        ("code", "ZZTWC8066-SS", "WC ZZTWC8066SS"),
        ("code", "Srt 364-hp", "Srt 364-hp x 1"),
        ("family", "8066", "toilet 8066"),
        ("code", "SRTWT5608", "龙头SRTWT5608"),
        ("code", "srtwt5608", "chrome tap SRTWT5608 got stock?"),
        # A trailing hyphen with nothing after it, and a value that sits twice in the
        # text, once cut and once whole: the whole occurrence counts.
        ("code", "SRTWC8066", "SRTWC8066-"),
        ("code", "SRTWC8066", "SRTWC8066-SS or SRTWC8066 ?"),
    ],
)
def test_guard_accepts_the_owners_examples_as_whole_word_runs(kind, value, typed):
    assert guard(CodeReading(kind, value), typed, typed.replace(" ", "")).kind == kind


def test_a_failed_config_read_leaves_the_session_usable_and_pending_work_intact(db, monkeypatch):
    """`_resolve_provider` reads the assistant config under a SAVEPOINT (reviewer, third
    pass, finding 3): a failed SELECT aborts a Postgres transaction and `Session.is_active`
    stays True after it, so without the savepoint the resolver's next query on the turn's
    session would raise and the caller's pending work would be lost with the rollback."""
    import uuid

    from sqlalchemy import text

    from app.models.order import Customer
    from app.services.company_scope import DEFAULT_COMPANY_ID

    marker = Customer(
        id=str(uuid.uuid4()), customer_code="ZZT-SAVEPOINT", customer_name="ZZT Savepoint",
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(marker)
    db.flush()

    real_query = db.query

    def broken_query(*args, **kwargs):
        # A real SQL failure inside the read, not a Python one: only that aborts the transaction.
        return db.execute(text('SELECT 1 FROM "zzt_no_such_table_for_this_test"'))

    monkeypatch.setattr(db, "query", broken_query)
    provider, _name, _model = reader_mod._resolve_provider(db)
    monkeypatch.setattr(db, "query", real_query)

    assert provider is None
    assert db.execute(text("SELECT 1")).scalar() == 1, "the session is still usable"
    assert db.query(Customer).filter(Customer.customer_code == "ZZT-SAVEPOINT").one().id == marker.id


def test_guard_leaves_none_and_error_alone():
    assert guard(CodeReading("none", None), "x", "x") == CodeReading("none", None)
    err = CodeReading("error", None, reason="timeout")
    assert guard(err, "x", "x") == err


# --------------------------------------------------------------------------- #
# The call: what the model sees, how long it may take
# --------------------------------------------------------------------------- #


def test_the_model_sees_the_message_and_the_tokens_and_nothing_else(db, monkeypatch):
    provider = _provider(monkeypatch, FakeProvider({"readings": [{"token": "TAPZZTWT5608", "kind": "code", "value": "ZZTWT5608"}]}))

    out = reader_mod.read_codes(
        db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="TAP ZZTWT5608 x 5", user_id=None, dry_run=True
    )

    assert out == {"TAPZZTWT5608": CodeReading("code", "ZZTWT5608")}
    call = provider.calls[0]
    user = json.loads(call["messages"][1]["content"])
    assert user == {"message": "TAP ZZTWT5608 x 5", "tokens": [{"token": "TAPZZTWT5608", "typed": "TAP ZZTWT5608"}]}
    assert "candidates" not in user and "near" not in json.dumps(user).lower()
    assert call["timeout"] == reader_mod.BUDGET_SECONDS == 3.0
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["role"] == "system"


def test_the_system_prompt_is_the_registry_key_with_its_fallback_text(db):
    from app.services.ai_prompt_registry import PROMPT_KEYS, get_prompt

    assert reader_mod.PROMPT_KEY in PROMPT_KEYS
    text = get_prompt(db, reader_mod.PROMPT_KEY).text
    assert "code" in text and "family" in text and "none" in text
    assert "never" in text.lower()


def test_the_answer_is_guarded_per_token(db, monkeypatch):
    _provider(monkeypatch, FakeProvider({"readings": [
        {"token": "TAPZZTWT5608", "kind": "code", "value": "ZZTWT5608"},
        {"token": "zztw5608", "kind": "code", "value": "ZZTWT5608"},
        {"token": "TOILET8066", "kind": "family", "value": "8066"},
        {"token": "BIDET", "kind": "none", "value": None},
        {"token": "NOT-ASKED", "kind": "code", "value": "X1"},
    ]}))
    missed = [("TAPZZTWT5608", "TAP ZZTWT5608"), ("zztw5608", "zztw5608"), ("TOILET8066", "toilet 8066"), ("BIDET", "bidet")]

    out = reader_mod.read_codes(db, missed, message_text="", user_id=None, dry_run=True)

    assert out["TAPZZTWT5608"] == CodeReading("code", "ZZTWT5608")
    assert out["zztw5608"].kind == "none" and out["zztw5608"].reason == reader_mod.REASON_NOT_IN_TYPED_TEXT
    assert out["TOILET8066"] == CodeReading("family", "8066")
    assert out["BIDET"] == CodeReading("none", None)
    assert "NOT-ASKED" not in out, "a token the caller never asked about is dropped"


def test_the_text_sent_to_the_model_is_capped(db, monkeypatch):
    """Security review (final head, finding 1): the feature is on by default for every
    API-key caller, so an unbounded `query` would bill a maximum-size input per request."""
    provider = _provider(monkeypatch, FakeProvider({"readings": []}))
    long_message = "x" * 10_000
    long_typed = "y" * 1_000
    reader_mod.read_codes(db, [("T", long_typed)], message_text=long_message, user_id=None, dry_run=True)
    user = json.loads(provider.calls[0]["messages"][1]["content"])
    assert len(user["message"]) == reader_mod.MESSAGE_CHARS == 2000
    assert len(user["tokens"][0]["typed"]) == reader_mod.TYPED_CHARS == 200


def test_a_kind_the_model_invents_is_none_never_the_codes_own_verdicts(db, monkeypatch):
    """`error` and `not_asked` are the code's verdicts; a model may not set them (or
    anything else outside code / family / none) and smuggle a value through."""
    _provider(monkeypatch, FakeProvider({"readings": [
        {"token": "A", "kind": "error", "value": "leak"},
        {"token": "B", "kind": "not_asked", "value": "leak"},
        {"token": "C", "kind": "maybe", "value": "C"},
    ]}))
    out = reader_mod.read_codes(db, [("A", "a"), ("B", "b"), ("C", "c")], message_text="", user_id=None, dry_run=True)
    assert out["A"] == CodeReading("none", None)
    assert out["B"] == CodeReading("none", None)
    assert out["C"] == CodeReading("none", None)


def test_a_token_the_model_left_out_is_none(db, monkeypatch):
    _provider(monkeypatch, FakeProvider({"readings": []}))
    out = reader_mod.read_codes(db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="", user_id=None, dry_run=True)
    assert out["TAPZZTWT5608"].kind == "none"
    assert out["TAPZZTWT5608"].reason == reader_mod.REASON_NO_READING


@pytest.mark.parametrize("content", ["not json", "[]", '{"readings": "x"}', ""])
def test_a_malformed_answer_is_an_error_reading(db, monkeypatch, content):
    _provider(monkeypatch, FakeProvider(content))
    out = reader_mod.read_codes(db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="", user_id=None, dry_run=True)
    assert out["TAPZZTWT5608"].kind == "error"
    assert out["TAPZZTWT5608"].reason == reader_mod.REASON_ANSWER_UNREADABLE


def test_a_provider_timeout_is_an_error_reading_with_a_fixed_reason(db, monkeypatch):
    _provider(monkeypatch, FakeProvider("", raises=TimeoutError("Request timed out.")))
    out = reader_mod.read_codes(db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="", user_id=None, dry_run=True)
    assert out["TAPZZTWT5608"].kind == "error"
    assert out["TAPZZTWT5608"].reason == reader_mod.REASON_TIMED_OUT


def test_a_provider_error_never_echoes_its_own_text(db, monkeypatch):
    """Security review finding 2: an SDK error can quote a masked key or a vendor body;
    the response carries a fixed code, the log keeps the text."""
    _provider(monkeypatch, FakeProvider("", raises=RuntimeError("Incorrect API key provided: sk-proj-****abcd")))
    out = reader_mod.read_codes(db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="", user_id=None, dry_run=True)
    assert out["TAPZZTWT5608"] == CodeReading("error", None, reason=reader_mod.REASON_PROVIDER_ERROR)


def test_no_provider_configured_is_an_error_reading_not_a_call(db, monkeypatch):
    monkeypatch.setattr(reader_mod, "_resolve_provider", lambda db: (None, "openai", "gpt-test"))
    out = reader_mod.read_codes(db, [("TAPZZTWT5608", "TAP ZZTWT5608")], message_text="", user_id=None, dry_run=True)
    assert out["TAPZZTWT5608"] == CodeReading("error", None, reason=reader_mod.REASON_NO_PROVIDER)


def test_provider_and_model_come_from_one_rung_of_the_ladder(db, monkeypatch):
    """Reviewer finding 3: a reader row set to gemini with no model must not post the
    parser's openai model to Gemini."""
    captured: dict[str, Any] = {}

    def fake_agent_model(session, name, label="production"):
        return {"resolver_code_reader": ("gemini", None), "chatbot_semantic_parser": ("openai", "gpt-x")}[name]

    def fake_get_provider(provider, api_key, model=None):
        captured.update(provider=provider, model=model)
        return object()

    import app.services.ai_prompt_registry as registry
    import app.services.llm_provider as llm

    monkeypatch.setattr(registry, "agent_model", fake_agent_model)
    monkeypatch.setattr(llm, "get_provider", fake_get_provider)
    monkeypatch.setattr(llm, "resolve_api_key", lambda cfg, name: "key")
    monkeypatch.setattr(llm, "resolve_model", lambda cfg, provider, explicit=None: explicit or f"default-for-{provider}")

    provider, name, model = reader_mod._resolve_provider(db)
    assert (name, model) == ("gemini", "default-for-gemini")
    assert captured == {"provider": "gemini", "model": "default-for-gemini"}


def test_the_real_provider_lookup_with_no_config_row_is_none(db):
    provider, _name, _model = reader_mod._resolve_provider(db)
    assert provider is None


# --------------------------------------------------------------------------- #
# Usage bookkeeping
# --------------------------------------------------------------------------- #


def _usage_rows(db):
    from app.models.ai_assistant import AIAssistantUsageLog

    return db.query(AIAssistantUsageLog).filter(AIAssistantUsageLog.feature == reader_mod.FEATURE).all()


def test_a_live_call_writes_one_usage_row(db, monkeypatch):
    _provider(monkeypatch, FakeProvider({"readings": [{"token": "T", "kind": "none", "value": None}]}))
    reader_mod.read_codes(db, [("T", "t")], message_text="t", user_id=None, dry_run=False)

    rows = _usage_rows(db)
    assert len(rows) == 1
    assert rows[0].provider == "openai" and rows[0].model == "gpt-test"
    assert rows[0].prompt_tokens == 120 and rows[0].completion_tokens == 14 and rows[0].total_tokens == 134


def test_a_dry_run_writes_no_usage_row(db, monkeypatch):
    _provider(monkeypatch, FakeProvider({"readings": []}))
    reader_mod.read_codes(db, [("T", "t")], message_text="t", user_id=None, dry_run=True)
    assert _usage_rows(db) == []


# --------------------------------------------------------------------------- #
# The per-request wrapper: cap, memo, never raises
# --------------------------------------------------------------------------- #


class Counting:
    def __init__(self, raises: bool = False):
        self.calls: list[list[tuple[str, str | None]]] = []
        self.raises = raises

    def __call__(self, missed):
        self.calls.append(list(missed))
        if self.raises:
            raise RuntimeError("boom")
        return {token: CodeReading("code", token) for token, _typed in missed}


def test_at_most_five_tokens_reach_the_model_the_rest_are_not_asked():
    inner = Counting()
    reader = per_request(inner)
    missed = [(f"T{i}", f"t {i}") for i in range(7)]

    out = reader(missed)

    assert inner.calls == [missed[:5]]
    assert all(out[f"T{i}"].kind == "code" for i in range(5))
    assert out["T5"] == CodeReading("not_asked", None, reason=reader_mod.REASON_OVER_CAP)
    assert out["T6"].kind == "not_asked"


def test_a_token_is_asked_once_per_request():
    inner = Counting()
    reader = per_request(inner)

    first = reader([("T1", "t 1"), ("T2", "t 2")])
    second = reader([("T2", "t 2"), ("T3", "t 3")])

    assert inner.calls == [[("T1", "t 1"), ("T2", "t 2")], [("T3", "t 3")]]
    assert first["T2"] == second["T2"] == CodeReading("code", "T2")


def test_the_cap_counts_across_calls_in_one_request():
    inner = Counting()
    reader = per_request(inner)
    reader([(f"T{i}", None) for i in range(4)])
    out = reader([("T4", None), ("T5", None)])
    assert inner.calls[1] == [("T4", None)]
    assert out["T5"].kind == "not_asked"


def test_the_wrapper_never_raises():
    reader = per_request(Counting(raises=True))
    out = reader([("T1", "t 1")])
    assert out["T1"] == CodeReading("error", None, reason=reader_mod.REASON_READER_RAISED)


def test_an_empty_ask_makes_no_call():
    inner = Counting()
    assert per_request(inner)([]) == {}
    assert inner.calls == []


def test_reader_for_is_the_wrapped_live_reader(db, monkeypatch):
    seen: list[dict[str, Any]] = []

    def fake_read_codes(session, missed, *, message_text, user_id, dry_run):
        seen.append({"missed": list(missed), "message_text": message_text, "user_id": user_id, "dry_run": dry_run})
        return {token: CodeReading("none", None) for token, _ in missed}

    monkeypatch.setattr(reader_mod, "read_codes", fake_read_codes)
    reader = reader_mod.reader_for(db, message_text="hello", user_id="u1", dry_run=True)

    reader([("A", "a")])
    reader([("A", "a")])

    assert seen == [{"missed": [("A", "a")], "message_text": "hello", "user_id": "u1", "dry_run": True}]
