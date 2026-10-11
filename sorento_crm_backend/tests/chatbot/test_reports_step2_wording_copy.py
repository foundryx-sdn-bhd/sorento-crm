"""Phase 2 RED tests - REPORTS-STEP2 report wording lives in the editable reply copy (owner, 11 Oct 2026).

The approved lines are `CHATBOT_REPLY_COPY` rows (so Settings > AI Prompts edits them with no deploy) and
carry `{{token}}`s the lane fills from what it knows, never fixed sentences. Short names pinned here
(the key is `chatbot_reply_<short name>`, with `.ms` / `.zh` siblings):

* report_subject_ask      "Which {{missing}} is this for?"                      tokens: missing
* report_refused          "I can't run that report as asked. Tell me ..."       tokens: report
* report_period_missing   "I still need a period for {{asked}}."                tokens: asked
* report_period_give_up   "I still need a period. Ask again in one message, e.g. {{example}}."  tokens: example
* report_own_account      route 403 `customer_not_permitted`
* report_dimension_denied route 403 `report_dimension_not_allowed`
* report_not_enabled      route 403 `sales_report_not_enabled`
The 403 lines are chosen by the route's `detail.code`, never by echoing `detail.message`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.services import ai_prompt_registry
from app.services.ai_prompt_registry import PROMPT_KEYS
from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY
from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, _e, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_fixes import _http_error, _payload, _reply_text, _run, _sales_payload
from tests.chatbot.test_reports_step2_lane import _s2_harness  # noqa: F401  (autouse fixture by name)

NO_PERIOD = {"date_filter_start": None, "date_filter_end": None}

KEYS: dict[str, tuple[str, ...]] = {
    "report_subject_ask": ("missing",),
    "report_refused": ("report",),
    "report_period_missing": ("asked",),
    "report_period_give_up": ("example",),
    "report_own_account": (),
    "report_dimension_denied": (),
    "report_not_enabled": (),
}
EN = {
    "report_subject_ask": "Which {{missing}} is this for?",
    "report_refused": "I can't run {{report}} as asked. Tell me the product, customer or period you want.",
    "report_period_missing": "I still need a period for {{asked}}.",
    "report_period_give_up": "I still need a period. Ask again in one message, e.g. {{example}}.",
}


@pytest.fixture
def bust():
    yield
    ai_prompt_registry.bust_cache()


# ================================================================================ the keys exist


@pytest.mark.parametrize("short", sorted(KEYS))
def test_each_key_is_a_copy_row_with_its_declared_tokens_and_language_variants(short) -> None:
    for suffix in ("", ".ms", ".zh"):
        row = CHATBOT_REPLY_COPY.get(f"{short}{suffix}")
        assert row is not None, f"{short}{suffix} is not in CHATBOT_REPLY_COPY"
        key, body, tokens = row
        assert key == f"chatbot_reply_{short}{suffix}", key
        assert tuple(tokens) == KEYS[short], (key, tokens)
        for token in KEYS[short]:
            assert "{{" + token + "}}" in body, (key, token)
        assert key in PROMPT_KEYS and list(PROMPT_KEYS[key].variables) == list(KEYS[short]), key


@pytest.mark.parametrize("short", sorted(EN))
def test_the_approved_english_text(short) -> None:
    assert CHATBOT_REPLY_COPY[short][1] == EN[short]


# ================================================================================ the old constants are gone


@pytest.mark.parametrize(
    "module, name",
    [
        ("app.services.chatbot.lanes.business", "SUBJECT_REQUIRED_ASK"),
        ("app.services.chatbot.lanes.business", "REPORT_REFUSED_LINE"),
        ("app.services.chatbot.lanes.business.report_ask", "PERIOD_STILL_MISSING"),
    ],
)
def test_the_hard_coded_constant_is_gone(module, name) -> None:
    import importlib

    assert not hasattr(importlib.import_module(module), name), f"{module}.{name} is still a code constant"


# ================================================================================ a published override changes the reply


def _publish(db, key: str, text: str) -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from app.services.ai_prompt_seed import seed_prompt_registry

    seed_prompt_registry(db.get_bind())
    version = AIPromptVersion(name=key, version=2, type="text", template=text, variables=list(PROMPT_KEYS[key].variables))
    db.add(version)
    db.flush()
    db.query(AIPromptLabel).filter_by(name=key, label="production").one().version_id = version.id
    db.commit()
    ai_prompt_registry.bust_cache(key)


def test_a_published_subject_ask_changes_the_reply_with_no_code_change(session_factory, bust) -> None:
    body = _http_error(422, "subject_required", "ignored")
    db = session_factory()
    try:
        _publish(db, "chatbot_reply_report_subject_ask", "Pick a {{missing}} first.")
    finally:
        db.close()
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert _reply_text(fragment) == "Pick a product or customer first.", fragment


def test_a_published_refusal_changes_the_reply_and_fills_the_report_name(session_factory, bust) -> None:
    body = _http_error(400, "invalid_period", "ignored")
    db = session_factory()
    try:
        _publish(db, "chatbot_reply_report_refused", "No go for {{report}}.")
    finally:
        db.close()
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert _reply_text(fragment) == "No go for the sales report.", fragment


# ================================================================================ specifics are filled


def test_the_default_subject_ask_names_what_is_missing(session_factory) -> None:
    body = _http_error(422, "subject_required", "ignored")
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert _reply_text(fragment) == "Which product or customer is this for?", fragment


def test_the_default_refusal_names_the_report_and_says_what_to_tell(session_factory) -> None:
    body = _http_error(400, "invalid_period", "ignored")
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: body)
    assert _reply_text(fragment) == EN["report_refused"].replace("{{report}}", "the sales report"), fragment
    assert "{{" not in _reply_text(fragment), fragment


def _restated(console, **overrides: Any) -> str:
    text, _calls = console.say(_rank(_e("Sorento", "brand"), **{**NO_PERIOD, **overrides}), "restated")
    return text


def test_the_period_re_ask_summarises_the_held_ask(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, measure="amount", **NO_PERIOD), "top 10 sorento")
    text = _restated(console, top_n=20, measure="amount")
    first = text.splitlines()[0]
    assert first.startswith("I still need a period for ") and first.endswith("."), text
    assert "20" in first and "Sorento" in first and "amount" in first.lower(), first
    assert "that." not in first, first
    assert PERIOD_Q in text, text


def test_the_give_up_carries_an_example_built_from_the_held_ask(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, measure="amount", **NO_PERIOD), "top 10 sorento")
    _restated(console, top_n=20, measure="amount")
    text = _restated(console, top_n=30, measure="amount")
    assert text.startswith("I still need a period. Ask again in one message, e.g. "), text
    assert "this month" in text and "Sorento" in text and "{{" not in text, text


# ================================================================================ route 403 lines by code


def _forbidden(code: str, message: str) -> str:
    return _http_error(403, code, message)


@pytest.mark.parametrize(
    "code, short",
    [
        ("customer_not_permitted", "report_own_account"),
        ("report_dimension_not_allowed", "report_dimension_denied"),
        ("sales_report_not_enabled", "report_not_enabled"),
    ],
)
def test_a_route_403_is_said_from_the_copy_chosen_by_its_code(session_factory, code, short) -> None:
    fragment, _calls = _run(session_factory, _sales_payload(), lambda n, a: _forbidden(code, "ROUTE WORDS THAT MUST NOT SHOW"))
    assert _reply_text(fragment) == CHATBOT_REPLY_COPY[short][1], fragment
    assert "ROUTE WORDS" not in _reply_text(fragment), fragment


def test_a_403_with_an_unknown_code_never_echoes_the_route_message(session_factory) -> None:
    fragment, _calls = _run(
        session_factory, _sales_payload(), lambda n, a: _forbidden("something_new", "SECRET ROUTE TEXT")
    )
    text = _reply_text(fragment)
    assert "SECRET ROUTE TEXT" not in text, fragment
    assert fragment.get("kind") != "error" and text.strip(), fragment


def test_a_published_403_line_changes_the_reply(session_factory, bust) -> None:
    db = session_factory()
    try:
        _publish(db, "chatbot_reply_report_own_account", "Only your own account, please.")
    finally:
        db.close()
    fragment, _calls = _run(
        session_factory, _sales_payload(), lambda n, a: _forbidden("customer_not_permitted", "route words")
    )
    assert _reply_text(fragment) == "Only your own account, please.", fragment


# ================================================================================ language


def test_the_ms_variant_is_chosen_for_a_malay_contact() -> None:
    from app.services.chatbot import copy as copy_mod

    canned = copy_mod.fallback_copy()
    en = canned.render_in("report_subject_ask", "en", missing="product")
    ms = canned.render_in("report_subject_ask", "ms", missing="produk")
    assert en == "Which product is this for?", en
    assert ms != en and "produk" in ms and "{{" not in ms, ms


# ================================================================================ the seed migration

NEW_KEYS = [f"chatbot_reply_{short}{suffix}" for short in KEYS for suffix in ("", ".ms", ".zh")]


def _script():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _head_module():
    import importlib.util

    script = _script()
    (head,) = script.get_heads()
    rev = script.get_revision(head)
    spec = importlib.util.spec_from_file_location("zzt_wording_seed_migration", rev.path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _upgrade(session) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _head_module()
    with Operations.context(MigrationContext.configure(session.connection())):
        module.upgrade()
    session.expire_all()


def test_the_chain_has_one_head_and_it_is_the_wording_seed_on_pab_0001() -> None:
    heads = _script().get_heads()
    assert len(heads) == 1, heads
    head = heads[0]
    assert head != "pab_0001_parser_ablation", "no new revision sits on top of pab_0001_parser_ablation"
    assert len(head) <= 32, head
    assert _script().get_revision(head).down_revision == "pab_0001_parser_ablation"


def _clear(db) -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    db.query(AIPromptLabel).filter(AIPromptLabel.name.in_(NEW_KEYS)).delete(synchronize_session=False)
    db.query(AIPromptVersion).filter(AIPromptVersion.name.in_(NEW_KEYS)).delete(synchronize_session=False)
    db.commit()


def test_the_upgrade_creates_v1_and_the_production_label_for_each_new_key() -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        _clear(db)
        _upgrade(db)
        for key in NEW_KEYS:
            v1 = db.query(AIPromptVersion).filter_by(name=key, version=1).one()
            label = db.query(AIPromptLabel).filter_by(name=key, label="production").one()
            assert label.version_id == v1.id, key
            assert v1.template == CHATBOT_REPLY_COPY[key.removeprefix("chatbot_reply_")][1], key


def test_running_the_upgrade_again_is_a_no_op() -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        _clear(db)
        _upgrade(db)
        _upgrade(db)
        for key in NEW_KEYS:
            assert [v.version for v in db.query(AIPromptVersion).filter_by(name=key).all()] == [1], key
            assert db.query(AIPromptLabel).filter_by(name=key, label="production").count() == 1, key


def test_an_edited_row_keeps_its_edited_text_after_the_upgrade() -> None:
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from tests._pg_fixture import blank_session

    key = "chatbot_reply_report_refused"
    with blank_session() as db:
        _clear(db)
        v1 = AIPromptVersion(name=key, version=1, type="text", template="old words", variables=["report"])
        v2 = AIPromptVersion(name=key, version=2, type="text", template="The owner's edit for {{report}}.", variables=["report"])
        db.add_all([v1, v2])
        db.flush()
        db.add(AIPromptLabel(name=key, label="production", version_id=v2.id))
        db.commit()
        _upgrade(db)
        label = db.query(AIPromptLabel).filter_by(name=key, label="production").one()
        assert label.version_id == v2.id
        assert db.query(AIPromptVersion).filter_by(name=key).count() == 2
        assert db.query(AIPromptVersion).filter_by(id=label.version_id).one().template == "The owner's edit for {{report}}."
