"""S2 - ideation brain extractor: new schema, deterministic guards, R17 semantic
field capture.

Keys back to
``documentation/plans/ideation/ideation-intake-redesign-24sep-acceptance-criteria.md``:

- **AC-1201** - output shape is ``{fields, remove, skip, title, review_action,
  change_text, duplicate_choice}``; ``confirm`` is derived, never read from the model.
- **AC-1202** - ``title`` cut to 8 words.
- **AC-1203** - a natural skip ("dunno lah, can skip this one?") produces
  ``skip == ["impact"]``.
- **AC-1204** - a question ABOUT a field produces an empty ``skip`` and no field set.
- **AC-1205** - ``problem`` can never appear in ``skip`` (deterministic guard).
- **AC-1208** - "yes"/"ok"/"boleh"/"submit"/"confirm" in review -> ``review_action ==
  "submit"`` and ``confirm`` derives to true.
- **AC-1209** - a review-turn change request -> ``review_action == "change"``,
  ``change_text`` set, the field updated, ``confirm`` false.
- **AC-1210** - "cancel" in review -> ``review_action == "cancel"``.
- **AC-1211** - ``review_action == "submit"`` outside review never derives
  ``confirm = True`` (the existing AC-11b guard, moved here).
- **AC-1219 (R17)** - the field a message updates is decided by MEANING, never by
  which field ``next_field`` hinted: a message that reads as more problem detail
  updates ``fields.problem`` even while ``proposed_solution`` was the field asked.

The LLM call is STUBBED (``get_provider`` patched to a deterministic stub, same
pattern as ``tests/test_ticket_intake.py``); no live provider, no live shared-service.
Postgres only (``tests/_pg_fixture.py``), per repo convention - ``AIAssistantConfigService``
needs a real config row.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.ideation_extractor import IdeateExtraction, extract_ideate_turn
from app.services.llm_provider import ChatResult
from tests._pg_fixture import blank_session


@pytest.fixture
def db_session() -> Session:
    with blank_session() as session:
        yield session


@pytest.fixture
def configured(db_session: Session) -> Session:
    """Seed the singleton AI assistant config so extract_ideate_turn doesn't
    degrade to an empty extraction on the api-key check."""
    cfg = AIAssistantConfigService(db_session).get()
    cfg.api_key_ciphertext = "fake-key"
    cfg.provider = "openai"
    cfg.model = "gpt-4o-mini"
    cfg.is_enabled = True
    db_session.commit()
    return db_session


class _StubProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    def chat(self, *_a, **_k):
        return ChatResult(
            content=json.dumps(self._payload),
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


def _extraction(
    db: Session,
    payload: dict,
    *,
    message_text: str = "some message",
    status: str | None = None,
    missing: list[str] | None = None,
    next_field: str | None = None,
    duplicate_candidate_title: str | None = None,
    captured: dict[str, str] | None = None,
    prior_title: str | None = None,
) -> IdeateExtraction:
    full_payload = {
        "fields": [],
        "remove": [],
        "skip": [],
        "title": "",
        "review_action": "none",
        "change_text": "",
        "duplicate_choice": "none",
        **payload,
    }
    with patch(
        "app.services.ideation_extractor.get_provider",
        return_value=_StubProvider(full_payload),
    ):
        return extract_ideate_turn(
            db,
            message_text=message_text,
            status=status,
            missing=missing,
            next_field=next_field,
            duplicate_candidate_title=duplicate_candidate_title,
            captured=captured,
            prior_title=prior_title,
        )


# --------------------------------------------------------------------------- #
# AC-1201 - shape + confirm derived, never read from the model                #
# --------------------------------------------------------------------------- #
def test_confirm_is_derived_not_read_from_model(configured):
    """Even if a rogue model payload carried a top-level `confirm`, the schema
    doesn't declare one - it can't reach the result except via review_action.
    #1279 round 2 (owner ruling 26 Sep 2026): only a plain yes creates, so a
    submit that carries a field edit and no yes word does not confirm."""
    out = _extraction(
        configured, {"review_action": "submit"}, message_text="yes", status="review"
    )
    assert out.confirm is True
    edit = _extraction(
        configured,
        {"fields": [{"key": "problem", "value": "x"}], "review_action": "submit"},
        status="review",
    )
    assert edit.confirm is False
    assert edit.fields == {"problem": "x"}


# --------------------------------------------------------------------------- #
# AC-1202 - title cut to 8 words                                              #
# --------------------------------------------------------------------------- #
def test_title_cut_to_eight_words(configured):
    long_title = " ".join(f"word{i}" for i in range(1, 13))  # 12 words
    out = _extraction(configured, {"title": long_title})
    assert out.title == " ".join(f"word{i}" for i in range(1, 9))
    assert len(out.title.split()) == 8


def test_short_title_untouched(configured):
    out = _extraction(configured, {"title": "Show promo price in red"})
    assert out.title == "Show promo price in red"


# --------------------------------------------------------------------------- #
# AC-1203 - a natural skip                                                    #
# --------------------------------------------------------------------------- #
def test_natural_skip_phrase(configured):
    out = _extraction(
        configured,
        {"skip": ["impact"]},
        message_text="dunno lah, can skip this one?",
        status="collecting",
        next_field="impact",
    )
    assert out.skip == ["impact"]


# --------------------------------------------------------------------------- #
# AC-1204 - a clarifying question is not a skip and sets no field             #
# --------------------------------------------------------------------------- #
def test_clarifying_question_is_not_a_skip(configured):
    out = _extraction(
        configured,
        {"fields": [], "skip": []},
        message_text="what do you mean impact?",
        status="collecting",
        next_field="impact",
    )
    assert out.skip == []
    assert out.fields == {}


# --------------------------------------------------------------------------- #
# AC-1205 - problem can never be skipped (deterministic guard)                #
# --------------------------------------------------------------------------- #
def test_problem_dropped_from_skip(configured):
    out = _extraction(configured, {"skip": ["problem", "impact"]})
    assert out.skip == ["impact"]
    assert "problem" not in out.skip


def test_unknown_skip_key_dropped(configured):
    out = _extraction(configured, {"skip": ["impact", "not_a_real_key"]})
    assert out.skip == ["impact"]


# --------------------------------------------------------------------------- #
# AC-1208 - natural submit words confirm only in review                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "word",
    ["yes", "ok", "ya", "boleh", "好", "可以", "submit", "confirm", "can you just submit it already"],
)
def test_submit_words_confirm_in_review(configured, word):
    out = _extraction(configured, {"review_action": "submit"}, message_text=word, status="review")
    assert out.review_action == "submit"
    assert out.confirm is True


# --------------------------------------------------------------------------- #
# AC-1209 - a review-turn change request                                      #
# --------------------------------------------------------------------------- #
def test_change_request_in_review(configured):
    out = _extraction(
        configured,
        {
            "fields": [{"key": "impact", "value": "faster checkout"}],
            "review_action": "change",
            "change_text": "change the impact to faster checkout",
        },
        message_text="change the impact to faster checkout",
        status="review",
    )
    assert out.review_action == "change"
    assert out.change_text == "change the impact to faster checkout"
    assert out.fields == {"impact": "faster checkout"}
    assert out.confirm is False


# --------------------------------------------------------------------------- #
# AC-1210 - cancel in review                                                  #
# --------------------------------------------------------------------------- #
def test_cancel_in_review(configured):
    out = _extraction(
        configured, {"review_action": "cancel"}, message_text="cancel", status="review"
    )
    assert out.review_action == "cancel"
    assert out.confirm is False


# --------------------------------------------------------------------------- #
# AC-1211 - submit outside review never derives confirm=True                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [None, "collecting", "duplicate_candidate"])
def test_submit_outside_review_never_confirms(configured, status):
    out = _extraction(configured, {"review_action": "submit"}, status=status)
    assert out.confirm is False


def test_cancel_outside_review_is_still_cancel(configured):
    """AC-1211: cancel applies at ANY status, unlike submit."""
    out = _extraction(configured, {"review_action": "cancel"}, status="collecting")
    assert out.review_action == "cancel"


# --------------------------------------------------------------------------- #
# AC-1219 (R17) - semantic field capture, not deterministic slot-filling      #
# --------------------------------------------------------------------------- #
def test_semantic_capture_ignores_next_field_hint(configured):
    """next_field='proposed_solution' is only a hint; a message that reads as
    more problem detail must land in `problem`, not be force-mapped to
    proposed_solution."""
    out = _extraction(
        configured,
        {"fields": [{"key": "problem", "value": "dealers keep calling to check order status, especially during month end when the team is busy with closing"}]},
        message_text="it happens most during month end when we're busy with closing",
        status="collecting",
        next_field="proposed_solution",
    )
    assert "problem" in out.fields
    assert "proposed_solution" not in out.fields


def test_context_block_carries_next_field_and_candidate_hint(configured):
    """The context passed to the model includes next_field and the duplicate
    candidate's title (plan: 'The context block gains next_field and the
    duplicate candidate title') - asserted via the captured provider messages."""
    captured_messages = {}

    class _CapturingProvider:
        def chat(self, messages, *_a, **_k):
            captured_messages["messages"] = messages
            return ChatResult(
                content=json.dumps(
                    {
                        "fields": [],
                        "remove": [],
                        "skip": [],
                        "title": "",
                        "review_action": "none",
                        "change_text": "",
                        "duplicate_choice": "none",
                    }
                ),
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )

    with patch(
        "app.services.ideation_extractor.get_provider", return_value=_CapturingProvider()
    ):
        extract_ideate_turn(
            configured,
            message_text="keep mine separate",
            status="duplicate_candidate",
            next_field="proposed_solution",
            duplicate_candidate_title="Show promo price in red",
        )

    user_content = captured_messages["messages"][1]["content"]
    assert "proposed_solution" in user_content
    assert "Show promo price in red" in user_content


# --------------------------------------------------------------------------- #
# Reviewer Blocking 2 (round 1, PR #1222 at 720bb8f5): the context must carry #
# the draft's captured answers and the stored title so the model can EXTEND a #
# field (rather than replace it, losing the earlier text) and keep the SAME   #
# title stable across turns (AC-1219 extend part; title stability).          #
# --------------------------------------------------------------------------- #
def test_context_block_carries_captured_answers_and_prior_title(configured):
    captured_messages = {}

    class _CapturingProvider:
        def chat(self, messages, *_a, **_k):
            captured_messages["messages"] = messages
            return ChatResult(
                content=json.dumps(
                    {
                        "fields": [],
                        "remove": [],
                        "skip": [],
                        "title": "",
                        "review_action": "none",
                        "change_text": "",
                        "duplicate_choice": "none",
                    }
                ),
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )

    with patch(
        "app.services.ideation_extractor.get_provider", return_value=_CapturingProvider()
    ):
        extract_ideate_turn(
            configured,
            message_text="it happens most during month end when we're busy with closing",
            status="collecting",
            next_field="proposed_solution",
            captured={"problem": "dealers keep calling to check order status"},
            prior_title="Dealers check order status by calling",
        )

    user_content = captured_messages["messages"][1]["content"]
    assert "dealers keep calling to check order status" in user_content
    assert "Dealers check order status by calling" in user_content


def test_semantic_capture_extends_the_existing_field_is_a_pass_through_check(configured):
    """Nit 1 (reviewer, round 2): this does NOT prove a real model extends the
    field using the context - the stub always returns the extended value
    regardless of what `captured`/`prior_title` hold, so it stays green even
    with the context wiring removed (K2a). It only proves the extractor
    forwards a cooperative model's output unchanged when the field's value
    happens to be an extension. `test_context_block_carries_captured_answers_
    and_prior_title` above is the test that actually pins the context wiring;
    live "does the model extend" behaviour is a console-walk item (AC-1219)."""
    out = _extraction(
        configured,
        {
            "fields": [
                {
                    "key": "problem",
                    "value": (
                        "dealers keep calling to check order status, especially "
                        "during month end when the team is busy with closing"
                    ),
                }
            ]
        },
        message_text="it happens most during month end when we're busy with closing",
        status="collecting",
        next_field="proposed_solution",
        captured={"problem": "dealers keep calling to check order status"},
        prior_title="Dealers check order status by calling",
    )
    assert out.fields["problem"].startswith("dealers keep calling to check order status")
    assert "month end" in out.fields["problem"]


# --------------------------------------------------------------------------- #
# Degrades to empty extraction on provider failure - never raises             #
# --------------------------------------------------------------------------- #
def test_provider_failure_degrades_to_empty_extraction(configured):
    class _BoomProvider:
        def chat(self, *_a, **_k):
            raise RuntimeError("provider down")

    with patch(
        "app.services.ideation_extractor.get_provider", return_value=_BoomProvider()
    ):
        out = extract_ideate_turn(configured, message_text="hello", status="collecting")

    assert out == IdeateExtraction()


# --------------------------------------------------------------------------- #
# #1277 (issue) - W2: clean values (preamble stripped, spelling corrected,    #
# never semicolon-joined). AC-3.                                             #
# --------------------------------------------------------------------------- #
def test_extractor_fallback_prompt_has_clean_values_rule():
    """The system prompt tells the model to strip conversational preamble,
    correct spelling, and never join an extended value with a semicolon."""
    from app.services import ai_prompt_registry

    text = ai_prompt_registry.PROMPT_KEYS["ideate_extractor"].fallback().lower()
    assert "i have an idea" in text
    assert "spelling" in text
    assert "semicolon" in text


def test_context_block_lists_captured_fields_one_per_line_no_semicolon_join(configured):
    """The captured-so-far context block is one field per line, never `"; "`-
    joined - the same glue the owner saw echoed inside the Problem line
    (#1277 finding 2)."""
    captured_messages = {}

    class _CapturingProvider:
        def chat(self, messages, *_a, **_k):
            captured_messages["messages"] = messages
            return ChatResult(
                content=json.dumps(
                    {
                        "fields": [],
                        "remove": [],
                        "skip": [],
                        "title": "",
                        "review_action": "none",
                        "change_text": "",
                        "duplicate_choice": "none",
                    }
                ),
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )

    with patch(
        "app.services.ideation_extractor.get_provider", return_value=_CapturingProvider()
    ):
        extract_ideate_turn(
            configured,
            message_text="more detail on the same idea",
            status="collecting",
            captured={"problem": "a", "impact": "b"},
        )

    user_content = captured_messages["messages"][1]["content"]
    assert "a; impact" not in user_content
    lines = [line.strip() for line in user_content.splitlines()]
    assert any(line == "- problem: a" for line in lines)
    assert any(line == "- impact: b" for line in lines)


# --------------------------------------------------------------------------- #
# #1277 - the migration that publishes the new ideate_extractor/ideate_reply  #
# prompt versions to production (AC-3). Text/grep-style: no DB required.     #
# --------------------------------------------------------------------------- #
def test_ideation_reply_fmt_migration_exists_and_bumps_both_prompts():
    import pathlib

    revision_id = "ideation_reply_fmt_prompts"
    assert len(revision_id) <= 32, "alembic head revision id must be <= 32 chars"

    versions_dir = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    matches = [
        p for p in versions_dir.glob("*.py") if revision_id in p.read_text(encoding="utf-8")
    ]
    assert matches, (
        f"expected a migration under {versions_dir} carrying revision "
        f"{revision_id!r} (bump_prompt_to_fallback for ideate_extractor and "
        f"ideate_reply)"
    )
    content = matches[0].read_text(encoding="utf-8")
    assert f'revision = "{revision_id}"' in content or f"revision = '{revision_id}'" in content
    assert 'bump_prompt_to_fallback(op.get_bind(), "ideate_extractor")' in content
    assert 'bump_prompt_to_fallback(op.get_bind(), "ideate_reply")' in content


# --------------------------------------------------------------------------- #
# #1279 round 2 - owner console test 26 Sep 14:09Z (W1 to W3).                #
# --------------------------------------------------------------------------- #
from app.services.ideation_extractor import derive_confirm, normalise_field_value, normalise_title  # noqa: E402


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("the manufacturing?", "Manufacturing"),
        ("manufacturing?", "Manufacturing"),
        ("  sales manager  ", "Sales Manager"),
        ("IT", "IT"),
        ("our operations team!", "Operations Team"),
        ("Manufacturing", "Manufacturing"),
    ],
)
def test_normaliser_department(raw, expected):
    assert normalise_field_value("department", raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("it will boost sales?", "It will boost sales"),
        ("It will reduce our supply chain constraints.", "It will reduce our supply chain constraints."),
        ("faster delivery ??", "Faster delivery"),
        ("  more sales！", "More sales"),
        ("提高销量？", "提高销量"),
    ],
)
def test_normaliser_impact(raw, expected):
    assert normalise_field_value("impact", raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"Implement manufacturing production line?"', "Implement manufacturing production line"),
        ("implement production line", "Implement production line"),
        ("“Sales order KPI tracking”", "Sales order KPI tracking"),
        ("", ""),
    ],
)
def test_normaliser_title(raw, expected):
    assert normalise_title(raw) == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("yes", True), ("ok", True), ("ya", True), ("boleh", True), ("好", True), ("可以", True),
        ("Yes!", True), ("ok lah", True), ("okay", True),
        ("sure", False), ("maybe", False), ("manufacturing?", False), ("no", False),
    ],
)
def test_a_plain_yes_confirms_in_review_without_the_model(message, expected):
    assert derive_confirm("review", message, fields={}, remove=[], review_action="none") is expected


def test_confirm_needs_review_and_no_edit():
    assert derive_confirm("collecting", "yes", fields={}, remove=[], review_action="submit") is False
    assert derive_confirm("review", "yes", fields={"impact": "x"}, remove=[], review_action="submit") is False
    assert derive_confirm("review", "yes", fields={}, remove=["impact"], review_action="none") is False
    assert derive_confirm("review", "yes cancel", fields={}, remove=[], review_action="cancel") is False
    assert derive_confirm("review", "ok that's correct", fields={}, remove=[], review_action="submit") is True
    assert derive_confirm("review", "that's correct", fields={}, remove=[], review_action="submit") is False


def test_extractor_prompt_cleans_every_value_and_never_keeps_typed_punctuation():
    from app.services import ai_prompt_registry

    text = ai_prompt_registry.PROMPT_KEYS["ideate_extractor"].fallback()
    lower = text.lower()
    # W2: the owner's own example, and the punctuation rule.
    assert "the manufactuirng?" in lower and "'manufacturing'" in lower
    assert "question mark" in lower
    assert "title case" in lower
    # W1: a stated idea gets the need behind it as its problem; a raw captured value is re-cleaned.
    assert "the need behind it" in lower and "never invent a problem for a message that names no idea" in lower
    assert "re-emit" in lower
    # W3: only a plain yes submits.
    for word in ("yes", "ok", "ya", "boleh", "好", "可以"):
        assert word in text


def test_reply_prompt_asks_the_confirm_question_in_review():
    from app.services import ai_prompt_registry

    text = ai_prompt_registry.PROMPT_KEYS["ideate_reply"].fallback()
    assert "Submit this idea? Reply yes to submit, or tell me what to change." in text


def test_ideation_confirm_migration_bumps_both_prompts_on_top_of_round_1():
    import pathlib

    revision_id = "ideation_confirm_prompts"
    assert len(revision_id) <= 32
    versions_dir = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    path = versions_dir / f"{revision_id}.py"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert f'revision = "{revision_id}"' in content
    assert 'down_revision = "ideation_reply_fmt_prompts"' in content
    assert 'bump_prompt_to_fallback(op.get_bind(), "ideate_extractor")' in content
    assert 'bump_prompt_to_fallback(op.get_bind(), "ideate_reply")' in content


# --------------------------------------------------------------------------- #
# has_idea - intent-only messages ("I have an idea") carry no idea            #
# --------------------------------------------------------------------------- #
def test_has_idea_is_a_required_boolean_in_the_schema():
    from app.services.ideation_extractor import IDEATE_EXTRACTION_JSON_SCHEMA as schema

    assert "has_idea" in schema["properties"]
    assert "has_idea" in schema["required"]
    prop = schema["properties"]["has_idea"]
    assert prop["type"] == "boolean"
    assert "I have an idea" in prop["description"]


@pytest.mark.parametrize("value, expected", [(True, True), (False, False)])
def test_has_idea_parser_maps_booleans(configured, value, expected):
    out = _extraction(configured, {"has_idea": value})
    assert out.has_idea is expected


@pytest.mark.parametrize("payload", [{}, {"has_idea": None}, {"has_idea": "false"}, {"has_idea": 0}, {"has_idea": "yes"}])
def test_has_idea_parser_gives_none_for_missing_or_non_bool(configured, payload):
    out = _extraction(configured, payload)
    assert out.has_idea is None


def test_has_idea_defaults_to_none_on_a_failed_parse():
    assert IdeateExtraction().has_idea is None
