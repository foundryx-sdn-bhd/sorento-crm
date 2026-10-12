"""Publish the GROUPED OUTPUT build of one parser version as a new UNLABELLED
`chatbot_semantic_parser` version (CHAT-SPARSE, `chatbot_parser_prompt.grouped_prompt`).

Migration `chs_0001_parser_sparse_output` builds the production line and the code-constant
line once, at upgrade. This is the same publish for a version that appears later, e.g. the
one SPEC-PROMO-PARAMS' `publish_parser_specs_block` prints: the grouped edit is a pure
function of the text, so it stacks on any line. Idempotent. Never moves a label: promote
the printed version on the Prompts page.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_parser_grouped --from-version 87
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "chs_0001_parser_sparse_output.py"


def _migration():
    spec = importlib.util.spec_from_file_location("_chs_0001_publish", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-version", type=int, required=True)
    from_version = parser.parse_args().from_version

    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptVersion

    module = _migration()
    session = SessionLocal()
    try:
        source = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == module.PROMPT_NAME, AIPromptVersion.version == from_version)
            .one()
        )
        version = module._publish(
            session,
            source=source.template,
            variables=source.variables or ["current_date"],
            line=f"v{from_version}",
            base=f"v{from_version}",
        )
        session.commit()
    finally:
        session.close()
    if version is None:
        print("nothing published: that text is already grouped, has no OUTPUT block, or its grouped build exists")
    else:
        print(f"published chatbot_semantic_parser v{version} (unlabelled); promote it on the Prompts page")


if __name__ == "__main__":
    main()
