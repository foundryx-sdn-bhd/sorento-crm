"""Fix round 3: no env key, honest AREAS block, embed timeout, pinned wiring, prompt override,
`missing` against the loaded cards, compact examples, and the seed script on the real DEV shape."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.config import settings
from app.services import llm_provider
from app.services.chatbot import engine as engine_mod
from tests._pg_fixture import blank_session
from tests.chatbot.prompt_diet._common import BACKEND, CORE_TXT, SEED_SCRIPT, V50, split_sections
from tests.chatbot.prompt_diet._engine import STATIC_PROMPT, DietHarness, emission
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    _envelope,
    seeded,
    stub_access,
)


def _m():
    from app.services.chatbot.head import prompt_diet

    return prompt_diet


# -- 1. no env fallback ------------------------------------------------------- #
def _set_config(db, provider, key):
    from app.services.ai_assistant_service import AIAssistantConfigService

    row = AIAssistantConfigService(db).get()
    row.provider = provider
    row.api_key_ciphertext = key
    db.flush()


def test_env_key_is_never_used_when_the_config_key_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-env-must-not-be-used")
    with blank_session() as db:
        _set_config(db, "openai", "")
        assert _m().resolve_embedding_access(db).api_key == ""


def test_a_non_openai_config_key_is_not_an_openai_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-env-must-not-be-used")
    with blank_session() as db:
        _set_config(db, "anthropic", "sk-ant-generic")
        assert _m().resolve_embedding_access(db).api_key == ""


@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_engine_env_key_set_and_config_empty_falls_back_without_a_provider_call(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no provider may be built without a config key")

    monkeypatch.setattr(settings, "openai_api_key", "sk-env-must-not-be-used")
    monkeypatch.setattr(llm_provider, "OpenAIProvider", Boom)
    # the pipeline's own entity resolver embeds with the env key; keep it off the network
    from app.services import embedding_worker

    monkeypatch.setattr(embedding_worker, "_embed_text_chunks", lambda chunks: [[0.0] * 1536 for _ in chunks])
    real_embed = _m().embed_text
    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=["inventory"])
    monkeypatch.setattr(_m(), "embed_text", real_embed)
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert h.system_of(h.parser_calls[0]) == STATIC_PROMPT
    assert "no embedding key" in str(h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["fallback"])


# -- 2. AREAS block ----------------------------------------------------------- #
def _enum_values() -> set[str]:
    text = V50.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("domain_hint = ONE of:"))
    values = {v.strip() for v in line.split(":", 1)[1].split("|")} - {"null"}
    return values | {"purchase_order"}  # added by v50's PURCHASE ORDERS section


def test_areas_block_lists_exactly_the_v50_domain_hints() -> None:
    block = next(s for s in split_sections(CORE_TXT.read_text(encoding="utf-8")) if s.startswith("== AREAS =="))
    lines = block.splitlines()[1:]
    assert lines[0] == "Areas this bot answers (the domain_hint values):"
    names = [l.split(":", 1)[0] for l in lines[1:]]
    assert set(names) == _enum_values()
    assert len(names) == len(set(names))
    assert "sales" not in names
    assert "sales report" in next(l for l in lines if l.startswith("order:"))


# -- 3. embed timeout --------------------------------------------------------- #
def test_embed_has_a_short_timeout_and_no_sdk_retries(monkeypatch) -> None:
    m = _m()
    seen = {}

    class Slow:
        class embeddings:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                raise TimeoutError("slow provider")

    def fake_client(self, **overrides):
        seen.update(overrides)
        return Slow()

    monkeypatch.setattr(llm_provider.OpenAIProvider, "_client", fake_client)
    with pytest.raises(TimeoutError):
        m.embed_text(m.EmbeddingAccess(api_key="sk-x", model="text-embedding-3-small"), "hello")
    assert seen.get("timeout") == 5.0 and seen.get("max_retries") == 0


# -- 4. pinned wiring ---------------------------------------------------------- #
@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
@pytest.mark.parametrize(
    "kind,pinned,card",
    [("product_pick", "open_question", "open_question"), ("team_pick", "escalation", "escalation")],
)
def test_engine_pins_the_state_cards(monkeypatch, session_factory, seeded, stub_access, kind, pinned, card) -> None:
    from tests.chatbot.prompt_diet._common import card_marker

    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=[])
    stub_access()
    state = {"open_question": {"kind": kind, "options": [{"label": "A", "value": "a"}], "expects": "pick"}}
    result = engine_mod.run_turn(
        _envelope(is_test=True, previous_conversation_state=state), session_factory=session_factory
    )
    diet = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]
    assert card in diet["pinned"] and card in diet["cards"]
    assert card_marker(card) in h.system_of(h.parser_calls[0])


# -- 5. prompt override -------------------------------------------------------- #
@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_a_prompt_override_skips_the_diet(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=["inventory"])
    stub_access()
    result = engine_mod.run_turn(
        _envelope(is_test=True, prompt_overrides={"chatbot_semantic_parser": "some-version-id"}),
        session_factory=session_factory,
    )
    assert h.system_of(h.parser_calls[0]) == STATIC_PROMPT
    assert h.embed_calls == []
    assert h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["fallback"] == "prompt override"


# -- 6. missing against the loaded cards ------------------------------------- #
def test_missing_intersects_with_the_available_cards() -> None:
    m = _m()
    parsed = {"domain_hint": "promotion", "asks": [], "order_status": None}
    assert m.missing(parsed, set(), available={"order"}) == set()
    assert m.missing(parsed, set(), available={"order", "promotion"}) == {"promotion"}


# -- 7. compact examples -------------------------------------------------------- #
def test_examples_render_compact_sorted_without_empty_values() -> None:
    m = _m()
    ex = m.Example(
        area="a",
        message="hi",
        context=None,
        expected_output={
            "z_kept": "x", "a_null": None, "b_false": False, "c_empty": "", "d_list": [], "e_dict": {},
            "nested": {"keep": 1, "drop": None, "deep": {"x": []}}, "zero": 0, "true": True,
        },
    )
    prompt = m.build_system_prompt("CORE", {}, [], [ex])
    line = next(l for l in prompt.splitlines() if l.startswith("Output: "))
    assert json.loads(line[len("Output: "):]) == {"nested": {"keep": 1}, "true": True, "z_kept": "x", "zero": 0}
    assert line.index('"nested"') < line.index('"true"') < line.index('"z_kept"')


# -- 8. seed script on the real DEV shape ------------------------------------- #
def _case(cid, text, history=None):
    return {
        "id": cid,
        "contact": {"respond_id": f"R-{cid}", "name": "Zorblat Marn", "first_name": "Zorblat",
                    "company": "Quixley Trading Sdn Bhd", "labels": []},
        "message": {"text": text},
        "history": history or [],
        "expect": {"outcome": "answers", "must": [], "options": [], "must_not": [], "judge_note": ""},
    }


def _emit(cid, text, created, understood):
    return {"id": cid, "contact": f"R-{cid}", "text": text, "created": created, "understood": understood}


def _run(tmp_path, cases, emissions, results):
    data = tmp_path / "dev"
    data.mkdir()
    for c in cases:
        (data / f"{c['id']}.json").write_text(json.dumps(c), encoding="utf-8")
    (tmp_path / "e.jsonl").write_text("".join(json.dumps(r) + "\n" for r in emissions), encoding="utf-8")
    (tmp_path / "r.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--data-dir", str(data), "--out-dir", str(out),
         "--emissions", str(tmp_path / "e.jsonl"), "--results", str(tmp_path / "r.jsonl")],
        cwd=BACKEND, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    path = out / "examples.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()], proc


def test_seed_uses_the_latest_non_null_emission_compacts_it_and_takes_the_last_bot_turn(tmp_path) -> None:
    cases = [_case("c1", "price for SRT-TEST-1", history=[
        {"role": "bot", "text": "Older reply"}, {"role": "user", "text": "ok"}, {"role": "bot", "text": "Hello Zorblat Marn"}])]
    old = {"domain_hint": "inventory", "intent_hint": "old"}
    new = {"domain_hint": "master_products", "intent_hint": "check_product", "order_status": None, "entities": [],
           "correction": False, "user_goal": "price for Zorblat"}
    emissions = [_emit("c1", "price for SRT-TEST-1", "2026-10-01T00:00:00", old),
                 _emit("c1", "price for SRT-TEST-1", "2026-10-02T00:00:00", new),
                 _emit("c1", "price for SRT-TEST-1", "2026-10-03T00:00:00", None)]
    rows, _ = _run(tmp_path, cases, emissions, [{"case_id": "c1", "label": "good_clarify"}])
    assert len(rows) == 1
    row = rows[0]
    assert row["area"] == "master_products"
    assert row["context"] == "Hello [customer]"
    assert row["expected_output"] == {"domain_hint": "master_products", "intent_hint": "check_product",
                                      "user_goal": "price for [customer]"}


def test_seed_skips_other_labels_missing_emissions_and_unknown_areas(tmp_path) -> None:
    cases = [_case("ok", "a"), _case("wrong", "b"), _case("noemit", "c"), _case("noarea", "d")]
    emissions = [
        _emit("ok", "a", "2026-10-01T00:00:00", {"domain_hint": "order", "order_status": "sales_report"}),
        _emit("wrong", "b", "2026-10-01T00:00:00", {"domain_hint": "order"}),
        _emit("noarea", "d", "2026-10-01T00:00:00", {"domain_hint": "not_a_card"}),
    ]
    results = [{"case_id": "ok", "label": "correct"}, {"case_id": "wrong", "label": "wrong"},
               {"case_id": "noemit", "label": "correct"}, {"case_id": "noarea", "label": "correct"}]
    rows, proc = _run(tmp_path, cases, emissions, results)
    assert [r["area"] for r in rows] == ["sales"]
    assert "noarea" in proc.stderr


# -- round 4 -------------------------------------------------------------------- #
@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_fallback_reason_is_capped_and_carries_no_parameter_dump(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    h = DietHarness(monkeypatch, [emission()])
    dump = "[SQL: SELECT 1] [parameters: {'vec': '[" + ",".join(["0.123456"] * 4000) + "]'}]"
    h.diet_on(example_areas=["inventory"], nearest_error=RuntimeError("could not run\n" + dump))
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    fallback = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["fallback"]
    assert len(fallback) <= 300
    assert fallback.startswith("RuntimeError: could not run")
    assert "parameters" not in fallback


def test_seed_exclude_drops_listed_messages(tmp_path) -> None:
    cases = [_case("keep", "keep me"), _case("drop1", "drop by pair"), _case("drop2", "drop by row")]
    emissions = [_emit(c["id"], c["message"]["text"], "2026-10-01T00:00:00", {"domain_hint": "order"}) for c in cases]
    results = [{"case_id": c["id"], "label": "correct"} for c in cases]
    exclude = tmp_path / "x.jsonl"
    exclude.write_text(json.dumps({"message": "drop by row"}) + "\n", encoding="utf-8")
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([["R-drop1", "drop by pair"]]), encoding="utf-8")
    for ex in (exclude, pairs):
        out = tmp_path / ("o_" + ex.name)
        data = tmp_path / "dev"
        if not data.exists():
            data.mkdir()
            for c in cases:
                (data / f"{c['id']}.json").write_text(json.dumps(c), encoding="utf-8")
            (tmp_path / "e.jsonl").write_text("".join(json.dumps(r) + "\n" for r in emissions), encoding="utf-8")
            (tmp_path / "r.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SEED_SCRIPT), "--data-dir", str(data), "--out-dir", str(out),
             "--emissions", str(tmp_path / "e.jsonl"), "--results", str(tmp_path / "r.jsonl"),
             "--exclude", str(ex)],
            cwd=BACKEND, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        got = {json.loads(l)["message"] for l in (out / "examples.jsonl").read_text().splitlines()}
        dropped = {"drop by row"} if ex is exclude else {"drop by pair"}
        assert got == {"keep me", "drop by row", "drop by pair"} - dropped
        assert "excluded (near-duplicate of hidden)" in proc.stderr
