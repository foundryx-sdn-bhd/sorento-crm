"""SPEC-PROMO-PARAMS hand test step 1 (owner, 12 Oct 2026): the specification vocabulary reaches
the parser on the production line.

The `Specification size` line reaches the parser only through the rendered `{{specs}}` block.
The owner's v84-v86 texts carry no `{{specs}}` (only v60 and v83 do), so on v86 the parser never
saw the size key. `chatbot_prompt_vars.publish_specs_block` publishes the PRODUCTION text with the
block added, as a new UNLABELLED version (the owner promotes it, R3), and never rewrites a word of
it. Idempotent.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END
from tests.chatbot.test_lane_require import _seed_registry

KEY = "chatbot_semantic_parser"
OWNER_TEXT = "ZZT owner parser text v86.\n== MESSAGE TYPE ==\nkeep every word {{domains}}\n"


@pytest.fixture()
def db(session_factory):
    session = session_factory()
    _seed_registry(session)
    session.flush()
    yield session
    session.rollback()
    session.close()


def _production(db, template: str, version: int = 86) -> AIPromptVersion:
    row = AIPromptVersion(
        id=str(uuid.uuid4()), name=KEY, version=version, type="text", template=template,
        variables=["domains"], config_json={}, commit_message="owner",
    )
    db.add(row)
    db.flush()
    db.add(AIPromptLabel(id=str(uuid.uuid4()), name=KEY, label="production", version_id=row.id))
    db.flush()
    return row


def _row(db, version: int) -> AIPromptVersion:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()


def _label(db) -> str:
    return db.query(AIPromptLabel).filter(AIPromptLabel.name == KEY, AIPromptLabel.label == "production").one().version_id


def test_p1_production_without_specs_gets_a_new_unlabelled_version_with_the_block(db):
    """RED: there is no publisher; v86 stays without `{{specs}}`."""
    from app.services import chatbot_prompt_vars as pv

    prod = _production(db, OWNER_TEXT)
    version = pv.publish_specs_block(db)
    assert version == 87
    new = _row(db, 87)
    assert new.template.startswith(OWNER_TEXT.rstrip("\n")), "the owner's words are kept, in order"
    assert "{{specs}}" in new.template
    assert new.template.count("{{specs}}") == 1
    assert "specs" in (new.variables or [])
    assert _label(db) == prod.id, "the label stays where the owner put it"


def test_p2_a_text_with_policy_markers_gets_specs_inside_them(db):
    """RED: with the markers present, the block goes inside them, before the end marker."""
    from app.services import chatbot_prompt_vars as pv

    text = f"{OWNER_TEXT}{BLOCKS_BEGIN}\n{{{{domains_detail}}}}\n{BLOCKS_END}\ntail\n"
    _production(db, text)
    version = pv.publish_specs_block(db)
    new = _row(db, version)
    head, rest = new.template.split(BLOCKS_BEGIN, 1)
    inside, tail = rest.split(BLOCKS_END, 1)
    assert "{{specs}}" in inside and "{{domains_detail}}" in inside
    assert head == OWNER_TEXT and tail == "\ntail\n"


def test_p3_idempotent_and_a_text_that_has_specs_publishes_nothing(db):
    """RED: a second run publishes nothing; a production that already carries the block too."""
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT)
    assert pv.publish_specs_block(db) == 87
    assert pv.publish_specs_block(db) is None
    assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == 2


def test_p3b_production_already_with_specs_publishes_nothing(db):
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT + "\n{{specs}}\n")
    assert pv.publish_specs_block(db) is None


def test_p4_the_published_version_renders_the_size_line(db):
    """RED: the new version, rendered, shows the parser the `Specification size` line."""
    from app.services import ai_prompt_registry
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT)
    version = pv.publish_specs_block(db)
    rendered, _ = ai_prompt_registry.render(db, KEY, current_date="TODAY", override_version_id=_row(db, version).id)
    assert any(line.startswith("Specification size") for line in rendered.splitlines()), rendered[-2000:]
