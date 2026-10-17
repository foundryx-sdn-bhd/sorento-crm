"""S2 - ideation brain extractor: new schema, deterministic guards, R17 semantic
field capture.

Keys back to
``documentation/plans/ideation/ideation-intake-redesign-24sep-acceptance-criteria.md``:

- **AC-1201** - output shape is ``{fields, remove, skip, title, review_action,
  change_text, duplicate_choice}``; ``confirm`` is derived, never read from the model.
- **AC-1202** - ``title`` cut to 8 words.
- **AC-1203** - a natural skip ("dunno lah, can skip this one?") produces
  ``skip == ["desired_outcome"]`` (IDEA-CAPTURE: the optional keys are now desired_outcome,
  use_cases, current_workaround).
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
        "use_cases": [],
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
        {"skip": ["desired_outcome"]},
        message_text="dunno lah, can skip this one?",
        status="collecting",
        next_field="desired_outcome",
    )
    assert out.skip == ["desired_outcome"]


# --------------------------------------------------------------------------- #
# AC-1204 - a clarifying question is not a skip and sets no field             #
# --------------------------------------------------------------------------- #
def test_clarifying_question_is_not_a_skip(configured):
    out = _extraction(
        configured,
        {"fields": [], "skip": []},
        message_text="what do you mean desired outcome?",
        status="collecting",
        next_field="desired_outcome",
    )
    assert out.skip == []
    assert out.fields == {}


# --------------------------------------------------------------------------- #
# AC-1205 - problem can never be skipped (deterministic guard)                #
# --------------------------------------------------------------------------- #
def test_problem_dropped_from_skip(configured):
    out = _extraction(configured, {"skip": ["problem", "desired_outcome"]})
    assert out.skip == ["desired_outcome"]
    assert "problem" not in out.skip


def test_unknown_skip_key_dropped(configured):
    out = _extraction(configured, {"skip": ["desired_outcome", "not_a_real_key"]})
    assert out.skip == ["desired_outcome"]


def test_ac05_skip_keeps_only_the_three_optional_keys(configured):
    out = _extraction(
        configured,
        {"skip": ["problem", "desired_outcome", "use_cases", "current_workaround",
                  "proposed_solution", "impact", "department"]},
    )
    assert out.skip == ["desired_outcome", "use_cases", "current_workaround"]


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
            "fields": [{"key": "desired_outcome", "value": "faster checkout"}],
            "review_action": "change",
            "change_text": "change the desired outcome to faster checkout",
        },
        message_text="change the desired outcome to faster checkout",
        status="review",
    )
    assert out.review_action == "change"
    assert out.change_text == "change the desired outcome to faster checkout"
    assert out.fields == {"desired_outcome": "faster checkout"}
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
    assert "desired_outcome" not in out.fields


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
            captured={"problem": "a", "desired_outcome": "b"},
        )

    user_content = captured_messages["messages"][1]["content"]
    assert "a; desired_outcome" not in user_content
    lines = [line.strip() for line in user_content.splitlines()]
    assert any(line == "- problem: a" for line in lines)
    assert any(line == "- desired_outcome: b" for line in lines)


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


def test_ac08_department_normaliser_is_gone():
    """AC-08: no Title Case / article stripping for a department any more."""
    assert normalise_field_value("department", "sales manager") == "Sales manager"
    assert normalise_field_value("department", "the manufacturing?") == "The manufacturing"
    assert normalise_field_value("department", "our operations team!") == "Our operations team"


@pytest.mark.parametrize("key", ["desired_outcome", "current_workaround", "problem"])
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("it will boost sales?", "It will boost sales"),
        ("It will reduce our supply chain constraints.", "It will reduce our supply chain constraints."),
        ("faster delivery ??", "Faster delivery"),
        ("  more sales！", "More sales"),
        ("提高销量？", "提高销量"),
        ('"copy into excel first"', "Copy into excel first"),
    ],
)
def test_normaliser_remaining_keys(key, raw, expected):
    assert normalise_field_value(key, raw) == expected


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
    # W2: the punctuation rule (the department example is gone with the department field, AC-07/08).
    assert "question mark" in lower
    assert "manufactuirng" not in lower and "title case" not in lower
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


# --------------------------------------------------------------------------- #
# IDEA-CAPTURE (13 Oct): the canny-style fields                               #
# UAC: idea-capture-fields-13oct-acceptance-criteria.md                       #
# --------------------------------------------------------------------------- #
from app.services.ideation_extractor import IDEATE_EXTRACTION_JSON_SCHEMA  # noqa: E402

_KEYS = {"problem", "desired_outcome", "current_workaround"}


def _key_schema() -> dict:
    return IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["fields"]["items"]["properties"]["key"]


def test_ac01_schema_field_keys_are_exactly_the_three():
    key = _key_schema()
    assert set(key["enum"]) == _KEYS
    assert len(key["enum"]) == 3
    for dropped in ("proposed_solution", "impact", "department"):
        assert dropped not in key["enum"]


def test_ac02_schema_has_required_use_cases_array_of_strings():
    schema = IDEATE_EXTRACTION_JSON_SCHEMA
    assert "use_cases" in schema["required"]
    prop = schema["properties"]["use_cases"]
    assert prop["type"] == "array"
    assert prop["items"] == {"type": "string"}
    desc = prop["description"].lower()
    assert "5" in desc
    assert "never" in desc and "suggest" in desc
    assert set(schema["required"]) == set(schema["properties"])


def test_ac06_schema_descriptions_carry_each_field_meaning():
    fields_desc = IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["fields"]["description"]
    key_desc = _key_schema().get("description", "")
    text = (fields_desc + " " + key_desc).lower()
    assert "business problem" in text
    for key in _KEYS:
        assert key in text
    for dropped in ("proposed_solution", "impact", "department"):
        assert dropped not in text
    assert "desired outcome" in text or "afterwards" in text
    assert "workaround" in text


def test_ac03_use_cases_are_cleaned_deduped_and_capped(configured):
    out = _extraction(
        configured,
        {
            "use_cases": [
                "a dealer checks whether his PO shipped?",
                "  ",
                "",
                '"A project customer checks the site delivery date"',
                "A dealer checks whether his PO shipped",
                "finance checks the invoice date",
                "sales checks stock before quoting",
                "logistics checks the truck slot",
                "a sixth one that must be cut",
            ]
        },
    )
    assert out.use_cases == [
        "A dealer checks whether his PO shipped",
        "A project customer checks the site delivery date",
        "Finance checks the invoice date",
        "Sales checks stock before quoting",
        "Logistics checks the truck slot",
    ]


def test_ac03_use_cases_default_empty_and_tolerate_absent_or_bad_values(configured):
    assert IdeateExtraction().use_cases == []
    assert _extraction(configured, {}).use_cases == []
    assert _extraction(configured, {"use_cases": None}).use_cases == []
    assert _extraction(configured, {"use_cases": ["", "   "]}).use_cases == []


def test_ac04_unknown_field_keys_are_dropped(configured):
    out = _extraction(
        configured,
        {
            "fields": [
                {"key": "problem", "value": "Quotes are priced by hand"},
                {"key": "impact", "value": "Saves an hour a day"},
                {"key": "proposed_solution", "value": "Build a pricing tool"},
                {"key": "department", "value": "Sales"},
                {"key": "current_workaround", "value": "Copy from Excel"},
                {"key": "desired_outcome", "value": "Prices fill in by themselves"},
            ]
        },
    )
    assert out.fields == {
        "problem": "Quotes are priced by hand",
        "current_workaround": "Copy from Excel",
        "desired_outcome": "Prices fill in by themselves",
    }


def test_ac07_fallback_prompt_names_the_four_fields_and_none_of_the_dropped():
    from app.services import ai_prompt_registry

    text = ai_prompt_registry.PROMPT_KEYS["ideate_extractor"].fallback()
    for name in ("problem", "desired_outcome", "use_cases", "current_workaround"):
        assert name in text
    for dropped in ("proposed_solution", "impact", "department"):
        assert dropped not in text, dropped
    lowered = text.lower()
    assert "business problem" in lowered
    assert "empty" in lowered
    assert "use cases" in lowered or "use_cases" in lowered
    assert "never suggest" in lowered or "never invent" in lowered or "do not suggest" in lowered
    assert "suggest" in lowered


# --------------------------------------------------------------------------- #
# IDEA-CAPTURE fix round 2 (reviewer B1): the routing rule lives IN THE       #
# SCHEMA, so an owner-edited production prompt that still describes the old   #
# fields cannot push a solution into `problem`.                               #
# --------------------------------------------------------------------------- #
import re  # noqa: E402


def _schema_texts() -> dict[str, str]:
    props = IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["fields"]
    return {
        "fields.description": props["description"].lower(),
        "key.description": props["items"]["properties"]["key"].get("description", "").lower(),
    }


@pytest.fixture(params=["fields.description", "key.description"])
def schema_text(request) -> str:
    return _schema_texts()[request.param]


def test_b1_problem_never_holds_a_solution_or_wish(schema_text):
    assert re.search(
        r"problem[^.;]{0,100}?\b(never|not|no)\b[^.;]{0,60}?(solution|wish|want)", schema_text
    ) or re.search(
        r"\b(never|not|no)\b[^.;]{0,60}?(solution|wish|want)[^.;]{0,60}?problem", schema_text
    ), schema_text


def test_b1_stated_solution_goes_to_desired_outcome_in_their_words(schema_text):
    assert re.search(r"solution.{0,160}desired_outcome|desired_outcome.{0,160}solution", schema_text), schema_text
    assert any(w in schema_text for w in ("their words", "own words", "their own", "verbatim")), schema_text


def test_b1_a_named_cost_stays_in_problem(schema_text):
    assert re.search(r"cost[^.;]{0,80}problem|problem[^.;]{0,80}cost", schema_text), schema_text


def test_b1_dropped_fields_do_not_exist(schema_text):
    # a stated benefit belongs in problem; a stated team name is not a field
    assert "benefit" in schema_text, schema_text
    assert re.search(
        r"(team|department)[^.;]{0,80}\b(not|no|never)\b|\b(not|no|never)\b[^.;]{0,80}(team|department)",
        schema_text,
    ), schema_text


def test_b1_fallback_prompt_has_no_leftover_department_example():
    from app.services import ai_prompt_registry

    assert "manufactur" not in ai_prompt_registry.PROMPT_KEYS["ideate_extractor"].fallback().lower()


# --------------------------------------------------------------------------- #
# IDEA-CAPTURE fix round 3 (crew live check, gpt-5.4-mini): truthfulness and  #
# language rules live in the fallback prompt AND the schema.                  #
# --------------------------------------------------------------------------- #
def _fallback_text() -> str:
    from app.services import ai_prompt_registry

    return ai_prompt_registry.PROMPT_KEYS["ideate_extractor"].fallback().lower()


def _bullet(text: str, name: str) -> str:
    """The fallback's '- <name>:' bullet, up to the next bullet or blank line."""
    m = re.search(rf"^- {re.escape(name)}\b.*?(?=^\s*-\s|\n\n|\Z)", text, re.S | re.M)
    assert m, f"no '- {name}' bullet in the fallback"
    return m.group(0)


def _field_texts() -> dict[str, str]:
    return _schema_texts()


@pytest.fixture(params=["fields.description", "key.description"])
def field_schema_text(request) -> str:
    return _field_texts()[request.param]


# a) use cases: a named situation, never a noun lifted from the problem
def _assert_use_case_rule(t: str) -> None:
    assert "situation" in t or "scenario" in t, t
    assert any(w in t for w in ("noun", "phrase", "lifted", "copied", "word from")), t
    assert "problem" in t, t


def test_r3a_schema_use_cases_are_named_situations_never_lifted_from_the_problem():
    _assert_use_case_rule(IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["use_cases"]["description"].lower())


def test_r3a_fallback_use_cases_are_named_situations_never_lifted_from_the_problem():
    _assert_use_case_rule(_bullet(_fallback_text(), "use_cases"))


# b) desired outcome: only when a want or solution was stated; a complaint gives none
def _assert_outcome_rule(t: str) -> None:
    assert re.search(r"desired_outcome.{0,400}?(complaint|problem alone|only a problem|alone|only describes)", t, re.S), t
    assert re.search(r"desired_outcome.{0,400}?\b(infer|guess|invent|assume)", t, re.S), t
    assert "empty" in t or "leave it out" in t or "no desired_outcome" in t, t


def test_r3b_schema_desired_outcome_needs_a_stated_want_and_is_never_inferred(field_schema_text):
    _assert_outcome_rule(field_schema_text)


def test_r3b_fallback_desired_outcome_needs_a_stated_want_and_is_never_inferred():
    _assert_outcome_rule(_bullet(_fallback_text(), "desired_outcome"))


# c) current workaround: what they do today, in en / ms / zh phrasings
def test_r3c_schema_workaround_is_what_they_do_today_or_now(field_schema_text):
    assert re.search(r"current_workaround.{0,300}?\b(now|right now)\b", field_schema_text, re.S), field_schema_text
    assert "today" in field_schema_text


def test_r3c_fallback_workaround_carries_multilingual_phrasings():
    b = _bullet(_fallback_text(), "current_workaround")
    for phrase in ("right now we", "sekarang kita", "现在只能"):
        assert phrase in b, (phrase, b)


# d) values and title are written in the language of the message
def test_r3d_schema_title_is_in_the_message_language_never_translated():
    t = IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["title"]["description"].lower()
    assert "language" in t and "translat" in t, t


def test_r3d_schema_fields_values_are_in_the_message_language_never_translated():
    t = IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["fields"]["description"].lower()
    assert "language" in t and "translat" in t, t


def test_r3d_fallback_title_is_in_the_message_language():
    b = _bullet(_fallback_text(), "title")
    assert "language" in b, b


# e) the golden script
def _golden_module():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ideation_capture_golden.py"
    spec = importlib.util.spec_from_file_location("_ideation_capture_golden_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_r3e_golden_set_has_twelve_cases_with_zh_workaround_and_ms_solution_only():
    mod = _golden_module()
    cases = mod.GOLDEN
    assert len(cases) >= 12
    assert len({text for _label, text in cases}) == len(cases)
    cjk = re.compile(r"[一-鿿]")
    assert any(cjk.search(text) and ("现在" in text or "目前" in text) for _l, text in cases), "zh workaround case"
    assert any(
        ("solution" in label.lower())
        and ("ms" in label.lower().split() or "malay" in label.lower())
        and not cjk.search(text)
        for label, text in cases
    ), "Malay solution-only case (label names ms and solution)"


def test_r3e_golden_docstring_names_the_migration_that_publishes_the_prompt():
    doc = (_golden_module().__doc__ or "")
    assert "idea_capture_fields_0001" in doc
    assert "--from-db" in doc


# --------------------------------------------------------------------------- #
# IDEA-CAPTURE fix round 4: a stated "what we do now" sentence goes to        #
# current_workaround and is not kept in problem (zh crew check).              #
# --------------------------------------------------------------------------- #
def _workaround_passages() -> dict[str, str]:
    fields = IDEATE_EXTRACTION_JSON_SCHEMA["properties"]["fields"]["description"].lower()
    return {
        "schema.fields.description": fields[fields.index("current_workaround"):],
        "fallback.current_workaround": _bullet(_fallback_text(), "current_workaround"),
    }


@pytest.fixture(params=["schema.fields.description", "fallback.current_workaround"])
def workaround_passage(request) -> str:
    return _workaround_passages()[request.param]


def test_r4_what_they_do_now_goes_to_workaround_and_not_into_problem(workaround_passage):
    t = workaround_passage
    assert re.search(
        r"\b(?:not|never|leaves? out|left out|kept out)\b[^.]{0,120}?problem"
        r"|problem[^.]{0,120}?\b(?:not|never|leaves? out|left out|kept out)\b",
        t,
    ), t
    assert "现在只能" in t, t
    # Crew live check (13 Oct): a list of phrasings alone left the coping sentence inside
    # problem 3/5 on gpt-5.4-mini. What fixed it is a WORKED split: a zh message where the
    # coping sentence is shown landing in current_workaround, problem keeping the rest.
    assert re.search(r"current_workaround\W{0,6}(?:=|is)\s*\W?现在只能", t), t
