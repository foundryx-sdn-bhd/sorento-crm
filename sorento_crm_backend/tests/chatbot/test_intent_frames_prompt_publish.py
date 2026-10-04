"""INTENT-FRAMES `if_0001_parser_frames` after PROMPT-DYNAMIC (#1405): once the owner's
wording layer exists, the SAME SUBJECT OR A NEW ONE block goes into the wording-layer text
(owner edits and registry variables kept), never into a version rebuilt from the code
constant, which `test_prompt_dynamic_drift` refuses."""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import app.main  # noqa: F401  isort:skip
from app.models.ai_prompt import AIPromptVersion
from app.services import chatbot_prompt_vars as pv
from app.services.chatbot_parser_prompt import BLOCKS_BEGIN
from tests._pg_fixture import pg_session

KEY = "chatbot_semantic_parser"
VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _migration():
    spec = importlib.util.spec_from_file_location("_t_if_0001", VERSIONS / "if_0001_parser_frames.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_block_goes_in_once_before_the_policy_blocks():
    mod = _migration()
    text = f"HEAD {{{{domains}}}}\n\n{BLOCKS_BEGIN}\nrows\n"
    out = mod.with_frames_block(text)
    head, rest = out.split(BLOCKS_BEGIN, 1)
    assert mod.BLOCK_HEADING in head and rest == "\nrows\n"
    assert out.startswith("HEAD {{domains}}\n\n")
    assert mod.with_frames_block(out) == out
    assert mod.BLOCK_HEADING in mod.with_frames_block("no blocks here")


def test_a_wording_layer_version_gets_the_block_with_its_variables_kept():
    mod = _migration()
    with pg_session() as db:
        top = db.query(AIPromptVersion.version).filter(AIPromptVersion.name == KEY).order_by(
            AIPromptVersion.version.desc()
        ).first()
        base = AIPromptVersion(
            id=str(uuid.uuid4()), name=KEY, version=int(top[0] if top else 0) + 1, type="text",
            template=f"ZZT owner wording {{{{domains}}}} {{{{statuses}}}}\n\n{BLOCKS_BEGIN}\n",
            variables=["current_date"], commit_message="zzt", config_json={},
        )
        db.add(base)
        db.flush()
        version = mod.publish(db)
        assert version == base.version + 1
        row = db.query(AIPromptVersion).filter(AIPromptVersion.name == KEY, AIPromptVersion.version == version).one()
        assert row.template == mod.with_frames_block(base.template)
        assert row.template.count(mod.BLOCK_HEADING) == 1
        assert pv.is_wording_layer(row.template) and pv.literal_lists(row.template) == []
        assert row.variables == ["current_date"]
        assert (row.config_json or {}).get(mod.MARKER_KEY) and row.config_json["from_version"] == base.version
        assert mod.publish(db) is None, "the newest wording layer already carries the block"
