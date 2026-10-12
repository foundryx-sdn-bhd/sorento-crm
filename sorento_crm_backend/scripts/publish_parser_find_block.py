"""Publish a parser version carrying the live `{{find_filters}}` block (CHAT-FIND), as a new
UNLABELLED `chatbot_semantic_parser` version (`chatbot_find_prompt.publish_find_block`).

The source text is copied word for word; the variable goes in before the policy end marker
(or a marker pair holding it is appended). Idempotent. Never moves a label: promote the
printed version on the Prompts page. Run once; filters registered later need no new version.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_parser_find_block --from-version 52

Without `--from-version` the version labelled production is the source.
"""
from __future__ import annotations


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-version", type=int, default=None)
    from_version = parser.parse_args().from_version

    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal
    from app.services.chatbot_find_prompt import publish_find_block

    session = SessionLocal()
    try:
        version = publish_find_block(session, from_version=from_version)
        session.commit()
    finally:
        session.close()
    if version is None:
        print("that text already carries {{find_filters}} (or that version exists, or the source is missing); nothing done")
    else:
        print(f"published chatbot_semantic_parser v{version} (unlabelled); promote it on the Prompts page")


if __name__ == "__main__":
    main()
