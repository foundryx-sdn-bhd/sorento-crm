"""CLARIFY-POLICY AC-149 (Q10): the parser's `continuation` definition ("SAME SUBJECT OR A NEW
ONE", word for word from INTENT-FRAMES #1473) is a constant, `CONTINUATION_ADDENDUM` in
`app/services/chatbot_parser_prompt.py`, and migration `clp_0002_parser_continuation` publishes it
as a new UNLABELLED `chatbot_semantic_parser` version without moving `production`
(pattern: `test_mem_0003_publishes_history_words.py`)."""
from __future__ import annotations

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session
from tests.chatbot.test_mem_0003_publishes_history_words import _parser_versions, _production, _seeded
from tests.chatbot.test_rearch_s12_config_ships import _load

#: The first paragraph of #1473's INTENT_FRAMES_ADDENDUM, verbatim.
PARAGRAPH = '''SAME SUBJECT OR A NEW ONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"continuation" is true when the current message asks about the SAME subject as the
previous turn without naming it again: a follow-up about what was just shown ("and its
price?", "cert?", "any incoming?", "check stock" right after a product answer, "how about
delivery?"). It is false when the message names a subject of its own (a code, a name, a
category), asks for a report or a list that needs no subject ("low stock report", "top
selling items", "my outstanding"), or starts something unrelated. A pick off a numbered
list, "yes" or "no" is false. When continuation is true, entity_op is "reuse".'''


def test_the_addendum_constant_carries_the_paragraph_word_for_word():
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    assert PARAGRAPH in CONTINUATION_ADDENDUM
    assert "TOPIC RESET" not in CONTINUATION_ADDENDUM  # only the continuation paragraph


def test_the_migration_publishes_one_unlabelled_version_and_production_does_not_move():
    from app.services.chatbot_parser_prompt import CONTINUATION_ADDENDUM

    with blank_session() as db:
        _seeded(db)
        production = _production(db)
        old = AIPromptVersion(
            name="chatbot_semantic_parser", version=99, type="text", template="the previous words",
            variables=[], config_json={}, commit_message="previous",
        )
        db.add(old)
        db.commit()
        mig = _load("clp_0002_parser_continuation.py")
        before = set(_parser_versions(db))

        version = mig.publish(db)
        assert version is not None and version > 99
        assert mig.publish(db) is None, "a second run publishes nothing"

        (row,) = [r for vid, r in _parser_versions(db).items() if vid not in before]
        assert CONTINUATION_ADDENDUM.strip() in row.template
        assert PARAGRAPH in row.template
        assert _production(db) == production, "the production label moved"
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.version_id == row.id).all()
        assert labels == [], "the new version must be unlabelled"
