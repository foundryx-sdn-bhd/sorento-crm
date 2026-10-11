"""CLARIFY-POLICY AC-149 (Q10): the parser's `continuation` definition ("SAME SUBJECT OR A NEW
ONE", amended from INTENT-FRAMES #1473, crew ruling 11 Oct) is a constant, `CONTINUATION_ADDENDUM` in
`app/services/chatbot_parser_prompt.py`, and migration `clp_0002_parser_continuation` published the first wording and `clp_0003_parser_continuation_v2` publishes the amended one
as a new UNLABELLED `chatbot_semantic_parser` version without moving `production`
(pattern: `test_mem_0003_publishes_history_words.py`)."""
from __future__ import annotations

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session
from tests.chatbot.test_mem_0003_publishes_history_words import _parser_versions, _production, _seeded
from tests.chatbot.test_rearch_s12_config_ships import _load

PARAGRAPH = '''SAME SUBJECT OR A NEW ONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"continuation" is true when the current message asks about the SAME subject as the
previous turn without naming it again: a short follow-up about what was just shown ("and its
price?", "cert?", "eta?", "any incoming?", "check stock" right after a product answer). It is
false when the message names a subject of its own (a code, a name, a category), asks for a
report or a list that needs no subject ("low stock report", "top selling items", "my
outstanding"), asks a different kind of thing with its own scope such as a date window or a
whole list ("delivery today", "orders this week", "all deliveries"), or starts something
unrelated. A pick off a numbered list, "yes" or "no" is false. When continuation is true,
entity_op is "reuse".'''


def test_the_addendum_constant_carries_the_amended_paragraph_exactly():
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    assert PARAGRAPH in CONTINUATION_ADDENDUM
    assert "how about delivery?" not in CONTINUATION_ADDENDUM
    assert "TOPIC RESET" not in CONTINUATION_ADDENDUM  # only the continuation paragraph


def _publishes_unlabelled(file_name: str, text_check) -> None:
    with blank_session() as db:
        _seeded(db)
        production = _production(db)
        db.add(
            AIPromptVersion(
                name="chatbot_semantic_parser", version=99, type="text", template="the previous words",
                variables=[], config_json={}, commit_message="previous",
            )
        )
        db.commit()
        mig = _load(file_name)
        before = set(_parser_versions(db))

        version = mig.publish(db)
        assert version is not None and version > 99
        assert mig.publish(db) is None, "a second run publishes nothing"

        (row,) = [r for vid, r in _parser_versions(db).items() if vid not in before]
        text_check(row.template)
        assert _production(db) == production, "the production label moved"
        assert db.query(AIPromptLabel).filter(AIPromptLabel.version_id == row.id).all() == []


def test_clp_0002_still_publishes_an_unlabelled_version():
    _publishes_unlabelled("clp_0002_parser_continuation.py", lambda t: None)


def test_each_revision_publishes_its_own_frozen_block_not_the_code_constant():
    """Reviewer S1: clp_0002 keeps the first wording once the constant is amended, and clp_0003's
    block is its own copy, so a later edit to the constant changes neither."""
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    first = _load("clp_0002_parser_continuation.py")
    assert "how about\ndelivery?" in first.CONTINUATION_ADDENDUM
    assert first.CONTINUATION_ADDENDUM.strip("\n") in first.with_continuation_block("base text")
    amended = _load("clp_0003_parser_continuation_v2.py")
    assert PARAGRAPH in amended.CONTINUATION_ADDENDUM
    assert amended.CONTINUATION_ADDENDUM == CONTINUATION_ADDENDUM  # today's constant, until it moves


def test_clp_0003_publishes_the_amended_paragraph_as_a_new_unlabelled_version():
    base = _load("clp_0003_parser_continuation_v2.py").SOURCE.read_text(encoding="utf-8")

    def check(template: str) -> None:
        assert PARAGRAPH in template
        # v56's own domain-hint examples carry the phrase; the amended block adds no copy of it.
        assert template.count("how about delivery?") == base.count("how about delivery?")

    _publishes_unlabelled("clp_0003_parser_continuation_v2.py", check)


def test_clp_0003_chains_onto_clp_0002_and_clp_0002_is_untouched():
    mig = _load("clp_0003_parser_continuation_v2.py")
    assert mig.revision == "clp_0003_parser_continuation_v2"
    assert mig.down_revision == "clp_0002_parser_continuation"
    assert len(mig.revision) <= 32
