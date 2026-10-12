"""Publish a parser version carrying the live `{{specs}}` block (the `Specification size`
line included) and the continuation rule, as a new UNLABELLED `chatbot_semantic_parser` version
(SPEC-PROMO-PARAMS, owner hand test 12 Oct 2026; `chatbot_prompt_vars.publish_specs_block`).

The source text is copied word for word: a static `Specification ...` block between the policy
markers (prod v52) becomes `{{specs}}`; a text with no markers (dev v52, v86) gets a marker pair
holding it; a text already carrying `{{specs}}` keeps it. Idempotent. Never moves a label:
promote the printed version on the Prompts page.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_parser_specs_block --from-version 52

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
    from app.services.chatbot_prompt_vars import publish_specs_block

    session = SessionLocal()
    try:
        version = publish_specs_block(session, from_version=from_version)
        session.commit()
    finally:
        session.close()
    if version is None:
        print("that text already carries {{specs}} and the continuation rule (or that version exists); nothing done")
    else:
        print(f"published chatbot_semantic_parser v{version} (unlabelled); promote it on the Prompts page")


if __name__ == "__main__":
    main()
