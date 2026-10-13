"""CA-3: the chat report prompt migration appends its block at the END of a production text
that has no MEMORY section, never splicing a line."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from tests._pg_fixture import blank_session

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "ca3_0001_chat_report_prompt.py"


def test_a_text_without_a_memory_section_gets_the_block_appended() -> None:
    spec = importlib.util.spec_from_file_location("_ca3_prompt_append", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = "Line one of the prompt.\nThe last line ends here.\n\n"
    with blank_session() as session:
        row = AIPromptVersion(name=module.PROMPT_NAME, version=1, type="text", template=base, variables=[])
        session.add(row)
        session.flush()
        session.add(AIPromptLabel(name=module.PROMPT_NAME, label="production", version_id=row.id))
        session.commit()
        version = module.publish(session)
        new = session.query(AIPromptVersion).filter_by(name=module.PROMPT_NAME, version=version).one()
        block = module._addendum().rstrip("\n")
        assert new.template == base.rstrip("\n") + "\n\n" + block
