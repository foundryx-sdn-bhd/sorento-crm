"""REF-PROBES: the reference kinds section rebuilt on the PROMPT-ABLATION parser text (v56).

`rkind_0001_parser_ref_kinds` publishes the code constant (v50 lineage) plus REF_KINDS. The
graded candidate for promotion is the ablation text (`pab_0001_parser_ablation`), so this lane
also publishes that text with the two sections it lacks, CHAT_REPORT_ADDENDUM and
REF_KINDS_ADDENDUM, placed beneath PO_REFS_ADDENDUM in the code's order. Label unmoved.

Red until `alembic/versions/rkind_0002_parser_ref_kinds_v56.py` and its data file exist.
Postgres only (tests._pg_fixture).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.chatbot_parser_prompt import (
    BLOCKS_BEGIN,
    CHAT_REPORT_ADDENDUM,
    PO_REFS_ADDENDUM,
    REF_KINDS_ADDENDUM,
)
from tests._pg_fixture import blank_session

BACKEND = Path(__file__).resolve().parents[2]
DATA = BACKEND / "alembic" / "data"
ABLATION_FILE = DATA / "chatbot_semantic_parser.ablation-20261011.txt"
REF_KINDS_FILE = DATA / "chatbot_semantic_parser.refkinds-v56-20261011.txt"
MIGRATION = BACKEND / "alembic" / "versions" / "rkind_0002_parser_ref_kinds_v56.py"
PROMPT_NAME = "chatbot_semantic_parser"

#: The entity format line's hint values as the ablation text has them. The rebuild extends this
#: line itself with the five reference kinds (crew decision 12 Oct 2026: the REF_KINDS addendum
#: alone left the model emitting "order" for a kind-worded number).
ABLATION_HINTS = (
    '"hint": "product|promotion|customer|transporter|inbound_shipment|warehouse|attachment|form'
    '|order|category|brand|attachment_type"'
)
#: Crew 12 Oct 2026 (reviewer ruling: the parser decides, no code-side prefix matching): the
#: PO REFS section of the rebuild states what a po_ref is NOT, right after its own last line.
PO_REFS_RULE = (
    "po_refs is ONLY the customer's own PO reference. A number you put in entities as an order or "
    "document number (a delivery order, sales order or invoice number, e.g. \"PS202609-1111\" or "
    "\"2609-0042\") is never a po_ref, with or without its letters.\n"
)
REF_KINDS = ("delivery_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request")


def _text() -> str:
    return REF_KINDS_FILE.read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("zzt_rkind_0002_parser_ref_kinds_v56", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions(db) -> list[AIPromptVersion]:
    return db.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME).all()


def _entity_format_line(text: str) -> str:
    (line,) = [l for l in text.splitlines() if l.startswith("Each entity: {")]
    return line


def test_data_file_is_the_ablation_text_plus_the_two_sections_and_the_hint_line() -> None:
    ablation = ABLATION_FILE.read_text(encoding="utf-8")
    assert ablation.count(PO_REFS_ADDENDUM) == 1
    assert ablation.count(ABLATION_HINTS) == 1
    expected = ablation.replace(
        PO_REFS_ADDENDUM, PO_REFS_ADDENDUM + PO_REFS_RULE + CHAT_REPORT_ADDENDUM + REF_KINDS_ADDENDUM
    )
    text = _text()
    old_line = _entity_format_line(expected)
    new_line = _entity_format_line(text)
    # Only the entity format line differs, and only inside its hint value.
    assert text.replace(new_line, old_line) == expected
    head, _, tail = old_line.partition(ABLATION_HINTS)
    assert new_line.startswith(head) and new_line.endswith(tail)


def test_entity_format_line_lists_each_reference_kind_with_an_example() -> None:
    line = _entity_format_line(_text())
    for kind in REF_KINDS:
        assert f"|{kind}" in line, kind
        assert f"-> {kind}" in line, f"no example for {kind}"
    assert ABLATION_HINTS not in line


def test_data_file_carries_each_section_once_and_no_policy_blocks() -> None:
    text = _text()
    assert text.count(REF_KINDS_ADDENDUM) == 1
    assert text.count(CHAT_REPORT_ADDENDUM) == 1
    assert BLOCKS_BEGIN not in text
    assert text.index(PO_REFS_ADDENDUM) < text.index(CHAT_REPORT_ADDENDUM) < text.index(REF_KINDS_ADDENDUM)
    assert "{{current_date}}" in text


def test_revision_ids_chain_onto_rkind_0001() -> None:
    module = _module()
    assert module.revision == "rkind_0002_parser_ref_kinds_v56"
    assert module.down_revision == "rkind_0001_parser_ref_kinds"
    assert len(module.revision) <= 32


def test_publish_on_empty_table_is_verbatim() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) == 1
        (row,) = _versions(db)
        assert row.template == _text()
        assert row.variables == ["current_date"]
        assert row.commit_message == module.MESSAGE


def test_publish_is_the_next_version_and_moves_no_label() -> None:
    module = _module()
    with blank_session() as db:
        prev = AIPromptVersion(name=PROMPT_NAME, version=56, type="text", template="v56", variables=[])
        db.add(prev)
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=prev.id))
        db.commit()
        assert module.publish(db) == 57
        labels = db.query(AIPromptLabel).filter(AIPromptLabel.name == PROMPT_NAME).all()
        assert [(l.label, l.version_id) for l in labels] == [("production", prev.id)]


def test_publish_twice_is_idempotent() -> None:
    module = _module()
    with blank_session() as db:
        assert module.publish(db) is not None
        assert module.publish(db) is None
        assert len(_versions(db)) == 1


def test_downgrade_deletes_only_its_unlabelled_rows(monkeypatch) -> None:
    module = _module()
    with blank_session() as db:
        unlabelled = AIPromptVersion(
            name=PROMPT_NAME, version=1, type="text", template="a", variables=[], commit_message=module.MESSAGE
        )
        labelled = AIPromptVersion(
            name=PROMPT_NAME, version=2, type="text", template="b", variables=[], commit_message=module.MESSAGE
        )
        other = AIPromptVersion(
            name=PROMPT_NAME, version=3, type="text", template="c", variables=[], commit_message="other"
        )
        db.add_all([unlabelled, labelled, other])
        db.flush()
        db.add(AIPromptLabel(name=PROMPT_NAME, label="production", version_id=labelled.id))
        db.commit()
        monkeypatch.setattr(module, "op", SimpleNamespace(get_bind=lambda: db.connection()))
        module.downgrade()
        db.commit()
        db.expire_all()
        assert sorted(r.version for r in _versions(db)) == [2, 3]


def test_po_refs_section_says_an_order_entity_number_is_never_a_po_ref() -> None:
    text = _text()
    start = text.index("PO REFS\n")
    section = text[start : text.index("== CHAT REPORT", start)]
    assert PO_REFS_RULE in section
    assert text.count(PO_REFS_RULE) == 1

# Writes the parser's prompt rows: one such test at a time across xdist workers
# (tests/conftest.py `_parser_prompt_rows_lock`).
import pytest  # noqa: E402

pytestmark = pytest.mark.parser_prompt_rows
