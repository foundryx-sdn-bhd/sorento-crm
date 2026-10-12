"""Publish the production parser text with the `{{specs}}` block as a new, UNLABELLED
`chatbot_semantic_parser` version (SPEC-PROMO-PARAMS, owner hand test 12 Oct 2026).

The owner's v84-v86 texts carry no `{{specs}}`, so the specification vocabulary (the
`Specification size` line included) never reaches the parser on that line. This copies the
production text word for word and adds the block (`chatbot_prompt_vars.publish_specs_block`).
Idempotent. Never moves a label: promote the printed version on the Prompts page.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_parser_specs_block
"""
from __future__ import annotations


def main() -> None:
    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal
    from app.services.chatbot_prompt_vars import publish_specs_block

    session = SessionLocal()
    try:
        version = publish_specs_block(session)
        session.commit()
    finally:
        session.close()
    if version is None:
        print("production text already carries {{specs}} (or that version exists); nothing done")
    else:
        print(f"published chatbot_semantic_parser v{version} (unlabelled); promote it on the Prompts page")


if __name__ == "__main__":
    main()
