#!/usr/bin/env python3
"""Publish the parser v2 prompt (head + INDEX) on THIS database (CONTRACT-PV2-13oct.md s.2).

Writes one immutable `ai_prompt_versions` row for `chatbot_semantic_parser_v2` and points the
`staging` label at it (never `production`). The index is rendered from this database's live
rows, so run it against the eval/dev copy the evals run on.

Dry-run is the DEFAULT: prints the index, its sha, sizes and any open alias clash; nothing
is written without `--apply`.

Run from sorento_crm_backend/:
    venv/bin/python scripts/pv2_publish.py                 # dry run
    venv/bin/python scripts/pv2_publish.py --apply         # publish the file head
    venv/bin/python scripts/pv2_publish.py --apply --head path/to/head.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--head", help="head text file (default: app/services/chatbot/pv2/prompt_v2.txt)")
    ap.add_argument("--message", default=None, help="commit message for the version row")
    args = ap.parse_args()

    from app.database import SessionLocal
    from app.services.chatbot.pv2 import index as pv2_index

    head_path = Path(args.head) if args.head else (
        Path(__file__).resolve().parent.parent / "app" / "services" / "chatbot" / "pv2" / "prompt_v2.txt"
    )
    head = head_path.read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        body, sha = pv2_index.render_index(db)
        clashes = pv2_index.alias_clashes(db)
        print(body)
        print(f"index sha1 {sha} | index {len(body)} chars (cap {pv2_index.INDEX_CAP_CHARS}) | "
              f"head {len(head)} chars (cap {pv2_index.HEAD_CAP_CHARS})")
        for kind, alias, keys in clashes:
            print(f"ALIAS CLASH {kind} '{alias}': {', '.join(keys)}")
        if not args.apply:
            print("dry run: nothing written (pass --apply)")
            return 0
        version_id = pv2_index.publish_v2(db, head, commit_message=args.message)
        db.commit()
        print(f"published version_id {version_id} (label {pv2_index.PUBLISH_LABEL})")
        return 0
    except pv2_index.PublishRefused as exc:
        db.rollback()
        print(f"REFUSED: {exc}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
