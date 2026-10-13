"""PROMPT-PROD-LABEL: the candidate parser body (AC-1..AC-7) and its publish migration.

Red until `alembic/data/chatbot_semantic_parser.candidate-20261010.txt` and
`alembic/versions/ppl_0001_parser_candidate.py` exist. Postgres only for the publish tests.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END
from tests._pg_fixture import blank_session
from tests.chatbot._order_broaden_prompt import ADDENDUM as ORDER_BROADEN_FILE, ANCHOR
from tests.chatbot._rogue_convo_prompt import (
    ANCHOR_BARE_COUNT,
    ANCHOR_CODE_IN_DESCRIPTION,
    BARE_COUNT,
    CODE_IN_DESCRIPTION,
)

BACKEND = Path(__file__).resolve().parents[2]
CANDIDATE_FILE = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.candidate-20261010.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "ppl_0001_parser_candidate.py"
PROMPT_NAME = "chatbot_semantic_parser"

REQUIRED_MARKERS = [
    "CUSTOMER ACCOUNT NUMBER",
    "LOW STOCK REPORT FILTERS",
    "TRAP SPECS",
    "PRODUCT CODE: entities[].canonical_code",
    '"po_refs"',
    "sales_ranking",
    "ranking_drill",
    "== PROMOTION KIND ==",
    "A MISS reply never moves the domain either",
    "A COUNT WITH NO CODE BESIDE IT",
    "A DESCRIPTION WITH A CODE INSIDE IT",
]


def _text() -> str:
    return CANDIDATE_FILE.read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("zzt_ppl_0001_parser_candidate", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


# ---- static -------------------------------------------------------------------------


@pytest.mark.parametrize("marker", REQUIRED_MARKERS)
def test_candidate_file_has_marker_and_migration_lists_it(marker: str) -> None:
    assert CANDIDATE_FILE.is_file(), CANDIDATE_FILE
    assert marker in _text()
    assert marker in _module().MARKERS


OWNER_EDITS = [
    '  - "installation guide", "installation manual" → attachment_type "installation guide"',
    "photo|technical drawing|3D model|certificate|installation guide",
    "(photo/drawing/installation guide)",
    "forwarder, consignee, warehouse, free days",
    "← warehouse, which warehouse it goes to",
    "product code, description; product details, dimension, size",
]


@pytest.mark.parametrize("snippet", OWNER_EDITS)
def test_owner_edits_present_verbatim(snippet: str) -> None:
    assert snippet in _text()


def test_attachment_type_codes_use_the_grounding_spelling() -> None:
    # app/services/chatbot/head/grounding.py DOCUMENT_TYPE_CODES accepts only "installation guide";
    # on the replay copy the underscore form made the bot ask "Which kind of file?".
    text = _text()
    assert 'attachment_type "installation_guide"' not in text
    assert "installation_guide" not in text


def test_candidate_keeps_the_constant_line_and_gains_the_owner_word() -> None:
    # The constant's newer wording ("sold the most", #1447) stays; the owner's word is added.
    assert "certificate, installation guide, forms, shipment, GRN, sold the most" in (
        CANDIDATE_FILE.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "obsolete",
    [
        "Never emit BOTH an entity and a reference_position",
        '"SPO", "shipment", "container" -> ["SPO"]',
        "null (the default, delivered)",
    ],
)
def test_obsolete_text_absent(obsolete: str) -> None:
    assert obsolete not in _text()


def test_placeholders_and_addenda_exactly_once_after_their_anchors() -> None:
    text = _text()
    assert text.count("{{current_date}}") == 1
    assert "<<<CHATBOT POLICY BLOCKS>>>" not in text
    for addendum_file, anchor in (
        (ORDER_BROADEN_FILE, ANCHOR),
        (CODE_IN_DESCRIPTION, ANCHOR_CODE_IN_DESCRIPTION),
        (BARE_COUNT, ANCHOR_BARE_COUNT),
    ):
        addendum = addendum_file.read_text(encoding="utf-8")
        assert text.count(addendum) == 1, addendum_file.name
        assert anchor + addendum in text, f"{addendum_file.name} not directly after its anchor"


def test_missing_markers() -> None:
    module = _module()
    text = _text()
    assert module.missing_markers(text) == []
    marker = REQUIRED_MARKERS[0]
    assert module.missing_markers(text.replace(marker, "")) == [marker]


# ---- DB -----------------------------------------------------------------------------


def test_publish_inserts_next_version_and_moves_no_label() -> None:
    module = _module()
    with blank_session() as db:
        prev = AIPromptVersion(name=PROMPT_NAME, version=7, type="text", template="live before", variables=[])
        db.add(prev)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=prev.id))
        db.commit()
        assert module.publish(db) == 8
        rows = sorted(_versions(db), key=lambda r: r.version)
        assert [r.version for r in rows] == [7, 8]
        new = rows[1]
        assert new.template.startswith(_text())
        assert BLOCKS_BEGIN in new.template and BLOCKS_END in new.template
        assert new.commit_message == module.MESSAGE
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert [(l.label, l.version_id) for l in labels] == [("production", prev.id)]


def test_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_publish_with_a_missing_marker_publishes_nothing(tmp_path, monkeypatch) -> None:
    module = _module()
    broken = tmp_path / "candidate.txt"
    broken.write_text(_text().replace(REQUIRED_MARKERS[0], ""), encoding="utf-8")
    monkeypatch.setattr(module, "CANDIDATE", broken)
    with blank_session() as db:
        assert module.publish(db) is None
        assert _versions(db) == []

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
pytestmark = pytest.mark.parser_prompt_rows
