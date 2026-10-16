"""CHAT-FIND parser / prompt contract (P1-P3).

Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md sections 2.4 and 4.

* P1 the strict output schema declares `find`, and a recorded emission without it still passes.
* P2 the `find_filters` prompt variable renders the FIND rule plus one line per registry row.
* P3 `chatbot_find_prompt.publish_find_block(session, from_version=N)` copies a version word for
  word, adds `{{find_filters}}` inside the policy markers, never moves a label, is idempotent.

Postgres only (`tests/_pg_fixture.py::blank_session`): the publish writes rows.
"""
from __future__ import annotations

import importlib

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services import chatbot_parser_prompt as parser_prompt
from app.services.chatbot.head import parser as head_parser
from tests._pg_fixture import blank_session

PROMPT_NAME = "chatbot_semantic_parser"
TOKEN = "{{find_filters}}"
BEGIN, END = parser_prompt.BLOCKS_BEGIN, parser_prompt.BLOCKS_END


def _find_module():
    return importlib.import_module("app.services.chatbot_find_prompt")


def _product_find():
    return importlib.import_module("app.services.product_find")


# --------------------------------------------------------------------------- P1


def test_p1_schema_declares_find_as_a_strict_required_object_or_null() -> None:
    schema = head_parser.PARSE_OUTPUT_JSON_SCHEMA
    assert "find" in schema["required"]
    find = schema["properties"]["find"]
    assert set(find["type"]) == {"object", "null"}
    assert find["additionalProperties"] is False
    assert find["required"] == ["filters"]
    filters = find["properties"]["filters"]
    assert filters["type"] == "array"
    item = filters["items"]
    assert item["additionalProperties"] is False
    # Round 3: a typed value (code, number + unit, enum) rides `value` + `unit`.
    # SEAT-AS-PRODUCT: the parser states the file word of THIS message (null when none).
    assert set(item["required"]) == {"key", "value", "unit", "file_word", "kind"}
    assert set(item["properties"]["file_word"]["type"]) == {"string", "null"}
    assert set(item["properties"]["unit"]["type"]) == {"string", "null"}
    assert item["properties"]["key"]["type"] == "string"
    assert set(item["properties"]["value"]["type"]) == {"string", "number", "null"}


def test_p1_find_is_tolerated_absent() -> None:
    assert "find" in head_parser.TOLERATED_ABSENT


# --------------------------------------------------------------------------- P2


def test_p2_find_filters_variable_renders_the_rule_and_every_filter_line() -> None:
    from app.services import chatbot_prompt_vars

    registry = _product_find()
    assert "find_filters" in chatbot_prompt_vars.VARIABLES
    with blank_session() as db:
        text = chatbot_prompt_vars.VARIABLES["find_filters"].render(db)
    assert "find" in text.lower()
    # SEAT-AS-PRODUCT: the relation picks WHICH products; the domain is the ask's own.
    assert "domain_hint is master_products" not in text
    assert "domain_hint and intent_hint included" in text
    for key, row in registry.FILTERS.items():
        assert key in text
        assert row.vocabulary in text
    assert "fits" in text


# --------------------------------------------------------------------------- P3


def _seed(db, version: int, template: str, variables=None) -> AIPromptVersion:
    row = AIPromptVersion(
        name=PROMPT_NAME, version=version, type="text", template=template, variables=variables or []
    )
    db.add(row)
    db.flush()
    return row


def _versions(db) -> list[AIPromptVersion]:
    return (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .order_by(AIPromptVersion.version)
        .all()
    )


def _version_of(result) -> int:
    return getattr(result, "version", result)


WITH_MARKERS = f"head text\n{BEGIN}\nPOLICY ONE\nPOLICY TWO\n{END}\ntail text\n"


def test_p3_inserts_the_variable_before_the_end_marker_keeping_every_other_byte() -> None:
    publish = _find_module().publish_find_block
    with blank_session() as db:
        source = _seed(db, 5, WITH_MARKERS, ["current_date"])
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=source.id))
        db.flush()

        result = publish(db, from_version=5)

        assert result is not None
        rows = _versions(db)
        assert [r.version for r in rows] == [5, 6]
        assert _version_of(result) == 6
        new = rows[-1]
        before_end, after_end = WITH_MARKERS.split(END)
        assert TOKEN in new.template
        assert new.template.index(TOKEN) < new.template.index(END)
        assert new.template.startswith(before_end), "everything before the end marker is kept"
        assert new.template.endswith(END + after_end), "the end marker and what follows are kept"
        assert new.template.count(TOKEN) == 1
        assert "find_filters" in new.variables
        assert "current_date" in new.variables
        # The source row is never edited.
        assert rows[0].template == WITH_MARKERS


def test_p3_appends_a_marker_pair_when_the_text_has_none() -> None:
    publish = _find_module().publish_find_block
    plain = "a prompt with no policy markers\nsecond line\n"
    with blank_session() as db:
        _seed(db, 3, plain)
        assert publish(db, from_version=3) is not None
        new = _versions(db)[-1]
        assert new.version == 4
        assert new.template.startswith(plain)
        assert BEGIN in new.template and END in new.template
        assert new.template.index(BEGIN) < new.template.index(TOKEN) < new.template.index(END)
        assert "find_filters" in new.variables


def test_p3_the_new_version_is_unlabelled_and_no_label_moves() -> None:
    publish = _find_module().publish_find_block
    with blank_session() as db:
        source = _seed(db, 1, WITH_MARKERS)
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=source.id))
        db.add(AIPromptLabel(name=PROMPT_NAME, label="staging", version_id=source.id))
        db.flush()
        publish(db, from_version=1)
        new = _versions(db)[-1]
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert {(l.label, l.version_id) for l in labels} == {
            ("production", source.id),
            ("staging", source.id),
        }
        assert all(l.version_id != new.id for l in labels)


def test_p3_a_second_call_publishes_nothing() -> None:
    publish = _find_module().publish_find_block
    with blank_session() as db:
        _seed(db, 1, WITH_MARKERS)
        assert publish(db, from_version=1) is not None
        assert publish(db, from_version=1) is None
        assert publish(db, from_version=2) is None, "the new version already carries the variable"
        assert [r.version for r in _versions(db)] == [1, 2]


def test_p3_an_unknown_version_is_none() -> None:
    publish = _find_module().publish_find_block
    with blank_session() as db:
        _seed(db, 1, WITH_MARKERS)
        assert publish(db, from_version=99) is None
        assert [r.version for r in _versions(db)] == [1]

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
