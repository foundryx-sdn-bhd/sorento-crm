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
    # The owner's words come first, unchanged; the continuation rule (owner ruling 12 Oct) sits
    # between them and the markers.
    assert head.startswith(OWNER_TEXT.rstrip("\n")) and tail == "\ntail\n"


def test_p3_idempotent_and_a_text_that_has_specs_publishes_nothing(db):
    """RED: a second run publishes nothing; a production that already carries the block too."""
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT)
    assert pv.publish_specs_block(db) == 87
    assert pv.publish_specs_block(db) is None
    assert db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY).count() == 2


def test_p3b_production_already_with_specs_keeps_its_block(db):
    """A text that already carries `{{specs}}` keeps exactly one (owner ruling 12 Oct: it still
    gets the continuation rule, so a version is published)."""
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT + "\n{{specs}}\n")
    version = pv.publish_specs_block(db)
    assert _row(db, version).template.count("{{specs}}") == 1


def test_p4_the_published_version_renders_the_size_line(db):
    """RED: the new version, rendered, shows the parser the `Specification size` line."""
    from app.services import ai_prompt_registry
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT)
    version = pv.publish_specs_block(db)
    rendered, _ = ai_prompt_registry.render(db, KEY, current_date="TODAY", override_version_id=_row(db, version).id)
    assert any(line.startswith("Specification size") for line in rendered.splitlines()), rendered[-2000:]


# --------------------------------------------------------------------------- owner ruling 12 Oct: shapes + continuation

PROD_BLOCK = (
    f"{BLOCKS_BEGIN}\n"
    "Domain inventory: stock.\n"
    'Specification dim_length ("Length"): a number in mm. Words: length, long.\n'
    'Specification dim_width ("Width"): a number in mm. Words: width, wide.\n'
    "Product types (the category entity keeps these, never a specification): wash basin.\n"
    f"{BLOCKS_END}\n"
)


def test_p5_prod_shape_static_spec_lines_become_the_live_block(db):
    """Prod v52 (crew, 12 Oct): the markers hold STATIC rendered Specification lines with no size
    line. They become `{{specs}}` (the live registry, size line included); the Domain line and
    every word outside the markers stay."""
    from app.services import ai_prompt_registry
    from app.services import chatbot_prompt_vars as pv

    _production(db, OWNER_TEXT + PROD_BLOCK, version=52)
    version = pv.publish_specs_block(db)
    new = _row(db, version).template
    head, rest = new.split(BLOCKS_BEGIN, 1)
    inside, _tail = rest.split(BLOCKS_END, 1)
    assert "Domain inventory: stock." in inside
    assert "{{specs}}" in inside and "Specification dim_length" not in inside, inside
    assert "Product types (the category" not in inside, inside
    assert head.startswith(OWNER_TEXT.rstrip("\n"))
    rendered, _ = ai_prompt_registry.render(db, KEY, current_date="TODAY", override_version_id=_row(db, version).id)
    assert sum(1 for ln in rendered.splitlines() if ln.startswith("Specification size")) == 1


def test_p6_from_version_publishes_off_that_version_not_production(db):
    """Owner/crew: production may be v60 (already carrying `{{specs}}`) while the line to publish
    is v86 or v52. `from_version` names the source; the label never moves."""
    from app.services import chatbot_prompt_vars as pv

    prod = _production(db, OWNER_TEXT + "\n{{specs}}\n", version=60)
    db.add(AIPromptVersion(id=str(uuid.uuid4()), name=KEY, version=86, type="text",
                           template="ZZT v86 text.\n", variables=[], config_json={}, commit_message="owner"))
    db.flush()
    version = pv.publish_specs_block(db, from_version=86)
    assert version == 87
    new = _row(db, 87).template
    assert new.startswith("ZZT v86 text.") and "{{specs}}" in new
    assert _label(db) == prod.id


def test_p7_the_continuation_rule_rides_every_published_version(db):
    """Owner ruling (12 Oct): the parser decides that "more" (any phrasing or language) continues
    the same list, through the `continuation` key the schema already carries. The rule is added
    once, also to a text that already had `{{specs}}`."""
    from app.services import chatbot_prompt_vars as pv
    from app.services.chatbot_parser_prompt import CONTINUATION_RULE

    _production(db, OWNER_TEXT + "\n{{specs}}\n", version=60)
    version = pv.publish_specs_block(db)
    assert version is not None, "a text with {{specs}} but no continuation rule still needs the rule"
    new = _row(db, version).template
    assert new.count(CONTINUATION_RULE.strip()) == 1
    assert pv.publish_specs_block(db, from_version=version) is None


def test_p8_the_rule_rides_the_published_version_not_the_code_constant():
    """The rule defines `continuation`; it reaches the parser through the published version.
    The code constant stays as measured (`test_parser_prompt_budget` pins its size and tail)."""
    from app.services import chatbot_prompt_vars as pv
    from app.services.chatbot_parser_prompt import CONTINUATION_RULE, SEMANTIC_PARSER_PROMPT

    assert '"continuation"' in CONTINUATION_RULE
    assert CONTINUATION_RULE.strip() not in SEMANTIC_PARSER_PROMPT
    assert pv._with_continuation_rule(SEMANTIC_PARSER_PROMPT).count(CONTINUATION_RULE.strip()) == 1


def test_p9_the_size_and_class_rule_rides_every_published_version(db):
    """Crew live check on 045a0fea: "600mm 的洗手盆有货吗？" left the class word untranslated
    ("Couldn't find: 洗手盆"), "600mm 的面盆有现货吗？" read 600 as a product code, and
    "ada sinki dapur ...?" missed "sinki dapur". The parser is told, in the published text, that
    a size said in any language is its own specification and that a class word in any language
    carries its English class word as canonical_code. Wording only; no code keyword rule."""
    from app.services import chatbot_prompt_vars as pv
    from app.services.chatbot_parser_prompt import SIZE_AND_CLASS_RULE

    assert "canonical_code" in SIZE_AND_CLASS_RULE and '"size"' in SIZE_AND_CLASS_RULE
    _production(db, OWNER_TEXT, version=52)
    version = pv.publish_specs_block(db)
    new = _row(db, version).template
    assert new.count(SIZE_AND_CLASS_RULE.strip()) == 1
    assert pv.publish_specs_block(db, from_version=version) is None


def test_p10_the_size_and_class_rule_teaches_no_language():
    """Owner rule (11 Oct): no language teaching in the prompt. The rule states the business rule
    (a class word in any language carries its English class word as canonical_code; a size is
    its own specification) and names no translation; the resolver's translation data does that."""
    import unicodedata

    from app.services.chatbot_parser_prompt import SIZE_AND_CLASS_RULE

    assert all(ord(ch) < 128 for ch in SIZE_AND_CLASS_RULE), "a non-English word is a translation example"
    for word in ("sinki", "dapur", "mangkuk", "tandas", "->"):
        assert word not in SIZE_AND_CLASS_RULE, word
    assert "canonical_code" in SIZE_AND_CLASS_RULE and '"size"' in SIZE_AND_CLASS_RULE
    assert unicodedata.normalize("NFKC", SIZE_AND_CLASS_RULE) == SIZE_AND_CLASS_RULE


def test_p11_the_size_rule_holds_on_every_ask_about_the_products_promotions_included():
    """Crew round 5 on b962ddf7 (v101): "any 600mm basin promo" lost the 600mm intermittently and
    answered every basin promotion. The rule must say, as a business rule, that the size entity
    stays whatever is asked about those products, a promotion included, and that it is never
    dropped."""
    from app.services.chatbot_parser_prompt import SIZE_AND_CLASS_RULE

    rule = SIZE_AND_CLASS_RULE.lower()
    assert "promotion" in rule, SIZE_AND_CLASS_RULE
    assert "never dropped" in rule or "never drop" in rule, SIZE_AND_CLASS_RULE


def test_p12_the_size_rule_quotes_no_customer_message():
    """Crew round 6 (owner rule "generalise, not one scenario"): the rule states the business rule
    and quotes no test message ("any 600mm basin promo"); a quoted string is only a field name or
    value the parser writes."""
    import re

    from app.services.chatbot_parser_prompt import SIZE_AND_CLASS_RULE

    allowed = {"specification", "size", "category"}
    quoted = re.findall(r'"([^"]*)"', SIZE_AND_CLASS_RULE)
    assert set(quoted) <= allowed, quoted
    assert "promotion" in SIZE_AND_CLASS_RULE.lower()
