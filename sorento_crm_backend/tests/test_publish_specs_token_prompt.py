"""SPEC-REGISTRY-UI Phase 2 red tests: `scripts/publish_specs_token_prompt.py` (contract
C6, UAC AC-1.4 prerequisite: the published parser text must carry `{{specs}}` or a
registry edit never reaches the bot).

`publish(session, from_version=None) -> int | None`: finds the ONE run of baked
`Specification ...` lines (plus an optional `Product types (` line) in the base version,
replaces it with `{{specs}}`, publishes the next version UNLABELLED, never moves a label.

Red until the script exists (ModuleNotFoundError at import, the right reason).
Postgres only (`tests._pg_fixture.blank_session`); every row is seeded here.
"""
from __future__ import annotations

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session

NAME = "chatbot_semantic_parser"

SPEC_A = 'Specification trap_length ("Trap outlet length"): a number in mm. Words: trap.'
SPEC_B = 'Specification finish ("Finish"): choices Chrome = chrome; Brushed brass = brushed_brass.'
TYPES = "Product types (the category entity keeps these, never a specification): Basin, Closet."


def _template(*middle: str) -> str:
    return "\n".join(["Intro line", *middle, "Outro {{current_date}}"]) + "\n"


ONE_RUN = _template(SPEC_A, SPEC_B, TYPES)


def _publish():
    from scripts.publish_specs_token_prompt import publish

    return publish


def _version(db, number: int, template: str, *, variables=None, config=None) -> AIPromptVersion:
    row = AIPromptVersion(
        name=NAME,
        version=number,
        type="text",
        template=template,
        variables=["current_date"] if variables is None else variables,
        config_json=config,
        commit_message=f"zzt v{number}",
    )
    db.add(row)
    db.flush()
    return row


def _production(db, version: AIPromptVersion) -> AIPromptLabel:
    label = AIPromptLabel(name=NAME, label="production", version_id=version.id)
    db.add(label)
    db.commit()
    return label


def _versions(db) -> list[AIPromptVersion]:
    db.expire_all()
    return (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.name == NAME)
        .order_by(AIPromptVersion.version)
        .all()
    )


def _labels(db) -> list[tuple[str, str]]:
    db.expire_all()
    return sorted(
        (l.label, l.version_id) for l in db.query(AIPromptLabel).filter(AIPromptLabel.name == NAME)
    )


def test_one_run_is_replaced_by_the_specs_token_and_published_next():
    with blank_session() as db:
        prod = _version(db, 1, ONE_RUN, config={"temperature": 0})
        _production(db, prod)

        new = _publish()(db)

        assert new == 2
        v2 = _versions(db)[-1]
        assert v2.version == 2
        assert v2.template == "Intro line\n{{specs}}\nOutro {{current_date}}\n"
        assert "Specification " not in v2.template and "Product types" not in v2.template
        assert v2.variables == ["current_date", "specs"]
        assert v2.type == "text"
        assert v2.config_json == {"temperature": 0}


def test_a_run_without_the_product_types_line_is_still_one_run():
    with blank_session() as db:
        _production(db, _version(db, 1, _template(SPEC_A, SPEC_B)))

        assert _publish()(db) == 2

        assert _versions(db)[-1].template == "Intro line\n{{specs}}\nOutro {{current_date}}\n"


def test_the_new_version_is_unlabelled_and_the_production_label_does_not_move():
    with blank_session() as db:
        prod = _version(db, 1, ONE_RUN)
        _production(db, prod)
        before = _labels(db)

        new = _publish()(db)
        assert new == 2

        assert _labels(db) == before == [("production", prod.id)]
        v2 = _versions(db)[-1]
        assert all(version_id != v2.id for _, version_id in _labels(db))


def test_a_second_call_is_idempotent_and_returns_none():
    with blank_session() as db:
        _production(db, _version(db, 1, ONE_RUN))

        assert _publish()(db) == 2
        assert _publish()(db) is None

        assert [v.version for v in _versions(db)] == [1, 2]


def test_variables_are_the_base_variables_plus_specs_without_duplicates():
    with blank_session() as db:
        _production(db, _version(db, 1, ONE_RUN, variables=["current_date", "domains"]))

        _publish()(db)

        assert _versions(db)[-1].variables == ["current_date", "domains", "specs"]


def test_zero_baked_blocks_is_refused():
    with blank_session() as db:
        _production(db, _version(db, 1, _template("just a line", "another line")))

        with pytest.raises(SystemExit) as raised:
            _publish()(db)

        assert "refused: no baked specification block" in str(raised.value)
        assert [v.version for v in _versions(db)] == [1]


def test_two_baked_blocks_is_refused():
    with blank_session() as db:
        template = _template(SPEC_A, "a line between the two blocks", SPEC_B, TYPES)
        _production(db, _version(db, 1, template))

        with pytest.raises(SystemExit) as raised:
            _publish()(db)

        assert "refused: 2 baked specification blocks" in str(raised.value)
        assert [v.version for v in _versions(db)] == [1]


def test_a_base_that_already_carries_the_specs_token_is_refused():
    with blank_session() as db:
        _production(db, _version(db, 1, _template("{{specs}}"), variables=["current_date", "specs"]))

        with pytest.raises(SystemExit) as raised:
            _publish()(db)

        assert "refused" in str(raised.value)
        assert [v.version for v in _versions(db)] == [1]


def test_from_version_is_honoured_over_the_production_label():
    other_spec = 'Specification bowls ("Number of bowls"): a number.'
    with blank_session() as db:
        prod = _version(db, 1, ONE_RUN)
        _production(db, prod)
        unlabelled = _version(
            db,
            2,
            _template("{{brands}}", other_spec),
            variables=["current_date", "brands"],
        )
        db.commit()

        new = _publish()(db, from_version=unlabelled.version)

        assert new == 3
        v3 = _versions(db)[-1]
        assert v3.template == "Intro line\n{{brands}}\n{{specs}}\nOutro {{current_date}}\n"
        assert v3.variables == ["current_date", "brands", "specs"]
        assert _labels(db) == [("production", prod.id)]


def test_from_version_that_does_not_exist_publishes_nothing():
    with blank_session() as db:
        _production(db, _version(db, 1, ONE_RUN))

        try:
            result = _publish()(db, from_version=99)
        except SystemExit:
            result = None

        assert result is None
        assert [v.version for v in _versions(db)] == [1]


# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
